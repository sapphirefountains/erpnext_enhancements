"""The framed Triton widget: who gets it, and the one-time sign-in ticket (v1.802.0).

The classic widget (``public/js/global_enhancements/triton_widget.js`` plus the relay in
``triton_chat.py``) is a second chat program that has to be taught every Triton feature by hand.
The framed widget replaces its insides with one ``<iframe>`` showing a compact chat page that
Triton serves itself (``/embed.html``), so the Desk keeps only the button, the panel and a small
``postMessage`` bridge (``public/js/triton_frame/``). This module is the server half of that:

1. **Which widget does this person get?** :func:`frame_config` answers it from *Triton Assistant
   Settings* and :func:`boot_payload` ships the answer in ``frappe.boot.triton_frame``. The key is
   present only for a person on the framed widget. Nobody else's boot grows by a byte, and the
   classic widget carries on exactly as before. The browser never reads the settings itself: that
   Single is a System Manager doctype, and a read from Desk JavaScript shows everyone else a
   "No permission" dialog.
2. **Signing the frame in.** The frame is a page on Triton's own address, so it needs a Triton
   token, and it must not get one by a Google step inside a frame or by sharing the site's
   long-lived token. :func:`get_ticket` asks Triton, server to server, for a ticket that works
   once and for about a minute. The Desk page hands the ticket to the frame; the frame trades it
   for the usual 30-minute token and keeps that in memory.

What this module is careful about
---------------------------------

* **Classic is the answer whenever anything is unclear.** A missing mode (the field is new, and a
  ``default`` on a new field of a Single never reaches the row that already exists), a value this
  code does not know, a blank or odd Triton address, a missing shared secret, a person outside the
  allow-list: all of them read as "classic". The framed widget is opt-in, so every failure to
  decide must land on the widget that works today.
* **The master switch and the allow-list are not re-implemented.** Both come from
  ``triton_chat.get_settings`` and ``triton_chat.user_has_widget_access``, the same two functions
  the classic widget's ``get_config`` uses. A person who cannot see the classic button cannot get
  a ticket.
* **The shared secret is read in one place.** ``triton_chat.get_settings`` is the only code that
  takes the Admin Webhook Secret out of *Triton Settings*. This module uses the value it returns
  and never reads the Password field itself.
* **Neither the secret nor a ticket is ever logged.** A failed call logs the HTTP status and
  nothing else, and only when *Debug Logging* is on. Triton's reply body is never written
  anywhere: on a success it *is* the ticket. Nothing is raised from inside the ``except`` around
  the HTTP call either, because an exception that escapes with the request's frames attached is
  how a header ends up in the Error Log (``frappe.log_error`` with no message renders frame
  locals).
* **An expected failure is an answer, not an exception.** "Triton could not be reached" comes back
  as ``{"ok": False, "reason": ..., "message": ...}``. The loader passes the sentence to the frame
  (the bridge's ``ticket-error``) and the frame shows it once, as written. A ``frappe.throw``
  would put a red Desk dialog in front of a person who only pressed a button, and would repeat on
  every retry.
* **The frame's address is checked before it is trusted.** The loader posts sign-in tickets to
  this origin and to nothing else, so it must be ``https`` (plain ``http`` only for a local
  development host) and carry no user name, path or query.

The Triton route and payload live in :func:`_request_ticket` alone. They are Triton's contract
(``docs/embed-bridge.md`` in the Triton repository, version 1); if it changes, that function is
the only one that does. ``docs/triton-framed-widget.md`` is this app's account of the whole
arrangement.
"""

from urllib.parse import urlsplit

import frappe
import requests
from frappe import _

from erpnext_enhancements import triton_chat

SETTINGS_DOCTYPE = "Triton Assistant Settings"

# The stored values of Triton Assistant Settings.widget_mode. A Select value is data: renaming
# one freezes every row that holds the old spelling (`_validate_selects` refuses the save), so
# these three strings are fixed. Change a label by adding a patch, not by editing them.
MODE_CLASSIC = "Classic"
MODE_PILOT = "Framed for pilot users"
MODE_EVERYONE = "Framed for everyone"

# The one Triton page that may be framed, and only by this site (Triton's nginx names the ERP
# address in that page's `frame-ancestors`; every other Triton page refuses all framing).
EMBED_PATH = "/embed.html"

# Hosts where plain http is acceptable: a developer's own machine, nothing else.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

# A person's browser asks for a ticket when the frame loads and again each time its 30-minute
# token runs out. Twenty a minute is far above that and far below what a frame stuck in a
# "sign me in again" loop would send.
TICKET_LIMIT = 20
TICKET_WINDOW_SEC = 60
# The frame waits 15 seconds for a ticket and then tells the person "the ERPNext page did not
# answer". This must give up well inside that, so the sentence the person reads is ours
# ("Triton could not be reached") and not the frame's guess.
_TICKET_TIMEOUT_SEC = 8
# Longer than any ticket Triton issues, shorter than anything else it could send by mistake.
_TICKET_MAX_LENGTH = 512


# ---------------------------------------------------------------------------
# Which widget
# ---------------------------------------------------------------------------
def frame_origin(site_url, gateway_url):
	"""The origin the frame is loaded from and the only one the bridge talks to, or ``None``.

	*Framed Site Address* wins when set; otherwise it is the origin of the Gateway URL in Triton
	Settings, which on production is the Triton site itself (its nginx serves the pages and the
	API from one address). ``None`` means "no usable address", and the caller answers classic.
	"""
	raw = (site_url or "").strip() or (gateway_url or "").strip()
	if not raw:
		return None
	try:
		parts = urlsplit(raw)
		host = (parts.hostname or "").lower()
		port = parts.port
	except ValueError:
		return None
	if not host or parts.username or parts.password:
		return None
	scheme = (parts.scheme or "").lower()
	local = host in _LOCAL_HOSTS or host.endswith(".localhost")
	if scheme != "https" and not (scheme == "http" and local):
		return None
	if ":" in host:  # an IPv6 literal goes back in its brackets
		host = f"[{host}]"
	return f"{scheme}://{host}" + (f":{port}" if port else "")


def _pilot_users(behavior):
	"""The User names in the pilot table, as a set."""
	return {
		(row.get("user") or "").strip()
		for row in (behavior.get("framed_pilot_users") or [])
		if (row.get("user") or "").strip()
	}


def frame_config():
	"""The framed widget's settings for the signed-in person, or ``None`` for the classic widget.

	``None`` is by far the common answer and the safe one: see the module docstring. Raises
	whatever the settings read raises; :func:`boot_payload` is the caller that must not.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		return None

	settings = triton_chat.get_settings()
	if not settings.get("enabled") or not triton_chat.user_has_widget_access(settings):
		return None

	behavior = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	mode = (behavior.get("widget_mode") or "").strip()
	if mode == MODE_PILOT:
		if user not in _pilot_users(behavior):
			return None
	elif mode != MODE_EVERYONE:
		return None

	# No secret, no ticket; and a frame that cannot sign in is a dead panel.
	if not settings.get("gateway_secret"):
		return None
	origin = frame_origin(behavior.get("framed_site_url"), settings.get("base_url"))
	if not origin:
		return None

	return {
		"origin": origin,
		"url": origin + EMBED_PATH,
		"page_context": 1 if settings.get("enable_page_context") else 0,
	}


def boot_payload():
	"""``frappe.boot.triton_frame`` for this person, or ``None``. Never raises: boot runs on every
	Desk page, and a settings problem here must leave the classic widget in charge, not break the
	Desk."""
	try:
		return frame_config()
	except Exception:
		return None


# ---------------------------------------------------------------------------
# The sign-in ticket
# ---------------------------------------------------------------------------
def _failure(reason, message):
	return {"ok": False, "reason": reason, "message": message}


def _over_the_limit(user):
	"""Count this request against the person's minute, and say whether it is one too many."""
	cache = frappe.cache()
	cache_key = f"triton_frame_tickets::{user}"
	try:
		count = int(cache.get_value(cache_key) or 0)
	except (TypeError, ValueError):
		count = 0
	if count >= TICKET_LIMIT:
		return True
	cache.set_value(cache_key, count + 1, expires_in_sec=TICKET_WINDOW_SEC)
	return False


def _request_ticket(base_url, secret, email, full_name):
	"""Ask Triton for a ticket. Returns ``(status, body)``; ``status`` is 0 when Triton was not reached.

	THE ONE PLACE that knows Triton's route and payload. It is the existing identity bridge's
	pattern (``triton_chat.mint_user_token``): the person's email and name in the body, the shared
	secret as a Bearer header, server to server. Triton does every check that matters (secret,
	company domain, disabled account, first-time provisioning) on its side.

	Never raises and never logs. The caller decides what a status means.
	"""
	try:
		resp = requests.post(
			f"{base_url}/api/v1/auth/erpnext-bridge/embed-ticket",
			json={"email": email, "full_name": full_name},
			headers={"Authorization": f"Bearer {secret}"},
			timeout=_TICKET_TIMEOUT_SEC,
		)
	except Exception:
		return 0, None
	if resp.status_code != 200:
		return resp.status_code, None
	try:
		return 200, resp.json()
	except Exception:
		return 200, None


@frappe.whitelist(methods=["POST"])
def get_ticket():
	"""A one-time Triton sign-in ticket for the signed-in person, for the framed widget.

	Returns ``{"ok": True, "ticket": ..., "expires_in": seconds}``, or
	``{"ok": False, "reason": ..., "message": ...}`` where ``reason`` is one of:

	* ``unreachable``: Triton did not answer, answered 5xx, or answered with something that is not
	  a ticket. Worth trying again. A 503 is here on purpose: Triton's contract gives it one
	  meaning ("no gateway secret configured") and Triton itself answers 503 for a second ("database
	  temporarily unavailable", seen against a local Triton on 2026-11-12). The two cannot be told
	  apart without reading the reply's body, which this module never does, and "try again in a
	  minute" is wrong for neither for long: with *Debug Logging* on, the status is in the Error Log.
	* ``unsupported``: Triton has no ticket route (404, 405 or 501). The loader falls back to the
	  classic widget, which does not need one.
	* ``setup``: Triton answered 401: the shared secret is wrong. An administrator's to fix;
	  trying again will not help.
	* ``refused``: Triton answered 400 or 403. It will not sign this person in: the email is not
	  one it accepts, or the person's Triton account is disabled.
	* ``busy``: more than :data:`TICKET_LIMIT` requests in a minute from this person.

	``message`` is one plain sentence of at most 300 characters. The loader passes it to the
	frame as the bridge's ``ticket-error`` and the frame shows it to the person as written, so it
	never carries a status code, an address or anything from Triton's reply.

	Raises ``PermissionError`` for a Guest and for anyone who is not on the framed widget: the
	master switch, the allow-list, the mode and the pilot list all sit behind
	:func:`frame_config`, so a person who cannot see the framed widget cannot ask for a ticket by
	calling this directly. The ticket is returned to the browser and nowhere else; it is not
	cached and not logged.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Sign in to use Triton."), frappe.PermissionError)
	if not frame_config():
		frappe.throw(_("The framed Triton assistant is not switched on for you."), frappe.PermissionError)

	if _over_the_limit(user):
		return _failure(
			"busy", _("Triton was asked to sign you in too many times. Wait a minute, then try again.")
		)

	settings = triton_chat.get_settings()
	# `secret` and `token` are two of the few names Frappe's traceback sanitiser redacts
	# (`password`, `passwd`, `secret`, `token`, `key`, `pwd`), so both locals carry one.
	secret = settings.get("gateway_secret")
	status, body = _request_ticket(
		settings.get("base_url"), secret, triton_chat._user_email(), frappe.utils.get_fullname(user)
	)

	token = body.get("ticket") if isinstance(body, dict) else None
	if status == 200 and isinstance(token, str) and 0 < len(token) <= _TICKET_MAX_LENGTH:
		try:
			expires_in = int(body.get("expires_in") or 60)
		except (TypeError, ValueError):
			expires_in = 60
		return {"ok": True, "ticket": token, "expires_in": expires_in}

	if settings.get("debug"):
		# The status and nothing else. Never the body: on a 200 it is the ticket.
		frappe.log_error(title="Triton Frame", message=f"Ticket request failed: HTTP {status or 'no answer'}")

	if status in (404, 405, 501):
		return _failure("unsupported", _("This Triton does not offer the framed assistant yet."))
	if status == 401:
		return _failure("setup", _("Triton is not set up correctly. Tell an administrator."))
	if status in (400, 403):
		return _failure(
			"refused",
			_(
				"Triton does not accept your ERPNext account. Ask an administrator to check your Triton account."
			),
		)
	return _failure("unreachable", _("Triton could not be reached. Try again in a minute."))

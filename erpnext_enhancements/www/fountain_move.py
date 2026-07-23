# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Web-page controller for the public fountain-move intake form at ``/fountain-move``.

**The filename is ``fountain_move.py`` while the template is ``fountain-move.html``,
and that is required, not a slip.** Frappe locates a page controller by taking the
template's basename and replacing hyphens with underscores
(``website/page_renderers/template_page.py:136-140``), so a controller named
``fountain-move.py`` would never be imported and ``get_context`` would never run.
The page would still render — silently, with none of the context below. That is
exactly what has happened to ``www/stripe-return.py``, which has never executed.

Intentionally public: the whole point is that a Cactus & Tropicals customer who
has no account with us can fill this in. Unlike every other page in ``www/``,
there is no guest-to-login redirect.

Config reaches the page through ``window.FM_BOOT``, built here as an explicit
allowlist. There is deliberately **no** config endpoint: a bare GET returning the
Maps key would hand it to any scraper without so much as loading the form.
"""

import frappe

from erpnext_enhancements.crm_enhancements.fountain_move import (
	HONEYPOT_FIELD_NAME,
	get_contact_phone,
	get_store_locations,
	max_preferred_date,
	min_preferred_date,
)
from erpnext_enhancements.crm_enhancements.fountain_move.invites import resolve_invite
from erpnext_enhancements.feature_flags import fountain_move_public_form_enabled
from erpnext_enhancements.utils.deploy import get_deploy_version

no_cache = 1


def get_context(context):
	# Per-visitor render (invite prefill, live settings) — never cache it.
	context.no_cache = 1

	if not fountain_move_public_form_enabled():
		# 404 rather than an explanation. A switched-off feature is not an
		# invitation to probe, and an anonymous visitor gains nothing from
		# knowing the route exists.
		raise frappe.DoesNotExistError

	context.deploy_version = get_deploy_version()
	context.title = "Request a Fountain Move"

	# Rendered straight into the date inputs' min/max attributes. The page is
	# no_cache, so these are fresh per visit; a tab left open across midnight
	# drifts a day, and the server re-validates at submit anyway.
	context.min_preferred_date = min_preferred_date().isoformat()
	context.max_preferred_date = max_preferred_date().isoformat()

	invite = resolve_invite(frappe.form_dict.get("ref"))

	# CSRF: emit the session's EXISTING token, or "" when there isn't one.
	#
	# For an anonymous customer this is empty and frappe's validate_csrf_token
	# short-circuits (auth.py:86 — `not (saved_token := session.data.csrf_token)`),
	# so nothing is validated and nothing needs sending. Abuse control on that
	# path is Turnstile + honeypot + rate limiting, in fountain_move/intake.py.
	#
	# But this page is equally reachable by LOGGED-IN staff previewing it, and
	# their session DOES carry a token — so validate_csrf_token does not
	# short-circuit, and a POST without the header throws CSRFTokenError, which
	# is HTTP 400. That broke the form outright for anyone signed in. Same-origin
	# does not save you: is_allowed_referrer only passes hosts explicitly listed
	# in site config's `allowed_referrers`, which is empty by default.
	#
	# Deliberately NOT frappe.sessions.get_csrf_token(): that MINTS a token when
	# one is absent, and Session.update() never persists it for Guest — so a
	# guest would send a token the server has never heard of.
	context.csrf_token = (getattr(frappe.session, "data", None) or {}).get("csrf_token") or ""

	context.boot = {
		"csrf_token": context.csrf_token,
		"turnstile_sitekey": _setting("fountain_move_turnstile_site_key"),
		"maps_api_key": _setting("fountain_move_maps_api_key"),
		"max_photo_mb": _max_photo_mb(),
		"locations": get_store_locations(),
		"honeypot_field": HONEYPOT_FIELD_NAME,
		"contact_phone": get_contact_phone(),
		"terms_url": "/terms-of-use",
		"privacy_url": "/privacy-policy",
		# Attribution only. The token grants nothing, and pre-fills nothing but
		# the store — a forwarded invite must not leak the original recipient's
		# name, email or phone to whoever received it.
		"invite_token": invite.get("token") if invite else None,
		"prefill_location": invite.get("ct_location") if invite else None,
	}

	if invite:
		_track_open(invite["name"])

	return context


def _setting(fieldname):
	try:
		return frappe.db.get_single_value("ERPNext Enhancements Settings", fieldname) or ""
	except Exception:
		return ""


def _max_photo_mb():
	"""The effective per-photo cap, so the client can reject early with a real number."""
	from frappe.core.api.file import get_max_file_size

	from erpnext_enhancements.crm_enhancements.fountain_move.intake import PHOTO_BYTE_CAP

	try:
		configured = frappe.utils.cint(_setting("fountain_move_max_photo_mb"))
		ours = (configured * 1024 * 1024) if configured > 0 else PHOTO_BYTE_CAP
		return max(1, min(ours, get_max_file_size()) // (1024 * 1024))
	except Exception:
		return PHOTO_BYTE_CAP // (1024 * 1024)


def _track_open(invite_name):
	"""Record that the invite was opened — from a background job, not inline.

	A GET is not an unsafe HTTP method, so frappe does not commit the request's
	transaction (``app.py``: it commits only for unsafe methods or an explicit
	flag). Writing here directly would be rolled back on the way out and the
	invite would sit at "Sent" forever, making the send flow look broken.
	"""
	try:
		frappe.enqueue(
			"erpnext_enhancements.crm_enhancements.fountain_move.invites.mark_invite_opened",
			enqueue_after_commit=False,
			invite_name=invite_name,
		)
	except Exception:
		# Tracking is a nicety; it must never take the form down.
		frappe.log_error(frappe.get_traceback(), "Fountain Move: invite open tracking", defer_insert=True)

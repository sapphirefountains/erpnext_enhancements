# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give the chat bot a working ERPNext credential **inside Triton**, without a browser.

--------------------------------------------------------------------------------------
The problem this exists for
--------------------------------------------------------------------------------------

``TritonIntelligence.__init__`` constructs its ``FrappeClient`` for whichever identity the
turn runs as, eagerly, before the model is called at all. If that identity has no ERPNext
credential stored in Triton, the constructor raises and the whole turn comes back as
**HTTP 401 ``erpnext_link_required``** — not because our token was rejected, but because
Triton had nothing to call *back* into ERPNext with. Triton refuses to fall back to its own
system key there, and it is right to: that would answer with service-account permissions
instead of the account's own.

For a human that is solved by clicking **Link ERPNext** once, which runs an OAuth flow in a
browser. ``triton@sapphirefountains.com`` cannot do that. It is a **Google group**, not a
mailbox anyone can sign into, so there is no browser session to complete the flow in and
never will be. The digest summariser runs as that account and therefore 401'd on every
single run, permanently, with no interactive path to a fix.

--------------------------------------------------------------------------------------
The route that does exist
--------------------------------------------------------------------------------------

Triton's ``FrappeClient`` accepts a **per-user API key/secret** as a documented fallback for
identities that never migrated to OAuth (``provider="erpnext"`` in its ``APIKey`` table), and
that path is honoured end to end: ``Authorization: token key:secret`` on every request, no
refresh attempted, no expiry to lapse. The credential is written by ``PUT
/api/v1/assistant/profile`` — which authenticates with a **bearer token**, and minting a
bearer token for the bot is machine-to-machine through the existing ERPNext bridge. No
browser, no password, no interactive session anywhere in the chain.

So this module is the last mile: read the bot's ERPNext API key, hand it to Triton as the
bot, and verify Triton kept it.

--------------------------------------------------------------------------------------
Three things it deliberately does not do
--------------------------------------------------------------------------------------

**It never generates the key.** ``frappe.core.doctype.user.user.generate_keys`` resets
``api_secret`` unconditionally and ``save()``\\ s the whole ``User``, so calling it silently
invalidates any credential already in use — including one this module wrote last week — and,
for a user that *does* carry a role profile, rebuilds its roles from the profile and drops
every directly-assigned one. Neither belongs in a function whose job is "make the existing
credential reachable". Generation stays a deliberate act in the Desk.

**It never returns or logs the secret.** Not in a response, not in an exception, not in a
debug field. The status functions answer *whether* a secret exists, which is the only part of
the answer anybody needs, and the one call that must transmit it puts it in a request body and
nowhere else.

**It does not narrow what the credential can do.** An API key carries the ERPNext user's own
permissions — the same breadth the OAuth grant would have carried, for the same account. Two
honest differences from OAuth, stated rather than discovered: an API key does not expire, and
it does not rotate. If the bot's role list is wider than a summariser needs, that is worth
narrowing on the **User**, where it applies to every path at once, rather than here.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

#: Triton's profile endpoint. ``PUT`` writes ``frappe_api_key`` / ``frappe_api_secret`` for
#: whoever the bearer belongs to; ``GET`` reports ``frappe_api_key_configured`` — a boolean,
#: never the credential — which is what makes the write verifiable from here.
PROFILE_PATH: str = "/api/v1/assistant/profile"

#: Roles that make an ERPNext API key too powerful to hand to a chat path.
#:
#: **This guard exists because the first version of this module would have caused a real
#: privilege escalation, and it looked like configuration.** On this deployment
#: ``Chat Settings.bot_user`` pointed at ``triton@sapphirefountains.com`` — which is *also*
#: Triton's shared ``FRAPPE_API_KEY`` system account, holds **System Manager**, and is actively
#: syncing. Credentialling "the bot" would therefore have taken a live System Manager API key
#: and stored it as a per-user credential on the path a chat message can reach. Nothing about
#: that step looks dangerous: you are copying a key that already exists to a service that
#: already has one.
#:
#: An API key does not expire and does not rotate, which is exactly why the account behind one
#: matters more than it would for an OAuth grant. Triton's own ADR 0004 says the shared key is
#: "the weak point — keep its use narrow"; widening it to the chat path is the opposite.
#:
#: There is deliberately **no override flag**. The fix is a separate account with no roles,
#: which takes a minute, and a boolean is one typo from being ``True``.
UNSAFE_BOT_ROLES: frozenset[str] = frozenset(
	{
		"System Manager",
		# Server Scripts are arbitrary code execution in the site's own process. This is not
		# hypothetical here: the 2026-08-02 intrusion planted miner Server Scripts.
		"Script Manager",
		"Administrator",
	}
)


@frappe.whitelist()
def bot_credential_status() -> dict[str, Any]:
	"""Whether the chat bot can reach ERPNext from inside Triton, and if not, which half is missing.

	Read-only and safe to call repeatedly. Answers from **both sides**, because either one
	alone is misleading: ERPNext can see that the key exists, Triton can see that it stored
	one, and the failure everybody actually hits is "generated here, never sent there".

	``triton_reports_key`` is ``None`` rather than ``False`` when Triton could not be reached —
	an unknown answer and a negative one call for different actions, and collapsing them is how
	a health check sends somebody to fix a credential when the gateway is simply down.
	"""
	frappe.only_for("System Manager")

	bot = _bot_user()
	api_key, has_secret = _erpnext_credential(bot)

	from erpnext_enhancements.chat.invoke.handler import has_erpnext_link

	remote, remote_error = remote_key_configured(bot)

	return {
		"bot_user": bot,
		"erpnext_api_key": bool(api_key),
		"erpnext_api_secret": has_secret,
		"erpnext_oauth_link": has_erpnext_link(bot),
		#: Reported even when everything else is green, because "it works" is exactly the state
		#: in which nobody looks at what the credential can do.
		"unsafe_roles": unsafe_roles(bot),
		"triton_reports_key": remote,
		"triton_error": remote_error,
		"ready": bool(api_key) and has_secret and remote is True,
	}


@frappe.whitelist()
def link_bot_credentials() -> dict[str, Any]:
	"""Push the bot's existing ERPNext API key/secret into Triton, as the bot. Idempotent.

	Idempotent in the way that matters: Triton's profile handler updates the row in place when
	one already exists, so running this twice writes the same credential twice and changes
	nothing. It is safe to re-run after a Triton deploy, after a key rotation, or simply
	because nobody is sure whether it was run.

	Returns the same structure as :func:`bot_credential_status`, re-read from Triton **after**
	the write rather than assumed from the fact that the write returned 200. A verified write
	and a write that did not raise are not the same claim, and this is the one place where
	getting it wrong is silent — the next failure would be a 401 on a digest at 3am.
	"""
	frappe.only_for("System Manager")

	bot = _bot_user()
	api_key, has_secret = _erpnext_credential(bot)
	if not api_key or not has_secret:
		# Named precisely, because "credentials missing" sent somebody to the wrong screen
		# twice already. The Desk path is: User list → the bot → API Access → Generate Keys.
		frappe.throw(
			_(
				"{0} has no ERPNext API key and secret yet. Open that User in the Desk, use "
				"API Access → Generate Keys, then run this again. Keys are deliberately not "
				"generated here: generating resets the secret and invalidates whatever is "
				"already using it."
			).format(bot)
		)

	unsafe = unsafe_roles(bot)
	if unsafe:
		frappe.throw(
			_(
				"Refusing to credential {0} for Triton: it holds {1}. An ERPNext API key carries "
				"that account's full permissions, does not expire and cannot be rotated by the "
				"holder — so this would put a privileged, non-expiring credential on the path a "
				"chat message can reach. Point Chat Settings → Bot User at a dedicated account "
				"with no roles, generate its API key, add it to Triton Assistant Settings → "
				"Allowed Users, and run this again."
			).format(bot, ", ".join(unsafe))
		)

	secret = _erpnext_api_secret(bot)
	_as_bot(
		bot,
		lambda: _request(
			"PUT",
			PROFILE_PATH,
			payload={"frappe_api_key": api_key, "frappe_api_secret": secret},
		),
	)

	return bot_credential_status()


# --------------------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------------------


def unsafe_roles(user: str) -> list[str]:
	"""Which of :data:`UNSAFE_BOT_ROLES` ``user`` holds. Sorted, so the message is stable.

	Reads ``frappe.get_roles`` rather than ``tabHas Role`` directly: role profiles and the
	implicit roles are part of the answer, and a check that missed them would pass an account
	that is a System Manager by another route.
	"""
	if not user:
		return []
	try:
		return sorted(set(frappe.get_roles(user)) & UNSAFE_BOT_ROLES)
	except Exception:
		# Cannot tell — treat as unsafe by naming the condition, never by returning "clean".
		return sorted(UNSAFE_BOT_ROLES)


def remote_key_configured(bot: str) -> tuple[bool | None, str]:
	"""``(stored, error)`` — what **Triton** holds for ``bot``. ``None`` means it could not be asked.

	Public and permission-free on purpose: the readiness report in ``chat/rollout.py`` needs
	this exact answer, and the alternative — calling the whitelisted
	:func:`bot_credential_status` — would drag a ``System Manager`` check into a report that is
	also run from a bench console, and would flatten the three states below into two.

	``None`` is not ``False``. "Triton has no credential for this account" sends an admin to
	re-credential it; "Triton did not answer" sends them to the gateway. A helper that returned
	a bare boolean would print the first instruction during an outage, confidently.
	"""
	try:
		profile = _as_bot(bot, lambda: _request("GET", PROFILE_PATH))
	except Exception as exc:
		# Class only unless the type guarantees otherwise — `TritonUnavailable` does, by
		# construction, which is the whole reason that type exists.
		from erpnext_enhancements.chat.invoke.triton_client import TritonUnavailable

		return None, (str(exc) if isinstance(exc, TritonUnavailable) else exc.__class__.__name__)
	return bool(profile.get("frappe_api_key_configured")), ""


def _bot_user() -> str:
	"""The account the bot side of chat runs as. One resolver, shared with the summariser."""
	from erpnext_enhancements.chat.invoke.handler import _bot_user as resolve

	return resolve()


def _erpnext_credential(user: str) -> tuple[str, bool]:
	"""``(api_key, secret_exists)``. The secret itself never leaves this module's callers."""
	api_key = frappe.db.get_value("User", user, "api_key") or ""
	return str(api_key), bool(_erpnext_api_secret(user))


def _erpnext_api_secret(user: str) -> str:
	"""The decrypted API secret, or ``""``.

	``raise_exception=False`` because "there is no secret" is an ordinary state this module
	reports on, not an error — a user who has never had keys generated is the common case on
	the first run, and it deserves the instruction above rather than a traceback.
	"""
	from frappe.utils.password import get_decrypted_password

	return get_decrypted_password("User", user, "api_secret", raise_exception=False) or ""


def _as_bot(bot: str, action):
	"""Run ``action`` with the session set to ``bot``, restoring the caller on every path.

	The token cache in ``triton_chat`` is keyed on ``frappe.session.user``, so minting without
	this would hand Triton a token for **whoever clicked the button** — a System Manager — and
	write that person's profile instead of the bot's. The same cache key defeated the
	two-identity rule once already, in ``triton_client.ask``; it is the same trap and it is
	worth recognising on sight.
	"""
	previous = frappe.session.user
	try:
		frappe.set_user(bot)
		return action()
	finally:
		if previous:
			frappe.set_user(previous)


def _request(method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
	"""One authed request to Triton as the current session user.

	Errors are built by ``triton_client``'s helpers rather than re-worded here, so a Triton
	error code reads the same whichever of the two modules provoked it — including the gloss on
	``erpnext_link_required``, which is exactly the condition this module exists to clear and
	which would be absurd to describe differently on the screen that fixes it.

	**The payload is never in an exception.** For ``PUT`` it is the API secret.
	"""
	import requests

	from erpnext_enhancements import triton_chat
	from erpnext_enhancements.chat.invoke import triton_client

	settings = triton_chat.get_settings()
	base_url = settings.get("base_url") or ""
	if not base_url:
		raise triton_client.TritonUnavailable("Triton Settings has no Gateway URL.")
	timeout = cint(settings.get("timeout")) or triton_client.DEFAULT_TIMEOUT

	try:
		token = triton_chat.mint_user_token()
	except Exception as exc:
		raise triton_client.TritonUnavailable(
			f"could not mint a Triton token for this identity: {exc.__class__.__name__}"
		) from None

	try:
		response = requests.request(
			method,
			f"{base_url}{path}",
			json=payload,
			headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
			timeout=timeout,
		)
	except Exception as exc:
		raise triton_client.TritonUnavailable(f"Could not reach Triton: {exc.__class__.__name__}") from None

	if response.status_code >= 400:
		code, upstream_request_id = triton_client._error_fields(response)
		raise triton_client.TritonUnavailable(
			triton_client._error_detail(response.status_code, path, code, upstream_request_id),
			status=response.status_code,
			code=code,
		) from None

	try:
		return response.json() or {}
	except Exception:
		raise triton_client.TritonUnavailable("Triton returned a body that is not JSON") from None

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Operator endpoints for the ad-platform and publishing connections. System Manager only.

Since v1.508.0 the Connect, callback, Disconnect and Test endpoints also serve the three
publishing connections (``publish/oauth.py``), behind the same fences and the same redirect
URI. ``sync_now`` stays ads-only.

Every endpoint is POST, except one: ``oauth_callback`` is **GET**, because that is how an
OAuth provider sends the browser back and no provider can be told otherwise. It is the
deliberate exception to "whitelisted methods are POST-only", and it is fenced four ways:

* **Not a guest endpoint.** The returning browser carries the ERPNext session cookie
  (SameSite=Lax is sent on a top-level navigation), so the callback requires a logged-in
  System Manager like every other endpoint here.
* **The ``state`` is single-use, expires in ten minutes, and is bound to the user** who
  clicked Connect -- a leaked state is useless to anyone else.
* **Rate-limited.**
* **It can only store a token for the platform the state was minted for**, and the code is
  exchanged server-side against the configured client secret, so a forged ``code`` gets
  nothing.

The redirect URI every platform app must register is
``https://erp.sapphirefountains.com/api/method/erpnext_enhancements.marketing.api.oauth_callback``
-- the short path ``marketing/api.py`` re-exports, so the registered URL survives any
refactor of this file.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core import oauth
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.core.utils import field, get_credentials, get_secret

OPERATOR_ROLE = "System Manager"
CREDENTIALS_ROUTE = "/app/marketing-connections"


def _require_operator():
	if OPERATOR_ROLE not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can manage marketing connections."), frappe.PermissionError)


def _require_platform(platform):
	if platform not in oauth.known_connections():
		frappe.throw(_("Unknown connection {0}").format(platform))
	return platform


def _is_publishing(name):
	return name not in C.PLATFORMS


@frappe.whitelist(methods=["POST"])
def start_oauth(platform):
	"""Mint a state and return the consent URL. The form sends the browser there."""
	_require_operator()
	platform = _require_platform(platform)
	creds = get_credentials()
	client_id = creds.get(field(platform, "client_id"))
	if not client_id or not get_secret(creds, field(platform, "client_secret")):
		frappe.throw(_("Enter and save the {0} client ID and client secret first.").format(platform))
	if platform == C.PLATFORM_GOOGLE and not get_secret(creds, field(platform, "developer_token")):
		frappe.throw(_("Enter and save the Google Ads developer token first."))
	state = oauth.mint_state(platform, frappe.session.user)
	if _is_publishing(platform):
		from erpnext_enhancements.marketing.publish import oauth as publish_oauth

		url = publish_oauth.authorization_url(platform, creds, state)
	else:
		url = oauth.authorization_url(platform, client_id, state)
	return {"authorization_url": url, "redirect_uri": oauth.redirect_uri()}


@frappe.whitelist(methods=["GET"])
@rate_limit(limit=30, seconds=3600, methods=["GET"])
def oauth_callback(state=None, code=None, error=None, error_description=None, **_ignored):
	"""Where the platform sends the browser back. See the module docstring for the fences."""
	_require_operator()
	platform = oauth.consume_state(state, frappe.session.user)
	if not platform:
		frappe.throw(
			_("This connection link has expired or was not started by you. Click Connect again."),
			frappe.PermissionError,
		)
	creds = get_credentials()
	if error or not code:
		_record(
			creds,
			platform,
			status="Not Connected",
			message=_("Connection was declined: {0}").format(error or "no code"),
		)
		return _redirect()
	summary = ""
	try:
		if _is_publishing(platform):
			from erpnext_enhancements.marketing.publish import oauth as publish_oauth

			summary = publish_oauth.exchange_code(platform, code, creds)
		else:
			oauth.exchange_code(platform, code, creds)
	except MarketingAPIError as exc:
		_record(creds, platform, status="Auth Failed", message=str(exc))
		return _redirect()
	_record(creds, platform, status="Connected", message=summary, connected=True)
	if _is_publishing(platform):
		_sync_social_accounts(platform, creds)
	return _redirect()


def _sync_social_accounts(connection, creds):
	"""The Social Accounts this connection reaches (v1.509.0). A failure here never undoes the connect."""
	from erpnext_enhancements.marketing.publish import accounts

	try:
		accounts.sync(connection, creds)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			f"Social Account sync failed for {connection}\n\n{frappe.get_traceback()}",
			"Marketing social account sync",
		)


def _record(creds, platform, *, status, message, connected=False):
	creds.set(field(platform, "connection_status"), status)
	creds.set(field(platform, "status_message"), (message or "")[:500])
	if connected:
		creds.set(field(platform, "connected_on"), now_datetime())
		creds.set(field(platform, "connected_by"), frappe.session.user)
	creds.save(ignore_permissions=True)
	frappe.db.commit()


def _redirect():
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = CREDENTIALS_ROUTE


@frappe.whitelist(methods=["POST"])
def disconnect(platform):
	"""Revoke where the platform allows it, then forget every token for it."""
	_require_operator()
	platform = _require_platform(platform)
	creds = get_credentials()
	oauth.revoke(platform, creds)
	for name in ("refresh_token", "access_token"):
		fieldname = field(platform, name)
		if creds.meta.has_field(fieldname):
			creds.set(fieldname, None)
	identity = ()
	if _is_publishing(platform):
		from erpnext_enhancements.marketing.publish import constants as P

		# What was connected goes too; the operator-set Page / Company Page ID stays.
		identity = P.IDENTITY_FIELDS[platform]
	for name in (
		"access_token_expires_on",
		"refresh_token_expires_on",
		"connected_on",
		"connected_by",
		*identity,
	):
		fieldname = field(platform, name)
		if creds.meta.has_field(fieldname):
			creds.set(fieldname, None)
	_record(
		creds, platform, status="Not Connected", message=_("Disconnected by {0}").format(frappe.session.user)
	)
	return {"status": "Not Connected"}


@frappe.whitelist(methods=["POST"])
def test_connection(platform):
	"""Read what the stored credential reaches. Writes nothing but the status."""
	_require_operator()
	platform = _require_platform(platform)
	from erpnext_enhancements.marketing.core.sync import open_transport
	from erpnext_enhancements.marketing.core.utils import get_settings
	from erpnext_enhancements.marketing.platforms import module_for

	creds = get_credentials()
	if _is_publishing(platform):
		return _test_publishing(creds, platform)
	try:
		transport = open_transport(platform, get_settings(), creds)
		accounts = module_for(platform).discover_accounts(transport)
	except MarketingAPIError as exc:
		_record(
			creds, platform, status="Auth Failed" if exc.is_auth_failure else "Connected", message=str(exc)
		)
		return {"ok": False, "message": str(exc)}
	_record(creds, platform, status="Connected", message=_("{0} account(s) readable").format(len(accounts)))
	return {
		"ok": True,
		"heading": _("Readable accounts:"),
		"accounts": [
			f"{a['account_name']} ({a['external_id']}, {a.get('currency') or '?'})" for a in accounts
		],
	}


def _test_publishing(creds, connection):
	"""Re-read the Page, Company Page or channel through the stored token (refreshing it if due)."""
	from erpnext_enhancements.marketing.publish import oauth as publish_oauth

	try:
		lines = publish_oauth.describe(connection, creds)
	except MarketingAPIError as exc:
		if exc.is_auth_failure:
			publish_oauth.mark_dead(creds, connection, str(exc))
			creds.save(ignore_permissions=True)
			frappe.db.commit()
		else:
			_record(creds, connection, status="Connected", message=str(exc))
		return {"ok": False, "message": str(exc)}
	_record(creds, connection, status="Connected", message=" · ".join(lines))
	return {"ok": True, "heading": _("Connected to:"), "accounts": lines}


@frappe.whitelist(methods=["POST"])
def sync_now():
	"""Queue a sync of every switched-on, connected platform, ignoring the nightly throttle."""
	_require_operator()
	from frappe.utils import cint

	from erpnext_enhancements.marketing.core.tasks import enqueue_sync, runnable_platforms
	from erpnext_enhancements.marketing.core.utils import get_settings

	settings = get_settings()
	if not cint(settings.get("enabled")):
		frappe.throw(_("Marketing Settings is switched off. Turn it on first."))
	platforms = runnable_platforms(settings)
	if not platforms:
		frappe.throw(_("No platform is both switched on in Marketing Settings and connected here."))
	return {"queued": enqueue_sync(platforms)}

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Calendar Sync Settings page's buttons (C2): sign-in, the calendar picker, health, preview,
re-sync. Everything here is **System Manager only**, checked explicitly on every call (a whitelisted
function is reachable by any signed-in user otherwise).

* ``authorize_url`` → the Google consent URL. Google sends the browser to Frappe's shared callback
  (``/api/method/frappe.integrations.google_oauth.callback``), which looks up the server-side state
  and calls :func:`oauth_callback` here with the code. That function is deliberately **not**
  whitelisted: only Frappe's callback, holding a single-use state token it issued, can reach it.
* :func:`oauth_callback` checks the person coming back is the System Manager who started it,
  exchanges the code, reads the account's address, and saves the refresh token into the Password
  field. The switch is left as it was.
* ``disconnect`` turns the switch off, forgets the token and asks Google to revoke it.
* ``list_calendars`` lists the account's calendars it can write to, for *Pick calendars*.
* ``health`` (``test=1`` also refreshes the token once), ``preview`` (the dry run: no Google call,
  no write) and ``resync_all`` (queues the full repair on the long queue, one at a time).

Nothing returned or logged carries a token, the client secret or the authorization code.
"""

import frappe
from frappe import _

from erpnext_enhancements.project_enhancements.calendar_sync import engine, google_api

ROLE = "System Manager"
SETTINGS = "Calendar Sync Settings"
WRITE_ROLES = ("owner", "writer")


def _require_manager(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return
	if ROLE not in set(frappe.get_roles(user) or ()):
		frappe.throw(_("Only a System Manager can manage calendar sync."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def authorize_url():
	_require_manager()
	return {"url": google_api.authorization_url(frappe.session.user)}


def oauth_callback(code=None, user=None, **kwargs):
	"""Called by ``frappe.integrations.google_oauth.callback`` with the state we stored."""
	if not code:
		frappe.throw(_("Google sent no authorization code."))
	if not user or frappe.session.user != user:
		frappe.throw(
			_("Sign in as the person who clicked Authorize with Google, then try again."),
			frappe.PermissionError,
		)
	_require_manager(user)
	try:
		tokens = google_api.exchange_code(code)
	except google_api.GoogleCalendarError as exc:
		frappe.throw(_("Google refused the sign-in: {0}").format(exc.describe()))
	refresh = tokens.get("refresh_token")
	if not refresh:
		frappe.throw(
			_("Google sent no refresh token. Remove ERPNext's access in the Google account and try again.")
		)
	granted = set((tokens.get("scope") or "").split())
	if (
		"https://www.googleapis.com/auth/calendar.events" not in granted
		and "https://www.googleapis.com/auth/calendar" not in granted
	):
		frappe.throw(_("Calendar access was not granted. Tick the calendar permission on Google's screen."))
	from erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings.calendar_sync_settings import (
		materialize_defaults,
	)

	materialize_defaults()
	google_api.forget_access_token()
	doc = frappe.get_doc(SETTINGS)
	doc.refresh_token = refresh
	doc.authorized_by = user
	doc.authorized_on = frappe.utils.now_datetime()
	doc.flags.ignore_permissions = True
	doc.save()
	try:
		doc.reload()
		email = google_api.Client().primary_email()
	except Exception:
		email = ""
	frappe.db.set_single_value(SETTINGS, "google_account", email, update_modified=False)
	frappe.clear_document_cache(SETTINGS, SETTINGS)


@frappe.whitelist(methods=["POST"])
def disconnect():
	_require_manager()
	token = google_api.stored_refresh_token()
	doc = frappe.get_doc(SETTINGS)
	doc.enabled = 0
	doc.refresh_token = ""
	doc.google_account = ""
	doc.authorized_on = None
	doc.authorized_by = None
	doc.flags.ignore_permissions = True
	doc.save()
	google_api.forget_access_token()
	return {"revoked": google_api.revoke(token)}


@frappe.whitelist()
def list_calendars():
	_require_manager()
	try:
		items = google_api.Client().calendar_list()
	except google_api.GoogleCalendarError as exc:
		frappe.throw(_("Could not list the calendars: {0}").format(exc.describe()))
	return [
		{
			"id": item.get("id"),
			"summary": item.get("summaryOverride") or item.get("summary") or item.get("id"),
			"access_role": item.get("accessRole"),
			"time_zone": item.get("timeZone"),
			"writable": item.get("accessRole") in WRITE_ROLES,
			"primary": bool(item.get("primary")),
		}
		for item in items
	]


@frappe.whitelist()
def health(test=0):
	_require_manager()
	return engine.health(test=bool(frappe.utils.cint(test)))


@frappe.whitelist()
def preview(start=None, end=None):
	"""The dry run for a range (default: the repair window). Writes nothing, calls no Google API."""
	_require_manager()
	if not start or not end:
		first, last = engine.repair_window()
		start, end = start or first, end or last
	return engine.plan(start, end)


@frappe.whitelist(methods=["POST"])
def resync_all():
	_require_manager()
	if not engine.is_on():
		frappe.throw(_("Switch sync on (and authorize an account) first. The preview works while it is off."))
	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.calendar_sync.engine.run_full_repair",
		queue="long",
		timeout=3600,
		job_id="calendar_sync::full_repair",
		deduplicate=True,
	)
	return {"queued": True}

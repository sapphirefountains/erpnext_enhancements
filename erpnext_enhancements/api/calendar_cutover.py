# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The calendar cutover buttons (C5) on the Calendar Cutover Item list. **System Manager only**,
checked explicitly on every call (a whitelisted function is reachable by any signed-in user otherwise),
and POST only, because each one either reads Google or writes.

* ``scan`` reads the Maintenance calendar (read-only on Google) and fills the review list.
* ``set_status`` approves, ignores or sends rows back to Pending.
* ``apply_approved`` copies Approved rows to their Maintenance Profiles; ``confirm=0`` says what it
  would write.
* ``end_series`` ends the hand-made series at a cutover date: ``confirm=0`` is the dry run; writing
  needs ``confirm=1`` with the dry run's ``token``, the sync switched on and an authorized account.
* ``undo_end`` restores the rules ``end_series`` replaced, the same way (dry run, then confirm).

The work is in ``project_enhancements/calendar_sync/cutover.py``. Nothing returned or logged carries a
token.
"""

import frappe
from frappe import _

from erpnext_enhancements.project_enhancements.calendar_sync import cutover, google_api

ROLE = "System Manager"


def _require_manager(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return
	if ROLE not in set(frappe.get_roles(user) or ()):
		frappe.throw(_("Only a System Manager can run the calendar cutover."), frappe.PermissionError)


def _names(names):
	if not names:
		return []
	if isinstance(names, str):
		names = frappe.parse_json(names)
	return [str(n) for n in names or [] if n]


def _flag(value):
	return bool(frappe.utils.cint(value))


def _google(call, *args, **kwargs):
	try:
		return call(*args, **kwargs)
	except google_api.GoogleCalendarError as exc:
		frappe.throw(_("Google answered with an error: {0}").format(exc.describe()))


@frappe.whitelist(methods=["POST"])
def scan(include_ended=0):
	_require_manager()
	return _google(cutover.scan, include_ended=_flag(include_ended))


@frappe.whitelist(methods=["POST"])
def set_status(names=None, status=None):
	_require_manager()
	return cutover.set_status(_names(names), status)


@frappe.whitelist(methods=["POST"])
def apply_approved(confirm=0, names=None):
	_require_manager()
	return cutover.apply_approved(confirm=_flag(confirm), names=_names(names) or None)


@frappe.whitelist(methods=["POST"])
def end_series(cutover_date=None, confirm=0, token=None):
	_require_manager()
	return _google(cutover.end_series, cutover_date, confirm=_flag(confirm), token=token)


@frappe.whitelist(methods=["POST"])
def undo_end(confirm=0, token=None):
	_require_manager()
	return _google(cutover.undo_end, confirm=_flag(confirm), token=token)

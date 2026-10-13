# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar Sync Settings: the master switch, the Google account and the three calendar IDs.

A Single, System Manager only. **Off by default** (``enabled`` = 0): nothing is queued and no Google
call is made until a System Manager authorizes an account, fills in the calendars and ticks it.

**Singles and their defaults** (CLAUDE.md). The sync writes its own state into this Single (the
snap-back's sync tokens, the last-run stamps), and a Single that has never been saved has no
``tabSingles`` rows: the first ``set_single_value`` of one field would make every other field load
blank, turning ``days_back`` / ``days_ahead`` into 0. So :func:`materialize_defaults` writes every
declared default that has no row before anything writes one field (``engine.write_state`` calls it),
and the patch ``materialize_calendar_sync_settings`` does it once at install.

``validate`` refuses only what cannot work: switching on with no account or no calendar, and a window
that is negative or absurd. It never refuses a field nobody touched, so the page cannot become
unsaveable the way Chat Settings once did.

**Read time off from Google** (C4, v1.607.0, off by default) is a second switch under the first:
``calendar_sync.time_off`` reads people's time off off the holidays calendar only while both are on,
an account is authorized and the calendar ID is filled in. Ticking it queues one full read.

The class name is the one Frappe derives (``doctype.replace(" ", "")``);
``tests/test_doctype_controller_names.py`` guards it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

SETTINGS_DOCTYPE = "Calendar Sync Settings"
MAX_DAYS_BACK = 90
MAX_DAYS_AHEAD = 730
CALENDAR_FIELDS = ("projects_calendar_id", "maintenance_calendar_id", "holidays_calendar_id")


class CalendarSyncSettings(Document):
	def validate(self):
		for field in CALENDAR_FIELDS:
			self.set(field, (self.get(field) or "").strip())
		back, ahead = self.get("days_back"), self.get("days_ahead")
		if back not in (None, "") and not 0 <= int(back) <= MAX_DAYS_BACK:
			frappe.throw(_("Days back must be between 0 and {0}.").format(MAX_DAYS_BACK))
		if ahead not in (None, "") and not 1 <= int(ahead) <= MAX_DAYS_AHEAD:
			frappe.throw(_("Days ahead must be between 1 and {0}.").format(MAX_DAYS_AHEAD))
		seen = set()
		for row in self.get("colors") or []:
			if row.get("applies_to") in seen:
				frappe.throw(_("{0} has two colors. Keep one row for it.").format(row.get("applies_to")))
			seen.add(row.get("applies_to"))
		if int(self.get("enabled") or 0):
			if not self.get_password(fieldname="refresh_token", raise_exception=False):
				frappe.throw(_("Click Authorize with Google before switching sync on."))
			if not any(self.get(field) for field in CALENDAR_FIELDS):
				frappe.throw(_("Fill in at least one calendar ID before switching sync on."))
		self._validate_time_off()

	def _validate_time_off(self):
		"""C4: *Read time off from Google* needs the switch, an account and the holidays calendar.
		Checked only when it is being ticked (or the calendar cleared under it), so switching sync off
		while it is ticked still saves; the read itself checks all three every run."""
		if not int(self.get("read_time_off") or 0):
			return
		if not self.get("holidays_calendar_id"):
			frappe.throw(
				_("Fill in the Company Holidays & Vacations calendar ID before reading time off from it.")
			)
		before = self.get_doc_before_save()
		if before is not None and int(before.get("read_time_off") or 0):
			return
		if not int(self.get("enabled") or 0):
			frappe.throw(_("Switch sync on before reading time off from Google."))

	def on_update(self):
		"""Switched on, or a calendar changed while on: repair the window straight away. *Read time
		off from Google* just ticked, or its calendar changed: read the window in straight away."""
		self._queue_time_off_read()
		try:
			before = self.get_doc_before_save()
			if not int(self.get("enabled") or 0):
				return
			changed = before is None or not int(before.get("enabled") or 0)
			changed = changed or any(before.get(f) != self.get(f) for f in CALENDAR_FIELDS)
			changed = changed or [(r.applies_to, r.color_id) for r in before.get("colors") or []] != [
				(r.applies_to, r.color_id) for r in self.get("colors") or []
			]
			if changed:
				frappe.enqueue(
					"erpnext_enhancements.project_enhancements.calendar_sync.engine.run_full_repair",
					queue="long",
					timeout=3600,
					enqueue_after_commit=True,
					job_id="calendar_sync::full_repair",
				)
		except Exception:
			frappe.log_error(title="Calendar sync: repair not queued", message=frappe.get_traceback())

	def _queue_time_off_read(self):
		try:
			if not int(self.get("read_time_off") or 0) or not int(self.get("enabled") or 0):
				return
			before = self.get_doc_before_save()
			if (
				before is not None
				and int(before.get("read_time_off") or 0)
				and int(before.get("enabled") or 0)
				and before.get("holidays_calendar_id") == self.get("holidays_calendar_id")
			):
				return
			from erpnext_enhancements.project_enhancements.calendar_sync import time_off

			time_off.queue_full_read()
		except Exception:
			frappe.log_error(title="Calendar time off: read not queued", message=frappe.get_traceback())


def missing_defaults(fields, stored):
	"""``{fieldname: default}`` for each declared default with no stored row (never over a row)."""
	return {
		name: default
		for name, default in fields
		if name and default not in (None, "") and name not in (stored or ())
	}


def materialize_defaults():
	"""Give every declared default that has no ``tabSingles`` row its row. Returns how many.

	Reads ``tabSingles`` with raw SQL (never ``db.get_value("Singles", ...)``, CLAUDE.md) and writes
	with ``set_single_value``, which runs no validate. Safe twice; never raises.
	"""
	try:
		if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
			return 0
		stored = {
			row[0]
			for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS_DOCTYPE,))
		}
		meta = frappe.get_meta(SETTINGS_DOCTYPE)
		todo = missing_defaults(
			((getattr(df, "fieldname", None), getattr(df, "default", None)) for df in meta.fields), stored
		)
		if todo:
			frappe.db.set_single_value(SETTINGS_DOCTYPE, todo, update_modified=False)
			frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
		return len(todo)
	except Exception:
		frappe.log_error(
			title="Calendar sync: settings defaults",
			message="Could not write the Calendar Sync Settings defaults; open and save the Settings once.",
		)
		return 0

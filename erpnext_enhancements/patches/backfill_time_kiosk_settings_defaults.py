"""Give the **existing** Time Kiosk Settings row the v1.480.0 defaults, then apply two policy flips.

**A ``default`` on a new field of a Single never reaches the row that already exists.** A
Single stores one row per field in ``tabSingles``; ``bench migrate`` adds no row for a newly
declared field and ``load_from_db`` applies no defaults — they fire in ``new_doc()``, on a
fresh install and never again. v1.480.0 adds eleven fields to Time Kiosk Settings
(``auto_close_after_hours``, ``tracking_gap_minutes``, ``keep_low_accuracy_fixes``,
``anchor_accuracy_m``, ``offsite_warn``, ``overtime_week_start``, ``overtime_weekly_hours``,
``default_burden_pct``, ``send_supervisor_digest``, plus the two sections' column breaks),
and ``get_settings()`` papers over a missing row with ``DEFAULTS`` — but the Desk form does
not, so the sweeper limit would read blank on the settings page while the code ran with 14.

Part one fills each declared default **only where ``tabSingles`` has no row** for the field,
copying ``backfill_marketing_settings_defaults`` — never over a stored falsy value, because an
unticked box and a deliberate ``0`` are not the same fact. The read goes to ``tabSingles`` in
raw SQL: ``frappe.db.get_value("Singles", …)`` compiles to ``ORDER BY creation`` on a table
that has no such column and raises on every site (CLAUDE.md), and a patch that raises aborts
``bench migrate``, which on this repo is the deploy.

Part two is a **documented one-time policy change, decided by Nik on 2026-09-17**, and it is
unconditional on purpose: ``keep_wake_lock`` goes from 0 to **1** (a phone that sleeps in a
pocket stops reporting, and the tracking gaps that produced cost more than the battery does)
and ``retention_days`` goes from 90 to **0** — keep forever (the trail is the evidence behind a
corrected timesheet, and a 90-day purge deleted it before the questions arrived). Both rows
exist on prod with the old values (verified live 2026-09-17: wake lock 0, retention 90), so a
fill-blanks pass would leave them exactly as they are. Patches run once, so a later manual
opt-out survives.

Safe to run twice: the second run finds rows for everything, and the two flips are idempotent.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Time Kiosk Settings"

#: Nik's 2026-09-17 decisions, applied unconditionally once.
POLICY_FLIPS = {
	"keep_wake_lock": 1,
	"retention_days": 0,
}


def execute() -> None:
	try:
		written = backfill_time_kiosk_settings_defaults()
		flipped = apply_policy_flips()
		print(f"Time Kiosk Settings: {written} default(s) backfilled, {flipped} policy flip(s) applied")
	except Exception:
		# Never abort the migrate over a settings row; the in-process DEFAULTS cover the gap.
		frappe.log_error(title="backfill_time_kiosk_settings_defaults")


def backfill_time_kiosk_settings_defaults() -> int:
	"""Write each declared default that has no stored row. Returns how many it wrote."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0

	stored = {
		row[0]
		for row in frappe.db.sql(
			"select field from tabSingles where doctype = %s",
			(SETTINGS_DOCTYPE,),
		)
	}

	written = 0
	for field in frappe.get_meta(SETTINGS_DOCTYPE).fields:
		if field.fieldtype in no_value_fields:
			continue
		if field.default in (None, ""):
			continue
		if field.fieldname in stored:
			continue
		# set_single_value writes tabSingles directly and does NOT run the controller's
		# validate — a patch going through the document API could not fix a document
		# whose validate is what fails.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, field.fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written


def apply_policy_flips() -> int:
	"""Set the two decided values whatever is stored. Returns how many changed."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0
	changed = 0
	for fieldname, value in POLICY_FLIPS.items():
		current = frappe.db.get_single_value(SETTINGS_DOCTYPE, fieldname)
		if current is not None and int(current or 0) == value:
			continue
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, value)
		changed += 1
	if changed:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
	return changed

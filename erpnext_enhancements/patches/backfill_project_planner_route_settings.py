"""Give an already-saved Project Planner Settings the defaults of the Routes fields (v1.578.0).

**A ``default`` on a new field of a Single never reaches the row that already exists.** The
Routes section added four fields to ``Project Planner Settings``, a Single that has been saved
since v1.577.0. ``bench migrate`` writes no ``tabSingles`` row for a new field, so on every such
site they read ``None``: drive time would be counted against nobody's hours, Google would never be
asked, and the day would start at no time at all, with the page showing the boxes empty and
nothing saying why.

The fields are ``pad_drive_time``, ``use_google_routes``, ``day_start_time`` and
``long_drive_minutes``. The shop's coordinates are not listed: they are a cache the routing module
fills, and a default would be a lie about them.

The same shape as ``backfill_marketing_settings_defaults``, with two differences that matter here:

* **Only where there is no row.** A field is filled only where ``tabSingles`` has no row for it,
  never over a stored value. An unticked box and a deliberate ``0`` are different decisions.
* **A Single never saved at all is left alone.** ``load_from_db`` builds a Single that has no rows
  from ``new_doc()``, with every default in the JSON. Writing four rows into it would end that for
  the other fields in the Single (the full-day hours, the visit length), and they would read
  ``None`` instead of their defaults. ``backfill_rental_settings_defaults`` makes the same call.

Reads ``tabSingles`` with raw SQL, never ``db.get_value("Singles", ...)``, which orders by
``creation`` and cannot succeed on that table (see CLAUDE.md).

Cannot raise: a patch that raises aborts ``bench migrate``, which on this repo is the deploy.
Safe twice: the second run finds rows for everything and writes nothing.
"""

import frappe

SETTINGS_DOCTYPE = "Project Planner Settings"

FIELDS = (
	"pad_drive_time",
	"use_google_routes",
	"day_start_time",
	"long_drive_minutes",
)


def execute() -> None:
	try:
		backfill_project_planner_route_settings()
	except Exception:
		# A fixed message and no traceback: this runs inside bench migrate, and the defaults
		# are repairable by hand, so a failure here must not stop the deploy.
		frappe.log_error(
			title="Project Planner: route settings backfill",
			message="The Routes defaults were not backfilled; set them by hand in Project Planner Settings.",
		)


def backfill_project_planner_route_settings() -> int:
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
	if not stored:
		return 0

	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	written = 0
	for fieldname in FIELDS:
		field = meta.get_field(fieldname)
		if not field or field.default in (None, "") or fieldname in stored:
			continue
		# set_single_value writes tabSingles directly and does not run validate, the same
		# reasoning as the marketing backfill: a patch that goes through the document API
		# would be refused by the very rule it is repairing.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written

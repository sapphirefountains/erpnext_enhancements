# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Settings: how many hours the planner books when nobody wrote an estimate.

A Single. Read it with `frappe.get_cached_doc("Project Planner Settings")` and never with
`frappe.db.get_single_value`: a Single that has never been saved has no `tabSingles` rows, so
`get_single_value` returns None for every field, while `get_cached_doc` goes through
`load_from_db`, which hands back the JSON defaults. Readers still treat None as "use the default"
(`x if x is not None else default`), but a deliberate 0 stays 0.

The validation only refuses what cannot mean anything -- negative hours, or more than a day -- and
the one value the engine falls back on, a full day of zero hours. It does not reject a zero visit
length or rental length: those mean "do not book hours for this", which is a choice.

There is no backfill patch for the defaults, unlike a Single that gained fields later (see
CLAUDE.md): this doctype is new, so every field is declared before any row can exist.

The Routes section (v1.578.0) added fields to a Single that already had rows, so the defaults
never reached it: `backfill_project_planner_route_settings` fills them in once. That is why
`validate` refuses nothing about the new fields when they are None or blank. Only a value that
cannot mean anything is refused: a negative drive limit. The shop's coordinates are a read-only
cache the routing module writes, so validate never looks at them.

**Writing that cache is what made this Single fragile (fixed v1.578.1).** The first route to
locate the shop wrote three `tabSingles` rows into a Settings nobody had ever saved. From then on
`load_from_db` read the stored rows instead of `new_doc()`, and every field without a row came
back blank -- and a blank **Check** is turned into 0 by `_fix_numeric_types`. So *Count drive
time against people's hours* and *Drive times from Google Routes* switched themselves off the
moment routing first worked, with nothing on screen to say so. Caught on production by
`check_routes`, which reported "Google Routes is working ... turned off" before any planner had
committed the rows. `materialize_defaults` writes every declared default that has no row, and
`routing.start_point` calls it before caching anything, so a partial Single can no longer exist.
"""

import frappe
from frappe import _
from frappe.model.document import Document

SETTINGS_DOCTYPE = "Project Planner Settings"

HOUR_FIELDS = (
	"default_day_hours",
	"maintenance_visit_hours",
	"rental_delivery_hours",
	"rental_setup_hours",
	"rental_takedown_hours",
	"rental_cleaning_hours",
)


class ProjectPlannerSettings(Document):
	def validate(self):
		for field in HOUR_FIELDS:
			value = self.get(field)
			if value is not None and not 0 <= float(value) <= 24:
				frappe.throw(_("{0} must be between 0 and 24 hours.").format(self.meta.get_label(field)))
		if self.default_day_hours is not None and float(self.default_day_hours) <= 0:
			frappe.throw(_("Full-day hours must be more than 0."))
		minutes = self.get("long_drive_minutes")
		if minutes not in (None, "") and float(minutes) < 0:
			frappe.throw(_("{0} cannot be negative.").format(self.meta.get_label("long_drive_minutes")))


def missing_defaults(fields, stored):
	"""``{fieldname: default}`` for each declared default with no stored row.

	``fields`` are ``(fieldname, default)`` pairs from the doctype's meta; ``stored`` is the set of
	fieldnames that already have a ``tabSingles`` row. Never a field that has a row: an unticked
	box and a deliberate 0 are decisions, a missing row is not.
	"""
	return {
		name: default
		for name, default in fields
		if name and default not in (None, "") and name not in (stored or ())
	}


def materialize_defaults():
	"""Give every declared default that has no ``tabSingles`` row its row. Returns how many.

	Call before writing any single field of this Single (see the module docstring): it is the
	write of one row that ends ``new_doc()`` defaults for all the others. Reads ``tabSingles`` with
	raw SQL (never ``db.get_value("Singles", ...)``, CLAUDE.md) and writes with
	``set_single_value``, which runs no validate. Safe twice; never raises.
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
			title="Project Planner: settings defaults",
			message="Could not write the Project Planner Settings defaults; open and save the Settings once.",
		)
		return 0

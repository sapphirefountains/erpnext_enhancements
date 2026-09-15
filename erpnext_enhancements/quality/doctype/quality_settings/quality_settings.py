# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Master switches for the Quality module.

Every dial here ships **off or permissive**, because the module lands before the inspection
chain it configures exists. ``quality_enabled`` is the one that matters: nothing in this module
acts while it is unticked, so sub-phase A can deploy to production without changing a single
thing anybody sees.

Why ``validate`` repairs instead of rejecting
---------------------------------------------

A ``default`` on a new field of a Single **never reaches the row that already exists**. A Single
stores one row per field in ``tabSingles``; ``bench migrate`` adds no row for a newly declared
field and ``load_from_db`` applies no defaults — they fire in ``new_doc()``, i.e. on a fresh
install and never again. So on every site that existed before a field did, that field reads
``None``.

That is harmless until a controller *rejects* the value. Chat Settings shipped 37 fields this
way and its settings page became **unsaveable**: ``validate`` refused the fifteen zeros, so
opening the page and pressing Save returned fifteen errors about fields nobody had touched.

Note the shape of it, because it is the reason this file repairs rather than validates: saving a
Single deletes and re-inserts every field row, so a page people use daily self-heals on the next
save. The ones that bite are the settings for **dormant** features — where the first save is the
one you need and the one that fails. This module is dormant by design, which puts it squarely in
that trap.

So there are two defences and they are deliberate. ``patches/backfill_quality_settings_defaults``
fills the missing rows at migrate time, and this controller repairs a missing dial in place, so
the page cannot brick even if the patch has not run. Neither one writes over a stored falsy
value: an unticked box and a deliberate ``0`` are both falsy and are not the same fact.
"""

import frappe
from frappe.model.document import Document

#: Fieldname -> the value to use when the field has no stored row at all.
#: Kept here rather than read back from the DocType meta so that a repair is a decision in
#: code, reviewable in a diff, rather than whatever the JSON happened to say that week.
REPAIRABLE_DEFAULTS = {
	"critical_ack_sla_hours": 24,
	"company_floor_enforcement": "Warn",
	# WI-075 sub-phase L. `patches/backfill_quality_settings_defaults` is registered on
	# `after_migrate` and fills any declared default with no stored row, so it covers these
	# already; they are repeated here for the same reason as the two above -- the page must not
	# brick if that has not run, and this module is dormant, which is exactly the case where the
	# first save is the one you need and the one that fails.
	"msa_expiry_enforcement": "Warn",
	"msa_warn_days": 60,
	"msa_escalate_days": 14,
}

#: Values ``company_floor_enforcement`` is allowed to hold. A stored value outside a Select's
#: options makes the row unsaveable and no ``ignore_*`` flag bypasses ``_validate_selects``, so
#: an unrecognised value is repaired here rather than left to raise on the user's next save.
FLOOR_ENFORCEMENT_OPTIONS = ("Off", "Warn", "Block")


class QualitySettings(Document):
	def validate(self):
		self._repair_missing_dials()
		self._clamp_sla_hours()

	def _repair_missing_dials(self):
		"""Fill a dial that has **no stored row**, never one holding a falsy value.

		``self.get(field)`` cannot tell "no row in tabSingles" from "a stored empty string",
		which is why the stored-row check goes to the table. Reading it through
		``frappe.db.sql`` rather than ``frappe.db.get_value`` is not a style choice:
		``tabSingles`` has exactly three columns — ``doctype``, ``field``, ``value`` — and
		``get_value`` defaults to ``order_by="creation"``, so it compiles to a query ending
		``ORDER BY creation`` and raises ``Unknown column 'creation'`` on every site, every
		time. See ``tests/test_singles_table_access.py``.
		"""
		stored = {
			row[0]
			for row in frappe.db.sql(
				"select field from tabSingles where doctype = %s", (self.doctype,)
			)
		}
		for fieldname, fallback in REPAIRABLE_DEFAULTS.items():
			if fieldname in stored:
				continue
			if self.get(fieldname) in (None, ""):
				self.set(fieldname, fallback)

		if self.company_floor_enforcement not in FLOOR_ENFORCEMENT_OPTIONS:
			self.company_floor_enforcement = "Warn"

	def _clamp_sla_hours(self):
		"""A zero or negative SLA would nag on every sweep; treat it as "use the default"."""
		if not self.critical_ack_sla_hours or self.critical_ack_sla_hours < 1:
			self.critical_ack_sla_hours = REPAIRABLE_DEFAULTS["critical_ack_sla_hours"]


def is_enabled() -> bool:
	"""Whether the Quality module should act at all.

	Every entry point in this module calls this first. Guarded on the DocType existing because
	callers fire from ``doc_events`` that also run during ERPNext's own test bootstrap, before
	this app's tables are created.
	"""
	if not frappe.db.exists("DocType", "Quality Settings"):
		return False
	return bool(frappe.db.get_single_value("Quality Settings", "quality_enabled"))

"""Sales Activity Settings (Single) doctype controller.

Holds two settings:

* the global ``inactivity_threshold`` (days), the fallback reminder window used by
  ``script_migrations.customer.customer_inactivity_reminder`` for Customers without a
  per-customer ``custom_reminder_days`` (ported from a DB-only custom DocType);
* ``stage_probabilities``, the forecast probability an Opportunity takes when it enters a stage
  (form kit P5.5, ``crm_enhancements.opportunity_probability``). Seeded with Nik's numbers of
  2026-10-13 by ``patches/seed_opportunity_stage_probabilities``.

Validation: one row per stage, and a probability from 0 to 100. A clear message rather than a
silent pick, because two rows for one stage would make a deal's probability depend on row order.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SalesActivitySettings(Document):
	def validate(self):
		seen = set()
		for row in self.get("stage_probabilities") or []:
			if row.stage in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, _(row.stage)))
			seen.add(row.stage)
			if not 0 <= flt(row.probability) <= 100:
				frappe.throw(_("Row {0}: a probability is between 0 and 100.").format(row.idx))

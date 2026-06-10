"""Backfill ``Opportunity.custom_stage_changed_on`` for pre-existing records.

The Sales Pipeline board ages every opportunity by its last status change
(``custom_stage_changed_on``, stamped by ``sales_pipeline.stamp_stage_change``
from v1.2.0 on). Records saved before that hook shipped have no stamp, and the
board would fall back to ``modified`` at read time forever. This one-shot sets
the stamp to ``modified`` — the best available approximation of "entered the
current stage" — for every Opportunity missing it.

Ordering note: patches run **before** fixture sync (see fixtures/README.md), so
on the deploy that introduces the field the column does not exist yet when this
executes. The patch therefore creates the Custom Field itself if missing, with
``is_system_generated=False`` and the exact fixture definition — fixture sync
then adopts the same record (same name) later in the migrate, keeping the
fixtures the source of truth.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	if not frappe.db.exists("Custom Field", "Opportunity-custom_stage_changed_on"):
		create_custom_field(
			"Opportunity",
			{
				"fieldname": "custom_stage_changed_on",
				"fieldtype": "Datetime",
				"label": "Stage Changed On",
				"insert_after": "custom_date_closed_won",
				"read_only": 1,
				"no_copy": 1,
			},
			is_system_generated=False,
		)

	frappe.db.sql(
		"""
		UPDATE `tabOpportunity`
		SET custom_stage_changed_on = modified
		WHERE COALESCE(custom_stage_changed_on, '') = ''
		"""
	)

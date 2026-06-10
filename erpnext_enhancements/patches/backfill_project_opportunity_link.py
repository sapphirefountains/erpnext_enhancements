"""Backfill ``Project.custom_opportunity`` from ``Opportunity.custom_created_project``.

Until v1.3.0 the Opportunity→Project conversion never persisted a forward
link: the desk path stamped ``custom_sales_opportunity`` (a field that does
not exist on the site — the value was dropped on insert) and the background
creator stamped only the reverse ``Opportunity.custom_created_project``.
The forward link now keys the PRO-0204 hand-off engine, AE resolution for
payment/step alerts, and the attachment sync, so this one-shot fills it for
every existing project the reverse link can identify. Idempotent and
non-destructive: only empty ``custom_opportunity`` values are written.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabProject` p
		JOIN `tabOpportunity` o ON o.custom_created_project = p.name
		SET p.custom_opportunity = o.name
		WHERE COALESCE(p.custom_opportunity, '') = ''
		"""
	)

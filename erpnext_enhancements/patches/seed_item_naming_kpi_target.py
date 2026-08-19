# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give `item_naming_compliance_pct` a KPI Target, so it renders graded rather than bare.

`kpi_dashboards/snapshots.py` documents the failure this avoids at length: a metric with no
`KPI Target` row gets no Good/Watch/Bad grading, so it renders as a plain grey number that
nobody disbelieves — which is how a labour-utilisation KPI read 3600x high for months without
anyone querying it.

**Why 100 is not an invented threshold.** For most KPIs a target is a business judgement and
seeding one would be putting words in the Process Owner's mouth. A *compliance percentage* is
the exception: 100% is what "compliant" means, and choosing 95 would be choosing how much
non-compliance is acceptable — which genuinely is the Process Owner's call, and which they can
make by editing this row. Seeding the definitional value and letting them lower it is the way
round that does not require them to act before the number means anything.

Safe twice: keyed on the doctype's own `format:` name, and it does not touch a row somebody has
already edited.
"""

import frappe

TARGET_NAME = "TGT-Product-item_naming_compliance_pct-Daily"


def execute():
	if not frappe.db.exists("DocType", "KPI Target"):
		# The KPI package is not installed on this site. Nothing to seed, nothing to warn about.
		return
	if frappe.db.exists("KPI Target", TARGET_NAME):
		return

	frappe.get_doc({
		"doctype": "KPI Target",
		"department": "Product",
		"kpi_key": "item_naming_compliance_pct",
		"label": "Item Naming Compliance",
		"period": "Daily",
		"target_value": 100,
		"unit": "%",
		"direction": "Higher is better",
		"notes": (
			"Seeded with the Item Naming Audit report (v1.337.0). 100 is the definition of "
			"compliant rather than a chosen threshold — lower it deliberately if the business "
			"decides some level of non-compliance is acceptable. Measured against the ERPNext "
			"Item Naming Schema SOP v1.0; the rules are in "
			"inventory_enhancements/item_naming_rules.py and the work list is the Item Naming "
			"Audit report."
		),
	}).insert(ignore_permissions=True)

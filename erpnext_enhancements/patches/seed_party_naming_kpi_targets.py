# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""KPI Targets for the three party-naming compliance metrics.

Same reasoning as `seed_item_naming_kpi_target`, which this follows: `snapshots.py` records
that a metric with no `KPI Target` gets no Good/Watch/Bad grading and renders as a plain grey
number nobody disbelieves — which is how a labour-utilisation KPI read 3600x high for months.

**Why 100 is not an invented threshold.** For most KPIs a target is a business judgement and
seeding one would put words in somebody's mouth. A *compliance percentage* is the exception:
100% is what "compliant" means. Choosing 95 would be choosing how much non-compliance is
acceptable, which genuinely is a decision for whoever owns the data — and they can make it by
editing the row. Seeding the definitional value and letting them lower it is the way round
that does not require them to act before the number means anything.

Note the denominators these grade against are **in-scope** records, not all of them: internal
Projects and unlinked Addresses are excluded by the rules module, so 100% is reachable rather
than aspirational. See `crm_enhancements.party_naming_rules.in_scope`.

Safe twice: keyed on the doctype's own `format:` name, and it does not touch a row somebody
has already edited.
"""

import frappe

#: (department, kpi_key, label). Projects are Operations; Opportunity and Address are Sales,
#: because addresses are account data — the same reason `Account Data Quality` is a CRM report.
TARGETS = (
	("Operations", "project_naming_compliance_pct", "Project Naming Compliance"),
	("Sales", "opportunity_naming_compliance_pct", "Opportunity Naming Compliance"),
	("Sales", "address_naming_compliance_pct", "Address Naming Compliance"),
)


def execute():
	if not frappe.db.exists("DocType", "KPI Target"):
		# The KPI package is not installed on this site. Nothing to seed, nothing to warn about.
		return

	for department, kpi_key, label in TARGETS:
		name = f"TGT-{department}-{kpi_key}-Daily"
		if frappe.db.exists("KPI Target", name):
			continue
		frappe.get_doc({
			"doctype": "KPI Target",
			"department": department,
			"kpi_key": kpi_key,
			"label": label,
			"period": "Daily",
			"target_value": 100,
			"unit": "%",
			"direction": "Higher is better",
			"notes": (
				"Seeded with the Party Naming Audit report (v1.339.0). 100 is the definition "
				"of compliant rather than a chosen threshold — lower it deliberately if some "
				"level of non-compliance is acceptable. Measured over IN-SCOPE records only: "
				"internal Projects and Addresses linked to nothing are excluded, so 100% is "
				"reachable. Rules in crm_enhancements/party_naming_rules.py; the work list is "
				"the Party Naming Audit report."
			),
		}).insert(ignore_permissions=True)

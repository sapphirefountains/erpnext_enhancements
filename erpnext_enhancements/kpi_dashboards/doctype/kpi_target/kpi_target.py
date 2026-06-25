"""KPI Target — the per-department, per-KPI target a snapshot value is graded against.

A deliberately tiny doctype: one row per (department, kpi_key, period), edited by
managers (System Manager / Accounts Manager / Sales Manager). It is the single
highest-leverage enabler for every "vs target / vs plan / vs quota" KPI — the
snapshot engine joins these in at build time to compute each value's
Good/Watch/Bad status without a full budgeting module.
"""

from frappe.model.document import Document


class KPITarget(Document):
	pass

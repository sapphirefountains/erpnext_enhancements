# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which KPI figures state the company's money, and who may be given them.

**The rule** (owner decision, 2026-11-19): a figure that states the company's money is
returned only to a caller who holds Accounts Manager or System Manager. Every other role
that may open a department's dashboard keeps that department's operational figures
(counts, days, hours, percentages that are not margin) and the money figures are simply
left out of the answer: not zeroed, not hidden in the browser. The department gate
(``api.kpi.DEPARTMENT_ROLES``) is unchanged; this module is only about the figures.

**One registry.** ``MONEY_KPIS`` is every ``kpi_key`` that carries a money figure, and
``REVIEWED_NOT_MONEY`` is every key whose name or label looks like money but is not (a
count, a duration, a ratio of hours), each with the reason. A key is the same figure in
every department that emits it, so the registry is keyed by ``kpi_key`` alone.
``tests/test_kpi_money_roles.py`` reads every aggregator and fails the build when a key
that looks like money is in neither table, so a new KPI has to be classified when it is
added.

**One helper.** ``can_see_money`` is the role rule and ``without_money`` is the filter.
Every read path calls them: the cockpit and executive endpoints, the Task Dashboard wall,
the assistant tool (through ``api.kpi``), KPI Snapshot document reads and lists
(``kpi_dashboards/permissions.py`` and the KPI Snapshot controller), and the department
worklist feeds that print an amount beside a row.

Pure on purpose: nothing here imports ``frappe`` at module scope, so the bench-free suite
runs the filter.
"""

#: Roles that may see money figures. System Manager is always included.
MONEY_ROLES = frozenset({"Accounts Manager", "System Manager"})

#: kpi_key -> label, grouped by the department that emits it. The Executive rollup
#: re-surfaces several of these under the same key, so it needs no entries of its own.
MONEY_KPIS = {
	# Finance
	"ar_outstanding": "Accounts Receivable",
	"ar_overdue": "AR Overdue",
	"revenue_30": "Revenue (30d)",
	"revenue_mtd": "Revenue (MTD)",
	"ap_outstanding": "Accounts Payable",
	"cash_collected_30": "Cash Collected (30d)",
	# Operations
	"store_run_spend_30": "Store-Run Spend (30d)",
	"inventory_stock_value": "Inventory Stock Value",
	# Sales
	"open_pipeline_value": "Open Pipeline Value",
	"won_value_30": "Closed-Won Value (30d)",
	"avg_deal_size": "Avg Deal Size (30d)",
	# Production (backlog_value is also the Executive rollup's)
	"backlog_value": "Backlog (Open Project Value)",
	"build_backlog_value": "Build Backlog Value",
	"change_order_net_value": "Change Order Net Value",
	# Marketing
	"marketing_pipeline": "Sourced Pipeline Value",
	"marketing_spend_mtd": "Marketing Spend (MTD)",
	"cpl_mtd": "Cost Per Lead (MTD)",
	# Product
	"catalog_revenue_30": "Catalog Revenue (30d)",
	"catalog_revenue_365": "Catalog Revenue (1y)",
	"rental_backlog_value": "Events Backlog Value",
	"gross_margin_pct": "Gross Margin (1y)",
	"rental_booked_revenue_90": "Rental Revenue Booked (90d)",
	"rental_revenue_per_fountain_90": "Rental Revenue per Fountain (90d)",
	# Executive
	"revenue_per_employee": "Revenue per Employee (30d)",
}

#: kpi_key -> why it stays, for keys whose name or label reads like money but is not.
REVIEWED_NOT_MONEY = {
	"dso": "A duration in days; it states no amount.",
	"overdue_invoices": "A count of invoices.",
	"placeholder_cost_stock_lines": "A count of stock lines; no cost amount is shown.",
	"unpriced_po_lines_90": "A count of purchase order lines.",
	"handoff_backlog": "A count of won deals waiting for a project.",
	"labor_budget_utilization": "A ratio of hours used to hours budgeted.",
	"marketing_won_30": "A count of sourced wins.",
}

#: Row keys that carry an amount in a department worklist feed. The feed leaves the key
#: out of each row for a caller who may not see money.
WORKLIST_AMOUNT_KEYS = ("amount",)


def can_see_money(roles=None):
	"""True when the session user (or the given roles) may be given money figures."""
	if roles is None:
		import frappe

		roles = frappe.get_roles()
	return bool(MONEY_ROLES & set(roles))


def is_money_kpi(kpi_key):
	return kpi_key in MONEY_KPIS


def without_money(rows, key="kpi_key", roles=None):
	"""``rows`` with every money figure removed, unless the caller may see money.

	``rows`` are dicts or documents carrying a ``kpi_key``. The input is never changed;
	a caller who may see money gets the same list back.
	"""
	rows = list(rows or [])
	if can_see_money(roles):
		return rows
	return [row for row in rows if not is_money_kpi(_key_of(row, key))]


def _key_of(row, key):
	if isinstance(row, dict):
		return row.get(key)
	return getattr(row, key, None)


def drop_worklist_amounts(rows, roles=None):
	"""Remove the amount from each worklist row for a caller who may not see money."""
	if can_see_money(roles):
		return rows
	for row in rows:
		for amount_key in WORKLIST_AMOUNT_KEYS:
			row.pop(amount_key, None)
	return rows

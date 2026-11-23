# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which KPI figures state revenue or margin, and who may be given them.

**The rule** (owner decision, 2026-11-19): a figure that states the company's revenue or
margin is returned only to a caller who holds Accounts Manager or System Manager. Cash
collected in the period is counted with revenue (the owner's answer the same day: "hide
it too"). Every other role that may open a department's dashboard keeps everything else
that department shows, money figures included (receivables, payables, spend, stock value,
pipeline, won value, deal size, backlog, change-order value, cost per lead). The revenue
and margin figures are simply left out of the answer: not zeroed, not hidden in the
browser. The department gate (``api.kpi.DEPARTMENT_ROLES``) is unchanged; this module is
only about the figures.

**One registry.** ``RESTRICTED_KPIS`` is every ``kpi_key`` that states revenue or margin,
and ``REVIEWED_UNRESTRICTED`` is every key whose name, label or unit reads like money but
is not a revenue or margin figure, each with the reason. A key is the same figure in every
department that emits it, so the registry is keyed by ``kpi_key`` alone.
``tests/test_kpi_money_roles.py`` reads every aggregator and fails the build when a key
that reads like money is in neither table, so a new KPI has to be classified when it is
added.

**One helper.** ``can_see_revenue`` is the role rule and ``without_revenue`` is the filter.
Every read path calls them: the cockpit and executive endpoints, the Task Dashboard wall,
the assistant tool (through ``api.kpi``), and KPI Snapshot document reads and lists
(``kpi_dashboards/permissions.py`` and the KPI Snapshot controller). KPI Target rows for
these keys follow the same rule (``permissions.py``).

Pure on purpose: nothing here imports ``frappe`` at module scope, so the bench-free suite
runs the filter.
"""

#: Roles that may see revenue and margin figures. System Manager is always included.
REVENUE_ROLES = frozenset({"Accounts Manager", "System Manager"})

#: kpi_key -> label for every figure that states revenue or margin. The Executive rollup
#: re-surfaces revenue_30 under the same key, so it needs no entry of its own.
RESTRICTED_KPIS = {
	# Finance (revenue_30 is also the Executive rollup's)
	"revenue_30": "Revenue (30d)",
	"revenue_mtd": "Revenue (MTD)",
	# Receipts for the period. Not revenue by the books, but the owner counts it with
	# revenue for this rule (2026-11-19).
	"cash_collected_30": "Cash Collected (30d)",
	# Product
	"catalog_revenue_30": "Catalog Revenue (30d)",
	"catalog_revenue_365": "Catalog Revenue (1y)",
	"rental_booked_revenue_90": "Rental Revenue Booked (90d)",
	"rental_revenue_per_fountain_90": "Rental Revenue per Fountain (90d)",
	"gross_margin_pct": "Gross Margin (1y)",
	# Executive
	"revenue_per_employee": "Revenue per Employee (30d)",
}

#: kpi_key -> why it is not a revenue or margin figure, for keys whose name, label or unit
#: reads like money. These are shown to every role that may open the department.
REVIEWED_UNRESTRICTED = {
	# Finance
	"ar_outstanding": "Amount owed to us; a balance, not revenue for a period.",
	"ar_overdue": "The overdue part of receivables; a balance, not revenue.",
	"ap_outstanding": "Amount we owe; a payable balance.",
	"dso": "A duration in days; it states no amount.",
	"overdue_invoices": "A count of invoices.",
	# Operations
	"store_run_spend_30": "Spend on store runs; a cost, not revenue or margin.",
	"inventory_stock_value": "Value of stock on hand; an asset balance.",
	"placeholder_cost_stock_lines": "A count of stock lines; no cost amount is shown.",
	"unpriced_po_lines_90": "A count of purchase order lines.",
	# Sales
	"open_pipeline_value": "Value of open opportunities; pipeline, not revenue.",
	"won_value_30": "Value of deals won; bookings, not invoiced revenue.",
	"avg_deal_size": "Average value of a won deal; bookings, not revenue.",
	"handoff_backlog": "A count of won deals waiting for a project.",
	# Production (backlog_value is also the Executive rollup's)
	"backlog_value": "Value of open projects still to deliver; backlog, not revenue.",
	"build_backlog_value": "Value of open builds still to deliver; backlog, not revenue.",
	"change_order_net_value": "Net value of change orders; contract changes, not revenue.",
	"labor_budget_utilization": "A ratio of hours used to hours budgeted.",
	# Marketing
	"marketing_pipeline": "Value of sourced opportunities; pipeline, not revenue.",
	"marketing_won_30": "A count of sourced wins.",
	"marketing_spend_mtd": "Marketing spend; a cost, not revenue or margin.",
	"cpl_mtd": "Spend per lead; a cost, not revenue or margin.",
	# Product
	"rental_backlog_value": "Value of open event projects still to deliver; backlog, not revenue.",
}


def can_see_revenue(roles=None):
	"""True when the session user (or the given roles) may be given revenue and margin figures."""
	if roles is None:
		import frappe

		roles = frappe.get_roles()
	return bool(REVENUE_ROLES & set(roles))


def is_restricted_kpi(kpi_key):
	return kpi_key in RESTRICTED_KPIS


def without_revenue(rows, key="kpi_key", roles=None):
	"""``rows`` with every revenue and margin figure removed, unless the caller may see them.

	``rows`` are dicts or documents carrying a ``kpi_key``. The input is never changed;
	a caller who may see them gets the same list back.
	"""
	rows = list(rows or [])
	if can_see_revenue(roles):
		return rows
	return [row for row in rows if not is_restricted_kpi(_key_of(row, key))]


def _key_of(row, key):
	if isinstance(row, dict):
		return row.get(key)
	return getattr(row, key, None)

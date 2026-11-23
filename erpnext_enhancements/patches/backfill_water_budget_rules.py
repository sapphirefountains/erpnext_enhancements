"""Give the **existing** Water Engineering Settings row the budget rules' defaults (v1.770.0).

Phase G of the Fountain Designer adds three house rules to Water Engineering Settings, a Single:
``labor_rate_per_hr`` (0 = not set), ``budget_contingency_pct`` (10) and ``budget_range_pct`` (15).
A ``default`` on a new field of a Single never reaches the row that already exists, so without
this the Settings page would show a 0 % contingency and the first save would store it.

``backfill_water_house_rules`` (v1.759.0) already does exactly this for every declared default
that has no ``tabSingles`` row, and never over a stored value. A patch runs once, so its
registration cannot pick up fields added later: this one runs the same function again.
"""

import frappe

from erpnext_enhancements.patches.backfill_water_house_rules import backfill_water_house_rules


def execute() -> None:
	# A raising patch aborts bench migrate, which is the deploy (CLAUDE.md): log and carry on.
	try:
		backfill_water_house_rules()
	except Exception:
		frappe.log_error(title="Water budget rules backfill failed", message=frappe.get_traceback())

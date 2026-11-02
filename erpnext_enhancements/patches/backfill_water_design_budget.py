"""Give every existing Water Feature Design the v1.770.0 budget figures (Fountain Designer phase G).

``budget_total`` changed meaning in v1.770.0: it was the bill of materials' sum and is now the Budget the
prints show, that sum plus the house-rule contingency, with the sum moving to the new ``bom_subtotal``.
A design takes the new meaning on its next save; until then the Start list and anything summing the
field would mix the two. This writes both figures from each design's stored ``bom`` rows and the current
budget rules, through ``prints.budget_summary``, the function the derive, the controller and the prints use.

``db.set_value`` with ``update_modified=False``: nothing else on the record changes and no controller
runs. A canceled design is left as it was. Logs and carries on per design; never raises (a raising patch
aborts ``bench migrate``, which is the deploy). Safe twice: the second run writes the same figures.
"""

import frappe

DESIGN_DOCTYPE = "Water Feature Design"
LINE_DOCTYPE = "Water Feature Design Line"


def execute() -> None:
	try:
		backfill_water_design_budget()
	except Exception:
		frappe.log_error(title="Water design budget backfill failed", message=frappe.get_traceback())


def backfill_water_design_budget() -> int:
	"""Write ``bom_subtotal`` and ``budget_total`` on every design that is not canceled. Returns how many."""
	if not frappe.db.exists("DocType", DESIGN_DOCTYPE) or not frappe.get_meta(DESIGN_DOCTYPE).has_field("bom_subtotal"):
		return 0

	from erpnext_enhancements.water_engineering import prints
	from erpnext_enhancements.water_engineering.api.designer import load_rules
	from erpnext_enhancements.water_engineering.derive import budget_rules

	rules = budget_rules(load_rules())
	written = 0
	for name in frappe.get_all(DESIGN_DOCTYPE, filters={"docstatus": ["<", 2]}, pluck="name"):
		try:
			rows = frappe.get_all(
				LINE_DOCTYPE,
				filters={"parent": name, "parenttype": DESIGN_DOCTYPE},
				fields=["section", "line_key", "item", "qty", "rate", "amount", "source"],
			)
			summary = prints.budget_summary(rows, rules)
			frappe.db.set_value(
				DESIGN_DOCTYPE,
				name,
				{"bom_subtotal": summary["subtotal"], "budget_total": summary["total"]},
				update_modified=False,
			)
			written += 1
		except Exception:
			frappe.log_error(title=f"Water design budget backfill {name}", message=frappe.get_traceback())
	return written

"""Put open Opportunities on their stage's default probability (form kit P5.5).

Every open deal reads 100%, ERPNext's field default, so the forecast counted each one as won.
The dry run Nik approved (TASK-2026-02772, 2026-10-13) applies one rule, and this patch runs
exactly that rule against the deals as they stand when it runs:

* a deal **in a stage with a default** (Qualification, Needs Analysis, Proposal/Price Quote,
  Negotiation/Review) at **exactly 100** takes the stage's default. 100 is the field default,
  and the version history showed no deal at 100 that anyone had typed (37 deals on prod);
* a deal in one of those stages **or On Hold** at **any other value** was given that number by
  someone (or by the Zoho import), so it is marked *Probability Set by Hand* and kept, and a
  later stage change keeps it too (2 open deals and 8 on hold);
* every other deal is left as it is: On Hold at 100 (out of the forecast, and it takes its
  stage's default when it leaves On Hold), Closed Won, Lost, and anything submitted.

Written with `set_value(update_modified=False)`: no controller, so no hook can refuse a deal
half-way, and `modified` keeps its meaning. Needs the field from
`add_opportunity_probability_by_hand` and the rows from `seed_opportunity_stage_probabilities`,
which run before it; with no table rows it changes nothing.

Cannot raise; safe twice (a second run finds no deal at 100 in those stages, and the marked
ones already marked).
"""

import frappe
from frappe.utils import flt

from erpnext_enhancements.crm_enhancements.opportunity_probability import (
	ERPNEXT_DEFAULT,
	FIELD,
	stage_defaults,
)

#: Out of the forecast, so given no default, but a hand-set number on it is still kept.
ON_HOLD = "On Hold"


def execute():
	try:
		counts = backfill()
		print(
			"Opportunity probability: {moved} moved to their stage default, {marked} marked set by hand".format(
				**counts
			)
		)
	except Exception:
		try:
			frappe.log_error(title="Opportunity probability backfill failed", message=frappe.get_traceback())
		except Exception:
			pass


def backfill():
	counts = {"moved": 0, "marked": 0}
	defaults = stage_defaults()
	if not defaults:
		return counts
	can_mark = frappe.db.has_column("Opportunity", FIELD)
	stages = [*defaults, ON_HOLD]
	deals = frappe.get_all(
		"Opportunity",
		filters={"docstatus": 0, "status": ["in", stages]},
		fields=["name", "status", "probability"],
		limit_page_length=0,
	)
	for deal in deals:
		at_default = abs(flt(deal.probability) - ERPNEXT_DEFAULT) < 1e-6
		if at_default and deal.status in defaults:
			frappe.db.set_value(
				"Opportunity", deal.name, "probability", defaults[deal.status], update_modified=False
			)
			counts["moved"] += 1
		elif not at_default and can_mark:
			frappe.db.set_value("Opportunity", deal.name, FIELD, 1, update_modified=False)
			counts["marked"] += 1
	frappe.db.commit()
	return counts

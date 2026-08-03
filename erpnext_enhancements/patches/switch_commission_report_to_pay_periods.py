"""Move the commission report onto semi-monthly pay periods (v1.232.0).

Two site records have to change the moment the code lands, and neither can wait
for the first scheduler tick:

1. **The weekly Monday email must stop.** "Brian Commissions Report" is a
   Weekly/Monday Auto Email Report that Frappe's own ``send_weekly`` fires. The
   new job in ``crm_enhancements.pay_period_reports`` drives that same doc on the
   1st and the 16th, so leaving it enabled means billing gets both — a duplicate
   commission statement on a schedule nobody asked for. Disabling it is what
   takes Frappe's scheduler off the doc; the doc itself is still very much in
   use, for its recipient list, description and rendering. **Do not delete it.**

2. **The hard-coded date window must go.** "Brian's Closed Won" carried
   ``custom_date_closed_won > 2026-07-16``, retyped by hand each period. Left
   alone until 07:00 the next morning, anyone opening the report between the
   deploy and that tick still sees the stale window — and the `>` in it excludes
   deals closed *on* the boundary day, which is how a $500 opportunity dated
   2026-07-16 came to be in neither period. Applying the running period here
   makes the report correct as migrate finishes.

Idempotent: re-running disables an already-disabled doc and rewrites an
identical window, both no-ops.
"""

import frappe
from frappe.utils import getdate, today

from erpnext_enhancements.crm_enhancements.pay_period_reports import (
	AUTO_EMAIL_REPORT_NAME,
	REPORT_NAME,
	_apply_window,
	pay_period_bounds,
)


def execute():
	if frappe.db.exists("Auto Email Report", AUTO_EMAIL_REPORT_NAME):
		frappe.db.set_value("Auto Email Report", AUTO_EMAIL_REPORT_NAME, "enabled", 0)

	if frappe.db.exists("Report", REPORT_NAME):
		_apply_window(*pay_period_bounds(getdate(today())))

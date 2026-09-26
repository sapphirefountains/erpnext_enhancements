"""Force the Labor Cost Analysis report to re-sync, so its narrowed role list actually lands.

v1.542.0 drops Projects Manager from ``workforce/report/labor_cost_analysis/labor_cost_analysis.json``,
leaving System Manager, HR Manager and Accounts Manager: the three roles that read Job Interval
at permlevel 1, ADR 0013's pay audience. The report prints per-employee pay and burdened rates
from raw SQL, and a raw query applies no permlevel, so the Report's own role list is the only
thing standing between those rates and everyone who holds the role.

A standard Report JSON is age-gated on the way in: ``bench migrate`` hands it to
``import_file``, which compares the file's ``modified`` against the row already in the
database and silently skips the file when the row is not older. The JSON carries a bumped
stamp, and this patch is the other half, the half a future edit that forgets the bump cannot
undo, exactly as ``reload_location_timeline_page`` was for the Location Timeline page (whose
deploy skipped the file and left the old roles in place without an error anywhere).

``import_doc`` keeps ``disabled``, ``prepared_report`` and ``add_total_row`` from the site row
(frappe's ``ignore_values`` for Report) and replaces the ``Has Role`` child table wholesale, so
a site that toggled prepared reporting keeps it and the role list becomes exactly the file's.
Patches run after model sync, so a second run re-imports the same file and changes nothing.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A report's
	# role list is not worth a half-finished deploy, so every failure here is quiet.
	try:
		frappe.reload_doc("workforce", "report", "labor_cost_analysis", force=True)
		print("reload_labor_cost_analysis_report: Labor Cost Analysis re-synced")
	except Exception:
		frappe.log_error(
			title="Report reload failed: Labor Cost Analysis",
			message=frappe.get_traceback(),
		)
	try:
		frappe.clear_cache()
	except Exception:
		pass

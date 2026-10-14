"""Put back the real won date on eleven deals a re-import dated 2026-06-30 (v1.628.0, WI-024).

Until v1.628.0 ``script_migrations.opportunity.stamp_won_date`` stamped today on ANY save of a
Closed-Won deal whose ``custom_date_closed_won`` was blank, not only on the transition into
Closed Won. A spreadsheet re-import on 2026-06-30 re-saved twelve long-won deals and every one
of them came out "won 06-30-2026". The hook is transition-only now; this restores the dates.

Each date below is the deal's own single status change into Closed Won in ``tabVersion``,
made by a person on an ordinary day (verified on production 2026-10-13), with one exception:

* **CRM-OPP-2025-00505** -> 2025-03-19, from the Zoho export (Stage "Closed Won", Closing Date
  2025-03-19). Its ERPNext status change on 2025-10-29 only replaced the migration's "Closed"
  placeholder, so the audit trail's date is the cleanup, not the win.
* **CRM-OPP-2026-00123** really was won on 2026-06-30. It is listed so the reader can see it
  was considered, and is reported as skipped, never written.

**Only where the current value is still 2026-06-30.** A date somebody corrected by hand since
is newer information than this table and is never overwritten. ``update_modified=False``:
``modified`` feeds the stalled-deal KPI and the win rate's Lost leg, and a data repair must not
make twelve old deals look touched today.

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the deploy.
Each deal is guarded on its own and the tally (done + skipped + failed) always sums to the
number of deals in the table. Safe twice: the second run finds nothing still on 2026-06-30.
"""

import frappe

FIELD = "custom_date_closed_won"
WRONG_DATE = "2026-06-30"

#: Opportunity -> the date it was actually won. ``None`` means "considered and correct as is".
RESTORE = {
	"CRM-OPP-2025-00183": "2025-09-26",
	"CRM-OPP-2025-00403": "2025-10-10",
	"CRM-OPP-2025-00505": "2025-03-19",  # Zoho export Closing Date; see the docstring
	"CRM-OPP-2025-00559": "2025-10-10",
	"CRM-OPP-2025-00593": "2025-09-24",
	"CRM-OPP-2025-00604": "2025-08-27",
	"CRM-OPP-2025-00625": "2025-09-24",
	"CRM-OPP-2025-00654": "2025-11-21",
	"CRM-OPP-2025-00662": "2025-12-02",
	"CRM-OPP-2026-00001": "2026-02-27",
	"CRM-OPP-2026-00083": "2026-04-27",
	"CRM-OPP-2026-00123": None,  # really won 2026-06-30: a documented no-op
}


def execute():
	tally = {"done": 0, "skipped": 0, "failed": 0}
	try:
		has_field = frappe.db.has_column("Opportunity", FIELD)
	except Exception:
		has_field = False
	if not has_field:
		tally["skipped"] = len(RESTORE)
		print(f"restore_opportunity_won_dates: Opportunity.{FIELD} does not exist; {tally}")
		return tally

	for name, won_on in RESTORE.items():
		try:
			if won_on is None:
				tally["skipped"] += 1
				continue
			current = frappe.db.get_value("Opportunity", name, FIELD)
			# str(): a Date column reads back as datetime.date.
			if current is None or str(current)[:10] != WRONG_DATE:
				# Missing deal, blank date, or a date somebody has corrected since.
				tally["skipped"] += 1
				continue
			frappe.db.set_value("Opportunity", name, FIELD, won_on, update_modified=False)
			tally["done"] += 1
		except Exception:
			tally["failed"] += 1
			frappe.log_error(
				title="Won-date restore failed",
				message=f"Opportunity {name}: {frappe.get_traceback()}",
				reference_doctype="Opportunity",
				reference_name=name,
			)

	print(
		f"restore_opportunity_won_dates: {tally['done']} restored, {tally['skipped']} skipped, "
		f"{tally['failed']} failed, of {len(RESTORE)}."
	)
	return tally

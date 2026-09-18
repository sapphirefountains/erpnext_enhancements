import frappe
from frappe import _

def submitted_timesheet_for(job_interval_name):
	"""The submitted Timesheet name whose detail row links this interval, or None."""
	try:
		if not frappe.db.has_column("Timesheet Detail", "custom_job_interval"):
			return None
	except Exception:
		return None

	row = frappe.db.get_value(
		"Timesheet Detail",
		{"custom_job_interval": job_interval_name, "parenttype": "Timesheet"},
		["parent", "docstatus"],
		as_dict=True,
	)
	if row and int(row.docstatus or 0) == 1:
		return row.parent
	return None

def assert_not_locked(doc):
	"""frappe.throw when the interval's Timesheet is already submitted."""
	if not doc.name:
		return

	ts_name = submitted_timesheet_for(doc.name)
	if ts_name:
		frappe.throw(
			_(
				"The hours for {0} are already on submitted Timesheet {1}. Cancel or amend that Timesheet first."
			).format(doc.name, ts_name),
			title=_("Timesheet already submitted"),
		)

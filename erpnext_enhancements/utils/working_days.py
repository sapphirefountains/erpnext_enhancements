"""Business-day (working-day) date arithmetic for the PRO-0204 hand-off SLAs.

Counts Monday-Friday only, skipping weekends and any dates on a configured
Holiday List. Used by the process-step engine
(:func:`erpnext_enhancements.process_steps._refresh_due`) so a 2-day SLA set on a
Friday lands on the following Tuesday, not Sunday.

No new dependency: the holidays are read from the standard ERPNext "Holiday List"
(always present in this app's stack), loaded once per call into a set, so the
weekday/holiday checks are local and cheap. A missing/misconfigured list degrades
to weekend-only skipping rather than ever breaking a save.
"""

import frappe
from frappe.utils import add_to_date, get_datetime, getdate


def add_working_days(start_dt, n_days, holiday_list=None):
	"""Return the datetime ``n_days`` business days after ``start_dt``.

	Business days are Mon-Fri excluding any date in ``holiday_list``. The
	time-of-day of ``start_dt`` is preserved. ``n_days <= 0`` returns ``start_dt``
	unchanged (the caller treats "no SLA" as "no due date").

	Args:
		start_dt: Datetime (or parseable string) to count from.
		n_days: Number of business days to add.
		holiday_list: Optional Holiday List name. When given, its holidays are
			skipped in addition to weekends; when ``None``/blank, only weekends skip.

	Returns:
		datetime: ``start_dt`` advanced by ``n_days`` business days.
	"""
	dt = get_datetime(start_dt)
	n_days = int(n_days or 0)
	if n_days <= 0:
		return dt

	is_holiday = _holiday_checker(holiday_list)
	remaining = n_days
	while remaining > 0:
		dt = add_to_date(dt, days=1)
		if dt.weekday() >= 5:  # Saturday=5, Sunday=6
			continue
		if is_holiday(dt):
			continue
		remaining -= 1
	return dt


def _holiday_checker(holiday_list):
	"""Return ``(datetime) -> bool`` reporting Holiday-List membership by date.

	Loads the list's holiday dates once. A blank list (or any load failure)
	yields a checker that's always False, i.e. weekend-only skipping.
	"""
	if not holiday_list:
		return lambda dt: False

	try:
		holidays = {
			getdate(row.holiday_date)
			for row in frappe.get_all(
				"Holiday",
				filters={"parent": holiday_list, "parenttype": "Holiday List"},
				fields=["holiday_date"],
			)
		}
	except Exception:
		# A missing/misconfigured Holiday List must never break due-date math.
		frappe.log_error(frappe.get_traceback(), "add_working_days: holiday list load failed")
		return lambda dt: False

	return lambda dt: getdate(dt) in holidays

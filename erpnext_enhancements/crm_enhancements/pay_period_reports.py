"""Semi-monthly pay-period delivery of the sales commission report.

Commission is paid on semi-monthly pay periods — the **1st–15th** and the
**16th–end of month** — and the "Brian Commissions Report" Auto Email Report has
said so in its own description since it was written:

	*Reports received on the 16th: covers the 1st through the 15th of the current
	month. Reports received on the 1st: covers the 16th through the end of the
	previous month.*

Nothing implemented it. ``Brian's Closed Won`` is a **Report Builder** report
whose saved filters carried a hard-coded ``custom_date_closed_won > 2026-07-16``,
retyped by hand each period, and the email went out weekly on Mondays. This
module is the missing half: it moves that date window on its own and sends on
the two days the description promises.

Why a job in this app rather than configuration on the Auto Email Report:

* Frappe's dynamic date filters (``from_date_field`` / ``to_date_field`` /
  ``dynamic_date_period``) are applied **only when the report type is not Report
  Builder** — ``AutoEmailReport.get_report_content`` guards them with
  ``if self.report_type != "Report Builder"``. They are inert here.
* Auto Email Report's own ``filters`` are *appended* to the report's saved
  filters, never substituted (``Report.run_standard_report``), so the email
  cannot override or widen a date already baked into the report.
* ``frequency`` offers only Daily / Weekdays / Weekly / Monthly, and
  ``dynamic_date_period`` only Daily…Yearly. Neither vocabulary can express
  "twice a month, on the 1st and the 16th".

So the window has to be written into the report's saved ``json``, which is also
what makes the desk view correct: opening the report shows **the pay period
currently in progress**, because that is the filter it now carries.

The email needs the *finished* period while the desk view wants the *running*
one, and a Report Builder report can only hold one filter set. The send
therefore borrows the report's window for the length of one
``get_report_content()`` call and :func:`run_pay_period_cycle` re-applies the
running window immediately afterwards — unconditionally, so a send that raises
part-way cannot strand the report on a stale window, and so a missed scheduler
run self-heals on the next day's tick rather than waiting for the next 1st.

Rendering goes through the existing Auto Email Report doc on purpose. It owns
the recipient list (editable in the desk by whoever owns billing, with no code
change), the letterhead-styled table and the description above, so recipients
keep receiving exactly the email they receive today — only with the right rows
in it. That doc is left **disabled**, which is what stops Frappe's own weekly
Monday scheduler from also firing it; it is still very much in use. Do not
delete it.
"""

import json

import frappe
from frappe.utils import add_days, formatdate, get_last_day, getdate, today

#: The Report Builder report whose saved date window this module owns.
REPORT_NAME = "Brian's Closed Won"

#: Drives rendering and recipients. Deliberately kept disabled — see module docstring.
AUTO_EMAIL_REPORT_NAME = "Brian Commissions Report"

#: ``ref_doctype`` of the report. Report Builder filter rows are
#: ``[doctype, fieldname, operator, value]``, so the doctype is part of the row.
REF_DOCTYPE = "Opportunity"

#: The date the pay period is measured on — when the deal was marked won, which
#: is a manually stamped field and NOT ``modified``/``creation``.
DATE_FIELD = "custom_date_closed_won"

#: Days of the month a period closes and its report goes out.
SEND_DAYS = (1, 16)

#: DefaultValue key holding the end date of the last period actually emailed.
#: Guards against a double send if the job is re-run by hand or the scheduler
#: retries — two commission statements for one period is a payroll conversation.
LAST_SENT_KEY = "brians_closed_won_last_pay_period_sent"


# ------------------------------------------------------------------ pay periods
# Pure date arithmetic, no frappe state — the part worth unit-testing, and the
# part any other semi-monthly job (WI-017's payroll hours export) should reuse
# rather than re-derive.


def pay_period_bounds(day):
	"""``(start, end)`` of the semi-monthly pay period containing ``day``.

	1st–15th, or 16th–last day of the month. Both ends inclusive.
	"""
	day = getdate(day)
	if day.day <= 15:
		return day.replace(day=1), day.replace(day=15)
	return day.replace(day=16), getdate(get_last_day(day))


def previous_pay_period(day):
	"""``(start, end)`` of the pay period that ended immediately before ``day``'s.

	The day before a period's start is always the last day of the one before it,
	which carries the month/year rollover for free: 1 Jan → 16–31 Dec.
	"""
	start, _end = pay_period_bounds(day)
	return pay_period_bounds(add_days(start, -1))


# --------------------------------------------------------------- report window


def _date_window_filter(start, end):
	"""The one filter row expressing ``start``–``end``, inclusive at both ends.

	``between`` and not ``>=``/``<=`` as two rows: it is a single row the desk
	filter area renders as a date range, and it cannot drift out of agreement
	with itself. The window it replaced used ``>`` against the period's first
	day, which silently dropped every deal closed *on* a 1st or a 16th.
	"""
	return [REF_DOCTYPE, DATE_FIELD, "between", [str(getdate(start)), str(getdate(end))]]


def _apply_window(start, end):
	"""Point the report's saved filters at ``start``–``end``. True if it changed.

	Replaces only the ``DATE_FIELD`` rows and leaves every other saved filter
	(the owner and the Closed Won status) exactly as found, so tightening the
	report in the desk survives the next tick instead of being stomped by it.

	Written with ``update_modified=False``: the window moves on a schedule, not
	because a person edited the report, and bumping ``modified`` would hand a
	TimestampMismatchError to anyone who happened to have the report open.
	"""
	saved = frappe.db.get_value("Report", REPORT_NAME, "json")
	if not saved:
		return False

	try:
		spec = json.loads(saved)
	except ValueError:
		frappe.log_error(f"Unparseable json on Report {REPORT_NAME}", "Pay-period report window")
		return False

	kept = [row for row in (spec.get("filters") or []) if not (len(row) > 1 and row[1] == DATE_FIELD)]
	spec["filters"] = [_date_window_filter(start, end), *kept]

	updated = json.dumps(spec)
	if updated == saved:
		return False

	frappe.db.set_value("Report", REPORT_NAME, "json", updated, update_modified=False)
	frappe.clear_document_cache("Report", REPORT_NAME)
	return True


# ---------------------------------------------------------------------- sending


def _already_sent(period_end):
	return frappe.db.get_default(LAST_SENT_KEY) == str(getdate(period_end))


def _mark_sent(period_end):
	frappe.db.set_default(LAST_SENT_KEY, str(getdate(period_end)))


def _send_period(start, end):
	"""Email the report for the closed period ``start``–``end``.

	Borrows the report's saved window to render, because ``get_report_content``
	re-reads the Report doc from the database and cannot be handed filters that
	*narrow* an existing one. The caller re-applies the running window
	immediately afterwards, on every path — see :func:`run_pay_period_cycle`.
	"""
	_apply_window(start, end)

	auto_email_report = frappe.get_doc("Auto Email Report", AUTO_EMAIL_REPORT_NAME)
	recipients = [address for address in (auto_email_report.email_to or "").split() if address]
	if not recipients:
		frappe.log_error(
			f"{AUTO_EMAIL_REPORT_NAME} has no recipients; nothing sent",
			"Pay-period commission report",
		)
		return False

	# Returns falsy when the period is empty AND the doc asks to be quiet about
	# it (`send_if_data`), so that setting keeps working from here.
	content = auto_email_report.get_report_content()
	if not content:
		return False

	frappe.sendmail(
		recipients=recipients,
		subject=f"{AUTO_EMAIL_REPORT_NAME} — {formatdate(start)} to {formatdate(end)}",
		message=content,
		reference_doctype="Report",
		reference_name=REPORT_NAME,
	)
	return True


# ------------------------------------------------------------------ entry point


def run_pay_period_cycle():
	"""Daily tick: send on a period boundary, then park the window on the running period.

	Wired to cron in ``hooks.py``. Runs every day rather than only on the 1st and
	16th so that the desk window is repaired the next morning if a tick is missed
	or a send dies half-way — the alternative strands the report until the next
	period boundary, up to sixteen days later.
	"""
	if not frappe.db.exists("Report", REPORT_NAME):
		return
	if not frappe.db.exists("Auto Email Report", AUTO_EMAIL_REPORT_NAME):
		return

	current = getdate(today())

	if current.day in SEND_DAYS:
		start, end = previous_pay_period(current)
		if not _already_sent(end):
			try:
				if _send_period(start, end):
					_mark_sent(end)
			except Exception:
				# Never let a failed send abort the run — the window restore
				# below is what keeps the desk report truthful, and it matters
				# more than this one email.
				frappe.log_error(frappe.get_traceback(), "Pay-period commission report failed")

	_apply_window(*pay_period_bounds(current))

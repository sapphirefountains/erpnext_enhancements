# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The three progress-billing reports (estimating B9 follow-up), as arithmetic. No frappe.

Each report's ``execute`` reads its documents through ``frappe.get_list`` (so a person sees only the
schedules and applications they may read) and hands plain dicts to the functions here, which
``tests/test_billing_reports.py`` runs without a bench. Every sum is in whole cents
(``pay_app_math.cents``), so a total row never drifts from the documents it adds up.

* :func:`retainage_rows`: Retainage Held, one row per schedule of values.
* :func:`outstanding_rows`: Pay Applications Outstanding, the applications still open and the locked
  jobs whose last application is older than the reminder window.
* :func:`billed_rows`: Billed vs Scheduled, a schedule's lines grouped by phase (or one per line), or
  one row per schedule when none is picked.

**Total rows are computed here, from cents**, never by frappe: at v16.60.2 both the on-screen total
(``report_column_total``) and the exported one (``query_report.add_total_row``) take a plain mean of a
Percent column, so held as a percent of billed across three jobs would be the average of three
percentages instead of total held over total billed. The reports set ``add_total_row`` off and
append :func:`retainage_total` / :func:`billed_total`.

**Only permlevel 0 fields are read**, all of which every role that can open these reports may read:
the reports run server-side, where this app's field-level read checks (v1.835.0) do not reach, so
the rule is kept here instead and ``tests/test_billing_reports.py`` holds every column to it.
"""

import datetime

from erpnext_enhancements.estimating import pay_app_math as pm
from erpnext_enhancements.estimating import rules

#: Days after a certified application's period ends before the job counts as due for the next one.
DEFAULT_DUE_AFTER_DAYS = 30


def _date(value):
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def latest_by_schedule(applications):
	"""The highest-numbered application of each schedule, from ``applications`` (dicts carrying
	``schedule_of_values`` and ``application_no``)."""
	out = {}
	for app in applications or []:
		key = app.get("schedule_of_values")
		if key not in out or int(app.get("application_no") or 0) > int(out[key].get("application_no") or 0):
			out[key] = app
	return out


# ----------------------------------------------------------------------------- Retainage Held


def retainage_rows(schedules, certified, holding_only=True):
	"""One row per schedule: what it holds and has released, from its latest certified application.

	``schedules`` carry ``name``, ``project``, ``customer``, ``status``, ``contract_sum_to_date``,
	``billed_to_date``, ``retainage_held`` and ``retainage_released``; ``certified`` are the certified
	applications (``name``, ``schedule_of_values``, ``application_no``, ``period_to``,
	``application_type``). With ``holding_only``, a schedule holding nothing is left out. Sorted by
	retainage held, most first, then by name.
	"""
	latest = latest_by_schedule(certified)
	rows = []
	for sov in schedules or []:
		held = pm.cents(sov.get("retainage_held"))
		if holding_only and not held:
			continue
		app = latest.get(sov.get("name")) or {}
		billed = pm.cents(sov.get("billed_to_date"))
		rows.append(
			{
				"schedule_of_values": sov.get("name"),
				"project": sov.get("project"),
				"customer": sov.get("customer"),
				"status": sov.get("status"),
				"contract_sum_to_date": pm.dollars(pm.cents(sov.get("contract_sum_to_date"))),
				"billed_to_date": pm.dollars(billed),
				"retainage_held": pm.dollars(held),
				"retainage_released": pm.dollars(pm.cents(sov.get("retainage_released"))),
				"held_percent_of_billed": pm.percent(held, billed),
				"latest_application": app.get("name"),
				"latest_period_to": _date(app.get("period_to")),
				"latest_type": app.get("application_type"),
			}
		)
	rows.sort(key=lambda r: (-pm.cents(r["retainage_held"]), r["schedule_of_values"] or ""))
	return rows


def retainage_total(rows):
	"""The total row of Retainage Held: every money column summed in cents, and held as a percent of
	billed taken from those sums, not averaged."""
	held = sum(pm.cents(r.get("retainage_held")) for r in rows or [])
	billed = sum(pm.cents(r.get("billed_to_date")) for r in rows or [])
	return {
		"status": "Total",
		"contract_sum_to_date": pm.dollars(sum(pm.cents(r.get("contract_sum_to_date")) for r in rows or [])),
		"billed_to_date": pm.dollars(billed),
		"retainage_held": pm.dollars(held),
		"retainage_released": pm.dollars(sum(pm.cents(r.get("retainage_released")) for r in rows or [])),
		"held_percent_of_billed": pm.percent(held, billed),
	}


# -------------------------------------------------------------------- Pay Applications Outstanding


def outstanding_rows(schedules, applications, today, due_after_days=DEFAULT_DUE_AFTER_DAYS):
	"""Applications still open, and locked jobs due for their next application.

	``schedules`` carry ``name``, ``project``, ``customer``, ``status``, ``billing_mode`` and
	``locked_on``; ``applications`` are every
	application of those schedules that is not canceled (``name``, ``schedule_of_values``,
	``application_no``, ``docstatus``, ``status``, ``application_type``, ``period_to``,
	``current_payment_due``, ``sent_for_review_on``). A row is either:

	* **an open application** (Draft, or To Review with accounting), with the days since its period
	  ended; or
	* **a locked schedule with nothing open** whose latest certified application's period ended more
	  than ``due_after_days`` ago ("Due"), or that has no application at all ("Not started", aged from
	  the day it was locked). A closed schedule is billed out and never due.

	A job billed **by milestones** is never "Due" by the calendar: between milestones there is nothing
	to bill, and the next application waits for the next milestone, not for a month to pass. It is
	listed only with an application open, or as "Not started".

	Sorted with the longest-waiting first.
	"""
	today = _date(today)
	by_name = {s.get("name"): s for s in schedules or []}
	open_apps = [a for a in applications or [] if a.get("docstatus") == 0]
	certified = [a for a in applications or [] if a.get("docstatus") == 1]
	has_open = {a.get("schedule_of_values") for a in open_apps}
	latest = latest_by_schedule(certified)
	rows = []

	def days_since(value):
		day = _date(value)
		return (today - day).days if day and today else None

	for app in open_apps:
		sov = by_name.get(app.get("schedule_of_values"))
		if sov is None:
			continue
		rows.append(
			{
				"state": app.get("status") or "Draft",
				"schedule_of_values": sov.get("name"),
				"project": sov.get("project"),
				"customer": sov.get("customer"),
				"pay_application": app.get("name"),
				"application_no": app.get("application_no"),
				"application_type": app.get("application_type") or pm.PROGRESS,
				"period_to": _date(app.get("period_to")),
				"days_waiting": days_since(app.get("period_to")),
				"current_payment_due": pm.dollars(pm.cents(app.get("current_payment_due"))),
				"sent_for_review_on": app.get("sent_for_review_on"),
			}
		)
	for sov in schedules or []:
		if sov.get("status") != "Locked" or sov.get("name") in has_open:
			continue
		last = latest.get(sov.get("name"))
		if last is None:
			state, waiting = "Not started", days_since(sov.get("locked_on"))
		elif sov.get("billing_mode") == "Milestones":
			continue
		else:
			waiting = days_since(last.get("period_to"))
			if waiting is None or waiting <= due_after_days:
				continue
			state = "Due"
		rows.append(
			{
				"state": state,
				"schedule_of_values": sov.get("name"),
				"project": sov.get("project"),
				"customer": sov.get("customer"),
				"pay_application": last.get("name") if last else None,
				"application_no": last.get("application_no") if last else None,
				"application_type": last.get("application_type") if last else None,
				"period_to": _date(last.get("period_to")) if last else None,
				"days_waiting": waiting,
				"current_payment_due": None,
				"sent_for_review_on": None,
			}
		)
	rows.sort(
		key=lambda r: (
			-(r["days_waiting"] if r["days_waiting"] is not None else -1),
			r["schedule_of_values"] or "",
		)
	)
	return rows


# --------------------------------------------------------------------------- Billed vs Scheduled


def group_of(line):
	"""The billing group of a schedule line: change orders together, then each top phase (a design
	stage under its hundred, as in the pay application editor), 0 for lines with no phase."""
	if line.get("change_order"):
		return "co"
	number = int(float(line.get("phase_number") or 0))
	return number - number % 100 if number >= 100 else 0


def group_label(group):
	if group == "co":
		return "Change orders"
	if not group:
		return "Lines"
	return f"{group} {rules.phase_label(group)}"


def _billed_row(label, scheduled, billed, **extra):
	row = {
		"label": label,
		"scheduled_value": pm.dollars(scheduled),
		"billed_to_date": pm.dollars(billed),
		"percent_billed": pm.percent(billed, scheduled),
		"balance": pm.dollars(scheduled - billed),
	}
	row.update(extra)
	return row


def billed_rows(lines, group_by="Phase"):
	"""One schedule's lines, billed to date against scheduled value.

	``lines`` carry ``idx``, ``item_no``, ``description``, ``phase_number``, ``scheduled_value``,
	``billed_to_date``, ``by_others`` and ``change_order``. ``group_by`` is ``"Phase"`` (one row per top
	phase, change orders last) or ``"Line"`` (one row per line, in schedule order). A line by others is
	worth nothing and billed nothing: it is listed on a per-line run, never counted in a group.
	"""
	ordered = sorted(lines or [], key=lambda l: int(l.get("idx") or 0))
	if group_by == "Line":
		return [
			_billed_row(
				f"{line.get('item_no') or ''} {line.get('description') or ''}".strip(),
				pm.cents(line.get("scheduled_value")),
				pm.cents(line.get("billed_to_date")),
				group=group_label(group_of(line)),
				by_others=1 if line.get("by_others") else 0,
			)
			for line in ordered
		]
	groups = {}
	for line in ordered:
		if line.get("by_others"):
			continue
		g = group_of(line)
		scheduled, billed = groups.get(g, (0, 0))
		groups[g] = (
			scheduled + pm.cents(line.get("scheduled_value")),
			billed + pm.cents(line.get("billed_to_date")),
		)
	keys = sorted(groups, key=lambda g: (g == "co", 0 if g == "co" else g))
	return [_billed_row(group_label(g), *groups[g], group=group_label(g), by_others=0) for g in keys]


def schedule_summary_rows(schedules):
	"""One row per schedule: its contract sum to date against what has been billed."""
	rows = [
		_billed_row(
			sov.get("title") or sov.get("project") or sov.get("name"),
			pm.cents(sov.get("contract_sum_to_date")),
			pm.cents(sov.get("billed_to_date")),
			schedule_of_values=sov.get("name"),
			project=sov.get("project"),
			status=sov.get("status"),
		)
		for sov in schedules or []
	]
	rows.sort(key=lambda r: r["schedule_of_values"] or "")
	return rows


def billed_total(rows):
	"""The total row of Billed vs Scheduled: sums in cents, and the percent billed taken from them (a
	line by others, worth nothing, adds nothing and never pulls an average down)."""
	scheduled = sum(pm.cents(r.get("scheduled_value")) for r in rows or [])
	billed = sum(pm.cents(r.get("billed_to_date")) for r in rows or [])
	return _billed_row("Total", scheduled, billed)

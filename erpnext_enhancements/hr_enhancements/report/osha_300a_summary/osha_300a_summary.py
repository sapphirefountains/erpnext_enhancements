# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""OSHA Form 300A — the annual summary the CEO signs and posts.

The totals off the 300 log, in the form's own boxes. **It must be posted from
1 February to 30 April whether or not anything happened**, and an empty summary is
the commonest one to forget, so this renders the zeros rather than an empty table.

`Total hours worked` is genuinely not in ERPNext here — pay runs through
QuickBooks and an outside bureau — so it is a filter the person filling the form
in types from the payroll report, not a number this invents. A plausible-looking
figure derived from headcount times 2,080 would be wrong by exactly the overtime
this crew works, and it is the denominator of every incidence rate an insurer
computes.

**Names never appear here at all**, which makes this the one OSHA form with no
privacy problem: the summary is counts.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

DOCTYPE = "Safety Incident"

DEATH = "Death"
DAYS_AWAY = "Days away from work"
RESTRICTION = "Job transfer or restriction"
OTHER_RECORDABLE = "Other recordable case"

ILLNESS_ROWS = (
	("(M1) Injuries", "Injury"),
	("(M2) Skin disorders", "Skin disorder"),
	("(M3) Respiratory conditions", "Respiratory condition"),
	("(M4) Poisonings", "Poisoning"),
	("(M5) Hearing loss", "Hearing loss"),
	("(M6) All other illnesses", "All other illnesses"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	year = cint(filters.get("year")) or getdate(today()).year
	cases = _cases(year)

	rows = [
		_row(_("Number of cases"), ""),
		_row(_("(G) Deaths"), sum(1 for c in cases if c.outcome == DEATH)),
		_row(_("(H) Cases with days away from work"), sum(1 for c in cases if c.outcome == DAYS_AWAY)),
		_row(_("(I) Cases with job transfer or restriction"), sum(1 for c in cases if c.outcome == RESTRICTION)),
		_row(_("(J) Other recordable cases"), sum(1 for c in cases if c.outcome == OTHER_RECORDABLE)),
		_row("", ""),
		_row(_("Number of days"), ""),
		_row(_("(K) Days away from work"), sum(cint(c.days_away) for c in cases)),
		_row(_("(L) Days of job transfer or restriction"), sum(cint(c.days_restricted) for c in cases)),
		_row("", ""),
		_row(_("Injury and illness types"), ""),
	]
	for label, value in ILLNESS_ROWS:
		rows.append(_row(_(label), sum(1 for c in cases if (c.illness_type or "Injury") == value)))

	rows.extend(
		[
			_row("", ""),
			_row(_("Establishment"), ""),
			_row(_("Annual average number of employees"), _headcount()),
			_row(_("Total hours worked"), cint(filters.get("total_hours")) or _("— type it in")),
		]
	)
	return _columns(), rows, _message(cases, year, filters)


def _cases(year):
	return frappe.get_all(
		DOCTYPE,
		filters={
			"is_recordable": 1,
			"occurred_on": ["between", [f"{year}-01-01 00:00:00", f"{year}-12-31 23:59:59"]],
		},
		fields=["name", "outcome", "days_away", "days_restricted", "illness_type"],
	)


def _headcount():
	"""Active employees now, as a stand-in for the annual average.

	Labelled honestly on the form rather than dressed up: on a sixteen-person shop
	with little turnover the two are the same number, and where they are not, the
	person filling in the form is the person who knows.
	"""
	return frappe.db.count("Employee", {"status": "Active"})


def _row(label, value):
	return {"item": label, "value": value}


def _message(cases, year, filters):
	parts = []
	if not cases:
		parts.append(
			_(
				"<b>No recordable cases in {0}.</b> The summary still has to be signed and posted "
				"from 1 February to 30 April — an empty one is the commonest to forget."
			).format(year)
		)
	if not cint(filters.get("total_hours")):
		parts.append(
			_(
				"<b>Total hours worked</b> is not in ERPNext — payroll runs through QuickBooks — so "
				"type it from the payroll report. It is the denominator of every incidence rate an "
				"insurer computes, and a figure guessed from headcount would be wrong by exactly "
				"the overtime this crew works."
			)
		)
	return " ".join(str(p) for p in parts) or None


def _columns():
	return [
		{"fieldname": "item", "label": _("Box"), "fieldtype": "Data", "width": 320},
		{"fieldname": "value", "label": _("Total"), "fieldtype": "Data", "width": 160},
	]

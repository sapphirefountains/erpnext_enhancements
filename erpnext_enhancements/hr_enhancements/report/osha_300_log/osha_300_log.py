# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""OSHA Form 300 — the log of work-related injuries and illnesses.

One row per **recordable** case, in the column order the form uses, for one
calendar year. Not a dashboard and not a management view: this is the thing you
print, and it is shaped by the form rather than by what would read nicely.

**The privacy rule is enforced here and only here.** Six categories of case must
not carry a name on the posted log. Every name on this report comes from
`SafetyIncident.log_name()` — one function, so there is one place to get it right
instead of one per report — and a case marked private prints "Privacy Case". The
name stays on the incident record and on `OSHA Privacy Case List`, which is a
separate report with a shorter role list.

**Only recordable cases appear**, which is the form's own rule and not a filter
for convenience. Near misses are excluded and that is correct even though they are
the most useful rows in the underlying data — the 300 log is a legal document with
a defined population, and padding it is not generosity, it is a wrong form.

Year is a filter rather than a parameter of the data because a case's year is the
year of the **injury**, not of the record: a case opened in January for a December
injury belongs on the previous year's log.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, today

DOCTYPE = "Safety Incident"

DEATH = "Death"
DAYS_AWAY = "Days away from work"
RESTRICTION = "Job transfer or restriction"
OTHER_RECORDABLE = "Other recordable case"

#: Column (M) on the form — exactly one per case.
ILLNESS_COLUMNS = (
	("Injury", "m1_injury"),
	("Skin disorder", "m2_skin"),
	("Respiratory condition", "m3_respiratory"),
	("Poisoning", "m4_poisoning"),
	("Hearing loss", "m5_hearing"),
	("All other illnesses", "m6_other"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	year = cint(filters.get("year")) or getdate(today()).year

	rows = []
	for name in _cases(year):
		doc = frappe.get_doc(DOCTYPE, name)
		row = {
			"case": doc.name,
			# The privacy rule, applied in the one place it can be. Never
			# `doc.employee_name`.
			"employee_name": doc.log_name(),
			"job_title": doc.job_title_at_time or "",
			"occurred_on": getdate(doc.occurred_on) if doc.occurred_on else None,
			"location_text": doc.location_text or "",
			"description": _description(doc),
			"g_death": 1 if doc.outcome == DEATH else 0,
			"h_days_away": 1 if doc.outcome == DAYS_AWAY else 0,
			"i_restriction": 1 if doc.outcome == RESTRICTION else 0,
			"j_other": 1 if doc.outcome == OTHER_RECORDABLE else 0,
			"k_days_away": cint(doc.days_away),
			"l_days_restricted": cint(doc.days_restricted),
		}
		for label, fieldname in ILLNESS_COLUMNS:
			row[fieldname] = 1 if (doc.illness_type or "Injury") == label else 0
		rows.append(row)

	rows.sort(key=lambda r: (r["occurred_on"] or getdate(today()), r["case"]))
	return _columns(), rows, _message(rows, year)


def _cases(year):
	"""Recordable cases whose INJURY fell in *year*.

	Keyed on `occurred_on`, not on `creation`: a case opened in January for a
	December injury belongs on the previous year's log, and getting that backwards
	moves a case between two forms that have both already been posted.
	"""
	return frappe.get_all(
		DOCTYPE,
		filters={
			"is_recordable": 1,
			"occurred_on": ["between", [f"{year}-01-01 00:00:00", f"{year}-12-31 23:59:59"]],
		},
		pluck="name",
	)


def _description(doc):
	"""Column (F): the injury, the body part, and what harmed them, in one line.

	The form asks for them together even though the record keeps them apart — which
	is the right way round, because a form field that asks for three things at once
	gets one.
	"""
	parts = [doc.injury_description or doc.what_happened or ""]
	if doc.body_part:
		parts.append(str(doc.body_part))
	if doc.harmed_by:
		parts.append(_("from {0}").format(doc.harmed_by))
	return ", ".join(p for p in parts if p)


def _message(rows, year):
	if not rows:
		return _(
			"No recordable cases for {0}. That is the log, and an empty one still has to be "
			"posted from 1 February to 30 April — see the 300A summary."
		).format(year)
	private = sum(1 for r in rows if r["employee_name"] == _("Privacy Case"))
	note = _("{0} recordable case(s) in {1}.").format(len(rows), year)
	if private:
		note += " " + _(
			"{0} of them are privacy cases and show no name here by rule — the names are on "
			"<b>OSHA Privacy Case List</b>."
		).format(private)
	return note


def _columns():
	return [
		{"fieldname": "case", "label": _("(A) Case"), "fieldtype": "Link", "options": DOCTYPE, "width": 130},
		{"fieldname": "employee_name", "label": _("(B) Employee"), "fieldtype": "Data", "width": 160},
		{"fieldname": "job_title", "label": _("(C) Job title"), "fieldtype": "Data", "width": 140},
		{"fieldname": "occurred_on", "label": _("(D) Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "location_text", "label": _("(E) Where"), "fieldtype": "Data", "width": 170},
		{"fieldname": "description", "label": _("(F) Describe"), "fieldtype": "Data", "width": 300},
		{"fieldname": "g_death", "label": _("(G) Death"), "fieldtype": "Int", "width": 80},
		{"fieldname": "h_days_away", "label": _("(H) Days away"), "fieldtype": "Int", "width": 100},
		{"fieldname": "i_restriction", "label": _("(I) Transfer"), "fieldtype": "Int", "width": 90},
		{"fieldname": "j_other", "label": _("(J) Other"), "fieldtype": "Int", "width": 80},
		{"fieldname": "k_days_away", "label": _("(K) Days"), "fieldtype": "Int", "width": 80},
		{"fieldname": "l_days_restricted", "label": _("(L) Restricted"), "fieldtype": "Int", "width": 100},
	] + [
		{"fieldname": fieldname, "label": f"(M{i + 1}) {label}", "fieldtype": "Int", "width": 90}
		for i, (label, fieldname) in enumerate(ILLNESS_COLUMNS)
	]

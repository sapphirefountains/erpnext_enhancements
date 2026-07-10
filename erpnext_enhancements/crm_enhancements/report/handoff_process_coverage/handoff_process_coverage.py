# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hand-Off Process Coverage — one row per Opportunity that has a linked
Project, showing whether that project's hand-off tracker (PRO-0204 Project
Process Steps) has been started and, if so, which step is currently live.

This surfaces the population that used to render a blank "Hand-Off Process"
tab: Closed-Won opportunities whose linked project has **no** started tracker
(``Tracker Started = No``). Default filters land on exactly that set. See
``crm_enhancements/project_prompt.opportunity_handoff_steps`` (the tab data
source) and ``process_steps.py`` (the engine that seeds the steps).
"""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Opportunity"), "fieldname": "opportunity", "fieldtype": "Link", "options": "Opportunity", "width": 165},
		{"label": _("Opp Status"), "fieldname": "opp_status", "fieldtype": "Data", "width": 110},
		{"label": _("Customer / Party"), "fieldname": "party", "fieldtype": "Data", "width": 190},
		{"label": _("Won Date"), "fieldname": "won_date", "fieldtype": "Date", "width": 95},
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
		{"label": _("Project Status"), "fieldname": "project_status", "fieldtype": "Data", "width": 105},
		{"label": _("Tracker Started"), "fieldname": "tracker_started", "fieldtype": "Data", "width": 120},
		{"label": _("Steps"), "fieldname": "n_steps", "fieldtype": "Int", "width": 65},
		{"label": _("Current Step"), "fieldname": "current_step", "fieldtype": "Data", "width": 240},
	]


def get_data(filters):
	opp_filters = {"custom_created_project": ("is", "set")}
	if filters.get("opp_status"):
		opp_filters["status"] = filters.opp_status

	opps = frappe.get_all(
		"Opportunity",
		filters=opp_filters,
		fields=[
			"name",
			"status",
			"custom_created_project",
			"custom_date_closed_won",
			"customer_name",
			"party_name",
		],
		order_by="custom_date_closed_won desc, name desc",
	)
	if not opps:
		return []

	projects = list({o.custom_created_project for o in opps if o.custom_created_project})

	# Step count + first pending step per project (bulk; avoids N+1).
	counts = {}
	first_pending = {}
	for s in frappe.get_all(
		"Project Process Step",
		filters={"parenttype": "Project", "parent": ("in", projects)},
		fields=["parent", "step_number", "step_title", "status"],
		order_by="parent asc, step_number asc",
	):
		counts[s.parent] = counts.get(s.parent, 0) + 1
		if s.status == "Pending" and s.parent not in first_pending:
			first_pending[s.parent] = f"{s.step_number}. {s.step_title}"

	proj_status = {
		p.name: p.status
		for p in frappe.get_all("Project", filters={"name": ("in", projects)}, fields=["name", "status"])
	}

	want = filters.get("coverage")  # "Started" | "Not Started" | "" (all)
	data = []
	for o in opps:
		proj = o.custom_created_project
		n = counts.get(proj, 0)
		started = n > 0
		if want == "Started" and not started:
			continue
		if want == "Not Started" and started:
			continue
		data.append(
			{
				"opportunity": o.name,
				"opp_status": o.status,
				"party": o.get("customer_name") or o.get("party_name"),
				"won_date": o.get("custom_date_closed_won"),
				"project": proj,
				"project_status": proj_status.get(proj),
				"tracker_started": _("Yes") if started else _("No"),
				"n_steps": n,
				"current_step": first_pending.get(proj)
				or (_("All steps complete") if started else _("Tracker not started")),
			}
		)
	return data

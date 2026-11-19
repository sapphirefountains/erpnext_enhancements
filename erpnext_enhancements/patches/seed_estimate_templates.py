"""Seed the Estimate Templates by job kind (v1.836.0, estimating milestone 1).

Four templates from ``estimating/data/estimate_templates.json``: Design-Build (phase 100 holds the
design stages 110 to 140), Build, Event (no phases) and Custom Panel (no rough-in). The test Nik set
for 2026-11-23 needs the first, third and fourth; Build is the design-build template without design.

Insert-only, keyed on the template name: a template somebody has edited in the Desk is never
overwritten, and a deleted one is not brought back by a later migrate of this patch (patches run
once). No prices are seeded; the file's lines are a checklist with placeholder quantities.
"""

import json
import os

import frappe


def load():
	path = os.path.join(
		frappe.get_app_path("erpnext_enhancements"), "estimating", "data", "estimate_templates.json"
	)
	with open(path) as fh:
		return json.load(fh)["templates"]


def execute():
	if not frappe.db.table_exists("Estimate Template"):
		return
	for spec in load():
		if frappe.db.exists("Estimate Template", spec["template_name"]):
			continue
		doc = frappe.new_doc("Estimate Template")
		doc.template_name = spec["template_name"]
		doc.job_kind = spec["job_kind"]
		doc.description = spec.get("description")
		for number, label, scope in spec["phases"]:
			doc.append("phases", {"phase_number": number, "label": label, "scope_text": scope})
		for number, include, description, cost_type, code, uom in spec["lines"]:
			doc.append(
				"lines",
				{
					"phase_number": number,
					"include": include,
					"description": description,
					"cost_type": cost_type,
					"cost_code": code,
					"qty": 1,
					"uom": uom,
				},
			)
		doc.insert(ignore_permissions=True)

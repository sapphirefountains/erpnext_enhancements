"""Seed the Project Planner's phase template and starter task bundles (T3, v1.608.0).

Creates, as ERPNext **Project Templates** made of template Tasks (``is_template``), what the Build out
grid's "Start from template..." offers:

* **Fountain build phases (DOC-0041)**: phases 100-700 as group template Tasks (number, the phases each
  runs after, and its procurement checklist), each with its labor and subcontract tasks and one
  Project management task, in order. Phase 800 is left out.
* Six starter **bundles** ("Bundle: Control panel design + build" and five more), Nik's list of
  2026-10-12 drafted for him to correct: blank hours, tasks in order, Review status *Needs review*.

The content is ``project_enhancements/data/planner_templates.json``, derived from the spreadsheet by
``scripts/derive_planner_templates.py`` (nothing parses a spreadsheet at migrate time).

* **Insert-only, by template name.** A template that exists is skipped whole, never compared, never
  touched: once seeded it is Nik's to edit in the Desk, and a later migrate must not put the draft back.
  A deleted one stays deleted (a patch runs once).
* **The custom fields first.** Patches run before fixture sync, so on the deploy that introduces them
  the template fields do not exist yet; the patch creates each one missing from the exact record in
  ``fixtures/custom_field.json`` (the ``backfill_stage_changed_on`` precedent), and fixture sync adopts
  the same records later in the migrate. That DDL commits on its own, so it runs before any savepoint.
* **One template, one savepoint.** A template whose seed has a problem (``planner_templates.seed_problems``)
  or that ERPNext refuses is rolled back whole (no orphan template Tasks) and logged; the others go on.
* **Cannot raise.** Everything is caught and logged with ``log_error(title=..., message=...)``: a patch
  that raises aborts ``bench migrate``, which on this repo is the deploy. A failed seed is re-run with
  ``bench --site <site> execute erpnext_enhancements.patches.seed_planner_templates.execute``.
"""

import json

import frappe

from erpnext_enhancements.project_enhancements import planner_templates as tpl

TEMPLATE = "Project Template"
SAVEPOINT = "seed_planner_template"
LOG_TITLE = "Seed planner templates"


def execute():
	try:
		report = seed()
		print(
			"Planner templates: created {}, already there {}, failed {}".format(
				len(report["created"]), len(report["skipped"]), len(report["failed"])
			)
		)
	except Exception:
		frappe.log_error(
			title=LOG_TITLE,
			message=(
				"The planner's phase template and bundles were not seeded. The Build out grid offers no "
				"templates until they exist; re-run this patch with bench execute.\n\n"
				+ frappe.get_traceback()
			),
		)


def seed(data=None):
	"""Create every template of the seed that does not exist yet. ``{"created", "skipped", "failed"}``."""
	report = {"created": [], "skipped": [], "failed": []}
	if not frappe.db.exists("DocType", TEMPLATE):
		return report
	ensure_fields()
	data = tpl.load_seed() if data is None else data
	if not data.get("templates"):
		frappe.log_error(title=LOG_TITLE, message="The seed file had no templates; nothing was seeded.")
		return report
	for template in data["templates"]:
		name = tpl.clean(template.get("name"))
		if name and frappe.db.exists(TEMPLATE, name):
			report["skipped"].append(name)
			continue
		problems = tpl.seed_problems({"templates": [template]})
		if problems:
			report["failed"].append(name)
			frappe.log_error(
				title=LOG_TITLE,
				message=f"{name or 'A template'} was not seeded:\n" + "\n".join(problems),
			)
			continue
		frappe.db.savepoint(SAVEPOINT)
		try:
			create_template(template)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			report["failed"].append(name)
			frappe.log_error(
				title=LOG_TITLE,
				message=f"{name} was not seeded (rolled back whole).\n\n" + frappe.get_traceback(),
			)
			continue
		report["created"].append(name)
	return report


def ensure_fields():
	"""Create each template field that does not exist yet, from its fixture record, in order."""
	path = frappe.get_app_path("erpnext_enhancements", "fixtures", "custom_field.json")
	with open(path, encoding="utf-8") as handle:
		records = {
			row.get("name"): row for row in json.load(handle) if row.get("name") in tpl.TEMPLATE_FIELDS
		}
	for name in tpl.TEMPLATE_FIELDS:
		if name in records and not frappe.db.exists("Custom Field", name):
			frappe.get_doc(dict(records[name])).insert(ignore_permissions=True)


def _task(values):
	doc = frappe.get_doc(dict(values, doctype="Task", is_template=1, status="Template"))
	doc.insert(ignore_permissions=True)
	return doc


def _leaf(task, parent=None, made=None):
	"""A template task; ``made`` maps the subjects above it (in its phase or bundle) to their Tasks."""
	made = made if made is not None else {}
	values = {
		"subject": tpl.clean(task.get("subject")),
		"expected_time": float(task.get("hours") or 0),
		"depends_on": [
			{"task": made[wait].name, "subject": made[wait].subject} for wait in task.get("waits_on") or []
		],
	}
	if parent:
		values["parent_task"] = parent
	if task.get("line"):
		values["custom_template_line"] = task["line"]
	if task.get("cost_code"):
		values["custom_cost_code"] = task["cost_code"]
	if task.get("crew_size"):
		values["custom_crew_size"] = int(task["crew_size"])
	return _task(values)


def create_template(template):
	"""The template Tasks of one seed template, in order, then the Project Template that lists them."""
	docs = []
	if template.get("kind") == tpl.PHASE_TEMPLATE:
		for phase in template.get("phases") or []:
			group = _task(
				{
					"subject": tpl.clean(phase.get("title")),
					"is_group": 1,
					"custom_phase_number": int(phase["number"]),
					"custom_phase_runs_after": tpl.format_runs_after(phase.get("runs_after")),
					"custom_procurement_checklist": "\n".join(phase.get("checklist") or []),
				}
			)
			docs.append(group)
			made = {}
			for task in phase.get("tasks") or []:
				made[tpl.clean(task.get("subject"))] = doc = _leaf(task, group.name, made)
				docs.append(doc)
	else:
		made = {}
		for task in template.get("tasks") or []:
			made[tpl.clean(task.get("subject"))] = doc = _leaf(task, None, made)
			docs.append(doc)
	frappe.get_doc(
		{
			"doctype": TEMPLATE,
			"custom_template_kind": template.get("kind"),
			"custom_review_status": template.get("review_status") or tpl.NEEDS_REVIEW,
			"custom_template_notes": template.get("notes") or "",
			"tasks": [{"task": doc.name, "subject": doc.subject} for doc in docs],
		}
	).insert(ignore_permissions=True, set_name=tpl.clean(template.get("name")))

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Design project template: PRO-0206's four design stages (Nik, 2026-10-30).

PRO-0206 *Design Process* (v1.0, 2026-05-25) runs a design job through four stages, with an
approval between each one: **Predesign (PD)**, **Schematic Design (SD)**, **Design
Development (DD)** and **Construction Documents (CD)**. Going over it on 2026-10-30, Nik asked for
just those four for now: four tasks, nothing under them, in order, with rough lengths.

* **The template** is an ERPNext **Project Template**, :data:`TEMPLATE` (project type Design), of four
  template Tasks (:data:`STAGES`). ERPNext's ``Project.copy_from_template`` dates each one from the
  project's start (``start`` and ``duration``, in days) and copies the dependencies, so on a new
  project each stage waits on the one before: PD days 0-13, SD 14-34, DD 35-62, CD 63-83, about twelve
  weeks. They are leaf tasks, not groups, so no group's end date can refuse a stage that a holiday
  pushed a day later.
* **Seeded once** by ``patches.seed_design_project_template``, insert-only by name: once it exists it
  is Nik's to edit in the Desk, and a later migrate never touches it.
* **The old "Design Project Template" is disabled** by the same patch, and only once the new one
  exists (Nik, 2026-10-30), so nobody can pick it for a new project. ``disabled`` is all that changes:
  frappe's link check reads only whether a record exists and its docstatus, so the 16 projects made
  from it keep their link and their tasks.
* **The Create Project dialog prefills it** (:func:`opportunity_defaults`) when the deal's value streams
  include Design, the stream ``create_project_from_opportunity_background`` ranks first when it derives
  the project type. It stays changeable, and a Fountain Move intake's own template wins.
* **The stages are not build phases.** A project's build phases are named ``Phase 100`` ... ``Phase
  700`` (decision 2026-10-12) and the planner recognises a phase by that number
  (``planner_templates.phase_number``), so no stage's name starts with one. On the planners' Simple mode
  a job with no phase work lists its top-level tasks, which is how the four stages show there.

The repository is public and PRO-0206 is an internal document, so each stage's description is a
one-line summary and a pointer to PRO-0206, never the procedure itself.

Everything here that runs during a migrate or a dialog is defensive and never raises.
"""

import frappe

#: The Project Template the seed patch creates. Renaming it in the Desk stops the dialog's prefill.
TEMPLATE = "Design Project (PRO-0206)"
#: The template PRO-0206's replaces (Nik, 2026-10-30: disable it, leave its projects alone).
OLD_TEMPLATE = "Design Project Template"
TEMPLATE_DOCTYPE = "Project Template"
PROJECT_TYPE = "Design"
#: A deal is a Design deal when one of its value streams is this.
VALUE_STREAM = "Design"
#: Opportunity.custom_value_stream rows; live rows use ``value_stream``, older ones ``value_streams``.
STREAM_DOCTYPE = "Value Stream"
STREAM_COLUMNS = ("value_stream", "value_streams")
LOG_TITLE = "Design projects"

#: PRO-0206's four stages, in order (Nik, 2026-10-30). ``start`` and ``duration`` are ERPNext's
#: *Begin On (Days)* and *Duration (Days)*: a stage runs from ``start`` to ``start + duration`` days after
#: the project's start, and the next begins the day after. Nik's rough lengths: PD about two weeks, SD
#: three, DD four, CD three. Each waits on the one before it.
STAGES = (
	{
		"subject": "Predesign (PD)",
		"start": 0,
		"duration": 13,
		"description": (
			"Define the scope, budget and constraints with the owner and architect, and prepare the "
			"preliminary proposal and baseline budget. Approved before Schematic Design starts."
		),
	},
	{
		"subject": "Schematic Design (SD)",
		"start": 14,
		"duration": 20,
		"description": (
			"The first 3D concept, renderings and system diagrams. The drawings are preliminary and not "
			"for construction. Approved before Design Development starts."
		),
	},
	{
		"subject": "Design Development (DD)",
		"start": 35,
		"duration": 27,
		"description": (
			"Freeze the layout and complete the calculations, the civil details and the controller "
			"package. Approved before Construction Documents start."
		),
	},
	{
		"subject": "Construction Documents (CD)",
		"start": 63,
		"duration": 20,
		"description": (
			"Issue the final construction set, with ERP part numbers in place of CAD part names, ready "
			"for production."
		),
	},
)
#: Where every stage's description points for the procedure itself.
PROCESS_NOTE = "Process: PRO-0206 Design Process."


def stage_description(stage):
	"""A stage's Task description: its one-line summary, then where the procedure is."""
	return f"<p>{stage['description']}</p><p>{PROCESS_NOTE}</p>"


def template_task_values(stage):
	"""The template Task one :data:`STAGES` entry seeds (no hours: Nik asked for none)."""
	return {
		"subject": stage["subject"],
		"description": stage_description(stage),
		"start": int(stage["start"]),
		"duration": int(stage["duration"]),
	}


def is_design_deal(rows):
	"""True when any value-stream row (``{"value_stream", "value_streams"}``) is Design."""
	for row in rows or []:
		for column in STREAM_COLUMNS:
			if ((row or {}).get(column) or "").strip() == VALUE_STREAM:
				return True
	return False


# ---------------------------------------------------------------------- reads


def template_exists(name=TEMPLATE):
	try:
		return bool(frappe.db.exists(TEMPLATE_DOCTYPE, name))
	except Exception:
		return False


def template_enabled(name=TEMPLATE):
	"""True when ``name`` exists and is not disabled."""
	try:
		if not frappe.db.exists(TEMPLATE_DOCTYPE, name):
			return False
		return not int(frappe.db.get_value(TEMPLATE_DOCTYPE, name, "disabled") or 0)
	except Exception:
		return False


def _stream_rows(opportunity):
	columns = []
	for column in STREAM_COLUMNS:
		try:
			if frappe.db.has_column(STREAM_DOCTYPE, column):
				columns.append(column)
		except Exception:
			pass
	if not columns:
		return []
	return frappe.get_all(
		STREAM_DOCTYPE,
		filters={"parenttype": "Opportunity", "parent": opportunity},
		fields=columns,
		limit_page_length=0,
	)


@frappe.whitelist()
def opportunity_defaults(opportunity):
	"""``{"template"}`` for the Create Project dialog of ``opportunity``: :data:`TEMPLATE` for a Design
	deal while it exists and is enabled, else None. Advisory: never raises."""
	try:
		if not opportunity or not frappe.has_permission("Opportunity", "read", doc=opportunity):
			return {"template": None}
		if not is_design_deal(_stream_rows(opportunity)) or not template_enabled():
			return {"template": None}
		return {"template": TEMPLATE}
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
		return {"template": None}


# ---------------------------------------------------------------------- seeding


def seed_template():
	"""Create the Design Project (PRO-0206) template and its four stage Tasks, unless it exists.

	Insert-only by name: once seeded it is Nik's to edit, and a later run never touches it. Each stage
	waits on the one before, so ERPNext copies the chain onto every project made from it. Returns
	``"created"`` or ``"exists"``; raises on a failure (the patch catches and logs).
	"""
	if template_exists():
		return "exists"
	docs = []
	for stage in STAGES:
		values = dict(template_task_values(stage), doctype="Task", is_template=1, status="Template")
		if docs:
			values["depends_on"] = [{"task": docs[-1].name}]
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=True)
		docs.append(doc)
	template = {
		"doctype": TEMPLATE_DOCTYPE,
		"project_type": PROJECT_TYPE if frappe.db.exists("Project Type", PROJECT_TYPE) else None,
		"tasks": [{"task": doc.name, "subject": doc.subject} for doc in docs],
	}
	frappe.get_doc(template).insert(ignore_permissions=True, set_name=TEMPLATE)
	return "created"


def disable_old_template():
	"""Disable :data:`OLD_TEMPLATE` once :data:`TEMPLATE` exists (Nik, 2026-10-30).

	Only the ``disabled`` box changes, by a field write: the template's tasks and the projects made from
	it are never touched. Returns ``"disabled"``, ``"already"``, ``"missing"`` (no old template) or
	``"kept"`` (the new one is not there, so Design projects keep the only template they have).
	"""
	if not template_exists(OLD_TEMPLATE):
		return "missing"
	if not template_exists():
		return "kept"
	if int(frappe.db.get_value(TEMPLATE_DOCTYPE, OLD_TEMPLATE, "disabled") or 0):
		return "already"
	frappe.db.set_value(TEMPLATE_DOCTYPE, OLD_TEMPLATE, "disabled", 1)
	return "disabled"

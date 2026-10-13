"""Project Planner T3 (v1.608.0): phase templates and task bundles, read for the Build out grid.

Two reads (plain ``frappe.whitelist()`` like the planner's other reads, so a GET works), both for
planner roles only; nothing here writes. The grid creates what it loads
through ``planner_actions.build_out_tasks`` like any other rows (as drafts in Draft mode).

* :func:`get_planner_templates` is the "Start from template..." dialog's catalog: the phase templates
  (with the phases a planner may tick: never 800) and the bundles, each with its review status.
* :func:`get_template_rows` turns the picked phases and/or bundles into grid rows (subject, default
  hours, phase, crew size, ``needs`` as ``row:N``), plus each picked phase's procurement checklist, in
  ONE answer. The rules (order inside a phase, 500 and 600 side by side, 800 never) are
  ``project_enhancements/planner_templates.py``'s, which is pure and tested on its own.

Templates are ERPNext Project Templates edited in the Desk (System Manager, Projects Manager); see
``project_enhancements/README.md`` "How to edit". They are read with ``get_all``: the planner role is
the gate, as for every planner read, because Projects User holds only *select* on Project Template.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import planner_templates as tpl

TEMPLATE_DOCTYPE = "Project Template"
TEMPLATE_ROW = "Project Template Task"
#: The Task fields a template reads; the custom ones only once their columns exist.
TASK_FIELDS = ("name", "subject", "is_group", "parent_task", "expected_time", "description")
TASK_CUSTOM_FIELDS = (
	"custom_crew_size",
	"custom_phase_number",
	"custom_phase_runs_after",
	"custom_procurement_checklist",
	"custom_template_line",
	"custom_cost_code",
)
#: Most bundles one request may add (each is a handful of rows; the grid holds 100).
MAX_BUNDLES = 20


def _ready():
	"""Whether the T3 fields exist yet (a site between the deploy and its migrate has none)."""
	try:
		return bool(frappe.db.has_column(TEMPLATE_DOCTYPE, "custom_template_kind"))
	except Exception:
		return False


def _templates(kind=None, names=None):
	filters = {"custom_template_kind": ["in", list(tpl.KINDS)], "disabled": 0}
	if kind:
		filters["custom_template_kind"] = kind
	if names is not None:
		filters["name"] = ["in", list(names)]
	return frappe.get_all(
		TEMPLATE_DOCTYPE,
		filters=filters,
		fields=["name", "custom_template_kind", "custom_review_status"],
		order_by="name asc",
		limit_page_length=0,
	)


def _structures(templates):
	"""``{template name: structure}`` for ``templates`` (rows of :func:`_templates`), in three reads:
	the templates' task lists, their Tasks, and those Tasks' dependencies."""
	names = [row.get("name") for row in templates]
	if not names:
		return {}
	links = frappe.get_all(
		TEMPLATE_ROW,
		filters={"parenttype": TEMPLATE_DOCTYPE, "parent": ["in", names]},
		fields=["parent", "task", "idx"],
		order_by="parent asc, idx asc",
		limit_page_length=0,
	)
	task_names = sorted({row.get("task") for row in links if row.get("task")})
	fields = list(TASK_FIELDS) + [c for c in TASK_CUSTOM_FIELDS if pp._task_has_column(c)]
	tasks = (
		{
			row.get("name"): row
			for row in frappe.get_all(
				"Task",
				filters={"name": ["in", task_names], "is_template": 1},
				fields=fields,
				limit_page_length=0,
			)
		}
		if task_names
		else {}
	)
	depends = (
		frappe.get_all(
			"Task Depends On",
			filters={"parenttype": "Task", "parent": ["in", sorted(tasks)]},
			fields=["parent", "task"],
			limit_page_length=0,
		)
		if tasks
		else []
	)
	ordered = {}
	for row in sorted(links, key=lambda r: (r.get("parent"), cint(r.get("idx")))):
		task = tasks.get(row.get("task"))
		if task and task not in ordered.setdefault(row.get("parent"), []):
			ordered[row.get("parent")].append(task)
	return {
		row.get("name"): tpl.structure_from_tasks(
			row.get("custom_template_kind"), ordered.get(row.get("name"), []), depends
		)
		for row in templates
	}


@frappe.whitelist()
def get_planner_templates():
	"""The catalog the Build out grid's "Start from template..." offers.

	``{"phase_templates": [{name, review_status, needs_review, phases: [{id, number, title, tasks,
	checklist}], loose}], "bundles": [{name, review_status, needs_review, tasks}], "ready"}``. Only
	enabled Project Templates whose *Planner template kind* is set; phase 800 is never in ``phases``.
	``ready`` is False on a site whose template fields do not exist yet (nothing is offered).
	"""
	pp._require_planner()
	if not _ready():
		return {"phase_templates": [], "bundles": [], "ready": False}
	templates = _templates()
	structures = _structures(templates)
	phase_templates, bundles = [], []
	for row in templates:
		structure = structures.get(row.get("name")) or {}
		review = row.get("custom_review_status") or ""
		entry = {"name": row.get("name"), "review_status": review, "needs_review": review != tpl.REVIEWED}
		if row.get("custom_template_kind") == tpl.BUNDLE:
			entry["tasks"] = len(structure.get("tasks") or [])
			bundles.append(entry)
		else:
			entry["phases"] = tpl.offered_phases(structure)
			entry["loose"] = structure.get("loose") or 0
			phase_templates.append(entry)
	return {"phase_templates": phase_templates, "bundles": bundles, "ready": True}


def _bundle_requests(bundles, bundle_phase):
	"""``[(name, phase), ...]`` from ``bundles``: names, or ``{"template", "phase"}`` entries; a bundle
	without a phase of its own goes under ``bundle_phase``."""
	try:
		items = pp.parse_list(pp.given(bundles)) or []
	except ValueError:
		frappe.throw(_("The bundles could not be read. Reload the planner and try again."))
	default = tpl.clean(pp.given(bundle_phase))
	out = []
	for item in items:
		if isinstance(item, dict):
			name, phase = tpl.clean(item.get("template")), tpl.clean(item.get("phase")) or default
		else:
			name, phase = tpl.clean(item), default
		if not name:
			continue
		if len(phase) > tpl.SUBJECT_MAX:
			frappe.throw(_("Keep the phase name to {0} characters.").format(tpl.SUBJECT_MAX))
		out.append((name, phase))
	if len(out) > MAX_BUNDLES:
		frappe.throw(_("Add {0} bundles or fewer at once.").format(MAX_BUNDLES))
	return out


def _picked(phases, offered):
	"""The phase ids asked for (None: all of them). One the template does not offer is refused."""
	try:
		items = pp.parse_list(pp.given(phases))
	except ValueError:
		frappe.throw(_("The phases could not be read. Reload the planner and try again."))
	if items is None:
		return None
	ids = {phase["id"] for phase in offered}
	picked = []
	for item in items:
		ident = tpl.clean(item)
		if not ident:
			continue
		if ident not in ids:
			frappe.throw(
				_("Phase {0} is not one this template offers.").format(frappe.utils.escape_html(ident))
			)
		picked.append(ident)
	return picked


def _estimate_hours(project):
	"""T5's seam: the hours a project's live estimate puts on each cost code. Not built yet (T5 is
	blocked on the estimate), so every row keeps its template hours."""
	return {}


@frappe.whitelist()
def get_template_rows(template=None, phases=None, bundles=None, bundle_phase=None, project=None):
	"""A phase template's picked phases and/or bundles as Build out grid rows.

	* ``template``: a *Phase template*; ``phases``: a JSON list of the phase ids (numbers) to use (left
	  out: every phase it offers; never 800).
	* ``bundles``: a JSON list of *Bundle* names, or of ``{"template", "phase"}``; each is filed under its
	  ``phase``, else under ``bundle_phase`` (blank: no phase). They come after the phase rows.
	* ``project`` (optional): the job the grid is for; T5 will price hours from its estimate.

	Returns ``{"rows": [{subject, hours, phase, crew_size, needs: ["row:N"], cost_code, line,
	template_task, hours_from}], "count", "checklists": {phase title: [items]}, "phases": [titles],
	"note", "needs_review": [template names]}``: the shape ``get_copy_source`` answers, so the grid loads
	it the same way. Refuses an unknown or disabled template, a template of the other kind, nothing
	picked, and more rows than the grid holds. Read-only.
	"""
	pp._require_planner()
	if not _ready():
		frappe.throw(_("The planner templates are not installed yet. Try again after the next update."))
	name = tpl.clean(pp.given(template))
	requests = _bundle_requests(bundles, bundle_phase)
	if not name and not requests:
		frappe.throw(_("Pick a phase template or at least one bundle."))
	wanted = ([name] if name else []) + [bundle for bundle, _phase in requests]
	rows = {row.get("name"): row for row in _templates(names=sorted(set(wanted)))}
	if name and (rows.get(name) or {}).get("custom_template_kind") != tpl.PHASE_TEMPLATE:
		frappe.throw(_("{0} is not a phase template.").format(frappe.utils.escape_html(name)))
	for bundle, _phase in requests:
		if (rows.get(bundle) or {}).get("custom_template_kind") != tpl.BUNDLE:
			frappe.throw(_("{0} is not a bundle.").format(frappe.utils.escape_html(bundle)))
	structures = _structures(list(rows.values()))
	structure = structures.get(name) if name else None
	picked = _picked(phases, tpl.offered_phases(structure)) if structure else None
	if structure and picked is not None and not picked and not requests:
		frappe.throw(_("Tick at least one phase."))
	plan = tpl.template_rows(
		structure, picked, [(structures.get(bundle) or {}, phase) for bundle, phase in requests]
	)
	if not plan["rows"]:
		frappe.throw(_("Those templates have no tasks to add."))
	if len(plan["rows"]) > tpl.MAX_ROWS:
		frappe.throw(
			_("That is {0} tasks; the grid holds {1}. Pick fewer phases or bundles.").format(
				len(plan["rows"]), tpl.MAX_ROWS
			)
		)
	tpl.apply_estimate_hours(plan["rows"], _estimate_hours(pp.given(project)))
	review = [
		entry for entry in wanted if (rows.get(entry) or {}).get("custom_review_status") != tpl.REVIEWED
	]
	parts = [_("{0} tasks from the template.").format(len(plan["rows"]))]
	if plan["checklists"]:
		parts.append(_("Each new phase gets its procurement checklist."))
	if review:
		parts.append(_("Still to be reviewed: {0}.").format(", ".join(review)))
	if structure and structure.get("loose"):
		parts.append(_("{0} template tasks under no phase were left out.").format(structure["loose"]))
	plan.update(count=len(plan["rows"]), note=" ".join(parts), needs_review=review)
	return plan

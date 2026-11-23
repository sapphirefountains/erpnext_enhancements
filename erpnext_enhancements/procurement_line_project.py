"""A project for the line that *Update Items* adds to a submitted Purchase Order (ER-2026-1333210).

``Purchase Order Item.project`` is mandatory here (Property Setter fixture
``Purchase Order Item-project-reqd``). On a draft order that is covered by
``procurement_project.cascade_project_to_items``, a ``before_validate`` hook on the
**Purchase Order**. It cannot help a submitted one, because of how ERPNext adds the row.

**What ERPNext does.** *Update Items* calls ``accounts_controller.update_child_qty_rate``.
For a new row that builds a ``Purchase Order Item`` with ``frappe.new_doc(..., parent_doc=...,
parentfield=...)`` (``set_order_defaults``, line 4379), never sets ``project`` on it, and then calls
``child_item.insert()`` (line 5026) **on the child by itself**, before ``parent.save()`` runs at all
(read at erpnext v16.54.0). Frappe's mandatory check runs on that insert, finds the project
blank, and raises ``Value missing for: Project``. The dialog has no Project column, so the
person cannot fix it. The parent's hooks never run for that insert, so the fix has to be on
the child.

**Which hook.** ``before_insert``. In frappe v16.56.0 ``Document.insert`` runs
``run_method("before_insert")`` (``frappe/model/document.py`` line 718) well ahead of
``run_before_save_methods()`` (line 724) and ``_validate()`` (line 725), whose first step is
``_validate_mandatory()`` (defined at line 1454). ``run_method`` applies ``doc_events`` for the
child's own doctype (``Document.hook``'s ``composer``, lines 1923-1927), so a ``doc_events`` entry on ``Purchase Order Item``
fires for a child inserted alone. ``before_validate`` would fire too (``check_if_latest`` sets
``_action = "save"`` at line 1367), but ``before_insert`` is earlier and says what it means:
this only ever concerns a row being created on its own.

**The rule** (:func:`project_for_new_line`):

1. A project already on the row is never touched.
2. The order's header ``project``.
3. If the header is blank, the one project the order's existing lines share.
4. If the lines name more than one project, nothing is guessed: the row is refused with a
   message that says why and what the person can do (:func:`ambiguous_message`).
5. If there is nothing to go on (no header, no lines with a project), the row is left blank and
   Frappe's own mandatory error stands.

Defensive on purpose: ``doc_events`` fire during ERPNext's own test bootstrap and for every
child insert, so a parent that cannot be read, or a row that is not a Purchase Order line,
returns quietly rather than becoming a second error.
"""

import frappe

ORDER_DOCTYPE = "Purchase Order"
LINE_DOCTYPE = "Purchase Order Item"


def _clean(value):
	return (value or "").strip() if isinstance(value, str) else ""


def distinct_projects(values):
	"""The non-blank projects in ``values``, first-seen order, each once."""
	out = []
	for value in values or ():
		value = _clean(value)
		if value and value not in out:
			out.append(value)
	return out


def project_for_new_line(header_project, line_projects):
	"""The project a new line should take, as ``(project, candidates)``.

	``project`` is the answer, or ``""`` when there is none to give. ``candidates`` is the list of
	distinct line projects when the answer is *ambiguous* (more than one and no header), so the
	caller can say which; it is empty in every other case.

	No Frappe, no I/O: this is the whole rule.
	"""
	header = _clean(header_project)
	if header:
		return header, []
	found = distinct_projects(line_projects)
	if len(found) == 1:
		return found[0], []
	if len(found) > 1:
		return "", found
	return "", []


def ambiguous_message(order, candidates):
	"""Plain words for a person who added a line to an order that spans several jobs."""
	jobs = ", ".join(candidates)
	return (
		f"{order} has no single project to give the new line. The order itself names none, and its lines "
		f"are for more than one job ({jobs}), so there is no safe guess. "
		"Update Items cannot ask which job the new line is for. "
		"Put the new item on its own Purchase Order, where you pick its project, "
		"or ask whoever manages purchasing to amend this order so one project is on its header."
	)


def fill_new_line_project(doc, method=None):
	"""``before_insert`` on **Purchase Order Item**: give a row added with Update Items its project.

	Rows saved with their order are never inserted on their own and never get here. Never
	overwrites a project that is set. Raises only for the ambiguous case, and not even then when
	the insert says it ignores mandatory fields (``flags.ignore_mandatory``: the check that would
	have refused the row is off).
	"""
	if _clean(doc.get("project")):
		return
	if doc.get("parenttype") != ORDER_DOCTYPE:
		return
	order = _clean(doc.get("parent"))
	if not order:
		return

	try:
		header = frappe.db.get_value(ORDER_DOCTYPE, order, "project")
		lines = frappe.get_all(
			LINE_DOCTYPE,
			filters={"parent": order, "parenttype": ORDER_DOCTYPE},
			pluck="project",
		)
	except Exception:
		_log("Purchase line project: the order could not be read")
		return

	project, candidates = project_for_new_line(header, lines)
	if project:
		doc.project = project
		return
	if candidates:
		flags = getattr(doc, "flags", None)
		if flags is not None and flags.get("ignore_mandatory"):
			return
		frappe.throw(ambiguous_message(order, candidates), title="Which project is this line for?")


def _log(title):
	"""Record a failure without ever becoming one: this runs inside another document's save."""
	try:
		frappe.log_error(title=title)
	except Exception:
		pass

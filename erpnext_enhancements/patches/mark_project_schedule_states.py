"""Give every Project a planner schedule state (P3 of the Planner UX redesign).

Nik, 2026-10-21 (D8, ``docs/planner-ux-redesign.md`` §4.8): each project publishes its own
schedule. ``Project.custom_schedule_status`` is *Draft* (private: the planner drafts every write to
the project's tasks and shares those drafts between schedulers) or *Published* (crews see it, edits
are live). A new project starts Draft. An existing one is **Published, without a baseline**, when it
already has dated work with a crew, because that schedule is already in front of people; it stays
**Draft** otherwise. The first *Set baseline* (``api.project_planner.reset_baseline``) gives a
Published project its baseline; nothing here copies a date.

**The rule** (:data:`SCHEDULED_SQL`): the project has a Task that is not Canceled or a template,
has a date (``exp_start_date`` or ``custom_start_datetime``), and has someone on it, a Task Crew
Member row naming a resource, or an open ToDo allocated to an active Planner Resource's user (the
assignee rule of D6). Measured on production on 2026-10-21: 10 of 661 projects (9 Active, 1
Completed); the other 651 are Draft. There were 0 Planner Draft Change rows, so the one behavior
change that rides on Draft (shared drafts) changes nothing until someone drafts.

**Keyed on the rule, not on emptiness**, as CLAUDE.md asks. ``Project`` is a normal doctype, so the
``ALTER`` that adds the column writes its default, ``Draft``, into all 661 rows, and a patch that
only filled blanks would find none and publish nothing. So: the rule's projects become Published
(where they are not already), and then any row still blank becomes Draft (a belt for a column that
arrived without its default). A project somebody published is never set back to Draft.

**It creates the Project fields first.** They are fixture Custom Fields and patches run *before*
fixture sync, so on the deploy that introduces them the columns do not exist yet. Each is inserted
from ``fixtures/custom_field.json`` itself (the ``set_historical_opportunities`` precedent), and
fixture sync then adopts the same records by name. The Task baseline fields are left to fixture
sync: nothing here reads them.

Raw ``UPDATE``s, so no Project is saved: no ``modified`` bump, no hooks, no calendar sync.

Cannot raise: a patch that raises aborts ``bench migrate``, which on this repo is the deploy. A
failure is logged and the patch returns. Safe twice: the second run changes nothing the first did
not. Re-run with
``bench --site <site> execute erpnext_enhancements.patches.mark_project_schedule_states.execute``.
"""

import json

import frappe

#: The Project fields, in the order they must be created (each is inserted after the one before).
PROJECT_FIELDS = (
	"Project-custom_schedule_state_section",
	"Project-custom_schedule_status",
	"Project-custom_schedule_published_on",
	"Project-custom_schedule_state_column",
	"Project-custom_baseline_set_on",
)
DRAFT, PUBLISHED = "Draft", "Published"

#: The projects whose schedule is already in front of people: dated work with someone on it.
SCHEDULED_SQL = """
	SELECT p.name
	FROM `tabProject` p
	WHERE EXISTS (
		SELECT 1 FROM `tabTask` t
		WHERE t.project = p.name
			AND t.status NOT IN ('Canceled', 'Cancelled', 'Template')
			AND (t.exp_start_date IS NOT NULL OR t.custom_start_datetime IS NOT NULL)
			AND (
				EXISTS (
					SELECT 1 FROM `tabTask Crew Member` c
					WHERE c.parenttype = 'Task' AND c.parent = t.name AND IFNULL(c.resource, '') <> ''
				)
				OR EXISTS (
					SELECT 1 FROM `tabToDo` d
					INNER JOIN `tabPlanner Resource` r ON r.user = d.allocated_to AND r.is_active = 1
					WHERE d.reference_type = 'Task' AND d.reference_name = t.name AND d.status = 'Open'
				)
			)
	)
"""


def _has_column():
	try:
		return bool(frappe.db.has_column("Project", "custom_schedule_status"))
	except Exception:
		return False


def ensure_fields():
	"""Insert each Project field the fixture declares and the site lacks, in order. True when the
	status column exists after."""
	if _has_column():
		return True
	path = frappe.get_app_path("erpnext_enhancements", "fixtures", "custom_field.json")
	with open(path, encoding="utf-8") as handle:
		records = {row.get("name"): row for row in json.load(handle) if row.get("name") in PROJECT_FIELDS}
	for name in PROJECT_FIELDS:
		if name in records and not frappe.db.exists("Custom Field", name):
			frappe.get_doc(dict(records[name])).insert(ignore_permissions=True)
	return _has_column()


def scheduled_projects():
	"""The names of the projects the rule publishes."""
	return [row[0] for row in frappe.db.sql(SCHEDULED_SQL)]


def mark(names):
	"""Publish ``names`` (where not already Published), then make every blank status Draft.
	``(published, drafted)``: how many rows each statement changed."""
	published = 0
	if names:
		frappe.db.sql(
			"""
			UPDATE `tabProject` SET custom_schedule_status = %(published)s
			WHERE name IN %(names)s AND IFNULL(custom_schedule_status, '') <> %(published)s
			""",
			{"published": PUBLISHED, "names": tuple(names)},
		)
		published = _changed()
	frappe.db.sql(
		"""
		UPDATE `tabProject` SET custom_schedule_status = %(draft)s
		WHERE IFNULL(custom_schedule_status, '') = ''
		""",
		{"draft": DRAFT},
	)
	return published, _changed()


def _changed():
	"""Rows the last statement changed, where the driver says; 0 where it cannot."""
	try:
		return int(frappe.db._cursor.rowcount or 0)
	except Exception:
		return 0


def execute():
	try:
		ready = ensure_fields()
	except Exception:
		frappe.log_error(title="Planner schedule states: fields not created", message=frappe.get_traceback())
		print("mark_project_schedule_states: could not create the fields; nothing marked.")
		return
	if not ready:
		print("mark_project_schedule_states: Project.custom_schedule_status is missing; nothing marked.")
		return
	try:
		names = scheduled_projects()
		published, drafted = mark(names)
	except Exception:
		frappe.log_error(title="Planner schedule states: not marked", message=frappe.get_traceback())
		print("mark_project_schedule_states: failed; logged. Re-run it with bench execute.")
		return
	print(
		f"mark_project_schedule_states: {len(names)} project(s) already scheduled, "
		f"{published} set Published, {drafted} blank set Draft."
	)

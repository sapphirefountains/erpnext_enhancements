"""Publish the schedule of every active customer job before P4 drafts their edits (Planner UX redesign).

Nik, 2026-10-21: "Keep Draft/Published as designed; publish the 47 active Draft jobs before P4
merges." P4 makes every planner write to a Draft project's tasks a draft, which reaches nobody until
someone presses *Publish…*. P3's patch (``mark_project_schedule_states``) published only the projects
with dated, crewed work (10 of 661), so on the day P4 deploys the jobs the office is running today
would start drafting every drag. This publishes them first.

**The rule** (:func:`active_draft_jobs`): a Project whose ``status`` is *Active*, that the planner
counts as a customer job, and whose ``custom_schedule_status`` is not Published (Draft or blank).
"Customer job" is the planner's own rule, read from ``crew_availability`` (``planner_job_sql`` for the
query, ``is_planner_job`` as the definition each row is re-checked with), so this can never drift
from what the planner shows: a ``project_type`` in ``PLANNER_PROJECT_TYPES`` or a qualifying Value
Stream row. Measured on production on 2026-10-21, read-only: **47** projects, all *Draft*, none
blank: Service 33 (PRJ-00775, the Fountain Move, among them), Design 8, Events 5, Build 1.

**What it writes**, as P3's patch does: one raw ``UPDATE`` of ``custom_schedule_status`` to
*Published*, so no Project is saved (no ``modified`` bump, no hooks, no calendar sync). No baseline
is copied and ``custom_schedule_published_on`` is not stamped, the same as P3's ten: the first *Set
baseline* gives each its baseline. It never sets anything back to Draft, and Completed or Canceled
projects are never touched (the ``UPDATE`` repeats the Active and not-Published conditions).

**New projects still start Draft**, as designed (D8): this runs once, on this deploy.

Cannot raise: a patch that raises aborts ``bench migrate``, which on this repo is the deploy. A
failure is logged and the patch returns. Safe twice: the second run finds nothing left to publish.
Runs after ``mark_project_schedule_states``, which creates the column. Re-run with
``bench --site <site> execute erpnext_enhancements.patches.publish_active_job_schedules.execute``.
"""

import frappe

ACTIVE = "Active"
PUBLISHED = "Published"


def _has_column():
	try:
		return bool(frappe.db.has_column("Project", "custom_schedule_status"))
	except Exception:
		return False


def active_draft_jobs():
	"""The names of the Active customer jobs whose schedule is not Published, by the planner's rule."""
	from erpnext_enhancements.project_enhancements import crew_availability as engine

	type_expr, stream = engine.planner_job_sql("p")
	rows = frappe.db.sql(
		f"""
		SELECT p.name, {type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabProject` p
		WHERE p.status = %(active)s AND IFNULL(p.custom_schedule_status, '') <> %(published)s
		ORDER BY p.name
		""",
		{"active": ACTIVE, "published": PUBLISHED, **engine.PLANNER_SQL_VALUES},
		as_dict=True,
	)
	return [
		row.get("name") for row in rows or [] if engine.is_planner_job({"project": row.get("name"), **row})
	]


def publish(names):
	"""Set ``names`` Published, where each is still Active and not Published. How many rows changed."""
	if not names:
		return 0
	frappe.db.sql(
		"""
		UPDATE `tabProject` SET custom_schedule_status = %(published)s
		WHERE name IN %(names)s AND status = %(active)s AND IFNULL(custom_schedule_status, '') <> %(published)s
		""",
		{"published": PUBLISHED, "active": ACTIVE, "names": tuple(names)},
	)
	try:
		return int(frappe.db._cursor.rowcount or 0)
	except Exception:
		return len(names)


def execute():
	if not _has_column():
		print("publish_active_job_schedules: Project.custom_schedule_status is missing; nothing published.")
		return
	try:
		names = active_draft_jobs()
		changed = publish(names)
	except Exception:
		frappe.log_error(title="Planner: active jobs not published", message=frappe.get_traceback())
		print("publish_active_job_schedules: failed; logged. Re-run it with bench execute.")
		return
	shown = ", ".join(names[:60]) + (" …" if len(names) > 60 else "")
	print(
		f"publish_active_job_schedules: {len(names)} active customer job(s) on Draft, "
		f"{changed} set Published{': ' + shown if names else ''}."
	)

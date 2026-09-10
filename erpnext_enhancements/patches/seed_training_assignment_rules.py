"""Point the assignment engine at somebody, at last (v1.386.0).

Prod reached v1.385.0 with **zero** ``Training Assignment Rule`` rows,
``auto_assign = 0`` on all six courses, five ``Training Assignment`` records in
total ever, three completions, and **two of sixteen active employees holding any
training record at all**. The engine in ``training/assignment.py`` has been
complete since v1.207.0 and had simply never been aimed at anything, because the
only way to aim it was to hand-add a child row on the Course form and then
republish the course.

Nik asked for sensible defaults to review rather than a blank slate, so this
seeds two, both against courses that are already **Published** and **Required**:

* ``Using the Training Module`` → **All Employees.** If a course about how to use
  the training system is not assigned to everybody, nothing else here will be
  either.
* ``Draining a Fountain Basin Safely`` → the **Production** department. It is the
  only operational safety course on the site, it already requires a supervisor
  sign-off, and Production is where the technicians are.

Deliberately conservative in three ways.

**Matched by slug, not by docname.** ``TRN-CRS-00001`` is a naming-series
accident; the slug is what the course calls itself. A patch that hard-codes
document names is a patch that silently seeds the wrong course on any other site.

**Never touches a course that already has rules.** Somebody's own targeting is
not something a patch gets to overwrite, and the second run of this must be a
no-op even if the first run's rules were later edited or deleted on purpose.

**Nothing is assigned here.** ``assignment._active()`` refuses to run during a
patch — deliberately, since a migrate is not the moment to email fifteen people —
and enqueuing would be worse than useless, because the prod deploy ``FLUSHDB``s
the queue redis immediately afterwards and destroys the job. The rules are seeded
and the new daily sweep (``training.tasks.sweep_auto_assignments``, 06:40) raises
the assignments on its next run. Anybody impatient can press **Assign To…** on
the course.
"""

import frappe

# (course slug, applies_to, applies_to_doctype, applies_to_value, due_days)
RULES = (
	("using-the-training-module", "All Employees", "", None, 14),
	("draining-a-fountain-basin-safely", "Department", "Department", "Production", 30),
)


def execute():
	for slug, applies_to, target_doctype, target_value, due_days in RULES:
		course = frappe.db.get_value(
			"Training Course",
			{"slug": slug},
			["name", "status", "weight"],
			as_dict=True,
		)
		if not course:
			continue
		if course.status != "Published" or course.weight != "Required":
			# Seeding a rule onto a draft or an optional course would assign nobody
			# and leave a rule nobody remembers adding.
			continue

		doc = frappe.get_doc("Training Course", course.name)
		if doc.get("assign_rules"):
			continue

		if target_value and not frappe.db.exists(target_doctype, target_value):
			# The department was renamed, or this is another site. Say so rather
			# than seeding a rule that can never match anybody.
			print(
				f"[erpnext_enhancements] no {target_doctype} named {target_value!r}; "
				f"left {course.name} without a seeded rule"
			)
			continue

		doc.append(
			"assign_rules",
			{
				"enabled": 1,
				"applies_to": applies_to,
				"applies_to_doctype": target_doctype,
				"applies_to_value": target_value,
				"due_days": due_days,
			},
		)
		doc.auto_assign = 1
		doc.save(ignore_permissions=True)
		print(
			f"[erpnext_enhancements] {course.name}: auto-assign on, "
			f"targeting {target_value or applies_to}"
		)

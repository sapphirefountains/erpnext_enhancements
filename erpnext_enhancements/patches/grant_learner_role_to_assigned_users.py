"""Re-grant ``Training Learner`` to people who owe a course and cannot open it.

``grant_training_learner_to_employees`` did this once, in v1.208.0, keyed on being
an **active Employee**. That was right for the population that existed then and is
not enough afterwards, because the grant has no keeper: ``assignment.py`` calls
:func:`~erpnext_enhancements.training.roles.grant_learner_role` only on Employee
*insert* and on an Employee *gaining* a ``user_id``, and neither event fires again
for somebody who already exists. ``User.validate`` meanwhile rebuilds ``roles``
from the union of the user's Role Profiles on **every** save, so a direct grant is
one unrelated save away from being dropped — and nothing notices, because a
missing role is not an error, it is a person who opens training and is told there
is nothing there.

Two people on this site were in exactly that state when this was written: each
holds one open assignment, each is an active Employee with a login, and neither
holds the role or the dedicated Role Profile. Both carry four or five unrelated
profiles, which is the shape that makes the wipe happen.

This is the immediate repair. The keeper is
:func:`~erpnext_enhancements.training.tasks.sweep_learner_roles`, which runs daily
and does the identical thing, so the condition cannot silently return — without
it, this patch would be another one-off repair of a hole that reopens.

**Keyed on owing a course, not on being an Employee.** That is the rule the writer
applies: the assignment engine and a Training Manager pressing *New* both create
the obligation, and the obligation is what needs the role. Keying on Employee
again would miss a manually-assigned contractor; keying on "has no role" would
grant it to the whole company.

Idempotent, and every write is independent — ``grant_learner_role`` swallows and
logs its own failures, so one odd user cannot abort ``bench migrate``, which on
this repo *is* the deploy.
"""

import frappe


def execute():
	from erpnext_enhancements.training.roles import ROLE

	if not frappe.db.exists("Role", ROLE):
		# seed_training_roles runs first in patches.txt; if it somehow did not,
		# referencing a missing role would throw and abort the migrate.
		return

	try:
		from erpnext_enhancements.training.tasks import sweep_learner_roles

		sweep_learner_roles()
	except Exception:
		# A role backfill is not worth a failed deploy. Logged rather than
		# swallowed, because "nobody was granted anything" is the defect.
		frappe.log_error(
			f"Learner-role backfill failed\n{frappe.get_traceback()}", "Training role grant"
		)

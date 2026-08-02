"""Give every active employee's login the Training Learner role (v1.207.0).

Without this the first assignment lands on somebody who cannot open ``/training``,
which reads as the feature being broken rather than as a missing role. All 15
active employees on this site have a ``user_id``.

The grant itself is not a one-liner here — a profiled user's direct roles are
rebuilt from their profiles on every save, so ``add_roles`` silently evaporates
for 11 of the 15. The two-path logic (extra Role Profile for profiled users,
direct grant for profile-less ones, and never the other way round) lives in
``training/roles.py`` and is shared with the Employee ``after_insert`` hook that
handles new hires, so the two cannot drift apart.

Idempotent; every write is independent so one odd user cannot abort a migrate.
Deliberately does **not** touch Website Users — a customer gaining training access
is a decision somebody makes, not a migration side effect.
"""

import frappe

from erpnext_enhancements.training.roles import ROLE, ensure_profile, grant_learner_role


def execute():
	if not frappe.db.exists("Role", ROLE):
		# seed_training_roles runs first in patches.txt; if it somehow did not,
		# referencing a missing role would throw and abort the migrate.
		return

	ensure_profile()

	users = frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"]},
		pluck="user_id",
	)
	for user in sorted({u for u in users if u}):
		grant_learner_role(user)

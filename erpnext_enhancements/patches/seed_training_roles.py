"""Seed the three Training roles (v1.208.0).

**Training Learner must keep ``desk_access = 0``.** It is held by customer
contacts as well as staff; granting desk access would turn every one of them into
a System User and move the licensed-user count (and the bill). ``/training`` is a
website page and needs no desk access at all. ``tests/test_training_roles.py``
pins this.

Not a ``fixtures/role.json`` entry: fixtures import in alphabetical filename
order, so ``custom_docperm.json`` lands before ``role.json`` and any DocPerm row
naming a not-yet-created role is silently dropped. Same trap as
``seed_hr_team_role`` and ``seed_dispatch_user_role``.

Insert-only and idempotent; assign the roles to users (or to a Role Profile)
post-deploy.
"""

import frappe

ROLES = (
	("Training Author", 1),
	("Training Manager", 1),
	# desk_access 0 — see the module docstring. Do not "fix" this.
	("Training Learner", 0),
)


def execute():
	for role_name, desk_access in ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = desk_access
		role.insert(ignore_permissions=True)

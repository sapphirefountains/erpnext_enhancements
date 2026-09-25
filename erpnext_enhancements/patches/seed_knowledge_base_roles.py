"""Seed the two Knowledge Base roles, and the one-role "KB Approver" Role Profile (v1.538.0).

WI-080 PR 1, ADR 0017.

* **KB Author** writes drafts (``Knowledge Article Version``: read, create, write).
* **KB Approver** reviews and publishes someone else's draft. It carries the same DocPerm as KB
  Author; the approval rules that make the difference live in code (PR 2 and PR 3).

Both get ``desk_access = 1``: the whole Knowledge Base is Desk-only, and only staff hold either.

Not a ``fixtures/role.json`` entry: fixtures import in alphabetical filename order, so
``custom_docperm.json`` lands before ``role.json`` and any DocPerm row naming a not-yet-created
role is silently dropped (hooks.py, the WI-010 note on the ``Role`` fixture). Same shape as
``seed_training_roles``.

On most sites the two roles already exist when this runs. v16 model sync calls
``DocType.on_update`` -> ``make_module_and_roles`` (frappe ``origin/version-16``
``core/doctype/doctype/doctype.py:544``, ``:1968-1999``), which creates any role a DocPerm row
names, with ``desk_access = 1``. This patch is what the Knowledge Base relies on rather than that
side effect, and it states the ``desk_access`` requirement in one place.

**The "KB Approver" Role Profile.** The fourth approver, Lisa Symanski (approved by James,
2026-09-25), has the Role Profile "Finance Team". On this site ``User.validate`` rebuilds a
profiled user's ``roles`` from the union of their profiles on every save, so a role granted to her
directly would be wiped on her next save. The only way to give her KB Approver is a profile that
carries it, added in the Desk as her second profile. This patch creates that profile, with the one
role and no members. **Assigning it is a Desk step for Nik, not part of this patch**, and so is
granting either role to a user who has no profile (a direct grant is right for them, and a profile
would wipe their other roles).

Deliberately not in ``fixtures/role_profile.json`` either. Fixture sync deletes and re-inserts
every listed profile on every migrate, which re-fires ``RoleProfile.on_update`` and its queued
"re-save every member" job each time; insert-only means a Desk edit to this profile survives and
the job fires once.

``RoleProfile.on_update`` (``core/doctype/role_profile/role_profile.py``) ``queue_action``s that
job, which file-locks the profile until a worker runs it. The deploy FLUSHDBs the queue right
after migrate, so the lock would sit orphaned for up to 3 hours. With no members the job has
nothing to do, so this patch releases the lock itself; ``setup.document_locks`` sweeps any Role
Profile lock at the start of the next migrate as well.

Insert-only and idempotent: an existing role or profile is never touched, whatever it holds.
Every step is guarded and commits alone. **It cannot raise**: a patch that raises aborts
``bench migrate``, which is the deploy.
"""

import frappe

#: (role name, desk_access). Both 1 -- see the module docstring.
ROLES = (
	("KB Author", 1),
	("KB Approver", 1),
)

#: The one-role profile a profiled user needs to hold KB Approver. Same name as the role, on
#: purpose: it carries exactly that role and nothing else.
APPROVER_PROFILE = "KB Approver"
APPROVER_ROLE = "KB Approver"


def execute():
	for role_name, desk_access in ROLES:
		_seed_role(role_name, desk_access)
	_seed_approver_profile()


def _seed_role(role_name, desk_access):
	try:
		if frappe.db.exists("Role", role_name):
			return
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = desk_access
		role.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			f"seed_knowledge_base_roles: role {role_name} failed\n{frappe.get_traceback()}",
			"Knowledge Base role seed",
		)


def _seed_approver_profile():
	try:
		if not frappe.db.exists("DocType", "Role Profile"):
			return
		if frappe.db.exists("Role Profile", APPROVER_PROFILE):
			return
		if not frappe.db.exists("Role", APPROVER_ROLE):
			# The role step above failed and logged why. A profile pointing at a missing role
			# would be worse than no profile, so wait for the next run of a fixed patch.
			return
		profile = frappe.get_doc(
			{
				"doctype": "Role Profile",
				"role_profile": APPROVER_PROFILE,
				"roles": [{"role": APPROVER_ROLE}],
			}
		)
		profile.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			f"seed_knowledge_base_roles: Role Profile {APPROVER_PROFILE} failed\n{frappe.get_traceback()}",
			"Knowledge Base role seed",
		)
		return

	try:
		# Release the queue_action lock now rather than leaving it for a worker the deploy's
		# FLUSHDB will never let run. The new profile has no members, so the job has no work.
		profile.unlock()
	except Exception:
		pass

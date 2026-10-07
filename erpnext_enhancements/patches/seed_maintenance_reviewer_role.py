"""Seed the "Maintenance Reviewer" role and the "Send Back" workflow action (v1.576.1).

A maintenance visit is reviewed before it is billed. Until now the Sapphire Maintenance Workflow
let **Projects Manager** approve, and every technician holds Projects Manager, so a technician
could approve a colleague's visit, or their own whenever the nightly scheduler had drafted it (the
self-approval rule only looks at the record's owner, which is then Administrator). On 2026-10-07
Nik named the reviewers: Lisa Symanski, James Harris, Nikolas Bradshaw and Clegg Mabey. The
workflow now gives Approve & Submit, editing a visit in Pending Review, and the new Send Back
(Pending Review -> Draft, so a technician can fix a visit) to this role alone.

* ``desk_access = 1``: the reviewers work in the Desk.
* **No DocPerm.** Approving a visit needs submit on Sapphire Maintenance Record, which every
  reviewer already holds through Maintenance User or Projects Manager. The role only decides who
  the workflow lets act; a DocPerm would also have to exist before model sync, which runs before
  this patch.
* **Granted to nobody here.** Who reviews is a person's call, and on this site a user with a Role
  Profile can only receive a role through a profile (``User.validate`` rebuilds ``roles`` from the
  profiles on every save). Grant it in the Desk: directly for users without a profile, and through
  a one-role "Maintenance Reviewer" Role Profile added as an extra profile for those with one.
  Until someone holds it, nobody can approve a visit.
* **"Send Back" must exist before the workflow fixture names it.** Fixtures import in filename
  order and ``workflow.json`` sorts before ``workflow_action_master.json``, so the action master is
  created here, in ``post_model_sync``, which runs before fixture sync. So is the role, which the
  workflow's ``allowed`` and ``allow_edit`` link to.

Not a ``fixtures/role.json`` entry, for the reason ``seed_error_log_recipient_role`` gives.
Insert-only and idempotent. **It cannot raise**: a patch that raises aborts ``bench migrate``,
which is the deploy. A failure is logged as "Maintenance reviewer role seed"; the fix is to create
the role (Desk Access ticked) and the Workflow Action Master "Send Back" by hand, then migrate.
"""

import frappe

ROLE = "Maintenance Reviewer"
SEND_BACK = "Send Back"


def execute():
	try:
		if not frappe.db.exists("Role", ROLE):
			role = frappe.new_doc("Role")
			role.role_name = ROLE
			role.desk_access = 1
			role.insert(ignore_permissions=True)
		if not frappe.db.exists("Workflow Action Master", SEND_BACK):
			action = frappe.new_doc("Workflow Action Master")
			action.workflow_action_name = SEND_BACK
			action.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		try:
			frappe.db.rollback()
			frappe.log_error(
				title="Maintenance reviewer role seed",
				message=f"seed_maintenance_reviewer_role failed\n{frappe.get_traceback()}",
			)
		except Exception:
			# The database went away mid-migrate. The workflow fixture then fails to import on its
			# own, which the deploy log shows; a raise here would hide it behind this patch.
			pass

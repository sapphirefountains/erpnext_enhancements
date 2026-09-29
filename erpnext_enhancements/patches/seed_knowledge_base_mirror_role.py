"""Seed the "KB Mirror" role (v1.561.0). WI-080 PR 8, Slice 6.

The one role that may call ``api/knowledge_base_mirror.snapshot``: the published knowledge base as
Markdown, which the company's private knowledge repo pulls on a schedule. It is meant for one service
account, made by hand in the Desk as that repo's runbook describes; this patch creates the role only,
and grants it to nobody.

* ``desk_access = 0``. v16 makes a user a System User when any role they hold has desk access
  (frappe ``origin/version-16`` ``core/doctype/user/user.py:404-415``), so an account holding only
  this role stays a Website User: no Desk, and no ``Desk User`` role, which is what lets a staff user
  read published articles through the Desk.
* **No DocPerm anywhere.** The endpoint's role check is the whole grant: the role reads nothing
  through ``/api/resource``, a list or a report. ``tests/test_knowledge_base_schema.py`` fails the
  build if a doctype JSON or a fixture gives it a permission row.

Not a ``fixtures/role.json`` entry, for the reason ``seed_knowledge_base_roles`` gives (fixtures import
in alphabetical filename order, and fixture sync would re-insert the row on every migrate). Nor does
model sync make it, as it makes the other two KB roles: no DocPerm names it, so
``make_module_and_roles`` never will. Without this patch the endpoint refuses everyone but
Administrator.

Insert-only and idempotent: a role that exists is never touched, whatever it holds, so a Desk edit
survives. **It cannot raise**: a patch that raises aborts ``bench migrate``, which is the deploy. Frappe
records a patch that returns as run, so a failed insert is not retried: the Error Log "Knowledge Base
role seed" says so, the mirror gets 403 until the role exists, and the fix is to create "KB Mirror" in
the Desk with Desk Access unticked.
"""

import frappe

#: ``knowledge_base/constants.MIRROR_ROLE``, written out so that this patch imports nothing of the
#: app; ``tests/test_knowledge_base_schema.py`` asserts the two agree.
ROLE = "KB Mirror"
#: 0 -- see the module docstring.
DESK_ACCESS = 0


def execute():
	try:
		if frappe.db.exists("Role", ROLE):
			return
		role = frappe.new_doc("Role")
		role.role_name = ROLE
		role.desk_access = DESK_ACCESS
		role.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		try:
			frappe.db.rollback()
			frappe.log_error(
				f"seed_knowledge_base_mirror_role: role {ROLE} failed\n{frappe.get_traceback()}",
				"Knowledge Base role seed",
			)
		except Exception:
			# Even the rollback or the log failed (the database went away mid-migrate). The role's
			# absence then shows as a 403 for the mirror, never as a broken deploy.
			pass

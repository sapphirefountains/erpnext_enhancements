"""Seed the "Error Log Recipient" role (v1.561.3).

The ``Error Log`` alert (``fixtures/notification.json``) used to email **every System Manager**, so
anyone who needed admin rights also got every error email, and the only way to stop the emails was
to take the rights away. Nik asked on 2026-09-29 for James Harris and the ``triton@`` service
account to stop getting them, and chose to keep both on System Manager. The alert now emails this
role instead: who administers the system and who reads its errors are separate decisions.

* **It grants nothing.** No DocPerm anywhere; it is a mailing list, not a permission.
  ``tests/test_notification_recipients.py`` fails the build if a doctype JSON or a fixture gives it
  one.
* ``desk_access = 0``, so holding it never turns anyone into a System User.
* **It is granted to nobody here.** Who reads the errors is a person's call, made in the Desk
  (User → Roles → Error Log Recipient). Until someone holds it the alert has no recipients and sends
  nothing; the Error Log list itself is unchanged.

Not a ``fixtures/role.json`` entry: fixtures import in alphabetical filename order and fixture sync
would re-insert the row on every migrate. This patch runs in ``post_model_sync``, before fixture
sync, so the role exists by the time the alert's recipient row naming it is imported.

Insert-only and idempotent: a role that exists is never touched. **It cannot raise**: a patch that
raises aborts ``bench migrate``, which is the deploy. If the insert fails, the Error Log "Error Log
recipient role seed" says so, and the fix is to create the role in the Desk with Desk Access unticked.
"""

import frappe

ROLE = "Error Log Recipient"
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
				f"seed_error_log_recipient_role: role {ROLE} failed\n{frappe.get_traceback()}",
				"Error Log recipient role seed",
			)
		except Exception:
			# The database went away mid-migrate. The alert then has no recipients until the role
			# exists, which is quieter than a broken deploy.
			pass

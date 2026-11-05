"""Remove the Production Team role's permission rows on User (v1.797.1).

Why
---
On 2026-05-28 a ``Custom DocPerm`` row was added on production through Role Permissions Manager.
It gave the **Production Team** role read, write, create, delete, report, export, print and email
on **User** at permlevel 0. That puts other people's accounts in the hands of everyone who holds
the role. Changing someone else's account is System Manager's job; in frappe's own User DocType it
is the only role with write.

The row was never in this repo. The ``Custom DocPerm`` fixture names only Material Request,
Purchase Order and Voice Call Settings, so no JSON change can remove it. This patch is the
"delete" half of the two-step rule in CLAUDE.md, and the only step needed.

Nik chose a patch over removing the row by hand in the Desk (2026-11-05), so the removal is
versioned and runs on every site that deploys this release.

What it does
------------
It lists every ``Custom DocPerm`` row whose ``parent`` is ``User`` and whose ``role`` is
``Production Team``, at any permlevel (production has one, at level 0). It deletes each one the way
Role Permissions Manager's own Remove button does (``permission_manager.remove``):
``frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)``. That route
leaves a Deleted Document and a Permission Log behind, which is the audit trail a permission change
should have. Then it clears User's cached meta, so the permissions are re-read.

It touches nothing else: other roles' rows on User stay, and so do Production Team's rows on other
doctypes. Members keep the role and everything else it grants. Each person can still open and edit
their own User record, because frappe shares every user's own record with them on save
(``User.share_with_self``), not through this row.

If these were ever the only ``Custom DocPerm`` rows on User, deleting them would hand User back to
its standard ``DocPerm`` rows: System Manager in full and Desk User with Select. That is safe too.
Production keeps four other custom rows on User, so its custom set still applies.

It never raises. A patch that raises aborts ``bench migrate``, which on this repo is the deploy
(v1.395.0). A failure is written to the Error Log and the patch returns. It is safe to run twice,
and safe on a site where someone already removed the row by hand: it then finds nothing and does
nothing.
"""

import frappe

DOCTYPE = "User"
ROLE = "Production Team"


def execute() -> None:
	try:
		remove_production_team_user_docperm()
	except Exception:
		frappe.log_error(title="remove_production_team_user_docperm: failed")


def remove_production_team_user_docperm() -> list[str]:
	"""Delete Production Team's Custom DocPerm rows on User; return the names deleted."""
	names = frappe.get_all("Custom DocPerm", filters={"parent": DOCTYPE, "role": ROLE}, pluck="name")
	if not names:
		return []

	for name in names:
		frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)

	frappe.clear_cache(doctype=DOCTYPE)
	print(f"remove_production_team_user_docperm: deleted {len(names)} {ROLE} row(s) on {DOCTYPE}")
	return names

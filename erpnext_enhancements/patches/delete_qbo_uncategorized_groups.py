"""Delete the three ``Uncategorized`` leaves v1.496.0 seeded under All Supplier Groups, All
Customer Groups and All Territories (v1.497.0).

Why
---
The first cut of v1.496.0 (PR #1070, deployed 2026-09-22 15:35 UTC) made ``Uncategorized`` the
QuickBooks importer's default group for a new Supplier or Customer and seeded that leaf in all
three trees. Eighteen minutes later PR #1071 replaced the design: the importer files a new party
into **no** group and never writes one on update (a wrong group is worse than no group -- Nik,
2026-09-22), and the seed patch went away. The leaves it had already created did not. Nothing was
ever filed under them, and an empty "Uncategorized" in a group picker invites exactly the filing
the no-default design rejects, so this removes them.

What it does
------------
For each of the three doctypes, ``frappe.delete_doc(doctype, "Uncategorized")`` when the DocType
and the record exist. ``delete_doc`` refuses a record any other doctype still links to
(``LinkExistsError``): a leaf that did pick up a reference in that window -- a Pricing Rule, a
Tax Rule, a Customer someone filed by hand -- is logged and left alone, never forced, because
the reference is a person's choice and the record it points at must keep existing.

Never raises. A patch that raises aborts ``bench migrate``, which on this repo is the deploy,
and the failure mode is a half-finished one (v1.395.0). A missing DocType or record is a quiet
skip; a refused or failed delete is an Error Log entry. Safe twice: the second run finds nothing.
"""

import frappe

UNCATEGORIZED = "Uncategorized"
GROUP_DOCTYPES = ("Supplier Group", "Customer Group", "Territory")


def execute() -> None:
	delete_uncategorized_groups()


def delete_uncategorized_groups() -> list[str]:
	"""Delete the ``Uncategorized`` leaf from each tree that has one; return the doctypes deleted from."""
	deleted: list[str] = []
	for doctype in GROUP_DOCTYPES:
		try:
			if not frappe.db.exists("DocType", doctype):
				continue
			if not frappe.db.exists(doctype, UNCATEGORIZED):
				continue
			frappe.delete_doc(doctype, UNCATEGORIZED, ignore_permissions=True)
			deleted.append(doctype)
		except Exception:
			# A linked record (LinkExistsError) or any other failure: log it, keep going, and
			# leave the leaf where a person can decide. One refusal must not abort the migrate.
			frappe.log_error(
				f"delete_qbo_uncategorized_groups: could not delete {doctype} {UNCATEGORIZED!r}\n"
				f"{frappe.get_traceback()}",
				"QBO Uncategorized Group Cleanup Error",
			)
	if deleted:
		frappe.db.commit()
	print(
		f"delete_qbo_uncategorized_groups: deleted {UNCATEGORIZED!r} from {deleted or 'nothing (none present)'}"
	)
	return deleted

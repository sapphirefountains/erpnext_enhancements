"""Seed the ``Uncategorized`` leaf under All Supplier Groups, All Customer Groups and All
Territories -- the deterministic default the QuickBooks importer files a new party into
(v1.496.0).

Why a named leaf
----------------
QuickBooks has no supplier group, customer group or territory, so the importer has to pick
one for every Supplier and Customer it creates. Until v1.496.0 the pick was
``frappe.db.get_value(doctype, {"is_group": 0}, "name")`` -- "any leaf" -- and on Frappe v16 a
dict-filtered ``get_value`` with no ``order_by`` sorts by ``creation`` **descending**, so
"any" meant "the one somebody created most recently". The update path re-applied every
mapped value on each re-sync, so each new Supplier Group anyone added became, on the next
scheduled run, the group of all 911 QBO-linked Suppliers at once (Staffing -> Event Decor ->
Encapsulant -> Labels -> Garbage & Junk Removal between 2026-06-18 and 2026-09-16), and 466
Customers landed in "Government" the same way. The mapper now returns
``constants.DEFAULT_PARTY_GROUP`` by name and only when it exists; this patch is what makes
it exist. ``core/party_group_remediation.py`` puts the swept records back.

Insert-only and idempotent, keyed on the name: running it twice creates nothing and rewrites
nothing, so a site that renames or re-parents the leaf keeps its edit (the mapper reads the
record by name, and a renamed leaf simply means new parties land group-less until the name
is restored). A seed rather than a fixture for the reason ``seed_budget_categories`` gives --
fixture sync deletes and re-inserts each document from its JSON on every migrate.

Also on ``after_migrate`` as the backstop for a site whose Patch Log already has the entry
(or where somebody deleted the leaf): the mapper depends on the record existing and cannot
create it itself -- a mapper is a transform, not a writer.

Never raises. A patch that raises aborts ``bench migrate``, which on this repo is the deploy,
and the failure mode is a half-finished one: schema synced, fixtures not, ``after_migrate``
never run, ``__version__`` reporting the new release. A missing root (the setup wizard has not
run) or a missing DocType is a quiet skip; an insert that fails is an Error Log entry.
"""

import frappe

from erpnext_enhancements.quickbooks_online.core.constants import (
	DEFAULT_PARTY_GROUP,
	PARTY_GROUP_DOCTYPES,
)


def execute() -> None:
	seed_uncategorized_groups()


def seed_uncategorized_groups() -> list[str]:
	"""Create the ``Uncategorized`` leaf under each party-group root that lacks one.

	Returns the doctypes a leaf was created in (empty when every one already existed).
	Safe to call on every migrate; it is the ``after_migrate`` entry as well as the patch.
	"""
	created: list[str] = []
	for doctype, root, name_field, parent_field in PARTY_GROUP_DOCTYPES.values():
		try:
			if not frappe.db.exists("DocType", doctype):
				continue
			if frappe.db.exists(doctype, DEFAULT_PARTY_GROUP):
				continue
			if not frappe.db.exists(doctype, root):
				# The roots come from the ERPNext setup wizard; without one there is
				# nothing to nest under and nothing the importer could run against either.
				continue
			frappe.get_doc(
				{
					"doctype": doctype,
					name_field: DEFAULT_PARTY_GROUP,
					parent_field: root,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
			created.append(doctype)
		except Exception:
			# One failed leaf must not abort the migrate; the other two still seed.
			frappe.log_error(
				f"seed_qbo_uncategorized_groups: could not create {doctype} "
				f"{DEFAULT_PARTY_GROUP!r}\n{frappe.get_traceback()}",
				"QBO Uncategorized Group Seed Error",
			)
	if created:
		frappe.db.commit()
	print(
		f"seed_qbo_uncategorized_groups: created {DEFAULT_PARTY_GROUP!r} in {created or 'nothing (all present)'}"
	)
	return created

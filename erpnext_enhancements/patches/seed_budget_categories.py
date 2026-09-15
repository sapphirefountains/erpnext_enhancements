"""Seed the seven project budget categories — WI-075 sub-phase M, v1.462.0.

Insert-only and idempotent, keyed on the category name. Running it twice creates nothing and
**overwrites nothing** — in particular it will not reset a description somebody has rewritten or
a source somebody has re-pointed, and it will not re-tick a ``disabled`` category. The one thing
that is re-asserted is protection, and only in the direction that adds it: see below.

Seeding rather than fixturing, for the reason ``seed_rental_asset_setup`` gives — Finance edits
these afterwards and fixture sync deletes and re-inserts each document from its JSON on every
migrate, so an edited description would be silently reverted every deploy.

What this changes on the day it runs: **nothing anybody sees.** Seven rows appear in a new
catalog. No project gains a budget line, ``Project.estimated_costing`` is untouched on all 654
projects, and the rollup that reads these categories returns on its first line for a project with
no lines. The categories are a vocabulary waiting to be used.

Protection is restored, never removed
-------------------------------------

``budgets.PROTECTED`` is the source of truth for which categories need a second approval, and the
``is_protected`` Check on the row is its display. This patch re-asserts the flag on an existing
row that has lost it, because a protection quietly unticked in the Desk removes the control with
no diff anywhere. It never *clears* the flag on a row that has it: a site that decides Equipment
should be protected too may tick it, and nothing here will take that back.
"""

import frappe

from erpnext_enhancements.quality import budgets

DOCTYPE = "Project Budget Category"


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo is the deploy — and the
	# failure mode is not a stopped deploy but a half-finished one: schema synced, fixtures not,
	# `after_migrate` hooks never run, and `__version__` reporting the new release. So the guard
	# is a quiet return rather than an assertion.
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	created = 0
	protected_restored = 0

	for row in budgets.seed_rows():
		name = row["category_name"]
		if frappe.db.exists(DOCTYPE, name):
			protected_restored += _restore_protection(name, row["is_protected"])
			continue

		doc = frappe.get_doc({"doctype": DOCTYPE, **row})
		doc.insert(ignore_permissions=True)
		created += 1

	if created or protected_restored:
		frappe.db.commit()

	print(
		f"seed_budget_categories: {created} created, "
		f"{protected_restored} protection flag(s) restored"
	)


def _restore_protection(name: str, should_be_protected: int) -> int:
	"""Re-tick protection on an existing row that has lost it. Never unticks."""
	if not should_be_protected:
		return 0
	if frappe.db.get_value(DOCTYPE, name, "is_protected"):
		return 0
	frappe.db.set_value(DOCTYPE, name, "is_protected", 1)
	return 1

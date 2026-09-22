"""Put every Supplier the QuickBooks importer swept into "Garbage & Junk Removal" back
where it belongs -- or in no group at all -- and apply the 2026-09-22 audit's hand-curated
supplier corrections (v1.496.0).

Why
---
Until v1.496.0 the importer's default supplier group was ``frappe.db.get_value("Supplier
Group", {"is_group": 0}, "name")``, which on Frappe v16 orders by ``creation`` DESC and so
answered "the leaf somebody created most recently"; the update path re-applied it on every
re-sync. All 911 QBO-linked Suppliers were moved en bloc each time anyone added a Supplier
Group: Staffing (2026-06-18) -> Event Decor -> Encapsulant -> Labels -> Garbage & Junk
Removal (2026-09-16, 906 rows). The forward fix is in ``core/mapping.py``; this patch is the
data half, and it applies Nik's rule of 2026-09-22: **a wrong group is worse than no group.**

What it does
------------
Two passes from ``quickbooks_online/core/party_group_remediation.py``, Suppliers only:

1. ``restore_party_groups(doctype="Supplier", include_sync_created=True)``: for every
   QBO-linked Supplier in "Garbage & Junk Removal", the OLD value of the first Version
   change whose NEW value is a sweep landing group is the pre-sweep group -- a real one is
   **restored** (about 63 on prod), anything else (empty, itself a landing group, deleted
   since) is **cleared** to NULL, and a record with no history at all is cleared only when
   its mapping says the import created it. A Supplier a person filed under a landing group
   on purpose (no mapping) is never touched.
2. ``apply_curated_supplier_groups``: the audit's ``supplier_group_corrections.json`` --
   about 230 Suppliers whose group is settled by a duplicate record of the same company, the
   trade in the name, the record's notes, or a group created for that supplier minutes before
   a sweep moved it (Kajae -> Staffing, Taiwan Imports -> Event Decor, Wesco / Anixter ->
   Encapsulant, Dumpster Depot -> Garbage & Junk Removal), plus the audit's misplacements
   (Stephanie Atwood -> Stone - Marble & Granite; Sapphire Fountains -> no group). The
   curated value wins over the history walk, so it runs second. An entry whose Supplier or
   group is missing is skipped and reported.

Writes are ``frappe.db.set_value`` (no doc hooks, 900 times over) with the Supplier's two
denormalized search fields recomputed alongside. Customers in "Government" are **not**
touched here -- see the remediation module's docstring.

Never raises. A patch that raises aborts ``bench migrate``, which on this repo is the
deploy, leaving the site half-migrated with ``__version__`` reporting the new release
(v1.395.0). Both passes are per-record guarded inside; this wrapper guards the passes
themselves and turns any failure into an Error Log entry. Safe twice: a restored or cleared
Supplier has left the landing group, and a curated entry already holding its value is a
no-op.
"""

import frappe


def execute() -> None:
	restore_supplier_groups()


def restore_supplier_groups() -> dict:
	"""Run both passes with ``apply=True``; return their reports (also printed to the migrate log)."""
	from erpnext_enhancements.quickbooks_online.core import party_group_remediation as remediation

	report = {"sweep": None, "curated": None}
	try:
		report["sweep"] = remediation.restore_party_groups(
			apply=True, doctype="Supplier", verbose=False, include_sync_created=True
		)
	except Exception:
		frappe.log_error(
			f"restore_supplier_groups_after_qbo_sweep: sweep restore failed\n{frappe.get_traceback()}",
			"QBO Party Group Remediation Error",
		)
	try:
		report["curated"] = remediation.apply_curated_supplier_groups(apply=True, verbose=False)
	except Exception:
		frappe.log_error(
			f"restore_supplier_groups_after_qbo_sweep: curated corrections failed\n{frappe.get_traceback()}",
			"QBO Party Group Remediation Error",
		)
	return report

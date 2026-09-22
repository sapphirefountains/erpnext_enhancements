"""Put every Customer the QuickBooks importer swept into "Government", and every territory it
swept into "Asia" or "United States of America", back where it belongs -- or leave it blank --
and keep the customers that really are government bodies (v1.498.0).

Why
---
Until v1.496.0 the importer's default customer group and territory were
``frappe.db.get_value(doctype, {"is_group": 0}, "name")``, which on Frappe v16 orders by
``creation`` DESC and so answered "the leaf somebody created most recently"; the update path
re-applied them on every re-sync. Verified on prod 2026-09-22: 466 QBO-linked Customers sat in
"Government" (the last of four leaves seeded in one second on 2025-07-08), one of which had a
real group before (Commercial); and 355 Customers had their territory written to "Asia" by the
2026-06-18 import -- 63 of them over a real value, 45 of those Utah -- then blanked by a manual
clear on 06-23 (real values included), then 178 re-filed under "United States of America" by the
08-19 sync once that leaf was the newest. The forward fix is in ``core/mapping.py`` (v1.496.0);
this patch is the Customer data half of Nik's rule of 2026-09-22: **a wrong group is worse than
no group.**

What it does
------------
Two passes from ``quickbooks_online/core/party_group_remediation.py``, Customers only:

1. ``restore_party_groups(doctype="Customer", clear_no_history=True)`` over both fields: the OLD
   value of the first Version change whose NEW value is a landing value is the pre-sweep value
   -- a real one is **restored** (1 group; ~63 territories, the lost Utahs among them), anything
   else is **cleared** to NULL, and a no-history record is cleared too, because every candidate
   is QBO-linked and the landing value is the sync's default. A blank territory whose history
   shows the sweep is a candidate as well (that is how the lost values come back). A record a
   person re-set to something else after the sweep is left alone.
2. ``apply_curated_party_values("Customer")``: ``core/customer_corrections.json`` -- the 26
   customers that really are government bodies (cities, counties, the state, public schools,
   universities, recreation districts) stay in Government, and the one territory a person set
   by hand the day the leaf was created is kept. The sweep pass skips these names; an entry
   whose record or leaf is missing is skipped and reported.

Writes are ``frappe.db.set_value`` (no doc hooks). Never raises: a patch that raises aborts
``bench migrate``, which on this repo is the deploy (v1.395.0). Both passes are per-record
guarded inside; this wrapper guards the passes themselves and turns any failure into an Error
Log entry. Safe twice: a restored or cleared Customer is no longer a candidate, and a curated
entry already holding its value is a no-op.
"""

import frappe


def execute() -> None:
	restore_customer_groups()


def restore_customer_groups() -> dict:
	"""Run both passes with ``apply=True``; return their reports (also printed to the migrate log)."""
	from erpnext_enhancements.quickbooks_online.core import party_group_remediation as remediation

	report = {"sweep": None, "curated": None}
	try:
		report["sweep"] = remediation.restore_party_groups(
			apply=True, doctype="Customer", verbose=False, include_sync_created=True, clear_no_history=True
		)
	except Exception:
		frappe.log_error(
			f"restore_customer_groups_after_qbo_sweep: sweep restore failed\n{frappe.get_traceback()}",
			"QBO Party Group Remediation Error",
		)
	try:
		report["curated"] = remediation.apply_curated_party_values("Customer", apply=True, verbose=False)
	except Exception:
		frappe.log_error(
			f"restore_customer_groups_after_qbo_sweep: curated corrections failed\n{frappe.get_traceback()}",
			"QBO Party Group Remediation Error",
		)
	return report

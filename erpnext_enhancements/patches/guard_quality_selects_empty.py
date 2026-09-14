"""Refuse to narrow a Select once rows depend on the wider one (v1.449.0, WI-075 E).

Sub-phase E replaces the ``status`` options on ``Non Conformance`` and ``Quality Action``. That
is free **only** while those tables are empty, which production was on 2026-09-14. It stops
being free the moment somebody saves a record holding a value the new list does not offer,
because that row then cannot be saved again by anybody, ever, and no ``ignore_*`` flag helps.

``migrate_quality_status_values`` handles every literal core itself writes. This patch exists
for the case that one cannot: a value nobody anticipated — typed by a script, imported, or left
by a future ERPNext release — sitting in a table about to have its options narrowed.

It does not raise. A patch that raises aborts ``bench migrate``, which on this repo is the
deploy, and stranding a site half-installed to protect a Select would be a far worse outcome
than the thing it was protecting against. It writes a **loud** Error Log instead, naming the
rows, so somebody can fix them by hand.

Runs ``pre_model_sync``, after the remap, for the same reason: fixtures land after patches.
"""

import frappe

from erpnext_enhancements.quality.lifecycle import ACTION_STATUSES, NCR_STATUSES

GUARDED = (
	("Non Conformance", NCR_STATUSES),
	("Quality Action", ACTION_STATUSES),
)


def execute() -> None:
	for doctype, allowed in GUARDED:
		try:
			_check(doctype, allowed)
		except Exception:
			frappe.log_error(
				title=f"guard_quality_selects_empty: {doctype}",
				message=frappe.get_traceback(),
			)


def _check(doctype, allowed):
	if not frappe.db.exists("DocType", doctype):
		return

	rows = frappe.get_all(doctype, fields=["name", "status"])
	stranded = [r for r in rows if (r.status or "") not in allowed]
	if not stranded:
		frappe.logger().info(
			f"guard_quality_selects_empty: {doctype} clean ({len(rows)} rows checked)"
		)
		return

	listed = ", ".join(f"{r.name} ({r.status!r})" for r in stranded[:50])
	frappe.log_error(
		title=f"Quality: {len(stranded)} {doctype} rows will be unsaveable",
		message=(
			f"WI-075 sub-phase E narrows {doctype}.status to {list(allowed)}.\n\n"
			f"These rows hold a value outside that list and will refuse to save until somebody "
			f"corrects them:\n\n{listed}\n\n"
			"Fix with frappe.db.set_value (NOT the document API, which will not save them "
			"either). This did not fail the migrate on purpose: a patch that raises aborts "
			"bench migrate, which on this repo is the deploy."
		),
	)

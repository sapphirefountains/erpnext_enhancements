"""Remap core's status literals before the new options replace them (v1.451.0, WI-075 E).

``Non Conformance`` ships ``Open / Resolved / Cancelled`` and ``Quality Action`` ships
``Open / Completed``. Sub-phase E replaces both with the lifecycles the build spec describes,
and a stored value outside a Select's options makes the row **permanently unsaveable** — no
``ignore_*`` flag bypasses ``_validate_selects``.

Measured on production 2026-09-14, both tables hold **zero rows**, so today this patch remaps
nothing. It ships anyway, for two reasons:

* the window is open now and closes the first time anybody saves one of these records, and a
  patch that exists is a patch that runs on whatever a site turns out to hold;
* a site restored from an older backup, or a second site, may not be empty.

**It logs its counts, and that is not decoration.** A patch that matched nothing commits and
writes its ``tabPatch Log`` row indistinguishably from one that did the work — the v1.280.3
failure verbatim, where a backfill keyed on emptiness matched zero rows and was recorded as a
success. The deploy log is the only witness that tells the two apart, so the counts go there.

Runs ``pre_model_sync``: fixtures — and therefore the Property Setters that narrow the options —
are applied after patches, so this must remap the rows while the old options are still in force.

Never raises. A patch that raises aborts ``bench migrate``, which on this repo is the deploy.
"""

import frappe

from erpnext_enhancements.quality.lifecycle import ACTION_LEGACY_MAP, NCR_LEGACY_MAP

REMAPS = (
	("Non Conformance", NCR_LEGACY_MAP),
	("Quality Action", ACTION_LEGACY_MAP),
)


def execute() -> None:
	for doctype, mapping in REMAPS:
		try:
			_remap(doctype, mapping)
		except Exception:
			# Log and carry on. Nothing here is worth failing a deploy over, and the next
			# migrate will try again -- unlike a patch that raises, which leaves the site
			# schema-synced, half-patched, and reporting the new version string.
			frappe.log_error(
				title=f"migrate_quality_status_values: {doctype}",
				message=frappe.get_traceback(),
			)


def _remap(doctype, mapping):
	if not frappe.db.exists("DocType", doctype):
		return
	total = frappe.db.count(doctype)
	moved = {}
	for old, new in mapping.items():
		names = frappe.get_all(doctype, filters={"status": old}, pluck="name")
		for name in names:
			# db.set_value rather than the document API: the doc would run its controller,
			# which for Quality Action is precisely the thing being replaced in this release.
			frappe.db.set_value(doctype, name, "status", new, update_modified=False)
		if names:
			moved[f"{old}->{new}"] = len(names)

	frappe.logger().info(
		f"migrate_quality_status_values: {doctype} holds {total} rows; remapped {moved or 'nothing'}"
	)

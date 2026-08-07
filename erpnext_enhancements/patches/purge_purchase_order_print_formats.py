# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Delete the two abandoned custom Purchase Order print formats (TASK-2026-01237).

"Purchase Order - Sapphire" is the only PO format we print. Of the five others on
the site, **only these two can actually be deleted**:

* ``Test Purchase Order Format`` — a builder experiment last touched 2025-10-23,
  and the reason ``setup_print_formats.CHROME_EXCLUDED_FORMATS`` existed: its
  header was shorter than its body, which trips an unbounded index in frappe's
  ``pdf_generator/pdf_merge.py``. That note said the guard stays "until either the
  format is deleted or upstream bounds that index". This is the deletion.
* ``PO Test Print Format`` — the same kind of leftover.

The other three (``Purchase Order Standard``, ``Purchase Order with Item Image``,
``Drop Shipping Format``) ship with ERPNext. Deleting a standard format is
pointless: it re-syncs from erpnext's JSON on the next ``bench migrate``, so the
purge would appear to work and quietly undo itself. They are *disabled* instead,
on every migrate, by ``setup_print_formats.disable_superseded_print_formats``.

Defensive in the shape this repo uses elsewhere (see
``drop_orphan_project_note.py``): existence guard, then a reference check that
**logs and bails rather than deleting**, then the delete. Idempotent — a second
run finds nothing and does nothing.
"""

import frappe

DOOMED = ("Test Purchase Order Format", "PO Test Print Format")

# Every column on this site that can name a print format, from
# INFORMATION_SCHEMA at the time of writing. A format still referenced by one of
# these is left alone and logged: a dangling Link is a worse outcome than an
# extra row in a dropdown.
REFERENCE_COLUMNS = (
	("Auto Repeat", "print_format"),
	("Notification", "print_format"),
	("Payment Request", "print_format"),
	("POS Profile", "print_format"),
	("Process Statement Of Accounts", "print_format"),
	("Training Certificate", "print_format"),
	("Training Course", "certificate_print_format"),
	("Web Form", "print_format"),
	("DocType", "default_print_format"),
)


def _references(name):
	"""Docs still pointing at ``name``, as ``["DocType.field -> docname", ...]``."""
	found = []
	for doctype, fieldname in REFERENCE_COLUMNS:
		# A doctype from an app that is not installed, or a field added after this
		# list was written, must not turn a cleanup patch into a failed migrate.
		if not frappe.db.exists("DocType", doctype):
			continue
		if not frappe.db.has_column(doctype, fieldname):
			continue
		for row in frappe.get_all(doctype, filters={fieldname: name}, pluck="name"):
			found.append(f"{doctype}.{fieldname} -> {row}")

	# Property Setters name their target in `value`, not in a Link column.
	if frappe.db.exists("DocType", "Property Setter"):
		for row in frappe.get_all("Property Setter", filters={"value": name}, pluck="name"):
			found.append(f"Property Setter -> {row}")
	return found


def execute():
	for name in DOOMED:
		if not frappe.db.exists("Print Format", name):
			continue

		referenced = _references(name)
		if referenced:
			frappe.log_error(
				f"Skipped deleting print format '{name}': still referenced by "
				f"{', '.join(referenced)}. Repoint or clear those first.",
				"Purchase Order print format purge",
			)
			continue

		frappe.delete_doc("Print Format", name, force=True, ignore_permissions=True)

	frappe.db.commit()

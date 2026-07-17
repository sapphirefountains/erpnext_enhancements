"""One-time migration patch (post_model_sync; listed in patches.txt).

Removes the legacy EMPTY duplicate "Comments" tab-break that coexisted with the
real Comments tab on 12 doctypes (v1.159.2).

Background: the canonical Comments UX is a ``custom_comments`` Tab Break (label
"Comments") immediately followed by the ``custom_comments_field`` HTML widget
(the Comments-app block). A number of doctypes had accumulated a *second*
"Comments" Tab Break — usually ``custom_comments_tab`` — that sat empty (no field
under it before the next Tab Break), rendering as a duplicate, clickable, blank
"Comments" tab. On three doctypes the roles were inverted (``custom_comments``
was the empty one and ``custom_comments_tab`` held the widget).

Which tab is empty was determined per-doctype from the live meta (the one WITHOUT
``custom_comments_field`` directly under it). The fixtures own the surviving
tab + widget; this patch only deletes the empty orphan. The fixtures also repoint
the surviving ``custom_comments_field`` (and a couple of sibling fields) off the
now-deleted tab so no dangling ``insert_after`` is left behind, and drop the
deleted field from each ``field_order`` property setter.

Project note: ``setup.custom_fields.create_comments_tab`` used to run for Project
too and would re-create the empty ``custom_comments_tab`` after this deletion;
that call was removed (Project's Comments tab now comes from the fixtures like
every other doctype — only Master Project still relies on create_comments_tab).

Contact is intentionally NOT touched: its second "Comments" tab is not empty
(it holds a "More Information" section and contact fields) — a mislabel, tracked
separately.

Deletes via ``delete_doc`` (Tab Break fields carry no DB column, so this is pure
metadata). Idempotent — matches nothing once cleaned.
"""

import frappe

# (doctype, empty duplicate Comments Tab Break to remove)
_TARGETS = [
	("Address", "custom_comments_tab"),
	("Batch", "custom_comments_tab"),
	("Delivery Note", "custom_comments_tab"),
	("Lead", "custom_comments_tab"),
	("Project", "custom_comments_tab"),
	("Quotation", "custom_comments_tab"),
	("Serial No", "custom_comments_tab"),
	("Stock Entry", "custom_comments_tab"),
	("Task", "custom_comments_tab"),
	("Employee", "custom_comments"),
	("Purchase Order", "custom_comments"),
	("Supplier Quotation", "custom_comments"),
]


def execute():
	touched = set()
	for dt, fieldname in _TARGETS:
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname}, "name")
		if not name:
			continue
		frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
		touched.add(dt)

	for dt in touched:
		frappe.clear_cache(doctype=dt)

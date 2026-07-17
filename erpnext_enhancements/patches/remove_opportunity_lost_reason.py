"""One-time migration patch (post_model_sync; listed in patches.txt).

Removes ``custom_lost_reason`` + ``custom_lost_competitor`` from Opportunity
(v1.159.0). Both duplicated capture that ERPNext already ships natively on the
Lost section of the form:

  * ``lost_reasons`` — Table MultiSelect of ``Opportunity Lost Reason Detail``
    rows linking the ``Opportunity Lost Reason`` master (a real, curated
    taxonomy) — replaces the hardcoded ``custom_lost_reason`` Select.
  * ``competitors`` — Table MultiSelect of ``Competitor Detail`` rows linking
    the ``Competitor`` master — replaces the free-text ``custom_lost_competitor``.

The duplicate was only ever added (v1.122.0) because a Property Setter hid the
native ``lost_reasons`` field, making it invisible to the win/loss KPI work;
v1.159.0 unhides it via the fixtures, so the native field is the single source
of truth again.

Data safety — verified on both sites before writing this patch: the two columns
are empty on test, and on production only three Opportunities carried a
``custom_lost_reason`` (all "Other"), each of which already had an equal or more
specific native ``lost_reasons`` row ("Other", "Competitor Loss", "Other"). So
nothing is migrated here: dropping the columns loses no information. The native
table meanwhile already holds ~100 rows across ~80 Opportunities.

Deletes via ``delete_doc`` so each field's ``on_trash`` also drops the DB column.
Idempotent — matches nothing once cleaned.
"""

import frappe


def execute():
	deleted = False
	for fieldname in ("custom_lost_competitor", "custom_lost_reason"):
		name = frappe.db.get_value("Custom Field", {"dt": "Opportunity", "fieldname": fieldname}, "name")
		if not name:
			continue
		frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
		deleted = True

	if deleted:
		frappe.clear_cache(doctype="Opportunity")

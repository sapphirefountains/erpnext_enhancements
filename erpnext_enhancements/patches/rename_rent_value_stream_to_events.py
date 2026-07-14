"""Rename the "Rent" value stream to "Events" (OD-3, 2026-07-14).

The business ruled that Rent and Events are ONE value stream and the term
changes to **Events**. The name "Rent" is load-bearing in three master
records and several plain-string data columns; the matching code/fixture
literals ship in the same release as this patch so the rename lands
atomically (the KPI snapshot SQL, dashboards, tag sync, and the Closed-Won
handoff all compare against the stream name).

What this patch renames (masters — ``rename_doc`` updates every Link
reference, including the 61+ ``Project.project_type`` values and the
``Value Stream`` child rows on Customer / Opportunity / Project):

* **Project Type** ``Rent`` -> ``Events`` (autoname ``field:project_type``)
* **Value Streams** ``Rent`` -> ``Events`` (the Table-MultiSelect master)
* **Tag** ``Rent`` -> ``Events`` (the Opportunity tag-sync tag)

Plain-string data the link-rename cannot reach:

* ``Value Streams.value_stream`` Data field on the renamed master (the
  dashboard/tag logic expects name == value_stream)
* residual ``tabValue Stream`` child rows that stored the stream as text
* ``Lead.custom_service_interest`` Select values (options change ships via
  the custom_field fixture in this release)
* ``_user_tags`` strings on Opportunity/Project/Customer (tag caches)
* Process Document Step ``target_artifact`` documentation text

Deliberately NOT renamed (identifier stability): the child DocTypes
``Rent Customer Requests`` / ``Rent Deliverables``, every ``custom_rent_*``
fieldname, and the ``rent_guidelines`` field — labels changed in fixtures /
doctype JSON only.

Idempotent: every step no-ops when the source record/value is absent, so
re-migrations and fresh sites are safe.
"""

import frappe


def _rename(doctype, old="Rent", new="Events"):
	"""Rename old->new, merging when the target already exists."""
	if not frappe.db.exists(doctype, old):
		return
	merge = bool(frappe.db.exists(doctype, new))
	frappe.rename_doc(doctype, old, new, force=True, merge=merge)


def _retag(table):
	"""Swap the 'Rent' entry inside cached ``_user_tags`` strings.

	``_user_tags`` is a comma-delimited cache (typically ``,A,B``); split and
	rejoin per row so leading/trailing comma conventions are preserved
	exactly and substrings like 'Rented' are never touched.
	"""
	rows = frappe.db.sql(
		f"SELECT name, _user_tags FROM `tab{table}` WHERE _user_tags LIKE '%Rent%'"
	)
	for name, tags in rows:
		parts = (tags or "").split(",")
		if "Rent" not in parts:
			continue
		new_tags = ",".join("Events" if p == "Rent" else p for p in parts)
		frappe.db.set_value(table, name, "_user_tags", new_tags, update_modified=False)


def execute():
	# 1. Masters — rename_doc walks every Link field for us.
	_rename("Project Type")
	_rename("Value Streams")
	if frappe.db.exists("Tag", "Rent"):
		_rename("Tag")

	# 2. Keep the Value Streams master's Data field matching its new name
	#    (autoname on Project Type is field:project_type — rename_doc updates
	#    that one itself; Value Streams has no autoname rule).
	if frappe.db.exists("Value Streams", "Events"):
		frappe.db.set_value(
			"Value Streams", "Events", "value_stream", "Events", update_modified=False
		)

	# 3. Residual plain-string child rows (paranoia — the Link rename should
	#    already have rewritten these; costs nothing when zero rows match).
	for col in ("value_stream", "value_streams"):
		frappe.db.sql(
			f"UPDATE `tabValue Stream` SET {col}='Events' WHERE {col}='Rent'"
		)

	# 4. Lead service-interest Select values (the option list itself ships via
	#    the custom_field fixture in this release).
	frappe.db.sql(
		"UPDATE `tabLead` SET custom_service_interest='Events' "
		"WHERE custom_service_interest='Rent'"
	)

	# 5. Tag caches on the three doctypes the value-stream multiselect lives on.
	for table in ("Opportunity", "Project", "Customer"):
		_retag(table)

	# 6. Process-map documentation text that names the old stream filter.
	frappe.db.sql(
		"UPDATE `tabProcess Document Step` "
		"SET target_artifact = REPLACE(target_artifact, \"project_type='Rent'\", \"project_type='Events'\") "
		"WHERE target_artifact LIKE \"%project_type='Rent'%\""
	)

	frappe.clear_cache()

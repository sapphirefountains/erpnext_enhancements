"""One-time data fix (post_model_sync; listed in patches.txt).

Clears legacy junk from the ``primary_address`` **Link** field on Opportunity /
Project / Master Project (v1.159.5).

``primary_address`` is a Link to Address on these doctypes, but Zoho import / an
older migration stored the rendered address *display* (HTML, e.g.
``2600 Taylorsville BLVD<br>…``) in some rows instead of an Address docname. A
Link can't validate that, so every save of those records failed with
``Could not find Address: …`` — making them un-editable. This nulls any value
that doesn't resolve to a real Address so the records save again; users re-pick
the primary address from the directory UI.

Going forward, ``sync_contact.sanitize_primary_address_link`` (before_validate on
the same three doctypes) prevents recurrence. Idempotent — a second run finds
nothing to clear.

Never touches Customer/Supplier: there ``primary_address`` is a read-only Text
Editor *display* and HTML is expected.
"""

import frappe

_DOCTYPES = ("Opportunity", "Project", "Master Project")


def execute():
	for dt in _DOCTYPES:
		if not frappe.db.exists("DocType", dt) or not frappe.db.has_column(dt, "primary_address"):
			continue

		rows = frappe.db.sql(
			"select name, primary_address from `tab{dt}` where coalesce(primary_address, '') <> ''".format(dt=dt),
			as_dict=True,
		)
		cleared = 0
		for r in rows:
			if not frappe.db.exists("Address", r.primary_address):
				frappe.db.set_value(dt, r.name, "primary_address", None, update_modified=False)
				cleared += 1
		if cleared:
			frappe.logger().info(f"clear_invalid_primary_address_links: cleared {cleared} {dt} row(s)")

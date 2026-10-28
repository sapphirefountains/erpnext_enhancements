"""Keep ERPNext's stock web forms ``tasks``, ``issues`` and ``addresses`` unpublished (v1.755.0).

ERPNext ships all three published, for any signed-in account, and this app's own portal pages
replace them. Unpublishing is the framework's own off switch: in frappe 16.53.0
``WebForm.raise_if_unpublished`` refuses the page and every web form endpoint for an unpublished
form, and an unpublished form drops out of ``get_published_web_forms``, the router's list.

**Why ``after_migrate`` and not a patch.** A standard web form is re-imported from its app's JSON
whenever that JSON's ``modified`` is newer than the row (``frappe.modules.import_file``), and Web Form
is not in that module's ``ignore_values``, so the re-import brings ``published: 1`` back. ERPNext
restamped ``addresses.json`` as recently as 2025-10-27. A patch runs once; this runs after the model
sync on every migrate, the first one included, so a re-import is undone in the same migrate. No
route guard on top: with ``published`` off, frappe already refuses both the page and the API.

``update_modified=False`` keeps each row's ``modified`` at ERPNext's stamp, so a later ERPNext change
to one of these forms still imports, and is unpublished again here, instead of being skipped as older
than the row.

To bring one back, take it out of :data:`STOCK_WEB_FORMS`. This never publishes anything. Never
raises, because a failed migrate is a failed deploy; a failure is an Error Log.
"""

import frappe

#: ERPNext's standard web forms that this app keeps unpublished, by name (not route).
STOCK_WEB_FORMS = ("tasks", "issues", "addresses")


def unpublish_stock_web_forms():
	"""``after_migrate``: unpublish each of :data:`STOCK_WEB_FORMS` that is published. Never raises."""
	try:
		published = frappe.get_all(
			"Web Form", filters={"name": ("in", STOCK_WEB_FORMS), "published": 1}, pluck="name"
		)
		for name in published:
			frappe.db.set_value("Web Form", name, "published", 0, update_modified=False)
		if published:
			# The router reads published forms through a one-hour redis cache.
			from frappe.website.router import clear_routing_cache

			clear_routing_cache()
	except Exception:
		frappe.log_error(title="Stock web forms not unpublished", message=frappe.get_traceback())

"""Keep ERPNext's stock web forms ``tasks``, ``issues`` and ``addresses`` unpublished (v1.755.0).

ERPNext ships all three published, for any signed-in account, and this app's own portal pages
replace them. Unpublishing is the framework's own off switch: in frappe 16.53.0
``WebForm.raise_if_unpublished`` refuses the page and every web form endpoint for an unpublished
form, and an unpublished form drops out of ``get_published_web_forms``, the router's list.

**Why ``after_migrate`` and not a patch.** A standard web form is re-imported from its app's JSON
whenever that JSON's ``modified`` is newer than the row (``frappe.modules.import_file``), and Web Form
is not in that module's ``ignore_values``, so the re-import brings ``published: 1`` back. ERPNext
restamped ``addresses.json`` as recently as 2025-10-27. A patch runs once; this runs after the model
sync on every migrate, the first one included, so a re-import is undone in the same migrate. Also
in ``after_install``, because ``bench install-app`` runs no ``after_migrate`` and would otherwise
leave a fresh site serving the forms until its first migrate. No route guard on top: with
``published`` off, frappe already refuses both the page and the API.

**The portal menu goes with them.** ERPNext's standard portal menu links two of the forms for the
Customer role: Issues (``/issues``) and Addresses (``/addresses``, which ERPNext's Website Route
Redirect sends on to ``address/list``). Unpublished, both would be dead links beside the portal's
own pages, so their Portal Settings rows are disabled too. ``PortalSettings.sync_menu`` only adds
routes that are missing and never re-enables a row, so this holds; it is re-checked every migrate
anyway. The redirect is left in place: with the menu row off nothing links to it, and it leads only
to a page that no longer exists.

``update_modified=False`` keeps each form's ``modified`` at ERPNext's stamp, so a later ERPNext change
to one of these forms still imports, and is unpublished again here, instead of being skipped as older
than the row.

**Every flip is printed** to the migrate output, so a form someone re-published from the Desk being
switched off again is visible rather than silent. To bring one back for good, take it out of
:data:`STOCK_WEB_FORMS` (and its route out of :data:`STOCK_MENU_ROUTES`). This never publishes or
enables anything. Never raises, because a failed migrate is a failed deploy; a failure is an Error
Log.
"""

import frappe

#: ERPNext's standard web forms that this app keeps unpublished, by name (not route).
STOCK_WEB_FORMS = ("tasks", "issues", "addresses")

#: The Portal Settings menu routes that lead to those forms (``/tasks`` has no menu row).
STOCK_MENU_ROUTES = ("/issues", "/addresses")


def unpublish_stock_web_forms():
	"""``after_migrate`` / ``after_install``: unpublish the forms and disable their menu rows. Never raises."""
	try:
		published = frappe.get_all(
			"Web Form", filters={"name": ("in", STOCK_WEB_FORMS), "published": 1}, pluck="name"
		)
		for name in published:
			frappe.db.set_value("Web Form", name, "published", 0, update_modified=False)
			print(f"[erpnext_enhancements] unpublished ERPNext's stock web form {name!r}")

		menu_rows = frappe.get_all(
			"Portal Menu Item",
			filters={"parent": "Portal Settings", "route": ("in", STOCK_MENU_ROUTES), "enabled": 1},
			fields=["name", "route"],
		)
		for row in menu_rows:
			frappe.db.set_value("Portal Menu Item", row.name, "enabled", 0, update_modified=False)
			print(f"[erpnext_enhancements] disabled the portal menu row for {row.route}")

		if menu_rows:
			# set_value on a child row clears that row's cache, not its parent's.
			frappe.clear_document_cache("Portal Settings", "Portal Settings")
		if published or menu_rows:
			# Routing (published forms, one-hour redis cache) and the portal menu, per user.
			from frappe.website.utils import clear_cache

			clear_cache()
	except Exception:
		frappe.log_error(title="Stock web forms not unpublished", message=frappe.get_traceback())

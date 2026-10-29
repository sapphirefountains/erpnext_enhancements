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
anyway. The redirect is left in place: with the menu row off nothing links to it, and it ends at the
Not Found page below.

**Their routes answer Not Found (v1.755.2).** Unpublished, a form drops out of the router, and a
path under its route falls through to frappe's later renderers. Paths under ``tasks`` and
``issues`` reach the Not Found page that way. Paths under ``address``, the Addresses form's route,
do not. In frappe 16.53.0 the print-view renderer (``PrintPage``) takes any two-part path whose first
part names a DocType in any letter case, then cannot load a controller for the lower-case name. So
``/address/list``, ``/address/new`` and the rest answered frappe's generic error page, signed in or
not. :class:`UnpublishedStockWebFormPage` is a ``page_renderer`` hook, frappe's own extension point,
which frappe consults before its built-in renderers. It answers frappe's Not Found page for an
unpublished form's route and every path under it. That covers the paths the form's router owned
while it was published (the route, ``/list``, ``/new``, ``/<name>`` and ``/<name>/edit``) and deeper
paths, which the form never owned. Bare ``/tasks`` never reaches it: ERPNext's own
``website_route_rules`` entry rewrites ``/tasks`` to the Task list before any renderer runs, and
that path already answers Not Found. It reads
the published state from frappe's ``get_published_web_forms``, the list the web form renderer itself
uses, so a published form is still frappe's to serve, and the two never disagree. It reads the routes
from the Web Form rows rather than a copy, cached until frappe next clears its website cache
(:func:`clear_route_cache`, the ``website_clear_cache`` hook). Nothing is stored that an ERPNext
re-import or upgrade could overwrite: a re-import that publishes a form makes the renderer step aside
until this module unpublishes it again, in the same migrate. ``/addresses`` keeps ERPNext's redirect
to ``address/list`` and so ends at the same Not Found page. Deleting that redirect would not give a
cleaner 404, because ERPNext's own ``website_route_rules`` entry for ``/addresses`` would take the
path over and serve the stock portal's Address list.

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

#: Redis key for the forms' routes; :func:`clear_route_cache` drops it with frappe's website cache.
ROUTE_CACHE_KEY = "erpnext_enhancements:stock_web_form_routes"


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


def _stock_web_form_routes():
	"""``{form name: route}`` for :data:`STOCK_WEB_FORMS`, read from the Web Form rows. Cached."""

	def _get():
		rows = frappe.get_all("Web Form", filters={"name": ("in", STOCK_WEB_FORMS)}, fields=["name", "route"])
		return {row.name: route for row in rows if (route := (row.route or "").strip("/ "))}

	return frappe.cache.get_value(ROUTE_CACHE_KEY, _get)


def unpublished_stock_web_form_route(path):
	"""The route of an unpublished stock web form that ``path`` is or sits under, else ``""``."""
	path = (path or "").strip("/ ")
	if not path:
		return ""
	for name, route in _stock_web_form_routes().items():
		if path != route and not path.startswith(f"{route}/"):
			continue
		from frappe.website.doctype.web_form.web_form import get_published_web_forms

		if name not in {form.name for form in get_published_web_forms()}:
			return route
	return ""


class UnpublishedStockWebFormPage:
	"""``page_renderer`` hook: frappe's Not Found page under the route of an unpublished stock form.

	frappe builds every hooked renderer for every website request and asks ``can_render`` before its
	own renderers, so construction stays this cheap and a failure leaves the request to frappe.
	"""

	def __init__(self, path=None, http_status_code=None):
		self.path = (path or "").strip("/ ")
		self.http_status_code = http_status_code

	def can_render(self):
		try:
			return bool(unpublished_stock_web_form_route(self.path))
		except Exception:
			return False

	def render(self):
		from frappe.website.page_renderers.not_found_page import NotFoundPage

		# Built as the resolver builds its own fallback, with no status code: NotFoundPage sets 404.
		return NotFoundPage(self.path).render()


def clear_route_cache(path=None):
	"""``website_clear_cache`` hook: forget the forms' routes whenever frappe clears its website cache."""
	frappe.cache.delete_value(ROUTE_CACHE_KEY)

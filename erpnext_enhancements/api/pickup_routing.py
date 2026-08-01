"""Supplier pick-up routing for a Project's Purchase Orders.

Whitelisted API behind the **Pick Routing Map** button on the Project form's
Budget tab (``public/js/project_enhancements/pick_routing_map.js``). A job's
material is normally spread across several vendors' will-call counters, and the
question nobody could answer from Desk was "what is still sitting at a supplier,
and what is the shortest way round to collect it?".

:func:`get_pickup_route_data` answers it in one round-trip: the Google Maps
browser key, the depot the run starts from, the candidate finish points, and one
*stop* per supplier pick-up address carrying the Purchase Orders and lines behind
it. The client hands those addresses straight to Google's ``DirectionsService``
with ``optimizeWaypoints: true``, so the travelling-salesman ordering is
drive-time based and happens in the browser -- this module still calls no Google
API of its own and needs no credentials.

A stop also carries ``latitude``/``longitude`` when its Address was picked from
the Places autocomplete (v1.205.0), which lets the client skip a billable
geocode and route to the exact building rather than to Google's reading of the
address text. Most Addresses predate that and have no point, so this is strictly
an optimisation layered on the text -- never a replacement for it.

Three things this module is careful about:

* **A Purchase Order reaches a Project two ways.** The header
  ``Purchase Order.project`` and the (mandatory, WI-014)
  ``Purchase Order Item.project`` do not always agree -- on prod there are POs
  with a blank header but a filled line, and closed POs with neither. So the
  candidate set is the union of both, the same shape
  ``project_enhancements._supplementary_documents`` already uses.

* **Supplier addresses are sparse and the two lookup paths disagree.** Only a
  minority of POs carry a ``supplier_address`` at all, and a supplier that has
  ``supplier_primary_address`` set often has no ``Dynamic Link`` row on the
  Address (and vice versa), so neither path alone is sufficient.
  :func:`_resolve_pickup_address` walks a four-step chain and reports which step
  won in ``address_source``, and a supplier that resolves to nothing is still
  returned -- as a stop with ``address: None`` -- so the UI can point at the
  vendor record that needs fixing instead of silently dropping the pick-up.

* **``Purchase Order.shipping_address`` is deliberately NOT in that chain.** On
  this site it is our own yard on essentially every PO; routing to it would send
  the truck back to the shop for every stop.

Statuses: "still needs picking up" is ``docstatus == 1``, ``status`` not in
``Closed``/``Delivered``, and ``per_received < 100``. The numeric field is the
authoritative signal -- ``Closed`` is the one status that can hide a PO whose
goods never arrived, and ``To Bill`` means the goods are already here.
"""

import frappe
from frappe import _
from frappe.utils import flt

#: Where a pick-up run starts when ``ERPNext Enhancements Settings``
#: ``pickup_route_start_address`` is blank. The shop; kept here as well as in the
#: field default because a field default only applies when the Single is first
#: created, and it already exists on every live site.
DEFAULT_DEPOT_ADDRESS = "85 W 300 S, Bountiful, UT 84010"

#: Which Purchase Orders make it onto the map.
SCOPE_OUTSTANDING = "outstanding"  # submitted, goods not yet received (default)
SCOPE_SUBMITTED = "submitted"  # every submitted PO, received or not
SCOPE_ALL = "all"  # drafts too, for planning a run before the POs are placed

SCOPES = (SCOPE_OUTSTANDING, SCOPE_SUBMITTED, SCOPE_ALL)

#: Statuses that mean the goods are no longer the supplier's to hand over, even
#: though ``per_received`` may still read under 100 (``Closed`` is a manual
#: "stop chasing this"; ``Delivered`` is a drop-ship straight to the customer).
_SETTLED_PO_STATUSES = ("Closed", "Delivered")

#: Address types preferred when a Supplier has several linked Addresses -- a
#: counter/warehouse beats the billing address for a pick-up.
_PICKUP_ADDRESS_TYPES = ("Shipping", "Warehouse", "Shop", "Plant")

_PO_FIELDS = (
	"name",
	"supplier",
	"supplier_name",
	"status",
	"docstatus",
	"transaction_date",
	"schedule_date",
	"per_received",
	"grand_total",
	"currency",
	"supplier_address",
	"dispatch_address",
	"contact_person",
	"contact_display",
	"contact_mobile",
)

_ADDRESS_FIELDS = (
	"name",
	"address_line1",
	"address_line2",
	"city",
	"state",
	"pincode",
	"country",
	"phone",
	"custom_full_address",
)

#: The point the Places autocomplete stored when this Address was picked
#: (v1.205.0). Requested only once ``bench migrate`` has created the columns:
#: ``main`` auto-deploys, so a code deploy can land before the fixture does, and
#: selecting a column that does not exist is a SQL error on every call rather
#: than a graceful ``None``. Same reasoning as ``api/comments.py``.
_ADDRESS_POINT_FIELDS = ("custom_latitude", "custom_longitude")


# ------------------------------------------------------------------ addresses


def _address_line(addr):
	"""A geocodable one-line string for an Address row, or None.

	Built from the parts rather than read straight from ``custom_full_address``
	so the country is included -- the stored one-liner deliberately omits it (see
	``script_migrations.address.set_full_address``), and a terse vendor address
	geocodes more reliably with it. Falls back to the stored value when every
	part is empty.
	"""
	if not addr:
		return None
	parts = (
		addr.get("address_line1"),
		addr.get("address_line2"),
		addr.get("city"),
		addr.get("state"),
		addr.get("pincode"),
		addr.get("country"),
	)
	line = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
	return line or (addr.get("custom_full_address") or None)


def _address_coords(addr):
	"""``(lat, lng)`` picked from Google for an Address row, or None.

	**0.0 means absent, not Null Island.** Float custom fields are ``NOT NULL
	DEFAULT 0``, so every Address that predates v1.205.0 reads back as 0.0 --
	and both writers blank the pair to a literal 0 when the address is edited by
	hand. ``if lat is not None`` would therefore be true for the whole table and
	route the entire run to the Gulf of Guinea, which Google will happily accept.

	The range check catches the other realistic corruption: a lat/lng written the
	wrong way round is a valid-looking pair that lands in the wrong hemisphere.
	"""
	if not addr:
		return None
	lat = flt(addr.get("custom_latitude"))
	lng = flt(addr.get("custom_longitude"))
	if not lat or not lng:
		return None
	if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
		return None
	return lat, lng


def _address_field_list(cache):
	"""``_ADDRESS_FIELDS`` plus the stored point when the columns exist.

	Resolved once per request rather than once per address -- ``has_column`` is
	cached by frappe, but the tuple key keeps it off the hot path entirely. The
	key is a tuple so it cannot collide with an Address literally named this.
	"""
	key = ("__address_fields",)
	if key not in cache:
		fields = list(_ADDRESS_FIELDS)
		if frappe.db.has_column("Address", "custom_latitude"):
			fields += list(_ADDRESS_POINT_FIELDS)
		cache[key] = fields
	return cache[key]


def _get_address(name, cache):
	"""Fetch an Address row as a dict (memoised per request), or None."""
	if not name:
		return None
	if name not in cache:
		cache[name] = frappe.db.get_value(
			"Address", name, _address_field_list(cache), as_dict=True
		)
	return cache[name]


def _supplier_linked_address(supplier):
	"""The best Address linked to a Supplier through the ``Dynamic Link`` child
	table, preferring a pick-up-shaped address type, then the primary, then
	whatever is left.

	Two queries rather than frappe's child-table filter join: ``name`` and
	``modified`` exist on both ``tabAddress`` and ``tabDynamic Link``, so an
	``order_by`` over the joined form is ambiguous. This is also the shape
	``sync_contact.set_primary_address`` uses.
	"""
	if not supplier:
		return None

	names = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Supplier", "link_name": supplier, "parenttype": "Address"},
		pluck="parent",
	)
	if not names:
		return None

	rows = frappe.get_all(
		"Address",
		filters={"name": ["in", sorted(set(names))], "disabled": 0},
		fields=["name", "address_type", "is_primary_address"],
		order_by="is_primary_address desc, modified desc",
	)
	if not rows:
		return None

	for row in rows:
		if row.get("address_type") in _PICKUP_ADDRESS_TYPES:
			return row.get("name")
	return rows[0].get("name")


def _resolve_pickup_address(po, cache):
	"""Work out where the truck actually goes for one Purchase Order.

	Most specific first::

	    1. po.dispatch_address                 -- "ships from", the native
	                                              semantic for a pick-up point
	    2. po.supplier_address                 -- the vendor address on the PO
	    3. Supplier.supplier_primary_address   -- the Link on the party
	    4. Address <- Dynamic Link -> Supplier -- the address directory

	``po.shipping_address`` is intentionally absent: on this site it is our own
	yard on nearly every PO.

	Returns ``(address_row_or_None, source_label_or_None)`` -- the raw Address
	dict, so the caller can build both the display block and the geocodable
	one-liner from it.
	"""
	for fieldname, source in (
		("dispatch_address", "po.dispatch_address"),
		("supplier_address", "po.supplier_address"),
	):
		addr = _get_address(po.get(fieldname), cache)
		if addr and _address_line(addr):
			return addr, source

	supplier = po.get("supplier")
	if not supplier:
		return None, None

	key = ("supplier_primary", supplier)
	if key not in cache:
		cache[key] = frappe.db.get_value("Supplier", supplier, "supplier_primary_address")
	addr = _get_address(cache[key], cache)
	if addr and _address_line(addr):
		return addr, "supplier.supplier_primary_address"

	key = ("supplier_directory", supplier)
	if key not in cache:
		cache[key] = _supplier_linked_address(supplier)
	addr = _get_address(cache[key], cache)
	if addr and _address_line(addr):
		return addr, "supplier.address_directory"

	return None, None


# ------------------------------------------------------------------- settings


def _maps_api_key():
	"""The Google Maps *browser* key.

	Shared with the travel maps rather than duplicated -- ``Travel Settings``
	owns the field (see ``api.travel._maps_api_key``); the pick-up map needs the
	**Directions API** enabled on that same key on top of Maps JavaScript. A
	referrer-restricted browser key is exposed to the client either way, so
	returning it to a user already permitted to read the Project is expected.
	Blank is a supported state: the client falls back to a Google Maps deep link.
	"""
	try:
		return frappe.db.get_single_value("Travel Settings", "google_maps_api_key") or ""
	except Exception:
		return ""


def _use_routes_api():
	"""Whether the client should try ``routes.Route.computeRoutes`` before DirectionsService.

	Off by default, and deliberately a *setting* rather than a constant. Routes needs
	"Routes API" enabled on the Cloud project and added to the key's restriction list --
	a console change that no deploy or rollback can make. This app auto-deploys from
	``main``, so a code-only switch would take the map down for every driver in the window
	between merge and somebody remembering to flip it in Google.

	It is also the kill switch for the failure the automatic fallback cannot catch: a Routes
	call that *succeeds* with output we read wrongly (see the field-name traps in
	pick_routing_map.js). A fallback only helps when the call fails.
	"""
	try:
		return 1 if frappe.db.get_single_value("Travel Settings", "use_routes_api") else 0
	except Exception:
		return 0


def _depot_address():
	"""The address a pick-up run starts from (the shop), from Settings."""
	try:
		configured = frappe.db.get_single_value("ERPNext Enhancements Settings", "pickup_route_start_address")
	except Exception:
		configured = None
	return (configured or "").strip() or DEFAULT_DEPOT_ADDRESS


# ------------------------------------------------------- purchase order lookup


def _project_po_names(project):
	"""Every Purchase Order attached to the project, by either route.

	The header ``project`` and the item-row ``project`` disagree often enough on
	real data that querying only one silently drops POs -- see the module
	docstring.
	"""
	names = set()

	for row in frappe.get_all(
		"Purchase Order Item",
		filters={"project": project},
		fields=["parent"],
		distinct=True,
	):
		if row.get("parent"):
			names.add(row["parent"])

	for name in frappe.get_all("Purchase Order", filters={"project": project}, pluck="name"):
		if name:
			names.add(name)

	return names


def _is_outstanding(po):
	"""Does this PO still have goods sitting at the supplier?"""
	if po.get("status") in _SETTLED_PO_STATUSES:
		return False
	return flt(po.get("per_received")) < 100


def _select_purchase_orders(project, scope):
	"""The project's Purchase Orders that belong on the map, newest first."""
	names = _project_po_names(project)
	if not names:
		return []

	docstatus_filter = ["<", 2] if scope == SCOPE_ALL else ["=", 1]
	rows = frappe.get_all(
		"Purchase Order",
		filters={"name": ["in", sorted(names)], "docstatus": docstatus_filter},
		fields=list(_PO_FIELDS),
		order_by="transaction_date desc, name desc",
	)

	if scope == SCOPE_OUTSTANDING:
		rows = [po for po in rows if _is_outstanding(po)]
	return rows


def _po_items(po_names):
	"""Line items for the selected POs, grouped by parent."""
	if not po_names:
		return {}

	rows = frappe.get_all(
		"Purchase Order Item",
		filters={"parent": ["in", sorted(po_names)]},
		fields=["parent", "idx", "item_code", "item_name", "qty", "received_qty", "uom"],
		order_by="parent asc, idx asc",
	)

	grouped = {}
	for row in rows:
		grouped.setdefault(row["parent"], []).append(
			{
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
				"qty": flt(row.get("qty")),
				"received_qty": flt(row.get("received_qty")),
				"uom": row.get("uom"),
			}
		)
	return grouped


# ------------------------------------------------------------------- assembly


def _stop_key(supplier, address_name, address_line):
	"""Identity of a stop: one supplier counter at one address.

	Keyed on the address as well as the supplier, so a vendor with two branches
	on the same job becomes two stops -- and so every PO from a supplier whose
	address could not be resolved still collapses into one "no address" entry.
	"""
	return "{}::{}".format(supplier or "", address_name or address_line or "")


def _build_stops(purchase_orders, items_by_po, cache):
	"""Collapse Purchase Orders into one stop per supplier pick-up address."""
	stops = {}
	order = []

	for po in purchase_orders:
		addr, source = _resolve_pickup_address(po, cache)
		address_line = _address_line(addr)
		coords = _address_coords(addr)
		key = _stop_key(po.get("supplier"), addr.get("name") if addr else None, address_line)

		stop = stops.get(key)
		if not stop:
			stop = stops[key] = {
				"key": key,
				"supplier": po.get("supplier"),
				"supplier_name": po.get("supplier_name") or po.get("supplier"),
				"address": address_line,
				"address_name": addr.get("name") if addr else None,
				"address_source": source,
				# None, never 0, so the client can test these directly. The text
				# is always sent as well -- it is what the stop list, the pick
				# sheet and every Maps deep link render, and a driver reading
				# "40.889,-111.881" off a printed sheet is a regression.
				"latitude": coords[0] if coords else None,
				"longitude": coords[1] if coords else None,
				"phone": (addr.get("phone") if addr else None) or po.get("contact_mobile"),
				"contact": po.get("contact_display") or po.get("contact_person"),
				"purchase_orders": [],
				"po_count": 0,
				"item_count": 0,
				"total_qty": 0.0,
				"amount": 0.0,
				"currency": po.get("currency"),
			}
			order.append(key)

		lines = items_by_po.get(po.get("name")) or []
		stop["purchase_orders"].append(
			{
				"name": po.get("name"),
				"status": po.get("status"),
				"docstatus": po.get("docstatus"),
				"date": str(po.get("transaction_date") or "") or None,
				"required_by": str(po.get("schedule_date") or "") or None,
				"per_received": flt(po.get("per_received")),
				"grand_total": flt(po.get("grand_total")),
				"currency": po.get("currency"),
				"items": lines,
			}
		)
		stop["po_count"] += 1
		stop["item_count"] += len(lines)
		stop["total_qty"] += sum(line["qty"] for line in lines)
		# Only total the money when every PO at this stop agrees on a currency --
		# summing across currencies would print a confidently wrong number.
		if stop["currency"] and po.get("currency") == stop["currency"]:
			stop["amount"] += flt(po.get("grand_total"))
		else:
			stop["currency"] = None
			stop["amount"] = None

	return [stops[key] for key in order]


def _project_site_address(project_doc, cache):
	"""A geocodable address for the job site, or None.

	The Contacts & Addresses tab's ``primary_address`` Link is the modern field;
	``custom_customer__lead_address`` is the older link kept on projects converted
	from an Opportunity, and ``custom_project_address`` is the free-text Zoho
	import. Best-effort -- a project with no address simply cannot be used as a
	finish point, and the client greys that option out.
	"""
	for fieldname in ("primary_address", "custom_customer__lead_address"):
		line = _address_line(_get_address(project_doc.get(fieldname), cache))
		if line:
			return line

	typed = (project_doc.get("custom_project_address") or "").strip()
	return typed or None


@frappe.whitelist()
def get_pickup_route_data(project, scope=SCOPE_OUTSTANDING):
	"""Everything the Pick Routing Map dialog needs, in one call.

	``scope`` is one of ``outstanding`` (default -- submitted POs whose goods have
	not arrived), ``submitted`` (every submitted PO) or ``all`` (drafts too). An
	unrecognised value falls back to ``outstanding`` rather than throwing: this
	is a read-only view and a stale client should still render.

	**Permission: read access to the Project, and nothing more.** The Purchase
	Order / Supplier / Address reads below use ``frappe.get_all``, which is
	``get_list`` with ``ignore_permissions=True`` -- so a user who can open the
	job sees its vendors, PO totals and lines whether or not they hold Purchase
	Order read. That is deliberate and matches the Procurement Tracker sitting a
	few inches up the same Budget tab (``project_enhancements
	.get_procurement_documents``, which is whitelisted with no gate at all); this
	endpoint is strictly the tighter of the two. If purchasing ever needs to be
	hidden from some Project readers, both must move to ``frappe.get_list``
	together -- see ``api/gantt.py`` for the permission-checked shape.
	"""
	scope = scope if scope in SCOPES else SCOPE_OUTSTANDING

	# ``frappe.get_doc(dt, {...})`` resolves a dict as a *filter* rather than a
	# name, and a JSON request body can send one. Not a hole -- the permission
	# check below still runs on whatever it resolved, and ``frappe.client.get``
	# offers the same lookup to every authenticated user by design -- but this
	# endpoint takes a project, so it insists on one.
	if not isinstance(project, str) or not project.strip():
		frappe.throw(_("Pass a Project name."))

	project_doc = frappe.get_doc("Project", project.strip())
	project_doc.check_permission("read")

	cache = {}
	purchase_orders = _select_purchase_orders(project_doc.name, scope)
	items_by_po = _po_items([po["name"] for po in purchase_orders])
	stops = _build_stops(purchase_orders, items_by_po, cache)

	return {
		"api_key": _maps_api_key(),
		"use_routes_api": _use_routes_api(),
		"scope": scope,
		"project": {
			"name": project_doc.name,
			"project_name": project_doc.get("project_name"),
			"site_address": _project_site_address(project_doc, cache),
		},
		"depot": {"label": _("Shop"), "address": _depot_address()},
		"stops": stops,
		"routable_count": len([s for s in stops if s.get("address")]),
	}

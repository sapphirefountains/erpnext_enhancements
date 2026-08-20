"""Supplier pick-up routing for outstanding Purchase Orders.

Whitelisted API behind two buttons, both served by
``public/js/project_enhancements/pick_routing_map.js``:

* **Pick Routing Map** on the Project form's Budget tab ->
  :func:`get_pickup_route_data`. A job's material is normally spread across
  several vendors' will-call counters, and the question nobody could answer from
  Desk was "what is still sitting at a supplier, and what is the shortest way
  round to collect it?".
* **Pick Sheet** on the Supplier form and the Supplier list ->
  :func:`get_supplier_pick_data` (v1.338.0). The same run turned inside out: one
  counter, every job. A crew already driving to Harrington should come back with
  everything Harrington is holding, not with one project's worth.

Both return the **same payload shape** and share every rule that decides what is
on it -- the scope test, the address fallback chain, stop identity, the money
guard. That is the point: two sheets that disagree about whether a PO is still
outstanding is the divergence ``docs/pick-routing-map-po-details.md`` rejected
option (a) to avoid.

:func:`get_pickup_route_data` answers the project half in one round-trip: the Google Maps
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

import json

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

#: "Fully received" is a tolerance test, not an equality one -- float quantities
#: never land exactly on zero. **Must agree with QTY_TOLERANCE in
#: pick_routing_map.js**, which decides the same thing for the tick boxes on the
#: printed sheet; a driver counting "still to collect" off the per-job summary
#: and off the line ticks must get the same number.
_QTY_TOLERANCE = 0.005


#: Address types preferred when a Supplier has several linked Addresses -- a
#: counter/warehouse beats the billing address for a pick-up.
_PICKUP_ADDRESS_TYPES = ("Shipping", "Warehouse", "Shop", "Plant")

_PO_FIELDS = (
	"name",
	"project",
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


def _project_names(project_names):
	"""``{name: project_name}`` for the jobs a set of lines belongs to.

	One query for the whole payload rather than one per line. A Project deleted
	since the PO was raised simply does not come back and the caller falls back to
	the bare ID -- which is still what is written on the material when it lands in
	the yard, so it is a usable label rather than a hole.
	"""
	names = sorted({n for n in project_names if n})
	if not names:
		return {}
	return {
		row["name"]: row.get("project_name")
		for row in frappe.get_all("Project", filters={"name": ["in", names]}, fields=["name", "project_name"])
	}


def _select_supplier_purchase_orders(suppliers, scope):
	"""Every Purchase Order at these suppliers that belongs on the run, newest first.

	The project-scoped sibling has to take the union of two ``project`` fields
	because they disagree; this one does not -- ``Purchase Order.supplier`` is
	mandatory and single-valued, so one filter is the whole answer. The *scope*
	rules are deliberately the identical ones, though: "still to collect" must mean
	the same thing whichever door the crew came in through, or the two sheets
	disagree about the same PO.
	"""
	names = sorted({s for s in suppliers if s})
	if not names:
		return []

	docstatus_filter = ["<", 2] if scope == SCOPE_ALL else ["=", 1]
	rows = frappe.get_all(
		"Purchase Order",
		filters={"supplier": ["in", names], "docstatus": docstatus_filter},
		fields=list(_PO_FIELDS),
		order_by="transaction_date desc, name desc",
	)

	if scope == SCOPE_OUTSTANDING:
		rows = [po for po in rows if _is_outstanding(po)]
	return rows


def _po_items(purchase_orders):
	"""Line items for the selected POs, grouped by parent.

	Each line carries **its own** ``project``, not the order's. A supplier pick-up
	runs across every job at once and one Purchase Order routinely covers more than
	one of them -- ``Purchase Order Item.project`` is mandatory (WI-014) for exactly
	that reason. Stamping the header project onto every line would put the wrong job
	number on material that is about to be split between three trucks, so the header
	is only the *fallback* for rows that predate the rule.
	"""
	by_name = {po["name"]: po for po in purchase_orders if po.get("name")}
	if not by_name:
		return {}

	rows = frappe.get_all(
		"Purchase Order Item",
		filters={"parent": ["in", sorted(by_name)]},
		fields=["parent", "idx", "item_code", "item_name", "qty", "received_qty", "uom", "project"],
		order_by="parent asc, idx asc",
	)

	resolved = [
		(row, row.get("project") or (by_name.get(row["parent"]) or {}).get("project")) for row in rows
	]
	labels = _project_names(project for _row, project in resolved)

	grouped = {}
	for row, project in resolved:
		grouped.setdefault(row["parent"], []).append(
			{
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
				"qty": flt(row.get("qty")),
				"received_qty": flt(row.get("received_qty")),
				"uom": row.get("uom"),
				"project": project,
				"project_name": labels.get(project),
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


def _note_line_project(stop, index, line):
	"""Fold one line into its stop's per-job rollup.

	The rollup is what makes a *supplier* run workable: the crew is standing at one
	counter collecting for four jobs at once, and the question at the tailgate is
	"how much of this pile is PRJ-00566?". Built here rather than in the browser so
	the dialog and the printed sheet cannot disagree about it.

	Keyed on ``project or ""`` so every line with no job at all collapses into one
	"unassigned" bucket instead of vanishing -- an unassigned line is still material
	that has to come off the truck somewhere.
	"""
	key = line.get("project") or ""
	entry = index.get(key)
	if not entry:
		entry = index[key] = {
			"project": line.get("project"),
			"project_name": line.get("project_name"),
			"line_count": 0,
			"open_count": 0,
		}
		stop["projects"].append(entry)
	entry["line_count"] += 1
	if flt(line.get("qty")) - flt(line.get("received_qty")) > _QTY_TOLERANCE:
		entry["open_count"] += 1


def _build_stops(purchase_orders, items_by_po, cache):
	"""Collapse Purchase Orders into one stop per supplier pick-up address."""
	stops = {}
	project_index = {}
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
				# Which jobs this counter is holding material for, in the order the
				# lines were met. One entry in project mode; the whole point of the
				# stop in supplier mode.
				"projects": [],
				"po_count": 0,
				"item_count": 0,
				"total_qty": 0.0,
				"amount": 0.0,
				"currency": po.get("currency"),
			}
			project_index[key] = {}
			order.append(key)

		lines = items_by_po.get(po.get("name")) or []
		for line in lines:
			_note_line_project(stop, project_index[key], line)
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
	items_by_po = _po_items(purchase_orders)
	stops = _build_stops(purchase_orders, items_by_po, cache)

	return {
		"api_key": _maps_api_key(),
		"use_routes_api": _use_routes_api(),
		"scope": scope,
		"mode": "project",
		"project": {
			"name": project_doc.name,
			"project_name": project_doc.get("project_name"),
			"site_address": _project_site_address(project_doc, cache),
		},
		"depot": {"label": _("Shop"), "address": _depot_address()},
		"stops": stops,
		"routable_count": len([s for s in stops if s.get("address")]),
	}


# --------------------------------------------------------------- supplier mode


#: How many suppliers one sheet may cover. Not a routing limit -- Google's
#: 23-waypoint ceiling is handled downstream, and a supplier with two branches is
#: two stops either way -- but a bound on the query a whitelisted endpoint will
#: run for a caller who passes the entire Supplier list.
MAX_PICK_SUPPLIERS = 25


def _normalise_suppliers(suppliers):
	"""One Supplier name, a list of them, or the JSON array a browser sends.

	``frappe.call`` posts a JS array as a real list, but the same endpoint reached
	form-encoded (``?suppliers=["A","B"]``) delivers the string -- and a caller who
	means one supplier reasonably passes a bare name. Accept all three rather than
	make the caller guess, and reject anything else loudly: a silently-empty
	supplier list produces an empty sheet, which reads as "nothing to collect".
	"""
	if isinstance(suppliers, str):
		value = suppliers.strip()
		if value.startswith("["):
			try:
				suppliers = json.loads(value)
			except ValueError:
				frappe.throw(_("Could not read the supplier list."))
		else:
			suppliers = [value]

	if not isinstance(suppliers, (list, tuple)):
		frappe.throw(_("Pass a Supplier name, or a list of them."))

	names = []
	for name in suppliers:
		if not isinstance(name, str) or not name.strip():
			continue
		if name.strip() not in names:
			names.append(name.strip())

	if not names:
		frappe.throw(_("Pass at least one Supplier."))
	if len(names) > MAX_PICK_SUPPLIERS:
		frappe.throw(_("A pick sheet covers at most {0} suppliers at once.").format(MAX_PICK_SUPPLIERS))
	return names


def _supplier_labels(names):
	"""``[{name, supplier_name}]`` for the suppliers asked for, in the order asked.

	Read from the Supplier table rather than from the stops, so a vendor with
	*nothing* outstanding is still named on the sheet. "Harrington: nothing to
	collect" and "we forgot to include Harrington" look identical otherwise, and
	only one of them means the crew can skip the stop.
	"""
	rows = {
		row["name"]: row.get("supplier_name")
		for row in frappe.get_all(
			"Supplier", filters={"name": ["in", sorted(names)]}, fields=["name", "supplier_name"]
		)
	}
	return [{"name": name, "supplier_name": rows.get(name) or name} for name in names]


@frappe.whitelist()
def get_supplier_pick_data(suppliers, scope=SCOPE_OUTSTANDING):
	"""The same run, scoped to a supplier instead of a job.

	The project sheet answers "where is this job's material?". This answers the
	other half of the same question -- "we are going to Harrington anyway, what
	else is sitting there?" -- so one crew clears a counter for every open job in
	one trip instead of one trip per project.

	The payload is deliberately the *same shape* as
	:func:`get_pickup_route_data`: same stops, same scope rules, same address
	fallback chain, so the dialog, the optimiser and the printed sheet are one
	implementation rather than two that drift. The differences are exactly two:
	``project`` is ``None`` (there is no single job site to finish at), and every
	line carries the job it belongs to, because that is how the pile gets sorted
	when the truck gets back.

	**Permission: Purchase Order read.** Stricter than the project endpoint, and
	necessarily so -- that one is anchored to a Project the caller can already open,
	and shows only that job's spend. This one is anchored to nothing and would
	otherwise hand any authenticated user every open order, every job and every
	total for a vendor of their choosing. ``frappe.get_all`` below still bypasses
	row-level permissions, so this check is the whole gate; do not remove it
	without replacing it with ``frappe.get_list``.
	"""
	scope = scope if scope in SCOPES else SCOPE_OUTSTANDING
	names = _normalise_suppliers(suppliers)

	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(_("You are not permitted to read Purchase Orders."), frappe.PermissionError)

	cache = {}
	purchase_orders = _select_supplier_purchase_orders(names, scope)
	items_by_po = _po_items(purchase_orders)
	stops = _build_stops(purchase_orders, items_by_po, cache)

	return {
		"api_key": _maps_api_key(),
		"use_routes_api": _use_routes_api(),
		"scope": scope,
		"mode": "supplier",
		# No job site to finish at: this run belongs to no single project. The
		# client greys the "Job site" finish option out on a falsy site_address, so
		# None here needs no special case there -- only null-safe access.
		"project": None,
		"suppliers": _supplier_labels(names),
		"depot": {"label": _("Shop"), "address": _depot_address()},
		"stops": stops,
		"routable_count": len([s for s in stops if s.get("address")]),
	}

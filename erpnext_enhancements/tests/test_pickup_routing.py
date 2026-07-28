"""Bench-free unit tests for the supplier pick-up routing API.

Stubs a minimal ``frappe`` (no site/bench) so ``api.pickup_routing`` runs under
plain unittest, backed by a tiny in-memory query engine so the ``frappe.get_all``
filter shapes the module actually uses are exercised rather than mocked away.
The stub is installed in ``setUpModule`` (execution time), not at import, so it
never fools the bench-only suites' ``import frappe`` skip-guards.

What is guarded here is the part that silently produces a *plausible but wrong*
route:

  * the four-step supplier address fallback, in order, with
    ``Purchase Order.shipping_address`` (our own yard) never reachable;
  * the PO -> Project union — the header ``project`` and the item-row ``project``
    disagree on real data, and querying either alone drops POs;
  * "still to collect" being ``per_received < 100`` and not the ``status`` label,
    because ``Closed`` hides POs whose goods never arrived;
  * stops keyed on supplier *and* address, so a two-branch vendor is two stops;
  * money never summed across currencies.

Run: python -m unittest erpnext_enhancements.tests.test_pickup_routing
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

pickup_routing = None

#: Tables the stubbed query engine serves, reset by each test.
STATE = {}


class StubPermissionError(Exception):
	pass


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"Purchase Order": [],
			"Purchase Order Item": [],
			"Address": [],
			"Supplier": [],
			"Dynamic Link": [],
			"singles": {},
			"project": {},
			"permission_checks": [],
		}
	)


# --------------------------------------------------------------- query engine


def _matches(row, filters):
	"""Evaluate the subset of frappe filter syntax this module uses."""
	if isinstance(filters, dict):
		filters = [
			[field, *(cond if isinstance(cond, list) else ["=", cond])] for field, cond in filters.items()
		]
	for field, op, value in filters:
		actual = row.get(field)
		if op == "=":
			if actual != value:
				return False
		elif op == "in":
			if actual not in value:
				return False
		elif op == "<":
			if not (actual is not None and actual < value):
				return False
		else:  # pragma: no cover - a new operator means a new test, not a silent pass
			raise AssertionError(f"unsupported filter operator: {op}")
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, distinct=False, **_kw):
	rows = [dict(r) for r in STATE.get(doctype, []) if _matches(r, filters or {})]

	if order_by:
		# Only the two orderings the module asks for; applied outermost-first.
		for clause in reversed([c.strip() for c in order_by.split(",")]):
			key, _, direction = clause.partition(" ")
			rows.sort(key=lambda r: (r.get(key) is None, r.get(key)), reverse=direction.strip() == "desc")

	if pluck:
		values = [r.get(pluck) for r in rows]
		return list(dict.fromkeys(values)) if distinct else values

	if fields:
		rows = [{f: r.get(f) for f in fields} for r in rows]
	if distinct:
		deduped = []
		for row in rows:
			if row not in deduped:
				deduped.append(row)
		rows = deduped
	return rows


def _db_get_value(doctype, name, fieldname, as_dict=False):
	table = STATE.get(doctype, [])
	row = next((r for r in table if r.get("name") == name), None)
	if not row:
		return None
	if as_dict:
		return {f: row.get(f) for f in fieldname}
	return row.get(fieldname)


def _db_get_single_value(doctype, fieldname):
	return STATE["singles"].get((doctype, fieldname))


class _FakeProject:
	def __init__(self, data):
		self._data = dict(data)
		self.name = self._data.get("name")

	def get(self, fieldname, default=None):
		return self._data.get(fieldname, default)

	def check_permission(self, ptype):
		STATE["permission_checks"].append(ptype)
		if STATE.get("deny_permission"):
			raise StubPermissionError(ptype)


def _get_doc(doctype, name):
	if doctype != "Project":
		raise AssertionError(f"unexpected get_doc({doctype})")
	data = STATE["project"]
	if data.get("name") != name:
		raise AssertionError(f"no such Project: {name}")
	return _FakeProject(data)


class StubThrow(Exception):
	pass


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def throw(msg, title=None, exc=None):
		raise StubThrow(msg)

	frappe.throw = throw
	frappe._ = lambda s: s
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.PermissionError = StubPermissionError
	frappe.db = types.SimpleNamespace(
		get_value=_db_get_value,
		get_single_value=_db_get_single_value,
	)

	utils = types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.flt = flt
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global pickup_routing
	_install_frappe_stub()
	_reset_state()
	sys.modules.pop("erpnext_enhancements.api.pickup_routing", None)
	from erpnext_enhancements.api import pickup_routing as mod

	pickup_routing = mod


# ------------------------------------------------------------------- fixtures


def make_po(name, **overrides):
	po = {
		"name": name,
		"project": None,
		"supplier": "Harrington",
		"supplier_name": "Harrington Industrial Plastics",
		"status": "To Receive and Bill",
		"docstatus": 1,
		"transaction_date": "2026-07-01",
		"schedule_date": "2026-07-15",
		"per_received": 0,
		"grand_total": 1000.0,
		"currency": "USD",
		"supplier_address": None,
		"dispatch_address": None,
		"shipping_address": "Sapphire Fountain-Billing",
		"contact_person": None,
		"contact_display": None,
		"contact_mobile": None,
	}
	po.update(overrides)
	return po


def make_address(name, **overrides):
	addr = {
		"name": name,
		"address_line1": "1907 Indiana Ave",
		"address_line2": None,
		"city": "Salt Lake City",
		"state": "UT",
		"pincode": "84104",
		"country": "United States",
		"phone": None,
		"custom_full_address": None,
		"address_type": "Billing",
		"is_primary_address": 0,
		"disabled": 0,
	}
	addr.update(overrides)
	return addr


class PickupRoutingTest(unittest.TestCase):
	def setUp(self):
		_reset_state()
		STATE["project"] = {"name": "PRJ-00566", "project_name": "Riverbend Fountain"}


# ------------------------------------------------------------------ addresses


class TestAddressLine(PickupRoutingTest):
	def test_joins_parts_including_country(self):
		line = pickup_routing._address_line(make_address("A"))
		self.assertEqual(line, "1907 Indiana Ave, Salt Lake City, UT, 84104, United States")

	def test_skips_blank_parts(self):
		line = pickup_routing._address_line(
			make_address("A", address_line2="   ", pincode=None, country=None)
		)
		self.assertEqual(line, "1907 Indiana Ave, Salt Lake City, UT")

	def test_falls_back_to_stored_one_liner(self):
		bare = make_address(
			"A",
			address_line1=None,
			city=None,
			state=None,
			pincode=None,
			country=None,
			custom_full_address="85 W 300 S, Bountiful, UT",
		)
		self.assertEqual(pickup_routing._address_line(bare), "85 W 300 S, Bountiful, UT")

	def test_empty_address_is_none(self):
		self.assertIsNone(pickup_routing._address_line(None))
		self.assertIsNone(
			pickup_routing._address_line(
				make_address(
					"A",
					address_line1=None,
					city=None,
					state=None,
					pincode=None,
					country=None,
				)
			)
		)


class TestResolvePickupAddress(PickupRoutingTest):
	def setUp(self):
		super().setUp()
		STATE["Address"] = [
			make_address("Dispatch", address_line1="10 Dock St"),
			make_address("SupplierAddr", address_line1="20 Vendor Way"),
			make_address("Primary", address_line1="30 Primary Rd"),
			make_address("Directory", address_line1="40 Directory Ln"),
			make_address("Counter", address_line1="50 Will Call Ct", address_type="Shipping"),
			make_address("Yard", address_line1="85 W 300 S"),
		]
		STATE["Supplier"] = [{"name": "Harrington", "supplier_primary_address": None}]

	def test_dispatch_address_wins(self):
		po = make_po("PO-1", dispatch_address="Dispatch", supplier_address="SupplierAddr")
		addr, source = pickup_routing._resolve_pickup_address(po, {})
		self.assertEqual(addr["name"], "Dispatch")
		self.assertEqual(source, "po.dispatch_address")

	def test_supplier_address_is_next(self):
		po = make_po("PO-1", supplier_address="SupplierAddr")
		addr, source = pickup_routing._resolve_pickup_address(po, {})
		self.assertEqual(addr["name"], "SupplierAddr")
		self.assertEqual(source, "po.supplier_address")

	def test_supplier_primary_address_is_third(self):
		STATE["Supplier"][0]["supplier_primary_address"] = "Primary"
		addr, source = pickup_routing._resolve_pickup_address(make_po("PO-1"), {})
		self.assertEqual(addr["name"], "Primary")
		self.assertEqual(source, "supplier.supplier_primary_address")

	def test_address_directory_is_the_last_resort(self):
		STATE["Dynamic Link"] = [
			{
				"parent": "Directory",
				"parenttype": "Address",
				"link_doctype": "Supplier",
				"link_name": "Harrington",
			}
		]
		addr, source = pickup_routing._resolve_pickup_address(make_po("PO-1"), {})
		self.assertEqual(addr["name"], "Directory")
		self.assertEqual(source, "supplier.address_directory")

	def test_directory_prefers_a_pickup_shaped_address_type(self):
		STATE["Dynamic Link"] = [
			{
				"parent": "Directory",
				"parenttype": "Address",
				"link_doctype": "Supplier",
				"link_name": "Harrington",
			},
			{
				"parent": "Counter",
				"parenttype": "Address",
				"link_doctype": "Supplier",
				"link_name": "Harrington",
			},
		]
		addr, _source = pickup_routing._resolve_pickup_address(make_po("PO-1"), {})
		self.assertEqual(addr["name"], "Counter")

	def test_directory_skips_disabled_addresses(self):
		STATE["Address"].append(make_address("Dead", address_line1="60 Gone St", disabled=1))
		STATE["Dynamic Link"] = [
			{"parent": "Dead", "parenttype": "Address", "link_doctype": "Supplier", "link_name": "Harrington"}
		]
		addr, source = pickup_routing._resolve_pickup_address(make_po("PO-1"), {})
		self.assertIsNone(addr)
		self.assertIsNone(source)

	def test_shipping_address_is_never_used(self):
		"""Our own yard must not become a pick-up stop."""
		po = make_po("PO-1", shipping_address="Yard")
		addr, source = pickup_routing._resolve_pickup_address(po, {})
		self.assertIsNone(addr)
		self.assertIsNone(source)

	def test_unresolvable_supplier_returns_nothing(self):
		addr, source = pickup_routing._resolve_pickup_address(make_po("PO-1"), {})
		self.assertIsNone(addr)
		self.assertIsNone(source)

	def test_a_link_to_a_deleted_address_falls_through(self):
		po = make_po("PO-1", supplier_address="GoneForever")
		STATE["Supplier"][0]["supplier_primary_address"] = "Primary"
		addr, source = pickup_routing._resolve_pickup_address(po, {})
		self.assertEqual(addr["name"], "Primary")
		self.assertEqual(source, "supplier.supplier_primary_address")


# ------------------------------------------------------------ selecting the POs


class TestSelectPurchaseOrders(PickupRoutingTest):
	def test_union_of_header_and_item_project(self):
		"""Either link alone drops POs that only carry the other."""
		STATE["Purchase Order"] = [
			make_po("PO-header", project="PRJ-00566"),
			make_po("PO-item"),
			make_po("PO-neither"),
		]
		STATE["Purchase Order Item"] = [
			{"parent": "PO-item", "idx": 1, "project": "PRJ-00566", "qty": 1, "item_code": "X"},
			{"parent": "PO-neither", "idx": 1, "project": "PRJ-00999", "qty": 1, "item_code": "Y"},
		]
		names = {po["name"] for po in pickup_routing._select_purchase_orders("PRJ-00566", "outstanding")}
		self.assertEqual(names, {"PO-header", "PO-item"})

	def test_outstanding_excludes_closed_and_fully_received(self):
		STATE["Purchase Order"] = [
			make_po("PO-open", project="PRJ-00566"),
			make_po("PO-closed", project="PRJ-00566", status="Closed"),
			make_po("PO-delivered", project="PRJ-00566", status="Delivered"),
			make_po("PO-received", project="PRJ-00566", status="To Bill", per_received=100),
			make_po("PO-part", project="PRJ-00566", status="To Receive and Bill", per_received=40),
		]
		names = {po["name"] for po in pickup_routing._select_purchase_orders("PRJ-00566", "outstanding")}
		self.assertEqual(names, {"PO-open", "PO-part"})

	def test_closed_with_goods_outstanding_is_still_excluded(self):
		"""Closed is a deliberate 'stop chasing this', whatever per_received says."""
		STATE["Purchase Order"] = [make_po("PO-closed", project="PRJ-00566", status="Closed", per_received=0)]
		self.assertEqual(pickup_routing._select_purchase_orders("PRJ-00566", "outstanding"), [])

	def test_submitted_scope_keeps_received_pos_but_not_drafts(self):
		STATE["Purchase Order"] = [
			make_po("PO-done", project="PRJ-00566", status="Completed", per_received=100),
			make_po("PO-draft", project="PRJ-00566", docstatus=0),
		]
		names = {po["name"] for po in pickup_routing._select_purchase_orders("PRJ-00566", "submitted")}
		self.assertEqual(names, {"PO-done"})

	def test_all_scope_includes_drafts_but_never_cancelled(self):
		STATE["Purchase Order"] = [
			make_po("PO-draft", project="PRJ-00566", docstatus=0),
			make_po("PO-live", project="PRJ-00566", docstatus=1),
			make_po("PO-void", project="PRJ-00566", docstatus=2, status="Cancelled"),
		]
		names = {po["name"] for po in pickup_routing._select_purchase_orders("PRJ-00566", "all")}
		self.assertEqual(names, {"PO-draft", "PO-live"})

	def test_no_purchase_orders_is_an_empty_run(self):
		self.assertEqual(pickup_routing._select_purchase_orders("PRJ-00566", "outstanding"), [])


# ----------------------------------------------------------------- the stops


class TestBuildStops(PickupRoutingTest):
	def setUp(self):
		super().setUp()
		STATE["Address"] = [
			make_address("North", address_line1="10 North St"),
			make_address("South", address_line1="20 South St"),
		]
		STATE["Supplier"] = [{"name": "Harrington", "supplier_primary_address": None}]

	def test_one_supplier_address_collapses_many_pos_into_one_stop(self):
		pos = [
			make_po("PO-1", supplier_address="North"),
			make_po("PO-2", supplier_address="North", grand_total=250.0),
		]
		items = {
			"PO-1": [{"item_code": "A", "item_name": "A", "qty": 2.0, "received_qty": 0.0, "uom": "Nos"}],
			"PO-2": [{"item_code": "B", "item_name": "B", "qty": 3.0, "received_qty": 0.0, "uom": "Nos"}],
		}
		stops = pickup_routing._build_stops(pos, items, {})
		self.assertEqual(len(stops), 1)
		self.assertEqual(stops[0]["po_count"], 2)
		self.assertEqual(stops[0]["item_count"], 2)
		self.assertEqual(stops[0]["total_qty"], 5.0)
		self.assertEqual(stops[0]["amount"], 1250.0)
		self.assertEqual(stops[0]["address"], "10 North St, Salt Lake City, UT, 84104, United States")

	def test_two_branches_of_one_supplier_are_two_stops(self):
		pos = [
			make_po("PO-1", supplier_address="North"),
			make_po("PO-2", supplier_address="South"),
		]
		stops = pickup_routing._build_stops(pos, {}, {})
		self.assertEqual(len(stops), 2)
		self.assertEqual({s["address_name"] for s in stops}, {"North", "South"})

	def test_unresolved_supplier_is_kept_as_a_single_addressless_stop(self):
		"""Dropping it would hide the vendor record that needs fixing."""
		pos = [make_po("PO-1"), make_po("PO-2")]
		stops = pickup_routing._build_stops(pos, {}, {})
		self.assertEqual(len(stops), 1)
		self.assertIsNone(stops[0]["address"])
		self.assertIsNone(stops[0]["address_source"])
		self.assertEqual(stops[0]["po_count"], 2)

	def test_mixed_currencies_refuse_to_total(self):
		pos = [
			make_po("PO-1", supplier_address="North", currency="USD"),
			make_po("PO-2", supplier_address="North", currency="CAD"),
		]
		stops = pickup_routing._build_stops(pos, {}, {})
		self.assertIsNone(stops[0]["amount"])
		self.assertIsNone(stops[0]["currency"])

	def test_stop_carries_the_address_phone_over_the_po_contact(self):
		STATE["Address"][0]["phone"] = "801-555-0100"
		pos = [make_po("PO-1", supplier_address="North", contact_mobile="801-555-0199")]
		stops = pickup_routing._build_stops(pos, {}, {})
		self.assertEqual(stops[0]["phone"], "801-555-0100")


# ------------------------------------------------------------------ settings


class TestDepotAddress(PickupRoutingTest):
	def test_configured_address_wins(self):
		STATE["singles"][("ERPNext Enhancements Settings", "pickup_route_start_address")] = (
			"1 New Shop Rd, Ogden, UT"
		)
		self.assertEqual(pickup_routing._depot_address(), "1 New Shop Rd, Ogden, UT")

	def test_blank_falls_back_to_the_shop(self):
		STATE["singles"][("ERPNext Enhancements Settings", "pickup_route_start_address")] = "   "
		self.assertEqual(pickup_routing._depot_address(), pickup_routing.DEFAULT_DEPOT_ADDRESS)

	def test_unset_falls_back_to_the_shop(self):
		self.assertEqual(pickup_routing._depot_address(), "85 W 300 S, Bountiful, UT 84010")

	def test_missing_maps_key_is_an_empty_string_not_a_crash(self):
		self.assertEqual(pickup_routing._maps_api_key(), "")


class TestProjectSiteAddress(PickupRoutingTest):
	def setUp(self):
		super().setUp()
		STATE["Address"] = [
			make_address("Site", address_line1="9 Job Site Dr"),
			make_address("Legacy", address_line1="8 Legacy Ave"),
		]

	def test_primary_address_wins(self):
		project = _FakeProject(
			{"name": "PRJ-1", "primary_address": "Site", "custom_customer__lead_address": "Legacy"}
		)
		self.assertTrue(pickup_routing._project_site_address(project, {}).startswith("9 Job Site Dr"))

	def test_falls_back_to_the_legacy_customer_address_link(self):
		project = _FakeProject({"name": "PRJ-1", "custom_customer__lead_address": "Legacy"})
		self.assertTrue(pickup_routing._project_site_address(project, {}).startswith("8 Legacy Ave"))

	def test_falls_back_to_the_typed_zoho_address(self):
		project = _FakeProject({"name": "PRJ-1", "custom_project_address": " 7 Typed Way, Provo UT "})
		self.assertEqual(pickup_routing._project_site_address(project, {}), "7 Typed Way, Provo UT")

	def test_no_address_is_none(self):
		self.assertIsNone(pickup_routing._project_site_address(_FakeProject({"name": "PRJ-1"}), {}))


# ------------------------------------------------------------------ endpoint


class TestGetPickupRouteData(PickupRoutingTest):
	def setUp(self):
		super().setUp()
		STATE["Address"] = [make_address("North", address_line1="10 North St")]
		STATE["Supplier"] = [{"name": "Harrington", "supplier_primary_address": None}]
		STATE["Purchase Order"] = [
			make_po("PO-1", project="PRJ-00566", supplier_address="North"),
			make_po("PO-2", project="PRJ-00566", supplier="Ferguson", supplier_name="Ferguson"),
		]
		STATE["Purchase Order Item"] = [
			{
				"parent": "PO-1",
				"idx": 1,
				"project": "PRJ-00566",
				"item_code": "PUMP-1",
				"item_name": "Pump",
				"qty": 2.0,
				"received_qty": 0.0,
				"uom": "Nos",
			}
		]
		STATE["singles"][("Travel Settings", "google_maps_api_key")] = "browser-key"

	def test_payload_shape(self):
		data = pickup_routing.get_pickup_route_data("PRJ-00566")
		self.assertEqual(data["api_key"], "browser-key")
		self.assertEqual(data["scope"], "outstanding")
		self.assertEqual(data["project"]["name"], "PRJ-00566")
		self.assertEqual(data["depot"]["address"], pickup_routing.DEFAULT_DEPOT_ADDRESS)
		self.assertEqual(len(data["stops"]), 2)
		# Only the resolvable supplier can be routed; the other is still listed.
		self.assertEqual(data["routable_count"], 1)

	def test_items_reach_the_stop(self):
		data = pickup_routing.get_pickup_route_data("PRJ-00566")
		routable = next(s for s in data["stops"] if s["address"])
		self.assertEqual(routable["purchase_orders"][0]["items"][0]["item_code"], "PUMP-1")
		self.assertEqual(routable["total_qty"], 2.0)

	def test_read_permission_is_checked(self):
		pickup_routing.get_pickup_route_data("PRJ-00566")
		self.assertEqual(STATE["permission_checks"], ["read"])

	def test_permission_failure_propagates(self):
		STATE["deny_permission"] = True
		with self.assertRaises(StubPermissionError):
			pickup_routing.get_pickup_route_data("PRJ-00566")

	def test_unknown_scope_degrades_to_outstanding(self):
		"""A read-only view should render for a stale client, not throw."""
		data = pickup_routing.get_pickup_route_data("PRJ-00566", scope="../../etc/passwd")
		self.assertEqual(data["scope"], "outstanding")

	def test_project_must_be_a_name_not_a_filter_dict(self):
		"""frappe.get_doc resolves a dict as a filter; a JSON body can send one."""
		for bad in ({"project_type": "Build"}, "", "   ", None, ["PRJ-00566"]):
			with self.assertRaises(StubThrow):
				pickup_routing.get_pickup_route_data(bad)

	def test_project_name_is_stripped(self):
		data = pickup_routing.get_pickup_route_data("  PRJ-00566  ")
		self.assertEqual(data["project"]["name"], "PRJ-00566")

	def test_scope_is_honoured(self):
		STATE["Purchase Order"].append(
			make_po("PO-3", project="PRJ-00566", supplier_address="North", status="Closed")
		)
		outstanding = pickup_routing.get_pickup_route_data("PRJ-00566", scope="outstanding")
		submitted = pickup_routing.get_pickup_route_data("PRJ-00566", scope="submitted")
		self.assertEqual(sum(s["po_count"] for s in outstanding["stops"]), 2)
		self.assertEqual(sum(s["po_count"] for s in submitted["stops"]), 3)


if __name__ == "__main__":  # pragma: no cover
	unittest.main()

"""The Products tab's endpoint, project_dashboard.get_products_data (v1.824.0), run on a stub.

Prod had no product Sales Order when the tab shipped, so the endpoint's first job is to answer an
empty site with every section present and empty, never a raise. Its second is to keep what it must
not show: a section the person cannot read comes back {"allowed": False}, the paid chip needs Sales
Invoice read on top of Sales Order (Logan builds the units and holds no accounts role), Work Order
and Delivery Note names need their own read, and the shelf commitments it works out for the stock
section never reach the browser. Its third, since the manager review of #1464: judge a document on
ALL its lines (``product_orders.classify_lines``), so a fountain job carrying one Splash Wizard stays
off the tab, and show product-line amounts, never a grand total.

The module is loaded from its file under a private name, with a ``frappe`` stub in sys.modules
for the life of this suite only (setUpModule / tearDownModule), so the package's own
``__init__`` (which imports half the app) never runs. The stub's ``db.sql`` answers by the table
a query reads and IGNORES its WHERE clause, on purpose: every rule the endpoint depends on is
applied again in Python (the board window, the status and return exclusions, the document rule),
so a stub row that the SQL would have filtered out must still be dropped here. It also formats
every query with its parameters the way the driver does (a stray ``%`` or a missing key fails the
test, as it would fail on prod), and records what was asked, so the suite can hold the CLAUDE.md
rules: no SQL function written as a ``get_all`` string field, and no query naming the
configurator's ``custom_source_configuration`` column on a site that lacks it.

Run: python -m unittest erpnext_enhancements.tests.test_products_tab_endpoint
"""

import importlib.util
import sys
import types
import unittest
from datetime import date, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE_FILE = APP / "project_enhancements" / "page" / "project_dashboard" / "project_dashboard.py"
TODAY = date(2026, 11, 18)

_saved_modules = {}
_STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.utils.spreadsheet",
	"erpnext_enhancements.crm_enhancements.historical",
)


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as error:
			raise AttributeError(key) from error


READABLE = {
	"Sales Order",
	"Sales Invoice",
	"Work Order",
	"Delivery Note",
	"Quotation",
	"Opportunity",
	"Item",
	"Bin",
	"Project",
}


class Site:
	"""The stub's database and permissions, reset per test."""

	def __init__(self):
		self.tables = {}
		self.readable = set(READABLE)
		self.has_source_column = True
		self.queries = []
		self.get_all_calls = []
		self.errors = []
		self.raise_on = None
		self.default_wip = None
		self.settings = None  # Product Build Settings; None: the DocType is not installed

	def rows(self, key):
		return [_Dict(row) for row in self.tables.get(key, [])]


SITE = Site()

# The table each query reads, by a phrase only that query has. Order matters: the Sales Order and
# Quotation header queries also name their item tables inside EXISTS.
_ROUTES = (
	("SELECT DISTINCT dni.against_sales_order", "deliveries"),
	("FROM `tabSales Order Item` d\n", "so_lines"),
	("FROM `tabQuotation Item` d\n", "quote_lines"),
	("FROM `tabSales Order` so", "so_headers"),
	("FROM `tabQuotation` q", "quote_headers"),
	("SELECT DISTINCT wip_warehouse", "wip_orders"),
	("FROM `tabWork Order`", "work_orders"),
	# The Testing column (v1.826.0): the units a finished Work Order made, the tested units on the
	# shelf, and their submitted pre-shipment tests.
	("WHERE work_order IN %(wos)s", "serials_wo"),
	("WHERE item_code IN %(items)s AND status = 'Active'", "serials_active"),
	("FROM `tabQuality Inspection`", "tests"),
	("SELECT item_code, warehouse, actual_qty, projected_qty, ordered_qty", "component_bins"),
	("FROM `tabSales Invoice Item` sii", "invoices"),
	("SELECT item_code, SUM(actual_qty) AS qty", "on_hand"),
	("FROM `tabOpportunity`\n", "opportunities"),
	("FROM `tabBin` b\n\t\tINNER JOIN `tabItem` i", "units"),
	("FROM `tabItem` i\n\t\tWHERE i.item_group IN", "components"),
)


def _sql(query, values=None, as_dict=False, **_kwargs):
	SITE.queries.append(query)
	# The driver's own formatting: every %(name)s needs its key, and a bare % is an error.
	if values is not None:
		query % {key: "x" for key in values}
	elif "%" in query.replace("%%", ""):
		raise ValueError("a % in a query with no parameters")
	for phrase, key in _ROUTES:
		if phrase in query:
			if SITE.raise_on == key:
				raise RuntimeError("boom: " + key)
			if key == "on_hand":
				return [
					_Dict(item_code=code, qty=qty) for code, qty in SITE.tables.get("on_hand", {}).items()
				]
			return SITE.rows(key)
	raise AssertionError("the stub does not know this query:\n" + query)


def _has_column(doctype, column):
	if doctype.startswith("tab"):
		raise Exception("TableMissingError: tab" + doctype)  # has_column takes a doctype
	if (doctype, column) == ("Item", "custom_source_configuration"):
		return SITE.has_source_column
	return True


def _matches(row, filters):
	"""The few filter shapes the endpoint writes: a value, or [op, value] with =, !=, in, not in."""
	for field, condition in (filters or {}).items():
		value = row.get(field)
		if isinstance(condition, (list, tuple)):
			op, arg = condition[0], condition[1]
			if (
				(op == "in" and value not in arg)
				or (op == "not in" and value in arg)
				or (op == "!=" and value == arg)
				or (op == "=" and value != arg)
			):
				return False
		elif value != condition:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	"""Unlike db.sql, get_all here honours its filters: the control panel section's whole point is
	which Projects each filter finds (type Products, or linked to an order whatever its type)."""
	SITE.get_all_calls.append((doctype, fields))
	table = {"Project": "projects", "Warehouse": "warehouses"}.get(doctype)
	rows = [row for row in SITE.rows(table) if _matches(row, filters)] if table else []
	return [row.get(pluck) for row in rows] if pluck else rows


def setUpModule():
	for name in _STUBBED:
		_saved_modules[name] = sys.modules.get(name)

	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn) if not (a and callable(a[0])) else a[0]
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.db = types.SimpleNamespace(
		sql=_sql,
		has_column=_has_column,
		get_value=lambda *a, **k: None,
		get_single_value=lambda doctype, field, *a, **k: (
			SITE.default_wip
			if (doctype, field) == ("Manufacturing Settings", "default_wip_warehouse")
			else None
		),
	)
	frappe.get_all = _get_all

	def get_cached_doc(doctype, *args, **kwargs):
		if doctype == "Product Build Settings" and SITE.settings is not None:
			return _Dict(SITE.settings)
		raise Exception("DoesNotExistError: " + doctype)

	frappe.get_cached_doc = get_cached_doc
	frappe.has_permission = lambda doctype=None, ptype="read", *a, **k: doctype in SITE.readable
	frappe.get_traceback = lambda *a, **k: "Traceback (most recent call last):\n  stub"
	frappe.get_roles = lambda *a, **k: []

	def log_error(title=None, message=None, **kwargs):
		SITE.errors.append({"title": title, "message": message})

	frappe.log_error = log_error

	utils = types.ModuleType("frappe.utils")

	def getdate(value=None):
		if value is None:
			return TODAY
		if isinstance(value, date):
			return value
		return date.fromisoformat(str(value)[:10])

	def flt(value=0, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.getdate = getdate
	utils.flt = flt
	utils.cint = lambda value=0, *a, **k: int(flt(value))
	utils.nowdate = lambda: str(TODAY)
	utils.add_days = lambda value, days: getdate(value) + timedelta(days=days)
	utils.cstr = lambda value=None: "" if value is None else str(value)
	frappe.utils = utils

	spreadsheet = types.ModuleType("erpnext_enhancements.utils.spreadsheet")
	spreadsheet.build_payload = lambda *a, **k: {}
	historical = types.ModuleType("erpnext_enhancements.crm_enhancements.historical")
	historical.exclude_sql = lambda alias="o": f"coalesce({alias}.custom_is_historical, 0) = 0"

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["erpnext_enhancements.utils.spreadsheet"] = spreadsheet
	sys.modules["erpnext_enhancements.crm_enhancements.historical"] = historical

	global dashboard
	spec = importlib.util.spec_from_file_location("_project_dashboard_under_test", MODULE_FILE)
	dashboard = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(dashboard)


def tearDownModule():
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


class Base(unittest.TestCase):
	def setUp(self):
		global SITE
		SITE = Site()
		globals()["SITE"] = SITE
		dashboard.check_permission = lambda: True

	def answer(self):
		return dashboard.get_products_data()


def so(
	name,
	*,
	status="To Deliver and Bill",
	per_delivered=0,
	per_billed=0,
	delivery_date="2026-12-01",
	project=None,
	docstatus=1,
):
	return {
		"name": name,
		"customer": "CUST-" + name,
		"customer_name": "Customer " + name,
		"transaction_date": date(2026, 11, 10),
		"delivery_date": date.fromisoformat(delivery_date) if delivery_date else None,
		"status": status,
		"docstatus": docstatus,
		"per_delivered": per_delivered,
		"per_billed": per_billed,
		"currency": "USD",
		"project": project,
	}


def so_line(parent, item_code, amount=0, qty=1, delivered=0, factor=1, source=None):
	return {
		"parent": parent,
		"item_code": item_code,
		"item_name": item_code,
		"amount": amount,
		"qty": qty,
		"delivered_qty": delivered,
		"conversion_factor": factor,
		"source_configuration": source,
	}


def quote(
	name,
	*,
	status="Draft",
	docstatus=0,
	transaction_date=date(2026, 11, 17),
	valid_till=None,
	opportunity=None,
):
	return {
		"name": name,
		"quotation_to": "Customer",
		"party_name": "P-" + name,
		"customer_name": "Customer " + name,
		"transaction_date": transaction_date,
		"valid_till": valid_till,
		"status": status,
		"docstatus": docstatus,
		"currency": "USD",
		"opportunity": opportunity,
	}


def quote_line(parent, item_code, amount=0, source=None):
	return {
		"parent": parent,
		"item_code": item_code,
		"item_name": item_code,
		"amount": amount,
		"source_configuration": source,
	}


class TestAnEmptySite(Base):
	"""Prod on 2026-11-18: no Sales Order, no Work Order, no finished unit, no Products project."""

	def test_every_section_is_present_allowed_and_empty(self):
		answer = self.answer()
		self.assertEqual(answer["today"], "2026-11-18")
		self.assertEqual(answer["lines"], ["E-Stops", "Control Panels", "Other"])
		self.assertEqual(
			[s["key"] for s in answer["stages"]], ["ordered", "building", "testing", "ready", "delivered"]
		)
		self.assertEqual(answer["orders"], {"rows": [], "paid_visible": True, "allowed": True})
		quotations = answer["pipeline"]["quotations"]
		self.assertEqual(quotations["rows"], [])
		self.assertIsNone(quotations["win_rate"])
		self.assertEqual((quotations["won"], quotations["lost"]), (0, 0))
		self.assertEqual(answer["pipeline"]["opportunities"], {"rows": [], "allowed": True})
		self.assertEqual(answer["stock"]["units"], [])
		self.assertEqual(answer["stock"]["components"], [])
		self.assertEqual(answer["projects"], {"rows": [], "allowed": True})
		self.assertEqual(SITE.errors, [])

	def test_the_private_hand_offs_never_reach_the_browser(self):
		orders = self.answer()["orders"]
		self.assertNotIn("_committed", orders)
		self.assertNotIn("_panel_orders", orders)

	def test_without_the_page_role_it_answers_the_dashboards_denial(self):
		dashboard.check_permission = lambda: False
		answer = self.answer()
		self.assertEqual(set(answer), {"error"})
		self.assertIn("permission", answer["error"])
		self.assertEqual(SITE.queries, [], "denied before any read")


class TestPermissions(Base):
	def setUp(self):
		super().setUp()
		SITE.tables["so_headers"] = [so("SO-1")]
		SITE.tables["so_lines"] = [so_line("SO-1", "PDT-0040", 2200)]
		SITE.tables["work_orders"] = [
			{
				"name": "WO-1",
				"sales_order": "SO-1",
				"production_item": "PDT-0040",
				"status": "In Process",
				"docstatus": 1,
				"qty": 1,
				"produced_qty": 0,
			}
		]
		SITE.tables["deliveries"] = [
			{"sales_order": "SO-1", "name": "DN-1", "posting_date": date(2026, 11, 12)}
		]

	def test_a_section_the_person_cannot_read_is_not_allowed_and_the_rest_still_draw(self):
		SITE.readable -= {"Quotation", "Bin"}
		answer = self.answer()
		self.assertEqual(answer["pipeline"]["quotations"], {"allowed": False})
		self.assertEqual(
			answer["pipeline"]["opportunities"], {"allowed": False}, "deals come through their quotes"
		)
		self.assertEqual(answer["stock"], {"allowed": False})
		self.assertTrue(answer["orders"]["allowed"])
		self.assertTrue(answer["projects"]["allowed"])
		self.assertFalse(
			any("FROM `tabQuotation` q" in q for q in SITE.queries), "nothing read for a denied section"
		)

	def test_without_sales_order_read_there_is_no_board_and_no_commitment(self):
		SITE.readable.discard("Sales Order")
		SITE.tables["units"] = [
			{
				"item_code": "PDT-0040-1-1-1-2-0",
				"item_name": "E-Stop",
				"source_configuration": None,
				"on_hand": 2,
				"warehouses": "Build Room - SF",
			}
		]
		answer = self.answer()
		self.assertEqual(answer["orders"], {"allowed": False})
		unit = answer["stock"]["units"][0]
		self.assertIsNone(unit["committed"])
		self.assertIsNone(unit["free"])

	def test_the_paid_chip_needs_sales_invoice_read(self):
		SITE.readable.discard("Sales Invoice")
		answer = self.answer()
		self.assertFalse(answer["orders"]["paid_visible"])
		self.assertIsNone(answer["orders"]["rows"][0]["paid"])
		self.assertFalse(
			any("`tabSales Invoice Item`" in q for q in SITE.queries), "no invoice read without the right"
		)

	def test_work_order_and_delivery_note_names_need_their_own_read(self):
		SITE.readable -= {"Work Order", "Delivery Note"}
		row = self.answer()["orders"]["rows"][0]
		self.assertEqual(row["work_orders"], [])
		self.assertEqual(row["deliveries"], [])
		self.assertEqual(
			row["stage"], "building", "the stage they imply is a fact about the order, and stays"
		)

	def test_with_the_reads_the_names_are_sent(self):
		row = self.answer()["orders"]["rows"][0]
		self.assertEqual([w["name"] for w in row["work_orders"]], ["WO-1"])
		self.assertEqual(row["deliveries"], ["DN-1"])


class TestAFailedSection(Base):
	def test_a_section_that_raises_is_logged_and_kept_to_itself(self):
		SITE.raise_on = "quote_headers"
		answer = self.answer()
		self.assertEqual(answer["pipeline"]["quotations"], {"allowed": True, "failed": True})
		self.assertEqual(answer["pipeline"]["opportunities"], {"rows": [], "allowed": True})
		self.assertTrue(answer["orders"]["allowed"])
		self.assertNotIn("failed", answer["orders"])
		self.assertEqual(len(SITE.errors), 1)
		self.assertNotIn("\n", SITE.errors[0]["title"], "a one-line title (v16's log_error heuristic)")
		self.assertIn("Traceback", SITE.errors[0]["message"])

	def test_a_failed_board_still_lets_the_stock_section_draw(self):
		SITE.raise_on = "so_headers"
		answer = self.answer()
		self.assertEqual(answer["orders"], {"allowed": True, "failed": True})
		self.assertTrue(answer["stock"]["allowed"])


class TestTheBoard(Base):
	def setUp(self):
		super().setUp()
		SITE.tables["so_headers"] = [
			so("SO-1", delivery_date="2026-11-25"),  # from stock
			so("SO-2"),  # a service order: PDT-0701 only
			so("SO-3", delivery_date="2026-11-10"),  # built to order, in process, late
			so("SO-4", status="Completed", per_delivered=100, per_billed=100),  # delivered and paid
			so("SO-5"),  # a configured Item with no PDT code, plus freight
			so("SO-6", delivery_date="2026-11-01"),  # a fountain job carrying one Splash Wizard
			so(
				"SO-7", status="Closed", per_delivered=50, delivery_date="2026-11-01"
			),  # short-shipped, Closed
			so("SO-8", status="Completed", per_delivered=100),  # delivered only by a return note
		]
		SITE.tables["so_lines"] = [
			so_line("SO-1", "PDT-0040-1-1-1-2-0", 1668),
			so_line("SO-2", "PDT-0701", 400),
			so_line("SO-3", "PDT-0040-3-2-0-0-0", 2380),
			so_line("SO-4", "PDT-0040", 2005.24),
			so_line("SO-5", "SW-CUSTOM-7", 900, source="CFG-0009"),
			so_line("SO-5", "SRV-105", 85),
			so_line("SO-6", "PDT-0002", 8165.64),
			so_line("SO-6", "PUMP-123", 60000),
			so_line("SO-7", "PDT-0040", 2200, qty=2, delivered=1),
			so_line("SO-8", "PDT-0040", 2200),
		]
		SITE.tables["work_orders"] = [
			{
				"name": "WO-1",
				"sales_order": "SO-3",
				"production_item": "PDT-0040-3-2-0-0-0",
				"status": "In Process",
				"docstatus": 1,
				"qty": 1,
				"produced_qty": 0,
			},
		]
		# The DN query asks for non-return notes; the stub ignores that, so SO-8's return note is
		# simply absent here (as the real query leaves it out), and SO-8 has no delivery to show.
		SITE.tables["deliveries"] = [
			{"sales_order": "SO-4", "name": "DN-1", "posting_date": date(2026, 11, 12)},
			{"sales_order": "SO-7", "name": "DN-2", "posting_date": date(2026, 11, 12)},
		]
		SITE.tables["invoices"] = [
			{"sales_order": "SO-4", "name": "SI-1", "grand_total": 2005.24, "outstanding_amount": 0},
			{"sales_order": "SO-1", "name": "SI-2", "grand_total": 2005.24, "outstanding_amount": 2005.24},
		]
		SITE.tables["on_hand"] = {"PDT-0040-1-1-1-2-0": 2, "PDT-0040": 5}
		SITE.tables["units"] = [
			{
				"item_code": "PDT-0040-1-1-1-2-0",
				"item_name": "E-Stop 1-1-1-2-0",
				"source_configuration": None,
				"on_hand": 2,
				"warehouses": "Build Room - SF",
			},
			{
				"item_code": "PDT-0701",
				"item_name": "Service materials",
				"source_configuration": None,
				"on_hand": 4,
				"warehouses": "Stores - SF",
			},
		]

	def rows(self):
		return {row["name"]: row for row in self.answer()["orders"]["rows"]}

	def test_only_product_documents_are_on_the_board(self):
		self.assertEqual(sorted(self.rows()), ["SO-1", "SO-3", "SO-4", "SO-5"])

	def test_the_board_logs_nothing(self):
		# The Testing column (v1.826.0) runs its own queries; one the stub does not know would be
		# swallowed into the Error Log and the column would silently never fill.
		self.rows()
		self.assertEqual(SITE.errors, [])

	def test_a_hand_made_product_from_stock_is_not_held_for_a_test(self):
		# SO-1's PDT-0040-1-1-1-2-0 here carries no configuration: the gate does not apply to it.
		self.assertEqual(self.rows()["SO-1"]["stage"], "ready")

	def test_a_fountain_job_with_one_product_line_stays_off(self):
		# Manager review of #1464: SO-6 would have shown at its full value and then turned Late.
		self.assertNotIn("SO-6", self.rows())

	def test_a_closed_short_shipment_stays_off_and_claims_no_stock(self):
		# Manager review of #1464: the delivered branch ignored the status, so a Closed order came
		# back for 30 days as Late and claimed a unit from the shelf.
		self.assertNotIn("SO-7", self.rows())
		self.assertNotIn("PDT-0040", {u["item_code"] for u in self.answer()["stock"]["units"]})

	def test_delivered_only_by_a_return_is_not_on_the_board(self):
		self.assertNotIn("SO-8", self.rows())

	def test_amounts_are_the_product_lines_and_freight_is_not_a_product(self):
		rows = self.rows()
		self.assertEqual(rows["SO-5"]["product_amount"], 900)
		self.assertEqual([i["item_code"] for i in rows["SO-5"]["items"]], ["SW-CUSTOM-7"])
		self.assertEqual(rows["SO-5"]["lines"], ["Other"])
		self.assertNotIn("grand_total", rows["SO-1"])

	def test_each_order_is_at_its_stage(self):
		rows = self.rows()
		self.assertEqual((rows["SO-1"]["stage"], rows["SO-1"]["note"]), ("ready", "From stock"))
		self.assertEqual((rows["SO-3"]["stage"], rows["SO-3"]["note"]), ("building", "Work Order in process"))
		self.assertEqual(rows["SO-4"]["stage"], "delivered")
		self.assertEqual(rows["SO-4"]["delivered_on"], "2026-11-12")
		self.assertEqual(rows["SO-4"]["deliveries"], ["DN-1"])
		self.assertEqual(rows["SO-5"]["stage"], "ordered")

	def test_late_and_paid(self):
		rows = self.rows()
		self.assertTrue(rows["SO-3"]["late"])
		self.assertFalse(rows["SO-1"]["late"])
		self.assertFalse(rows["SO-4"]["late"], "delivered is never late")
		self.assertEqual(rows["SO-4"]["paid"], "paid")
		self.assertEqual(rows["SO-1"]["paid"], "unpaid")
		self.assertEqual(rows["SO-5"]["paid"], "not_invoiced")

	def test_the_shelf_shows_what_the_orders_claimed(self):
		units = self.answer()["stock"]["units"]
		self.assertEqual(
			[u["item_code"] for u in units], ["PDT-0040-1-1-1-2-0"], "a billing code is not a finished unit"
		)
		self.assertEqual((units[0]["on_hand"], units[0]["committed"], units[0]["free"]), (2.0, 1.0, 1.0))

	def test_pending_quantities_are_in_the_stock_uom(self):
		SITE.tables["so_headers"] = [so("SO-9")]
		SITE.tables["so_lines"] = [so_line("SO-9", "PDT-0151", 120, qty=2, delivered=1, factor=12)]
		SITE.tables["on_hand"] = {"PDT-0151": 11}
		row = self.rows()["SO-9"]
		self.assertEqual(row["items"][0]["pending_qty"], 12.0)
		self.assertEqual(row["stage"], "ordered", "11 units do not cover a case of 12")

	def test_the_board_query_excludes_closed_orders_and_return_notes(self):
		self.answer()
		query = next(q for q in SITE.queries if "FROM `tabSales Order` so" in q)
		self.assertIn("so.docstatus = 1", query)
		self.assertIn("so.status NOT IN %(off)s", query)
		self.assertIn("dn.is_return = 0", query)
		self.assertIn("dn.posting_date >= %(since)s", query)


def project(
	name, project_name, *, project_type="Products", status="Active", sales_order=None, end=None, active="Yes"
):
	return {
		"name": name,
		"project_name": project_name,
		"project_type": project_type,
		"customer": "C",
		"status": status,
		"is_active": active,
		"percent_complete": 10,
		"expected_end_date": end,
		"sales_order": sales_order,
	}


class TestControlPanels(Base):
	"""A product order with a control panel needs a project: off the board, once in the projects."""

	def setUp(self):
		super().setUp()
		SITE.tables["so_headers"] = [
			so("SO-P1", project="PRJ-1"),
			so("SO-P2"),  # no project yet (its only Project was Canceled)
			so("SO-P3"),  # linked by Project.sales_order
			so("SO-P4", project="PRJ-BUILD"),  # the hand-off made a Build project for it
			so("SO-P5", project="PRJ-1"),  # a second order on PRJ-1
			so("SO-P6", project="PRJ-3"),  # PRJ-4 names it too: the links disagree
			so("SO-E"),
		]
		SITE.tables["so_lines"] = [
			so_line("SO-P1", "PDT-0003", 28490),
			so_line("SO-P2", "PDT-0011", 2500),
			so_line("SO-P3", "PDT-0016", 9000),
			so_line("SO-P4", "PDT-0002", 9950),
			so_line("SO-P5", "PDT-0009", 2400),
			so_line("SO-P6", "PDT-0020", 1800),
			so_line("SO-E", "PDT-0040", 2200),
		]
		SITE.tables["projects"] = [
			project("PRJ-1", "Splash Wizard Industrial", end=date(2026, 12, 20)),
			project("PRJ-2", "Pool & Fountain Center", sales_order="SO-P3", end=date(2026, 11, 1)),
			project("PRJ-3", "Level Master", end=date(2026, 12, 1)),
			project("PRJ-4", "Level Master, second try", sales_order="SO-P6", end=date(2026, 12, 2)),
			project("PRJ-BUILD", "Splash Wizard MAX", project_type="Build", end=date(2026, 12, 10)),
			project("PRJ-OTHER", "A fountain build", project_type="Build"),
			project(
				"PRJ-CANCELED", "Old panel", project_type="Build", status="Canceled", sales_order="SO-P2"
			),
		]

	def rows(self):
		return {row["name"]: row for row in self.answer()["projects"]["rows"]}

	def orders_of(self, row):
		return [order["name"] for order in row["orders"]]

	def test_control_panel_orders_are_not_on_the_board(self):
		self.assertEqual([r["name"] for r in self.answer()["orders"]["rows"]], ["SO-E"])

	def test_a_linked_project_of_another_type_is_found(self):
		# Manager re-review of #1464: the hand-off picks Build for a Build + Products deal, so
		# reading type Products only left SO-P4 on "No project yet" and PRJ-BUILD nowhere.
		rows = self.rows()
		self.assertEqual(self.orders_of(rows["PRJ-BUILD"]), ["SO-P4"])
		self.assertEqual(rows["PRJ-BUILD"]["project_type"], "Build")
		self.assertNotIn("SO-P4", rows)
		self.assertNotIn("PRJ-OTHER", rows, "an unlinked project of another type stays out")

	def test_two_orders_on_one_project_both_show_on_it(self):
		self.assertEqual(self.orders_of(self.rows()["PRJ-1"]), ["SO-P1", "SO-P5"])

	def test_links_that_disagree_list_the_order_once(self):
		rows = self.rows()
		self.assertEqual(self.orders_of(rows["PRJ-3"]), ["SO-P6"], "the order's own link wins")
		self.assertEqual(self.orders_of(rows["PRJ-4"]), [])

	def test_a_canceled_project_is_no_project(self):
		rows = self.rows()
		self.assertNotIn("PRJ-CANCELED", rows)
		self.assertEqual(rows["SO-P2"]["kind"], "order")
		self.assertEqual(self.orders_of(rows["SO-P2"]), ["SO-P2"])
		self.assertEqual(rows["SO-P2"]["orders"][0]["product_amount"], 2500)

	def test_every_panel_order_shows_exactly_once(self):
		listed = [order["name"] for row in self.answer()["projects"]["rows"] for order in row["orders"]]
		self.assertEqual(sorted(listed), ["SO-P1", "SO-P2", "SO-P3", "SO-P4", "SO-P5", "SO-P6"])

	def test_soonest_end_first(self):
		names = [r["name"] for r in self.answer()["projects"]["rows"]]
		# SO-P2 (promise 12/01) and PRJ-3 (end 12/01) tie on the date; the name breaks it.
		self.assertEqual(names, ["PRJ-2", "PRJ-3", "SO-P2", "PRJ-4", "PRJ-BUILD", "PRJ-1"])

	def test_without_sales_order_read_the_products_projects_stand_alone(self):
		SITE.readable.discard("Sales Order")
		rows = self.answer()["projects"]["rows"]
		self.assertEqual([r["name"] for r in rows], ["PRJ-2", "PRJ-3", "PRJ-4", "PRJ-1"])
		self.assertTrue(all(r["orders"] == [] for r in rows))


class TestThePipeline(Base):
	def setUp(self):
		super().setUp()
		SITE.tables["quote_headers"] = [
			quote("QTN-1", opportunity="CRM-OPP-1"),  # an E-Stop plus freight
			quote("QTN-2"),  # a fountain job carrying one Splash Wizard (prod's SAL-QTN-2026-02070 shape)
			quote("QTN-3"),  # a service quote: PDT-0701 only
			quote("QTN-4", status="Ordered", docstatus=1, transaction_date=date(2026, 10, 1)),  # won
			quote("QTN-5", status="Lost", docstatus=1, transaction_date=date(2026, 9, 1)),  # lost
			quote(
				"QTN-6", status="Ordered", docstatus=1, transaction_date=date(2025, 1, 1)
			),  # won, before the year
			quote(
				"QTN-7", status="Lost", docstatus=1, transaction_date=date(2026, 10, 5)
			),  # a lost job: not a product
			quote("QTN-8", status="Expired", docstatus=1, transaction_date=date(2026, 8, 1)),  # expired: lost
			quote("QTN-9", status="Expired", docstatus=1, transaction_date=date(2026, 7, 1)),  # expired: lost
		]
		SITE.tables["quote_lines"] = [
			quote_line("QTN-1", "PDT-0040", 2200),
			quote_line("QTN-1", "SRV-105", 150),
			quote_line("QTN-2", "PDT-0002", 8165.64),
			quote_line("QTN-2", "NOZZLE-9", 106386.80),
			quote_line("QTN-3", "PDT-0701", 400),
			quote_line("QTN-4", "PDT-0040", 1668),
			quote_line("QTN-5", "PDT-0040", 1668),
			quote_line("QTN-6", "PDT-0040", 1668),
			quote_line("QTN-7", "PDT-0003", 28490),
			quote_line("QTN-7", "PUMP-1", 50000),
			quote_line("QTN-8", "PDT-0040", 1668),
			quote_line("QTN-9", "PDT-0040", 1668),
		]
		SITE.tables["opportunities"] = [
			{
				"name": "CRM-OPP-1",
				"title": "Pool",
				"opportunity_name": "Hotel pool E-Stop",
				"party_name": "C1",
				"customer_name": "Customer 1",
				"status": "Qualification",
				"currency": "USD",
				"transaction_date": date(2026, 11, 1),
				"expected_closing": date(2026, 12, 1),
			},
			# a deal no product quote names: the stub returns it anyway, and it must be dropped
			{
				"name": "CRM-OPP-9",
				"title": "Other",
				"opportunity_name": None,
				"party_name": "C9",
				"customer_name": None,
				"status": "Qualification",
				"currency": "USD",
				"transaction_date": date(2026, 11, 1),
				"expected_closing": None,
			},
		]

	def test_open_product_quotes_with_product_amounts(self):
		rows = self.answer()["pipeline"]["quotations"]["rows"]
		self.assertEqual([q["name"] for q in rows], ["QTN-1"])
		self.assertEqual(rows[0]["product_amount"], 2200, "the product lines, not the grand total")
		self.assertEqual(rows[0]["lines"], ["E-Stops"])
		self.assertNotIn("grand_total", rows[0])

	def test_the_win_rate_is_the_product_quotes_over_a_year(self):
		# 1 ordered; 1 Lost and 2 Expired are lost (manager re-review: an unordered submitted quote
		# expires and is never marked Lost). The job and the quote before the year do not count.
		quotations = self.answer()["pipeline"]["quotations"]
		self.assertEqual((quotations["won"], quotations["lost"], quotations["win_rate"]), (1, 3, 25))
		self.assertNotIn("QTN-8", [q["name"] for q in quotations["rows"]], "an expired quote is not open")

	def test_opportunities_come_through_their_product_quotes(self):
		rows = self.answer()["pipeline"]["opportunities"]["rows"]
		self.assertEqual([o["name"] for o in rows], ["CRM-OPP-1"])
		self.assertEqual(rows[0]["title"], "Hotel pool E-Stop")
		self.assertEqual(rows[0]["quotations"], ["QTN-1"])
		self.assertEqual(rows[0]["product_amount"], 2200)
		self.assertEqual(rows[0]["lines"], ["E-Stops"])

	def test_no_query_reads_the_products_value_stream(self):
		self.answer()
		self.assertFalse(any("tabValue Stream" in q for q in SITE.queries))


def component(code, name):
	return {
		"item_code": code,
		"item_name": name,
		"item_group": "E-Stop Components",
		"stock_uom": "Nos",
		"reorder_level": 0,
	}


def bin_row(code, warehouse, actual=0, projected=0, ordered=0):
	return {
		"item_code": code,
		"warehouse": warehouse,
		"actual_qty": actual,
		"projected_qty": projected,
		"ordered_qty": ordered,
	}


class TestTheStock(Base):
	def setUp(self):
		super().setUp()
		SITE.tables["components"] = [component("GRT6-M1", "Timer relay"), component("AF12Z", "Contactor")]
		SITE.tables["warehouses"] = [
			{"name": "Work In Progress - SF", "warehouse_name": "Work In Progress"},
			{"name": "Stores - SF", "warehouse_name": "Stores"},
		]

	def test_components_are_judged_on_projected_quantity(self):
		SITE.tables["component_bins"] = [
			# 3 on hand, all of it reserved for a build: projected 0, so short
			bin_row("GRT6-M1", "Stores - SF", actual=3, projected=0),
			# none on hand but 10 on a purchase order: projected 10, not short
			bin_row("AF12Z", "Stores - SF", actual=0, projected=10, ordered=10),
		]
		components = {c["item_code"]: c for c in self.answer()["stock"]["components"]}
		self.assertEqual(components["GRT6-M1"]["state"], "out")
		self.assertEqual(components["AF12Z"]["state"], "ok")
		self.assertEqual(components["AF12Z"]["on_order"], 10.0)

	def test_parts_moved_to_work_in_progress_are_not_counted_again(self):
		# Manager re-review of #1464: summed over every warehouse, the 5 relays already moved to
		# WIP for a build counted as available, and the shortage never showed.
		SITE.tables["component_bins"] = [
			bin_row("GRT6-M1", "Stores - SF", actual=0, projected=0),
			bin_row("GRT6-M1", "Work In Progress - SF", actual=5, projected=5),
		]
		relay = {c["item_code"]: c for c in self.answer()["stock"]["components"]}["GRT6-M1"]
		self.assertEqual((relay["on_hand"], relay["projected"], relay["state"]), (0.0, 0.0, "out"))

	def test_every_wip_source_counts(self):
		SITE.tables["warehouses"] = []
		SITE.default_wip = "WIP Default - SF"
		SITE.tables["wip_orders"] = [{"wip_warehouse": "Panel Bench - SF"}]
		SITE.tables["component_bins"] = [
			bin_row("GRT6-M1", "WIP Default - SF", actual=4, projected=4),
			bin_row("GRT6-M1", "Panel Bench - SF", actual=2, projected=2),
			bin_row("GRT6-M1", "Stores - SF", actual=1, projected=1),
		]
		relay = {c["item_code"]: c for c in self.answer()["stock"]["components"]}["GRT6-M1"]
		self.assertEqual((relay["on_hand"], relay["projected"]), (1.0, 1.0))


class TestTheCLAUDEmdRules(Base):
	def test_no_get_all_field_is_a_sql_function_string(self):
		SITE.tables["projects"] = [{"name": "PRJ-1", "project_name": "P", "status": "Active"}]
		self.answer()
		self.assertTrue(SITE.get_all_calls)
		for doctype, fields in SITE.get_all_calls:
			for field in fields or ():
				self.assertNotIn("(", str(field), f"{doctype}: {field} (Frappe 16 refuses it)")

	def test_no_query_names_the_configurator_column_on_a_site_without_it(self):
		SITE.has_source_column = False
		SITE.tables["so_headers"] = [so("SO-1")]
		SITE.tables["so_lines"] = [so_line("SO-1", "PDT-0040", 2200)]
		SITE.tables["quote_headers"] = [quote("QTN-1")]
		SITE.tables["quote_lines"] = [quote_line("QTN-1", "PDT-0040", 2200)]
		self.answer()
		self.assertTrue(SITE.queries)
		for query in SITE.queries:
			self.assertNotIn("custom_source_configuration", query)

	def test_with_the_column_a_configured_item_needs_no_pdt_code(self):
		self.answer()
		query = next(q for q in SITE.queries if "FROM `tabSales Order` so" in q)
		self.assertIn("coalesce(i.custom_source_configuration, '') <> ''", query)


if __name__ == "__main__":
	unittest.main()


def unit(name, item, built_by, work_order=None, status="Active"):
	return {"name": name, "item_code": item, "work_order": work_order, "custom_built_by": built_by, "status": status}


def test_row(serial, item, status, by, day="2026-11-17", template="Pre-Shipment Functional Test - E-Stop", created=None):
	return {
		"name": "QI-" + serial + "-" + by + "-" + status,
		"item_serial_no": serial,
		"item_code": item,
		"status": status,
		"docstatus": 1,
		"inspected_by": by,
		"report_date": day,
		"creation": created or day + " 10:00:00",
		"quality_inspection_template": template,
	}


CONFIGURED = "PDT-0040-1-1-1-2-0"
LOGAN = "logan@example.com"
NATHAN = "nathan@example.com"


class TestTheTestingColumn(Base):
	"""v1.826.0: a built configured unit waits in Testing until its pre-shipment test passes, signed by
	someone other than its builder. A unit from the shelf passes when tested units on hand cover it."""

	def setUp(self):
		super().setUp()
		SITE.tables["so_headers"] = [so("SO-10"), so("SO-11", delivery_date="2026-12-05")]
		SITE.tables["so_lines"] = [
			so_line("SO-10", CONFIGURED, 1668, source="CFG-1"),
			so_line("SO-11", CONFIGURED, 1668, source="CFG-1"),
		]
		SITE.tables["work_orders"] = [
			{
				"name": "WO-10",
				"sales_order": "SO-10",
				"production_item": CONFIGURED,
				"status": "Completed",
				"docstatus": 1,
				"qty": 1,
				"produced_qty": 1,
			}
		]
		SITE.tables["on_hand"] = {CONFIGURED: 2}  # SO-10's built unit, plus one built ahead
		SITE.tables["serials_wo"] = [unit("SN-1", CONFIGURED, LOGAN, work_order="WO-10")]
		SITE.tables["serials_active"] = [unit("SN-2", CONFIGURED, NATHAN)]
		SITE.tables["tests"] = [test_row("SN-2", CONFIGURED, "Accepted", LOGAN)]

	def rows(self):
		answer = self.answer()
		self.assertEqual(SITE.errors, [])
		return {row["name"]: row for row in answer["orders"]["rows"]}

	def test_built_and_untested_waits_in_testing(self):
		row = self.rows()["SO-10"]
		self.assertEqual((row["stage"], row["note"]), ("testing", "Waiting for the pre-shipment test"))

	def test_passed_by_someone_else_is_ready(self):
		SITE.tables["tests"].append(test_row("SN-1", CONFIGURED, "Accepted", NATHAN))
		self.assertEqual(self.rows()["SO-10"]["stage"], "ready")

	def test_tested_by_its_builder_does_not_count(self):
		SITE.tables["tests"].append(test_row("SN-1", CONFIGURED, "Accepted", LOGAN))
		self.assertEqual(self.rows()["SO-10"]["stage"], "testing")

	def test_a_failed_test_says_so(self):
		SITE.tables["tests"].append(test_row("SN-1", CONFIGURED, "Rejected", NATHAN))
		row = self.rows()["SO-10"]
		self.assertEqual((row["stage"], row["note"]), ("testing", "Test failed"))

	def test_fixed_and_tested_again_is_ready(self):
		SITE.tables["tests"] += [
			test_row("SN-1", CONFIGURED, "Rejected", NATHAN, day="2026-11-16"),
			test_row("SN-1", CONFIGURED, "Accepted", NATHAN, day="2026-11-17"),
		]
		self.assertEqual(self.rows()["SO-10"]["stage"], "ready")

	def test_a_test_on_another_template_does_not_count(self):
		SITE.tables["tests"].append(test_row("SN-1", CONFIGURED, "Accepted", NATHAN, template="Incoming"))
		self.assertEqual(self.rows()["SO-10"]["stage"], "testing")

	def test_from_the_shelf_a_tested_unit_on_hand_makes_it_ready(self):
		row = self.rows()["SO-11"]
		self.assertEqual((row["stage"], row["note"]), ("ready", "From stock"))

	def test_from_the_shelf_with_no_tested_unit_it_waits(self):
		SITE.tables["tests"] = []
		self.assertEqual(self.rows()["SO-11"]["stage"], "testing")

	def test_a_unit_built_for_another_order_is_not_shelf_stock(self):
		# SN-1 is SO-10's own unit: passing it must not make SO-11's shelf unit read tested.
		SITE.tables["serials_active"].append(unit("SN-1", CONFIGURED, LOGAN, work_order="WO-10"))
		SITE.tables["tests"] = [test_row("SN-1", CONFIGURED, "Accepted", NATHAN)]
		rows = self.rows()
		self.assertEqual((rows["SO-10"]["stage"], rows["SO-11"]["stage"]), ("ready", "testing"))

	def test_the_switch_off_brings_back_ready_as_soon_as_built(self):
		SITE.settings = {"require_test": 0}
		rows = self.rows()
		self.assertEqual((rows["SO-10"]["stage"], rows["SO-11"]["stage"]), ("ready", "ready"))

	def test_no_private_key_reaches_the_browser(self):
		for row in self.rows().values():
			self.assertNotIn("_tested_items", row)

"""The Products tab's endpoint, project_dashboard.get_products_data (v1.824.0), run on a stub.

Prod had no product Sales Order when the tab shipped, so the endpoint's first job is to answer an
empty site with every section present and empty, never a raise. Its second is to keep what it must
not show: a section the person cannot read comes back {"allowed": False}, the paid chip needs Sales
Invoice read on top of Sales Order (Logan builds the units and holds no accounts role), and the
shelf commitments it works out for the stock section never reach the browser.

The module is loaded from its file under a private name, with a ``frappe`` stub in sys.modules
for the life of this suite only (setUpModule / tearDownModule), so the package's own
``__init__`` (which imports half the app) never runs. The stub's ``db.sql`` answers by the table
a query reads, formats every query with its parameters the way the driver does (a stray ``%`` or a
missing key fails the test, as it would fail on prod), and records what was asked, so the suite can
also hold the CLAUDE.md rules: no SQL function written as a ``get_all`` string field, and no query
naming the configurator's ``custom_source_configuration`` column on a site that lacks it.

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


class Site:
	"""The stub's database and permissions, reset per test."""

	def __init__(self):
		self.tables = {}
		self.readable = {"Sales Order", "Sales Invoice", "Quotation", "Opportunity", "Item", "Bin", "Project"}
		self.has_source_column = True
		self.queries = []
		self.get_all_calls = []
		self.errors = []
		self.raise_on = None

	def rows(self, key):
		return [_Dict(row) for row in self.tables.get(key, [])]


SITE = Site()

# The table each query reads, by a phrase only that query has.
_ROUTES = (
	("FROM `tabSales Order Item` soi", "so_items"),
	("FROM `tabWork Order`", "work_orders"),
	("SELECT DISTINCT dni.against_sales_order", "deliveries"),
	("FROM `tabSales Invoice Item` sii", "invoices"),
	("SELECT item_code, SUM(actual_qty) AS qty", "on_hand"),
	("FROM `tabQuotation Item` qi", "quote_items"),
	("FROM `tabOpportunity` o", "opportunities"),
	("FROM `tabOpportunity Item`", "opportunity_items"),
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


def _get_all(doctype, filters=None, fields=None, **kwargs):
	SITE.get_all_calls.append((doctype, fields))
	if doctype == "Project":
		return SITE.rows("projects")
	return []


def setUpModule():
	for name in _STUBBED:
		_saved_modules[name] = sys.modules.get(name)

	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn) if not (a and callable(a[0])) else a[0]
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.db = types.SimpleNamespace(sql=_sql, has_column=_has_column, get_value=lambda *a, **k: None)
	frappe.get_all = _get_all
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


def so_line(
	name,
	item_code,
	qty=1,
	delivered=0,
	*,
	source=None,
	status="To Deliver and Bill",
	per_delivered=0,
	per_billed=0,
	delivery_date="2026-12-01",
	total=2005.24,
):
	return {
		"name": name,
		"customer": "CUST-" + name,
		"customer_name": "Customer " + name,
		"transaction_date": date(2026, 11, 10),
		"delivery_date": date.fromisoformat(delivery_date) if delivery_date else None,
		"status": status,
		"per_delivered": per_delivered,
		"per_billed": per_billed,
		"grand_total": total,
		"currency": "USD",
		"item_code": item_code,
		"item_name": item_code,
		"qty": qty,
		"delivered_qty": delivered,
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
		self.assertEqual(answer["pipeline"]["quotations"], {"rows": [], "open_days": 90, "allowed": True})
		opportunities = answer["pipeline"]["opportunities"]
		self.assertEqual(opportunities["rows"], [])
		self.assertIsNone(opportunities["win_rate"])
		self.assertEqual((opportunities["won"], opportunities["lost"]), (0, 0))
		self.assertEqual(answer["stock"]["units"], [])
		self.assertEqual(answer["stock"]["components"], [])
		self.assertEqual(answer["projects"], {"rows": [], "allowed": True})
		self.assertEqual(SITE.errors, [])

	def test_the_shelf_commitments_never_reach_the_browser(self):
		self.assertNotIn("_committed", self.answer()["orders"])

	def test_without_the_page_role_it_answers_the_dashboards_denial(self):
		dashboard.check_permission = lambda: False
		answer = self.answer()
		self.assertEqual(set(answer), {"error"})
		self.assertIn("permission", answer["error"])
		self.assertEqual(SITE.queries, [], "denied before any read")


class TestPermissions(Base):
	def test_a_section_the_person_cannot_read_is_not_allowed_and_the_rest_still_draw(self):
		SITE.readable -= {"Quotation", "Bin"}
		answer = self.answer()
		self.assertEqual(answer["pipeline"]["quotations"], {"allowed": False})
		self.assertEqual(answer["stock"], {"allowed": False})
		self.assertTrue(answer["pipeline"]["opportunities"]["allowed"])
		self.assertTrue(answer["orders"]["allowed"])
		self.assertTrue(answer["projects"]["allowed"])
		self.assertFalse(
			any("`tabQuotation Item`" in q for q in SITE.queries), "nothing read for a denied section"
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
		SITE.tables["so_items"] = [so_line("SO-1", "PDT-0040")]
		SITE.readable.discard("Sales Invoice")
		answer = self.answer()
		self.assertFalse(answer["orders"]["paid_visible"])
		self.assertIsNone(answer["orders"]["rows"][0]["paid"])
		self.assertFalse(
			any("`tabSales Invoice Item`" in q for q in SITE.queries), "no invoice read without the right"
		)


class TestAFailedSection(Base):
	def test_a_section_that_raises_is_logged_and_kept_to_itself(self):
		SITE.raise_on = "quote_items"
		answer = self.answer()
		self.assertEqual(answer["pipeline"]["quotations"], {"allowed": True, "failed": True})
		self.assertTrue(answer["orders"]["allowed"])
		self.assertNotIn("failed", answer["orders"])
		self.assertEqual(len(SITE.errors), 1)
		self.assertNotIn("\n", SITE.errors[0]["title"], "a one-line title (v16's log_error heuristic)")
		self.assertIn("Traceback", SITE.errors[0]["message"])

	def test_a_failed_board_still_lets_the_stock_section_draw(self):
		SITE.raise_on = "so_items"
		answer = self.answer()
		self.assertEqual(answer["orders"], {"allowed": True, "failed": True})
		self.assertTrue(answer["stock"]["allowed"])


class TestTheBoard(Base):
	def setUp(self):
		super().setUp()
		SITE.tables["so_items"] = [
			# from stock: a common configuration on the shelf, no Work Order
			so_line("SO-1", "PDT-0040-1-1-1-2-0", delivery_date="2026-11-25"),
			# a service order with a PDT billing line only: not a product order
			so_line("SO-2", "PDT-0701"),
			# built to order, Work Order in process, promise date gone by
			so_line("SO-3", "PDT-0040-3-2-0-0-0", delivery_date="2026-11-10"),
			# delivered, invoiced and paid
			so_line("SO-4", "PDT-0003", status="Completed", per_delivered=100, per_billed=100),
			# a configured Item with no PDT code, and a hand-made Item on the same order
			so_line("SO-5", "SW-CUSTOM-7", source="CFG-0009"),
			so_line("SO-5", "H-6470"),
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
		SITE.tables["deliveries"] = [
			{"sales_order": "SO-4", "name": "DN-1", "posting_date": date(2026, 11, 12)}
		]
		SITE.tables["invoices"] = [
			{"sales_order": "SO-4", "name": "SI-1", "grand_total": 2005.24, "outstanding_amount": 0},
			{"sales_order": "SO-1", "name": "SI-2", "grand_total": 2005.24, "outstanding_amount": 2005.24},
		]
		SITE.tables["on_hand"] = {"PDT-0040-1-1-1-2-0": 2}
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

	def test_only_product_orders_are_on_the_board(self):
		self.assertEqual(sorted(self.rows()), ["SO-1", "SO-3", "SO-4", "SO-5"])

	def test_a_hand_made_item_on_a_product_order_is_not_listed_as_a_product(self):
		self.assertEqual([i["item_code"] for i in self.rows()["SO-5"]["items"]], ["SW-CUSTOM-7"])
		self.assertEqual(self.rows()["SO-5"]["lines"], ["Other"])

	def test_each_order_is_at_its_stage(self):
		rows = self.rows()
		self.assertEqual((rows["SO-1"]["stage"], rows["SO-1"]["note"]), ("ready", "From stock"))
		self.assertEqual((rows["SO-3"]["stage"], rows["SO-3"]["note"]), ("building", "Work Order in process"))
		self.assertEqual(rows["SO-4"]["stage"], "delivered")
		self.assertEqual(rows["SO-4"]["delivered_on"], "2026-11-12")
		self.assertEqual(rows["SO-4"]["deliveries"], ["DN-1"])
		self.assertEqual(rows["SO-5"]["stage"], "ordered")
		self.assertEqual(
			rows["SO-3"]["work_orders"], [{"name": "WO-1", "status": "In Process", "docstatus": 1}]
		)

	def test_late_and_paid(self):
		rows = self.rows()
		self.assertTrue(rows["SO-3"]["late"])
		self.assertFalse(rows["SO-1"]["late"])
		self.assertFalse(rows["SO-4"]["late"], "delivered is never late")
		self.assertEqual(rows["SO-4"]["paid"], "paid")
		self.assertEqual(rows["SO-1"]["paid"], "unpaid")
		self.assertEqual(rows["SO-5"]["paid"], "not_invoiced")
		self.assertEqual(rows["SO-1"]["delivery_date"], "2026-11-25", "dates go out as ISO strings")

	def test_the_shelf_shows_what_the_orders_claimed(self):
		units = self.answer()["stock"]["units"]
		self.assertEqual(
			[u["item_code"] for u in units], ["PDT-0040-1-1-1-2-0"], "a billing code is not a finished unit"
		)
		self.assertEqual((units[0]["on_hand"], units[0]["committed"], units[0]["free"]), (2.0, 1.0, 1.0))
		self.assertEqual(units[0]["line"], "E-Stops")

	def test_the_board_reads_only_submitted_orders_and_recent_deliveries(self):
		self.answer()
		query = next(q for q in SITE.queries if "FROM `tabSales Order Item` soi" in q)
		self.assertIn("so.docstatus = 1", query)
		self.assertIn("so.per_delivered < 100", query)
		self.assertIn("dn.posting_date >= %(since)s", query)


class TestThePipeline(Base):
	def test_open_quotes_and_opportunities_and_the_win_rate(self):
		SITE.tables["quote_items"] = [
			{
				"name": "QTN-1",
				"quotation_to": "Customer",
				"party_name": "C1",
				"customer_name": "Customer 1",
				"transaction_date": date(2026, 11, 17),
				"valid_till": None,
				"status": "Draft",
				"docstatus": 0,
				"grand_total": 2200,
				"currency": "USD",
				"opportunity": None,
				"item_code": "PDT-0040",
				"source_configuration": None,
			},
			{
				"name": "QTN-1",
				"quotation_to": "Customer",
				"party_name": "C1",
				"customer_name": "Customer 1",
				"transaction_date": date(2026, 11, 17),
				"valid_till": None,
				"status": "Draft",
				"docstatus": 0,
				"grand_total": 2200,
				"currency": "USD",
				"opportunity": None,
				"item_code": "PDT-0701",
				"source_configuration": None,
			},
			# a service quote: only the billing line
			{
				"name": "QTN-2",
				"quotation_to": "Customer",
				"party_name": "C2",
				"customer_name": "Customer 2",
				"transaction_date": date(2026, 11, 16),
				"valid_till": None,
				"status": "Draft",
				"docstatus": 0,
				"grand_total": 900,
				"currency": "USD",
				"opportunity": None,
				"item_code": "PDT-0701",
				"source_configuration": None,
			},
		]
		SITE.tables["opportunities"] = [
			{
				"name": "CRM-OPP-1",
				"title": "Pool",
				"opportunity_name": "Hotel pool E-Stop",
				"opportunity_from": "Customer",
				"party_name": "C1",
				"customer_name": "Customer 1",
				"status": "Qualification",
				"opportunity_amount": 2000,
				"currency": "USD",
				"transaction_date": date(2026, 11, 1),
				"expected_closing": date(2026, 12, 1),
				"opportunity_owner": "x@example.com",
			},
			{
				"name": "CRM-OPP-2",
				"title": "Won",
				"opportunity_name": None,
				"opportunity_from": "Customer",
				"party_name": "C2",
				"customer_name": None,
				"status": "Closed Won",
				"opportunity_amount": 1700,
				"currency": "USD",
				"transaction_date": date(2026, 9, 1),
				"expected_closing": None,
				"opportunity_owner": None,
			},
			{
				"name": "CRM-OPP-3",
				"title": "Lost",
				"opportunity_name": None,
				"opportunity_from": "Customer",
				"party_name": "C3",
				"customer_name": None,
				"status": "Lost",
				"opportunity_amount": 0,
				"currency": "USD",
				"transaction_date": date(2026, 10, 1),
				"expected_closing": None,
				"opportunity_owner": None,
			},
			# won, but before the year the win rate counts
			{
				"name": "CRM-OPP-4",
				"title": "Old",
				"opportunity_name": None,
				"opportunity_from": "Customer",
				"party_name": "C4",
				"customer_name": None,
				"status": "Closed Won",
				"opportunity_amount": 0,
				"currency": "USD",
				"transaction_date": date(2025, 1, 1),
				"expected_closing": None,
				"opportunity_owner": None,
			},
		]
		SITE.tables["opportunity_items"] = [{"parent": "CRM-OPP-1", "item_code": "PDT-0040"}]
		pipeline = self.answer()["pipeline"]
		quotes = pipeline["quotations"]["rows"]
		self.assertEqual([q["name"] for q in quotes], ["QTN-1"])
		self.assertEqual(quotes[0]["lines"], ["E-Stops"])
		self.assertEqual(quotes[0]["party"], "Customer 1")
		opps = pipeline["opportunities"]
		self.assertEqual([o["name"] for o in opps["rows"]], ["CRM-OPP-1"])
		self.assertEqual(opps["rows"][0]["title"], "Hotel pool E-Stop")
		self.assertEqual(opps["rows"][0]["lines"], ["E-Stops"])
		self.assertEqual((opps["won"], opps["lost"], opps["win_rate"]), (1, 1, 50))

	def test_historical_deals_are_left_out(self):
		self.answer()
		query = next(q for q in SITE.queries if "FROM `tabOpportunity` o" in q)
		self.assertIn("custom_is_historical", query)
		self.assertIn("vs.value_stream = %(stream)s", query)


class TestTheProjects(Base):
	def test_active_products_projects_soonest_end_first_with_late(self):
		SITE.tables["projects"] = [
			{
				"name": "PRJ-2",
				"project_name": "Splash Wizard MAX",
				"customer": "C",
				"status": "Active",
				"percent_complete": 40,
				"expected_start_date": None,
				"expected_end_date": None,
			},
			{
				"name": "PRJ-1",
				"project_name": "VFD Bypass",
				"customer": None,
				"status": "Active",
				"percent_complete": 10,
				"expected_start_date": None,
				"expected_end_date": date(2026, 11, 1),
			},
		]
		rows = self.answer()["projects"]["rows"]
		self.assertEqual([p["name"] for p in rows], ["PRJ-1", "PRJ-2"])
		self.assertTrue(rows[0]["late"])
		self.assertFalse(rows[1]["late"])
		self.assertEqual({p["line"] for p in rows}, {"Control Panels"})
		doctype, fields = SITE.get_all_calls[-1]
		self.assertEqual(doctype, "Project")


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
		SITE.tables["so_items"] = [so_line("SO-1", "PDT-0040")]
		self.answer()
		self.assertTrue(SITE.queries)
		for query in SITE.queries:
			self.assertNotIn("custom_source_configuration", query)

	def test_with_the_column_a_configured_item_needs_no_pdt_code(self):
		self.answer()
		query = next(q for q in SITE.queries if "FROM `tabSales Order Item` soi" in q)
		self.assertIn("coalesce(i.custom_source_configuration, '') <> ''", query)


if __name__ == "__main__":
	unittest.main()

"""Bench-free tests for the Estimate's Quotation, its seed patches and its delete links.

Runs the real ``api/estimating.py``, ``estimating/seed.py``, ``estimating/links.py`` and the four seed
patches against an in-memory ``frappe`` stub installed in ``setUpModule``, so it has its own CI step.
What it holds:

* **Issue Quotation** goes to the account, or to the opportunity's party when there is no account; one
  line per phase at the phase's price on EST-SCOPE; no taxes on a build job even when ERPNext's
  ``set_missing_values`` copies a customer's template in; the company's default taxes on a Product job;
  Pricing Rules off; the deal on ``custom_source_opportunity`` and never ``opportunity``; a draft
  quotation refreshed in place and a submitted one left alone; a "valid until" already past dropped;
  an unpriced line, a superseded revision, a missing Item and a user who cannot make Quotations all
  refused. And EST-SCOPE gets a 0 Item Price on the quotation's price list before the insert, so
  ERPNext's auto-insert never makes the first phase price its list price.
* **The seeds never raise.** A failing step rolls back, writes one Error Log and the migrate goes on;
  one bad template does not stop the others; EST-SCOPE is made with unit "Unit" when "Nos" is
  disabled, named in the Item Naming Schema's shape (its only remaining finding is the category), and
  its zero price is made once.
* **An Estimate and its Quotation can each be deleted first**: the on_trash hooks clear the Links that
  would make ``delete_doc`` refuse, and deleting the newest revision makes the one before it editable.

Run: python -m unittest erpnext_enhancements.tests.test_estimating_api -v
"""

import datetime
import importlib
import importlib.util
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)

STORE = {}
LOG = {"errors": [], "commits": 0, "rollbacks": 0, "comments": []}
FAIL_INSERT = set()
PERMS = {"Quotation": True}
TODAY = datetime.date(2026, 11, 20)


class Thrown(Exception):
	pass


class PermissionError_(Thrown):
	pass


class ValidationError_(Exception):
	pass


def reset():
	STORE.clear()
	LOG.update(errors=[], commits=0, rollbacks=0, comments=[])
	FAIL_INSERT.clear()
	PERMS["Quotation"] = True
	STORE["UOM"] = {"Unit": {"name": "Unit", "enabled": 1}, "Nos": {"name": "Nos", "enabled": 0}}
	STORE["Item Group"] = {"Service": {"name": "Service", "is_group": 0}}
	STORE["Price List"] = {"Standard Selling": {"name": "Standard Selling"}}
	STORE["Singles"] = {("Selling Settings", "selling_price_list"): "Standard Selling"}
	for table in ("Estimate Rate Card", "Estimate Template", "Estimate", "Quotation", "Item", "Item Price"):
		STORE.setdefault(table, {})


def _matches(row, filters):
	for key, want in (filters or {}).items():
		if row.get(key) != want:
			return False
	return True


def _find(doctype, filters):
	rows = STORE.get(doctype, {})
	if isinstance(filters, str):
		return rows.get(filters)
	for row in rows.values():
		if _matches(row, filters):
			return row
	return None


class Row(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value

	def as_dict(self):
		return dict(self)


class Meta:
	FIELDS = {"Quotation": {"custom_source_opportunity", "custom_estimate"}}

	def __init__(self, doctype):
		self.doctype = doctype

	def has_field(self, field):
		return field in self.FIELDS.get(self.doctype, set())


class FakeDoc:
	"""A new_doc / get_doc stand-in: attributes, child tables, insert into STORE."""

	counter = 0

	def __init__(self, doctype, **fields):
		self.__dict__["doctype"] = doctype
		self.__dict__["_f"] = {}
		self.__dict__["flags"] = types.SimpleNamespace()
		self.__dict__["meta"] = Meta(doctype)
		self.__dict__["calls"] = []
		for k, v in fields.items():
			setattr(self, k, v)

	def __getattr__(self, key):
		return self.__dict__["_f"].get(key)

	def __setattr__(self, key, value):
		self.__dict__["_f"][key] = value

	def get(self, key, default=None):
		value = self.__dict__["_f"].get(key)
		return default if value is None else value

	def set(self, key, value):
		self.__dict__["_f"][key] = [Row(v) for v in value] if isinstance(value, list) else value

	def update(self, values):
		for k, v in values.items():
			self.set(k, v)

	def append(self, table, row):
		rows = self.__dict__["_f"].setdefault(table, [])
		rows.append(Row(row, idx=len(rows) + 1))
		return rows[-1]

	def is_new(self):
		return not self.name

	def check_permission(self, ptype):
		self.calls.append(f"check_permission:{ptype}")

	def auto_insert_prices(self):
		"""ERPNext's insert_item_price with prod's settings (auto-insert on, update-existing off): a row
		with a rate, on a list with no price for its item, makes an Item Price at that rate. It runs from
		set_missing_values and again from validate on insert/save."""
		for item in self.get("items") or []:
			if not item.get("rate") or not self.selling_price_list:
				continue
			key = {"item_code": item["item_code"], "price_list": self.selling_price_list}
			if not _find("Item Price", key):
				STORE["Item Price"][f"AUTO-{len(STORE['Item Price']) + 1}"] = dict(
					key, uom=item.get("stock_uom"), price_list_rate=item["rate"]
				)

	def run_method(self, method):
		self.calls.append(method)
		if self.doctype == "Quotation" and method == "set_missing_values":
			# What ERPNext does here that matters: a selling price list from the defaults, and the
			# customer's tax template copied in (update_if_missing) with its rows appended.
			if not self.selling_price_list:
				self.selling_price_list = "Standard Selling"
			if not self.taxes_and_charges:
				self.taxes_and_charges = "Utah Sales Tax"
				self.append("taxes", {"charge_type": "On Net Total", "rate": 7.25})
			self.auto_insert_prices()
		self.calls.append(f"after:{method}:{len(STORE['Item Price'])}")

	def insert(self, ignore_permissions=False):
		if self.doctype in FAIL_INSERT:
			raise ValidationError_(f"{self.doctype} refused")
		if self.doctype == "Quotation":
			self.auto_insert_prices()  # validate runs set_missing_values again
		FakeDoc.counter += 1
		name = (
			self.get("item_code")
			if self.doctype == "Item"
			else self.get("role_name")
			or self.get("rate_card_name")
			or self.get("template_name")
			or f"{self.doctype[:3].upper()}-{FakeDoc.counter:04d}"
		)
		self.name = name
		row = dict(self.__dict__["_f"])
		row.setdefault("docstatus", 0)
		STORE.setdefault(self.doctype, {})[name] = row
		self.calls.append("insert")
		return self

	def save(self, ignore_permissions=False):
		if self.doctype == "Quotation":
			self.auto_insert_prices()
		STORE[self.doctype][self.name].update(self.__dict__["_f"])
		self.calls.append("save")
		return self

	def db_set(self, values, value=None):
		if isinstance(values, str):
			values = {values: value}
		for k, v in values.items():
			setattr(self, k, v)
		STORE["Estimate"].setdefault(self.name, {}).update(values)

	def add_comment(self, kind, text):
		LOG["comments"].append(text)


def setUpModule():
	reset()
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s: s
	frappe.PermissionError = PermissionError_
	frappe.ValidationError = ValidationError_
	frappe.session = types.SimpleNamespace(user="estimator@example.com")
	frappe.flags = types.SimpleNamespace()

	def throw(msg, exc=None, title=None, **k):
		raise (exc or Thrown)(msg)

	frappe.throw = throw
	frappe.whitelist = lambda **k: (lambda fn: fn)
	frappe.has_permission = lambda doctype, ptype="read", throw=False, **k: PERMS.get(doctype, True)
	frappe.get_traceback = lambda: "Traceback: stub"
	frappe.log_error = lambda title=None, message=None, **k: LOG["errors"].append(title)
	frappe.get_meta = Meta
	frappe.get_app_path = lambda app, *parts: os.path.join(APP, *parts)

	def new_doc(doctype, **fields):
		return FakeDoc(doctype, **fields)

	def get_doc(doctype, name=None):
		if doctype == "Estimate":
			return ESTIMATES[name]
		row = STORE[doctype][name]
		doc = FakeDoc(doctype)
		doc.__dict__["_f"].update(row)
		return doc

	def get_all(doctype, filters=None, fields=None, pluck=None, **k):
		rows = [Row(r, name=n) for n, r in STORE.get(doctype, {}).items() if _matches(r, filters)]
		return [r[pluck] for r in rows] if pluck else rows

	frappe.new_doc = new_doc
	frappe.get_doc = get_doc
	frappe.get_all = get_all

	class DB:
		def exists(self, doctype, filters=None):
			if doctype == "DocType":
				return True
			return bool(_find(doctype, filters))

		def get_value(self, doctype, filters, fields="name", as_dict=False, order_by=None, **k):
			row = _find(doctype, filters)
			if row is None:
				return None
			if isinstance(filters, dict) and fields == "name":
				return next(n for n, r in STORE[doctype].items() if r is row)
			if isinstance(fields, (list, tuple)):
				out = Row({f: row.get(f) for f in fields})
				return out if as_dict else tuple(out.values())
			return row.get(fields)

		def set_value(self, doctype, name, field, value=None, update_modified=True):
			values = field if isinstance(field, dict) else {field: value}
			STORE[doctype][name].update(values)

		def get_single_value(self, doctype, field):
			return STORE["Singles"].get((doctype, field))

		def count(self, doctype):
			return len(STORE.get(doctype, {}))

		def table_exists(self, doctype):
			return True

		def commit(self):
			LOG["commits"] += 1

		def rollback(self):
			LOG["rollbacks"] += 1

	frappe.db = DB()

	utils = types.ModuleType("frappe.utils")
	utils.escape_html = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.nowdate = lambda: TODAY.isoformat()
	utils.getdate = lambda d: d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d))
	frappe.utils = utils

	erpnext = types.ModuleType("erpnext")
	controllers = types.ModuleType("erpnext.controllers")
	accounts = types.ModuleType("erpnext.controllers.accounts_controller")
	accounts._get_default_taxes_and_charges = lambda doctype, company=None: {
		"taxes_and_charges": "Company Default Tax",
		"taxes": [{"charge_type": "On Net Total", "rate": 7.25}],
	}

	for name, mod in (
		("frappe", frappe),
		("frappe.utils", utils),
		("erpnext", erpnext),
		("erpnext.controllers", controllers),
		("erpnext.controllers.accounts_controller", accounts),
	):
		sys.modules[name] = mod

	global api, seed, links
	seed = importlib.reload(importlib.import_module("erpnext_enhancements.estimating.seed"))
	links = importlib.reload(importlib.import_module("erpnext_enhancements.estimating.links"))
	api = importlib.reload(importlib.import_module("erpnext_enhancements.api.estimating"))


def tearDownModule():
	for name in ("frappe", "frappe.utils", "erpnext", "erpnext.controllers", "erpnext.controllers.accounts_controller"):
		sys.modules.pop(name, None)


ESTIMATES = {}


def make_estimate(**fields):
	doc = FakeDoc("Estimate")
	base = {
		"name": "EST-26-0001",
		"status": "Draft",
		"customer": "Example Account",
		"opportunity": "CRM-OPP-0001",
		"company": "Example Co",
		"job_kind": "Design-Build",
		"terms": "Net 30.",
		"total_amount": 3000,
		"phases": [
			Row(phase_number=100, label="Design and planning"),
			Row(phase_number=120, label="Schematic design"),
			Row(phase_number=500, label="Mechanical room", scope_text="Pumps & filters."),
		],
		"lines": [
			Row(idx=1, phase_number=120, description="Schematic design", amount=1000.0),
			Row(idx=2, phase_number=500, description="Pump", amount=1500.0),
			Row(idx=3, phase_number=500, description="Basin rocks", amount=500.0, basis="Allowance"),
			Row(idx=4, phase_number=500, description="Tile", amount=0, by_others=1),
		],
	}
	base.update(fields)
	for k, v in base.items():
		setattr(doc, k, v)
	doc.__dict__["save"] = lambda: doc.calls.append("estimate.save")
	ESTIMATES[doc.name] = doc
	STORE["Estimate"][doc.name] = {"status": doc.status, "quotation": doc.quotation}
	return doc


def seed_item():
	seed.seed_scope_item()
	FakeDoc.counter = 0


def issued():
	name = api.issue_quotation("EST-26-0001")
	return name, STORE["Quotation"][name]


class TestIssueQuotation(unittest.TestCase):
	def setUp(self):
		reset()
		ESTIMATES.clear()
		seed_item()

	def test_one_line_per_phase_at_its_price_with_no_tax_on_a_build_job(self):
		make_estimate()
		name, q = issued()
		self.assertEqual((q["quotation_to"], q["party_name"]), ("Customer", "Example Account"))
		self.assertEqual([i["item_name"] for i in q["items"]], ["120 Schematic design", "500 Mechanical room"])
		self.assertEqual([(i["rate"], i["price_list_rate"]) for i in q["items"]], [(1000.0, 1000.0), (2000.0, 2000.0)])
		self.assertEqual({i["item_code"] for i in q["items"]}, {"EST-SCOPE"})
		self.assertEqual({i["uom"] for i in q["items"]}, {"Unit"})
		self.assertIn("Pumps &amp; filters.", q["items"][1]["description"])
		self.assertIn("Includes an allowance: Basin rocks", q["items"][1]["description"])
		self.assertIn("Tile", q["terms"])
		# set_missing_values copied the customer's template in; a build price already carries its use tax
		self.assertEqual(q["taxes"], [])
		self.assertIsNone(q["taxes_and_charges"])
		self.assertEqual(q["ignore_pricing_rule"], 1)

	def test_the_deal_goes_on_the_custom_field_never_opportunity(self):
		make_estimate()
		name, q = issued()
		self.assertEqual(q["custom_source_opportunity"], "CRM-OPP-0001")
		self.assertNotIn("opportunity", q)
		self.assertEqual(q["custom_estimate"], "EST-26-0001")
		self.assertEqual(STORE["Estimate"]["EST-26-0001"], {"status": "Issued", "quotation": name})

	def test_no_account_quotes_the_opportunitys_party(self):
		make_estimate(customer=None)
		STORE["Opportunity"] = {"CRM-OPP-0001": {"opportunity_from": "Lead", "party_name": "LEAD-0001"}}
		name, q = issued()
		self.assertEqual((q["quotation_to"], q["party_name"]), ("Lead", "LEAD-0001"))

	def test_a_product_job_takes_the_company_default_taxes(self):
		make_estimate(job_kind="Product")
		name, q = issued()
		self.assertEqual(q["taxes_and_charges"], "Company Default Tax")
		self.assertTrue(q["taxes"])

	def test_est_scope_gets_a_zero_price_before_the_insert(self):
		make_estimate()
		issued()
		prices = list(STORE["Item Price"].values())
		self.assertEqual(len(prices), 1)
		self.assertEqual((prices[0]["price_list"], prices[0]["price_list_rate"], prices[0]["uom"]), ("Standard Selling", 0, "Unit"))

	def test_a_price_list_without_a_price_gets_zero_not_the_first_phase_price(self):
		# Auto-insert is on (prod): were the rate on the row during set_missing_values, ERPNext would
		# make "EST-SCOPE / <list>" at 1000 before the zero price could go in.
		make_estimate()
		STORE["Singles"][("Selling Settings", "selling_price_list")] = None
		quotation = FakeDoc("Quotation", selling_price_list="Example Customer List")
		real_new_doc = sys.modules["frappe"].new_doc
		sys.modules["frappe"].new_doc = lambda dt, **f: quotation if dt == "Quotation" else real_new_doc(dt, **f)
		try:
			issued()
		finally:
			sys.modules["frappe"].new_doc = real_new_doc
		prices = [(p["price_list"], p["price_list_rate"]) for p in STORE["Item Price"].values()]
		self.assertEqual(prices, [("Example Customer List", 0)])

	def test_a_draft_quotation_is_refreshed_and_a_submitted_one_left_alone(self):
		make_estimate()
		first, _q = issued()
		ESTIMATES["EST-26-0001"].quotation = first
		again, _q = issued()
		self.assertEqual(again, first)
		STORE["Quotation"][first]["docstatus"] = 1
		third, _q = issued()
		self.assertNotEqual(third, first)
		self.assertEqual(STORE["Quotation"][first]["docstatus"], 1)

	def test_a_valid_until_already_past_is_dropped(self):
		make_estimate(valid_till="2026-11-01")
		name, q = issued()
		self.assertIsNone(q["valid_till"])
		make_estimate(valid_till="2026-12-31")
		name, q = issued()
		self.assertEqual(q["valid_till"], "2026-12-31")

	def test_refusals(self):
		est = make_estimate()
		est.lines[1]["amount"] = 0
		with self.assertRaises(Thrown) as cm:
			api.issue_quotation("EST-26-0001")
		self.assertIn("2 Pump", str(cm.exception))
		make_estimate(status="Superseded")
		with self.assertRaises(Thrown):
			api.issue_quotation("EST-26-0001")
		make_estimate()
		PERMS["Quotation"] = False
		with self.assertRaises(PermissionError_):
			api.issue_quotation("EST-26-0001")
		PERMS["Quotation"] = True
		STORE["Item"].clear()
		with self.assertRaises(Thrown):
			api.issue_quotation("EST-26-0001")
		self.assertEqual(STORE["Quotation"], {})


class TestSeeds(unittest.TestCase):
	def setUp(self):
		reset()

	def test_run_never_raises_and_logs_a_failure(self):
		def boom():
			raise ValueError("no")

		self.assertIsNone(seed.run("p", "step", boom, "do it by hand"))
		self.assertEqual(LOG["errors"], ["Estimating seed: step"])
		self.assertEqual(LOG["rollbacks"], 1)
		self.assertEqual(seed.run("p", "ok", lambda: "created", "x"), "created")

	def test_scope_item_uses_unit_and_a_schema_shaped_name(self):
		self.assertEqual(seed.seed_scope_item(), "created")
		item = STORE["Item"]["EST-SCOPE"]
		self.assertEqual((item["stock_uom"], item["item_group"], item["is_stock_item"]), ("Unit", "Service", 0))
		self.assertEqual(item["item_name"], "SERVICE, ESTIMATE SCOPE")
		self.assertEqual(seed.seed_scope_item(), "already there")

	def test_scope_item_name_has_only_the_category_finding_left(self):
		path = os.path.join(APP, "inventory_enhancements", "item_naming_rules.py")
		spec = importlib.util.spec_from_file_location("_naming_rules", path)
		naming = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(naming)
		result = naming.evaluate(
			{"item_code": "EST-SCOPE", "item_name": seed.SCOPE_ITEM_NAME, "item_group": "Service", "stock_uom": "Unit"},
			[],
		)
		self.assertEqual([f["code"] for f in result["findings"]], ["name_category_unapproved"])

	def test_zero_price_is_made_once(self):
		seed.seed_scope_item()
		self.assertEqual(seed.hold_zero_price("Standard Selling"), "created")
		self.assertEqual(seed.hold_zero_price("Standard Selling"), "already there")
		self.assertEqual(len(STORE["Item Price"]), 1)

	def test_patches_run_and_never_raise(self):
		names = ("seed_estimator_role", "seed_estimate_rate_card", "seed_estimate_templates", "seed_estimate_scope_item")
		mods = [importlib.reload(importlib.import_module(f"erpnext_enhancements.patches.{n}")) for n in names]
		for mod in mods:
			mod.execute()
		self.assertIn("Estimator", STORE["Role"])
		self.assertTrue(STORE["Estimate Rate Card"]["Standard"]["is_default"])
		self.assertEqual(set(STORE["Estimate Template"]), {"Design-Build", "Build", "Event", "Custom Panel"})
		self.assertIn("EST-SCOPE", STORE["Item"])
		self.assertEqual(len(STORE["Item Price"]), 1)
		self.assertEqual(LOG["errors"], [])

		reset()
		FAIL_INSERT.update({"Role", "Estimate Rate Card", "Estimate Template", "Item", "Item Price"})
		for mod in mods:
			mod.execute()  # must not raise: a raising patch aborts the deploy
		self.assertIn("Estimating seed: Estimator role", LOG["errors"])
		self.assertIn("Estimating seed: Item EST-SCOPE", LOG["errors"])
		self.assertEqual(sum(1 for e in LOG["errors"] if e.startswith("Estimating seed: template ")), 4)

	def test_one_bad_template_does_not_stop_the_others(self):
		mod = importlib.import_module("erpnext_enhancements.patches.seed_estimate_templates")
		real = mod._template

		def picky(spec):
			if spec["template_name"] == "Build":
				raise ValidationError_("bad row")
			return real(spec)

		mod._template = picky
		try:
			mod.execute()
		finally:
			mod._template = real
		self.assertEqual(set(STORE["Estimate Template"]), {"Design-Build", "Event", "Custom Panel"})
		self.assertEqual(LOG["errors"], ["Estimating seed: template Build"])


class TestDeleteLinks(unittest.TestCase):
	def setUp(self):
		reset()

	def test_deleting_the_quotation_unlinks_its_estimates(self):
		STORE["Estimate"] = {
			"EST-1": {"quotation": "QTN-1", "status": "Superseded"},
			"EST-2": {"quotation": "QTN-1", "status": "Issued", "previous_revision": "EST-1"},
			"EST-3": {"quotation": "QTN-2", "status": "Issued"},
		}
		links.release_quotation(FakeDoc("Quotation", name="QTN-1"))
		self.assertEqual(STORE["Estimate"]["EST-1"], {"quotation": None, "status": "Superseded"})
		self.assertEqual(STORE["Estimate"]["EST-2"]["quotation"], None)
		self.assertEqual(STORE["Estimate"]["EST-2"]["status"], "Draft")
		self.assertEqual(STORE["Estimate"]["EST-3"]["quotation"], "QTN-2")

	def test_deleting_the_estimate_unlinks_its_quotations_and_keeps_the_revision_chain(self):
		STORE["Quotation"] = {"QTN-1": {"custom_estimate": "EST-2", "docstatus": 1}, "QTN-2": {"custom_estimate": "EST-9"}}
		STORE["Estimate"] = {
			"EST-1": {"status": "Superseded", "quotation": "QTN-0"},
			"EST-2": {"status": "Superseded", "previous_revision": "EST-1"},
			"EST-3": {"status": "Draft", "previous_revision": "EST-2"},
		}
		links.release_estimate(FakeDoc("Estimate", name="EST-2", status="Superseded", previous_revision="EST-1"))
		self.assertIsNone(STORE["Quotation"]["QTN-1"]["custom_estimate"])
		self.assertEqual(STORE["Quotation"]["QTN-2"]["custom_estimate"], "EST-9")
		self.assertEqual(STORE["Estimate"]["EST-3"]["previous_revision"], "EST-1")  # the chain survives
		self.assertEqual(STORE["Estimate"]["EST-1"]["status"], "Superseded")  # not the newest: untouched
		# ...so deleting the newest next still reopens the first
		links.release_estimate(FakeDoc("Estimate", name="EST-3", status="Draft", previous_revision="EST-1"))
		self.assertEqual(STORE["Estimate"]["EST-1"]["status"], "Issued")

	def test_a_draft_quotation_repriced_by_the_deleted_revision_reopens_as_draft(self):
		# A new revision takes over the old one's draft quotation; issuing it put the new prices on it.
		STORE["Estimate"] = {"EST-1": {"status": "Superseded", "quotation": "QTN-1"}}
		links.release_estimate(
			FakeDoc("Estimate", name="EST-2", status="Issued", quotation="QTN-1", previous_revision="EST-1")
		)
		self.assertEqual(STORE["Estimate"]["EST-1"], {"status": "Draft", "quotation": "QTN-1"})
		# Never issued after the revision: the shared draft still holds EST-1's prices.
		STORE["Estimate"] = {"EST-1": {"status": "Superseded", "quotation": "QTN-1"}}
		links.release_estimate(
			FakeDoc("Estimate", name="EST-2", status="Draft", quotation="QTN-1", previous_revision="EST-1")
		)
		self.assertEqual(STORE["Estimate"]["EST-1"]["status"], "Issued")

	def test_deleting_the_newest_revision_reopens_the_one_before(self):
		STORE["Estimate"] = {
			"EST-1": {"status": "Superseded", "quotation": "QTN-0"},
			"EST-0": {"status": "Superseded", "quotation": None},
		}
		links.release_estimate(FakeDoc("Estimate", name="EST-2", status="Draft", previous_revision="EST-1"))
		self.assertEqual(STORE["Estimate"]["EST-1"]["status"], "Issued")
		links.release_estimate(FakeDoc("Estimate", name="EST-1", status="Issued", previous_revision="EST-0"))
		self.assertEqual(STORE["Estimate"]["EST-0"]["status"], "Draft")

	def test_the_hooks_are_wired(self):
		hooks = open(os.path.join(APP, "hooks.py"), encoding="utf-8").read()
		self.assertIn('"on_trash": "erpnext_enhancements.estimating.links.release_quotation"', hooks)
		controller = open(os.path.join(APP, "estimating", "doctype", "estimate", "estimate.py"), encoding="utf-8").read()
		self.assertIn("links.release_estimate(self)", controller)


if __name__ == "__main__":
	unittest.main()

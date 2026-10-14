"""Bench-free tests for the guided Item flow's server side (form kit P8.1, TASK-2026-02790).

Two modules:

* ``inventory_enhancements/item_flow_rules.py``, pure: the PDT/SRV blocks (from the allocated
  blocks, never ``MAX() + 1``, with both of ``item_naming_rules``' occupancy traps), the catalog's
  segments for the name builder's pick lists, and each block's refusals.
* ``api/item_flow.py``, the endpoints, against a ``frappe`` stub installed in ``setUpModule``
  with an in-memory store of Items, Item Prices and Assets: every endpoint checks its own
  permission before anything is written, a resend makes nothing twice (rows and prices keyed on
  what they are about, the Asset on the page's id in a unique column, a racing resend settled by
  the unique index), and a refusal comes before any write.

Run: python -m unittest erpnext_enhancements.tests.test_item_flow -v
"""

import ast
import copy
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import item_flow_rules as rules

APP = REPO_ROOT / "erpnext_enhancements"
API_FILE = APP / "api" / "item_flow.py"

api = None
FRAPPE = None
STATE = None


# --------------------------------------------------------------------------- the pure rules


class TestCodeBlocks(unittest.TestCase):
	def test_blocks_in_use_offer_their_lowest_free_number(self):
		out = rules.code_blocks(["PDT-0001", "PDT-0002 WIDGET", "PDT-0101", "PDT-0100"], "PDT")
		self.assertEqual(
			[b["label"] for b in out["blocks"]], ["PDT-0000 to PDT-0099", "PDT-0100 to PDT-0199"]
		)
		self.assertEqual(out["blocks"][0]["next_code"], "PDT-0003", "0 is never offered, then the first gap")
		self.assertEqual(out["blocks"][1]["next_code"], "PDT-0102")
		self.assertEqual(out["new_block"]["next_code"], "PDT-0200")
		self.assertEqual((out["width"], out["block_size"]), (4, 100))

	def test_the_lowest_gap_not_max_plus_one(self):
		out = rules.code_blocks(["PDT-0400", "PDT-0401", "PDT-0403", "PDT-0499"], "PDT")
		self.assertEqual(out["blocks"][0]["next_code"], "PDT-0402")
		self.assertEqual(
			out["new_block"]["next_code"], "PDT-0001", "the lowest unused block, not the next one up"
		)

	def test_a_taken_number_with_trailing_text_is_taken(self):
		"""Trap 1 of item_naming_rules: the only record of the 5HP VFD bypass."""
		out = rules.code_blocks(["PDT-0001", "PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy"], "PDT")
		self.assertEqual(out["blocks"][0]["used"], 2)
		free = out["blocks"][0]["next_free"]
		self.assertNotEqual(free, 8)

	def test_the_five_digit_family_does_not_collapse_onto_four_digit_slots(self):
		"""Trap 2: PDT-00051 is a different code from PDT-0051."""
		out = rules.code_blocks(["PDT-00051", "PDT-00000 (deleted)", "PDT-0050"], "PDT")
		self.assertEqual(out["blocks"][0]["used"], 1)
		self.assertEqual(out["blocks"][0]["next_code"], "PDT-0001")

	def test_a_tombstone_still_holds_its_number(self):
		out = rules.code_blocks(["SRV-001 (deleted)", "SRV-002"], "SRV")
		self.assertEqual(out["blocks"][0]["next_code"], "SRV-003")

	def test_a_full_block_offers_nothing(self):
		codes = [f"SRV-{n:03d}" for n in range(1, 100)]
		out = rules.code_blocks(codes, "SRV")
		self.assertIsNone(out["blocks"][0]["next_code"])
		self.assertEqual(out["new_block"]["label"], "SRV-100 to SRV-199")

	def test_only_the_numbered_families(self):
		with self.assertRaises(ValueError):
			rules.code_blocks([], "CON")


class TestCatalog(unittest.TestCase):
	def test_live_names_become_their_segments(self):
		rows = [
			{"item_code": "A", "item_name": "Elbow, 90,  soc, PVC"},
			{"item_code": "B (deleted)", "item_name": "ELBOW, 45"},
			{"item_code": "C", "item_name": "NO COMMA"},
			{"item_code": "D", "item_name": "A, B, C, D, E, F, G, H"},
			{"item_code": "E", "item_name": ", LEADING EMPTY"},
		]
		self.assertEqual(rules.catalog_segments(rows), [["ELBOW", "90", "SOC", "PVC"]])

	def test_the_categories_are_appendix_a(self):
		categories = rules.approved_categories()
		self.assertIn("ELBOW", categories)
		self.assertEqual(categories, sorted(categories))


class TestBlockRules(unittest.TestCase):
	def test_purchase_links(self):
		self.assertIsNone(rules.purchase_url_problem(""))
		self.assertIsNone(rules.purchase_url_problem("https://lg.example/p/505025"))
		self.assertIn("https://", rules.purchase_url_problem("lg.example/p"))
		self.assertIn("https://", rules.purchase_url_problem("javascript:alert(1)"))
		self.assertIn("140", rules.purchase_url_problem("https://x.example/" + "a" * 140))

	def test_prices(self):
		self.assertIsNone(rules.price_problem(""))
		self.assertIsNone(rules.price_problem("0"))
		self.assertIsNone(rules.price_problem(41.5))
		self.assertTrue(rules.price_problem("-1"))
		self.assertTrue(rules.price_problem("abc"))

	def test_reorder_rules_need_a_level_and_a_quantity(self):
		self.assertIsNone(rules.reorder_problem(None, None))
		self.assertIsNone(rules.reorder_problem("4", "12"))
		self.assertIsNone(rules.reorder_problem(0, 5))
		self.assertIn("how many to buy", rules.reorder_problem(4, None))
		self.assertIn("how many to buy", rules.reorder_problem(4, 0))
		self.assertIn("reorder level too", rules.reorder_problem(None, 5))
		self.assertTrue(rules.reorder_problem(-1, 5))
		self.assertTrue(rules.reorder_problem("x", 5))

	def test_an_asset_needs_a_place_and_a_cost(self):
		self.assertIsNone(rules.asset_problem("Upstairs Rental Warehouse", "1200"))
		self.assertIn("kept", rules.asset_problem(" ", "1200"))
		self.assertIn("cost", rules.asset_problem("X", "0"))
		self.assertIn("cost", rules.asset_problem("X", None))

	def test_nan_and_infinity_are_not_numbers(self):
		# float() accepts all of these, and NaN passes every `< 0` check: each must be refused.
		for bad in ("nan", "NaN", "inf", "-inf", "Infinity", float("nan"), float("inf")):
			with self.subTest(value=bad):
				self.assertTrue(rules.price_problem(bad))
				self.assertEqual(
					rules.reorder_problem(bad, 5), "The reorder level and quantity must be numbers."
				)
				self.assertEqual(
					rules.reorder_problem(4, bad), "The reorder level and quantity must be numbers."
				)
				self.assertIn("cost", rules.asset_problem("X", bad))

	def test_client_ids(self):
		self.assertEqual(rules.client_id(" gi-abc-123456 "), "gi-abc-123456")
		for bad in ("", "short", "has space 123", "x" * 65, None, 12345678, "semi;colon12"):
			with self.subTest(value=bad):
				self.assertEqual(rules.client_id(bad), "")


# --------------------------------------------------------------------------- the stub


class Row(dict):
	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value


TABLES = {"Item": ("supplier_items", "item_defaults", "reorder_levels")}


class Doc:
	def __init__(self, doctype, data):
		object.__setattr__(self, "doctype", doctype)
		object.__setattr__(self, "_d", {})
		for key, value in data.items():
			self.set(key, value)

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self._d.get(key)

	def __setattr__(self, key, value):
		self._d[key] = value

	def get(self, key, default=None):
		return self._d.get(key, default)

	def set(self, key, value):
		if key in TABLES.get(self.doctype, ()):
			self._d[key] = [v if isinstance(v, Row) else Row(v) for v in value or []]
		else:
			self._d[key] = value

	def append(self, table, data):
		row = Row(data)
		self._d.setdefault(table, []).append(row)
		return row

	def check_permission(self, ptype="read"):
		if not FRAPPE.has_permission(self.doctype, ptype, doc=self):
			raise FRAPPE.PermissionError(f"No permission to {ptype} {self.doctype}")

	def _store(self):
		STATE["clock"] += 1
		self._d["modified"] = f"2026-10-14 09:00:{STATE['clock']:02d}"
		STATE["db"][self.doctype][self._d["name"]] = copy.deepcopy(self._d)

	def save(self):
		self.check_permission("write")
		if self.doctype == "Item":
			for row in self._d.get("reorder_levels") or []:
				if row.get("warehouse_reorder_level") and not row.get("warehouse_reorder_qty"):
					raise FRAPPE.ValidationError("Row #1: Please set reorder quantity")
		STATE["saves"].append((self.doctype, self._d["name"]))
		self._store()

	def insert(self):
		if not FRAPPE.has_permission(self.doctype, "create"):
			raise FRAPPE.PermissionError(f"No permission to create {self.doctype}")
		if STATE["race"] and self.doctype == "Asset":
			# Another request with the same id committed first: the unique index refuses this one.
			winner = dict(self._d, name="ACC-ASS-2026-00099", owner=FRAPPE.session.user)
			STATE["after_rollback"].append(("Asset", winner))
			raise FRAPPE.UniqueValidationError("Duplicate entry")
		STATE["seq"] += 1
		prefix = {"Asset": "ACC-ASS-2026-", "Item Price": "IP-"}[self.doctype]
		self._d["name"] = f"{prefix}{STATE['seq']:05d}"
		self._d.setdefault("owner", FRAPPE.session.user)
		STATE["inserts"].append((self.doctype, self._d["name"]))
		self._store()
		return self


class Meta:
	def __init__(self, fields):
		self.fields = fields

	def has_field(self, fieldname):
		return fieldname in self.fields


def _matches(record, filters):
	for key, rule in (filters or {}).items():
		value = record.get(key)
		if isinstance(rule, list) and rule[0] == "in":
			if value not in rule[1]:
				return False
		elif value != rule:
			return False
	return True


def _install_frappe_stub():
	global FRAPPE, STATE
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	class PermissionError(Exception):
		pass

	class DoesNotExistError(ValidationError):
		pass

	class UniqueValidationError(ValidationError):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe.DoesNotExistError = DoesNotExistError
	frappe.UniqueValidationError = UniqueValidationError
	frappe._ = lambda s, *a, **k: s
	frappe.bold = lambda s: f"<b>{s}</b>"

	# As frappe's: the message is queued for the browser before the raise, unless messages are
	# muted, so a throw that is caught still shows a dialog (v16 utils/messages.py).
	def throw(msg="", exc=ValidationError, title=None):
		if not frappe.flags.mute_messages:
			frappe.message_log.append(msg)
		raise exc(msg)

	frappe.throw = throw
	frappe.flags = types.SimpleNamespace(mute_messages=False)
	frappe.message_log = []

	def whitelist(*dargs, **dkwargs):
		def wrap(fn):
			fn._whitelist = dkwargs
			return fn

		return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

	frappe.whitelist = whitelist
	frappe.session = types.SimpleNamespace(user="parker@example.com")
	STATE = {}
	frappe.get_roles = lambda user=None: list(STATE["roles"])

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		allowed = STATE["perm"].get((doctype, ptype), True)
		STATE["perm_checks"].append((doctype, ptype))
		if throw and not allowed:
			raise PermissionError(f"No permission for {doctype}")
		return allowed

	frappe.has_permission = has_permission
	frappe.get_meta = lambda doctype: Meta(STATE["meta"].get(doctype, set()))

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			data = dict(doctype)
			return Doc(data.pop("doctype"), data)
		if name not in STATE["db"][doctype]:
			raise DoesNotExistError(f"{doctype} {name}")
		return Doc(doctype, copy.deepcopy(STATE["db"][doctype][name]))

	frappe.get_doc = get_doc
	frappe.new_doc = lambda doctype: Doc(
		doctype, {"asset_type": "Existing Asset"} if doctype == "Asset" else {}
	)

	def get_list(doctype, filters=None, fields=None, order_by=None, **kwargs):
		STATE["get_list"].append(doctype)
		rows = [r for r in STATE["db"].get(doctype, {}).values() if _matches(r, filters)]
		if order_by == "modified desc":
			rows.sort(key=lambda r: r.get("modified") or "", reverse=True)
		return [types.SimpleNamespace(**{f: r.get(f) for f in fields}) for r in rows]

	frappe.get_list = get_list

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		rows = [r for r in STATE["db"].get(doctype, {}).values() if _matches(r, filters)]
		if pluck:
			return [r.get(pluck) for r in rows]
		return [types.SimpleNamespace(**{f: r.get(f) for f in fields}) for r in rows]

	frappe.get_all = get_all

	def exists(doctype, name=None):
		if isinstance(name, dict):
			return any(_matches(r, name) for r in STATE["db"].get(doctype, {}).values())
		if doctype == "DocType":
			return name in STATE["db"]
		return name in STATE["db"].get(doctype, {})

	def get_value(doctype, filters, fieldname="name", as_dict=False, order_by=None):
		if isinstance(filters, str):
			record = STATE["db"].get(doctype, {}).get(filters)
		else:
			found = [r for r in STATE["db"].get(doctype, {}).values() if _matches(r, filters)]
			record = found[0] if found else None
		if record is None:
			return None
		if isinstance(fieldname, list):
			return (
				frappe._dict({f: record.get(f) for f in fieldname})
				if as_dict
				else [record.get(f) for f in fieldname]
			)
		return record.get(fieldname)

	def rollback(save_point=None):
		if save_point:
			STATE["savepoint_rollbacks"].append(save_point)
			return
		STATE["rollbacks"] += 1
		for doctype, record in STATE["after_rollback"]:
			STATE["db"][doctype][record["name"]] = record
		STATE["after_rollback"].clear()

	frappe.db = types.SimpleNamespace(
		exists=exists,
		get_value=get_value,
		get_single_value=lambda doctype, field: STATE["singles"].get((doctype, field)),
		rollback=rollback,
		savepoint=lambda name: STATE["savepoints"].append(name),
		count=lambda doctype: len(STATE["db"].get(doctype, {})),
	)

	class _dict(dict):
		def __getattr__(self, key):
			if key.startswith("__"):
				raise AttributeError(key)
			return self.get(key)

	frappe._dict = _dict
	frappe.defaults = types.SimpleNamespace(
		get_user_default=lambda key: None, get_global_default=lambda key: "Sapphire Fountains"
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(float(value or 0)) if str(value or "").strip() else 0

	def flt(value, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.flt = flt
	utils.cstr = lambda value: "" if value is None else str(value)
	utils.getdate = lambda value=None: str(value or "2026-10-14")[:10]
	utils.nowdate = lambda: "2026-10-14"
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", text or "")
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	FRAPPE = frappe


def setUpModule():
	global api
	_install_frappe_stub()
	stock_scan = types.ModuleType("erpnext_enhancements.api.stock_scan")
	stock_scan.preview_new_item = lambda *args: {"checked": True, "asked": list(args)}
	sys.modules["erpnext_enhancements.api.stock_scan"] = stock_scan
	bins = types.ModuleType("erpnext_enhancements.inventory_enhancements.bin_assignments")
	bins.ensure_assignment = _ensure_assignment
	bins.release_if_empty = _release_if_empty
	sys.modules["erpnext_enhancements.inventory_enhancements.bin_assignments"] = bins
	for mod in (
		"erpnext_enhancements.api.item_flow",
		"erpnext_enhancements.inventory_enhancements.item_naming",
	):
		sys.modules.pop(mod, None)
	from erpnext_enhancements.api import item_flow

	api = item_flow


def tearDownModule():
	for mod in (
		"frappe",
		"frappe.utils",
		"erpnext_enhancements.api.stock_scan",
		"erpnext_enhancements.inventory_enhancements.bin_assignments",
		"erpnext_enhancements.api.item_flow",
		"erpnext_enhancements.inventory_enhancements.item_naming",
	):
		sys.modules.pop(mod, None)


# The bin module's helper (v1.689.0): records the assignment, or refuses as it would, in words
# through frappe.throw (`assign_refusal`), or fails some other way (`assign_error`).
def _ensure_assignment(item_code, warehouse):
	if STATE["assign_error"]:
		raise STATE["assign_error"]
	if STATE["assign_refusal"]:
		exc, message = STATE["assign_refusal"]
		FRAPPE.throw(message, exc)
	STATE["assigned"].append((item_code, warehouse))
	return f"PUTAWAY-{len(STATE['assigned'])}"


# Its other half: takes the item off the bin it left, answering what is still on hand there
# (`left`: 0.0 taken off, more kept, None nothing claimed it), or refuses (`release_refusal`).
def _release_if_empty(item_code, warehouse):
	if STATE["release_refusal"]:
		exc, message = STATE["release_refusal"]
		FRAPPE.throw(message, exc)
	STATE["released"].append((item_code, warehouse))
	return STATE["left"]


def _reset(perm=None):
	STATE.clear()
	STATE.update(
		{
			"perm": dict(perm or {}),
			"perm_checks": [],
			"roles": ["Item Manager", "Stock User", "Purchase Master Manager"],
			"meta": {
				"Item Supplier": {"purchase_url"},
				"Asset": {"custom_guided_client_id", "custom_rentable"},
			},
			"singles": {
				("Buying Settings", "buying_price_list"): "Standard Buying",
				("Stock Settings", "auto_indent"): 1,
			},
			"db": {
				"Item": {},
				"Item Price": {},
				"Asset": {},
				"Supplier": {
					"Little Giant": {"name": "Little Giant", "supplier_name": "Little Giant Pumps"},
					"Aquascape": {"name": "Aquascape", "supplier_name": "Aquascape Inc"},
				},
				"Warehouse": {
					"Bin A1-3-1 - SF": {
						"name": "Bin A1-3-1 - SF",
						"company": "Sapphire Fountains",
						"is_group": 0,
						"disabled": 0,
						"warehouse_type": "",
					},
					"Inventory - SF": {
						"name": "Inventory - SF",
						"company": "Sapphire Fountains",
						"is_group": 1,
						"disabled": 0,
						"warehouse_type": "",
					},
					"Truck - SF": {
						"name": "Truck - SF",
						"company": "Sapphire Fountains",
						"is_group": 0,
						"disabled": 0,
						"warehouse_type": "Transit",
					},
				},
				"Location": {"Upstairs Rental Warehouse": {"name": "Upstairs Rental Warehouse"}},
				"Price List": {"Standard Buying": {"name": "Standard Buying", "currency": "USD"}},
				"Company": {"Sapphire Fountains": {"name": "Sapphire Fountains"}},
			},
			"saves": [],
			"inserts": [],
			"get_list": [],
			"clock": 0,
			"seq": 0,
			"race": False,
			"after_rollback": [],
			"rollbacks": 0,
			"savepoints": [],
			"savepoint_rollbacks": [],
			"assigned": [],
			"assign_refusal": None,
			"assign_error": None,
			"released": [],
			"left": 0.0,
			"release_refusal": None,
		}
	)
	FRAPPE.session.user = "parker@example.com"
	FRAPPE.flags.mute_messages = False
	FRAPPE.message_log.clear()
	_item("LG-505025", is_stock_item=1)
	_item("RENTAL-FOUNTAIN-01", is_stock_item=0, is_fixed_asset=1, asset_category="Rental Fountain Fleet")


def _item(code, **values):
	data = {
		"name": code,
		"item_code": code,
		"item_name": f"PUMP, {code}",
		"stock_uom": "Unit",
		"is_stock_item": 0,
		"is_fixed_asset": 0,
		"supplier_items": [],
		"item_defaults": [],
		"reorder_levels": [],
		"modified": "2026-10-14 08:00:00",
	}
	data.update(values)
	STATE["db"]["Item"][code] = data


def _stored(code):
	return STATE["db"]["Item"][code]


# --------------------------------------------------------------------------- the endpoints


class TestSupplier(unittest.TestCase):
	def setUp(self):
		_reset()

	def _save(self, **extra):
		args = {
			"item_code": "LG-505025",
			"supplier": "Little Giant",
			"supplier_part_no": "505025",
			"purchase_url": "https://lg.example/p/505025",
			"rate": "41.50",
		}
		args.update(extra)
		return api.save_supplier(**args)

	def test_records_the_row_the_default_and_the_price(self):
		out = self._save()
		item = _stored("LG-505025")
		self.assertEqual(
			[(r["supplier"], r["supplier_part_no"], r["purchase_url"]) for r in item["supplier_items"]],
			[("Little Giant", "505025", "https://lg.example/p/505025")],
		)
		self.assertEqual(
			item["item_defaults"], [{"company": "Sapphire Fountains", "default_supplier": "Little Giant"}]
		)
		(price,) = STATE["db"]["Item Price"].values()
		self.assertEqual(
			(
				price["item_code"],
				price["price_list"],
				price["supplier"],
				price["uom"],
				price["price_list_rate"],
			),
			("LG-505025", "Standard Buying", "Little Giant", "Unit", 41.5),
		)
		self.assertEqual(out["default_supplier"], "Little Giant")
		self.assertEqual(out["suppliers"][0]["rate"], 41.5)
		self.assertEqual(out["suppliers"][0]["supplier_name"], "Little Giant Pumps")

	def test_a_resend_makes_nothing_twice_and_saves_nothing_again(self):
		self._save()
		saves, inserts = len(STATE["saves"]), len(STATE["inserts"])
		self._save()
		self.assertEqual(len(_stored("LG-505025")["supplier_items"]), 1)
		self.assertEqual(len(STATE["db"]["Item Price"]), 1)
		self.assertEqual((len(STATE["saves"]), len(STATE["inserts"])), (saves, inserts))

	def test_a_new_price_updates_the_suppliers_price(self):
		self._save()
		self._save(rate="45")
		(price,) = STATE["db"]["Item Price"].values()
		self.assertEqual(price["price_list_rate"], 45.0)

	def test_a_second_supplier_is_a_second_row(self):
		self._save()
		self._save(supplier="Aquascape", supplier_part_no="AQ-1", rate="", make_default=0)
		item = _stored("LG-505025")
		self.assertEqual([r["supplier"] for r in item["supplier_items"]], ["Little Giant", "Aquascape"])
		self.assertEqual(item["item_defaults"][0]["default_supplier"], "Little Giant")
		self.assertEqual(len(STATE["db"]["Item Price"]), 1, "no rate, no price")

	def test_without_item_write_nothing_is_written(self):
		_reset({("Item", "write"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			self._save()
		self.assertEqual((STATE["saves"], STATE["inserts"]), ([], []))

	def test_without_item_price_create_the_refusal_comes_before_any_write(self):
		_reset({("Item Price", "create"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			self._save()
		self.assertEqual((STATE["saves"], STATE["inserts"]), ([], []))
		self._save(rate="")
		self.assertEqual(len(_stored("LG-505025")["supplier_items"]), 1, "without a price it is allowed")

	def test_bad_values_are_refused_in_words_before_a_write(self):
		for extra, words in (
			({"supplier": "Nobody"}, "from the list"),
			({"purchase_url": "lg.example"}, "https://"),
			({"rate": "-3"}, "0 or more"),
		):
			with self.subTest(extra=extra):
				with self.assertRaises(FRAPPE.ValidationError) as caught:
					self._save(**extra)
				self.assertIn(words, str(caught.exception))
		self.assertEqual((STATE["saves"], STATE["inserts"]), ([], []))

	def test_a_missing_item_is_named(self):
		with self.assertRaises(FRAPPE.DoesNotExistError):
			self._save(item_code="NOPE")


class TestStock(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_records_the_bin_and_its_purchase_reorder_rule(self):
		out = api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		item = _stored("LG-505025")
		self.assertEqual(
			item["item_defaults"], [{"company": "Sapphire Fountains", "default_warehouse": "Bin A1-3-1 - SF"}]
		)
		self.assertEqual(
			item["reorder_levels"],
			[
				{
					"warehouse": "Bin A1-3-1 - SF",
					"warehouse_group": "Bin A1-3-1 - SF",
					"material_request_type": "Purchase",
					"warehouse_reorder_level": 4.0,
					"warehouse_reorder_qty": 12.0,
				}
			],
		)
		self.assertEqual(out["default_warehouse"], "Bin A1-3-1 - SF")
		self.assertEqual(out["reorder"][0]["level"], 4.0)

	def test_a_resend_changes_nothing(self):
		api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		saves = len(STATE["saves"])
		api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		self.assertEqual(len(_stored("LG-505025")["reorder_levels"]), 1)
		self.assertEqual(len(STATE["saves"]), saves)

	def test_a_transfer_rule_for_the_same_bin_is_left_alone(self):
		_item(
			"LG-505025",
			is_stock_item=1,
			reorder_levels=[
				{
					"warehouse": "Bin A1-3-1 - SF",
					"material_request_type": "Transfer",
					"warehouse_reorder_level": 1,
					"warehouse_reorder_qty": 2,
				}
			],
		)
		api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		self.assertEqual(
			[r["material_request_type"] for r in _stored("LG-505025")["reorder_levels"]],
			["Transfer", "Purchase"],
		)

	def test_only_a_bin_that_holds_stock_and_only_a_stock_item(self):
		for warehouse in ("Inventory - SF", "Truck - SF", "Nowhere"):
			with self.subTest(warehouse=warehouse):
				with self.assertRaises(FRAPPE.ValidationError):
					api.save_stock("LG-505025", warehouse)
		with self.assertRaises(FRAPPE.ValidationError):
			api.save_stock("RENTAL-FOUNTAIN-01", "Bin A1-3-1 - SF")
		self.assertEqual(STATE["saves"], [])

	def test_a_level_without_a_quantity_is_refused_before_a_write(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4")
		self.assertIn("how many to buy", str(caught.exception))
		self.assertEqual(STATE["saves"], [])

	def test_moving_bins_moves_the_reorder_rule(self):
		"""Review of #1270: the old bin's Purchase rule would otherwise keep asking for stock in a
		bin the item no longer lives in."""
		STATE["db"]["Warehouse"]["Bin B2-1-1 - SF"] = dict(
			STATE["db"]["Warehouse"]["Bin A1-3-1 - SF"], name="Bin B2-1-1 - SF"
		)
		api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		out = api.save_stock("LG-505025", "Bin B2-1-1 - SF")
		rules_now = _stored("LG-505025")["reorder_levels"]
		self.assertEqual(
			[(r["warehouse"], r["warehouse_group"], r["warehouse_reorder_level"]) for r in rules_now],
			[("Bin B2-1-1 - SF", "Bin B2-1-1 - SF", 4.0)],
		)
		self.assertEqual(_stored("LG-505025")["item_defaults"][0]["default_warehouse"], "Bin B2-1-1 - SF")
		self.assertIn("moved from Bin A1-3-1 - SF to Bin B2-1-1 - SF", out["note"])

	def test_a_new_bin_with_its_own_rule_leaves_the_old_one_and_says_so(self):
		STATE["db"]["Warehouse"]["Bin B2-1-1 - SF"] = dict(
			STATE["db"]["Warehouse"]["Bin A1-3-1 - SF"], name="Bin B2-1-1 - SF"
		)
		_item(
			"LG-505025",
			is_stock_item=1,
			item_defaults=[{"company": "Sapphire Fountains", "default_warehouse": "Bin A1-3-1 - SF"}],
			reorder_levels=[
				{
					"warehouse": "Bin A1-3-1 - SF",
					"material_request_type": "Purchase",
					"warehouse_reorder_level": 4,
					"warehouse_reorder_qty": 12,
				},
				{
					"warehouse": "Bin B2-1-1 - SF",
					"material_request_type": "Purchase",
					"warehouse_reorder_level": 2,
					"warehouse_reorder_qty": 6,
				},
			],
		)
		out = api.save_stock("LG-505025", "Bin B2-1-1 - SF")
		self.assertEqual(len(_stored("LG-505025")["reorder_levels"]), 2)
		self.assertIn("Bin A1-3-1 - SF still has its own reorder rule", out["note"])
		self.assertIsNone(
			api.save_stock("LG-505025", "Bin B2-1-1 - SF")["note"], "nothing moved, nothing said"
		)

	def test_the_bin_alone_is_fine(self):
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(_stored("LG-505025")["reorder_levels"], [])
		self.assertEqual(_stored("LG-505025")["item_defaults"][0]["default_warehouse"], "Bin A1-3-1 - SF")

	def test_without_item_write_nothing_is_written(self):
		_reset({("Item", "write"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(STATE["assigned"], [])


def _bin_b():
	STATE["db"]["Warehouse"]["Bin B2-1-1 - SF"] = dict(
		STATE["db"]["Warehouse"]["Bin A1-3-1 - SF"], name="Bin B2-1-1 - SF"
	)


class TestTheBinAssignment(unittest.TestCase):
	"""v1.689.1: the bin the flow saves is also where the item is assigned, an enabled Putaway Rule
	through ``bin_assignments.ensure_assignment`` (bins are Putaway Rules: Nik, 2026-10-14)."""

	def setUp(self):
		_reset()

	def test_the_item_is_put_on_the_bin_it_saved(self):
		out = api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		self.assertEqual(STATE["assigned"], [("LG-505025", "Bin A1-3-1 - SF")])
		self.assertIsNone(out["note"])
		self.assertEqual(STATE["savepoint_rollbacks"], [])

	def test_a_resend_asks_again_and_the_helper_keeps_it_to_one(self):
		# The helper is idempotent (an existing enabled rule comes back as it is), so asking on every
		# save is what keeps an item whose rule went missing on its bin.
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(STATE["assigned"], [("LG-505025", "Bin A1-3-1 - SF")] * 2)

	def test_a_refusal_keeps_the_bin_and_says_why_without_a_dialog(self):
		STATE["assign_refusal"] = (
			FRAPPE.ValidationError,
			"<b>PUMP</b> is a template: put its variants on bins.",
		)
		out = api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(_stored("LG-505025")["item_defaults"][0]["default_warehouse"], "Bin A1-3-1 - SF")
		self.assertEqual(
			out["note"],
			"Stock Scan's bin list was not changed: PUMP is a template: put its variants on bins.",
		)
		self.assertEqual(STATE["savepoint_rollbacks"], [api.ASSIGN_SAVEPOINT])
		self.assertEqual(STATE["savepoints"], [api.ASSIGN_SAVEPOINT])
		self.assertEqual(FRAPPE.message_log, [], "a caught throw must not reach the browser")
		self.assertFalse(FRAPPE.flags.mute_messages, "muting is put back")
		self.assertEqual(STATE["rollbacks"], 0, "the bin the person saved stays saved")

	def test_a_caller_the_bin_module_turns_away_is_told_so(self):
		STATE["assign_refusal"] = (FRAPPE.PermissionError, "Changing bins needs a stock role.")
		out = api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(
			out["note"], "Stock Scan's bin list was not changed: Changing bins needs a stock role."
		)
		self.assertEqual(FRAPPE.message_log, [])

	def test_both_notes_show_when_the_reorder_rule_moved_and_the_bin_refused(self):
		STATE["db"]["Warehouse"]["Bin B2-1-1 - SF"] = dict(
			STATE["db"]["Warehouse"]["Bin A1-3-1 - SF"], name="Bin B2-1-1 - SF"
		)
		api.save_stock("LG-505025", "Bin A1-3-1 - SF", reorder_level="4", reorder_qty="12")
		STATE["assign_refusal"] = (FRAPPE.ValidationError, "Changing bins needs a stock role.")
		note = api.save_stock("LG-505025", "Bin B2-1-1 - SF")["note"]
		self.assertTrue(note.startswith("The reorder rule moved from Bin A1-3-1 - SF to Bin B2-1-1 - SF."))
		self.assertTrue(
			note.endswith("Stock Scan's bin list was not changed: Changing bins needs a stock role.")
		)

	def test_any_other_failure_is_not_swallowed(self):
		STATE["assign_error"] = RuntimeError("Lost connection to the database")
		with self.assertRaises(RuntimeError):
			api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(STATE["savepoint_rollbacks"], [])
		self.assertFalse(FRAPPE.flags.mute_messages, "muting is put back on the way out too")

	def test_moving_bins_takes_the_item_off_the_old_one_when_it_is_empty(self):
		"""Review of #1311: as a Move that empties its source does. Left assigned, the old bin kept
		an enabled rule beside the new one, and *Apply Putaway Rule* could fill either."""
		_bin_b()
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(STATE["released"], [], "a first bin leaves nothing behind")
		out = api.save_stock("LG-505025", "Bin B2-1-1 - SF")
		self.assertEqual(STATE["assigned"][-1], ("LG-505025", "Bin B2-1-1 - SF"))
		self.assertEqual(STATE["released"], [("LG-505025", "Bin A1-3-1 - SF")])
		self.assertIsNone(out["note"])

	def test_stock_left_on_the_old_bin_keeps_it_and_says_so(self):
		_bin_b()
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		STATE["left"] = 3.0
		out = api.save_stock("LG-505025", "Bin B2-1-1 - SF")
		self.assertEqual(
			out["note"],
			"Bin A1-3-1 - SF still has 3 on hand, so the item stays on that bin too until it is moved.",
		)

	def test_the_same_bin_again_takes_nothing_off(self):
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		self.assertEqual(STATE["released"], [])

	def test_a_refused_release_undoes_the_new_assignment_too(self):
		_bin_b()
		api.save_stock("LG-505025", "Bin A1-3-1 - SF")
		STATE["release_refusal"] = (FRAPPE.PermissionError, "Changing bins needs a stock role.")
		out = api.save_stock("LG-505025", "Bin B2-1-1 - SF")
		self.assertEqual(
			STATE["savepoint_rollbacks"], [api.ASSIGN_SAVEPOINT], "both helpers' writes, together"
		)
		self.assertEqual(
			out["note"], "Stock Scan's bin list was not changed: Changing bins needs a stock role."
		)
		self.assertEqual(_stored("LG-505025")["item_defaults"][0]["default_warehouse"], "Bin B2-1-1 - SF")
		self.assertEqual(FRAPPE.message_log, [])

	def test_a_bin_the_flow_refuses_never_reaches_the_bin_module(self):
		for warehouse in ("Inventory - SF", "Truck - SF", "Nowhere"):
			with self.subTest(warehouse=warehouse):
				with self.assertRaises(FRAPPE.ValidationError):
					api.save_stock("LG-505025", warehouse)
		self.assertEqual(STATE["assigned"], [])


class TestAsset(unittest.TestCase):
	def setUp(self):
		_reset()

	def _create(self, **extra):
		args = {
			"item_code": "RENTAL-FOUNTAIN-01",
			"client_id": "gi-mg8x1-abcdefghijkl",
			"location": "Upstairs Rental Warehouse",
			"net_purchase_amount": "1200",
			"purchase_date": "2026-09-30",
			"rentable": 1,
		}
		args.update(extra)
		return api.create_asset(**args)

	def test_makes_a_draft_asset_from_the_item(self):
		out = self._create()
		self.assertEqual(out["deduped"], 0)
		asset = STATE["db"]["Asset"][out["name"]]
		for key, value in (
			("item_code", "RENTAL-FOUNTAIN-01"),
			("asset_name", "PUMP, RENTAL-FOUNTAIN-01"),
			("asset_category", "Rental Fountain Fleet"),
			("company", "Sapphire Fountains"),
			("location", "Upstairs Rental Warehouse"),
			("purchase_date", "2026-09-30"),
			("net_purchase_amount", 1200.0),
			("asset_type", "Existing Asset"),
			("custom_rentable", 1),
			("custom_guided_client_id", "gi-mg8x1-abcdefghijkl"),
		):
			self.assertEqual(asset.get(key), value, key)

	def test_a_resend_returns_the_first_asset(self):
		first = self._create()
		again = self._create()
		self.assertEqual(again, {"name": first["name"], "deduped": 1})
		self.assertEqual(len(STATE["db"]["Asset"]), 1)

	def test_a_resend_from_someone_else_is_not_theirs(self):
		self._create()
		FRAPPE.session.user = "clegg@example.com"
		other = self._create()
		self.assertEqual(other["deduped"], 0)
		self.assertEqual(len(STATE["db"]["Asset"]), 2)

	def test_a_racing_resend_reads_the_winner_after_the_unique_index(self):
		STATE["race"] = True
		out = self._create()
		self.assertEqual(out, {"name": "ACC-ASS-2026-00099", "deduped": 1})
		self.assertEqual(STATE["rollbacks"], 1)

	def test_refusals_come_before_anything_is_made(self):
		cases = (
			({"item_code": "LG-505025"}, FRAPPE.ValidationError),
			({"client_id": "short"}, FRAPPE.ValidationError),
			({"location": ""}, FRAPPE.ValidationError),
			({"location": "Nowhere"}, FRAPPE.ValidationError),
			({"net_purchase_amount": "0"}, FRAPPE.ValidationError),
		)
		for extra, exc in cases:
			with self.subTest(extra=extra):
				with self.assertRaises(exc):
					self._create(**extra)
		_item("RENTAL-FOUNTAIN-02", is_fixed_asset=1)
		with self.assertRaises(FRAPPE.ValidationError):
			self._create(item_code="RENTAL-FOUNTAIN-02")
		self.assertEqual(STATE["db"]["Asset"], {})

	def test_without_asset_create_it_is_refused(self):
		_reset({("Asset", "create"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			self._create()
		self.assertEqual(STATE["db"]["Asset"], {})

	def test_before_the_column_exists_it_says_so(self):
		STATE["meta"]["Asset"] = {"custom_rentable"}
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			self._create()
		self.assertIn("custom_guided_client_id", str(caught.exception))


class TestReads(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_setup_says_what_the_caller_may_do(self):
		out = api.get_setup("RENTAL-FOUNTAIN-01")
		self.assertEqual(out["can"], {"write": 1, "price": 1, "link": 1, "asset": 1, "label": 1})
		self.assertEqual(
			(out["price_list"], out["currency"], out["auto_reorder"]), ("Standard Buying", "USD", 1)
		)
		_reset({("Item Price", "create"): False, ("Item Price", "write"): False, ("Asset", "create"): False})
		STATE["roles"] = ["Purchase User"]
		STATE["meta"]["Item Supplier"] = set()
		out = api.get_setup("RENTAL-FOUNTAIN-01")
		self.assertEqual(out["can"], {"write": 1, "price": 0, "link": 0, "asset": 0, "label": 0})

	def _price(self, name, supplier, rate, uom="Unit", modified="2026-10-01 00:00:00"):
		STATE["db"]["Item Price"][name] = {
			"name": name,
			"item_code": "LG-505025",
			"price_list": "Standard Buying",
			"supplier": supplier,
			"uom": uom,
			"price_list_rate": rate,
			"currency": "USD",
			"modified": modified,
		}

	def test_the_price_shown_is_the_newest_in_the_stock_unit(self):
		"""Review of #1270: a dict built over rows newest-first kept the OLDEST, and a price in
		another unit could stand in for the stock unit's, so a stale rate was offered and re-sent."""
		api.save_supplier("LG-505025", "Little Giant", rate="")
		self._price("IP-OLD", "Little Giant", 30.0, modified="2026-09-01 00:00:00")
		self._price("IP-NEW", "Little Giant", 41.5, modified="2026-10-01 00:00:00")
		self._price("IP-BOX", "Little Giant", 400.0, uom="Box", modified="2026-10-05 00:00:00")
		out = api.get_setup("LG-505025")
		self.assertEqual(out["suppliers"][0]["rate"], 41.5)

	def test_a_save_without_a_rate_leaves_the_price_alone(self):
		api.save_supplier("LG-505025", "Little Giant", rate="")
		self._price("IP-NEW", "Little Giant", 41.5)
		api.save_supplier("LG-505025", "Little Giant", supplier_part_no="505025-B")
		self.assertEqual(STATE["db"]["Item Price"]["IP-NEW"]["price_list_rate"], 41.5)
		self.assertNotIn(("Item Price", "IP-NEW"), STATE["saves"])

	def test_prices_and_assets_are_read_through_the_callers_permissions(self):
		api.create_asset("RENTAL-FOUNTAIN-01", "gi-mg8x1-abcdefghijkl", "Upstairs Rental Warehouse", "1200")
		out = api.get_setup("RENTAL-FOUNTAIN-01")
		self.assertEqual(len(out["assets"]), 1)
		self.assertIn("Asset", STATE["get_list"])
		_reset({("Asset", "read"): False, ("Item Price", "read"): False})
		STATE["get_list"].clear()
		out = api.get_setup("RENTAL-FOUNTAIN-01")
		self.assertEqual(out["assets"], [])
		self.assertEqual(STATE["get_list"], [])

	def test_setup_needs_item_read(self):
		_reset({("Item", "read"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			api.get_setup("LG-505025")

	def test_the_preview_is_stock_scans_and_needs_item_create(self):
		out = api.preview_item(" X-1 ", "PUMP, X", "Pumps", "Unit")
		self.assertEqual(out["asked"], ["X-1", "PUMP, X", "Pumps", "Unit"])
		_reset({("Item", "create"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			api.preview_item("X-1")

	def test_code_blocks_read_every_code_and_need_item_create(self):
		_item("PDT-0101")
		_item("PDT-0100")
		out = api.code_blocks("pdt")
		self.assertEqual(out["blocks"][0]["next_code"], "PDT-0102")
		with self.assertRaises(FRAPPE.ValidationError):
			api.code_blocks("CON")
		_reset({("Item", "create"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			api.code_blocks("PDT")

	def test_naming_options_need_item_read(self):
		_reset({("Item", "read"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			api.naming_options()


class TestTheSurface(unittest.TestCase):
	def setUp(self):
		self.tree = ast.parse(API_FILE.read_text(encoding="utf-8"))
		self.decorated = {}
		for node in self.tree.body:
			if isinstance(node, ast.FunctionDef):
				for decorator in node.decorator_list:
					if "whitelist" in ast.unparse(decorator):
						self.decorated[node.name] = ast.unparse(decorator)

	def test_writes_are_post_and_nothing_is_open_to_guests(self):
		self.assertEqual(
			set(self.decorated),
			{
				"naming_options",
				"preview_item",
				"code_blocks",
				"get_setup",
				"save_supplier",
				"save_stock",
				"create_asset",
			},
		)
		for name in ("save_supplier", "save_stock", "create_asset"):
			self.assertIn("methods=['POST']", self.decorated[name])
		for decorator in self.decorated.values():
			self.assertNotIn("allow_guest", decorator)

	def test_nothing_is_written_past_the_callers_permissions(self):
		source = API_FILE.read_text(encoding="utf-8")
		code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
		code = re.sub(r'"""[\s\S]*?"""', "", code)
		self.assertNotIn("ignore_permissions", code)
		self.assertNotIn("flags.ignore", code)

	def test_aggregates_are_never_get_all_strings(self):
		code = re.sub(r'"""[\s\S]*?"""', "", API_FILE.read_text(encoding="utf-8"))
		self.assertNotRegex(code, r"fields\s*=\s*\[[^\]]*\b(count|sum|max|min|avg)\s*\(", re.I)

	def test_the_file_is_tab_indented(self):
		"""A new api/ file, so tabs (api/README.md lists the package's mix)."""
		source = API_FILE.read_text(encoding="utf-8")
		self.assertIn("\n\tfrappe.has_permission(", source)
		self.assertNotRegex(source, r"(?m)^    (def|return|if|for|frappe|item|row)\b")


if __name__ == "__main__":
	unittest.main()

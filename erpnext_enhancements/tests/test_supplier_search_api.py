"""Bench-free tests for the Supplier Finder's endpoint and assistant tool (v1.722.0).

``api/supplier_search.py`` runs against a ``frappe`` stub installed in ``setUpModule``. Its reads
(the catalog, the suppliers, the groups, the evidence) are replaced per test, and the model is a
fake: nothing here calls Gemini. What is held:

* **The AI step degrades to the keyword search.** Not set up, rate-limited, raising, or answering
  something other than the JSON asked for: the keyword results still come back, with a plain
  message, and the Error Log row names the exception's class, never the search.
* **The model sees the search and the group names, nothing else.** No supplier, no item, no price
  reaches the prompt (Nik's ruling, 2026-10-22).
* A model or part number skips the AI step, and an expansion is cached so the same search does not
  call the model twice.
* Supplier and Item read, or nothing. The assistant tool's ``expand`` is off unless it is ``true``.

Run: python -m unittest erpnext_enhancements.tests.test_supplier_search_api -v
"""

import datetime as dt
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

api = None
tool_module = None
STATE = None

# Invented suppliers: the repo is public.
SUPPLIERS = {
	"Valley Pool Co": {"supplier_name": "Valley Pool Co", "groups": ["Pool Supplies"], "disabled": False},
	"Acme Pump Co": {"supplier_name": "Acme Pump Co", "groups": ["Pumps"], "disabled": False},
	"Bright Lamp Works": {"supplier_name": "Bright Lamp Works", "groups": ["Lighting"], "disabled": False},
}
GROUPS = ["Lighting", "Pool Supplies", "Pumps"]
CATALOG = [
	{
		"item_code": "340605",
		"item_name": "PUMP, PENTAIR EQKT750, 7.5HP",
		"item_group": "Products",
		"description": "",
		"part_nos": ["PEN340605"],
	},
	{
		"item_code": "500035",
		"item_name": "PUMP, SUBMERSIBLE, 66 GPH",
		"item_group": "Pumps",
		"description": "",
		"part_nos": [],
	},
	{
		"item_code": "LT-1",
		"item_name": "LAMP, UNDERWATER, LED, 24V",
		"item_group": "Products",
		"description": "",
		"part_nos": [],
	},
]


def _ev(supplier, code, **kw):
	row = {
		"supplier": supplier,
		"item_code": code,
		"po_lines": 0,
		"pr_lines": 0,
		"sq_lines": 0,
		"listed": False,
		"part_no": "",
		"default": False,
		"price": False,
		"last_date": None,
		"last_rate": None,
		"uom": None,
	}
	row.update(kw)
	return row


EVIDENCE = [
	_ev("Valley Pool Co", "340605", po_lines=2, last_date="2026-10-01", last_rate=1200.0, uom="Nos"),
	_ev("Bright Lamp Works", "LT-1", po_lines=1, last_date="2026-09-01"),
]


def _install_stubs():
	global STATE
	STATE = {}
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	class PermissionError(Exception):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe._ = lambda s, *a, **k: s

	def throw(msg="", exc=ValidationError, title=None):
		raise exc(msg)

	frappe.throw = throw

	def whitelist(*dargs, **dkwargs):
		def wrap(fn):
			fn._whitelist = dkwargs
			return fn

		return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

	frappe.whitelist = whitelist
	frappe.session = types.SimpleNamespace(user="buyer@example.com")

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		allowed = STATE["perm"].get((doctype, ptype), True)
		STATE["perm_checks"].append((doctype, ptype))
		if throw and not allowed:
			raise PermissionError(f"No permission for {doctype}")
		return allowed

	frappe.has_permission = has_permission

	def log_error(*args, **kwargs):
		STATE["logged"].append((args, kwargs))

	frappe.log_error = log_error

	def sql(query, values=None, as_dict=False):
		STATE["sql"].append(query)
		return []

	frappe.db = types.SimpleNamespace(sql=sql, exists=lambda doctype, name: name in SUPPLIERS)

	counts = {}

	def incrby(key, amount=1):
		counts[key] = counts.get(key, 0) + amount
		return counts[key]

	def set_value(key, value, expires_in_sec=None):
		STATE["cache"][key] = value

	frappe.cache = types.SimpleNamespace(
		make_key=lambda key: f"site:{key}",
		incrby=incrby,
		expire=lambda key, seconds: None,
		get_value=lambda key: STATE["cache"].get(key),
		set_value=set_value,
	)
	STATE["counts"] = counts

	utils = types.ModuleType("frappe.utils")
	utils.cstr = lambda value: "" if value is None else str(value)
	utils.cint = lambda value: int(float(value or 0))
	utils.getdate = (
		lambda value=None: dt.date.fromisoformat(str(value)[:10]) if value else dt.date(2026, 10, 22)
	)
	utils.nowdate = lambda: "2026-10-22"
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	drive_pkg = types.ModuleType("erpnext_enhancements.google_drive")
	drive_utils = types.ModuleType("erpnext_enhancements.google_drive.drive_utils")
	drive_utils.get_service_account_info = lambda: STATE["service_account"]
	drive_pkg.drive_utils = drive_utils
	sys.modules["erpnext_enhancements.google_drive"] = drive_pkg
	sys.modules["erpnext_enhancements.google_drive.drive_utils"] = drive_utils

	gemini = types.ModuleType("erpnext_enhancements.api.gemini")

	def generate(prompt, system_instruction, settings, feature="unknown", model_id=None, **kwargs):
		STATE["model_calls"].append(
			{"prompt": prompt, "system": system_instruction, "feature": feature, **kwargs}
		)
		if STATE["model_raises"]:
			raise STATE["model_raises"]
		return STATE["reply"], "thoughts"

	gemini.generate_content_with_vertex_ai = generate
	sys.modules["erpnext_enhancements.api.gemini"] = gemini

	base_tool = types.ModuleType("frappe_assistant_core.core.base_tool")

	class BaseTool:
		def __init__(self):
			self.name = ""
			self.description = ""
			self.inputSchema = {}
			self.requires_permission = None

	base_tool.BaseTool = BaseTool
	fac = types.ModuleType("frappe_assistant_core")
	fac_core = types.ModuleType("frappe_assistant_core.core")
	fac.core = fac_core
	fac_core.base_tool = base_tool
	sys.modules["frappe_assistant_core"] = fac
	sys.modules["frappe_assistant_core.core"] = fac_core
	sys.modules["frappe_assistant_core.core.base_tool"] = base_tool


_STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.google_drive",
	"erpnext_enhancements.google_drive.drive_utils",
	"erpnext_enhancements.api.gemini",
	"frappe_assistant_core",
	"frappe_assistant_core.core",
	"frappe_assistant_core.core.base_tool",
	"erpnext_enhancements.api.supplier_search",
	"erpnext_enhancements.assistant_tools",
	"erpnext_enhancements.assistant_tools._gate",
	"erpnext_enhancements.assistant_tools.supplier_search",
)


def setUpModule():
	global api, tool_module
	for mod in _STUBBED:
		sys.modules.pop(mod, None)
	_install_stubs()
	from erpnext_enhancements.api import supplier_search
	from erpnext_enhancements.assistant_tools import supplier_search as tool

	api = supplier_search
	tool_module = tool


def tearDownModule():
	for mod in _STUBBED:
		sys.modules.pop(mod, None)


class Base(unittest.TestCase):
	def setUp(self):
		STATE.update(
			perm={},
			perm_checks=[],
			logged=[],
			sql=[],
			cache={},
			model_calls=[],
			model_raises=None,
			reply=json.dumps({"terms": ["submersible pump"], "groups": ["Pumps"], "note": "A small pump."}),
			service_account={"client_email": "sa@example.com"},
			evidence_calls=[],
		)
		STATE["counts"].clear()
		self._saved = {
			name: getattr(api, name) for name in ("_items", "_suppliers", "_group_names", "_evidence")
		}
		api._items = lambda: [dict(i) for i in CATALOG]
		api._suppliers = lambda: {k: dict(v, groups=list(v["groups"])) for k, v in SUPPLIERS.items()}
		api._group_names = lambda: list(GROUPS)

		def evidence(item_codes=None, suppliers=None, with_rates=False):
			STATE["evidence_calls"].append(
				{"item_codes": item_codes, "suppliers": suppliers, "with_rates": with_rates}
			)
			rows = [dict(e) for e in EVIDENCE]
			if item_codes is not None:
				rows = [e for e in rows if e["item_code"] in item_codes]
			if suppliers is not None:
				rows = [e for e in rows if e["supplier"] in suppliers]
			if not with_rates:
				for e in rows:
					e["last_rate"] = e["uom"] = None
			return rows

		api._evidence = evidence

	def tearDown(self):
		for name, fn in self._saved.items():
			setattr(api, name, fn)


class SearchTest(Base):
	def test_empty_search_is_a_message(self):
		answer = api.run_search("  ")
		self.assertFalse(answer["ok"])
		self.assertEqual(answer["message"], api.MSG_EMPTY)

	def test_keyword_search_ranks_history_first_and_never_calls_the_model(self):
		answer = api.run_search("pump")
		self.assertTrue(answer["ok"])
		self.assertEqual(STATE["model_calls"], [])
		self.assertFalse(answer["ai"]["asked"])
		self.assertEqual([s["supplier"] for s in answer["suppliers"]], ["Valley Pool Co", "Acme Pump Co"])
		self.assertEqual(answer["suppliers"][0]["tier"], "history")
		self.assertEqual(answer["suppliers"][1]["tier"], "group")
		self.assertTrue(answer["suppliers"][0]["reason"].startswith("Bought 1 matching item"))
		by_code = {i["item_code"]: i for i in answer["items"]}
		self.assertEqual(by_code["340605"]["suppliers"], ["Valley Pool Co"])
		self.assertEqual([g["name"] for g in answer["groups"]], ["Pumps"])

	def test_rates_only_for_purchase_order_readers(self):
		answer = api.run_search("pump")
		self.assertEqual(answer["suppliers"][0]["items"][0]["last_rate"], 1200.0)
		STATE["perm"][("Purchase Order", "read")] = False
		answer = api.run_search("pump")
		self.assertFalse(answer["rates"])
		self.assertIsNone(answer["suppliers"][0]["items"][0]["last_rate"])
		self.assertFalse(STATE["evidence_calls"][-1]["with_rates"])

	def test_model_number_finds_its_category_groups(self):
		answer = api.run_search("EQKT750")
		self.assertEqual([i["item_code"] for i in answer["items"]], ["340605"])
		self.assertEqual(
			answer["groups"],
			[{"name": "Pumps", "hits": 1, "coverage": 1.0, "source": "category", "suppliers": 1}],
		)
		self.assertEqual([s["supplier"] for s in answer["suppliers"]], ["Valley Pool Co", "Acme Pump Co"])

	def test_pinned_item(self):
		answer = api.run_search(None, item_code="LT-1")
		self.assertEqual([i["item_code"] for i in answer["items"]], ["LT-1"])
		self.assertEqual(answer["suppliers"][0]["supplier"], "Bright Lamp Works")
		self.assertEqual(STATE["model_calls"], [])

	def test_search_endpoint_needs_supplier_and_item_read(self):
		for doctype in ("Supplier", "Item"):
			STATE["perm"] = {(doctype, "read"): False}
			with self.subTest(doctype=doctype), self.assertRaises(sys.modules["frappe"].PermissionError):
				api.search("pump")
		self.assertEqual(api.search._whitelist, {"methods": ["POST"]})


class AiStepTest(Base):
	def test_expansion_adds_items_and_groups(self):
		catalog = CATALOG + [
			{
				"item_code": "SP-9",
				"item_name": "PUMP, SUBMERSIBLE, FOUNTAIN, 1/2HP",
				"item_group": "Products",
				"description": "",
				"part_nos": [],
			}
		]
		api._items = lambda: [dict(i) for i in catalog]
		answer = api.run_search("something to move water in a small fountain", use_ai=1)
		self.assertTrue(answer["ai"]["used"])
		self.assertEqual(answer["ai"]["terms"], ["SUBMERSIBLE PUMP"])
		self.assertEqual(answer["ai"]["groups"], ["Pumps"])
		via_ai = [i["item_code"] for i in answer["items"] if i.get("via_ai")]
		self.assertIn("SP-9", via_ai)
		self.assertIn("Acme Pump Co", [s["supplier"] for s in answer["suppliers"]])
		self.assertEqual(STATE["model_calls"][0]["feature"], "supplier_search_expand")

	def test_the_model_sees_the_search_and_group_names_only(self):
		api.run_search("pump stuff", use_ai=1)
		call = STATE["model_calls"][0]
		text = call["prompt"] + call["system"]
		for name in list(SUPPLIERS) + [i["item_name"] for i in CATALOG] + ["1200", "PEN340605"]:
			self.assertNotIn(name, text)
		self.assertIn("pump stuff", call["prompt"])
		for g in GROUPS:
			self.assertIn(f"- {g}", call["prompt"])

	def test_cached_expansion_is_not_asked_twice(self):
		api.run_search("pump stuff", use_ai=1)
		answer = api.run_search("Pump stuff!", use_ai=1)
		self.assertEqual(len(STATE["model_calls"]), 1)
		self.assertTrue(answer["ai"]["used"])

	def test_a_model_number_skips_the_ai_step(self):
		answer = api.run_search("EQKT750", use_ai=1)
		self.assertEqual(STATE["model_calls"], [])
		self.assertEqual(answer["ai"]["message"], api.MSG_AI_SKIPPED_MODEL)

	def test_not_set_up_falls_back(self):
		STATE["service_account"] = None
		answer = api.run_search("pump", use_ai=1)
		self.assertTrue(answer["ok"])
		self.assertEqual(STATE["model_calls"], [])
		self.assertEqual(answer["ai"]["message"], api.MSG_AI_OFF)
		self.assertEqual(answer["suppliers"][0]["supplier"], "Valley Pool Co")

	def test_a_failing_model_falls_back_and_logs_no_query(self):
		STATE["model_raises"] = TimeoutError("read timed out for pump secret")
		answer = api.run_search("pump secret", use_ai=1)
		self.assertTrue(answer["ok"])
		self.assertFalse(answer["ai"]["used"])
		self.assertEqual(answer["ai"]["message"], api.MSG_AI_FAILED)
		self.assertEqual(answer["suppliers"][0]["supplier"], "Valley Pool Co")
		((_args, kwargs),) = STATE["logged"]
		self.assertTrue(kwargs["message"].startswith("TimeoutError"))
		self.assertEqual(STATE["cache"], {})

	def test_a_garbage_reply_falls_back(self):
		STATE["reply"] = "I think you want a pump."
		answer = api.run_search("pump", use_ai=1)
		self.assertEqual(answer["ai"]["message"], api.MSG_AI_FAILED)
		self.assertEqual(len(STATE["logged"]), 1)
		self.assertEqual(STATE["cache"], {})

	def test_rate_limit(self):
		for _i in range(api.RATE_LIMIT):
			STATE["cache"].clear()
			api.run_search("pump", use_ai=1)
		STATE["cache"].clear()
		answer = api.run_search("pump", use_ai=1)
		self.assertEqual(answer["ai"]["message"], api.MSG_AI_BUSY)
		self.assertEqual(len(STATE["model_calls"]), api.RATE_LIMIT)


class HistoryTest(Base):
	def test_aliases_fold_into_the_history(self):
		suppliers = dict(SUPPLIERS)
		suppliers["Valley Pool Company, Inc."] = {
			"supplier_name": "Valley Pool Company, Inc.",
			"groups": ["Pumps"],
			"disabled": False,
		}
		api._suppliers = lambda: {k: dict(v, groups=list(v["groups"])) for k, v in suppliers.items()}
		answer = api.run_supplier_history("Valley Pool Co")
		self.assertEqual(answer["aliases"], ["Valley Pool Company, Inc."])
		self.assertEqual(answer["groups"], ["Pool Supplies", "Pumps"])
		self.assertEqual(
			STATE["evidence_calls"][-1]["suppliers"], ["Valley Pool Co", "Valley Pool Company, Inc."]
		)
		self.assertEqual([i["item_code"] for i in answer["items"]], ["340605"])

	def test_unknown_supplier(self):
		self.assertFalse(api.run_supplier_history("Nobody")["ok"])


class ToolTest(Base):
	def tool(self):
		return tool_module.SupplierSearch()

	def test_shape(self):
		t = self.tool()
		self.assertEqual(t.name, "supplier_search")
		self.assertEqual(t.requires_permission, "Supplier")
		self.assertNotIn("required", t.inputSchema)

	def test_needs_a_query_or_an_item(self):
		with self.assertRaises(sys.modules["frappe"].ValidationError):
			self.tool().execute({"query": 7})

	def test_expand_only_when_true(self):
		self.tool().execute({"query": "pump stuff", "expand": "yes"})
		self.assertEqual(STATE["model_calls"], [])
		self.tool().execute({"query": "pump stuff", "expand": True})
		self.assertEqual(len(STATE["model_calls"]), 1)

	def test_needs_item_read(self):
		STATE["perm"] = {("Item", "read"): False}
		with self.assertRaises(sys.modules["frappe"].PermissionError):
			self.tool().execute({"query": "pump"})

	def test_limit(self):
		answer = self.tool().execute({"query": "pump", "limit": 1})
		self.assertEqual(len(answer["suppliers"]), 1)


if __name__ == "__main__":
	unittest.main()

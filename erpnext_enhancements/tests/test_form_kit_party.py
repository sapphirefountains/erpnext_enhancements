"""Bench-free tests for the Party entry component (form kit P3.2, TASK-2026-02755, v1.677.0).

Three halves:

* **Under node**: the component's pure decisions (``public/js/form_kit/party.js``) against the real
  information blocks (``public/js/info_blocks/blocks.js``): which field is the account, the contact,
  the billing address and the site (held equal to ``form_kit.blocks.party_roles``), what "+ New"
  makes on each, and what happens to the contact and addresses when the account changes: a value
  of the new account's stays, one the person chose this visit stays and is reported, anything else
  follows the account.
* **The endpoint** (``form_kit.api.party_details`` / ``form_kit.party.details``) against a frappe
  stub with an in-memory set of Contacts and Addresses and their Dynamic Links: the allowlist, read
  on the DocType and on the account, the account kind the Party block allows, ERPNext's default
  contact / address rules (and the site rule this app adds), and every read through ``get_list``.
* **The wiring**: the editor both shells run makes one PartyEntry and calls it where it should, the
  component's one request is sf_kit's quiet GET, it never writes HTML from a value, its styles are
  in the bundle stylesheet with tokens only, and the Back guard is the planner kit's.

Stubs ``frappe`` in ``setUpModule`` and restores it after. Every name here is invented.

Run: python -m unittest erpnext_enhancements.tests.test_form_kit_party -v
"""

import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
FORM_KIT_JS = JS / "form_kit"
BLOCKS_JS = JS / "info_blocks"
PARTY_UI = FORM_KIT_JS / "ui" / "party.js"
EDITOR_JS = FORM_KIT_JS / "ui" / "editor.js"
CSS = APP / "public" / "css" / "guided_entry.bundle.css"
PLANNER_STYLES = JS / "planner_kit" / "styles.js"
PARTY_PY = APP / "form_kit" / "party.py"
README = APP / "form_kit" / "README.md"

#: Restored after the suite: the stub and every form_kit module imported against it.
_MODULES = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.form_kit.party",
	"erpnext_enhancements.form_kit.api",
	"erpnext_enhancements.form_kit.saver",
	"erpnext_enhancements.form_kit.steps",
	"erpnext_enhancements.form_kit.completeness",
	"erpnext_enhancements.form_kit.field_help",
)
_saved = {}
party = api = blocks = None
FRAPPE = None
STATE = {}


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


# ---------------------------------------------------------------------------------- under node

RUNNER = r"""
import { pathToFileURL } from "node:url";
const dir = process.argv[2];
const party = await import(pathToFileURL(dir + "/form_kit/party.js").href);
const kit = await import(pathToFileURL(dir + "/form_kit/index.js").href);
const { BLOCKS } = await import(pathToFileURL(dir + "/info_blocks/blocks.js").href);
const opp = party.party_roles(BLOCKS, "Opportunity");
const out = {};
out.roles = {
	Opportunity: opp,
	Project: party.party_roles(BLOCKS, "Project"),
	Lead: party.party_roles(BLOCKS, "Lead"),
	inherited: party.party_roles(BLOCKS, "toString"),
};
out.index_exports = ["party_roles", "role_of", "account_of", "create_kind", "details_args", "plan_account_change"].every(
	(name) => typeof kit[name] === "function"
);
out.role_of = ["opportunity_from", "party_name", "primary_contact", "customer_address", "primary_address", "contact_person", ""].map(
	(f) => party.role_of(opp, f)
);
out.account_of = {
	customer: party.account_of(opp, { opportunity_from: "Customer", party_name: " Harbor Plaza " }),
	lead: party.account_of(opp, { opportunity_from: "Lead", party_name: "CRM-LEAD-0001" }),
	not_allowed: party.account_of(opp, { opportunity_from: "Supplier", party_name: "Acme" }),
	no_name: party.account_of(opp, { opportunity_from: "Customer", party_name: "  " }),
	no_type: party.account_of(opp, { party_name: "Harbor Plaza" }),
	project: party.account_of(party.party_roles(BLOCKS, "Project"), { customer: "Harbor Plaza" }),
	no_roles: party.account_of(null, {}),
};
out.create_kind = {
	account_customer: party.create_kind(opp, "party_name", { opportunity_from: "Customer" }),
	account_lead: party.create_kind(opp, "party_name", { opportunity_from: "Lead" }),
	account_unknown: party.create_kind(opp, "party_name", {}),
	contact: party.create_kind(opp, "primary_contact", {}),
	billing: party.create_kind(opp, "customer_address", {}),
	site: party.create_kind(opp, "primary_address", {}),
	other: party.create_kind(opp, "custom_opportunity_name", {}),
};
out.details_args = party.details_args(opp, {
	primary_contact: "Dee Example-Harbor Plaza",
	customer_address: "Harbor Plaza-Billing",
	primary_address: "Harbor Plaza-Billing",
});
out.details_args_empty = party.details_args(opp, {});

const details = {
	defaults: { contact: "Ann New-Bayside", billing: "Bayside-Billing", site: "Bayside-Shipping" },
	linked: { Contact: ["Cam Shared-Bayside"], Address: ["Bayside-Billing"] },
};
const plan = (values, chosen, editable, d) =>
	party.plan_account_change({ roles: opp, values, chosen, details: d === undefined ? details : d, editable });
out.plan = {
	// Nothing chosen this visit: everything follows the new account.
	follows: plan({ primary_contact: "Old Person-Harbor Plaza", customer_address: "Harbor Plaza-Billing", primary_address: "" }, []),
	// Already the new account's: stays.
	linked_stays: plan({ primary_contact: "Cam Shared-Bayside", customer_address: "Bayside-Billing", primary_address: "Bayside-Shipping" }, []),
	// Chosen this visit and not the new account's: kept and reported, never overwritten.
	chosen_kept: plan(
		{ primary_contact: "Old Person-Harbor Plaza", customer_address: "Harbor Plaza-Billing", primary_address: "Harbor Plaza-Site" },
		["primary_contact", "primary_address"]
	),
	// Chosen this visit and empty: nothing to keep, the suggestion fills it.
	chosen_empty: plan({ primary_contact: "" }, ["primary_contact"]),
	// The new account has no defaults: a stale value is emptied; an empty one stays empty.
	no_defaults: plan({ primary_contact: "Old Person-Harbor Plaza", customer_address: "" }, [], null, { defaults: {}, linked: {} }),
	// A field the person may not change is never touched.
	not_editable: plan({ primary_contact: "Old Person-Harbor Plaza" }, [], ["customer_address", "primary_address"]),
	no_details: plan({ primary_contact: "x" }, [], null, null),
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestPartyDecisionsUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(FORM_KIT_JS, folder / "form_kit")
			shutil.copytree(BLOCKS_JS, folder / "info_blocks")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			proc = subprocess.run(
				[node, str(folder / "runner.mjs"), str(folder)],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	def test_roles_are_the_python_registrys(self):
		from erpnext_enhancements.form_kit import blocks as py_blocks

		for doctype in ("Opportunity", "Project", "Lead"):
			with self.subTest(doctype=doctype):
				self.assertEqual(self.out["roles"][doctype], py_blocks.party_roles(doctype))
		self.assertIsNone(self.out["roles"]["inherited"])
		self.assertTrue(self.out["index_exports"], "the bundle's kit does not carry the party decisions")

	def test_each_field_has_its_role(self):
		self.assertEqual(
			self.out["role_of"], ["account_type", "account", "contact", "billing", "site", None, None]
		)

	def test_the_account_is_a_kind_the_block_allows(self):
		a = self.out["account_of"]
		self.assertEqual(a["customer"], {"doctype": "Customer", "name": "Harbor Plaza"})
		self.assertEqual(a["lead"], {"doctype": "Lead", "name": "CRM-LEAD-0001"})
		self.assertEqual(a["project"], {"doctype": "Customer", "name": "Harbor Plaza"})
		for case in ("not_allowed", "no_name", "no_type", "no_roles"):
			with self.subTest(case=case):
				self.assertIsNone(a[case])

	def test_new_makes_the_right_record(self):
		k = self.out["create_kind"]
		self.assertEqual(k["account_customer"], {"doctype": "Customer", "role": "account"})
		self.assertEqual(k["account_lead"], {"doctype": "Lead", "role": "account"})
		self.assertIsNone(k["account_unknown"], "no kind chosen: nothing to make yet")
		self.assertEqual(k["contact"], {"doctype": "Contact", "role": "contact"})
		self.assertEqual(k["billing"], {"doctype": "Address", "role": "billing"})
		self.assertEqual(k["site"], {"doctype": "Address", "role": "site"})
		self.assertIsNone(k["other"])

	def test_the_current_values_are_asked_about_once_each(self):
		self.assertEqual(
			self.out["details_args"],
			{"contacts": ["Dee Example-Harbor Plaza"], "addresses": ["Harbor Plaza-Billing"]},
		)
		self.assertEqual(self.out["details_args_empty"], {"contacts": [], "addresses": []})

	def test_unchosen_values_follow_the_account(self):
		p = self.out["plan"]["follows"]
		self.assertEqual(
			p["set"],
			{
				"primary_contact": "Ann New-Bayside",
				"customer_address": "Bayside-Billing",
				"primary_address": "Bayside-Shipping",
			},
		)
		self.assertEqual(p["kept"], [])

	def test_values_of_the_new_account_stay(self):
		self.assertEqual(self.out["plan"]["linked_stays"], {"set": {}, "kept": []})

	def test_a_value_chosen_this_visit_is_never_silently_overwritten(self):
		p = self.out["plan"]["chosen_kept"]
		self.assertEqual(p["set"], {"customer_address": "Bayside-Billing"})
		self.assertEqual(
			p["kept"],
			[
				{"fieldname": "primary_contact", "role": "contact", "suggestion": "Ann New-Bayside"},
				{"fieldname": "primary_address", "role": "site", "suggestion": "Bayside-Shipping"},
			],
		)
		self.assertEqual(self.out["plan"]["chosen_empty"]["set"]["primary_contact"], "Ann New-Bayside")

	def test_no_defaults_empties_a_stale_value(self):
		p = self.out["plan"]["no_defaults"]
		self.assertEqual(p["set"], {"primary_contact": None})
		self.assertEqual(p["kept"], [])

	def test_only_editable_fields_are_touched(self):
		self.assertNotIn("primary_contact", self.out["plan"]["not_editable"]["set"])
		self.assertEqual(self.out["plan"]["no_details"], {"set": {}, "kept": []})


# ---------------------------------------------------------------------------------- the endpoint


class PermissionError_(Exception):
	pass


class ValidationError_(Exception):
	pass


def _match(row, condition):
	field, op, value = condition[-3:]
	return _compare(row.get(field), op, value)


def _compare(actual, op, value):
	if op == "=":
		return actual == value
	if op == "in":
		return actual in value
	raise AssertionError(f"unexpected operator {op}")


def _get_list(
	doctype, filters=None, or_filters=None, fields=None, order_by=None, limit_page_length=None, **kwargs
):
	STATE["get_list"].append(
		{"doctype": doctype, "filters": filters, "or_filters": or_filters, "order_by": order_by}
	)
	for field in fields or []:
		assert "(" not in field, "Frappe 16 refuses SQL functions as strings in get_list"
	rows = [r for r in STATE["records"].get(doctype, []) if r["name"] not in STATE["unreadable"]]
	# Dynamic Link conditions name ONE link row: both halves must be on the same row.
	link_conds = [c for c in filters or [] if len(c) == 4 and c[0] == "Dynamic Link"]
	other = [c for c in filters or [] if not (len(c) == 4 and c[0] == "Dynamic Link")]
	if link_conds:
		want = {c[1]: c[3] for c in link_conds}
		rows = [r for r in rows if any(all(link.get(k) == v for k, v in want.items()) for link in r["links"])]
	rows = [r for r in rows if all(_match(r, c) for c in other)]
	if or_filters:
		rows = [r for r in rows if any(_match(r, c) for c in or_filters)]
	# order_by is honored the way the module asks for it: flags first, then newest change.
	keys = [part.strip().split() for part in (order_by or "").split(",") if part.strip()]
	for field, direction in reversed(keys):
		rows = sorted(rows, key=lambda r, f=field: r.get(f) or 0, reverse=direction.lower() == "desc")
	if limit_page_length:
		rows = rows[:limit_page_length]
	return [{f: r.get(f) for f in fields or ["name"]} for r in rows]


def _install_frappe():
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.escape_html = lambda s: str(s).replace("<", "&lt;").replace(">", "&gt;")
	# What the rest of form_kit imports from frappe.utils (api.py imports it all).
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v: float(v or 0)
	frappe.utils = utils
	frappe.PermissionError = PermissionError_
	frappe.ValidationError = ValidationError_
	frappe._ = lambda s: s

	def throw(message, exc=None, title=None):
		raise (exc or ValidationError_)(message)

	frappe.throw = throw
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	def has_permission(doctype, ptype="read", doc=None, throw=False, **kwargs):
		STATE["perm_checks"].append((doctype, ptype, doc))
		ok = (doctype, ptype) not in STATE["denied"] and (doctype, ptype, doc) not in STATE["denied"]
		if not ok and throw:
			raise PermissionError_(f"No {ptype} on {doctype} {doc or ''}")
		return ok

	frappe.has_permission = has_permission
	frappe.get_list = _get_list

	class Meta:
		def __init__(self, fields):
			self._fields = set(fields)

		def has_field(self, fieldname):
			return fieldname in self._fields

	frappe.get_meta = lambda doctype: Meta(
		{"Customer": ["primary_contact", "customer_primary_contact"]}.get(doctype, [])
	)

	class DB:
		def get_value(self, doctype, name, fields, as_dict=False):
			STATE["db_reads"].append((doctype, name))
			row = STATE["accounts"].get((doctype, name)) or {}
			return {f: row.get(f) for f in fields}

		def sql(self, *args, **kwargs):  # pragma: no cover - guarded below
			raise AssertionError("party details read with raw SQL")

	frappe.db = DB()
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


def setUpModule():
	global party, api, blocks, FRAPPE
	for name in _MODULES:
		_saved[name] = sys.modules.pop(name, None)
	FRAPPE = _install_frappe()
	party = importlib.import_module("erpnext_enhancements.form_kit.party")
	api = importlib.import_module("erpnext_enhancements.form_kit.api")
	blocks = importlib.import_module("erpnext_enhancements.form_kit.blocks")


def tearDownModule():
	for name in _MODULES:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _link(doctype, name):
	return {"link_doctype": doctype, "link_name": name}


def _reset(**overrides):
	STATE.clear()
	STATE.update(
		{
			"get_list": [],
			"perm_checks": [],
			"db_reads": [],
			"denied": set(),
			"unreadable": set(),
			"accounts": {},
			"records": {
				"Contact": [
					{
						"name": "Old Person-Harbor Plaza",
						"links": [_link("Customer", "Harbor Plaza")],
						"modified": 1,
					},
					{
						"name": "Ann New-Bayside",
						"links": [_link("Customer", "Bayside")],
						"is_primary_contact": 1,
						"modified": 2,
					},
					{"name": "Bo Other-Bayside", "links": [_link("Customer", "Bayside")], "modified": 3},
					{
						"name": "Cam Shared-Bayside",
						"links": [_link("Customer", "Harbor Plaza"), _link("Customer", "Bayside")],
						"modified": 4,
					},
				],
				"Address": [
					{
						"name": "Harbor Plaza-Billing",
						"links": [_link("Customer", "Harbor Plaza")],
						"address_type": "Billing",
						"disabled": 0,
						"modified": 1,
					},
					{
						"name": "Bayside-Billing",
						"links": [_link("Customer", "Bayside")],
						"address_type": "Billing",
						"is_primary_address": 1,
						"disabled": 0,
						"modified": 2,
					},
					{
						"name": "Bayside-Office",
						"links": [_link("Customer", "Bayside")],
						"address_type": "Office",
						"disabled": 0,
						"modified": 5,
					},
					{
						"name": "Bayside-Shipping",
						"links": [_link("Customer", "Bayside")],
						"address_type": "Shipping",
						"disabled": 0,
						"modified": 3,
					},
					{
						"name": "Bayside-Old",
						"links": [_link("Customer", "Bayside")],
						"address_type": "Shipping",
						"disabled": 1,
						"modified": 9,
					},
				],
			},
		}
	)
	STATE.update(overrides)


def _details(
	party_type="Customer", party_name="Bayside", contacts=None, addresses=None, doctype="Opportunity"
):
	return api.party_details(
		doctype,
		party_type,
		party_name,
		json.dumps(contacts or []),
		json.dumps(addresses or []),
	)


class TestPartyDetails(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_defaults_are_erpnexts(self):
		got = _details(
			contacts=["Old Person-Harbor Plaza", "Cam Shared-Bayside"], addresses=["Harbor Plaza-Billing"]
		)
		self.assertEqual(
			got,
			{
				# The flagged primary contact; the flagged primary address for billing; the one Shipping
				# address (a disabled one does not count) for the site.
				"defaults": {
					"contact": "Ann New-Bayside",
					"billing": "Bayside-Billing",
					"site": "Bayside-Shipping",
				},
				"linked": {"Contact": ["Cam Shared-Bayside"], "Address": []},
			},
		)

	def test_a_flagged_shipping_address_wins_over_the_type(self):
		STATE["records"]["Address"].append(
			{
				"name": "Bayside-Yard",
				"links": [_link("Customer", "Bayside")],
				"address_type": "Billing",
				"is_shipping_address": 1,
				"disabled": 0,
				"modified": 0,
			}
		)
		self.assertEqual(_details()["defaults"]["site"], "Bayside-Yard")

	def test_two_shipping_addresses_and_no_flag_suggest_no_site(self):
		STATE["records"]["Address"].append(
			{
				"name": "Bayside-Dock",
				"links": [_link("Customer", "Bayside")],
				"address_type": "Shipping",
				"disabled": 0,
				"modified": 4,
			}
		)
		self.assertIsNone(_details()["defaults"]["site"])

	def test_one_address_is_the_billing_address_and_the_site(self):
		got = _details(party_name="Harbor Plaza")
		self.assertEqual(got["defaults"]["billing"], "Harbor Plaza-Billing")
		self.assertEqual(got["defaults"]["site"], "Harbor Plaza-Billing")

	def test_no_flag_falls_back_to_the_most_recent_address_for_billing(self):
		for row in STATE["records"]["Address"]:
			row.pop("is_primary_address", None)
		self.assertEqual(_details()["defaults"]["billing"], "Bayside-Office")

	def test_no_flagged_contact_falls_back_to_the_accounts_own_field_only_when_linked(self):
		for row in STATE["records"]["Contact"]:
			row.pop("is_primary_contact", None)
		STATE["accounts"][("Customer", "Bayside")] = {
			"primary_contact": "Old Person-Harbor Plaza",
			"customer_primary_contact": "Bo Other-Bayside",
		}
		# primary_contact names someone not linked to Bayside: skipped for the next field.
		self.assertEqual(_details()["defaults"]["contact"], "Bo Other-Bayside")
		STATE["accounts"][("Customer", "Bayside")] = {}
		self.assertIsNone(_details()["defaults"]["contact"])

	def test_an_unreadable_record_is_neither_suggested_nor_reported(self):
		STATE["unreadable"] = {"Ann New-Bayside", "Cam Shared-Bayside", "Bayside-Billing"}
		got = _details(contacts=["Cam Shared-Bayside"], addresses=["Bayside-Billing"])
		self.assertNotEqual(got["defaults"]["contact"], "Ann New-Bayside")
		self.assertNotEqual(got["defaults"]["billing"], "Bayside-Billing")
		self.assertEqual(got["linked"], {"Contact": [], "Address": []})

	def test_every_read_is_get_list(self):
		_details(contacts=["Cam Shared-Bayside"], addresses=["Bayside-Billing"])
		self.assertTrue(STATE["get_list"])
		self.assertTrue(all(call["doctype"] in ("Contact", "Address") for call in STATE["get_list"]))
		code = _text(PARTY_PY)
		self.assertNotIn("frappe.db.sql", code)
		self.assertNotIn("get_all(", code, "get_all skips the caller's permissions")

	def test_read_on_the_doctype_and_the_account_is_required(self):
		_details()
		self.assertIn(("Opportunity", "read", None), STATE["perm_checks"])
		self.assertIn(("Customer", "read", "Bayside"), STATE["perm_checks"])
		STATE["denied"] = {("Customer", "read", "Bayside")}
		with self.assertRaises(PermissionError_):
			_details()
		STATE["denied"] = {("Opportunity", "read")}
		with self.assertRaises(PermissionError_):
			_details()

	def test_only_a_configured_doctype_and_an_allowed_account_kind(self):
		with self.assertRaises(PermissionError_):
			_details(doctype="Sales Order")
		with self.assertRaises(PermissionError_):
			_details(party_type="Supplier")
		with self.assertRaises(PermissionError_):
			_details(party_type="User", party_name="Administrator")
		self.assertEqual(STATE["get_list"], [], "nothing is read before the checks pass")

	def test_a_missing_account_is_refused(self):
		with self.assertRaises(ValidationError_):
			_details(party_name="  ")

	def test_names_are_a_short_json_list(self):
		names = [f"Person {i}" for i in range(50)]
		_details(contacts=names)
		asked = next(c for c in STATE["get_list"] if any(f[0] == "name" for f in c["filters"] if len(f) == 3))
		self.assertEqual(
			len(next(f for f in asked["filters"] if len(f) == 3 and f[0] == "name")[2]), party.MAX_NAMES
		)
		with self.assertRaises(ValidationError_):
			api.party_details("Opportunity", "Customer", "Bayside", "not json", "[]")
		with self.assertRaises(ValidationError_):
			api.party_details("Opportunity", "Customer", "Bayside", '{"a": 1}', "[]")
		# Non-text entries and blanks are dropped, never queried.
		self.assertEqual(party._names(["", None, 3, "Ann", "Ann"]), ["Ann"])


# ---------------------------------------------------------------------------------- the wiring


class TestWiring(unittest.TestCase):
	def test_the_editor_runs_one_party_entry_in_both_shells(self):
		editor = _code(EDITOR_JS)
		self.assertIn('import { PartyEntry } from "./party.js";', editor)
		self.assertIn("this.party_ui = new PartyEntry(this)", editor)
		# Reset on every load, drawn with every step, told of every change the person makes.
		take_flow = editor[editor.index("\ttake_flow(flow) {") :]
		self.assertIn("this.party_entry().reset();", take_flow[: take_flow.index("\n\t}\n")])
		render = editor[editor.index("\trender_fields(step, $fields, editable) {") :]
		self.assertIn("this.party_entry().decorate(step, editable);", render[: render.index("\n\t}\n")])
		changed = editor[editor.index("\tfield_changed(fieldname) {") :]
		self.assertIn(
			"this.party_entry().changed(fieldname, before, value);", changed[: changed.index("\n\t}\n")]
		)
		# The side panel draws with the same editor, and adds no party code of its own.
		panel = _code(FORM_KIT_JS / "ui" / "side_panel.js")
		self.assertIn("class SidePanel extends RecordEditor", panel)
		self.assertNotIn("PartyEntry", panel)

	def test_its_one_request_is_sf_kits_quiet_get(self):
		code = _code(PARTY_UI)
		for forbidden in ("frappe.call(", "frappe.xcall(", "fetch(", "$.ajax("):
			self.assertNotIn(forbidden, code)
		self.assertRegex(code, r'API \+ "party_details",[\s\S]*?\{ quiet: true, method: "GET" \}')
		self.assertEqual(code.count("API + "), 1)

	def test_values_are_never_written_as_html(self):
		code = _code(PARTY_UI)
		self.assertNotIn(".html(", code)
		self.assertNotIn("innerHTML", code)
		self.assertNotIn("insertAdjacentHTML", code)

	def test_new_records_are_linked_and_never_route_away(self):
		code = _code(PARTY_UI)
		# Contacts and Addresses through the app's dialogs, with the record as context.
		self.assertIn("ux.create_for(kind.doctype, record, {", code)
		self.assertIn('prefer_party: kind.role === "billing"', code)
		self.assertIn('address_type: kind.role === "site" ? "Shipping" : null', code)
		# Frappe's own "Create a new ..." takes the same path.
		self.assertIn("control.new_doc = () => {", code)
		# An account: the Customer dialog, which never opens the full form on a refusal.
		self.assertIn("dialog.skip_redirect_on_error = true", code)
		self.assertIn("frappe._from_link = null", code)
		# A Lead or Prospect opens its own form in a new tab, never over the page.
		self.assertIn('window.open(href, "_blank", "noopener")', code)
		self.assertNotIn("frappe.set_route", code)
		self.assertNotIn("frappe.new_doc(", code)
		self.assertIsNone(re.search(r"""['"`(]/app/""", code))

	def test_back_closes_the_dialog_through_the_planner_kits_guard(self):
		code = _code(PARTY_UI)
		self.assertIn('frappe.require("planner_kit.bundle.js"', code)
		self.assertIn("guard.open(overlay);", code)
		self.assertIn('dialog.$wrapper.one("hidden.bs.modal", () => guard.close(overlay, "user"));', code)
		self.assertNotIn(
			"pushState", code, "the guard owns history; the component never pushes its own entry"
		)
		self.assertNotIn('addEventListener("popstate"', code)

	def test_no_settings_single_is_read(self):
		code = _code(PARTY_UI)
		self.assertNotIn("get_single_value", code)
		self.assertNotIn("Settings", code)

	def test_styles_live_in_the_bundle_stylesheet_with_tokens_only(self):
		css = _text(CSS)
		start = css.index("/* ---- The Party entry component")
		block = re.sub(r"/\*[\s\S]*?\*/", "", css[start : css.index("/* ---- Conflict ---- */")])
		for selector in (".fk-party-tools", ".fk-party-new", ".fk-party-flag", ".fk-party-use"):
			self.assertIn(selector, block)
		self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", block))
		self.assertNotIn("rgb(", block)
		self.assertIn("min-height: var(--sf-touch-min);", block)
		self.assertNotIn("fk-party", _text(PLANNER_STYLES), "never in planner_kit's injected styles")
		code = _code(PARTY_UI)
		self.assertNotIn('createElement("style")', code)
		self.assertNotIn("<style", code)

	def test_the_readme_records_the_decisions(self):
		readme = _text(README)
		self.assertIn("## The Party entry component (P3.2)", readme)
		self.assertIn("party_details", readme)


if __name__ == "__main__":
	unittest.main()

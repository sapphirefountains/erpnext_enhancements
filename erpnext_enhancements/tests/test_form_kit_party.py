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
	"erpnext_enhancements.sync_contact",
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
const plan = (values, editable, d) =>
	party.plan_account_change({ roles: opp, values, details: d === undefined ? details : d, editable });
const STORED = { primary_contact: "Old Person-Harbor Plaza", customer_address: "Harbor Plaza-Billing", primary_address: "Harbor Plaza-Site" };
out.plan = {
	// Empty fields take the new account's defaults.
	empty_fills: plan({ primary_contact: "", customer_address: null, primary_address: "" }),
	// Stored values that are not the new account's: all kept, all flagged with the suggestion.
	stored_kept: plan(STORED),
	// Already the new account's (or the record's own, which the server reports as linked): stays, no flag.
	linked_stays: plan({ primary_contact: "Cam Shared-Bayside", customer_address: "Bayside-Billing", primary_address: "Bayside-Shipping" }, null, {
		defaults: details.defaults,
		linked: { Contact: ["Cam Shared-Bayside"], Address: ["Bayside-Billing", "Bayside-Shipping"] },
	}),
	// A general contractor's site, linked to the deal only: the server counts it linked, so it stays quietly.
	gc_site: plan({ primary_address: "GC Yard-Shipping" }, null, {
		defaults: details.defaults,
		linked: { Contact: [], Address: ["GC Yard-Shipping"] },
	}),
	// The new account has one address (it is both its billing address and the suggested site): a
	// stored site is still kept, only flagged.
	one_address: plan({ primary_address: "GC Yard-Shipping", customer_address: "" }, null, {
		defaults: { contact: null, billing: "Solo-Billing", site: "Solo-Billing" },
		linked: { Contact: [], Address: [] },
	}),
	// The new account has many addresses and no shipping flag (no suggested site): never cleared to null.
	many_addresses: plan({ primary_address: "GC Yard-Shipping" }, null, {
		defaults: { contact: null, billing: "Many-Billing", site: null },
		linked: { Contact: [], Address: [] },
	}),
	// No defaults at all: nothing is written, a stored value is kept.
	no_defaults: plan({ primary_contact: "Old Person-Harbor Plaza", customer_address: "" }, null, { defaults: {}, linked: {} }),
	// A field the person may not change is never touched.
	not_editable: plan({ primary_contact: "", customer_address: "" }, ["customer_address", "primary_address"]),
	no_details: plan({ primary_contact: "" }, null, null),
};
// v1.677.1: make_quick_entry resolves with the QuickEntryForm; its Dialog is .dialog.
const wrapper = { one() {} };
const dlg = { $wrapper: wrapper, display: true };
out.dialog_of = {
	quick_entry_form: party.dialog_of({ doctype: "Contact", dialog: dlg }) === dlg,
	bare_dialog: party.dialog_of(dlg) === dlg,
	full_form_instead: party.dialog_of({ doctype: "Contact" }),
	nothing: party.dialog_of(undefined),
	not_object: party.dialog_of("x"),
};
out.same_args = {
	same: party.same_details_args({ contacts: ["A"], addresses: ["B"] }, { contacts: ["A"], addresses: ["B"] }),
	changed: party.same_details_args({ contacts: ["A"], addresses: ["B"] }, { contacts: ["C"], addresses: ["B"] }),
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

	def test_the_dialog_is_unwrapped_from_the_quick_entry_form(self):
		# v16 QuickEntryForm.setup() resolves the QuickEntryForm, not its Dialog. Taking it as the
		# dialog skipped the history guard on prod, so Back closed the dialog and left the step.
		d = self.out["dialog_of"]
		self.assertTrue(d["quick_entry_form"])
		self.assertTrue(d["bare_dialog"])
		self.assertIsNone(d["full_form_instead"])
		self.assertIsNone(d["nothing"])
		self.assertIsNone(d["not_object"])

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

	def test_empty_fields_take_the_new_accounts_defaults(self):
		p = self.out["plan"]["empty_fills"]
		self.assertEqual(
			p["set"],
			{
				"primary_contact": "Ann New-Bayside",
				"customer_address": "Bayside-Billing",
				"primary_address": "Bayside-Shipping",
			},
		)
		self.assertEqual(p["kept"], [])

	def test_a_stored_value_is_never_overwritten_only_flagged(self):
		"""Review of #1291: a stored site nobody touched this visit was replaced or cleared."""
		p = self.out["plan"]["stored_kept"]
		self.assertEqual(p["set"], {})
		self.assertEqual(
			p["kept"],
			[
				{"fieldname": "primary_contact", "role": "contact", "suggestion": "Ann New-Bayside"},
				{"fieldname": "customer_address", "role": "billing", "suggestion": "Bayside-Billing"},
				{"fieldname": "primary_address", "role": "site", "suggestion": "Bayside-Shipping"},
			],
		)

	def test_values_of_the_new_account_or_the_record_stay_quietly(self):
		self.assertEqual(self.out["plan"]["linked_stays"], {"set": {}, "kept": []})
		gc = self.out["plan"]["gc_site"]
		self.assertEqual(gc["kept"], [], "a site linked to the deal itself was flagged as not belonging")
		self.assertNotIn("primary_address", gc["set"])
		# The empty contact and billing address still take the new account's.
		self.assertEqual(
			gc["set"], {"primary_contact": "Ann New-Bayside", "customer_address": "Bayside-Billing"}
		)

	def test_a_stored_site_survives_an_account_with_one_address(self):
		p = self.out["plan"]["one_address"]
		self.assertNotIn(
			"primary_address", p["set"], "the general contractor's site became the account's office"
		)
		self.assertEqual(p["set"], {"customer_address": "Solo-Billing"})
		self.assertEqual(
			p["kept"], [{"fieldname": "primary_address", "role": "site", "suggestion": "Solo-Billing"}]
		)

	def test_a_stored_site_survives_an_account_with_many_addresses(self):
		p = self.out["plan"]["many_addresses"]
		self.assertNotIn("primary_address", p["set"], "the site was cleared to null")
		self.assertEqual(p["kept"], [{"fieldname": "primary_address", "role": "site", "suggestion": None}])

	def test_null_is_never_written(self):
		for case, p in self.out["plan"].items():
			with self.subTest(case=case):
				self.assertNotIn(None, p["set"].values())
		self.assertEqual(self.out["plan"]["no_defaults"]["set"], {})
		self.assertEqual(
			self.out["plan"]["no_defaults"]["kept"],
			[{"fieldname": "primary_contact", "role": "contact", "suggestion": None}],
		)

	def test_only_editable_fields_are_touched(self):
		self.assertNotIn("primary_contact", self.out["plan"]["not_editable"]["set"])
		self.assertIn("customer_address", self.out["plan"]["not_editable"]["set"])
		self.assertEqual(self.out["plan"]["no_details"], {"set": {}, "kept": []})

	def test_an_answer_is_for_the_values_that_were_sent(self):
		self.assertEqual(self.out["same_args"], {"same": True, "changed": False})


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
	doctype,
	filters=None,
	or_filters=None,
	fields=None,
	order_by=None,
	limit_page_length=None,
	distinct=False,
	**kwargs,
):
	STATE["get_list"].append(
		{
			"doctype": doctype,
			"filters": filters,
			"or_filters": or_filters,
			"order_by": order_by,
			"distinct": distinct,
			"limit": limit_page_length,
		}
	)
	for field in fields or []:
		assert "(" not in field, "Frappe 16 refuses SQL functions as strings in get_list"
	rows = [r for r in STATE["records"].get(doctype, []) if r["name"] not in STATE["unreadable"]]
	# Dynamic Link conditions name ONE link row: both halves must be on the same row. As in SQL, the
	# join returns a record once per matching link row unless the query is DISTINCT.
	link_conds = [c for c in filters or [] if len(c) == 4 and c[0] == "Dynamic Link"]
	other = [c for c in filters or [] if not (len(c) == 4 and c[0] == "Dynamic Link")]
	if link_conds:
		want = {c[1]: c[3] for c in link_conds}
		rows = [r for r in rows for link in r["links"] if all(link.get(k) == v for k, v in want.items())]
		if distinct:
			unique = []
			for r in rows:
				if r not in unique:
					unique.append(r)
			rows = unique
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

		def exists(self, doctype, name):
			return any(r["name"] == name for r in STATE["records"].get(doctype, []))

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
	party_type="Customer",
	party_name="Bayside",
	contacts=None,
	addresses=None,
	doctype="Opportunity",
	name=None,
):
	return api.party_details(
		doctype,
		party_type,
		party_name,
		json.dumps(contacts or []),
		json.dumps(addresses or []),
		name,
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

	def test_duplicate_link_rows_never_push_a_linked_record_out(self):
		"""Review of #1291: the Dynamic Link join returns a record once per link row; the Zoho import
		left records linked twice to one party. Without DISTINCT and with limit=len(names), the
		duplicate took the place of the second name."""
		contacts = STATE["records"]["Contact"]
		ann = next(r for r in contacts if r["name"] == "Ann New-Bayside")
		ann["links"] = [_link("Customer", "Bayside"), _link("Customer", "Bayside")]
		got = _details(contacts=["Ann New-Bayside", "Cam Shared-Bayside"])
		self.assertEqual(got["linked"]["Contact"], ["Ann New-Bayside", "Cam Shared-Bayside"])
		asked = [c for c in STATE["get_list"] if any(len(f) == 3 and f[0] == "name" for f in c["filters"])]
		self.assertTrue(asked and all(c["distinct"] for c in asked))
		self.assertTrue(all(c["limit"] >= party.MAX_NAMES for c in asked))

	def test_duplicate_link_rows_do_not_hide_the_one_address_site(self):
		addresses = STATE["records"]["Address"]
		harbor = next(r for r in addresses if r["name"] == "Harbor Plaza-Billing")
		harbor["links"] = [_link("Customer", "Harbor Plaza")] * 3
		self.assertEqual(_details(party_name="Harbor Plaza")["defaults"]["site"], "Harbor Plaza-Billing")

	def test_an_address_linked_to_the_record_itself_counts_as_linked(self):
		"""A general contractor's job site is linked to the Opportunity only (the 2026-10-06 rule)."""
		STATE["records"]["Address"].append(
			{
				"name": "GC Yard-Shipping",
				"links": [_link("Opportunity", "CRM-OPP-0001")],
				"address_type": "Shipping",
				"disabled": 0,
				"modified": 7,
			}
		)
		got = _details(addresses=["GC Yard-Shipping"], name="CRM-OPP-0001")
		self.assertEqual(got["linked"]["Address"], ["GC Yard-Shipping"])
		self.assertIn(("Opportunity", "read", "CRM-OPP-0001"), STATE["perm_checks"])
		# Without the record, it is not the account's.
		self.assertEqual(_details(addresses=["GC Yard-Shipping"])["linked"]["Address"], [])
		# The record must be readable.
		STATE["denied"] = {("Opportunity", "read", "CRM-OPP-0001")}
		with self.assertRaises(PermissionError_):
			_details(addresses=["GC Yard-Shipping"], name="CRM-OPP-0001")


# ------------------------------------------------------------ map_fields and the job site (v1.677.0)


class _Field:
	def __init__(self, fieldtype):
		self.fieldtype = fieldtype


class _Meta:
	def get_field(self, fieldname):
		return _Field("Link") if fieldname == "primary_address" else None


class _Opportunity(dict):
	meta = _Meta()

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class TestMapFieldsCannotStoreAddressHtml(unittest.TestCase):
	"""ERPNext's Opportunity.validate runs map_fields, which copies every EMPTY column from the party
	by name; Customer.primary_address is ERPNext's rendered address (a Text Editor of HTML). v16 runs
	_validate_links before before_validate and validate (frappe/model/document.py at version-16), so
	the HTML landed in Opportunity.primary_address, a Link, on every save of a deal with no site.
	sanitize_primary_address_link now runs on validate too, after ERPNext's own."""

	def setUp(self):
		_reset()

	def test_it_runs_on_validate_after_erpnexts_map_fields(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		block = hooks[hooks.index('\t"Opportunity": {') :]
		block = block[: block.index('"before_save"')]
		validate = block[block.index('"validate": [') :]
		first = re.search(r'^\s*"([\w.]+)",', validate, re.M).group(1)
		self.assertEqual(first, "erpnext_enhancements.sync_contact.sanitize_primary_address_link")
		self.assertIn('"before_validate": [', block)

	def test_html_from_map_fields_is_cleared_and_an_address_name_is_kept(self):
		sync_contact = importlib.import_module("erpnext_enhancements.sync_contact")
		doc = _Opportunity(primary_address="2600 Example Blvd<br>Springfield")
		sync_contact.sanitize_primary_address_link(doc)
		self.assertIsNone(doc.primary_address)
		doc = _Opportunity(primary_address="Bayside-Billing")
		sync_contact.sanitize_primary_address_link(doc)
		self.assertEqual(doc.primary_address, "Bayside-Billing")


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

	def test_an_answer_for_values_since_changed_is_asked_again(self):
		"""Review of #1291: a stale `linked` answer could flag a contact picked while it was in flight."""
		code = _code(PARTY_UI)
		body = code[code.index("\taccount_changed(value, retries) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertIn("if (!same_details_args(current, details_args(this.roles, this.ed.values))) {", body)
		self.assertIn("this.account_changed(value, (retries || 0) + 1);", body)
		self.assertLess(body.index("same_details_args"), body.index("this.apply(party, details || {});"))
		# The record goes along, so its own job site counts as linked.
		self.assertRegex(body, r"API \+ \"party_details\",\s*\{\s*doctype,\s*(//[^\n]*\n\s*)*name,")

	def test_new_is_let_go_even_if_the_dialog_never_opens(self):
		"""Review of #1291: with_doctype never calls back on a failed request, so the promise may never
		settle; "+ New" must not stay held."""
		code = _code(PARTY_UI)
		body = code[code.index("\topen_dialog(open) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertIn("const timer = setTimeout(release, OPEN_WAIT_MS);", body)
		self.assertIn("clearTimeout(timer);", body)
		self.assertIn("const OPEN_WAIT_MS = ", code)

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
		# What make_quick_entry resolves with is the QuickEntryForm, unwrapped to its Dialog (v1.677.1).
		self.assertIn("track(entry, guard) {", code)
		self.assertIn("const dialog = dialog_of(entry);", code)
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

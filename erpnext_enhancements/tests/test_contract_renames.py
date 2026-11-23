"""Bench-free: the v1.783.0 contract changes (Nikolas, 2026-11-03).

"Add Contract button to Scope to view contract. If no contract is listed, then it asks and opens
the Contract Builder. Remove the contract tab in Project DocType and just make that button the new
primary way to view and create contracts. Change Architect Agreement to Design Agreement. Rental
Agreement change to Events Agreement. For now hide Employee Contractor Agreement and Mutual
Non-disclosure agreements. Down Payment Percentage as the name in the Budget tab instead of First
Invoice Percentage."

What this pins, and what each case would otherwise let through:

* ``patches/rename_agreement_templates``: renames the two titles and the live bodies' own
  wording, hides the two templates, changes nothing on a second run, leaves a body whose wording
  has diverged alone (title only), and **never raises** — a patch that raises aborts
  ``bench migrate``, which on this repo is the deploy.
* ``patches/remove_contracts_tabs``: deletes exactly the four Custom Fields, skips the ones
  already gone, and goes on past one that will not delete.
* ``seed_contract_templates`` and the two template files agree with the patch, so a fresh site
  reads as a migrated one.
* ``contracts_tab.js`` is gone and ``contract_button.js`` is loaded on Project and Customer; the
  button goes to the Builder's review step, asks before creating, and the Builder's start screen
  takes the record it was sent.
* The Generate Contract dialog reads the enabled templates from the server and offers no hidden
  one from its static list; "Events Agreement" and "Down Payment Percentage" are what the user
  reads in the places the brief names.

Stubs a minimal ``frappe`` in ``setUpModule`` and restores what was there in ``tearDownModule``.

Run: python -m unittest erpnext_enhancements.tests.test_contract_renames
"""

import ast
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

TEMPLATES = APP / "templates" / "contracts"
PATCH_MODULES = (
	"erpnext_enhancements.patches.rename_agreement_templates",
	"erpnext_enhancements.patches.update_maintenance_agreement_terms",
	"erpnext_enhancements.patches.remove_contracts_tabs",
	"erpnext_enhancements.patches.seed_contract_templates",
)

_saved = {}


class _Doc(dict):
	def __getattr__(self, key):
		return self.get(key)


class Site:
	"""The fake site the stub reads and writes. Reset before each test."""

	def reset(self):
		self.templates = {}
		self.custom_fields = set()
		self.writes = []
		self.deleted = []
		self.cleared = []
		self.rollbacks = []
		self.savepoints = []
		self.errors = []
		self.fail_get_doc = set()
		self.fail_delete = set()
		self.signed = {}
		self.frozen = []
		self.events = []
		self.fail_freeze = set()


SITE = Site()


def _install_stub():
	frappe = types.ModuleType("frappe")

	def exists(doctype, name):
		if doctype == "Contract Template":
			return name in SITE.templates
		if doctype == "Custom Field":
			return name in SITE.custom_fields
		return False

	class _Contract:
		def __init__(self, name):
			self.name = name

		def freeze_executed_copy(self):
			if self.name in SITE.fail_freeze:
				raise RuntimeError(f"cannot freeze {self.name}")
			SITE.frozen.append(self.name)

	def get_doc(doctype, name):
		if doctype == "Project Contract":
			return _Contract(name)
		if name in SITE.fail_get_doc:
			raise RuntimeError(f"boom {name}")
		return _Doc(dict(SITE.templates[name], name=name))

	def get_all(doctype, filters=None, pluck=None, **kwargs):
		# freeze_contract_copies: every Signed, submitted contract of the template's kind.
		if doctype == "Project Contract" and (filters or {}).get("status") == "Signed":
			SITE.events.append(("freeze", filters["template_key"]))
			return list(SITE.signed.get(filters["template_key"], []))
		return []

	def set_value(doctype, name, fieldname, value, update_modified=True):
		SITE.writes.append((doctype, name, fieldname, value, update_modified))
		SITE.events.append(("write", name, fieldname))
		SITE.templates[name][fieldname] = value

	def delete_doc(doctype, name, **kwargs):
		if name in SITE.fail_delete:
			raise RuntimeError(f"cannot delete {name}")
		SITE.deleted.append((doctype, name, kwargs))
		SITE.custom_fields.discard(name)

	frappe.db = types.SimpleNamespace(
		exists=exists,
		set_value=set_value,
		savepoint=lambda name: SITE.savepoints.append(name),
		rollback=lambda save_point=None: SITE.rollbacks.append(save_point),
	)
	frappe.get_doc = get_doc
	frappe.get_all = get_all
	frappe.delete_doc = delete_doc
	frappe.clear_cache = lambda doctype=None: SITE.cleared.append(doctype)
	frappe.log_error = lambda title=None, message=None, **k: SITE.errors.append(title)
	frappe.get_traceback = lambda: "traceback"
	frappe.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))
	return frappe


def setUpModule():
	_saved["frappe"] = sys.modules.get("frappe")
	sys.modules["frappe"] = _install_stub()
	for name in PATCH_MODULES:
		sys.modules.pop(name, None)


def tearDownModule():
	for name in PATCH_MODULES:
		sys.modules.pop(name, None)
	if _saved.get("frappe") is None:
		sys.modules.pop("frappe", None)
	else:
		sys.modules["frappe"] = _saved["frappe"]


def _module(name):
	return importlib.import_module(name)


def _read(*parts):
	return APP.joinpath(*parts).read_text(encoding="utf-8")


def _code_only(js):
	"""JS with its comments removed, so an assertion is about what runs, not what is said."""
	js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
	return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def _hooks(name):
	tree = ast.parse(_read("hooks.py"))
	return next(
		ast.literal_eval(node.value)
		for node in tree.body
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets)
	)


def _old_body(template_key):
	"""The repo template with the pre-v1.783.0 wording put back: what a migrated site holds."""
	patch = _module("erpnext_enhancements.patches.rename_agreement_templates")
	seed = _module("erpnext_enhancements.patches.seed_contract_templates")
	spec = next(s for s in seed.TEMPLATES if s["template_key"] == template_key)
	body = (TEMPLATES / spec["file"]).read_text(encoding="utf-8")
	for old, new in patch.RENAMES[template_key][1]:
		assert new in body, (template_key, new)
		body = body.replace(new, old)
	return body


class TestRenamePatch(unittest.TestCase):
	def setUp(self):
		SITE.reset()
		SITE.templates = {
			"architect": {"title": "Architect Agreement", "body": _old_body("architect"), "enabled": 1},
			"rental": {"title": "Rental Agreement", "body": _old_body("rental"), "enabled": 1},
			"nda": {"title": "Mutual Non-Disclosure Agreement", "body": "<p>nda</p>", "enabled": 1},
			"employee_contractor": {
				"title": "Employee-Contractor Agreement",
				"body": "<p>ec</p>",
				"enabled": 1,
			},
			"owner": {"title": "Owner Contract", "body": "<p>owner</p>", "enabled": 1},
		}
		self.patch = _module("erpnext_enhancements.patches.rename_agreement_templates")

	def test_titles_bodies_and_enabled_after_one_run(self):
		self.patch.execute()
		self.assertEqual(SITE.templates["architect"]["title"], "Design Agreement")
		self.assertEqual(SITE.templates["rental"]["title"], "Events Agreement")
		for key, file in (("architect", "architect_agreement.html"), ("rental", "rental_agreement.html")):
			with self.subTest(key=key):
				self.assertEqual(SITE.templates[key]["body"], (TEMPLATES / file).read_text(encoding="utf-8"))
		self.assertEqual(SITE.templates["nda"]["enabled"], 0)
		self.assertEqual(SITE.templates["employee_contractor"]["enabled"], 0)
		# The Owner Contract and every key are untouched: code keys on template_key, not the title.
		self.assertEqual(
			SITE.templates["owner"], {"title": "Owner Contract", "body": "<p>owner</p>", "enabled": 1}
		)
		self.assertEqual({w[1] for w in SITE.writes}, {"architect", "rental", "nda", "employee_contractor"})
		self.assertTrue(
			all(w[4] is False for w in SITE.writes), "update_modified=False, as the layout patches write"
		)
		self.assertEqual(SITE.errors, [])
		self.assertEqual(SITE.rollbacks, [])

	def test_a_second_run_changes_nothing(self):
		self.patch.execute()
		SITE.writes.clear()
		self.patch.execute()
		self.assertEqual(SITE.writes, [])
		self.assertEqual(SITE.errors, [])

	def test_a_diverged_body_keeps_its_wording_and_still_gets_the_title(self):
		SITE.templates["rental"]["body"] = "<h3><b>RENTAL AGREEMENT</b></h3><p>edited on the site</p>"
		self.patch.execute()
		self.assertEqual(SITE.templates["rental"]["title"], "Events Agreement")
		# The heading is one of the exact strings and still changes; the rest of the body is the site's own.
		self.assertEqual(
			SITE.templates["rental"]["body"], "<h3><b>EVENTS AGREEMENT</b></h3><p>edited on the site</p>"
		)
		self.assertEqual(SITE.errors, [])

	def test_a_body_edited_past_recognition_is_logged_not_rewritten(self):
		SITE.templates["architect"]["body"] = "<p>rewritten entirely</p>"
		self.patch.execute()
		self.assertEqual(SITE.templates["architect"]["title"], "Design Agreement")
		self.assertEqual(SITE.templates["architect"]["body"], "<p>rewritten entirely</p>")
		self.assertEqual(SITE.errors, ["rename_agreement_templates: architect body not renamed"])
		# Logged once: the second run changes no title, so it says nothing.
		self.patch.execute()
		self.assertEqual(len(SITE.errors), 1)

	def test_paper_signed_contracts_are_frozen_before_the_body_changes(self):
		SITE.signed = {"rental": ["SF-RA-2026-0001", "SF-RA-2026-0002"], "architect": ["SF-ARCH-2026-0001"]}
		self.patch.execute()
		# Every Signed contract of a type whose body is rewritten, frozen before the body write.
		self.assertEqual(sorted(SITE.frozen), ["SF-ARCH-2026-0001", "SF-RA-2026-0001", "SF-RA-2026-0002"])
		for key in ("rental", "architect"):
			self.assertLess(
				SITE.events.index(("freeze", key)), SITE.events.index(("write", key, "body")), key
			)
		# A title-only change (the body already reads the new words) freezes nothing.
		SITE.frozen.clear()
		SITE.templates["rental"]["title"] = "Rental Agreement"
		self.patch.execute()
		self.assertEqual(SITE.frozen, [])

	def test_a_freeze_that_fails_is_logged_and_the_rename_still_lands(self):
		SITE.signed = {"rental": ["SF-RA-2026-0001"]}
		SITE.fail_freeze = {"SF-RA-2026-0001"}
		self.patch.execute()
		self.assertEqual(SITE.errors, ["Contract terms patch: could not freeze SF-RA-2026-0001"])
		self.assertEqual(SITE.templates["rental"]["title"], "Events Agreement")

	def test_a_missing_template_is_skipped(self):
		del SITE.templates["nda"]
		self.patch.execute()
		self.assertNotIn("nda", {w[1] for w in SITE.writes})
		self.assertEqual(SITE.errors, [])

	def test_one_failure_is_logged_rolled_back_and_the_rest_still_change(self):
		SITE.fail_get_doc = {"rental"}
		self.patch.execute()  # must not raise
		self.assertEqual(SITE.errors, ["rename_agreement_templates: rental not updated"])
		self.assertEqual(SITE.rollbacks, ["rename_agreement_templates_rental"])
		self.assertEqual(SITE.templates["architect"]["title"], "Design Agreement")
		self.assertEqual(SITE.templates["nda"]["enabled"], 0)
		self.assertEqual(SITE.templates["rental"]["title"], "Rental Agreement")


class TestRemoveContractsTabs(unittest.TestCase):
	def setUp(self):
		SITE.reset()
		self.patch = _module("erpnext_enhancements.patches.remove_contracts_tabs")

	def test_the_four_fields_and_only_those(self):
		self.assertEqual(
			set(self.patch.FIELD_NAMES),
			{
				"Project-custom_contracts_tab",
				"Project-custom_contracts_html",
				"Customer-custom_contracts_tab",
				"Customer-custom_contracts_html",
			},
		)
		# Never in the fixtures, so the delete is the only way they leave a site.
		self.assertNotIn("custom_contracts", _read("fixtures", "custom_field.json"))

	def test_deletes_what_exists_and_clears_only_those_doctypes(self):
		SITE.custom_fields = {"Project-custom_contracts_tab", "Project-custom_contracts_html"}
		self.patch.execute()
		self.assertEqual(
			[(d, n) for d, n, _k in SITE.deleted],
			[
				("Custom Field", "Project-custom_contracts_tab"),
				("Custom Field", "Project-custom_contracts_html"),
			],
		)
		self.assertTrue(all(k == {"ignore_permissions": True, "force": True} for _d, _n, k in SITE.deleted))
		self.assertEqual(SITE.cleared, ["Project"])
		self.assertEqual(SITE.errors, [])

	def test_nothing_to_do_is_nothing_done(self):
		self.patch.execute()
		self.assertEqual(SITE.deleted, [])
		self.assertEqual(SITE.cleared, [])

	def test_one_refusal_is_logged_and_the_rest_still_go(self):
		SITE.custom_fields = set(self.patch.FIELD_NAMES)
		SITE.fail_delete = {"Project-custom_contracts_html"}
		self.patch.execute()  # must not raise
		self.assertEqual(SITE.errors, ["remove_contracts_tabs: Project-custom_contracts_html not deleted"])
		self.assertEqual(len(SITE.deleted), 3)
		self.assertEqual(sorted(SITE.cleared), ["Customer", "Project"])


class TestSeedAndTemplates(unittest.TestCase):
	def test_seed_titles_and_hidden_templates(self):
		seed = _module("erpnext_enhancements.patches.seed_contract_templates")
		by_key = {s["template_key"]: s for s in seed.TEMPLATES}
		self.assertEqual(by_key["architect"]["title"], "Design Agreement")
		self.assertEqual(by_key["rental"]["title"], "Events Agreement")
		self.assertEqual(by_key["nda"].get("enabled"), 0)
		self.assertEqual(by_key["employee_contractor"].get("enabled"), 0)
		for key in ("msa", "sow", "owner", "rental", "maintenance", "architect"):
			self.assertEqual(by_key[key].get("enabled", 1), 1, key)
		self.assertIn('doc.enabled = spec.get("enabled", 1)', _read("patches", "seed_contract_templates.py"))

	def test_the_patch_and_the_seed_name_the_same_titles(self):
		patch = _module("erpnext_enhancements.patches.rename_agreement_templates")
		seed = _module("erpnext_enhancements.patches.seed_contract_templates")
		by_key = {s["template_key"]: s for s in seed.TEMPLATES}
		for key, (title, _replacements) in patch.RENAMES.items():
			self.assertEqual(by_key[key]["title"], title, key)
		self.assertEqual(set(patch.DISABLE), {k for k, s in by_key.items() if s.get("enabled") == 0})

	def test_template_headings_and_defining_sentences(self):
		architect = (TEMPLATES / "architect_agreement.html").read_text(encoding="utf-8")
		rental = (TEMPLATES / "rental_agreement.html").read_text(encoding="utf-8")
		self.assertTrue(architect.startswith("<h3><b>DESIGN AGREEMENT</b></h3>"))
		self.assertTrue(rental.startswith("<h3><b>EVENTS AGREEMENT</b></h3>"))
		self.assertNotIn("Architect Agreement", architect)
		self.assertNotIn("Rental Agreement", rental)
		# The patch's new wording is exactly what the files say, so a migrated site equals a fresh one.
		patch = _module("erpnext_enhancements.patches.rename_agreement_templates")
		for key, text in (("architect", architect), ("rental", rental)):
			for old, new in patch.RENAMES[key][1]:
				self.assertIn(new, text, (key, new))
				self.assertNotIn(old, text, (key, old))

	def test_patches_txt_lists_both_after_what_they_follow(self):
		lines = [l.strip() for l in _read("patches.txt").splitlines() if l.strip() and not l.startswith("#")]
		index = {line: i for i, line in enumerate(lines)}
		remove = index["erpnext_enhancements.patches.remove_contracts_tabs"]
		rename = index["erpnext_enhancements.patches.rename_agreement_templates"]
		self.assertGreater(remove, index["erpnext_enhancements.patches.add_project_contracts_tab"])
		self.assertGreater(remove, index["erpnext_enhancements.patches.add_customer_contracts_tab"])
		self.assertGreater(rename, index["erpnext_enhancements.patches.seed_contract_templates"])
		self.assertGreater(rename, index["erpnext_enhancements.patches.refresh_general_agreement_layout"])
		self.assertGreater(
			rename, index["erpnext_enhancements.patches.refresh_rental_owner_agreement_layout"]
		)


class TestContractButton(unittest.TestCase):
	def test_the_tab_script_is_gone_and_the_button_is_loaded_on_both_forms(self):
		self.assertFalse((APP / "public" / "js" / "project_enhancements" / "contracts_tab.js").exists())
		form_js = _hooks("doctype_js")
		button = "public/js/project_enhancements/contract_button.js"
		for doctype in ("Project", "Customer"):
			with self.subTest(doctype=doctype):
				self.assertIn(button, form_js[doctype])
				self.assertFalse(any("contracts_tab" in entry for entry in form_js[doctype]))
		# Nothing else in hooks names the tab script.
		self.assertNotIn("contracts_tab.js", _read("hooks.py"))
		# The viewer stays where contracts are read: the two contract forms and the list.
		self.assertIn("public/js/project_enhancements/contract_viewer.js", form_js["Project Contract"])
		self.assertIn(
			"public/js/project_enhancements/contract_viewer.js", form_js["Sapphire Maintenance Contract"]
		)

	def test_what_a_press_does(self):
		code = _code_only(_read("public", "js", "project_enhancements", "contract_button.js"))
		# The list the old tab read, asked on the press.
		self.assertIn("get_contracts", code)
		self.assertIn("args: { source_doctype: frm.doctype, source_name: frm.doc.name }", code)
		# One: the Builder's review step. A maintenance contract: its form, read-only.
		self.assertIn('frappe.set_route("contract-builder", row.name, "review")', code)
		self.assertIn('frappe.set_route("Form", MAINTENANCE, row.name)', code)
		self.assertIn('const MAINTENANCE = "Sapphire Maintenance Contract"', code)
		self.assertRegex(code, r"if \(rows\.length === 1\) return open_row\(rows\[0\]\);")
		# Several: a picker. None: ask, then the start screen with this record filled in.
		self.assertIn("pick(frm, rows)", code)
		self.assertIn('__("No contract on this {0} yet. Create one?"', code)
		self.assertIn('frappe.set_route("contract-builder")', code)
		self.assertIn("{ customer: frm.doc.name } : { project: frm.doc.name }", code)
		self.assertIn('frappe.model.can_create("Project Contract")', code)
		# Project: the Scope tab's pane. Customer: the page head.
		self.assertIn('tab.df.fieldname === "custom_scope"', code)
		self.assertIn("tab.wrapper.prepend($bar)", code)
		self.assertIn('frm.add_custom_button(__("Contract"), () => on_press(frm))', code)
		# Routes only; never a window.open or a reload, so Back and Forward keep working.
		for own in ("window.open", "location.reload", "location.href"):
			self.assertNotIn(own, code)

	def test_the_contract_type_link_offers_enabled_templates_only(self):
		js = _code_only(_read("project_enhancements", "doctype", "project_contract", "project_contract.js"))
		self.assertIn('frm.set_query("contract_template", () => ({ filters: { enabled: 1 } }))', js)

	def test_the_builder_start_screen_takes_the_record_it_was_sent(self):
		js = _read("project_enhancements", "page", "contract_builder", "contract_builder.js")
		self.assertIn('.on("click", () => this.start_dialog(type, options))', js)
		self.assertIn("default: options.project", js)
		self.assertIn("default: options.customer || options.supplier", js)


class TestGenerateContractList(unittest.TestCase):
	def test_the_static_list_offers_no_hidden_template_and_the_new_names(self):
		code = _code_only(_read("public", "js", "global_enhancements", "record_actions.js"))
		block = code[
			code.index("export function contract_types(") : code.index("function enabled_contract_types(")
		]
		values = re.findall(r'value: "([a-z_]+)"', block)
		# v1.808.0: the Certificate of Warranty joins the customer list, for a Project only
		# (tests/test_warranty_certificate pins that condition).
		self.assertEqual(values, ["owner", "rental", "maintenance", "sow", "warranty", "msa", "sow"])
		self.assertIn('label: __("Events Agreement")', block)
		# No Design Agreement here: create_contract prefills the party from the project's customer,
		# and the other side of a Design Agreement is the architect (the Builder's start dialog asks).
		for gone in (
			"nda",
			"employee_contractor",
			"architect",
			"Rental Agreement",
			"Architect Agreement",
			"Non-Disclosure",
		):
			self.assertNotIn(gone, block, gone)

	def test_the_dialog_reads_the_enabled_templates_from_the_server(self):
		code = _code_only(_read("public", "js", "global_enhancements", "record_actions.js"))
		self.assertIn("get_contract_types", code)
		self.assertIn("types.filter((t) => t.value in live)", code)
		self.assertIn("return enabled_contract_types(source_doctype).then((types) =>", code)
		# The form button and the guided page still call the same opener.
		self.assertIn(
			"actions.open_generate_contract(source_doctype, frm.doc.name)",
			_read("public", "js", "contracts.js"),
		)

	def test_the_endpoint_lists_enabled_templates_and_feeds_the_start_screen(self):
		src = _read("project_enhancements", "contract_builder.py")
		tree = ast.parse(src)
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_contract_types")
		self.assertTrue(any(ast.unparse(d).startswith("frappe.whitelist") for d in fn.decorator_list))
		body = ast.unparse(fn)
		self.assertIn("filters={'enabled': 1}", body)
		self.assertIn("frappe.has_permission(DOCTYPE, 'create', throw=True)", body)
		start = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_builder_start")
		self.assertIn("types = get_contract_types()", ast.unparse(start))


class TestWording(unittest.TestCase):
	def test_events_agreement_on_the_booking_the_portal_and_the_public_page(self):
		js = _code_only(_read("asset_management", "doctype", "rental_booking", "rental_booking.js"))
		self.assertEqual(js.count('__("Events Agreement")'), 2)
		self.assertNotIn("Rental Agreement", js)
		meta = json.loads(_read("asset_management", "doctype", "rental_booking", "rental_booking.json"))
		field = next(f for f in meta["fields"] if f["fieldname"] == "rental_agreement")
		self.assertEqual(field["label"], "Events Agreement")
		page = _read("www", "rentals.html")
		self.assertIn('_("Events agreement signed on {0}.")', page)
		self.assertIn("Your events agreement is waiting for your signature", page)
		self.assertNotIn("ental agreement", page)
		for module in ("rental_sales.py", "rental_holds.py", "rental_portal.py"):
			with self.subTest(module=module):
				src = _read("asset_management", module)
				# Translated strings and log titles say Events; the one "Rental Agreement" left is the
				# module docstring's note of the old name.
				self.assertNotRegex(src, r'_\("[^"]*Rental Agreement')
				self.assertNotRegex(
					src, r'"[^"]*rental agreement[^"]*"', "a lower-case mention in a user string"
				)
				self.assertNotRegex(src, r'f"Rental Agreement')
		# Car rentals are another module and keep their words.
		self.assertNotIn("Events Agreement", _read("travel_management", "README.md"))

	def test_down_payment_percentage(self):
		fields = {f["name"]: f for f in json.loads(_read("fixtures", "custom_field.json"))}
		for name in (
			"Project-custom_first_invoice_percentage",
			"Opportunity-custom_first_invoice_percentage",
		):
			with self.subTest(name=name):
				self.assertEqual(fields[name]["label"], "Down Payment Percentage")
				self.assertEqual(fields[name]["fieldname"], "custom_first_invoice_percentage")
		steps = _code_only(_read("public", "js", "project_enhancements", "process_steps.js"))
		self.assertIn('__("Down Payment Percentage is not set on this project', steps)
		self.assertIn('__("Down payment: {0}% of {1} = {2}."', steps)
		self.assertNotIn("First Invoice", steps)
		self.assertNotIn("First invoice", steps)
		for name in (
			"Project-custom_first_invoice_percentage",
			"Opportunity-custom_first_invoice_percentage",
		):
			self.assertIn("down payment", fields[name]["description"], name)
		readme = _read("project_enhancements", "README.md")
		self.assertIn("names the **Down Payment Percentage**", readme)
		self.assertNotIn("names the **First Invoice Percentage**", readme)
		help_text = json.loads(_read("form_kit", "help_text", "opportunity.json"))
		hint = help_text["fields"]["custom_first_invoice_percentage"]["hint"]
		self.assertIn("down payment", hint)
		# The guided hint is the field's fixture description (test_form_kit holds the whole file to it).
		self.assertEqual(fields["Opportunity-custom_first_invoice_percentage"]["description"], hint)


if __name__ == "__main__":
	unittest.main()

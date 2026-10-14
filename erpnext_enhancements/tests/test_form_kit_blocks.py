"""Bench-free tests for the information-block registry (form kit P3.1, TASK-2026-02754).

``form_kit/blocks.py`` names, for each block (Party, Site, Schedule window, Scope by pillar, Money,
Crew), the fields that carry it on Opportunity and Project and its three faces. Done means: this
suite fails when a block is missing a face, or names a field the DocType's meta does not have.

The meta is built without a bench, from what a v16 site's meta is built from:

* ERPNext's own DocType JSON at ``origin/version-16`` (v16.50.0), trimmed and committed:
  ``tests/fixtures/form_kit/erpnext_v16_opportunity.json`` (made for ``test_form_kit``) and
  ``tests/fixtures/form_kit/erpnext_v16_project.json`` (made for this suite the same way; its
  ``_source`` key records the commit, the path and how it was produced). Read from a sibling
  ``erpnext`` checkout with ``git show origin/version-16:<path>``, never its working tree, which
  is ``develop`` (v17);
* this app's Custom Fields for the DocType (``fixtures/custom_field.json``);
* the fields ``setup/custom_fields.create_unified_tabs`` adds on every migrate (Primary Contact,
  Primary Address and their widgets), which no fixture holds; ``test_unified_tab_fields_are_real``
  holds the list here to that file.

Also held: every face resolves to something real (an ES module's export, a report-face kind in
``report_face/cells.js``, a print helper defined in this app and registered as a Jinja global, or
Frappe's own formatter), the Schedule window is ``utils/date_pairs.PAIRS`` rather than a copy, and
the JavaScript twin ``public/js/info_blocks/blocks.js`` equals the Python registry, under node.

Stubs ``frappe`` in ``setUpModule`` (``utils/date_pairs`` imports it) and restores it after.

Run: python -m unittest erpnext_enhancements.tests.test_form_kit_blocks -v
"""

import ast
import copy
import importlib
import json
import os
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
FIXTURES = APP / "tests" / "fixtures" / "form_kit"
ERPNEXT_FIXTURES = {
	"Opportunity": FIXTURES / "erpnext_v16_opportunity.json",
	"Project": FIXTURES / "erpnext_v16_project.json",
}
CUSTOM_FIELDS = APP / "fixtures" / "custom_field.json"
SETUP_FIELDS = APP / "setup" / "custom_fields.py"
HOOKS = APP / "hooks.py"
CELLS_JS = APP / "public" / "js" / "report_face" / "cells.js"
TWIN_JS = APP / "public" / "js" / "info_blocks" / "blocks.js"

#: What setup/custom_fields.create_unified_tabs adds to both Opportunity and Project.
UNIFIED_TAB_FIELDS = {
	"primary_contact": {"fieldtype": "Link", "options": "Contact"},
	"section_break_contacts": {"fieldtype": "Section Break"},
	"contact_list_html": {"fieldtype": "HTML"},
	"section_break_map": {"fieldtype": "Section Break"},
	"primary_address": {"fieldtype": "Link", "options": "Address"},
	"location_map_col_break": {"fieldtype": "Column Break"},
	"location_map_html": {"fieldtype": "HTML"},
	"section_break_address_list": {"fieldtype": "Section Break"},
	"address_list_html": {"fieldtype": "HTML"},
}

blocks = None
date_pairs = None
_saved = {}
_MODULES = ("frappe", "erpnext_enhancements.utils.popup_once", "erpnext_enhancements.utils.date_pairs")


def setUpModule():
	global blocks, date_pairs
	for name in (*_MODULES, "erpnext_enhancements.form_kit.blocks"):
		_saved[name] = sys.modules.get(name)
		sys.modules.pop(name, None)
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text, *args, **kwargs: text
	sys.modules["frappe"] = frappe
	blocks = importlib.import_module("erpnext_enhancements.form_kit.blocks")
	date_pairs = importlib.import_module("erpnext_enhancements.utils.date_pairs")


def tearDownModule():
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def build_metas():
	"""``{doctype: {fieldname: docfield}}``: ERPNext's v16 fields, the fixtures', setup's."""
	custom = json.loads(CUSTOM_FIELDS.read_text(encoding="utf-8"))
	metas = {}
	for doctype, path in ERPNEXT_FIXTURES.items():
		source = json.loads(path.read_text(encoding="utf-8"))["doctypes"][doctype]
		fields = {f["fieldname"]: dict(f) for f in source["fields"]}
		for row in custom:
			if row.get("dt") == doctype:
				fields.setdefault(row["fieldname"], dict(row))
		for fieldname, df in UNIFIED_TAB_FIELDS.items():
			fields.setdefault(fieldname, dict(df, fieldname=fieldname))
		metas[doctype] = fields
	return metas


def report_kinds():
	match = re.search(r"export const KINDS = \[([^\]]*)\]", CELLS_JS.read_text(encoding="utf-8"))
	return set(re.findall(r'"([a-z]+)"', match.group(1)))


def _top_level_names(path):
	tree = ast.parse(path.read_text(encoding="utf-8"))
	names = set()
	for node in tree.body:
		if isinstance(node, ast.FunctionDef | ast.ClassDef):
			names.add(node.name)
		elif isinstance(node, ast.Assign):
			names.update(t.id for t in node.targets if isinstance(t, ast.Name))
		elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
			names.add(node.target.id)
	return names


def _js_exports(path):
	text = path.read_text(encoding="utf-8")
	names = set(
		re.findall(r"^export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", text, re.M)
	)
	for group in re.findall(r"^export\s*\{([^}]*)\}", text, re.M):
		for part in group.split(","):
			part = part.strip()
			if part:
				names.add(part.split(" as ")[-1].strip())
	return names


class RegistryShapeTest(unittest.TestCase):
	def test_the_six_blocks(self):
		self.assertEqual(
			set(blocks.BLOCKS), {"party", "site", "schedule_window", "scope_by_pillar", "money", "crew"}
		)

	def test_the_registry_is_sound(self):
		self.assertEqual(blocks.check(report_kinds=report_kinds()), [])

	def test_every_block_declares_all_three_faces(self):
		for key, block in blocks.BLOCKS.items():
			for face in ("entry", "report", "print"):
				with self.subTest(block=key, face=face):
					self.assertTrue(block["faces"].get(face), f"{key} has no {face} face")

	def test_a_block_missing_any_face_fails(self):
		for face in ("entry", "report", "print"):
			with self.subTest(face=face):
				broken = copy.deepcopy(blocks.BLOCKS)
				broken["money"]["faces"].pop(face, None)
				self.assertIn(f"money.{face}: no {face} face declared", blocks.check(broken))
				broken["money"]["faces"][face] = {}
				self.assertIn(f"money.{face}: no {face} face declared", blocks.check(broken))

	def test_a_pending_face_names_its_task_and_what_it_builds(self):
		broken = copy.deepcopy(blocks.BLOCKS)
		broken["party"]["faces"]["entry"] = {"pending": "later", "builds": "x"}
		self.assertIn("party.entry: a pending face must name the task that builds it", blocks.check(broken))
		broken["party"]["faces"]["entry"] = {"pending": "TASK-2026-02755", "builds": " "}
		self.assertIn("party.entry: a pending face must say what will be built", blocks.check(broken))

	def test_an_entry_face_is_a_module_export_or_pending(self):
		broken = copy.deepcopy(blocks.BLOCKS)
		broken["site"]["faces"]["entry"] = {"component": "a picker"}
		self.assertTrue(any(p.startswith("site.entry:") for p in blocks.check(broken)))

	def test_an_unknown_report_kind_fails(self):
		broken = copy.deepcopy(blocks.BLOCKS)
		broken["money"]["faces"]["report"] = {"kinds": ["currency"]}
		self.assertIn(
			"money.report: 'currency' is not a report-face kind", blocks.check(broken, report_kinds())
		)

	def test_pending_entry_faces_are_the_ones_the_plan_expects(self):
		"""Party and Site wait for P3.2's component; nothing else may quietly join them."""
		pending = {k: b["faces"]["entry"].get("pending") for k, b in blocks.BLOCKS.items()}
		self.assertEqual(pending["party"], "TASK-2026-02755")
		self.assertEqual(pending["site"], "TASK-2026-02755")
		self.assertIsNone(pending["scope_by_pillar"])


class FacesResolveTest(unittest.TestCase):
	def test_entry_modules_export_what_they_name(self):
		for key, block in blocks.BLOCKS.items():
			entry = block["faces"].get("entry") or {}
			if not entry or "pending" in entry:
				continue  # a missing face fails test_every_block_declares_all_three_faces
			with self.subTest(block=key):
				self.assertTrue(entry.get("module"), f"{key}'s entry face names no module")
				path = APP / entry["module"]
				self.assertTrue(path.is_file(), f"{entry['module']} does not exist")
				self.assertIn(entry["export"], _js_exports(path))

	def test_report_kinds_are_the_report_face_kinds(self):
		kinds = report_kinds()
		self.assertEqual(kinds, {"phone", "party", "pillar", "money", "date", "datetime", "time"})
		for key, block in blocks.BLOCKS.items():
			for kind in (block["faces"].get("report") or {}).get("kinds", []):
				with self.subTest(block=key, kind=kind):
					self.assertIn(kind, kinds)

	def test_print_helpers_exist_and_ps_helpers_reach_jinja(self):
		hooks = HOOKS.read_text(encoding="utf-8")
		for key, block in blocks.BLOCKS.items():
			face = block["faces"].get("print") or {}
			for helper in face.get("helpers", []):
				with self.subTest(block=key, helper=helper):
					if helper in blocks.FRAMEWORK_HELPERS:
						continue
					self.assertTrue(helper.startswith("erpnext_enhancements."), helper)
					module, _, name = helper.rpartition(".")
					path = REPO_ROOT / (module.replace(".", "/") + ".py")
					self.assertTrue(path.is_file(), f"{module} is not a module of this app")
					self.assertIn(name, _top_level_names(path))
					if name.startswith("ps_"):
						self.assertIn(f'"{helper}"', hooks, f"{helper} is not registered as a Jinja global")

	def test_framework_helpers_say_why(self):
		for helper, why in blocks.FRAMEWORK_HELPERS.items():
			self.assertTrue(helper.startswith("frappe."))
			self.assertGreater(len(why), 40)


class FieldsOnMetaTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.metas = build_metas()

	def test_every_named_field_is_on_the_doctypes_meta(self):
		self.assertEqual(blocks.field_problems(self.metas), [])

	def test_a_field_the_meta_lacks_fails(self):
		broken = copy.deepcopy(blocks.BLOCKS)
		broken["crew"]["doctypes"]["Project"]["fields"].append("custom_crew_chief")
		self.assertIn(
			"crew.Project: custom_crew_chief is not a field of Project",
			blocks.field_problems(self.metas, broken),
		)

	def test_a_hidden_mirror_the_meta_lacks_fails(self):
		broken = copy.deepcopy(blocks.BLOCKS)
		broken["party"]["doctypes"]["Opportunity"]["mirrors"] = {"primary_contact": "contact_persons"}
		self.assertIn(
			"party.Opportunity: contact_persons is not a field of Opportunity",
			blocks.field_problems(self.metas, broken),
		)

	def test_a_doctype_with_no_meta_fails(self):
		broken = copy.deepcopy(blocks.BLOCKS)
		broken["site"]["doctypes"]["Lead"] = {"fields": ["primary_address"]}
		self.assertIn("site.Lead: no meta to check against", blocks.field_problems(self.metas, broken))

	def test_both_doctypes_are_covered_by_every_block(self):
		for key, block in blocks.BLOCKS.items():
			with self.subTest(block=key):
				self.assertEqual(set(block["doctypes"]), {"Opportunity", "Project"})

	def test_a_mirror_links_to_what_its_visible_field_links_to(self):
		for doctype in ("Opportunity", "Project"):
			for visible, hidden in blocks.mirrors_for(doctype).items():
				with self.subTest(doctype=doctype, pair=(visible, hidden)):
					meta = self.metas[doctype]
					self.assertEqual(meta[visible].get("fieldtype"), "Link")
					self.assertEqual(meta[visible].get("fieldtype"), meta[hidden].get("fieldtype"))
					self.assertEqual(meta[visible].get("options"), meta[hidden].get("options"))

	def test_the_opportunity_party_is_the_account_the_contact_and_the_billing_address(self):
		self.assertEqual(
			blocks.fields_for("party", "Opportunity"),
			["opportunity_from", "party_name", "primary_contact", "customer_address"],
		)
		self.assertEqual(blocks.mirrors_for("Opportunity"), {"primary_contact": "contact_person"})
		# The job site is the Site block; the billing address is never mirrored onto it (v1.633.0).
		self.assertEqual(blocks.fields_for("site", "Opportunity"), ["primary_address"])
		self.assertNotIn("customer_address", blocks.mirrors_for("Opportunity").values())

	def test_project_has_no_hidden_contact_to_mirror(self):
		self.assertNotIn("contact_person", self.metas["Project"])
		self.assertEqual(blocks.mirrors_for("Project"), {})

	def test_money_fields_are_currency(self):
		for doctype in ("Opportunity", "Project"):
			currency = blocks.resolved()["money"]["doctypes"][doctype].get("currency")
			for fieldname in blocks.fields_for("money", doctype):
				if fieldname == currency:
					continue
				with self.subTest(doctype=doctype, field=fieldname):
					self.assertEqual(self.metas[doctype][fieldname]["fieldtype"], "Currency")

	def test_schedule_fields_are_dates(self):
		for doctype in ("Opportunity", "Project"):
			for fieldname in blocks.fields_for("schedule_window", doctype):
				with self.subTest(doctype=doctype, field=fieldname):
					self.assertIn(self.metas[doctype][fieldname]["fieldtype"], ("Date", "Datetime"))

	def test_scope_tables_are_the_streams_tables(self):
		for doctype in ("Opportunity", "Project"):
			spec = blocks.resolved()["scope_by_pillar"]["doctypes"][doctype]
			for stream, tables in spec["streams"].items():
				for kind, fieldname in tables.items():
					with self.subTest(doctype=doctype, stream=stream, table=kind):
						df = self.metas[doctype][fieldname]
						self.assertEqual(df["fieldtype"], "Table")
						self.assertTrue(
							df["options"].endswith(
								"Customer Requests" if kind == "requests" else "Deliverables"
							)
						)

	def test_unified_tab_fields_are_real(self):
		source = SETUP_FIELDS.read_text(encoding="utf-8")
		for fieldname in UNIFIED_TAB_FIELDS:
			with self.subTest(field=fieldname):
				self.assertIn(f'"fieldname": "{fieldname}"', source)
		for doctype in ("Opportunity", "Project"):
			self.assertRegex(source, rf'"{doctype}": \{{"contacts": True, "addresses": True\}}')

	def test_the_project_fixture_records_where_it_came_from(self):
		source = json.loads(ERPNEXT_FIXTURES["Project"].read_text(encoding="utf-8"))["_source"]
		self.assertEqual(source["ref"], "origin/version-16")
		self.assertEqual(source["paths"], ["erpnext/projects/doctype/project/project.json"])
		self.assertTrue(source["produced_by"])


class ReusesTheRulesTest(unittest.TestCase):
	def test_the_schedule_window_is_the_date_pairs_registry(self):
		for doctype in ("Opportunity", "Project"):
			with self.subTest(doctype=doctype):
				spec = blocks.resolved()["schedule_window"]["doctypes"][doctype]
				self.assertEqual(spec["pairs"], [pair["fields"] for pair in date_pairs.PAIRS[doctype]])

	def test_core_pairs_are_the_ones_date_pairs_leaves_to_erpnext(self):
		"""A pair ERPNext already validates is named, and date_pairs must not register it."""
		registered = {f for pair in date_pairs.PAIRS["Project"] for f in pair["fields"]}
		core = blocks.resolved()["schedule_window"]["doctypes"]["Project"]["core_pairs"]
		self.assertTrue(core)
		for pair in core:
			self.assertFalse(set(pair) & registered)
			# Its docstring lists what it leaves to ERPNext ("expected_start_date", "actual_*").
			named = pair[0] in date_pairs.__doc__ or f"``{pair[0].split('_')[0]}_*``" in date_pairs.__doc__
			self.assertTrue(named, f"{pair[0]} is not among the pairs date_pairs says ERPNext validates")

	def test_scope_streams_have_a_pillar(self):
		from erpnext_enhancements import print_style

		for doctype in ("Opportunity", "Project"):
			for stream in blocks.resolved()["scope_by_pillar"]["doctypes"][doctype]["streams"]:
				with self.subTest(stream=stream):
					self.assertIn(stream, print_style.STREAM_PILLAR)

	def test_scope_requests_are_the_opportunity_configs(self):
		from erpnext_enhancements.form_kit.config.opportunity import REQUEST_TABLES

		streams = blocks.resolved()["scope_by_pillar"]["doctypes"]["Opportunity"]["streams"]
		self.assertEqual(
			{s: t["requests"] for s, t in streams.items()}, {s: t[0] for s, t in REQUEST_TABLES.items()}
		)


RUNNER = r"""
import { pathToFileURL } from "node:url";
const twin = await import(pathToFileURL(process.argv[2]).href);
const out = {
	blocks: twin.BLOCKS,
	fields_for: {
		party_opp: twin.fields_for("party", "Opportunity"),
		site_project: twin.fields_for("site", "Project"),
		unknown_block: twin.fields_for("nope", "Opportunity"),
		unknown_doctype: twin.fields_for("party", "Lead"),
		inherited: twin.fields_for("toString", "Opportunity"),
	},
	mirrors_for: {
		Opportunity: twin.mirrors_for("Opportunity"),
		Project: twin.mirrors_for("Project"),
		Lead: twin.mirrors_for("Lead"),
	},
};
process.stdout.write(JSON.stringify(out));
"""


class JavaScriptTwinTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			# CI (GitHub sets CI=true) must hold the twin: a runner without node fails, never skips.
			if os.environ.get("CI"):
				raise AssertionError("node is not on PATH in CI, so the JS twin went unchecked")
			raise unittest.SkipTest("node is not on PATH")
		cls.tmp = tempfile.TemporaryDirectory()
		root = Path(cls.tmp.name)
		(root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
		shutil.copy(TWIN_JS, root / "blocks.js")
		(root / "run.mjs").write_text(RUNNER, encoding="utf-8")
		result = subprocess.run(
			[node, str(root / "run.mjs"), str(root / "blocks.js")],
			capture_output=True,
			text=True,
			check=True,
			encoding="utf-8",
		)
		cls.out = json.loads(result.stdout)

	@classmethod
	def tearDownClass(cls):
		cls.tmp.cleanup()

	def test_the_twin_equals_the_python_registry(self):
		self.assertEqual(self.out["blocks"], blocks.resolved())

	def test_fields_for_agrees(self):
		got = self.out["fields_for"]
		self.assertEqual(got["party_opp"], blocks.fields_for("party", "Opportunity"))
		self.assertEqual(got["site_project"], blocks.fields_for("site", "Project"))
		self.assertEqual(got["unknown_block"], [])
		self.assertEqual(got["unknown_doctype"], [])
		self.assertEqual(got["inherited"], [])

	def test_mirrors_for_agrees(self):
		for doctype, value in self.out["mirrors_for"].items():
			self.assertEqual(value, blocks.mirrors_for(doctype))


if __name__ == "__main__":
	unittest.main()

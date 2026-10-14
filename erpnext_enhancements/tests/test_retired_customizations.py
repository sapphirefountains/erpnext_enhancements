"""Bench-free: retired customizations stay retired (v1.682.1).

On 2026-07-18 a migrate ran an older build of this app. Its fixtures re-inserted 14 Custom Fields
that later releases had deleted with one-shot patches (12 blank "Comments" tabs from v1.159.2,
Opportunity's ``custom_lost_reason`` and ``custom_lost_competitor`` from v1.159.0), and nothing
deleted them again for three months, because those patches were already in ``tabPatch Log``.

This suite pins the three parts of the fix:

* ``setup/downgrade_guard.py`` runs FIRST in ``before_migrate`` and refuses a build older than the
  version that last migrated the site (with a per-site override), and never blocks on its own
  failure;
* ``patches/retire_resurrected_customizations.py`` runs both removal patches once more, is listed
  in ``patches.txt`` after them, and never raises;
* no retired field is back in the fixtures, anchored on, ordered, or created by app code.

Stubs a minimal ``frappe`` in ``setUpModule`` and restores what was there in ``tearDownModule``.

Run: python -m unittest erpnext_enhancements.tests.test_retired_customizations
"""

import ast
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

_saved = {}


class _ValidationError(Exception):
	pass


def setUpModule():
	_saved["frappe"] = sys.modules.get("frappe")
	stub = types.ModuleType("frappe")
	stub.ValidationError = _ValidationError
	stub.conf = {}
	stub.db = types.SimpleNamespace(sql=lambda *a, **k: [])
	stub.logged = []
	stub.log_error = lambda **kwargs: stub.logged.append(kwargs)
	stub.get_traceback = lambda: "traceback"
	sys.modules["frappe"] = stub
	for name in (
		"erpnext_enhancements.setup.downgrade_guard",
		"erpnext_enhancements.patches.retire_resurrected_customizations",
	):
		sys.modules.pop(name, None)


def tearDownModule():
	if _saved.get("frappe") is None:
		sys.modules.pop("frappe", None)
	else:
		sys.modules["frappe"] = _saved["frappe"]


def _guard():
	return importlib.import_module("erpnext_enhancements.setup.downgrade_guard")


def _targets(patch_file):
	"""(doctype, fieldname) pairs a removal patch deletes, read from its source."""
	tree = ast.parse((APP / "patches" / patch_file).read_text(encoding="utf-8"))
	pairs = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_TARGETS" for t in node.targets):
			pairs += [tuple(ast.literal_eval(elt)) for elt in node.value.elts]
	if not pairs:
		# remove_opportunity_lost_reason loops over a tuple of fieldnames on Opportunity.
		for node in ast.walk(tree):
			if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
				pairs += [("Opportunity", ast.literal_eval(elt)) for elt in node.iter.elts]
	return pairs


RETIRED = _targets("remove_duplicate_comments_tabs.py") + _targets("remove_opportunity_lost_reason.py")


class TestTheDowngradeGuard(unittest.TestCase):
	def test_versions_compare_as_numbers(self):
		parse = _guard().parse
		self.assertEqual(parse("1.682.1"), (1, 682, 1))
		self.assertEqual(parse("v1.10.0"), (1, 10, 0))
		self.assertGreater(parse("1.682.10"), parse("1.682.9"))
		self.assertEqual(parse("1.682"), (1, 682, 0))
		self.assertEqual(parse(None), (0, 0, 0))
		self.assertEqual(parse("1.2.3-beta"), (1, 2, 3))

	def test_an_older_build_is_refused_and_newer_or_equal_runs(self):
		check = _guard().check
		self.assertIsNone(check("1.682.1", "1.682.1"))
		self.assertIsNone(check("1.683.0", "1.682.1"))
		self.assertIsNone(check("1.682.1", None), "a fresh install has no row")
		message = check("1.158.0", "1.682.1")
		self.assertIn("v1.158.0", message)
		self.assertIn("v1.682.1", message)
		self.assertIn("allow_erpnext_enhancements_downgrade", message)
		self.assertIsNone(check("1.158.0", "1.682.1", allowed=True))

	def test_the_hook_raises_on_a_downgrade(self):
		guard = _guard()
		frappe = sys.modules["frappe"]
		frappe.db.sql = lambda *a, **k: [["99.0.0"]]
		frappe.conf = {}
		with self.assertRaises(_ValidationError):
			guard.refuse_downgrade()

	def test_the_hook_lets_a_normal_deploy_and_an_override_through(self):
		guard = _guard()
		frappe = sys.modules["frappe"]
		frappe.db.sql = lambda *a, **k: [["0.0.1"]]
		frappe.conf = {}
		guard.refuse_downgrade()
		frappe.db.sql = lambda *a, **k: []
		guard.refuse_downgrade()
		frappe.db.sql = lambda *a, **k: [["99.0.0"]]
		frappe.conf = {"allow_erpnext_enhancements_downgrade": 1}
		guard.refuse_downgrade()
		frappe.conf = {}

	def test_the_guard_never_blocks_on_its_own_failure(self):
		guard = _guard()
		frappe = sys.modules["frappe"]

		def broken(*args, **kwargs):
			raise RuntimeError("no table")

		frappe.db.sql = broken
		guard.refuse_downgrade()
		frappe.db.sql = lambda *a, **k: []

	def test_it_runs_first_in_before_migrate(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		hooks = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", "") == "before_migrate" for t in node.targets)
		)
		first = ast.literal_eval(hooks.elts[0])
		self.assertEqual(first, "erpnext_enhancements.setup.downgrade_guard.refuse_downgrade")


class TestTheCleanupPatch(unittest.TestCase):
	def test_it_is_listed_after_the_patches_it_reruns(self):
		lines = [line.strip() for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
		post = lines.index("[post_model_sync]")
		mine = lines.index("erpnext_enhancements.patches.retire_resurrected_customizations")
		self.assertGreater(mine, post)
		for older in (
			"erpnext_enhancements.patches.remove_duplicate_comments_tabs",
			"erpnext_enhancements.patches.remove_opportunity_lost_reason",
		):
			self.assertLess(lines.index(older), mine)

	def test_it_reruns_both_and_never_raises(self):
		patch = importlib.import_module("erpnext_enhancements.patches.retire_resurrected_customizations")
		frappe = sys.modules["frappe"]
		ran = []
		fakes = {}
		for path in patch.PATCHES:
			module = types.ModuleType(path)
			if path.endswith("remove_duplicate_comments_tabs"):
				module.execute = lambda: ran.append("tabs")
			else:

				def boom():
					ran.append("lost")
					raise RuntimeError("delete failed")

				module.execute = boom
			fakes[path] = module
		saved = {path: sys.modules.get(path) for path in fakes}
		sys.modules.update(fakes)
		frappe.logged.clear()
		try:
			patch.execute()
		finally:
			for path, module in saved.items():
				if module is None:
					sys.modules.pop(path, None)
				else:
					sys.modules[path] = module
		self.assertEqual(ran, ["tabs", "lost"])
		self.assertEqual(len(frappe.logged), 1)
		self.assertEqual(frappe.logged[0]["title"], "Retired customization not removed")


class TestRetiredFieldsStayRetired(unittest.TestCase):
	def test_the_retired_list_is_read_from_the_removal_patches(self):
		self.assertEqual(len(RETIRED), 14)
		self.assertIn(("Purchase Order", "custom_comments"), RETIRED)
		self.assertIn(("Address", "custom_comments_tab"), RETIRED)
		self.assertIn(("Opportunity", "custom_lost_reason"), RETIRED)
		self.assertIn(("Opportunity", "custom_lost_competitor"), RETIRED)

	def test_no_retired_field_is_back_in_the_fixtures(self):
		fields = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		retired = set(RETIRED)
		self.assertEqual(sorted(t for t in ((r["dt"], r["fieldname"]) for r in fields) if t in retired), [])
		anchored = [(r["dt"], r["fieldname"]) for r in fields if (r["dt"], r.get("insert_after")) in retired]
		self.assertEqual(anchored, [], "a fixture field is anchored on a retired one")

	def test_no_property_setter_sets_or_orders_a_retired_field(self):
		setters = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
		retired = set(RETIRED)
		self.assertEqual([r["name"] for r in setters if (r["doc_type"], r.get("field_name")) in retired], [])
		ordered = []
		for record in setters:
			if record.get("property") == "field_order":
				for doctype, fieldname in retired:
					if record["doc_type"] == doctype and fieldname in json.loads(record["value"]):
						ordered.append((doctype, fieldname))
		self.assertEqual(ordered, [])

	def test_no_app_code_creates_a_retired_field(self):
		# The canonical Comments tab on these forms comes from the fixtures. Master Project, the one
		# doctype that still gets `custom_comments_tab` from setup.custom_fields.create_comments_tab,
		# is not among the retired.
		retired_doctypes = {doctype for doctype, _ in RETIRED}
		for path in APP.rglob("*.py"):
			if "tests" in path.parts or path.parent.name == "patches":
				continue
			text = path.read_text(encoding="utf-8")
			if "create_comments_tab(" in text:
				for call in ast.walk(ast.parse(text)):
					if (
						isinstance(call, ast.Call)
						and getattr(call.func, "id", "") == "create_comments_tab"
						and call.args
					):
						arg = call.args[0]
						if isinstance(arg, ast.Constant):
							with self.subTest(path=str(path)):
								self.assertNotIn(arg.value, retired_doctypes)
			for fieldname in ("custom_lost_reason", "custom_lost_competitor"):
				with self.subTest(path=str(path), fieldname=fieldname):
					self.assertNotIn(f'"fieldname": "{fieldname}"', text)


if __name__ == "__main__":
	unittest.main()

"""Bench-free guard: every custom field on a generator-owned form is in that form's field_order.

The seven procurement forms (Item, Material Request, Purchase Order, Purchase Receipt, Purchase
Invoice, Supplier Quotation, Request for Quotation) take their field order from a
``<DocType>-main-field_order`` Property Setter that ``scripts/layout/generate_field_order.py``
writes from ``scripts/layout/specs/*.json``. A custom field added later and left out of that
array is not lost: v16 places it by its ``insert_after``. But a **Section Break** placed that way
lands in the middle of whatever section it follows, and every field after it, up to the next
break, renders under the new heading. On prod's Item form (found 2026-10-14, fixed in v1.682.0) Default UOM, Image,
Disabled, Maintain Stock and the fixed-asset ticks sat under "Maintenance Billing", because the
Safety Data and Maintenance Billing sections (inserted after Item Group) never reached the array.
The Purchase Order form had the same fault: Company, Is Subcontracted, Supplier Warehouse and
Amended From sat under "Contracted under", and Order Status was cut in two by "Approval". Nothing
failed; the forms just read wrong.

This suite fails the build when that can happen again:

* every Custom Field in ``fixtures/custom_field.json`` on one of the seven doctypes is in its
  field_order array and placed explicitly in its spec (the generator never pulls custom fields in
  by macro);
* every field in ``scripts/layout/code_owned_fields.json`` is too;
* every field the app creates in code with ``create_custom_fields`` on one of the seven doctypes
  (the aquatic-equipment block on Item, the approval stamp on Purchase Order, the store-run fields
  on Purchase Receipt) is registered in ``code_owned_fields.json``. Field names are read from the
  source with ``ast``: a string literal, a module-level string constant, or a ``field_definition()``
  helper returning a dict literal. A field created any other way is outside what this can see;
  the README's dev-bench check (``set(field_order) == meta fields``) is the complete one.

Run: python -m unittest erpnext_enhancements.tests.test_layout_field_order_coverage
"""

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
LAYOUT = REPO_ROOT / "scripts" / "layout"


def _load(path):
	return json.loads(path.read_text(encoding="utf-8"))


def _specs():
	return {spec["doctype"]: spec for spec in (_load(p) for p in sorted((LAYOUT / "specs").glob("*.json")))}


def _field_orders(doctypes):
	by_name = {r["name"]: r for r in _load(APP / "fixtures" / "property_setter.json")}
	return {dt: json.loads(by_name[f"{dt}-main-field_order"]["value"]) for dt in doctypes}


def missing_from_layout(doctype, required, field_order, spec):
	"""The required fieldnames absent from the field_order array or not placed in the spec."""
	placed = {item for item in spec["layout"] if isinstance(item, str)}
	return sorted({name for name in required if name not in field_order or name not in placed})


# ----------------------------------------------------------------- fields created in app code


def _module_constants(tree):
	"""Module-level NAME = "string" assignments."""
	out = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
			if isinstance(node.value.value, str):
				for target in node.targets:
					if isinstance(target, ast.Name):
						out[target.id] = node.value.value
	return out


def _string(node, constants):
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	if isinstance(node, ast.Name):
		return constants.get(node.id)
	return None


def _dict_fieldname(node, constants):
	for key, value in zip(node.keys, node.values, strict=True):
		if isinstance(key, ast.Constant) and key.value == "fieldname":
			return _string(value, constants)
	return None


def _imports(tree):
	"""Local name -> (module path, original name) for `from erpnext_enhancements... import x`."""
	out = {}
	for node in ast.walk(tree):
		if (
			isinstance(node, ast.ImportFrom)
			and node.module
			and node.module.startswith("erpnext_enhancements")
		):
			for alias in node.names:
				out[alias.asname or alias.name] = (node.module, alias.name)
	return out


def _helper_fieldname(module, func_name):
	"""The fieldname a `def func_name(): return {...}` helper in `module` returns, if literal."""
	path = REPO_ROOT / Path(*module.split(".")).with_suffix(".py")
	if not path.exists():
		return None
	tree = ast.parse(path.read_text(encoding="utf-8"))
	constants = _module_constants(tree)
	for node in tree.body:
		if isinstance(node, ast.FunctionDef) and node.name == func_name:
			for sub in ast.walk(node):
				if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
					return _dict_fieldname(sub.value, constants)
	return None


def code_created_fields(doctypes):
	"""{doctype: {fieldname: source file}} for create_custom_fields calls in app code."""
	found = {}
	for path in sorted(APP.rglob("*.py")):
		if "tests" in path.parts:
			continue
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue
		constants = _module_constants(tree)
		imports = _imports(tree)
		for node in ast.walk(tree):
			if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Dict)):
				continue
			func = node.func
			name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
			if name != "create_custom_fields":
				continue
			for key, value in zip(node.args[0].keys, node.args[0].values, strict=True):
				doctype = _string(key, constants) if key is not None else None
				if doctype not in doctypes or not isinstance(value, (ast.List, ast.Tuple)):
					continue
				for element in value.elts:
					fieldname = None
					if isinstance(element, ast.Dict):
						fieldname = _dict_fieldname(element, constants)
					elif isinstance(element, ast.Call) and isinstance(element.func, ast.Name):
						origin = imports.get(element.func.id)
						if origin:
							fieldname = _helper_fieldname(*origin)
					if fieldname:
						found.setdefault(doctype, {})[fieldname] = str(path.relative_to(REPO_ROOT))
	return found


class TestLayoutFieldOrderCoverage(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.specs = _specs()
		cls.doctypes = sorted(cls.specs)
		cls.orders = _field_orders(cls.doctypes)
		cls.custom_fields = _load(APP / "fixtures" / "custom_field.json")
		cls.registry = _load(LAYOUT / "code_owned_fields.json")

	def test_the_seven_procurement_forms_are_covered(self):
		self.assertEqual(
			self.doctypes,
			[
				"Item",
				"Material Request",
				"Purchase Invoice",
				"Purchase Order",
				"Purchase Receipt",
				"Request for Quotation",
				"Supplier Quotation",
			],
		)

	def test_every_fixture_custom_field_is_in_the_field_order(self):
		for doctype in self.doctypes:
			with self.subTest(doctype=doctype):
				fields = [r["fieldname"] for r in self.custom_fields if r["dt"] == doctype]
				missing = missing_from_layout(doctype, fields, self.orders[doctype], self.specs[doctype])
				self.assertEqual(
					missing,
					[],
					f"{doctype}: add these to scripts/layout/specs and re-run generate_field_order.py",
				)

	def test_every_registered_code_owned_field_is_in_the_field_order(self):
		for doctype in self.doctypes:
			with self.subTest(doctype=doctype):
				fields = [r["fieldname"] for r in self.registry.get(doctype, [])]
				missing = missing_from_layout(doctype, fields, self.orders[doctype], self.specs[doctype])
				self.assertEqual(missing, [], doctype)

	def test_every_field_created_in_code_is_registered(self):
		created = code_created_fields(set(self.doctypes))
		self.assertIn(
			"custom_aquatic_section", created.get("Item", {}), "the ast scan no longer sees setup.py"
		)
		self.assertIn(
			"custom_order_stage", created.get("Purchase Order", {}), "field_definition() not resolved"
		)
		self.assertIn(
			"custom_store_run", created.get("Purchase Receipt", {}), "module constants not resolved"
		)
		for doctype, fields in created.items():
			with self.subTest(doctype=doctype):
				known = {r["fieldname"] for r in self.registry.get(doctype, [])}
				known |= {r["fieldname"] for r in self.custom_fields if r["dt"] == doctype}
				unregistered = {name: src for name, src in fields.items() if name not in known}
				self.assertEqual(
					unregistered,
					{},
					f"{doctype}: register these in scripts/layout/code_owned_fields.json and place them",
				)

	def test_the_checker_reports_a_section_left_out(self):
		# The 2026-10-14 fault itself: the Safety Data section and its fields were in the fixtures and
		# in neither the array nor the spec.
		spec = {"layout": ["details", "item_group", "stock_uom"]}
		order = ["details", "item_group", "stock_uom"]
		self.assertEqual(
			missing_from_layout("Item", ["custom_sds_section", "custom_is_chemical"], order, spec),
			["custom_is_chemical", "custom_sds_section"],
		)
		# In the array but not the spec (a hand edit the generator would undo) is caught too.
		self.assertEqual(
			missing_from_layout("Item", ["custom_x"], [*order, "custom_x"], spec),
			["custom_x"],
		)


if __name__ == "__main__":
	unittest.main()

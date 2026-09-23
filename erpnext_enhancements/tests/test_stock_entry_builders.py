"""Every Stock Entry this app builds sets its type and a difference account. Bench-free.

On ERPNext v16 a Stock Entry built in Python fails two ways that nothing bench-free can
see, because both live in ERPNext's ``validate``:

* **``stock_entry_type`` is mandatory, and ``purpose`` is only a read-only fetch from it.**
  ``validate`` never derives the type from the purpose — ERPNext's own builders call
  ``set_stock_entry_type()`` by hand — so setting only ``purpose`` raises
  ``MandatoryError: stock_entry_type`` on insert.
* **Every row needs a difference account.** ERPNext fills one from the Item's default,
  its Item Group's, then ``Company.stock_adjustment_account`` — and production's Company
  has none, so with perpetual inventory on ``validate_difference_account`` refuses the
  entry. ``inventory_enhancements.stock_accounts.difference_account`` is the one rule this
  app resolves it with.

``api/maintenance_workflow.create_stock_entry`` shipped with both bugs and a green build:
it set ``purpose = "Material Issue"`` and appended rows with no ``expense_account``. It
never failed on production only because no maintenance record had been submitted yet
(v1.521.0). This suite reads the source instead of running it, so it fails the build on
that shape wherever it appears next.

The rules, per module that builds a Stock Entry (``frappe.new_doc("Stock Entry")`` or a
``"doctype": "Stock Entry"`` dict):

1. The building function sets ``stock_entry_type``.
2. Every ``<entry>.append("items", row)`` takes ``expense_account`` from a call to the
   shared resolver — in the dict literal, or by assigning ``row["expense_account"]`` in the
   same function. ``<entry>`` is any name bound to a construction, or to a call of a
   function that builds one (``se = _stock_entry(...)``).
3. The resolver is defined exactly once, in ``stock_accounts``.
4. ``KNOWN_BUILDERS`` names every building function. A new one fails the inventory test
   on purpose: that is the moment to read the two bullets above.

Needs a specific account instead? Pass it as ``configured=`` to the resolver, which still
refuses a group, disabled, Stock-type or other-company account, then falls back.

Run: python -m unittest erpnext_enhancements.tests.test_stock_entry_builders -v
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

#: (module path relative to the app package, function) for every Stock Entry this app builds.
KNOWN_BUILDERS = {
	("api/maintenance_workflow.py", "create_stock_entry"),
	("api/stock_scan.py", "_stock_entry"),
}

#: Names the shared resolver is called by: its own, and the alias ``api/stock_scan`` keeps.
RESOLVERS = {"difference_account", "_difference_account"}

#: Where the resolver lives. It must be defined there and nowhere else.
RESOLVER_HOME = "inventory_enhancements/stock_accounts.py"

_SKIP_PARTS = {"tests", "node_modules", "__pycache__"}


def _app_modules():
	for path in sorted(APP.rglob("*.py")):
		if _SKIP_PARTS.intersection(path.relative_to(APP).parts):
			continue
		yield path.relative_to(APP).as_posix(), path.read_text(encoding="utf-8")


def _call_name(func):
	if isinstance(func, ast.Name):
		return func.id
	if isinstance(func, ast.Attribute):
		return func.attr
	return None


def _is_stock_entry_dict(node):
	if not isinstance(node, ast.Dict):
		return False
	for key, value in zip(node.keys, node.values, strict=False):
		if (
			isinstance(key, ast.Constant)
			and key.value == "doctype"
			and isinstance(value, ast.Constant)
			and value.value == "Stock Entry"
		):
			return True
	return False


def _is_construction(node):
	"""``frappe.new_doc("Stock Entry")``, ``get_doc("Stock Entry", ...)`` or a Stock Entry dict."""
	if _is_stock_entry_dict(node):
		return True
	if not isinstance(node, ast.Call) or _call_name(node.func) not in ("new_doc", "get_doc"):
		return False
	first = node.args[0] if node.args else None
	# get_doc("Stock Entry", name) loads an existing entry; only new_doc makes one by name.
	if _call_name(node.func) == "new_doc" and isinstance(first, ast.Constant) and first.value == "Stock Entry":
		return True
	return _is_stock_entry_dict(first)


def _functions(tree):
	return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]


def _innermost_function(tree, target):
	best = None
	for func in _functions(tree):
		if any(node is target for node in ast.walk(func)):
			if best is None or func.lineno >= best.lineno:
				best = func
	return best


def builder_sites(source):
	"""``[(function name, construction node)]`` for every Stock Entry built in ``source``.

	``source`` may be text or an already-parsed tree. Pass the tree when the caller goes on
	to look nodes up in it: nodes are matched by identity, and a second parse makes new ones.
	"""
	tree = ast.parse(source) if isinstance(source, str) else source
	sites = []
	# A dict handed to get_doc is one construction, not two: skip it once its call is counted.
	counted = set()
	for node in ast.walk(tree):
		if id(node) in counted or not _is_construction(node):
			continue
		if isinstance(node, ast.Call) and node.args:
			counted.add(id(node.args[0]))
		func = _innermost_function(tree, node)
		sites.append((func.name if func else "<module>", node))
	return sites


def _sets_stock_entry_type(func):
	for node in ast.walk(func):
		if isinstance(node, ast.Assign | ast.AnnAssign):
			targets = node.targets if isinstance(node, ast.Assign) else [node.target]
			if any(isinstance(t, ast.Attribute) and t.attr == "stock_entry_type" for t in targets):
				return True
		if isinstance(node, ast.Dict) and any(
			isinstance(k, ast.Constant) and k.value == "stock_entry_type" for k in node.keys
		):
			return True
		if isinstance(node, ast.Call) and any(kw.arg == "stock_entry_type" for kw in node.keywords):
			return True
	return False


def _is_resolver_call(node):
	return isinstance(node, ast.Call) and _call_name(node.func) in RESOLVERS


def _row_account_assigned(func, row_name):
	"""True when ``func`` sets ``row_name["expense_account"]`` (or ``.expense_account``) from the resolver."""
	for node in ast.walk(func):
		if not isinstance(node, ast.Assign) or not _is_resolver_call(node.value):
			continue
		for target in node.targets:
			if (
				isinstance(target, ast.Subscript)
				and isinstance(target.value, ast.Name)
				and target.value.id == row_name
				and isinstance(target.slice, ast.Constant)
				and target.slice.value == "expense_account"
			):
				return True
			if (
				isinstance(target, ast.Attribute)
				and target.attr == "expense_account"
				and isinstance(target.value, ast.Name)
				and target.value.id == row_name
			):
				return True
	return False


def problems(source, path="<source>"):
	"""Every rule ``source`` breaks, as sentences. Empty when it builds Stock Entries right."""
	tree = ast.parse(source)
	sites = builder_sites(tree)
	if not sites:
		return []
	found = []
	builder_names = {name for name, _node in sites}
	for name, node in sites:
		func = _innermost_function(tree, node)
		if func is None or not _sets_stock_entry_type(func):
			found.append(f"{path}:{node.lineno} {name}() builds a Stock Entry without setting stock_entry_type.")

	for func in _functions(tree):
		entries = set()
		for node in ast.walk(func):
			if isinstance(node, ast.Assign) and (
				_is_construction(node.value)
				or (isinstance(node.value, ast.Call) and _call_name(node.value.func) in builder_names)
			):
				entries.update(t.id for t in node.targets if isinstance(t, ast.Name))
		for node in ast.walk(func):
			if not (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "append"
				and isinstance(node.func.value, ast.Name)
				and node.func.value.id in entries
				and node.args
				and isinstance(node.args[0], ast.Constant)
				and node.args[0].value == "items"
			):
				continue
			row = node.args[1] if len(node.args) > 1 else None
			where = f"{path}:{node.lineno} {func.name}()"
			if isinstance(row, ast.Dict):
				values = {
					k.value: v for k, v in zip(row.keys, row.values, strict=False) if isinstance(k, ast.Constant)
				}
				if "expense_account" not in values:
					found.append(f"{where} appends a Stock Entry row with no expense_account.")
				elif not _is_resolver_call(values["expense_account"]):
					found.append(f"{where} sets expense_account without the shared difference_account resolver.")
			elif isinstance(row, ast.Name):
				if not _row_account_assigned(func, row.id):
					found.append(
						f"{where} appends Stock Entry row {row.id!r} without setting "
						f"{row.id}['expense_account'] from the shared difference_account resolver."
					)
			else:
				found.append(f"{where} appends a Stock Entry row whose expense_account cannot be traced.")
	return found


class TestStockEntryBuilders(unittest.TestCase):
	def test_the_inventory_of_builders_is_complete(self):
		found = set()
		for path, source in _app_modules():
			found.update((path, name) for name, _node in builder_sites(source))
		self.assertEqual(
			found,
			KNOWN_BUILDERS,
			"The set of functions that build a Stock Entry changed. Add the new one to "
			"KNOWN_BUILDERS after reading this module's docstring: it must set "
			"stock_entry_type and take each row's expense_account from difference_account.",
		)

	def test_every_builder_sets_its_type_and_every_row_a_difference_account(self):
		failures = []
		for path, source in _app_modules():
			failures.extend(problems(source, path))
		self.assertEqual(failures, [], "\n".join(failures))

	def test_the_resolver_is_defined_once(self):
		homes = []
		for path, source in _app_modules():
			for func in _functions(ast.parse(source)):
				if func.name in RESOLVERS:
					homes.append(path)
		self.assertEqual(homes, [RESOLVER_HOME])


class TestTheCheckItself(unittest.TestCase):
	"""The checker has to catch the code that shipped, or it guards nothing."""

	SHIPPED = '''
def create_stock_entry(doc):
    rows = build_stock_entry_rows(doc)
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.purpose = "Material Issue"
    for row in rows:
        stock_entry.append("items", row)
    stock_entry.insert()
'''

	def test_it_catches_the_maintenance_builder_as_it_shipped(self):
		found = problems(self.SHIPPED)
		self.assertEqual(len(found), 2, found)
		self.assertIn("without setting stock_entry_type", found[0])
		self.assertIn("row 'row'", found[1])

	def test_it_accepts_the_fixed_shape(self):
		fixed = self.SHIPPED.replace(
			'stock_entry.purpose = "Material Issue"', 'stock_entry.stock_entry_type = "Material Issue"'
		).replace(
			'    for row in rows:\n',
			'    for row in rows:\n        row["expense_account"] = difference_account(company, row["item_code"])\n',
		)
		self.assertEqual(problems(fixed), [])

	def test_a_dict_row_needs_the_resolver_not_a_literal(self):
		source = '''
def post(company):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Issue"
    se.append("items", {"item_code": "X", "qty": 1})
    se.append("items", {"item_code": "X", "qty": 1, "expense_account": "5119 - Stock Adjustment - SF"})
    se.append("items", {"item_code": "X", "qty": 1, "expense_account": difference_account(company, "X")})
'''
		found = problems(source)
		self.assertEqual(len(found), 2, found)
		self.assertIn("no expense_account", found[0])
		self.assertIn("without the shared difference_account resolver", found[1])

	def test_it_follows_an_entry_built_by_a_helper(self):
		source = '''
def _entry(company):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Receipt"
    return se

def post(company):
    se = _entry(company)
    se.append("items", {"item_code": "X", "qty": 1})
'''
		found = problems(source)
		self.assertEqual(len(found), 1, found)
		self.assertIn("post()", found[0])

	def test_a_dict_built_entry_counts_and_other_doctypes_do_not(self):
		source = '''
def post():
    frappe.get_doc({"doctype": "Stock Entry", "purpose": "Material Issue"}).insert()
    frappe.get_doc({"doctype": "Stock Reconciliation", "purpose": "Stock Reconciliation"}).insert()
    si = frappe.new_doc("Sales Invoice")
    si.append("items", {"item_code": "X"})
'''
		found = problems(source)
		self.assertEqual(len(found), 1, found)
		self.assertIn("without setting stock_entry_type", found[0])


if __name__ == "__main__":
	unittest.main()

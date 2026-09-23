"""Receive Items on the Purchase Order (ER-2026-458194, TASK-2026-02045): the wiring.

The arithmetic — what arrived against what is pending, with the over-receipt allowance —
lives in ``procurement_quantities.plan_receipt`` and is tested next to the rest of that
module in ``test_procurement_quantities.py``. This file pins the things around it that fail
silently: an endpoint that answers a GET, a receipt inserted or submitted past the
framework's permission check, a form script that is not loaded or dials a method that does
not exist, and a receipt built by hand instead of by ERPNext's own mapper.

Bench-free by construction: AST over ``api/procurement.py``, regex over the form script and
``hooks.py``. Plain pytest functions, so it has its own ``python -m pytest`` step in
``ci.yml`` — ``python -m unittest`` collects nothing from these and reports success.

Run: python -m pytest erpnext_enhancements/tests/test_po_receive_items.py -q
"""

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
API = APP / "api" / "procurement.py"
SCRIPT = APP / "public" / "js" / "po_receive_items.js"
HOOKS = APP / "hooks.py"
API_README = APP / "api" / "README.md"

ENDPOINT = "receive_items"
DOTTED = f"erpnext_enhancements.api.procurement.{ENDPOINT}"


def _endpoint():
	tree = ast.parse(API.read_text(encoding="utf-8"))
	for node in ast.walk(tree):
		if isinstance(node, ast.FunctionDef) and node.name == ENDPOINT:
			return node
	raise AssertionError(f"{ENDPOINT} is not defined in api/procurement.py")


def _endpoint_source():
	source = API.read_text(encoding="utf-8")
	return ast.get_source_segment(source, _endpoint()) or ""


def _whitelist_decorator(node):
	for dec in node.decorator_list:
		target = dec.func if isinstance(dec, ast.Call) else dec
		if getattr(target, "attr", "") == "whitelist":
			return dec
	return None


def _strip_prose(source):
	"""Source with docstrings and ``#`` comments removed, so a rule discussed in prose is
	not matched as if it were code — the trap the chat work fell into three times.

	Every docstring is located against the source *as parsed* before any is removed: removing
	the module docstring first shifts every later line, and on a whole file the next lookup
	then reads the wrong text or runs off the end (IndexError).
	"""
	tree = ast.parse(source)
	docstrings = []
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				if isinstance(body[0].value.value, str):
					docstrings.append(ast.get_source_segment(source, body[0].value) or "")
	for segment in docstrings:
		if segment:
			source = source.replace(segment, "", 1)
	return re.sub(r"#.*$", "", source, flags=re.M)


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


def test_endpoint_is_whitelisted_post_only():
	"""It writes and submits a stock document; a GET would put that in access logs and the
	browser history, and would let an image tag do it."""
	dec = _whitelist_decorator(_endpoint())
	assert dec is not None, "receive_items must be @frappe.whitelist()ed"
	assert isinstance(dec, ast.Call), "receive_items must declare methods=[\"POST\"]"
	methods = [k for k in dec.keywords if k.arg == "methods"]
	assert methods, "receive_items must declare methods=[\"POST\"]"
	values = [e.value for e in methods[0].value.elts if isinstance(e, ast.Constant)]
	assert values == ["POST"]


def test_the_receipt_goes_through_the_frameworks_permission_checks():
	"""No ``ignore_permissions`` anywhere in the function: a user who could not create and
	submit a Purchase Receipt by hand must not be able to do it from the dialog."""
	body = _strip_prose(_endpoint_source())
	assert "ignore_permissions" not in body
	assert re.search(r"\.insert\(\)", body), "the receipt must be inserted with the framework's checks"
	assert re.search(r"\.submit\(\)", body), "the receipt must be submitted, not left as a draft"
	assert 'check_permission("read")' in body, "the order needs an explicit read check"
	assert '"Purchase Receipt"' in body and "has_permission(" in body


def test_the_receipt_is_built_by_erpnexts_own_mapper():
	"""``make_purchase_receipt`` with ``filtered_children`` is exactly what Create >
	Purchase Receipt does. A hand-built receipt would be a second copy of that mapping."""
	body = _strip_prose(_endpoint_source())
	assert "make_purchase_receipt(" in body
	assert "filtered_children" in body
	assert "plan_receipt(" in body, "the quantity rules live in procurement_quantities, not here"


def test_order_state_is_refused_before_anything_is_built():
	body = _strip_prose(_endpoint_source())
	assert "docstatus != 1" in body
	assert '"Closed"' in body and '"On Hold"' in body


# ---------------------------------------------------------------------------
# The form script
# ---------------------------------------------------------------------------


def test_form_script_is_loaded_on_the_purchase_order():
	hooks = HOOKS.read_text(encoding="utf-8")
	block = re.search(r'"Purchase Order":\s*\[(.*?)\]', hooks[hooks.index("doctype_js") :], re.S)
	assert block, "hooks.py has no doctype_js entry for Purchase Order"
	assert '"public/js/po_receive_items.js"' in block.group(1)


def test_form_script_dials_a_method_that_exists():
	script = SCRIPT.read_text(encoding="utf-8")
	assert f'"{DOTTED}"' in script
	_endpoint()  # raises if the dotted name does not resolve


def test_form_script_gates_the_button_the_way_the_endpoint_does():
	"""Both ends agree on when receiving is possible; a button that shows on a Closed order
	would be a dialog that ends in a server error."""
	script = SCRIPT.read_text(encoding="utf-8")
	assert "frm.doc.docstatus === 1" in script
	assert "flt(frm.doc.per_received) < 100" in script
	assert '["Closed", "On Hold"]' in script
	assert 'frappe.model.can_create("Purchase Receipt")' in script
	assert "delivered_by_supplier" in script, "drop-ship lines are never received here"


def test_form_script_does_not_cap_at_pending():
	"""The over-receipt allowance is a server setting (Item, then Stock Settings). A cap in
	the client would either duplicate that rule or, once somebody sets an allowance, refuse a
	receipt the server would accept."""
	script = SCRIPT.read_text(encoding="utf-8")
	code = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
	code = re.sub(r"//.*$", "", code, flags=re.M)
	assert "allowance" not in code
	assert "qty < 0" in code, "a negative is refused client-side; it needs no settings to refuse"


def test_endpoint_is_documented_in_the_api_map():
	assert f"`{ENDPOINT}`" in API_README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The Stock Scan page's "+" (v1.521.0): one order line, into the scanned location
#
# The page receives through ``receive_order_line`` -> ``receive_items``, so the page and the
# order's Receive Items dialog cannot disagree about what may be received. What can go wrong
# silently: the helper growing a receipt path of its own (a second mapper, a second set of
# checks, an ignore_permissions), the helper becoming dialable over HTTP without the page's
# gate, and the dialog's receipts being put somewhere other than the order line's warehouse
# because a new ``warehouse`` argument leaked out of its ``if``.
# ---------------------------------------------------------------------------

LINE_HELPER = "receive_order_line"
WAREHOUSE_HELPER = "_receiving_warehouse"


def _function(name):
	for node in ast.parse(API.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name} is not defined in api/procurement.py")


def _function_source(name):
	return ast.get_source_segment(API.read_text(encoding="utf-8"), _function(name)) or ""


def _calls_named(node, name):
	return [
		sub
		for sub in ast.walk(node)
		if isinstance(sub, ast.Call) and ast.unparse(sub.func).split(".")[-1] == name
	]


def test_receive_order_line_is_not_reachable_over_http():
	"""The page reaches it only through ``api.stock_scan.add``, which checks the page's roles
	and the ``client_ref`` first. Whitelisted, it would be a second door with neither."""
	assert _whitelist_decorator(_function(LINE_HELPER)) is None
	assert _whitelist_decorator(_function(WAREHOUSE_HELPER)) is None


def test_receive_order_line_owns_no_receipt_of_its_own():
	"""Every refusal and the receipt itself are ``receive_items``'. A second mapper, insert or
	submit here would be a second copy of that path, with its own idea of what is allowed."""
	body = _strip_prose(_function_source(LINE_HELPER))
	assert "receive_items(" in body
	for forbidden in (
		"make_purchase_receipt(",
		".insert(",
		".submit(",
		"ignore_permissions",
		"set_user",
		".commit(",
	):
		assert forbidden not in body, f"receive_order_line must not contain {forbidden}"


def test_receive_order_line_hands_on_the_location_and_converts_the_unit():
	"""The page counts in the stock UOM; a receipt row is in the order line's UOM."""
	calls = _calls_named(_function(LINE_HELPER), "receive_items")
	assert len(calls) == 1
	keywords = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
	assert keywords.get("warehouse") == "warehouse", "the scanned location must reach the receipt"
	assert keywords.get("remarks") == "remarks"
	body = _strip_prose(_function_source(LINE_HELPER))
	assert "to_order_uom(" in body
	unparsed = ast.unparse(_function(LINE_HELPER))
	assert "line.parenttype != 'Purchase Order'" in unparsed, "the line must belong to a Purchase Order"
	assert "line.item_code != item_code" in body, "the line must be for the item that was scanned"


def test_only_the_stock_scan_endpoint_calls_it():
	callers = []
	for path in sorted(APP.rglob("*.py")):
		if "tests" in path.relative_to(APP).parts or path == API:
			continue
		source = path.read_text(encoding="utf-8")
		if f"{LINE_HELPER}(" in source and f"{LINE_HELPER}(" in _strip_prose(source):
			callers.append(path.relative_to(APP).as_posix())
	assert callers == ["api/stock_scan.py"]


def test_receive_items_keeps_the_dialogs_signature():
	"""The dialog sends ``purchase_order, rows, posting_date``; the two new arguments default
	to None so that call means exactly what it meant before."""
	args = _endpoint().args
	names = [a.arg for a in args.args]
	assert names[:3] == ["purchase_order", "rows", "posting_date"]
	defaults = dict(
		zip(names[len(names) - len(args.defaults) :], (ast.unparse(d) for d in args.defaults), strict=True)
	)
	assert defaults.get("posting_date") == "None"
	assert defaults.get("warehouse") == "None"
	assert defaults.get("remarks") == "None"


def _parents(tree):
	parents = {}
	for node in ast.walk(tree):
		for child in ast.iter_child_nodes(node):
			parents[child] = node
	return parents


def _inside_if_target(node, parents):
	child, parent = node, parents.get(node)
	while parent is not None:
		if isinstance(parent, ast.If) and ast.unparse(parent.test) == "target" and child in parent.body:
			return True
		child, parent = parent, parents.get(parent)
	return False


def test_the_warehouse_is_only_changed_when_one_was_asked_for():
	"""Outside ``if target`` the dialog's receipts would land in ``None``, or in the last
	scanned bin, instead of the order line's warehouse."""
	fn = _endpoint()
	parents = _parents(fn)
	writes = [
		(ast.unparse(target), node)
		for node in ast.walk(fn)
		if isinstance(node, ast.Assign)
		for target in node.targets
		if isinstance(target, ast.Attribute) and target.attr in ("warehouse", "set_warehouse")
	]
	# Both the header and every row: ERPNext never pushes the header into the rows.
	assert {name for name, _node in writes} == {"receipt.set_warehouse", "item.warehouse"}
	for name, node in writes:
		assert _inside_if_target(node, parents), f"{name} is assigned outside `if target:`"


def test_the_location_is_checked_after_the_permissions_and_before_the_receipt():
	fn = _endpoint()
	assigns = [
		node
		for node in ast.walk(fn)
		if isinstance(node, ast.Assign)
		and any(isinstance(t, ast.Name) and t.id == "target" for t in node.targets)
	]
	assert len(assigns) == 1, "target is computed once"
	assert f"{WAREHOUSE_HELPER}(" in ast.unparse(assigns[0].value)
	permission_lines = [
		call.lineno for name in ("has_permission", "check_permission") for call in _calls_named(fn, name)
	]
	assert permission_lines, "receive_items checks permissions"
	# A refusal about the location must not leak before a permission one.
	assert assigns[0].lineno > max(permission_lines)
	mapper = _calls_named(fn, "make_purchase_receipt")
	assert mapper and assigns[0].lineno < min(call.lineno for call in mapper)


def test_the_receiving_warehouse_refuses_what_erpnext_would():
	"""Group, disabled and another company's warehouse: ERPNext refuses all three later, in
	words about a Stock Ledger Entry. Here they are sentences, before a document exists."""
	body = _strip_prose(_function_source(WAREHOUSE_HELPER))
	for check in ("if not row:", "row.is_group", "row.disabled", "row.company != company"):
		assert check in body, f"_receiving_warehouse no longer checks {check}"
	assert body.count("frappe.throw(") >= 4

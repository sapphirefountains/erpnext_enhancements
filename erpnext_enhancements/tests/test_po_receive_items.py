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
	not matched as if it were code — the trap the chat work fell into three times."""
	tree = ast.parse(source)
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				if isinstance(body[0].value.value, str):
					segment = ast.get_source_segment(source, body[0].value) or ""
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

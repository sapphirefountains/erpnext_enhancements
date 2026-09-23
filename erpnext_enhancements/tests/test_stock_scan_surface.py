# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Stock Scan page's surface: what it exposes, how it posts stock, and how its pieces meet.

``/stock-scan`` posts **submitted** stock vouchers from a phone, one tap per save, with no
draft in between. So the properties that matter are the ones a review reads past and a
bench-free CI cannot otherwise see:

1. **The endpoint surface.** Every function in ``api/stock_scan.py`` the page can dial is
   ``@frappe.whitelist(methods=["POST"])``, never guest, and checks the page's roles before it
   does anything else; the client's ``M`` map and the whitelisted functions are the same set;
   ``boot_payload`` is not reachable over HTTP at all. A rename with no matching edit to
   ``transport.js`` is a 404 in front of a technician holding a part.
2. **The framework decides who may post.** The only ``ignore_permissions`` in the module is
   on the page's own log row. A voucher inserted, submitted or canceled past the framework
   would let anyone with the page's role post stock they could not post in the Desk.
3. **A retried save does not post twice.** ``take``/``add``/``move_here`` look the
   ``client_ref`` up before any voucher work and insert the log row (unique on it) before
   the voucher, in that order — asserted on the source, because the only other way to see it
   fail is a double-counted receipt.
4. **The vouchers are the shape ERPNext v16 accepts.** ``stock_entry_type`` set (``purpose``
   alone raises ``MandatoryError``), a difference account on every row (production's Company
   has none), an explicit rate on a Material Receipt ("Valuation Rate Missing" otherwise).
5. **The shell and the page controllers**: bundles only, the boot payload through
   ``| tojson``, ``no_cache``, and a signed-out visitor sent to log in *before* the role check,
   with the scanned label's query string intact.
6. **The client never renders HTML, never calls ``frappe.*``** (a website route has no Desk),
   and never changes the URL (iOS Safari asks for the camera again when it does).
7. **The Desk door, the log doctype and the settings**: one ``doctype_js["Warehouse"]`` entry
   whose buttons open the two pages with the query key the page understands; the log's
   ``client_ref`` unique and the log unwritable by hand; every new setting's default mirrored in
   ``DEFAULTS`` and backfilled onto the existing Single (a new field's default never reaches it).

Bench-free: ``ast``, ``json`` and ``re`` over the sources, plus ``stock_scan_rules`` (which
imports no frappe). Files the front end owns (``www/stock-scan.html``,
``public/js/stock_scan/*.js``) fail with a sentence naming the missing file rather than
skipping — a skipped surface test is a surface nobody checked.

Run: python -m unittest erpnext_enhancements.tests.test_stock_scan_surface -v
"""

import ast
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

APP = REPO_ROOT / "erpnext_enhancements"
API = APP / "api" / "stock_scan.py"
WWW = APP / "www"
PAGE_PY = WWW / "stock_scan.py"
PAGE_HTML = WWW / "stock-scan.html"
LABELS_PY = WWW / "warehouse_labels.py"
LABELS_HTML = WWW / "warehouse-labels.html"
PUBLIC = APP / "public"
CLIENT = PUBLIC / "js" / "stock_scan"
TRANSPORT = CLIENT / "transport.js"
ENTRY = PUBLIC / "js" / "stock_scan.bundle.js"
CSS = PUBLIC / "css" / "stock_scan.bundle.css"
FORM_SCRIPT = PUBLIC / "js" / "warehouse_stock_scan.js"
HOOKS = APP / "hooks.py"
LOG_DIR = APP / "inventory_enhancements" / "doctype" / "stock_scan_log"
SETTINGS_DIR = APP / "inventory_enhancements" / "doctype" / "inventory_scanner_settings"
BACKFILL = APP / "patches" / "backfill_stock_scan_settings_defaults.py"
PATCHES_TXT = APP / "patches.txt"

DOTTED = "erpnext_enhancements.api.stock_scan"

#: The endpoint contract from the build spec: ``M`` key -> (function, required args, optional args).
#: The page sends these as JSON keys, so a renamed parameter is a ``TypeError`` on every save.
CONTRACT = {
	"RESOLVE": ("resolve", {"code"}, {"warehouse"}),
	"LOCATION": ("get_location", {"warehouse"}, set()),
	"ITEM": ("get_item", {"item_code"}, {"warehouse"}),
	"SEARCH_ITEMS": ("search_items", {"query"}, {"warehouse"}),
	"SEARCH_LOCATIONS": ("search_locations", {"query"}, set()),
	"SEARCH_PROJECTS": ("search_projects", set(), {"query"}),
	"TAKE": ("take", {"item_code", "warehouse", "qty", "client_ref"}, {"project", "scanned_code"}),
	"ADD": (
		"add",
		{"item_code", "warehouse", "qty", "client_ref"},
		{"purchase_order_item", "without_po", "project", "scanned_code"},
	),
	"MOVE": (
		"move_here",
		{"item_code", "warehouse", "from_warehouse", "qty", "client_ref"},
		{"scanned_code"},
	),
	"UNDO": ("undo", {"log"}, set()),
	"RECENT": ("get_recent", set(), set()),
}

#: Keys Frappe consumes from the request before argument binding (``sid``, ``cmd``,
#: ``csrf_token``) or treats as a login (``usr``, ``pwd``). A parameter with one of these
#: names is never bound, or fakes "session expired".
RESERVED_REQUEST_KEYS = {"sid", "cmd", "csrf_token", "usr", "pwd"}

#: The saves, which post a submitted voucher.
SAVES = ("take", "add", "move_here")

#: The only functions allowed to pass ``ignore_permissions``: both write the page's own
#: ``Stock Scan Log`` row, never a voucher.
IGNORE_PERMISSIONS_ALLOWED = {"_begin_log", "undo"}

#: The client modules the spec names. ``lib/`` is vendored and excluded from every rule here.
CLIENT_MODULES = ("transport.js", "logic.js", "dom.js", "ui.js", "scanner.js", "app.js")

# The payload shapes the page reads (the spec's "Shapes"). Asserted as subsets: a key the
# server adds is harmless, a key it drops is ``undefined`` on a phone.
LOCATION_KEYS = {"warehouse", "warehouse_name", "trail", "company", "items", "truncated"}
LOCATION_ITEM_KEYS = {"item_code", "item_name", "image", "stock_uom", "on_hand"}
ITEM_KEYS = {
	"item_code",
	"item_name",
	"description",
	"image",
	"stock_uom",
	"whole_number",
	"blocked",
	"warehouse",
	"warehouse_name",
	"on_hand",
	"available",
	"elsewhere",
	"open_orders",
}
ELSEWHERE_KEYS = {"warehouse", "warehouse_name", "on_hand", "available"}
OPEN_ORDER_KEYS = {
	"purchase_order",
	"purchase_order_item",
	"idx",
	"supplier",
	"supplier_name",
	"schedule_date",
	"expected_delivery_date",
	"transaction_date",
	"uom",
	"conversion_factor",
	"ordered_qty",
	"pending_qty",
	"pending_stock_qty",
}
LOG_ROW_KEYS = {
	"name",
	"action",
	"status",
	"item_code",
	"item_name",
	"qty",
	"stock_uom",
	"warehouse",
	"warehouse_name",
	"from_warehouse",
	"from_warehouse_name",
	"project",
	"purchase_order",
	"voucher_type",
	"voucher_no",
	"posted_at",
	"needs_review",
	"can_undo",
	"undo_refusal",
}
SAVE_RESULT_KEYS = {"log", "item", "message", "repeated"}
SEARCH_ITEM_KEYS = {
	"item_code",
	"item_name",
	"image",
	"stock_uom",
	"is_stock_item",
	"on_hand_total",
	"on_hand_here",
}
SEARCH_LOCATION_KEYS = {"warehouse", "warehouse_name", "trail"}
SEARCH_PROJECT_KEYS = {"project", "project_name", "customer"}
BOOT_KEYS = {
	"user",
	"full_name",
	"is_supervisor",
	"settings",
	"recent",
	"initial",
	"today",
	"csrf_token",
	"build",
	"decoder_url",
}
BOOT_SETTINGS_KEYS = {"require_project_for_take", "undo_window_minutes"}
SCAN_KINDS = {"location", "item", "unknown"}


# ---------------------------------------------------------------------------
# Reading sources
# ---------------------------------------------------------------------------


def _read(path, owner="the front end"):
	"""A file's text, or a failure that names the missing file and who writes it."""
	if not path.is_file():
		raise AssertionError(
			f"{path.relative_to(REPO_ROOT).as_posix()} does not exist. It is written by {owner} "
			"to the Stock Scan build spec; this check cannot run until it is."
		)
	return path.read_text(encoding="utf-8")


def _tree(path):
	return ast.parse(_read(path, "the server side"))


def _functions(path):
	return {node.name: node for node in _tree(path).body if isinstance(node, ast.FunctionDef)}


def _whitelist_decorator(node):
	for dec in node.decorator_list:
		target = dec.func if isinstance(dec, ast.Call) else dec
		if getattr(target, "attr", "") == "whitelist" or getattr(target, "id", "") == "whitelist":
			return dec
	return None


def _whitelisted(path=None):
	return {name: node for name, node in _functions(path or API).items() if _whitelist_decorator(node)}


def _body_after_docstring(fn):
	body = list(fn.body)
	if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
		if isinstance(body[0].value.value, str):
			body = body[1:]
	return body


def _strip_prose(source):
	"""Source with docstrings and ``#`` comments removed, so a rule discussed in prose is not
	matched as if it were code (the house helper; see ``test_feedback_endpoint_surface``).

	Every docstring is located against the *original* source before any is removed: node
	positions are offsets into the text as parsed, and removing the module docstring first
	shifts every line after it.
	"""
	tree = ast.parse(source)
	docstrings = []
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				if isinstance(body[0].value.value, str):
					docstrings.append(ast.get_source_segment(source, body[0].value) or "")
	for doc in docstrings:
		if doc:
			source = source.replace(doc, "", 1)
	return re.sub(r"#.*$", "", source, flags=re.M)


def _strip_js_comments(source):
	"""JavaScript with ``//`` and ``/* */`` comments blanked, strings kept.

	A port of ``scripts/test_marketing_source_rules.js``'s ``stripComments``: the client's
	own comments say "no innerHTML" and "never frappe.*", and a scan that matched its prose
	would fail on the file that obeys the rule. An escape outside a string belongs to a
	regex literal (``/^https?:\\/\\//``), so it is copied through rather than read as a
	comment opener.
	"""
	out = []
	i, n = 0, len(source)
	quote = None
	while i < n:
		ch = source[i]
		two = source[i : i + 2]
		if quote:
			out.append(ch)
			if ch == "\\":
				out.append(source[i + 1 : i + 2])
				i += 2
				continue
			if ch == quote:
				quote = None
			i += 1
		elif ch == "\\":
			out.append(two)
			i += 2
		elif ch in "\"'`":
			quote = ch
			out.append(ch)
			i += 1
		elif two == "//":
			while i < n and source[i] != "\n":
				out.append(" ")
				i += 1
		elif two == "/*":
			end = source.find("*/", i + 2)
			end = n if end == -1 else end + 2
			out.append(re.sub(r"[^\n]", " ", source[i:end]))
			i = end
		else:
			out.append(ch)
			i += 1
	return "".join(out)


def _strip_jinja_comments(html):
	return re.sub(r"\{#.*?#\}", "", html, flags=re.S)


def _call_name(call):
	"""``frappe.new_doc`` / ``_post`` / ``se.append`` for a Call node."""
	try:
		return ast.unparse(call.func)
	except Exception:  # pragma: no cover - unparse handles every expression
		return ""


def _calls(node):
	"""Every call inside ``node`` as ``(line, col, dotted name, Call)``, in source order."""
	found = [
		(sub.lineno, sub.col_offset, _call_name(sub), sub)
		for sub in ast.walk(node)
		if isinstance(sub, ast.Call)
	]
	return sorted(found, key=lambda row: (row[0], row[1]))


def _first(node, name):
	positions = [(line, col) for line, col, called, _c in _calls(node) if called == name]
	return min(positions) if positions else None


def _dict_keys(node):
	"""The key set of a dict literal whose keys are all strings, else ``None``."""
	if not isinstance(node, ast.Dict) or not node.keys:
		return None
	if not all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
		return None
	return {k.value for k in node.keys}


def _dicts_in(node):
	return [keys for keys in (_dict_keys(sub) for sub in ast.walk(node)) if keys]


def _returned_dicts(fn):
	"""Key sets of dict literals a function returns: ``return {...}``, ``return [{...} for ...]``."""
	out = []
	for sub in ast.walk(fn):
		if not isinstance(sub, ast.Return) or sub.value is None:
			continue
		value = sub.value
		if isinstance(value, ast.ListComp):
			value = value.elt
		keys = _dict_keys(value)
		if keys:
			out.append(keys)
	return out


def _appended_dicts(fn):
	"""Key sets of dict literals a function ``.append``s to a list."""
	return [
		keys
		for _l, _c, name, call in _calls(fn)
		if name.endswith(".append") and call.args
		for keys in [_dict_keys(call.args[-1])]
		if keys
	]


def _module_constant(path, name):
	for node in _tree(path).body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == name for t in node.targets
		):
			return node.value
		if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
			return node.value
	return None


# -- the client's M map ------------------------------------------------------


def _object_body(code, start):
	"""The text between the ``{`` at ``start`` and its matching ``}``, strings skipped."""
	depth, i, quote = 0, start, None
	while i < len(code):
		ch = code[i]
		if quote:
			if ch == "\\":
				i += 2
				continue
			if ch == quote:
				quote = None
		elif ch in "\"'`":
			quote = ch
		elif ch == "{":
			depth += 1
		elif ch == "}":
			depth -= 1
			if depth == 0:
				return code[start + 1 : i]
		i += 1
	raise AssertionError("transport.js: the M map's braces never close")


def _split_entries(body):
	"""Top-level ``KEY: value`` entries of an object literal body."""
	entries, current, quote = [], [], None
	for ch in body:
		if quote:
			current.append(ch)
			if ch == quote:
				quote = None
			continue
		if ch in "\"'`":
			quote = ch
		if ch == ",":
			entries.append("".join(current))
			current = []
			continue
		current.append(ch)
	entries.append("".join(current))
	return [entry.strip() for entry in entries if entry.strip()]


def _js_string(expr, consts):
	"""Resolve an ``M`` value: a string literal, `` `${PREFIX}.name` ``, or ``PREFIX + ".name"``."""
	expr = expr.strip()
	literal = re.fullmatch(r"([\"'])([A-Za-z0-9_.]+)\1", expr)
	if literal:
		return literal.group(2)
	template = re.fullmatch(r"`\$\{\s*([A-Za-z_$][\w$]*)\s*\}([A-Za-z0-9_.]*)`", expr)
	if template and template.group(1) in consts:
		return consts[template.group(1)] + template.group(2)
	concat = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*\+\s*([\"'])([A-Za-z0-9_.]*)\2", expr)
	if concat and concat.group(1) in consts:
		return consts[concat.group(1)] + concat.group(3)
	return None


def _method_map():
	"""``{M key: dotted method}`` read from ``transport.js``'s ``export const M``.

	Read from the map's own braces rather than by grepping the file for method names: the
	module names endpoints in prose too, and a scan that cannot tell a comment from the
	dispatch table is satisfied by deleting the comment.
	"""
	code = _strip_js_comments(_read(TRANSPORT))
	consts = dict(
		(name, value)
		for name, _q, value in re.findall(
			r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*([\"'])([A-Za-z0-9_.]+)\2\s*;", code
		)
	)
	head = re.search(r"export\s+const\s+M\s*=\s*(?:Object\.freeze\(\s*)?\{", code)
	if not head:
		raise AssertionError("transport.js has no `export const M = {...}`; the spec names it")
	out, unreadable = {}, []
	for entry in _split_entries(_object_body(code, head.end() - 1)):
		match = re.fullmatch(r"([A-Za-z_$][\w$]*)\s*:\s*(.+)", entry, flags=re.S)
		if not match:
			unreadable.append(entry)
			continue
		resolved = _js_string(match.group(2), consts)
		if resolved is None:
			unreadable.append(entry)
			continue
		out[match.group(1)] = resolved
	if unreadable:
		raise AssertionError(
			"transport.js's M map has entries this scan cannot read (write each as a string "
			'literal, `${PREFIX}.name` or PREFIX + ".name"): ' + "; ".join(unreadable)
		)
	return out


def _client_sources():
	"""``{relative path: comment-stripped code}`` for the client, ``lib/`` excluded, entry included."""
	missing = [name for name in CLIENT_MODULES if not (CLIENT / name).is_file()]
	if missing:
		raise AssertionError(
			f"public/js/stock_scan/ is missing {missing}. The spec names six client modules; "
			"these checks cannot run until the front end has written them."
		)
	out = {
		f"public/js/stock_scan/{path.relative_to(CLIENT).as_posix()}": _strip_js_comments(
			path.read_text(encoding="utf-8")
		)
		for path in sorted(CLIENT.rglob("*.js"))
		if "lib" not in path.relative_to(CLIENT).parts
	}
	if ENTRY.is_file():
		out["public/js/stock_scan.bundle.js"] = _strip_js_comments(ENTRY.read_text(encoding="utf-8"))
	return out


# ---------------------------------------------------------------------------
# 1. The endpoint surface
# ---------------------------------------------------------------------------


class TestEndpointSurface(unittest.TestCase):
	def test_the_contract_has_eleven_endpoints_and_they_are_all_whitelisted(self):
		"""Anti-vacuity for every loop below, and the spec's table, both ways."""
		endpoints = _whitelisted()
		self.assertEqual(len(CONTRACT), 11)
		self.assertEqual(
			set(endpoints),
			{fn for fn, _req, _opt in CONTRACT.values()},
			"api/stock_scan.py's whitelisted functions and the spec's endpoint table differ. A new "
			"endpoint needs a row in CONTRACT (and in the client's M map); a removed one, the reverse.",
		)

	def test_every_endpoint_is_post_only_and_never_guest(self):
		"""These post and cancel stock vouchers. A GET would let an image tag do it, and put the
		quantities in access logs and the browser history."""
		for name, node in _whitelisted().items():
			with self.subTest(endpoint=name):
				self.assertEqual(
					[ast.unparse(d) for d in node.decorator_list],
					["frappe.whitelist(methods=['POST'])"],
					f'{name} must be exactly @frappe.whitelist(methods=["POST"]) — POST-only, never allow_guest',
				)

	def test_every_endpoint_checks_the_role_first(self):
		for name, node in _whitelisted().items():
			with self.subTest(endpoint=name):
				first = _body_after_docstring(node)[0]
				self.assertEqual(
					ast.unparse(first),
					"_check_access()",
					f"{name} must call _check_access() before anything else",
				)

	def test_the_role_check_is_the_pages(self):
		"""The page and its endpoints admit the same people: one constant, both places."""
		check = ast.unparse(_functions(API)["_check_access"])
		self.assertIn("rules.SCAN_ROLES", check)
		self.assertIn("frappe.PermissionError", check)
		page = ast.unparse(_functions(PAGE_PY)["get_context"])
		self.assertIn("rules.SCAN_ROLES", page)

	def test_boot_payload_is_not_reachable_over_http(self):
		"""It is the page's server-side render input: the caller's recent saves and the scanned
		location, resolved without the per-endpoint gate. ``www/stock_scan.py`` gates it."""
		functions = _functions(API)
		self.assertIn("boot_payload", functions)
		self.assertIsNone(_whitelist_decorator(functions["boot_payload"]))

	def test_parameters_match_the_contract(self):
		endpoints = _whitelisted()
		for key, (fn, required, optional) in CONTRACT.items():
			with self.subTest(endpoint=fn):
				args = endpoints[fn].args
				names = [a.arg for a in args.args]
				with_default = set(names[len(names) - len(args.defaults) :]) if args.defaults else set()
				self.assertEqual(set(names) - with_default, required, f"{fn}: required parameters ({key})")
				self.assertEqual(with_default, optional, f"{fn}: optional parameters ({key})")
				self.assertIsNone(args.vararg, f"{fn} takes *args")
				self.assertIsNone(args.kwarg, f"{fn} takes **kwargs: an unknown key would be swallowed")

	def test_no_parameter_uses_a_name_frappe_consumes(self):
		for fn, node in _whitelisted().items():
			with self.subTest(endpoint=fn):
				clash = {a.arg for a in node.args.args} & RESERVED_REQUEST_KEYS
				self.assertEqual(clash, set(), f"{fn} names a request key Frappe consumes first")


class TestTheClientDialsExactlyTheEndpoints(unittest.TestCase):
	"""``transport.js``'s ``M`` map against the whitelisted functions, by key and by path."""

	def test_the_map_has_exactly_the_contracts_keys(self):
		self.assertEqual(set(_method_map()), set(CONTRACT))

	def test_every_key_dials_its_endpoint(self):
		dialled = _method_map()
		for key, (fn, _req, _opt) in CONTRACT.items():
			with self.subTest(key=key):
				self.assertEqual(dialled.get(key), f"{DOTTED}.{fn}")

	def test_every_dialled_function_is_whitelisted(self):
		endpoints = set(_whitelisted())
		for key, path in _method_map().items():
			with self.subTest(key=key):
				self.assertTrue(
					path.startswith(DOTTED + "."), f"M.{key} = {path} is not in api/stock_scan.py"
				)
				self.assertIn(path.rsplit(".", 1)[-1], endpoints)


# ---------------------------------------------------------------------------
# 2-4. How the saves post stock
# ---------------------------------------------------------------------------


class TestPermissionsAreTheFrameworks(unittest.TestCase):
	def _uses(self):
		"""``{function: [line, ...]}`` for every ``ignore_permissions`` keyword or attribute."""
		found = {}
		for fn in ast.walk(_tree(API)):
			if not isinstance(fn, ast.FunctionDef):
				continue
			for sub in ast.walk(fn):
				hit = (isinstance(sub, ast.keyword) and sub.arg == "ignore_permissions") or (
					isinstance(sub, ast.Attribute) and sub.attr == "ignore_permissions"
				)
				if hit:
					found.setdefault(fn.name, []).append(getattr(sub, "lineno", 0))
		return found

	def test_only_the_log_row_is_written_past_the_permission_check(self):
		self.assertEqual(
			set(self._uses()),
			IGNORE_PERMISSIONS_ALLOWED,
			"ignore_permissions may appear only where the Stock Scan Log row is written "
			"(_begin_log, undo). A voucher posted or canceled with it lets anyone holding the "
			"page's role move stock they could not move in the Desk.",
		)

	def test_the_allowed_uses_are_on_the_log_not_a_voucher(self):
		functions = _functions(API)
		begin = ast.unparse(functions["_begin_log"])
		self.assertIn("log.insert(ignore_permissions=True)", begin)
		self.assertIn("LOG", begin)
		undo = ast.unparse(functions["undo"])
		self.assertIn("doc = frappe.get_doc(LOG,", undo)
		self.assertIn("doc.save(ignore_permissions=True)", undo)
		self.assertIn("voucher.cancel()", undo, "the voucher is canceled as the session user")

	def test_post_inserts_and_submits_as_the_session_user(self):
		post = ast.unparse(_functions(API)["_post"])
		self.assertIn("se.insert()", post)
		self.assertIn("se.submit()", post)

	def test_nothing_sidesteps_the_request(self):
		"""No second user, no flag-level bypass, and no commit: the log row and the voucher are
		one transaction, which is what makes a failed post take its log row with it."""
		code = _strip_prose(_read(API, "the server side"))
		self.assertNotIn("set_user", code)
		self.assertNotRegex(code, r"flags\.ignore_permissions")
		self.assertNotRegex(code, r"\.commit\(")

	def test_the_receipt_path_is_procurements(self):
		"""+ against an order line goes through ``receive_order_line`` -> ``receive_items``, the
		order's own Receive Items path, never a second receipt builder."""
		add = ast.unparse(_functions(API)["add"])
		self.assertIn("from erpnext_enhancements.api.procurement import receive_order_line", add)
		self.assertIn("receive_order_line(", add)
		self.assertNotIn("make_purchase_receipt", _strip_prose(_read(API, "the server side")))


class TestTheVouchersAreV16Shaped(unittest.TestCase):
	def test_stock_entry_type_is_set_and_purpose_is_not(self):
		fn = _functions(API)["_stock_entry"]
		assigned = {
			ast.unparse(target)
			for sub in ast.walk(fn)
			if isinstance(sub, ast.Assign)
			for target in sub.targets
		}
		self.assertIn("se.stock_entry_type", assigned)
		for sub in ast.walk(_tree(API)):
			if isinstance(sub, ast.Assign):
				for target in sub.targets:
					with self.subTest(line=sub.lineno):
						self.assertFalse(
							isinstance(target, ast.Attribute) and target.attr == "purpose",
							"purpose is fetched from stock_entry_type on insert; set the type",
						)

	def test_every_stock_entry_is_built_by_the_one_helper(self):
		"""So every one of them gets ``stock_entry_type``."""
		builders = []
		for fn in _functions(API).values():
			for _l, _c, name, call in _calls(fn):
				if name == "frappe.new_doc" and call.args and ast.unparse(call.args[0]) == "'Stock Entry'":
					builders.append(fn.name)
			for keys_node in ast.walk(fn):
				if isinstance(keys_node, ast.Dict) and any(
					isinstance(k, ast.Constant) and k.value == "doctype" for k in keys_node.keys
				):
					values = [ast.unparse(v) for v in keys_node.values]
					self.assertNotIn("'Stock Entry'", values, f"{fn.name} builds a Stock Entry by dict")
		self.assertEqual(builders, ["_stock_entry"])

	def _item_rows(self):
		"""``{function: (stock entry type, [row keys])}`` for each ``se.append("items", {...})``."""
		out = {}
		for fn in _functions(API).values():
			types = [
				call.args[0].value
				for _l, _c, name, call in _calls(fn)
				if name == "_stock_entry" and call.args and isinstance(call.args[0], ast.Constant)
			]
			rows = [
				_dict_keys(call.args[1])
				for _l, _c, name, call in _calls(fn)
				if name.endswith(".append")
				and len(call.args) == 2
				and isinstance(call.args[0], ast.Constant)
				and call.args[0].value == "items"
			]
			if rows:
				out[fn.name] = (types, rows)
		return out

	def test_the_three_stock_entries_are_the_three_saves(self):
		rows = self._item_rows()
		self.assertEqual(
			{name: types for name, (types, _r) in rows.items()},
			{"take": ["Material Issue"], "add": ["Material Receipt"], "move_here": ["Material Transfer"]},
		)
		for name, (_t, keys) in rows.items():
			with self.subTest(save=name):
				self.assertEqual(len(keys), 1)
				self.assertIsNotNone(keys[0], f"{name}'s item row must be a dict literal this test can read")

	def test_every_row_carries_a_difference_account(self):
		"""Production's Company has no Stock Adjustment Account, so ERPNext's own chain comes up
		empty and the entry refuses to insert without one."""
		for name, (_t, keys) in self._item_rows().items():
			with self.subTest(save=name):
				self.assertIn("expense_account", keys[0])

	def test_the_warehouse_sides_and_the_receipt_rate(self):
		rows = {name: keys[0] for name, (_t, keys) in self._item_rows().items()}
		self.assertIn("s_warehouse", rows["take"])
		self.assertNotIn("t_warehouse", rows["take"])
		self.assertIn("t_warehouse", rows["add"])
		self.assertNotIn("s_warehouse", rows["add"])
		self.assertIn(
			"basic_rate",
			rows["add"],
			"a Material Receipt without basic_rate is refused at submit (Valuation Rate Missing) "
			"or posts stock at zero value",
		)
		self.assertTrue({"s_warehouse", "t_warehouse"} <= rows["move_here"])


class TestARetriedSaveDoesNotPostTwice(unittest.TestCase):
	#: Calls that are voucher work: nothing of these may run before the client_ref is checked.
	VOUCHER_WORK = {"_begin_log", "_post", "receive_order_line", "_stock_entry", "frappe.new_doc"}
	POSTS = {"_post", "receive_order_line"}

	def test_the_reference_is_checked_before_any_voucher_work(self):
		for name in SAVES:
			with self.subTest(save=name):
				fn = _functions(API)[name]
				ref, saved = _first(fn, "_client_ref"), _first(fn, "_already_saved")
				self.assertIsNotNone(ref, f"{name} never validates its client_ref")
				self.assertIsNotNone(saved, f"{name} never looks for an earlier save with that client_ref")
				work = [(line, col) for line, col, called, _c in _calls(fn) if called in self.VOUCHER_WORK]
				self.assertTrue(work, f"{name} does no voucher work at all?")
				self.assertLess(ref, saved)
				self.assertLess(saved, min(work), f"{name} starts a voucher before checking its client_ref")

	def test_an_earlier_save_is_returned_not_repeated(self):
		for name in SAVES:
			with self.subTest(save=name):
				body = [ast.unparse(s) for s in _body_after_docstring(_functions(API)[name])[:4]]
				self.assertEqual(
					body,
					[
						"_check_access()",
						"ref = _client_ref(client_ref)",
						"done = _already_saved(ref)",
						"if done:\n    return done",
					],
				)

	def test_the_log_row_is_inserted_before_every_post(self):
		"""The log row carries the unique ``client_ref``; inserted first, a concurrent duplicate
		fails before any voucher exists. Branch-aware: each post's nearest preceding
		begin/post call must be a ``_begin_log``."""
		for name in SAVES:
			fn = _functions(API)[name]
			sequence = [
				called for _l, _c, called, _call in _calls(fn) if called in self.POSTS | {"_begin_log"}
			]
			posts = [i for i, called in enumerate(sequence) if called in self.POSTS]
			with self.subTest(save=name):
				self.assertTrue(posts, f"{name} never posts")
				for index in posts:
					self.assertGreater(index, 0, f"{name} posts before any _begin_log")
					self.assertEqual(
						sequence[index - 1],
						"_begin_log",
						f"{name}: {sequence[index]} is not preceded by its own _begin_log",
					)

	def test_every_post_is_recorded_on_its_log_row(self):
		for name in SAVES:
			fn = _functions(API)[name]
			sequence = [
				called for _l, _c, called, _call in _calls(fn) if called in self.POSTS | {"_finish_log"}
			]
			with self.subTest(save=name):
				for index, called in enumerate(sequence):
					if called in self.POSTS:
						self.assertLess(index + 1, len(sequence), f"{name}: {called} is never recorded")
						self.assertEqual(sequence[index + 1], "_finish_log")

	def test_refusals_come_before_the_log_row(self):
		"""Taking more than is on hand, a closed job, or a missing Stock Entry permission is a
		sentence, not a log row that the request then rolls back."""
		expectations = {
			"take": ("rules.check_take", "_project", "_require"),
			"move_here": ("_available", "_require"),
			# add's _require guards its without-PO branch (the receipt branch is checked by
			# receive_items itself), so it is compared with that branch's _begin_log: the last.
			"add": ("_require", "_receipt_rate"),
		}
		for name, before in expectations.items():
			fn = _functions(API)[name]
			begins = [(line, col) for line, col, called, _c in _calls(fn) if called == "_begin_log"]
			begin = max(begins) if name == "add" else min(begins)
			for call in before:
				with self.subTest(save=name, check=call):
					position = _first(fn, call)
					self.assertIsNotNone(position, f"{name} never calls {call}")
					self.assertLess(position, begin, f"{name} writes its log row before {call}")

	def test_add_refuses_to_guess_where_stock_came_from(self):
		"""Receiving stock that is on an order *without* the order double-counts it the day the
		order's own receipt arrives, so ``add`` with neither a line nor ``without_po`` posts
		nothing."""
		fn = _functions(API)["add"]
		guards = [
			sub
			for sub in ast.walk(fn)
			if isinstance(sub, ast.If) and ast.unparse(sub.test) == "not cint(without_po)"
		]
		self.assertEqual(len(guards), 1)
		self.assertIn("frappe.throw(", ast.unparse(guards[0]))

	def test_a_reference_still_being_posted_is_a_409_not_a_refusal(self):
		"""A retry that arrives while the first attempt is still submitting waits on the log's
		unique ``client_ref`` and then collides. That is not a refusal: the first attempt is
		about to commit. The page keeps a save's reference only for statuses that may have
		saved (``logic.mayHaveSaved``: 0, 409, 502-504), so a plain ``ValidationError`` (417)
		here would make it mint a fresh reference and post the save a second time."""
		fn = _functions(API)["_begin_log"]
		handlers = [sub for sub in ast.walk(fn) if isinstance(sub, ast.ExceptHandler)]
		self.assertTrue(handlers, "_begin_log no longer catches the unique-key collision")
		for handler in handlers:
			caught = ast.unparse(handler.type)
			with self.subTest(caught=caught):
				self.assertIn("UniqueValidationError", caught)
				throws = [ast.unparse(sub) for sub in ast.walk(handler) if isinstance(sub, ast.Call)]
				self.assertTrue(
					any(t.startswith("frappe.throw(") and "frappe.DuplicateEntryError" in t for t in throws),
					"the collision must be thrown as frappe.DuplicateEntryError (HTTP 409)",
				)
		logic = (CLIENT / "logic.js").read_text(encoding="utf-8")
		body = re.search(r"export function mayHaveSaved\(status\) \{(.*?)\n\}", logic, re.S)
		self.assertIsNotNone(body, "logic.js no longer defines mayHaveSaved(status)")
		self.assertIn("status === 409", body.group(1))

	def test_the_server_reference_rule_is_the_spec_rule(self):
		"""``scripts/test_stock_scan_client.mjs`` reads this literal to check the page's
		``mintRef()`` against it, so it is pinned here to the spec's rule."""
		value = _module_constant(API, "_CLIENT_REF")
		self.assertIsNotNone(value)
		self.assertEqual(ast.unparse(value), "re.compile('^[A-Za-z0-9._:-]{8,80}$')")


# ---------------------------------------------------------------------------
# What the page reads
# ---------------------------------------------------------------------------


class TestPayloadShapes(unittest.TestCase):
	"""The spec's shapes, as subsets of the dict literals the server builds."""

	def _fn(self, name):
		return _functions(API)[name]

	def assertCarries(self, found, needed, where):
		self.assertTrue(found, f"{where}: no dict literal found")
		self.assertTrue(
			any(needed <= keys for keys in found),
			f"{where} lacks {sorted(needed - max(found, key=lambda k: len(k & needed)))}",
		)

	def test_location(self):
		self.assertCarries(_returned_dicts(self._fn("_location_payload")), LOCATION_KEYS, "Location")
		self.assertCarries(_dicts_in(self._fn("_items_at")), LOCATION_ITEM_KEYS, "Location.items[]")

	def test_item(self):
		payload = [
			_dict_keys(sub.value)
			for sub in ast.walk(self._fn("_item_payload"))
			if isinstance(sub, ast.Assign) and ast.unparse(sub.targets[0]) == "payload"
		]
		self.assertCarries([k for k in payload if k], ITEM_KEYS, "Item")
		self.assertCarries(_appended_dicts(self._fn("_elsewhere")), ELSEWHERE_KEYS, "Item.elsewhere[]")
		self.assertCarries(
			_appended_dicts(self._fn("_open_order_lines")), OPEN_ORDER_KEYS, "Item.open_orders[]"
		)

	def test_log_row_and_save_result(self):
		self.assertCarries(_returned_dicts(self._fn("_log_row")), LOG_ROW_KEYS, "LogRow")
		self.assertCarries(_returned_dicts(self._fn("_result")), SAVE_RESULT_KEYS, "SaveResult")

	def test_searches(self):
		self.assertCarries(_returned_dicts(self._fn("search_items")), SEARCH_ITEM_KEYS, "search_items")
		self.assertCarries(
			_returned_dicts(self._fn("search_locations")), SEARCH_LOCATION_KEYS, "search_locations"
		)
		self.assertCarries(
			_returned_dicts(self._fn("search_projects")), SEARCH_PROJECT_KEYS, "search_projects"
		)

	def test_a_scan_resolves_to_one_of_three_kinds(self):
		for name in ("_resolve", "boot_payload"):
			kinds = set()
			for sub in ast.walk(self._fn(name)):
				keys = _dict_keys(sub)
				if not keys or "kind" not in keys:
					continue
				value = sub.values[[k.value for k in sub.keys].index("kind")]
				kinds.add(value.value if isinstance(value, ast.Constant) else ast.unparse(value))
			with self.subTest(function=name):
				self.assertEqual(kinds, SCAN_KINDS)

	def test_the_boot_payload(self):
		"""``boot_payload``'s keys plus the three the controller adds are the spec's boot."""
		boot = _returned_dicts(self._fn("boot_payload"))
		self.assertEqual(len(boot), 1, "boot_payload should return one dict literal")
		added = {
			target.slice.value
			for sub in ast.walk(_functions(PAGE_PY)["get_context"])
			if isinstance(sub, ast.Assign)
			for target in sub.targets
			if isinstance(target, ast.Subscript)
			and ast.unparse(target.value) == "boot"
			and isinstance(target.slice, ast.Constant)
		}
		self.assertEqual(added, {"csrf_token", "build", "decoder_url"})
		self.assertTrue(BOOT_KEYS <= boot[0] | added, f"the boot lacks {sorted(BOOT_KEYS - boot[0] - added)}")
		self.assertTrue(
			any(BOOT_SETTINGS_KEYS <= keys for keys in _dicts_in(self._fn("boot_payload"))),
			f"boot.settings lacks one of {sorted(BOOT_SETTINGS_KEYS)}",
		)
		page = ast.unparse(_functions(PAGE_PY)["get_context"])
		self.assertIn("boot = boot_payload(frappe.form_dict)", page)
		self.assertIn("context.boot = boot", page)
		self.assertIn("frappe.sessions.get_csrf_token()", page)

	def test_the_boot_reads_the_query_keys_the_labels_carry(self):
		source = ast.unparse(self._fn("boot_payload"))
		self.assertIn("rules.QUERY_KINDS", source)
		self.assertEqual(rules.QUERY_KINDS[0], ("w", "warehouse"), "labels print ?w=")


# ---------------------------------------------------------------------------
# 5. The page controllers and the shell
# ---------------------------------------------------------------------------


class TestThePageControllers(unittest.TestCase):
	CONTROLLERS = {PAGE_PY: "rules.SCAN_ROLES", LABELS_PY: "rules.LABEL_ROLES"}

	def test_no_cache_at_module_level(self):
		"""Both render the caller's own data; a cached render is someone else's page."""
		for path in self.CONTROLLERS:
			with self.subTest(controller=path.name):
				value = _module_constant(path, "no_cache")
				self.assertIsNotNone(value, f"{path.name} has no module-level no_cache")
				self.assertEqual(ast.unparse(value), "1")

	def test_a_guest_is_sent_to_log_in_before_the_role_check(self):
		"""A signed-out technician scanning a label must land on the login page and come back to
		that label — not on a permission error for Guest."""
		for path, roles in self.CONTROLLERS.items():
			with self.subTest(controller=path.name):
				fn = ast.unparse(_functions(path)["get_context"])
				self.assertIn("'Guest'", fn)
				# Through the shared helper, which encodes the whole path so a multi-parameter
				# query (?under=...&size=...&skip=...) survives the login page's own parsing.
				self.assertIn("rules.login_redirect(", fn)
				self.assertIn(
					"frappe.request.full_path", fn, "the query string (?w=...) must survive the login"
				)
				self.assertIn("raise frappe.Redirect", fn)
				self.assertIn(roles, fn)
				self.assertIn("frappe.PermissionError", fn)
				self.assertLess(fn.index("raise frappe.Redirect"), fn.index("frappe.get_roles()"))
				self.assertLess(fn.index("'Guest'"), fn.index("raise frappe.Redirect"))

	def test_the_decoder_ships_at_the_path_the_boot_names(self):
		value = _module_constant(PAGE_PY, "DECODER_PATH")
		self.assertIsInstance(value, ast.Constant)
		path = value.value
		prefix = "/assets/erpnext_enhancements/"
		self.assertTrue(path.startswith(prefix), path)
		self.assertTrue(path.endswith(".min.js"), "vendored as *.min.js, which .eslintignore exempts")
		on_disk = PUBLIC / path[len(prefix) :]
		self.assertTrue(on_disk.is_file(), f"DECODER_PATH points at {on_disk}, which does not exist")
		self.assertTrue(
			on_disk.with_name(on_disk.name.replace(".min.js", ".LICENSE")).is_file(),
			"a vendored library ships with its license",
		)
		self.assertGreater(on_disk.stat().st_size, 10_000, "the decoder is suspiciously small")

	def test_the_label_page_reads_the_keys_the_warehouse_form_sends(self):
		source = _read(LABELS_PY, "the server side")
		self.assertIn('getlist("w")', source)
		self.assertIn('form_dict.get("under")', source)


class TestTheShell(unittest.TestCase):
	def html(self):
		return _strip_jinja_comments(_read(PAGE_HTML))

	def test_it_loads_the_two_bundles_and_no_raw_asset_path(self):
		html = self.html()
		for bundle in ("stock_scan.bundle.js", "stock_scan.bundle.css"):
			with self.subTest(bundle=bundle):
				self.assertRegex(html, r"bundled_asset\(\s*['\"]" + re.escape(bundle) + r"['\"]\s*\)")
		self.assertNotIn(
			"/assets/erpnext_enhancements/",
			_read(PAGE_HTML),
			"raw /assets paths are served immutable for a year with no hash; the decoder URL arrives in the boot",
		)
		self.assertTrue(ENTRY.is_file(), f"{ENTRY.relative_to(REPO_ROOT)} is the bundle the shell loads")
		self.assertTrue(CSS.is_file(), f"{CSS.relative_to(REPO_ROOT)} is the stylesheet the shell loads")

	def test_the_boot_goes_in_through_tojson(self):
		html = self.html()
		self.assertRegex(
			html,
			r"window\.EE_STOCK_SCAN_BOOT\s*=\s*\{\{\s*boot\s*\|\s*tojson\s*\}\}",
			"the boot must be serialised by | tojson: item and supplier names are data, and tojson "
			"escapes </script> and quotes where a hand-built object literal would not",
		)
		boot = html.index("EE_STOCK_SCAN_BOOT")
		bundle = re.search(r"<script[^>]*bundled_asset\(\s*['\"]stock_scan\.bundle\.js", html)
		self.assertIsNotNone(bundle, "the bundle is not loaded by a <script src>")
		self.assertLess(boot, bundle.start(), "the boot must exist before the bundle reads it")

	def test_the_mount_point(self):
		html = self.html()
		root = re.search(r"<div[^>]*\bid=\"ee-stock-scan-root\"[^>]*>", html)
		self.assertIsNotNone(root, 'no <div id="ee-stock-scan-root">')
		self.assertIn("ee-ss-root", root.group(0), "the root carries the ee-ss-root class the tokens hang on")
		self.assertIn("<noscript>", html, "a bundle that fails to load must not leave a blank screen")

	def test_it_is_a_chrome_free_web_page(self):
		html = self.html()
		self.assertRegex(html, r"\{%\s*extends\s+['\"]templates/web\.html['\"]\s*%\}")
		for block in (
			"banner",
			"navbar",
			"header",
			"page_header",
			"footer",
			"footer_extension",
			"page_breadcrumbs",
		):
			with self.subTest(block=block):
				self.assertRegex(html, r"\{%\s*block\s+" + block + r"\s*%\}\s*\{%\s*endblock\s*%\}")

	def test_only_jinja_comments(self):
		"""An HTML comment ships to every phone; a Jinja one does not."""
		self.assertNotIn("<!--", _read(PAGE_HTML))

	def test_the_templates_parse(self):
		try:
			import jinja2
		except ImportError:  # pragma: no cover - CI installs jinja2
			self.skipTest("jinja2 is not installed (CI installs it)")
		env = jinja2.Environment()
		for path in (PAGE_HTML, LABELS_HTML):
			with self.subTest(template=path.name):
				env.parse(_read(path))

	def test_the_bundle_entry_mounts_the_app(self):
		entry = _strip_js_comments(_read(ENTRY))
		self.assertRegex(entry, r"import\s*\{\s*StockScanApp\s*\}\s*from\s*['\"]\./stock_scan/app\.js['\"]")
		self.assertIn("ee-stock-scan-root", entry)
		self.assertIn("EE_STOCK_SCAN_BOOT", entry)


# ---------------------------------------------------------------------------
# 6. The client's source rules
# ---------------------------------------------------------------------------


class TestClientSourceRules(unittest.TestCase):
	def assertNoLineMatches(self, pattern, why):
		"""Fail naming ``file: line N`` for every code line (comments stripped) matching ``pattern``."""
		pattern = re.compile(pattern)
		for path, code in _client_sources().items():
			with self.subTest(file=path):
				hits = [f"line {n}" for n, line in enumerate(code.splitlines(), 1) if pattern.search(line)]
				self.assertEqual(hits, [], f"{path}: {why}")

	def test_the_client_modules_exist(self):
		self.assertGreaterEqual(len(_client_sources()), len(CLIENT_MODULES))

	def test_no_html_is_ever_parsed_from_a_string(self):
		"""Item names, supplier names and server errors are data. Every node is built with
		createElement/textContent, so the rule needs no judgement at the call site."""
		self.assertNoLineMatches(
			r"\binnerHTML\b|\bouterHTML\b|\binsertAdjacentHTML\b|\bdocument\.write(ln)?\b",
			"renders HTML from a string",
		)

	def test_no_frappe_namespace(self):
		"""A website route does not load the Desk; ``frappe.*`` is undefined or a stub there."""
		self.assertNoLineMatches(r"\bfrappe\s*\.", "reaches for frappe.*")

	def test_the_url_never_changes(self):
		"""iOS Safari asks for the camera again whenever the URL changes, so the page keeps an
		in-memory back stack instead of history entries."""
		self.assertNoLineMatches(
			r"\b(pushState|replaceState)\s*\(|\blocation\.hash\s*=(?!=)", "changes the URL"
		)

	def test_no_random_uuid(self):
		"""Older Android WebViews lack ``crypto.randomUUID``; ``mintRef`` uses time + random."""
		self.assertNoLineMatches(r"randomUUID", "uses crypto.randomUUID")

	def test_the_decoder_path_is_not_hardcoded(self):
		"""The decoder URL arrives in the boot (``decoder_url``), so the client names no asset path."""
		self.assertNoLineMatches(r"/assets/", "hardcodes an /assets/ path")

	def test_the_client_imports_only_its_own_modules(self):
		for path, code in _client_sources().items():
			with self.subTest(file=path):
				for spec in re.findall(r"\bimport\b[^;]*?\bfrom\s*['\"]([^'\"]+)['\"]", code):
					self.assertTrue(
						spec.startswith("./"),
						f"{path} imports {spec}; the client is self-contained (no npm, no other app's modules)",
					)


# ---------------------------------------------------------------------------
# 7. The Desk door, the log doctype, the settings, the label sheets
# ---------------------------------------------------------------------------


class TestTheWarehouseFormDoor(unittest.TestCase):
	def _doctype_js(self):
		for node in ast.parse(HOOKS.read_text(encoding="utf-8")).body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "doctype_js" for t in node.targets
			):
				return node.value
		raise AssertionError("hooks.py has no doctype_js")

	def test_exactly_one_warehouse_entry(self):
		"""A duplicate dict key silently keeps only the last one."""
		mapping = self._doctype_js()
		entries = [
			value
			for key, value in zip(mapping.keys, mapping.values, strict=True)
			if isinstance(key, ast.Constant) and key.value == "Warehouse"
		]
		self.assertEqual(len(entries), 1)
		self.assertEqual(ast.literal_eval(entries[0]), ["public/js/warehouse_stock_scan.js"])
		self.assertTrue(FORM_SCRIPT.is_file(), "doctype_js paths are read with raise_not_found=False")

	def test_the_buttons_open_the_two_pages_with_the_keys_they_read(self):
		code = _strip_js_comments(_read(FORM_SCRIPT, "the server side"))
		self.assertIn('frappe.ui.form.on("Warehouse"', code)
		key = rules.QUERY_KINDS[0][0]
		for url in (f"/stock-scan?{key}=", f"/warehouse-labels?{key}=", "/warehouse-labels?under="):
			with self.subTest(url=url):
				self.assertIn(url, code)
		self.assertIn("encodeURIComponent(frm.doc.name)", code, "warehouse names carry spaces and slashes")


class TestTheLogDoctype(unittest.TestCase):
	def meta(self):
		return json.loads(_read(LOG_DIR / "stock_scan_log.json", "the server side"))

	def fields(self):
		return {f["fieldname"]: f for f in self.meta()["fields"]}

	def test_client_ref_is_unique(self):
		"""The idempotency key. Without the unique index a retry after a dropped connection
		posts the receipt twice."""
		self.assertEqual(self.fields()["client_ref"].get("unique"), 1)

	def test_rows_are_written_by_the_page_only(self):
		meta = self.meta()
		self.assertEqual(meta.get("in_create"), 1)
		creators = [p["role"] for p in meta["permissions"] if p.get("create")]
		self.assertEqual(creators, [], "nobody creates a log row by hand; the endpoints do")
		deleters = [p["role"] for p in meta["permissions"] if p.get("delete")]
		self.assertEqual(deleters, ["System Manager"], "the log is the undo and review history")

	def test_status_options_are_the_ones_undo_reads(self):
		self.assertEqual(tuple(self.fields()["status"]["options"].split("\n")), ("Posted", "Undone"))

	def test_a_fetched_field_cannot_turn_undo_into_an_error(self):
		"""Frappe v16 re-fetches every ``fetch_from`` field on each save of a non-submittable
		document (``BaseDocument.get_invalid_links`` -> ``set_fetch_from_value``, unless
		``fetch_if_empty``), and it does so in ``_validate_links`` — *before* the controller's
		``validate``. The controller refuses a change to any field outside its allow-lists, so a
		fetched field that is neither ``fetch_if_empty`` nor allow-listed turns Undo, and a
		Stock Manager's review save, into "Item Name cannot be changed on a Stock Scan Log" the
		day somebody edits the item's name."""
		allowed = set()
		for node in _tree(LOG_DIR / "stock_scan_log.py").body:
			if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
				if ast.unparse(node.value.func) == "frozenset":
					allowed |= set(ast.literal_eval(node.value.args[0]))
		self.assertTrue(allowed, "the controller's allow-lists were not found")
		refetched = sorted(
			name
			for name, field in self.fields().items()
			if field.get("fetch_from") and not field.get("fetch_if_empty") and name not in allowed
		)
		self.assertEqual(
			refetched,
			[],
			f"{refetched} are re-fetched on every save and refused by _keep_history_immutable when "
			'the source changes: give them "fetch_if_empty": 1 (history keeps the name as scanned), '
			"or let the controller ignore them",
		)

	def test_everything_the_endpoints_write_is_a_field(self):
		"""An unknown key on ``get_doc`` is dropped without a word, so a typo is a column that is
		always empty."""
		functions = _functions(API)
		written = set()
		begin = _dicts_in(functions["_begin_log"])
		self.assertTrue(begin)
		written |= max(begin, key=len) - {"doctype"}
		for fn in functions.values():
			for _l, _c, name, call in _calls(fn):
				if name == "_begin_log":
					written |= {k.arg for k in call.keywords if k.arg}
		written |= set().union(*_dicts_in(functions["_finish_log"]))
		undo_writes = self._undo_writes()
		written |= undo_writes
		unknown = sorted(written - set(self.fields()))
		self.assertEqual(unknown, [], f"the endpoints write {unknown}, which Stock Scan Log does not have")
		self.assertTrue({"project", "needs_review", "purchase_order", "from_warehouse"} <= written)

	def _undo_writes(self):
		return {
			target.attr
			for sub in ast.walk(_functions(API)["undo"])
			if isinstance(sub, ast.Assign)
			for target in sub.targets
			if isinstance(target, ast.Attribute)
			and isinstance(target.value, ast.Name)
			and target.value.id == "doc"
		}

	def test_undo_writes_only_what_the_controller_lets_it(self):
		"""The controller refuses a change to any field outside its allow-lists, so a field undo
		writes that is not in ``UNDO_FIELDS`` turns every Undo into an error."""
		constants = {}
		for node in _tree(LOG_DIR / "stock_scan_log.py").body:
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
				if isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "frozenset":
					constants[node.targets[0].id] = set(ast.literal_eval(node.value.args[0]))
		self.assertIn("UNDO_FIELDS", constants)
		writes = self._undo_writes()
		self.assertTrue(writes, "undo assigns nothing?")
		self.assertTrue(
			writes <= constants["UNDO_FIELDS"], f"undo writes {sorted(writes - constants['UNDO_FIELDS'])}"
		)
		for name, values in constants.items():
			with self.subTest(constant=name):
				self.assertTrue(
					values <= set(self.fields()), f"{name} names {sorted(values - set(self.fields()))}"
				)


class TestTheSettings(unittest.TestCase):
	NO_VALUE = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading"}

	def meta(self):
		return json.loads(_read(SETTINGS_DIR / "inventory_scanner_settings.json", "the server side"))

	def new_fields(self):
		"""The Stock Scan section's data fields: everything after ``stock_scan_section``."""
		meta = self.meta()
		order = meta["field_order"]
		self.assertIn("stock_scan_section", order)
		by_name = {f["fieldname"]: f for f in meta["fields"]}
		fields = [
			by_name[name]
			for name in order[order.index("stock_scan_section") + 1 :]
			if by_name[name]["fieldtype"] not in self.NO_VALUE
		]
		self.assertGreaterEqual(len(fields), 5)
		return fields

	@staticmethod
	def expected(field):
		default = field.get("default")
		if default in (None, ""):
			return None
		if field["fieldtype"] in ("Int", "Check"):
			return int(default)
		if field["fieldtype"] in ("Float", "Currency", "Percent"):
			return float(default)
		return default

	def defaults(self):
		value = _module_constant(SETTINGS_DIR / "inventory_scanner_settings.py", "DEFAULTS")
		self.assertIsInstance(value, ast.Dict)
		return ast.literal_eval(value)

	def test_every_new_field_has_a_matching_fallback(self):
		"""``get_settings()`` falls back to ``DEFAULTS`` for an unset field. A fallback that
		disagrees with the JSON — ``"30"`` for an Int, ``True`` for a Check — is a second
		answer to the same question."""
		defaults = self.defaults()
		for field in self.new_fields():
			name = field["fieldname"]
			with self.subTest(field=name):
				self.assertIn(name, defaults)
				want = self.expected(field)
				self.assertEqual(defaults[name], want)
				self.assertIs(type(defaults[name]), type(want))

	def test_no_fallback_for_a_field_that_does_not_exist(self):
		fields = {f["fieldname"] for f in self.meta()["fields"]}
		self.assertEqual(sorted(set(self.defaults()) - fields), [])

	def test_the_backfill_covers_exactly_the_fields_with_defaults(self):
		"""A new field's default never reaches a Single that already exists (CLAUDE.md), so every
		new field that has one is backfilled — and only those: the patch writes a field's own
		declared default, and a field without one would be skipped anyway."""
		value = _module_constant(BACKFILL, "FIELDS")
		self.assertIsNotNone(value, "the backfill patch has no FIELDS")
		fields = set(ast.literal_eval(value))
		with_default = {f["fieldname"] for f in self.new_fields() if self.expected(f) is not None}
		self.assertEqual(fields, with_default)
		self.assertEqual(
			ast.literal_eval(_module_constant(BACKFILL, "SETTINGS_DOCTYPE")), "Inventory Scanner Settings"
		)

	def test_the_backfill_is_registered(self):
		lines = [
			line.strip()
			for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()
			if line.strip() and not line.strip().startswith("#")
		]
		self.assertIn("erpnext_enhancements.patches.backfill_stock_scan_settings_defaults", lines)

	def test_the_backfill_never_overwrites_a_stored_value(self):
		code = _strip_prose(_read(BACKFILL, "the server side"))
		self.assertIn("select field from tabSingles", code)
		self.assertIn("set_single_value", code)
		self.assertNotIn(".save(", code)
		self.assertNotRegex(code, r"get_value\(\s*[\"']Singles", "tabSingles has no creation column")


class TestLabelPresets(unittest.TestCase):
	def test_every_preset_fits_its_page(self):
		self.assertGreaterEqual(len(rules.LABEL_PRESETS), 3)
		for key, preset in rules.LABEL_PRESETS.items():
			with self.subTest(preset=key):
				self.assertTrue(rules.preset_fits_page(preset))
				self.assertEqual(rules.label_preset(key), (key, preset))

	def test_a_preset_a_sixteenth_off_fails(self):
		"""The control: the check above must be able to fail."""
		base = rules.LABEL_PRESETS["avery-5160"]
		for field, delta in (("left", 0.0625), ("width", 0.01), ("column_gap", -0.0625), ("height", 0.06)):
			with self.subTest(field=field):
				broken = dict(base, **{field: base[field] + delta})
				self.assertFalse(rules.preset_fits_page(broken))

	def test_an_unknown_size_falls_back_to_the_default_sheet(self):
		self.assertIn(rules.DEFAULT_LABEL_PRESET, rules.LABEL_PRESETS)
		self.assertEqual(rules.label_preset("nonsense")[0], rules.DEFAULT_LABEL_PRESET)
		self.assertEqual(rules.label_preset(None)[0], rules.DEFAULT_LABEL_PRESET)


if __name__ == "__main__":
	unittest.main()

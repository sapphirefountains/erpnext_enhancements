# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Review Room's surface: its endpoints, its route, its frames and its bundle format.

The Review Room is a website app at ``/review`` (``www/review.py``), and everything it does goes
through ``api/design_review.py``. These are the properties that break silently:

1. **Every whitelisted endpoint is POST.** Note text and decisions in a query string land in
   access logs and ``Referer`` headers.
2. **Every method the app dials exists, and every endpoint is dialled** (set equality, as
   ``test_feedback_endpoint_surface`` argues). A rename on one side only is a 404 people see
   and CI does not.
3. **``/review/<anything>`` reaches the shell.** Without the ``website_route_rules`` entry a
   shared link or a refresh is a 404, and Frappe caches that 404 until the next deploy.
4. **Frames never get ``allow-scripts``, and always carry the content policy.** The pair
   ``allow-scripts`` + ``allow-same-origin`` lets a frame lift its own sandbox; the policy is
   what stops a screen fetching anything. Both are one careless edit away.
5. **The example bundle, the schema and the importer agree** on the format string and the kit
   names, so the documented example is one the importer takes.
6. **No Long Text holds content.** v1.570.0 stored screens in Long Text fields and Frappe's
   save-time sanitizer stripped their SVG and CSS. Design doctypes may not grow one back for
   screen markup without this test being changed on purpose.

Bench-free: AST and text reads only.

Run: python -m unittest erpnext_enhancements.tests.test_design_review_surface
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
API = APP / "api" / "design_review.py"
JS = APP / "public" / "js" / "design_review"
TRANSPORT = JS / "transport.js"
FRAMES = JS / "frames.js"
HOOKS = APP / "hooks.py"
MODULE = APP / "design_review"
PREFIX = "erpnext_enhancements.api.design_review."


def _whitelisted():
	"""``{name: methods}`` for every ``@frappe.whitelist`` function in the API module."""
	out = {}
	for node in ast.parse(API.read_text(encoding="utf-8")).body:
		if not isinstance(node, ast.FunctionDef):
			continue
		for dec in node.decorator_list:
			if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "whitelist":
				methods = next((kw.value for kw in dec.keywords if kw.arg == "methods"), None)
				out[node.name] = [e.value for e in methods.elts] if isinstance(methods, ast.List) else None
	return out


def _dialled():
	text = TRANSPORT.read_text(encoding="utf-8")
	block = text[text.index("export const M = {") : text.index("};", text.index("export const M = {"))]
	return set(re.findall(r'"erpnext_enhancements\.api\.design_review\.(\w+)"', block))


class TestEndpoints(unittest.TestCase):
	def test_every_endpoint_is_post_only(self):
		found = _whitelisted()
		self.assertTrue(found)
		for name, methods in found.items():
			self.assertEqual(methods, ["POST"], name)

	def test_the_app_dials_exactly_the_endpoints(self):
		self.assertEqual(_dialled(), set(_whitelisted()))

	def test_the_app_calls_methods_only_through_the_map(self):
		for path in JS.glob("*.js"):
			if path == TRANSPORT:
				continue
			self.assertNotIn(PREFIX, path.read_text(encoding="utf-8"), path.name)


class TestRouteAndShell(unittest.TestCase):
	def test_every_sub_path_reaches_the_shell(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		rules = next(
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "website_route_rules"
		)
		self.assertIn({"from_route": "/review/<path:review_path>", "to_route": "review"}, rules)
		self.assertTrue((APP / "www" / "review.html").exists())
		self.assertTrue((APP / "www" / "review.py").exists())

	def test_the_shell_hands_the_app_a_csrf_token(self):
		"""A website page gets no token unless its controller asks; without one every POST is refused."""
		controller = (APP / "www" / "review.py").read_text(encoding="utf-8")
		html = (APP / "www" / "review.html").read_text(encoding="utf-8")
		self.assertIn("context.csrf_token = frappe.sessions.get_csrf_token()", controller)
		self.assertIn("csrf_token: {{ csrf_token | tojson }}", html)
		self.assertIn("X-Frappe-CSRF-Token", TRANSPORT.read_text(encoding="utf-8"))

	def test_the_shell_loads_hashed_bundles(self):
		html = (APP / "www" / "review.html").read_text(encoding="utf-8")
		self.assertIn("bundled_asset('design_review.bundle.js')", html)
		self.assertIn("bundled_asset('design_review.bundle.css')", html)
		self.assertNotIn("/assets/erpnext_enhancements/js/", html)


class TestFrames(unittest.TestCase):
	def test_frames_never_run_script(self):
		code = re.sub(r"//[^\n]*|/\*.*?\*/", "", FRAMES.read_text(encoding="utf-8"), flags=re.S)
		self.assertIn('setAttribute("sandbox", "allow-same-origin")', code)
		self.assertNotIn("allow-scripts", code)
		for path in JS.glob("*.js"):
			body = re.sub(r"//[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
			self.assertNotIn("allow-scripts", body, path.name)

	def test_frames_carry_the_policy(self):
		code = FRAMES.read_text(encoding="utf-8")
		self.assertRegex(code, r"default-src 'none'")
		self.assertIn("Content-Security-Policy", code)


class TestFormat(unittest.TestCase):
	def test_example_schema_and_importer_agree(self):
		importer = (MODULE / "importer.py").read_text(encoding="utf-8")
		fmt = re.search(r'^FORMAT = "([^"]+)"', importer, re.M).group(1)
		schema = json.loads((MODULE / "bundle.schema.json").read_text(encoding="utf-8"))
		example = json.loads((MODULE / "examples" / "minimal-bundle.json").read_text(encoding="utf-8"))
		self.assertEqual(schema["properties"]["format"]["const"], fmt)
		self.assertEqual(example["format"], fmt)
		kits = re.search(r"^KITS = (\{[^}]*\})", (MODULE / "content.py").read_text(encoding="utf-8"), re.M).group(1)
		kits = ast.literal_eval(kits)
		self.assertEqual(set(schema["properties"]["kit"]["enum"]), set(kits))
		for filename in kits.values():
			self.assertTrue((MODULE / "kit" / filename).exists(), filename)
		self.assertIn(example["kit"], kits)

	def test_kits_load_nothing_from_outside(self):
		for path in (MODULE / "kit").glob("*.css"):
			css = path.read_text(encoding="utf-8")
			self.assertNotRegex(css, r"url\s*\(|@import", path.name)

	def test_no_design_doctype_stores_screen_markup_in_long_text(self):
		for path in (MODULE / "doctype").glob("*/*.json"):
			meta = json.loads(path.read_text(encoding="utf-8"))
			for field in meta.get("fields", []):
				if field.get("fieldtype") in ("Long Text", "Text Editor", "HTML Editor"):
					self.assertNotRegex(field["fieldname"], r"html|screen|stylesheet|css|flow|content", path.name)


if __name__ == "__main__":
	unittest.main()

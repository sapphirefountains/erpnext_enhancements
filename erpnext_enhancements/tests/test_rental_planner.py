"""The Rental Planner page (v1.563.0): Back/Forward behaviour and its server surface.

``scripts/test_rental_planner_history.js`` runs the real page script against a model of the v16
router and session history (Next, the flow's Back button, browser Back and Forward, deep links,
a reload, a booking). It is run from here so the behaviour and the source rules below run
together. Bench-free: no ``frappe`` stub — the server checks read the source.

Run: python -m unittest erpnext_enhancements.tests.test_rental_planner -v
"""

import ast
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
PAGE = APP / "asset_management" / "page" / "rental_planner"
SCRIPT = PAGE / "rental_planner.js"
SERVER = APP / "asset_management" / "rental_planner.py"
HARNESS = REPO / "scripts" / "test_rental_planner_history.js"


def _code(path):
	"""The script with comments stripped: an absence assertion must not trip on its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _functions(path):
	tree = ast.parse(path.read_text(encoding="utf-8"))
	return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _decorators(node):
	return [ast.unparse(d) for d in node.decorator_list]


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_history_harness_passes(self):
		result = subprocess.run([shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=120)
		output = (result.stdout + result.stderr)[-4000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)

	def test_the_page_script_parses(self):
		result = subprocess.run([shutil.which("node"), "--check", str(SCRIPT)], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stderr)


class TestTheRouterOwnsHistory(unittest.TestCase):
	"""Frappe's router owns the Desk's history; a page writing its own gets a popstate the
	router re-renders as whatever the URL still names."""

	def test_no_history_writes_of_its_own(self):
		code = _code(SCRIPT)
		for token in ("pushState", "replaceState", "popstate", "beforeunload", "location.href ="):
			self.assertNotIn(token, code)

	def test_every_screen_is_a_route(self):
		code = _code(SCRIPT)
		self.assertIn('steps: ["dates", "fountains", "customer", "review"]', code)
		self.assertIn('this.go([RP.route, "new", step]);', code)
		self.assertIn("frappe.pages[RP.route].on_page_show", code)

	def test_a_correction_replaces_rather_than_pushes(self):
		self.assertIn("frappe.route_flags.replace_route = true", _code(SCRIPT))
		self.assertIn("delete frappe.route_flags.replace_route", _code(SCRIPT))

	def test_storage_is_guarded(self):
		"""Private windows and blocked site data throw on storage access; the page must still work."""
		code = _code(SCRIPT)
		for use in re.finditer(r"window\.(sessionStorage|localStorage)", code):
			before = code[max(0, use.start() - 200) : use.start()]
			self.assertIn("try {", before, f"unguarded {use.group(0)}")


class TestThePage(unittest.TestCase):
	def test_page_record(self):
		page = json.loads((PAGE / "rental_planner.json").read_text(encoding="utf-8"))
		self.assertEqual(page["name"], "rental-planner")
		self.assertEqual(page["module"], "Asset Management")
		roles = {r["role"] for r in page["roles"]}
		# The same teams that can read a Rental Booking; anyone else would see an empty board.
		booking = json.loads(
			(APP / "asset_management" / "doctype" / "rental_booking" / "rental_booking.json").read_text(encoding="utf-8")
		)
		self.assertEqual(roles, {p["role"] for p in booking["permissions"] if p.get("read")})

	def test_the_page_is_not_named_like_a_workspace(self):
		"""A Page named like a workspace never renders (the slug collides)."""
		for path in APP.rglob("workspace/*/*.json"):
			doc = json.loads(path.read_text(encoding="utf-8"))
			self.assertNotEqual(str(doc.get("name", "")).lower().replace(" ", "-"), "rental-planner", path)

	def test_the_workspace_links_the_page(self):
		ws = json.loads(
			(APP / "asset_management" / "workspace" / "asset_management" / "asset_management.json").read_text(
				encoding="utf-8"
			)
		)
		self.assertIn(("Page", "rental-planner"), {(s.get("type"), s.get("link_to")) for s in ws["shortcuts"]})


class TestTheServerSurface(unittest.TestCase):
	def test_endpoints_check_permission_first(self):
		fns = _functions(SERVER)
		for name, perm in (("get_timeline", '"read"'), ("create_rental", '"create"')):
			with self.subTest(endpoint=name):
				self.assertTrue(any(d.startswith("frappe.whitelist") for d in _decorators(fns[name])))
				first = ast.unparse(fns[name].body[1] if isinstance(fns[name].body[0], ast.Expr) else fns[name].body[0])
				self.assertIn("frappe.has_permission('Rental Booking'", first)
				self.assertIn(perm.replace('"', "'"), first)

	def test_create_is_post_only(self):
		self.assertIn("frappe.whitelist(methods=['POST'])", _decorators(_functions(SERVER)["create_rental"]))

	def test_the_payload_cannot_set_what_the_booking_decides(self):
		tree = ast.parse(SERVER.read_text(encoding="utf-8"))
		allowed = next(
			ast.literal_eval(n.value)
			for n in tree.body
			if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "BOOKING_FIELDS" for t in n.targets)
		)
		for field in ("status", "confirmed_on", "hold_expires_on", "total_amount", "title", "name", "docstatus"):
			self.assertNotIn(field, allowed)

	def test_a_new_project_is_typed_both_ways(self):
		"""What kind of job it is is read from project_type in some places and the value stream in others."""
		source = ast.unparse(_functions(SERVER)["create_event_project"])
		self.assertIn("project.project_type = EVENTS", source)
		self.assertIn("'custom_value_stream'", source)
		self.assertIn("frappe.has_permission('Project', 'create', throw=True)", source)


if __name__ == "__main__":
	unittest.main()

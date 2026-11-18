"""Bench-free tests for the escaping of record values in rendered HTML (v1.826.4).

Record values (file names, supplier names, notes typed into a portal form, a lesson's text) reach
a page in four ways, and each now has one rule:

* an inline ``<script>`` boot or data island: ``utils/script_json.script_json``, which writes
  ``<``, ``>`` and ``&`` as ``\\u`` escapes (the kiosk, wall display, itinerary and training preview);
* a Jinja template: ``| e`` on every record value printed as text or into an attribute
  (``www/rentals.html``), checked by rendering the real template body;
* a string of HTML built in Python: ``frappe.utils.escape_html`` (the Task form's child-task tree);
* a string of HTML built in the browser: escaped values, and an address used as a link only when it
  is a site file or an http(s) page (the node checks are ``scripts/test_safe_file_link.mjs``).

``api/procurement.save_item_link`` is also covered here: it stores only an address the guided Item
flow would accept, and no longer saves the Item past its permission check.

Each suite loads the one function it needs from the source file with ``ast`` over a stand-in
``frappe``, so nothing here installs a ``frappe`` stub for other suites to inherit.

Run: python -m unittest erpnext_enhancements.tests.test_output_escaping -v
"""

import ast
import html
import json
import re
import sys
import types
import unittest
from pathlib import Path

import markupsafe

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import item_flow_rules

APP = REPO_ROOT / "erpnext_enhancements"


def _read(path):
	return Path(path).read_text(encoding="utf-8")


def _load_functions(path, names, namespace):
	"""The named top-level functions of a source file, executed over ``namespace``."""
	tree = ast.parse(_read(path))
	wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
	assert {n.name for n in wanted} == set(names), (path, names)
	exec(compile(ast.Module(body=wanted, type_ignores=[]), str(path), "exec"), namespace)
	return namespace


def _strip_comments(source):
	return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", "//", "*", "/*")))


class TestScriptJson(unittest.TestCase):
	SOURCE = APP / "utils" / "script_json.py"

	def _script_json(self):
		fake = types.SimpleNamespace(as_json=lambda value: json.dumps(value, indent=1, sort_keys=True))
		return _load_functions(self.SOURCE, ["script_json"], {"frappe": fake})["script_json"]

	def test_the_three_characters_are_written_as_escapes_and_read_back_the_same(self):
		script_json = self._script_json()
		value = {"a": "</script><script>x</script>", "b": "R&D <b>", "n": [1, 2.5, None, True]}
		out = script_json(value)
		for char in "<>&":
			self.assertNotIn(char, out)
		self.assertIn("\\u003c/script\\u003e", out)
		self.assertEqual(json.loads(out), value)

	def test_a_value_without_the_characters_is_printed_as_frappe_prints_it(self):
		script_json = self._script_json()
		value = {"employee": "EMP-1", "rows": [{"qty": 3, "ok": False}], "note": "quote \" and 'apostrophe'"}
		self.assertEqual(script_json(value), json.dumps(value, indent=1, sort_keys=True))

	def test_the_pages_print_their_boot_through_it(self):
		for name, line in (
			("kiosk", "context.boot_json = script_json(boot)"),
			("wall", "context.boot_json = script_json(get_wall_dashboard_data())"),
			("itinerary", "context.boot_json = script_json(boot)"),
		):
			with self.subTest(page=name):
				source = _read(APP / "www" / f"{name}.py")
				self.assertIn(line, source)
				self.assertIn("from erpnext_enhancements.utils.script_json import script_json", source)
				self.assertNotIn("def script_json", source)
				self.assertNotIn("frappe.as_json(boot", _strip_comments(source))
		preview = _read(APP / "www" / "training_preview.py")
		self.assertIn("from erpnext_enhancements.utils.script_json import script_json", preview)
		self.assertNotIn("frappe.as_json", _strip_comments(preview))
		self.assertIn("return script_json(", preview)

	def test_the_templates_still_read_the_boot_the_same_way(self):
		"""Only the encoder changed: the templates print the string and the page scripts read the
		same names, so the structure the front end parses is the structure it always had."""
		self.assertIn("window.KIOSK_BOOT = {{ boot_json | safe }};", _read(APP / "www" / "kiosk.html"))
		self.assertIn("window.WALL_BOOT = {{ boot_json | safe }};", _read(APP / "www" / "wall.html"))
		self.assertIn("{{ draft_json | safe }}</script>", _read(APP / "www" / "training_preview.html"))
		self.assertIn("window.KIOSK_BOOT", _read(APP / "public" / "js" / "kiosk" / "app.js"))
		self.assertIn("WALL_BOOT", _read(APP / "public" / "js" / "wall" / "app.js"))
		self.assertIn("JSON.parse(raw.textContent", _read(APP / "www" / "training_preview.html"))


class TestRentalsTemplate(unittest.TestCase):
	TEMPLATE = APP / "www" / "rentals.html"
	HOSTILE = '"><img src=x onerror=alert(1)></textarea><b>&amp;'

	def _render(self, context):
		import jinja2

		templates = {
			"templates/web.html": "{% block title %}{% endblock %}{% block head_include %}{% endblock %}"
			"{% block navbar %}{% endblock %}{% block page_content %}{% endblock %}{% block script %}{% endblock %}",
			"erpnext_enhancements/templates/includes/sf_portal_light.html": "",
			"erpnext_enhancements/templates/includes/sf_portal_nav.html": "",
			"rentals": _read(self.TEMPLATE),
		}
		env = jinja2.Environment(loader=jinja2.DictLoader(templates), autoescape=False)
		env.globals["_"] = lambda text: text
		env.globals["bundled_asset"] = lambda name: "/assets/" + name
		env.globals["frappe"] = types.SimpleNamespace(format=lambda value, df=None, doc=None: str(value))
		return env.get_template("rentals").render(**context)

	def _detail(self, hostile):
		return {
			"name": hostile,
			"event_name": hostile,
			"status_label": hostile,
			"status": "Booked",
			"venue": hostile,
			"fountains": [hostile],
			"accessories": [hostile],
			"delivery_datetime": "d1",
			"takedown_datetime": "d2",
			"event_start_datetime": None,
			"hold_expires_on": None,
			"agreement": None,
			"invoices": [{"name": hostile, "custom_rental_invoice_kind": hostile, "due_date": "d", "outstanding_amount": 1}],
			"editable": True,
			"site_prep": {
				"site_contact_name": hostile,
				"site_contact_phone": hostile,
				"surface": "",
				"site_access": hostile,
				"power_source": hostile,
				"water_source": hostile,
				"site_prep_notes": hostile,
			},
		}

	def test_a_detail_page_shows_every_record_value_as_text(self):
		out = self._render(
			{
				"detail": self._detail(self.HOSTILE),
				"bookings": [],
				"surfaces": ["", "Grass"],
				"csrf_token": "tok",
				"not_found": False,
			}
		)
		self.assertNotIn("<img src=x", out)
		self.assertNotIn("</textarea><b>", out)
		self.assertIn(str(markupsafe.escape(self.HOSTILE)), out)

	def test_a_list_page_shows_every_record_value_as_text(self):
		out = self._render(
			{
				"detail": None,
				"bookings": [
					{"name": 'B-1"><i>', "event_name": self.HOSTILE, "status_label": self.HOSTILE,
					 "delivery_datetime": "d1", "takedown_datetime": "d2"}
				],
				"surfaces": [],
				"csrf_token": "tok",
			}
		)
		self.assertNotIn("<img src=x", out)
		self.assertNotIn("<i>", out)
		self.assertIn(str(markupsafe.escape(self.HOSTILE)), out)

	def test_ordinary_values_read_as_they_did(self):
		detail = self._detail("Garden Party")
		detail["site_prep"]["site_access"] = "Gate code on the left\nPark at the back"
		out = self._render({"detail": detail, "bookings": [], "surfaces": ["", "Grass"], "csrf_token": "t"})
		self.assertIn("Garden Party", out)
		self.assertIn(">Gate code on the left\nPark at the back</textarea>", out)
		self.assertIn('value="Garden Party"', out)

	def test_every_record_value_in_the_source_goes_through_the_filter(self):
		"""The rendered checks above cover the branches a context reaches; this one holds the
		source to the rule, so a value added later cannot be printed bare."""
		source = _read(self.TEMPLATE)
		exempt = ("_(", "frappe.format", "tojson", "bundled_asset", "super()", "urlencode")
		for match in re.finditer(r"\{\{(.*?)\}\}", source):
			expression = match.group(1).strip()
			if any(token in expression for token in exempt):
				continue
			with self.subTest(expression=expression):
				self.assertIn("| e", expression)


class TestChildTaskTree(unittest.TestCase):
	SOURCE = APP / "task_enhancements" / "doctype" / "task" / "task.py"

	def _tree(self):
		fake = types.SimpleNamespace(
			utils=types.SimpleNamespace(escape_html=html.escape),
			_dict=dict,
		)
		return _load_functions(
			self.SOURCE, ["_build_task_tree_html"], {"frappe": fake, "quote": lambda text: text}
		)["_build_task_tree_html"]

	def test_status_and_assignee_are_shown_as_text(self):
		task = types.SimpleNamespace(
			name="TASK-1", subject="Pour <slab>", status='<img src=x onerror=alert(1)>',
			allocated_to='"><script>x</script>', exp_start_date="", exp_end_date="", expected_time="", children=[],
		)
		out = self._tree()([task])
		self.assertNotIn("<img", out)
		self.assertNotIn("<script>", out)
		self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
		self.assertIn("Pour &lt;slab&gt;", out)

	def test_ordinary_values_read_as_they_did(self):
		task = types.SimpleNamespace(
			name="TASK-1", subject="Pour slab", status="Open", allocated_to="crew@example.com",
			exp_start_date="", exp_end_date="", expected_time="", children=[],
		)
		out = self._tree()([task])
		self.assertIn('<div class="task-tree-col task-tree-col-status">Open</div>', out)
		self.assertIn('<div class="task-tree-col task-tree-col-user">crew@example.com</div>', out)

	def test_the_error_text_is_escaped_and_the_endpoint_gained_no_check(self):
		source = _read(self.SOURCE)
		self.assertIn('{frappe.utils.escape_html(str(e))}', source)
		self.assertNotIn("{e}</div>", source)
		body = source[source.index("def get_child_tasks_html") : source.index("def _get_all_descendants")]
		self.assertNotIn("has_permission", body)
		self.assertNotIn("check_permission", body)


class TestSaveItemLink(unittest.TestCase):
	SOURCE = APP / "api" / "procurement.py"

	def _api(self, roles=("Purchase User",), may_write=True, existing=False):
		calls = types.SimpleNamespace(permission=[], set_value=[], saved=[], appended=[], throws=[])

		class Refused(Exception):
			pass

		class Item:
			def append(self, table, row):
				calls.appended.append((table, row))

			def save(self, *args, **kwargs):
				calls.saved.append(kwargs)

		def has_permission(doctype, ptype, doc=None, throw=False):
			calls.permission.append((doctype, ptype, doc, throw))
			if not may_write:
				raise Refused("no write")
			return True

		def throw(message, exc=None):
			calls.throws.append(message)
			raise ValueError(message)

		fake = types.SimpleNamespace(
			whitelist=lambda *a, **k: (lambda fn: fn),
			get_roles=lambda: list(roles),
			throw=throw,
			PermissionError=PermissionError,
			has_permission=has_permission,
			get_doc=lambda doctype, name: Item(),
			db=types.SimpleNamespace(
				exists=lambda doctype, filters: "ROW-1" if existing else None,
				set_value=lambda *args: calls.set_value.append(args),
			),
		)
		namespace = {
			"frappe": fake,
			"_": lambda text: text,
			"ALLOWED_ROLES": frozenset({"Purchase User", "System Manager"}),
			"purchase_url_problem": item_flow_rules.purchase_url_problem,
		}
		_load_functions(self.SOURCE, ["_require_purchasing_access", "save_item_link"], namespace)
		return namespace["save_item_link"], calls, Refused

	def test_a_web_address_is_stored(self):
		save, calls, _ = self._api()
		self.assertTrue(save("ITEM-1", "Supplier A", "https://supplier.example/p/1"))
		self.assertEqual(calls.appended[0][1]["purchase_url"], "https://supplier.example/p/1")
		self.assertEqual(calls.permission, [("Item", "write", "ITEM-1", True)])
		self.assertEqual(calls.saved, [{}], "saved through the framework's own permission checks")

	def test_a_row_that_exists_is_updated_after_the_permission_check(self):
		save, calls, _ = self._api(existing=True)
		self.assertTrue(save("ITEM-1", "Supplier A", "  http://supplier.example/p/1 "))
		self.assertEqual(calls.set_value, [("Item Supplier", "ROW-1", "purchase_url", "http://supplier.example/p/1")])
		self.assertEqual(len(calls.permission), 1)

	def test_an_address_that_is_not_a_web_address_is_refused_before_anything_is_written(self):
		for bad in ("javascript:alert(1)", "supplier.example/p", "data:text/html,x", "ftp://x.example/a", "https://a b.example/"):
			with self.subTest(url=bad):
				save, calls, _ = self._api()
				with self.assertRaises(ValueError):
					save("ITEM-1", "Supplier A", bad)
				self.assertEqual((calls.set_value, calls.saved, calls.appended), ([], [], []))

	def test_a_buyer_without_write_on_the_item_is_refused(self):
		save, calls, Refused = self._api(may_write=False)
		with self.assertRaises(Refused):
			save("ITEM-1", "Supplier A", "https://supplier.example/p/1")
		self.assertEqual((calls.set_value, calls.saved, calls.appended), ([], [], []))

	def test_the_role_gate_and_the_blank_case_are_unchanged(self):
		save, calls, _ = self._api(roles=("Guest",))
		with self.assertRaises(ValueError):
			save("ITEM-1", "Supplier A", "https://supplier.example/p/1")
		self.assertIn("not permitted", calls.throws[0])
		save, calls, _ = self._api()
		self.assertIsNone(save("ITEM-1", "Supplier A", ""))
		self.assertEqual(calls.permission, [])

	def test_the_decorator_and_signature_are_untouched(self):
		source = _read(self.SOURCE)
		self.assertIn("@frappe.whitelist()\ndef save_item_link(item_code, supplier, url):", source)
		body = source[source.index("def save_item_link") : source.index("def cascade_expected_delivery_date")]
		self.assertNotIn("ignore_permissions", _strip_comments(body))


class TestBrowserSources(unittest.TestCase):
	"""The browser halves are run by scripts/test_safe_file_link.mjs; these hold the wiring."""

	def test_the_sidebar_and_comment_box_build_their_rows_through_the_helper(self):
		js = APP / "public" / "js"
		sidebar = _read(js / "erpnext_enhancements.js")
		self.assertIn('from "./global_enhancements/safe_file_link.js"', sidebar)
		self.assertIn("sidebarAttachmentRowHtml(file_name, file_url, size, icon, frappe.utils.escape_html)", sidebar)
		self.assertNotIn('<a href="${file_url}"', sidebar)
		comments = _read(js / "global_comments.js")
		self.assertIn('from "./global_enhancements/safe_file_link.js"', comments)
		self.assertNotIn('href="${uploaded_file.file_url}"', comments)
		self.assertNotIn('href="${file.file_url}"', comments)

	def test_the_global_bundle_still_imports_both_files(self):
		bundle = _read(APP / "public" / "js" / "erpnext_enhancements.bundle.js")
		self.assertIn('import "./erpnext_enhancements.js";', bundle)
		self.assertIn('import "./global_comments.js";', bundle)

	def test_the_purchase_links_have_no_inline_handlers(self):
		code = _strip_comments(_read(APP / "public" / "js" / "procurement_links.js"))
		self.assertNotIn("onclick=", code)
		self.assertIn("data-item-code=", code)
		self.assertIn("erpnext_enhancements.procurement.edit_link = function", code)

	def test_the_intake_preview_frames_only_site_files_and_https(self):
		code = _read(APP / "public" / "js" / "accounting_intake" / "document_intake.js")
		self.assertIn(r"/^(\/files\/|\/private\/files\/|https:\/\/)/", code)
		self.assertLess(code.index(r"\/private\/files\/"), code.index("<iframe"))

	def test_the_node_checks_run_in_ci(self):
		ci = _read(REPO_ROOT / ".github" / "workflows" / "ci.yml")
		self.assertIn("node scripts/test_safe_file_link.mjs", ci)
		self.assertIn("erpnext_enhancements.tests.test_output_escaping", ci)


if __name__ == "__main__":
	unittest.main()

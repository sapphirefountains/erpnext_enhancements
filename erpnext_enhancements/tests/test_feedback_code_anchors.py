"""The code anchors the Enhancement Request breakdown sends Triton (WI-079 slice 3). Bench-free.

``product_feedback/code_anchors.py`` tells the planner where in the code a request points: the
``www/`` page behind a route, the doctype's fields and controller, the module README, and the
CHANGELOG lines that name them. ``breakdown.build_payload`` carries it as request schema 2.
Four properties matter and none is visible from a breakdown that works:

* **No document data, ever.** No docname, no field value, no query string. The document name
  used to travel as ``context.docname``; it is gone, and so is the record segment of the path
  (``/desk/item/PUMP-001`` carried the same name). The tests plant a sentinel in every place a
  value could come from and assert it never reaches the payload, and that the only tables the
  anchors query are ``DocType`` and ``Property Setter``.
* **Deterministic.** The same input gives the same bytes, or the before/after measurement is
  noise.
* **Bounded.** At most 40,000 characters, whatever the input.
* **Real.** The pure helpers run against the repository's own files: ``breakdown.py`` is
  outlined, ``www/kiosk.py`` resolved, and the real CHANGELOG searched for "Enhancement Request".

Its own CI step: it installs a ``frappe`` stub in ``setUpModule`` (both modules import frappe at
module level), and a stub must not leak into the stub-free suites.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_code_anchors -v
"""

import ast
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

ca = None
breakdown = None
S = None  # the stub's world, reset per test
_saved = {}
_CORE_APP = None  # a temp directory standing in for the erpnext app

SECRET_DOCNAME = "PUMP-SECRET-0042"
SECRET_QUERY = "owner=someone%40example.com&token=QUERY-SECRET-9"
SECRET_VALUE = "FIELD-VALUE-SECRET-7"
VERSION = "1.527.0"


def _real_hook(name):
	"""A literal hook from the real hooks.py, read with ast: importing it pulls in monkeypatches."""
	tree = ast.parse((APP_DIR / "hooks.py").read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(f"hooks.py has no {name}")


def _er_meta_fields():
	schema = json.loads(
		(APP_DIR / "product_feedback/doctype/enhancement_request/enhancement_request.json").read_text(
			encoding="utf-8"
		)
	)
	return schema["fields"]


#: A core doctype with the three things the app doctype lacks: a permlevel field, a Password
#: field, a custom field. And a `default` that must never be sent.
EMPLOYEE_PAY_FIELDS = [
	{"fieldname": "details_section", "fieldtype": "Section Break", "label": "Details"},
	{"fieldname": "employee", "fieldtype": "Link", "label": "Employee", "options": "Employee", "reqd": 1},
	{"fieldname": "col", "fieldtype": "Column Break"},
	{
		"fieldname": "pay_rate",
		"fieldtype": "Currency",
		"label": "Pay Rate",
		"permlevel": 1,
		"default": SECRET_VALUE,
	},
	{"fieldname": "api_secret", "fieldtype": "Password", "label": "API Secret"},
	{"fieldname": "intro", "fieldtype": "HTML", "options": "<p>layout</p>"},
	{
		"fieldname": "grade",
		"fieldtype": "Select",
		"label": "Grade",
		"options": "\n".join(f"G{i:03d}" for i in range(200)),
	},
	{"fieldname": "custom_crew", "fieldtype": "Data", "label": "Crew", "is_custom_field": 1, "read_only": 1},
]


class World:
	def __init__(self):
		self.db_calls = []  # (method, doctype)
		self.doctypes = {
			"Enhancement Request": {"module": "Product Feedback", "fields": _er_meta_fields()},
			"Employee Pay": {"module": "HR", "fields": EMPLOYEE_PAY_FIELDS, "is_submittable": 1},
		}
		self.request_row = {
			"name": "ER-2026-00077",
			"title": "Item form is slow",
			"request_type": "Bug",
			"impact": "Nice to have",
			"description": "It hangs.",
			"steps_to_reproduce": "Open it.",
			"context_url": f"/desk/enhancement-request/{SECRET_DOCNAME}?{SECRET_QUERY}#frag",
			"context_doctype": "Enhancement Request",
			"context_docname": SECRET_DOCNAME,
			"context_app_version": "1.526.0",
		}
		self.request_fields_asked = None
		self.property_setters = []
		self.hooks = {
			"website_route_rules": _real_hook("website_route_rules"),
			"doc_events": {
				"Employee Pay": {
					"validate": ["erpnext_enhancements.hr_enhancements.pay.validate"],
					"on_update": ["erpnext_enhancements.quality.routing.on_update"],
				}
			},
			"doctype_js": {"Employee Pay": ["public/js/employee_pay.js"]},
			"doctype_list_js": {"Employee Pay": ["public/js/employee_pay_list.js"]},
			"override_doctype_class": {
				"Employee Pay": [
					"erpnext_enhancements.old.EmployeePay",
					"erpnext_enhancements.hr.EmployeePay",
				]
			},
		}
		self.app_path_fails = False
		self.inserted = []
		self.set_values = []
		self.saved_docs = []
		self.commits = 0


class _Meta:
	def __init__(self, name, spec):
		self._values = {"name": name, **{k: v for k, v in spec.items() if k != "fields"}}
		self.fields = [dict(f) for f in spec["fields"]]

	def get(self, key, default=None):
		return self._values.get(key, default)


class _Row(dict):
	def __getattr__(self, key):
		return self.get(key)


class _FakeDoc:
	def __init__(self, name):
		self.name = name
		self.tables = {}

	def set(self, key, value):
		self.tables[key] = list(value)

	def append(self, key, value):
		self.tables.setdefault(key, []).append(value)

	def save(self, ignore_permissions=False):
		S.saved_docs.append(self)


def _install():
	frappe = types.ModuleType("frappe")

	def get_app_path(app, *parts):
		if S is not None and S.app_path_fails:
			raise RuntimeError("no bench")
		base = APP_DIR if app == "erpnext_enhancements" else Path(_CORE_APP) / app
		return str(base.joinpath(*parts))

	frappe.get_app_path = get_app_path
	frappe.scrub = lambda text: (text or "").replace(" ", "_").replace("-", "_").lower()
	frappe.get_attr = lambda path: VERSION if path.endswith("__version__") else None
	frappe.local = types.SimpleNamespace(
		module_app={"product_feedback": "erpnext_enhancements", "hr": "erpnext"}
	)
	frappe.session = types.SimpleNamespace(user="reviewer@example.com")

	def get_value(doctype, name=None, fields=None, as_dict=False, **kwargs):
		S.db_calls.append(("get_value", doctype))
		if doctype == "DocType":
			for known in S.doctypes:
				if known.lower() == str(name).lower():
					return known
			return None
		if doctype == "Enhancement Request":
			S.request_fields_asked = list(fields or [])
			return _Row({key: S.request_row.get(key) for key in fields})
		if doctype == "Project":
			return _Row({"name": name, "project_name": "Board", "notes": ""})
		# Anything else is a user doctype, and its value is the sentinel.
		return SECRET_VALUE

	def set_value(doctype, name, values, update_modified=True):
		S.set_values.append((doctype, name, dict(values)))

	def commit():
		S.commits += 1

	frappe.db = types.SimpleNamespace(
		get_value=get_value,
		set_value=set_value,
		commit=commit,
		get_single_value=lambda doctype, field: 1,
		sql=lambda *a, **k: [[SECRET_VALUE]],
	)

	def get_meta(name):
		S.db_calls.append(("get_meta", name))
		return _Meta(name, S.doctypes[name])

	def get_all(doctype, filters=None, fields=None, **kwargs):
		S.db_calls.append(("get_all", doctype))
		if doctype == "Property Setter":
			return [_Row(row) for row in S.property_setters if row.get("doc_type") == filters.get("doc_type")]
		return [_Row({"name": SECRET_VALUE, "value": SECRET_VALUE})]

	def get_hooks(hook=None, default="_KEEP_DEFAULT_LIST", app_name=None):
		value = S.hooks.get(hook)
		if value is None:
			return [] if default == "_KEEP_DEFAULT_LIST" else default
		return copy.deepcopy(value)

	class _UsageDoc:
		def __init__(self, values):
			self.values = values

		def insert(self, ignore_permissions=False):
			S.inserted.append(self.values)

	def get_doc(arg, name=None):
		if isinstance(arg, dict):
			return _UsageDoc(arg)
		return _FakeDoc(name)

	frappe.get_meta = get_meta
	frappe.get_all = get_all
	frappe.get_hooks = get_hooks
	frappe.get_doc = get_doc
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""

	utils = types.ModuleType("frappe.utils")

	def cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	utils.cint = cint
	utils.now_datetime = lambda: "2026-09-24 12:00:00"
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document
	frappe.model = model

	for name in ("frappe", "frappe.utils", "frappe.model", "frappe.model.document"):
		_saved[name] = sys.modules.get(name)
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document


_OURS = (
	"erpnext_enhancements.product_feedback.code_anchors",
	"erpnext_enhancements.product_feedback.breakdown",
	"erpnext_enhancements.product_feedback.codemap",
	"erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings",
)


def setUpModule():
	global ca, breakdown, _CORE_APP
	_CORE_APP = tempfile.mkdtemp()
	controller = Path(_CORE_APP, "erpnext", "hr", "doctype", "employee_pay")
	controller.mkdir(parents=True)
	(controller / "employee_pay.py").write_text(
		'"""Employee pay."""\n\nclass EmployeePay(Document):\n\tdef validate(self):\n\t\tpass\n',
		encoding="utf-8",
		newline="\n",
	)
	_install()
	for name in _OURS:
		sys.modules.pop(name, None)
	from erpnext_enhancements.product_feedback import breakdown as _breakdown
	from erpnext_enhancements.product_feedback import code_anchors as _ca

	ca = _ca
	breakdown = _breakdown


def tearDownModule():
	for name, mod in _saved.items():
		if mod is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = mod
	for name in _OURS:
		sys.modules.pop(name, None)


class Base(unittest.TestCase):
	def setUp(self):
		global S
		S = World()


def _dump(value):
	return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------------------ pure helpers


class TestParsePath(Base):
	def test_scheme_host_query_and_fragment_go(self):
		self.assertEqual(ca.parse_path("https://erp.example.com/kiosk?x=1#top"), ("/kiosk", "web"))
		self.assertEqual(ca.parse_path("/kiosk#top?x=1"), ("/kiosk", "web"))
		self.assertEqual(ca.parse_path("//erp.example.com/kiosk"), ("/kiosk", "web"))

	def test_a_desk_form_keeps_the_doctype_and_drops_the_record(self):
		self.assertEqual(ca.parse_path(f"/desk/item/{SECRET_DOCNAME}?{SECRET_QUERY}"), ("/desk/item", "desk"))
		self.assertEqual(
			ca.parse_path("/app/sales-invoice/new-sales-invoice-abc"), ("/app/sales-invoice", "desk")
		)
		self.assertEqual(ca.parse_path("/app/Form/Item/ITEM-1"), ("/app/Form/Item", "desk"))
		self.assertEqual(ca.parse_path("/desk"), ("/desk", "desk"))
		self.assertEqual(ca.parse_path("/APP"), ("/APP", "desk"))

	def test_a_known_view_stays_and_a_saved_board_name_does_not(self):
		self.assertEqual(ca.parse_path("/desk/task/view/report"), ("/desk/task/view/report", "desk"))
		self.assertEqual(
			ca.parse_path("/desk/task/view/kanban/Nik's Board"), ("/desk/task/view/kanban", "desk")
		)
		self.assertEqual(ca.parse_path("/desk/task/view/nonsense"), ("/desk/task", "desk"))

	def test_a_web_subtree_keeps_its_first_segment(self):
		self.assertEqual(ca.parse_path("/feedback/request/ER-2026-00001"), ("/feedback", "web"))
		self.assertEqual(ca.parse_path("/private/files/salary.pdf"), ("/private", "web"))
		self.assertEqual(ca.parse_path("/application"), ("/application", "web"))

	def test_empty_is_none_and_root_is_the_home_page(self):
		for empty in ("", None, "   ", "?x=1", "#frag"):
			self.assertEqual(ca.parse_path(empty), ("", None), empty)
		self.assertEqual(ca.parse_path("/"), ("/", "web"))
		self.assertEqual(ca.parse_path("https://erp.example.com"), ("/", "web"))

	def test_neither_a_path_nor_an_absolute_url_is_refused(self):
		# Taken as a path, the first segment would be the host, and it would be sent.
		for text in (
			"erp.example.com/desk/item",
			f"erp.example.com/desk/item/{SECRET_DOCNAME}?{SECRET_QUERY}",
			"https:erp.example.com/desk/item",
			"desk/item",
			"kiosk",
			"http://[broken/kiosk",
		):
			self.assertEqual(ca.parse_path(text), ("", None), text)
			self.assertEqual(ca.desk_doctype_slug(text), "", text)
		self.assertEqual(ca.build_anchors("erp.example.com/desk/enhancement-request/X", ""), {})


class TestDeskSlugs(Base):
	def test_a_slug_becomes_a_spaced_name_first(self):
		self.assertEqual(ca.desk_slug_to_candidates("sales-invoice"), ["sales invoice", "sales-invoice"])
		self.assertEqual(ca.desk_slug_to_candidates("Sales%20Invoice"), ["Sales Invoice"])
		self.assertEqual(ca.desk_slug_to_candidates("item"), ["item"])

	def test_route_words_are_not_doctypes(self):
		for word in ("query-report", "print", "view", "workspace", "List", "Form", "page", "report", ""):
			self.assertEqual(ca.desk_slug_to_candidates(word), [], word)

	def test_the_doctype_slug_steps_over_form_words(self):
		self.assertEqual(ca.desk_doctype_slug("/desk/sales-invoice/SINV-1"), "sales-invoice")
		self.assertEqual(ca.desk_doctype_slug("/app/Form/Sales Invoice/SINV-1"), "Sales Invoice")
		self.assertEqual(ca.desk_doctype_slug("/desk/print/Item/ITEM-1"), "Item")
		self.assertEqual(ca.desk_doctype_slug("/desk/query-report/Stock Balance"), "query-report")
		self.assertEqual(ca.desk_doctype_slug("/kiosk"), "")
		self.assertEqual(ca.desk_doctype_slug("/desk"), "")

	def test_a_route_rule_maps_a_subtree_to_its_page(self):
		rules = _real_hook("website_route_rules")
		self.assertEqual(ca.route_rule_target("/feedback/request/ER-1", rules), "feedback")
		self.assertEqual(ca.route_rule_target("/marketing/post/X", rules), "marketing")
		self.assertEqual(ca.route_rule_target("/feedback", rules), "")
		self.assertEqual(ca.route_rule_target("/kiosk", rules), "")


class TestOutline(Base):
	def test_the_real_breakdown_module(self):
		source = (APP_DIR / "product_feedback/breakdown.py").read_text(encoding="utf-8")
		out = ca.outline_python(source, ca.MAX_OUTLINE_CHARS)
		self.assertTrue(out.startswith("The background worker that asks Triton for a work breakdown"))
		self.assertIn("build_payload(request_name, *, target_projects, known_tasks, max_tasks)", out)
		self.assertIn("run_breakdown(request_name)  # Ask Triton to break one request down", out)
		self.assertLessEqual(len(out), ca.MAX_OUTLINE_CHARS)
		# A table of contents, not the file: no body line survives.
		self.assertNotIn("frappe.enqueue(", out)

	def test_classes_bases_methods_and_decorators(self):
		source = (
			'"""First paragraph\nstill first.\n\nSecond paragraph."""\n'
			"import frappe\n\n"
			"@frappe.whitelist(methods=['POST'])\n"
			"def submit(values, attachments=None, *args, flag=True, **kwargs):\n"
			'\t"""File one.\n\n\tMore."""\n\n'
			"class Thing(Document, Mixin):\n"
			"\t@property\n"
			"\tdef label(self):\n"
			'\t\t"""The label."""\n'
			"\tasync def fetch(self, *, retries=3):\n"
			"\t\tpass\n"
		)
		out = ca.outline_python(source)
		lines = out.splitlines()
		self.assertEqual(lines[0], "First paragraph still first.")
		self.assertNotIn("Second paragraph", out)
		self.assertIn("class Thing(Document, Mixin):", lines)
		self.assertIn("    @property label(self)  # The label.", lines)
		self.assertIn("    async fetch(self, *, retries=3)", lines)
		self.assertIn(
			"@frappe.whitelist submit(values, attachments=None, *args, flag=True, **kwargs)  # File one.",
			lines,
		)
		# Classes first, then functions.
		self.assertLess(out.index("class Thing"), out.index("submit("))

	def test_unparseable_source_is_empty(self):
		self.assertEqual(ca.outline_python("def broken(:\n"), "")
		self.assertEqual(ca.outline_python("\0"), "")

	def test_the_cap_holds_at_a_line_boundary(self):
		source = "\n".join(f"def function_number_{i}(a, b):\n\t'''Does thing {i}.'''\n" for i in range(400))
		out = ca.outline_python(source, 1000)
		self.assertLessEqual(len(out), 1000)
		self.assertTrue(out.splitlines()[-1].startswith("function_number_"))


class TestChangelog(Base):
	@classmethod
	def setUpClass(cls):
		cls.sections = ca.changelog_sections((REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

	def test_sections_are_split_on_release_headings_newest_first(self):
		self.assertGreater(len(self.sections), 200)
		version = (APP_DIR / "__init__.py").read_text(encoding="utf-8").split('"')[1]
		self.assertEqual(self.sections[0]["version"], version)
		self.assertNotIn("## [", self.sections[0]["body"])
		self.assertRegex(self.sections[0]["date"], r"^\d{4}-\d{2}-\d{2}$")

	def test_real_sections_naming_enhancement_request(self):
		# The sections that name it, found by a plain substring test, so this does not depend on
		# how recently a release mentioned the feature: `changelog_excerpts` reads only the
		# newest CHANGELOG_SECTIONS_SCANNED, and handed the whole file it would find nothing
		# once 200 releases went by without a mention. The window has its own synthetic test.
		naming = [
			s
			for s in self.sections
			if "Enhancement Request" in s["body"] or "enhancement_request" in s["body"].lower()
		]
		self.assertGreaterEqual(len(naming), 5, "the history only grows; the filter is broken")
		terms = [("Enhancement Request", True), ("enhancement_request", False)]
		excerpts = ca.changelog_excerpts(naming, terms, 5)
		self.assertEqual(len(excerpts), 5)
		order = [s["version"] for s in self.sections]
		self.assertEqual(
			[e["version"] for e in excerpts], sorted((e["version"] for e in excerpts), key=order.index)
		)
		for excerpt in excerpts:
			self.assertLessEqual(len(excerpt["excerpt"]), ca.MAX_CHANGELOG_EXCERPT_CHARS)
			text = excerpt["excerpt"]
			self.assertTrue(
				"Enhancement Request" in text or "enhancement_request" in text.lower(), excerpt["version"]
			)

	def test_only_matching_lines_come_and_a_bullet_brings_its_children(self):
		body = (
			"**Lead paragraph about something else.**\n\n"
			"### Added\n\n"
			"- **Enhancement Request fields:**\n"
			"  - `breakdown_stats`, the measurement.\n"
			"    Wrapped line.\n"
			"- Unrelated bullet.\n"
			"  - Its child mentions Item.\n"
		)
		sections = [{"version": "9.9.9", "date": "2026-01-01", "body": body}]
		[excerpt] = ca.changelog_excerpts(sections, [("Enhancement Request", True)], 5)
		self.assertIn("breakdown_stats", excerpt["excerpt"])
		self.assertIn("Wrapped line.", excerpt["excerpt"])
		self.assertNotIn("Unrelated", excerpt["excerpt"])
		self.assertNotIn("Lead paragraph", excerpt["excerpt"])

	def test_case_rules(self):
		sections = [
			{
				"version": "1.0.0",
				"date": "d",
				"body": "- fixed the enhancement request list\n- see WWW/KIOSK.PY\n",
			}
		]
		self.assertEqual(ca.changelog_excerpts(sections, [("Enhancement Request", True)], 5), [])
		[hit] = ca.changelog_excerpts(sections, [("www/kiosk.py", False)], 5)
		self.assertIn("KIOSK", hit["excerpt"])

	def test_a_route_path_is_matched_as_a_path(self):
		sections = [
			{"version": "1.0.2", "date": "d", "body": "- `/pay-card` changed\n"},
			{"version": "1.0.1", "date": "d", "body": "- `www/pay.py` changed\n"},
			{"version": "1.0.0", "date": "d", "body": "- `/pay` and `/pay?x` changed\n"},
		]
		hits = ca.changelog_excerpts(sections, [("/pay", False)], 5)
		self.assertEqual([h["version"] for h in hits], ["1.0.1", "1.0.0"])

	def test_only_the_newest_sections_are_read_and_at_most_the_limit(self):
		sections = [{"version": f"1.0.{i}", "date": "d", "body": "- nothing"} for i in range(250)]
		sections[230]["body"] = "- Enhancement Request"
		self.assertEqual(ca.changelog_excerpts(sections, [("Enhancement Request", True)], 5), [])
		many = [{"version": f"1.0.{i}", "date": "d", "body": "- Enhancement Request"} for i in range(20)]
		self.assertEqual(len(ca.changelog_excerpts(many, [("Enhancement Request", True)], 5)), 5)

	def test_a_doctype_name_is_a_whole_word_a_plural_allowed(self):
		sections = [
			{"version": "1.0.3", "date": "d", "body": "- YouTube (`playlistItems.insert`) uploads.\n"},
			{"version": "1.0.2", "date": "d", "body": "- Scanning 6 Items moves stock.\n"},
			{"version": "1.0.1", "date": "d", "body": "- Reads the Item Group's default account.\n"},
			{"version": "1.0.0", "date": "d", "body": "- An ItemGroup or subItem is neither.\n"},
		]
		hits = ca.changelog_excerpts(sections, [("Item", True)], 5)
		self.assertEqual([h["version"] for h in hits], ["1.0.2", "1.0.1"])
		addresses = [{"version": "2.0.0", "date": "d", "body": "- Two Addresses per Customer.\n"}]
		self.assertEqual(len(ca.changelog_excerpts(addresses, [("Address", True)], 5)), 1)
		# A case-insensitive term gets the same boundary: a file name inside a longer one is not it.
		files = [
			{"version": "3.0.1", "date": "d", "body": "- `kiosk_clock.js` changed\n"},
			{"version": "3.0.0", "date": "d", "body": "- The KIOSK page changed\n"},
		]
		self.assertEqual(
			[h["version"] for h in ca.changelog_excerpts(files, [("kiosk", False)], 5)], ["3.0.0"]
		)

	def test_a_one_word_doctype_is_not_also_matched_in_lower_case(self):
		terms = ca.changelog_terms(
			None, [{"name": "Project", "controller": {"path": "erpnext/projects/doctype/project/project.py"}}]
		)
		self.assertIn(("Project", True), terms)
		self.assertIn(("projects/doctype/project/project.py", False), terms)
		# Neither the folder nor the controller's basename, which are both the name lowercased.
		self.assertNotIn(("project", False), terms)
		sections = [{"version": "1.0.0", "date": "d", "body": "- a group_subject, project and no parent\n"}]
		self.assertEqual(ca.changelog_excerpts(sections, terms, 5), [])
		# A multi-word name's folder is not its name, and stays a term.
		self.assertIn(("sales_invoice", False), ca.changelog_terms(None, [{"name": "Sales Invoice"}]))

	def test_terms_skip_short_basenames(self):
		terms = ca.changelog_terms(
			{"path": "/pay", "kind": "web", "controller": "erpnext_enhancements/www/pay.py"},
			[{"name": "Task", "controller": {"path": "erpnext/projects/doctype/task/task.py"}}],
		)
		self.assertIn(("Task", True), terms)
		self.assertIn(("/pay", False), terms)
		self.assertIn(("www/pay.py", False), terms)
		self.assertIn(("projects/doctype/task/task.py", False), terms)
		self.assertNotIn(("pay", False), terms)
		self.assertNotIn(("task", False), terms)


class TestReadmeSections(Base):
	@classmethod
	def setUpClass(cls):
		cls.text = (APP_DIR / "www/README.md").read_text(encoding="utf-8")

	def test_the_kiosk_parts_of_the_real_www_readme(self):
		out = ca.readme_sections(self.text, "/kiosk", 6000)
		self.assertIn("Time Kiosk", out)
		self.assertIn("www/kiosk.py", out)
		self.assertLessEqual(len(out), 6000)
		# The intro lists every page; only the kiosk's bullet comes.
		self.assertNotIn("Wall Display** at", out)

	def test_a_section_whose_heading_names_the_path_comes_whole(self):
		out = ca.readme_sections(self.text, "/wall", 20000)
		self.assertIn("## Wall Display (`/wall`)", out)
		heading = self.text.index("## Wall Display")
		body = self.text[heading : self.text.index("\n## ", heading + 5)]
		self.assertIn(body.strip().splitlines()[-1].strip(), out)

	def test_a_path_nobody_mentions_is_empty(self):
		self.assertEqual(ca.readme_sections(self.text, "/no-such-page", 6000), "")
		self.assertEqual(ca.readme_sections("", "/kiosk", 6000), "")

	def test_a_heading_inside_a_code_fence_is_not_a_heading(self):
		text = "## Real\n\n```bash\n# not a heading /kiosk\n```\n\nplain\n"
		out = ca.readme_sections(text, "/kiosk", 6000)
		self.assertTrue(out.startswith("## Real"))
		self.assertIn("# not a heading /kiosk", out)


class TestProjectField(Base):
	def test_falsy_keys_are_left_out_but_name_and_type_stay(self):
		self.assertEqual(
			ca.project_field({"fieldname": "notes", "fieldtype": "Small Text", "reqd": 0, "label": ""}),
			{"fieldname": "notes", "fieldtype": "Small Text"},
		)

	def test_every_key_it_sends(self):
		out = ca.project_field(
			{
				"fieldname": "pay_rate",
				"fieldtype": "Select",
				"label": "Pay Rate",
				"options": "x" * 1000,
				"reqd": 1,
				"read_only": "1",
				"permlevel": 2,
				"is_custom_field": 1,
				"default": SECRET_VALUE,
				"description": "long help",
			}
		)
		self.assertEqual(len(out["options"]), 300)
		self.assertEqual(
			{k: v for k, v in out.items() if k != "options"},
			{
				"fieldname": "pay_rate",
				"fieldtype": "Select",
				"label": "Pay Rate",
				"reqd": 1,
				"read_only": 1,
				"permlevel": 2,
				"custom": 1,
			},
		)
		self.assertNotIn(SECRET_VALUE, _dump(out))

	def test_an_object_works_like_a_dict(self):
		out = ca.project_field(types.SimpleNamespace(fieldname="a", fieldtype="Data", permlevel=None))
		self.assertEqual(out, {"fieldname": "a", "fieldtype": "Data"})


class TestFitToBudget(Base):
	def _big(self):
		return {
			"repo": "erpnext_enhancements",
			"version": VERSION,
			"route": {"path": "/kiosk", "kind": "web", "outline": "o\n" * 4000, "readme": "r\n" * 4000},
			"doctypes": [
				{
					"name": "Big",
					"fields": [
						{"fieldname": f"field_{i}", "fieldtype": "Select", "options": "z" * 300}
						for i in range(90)
					],
					"fields_total": 90,
					"controller": {"path": "p.py", "outline": "c\n" * 3000},
					"customizations": {
						"property_setters": [
							{"field_name": f"f{i}", "property": "hidden", "value": "v" * 200}
							for i in range(40)
						]
					},
				}
			],
			"readme": {"path": "x/README.md", "text": "t\n" * 3000},
			"changelog": [{"version": f"1.0.{i}", "date": "d", "excerpt": "e" * 2500} for i in range(5)],
			"truncated": [],
		}

	def test_a_large_object_is_cut_in_order_and_fits(self):
		out = ca.fit_to_budget(self._big(), 40000)
		size = len(_dump(out))
		self.assertLessEqual(size, 40000)
		self.assertEqual(out["chars"], size)
		self.assertEqual(out["truncated"][:2], ["changelog", "readme"])
		self.assertNotIn("changelog", out)
		self.assertEqual(out["doctypes"][0]["fields_total"], 90)
		steps = ["changelog", "readme", "outlines", "fields", "property_setters", "route_readme"]
		self.assertEqual(out["truncated"], [s for s in steps if s in out["truncated"]])

	def test_setters_that_were_the_only_customization_leave_no_empty_dict(self):
		# `customizations: {}` reads as "this app does nothing to the doctype"; after the cut the
		# truth is "it does something that was trimmed", and `truncated` says which.
		out = ca.fit_to_budget(self._big(), 30000)
		self.assertIn("property_setters", out["truncated"])
		self.assertNotIn("customizations", out["doctypes"][0])
		self.assertEqual(out["chars"], len(_dump(out)))

	def test_setters_go_and_the_other_customizations_stay(self):
		anchors = self._big()
		hooks = {"validate": ["erpnext_enhancements.x.validate"]}
		anchors["doctypes"][0]["customizations"]["doc_events"] = hooks
		out = ca.fit_to_budget(anchors, 30000)
		self.assertIn("property_setters", out["truncated"])
		self.assertEqual(out["doctypes"][0]["customizations"], {"doc_events": hooks})

	def test_the_fields_cap_steps_to_60_then_30(self):
		anchors = self._big()
		anchors.pop("changelog")
		anchors.pop("readme")
		anchors["route"].pop("outline")
		anchors["route"].pop("readme")
		anchors["doctypes"][0].pop("controller")
		anchors["doctypes"][0].pop("customizations")
		out = ca.fit_to_budget(anchors, 25000)
		self.assertEqual(len(out["doctypes"][0]["fields"]), 60)
		self.assertEqual(out["truncated"], ["fields"])

	def test_a_small_object_is_untouched_apart_from_chars(self):
		anchors = {"repo": "r", "version": "v", "route": None, "doctypes": [], "truncated": []}
		out = ca.fit_to_budget(copy.deepcopy(anchors), 40000)
		self.assertEqual(out["truncated"], [])
		self.assertEqual(out["chars"], len(_dump(out)))
		self.assertEqual({k: v for k, v in out.items() if k != "chars"}, anchors)

	def test_the_cap_holds_for_any_input(self):
		anchors = self._big()
		anchors["doctypes"][0]["name"] = "N" * 50000
		out = ca.fit_to_budget(anchors, 40000)
		self.assertLessEqual(len(_dump(out)), 40000)
		self.assertIn("everything", out["truncated"])
		self.assertEqual(out["chars"], len(_dump(out)))

	def test_non_ascii_counts_as_characters(self):
		self.assertEqual(ca.serialized_size({"a": "—"}), len('{"a":"—"}'))


# ------------------------------------------------------------------------------ build_anchors


class TestBuildAnchors(Base):
	def test_a_request_filed_from_an_app_doctype_form(self):
		out = ca.build_anchors(f"/desk/enhancement-request/{SECRET_DOCNAME}?{SECRET_QUERY}", "")
		self.assertEqual(out["repo"], "erpnext_enhancements")
		self.assertEqual(out["version"], VERSION)
		self.assertEqual(out["route"], {"path": "/desk/enhancement-request", "kind": "desk"})
		[doctype] = out["doctypes"]
		self.assertEqual(doctype["name"], "Enhancement Request")
		self.assertEqual(doctype["origin"], "app")
		self.assertEqual(doctype["module"], "Product Feedback")
		self.assertEqual(
			doctype["path"], "erpnext_enhancements/product_feedback/doctype/enhancement_request/"
		)
		fieldnames = [f["fieldname"] for f in doctype["fields"]]
		self.assertIn("breakdown_stats", fieldnames)
		self.assertIn("proposed_tasks", fieldnames, "a Table field names its child doctype and stays")
		self.assertNotIn("breakdown_section", fieldnames)
		self.assertNotIn("column_break_head", fieldnames)
		self.assertEqual(doctype["fields_total"], len(doctype["fields"]))
		status = next(f for f in doctype["fields"] if f["fieldname"] == "status")
		self.assertTrue(status["options"].startswith("Submitted\nApproved"))
		self.assertEqual(
			doctype["controller"]["path"],
			"erpnext_enhancements/product_feedback/doctype/enhancement_request/enhancement_request.py",
		)
		self.assertIn("class EnhancementRequest(Document):", doctype["controller"]["outline"])
		self.assertEqual(out["readme"]["path"], "erpnext_enhancements/product_feedback/README.md")
		self.assertLessEqual(len(out["readme"]["text"]), ca.MAX_README_CHARS)
		# Not "non-empty": this reads the live CHANGELOG through a 200-section window, and a
		# quiet fortnight for the feature would empty it. The wiring has a fixture test below.
		self.assertLessEqual(len(out.get("changelog") or []), 5)
		self.assertEqual(out["chars"], len(_dump(out)))
		self.assertLessEqual(out["chars"], ca.MAX_ANCHOR_CHARS)

	def test_the_changelog_lines_naming_the_doctype_ride_with_it(self):
		fixture = (
			"## [Unreleased]\n\n"
			"## [9.9.9] - 2026-01-02\n\n"
			"### Fixed\n\n"
			"- **Enhancement Requests** keep their proposal.\n"
			"- Unrelated.\n\n"
			"## [9.9.8] - 2026-01-01\n\n"
			"- Nothing about it.\n"
		)
		real = ca._read
		ca._read = lambda path: fixture if Path(path).name == "CHANGELOG.md" else real(path)
		try:
			out = ca.build_anchors("/desk/enhancement-request/X", "")
		finally:
			ca._read = real
		self.assertEqual(
			out["changelog"],
			[
				{
					"version": "9.9.9",
					"date": "2026-01-02",
					"excerpt": "- **Enhancement Requests** keep their proposal.",
				}
			],
		)

	def test_context_doctype_wins_and_is_confirmed(self):
		out = ca.build_anchors("/kiosk", "enhancement request")
		self.assertEqual(out["doctypes"][0]["name"], "Enhancement Request")
		self.assertEqual(out["route"]["kind"], "web")

	def test_a_core_doctype_flags_restricted_fields_and_lists_what_the_app_hooks(self):
		S.property_setters = [
			{"doc_type": "Employee Pay", "field_name": "grade", "property": "reqd", "value": "1"},
			{"doc_type": "Employee Pay", "field_name": "", "property": "field_order", "value": '["a"]'},
			{"doc_type": "Employee Pay", "field_name": "employee", "property": "label", "value": "W" * 500},
			{"doc_type": "Employee Pay", "field_name": "employee", "property": "bold", "value": "1"},
		] + [
			{"doc_type": "Employee Pay", "field_name": f"zz_{i:02d}", "property": "hidden", "value": "1"}
			for i in range(50)
		]
		out = ca.build_anchors("/desk/employee-pay/EP-0001", "")
		[doctype] = out["doctypes"]
		self.assertEqual(doctype["origin"], "erpnext")
		self.assertNotIn("path", doctype, "a doctype folder path is for app doctypes only")
		self.assertEqual(doctype["is_submittable"], 1)
		self.assertEqual(doctype["restricted_fields"], ["pay_rate", "api_secret"])
		self.assertEqual(
			[f["fieldname"] for f in doctype["fields"]],
			["employee", "pay_rate", "api_secret", "grade", "custom_crew"],
		)
		crew = doctype["fields"][-1]
		self.assertEqual(crew["custom"], 1)
		self.assertLessEqual(len(doctype["fields"][3]["options"]), 300)
		self.assertEqual(doctype["controller"]["path"], "erpnext/hr/doctype/employee_pay/employee_pay.py")
		self.assertIn("class EmployeePay(Document):", doctype["controller"]["outline"])
		custom = doctype["customizations"]
		self.assertEqual(list(custom["doc_events"]), ["on_update", "validate"])
		self.assertEqual(
			custom["doctype_js"], ["public/js/employee_pay.js", "public/js/employee_pay_list.js"]
		)
		self.assertEqual(
			custom["override_class"], "erpnext_enhancements.hr.EmployeePay", "Frappe uses the last"
		)
		setters = custom["property_setters"]
		self.assertEqual(len(setters), ca.MAX_PROPERTY_SETTERS)
		self.assertEqual(setters[0], {"field_name": "employee", "property": "bold", "value": "1"})
		self.assertEqual(len(setters[1]["value"]), 200)
		self.assertNotIn("field_order", _dump(setters))
		self.assertEqual(setters, sorted(setters, key=lambda r: (r["field_name"], r["property"], r["value"])))
		# The first doc_events handler with a README is quality's (on_update sorts first).
		self.assertEqual(out["readme"]["path"], "erpnext_enhancements/quality/README.md")

	def test_the_real_kiosk_route(self):
		out = ca.build_anchors("/kiosk?utm=x", "")
		route = out["route"]
		self.assertEqual(route["path"], "/kiosk")
		self.assertEqual(route["kind"], "web")
		self.assertEqual(route["controller"], "erpnext_enhancements/www/kiosk.py")
		self.assertEqual(route["template"], "erpnext_enhancements/www/kiosk.html")
		self.assertIn("get_context(context)", route["outline"])
		self.assertIn("/kiosk", route["readme"])
		self.assertEqual(out["doctypes"], [])
		self.assertNotIn("readme", out, "a web route's README is route.readme")

	def test_a_route_rule_and_a_hyphenated_route(self):
		feedback = ca.build_anchors("/feedback/request/ER-2026-00001", "")["route"]
		self.assertEqual(feedback["path"], "/feedback")
		self.assertEqual(feedback["controller"], "erpnext_enhancements/www/feedback.py")
		contract = ca.build_anchors("/contract-sign?token=abc", "")["route"]
		self.assertEqual(contract["controller"], "erpnext_enhancements/www/contract_sign.py")
		self.assertEqual(contract["template"], "erpnext_enhancements/www/contract-sign.html")

	def test_nothing_resolvable_is_empty(self):
		self.assertEqual(ca.build_anchors("/desk/no-such-doctype/X", ""), {})
		self.assertEqual(ca.build_anchors("/desk/query-report/Stock Balance", ""), {})
		self.assertEqual(ca.build_anchors("/login", ""), {})
		self.assertEqual(ca.build_anchors("", ""), {})
		self.assertEqual(ca.build_anchors("", "No Such DocType"), {})
		self.assertEqual(ca.build_anchors("/..%2f..%2fetc/passwd", ""), {})
		self.assertEqual(ca.build_anchors("/../hooks", ""), {})

	def test_a_failed_build_is_empty_not_an_error(self):
		S.app_path_fails = True
		self.assertEqual(ca.build_anchors("/desk/enhancement-request/X", ""), {})

	def test_a_failed_step_contributes_nothing(self):
		S.hooks = None  # every hook read raises

		out = ca.build_anchors("/desk/employee-pay/EP-1", "")
		self.assertEqual(out["doctypes"][0]["name"], "Employee Pay")
		self.assertNotIn("customizations", out["doctypes"][0])

	def test_two_builds_are_byte_identical(self):
		first = ca.build_anchors(f"/desk/enhancement-request/{SECRET_DOCNAME}", "")
		second = ca.build_anchors(f"/desk/enhancement-request/{SECRET_DOCNAME}", "")
		self.assertEqual(first, second)
		self.assertEqual(_dump(first), _dump(second))

	def test_no_document_data_can_appear(self):
		S.property_setters = [
			{"doc_type": "Employee Pay", "field_name": "x", "property": "hidden", "value": "1"}
		]
		for url, doctype in (
			(f"/desk/enhancement-request/{SECRET_DOCNAME}?{SECRET_QUERY}", ""),
			(f"/desk/employee-pay/{SECRET_DOCNAME}?{SECRET_QUERY}", "Employee Pay"),
			(f"/kiosk/{SECRET_DOCNAME}?{SECRET_QUERY}", ""),
		):
			text = _dump(ca.build_anchors(url, doctype))
			for secret in (SECRET_DOCNAME, "QUERY-SECRET-9", SECRET_VALUE, "someone%40example.com"):
				self.assertNotIn(secret, text, (url, secret))
		# The only tables read: DocType to confirm a name, schema through get_meta, and
		# Property Setter. Never a user doctype's rows.
		tables = {doctype for method, doctype in S.db_calls if method != "get_meta"}
		self.assertLessEqual(tables, {"DocType", "Property Setter"})


# ------------------------------------------------------------------------------ breakdown


class TestBuildPayload(Base):
	def setUp(self):
		super().setUp()
		self._codemap = breakdown.build_codemap
		breakdown.build_codemap = lambda: {"repo": "erpnext_enhancements", "conventions": ""}

	def tearDown(self):
		breakdown.build_codemap = self._codemap

	def _build(self):
		return breakdown.build_payload(
			"ER-2026-00077", target_projects={"erpnext": "PRJ-00580"}, known_tasks={}, max_tasks=12
		)

	def test_schema_2_path_only_url_and_no_docname_key(self):
		payload = self._build()
		self.assertEqual(payload["schema_version"], 2)
		context = payload["request"]["context"]
		self.assertEqual(
			context,
			{"url": "/desk/enhancement-request", "doctype": "Enhancement Request", "app_version": "1.526.0"},
		)
		self.assertNotIn("docname", context)
		self.assertNotIn("context_docname", S.request_fields_asked, "the name is not even read")

	def test_no_query_string_or_document_name_reaches_the_payload(self):
		for url in (
			f"https://erp.example.com/desk/enhancement-request/{SECRET_DOCNAME}?{SECRET_QUERY}#x",
			f"/kiosk?{SECRET_QUERY}",
			f"/feedback/request/{SECRET_DOCNAME}?{SECRET_QUERY}",
			f"erp.example.com/desk/enhancement-request/{SECRET_DOCNAME}?{SECRET_QUERY}",
		):
			S.request_row["context_url"] = url
			payload = self._build()
			self.assertNotIn("?", payload["request"]["context"]["url"])
			self.assertNotIn("#", payload["request"]["context"]["url"])
			text = _dump(payload)
			for secret in (SECRET_DOCNAME, "QUERY-SECRET-9", "someone%40example.com", "erp.example.com"):
				self.assertNotIn(secret, text, (url, secret))

	def test_the_anchors_ride_under_erpnext(self):
		payload = self._build()
		anchors = payload["anchors"]["erpnext"]
		self.assertEqual(anchors["doctypes"][0]["name"], "Enhancement Request")
		self.assertLessEqual(len(_dump(anchors)), 40000)
		self.assertEqual(anchors["chars"], len(_dump(anchors)))

	def test_anchors_are_empty_when_nothing_resolves(self):
		S.request_row.update(context_url="/login", context_doctype="")
		self.assertEqual(self._build()["anchors"], {"erpnext": {}})

	def test_the_payload_is_deterministic(self):
		self.assertEqual(_dump(self._build()), _dump(self._build()))


class TestMeasurement(Base):
	def test_stats_from_a_v1_triton(self):
		sizes = {"payload_chars": 900, "anchors_chars": 300, "codebase_chars": 500}
		stats = json.loads(
			breakdown.breakdown_stats(sizes, {"usage": {"prompt_tokens": 40, "total_tokens": 90}})
		)
		self.assertEqual(
			stats,
			{
				"schema": 1,
				"payload_chars": 900,
				"anchors_chars": 300,
				"codebase_chars": 500,
				"prompt_chars": None,
				"prompt_tokens": 40,
				"total_tokens": 90,
				"attempts": None,
			},
		)

	def test_stats_from_a_v2_triton(self):
		body = {
			"schema_version": 2,
			"prompt_chars": 51234,
			"attempts": 2,
			"usage": {"prompt_tokens": 1, "total_tokens": 2},
		}
		stats = json.loads(breakdown.breakdown_stats({}, body))
		self.assertEqual((stats["schema"], stats["prompt_chars"], stats["attempts"]), (2, 51234, 2))
		body["attempts"] = 1
		self.assertEqual(json.loads(breakdown.breakdown_stats({}, body))["attempts"], 1)

	def test_stats_never_raise(self):
		self.assertEqual(breakdown.breakdown_stats(None, None)[:10], '{"schema":')
		self.assertEqual(breakdown.payload_sizes(object()), {})

	def test_sizes_agree_with_the_anchors_own_count(self):
		payload = {"anchors": {"erpnext": {"a": "—", "chars": 13}}, "codebase": {"erpnext": {}}}
		sizes = breakdown.payload_sizes(payload)
		self.assertEqual(sizes["anchors_chars"], len(_dump(payload["anchors"]["erpnext"])))
		self.assertEqual(sizes["codebase_chars"], 0)
		self.assertEqual(sizes["payload_chars"], len(_dump(payload)))

	def test_the_usage_row_names_its_request(self):
		breakdown._record_usage(
			{"usage": {"prompt_tokens": 5, "total_tokens": 9}}, "gemini-x", "ER-2026-00077"
		)
		[row] = S.inserted
		self.assertEqual(row["reference_doctype"], "Enhancement Request")
		self.assertEqual(row["reference_name"], "ER-2026-00077")
		self.assertEqual(row["feature"], "feedback_work_breakdown")

	def test_apply_writes_the_stats_with_the_proposal(self):
		result = types.SimpleNamespace(tasks=[], duplicates=[], summary="s", model="m", dropped=[])
		breakdown._apply("ER-2026-00077", result, stats='{"schema":2}')
		[doc] = S.saved_docs
		self.assertEqual(doc.breakdown_stats, '{"schema":2}')
		self.assertEqual(doc.status, "Breakdown Ready")

	def test_fail_writes_stats_only_when_triton_answered(self):
		notify = breakdown._notify_reviewer
		breakdown._notify_reviewer = lambda *a, **k: None
		try:
			breakdown._fail("ER-1", "no payload")
			breakdown._fail("ER-2", "empty plan", stats='{"schema":1}')
		finally:
			breakdown._notify_reviewer = notify
		self.assertNotIn("breakdown_stats", S.set_values[0][2])
		self.assertEqual(S.set_values[1][2]["breakdown_stats"], '{"schema":1}')

	def test_a_proposal_that_cannot_be_saved_still_records_the_paid_call(self):
		S.request_row.update(status="Approved", decided_by="reviewer@example.com", target_erpnext=1)
		failed = []

		class TritonUnavailable(Exception):
			pass

		key = "erpnext_enhancements.product_feedback.triton_client"
		client = types.ModuleType(key)
		client.TritonUnavailable = TritonUnavailable
		client.request_breakdown = lambda **kwargs: {
			"schema_version": 2,
			"attempts": 1,
			"usage": {"prompt_tokens": 3, "total_tokens": 5},
		}
		proposal = types.SimpleNamespace(
			is_empty=False, duplicates=[], model="m", summary="s", tasks=[], dropped=[]
		)

		def cannot_save(*args, **kwargs):
			raise RuntimeError("the document is locked")

		patches = {
			"get_settings": lambda: {
				"erpnext_project": "PRJ-00580",
				"duplicate_scan_limit": 5,
				"max_proposed_tasks": 12,
				"breakdown_timeout": 30,
			},
			"open_tasks": lambda projects, limit: {},
			"build_payload": lambda *args, **kwargs: {
				"anchors": {"erpnext": {}},
				"codebase": {"erpnext": {}},
			},
			"parse_breakdown": lambda *args, **kwargs: proposal,
			"_apply": cannot_save,
			"_fail": lambda name, message, stats=None: failed.append((name, message, stats)),
		}
		package = sys.modules["erpnext_enhancements.product_feedback"]
		saved = {name: getattr(breakdown, name) for name in patches}
		saved_module = sys.modules.get(key)
		saved_attr = package.__dict__.get("triton_client")
		sys.modules[key] = client
		package.triton_client = client
		for name, value in patches.items():
			setattr(breakdown, name, value)
		try:
			breakdown.run_breakdown("ER-2026-00077")
		finally:
			for name, value in saved.items():
				setattr(breakdown, name, value)
			if saved_module is None:
				sys.modules.pop(key, None)
			else:
				sys.modules[key] = saved_module
			if saved_attr is None:
				package.__dict__.pop("triton_client", None)
			else:
				package.triton_client = saved_attr

		[(name, message, stats)] = failed
		self.assertEqual(
			(name, message), ("ER-2026-00077", "Could not save the proposal; see the Error Log.")
		)
		self.assertEqual((json.loads(stats)["schema"], json.loads(stats)["attempts"]), (2, 1))
		self.assertEqual(len(S.inserted), 1, "the usage row for the same call")


class TestTheField(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		path = APP_DIR / "product_feedback/doctype/enhancement_request/enhancement_request.json"
		cls.schema = json.loads(path.read_text(encoding="utf-8"))
		cls.fields = {f["fieldname"]: f for f in cls.schema["fields"]}

	def test_a_read_only_small_text_in_the_breakdown_section_with_no_default(self):
		field = self.fields["breakdown_stats"]
		self.assertEqual(field["fieldtype"], "Small Text")
		self.assertEqual(field.get("read_only"), 1)
		# A normal doctype: a default would be written into every existing row by the ALTER.
		self.assertNotIn("default", field)
		order = self.schema["field_order"]
		self.assertEqual(order.index("breakdown_stats"), order.index("breakdown_error") + 1)
		self.assertGreater(order.index("breakdown_stats"), order.index("breakdown_section"))

	def test_the_json_modified_moved_forward(self):
		# Without a newer `modified`, `bench migrate` skips the JSON and the column never exists.
		self.assertGreaterEqual(self.schema["modified"], "2026-09-24 12:00:00")

	def test_it_is_never_client_input_and_never_frozen(self):
		source = (APP_DIR / "api/feedback.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		allowed = None
		for node in tree.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SUBMIT_ALLOWED_FIELDS":
				allowed = ast.literal_eval(node.value.args[0])
		self.assertIsNotNone(allowed)
		self.assertNotIn("breakdown_stats", allowed)
		controller = ast.parse(
			(APP_DIR / "product_feedback/doctype/enhancement_request/enhancement_request.py").read_text(
				encoding="utf-8"
			)
		)
		frozen = None
		for node in controller.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_FROZEN_FIELDS":
				frozen = ast.literal_eval(node.value)
		self.assertIsNotNone(frozen)
		self.assertNotIn("breakdown_stats", frozen, "the worker rewrites it on every run")


if __name__ == "__main__":
	unittest.main()

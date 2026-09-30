# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An article as the company register's template, printed, previewed and on its form (2026-09-30).
Bench-free.

Nik: "can those be printed and previewed and viewed as the templates in the google drive", then
"please proceed 1-3". What this holds:

* **The templates' sections** (``constants.KIND_SECTIONS``): the three kinds, in the register's
  order, with guidance that is plain ASCII and never "Revision History" (the page draws that).
* **A new draft's starting text** (``document.skeleton``) and the check that stops its guidance
  being submitted (``content.guidance_left``, asked by ``workflow.submit_problems``), which must
  find the guidance however the editor wraps it and let go of it once it is rewritten.
* **The page** (``document.render``): the header, the owner row, numbered headings that change
  nothing else in the body, the Revision History, the footer, every typed value escaped, and a
  stylesheet whose every rule is scoped to ``.kb-doc`` and uses no flex or grid.
* **The glue** (``printing.py``), over a small frappe stub: it draws the record **as saved**, never
  the document it is handed; it refuses an unsaved draft and a reader who cannot read it; an
  article's page reads no version; a draft's history ends with its own pending line; and only a
  picture attached to the record itself (or a revision's article) is written into the page.
* **The wiring**: the two print formats are one line each, the Jinja global and the migrate hook are
  registered (the hook above the chrome pin), the forms call what exists, and the drafting tool's
  schema names the sections.

Installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_document -v
"""

import copy
import datetime
import importlib
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.knowledge_base import constants as K
from erpnext_enhancements.knowledge_base import content as C
from erpnext_enhancements.knowledge_base import document as D
from erpnext_enhancements.knowledge_base import workflow as W

MODULE_DIR = APP / "knowledge_base"
AUTHOR = "parker@example.com"
APPROVER = "james@example.com"
OWNER = "parker@example.com"

SOP_BODY = (
	'<div class="ql-editor read-mode"><h2>Purpose</h2><p>Record what arrived.</p>'
	"<h2>Step-by-Step Procedure</h2><h3>Check the delivery</h3><ol><li>Count it.</li></ol>"
	'<h2 class="ql-align-center">Troubleshooting &amp; Exceptions</h2>'
	"<table><thead><tr><th>If this happens</th><th>Then do this</th></tr></thead>"
	"<tbody><tr><td>Short</td><td>Note it</td></tr></tbody></table></div>"
)


def _version(**values):
	version = {
		"name": "KBV-00001",
		"review_state": W.DRAFT,
		"title": "Receiving a PO against a packing slip",
		"department_block": "06 Operations",
		"kind": "SOP",
		"body": "<p>Count it.</p>",
		"owner": AUTHOR,
		"submitted_by": None,
		"contributors": AUTHOR,
		"ai_requested_by": None,
		"modified": "2026-09-30 10:00:00.000000",
	}
	version.update(values)
	return version


# ================================================================== the templates' sections


class TestStandardLibraryOnly(unittest.TestCase):
	def test_the_renderer_imports_no_frappe(self):
		"""In a fresh interpreter, so this suite's own stub cannot hide an import."""
		code = (
			"import sys\n"
			"import erpnext_enhancements.knowledge_base.document\n"
			"bad = sorted(m for m in sys.modules if m == 'frappe' or m.startswith('frappe.'))\n"
			"print(','.join(bad))\n"
		)
		result = subprocess.run(
			[sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True
		)
		self.assertEqual(result.stdout.strip(), "")


class TestTheTemplatesSections(unittest.TestCase):
	def test_every_kind_has_its_register_template_and_sections(self):
		self.assertEqual(tuple(K.KIND_SECTIONS), K.ARTICLE_KINDS)
		self.assertEqual(
			K.KIND_REGISTER_TEMPLATES, {"Policy": "POL-0002", "Process": "POL-0003", "SOP": "POL-0004"}
		)

	def test_the_sections_are_the_templates_in_order(self):
		"""Read from the three templates' Google Docs export, 2026-09-30."""
		self.assertEqual(
			K.kind_section_names("Policy"),
			(
				"Introduction & Purpose",
				"Scope",
				"Roles & Responsibilities",
				"Policy Statements",
				"Non-Compliance",
				"Related Documentation",
			),
		)
		self.assertEqual(
			K.kind_section_names("Process"),
			(
				"Overview & Objective",
				"Inputs & Outputs (SIPOC)",
				"High-Level Process Map",
				"Key Steps & Handoffs",
				"Related Procedures (SOPs)",
			),
		)
		self.assertEqual(
			K.kind_section_names("SOP"),
			(
				"Purpose",
				"Scope",
				"Prerequisites & Tools",
				"Visual Process Map",
				"Step-by-Step Procedure",
				"Troubleshooting & Exceptions",
			),
		)
		self.assertEqual(K.kind_section_names("Nope"), ())

	def test_no_template_lists_the_revision_history(self):
		"""The page draws it from the approved versions; a typed one would print twice."""
		for kind, sections in K.KIND_SECTIONS.items():
			for name, _guidance in sections:
				with self.subTest(kind=kind, name=name):
					self.assertNotIn("revision", name.casefold())

	def test_the_guidance_is_plain_ascii_with_no_quotation_marks_and_each_is_its_own(self):
		"""What a person sees is what the check compares; and one section's guidance never names
		another's by accident."""
		seen = []
		for kind, sections in K.KIND_SECTIONS.items():
			for name, guidance in sections:
				with self.subTest(kind=kind, name=name):
					self.assertTrue(guidance.isascii())
					self.assertNotIn('"', guidance)
					self.assertNotIn("[", guidance)
					self.assertNotIn("]", guidance)
					self.assertEqual(guidance, guidance.strip())
					for other in seen:
						self.assertNotIn(other, guidance)
						self.assertNotIn(guidance, other)
				seen.append(guidance)


class TestTheSkeleton(unittest.TestCase):
	def test_each_section_is_a_heading_with_its_guidance_bracketed_in_italics(self):
		body = D.skeleton("SOP")
		headings = re.findall(r"<h2>(.*?)</h2>", body)
		self.assertEqual(
			headings,
			[
				"Purpose",
				"Scope",
				"Prerequisites &amp; Tools",
				"Visual Process Map",
				"Step-by-Step Procedure",
				"Troubleshooting &amp; Exceptions",
			],
		)
		for _name, guidance in K.KIND_SECTIONS["SOP"]:
			with self.subTest(guidance=guidance[:20]):
				self.assertIn(f"<p><em>[{guidance}]</em></p>", body.replace("&amp;", "&"))

	def test_anything_that_is_not_a_kind_gets_nothing(self):
		for kind in (None, "", "sop", "Nope"):
			with self.subTest(kind=kind):
				self.assertEqual(D.skeleton(kind), "")


class TestGuidanceLeft(unittest.TestCase):
	def test_a_fresh_skeleton_has_every_sections_guidance(self):
		for kind in K.ARTICLE_KINDS:
			with self.subTest(kind=kind):
				self.assertEqual(C.guidance_left(D.skeleton(kind)), list(K.kind_section_names(kind)))

	def test_found_however_the_editor_wraps_it(self):
		guidance = dict(K.KIND_SECTIONS["SOP"])["Scope"]
		words = guidance.split(" ")
		wrapped = (
			'<div class="ql-editor read-mode"><p><em>['
			+ " ".join(words[:3])
			+ "<br>"
			+ "&nbsp;".join(words[3:6])
			+ " "
			+ " ".join(words[6:])
			+ "]</em></p></div>"
		)
		self.assertEqual(C.guidance_left(wrapped), ["Scope"])

	def test_once_it_is_rewritten_it_is_the_authors(self):
		guidance = dict(K.KIND_SECTIONS["SOP"])["Purpose"]
		edited = guidance.replace("outcome", "result")
		self.assertEqual(C.guidance_left(f"<p><em>[{edited}]</em></p>"), [])
		self.assertEqual(C.guidance_left("<h2>Purpose</h2><p>Record what arrived.</p>"), [])

	def test_the_headings_alone_are_not_guidance(self):
		self.assertEqual(C.guidance_left("<h2>Purpose</h2><h2>Scope</h2>"), [])

	def test_any_kinds_guidance_counts_whatever_the_draft_is_now(self):
		"""A draft filled as an SOP and then made a Policy still has the SOP's guidance in it."""
		self.assertEqual(C.guidance_left(D.skeleton("SOP"))[:1], ["Purpose"])

	def test_script_and_style_text_is_not_read(self):
		guidance = dict(K.KIND_SECTIONS["SOP"])["Purpose"]
		self.assertEqual(C.guidance_left(f"<script>{guidance}</script><style>{guidance}</style>"), [])

	def test_nothing_is_nothing(self):
		for value in (None, "", "   ", 3):
			with self.subTest(value=value):
				self.assertEqual(C.guidance_left(value), [])


class TestSubmitRefusesGuidance(unittest.TestCase):
	def test_a_draft_with_guidance_left_cannot_be_submitted(self):
		problems = W.submit_problems(_version(body=D.skeleton("SOP")), AUTHOR, (K.AUTHOR_ROLE,))
		self.assertEqual(len(problems), 1)
		self.assertIn("still has the template's guidance under Purpose, Scope,", problems[0])
		self.assertIn("and Troubleshooting & Exceptions", problems[0])
		self.assertIn("replace it with the article's own words, or delete it", problems[0])

	def test_one_section_left_is_named_alone(self):
		body = "<h2>Purpose</h2><p>Record it.</p><h2>Scope</h2><p><em>[{}]</em></p>".format(
			dict(K.KIND_SECTIONS["SOP"])["Scope"]
		)
		self.assertEqual(
			W.submit_problems(_version(body=body), AUTHOR, (K.AUTHOR_ROLE,)),
			[
				"it still has the template's guidance under Scope: replace it with the article's own words, or delete it"
			],
		)

	def test_a_written_draft_is_not_refused(self):
		self.assertEqual(W.submit_problems(_version(body=SOP_BODY), AUTHOR, (K.AUTHOR_ROLE,)), [])


# ================================================================== the page


def _sheet(**values):
	sheet = D.Sheet(
		title="Receiving a PO against a packing slip",
		kind="SOP",
		number="SOP-06-0001",
		version=2,
		date=datetime.date(2026, 10, 2),
		owner="Parker Bailey",
		owner_role="Purchasing Agent/Inventory Clerk",
		group="Operations",
		body=SOP_BODY,
		revisions=[
			D.Revision(
				2, datetime.date(2026, 10, 2), "Nik Bradshaw", "Lisa Symanski", "Added the damaged-goods step"
			),
			D.Revision(1, datetime.date(2026, 9, 30), "Nik Bradshaw", "James Harris", "Initial release"),
		],
	)
	for key, value in values.items():
		setattr(sheet, key, value)
	return sheet


class TestNumberSections(unittest.TestCase):
	def test_the_top_level_is_numbered_in_order_and_the_rest_are_sub_headings(self):
		body, count = D.number_sections(SOP_BODY)
		self.assertEqual(count, 3)
		self.assertIn('<h2 class="kb-section"><span class="kb-number">1.</span> Purpose</h2>', body)
		self.assertIn('<span class="kb-number">2.</span> Step-by-Step Procedure</h2>', body)
		self.assertIn('<h3 class="kb-sub">Check the delivery</h3>', body)

	def test_a_class_already_there_is_kept(self):
		body, _count = D.number_sections(SOP_BODY)
		self.assertIn(
			'<h2 class="kb-section ql-align-center"><span class="kb-number">3.</span> Troubleshooting &amp; Exceptions</h2>',
			body,
		)

	def test_nothing_else_in_the_body_changes(self):
		body, _count = D.number_sections(SOP_BODY)

		def without_headings(markup):
			return re.sub(r"<h[1-6][^>]*>.*?</h[1-6]>", "", markup, flags=re.S)

		self.assertEqual(without_headings(body), without_headings(SOP_BODY))

	def test_an_authors_own_number_is_kept_and_still_counts(self):
		body, count = D.number_sections("<h2>1. Purpose</h2><h2>Scope</h2>")
		self.assertEqual(count, 2)
		self.assertIn('<h2 class="kb-section">1. Purpose</h2>', body)
		self.assertIn('<span class="kb-number">2.</span> Scope', body)

	def test_the_highest_level_used_is_the_top_level(self):
		body, count = D.number_sections("<h1>Purpose</h1><h2>Detail</h2><h1>Scope</h1>")
		self.assertEqual(count, 2)
		self.assertIn('<h2 class="kb-sub">Detail</h2>', body)

	def test_a_body_with_no_heading_is_unchanged(self):
		self.assertEqual(D.number_sections("<p>Count it.</p>"), ("<p>Count it.</p>", 0))


class TestRender(unittest.TestCase):
	def test_the_header_is_the_templates(self):
		page = D.render(_sheet())
		self.assertIn('<div class="kb-brand">Sapphire Fountains</div>', page)
		self.assertIn("Masters of Fountaineering&trade;", page)
		self.assertIn('<div class="kb-docid">Document ID: SOP-06-0001</div>', page)
		self.assertIn('<div class="kb-small">Version: 2</div>', page)
		self.assertIn('<div class="kb-small">Last Updated: 10/02/2026</div>', page)
		self.assertIn('<h1 class="kb-title">Receiving a PO against a packing slip</h1>', page)

	def test_each_kind_words_its_owner_date_and_footer(self):
		expected = {
			"Policy": ("Policy Owner:", "Effective Date:", "Policy Document - For Internal Use Only"),
			"Process": ("Process Owner:", "Last Updated:", "Process Definition - For Internal Use Only"),
			"SOP": ("SOP Owner:", "Last Updated:", "Confidential - Sapphire Fountains Internal Use Only"),
			None: ("Owner:", "Last Updated:", "Confidential - Sapphire Fountains Internal Use Only"),
		}
		for kind, words in expected.items():
			page = D.render(_sheet(kind=kind))
			for word in words:
				with self.subTest(kind=kind, word=word):
					self.assertIn(word, page)

	def test_the_owner_row_leads_with_the_role(self):
		page = D.render(_sheet())
		self.assertIn("<td>Purchasing Agent/Inventory Clerk (Parker Bailey)</td>", page)
		self.assertIn("<td>Operations</td>", page)
		self.assertEqual(D.owner_text("Parker Bailey", ""), "Parker Bailey")
		self.assertEqual(D.owner_text("", "Operations Manager"), "Operations Manager")
		self.assertEqual(D.owner_text(None, None), "Not set")
		self.assertIn("<td>Not set</td>", D.render(_sheet(owner="", owner_role="", group="")))

	def test_the_revision_history_takes_the_next_number_oldest_first(self):
		page = D.render(_sheet())
		self.assertIn('<span class="kb-number">4.</span> Revision History</h2>', page)
		self.assertLess(page.index("Initial release"), page.index("Added the damaged-goods step"))
		self.assertIn(
			"<tr><td>1</td><td>09/30/2026</td><td>Nik Bradshaw</td><td>James Harris</td><td>Initial release</td></tr>",
			page,
		)
		for column in D.REVISION_COLUMNS:
			self.assertIn(f"<th>{column}</th>", page)

	def test_a_pending_line_says_so_and_names_no_approver(self):
		sheet = _sheet(
			revisions=[D.Revision(1, None, "Nik Bradshaw", "James Harris", "First draft", pending=True)]
		)
		page = D.render(sheet)
		self.assertIn(
			'<tr class="kb-pending"><td>1 (draft)</td><td>Not yet approved</td><td>Nik Bradshaw</td><td></td>'
			"<td>First draft</td></tr>",
			page,
		)
		self.assertIn("None yet.", D.render(_sheet(revisions=[])))

	def test_every_typed_value_is_escaped(self):
		evil = '<script>alert("x")</script>'
		sheet = _sheet(
			title=evil,
			owner=evil,
			owner_role=evil,
			group=evil,
			number=evil,
			revisions=[D.Revision(1, None, evil, evil, evil)],
			notice=D.Notice("warn", evil, evil),
			footnote=evil,
			body="<p>ok</p>",
		)
		page = D.render(sheet)
		self.assertNotIn("<script>", page)
		self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", page)

	def test_an_unnumbered_draft(self):
		self.assertEqual(D.document_id(_sheet(number=None, scope="SOP-06-")), "SOP-06-####")
		self.assertEqual(D.document_id(_sheet(number=None, scope=None)), "Not numbered yet")
		page = D.render(_sheet(number=None, scope="SOP-06-", version=None, date=None))
		self.assertIn("Document ID: SOP-06-####", page)
		self.assertIn("Version: Not yet approved", page)
		self.assertIn("Last Updated: Not yet approved", page)

	def test_the_notice_the_watermark_and_the_screen_copy(self):
		notice = D.Notice("warn", "Draft - not approved", "A preview of KBV-00001.")
		page = D.render(_sheet(notice=notice, watermark=True))
		self.assertIn('<div class="kb-notice kb-notice-warn">', page)
		self.assertIn('<div class="kb-watermark">DRAFT</div>', page)
		screen = D.render(_sheet(notice=notice, watermark=True, screen=True))
		self.assertNotIn('<div class="kb-watermark">', screen)
		self.assertIn('<div class="kb-doc kb-screen">', screen)
		self.assertIn('<div class="kb-doc">', page)

	def test_the_footer_and_footnote(self):
		page = D.render(_sheet(footnote="SOP-06-0001 version 2, printed 10/03/2026."))
		self.assertIn("<svg", page)
		self.assertIn('<div class="kb-footnote">SOP-06-0001 version 2, printed 10/03/2026.</div>', page)
		self.assertNotIn("kb-footnote", D.render(_sheet(footnote="")).split("</style>", 1)[1])

	def test_print_safe_and_scoped(self):
		"""No flex or grid (the PDF engines here lay neither out), and every rule under .kb-doc, so the
		same markup sits in the Desk without restyling it."""
		page = D.render(_sheet())
		self.assertNotRegex(page, r"display\s*:\s*(inline-)?(flex|grid)")
		css = re.search(r"<style>(.*)</style>", D.STYLE, re.S).group(1)
		css = re.sub(r"@media[^{]*\{", "", css)
		for rule in re.findall(r"([^{}]+)\{[^{}]*\}", css):
			for selector in rule.split(","):
				selector = selector.strip()
				if selector:
					with self.subTest(selector=selector):
						self.assertTrue(selector.startswith(".kb-doc"), selector)

	def test_the_cells_beat_frappes_print_padding(self):
		"""standard.css and the Redesign style set ``.print-format td`` padding ``!important``: only a
		more specific ``!important`` rule wins, so each cell rule names two classes and ``!important``."""
		for table in ("kb-owner", "kb-revisions"):
			with self.subTest(table=table):
				self.assertRegex(
					D.STYLE, rf"\.kb-doc table\.{table} td[^{{]*\{{[^}}]*padding:[^;]*!important"
				)
		self.assertRegex(
			D.STYLE,
			r"\.kb-doc \.kb-body table td, \.kb-doc \.kb-body table th \{[^}]*padding:[^;]*!important",
		)


# ================================================================== the glue, over a frappe stub

STATE = {}


class Refused(Exception):
	pass


class PermissionRefused(Refused):
	pass


class _Row(dict):
	def __getattr__(self, key):
		return self.get(key)


class _Doc:
	def __init__(self, values):
		self.__dict__.update(copy.deepcopy(values))

	def __getattr__(self, key):
		if key.startswith("_"):
			raise AttributeError(key)
		return None

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def get_content(self):
		return STATE["contents"][self.name]


def _reset():
	STATE.clear()
	STATE.update(
		{
			"db": {"Knowledge Article": {}, "Knowledge Article Version": {}, "File": {}},
			"readable": {"Knowledge Article", "Knowledge Article Version"},
			"permission_asked": [],
			"employees": {OWNER: "Purchasing Agent/Inventory Clerk"},
			"names": {AUTHOR: "Parker Bailey", APPROVER: "James Harris", "nik@example.com": "Nik Bradshaw"},
			"contents": {},
		}
	)


def _throw(message, exc=None, title=None, **kwargs):
	raise (PermissionRefused if exc is PermissionRefused else Refused)(message)


def _exists(doctype, name=None):
	return name in STATE["db"].get(doctype, {})


def _get_doc(doctype, name=None):
	return _Doc(STATE["db"][doctype][name])


def _has_permission(doctype, ptype="read", doc=None, **kwargs):
	STATE["permission_asked"].append((doctype, ptype, getattr(doc, "name", None), getattr(doc, "body", None)))
	return doctype in STATE["readable"]


def _get_value(doctype, filters, fieldname, **kwargs):
	assert doctype == "Employee", doctype
	assert filters.get("status") == "Active", filters
	return STATE["employees"].get(filters.get("user_id"))


def _get_all(doctype, filters=None, fields=None, **kwargs):
	assert doctype == "File", doctype
	rows = []
	for row in STATE["db"]["File"].values():
		if all(row.get(key) == value for key, value in (filters or {}).items()):
			rows.append(_Row({field: row.get(field) for field in fields}))
	return rows


def _getdate(value=None):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda message, *a, **k: message
	frappe.throw = _throw
	frappe.PermissionError = PermissionRefused
	frappe.db = types.SimpleNamespace(exists=_exists, get_value=_get_value)
	frappe.get_doc = _get_doc
	frappe.get_all = _get_all
	frappe.has_permission = _has_permission
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") and str(v).strip() else 0
	utils.get_fullname = lambda user=None: STATE["names"].get(user, user)
	utils.getdate = _getdate
	utils.nowdate = lambda: "2026-10-03"
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


printing = None


def setUpModule():
	global printing
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.knowledge_base.printing", None)
	printing = importlib.import_module("erpnext_enhancements.knowledge_base.printing")


def _article(**values):
	article = {
		"doctype": "Knowledge Article",
		"name": "SOP-06-0001",
		"title": "Receiving a PO against a packing slip",
		"kind": "SOP",
		"department_block": "06 Operations",
		"status": "Published",
		"version_number": 1,
		"approved_on": "2026-09-30 14:00:00",
		"approved_by": APPROVER,
		"author": "nik@example.com",
		"change_note": "Initial release",
		"process_owner": OWNER,
		"body": SOP_BODY,
		"live_version": "KBV-00001",
		"revisions": [],
	}
	article.update(values)
	STATE["db"]["Knowledge Article"][article["name"]] = article
	return article


def _stored_version(**values):
	version = {
		"doctype": "Knowledge Article Version",
		"name": "KBV-00002",
		"docstatus": 0,
		"review_state": "Draft",
		"title": "Receiving a PO against a packing slip",
		"kind": "SOP",
		"department_block": "06 Operations",
		"owner": AUTHOR,
		"process_owner": OWNER,
		"body": "<h2>Purpose</h2><p>Draft text.</p>",
		"change_note": "Added the damaged-goods step",
		"article": None,
		"version_number": None,
	}
	version.update(values)
	STATE["db"]["Knowledge Article Version"][version["name"]] = version
	return version


def _file(name, url, doctype, docname, content=b"\x89PNG....", size=None, private=1):
	STATE["db"]["File"][name] = {
		"name": name,
		"file_url": url,
		"attached_to_doctype": doctype,
		"attached_to_name": docname,
		"is_private": private,
		"file_size": len(content) if size is None else size,
	}
	STATE["contents"][name] = content


class GlueBase(unittest.TestCase):
	def setUp(self):
		_reset()


class TestKbDocumentDrawsTheSavedRecord(GlueBase):
	def test_anything_else_draws_nothing(self):
		self.assertEqual(printing.kb_document(_Doc({"doctype": "Sales Invoice", "name": "SINV-1"})), "")
		self.assertEqual(printing.kb_document(None), "")

	def test_it_draws_the_article_as_saved_not_as_handed(self):
		_article()
		handed = _Doc(
			{**_article(), "title": "Forged title", "body": "<p>Forged text</p>", "live_version": "KBV-99999"}
		)
		page = printing.kb_document(handed)
		self.assertIn("Receiving a PO against a packing slip", page)
		self.assertNotIn("Forged", page)
		self.assertNotIn("KBV-99999", page)
		self.assertEqual(STATE["permission_asked"], [("Knowledge Article", "read", "SOP-06-0001", SOP_BODY)])

	def test_a_reader_who_cannot_read_it_is_refused(self):
		_stored_version()
		STATE["readable"] = {"Knowledge Article"}
		with self.assertRaises(PermissionRefused):
			printing.kb_document(_Doc({"doctype": "Knowledge Article Version", "name": "KBV-00002"}))

	def test_an_unsaved_draft_is_refused(self):
		for name in (None, "", "new-knowledge-article-version-abc"):
			with self.subTest(name=name):
				with self.assertRaisesRegex(Refused, "Save the draft to preview it"):
					printing.kb_document(_Doc({"doctype": "Knowledge Article Version", "name": name}))
		with self.assertRaisesRegex(Refused, "There is no article"):
			printing.kb_document(_Doc({"doctype": "Knowledge Article", "name": "SOP-06-0404"}))

	def test_the_articles_page_and_its_footnote(self):
		_article()
		page = printing.kb_document(_Doc({"doctype": "Knowledge Article", "name": "SOP-06-0001"}))
		self.assertIn("Document ID: SOP-06-0001", page)
		self.assertIn("Last Updated: 09/30/2026", page)
		self.assertIn("<td>Purchasing Agent/Inventory Clerk (Parker Bailey)</td>", page)
		self.assertIn("<td>Operations</td>", page)
		self.assertIn(
			"SOP-06-0001 version 1, printed 10/03/2026. A printed copy is not controlled: the current "
			"version is in the ERPNext Knowledge Base.",
			page,
		)
		self.assertNotIn('kb-watermark">', page)


class TestTheArticlesHistory(GlueBase):
	def test_the_rows_oldest_first_by_name_never_by_address(self):
		rows = [
			{
				"version_number": 2,
				"approved_on": "2026-10-02 09:00:00",
				"author": AUTHOR,
				"approved_by": "nik@example.com",
				"change_note": "Second",
			},
			{
				"version_number": 1,
				"approved_on": "2026-09-30 14:00:00",
				"author": "nik@example.com",
				"approved_by": APPROVER,
				"change_note": "Initial release",
			},
		]
		lines = printing.article_revisions(_Doc(_article(version_number=2, revisions=rows)))
		self.assertEqual([line.version for line in lines], [1, 2])
		self.assertEqual((lines[0].author, lines[0].approved_by), ("Nik Bradshaw", "James Harris"))
		self.assertEqual(lines[1].date, datetime.date(2026, 10, 2))

	def test_an_article_published_before_the_rows_shows_its_live_line(self):
		lines = printing.article_revisions(_Doc(_article()))
		self.assertEqual(len(lines), 1)
		self.assertEqual(
			(lines[0].version, lines[0].author, lines[0].approved_by, lines[0].note),
			(1, "Nik Bradshaw", "James Harris", "Initial release"),
		)

	def test_a_retired_article_says_so(self):
		sheet = printing.article_sheet(
			_Doc(
				_article(
					status="Retired",
					retired_on="2026-10-05 08:00:00",
					retired_reason="Replaced by PRO-06-0001",
				)
			)
		)
		self.assertEqual(sheet.notice.heading, "Retired on 10/05/2026")
		self.assertEqual(sheet.notice.text, "Replaced by PRO-06-0001")

	def test_an_owner_with_no_active_employee_record_is_their_name(self):
		STATE["employees"] = {}
		self.assertEqual(printing.article_sheet(_Doc(_article())).owner_role, "")

	def test_the_articles_page_names_no_version_doctype(self):
		"""The reader path reads the article and its rows only (README, "Every leak path")."""
		source = (MODULE_DIR / "printing.py").read_text(encoding="utf-8")
		start = source.index("def article_sheet")
		end = source.index("def version_sheet")
		self.assertNotIn("VERSION", source[start:end])
		self.assertNotIn("Knowledge Article Version", source[start:end])
		start = source.index("def article_revisions")
		end = source.index("def revision_values")
		self.assertNotIn("VERSION", source[start:end])


class TestTheDraftPreview(GlueBase):
	def _page(self, name="KBV-00002"):
		return printing.kb_document(_Doc({"doctype": "Knowledge Article Version", "name": name}))

	def test_a_first_draft(self):
		_stored_version()
		page = self._page()
		self.assertIn("Document ID: SOP-06-####", page)
		self.assertIn("Version: 1", page)
		self.assertIn("Last Updated: Not yet approved", page)
		self.assertIn('<div class="kb-notice-heading">Draft - not approved</div>', page)
		self.assertIn('<div class="kb-watermark">DRAFT</div>', page)
		self.assertIn(
			'<tr class="kb-pending"><td>1 (draft)</td><td>Not yet approved</td><td>Parker Bailey</td>', page
		)
		self.assertIn(
			"A preview of KBV-00002, printed 10/03/2026. It is not company guidance until it is published.",
			page,
		)

	def test_a_revision_follows_its_articles_approved_lines(self):
		_article()
		_stored_version(article="SOP-06-0001", version_number=2)
		page = self._page()
		self.assertIn("Document ID: SOP-06-0001", page)
		self.assertIn("Version: 2", page)
		self.assertIn("<td>Initial release</td>", page)
		self.assertIn("<td>2 (draft)</td>", page)
		self.assertLess(page.index("Initial release"), page.index("2 (draft)"))

	def test_an_approved_version_is_the_record_with_no_notice(self):
		_article()
		_stored_version(
			name="KBV-00001",
			docstatus=1,
			review_state="Published",
			article="SOP-06-0001",
			version_number=1,
			approved_on="2026-09-30 14:00:00",
			approved_by=APPROVER,
		)
		page = self._page("KBV-00001")
		self.assertNotIn("kb-notice", page.split("</style>", 1)[1])
		self.assertNotIn('<div class="kb-watermark">', page)
		self.assertIn("<td>1</td><td>09/30/2026</td><td>Parker Bailey</td><td>James Harris</td>", page)
		self.assertIn("KBV-00001, published as SOP-06-0001 version 1, printed 10/03/2026.", page)

	def test_in_review_says_so(self):
		_stored_version(review_state="In Review")
		self.assertIn('<div class="kb-notice-heading">In review - not approved</div>', self._page())

	def test_superseded_and_discarded_are_history(self):
		for state, heading in (("Superseded", "Superseded"), ("Discarded", "Discarded")):
			with self.subTest(state=state):
				_reset()
				_article()
				_stored_version(
					review_state=state,
					docstatus=1 if state == "Superseded" else 0,
					article="SOP-06-0001",
					version_number=1,
				)
				page = self._page()
				self.assertIn('<div class="kb-notice kb-notice-history">', page)
				self.assertIn(heading, page)


class TestPicturesInThePrintedPage(GlueBase):
	def test_only_a_file_attached_to_the_record_is_written_in(self):
		_file("F1", "/private/files/map.png", "Knowledge Article", "SOP-06-0001", content=b"PNGDATA")
		_file("F2", "/private/files/payroll.png", "Employee", "HR-EMP-1", content=b"SECRET")
		body = '<p><img src="/private/files/map.png?fid=F1"><img src="/private/files/payroll.png"></p>'
		out = printing.inline_images(body, [("Knowledge Article", "SOP-06-0001")])
		self.assertIn('src="data:image/png;base64,UE5HREFUQQ=="', out)
		self.assertIn('src="/private/files/payroll.png"', out)

	def test_a_revision_may_use_its_articles_pictures(self):
		_stored_version(article="SOP-06-0001")
		_file("F1", "/private/files/map.png", "Knowledge Article", "SOP-06-0001", content=b"PNGDATA")
		saved = _Doc(STATE["db"]["Knowledge Article Version"]["KBV-00002"])
		self.assertEqual(
			printing._attachments(saved),
			[("Knowledge Article Version", "KBV-00002"), ("Knowledge Article", "SOP-06-0001")],
		)

	def test_an_escaped_ampersand_the_path_alone_and_single_quotes(self):
		_file("F1", "/private/files/map one.png", "Knowledge Article", "SOP-06-0001", content=b"PNGDATA")
		body = "<img src='/private/files/map%20one.png?x=1&amp;fid=F1'><img src=\"/private/files/map%20one.png\">"
		out = printing.inline_images(body, [("Knowledge Article", "SOP-06-0001")])
		self.assertEqual(out.count("data:image/png;base64,"), 2)

	def test_too_big_or_not_a_picture_is_left_as_a_link(self):
		_file(
			"F1",
			"/private/files/huge.png",
			"Knowledge Article",
			"SOP-06-0001",
			size=printing.INLINE_IMAGE_LIMIT + 1,
		)
		_file("F2", "/private/files/notes.pdf", "Knowledge Article", "SOP-06-0001", content=b"%PDF")
		body = '<img src="/private/files/huge.png"><img src="/private/files/notes.pdf">'
		self.assertEqual(printing.inline_images(body, [("Knowledge Article", "SOP-06-0001")]), body)

	def test_the_forms_copy_leaves_pictures_to_the_browser(self):
		_file("F1", "/private/files/map.png", "Knowledge Article", "SOP-06-0001", content=b"PNGDATA")
		article = _Doc(_article(body='<h2>Purpose</h2><p><img src="/private/files/map.png?fid=F1"></p>'))
		page = printing.article_html(article)
		self.assertIn('src="/private/files/map.png?fid=F1"', page)
		self.assertIn('<div class="kb-doc kb-screen">', page)
		self.assertNotIn('kb-footnote">', page)


# ================================================================== the wiring


class TestWiring(unittest.TestCase):
	def test_each_format_is_one_call_to_the_global(self):
		source = (MODULE_DIR / "setup_print_formats.py").read_text(encoding="utf-8")
		html = re.search(r"FORMAT_HTML = \((.*?)\n\)", source, re.S).group(1)
		self.assertIn("{{ kb_document(doc) }}", html)
		self.assertNotRegex(html, r"doc\.\w")
		self.assertIn("ARTICLE_FORMAT: constants.ARTICLE_DOCTYPE", source)
		self.assertIn("VERSION_FORMAT: constants.VERSION_DOCTYPE", source)
		self.assertIn('pf.pdf_generator = "chrome"', source)

	def test_the_global_and_the_hook_are_registered_the_hook_above_the_chrome_pin(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.knowledge_base.printing.kb_document",', hooks)
		ours = hooks.index(
			'"erpnext_enhancements.knowledge_base.setup_print_formats.ensure_knowledge_base_print_formats",'
		)
		pin = hooks.index(
			'"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_chrome_pdf_generator",'
		)
		self.assertLess(ours, pin)

	def test_the_forms_draw_the_document_preview_and_fill_the_template(self):
		article_js = (APP / "public" / "js" / "knowledge_base" / "knowledge_article.js").read_text(
			encoding="utf-8"
		)
		self.assertIn('frm.get_field("document_view")', article_js)
		self.assertIn("frm.print_doc()", article_js)
		version_js = (APP / "public" / "js" / "knowledge_base" / "knowledge_article_version.js").read_text(
			encoding="utf-8"
		)
		self.assertIn('kb_call("document_template", { kind: frm.doc.kind }', version_js)
		self.assertIn('"GET"', version_js)
		self.assertIn("kind(frm) {", version_js)
		self.assertIn('__("Preview")', version_js)
		# A body is read with DOMParser, never jQuery's .html(), which runs a script in what it is given.
		self.assertNotIn('$("<div>").html(', version_js)

	def test_the_drafting_tool_names_every_kinds_sections(self):
		source = (APP / "assistant_tools" / "draft_knowledge_article.py").read_text(encoding="utf-8")
		self.assertIn("constants.kind_section_names(kind)", source)
		self.assertIn("Do not number them or add a Revision History", source)
		self.assertIn("printed Revision History", source)


if __name__ == "__main__":
	unittest.main()

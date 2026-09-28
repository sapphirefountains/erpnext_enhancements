# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's entry points and its two reports (WI-080 PR 4). Bench-free, own ``frappe`` stub.

PR 4 is what makes the knowledge base findable without typing a URL: a workspace with its sidebar, a
Desk home-screen tile, a Help menu item, and two Script Reports. Every one of them fails **silently**
in v16, which is why this suite exists rather than a paragraph in the README:

* **The workspace vanishes** for anyone who holds no DocPerm on a non-child doctype in its module
  (``desk/desktop.py:38-44``, swallowed at ``:421``). Every staff user reaches it through Knowledge
  Article's ``Desk User`` read, so that row, the doctype's module and its not being a child, a single
  or ``read_only`` (which drops it from ``can_read``, ``utils/user.py:140-146``) are pinned here.
* **The tile does not render** unless a same-named Workspace Sidebar has an item the viewer may see
  (``desktop_icon.py:193-196``). The sidebar's first item is the workspace, and a reader can see two.
* **A workspace or sidebar JSON whose ``modified`` did not move is never re-imported.** Each file's
  content is fingerprinted with its stamp: change the file and this suite fails until the stamp moves
  past the pinned one (and the pin is updated).
* **A link a reader cannot open** must be dropped by v16, not shown: every item is a DocType, Report
  or Workspace link (a URL item is never permission-filtered), every KB-only one points at something
  only the KB roles can open, and the reader's blocks come first so a reader's page has no holes.
* **A filter that matches nothing reads as an empty queue.** Every filter names a real field and a
  real option; "My drafts" is a JS expression on purpose (v16 evaluates ``stats_filter`` with
  ``new Function``), and the sidebar's ``route_options`` are plain strings, since v16 writes them into
  the URL with ``encodeURIComponent`` (``utils.js:1608-1613``).
* **The Help item** routes to ``/desk/`` + the workspace's slug, as a Route (``ui/menu.js:169-170``).
* **The reports**: their roles and ``ref_doctype`` (who can run them, through the Desk or an AI
  tool's ``generate_report``), bound SQL with no function strings, no draft text selected, no text
  quoted in any row, and every rule of ``reporting.review_due`` and ``reporting.integrity_problems``.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_entry_points -v
"""

import ast
import datetime
import hashlib
import importlib
import json
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path
from xml.etree import ElementTree

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.knowledge_base import constants as K
from erpnext_enhancements.knowledge_base import content as C
from erpnext_enhancements.knowledge_base import reporting as R
from erpnext_enhancements.setup.desktop_icon_map import ASSET_SUBDIR, TILES, logo_url

KB = APP / "knowledge_base"
WORKSPACE_PATH = KB / "workspace" / "knowledge_base" / "knowledge_base.json"
SIDEBAR_PATH = APP / "workspace_sidebar" / "knowledge_base.json"
ARTICLE_JSON = KB / "doctype" / "knowledge_article" / "knowledge_article.json"
VERSION_JSON = KB / "doctype" / "knowledge_article_version" / "knowledge_article_version.json"
REPORT_DIR = KB / "report"
DUE_REPORT = "Knowledge Articles Due for Review"
INTEGRITY_REPORT = "Knowledge Base Integrity"
DUE_MODULE = "erpnext_enhancements.knowledge_base.report.knowledge_articles_due_for_review.knowledge_articles_due_for_review"
INTEGRITY_MODULE = (
	"erpnext_enhancements.knowledge_base.report.knowledge_base_integrity.knowledge_base_integrity"
)
HOOKS = APP / "hooks.py"
README = KB / "README.md"
SETUP_MODULE = "erpnext_enhancements.setup.desktop_icons"
EMAILED_MODULE = "erpnext_enhancements.knowledge_base.emailed_reports"

WORKSPACE = "Knowledge Base"
ARTICLE = K.ARTICLE_DOCTYPE
VERSION = K.VERSION_DOCTYPE

#: What each file held when its ``modified`` was last set, and that stamp. A workspace or sidebar is
#: imported only when the file's ``modified`` beats the stored row's (``modules/import_file.py``), so an
#: edit that keeps the stamp never reaches a site. Change a file, and this suite fails until you move
#: its ``modified`` past the stamp below; then put the new fingerprint and stamp here.
PINNED = {
	WORKSPACE_PATH: (
		"9414c2df5542efac3b44079d1aaf0a571698de41bb51932a2fef0118dd1dfb8d",
		"2026-09-28 22:00:00.000000",
	),
	SIDEBAR_PATH: (
		"602355a1de2f7d8bd5e5f405064d1b1497fddbd5071f601b71751fa5b957e3ca",
		"2026-09-28 21:00:00.000000",
	),
}

#: The role sets v16 gives the three kinds of staff user this PR serves (``permissions.py:549-563``:
#: ``All`` and ``Guest`` for everyone signed in, ``Desk User`` for a System User).
READER = frozenset({"Desk User", "All", "Guest"})
AUTHOR = READER | {K.AUTHOR_ROLE}
APPROVER = READER | {K.APPROVER_ROLE}
PORTAL = frozenset({"All", "Guest"})

#: What each of them sees on the workspace (blocks, by label) and in its sidebar (items, by label).
READER_BLOCKS = {"Published articles", "Newest articles"}
AUTHOR_BLOCKS = READER_BLOCKS | {
	"My drafts",
	"In review",
	"Due for review",
	"New draft",
	"Writing and review",
}
APPROVER_BLOCKS = AUTHOR_BLOCKS
READER_SIDEBAR = {"Home", "Published articles"}
AUTHOR_SIDEBAR = READER_SIDEBAR | {
	"Writing and review",
	"Drafts",
	"In review",
	"Due for review",
	"All versions",
}
APPROVER_SIDEBAR = AUTHOR_SIDEBAR | {"Integrity check"}

#: A draft's text, and the article's, planted in every text field of the report corpus. None may
#: appear in any report row or message.
SENTINELS = ("DRAFT-TITLE-7Q", "DRAFT-BODY-7Q", "DRAFT-SUMMARY-7Q", "DRAFT-KEYWORD-7Q", "DRAFT-NOTE-7Q")
TEXT_FIELDS = ("title", "summary", "keywords", "body", "change_note", "review_note")

PARKER = "parker@example.com"
JAMES = "james@example.com"
NIK = "nik@example.com"
LISA = "lisa@example.com"
TODAY = datetime.date(2026, 9, 28)


def load(path):
	return json.loads(path.read_text(encoding="utf-8"))


def slug(name):
	"""``frappe.router.slug``: ``name.toLowerCase().replace(/ /g, "-")``."""
	return str(name or "").lower().replace(" ", "-")


def fingerprint(doc):
	doc = {key: value for key, value in doc.items() if key != "modified"}
	return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def hook(name):
	for node in ast.parse(HOOKS.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	return None


def readme_checks(text=None):
	"""The Check column of the Integrity table in the README's "### The two reports", in order."""
	text = README.read_text(encoding="utf-8") if text is None else text
	section = text.split("\n### The two reports\n", 1)[1].split("\n## ", 1)[0]
	rows, in_table = [], False
	for line in section.splitlines():
		line = line.strip()
		if not line.startswith("|"):
			if in_table:
				break
			continue
		cells = [cell.strip() for cell in line.strip("|").split("|")]
		if cells[:2] == ["Check", "What must hold"]:
			in_table = True
		elif in_table and not set(cells[0]) <= set("-: "):
			rows.append(cells[0])
	return rows


def report_json(name):
	folder = name.replace(" ", "_").replace("-", "_").lower()
	return load(REPORT_DIR / folder / f"{folder}.json")


def perms(doctype_json):
	return [row for row in doctype_json["permissions"] if not row.get("permlevel")]


def readable(doctype, roles):
	"""Whether a user holding ``roles`` reads ``doctype`` at level 0 (v16 ``can_read``)."""
	path = {ARTICLE: ARTICLE_JSON, VERSION: VERSION_JSON}[doctype]
	return any(row.get("read") and row["role"] in roles for row in perms(load(path)))


def reportable(doctype, roles):
	path = {ARTICLE: ARTICLE_JSON, VERSION: VERSION_JSON}[doctype]
	return any(row.get("report") and row["role"] in roles for row in perms(load(path)))


def report_runnable(name, roles):
	"""v16 ``get_report_doc`` (``desk/query_report.py:43-53``): the report's roles, then ``report``
	on its ``ref_doctype``. ``DeskViews.get_allowed_reports`` shows a report link on the same roles."""
	doc = report_json(name)
	allowed = {row["role"] for row in doc["roles"]}
	return bool(allowed & roles) and reportable(doc["ref_doctype"], roles)


def workspace_open(roles):
	"""The module gate (``desk/desktop.py:38-44``): a non-child doctype of the module the user reads.
	The workspace's own roles are empty (pinned), so nothing else applies."""
	return readable(ARTICLE, roles) or readable(VERSION, roles)


def target_visible(link_type, link_to, roles):
	if link_type == "DocType":
		return readable(link_to, roles)
	if link_type == "Report":
		return report_runnable(link_to, roles)
	if link_type == "Workspace":
		assert link_to == WORKSPACE, link_to
		return workspace_open(roles)
	raise AssertionError(
		f"{link_type} {link_to}: only DocType, Report and Workspace links are filtered by v16"
	)


def visible_blocks(roles):
	"""The workspace blocks v16 renders for ``roles`` (``Workspace.get_shortcuts``, ``get_quick_lists``,
	``get_links``: each drops what ``is_item_allowed`` refuses, and a card with nothing left)."""
	ws = load(WORKSPACE_PATH)
	shortcuts = {row["label"]: row for row in ws["shortcuts"]}
	quick_lists = {row["label"]: row for row in ws["quick_lists"]}
	cards = card_links(ws)
	seen = []
	for block in json.loads(ws["content"]):
		data = block.get("data") or {}
		kind = block["type"]
		if kind == "shortcut":
			row = shortcuts[data["shortcut_name"]]
			if target_visible(row["type"], row["link_to"], roles):
				seen.append(row["label"])
		elif kind == "quick_list":
			row = quick_lists[data["quick_list_name"]]
			if readable(row["document_type"], roles):
				seen.append(row["label"])
		elif kind == "card":
			links = cards[data["card_name"]]
			if any(target_visible(link["link_type"], link["link_to"], roles) for link in links):
				seen.append(data["card_name"])
	return seen


def card_links(ws):
	cards, current = {}, None
	for row in ws["links"]:
		if row["type"] == "Card Break":
			current = cards.setdefault(row["label"], [])
		else:
			current.append(row)
	return cards


def visible_sidebar(roles):
	"""v16 ``boot.get_sidebar_items`` (``boot.py:463-505``) then ``sidebar_item.js``: an item survives
	``is_item_allowed``; a Section Break always does, but renders only with a child left (``:189``)."""
	items = load(SIDEBAR_PATH)["items"]
	kept = [
		item
		for item in items
		if item["type"] == "Section Break" or target_visible(item["link_type"], item["link_to"], roles)
	]
	shown, section = [], None
	for item in kept:
		if item["type"] == "Section Break":
			section = item
			continue
		if item.get("child") and section is not None:
			if section["label"] not in shown:
				shown.append(section["label"])
		shown.append(item["label"])
	return shown


# ------------------------------------------------------------------ the frappe stub (reports only)


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)


DB = {}
SQL_CALLS = []

_SELECT = re.compile(r"^select (?P<columns>.+?) from `tab(?P<table>[^`]+)`(?P<rest>.*)$", re.S)


def _sql(query, values=None, as_dict=False, **kwargs):
	"""Runs the four shapes of query the two reports send, against ``DB``. Anything else fails, so a
	changed query cannot pass here without this emulation being changed to match."""
	assert as_dict, "the reports read rows as dicts"
	SQL_CALLS.append((query, values))
	match = _SELECT.match(query)
	assert match, query
	columns = [c.strip() for c in match.group("columns").split(",")]
	rows = list(DB.get(match.group("table"), []))
	rest = match.group("rest")
	clauses = {
		" where status = %(status)s": lambda r: r.get("status") == values["status"],
		" docstatus = 1": lambda r: r.get("docstatus") == 1,
		" name in %(names)s": lambda r: r.get("name") in values["names"],
		" where attached_to_doctype in %(doctypes)s": lambda r: r.get("attached_to_doctype")
		in values["doctypes"],
		" ifnull(is_private, 0) = 0": lambda r: not r.get("is_private"),
	}
	remaining = rest.replace(" and", "").replace(" where docstatus = 1", " docstatus = 1")
	for clause, keep in clauses.items():
		if clause in remaining:
			rows = [r for r in rows if keep(r)]
			remaining = remaining.replace(clause, "")
	assert remaining.strip() in ("", "order by name"), f"unexpected SQL: {rest!r}"
	return [_Dict({c: r.get(c) for c in columns}) for r in rows]


def _getdate(value):
	return datetime.date.fromisoformat(str(value)[:10])


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda message, *a, **k: message
	frappe.db = types.SimpleNamespace(sql=_sql)
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.nowdate = lambda: TODAY.isoformat()
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


due_report = None
integrity_report = None
desktop_icons = None
emailed_reports = None


def setUpModule():
	global due_report, integrity_report, desktop_icons, emailed_reports
	_install_frappe_stub()
	for name in (DUE_MODULE, INTEGRITY_MODULE, SETUP_MODULE, EMAILED_MODULE):
		sys.modules.pop(name, None)
	due_report = importlib.import_module(DUE_MODULE)
	integrity_report = importlib.import_module(INTEGRITY_MODULE)
	desktop_icons = importlib.import_module(SETUP_MODULE)
	emailed_reports = importlib.import_module(EMAILED_MODULE)


# ------------------------------------------------------------------ the corpus


LIVE_TEXT = {
	"title": "Receiving a PO against a packing slip",
	"summary": "How to receive a purchase order.",
	"keywords": "PO, receiving",
	"body": "<p>Open the PO, press <strong>Receive</strong>, and check each line against the slip.</p>",
}


def corpus():
	"""One healthy article on its second version, with the whole history a real one has: the
	version it superseded, an open revision, a discarded first draft and another article's first
	draft in review. Every draft carries sentinel text."""
	draft_text = {
		"title": SENTINELS[0],
		"body": f"<p>{SENTINELS[1]}</p>",
		"summary": SENTINELS[2],
		"keywords": SENTINELS[3],
		"change_note": SENTINELS[4],
		"review_note": SENTINELS[4],
	}
	article = {
		"name": "KB-0612",
		"status": "Published",
		"department_block": "06 Operations",
		"version_number": 2,
		"live_version": "KBV-00003",
		"content_hash": C.content_hash(LIVE_TEXT),
		"approved_by": JAMES,
		"author": PARKER,
		"process_owner": PARKER,
		"review_by": datetime.date(2027, 3, 28),
		"body_md": "Open the PO",
		**LIVE_TEXT,
	}
	base = {"ai_requested_by": None, "department_block": "06 Operations", "reviewer": None}
	versions = [
		{
			**base,
			**LIVE_TEXT,
			"name": "KBV-00001",
			"article": "KB-0612",
			"review_state": "Superseded",
			"docstatus": 1,
			"version_number": 1,
			"owner": PARKER,
			"submitted_by": PARKER,
			"contributors": PARKER,
			"approved_by": NIK,
		},
		{
			**base,
			**draft_text,
			"name": "KBV-00002",
			"article": None,
			"review_state": "Discarded",
			"docstatus": 0,
			"version_number": 0,
			"owner": LISA,
			"submitted_by": None,
			"contributors": LISA,
			"approved_by": None,
		},
		{
			**base,
			**LIVE_TEXT,
			"name": "KBV-00003",
			"article": "KB-0612",
			"review_state": "Published",
			"docstatus": 1,
			"version_number": 2,
			"owner": PARKER,
			"submitted_by": PARKER,
			"contributors": f"{PARKER}\n{LISA}",
			"approved_by": JAMES,
		},
		{
			**base,
			**draft_text,
			"name": "KBV-00004",
			"article": "KB-0612",
			"review_state": "Draft",
			"docstatus": 0,
			"version_number": 3,
			"owner": PARKER,
			"submitted_by": None,
			"contributors": PARKER,
			"approved_by": None,
		},
		{
			**base,
			**draft_text,
			"name": "KBV-00005",
			"article": None,
			"review_state": "In Review",
			"docstatus": 0,
			"version_number": 0,
			"owner": PARKER,
			"submitted_by": PARKER,
			"contributors": PARKER,
			"approved_by": None,
			"department_block": "02 Design",
		},
	]
	files = [
		{"name": "f-private", "attached_to_doctype": ARTICLE, "attached_to_name": "KB-0612", "is_private": 1},
		{"name": "f-other", "attached_to_doctype": "Task", "attached_to_name": "TASK-1", "is_private": 0},
	]
	return {"articles": [article], "versions": versions, "files": files}


def problems_of(data):
	"""``integrity_problems`` fed the way the controller feeds it: metadata for every version, the
	approved text of submitted live versions only, and public KB Files."""
	versions = [{f: v.get(f) for f in R.INTEGRITY_VERSION_FIELDS} for v in data["versions"]]
	live = {a["live_version"] for a in data["articles"]}
	texts = {
		v["name"]: {f: v.get(f) for f in R.APPROVED_TEXT_FIELDS}
		for v in data["versions"]
		if v["name"] in live and v["docstatus"] == 1
	}
	files = [f for f in data["files"] if f["attached_to_doctype"] in K.KB_DOCTYPES and not f["is_private"]]
	return R.integrity_problems(data["articles"], versions, texts, files)


def checks(problems):
	return {row["check"] for row in problems}


def version(data, name):
	return next(v for v in data["versions"] if v["name"] == name)


# ------------------------------------------------------------------ the workspace


class TestWorkspace(unittest.TestCase):
	def setUp(self):
		self.ws = load(WORKSPACE_PATH)

	def test_its_names_and_module(self):
		for key in ("name", "label", "title"):
			self.assertEqual(self.ws[key], WORKSPACE, key)
		self.assertEqual(self.ws["module"], "Knowledge Base")
		self.assertEqual(self.ws["app"], "erpnext_enhancements")
		self.assertEqual(self.ws["doctype"], "Workspace")

	def test_it_is_public_and_gated_only_by_the_module(self):
		"""``roles: []`` means "no restriction beyond the module gate", and the module gate is the
		Article's Desk User read: every staff user, and no portal user."""
		self.assertEqual(self.ws["public"], 1)
		self.assertEqual(self.ws["is_hidden"], 0)
		self.assertEqual(self.ws["roles"], [])
		self.assertEqual(self.ws["for_user"], "")
		self.assertEqual(self.ws["hide_custom"], 1)
		self.assertTrue(workspace_open(READER))
		self.assertFalse(workspace_open(PORTAL))

	def test_the_modified_stamp_moves_with_the_content(self):
		for path, (pinned, stamp) in PINNED.items():
			doc = load(path)
			with self.subTest(path=path.name):
				self.assertGreaterEqual(doc["modified"], doc["creation"])
				if fingerprint(doc) == pinned:
					self.assertEqual(doc["modified"], stamp)
				else:
					self.assertGreater(
						doc["modified"],
						stamp,
						f"{path.name} changed but its modified did not move past {stamp}, so no site "
						"will import it. Bump it, then pin the new fingerprint and stamp in PINNED.",
					)
					self.fail(
						f"{path.name} changed: pin fingerprint {fingerprint(doc)} and stamp {doc['modified']}"
					)

	def test_every_link_is_one_v16_filters_by_permission(self):
		"""A URL or Page item is never permission-filtered: it would show a reader a door to a refusal."""
		for row in self.ws["shortcuts"]:
			with self.subTest(shortcut=row["label"]):
				self.assertIn(row["type"], ("DocType", "Report"))
				self.assertNotIn("url", row)
		for row in self.ws["links"]:
			if row["type"] == "Link":
				with self.subTest(link=row["label"]):
					self.assertIn(row["link_type"], ("DocType", "Report"))
					self.assertEqual(row["is_query_report"], 1 if row["link_type"] == "Report" else 0)
		self.assertEqual(self.ws["custom_blocks"], [])
		self.assertEqual(self.ws["number_cards"], [])
		self.assertEqual(self.ws["charts"], [])

	def test_who_sees_what(self):
		self.assertEqual(set(visible_blocks(READER)), READER_BLOCKS)
		self.assertEqual(set(visible_blocks(AUTHOR)), AUTHOR_BLOCKS)
		self.assertEqual(set(visible_blocks(APPROVER)), APPROVER_BLOCKS)

	def test_the_integrity_check_is_for_approvers_only(self):
		(link,) = [row for row in self.ws["links"] if row.get("link_to") == INTEGRITY_REPORT]
		self.assertTrue(target_visible("Report", INTEGRITY_REPORT, APPROVER))
		self.assertFalse(target_visible("Report", INTEGRITY_REPORT, AUTHOR))
		self.assertFalse(target_visible("Report", INTEGRITY_REPORT, READER))
		self.assertFalse(target_visible("Report", INTEGRITY_REPORT, READER | {"System Manager"}))
		self.assertEqual(link["label"], "Integrity check")

	def test_a_readers_blocks_come_first_so_their_page_has_no_holes(self):
		"""v16 renders a refused shortcut as an empty block that still takes its columns
		(``blocks/shortcut.js:53-55``), so a KB-only block before a reader's leaves a hole."""
		ws = self.ws
		order = []
		for block in json.loads(ws["content"]):
			data = block.get("data") or {}
			label = data.get("shortcut_name") or data.get("quick_list_name") or data.get("card_name")
			if label:
				order.append(label)
		reader = [label for label in order if label in READER_BLOCKS]
		self.assertEqual(order[: len(reader)], reader)

	def test_every_kb_only_item_opens_for_a_kb_role(self):
		for label in AUTHOR_BLOCKS | APPROVER_BLOCKS:
			with self.subTest(label=label):
				self.assertIn(label, set(visible_blocks(AUTHOR)) | set(visible_blocks(APPROVER)))

	def test_the_shortcuts(self):
		rows = {row["label"]: row for row in self.ws["shortcuts"]}
		self.assertEqual(
			{label: (row["type"], row["link_to"], row.get("doc_view")) for label, row in rows.items()},
			{
				"Published articles": ("DocType", ARTICLE, "List"),
				"My drafts": ("DocType", VERSION, "List"),
				"In review": ("DocType", VERSION, "List"),
				"Due for review": ("Report", DUE_REPORT, None),
				"New draft": ("DocType", VERSION, "New"),
			},
		)

	def test_the_card_holds_the_full_lists(self):
		cards = card_links(self.ws)
		self.assertEqual(list(cards), ["Writing and review"])
		self.assertEqual(
			[(row["label"], row["link_type"], row["link_to"]) for row in cards["Writing and review"]],
			[
				("All versions", "DocType", VERSION),
				("Due for review", "Report", DUE_REPORT),
				("Integrity check", "Report", INTEGRITY_REPORT),
			],
		)

	def test_the_quick_list_is_the_newest_published_articles(self):
		(row,) = self.ws["quick_lists"]
		self.assertEqual(row["document_type"], ARTICLE)
		self.assertEqual(json.loads(row["quick_list_filter"]), [[ARTICLE, "status", "=", "Published"]])

	def test_the_search_hint_works_on_a_phone(self):
		"""On a phone v16 hides the whole standard-filter row, Title and Keywords included, until the
		chevrons-up-down button beside Filter is tapped, and moves only the ID box out of it
		(``base_list.js`` ``setup_mobile``, :662-698; :1159-1163). Technicians read on phones, so the
		paragraph that sends them to those boxes must say how to show them (PR 4 review)."""
		(intro,) = [b for b in json.loads(self.ws["content"]) if b["id"] == "kb_intro"]
		text = intro["data"]["text"]
		for box in ("<b>Title</b>", "<b>Keywords</b>", "<b>ID</b>"):
			self.assertIn(box, text)
		self.assertIn("on a phone", text)
		self.assertIn("up-and-down arrows button next to <b>Filter</b>", text)
		# The hint belongs to the Title/Keywords clause, not to the ID box's, which v16 keeps visible.
		self.assertLess(text.index("<b>Keywords</b>"), text.index("on a phone"))
		self.assertLess(text.index("on a phone"), text.index("<b>ID</b>"))


class TestFilters(unittest.TestCase):
	"""A filter on a field that does not exist, or a value that is not an option, counts nothing and
	lists nothing, forever, and reads as an empty queue."""

	def setUp(self):
		self.ws = load(WORKSPACE_PATH)
		self.fields = {
			ARTICLE: {f["fieldname"]: f for f in load(ARTICLE_JSON)["fields"]},
			VERSION: {f["fieldname"]: f for f in load(VERSION_JSON)["fields"]},
		}

	def _check(self, doctype, field, value):
		if field == "owner":
			return
		self.assertIn(field, self.fields[doctype], f"{doctype} has no field {field}")
		options = (self.fields[doctype][field].get("options") or "").split("\n")
		self.assertIn(value, options, f"{value!r} is not an option of {doctype}.{field}")

	def test_stats_filters_are_objects_of_real_fields_and_options(self):
		seen = 0
		for row in self.ws["shortcuts"]:
			raw = row.get("stats_filter")
			if not raw:
				continue
			seen += 1
			with self.subTest(shortcut=row["label"]):
				# frappe.session.user is the viewer: v16 evaluates the string with `new Function`
				# (utils.js process_filter_expression), which a JSON parser cannot.
				parsed = json.loads(raw.replace("frappe.session.user", '"<viewer>"'))
				self.assertIsInstance(parsed, dict)
				for field, (op, value) in parsed.items():
					self.assertEqual(op, "=")
					self._check(row["link_to"], field, value)
		self.assertEqual(seen, 3)

	def test_my_drafts_are_the_viewers_own_drafts(self):
		(row,) = [r for r in self.ws["shortcuts"] if r["label"] == "My drafts"]
		self.assertEqual(
			row["stats_filter"], '{"review_state":["=","Draft"],"owner":["=",frappe.session.user]}'
		)

	def test_the_quick_list_filter_is_the_array_form_of_its_own_doctype(self):
		for row in self.ws["quick_lists"]:
			for clause in json.loads(row["quick_list_filter"]):
				self.assertEqual(len(clause), 4)
				self.assertEqual(clause[0], row["document_type"])
				self._check(clause[0], clause[1], clause[3])

	def test_sidebar_route_options_are_plain_strings_of_real_fields(self):
		"""v16 appends ``route_options`` to the URL with ``encodeURIComponent(value)`` (``utils.js:
		1608-1613``), so an ``["=", "Draft"]`` would filter on the string ``=,Draft``."""
		seen = 0
		for item in load(SIDEBAR_PATH)["items"]:
			raw = item.get("route_options")
			if not raw:
				continue
			seen += 1
			with self.subTest(item=item["label"]):
				self.assertEqual(item["link_type"], "DocType")
				self.assertNotIn("filters", item)
				for field, value in json.loads(raw).items():
					self.assertIsInstance(value, str)
					self._check(item["link_to"], field, value)
		self.assertEqual(seen, 3)

	def test_the_states_filtered_on_are_the_constants(self):
		self.assertIn("Draft", K.OPEN_REVIEW_STATES)
		self.assertIn("In Review", K.OPEN_REVIEW_STATES)
		self.assertEqual(K.ARTICLE_STATUSES[0], "Published")

	def test_keywords_are_a_list_filter(self):
		"""The reader's search, until PR 5: v16 puts the title field, the ID and every
		``in_standard_filter`` field above the list, a text one as a ``like`` (``base_list.js``
		FilterArea), so "PO" finds an article v16's global search never would."""
		self.assertEqual(self.fields[ARTICLE]["keywords"].get("in_standard_filter"), 1)
		self.assertEqual(load(ARTICLE_JSON)["title_field"], "title")


class TestModuleGate(unittest.TestCase):
	"""The precondition the whole PR stands on: every staff user holds a DocPerm on a non-child
	doctype in the workspace's module, so the workspace, the tile and the Help item open for them."""

	def setUp(self):
		self.article = load(ARTICLE_JSON)

	def test_the_article_is_a_plain_doctype_in_the_module(self):
		self.assertEqual(self.article["module"], load(WORKSPACE_PATH)["module"])
		for key in ("istable", "issingle", "read_only", "is_virtual"):
			self.assertFalse(self.article.get(key), key)
		modules = (APP / "modules.txt").read_text(encoding="utf-8").splitlines()
		self.assertIn("Knowledge Base", [m.strip() for m in modules])

	def test_every_staff_user_reads_it_at_level_zero(self):
		rows = [r for r in perms(self.article) if r["role"] == "Desk User"]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].get("read"), 1)
		self.assertFalse(rows[0].get("if_owner"))

	def test_a_portal_user_reaches_nothing(self):
		for doctype in (ARTICLE, VERSION):
			self.assertFalse(readable(doctype, PORTAL))

	def test_a_reader_never_reaches_a_draft(self):
		self.assertFalse(readable(VERSION, READER | {"System Manager"}))


class TestSidebar(unittest.TestCase):
	def setUp(self):
		self.sidebar = load(SIDEBAR_PATH)

	def test_its_names(self):
		self.assertEqual(self.sidebar["name"], WORKSPACE)
		self.assertEqual(self.sidebar["title"], WORKSPACE)
		self.assertEqual(self.sidebar["app"], "erpnext_enhancements")
		self.assertEqual(self.sidebar["module"], "Knowledge Base")
		self.assertEqual(self.sidebar["standard"], 1)
		self.assertEqual(SIDEBAR_PATH.stem, WORKSPACE.replace(" ", "_").lower())

	def test_the_first_item_is_the_workspace(self):
		"""``add_workspace_to_desktop`` appends a workspace link unless one is there
		(``desktop_icon.py:334-341``), and ``setup/desktop_icons._create_tile`` calls it."""
		first = self.sidebar["items"][0]
		self.assertEqual(
			(first["type"], first["link_type"], first["link_to"]), ("Link", "Workspace", WORKSPACE)
		)

	def test_who_sees_what(self):
		self.assertEqual(set(visible_sidebar(READER)), READER_SIDEBAR)
		self.assertEqual(set(visible_sidebar(AUTHOR)), AUTHOR_SIDEBAR)
		self.assertEqual(set(visible_sidebar(APPROVER)), APPROVER_SIDEBAR)
		self.assertEqual(visible_sidebar(PORTAL), [])

	def test_a_reader_has_an_item_so_the_tile_renders(self):
		"""``get_desktop_icons`` keeps a Link tile only when the viewer's sidebar has an item
		(``desktop_icon.py:193-196``; ``boot.py:500-505`` drops a sidebar with none)."""
		self.assertTrue([label for label in visible_sidebar(READER) if label != "Writing and review"])

	def test_the_section_holds_the_kb_items(self):
		items = self.sidebar["items"]
		(section_at,) = [i for i, item in enumerate(items) if item["type"] == "Section Break"]
		self.assertEqual(items[section_at]["indent"], 1)
		for item in items[:section_at]:
			self.assertEqual(item["child"], 0)
		for item in items[section_at + 1 :]:
			with self.subTest(item=item["label"]):
				self.assertEqual(item["child"], 1)
				self.assertFalse(target_visible(item["link_type"], item["link_to"], READER))

	def test_it_links_both_doctypes_so_their_pages_keep_it(self):
		"""v16 shows a doctype's list and form with the sidebar that links that doctype."""
		linked = {item.get("link_to") for item in self.sidebar["items"] if item.get("link_type") == "DocType"}
		self.assertEqual(linked, {ARTICLE, VERSION})

	def test_every_icon_is_in_v16s_sprites(self):
		"""Skipped where there is no frappe checkout beside this repo (CI included)."""
		checkout = _frappe_checkout()
		if checkout is None:
			self.skipTest("no frappe checkout beside this repo")
		ids = set()
		for sprite in ("frappe/public/icons/lucide/icons.svg", "frappe/public/icons/timeless/icons.svg"):
			result = subprocess.run(
				["git", "-C", str(checkout), "show", f"origin/version-16:{sprite}"],
				capture_output=True,
				text=True,
				encoding="utf-8",
			)
			if result.returncode:
				self.skipTest(f"{checkout} has no origin/version-16:{sprite}")
			ids |= set(re.findall(r'id="icon-([a-z0-9-]+)"', result.stdout))
		icons = {item["icon"] for item in self.sidebar["items"]} | {
			self.sidebar["header_icon"],
			load(WORKSPACE_PATH)["icon"],
		}
		self.assertEqual(sorted(icons - ids), [])


def _frappe_checkout():
	for parent in REPO_ROOT.parents:
		if (parent / "frappe" / ".git").exists():
			return parent / "frappe"
	return None


class TestTile(unittest.TestCase):
	def test_the_tile_is_keyed_by_the_workspace_label(self):
		"""Tiles are keyed by workspace label: ``setup/desktop_icons._create_tile`` makes the Desktop
		Icon only when a Workspace of that name exists, and ``_sync_roles`` copies its roles."""
		self.assertIn(WORKSPACE, TILES)
		slug_, glyph, colour = TILES[WORKSPACE]
		self.assertEqual(slug_, "knowledge_base")
		self.assertEqual(load(WORKSPACE_PATH)["label"], WORKSPACE)
		self.assertEqual([label for label, (_s, g, _c) in TILES.items() if g == glyph], [WORKSPACE])

	def test_the_artwork_exists_in_the_tiles_colour(self):
		slug_, _glyph, colour = TILES[WORKSPACE]
		path = APP / ASSET_SUBDIR / f"{slug_}.svg"
		root = ElementTree.parse(path).getroot()
		self.assertEqual(root.find("{http://www.w3.org/2000/svg}path").get("fill"), colour)
		self.assertEqual(logo_url(slug_), "/assets/erpnext_enhancements/desktop_icons/knowledge_base.svg")

	def test_the_desktop_icon_is_made_on_migrate(self):
		"""No Desktop Icon JSON ships: ``sync_desktop_icons`` creates it on ``after_migrate`` for every
		TILES label with a Workspace behind it, and stamps the artwork (setup/README.md)."""
		self.assertIn("erpnext_enhancements.setup.desktop_icons.sync_desktop_icons", hook("after_migrate"))
		self.assertFalse((APP / "desktop_icon").exists())

	def test_everyone_sees_the_tile(self):
		"""The tile's roles are the workspace's (``_sync_roles``), and those are empty: the tile is
		shown to anyone whose sidebar has an item, which is every staff user."""
		self.assertEqual(load(WORKSPACE_PATH)["roles"], [])


class TestHelpItem(unittest.TestCase):
	def setUp(self):
		self.items = hook("standard_help_items")

	def test_the_item(self):
		(item,) = [i for i in self.items if i["item_label"] == "Company Knowledge Base"]
		self.assertEqual(
			item,
			{
				"item_label": "Company Knowledge Base",
				"item_type": "Route",
				"route": "/desk/knowledge-base",
				"is_standard": 1,
			},
		)

	def test_the_route_is_the_workspaces_slug_under_desk(self):
		"""v16's desk is /desk (``router.js`` ``make_url``); the router opens a workspace by its slug."""
		(item,) = [i for i in self.items if i["item_label"] == "Company Knowledge Base"]
		self.assertEqual(item["route"], "/desk/" + slug(load(WORKSPACE_PATH)["name"]))

	def test_labels_are_unique_and_the_problem_report_stays(self):
		"""``sync_table`` keys the existing rows by label (``navbar_settings.py:55``)."""
		labels = [i["item_label"] for i in self.items]
		self.assertEqual(len(labels), len(set(labels)))
		self.assertEqual(labels, ["Company Knowledge Base", "Report a Problem"])

	def test_no_page_is_named_like_the_workspace(self):
		for path in APP.glob("*/page/*/*.json"):
			with self.subTest(page=path.stem):
				self.assertNotEqual(load(path).get("name"), slug(WORKSPACE))


# ------------------------------------------------------------------ the tile in a saved home-screen layout


class _Site:
	"""The rows ``setup/desktop_icons._sync`` reads and writes on an after_migrate, and nothing else.

	Every TILES label already has its Desktop Icon with its artwork stamped, and no Workspace exists,
	so the tile-making and role steps have nothing to do and only the saved layouts can change."""

	def __init__(self, layouts, icons=None, tables=("Desktop Layout",)):
		self.icons = icons if icons is not None else {label: icon_row(label) for label in TILES}
		self.layouts = dict(layouts)
		self.tables = set(tables)
		self.writes = []
		self.cleared = []
		self.layout_reads = 0

	def exists(self, doctype, name=None):
		return doctype == "Desktop Icon" and name in self.icons

	def table_exists(self, doctype):
		return doctype in self.tables

	def get_value(self, doctype, name, fields, as_dict=False):
		row = self.icons.get(name) if doctype == "Desktop Icon" else None
		if row is None:
			return None
		if isinstance(fields, str):
			return row.get(fields)
		assert as_dict
		return _Dict({field: row.get(field) for field in fields})

	def set_value(self, doctype, name, field, value, update_modified=True):
		self.writes.append((doctype, name, field, update_modified))
		if doctype == "Desktop Layout":
			self.layouts[name] = value
		else:
			self.icons[name][field] = value

	def get_all(self, doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
		if doctype != "Desktop Layout":
			return []
		self.layout_reads += 1
		return [_Dict(name=name, layout=layout) for name, layout in sorted(self.layouts.items())]

	def layout_writes(self):
		return [w for w in self.writes if w[0] == "Desktop Layout"]


def icon_row(label, **overrides):
	"""A Desktop Icon as ``add_workspace_to_desktop`` makes it (``desktop_icon.py:346-351``), stamped."""
	row = {field: None for field in desktop_icons.LAYOUT_FIELDS}
	row.update(
		label=label,
		name=label,
		icon_type="Link",
		link_type="Workspace Sidebar",
		link_to=label,
		idx=0,
		standard=0,
		hidden=0,
		restrict_removal=0,
		bg_color="blue",
		logo_url=logo_url(TILES[label][0]),
	)
	row.update(overrides)
	return row


def saved(*entries):
	"""A saved layout as v16's ``save_layout`` stores it: ``json.dumps`` of the icon list."""
	return json.dumps(
		[
			{"label": label, "idx": idx, "hidden": hidden, "icon_type": "Link"}
			for label, idx, hidden in entries
		]
	)


def drawn(layout):
	"""What v16 draws from a saved layout: ``sync_layout`` uses it verbatim when it is non-empty
	(``desktop.js:225-229``), ``prepare`` drops the hidden ones (:189-201), and the grid sorts by idx
	then label (:686-691)."""
	entries = json.loads(layout)
	shown = [e for e in entries if e.get("hidden") != 1]
	return [e["label"] for e in sorted(shown, key=lambda e: (e["idx"], e["label"]))]


class TestSavedLayouts(unittest.TestCase):
	"""v16 draws the home grid from a person's saved ``Desktop Layout`` whenever they have one, a copy
	of the icon list frozen at their last Edit Layout save, so a tile made later never reaches them.
	Prod had five such layouts when PR 4 was reviewed, a KB Approver's among them. ``_sync`` appends
	the Knowledge Base tile to each on after_migrate (PR 4 review)."""

	def setUp(self):
		frappe = sys.modules["frappe"]
		for name in ("db", "get_all", "cache"):
			self.addCleanup(setattr, frappe, name, getattr(frappe, name, None))

	def run_sync(self, site):
		frappe = sys.modules["frappe"]
		frappe.db = site
		frappe.get_all = site.get_all
		frappe.cache = types.SimpleNamespace(delete_key=site.cleared.append)
		desktop_icons._sync()

	def test_only_tiles_every_staff_user_may_open_are_added(self):
		"""A saved layout is drawn verbatim, past every role and sidebar check of
		``get_desktop_icons``, so only a tile nobody is refused belongs in the list."""
		self.assertEqual(desktop_icons.ADD_TO_SAVED_LAYOUTS, (WORKSPACE,))
		for label in desktop_icons.ADD_TO_SAVED_LAYOUTS:
			with self.subTest(tile=label):
				self.assertIn(label, TILES)
				self.assertEqual(load(WORKSPACE_PATH)["roles"], [])
				self.assertTrue(workspace_open(READER))

	def test_a_layout_saved_before_the_tile_gets_it_after_the_persons_own(self):
		before = saved(("Training", 3, 0), ("Travel", 5, 0), ("Finance Hub", 9, 1))
		site = _Site({"james@example.com": before})
		self.run_sync(site)

		self.assertEqual(site.layout_writes(), [("Desktop Layout", "james@example.com", "layout", False)])
		after = json.loads(site.layouts["james@example.com"])
		self.assertEqual(after[:3], json.loads(before), "the person's own entries are untouched")
		(added,) = after[3:]
		self.assertEqual(added, {**icon_row(WORKSPACE), "idx": 10})
		self.assertEqual(added["logo_url"], "/assets/erpnext_enhancements/desktop_icons/knowledge_base.svg")
		self.assertEqual(drawn(site.layouts["james@example.com"]), ["Training", "Travel", WORKSPACE])
		self.assertEqual(set(site.cleared), {"desktop_icons", "bootinfo"})

	def test_every_saved_layout_is_reached(self):
		site = _Site({"a@example.com": saved(("Training", 0, 0)), "b@example.com": saved(("HR", 2, 0))})
		self.run_sync(site)
		for user in ("a@example.com", "b@example.com"):
			with self.subTest(user=user):
				self.assertEqual(drawn(site.layouts[user])[-1], WORKSPACE)

	def test_a_layout_that_holds_the_tile_is_left_alone_even_hidden(self):
		"""Hiding a tile keeps its entry with ``hidden: 1`` (``desktop.js:1024-1035``), and v16 has no
		other way to take one out, so an entry present is the person's choice."""
		for hidden in (0, 1):
			with self.subTest(hidden=hidden):
				site = _Site({"x@example.com": saved(("Training", 0, 0), (WORKSPACE, 1, hidden))})
				self.run_sync(site)
				self.assertEqual(site.layout_writes(), [])

	def test_an_empty_or_unreadable_layout_is_left_alone(self):
		"""An empty layout makes v16 draw the site's own list, which has the tile already; one this
		cannot read is not this code's to rewrite."""
		site = _Site(
			{"a": "[]", "b": "{}", "c": "not json", "d": None, "e": "", "f": '"text"', "g": "[1, 2]"}
		)
		self.run_sync(site)
		self.assertEqual(site.layout_writes(), [])

	def test_a_second_migrate_writes_nothing(self):
		site = _Site({"x@example.com": saved(("Training", 0, 0))})
		self.run_sync(site)
		self.run_sync(site)
		self.assertEqual(len(site.layout_writes()), 1)

	def test_nothing_is_added_while_the_tile_does_not_exist(self):
		icons = {label: icon_row(label) for label in TILES if label != WORKSPACE}
		site = _Site({"x@example.com": saved(("Training", 0, 0))}, icons=icons)
		self.run_sync(site)
		self.assertEqual(site.layout_writes(), [])

	def test_a_site_without_the_table_is_left_alone(self):
		site = _Site({"x@example.com": saved(("Training", 0, 0))}, tables=())
		self.run_sync(site)
		self.assertEqual(site.layout_reads, 0)
		self.assertEqual(site.layout_writes(), [])

	def test_the_after_migrate_entry_point_runs_it(self):
		"""``_sync`` is what ``sync_desktop_icons`` (after_migrate, after_install) calls, and it
		cannot raise: a layout step that did would take the artwork and roles steps down with it."""
		tree = ast.parse((APP / "setup" / "desktop_icons.py").read_text(encoding="utf-8"))
		(sync,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_sync"]
		self.assertIn("_sync_saved_layouts", {c.func.id for c in _calls(sync, "_sync_saved_layouts")})
		self.assertIn("erpnext_enhancements.setup.desktop_icons.sync_desktop_icons", hook("after_migrate"))

	def test_the_fields_are_what_v16_reads_for_a_tile(self):
		"""A saved layout is a copy of ``get_desktop_icons``' rows, so an appended entry carries the same
		columns (``desktop_icon.py:130-147``). Skipped where there is no frappe checkout (CI included)."""
		checkout = _frappe_checkout()
		if checkout is None:
			self.skipTest("no frappe checkout beside this repo")
		path = "frappe/desk/doctype/desktop_icon/desktop_icon.py"
		result = subprocess.run(
			["git", "-C", str(checkout), "show", f"origin/version-16:{path}"],
			capture_output=True,
			text=True,
			encoding="utf-8",
		)
		if result.returncode:
			self.skipTest(f"{checkout} has no origin/version-16:{path}")
		body = result.stdout.split("def get_desktop_icons(", 1)[1]
		fields = re.search(r"fields = \[(.*?)\]", body, re.S).group(1)
		self.assertEqual(tuple(re.findall(r'"([a-z_]+)"', fields)), desktop_icons.LAYOUT_FIELDS)


# ------------------------------------------------------------------ an emailed Knowledge Base report


class _Refused(Exception):
	pass


class TestEmailedReports(unittest.TestCase):
	"""v16 checks a report's roles against the session user, and an Auto Email Report's scheduled
	send runs as Administrator, who holds every role. So the guard refuses, at save, an Auto Email
	Report on a KB report unless the person saving it and the user it runs as both hold its role
	(PR 4 review). Report Manager may create one, and four Team profiles carry it."""

	REPORTS = {
		INTEGRITY_REPORT: {
			"module": "Knowledge Base",
			"report_type": "Script Report",
			"reference_report": None,
		},
		DUE_REPORT: {"module": "Knowledge Base", "report_type": "Script Report", "reference_report": None},
		"General Ledger": {"module": "Accounts", "report_type": "Script Report", "reference_report": None},
		"My Integrity": {
			"module": "Accounts",
			"report_type": "Custom Report",
			"reference_report": INTEGRITY_REPORT,
		},
		"Loop A": {"module": "Accounts", "report_type": "Custom Report", "reference_report": "Loop B"},
		"Loop B": {"module": "Accounts", "report_type": "Custom Report", "reference_report": "Loop A"},
	}
	ROLES = {
		NIK: {"KB Approver", "KB Author", "System Manager"},
		PARKER: {"KB Author"},
		"reports@example.com": {"Report Manager"},
		"sysman@example.com": {"System Manager", "Report Manager"},
		"Administrator": None,  # every role, as v16's get_roles gives it (permissions.py:546-547)
	}

	def setUp(self):
		frappe = sys.modules["frappe"]
		for name in ("db", "get_all", "get_roles", "session", "throw", "PermissionError"):
			self.addCleanup(setattr, frappe, name, getattr(frappe, name, None))
		self.reads = []
		frappe.db = types.SimpleNamespace(get_value=self._report)
		frappe.get_all = self._has_role
		frappe.get_roles = self._roles
		frappe.PermissionError = type("PermissionError", (Exception,), {})
		frappe.throw = self._throw
		self.frappe = frappe

	def _report(self, doctype, name, fields, as_dict=False):
		self.reads.append((doctype, name))
		row = self.REPORTS.get(name)
		return _Dict({f: row.get(f) for f in fields}) if row else None

	def _has_role(self, doctype, filters=None, pluck=None, **kwargs):
		assert doctype == "Has Role" and filters["parenttype"] == "Report" and pluck == "role"
		return [row["role"] for row in report_json(filters["parent"])["roles"]]

	def _roles(self, user):
		roles = self.ROLES.get(user, set())
		if roles is None:
			roles = {"KB Approver", "KB Author", "System Manager", "Report Manager"}
		return sorted(roles | {"All", "Guest"})

	def _throw(self, message, exc=None, title=None):
		self.assertIs(exc, self.frappe.PermissionError)
		raise _Refused(message)

	def save(self, report, user, by):
		self.frappe.session = types.SimpleNamespace(user=by)
		emailed_reports.guard_auto_email_report(_Dict(report=report, user=user))

	def test_it_is_registered_before_validate(self):
		"""``before_validate`` runs on every save, ``flags.ignore_validate`` or not."""
		self.assertEqual(
			hook("doc_events")["Auto Email Report"],
			{"before_validate": f"{EMAILED_MODULE}.guard_auto_email_report"},
		)

	def test_a_kb_approver_may_email_the_integrity_report_to_themselves(self):
		self.save(INTEGRITY_REPORT, NIK, by=NIK)
		self.save(INTEGRITY_REPORT, NIK, by="Administrator")

	def test_a_report_manager_without_a_kb_role_is_refused(self):
		with self.assertRaises(_Refused) as refused:
			self.save(INTEGRITY_REPORT, "reports@example.com", by="reports@example.com")
		self.assertIn("reports@example.com", str(refused.exception))
		self.assertIn("KB Approver", str(refused.exception))

	def test_naming_an_approver_as_the_user_does_not_get_round_it(self):
		with self.assertRaises(_Refused):
			self.save(INTEGRITY_REPORT, NIK, by="sysman@example.com")

	def test_an_approver_cannot_make_it_run_as_someone_without_the_role(self):
		with self.assertRaises(_Refused):
			self.save(INTEGRITY_REPORT, "reports@example.com", by=NIK)

	def test_a_kb_author_is_refused_the_integrity_report_and_allowed_due_for_review(self):
		with self.assertRaises(_Refused):
			self.save(INTEGRITY_REPORT, PARKER, by=PARKER)
		self.save(DUE_REPORT, PARKER, by=PARKER)
		with self.assertRaises(_Refused):
			self.save(DUE_REPORT, "reports@example.com", by="reports@example.com")

	def test_a_custom_report_built_on_it_is_the_same_report(self):
		"""v16 runs a Custom Report as the report it refers to (``query_report.get_reference_report``)."""
		with self.assertRaises(_Refused):
			self.save("My Integrity", "reports@example.com", by="reports@example.com")
		self.save("My Integrity", NIK, by=NIK)

	def test_any_other_report_is_left_alone_after_one_read(self):
		self.save("General Ledger", "reports@example.com", by="reports@example.com")
		self.assertEqual(self.reads, [("Report", "General Ledger")])
		self.save("", "reports@example.com", by="reports@example.com")
		self.save("No Such Report", "reports@example.com", by="reports@example.com")

	def test_a_custom_report_loop_ends(self):
		self.save("Loop A", "reports@example.com", by="reports@example.com")
		self.assertLessEqual(len(self.reads), emailed_reports.MAX_REFERENCE_DEPTH)


# ------------------------------------------------------------------ the reports: shape and SQL


class TestReportRecords(unittest.TestCase):
	def test_due_for_review(self):
		doc = report_json(DUE_REPORT)
		self.assertEqual(doc["ref_doctype"], ARTICLE)
		self.assertEqual({r["role"] for r in doc["roles"]}, {K.AUTHOR_ROLE, K.APPROVER_ROLE})
		self._common(doc, DUE_REPORT)

	def test_integrity(self):
		doc = report_json(INTEGRITY_REPORT)
		self.assertEqual(doc["ref_doctype"], VERSION)
		self.assertEqual([r["role"] for r in doc["roles"]], [K.APPROVER_ROLE])
		self._common(doc, INTEGRITY_REPORT)

	def _common(self, doc, name):
		self.assertEqual(doc["name"], name)
		self.assertEqual(doc["report_name"], name)
		self.assertEqual(doc["module"], "Knowledge Base")
		self.assertEqual(doc["report_type"], "Script Report")
		self.assertEqual(doc["is_standard"], "Yes")
		self.assertEqual(doc["disabled"], 0)
		# Never a background job whose result is stored as a File, nor one a deploy's FLUSHDB kills.
		self.assertEqual(doc["prepared_report"], 0)
		self.assertEqual(doc["disable_prepared_report_automation"], 1)
		self.assertNotIn("System Manager", {r["role"] for r in doc["roles"]})

	def test_who_can_run_them(self):
		self.assertTrue(report_runnable(DUE_REPORT, AUTHOR))
		self.assertTrue(report_runnable(DUE_REPORT, APPROVER))
		self.assertFalse(report_runnable(DUE_REPORT, READER | {"System Manager"}))
		self.assertTrue(report_runnable(INTEGRITY_REPORT, APPROVER))
		self.assertFalse(report_runnable(INTEGRITY_REPORT, AUTHOR))
		self.assertFalse(report_runnable(INTEGRITY_REPORT, READER | {"System Manager"}))


def _calls(tree, name):
	"""Every call whose function is ``name`` (``sql``) or ends ``.name``."""
	found = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Call):
			func = node.func
			called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
			if called == name:
				found.append(node)
	return found


def _columns(sql):
	match = _SELECT.match(sql)
	return match.group("table"), [c.strip() for c in match.group("columns").split(",")]


class TestReportSQL(unittest.TestCase):
	SOURCES = (
		REPORT_DIR / "knowledge_articles_due_for_review" / "knowledge_articles_due_for_review.py",
		REPORT_DIR / "knowledge_base_integrity" / "knowledge_base_integrity.py",
		KB / "reporting.py",
	)

	def test_every_query_is_a_module_constant_with_bound_parameters(self):
		for path in self.SOURCES[:2]:
			tree = ast.parse(path.read_text(encoding="utf-8"))
			calls = _calls(tree, "sql")
			with self.subTest(path=path.name):
				self.assertTrue(calls)
				for call in calls:
					self.assertIsInstance(call.args[0], ast.Name)
					self.assertTrue(call.args[0].id.endswith("_SQL"))
					if len(call.args) > 1:
						self.assertIsInstance(call.args[1], ast.Dict)

	def test_no_query_is_formatted(self):
		"""Built only by joining a fixed tuple of column names; every value is a bound ``%(name)s``."""
		for module in (due_report, integrity_report):
			names = [name for name in dir(module) if name.endswith("_SQL")]
			self.assertTrue(names)
			for name in names:
				sql = getattr(module, name)
				with self.subTest(query=name):
					self.assertNotIn("{", sql)
					for match in re.finditer("%", sql):
						self.assertRegex(sql[match.start() :], r"^%\([a-z_]+\)s")
			tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
			for node in tree.body:
				if not isinstance(node, ast.Assign):
					continue
				if not any(isinstance(t, ast.Name) and t.id.endswith("_SQL") for t in node.targets):
					continue
				for inner in ast.walk(node.value):
					with self.subTest(module=module.__name__, line=inner.__dict__.get("lineno")):
						self.assertNotIsInstance(inner, ast.JoinedStr)
						self.assertFalse(isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Mod))
						if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
							self.assertEqual(inner.func.attr, "join")

	def test_no_orm_list_call_and_no_function_strings(self):
		"""v16 refuses ``fields=["count(name) as n"]`` in ``get_all`` (CLAUDE.md). Neither report
		uses the ORM's list calls at all, so none can carry one."""
		for path in self.SOURCES:
			tree = ast.parse(path.read_text(encoding="utf-8"))
			for name in ("get_all", "get_list", "get_value", "get_values"):
				with self.subTest(path=path.name, call=name):
					self.assertEqual(_calls(tree, name), [])

	def test_the_due_report_reads_the_published_article_only(self):
		table, columns = _columns(due_report.ARTICLES_SQL)
		self.assertEqual(table, ARTICLE)
		self.assertEqual(tuple(columns), R.DUE_FIELDS)
		self.assertIn("where status = %(status)s", due_report.ARTICLES_SQL)
		self.assertNotIn("Version", Path(due_report.__file__).read_text(encoding="utf-8").split('"""', 2)[2])

	def test_no_draft_text_is_ever_selected(self):
		"""Every version's metadata, and the text only of submitted (approved) versions."""
		text_fields = set(K.VERSION_CONTENT_FIELDS) | {"review_note", "body_md", "source_url"}
		table, columns = _columns(integrity_report.VERSIONS_SQL)
		self.assertEqual(table, VERSION)
		self.assertEqual(set(columns) & text_fields, set())
		self.assertEqual(tuple(columns), R.INTEGRITY_VERSION_FIELDS)

		table, columns = _columns(integrity_report.APPROVED_TEXT_SQL)
		self.assertEqual(table, VERSION)
		self.assertIn(" where docstatus = 1 and name in %(names)s", integrity_report.APPROVED_TEXT_SQL)
		self.assertEqual(tuple(columns), R.APPROVED_TEXT_FIELDS)

		table, columns = _columns(integrity_report.PUBLIC_FILES_SQL)
		self.assertEqual(table, "File")
		self.assertEqual(set(columns), {"name", "attached_to_doctype", "attached_to_name"})

	def test_no_output_column_is_text(self):
		self.assertEqual(
			[c["fieldname"] for c in integrity_report.get_columns()],
			["check", "article", "version", "detail"],
		)
		due = {c["fieldname"] for c in due_report.get_columns()}
		self.assertEqual(due & {"summary", "keywords", "body", "body_md", "change_note"}, set())
		# Every row key is a column, and every column a row key.
		self.assertEqual(
			due,
			{
				"kb_number",
				"title",
				"state",
				"review_by",
				"days_left",
				"process_owner",
				"department_block",
				"review_every_months",
				"last_reviewed_on",
				"last_reviewed_by",
			},
		)

	def test_reporting_imports_no_frappe(self):
		"""In a fresh interpreter, so this suite's own stub cannot hide an import."""
		code = (
			"import sys\n"
			"import erpnext_enhancements.knowledge_base.reporting\n"
			"bad = sorted(m for m in sys.modules if m == 'frappe' or m.startswith('frappe.'))\n"
			"print(','.join(bad))\n"
		)
		result = subprocess.run(
			[sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True
		)
		self.assertEqual(result.stdout.strip(), "")


# ------------------------------------------------------------------ the reports, run


def db_version(name):
	return next(v for v in DB[VERSION] if v["name"] == name)


class _ReportRun(unittest.TestCase):
	def setUp(self):
		DB.clear()
		SQL_CALLS.clear()
		data = corpus()
		DB[ARTICLE] = data["articles"]
		DB[VERSION] = data["versions"]
		DB["File"] = data["files"]


class TestDueReportRuns(_ReportRun):
	def _articles(self, *review_dates):
		DB[ARTICLE] = [
			{
				"name": f"KB-06{index + 1:02d}",
				"title": f"Article {index + 1}",
				"status": "Published",
				"department_block": "06 Operations",
				"process_owner": PARKER if index % 2 else JAMES,
				"review_by": review_by,
				"body": f"<p>{SENTINELS[1]}</p>",
				"summary": SENTINELS[2],
			}
			for index, review_by in enumerate(review_dates)
		] + [
			{
				"name": "KB-0699",
				"title": "Retired",
				"status": "Retired",
				"review_by": datetime.date(2020, 1, 1),
			}
		]

	def test_it_lists_what_is_due_with_the_owner(self):
		self._articles(
			datetime.date(2026, 9, 1), None, datetime.date(2026, 10, 20), datetime.date(2027, 3, 1)
		)
		columns, rows, message = due_report.execute({})
		self.assertEqual([r["kb_number"] for r in rows], ["KB-0602", "KB-0601", "KB-0603"])
		self.assertEqual([r["state"] for r in rows], [R.NO_REVIEW_DATE, R.OVERDUE, R.DUE_SOON])
		self.assertEqual(rows[1]["days_left"], -27)
		self.assertEqual(rows[1]["process_owner"], JAMES)
		self.assertIsNone(message)
		((query, values),) = SQL_CALLS
		self.assertEqual(values, {"status": "Published"})
		self.assertNotIn("KB-0699", [r["kb_number"] for r in rows])
		self.assertNotIn(SENTINELS[1], json.dumps(rows, default=str))

	def test_the_window_and_the_owner_filter(self):
		self._articles(datetime.date(2026, 10, 5), datetime.date(2026, 10, 20))
		_c, rows, _m = due_report.execute({"within_days": 10})
		self.assertEqual([r["kb_number"] for r in rows], ["KB-0601"])
		_c, rows, _m = due_report.execute({"process_owner": PARKER.upper()})
		self.assertEqual([r["kb_number"] for r in rows], ["KB-0602"])

	def test_nothing_due_says_so(self):
		self._articles(datetime.date(2027, 3, 1))
		_c, rows, message = due_report.execute({"within_days": "7"})
		self.assertEqual(rows, [])
		self.assertIn("7 days", message)


class TestIntegrityReportRuns(_ReportRun):
	def test_a_healthy_knowledge_base_returns_no_rows(self):
		columns, rows, message = integrity_report.execute({})
		self.assertEqual(rows, [])
		self.assertIn("No problems found in 1 article(s) and 5 version(s)", message)

	def test_the_queries_it_sends(self):
		integrity_report.execute()
		self.assertEqual(len(SQL_CALLS), 4)
		approved = [values for query, values in SQL_CALLS if "docstatus = 1" in query]
		self.assertEqual(approved, [{"names": ("KBV-00003",)}])
		files = [values for query, values in SQL_CALLS if "`tabFile`" in query]
		self.assertEqual(files, [{"doctypes": (ARTICLE, VERSION)}])

	def test_with_no_articles_it_reads_no_text_at_all(self):
		DB[ARTICLE] = []
		integrity_report.execute()
		self.assertEqual([q for q, _v in SQL_CALLS if "docstatus = 1" in q], [])

	def test_no_row_or_message_carries_text(self):
		db_version("KBV-00003")["docstatus"] = 0  # the live version is now a draft...
		db_version("KBV-00004")["docstatus"] = 2
		DB["File"].append(
			{
				"name": "f-pub",
				"attached_to_doctype": VERSION,
				"attached_to_name": "KBV-00004",
				"is_private": 0,
			}
		)
		DB[ARTICLE][0]["body"] = f"<p>{SENTINELS[1]}</p>"
		_columns_, rows, message = integrity_report.execute({})
		self.assertTrue(rows)
		dumped = json.dumps(rows, default=str) + str(message)
		for sentinel in (*SENTINELS, LIVE_TEXT["title"], "Receive"):
			self.assertNotIn(sentinel, dumped)
		# ...so its text was never selected, not merely never shown.
		self.assertEqual(
			[values for q, values in SQL_CALLS if "docstatus = 1" in q], [{"names": ("KBV-00003",)}]
		)
		self.assertNotIn(C.content_hash(LIVE_TEXT), dumped)

	def test_a_public_file_is_a_row(self):
		DB["File"].append(
			{"name": "f-pub", "attached_to_doctype": ARTICLE, "attached_to_name": "KB-0612", "is_private": 0}
		)
		_c, rows, message = integrity_report.execute({})
		self.assertEqual(
			[(r["check"], r["article"], r["version"]) for r in rows], [(R.CHECK_PUBLIC_FILE, "KB-0612", None)]
		)
		self.assertIn("1 problem(s)", message)


# ------------------------------------------------------------------ reporting.review_due


class TestReviewDue(unittest.TestCase):
	def test_the_states(self):
		for review_by, expected in (
			(datetime.date(2026, 9, 27), (R.OVERDUE, -1)),
			(datetime.date(2026, 9, 28), (R.DUE_SOON, 0)),
			(datetime.date(2026, 10, 28), (R.DUE_SOON, 30)),
			(datetime.date(2026, 10, 29), None),
			(datetime.datetime(2026, 9, 1, 9, 30), (R.OVERDUE, -27)),
			("2026-10-01", (R.DUE_SOON, 3)),
			(None, (R.NO_REVIEW_DATE, None)),
			("", (R.NO_REVIEW_DATE, None)),
			("not a date", (R.NO_REVIEW_DATE, None)),
		):
			with self.subTest(review_by=review_by):
				self.assertEqual(R.review_due(review_by, TODAY), expected)

	def test_the_window(self):
		self.assertEqual(R.review_due(datetime.date(2026, 10, 5), TODAY, 7), (R.DUE_SOON, 7))
		self.assertIsNone(R.review_due(datetime.date(2026, 10, 6), TODAY, 7))
		self.assertIsNone(R.review_due(datetime.date(2026, 9, 29), TODAY, 0))
		self.assertEqual(R.review_due(datetime.date(2026, 9, 27), TODAY, 0), (R.OVERDUE, -1))

	def test_within_days(self):
		for value, expected in ((None, 30), ("", 30), ("abc", 30), (-5, 0), ("7", 7), (14.0, 14), (0, 0)):
			with self.subTest(value=value):
				self.assertEqual(R.within_days(value), expected)

	def test_the_order(self):
		rows = R.due_rows(
			[
				{"name": "KB-0603", "review_by": datetime.date(2026, 10, 1)},
				{"name": "KB-0602", "review_by": datetime.date(2026, 9, 1)},
				{"name": "KB-0604", "review_by": None},
				{"name": "KB-0601", "review_by": datetime.date(2026, 9, 20)},
				{"name": "KB-0605", "review_by": datetime.date(2027, 1, 1)},
			],
			TODAY,
		)
		self.assertEqual([r["kb_number"] for r in rows], ["KB-0604", "KB-0602", "KB-0601", "KB-0603"])


# ------------------------------------------------------------------ reporting.integrity_problems


class TestIntegrity(unittest.TestCase):
	def setUp(self):
		self.data = corpus()
		self.article = self.data["articles"][0]
		self.live = version(self.data, "KBV-00003")

	def assertOnly(self, *expected):
		problems = problems_of(self.data)
		self.assertTrue(problems, "no problem found")
		self.assertEqual(checks(problems), set(expected), problems)
		return problems

	def test_healthy(self):
		self.assertEqual(problems_of(self.data), [])

	def test_the_checks_are_the_readme_contract(self):
		"""The README's Check table is how a KB Approver reads a row: its Check column must be
		``R.CHECKS`` exactly, in order, so renaming, adding or dropping a check fails until the table
		says the same (PR 4 review: this used to assert only that the labels were unique)."""
		self.assertEqual(len(R.CHECKS), len(set(R.CHECKS)))
		self.assertEqual(readme_checks(), list(R.CHECKS))

	def test_the_readme_table_parser_reads_a_changed_table(self):
		"""The parser must see an edit, or the contract test above passes on anything."""
		text = README.read_text(encoding="utf-8").replace("| Approver |", "| Approval |", 1)
		self.assertIn("Approval", readme_checks(text))
		self.assertNotEqual(readme_checks(text), list(R.CHECKS))

	# --- the article

	def test_an_unknown_status(self):
		self.article["status"] = "Draft"
		self.assertOnly(R.CHECK_STATUS)

	def test_a_number_in_another_block(self):
		self.article["department_block"] = "02 Design"
		problems = self.assertOnly(R.CHECK_NUMBER)
		self.assertEqual(len(problems), 2)  # the number's block, and the approved department

	def test_a_block_index_or_a_malformed_number(self):
		for name in ("KB-0600", "KB-612", "kb-0612"):
			with self.subTest(name=name):
				data = corpus()
				data["articles"][0]["name"] = name
				for v in data["versions"]:
					if v["article"] == "KB-0612":
						v["article"] = name
				self.assertEqual(checks(problems_of(data)), {R.CHECK_NUMBER})

	# --- the live version

	def test_no_live_version(self):
		self.article["live_version"] = ""
		problems = self.assertOnly(R.CHECK_LIVE_VERSION)
		details = " ".join(p["detail"] for p in problems)
		self.assertIn("KB-0612 has no live version", details)
		self.assertIn("KBV-00003 is Published", details)

	def test_a_live_version_that_does_not_exist(self):
		self.article["live_version"] = "KBV-09999"
		self.assertOnly(R.CHECK_LIVE_VERSION)

	def test_a_live_version_that_is_not_submitted(self):
		self.live["docstatus"] = 0
		self.assertOnly(R.CHECK_LIVE_VERSION, R.CHECK_VERSION_STATE)

	def test_a_live_version_that_is_not_published(self):
		self.live["review_state"] = "Superseded"
		self.assertOnly(R.CHECK_LIVE_VERSION)

	def test_a_version_number_that_disagrees(self):
		self.article["version_number"] = 3
		self.assertOnly(R.CHECK_LIVE_VERSION)

	def test_a_live_version_of_another_article(self):
		self.live["article"] = "KB-0699"
		self.assertOnly(R.CHECK_LIVE_VERSION, R.CHECK_VERSION_STATE)

	# --- the approved text

	def test_a_tampered_hash(self):
		self.article["content_hash"] = "0" * 64
		(problem,) = self.assertOnly(R.CHECK_APPROVED_TEXT)
		self.assertIn("recorded content hash", problem["detail"])

	def test_an_article_edited_past_the_orm(self):
		self.article["body"] = "<p>Open the PO and skip the check.</p>"
		(problem,) = self.assertOnly(R.CHECK_APPROVED_TEXT)
		self.assertIn("the article changed after it was approved", problem["detail"])

	def test_an_approved_version_edited_past_the_orm(self):
		self.live["body"] = "<p>Something else.</p>"
		problems = self.assertOnly(R.CHECK_APPROVED_TEXT)
		self.assertEqual(len(problems), 2)

	def test_what_nobody_can_see_is_not_an_edit(self):
		self.article["body"] = (
			'<p style="color: red">Open the PO, press <strong>Receive</strong>, and check each line against the slip.</p>'
		)
		self.article["keywords"] = "receiving,  po"
		self.assertEqual(problems_of(self.data), [])

	# --- the approver

	def test_an_approver_with_a_hand_in_it(self):
		for approver, did in (
			(PARKER, "created it, submitted it for review and changed its content"),
			(LISA, "changed its content"),
			(PARKER.upper(), "created it"),
		):
			with self.subTest(approver=approver):
				data = corpus()
				data["articles"][0]["approved_by"] = approver
				version(data, "KBV-00003")["approved_by"] = approver
				problems = problems_of(data)
				self.assertEqual(checks(problems), {R.CHECK_APPROVER})
				self.assertIn(did, problems[0]["detail"])

	def test_the_ai_requester_may_not_approve(self):
		self.live["ai_requested_by"] = JAMES
		(problem,) = self.assertOnly(R.CHECK_APPROVER)
		self.assertIn("asked an AI to draft it", problem["detail"])

	def test_administrator_and_guest_never_approve(self):
		for account in K.NEVER_APPROVERS:
			with self.subTest(account=account):
				data = corpus()
				data["articles"][0]["approved_by"] = account
				version(data, "KBV-00003")["approved_by"] = account
				self.assertEqual(checks(problems_of(data)), {R.CHECK_APPROVER})

	def test_no_approver(self):
		self.article["approved_by"] = None
		self.assertOnly(R.CHECK_APPROVER)

	def test_the_article_and_version_disagree_on_the_approver(self):
		self.live["approved_by"] = NIK
		self.assertOnly(R.CHECK_APPROVER)

	# --- the versions

	def test_two_published_versions(self):
		version(self.data, "KBV-00001")["review_state"] = "Published"
		(problem,) = self.assertOnly(R.CHECK_LIVE_VERSION)
		self.assertEqual(problem["version"], "KBV-00001")

	def test_two_open_versions(self):
		version(self.data, "KBV-00002")["article"] = "KB-0612"
		version(self.data, "KBV-00002")["review_state"] = "In Review"
		(problem,) = self.assertOnly(R.CHECK_OPEN_VERSIONS)
		self.assertIn("KBV-00002, KBV-00004", problem["detail"])

	def test_an_open_version_on_a_retired_article(self):
		self.article["status"] = "Retired"
		(problem,) = self.assertOnly(R.CHECK_OPEN_VERSIONS)
		self.assertEqual(problem["version"], "KBV-00004")

	def test_a_canceled_version_is_never_right_and_never_open(self):
		version(self.data, "KBV-00004")["docstatus"] = 2
		(problem,) = self.assertOnly(R.CHECK_VERSION_STATE)
		self.assertIn("docstatus 2", problem["detail"])
		self.article["status"] = "Retired"  # a canceled draft does not hold the article open
		self.assertOnly(R.CHECK_VERSION_STATE)

	def test_a_state_with_the_wrong_docstatus(self):
		for name, state, docstatus in (
			("KBV-00001", "Superseded", 0),
			("KBV-00004", "Draft", 1),
			("KBV-00002", "Discarded", 1),
		):
			with self.subTest(name=name):
				data = corpus()
				version(data, name).update(review_state=state, docstatus=docstatus)
				self.assertEqual(checks(problems_of(data)), {R.CHECK_VERSION_STATE})

	def test_an_unknown_state(self):
		version(self.data, "KBV-00004")["review_state"] = "Approved"
		self.assertOnly(R.CHECK_VERSION_STATE)

	def test_a_published_version_of_no_article(self):
		version(self.data, "KBV-00002").update(review_state="Superseded", docstatus=1)
		self.assertOnly(R.CHECK_VERSION_STATE)

	def test_a_version_of_an_article_that_does_not_exist(self):
		version(self.data, "KBV-00005")["article"] = "KB-0299"
		(problem,) = self.assertOnly(R.CHECK_VERSION_STATE)
		self.assertEqual(problem["article"], "KB-0299")

	def test_first_drafts_may_be_many(self):
		version(self.data, "KBV-00002")["review_state"] = "Draft"
		self.assertEqual(problems_of(self.data), [])

	# --- files

	def test_a_public_file_names_where_it_is_attached(self):
		self.data["files"] += [
			{"name": "f1", "attached_to_doctype": ARTICLE, "attached_to_name": "KB-0612", "is_private": 0},
			{"name": "f2", "attached_to_doctype": VERSION, "attached_to_name": "KBV-00004", "is_private": 0},
		]
		problems = self.assertOnly(R.CHECK_PUBLIC_FILE)
		self.assertEqual(
			{(p["article"], p["version"]) for p in problems}, {(None, "KBV-00004"), ("KB-0612", None)}
		)

	# --- never text

	def test_no_row_ever_quotes_text(self):
		"""Every mutation above at once, with sentinel text in every draft and the live text known:
		not one word of either appears in a row."""
		self.article.update(status="Retired", version_number=4, content_hash="0" * 64, approved_by=PARKER)
		self.article["body"] = f"<p>{SENTINELS[1]}</p>"
		self.live.update(docstatus=1, ai_requested_by=PARKER)
		version(self.data, "KBV-00001")["review_state"] = "Published"
		version(self.data, "KBV-00002").update(article="KB-0612", review_state="In Review")
		version(self.data, "KBV-00005")["article"] = "KB-0299"
		problems = problems_of(self.data)
		self.assertGreater(len(checks(problems)), 4)
		dumped = json.dumps(problems)
		for text in (*SENTINELS, LIVE_TEXT["title"], LIVE_TEXT["summary"], "packing slip", "Receive"):
			with self.subTest(text=text):
				self.assertNotIn(text, dumped)
		for problem in problems:
			self.assertEqual(set(problem), {"check", "article", "version", "detail"})
			self.assertIn(problem["check"], R.CHECKS)

	def test_the_version_fields_read_are_disjoint_from_its_text(self):
		self.assertEqual(set(R.INTEGRITY_VERSION_FIELDS) & set(K.VERSION_CONTENT_FIELDS), set())
		self.assertEqual(set(R.APPROVED_TEXT_FIELDS), {"name", "department_block", *C.HASHED_FIELDS})


if __name__ == "__main__":
	unittest.main()

"""The status return: a release's ``Refs:`` line moves its Tasks to Pending Review (WI-079 slice 4).

``product_feedback/release_sync.py`` runs hourly. It reads the installed ``CHANGELOG.md``,
takes the release sections above its marker and at or below the version
``tabInstalled Application`` records, and hands every ``TASK-…`` id on a ``Refs:`` line to
``task_writer.mark_shipped``. The acceptance criteria this pins (ADR 0016 §5):

* a referenced feedback Task moves to ``Pending Review`` with a future ``review_date``, and
  never to ``Completed``;
* running the sync twice changes nothing the second time, and a full replay moves nothing
  that already moved (a ``Completed`` Task stays ``Completed``);
* a ``Refs:`` naming a Task without ``custom_enhancement_request`` changes nothing;
* a section above the installed version is never acted on, because its code may not be live;
* the marker moves only after a clean run, an absent marker means "process everything", and a
  run is capped at 500 save attempts, a skip costing nothing, so one Task that never saves
  cannot starve the releases behind it;
* no Refs line in the real ``CHANGELOG.md`` names a Task unless :data:`SHIPPED_REFS` lists it,
  so an example of the convention can never move a real Task.

``frappe`` is a local stub whose savepoint really rolls back, so this suite has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_release_sync -v
"""

import ast
import copy
import json
import re
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

TODAY = "2026-09-24"
rs = None
tw = None
W = None  # the stub's world, reset per test
_saved = {}
_TMP = None
_MODULES = (
	"erpnext_enhancements.product_feedback.release_sync",
	"erpnext_enhancements.product_feedback.task_writer",
	"erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings",
)
_STUBS = ("frappe", "frappe.utils", "frappe.model", "frappe.model.document")


class ValidationError(Exception):
	pass


class DoesNotExistError(Exception):
	pass


class World:
	def __init__(self):
		self.installed_rows = [("1.529.0",)]
		self.sql_calls = []
		self.sql_raises = None
		self.singles = {}
		self.single_reads = []
		self.single_writes = []
		self.commits = 0
		self.errors = []  # (title, message)
		self.tasks = {}  # name -> row
		self.saves = []  # (name, ignore_permissions)
		self.save_fails = {}
		self.comment_fails = set()
		self.comments = []
		self.snapshot = None
		self.savepoint_events = []
		self.changelog = ""
		self.versions = []  # (docname, data), oldest first, as Frappe inserts them
		self.version_queries = []
		self.vanish_on_get = set()  # exists() says yes, get_doc() raises: deleted in between

	def task(self, name, status="Open", request="ER-2026-00012", **extra):
		row = {"status": status, **extra}
		if request is not None:
			row["custom_enhancement_request"] = request
		self.tasks[name] = row
		return row

	def version(self, name, *changes, **extra):
		"""A Version row for Task ``name``: ``changes`` are ``[field, old, new]`` entries."""
		self.versions.append((name, json.dumps({"changed": [list(c) for c in changes], **extra})))


class TaskDoc:
	def __init__(self, name):
		self.name = name
		self.doctype = "Task"
		for key, value in W.tasks[name].items():
			setattr(self, key, value)

	def get(self, key, default=None):
		return getattr(self, key, default)

	def save(self, ignore_permissions=False):
		W.saves.append((self.name, ignore_permissions))
		if self.name in W.save_fails:
			raise W.save_fails[self.name]
		W.tasks[self.name].update(status=self.status, review_date=getattr(self, "review_date", None))

	def add_comment(self, comment_type="Comment", text=None):
		if self.name in W.comment_fails:
			raise RuntimeError("comment insert failed")
		W.comments.append(
			{
				"reference_doctype": "Task",
				"reference_name": self.name,
				"comment_type": comment_type,
				"content": text,
			}
		)


def _install():
	global _TMP
	_TMP = tempfile.TemporaryDirectory()
	package = Path(_TMP.name) / "erpnext_enhancements"
	package.mkdir()

	frappe = types.ModuleType("frappe")
	frappe.ValidationError = ValidationError
	frappe.DoesNotExistError = DoesNotExistError
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe._ = lambda text: text

	def sql(query, values=None, *a, **k):
		W.sql_calls.append((query, values))
		if W.sql_raises:
			raise W.sql_raises
		return list(W.installed_rows)

	def get_single_value(doctype, field, cache=True):
		W.single_reads.append((doctype, field, cache))
		if getattr(W, "single_raises", None):
			raise W.single_raises
		return W.singles.get(field)

	def set_single_value(doctype, field, value, update_modified=True):
		W.single_writes.append((doctype, field, value, update_modified))
		W.singles[field] = value

	def commit():
		W.commits += 1

	def exists(doctype, name):
		return doctype == "Task" and name in W.tasks

	def savepoint(name):
		W.savepoint_events.append(("savepoint", name))
		W.snapshot = (copy.deepcopy(W.tasks), copy.deepcopy(W.comments))

	def release_savepoint(name):
		W.savepoint_events.append(("release", name))
		W.snapshot = None

	def rollback(save_point=None):
		W.savepoint_events.append(("rollback", save_point))
		if save_point and W.snapshot:
			W.tasks, W.comments = W.snapshot
			W.snapshot = None

	frappe.db = types.SimpleNamespace(
		sql=sql,
		get_single_value=get_single_value,
		set_single_value=set_single_value,
		commit=commit,
		exists=exists,
		savepoint=savepoint,
		release_savepoint=release_savepoint,
		rollback=rollback,
	)

	def get_doc(doctype, name=None):
		assert doctype == "Task", doctype
		if name in W.vanish_on_get:
			raise DoesNotExistError(f"Task {name} not found")
		return TaskDoc(name)

	def get_all(doctype, filters=None, pluck=None, limit=None, order_by=None, **k):
		if doctype == "Version":
			W.version_queries.append(
				{"filters": filters, "pluck": pluck, "limit": limit, "order_by": order_by}
			)
			assert order_by == "creation desc", order_by
			rows = [
				{"data": data}
				for name, data in reversed(W.versions)
				if filters == {"ref_doctype": "Task", "docname": name}
			][:limit]
			return [r[pluck] for r in rows] if pluck else rows
		assert doctype == "Comment", doctype
		needle = filters["content"][1].strip("%")
		rows = [
			c
			for c in W.comments
			if c["reference_name"] == filters["reference_name"]
			and c["comment_type"] == filters["comment_type"]
			and needle in c["content"]
		]
		return [c[pluck] for c in rows] if pluck else rows

	frappe.get_doc = get_doc
	frappe.get_all = get_all
	frappe.get_app_path = lambda app: str(package)
	frappe.log_error = lambda title=None, message=None, **k: W.errors.append((title, message))

	utils = types.ModuleType("frappe.utils")
	utils.today = lambda: TODAY
	utils.add_days = lambda value, days: str(date.fromisoformat(str(value)) + timedelta(days=days))
	utils.cint = lambda value: int(value or 0)
	utils.flt = lambda value: float(value or 0)
	utils.escape_html = lambda value: str(value)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	for name, module in zip(_STUBS, (frappe, utils, model, document), strict=True):
		_saved[name] = sys.modules.get(name)
		sys.modules[name] = module


def setUpModule():
	global rs, tw
	_install()
	for name in _MODULES:
		sys.modules.pop(name, None)
	from erpnext_enhancements.product_feedback import release_sync, task_writer

	rs = release_sync
	tw = task_writer


def tearDownModule():
	for name in _MODULES:
		sys.modules.pop(name, None)
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module
	if _TMP is not None:
		_TMP.cleanup()


def write_changelog(text):
	(Path(_TMP.name) / "CHANGELOG.md").write_text(text, encoding="utf-8")


def changelog(*sections):
	"""A CHANGELOG with ``[Unreleased]`` on top and ``(version, body)`` sections, newest first."""
	parts = ["# Changelog", "", "## [Unreleased]", ""]
	for version, body in sections:
		parts += [f"## [{version}] - 2026-09-24", "", body, ""]
	return "\n".join(parts)


class Base(unittest.TestCase):
	def setUp(self):
		global W
		W = World()
		write_changelog("")


# ------------------------------------------------------------------------------ pure parts


class TestVersions(unittest.TestCase):
	def test_a_tuple_of_ints(self):
		self.assertEqual(rs.version_key("1.529.0"), (1, 529, 0))
		self.assertEqual(rs.version_key(" 2.0.10 "), (2, 0, 10))

	def test_compared_as_numbers_not_text(self):
		self.assertLess(rs.version_key("1.99.0"), rs.version_key("1.100.0"))
		self.assertLess(rs.version_key("1.529.9"), rs.version_key("1.529.10"))

	def test_anything_else_is_none(self):
		for value in ("", None, "1.2", "v1.2.3", "1.2.3-beta", "UNVERSIONED", "1.2.3.4", "a.b.c"):
			with self.subTest(value=value):
				self.assertIsNone(rs.version_key(value))


class TestChangelogVersions(unittest.TestCase):
	def test_sections_in_file_order_without_unreleased(self):
		text = changelog(("1.3.0", "three"), ("1.2.0", "two\n\n### Added\n- x"), ("1.1.0", "one"))
		self.assertEqual(
			rs.changelog_versions(text),
			[("1.3.0", "three"), ("1.2.0", "two\n\n### Added\n- x"), ("1.1.0", "one")],
		)

	def test_any_level_two_heading_ends_a_section(self):
		text = "## [1.1.0] - 2026-01-01\nbody\n## Notes\nRefs: TASK-2026-00001\n"
		self.assertEqual(rs.changelog_versions(text), [("1.1.0", "body")])

	def test_the_real_changelog(self):
		text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
		sections = rs.changelog_versions(text)
		self.assertGreater(len(sections), 800)
		self.assertTrue(all(rs.version_key(version) for version, _ in sections))
		init = (APP_DIR / "__init__.py").read_text(encoding="utf-8")
		current = re.search(r'__version__ = "([^"]+)"', init).group(1)
		self.assertEqual(sections[0][0], current, "the newest section is the version being released")

	def test_this_releases_refs_line_names_nothing_to_move(self):
		"""v1.531.0 is the convention's first example. Its Tasks are tracked outside Enhancement
		Requests, so the line must name none: the first run on production replays everything."""
		text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
		body = dict(rs.changelog_versions(text))["1.531.0"]
		self.assertRegex(body, r"(?m)^Refs: ")
		self.assertEqual(rs.refs_in(body), [])


#: Releases whose CHANGELOG section deliberately moves feedback Tasks: version -> the exact
#: normalized Refs lines ``refs_in`` finds in it. Empty until the first release that ships
#: Enhancement Request work. See the test below for why a real Refs line must be listed here.
SHIPPED_REFS: dict[str, list[str]] = {}


class TestTheRealChangelogCannotMoveATaskByAccident(unittest.TestCase):
	"""A CHANGELOG example of the ``Refs:`` convention must never be read as a real Refs line.

	The CHANGELOG documents this convention, and on production every Task id is live: an example
	that ``refs_in`` accepted would move real Tasks to Pending Review within the hour of the
	deploy that shipped it, and nothing would ever move them back. The parser is strict for that
	reason (backticks, bold, four spaces, fences and HTML comments all disqualify a line), and
	this test is the second half of the guard: it runs the parser over the **real** file and fails
	the build on any Refs line that names a Task and is not listed in :data:`SHIPPED_REFS`.

	So when a release really does ship feedback Tasks, its Refs line fails this test until the
	same change adds it to ``SHIPPED_REFS``: one deliberate line in a test beside one line in the
	CHANGELOG. When the failure is an example instead, put the example in a code span or a code
	fence. Only ``TASK-…`` ids move anything, so a line naming only an ``ER-…`` is not checked.
	"""

	def setUp(self):
		self.text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

	def test_no_section_has_a_refs_line_naming_a_task_unless_it_is_a_listed_shipped_release(self):
		found = {}
		for version, body in rs.changelog_versions(self.text):
			lines = [refs["line"] for refs in rs.refs_in(body) if refs["tasks"]]
			if lines:
				found[version] = lines
		self.assertEqual(
			found,
			SHIPPED_REFS,
			"A CHANGELOG Refs line names a Task. If it is an example, put it in a code span or a "
			"fence; if the release really ships those Tasks, add it to SHIPPED_REFS in this file.",
		)

	def test_nor_does_the_file_read_as_one_text(self):
		"""The sync reads section by section; read whole, the fences and comments pair up
		differently, and that must not surface an example either."""
		listed = {line for lines in SHIPPED_REFS.values() for line in lines}
		lines = {refs["line"] for refs in rs.refs_in(self.text) if refs["tasks"]}
		self.assertEqual(lines - listed, set())

	def test_this_releases_own_examples_are_written_so_that_they_do_not_match(self):
		"""The 1.531.0 entry shows the convention with real-looking ids. Control: without the
		code spans they would parse, so it is the spans that keep them inert."""
		body = dict(rs.changelog_versions(self.text))["1.531.0"]
		examples = re.findall(r"`(Refs: [^`]*TASK-[^`]*)`", body)
		self.assertTrue(examples, "the entry documents the convention with an example")
		for example in examples:
			with self.subTest(example=example):
				self.assertTrue(rs.refs_in(example)[0]["tasks"], "bare, the example would move Tasks")


class TestRefsIn(unittest.TestCase):
	def test_the_convention(self):
		refs = rs.refs_in("Some prose.\n\nRefs: ER-2026-00012, TASK-2026-00345, TASK-2026-00346\n")
		self.assertEqual(
			refs,
			[
				{
					"line": "Refs: ER-2026-00012, TASK-2026-00345, TASK-2026-00346",
					"requests": ["ER-2026-00012"],
					"tasks": ["TASK-2026-00345", "TASK-2026-00346"],
				}
			],
		)

	def test_list_markers_and_up_to_three_spaces(self):
		for prefix in ("", "- ", "* ", "+ ", "1. ", "12) ", " ", "  ", "   ", "  - ", "   * ", "-\t"):
			with self.subTest(prefix=prefix):
				refs = rs.refs_in(f"{prefix}Refs: TASK-2026-00345")
				self.assertEqual([r["tasks"] for r in refs], [["TASK-2026-00345"]])

	def test_four_spaces_or_a_tab_is_an_indented_code_block_not_a_refs_line(self):
		for prefix in ("    ", "\t", "    - ", "\t- ", "      "):
			with self.subTest(prefix=prefix):
				self.assertEqual(rs.refs_in(f"text\n\n{prefix}Refs: ER-2026-00012, TASK-2026-00345"), [])

	def test_case_sensitive_and_only_at_the_start_of_a_line(self):
		for line in (
			"refs: TASK-2026-00345",
			"REFS: TASK-2026-00345",
			"Ref: TASK-2026-00345",
			"Refs TASK-2026-00345",
			"The entry carries `Refs: TASK-2026-00345` from now on.",
			"**Refs:** TASK-2026-00345",
			"-Refs: TASK-2026-00345",
			"> Refs: TASK-2026-00345",
		):
			with self.subTest(line=line):
				self.assertEqual(rs.refs_in(line), [])

	def test_a_backticked_or_bold_line_is_ignored(self):
		"""House style puts identifiers in code spans, and the CHANGELOG writes its examples of this
		convention that way. So a line that starts with a backtick or ``**`` is never a Refs line,
		deliberately: a lenient parser would act on the examples."""
		for line in (
			"`Refs: ER-2026-00012, TASK-2026-00345`",
			"- `Refs: ER-2026-00012, TASK-2026-00345`",
			"  `Refs: ER-2026-00012, TASK-2026-00345`. It is documented in the skill.",
			"**Refs: ER-2026-00012, TASK-2026-00345**",
			"- **Refs:** ER-2026-00012, TASK-2026-00345",
			"``Refs: TASK-2026-00345``",
		):
			with self.subTest(line=line):
				self.assertEqual(rs.refs_in(line), [])

	def test_ids_are_exact(self):
		refs = rs.refs_in(
			"Refs: task-2026-00345, TASK-2026-0034, XTASK-2026-00111, TASK-2026-00222-b, ER-2026-458194, "
			"TASK-2026-123456, WI-079"
		)
		self.assertEqual(refs[0]["tasks"], ["TASK-2026-123456"])
		# Production's ER counter already runs to six digits (ER-2026-458194).
		self.assertEqual(refs[0]["requests"], ["ER-2026-458194"])

	def test_a_line_naming_no_id_is_dropped(self):
		self.assertEqual(rs.refs_in("Refs: WI-079"), [])

	def test_fenced_examples_are_not_references(self):
		self.assertEqual(rs.refs_in("```\nRefs: TASK-2026-00345\n```\n"), [])
		self.assertEqual(
			[r["tasks"] for r in rs.refs_in("```md\nRefs: TASK-2026-00001\n```\nRefs: TASK-2026-00002")],
			[["TASK-2026-00002"]],
		)

	def test_tilde_fences_too(self):
		self.assertEqual(rs.refs_in("~~~\nRefs: TASK-2026-00345\n~~~"), [])
		self.assertEqual(
			[r["tasks"] for r in rs.refs_in("~~~text\nRefs: TASK-2026-00001\n~~~\nRefs: TASK-2026-00002")],
			[["TASK-2026-00002"]],
		)

	def test_a_fence_closes_only_on_its_own_character_at_its_own_length_or_more(self):
		def tasks_in(text):
			return [r["tasks"] for r in rs.refs_in(text)]

		# A ``` line inside a ```` block is content: the block runs on to the ```` that closes it,
		# and the real Refs line after that is read, not swallowed by a fence left open.
		self.assertEqual(
			tasks_in("````\n```\nRefs: TASK-2026-00001\n```\n````\nRefs: TASK-2026-00002"),
			[["TASK-2026-00002"]],
		)
		self.assertEqual(tasks_in("````\n```\n````\nRefs: TASK-2026-00346\n"), [["TASK-2026-00346"]])
		# A tilde does not close a backtick fence, nor a backtick a tilde one.
		self.assertEqual(
			tasks_in("```\n~~~\nRefs: TASK-2026-00001\n```\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]]
		)
		self.assertEqual(
			tasks_in("~~~\n```\nRefs: TASK-2026-00001\n~~~\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]]
		)
		# A longer closer closes; a closer with text after it does not.
		self.assertEqual(
			tasks_in("```\nRefs: TASK-2026-00001\n`````\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]]
		)
		self.assertEqual(
			tasks_in("```\n``` not a closer\nRefs: TASK-2026-00001\n```\nRefs: TASK-2026-00002"),
			[["TASK-2026-00002"]],
		)
		# A fence inside a list item is indented with it.
		self.assertEqual(
			tasks_in("- Example:\n\n  ```\n  Refs: TASK-2026-00001\n  ```\nRefs: TASK-2026-00002"),
			[["TASK-2026-00002"]],
		)
		# Backticks in a backtick fence's info string make it inline code, not a fence.
		self.assertEqual(tasks_in("```inline``` code\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]])

	def test_lines_inside_an_html_comment_are_not_references(self):
		def tasks_in(text):
			return [r["tasks"] for r in rs.refs_in(text)]

		self.assertEqual(tasks_in("<!--\nRefs: TASK-2026-00347\n-->"), [])
		self.assertEqual(
			tasks_in("<!-- note\nRefs: TASK-2026-00001\nstill hidden -->\nRefs: TASK-2026-00002"),
			[["TASK-2026-00002"]],
		)
		# A comment that closes on its own line hides nothing after it.
		self.assertEqual(tasks_in("<!-- a note -->\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]])
		# What comes before a comment on the same line is still the line.
		self.assertEqual(
			tasks_in("Refs: TASK-2026-00002 <!-- and not TASK-2026-00001 -->"), [["TASK-2026-00002"]]
		)
		self.assertEqual(tasks_in("<!-- x --> Refs: TASK-2026-00001"), [])
		# `<!--` written inside a code span opens no comment.
		self.assertEqual(tasks_in("Skips `<!--` blocks.\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]])
		# A fence inside a comment is hidden with it, and a comment opener inside a fence is code.
		self.assertEqual(tasks_in("<!--\n```\n-->\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]])
		self.assertEqual(tasks_in("```\n<!--\n```\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]])
		# Indented up to three spaces, `<!--` still opens an HTML block.
		self.assertEqual(
			tasks_in("   <!--\nRefs: TASK-2026-00001\n-->\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]]
		)

	def test_a_comment_opened_mid_line_hides_only_what_it_closes_over(self):
		"""Inline, ``<!--`` is a comment only if ``-->`` follows within its paragraph; otherwise it is
		literal text, and a real Refs line after it must still be read, or its Tasks would silently
		never move."""

		def tasks_in(text):
			return [r["tasks"] for r in rs.refs_in(text)]

		self.assertEqual(
			tasks_in("Text <!-- a\nRefs: TASK-2026-00001\nb --> more\nRefs: TASK-2026-00002"),
			[["TASK-2026-00002"]],
		)
		self.assertEqual(
			tasks_in("Two <!-- a --> and <!-- b\nRefs: TASK-2026-00001\n-->\nRefs: TASK-2026-00002"),
			[["TASK-2026-00002"]],
		)
		# Never closed: literal.
		self.assertEqual(tasks_in("Mentions <!-- in prose.\nRefs: TASK-2026-00002"), [["TASK-2026-00002"]])
		# Closed only after the paragraph ended: literal too.
		self.assertEqual(tasks_in("Text <!-- a\n\nRefs: TASK-2026-00002\n-->"), [["TASK-2026-00002"]])

	def test_several_lines_and_duplicates(self):
		refs = rs.refs_in("Refs: TASK-2026-00001, TASK-2026-00001\n- Refs: ER-2026-00009, TASK-2026-00002")
		self.assertEqual(refs[0]["line"], "Refs: TASK-2026-00001")
		self.assertEqual(refs[1]["line"], "Refs: ER-2026-00009, TASK-2026-00002")


class TestPendingSections(unittest.TestCase):
	SECTIONS = [("1.530.0", "c"), ("1.529.0", "b"), ("1.100.0", "a2"), ("1.99.0", "a1")]

	def test_an_absent_marker_is_everything_up_to_installed_oldest_first(self):
		self.assertEqual(
			[v for v, _ in rs.pending_sections(self.SECTIONS, None, "1.529.0")],
			["1.99.0", "1.100.0", "1.529.0"],
		)

	def test_above_the_marker_and_at_or_below_installed(self):
		self.assertEqual(
			[v for v, _ in rs.pending_sections(self.SECTIONS, "1.99.0", "1.529.0")], ["1.100.0", "1.529.0"]
		)
		self.assertEqual(rs.pending_sections(self.SECTIONS, "1.529.0", "1.529.0"), [])

	def test_an_unreadable_installed_version_is_nothing(self):
		self.assertEqual(rs.pending_sections(self.SECTIONS, None, "UNVERSIONED"), [])

	def test_an_unreadable_marker_is_everything(self):
		self.assertEqual(len(rs.pending_sections(self.SECTIONS, "garbage", "1.529.0")), 3)


# ------------------------------------------------------------------------------ mark_shipped


class TestMarkShipped(Base):
	def test_open_working_overdue_move_to_pending_review_with_review_date_and_comment(self):
		for status in ("Open", "Working", "Overdue"):
			with self.subTest(status=status):
				W.task("TASK-2026-00345", status=status)
				W.comments.clear()
				result = tw.mark_shipped("TASK-2026-00345", "1.529.0", "Refs: ER-2026-00012, TASK-2026-00345")
				self.assertEqual(result, "marked")
				row = W.tasks["TASK-2026-00345"]
				self.assertEqual(row["status"], "Pending Review")
				self.assertEqual(row["review_date"], "2026-10-08")
				self.assertEqual(W.saves[-1], ("TASK-2026-00345", True))
				self.assertEqual(
					W.comments,
					[
						{
							"reference_doctype": "Task",
							"reference_name": "TASK-2026-00345",
							"comment_type": "Comment",
							"content": "Shipped in erpnext_enhancements 1.529.0 (Refs: ER-2026-00012, TASK-2026-00345)",
						}
					],
				)

	def test_review_date_is_fourteen_days_out(self):
		self.assertEqual(tw.REVIEW_DAYS, 14)
		W.task("TASK-2026-00345")
		tw.mark_shipped("TASK-2026-00345", "1.529.0")
		self.assertEqual(
			date.fromisoformat(W.tasks["TASK-2026-00345"]["review_date"]) - date.fromisoformat(TODAY),
			timedelta(days=14),
		)

	def test_every_other_status_is_left_alone(self):
		for status in (
			"Completed",
			"Canceled",
			"Cancelled",
			"Invoiced",
			"Template",
			"Pending Review",
			"",
			"Some Status Added Later",
		):
			with self.subTest(status=status):
				W.saves.clear()
				W.comments.clear()
				W.task("TASK-2026-00345", status=status)
				result = tw.mark_shipped("TASK-2026-00345", "1.529.0", "Refs: TASK-2026-00345")
				self.assertTrue(result.startswith("skipped:"), result)
				self.assertEqual(W.tasks["TASK-2026-00345"]["status"], status)
				self.assertEqual(W.saves, [])
				self.assertEqual(W.comments, [])

	def test_it_never_sets_completed(self):
		self.assertEqual(tw.SHIPPED_STATUS, "Pending Review")
		self.assertEqual(tw.SHIPPABLE_STATUSES, frozenset({"Open", "Working", "Overdue"}))

	def test_only_tasks_the_pipeline_created(self):
		W.task("TASK-2026-00001", request="")
		W.task("TASK-2026-00002", request=None)  # the column is not there at all
		for name in ("TASK-2026-00001", "TASK-2026-00002"):
			with self.subTest(task=name):
				self.assertEqual(tw.mark_shipped(name, "1.529.0"), "skipped:not from an Enhancement Request")
		self.assertEqual(W.saves, [])
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Open")

	def test_a_missing_task_or_empty_input_is_skipped(self):
		self.assertEqual(tw.mark_shipped("TASK-2026-99999", "1.529.0"), "skipped:no such Task")
		self.assertTrue(tw.mark_shipped("", "1.529.0").startswith("skipped:"))
		W.task("TASK-2026-00345")
		self.assertTrue(tw.mark_shipped("TASK-2026-00345", "").startswith("skipped:"))
		self.assertEqual(W.saves, [])

	def test_a_task_deleted_between_the_two_reads_is_skipped_never_failed(self):
		"""A missing Task is permanent. As a failure it would hold the marker behind it forever."""
		W.task("TASK-2026-00345")
		W.vanish_on_get.add("TASK-2026-00345")
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.529.0"), "skipped:no such Task")
		self.assertEqual(W.saves, [])

	def test_a_task_belonging_to_a_request_not_on_the_line_is_skipped(self):
		"""A one-digit slip in a Task id lands on a neighbor, usually another request's."""
		W.task("TASK-2026-02046", request="ER-2026-00099")
		result = tw.mark_shipped(
			"TASK-2026-02046", "1.529.0", "Refs: ER-2026-00012, TASK-2026-02046", ["ER-2026-00012"]
		)
		self.assertEqual(result, "skipped:belongs to ER-2026-00099, not on the Refs line")
		self.assertEqual(W.tasks["TASK-2026-02046"]["status"], "Open")
		self.assertEqual(W.saves, [])

	def test_a_task_of_any_request_on_the_line_moves(self):
		W.task("TASK-2026-02046", request="ER-2026-00099")
		result = tw.mark_shipped("TASK-2026-02046", "1.529.0", "", ["ER-2026-00012", "ER-2026-00099"])
		self.assertEqual(result, "marked")

	def test_a_line_naming_no_request_keeps_the_old_rule(self):
		for requests in (None, [], [""]):
			with self.subTest(requests=requests):
				W.task("TASK-2026-02046", request="ER-2026-00099")
				W.comments.clear()
				self.assertEqual(tw.mark_shipped("TASK-2026-02046", "1.529.0", "", requests), "marked")


class TestOverdueAfterAClosedStatus(Base):
	"""ERPNext v16's overdue job exempts only ``Cancelled`` and ``Completed``, so this site's
	``Canceled`` and ``Invoiced`` Tasks turn ``Overdue`` once their expected end passes. The flip
	is a ``db_set`` and leaves no Version; a person's save does. So the newest Version that changed
	``status`` says what a person last chose, and a closed choice is not reopened as shipped."""

	def test_overdue_after_a_closed_status_is_skipped(self):
		for closed in ("Canceled", "Cancelled", "Invoiced", "Completed", "Template"):
			with self.subTest(closed=closed):
				W.saves.clear()
				W.versions.clear()
				W.task("TASK-2026-00345", status="Overdue")
				W.version("TASK-2026-00345", ["status", "Open", "Working"])
				W.version("TASK-2026-00345", ["status", "Working", closed], ["priority", "Low", "High"])
				W.version("TASK-2026-00345", ["subject", "Old", "New"])  # a later edit, status untouched
				result = tw.mark_shipped("TASK-2026-00345", "1.529.0", "Refs: TASK-2026-00345")
				self.assertEqual(result, f"skipped:overdue after {closed}")
				self.assertEqual(W.tasks["TASK-2026-00345"]["status"], "Overdue")
				self.assertEqual(W.saves, [])

	def test_overdue_after_an_open_status_moves(self):
		W.task("TASK-2026-00345", status="Overdue")
		W.version("TASK-2026-00345", ["status", "Open", "Canceled"])
		W.version("TASK-2026-00345", ["status", "Canceled", "Working"])  # a person reopened it
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.529.0"), "marked")

	def test_overdue_with_no_status_history_moves(self):
		W.task("TASK-2026-00345", status="Overdue")
		W.version("TASK-2026-00345", ["subject", "Old", "New"])
		W.version("TASK-2026-00399", ["status", "Open", "Canceled"])  # another Task's history
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.529.0"), "marked")

	def test_a_version_that_does_not_parse_is_passed_over(self):
		W.task("TASK-2026-00345", status="Overdue")
		W.version("TASK-2026-00345", ["status", "Open", "Canceled"])
		W.versions.append(("TASK-2026-00345", "{not json"))
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.529.0"), "skipped:overdue after Canceled")

	def test_only_overdue_reads_the_history_and_the_query_is_small_and_newest_first(self):
		W.task("TASK-2026-00345", status="Open")
		W.version("TASK-2026-00345", ["status", "Working", "Canceled"])  # not consulted from Open
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.529.0"), "marked")
		self.assertEqual(W.version_queries, [])
		W.task("TASK-2026-00346", status="Overdue")
		tw.mark_shipped("TASK-2026-00346", "1.529.0")
		self.assertEqual(
			W.version_queries,
			[
				{
					"filters": {"ref_doctype": "Task", "docname": "TASK-2026-00346"},
					"pluck": "data",
					"limit": tw.VERSION_LOOKBACK,
					"order_by": "creation desc",
				}
			],
		)
		self.assertLessEqual(tw.VERSION_LOOKBACK, 50)

	def test_a_failed_save_never_raises_and_leaves_the_task_as_it_was(self):
		W.task("TASK-2026-00345", status="Working")
		W.save_fails["TASK-2026-00345"] = ValidationError("Parent task ends before this one")
		result = tw.mark_shipped("TASK-2026-00345", "1.529.0")
		self.assertEqual(result, "failed:ValidationError: Parent task ends before this one")
		self.assertEqual(W.tasks["TASK-2026-00345"]["status"], "Working")
		self.assertIn(("rollback", "ee_mark_shipped"), W.savepoint_events)

	def test_a_failed_comment_rolls_the_status_back_too(self):
		W.task("TASK-2026-00345")
		W.comment_fails.add("TASK-2026-00345")
		result = tw.mark_shipped("TASK-2026-00345", "1.529.0")
		self.assertTrue(result.startswith("failed:RuntimeError"), result)
		self.assertEqual(W.tasks["TASK-2026-00345"]["status"], "Open")
		self.assertNotIn("review_date", W.tasks["TASK-2026-00345"])

	def test_a_task_reopened_after_this_release_is_not_moved_again_by_a_replay(self):
		W.task("TASK-2026-00345")
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.529.0"), "marked")
		W.tasks["TASK-2026-00345"]["status"] = "Working"  # a person reopened it
		result = tw.mark_shipped("TASK-2026-00345", "1.529.0")
		self.assertEqual(result, "skipped:already noted as shipped in 1.529.0")
		self.assertEqual(W.tasks["TASK-2026-00345"]["status"], "Working")
		# A later release that names it again is a new fact, and moves it.
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.530.0"), "marked")
		self.assertEqual(len(W.comments), 2)

	def test_the_version_in_a_note_must_end_where_the_note_does(self):
		W.task("TASK-2026-00345")
		W.comments.append(
			{
				"reference_doctype": "Task",
				"reference_name": "TASK-2026-00345",
				"comment_type": "Comment",
				"content": "Shipped in erpnext_enhancements 1.52.01",
			}
		)
		self.assertEqual(tw.mark_shipped("TASK-2026-00345", "1.52.0"), "marked")

	def test_the_comment_without_a_refs_line(self):
		W.task("TASK-2026-00345")
		tw.mark_shipped("TASK-2026-00345", "1.529.0")
		self.assertEqual(W.comments[0]["content"], "Shipped in erpnext_enhancements 1.529.0")


# ------------------------------------------------------------------------------ the job


class TestTheJob(Base):
	def run_sync(self):
		return rs.sync_shipped_tasks()

	def test_installed_version_is_read_with_bound_params(self):
		W.installed_rows = [("1.530.0",), ("1.529.0",), ("UNVERSIONED",)]
		self.assertEqual(rs.installed_version(), "1.529.0", "two rows: the lower wins")
		query, values = W.sql_calls[0]
		self.assertIn("`tabInstalled Application`", query)
		self.assertEqual(values, ("erpnext_enhancements", "Installed Applications"))
		self.assertEqual(query.count("%"), query.count("%s"), "no literal percent sign")

	def test_a_section_above_the_installed_version_is_never_acted_on(self):
		W.task("TASK-2026-00001")
		W.task("TASK-2026-00002")
		write_changelog(
			changelog(
				("1.530.0", "Refs: TASK-2026-00002"), ("1.529.0", "Refs: ER-2026-00012, TASK-2026-00001")
			)
		)
		out = self.run_sync()
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Pending Review")
		self.assertEqual(W.tasks["TASK-2026-00002"]["status"], "Open")
		self.assertEqual(out["marked"], 1)
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0")

	def test_an_absent_marker_processes_every_release_oldest_first(self):
		for name in ("TASK-2026-00001", "TASK-2026-00002", "TASK-2026-00003"):
			W.task(name)
		write_changelog(
			changelog(
				("1.529.0", "- Refs: TASK-2026-00003"),
				("1.100.0", "Refs: TASK-2026-00002"),
				("1.99.0", "Refs: TASK-2026-00001"),
			)
		)
		out = self.run_sync()
		self.assertEqual(
			[name for name, _ in W.saves], ["TASK-2026-00001", "TASK-2026-00002", "TASK-2026-00003"]
		)
		self.assertEqual(out["marked"], 3)
		self.assertEqual(W.single_reads[0], ("Product Feedback Settings", "release_sync_last_version", False))
		self.assertEqual(
			W.single_writes, [("Product Feedback Settings", "release_sync_last_version", "1.529.0", False)]
		)
		self.assertEqual(W.errors, [])

	def test_the_marker_skips_what_it_already_processed(self):
		W.task("TASK-2026-00001")
		W.task("TASK-2026-00002")
		W.singles["release_sync_last_version"] = "1.528.0"
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00002"), ("1.528.0", "Refs: TASK-2026-00001")))
		self.run_sync()
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Open")
		self.assertEqual(W.tasks["TASK-2026-00002"]["status"], "Pending Review")

	def test_running_twice_changes_nothing_the_second_time(self):
		W.task("TASK-2026-00001")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001")))
		self.run_sync()
		before = (copy.deepcopy(W.tasks), len(W.saves), len(W.comments), len(W.single_writes))
		out = self.run_sync()
		self.assertEqual((W.tasks, len(W.saves), len(W.comments), len(W.single_writes)), before)
		self.assertEqual(out["sections"], 0)

	def test_a_full_replay_moves_nothing_that_already_moved(self):
		W.task("TASK-2026-00001", status="Completed")
		W.task("TASK-2026-00002")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00002"), ("1.200.0", "Refs: TASK-2026-00001")))
		self.run_sync()
		W.singles.pop("release_sync_last_version")  # as if the marker were cleared
		before = (copy.deepcopy(W.tasks), len(W.comments))
		out = self.run_sync()
		self.assertEqual((W.tasks, len(W.comments)), before)
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Completed")
		self.assertEqual((out["marked"], out["skipped"]), (0, 2))

	def test_a_refs_naming_a_task_the_pipeline_did_not_create_changes_nothing(self):
		W.task("TASK-2026-00001", request="")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001")))
		out = self.run_sync()
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Open")
		self.assertEqual(W.saves, [])
		self.assertEqual(out["skipped"], 1)
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0", "a skip is a clean result")

	def test_a_failure_keeps_the_marker_and_writes_one_error_log_without_locals(self):
		W.task("TASK-2026-00001")
		W.task("TASK-2026-00002")
		W.task("TASK-2026-00003")
		W.save_fails["TASK-2026-00001"] = ValidationError("first")
		W.save_fails["TASK-2026-00002"] = ValidationError("second")
		W.singles["release_sync_last_version"] = "1.500.0"
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001, TASK-2026-00002, TASK-2026-00003")))
		out = self.run_sync()
		self.assertEqual(out["status"], "failed")
		self.assertEqual(W.singles["release_sync_last_version"], "1.500.0")
		self.assertEqual(W.single_writes, [])
		self.assertEqual(W.tasks["TASK-2026-00003"]["status"], "Pending Review", "the others still move")
		self.assertEqual(len(W.errors), 1)
		title, message = W.errors[0]
		self.assertIn("2 failure", title)
		self.assertEqual(
			message,
			"TASK-2026-00001 in 1.529.0: ValidationError: first\nTASK-2026-00002 in 1.529.0: ValidationError: second",
		)

	def test_the_next_run_retries_and_then_moves_the_marker(self):
		W.task("TASK-2026-00001")
		W.save_fails["TASK-2026-00001"] = ValidationError("not yet")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001")))
		self.run_sync()
		self.assertNotIn("release_sync_last_version", W.singles)
		W.save_fails.clear()
		self.run_sync()
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Pending Review")
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0")

	def test_a_run_is_capped_at_500_calls_and_the_marker_stops_at_the_last_finished_release(self):
		self.assertEqual(rs.MAX_MARKS_PER_RUN, 500)
		old = [f"TASK-2026-{i:05d}" for i in range(1, 301)]
		new = [f"TASK-2026-{i:05d}" for i in range(301, 601)]
		for name in old + new:
			W.task(name)
		write_changelog(
			changelog(("1.529.0", "Refs: " + ", ".join(new)), ("1.528.0", "Refs: " + ", ".join(old)))
		)
		out = self.run_sync()
		self.assertTrue(out["capped"])
		self.assertEqual(out["marked"], 500)
		self.assertEqual(W.singles["release_sync_last_version"], "1.528.0")
		self.assertEqual(W.tasks[new[199]]["status"], "Pending Review")
		self.assertEqual(W.tasks[new[200]]["status"], "Open")
		out = self.run_sync()
		self.assertFalse(out["capped"])
		self.assertEqual((out["marked"], out["skipped"]), (100, 200))
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0")
		self.assertTrue(all(W.tasks[name]["status"] == "Pending Review" for name in old + new))

	def test_one_release_over_the_cap_finishes_over_two_runs(self):
		"""The second run's replay of the first 500 is all skips, which cost no budget."""
		names = [f"TASK-2026-{i:05d}" for i in range(1, 502)]
		for name in names:
			W.task(name)
		write_changelog(changelog(("1.529.0", "Refs: " + ", ".join(names))))
		out = self.run_sync()
		self.assertTrue(out["capped"])
		self.assertEqual(out["marked"], 500)
		self.assertNotIn("release_sync_last_version", W.singles)
		self.assertEqual(W.errors, [], "being capped with nothing failed is progress, not an error")
		out = self.run_sync()
		self.assertFalse(out["capped"])
		self.assertEqual((out["marked"], out["skipped"]), (1, 500))
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0")

	def test_skips_do_not_count_toward_the_cap(self):
		done = [f"TASK-2026-{i:05d}" for i in range(1, 701)]
		for name in done:
			W.task(name, status="Pending Review")
		W.task("TASK-2026-00999")
		write_changelog(changelog(("1.529.0", "Refs: " + ", ".join([*done, "TASK-2026-00999"]))))
		out = self.run_sync()
		self.assertFalse(out["capped"])
		self.assertEqual((out["marked"], out["skipped"]), (1, 700))
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0")

	def test_one_task_that_never_saves_does_not_starve_the_releases_behind_it(self):
		"""The review's reproduction: a poison Task in the oldest pending release and 600 Task ids
		in the hundred releases after it. Counting skips, every run replayed the same 499 skips
		and stopped at the same place, and 101 Tasks never moved."""
		W.installed_rows = [("1.700.0",)]
		W.task("TASK-2026-09999")
		W.save_fails["TASK-2026-09999"] = ValidationError("Expected End Date should be less than parent")
		sections = [("1.600.0", "Refs: ER-2026-00012, TASK-2026-09999")]
		for i in range(1, 101):
			ids = [f"TASK-2026-{10000 + i * 10 + k:05d}" for k in range(6)]
			for name in ids:
				W.task(name)
			sections.append((f"1.{600 + i}.0", "Refs: ER-2026-00012, " + ", ".join(ids)))
		write_changelog(changelog(*reversed(sections)))

		first = self.run_sync()
		self.assertEqual((first["capped"], first["marked"], first["failed"]), (True, 499, 1))
		self.assertEqual(len(W.errors), 1)
		title, message = W.errors[0]
		self.assertIn("capped", title)
		# 1 failure + 83 releases x 6 = 499 calls; the 500th is the first Task of 1.684.0.
		self.assertEqual(
			message,
			"TASK-2026-09999 in 1.600.0: ValidationError: Expected End Date should be less than parent\n"
			"run capped at 500 calls; releases after 1.683.0 not reached",
		)

		second = self.run_sync()
		self.assertFalse(second["capped"])
		self.assertEqual((second["marked"], second["skipped"], second["failed"]), (101, 499, 1))
		still_open = sorted(name for name, row in W.tasks.items() if row["status"] == "Open")
		self.assertEqual(still_open, ["TASK-2026-09999"], "only the poison Task is left")
		# Its release failed, so the marker cannot pass it, and the hourly Error Log goes on
		# naming it: that is the retry the spec asks for.
		self.assertNotIn("release_sync_last_version", W.singles)
		self.assertNotIn("capped", W.errors[1][0])

	def test_capped_and_failing_moves_the_marker_to_the_last_clean_release(self):
		clean = [f"TASK-2026-{i:05d}" for i in range(1, 301)]
		mixed = [f"TASK-2026-{i:05d}" for i in range(301, 312)]
		later = [f"TASK-2026-{i:05d}" for i in range(401, 801)]
		for name in clean + mixed + later:
			W.task(name)
		W.save_fails["TASK-2026-00301"] = ValidationError("Parent task ends before this one")
		W.singles["release_sync_last_version"] = "1.526.0"
		write_changelog(
			changelog(
				("1.529.0", "Refs: " + ", ".join(later)),
				("1.528.0", "Refs: " + ", ".join(mixed)),
				("1.527.0", "Refs: " + ", ".join(clean)),
			)
		)
		out = self.run_sync()
		self.assertTrue(out["capped"])
		self.assertEqual((out["marked"], out["failed"]), (499, 1))
		self.assertEqual(out["to"], "1.527.0")
		self.assertEqual(W.singles["release_sync_last_version"], "1.527.0")
		self.assertEqual(len(W.errors), 1)
		title, message = W.errors[0]
		self.assertEqual(title, "Release sync: 1 failure(s), installed 1.529.0, capped")
		self.assertEqual(
			message,
			"TASK-2026-00301 in 1.528.0: ValidationError: Parent task ends before this one\n"
			"run capped at 500 calls; releases after 1.528.0 not reached",
		)

	def test_capped_inside_the_first_release_says_so(self):
		names = [f"TASK-2026-{i:05d}" for i in range(1, 502)]
		for name in names:
			W.task(name)
		W.save_fails[names[0]] = ValidationError("no")
		write_changelog(changelog(("1.529.0", "Refs: " + ", ".join(names))))
		self.run_sync()
		self.assertNotIn("release_sync_last_version", W.singles)
		self.assertTrue(
			W.errors[0][1].endswith(
				"run capped at 500 calls inside the oldest pending release; it and the releases after "
				"it not finished"
			),
			W.errors[0][1],
		)

	def test_a_failing_run_moves_the_marker_up_to_the_release_before_the_failure(self):
		W.task("TASK-2026-00001")
		W.task("TASK-2026-00002")
		W.save_fails["TASK-2026-00002"] = ValidationError("no")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00002"), ("1.528.0", "Refs: TASK-2026-00001")))
		out = self.run_sync()
		self.assertEqual(out["status"], "failed")
		self.assertEqual(W.singles["release_sync_last_version"], "1.528.0")
		self.assertNotIn("capped", W.errors[0][0])
		self.assertNotIn("capped", W.errors[0][1])

	def test_a_typo_on_a_refs_line_does_not_pin_the_marker(self):
		W.task("TASK-2026-00001")
		write_changelog(changelog(("1.529.0", "Refs: ER-2026-00012, TASK-2026-00001, TASK-2026-00010")))
		out = self.run_sync()
		self.assertEqual((out["marked"], out["skipped"], out["failed"]), (1, 1, 0))
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0")
		self.assertEqual(W.errors, [])

	def test_the_requests_on_the_line_reach_the_writer(self):
		W.task("TASK-2026-00001", request="ER-2026-00012")
		W.task("TASK-2026-00002", request="ER-2026-00099")
		write_changelog(changelog(("1.529.0", "Refs: ER-2026-00012, TASK-2026-00001, TASK-2026-00002")))
		out = self.run_sync()
		self.assertEqual(W.tasks["TASK-2026-00001"]["status"], "Pending Review")
		self.assertEqual(W.tasks["TASK-2026-00002"]["status"], "Open")
		self.assertEqual((out["marked"], out["skipped"]), (1, 1))
		self.assertEqual(W.singles["release_sync_last_version"], "1.529.0", "a skip is a clean result")

	def test_each_transition_is_committed(self):
		W.task("TASK-2026-00001")
		W.task("TASK-2026-00002", status="Completed")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001, TASK-2026-00002")))
		self.run_sync()
		self.assertEqual(W.commits, 2, "one for the marked Task, one for the marker")

	def test_an_up_to_date_marker_does_not_read_the_changelog(self):
		W.singles["release_sync_last_version"] = "1.529.0"
		(Path(_TMP.name) / "CHANGELOG.md").unlink()
		self.assertEqual(self.run_sync()["status"], "up to date")
		self.assertEqual(W.errors, [])

	def test_the_marker_never_moves_backwards(self):
		W.singles["release_sync_last_version"] = "1.600.0"
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001")))
		self.run_sync()
		self.assertEqual(W.single_writes, [])

	def test_an_unreadable_marker_stops_the_run_and_writes_nothing(self):
		# Stored 1.600.0, but the read fails. Treated as "never run", a replay that failed would
		# write back a lower marker; so the run stops and the next hour tries again.
		W.singles["release_sync_last_version"] = "1.600.0"
		W.single_raises = RuntimeError("lock wait timeout")
		W.task("TASK-2026-00001")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001")))
		out = self.run_sync()
		self.assertEqual(out["status"], "marker unreadable")
		self.assertEqual((W.saves, W.single_writes), ([], []))
		self.assertEqual(W.singles["release_sync_last_version"], "1.600.0")

	def test_the_write_never_goes_below_what_is_stored_now(self):
		# The run started from an older view of the marker; the stored value has moved on since.
		W.singles["release_sync_last_version"] = "1.600.0"
		self.assertIs(rs._write_marker("1.200.0", None), False)
		self.assertIs(rs._write_marker("1.600.0", "1.100.0"), False)
		self.assertEqual(W.single_writes, [])
		self.assertIs(rs._write_marker("1.601.0", "1.600.0"), True)

	def test_nothing_happens_without_an_installed_version(self):
		W.installed_rows = []
		W.task("TASK-2026-00001")
		write_changelog(changelog(("1.529.0", "Refs: TASK-2026-00001")))
		out = self.run_sync()
		self.assertEqual(W.saves, [])
		self.assertEqual(W.single_writes, [])
		self.assertEqual(out["status"], "no installed version")

	def test_it_never_raises_out_of_the_job(self):
		W.sql_raises = RuntimeError("database gone")
		out = self.run_sync()
		self.assertEqual(out, {"status": "error"})
		self.assertEqual(len(W.errors), 1)
		self.assertIn("RuntimeError: database gone", W.errors[0][1])


# ------------------------------------------------------------------------------ wiring


class TestWiring(unittest.TestCase):
	def test_it_runs_hourly(self):
		tree = ast.parse((APP_DIR / "hooks.py").read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				getattr(t, "id", None) == "scheduler_events" for t in node.targets
			):
				hourly = ast.literal_eval(node.value)["hourly"]
				break
		else:
			self.fail("hooks.py has no scheduler_events")
		self.assertIn("erpnext_enhancements.product_feedback.release_sync.sync_shipped_tasks", hourly)

	def test_the_marker_field_has_no_default(self):
		path = APP_DIR / "product_feedback/doctype/product_feedback_settings/product_feedback_settings.json"
		schema = json.loads(path.read_text(encoding="utf-8"))
		field = next(f for f in schema["fields"] if f["fieldname"] == rs.MARKER_FIELD)
		self.assertEqual(field["fieldtype"], "Data")
		self.assertEqual(field.get("read_only"), 1)
		self.assertNotIn("default", field, "absent must mean 'process everything'")
		self.assertIn(rs.MARKER_FIELD, schema["field_order"])
		self.assertGreater(
			schema["modified"], "2026-08-17 12:00:00.000000", "bump modified or it never syncs"
		)


if __name__ == "__main__":
	unittest.main()

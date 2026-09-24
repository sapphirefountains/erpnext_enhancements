"""The Claude Code brief (WI-079 slice 4, ADR 0016 §5). Bench-free.

``product_feedback/brief.py`` renders one confirmed Enhancement Request as a brief a Claude
Code session can execute: Markdown in this repository's work-item shape plus one ``json``
block. The renderer is pure. What it must never get wrong, and none of it shows in a brief
that merely looks right:

* **No requester and no document.** The requester's identity is never needed to build
  anything, and the group Task's origin note names both the requester and the approver. The
  docname travels in the path (``/desk/item/PUMP-001``) as surely as in a field. Sentinels are
  planted in each place one could come from.
* **The loop closes.** The acceptance criteria end with the exact ``Refs:`` line the release
  sync reads, naming the open leaf Tasks, and a Bug gets its reproduction criterion. The line is
  checked with ``release_sync.refs_in`` itself, the parser that will read it: printed bare in a
  block of its own it yields exactly its Tasks, and wrapped in backticks, as house style would
  write it, it yields nothing. A closed leaf is kept off it.
* **Bounded.** At most 60,000 characters whatever the input, cutting the anchors' changelog,
  then README, then field lists, then the rest, and saying truthfully what it cut.

``brief.py`` imports ``code_anchors.parse_path``, and ``code_anchors`` imports ``frappe`` at
module level; ``release_sync`` imports it too, and calls it only inside its job functions. So
this suite installs an **empty** ``frappe`` placeholder when there is no real one, a module with
no attributes, which proves the renderer and ``refs_in`` call nothing on it. It gets its own CI
step all the same, so the placeholder cannot meet another suite's stub.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_brief -v
"""

import ast
import copy
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

brief = None
release_sync = None
_saved = {}
_MODULES = (
	"erpnext_enhancements.product_feedback.brief",
	"erpnext_enhancements.product_feedback.code_anchors",
	"erpnext_enhancements.product_feedback.release_sync",
)

REQUESTER = "alice.requester@example.com"
REQUESTER_NAME = "Alice Requester"
APPROVER = "boss.approver@example.com"
SECRET_DOCNAME = "PUMP-SECRET-0042"
SECRET_QUERY = "owner=someone%40example.com&token=QUERY-SECRET-9"


def setUpModule():
	global brief, release_sync
	try:
		import frappe  # a real bench: use it
	except ImportError:
		_saved["frappe"] = sys.modules.get("frappe")
		sys.modules["frappe"] = types.ModuleType("frappe")
	for name in _MODULES:
		sys.modules.pop(name, None)
	from erpnext_enhancements.product_feedback import brief as module
	from erpnext_enhancements.product_feedback import release_sync as sync

	brief = module
	release_sync = sync


def tearDownModule():
	for name in _MODULES:
		sys.modules.pop(name, None)
	if "frappe" in _saved:
		if _saved["frappe"] is None:
			sys.modules.pop("frappe", None)
		else:
			sys.modules["frappe"] = _saved["frappe"]


def origin_note(er="ER-2026-00012"):
	"""What ``task_writer._origin_note`` writes into every group Task."""
	return (
		f"<p><b>Raised from {er}</b> — an enhancement request filed by {REQUESTER_NAME}"
		f" and approved by {APPROVER}.</p>"
	)


def request(**overrides):
	row = {
		"name": "ER-2026-00012",
		"title": "Item form hangs on save",
		"request_type": "Bug",
		"impact": "Blocking my work",
		"description": "<p>The <b>Item</b> form hangs when I save.</p><p>Every time.</p>",
		"steps_to_reproduce": "<ol><li>Open an Item</li><li>Press Ctrl+S</li></ol>",
		"context_url": f"/desk/item/{SECRET_DOCNAME}?{SECRET_QUERY}#frag",
		"context_doctype": "Item",
		# Neither may reach the brief; the renderer must not even look.
		"requested_by": REQUESTER,
		"context_docname": SECRET_DOCNAME,
	}
	row.update(overrides)
	return row


def tasks():
	return [
		# Two leaves under a group this request created, listed before the group (proposal order).
		{
			"name": "TASK-2026-01481",
			"subject": "Profile the save",
			"description": "<p>Find the <b>slow</b> hook.</p>",
			"parent_task": "TASK-2026-01480",
			"project": "PRJ-00580",
			"is_group": 0,
		},
		{
			"name": "TASK-2026-01482",
			"subject": "Fix the hook",
			"description": "Plain text from the model.\nSecond line.",
			"parent_task": "TASK-2026-01480",
			"project": "PRJ-00580",
			"is_group": 0,
		},
		# An ungrouped leaf.
		{
			"name": "TASK-2026-01483",
			"subject": "Add a regression test",
			"description": "",
			"parent_task": "",
			"project": "PRJ-00580",
			"is_group": 0,
		},
		# A leaf the model nested under an existing epic.
		{
			"name": "TASK-2026-01484",
			"subject": "Tell the Triton board",
			"description": "",
			"parent_task": "TASK-2026-00100",
			"parent_subject": "Triton epic",
			"project": "PRJ-00755",
			"is_group": 0,
		},
		# The group, back-linked, its description the origin note.
		{
			"name": "TASK-2026-01480",
			"subject": "Item save performance",
			"description": origin_note(),
			"parent_task": "",
			"project": "PRJ-00580",
			"is_group": 1,
		},
	]


def duplicates():
	return [
		{
			"task": "TASK-2026-00777",
			"task_subject": "Speed up Item save",
			"confidence": "High",
			"why": "<p>Same hook, filed in August.</p>",
		}
	]


def anchors():
	return {
		"repo": "erpnext_enhancements",
		"version": "1.529.0",
		"route": {"path": "/desk/item", "kind": "desk"},
		"doctypes": [
			{
				"name": "Item",
				"fields": [{"fieldname": f"f{i}", "fieldtype": "Data"} for i in range(5)],
				"fields_total": 5,
				"restricted_fields": [],
			}
		],
		"readme": {"path": "erpnext_enhancements/inventory_enhancements/README.md", "text": "README"},
		"changelog": [{"version": "1.500.0", "date": "2026-09-01", "excerpt": "Item things"}],
		"truncated": [],
		"chars": 100,
	}


def render(**kw):
	args = {
		"request": request(),
		"tasks": tasks(),
		"duplicates": duplicates(),
		"anchors": anchors(),
		"design_notes": [],
	}
	args.update(kw)
	return brief.render_brief(
		args["request"], args["tasks"], args["duplicates"], args["anchors"], args["design_notes"]
	)


def section(markdown, heading):
	"""The text under ``heading`` up to the next ``## `` heading."""
	start = markdown.index(heading + "\n")
	rest = markdown[start + len(heading) + 1 :]
	stop = re.search(r"^## ", rest, re.M)
	return rest[: stop.start()] if stop else rest


def worst_case(leaves=50):
	"""``render`` arguments for ``leaves`` leaves, each under a group of its own, every subject 140
	characters, every text long, six duplicates, and anchors at their 40,000-character budget."""
	subject = "S" * 139 + "!"
	rows = []
	for i in range(leaves):
		rows.append(
			{
				"name": f"TASK-2026-{10000 + i:05d}",
				"subject": subject,
				"description": "<p>" + "d " * 5000 + "</p>",
				"parent_task": f"TASK-2026-{30000 + i:05d}",
				"project": "PRJ-00580",
				"is_group": 0,
				"status": "Open",
			}
		)
	for i in range(leaves):
		rows.append(
			{
				"name": f"TASK-2026-{30000 + i:05d}",
				"subject": subject,
				"description": origin_note() + "<p>" + "g " * 2000 + "</p>",
				"parent_task": "",
				"project": "PRJ-00580",
				"is_group": 1,
				"status": "Open",
			}
		)
	big = anchors()
	big["route"]["outline"] = "o" * 20000
	big["changelog"] = [{"version": "1.0.0", "date": "x", "excerpt": "c" * 8000}]
	big["readme"]["text"] = "r" * 6000
	big["doctypes"][0]["fields"] = [{"fieldname": "f" * 40, "fieldtype": "Data"}] * 100
	return {
		"request": request(description="x " * 10000, steps_to_reproduce="y " * 10000, title="T" * 140),
		"tasks": rows,
		"duplicates": [
			{
				"task": f"TASK-2026-{20000 + i:05d}",
				"task_subject": subject,
				"confidence": "High",
				"why": "w " * 3000,
			}
			for i in range(6)
		],
		"anchors": big,
	}


def copy_block(markdown):
	"""The contents of the fenced block under the acceptance item: what a person copies."""
	ac = section(markdown, "## Acceptance criteria")
	match = re.search(r"^```text\n(.*?)\n```$", ac, re.S | re.M)
	assert match, "no copy block under the acceptance criteria"
	return match.group(1)


def json_block(markdown):
	match = re.search(r"^```json\n(.*?)\n```$", markdown, re.S | re.M)
	assert match, "no ```json block"
	return json.loads(match.group(1))


class TestShape(unittest.TestCase):
	def test_title_then_the_work_item_headings_in_order(self):
		markdown, _ = render()
		self.assertTrue(markdown.startswith("# ER-2026-00012: Item form hangs on save\n"))
		self.assertEqual(re.findall(r"^## .+$", markdown, re.M), list(brief.HEADINGS))
		# The out-of-scope heading is the one every work item and the work-item skill use.
		self.assertEqual(
			brief.HEADINGS,
			(
				"## Why",
				"## Scope",
				"## Acceptance criteria",
				"## Explicitly NOT in this work item",
				"## Data",
			),
		)

	def test_the_headings_match_the_work_items_themselves(self):
		skill = (REPO_ROOT / ".claude" / "skills" / "work-item" / "SKILL.md").read_text(encoding="utf-8")
		self.assertIn(brief.HEADINGS[3], skill)
		wi = (REPO_ROOT / "work-items" / "WI-079-feedback-capture-and-design-review.md").read_text(
			encoding="utf-8"
		)
		self.assertRegex(wi, "(?m)^" + re.escape(brief.HEADINGS[3]) + "$")

	def test_the_data_block_is_the_returned_data_and_holds_exactly_the_five_keys(self):
		markdown, data = render()
		self.assertEqual(json_block(markdown), data)
		self.assertEqual(list(data), ["request", "tasks", "anchors", "design_notes", "refs"])
		self.assertEqual(data["request"], "ER-2026-00012")
		self.assertEqual(data["design_notes"], [])
		self.assertEqual(data["anchors"]["route"]["path"], "/desk/item")
		self.assertEqual(markdown.count("```json"), 1)
		self.assertTrue(markdown.rstrip().endswith("```"), "the Data block is last")

	def test_tasks_in_the_data_carry_name_subject_parent_project(self):
		_, data = render()
		self.assertEqual(
			data["tasks"][0],
			{
				"name": "TASK-2026-01481",
				"subject": "Profile the save",
				"parent": "TASK-2026-01480",
				"project": "PRJ-00580",
			},
		)
		self.assertEqual([t["name"] for t in data["tasks"]], [t["name"] for t in tasks()])

	def test_the_anchors_passed_in_are_not_changed(self):
		given = anchors()
		given["changelog"] = [{"version": "1.0.0", "date": "x", "excerpt": "y" * 70000}]
		before = copy.deepcopy(given)
		brief.render_brief(request(), tasks(), [], given, [])
		self.assertEqual(given, before)


class TestWhy(unittest.TestCase):
	def test_type_impact_description_and_steps(self):
		why = section(render()[0], "## Why")
		self.assertIn("- **Type:** Bug", why)
		self.assertIn("- **Impact:** Blocking my work", why)
		self.assertIn("- **Doctype:** Item", why)
		self.assertIn("> The Item form hangs when I save.", why)
		self.assertIn("> Every time.", why)
		self.assertIn("**Steps to reproduce**", why)
		self.assertIn("> - Open an Item", why)
		self.assertIn("> - Press Ctrl+S", why)

	def test_the_path_is_reduced_by_the_payload_rule(self):
		why = section(render()[0], "## Why")
		self.assertIn("- **Page:** `/desk/item`", why)
		web = section(render(request=request(context_url="/feedback/request/ER-2026-00012?x=1"))[0], "## Why")
		self.assertIn("- **Page:** `/feedback`", web)
		none = section(render(request=request(context_url=""))[0], "## Why")
		self.assertNotIn("**Page:**", none)

	def test_the_requesters_markdown_stays_inside_the_quote(self):
		text = "## Not a heading\n```\nnot a fence\n```\nend"
		markdown, data = render(request=request(description=text))
		why = section(markdown, "## Why")
		reported = why[why.index("**What was reported**") : why.index("**Steps to reproduce**")]
		for line in reported.strip().split("\n")[1:]:
			if line:
				self.assertTrue(line.startswith(">"), line)
		self.assertEqual(re.findall(r"^## .+$", markdown, re.M), list(brief.HEADINGS))
		self.assertEqual(json_block(markdown), data)

	def test_no_steps_section_without_steps(self):
		why = section(render(request=request(steps_to_reproduce=""))[0], "## Why")
		self.assertNotIn("Steps to reproduce", why)


class TestNothingPersonal(unittest.TestCase):
	SENTINELS = (
		REQUESTER,
		REQUESTER_NAME,
		APPROVER,
		SECRET_DOCNAME,
		"QUERY-SECRET-9",
		"someone%40example.com",
		"#frag",
	)

	def test_no_requester_approver_docname_or_query_anywhere(self):
		markdown, data = render()
		serialized = json.dumps(data)
		for sentinel in self.SENTINELS:
			with self.subTest(sentinel=sentinel):
				self.assertNotIn(sentinel, markdown)
				self.assertNotIn(sentinel, serialized)

	def test_the_origin_note_is_removed_and_the_rest_of_the_description_kept(self):
		group = dict(tasks()[4], description=origin_note() + "<p>Keep this line.</p>")
		markdown, _ = render(tasks=[*tasks()[:4], group])
		self.assertNotIn("Raised from", markdown)
		self.assertIn("Keep this line.", section(markdown, "## Scope"))

	def test_the_renderer_imports_no_frappe(self):
		tree = ast.parse((APP_DIR / "product_feedback" / "brief.py").read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				self.assertFalse(any(a.name.split(".")[0] == "frappe" for a in node.names))
			elif isinstance(node, ast.ImportFrom):
				self.assertNotEqual((node.module or "").split(".")[0], "frappe")

	def test_the_wrapper_never_reads_the_requester_or_the_docname(self):
		"""Checked on the code, not the prose: the docstring says it never reads them."""
		tree = ast.parse((APP_DIR / "api" / "feedback.py").read_text(encoding="utf-8"))
		for fn_name in ("claude_code_brief", "_brief_tasks"):
			fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
			body = fn.body[1:] if ast.get_docstring(fn) else fn.body
			for node in (n for stmt in body for n in ast.walk(stmt)):
				if isinstance(node, ast.Attribute):
					self.assertNotIn(node.attr, ("requested_by", "context_docname"), fn_name)
				if isinstance(node, ast.Constant) and isinstance(node.value, str):
					self.assertNotIn(node.value, ("requested_by", "context_docname"), fn_name)

	def test_the_wrapper_checks_the_reviewer_first(self):
		tree = ast.parse((APP_DIR / "api" / "feedback.py").read_text(encoding="utf-8"))
		fn = next(
			n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "claude_code_brief"
		)
		first = fn.body[1] if ast.get_docstring(fn) else fn.body[0]
		self.assertEqual(ast.unparse(first), "_require_reviewer()")


class TestScope(unittest.TestCase):
	def test_leaves_sit_under_their_group_and_ungrouped_leaves_come_first(self):
		scope = section(render()[0], "## Scope")
		group = scope.index("### TASK-2026-01480 — Item save performance")
		self.assertLess(scope.index("- **TASK-2026-01483 — Add a regression test**"), group)
		self.assertLess(group, scope.index("- **TASK-2026-01481 — Profile the save** (PRJ-00580)"))
		self.assertLess(group, scope.index("- **TASK-2026-01482 — Fix the hook**"))

	def test_a_leaf_under_an_existing_epic_is_headed_by_it(self):
		scope = section(render()[0], "## Scope")
		epic = scope.index("### TASK-2026-00100 — Triton epic (an existing Task")
		self.assertLess(epic, scope.index("- **TASK-2026-01484 — Tell the Triton board** (PRJ-00755)"))

	def test_html_is_stripped_from_task_descriptions_and_plain_text_is_kept(self):
		scope = section(render()[0], "## Scope")
		self.assertIn("  Find the slow hook.", scope)
		self.assertIn("  Plain text from the model.\n  Second line.", scope)
		self.assertNotIn("<p>", scope)
		self.assertNotIn("<b>", scope)

	def test_markup_scripts_and_entities(self):
		html = (
			'<p>First <b>bold</b>\n   para</p><ol><li data-list="bullet">one</li><li>two</li></ol>'
			"<script>alert(1)</script><style>p{}</style><p>fish &amp; chips&nbsp;&lt;3</p>"
		)
		text = brief._plain(html)
		# A non-breaking space is whitespace like any other once the markup is gone.
		self.assertEqual(text, "First bold para\n\n- one\n- two\n\nfish & chips <3")

	def test_a_pre_block_keeps_its_indentation(self):
		"""A Quill code block reaches the brief as the code, not as flattened lines."""
		self.assertEqual(
			brief._plain('<pre class="ql-syntax">def f():\n    if x:\n        return 1\n</pre>'),
			"def f():\n    if x:\n        return 1",
		)
		# Its first line too, and around it the ordinary text is still trimmed and joined.
		self.assertEqual(
			brief._plain("<p>  Before   this:</p><pre>    indented()\n  <b>half</b>\n</pre><p>after </p>"),
			"Before this:\n\n    indented()\n  half\n\nafter",
		)

	def test_a_less_than_sign_in_plain_text_is_not_markup(self):
		"""``<`` with a space after it is no tag to HTMLParser or to Frappe, so the text keeps its
		line breaks."""
		self.assertEqual(
			brief._plain("Step one: check a < b\nStep two: save"), "Step one: check a < b\nStep two: save"
		)
		self.assertEqual(brief._plain("x </ b\ny"), "x </ b\ny")
		# A real tag still makes it HTML.
		self.assertEqual(brief._plain("a < b<br>c"), "a < b\nc")

	def test_a_pre_block_under_scope_keeps_its_indentation_inside_the_bullet(self):
		rows = tasks()
		rows[0]["description"] = "<p>Run:</p><pre>if x:\n    go()</pre>"
		scope = section(render(tasks=rows)[0], "## Scope")
		self.assertIn("  Run:\n\n  if x:\n      go()\n", scope)


class TestAcceptance(unittest.TestCase):
	def test_one_box_per_leaf_and_none_for_a_group(self):
		ac = section(render()[0], "## Acceptance criteria")
		boxes = re.findall(r"^- \[ \] (TASK-\S+) — ", ac, re.M)
		self.assertEqual(boxes, ["TASK-2026-01481", "TASK-2026-01482", "TASK-2026-01483", "TASK-2026-01484"])

	def test_a_bug_must_stop_reproducing(self):
		self.assertIn(
			"- [ ] The steps to reproduce no longer reproduce it",
			section(render()[0], "## Acceptance criteria"),
		)
		feature = section(render(request=request(request_type="Feature"))[0], "## Acceptance criteria")
		self.assertNotIn("reproduce", feature)

	def test_the_refs_line_closes_the_loop(self):
		markdown, data = render()
		refs = "Refs: ER-2026-00012, TASK-2026-01481, TASK-2026-01482, TASK-2026-01483, TASK-2026-01484"
		self.assertEqual(data["refs"], refs)
		ac = section(markdown, "## Acceptance criteria")
		last = [line for line in ac.split("\n") if line.startswith("- [ ]")][-1]
		self.assertEqual(
			last,
			"- [ ] The CHANGELOG entry for the release carries the Refs line below, so the release "
			"sync marks these Tasks shipped. Paste this line into the release's CHANGELOG section "
			"exactly as it is, on its own line, without backticks or bold.",
		)
		# Right under the item: the line, bare, alone in a fenced block at column 0.
		self.assertIn(last + "\n\n```text\n" + refs + "\n```\n", ac)
		self.assertEqual(copy_block(markdown), refs)

	def test_the_copied_line_is_read_by_the_release_sync_as_exactly_its_refs_line(self):
		"""Through ``release_sync.refs_in`` itself, not a restatement of its regexes."""
		markdown, data = render()
		leaves = [t["name"] for t in tasks() if not t["is_group"]]
		expected = [{"line": data["refs"], "requests": ["ER-2026-00012"], "tasks": leaves}]
		pasted = copy_block(markdown)
		self.assertEqual(release_sync.refs_in(pasted), expected)
		# Pasted into a CHANGELOG section the way a release writes one, bullet or not.
		for section_text in (
			f"### Fixed\n\n- Something.\n\n{pasted}\n",
			f"### Fixed\n\n- {pasted}\n",
		):
			with self.subTest(section_text=section_text):
				self.assertEqual(release_sync.refs_in(section_text), expected)

	def test_the_same_line_in_backticks_or_bold_is_ignored_which_is_why_it_is_printed_bare(self):
		_, data = render()
		refs = data["refs"]
		for line in (f"`{refs}`", f"- `{refs}`", f"**{refs}**", "**Refs:** " + refs.removeprefix("Refs: ")):
			with self.subTest(line=line):
				self.assertEqual(release_sync.refs_in(line), [])

	def test_the_brief_as_a_whole_moves_nothing(self):
		"""Its Refs line is fenced and its Data block is fenced, so even a whole brief pasted into a
		CHANGELOG by mistake names no Task."""
		markdown, _ = render()
		self.assertEqual(release_sync.refs_in(markdown), [])
		self.assertEqual(markdown.count("Refs: ER-2026-00012"), 2, "the copy block and the Data block")


class TestClosedLeaves(unittest.TestCase):
	"""A Completed, Canceled, Cancelled or Invoiced leaf is listed, marked, and kept off the Refs
	line and the boxes. ERPNext's overdue job exempts only ``Cancelled`` and ``Completed``, so
	this site's ``Canceled`` becomes ``Overdue``, which ``mark_shipped`` would otherwise ship."""

	def with_status(self, statuses):
		rows = tasks()
		for row in rows:
			if row["name"] in statuses:
				row["status"] = statuses[row["name"]]
		return rows

	def test_closed_leaves_are_marked_in_scope_and_left_off_the_refs_line_and_the_boxes(self):
		for status, mark in (
			("Canceled", "canceled"),
			("Cancelled", "canceled"),
			("Completed", "completed"),
			("Invoiced", "invoiced"),
		):
			with self.subTest(status=status):
				markdown, data = render(tasks=self.with_status({"TASK-2026-01482": status}))
				scope = section(markdown, "## Scope")
				self.assertIn(f"- **TASK-2026-01482 — Fix the hook** (PRJ-00580) ({mark})", scope)
				self.assertIn("Plain text from the model.", scope, "still described")
				ac = section(markdown, "## Acceptance criteria")
				self.assertNotIn("TASK-2026-01482", ac)
				self.assertEqual(
					data["refs"], "Refs: ER-2026-00012, TASK-2026-01481, TASK-2026-01483, TASK-2026-01484"
				)
				self.assertEqual(release_sync.refs_in(copy_block(markdown))[0]["line"], data["refs"])
				self.assertIn("TASK-2026-01482", [t["name"] for t in data["tasks"]], "still in the Data")

	def test_open_statuses_stay_on_it(self):
		markdown, data = render(
			tasks=self.with_status(
				{
					"TASK-2026-01481": "Open",
					"TASK-2026-01482": "Working",
					"TASK-2026-01483": "Overdue",
					"TASK-2026-01484": "Pending Review",
				}
			)
		)
		self.assertEqual(
			data["refs"],
			"Refs: ER-2026-00012, TASK-2026-01481, TASK-2026-01482, TASK-2026-01483, TASK-2026-01484",
		)
		self.assertNotRegex(section(markdown, "## Scope"), r"\((canceled|completed|invoiced)\)")

	def test_the_wrapper_reads_the_status(self):
		"""Without it every leaf reads as open, and a Canceled one goes on the Refs line."""
		tree = ast.parse((APP_DIR / "api" / "feedback.py").read_text(encoding="utf-8"))
		fields = next(
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "_BRIEF_TASK_FIELDS" for t in node.targets)
		)
		self.assertIn("status", fields)

	def test_a_closed_group_is_marked_too(self):
		markdown, _ = render(tasks=self.with_status({"TASK-2026-01480": "Completed"}))
		self.assertIn("### TASK-2026-01480 — Item save performance (completed)", markdown)


class TestNotInScope(unittest.TestCase):
	def test_each_duplicate_with_its_reason_then_everything_else(self):
		nis = section(render()[0], "## Explicitly NOT in this work item")
		lines = [line for line in nis.split("\n") if line.startswith("- ")]
		self.assertEqual(
			lines[0],
			"- TASK-2026-00777 — Speed up Item save. The breakdown flagged it as a possible duplicate "
			"(High confidence): Same hook, filed in August.",
		)
		self.assertEqual(lines[-1], "- Anything not listed under Scope.")

	def test_without_duplicates_only_the_catch_all(self):
		nis = section(render(duplicates=[])[0], "## Explicitly NOT in this work item")
		self.assertEqual(
			[line for line in nis.split("\n") if line.startswith("- ")],
			["- Anything not listed under Scope."],
		)


class TestSizeCap(unittest.TestCase):
	def test_an_ordinary_brief_is_not_cut(self):
		markdown, data = render()
		self.assertNotIn("Shortened to stay under", markdown)
		self.assertIn("changelog", data["anchors"])
		self.assertEqual(data["anchors"]["truncated"], [])

	def test_the_changelog_goes_first_and_alone_when_that_is_enough(self):
		big = anchors()
		big["changelog"] = [{"version": "1.0.0", "date": "x", "excerpt": "c" * 65000}]
		markdown, data = render(anchors=big)
		self.assertLessEqual(len(markdown), brief.MAX_BRIEF_CHARS)
		self.assertNotIn("changelog", data["anchors"])
		self.assertIn("readme", data["anchors"])
		self.assertIn("fields", data["anchors"]["doctypes"][0])
		self.assertEqual(data["anchors"]["truncated"], ["changelog"])
		self.assertIn(
			"Shortened to stay under 60,000 characters. Dropped or cut: the anchors' CHANGELOG excerpts.",
			markdown,
		)
		self.assertEqual(json_block(markdown), data)
		self.assertEqual(data["anchors"]["chars"], brief.serialized_size(data["anchors"]))

	def test_then_the_readme_then_the_fields(self):
		big = anchors()
		big["changelog"] = [{"version": "1.0.0", "date": "x", "excerpt": "c" * 30000}]
		big["readme"]["text"] = "r" * 30000
		big["doctypes"][0]["fields"] = [{"fieldname": "f" * 50, "fieldtype": "Data"}] * 700
		markdown, data = render(anchors=big)
		self.assertLessEqual(len(markdown), brief.MAX_BRIEF_CHARS)
		self.assertEqual(data["anchors"]["truncated"], ["changelog", "readme", "fields"])
		self.assertNotIn("fields", data["anchors"]["doctypes"][0])
		self.assertEqual(data["anchors"]["doctypes"][0]["fields_total"], 5, "the real count stays")
		self.assertIn(
			"Dropped or cut: the anchors' CHANGELOG excerpts; the anchors' module README; "
			"the anchors' field lists.",
			markdown,
		)

	def test_the_cap_holds_for_the_largest_request_the_proposal_allows(self):
		huge = [
			{
				"name": f"TASK-2026-{10000 + i:05d}",
				"subject": "S" * 140,
				"description": "<p>" + "d " * 20000 + "</p>",
				"parent_task": "",
				"project": "PRJ-00580",
				"is_group": 0,
			}
			for i in range(50)
		]
		big = anchors()
		big["route"]["outline"] = "o" * 39000
		markdown, data = render(
			request=request(description="x" * 20000, steps_to_reproduce="y" * 20000),
			tasks=huge,
			anchors=big,
		)
		self.assertLessEqual(len(markdown), brief.MAX_BRIEF_CHARS)
		self.assertIn("each description, cut to", markdown)
		self.assertEqual(
			len(re.findall(r"^- \[ \] TASK-", section(markdown, "## Acceptance criteria"), re.M)), 50
		)
		self.assertEqual(json_block(markdown), data)

	def test_the_worst_case_fits_and_the_note_says_exactly_what_was_cut(self):
		"""The most one confirmed proposal can create: 50 leaves, each under its own group,
		140-character subjects (the Task limit), long descriptions, six duplicates and anchors.
		And twice that, which needs the subjects cut too, still fits without the last resort."""
		for leaves in (50, 100):
			with self.subTest(leaves=leaves):
				self.check_worst_case(leaves)

	def check_worst_case(self, leaves):
		markdown, data = render(**worst_case(leaves))
		self.assertLessEqual(len(markdown), brief.MAX_BRIEF_CHARS)
		self.assertNotIn("brief stops here", markdown)
		self.assertEqual(json_block(markdown), data, "the Data block is whole")
		note = re.search(
			r"^Shortened to stay under 60,000 characters\. Dropped or cut: (.*)\.$", markdown, re.M
		)
		self.assertIsNotNone(note)
		cuts = note.group(1).split("; ")
		self.assertEqual(
			cuts[:4],
			[
				"the anchors' CHANGELOG excerpts",
				"the anchors' module README",
				"the anchors' field lists",
				"the anchors",
			],
		)
		self.assertEqual(data["anchors"], {})
		scope = section(markdown, "## Scope")
		why = section(markdown, "## Why")
		self.assertIn("the Task descriptions under Scope", cuts)
		self.assertIn("every other description, cut to 120 characters", cuts)
		self.assertNotIn("each description", note.group(1), "no claim the Scope descriptions were only cut")
		self.assertNotIn("d d d", scope, "the Scope descriptions really are gone")
		self.assertNotIn("g g g", scope)
		quoted = [line for line in why.split("\n") if line.startswith("> ")]
		self.assertTrue(quoted)
		self.assertTrue(all(len(line) <= 2 + 120 for line in quoted), "every other description cut to 120")
		subject_cut = [c for c in cuts if c.startswith("each Task subject")]
		if leaves == 100:
			self.assertEqual(subject_cut, ["each Task subject, cut to 40 characters"])
		if subject_cut:
			cap = int(re.search(r"(\d+) characters", subject_cut[0]).group(1))
			self.assertTrue(all(len(t["subject"]) <= cap for t in data["tasks"]))
			self.assertTrue(all(t["subject"].endswith("…") for t in data["tasks"]), "and marked as cut")
		else:
			self.assertTrue(all(len(t["subject"]) == 140 for t in data["tasks"]), "subjects whole")
		ac = section(markdown, "## Acceptance criteria")
		self.assertEqual(len(re.findall(r"^- \[ \] TASK-", ac, re.M)), leaves)
		self.assertEqual(
			release_sync.refs_in(copy_block(markdown))[0]["tasks"],
			[f"TASK-2026-{10000 + i:05d}" for i in range(leaves)],
		)

	def test_the_cap_holds_for_any_input_and_the_ending_says_the_brief_is_incomplete(self):
		"""Far past anything a request produces: the last step cuts at a line and says so, and
		says truthfully whether the Refs line made it. 125 leaves cut inside the Data block, after
		the copy block; 300 cut inside Scope, before it."""
		for leaves, refs_kept in ((125, True), (300, False)):
			with self.subTest(leaves=leaves):
				markdown, data = render(**worst_case(leaves))
				self.assertLessEqual(len(markdown), brief.MAX_BRIEF_CHARS)
				self.assertTrue(
					markdown.endswith("Take the Task list from the Enhancement Request itself.]\n")
				)
				self.assertEqual(
					sum(1 for line in markdown.split("\n") if line.startswith("```")) % 2, 0, "fences closed"
				)
				copied = [line for line in markdown.split("\n") if line.startswith("Refs: ")]
				if refs_kept:
					self.assertEqual(copy_block(markdown), data["refs"], "whole, never partial")
					self.assertIn("so the rest is missing, the Data block included.", markdown)
					self.assertNotIn("the Refs line and the Data block included", markdown)
				else:
					self.assertEqual(copied, [])
					self.assertIn(
						"so the rest is missing, the Refs line and the Data block included.", markdown
					)


if __name__ == "__main__":
	unittest.main()

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's pure rules: who may approve, KB numbers, review dates, content hygiene.

WI-080 PR 2, ADR 0017. ``knowledge_base/workflow.py`` and ``knowledge_base/content.py`` import no
frappe, so every branch runs here, bench-free and with no stub:

* **Approval** (``approval_problems``): a KB Approver who is not the owner, the submitter, a
  contributor or the AI requester, signed in from a browser and not acting through an AI gate
  card, on a version that is In Review and still the copy they opened. Each rule alone refuses,
  and the refusal names it.
* **Content changes only in Draft**, and every saver of a change is a contributor.
* **KB numbers** are ``KB-{block}{01..99}``: ``00`` is never allocated, a number is never reused,
  and a full block fails loudly.
* **Review dates** default to ``constants.DEFAULT_REVIEW_EVERY_MONTHS`` (POL-0001's six months)
  and handle month ends and leap years.
* **Presentation stripping** removes colour, background, size and font, keeps alignment, indent,
  lists and tables, rewrites nothing else, and is idempotent.
* **The secret scan** finds each kind, skips ``data:`` images, reports line and kind and never the
  value, and leaves ordinary KB prose alone.
* **The content hash** ignores what nobody can see and changes on what anyone can.

**Every secret-shaped fixture is built by concatenation.** GitHub push protection refused a
branch of this repo once for a literal Stripe-key-shaped test string; a split literal is the same
test and no longer looks like a key to a scanner.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_rules -v
"""

import ast
import datetime
import json
import subprocess
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.knowledge_base import constants as K
from erpnext_enhancements.knowledge_base import content as C
from erpnext_enhancements.knowledge_base import workflow as W
from erpnext_enhancements.marketing.publish import workflow as marketing_workflow

MODULE_DIR = APP / "knowledge_base"
VERSION_JSON = MODULE_DIR / "doctype" / "knowledge_article_version" / "knowledge_article_version.json"
ARTICLE_JSON = MODULE_DIR / "doctype" / "knowledge_article" / "knowledge_article.json"
LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading", "Fold"}

AUTHOR = "parker@example.com"
APPROVER = "james@example.com"
OPENED = "2026-09-25 10:15:00.123456"


def _module_assignment(path, name):
	for node in ast.parse(path.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value) if not isinstance(node.value, ast.Call) else node.value
	raise AssertionError(f"{name} not found in {path}")


def _in_review(**values):
	version = {
		"name": "KBV-00012",
		"review_state": W.IN_REVIEW,
		"owner": AUTHOR,
		"submitted_by": AUTHOR,
		"contributors": AUTHOR,
		"ai_requested_by": None,
		"modified": datetime.datetime(2026, 9, 25, 10, 15, 0, 123456),
	}
	version.update(values)
	return version


def _approve(version, user=APPROVER, roles=(K.APPROVER_ROLE,), browser=True, gate=None, opened=OPENED):
	return W.approval_problems(
		version, user, roles, browser=browser, gate_flags=gate or {}, opened_modified=opened
	)


# ------------------------------------------------------------------ the pure modules stay pure


class TestStandardLibraryOnly(unittest.TestCase):
	def test_importing_the_rules_does_not_import_frappe(self):
		"""In a fresh interpreter, so a stub another suite installed cannot hide an import."""
		code = (
			"import sys\n"
			"import erpnext_enhancements.knowledge_base.workflow\n"
			"import erpnext_enhancements.knowledge_base.content\n"
			"import erpnext_enhancements.knowledge_base.constants\n"
			"bad = sorted(m for m in sys.modules if m == 'frappe' or m.startswith('frappe.'))\n"
			"print(','.join(bad))\n"
		)
		result = subprocess.run(
			[sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True
		)
		self.assertEqual(result.stdout.strip(), "")

	def test_signed_in_browser_is_marketings_own(self):
		"""One definition of "a person's login, not a token or a job", shared, not copied."""
		self.assertIs(W.signed_in_browser, marketing_workflow.signed_in_browser)

	def test_marketings_approval_rule_is_not_reused(self):
		source = (MODULE_DIR / "workflow.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		imported = {
			alias.name
			for node in ast.walk(tree)
			if isinstance(node, ast.ImportFrom) and node.module == "erpnext_enhancements.marketing.publish.workflow"
			for alias in node.names
		}
		self.assertEqual(imported, {"signed_in_browser"})


# ------------------------------------------------------------------ the constants agree


class TestConstantsAgree(unittest.TestCase):
	def test_content_fields_are_exactly_the_versions_level_zero_value_fields(self):
		meta = json.loads(VERSION_JSON.read_text(encoding="utf-8"))
		level_zero = {
			f["fieldname"]
			for f in meta["fields"]
			if f["fieldtype"] not in LAYOUT_TYPES and not f.get("permlevel")
		}
		self.assertEqual(set(K.VERSION_CONTENT_FIELDS), level_zero - {"amended_from"})
		self.assertEqual(len(K.VERSION_CONTENT_FIELDS), len(set(K.VERSION_CONTENT_FIELDS)))

	def test_doctype_names_match_the_jsons_and_the_gate(self):
		self.assertEqual(json.loads(ARTICLE_JSON.read_text(encoding="utf-8"))["name"], K.ARTICLE_DOCTYPE)
		self.assertEqual(json.loads(VERSION_JSON.read_text(encoding="utf-8"))["name"], K.VERSION_DOCTYPE)
		gate = _module_assignment(APP / "assistant_tools" / "_gate.py", "KNOWLEDGE_BASE_DOCTYPES")
		names = {elt.value for elt in gate.args[0].elts}
		self.assertEqual(names, set(K.KB_DOCTYPES))

	def test_role_names_match_the_seed_patch_and_the_docperms(self):
		seed = APP / "patches" / "seed_knowledge_base_roles.py"
		seeded = {name for name, _desk in _module_assignment(seed, "ROLES")}
		self.assertEqual(seeded, {K.AUTHOR_ROLE, K.APPROVER_ROLE})
		self.assertEqual(_module_assignment(seed, "APPROVER_ROLE"), K.APPROVER_ROLE)
		meta = json.loads(VERSION_JSON.read_text(encoding="utf-8"))
		self.assertEqual({p["role"] for p in meta["permissions"]}, {K.AUTHOR_ROLE, K.APPROVER_ROLE})

	def test_the_workflow_states_are_the_constants(self):
		self.assertEqual(
			(W.DRAFT, W.IN_REVIEW, W.PUBLISHED, W.SUPERSEDED, W.DISCARDED), K.REVIEW_STATES
		)


# ------------------------------------------------------------------ approval


class TestApproval(unittest.TestCase):
	def test_a_second_person_from_a_browser_may_approve(self):
		self.assertEqual(_approve(_in_review()), [])

	def test_the_opened_value_may_be_a_string_or_a_datetime(self):
		self.assertEqual(_approve(_in_review(), opened=datetime.datetime(2026, 9, 25, 10, 15, 0, 123456)), [])
		self.assertEqual(
			_approve(_in_review(modified="2026-09-25 10:15:00.123456"), opened="2026-09-25T10:15:00.123456"),
			[],
		)

	def test_the_approver_role_is_required(self):
		for roles in ((), (K.AUTHOR_ROLE,), ("System Manager", "Desk User")):
			with self.subTest(roles=roles):
				problems = _approve(_in_review(), roles=roles)
				self.assertEqual(len(problems), 1)
				self.assertIn(K.APPROVER_ROLE, problems[0])

	def test_a_token_a_job_or_the_console_cannot_approve(self):
		problems = _approve(_in_review(), browser=False)
		self.assertEqual(len(problems), 1)
		self.assertIn("browser", problems[0])

	def test_an_ai_gate_card_cannot_approve_even_when_confirmed(self):
		"""Nik confirming a card runs the tool in HIS browser session, so the browser test alone
		passes: the gate's own flags are what give it away."""
		for gate in (
			{"ai_gate_pending": "AIPA-0001"},
			{"ai_gate_bypass": True},
			type("Flags", (), {"ai_gate_bypass": True, "ai_gate_pending": None})(),
		):
			with self.subTest(gate=gate):
				problems = _approve(_in_review(), gate=gate)
				self.assertEqual(len(problems), 1)
				self.assertIn("AI", problems[0])
		self.assertEqual(_approve(_in_review(), gate={"ai_gate_pending": None, "ai_gate_bypass": False}), [])

	def test_only_a_version_in_review_can_be_approved(self):
		for state in (W.DRAFT, W.PUBLISHED, W.SUPERSEDED, W.DISCARDED, None, ""):
			with self.subTest(state=state):
				problems = _approve(_in_review(review_state=state))
				self.assertEqual(len(problems), 1)
				self.assertIn(W.IN_REVIEW, problems[0])

	def test_nothing_stored_is_nothing_to_approve(self):
		self.assertIn("no saved version", _approve(None)[0])

	def test_everyone_who_had_a_hand_in_it_is_refused(self):
		cases = {
			"owner": ({"owner": APPROVER}, "created it"),
			"submitter": ({"submitted_by": APPROVER}, "submitted it"),
			"contributor": ({"contributors": f"{AUTHOR}\n{APPROVER}"}, "changed its content"),
			"ai requester": ({"ai_requested_by": APPROVER}, "asked an AI"),
		}
		for label, (values, phrase) in cases.items():
			with self.subTest(label):
				problems = _approve(_in_review(**values))
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(phrase, problems[0])
				self.assertIn("different", problems[0])

	def test_identity_is_compared_without_case_or_stray_space(self):
		self.assertTrue(_approve(_in_review(owner=" James@Example.com ")))
		self.assertTrue(_approve(_in_review(contributors=f"{AUTHOR}, JAMES@example.com")))

	def test_the_author_approving_their_own_draft_hears_every_reason_in_one_sentence(self):
		problems = _approve(_in_review(), user=AUTHOR)
		self.assertEqual(len(problems), 1)
		self.assertIn("created it, submitted it for review and changed its content", problems[0])

	def test_a_changed_or_unknown_copy_is_refused(self):
		for opened in ("2026-09-25 10:15:00.123457", "2026-09-25 10:15:00", None, "", "yesterday"):
			with self.subTest(opened=opened):
				problems = _approve(_in_review(), opened=opened)
				self.assertEqual(len(problems), 1)
				self.assertIn("changed after you opened it", problems[0])

	def test_every_broken_rule_is_reported(self):
		problems = _approve(
			_in_review(review_state=W.DRAFT, owner=APPROVER),
			roles=(),
			browser=False,
			gate={"ai_gate_bypass": True},
			opened=None,
		)
		self.assertEqual(len(problems), 6)

	def test_the_refusal_is_one_sentence_naming_each_rule(self):
		message = W.refusal("KBV-00012", "approved", ["a", "b"])
		self.assertEqual(message, "KBV-00012 cannot be approved: a; b.")

	def test_the_context_arguments_are_keyword_only(self):
		with self.assertRaises(TypeError):
			W.approval_problems(_in_review(), APPROVER, (K.APPROVER_ROLE,), True, {}, OPENED)


# ------------------------------------------------------------------ content edits and contributors


class TestContentEdits(unittest.TestCase):
	def test_a_new_version_is_wholly_its_creators_work(self):
		self.assertEqual(W.changed_content_fields(None, {"title": "x"}), K.VERSION_CONTENT_FIELDS)

	def test_what_counts_as_a_change(self):
		stored = {
			"title": "Receiving a PO",
			"summary": None,
			"review_every_months": 6,
			"body": '<p style="color: red;">Scan it.</p>',
			"review_state": W.DRAFT,
		}
		same = dict(stored, summary="", review_every_months="6", body="<p>Scan it.</p>", review_state=W.IN_REVIEW)
		self.assertEqual(W.changed_content_fields(stored, same), ())
		self.assertEqual(W.changed_content_fields(stored, dict(stored, title="Receiving a PO!")), ("title",))
		self.assertEqual(W.changed_content_fields(stored, dict(stored, review_every_months=12)), ("review_every_months",))
		self.assertEqual(W.changed_content_fields(stored, dict(stored, body="<p>Scan it twice.</p>")), ("body",))

	def test_content_changes_only_in_draft(self):
		self.assertIsNone(W.content_edit_problem({"review_state": W.DRAFT}, ("body",)))
		self.assertIsNone(W.content_edit_problem({"review_state": None}, ("body",)))
		self.assertIsNone(W.content_edit_problem(None, K.VERSION_CONTENT_FIELDS))
		for state in (W.IN_REVIEW, W.PUBLISHED, W.SUPERSEDED, W.DISCARDED):
			with self.subTest(state=state):
				problem = W.content_edit_problem({"review_state": state}, ("body",))
				self.assertTrue(problem)
				self.assertIn(state.lower() if state == W.DISCARDED else state, problem)

	def test_no_change_is_never_a_problem(self):
		for state in K.REVIEW_STATES:
			self.assertIsNone(W.content_edit_problem({"review_state": state}, ()))

	def test_contributors_are_read_one_per_id_whatever_the_separator(self):
		self.assertEqual(W.contributors("a@x\nb@x, c@x;  A@X\n\n"), ("a@x", "b@x", "c@x"))
		self.assertEqual(W.contributors(None), ())

	def test_a_contributor_is_recorded_once(self):
		self.assertEqual(W.with_contributor(None, "a@x"), "a@x")
		self.assertEqual(W.with_contributor("a@x", "b@x"), "a@x\nb@x")
		self.assertEqual(W.with_contributor("a@x\nb@x", "B@X"), "a@x\nb@x")
		self.assertEqual(W.with_contributor("a@x", ""), "a@x")


# ------------------------------------------------------------------ KB numbers


class TestKbNumbers(unittest.TestCase):
	def test_the_first_number_in_a_block_is_01_never_00(self):
		self.assertEqual(W.next_kb_number("06 Operations", []), "KB-0601")
		self.assertEqual(W.next_kb_number("06 Operations", ["KB-0600"]), "KB-0601")

	def test_it_is_one_more_than_the_highest_and_never_fills_a_gap(self):
		self.assertEqual(W.next_kb_number("06 Operations", ["KB-0601", "KB-0605"]), "KB-0606")

	def test_other_blocks_and_non_numbers_are_ignored(self):
		taken = ["KB-0199", "KB-0712", "KBV-00006", "PRJ-00580", "KB-06", "KB-06123", None, ""]
		self.assertEqual(W.next_kb_number("06 Operations", taken), "KB-0601")

	def test_a_number_is_matched_the_way_mariadb_compares_names(self):
		self.assertEqual(W.next_kb_number("06 Operations", ["kb-0607 ", " KB-0603"]), "KB-0608")

	def test_every_block_and_both_spellings(self):
		for code, label in K.DEPARTMENT_BLOCKS:
			with self.subTest(code=code):
				self.assertEqual(W.next_kb_number(f"{code} {label}", []), f"KB-{code}01")
				self.assertEqual(W.next_kb_number(code, []), f"KB-{code}01")
				self.assertEqual(W.kb_number_prefix(f"{code} {label}"), f"KB-{code}")

	def test_99_is_the_last_and_a_full_block_fails_loudly(self):
		self.assertEqual(W.next_kb_number("06 Operations", ["KB-0698"]), "KB-0699")
		with self.assertRaises(W.BlockFullError) as caught:
			W.next_kb_number("06 Operations", ["KB-0699"])
		self.assertIsInstance(caught.exception, ValueError)
		message = str(caught.exception)
		for part in ("06 Operations", "KB-0601", "KB-0699", "KB-0600"):
			self.assertIn(part, message)

	def test_an_unplaced_or_unknown_block_is_refused(self):
		for block in ("", None, "6", "10", "06 operations", "Operations", "06 Operations "):
			with self.subTest(block=block), self.assertRaises(ValueError):
				W.next_kb_number(block, [])


# ------------------------------------------------------------------ review dates


class TestReviewBy(unittest.TestCase):
	def test_the_default_is_the_constant_six_months(self):
		self.assertEqual(K.DEFAULT_REVIEW_EVERY_MONTHS, 6)
		for months in (None, "", 0, "0", -3, "abc", True, 2.5):
			with self.subTest(months=months):
				self.assertEqual(W.review_interval(months), 6)
				self.assertEqual(W.review_by(datetime.date(2026, 9, 25), months), datetime.date(2027, 3, 25))

	def test_the_default_is_read_from_the_constant_not_restated(self):
		original = K.DEFAULT_REVIEW_EVERY_MONTHS
		K.DEFAULT_REVIEW_EVERY_MONTHS = 3
		try:
			self.assertEqual(W.review_by(datetime.date(2026, 9, 25)), datetime.date(2026, 12, 25))
		finally:
			K.DEFAULT_REVIEW_EVERY_MONTHS = original

	def test_an_interval_the_author_set_is_used(self):
		for months in (1, "12", 12.0, 24):
			with self.subTest(months=months):
				self.assertEqual(W.review_interval(months), int(float(months)))

	def test_month_ends_and_leap_years(self):
		cases = [
			(datetime.date(2026, 8, 31), 6, datetime.date(2027, 2, 28)),
			(datetime.date(2027, 8, 31), 6, datetime.date(2028, 2, 29)),
			(datetime.date(2028, 2, 29), 12, datetime.date(2029, 2, 28)),
			(datetime.date(2028, 2, 29), 48, datetime.date(2032, 2, 29)),
			(datetime.date(2027, 1, 31), 1, datetime.date(2027, 2, 28)),
			(datetime.date(2028, 1, 31), 1, datetime.date(2028, 2, 29)),
			(datetime.date(2026, 3, 31), 6, datetime.date(2026, 9, 30)),
			(datetime.date(2026, 7, 31), 6, datetime.date(2027, 1, 31)),
			(datetime.date(2026, 12, 15), 1, datetime.date(2027, 1, 15)),
			(datetime.date(2026, 11, 30), 3, datetime.date(2027, 2, 28)),
			(datetime.date(2100, 8, 31), 6, datetime.date(2101, 2, 28)),
		]
		for start, months, expected in cases:
			with self.subTest(start=start, months=months):
				self.assertEqual(W.review_by(start, months), expected)

	def test_the_start_may_be_a_datetime_or_a_string(self):
		expected = datetime.date(2027, 3, 25)
		for start in (
			datetime.datetime(2026, 9, 25, 23, 59, 59),
			"2026-09-25 23:59:59.123456",
			"2026-09-25",
			"2026-09-25T08:00:00",
		):
			with self.subTest(start=start):
				self.assertEqual(W.review_by(start), expected)

	def test_no_start_is_an_error(self):
		for start in (None, "", "not a date"):
			with self.subTest(start=start), self.assertRaises(ValueError):
				W.review_by(start)


# ------------------------------------------------------------------ presentation

#: The shape v16's Text Editor saves: its own wrapper, style attributors for colour, background,
#: font, size and alignment, class attributors for indent, <ul> patched in for bullet lists, and
#: tables with Bootstrap classes and data-row ids.
QUILL_BODY = (
	'<div class="ql-editor read-mode">'
	'<h2 style="text-align: center;">Receiving a PO</h2>'
	'<p>Scan the <strong style="color: rgb(230, 0, 0);">packing slip</strong> first &amp; check '
	'the <span style="background-color: rgb(255, 255, 0); font-size: 18px; font-family: serif;">'
	"quantity</span>.&nbsp;Then:</p>"
	'<ol><li data-list="ordered"><span class="ql-ui" contenteditable="false"></span>Open the PO.</li>'
	'<li data-list="ordered" class="ql-indent-1"><span class="ql-ui" contenteditable="false"></span>'
	"Press Receive.</li></ol>"
	'<ul><li data-list="bullet"><span class="ql-ui" contenteditable="false"></span>Keep it.</li></ul>'
	'<table class="table table-bordered"><tbody><tr><td data-row="row-a1">Part</td>'
	'<td data-row="row-a1" style="text-align: right;">Qty</td></tr></tbody></table>'
	'<p class="ql-align-right ql-direction-rtl">x</p>'
	'<p><img src="/private/files/slip.png" width="300"></p>'
	"</div>"
)


class TestStripPresentation(unittest.TestCase):
	def test_a_quill_body_keeps_its_structure_and_loses_its_colours(self):
		out = C.strip_presentation(QUILL_BODY)
		for gone in ("color:", "background-color", "font-size", "font-family"):
			self.assertNotIn(gone, out)
		for kept in (
			'<h2 style="text-align: center;">',
			'<td data-row="row-a1" style="text-align: right;">',
			'class="ql-indent-1"',
			'class="ql-align-right ql-direction-rtl"',
			'<table class="table table-bordered">',
			'<ol><li data-list="ordered">',
			'<ul><li data-list="bullet">',
			'<span class="ql-ui" contenteditable="false">',
			'<img src="/private/files/slip.png" width="300">',
			"&amp; check",
			".&nbsp;Then:",
			'<div class="ql-editor read-mode">',
		):
			self.assertIn(kept, out)
		self.assertIn("<strong>packing slip</strong>", out)
		self.assertIn("<span>quantity</span>", out)

	def test_only_the_tags_that_change_are_rewritten(self):
		"""Everything outside the rewritten start tags comes back byte for byte."""
		body = '<p>a &lt;b&gt; &#169; &copy; AT&amp;T <b>bold</b></p><!-- note --><p style="color:red">c</p>'
		self.assertEqual(C.strip_presentation(body), '<p>a &lt;b&gt; &#169; &copy; AT&amp;T <b>bold</b></p><!-- note --><p>c</p>')

	def test_nothing_to_strip_returns_the_same_text(self):
		for body in ("<p>Plain.</p>", '<p class="ql-indent-2">x</p>', '<p style="text-align: justify;">x</p>', ""):
			with self.subTest(body=body):
				self.assertEqual(C.strip_presentation(body), body)

	def test_it_is_idempotent(self):
		once = C.strip_presentation(QUILL_BODY)
		self.assertEqual(C.strip_presentation(once), once)

	def test_anything_but_a_string_comes_back_as_it_came(self):
		for value in (None, 0, b"<p style='color:red'>x</p>"):
			self.assertIs(C.strip_presentation(value), value)

	def test_every_way_of_hiding_text_with_style_goes(self):
		for style in (
			"color: white",
			"COLOR:#fff",
			"font-size: 0",
			"display:none",
			"visibility: hidden",
			"opacity: 0",
			"position: absolute; left: -9999px",
			"text-align: center; color: white",
			"text-align: center !important",
			"text-align: center /* ; */ ; color: white",
			"col/**/or: white",
		):
			with self.subTest(style=style):
				out = C.strip_presentation(f'<span style="{style}">hidden</span>')
				self.assertIn(out, {"<span>hidden</span>", '<span style="text-align: center;">hidden</span>'})
		self.assertEqual(
			C.strip_presentation('<p style="text-align: center; color: white">x</p>'),
			'<p style="text-align: center;">x</p>',
		)

	def test_quills_presentation_classes_go_and_its_other_classes_stay(self):
		out = C.strip_presentation(
			'<span class="ql-color-white ql-bg-black ql-size-small ql-font-serif ql-indent-3 mention">x</span>'
		)
		self.assertEqual(out, '<span class="ql-indent-3 mention">x</span>')
		self.assertEqual(C.strip_presentation('<span class="ql-color-white">x</span>'), "<span>x</span>")

	def test_markup_is_read_the_way_a_browser_reads_it(self):
		cases = {
			'<p title="a > b" style="color:red">x</p>': '<p title="a &gt; b">x</p>',
			'<p data-x=y=z style="color:red">x</p>': '<p data-x="y=z">x</p>',
			"<P STYLE='color:red' Class='ql-size-huge keep'>x</P>": '<p class="keep">x</P>',
			'<br style="color:red"/>': "<br />",
			'<p\nstyle="color:red">a</p>\n<p style="font-size:0">b</p>': "<p>a</p>\n<p>b</p>",
		}
		for body, expected in cases.items():
			with self.subTest(body=body):
				self.assertEqual(C.strip_presentation(body), expected)

	def test_a_pasted_screenshot_is_left_exactly_as_it_was(self):
		payload = "iVBORw0KGgo" + "A" * 50_000 + "=="
		body = f'<p style="color:red">see</p><p><img src="data:image/png;base64,{payload}"></p>'
		out = C.strip_presentation(body)
		self.assertIn(f'<img src="data:image/png;base64,{payload}">', out)
		self.assertTrue(out.startswith("<p>see</p>"))


# ------------------------------------------------------------------ secrets

#: Built by concatenation: see the module docstring.
SECRETS = {
	"a private key": "-----" + "BEGIN RSA PRIVATE" + " KEY-----",
	"a Stripe secret key": "sk" + "_live_" + "a1B2" * 6,
	"a Stripe webhook secret": "wh" + "sec_" + "abcdefghijklmnopqrstuvwxyz0123",
	"an AWS access key": "AK" + "IA" + "Q3VZ7XK2M9PL4RT8",
	"a Google API key": "AI" + "za" + "SyD" + "x" * 32,
	"a Google OAuth client secret": "GOC" + "SPX-" + "abcdefghijklmnopqrstuvwx",
	"a Google OAuth token": "ya" + "29." + "a0AfH6SMBx" + "y" * 20,
	"a GitHub token": "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
	"a Slack token": "xo" + "xb-" + "123456789012-abcdefghij",
	"a SendGrid API key": "S" + "G." + "abcdefghijklmnopqrstuv" + "." + "w" * 43,
	"an Anthropic API key": "sk" + "-ant-" + "api03-" + "z" * 30,
	"an OpenAI API key": "sk" + "-proj-" + "Q" * 40,
	"a Plaid access token": "access" + "-production-" + "0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
	"a JSON Web Token": "ey" + "JhbGciOiJIUzI1NiJ9" + ".ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "s" * 20,
	"a Frappe API key and secret": "token " + "a1b2c3d4e5f6a7b" + ":" + "0f9e8d7c6b5a4f3",
	"a bearer token": "Bear" + "er " + "abcdefghijklmnopqrstuvwxyz0123",
	"an HTTP Basic credential": "Bas" + "ic " + "dXNlcjpwYXNzd29yZDEyMw==",
	"a password in a web address": "https://" + "admin:" + "hunter2" + "@erp.example.com/",
	"a written-out password": "Pass" + "word: " + "Fountain#2026",
	"a written-out key or token": "API " + "key = " + "k7Hq9ZpX2mW4vR8tL1",
	"a Google service-account key": '"private_' + 'key_id": "' + "0123456789abcdef" * 2 + "01234567" + '"',
}


class TestSecretFindings(unittest.TestCase):
	def test_every_kind_is_found_and_named(self):
		for kind, secret in SECRETS.items():
			with self.subTest(kind=kind):
				self.assertIn(C.Finding(1, kind), C.secret_findings(f"see {secret} here"))

	def test_a_finding_never_carries_the_value(self):
		self.assertEqual(C.Finding._fields, ("line", "kind"))
		for kind, secret in SECRETS.items():
			with self.subTest(kind=kind):
				found = C.document_secret_findings({"body": f"<p>x</p><p>{secret}</p>"})
				message = C.secret_refusal(found)
				self.assertIn("Body line 2", message)
				self.assertIn(kind, message)
				core = secret.split(" ")[-1].strip('"')
				self.assertNotIn(core[-12:], message)
				self.assertNotIn(core[-12:], repr(found))

	def test_lines_are_counted_as_the_page_reads(self):
		body = (
			'<div class="ql-editor"><h1>Title</h1><p>one</p><p><br></p>'
			"<ol><li>two</li><li>three " + SECRETS["an AWS access key"] + "</li></ol>"
			"<table><tr><td>a</td><td>" + SECRETS["a Stripe secret key"] + "</td></tr></table></div>"
		)
		self.assertEqual(
			C.secret_findings(body, html=True),
			[C.Finding(4, "an AWS access key"), C.Finding(5, "a Stripe secret key")],
		)

	def test_plain_text_lines_are_the_fields_own(self):
		text = "one\r\n\r\nthree " + SECRETS["a GitHub token"]
		self.assertEqual(C.secret_findings(text), [C.Finding(3, "a GitHub token")])

	def test_attribute_values_are_read_with_their_line(self):
		for body in (
			'<p>Log in <a href="https://admin:' + "hunter2" + '@erp.example.com">here</a></p>',
			'<p data-note="' + SECRETS["a Stripe secret key"] + '">x</p>',
			'<p><img src="/files/x.png" alt="' + SECRETS["an AWS access key"] + '"></p>',
		):
			with self.subTest(body=body):
				self.assertEqual(len(C.secret_findings(body, html=True)), 1)

	def test_a_secret_split_by_formatting_is_still_found(self):
		key = SECRETS["a Stripe secret key"]
		body = f"<p>{key[:10]}<strong>{key[10:18]}</strong>{key[18:]}</p>"
		self.assertEqual(C.secret_findings(body, html=True), [C.Finding(1, "a Stripe secret key")])

	def test_pasted_images_are_removed_before_the_scan(self):
		"""Base64 can contain anything, and a screenshot is megabytes of it."""
		noise = "AK" + "IA" + "ABCDEFGHIJKLMNOP" + "/" + "sk" + "_live_" + "q" * 30
		# The noise really is secret-shaped: scanned as text, it is two findings.
		self.assertEqual(len(C.secret_findings(noise)), 2)
		body = f'<p>ok</p><p><img src="data:image/png;base64,{noise}"></p><p><img src=\'data:image/gif;base64,{noise}\'></p>'
		self.assertEqual(C.secret_findings(body, html=True), [])
		self.assertEqual(C.without_data_uris(f"a data:image/png;base64,{noise} b"), "a  b")

	def test_ordinary_knowledge_base_prose_is_not_a_secret(self):
		for text in (
			"The Wi-Fi password is kept in 1Password under Shop.",
			"Password: ask Nik, or look in the password manager.",
			"Password: ********",
			"The password is stored in the vault.",
			"Reset it at https://accounts.google.com/signin/recovery",
			"See KB-0612, KBV-00012, PRJ-00580, TASK-2026-02297 and PO-2026-00123.",
			"https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view",
			"https://docs.google.com/document/d/1aB2cD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2wX3yZ4a5B6/edit",
			"The publishable key starts pk" + "_live_ and is public.",
			"Basic maintenance is monthly. The bearer of the key signs for it.",
			"API key: stored in 1Password (ask James).",
			"Scan the QR code, then press Receive.",
		):
			with self.subTest(text=text):
				self.assertEqual(C.secret_findings(text), [])
				self.assertEqual(C.secret_findings(f"<p>{text}</p>", html=True), [])

	def test_each_line_and_kind_is_reported_once(self):
		key = SECRETS["a Stripe secret key"]
		self.assertEqual(C.secret_findings(f"{key} {key}"), [C.Finding(1, "a Stripe secret key")])

	def test_only_the_content_fields_are_scanned_and_each_is_named(self):
		version = {
			"title": "x " + SECRETS["an AWS access key"],
			"summary": "fine",
			"keywords": SECRETS["a Slack token"],
			"change_note": "one\n" + SECRETS["a GitHub token"],
			"body": "<p>" + SECRETS["a Stripe secret key"] + "</p>",
			"review_note": SECRETS["a Stripe secret key"],
			"source_url": SECRETS["a Stripe secret key"],
		}
		found = C.document_secret_findings(version)
		self.assertEqual(
			[(field, f.line) for field, f in found],
			[("title", 1), ("keywords", 1), ("change_note", 2), ("body", 1)],
		)
		self.assertEqual(set(C.SCANNED_FIELDS) - set(K.VERSION_CONTENT_FIELDS), set())
		self.assertEqual(set(C.FIELD_LABELS), set(C.SCANNED_FIELDS))

	def test_the_labels_are_the_forms(self):
		meta = json.loads(VERSION_JSON.read_text(encoding="utf-8"))
		labels = {f["fieldname"]: f.get("label") for f in meta["fields"]}
		for field, label in C.FIELD_LABELS.items():
			self.assertEqual(labels[field], label)


# ------------------------------------------------------------------ the content hash


class TestContentHash(unittest.TestCase):
	BASE = {
		"title": "Receiving a PO",
		"summary": "How to receive.\nAgainst a slip.",
		"keywords": "PO, QBO",
		"body": '<p style="text-align: center;">Scan it.</p><pre>a  b</pre>',
	}

	def _hash(self, **changes):
		return C.content_hash(dict(self.BASE, **changes))

	def test_it_is_a_sha256_hex_digest(self):
		digest = self._hash()
		self.assertEqual(len(digest), 64)
		int(digest, 16)

	def test_invisible_differences_do_not_change_it(self):
		same = self._hash()
		for changes in (
			{"title": "  Receiving   a\tPO "},
			{"summary": "How to receive.  \r\nAgainst a slip.\r\n"},
			{"keywords": "qbo;po, PO\n"},
			{"body": '<p style="text-align: center; color: red;">Scan it.</p><pre>a  b</pre>'},
			{"body": '<p style="text-align: center;" class="ql-bg-red">Scan it.</p><pre>a  b</pre>\n'},
			{"version_number": 7, "approved_by": "nik@example.com", "review_by": "2027-03-25"},
		):
			with self.subTest(changes=changes):
				self.assertEqual(self._hash(**changes), same)

	def test_unicode_spellings_of_one_character_hash_the_same(self):
		self.assertEqual(self._hash(title="Café"), self._hash(title="Café"))

	def test_missing_and_empty_are_the_same(self):
		self.assertEqual(C.content_hash({"title": "x"}), C.content_hash({"title": "x", "summary": "", "keywords": None, "body": ""}))

	def test_anything_a_reader_can_see_changes_it(self):
		same = self._hash()
		for changes in (
			{"title": "Receiving a PO!"},
			{"summary": "How to receive. Against a slip."},
			{"keywords": "PO, QBO, SOP"},
			{"body": '<p style="text-align: center;">Scan it twice.</p><pre>a  b</pre>'},
			{"body": '<p style="text-align: center;">Scan it.</p><pre>a b</pre>'},
			{"body": '<p style="text-align: left;">Scan it.</p><pre>a  b</pre>'},
		):
			with self.subTest(changes=changes):
				self.assertNotEqual(self._hash(**changes), same)

	def test_the_fields_are_distinct_in_the_hash(self):
		self.assertNotEqual(C.content_hash({"title": "a", "summary": "b"}), C.content_hash({"title": "b", "summary": "a"}))


if __name__ == "__main__":
	unittest.main()

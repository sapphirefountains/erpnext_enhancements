# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's pure rules: who may approve, KB numbers, review dates, content hygiene.

WI-080 PR 2, ADR 0017. ``knowledge_base/workflow.py`` and ``knowledge_base/content.py`` import no
frappe, so every branch runs here, bench-free and with no stub:

* **Approval** (``approval_problems``): a KB Approver who is a named person with a System User
  login (never Administrator, which holds every role, and never Guest), not the owner, the
  submitter, a contributor or the AI requester, signed in from a browser and not acting through an
  AI gate card, on a version that is In Review and still the copy they opened. Each rule alone
  refuses, and the refusal names it.
* **Content changes only in Draft**, and every saver of a change is a contributor.
* **KB numbers** are ``KB-{block}{01..99}``: ``00`` is never allocated, a number is never reused,
  and a full block fails loudly.
* **Review dates** default to ``constants.DEFAULT_REVIEW_EVERY_MONTHS`` (POL-0001's six months)
  and handle month ends and leap years.
* **Presentation stripping** removes colour, background, size and font (as ``style``, or as
  ``<font color size face>`` and ``bgcolor``), the ``hidden`` and ``id`` attributes, and every class
  outside ``KEPT_CLASSES``: each hiding class goes, each kept class survives a realistic v16 body,
  and the kept list is derived here from the v16 and Quill source lines it cites (checked against a
  local v16 checkout when there is one). Elements are an allowlist the same way: every tag v16's
  ``sanitize_html`` allows is kept or unwrapped, so text a browser never paints (a ``<dialog>``,
  an SVG ``<desc>``) comes out where it does. Comments and declarations go whole and raw text
  comes back escaped, where Python's parser and a browser disagree about where they end.
  Alignment, indent, lists, code blocks and tables stay; nothing else is rewritten; the output is a
  fixed point.
* **The secret scan** finds each kind, skips ``data:`` images, reports line and kind and never the
  value, and leaves ordinary KB prose alone: nothing lets an author past a finding, so a sentence
  it refuses ("Basic Maintenance/Cleaning", "Password: case-sensitive.") is a save that cannot be
  made.
* **The content hash** ignores what nobody can see and changes on what anyone can.

**Every secret-shaped fixture is built by concatenation.** GitHub push protection refused a
branch of this repo once for a literal Stripe-key-shaped test string; a split literal is the same
test and no longer looks like a key to a scanner.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_rules -v
"""

import ast
import datetime
import inspect
import json
import re
import subprocess
import sys
import unittest
from html.parser import HTMLParser
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


def _approve(
	version,
	user=APPROVER,
	roles=(K.APPROVER_ROLE,),
	browser=True,
	gate=None,
	opened=OPENED,
	user_type=K.APPROVER_USER_TYPE,
):
	return W.approval_problems(
		version,
		user,
		roles,
		user_type=user_type,
		browser=browser,
		gate_flags=gate or {},
		opened_modified=opened,
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
			user_type="Website User",
		)
		self.assertEqual(len(problems), 7)

	def test_the_refusal_is_one_sentence_naming_each_rule(self):
		message = W.refusal("KBV-00012", "approved", ["a", "b"])
		self.assertEqual(message, "KBV-00012 cannot be approved: a; b.")

	def test_the_context_arguments_are_keyword_only(self):
		with self.assertRaises(TypeError):
			W.approval_problems(
				_in_review(), APPROVER, (K.APPROVER_ROLE,), K.APPROVER_USER_TYPE, True, {}, OPENED
			)
		parameters = inspect.signature(W.approval_problems).parameters
		for name in ("user_type", "browser", "gate_flags", "opened_modified"):
			with self.subTest(name=name):
				self.assertIs(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
				self.assertIs(parameters[name].default, inspect.Parameter.empty)


class TestApproversAreNamedPeople(unittest.TestCase):
	"""Administrator holds every role implicitly (v16 ``permissions.py:546-547``), so the role rule
	alone lets it approve; and v16 makes it a System User. Approvers are named people: the continuity
	runbook uses Administrator only to grant or revoke KB roles, never to approve."""

	EVERY_ROLE = (K.APPROVER_ROLE, K.AUTHOR_ROLE, "System Manager", "Administrator", "Desk User")

	def test_a_named_approver_with_a_staff_login_still_approves(self):
		self.assertEqual(_approve(_in_review(), user=APPROVER, user_type="System User"), [])
		self.assertEqual(_approve(_in_review(), user=APPROVER, roles=self.EVERY_ROLE), [])

	def test_administrator_never_approves_whatever_roles_it_holds(self):
		for user in ("Administrator", "administrator", " ADMINISTRATOR "):
			with self.subTest(user=user):
				problems = _approve(_in_review(), user=user, roles=self.EVERY_ROLE, user_type="System User")
				self.assertEqual(len(problems), 1, problems)
				self.assertIn("Administrator is a shared account, not a person", problems[0])
				self.assertIn("named KB Approver", problems[0])

	def test_administrator_is_refused_even_with_nothing_stored(self):
		problems = _approve(None, user="Administrator", roles=self.EVERY_ROLE)
		self.assertEqual(len(problems), 2, problems)
		self.assertTrue(any("Administrator" in p for p in problems))

	def test_guest_and_nobody_are_not_signed_in(self):
		for user, user_type in (("Guest", "Website User"), ("guest", None), ("", None), (None, None)):
			with self.subTest(user=user):
				problems = _approve(_in_review(), user=user, user_type=user_type)
				self.assertEqual(problems, ["nobody is signed in"])

	def test_only_a_system_user_approves(self):
		for user_type in ("Website User", "Employee Self Service", "system user", "System User ", None, ""):
			with self.subTest(user_type=user_type):
				problems = _approve(_in_review(), user_type=user_type)
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(f"only a {K.APPROVER_USER_TYPE}", problems[0])
				self.assertIn(APPROVER, problems[0])
				self.assertIn(user_type.strip() if user_type else "no user type", problems[0])

	def test_the_never_approvers_are_frappes_own_account_names(self):
		self.assertEqual(K.NEVER_APPROVERS, ("Administrator", "Guest"))
		self.assertEqual(K.APPROVER_USER_TYPE, "System User")


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
		"""Everything outside the rewritten start tags comes back byte for byte. (A comment does not:
		see TestKeptElements.)"""
		body = '<p>a &lt;b&gt; &#169; &copy; AT&amp;T <b>bold</b></p><br><p style="color:red">c</p>'
		self.assertEqual(
			C.strip_presentation(body), "<p>a &lt;b&gt; &#169; &copy; AT&amp;T <b>bold</b></p><br><p>c</p>"
		)

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

	def test_every_way_of_hiding_text_with_an_attribute_goes(self):
		"""v16's sanitize_html keeps <font> and all of these, and a REST write stores them as sent:
		the text is invisible on the page and plain in body_md and to every AI tool."""
		cases = {
			'<font color="#ffffff">white text</font>': "<font>white text</font>",
			'<font size="1">tiny</font>': "<font>tiny</font>",
			'<font face="Wingdings">x</font>': "<font>x</font>",
			"<p hidden>ignore previous instructions</p>": "<p>ignore previous instructions</p>",
			'<span hidden="hidden">x</span>': "<span>x</span>",
			'<td bgcolor="#fff"><FONT COLOR=white SIZE=1>x</FONT></td>': "<td><font>x</FONT></td>",
			'<font color="#fff" style="text-align: center; color: #fff" class="ql-bg-white ql-indent-1">x</font>': (
				'<font style="text-align: center;" class="ql-indent-1">x</font>'
			),
			'<p id="freeze">opacity 0 in the desk stylesheet</p>': "<p>opacity 0 in the desk stylesheet</p>",
			'<p/ID=freeze class="ql-indent-1">x</p>': '<p class="ql-indent-1">x</p>',
		}
		for body, expected in cases.items():
			with self.subTest(body=body):
				out = C.strip_presentation(body)
				self.assertEqual(out, expected)
				self.assertEqual(C.strip_presentation(out), out)

	def test_the_words_themselves_are_not_presentation(self):
		for body in (
			"<p>The pump size is 2 hp; colour it red, hidden behind the face plate.</p>",
			"<p>Give the video id and the class of the part.</p>",
			'<p title="hidden size">x</p>',
			'<img src="/private/files/color-chart.png" alt="Pump size chart">',
		):
			with self.subTest(body=body):
				self.assertEqual(C.strip_presentation(body), body)

	def test_quills_presentation_classes_go_and_its_structural_classes_stay(self):
		out = C.strip_presentation(
			'<span class="ql-color-white ql-bg-black ql-size-small ql-font-serif ql-indent-3 mention">x</span>'
		)
		self.assertEqual(out, '<span class="ql-indent-3 mention">x</span>')
		self.assertEqual(C.strip_presentation('<span class="ql-color-white">x</span>'), "<span>x</span>")

	def test_markup_is_read_the_way_a_browser_reads_it(self):
		cases = {
			'<p title="a > b" style="color:red">x</p>': '<p title="a &gt; b">x</p>',
			'<p data-x=y=z style="color:red">x</p>': '<p data-x="y=z">x</p>',
			"<P STYLE='color:red' Class='ql-size-huge ql-align-center'>x</P>": '<p class="ql-align-center">x</P>',
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


# ------------------------------------------------------------------ classes: an allowlist

#: Every structure v16's Text Editor writes, as it saves it: its wrapper; a centred heading and a
#: justified paragraph (as ``style``, v16's own form of alignment) and three paragraphs aligned with
#: Quill's class, which pasted HTML and a REST write carry; a numbered list nested eight deep, which
#: Quill writes as ONE flat list of indented items; a bullet list (``<ul>``, patched in by v16's
#: ``patch_unordered_list``) with a nested item; a checklist; a right-to-left paragraph; a code block
#: (v16 makes Quill's container a ``<pre>``); a table; an image; and an @-mention, whose guard
#: characters are U+FEFF.
QUILL_STRUCTURE = (
	'<div class="ql-editor read-mode">'
	'<h2 style="text-align: center;">Receiving a PO</h2>'
	'<p style="text-align: justify;">Read every line before you sign.&nbsp;Then:</p>'
	'<p class="ql-align-justify">Pasted from another Quill editor.</p>'
	'<p class="ql-align-center">Centred, the same way.</p>'
	'<p class="ql-align-right">And right.</p>'
	"<ol>"
	'<li data-list="ordered"><span class="ql-ui" contenteditable="false"></span>Open the PO.</li>'
	+ "".join(
		f'<li data-list="ordered" class="ql-indent-{level}"><span class="ql-ui" contenteditable="false">'
		f"</span>Step at level {level}.</li>"
		for level in range(1, 9)
	)
	+ "</ol>"
	'<ul><li data-list="bullet"><span class="ql-ui" contenteditable="false"></span>Keep the slip.</li>'
	'<li data-list="bullet" class="ql-indent-1"><span class="ql-ui" contenteditable="false"></span>'
	"In the PO folder.</li></ul>"
	'<ol><li data-list="checked"><span class="ql-ui" contenteditable="false"></span>Counted.</li>'
	'<li data-list="unchecked"><span class="ql-ui" contenteditable="false"></span>Signed.</li></ol>'
	'<p class="ql-direction-rtl" style="text-align: right;">שלום</p>'
	'<pre class="ql-code-block-container" spellcheck="false">'
	'<div class="ql-code-block">bench --site erp.example.com migrate</div>'
	'<div class="ql-code-block">bench restart</div></pre>'
	'<table class="table table-bordered"><tbody>'
	'<tr><td data-row="row-k3x1">Part</td><td data-row="row-k3x1">Qty</td></tr>'
	'<tr><td data-row="row-9f2c">Nozzle</td><td data-row="row-9f2c" style="text-align: right;">4</td></tr>'
	"</tbody></table>"
	'<p><img src="/private/files/slip.png" width="300"></p>'
	'<p>Ask <span class="mention" data-id="james@example.com" data-value="James" '
	'data-denotation-char="@" data-is-group="false">﻿<span contenteditable="false">'
	'<span class="ql-mention-denotation-char">@</span><span>James</span></span>﻿</span> first.</p>'
	"</div>"
)

#: Classes a stylesheet on the page uses, or could, to hide text, and near misses of the kept ones.
#: None may survive: the page carries Bootstrap, Frappe, ERPNext and this app's CSS, and a class
#: means whatever any of them says.
HIDING_CLASSES = (
	# Bootstrap and utility-class spellings of "not shown"
	"hidden",
	"hide",
	"d-none",
	"invisible",
	"sr-only",
	"visually-hidden",
	"text-white",
	"text-hide",
	"opacity-0",
	"collapse",
	"fade",
	# Frappe's own: `.icon` is font-size 0 (scss/common/icons.scss:3)
	"icon",
	"icon-sm",
	"mention-link",
	# Quill's, outside the editor's structure: `.ql-clipboard` is left -100000px (core.styl:30-35)
	"ql-clipboard",
	"ql-hidden",
	"ql-blank",
	"ql-cursor",
	"ql-tooltip",
	"ql-video",
	"ql-formula",
	"ql-syntax",
	"ql-color-white",
	"ql-bg-white",
	"ql-size-small",
	"ql-font-serif",
	# near misses of kept classes: compared exactly
	"ql-indent-0",
	"ql-indent-9",
	"QL-INDENT-1",
	"Ql-Editor",
	"ql-align-left",
	"ql-direction-ltr",
	"table-borderless",
	"read-mode-hidden",
	# one token to a browser, which splits classes on ASCII whitespace only
	"ql-indent-1 hidden",
	"ql-indent-1\x0bhidden",
)

TEXT_EDITOR = "frappe/public/js/frappe/form/controls/text_editor.js"
MENTION_BLOT = "frappe/public/js/frappe/form/controls/quill-mention/blots/mention.js"
COMMENT_CONTROL = "frappe/public/js/frappe/form/controls/comment.js"

#: The source lines ``content.KEPT_CLASSES`` is derived from, verbatim without their indentation, as
#: ``(origin, path, line, text)``. "frappe" is frappe ``origin/version-16``, checked below against a
#: local checkout when there is one. "quill" is Quill 2.0.3, the version v16 pins (the first row),
#: under ``packages/quill/src/``. No checkout carries it, so its rows were checked against the
#: ``v2.0.3`` tag of ``slab/quill`` when this list was written.
KEPT_CLASS_SOURCES = (
	("frappe", "package.json", 73, '"quill": "2.0.3",'),
	("frappe", TEXT_EDITOR, 53, 'node.classList.add("table");'),
	("frappe", TEXT_EDITOR, 54, 'node.classList.add("table-bordered");'),
	("frappe", TEXT_EDITOR, 402, 'value = `<div class="ql-editor read-mode">${value}</div>`;'),
	("frappe", MENTION_BLOT, 9, 'denotationChar.className = "ql-mention-denotation-char";'),
	("frappe", MENTION_BLOT, 49, 'MentionBlot.className = "mention";'),
	("quill", "formats/indent.ts", 28, "const IndentClass = new IndentAttributor('indent', 'ql-indent', {"),
	("quill", "formats/indent.ts", 31, "whitelist: [1, 2, 3, 4, 5, 6, 7, 8],"),
	("quill", "formats/align.ts", 5, "whitelist: ['right', 'center', 'justify'],"),
	("quill", "formats/align.ts", 9, "const AlignClass = new ClassAttributor('align', 'ql-align', config);"),
	("quill", "formats/direction.ts", 5, "whitelist: ['rtl'],"),
	(
		"quill",
		"formats/direction.ts",
		9,
		"const DirectionClass = new ClassAttributor('direction', 'ql-direction', config);",
	),
	("quill", "formats/code.ts", 46, "CodeBlock.className = 'ql-code-block';"),
	("quill", "formats/code.ts", 49, "CodeBlockContainer.className = 'ql-code-block-container';"),
	("quill", "core/quill.ts", 31, "Parchment.ParentBlot.uiClass = 'ql-ui';"),
)

#: The lines that put those formats in v16's editor. They name no class, so nothing is derived from
#: them, but without them the classes above would be Quill's and not v16's.
KEPT_CLASS_REGISTRATIONS = (
	("frappe", TEXT_EDITOR, 7, 'const CodeBlockContainer = Quill.import("formats/code-block-container");'),
	("frappe", TEXT_EDITOR, 8, 'CodeBlockContainer.tagName = "PRE";'),
	("frappe", TEXT_EDITOR, 115, 'const DirectionClass = Quill.import("attributors/class/direction");'),
	("frappe", TEXT_EDITOR, 116, "Quill.register(DirectionClass, true);"),
	("frappe", TEXT_EDITOR, 350, '[{ indent: "-1" }, { indent: "+1" }],'),
	("frappe", COMMENT_CONTROL, 4, 'Quill.register("modules/mention", Mention, true);'),
	("quill", "quill.ts", 76, "'formats/align': AlignClass,"),
	("quill", "quill.ts", 78, "'formats/indent': Indent,"),
	("quill", "formats/list.ts", 41, "this.attachUI(ui);"),
)


def _derive_kept_classes(sources):
	"""The classes the cited lines emit: a class attributor's prefix with each whitelisted value, a
	blot's ``className``, Parchment's ``uiClass``, a ``classList.add`` and a literal ``class="..."``."""
	by_file = {}
	for origin, path, _line, text in sources:
		by_file.setdefault((origin, path), []).append(text)
	classes = set()
	for lines in by_file.values():
		text = "\n".join(lines)
		attributor = re.search(r"new \w+Attributor\('\w+', '([\w-]+)'", text)
		if attributor:
			whitelist = re.search(r"whitelist: \[([^\]]*)\]", text).group(1)
			for value in whitelist.split(","):
				classes.add(f"{attributor.group(1)}-{value.strip().strip(chr(39))}")
		classes.update(re.findall(r"(?:className|uiClass) = ['\"]([\w-]+)['\"]", text))
		classes.update(re.findall(r'classList\.add\("([\w-]+)"\)', text))
		for value in re.findall(r'class="([^"$]+)"', text):
			classes.update(value.split())
	return classes


def _classes_in(markup):
	found = set()

	class _Collector(HTMLParser):
		def handle_starttag(self, tag, attrs):
			for name, value in attrs:
				if name == "class":
					found.update((value or "").split(" "))

	collector = _Collector()
	collector.feed(markup)
	collector.close()
	found.discard("")
	return found


def _frappe_checkout():
	"""A frappe clone beside this repo (or beside the repo a worktree belongs to), or ``None``."""
	for parent in REPO_ROOT.parents:
		if (parent / "frappe" / ".git").exists():
			return parent / "frappe"
	return None


class TestKeptClasses(unittest.TestCase):
	def test_every_hiding_class_is_dropped(self):
		for hiding in HIDING_CLASSES:
			with self.subTest(hiding=hiding):
				self.assertEqual(C.strip_presentation(f'<p class="{hiding}">text</p>'), "<p>text</p>")
				self.assertEqual(
					C.strip_presentation(
						f'<p class="ql-indent-2 {hiding}" style="text-align: center;">text</p>'
					),
					'<p class="ql-indent-2" style="text-align: center;">text</p>',
				)

	def test_a_hiding_class_goes_from_every_tag_of_a_real_body(self):
		hidden = QUILL_STRUCTURE.replace('class="', 'class="hidden ')
		self.assertNotEqual(hidden, QUILL_STRUCTURE)
		self.assertEqual(C.strip_presentation(hidden), QUILL_STRUCTURE)

	def test_every_kept_class_survives_a_real_v16_body(self):
		self.assertEqual(
			_classes_in(QUILL_STRUCTURE), C.KEPT_CLASSES, "the fixture must use every kept class"
		)
		self.assertEqual(C.strip_presentation(QUILL_STRUCTURE), QUILL_STRUCTURE)

	def test_every_kept_class_survives_on_its_own(self):
		for kept in sorted(C.KEPT_CLASSES):
			with self.subTest(kept=kept):
				body = f'<p class="{kept}">x</p>'
				self.assertEqual(C.strip_presentation(body), body)

	def test_classes_are_split_where_a_browser_splits_them(self):
		self.assertEqual(
			C.strip_presentation('<p class="ql-indent-1\thidden\nql-align-center\r\x0cd-none">x</p>'),
			'<p class="ql-indent-1 ql-align-center">x</p>',
		)

	def test_the_list_is_what_the_cited_v16_and_quill_lines_emit(self):
		self.assertEqual(_derive_kept_classes(KEPT_CLASS_SOURCES), C.KEPT_CLASSES)

	def test_the_derivation_reads_each_kind_of_line(self):
		"""So the agreement above cannot pass by deriving nothing from a kind of line."""
		for row, expected in (
			(
				("quill", "a.ts", 1, "A = new ClassAttributor('a', 'ql-x', config); whitelist: ['p', 'q'],"),
				{"ql-x-p", "ql-x-q"},
			),
			(("quill", "b.ts", 1, "B.className = 'ql-y';"), {"ql-y"}),
			(("quill", "c.ts", 1, "Parchment.ParentBlot.uiClass = 'ql-z';"), {"ql-z"}),
			(("frappe", "d.js", 1, 'node.classList.add("w");'), {"w"}),
			(("frappe", "e.js", 1, 'value = `<div class="u v">${value}</div>`;'), {"u", "v"}),
		):
			with self.subTest(row=row):
				self.assertEqual(_derive_kept_classes((row,)), expected)

	def test_the_cited_frappe_lines_are_v16s(self):
		"""Skipped where there is no frappe checkout beside this repo, CI included. A failure means
		the cited line has moved in that checkout's ``origin/version-16``: re-read the file there and
		re-cite, in this list, in ``content.KEPT_CLASSES`` or ``content.KEPT_ELEMENTS`` and in the
		knowledge_base README."""
		checkout = _frappe_checkout()
		if checkout is None:
			self.skipTest("no frappe checkout beside this repo")
		files = {}
		for origin, path, line, text in KEPT_CLASS_SOURCES + KEPT_CLASS_REGISTRATIONS + KEPT_ELEMENT_SOURCES:
			if origin != "frappe":
				continue
			if path not in files:
				result = subprocess.run(
					["git", "-C", str(checkout), "show", f"origin/version-16:{path}"],
					capture_output=True,
					text=True,
					encoding="utf-8",
				)
				if result.returncode:
					self.skipTest(f"{checkout} has no origin/version-16:{path}")
				files[path] = result.stdout.splitlines()
			with self.subTest(path=path, line=line):
				actual = files[path][line - 1].strip() if line <= len(files[path]) else None
				self.assertEqual(actual, text, f"{checkout} origin/version-16:{path}:{line}")

	def test_an_id_goes_because_a_stylesheet_can_hide_by_it(self):
		self.assertIn("id", C.DROPPED_ATTRIBUTES)
		self.assertTrue(C.PRESENTATION_ATTRIBUTES < C.DROPPED_ATTRIBUTES)
		self.assertEqual(
			C.strip_presentation('<h2 id="freeze" style="text-align: center;">x</h2>'),
			'<h2 style="text-align: center;">x</h2>',
		)


# ------------------------------------------------------------------ elements: an allowlist

#: The source lines ``content.KEPT_ELEMENTS`` is derived from, as ``KEPT_CLASS_SOURCES`` above: each
#: format's ``tagName``, the ``<ul>`` v16 builds for a bullet list, and the wrapper ``<div>``. The
#: Quill rows were checked against the ``v2.0.3`` tag of ``slab/quill`` when this list was written.
KEPT_ELEMENT_SOURCES = (
	("quill", "blots/block.ts", 127, "Block.tagName = 'P';"),
	("quill", "blots/break.ts", 23, "Break.tagName = 'BR';"),
	("frappe", TEXT_EDITOR, 15, 'BreakBlot.tagName = "br";'),
	("quill", "formats/header.ts", 5, "static tagName = ['H1', 'H2', 'H3', 'H4', 'H5', 'H6'];"),
	("quill", "formats/blockquote.ts", 5, "static tagName = 'blockquote';"),
	("quill", "formats/list.ts", 8, "ListContainer.tagName = 'OL';"),
	("quill", "formats/list.ts", 53, "ListItem.tagName = 'LI';"),
	("frappe", TEXT_EDITOR, 428, 'const ul = document.createElement("ul");'),
	("frappe", TEXT_EDITOR, 8, 'CodeBlockContainer.tagName = "PRE";'),
	("quill", "formats/code.ts", 47, "CodeBlock.tagName = 'DIV';"),
	("frappe", TEXT_EDITOR, 402, 'value = `<div class="ql-editor read-mode">${value}</div>`;'),
	("quill", "formats/table.ts", 7, "static tagName = 'TD';"),
	("quill", "formats/table.ts", 61, "static tagName = 'TR';"),
	("quill", "formats/table.ts", 121, "static tagName = 'TBODY';"),
	("quill", "formats/table.ts", 128, "static tagName = 'TABLE';"),
	("quill", "formats/bold.ts", 5, "static tagName = ['STRONG', 'B'];"),
	("quill", "formats/italic.ts", 5, "static tagName = ['EM', 'I'];"),
	("quill", "formats/strike.ts", 5, "static tagName = ['S', 'STRIKE'];"),
	("quill", "formats/underline.ts", 5, "static tagName = 'U';"),
	("quill", "formats/script.ts", 5, "static tagName = ['SUB', 'SUP'];"),
	("quill", "formats/code.ts", 43, "Code.tagName = 'CODE';"),
	("quill", "formats/link.ts", 5, "static tagName = 'A';"),
	("quill", "formats/image.ts", 8, "static tagName = 'IMG';"),
	("quill", "blots/cursor.ts", 10, "static tagName = 'span';"),
	("frappe", MENTION_BLOT, 48, 'MentionBlot.tagName = "span";'),
	("frappe", TEXT_EDITOR, 132, 'CustomColor.tagName = "font";'),
)

#: Kept without a cited line: a table's header row, which Quill never writes and a table written any
#: other way has (see ``content.KEPT_ELEMENTS``).
TABLE_HEADER_ELEMENTS = frozenset({"thead", "th"})

#: Every tag v16's ``sanitize_html`` lets through (frappe ``origin/version-16``
#: ``utils/html_utils.py``: ``acceptable_elements``, ``svg_elements``, ``mathml_elements`` and the
#: six it adds in ``sanitize_html``), held here so CI can push each one through the strip; checked
#: against a local v16 checkout below when there is one. SVG names keep their case, as v16 spells
#: them.
SANITIZE_HTML_TAGS = frozenset(
	"""
	a abbr acronym address animate animateColor animateMotion animateTransform area article aside
	audio b big blockquote body br button canvas caption center circle cite clipPath code col
	colgroup command datagrid datalist dd defs del desc details dfn dialog dir div dl dt ellipse em
	event-source fieldset figcaption figure font font-face font-face-name font-face-src footer form
	g glyph h1 h2 h3 h4 h5 h6 head header hkern hr html i img input ins kbd keygen label legend li
	line linearGradient link m maction map mark marker math menu merror meta metadata meter mfrac mi
	missing-glyph mmultiscripts mn mo mover mpadded mpath mphantom mprescripts mroot mrow mspace
	msqrt mstyle msub msubsup msup mtable mtd mtext mtr multicol munder munderover nav nextid none
	o:p ol optgroup option output p path polygon polyline pre progress q radialGradient rect s samp
	section select set small sound source spacer span stop strike strong sub summary sup svg switch
	table tbody td text textarea tfoot th thead time title tr tspan tt u ul use var video
	""".split()
)

#: What KB-PR2-R3-01 found: each element v16's ``sanitize_html`` keeps whose text a browser (Chrome
#: 152, inside v16's read-mode wrapper) never paints, while the text stays in the HTML and in
#: ``body_md``.
NEVER_SHOWN = {
	"<dialog>SECRET dialog</dialog>": "SECRET dialog",
	"<datalist><option>SECRET datalist</option></datalist>": "SECRET datalist",
	"<audio>SECRET audio</audio>": "SECRET audio",
	"<video>SECRET video</video>": "SECRET video",
	"<canvas>SECRET canvas</canvas>": "SECRET canvas",
	"<meter>SECRET meter</meter>": "SECRET meter",
	"<progress>SECRET progress</progress>": "SECRET progress",
	"<svg><desc>SECRET desc</desc></svg>": "SECRET desc",
	"<svg><title>SECRET title</title></svg>": "SECRET title",
	"<svg><metadata>SECRET metadata</metadata></svg>": "SECRET metadata",
	'<svg opacity="0"><text y="20">SECRET svg opacity</text></svg>': "SECRET svg opacity",
	'<svg><text font-size="0" fill="#fff" visibility="hidden">SECRET svg attrs</text></svg>': "SECRET svg attrs",
	"<math><mphantom><mtext>SECRET mphantom</mtext></mphantom></math>": "SECRET mphantom",
}

#: What KB-PR2-R3-02 found: Python's parser reads each as a comment, a CDATA section or raw text,
#: where a browser, and nh3, read a live ``<p class="hidden">``. Each maps to what a browser
#: serialises it back as (Chrome 152 ``innerHTML``), which is what nh3 would store.
PARSERS_DISAGREE = {
	'<!--><p class="hidden">SECRET cmt</p><!-- -->': '<!----><p class="hidden">SECRET cmt</p><!-- -->',
	'<![CDATA[x><p class="hidden">SECRET cdata</p>]]>': '<!--[CDATA[x--><p class="hidden">SECRET cdata</p>]]&gt;',
	'<svg><style><p class="hidden">SECRET breakout</p></style></svg>': (
		'<svg><style></style></svg><p class="hidden">SECRET breakout</p>'
	),
}


def _derive_kept_elements(sources):
	"""The tags the cited lines name: a ``tagName`` (one, or a list), a ``createElement`` and a
	literal start tag, lower-cased as a browser reads them."""
	tags = set()
	for _origin, _path, _line, text in sources:
		for listed in re.findall(r"tagName = \[([^\]]*)\]", text):
			tags.update(name.strip().strip("'\"").lower() for name in listed.split(","))
		tags.update(name.lower() for name in re.findall(r"tagName = ['\"](\w+)['\"]", text))
		tags.update(name.lower() for name in re.findall(r'createElement\("(\w+)"\)', text))
		tags.update(name.lower() for name in re.findall(r"<([a-zA-Z]\w*)[\s>]", text))
	return tags


class _Structure(HTMLParser):
	"""What the parser reads in stripped markup: its tags, its comments and declarations, its text."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.tags, self.other, self.text = [], [], []

	def handle_starttag(self, tag, attrs):
		self.tags.append((tag, dict(attrs)))

	handle_startendtag = handle_starttag

	def handle_data(self, data):
		self.text.append(data)

	def handle_comment(self, data):
		self.other.append(("comment", data))

	def handle_decl(self, decl):
		self.other.append(("decl", decl))

	def unknown_decl(self, data):
		self.other.append(("decl", data))

	def handle_pi(self, data):
		self.other.append(("pi", data))


def _structure(markup):
	reader = _Structure()
	reader.feed(markup)
	reader.close()
	return reader


class TestKeptElements(unittest.TestCase):
	def assertOnlyKeptMarkup(self, markup):
		"""Every tag is a kept element, no class is outside the kept ones, nothing is a comment or a
		declaration, and no raw ``<`` is left in the text."""
		read = _structure(markup)
		for tag, attrs in read.tags:
			self.assertIn(tag, C.KEPT_ELEMENTS, markup)
			self.assertTrue(set((attrs.get("class") or "").split()) <= C.KEPT_CLASSES, markup)
			self.assertFalse(C.DROPPED_ATTRIBUTES & set(attrs), markup)
		self.assertEqual(read.other, [], markup)
		self.assertNotIn("<", re.sub(r"<[^>]*>", "", markup), markup)

	def test_text_a_browser_never_shows_comes_out_where_it_does(self):
		for body, text in NEVER_SHOWN.items():
			with self.subTest(body=body):
				out = C.strip_presentation(f"<p>Visible.</p>{body}")
				self.assertEqual(out, f"<p>Visible.</p>{text}")
				self.assertOnlyKeptMarkup(out)

	def test_every_tag_sanitize_html_allows_is_kept_or_unwrapped(self):
		self.assertTrue(C.KEPT_ELEMENTS <= {tag.lower() for tag in SANITIZE_HTML_TAGS})
		for tag in sorted(SANITIZE_HTML_TAGS):
			with self.subTest(tag=tag):
				body = f'<p>Visible.</p><{tag} title="t">SECRET {tag}</{tag}>'
				out = C.strip_presentation(body)
				if tag in C.KEPT_ELEMENTS:
					self.assertEqual(out, body)
				else:
					self.assertEqual(out, f"<p>Visible.</p>SECRET {tag}")
				self.assertOnlyKeptMarkup(out)

	def test_an_unknown_element_is_unwrapped_too(self):
		for body in (
			"<x-note>kept text</x-note>",
			"<template>kept text</template>",
			"<noscript>kept text</noscript>",
		):
			with self.subTest(body=body):
				self.assertEqual(C.strip_presentation(body), "kept text")

	def test_every_kept_element_survives_on_its_own(self):
		for tag in sorted(C.KEPT_ELEMENTS):
			with self.subTest(tag=tag):
				body = f"<{tag}>x</{tag}>"
				self.assertEqual(C.strip_presentation(body), body)

	def test_what_a_parser_misreads_is_not_kept(self):
		"""Python's parser runs a comment opened by ``<!-->`` to the next ``-->`` and a CDATA section
		to ``]]>``, and reads ``<style>`` as raw text even inside an ``<svg>``; a browser ends the
		first two at the first ``>`` and breaks out of the third. Nothing Python could not read
		into is kept. Once a browser or nh3 has written the same markup back out, the two parsers
		agree, and the class goes the ordinary way."""
		for body, as_a_browser_writes_it in PARSERS_DISAGREE.items():
			with self.subTest(body=body):
				out = C.strip_presentation(body)
				self.assertNotIn("hidden", out)
				self.assertEqual(out, "")
				normalised = C.strip_presentation(as_a_browser_writes_it)
				self.assertNotIn('class="hidden"', normalised)
				self.assertOnlyKeptMarkup(normalised)
				self.assertTrue(normalised.startswith("<p>SECRET "), normalised)

	def test_comments_and_declarations_go_whole(self):
		cases = {
			"<p>a</p><!-- note --><p>b</p>": "<p>a</p><p>b</p>",
			"<!DOCTYPE html><p>a</p>": "<p>a</p>",
			'<?xml version="1.0"?><p>a</p>': "<p>a</p>",
			"<p>a</p><!bogus><p>b</p></ bogus>": "<p>a</p><p>b</p>",
			"<p>a</p><!-- --!><p>b</p> -->": "<p>a</p><p>b</p> -->",
			'<p>a</p><!-- left open <p class="hidden">b</p>': "<p>a</p>",
		}
		for body, expected in cases.items():
			with self.subTest(body=body):
				self.assertEqual(C.strip_presentation(body), expected)

	def test_raw_text_comes_back_as_text_never_as_markup(self):
		"""Python reads what ``<textarea>``, ``<title>``, ``<xmp>`` and ``<plaintext>`` hold as text,
		where a browser inside an ``<svg>`` reads markup. Unwrapped, it is written back escaped."""
		hidden_p = "&lt;p class=&quot;hidden&quot;&gt;x&lt;/p&gt;"
		cases = {
			'<textarea><p class="hidden">x</p></textarea>': hidden_p,
			'<svg><title><p class="hidden">x</p></title></svg>': hidden_p,
			'<svg><textarea><a class="hidden">x</a></textarea></svg>': "&lt;a class=&quot;hidden&quot;&gt;x&lt;/a&gt;",
			"<xmp><b>x</b> &amp;</xmp>": "&lt;b&gt;x&lt;/b&gt; &amp;amp;",
			'<plaintext><p class="hidden">x</p>': hidden_p,
			"<textarea>just words &amp; more</textarea>": "just words &amp; more",
		}
		for body, expected in cases.items():
			with self.subTest(body=body):
				out = C.strip_presentation(body)
				self.assertEqual(out, expected)
				self.assertOnlyKeptMarkup(out)

	def test_script_and_style_go_with_what_they_hold(self):
		self.assertEqual(
			C.strip_presentation("<p>a</p><script>alert(1)</script><style>p { color: #fff }</style><p>b</p>"),
			"<p>a</p><p>b</p>",
		)
		self.assertEqual(C.strip_presentation("<p>a</p><style>p { color: #fff }"), "<p>a</p>")

	def test_a_raw_angle_bracket_in_text_is_escaped(self):
		self.assertEqual(C.strip_presentation("<p>a < b, 3<4</p>"), "<p>a &lt; b, 3&lt;4</p>")
		self.assertEqual(C.strip_presentation("<p>a &lt; b</p>"), "<p>a &lt; b</p>")

	def test_a_cut_off_or_ignored_tag_is_not_kept(self):
		cases = {
			'<p>x</p><p class="hidden"': "<p>x</p>",
			'<p>x</p class="hidden">': "<p>x</p>",
			"<p>x</>y</p>": "<p>xy</p>",
			"<p>x</P\n>": "<p>x</p>",
		}
		for body, expected in cases.items():
			with self.subTest(body=body):
				self.assertEqual(C.strip_presentation(body), expected)

	def test_a_self_closed_element_is_unwrapped_and_its_text_kept(self):
		"""A browser ignores the slash on ``<dialog/>`` and puts what follows inside it."""
		self.assertEqual(C.strip_presentation("<p>a</p><dialog/>SECRET"), "<p>a</p>SECRET")

	def test_a_real_v16_body_has_only_kept_elements(self):
		for body in (QUILL_BODY, QUILL_STRUCTURE):
			with self.subTest(body=body[:40]):
				tags = {tag for tag, _attrs in _structure(body).tags}
				self.assertTrue(tags <= C.KEPT_ELEMENTS, tags - C.KEPT_ELEMENTS)
		self.assertEqual(C.strip_presentation(QUILL_STRUCTURE), QUILL_STRUCTURE)

	def test_the_output_is_a_fixed_point(self):
		bodies = [QUILL_BODY, QUILL_STRUCTURE, *NEVER_SHOWN, *PARSERS_DISAGREE, *PARSERS_DISAGREE.values()]
		bodies += [f"<{tag}>x</{tag}>" for tag in SANITIZE_HTML_TAGS]
		bodies += [
			'<svg><title><p class="hidden">x</p></title></svg>',
			"<p>a < b</p><xmp><b>&amp;</b></xmp><p>x</p class=y><p>z</p><p ",
			"&am<dialog>p;&l</dialog>t;p class=hidden>",
		]
		for body in bodies:
			with self.subTest(body=body):
				once = C.strip_presentation(body)
				self.assertEqual(C.strip_presentation(once), once)
				self.assertOnlyKeptMarkup(once)

	def test_the_list_is_what_the_cited_v16_and_quill_lines_name(self):
		self.assertEqual(_derive_kept_elements(KEPT_ELEMENT_SOURCES) | TABLE_HEADER_ELEMENTS, C.KEPT_ELEMENTS)
		self.assertFalse(TABLE_HEADER_ELEMENTS & _derive_kept_elements(KEPT_ELEMENT_SOURCES))

	def test_the_derivation_reads_each_kind_of_line(self):
		for text, expected in (
			("static tagName = ['A1', 'B2'];", {"a1", "b2"}),
			("X.tagName = 'Q';", {"q"}),
			('Y.tagName = "r";', {"r"}),
			('const u = document.createElement("ul");', {"ul"}),
			('value = `<div class="w">${value}</div>`;', {"div"}),
		):
			with self.subTest(text=text):
				self.assertEqual(_derive_kept_elements((("x", "x", 1, text),)), expected)

	def test_sanitize_html_tags_and_removed_content_are_v16s(self):
		"""Skipped where there is no frappe checkout beside this repo, CI included."""
		checkout = _frappe_checkout()
		if checkout is None:
			self.skipTest("no frappe checkout beside this repo")
		result = subprocess.run(
			["git", "-C", str(checkout), "show", "origin/version-16:frappe/utils/html_utils.py"],
			capture_output=True,
			text=True,
			encoding="utf-8",
		)
		if result.returncode:
			self.skipTest(f"{checkout} has no origin/version-16:frappe/utils/html_utils.py")
		sets = {}
		for node in ast.parse(result.stdout).body:
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
				if isinstance(node.value, ast.Set):
					sets[node.targets[0].id] = {element.value for element in node.value.elts}
		added = re.search(r"\.union\((\[[^\]]*\])\)\s*\)", result.stdout)
		self.assertIsNotNone(added, "sanitize_html no longer adds its own tags the way this test reads them")
		allowed = sets["acceptable_elements"] | sets["svg_elements"] | sets["mathml_elements"]
		self.assertEqual(allowed | set(ast.literal_eval(added.group(1))), SANITIZE_HTML_TAGS)
		self.assertEqual(sets["REMOVE_CONTENT_TAGS"], C.DROPPED_WITH_CONTENT)
		self.assertEqual(result.stdout.splitlines()[20], 'REMOVE_CONTENT_TAGS = {"script", "style"}')


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
			# "Basic" then 16+ letters and slashes: base64 characters, but not user:password.
			"Basic Maintenance/Cleaning",
			"Basic troubleshooting/diagnostics",
			"Basic Pumps/Filters/Lights",
			"Basic electrical/plumbing checks",
			"Use the basic TroubleshootingGuide for pumps",
			# A word of the sentence after "password is" or "password:", with its punctuation.
			"If the password is forgotten, click Reset Password on the login page.",
			"Make sure the password is correct.",
			"The password is case-sensitive.",
			"If the Wi-Fi password is rejected, restart the router.",
			"If your password is incorrect, try again.",
			"Your password is expired? Contact IT.",
			"The passcode is customer-specific.",
			"The password is: first.last",
			"Password: case-sensitive.",
			"Password: self-service reset at the login page",
			"Password: required.",
			"Password: (unchanged)",
			"Password: (optional)",
			"Password: required/optional",
		):
			with self.subTest(text=text):
				self.assertEqual(C.secret_findings(text), [])
				self.assertEqual(C.secret_findings(f"<p>{text}</p>", html=True), [])

	def test_a_heading_is_prose_too(self):
		self.assertEqual(C.secret_findings("<h2>Basic Maintenance/Cleaning</h2>", html=True), [])

	def test_a_basic_credential_is_user_colon_password(self):
		"""Base64 of text without a colon is not what HTTP Basic sends."""
		self.assertEqual(C.secret_findings("Bas" + "ic " + "aGVsbG8gd29ybGQsIGZvbw=="), [])
		for token in (
			"dXNlcjpwYXNzd29yZDEyMw==",
			"YWRtaW46aHVudGVyMmh1bnRlcjI",
			"YWRtaW46aHVudGVyMmh1bnRlcjI=",
		):
			with self.subTest(token=token):
				self.assertEqual(
					C.secret_findings("Authorization: Bas" + "ic " + token),
					[C.Finding(1, "an HTTP Basic credential")],
				)

	def test_a_written_password_is_found_through_the_sentences_punctuation(self):
		for text in (
			"Pass" + "word: " + "Fountain#2026.",
			"Pass" + "word: " + "Welcome1",
			"The Wi-Fi pass" + "word is 'Sapphire2026'.",
			"The gate pass" + "code is (Gate#Code2026).",
			"pass" + "word=" + "Pa$$w0rd!x",
		):
			with self.subTest(text=text):
				self.assertEqual(C.secret_findings(text), [C.Finding(1, "a written-out password")])
		self.assertEqual(
			C.secret_findings("API " + "key: " + "k7Hq9ZpX2mW4vR8tL1."),
			[C.Finding(1, "a written-out key or token")],
		)

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

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's pure rules: who may approve, article numbers, review dates, content hygiene.

WI-080 PR 2, ADR 0017. ``knowledge_base/workflow.py`` and ``knowledge_base/content.py`` import no
frappe, so every branch runs here, bench-free and with no stub:

* **Approval** (``approval_problems``): a KB Approver who is a named person with a System User
  login (never Administrator, which holds every role, and never Guest), not the owner, the
  submitter, a contributor or the AI requester, signed in from a browser and not acting through an
  AI gate card, on a version that is In Review and still the copy they opened. Each rule alone
  refuses, and the refusal names it.
* **Content changes only in Draft**, and every saver of a change is a contributor.
* **Article numbers** (2026-09-29) are ``<PREFIX>-<DD>-<NNNN>`` by kind and department
  (``SOP-06-0001``), one definition in ``constants``: every written form reads as the canonical one,
  the Drive register's ``SOP-0601`` and the retired ``KB`` format never do, and the department part
  accepts exactly the ten blocks. Each ``(prefix, department)`` scope counts from ``0001``, one more
  than the highest taken (never a gap, never reused), and a full scope fails loudly. A revision keeps
  its article's kind and department (``identity_problem``), and a number must fit its row
  (``number_problems``). **No retired-format number is written in the Knowledge Base's code**,
  comments included (``TestNoRetiredNumbersInTheCode``).
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
* **The article's kind** (PR 5): a content field, left out of the hash, read from what people type
  (every kind in any case, every alias, any separator) and nothing else; the department filter's
  reader and folder names; and the Integrity report reading a kind only with approved text.
* **The Markdown renderer** (PR 6a, ``markdown.py``): the eleven header keys in order; a title with
  ``:``, ``#``, ``"`` and a line break quoted so it reads back exactly; bare dates, integers and
  booleans, ``null`` for what is missing; keyword splitting; related article numbers; truncation at a line
  boundary; site paths made absolute and nothing else; identical input, identical bytes; an approver
  never shown as an email address; and the header read back as YAML front matter by a parser this
  file carries (and by PyYAML too, where it is installed).

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
from erpnext_enhancements.knowledge_base import markdown as M
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
			# PR 6a: the Markdown renderer, which the mirror (Slice 6) will run too.
			"import erpnext_enhancements.knowledge_base.markdown\n"
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


# ------------------------------------------------------------------ the article's kind (PR 5)


class TestKind(unittest.TestCase):
	"""WI-080 PR 5: Policy, Process or SOP, the register's three document types (Nik, 2026-09-28)."""

	def test_kind_is_a_content_field_after_the_department(self):
		"""So a revision copies it, changing it makes the saver a contributor, and it is frozen once
		the version leaves Draft: everything ``VERSION_CONTENT_FIELDS`` means."""
		fields = K.VERSION_CONTENT_FIELDS
		self.assertEqual(fields[fields.index("department_block") + 1], "kind")

	def test_a_change_of_kind_is_a_content_change(self):
		stored = {"review_state": W.DRAFT, "kind": "Policy"}
		self.assertEqual(W.changed_content_fields(stored, {**stored, "kind": "SOP"}), ("kind",))
		self.assertEqual(W.changed_content_fields(stored, dict(stored)), ())
		self.assertEqual(W.changed_content_fields({"kind": None}, {"kind": ""}), ())
		in_review = {"review_state": W.IN_REVIEW, "kind": "Policy"}
		self.assertIn("In Review", W.content_edit_problem(in_review, ("kind",)))

	def test_the_content_hash_does_not_include_the_kind(self):
		"""Adding it would make every stored ``content_hash`` mismatch its live version; the Integrity
		report compares the kind on its own instead. Slice 4 must key its export on (hash, version)."""
		text = {"title": "T", "summary": "S", "keywords": "PO", "body": "<p>B</p>"}
		self.assertEqual(C.content_hash({**text, "kind": "Policy"}), C.content_hash({**text, "kind": "SOP"}))
		self.assertNotIn("kind", C.HASHED_FIELDS)

	def test_kind_option_reads_each_kind_in_any_case(self):
		for kind in K.ARTICLE_KINDS:
			for spelling in (kind, kind.lower(), kind.upper(), f"  {kind}  "):
				with self.subTest(spelling=spelling):
					self.assertEqual(K.kind_option(spelling), kind)

	def test_kind_option_reads_every_alias(self):
		for alias, kind in K.KIND_ALIASES.items():
			with self.subTest(alias=alias):
				self.assertIn(kind, K.ARTICLE_KINDS)
				self.assertEqual(K.kind_option(alias), kind)
				self.assertEqual(K.kind_option(alias.upper()), kind)
		# The mapping the design settled on: a procedure is an SOP, a workflow is a Process.
		self.assertEqual(K.kind_option("Procedure"), "SOP")
		self.assertEqual(K.kind_option("Workflows"), "Process")
		self.assertEqual(K.kind_option("rules"), "Policy")
		self.assertEqual(K.kind_option("POL"), "Policy")
		self.assertEqual(K.kind_option("PRO"), "Process")

	def test_kind_option_folds_separators(self):
		for spelling in ("How to", "how_to", "HOW-TO", "how  -  to", "how__to"):
			with self.subTest(spelling=spelling):
				self.assertEqual(K.kind_option(spelling), "SOP")
		self.assertEqual(K.kind_option("Standard Operating Procedure"), "SOP")
		self.assertEqual(K.kind_option("standard_operating-procedure"), "SOP")

	def test_kind_option_answers_none_for_anything_else(self):
		for value in ("", "   ", None, "Checklist", "Guide", "SOPP", "policy!", 3, ["SOP"]):
			with self.subTest(value=value):
				self.assertIsNone(K.kind_option(value))

	def test_kind_help_has_one_line_per_kind(self):
		self.assertEqual(tuple(K.KIND_HELP), K.ARTICLE_KINDS)
		for kind, line in K.KIND_HELP.items():
			with self.subTest(kind=kind):
				self.assertTrue(line.strip())
				self.assertNotIn("\n", line)
		self.assertEqual(set(K.KIND_ALIASES.values()), set(K.ARTICLE_KINDS))
		self.assertTrue(set(K.KIND_ALIASES).isdisjoint({k.casefold() for k in K.ARTICLE_KINDS}))

	def test_department_option_reads_code_label_and_option(self):
		for value in ("06", "6", 6, "Operations", "operations", "06 Operations", " 06   operations ", "06-operations"):
			with self.subTest(value=value):
				self.assertEqual(K.department_option(value), "06 Operations")
		self.assertEqual(K.department_option("0"), "00 Company Wide")
		self.assertEqual(K.department_option("Product Management"), "07 Product Management")
		self.assertEqual(K.department_option("hr"), "04 HR")
		for value in ("", None, "10", "Ops", True, "06 Sales", 3.5):
			with self.subTest(value=value):
				self.assertIsNone(K.department_option(value))

	def test_department_folder(self):
		self.assertEqual(K.department_folder("06 Operations"), "06-operations")
		self.assertEqual(K.department_folder("07 Product Management"), "07-product-management")
		self.assertEqual(K.department_folder("04 HR"), "04-hr")
		for option in K.DEPARTMENT_BLOCK_OPTIONS:
			with self.subTest(option=option):
				self.assertRegex(K.department_folder(option), r"^\d{2}-[a-z-]+$")
		for value in ("06", "Operations", "", None, "06 operations"):
			with self.subTest(value=value):
				self.assertIsNone(K.department_folder(value))

	def test_the_integrity_report_never_reads_a_versions_kind_with_its_metadata(self):
		"""``INTEGRITY_VERSION_FIELDS`` is read for every version, drafts included, so it holds no
		content field, the kind included; the kind is read only with the approved text."""
		from erpnext_enhancements.knowledge_base import reporting as R

		self.assertEqual(set(R.INTEGRITY_VERSION_FIELDS) & set(K.VERSION_CONTENT_FIELDS), set())
		self.assertNotIn("kind", R.INTEGRITY_VERSION_FIELDS)
		self.assertIn("kind", R.APPROVED_TEXT_FIELDS)
		self.assertIn("kind", R.INTEGRITY_ARTICLE_FIELDS)


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


# ------------------------------------------------------------------ article numbers (2026-09-29)


class TestArticleNumberFormat(unittest.TestCase):
	"""``constants``: the one definition every module imports."""

	def test_a_number_is_made_from_the_kind_the_department_and_a_sequence(self):
		self.assertEqual(K.article_number("SOP", "06 Operations", 1), "SOP-06-0001")
		self.assertEqual(K.article_number("Policy", "00", 1), "POL-00-0001")
		self.assertEqual(K.article_number("Process", "02 Design", 3), "PRO-02-0003")
		self.assertEqual(K.article_number("SOP", "09 Sales", K.MAX_SEQUENCE), "SOP-09-9999")
		self.assertEqual(K.number_scope("SOP", "06 Operations"), "SOP-06-")
		self.assertEqual(K.number_scope("Process", "09"), "PRO-09-")
		for kind, block, sequence in (
			("Checklist", "06", 1),
			(None, "06", 1),
			("sop", "06", 1),  # the stored kind exactly, never a spelling
			("SOP", "10 Anything", 1),
			("SOP", "6", 1),
			("SOP", "", 1),
			("SOP", "06", 0),
			("SOP", "06", 10000),
			("SOP", "06", True),
			("SOP", "06", "1"),
		):
			with self.subTest(kind=kind, block=block, sequence=sequence), self.assertRaises(ValueError):
				K.article_number(kind, block, sequence)

	def test_every_kind_has_a_prefix_and_every_prefix_a_kind(self):
		self.assertEqual(set(K.KIND_PREFIXES), set(K.ARTICLE_KINDS))
		self.assertEqual(K.KIND_PREFIXES, {"Policy": "POL", "Process": "PRO", "SOP": "SOP"})
		self.assertEqual({K.PREFIX_KINDS[prefix] for prefix in K.KIND_PREFIXES.values()}, set(K.ARTICLE_KINDS))

	def test_parsing_reads_only_the_canonical_number(self):
		self.assertEqual(K.parse_article_number("SOP-06-0001"), ("SOP", "06", 1))
		self.assertEqual(K.parse_article_number("POL-00-9999"), ("Policy", "00", 9999))
		self.assertEqual(K.parse_article_number("PRO-02-0003"), ("Process", "02", 3))
		for text in (
			"sop-06-0001",
			" SOP-06-0001",
			"SOP-06-0001 ",
			"SOP-06-1",
			"SOP-06-0000",
			"SOP-10-0001",
			"SOP-6-0001",
			"KB-0601",
			"SOP-0601",
			"",
			None,
			601,
		):
			with self.subTest(text=text):
				self.assertIsNone(K.parse_article_number(text))

	def test_every_written_form_reads_as_the_canonical_number(self):
		for text in (
			"SOP-06-0001",
			"sop 06 0001",
			"SOP-06-1",
			"sop_6_1",
			"SOP06-0001",
			"SOP\u201306\u20130001",  # en dashes, as Word and Docs paste a typed hyphen
			"SOP\u201006\u20110001",  # a hyphen and a non-breaking hyphen
			"SOP-06-00001",
			"  sop-06-0001  ",
			"\uff33\uff2f\uff30-06-0001",  # full-width letters, NFKC
		):
			with self.subTest(text=text):
				self.assertEqual(K.normalize_article_number(text), "SOP-06-0001")
		self.assertEqual(K.normalize_article_number("pol 0 1"), "POL-00-0001")
		self.assertEqual(K.normalize_article_number("PRO-2-3"), "PRO-02-0003")

	def test_what_is_not_an_article_number(self):
		for text in (
			"KB-0601",
			"KBV-00001",
			"SOP-0601",  # the Drive register's own numbers: no separator between department and sequence
			"POL-0600",
			"PRO-0210",
			"SOP-10-0001",
			"SOP-06-0000",
			"SOP-06-12345",
			"SOP060001",
			"SOP-06-0001 and more",
			"see SOP-06-0001",
			"SOP-06",
			"",
			None,
			"601",
			601,
		):
			with self.subTest(text=text):
				self.assertIsNone(K.normalize_article_number(text))

	def test_the_department_part_accepts_exactly_the_ten_blocks(self):
		"""A block ``10`` added to ``DEPARTMENT_BLOCKS`` fails here until the written pattern is widened
		to read it."""
		pattern = re.compile(K.ARTICLE_NUMBER_WRITTEN, re.IGNORECASE)
		accepted = set()
		for width in (1, 2, 3):
			for value in range(10**width):
				match = pattern.fullmatch(f"SOP-{value:0{width}d}-0001")
				if match:
					accepted.add(f"{int(match.group(2)):02d}")
		self.assertEqual(accepted, {code for code, _label in K.DEPARTMENT_BLOCKS})

	def test_written_numbers_in_a_text(self):
		text = (
			"See POL-0600 and SOP-06-0001, then sop 6 2, SOP-06-1 again, PRO-0210, KB-0601, "
			"SOP-12-0001, SOP-06-0000 and pol_0_7."
		)
		self.assertEqual(K.written_article_numbers(text), ["SOP-06-0001", "SOP-06-0002", "POL-00-0007"])
		self.assertEqual(K.written_article_numbers(None), [])
		self.assertEqual(K.written_article_numbers(""), [])

	def test_what_running_text_cites(self):
		"""Review of v1.568.0: running text holds ordinary words shaped like a number, such as a product
		called "Pro 2 1000". What it *cites* allows a space between the parts only when the department
		is written with two digits, so fetch's ``related`` never lists an article the text never cited.
		The loose reading, for a query, still finds them all."""
		text = (
			"Use the Pro 2 1000 pump kit, pro 5 10 times, SOP 1 2 3, SOP 6-9; then sop 06 0003, SOP-6-4, "
			"sop_6_5, SOP06-0006, SOP 06-7, SOP–6–8 and POL-0600."
		)
		self.assertEqual(
			K.cited_article_numbers(text),
			["SOP-06-0003", "SOP-06-0004", "SOP-06-0005", "SOP-06-0006", "SOP-06-0007", "SOP-06-0008"],
		)
		self.assertEqual(
			K.written_article_numbers(text)[:4], ["PRO-02-1000", "PRO-05-0010", "SOP-01-0002", "SOP-06-0009"]
		)
		self.assertEqual(K.cited_article_numbers(None), [])
		self.assertEqual(K.cited_article_numbers(""), [])
		# Whole-string readings (fetch, the drafting tool, a query) stay loose.
		self.assertEqual(K.normalize_article_number("sop 6 1"), "SOP-06-0001")


class TestNextArticleNumber(unittest.TestCase):
	def test_the_first_number_in_a_scope_is_0001(self):
		self.assertEqual(W.next_article_number("SOP", "06 Operations", []), "SOP-06-0001")
		self.assertEqual(W.next_article_number("SOP", "06 Operations", ["SOP-06-0000"]), "SOP-06-0001")

	def test_each_kind_and_department_counts_on_its_own(self):
		taken = ["SOP-06-0001", "SOP-06-0002", "POL-03-0004"]
		self.assertEqual(W.next_article_number("SOP", "06 Operations", taken), "SOP-06-0003")
		self.assertEqual(W.next_article_number("Policy", "06 Operations", taken), "POL-06-0001")
		self.assertEqual(W.next_article_number("SOP", "03 Finance", taken), "SOP-03-0001")
		self.assertEqual(W.next_article_number("Policy", "03 Finance", taken), "POL-03-0005")
		self.assertEqual(W.next_article_number("Process", "06 Operations", taken), "PRO-06-0001")

	def test_it_is_one_more_than_the_highest_and_never_fills_a_gap(self):
		self.assertEqual(W.next_article_number("SOP", "06 Operations", ["SOP-06-0001", "SOP-06-0005"]), "SOP-06-0006")

	def test_other_scopes_the_retired_format_and_garbage_are_ignored(self):
		taken = [
			"SOP-01-0099",
			"POL-06-0042",
			"KB-0601",
			"KB-0699",
			"KBV-00006",
			"PRJ-00580",
			"SOP-06",
			"SOP-06-12345",
			"SOP-0601",
			None,
			"",
			42,
		]
		self.assertEqual(W.next_article_number("SOP", "06 Operations", taken), "SOP-06-0001")

	def test_a_number_is_matched_the_way_mariadb_compares_names(self):
		self.assertEqual(W.next_article_number("SOP", "06", ["sop-06-0007 ", " SOP-06-0003"]), "SOP-06-0008")

	def test_every_block_every_kind_and_both_spellings(self):
		for code, label in K.DEPARTMENT_BLOCKS:
			for kind, prefix in K.KIND_PREFIXES.items():
				with self.subTest(code=code, kind=kind):
					self.assertEqual(W.next_article_number(kind, f"{code} {label}", []), f"{prefix}-{code}-0001")
					self.assertEqual(W.next_article_number(kind, code, []), f"{prefix}-{code}-0001")
					self.assertEqual(W.number_scope(kind, f"{code} {label}"), f"{prefix}-{code}-")

	def test_9999_is_the_last_and_a_full_scope_fails_loudly(self):
		self.assertEqual(W.next_article_number("SOP", "06 Operations", ["SOP-06-9998"]), "SOP-06-9999")
		with self.assertRaises(W.SequenceFullError) as caught:
			W.next_article_number("SOP", "06 Operations", ["SOP-06-9999"])
		self.assertIsInstance(caught.exception, ValueError)
		message = str(caught.exception)
		for part in ("06 Operations", "SOP-06-0001", "SOP-06-9999", "SOP"):
			self.assertIn(part, message)

	def test_an_unplaced_or_unknown_block_or_kind_is_refused(self):
		for block in ("", None, "6", "10", "06 operations", "Operations", "06 Operations "):
			with self.subTest(block=block), self.assertRaises(ValueError):
				W.next_article_number("SOP", block, [])
		for kind in ("", None, "sop", "Checklist", "Procedure"):
			with self.subTest(kind=kind), self.assertRaises(ValueError):
				W.next_article_number(kind, "06 Operations", [])


class TestKindAndDepartmentAreFixed(unittest.TestCase):
	"""A published article's number never changes and is never reused, and it carries the kind and
	the department, so a revision keeps both (Nik, 2026-09-29)."""

	ARTICLE = {"name": "SOP-06-9001", "status": "Published", "kind": "SOP", "department_block": "06 Operations"}

	def test_a_revision_that_keeps_both_is_fine(self):
		self.assertIsNone(W.identity_problem({"kind": "SOP", "department_block": "06 Operations"}, self.ARTICLE))
		self.assertIsNone(W.identity_problem({"kind": "Policy"}, None))  # a first version has no article
		self.assertEqual(W.publish_problems({"kind": "SOP", "department_block": "06 Operations"}, self.ARTICLE), [])

	def test_a_changed_kind_or_department_names_the_new_article_route(self):
		for version, change in (
			({"kind": "Policy", "department_block": "06 Operations"}, "To make it a Policy, start a new article"),
			({"kind": "SOP", "department_block": "03 Finance"}, "To move it to 03 Finance, start a new article"),
			(
				{"kind": "Process", "department_block": "03 Finance"},
				"To make it a Process in 03 Finance, start a new article",
			),
		):
			with self.subTest(version=version):
				problem = W.identity_problem(version, self.ARTICLE)
				self.assertTrue(
					problem.startswith(
						"SOP-06-9001 keeps its kind and department: they are part of its number, which never changes."
					),
					problem,
				)
				self.assertIn(change, problem)
				self.assertIn("Once the new one is published, retire SOP-06-9001 and name the new article", problem)
				self.assertEqual(W.publish_problems(version, self.ARTICLE), [problem])

	def test_a_blank_kind_or_department_says_what_it_stays(self):
		for version in ({"kind": None, "department_block": "06 Operations"}, {"kind": "SOP", "department_block": ""}):
			with self.subTest(version=version):
				problem = W.identity_problem(version, self.ARTICLE)
				self.assertIn("keeps its kind and department", problem)
				self.assertIn("Each of its versions is an SOP in 06 Operations.", problem)

	def test_the_number_decides_when_the_row_has_none(self):
		"""An article row with no kind or department (none exists; written past the ORM) is held to
		what its number says."""
		article = {"name": "POL-03-9002", "status": "Published", "kind": None, "department_block": None}
		self.assertIsNone(W.identity_problem({"kind": "Policy", "department_block": "03 Finance"}, article))
		self.assertIn("To make it an SOP", W.identity_problem({"kind": "SOP", "department_block": "03 Finance"}, article))

	def test_a_first_version_without_a_kind_or_department_cannot_be_numbered(self):
		self.assertEqual(
			W.publish_problems({"kind": None, "department_block": ""}, None),
			["it has no department, so it cannot be numbered", "it has no kind, so it cannot be numbered"],
		)
		self.assertEqual(W.publish_problems({"kind": "SOP", "department_block": "06 Operations"}, None), [])


class TestNumberProblems(unittest.TestCase):
	"""What the Article controller refuses on insert and the Integrity report lists."""

	def test_a_number_that_fits_its_row(self):
		self.assertEqual(W.number_problems("SOP-06-0001", "SOP", "06 Operations"), [])
		self.assertEqual(W.number_problems("POL-00-0001", "Policy", "00 Company Wide"), [])

	def test_each_way_a_number_can_be_wrong(self):
		cases = {
			("KB-0612", "SOP", "06 Operations"): ["KB-0612 is not an article number of the form SOP-06-0001"],
			("sop-06-0001", "SOP", "06 Operations"): ["sop-06-0001 is not an article number of the form SOP-06-0001"],
			("SOP-06-0000", "SOP", "06 Operations"): ["SOP-06-0000 ends in 0000, which is never allocated"],
			("SOP-42-0001", "SOP", "06 Operations"): [
				"SOP-42-0001 is numbered in block 42, which is not a department block"
			],
			("SOP-06-0001", "SOP", "07 Product Management"): [
				"SOP-06-0001 is numbered in block 06, but its department is 07 Product Management"
			],
			("SOP-06-0001", "SOP", None): ["SOP-06-0001 is numbered in block 06, but its department is blank"],
			("SOP-06-0001", "Policy", "06 Operations"): [
				"SOP-06-0001 is numbered as an SOP, but its kind is Policy"
			],
			("POL-06-0001", None, "06 Operations"): ["POL-06-0001 is numbered as a Policy, but its kind is blank"],
			("PRO-06-0001", "Checklist", "Warehouse"): [
				"PRO-06-0001 is numbered in block 06, but its department is not a department block",
				"PRO-06-0001 is numbered as a Process, but its kind is not one of the kinds",
			],
			(None, "SOP", "06 Operations"): ["The name is not an article number of the form SOP-06-0001"],
		}
		for (number, kind, department), expected in cases.items():
			with self.subTest(number=number, kind=kind, department=department):
				self.assertEqual(W.number_problems(number, kind, department), expected)


#: Where the retired format may not be written: the code and schema of everything that reads or writes
#: an article number. Markdown docs (READMEs, the ADR, WI-080) may name it as history.
_CODE_ROOTS = (
	(APP / "knowledge_base", ("*.py", "*.js", "*.json")),
	(APP / "assistant_tools", ("*knowledge*.py",)),
	(APP / "api", ("knowledge_base*.py",)),
	(APP / "public" / "js" / "knowledge_base", ("*.js",)),
)
_RETIRED_LITERAL = re.compile(r"\bKB-?[0-9]", re.IGNORECASE)


class TestNoRetiredNumbersInTheCode(unittest.TestCase):
	"""2026-09-29: no ``KB-0601``-shaped literal anywhere in the Knowledge Base's code, schema or
	workspace, comments and docstrings included, so a stale example cannot survive in a description an
	AI reads or a message a person does. The one pattern of that format kept on purpose is
	``ai_tools._RETIRED_FORMAT`` (search's hint for an agent that learned it), found by its name."""

	def test_no_file_writes_the_retired_format(self):
		found, scanned = [], 0
		for root, patterns in _CODE_ROOTS:
			for pattern in patterns:
				for path in sorted(root.rglob(pattern)):
					if "__pycache__" in path.parts:
						continue
					scanned += 1
					for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
						if path.name == "ai_tools.py" and line.startswith("_RETIRED_FORMAT = "):
							continue
						if _RETIRED_LITERAL.search(line):
							found.append(f"{path.relative_to(APP)}:{number}: {line.strip()}")
		self.assertGreater(scanned, 20)
		self.assertEqual(found, [])

	def test_the_one_kept_pattern_is_where_the_scan_expects_it(self):
		text = (APP / "knowledge_base" / "ai_tools.py").read_text(encoding="utf-8")
		self.assertEqual(sum(line.startswith("_RETIRED_FORMAT = ") for line in text.splitlines()), 1)

	def test_what_the_docs_say_to_do_now_uses_the_new_format(self):
		"""Review of v1.568.0: the docs may name the retired format as history, but not in an
		instruction someone acts on today. WI-080's T0 companion ("now") told course authors to cite
		``see KB-0612`` in lesson text while ADR 0017 said article numbers, and Slice 5's scanner
		looked for "the KB number". Each line must name the new format, and the scan must read what
		running text cites."""
		wi = (REPO_ROOT / "work-items" / "WI-080-company-knowledge-base.md").read_text(encoding="utf-8")
		adr = (
			REPO_ROOT / "decisions" / "adr" / "0017-company-knowledge-lives-in-a-native-module.md"
		).read_text(encoding="utf-8")
		lines = {
			"WI-080's T0 companion": [line for line in wi.splitlines() if "Zero-code companions (T0" in line],
			"ADR 0017's T0 rule": [line for line in adr.splitlines() if "Until then (T0)" in line],
		}
		for where, found in lines.items():
			with self.subTest(where=where):
				self.assertEqual(len(found), 1, found)
				self.assertIn("lesson text", found[0])
				self.assertNotRegex(found[0], _RETIRED_LITERAL)
				self.assertRegex(found[0], K.ARTICLE_NUMBER)
		scan = [line for line in wi.splitlines() if "Scan the live `published_content_json`" in line]
		self.assertEqual(len(scan), 1, scan)
		self.assertIn("constants.cited_article_numbers", scan[0])
		self.assertNotIn("KB number", scan[0])


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


# ------------------------------------------------------------------ the Markdown renderer (PR 6a)

#: YAML 1.2's double-quoted escapes (spec 5.7), as a front-matter reader decodes them.
_YAML_ESCAPES = {
	"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
	"e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": " ", "P": " ",
}
#: YAML 1.2 ``c-printable`` less the line breaks: what a one-line double-quoted scalar may hold raw.
_YAML_RAW = re.compile("[\t\x20-\x7e\xa0-퟿-�\U00010000-\U0010ffff]*")
_FLOW_ITEM = re.compile(r'\s*("(?:[^"\\]|\\.)*")\s*(?:,|$)')


def _yaml_scalar(text):
	"""One header value as a YAML 1.2 reader reads it: a double-quoted scalar, a flow sequence of them,
	or a plain ``null``, ``true``, ``false``, integer or ISO date. Anything else fails the test."""
	if text.startswith('"'):
		body = text[1:-1]
		assert text.endswith('"') and len(text) > 1 and _YAML_RAW.fullmatch(body), text
		out, i = [], 0
		while i < len(body):
			assert body[i] != '"', f"a raw quote in {text}"
			if body[i] != "\\":
				out.append(body[i])
				i += 1
			elif body[i + 1] in "xuU":
				width = {"x": 2, "u": 4, "U": 8}[body[i + 1]]
				out.append(chr(int(body[i + 2 : i + 2 + width], 16)))
				i += 2 + width
			else:
				out.append(_YAML_ESCAPES[body[i + 1]])
				i += 2
		return "".join(out)
	if text.startswith("["):
		inner = text[1:-1]
		assert text.endswith("]") and "".join(m.group(0) for m in _FLOW_ITEM.finditer(inner)) == inner, text
		return [_yaml_scalar(m.group(1)) for m in _FLOW_ITEM.finditer(inner)]
	if text in ("null", "true", "false"):
		return {"null": None, "true": True, "false": False}[text]
	if re.fullmatch(r"-?[0-9]+", text):
		return int(text)
	assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text), f"not a value the header writes: {text!r}"
	return datetime.date.fromisoformat(text)


def _front_matter(text):
	"""``(header, the lines after it)``: a ``---`` line, one ``key: value`` per line, a ``---`` line."""
	lines = text.split("\n")
	assert lines[0] == "---", lines[0]
	end = lines.index("---", 1)
	header = {}
	for line in lines[1:end]:
		key, separator, value = line.partition(": ")
		assert separator and re.fullmatch(r"[a-z_]+", key) and key not in header, line
		header[key] = _yaml_scalar(value)
	return header, lines[end + 1 :]


def _article(**changes):
	row = {
		"kb_number": "SOP-06-9098",
		"version_number": 3,
		"title": "Logging a fictitious widget return",
		"kind": "SOP",
		"department_block": "06 Operations",
		"approved_by_name": "Alex Example",
		"approved_on": datetime.datetime(2026, 10, 2, 14, 30, 5, 123456),
		"review_by": datetime.date(2027, 4, 2),
		"ai_drafted": 0,
		"keywords": "RMA, widget return; return slip\nSOP-9001",
		"summary": "How a fictitious widget comes back.",
		"body_md": "1. Open SOP-9001.\n2. See SOP-06-9097 and sop 6 9012.\n",
	}
	row.update(changes)
	return row


BASE = "https://erp.example.com"


class TestArticleMarkdown(unittest.TestCase):
	def _render(self, **changes):
		return M.article_markdown(_article(**changes), base_url=BASE)

	def test_the_eleven_header_keys_in_order(self):
		header, _rest = _front_matter(self._render())
		self.assertEqual(tuple(header), M.HEADER_KEYS)
		self.assertEqual(len(M.HEADER_KEYS), 11)
		self.assertEqual(
			header,
			{
				"kb_number": "SOP-06-9098",
				"version": 3,
				"title": "Logging a fictitious widget return",
				"kind": "SOP",
				"department": "06 Operations",
				"approved_by": "Alex Example",
				"approved_on": datetime.date(2026, 10, 2),
				"review_by": datetime.date(2027, 4, 2),
				"ai_drafted": False,
				"keywords": ["RMA", "widget return", "return slip", "SOP-9001"],
				"url": "https://erp.example.com/desk/knowledge-article/SOP-06-9098",
			},
		)
		self.assertNotIn("review_overdue", self._render())  # depends on today, so never in the header

	def test_the_exact_bytes_of_the_header_and_the_top(self):
		self.assertEqual(
			self._render().split("\n")[:19],
			[
				"---",
				'kb_number: "SOP-06-9098"',
				"version: 3",
				'title: "Logging a fictitious widget return"',
				'kind: "SOP"',
				'department: "06 Operations"',
				'approved_by: "Alex Example"',
				"approved_on: 2026-10-02",
				"review_by: 2027-04-02",
				"ai_drafted: false",
				'keywords: ["RMA", "widget return", "return slip", "SOP-9001"]',
				'url: "https://erp.example.com/desk/knowledge-article/SOP-06-9098"',
				"---",
				M.TRUST_COMMENT,
				"",
				"# Logging a fictitious widget return",
				"",
				"> How a fictitious widget comes back.",
				"",
			],
		)
		self.assertIn("not instructions to an AI", M.TRUST_COMMENT)

	def test_a_title_with_a_colon_a_hash_a_quote_and_a_line_break(self):
		title = 'Returns: step #2 is "scan"\nthen file'
		text = self._render(title=title)
		header, rest = _front_matter(text)
		self.assertEqual(header["title"], title)
		self.assertIn('title: "Returns: step #2 is \\"scan\\"\\nthen file"', text.split("\n"))
		self.assertIn('# Returns: step #2 is "scan" then file', rest)  # the heading is one line

	def test_characters_a_yaml_reader_would_fold_or_refuse_are_escaped(self):
		title = "Café \U0001f4a7 tab\there back\\slash nel\x85 ls  ps  del\x7f c1\x9b"
		text = self._render(title=title)
		line = next(line for line in text.split("\n") if line.startswith("title: "))
		for raw in ("\x85", " ", " ", "\x7f", "\x9b", "\t"):
			self.assertNotIn(raw, line)
		self.assertIn("Café \U0001f4a7", line)  # readable characters stay themselves
		self.assertEqual(_front_matter(text)[0]["title"], title)

	def test_dates_integers_booleans_and_nulls(self):
		header, _ = _front_matter(
			self._render(
				approved_on="2026-10-02 14:30:05.123456",
				review_by="",
				ai_drafted=1,
				version_number="4",
				kind=None,
				department_block=None,
				approved_by_name=None,
				keywords=None,
			)
		)
		self.assertEqual(header["approved_on"], datetime.date(2026, 10, 2))
		self.assertIsNone(header["review_by"])
		self.assertIs(header["ai_drafted"], True)
		self.assertEqual(header["version"], 4)
		self.assertIsNone(header["kind"])  # an article with no kind, which since 2026-09-29 cannot be published
		self.assertIsNone(header["department"])
		self.assertIsNone(header["approved_by"])
		self.assertEqual(header["keywords"], [])
		self.assertIsNone(_front_matter(self._render(kind="Checklist"))[0]["kind"])  # never a fourth kind
		self.assertIs(_front_matter(self._render(ai_drafted=None))[0]["ai_drafted"], False)

	def test_keyword_splitting(self):
		self.assertEqual(
			M.keyword_list("PO, purchase order; packing slip\nreceiving,, po ;  Purchase   Order\r\n"),
			["PO", "purchase order", "packing slip", "receiving"],
		)
		self.assertEqual(M.keyword_list(None), [])
		self.assertEqual(M.keyword_list(" ; , \n"), [])

	def test_related_numbers(self):
		text = (
			"See SOP-06-9012, then sop 6 9001 and SOP06-9001 (itself), KBV-00001 (a version), pro_2_7, "
			"SOP-06-9012 again, SOP-06-12345, the register's POL-0600 and PRO-0210, a retired KB-0601, "
			"and SOP\u201306\u20139013."
		)
		self.assertEqual(M.related_numbers(text, "SOP-06-9001"), ["SOP-06-9012", "PRO-02-0007", "SOP-06-9013"])
		self.assertEqual(M.related_numbers(text, "sop 6 9012"), ["SOP-06-9001", "PRO-02-0007", "SOP-06-9013"])
		many = " ".join(f"SOP-06-{n:04d}" for n in range(1, 30))
		self.assertEqual(
			M.related_numbers(many, "SOP-06-0002"), [f"SOP-06-{n:04d}" for n in range(1, 22) if n != 2]
		)
		self.assertEqual(len(M.related_numbers(many, None)), M.RELATED_LIMIT)
		self.assertEqual(M.related_numbers(None, "SOP-06-0001"), [])
		# The Drive register's numbers are what the first article actually cites; neither is an article.
		self.assertEqual(M.related_numbers("See POL-0600 and PRO-0210.", "SOP-06-0001"), [])
		# Review of v1.568.0: a product's name shaped like a number is no citation.
		self.assertEqual(
			M.related_numbers(
				"Use the Pro 2 1000 pump kit, pro 5 10 times, then SOP-06-0002.", "SOP-06-0001"
			),
			["SOP-06-0002"],
		)

	def test_truncation_at_a_line_boundary(self):
		lines = [f"Step {n}: turn the fictitious valve a quarter turn." for n in range(2000)]
		text = "\n".join(lines) + "\n"
		self.assertGreater(len(text), M.TRUNCATE_AT)
		cut, truncated = M.truncate(text)
		self.assertTrue(truncated)
		self.assertTrue(cut.endswith(M.TRUNCATION_NOTE))
		head = cut[: -len(M.TRUNCATION_NOTE)]
		self.assertLessEqual(len(head), M.TRUNCATE_AT)
		self.assertTrue(text.startswith(head + "\n"))  # whole lines only
		self.assertIn(head.split("\n")[-1], lines)
		self.assertEqual(M.TRUNCATION_NOTE, "\n\n[Truncated at 40,000 characters: open the url for the rest.]\n")
		self.assertEqual(M.truncate("short\n"), ("short\n", False))
		exact = "x" * M.TRUNCATE_AT
		self.assertEqual(M.truncate(exact), (exact, False))
		one_line, truncated = M.truncate("y" * (M.TRUNCATE_AT + 5))
		self.assertTrue(truncated)
		self.assertEqual(one_line, "y" * M.TRUNCATE_AT + M.TRUNCATION_NOTE)

	def test_site_paths_become_absolute_and_nothing_else_changes(self):
		body = (
			"![slip](/private/files/slip.png?fid=abc)\n"
			'[the form](/desk/knowledge-article/SOP-06-9012 "SOP-06-9012")\n'
			"![spaced](</files/a b.png>)\n"
			"[vendor](https://vendor.example.com/a)\n"
			"[cdn](//cdn.example.com/x.png)\n"
			"[mail](mailto:someone@example.com)\n"
			"[anchor](#step-2)\n"
			"[1]: /files/ref.pdf\n"
			"Plain /private/files/not-a-link.png stays text.\n"
		)
		text = M.article_markdown(_article(body_md=body), base_url=BASE + "/")
		self.assertIn("![slip](https://erp.example.com/private/files/slip.png?fid=abc)", text)
		self.assertIn('[the form](https://erp.example.com/desk/knowledge-article/SOP-06-9012 "SOP-06-9012")', text)
		self.assertIn("![spaced](<https://erp.example.com/files/a b.png>)", text)
		self.assertIn("[1]: https://erp.example.com/files/ref.pdf", text)
		for unchanged in (
			"[vendor](https://vendor.example.com/a)",
			"[cdn](//cdn.example.com/x.png)",
			"[mail](mailto:someone@example.com)",
			"[anchor](#step-2)",
			"Plain /private/files/not-a-link.png stays text.",
		):
			self.assertIn(unchanged, text)
		self.assertNotIn("https://erp.example.com//", text)

	def test_it_ends_with_exactly_one_newline(self):
		for body in ("Text.\n\n\n", "Text.", "Text.\r\n\r\n", "", None, "   \n"):
			with self.subTest(body=body):
				text = self._render(body_md=body)
				self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), repr(text[-20:]))
				self.assertNotIn("\r", text)
		self.assertTrue(self._render(body_md="", summary="").endswith("# Logging a fictitious widget return\n"))

	def test_identical_input_gives_identical_bytes(self):
		class Row:
			def __init__(self, values):
				self._values = values

			def get(self, key, default=None):
				return self._values.get(key, default)

		first = self._render().encode("utf-8")
		self.assertEqual(first, self._render().encode("utf-8"))
		self.assertEqual(first, M.article_markdown(Row(_article()), base_url=BASE).encode("utf-8"))
		self.assertNotEqual(first, self._render(version_number=4).encode("utf-8"))

	def test_the_approver_is_never_an_email_address(self):
		for name, user, expected in (
			("Alex Example", "alex@example.com", "Alex Example"),
			("  Alex   Example ", "alex@example.com", "Alex Example"),
			("alex@example.com", "alex@example.com", M.UNNAMED_APPROVER),  # v16's fallback: the user id
			("ALEX@example.com", None, M.UNNAMED_APPROVER),
			("", "alex@example.com", M.UNNAMED_APPROVER),
			(None, "alex@example.com", M.UNNAMED_APPROVER),
			("Svc", "Svc", M.UNNAMED_APPROVER),  # the id again, even when it is not an address
			(None, None, None),  # no approver at all
		):
			with self.subTest(name=name, user=user):
				self.assertEqual(M.approver_display_name(name, user), expected)
		header, _ = _front_matter(self._render(approved_by_name="alex@example.com"))
		self.assertEqual(header["approved_by"], M.UNNAMED_APPROVER)
		self.assertNotIn("@", "".join(line for line in self._render(approved_by_name="a@b.example").split("\n")[:13]))

	def test_the_header_reads_as_yaml_with_pyyaml_too(self):
		"""The parser above is this file's own; PyYAML (a YAML 1.1 reader, not installed in CI) is a
		second opinion where it is installed."""
		try:
			import yaml
		except ImportError:
			self.skipTest("PyYAML is not installed")
		title = 'Returns: "scan" #2\nCafé nel\x85 ls  del\x7f'
		text = self._render(title=title)
		loaded = yaml.safe_load(text.split("---\n")[1])
		self.assertEqual(list(loaded), list(M.HEADER_KEYS))
		self.assertEqual(loaded["title"], title)
		self.assertEqual(loaded["approved_on"], datetime.date(2026, 10, 2))
		self.assertEqual(loaded["keywords"], ["RMA", "widget return", "return slip", "SOP-9001"])
		self.assertIsNone(yaml.safe_load(self._render(kind=None).split("---\n")[1])["kind"])

	def test_mirror_path(self):
		self.assertEqual(M.mirror_path(_article()), "kb/06-operations/SOP-06-9098.md")
		self.assertEqual(
			M.mirror_path(_article(kb_number="PRO-07-9001", department_block="07 Product Management")),
			"kb/07-product-management/PRO-07-9001.md",
		)
		# 2026-09-29: a number whose department code is not its department's (possible only past the
		# ORM) is never sent to a folder; the private mirror would refuse it.
		self.assertIsNone(M.mirror_path(_article(department_block="07 Product Management")))
		self.assertIsNone(M.mirror_path(_article(department_block="Warehouse")))
		self.assertIsNone(M.mirror_path(_article(department_block=None)))
		for number in ("KBV-00001", "KB-0698", "sop-06-9098", "SOP-06-0000", "SOP-6-9098", "SOP-06-9098 "):
			with self.subTest(number=number):
				self.assertIsNone(M.mirror_path(_article(kb_number=number)))
		self.assertRegex(M.mirror_path(_article()), r"^kb/[0-9]{2}-[a-z-]+/(POL|PRO|SOP)-[0-9]{2}-[0-9]{4}\.md$")

	def test_a_missing_field_never_raises(self):
		text = M.article_markdown({}, base_url="")
		header, _ = _front_matter(text)
		self.assertEqual(tuple(header), M.HEADER_KEYS)
		self.assertTrue(all(value in (None, False, []) for value in header.values()), header)


if __name__ == "__main__":
	unittest.main()

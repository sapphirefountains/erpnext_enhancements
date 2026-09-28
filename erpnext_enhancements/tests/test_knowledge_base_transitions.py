# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's state machine and who may make each move, as pure rules (WI-080 PR 3).

``knowledge_base/workflow.py`` and ``content.py`` import no frappe (``test_knowledge_base_rules``
checks that in a fresh interpreter), so this suite needs no stub. What it pins:

* **The state machine is exactly** ``workflow.TRANSITIONS``: every (action, state) pair is either
  in the table and allowed, or refused with a sentence naming the state. Superseded and Discarded
  are final; Published only ever becomes Superseded.
* **Who may make each move**, rule by rule, each alone refusing and named: submit (a KB role, a
  Draft with a title, a department and some text, no secret, an article that is not retired and
  keeps its department), withdraw (the author's side), request changes (a KB Approver with no hand
  in it, a named person, from a browser, not an AI gate card), discard (the author's side or a KB
  Approver, Draft only), start a revision (a KB role, a published article), retire (a KB Approver,
  named, from a browser, not an AI card, nothing open), confirm (the process owner or a KB
  Approver, named, from a browser, not an AI card), and reading the diff (a KB role, a browser,
  not an AI card).
* **The buttons are the rules**: ``version_actions`` and ``article_actions`` offer an action
  exactly when its ``*_problems`` has nothing to say.
* **Who is asked to review**: every KB Approver candidate except Administrator, Guest and anyone
  with a hand in the version.
* **What publishing reads**: ``shows_anything`` (Quill's empty editor and an empty list show
  nothing; a picture does), ``referenced_files`` (``?fid=`` and plain ``/private/files/`` paths in
  ``src`` and ``href``, any host, never a public path) and ``text_diff``.
* **Decision (c): an example key.** v1 has no override for the secret scan, so an article that
  must show where a key goes uses a placeholder the scan accepts. The one the README shows is
  held here and must pass, inside a body as the editor stores it, while a real key shape fails.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_transitions -v
"""

import datetime
import itertools
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

README = APP / "knowledge_base" / "README.md"

AUTHOR = "parker@example.com"
APPROVER = "james@example.com"
OTHER = "lisa@example.com"
MODIFIED = datetime.datetime(2026, 9, 28, 9, 30, 0, 250000)
BODY = '<div class="ql-editor read-mode"><p>Scan the packing slip.</p></div>'

#: Built by concatenation, never a literal (push protection refused one once).
STRIPE_KEY = "sk" + "_live_" + "a1B2" * 6

#: The example placeholder the README shows (decision (c)). Held verbatim; the README must carry
#: it word for word, and the scan must accept it.
EXAMPLE_PLACEHOLDERS = (
	"STRIPE_SECRET_KEY=sk_live_<secret key from 1Password>",
	"Authorization: Bearer <token from 1Password>",
)

BROWSER = {"user_type": K.APPROVER_USER_TYPE, "browser": True, "gate_flags": {}}


def _version(**values):
	version = {
		"name": "KBV-00012",
		"review_state": W.DRAFT,
		"title": "Receiving a PO",
		"department_block": "06 Operations",
		"body": BODY,
		"owner": AUTHOR,
		"submitted_by": None,
		"contributors": AUTHOR,
		"ai_requested_by": None,
		"modified": MODIFIED,
	}
	version.update(values)
	return version


def _article(**values):
	article = {
		"name": "KB-0612",
		"status": "Published",
		"department_block": "06 Operations",
		"process_owner": OTHER,
		"live_version": "KBV-00007",
	}
	article.update(values)
	return article


# ------------------------------------------------------------------ the state machine


class TestTheStateMachine(unittest.TestCase):
	def test_the_table_is_exactly_the_lifecycle(self):
		self.assertEqual(
			W.TRANSITIONS,
			{
				"submit_for_review": (("Draft",), "In Review"),
				"withdraw": (("In Review",), "Draft"),
				"request_changes": (("In Review",), "Draft"),
				"approve_and_publish": (("In Review",), "Published"),
				"discard": (("Draft",), "Discarded"),
				"supersede": (("Published",), "Superseded"),
			},
		)

	def test_every_state_in_the_table_is_a_real_state(self):
		for action, (sources, target) in W.TRANSITIONS.items():
			with self.subTest(action=action):
				self.assertIn(target, K.REVIEW_STATES)
				self.assertTrue(set(sources) <= set(K.REVIEW_STATES))
				self.assertIn(action, W.VERBS)

	def test_every_action_and_state_is_either_allowed_or_refused_by_name(self):
		for action, state in itertools.product(W.TRANSITIONS, K.REVIEW_STATES):
			with self.subTest(action=action, state=state):
				sources, target = W.TRANSITIONS[action]
				problem = W.transition_problem(action, state)
				if state in sources:
					self.assertIsNone(problem)
					self.assertEqual(W.next_state(action, state), target)
				else:
					self.assertIn(f"it is {state}", problem)
					with self.assertRaises(ValueError):
						W.next_state(action, state)

	def test_superseded_and_discarded_are_final_and_published_only_becomes_superseded(self):
		leaving = {state: [a for a, (sources, _t) in W.TRANSITIONS.items() if state in sources] for state in K.REVIEW_STATES}
		self.assertEqual(leaving["Superseded"], [])
		self.assertEqual(leaving["Discarded"], [])
		self.assertEqual(leaving["Published"], ["supersede"])
		self.assertEqual(sorted(leaving["In Review"]), ["approve_and_publish", "request_changes", "withdraw"])
		self.assertEqual(sorted(leaving["Draft"]), ["discard", "submit_for_review"])

	def test_a_row_with_no_state_is_a_draft(self):
		self.assertEqual(W.state_of({"review_state": None}), "Draft")
		self.assertEqual(W.state_of({}), "Draft")
		self.assertIsNone(W.transition_problem("submit_for_review", None))

	def test_an_action_that_is_not_a_move_is_refused(self):
		for action in ("start_revision", "retire", "review_diff", "publish", ""):
			with self.subTest(action=action):
				self.assertIn("does not move a version", W.transition_problem(action, "Draft"))

	def test_the_action_names_are_the_endpoint_names(self):
		source = (APP / "api" / "knowledge_base.py").read_text(encoding="utf-8")
		for action in (*W.VERSION_ACTIONS, *W.ARTICLE_ACTIONS):
			with self.subTest(action=action):
				self.assertIn(f"def {action}(", source)


# ------------------------------------------------------------------ submit


class TestSubmit(unittest.TestCase):
	def test_a_kb_author_submits_a_complete_draft(self):
		for roles in ((K.AUTHOR_ROLE,), (K.APPROVER_ROLE,)):
			with self.subTest(roles=roles):
				self.assertEqual(W.submit_problems(_version(), AUTHOR, roles), [])

	def test_anyone_with_a_kb_role_may_submit_anyones_draft(self):
		self.assertEqual(W.submit_problems(_version(), OTHER, (K.AUTHOR_ROLE,)), [])

	def test_no_kb_role(self):
		problems = W.submit_problems(_version(), AUTHOR, ("Desk User",))
		self.assertEqual(len(problems), 1)
		self.assertIn("KB Author or KB Approver", problems[0])

	def test_only_a_draft(self):
		for state in ("In Review", "Published", "Superseded", "Discarded"):
			with self.subTest(state=state):
				problems = W.submit_problems(_version(review_state=state), AUTHOR, (K.AUTHOR_ROLE,))
				self.assertEqual(len(problems), 1)
				self.assertIn(f"it is {state}", problems[0])

	def test_a_draft_must_have_a_title_a_department_and_some_text(self):
		cases = {
			"it has no title": {"title": "  "},
			"it has no department": {"department_block": ""},
			"it has no text": {"body": '<div class="ql-editor read-mode"><p><br></p></div>'},
		}
		for phrase, values in cases.items():
			with self.subTest(phrase):
				self.assertEqual(W.submit_problems(_version(**values), AUTHOR, (K.AUTHOR_ROLE,)), [phrase])

	def test_a_department_that_is_not_an_option_is_no_department(self):
		self.assertEqual(
			W.submit_problems(_version(department_block="06"), AUTHOR, (K.AUTHOR_ROLE,)), ["it has no department"]
		)

	def test_a_secret_found_since_the_save_is_named_without_its_value(self):
		found = [("body", C.Finding(3, "a Stripe secret key"))]
		problems = W.submit_problems(_version(), AUTHOR, (K.AUTHOR_ROLE,), secrets=found)
		self.assertEqual(len(problems), 1)
		self.assertIn("Body line 3 looks like a Stripe secret key", problems[0])
		self.assertNotIn(STRIPE_KEY, problems[0])

	def test_a_revision_of_a_retired_article_or_in_another_department(self):
		problems = W.submit_problems(
			_version(department_block="03 Finance"),
			AUTHOR,
			(K.AUTHOR_ROLE,),
			article=_article(status="Retired"),
		)
		self.assertEqual(len(problems), 2)
		self.assertIn("KB-0612 is Retired", problems[0])
		self.assertIn("an article keeps its department", problems[1])
		self.assertIn("06 Operations", problems[1])


# ------------------------------------------------------------------ withdraw


class TestWithdraw(unittest.TestCase):
	def test_the_authors_side_withdraws(self):
		cases = {
			"creator": _version(review_state="In Review", owner=AUTHOR, contributors=""),
			"submitter": _version(review_state="In Review", owner=OTHER, submitted_by=AUTHOR, contributors=""),
			"contributor": _version(review_state="In Review", owner=OTHER, contributors=f"{OTHER}\n{AUTHOR}"),
		}
		for label, version in cases.items():
			with self.subTest(label):
				self.assertEqual(W.withdraw_problems(version, AUTHOR, (K.AUTHOR_ROLE,)), [])

	def test_an_approver_with_no_hand_in_it_requests_changes_instead(self):
		problems = W.withdraw_problems(_version(review_state="In Review"), APPROVER, (K.APPROVER_ROLE,))
		self.assertEqual(len(problems), 1)
		self.assertIn("Request Changes", problems[0])

	def test_only_in_review(self):
		for state in ("Draft", "Published", "Superseded", "Discarded"):
			with self.subTest(state=state):
				problems = W.withdraw_problems(_version(review_state=state), AUTHOR, (K.AUTHOR_ROLE,))
				self.assertEqual(len(problems), 1)
				self.assertIn(f"it is {state}", problems[0])

	def test_no_kb_role(self):
		problems = W.withdraw_problems(_version(review_state="In Review"), AUTHOR, ())
		self.assertEqual(len(problems), 1)


# ------------------------------------------------------------------ request changes


class TestRequestChanges(unittest.TestCase):
	def _ask(self, version=None, user=APPROVER, roles=(K.APPROVER_ROLE,), **context):
		kwargs = dict(BROWSER, **context)
		return W.request_changes_problems(
			version if version is not None else _version(review_state="In Review", submitted_by=AUTHOR),
			user,
			roles,
			**kwargs,
		)

	def test_an_independent_approver_in_a_browser_sends_it_back(self):
		self.assertEqual(self._ask(), [])

	def test_each_rule_alone_refuses(self):
		cases = {
			"only a KB Approver can send back": {"roles": (K.AUTHOR_ROLE,)},
			"API keys, tokens": {"browser": False},
			"an AI assistant's action cannot send back": {"gate_flags": {"ai_gate_bypass": True}},
			"only a System User (a staff login) can send back": {"user_type": "Website User"},
			"Administrator is a shared account": {"user": "Administrator"},
			"it is Draft": {"version": _version(review_state="Draft")},
			"so a different KB Approver must review it": {
				"version": _version(review_state="In Review", contributors=f"{AUTHOR}\n{APPROVER}")
			},
		}
		for phrase, override in cases.items():
			with self.subTest(phrase):
				problems = self._ask(**override)
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(phrase, problems[0])

	def test_the_ai_requester_cannot_review_it_either(self):
		problems = self._ask(_version(review_state="In Review", ai_requested_by=APPROVER))
		self.assertIn("asked an AI to draft it", problems[0])


# ------------------------------------------------------------------ discard


class TestDiscard(unittest.TestCase):
	def test_the_authors_side_or_an_approver_discards_a_draft(self):
		self.assertEqual(W.discard_problems(_version(), AUTHOR, (K.AUTHOR_ROLE,)), [])
		self.assertEqual(W.discard_problems(_version(), APPROVER, (K.APPROVER_ROLE,)), [])

	def test_another_author_cannot(self):
		problems = W.discard_problems(_version(), OTHER, (K.AUTHOR_ROLE,))
		self.assertEqual(len(problems), 1)
		self.assertIn("can discard it", problems[0])

	def test_only_a_draft(self):
		for state in ("In Review", "Published", "Superseded", "Discarded"):
			with self.subTest(state=state):
				problems = W.discard_problems(_version(review_state=state), AUTHOR, (K.AUTHOR_ROLE,))
				self.assertEqual(len(problems), 1)
				self.assertIn(f"it is {state}", problems[0])


# ------------------------------------------------------------------ publishing into an article


class TestPublishProblems(unittest.TestCase):
	def test_a_first_version_has_no_article_to_check(self):
		self.assertEqual(W.publish_problems(_version(), None), [])

	def test_a_published_article_in_the_same_department(self):
		self.assertEqual(W.publish_problems(_version(), _article()), [])

	def test_a_retired_article_takes_no_new_version(self):
		self.assertIn("KB-0612 is Retired", W.publish_problems(_version(), _article(status="Retired"))[0])

	def test_an_article_keeps_its_department(self):
		problem = W.publish_problems(_version(department_block="03 Finance"), _article())[0]
		self.assertIn("an article keeps its department: KB-0612 is numbered in 06 Operations", problem)


# ------------------------------------------------------------------ articles


class TestStartRevision(unittest.TestCase):
	def test_a_kb_role_revises_a_published_article(self):
		for roles in ((K.AUTHOR_ROLE,), (K.APPROVER_ROLE,)):
			with self.subTest(roles=roles):
				self.assertEqual(W.start_revision_problems(_article(), roles), [])

	def test_refused(self):
		self.assertEqual(len(W.start_revision_problems(_article(), ("Desk User",))), 1)
		self.assertEqual(W.start_revision_problems(_article(status="Retired"), (K.AUTHOR_ROLE,)), ["it is Retired"])
		self.assertIn("no published article", W.start_revision_problems(None, (K.AUTHOR_ROLE,))[0])


class TestRetire(unittest.TestCase):
	def _ask(self, article=None, user=APPROVER, roles=(K.APPROVER_ROLE,), open_version=None, **context):
		return W.retire_problems(
			article if article is not None else _article(),
			user,
			roles,
			open_version=open_version,
			**dict(BROWSER, **context),
		)

	def test_a_named_approver_in_a_browser_retires_it(self):
		self.assertEqual(self._ask(), [])

	def test_it_need_not_be_a_second_person(self):
		"""Stopping is never the hard direction: the approver who published it may retire it."""
		self.assertEqual(self._ask(_article(approved_by=APPROVER, author=APPROVER)), [])

	def test_each_rule_alone_refuses(self):
		cases = {
			"only a KB Approver can retire": {"roles": (K.AUTHOR_ROLE,)},
			"cannot retire one": {"browser": False},
			"an AI assistant's action cannot retire": {"gate_flags": {"ai_gate_pending": "AIPA-1"}},
			"Administrator is a shared account": {"user": "Administrator"},
			"it is already Retired": {"article": _article(status="Retired")},
			"KBV-00020 is still open on it": {"open_version": "KBV-00020"},
		}
		for phrase, override in cases.items():
			with self.subTest(phrase):
				problems = self._ask(**override)
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(phrase, problems[0])


class TestConfirm(unittest.TestCase):
	def _ask(self, user=OTHER, roles=("Desk User",), article=None, **context):
		return W.confirm_problems(
			article if article is not None else _article(), user, roles, **dict(BROWSER, **context)
		)

	def test_the_process_owner_confirms_without_a_kb_role(self):
		self.assertEqual(self._ask(), [])

	def test_a_kb_approver_confirms(self):
		self.assertEqual(self._ask(user=APPROVER, roles=(K.APPROVER_ROLE,)), [])

	def test_each_rule_alone_refuses(self):
		cases = {
			"only its process owner or a KB Approver": {"user": AUTHOR, "roles": (K.AUTHOR_ROLE,)},
			"cannot confirm one": {"browser": False},
			"an AI assistant's action cannot confirm": {"gate_flags": {"ai_gate_bypass": True}},
			"only a System User (a staff login) can confirm": {"user_type": "Website User"},
			"it is Retired": {"article": _article(status="Retired")},
		}
		for phrase, override in cases.items():
			with self.subTest(phrase):
				problems = self._ask(**override)
				self.assertEqual(len(problems), 1, problems)
				self.assertIn(phrase, problems[0])

	def test_administrator_never_confirms(self):
		problems = self._ask(user="Administrator", roles=(K.APPROVER_ROLE, "System Manager"))
		self.assertEqual(len(problems), 1)
		self.assertIn("named KB Approver must confirm it", problems[0])


class TestReviewDiffRules(unittest.TestCase):
	def test_a_kb_role_in_a_browser_reads_the_diff(self):
		self.assertEqual(W.review_diff_problems((K.AUTHOR_ROLE,), browser=True, gate_flags={}), [])

	def test_a_token_an_ai_card_or_a_reader_cannot(self):
		self.assertEqual(len(W.review_diff_problems((K.AUTHOR_ROLE,), browser=False, gate_flags={})), 1)
		self.assertEqual(
			len(W.review_diff_problems((K.AUTHOR_ROLE,), browser=True, gate_flags={"ai_gate_bypass": True})), 1
		)
		self.assertEqual(len(W.review_diff_problems(("System Manager", "Desk User"), browser=True, gate_flags={})), 1)


class TestRequiredText(unittest.TestCase):
	def test_blank_is_the_message(self):
		for value in (None, "", "   \n"):
			with self.subTest(value=value):
				self.assertEqual(W.required_text_problem(value, "say why"), "say why")
		self.assertIsNone(W.required_text_problem("Replaced by KB-0613.", "say why"))


# ------------------------------------------------------------------ the buttons are the rules


class TestTheButtonsAreTheRules(unittest.TestCase):
	def _actions(self, version, user, roles, **context):
		return W.version_actions(version, user, roles, **dict(BROWSER, **context))

	def test_the_author_of_a_draft(self):
		self.assertEqual(
			self._actions(_version(), AUTHOR, (K.AUTHOR_ROLE,)), ("submit_for_review", "discard", "review_diff")
		)

	def test_an_independent_approver_on_a_version_in_review(self):
		version = _version(review_state="In Review", submitted_by=AUTHOR)
		self.assertEqual(
			self._actions(version, APPROVER, (K.APPROVER_ROLE,)),
			("approve_and_publish", "request_changes", "review_diff"),
		)

	def test_the_author_of_a_version_in_review(self):
		version = _version(review_state="In Review", submitted_by=AUTHOR)
		self.assertEqual(self._actions(version, AUTHOR, (K.AUTHOR_ROLE,)), ("withdraw", "review_diff"))

	def test_an_approver_who_contributed(self):
		version = _version(review_state="In Review", submitted_by=AUTHOR, contributors=f"{AUTHOR}\n{APPROVER}")
		self.assertEqual(self._actions(version, APPROVER, (K.APPROVER_ROLE,)), ("withdraw", "review_diff"))

	def test_history_offers_only_the_diff(self):
		for state in ("Published", "Superseded", "Discarded"):
			with self.subTest(state=state):
				self.assertEqual(
					self._actions(_version(review_state=state), APPROVER, (K.APPROVER_ROLE,)), ("review_diff",)
				)

	def test_a_token_offers_nothing_on_the_approval_path(self):
		version = _version(review_state="In Review", submitted_by=AUTHOR)
		self.assertEqual(self._actions(version, APPROVER, (K.APPROVER_ROLE,), browser=False), ())

	def test_every_offered_action_has_no_problems_and_every_other_has_one(self):
		"""Exhaustively, over states, people and roles: offered exactly when the rule is silent."""
		people = {AUTHOR: (K.AUTHOR_ROLE,), APPROVER: (K.APPROVER_ROLE,), OTHER: ("Desk User",)}
		for state in K.REVIEW_STATES:
			for user, roles in people.items():
				version = _version(review_state=state, submitted_by=AUTHOR)
				offered = set(self._actions(version, user, roles))
				rules = {
					"submit_for_review": W.submit_problems(version, user, roles),
					"approve_and_publish": W.approval_problems(
						version, user, roles, opened_modified=MODIFIED, **BROWSER
					),
					"request_changes": W.request_changes_problems(version, user, roles, **BROWSER),
					"withdraw": W.withdraw_problems(version, user, roles),
					"discard": W.discard_problems(version, user, roles),
					"review_diff": W.review_diff_problems(roles, browser=True, gate_flags={}),
				}
				for action, problems in rules.items():
					with self.subTest(state=state, user=user, action=action):
						self.assertEqual(action in offered, not problems)

	def test_the_article_buttons(self):
		article = _article()
		ctx = BROWSER
		self.assertEqual(
			W.article_actions(article, APPROVER, (K.APPROVER_ROLE,), **ctx),
			("start_revision", "confirm_still_accurate", "retire"),
		)
		self.assertEqual(W.article_actions(article, AUTHOR, (K.AUTHOR_ROLE,), **ctx), ("start_revision",))
		self.assertEqual(W.article_actions(article, OTHER, ("Desk User",), **ctx), ("confirm_still_accurate",))
		self.assertEqual(W.article_actions(article, "tech@example.com", ("Desk User",), **ctx), ())
		self.assertEqual(
			W.article_actions(article, APPROVER, (K.APPROVER_ROLE,), open_version="KBV-00020", **ctx),
			("start_revision", "confirm_still_accurate"),
		)
		self.assertEqual(W.article_actions(_article(status="Retired"), APPROVER, (K.APPROVER_ROLE,), **ctx), ())


# ------------------------------------------------------------------ who is asked


class TestReviewersFor(unittest.TestCase):
	def test_everyone_with_a_hand_in_it_and_the_never_approvers_are_left_out(self):
		version = _version(
			owner=AUTHOR,
			submitted_by="sub@example.com",
			contributors=f"{AUTHOR}\ncontrib@example.com",
			ai_requested_by="ai@example.com",
		)
		candidates = [
			"Administrator",
			AUTHOR,
			"SUB@example.com",
			"contrib@example.com",
			"ai@example.com",
			APPROVER,
			" james@example.com ",
			"Guest",
			OTHER,
		]
		self.assertEqual(W.reviewers_for(version, candidates), [APPROVER, OTHER])

	def test_nobody_left(self):
		self.assertEqual(W.reviewers_for(_version(owner=APPROVER), [APPROVER]), [])
		self.assertEqual(W.reviewers_for(_version(), None), [])


# ------------------------------------------------------------------ what publishing reads


class TestShowsAnything(unittest.TestCase):
	def test_nothing(self):
		for body in (
			None,
			"",
			"   ",
			'<div class="ql-editor read-mode"><p><br></p></div>',
			'<ol><li data-list="bullet"><span class="ql-ui" contenteditable="false"></span></li></ol>',
			"<p><!-- a note --></p><script>x()</script><style>p{}</style>",
			'<p><img src=""></p>',
		):
			with self.subTest(body=body):
				self.assertFalse(C.shows_anything(body))

	def test_something(self):
		for body in (
			BODY,
			"plain words",
			'<p><img src="/private/files/slip.png?fid=abc123"></p>',
			"<table><tr><td>1</td></tr></table>",
			"<p>&nbsp;x</p>",
		):
			with self.subTest(body=body):
				self.assertTrue(C.shows_anything(body))


class TestReferencedFiles(unittest.TestCase):
	def test_fids_and_private_paths_in_src_and_href(self):
		body = (
			'<p><img src="/private/files/slip.png?fid=a1b2c3"></p>'
			'<p><img src="https://erp.sapphirefountains.com/private/files/valve%20B.png?fid=d4e5f6&amp;x=1"></p>'
			'<p><a href="/private/files/manual.pdf">the manual</a></p>'
			'<p><img src="/files/public.png"></p>'
			'<p><img src="data:image/png;base64,AAAA"></p>'
			'<p><a href="https://drive.google.com/file/d/xyz/view">Drive</a></p>'
		)
		fids, paths = C.referenced_files(body)
		self.assertEqual(fids, {"a1b2c3", "d4e5f6"})
		self.assertEqual(
			paths, {"/private/files/slip.png", "/private/files/valve B.png", "/private/files/manual.pdf"}
		)

	def test_nothing_referenced(self):
		for body in (None, "", "plain", "<p>no files</p>"):
			with self.subTest(body=body):
				self.assertEqual(C.referenced_files(body), (frozenset(), frozenset()))

	def test_an_fid_in_text_is_not_a_reference(self):
		self.assertEqual(C.referenced_files("<p>see /private/files/x.png?fid=zz</p>"), (frozenset(), frozenset()))


class TestTextDiff(unittest.TestCase):
	def test_identical_is_empty(self):
		self.assertEqual(C.text_diff("a\nb", "a\nb"), [])
		self.assertEqual(C.text_diff(None, ""), [])

	def test_changes_are_marked_and_the_header_is_dropped(self):
		lines = C.text_diff("Scan the slip.\nCount the boxes.", "Scan the slip.\nCount every box.\nSign it.")
		self.assertTrue(lines[0].startswith("@@"))
		self.assertIn("-Count the boxes.", lines)
		self.assertIn("+Count every box.", lines)
		self.assertIn("+Sign it.", lines)
		self.assertIn(" Scan the slip.", lines)
		self.assertFalse(any(line.startswith(("---", "+++")) for line in lines))

	def test_a_first_version_is_all_added(self):
		self.assertEqual(C.text_diff("", "one\ntwo"), ["@@ -0,0 +1,2 @@", "+one", "+two"])


# ------------------------------------------------------------------ decision (c): example keys


class TestExampleKeys(unittest.TestCase):
	"""v1 has no override for the secret scan. An article that shows where a key goes uses a
	placeholder the scan accepts; the README shows these, and they must keep passing."""

	def test_the_readme_shows_the_placeholders_word_for_word(self):
		text = README.read_text(encoding="utf-8")
		for placeholder in EXAMPLE_PLACEHOLDERS:
			with self.subTest(placeholder=placeholder):
				self.assertIn(placeholder, text)

	def test_the_placeholders_pass_the_scan_as_typed_and_as_the_editor_stores_them(self):
		for placeholder in EXAMPLE_PLACEHOLDERS:
			stored = "<p>" + placeholder.replace("<", "&lt;").replace(">", "&gt;") + "</p>"
			with self.subTest(placeholder=placeholder):
				self.assertEqual(C.secret_findings(placeholder), [])
				self.assertEqual(C.secret_findings(stored, html=True), [])

	def test_a_real_key_shape_in_the_same_place_is_still_refused(self):
		self.assertEqual(
			[f.kind for f in C.secret_findings("STRIPE_SECRET_KEY=" + STRIPE_KEY)], ["a Stripe secret key"]
		)
		fake_but_key_shaped = "sk" + "_live_" + "0" * 16
		self.assertEqual(len(C.secret_findings(fake_but_key_shaped)), 1)


if __name__ == "__main__":
	unittest.main()

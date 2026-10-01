# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who may do what to a Knowledge Base version or article, and the numbers and dates publishing
writes (WI-080 PRs 2 and 3).

ADR 0017 section 2: **a version is published only by a KB Approver who did not write it, from a
signed-in browser, exactly as they opened it.** The approver is a named person with a staff login:
never Administrator, which holds every role, and never Guest. This module is the rule itself, as
functions of plain values, so the bench-free CI tier tests every branch. The controller
(``knowledge_base/doctype/knowledge_article_version``) applies it in ``before_submit`` and again in
``on_submit``, and ``api/knowledge_base.approve_and_publish`` asks it before it writes anything.

Standard library only, apart from ``signed_in_browser``, which comes from
``marketing/publish/workflow.py`` so that Marketing and the Knowledge Base share one definition of
"a person's login, not a token or a job". That module imports no frappe. Its ``approval_problems``
is deliberately **not** reused: it hard-codes Marketing Manager and knows nothing of contributors
or AI requesters.

The lifecycle (``constants.REVIEW_STATES``), and the action that makes each move (:data:`TRANSITIONS`,
PR 3)::

    Draft --submit_for_review--> In Review --approve_and_publish--> Published --supersede--> Superseded
      ^                              |
      +--withdraw / request_changes--+
    Draft --discard--> Discarded

``TRANSITIONS`` is the whole state machine. ``knowledge_base/publish.transition`` is the only code
that writes ``review_state``, and it refuses any move that is not in the table. Who may make each
move is a ``*_problems`` function below; each returns every broken rule, in words, and an empty
list means the move is allowed. The same functions decide which buttons the form shows
(:func:`version_actions`, :func:`article_actions`), so a button is never offered for a move the
server would refuse, and never hidden from someone the server would let through.

Content (``constants.VERSION_CONTENT_FIELDS``) changes only in Draft. Anyone who saves a change to
it is recorded in ``contributors``, one user per line, and may not approve that version.
"""

import calendar
import datetime
import re

from erpnext_enhancements.knowledge_base import constants
from erpnext_enhancements.knowledge_base.content import (
	FIELD_LABELS,
	guidance_left,
	shows_anything,
	strip_presentation,
)
from erpnext_enhancements.marketing.publish.workflow import signed_in_browser

__all__ = [
	"APPROVE_AND_PUBLISH",
	"ARTICLE_ACTIONS",
	"CONFIRM_STILL_ACCURATE",
	"DISCARD",
	"REQUEST_CHANGES",
	"RETIRE",
	"REVIEW_DIFF",
	"START_REVISION",
	"SUBMIT_FOR_REVIEW",
	"SUPERSEDE",
	"TRANSITIONS",
	"VERBS",
	"VERSION_ACTIONS",
	"WITHDRAW",
	"SequenceFullError",
	"approval_problems",
	"article_actions",
	"changed_content_fields",
	"confirm_problems",
	"content_edit_problem",
	"contributors",
	"discard_problems",
	"holds_kb_role",
	"identity_problem",
	"next_article_number",
	"next_state",
	"number_problems",
	"number_scope",
	"publish_problems",
	"refusal",
	"request_changes_problems",
	"required_text_problem",
	"retire_problems",
	"review_by",
	"review_diff_problems",
	"review_interval",
	"reviewers_for",
	"secret_problem",
	"signed_in_browser",
	"start_revision_problems",
	"state_of",
	"submit_problems",
	"transition_problem",
	"version_actions",
	"with_contributor",
	"withdraw_problems",
]

# The states by name, unpacked from the one definition. If constants.REVIEW_STATES ever gains or
# loses a state this line raises at import, which is the point: every rule below names states.
DRAFT, IN_REVIEW, PUBLISHED, SUPERSEDED, DISCARDED = constants.REVIEW_STATES

#: A contributors field may have been typed with commas or spaces by an earlier tool; every one of
#: those separates user ids, none of which can contain them.
_CONTRIBUTOR_SEPARATORS = re.compile(r"[\s,;]+")

#: The two accounts that never approve, compared as :func:`_person` compares user ids. Unpacked for
#: the same reason as the states above: each gets its own sentence in the refusal.
_ADMINISTRATOR, _GUEST = (name.casefold() for name in constants.NEVER_APPROVERS)


class SequenceFullError(ValueError):
	"""A ``(prefix, department)`` scope has used ``0001`` to ``9999``. Publishing shows the message as
	it stands."""


# ------------------------------------------------------------------ approval


def approval_problems(version, user, roles, *, user_type, browser, gate_flags, opened_modified):
	"""Why ``user`` cannot approve ``version``; an empty list means they can.

	``version`` is the version **as stored** (the controller passes ``get_doc_before_save()``),
	never the copy in memory, which the code calling ``submit()`` could have changed; ``None``
	means there is nothing stored to approve. ``roles`` are the approver's roles. ``user_type`` is
	the approver's ``User.user_type`` as stored (``frappe.db.get_value("User", user,
	"user_type")``); only ``constants.APPROVER_USER_TYPE`` approves, and ``None`` (no User row) is
	refused. It has no default, so a caller that forgets it fails loudly rather than approving.
	``browser`` is :func:`signed_in_browser` for this request. ``gate_flags`` is ``frappe.flags``
	(anything with ``.get`` or attributes). ``opened_modified`` is the ``modified`` value of the
	copy the approver had open, as the page sent it.

	Approvers are named people: Administrator and Guest (``constants.NEVER_APPROVERS``) are
	refused by name, whatever roles they hold, and so is any account that is not a System User.

	Every rule is checked and every broken one is reported, so the refusal names each of them.
	"""
	problems = _reviewer_problems(
		user, roles, user_type=user_type, browser=browser, gate_flags=gate_flags, verb="approve"
	)
	if version is None:
		problems.append("there is no saved version to approve")
		return problems

	state = state_of(version)
	if state != IN_REVIEW:
		problems.append(f"it is {state}, not {IN_REVIEW}")

	did = _hands_in(version, _person(user))
	if did:
		problems.append(f"you {_and(did)}, so a different {constants.APPROVER_ROLE} must approve it")

	if not _same_moment(_get(version, "modified"), opened_modified):
		problems.append("it changed after you opened it, so reload it and review it again")
	return problems


def _reviewer_problems(user, roles, *, user_type, browser, gate_flags, verb):
	"""The rules on the person, whatever the version: a KB Approver who is a named person with a
	staff login, signed in to a browser, not acting through an AI gate card. ``verb`` completes
	"only a KB Approver can ... a version" ("approve", "send back", "retire")."""
	problems = []
	if constants.APPROVER_ROLE not in set(roles or ()):
		problems.append(f"only a {constants.APPROVER_ROLE} can {verb} a version")
	problem = _approver_account_problem(user, _person(user), user_type, verb=verb)
	if problem:
		problems.append(problem)
	if not browser:
		problems.append(_NOT_A_BROWSER[verb])
	if _gate_action(gate_flags):
		problems.append(f"an AI assistant's action cannot {verb} a version, even once it is confirmed")
	return problems


#: Why a request that is not a person's browser session is refused, per action. The approval
#: sentence is PR 2's, word for word.
_NOT_A_BROWSER = {
	"approve": (
		"approvals are made by a person signed in to ERPNext in a browser; API keys, tokens, "
		"background jobs and the console cannot approve"
	),
	"send back": (
		"a version is sent back by a person signed in to ERPNext in a browser; API keys, tokens, "
		"background jobs and the console cannot send one back"
	),
	"retire": (
		"an article is retired by a person signed in to ERPNext in a browser; API keys, tokens, "
		"background jobs and the console cannot retire one"
	),
	"confirm": (
		"an article is confirmed by a person signed in to ERPNext in a browser; API keys, tokens, "
		"background jobs and the console cannot confirm one"
	),
	"compare": (
		"a draft is shown only to a person signed in to ERPNext in a browser; API keys, tokens, "
		"background jobs and the console cannot read one"
	),
}


def _hands_in(version, me):
	"""What ``me`` did to ``version`` that stops them approving or reviewing it, as phrases."""
	if not me or me == _GUEST:
		return []
	did = []
	if me == _person(_get(version, "owner")):
		did.append("created it")
	if me == _person(_get(version, "submitted_by")):
		did.append("submitted it for review")
	if me in {_person(name) for name in contributors(_get(version, "contributors"))}:
		did.append("changed its content")
	if me == _person(_get(version, "ai_requested_by")):
		did.append("asked an AI to draft it")
	return did


def _approver_account_problem(user, me, user_type, verb="approve"):
	"""Why this account is not a named person who may approve (or ``verb``); ``None`` if it is."""
	if not me or me == _GUEST:
		return "nobody is signed in"
	if me == _ADMINISTRATOR:
		return (
			"Administrator is a shared account, not a person, and only grants or revokes KB roles, "
			f"so a named {constants.APPROVER_ROLE} must {verb} it"
		)
	if user_type != constants.APPROVER_USER_TYPE:
		held = f"has user type {user_type}" if user_type else "has no user type"
		return (
			f"only a {constants.APPROVER_USER_TYPE} (a staff login) can {verb}, and "
			f"{str(user).strip()} {held}"
		)
	return None


def refusal(name, action, problems):
	"""One sentence for the person who asked: ``KBV-00012 cannot be approved: a; b.``"""
	return f"{name} cannot be {action}: " + "; ".join(problems) + "."


# ------------------------------------------------------------------ the state machine (PR 3)

#: The actions, by the names of the endpoints in ``api/knowledge_base.py`` that perform them.
SUBMIT_FOR_REVIEW = "submit_for_review"
WITHDRAW = "withdraw"
REQUEST_CHANGES = "request_changes"
APPROVE_AND_PUBLISH = "approve_and_publish"
DISCARD = "discard"
#: Not an endpoint: publishing a newer version does this to the one it replaces.
SUPERSEDE = "supersede"
START_REVISION = "start_revision"
CONFIRM_STILL_ACCURATE = "confirm_still_accurate"
RETIRE = "retire"
#: A read, not a move: the live-vs-draft diff (``review_diff``). Listed so the form knows whether
#: to offer it.
REVIEW_DIFF = "review_diff"

#: **The whole state machine.** Every move a version's ``review_state`` can make, as
#: ``action: (states it may start from, state it ends in)``. ``publish.transition`` is the only
#: writer of ``review_state`` and refuses any move not listed here; a version starts as a Draft
#: when it is created (the field's default), and nothing else moves it. Published, Superseded and
#: Discarded are history: Superseded and Discarded are final, and Published only ever becomes
#: Superseded, when a newer version of the same article is approved.
TRANSITIONS = {
	SUBMIT_FOR_REVIEW: ((DRAFT,), IN_REVIEW),
	WITHDRAW: ((IN_REVIEW,), DRAFT),
	REQUEST_CHANGES: ((IN_REVIEW,), DRAFT),
	APPROVE_AND_PUBLISH: ((IN_REVIEW,), PUBLISHED),
	DISCARD: ((DRAFT,), DISCARDED),
	SUPERSEDE: ((PUBLISHED,), SUPERSEDED),
}

#: The buttons a version's form can show, in the order it shows them.
VERSION_ACTIONS = (SUBMIT_FOR_REVIEW, APPROVE_AND_PUBLISH, REQUEST_CHANGES, WITHDRAW, DISCARD, REVIEW_DIFF)
#: The buttons an article's form can show.
ARTICLE_ACTIONS = (START_REVISION, CONFIRM_STILL_ACCURATE, RETIRE)

#: How each action completes "KBV-00012 cannot be ...", for :func:`refusal`.
VERBS = {
	SUBMIT_FOR_REVIEW: "submitted for review",
	WITHDRAW: "withdrawn",
	REQUEST_CHANGES: "sent back for changes",
	APPROVE_AND_PUBLISH: "approved",
	DISCARD: "discarded",
	SUPERSEDE: "superseded",
	START_REVISION: "revised",
	CONFIRM_STILL_ACCURATE: "confirmed as still accurate",
	RETIRE: "retired",
	REVIEW_DIFF: "compared with the published text",
}

_ARTICLE_PUBLISHED, _ARTICLE_RETIRED = constants.ARTICLE_STATUSES


def state_of(version):
	"""A version's ``review_state``; a row with none is a Draft (the field's default)."""
	return _get(version, "review_state") or DRAFT


def transition_problem(action, state):
	"""Why ``action`` cannot move a version that is ``state``; ``None`` if :data:`TRANSITIONS`
	allows it. An action that is not a move at all is refused too."""
	if action not in TRANSITIONS:
		return f"{action!r} does not move a version"
	sources, _target = TRANSITIONS[action]
	state = state or DRAFT
	if state in sources:
		return None
	return f"it is {state}, and only a version that is {_or(sources)} can be {VERBS[action]}"


def next_state(action, state):
	"""The state ``action`` moves a ``state`` version to. Raises ``ValueError`` (with the reason)
	for a move :data:`TRANSITIONS` does not allow."""
	problem = transition_problem(action, state)
	if problem:
		raise ValueError(problem)
	return TRANSITIONS[action][1]


def holds_kb_role(roles):
	"""KB Author or KB Approver: the two roles that can open a version at all."""
	return bool({constants.AUTHOR_ROLE, constants.APPROVER_ROLE} & set(roles or ()))


def required_text_problem(value, what):
	"""``what`` ("say what needs to change") when ``value`` is blank; ``None`` otherwise."""
	return None if str(value or "").strip() else what


# ------------------------------------------------------------------ who may move a version


def submit_problems(version, user, roles, *, article=None, secrets=()):
	"""Why ``user`` cannot send ``version`` for review; empty means they can.

	Any KB Author or KB Approver may submit any draft (the DocPerm is not owner-scoped, and the
	submitter is recorded, so they cannot then approve it). ``article`` is the article a revision
	belongs to (``None`` for a first version); ``secrets`` is ``content.document_secret_findings``
	for the version, which a save already refused but a stricter scan may find since: an approver
	is never asked to approve something that cannot be published.

	**A draft needs a kind** (PR 5): one of ``constants.ARTICLE_KINDS``, exactly. Required here and
	not by the schema, because v16 checks ``reqd`` on every save of a submitted version as well
	(``model/document.py:596-600``, ``:827-828``), so a ``reqd`` kind would stop a version published
	before the field existed from ever being superseded. Since 2026-09-29 a first version also needs
	one to be **approved** (:func:`publish_problems`): its number is made from it. That rule's own
	words ("..., so it cannot be numbered") are left out here when this list already says the same
	thing, so the form's Submit blocker names each missing field once.

	**No template guidance** (2026-09-30): a new draft starts with its kind's sections and the
	register template's guidance under each (``constants.KIND_SECTIONS``); guidance still in the body
	(``content.guidance_left``) is refused, so none is ever published. Only here: content cannot change
	once a version has left Draft, so what is submitted is what is approved.
	"""
	problems = []
	if not holds_kb_role(roles):
		problems.append(
			f"only a {constants.AUTHOR_ROLE} or {constants.APPROVER_ROLE} can submit a version for review"
		)
	problem = transition_problem(SUBMIT_FOR_REVIEW, state_of(version))
	if problem:
		problems.append(problem)
	if not str(_get(version, "title") or "").strip():
		problems.append("it has no title")
	if constants.block_code(_get(version, "department_block")) is None:
		problems.append(_NO_DEPARTMENT)
	if _get(version, "kind") not in constants.ARTICLE_KINDS:
		problems.append(_NO_KIND)
	if not shows_anything(_get(version, "body")):
		problems.append("it has no text")
	left = guidance_left(_get(version, "body"))
	if left:
		# 2026-09-30: a new draft starts with its template's guidance under each heading.
		problems.append(
			f"it still has the template's guidance under {_and(left)}: replace it with the "
			"article's own words, or delete it"
		)
	problems.extend(
		problem
		for problem in publish_problems(version, article)
		if problem.removesuffix(_UNNUMBERABLE) not in problems
	)
	if secrets:
		problems.append(secret_problem(secrets))
	return problems


def withdraw_problems(version, user, roles):
	"""Why ``user`` cannot take ``version`` back out of review; empty means they can.

	The author's side withdraws: whoever created it, submitted it or changed its content. A KB
	Approver who wants changes uses Request Changes, which says what to change.
	"""
	problems = []
	if not holds_kb_role(roles):
		problems.append(f"only a {constants.AUTHOR_ROLE} or {constants.APPROVER_ROLE} can withdraw a version")
	problem = transition_problem(WITHDRAW, state_of(version))
	if problem:
		problems.append(problem)
	if not _hands_in(version, _person(user)):
		problems.append(
			"only the person who created it, submitted it or changed its content can withdraw it; "
			f"a {constants.APPROVER_ROLE} sends it back with Request Changes"
		)
	return problems


def request_changes_problems(version, user, roles, *, user_type, browser, gate_flags):
	"""Why ``user`` cannot send ``version`` back to its author; empty means they can.

	The reviewer's side of the same move as :func:`withdraw_problems`: a KB Approver who is a named
	person, from a browser and not through an AI gate card, **who had no hand in it** (the same
	rule as approving). Someone who did is on the author's side, and withdraws it instead. The
	note saying what to change is checked by the endpoint (:func:`required_text_problem`).
	"""
	problems = _reviewer_problems(
		user, roles, user_type=user_type, browser=browser, gate_flags=gate_flags, verb="send back"
	)
	problem = transition_problem(REQUEST_CHANGES, state_of(version))
	if problem:
		problems.append(problem)
	did = _hands_in(version, _person(user))
	if did:
		problems.append(
			f"you {_and(did)}, so a different {constants.APPROVER_ROLE} must review it; you can "
			"withdraw it instead"
		)
	return problems


def discard_problems(version, user, roles):
	"""Why ``user`` cannot discard ``version``; empty means they can.

	A draft is discarded by someone on its author's side, or by a KB Approver tidying up. It is
	kept, as Discarded, and never deleted. Only a Draft: a version in review is withdrawn first.
	"""
	problems = []
	if not holds_kb_role(roles):
		problems.append(f"only a {constants.AUTHOR_ROLE} or {constants.APPROVER_ROLE} can discard a version")
	problem = transition_problem(DISCARD, state_of(version))
	if problem:
		problems.append(problem)
	if constants.APPROVER_ROLE not in set(roles or ()) and not _hands_in(version, _person(user)):
		problems.append(
			f"only the person who created it, submitted it or changed its content, or a "
			f"{constants.APPROVER_ROLE}, can discard it"
		)
	return problems


def publish_problems(version, article):
	"""What stops ``version`` being published; empty if nothing does.

	``article`` is the Knowledge Article a revision belongs to (anything with ``.get``), or
	``None`` for a first version.

	* **A first version must have a kind and a department**, because its number is made from them
	  (``SOP-06-0001``, :func:`next_article_number`). Submit already asks for both; this also holds
	  a version submitted before the kind existed.
	* A retired article takes no new version (v1 has no way back from retirement, WI-080
	  "Explicitly NOT").
	* **An article keeps its kind and its department**: both are part of its number, and a number
	  never changes (:func:`identity_problem`).
	"""
	if article is None:
		problems = []
		if constants.block_code(_get(version, "department_block")) is None:
			problems.append(_NO_DEPARTMENT + _UNNUMBERABLE)
		if _get(version, "kind") not in constants.ARTICLE_KINDS:
			problems.append(_NO_KIND + _UNNUMBERABLE)
		return problems
	problems = []
	name = _article_name(article)
	if (_get(article, "status") or _ARTICLE_PUBLISHED) != _ARTICLE_PUBLISHED:
		problems.append(f"{name} is {_get(article, 'status')}, so it takes no new version")
	problem = identity_problem(version, article)
	if problem:
		problems.append(problem)
	return problems


#: Why a first version cannot be numbered; :func:`submit_problems` leaves the suffix off when it
#: already says the rest.
_NO_DEPARTMENT = "it has no department"
_NO_KIND = "it has no kind"
_UNNUMBERABLE = ", so it cannot be numbered"

#: How a kind reads after "make it": "a Policy", "an SOP" (said "ess-oh-pee").
_A_KIND = {"Policy": "a Policy", "Process": "a Process", "SOP": "an SOP"}


def identity_problem(version, article):
	"""Why ``version`` cannot belong to ``article``: it changes the kind or the department that the
	article's number carries. ``None`` when it keeps both, and when there is no article (a first
	version is numbered from its own).

	**A published article's number never changes, and a number is never reused** (Nik, 2026-09-29),
	and the number says the kind and the department (``SOP-06-0001`` is an SOP in 06 Operations), so
	neither ever changes. To reclassify or move an article, a new article is published and the old
	one retired, naming its replacement, so an old citation still leads somewhere.

	The article's kind and department are its stored ones, or failing those, the ones its number
	carries. The same words at every layer that refuses the change: the version's save, submit and
	approve, and the AI drafting tool."""
	if article is None:
		return None
	kind, block = _identity(article)
	new_kind, new_block = _get(version, "kind") or None, _get(version, "department_block") or None
	kind_changes = bool(kind) and new_kind != kind
	block_changes = bool(block) and new_block != block
	if not kind_changes and not block_changes:
		return None
	name = _article_name(article)
	fixed = f"{name} keeps its kind and department: they are part of its number, which never changes."
	valid_kind = new_kind in constants.ARTICLE_KINDS
	valid_block = constants.block_code(new_block) is not None
	if (kind_changes and not valid_kind) or (block_changes and not valid_block):
		# A blank or unknown value is not somewhere to move it to: say what the revision stays.
		stays = " ".join(part for part in (_A_KIND.get(kind) or "", f"in {block}" if block else "") if part)
		return f"{fixed} Each of its versions is {stays}." if stays else fixed
	if kind_changes and block_changes:
		change = f"make it {_A_KIND[new_kind]} in {new_block}"
	elif kind_changes:
		change = f"make it {_A_KIND[new_kind]}"
	else:
		change = f"move it to {new_block}"
	return (
		f"{fixed} To {change}, start a new article with that kind and department. Once the new one is "
		f"published, retire {name} and name the new article in the reason."
	)


def number_problems(number, kind, department):
	"""Why ``number`` is not the right number for an article of ``kind`` in ``department``, as
	clauses (no full stop); empty when it is. Asked when an article row is first written (the
	controller) and of every article by the Integrity report.

	Names only numbers, codes and the fixed option values, never text: a kind or department that is
	not one of the options (possible only past the ORM) is described, not quoted."""
	number = "" if number is None else str(number)
	match = constants.ARTICLE_NUMBER.fullmatch(number)
	if match is None:
		return [f"{number or 'The name'} is not an article number of the form SOP-06-0001"]
	prefix, code, digits = match.groups()
	problems = []
	if int(digits) == 0:
		problems.append(f"{number} ends in 0000, which is never allocated")
	option = constants.department_option(code) if code in _BLOCK_CODES else None
	if option is None:
		problems.append(f"{number} is numbered in block {code}, which is not a department block")
	elif constants.block_code(department) != code:
		problems.append(
			f"{number} is numbered in block {code}, but its department is {_described(department)}"
		)
	number_kind = constants.PREFIX_KINDS[prefix]
	if kind != number_kind:
		problems.append(
			f"{number} is numbered as {_A_KIND[number_kind]}, but its kind is {_kind_described(kind)}"
		)
	return problems


def start_revision_problems(article, roles):
	"""Why a new revision of ``article`` cannot be started; empty means it can.

	One open version per article is not a refusal: ``start_revision`` answers with the open one.
	"""
	problems = []
	if not holds_kb_role(roles):
		problems.append(f"only a {constants.AUTHOR_ROLE} or {constants.APPROVER_ROLE} can revise an article")
	if article is None:
		problems.append("there is no published article to revise")
		return problems
	status = _get(article, "status") or _ARTICLE_PUBLISHED
	if status != _ARTICLE_PUBLISHED:
		problems.append(f"it is {status}")
	return problems


def retire_problems(article, user, roles, *, user_type, browser, gate_flags, open_version=None):
	"""Why ``user`` cannot retire ``article``; empty means they can.

	A KB Approver who is a named person, from a browser and not through an AI gate card: retiring
	takes approved text away from every reader, so it is an approver's decision, though not a
	second person's (stopping should never be the hard direction). Not while a revision is open,
	which would otherwise publish into a retired article; publish or discard it first. The reason
	readers are shown is checked by the endpoint (:func:`required_text_problem`).
	"""
	problems = _reviewer_problems(
		user, roles, user_type=user_type, browser=browser, gate_flags=gate_flags, verb="retire"
	)
	if article is None:
		problems.append("there is no article to retire")
		return problems
	status = _get(article, "status") or _ARTICLE_PUBLISHED
	if status != _ARTICLE_PUBLISHED:
		problems.append(f"it is already {status}")
	if open_version:
		problems.append(f"{open_version} is still open on it; publish or discard it first")
	return problems


def confirm_problems(article, user, roles, *, user_type, browser, gate_flags):
	"""Why ``user`` cannot confirm ``article`` is still accurate; empty means they can.

	The article's process owner or a KB Approver (the process owner need hold no KB role: they own
	the process, not the knowledge base), as a named person with a staff login, from a browser and
	not through an AI gate card: a review date an assistant could push forward is not a review.
	"""
	me = _person(user)
	problems = []
	is_owner = bool(me) and me == _person(_get(article, "process_owner"))
	if not is_owner and constants.APPROVER_ROLE not in set(roles or ()):
		problems.append(f"only its process owner or a {constants.APPROVER_ROLE} can confirm it is still accurate")
	problem = _approver_account_problem(user, me, user_type, verb="confirm")
	if problem:
		problems.append(problem)
	if not browser:
		problems.append(_NOT_A_BROWSER["confirm"])
	if _gate_action(gate_flags):
		problems.append("an AI assistant's action cannot confirm an article, even once it is confirmed")
	if article is None:
		problems.append("there is no article to confirm")
		return problems
	status = _get(article, "status") or _ARTICLE_PUBLISHED
	if status != _ARTICLE_PUBLISHED:
		problems.append(f"it is {status}")
	return problems


def review_diff_problems(roles, *, browser, gate_flags):
	"""Why the live-vs-draft diff cannot be shown; empty means it can.

	It returns draft text, so it answers only what the Version doctype itself answers (a KB role)
	and only a person's browser, never a token and never an AI gate card: drafts do not reach an
	assistant by any path (ADR 0017).
	"""
	problems = []
	if not holds_kb_role(roles):
		problems.append(f"only a {constants.AUTHOR_ROLE} or {constants.APPROVER_ROLE} can read a draft")
	if not browser:
		problems.append(_NOT_A_BROWSER["compare"])
	if _gate_action(gate_flags):
		problems.append("an AI assistant's action cannot read a draft")
	return problems


# ------------------------------------------------------------------ what the forms offer


def version_actions(version, user, roles, *, user_type, browser, gate_flags, article=None):
	"""The version actions ``user`` may take now, in :data:`VERSION_ACTIONS` order.

	Each is offered exactly when its ``*_problems`` function has nothing to say, so the form never
	shows a button the server would refuse. Approve is judged as the page will send it (the
	``modified`` the form loaded), and Request Changes without its note, which the dialog asks for.
	"""
	allowed = []
	for action in VERSION_ACTIONS:
		if action == SUBMIT_FOR_REVIEW:
			problems = submit_problems(version, user, roles, article=article)
		elif action == APPROVE_AND_PUBLISH:
			problems = approval_problems(
				version,
				user,
				roles,
				user_type=user_type,
				browser=browser,
				gate_flags=gate_flags,
				opened_modified=_get(version, "modified"),
			) + publish_problems(version, article)
		elif action == REQUEST_CHANGES:
			problems = request_changes_problems(
				version, user, roles, user_type=user_type, browser=browser, gate_flags=gate_flags
			)
		elif action == WITHDRAW:
			problems = withdraw_problems(version, user, roles)
		elif action == DISCARD:
			problems = discard_problems(version, user, roles)
		else:
			problems = review_diff_problems(roles, browser=browser, gate_flags=gate_flags)
		if not problems:
			allowed.append(action)
	return tuple(allowed)


def article_actions(article, user, roles, *, user_type, browser, gate_flags, open_version=None):
	"""The article actions ``user`` may take now, in :data:`ARTICLE_ACTIONS` order.

	Start Revision is offered to KB roles on a published article whether or not a revision is
	already open, because the action then opens that one.
	"""
	allowed = []
	if not start_revision_problems(article, roles):
		allowed.append(START_REVISION)
	context = {"user_type": user_type, "browser": browser, "gate_flags": gate_flags}
	if not confirm_problems(article, user, roles, **context):
		allowed.append(CONFIRM_STILL_ACCURATE)
	if not retire_problems(article, user, roles, open_version=open_version, **context):
		allowed.append(RETIRE)
	return tuple(allowed)


def reviewers_for(version, candidates):
	"""Who is asked to review ``version``: every candidate (the enabled System Users holding KB
	Approver) except Administrator and Guest and anyone with a hand in it (its owner, submitter,
	contributors and AI requester), the people :func:`approval_problems` would refuse anyway.
	First-seen order, each once."""
	seen, chosen = set(), []
	never = {name.casefold() for name in constants.NEVER_APPROVERS}
	for candidate in candidates or ():
		me = _person(candidate)
		if not me or me in seen or me in never or _hands_in(version, me):
			continue
		seen.add(me)
		chosen.append(str(candidate).strip())
	return chosen


def secret_problem(found):
	"""``content.document_secret_findings`` as one clause: where and what kind, never the value."""
	places = [f"{FIELD_LABELS.get(field, field)} line {f.line} looks like {f.kind}" for field, f in found]
	return "its content looks like it contains a secret (" + "; ".join(places) + "), so take it out first"


# ------------------------------------------------------------------ content edits and contributors


def changed_content_fields(stored, current):
	"""The content fields whose value differs between the stored version and ``current``.

	``stored`` is ``None`` for a new version, and then every content field counts as changed: a
	new version is wholly its creator's work. Values are compared as they would read: ``None``
	and ``""`` are equal, ``review_every_months`` compares as a number, and ``body`` compares
	after ``content.strip_presentation``, so a colour that the save strips anyway is not an edit.
	"""
	fields = constants.VERSION_CONTENT_FIELDS
	if stored is None:
		return fields
	return tuple(f for f in fields if _differs(f, _get(stored, f), _get(current, f)))


def content_edit_problem(stored, changed):
	"""Why these content changes may not be saved; ``None`` if they may.

	Content changes only while the stored version is a Draft. There is no flag that lets code
	past this (the controller applies it in ``before_validate``, which ``flags.ignore_validate``
	does not skip): nothing the Knowledge Base does needs to change the text of a version that has
	left Draft, and "what the approver read is what was published" depends on it.
	"""
	if stored is None or not changed:
		return None
	state = _get(stored, "review_state") or DRAFT
	if state == DRAFT:
		return None
	if state == IN_REVIEW:
		return (
			f"It is {IN_REVIEW}, so its content cannot change while a {constants.APPROVER_ROLE} "
			"reviews it. Withdraw it, or wait for the reviewer to request changes, and then edit "
			"the draft."
		)
	if state == DISCARDED:
		return "It was discarded and is kept as history. Start a new revision of the article instead."
	return f"It is {state}, and published versions are permanent history. Start a new revision instead."


def contributors(text):
	"""The user ids in a ``contributors`` value, first-seen order, each once (case-insensitively)."""
	seen, names = set(), []
	for token in _CONTRIBUTOR_SEPARATORS.split(text or ""):
		key = _person(token)
		if key and key not in seen:
			seen.add(key)
			names.append(token.strip())
	return tuple(names)


def with_contributor(text, user):
	"""``contributors`` with ``user`` added, one id per line. Unchanged content if already there."""
	names = list(contributors(text))
	if _person(user) and _person(user) not in {_person(n) for n in names}:
		names.append(str(user).strip())
	return "\n".join(names)


# ------------------------------------------------------------------ article numbers (2026-09-29)

#: ``("SOP", "06 Operations")`` -> ``"SOP-06-"``: ``constants.number_scope``, the part of a number
#: the kind and department fix. Publishing binds ``scope + "%"`` in its ``SELECT ... FOR UPDATE``, so
#: the ``%`` sits inside the parameter, never in the SQL text.
number_scope = constants.number_scope


def next_article_number(kind, block, taken):
	"""The next article number for a ``kind`` article in ``block``: ``SOP-06-0001``, then
	``SOP-06-0002``, and so on. Never ``0000``, and never one reused.

	``kind`` is one of ``constants.ARTICLE_KINDS``; ``block`` a ``department_block`` option
	(``"06 Operations"``) or its two-digit code. Each ``(prefix, department)`` scope has its own
	sequence (:func:`number_scope`). ``taken`` is every number already used: the articles' names and
	every number a version still names as its article. Entries in another scope, and anything that is
	not an article number (the retired ``KB`` format, garbage), are ignored; an entry is read after
	``strip()`` and without regard to case, because MariaDB compares names that way (``_ci``, PAD
	SPACE), so a ``sop-06-0001`` row would collide with a new ``SOP-06-0001`` on the primary key.

	The answer is one more than the highest taken, not the lowest gap: articles are never deleted, so
	a gap exists only if one vanished past the ORM, and its number may still be cited somewhere.

	Raises ``ValueError`` for a kind or block that is not one of the options, and
	:class:`SequenceFullError` once ``9999`` is taken.
	"""
	scope = number_scope(kind, block)
	highest = 0
	for number in taken or ():
		text = str(number or "").strip().upper()
		if not text.startswith(scope):
			continue
		parsed = constants.parse_article_number(text)
		if parsed is not None:
			highest = max(highest, parsed[2])
	if highest >= constants.MAX_SEQUENCE:
		raise SequenceFullError(
			f"No {kind} in {_block_option(scope[4:6])} can be numbered: {scope}0001 to "
			f"{scope}{constants.MAX_SEQUENCE} are all used. Ask Nik to extend the numbering."
		)
	return constants.article_number(kind, block, highest + 1)


# ------------------------------------------------------------------ review dates


def review_interval(months):
	"""The review interval in months: ``months`` when it is a positive whole number, otherwise
	``constants.DEFAULT_REVIEW_EVERY_MONTHS`` (6, POL-0001's six-month review).

	"Otherwise" covers blank, ``None``, ``0`` (what an Int field left empty reads as) and anything
	negative or unreadable. A nonsensical interval gets the policy default rather than a review
	date in the past or an error at publish.
	"""
	value = _whole_number(months)
	return value if value is not None and value > 0 else constants.DEFAULT_REVIEW_EVERY_MONTHS


def review_by(start, months=None):
	"""The date a review falls due: ``start`` plus :func:`review_interval` months.

	``start`` is the approval or last-review moment (``date``, ``datetime`` or an ISO string; a
	time of day is dropped, with no time-zone conversion, because Frappe's datetimes are already
	site-local). Calendar months, clamped to the end of a shorter month: 31 August plus six months
	is 28 February, or the 29th in a leap year, and 29 February plus twelve months is 28 February.
	"""
	day = _to_date(start)
	if day is None:
		raise ValueError(f"A review date needs a start date, not {start!r}.")
	return _add_months(day, review_interval(months))


# ------------------------------------------------------------------ helpers


def _add_months(day, months):
	index = day.year * 12 + (day.month - 1) + months
	year, month_index = divmod(index, 12)
	month = month_index + 1
	return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _to_date(value):
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip()
	try:
		return datetime.datetime.fromisoformat(text).date()
	except ValueError:
		pass
	try:
		return datetime.date.fromisoformat(text[:10])
	except ValueError:
		raise ValueError(f"{value!r} is not a date.") from None


def _whole_number(value):
	"""An int from an Int field's value, or ``None``. ``True`` is not a number of months."""
	if value is None or isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value
	if isinstance(value, float):
		return int(value) if value.is_integer() else None
	try:
		return int(str(value).strip())
	except ValueError:
		return None


def _moment(value):
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value
	try:
		return datetime.datetime.fromisoformat(str(value).strip())
	except ValueError:
		return None


def _same_moment(stored, opened):
	"""``modified`` as stored against the value the approver's page sent. Unreadable is different."""
	a, b = _moment(stored), _moment(opened)
	return a is not None and b is not None and a == b


_BLOCK_CODES = frozenset(code for code, _label in constants.DEPARTMENT_BLOCKS)


def _block_option(code):
	return next(option for option in constants.DEPARTMENT_BLOCK_OPTIONS if option.startswith(code + " "))


def _article_name(article):
	return _get(article, "name") or _get(article, "kb_number") or "its article"


def _identity(article):
	"""``(kind, department option)`` an article's number fixes: the ones stored on it, or failing
	those, the ones its number carries."""
	parsed = constants.parse_article_number(_get(article, "name") or _get(article, "kb_number"))
	kind = _get(article, "kind") or (parsed[0] if parsed else None)
	block = _get(article, "department_block") or (_block_option(parsed[1]) if parsed else None)
	return kind, block


def _described(department):
	"""A ``department_block`` value as a problem names it: the option, "blank", or a description of
	something that is not one (written past the ORM), never the value itself."""
	if not department:
		return "blank"
	return department if constants.block_code(department) else "not a department block"


def _kind_described(kind):
	if not kind:
		return "blank"
	return kind if kind in constants.ARTICLE_KINDS else "not one of the kinds"


def _differs(fieldname, before, after):
	# Identical values are the common case, and skip re-parsing a body that may carry megabytes of
	# pasted screenshots.
	if before == after:
		return False
	return _comparable(fieldname, before) != _comparable(fieldname, after)


def _comparable(fieldname, value):
	if value is None or value == "":
		return 0 if fieldname == "review_every_months" else ""
	if fieldname == "review_every_months":
		number = _whole_number(value)
		return number if number is not None else str(value).strip()
	if fieldname == "body":
		return strip_presentation(value)
	return value if isinstance(value, str) else str(value)


def _person(user):
	return str(user or "").strip().casefold()


def _and(parts):
	if len(parts) == 1:
		return parts[0]
	return ", ".join(parts[:-1]) + " and " + parts[-1]


def _or(parts):
	parts = list(parts)
	if len(parts) == 1:
		return parts[0]
	return ", ".join(parts[:-1]) + " or " + parts[-1]


def _gate_action(gate_flags):
	"""Whether this request is an AI gate card being queued or run (``frappe.flags``)."""
	return bool(_flag(gate_flags, "ai_gate_pending") or _flag(gate_flags, "ai_gate_bypass"))


def _get(obj, key):
	getter = getattr(obj, "get", None)
	if callable(getter):
		return getter(key)
	return getattr(obj, key, None)


def _flag(flags, name):
	if flags is None:
		return None
	return _get(flags, name)

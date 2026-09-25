# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who may approve a Knowledge Base version, and the numbers and dates publishing writes (WI-080 PR 2).

ADR 0017 section 2: **a version is published only by a KB Approver who did not write it, from a
signed-in browser, exactly as they opened it.** This module is the rule itself, as functions of
plain values, so the bench-free CI tier tests every branch. The controller
(``knowledge_base/doctype/knowledge_article_version``) applies it in ``before_submit`` and again in
``on_submit``, and PR 3's ``approve_and_publish`` asks it before it tries.

Standard library only, apart from ``signed_in_browser``, which comes from
``marketing/publish/workflow.py`` so that Marketing and the Knowledge Base share one definition of
"a person's login, not a token or a job". That module imports no frappe. Its ``approval_problems``
is deliberately **not** reused: it hard-codes Marketing Manager and knows nothing of contributors
or AI requesters.

The lifecycle (``constants.REVIEW_STATES``)::

    Draft --submit for review--> In Review --approve and publish--> Published --newer--> Superseded
      ^                              |
      +--withdraw / request changes--+
    Draft --discard--> Discarded

Content (``constants.VERSION_CONTENT_FIELDS``) changes only in Draft. Anyone who saves a change to
it is recorded in ``contributors``, one user per line, and may not approve that version.
"""

import calendar
import datetime
import re

from erpnext_enhancements.knowledge_base import constants
from erpnext_enhancements.knowledge_base.content import strip_presentation
from erpnext_enhancements.marketing.publish.workflow import signed_in_browser

__all__ = [
	"BlockFullError",
	"approval_problems",
	"changed_content_fields",
	"content_edit_problem",
	"contributors",
	"kb_number_prefix",
	"next_kb_number",
	"refusal",
	"review_by",
	"review_interval",
	"signed_in_browser",
	"with_contributor",
]

# The states by name, unpacked from the one definition. If constants.REVIEW_STATES ever gains or
# loses a state this line raises at import, which is the point: every rule below names states.
DRAFT, IN_REVIEW, PUBLISHED, SUPERSEDED, DISCARDED = constants.REVIEW_STATES

#: ``KB-0612``: block ``06``, article ``12``. Matched case-insensitively and with surrounding
#: space ignored, because MariaDB's default collation compares names that way too (``_ci``, PAD
#: SPACE): a ``kb-0612`` row would collide with a new ``KB-0612`` on the primary key.
_KB_NUMBER = re.compile(r"^KB-(\d{2})(\d{2})$", re.IGNORECASE)

#: A contributors field may have been typed with commas or spaces by an earlier tool; every one of
#: those separates user ids, none of which can contain them.
_CONTRIBUTOR_SEPARATORS = re.compile(r"[\s,;]+")


class BlockFullError(ValueError):
	"""A department block has used ``01`` to ``99``. PR 3 shows the message as it stands."""


# ------------------------------------------------------------------ approval


def approval_problems(version, user, roles, *, browser, gate_flags, opened_modified):
	"""Why ``user`` cannot approve ``version``; an empty list means they can.

	``version`` is the version **as stored** (the controller passes ``get_doc_before_save()``),
	never the copy in memory, which the code calling ``submit()`` could have changed; ``None``
	means there is nothing stored to approve. ``roles`` are the approver's roles.
	``browser`` is :func:`signed_in_browser` for this request. ``gate_flags`` is ``frappe.flags``
	(anything with ``.get`` or attributes). ``opened_modified`` is the ``modified`` value of the
	copy the approver had open, as the page sent it.

	Every rule is checked and every broken one is reported, so the refusal names each of them.
	"""
	problems = []
	if constants.APPROVER_ROLE not in set(roles or ()):
		problems.append(f"only a {constants.APPROVER_ROLE} can approve a version")
	if not browser:
		problems.append(
			"approvals are made by a person signed in to ERPNext in a browser; API keys, tokens, "
			"background jobs and the console cannot approve"
		)
	if _flag(gate_flags, "ai_gate_pending") or _flag(gate_flags, "ai_gate_bypass"):
		problems.append("an AI assistant's action cannot approve a version, even once it is confirmed")
	if version is None:
		problems.append("there is no saved version to approve")
		return problems

	state = _get(version, "review_state") or DRAFT
	if state != IN_REVIEW:
		problems.append(f"it is {state}, not {IN_REVIEW}")

	me = _person(user)
	if not me:
		problems.append("nobody is signed in")
	else:
		did = []
		if me == _person(_get(version, "owner")):
			did.append("created it")
		if me == _person(_get(version, "submitted_by")):
			did.append("submitted it for review")
		if me in {_person(name) for name in contributors(_get(version, "contributors"))}:
			did.append("changed its content")
		if me == _person(_get(version, "ai_requested_by")):
			did.append("asked an AI to draft it")
		if did:
			problems.append(f"you {_and(did)}, so a different {constants.APPROVER_ROLE} must approve it")

	if not _same_moment(_get(version, "modified"), opened_modified):
		problems.append("it changed after you opened it, so reload it and review it again")
	return problems


def refusal(name, action, problems):
	"""One sentence for the person who asked: ``KBV-00012 cannot be approved: a; b.``"""
	return f"{name} cannot be {action}: " + "; ".join(problems) + "."


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


# ------------------------------------------------------------------ KB numbers


def kb_number_prefix(block):
	"""``"06 Operations"`` (or ``"06"``) -> ``"KB-06"``. PR 3 binds ``prefix + "%"`` in its
	``SELECT ... FOR UPDATE``, so the ``%`` sits inside the parameter, never in the SQL text."""
	return f"KB-{_block(block)}"


def next_kb_number(block, taken):
	"""The next KB number in ``block``: ``KB-{block}{01..99}``, never ``00``, never one reused.

	``block`` is a ``department_block`` option (``"06 Operations"``) or its two-digit code.
	``taken`` is every KB number already allocated (any block; others are ignored, and so is
	anything that is not a KB number). The answer is one more than the highest taken, not the
	lowest gap: articles are never deleted, so a gap exists only if one vanished past the ORM, and
	its number may still be cited somewhere. ``{block}00`` is reserved for the block's index
	article, as in the POL-0000 register, and is never allocated here.

	Raises ``ValueError`` for a block that is not one of ``constants.DEPARTMENT_BLOCKS``, and
	:class:`BlockFullError` once ``{block}99`` is taken.
	"""
	code = _block(block)
	highest = 0
	for number in taken or ():
		match = _KB_NUMBER.match(str(number or "").strip())
		if match and match.group(1) == code:
			highest = max(highest, int(match.group(2)))
	if highest >= 99:
		raise BlockFullError(
			f"Department block {_block_option(code)} has no KB numbers left: KB-{code}01 to "
			f"KB-{code}99 are all used, and KB-{code}00 is reserved for the block's index. "
			"Publish the article under another block, or ask Nik to extend the numbering."
		)
	return f"KB-{code}{highest + 1:02d}"


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


def _block(block):
	codes = {code for code, _label in constants.DEPARTMENT_BLOCKS}
	code = constants.block_code(block)
	if code is None and isinstance(block, str) and block in codes:
		code = block
	if code is None:
		raise ValueError(f"{block!r} is not a department block.")
	return code


def _block_option(code):
	return next(option for option in constants.DEPARTMENT_BLOCK_OPTIONS if option.startswith(code + " "))


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


def _get(obj, key):
	getter = getattr(obj, "get", None)
	if callable(getter):
		return getter(key)
	return getattr(obj, key, None)


def _flag(flags, name):
	if flags is None:
		return None
	return _get(flags, name)

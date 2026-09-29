# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The pure halves of the Knowledge Base's two reports (WI-080 PR 4).

Standard library only (plus ``constants``, ``content`` and ``workflow``, which are too), so the
bench-free tier tests every rule here without a stub. The report controllers under ``report/`` read
the rows with bound SQL and hand them to these functions; nothing here reads or writes the database.

**"Knowledge Articles Due for Review"** (:func:`due_rows`) lists the published articles whose
``review_by`` has passed or falls within the window (30 days unless the filter says otherwise), and
the ones with no review date at all, with the process owner who has to look at each. Everything it
reads is the published article, which every staff user can read anyway.

**"Knowledge Base Integrity"** (:func:`integrity_problems`) checks what only a write past the ORM
can break: ``frappe.db.set_value``, raw SQL, or a batch-approved ``run_python_code`` card. Nothing
the Knowledge Base's own actions do can produce a row here, so a healthy site returns none. The
checks are the contract PR 3 left for PR 4, plus the invariants the README states elsewhere (see
its "The two reports"):

* every article's ``live_version`` exists, belongs to it, is submitted, is Published and has the
  article's ``version_number``;
* the article's ``content_hash`` equals :func:`content.content_hash` of that version, **and** the
  article's own text hashes the same (the first catches the approved version edited, the second
  the article edited, which is what every reader and AI tool reads), and the two agree on the
  department and on the kind (since 2026-09-29 a missing kind is a problem on either side: the
  number is made from it);
* the approver is a named person (never Administrator or Guest), is recorded the same on the
  article and the version, and is not the live version's owner, submitter, AI requester or a
  contributor;
* every other version with an ``article`` is Superseded, Discarded or open, at most one is open,
  and no open version sits on a Retired article;
* every ``review_state`` is one of the five, with the docstatus it must have (a Published or
  Superseded version is submitted; nothing is ever canceled);
* an article's name is a canonical article number (``SOP-06-0001``: never ``0000``, a real block)
  whose prefix is its kind and whose block is its department (``workflow.number_problems``), and a
  status is one of two;
* no File attached to either doctype is public.

**What a row says is never draft text.** A problem names the rule broken, article numbers, version names
(``KBV-00012``), states, numbers and user ids. It never quotes a title, summary, keyword, body,
change note or review note, of a version or an article: the report is readable by every KB
Approver and by an AI tool acting for one (``generate_report``, see the README), and the rule of
the Knowledge Base is that only approved text reaches an assistant. The controller also never
*selects* a version's text unless the version is submitted, i.e. approved: ``approved_texts`` holds
live versions at docstatus 1 only, read for hashing and nothing else.
"""

import datetime

from erpnext_enhancements.knowledge_base import constants, content, workflow

ARTICLE = constants.ARTICLE_DOCTYPE
VERSION = constants.VERSION_DOCTYPE
DRAFT, IN_REVIEW, PUBLISHED, SUPERSEDED, DISCARDED = constants.REVIEW_STATES
ARTICLE_PUBLISHED, ARTICLE_RETIRED = constants.ARTICLE_STATUSES

# ------------------------------------------------------------------ due for review

#: How far ahead "Knowledge Articles Due for Review" looks unless its filter says otherwise: a
#: process owner gets a month's notice before an article's review date.
DEFAULT_DUE_WITHIN_DAYS = 30

#: The report's ``state`` column, in the order the report lists them.
NO_REVIEW_DATE = "No review date"
OVERDUE = "Overdue"
DUE_SOON = "Due soon"
DUE_STATES = (NO_REVIEW_DATE, OVERDUE, DUE_SOON)

#: The Article columns the Due for Review report reads, and the only ones. All of them are the
#: published article's, which every staff user can read.
DUE_FIELDS = (
	"name",
	"title",
	"department_block",
	"process_owner",
	"review_by",
	"review_every_months",
	"last_reviewed_on",
	"last_reviewed_by",
)


def within_days(value):
	"""The window, in whole days, from the report's filter: blank is the default, below 0 is 0."""
	if value is None or value == "":
		return DEFAULT_DUE_WITHIN_DAYS
	try:
		days = int(float(value))
	except (TypeError, ValueError):
		return DEFAULT_DUE_WITHIN_DAYS
	return max(days, 0)


def review_due(review_by, today, days=DEFAULT_DUE_WITHIN_DAYS):
	"""``(state, days_left)`` for a published article's ``review_by``, or ``None`` when it is not
	due within ``days`` of ``today``.

	``days_left`` is negative when the date has passed and ``None`` when there is no date. A date
	that is today is due, not overdue: the README's "Due for review" is ``review_by`` *before*
	today. A published article with no readable date is listed, because an article nobody is ever
	asked to review is the case the report exists for.
	"""
	due = _date(review_by)
	if due is None:
		return NO_REVIEW_DATE, None
	days_left = (due - _date(today)).days
	if days_left < 0:
		return OVERDUE, days_left
	if days_left <= within_days(days):
		return DUE_SOON, days_left
	return None


def due_rows(articles, today, days=DEFAULT_DUE_WITHIN_DAYS, *, process_owner=None):
	"""The report's rows: every article in ``articles`` (published ones, as the controller reads
	them) that :func:`review_due` lists, optionally only one process owner's. No review date first,
	then the most overdue, then the soonest due."""
	owner = _folded(process_owner)
	rows = []
	for article in articles or ():
		if owner and _folded(_get(article, "process_owner")) != owner:
			continue
		due = review_due(_get(article, "review_by"), today, days)
		if due is None:
			continue
		state, days_left = due
		rows.append(
			{
				"kb_number": _get(article, "name"),
				"title": _get(article, "title"),
				"department_block": _get(article, "department_block"),
				"process_owner": _get(article, "process_owner"),
				"review_by": _get(article, "review_by"),
				"days_left": days_left,
				"state": state,
				"review_every_months": _get(article, "review_every_months"),
				"last_reviewed_on": _get(article, "last_reviewed_on"),
				"last_reviewed_by": _get(article, "last_reviewed_by"),
			}
		)
	rows.sort(
		key=lambda row: (
			DUE_STATES.index(row["state"]),
			row["days_left"] if row["days_left"] is not None else 0,
			str(row["kb_number"] or ""),
		)
	)
	return rows


# ------------------------------------------------------------------ integrity

#: The ``check`` column: which rule a row breaks. Short, so the column sorts and filters well.
CHECK_STATUS = "Article status"
CHECK_NUMBER = "Number"
CHECK_LIVE_VERSION = "Live version"
CHECK_APPROVED_TEXT = "Approved text"
CHECK_APPROVER = "Approver"
CHECK_VERSION_STATE = "Version state"
CHECK_OPEN_VERSIONS = "Open versions"
CHECK_PUBLIC_FILE = "Public file"
CHECKS = (
	CHECK_STATUS,
	CHECK_NUMBER,
	CHECK_LIVE_VERSION,
	CHECK_APPROVED_TEXT,
	CHECK_APPROVER,
	CHECK_VERSION_STATE,
	CHECK_OPEN_VERSIONS,
	CHECK_PUBLIC_FILE,
)

#: The Article columns the Integrity report reads. The article's text is read to hash it; it is
#: published text, and no row ever quotes it.
INTEGRITY_ARTICLE_FIELDS = (
	"name",
	"status",
	"department_block",
	"kind",
	"version_number",
	"live_version",
	"content_hash",
	"approved_by",
	"title",
	"summary",
	"keywords",
	"body",
)

#: The Version columns the Integrity report reads for **every** version: states, numbers and user
#: ids, and **none** of ``constants.VERSION_CONTENT_FIELDS`` (the test pins the two disjoint). A
#: draft's text is never selected at all.
INTEGRITY_VERSION_FIELDS = (
	"name",
	"article",
	"review_state",
	"docstatus",
	"version_number",
	"owner",
	"submitted_by",
	"ai_requested_by",
	"contributors",
	"approved_by",
)

#: The Version columns read for the **live versions only, and only once submitted** (the
#: controller's query says ``docstatus = 1``): the approved text, hashed and compared, never shown;
#: and the department and kind it was approved with, compared with the article's. ``kind`` (PR 5) is
#: not in ``content.HASHED_FIELDS`` (adding it would make every stored ``content_hash`` mismatch
#: its live version), so it is checked here on its own.
APPROVED_TEXT_FIELDS = ("name", *content.HASHED_FIELDS, "department_block", "kind")

#: The File columns read: which File, and where it is attached. Not its name or URL, which an
#: uploader chose and which may describe a draft.
PUBLIC_FILE_FIELDS = ("name", "attached_to_doctype", "attached_to_name")

#: The docstatus each state has. Nothing the Knowledge Base does cancels a version, and v1.556.1
#: refuses Frappe's own Discard, so docstatus 2 is never right.
EXPECTED_DOCSTATUS = {DRAFT: 0, IN_REVIEW: 0, DISCARDED: 0, PUBLISHED: 1, SUPERSEDED: 1}

_NEVER_APPROVERS = frozenset(name.casefold() for name in constants.NEVER_APPROVERS)


def integrity_problems(articles, versions, approved_texts, public_files):
	"""Every broken invariant, as ``{check, article, version, detail}`` rows. ``[]`` when healthy.

	``articles`` are Knowledge Article rows (:data:`INTEGRITY_ARTICLE_FIELDS`); ``versions`` are
	every Knowledge Article Version row, metadata only (:data:`INTEGRITY_VERSION_FIELDS`);
	``approved_texts`` maps a live version's name to its approved text (:data:`APPROVED_TEXT_FIELDS`,
	submitted versions only); ``public_files`` are the Files attached to either doctype with
	``is_private`` unset (:data:`PUBLIC_FILE_FIELDS`).
	"""
	articles = list(articles or ())
	versions = list(versions or ())
	approved_texts = dict(approved_texts or {})
	by_article = {_get(a, "name"): a for a in articles}
	by_version = {_get(v, "name"): v for v in versions}
	problems = []

	def add(check, article, version, detail):
		problems.append({"check": check, "article": article, "version": version, "detail": detail})

	for article in articles:
		_article_problems(article, by_version, approved_texts, add)

	for version in versions:
		_version_problems(version, by_article, add)

	for name, article in by_article.items():
		_open_version_problems(name, article, versions, add)

	for row in public_files or ():
		doctype = _get(row, "attached_to_doctype")
		attached = _get(row, "attached_to_name")
		add(
			CHECK_PUBLIC_FILE,
			attached if doctype == ARTICLE else None,
			attached if doctype == VERSION else None,
			f"File {_get(row, 'name')}, attached to {doctype} {attached}, is public. "
			"Every knowledge-base file is private.",
		)

	problems.sort(
		key=lambda row: (
			str(row["article"] or ""),
			str(row["version"] or ""),
			CHECKS.index(row["check"]),
			row["detail"],
		)
	)
	return problems


def _article_problems(article, by_version, approved_texts, add):
	name = _get(article, "name")
	status = _get(article, "status")
	if status not in constants.ARTICLE_STATUSES:
		add(
			CHECK_STATUS, name, None, f"{name} has status {status!r}, which is neither Published nor Retired."
		)

	# 2026-09-29: canonical (case-sensitive, so a lowercase name written past the ORM is caught), never
	# 0000, in a real block, in its own department, and with its kind's prefix.
	for problem in workflow.number_problems(name, _get(article, "kind"), _get(article, "department_block")):
		add(CHECK_NUMBER, name, None, problem + ".")

	live = _get(article, "live_version")
	if not live:
		add(CHECK_LIVE_VERSION, name, None, f"{name} has no live version.")
		return
	version = by_version.get(live)
	if version is None:
		add(CHECK_LIVE_VERSION, name, live, f"{name}'s live version {live} does not exist.")
		return

	owner_of = _get(version, "article")
	if owner_of != name:
		add(
			CHECK_LIVE_VERSION,
			name,
			live,
			f"{name}'s live version {live} belongs to {owner_of or 'no article'}.",
		)
	docstatus = _docstatus(version)
	if docstatus != 1:
		add(
			CHECK_LIVE_VERSION,
			name,
			live,
			f"{name}'s live version {live} is not submitted (docstatus {docstatus}).",
		)
	state = _get(version, "review_state")
	if state != PUBLISHED:
		add(
			CHECK_LIVE_VERSION,
			name,
			live,
			f"{name}'s live version {live} is {state or 'blank'}, not Published.",
		)
	article_number, version_number = (
		_int(_get(article, "version_number")),
		_int(_get(version, "version_number")),
	)
	if article_number != version_number:
		add(
			CHECK_LIVE_VERSION,
			name,
			live,
			f"{name} says it is at version {article_number}, but its live version {live} is version "
			f"{version_number}.",
		)

	_approver_problems(name, article, live, version, add)

	text = approved_texts.get(live) if docstatus == 1 else None
	if text is None:
		return
	approved = content.content_hash(text)
	if _get(article, "content_hash") != approved:
		add(
			CHECK_APPROVED_TEXT,
			name,
			live,
			f"{name}'s recorded content hash does not match its live version {live}: that version's "
			"text, or the hash, changed after it was approved.",
		)
	if content.content_hash(article) != approved:
		add(
			CHECK_APPROVED_TEXT,
			name,
			live,
			f"{name}'s published text is not the text of its live version {live}: the article changed "
			"after it was approved.",
		)
	if _get(text, "department_block") != _get(article, "department_block"):
		add(
			CHECK_NUMBER,
			name,
			live,
			f"{name} is in {_get(article, 'department_block') or 'no department'}, but its live version "
			f"{live} was approved in {_get(text, 'department_block') or 'no department'}.",
		)
	# PR 5: the kind is not hashed, so it is compared here. Since 2026-09-29 no kind on either side is
	# a problem too, not agreement: the number is made from the kind, and every article has one.
	classified, approved_as = _get(article, "kind") or None, _get(text, "kind") or None
	if classified != approved_as:
		add(
			CHECK_APPROVED_TEXT,
			name,
			live,
			f"{name} is {_classified(classified)}, but its live version {live} was approved "
			f"{_approved_as(approved_as)}.",
		)
	elif classified is None:
		add(CHECK_APPROVED_TEXT, name, live, f"{name} and its live version {live} have no kind.")


def _approver_problems(name, article, live, version, add):
	approver = _get(article, "approved_by")
	recorded = _get(version, "approved_by")
	if not approver:
		add(CHECK_APPROVER, name, live, f"{name} records no approver.")
		return
	if _folded(approver) != _folded(recorded):
		add(
			CHECK_APPROVER,
			name,
			live,
			f"{name} says it was approved by {approver}, but its live version {live} says "
			f"{recorded or 'nobody'}.",
		)
	if _folded(approver) in _NEVER_APPROVERS:
		add(
			CHECK_APPROVER, name, live, f"{name} was approved by {approver}, which never approves an article."
		)
		return
	hands = _hands_in(version, approver)
	if hands:
		add(
			CHECK_APPROVER,
			name,
			live,
			f"{name} was approved by {approver}, who {_and(hands)} on its live version {live}. A second "
			"person approves.",
		)


def _version_problems(version, by_article, add):
	name = _get(version, "name")
	article = _get(version, "article") or None
	state = _get(version, "review_state")
	docstatus = _docstatus(version)

	if state not in constants.REVIEW_STATES:
		add(
			CHECK_VERSION_STATE,
			article,
			name,
			f"{name} has review state {state!r}, which is not one of the knowledge base's states.",
		)
	elif docstatus == 2:
		add(
			CHECK_VERSION_STATE,
			article,
			name,
			f"{name} is {state} at docstatus 2 (canceled). The knowledge base never cancels a version.",
		)
	elif docstatus != EXPECTED_DOCSTATUS[state]:
		add(
			CHECK_VERSION_STATE,
			article,
			name,
			f"{name} is {state} at docstatus {docstatus}; a {state} version is always at docstatus "
			f"{EXPECTED_DOCSTATUS[state]}.",
		)

	if article is None:
		if state in (PUBLISHED, SUPERSEDED):
			add(CHECK_VERSION_STATE, None, name, f"{name} is {state} but belongs to no article.")
		return
	if article not in by_article:
		add(CHECK_VERSION_STATE, article, name, f"{name} belongs to {article}, which does not exist.")
		return
	live = _get(by_article[article], "live_version")
	if state == PUBLISHED and live != name:
		add(
			CHECK_LIVE_VERSION,
			article,
			name,
			f"{name} is Published, but {article}'s live version is {live or 'not set'}. An article has "
			"one Published version; the others are Superseded.",
		)


def _open_version_problems(name, article, versions, add):
	open_names = sorted(
		_get(v, "name")
		for v in versions
		if _get(v, "article") == name
		and _docstatus(v) == 0
		and _get(v, "review_state") in constants.OPEN_REVIEW_STATES
	)
	if len(open_names) > 1:
		add(
			CHECK_OPEN_VERSIONS,
			name,
			None,
			f"{name} has {len(open_names)} open versions ({', '.join(open_names)}); an article has at "
			"most one.",
		)
	if _get(article, "status") == ARTICLE_RETIRED:
		for version in open_names:
			add(
				CHECK_OPEN_VERSIONS,
				name,
				version,
				f"{name} is Retired, but {version} is still open on it. A retired article takes no revision.",
			)


# ------------------------------------------------------------------ helpers


def _hands_in(version, user):
	"""What ``user`` did to ``version`` that stops them approving it, as phrases (the approval
	rules' own list, ``workflow.approval_problems``)."""
	me = _folded(user)
	did = []
	if me == _folded(_get(version, "owner")):
		did.append("created it")
	if me == _folded(_get(version, "submitted_by")):
		did.append("submitted it for review")
	if me in {_folded(other) for other in workflow.contributors(_get(version, "contributors"))}:
		did.append("changed its content")
	if me == _folded(_get(version, "ai_requested_by")):
		did.append("asked an AI to draft it")
	return did


def _and(parts):
	if len(parts) == 1:
		return parts[0]
	return ", ".join(parts[:-1]) + " and " + parts[-1]


def _classified(kind):
	"""``"classified SOP"``, or ``"not classified"``. A kind is one of three fixed words (v16's Select
	validation refuses any other through the ORM), never text someone typed; anything else written
	past the ORM is named only as not one of them."""
	if kind is None:
		return "not classified"
	return f"classified {kind}" if kind in constants.ARTICLE_KINDS else "classified as something that is not a kind"


def _approved_as(kind):
	if kind is None:
		return "with no kind"
	return f"as {kind}" if kind in constants.ARTICLE_KINDS else "as something that is not a kind"


def _docstatus(row):
	return _int(_get(row, "docstatus"))


def _int(value):
	if value is None or value == "" or isinstance(value, bool):
		return 0
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return 0


def _date(value):
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return datetime.date.fromisoformat(str(value).strip()[:10])
	except ValueError:
		return None


def _folded(value):
	return str(value or "").strip().casefold()


def _get(row, key):
	getter = getattr(row, "get", None)
	if callable(getter):
		return getter(key)
	return getattr(row, key, None)

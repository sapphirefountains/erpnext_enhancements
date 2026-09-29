# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The drafting tool's rules and its one write (WI-080 PR 6b, ADR 0017's 2026-09-28 amendment).

``assistant_tools/draft_knowledge_article.py`` is a thin Frappe Assistant Core wrapper over this
module: its ``precheck`` method is :func:`precheck` for the session user, which the AI write gate asks
before it queues a card, and its ``execute`` is :func:`from_card`. Nik approved the tool on 2026-09-28
("Yes, but they can submit as well"): an AI may write a **Draft**, a new article or a revision, and may
submit that Draft for review in the same card. Nothing here approves, publishes, sends back, withdraws,
discards, supersedes, retires or confirms anything; the only move it makes is Draft -> In Review, on the
version its own card has just written, through the same function the Submit for Review button uses
(``api.knowledge_base.submit_version``). ``tests/test_knowledge_base_tools.py`` holds this file to that
statically: every attribute it reads from ``publish`` and from ``api.knowledge_base`` is one of
``run``, ``asker``, ``open_version``, ``start_revision``, ``article_row`` and ``submit_version``.

**Always a card, confirmed by the person who asked.** :func:`from_card` refuses unless
``frappe.flags.ai_gate_pending`` names an AI Pending Action for this tool that ``gating_api._confirm_one``
is running (status Confirmed), so the tool is unusable, not unconfirmed, while AI write gating is off.
The requester is read from that card; the confirmer is the session user, and they must be the same
person (trimmed and casefolded, as the rules compare user ids). ``gating_api._check_identity`` lets any
System Manager decide a card, and a confirmed card runs as whoever confirms it, so a System Manager
confirming someone else's drafting card would become the draft's owner, a contributor and (with
``submit_for_review``) its submitter, and use up a second of the few approvers (WI-080, "Found while
designing Slice 3", 13). That rule lives here, not in the gate: a System Manager can still cancel the
card.

**Why a person still approves it.** Submitting records the requester as ``submitted_by``; they are
already the version's ``owner``, a contributor and its ``ai_requested_by``. ``workflow.approval_problems``
refuses every one of those as its approver, and refuses **any** approval made while a gate card runs
(``ai_gate_pending`` or ``ai_gate_bypass``), whoever confirmed it (finding 12). A different KB Approver,
a named System User signed in to a browser, approves it in the Desk like any draft. Nothing in the rules
changed for this.

**What an AI writes is escaped, not trusted.** ``frappe.utils.md_to_html`` passes raw HTML through
(markdown2 without ``safe_mode``, frappe ``origin/version-16`` ``utils/data.py:2480-2495``), so
:func:`markdown_html` calls markdown2 itself with the same extras and ``safe_mode="escape"``: a
``<script>`` or ``<div class="hidden">`` in the proposal becomes text a reader sees. The controller's
``before_validate`` then strips presentation, scans the stored HTML for secrets and records the
confirmer as a contributor, as for any save.

**The secret scan reads the Markdown, not the HTML** (found while building PR 6b). markdown2 reads
underscores inside a word as emphasis, so ``sk_live_...`` becomes ``sk<em>live</em>...`` and no longer
looks like a key once converted; the stored-HTML scan would miss it. :func:`secret_problems` scans every
text argument as the model sent it, names the argument, the line and the kind and never the value, and
**fails closed**: if the scan itself raises, the answer is that the text could not be checked.

**Pictures** are read from markdown2's HTML (every ``<img src>``, so a reference-style image is covered
too). A new article embeds none: pictures are added in the Desk, where they become private Files. A
revision may embed only this site's Files attached to that article, matched by ``?fid=`` or by path
once the site's origin is removed. Any other picture is refused by its position and host, never by its
URL.

**Every outcome is a return** (finding 5): FAC turns an exception into an Error Log carrying the call's
arguments and its traceback, which here would be a draft's whole text. A refusal is ``{"success":
false, "error": <reason>}``; at execution FAC reports it as a ``ToolReportedError`` and
``_confirm_one`` rolls back and marks the card Failed with the reason. No refusal and no result names
the content of any version: reasons name arguments, lines, kinds, positions, hosts, ids, states and
people, and a message that comes from elsewhere is scrubbed of the proposal's own text before it is
returned.

**All or nothing.** :func:`draft` runs inside ``publish.run`` (the deadlock retry), with messages muted,
and writes under a savepoint: a refusal at any step rolls back to it, so a card that asked to submit and
could not leaves no Draft behind. A deadlock or a duplicate entry is re-raised for ``publish.run`` to
retry the whole action.

Nothing here imports FAC or ``assistant_tools``. ``publish`` and ``api.knowledge_base`` are imported
inside the functions that write, so the pure checks (:func:`markdown_html`, :func:`secret_problems`,
:func:`picture_problems`) import on the bench-free contract tests' stubs.
"""

from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

import frappe

from erpnext_enhancements.knowledge_base import constants, content, workflow
from erpnext_enhancements.knowledge_base.search import normalize_kb_number

TOOL_NAME = "draft_knowledge_article"
ARTICLE = constants.ARTICLE_DOCTYPE
VERSION = constants.VERSION_DOCTYPE
PUBLISHED = constants.ARTICLE_STATUSES[0]

#: An AI Pending Action's status while ``gating_api._confirm_one`` runs its tool: it commits Confirmed
#: before it executes, and only then sets ``frappe.flags.ai_gate_pending``.
CONFIRMED = "Confirmed"

#: The limits the schema's descriptions state. The schema carries no ``maxLength``/``maxItems``: a
#: client's validator could otherwise refuse every turn, and the server is the one that enforces them.
MAX_TITLE = 140
MAX_SUMMARY = 500
MAX_KEYWORDS = 30
MAX_KEYWORD = 60
MAX_BODY = 60_000
MAX_CHANGE_NOTE = 1_000

#: Every argument the tool takes. Anything else is refused: a card should hold only what it will run.
ARGUMENTS = (
	"kb_number",
	"article_title",
	"department",
	"kind",
	"summary",
	"keywords",
	"body_markdown",
	"change_note",
	"process_owner",
	"submit_for_review",
)
REQUIRED = ("article_title", "kind", "summary", "body_markdown", "change_note")
TEXT_LIMITS = {
	"article_title": MAX_TITLE,
	"summary": MAX_SUMMARY,
	"body_markdown": MAX_BODY,
	"change_note": MAX_CHANGE_NOTE,
}
#: The arguments scanned for secrets, in this order. They are also the ones the AI gate withholds from
#: a log row that has no card (``_gate.WITHHELD_WHEN_UNQUEUED``).
SCANNED = ("article_title", "summary", "keywords", "body_markdown", "change_note")

#: The extras of v16's ``md_to_html`` (frappe ``origin/version-16`` ``utils/data.py:2485-2492``), which
#: :func:`markdown_html` uses with ``safe_mode="escape"``. The header ids and the ``screenshot`` and
#: table classes it adds are removed again by ``content.strip_presentation`` on save; the table keeps the
#: two classes v16's own editor writes.
MARKDOWN_EXTRAS = {
	"fenced-code-blocks": None,
	"tables": None,
	"header-ids": None,
	"toc": None,
	"highlightjs-lang": None,
	"html-classes": {"table": "table table-bordered", "img": "screenshot"},
}

#: Where a KB File's bytes are served from. A KB File is always private; ``/files/`` is accepted so a
#: revision is never refused over the prefix alone.
FILE_PREFIXES = ("/private/files/", "/files/")

#: A refusal names at most this many pictures, then how many more.
MAX_PICTURES_NAMED = 5

SAVEPOINT = "kb_ai_draft"

NOT_FROM_A_CARD = (
	"draft_knowledge_article runs only from its own approval card, once the person who asked for the "
	"draft has confirmed it in ERPNext. Nothing was written."
)
SECRETS_UNCHECKED = "the text could not be checked for secrets"
NEXT_STEP_DRAFT = (
	"Open the draft, check it, and press Submit for Review. A KB Approver who did not ask for it "
	"publishes it."
)
NEXT_STEP_IN_REVIEW = (
	"It is in review. A KB Approver other than you reviews it in ERPNext; to change it, withdraw it "
	"there first."
)
NOBODY_ASKED = " No other KB Approver could be asked to review it, so tell one yourself."

_NEVER = frozenset(name.casefold() for name in constants.NEVER_APPROVERS)


class MarkdownUnreadable(ValueError):
	"""markdown2 could not convert the body."""


class _NotWritten(Exception):
	"""A refusal found after the savepoint was taken: roll back to it and return the reasons."""

	def __init__(self, problems):
		super().__init__("; ".join(problems))
		self.problems = list(problems)


# ------------------------------------------------------------------ what the gate asks before a card


def precheck(arguments, requester, confirmer=None):
	"""Why this proposal cannot be written for ``requester``, as short clauses; empty means it can.

	The AI gate asks it (through the tool's ``precheck`` method, with the session user as
	``requester``) before it queues a card, and :func:`draft` asks it again at execution with the
	``confirmer``. Its reads are made as the server; it returns reasons only, never any text.

	The secret scan runs first and fails closed on its own. If a lookup after it raises, the gate
	queues the card (the tool checks again when it runs), but not a card the scan has refused: a
	secret it found is still the answer.
	"""
	secrets = secret_problems(arguments)
	try:
		problems, _values = _check(arguments, requester, confirmer, secrets=secrets)
	except Exception:
		if secrets:
			return secrets
		raise
	return problems


def markdown_html(text):
	"""``text`` (Markdown) as HTML, with every piece of raw HTML in it escaped into visible text."""
	import markdown2

	# A fresh copy each call: the nested dict is the caller's, and markdown2 is not promised not to touch it.
	extras = {
		key: (dict(value) if isinstance(value, dict) else value) for key, value in MARKDOWN_EXTRAS.items()
	}
	try:
		return str(markdown2.markdown(text or "", extras=extras, safe_mode="escape"))
	except markdown2.MarkdownError:
		raise MarkdownUnreadable("the Markdown could not be converted") from None


def secret_problems(arguments):
	"""A clause naming every secret-shaped string in the text arguments, by argument, line and kind,
	never the value; ``[]`` when there is none. Fails closed: a scan that raises is a refusal."""
	args = arguments if isinstance(arguments, dict) else {}
	places = []
	try:
		for key in SCANNED:
			value = args.get(key)
			if key == "keywords":
				if not isinstance(value, list):
					continue
				# One keyword per line, so a finding's line is the keyword's position.
				for finding in content.secret_findings("\n".join(str(word) for word in value)):
					places.append(f"keyword {finding.line} looks like {finding.kind}")
				continue
			if isinstance(value, str) and value:
				for finding in content.secret_findings(value):
					places.append(f"{key} line {finding.line} looks like {finding.kind}")
	except Exception:
		return [SECRETS_UNCHECKED]
	if not places:
		return []
	return [
		"the text looks like it contains a secret (" + "; ".join(places) + "); take it out: every staff "
		"member and every AI tool reads the knowledge base, so a secret belongs in the password manager, "
		"and an article can say where it is kept and who to ask"
	]


def picture_problems(
	body_html, *, kb_number=None, allowed_fids=frozenset(), allowed_paths=frozenset(), site_url=""
):
	"""A clause naming every picture in ``body_html`` that may not be written; ``[]`` when none.

	``kb_number`` is ``None`` for a new article, which embeds no picture at all. A revision of
	``kb_number`` may embed only this site's Files attached to that article, matched by ``?fid=``
	against ``allowed_fids`` or by path against ``allowed_paths`` once ``site_url``'s origin is removed.
	A refused picture is named by its position and its host, never by its URL.
	"""
	sources = _picture_sources(body_html)
	site_host = (urlsplit(site_url or "").hostname or "").casefold()
	refused = []
	for position, src in enumerate(sources, 1):
		parts = urlsplit(src.strip())
		host = (parts.hostname or "").casefold()
		local = (not parts.scheme and not parts.netloc) or (
			parts.scheme in ("http", "https") and bool(host) and host == site_host
		)
		if kb_number is not None and local and _own_file(parts, allowed_fids, allowed_paths):
			continue
		refused.append(_picture_place(position, parts, local))
	if not refused:
		return []
	named = refused[:MAX_PICTURES_NAMED]
	if len(refused) > MAX_PICTURES_NAMED:
		named.append(f"and {len(refused) - MAX_PICTURES_NAMED} more")
	where = "; ".join(named)
	if kb_number is None:
		return [f"a new article cannot embed pictures from here ({where}); add pictures in the Desk"]
	return [f"a revision can embed only {kb_number}'s own pictures ({where}); add other pictures in the Desk"]


# ------------------------------------------------------------------ the one write


def from_card(arguments):
	"""The tool's ``execute``: :func:`draft`, only for the confirmed card this tool is running under."""
	pending = getattr(frappe.flags, "ai_gate_pending", None)
	card = None
	if isinstance(pending, str) and pending:
		card = frappe.db.get_value(
			"AI Pending Action", pending, ["tool_name", "requested_by", "status"], as_dict=True
		)
	if not card or card.get("tool_name") != TOOL_NAME or card.get("status") != CONFIRMED:
		return {"success": False, "error": NOT_FROM_A_CARD}
	return draft(arguments, card.get("requested_by"))


def draft(arguments, requester):
	"""Write the Draft ``requester`` asked for, as the confirming (session) user, and submit it for
	review when ``submit_for_review`` is true: all of it or none of it. Returns the result, or
	``{"success": False, "error": <reason>}``; see the module docstring."""
	from erpnext_enhancements.knowledge_base import publish

	args = arguments if isinstance(arguments, dict) else {}
	confirmer = frappe.session.user
	flags = frappe.flags
	muted = flags.mute_messages
	flags.mute_messages = True
	try:
		return publish.run(lambda: _attempt(args, requester, confirmer))
	except (frappe.ValidationError, frappe.PermissionError) as exc:
		# publish.run's own "try again", after three attempts MariaDB rolled back.
		frappe.clear_messages()
		return _not_written([_clause(exc, args)])
	finally:
		flags.mute_messages = muted


def _attempt(args, requester, confirmer):
	problems, values = _check(args, requester, confirmer)
	if problems:
		return _not_written(problems)
	frappe.db.savepoint(SAVEPOINT)
	try:
		outcome = _write(values, requester)
	except (frappe.QueryDeadlockError, frappe.DuplicateEntryError):
		# MariaDB rolled the transaction back (or will): publish.run starts the whole action again.
		raise
	except Exception as exc:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.clear_messages()
		if isinstance(exc, _NotWritten):
			return _not_written(exc.problems)
		if isinstance(exc, frappe.ValidationError | frappe.PermissionError):
			return _not_written([_clause(exc, args)])
		raise
	frappe.db.release_savepoint(SAVEPOINT)
	return outcome


def _write(values, requester):
	from erpnext_enhancements.api import knowledge_base as kb_api
	from erpnext_enhancements.knowledge_base import publish

	fields = {
		"title": values["title"],
		"department_block": values["department_block"],
		"kind": values["kind"],
		"summary": values["summary"],
		"keywords": values["keywords"],
		"body": values["body"],
		"change_note": values["change_note"],
	}
	if values.get("process_owner"):
		fields["process_owner"] = values["process_owner"]
	provenance = {"ai_drafted": 1, "ai_requested_by": requester}
	number = values.get("kb_number")
	if number is None:
		doc = frappe.get_doc({"doctype": VERSION, **fields, **provenance})
		# ai_drafted and ai_requested_by sit at permlevel 1, which v16 resets for anyone who cannot write
		# it; the confirmer's create permission was checked by precheck, as publish.start_revision's
		# caller checks it.
		doc.flags.ignore_permissions = True
		doc.insert()
		action = "created"
	else:
		# Under the article's row lock, as the Start Revision button does: a retire or another revision
		# that committed since the precheck read is seen here.
		article = frappe.get_doc(ARTICLE, number, for_update=True)
		problems = _article_problems(number, article, fields["department_block"])
		if not problems:
			open_row = publish.open_version(number, lock=True)
			if open_row:
				problems = [_open_version_problem(number, open_row)]
		if problems:
			raise _NotWritten(problems)
		doc = publish.start_revision(article, content=fields, provenance=provenance)
		action = "revision_started"
	asked = []
	if values["submit"]:
		# The check the Submit for Review endpoint's _load_version makes, as the confirmer.
		doc.check_permission("write")
		moved = kb_api.submit_version(doc, publish.asker())
		asked = moved.get("notified") or []
	submitted = bool(values["submit"])
	next_step = NEXT_STEP_IN_REVIEW if submitted else NEXT_STEP_DRAFT
	if submitted and not asked:
		next_step += NOBODY_ASKED
	return {
		"success": True,
		"action": action,
		"name": doc.name,
		"kb_number": number,
		"review_state": workflow.state_of(doc),
		"submitted": submitted,
		# A count: the reviewers' names stay in ERPNext.
		"reviewers_asked": len(asked),
		"desk_url": frappe.utils.get_url_to_form(VERSION, doc.name),
		"next_step": next_step,
	}


# ------------------------------------------------------------------ the checks


def _check(arguments, requester, confirmer=None, *, secrets=None):
	"""``(problems, values)``: every reason this cannot be written, and the arguments as they would be.
	``secrets`` is :func:`secret_problems` when the caller already ran it."""
	args = arguments if isinstance(arguments, dict) else {}
	if confirmer is not None and _person(confirmer) != _person(requester):
		return [f"only {_full_name(requester)}, who asked for this draft, can confirm it"], {}
	problems, values = _read(args)
	problems += _who(requester, confirmer)
	problems += secret_problems(args) if secrets is None else list(secrets)
	owner = values.get("process_owner")
	if owner and not _enabled_staff(owner):
		problems.append("process_owner must be the user id of an enabled staff login")
	number = values.get("kb_number")
	article = None
	if number is not None:
		from erpnext_enhancements.knowledge_base import publish

		article = publish.article_row(number)
		problems += _article_problems(number, article, values.get("department_block"))
		if article is not None and article.get("status") == PUBLISHED:
			if not values.get("department_block"):
				values["department_block"] = article.get("department_block")
			open_row = publish.open_version(number)
			if open_row:
				problems.append(_open_version_problem(number, open_row))
	if values.get("body") is not None:
		if number is None:
			problems += picture_problems(values["body"])
		elif article is not None and article.get("status") == PUBLISHED:
			fids, paths = _article_files(number)
			problems += picture_problems(
				values["body"],
				kb_number=number,
				allowed_fids=fids,
				allowed_paths=paths,
				site_url=frappe.utils.get_url(),
			)
	return list(dict.fromkeys(problems)), values


def _read(args):
	"""The shape rules, and the values as they would be written. No lookups."""
	problems, values = [], {}
	unknown = [str(key)[:40] for key in args if key not in ARGUMENTS]
	if unknown:
		verb = "is not an argument" if len(unknown) == 1 else "are not arguments"
		problems.append(f"{_and(unknown)} {verb} of {TOOL_NAME}")
	for key in REQUIRED:
		value = args.get(key)
		if value is None:
			problems.append(f"{key} is required")
		elif not isinstance(value, str):
			problems.append(f"{key} must be text")
		elif not value.strip():
			problems.append(f"{key} is empty")
		elif key in TEXT_LIMITS and len(value) > TEXT_LIMITS[key]:
			problems.append(f"{key} is {len(value):,} characters, more than {TEXT_LIMITS[key]:,}")
	values["title"] = _text(args.get("article_title"), collapse=True)
	values["summary"] = _text(args.get("summary"))
	values["change_note"] = _text(args.get("change_note"))

	kind = args.get("kind")
	values["kind"] = constants.kind_option(kind) if isinstance(kind, str) else None
	if isinstance(kind, str) and kind.strip() and values["kind"] is None:
		problems.append("kind must be Policy, Process or SOP")

	raw_number = args.get("kb_number")
	values["kb_number"] = None
	if raw_number is not None and not (isinstance(raw_number, str) and not raw_number.strip()):
		values["kb_number"] = normalize_kb_number(raw_number) if isinstance(raw_number, str) else None
		if values["kb_number"] is None:
			problems.append("kb_number must be a KB number such as KB-0601, or left out for a new article")
	new_article = raw_number is None or (isinstance(raw_number, str) and not raw_number.strip())

	department = args.get("department")
	values["department_block"] = None
	if department is not None and not (isinstance(department, str) and not department.strip()):
		values["department_block"] = constants.department_option(department)
		if values["department_block"] is None:
			problems.append("department must be one of the ten department blocks, such as 06 Operations")
	elif new_article:
		problems.append("department is required for a new article")

	values["keywords"] = ""
	keywords = args.get("keywords")
	if keywords is not None:
		if not isinstance(keywords, list):
			problems.append("keywords must be a list of words or short phrases")
		else:
			if len(keywords) > MAX_KEYWORDS:
				problems.append(f"keywords has {len(keywords)} entries, more than {MAX_KEYWORDS}")
			kept, seen = [], set()
			for position, word in enumerate(keywords, 1):
				if not isinstance(word, str):
					problems.append(f"keyword {position} must be text")
					continue
				word = " ".join(word.split())
				if len(word) > MAX_KEYWORD:
					problems.append(f"keyword {position} is longer than {MAX_KEYWORD} characters")
				if word and word.casefold() not in seen:
					seen.add(word.casefold())
					kept.append(word)
			values["keywords"] = ", ".join(kept)

	submit = args.get("submit_for_review", False)
	if submit is not None and not isinstance(submit, bool):
		problems.append("submit_for_review must be true or false")
	values["submit"] = submit is True

	owner = args.get("process_owner")
	values["process_owner"] = None
	if owner is not None and not (isinstance(owner, str) and not owner.strip()):
		if isinstance(owner, str):
			values["process_owner"] = owner.strip()
		else:
			problems.append("process_owner must be a user id (an email address)")

	values["body"] = None
	body = args.get("body_markdown")
	if isinstance(body, str) and body.strip() and len(body) <= MAX_BODY:
		try:
			html = markdown_html(body)
		except MarkdownUnreadable:
			problems.append("body_markdown could not be read as Markdown")
		else:
			if content.shows_anything(html):
				values["body"] = html
			else:
				problems.append("body_markdown shows nothing once converted; give the article's text")
	return problems, values


def _who(requester, confirmer):
	"""The rules on the people: a named, enabled staff login with a KB role asked for it, and the
	person confirming (at execution) may create a version."""
	if _person(requester) in _NEVER or not _person(requester):
		return [
			"an AI drafts a knowledge-base article only for a named person, and Administrator is a shared "
			"account: ask as yourself"
		]
	problems = []
	if not _enabled_staff(requester):
		problems.append(
			"an AI drafts a knowledge-base article only for an enabled staff login (a System User)"
		)
	elif not workflow.holds_kb_role(frappe.get_roles(requester)):
		problems.append(
			f"only a {constants.AUTHOR_ROLE} or {constants.APPROVER_ROLE} can have an AI draft a "
			"knowledge-base article"
		)
	if confirmer is not None and not frappe.has_permission(VERSION, "create", user=confirmer):
		problems.append(
			f"you cannot create a knowledge-base version (that needs {constants.AUTHOR_ROLE} or "
			f"{constants.APPROVER_ROLE})"
		)
	return problems


def _article_problems(number, article, department):
	"""Why ``number`` cannot take a new version: it is not a published article (an unknown and a
	retired one get the same words), or it keeps another department (``workflow.publish_problems``)."""
	if article is None or article.get("status") != PUBLISHED:
		return [
			f"{number} is not a published article, so it cannot be revised; find it with "
			"search_company_knowledge, or leave kb_number out to draft a new article"
		]
	block = department or article.get("department_block")
	return workflow.publish_problems({"department_block": block}, article)


def _open_version_problem(number, open_row):
	"""The article's open version, by id, state and who started it, and where to finish it: never its
	text. Continuing a draft by tool is deferred (WI-080, "Explicitly NOT")."""
	name = open_row.get("name")
	owner = frappe.db.get_value(VERSION, name, "owner")
	return (
		f"{number} already has an open version ({name}, {open_row.get('review_state') or 'Draft'}), "
		f"started by {_full_name(owner)}; finish or discard it in the Desk, then ask again: "
		f"{frappe.utils.get_url_to_form(VERSION, name)}"
	)


def _article_files(number):
	"""The names and paths of the Files attached to article ``number``: the only pictures a revision may
	embed. Publishing moved every picture a version used onto its article (``publish.move_files``), and a
	File left on a published version is one its text did not use; a revision that embedded one would
	show readers a picture they cannot open, since it would stay attached to that version."""
	fids, paths = set(), set()
	rows = frappe.get_all(
		"File",
		filters={"attached_to_doctype": ARTICLE, "attached_to_name": number},
		fields=["name", "file_url"],
	)
	for row in rows:
		if row.get("name"):
			fids.add(row.get("name"))
		if row.get("file_url"):
			paths.add(unquote(row.get("file_url")))
	return frozenset(fids), frozenset(paths)


def _own_file(parts, allowed_fids, allowed_paths):
	path = unquote(parts.path or "")
	if not path.startswith(FILE_PREFIXES):
		return False
	fids = {fid.strip() for fid in parse_qs(parts.query).get("fid", ()) if fid.strip()}
	return bool(fids & set(allowed_fids)) or path in allowed_paths


def _picture_place(position, parts, local):
	if local:
		return f"picture {position}, a file on this site"
	if parts.scheme == "data":
		return f"picture {position}, embedded data"
	host = parts.hostname or parts.scheme or "an unknown place"
	return f"picture {position}, from {host}"


def _picture_sources(body_html):
	if not isinstance(body_html, str) or "<" not in body_html:
		return []
	reader = _Pictures()
	reader.feed(body_html)
	reader.close()
	return reader.sources


class _Pictures(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.sources = []

	def handle_starttag(self, tag, attrs):
		if tag == "img":
			self.sources.append(next((value or "" for name, value in attrs if name == "src"), ""))

	handle_startendtag = handle_starttag


# ------------------------------------------------------------------ helpers


def _not_written(problems):
	return {"success": False, "error": _sentence(problems) + " Nothing was written."}


def _sentence(problems):
	text = "; ".join(str(problem).strip().rstrip(".") for problem in problems if str(problem).strip())
	text = text or "it was refused"
	return text[:1].upper() + text[1:] + "."


def _clause(exc, args):
	"""A message from elsewhere (a rule, the controller, Frappe) as a clause, with any of the proposal's
	own text in it replaced: a result or an error never carries a version's content."""
	return _scrub(str(exc).strip(), args) or "ERPNext refused it"


def _scrub(message, args):
	pieces = []
	for key in ("article_title", "summary", "change_note", "body_markdown"):
		value = args.get(key)
		if isinstance(value, str):
			pieces.extend(part.strip() for part in value.split("\n"))
	keywords = args.get("keywords")
	if isinstance(keywords, list):
		pieces.extend(str(word).strip() for word in keywords)
	for piece in sorted({p for p in pieces if len(p) >= 6}, key=len, reverse=True):
		message = message.replace(piece, "<withheld>")
	return message


def _text(value, *, collapse=False):
	if not isinstance(value, str):
		return ""
	return " ".join(value.split()) if collapse else value.strip()


def _enabled_staff(user):
	row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not row:
		return False
	try:
		enabled = int(row.get("enabled") or 0)
	except (TypeError, ValueError):
		enabled = 0
	return bool(enabled) and row.get("user_type") == constants.APPROVER_USER_TYPE


def _full_name(user):
	"""v16's ``get_fullname``, which answers for the session user when given nothing, so a blank is
	never passed to it."""
	if not str(user or "").strip():
		return "the person who asked"
	return frappe.utils.get_fullname(user) or str(user)


def _person(user):
	"""A user id as the rules compare them (``workflow._person``): trimmed and casefolded."""
	return str(user or "").strip().casefold()


def _and(parts):
	if len(parts) == 1:
		return parts[0]
	return ", ".join(parts[:-1]) + " and " + parts[-1]

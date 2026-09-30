"""The Knowledge Base's fixed vocabulary, defined once (WI-080 PR 1), and since 2026-09-29 the one
definition of an article's number (:data:`ARTICLE_NUMBER` and the functions after it).

Every value a Select field on the two KB doctypes can hold lives here, and
``tests/test_knowledge_base_schema.py`` asserts that each doctype JSON's ``options`` equal these
tuples exactly (``department_block`` with the blank it stores first, see
``DEPARTMENT_BLOCK_SELECT_OPTIONS``). The publishing code that arrives in later PRs writes these values, so it imports
them from here rather than typing the strings again: a misspelt state written by code is a row
that saves cleanly, never matches a filter, and is only found when somebody counts.

Standard library only, so the schema test and the pure rules modules can import it without a
bench.
"""

import re
import unicodedata

#: ``Knowledge Article.status``. An Article row exists only once a version has been approved, so
#: there is no Draft here: drafts live in the other doctype.
ARTICLE_STATUSES = ("Published", "Retired")

#: ``Knowledge Article Version.review_state``, in lifecycle order.
#:
#: - Draft: an author is writing it.
#: - In Review: submitted to a named KB Approver; content edits are refused (PR 2,
#:   ``workflow.content_edit_problem``).
#: - Published: approved and submitted; this is the live text.
#: - Superseded: was Published; a newer version replaced it. Kept as history.
#: - Discarded: abandoned before publishing. Kept, never deleted.
REVIEW_STATES = ("Draft", "In Review", "Published", "Superseded", "Discarded")

#: The two states in which a version is still being worked on. At most one per article: PR 3's
#: ``start_revision`` returns the open one rather than starting a second.
OPEN_REVIEW_STATES = ("Draft", "In Review")

#: The two doctypes, by name. ``assistant_tools/_gate.py`` keeps its own copy
#: (``KNOWLEDGE_BASE_DOCTYPES``) because it must not import this package, and
#: ``tests/test_knowledge_base_rules.py`` asserts the two agree with each other and with the JSONs.
ARTICLE_DOCTYPE = "Knowledge Article"
VERSION_DOCTYPE = "Knowledge Article Version"
KB_DOCTYPES = frozenset({ARTICLE_DOCTYPE, VERSION_DOCTYPE})

#: The two roles, seeded by ``patches/seed_knowledge_base_roles.py``. Both hold the same DocPerm;
#: what an approver may do is decided in ``workflow.approval_problems``.
AUTHOR_ROLE = "KB Author"
APPROVER_ROLE = "KB Approver"

#: The one role that may read the private mirror's snapshot (WI-080 Slice 6, PR 8:
#: ``api/knowledge_base_mirror.snapshot``), seeded by ``patches/seed_knowledge_base_mirror_role.py``
#: with ``desk_access = 0`` and no DocPerm anywhere. It is a service account's, never a person's, and
#: it confers nothing but that endpoint's role check. A Website User holding it can call nothing else:
#: ``knowledge_base/mirror_guard.py`` (an ``auth_hooks`` entry) refuses every other request it makes.
MIRROR_ROLE = "KB Mirror"

#: An approver is a named person with a staff login: ``User.user_type`` exactly this. Not a
#: "Website User" (a portal contact) and not a custom User Type. ``workflow.approval_problems``
#: takes the value as an argument, so its callers read it from the User row.
APPROVER_USER_TYPE = "System User"

#: Accounts that never approve, whatever roles they hold. Administrator is a shared account, not a
#: person, and holds every role implicitly (v16 ``frappe.get_roles`` returns them all for it,
#: frappe ``origin/version-16`` ``permissions.py:546-547``), so the role rule alone would let it
#: approve; and v16 makes it a System User (``core/doctype/user/user.py:406``), so the user-type
#: rule would not catch it either. The continuity runbook uses Administrator only to grant or revoke
#: KB roles, never to approve. Guest is nobody signed in. Both are refused by name.
NEVER_APPROVERS = ("Administrator", "Guest")

#: The Version fields an author types into, in form order. Every other Version field is set by the
#: Knowledge Base's own code and sits at permlevel 1. Saving a change to any of these makes the
#: saver a contributor, who may not approve the version (``workflow.approval_problems``), and none
#: of them may change once the version has left Draft (``workflow.content_edit_problem``). The
#: rules test asserts this is exactly the Version JSON's level-0 value fields, less ``amended_from``.
VERSION_CONTENT_FIELDS = (
	"title",
	"department_block",
	"kind",
	"summary",
	"keywords",
	"process_owner",
	"review_every_months",
	"body",
	"change_note",
)

#: How many months a published article may go before its process owner must review it, unless
#: the author sets another interval. POL-0001 (Company Documentation - Guiding Principles)
#: mandates a review every six months and lists the Knowledge Base among the company's document
#: types. This is the ``default`` of ``review_every_months`` on both doctypes, and the schema test
#: asserts both JSONs match it, so changing the cadence means changing it here and in both JSONs.
#: A new default reaches new drafts only: every version and article already saved keeps the
#: interval it was written with.
DEFAULT_REVIEW_EVERY_MONTHS = 6

#: The POL-0000 register's department blocks, as ``(two-digit code, label)``. The code is the middle
#: part of an article's number (``SOP-06-0001`` is in 06 Operations; see :func:`article_number`).
DEPARTMENT_BLOCKS = (
	("00", "Company Wide"),
	("01", "Executive"),
	("02", "Design"),
	("03", "Finance"),
	("04", "HR"),
	("05", "Marketing"),
	("06", "Operations"),
	("07", "Product Management"),
	("08", "Production"),
	("09", "Sales"),
)

#: The valid ``department_block`` values, e.g. ``"06 Operations"``. The code leads so that the list
#: sorts in register order and :func:`block_code` needs no lookup table.
DEPARTMENT_BLOCK_OPTIONS = tuple(f"{code} {label}" for code, label in DEPARTMENT_BLOCKS)

#: ``department_block``'s Select ``options`` as the doctype JSONs store them: a **blank first**, then
#: the valid values. v16 gives a Select with no ``default`` its first option on every new document,
#: on the server (``model/create_new.py:117-118``, applied by ``Document._set_defaults``,
#: ``model/document.py:1071-1077``) and in the Desk (``model/create_new.js:107-114``). With
#: ``"00 Company Wide"`` first, ``reqd`` could never fire, and a draft nobody placed would be
#: published into block 00 under an article number that can never be renamed. A blank first option
#: defaults to ``""``, which ``reqd`` refuses, so an author has to choose. Never a valid value:
#: :func:`block_code` answers ``None`` for it.
DEPARTMENT_BLOCK_SELECT_OPTIONS = ("", *DEPARTMENT_BLOCK_OPTIONS)


def block_code(option):
	"""``"06 Operations"`` -> ``"06"``. ``None`` for anything that is not one of the options.

	Strict on purpose: a value that is not an exact option would allocate a number in a block
	nobody chose, so it answers "no block" rather than guessing from the first two characters.
	"""
	if isinstance(option, str) and option in DEPARTMENT_BLOCK_OPTIONS:
		return option[:2]
	return None


def department_option(value):
	"""The ``department_block`` option a person or a tool meant, or ``None``.

	Accepts the option itself (``"06 Operations"``), its code (``"06"`` or ``"6"``), its label
	(``"Operations"``) or its folder (``"06-operations"``), in any case and spacing. Unlike
	:func:`block_code` this is for reading a *filter* someone typed; nothing is ever written from it.
	``None`` for blank and for anything that is not one of the ten blocks.
	"""
	if isinstance(value, bool) or value is None:
		return None
	if isinstance(value, int):
		value = str(value)
	if not isinstance(value, str):
		return None
	return _DEPARTMENT_LOOKUP.get(_fold(value, " "))


def department_folder(option):
	"""``"06 Operations"`` -> ``"06-operations"``: the folder a mirrored article is written under
	(WI-080 Slice 6). Strict, like :func:`block_code`: ``None`` for anything that is not an option."""
	if not isinstance(option, str) or option not in DEPARTMENT_BLOCK_OPTIONS:
		return None
	return re.sub(r"[^0-9a-z]+", "-", option.casefold()).strip("-")


# ------------------------------------------------------------------ the article's kind (PR 5)

#: ``kind`` on both doctypes: the company document register's three document types, each with its
#: own template (POL-0002 Policy, POL-0003 Process, POL-0004 SOP). An article is classified the way
#: the controlled document it replaces or summarizes already is. There is no catch-all and no
#: default kind (decided 2026-09-28): a guide to diagnosing a fault is an SOP (steps for one task),
#: and a rule with its reasons is a Policy.
ARTICLE_KINDS = ("Policy", "Process", "SOP")

#: ``kind``'s Select ``options`` as both JSONs store them: a **blank first**, for the reason
#: :data:`DEPARTMENT_BLOCK_SELECT_OPTIONS` gives. v16 gives a Select with no ``default`` its first
#: option on every new document, so with ``Policy`` first every new draft would start classified.
#: Neither doctype makes the field ``reqd``: ``workflow.submit_problems`` requires it instead, because
#: v16 checks ``reqd`` on every save of a submitted version too, and a version published before the
#: field existed could then never be superseded (WI-080, "Found while designing Slice 3", 3).
KIND_SELECT_OPTIONS = ("", *ARTICLE_KINDS)

#: One line per kind: what it is, as a reader or a model should use it. It feeds the field's
#: description (:func:`kind_description`, which the schema test holds both JSONs to) and, from PR 6a,
#: the AI tools' schemas.
KIND_HELP = {
	"Policy": "a rule the company requires: what must or must not be done, and why.",
	"Process": (
		"how work flows across roles and stages: who does what, in what order, and where it is "
		"handed off."
	),
	"SOP": "step-by-step instructions for one task, followed in order.",
}

#: Other words people use for a kind, as :func:`kind_option` reads them (after :func:`_fold`, so
#: "How to", "how_to" and "HOW-TO" are all ``how-to``). ``pol`` and ``pro`` are the register's own
#: number prefixes (POL-, PRO-). "Procedure" is an SOP: SOP stands for standard operating procedure,
#: and in the register an SOP is the procedure for one task, while a Process is the flow across
#: roles those tasks sit inside, which is also what "workflow" means. ``sop`` needs no alias: it is
#: the kind itself. Search also indexes these words with each article (``search.py``, the meta
#: field), so "procedure for receiving" reaches an SOP.
KIND_ALIASES = {
	"pol": "Policy",
	"policies": "Policy",
	"rule": "Policy",
	"rules": "Policy",
	"pro": "Process",
	"processes": "Process",
	"workflow": "Process",
	"workflows": "Process",
	"sops": "SOP",
	"procedure": "SOP",
	"procedures": "SOP",
	"how-to": "SOP",
	"howto": "SOP",
	"how-tos": "SOP",
	"instructions": "SOP",
	"standard-operating-procedure": "SOP",
}


def kind_option(value):
	"""The kind ``value`` names: a kind in any case (``"sop"`` -> ``"SOP"``) or one of
	:data:`KIND_ALIASES`. ``None`` for blank and for any other word. For reading a filter someone
	typed; what is *stored* is only ever one of :data:`ARTICLE_KINDS`, which v16's Select validation
	enforces (``model/base_document.py:1101-1126``)."""
	if not isinstance(value, str):
		return None
	key = _fold(value, "-")
	if not key:
		return None
	for kind in ARTICLE_KINDS:
		if key == kind.casefold():
			return kind
	return KIND_ALIASES.get(key)


def kind_description():
	"""The ``kind`` field's description on both doctypes: the three kinds, one clause each, and how
	readers and AI tools use them. The schema test compares both JSONs with this."""
	lines = [f"{kind}: {KIND_HELP[kind]}" for kind in ARTICLE_KINDS]
	lines.append("Readers and AI tools use it: a Policy is binding, and an SOP's steps are followed in order.")
	return " ".join(lines)


# ------------------------------------------------------------------ the article number (2026-09-29)

#: Each kind's number prefix: the company register's own (POL-, PRO-, SOP-), so an article's number
#: says what it is. Nik, 2026-09-29: "KB-#### is too limiting"; he chose ``SOP-06-0001`` by kind.
KIND_PREFIXES = {"Policy": "POL", "Process": "PRO", "SOP": "SOP"}
PREFIX_KINDS = {prefix: kind for kind, prefix in KIND_PREFIXES.items()}

#: The highest sequence a ``(prefix, department)`` scope can reach: four digits.
MAX_SEQUENCE = 9999

#: An article number **as stored**: ``<PREFIX>-<DD>-<NNNN>``, e.g. ``SOP-06-0001`` (the kind's prefix,
#: the department block's code, a four-digit sequence). Canonical and case-sensitive; use it with
#: ``fullmatch``. ``[0-9]``, never ``\d``, which also matches other scripts' digits. The pattern alone
#: also matches ``SOP-06-0000`` and ``SOP-42-0001``: :func:`parse_article_number` is the whole rule.
#:
#: **A published article's number never changes, and a number is never reused.** Its kind and
#: department are part of it, so they never change either: to reclassify an article or move it to
#: another department, a new article is published and the old one retired, naming the new one, so
#: every citation of the old number still leads somewhere (Nik, 2026-09-29).
ARTICLE_NUMBER = re.compile(r"(POL|PRO|SOP)-([0-9]{2})-([0-9]{4})")

#: An article number **as people write one**, as pattern text: compiled with ``re.IGNORECASE`` over
#: NFKC-normalized text (so full-width letters and digits read as plain ones). The separators are a
#: space, ``_``, ``-`` or U+2010 to U+2013 (hyphen, non-breaking hyphen, figure dash, en dash: Word and
#: Docs turn a typed ``-`` into these in pasted text). The one between the prefix and the department is
#: optional; the one between the department and the sequence is **required**, so the Drive register's
#: own ``PREFIX-DDNN`` numbers (``SOP-0601``, ``POL-0600``) are never read as article numbers. The
#: department part, ``0?[0-9]``, accepts exactly the codes 00 to 09 (a test holds it to
#: :data:`DEPARTMENT_BLOCKS`, so a block ``10`` fails the build until this is widened). So
#: ``sop 06 0001``, ``SOP-06-1``, ``sop_6_1``, ``SOP06-0001`` and ``SOP-06-00001`` all read as
#: ``SOP-06-0001``; :func:`normalize_article_number` refuses a match whose sequence is 0 or past 9999.
#:
#: This loose form is for text meant **as** a number, or a query naming one. Running text (an article's
#: body, a lesson) holds ordinary words shaped like it, such as a product's ``Pro 2 1000``, so what it
#: *cites* is read more strictly: :func:`cited_article_numbers`.
ARTICLE_NUMBER_WRITTEN = r"\b(pol|pro|sop)[ _\-‐-–]?(0?[0-9])[ _\-‐-–](0*[0-9]{1,4})\b"
_WRITTEN_NUMBER = re.compile(ARTICLE_NUMBER_WRITTEN, re.IGNORECASE)

_BLOCK_CODES = frozenset(code for code, _label in DEPARTMENT_BLOCKS)


def article_number(kind, block, sequence):
	"""``("SOP", "06 Operations", 1)`` -> ``"SOP-06-0001"``.

	``kind`` is one of :data:`ARTICLE_KINDS`; ``block`` a ``department_block`` option or its two-digit
	code; ``sequence`` a whole number from 1 to :data:`MAX_SEQUENCE`. Raises ``ValueError``, in words,
	for anything else: a number is never made from a guess."""
	if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= MAX_SEQUENCE:
		raise ValueError(f"{sequence!r} is not an article sequence: it runs from 1 to {MAX_SEQUENCE}.")
	return f"{number_scope(kind, block)}{sequence:04d}"


def number_scope(kind, block):
	"""``("SOP", "06 Operations")`` -> ``"SOP-06-"``: the part of the number the kind and department
	fix. Each scope has its own sequence, as the register numbers each document type separately, so
	``POL-06-0001``, ``PRO-06-0001`` and ``SOP-06-0001`` can all exist. Publishing binds
	``scope + "%"`` in its ``LIKE``, which is safe because a scope holds no ``%`` or ``_``. Raises
	``ValueError`` for a kind or block that is not one of the options."""
	prefix = KIND_PREFIXES.get(kind) if isinstance(kind, str) else None
	if prefix is None:
		raise ValueError(
			f"{kind!r} is not a kind, so no article number can be made from it: it needs Policy, "
			"Process or SOP."
		)
	return f"{prefix}-{_code_of(block)}-"


def parse_article_number(text):
	"""``"SOP-06-0001"`` -> ``("SOP", "06", 1)``: the kind, the department code and the sequence.
	``None`` for anything that is not a canonical article number, exactly as stored: a lowercase
	spelling, surrounding space, a department that is not a block, and a sequence of 0 are all
	``None``. For reading what people write, :func:`normalize_article_number`."""
	if not isinstance(text, str):
		return None
	match = ARTICLE_NUMBER.fullmatch(text)
	if match is None:
		return None
	prefix, code, digits = match.groups()
	sequence = int(digits)
	if code not in _BLOCK_CODES or not 1 <= sequence <= MAX_SEQUENCE:
		return None
	return PREFIX_KINDS[prefix], code, sequence


def normalize_article_number(text):
	"""The article number the **whole** of ``text`` spells, canonical (``"sop 06 1"`` ->
	``"SOP-06-0001"``); ``None`` when it spells none. So a version's id (``KBV-00001``), a register
	number (``SOP-0601``), one in the retired ``KB`` format and ``"SOP-06-0001 and more"`` are all
	``None``."""
	if not isinstance(text, str):
		return None
	match = _WRITTEN_NUMBER.fullmatch(unicodedata.normalize("NFKC", text).strip())
	return _canonical(match) if match else None


def written_article_numbers(text):
	"""Every article number written in ``text``, canonical, in order of first mention, each once, read
	as loosely as :func:`normalize_article_number` reads one. For a query, where a number is meant: in
	running text use :func:`cited_article_numbers`. A register number (``POL-0600``) and one in the
	retired ``KB`` format are not article numbers, and are not listed."""
	return _numbers_in(text, cited=False)


def cited_article_numbers(text):
	"""Every article number ``text`` **cites**, as :func:`written_article_numbers` lists them, except
	that a space may separate the parts only when the department is written with two digits. So
	``SOP-06-0001``, ``sop 06 0001``, ``SOP-6-1`` and ``sop_6_1`` are citations, and ``Pro 2 1000`` (a
	product's name), ``pro 5 10 times`` and ``SOP 1 2 3`` are words. For running text: an article's
	body, what fetch lists as ``related``, a lesson."""
	return _numbers_in(text, cited=True)


def _numbers_in(text, cited):
	if not isinstance(text, str) or not text:
		return []
	numbers = []
	for match in _WRITTEN_NUMBER.finditer(unicodedata.normalize("NFKC", text)):
		# The only whitespace a match can hold is a separator's space. Nothing is lost by skipping a
		# match here rather than in the pattern: no other number can start inside one.
		if cited and len(match.group(2)) == 1 and " " in match.group(0):
			continue
		number = _canonical(match)
		if number is not None and number not in numbers:
			numbers.append(number)
	return numbers


def _canonical(match):
	"""A match of :data:`ARTICLE_NUMBER_WRITTEN`, canonical; ``None`` when its sequence is 0 or past
	:data:`MAX_SEQUENCE`."""
	prefix, code, digits = match.group(1).upper(), f"{int(match.group(2)):02d}", int(match.group(3))
	if code not in _BLOCK_CODES or not 1 <= digits <= MAX_SEQUENCE:
		return None
	return f"{prefix}-{code}-{digits:04d}"


def _code_of(block):
	"""A department block's two-digit code, from its option (``"06 Operations"``) or the code itself
	(``"06"``). Strict, like :func:`block_code`: raises ``ValueError`` for anything else."""
	code = block_code(block)
	if code is None and isinstance(block, str) and block in _BLOCK_CODES:
		code = block
	if code is None:
		raise ValueError(f"{block!r} is not a department block.")
	return code


def _fold(value, separator):
	"""NFKC, casefold, trim, and each run of spaces, ``_`` and ``-`` as one ``separator``."""
	text = unicodedata.normalize("NFKC", value).casefold().strip()
	return re.sub(r"[\s_\-]+", separator, text).strip(separator)


#: What :func:`department_option` accepts, folded: each block's code (``06`` and ``6``), label and
#: option. A folder name (``06-operations``) folds to its option's key.
_DEPARTMENT_LOOKUP = {
	_fold(key, " "): f"{code} {label}"
	for code, label in DEPARTMENT_BLOCKS
	for key in (code, str(int(code)), label, f"{code} {label}")
}

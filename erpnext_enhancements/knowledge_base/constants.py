"""The Knowledge Base's fixed vocabulary, defined once (WI-080 PR 1).

Every value a Select field on the two KB doctypes can hold lives here, and
``tests/test_knowledge_base_schema.py`` asserts that each doctype JSON's ``options`` equal these
tuples exactly (``department_block`` with the blank it stores first, see
``DEPARTMENT_BLOCK_SELECT_OPTIONS``). The publishing code that arrives in later PRs writes these values, so it imports
them from here rather than typing the strings again: a misspelt state written by code is a row
that saves cleanly, never matches a filter, and is only found when somebody counts.

Standard library only, so the schema test and the pure rules modules can import it without a
bench.
"""

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

#: The POL-0000 register's department blocks, as ``(two-digit code, label)``. A KB number is
#: ``KB-{code}{01..99}``, and ``{code}00`` is reserved as that block's index, following the
#: register's own convention (xx00 is the group's Roles & Responsibilities).
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
#: published into block 00 under a KB number that can never be renamed. A blank first option
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

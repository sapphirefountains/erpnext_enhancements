"""The Knowledge Base's fixed vocabulary, defined once (WI-080 PR 1).

Every value a Select field on the two KB doctypes can hold lives here, and
``tests/test_knowledge_base_schema.py`` asserts that each doctype JSON's ``options`` equal these
tuples exactly. The publishing code that arrives in later PRs writes these values, so it imports
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
#: - In Review: submitted to a named KB Approver; content edits are refused (PR 2).
#: - Published: approved and submitted; this is the live text.
#: - Superseded: was Published; a newer version replaced it. Kept as history.
#: - Discarded: abandoned before publishing. Kept, never deleted.
REVIEW_STATES = ("Draft", "In Review", "Published", "Superseded", "Discarded")

#: The two states in which a version is still being worked on. At most one per article (PR 2).
OPEN_REVIEW_STATES = ("Draft", "In Review")

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

#: ``department_block`` Select options exactly as stored, e.g. ``"06 Operations"``. The code
#: leads so that the list sorts in register order and :func:`block_code` needs no lookup table.
DEPARTMENT_BLOCK_OPTIONS = tuple(f"{code} {label}" for code, label in DEPARTMENT_BLOCKS)


def block_code(option):
	"""``"06 Operations"`` -> ``"06"``. ``None`` for anything that is not one of the options.

	Strict on purpose: a value that is not an exact option would allocate a number in a block
	nobody chose, so it answers "no block" rather than guessing from the first two characters.
	"""
	if isinstance(option, str) and option in DEPARTMENT_BLOCK_OPTIONS:
		return option[:2]
	return None

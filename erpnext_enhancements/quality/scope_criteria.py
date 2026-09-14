"""What makes an acceptance criterion inspectable — the rules a Scope of Work locks against.

Row identity moved to :mod:`erpnext_enhancements.quality.stable_keys` once inspection checks
needed the same thing; ``mint_missing_keys`` and ``duplicate_keys`` are re-exported here so
callers that only care about criteria have one import.

What stays here is the part that is genuinely about *scope* rather than about rows: a criterion
that cannot be inspected as written should not be lockable, because a locked scope with
unmeasurable criteria in it is the failure this whole record exists to end.

Imports no ``frappe``. There is no Frappe integration-test job in CI, so this split is what lets
``tests/test_scope_criteria.py`` run on every push. It lives under ``quality/`` rather than
beside the DocType that uses it because ``project_enhancements/__init__.py`` imports ``frappe``
at module scope, which would put it out of reach however pure it was — the same split
``utils/url_safety.py`` makes, and for the same stated reason.
"""

from erpnext_enhancements.quality.stable_keys import (
	KEY_LENGTH,
	_get,
	duplicate_keys,
	mint_missing_keys,
)

#: Fields a criterion must carry before its parent can be locked. Held here rather than as
#: ``reqd`` on the child JSON alone because ``reqd`` is enforced per row as it is typed, and the
#: message that matters is the one at submit time: this row cannot be inspected as written.
REQUIRED_FOR_LOCK = ("criterion", "pass_standard", "verification_method")


def incomplete_rows(rows):
	"""``[(idx, [missing field, ...]), ...]`` for rows that cannot be inspected as written.

	``idx`` is 1-based to match what the grid shows the user, and rows come back in grid order
	so the message reads top to bottom.

	The blank test is ``strip()`` in Python and not a SQL comparison, on purpose. Under
	MariaDB's default PAD SPACE collation a non-binary comparison ignores trailing spaces, so
	``WHERE pass_standard <> ''`` treats ``"   "`` as present. Note the failure direction of
	that: the check **passes**, the criterion looks measurable, and nobody goes looking.
	"""
	problems = []
	for position, row in enumerate(rows or [], start=1):
		idx = _get(row, "idx") or position
		missing = [f for f in REQUIRED_FOR_LOCK if not (_get(row, f) or "").strip()]
		if missing:
			problems.append((idx, missing))
	return problems

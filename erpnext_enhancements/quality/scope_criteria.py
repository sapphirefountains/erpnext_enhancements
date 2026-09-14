"""Stable identity for acceptance criteria — the field the whole quality chain joins on.

Every downstream record in WI-075 points at *a criterion*, not at a row position: the
inspection template row that carries it, the inspection result that records a pass or fail,
the NCR raised when it fails, and the Quality Action that closes it out. Those links have to
survive somebody inserting a criterion in the middle of the list a year later.

So each row carries a ``criterion_key``: minted once on first save, read-only, **never
regenerated**. Nothing joins on ``idx``, and nothing joins on the criterion text.

The precedent is ``block_key`` in the Training module, and its README states the property this
file exists to provide in one line: *everything joins on stable ``*_key`` values rather than
``idx``, which is why reordering blocks never orphans a learner's in-flight progress.* Here the
stakes are the same shape but the record is legal rather than pedagogical — a closed NCR that
silently repoints at a different standard is an audit failure, not an inconvenience.

Why this is a module and not four lines in the controller
----------------------------------------------------------

There is no Frappe integration-test job in CI, so anything living inside a DocType controller
is untestable on this repo until somebody runs a bench by hand. These functions take plain
sequences of dicts (or of Frappe child rows — both are supported), import no ``frappe``, and
have no I/O, so ``tests/test_scope_criteria.py`` exercises them on every push.

And why it sits in ``quality/`` rather than beside the DocType that uses it, which is
``Project Scope of Work`` over in ``project_enhancements``: two reasons, and the second is the
load-bearing one.

The criterion key is the **quality chain's** join key. The scope record lives with the
contracts because that is what it is contractually, but the identity rules belong to the thing
that consumes them — the inspection template, the inspection result, the NCR, the Quality
Action.

And ``project_enhancements/__init__.py`` imports ``frappe`` at module scope, so anything under
that package is unreachable without a bench, however pure the module itself is. ``quality/`` has
an empty ``__init__``. That importability is not an accident of layout, it is the requirement —
the same split ``utils/url_safety.py`` makes, and for the same stated reason.
"""

KEY_LENGTH = 10

#: Fields a criterion must carry before its parent can be locked. Held here rather than as
#: ``reqd`` on the child JSON alone because ``reqd`` is enforced per row as it is typed, and the
#: message that matters is the one at submit time: this row cannot be inspected as written.
REQUIRED_FOR_LOCK = ("criterion", "pass_standard", "verification_method")


def _get(row, field):
	"""Read ``field`` from a dict or from a Frappe child row, without caring which."""
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def _set(row, field, value):
	if isinstance(row, dict):
		row[field] = value
	else:
		setattr(row, field, value)


def mint_missing_keys(rows, generator):
	"""Give every row without a ``criterion_key`` one. Returns the keys minted.

	``generator`` is injected — in production it is ``frappe.generate_hash``; in tests it is a
	counter — because a function that reaches for a global random source cannot be asserted on.

	A row that already has a key is left **exactly** as it is. That is the entire contract of
	this function: re-running it on a saved document must be a no-op, or every save would
	silently orphan whatever pointed at the old key.
	"""
	minted = []
	for row in rows or []:
		if _get(row, "criterion_key"):
			continue
		key = generator(length=KEY_LENGTH)
		_set(row, "criterion_key", key)
		minted.append(key)
	return minted


def duplicate_keys(rows):
	"""Keys appearing on more than one row, sorted. Empty when the table is sound.

	Duplicates are not a theoretical worry: Frappe's "duplicate row" grid action copies every
	field including the read-only ones, so two rows sharing a key is a couple of clicks away.
	A downstream join would then resolve to whichever row it happened to read first.
	"""
	seen = {}
	for row in rows or []:
		key = _get(row, "criterion_key")
		if not key:
			continue
		seen[key] = seen.get(key, 0) + 1
	return sorted(key for key, count in seen.items() if count > 1)


def incomplete_rows(rows):
	"""``[(idx, [missing field, ...]), ...]`` for rows that cannot be inspected as written.

	``idx`` is 1-based to match what the grid shows the user. Rows are reported in grid order so
	the message reads top to bottom.
	"""
	problems = []
	for position, row in enumerate(rows or [], start=1):
		idx = _get(row, "idx") or position
		missing = [f for f in REQUIRED_FOR_LOCK if not (_get(row, f) or "").strip()]
		if missing:
			problems.append((idx, missing))
	return problems

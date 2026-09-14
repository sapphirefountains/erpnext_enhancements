"""Stable row identity — the thing every join in the quality chain hangs off.

Three different child tables in this programme carry a key that is minted once and never
regenerated: an acceptance criterion on a locked Scope of Work, a check on an Inspection
Section, and (in sub-phase D) a result row on a generated inspection. They are the same idea
each time, so they are one function here rather than three near-copies.

The property is always the same, and it is the whole reason the field exists: **a key, once
minted, never changes.** Rows get reordered, reworded, inserted between other rows and deleted
from the middle, and everything pointing at them has to survive all of that. Nothing joins on
``idx``, and nothing joins on the human-readable text.

The precedent is ``block_key`` in the Training module, whose README puts the property in one
line: *everything joins on stable ``*_key`` values rather than ``idx``, which is why reordering
blocks never orphans a learner's in-flight progress.* Here the stakes are contractual rather
than pedagogical — a closed NCR that silently repoints at a different standard is an audit
failure, not an inconvenience.

Imports no ``frappe``, deliberately: there is no Frappe integration-test job in CI, so logic
inside a DocType controller does not run until somebody starts a bench by hand. These functions
take plain sequences of dicts or of Frappe child rows, and are exercised on every push.
"""

KEY_LENGTH = 10


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


def mint_missing_keys(rows, generator, key_field="criterion_key"):
	"""Give every row without a key one. Returns the keys minted, in row order.

	``generator`` is injected — in production it is ``frappe.generate_hash``; in tests it is a
	counter — because a function that reaches for a global random source can be observed but
	not asserted on.

	A row that already has a key is left **exactly** as it is. That is the entire contract:
	re-running this on a saved document must be a no-op, or every save would silently orphan
	whatever pointed at the old key.
	"""
	minted = []
	for row in rows or []:
		if _get(row, key_field):
			continue
		key = generator(length=KEY_LENGTH)
		_set(row, key_field, key)
		minted.append(key)
	return minted


def duplicate_keys(rows, key_field="criterion_key"):
	"""Keys appearing on more than one row, sorted. Empty when the table is sound.

	Duplicates are not a theoretical worry: Frappe's "duplicate row" grid action copies every
	field including the read-only ones, so two rows sharing a key is a couple of clicks away.
	A downstream join would then resolve to whichever row it happened to read first.
	"""
	seen = {}
	for row in rows or []:
		key = _get(row, key_field)
		if not key:
			continue
		seen[key] = seen.get(key, 0) + 1
	return sorted(key for key, count in seen.items() if count > 1)

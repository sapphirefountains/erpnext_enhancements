"""Restore audit rows to exactly what they signed, by clearing a value written after the hash.

--------------------------------------------------------------------------------------
What went wrong
--------------------------------------------------------------------------------------

``reason_category`` is in :data:`chat.audit._OPTIONAL_CHAINED_FIELDS`, so the writer signs it
on any row that carries one. Until v1.307.0 the retrieval verifier's SELECT was written out by
hand and did not include the column, so the two sides only agreed because no caller ever
supplied a value — and ``chat.governance.viewer._stamp_category`` then wrote the column onto
the row with ``frappe.db.set_value`` **after** the gate had hashed it, justified by a comment
claiming "re-signing happens on the next verify". Nothing re-signs.

The result is a population of rows whose stored ``chain_hash`` commits to a payload with no
``reason_category`` key while the column holds a value. v1.307.0 makes both verifiers derive
their columns from the signed tuples, which is the fix — and the moment they do, every one of
those rows reports as the chain's ``first_break``. ``verify_chain`` returns at the first break,
so **every row after it stops being checked at all**, and the nightly ``verify_all_chains``
raises a SEVERITY_CRITICAL alert that would never clear.

--------------------------------------------------------------------------------------
Why clearing, and not re-signing
--------------------------------------------------------------------------------------

The honest repair is to put each row back to what it actually signed. The alternative —
recomputing ``chain_hash`` over the current contents — is not a repair at all: a log that
re-signs itself signs whatever it currently says, which is a checksum over the present rather
than a record of the past. That is the same non-mechanism the deleted comment appealed to.

What is lost is real and small: on those rows the subject-facing transparency view shows "Not
recorded" instead of a category. That value never had evidential weight — nothing vouched for
it — and the free-text ``reason`` beside it is signed and untouched.

--------------------------------------------------------------------------------------
The predicate, and why it is not "where the column is non-empty"
--------------------------------------------------------------------------------------

A row is cleared **only if** it fails to verify with its category and succeeds without it.
That is keyed on *the rule the writer applied*, not on emptiness — CLAUDE.md's
``Chat Relay Job.auth_identity`` lesson (v1.280.3) is that an emptiness predicate describes the
schema migration rather than the data, and can match nothing while logging itself a success.
Here it would be worse than useless: the rows needing repair are precisely the non-empty ones,
and clearing all of them would destroy correctly-signed categories written by v1.307.0 if this
patch is ever re-run after new rows exist.

It cannot launder tampering. A row edited in any other field fails **both** variants, so the
patch stops there and changes nothing further — deliberately loud, because a repair sweep that
walks past a real break is how a break gets buried. A row where somebody *added* a category to
an uncategorised row satisfies the predicate and is cleared, which is the correct outcome: that
value was never signed and was never evidence.

Clearing does not disturb the chain. ``chain_hash`` is never written, and each row's
``previous`` is the **stored** hash of the row before it, so nothing cascades.

--------------------------------------------------------------------------------------
Both tables
--------------------------------------------------------------------------------------

``Chat Audit Log`` is swept with the identical predicate. ``record_governance_event`` has no
``reason_category`` parameter, so on that chain the column cannot ever have been signed and any
non-empty value in it is unsigned by construction — the category travels inside ``detail``
there, which *is* a mandatory chained field. The sweep is expected to find nothing; it exists so
that v1.307.0's matching change to ``verify_governance_chain`` cannot raise a false critical on
a value somebody set by hand.

Safe to run twice: the second run finds every row verifying as stored and writes nothing.
"""

import frappe

from erpnext_enhancements.chat import audit


def _clear(doctype: str, name: str) -> None:
	"""Blank the column without touching ``modified`` or ``chain_hash``.

	``update_modified=False`` for the same reason the write being undone used it: ``modified``
	is not a signed field, but moving it on an audit row makes a repair look like activity.
	"""
	frappe.db.set_value(doctype, name, "reason_category", None, update_modified=False)


def _sweep_retrieval() -> dict[str, int]:
	columns = audit._select_columns(
		audit._VERIFY_EXTRA_COLUMNS, audit._CHAINED_FIELDS, audit._OPTIONAL_CHAINED_FIELDS
	)
	rows = frappe.db.sql(
		f"""select {columns}
			from `tabChat Retrieval Audit`
			order by `recorded_at` asc, `name` asc""",
		as_dict=True,
	)
	kids: dict[str, list[dict]] = {}
	if rows:
		for kid in frappe.db.sql(
			"""select `parent`, `room`, `was_participant`, `messages_read`, `first_seq`,
					`last_seq`, `oldest_message_ts`, `newest_message_ts`
				from `tabChat Retrieval Audit Room`
				where `parent` in %(parents)s
				order by `parent` asc, `idx` asc""",
			{"parents": tuple(r["name"] for r in rows)},
			as_dict=True,
		):
			kids.setdefault(kid["parent"], []).append(dict(kid))

	previous = audit._GENESIS
	cleared = 0
	for row in rows:
		stored = row.get("chain_hash") or ""
		as_read = dict(row)
		if audit.compute_chain_hash(as_read, previous, kids.get(row["name"], [])) != stored:
			without = dict(as_read)
			without["reason_category"] = None
			carries_one = str(as_read.get("reason_category") or "").strip()
			if carries_one and audit.compute_chain_hash(
				without, previous, kids.get(row["name"], [])
			) == stored:
				_clear("Chat Retrieval Audit", row["name"])
				cleared += 1
			else:
				# Neither variant matches, so this is not the defect being repaired. Stop:
				# every row after a break is suspect, and sweeping past one would quietly
				# rewrite rows inside a range nobody has explained yet.
				frappe.log_error(
					title="chat audit repair: stopped at a genuine break",
					message=(
						f"Chat Retrieval Audit {row['name']} verifies neither with nor without "
						f"its reason_category. Cleared {cleared} row(s) before it. Run "
						f"erpnext_enhancements.chat.audit.verify_chain and investigate."
					),
				)
				return {"cleared": cleared, "stopped_at_break": 1}
		previous = stored

	return {"cleared": cleared, "stopped_at_break": 0}


def _sweep_governance() -> dict[str, int]:
	columns = audit._select_columns(
		audit._GOVERNANCE_VERIFY_EXTRA_COLUMNS,
		audit._GOVERNANCE_CHAINED_FIELDS,
		audit._OPTIONAL_CHAINED_FIELDS,
	)
	rows = frappe.db.sql(
		f"""select {columns}
			from `tabChat Audit Log`
			order by `recorded_at` asc, `name` asc""",
		as_dict=True,
	)
	previous = audit._GOVERNANCE_GENESIS
	cleared = 0
	for row in rows:
		stored = row.get("chain_hash") or ""
		if audit.compute_governance_chain_hash(dict(row), previous) != stored:
			without = dict(row)
			without["reason_category"] = None
			carries_one = str(row.get("reason_category") or "").strip()
			if carries_one and audit.compute_governance_chain_hash(without, previous) == stored:
				_clear("Chat Audit Log", row["name"])
				cleared += 1
			else:
				frappe.log_error(
					title="chat audit repair: stopped at a genuine break",
					message=(
						f"Chat Audit Log {row['name']} verifies neither with nor without its "
						f"reason_category. Cleared {cleared} row(s) before it. Run "
						f"erpnext_enhancements.chat.audit.verify_governance_chain."
					),
				)
				return {"cleared": cleared, "stopped_at_break": 1}
		previous = stored

	return {"cleared": cleared, "stopped_at_break": 0}


def execute() -> None:
	for doctype in ("Chat Retrieval Audit", "Chat Audit Log"):
		if not frappe.db.table_exists(doctype):
			return
		# The column is read on both sides of the comparison below. On a site where the
		# migration adding it has not landed, doing nothing is right; guessing is not.
		if not frappe.db.has_column(doctype, "reason_category"):
			return

	retrieval = _sweep_retrieval()
	governance = _sweep_governance()
	frappe.db.commit()

	print(
		f"chat audit reason_category repair: retrieval cleared={retrieval['cleared']} "
		f"stopped={retrieval['stopped_at_break']}, governance cleared={governance['cleared']} "
		f"stopped={governance['stopped_at_break']}"
	)

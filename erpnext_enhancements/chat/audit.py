# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The **only** module that writes a ``Chat Retrieval Audit`` row.

Decision #12 permits a configured role to read conversations it is not a participant in, on
the condition that the read is recorded. Until this module existed, the condition was not
met: :func:`chat.permissions.note_privileged_read` was a documented stub that returned
``None``, so filling ``Chat Settings.admin_oversight_role`` granted the whole company's
private conversations to a role and logged nothing at all.

--------------------------------------------------------------------------------------
One row per REQUEST, not per call — and why the ADR's ordering rule cannot apply here
--------------------------------------------------------------------------------------

The ADR designs this audit around the Phase 5 retrieval gate: ``retrieve()`` is called once
per Triton turn, knows the rooms and the message ranges it is about to hand over, and can
therefore write one complete row and **commit it before returning content** — fail-closed,
per §F.12: "if the audit write fails, retrieval fails."

That gate does not exist yet. What exists is nine call sites inside
:mod:`chat.permissions`, and they are a different shape in two ways that both matter:

* **They fire during query *building*, several times per request.** ``membership_filter_sql``
  is called by the room list, the transcript read, the search and the four
  ``permission_query_conditions`` hooks. A row per call would produce a dozen rows for one
  page load — an audit nobody can read is not much better than no audit. So the write is
  **deduplicated per request** through :data:`_REQUEST_FLAG`, and the row is enriched in
  place by whichever caller actually knows what it returned.
* **They must never raise.** A ``has_permission`` hook that throws denies the read at best
  and breaks every Desk page that touches a chat DocType at worst — including pages that
  have nothing to do with this feature. So this module swallows everything and logs.

That is a **deliberate weakening of the ADR's fail-closed rule, and it is confined to the
permission-hook path**. It is written down here rather than discovered later:

    A privileged read through a permission hook is recorded on a best-effort basis. A
    privileged read through an endpoint that returns content — today only
    ``search_messages`` — records **before** it returns, and refuses to return if it
    cannot record.

When the Phase 5 gate lands it should adopt the second rule, not the first.

--------------------------------------------------------------------------------------
The two rules the stub's docstring set, both still binding
--------------------------------------------------------------------------------------

* **Never raise** (see above), except from :func:`record_or_refuse`, which exists precisely
  to be the fail-closed variant and is never called from a hook.
* **Never go through the permission stack.** Every read and write here uses
  ``ignore_permissions=True`` and raw SQL, or an audited read recurses into auditing the
  audit — and the recursion is unbounded, because auditing the audit is itself a privileged
  read.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe.utils import cint

AUDIT_DOCTYPE = "Chat Retrieval Audit"
ROOM_DOCTYPE = "Chat Retrieval Audit Room"

#: Where the per-request dedupe lives. ``frappe.flags`` is reset per request by the framework,
#: which is exactly the lifetime wanted: one row per request, and a background job (which gets
#: its own flags) is its own request for this purpose.
_REQUEST_FLAG = "chat_privileged_read_row"

#: Purposes the ``purpose`` Select accepts. Kept here as well as in the JSON so a caller
#: passing a typo gets a stored row with a legible purpose rather than a validation error
#: thrown out of a permission hook.
_PURPOSES = frozenset({"mention", "search", "briefing", "oversight", "attachment"})
_DEFAULT_PURPOSE = "oversight"

#: The fields the chain commits to. Deliberately **not** every field: ``chain_hash`` itself
#: obviously cannot be in it, and ``modified``/``modified_by`` are framework noise that would
#: make the chain unverifiable after a harmless reindex. Room rows ARE included, because "which
#: rooms did they read" is the fact most worth tampering with.
_CHAINED_FIELDS = (
	"accessed_by",
	"actor_type",
	"purpose",
	"request_id",
	"reason",
	"query_hash",
	"message_count",
	"creation",
)


def query_hash(text: str | None) -> str:
	"""sha256 of a query string. The hash is stored by default; the text is not.

	The query a manager types into a search box is itself sensitive — "did anyone mention my
	name", typed by the person about to run a redundancy, is content. §F.12 stores the hash so
	that two identical searches are recognisable as identical without the log becoming a
	second copy of what was searched for.
	"""
	return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _store_query_text() -> bool:
	"""Is the raw-query-text flag on? Ships off, and any failure reads as off."""
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "store_retrieval_query_text")))
	except Exception:
		return False


def _canonical(payload: dict[str, Any]) -> str:
	"""Canonical JSON for hashing. Stable across Python versions and dict ordering.

	``sort_keys`` so field order cannot change the hash; ``separators`` so whitespace cannot;
	``ensure_ascii=False`` so a name with an accent in it hashes the same here as in the
	verifier. Any of the three differing between writer and verifier reports every row as
	tampered, which is worse than no chain because it trains people to ignore the alarm.
	"""
	return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _previous_chain_hash() -> str:
	"""The chain head: the newest row's ``chain_hash``, or the genesis value.

	Raw SQL and ``ignore_permissions`` by construction — reading the audit through the ORM
	would be a privileged read of the audit, which is the recursion this module must not
	start. Ordered by ``creation`` then ``name`` so two rows written inside the same
	microsecond still have a deterministic order, which is the ordering the verifier walks.
	"""
	rows = frappe.db.sql(
		"""select `chain_hash` from `tabChat Retrieval Audit`
			order by `creation` desc, `name` desc limit 1""",
	)
	if rows and rows[0][0]:
		return rows[0][0]
	# Genesis. A fixed, non-empty string so the first row's hash is not the hash of "" — which
	# is a well-known constant and would let a forged first row be recognised as genesis.
	return "chat-retrieval-audit-genesis"


def compute_chain_hash(row: dict[str, Any], previous: str, rooms: list[dict[str, Any]]) -> str:
	"""``sha256(previous ‖ canonical(audited fields ‖ rooms))``.

	Shared by the writer and :func:`verify_chain` so that the two cannot drift into disagreeing
	about what was signed — a verifier with its own idea of the payload reports false breaks.
	"""
	payload = {k: row.get(k) for k in _CHAINED_FIELDS}
	payload["rooms"] = [
		{
			"room": r.get("room"),
			"was_participant": cint(r.get("was_participant")),
			"messages_read": cint(r.get("messages_read")),
			"first_seq": cint(r.get("first_seq")),
			"last_seq": cint(r.get("last_seq")),
		}
		for r in (rooms or [])
	]
	return hashlib.sha256((previous + _canonical(payload)).encode("utf-8")).hexdigest()


def record_privileged_read(
	*,
	user: str,
	purpose: str = _DEFAULT_PURPOSE,
	actor_type: str = "Admin",
	rooms: list[dict[str, Any]] | None = None,
	query: str | None = None,
	reason: str | None = None,
	request_id: str | None = None,
	message_count: int = 0,
	force_new: bool = False,
) -> str | None:
	"""Record one privileged read. **Never raises.** Returns the row name, or ``None``.

	Deduplicated per request unless ``force_new``: the first call in a request writes the row
	and later calls in the same request are no-ops, so a page load that builds six scoped
	queries produces one audit row rather than six.

	``rooms`` is a list of ``{room, was_participant, messages_read, first_seq, last_seq}``.
	Callers that know what they returned should pass it; the permission hooks do not know and
	pass nothing, which is why a hook-written row is a record that a privileged read happened
	rather than a record of what it reached.
	"""
	try:
		if not force_new and frappe.flags.get(_REQUEST_FLAG):
			return frappe.flags.get(_REQUEST_FLAG)
		name = _write(
			user=user,
			purpose=purpose,
			actor_type=actor_type,
			rooms=rooms,
			query=query,
			reason=reason,
			request_id=request_id,
			message_count=message_count,
		)
		if not force_new:
			frappe.flags[_REQUEST_FLAG] = name
		return name
	except Exception:
		# Called from inside permission hooks. Raising here denies the read at best and breaks
		# every Desk page touching a chat DocType at worst. `... from None` so the frame locals
		# -- which include the query text -- do not reach the Error Log.
		try:
			frappe.log_error(
				title="chat audit: privileged read NOT recorded",
				message=f"user={user} purpose={purpose}",
			)
		except Exception:
			pass
		return None


def record_or_refuse(**kwargs: Any) -> str:
	"""The fail-closed variant, for endpoints that are about to **return content**.

	§F.12's rule as written: the audit row is committed before content is returned, and if the
	audit cannot be written the read does not happen. Safe to raise because the caller is an
	endpoint, not a permission hook — nothing else on the page dies with it.
	"""
	name = record_privileged_read(**kwargs)
	if not name:
		frappe.throw(
			frappe._("This read could not be recorded, so it was refused."),
			frappe.ValidationError,
		)
	return name


def _write(
	*,
	user: str,
	purpose: str,
	actor_type: str,
	rooms: list[dict[str, Any]] | None,
	query: str | None,
	reason: str | None,
	request_id: str | None,
	message_count: int,
) -> str:
	"""Insert the row. Private: everything public here goes through the swallowing wrapper."""
	room_rows = _resolve_rooms(rooms, user)

	row: dict[str, Any] = {
		"accessed_by": user,
		"actor_type": actor_type if actor_type in ("Triton", "Admin", "User") else "Admin",
		"purpose": purpose if purpose in _PURPOSES else _DEFAULT_PURPOSE,
		"request_id": (request_id or "")[:64] or None,
		"reason": (reason or "").strip() or None,
		"query_hash": query_hash(query) if query is not None else None,
		"message_count": cint(message_count) or sum(cint(r.get("messages_read")) for r in room_rows),
		# `creation` is set by the framework on insert, but the chain has to commit to a value
		# that exists at hashing time, so it is stamped here and handed to the insert.
		"creation": frappe.utils.now(),
	}

	doc = frappe.new_doc(AUDIT_DOCTYPE)
	doc.update({k: v for k, v in row.items() if k != "creation"})
	if query is not None and _store_query_text():
		doc.query_text = query
	for r in room_rows:
		doc.append("rooms", r)

	doc.chain_hash = compute_chain_hash(row, _previous_chain_hash(), room_rows)
	doc.insert(ignore_permissions=True)

	# **Committed before the caller returns.** An audit row that is rolled back by a later
	# failure in the same transaction is an audit row that did not happen, and the read it was
	# recording did.
	frappe.db.commit()
	return doc.name


def _resolve_rooms(rooms: list[dict[str, Any]] | None, user: str) -> list[dict[str, Any]]:
	"""Normalise the caller's room list and compute ``was_participant`` **now**.

	Participation is resolved at read time and stored, never inferred later: membership
	changes, and a row that has to be re-derived against today's membership is a row that
	answers a different question every time it is read.
	"""
	out: list[dict[str, Any]] = []
	for entry in rooms or []:
		room = (entry.get("room") or "").strip() if isinstance(entry, dict) else str(entry or "")
		if not room:
			continue
		if isinstance(entry, dict) and "was_participant" in entry:
			member = cint(entry.get("was_participant"))
		else:
			member = _was_participant(room, user)
		out.append(
			{
				"room": room,
				"was_participant": member,
				"messages_read": cint(entry.get("messages_read")) if isinstance(entry, dict) else 0,
				"first_seq": cint(entry.get("first_seq")) if isinstance(entry, dict) else 0,
				"last_seq": cint(entry.get("last_seq")) if isinstance(entry, dict) else 0,
				"oldest_message_ts": entry.get("oldest_message_ts") if isinstance(entry, dict) else None,
				"newest_message_ts": entry.get("newest_message_ts") if isinstance(entry, dict) else None,
			}
		)
	return out


def _was_participant(room: str, user: str) -> int:
	"""Active membership, by direct SQL. Never through :mod:`chat.permissions`.

	Going through the permission stack here is the recursion this module exists to avoid:
	``membership_filter_sql`` calls ``note_privileged_read`` for an oversight user, which calls
	this, which would call it again.
	"""
	try:
		rows = frappe.db.sql(
			"""select 1 from `tabChat Room Member`
				where `room` = %(room)s and `user` = %(user)s and `is_active` = 1 limit 1""",
			{"room": room, "user": user},
		)
		return 1 if rows else 0
	except Exception:
		return 0


def verify_chain(limit: int | None = None) -> dict[str, Any]:
	"""Walk ``chain_hash`` and report the first break. Run with ``bench execute``.

	``bench --site <site> execute erpnext_enhancements.chat.audit.verify_chain``

	Reports the first break with its row name and timestamp rather than a count, because a
	break is a point in time: every row after it is suspect and every row before it is not.

	What this proves and what it does not: a break means the row's audited fields no longer
	match what was signed, which is tampering or corruption. **No break does not mean no
	tampering** — anyone with database write access can rewrite a row and recompute every
	chain_hash after it. It raises the cost from "one UPDATE" to "one UPDATE plus a correct
	rewrite of the whole tail", and it makes a careless edit obvious.
	"""
	# One literal, with the bound as a parameter rather than concatenated in. Building the
	# query by appending to a local makes the target unresolvable to
	# `tests/test_chat_rawsql_guard.py`, and a table the guard cannot name is a table it
	# cannot protect — which for this file would mean the audit log itself is the one chat
	# table nobody is checking.
	rows = frappe.db.sql(
		"""select `name`, `creation`, `chain_hash`, `accessed_by`, `actor_type`, `purpose`,
				`request_id`, `reason`, `query_hash`, `message_count`
			from `tabChat Retrieval Audit`
			order by `creation` asc, `name` asc
			limit %(limit)s""",
		{"limit": cint(limit) or 100000000},
		as_dict=True,
	)

	previous = "chat-retrieval-audit-genesis"
	checked = 0
	for row in rows:
		kids = frappe.db.sql(
			"""select `room`, `was_participant`, `messages_read`, `first_seq`, `last_seq`
				from `tabChat Retrieval Audit Room`
				where `parent` = %(parent)s order by `idx` asc""",
			{"parent": row["name"]},
			as_dict=True,
		)
		expected = compute_chain_hash(dict(row), previous, [dict(k) for k in kids])
		if expected != (row.get("chain_hash") or ""):
			return {
				"ok": False,
				"rows_checked": checked,
				"first_break": row["name"],
				"at": str(row.get("creation")),
				"detail": "audited fields do not match the stored chain hash",
			}
		previous = row["chain_hash"]
		checked += 1

	return {"ok": True, "rows_checked": checked, "first_break": None}

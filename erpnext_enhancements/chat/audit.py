# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The **only** module that writes a ``Chat Retrieval Audit`` row.

Decision #12 permits a configured role to read conversations it is not a participant in, on
the condition that the read is recorded. This module is that condition.

--------------------------------------------------------------------------------------
Recorded at ENDPOINTS, never inside permission hooks. This is the load-bearing rule.
--------------------------------------------------------------------------------------

v1.268.0 wrote the audit row from :func:`chat.permissions.note_privileged_read`, which is
called from nine places inside the permission stack. Every serious defect that release
shipped came from that one decision, so it is worth listing what it cost:

* **It committed inside other people's transactions.** ``_write`` ends with
  ``frappe.db.commit()`` so the row is durable before content is returned. From a permission
  hook that fires part-way through an unrelated request — ``announce_unread`` is a
  ``Chat Message.after_insert``, and the relay inserts messages from background jobs — the
  commit ends a transaction somebody else was still building, releasing savepoints they were
  about to roll back to.
* **It recorded reads that were then denied.** A ``has_permission`` hook runs *before* the
  answer is known.
* **It recorded almost nothing.** A hook knows who is asking and that the scope is
  unrestricted; it does not know which rooms will be read or how many messages. The rows it
  produced said "something privileged happened" and nothing else.
* **It fired for ``Administrator``.** ``membership_filter_sql`` grants the unrestricted scope
  to ``Administrator`` *or* the oversight role, so background jobs filled the log with rows
  naming an identity the schema explicitly says is meaningless.

So the hook no longer writes. It sets an in-memory marker
(:func:`mark_privileged_scope`) and returns. **Endpoints that actually return content record
the read themselves**, fail-closed, with the rooms and ranges they returned —
``tests/test_chat_audit_immutability.py`` fails the build if an endpoint that can obtain the
unrestricted scope does not.

--------------------------------------------------------------------------------------
Two rules inherited from the stub's docstring, both still binding
--------------------------------------------------------------------------------------

* **Never raise from the hook path.** :func:`mark_privileged_scope` touches no database.
  :func:`record_or_refuse` is the one function here that raises, and it is only ever called
  from an endpoint, where nothing else on the page dies with it.
* **Never go through the permission stack.** Every read and write here uses raw SQL or
  ``ignore_permissions=True``; an audited read of the audit recurses without bound.

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

#: Set by the permission hook when it hands out the unrestricted scope. Read by nothing that
#: writes — it exists so an endpoint, a test or an operator can ask "did the permission stack
#: grant a privileged scope during this request?" without the hook needing a database.
PRIVILEGED_SCOPE_FLAG = "chat_privileged_scope"

#: Serialises the chain. ``GET_LOCK`` is **connection**-scoped in MariaDB, not
#: transaction-scoped, so it survives the ``commit()`` in the middle of the critical section —
#: which a ``SELECT ... FOR UPDATE`` would not.
_CHAIN_LOCK = "ee_chat_retrieval_audit_chain"
_CHAIN_LOCK_TIMEOUT = 10

#: Purposes the ``purpose`` Select accepts.
_PURPOSES = frozenset({"mention", "search", "briefing", "oversight", "attachment"})
_DEFAULT_PURPOSE = "oversight"

#: The fields the chain commits to.
#:
#: ``recorded_at`` and **not** ``creation``: ``Document.insert()`` calls
#: ``set_user_and_timestamp()`` first thing (``frappe/model/document.py``), which assigns
#: ``creation = modified = now()`` unconditionally for a new document. A caller-supplied
#: ``creation`` therefore never survives, so signing it signed a value the database did not
#: store and every row verified as tampered. ``recorded_at`` is a field of this app's own,
#: which Frappe has no opinion about.
#:
#: ``query_text`` is deliberately absent and is covered a different way — see
#: :func:`verify_chain`, which re-derives it against ``query_hash``. Signing text that Frappe
#: may sanitise on the way in would mean signing something other than what is stored.
_CHAINED_FIELDS = (
	"accessed_by",
	"actor_type",
	"purpose",
	"request_id",
	"reason",
	"query_hash",
	"message_count",
	"recorded_at",
	# Phase 5. The retrieval facts — how much was read, from which tiers, and whether the
	# reader's view was cut. Signed rather than merely stored, because an audit row whose
	# *scale* can be edited without breaking the chain is one where "Triton read four
	# messages" can quietly become the record of a read of four hundred.
	"chunk_count",
	"token_count",
	"tiers_used",
	"context_truncated",
)

#: Fields coerced through ``cint`` before hashing. This is what makes adding an integer column
#: to :data:`_CHAINED_FIELDS` **backward-compatible**: a row written before the column existed
#: signed ``row.get(k)`` → ``None``, while the verifier reads it back from MariaDB as ``0``.
#: Without the coercion those two hash differently and every pre-existing row reports as
#: tampered — an alarm firing on the whole log, for a schema change.
#:
#: (Measured 2026-08-11: production holds **zero** audit rows, so nothing is retroactively
#: affected today. The coercion is here so the next person to add a column does not have to
#: rediscover this.)
_CHAINED_INT_FIELDS = frozenset({"message_count", "chunk_count", "token_count", "context_truncated"})

_GENESIS = "chat-retrieval-audit-genesis"


# --------------------------------------------------------------------------- the hook side


def mark_privileged_scope(*, user: str, doctype: str | None = None, ptype: str | None = None) -> None:
	"""Note in memory that the permission stack granted an unrestricted scope.

	**Touches no database and cannot raise.** It is called from inside ``has_permission`` and
	``permission_query_conditions`` hooks, where an exception denies the read at best and
	breaks every Desk page touching a chat DocType at worst, and where a write would commit
	somebody else's half-finished transaction.

	This is not the audit record. The audit record is written by the endpoint that returns the
	content, which is the only place that knows what was actually read.
	"""
	try:
		scope = frappe.flags.get(PRIVILEGED_SCOPE_FLAG)
		if not isinstance(scope, dict):
			scope = {"user": user, "doctypes": []}
			frappe.flags[PRIVILEGED_SCOPE_FLAG] = scope
		if doctype and doctype not in scope["doctypes"]:
			scope["doctypes"].append(doctype)
	except Exception:
		pass


def privileged_scope_granted() -> bool:
	"""Did the permission stack hand out an unrestricted scope during this request?"""
	return bool(frappe.flags.get(PRIVILEGED_SCOPE_FLAG))


# --------------------------------------------------------------------------- hashing


def query_hash(text: str | None) -> str:
	"""sha256 of a query string. The hash is stored by default; the text is not.

	The query a manager types is itself content — "did anyone mention my name", typed by the
	person about to run a redundancy, is not metadata.
	"""
	return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _chain_value(value: Any) -> Any:
	"""Normalise one field to the form BOTH sides hash.

	The writer holds a timestamp as the string ``frappe.utils.now()`` returns; the verifier
	reads it back from MariaDB as a ``datetime``. ``str(datetime)`` drops ``.000000`` when the
	microseconds are zero and ``now()`` never does, so without this roughly one row in a
	million reports as tampered for no reason.
	"""
	strftime = getattr(value, "strftime", None)
	if strftime:
		return strftime("%Y-%m-%d %H:%M:%S.%f")
	return value


def _canonical(payload: dict[str, Any]) -> str:
	"""Canonical JSON for hashing. Stable across Python versions and dict ordering."""
	return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def compute_chain_hash(row: dict[str, Any], previous: str, rooms: list[dict[str, Any]]) -> str:
	"""``sha256(previous ‖ canonical(audited fields ‖ rooms))``.

	Shared by the writer and :func:`verify_chain` so the two cannot drift into disagreeing
	about what was signed — a verifier with its own idea of the payload reports false breaks,
	and an alarm that always fires is worse than no alarm at all.
	"""
	payload = {
		k: (cint(row.get(k)) if k in _CHAINED_INT_FIELDS else _chain_value(row.get(k)))
		for k in _CHAINED_FIELDS
	}
	payload["rooms"] = [
		{
			"room": r.get("room"),
			"was_participant": cint(r.get("was_participant")),
			"messages_read": cint(r.get("messages_read")),
			"first_seq": cint(r.get("first_seq")),
			"last_seq": cint(r.get("last_seq")),
			"oldest": _chain_value(r.get("oldest_message_ts")),
			"newest": _chain_value(r.get("newest_message_ts")),
		}
		for r in (rooms or [])
	]
	return hashlib.sha256((previous + _canonical(payload)).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- the write side


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
	chunk_count: int = 0,
	token_count: int = 0,
	tiers_used: str | None = None,
	context_truncated: int = 0,
) -> str | None:
	"""Record one privileged read. Returns the row name, or ``None`` if it could not.

	**Every call writes its own row.** There is no per-request deduplication: the previous
	design deduplicated because permission hooks fired a dozen times per page, and with the
	hooks no longer writing, one call means one read that actually returned content. Dedupe
	here would silently merge two different reads into one record.

	Swallows, so a caller that is not returning content can record on a best-effort basis.
	Anything about to hand over message bodies must use :func:`record_or_refuse` instead.
	"""
	try:
		return _write(
			user=user,
			purpose=purpose,
			actor_type=actor_type,
			rooms=rooms,
			query=query,
			reason=reason,
			request_id=request_id,
			message_count=message_count,
			chunk_count=chunk_count,
			token_count=token_count,
			tiers_used=tiers_used,
			context_truncated=context_truncated,
		)
	except Exception:
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

	§F.12 as written: the row is committed before content is returned, and if it cannot be
	written the read does not happen. Safe to raise because the caller is an endpoint.
	"""
	name = record_privileged_read(**kwargs)
	if not name:
		frappe.throw(
			frappe._("This read could not be recorded, so it was refused."),
			frappe.ValidationError,
		)
	return name


def _acquire_chain_lock() -> bool:
	"""Serialise read-head → insert → commit. Returns False if the lock was not obtained.

	Without this, two overlapping privileged reads both read chain head ``H``, both sign
	``previous = H``, and the chain forks — after which :func:`verify_chain` reports a
	permanent break that is indistinguishable from tampering. Overlap is the normal case, not
	a rare race: the SPA issues its room list, unread counts and transcript as parallel
	requests.
	"""
	rows = frappe.db.sql("select get_lock(%s, %s)", (_CHAIN_LOCK, _CHAIN_LOCK_TIMEOUT))
	return bool(rows and rows[0][0] == 1)


def _release_chain_lock() -> None:
	try:
		frappe.db.sql("select release_lock(%s)", (_CHAIN_LOCK,))
	except Exception:
		# The lock is connection-scoped and released when the connection closes, so a failure
		# here costs a lock held slightly too long, never a lock held forever.
		pass


def _previous_chain_hash() -> str:
	"""The chain head. Called only while the chain lock is held."""
	rows = frappe.db.sql(
		"""select `chain_hash` from `tabChat Retrieval Audit`
			order by `recorded_at` desc, `name` desc limit 1"""
	)
	if rows and rows[0][0]:
		return rows[0][0]
	return _GENESIS


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
	chunk_count: int = 0,
	token_count: int = 0,
	tiers_used: str | None = None,
	context_truncated: int = 0,
) -> str:
	"""Insert the row under the chain lock. Private: the public entry points wrap this."""
	room_rows = _resolve_rooms(rooms, user)
	stamp = frappe.utils.now()

	row: dict[str, Any] = {
		"accessed_by": user,
		"actor_type": actor_type if actor_type in ("Triton", "Admin", "User") else "Admin",
		"purpose": purpose if purpose in _PURPOSES else _DEFAULT_PURPOSE,
		"request_id": (request_id or "")[:64] or None,
		"reason": (reason or "").strip() or None,
		"query_hash": query_hash(query) if query is not None else None,
		"message_count": cint(message_count) or sum(cint(r.get("messages_read")) for r in room_rows),
		"recorded_at": stamp,
		# Phase 5's retrieval facts. Present since Phase 3 as columns nothing wrote, which is
		# its own defect: a governance column that is always empty reads as "this never
		# happens" rather than as "nobody fills this in".
		"chunk_count": cint(chunk_count),
		"token_count": cint(token_count),
		"tiers_used": (tiers_used or "")[:140] or None,
		"context_truncated": 1 if cint(context_truncated) else 0,
	}

	if not _acquire_chain_lock():
		# Fail rather than fork. A row signed against a stale head is a permanent false
		# "tampered" verdict on a log whose entire job is to be trustworthy, and the caller
		# above turns this into a refused read.
		raise RuntimeError("could not acquire the chat audit chain lock")

	try:
		doc = frappe.new_doc(AUDIT_DOCTYPE)
		doc.update(row)
		if query is not None and _store_query_text():
			doc.query_text = query
		for r in room_rows:
			doc.append("rooms", r)

		doc.chain_hash = compute_chain_hash(row, _previous_chain_hash(), room_rows)
		doc.insert(ignore_permissions=True)

		# Committed before the caller returns: a row rolled back by a later failure is a row
		# that did not happen, and the read it recorded did. Safe here in a way it was not
		# from a permission hook, because this only ever runs from an endpoint.
		frappe.db.commit()
		return doc.name
	finally:
		_release_chain_lock()


def _store_query_text() -> bool:
	"""Is the raw-query-text flag on? Ships off, and any failure reads as off."""
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "store_retrieval_query_text")))
	except Exception:
		return False


def _resolve_rooms(rooms: list[dict[str, Any]] | None, user: str) -> list[dict[str, Any]]:
	"""Normalise the caller's room list and compute ``was_participant`` **now**.

	Participation is resolved at read time and stored, never inferred later: membership
	changes, and a row that has to be re-derived against today's roster answers a different
	question every time it is read.
	"""
	out: list[dict[str, Any]] = []
	for entry in rooms or []:
		if isinstance(entry, dict):
			room = (entry.get("room") or "").strip()
		else:
			room, entry = str(entry or "").strip(), {}
		if not room:
			continue
		member = (
			cint(entry.get("was_participant"))
			if "was_participant" in entry
			else _was_participant(room, user)
		)
		out.append(
			{
				"room": room,
				"was_participant": member,
				"messages_read": cint(entry.get("messages_read")),
				"first_seq": cint(entry.get("first_seq")),
				"last_seq": cint(entry.get("last_seq")),
				"oldest_message_ts": entry.get("oldest_message_ts"),
				"newest_message_ts": entry.get("newest_message_ts"),
			}
		)
	return out


def _was_participant(room: str, user: str) -> int:
	"""Active membership, by direct SQL. Never through :mod:`chat.permissions`.

	Going through the permission stack here is the recursion this module must not start:
	``membership_filter_sql`` marks the privileged scope, and auditing the audit has no bound.
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


# --------------------------------------------------------------------------- verification


def verify_chain(limit: int | None = None) -> dict[str, Any]:
	"""Walk the chain and report the first break. Run with ``bench execute``.

	``bench --site <site> execute erpnext_enhancements.chat.audit.verify_chain``

	Reports the first break with its row name and timestamp rather than a count, because a
	break is a point in time: every row after it is suspect and every row before it is not.

	Two things are checked per row. The **chain hash**, which covers the audited fields and
	the room rows; and, when the raw query text was stored, that it still hashes to the
	``query_hash`` beside it — ``query_text`` is not signed directly because Frappe may
	sanitise it on the way in, so signing what we sent would not describe what was stored.

    What a clean run proves and what it does not: a break means a row no longer matches what
    was signed, which is tampering or corruption. **No break does not mean no tampering** —
    anyone with database write access can rewrite a row and recompute every hash after it. It
    raises the cost from one ``UPDATE`` to a correct rewrite of the whole tail.
	"""
	rows = frappe.db.sql(
		"""select `name`, `recorded_at`, `chain_hash`, `accessed_by`, `actor_type`, `purpose`,
				`request_id`, `reason`, `query_hash`, `query_text`, `message_count`,
				`chunk_count`, `token_count`, `tiers_used`, `context_truncated`
			from `tabChat Retrieval Audit`
			order by `recorded_at` asc, `name` asc
			limit %(limit)s""",
		{"limit": cint(limit) or 100000000},
		as_dict=True,
	)
	if not rows:
		return {"ok": True, "rows_checked": 0, "first_break": None}

	# One query for every child row, not one per parent: the previous shape was an N+1 that
	# turned a routine integrity check into a six-figure query count.
	kids = frappe.db.sql(
		"""select `parent`, `room`, `was_participant`, `messages_read`, `first_seq`,
				`last_seq`, `oldest_message_ts`, `newest_message_ts`
			from `tabChat Retrieval Audit Room`
			where `parent` in %(parents)s
			order by `parent` asc, `idx` asc""",
		{"parents": tuple(r["name"] for r in rows)},
		as_dict=True,
	)
	by_parent: dict[str, list[dict[str, Any]]] = {}
	for kid in kids:
		by_parent.setdefault(kid["parent"], []).append(dict(kid))

	previous = _GENESIS
	checked = 0
	for row in rows:
		expected = compute_chain_hash(dict(row), previous, by_parent.get(row["name"], []))
		if expected != (row.get("chain_hash") or ""):
			return {
				"ok": False,
				"rows_checked": checked,
				"first_break": row["name"],
				"at": str(row.get("recorded_at")),
				"detail": "audited fields do not match the stored chain hash",
			}
		stored_text = row.get("query_text")
		if stored_text is not None and query_hash(stored_text) != (row.get("query_hash") or ""):
			return {
				"ok": False,
				"rows_checked": checked,
				"first_break": row["name"],
				"at": str(row.get("recorded_at")),
				"detail": "query_text no longer hashes to query_hash",
			}
		previous = row["chain_hash"]
		checked += 1

	return {"ok": True, "rows_checked": checked, "first_break": None}

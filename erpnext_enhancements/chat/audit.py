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

#: The second audit table, and the second chain. Governance EVENTS — who was granted the
#: oversight role, who expanded a tombstone, what a retention run destroyed — as opposed to
#: ``Chat Retrieval Audit``, which records READS of chat content.
#:
#: **Its own chain, deliberately.** One chain per table: interleaving two writers into a single
#: chain makes every concurrent write a fork, and ``verify_chain`` reports a fork as a permanent
#: break indistinguishable from tampering. Two streams, two locks, two genesis strings.
GOVERNANCE_DOCTYPE = "Chat Audit Log"

_GOVERNANCE_CHAIN_LOCK = "ee_chat_audit_log_chain"
_GOVERNANCE_GENESIS = "chat-audit-log-genesis"

#: The fields the governance chain signs. Everything a reader would care about, and nothing
#: Frappe rewrites on the way in — ``recorded_at`` rather than ``creation``, for the reason
#: v1.268.0 learned the hard way: ``Document.insert()`` overwrites ``creation`` unconditionally,
#: so signing it signs a value the database never stored and the first row reports as tampered.
_GOVERNANCE_CHAINED_FIELDS = (
	"event_type",
	"actor",
	"subject_user",
	"room",
	"reference_doctype",
	"reference_name",
	"reason",
	"detail",
	"recorded_at",
	"affected_count",
	"first_seq",
	"last_seq",
)

#: Coerced through ``cint`` before hashing, so adding an integer column later stays
#: backward-compatible: a row written before the column existed signed ``None`` while the
#: verifier reads ``0`` back from MariaDB, and without the coercion every pre-existing row
#: would report as tampered for a schema change.
_GOVERNANCE_INT_FIELDS = frozenset({"affected_count", "first_seq", "last_seq"})

#: The events ``Chat Audit Log.event_type`` accepts. Mirrors the Select, and is checked rather
#: than trusted: an unknown value would be rejected by Frappe at save time, i.e. *after* the act
#: it was supposed to record already happened.
#: **Must equal the DocField's Select**, which ``tests/test_chat_governance_audit.py`` asserts
#: by set equality — and every ``event_type=`` literal at a call site must be a member, which
#: the same suite scans for. Both checks exist because :func:`record_governance_event` answers
#: an unknown event type by writing **no row at all** (it logs and returns ``None``): the act
#: happens, the log does not record it, and nothing at the call site can tell. That is the same
#: shape as the ``purpose`` coercion corrected in v1.307.1, one table over.
GOVERNANCE_EVENTS = frozenset(
	{
		"oversight_role_granted",
		"oversight_role_revoked",
		"oversight_config_changed",
		"oversight_rooms_listed",
		# Who was in one room, and when they left. The inverse of `oversight_rooms_listed`,
		# and deliberately not folded into it: "which rooms is this person in" and "who was in
		# this room" are different acts, and a log that spelled them the same way could not
		# answer either question about the other.
		"oversight_members_listed",
		"tombstone_expanded",
		# The live-message twin of `tombstone_expanded`: the superseded bodies of a message
		# that was edited and never deleted. A separate type because the acts are separately
		# defensible — "I read what somebody deleted" and "I read what somebody rewrote" are
		# different sentences to have to justify, and one label for both would let the rarer
		# one hide inside the commoner.
		"revision_history_read",
		"export_requested",
		"export_downloaded",
		"retention_run",
		"chain_verification_failed",
	}
)

#: Purposes the ``purpose`` Select accepts. **Must equal the DocField's options**, which
#: ``tests/test_chat_governance_audit.py`` asserts by set equality — the two drifting is how
#: this set came to be missing three of the five things that actually call the writer.
#:
#: ``transcript``, ``thread`` and ``message_context`` were added in v1.307.1. All three were
#: already being passed by ``chat/api/history.py`` and none was in this set, so every
#: privileged content read from those endpoints was silently rewritten to ``oversight`` by the
#: coercion below and recorded as a deliberate investigation. See that coercion for why the
#: real defence against a fourth one is a build failure rather than a runtime check.
#:
#: ``briefing`` and ``attachment`` have no caller today. They are kept rather than pruned:
#: removing a Select option needs a data patch for any row still holding it, and the audit
#: tables cannot be surveyed from here — the chat gate refuses them to generic query tools,
#: by design. An unused option costs nothing; a deletion that strands rows costs a migration.
_PURPOSES = frozenset(
	{
		"mention",
		"search",
		"briefing",
		"oversight",
		"attachment",
		"transcript",
		"thread",
		"message_context",
	}
)
_DEFAULT_PURPOSE = "oversight"

#: Fields signed by **both** chains, but only on rows that actually carry a value.
#:
#: This is how a governance column gets added to a live, already-hashed log without a
#: migration and without a hole. A *mandatory* chained key would change the canonical JSON of
#: every historical row — including the ones written before the column existed — and the
#: verifier would report the entire log as tampered, which is the fork divergence D2 warns
#: about. Omitting the key when the value is empty means a row without one serialises byte for
#: byte as it did before this release, so it still verifies.
#:
#: **And it is still tamper-evident, in both directions.** Removing a category from a row that
#: had one drops a key from the payload and changes the hash. *Adding* one to a row that had
#: none adds a key and changes the hash. Neither edit passes the verifier, which is the whole
#: requirement — an unsigned field is one an operator can rewrite in SQL to reclassify why
#: they read somebody's messages, and that is exactly the edit this log exists to catch.
#:
#: **That last paragraph was false on the retrieval chain from v1.288.6 until v1.307.0, and
#: the mechanism was never the problem.** ``verify_chain`` wrote its column list out by hand
#: and nobody added ``reason_category`` to it, so the verifier recomputed a payload without a
#: key the writer would have signed — and the two agreed only because no caller ever supplied
#: a value. Meanwhile ``viewer._stamp_category`` wrote the column *after* the hash on the
#: strength of a re-signing pass that does not exist. Both verifiers now derive their SELECT
#: from these tuples (:func:`_select_columns`), so the reader and the writer cannot name
#: different fields again.
#:
#: The alternative that will be proposed next is a stored ``chain_version`` column selecting
#: between payload recipes. It reopens the hole this closes: an *unsigned* selector lets an
#: attacker set the category and the version together, and the older recipe then recomputes a
#: matching hash. A version that governs a signature has to be signed itself, which is the
#: same problem one level up.
_OPTIONAL_CHAINED_FIELDS = ("reason_category",)

#: Columns the retrieval verifier reads that no payload signs: the row's identity, the
#: signature being checked, and the raw ``query_text`` re-derived against ``query_hash``.
#: ``recorded_at`` is absent because :data:`_CHAINED_FIELDS` already carries it.
_VERIFY_EXTRA_COLUMNS = ("name", "chain_hash", "query_text")

#: The governance equivalent. ``recorded_at`` is in :data:`_GOVERNANCE_CHAINED_FIELDS`.
_GOVERNANCE_VERIFY_EXTRA_COLUMNS = ("name", "chain_hash")


def _select_columns(*groups: tuple[str, ...]) -> str:
	"""```a`, `b``` for a SELECT list, built from the signed-field tuples themselves.

	**Derived rather than written out, and that is the fix rather than the tidying.** A
	verifier that reads a different set of columns than the writer signed does not report a
	break; it silently recomputes a *different payload* and reports success. That is the one
	failure a chain cannot survive, because what stops working is the alarm it exists to
	raise — and it is invisible for exactly as long as no row carries a value for the missing
	column, which on this chain was over a dozen releases.

	Order-preserving and de-duplicated. A name listed in both an extras tuple and a signed
	tuple would otherwise be emitted twice, which MariaDB rejects outright — better than the
	alternative, but only by luck.

	The names are module-level constants and never input, so the interpolation carries no
	injection surface. It does move one class of mistake from import time to query time: a
	name in a tuple that is not a column on the DocType is now an ``OperationalError`` when
	the nightly job runs. :func:`verify_all_chains` catches that deliberately — see the
	reasoning there, because an uncaught one is silent.
	"""
	seen: dict[str, None] = {}
	for group in groups:
		for name in group:
			seen.setdefault(name, None)
	return ", ".join(f"`{name}`" for name in seen)


def viewer_session_id() -> str | None:
	"""An opaque, stable id for one signed-in session, for correlating its audit rows.

	:data:`request_id` is documented on both audit DocTypes as correlating "the rows written by
	one viewer session", and until v1.307.1 nothing outside ``chat/retrieval`` supplied one — so
	every row written by ``chat/api`` landed with it NULL and the correlation existed as a
	column and a sentence and nowhere else. Reading a transcript is inherently multi-request:
	the SPA pages, and §4.D.2 asks for one row per page. Without a shared id those rows are N
	unrelated facts rather than one act of reading, which is the question an audit trail is
	opened to answer.

	**Derived from the session, never supplied by the client.** A caller-chosen correlation id
	can be varied per request to make one sustained read look like many unrelated ones, and
	this column is signed — a client-controlled signed field records the client's story.

	**Hashed, never stored raw.** ``sid`` is a bearer credential; an audit row is read by more
	people than the cookie ever should be. The user is folded in so a digest here cannot be
	confirmed against one computed from a stolen sid alone.

	``None`` when there is no session, which is honest and is exactly what the writer already
	stores for "no correlation". Lives here rather than in ``chat/api/_common.py`` because
	``chat/governance`` needs it too and already imports this module; the alternative was an
	HTTP-layer helper reached from a governance module.
	"""
	session = getattr(frappe, "session", None)
	sid = getattr(session, "sid", None)
	user = getattr(session, "user", None)
	if not sid or not user:
		return None
	return hashlib.sha256(f"{user}\x1f{sid}".encode()).hexdigest()[:48]


def _optional_chain_payload(row: dict[str, Any]) -> dict[str, Any]:
	"""The optional chained fields this row actually carries. Empty ones are omitted.

	**Stripped before the emptiness test, and that is not cosmetic.** ``"   "`` is truthy, so
	without the strip a whitespace-only value would be signed as a category — and since
	``None``, ``""`` and ``"   "`` all mean "no category", two rows that mean the same thing
	would hash differently. MariaDB will hand back a padded string for a ``CHAR``-ish column,
	and Frappe strips on save but not on every path, so the two spellings genuinely coexist.
	A verifier that reported them as different would be reporting tampering that did not
	happen, which is the failure mode that gets an alarm switched off.
	"""
	out: dict[str, Any] = {}
	for key in _OPTIONAL_CHAINED_FIELDS:
		value = _chain_value(row.get(key))
		if isinstance(value, str):
			value = value.strip()
		if value:
			out[key] = value
	return out

#: The ``reason_category`` Select, on both audit doctypes. Decision D-3 settled that the
#: **subject** is shown a category rather than the free-text reason: the free text is written
#: for a compliance reviewer and can name a third party or an open investigation, so showing
#: it verbatim would make the transparency view the leak it exists to prevent.
#:
#: Lives here, beside the schema it validates, rather than in
#: ``chat.governance.access_report`` — that module imports this one, and putting the
#: vocabulary there would invert the dependency to define a constant.
#:
#: Chained, but **optionally** — see :data:`_OPTIONAL_CHAINED_FIELDS`. The obvious move was to
#: leave it unsigned, on the grounds that adding a key to a chained tuple re-serialises every
#: row ever written and reports the whole log as tampered. That is true of a *mandatory* key
#: and is not true of an optional one, and the difference buys both properties at once.
#: **Written for the employee, not for the compliance file.** These strings are read by
#: somebody being told a colleague read their messages, so each is phrased as an answer to
#: "why?" that a person can act on without knowing the org chart.
#:
#: Deliberately broader than the obvious list. "HR or personnel matter" rather than "HR
#: Investigation", because a reference check and a grievance are not the same event, and
#: filing both under *Investigation* tells an employee something untrue about their own
#: standing. These are for orientation, not classification.
#:
#: **"Safety or site incident" is here because of what this company does.** Fountain
#: construction and service is physical work on customer sites; when something goes wrong,
#: reconstructing who said what is a safety review rather than an HR one, and mislabelling it
#: as HR would frighten people for no reason.
#:
#: **A consequence worth stating plainly, because decision D-4 made it unavoidable.** There is
#: no investigation exemption — the shapes on offer were "with a second named approver and a
#: mandatory expiry" or "not at all", and not-at-all is the one that cannot rot. So an employee
#: under an HR or security investigation **will** see that category appear against a read of
#: their messages. That is the accepted cost of D-1 and D-4 together rather than a gap in this
#: list, and it is the reason the list does not sub-classify further: the more precise the
#: category, the more the transparency view becomes a briefing.
#:
#: **"Other" is a measurement, not an escape hatch.** Forcing a bad fit produces a *wrong*
#: category, which is worse than an unspecific one — so it exists, and
#: :func:`chat.governance.access_report.category_summary` reports its share. A sustained
#: "Other" rate means this list is wrong, and this list being wrong is the failure nobody
#: would otherwise notice.
REASON_CATEGORIES: tuple[str, ...] = (
	"HR or personnel matter",
	"Legal, regulatory or compliance",
	"Security investigation",
	"Safety or site incident",
	"Customer or contract dispute",
	"Requested by the employee",
	"Technical maintenance",
	"Other",
)

#: The share of ``Other`` above which the vocabulary should be revisited. Not enforced —
#: there is nothing sensible to *do* at a threshold except look — but named so the number on
#: the dashboard has something to be compared against.
CATEGORY_OTHER_REVIEW_THRESHOLD = 0.20


def normalise_reason_category(value: str | None) -> str | None:
	"""An accepted category, or ``None``. Never raises, never guesses.

	An unrecognised value becomes ``None`` rather than being stored: a category is only
	worth showing a subject if it means something, and a free-text value that slipped in
	through this field would be the un-reviewed text D-3 exists to keep away from them.
	"""
	text = (value or "").strip()
	return text if text in REASON_CATEGORIES else None

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
	payload.update(_optional_chain_payload(row))
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
	reason_category: str | None = None,
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
			reason_category=reason_category,
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
	reason_category: str | None = None,
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
		# **A backstop, and not the defence.** This coercion is why three of the five callers
		# spent their whole lives recorded as something they were not: an unknown purpose was
		# rewritten to `oversight` and then *signed*, so the log asserted a deliberate
		# investigation in the chain's own hand and nothing anywhere disagreed.
		#
		# It stays, because refusing here would refuse the read — `record_or_refuse` fails
		# closed, so a typo in a purpose string would take an endpoint down in production. The
		# real defence is `TheAuditPurposeVocabulary` in tests/test_chat_governance_audit.py,
		# which reads every `purpose=` literal at every call site and fails the build on one
		# this set does not contain. A mistake should cost a red CI job, not a silent lie.
		"purpose": purpose if purpose in _PURPOSES else _DEFAULT_PURPOSE,
		"request_id": (request_id or "")[:64] or None,
		"reason": (reason or "").strip() or None,
		"reason_category": normalise_reason_category(reason_category),
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
		f"""select {_select_columns(_VERIFY_EXTRA_COLUMNS, _CHAINED_FIELDS, _OPTIONAL_CHAINED_FIELDS)}
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


# ------------------------------------------------------------- the governance stream


def compute_governance_chain_hash(row: dict, previous: str) -> str:
	"""``sha256(previous | canonical(audited fields))`` for a ``Chat Audit Log`` row.

	Shared by the writer and :func:`verify_governance_chain`, for the same reason its retrieval
	sibling is: a verifier with its own idea of the payload reports false breaks, and an alarm
	that always fires is worse than no alarm at all.

	No child table here — governance events have no rooms child — so this is the simpler of the
	two and takes no second argument. That asymmetry is why the two are separate functions
	rather than one with an optional parameter: an optional argument silently defaulting to
	``[]`` on the wrong stream is a hash that verifies against nothing.
	"""
	payload = {
		key: (cint(row.get(key)) if key in _GOVERNANCE_INT_FIELDS else _chain_value(row.get(key)))
		for key in _GOVERNANCE_CHAINED_FIELDS
	}
	payload.update(_optional_chain_payload(row))
	return hashlib.sha256((previous + _canonical(payload)).encode("utf-8")).hexdigest()


def record_governance_event(
	*,
	event_type: str,
	actor: str,
	subject_user: str = "",
	room: str = "",
	reference_doctype: str = "",
	reference_name: str = "",
	reason: str = "",
	detail: str = "",
	affected_count: int = 0,
	first_seq: int = 0,
	last_seq: int = 0,
) -> str | None:
	"""Record one act of administration over chat. Returns the row name, or ``None``.

	**Swallows.** Every caller is an act that has already happened — a role granted, a
	tombstone expanded, a retention run finished — and raising here would undo the act to
	record it, which is the wrong trade for all of them. The retrieval writer makes the
	opposite trade, and the difference is the direction of time: that one runs *before*
	content is returned, so it can still refuse. This one runs after.

	The failure is loud in the Error Log rather than silent, and the chain makes a *missing*
	row detectable only by its absence — which is the honest limit of an append-only log that
	cannot refuse the thing it describes.

	**Never put message text in ``detail``.** A retention run records how many rows it
	destroyed and over which ``seq`` range; a log of what was deleted that contains what was
	deleted has not deleted anything.
	"""
	if event_type not in GOVERNANCE_EVENTS:
		# Checked here rather than left to Frappe's Select validation, which would raise at
		# save time — after the act being recorded already happened.
		frappe.log_error(
			title="chat audit: unknown governance event",
			message=f"event_type={event_type!r} actor={actor!r}",
		)
		return None

	row = {
		"event_type": event_type,
		"actor": actor,
		"subject_user": (subject_user or "").strip() or None,
		"room": (room or "").strip() or None,
		"reference_doctype": (reference_doctype or "").strip()[:140] or None,
		"reference_name": (reference_name or "").strip()[:140] or None,
		"reason": (reason or "").strip() or None,
		"detail": (detail or "").strip() or None,
		"recorded_at": frappe.utils.now(),
		"affected_count": cint(affected_count),
		"first_seq": cint(first_seq),
		"last_seq": cint(last_seq),
	}

	if not _acquire_governance_lock():
		frappe.log_error(
			title="chat audit: governance chain lock not obtained",
			message=f"event_type={event_type} actor={actor}",
		)
		return None

	try:
		doc = frappe.new_doc(GOVERNANCE_DOCTYPE)
		doc.update(row)
		doc.chain_hash = compute_governance_chain_hash(row, _previous_governance_hash())
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name
	except Exception as exc:
		try:
			frappe.log_error(
				title="chat audit: governance event NOT recorded",
				message=f"event_type={event_type} actor={actor} error={exc.__class__.__name__}",
			)
		except Exception:
			pass
		return None
	finally:
		_release_governance_lock()


def _acquire_governance_lock() -> bool:
	rows = frappe.db.sql("select get_lock(%s, %s)", (_GOVERNANCE_CHAIN_LOCK, _CHAIN_LOCK_TIMEOUT))
	return bool(rows and rows[0][0] == 1)


def _release_governance_lock() -> None:
	try:
		frappe.db.sql("select release_lock(%s)", (_GOVERNANCE_CHAIN_LOCK,))
	except Exception:
		# Connection-scoped, so a failure costs a lock held slightly too long, never forever.
		pass


def _previous_governance_hash() -> str:
	"""The governance chain head. Called only while the governance lock is held."""
	rows = frappe.db.sql(
		"""select `chain_hash` from `tabChat Audit Log`
			order by `recorded_at` desc, `name` desc limit 1"""
	)
	if rows and rows[0][0]:
		return rows[0][0]
	return _GOVERNANCE_GENESIS


def verify_governance_chain(limit: int | None = None) -> dict:
	"""Walk the governance chain and report the first break.

	``bench --site <site> execute erpnext_enhancements.chat.audit.verify_governance_chain``

	Same contract as :func:`verify_chain`, and the same honest limit: a break means a row no
	longer matches what was signed. **No break does not mean no tampering** — anyone with
	database write access can rewrite a row and recompute every hash after it. It raises the
	cost from one ``UPDATE`` to a correct rewrite of the whole tail.
	"""
	# The optional tuple belongs here too, and its absence was the same defect armed on this
	# chain. Deriving from the *mandatory* tuple protects against a new mandatory field and
	# not against a new optional one — `compute_governance_chain_hash` folds
	# `_optional_chain_payload` in at the bottom of this module exactly as its retrieval twin
	# does. It is dormant only because `record_governance_event` has no `reason_category`
	# parameter, so the column is never populated; adding one would have re-created the bug on
	# the second chain. Reading it is a no-op on today's rows and closes the trap for good.
	rows = frappe.db.sql(
		f"""select {_select_columns(
			_GOVERNANCE_VERIFY_EXTRA_COLUMNS, _GOVERNANCE_CHAINED_FIELDS, _OPTIONAL_CHAINED_FIELDS
		)}
			from `tabChat Audit Log`
			order by `recorded_at` asc, `name` asc
			limit %(limit)s""",
		{"limit": cint(limit) or 100000000},
		as_dict=True,
	)
	if not rows:
		return {"ok": True, "rows_checked": 0, "first_break": None}

	previous = _GOVERNANCE_GENESIS
	checked = 0
	for row in rows:
		if compute_governance_chain_hash(dict(row), previous) != (row.get("chain_hash") or ""):
			return {
				"ok": False,
				"rows_checked": checked,
				"first_break": row["name"],
				"at": str(row.get("recorded_at")),
				"detail": "audited fields do not match the stored chain hash",
			}
		previous = row["chain_hash"]
		checked += 1

	return {"ok": True, "rows_checked": checked, "first_break": None}


def verify_all_chains() -> dict:
	"""Both chains, for the nightly scheduler. Returns a summary and records any break.

	**A break is recorded as a governance event**, which is the only self-consistent place for
	it: the audit log is what the system uses to say something happened, and "the audit log was
	tampered with" is something that happened. The row is written to the governance stream even
	when the governance stream is the one that broke — a later row cannot repair an earlier
	break, and its own hash will verify against the new head, so the record survives and the
	break stays visible behind it.

	Wired to a nightly cron in ``hooks.py``. Delivery is §4.H's alert path as of v1.291.0 —
	the ``Error Log`` alone was *necessary and not sufficient*, because nobody reads it
	unprompted. Each chain alerts under **its own scope**, so a broken retrieval chain and a
	broken governance chain are two incidents rather than one row that keeps changing its
	mind about which chain it is describing.
	"""
	from erpnext_enhancements.chat.governance import alert_rules, alerts

	def _run(verifier) -> dict[str, Any]:
		"""Never let a verifier's own failure look like a healthy chain.

		Both SELECTs are now derived from the signed-field tuples, which moves one mistake —
		a name in a tuple that is not a column — from import-time nothing to an
		``OperationalError`` here. Unguarded it would propagate out of the scheduled job
		*before* ``alerts.check`` runs, and no alert at all is exactly what a verifying chain
		looks like from the alert board. Caught, it becomes its own incident.
		"""
		try:
			return verifier()
		# Blind on purpose, and the breadth *is* the feature: what is being caught is not a
		# known failure mode but the category "this check did not happen", and narrowing it to
		# OperationalError would let the next unanticipated one go back to being silent.
		except Exception as exc:
			return {
				"ok": False,
				"errored": True,
				"rows_checked": 0,
				"first_break": None,
				"detail": f"the verifier could not run: {type(exc).__name__}",
			}

	results = {"retrieval": _run(verify_chain), "governance": _run(verify_governance_chain)}
	broken = [name for name, result in results.items() if not result.get("ok")]

	for name, result in results.items():
		errored = bool(result.get("errored"))
		alerts.check(
			not errored,
			subsystem="audit",
			kind="chain_verification_errored",
			scope=name,
			severity=alert_rules.SEVERITY_CRITICAL,
			summary=f"the {name} audit chain verifier could not run",
			detail=(
				f"{result.get('detail')}. This says nothing about the chain — it says the "
				f"check did not happen, which is a different incident and needs a different "
				f"fix. The chain's own alert is left exactly as it was."
			),
		)
		if errored:
			# `chain_verification_failed` is deliberately NOT touched here. Clearing an alert
			# asserts health, and a verifier that did not run is nobody asserting anything.
			continue
		alerts.check(
			bool(result.get("ok")),
			subsystem="audit",
			kind="chain_verification_failed",
			scope=name,
			severity=alert_rules.SEVERITY_CRITICAL,
			summary=f"the {name} audit chain does not verify",
			detail=(
				f"First break {result.get('first_break')} at {result.get('at')}. Every row "
				f"after the break is suspect and every row before it is not. A break is "
				f"tampering or corruption; it is not something the application can cause."
			),
			first_break=result.get("first_break"),
		)

	if broken:
		# One event type, not two. A verifier that could not run is still "this run did not
		# come back clean", and the distinction belongs in the detail rather than in
		# `GOVERNANCE_EVENTS` — a new event type is a schema change and a Select migration for
		# something a sentence answers.
		detail = "; ".join(
			(
				f"{name}: {results[name].get('detail')}"
				if results[name].get("errored")
				else f"{name}: first break {results[name].get('first_break')} at {results[name].get('at')}"
			)
			for name in broken
		)
		record_governance_event(
			event_type="chain_verification_failed",
			actor="Administrator",
			detail=detail[:1000],
			affected_count=len(broken),
		)

	return {"ok": not broken, "broken": broken, "results": results}

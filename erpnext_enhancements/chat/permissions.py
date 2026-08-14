# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level scoping for the chat DocTypes — and the realtime security boundary.

======================================================================================
REVIEW CHECKLIST — READ THIS BEFORE WRITING ANY QUERY IN ``erpnext_enhancements/chat``
======================================================================================

**These hooks do not protect ``frappe.db.sql``.** A ``permission_query_conditions``
fragment is appended by ``frappe.model.db_query.DatabaseQuery`` — i.e. by ``get_list``
and by the desk's list/report views, and by nothing else. Raw SQL bypasses the whole
permission stack: no DocPerm, no query condition, no ``has_permission``. So does
``frappe.get_all``, which is ``get_list(ignore_permissions=True)`` wearing a friendlier
name.

History paging and search *will* be written in raw SQL, for keyset paging and for
``MATCH``. **Every one of those queries must apply the membership filter itself**, and
the way to do that without a second implementation drifting away from this one is
:func:`membership_filter_sql` / :func:`visible_room_names`. This is the single most
likely route to a real data leak in this system; ADR §9-F's standing acceptance bar is
*"even a raw report view cannot leak another user's rooms."*

--------------------------------------------------------------------------------------
When these hooks actually run — the zero-DocPerm consequence
--------------------------------------------------------------------------------------

ADR §F.18.1 Layer 1: **every chat DocType ships with an empty ``permissions`` array
except ``Chat Room``.** A ``has_permission`` hook can only ever *restrict* what DocPerms
already grant — it cannot widen — so on a DocType with no DocPerm row the permission
stack refuses before any hook is consulted, for everybody except ``Administrator``
(who bypasses the stack unconditionally, in ``frappe/permissions.py``, before hooks).

Concretely:

* ``Chat Room`` — carries a minimal ``read`` DocPerm for ``Chat User`` (ADR §H.4.2), so
  :func:`chat_room_query` and :func:`chat_room_has_permission` **do** run, and they are
  load-bearing.
* ``Chat Message``, ``Chat Room Member``, ``Chat Attachment`` — zero DocPerm, so their
  pairs below are **defence in depth, not the live gate**. They exist for two reasons:
  the ADR's standing Layer-3 obligation says the pair must land in the same commit as
  any DocPerm that ever gets added (and somebody adding a ``System Manager`` row so they
  can look at a message in the desk is the most likely regression in the whole feature);
  and they are the canonical, tested expression of the membership rule that the raw-SQL
  paths reuse.
* Operational tables (``Chat Relay Job``, ``Chat Inbound Event``,
  ``Chat Event Subscription``) get no pair. They carry no per-user scope — "the rooms
  you are in" is not a meaningful filter on a retry queue — and inventing one would be
  policy this ADR has not written. They stay at zero DocPerm and on the ``_gate.py``
  denylist. If a role is ever granted ``read`` on one, the standing obligation fires and
  that commit owes a pair here.

--------------------------------------------------------------------------------------
Frappe v16 semantics this module is written against
--------------------------------------------------------------------------------------

* **A ``has_permission`` hook must return an explicit ``True``.** Returning ``None`` —
  or falling off the end of the function — now **denies**. Every path in every hook
  below returns an explicit boolean, exception paths included, and
  ``tests/test_chat_permissions_bench.py`` asserts exactly that. The failure mode is a
  silent lockout rather than a leak, which is the safe direction, but it is still a
  production outage nobody can debug from the symptom.
* **``frappe.db.escape(user)`` — always.** The string these functions return is
  concatenated straight into a ``WHERE`` clause. A permission hook is the last place in
  a codebase you want an injection, and it is a classic Frappe footgun. There is no
  parameter binding available on this seam; escaping is the whole defence.

--------------------------------------------------------------------------------------
:func:`chat_room_has_permission` IS the realtime security boundary (invariant I8)
--------------------------------------------------------------------------------------

Verified from the v16 node source (ADR §H.4.1), not assumed: the socket.io server's
``doc_subscribe`` handler calls back into Python —
``socket.has_permission(doctype, docname)`` → ``/api/method/frappe.realtime.has_permission``
→ ``frappe.has_permission(doctype, doc=name, throw=True)`` → the full document-level
permission stack, including this hook, with ``ptype="read"``, under the joining user's
own session — **before** it joins ``doc:Chat Room/<room>``. User rooms are not
client-joinable at all: ``user_room`` appears twice in the entire node surface, once as
a definition and once as a server-side join from the already-authenticated identity, and
there is no ``user_subscribe`` verb for a client to call.

So getting this one function right makes socket security free, and getting it wrong
leaks message content in realtime even with every REST endpoint locked down. Two
residuals come with it and are accepted rather than hidden (ADR §H.4.3): membership is
checked **once, at join**, so eviction is cooperative; and a refused join is **silent**
(the promise is left pending forever), so a client must never treat "I emitted
``doc_subscribe``" as "I am subscribed".

--------------------------------------------------------------------------------------
Departed members read nothing — the rule is closed until CQ-10 is answered
--------------------------------------------------------------------------------------

``Chat Room`` read **and** ``Chat Message`` read are both **active membership only**.

ADR CQ-10's *recommendation* is "history stays visible up to the moment they left;
nothing after", and ``Chat Room Member.left_seq`` is carried in the schema so that answer
is cheap to implement the day it is given. **CQ-10 is not answered.** ADR §K.2.1 lists it
as open and blocking Phase 1 schema / Phase 5 gate, and its own text (ADR CQ-10) names a
``left_on`` *timestamp* rather than a sequence bound — so even the shape of the answer is
still in play. Implementing the recommendation now would widen read access to a
**non-member** on nobody's authority, which is the one direction a permission module must
never guess in. So the code fails closed: ``left_seq`` is stored and nothing reads it.

The rule is expressed in three places — :func:`_message_scope_sql`,
:func:`_may_read_message` and :func:`chat_attachment_query` — and each carries the same
comment, so answering CQ-10 "yes" is a three-site change rather than a hunt.

The ``Chat Room`` asymmetry survives whatever CQ-10 decides: room read is the doc-room
join, and a join is evaluated once and then never re-checked, so letting a departed member
read the room document would hand them a live feed of everything said after they left.
Removing someone from a room has to remain a control (ADR §H.4.3), so the room document
closes immediately and any historical backlog is served by the Phase 3 endpoint under the
*message* rule.

--------------------------------------------------------------------------------------
The admin escape hatch
--------------------------------------------------------------------------------------

Decision #12: the oversight role may read chat it is not a participant in. That is the
*only* thing that returns an unrestricted ``""``, it is gated on a role read from
``Chat Settings.admin_oversight_role`` (never a literal, never a caller-supplied flag),
and the field **ships blank on purpose** so the hatch fails closed until somebody
deliberately opens it. Every privileged read routes through :func:`note_privileged_read`
— one function, one call site per path — which is a no-op in Phase 1 and is where
Phase 6 writes its ``Chat Retrieval Audit`` row. One hook point rather than audit calls
scattered through six functions later.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any, Final

import frappe
from frappe.utils import cint

#: Table names, written once. A typo in a backticked identifier inside an f-string is a
#: runtime SQL error on a permission path, i.e. it takes the desk down rather than
#: failing a test.
_MEMBER_TABLE = "`tabChat Room Member`"
_MESSAGE_TABLE = "`tabChat Message`"

#: Users that can never hold chat membership. ``Guest`` is called out separately from
#: "empty" because it is a real, addressable session: CHAT-RT-3 refuses it on the
#: realtime side for the same reason it is refused here — ``user:Guest`` is a *shared*
#: room, not a private one.
_NEVER_A_MEMBER = frozenset({"", "Guest"})


# --------------------------------------------------------------------------- helpers


def _resolve_user(user: str | None) -> str:
	"""The session user unless a hook was handed an explicit one.

	Frappe passes ``user`` positionally to ``permission_query_conditions`` and as a
	keyword to ``has_permission``, and in both cases it is occasionally ``None``.
	"""
	return (user or frappe.session.user or "").strip()


def _is_scopable(user: str) -> bool:
	"""Whether membership scoping is even a coherent question for this identity."""
	return user not in _NEVER_A_MEMBER


#: The ``ptype`` values the oversight escape hatch may answer for. Decision #12 buys a
#: **read** of conversations you are not in; it buys nothing else.
#:
#: ``select`` is here alongside ``read`` because Frappe asks for it on link-field and query
#: paths, and refusing it would break an oversight surface in the shape of an empty dropdown
#: rather than a permission error. ``report`` is **not**: a report over chat content is the
#: bulk-extraction path the audited viewer exists to replace.
#:
#: ``None`` is treated as ``read``, because Frappe calls ``has_permission`` with no ``ptype``
#: on several paths and the historical behaviour of this hook was to allow them. Widening the
#: default to "any operation" is the mistake this constant exists to prevent.
_PRIVILEGED_PTYPES: Final[frozenset[str]] = frozenset({"read", "select"})


def _is_read_ptype(ptype: str | None) -> bool:
	"""Whether ``ptype`` is one the escape hatch may answer for. ``None`` means ``read``."""
	return (ptype or "read").strip().lower() in _PRIVILEGED_PTYPES


def _docname(doc: Any) -> str:
	"""``doc`` arrives as a Document, occasionally as a dict, rarely as a plain name."""
	if not doc:
		return ""
	if isinstance(doc, str):
		return doc.strip()
	if isinstance(doc, dict):
		return (doc.get("name") or "").strip()
	# getattr(..., None) or "": doc_events and permission hooks both fire during
	# ERPNext's own test bootstrap, before this app's fields necessarily exist.
	return (getattr(doc, "name", None) or "").strip()


def _field(doc: Any, fieldname: str) -> Any:
	"""Read one field off whatever shape ``doc`` turned out to be."""
	if isinstance(doc, dict):
		return doc.get(fieldname)
	return getattr(doc, fieldname, None)


def _oversight_role() -> str:
	"""The configured oversight role, or ``""`` when the hatch is shut.

	Read from ``Chat Settings`` rather than hardcoded, because decision #12's role is a
	deployment choice and a literal here would be a second source of truth. Blank is the
	shipped default and it means *nobody* — the hatch fails closed.

	Never raises: this runs inside a permission hook, and a hook that throws during
	``bench migrate`` on a site where ``Chat Settings`` does not exist yet takes the
	whole migration with it.
	"""
	try:
		return (frappe.db.get_single_value("Chat Settings", "admin_oversight_role") or "").strip()
	except Exception:
		return ""


def _has_oversight(user: str) -> bool:
	"""Whether ``user`` holds the configured oversight role. Gated on the role only."""
	role = _oversight_role()
	if not role:
		return False
	try:
		return role in set(frappe.get_roles(user))
	except Exception:
		# Fail closed. An unreadable role list is not a licence to read everything.
		return False


def note_privileged_read(
	doctype: str,
	name: str | None = None,
	user: str | None = None,
	ptype: str | None = None,
) -> None:
	"""Note that an unrestricted scope was granted. **Marks memory; writes nothing.**

	This function used to write the ``Chat Retrieval Audit`` row itself, and every serious
	defect in v1.268.0 came from that. It is called from nine places inside the permission
	stack, and a database write from here:

	* **committed inside other people's transactions.** The row has to be durable before
	  content is returned, so the writer commits — and from a hook firing part-way through an
	  unrelated request (``announce_unread`` is a ``Chat Message.after_insert``, and the relay
	  inserts messages from background jobs) that ends a transaction somebody else was still
	  building.
	* **recorded reads that were then denied.** A ``has_permission`` hook runs before the
	  answer is known.
	* **recorded almost nothing.** A hook knows the scope is unrestricted; it does not know
	  which rooms will be read. The rows said "something privileged happened" and no more.
	* **fired for ``Administrator``**, filling the log with rows naming an identity the
	  schema's own field description calls meaningless.

	So the audit row is written by **the endpoint that returns the content**, which is the
	only place that knows what was actually read.
	``tests/test_chat_audit_immutability.py`` fails the build if an endpoint that can obtain
	the unrestricted scope does not record.

	Both rules the original stub laid down still hold, and are easier to keep now: this cannot
	raise (it touches no database) and cannot re-enter the permission stack (it makes no
	query).
	"""
	from erpnext_enhancements.chat import audit

	audit.mark_privileged_scope(user=_resolve_user(user), doctype=doctype, ptype=ptype)
	return None


# ----------------------------------------------------------------- SQL fragment builders


def _active_member_sql(room_column: str, user: str) -> str:
	"""``EXISTS`` over an *active* membership row for ``room_column``.

	Correlated ``EXISTS`` rather than ``room IN (SELECT …)`` so the optimiser can use the
	``unique(room, user)`` index (ADR §F.5.2) as a single probe per candidate row instead
	of materialising the user's whole room list.
	"""
	return (
		f"exists (select 1 from {_MEMBER_TABLE} `cm`"
		f" where `cm`.`room` = {room_column}"
		f" and `cm`.`user` = {frappe.db.escape(user)}"
		f" and `cm`.`is_active` = 1)"
	)


def _message_scope_sql(room_column: str, seq_column: str, user: str) -> str:
	"""``EXISTS`` for message-level visibility: **active membership only**.

	**The departed-member rule is deliberately closed pending CQ-10.** ADR CQ-10 is listed
	as open (§K.2.1) and its text names a ``left_on`` timestamp, not a ``left_seq`` bound;
	granting a non-member read on the strength of an unapproved recommendation is an
	access *widening* this module is not entitled to make. ``left_seq`` is carried in the
	schema so the answer is cheap to implement, and read by nothing.

	When CQ-10 is answered "history up to the moment they left", the disjunct that goes
	back here is
	``or (ifnull(`cm`.`left_seq`, 0) > 0 and {seq_column} <= `cm`.`left_seq`)`` — and the
	``ifnull(..., 0) > 0`` guard matters, because a departed row whose ``left_seq`` was
	never stamped has an *unknown* boundary and an unknown boundary must read as "nothing".
	``seq_column`` stays in the signature for that day; today it is unused.
	"""
	return _active_member_sql(room_column, user)


def membership_filter_sql(
	room_column: str,
	user: str | None = None,
	seq_column: str | None = None,
	*,
	allow_oversight: bool = False,
) -> str:
	"""The membership filter, for the raw SQL that these hooks do **not** protect.

	Every hand-written query in ``erpnext_enhancements/chat`` — history paging, search,
	the retrieval candidate scan — must AND this into its ``WHERE``. Reuse rather than
	re-derivation is the only thing that keeps the raw paths and the hook paths from
	drifting apart, and a drift here is a data leak, not a bug.

	**``allow_oversight`` defaults to ``False``, and that default is the decision.** The
	oversight escape hatch is opt-in per call site rather than a property of the caller's
	roles, so a query is unrestricted only where somebody wrote down that it should be.

	This makes the fragment agree with its own Python twin at last. :func:`visible_room_names`
	has never short-circuited for the oversight role, on the stated grounds that *"every room
	in the system is a different query with a different audit obligation, and it must be
	written where that obligation is visible"* — and this function did the opposite, silently,
	for every caller. An auditor is also an ordinary employee, and their ordinary scrollback
	ran unrestricted and wrote an audit row about their own reading.

	§4.B's *"the gate has exactly two doors"* is the same rule stated from the other end: the
	employee surface scopes to membership, and oversight reads happen through the audited
	surface built for them, which is also the only place a mandatory ``reason`` can be
	collected. Settled with the human 2026-08-13.

	Args:
		room_column: the already-backticked SQL expression naming the room column of the
			query's own table, e.g. ``` "`tabChat Message`.`room`" ``` or ``"m.room"``.
			It is interpolated verbatim — **never pass caller-supplied text**.
		user: defaults to the session user.
		seq_column: selects the ``Chat Message`` rule rather than the ``Chat Room`` rule.
			Both are active membership only today — the departed-member bound is closed
			pending CQ-10 (see :func:`_message_scope_sql`) — so the two currently produce
			the same fragment, and the column is not read.
		allow_oversight: whether this call site is one of the two doors. Keyword-only, so it
			can never be passed by accident in the ``seq_column`` position.

	Returns:
		A SQL boolean expression. Unlike a ``permission_query_conditions`` hook this
		never returns ``""`` — an empty string silently disappears out of a hand-written
		``WHERE`` and the query then returns everything. Unrestricted is spelled
		``"1 = 1"`` and denied is spelled ``"1 = 0"``, both of which survive being
		concatenated by a careless caller.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return "1 = 0"
	if allow_oversight and (user == "Administrator" or _has_oversight(user)):
		note_privileged_read("Chat Message", None, user, "read")
		return "1 = 1"
	if seq_column:
		return _message_scope_sql(room_column, seq_column, user)
	return _active_member_sql(room_column, user)


def visible_room_names(user: str | None = None) -> list[str]:
	"""Rooms ``user`` is an **active** member of — the Python-side twin of the filter.

	For the call sites that need a room list rather than a SQL fragment: the Phase 3 room
	list, and Phase 5's ``allowed_rooms`` derivation (which ADR §I.3 requires be derived
	server-side and never accepted as an argument).

	Returns ``[]`` for Guest and for an unresolvable user — fail closed. Does **not**
	short-circuit for the oversight role: "every room in the system" is a different query
	with a different audit obligation, and it must be written where that obligation is
	visible.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return []
	try:
		return frappe.get_all(
			"Chat Room Member",
			filters={"user": user, "is_active": 1},
			pluck="room",
		)
	except Exception:
		return []


# ------------------------------------------------------- membership probes (has_permission)


def _is_active_member(room: str, user: str) -> bool:
	"""One indexed probe against ``unique(room, user)``. Never raises."""
	if not room or not _is_scopable(user):
		return False
	try:
		return bool(
			frappe.db.exists("Chat Room Member", {"room": room, "user": user, "is_active": 1})
		)
	except Exception:
		# Fail closed: an unanswerable membership question is a "no".
		return False


def is_active_member(room: str, user: str) -> bool:
	"""Public: is this identity **in** this room. Membership only, no escape hatch.

	The read gates in this module all answer "may this identity see the room", which the
	oversight role and ``Administrator`` may — that is decision #12. This answers the other
	question, the one no amount of oversight makes true, and it exists because
	:func:`chat.api._common.require_room` needs to distinguish them for writes.

	Deliberately a thin public alias rather than a second implementation. The private probe
	is called from inside the permission hooks and this is called from the HTTP gate; two
	probes that could disagree about who is in a room is exactly the failure the hooks were
	centralised to avoid.
	"""
	return _is_active_member(room, user)


def _may_read_message(room: str, seq: int, user: str) -> bool:
	"""One message. The Python twin of :func:`_message_scope_sql`, and it must stay one.

	**The departed-member rule is deliberately closed pending CQ-10**, exactly as in the
	SQL twin: a non-member reads nothing, whatever their ``left_seq`` says. ``seq`` stays
	in the signature for the day CQ-10 is answered; today it is unused, which is also what
	keeps the two implementations from disagreeing about a ``NULL`` seq (SQL excludes such
	a row, ``cint(None)`` would have admitted it).
	"""
	if not room or not _is_scopable(user):
		return False
	return _is_active_member(room, user)


# ------------------------------------------------ permission_query_conditions (list reads)


def chat_room_query(user: str | None = None) -> str:
	"""List scoping for ``Chat Room`` — the one chat DocType that carries a DocPerm.

	Active membership only; see the module docstring on why this is stricter than the
	message rule.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return "1 = 0"
	if user == "Administrator" or _has_oversight(user):
		note_privileged_read("Chat Room", None, user, "read")
		return ""
	return _active_member_sql("`tabChat Room`.`name`", user)


def chat_room_member_query(user: str | None = None) -> str:
	"""List scoping for ``Chat Room Member``: your own rows, plus the roster of your rooms.

	Your own rows are always visible, including the departed ones — that is your record
	of what you used to be in, and hiding it would make leaving a room look like the room
	never existed.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return "1 = 0"
	if user == "Administrator" or _has_oversight(user):
		note_privileged_read("Chat Room Member", None, user, "read")
		return ""
	own = f"{_MEMBER_TABLE}.`user` = {frappe.db.escape(user)}"
	roster = _active_member_sql(f"{_MEMBER_TABLE}.`room`", user)
	return f"({own} or {roster})"


def chat_message_query(user: str | None = None) -> str:
	"""List scoping for ``Chat Message`` — the DocType that carries message bodies.

	Zero DocPerm means this hook is not what actually stops a stranger reading a message;
	the absent DocPerm is (ADR §F.18.1 Layer 1). It is here so the rule is expressed,
	tested and reusable, and so the day someone adds a DocPerm row the gate is already
	standing.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return "1 = 0"
	if user == "Administrator" or _has_oversight(user):
		note_privileged_read("Chat Message", None, user, "read")
		return ""
	return _message_scope_sql(f"{_MESSAGE_TABLE}.`room`", f"{_MESSAGE_TABLE}.`seq`", user)


def chat_attachment_query(user: str | None = None) -> str:
	"""List scoping for ``Chat Attachment``: whatever its parent message allows.

	Scoped through the message rather than through ``Chat Attachment.room`` — the
	denormalised column is there for the render path, and two sources of truth for one
	permission is how a leak arrives. It also keeps the join in place for the day CQ-10
	is answered, because that boundary is a *seq* comparison and only the message carries
	a seq.

	**The departed-member rule is deliberately closed pending CQ-10**, matching
	:func:`_message_scope_sql`. The disjunct that goes back into the ``exists`` when the
	question is answered is
	``or (ifnull(`cm`.`left_seq`, 0) > 0 and `cmsg`.`seq` <= `cm`.`left_seq`)``.

	Note that this governs the ``Chat Attachment`` row, not the bytes. File access is
	Frappe's own check on a private ``File`` attached to the ``Chat Message``, which is
	why ADR §F.8 requires ``is_private = 1`` on every chat attachment — a public file is
	served by the web server with no auth at all and no hook of ours is consulted.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return "1 = 0"
	if user == "Administrator" or _has_oversight(user):
		note_privileged_read("Chat Attachment", None, user, "read")
		return ""
	escaped = frappe.db.escape(user)
	return (
		f"exists (select 1 from {_MESSAGE_TABLE} `cmsg`"
		f" join {_MEMBER_TABLE} `cm` on `cm`.`room` = `cmsg`.`room`"
		f" where `cmsg`.`name` = `tabChat Attachment`.`message`"
		f" and `cm`.`user` = {escaped}"
		f" and `cm`.`is_active` = 1)"
	)


# ------------------------------------------------------ has_permission (single-doc reads)
#
# Ten-and-ten parity is the house doctrine (``hooks.py:1123-1148`` / ``:1150-1164``):
# every ``permission_query_conditions`` entry has a ``has_permission`` twin, without
# exception. A query condition filters lists; only these refuse a direct read by name —
# and only this one on ``Chat Room`` refuses a socket join.
#
# EVERY path below returns an explicit ``bool``. On v16 a ``None`` denies, so a missing
# return is a silent lockout; ``tests/test_chat_permissions_bench.py`` asserts the return
# type on every branch, exception branches included.


def chat_room_has_permission(doc: Any, ptype: str | None = None, user: str | None = None) -> bool:
	"""Single-document gate for ``Chat Room`` — **and the realtime security boundary**.

	``doc_subscribe`` on ``doc:Chat Room/<room>`` reaches this function with
	``ptype="read"`` under the joining user's own session (ADR §H.4.1). Whatever this
	returns decides whether that socket receives the room's message traffic; every REST
	endpoint in the system being correct does not help if this one is not.

	Applies to every ``ptype``, not just ``read``: this hook narrows rows, the DocPerm
	decides operations. ``Chat Room``'s DocPerm grants ``read`` and nothing else, so a
	write attempt is already refused above us — but if a future commit widens it, the
	membership scope should still hold rather than having to be remembered.

	**The privileged branches are ``read``-only, and that sentence above is why it had to
	be made explicit.** "Already refused above us" is true of the DocPerm stack and false of
	any caller that reaches this function directly, which
	:func:`chat.api._common.require_room` did for every write endpoint in the package. The
	comment described a guarantee that the code next to it did not provide. Now the escape
	hatch answers the question it was built for — *may this identity see the room* — and
	nothing else; §4.A.3's "read and nothing else" is a property of this function rather
	than of a DocPerm somebody might widen.

	``PRIVILEGED_PTYPES`` rather than ``ptype == "read"``: ``select`` is what the link-field
	and query paths ask for, and refusing it would break an oversight surface in a way that
	presents as an empty dropdown rather than as a permission error.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return False

	privileged = user == "Administrator" or _has_oversight(user)
	if privileged and _is_read_ptype(ptype):
		note_privileged_read("Chat Room", _docname(doc), user, ptype)
		return True

	room = _docname(doc)
	if not room:
		return False
	return _is_active_member(room, user)


def chat_room_member_has_permission(
	doc: Any, ptype: str | None = None, user: str | None = None
) -> bool:
	"""Single-document twin of :func:`chat_room_member_query`."""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return False
	if (user == "Administrator" or _has_oversight(user)) and _is_read_ptype(ptype):
		note_privileged_read("Chat Room Member", _docname(doc), user, ptype)
		return True
	if (_field(doc, "user") or "").strip() == user:
		return True
	room = (_field(doc, "room") or "").strip()
	if not room:
		return False
	return _is_active_member(room, user)


def chat_message_has_permission(
	doc: Any, ptype: str | None = None, user: str | None = None
) -> bool:
	"""Single-document twin of :func:`chat_message_query`.

	Nothing joins a doc room named after a *message*, so unlike ``Chat Room`` this is not
	a realtime boundary — it is the REST/desk boundary that zero DocPerm already closes.
	"""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return False
	if (user == "Administrator" or _has_oversight(user)) and _is_read_ptype(ptype):
		note_privileged_read("Chat Message", _docname(doc), user, ptype)
		return True
	room = (_field(doc, "room") or "").strip()
	if not room:
		return False
	return _may_read_message(room, cint(_field(doc, "seq")), user)


def chat_attachment_has_permission(
	doc: Any, ptype: str | None = None, user: str | None = None
) -> bool:
	"""Single-document twin of :func:`chat_attachment_query`, resolved through the message."""
	user = _resolve_user(user)
	if not _is_scopable(user):
		return False
	if (user == "Administrator" or _has_oversight(user)) and _is_read_ptype(ptype):
		note_privileged_read("Chat Attachment", _docname(doc), user, ptype)
		return True

	message = (_field(doc, "message") or "").strip()
	if not message:
		# An attachment with no parent message has no room and therefore no reader. It
		# should be unreachable; treat it as unreadable rather than guessing from the
		# denormalised room column, which carries no seq and so cannot honour CQ-10.
		return False
	try:
		row = frappe.db.get_value("Chat Message", message, ["room", "seq"], as_dict=True)
	except Exception:
		return False
	if not row:
		return False
	return _may_read_message((row.get("room") or "").strip(), cint(row.get("seq")), user)

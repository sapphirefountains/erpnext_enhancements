"""Every unscoped query in the chat package, enumerated and justified. Bench-free.

The Phase 2 brief calls raw SQL *"the single most likely route to a real data leak"* in this
feature, and the reason is mechanical rather than rhetorical:

* ``permission_query_conditions`` is appended by the ``get_list`` engine and by **nothing
  else**. Raw ``frappe.db.sql`` never sees it.
* ``frappe.get_all`` is ``get_list(ignore_permissions=True)`` wearing a friendlier name. It
  is in scope here, it is **not** exempt, and the fact that it reads like an innocent helper
  is exactly why it needs a lint rather than a code-review habit.
* ``frappe.qb`` builds SQL directly. Same hole, newer syntax, and this repo has no
  occurrence of it yet — which is the cheapest moment to fence it.

Phase 1 wrote ``chat/permissions.py`` and its hooks were **never executed**; Phase 2 adds a
second origin (Google) and a dozen background jobs, none of which run under a session user.
So the question this file answers is not "are the hooks correct" — that is
``tests/test_chat_permissions_bench.py``, which needs a bench — but "is there any query in
the package that never reaches them, and has somebody written down why it is safe".

--------------------------------------------------------------------------------------
The rule
--------------------------------------------------------------------------------------

Every raw query is resolved to the table(s) it touches, then classified:

* **Unscoped table** (:data:`UNSCOPED_TABLES`) — a retry queue, a raw-event log, a
  subscription roster, a provisioning checkpoint. "The rooms you are in" is not a
  meaningful filter on any of them, so there is nothing to scope and the read is allowed.
  Each entry carries the reason it holds no per-user content.
* **Scoped table** (:data:`SCOPED_TABLES`) — anything carrying, or joining to, what a
  coworker said. Allowed only if the enclosing function applies
  :func:`chat.permissions.membership_filter_sql` (or is one of the helpers that *is* the
  filter), **or** the exact ``(file, function, table)`` triple is listed in
  :data:`SYSTEM_CONTEXT_READS` with a written reason.
* **Foreign table** (:data:`FOREIGN_TABLES`) — ``File``, ``Has Role`` and friends. Listed
  so a new one cannot appear silently; a Frappe core table that suddenly shows up in a chat
  query is worth one line of thought.
* **Anything else, or a target this file cannot resolve** — a failure. An unresolved target
  is not a small gap: it is a query whose table nobody can name from the source, and the
  check that cannot see it is the check that stops protecting it.

The exemption key is the ``(file, function, table)`` **triple**, deliberately, not the file.
A file-level exemption would silently cover the next function somebody adds to that file,
and the next function is the one that returns bodies to an endpoint.

--------------------------------------------------------------------------------------
What this file cannot do, stated so nobody reads more into a green run
--------------------------------------------------------------------------------------

It is a source-level check. It proves a query *mentions* the shared fragment; it cannot
prove the fragment was ANDed into the right ``WHERE`` rather than assigned to an unused
local. It reads Python only, so a query assembled in JavaScript, a Server Script or a
report definition is invisible to it. And "the enclosing function references
``membership_filter_sql``" is a proxy for "this read is scoped" — a good proxy, and still a
proxy. The behavioural half is the bench suite.

The enforced call set is exactly the four the brief names: ``frappe.db.sql``,
``frappe.db.get_all``, ``frappe.get_all``, ``frappe.qb``. ``frappe.db.get_value`` /
``exists`` / ``count`` are **not** enforced, and that is a deliberate, stated boundary
rather than an oversight: they return a scalar or a single row addressed by name, they are
used throughout the package as existence probes, and enforcing them would produce an
exemption list long enough to stop anybody reading it. They are still an unscoped read. If
one of them ever selects a message body, this file will not catch it.

Raw SQL is found by **two** detectors, because one is evadable:

1. every ``frappe.db.sql`` call whose query argument resolves to table names; and
2. every live (non-docstring) string literal anywhere in the package containing a
   backticked ``tab<Table>`` reference.

(2) is what makes a helper like ``chat/health.py``'s ``_sql(errors, what, query, values)``
visible: the ``frappe.db.sql`` call inside it names no table at all, while its callers'
query literals name five. A check that only looked at the call site would have reported
that module as clean.

Run: python -m unittest erpnext_enhancements.tests.test_chat_rawsql_guard -v
"""

from __future__ import annotations

import ast
import re
import unittest
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

APP_DIR: Path = Path(__file__).resolve().parents[1]
CHAT_DIR: Path = APP_DIR / "chat"

#: The four call surfaces the brief names, as dotted callee paths.
#:
#: ``frappe.get_all`` sits here rather than in an exempt list on purpose. It is
#: ``get_list(ignore_permissions=True)``: the permission stack is skipped by the callee, not
#: by the caller, so nothing at the call site looks wrong.
SQL_CALLS: frozenset[tuple[str, ...]] = frozenset(
	{
		("frappe", "db", "sql"),
		("frappe", "db", "get_all"),
		("frappe", "get_all"),
	}
)

#: ``frappe.qb`` is a namespace, not a function, so it is matched as an attribute chain.
QB_ROOT: tuple[str, str] = ("frappe", "qb")

#: Functions whose presence in a query's enclosing function counts as "this read is scoped".
#: Both live in ``chat/permissions.py`` and both are the *shared* expression of the
#: membership rule — a second, hand-rolled ``exists (select 1 from tabChat Room Member …)``
#: would not satisfy this check, and should not: two implementations of one permission rule
#: is how the two drift apart, and a drift here is a leak rather than a bug.
FILTER_HELPERS: frozenset[str] = frozenset({"membership_filter_sql", "visible_room_names"})

#: The fragment builders inside ``chat/permissions.py``. A backticked ``tabChat …`` inside
#: one of these is the membership filter's **own text** — it is the thing every other query
#: is supposed to AND in — so reporting them as unscoped reads would be exactly backwards.
#:
#: The exemption is structural rather than trusted: :class:`TestTheAllowlistsAreHonest`
#: asserts none of these functions executes anything. They return SQL *text*; the day one of
#: them grows a ``frappe.db.sql`` or a ``frappe.get_all``, it stops being a builder and this
#: suite says so.
FRAGMENT_BUILDERS: frozenset[str] = frozenset(
	{
		"_active_member_sql",
		"_message_scope_sql",
		"chat_room_query",
		"chat_room_member_query",
		"chat_message_query",
		"chat_attachment_query",
	}
)

#: The module the fragment builders must live in. Nowhere else gets the exemption: a second
#: hand-rolled ``exists (select 1 from tabChat Room Member …)`` in another module is the
#: drift this whole design exists to prevent.
PERMISSIONS_MODULE: str = "permissions.py"

#: Tables with **no per-user scope**. These are the brief's allowlist. Every entry states why
#: scoping it is not merely unnecessary but incoherent — an operator queue is not "yours".
#:
#: Adding a table here is a policy decision, not a convenience. The test that matters is the
#: one below asserting the reason is actually written.
UNSCOPED_TABLES: dict[str, str] = {
	"Chat Push Subscription": (
		"The Web Push device registry: one row per browser, holding an endpoint URL, the "
		"subscription's own two keys, a user agent and delivery health. **No conversation, no "
		"room and no message ever touches this table** — the only chat identifier a push "
		"carries is in the encrypted payload, which is built in memory and never stored.\n\n"
		"Membership is not the scope here because a device is not in a room. The scope that "
		"does apply is `user`, and every read filters on it except the two sweeps, which are "
		"deliberately global: `prune_stale` and `clear_old_logs` run on a schedule with no "
		"session user and must see every row or they retire nothing.\n\n"
		"What keeps it safe is that nothing user-facing reads it at all. Zero DocPerm keeps it "
		"off the desk, out of /api/resource and away from the generic MCP tools; the only "
		"reader is webpush/subscriptions.py. That matters more here than on most tables, "
		"because **an endpoint is a bearer capability** — anybody holding one can push to that "
		"browser — which is also why it is never written to a log line and why `_origin()` "
		"reduces it to a host before it reaches one."
	),
	"Chat Retrieval Audit": (
		"The decision #12 audit log. It records THAT a privileged read happened, by whom, for "
		"what and over which rooms — never what was said: no body, no snippet, and the query "
		"text only behind a flag that ships off. Scoping it by the reader's own membership "
		"would be precisely backwards, because the rows worth reading are the ones where the "
		"reader was NOT a member; a membership filter would hide exactly the events the log "
		"exists to surface. Per §F.12 it carries DocPerm rather than none — System Manager, "
		"read and report only — because an audit log nobody can look at is not an audit log."
	),
	"Chat Audit Log": (
		"Phase 6's governance log — acts of administration OVER chat rather than reads OF it: "
		"who was granted the oversight role, who expanded a tombstone, what a retention run "
		"destroyed as a count and a seq range. It holds **no message text in any field, by "
		"design**, because a log of what was deleted that contains what was deleted has not "
		"deleted anything. Unscoped for the same reason its sibling is: the rows worth reading "
		"are the ones about somebody else, so a membership filter would hide exactly the "
		"events the log exists to surface. Read only by the chain verifier, which must walk "
		"EVERY row in insertion order — a scoped walk would report a break at the first row "
		"the walker could not see. DocPerm is System Manager, read and report only."
	),
	"Chat Retrieval Audit Room": (
		"The child of the above: one row per room touched by one privileged read, carrying "
		"room, was_participant and the seq range. Same reasoning, and it holds no text either. "
		"Read only by the chain verifier, which must walk EVERY row in insertion order — a "
		"scoped walk would report a break at the first row the walker could not see."
	),
	"Chat Relay Job": (
		"The outbound retry queue. A row holds identifiers (room, message, job_seq), a state, "
		"an attempt count, a lease and a scrubbed error string — no message text, ever, "
		"because the relay's own logging rule forbids a body reaching a log or an error "
		"field. 'The rooms you are in' is not a meaningful filter on a retry queue: the "
		"reader is a worker or an operator at 2am, and both need the whole queue or none of "
		"it. Zero DocPerm keeps it off the desk and out of /api/resource."
	),
	"Chat Inbound Event": (
		"The raw Pub/Sub event log. Under payloadOptions.includeResource=false a Workspace "
		"Events payload carries a resource NAME and no body at all (PHASE2_VERIFIED §7), so "
		"the stored envelope is metadata by construction rather than by redaction. It is "
		"consumed by the ingest worker and by a replay command, neither of which has a "
		"session user to scope against, and a partially-scoped replay would silently skip "
		"events — losing a coworker's message, which is the failure this whole phase exists "
		"to prevent."
	),
	"Chat Event Subscription": (
		"One row per coworker's Workspace Events subscription (shape B, ~20 rows): the "
		"Google subscription uid, target resource, state, expireTime, failure counters. It "
		"is operational health, not conversation. The renewal scheduler and the alerting "
		"sweep must see every row or the one subscription nobody is watching is the one that "
		"expires — and an expired subscription is deleted by Google and cannot be renewed, "
		"i.e. permanent silent loss of inbound sync for every space only that user covers."
	),
	"Chat Provisioning Run": (
		"The bulk org-sweep checkpoint: mode, dry-run flag, cursor, counts, timestamps. It "
		"exists so an interrupted run resumes rather than restarts, so the resuming worker "
		"needs the row regardless of who started it. It references org units, never messages."
	),
	"Chat Drift Report": (
		"Phase 6 §4.I's divergence census: a class slug, a scope, identifiers, counts, "
		"timestamps and a lifecycle. No message text in any field by schema — the detail "
		"column carries states, error strings and recovery instructions, never a body, "
		"because a drift table holding bodies would be a second transcript sitting outside "
		"the membership model.\n\n"
		"UNSCOPED for the same reason as its siblings: every reader is the nightly scan, a "
		"`bench execute` command or the alert path, none of which has a session user. There "
		"is also nothing for membership to mean on it — half its rows are about rooms rather "
		"than about anything anybody said, and the most important class in the set is "
		"literally 'this room has no active members', which a membership filter would hide."
	),
	"Chat Ops Alert": (
		"Phase 6 §4.H's alert board: a subsystem, a failure kind, a scope, counts, "
		"timestamps and a lifecycle. It holds no message text in any field by schema, and "
		"there is nothing for membership to mean on it — an alert is about the machinery "
		"rather than about anything anybody said.\n\n"
		"UNSCOPED rather than a per-call-site exemption, and the readers are the reason: "
		"every one of them is a scheduler, a worker or a `bench execute` command with no "
		"session user. `_live_alerts` is the deduplication lookup that runs inside whatever "
		"job noticed the problem, and `open_alerts` feeds the health report. A membership "
		"filter on either would return nothing and look correct doing it — which is exactly "
		"how the oversight read path stayed useless for a whole phase.\n\n"
		"What keeps it closed is what closes the queue tables: zero DocPerm — off the desk, "
		"out of /api/resource, away from the generic MCP tools — plus an entry in the shared "
		"MCP denylist. It is on that denylist despite holding no content, because it names "
		"rooms, which is a room census by another route, and because an alert board is a map "
		"of what is currently broken."
	),
}

#: Tables that DO carry, or join straight to, what a coworker said. A query touching one of
#: these applies the shared fragment or earns an entry in :data:`SYSTEM_CONTEXT_READS`.
#:
#: ``Chat Room`` is in here even though a room row holds no message text: the room list is
#: itself confidential (titles, linked documents, who is talking to whom), and ADR §9-F's
#: acceptance bar is worded about rooms — *"even a raw report view cannot leak another
#: user's rooms"*.
#:
#: ``Chat Message Revision`` is in here and needs **tighter** handling than the message
#: table, not equal handling: it is where superseded and deleted bodies live, so it is the
#: one table where a leak survives the user's decision to delete.
SCOPED_TABLES: frozenset[str] = frozenset(
	{
		"Chat Message",
		"Chat Message Revision",
		"Chat Room",
		"Chat Room Member",
		"Chat Attachment",
		"Chat Mention",
		# Phase 5's index. `Chat Context Chunk.body` holds the messages VERBATIM, so it is
		# not a derived artefact deserving lighter handling — it is the transcript,
		# pre-assembled into prose, and a leak here is a leak of the conversation in a form
		# that is easier to read than the conversation.
		"Chat Context Chunk",
		# The digests are *summaries* of conversation, which is worse rather than better: a
		# summary crosses time and topic boundaries a single message does not, and it cannot
		# be un-said once it has been quoted into a context window.
		"Chat Room Digest",
		"Chat Thread Digest",
		# Holds no message text — identifiers, counts and timings — but the citation manifest
		# on it carries a person's name and a room's identity per entry, so an unscoped read
		# leaks who is talking to whom without leaking a single body. It also records WHO
		# ASKED TRITON WHAT, WHEN, which is a behavioural record of employees and belongs
		# behind the same door as the conversation.
		"Triton Invocation Log",
	}
)

#: Non-chat tables a chat query may name, each with the reason. Listed rather than ignored
#: so that a Frappe core table appearing in a chat query is a decision somebody made rather
#: than a line that slipped through.
FOREIGN_TABLES: dict[str, str] = {
	"File": (
		"Private File rows attached to a Chat Message. Read in the relay worker to collect "
		"the bytes to upload; the filter is attached_to_doctype/attached_to_name, so the scope "
		"is the single message the job was handed. The bytes' own access control is Frappe's "
		"private-file check delegating to Chat Message's has_permission (is_private = 1 on "
		"every chat attachment, ADR §F.8) and is not this query's job."
	),
	"Has Role": (
		"Role membership, used to find the operators who should receive a dead-letter or "
		"subscription alert. It is not chat data and it is already readable by anybody who "
		"can read the User doctype."
	),
	"Chat Settings": (
		"The Single. Identifiers, thresholds and switches — no message content, and no "
		"credential (the guardrail suite asserts no chat DocType declares a Password field)."
	),
	"Chat Allowed User": (
		"The pilot roster child table on Chat Settings: a list of email addresses allowed to "
		"use the feature. Not conversation."
	),
	"Notification Log": (
		"Frappe's own bell table, written and cleared by chat/notifications/bell.py. Every "
		"query against it is filtered on `for_user`, which IS the scope — a Notification Log "
		"row belongs to exactly one person by construction, so there is no membership "
		"question to ask and no second reader to exclude.\n\n"
		"What keeps it safe is the write side rather than the read side: a chat notification "
		"row carries a subject naming a sender and a room, a deep link, and the "
		"document_type/document_name pair — and **never any message text**. That is "
		"deliberate and it is the reason this entry is narrow. `Chat Message` ships zero "
		"DocPerm precisely so bodies are unreachable through /api/resource, the desk, global "
		"search and the generic MCP tools; a snippet in a Notification Log subject would move "
		"employee-private conversation into a table with ordinary permissions and undo all of "
		"it. A query here that starts selecting or writing a body is a finding, not a feature."
	),
	"User": (
		"Display names and avatars, joined onto rows the membership filter has ALREADY "
		"scoped — the room roster, a search hit's author, a mention candidate. It is the one "
		"join that turns a room of hash docnames into a room of people, and doing it in the "
		"client would be fifty round trips to paint fifty avatars.\n\n"
		"It discloses nothing new: every authenticated user can already read `User` through "
		"any link field's search on any form, and `chat/api/mentions.py::_other_candidates` "
		"is deliberately the only place that reads it WITHOUT a chat table beside it — a "
		"coworker directory, filtered to enabled System Users, which is the same list the "
		"awesomebar returns. The columns selected are `name`, `full_name`, `user_image` and "
		"`enabled`; a password, an API key or a session is never in the select list and a "
		"query here that grows one is a finding.\n\n"
		"Scoping it by membership would be incoherent in the direction that matters: the "
		"mention menu's whole job is to let somebody discover a colleague who is NOT yet in "
		"the room. What stops that becoming an escalation is the write side — "
		"`compose._clean_mentions` drops a User mention of a non-member — not this read."
	),
}

#: The ``(file, function, table)`` triples permitted to read a scoped table **without** the
#: membership fragment, each with the reason it is a system-context read.
#:
#: Keyed on the triple, not the file. A file-level exemption silently covers whatever
#: function is added to that file next, and the next function is the one that answers a user.
#:
#: The shape of every legitimate entry below is the same and it is worth naming, because it
#: is the test a new entry has to pass: **the caller has no session user, the row set is
#: bounded by something other than the reader's identity, and no column selected is a body.**
#: A read that answers an HTTP request fails all three and does not belong here.
SYSTEM_CONTEXT_READS: dict[tuple[str, str, str], str] = {
	# --- chat/governance/purge.py: the purge itself (Phase 6 §4.F) ----------------------
	#
	# Eligible on all three counts, and the third is worth reading rather than assuming.
	# NO SESSION USER: `bench execute` only — there is no scheduler entry, deliberately (a job
	# that destroys conversation on a timer is not something to add and then remember to think
	# about) and no endpoint, which `test_chat_purge` asserts. BOUNDED BY SOMETHING OTHER THAN
	# THE READER: whatever the retention window makes eligible, which is a property of the
	# calendar rather than of anybody's membership. NO BODY COLUMN: `name` and `seq` only, and
	# `Chat Attachment.file` which is a File docname.
	#
	# A membership filter would be actively wrong rather than merely unnecessary: a purge
	# scoped to what the operator happens to be a member of would silently skip exactly the
	# rooms nobody is watching, which are the rooms whose messages age out. The eligibility
	# decision is `purge_rules.holds` and is made in one place; these queries only fetch the
	# identifiers it has already ruled on.
	#
	# `Chat Message Revision` has NO entry here on purpose — its rows are removed by a
	# filtered `frappe.db.delete` with no preceding read, because they are being destroyed
	# rather than inspected and that table's guard offers no exemption mechanism.
	(
		"governance/purge.py",
		"_rooms",
		"Chat Room",
	): (
		"The unarchived rooms a run will consider, `name` only. The purge iterates rooms so a "
		"failure in one cannot abandon the rest, and so the retirement mark advances per room."
	),
	(
		"governance/purge.py",
		"_eligible",
		"Chat Message",
	): (
		"`name` and `seq` for the span the retention planner already ruled eligible — the "
		"eligibility rule itself is `purge_rules.holds` and is deliberately not restated here, "
		"so the report and the purge cannot disagree about what may go."
	),
	(
		"governance/purge.py",
		"_retire",
		"Chat Message",
	): (
		"One row: the lowest surviving `seq` in the room, which is where the retirement mark "
		"has to stop. `set_retirement_mark` refuses unless everything at or below it is gone, "
		"so the mark cannot be the batch's high seq — a message held by an open relay job or a "
		"live thread reply is still there."
	),
	(
		"governance/purge.py",
		"_destroy_sidecars",
		"Chat Attachment",
	): (
		"`name` and `file` for one already-destroyed message. The `file` is a File docname and "
		"is read because the bytes have to go with the row: `sync/attachments.download` "
		"resolves by attachment name and there is no other door, so deleting the attachment "
		"alone would leave the bytes on disk unreadable AND undeleted."
	),
	# --- chat/retrieval/gate.py: the retirement floor fragments (Phase 6 §4.F) -----------
	#
	# **The same inversion `permissions.py`'s own builders get, and for the same reason: these
	# ARE a filter.** Each is a correlated sub-select reading exactly one column —
	# `retired_below_seq` — of the room a row already belongs to, ANDed into a WHERE that
	# already carries `membership_filter_sql` on that same room. It cannot widen a result set;
	# it can only remove rows. A fragment that only ever narrows cannot leak, and reporting it
	# as an unfiltered read of `Chat Room` is backwards in the way the guard's own docstring
	# describes for the membership builders.
	#
	# Why a sub-select rather than a join: the fragment has to compose into five different
	# queries with different FROM clauses, three of them already carrying a FULLTEXT match or
	# an `IN (…)` of chunk names. One shared string that can be ANDed anywhere is what stops
	# five hand-written copies of a correctness filter — and the copy that gets forgotten is
	# the one that serves the transcript of destroyed messages.
	#
	# No body column is selected by any of the three; the value is an integer watermark.
	(
		"retrieval/gate.py",
		"<module>",
		"Chat Room",
	): (
		"`_RETIRED_CHUNK_SQL` and `_retired_digest_sql`, declared at module scope beside the "
		"table constants. Each reads one integer column of the row's own room and is ANDed "
		"into a membership-filtered WHERE, so it can only narrow. Keyed on the chunk's "
		"`first_seq` rather than `last_seq` deliberately — see `chat/retire_rules."
		"wholly_retired`: for a mark set by hand rather than snapped to a chunk boundary, "
		"`last_seq` would serve a chunk straddling the mark whose body is the retired "
		"transcript verbatim."
	),
	# --- chat/governance/retention.py: what a purge WOULD destroy (Phase 6 §4.F) ---------
	#
	# Eligible on all three counts, and the third is the load-bearing one here. NO SESSION
	# USER: `bench execute` only — there is no scheduler entry and no endpoint, and
	# `test_chat_purge_surface` asserts the absence of `@frappe.whitelist()`. BOUNDED BY
	# SOMETHING OTHER THAN THE READER: every room on the site, and every message in each,
	# because the question is "what would a purge destroy" and a purge is not scoped to
	# anybody's membership. NO BODY COLUMN: `text` and `text_plain` appear in neither query,
	# and `test_chat_purge_surface` asserts that mechanically rather than by review.
	#
	# The membership filter would be actively wrong rather than merely unnecessary: a purge
	# plan filtered to what the operator happens to be a member of would under-report by
	# exactly the rooms nobody is watching — which are the rooms whose messages age out.
	#
	# **This module deletes nothing.** The whole point of §4.F as shipped is the eligibility
	# rule and the survives-a-purge table; the destructive half is blocked on Phase 5's
	# derived layer having no retirement path (`purge_rules.can_enable`). If a future commit
	# adds deletion here, these exemptions do NOT cover it — they justify a read.
	(
		"governance/retention.py",
		"_plan_rooms",
		"Chat Room",
	): (
		"Every unarchived room, selecting `name` and `last_message` only. `last_message` is "
		"what makes the room-last-message hold computable: it is a Link, so purging its "
		"target would leave the room list pointing at nothing."
	),
	(
		"governance/retention.py",
		"_messages",
		"Chat Message",
	): (
		"One room's messages, selecting `name`, `seq`, `creation`, `is_deleted` and "
		"`thread_root` — identifiers, states and timestamps, no content. These are exactly "
		"the columns the eligibility predicate needs and nothing else; a planner that read "
		"`text` would be a second unaudited transcript reader that could not meet the "
		"no-body-column test above."
	),
	(
		"governance/retention.py",
		"_live_reply_roots",
		"Chat Message",
	): (
		"`thread_root` of every undeleted reply in one room, as a set. `thread_root` is a "
		"Link, so purging a root out from under live replies orphans the thread — and those "
		"replies are inside the retention window by definition, or they would be in the "
		"batch themselves. One column, and it is a docname."
	),
	# --- chat/governance/drift.py: the divergence census (Phase 6 §4.I) ------------------
	#
	# Eligible on all three counts the rule names, and the third one is worth reading rather
	# than assuming. NO SESSION USER: a nightly scheduler job, refused outright unless BOTH
	# `Chat Settings.enabled` and `drift_detection_enabled` are on. BOUNDED BY SOMETHING
	# OTHER THAN THE READER'S IDENTITY: every mirrored room on the site, ordered by watermark
	# — the population is a property of the estate, not of anybody's membership. NO BODY
	# COLUMN: none of the four selected fields is content, and `test_chat_drift_surface`
	# asserts that mechanically rather than leaving it to review.
	#
	# The membership filter would not merely be unnecessary here, it would defeat the most
	# important finding in the module. `room_unreadable` fires on a mirrored room with **no
	# active members at all** — the case where `spaces.messages.list` has nobody to
	# impersonate, so the room silently contributes nothing to every other class and reads as
	# clean. A membership filter returns zero rooms for a room with no members. The one query
	# that must see it is the one a filter would blind.
	(
		"governance/drift.py",
		"_room_findings",
		"Chat Room",
	): (
		"Every mirrored room, oldest reconcile watermark first, selecting `name`, "
		"`gchat_space_name`, `last_reconcile_at` and `last_event_at` — identifiers and "
		"timestamps, no content. The row count itself is load-bearing beyond the findings: "
		"`drift_rules.reconcile_stale_hours` raises the staleness threshold above the sweep's "
		"own rotation (25 rooms per hourly pass), so a large site does not alarm on every "
		"room for behaving exactly as designed. A filtered subset would compute that "
		"threshold from a fraction of the estate and alarm on the whole of it."
	),
	# --- chat/governance/tombstone.py: seeing through a delete (Phase 6 §4.E) ------------
	#
	# Both reads are bounded to ONE named message and gated identically. The membership
	# filter is not merely unnecessary here but wrong: the auditor is not a participant, so
	# it would return nothing and look correct doing it.
	#
	# Eligible on all three counts the rule names. NO SESSION USER: there is one, and it is
	# checked FIRST — `_require_auditor` refuses anyone without the configured oversight role
	# before either query runs. BOUNDED BY SOMETHING OTHER THAN THE READER: a single named
	# message. NO BODY COLUMN: these DO read the body, which is the entire point of an
	# expansion — the price is a chained, reason-required `tombstone_expanded` row written
	# BEFORE the body is returned, and a failure to write it refuses the read outright.
	# `_message` deliberately has NO entry: it reads one row with `frappe.db.get_value`,
	# which this scanner does not collect (it walks `frappe.get_all`, `frappe.db.sql`,
	# `frappe.qb` and SQL literals). An exemption for it would match no query and this file's
	# own staleness check refuses that — correctly, since a waiver covering nothing today
	# silently covers whatever later takes the name.
	#
	# Noting the gap rather than quietly benefiting from it: a `db.get_value` on a body
	# column is a real read path and is not scanned here. Widening the collector is its own
	# change with its own backlog of pre-existing hits, so it is filed rather than smuggled
	# into this one.
	(
		"governance/tombstone.py",
		"_revisions",
		"Chat Message Revision",
	): (
		"The edit trail for that one message, oldest first. A message deleted after three "
		"edits has four bodies in its past, and returning only the last answers 'what did it "
		"say' with the least interesting of them while reading as though it were the whole "
		"story. Same gate, same audit row, same single-message bound as the entry above."
	),
	# --- chat/governance/export_runner.py: the export bundle (Phase 6 §4.C) --------------
	#
	# All three share one argument, so it is written once here rather than three times below.
	# This is decision #12's privileged read at its WIDEST, and the membership filter is not
	# merely unnecessary — it is wrong. The auditor is a participant in NONE of the rooms
	# being exported, so applying the shared fragment returns an empty bundle and looks
	# correct doing it.
	#
	# Eligible on all three counts the rule names. NO SESSION USER: this runs in a background
	# worker, because a room with a year of history exceeds any request timeout and a partial
	# download is worse than none. BOUNDED BY SOMETHING OTHER THAN THE READER: bounded by the
	# rooms named on the Chat Export Request, frozen at insert and restated in both
	# `manifest.json` and the `export_requested` audit row — three records that cannot drift
	# apart. NO BODY COLUMN: this one DOES read bodies, and that is what an export is; the
	# gate is the configured oversight role plus a reason graded by
	# `access_report.reason_quality`, checked in `request_export` before the row exists, and
	# the read is paid for with a chained `export_requested` governance row rather than an
	# in-memory marker.
	(
		"governance/export_runner.py",
		"_messages",
		"Chat Message",
	): (
		"The export bundle's message file. See the block comment above this entry for the "
		"full argument. Ordered by room then seq, so a re-export is byte-identical (G6-9)."
	),
	(
		"governance/export_runner.py",
		"_revisions",
		"Chat Message Revision",
	): (
		"The export bundle's revision file, and the reason the bundle exists at all: G6-9 "
		"requires a deleted body to appear HERE and not in messages.jsonl. An export that "
		"could not read this table would record that a message was deleted and never be able "
		"to say what it said, which is a gap rather than a redaction. See the block comment "
		"above, and the matching deliberate waiver in "
		"test_chat_message_revision_is_never_read_by_a_bare_query."
	),
	(
		"governance/export_runner.py",
		"_attachments",
		"Chat Attachment",
	): (
		"The export bundle's attachment list. Metadata only — the bytes come from the File "
		"doc — and bounded by TOTAL SIZE rather than by count, because one 400 MB video is "
		"the case a count limit does not see. See the block comment above this entry."
	),
	# --- chat/governance/access_report.py: the subject reading their own membership ------
	(
		"governance/access_report.py",
		"subject_rows",
		"Chat Room Member",
	): (
		"The 'who read my messages' view (decision D-1), which answers for ONE named subject "
		"and nobody else. The read is `filters={'user': subject}` — it returns the rooms that "
		"person belongs to, which IS the membership filter for this query, spelled as an "
		"equality on the very column the shared fragment exists to constrain. Applying "
		"`membership_filter_sql` on top would ask 'which rooms is the SESSION user in' of a "
		"query whose entire purpose is to ask it of the SUBJECT, and would return the wrong "
		"answer every time those two differ — which is every time an auditor or a scheduled "
		"digest builds the view on somebody else's behalf.\n"
		"Eligible on all three counts the rule names. NO SESSION USER: this is a library "
		"function with no HTTP door of its own — the module docstring says why, an endpoint "
		"here would be a path that reads the access report while recording nothing — and it "
		"is reached from a background job as well as from a request. BOUNDED BY SOMETHING "
		"OTHER THAN THE READER: bounded by `subject`, explicitly and by construction. NO BODY "
		"COLUMN: `pluck='parent'` returns room NAMES only, no message text and no membership "
		"metadata; the rows it then fetches are audit records rather than conversation. "
		"Establishing that the caller may answer for `subject` is the caller's job."
	),
	# --- chat/health.py: the only surface that can read the invocation log at all --------
	(
		"health.py",
		"_collect_triton",
		"Triton Invocation Log",
	): (
		"Phase 6 §4.H.1 panel 8, and it exists because the table was WRITE-ONLY. "
		"`Triton Invocation Log` ships zero DocPerm (ADR §F.18.1 Layer 1), which closes "
		"/api/resource, the desk list view, the form view and the report view for everybody "
		"but Administrator — the desk answers 'Page triton-invocation-log not found' — and it "
		"is also on the MCP denylist. So a failed @triton turn recorded its reason where no "
		"operator could reach it, while the failure message in chat told them to go and look "
		"there.\n"
		"Eligible on all three counts the rule names. NO SESSION USER: `health.report` is "
		"deliberately not whitelisted (see chat/health.py's header) so it has no HTTP surface "
		"and answers no request; it is a bench/operator command. BOUNDED BY SOMETHING OTHER "
		"THAN THE READER: newest ten by creation, which is a property of the table rather "
		"than of who is asking — a membership filter would be meaningless here anyway, since "
		"the rows worth reading are other people's failed turns. NO BODY COLUMN: this table "
		"is content-free by construction (ids, hashes, counts, timings, scrubbed error "
		"strings, `query_hash` and never the query text), and the fields are ENUMERATED "
		"rather than '*' so a future column carrying content cannot silently join them."
	),
	# --- chat/notifications/fanout.py: deciding who to tell, with nobody logged in ------
	(
		"notifications/fanout.py",
		"_mentioned_users",
		"Chat Mention",
	): (
		"Who a message names directly, read inside the notification background job so the "
		"suppression rule knows whether to apply the mention override. It passes all three "
		"tests. There is no session user — this runs on a queue, triggered by an insert that "
		"may itself have come from the inbound sync worker relaying a coworker's message from "
		"Google. The row set is bounded by `parent = <the one message being fanned out>` and "
		"by `mention_type = 'User'`, not by anybody's identity. And the only column selected "
		"is `user`: a mention row's span offsets are not read and the message body is not "
		"touched at all.\n"
		"Scoping it by membership would also be answering the wrong question. The reader here "
		"is the SERVER deciding what to tell each member in turn, and it has already resolved "
		"the room's active membership through the filtered statement in `_members` — the very "
		"next thing it does with this set is intersect it with that roster. A membership "
		"filter applied against `frappe.session.user` (Administrator, in a job) would expand "
		"to `1 = 1` and mean nothing, which is worse than not asking: it would read as a "
		"scoped query to the next person and be one only by accident."
	),
	# --- chat/audit.py: the writer that must not re-enter the permission stack ----------
	(
		"audit.py",
		"_was_participant",
		"Chat Room Member",
	): (
		"The one read in this package that must NOT use membership_filter_sql, and the reason "
		"is recursion rather than convenience. membership_filter_sql calls "
		"note_privileged_read for an oversight user; that writes an audit row; writing the row "
		"has to answer 'was this person a member of this room' per room — and answering it "
		"through the filter would call membership_filter_sql again, unboundedly, because "
		"auditing the audit is itself a privileged read. Both of the rules in "
		"note_privileged_read's docstring say exactly this.\n"
		"It passes the three tests above on its own merits: the row set is bounded by an "
		"explicit (room, user) pair the caller already holds rather than by the reader's "
		"identity, the only thing selected is the literal 1, and it serves an audit write "
		"rather than an HTTP request. The most it can return is a boolean about a membership "
		"the caller already named."
	),
	# --- chat/permissions.py: the module that IS the rule -----------------------------
	(
		"permissions.py",
		"<module>",
		"Chat Message",
	): (
		"_MESSAGE_TABLE, the backticked identifier the fragment builders interpolate. A "
		"module-level constant naming the table is what STOPS a typo in an f-string becoming "
		"a runtime SQL error on a permission path — which takes the desk down rather than "
		"failing a test. It is a name, not a query."
	),
	(
		"permissions.py",
		"<module>",
		"Chat Room Member",
	): ("_MEMBER_TABLE, same as _MESSAGE_TABLE above: the identifier the fragment is built from."),
	# --- chat/retrieval/gate.py: the module constants the gate's statements are built from ---
	#
	# Four entries rather than one, because the key is a triple and a file-level exemption
	# would silently cover the next function added to the gate — which, in this file above all
	# others, is the function that returns conversation to a model.
	#
	# Each is a backticked identifier held in a module-level constant, which is the correct
	# style for the same reason `permissions.py` uses it: a typo inside an f-string is a
	# runtime SQL error on a live path rather than a test failure. Every statement that
	# interpolates one lives in a function that ANDs in membership_filter_sql, and this suite
	# checks that separately — delete one of those calls and the function, not the constant,
	# is what goes red.
	(
		"retrieval/gate.py",
		"<module>",
		"Chat Context Chunk",
	): (
		"_CHUNK_TABLE. The semantic index's identifier. Every read of it in this module is "
		"bounded by BOTH an explicit `room in (<derived set>)` and the shared membership "
		"fragment, ANDed — so the fragment can never widen the derived set, which is what "
		"makes the same statement safe on the oversight path where the fragment returns "
		"`1 = 1`."
	),
	# --- chat/indexing/digest.py: an operator unlatching a flag, reading no content --------
	(
		"indexing/digest.py",
		"clear_digest_poison",
		"Chat Room Digest",
	): (
		"Selects the `name` of every row where `poisoned = 1` so an operator can clear the "
		"flag after fixing whatever caused it, and writes two integer columns. It reads no "
		"summary_text, no message, no room title — only primary keys of rows already marked "
		"broken — and returns two counts. Membership is not a meaningful filter here: the "
		"caller is a System Manager at a bench prompt with no session user, recovering a "
		"scheduler job, and scoping to 'rooms the operator is in' would leave the other "
		"rooms latched off forever with nothing to say so."
	),
	(
		"indexing/digest.py",
		"clear_digest_poison",
		"Chat Thread Digest",
	): (
		"The thread tier of the same clear, with the same filter, the same two columns and "
		"the same content-free projection."
	),
	(
		"retrieval/gate.py",
		"<module>",
		"Chat Room Digest",
	): (
		"_ROOM_DIGEST_TABLE. Same construction and the same double bound. Digests are read "
		"only where `is_stale = 0 and poisoned = 0`, which is the invalidation contract "
		"rather than a filter: a stale digest may summarise a message somebody deleted, and "
		"ERPNext holds the only copy of that text."
	),
	(
		"retrieval/gate.py",
		"<module>",
		"Chat Thread Digest",
	): ("_THREAD_DIGEST_TABLE. Same construction, same bounds, same staleness contract."),
	(
		"retrieval/gate.py",
		"<module>",
		"Chat Message",
	): (
		"_MESSAGE_TABLE. The verbatim thread tier and the authored tier both read it, and "
		"both statements pass the message-scope form of the shared fragment (the one taking "
		"a seq column) as well as the explicit room list. The watermark read is an aggregate "
		"over the same bounds and selects no body at all."
	),
	# --- chat/bench_verify.py: a probe that refuses to run beside real data -------------
	(
		"bench_verify.py",
		"_check_seq_row_lock",
		"Chat Room",
	): (
		"Two UPDATEs and no SELECT — it takes the row lock on one connection and watches a "
		"second connection block on it, which is the only way to prove the claim that makes "
		"`allocate_seq`'s read-after-UPDATE safe. It reads no column and returns no row.\n\n"
		"It is also structurally incapable of touching anybody's data: `run()` refuses to "
		"start unless EVERY chat table is empty and `Chat Settings.enabled` is 0, and the "
		"room it locks is one it created seconds earlier and deletes in a `finally`. A "
		"membership filter here would be meaningless — there is no session user under "
		"`bench execute`, and the row set is bounded by 'the row this function just made'.\n\n"
		"Scoped to this function on purpose. The next function somebody adds to this file "
		"gets no cover from this entry, which is why the key is a triple."
	),
	# --- chat/rollout.py: the @triton readiness report, bench execute only -------------
	(
		"rollout.py",
		"_room_members",
		"Chat Room Member",
	): (
		"Distinct active members across every room, as the roster for the ERPNext-link "
		"readiness report. It passes all three tests. There is no session user — the only "
		"caller is `bench execute` on the VM, which already implies shell and therefore "
		"direct database access, and the module is deliberately NOT whitelisted and is "
		"registered in no hook, scheduler or endpoint. The row set is bounded by "
		"`is_active = 1` across the whole site rather than by the reader's identity. And the "
		"only column selected is `user`: no room, no message, nothing about who talks to whom."
		"\n"
		"Scoping it to the invoking identity would also be answering the wrong question. The "
		"operator is asking 'who on the roster still needs to click Link ERPNext', and an "
		"answer that depended on which rooms the person running the command happened to be in "
		"would be silently short — which is worse than no answer, because the missing names "
		"are exactly the people nobody then chases."
	),
	# --- chat/health.py: bench execute only, aggregates only ---------------------------
	(
		"health.py",
		"_collect_rooms",
		"Chat Message",
	): (
		"COUNT(*) grouped by (room, sync_state) over non-deleted rows — no text column is "
		"selected and none could be, since the select list is fixed in the source. health.py "
		"is deliberately NOT @frappe.whitelist() and is registered in no hook, no scheduler "
		"and no endpoint: the only way to reach it is `bench execute` on the VM, which "
		"already implies shell and therefore direct database access. Scoping it to the "
		"invoking user would also be wrong — the operator needs the rooms that are stuck, "
		"which are by definition the rooms they are not in."
	),
	(
		"health.py",
		"_collect_rooms",
		"Chat Room",
	): (
		"Room metadata (title, type, provisioning state, space name, last_message_at) for the "
		"handful of rooms the stuck-job query already surfaced, so the operator reads a room "
		"title instead of a hash. Same bench-only reachability as the Chat Message aggregate "
		"above. This IS a read of the room list past the membership model and it is the "
		"single strongest reason health.py must never grow an HTTP surface."
	),
	# --- chat/sync/outbox.py: the seq allocator ----------------------------------------
	(
		"sync/outbox.py",
		"lock_room",
		"Chat Room",
	): (
		"SELECT name … FOR UPDATE on the primary key. It takes a row lock and reads one "
		"column that is also the key it was given; it discloses nothing the caller did not "
		"already have. Runs inside the inserting transaction of a message the caller is "
		"writing."
	),
	(
		"sync/outbox.py",
		"allocate_seq",
		"Chat Room",
	): (
		"UPDATE … SET seq_high_water = seq_high_water + 1, then the read-back, both under the "
		"row lock held to commit. This is the ordering allocator for a message the caller is "
		"inserting; a membership filter on it would mean a member could not be added to a "
		"room by a system job. Reads one integer."
	),
	(
		"sync/outbox.py",
		"repair_seq_high_water",
		"Chat Message",
	): (
		"SELECT coalesce(max(seq), 0) — the one legitimate MAX(seq) in the package, and only "
		"as the cold-path repair after unique(room, seq) has already proved the counter is "
		"behind reality. One integer, no body, inside the writer's own transaction."
	),
	(
		"sync/outbox.py",
		"repair_seq_high_water",
		"Chat Room",
	): ("The paired UPDATE that catches seq_high_water up, and the room lock it retakes first."),
	# --- chat/sync/inbound.py: the ingest worker ----------------------------------------
	(
		"sync/inbound.py",
		"_reader_subject",
		"Chat Room Member",
	): (
		"Picks WHICH coworker the messages.get impersonates. Ordered by user asc so a retry "
		"of the same event uses the same principal — a read that succeeds on one attempt and "
		"403s on the next is a fault that reproduces for nobody. The reader here is Google, "
		"not a person; there is no session user to scope to, and scoping it to one would pick "
		"a principal who is not in the space."
	),
	(
		"sync/inbound.py",
		"_resolve_sender",
		"Chat Room Member",
	): (
		"Maps an inbound users/{id} to an ERPNext User by matching gchat_membership_name "
		"within the ONE room the event belongs to. Selects user and the membership resource "
		"name. Runs in the ingest worker with no session; the message being filed is the "
		"scope."
	),
	(
		"sync/inbound.py",
		"_check_skew_alert",
		"Chat Message",
	): (
		"Selects exactly one integer column, clock_skew_ms, over recent inbound rows in one "
		"room, to take a median. Sustained skew means the VM's clock has drifted and every "
		"timestamp-adjacent decision downstream inherits the error (ADR §G.8 Rule 3 compares "
		"Google's lastUpdateTime against ours), so this must see every row in the room "
		"regardless of who is asking — and nobody is asking: it runs in the ingest worker."
	),
	# --- chat/sync/attachments.py -------------------------------------------------------
	(
		"sync/attachments.py",
		"sweep_pending_attachments",
		"Chat Attachment",
	): (
		"The re-download sweeper: plucks names of rows stuck in an unsettled ingest state and "
		"quiet for the cooldown, and enqueues each. A scheduler job with no session user; the "
		"only column selected is a row name. It exists because Frappe v16 wires no RQ retries "
		"and the production deploy FLUSHDBs the queue Redis, so a queued download does not "
		"survive a release while a Chat Attachment row does."
	),
	# --- chat/sync/membership.py ---------------------------------------------------------
	(
		"sync/membership.py",
		"_member_rows",
		"Chat Room Member",
	): (
		"The membership reconciler's local half: it computes the desired member set for one "
		"room and diffs it against spaces.members.list. Reading only the rooms the invoking "
		"user is in would make reconciliation depend on who triggered it, which is exactly "
		"the bug a converging diff exists to avoid. Background job, no session."
	),
	# --- chat/sync/provisioning.py --------------------------------------------------------
	(
		"sync/provisioning.py",
		"_member_rows",
		"Chat Room Member",
	): (
		"The ERPNext-derived member set for one room being provisioned, used to build the "
		"spaces.setup memberships list. Same converging-diff argument as membership.py."
	),
	(
		"sync/provisioning.py",
		"sweep_pending_provisioning",
		"Chat Room",
	): (
		"Finds rooms stuck in Pending/Provisioning with no gchat_space_name and re-enqueues "
		"them — the recovery path for a worker killed mid-provision and for rooms created "
		"while the master switch was off. Selects name, mode and last_message. A scheduler "
		"job: there is no user, and a room nobody has joined yet is precisely the row that "
		"needs provisioning."
	),
	(
		"sync/provisioning.py",
		"_enrolled_units",
		"Chat Room",
	): (
		"The bulk org sweep's work list: linked_document of enrolled-but-unbound organisation "
		"rooms, in the total order the resumable cursor depends on. Selects one Link value."
	),
	(
		"sync/provisioning.py",
		"sweep_orphaned_document_rooms",
		"Chat Room",
	): (
		"Asks the only question that is reliably true about a per-document room — does the "
		"linked document still exist and is it cancelled — because a deleted document fires "
		"no event. Selects name and the link pair. The action is archive-and-alert, never "
		"spaces.delete: a document being deleted is not consent to destroy what people said "
		"about it."
	),
	# --- chat/sync/reconcile.py ------------------------------------------------------------
	(
		"sync/reconcile.py",
		"_mirrored_rooms",
		"Chat Room",
	): (
		"The gap-recovery sweep's work list: rooms that are Ready, unarchived and actually "
		"bound to a space, oldest watermark first. This sweep is what converts a missed "
		"subscription renewal from permanent data loss into lag, so it must cover every "
		"mirrored room; a membership filter would silently make recovery depend on the "
		"invoking identity. Selects identifiers and watermarks."
	),
	(
		"sync/reconcile.py",
		"_mirrored_rooms",
		"Chat Room Member",
	): (
		"The optional user= narrowing on the same sweep — plucks the rooms one named coworker "
		"is in, so an operator can reconcile a single person's spaces. It NARROWS the sweep; "
		"it can never widen it."
	),
	(
		"sync/reconcile.py",
		"_subject_for_room",
		"Chat Room Member",
	): (
		"Chooses which member to impersonate for spaces.messages.list, preferring one with an "
		"ACTIVE subscription. Under user auth the read returns what that user can see, so the "
		"subject must be a member; skipping the room is the alternative, never reading as the "
		"app identity, which would read a DIFFERENT set of messages from the one a member "
		"sees."
	),
	# --- chat/doctype/chat_room_member/chat_room_member.py ------------------------------------
	(
		"doctype/chat_room_member/chat_room_member.py",
		"advance_read_mark",
		"Chat Room Member",
	): (
		"A single monotonic UPDATE of last_read_seq/last_read_at for one (room, user) pair, "
		"guarded by coalesce(last_read_seq, 0) < seq. It is the highest-frequency write in "
		"the feature — every member of every room on every message — and has to cost one "
		"statement. It writes the caller's own read mark and selects nothing."
	),
}

#: The one *package* exempted as a whole rather than per triple, and the properties that make
#: that defensible.
#:
#: Everything under ``chat/indexing/`` is the index **writer**: the chunker's pass, the
#: summariser and the invalidation writer. Every function in it makes the same argument —
#: scheduler job, no session user, reads across every room by design — and twenty copies of one
#: argument is twenty entries nobody reads, which is worse for a security list than one entry
#: somebody does.
#:
#: What replaces the per-triple discipline is structural rather than trusted, and is asserted
#: by :class:`TestTheWriterPackageIsUnreachableFromARequest` below and, independently, by
#: ``tests/test_chat_gate_source_scan.py``:
#:
#: * **no ``@frappe.whitelist()`` anywhere in the package** — no HTTP request can ask it for
#:   anything, which is the property that actually matters;
#: * **every public function in it is named and justified individually** (in the gate scan's
#:   ``WRITER_ENTRY_POINTS``), so the "next function somebody adds" risk lands on a list that
#:   is short enough to read;
#: * **every one of those is a registered scheduler job**, bar the staleness seam's writer.
#:
#: The reads it performs are the ones the design requires it to perform. Scoping them by
#: membership would be incoherent: there is no session user, and a summariser that only saw
#: the rooms of whoever happened to trigger it would produce a different index every run.
SYSTEM_CONTEXT_PACKAGES: dict[str, str] = {
	"indexing/": (
		"Phase 5's index writer — the chunker's pass, the rolling summariser, the thread "
		"summariser and the invalidation writer. All of it runs on the scheduler with no "
		"session user, and all of it reads across every room deliberately: an index scoped to "
		"one person's membership would be a different index for every person.\n\n"
		"This is exactly the read the retrieval gate and the MCP denylist exist to contain. "
		"The containment is at the point of CONSUMPTION rather than production: the gate is "
		"the only way a person's question reaches a chunk body, and no generic tool can reach "
		"the tables at all. This package holds no user-facing surface of its own — no "
		"whitelisted method, and every public function named and justified in the gate scan's "
		"WRITER_ENTRY_POINTS.\n\n"
		"Exempted as a package rather than per triple because every function makes the same "
		"argument, and twenty copies of one argument is twenty entries nobody re-reads. The "
		"protection a per-triple list buys — that the next function added is not silently "
		"covered — is bought here instead by the public-surface rule, which fails the build on "
		"a new public function in any file that names an index table."
	),
}

#: ``frappe.db.sql`` call sites whose query argument cannot be resolved at this call site,
#: with the reason that is acceptable. **Not a place to put a query you did not want to
#: name** — the entry has to explain how the real tables are still covered.
UNRESOLVED_QUERY_EXEMPTIONS: dict[tuple[str, str], str] = {
	(
		"health.py",
		"_sql",
	): (
		"The one guarded execute in health.py: `_sql(errors, what, query, values)` takes the "
		"query as a parameter so that a failure becomes a named entry in the report instead "
		"of a raise — a health command that crashes tells the operator nothing except that "
		"something is broken. The tables it actually touches are named in its callers' "
		"literals and ARE covered, by the second detector, as health.py entries in "
		"SYSTEM_CONTEXT_READS. Verify that by deleting one of those entries: this suite goes "
		"red naming the caller."
	),
	(
		"audit.py",
		"_acquire_chain_lock",
	): (
		"`select get_lock(%s, %s)` names no table, and that is the point: it is a MariaDB "
		"advisory lock, not a read. It serialises read-head -> insert -> commit for the audit "
		"chain, because two overlapping privileged reads that both sign the same predecessor "
		"fork the chain and produce a permanent false 'tampered' verdict. GET_LOCK is "
		"connection-scoped rather than transaction-scoped, which is exactly why a "
		"`SELECT ... FOR UPDATE` cannot do this job — the critical section contains a commit. "
		"There is no row set to scope, so there is nothing this guard could protect."
	),
	(
		"audit.py",
		"_release_chain_lock",
	): ("`select release_lock(%s)`. The other half of the advisory lock above; touches no table."),
	(
		"audit.py",
		"_acquire_governance_lock",
	): (
		"`select get_lock(%s, %s)` — the governance chain's advisory lock, and the twin of "
		"`_acquire_chain_lock` above. A SECOND lock rather than a shared one on purpose: one "
		"chain per table, so sharing a lock would serialise two independent streams against "
		"each other and make every governance write wait on an unrelated retrieval read. "
		"Names no table; there is no row set to scope."
	),
	(
		"audit.py",
		"_release_governance_lock",
	): ("`select release_lock(%s)`. The other half of the governance advisory lock; touches no table."),
	(
		"indexing/invalidate.py",
		"_rows_affected",
	): (
		"`select row_count()` — MariaDB reporting how many rows the preceding UPDATE changed. "
		"It names no table and reads none; it is the return value of a write that has already "
		"happened. The three UPDATEs whose effect it reports each name their table in a module "
		"constant and ARE covered, in this file, as indexing/ package reads.\n"
		"It exists as one helper rather than the same statement inlined three times "
		"specifically so this exemption is one entry rather than three copies of one argument "
		"— and the count matters: an invalidation that reports nothing is indistinguishable "
		"from one that matched nothing, and those need different responses."
	),
}

_TAB_RE = re.compile(r"`tab([^`]+)`")
_UNRESOLVED = "\x00?\x00"


# --------------------------------------------------------------------------- collection


@dataclass(frozen=True)
class QueryUse:
	"""One raw-query occurrence, resolved as far as the source allows."""

	file: str  #: posix path relative to ``chat/``
	function: str  #: enclosing function, or ``"<module>"``
	line: int
	kind: str  #: ``frappe.get_all`` / ``frappe.db.sql`` / ``frappe.qb`` / ``sql-literal``
	table: str  #: a DocType name, or ``"<unresolved>"``

	@property
	def key(self) -> tuple[str, str, str]:
		return (self.file, self.function, self.table)

	def __str__(self) -> str:
		return f"{self.file}:{self.line} {self.function}() {self.kind} -> {self.table}"


def _chat_python_files() -> list[Path]:
	return sorted(p for p in CHAT_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
	return path.relative_to(CHAT_DIR).as_posix()


def _dotted(node: ast.AST) -> tuple[str, ...]:
	"""``frappe.db.sql`` -> ``("frappe", "db", "sql")``; ``()`` for anything else."""
	parts: list[str] = []
	current: ast.AST = node
	while isinstance(current, ast.Attribute):
		parts.append(current.attr)
		current = current.value
	if not isinstance(current, ast.Name):
		return ()
	parts.append(current.id)
	return tuple(reversed(parts))


def _string_constants(tree: ast.Module) -> dict[str, str]:
	"""Module-level ``NAME = "literal"`` and ``NAME: Final[str] = "literal"``.

	This is what turns ``frappe.get_all(RELAY_JOB, …)`` and
	``f"select … from `tab{ROOM_DOCTYPE}`"`` from an unresolvable target into a named one.
	Naming the table in a constant is the *correct* style — a typo in a backticked
	identifier is a runtime SQL error, not a test failure — so a checker that could not
	follow one would push authors towards inline literals to keep it quiet.
	"""
	out: dict[str, str] = {}
	for node in tree.body:
		target: ast.expr | None = None
		if isinstance(node, ast.Assign) and len(node.targets) == 1:
			target = node.targets[0]
		elif isinstance(node, ast.AnnAssign):
			target = node.target
		else:
			continue
		value = node.value
		if not isinstance(target, ast.Name) or not isinstance(value, ast.Constant):
			continue
		if isinstance(value.value, str):
			out[target.id] = value.value
	return out


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
	"""Object ids of docstring constants, so prose about a table is not read as a query."""
	ids: set[int] = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			continue
		body = getattr(node, "body", None)
		if not body:
			continue
		first = body[0]
		if (
			isinstance(first, ast.Expr)
			and isinstance(first.value, ast.Constant)
			and isinstance(first.value.value, str)
		):
			ids.add(id(first.value))
	return ids


def _function_index(tree: ast.Module) -> dict[int, str]:
	"""``id(node) -> enclosing function name`` for every node in the tree.

	The innermost enclosing ``def`` wins. A nested helper reports its own name, which is what
	an exemption should have to spell — ``_pick`` inside ``sweep`` is a different reader from
	``sweep``.
	"""
	index: dict[int, str] = {}

	def walk(node: ast.AST, current: str) -> None:
		name = current
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
			name = node.name
		index[id(node)] = name
		for child in ast.iter_child_nodes(node):
			walk(child, name)

	walk(tree, "<module>")
	return index


def _resolve_string(node: ast.expr, consts: dict[str, str], globals_: dict[str, str]) -> str | None:
	"""A literal, a module constant, or an ``other_module.CONSTANT`` reference."""
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	if isinstance(node, ast.Name):
		return consts.get(node.id, globals_.get(node.id))
	if isinstance(node, ast.Attribute):
		# `subscriptions.SUBSCRIPTION_DOCTYPE` — resolved from the package-wide union, which
		# is unambiguous because a DocType-name constant that disagreed with itself across two
		# modules would be a bug this file should surface anyway (see the union builder).
		return globals_.get(node.attr)
	return None


def _sql_text(node: ast.expr, consts: dict[str, str], globals_: dict[str, str]) -> str | None:
	"""Reconstruct a SQL string, substituting resolvable f-string holes.

	An unresolvable hole becomes a sentinel rather than being dropped, so
	``f"select … from `tab{whatever}`"`` reports ``<unresolved>`` instead of quietly matching
	nothing.
	"""
	if isinstance(node, ast.Constant):
		return node.value if isinstance(node.value, str) else None
	if isinstance(node, ast.JoinedStr):
		parts: list[str] = []
		for piece in node.values:
			if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
				parts.append(piece.value)
			elif isinstance(piece, ast.FormattedValue):
				parts.append(_resolve_string(piece.value, consts, globals_) or _UNRESOLVED)
			else:  # pragma: no cover - defensive
				parts.append(_UNRESOLVED)
		return "".join(parts)
	return None


def _tables_in_sql(text: str) -> list[str]:
	found = []
	for raw in _TAB_RE.findall(text):
		found.append("<unresolved>" if _UNRESOLVED in raw else raw)
	return found


def _global_constants() -> dict[str, str]:
	"""The package-wide union of module-level string constants.

	Only names whose value is the same everywhere are kept. A name defined twice with two
	different values is dropped rather than guessed at, which downgrades any call using it to
	``<unresolved>`` — a failure, and the right one: if ``ROOM_DOCTYPE`` ever means two
	things, no reader can tell which table a query hits either.
	"""
	seen: dict[str, set[str]] = {}
	for path in _chat_python_files():
		for name, value in _string_constants(_parse(path)).items():
			seen.setdefault(name, set()).add(value)
	return {name: next(iter(values)) for name, values in seen.items() if len(values) == 1}


def _parse(path: Path) -> ast.Module:
	return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def collect(files: list[Path] | None = None) -> list[QueryUse]:
	"""Every raw-query occurrence in the chat package."""
	globals_ = _global_constants()
	uses: list[QueryUse] = []
	for path in files if files is not None else _chat_python_files():
		uses.extend(collect_from_source(path.read_text(encoding="utf-8"), _rel(path), globals_))
	return uses


def collect_from_source(source: str, rel: str, globals_: dict[str, str]) -> Iterator[QueryUse]:
	"""The analyser proper, over one module's text. Exposed so it can be self-tested."""
	tree = ast.parse(source, filename=rel)
	consts = _string_constants(tree)
	functions = _function_index(tree)
	docstrings = _docstring_constant_ids(tree)

	for node in ast.walk(tree):
		where = functions.get(id(node), "<module>")

		# (1) the three named call surfaces
		if isinstance(node, ast.Call):
			dotted = _dotted(node.func)
			if dotted in SQL_CALLS:
				kind = ".".join(dotted)
				first = node.args[0] if node.args else None
				keyword = {kw.arg: kw.value for kw in node.keywords if kw.arg}
				if dotted[-1] == "sql":
					query = first if first is not None else keyword.get("query")
					text = _sql_text(query, consts, globals_) if query is not None else None
					tables = _tables_in_sql(text) if text is not None else ["<unresolved>"]
					if not tables:
						tables = ["<unresolved>"]
					for table in tables:
						yield QueryUse(rel, where, node.lineno, kind, table)
				else:
					target = first if first is not None else keyword.get("doctype")
					table = _resolve_string(target, consts, globals_) if target is not None else None
					yield QueryUse(rel, where, node.lineno, kind, table or "<unresolved>")

		# (2) frappe.qb, matched as an attribute chain because it is a namespace
		if isinstance(node, ast.Attribute):
			dotted = _dotted(node)
			if len(dotted) >= 2 and dotted[:2] == QB_ROOT:
				yield QueryUse(rel, where, node.lineno, "frappe.qb", "<unresolved>")

		# (3) every live string literal naming a `tab<Table>` — the wrapper-proof detector
		if isinstance(node, ast.Constant | ast.JoinedStr) and id(node) not in docstrings:
			text = _sql_text(node, consts, globals_)
			if text and "`tab" in text:
				for table in _tables_in_sql(text):
					yield QueryUse(rel, where, node.lineno, "sql-literal", table)


def _in_system_context_package(rel: str) -> bool:
	"""Is this file inside a package exempted as a whole? See :data:`SYSTEM_CONTEXT_PACKAGES`."""
	return any(rel.startswith(prefix) for prefix in SYSTEM_CONTEXT_PACKAGES)


def _applies_the_filter(rel: str, function: str) -> bool:
	"""Does the enclosing function reference (or provide) the shared membership fragment?

	A proxy, and stated as one in the module docstring: it proves the fragment is *named* in
	the same function, not that it was ANDed into the right ``WHERE``.
	"""
	if function == "<module>":
		return False
	if rel == PERMISSIONS_MODULE and function in FRAGMENT_BUILDERS:
		return True
	path = CHAT_DIR / rel
	tree = _parse(path)
	for node in ast.walk(tree):
		if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
			continue
		if node.name != function:
			continue
		if node.name in FILTER_HELPERS:
			return True
		for inner in ast.walk(node):
			if isinstance(inner, ast.Name) and inner.id in FILTER_HELPERS:
				return True
			if isinstance(inner, ast.Attribute) and inner.attr in FILTER_HELPERS:
				return True
	return False


USES: list[QueryUse] = collect()

#: A floor, not an exact count, so ordinary refactoring does not fail the build — but a
#: collapse to nothing does. Every assertion below iterates USES, so an empty USES is a
#: suite that passes while guarding nothing, which is the failure mode this repo has
#: already shipped once (a pytest suite on a unittest step, collecting zero tests).
MINIMUM_EXPECTED_USES: int = 40

_MIN_REASON = 60


# --------------------------------------------------------------------------------- tests


class TestTheWalkIsNotVacuous(unittest.TestCase):
	"""Without these, every assertion below can pass by finding nothing."""

	def test_chat_python_files_were_found(self) -> None:
		self.assertTrue(
			_chat_python_files(),
			f"no Python modules under {CHAT_DIR}. Fix the path; do not delete the test.",
		)

	def test_the_walk_found_the_queries_that_are_known_to_exist(self) -> None:
		self.assertGreaterEqual(
			len(USES),
			MINIMUM_EXPECTED_USES,
			f"the raw-query walk found only {len(USES)} occurrences, below the floor of "
			f"{MINIMUM_EXPECTED_USES}. Either the package genuinely shrank — in which case "
			"lower the floor deliberately, in the same commit — or the analyser stopped "
			"matching and this suite is now green while enforcing nothing.",
		)

	def test_the_walk_sees_frappe_get_all_and_raw_sql_and_constants(self) -> None:
		"""Each detector, proven live against real source rather than assumed."""
		kinds = {use.kind for use in USES}
		for kind in ("frappe.get_all", "frappe.db.sql", "sql-literal"):
			self.assertIn(
				kind,
				kinds,
				f"the {kind} detector matched nothing in the whole package. It matched "
				"something when this was written; a change to the analyser has silenced it.",
			)
		resolved_via_constant = [
			use for use in USES if use.file == "sync/outbound.py" and use.table == "Chat Relay Job"
		]
		self.assertTrue(
			resolved_via_constant,
			"sync/outbound.py queries `Chat Relay Job` through the module constant RELAY_JOB "
			"and the resolver no longer follows it. Every such call now reports <unresolved>, "
			"which fails loudly — but if somebody 'fixes' that by exempting the file, the "
			"constant-following is gone for good.",
		)


class TestTheAnalyserItself(unittest.TestCase):
	"""Positive and negative controls, because the rule below has no live compliant example.

	Every scoped-table read in the package today is a system-context read, so the "or applies
	``membership_filter_sql``" branch of the rule is never taken by real source. A branch no
	test exercises is a branch that can rot into always-false — at which point the first
	author who does the right thing gets a failing build and learns that the correct fix is
	an exemption entry. These synthetic modules keep both branches honest.
	"""

	COMPLIANT = (
		"import frappe\n"
		"from erpnext_enhancements.chat import permissions\n"
		"def page(room):\n"
		"	where = permissions.membership_filter_sql('`tabChat Message`.`room`')\n"
		"	return frappe.db.sql(f'select name from `tabChat Message` where {where}')\n"
	)
	LEAKY = (
		"import frappe\n"
		"def page(room):\n"
		"	return frappe.db.sql('select text from `tabChat Message` where room = %s', room)\n"
	)
	LEAKY_GET_ALL = (
		"import frappe\n"
		"def page(room):\n"
		"	return frappe.get_all('Chat Message', filters={'room': room}, fields=['text'])\n"
	)
	QB = "import frappe\ndef page():\n	return frappe.qb.from_('Chat Message').select('text').run()\n"

	def _analyse(self, source: str) -> list[QueryUse]:
		return list(collect_from_source(source, "synthetic.py", {}))

	def test_a_leaky_raw_query_is_detected_and_attributed(self) -> None:
		uses = self._analyse(self.LEAKY)
		tables = {(use.function, use.table) for use in uses}
		self.assertIn(("page", "Chat Message"), tables, uses)

	def test_frappe_get_all_is_in_scope_and_not_exempt(self) -> None:
		"""`frappe.get_all` is `get_list(ignore_permissions=True)`. It is the friendly-looking
		one, so it is the one most likely to be argued out of scope."""
		uses = self._analyse(self.LEAKY_GET_ALL)
		self.assertIn(("page", "Chat Message"), {(u.function, u.table) for u in uses}, uses)

	def test_frappe_qb_is_detected_even_though_the_package_has_none_yet(self) -> None:
		uses = [u for u in self._analyse(self.QB) if u.kind == "frappe.qb"]
		self.assertTrue(
			uses,
			"frappe.qb went undetected. There is no occurrence of it in the package today, so "
			"this synthetic case is the ONLY thing keeping that surface fenced.",
		)

	def test_a_compliant_query_names_the_shared_fragment(self) -> None:
		"""The negative control: the analyser must not report the fragment's own name as a
		table, and the compliant function must be recognisable as compliant."""
		uses = self._analyse(self.COMPLIANT)
		self.assertIn("Chat Message", {u.table for u in uses})
		names = {
			inner.attr for inner in ast.walk(ast.parse(self.COMPLIANT)) if isinstance(inner, ast.Attribute)
		}
		self.assertIn(
			"membership_filter_sql",
			names,
			"the compliant fixture no longer calls the fragment builder, so it stops proving "
			"that _applies_the_filter can ever return True.",
		)

	def test_a_docstring_that_merely_mentions_a_table_is_not_a_query(self) -> None:
		source = '"""Prose about `tabChat Message` and how not to query it."""\nX = 1\n'
		self.assertEqual(self._analyse(source), [])

	def test_an_unresolvable_table_reports_unresolved_rather_than_nothing(self) -> None:
		source = "import frappe\ndef p(dt):\n	return frappe.get_all(dt, filters={})\n"
		self.assertEqual([u.table for u in self._analyse(source)], ["<unresolved>"])

	def test_an_unresolvable_f_string_hole_reports_unresolved(self) -> None:
		source = "import frappe\ndef p(t):\n	return frappe.db.sql(f'select 1 from `tab{t}`')\n"
		self.assertIn("<unresolved>", [u.table for u in self._analyse(source)])


class TestEveryQueryIsScopedOrJustified(unittest.TestCase):
	"""The rule itself. One failure message per offending occurrence, naming file:line."""

	def test_no_query_touches_a_table_nobody_classified(self) -> None:
		known = set(UNSCOPED_TABLES) | SCOPED_TABLES | set(FOREIGN_TABLES)
		offenders = sorted(
			{str(use) for use in USES if use.table not in known and use.table != "<unresolved>"}
		)
		self.assertFalse(
			offenders,
			"these chat queries touch a table this file has never classified:\n  "
			+ "\n  ".join(offenders)
			+ "\n\nEvery table a chat query names is either UNSCOPED (no per-user scope "
			"exists — a queue, a log, a roster), SCOPED (it carries or joins to what a "
			"coworker said) or FOREIGN (a Frappe core table, listed so a new one is a "
			"decision rather than a slip). Add it to the right dict WITH the reason. "
			"Defaulting an unknown table to 'probably fine' is how the leak arrives.",
		)

	def test_no_query_target_is_unresolvable(self) -> None:
		offenders = sorted(
			{
				str(use)
				for use in USES
				if use.table == "<unresolved>" and (use.file, use.function) not in UNRESOLVED_QUERY_EXEMPTIONS
			}
		)
		self.assertFalse(
			offenders,
			"these chat queries build their target from something this file cannot resolve:\n  "
			+ "\n  ".join(offenders)
			+ "\n\nAn unresolved target is not a small gap — it is a query whose table nobody "
			"can name by reading the source, and a checker that cannot see the table cannot "
			"protect it. Name the DocType in a literal or a module-level constant (the "
			"resolver follows both, and a constant is the better style anyway: a typo in a "
			"backticked identifier is a runtime SQL error on a live path, not a test "
			"failure). If the call genuinely takes its query as a parameter, add it to "
			"UNRESOLVED_QUERY_EXEMPTIONS and say there how the real tables are still covered.",
		)

	def test_every_scoped_read_applies_the_filter_or_is_a_justified_system_read(self) -> None:
		offenders: list[str] = []
		for use in USES:
			if use.table not in SCOPED_TABLES:
				continue
			if use.key in SYSTEM_CONTEXT_READS:
				continue
			if _in_system_context_package(use.file):
				continue
			if _applies_the_filter(use.file, use.function):
				continue
			offenders.append(str(use))
		self.assertFalse(
			sorted(set(offenders)),
			"these chat queries read a table carrying conversation, without the membership "
			"filter and without a written justification:\n  "
			+ "\n  ".join(sorted(set(offenders)))
			+ "\n\nThe brief calls raw SQL 'the single most likely route to a real data "
			"leak' in this feature, and the mechanism is that permission_query_conditions is "
			"appended by the get_list engine and by NOTHING ELSE — frappe.db.sql never sees "
			"it, and frappe.get_all is get_list(ignore_permissions=True).\n"
			"Two ways to fix this, and only two:\n"
			"  (a) AND permissions.membership_filter_sql(...) into the WHERE, in this "
			"function. Reuse it; do not hand-roll a second `exists (select 1 from "
			"tabChat Room Member ...)`, because two implementations of one permission rule "
			"drift, and a drift here is a leak.\n"
			"  (b) If this is genuinely a system-context read — no session user, the row set "
			"bounded by something other than the reader's identity, and no body column "
			"selected — add the (file, function, table) triple to SYSTEM_CONTEXT_READS with "
			"the reason. A read that answers an HTTP request fails all three tests and is "
			"not eligible.",
		)

	def test_chat_message_revision_is_never_read_by_a_bare_query(self) -> None:
		"""The audit table is where deleted and superseded bodies live.

		It ships zero DocPerm and — unlike Chat Message — no permission hook pair, so the
		platform refuses it to everybody except Administrator. That posture is only worth
		anything if no background job quietly reads it and hands the rows somewhere else, so
		this is asserted separately from the general rule and with no exemption mechanism:
		the day a Phase 6 oversight endpoint needs it, it should have to change this test on
		purpose.

		**That day arrived, and this is the deliberate change it asked for** (v1.289.5). The
		export bundle exists precisely so a deleted body appears in ``revisions.jsonl`` and
		*not* in ``messages.jsonl`` (G6-9) — so the exporter must read this table. An export
		that could not would record *that* a message was deleted and never be able to say what
		it said, which is not a redaction but a gap.

		The waiver is one function, named, keyed on ``file:function`` so a second function in
		the same module does not inherit it. And it pays for the read in a **stronger**
		currency than the docstring above asks for: ``note_privileged_read`` only marks
		memory, whereas ``request_export`` writes an ``export_requested`` row to
		``Chat Audit Log`` — chained, reason-required, and refused below twelve characters.
		"""
		allowed = {
			("governance/export_runner.py", "_revisions"),
			# Phase 6 §4.E. Seeing through a tombstone IS reading this table — the deleted
			# body lives here and on `Chat Message.text`, and an expansion that could not
			# show the edit history would answer "what did it say" with only the version
			# current when somebody removed it. Paid for with a `tombstone_expanded` row
			# that is chained, reason-required, and whose failure refuses the read.
			("governance/tombstone.py", "_revisions"),
		}
		offenders = sorted(
			{
				str(use)
				for use in USES
				if use.table == "Chat Message Revision" and (use.file, use.function) not in allowed
			}
		)
		self.assertFalse(
			offenders,
			"these queries read Chat Message Revision directly:\n  "
			+ "\n  ".join(offenders)
			+ "\n\nThat table holds the body of every superseded edit and every deleted "
			"message — it is the one place where a leak survives the user's decision to "
			"delete, which is why the contract gives it TIGHTER permissions than the message "
			"table rather than equal ones. Phase 6's oversight endpoint is the intended "
			"reader, gated on the configured oversight role and paying for the read with an "
			"audit row (permissions.note_privileged_read). If that is what you are building, "
			"change this test deliberately and say so in the changelog.",
		)


class TestTheWriterPackageIsUnreachableFromARequest(unittest.TestCase):
	"""The price of the one package-level exemption in this file.

	``SYSTEM_CONTEXT_PACKAGES`` waives the per-triple rule for the index writer, on the
	grounds that it has no user-facing surface. These assertions are what make that a fact
	rather than a claim — and they are duplicated in ``test_chat_gate_source_scan.py`` on
	purpose, because the two files police different rules and either one being deleted should
	not silently remove the other's precondition.
	"""

	def test_every_exempted_package_states_why(self) -> None:
		for prefix, reason in sorted(SYSTEM_CONTEXT_PACKAGES.items()):
			with self.subTest(package=prefix):
				self.assertGreaterEqual(
					len(reason.strip()),
					_MIN_REASON,
					f"SYSTEM_CONTEXT_PACKAGES[{prefix!r}] has no real justification. A "
					"package-level waiver is broader than anything else in this file; say why "
					"a per-triple list would be worse, and say what replaces it.",
				)

	def test_every_exempted_package_exists_and_is_not_empty(self) -> None:
		for prefix in sorted(SYSTEM_CONTEXT_PACKAGES):
			with self.subTest(package=prefix):
				directory = CHAT_DIR / prefix.rstrip("/")
				self.assertTrue(directory.is_dir(), f"{directory} does not exist")
				self.assertTrue(
					[p for p in directory.glob("*.py") if p.name != "__init__.py"],
					f"{directory} holds no modules, so this waiver covers nothing today and "
					"whatever is put there tomorrow.",
				)

	def test_no_exempted_package_exposes_a_whitelisted_method(self) -> None:
		"""The property the waiver actually rests on. Everything else is defence in depth.

		A whitelisted method in the index writer would be a cross-room reader reachable over
		HTTP by any signed-in user — which is precisely the one thing the retrieval gate
		exists to be the only instance of.
		"""
		offenders: list[str] = []
		for prefix in sorted(SYSTEM_CONTEXT_PACKAGES):
			for path in sorted((CHAT_DIR / prefix.rstrip("/")).rglob("*.py")):
				tree = _parse(path)
				for node in ast.walk(tree):
					if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
						continue
					for decorator in node.decorator_list:
						target = decorator.func if isinstance(decorator, ast.Call) else decorator
						if ".".join(_dotted(target)[-2:]) == "frappe.whitelist":
							offenders.append(f"{_rel(path)}:{node.lineno} {node.name}")
		self.assertFalse(
			offenders,
			f"these functions are whitelisted inside a package this file exempts wholesale: "
			f"{offenders}.\n\n"
			"The exemption is granted because the package has no user-facing surface and its "
			"reads are scheduler-context reads. That is no longer true. Either move the "
			"endpoint out, or remove the package waiver and justify every read in it per "
			"(file, function, table) like everything else here.",
		)

	def test_no_exempted_package_is_imported_by_the_request_surface(self) -> None:
		"""The second door: reachable *indirectly* from an endpoint is still reachable.

		``chat/api/`` is the request surface. The one legitimate crossing is the retrieval
		gate importing the embedding client, which names no chat table and cannot reach a row
		— so the check is on the three modules that can.
		"""
		reachable = ("indexer", "digest", "invalidate")
		offenders: list[str] = []
		for path in sorted((CHAT_DIR / "api").rglob("*.py")):
			source = path.read_text(encoding="utf-8")
			for module in reachable:
				if f"indexing.{module}" in source or f"indexing import {module}" in source:
					offenders.append(f"{_rel(path)} imports chat.indexing.{module}")
		self.assertFalse(
			offenders,
			f"{offenders}\n\nA module under chat/api/ answers HTTP requests. Importing the "
			"index writer there puts a cross-room reader one call away from a request "
			"handler, which is the whole thing the package waiver assumes cannot happen. The "
			"staleness seam is the one intended synchronous caller, and it lives in "
			"chat/seams.py rather than in an endpoint precisely so this check can be this "
			"blunt.",
		)


class TestTheAllowlistsAreHonest(unittest.TestCase):
	"""An exemption list nobody prunes is a hole nobody is watching."""

	def test_every_unscoped_table_states_why_it_has_no_per_user_scope(self) -> None:
		for table, reason in sorted(UNSCOPED_TABLES.items()):
			with self.subTest(table=table):
				self.assertGreaterEqual(
					len(reason.strip()),
					_MIN_REASON,
					f"UNSCOPED_TABLES[{table!r}] has no real justification. The allowlist is "
					"for tables where 'the rooms you are in' is not a meaningful filter; say "
					"which columns the table holds and why none of them is conversation.",
				)

	def test_every_foreign_table_states_why_a_chat_query_may_name_it(self) -> None:
		for table, reason in sorted(FOREIGN_TABLES.items()):
			with self.subTest(table=table):
				self.assertGreaterEqual(len(reason.strip()), _MIN_REASON, table)

	def test_every_system_context_read_states_why_it_is_one(self) -> None:
		for key, reason in sorted(SYSTEM_CONTEXT_READS.items()):
			with self.subTest(read=key):
				self.assertGreaterEqual(
					len(reason.strip()),
					_MIN_REASON,
					f"SYSTEM_CONTEXT_READS[{key!r}] has no real justification. Three things "
					"have to be true and the entry has to say so: no session user, a row set "
					"bounded by something other than the reader's identity, and no body "
					"column selected.",
				)

	def test_no_system_context_read_is_stale(self) -> None:
		"""A triple that no longer matches any query exempts nothing today and whatever takes
		that function name tomorrow."""
		live = {use.key for use in USES}
		stale = sorted(key for key in SYSTEM_CONTEXT_READS if key not in live)
		self.assertFalse(
			stale,
			f"these SYSTEM_CONTEXT_READS entries match no query in the package: {stale}. "
			"A function was renamed, moved or deleted. Delete the entry in the same commit — "
			"a stale exemption silently covers whatever later takes that name, and nobody "
			"re-reads an exemption they did not have to write.",
		)

	def test_no_unresolved_exemption_is_stale(self) -> None:
		live = {(use.file, use.function) for use in USES if use.table == "<unresolved>"}
		stale = sorted(key for key in UNRESOLVED_QUERY_EXEMPTIONS if key not in live)
		self.assertFalse(stale, f"stale UNRESOLVED_QUERY_EXEMPTIONS entries: {stale}")

	def test_the_unscoped_allowlist_is_exactly_the_tables_named_here(self) -> None:
		"""Pinned by name so growing it is a deliberate edit to a test, not a quiet dict entry.

		Four of these are the operational tables the ADR keeps at zero DocPerm and off any
		membership model. A fifth would have to be a table somebody decided carries no
		per-user content — a decision worth making in front of a reviewer.

		The two audit tables are the deliberate exception, and they are the *inverse* case
		rather than another operational queue: they hold no conversation, and scoping them by
		the reader's own membership would hide precisely the rows they exist to surface — the
		reads where the reader was **not** a member. They also carry DocPerm (§F.12, System
		Manager read/report) rather than none, which is why they are listed here with a reason
		and not quietly waved through.
		"""
		self.assertEqual(
			sorted(UNSCOPED_TABLES),
			[
				# Phase 6's governance log. Listed here rather than waved through because it
				# is the newest member of the audit family and the family is the reason this
				# pin exists: these tables are unscoped precisely BECAUSE the rows worth
				# reading are the ones about somebody else.
				"Chat Audit Log",
				# Phase 6 §4.I's divergence census. Unscoped because its most important class
				# is "this mirrored room has no active members" — a membership filter would
				# hide exactly the room that reports clean on everything else.
				"Chat Drift Report",
				"Chat Event Subscription",
				"Chat Inbound Event",
				# Phase 6 §4.H's alert board. Unscoped because every reader is a scheduler, a
				# worker or a `bench execute` command with no session user — and a membership
				# filter on the deduplication lookup would return nothing and look correct
				# doing it, which is the failure that made the oversight read path useless for
				# a whole phase.
				"Chat Ops Alert",
				"Chat Provisioning Run",
				# Phase 4. A device registry, not a conversation table: endpoint, keys, user
				# agent and delivery health, and no room or message reaches it at all. Its two
				# sweeps run on a schedule with no session user and must see every row, which is
				# what makes it unscoped rather than merely unfiltered.
				"Chat Push Subscription",
				"Chat Relay Job",
				"Chat Retrieval Audit",
				"Chat Retrieval Audit Room",
			],
		)

	def test_the_fragment_builders_exist_and_execute_nothing(self) -> None:
		"""The one exemption granted by function name rather than by a written reason, made
		structural so it cannot be abused.

		``permissions.py``'s builders return SQL *text* — they are the thing every other query
		is meant to AND in — so a backticked table name inside one is the filter, not a read.
		That argument holds only while they run nothing. The day a builder grows a
		``frappe.db.sql`` or a ``frappe.get_all`` it has become a query, and its table name
		stops being self-evidently safe.
		"""
		tree = _parse(CHAT_DIR / PERMISSIONS_MODULE)
		found: set[str] = set()
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			if node.name not in FRAGMENT_BUILDERS:
				continue
			found.add(node.name)
			executed = [
				f"{node.name}():{inner.lineno} {'.'.join(_dotted(inner.func))}"
				for inner in ast.walk(node)
				if isinstance(inner, ast.Call) and _dotted(inner.func) in SQL_CALLS
			]
			self.assertFalse(
				executed,
				f"{executed} — a FRAGMENT_BUILDERS function now executes a query. It is "
				"exempted from the scoping rule on the grounds that it only BUILDS the "
				"membership fragment; that is no longer true. Move the execution out, or "
				"remove the name from FRAGMENT_BUILDERS and justify the read properly.",
			)
		missing = sorted(FRAGMENT_BUILDERS - found)
		self.assertFalse(
			missing,
			f"FRAGMENT_BUILDERS names {missing}, which no longer exist in "
			f"chat/{PERMISSIONS_MODULE}. A stale name exempts nothing today and whatever takes "
			"it tomorrow.",
		)

	def test_no_table_is_both_scoped_and_unscoped(self) -> None:
		overlap = sorted(SCOPED_TABLES & set(UNSCOPED_TABLES))
		self.assertFalse(overlap, f"{overlap} are classified both ways; one of the two is wrong")
		overlap = sorted(SCOPED_TABLES & set(FOREIGN_TABLES))
		self.assertFalse(overlap, f"{overlap} are both scoped and foreign")


if __name__ == "__main__":  # pragma: no cover
	unittest.main()

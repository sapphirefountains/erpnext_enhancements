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
		"""
		offenders = sorted({str(use) for use in USES if use.table == "Chat Message Revision"})
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

	def test_the_unscoped_allowlist_is_exactly_the_four_the_brief_names(self) -> None:
		"""Pinned by name so growing it is a deliberate edit to a test, not a quiet dict entry.

		These four are the operational tables the ADR keeps at zero DocPerm and off any
		membership model. A fifth would have to be a table somebody decided carries no
		per-user content — a decision worth making in front of a reviewer.
		"""
		self.assertEqual(
			sorted(UNSCOPED_TABLES),
			[
				"Chat Event Subscription",
				"Chat Inbound Event",
				"Chat Provisioning Run",
				"Chat Relay Job",
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

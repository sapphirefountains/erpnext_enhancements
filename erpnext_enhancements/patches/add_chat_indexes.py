"""Create the chat module's composite indexes and composite unique constraints.

**Why this is a patch and not schema.** Frappe's DocType JSON has no first-class
composite-index field — ``search_index: 1`` produces a single-column index and nothing
else — so every composite in ADR 0009 §F.4.2 / §F.5.2 / §F.6.6 / §F.9.1 / §F.10.1 exists
**only** if this patch runs. That makes a silent no-op here the worst outcome available:
the design's central uniqueness claims (echo suppression, per-room sequence, per-room FIFO)
and its only pagination index would be unbacked, and nothing would say so until either a
duplicate message appeared in production or the backlog query started filesorting the
whole table. So skips are logged loudly and DDL failures are raised, never swallowed.

**Single-column uniques are deliberately absent from this file.** ADR §F.2's trap:
``frappe.model.base_document.get_valid_dict`` coerces ``""`` → ``NULL`` *only* when the
DocField itself carries ``unique: 1``, and MariaDB allows unlimited ``NULL``s in a unique
index. ``gchat_message_name``, ``gchat_attachment_name``, ``gchat_space_name``,
``pubsub_message_id`` and ``subscription_uid`` are therefore declared ``unique: 1`` on the
field, where the coercion fires and un-relayed rows can coexist. Adding the same constraint
here instead would leave those rows empty-stringed and the *second* un-relayed row in the
whole table would fail to insert — in production only, invisible to any test that relays
synchronously. **Do not "complete" this file by moving them here.**

Every composite below is safe against the same trap because each of its columns is either
``reqd`` at insert (``room``/``seq``/``user``/``client_message_id``/``job_seq``/``status``)
or nulled as a pair by ``ChatRoom.before_insert``'s ``_blank_pair_to_null`` — a group room
stores ``(NULL, NULL)`` for the DM pair rather than ``("", "")``, which is what lets two
group rooms coexist under ``unique(dm_user_1, dm_user_2)``.

**Idempotence.** ``frappe.db.add_index`` (v16 signature
``add_index(doctype, fields, index_name=None)``) and ``frappe.db.add_unique``
(``add_unique(doctype, fields, constraint_name=None)``) both carry their own guards, but
this patch checks ``information_schema.STATISTICS`` first anyway: the check is what makes
the re-run provably a no-op rather than dependent on framework internals, and it is what
lets the patch report *which* index it created. Index names are the framework's own default
shapes (``a_b_index`` / ``unique_a_b``) so a constraint Frappe later adds by itself collides
by name and is skipped rather than duplicated.

**Two entry points, on purpose.** :func:`execute` is the patch and raises on failure;
:func:`ensure_chat_indexes` is registered in ``hooks.py``'s ``after_migrate`` as a backstop
for the fresh-site case and logs instead. The reasoning is on
:func:`ensure_chat_indexes` — read it before collapsing them into one.

**A missing table is reported as a banner on stdout, not as eleven Error Log rows.** That
changed after 2026-08-09, when this patch correctly skipped all 11 indexes because the Chat
module had never reached model sync — and the deploy reported success, because a skip note in
the Error Log is indistinguishable from a clean run at the deploy level. ``bench migrate``'s
stdout is the deploy log, so :func:`_report_skips` prints there as well as logging one
aggregated row. It still does not *raise*: an exception in ``post_model_sync`` aborts the
migrate after the code has already been reset to the new release, which leaves the site
running new code against a half-migrated schema — worse than a loud log. The structural
guarantee that the module installs at all belongs one layer up, in
``setup/module_map.py`` + ``tests/test_module_installability.py``.

Run twice; the second run writes nothing and touches nothing.
"""

import frappe

#: ``(doctype, columns, constraint_name)`` — composite UNIQUE constraints.
#: Order is stable so the log reads the same on every site.
UNIQUE_CONSTRAINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
	# ADR §F.6.6. The backlog page (`room = %s AND seq < %s ORDER BY seq DESC`), the
	# unread count, and the structural enforcement of D6's "one message per (room, seq)".
	# It is the last of those that is load-bearing: seq is allocated by incrementing
	# `Chat Room.seq_high_water` under that row's lock, NOT by a MAX(seq) probe over this
	# table (an earlier revision of this comment said otherwise) — so this constraint is
	# the guarantee and the counter is only the optimisation that keeps it from being
	# tested. `outbox.insert_message` reads this exact name out of MariaDB's duplicate-entry
	# text to tell "somebody took this seq, allocate another" from "this Google message is
	# already stored"; renaming it turns a retry into an unhandled error.
	("Chat Message", ("room", "seq"), "unique_room_seq"),
	# ADR §F.6.6. Echo suppression in one probe (§G.3): an inbound message carrying a
	# `client-` id we issued is definitionally our own message coming back. Scoped to
	# `room` because Google guarantees messageId uniqueness within a space.
	("Chat Message", ("room", "client_message_id"), "unique_room_client_message_id"),
	# ADR §F.5.2. The membership check that gates every read in the system, and the
	# structural fix for the duplicate-member row Raven needed a patch for.
	("Chat Room Member", ("room", "user"), "unique_room_user"),
	# ADR §F.4.2. What makes get_or_create_document_room() idempotent without a lock
	# when two workers race on the same Project.
	("Chat Room", ("linked_doctype", "linked_document"), "unique_linked_doctype_linked_document"),
	# ADR §F.4.2. One DM per pair, with the pair stored lexicographically by
	# ChatRoom.before_insert so (A,B) and (B,A) are the same row.
	("Chat Room", ("dm_user_1", "dm_user_2"), "unique_dm_user_1_dm_user_2"),
	# ADR §F.9.1. The per-room FIFO that makes §G.8's Create-Before-Edit rule a property
	# of the schema rather than a hope.
	("Chat Relay Job", ("room", "job_seq"), "unique_room_job_seq"),
)

#: ``(doctype, columns, index_name)`` — non-unique composite indexes, each named with the
#: one query it serves. Anything not listed here was rejected in the ADR with a reason;
#: re-read §F.4.2/§F.6.6's "Rejected" tables before adding to this tuple.
INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
	# ADR §F.6.6. The thread panel: every reply under this root, in order.
	("Chat Message", ("thread_root", "seq"), "thread_root_seq_index"),
	# ADR §F.6.6. An inbound threaded reply names spaces/{s}/threads/{t} and we do not
	# know its ERPNext parent; find the root message already bound to that thread.
	("Chat Message", ("gchat_thread_name",), "gchat_thread_name_index"),
	# ADR §F.5.2. "Every room this user is currently in" — the SPA's first query, the
	# unread badge, and Phase 5's server-derived allowed_rooms. unique(room, user)
	# cannot serve it: its leading column is `room`.
	("Chat Room Member", ("user", "is_active"), "user_is_active_index"),
	# ADR §F.9.1. The outbox sweeper's only query.
	("Chat Relay Job", ("status", "available_at"), "status_available_at_index"),
	# ADR §F.10.1. The stuck-event sweeper, and the drain query if the puller falls behind.
	("Chat Inbound Event", ("status", "received_at"), "status_received_at_index"),
)


def execute() -> None:
	"""The patch entry point. Creates every composite, and **raises** if it cannot.

	Loud on purpose: a half-applied index set is a design whose uniqueness guarantees
	are not actually guaranteed, and the only thing worse than a migrate that stops here
	is one that continues. An index already present is a silent no-op; a *missing table or
	column* is reported as a banner on stdout plus one aggregated Error Log row, because at
	this point in a migrate it means the module never synced (see :func:`_report_skips`); and
	a real DDL failure is collected so one bad constraint cannot hide the state of the other
	ten, and re-raised at the end.
	"""
	failures = _create_all("add_chat_indexes (patch)")
	if failures:
		raise frappe.ValidationError("add_chat_indexes could not create: " + "; ".join(failures))


def ensure_chat_indexes() -> None:
	"""The ``after_migrate`` entry point. Same work, **never raises**.

	Two entry points because the two callers want opposite failure behaviour, and
	collapsing them would be wrong in one direction or the other:

	* The patch runs **once**. If it cannot create a unique constraint the right answer
	  is to stop the migrate and make a human look, which is what :func:`execute` does.
	* This runs on **every** migrate, and every deploy is a migrate. A hook that raises
	  here does not report one bad constraint — it bricks the deploy pipeline until
	  somebody edits `hooks.py`. So the backstop logs and returns.

	The backstop exists because a patch is not guaranteed to run at all: ``bench
	install-app`` marks an app's whole `patches.txt` as already-executed, so on a fresh
	site the patch is skipped and never runs again — and *that* failure is silent, since
	inserts succeed happily without a unique constraint right up until two of them should
	have collided.

	**This function is registered on both `after_migrate` and `after_install`, and it
	needs both.** `after_migrate` does not run during `bench install-app` — core runs
	`before_install`, `after_install` and `after_sync` there and nothing else — so on a
	fresh site the `after_migrate` registration alone would never fire, which is the very
	case the paragraph above says the backstop exists for. Verified against Frappe v16
	`installer.install_app`; do not drop either registration.
	"""
	failures = _create_all("ensure_chat_indexes (after_migrate)")
	if failures:
		_note("could not create: " + "; ".join(failures))


def _create_all(source: str) -> list[str]:
	"""Create every composite in :data:`UNIQUE_CONSTRAINTS` and :data:`INDEXES`.

	Args:
		source: Which entry point is running, named in the skip banner so a deploy log
			says whether it was the one-shot patch or the every-migrate backstop.

	Returns:
		One string per failure, empty when everything exists or was created.
	"""
	failures: list[str] = []
	skipped: list[str] = []

	for doctype, columns, constraint_name in UNIQUE_CONSTRAINTS:
		_apply(doctype, columns, constraint_name, unique=True, failures=failures, skipped=skipped)

	for doctype, columns, index_name in INDEXES:
		_apply(doctype, columns, index_name, unique=False, failures=failures, skipped=skipped)

	if skipped:
		_report_skips(source, skipped)

	return failures


def _apply(
	doctype: str,
	columns: tuple[str, ...],
	name: str,
	*,
	unique: bool,
	failures: list[str],
	skipped: list[str],
) -> None:
	"""Create one index/constraint if the table, the columns and the gap all exist."""
	table = f"tab{doctype}"

	if not _table_exists(doctype):
		# Not a failure in itself — but on a real migrate it means the chat DocTypes did
		# not sync, which is a silently broken deploy. Collected and reported once, loudly,
		# by _report_skips rather than logged per index.
		skipped.append(f"{table}: table missing, skipped index {name}")
		return

	missing = [column for column in columns if not _has_column(doctype, column)]
	if missing:
		skipped.append(f"{table}: column(s) {', '.join(missing)} missing, skipped index {name}")
		return

	if _index_exists(table, name):
		return

	try:
		if unique:
			frappe.db.add_unique(doctype, list(columns), constraint_name=name)
		else:
			frappe.db.add_index(doctype, list(columns), index_name=name)
	except Exception as exc:  # every failure is reported by the caller, none swallowed
		# Do not include the exception's frame locals anywhere: this runs inside migrate
		# and a bare re-raise out of a job publishes them to the Error Log.
		failures.append(f"{table}({', '.join(columns)}) as {name}: {exc}")


def _table_exists(doctype: str) -> bool:
	try:
		return bool(frappe.db.table_exists(doctype, cached=False))
	except Exception:
		return False


def _has_column(doctype: str, column: str) -> bool:
	"""``frappe.db.has_column``, guarded — the repo-wide convention for column reads."""
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _index_exists(table: str, index_name: str) -> bool:
	"""Is an index of this *name* already on this table?

	Name rather than column set: the framework's helpers key on the name too, so a
	name match is exactly the condition under which they would no-op.
	"""
	try:
		return bool(
			frappe.db.sql(
				"""
				select 1 from information_schema.STATISTICS
				where table_schema = database()
					and table_name = %s
					and index_name = %s
				limit 1
				""",
				(table, index_name),
			)
		)
	except Exception:
		return False


def _report_skips(source: str, skipped: list[str]) -> None:
	"""Say — unmissably — that composites the design depends on were not created.

	Two channels, because the 2026-08-09 incident proved one is not enough:

	* **stdout**, because ``bench migrate``'s stdout *is* the deploy log. Eleven Error Log
	  rows are invisible to a pipeline that only checks the exit code; a banner is not.
	* **one** aggregated Error Log row rather than one per index, so the Error Log carries
	  the diagnosis instead of eleven copies of a symptom.

	The most likely cause is named explicitly. A missing ``tabChat …`` at this point almost
	never means "mid-install" — it means model sync never walked ``erpnext_enhancements/chat/``,
	which is the stale-app-module-map failure ``setup/module_map.py`` exists to prevent.
	"""
	total = len(UNIQUE_CONSTRAINTS) + len(INDEXES)
	headline = (
		f"CHAT SCHEMA INCOMPLETE: {source} skipped {len(skipped)} of {total} composite "
		f"indexes/constraints because their tables or columns do not exist"
	)
	remedy = (
		"Most likely cause: the Chat module was not visible to frappe.model.sync (a stale "
		"app_modules map), so no chat DocType was imported and no table was created. "
		"See erpnext_enhancements/setup/module_map.py and chat/README.md. Invariant I2 "
		"(unique gchat_message_name) and the per-room uniqueness constraints are UNBACKED "
		"until this reports clean."
	)

	try:
		print("")
		print("*" * 78)
		print(f"[erpnext_enhancements] {headline}")
		for entry in skipped:
			print(f"    {entry}")
		print(f"    {remedy}")
		print("*" * 78)
		print("")
	except Exception:
		pass

	_note(headline + "\n\n" + "\n".join(skipped) + "\n\n" + remedy)


def _note(message: str) -> None:
	"""Record a skip where a human will find it, without failing the migrate.

	Keyword form on purpose. ``frappe.log_error(title, message)`` only swaps its two
	positional arguments when ``"Traceback" in title`` — which never holds here — so the
	positional call put the diagnostic in the 140-char title and the constant in the body.
	Naming both is the only form that cannot be read the wrong way round.
	"""
	try:
		frappe.log_error(title="add_chat_indexes", message=message)
	except Exception:
		pass

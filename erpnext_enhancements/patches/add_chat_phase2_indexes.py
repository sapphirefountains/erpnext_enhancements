"""Create the Phase 2 chat composites — the audit trail's uniqueness and the sweeper's queries.

**A companion to ``add_chat_indexes``, not a replacement, and deliberately a second file.**
That patch is spent: it is written to ``Patch Log`` on every site that has run it, and
``run_all()`` filters the pending list against ``Patch Log`` with no re-run path, so adding a
row to its tuples would create an index on a *fresh* site and never on production. A new
module is a new ``Patch Log`` key. The two files stay separate for that reason alone; the
structure below is a deliberate mirror of ``add_chat_indexes`` so a reader who has understood
one has understood both, and any future fix applies to the same shape twice.

**Why this is a patch and not schema.** Frappe's DocType JSON has no first-class
composite-index field — ``search_index: 1`` produces a single-column index and nothing else —
so every composite here exists **only** if this runs. Two of them are correctness, not
performance:

* ``unique(message, revision_no)`` on ``Chat Message Revision`` is what makes "the same edit
  recorded twice" a failed insert instead of two audit rows both claiming to be revision 4.
  Every writer of that table is retried — the relay sweeper re-drives a crashed job, Pub/Sub
  redelivers at least once — so without the constraint the duplicate is not unlikely, it is
  scheduled.
* ``(status, lease_expires_at)`` on ``Chat Relay Job`` backs the crashed-worker sweep. Without
  it the sweep filesorts the whole outbox on every run, which is survivable right up until the
  run where the outbox is large *because* workers have been crashing.

**Single-column uniques are deliberately absent, same as in ``add_chat_indexes``.** ADR §F.2's
trap: ``frappe.model.base_document.get_valid_dict`` coerces ``""`` → ``NULL`` *only* when the
DocField itself carries ``unique: 1``, and MariaDB allows unlimited ``NULL``s in a unique index
and exactly one ``""``. A single-column unique added here instead of on the field would let the
*second* unwritten row in the whole table fail to insert — in production only, invisible to any
test that populates the column synchronously. **Do not "complete" this file by moving one here.**

Every composite below is safe against that trap because each of its columns is either ``reqd``
at insert (``message``, ``revision_no``, ``room``, ``status``) or a framework column that is
always populated (``creation``, ``available_at``). ``lease_expires_at`` is the one nullable
member, and it is fine: it is the *trailing* column of ``(status, lease_expires_at)``, so NULLs
sort together and the index still resolves the leading ``status`` predicate.

**Idempotence.** Index names are the framework's own default shapes (``a_b_index`` /
``unique_a_b``) and ``information_schema.STATISTICS`` is checked by name before any DDL, so a
re-run is provably a no-op rather than one that depends on framework internals. One entry —
``Chat Inbound Event (status, received_at)`` — is **already** declared in ``add_chat_indexes``
under the same name and is re-asserted here on purpose: Phase 2's inbound drain is the first
code that actually depends on it, and a name match means the check finds it and skips. If that
patch was one of the ones the 2026-08-09 bootstrap incident burned, this is the file that
creates it.

**Two entry points, on purpose.** :func:`execute` is the patch and raises on failure;
:func:`ensure_chat_phase2_indexes` is registered in ``hooks.py``'s ``after_migrate`` **and**
``after_install`` as a backstop, and logs instead. Read that function's docstring before
collapsing them into one — the two callers want opposite failure behaviour, and a patch that
logs as executed while doing nothing has already happened twice in this repo.

Run twice; the second run writes nothing and touches nothing.
"""

import frappe

#: ``(doctype, columns, constraint_name)`` — composite UNIQUE constraints.
#: Order is stable so the log reads the same on every site.
UNIQUE_CONSTRAINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
	# Phase 2 §4.F. The edit/delete audit trail's identity. Both columns are reqd, so
	# neither can arrive as the empty string ADR §F.2 warns about. This is the structural
	# form of "one row per change", and it is what a retried writer collides with instead
	# of silently recording the same edit twice under two different hash names.
	("Chat Message Revision", ("message", "revision_no"), "unique_message_revision_no"),
)

#: ``(doctype, columns, index_name)`` — non-unique composite indexes, each named with the one
#: query it serves. Anything not listed here was rejected in the ADR with a reason; re-read
#: §F.6.6 / §F.9.1 / §F.10.1's "Rejected" tables before adding to this tuple.
INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
	# Phase 2 §4.F. "Everything that changed in this room, newest first" — the Phase 6
	# oversight read, and the reconciliation report. `room` leads because the membership
	# filter is always a room predicate; `creation` follows because the audit table is the
	# one place where wall-clock order IS the question being asked. (Message ORDER is still
	# seq and never a timestamp — that is Chat Message's index, not this one.)
	("Chat Message Revision", ("room", "creation"), "room_creation_index"),
	# ADR §G.8 Rule 1 / §F.9.1. THE SWEEPER'S EXACT QUERY SHAPE: the next runnable job for
	# one room is `room = %s AND status = 'Pending' AND available_at <= now() ORDER BY
	# job_seq`. `(status, available_at)` from add_chat_indexes serves the global drain;
	# this one serves the per-room FIFO, which is the query the relay actually runs once a
	# room is claimed, and its leading column has to be `room` to do that.
	("Chat Relay Job", ("room", "status", "available_at"), "room_status_available_at_index"),
	# ADR §G.8 Rule 4 / §4.D. The crashed-worker sweep: rows still `In Progress` past their
	# lease. Without this the sweep scans the whole outbox every run — and the run where
	# that matters is the one where the outbox is large BECAUSE workers have been dying.
	("Chat Relay Job", ("status", "lease_expires_at"), "status_lease_expires_at_index"),
	# ADR §F.10.1. Already declared in add_chat_indexes under this exact name; re-asserted
	# because Phase 2's inbound drain is the first code that depends on it and because that
	# patch is spent on any site where it ran before the chat tables existed. A name match
	# means _index_exists finds it and this is a no-op.
	("Chat Inbound Event", ("status", "received_at"), "status_received_at_index"),
)


def execute() -> None:
	"""The patch entry point. Creates every composite, and **raises** if it cannot.

	Loud on purpose, for the same reason ``add_chat_indexes.execute`` is: a half-applied
	index set is a design whose uniqueness guarantees are not actually guaranteed, and the
	only thing worse than a migrate that stops here is one that continues. An index already
	present is a silent no-op; a *missing table or column* is reported as a banner on stdout
	plus one aggregated Error Log row (see :func:`_report_skips`); and a real DDL failure is
	collected so one bad constraint cannot hide the state of the others, then re-raised.
	"""
	failures = _create_all("add_chat_phase2_indexes (patch)")
	if failures:
		raise frappe.ValidationError("add_chat_phase2_indexes could not create: " + "; ".join(failures))


def ensure_chat_phase2_indexes() -> None:
	"""The ``after_migrate`` / ``after_install`` entry point. Same work, **never raises**.

	Two entry points because the two callers want opposite failure behaviour, and collapsing
	them would be wrong in one direction or the other:

	* The patch runs **once**. If it cannot create a unique constraint the right answer is to
	  stop the migrate and make a human look, which is what :func:`execute` does.
	* This runs on **every** migrate, and every deploy is a migrate. A hook that raises here
	  does not report one bad constraint — it bricks the deploy pipeline until somebody edits
	  ``hooks.py``. So the backstop logs and returns.

	The backstop exists because a patch is not guaranteed to run at all. ``bench install-app``
	calls ``set_all_patches_as_completed()``, which writes a ``Patch Log`` row for **every**
	line in ``patches.txt`` without executing any of them — confirmed against Frappe v16
	``installer.install_app`` — so on a fresh site the patch is skipped and never runs again.
	And that failure is silent: inserts succeed happily without a unique constraint right up
	until two of them should have collided.

	**Registered on both ``after_migrate`` and ``after_install``, and it needs both.**
	``after_migrate`` does not run during ``bench install-app`` — core runs ``before_install``,
	``after_install`` and ``after_sync`` there and nothing else — so on a fresh site the
	``after_migrate`` registration alone would never fire, which is the very case the paragraph
	above says the backstop exists for. Do not drop either registration.

	This is not a hypothetical. On 2026-08-09 ``add_chat_indexes`` correctly skipped all eleven
	of its composites because the Chat module had never reached model sync, wrote itself to
	``Patch Log`` with ``skipped = 0``, and the deploy reported success — a spent patch and an
	unbacked schema. Its backstop is the only reason that was recoverable.
	"""
	failures = _create_all("ensure_chat_phase2_indexes (after_migrate)")
	if failures:
		_note("could not create: " + "; ".join(failures))


def _create_all(source: str) -> list[str]:
	"""Create every composite in :data:`UNIQUE_CONSTRAINTS` and :data:`INDEXES`.

	Args:
		source: Which entry point is running, named in the skip banner so a deploy log says
			whether it was the one-shot patch or the every-migrate backstop.

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
		# Not a failure in itself — but on a real migrate it means the chat DocTypes did not
		# sync, which is a silently broken deploy. Collected and reported once, loudly, by
		# _report_skips rather than logged per index.
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
		# Do not include the exception's frame locals anywhere: this runs inside migrate and a
		# bare re-raise out of a job publishes them to the Error Log.
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

	Name rather than column set: the framework's helpers key on the name too, so a name match
	is exactly the condition under which they would no-op. It is also what makes re-asserting
	``add_chat_indexes``'s ``status_received_at_index`` free rather than a duplicate index.
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

	* **stdout**, because ``bench migrate``'s stdout *is* the deploy log. A handful of Error
	  Log rows are invisible to a pipeline that only checks the exit code; a banner is not.
	* **one** aggregated Error Log row rather than one per index, so the Error Log carries the
	  diagnosis instead of several copies of a symptom.

	The most likely cause is named explicitly, and for this file it is a different one from
	``add_chat_indexes``: the two Phase 2 DocTypes are **new**, so a missing
	``tabChat Message Revision`` most likely means model sync has not seen them yet — the
	stale-app-module-map failure ``setup/module_map.py`` exists to prevent, which shipped ten
	Chat DocTypes into a green deploy that installed none of them.

	It does not *raise*: an exception in ``post_model_sync`` aborts the migrate after the code
	has already been reset to the new release, leaving the site running new code against a
	half-migrated schema — worse than a loud log.
	"""
	total = len(UNIQUE_CONSTRAINTS) + len(INDEXES)
	headline = (
		f"CHAT PHASE 2 SCHEMA INCOMPLETE: {source} skipped {len(skipped)} of {total} composite "
		f"indexes/constraints because their tables or columns do not exist"
	)
	remedy = (
		"Most likely cause: the two Phase 2 DocTypes (Chat Message Revision, Chat Provisioning "
		"Run) or the Phase 2 columns (Chat Relay Job.lease_expires_at) were not visible to "
		"frappe.model.sync -- a stale app_modules map, the same failure that shipped ten Chat "
		"DocTypes into a green deploy that installed none of them. See "
		"erpnext_enhancements/setup/module_map.py and chat/README.md. Until this reports clean, "
		"unique(message, revision_no) is UNBACKED -- a retried writer will record the same edit "
		"twice -- and the crashed-worker sweep has no index."
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

	Keyword form on purpose. ``frappe.log_error(title, message)`` only swaps its two positional
	arguments when ``"Traceback" in title`` — which never holds here — so the positional call
	put the diagnostic in the 140-char title and the constant in the body. Naming both is the
	only form that cannot be read the wrong way round.
	"""
	try:
		frappe.log_error(title="add_chat_phase2_indexes", message=message)
	except Exception:
		pass

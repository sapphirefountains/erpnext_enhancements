"""Create the Phase 5 chat composites and the one FULLTEXT index retrieval cannot work without.

**A third file rather than rows appended to ``add_chat_phase2_indexes``, for the reason that
file already states about its own predecessor**: a patch is written to ``Patch Log`` on every
site that has run it and ``run_all()`` has no re-run path, so a row added to a spent patch
creates its index on a *fresh* site and never on production. A new module is a new key. The
structure below deliberately mirrors both predecessors so a reader who has understood one has
understood all three.

--------------------------------------------------------------------------------------
The FULLTEXT index is the part that is not like the others
--------------------------------------------------------------------------------------

``frappe.db.add_index`` cannot create a FULLTEXT index — it emits a plain ``ADD INDEX`` — so
that one is raw DDL, and it is the reason this file exists at all rather than being folded in
somewhere convenient.

**The lexical tier is mandatory, not a fallback.** It is what makes an exact invoice number
outrank a chunk that is merely *about* invoices; a semantic index alone answers "roughly this
topic" and cannot answer "this exact string". So a missing FULLTEXT index does not degrade
retrieval gracefully — it silently deletes the half of it that handles identifiers, and
nothing errors.

Three InnoDB properties the implementer has to know, all of them load-bearing here:

* **FULLTEXT sees committed rows only.** A chunk sealed inside the current transaction is not
  findable until that transaction commits. The indexer therefore commits before anything
  expects to search what it wrote.
* **Partitioned tables cannot have FULLTEXT indexes.** ``tabChat Context Chunk`` is not
  partitioned and must not become so without replacing this tier.
* **Rebuilding it on a populated table is an ``ALTER`` that locks.** Doing it now, while the
  table is empty, costs nothing; doing it in a year does not.

``VERIFY:`` **whether ``bench migrate`` drops a hand-added FULLTEXT index.** Add it, migrate
twice, then ``SHOW INDEX FROM `tabChat Context Chunk``. If it does drop it, the lexical half
of retrieval degrades **invisibly** after every deploy — exact-number matching stops working
and nothing anywhere raises. This is precisely why the ``after_migrate`` backstop below is not
optional belt-and-braces: it re-creates the index after every migrate, so even if the answer
to that VERIFY is the bad one, the window is one migrate rather than forever.

--------------------------------------------------------------------------------------
What each composite is for
--------------------------------------------------------------------------------------

``unique(room, first_seq)`` on ``Chat Context Chunk`` is **correctness, not speed**. The
indexer is a background job and background jobs in this deployment are retried — a deploy
FLUSHDBs the queue Redis, Frappe v16 wires no RQ retries, and the sweeper re-drives — so two
workers building the same chunk is scheduled rather than unlikely. Without the constraint the
result is two chunks covering the same messages, both of which the ranker happily returns, and
the model sees the conversation twice.

``(room, last_seq)`` is what keeps the gate's candidate scan bounded. The design caps the
permission-filtered candidate set, and a cap is only meaningful if the range predicate that
produces it is indexed.

Single-column uniques are deliberately absent here, same as in both predecessors, and for the
same trap: ``get_valid_dict`` coerces ``""`` → ``NULL`` **only** when the DocField itself
carries ``unique: 1``, and MariaDB allows unlimited ``NULL``s but exactly one ``""``. A
single-column unique added *here* rather than on the field would let the **second** unwritten
row in the table fail to insert — in production only, invisible to any test that populates the
column. ``Triton Invocation Log.request_id`` is unique **on the field**, which is the correct
half of that pair.

Run twice; the second run writes nothing and touches nothing.
"""

import frappe

#: ``(doctype, columns, constraint_name)`` — composite UNIQUE constraints.
UNIQUE_CONSTRAINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
	# One chunk per (room, starting message). Both columns are reqd, so neither can arrive
	# as the empty string the single-column trap is about. This is the structural form of
	# "the indexer is idempotent" — a retried job collides instead of duplicating the
	# conversation into the candidate set twice.
	("Chat Context Chunk", ("room", "first_seq"), "unique_room_first_seq"),
)

#: ``(doctype, columns, index_name)`` — non-unique composites, each named with the one query
#: it serves.
INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
	# The gate's candidate scan: chunks in the allowed rooms whose span overlaps the window
	# being searched. `room` leads because the permission filter is always a room predicate
	# and it must be the first thing the optimiser can use — filtering after ranking is
	# forbidden, so this index is what makes filtering first affordable.
	("Chat Context Chunk", ("room", "last_seq"), "room_last_seq_index"),
	# "Which sealed, embeddable, non-stale chunks are outstanding" — the embedding backfill's
	# work list, and the rebuild sweep's.
	("Chat Context Chunk", ("sealed", "is_stale"), "sealed_is_stale_index"),
	# The digest pass's selection: stale or poisoned digests, and the staleness alarm's
	# "newest generated_at anywhere".
	("Chat Room Digest", ("is_stale", "generated_at"), "is_stale_generated_at_index"),
	("Chat Thread Digest", ("room", "is_stale"), "room_is_stale_index"),
	# "What did Triton cost this week, and for whom" — the Phase 6 correlation, and the
	# per-person view an operator wants during a rollout.
	("Triton Invocation Log", ("asked_by", "creation"), "asked_by_creation_index"),
	("Triton Invocation Log", ("status", "creation"), "status_creation_index"),
)

#: ``(doctype, column, index_name)`` — FULLTEXT indexes, which need raw DDL.
FULLTEXT_INDEXES: tuple[tuple[str, str, str], ...] = (("Chat Context Chunk", "body", "chunk_body_fulltext"),)


def execute() -> None:
	"""The patch entry point. Creates everything, and **raises** if it cannot.

	Loud on purpose, exactly as in both predecessors: a half-applied index set is a design
	whose guarantees are not actually guaranteed, and the only thing worse than a migrate
	that stops here is one that continues.
	"""
	failures = _create_all("add_chat_phase5_indexes (patch)")
	if failures:
		raise frappe.ValidationError("add_chat_phase5_indexes could not create: " + "; ".join(failures))


def ensure_chat_phase5_indexes() -> None:
	"""The ``after_migrate`` / ``after_install`` entry point. Same work, **never raises**.

	Registered on **both**, and it needs both — ``after_migrate`` does not run during
	``bench install-app``, and ``bench install-app`` marks the whole of ``patches.txt``
	executed without running any of it. Read ``add_chat_phase2_indexes`` for the incident
	that proved the backstop is the only reason a spent patch is recoverable.

	It also carries a job the other two backstops do not: **re-creating the FULLTEXT index
	after every migrate**, in case ``bench migrate`` drops it. That question is an open
	``VERIFY`` (see the module docstring) and this is what makes the bad answer survivable.
	"""
	failures = _create_all("ensure_chat_phase5_indexes (after_migrate)")
	if failures:
		_note("could not create: " + "; ".join(failures))


def _create_all(source: str) -> list[str]:
	failures: list[str] = []
	skipped: list[str] = []

	for doctype, columns, constraint_name in UNIQUE_CONSTRAINTS:
		_apply(doctype, columns, constraint_name, unique=True, failures=failures, skipped=skipped)

	for doctype, columns, index_name in INDEXES:
		_apply(doctype, columns, index_name, unique=False, failures=failures, skipped=skipped)

	for doctype, column, index_name in FULLTEXT_INDEXES:
		_apply_fulltext(doctype, column, index_name, failures=failures, skipped=skipped)

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
	table = f"tab{doctype}"

	if not _table_exists(doctype):
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
	except Exception as exc:
		failures.append(f"{table}({', '.join(columns)}) as {name}: {exc}")


def _apply_fulltext(
	doctype: str,
	column: str,
	name: str,
	*,
	failures: list[str],
	skipped: list[str],
) -> None:
	"""Create one FULLTEXT index by raw DDL.

	``frappe.db.add_index`` emits a plain ``ADD INDEX``, which on a ``longtext`` column is
	either a prefix index or an error — never a FULLTEXT index — so there is no framework
	helper to use here. The identifiers are interpolated rather than bound because MariaDB
	does not accept a placeholder for a table or index name; both come from the module
	constants above and never from input, and the guard on that is that this file has no
	parameter through which a caller could supply one.
	"""
	table = f"tab{doctype}"

	if not _table_exists(doctype):
		skipped.append(f"{table}: table missing, skipped FULLTEXT {name}")
		return
	if not _has_column(doctype, column):
		skipped.append(f"{table}: column {column} missing, skipped FULLTEXT {name}")
		return
	if _index_exists(table, name):
		return

	try:
		frappe.db.sql_ddl(f"alter table `{table}` add fulltext index `{name}` (`{column}`)")
	except Exception as exc:
		failures.append(f"{table}({column}) as FULLTEXT {name}: {exc}")


def _table_exists(doctype: str) -> bool:
	try:
		return bool(frappe.db.table_exists(doctype, cached=False))
	except Exception:
		return False


def _has_column(doctype: str, column: str) -> bool:
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _index_exists(table: str, index_name: str) -> bool:
	"""Is an index of this *name* already on this table?

	Name rather than column set, matching both predecessors: the framework's helpers key on
	the name too, so a name match is exactly the condition under which they would no-op.
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
	"""Say — unmissably — that indexes the design depends on were not created.

	Two channels, because the 2026-08-09 incident proved one is not enough: **stdout**,
	because ``bench migrate``'s stdout *is* the deploy log and a pipeline that checks only
	the exit code cannot see an Error Log row; and **one** aggregated Error Log row rather
	than one per index, so the log carries the diagnosis instead of several copies of a
	symptom.

	For this file the likeliest cause is that the four Phase 5 DocTypes have not reached
	model sync yet, which on a real migrate means a silently broken deploy rather than a
	missing optimisation: without ``unique(room, first_seq)`` the indexer duplicates chunks,
	and without the FULLTEXT index exact-string retrieval stops working with no error.
	"""
	message = "\n".join(f"  - {line}" for line in skipped)
	banner = (
		f"\n{'=' * 78}\nCHAT PHASE 5 INDEXES NOT CREATED — {source}\n{message}\n"
		"The four Phase 5 DocTypes (Chat Context Chunk, Chat Room Digest, Chat Thread Digest,\n"
		"Triton Invocation Log) most likely have not reached model sync. This is not a missing\n"
		"optimisation: unique(room, first_seq) is what stops a retried indexing job duplicating\n"
		"a conversation into the candidate set, and the FULLTEXT index is the whole lexical\n"
		f"tier — without it, exact-string retrieval stops working and nothing raises.\n{'=' * 78}\n"
	)
	# stdout deliberately: bench migrate's stdout IS the deploy log.
	print(banner)
	_note("skipped:\n" + message)


def _note(message: str) -> None:
	try:
		frappe.log_error(f"add_chat_phase5_indexes {message}", "Chat")
	except Exception:
		pass

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The three Phase 2 claims that only a real MariaDB and a real Redis can falsify.

    bench --site erp.sapphirefountains.com execute \\
        erpnext_enhancements.chat.bench_verify.run

**Why this exists rather than a bench test suite.** ``CLAUDE.md`` records that this repo has
no Frappe integration-test job: v16's test-record auto-generation walks the whole ERPNext
DocType dependency graph and aborts on environment gaps, which is why the job was removed
rather than fixed. So "run the bench suites" is not an instruction anybody here can follow.

But the blocker is the **test runner**, not the bench. ``bench execute`` bootstraps no test
records — it imports a module and calls a function — which is exactly how ``smoke_test.py``,
``c1_dwd_check.py`` and ``c2_events_subscription_check.py`` already work in this project.
This module takes the same route to the same place.

WHAT IS ACTUALLY UNPROVEN, AND WHAT IS NOT
-------------------------------------------
Phase 2 shipped roughly a thousand bench-free tests, every one of them against an in-memory
stub. Three claims survived that with no execution behind them at all, and each one fails in
a way that **corrupts data rather than raising**:

1. **The seq row lock.** ``allocate_seq`` does ``UPDATE … seq_high_water = seq_high_water + 1``
   and then reads the value back, and the read-back is only safe because MariaDB holds the row
   lock until commit. If that is wrong, two concurrent senders get the same ``seq``, and the
   symptom is a transcript that renders in the wrong order — not an error.
2. **The duplicate-exception class.** Every dedupe insert catches
   ``(DuplicateEntryError, UniqueValidationError)`` on the strength of *reading* v16's
   ``base_document.py``. If a non-primary-key collision actually raises something else, dedupe
   fails open on every at-least-once Pub/Sub redelivery, and the symptom is a logged error
   nobody reads plus a duplicated message.
3. **The Redis token bucket.** The GCRA arithmetic is unit-tested; the Lua script that deploys
   it has never been sent to a Redis. If ``eval`` is unavailable or the script is wrong, the
   per-space limiter silently permits everything and Google starts 429ing under load.

Three further items on the same list were **already settled** by the post-deploy check on
2026-08-10 and are deliberately not re-tested here: both new DocTypes installed with their
tables, the composite indexes exist, and all eight ``Scheduled Job Type`` rows registered.
Re-asserting those would pad the output and dilute the three that matter.

SAFETY, BECAUSE THIS RUNS ON PRODUCTION
----------------------------------------
* **It refuses to start unless every chat table is empty.** That is not politeness — an empty
  table is what makes "delete everything tagged with my run id" a complete cleanup rather than
  a hopeful one.
* **It refuses to start unless chat is dormant** (``enabled = 0``). It makes no Google call by
  construction — there is no import of the transport anywhere in this module — but a run that
  began while the relay was live could enqueue outbound work for its own scratch rows, and the
  cheapest way to never think about that again is to refuse.
* **Every row it creates carries the run id in a field it owns**, and cleanup runs in a
  ``finally``. It then *verifies* the cleanup and says so, because a cleanup that silently
  half-worked on production is worse than one that never ran.
* It writes no ``Error Log``, sends no notification and touches no setting.

Read the printed block, not the exit code: a ``FAIL`` here is a real finding about the
production stack and is worth more than a green run.
"""

from __future__ import annotations

import time
import traceback
from typing import Any

import frappe

#: Tables that must be empty before this will run. The whole cleanup argument rests on it.
_CHAT_TABLES: tuple[str, ...] = (
	"Chat Room",
	"Chat Message",
	"Chat Room Member",
	"Chat Relay Job",
	"Chat Inbound Event",
	"Chat Message Revision",
	"Chat Attachment",
)

#: How many sequential allocations the gap/duplicate check makes. Small on purpose: this is a
#: correctness probe, not a benchmark, and it runs against the production database.
_SEQ_ITERATIONS: int = 25

#: Seconds the second connection waits for the row lock before giving up. The *timeout* is the
#: evidence — we want it to be reached, so it is short enough not to hold a connection open and
#: long enough that a slow server does not produce a false pass.
_LOCK_WAIT_SECONDS: int = 3


class _Result:
	"""One printed line. ``ok is None`` means the environment could not answer the question."""

	def __init__(self, name: str, ok: bool | None, detail: str, note: str = "") -> None:
		self.name = name
		self.ok = ok
		self.detail = detail
		self.note = note

	def render(self) -> str:
		tag = "PASS   " if self.ok else ("SKIPPED" if self.ok is None else "FAIL   ")
		line = f"[{tag}] {self.name:<58} {self.detail}"
		if self.note:
			line += f"\n            -> {self.note}"
		return line


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------


def _refuse_reasons() -> list[str]:
	"""Everything that must be true before a single row is written. Empty list means go."""
	reasons: list[str] = []

	for doctype in _CHAT_TABLES:
		if not frappe.db.exists("DocType", doctype):
			reasons.append(f"{doctype} does not exist — is v1.262.0 deployed?")
			continue
		count = frappe.db.count(doctype)
		if count:
			reasons.append(
				f"{doctype} holds {count} row(s). This probe only guarantees a complete cleanup "
				"against empty tables, so it will not run beside real data."
			)

	try:
		if frappe.db.get_single_value("Chat Settings", "enabled"):
			reasons.append(
				"Chat Settings.enabled is 1. Refusing: a live relay could pick up this probe's "
				"scratch rows and try to mirror them to Google."
			)
	except Exception as exc:
		reasons.append(f"could not read Chat Settings.enabled ({type(exc).__name__})")

	return reasons


# --------------------------------------------------------------------------------------
# Claim 1 — the seq row lock, and allocation with no gaps or duplicates
# --------------------------------------------------------------------------------------


def _check_seq_allocation(room: str) -> _Result:
	"""``_SEQ_ITERATIONS`` allocations must yield a contiguous run with no repeats.

	This is the cheap half of the claim. It cannot see a concurrency bug — that is
	:func:`_check_seq_row_lock` — but a derivation that is wrong *sequentially* is wrong
	everywhere, and finding that out costs one transaction.
	"""
	from erpnext_enhancements.chat.sync import outbox

	seen: list[int] = []
	for _ in range(_SEQ_ITERATIONS):
		seen.append(int(outbox.allocate_seq(room)))
	frappe.db.commit()

	expected = list(range(seen[0], seen[0] + _SEQ_ITERATIONS))
	ok = seen == expected
	return _Result(
		"seq: allocation is contiguous and never repeats",
		ok,
		f"actual={len(set(seen))} distinct over {_SEQ_ITERATIONS}",
		"" if ok else f"got {seen[:8]}… expected {expected[:8]}… — the counter is not monotonic",
	)


def _check_seq_row_lock(room: str) -> _Result:
	"""Prove MariaDB holds the row lock to commit, by watching a second connection block.

	The whole read-back-after-UPDATE design rests on this one property, and it is the property
	an in-memory stub is structurally incapable of having an opinion about. So: take the lock
	on this connection without committing, then have a *second* connection attempt the same
	UPDATE with a short ``innodb_lock_wait_timeout``. A lock-wait timeout is the pass; the
	second UPDATE returning promptly is the failure, and it would mean two senders can read the
	same ``seq``.

	Returns ``SKIPPED`` rather than ``FAIL`` if a second connection cannot be opened — that is a
	statement about this bench's configuration, not about the claim, and reporting it as a
	failure would be a lie in the direction that gets ignored.
	"""
	second: Any = None
	try:
		from frappe.database import get_db

		second = get_db(
			socket=frappe.conf.get("db_socket"),
			host=frappe.conf.get("db_host"),
			port=frappe.conf.get("db_port"),
			user=frappe.conf.get("db_name"),
			password=frappe.conf.get("db_password"),
			cur_db_name=frappe.conf.get("db_name"),
		)
		second.connect()
	except Exception as exc:
		return _Result(
			"seq: the row lock is held until commit",
			None,
			f"second connection unavailable ({type(exc).__name__})",
			"frappe.database.get_db's signature moves between versions. The claim is untested, "
			"not disproved — re-run on a bench where a second connection can be opened.",
		)

	try:
		# Take the lock here and hold it: no commit until the finally below.
		frappe.db.sql(
			"update `tabChat Room` set seq_high_water = seq_high_water + 1 where name = %s",
			(room,),
		)

		second.sql(f"set session innodb_lock_wait_timeout = {_LOCK_WAIT_SECONDS}")
		started = time.monotonic()
		blocked = False
		try:
			second.sql(
				"update `tabChat Room` set seq_high_water = seq_high_water + 1 where name = %s",
				(room,),
			)
		except Exception:
			blocked = True
		waited = time.monotonic() - started

		if blocked:
			return _Result(
				"seq: the row lock is held until commit",
				True,
				f"second connection blocked then timed out after {waited:.1f}s",
				"so UPDATE-then-read-back cannot hand the same seq to two concurrent senders",
			)
		return _Result(
			"seq: the row lock is held until commit",
			False,
			f"second connection completed in {waited:.2f}s WITHOUT blocking",
			"two concurrent senders can read the same seq. unique(room, seq) still refuses the "
			"second insert, so nothing is silently duplicated — but allocate_seq's retry path "
			"becomes the hot path rather than the exception, and that has never been load-tested.",
		)
	finally:
		try:
			second.rollback()
			second.close()
		except Exception:
			pass
		frappe.db.rollback()


# --------------------------------------------------------------------------------------
# Claim 2 — which exception a non-primary-key unique collision actually raises
# --------------------------------------------------------------------------------------


def _check_duplicate_exception_class(room: str, run_id: str) -> tuple[_Result, _Result]:
	"""The finding is the exception's class name, so print it whatever it turns out to be.

	Phase 2 changed every dedupe insert to catch ``UniqueValidationError`` as well as
	``DuplicateEntryError``, contradicting ADR §G.3.1 and §G.8 Rule 2, purely on the strength of
	reading ``base_document.py``. This is that reading, executed.

	Second result: after the collision is swallowed inside a savepoint, **the transaction must
	still be usable**. That is the half the savepoint exists for, and it is not implied by the
	first — catching the right exception with no savepoint still leaves a poisoned transaction
	on some backends.
	"""
	from frappe.database.database import savepoint

	from erpnext_enhancements.chat.sync import outbox

	resource = f"spaces/PROBE{run_id}/messages/PROBE{run_id}"

	def _message() -> Any:
		doc = frappe.new_doc("Chat Message")
		doc.room = room
		doc.sender_email = f"probe-{run_id}@example.invalid"
		doc.sender_kind = "System"
		doc.message_type = "System"
		doc.sync_origin = "Google Chat"
		doc.text = f"bench_verify probe {run_id}"
		doc.gchat_message_name = resource
		return doc

	outbox.insert_message(_message())
	frappe.db.commit()

	raised: BaseException | None = None
	try:
		with savepoint(catch=Exception):
			frappe.flags.ignore_msgprint = True
			_message().insert(ignore_permissions=True)
	except Exception as exc:  # pragma: no cover — savepoint(catch=Exception) should swallow
		raised = exc
	finally:
		frappe.flags.ignore_msgprint = False

	# The class is whatever the driver produced; ask the exception, do not assume.
	observed = type(raised).__name__ if raised is not None else None
	if observed is None:
		# savepoint swallowed it, which is the normal path. Re-provoke it bare to name the class.
		try:
			with savepoint(catch=()):
				_message().insert(ignore_permissions=True)
		except Exception as exc:
			raised = exc
			observed = type(exc).__name__

	expected = {"UniqueValidationError", "DuplicateEntryError"}
	caught_by_ours = observed in expected
	class_result = _Result(
		"dedupe: which class a non-PK unique collision raises",
		caught_by_ours,
		f"actual={observed}",
		"UniqueValidationError is what Phase 2 predicted from source; DuplicateEntryError would "
		"mean the ADR was right after all. Anything else means every dedupe insert in the chat "
		"package catches the wrong thing and fails OPEN on redelivery."
		if not caught_by_ours
		else "matches what the chat package catches, so structural dedupe holds",
	)

	# The transaction must survive the swallowed collision. A successful count against a named
	# table IS the liveness probe — a poisoned transaction cannot answer it — so there is no
	# need for a bare `select 1`, which would also be a query whose table nobody can read off
	# the source. tests/test_chat_rawsql_guard.py refuses those, correctly.
	usable = False
	detail = ""
	try:
		still_one = frappe.db.count("Chat Message", {"gchat_message_name": resource})
		usable = still_one == 1
		detail = f"rows for that resource name={still_one}"
	except Exception as exc:
		detail = f"transaction unusable: {type(exc).__name__}"
	frappe.db.commit()

	savepoint_result = _Result(
		"dedupe: the transaction survives a swallowed collision",
		usable,
		detail,
		"a savepoint that does not contain the failure leaves the whole enclosing transaction "
		"poisoned, which turns one redelivered event into a lost batch",
	)
	return class_result, savepoint_result


# --------------------------------------------------------------------------------------
# Claim 3 — the Redis Lua token bucket
# --------------------------------------------------------------------------------------


def _check_redis_bucket(run_id: str) -> tuple[_Result, _Result]:
	"""``eval`` has to work, and the second acquire in the same second has to be told to wait."""
	from erpnext_enhancements.chat.sync import ratelimit

	space = f"spaces/PROBE{run_id}"

	try:
		limiter = ratelimit.SpaceRateLimiter()
		first = limiter.acquire(space, cost_ms=ratelimit.SPACE_WRITE_COST_MS, block=False)
	except Exception as exc:
		trace = traceback.format_exc(limit=3).strip().splitlines()[-1]
		return (
			_Result(
				"bucket: the Lua script runs against the real Redis",
				False,
				f"{type(exc).__name__}",
				f"{trace} — the per-space limiter is inert, so nothing throttles outbound writes",
			),
			_Result("bucket: a second write in the same second is made to wait", None, "not reached"),
		)

	eval_result = _Result(
		"bucket: the Lua script runs against the real Redis",
		bool(first.allowed),
		f"first acquire allowed={first.allowed} wait_ms={first.wait_ms}",
		"an empty bucket must admit the first write immediately",
	)

	second = limiter.acquire(space, cost_ms=ratelimit.SPACE_WRITE_COST_MS, block=False)
	waits = (not second.allowed) or second.wait_ms > 0
	wait_result = _Result(
		"bucket: a second write in the same second is made to wait",
		waits,
		f"allowed={second.allowed} wait_ms={second.wait_ms}",
		"Google's limit is one write per second per space, shared across every Chat app acting "
		"in that space. A bucket that admits two immediately is not limiting anything."
		if not waits
		else "",
	)

	try:
		frappe.cache().delete_value(f"chat:rl:{space}")
		frappe.cache().delete(f"chat:rl:{space}")
	except Exception:
		pass

	return eval_result, wait_result


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def run(cleanup: bool = True) -> int:
	"""Run every probe, print a block, clean up, and return the number of failures.

	``cleanup=False`` leaves the scratch rows behind for inspection. It is here for a bad day,
	not for normal use, and the report says loudly when it was used.
	"""
	print("=" * 96)
	print("chat bench_verify — the three Phase 2 claims only a real MariaDB and Redis can settle")
	print("=" * 96)

	refusals = _refuse_reasons()
	if refusals:
		print("\nREFUSING TO RUN:\n")
		for reason in refusals:
			print(f"  - {reason}")
		print(
			"\nNothing was created. Every guard above exists so that 'delete what I made' is a "
			"complete statement rather than a hopeful one."
		)
		return 1

	run_id = frappe.generate_hash(length=8).lower()
	results: list[_Result] = []
	room: str | None = None

	print(f"\nsite    : {frappe.local.site}")
	print(f"run id  : {run_id}   (every row this creates carries it)")
	print("chat    : dormant, all tables empty — verified above\n")

	try:
		doc = frappe.new_doc("Chat Room")
		doc.room_type = "Group"
		doc.title = f"bench_verify probe {run_id}"
		doc.provisioning_mode = "Not Mirrored"
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		room = doc.name

		results.append(_check_seq_allocation(room))
		results.append(_check_seq_row_lock(room))
		results.extend(_check_duplicate_exception_class(room, run_id))
		results.extend(_check_redis_bucket(run_id))
	except Exception:
		print(traceback.format_exc())
		results.append(_Result("probe aborted before completing", False, "see traceback above"))
	finally:
		removed = _cleanup(room, run_id) if cleanup else "SKIPPED (cleanup=False)"

	for result in results:
		print(result.render())

	failures = sum(1 for r in results if r.ok is False)
	skipped = sum(1 for r in results if r.ok is None)
	print("-" * 96)
	print(f"{len(results)} checks · {len(results) - failures - skipped} pass · {failures} fail · {skipped} skipped")
	print(f"cleanup : {removed}")
	if failures:
		print(
			"\nA FAIL here is a real finding about the production stack, not a flaky test. Each "
			"one names what it means in its note; none of them is cosmetic."
		)
	return failures


def _cleanup(room: str | None, run_id: str) -> str:
	"""Delete everything this run created, then prove it. Never raises.

	**Every DocType below is a literal**, and the deletes are ``frappe.db.delete`` rather than a
	``get_all``-then-delete over a loop variable. Two reasons, and the second is the real one:

	* ``tests/test_chat_rawsql_guard.py`` resolves the target of every chat query statically and
	  refuses one it cannot name. A loop variable defeats it, and a checker that cannot see the
	  table cannot protect it — so writing the loop would have bought brevity by blinding the
	  guard. It caught this file on its first run, which is the guard working.
	* Deleting by filter reads nothing. ``Chat Message`` and ``Chat Message Revision`` hold
	  employee chat content and superseded bodies; the fewer paths in this package that *read*
	  them without a membership filter, the smaller the surface that has to be argued about.
	"""
	deleted: dict[str, int] = {}
	try:
		if room:
			deleted["Chat Message Revision"] = frappe.db.count("Chat Message Revision", {"room": room})
			frappe.db.delete("Chat Message Revision", {"room": room})

			deleted["Chat Relay Job"] = frappe.db.count("Chat Relay Job", {"room": room})
			frappe.db.delete("Chat Relay Job", {"room": room})

			deleted["Chat Message"] = frappe.db.count("Chat Message", {"room": room})
			frappe.db.delete("Chat Message", {"room": room})

			deleted["Chat Room Member"] = frappe.db.count("Chat Room Member", {"room": room})
			frappe.db.delete("Chat Room Member", {"room": room})

			frappe.db.delete("Chat Room", {"name": room})
			deleted["Chat Room"] = 1
		frappe.db.commit()

		leftover = {d: frappe.db.count(d) for d in _CHAT_TABLES if frappe.db.exists("DocType", d)}
		stragglers = {d: n for d, n in leftover.items() if n}
		if stragglers:
			return f"INCOMPLETE — removed {deleted}, but these tables are not empty: {stragglers}"
		return f"complete — removed {deleted}; every chat table is empty again"
	except Exception as exc:
		return f"FAILED ({type(exc).__name__}) — removed {deleted}; run id {run_id} may need manual cleanup"


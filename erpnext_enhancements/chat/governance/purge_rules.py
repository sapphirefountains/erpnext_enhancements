# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a purge would destroy, what survives it, and why it cannot run yet.

**No Frappe, no imports.** Phase 6 §4.F. :mod:`retention` computes against this; nothing in
either module deletes anything, and :mod:`test_chat_purge_surface` asserts that mechanically.

--------------------------------------------------------------------------------------
The blocker, stated first because it is the finding rather than a caveat
--------------------------------------------------------------------------------------

**Phase 5's derived layer has a staleness story and no retirement story.** Chunks and digests
are *rebuilt from live messages*; nothing anywhere removes derived content on the grounds
that its source was destroyed. Verified on disk, not inferred:

* ``indexing/digest.py::_messages_for_digest`` filters ``is_deleted = 0``, and
  ``_rebuild_room_digest`` returns before writing when that comes back empty. So a room the
  purge empties keeps its model-written ``summary_text`` **permanently** — ``is_stale`` stays
  1, ``rebuild_failures`` is never incremented so it never poisons out, and the dirty sweep
  re-selects it every five minutes forever, doing nothing. That is not an edge case; it is
  the *designed end state* for exactly the rooms retention exists to clear.
* ``indexing/digest.py::_dirty_rooms`` and ``indexer.py::_rooms_needing_chunks`` both open
  ``where is_archived = 0``. An archived room's derived artefacts are never revisited under
  any circumstances.
* ``indexer.py::_rooms_needing_chunks`` computes its watermark as
  ``coalesce(max(c.last_seq), 0)`` over sealed chunks with **no staleness filter**, and
  ``_messages_after`` then re-reads every live message above it — whose own docstring says
  *"a chunk is a verbatim copy"*. So deleting a room's chunks **retreats the watermark**, and
  the ten-minute chunk sweep re-chunks the messages the purge has not deleted yet, verbatim,
  with a fresh embedding. **A purge that tidies up its chunks manufactures new verbatim
  copies of the text it is destroying**, once every ten minutes, for as many nights as the
  batch cap takes to finish the room.

Both directions are wrong and neither is fixable inside a retention job:

===============================  ============================================================
if the purge leaves them         a model-written summary of the destroyed conversation is
                                 served by the retrieval gate forever, and the chunk bodies
                                 are the transcript pre-assembled into prose
if the purge deletes them        it destroys retrieval coverage of *retained* messages at
                                 every boundary chunk, and re-chunking recreates the bodies
===============================  ============================================================

So :data:`BLOCKED` is a third answer alongside "purge it" and "it survives", and the modules
carrying it are the prerequisite. The fix is a **retirement path in** ``chat/indexing/`` — a
way to say *"this span is gone, drop its coverage and do not re-read it"* — which is a change
to the indexer with its own tests, not a paragraph in a purge.

--------------------------------------------------------------------------------------
What ships, given that
--------------------------------------------------------------------------------------

The eligibility rule, the survives-a-purge table, and the writer for ``retention_run`` — the
last event type ``Chat Audit Log`` declares with nothing able to produce it. **Not the
deletion.** A destroying job whose prerequisite is unwritten is worse than no job: it reads
as the safety having been thought about.

Decision D-6 is *keep forever* and is already the shipped state —
``message_retention_days`` defaults to ``0`` and ``0`` means never — so nothing here changes
what any site does today.
"""

from __future__ import annotations

from typing import Final

# --- the survives-a-purge table -------------------------------------------------

#: Rows the purge would destroy. The bodies, and the per-message sidecars that are
#: meaningless without them.
PURGE: Final[str] = "purge"

#: Rows the purge must leave alone.
SURVIVES: Final[str] = "survives"

#: Rows the purge can neither keep nor destroy correctly. A DocType here is a reason the
#: purge cannot be enabled, and naming it is the point. **Empty as of v1.299.0** — the three
#: entries that were here are now :data:`RETIRED`.
BLOCKED: Final[str] = "blocked"

#: Rows a purge does not touch itself, because something else removes them **on the purge's
#: behalf and on its own terms**. Today that is one thing: the derived layer, removed by
#: `chat/indexing/retire.py` once the retirement mark advances over the span.
#:
#: A separate answer from SURVIVES, and the distinction is load-bearing. SURVIVES means *this
#: outlives the purge* — the audit trail, the room, the queue tables. RETIRED means *this must
#: go too, and here is the other mechanism that takes it*. Collapsing them would lose the only
#: record that a second mechanism has to run for the purge to be complete.
RETIRED: Final[str] = "retired"

DISPOSITIONS: Final[tuple[str, ...]] = (PURGE, SURVIVES, BLOCKED, RETIRED)

#: Every chat DocType, classified, with the reason. **Set-equality against the filesystem**
#: is asserted by the test suite, so a chat DocType added next year fails the build until
#: somebody decides what a purge does to it — the same mechanism the MCP denylist uses, and
#: for the same reason: the entry that gets forgotten is the one nobody was asked about.
DISPOSITION: Final[dict[str, tuple[str, str]]] = {
	# ---- the target -------------------------------------------------------------
	"Chat Message": (
		PURGE,
		"The bodies. This is what retention is about, and the only doctype whose deletion is "
		"the point rather than a consequence.",
	),
	"Chat Message Revision": (
		PURGE,
		"Superseded and deleted bodies — the one table where a body survives the user's own "
		"decision to delete it. Retaining these past a purge of the message they belong to "
		"would make the purge cosmetic, since `tombstone.expand` reads exactly this table.",
	),
	"Chat Attachment": (
		PURGE,
		"The sidecar that binds a message to its File. Meaningless without the message, and "
		"leaving it turns the bytes on disk into an orphan nothing can reach: "
		"`sync/attachments.download` resolves by attachment name and there is no other door.",
	),
	"Chat Mention": (
		PURGE,
		"A child table of Chat Message. Frappe deletes child rows with the parent, so this "
		"is listed to record that it is intended rather than incidental.",
	),
	# ---- blocked ----------------------------------------------------------------
	"Chat Context Chunk": (
		RETIRED,
		"`body` is the messages verbatim, pre-assembled into prose. Deleting a chunk retreats "
		"`indexer._rooms_needing_chunks`'s watermark — it is `max(last_seq)` over sealed "
		"chunks with no staleness filter — so the ten-minute sweep re-reads every live "
		"message above it and re-chunks them verbatim. The purge would manufacture new copies "
		"of the text it is destroying. Leaving them is equally wrong: the chunk IS the "
		"transcript.",
	),
	"Chat Room Digest": (
		RETIRED,
		"A model-written summary of the conversation. `_messages_for_digest` filters "
		"`is_deleted = 0` and `_rebuild_room_digest` returns before writing when that is "
		"empty, so a purged room keeps its summary forever while the dirty sweep re-selects "
		"it every five minutes doing nothing. `_dirty_rooms` also skips archived rooms "
		"entirely, so there the summary is permanent unconditionally.",
	),
	"Chat Thread Digest": (
		RETIRED,
		"Same failure, one level down, plus its own: `_dirty_threads` selects with `having "
		"count(*) >= minimum`, so a thread whose replies are purged below the threshold is "
		"never selected again and keeps a summary of the purged replies indefinitely.",
	),
	# ---- the audit trail: D-7 ----------------------------------------------------
	"Chat Audit Log": (
		SURVIVES,
		"Decision D-7. Bodies go; the record that somebody read them stays. It holds no "
		"message text by design, and its reference fields are plain Data precisely so a row "
		"outlives what it points at.",
	),
	"Chat Retrieval Audit": (
		SURVIVES,
		"D-7, and hash-chained. Deleting a referenced message cannot break the chain — no "
		"verifier joins the message table — but touching this one does.",
	),
	"Chat Retrieval Audit Room": (
		SURVIVES,
		"D-7, and **the trap**: its controller is `pass`, so it has no guard of its own, yet "
		"its rows are inside the parent's hash. A room-scoped cleanup over this child meets "
		"nothing and silently breaks the retrieval chain at the earliest affected row — after "
		"which the nightly verifier alerts every night forever with nothing able to re-anchor "
		"it. Anonymising instead of deleting breaks it identically.",
	),
	# ---- everything else ---------------------------------------------------------
	"Chat Room": (
		SURVIVES,
		"A room outlives its messages. Purging the room because it is empty would also "
		"destroy its Google space binding and its membership, which is a provisioning "
		"decision rather than a retention one.",
	),
	"Chat Room Member": (
		SURVIVES,
		"Membership is not conversation. Who was in a room outlives what was said in it, and "
		"removing the roster would make an audit row naming a participant unresolvable.",
	),
	"Chat Allowed User": (
		SURVIVES,
		"A child table of Chat Settings holding the pilot whitelist. Configuration, and "
		"deleting it would silently widen or close access rather than retain anything.",
	),
	"Chat Settings": (
		SURVIVES,
		"Configuration, and the Single that holds the retention dials themselves. A purge "
		"that could reach its own switches is one nobody can reason about.",
	),
	"Chat Relay Job": (
		SURVIVES,
		"Has its own retention already, driven by `relay_job_retention_days` through "
		"`chat/retention.py` and Frappe's log clearing. A second deleter for one table is how "
		"two retention rules end up disagreeing about the same rows.",
	),
	"Chat Inbound Event": (
		SURVIVES,
		"Same: `inbound_event_retention_days`, through the same module.",
	),
	"Chat Event Subscription": (
		SURVIVES,
		"Operational health, one row per coworker: the Google subscription uid, its state and "
		"expiry. Deleting one stops inbound sync for every space only that person covers.",
	),
	"Chat Provisioning Run": (
		SURVIVES,
		"A bulk provisioning checkpoint so an interrupted run resumes rather than restarts. "
		"It references org units and never messages, so retention has no claim on it.",
	),
	"Chat Push Subscription": (
		SURVIVES,
		"A Web Push device registry: endpoint, keys, user agent, delivery health. No room and "
		"no message reaches it, and it has its own staleness sweep already.",
	),
	"Chat Export Request": (
		SURVIVES,
		"The record that a bundle left the building. Its controller already refuses deletion "
		"of a completed request, and for the same reason: deleting it removes the evidence "
		"rather than the bundle.",
	),
	"Chat Ops Alert": (
		SURVIVES,
		"The alert board. No message text by schema, and purging it would delete the record of "
		"incidents — including, eventually, the record of a retention run going wrong.",
	),
	"Chat Drift Report": (
		SURVIVES,
		"The divergence census. A finding that references a purged object clears itself on "
		"the next scan, which is the designed behaviour and needs no help from here.",
	),
	"Triton Invocation Log": (
		SURVIVES,
		"What the assistant was asked and answered. It links three Chat Message fields, so a "
		"purge dangles them — but the invocation record is not the transcript, and destroying "
		"the history of what Triton did because a message aged out would be a second, "
		"unasked-for retention policy.",
	),
}

# --- eligibility -----------------------------------------------------------------

#: Reasons a message is held back. Returned as a set so a caller can report *why* rather than
#: only *how many* — the number alone is exactly the report that makes a purge look ready.
HOLD_NOT_OLD_ENOUGH: Final[str] = "inside the retention window"
HOLD_RELAY_IN_FLIGHT: Final[str] = "outstanding relay work"
HOLD_TOMBSTONE_KEPT: Final[str] = "keep_tombstones_forever is on"
HOLD_THREAD_ROOT: Final[str] = "a live reply still names this message as its thread root"
HOLD_ROOM_LAST_MESSAGE: Final[str] = "the room points at this as its last message"
HOLD_DERIVED_BLOCKED: Final[str] = "derived artefacts covering it cannot be retired"
HOLD_NOT_RETIRED: Final[str] = "the retirement mark for this room does not cover it yet"

#: Fallback when ``Chat Settings.message_retention_days`` reads 0. ``0`` means **never**, the
#: convention the rest of that form already uses and which ``chat/retention.py`` established;
#: it is not a "use the default" sentinel and must never become one.
NEVER: Final[int] = 0


def holds(facts: dict) -> set[str]:
	"""Every reason this message may not be purged. Empty means eligible.

	Every reason, not the first one, and that is the useful part: a message held by three
	different rules is a different fact from one held by a boundary condition, and a purge
	report that stops at the first hold cannot tell an operator which rule is actually
	binding.

	``facts`` is a plain dict so the caller owns the queries:

	``age_days``            how old, in days, computed by the caller from one clock
	``retention_days``      the setting; ``0`` is never and holds everything
	``open_relay_jobs``     count of Chat Relay Job rows in OPEN_STATUSES for this message
	``is_deleted``          already tombstoned
	``keep_tombstones``     ``Chat Settings.keep_tombstones_forever``
	``live_replies``        count of undeleted messages naming this one as thread_root
	``is_room_last_message`` ``Chat Room.last_message`` points here
	``derived_blocked``     a BLOCKED artefact covers this message's span
	``seq``                 this message's sequence number
	``retired_below_seq``   ``Chat Room.retired_below_seq`` — derived coverage at or below
	                        this seq has been retired, so purging is safe there
	"""
	out: set[str] = set()

	retention_days = int(facts.get("retention_days") or NEVER)
	age_days = facts.get("age_days")
	if retention_days <= NEVER or age_days is None or float(age_days) < retention_days:
		out.add(HOLD_NOT_OLD_ENOUGH)

	if int(facts.get("open_relay_jobs") or 0) > 0:
		# A Message Delete job whose message is gone Skips silently, leaving the Chat copy
		# live with nothing in ERPNext to reconcile against — irreparable, and the exact
		# failure `outbox.refuse_hard_delete` exists to prevent.
		out.add(HOLD_RELAY_IN_FLIGHT)

	if facts.get("is_deleted") and facts.get("keep_tombstones"):
		out.add(HOLD_TOMBSTONE_KEPT)

	if int(facts.get("live_replies") or 0) > 0:
		# `thread_root` is a Link. Purging a root out from under live replies orphans the
		# thread, and the replies are inside the retention window by definition.
		out.add(HOLD_THREAD_ROOT)

	if facts.get("is_room_last_message"):
		out.add(HOLD_ROOM_LAST_MESSAGE)

	if facts.get("derived_blocked"):
		out.add(HOLD_DERIVED_BLOCKED)

	# The retirement floor. A message is only purgeable once the derived coverage over its
	# span has actually been retired — which is a per-message fact about `seq`, not a
	# site-wide one. It is checked LAST because it is the most specific: a message held by
	# this and nothing else is one waiting on the retirement mark to advance, which is a
	# different conversation from one held because it is still in the retention window.
	retired_below = int(facts.get("retired_below_seq") or 0)
	if int(facts.get("seq") or 0) > retired_below:
		out.add(HOLD_NOT_RETIRED)

	return out


def is_eligible(facts: dict) -> bool:
	return not holds(facts)


def retired_doctypes() -> tuple[str, ...]:
	"""The DocTypes the retirement path removes rather than the purge."""
	return tuple(sorted(name for name, (d, _) in DISPOSITION.items() if d == RETIRED))


def blocked_doctypes() -> tuple[str, ...]:
	"""The DocTypes that make a purge unsafe today, in a stable order."""
	return tuple(sorted(name for name, (d, _) in DISPOSITION.items() if d == BLOCKED))


def purgeable_doctypes() -> tuple[str, ...]:
	return tuple(sorted(name for name, (d, _) in DISPOSITION.items() if d == PURGE))


def surviving_doctypes() -> tuple[str, ...]:
	return tuple(sorted(name for name, (d, _) in DISPOSITION.items() if d == SURVIVES))


def unclassified(doctypes_on_disk: set[str]) -> tuple[str, ...]:
	"""Chat DocTypes nobody has decided about. Non-empty is a build failure.

	The property that keeps this table honest in a year, and the reason it is set equality
	rather than containment: a DocType added and never classified is one a purge would treat
	by accident, and the accident is silent in both directions.
	"""
	return tuple(sorted(set(doctypes_on_disk) - set(DISPOSITION)))


def stale_entries(doctypes_on_disk: set[str]) -> tuple[str, ...]:
	"""Entries naming a DocType that no longer exists. Noise in a table read under pressure."""
	return tuple(sorted(set(DISPOSITION) - set(doctypes_on_disk)))


def can_enable() -> tuple[bool, str]:
	"""May the destructive path be built yet? ``(False, why not)`` until the blocker clears.

	A function rather than a comment because :mod:`retention` calls it and refuses on it, and
	because the day somebody writes the retirement path in ``chat/indexing/`` this is the one
	place that has to change.
	"""
	blocked = blocked_doctypes()
	if not blocked:
		return True, ""
	return False, (
		"a purge cannot retire the derived artefacts covering what it destroys: "
		+ ", ".join(blocked)
		+ ". Chunks and digests are rebuilt FROM live messages and nothing removes them "
		"because their source is gone, so leaving them serves a summary of the destroyed "
		"conversation forever while deleting them retreats the indexer watermark and "
		"re-chunks the not-yet-purged messages verbatim. The prerequisite is a retirement "
		"path in chat/indexing/, not a bigger retention job."
	)

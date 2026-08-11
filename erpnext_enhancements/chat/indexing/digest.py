# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Rolling summaries. **A scheduler batch over a dirty predicate — never a per-message enqueue.**

The natural design is to enqueue a summarize job per message with
``job_id=f"digest-{room}"`` and ``deduplicate=True`` so repeated messages collapse. It is
silently broken, and the source says so:

.. code-block:: python

    if deduplicate:
        job = get_job(job_id)
        if job and job.get_status(refresh=False) in (JobStatus.QUEUED, JobStatus.STARTED):
            frappe.logger().error(f"Not queueing job {job.id} because it is in queue already")
            return

``deduplicate`` drops the new enqueue when an existing job is ``QUEUED`` **or ``STARTED``**. A
digest job that is *currently running* therefore swallows every message arriving while it runs
— and those are exactly the messages that made the digest stale. In a busy room the job is
always running, the drop is always happening, and the digest never advances. It is a stall, it
is silent (one ``ERROR`` line and a ``return``), and for weeks it looks like "summaries are a
bit behind".

So: a cron every five minutes, selecting rooms by a predicate, summarising in batch.

--------------------------------------------------------------------------------------
The dirty predicate is derived, not counted — a deliberate divergence from ADR §I.6
--------------------------------------------------------------------------------------

§I.6 specifies ``unsummarized_count >= 25 OR digest_dirty_since < now() - 15 minutes``, which
implies two counter columns maintained on the message write path. This computes the same thing
from facts the room already stores:

* ``Chat Room.seq_high_water - digest.watermark_seq`` **is** the unsummarised count;
* ``Chat Room.last_message_at`` against ``digest.generated_at`` **is** the dirty age.

A counter is a second source of truth for a number already known, it costs a write on the
hottest path in the feature, and the copy that drifts is the one nobody notices. Derived is
self-correcting: delete a digest and the next pass rebuilds it.

--------------------------------------------------------------------------------------
The clock trap that would corrupt every predicate here
--------------------------------------------------------------------------------------

The production database runs **UTC** while Frappe writes ``creation``/``modified`` in
**site-local** time. A naive ``TIMESTAMPDIFF(MINUTE, MAX(creation), NOW())`` reports a row
written one minute ago as **361 minutes** old. Every freshness comparison below therefore uses
``frappe.utils.now_datetime()`` on both sides, passed in as a bound parameter — never SQL
``NOW()``. Get this wrong and every room looks permanently dirty, the pass rebuilds everything
every five minutes, and the only symptom is a model bill.

--------------------------------------------------------------------------------------
Invalidation adds information; it cannot unsay it
--------------------------------------------------------------------------------------

An edit or delete inside a digest's covered span sets ``is_stale``, and the rebuild is a
**full regeneration from source messages**, never an incremental append. Retrieval **skips** a
stale digest outright rather than serving it with a caveat — because the thing that may be out
of date is a sentence somebody deleted, ERPNext holds the only copy of it, and Google's
tombstone is content-free. See :mod:`chat.indexing.invalidate` for the write side.

``poisoned`` is a separate flag from ``is_stale``: "nobody has rebuilt this yet" and "this
cannot be rebuilt" need different answers from an operator, and collapsing them makes a
permanently failing room look identical to a busy one.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from erpnext_enhancements.chat.doctype.chat_context_chunk.chat_context_chunk import estimate_tokens

ROOM_DIGEST_DOCTYPE = "Chat Room Digest"
THREAD_DIGEST_DOCTYPE = "Chat Thread Digest"
MESSAGE_DOCTYPE = "Chat Message"
ROOM_DOCTYPE = "Chat Room"

#: Messages a rebuild reads. A bound, because a full rebuild of a two-year-old room must not
#: be one statement that loads it all.
MESSAGES_PER_REBUILD: int = 500

#: The digest is built from the tail of the covered span. Older material is already
#: represented in the previous generation's text, which the prompt carries forward.
ROLL_FORWARD_MESSAGES: int = 120


def sweep_digests() -> dict[str, int]:
	"""The five-minute pass. Selects dirty rooms, summarises them, bounded per run."""
	if not _enabled():
		return {"rooms": 0, "rebuilt": 0, "failed": 0}

	batch = cint(_setting("digest_batch_rooms")) or 20
	rooms = _dirty_rooms(limit=batch)
	rebuilt = 0
	failed = 0
	not_rebuilt = 0
	for room in rooms:
		try:
			if _rebuild_room_digest(room["name"]):
				rebuilt += 1
			else:
				# A room that was selected and not rebuilt did not succeed. It reported its
				# own reason and returned, so no exception reached here — and counting only
				# exceptions made this return `failed: 0` while `rebuild_failures` climbed and
				# the room was poisoned. An operator reading that number concluded nothing was
				# wrong, which is worse than no number.
				not_rebuilt += 1
		except Exception as exc:
			failed += 1
			frappe.db.rollback()
			_record_failure(room["name"], exc.__class__.__name__)

	threads = _sweep_thread_digests(limit=batch)
	frappe.db.commit()
	return {
		"rooms": len(rooms),
		"rebuilt": rebuilt,
		"failed": failed,
		"not_rebuilt": not_rebuilt,
		"threads": threads,
	}


def check_digest_staleness() -> dict[str, Any]:
	"""Alert when the newest digest anywhere is older than the configured window.

	**The failure being watched for is not an error — it is silence.** Every other failure in
	this package announces itself; a digest pass that has quietly stopped running produces no
	log line, no exception and no user complaint, because stale summaries still answer.
	"""
	if not _enabled():
		return {"ok": True, "reason": "chat is disabled"}

	window = cint(_setting("digest_staleness_alert_minutes")) or 60
	newest = _newest_generated_at()
	if newest is None:
		# No digest has ever been generated. Not an alarm on a site where chat is dormant or
		# newly enabled — the pass has simply not had anything to do yet.
		return {"ok": True, "reason": "no digest has been generated yet"}

	age = (now_datetime() - get_datetime(newest)).total_seconds() / 60.0
	if age <= window:
		return {"ok": True, "age_minutes": round(age, 1)}

	_note(
		f"No chat digest has been generated for {round(age)} minutes (threshold {window}). "
		"The summariser has stopped, and stale summaries keep answering, so nothing else "
		"will report this."
	)
	return {"ok": False, "age_minutes": round(age, 1), "threshold_minutes": window}


# --------------------------------------------------------------------------- one room


def _rebuild_room_digest(room: str) -> bool:
	"""Regenerate one room's digest. Returns whether it was written.

	A **full rebuild from source** whenever the digest is stale, has rolled forward too many
	times, or covers too long a span. Otherwise the previous summary is carried into the
	prompt and extended — which is cheaper, and is safe only because the stale case above
	takes the other branch.
	"""
	existing = _digest_row(room)
	full = _needs_full_rebuild(existing)

	if existing and cint(existing.get("poisoned")):
		return False

	since = 0 if full else cint((existing or {}).get("watermark_seq"))
	messages = _messages_for_digest(
		room, since, limit=MESSAGES_PER_REBUILD if full else ROLL_FORWARD_MESSAGES
	)
	if not messages:
		return False

	watermark = _watermark(room)
	summary = _summarise(
		room=room,
		messages=messages,
		previous=None if full else (existing or {}).get("summary_text"),
	)
	if not summary:
		# The summariser reports its own reason and returns None rather than raising, so this
		# is NOT reached by the caller's except - and without counting it here the ceiling can
		# never be reached, which is the other half of the forever-retry. A model that is down
		# is precisely the failure that repeats.
		_record_failure(room, "the summariser returned nothing")
		return False

	covered_from = cint((existing or {}).get("covered_from")) or cint(messages[0].get("seq"))
	if full:
		covered_from = cint(messages[0].get("seq"))

	values = {
		"room": room,
		"summary_text": summary["text"],
		"token_count": estimate_tokens(summary["text"]),
		"model_used": (summary.get("model") or "")[:140] or None,
		"generated_at": now_datetime(),
		"watermark_seq": watermark[0],
		"watermark_count": watermark[1],
		"watermark_modified": watermark[2],
		"covered_from": covered_from,
		"covered_to": cint(messages[-1].get("seq")),
		"covered_from_at": messages[0].get("creation"),
		"covered_to_at": messages[-1].get("creation"),
		"is_stale": 0,
		"stale_since": None,
		"rebuild_failures": 0,
		"last_error": None,
	}

	if existing:
		values["digest_version"] = cint(existing.get("digest_version")) + 1
		values["generation_count"] = 0 if full else cint(existing.get("generation_count")) + 1
		frappe.db.set_value(ROOM_DIGEST_DOCTYPE, existing["name"], values, update_modified=False)
	else:
		doc = frappe.new_doc(ROOM_DIGEST_DOCTYPE)
		doc.update(values)
		doc.digest_version = 1
		doc.generation_count = 0
		doc.insert(ignore_permissions=True)
	return True


def _sweep_thread_digests(*, limit: int) -> int:
	"""Summarise threads long enough to have earned one. Returns how many were written.

	A thread shorter than the configured length gets **no** digest, because the thread tier
	carries it verbatim and a summary would be strictly worse than the text it replaces. The
	consumer is the degradation ladder: when the thread will not fit, the root and the last
	few replies stay verbatim and the middle is replaced by this.

	Written in the same pass as the room digests rather than its own job, because the two
	share the model call, the poison-pill guard and the staleness contract — and a second
	scheduler entry for a strictly smaller version of the same work is a second thing to
	notice has stopped.
	"""
	minimum = cint(_setting("thread_digest_min_messages")) or 40
	written = 0
	for thread in _dirty_threads(minimum=minimum, limit=limit):
		try:
			if _rebuild_thread_digest(thread["thread_root"], thread["room"]):
				written += 1
		except Exception as exc:
			_record_thread_failure(thread["thread_root"], exc.__class__.__name__)
	return written


def _rebuild_thread_digest(thread_root: str, room: str) -> bool:
	"""Always a **full** rebuild from the thread's own messages.

	No roll-forward here, unlike the room digest. A thread is bounded and re-reading it is
	cheap, so the incremental path would buy little and would carry the one risk that matters:
	an incremental summary cannot unsay a message that was edited inside it.
	"""
	messages = _thread_messages(thread_root, limit=MESSAGES_PER_REBUILD)
	if not messages:
		return False

	summary = _summarise(room=room, messages=messages, previous=None)
	if not summary:
		return False

	existing = frappe.db.get_value(THREAD_DIGEST_DOCTYPE, {"thread_root": thread_root}, "name")
	values = {
		"thread_root": thread_root,
		"room": room,
		"summary_text": summary["text"],
		"token_count": estimate_tokens(summary["text"]),
		"model_used": (summary.get("model") or "")[:140] or None,
		"generated_at": now_datetime(),
		"message_count": len(messages),
		"watermark_seq": cint(messages[-1].get("seq")),
		"watermark_count": len(messages),
		"watermark_modified": max(str(row.get("modified") or "") for row in messages),
		"covered_from": cint(messages[0].get("seq")),
		"covered_to": cint(messages[-1].get("seq")),
		"covered_from_at": messages[0].get("creation"),
		"covered_to_at": messages[-1].get("creation"),
		"is_stale": 0,
		"stale_since": None,
		"rebuild_failures": 0,
		"last_error": None,
	}
	if existing:
		current = cint(frappe.db.get_value(THREAD_DIGEST_DOCTYPE, existing, "digest_version"))
		values["digest_version"] = current + 1
		values["generation_count"] = 0
		frappe.db.set_value(THREAD_DIGEST_DOCTYPE, existing, values, update_modified=False)
	else:
		doc = frappe.new_doc(THREAD_DIGEST_DOCTYPE)
		doc.update(values)
		doc.digest_version = 1
		doc.insert(ignore_permissions=True)
	return True


def _needs_full_rebuild(existing: dict[str, Any] | None) -> bool:
	"""Stale, too many roll-forwards, or too long a span.

	The staleness branch is the one that matters: **a rolling summary can add information but
	it cannot unsay it**, so a digest whose span contains an edited or deleted message must be
	regenerated from the messages rather than extended. The other two are drift controls — a
	summary of a summary of a summary diverges from the transcript in ways nothing measures.
	"""
	if not existing:
		return True
	if cint(existing.get("is_stale")):
		return True
	if cint(existing.get("generation_count")) >= (cint(_setting("digest_full_rebuild_generations")) or 20):
		return True

	days = cint(_setting("digest_full_rebuild_days")) or 90
	covered_from_at = existing.get("covered_from_at")
	if covered_from_at:
		try:
			if get_datetime(covered_from_at) < add_to_date(now_datetime(), days=-days):
				return True
		except Exception:
			return True
	return False


def _summarise(*, room: str, messages: list[dict[str, Any]], previous: str | None) -> dict[str, Any] | None:
	"""Ask the model for a summary, as the **app**, not as any coworker.

	This is the one read in the feature that legitimately crosses every room, and it is
	exactly why the retrieval gate and the MCP denylist exist: the summariser is a scheduler
	job with no session user, so nothing here is scoped by membership — and its *output* is
	therefore governed at the point of consumption rather than the point of production.

	Scheduler jobs run as ``Administrator``. The identity is set explicitly rather than
	inherited, so that a future change that starts calling a permission-aware helper from
	here fails loudly instead of quietly running as a superuser.

	Returns ``None`` rather than raising when the model is unavailable: an unbuilt digest is a
	less complete answer, while a raising sweep would take the whole batch down with it.
	"""
	target = cint(_setting("digest_room_target_tokens")) or 400
	transcript = "\n".join(
		f"{row.get('sender') or row.get('sender_email') or 'someone'}: {row.get('text_plain') or row.get('text') or ''}"
		for row in messages
	)
	prompt = _prompt(previous=previous, transcript=transcript, target=target)

	summariser = _summariser_user()
	if not summariser:
		# No bot identity configured. Refusing beats falling back to Administrator, and
		# beats a silent skip: the reason names the field somebody has to fill in.
		_note(f"no digest for room {room}: Chat Settings has no Chat App Service Account")
		return None

	# Imported OUTSIDE the try: the except clause below names `triton_client`, and an import
	# that failed inside the try would make handling the exception raise NameError instead —
	# an error path that only breaks when the error path is taken.
	from erpnext_enhancements.chat.invoke import triton_client

	try:
		answer = triton_client.ask(
			user=summariser,
			question=prompt,
			context="",
			request_id=f"digest:{room}",
		)
	except triton_client.TritonUnavailable as exc:
		# Logged VERBATIM, and safe to: every message this type carries is content-free by
		# construction — a status code, a path, an exception class name — because the body of
		# a Triton response can echo the prompt back, and the prompt is the transcript. That
		# is the whole reason the type exists.
		#
		# "the model was unavailable" was the string here, and it discarded the one fact
		# needed: a 404 on the session create, a 401 from the gateway and a connection refused
		# want three different answers and produced one sentence. Three separate faults today
		# were slow to diagnose for this reason, all of them mine, all of them the same
		# instinct — strip the detail to protect message content — applied without asking
		# whether the string could contain any.
		_note(f"could not summarise room {room}: {exc}")
		return None
	except Exception as exc:
		# Anything else is a bug rather than an outage, and its message is NOT known to be
		# content-free, so only the class is recorded.
		_note(f"could not summarise room {room}: unexpected {exc.__class__.__name__}")
		return None

	text = (answer.get("text") or "").strip()
	if not text:
		_note(f"could not summarise room {room}: the model answered with no text")
		return None
	return {"text": text, "model": answer.get("model")}


def _prompt(*, previous: str | None, transcript: str, target: int) -> str:
	"""The summarisation prompt. Written here rather than in a settings field on purpose —
	it is code whose changes belong in a diff, not a value somebody edits in a form at 2am."""
	head = (
		f"Summarise this workplace chat in about {target} tokens. Keep decisions, commitments, "
		"dates, numbers, document references and who said what. Drop greetings and small talk. "
		"Write plainly, in the past tense, as notes rather than prose. Do not speculate about "
		"anything that is not in the text."
	)
	if previous:
		return (
			f"{head}\n\nHere is the summary so far:\n{previous}\n\n"
			f"Here is what has been said since. Produce a single updated summary covering both, "
			f"not an appendix:\n{transcript}"
		)
	return f"{head}\n\n{transcript}"


def _summariser_user() -> str:
	"""Whose Triton identity the summariser borrows. **Empty when it has nobody to borrow.**

	The bot's, never a coworker's. Borrowing a person's identity for a job that reads every
	room would attribute a cross-room read to somebody who did not ask for it, and would put
	their name on the audit trail of a machine's work.

	**It used to fall back to ``Administrator``, which is worse than borrowing a coworker's.**
	:func:`~chat.invoke.triton_client.ask` sets the session before minting, and the token
	cache is keyed on the session user — so an unset service account would have handed Triton
	a **superuser** token, on a schedule, unattended. That is the exact failure the
	two-identity rule exists to prevent, arriving through a default rather than through a
	decision. It went unnoticed on production only because Triton was unconfigured and the
	call failed before the token could be used.

	So there is no fallback. An unconfigured summariser builds no digests, which is a missing
	feature; the fallback was a privilege escalation, which is not.

	**It delegates rather than resolving its own**, because resolving its own is what broke it.
	This read ``chat_app_service_account`` raw, and on production that field holds the **Google**
	service account — ``chat-app@…iam.gserviceaccount.com``, a GCP identity for calling the Chat
	API. It is not an ERPNext User and not a mailbox, and Triton's bridge rejected it as a
	non-domain email, which is the correct answer to the wrong question.

	``handler._bot_user`` already resolved this properly: it accepts that field *only when a
	User by that name exists* and otherwise falls back to the real domain account. Two
	resolvers for one identity, and the second one skipped the check that made the first
	correct.
	"""
	from erpnext_enhancements.chat.invoke.handler import _bot_user

	try:
		return _bot_user()
	except Exception:
		# Unconfigured. The caller reports it and builds nothing, which is the same answer as
		# before — reached through the one resolver instead of a second, laxer copy of it.
		return ""


# ------------------------------------------------------------------------------- queries


def _dirty_rooms(*, limit: int) -> list[dict[str, Any]]:
	"""Rooms whose digest is behind, by the **derived** predicate. See the module docstring.

	The two thresholds are ORed, and both are needed: without the count a quiet room's few
	messages wait forever, and without the age a busy room is rebuilt constantly.

	``coalesce(d.watermark_seq, 0)`` is what makes a room with **no** digest yet dirty by
	definition, so the first pass needs no backfill.

	The age comparison binds ``frappe.utils.now_datetime()`` rather than calling SQL ``NOW()``:
	this database runs UTC and Frappe writes site-local, so ``NOW()`` here would make every
	room permanently dirty and the only symptom would be the bill.
	"""
	threshold = cint(_setting("digest_dirty_message_threshold")) or 25
	minutes = cint(_setting("digest_dirty_minutes")) or 15
	cutoff = add_to_date(now_datetime(), minutes=-minutes)

	return (
		frappe.db.sql(
			f"""
		select r.`name` as `name`,
			r.`seq_high_water` as `seq_high_water`,
			r.`last_message_at` as `last_message_at`,
			coalesce(d.`watermark_seq`, 0) as `watermark_seq`,
			coalesce(d.`is_stale`, 0) as `is_stale`
		from `tab{ROOM_DOCTYPE}` r
		left join `tab{ROOM_DIGEST_DOCTYPE}` d on d.`room` = r.`name`
		where r.`is_archived` = 0
			and coalesce(d.`poisoned`, 0) = 0
			and r.`last_message_at` is not null
			and (
				r.`seq_high_water` - coalesce(d.`watermark_seq`, 0) >= %(threshold)s
				or coalesce(d.`is_stale`, 0) = 1
				or (
					r.`seq_high_water` > coalesce(d.`watermark_seq`, 0)
					and (d.`generated_at` is null or d.`generated_at` < %(cutoff)s)
				)
			)
		order by (r.`seq_high_water` - coalesce(d.`watermark_seq`, 0)) desc
		limit %(limit)s
		""",
			{"threshold": threshold, "cutoff": cutoff, "limit": max(cint(limit), 0)},
			as_dict=True,
		)
		or []
	)


def _digest_row(room: str) -> dict[str, Any] | None:
	rows = frappe.db.sql(
		f"""
		select `name`, `summary_text`, `digest_version`, `generation_count`, `watermark_seq`,
			`covered_from`, `covered_from_at`, `is_stale`, `poisoned`, `rebuild_failures`
		from `tab{ROOM_DIGEST_DOCTYPE}`
		where `room` = %(room)s
		limit 1
		""",
		{"room": room},
		as_dict=True,
	)
	return (rows or [None])[0]


def _messages_for_digest(room: str, since: int, *, limit: int) -> list[dict[str, Any]]:
	"""One room's live messages after ``since``, oldest first, bounded.

	Fetched newest-first so the bound keeps the **most recent** history, then reversed so the
	model reads the conversation in the order it happened. Taking the oldest N instead would
	summarise last year and ignore this week.
	"""
	rows = frappe.db.sql(
		f"""
		select `name`, `seq`, `sender`, `sender_email`, `text`, `text_plain`, `creation`
		from `tab{MESSAGE_DOCTYPE}`
		where `room` = %(room)s
			and `seq` > %(since)s
			and `is_deleted` = 0
		order by `seq` desc
		limit %(limit)s
		""",
		{"room": room, "since": max(cint(since), 0), "limit": max(cint(limit), 0)},
		as_dict=True,
	)
	return list(reversed(rows or []))


def _watermark(room: str) -> tuple[int, int, str]:
	"""The **three-value watermark**: ``(max(seq), count(*), max(modified))``.

	All three, because each catches what the others cannot — ``seq`` a new message,
	``modified`` an **edit**, ``count`` a **hard delete** that moves neither. A digest keyed on
	``seq`` alone is unchanged by a delete, so its cache key is unchanged, so the summary
	containing the message somebody just deleted is served again.
	"""
	rows = frappe.db.sql(
		f"""
		select coalesce(max(`seq`), 0), count(*), coalesce(max(`modified`), '1970-01-01')
		from `tab{MESSAGE_DOCTYPE}`
		where `room` = %(room)s and `is_deleted` = 0
		""",
		{"room": room},
	)
	if not rows:
		return (0, 0, "")
	seq, count, modified = rows[0]
	return (cint(seq), cint(count), str(modified))


def _dirty_threads(*, minimum: int, limit: int) -> list[dict[str, Any]]:
	"""Threads past the length threshold whose digest is missing or stale.

	Grouped on ``thread_root``, so a thread that has grown past the threshold since the last
	pass appears without anything having to notice the moment it crossed. ``having`` rather
	than a stored count, for the same reason the room predicate is derived: the count is
	already in the table.
	"""
	return (
		frappe.db.sql(
			f"""
			select m.`thread_root` as `thread_root`, min(m.`room`) as `room`, count(*) as `replies`
			from `tab{MESSAGE_DOCTYPE}` m
			left join `tab{THREAD_DIGEST_DOCTYPE}` d on d.`thread_root` = m.`thread_root`
			where m.`thread_root` is not null
				and m.`thread_root` != ''
				and m.`is_deleted` = 0
				and coalesce(d.`poisoned`, 0) = 0
				and (d.`name` is null or d.`is_stale` = 1 or m.`seq` > coalesce(d.`watermark_seq`, 0))
			group by m.`thread_root`
			having count(*) >= %(minimum)s
			order by count(*) desc
			limit %(limit)s
			""",
			{"minimum": max(cint(minimum), 1), "limit": max(cint(limit), 0)},
			as_dict=True,
		)
		or []
	)


def _thread_messages(thread_root: str, *, limit: int) -> list[dict[str, Any]]:
	"""One thread's live messages, oldest first. One indexed range scan on
	``(thread_root, seq)``; threading is exactly one level deep by construction."""
	return (
		frappe.db.sql(
			f"""
			select `name`, `seq`, `sender`, `sender_email`, `text`, `text_plain`, `creation`, `modified`
			from `tab{MESSAGE_DOCTYPE}`
			where `thread_root` = %(thread_root)s
				and `is_deleted` = 0
			order by `seq` asc
			limit %(limit)s
			""",
			{"thread_root": thread_root, "limit": max(cint(limit), 0)},
			as_dict=True,
		)
		or []
	)


def _record_thread_failure(thread_root: str, reason: str) -> None:
	ceiling = cint(_setting("digest_max_rebuild_failures")) or 3
	try:
		frappe.db.sql(
			f"""
			update `tab{THREAD_DIGEST_DOCTYPE}`
			set `rebuild_failures` = coalesce(`rebuild_failures`, 0) + 1,
				`last_error` = %(reason)s,
				`is_stale` = 1,
				`poisoned` = case when coalesce(`rebuild_failures`, 0) + 1 >= %(ceiling)s then 1 else 0 end
			where `thread_root` = %(thread_root)s
			""",
			{"thread_root": thread_root, "reason": reason[:500], "ceiling": ceiling},
		)
	except Exception:
		pass


def _newest_generated_at() -> Any:
	rows = frappe.db.sql(f"select max(`generated_at`) from `tab{ROOM_DIGEST_DOCTYPE}`")
	return rows[0][0] if rows and rows[0] else None


def _record_failure(room: str, reason: str) -> None:
	"""Count the failure on the room digest, and poison the row at the ceiling.

	The poison-pill guard is what stops a permanently failing room being retried every five
	minutes forever until the Error Log is all one message and nobody reads any of it.

	**The counter has to be able to exist before the first success, or it guards nothing.**
	This was an ``UPDATE`` alone, and an update matches no rows for a room whose digest has
	never been built — while :func:`_dirty_rooms` treats a room with no digest as dirty *by
	definition*, so it is selected again on the next sweep. A room whose very first build
	fails therefore retried every five minutes forever and could never be poisoned: the guard
	was defeated in exactly the case it was written for, and production ran that way from the
	Phase 5 deploy until v1.277.5. So a miss now inserts the row that carries the count.

	The table is a module constant rather than a parameter, deliberately: a doctype passed in
	makes the query's target unresolvable from the source, and a query whose table nobody can
	name by reading it is one no static check can protect. The thread digest has its own
	writer for the same reason.
	"""
	ceiling = cint(_setting("digest_max_rebuild_failures")) or 3
	try:
		frappe.db.sql(
			f"""
			update `tab{ROOM_DIGEST_DOCTYPE}`
			set `rebuild_failures` = coalesce(`rebuild_failures`, 0) + 1,
				`last_error` = %(reason)s,
				`is_stale` = 1,
				`poisoned` = case when coalesce(`rebuild_failures`, 0) + 1 >= %(ceiling)s then 1 else 0 end
			where `room` = %(room)s
			""",
			{"room": room, "reason": reason[:500], "ceiling": ceiling},
		)
		if not _rows_affected():
			# No digest row yet, so there was nothing to count on. Insert the row that holds
			# the count. It is created stale and unbuilt — `summary` stays empty and
			# `watermark_seq` stays 0 — so it is never *served*; it exists only to be counted
			# on, and to be poisoned once the ceiling is reached.
			digest = frappe.new_doc(ROOM_DIGEST_DOCTYPE)
			digest.room = room
			digest.is_stale = 1
			digest.rebuild_failures = 1
			digest.last_error = reason[:500]
			digest.poisoned = 1 if ceiling <= 1 else 0
			digest.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		pass


def _rows_affected() -> int:
	"""How many rows the statement just run touched. One helper, so the raw-SQL guard has one
	entry to exempt rather than an unresolvable count at every call site."""
	try:
		return cint(frappe.db._cursor.rowcount)
	except Exception:
		return 0


# ------------------------------------------------------------------------------- helpers


def _enabled() -> bool:
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "enabled")))
	except Exception:
		return False


def _setting(fieldname: str) -> Any:
	try:
		return frappe.db.get_single_value("Chat Settings", fieldname)
	except Exception:
		return None


def _note(message: str) -> None:
	try:
		frappe.log_error(message, "Chat Indexing")
	except Exception:
		pass

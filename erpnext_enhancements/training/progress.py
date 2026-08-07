# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The watched-seconds engine: interval arithmetic and the attempt progress blob.

This is the module that decides whether somebody passed. Everything a learner
does inside a lesson eventually lands here as a list of ``[start, end]`` second
ranges, and a coverage gate is nothing more than a division over the union of
them. That makes the arithmetic worth keeping *pure* and worth testing to death
— ``tests/test_training_progress.py`` is deliberately larger than this file.

**Why intervals rather than a running counter.** A counter cannot tell watching
from scrubbing: seek to the end, wait, and the counter says the same thing as an
honest viewing. A merged interval union can only grow by seconds actually
occupied, so forward-seeking earns nothing by construction rather than by a
check somebody could forget to write.

**The over-credit trade-off.** The interval list is capped (``max_intervals_per_video``)
because a learner who scrubs a long video can otherwise accumulate thousands of
fragments in a blob written on every heartbeat. When the cap is hit the list is
compacted by *collapsing the smallest gaps first* — never by dropping intervals.
Collapsing a gap slightly over-credits (it counts a second or two nobody
watched); dropping an interval under-credits. Over-crediting somebody who
genuinely watched is the kinder error: the failure mode is a learner passing a
gate they were a few seconds short of, not a learner who sat through the whole
video being told to watch it again.

**The server clock is the anti-cheat backstop.** Intervals arrive from a browser
and a browser can claim anything. :func:`clamp_new_seconds` refuses to credit
more new seconds than the wall time actually elapsed between beats (times a
small tolerance for jitter and a paused-then-resumed tab). Timestamps come from
``now_datetime()``, which is site-local — fine here because *both* ends of every
subtraction are, so the offset cancels. The one hazard is a DST jump landing
between two beats, which is why a negative or absurd elapsed is treated as a
single heartbeat rather than trusted.

**Why cache-first, and why never ``doc.save()``.** A heartbeat every 15 seconds
per active learner is a lot of writes. Going through ``doc.save()`` would spawn a
Version row, fire ``doc_events`` and churn ``modified`` on each one — for a blob
that only matters in aggregate. So the working copy lives in Redis and is
written through with ``frappe.db.set_value(..., update_modified=False)`` only
when it *has* to be: on a state transition, when ``progress_flush_seconds`` has
elapsed, or when a caller forces it. The cost is honest and bounded: a deploy
FLUSHDBs Redis, so the worst case is losing one flush interval of watching —
about a minute — not an attempt. :func:`flush_stale_attempts` drains what a
closed tab left behind.
"""

import json
from datetime import timedelta

import frappe
from frappe.utils import cint, flt, now_datetime

# Redis keys. The meta blob is separate from the progress blob on purpose: it
# holds bookkeeping (last beat, last flush, state fingerprint) that must never
# find its way into ``progress_json``, whose shape is a contract shared with the
# player, the grader and the completion record.
CACHE_PREFIX = "training:attempt:"
PENDING_KEY = "training:attempts:pending"
CACHE_TTL_SECONDS = 24 * 3600

# Two ranges separated by a gap this small are the same watch. Sub-second timing
# jitter in ``timeupdate`` otherwise shreds a continuous viewing into hundreds of
# fragments that differ from each other by rounding alone.
ADJACENCY_SECONDS = 1

# Fallbacks for when Training Settings cannot be read — a fresh site mid-migrate,
# or the Single not existing yet. They match the shipped defaults.
DEFAULT_MAX_INTERVALS = 200
DEFAULT_FLUSH_SECONDS = 60
DEFAULT_HEARTBEAT_SECONDS = 15
DEFAULT_IDLE_TIMEOUT_MINUTES = 120
DEFAULT_DOC_MIN_DWELL = 20

# Tolerance on the server-clock clamp. Above 1.0 because a beat can arrive late
# (a throttled background tab batches its timers) and the seconds it reports were
# still genuinely watched; 1.25 absorbs that without giving a forged payload room
# to matter.
MAX_CLAIM_RATE = 1.25

# ``integrity_flags`` is a text field an auditor reads, not a log. Keep the most
# recent lines and let the oldest fall off.
INTEGRITY_FLAG_LIMIT = 4000

# A guard against an unbounded pending registry, not a real operating limit —
# this site has fifteen employees. See :func:`_mark_pending`.
MAX_PENDING_TRACKED = 5000


# ------------------------------------------------------------------- interval maths


def merge_intervals(existing, new, duration=0, cap=0):
	"""Union of ``existing`` and ``new``, sorted, merged, clipped and capped.

	Pure: no site, no document, no clock. Rubbish input is dropped rather than
	raised on — these lists arrive from a browser, and one malformed pair must
	not cost a learner the rest of their heartbeat.

	``duration <= 0`` means the video's length is not verified yet, in which case
	nothing is clipped: the ranges are kept as claimed so they can be clipped
	properly once a real duration arrives. ``cap <= 0`` means no cap.
	"""
	merged = _normalise(list(existing or []) + list(new or []), cint(duration))
	cap = cint(cap)
	if cap > 0 and len(merged) > cap:
		merged = _collapse_smallest_gaps(merged, cap)
	return merged


def coverage(intervals, duration):
	"""Fraction of the video actually occupied, 0.0 to 1.0.

	Returns ``0.0`` when ``duration <= 0``. That is the whole point of the guard:
	a video whose duration was never probed must read as 0% watched, never as
	100%. Dividing by a missing duration is how a compliance gate quietly stops
	gating — see ``Training Video Asset.duration_source``.

	Note the unit. This returns a *fraction*; :func:`record_heartbeat` and
	``grading.evaluate_gates`` return a *percentage*. They are one factor of 100
	apart and both are called "coverage".
	"""
	duration = cint(duration)
	if duration <= 0:
		return 0.0
	return max(0.0, min(1.0, _covered_seconds(intervals) / float(duration)))


def clamp_new_seconds(claimed, elapsed_server_seconds, max_rate=MAX_CLAIM_RATE):
	"""Newly-claimed seconds, capped at what the server clock allows.

	The backstop for every other measurement in this module. A client controls
	its own timestamps, its own ``currentTime`` and its own payload; it does not
	control how much wall time passed between the two requests that the server
	itself timed. Whatever exceeds ``elapsed * max_rate`` is simply not credited.
	"""
	claimed = max(0, int(flt(claimed)))
	elapsed = max(0.0, flt(elapsed_server_seconds))
	return min(claimed, int(elapsed * flt(max_rate)))


def _normalise(pairs, duration):
	"""Sort, clip, drop the unusable, and union everything that touches."""
	clean = []
	for pair in pairs or []:
		bounds = _bounds(pair, duration)
		if bounds:
			clean.append(bounds)
	clean.sort()

	merged = []
	for start, end in clean:
		# A single comparison covers overlapping (negative gap), touching (zero)
		# and adjacent-within-a-second. Nested ranges fall out of the max().
		if merged and start - merged[-1][1] <= ADJACENCY_SECONDS:
			merged[-1][1] = max(merged[-1][1], end)
		else:
			merged.append([start, end])
	return merged


def _bounds(pair, duration):
	"""One sanitised ``[start, end]``, or ``None`` if the pair is unusable.

	Truncates to whole seconds rather than rounding: sub-second precision is
	noise at this resolution, and truncating both ends under-credits by less than
	a second per range — which the adjacency merge then mostly absorbs anyway.
	"""
	try:
		start, end = int(flt(pair[0])), int(flt(pair[1]))
	except (TypeError, ValueError, IndexError, KeyError):
		return None

	start = max(0, start)
	if duration > 0:
		end = min(end, duration)
		if start >= duration:
			return None
	if end <= start:
		return None
	return [start, end]


def _collapse_smallest_gaps(intervals, cap):
	"""Compact to ``cap`` ranges by repeatedly closing the narrowest gap.

	Deliberately over-credits rather than dropping ranges; see the module
	docstring for why that is the kinder error. Ties break on the earlier gap so
	the result is deterministic — two learners with identical watch histories must
	produce identical stored progress, or a support conversation about "why does
	his say 81% and mine 80%" has no answer.
	"""
	merged = [list(interval) for interval in intervals]
	while len(merged) > max(cap, 1):
		index = min(range(len(merged) - 1), key=lambda i: (merged[i + 1][0] - merged[i][1], i))
		merged[index] = [merged[index][0], max(merged[index][1], merged[index + 1][1])]
		del merged[index + 1]
	return merged


def _covered_seconds(intervals):
	"""Total seconds occupied by ``intervals``, unioned first so overlaps in an
	unmerged list cannot be counted twice."""
	return sum(end - start for start, end in _normalise(intervals, 0))


def _uncovered(interval, blocking):
	"""The parts of ``interval`` not already inside ``blocking``.

	``blocking`` must be normalised (sorted, disjoint). Used to spend a clamped
	budget on the seconds that would actually earn credit, rather than on
	re-sent ranges the learner already has.
	"""
	start, end = interval
	pieces = []
	for block_start, block_end in blocking:
		if block_end <= start:
			continue
		if block_start >= end:
			break
		if block_start > start:
			pieces.append([start, block_start])
		start = max(start, block_end)
		if start >= end:
			return pieces
	if start < end:
		pieces.append([start, end])
	return pieces


def _trim_to_budget(existing, claimed, budget):
	"""As much of ``claimed`` as ``budget`` new seconds pays for, earliest first.

	Earliest first because that is the order somebody watching honestly would
	have earned them, and because truncating the *tail* of an over-claim leaves
	the learner's real position intact — the next beat picks up from there rather
	than re-litigating the same range.
	"""
	kept = []
	remaining = max(0, cint(budget))
	for interval in claimed:
		if remaining <= 0:
			break
		for piece in _uncovered(interval, existing):
			if remaining <= 0:
				break
			span = piece[1] - piece[0]
			if span <= remaining:
				kept.append(piece)
				remaining -= span
			else:
				kept.append([piece[0], piece[0] + remaining])
				remaining = 0
	return kept


# ------------------------------------------------------------------------ the blob


def load(attempt_name):
	"""The attempt's progress blob, from cache when possible.

	Always returns a dict with a ``lessons`` key, so callers never branch on a
	brand-new attempt. The returned dict is the working copy: mutate it and hand
	it to :func:`save`.
	"""
	if not attempt_name:
		return _empty()

	cached = _cache_get(_key(attempt_name))
	if isinstance(cached, dict) and isinstance(cached.get("lessons"), dict):
		return cached

	data = _read_stored(attempt_name)
	_cache_set(_key(attempt_name), data)
	return data


def save(attempt_name, data, force=False):
	"""Update the cached blob, and write through to MySQL only when warranted.

	Write-through happens on ``force``, on a state transition (a lesson finishing,
	a checkpoint being answered, a quiz run completing), or once
	``progress_flush_seconds`` has passed since the last write. Everything else
	stays in Redis and is drained by :func:`flush_stale_attempts`.

	Never ``doc.save()``. That would put a Version row, a full ``doc_events``
	cycle and a ``modified`` bump behind every heartbeat, for a field nobody
	audits second by second.
	"""
	if not attempt_name:
		return

	if not isinstance(data, dict):
		data = _empty()
	data.setdefault("lessons", {})

	_cache_set(_key(attempt_name), data)

	meta = _meta(attempt_name)
	fingerprint = _state_fingerprint(data)
	since_flush = _seconds_since(meta.get("flushed_at"))
	flush_after = _setting("progress_flush_seconds", DEFAULT_FLUSH_SECONDS)

	due = (
		bool(force)
		or fingerprint != meta.get("fingerprint")
		or since_flush is None
		or since_flush >= flush_after
	)

	now = now_datetime()
	if not due and _mark_pending(attempt_name, now):
		meta["dirty"] = 1
		meta["touched_at"] = now
		_meta_set(attempt_name, meta)
		return

	# Either the write is due, or the pending registry is full and holding this
	# blob only in Redis would risk losing it with nobody tracking that it is
	# owed. Writing is always the safe way to be wrong.
	if not _write_through(attempt_name, data):
		_mark_pending(attempt_name, now)
		return

	meta.update({"fingerprint": fingerprint, "flushed_at": now, "touched_at": now, "dirty": 0})
	_meta_set(attempt_name, meta)
	_clear_pending(attempt_name)


def record_heartbeat(attempt_name, payload):
	"""Credit a beat's watched ranges and return what the *server* thinks.

	``{"coverage": <percent>, "credited": <seconds>, "flags": [...]}``. Every
	number in that reply is computed here — the client is told the outcome, never
	consulted about it. ``user`` and ``employee`` are never read from the payload
	either; the runtime derives them from ``frappe.session.user``.

	The caller is responsible for one thing this module cannot check: putting the
	*server-verified* duration (``Training Video Asset.duration_seconds``) into
	``payload["duration"]``. A client-supplied duration that is too small would
	inflate coverage, so a duration that shrinks below what is already stored is
	ignored and flagged.
	"""
	payload = payload or {}
	lesson_key = str(payload.get("lesson_key") or "").strip()
	block_key = str(payload.get("block_key") or "").strip()
	if not attempt_name or not lesson_key or not block_key:
		return {"coverage": 0.0, "credited": 0, "flags": ["ignored"], "ack": None}

	data = load(attempt_name)
	lesson = data["lessons"].setdefault(lesson_key, _empty_lesson())
	lesson.setdefault("blocks", {})
	block = lesson["blocks"].setdefault(block_key, _empty_block())

	# A document block is dwell time against a target this side owns.
	#
	# `_resolve_duration` trusts the payload's `duration` for want of anything
	# better, which is safe for video — the Training Video Asset row carries a
	# server-verified duration, and the ratchet below refuses a shrinking one —
	# and is not safe here, because a document has no asset to check against. A
	# payload claiming `duration: 1` would reach full coverage after one second.
	# So the divisor is the setting, read here, and the client's is discarded.
	kind = str(payload.get("kind") or "").strip().lower()
	if kind == "doc":
		payload = dict(payload)
		payload["duration"] = _setting("doc_min_dwell_seconds", DEFAULT_DOC_MIN_DWELL)

	flags = []
	duration = _resolve_duration(block, payload, flags)
	cap = _setting("max_intervals_per_video", DEFAULT_MAX_INTERVALS)

	existing = merge_intervals(block.get("iv") or [], [], duration, cap)
	claimed = merge_intervals(payload.get("intervals") or [], [], duration)

	candidate = merge_intervals(existing, claimed, duration, cap)
	gained = _covered_seconds(candidate) - _covered_seconds(existing)

	# The player counts the integer seconds it is claiming and sends the total in
	# `claimed_seconds`, arrived at independently of how it encodes the ranges. So
	# the two numbers are a check on each other, and when they diverge the
	# encoding is being misread. That is not hypothetical: for the whole of
	# v1.235.0 the wire's `[start, end]` pairs were decoded as `[start, length]`,
	# every beat after a learner's first pause banked roughly double, and this
	# exact figure was sitting in the payload the whole time, unread.
	#
	# Flagged, not trimmed. Compaction (`_collapse_smallest_gaps`) legitimately
	# over-credits by a second or two when the interval cap is hit, and adjacency
	# merging closes a one-second gap per join by design — trimming here would
	# fight decisions this module makes on purpose, and quietly. An auditor
	# reading `integrity_flags` should see that coverage was inflated and by how
	# much; a learner should not lose seconds to a heuristic.
	stated = cint(payload.get("claimed_seconds"))
	if stated and gained > stated + len(claimed) * ADJACENCY_SECONDS:
		flags.append("claim_mismatch")
		_flag(
			attempt_name,
			f"{lesson_key}/{block_key}: player claimed {stated}s, the server derived "
			f"{gained}s from its ranges. The two sides disagree about the wire format.",
		)

	allowed = clamp_new_seconds(gained, _elapsed_seconds(attempt_name))
	if allowed < gained:
		candidate = merge_intervals(existing, _trim_to_budget(existing, claimed, allowed), duration, cap)
		flags.append("over_claim")
		_flag(
			attempt_name,
			f"{lesson_key}/{block_key}: claimed {gained}s of new watching, credited {allowed}s.",
		)
		gained = _covered_seconds(candidate) - _covered_seconds(existing)

	block["iv"] = candidate
	block["dur"] = duration
	block["cov"] = round(coverage(candidate, duration), 4)
	# Counters, not gates. They are what a manager looks at when a coverage
	# number is technically fine but the shape of it is odd: forty seeks and two
	# hundred seconds hidden is a different afternoon from nought and nought.
	block["seeks"] = cint(block.get("seeks")) + max(0, cint(payload.get("seeks")))
	block["hidden"] = cint(block.get("hidden")) + max(0, cint(payload.get("hidden")))

	# "I have read it", recorded at last.
	#
	# `blocks.js` has sent this since the document blocks were written and nothing
	# here had ever read it, so the explicit acknowledgement — the module README's
	# documented substitute for per-page tracking, and the thing an auditor would
	# actually be shown — was recorded nowhere. `player.js` then read `response.ack`
	# back off a reply that never carried it.
	#
	# Set-only, never cleared, matching the one-way checkbox on the client. A beat
	# replayed out of the offline queue arrives with whatever was true when it was
	# built, and the last one to land must not be able to un-tick a statement of
	# fact. The 0 is written on the first document beat so the gate can tell
	# "opened, not yet acknowledged" from "not a tracked block at all" — player.js
	# treats an absent `ack` as not-tracked and will not hold a learner on it.
	if kind == "doc" and "ack" not in block:
		block["ack"] = 0
	if cint(payload.get("ack")):
		block["ack"] = 1

	if lesson.get("status") != "done":
		lesson["status"] = "in_progress"

	# Spend only what was credited. See :func:`_touch_beat` — resetting the window
	# to now here is what starved every replayed beat after the first.
	_touch_beat(attempt_name, spent_seconds=gained / MAX_CLAIM_RATE)
	save(attempt_name, data)

	# `ack` is None for a video block, which is exactly what player.js's
	# `if (response.ack != null)` wants — it merges an acknowledgement when there
	# is one and leaves the block alone when the concept does not apply.
	return {
		"coverage": round(block["cov"] * 100.0, 2),
		"credited": int(gained),
		"flags": flags,
		"ack": block.get("ack"),
	}


def flush_stale_attempts():
	"""Hourly job: write through blobs whose session ended without a final beacon.

	A closed laptop lid sends no last heartbeat, so without this the last minute
	of an attempt lives only in Redis until it expires. Nothing here emails or
	acts on a learner's behalf; it is purely a drain.
	"""
	if any(frappe.flags.get(flag) for flag in ("in_migrate", "in_install", "in_patch", "in_import")):
		return

	pending = _pending()
	if not pending:
		return

	# Two flush intervals, so an attempt that is merely mid-beat is never written
	# twice for no reason. The job runs hourly; in practice everything still
	# pending when it fires is genuinely abandoned.
	threshold = max(2 * _setting("progress_flush_seconds", DEFAULT_FLUSH_SECONDS), 120)

	wrote = False
	for attempt_name, touched_at in list(pending.items()):
		since = _seconds_since(touched_at)
		if since is not None and since < threshold:
			continue

		cached = _cache_get(_key(attempt_name))
		if isinstance(cached, dict) and isinstance(cached.get("lessons"), dict):
			save(attempt_name, cached, force=True)
			wrote = True
		else:
			# Redis dropped it (a deploy FLUSHDB, or the TTL). There is nothing
			# left to write and nothing to be done about it — the loss is bounded
			# at one flush interval, which is the deal this design makes.
			_clear_pending(attempt_name)

	if wrote:
		frappe.db.commit()


# ------------------------------------------------------------------------- helpers


def _empty():
	return {"lessons": {}}


def _empty_lesson():
	return {"status": "in_progress", "blocks": {}, "checkpoints": {}, "quiz": {}}


def _empty_block():
	return {"dur": 0, "iv": [], "cov": 0.0, "seeks": 0, "hidden": 0}


def _key(attempt_name):
	return f"{CACHE_PREFIX}{attempt_name}"


def _meta_key(attempt_name):
	return f"{CACHE_PREFIX}{attempt_name}:meta"


def _setting(field, default):
	"""One numeric Training Settings value, or ``default`` if it cannot be read.

	Never throws. A heartbeat arriving while the Single is missing (fresh site,
	mid-migrate) must degrade to the shipped defaults, not 500 at a learner.
	"""
	try:
		value = cint(frappe.get_cached_doc("Training Settings").get(field))
	except Exception:
		return default
	return value or default


def _cache_get(key):
	try:
		return frappe.cache().get_value(key)
	except Exception:
		return None


def _cache_set(key, value):
	try:
		frappe.cache().set_value(key, value, expires_in_sec=CACHE_TTL_SECONDS)
	except Exception:
		# Deliberately silent. If Redis is unreachable this fires on every beat of
		# every learner, and a log flood would bury the actual outage. The
		# consequence is only that nothing is cached, so every save looks like a
		# first save and writes through — slower, still correct.
		pass


def _read_stored(attempt_name):
	"""``progress_json`` off the row, defensively.

	A blob that will not parse is replaced with an empty one and logged. That
	loses watch history, which is bad; the alternative is a learner who cannot
	open the lesson at all until somebody edits JSON in the database, which is
	worse.
	"""
	try:
		raw = frappe.db.get_value("Training Attempt", attempt_name, "progress_json")
	except Exception:
		return _empty()
	if not raw:
		return _empty()

	try:
		data = frappe.parse_json(raw)
	except Exception:
		frappe.log_error(
			f"Training Attempt {attempt_name} has unparseable progress_json; starting a fresh blob.",
			"Training progress",
		)
		return _empty()

	if not isinstance(data, dict) or not isinstance(data.get("lessons"), dict):
		return _empty()
	return data


def _write_through(attempt_name, data):
	"""Persist the blob. Returns False if the write did not happen."""
	try:
		frappe.db.set_value(
			"Training Attempt",
			attempt_name,
			"progress_json",
			json.dumps(data, separators=(",", ":")),
			update_modified=False,
		)
		return True
	except Exception:
		frappe.log_error(
			f"Could not write progress for Training Attempt {attempt_name}\n{frappe.get_traceback()}",
			"Training progress",
		)
		return False


def _state_fingerprint(data):
	"""A compact signature of everything a *transition* could change.

	:func:`save` takes ``(attempt_name, data, force)`` and nothing else, so it has
	to work out for itself whether something happened that must not sit in Redis
	waiting for a flush. Watched seconds are allowed to lag by a flush interval;
	a passed quiz or an answered checkpoint is not.
	"""
	parts = []
	for lesson_key in sorted(data.get("lessons") or {}):
		lesson = data["lessons"].get(lesson_key) or {}
		checkpoints = lesson.get("checkpoints") or {}
		answered = sorted(k for k, v in checkpoints.items() if cint((v or {}).get("answered")))
		quiz = lesson.get("quiz") or {}
		parts.append(
			"|".join(
				[
					lesson_key,
					str(lesson.get("status") or ""),
					",".join(answered),
					str(cint(quiz.get("runs"))),
				]
			)
		)
	return ";".join(parts)


def _meta(attempt_name):
	value = _cache_get(_meta_key(attempt_name))
	return value if isinstance(value, dict) else {}


def _meta_set(attempt_name, meta):
	_cache_set(_meta_key(attempt_name), meta)


def _seconds_since(stamp):
	"""Seconds between ``stamp`` and now, or ``None`` if that cannot be computed."""
	if not stamp:
		return None
	try:
		return (now_datetime() - stamp).total_seconds()
	except Exception:
		return None


def _touch_beat(attempt_name, spent_seconds=None):
	"""Move the clamp's window forward.

	``spent_seconds`` is how much wall time this beat's credit *paid for*
	(``credited / MAX_CLAIM_RATE``). The window advances by that much rather than
	jumping to now, so a burst of beats arriving together shares one budget
	instead of the first one swallowing it whole.

	That mattered on the path the queue exists for. A learner whose phone drops
	signal keeps watching; ``video.js`` stacks the beats in ``sessionStorage`` and
	replays them in one drain when the connection returns. Every one of those
	requests used to reset the window to now, so beat two arrived a few hundred
	milliseconds later, ``int(0.2 * 1.25)`` allowed **zero** seconds, and the
	whole backlog was thrown away — *and* flagged as over-claiming, which puts an
	integrity note on the record of somebody who did nothing but ride a lift.

	Never past ``now``: credit is drawn from time that has actually elapsed. The
	unspent remainder does accumulate, and that is deliberate — the alternative,
	forfeiting it per request, is what caused the bug. It is also no weaker than
	before in any way that matters: a paused video sends no beats at all, so an
	idle window already grows without limit, bounded only by the idle ceiling in
	:func:`_elapsed_seconds`. This changes who benefits from that, not whether it
	exists.
	"""
	meta = _meta(attempt_name)
	now = now_datetime()
	previous = meta.get("beat_at")
	if spent_seconds is None or not previous:
		meta["beat_at"] = now
	else:
		advanced = previous + timedelta(seconds=max(0.0, flt(spent_seconds)))
		meta["beat_at"] = advanced if advanced < now else now
	_meta_set(attempt_name, meta)


def _elapsed_seconds(attempt_name):
	"""Wall time since this attempt's previous beat, for the clamp.

	The fallbacks all resolve to one heartbeat interval rather than to zero. Zero
	would mean the first beat after any Redis flush credits nothing, which reads
	to a learner as the video not counting — a support ticket generated by our own
	deploy. One interval is the smallest defensible allowance.

	A negative elapsed means the site clock went backwards (DST, or an NTP step).
	Trusting it would either zero a legitimate beat or, if it were used naively,
	hand out an hour of credit.
	"""
	heartbeat = _setting("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_SECONDS)
	elapsed = _seconds_since(_meta(attempt_name).get("beat_at"))
	if elapsed is None or elapsed < 0:
		return heartbeat

	ceiling = _setting("attempt_idle_timeout_minutes", DEFAULT_IDLE_TIMEOUT_MINUTES) * 60
	return min(elapsed, ceiling)


def _resolve_duration(block, payload, flags):
	"""The duration to divide by: never smaller than what is already stored.

	Coverage is ``watched / duration``, so a shrinking duration is the cheapest
	possible cheat — halve it and halve the watching needed. A growing one is
	either the first beat or a genuine re-probe, and costs the learner nothing
	they did not owe.
	"""
	stored = cint(block.get("dur"))
	claimed = cint(payload.get("duration"))
	if claimed <= 0:
		return stored
	if stored and claimed < stored:
		flags.append("duration_shrank")
		return stored
	return claimed


def _flag(attempt_name, message):
	"""Append a line to the attempt's ``integrity_flags``.

	Guarded on the column existing: ``doc_events`` and jobs can run during a
	migrate in which the field has not been added yet, and a missing column must
	not turn a heartbeat into a 500.
	"""
	try:
		if not frappe.db.has_column("Training Attempt", "integrity_flags"):
			return
		existing = frappe.db.get_value("Training Attempt", attempt_name, "integrity_flags") or ""
		line = f"{now_datetime()} {message}"
		# Keep the tail: the newest flags are the ones somebody is investigating.
		combined = f"{existing}\n{line}".strip()[-INTEGRITY_FLAG_LIMIT:]
		frappe.db.set_value(
			"Training Attempt", attempt_name, "integrity_flags", combined, update_modified=False
		)
	except Exception:
		frappe.log_error(
			f"Could not record an integrity flag on {attempt_name}\n{frappe.get_traceback()}",
			"Training progress",
		)


def _pending():
	value = _cache_get(PENDING_KEY)
	return value if isinstance(value, dict) else {}


def _mark_pending(attempt_name, when):
	"""Register an unwritten blob for :func:`flush_stale_attempts`.

	Returns False when the registry is full, which tells :func:`save` to write
	through immediately instead — an untracked dirty blob is the one case where
	the bounded-loss deal stops being bounded.
	"""
	pending = _pending()
	if attempt_name not in pending and len(pending) >= MAX_PENDING_TRACKED:
		frappe.log_error(
			f"More than {MAX_PENDING_TRACKED} training attempts are awaiting a progress flush; "
			f"writing {attempt_name} through immediately.",
			"Training progress",
		)
		return False
	pending[attempt_name] = when
	_cache_set(PENDING_KEY, pending)
	return True


def _clear_pending(attempt_name):
	pending = _pending()
	if pending.pop(attempt_name, None) is not None:
		_cache_set(PENDING_KEY, pending)

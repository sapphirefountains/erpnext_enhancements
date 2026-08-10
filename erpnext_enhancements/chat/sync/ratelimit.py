# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Rate limiting for the Google Chat write path — pure arithmetic, then a Redis deployment.

Two halves, and the split is the whole design:

* :func:`bucket_decision` and :func:`quota_decision` are **pure, total functions of their
  arguments**. They import nothing but the standard library, so they run in the bench-free
  CI tier — the only tier in this repo with automatic regression protection (``CLAUDE.md``:
  there is no Frappe integration-test job). **The Python is the tested specification.**
* :class:`SpaceRateLimiter` and :class:`ProjectQuota` are the deployment of that
  specification onto Redis, so the budget is shared across workers. They import ``frappe``
  **inside their methods only**; a module-scope import would drag the arithmetic out of the
  tier that tests it. **The Lua is the deployment, and it must implement exactly the same
  branch and nothing else** — it is printed immediately below the function it mirrors so
  the two can be read side by side, and ``tests/test_chat_ratelimit.py`` asserts the script
  issues no Redis command outside the allowlist the arithmetic needs.

--------------------------------------------------------------------------------------
Why Redis and not an in-process bucket
--------------------------------------------------------------------------------------

Google's per-space limit is **1 write per second, shared across every Chat app acting in
that space** (``PHASE2_VERIFIED.md`` §2) — a human typing in the native client spends from
the same second we are trying to claim. An in-process bucket is therefore wrong from the
moment a second worker starts, and this deployment runs several. ``frappe.cache()`` **is**
a ``redis.Redis`` subclass, so ``.eval()`` is available directly and keys are already
prefixed with the site's ``db_name`` — no cross-site collision, and no second Redis client
to configure.

**The bucket is an optimisation; backoff is the correctness mechanism.** Google publishes
two caveats no design can remove: *"Additional rate limit checks on the Chat backend might
also generate the same error response"* and *"High API traffic targeting the same space can
trigger additional internal limits that aren't visible in the Quotas page."* Staying under
one write per second does **not** guarantee no 429. Never delete the retry loop because the
limiter is in place.

--------------------------------------------------------------------------------------
What does NOT go through the space bucket
--------------------------------------------------------------------------------------

``members.create`` / ``members.delete`` and ``spaces.setup`` / ``spaces.create`` appear in
**no** per-space row of Google's table (``PHASE2_VERIFIED.md`` §2, corrections 1 and 2) —
in the ``setup`` case the space does not exist yet. Charging them to
:class:`SpaceRateLimiter` would throttle membership reconciliation roughly 300× harder than
necessary: 1/second instead of 300/minute. They are charged to :class:`ProjectQuota` alone.

``media.upload`` is the opposite case: it **shares** the per-space write bucket with
``messages.create``, so relaying one attachment message costs **two** seconds of that
space's budget — hence :data:`UPLOAD_WRITE_COST_MS`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

# --- the per-space bucket: pure arithmetic ------------------------------------------


@dataclass(frozen=True)
class BucketDecision:
	"""The answer to "may I write to this space right now?".

	``next_free_ms`` is returned on **both** branches, and it means different things on
	each: on ``allowed`` it is the new watermark the bucket was advanced to, and on a
	refusal it is the existing watermark, unchanged. A caller that stores it unconditionally
	is therefore correct either way, which is the reason the field is not optional.
	"""

	allowed: bool
	wait_ms: int
	next_free_ms: int


def bucket_decision(now_ms: int, next_free_ms: int, cost_ms: int) -> BucketDecision:
	"""GCRA, with zero burst tolerance. Pure and total.

	``next_free_ms`` is the epoch-millisecond at which the bucket becomes free again — the
	"theoretical arrival time" of GCRA, stored as a single integer. One integer per space is
	the entire state: no token count to decay, no timestamp list to trim, and nothing that
	drifts if a worker dies holding it.

	Burst tolerance is deliberately **zero**. A leaky bucket that allowed even a two-write
	burst would put two ``messages.create`` calls into the same second on a space, which is
	exactly the thing Google's limit forbids and which our own ordering rules do not need:
	a room is a strict FIFO drained at one write per second, so there is no parallelism to
	recover with a burst allowance.

	**A refusal does not reserve.** ``next_free_ms`` is returned unchanged, so two workers
	racing for the same space are both told to wait and both come back; the atomic
	``.eval()`` in :class:`SpaceRateLimiter` is what makes exactly one of them win on the
	retry. Reserving on refusal would be fairer in a queueing sense and strictly worse here,
	because a worker that is told to wait 900 ms and then crashes would have burned a slot
	nobody used, and the sweeper's only way to notice is the lease expiring.

	``cost_ms`` below zero is clamped to zero rather than rejected: this is called on the
	hot path from a background job, and raising on a bad settings value is how a message
	disappears with an empty Error Log. A zero cost is still gated by the bucket — it may
	proceed only while the bucket is free — it simply does not advance the watermark.
	"""
	now = int(now_ms)
	watermark = int(next_free_ms)
	cost = max(int(cost_ms), 0)

	if watermark <= now:
		return BucketDecision(allowed=True, wait_ms=0, next_free_ms=now + cost)
	return BucketDecision(allowed=False, wait_ms=watermark - now, next_free_ms=watermark)


#: The Lua deployment of :func:`bucket_decision`, one round trip and atomic. Read the two
#: together: the ``if`` here is the ``if`` there, the two ``return`` shapes are the two
#: ``BucketDecision`` shapes, and nothing else happens. ``cost`` is clamped **on the Python
#: side** before it is passed so the script can stay a literal transcription of the branch.
#:
#: ``ttl_ms`` exists for one reason and it is not memory: it is the clock-skew fuse. If an
#: NTP step ever writes a watermark far in the future, every write to that space would be
#: refused until real time caught up — the key expiring within a few seconds of its own
#: cost bounds that outage instead of leaving a space wedged.
#:
#: Epoch milliseconds are well inside the 2**53 a Lua number represents exactly, so the
#: implicit double arithmetic here is not lossy; Redis truncates the returned numbers to
#: integers, which is what the caller wants anyway.
SPACE_BUCKET_LUA: Final[str] = """
-- KEYS[1] = the space bucket key. ARGV = now_ms, cost_ms (already clamped >= 0), ttl_ms.
-- Returns {allowed, wait_ms, next_free_ms} -- exactly BucketDecision, in field order.
local now = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local next_free = tonumber(redis.call('GET', KEYS[1])) or 0
if next_free <= now then
	local advanced = now + cost
	redis.call('SET', KEYS[1], advanced, 'PX', ttl)
	return {1, 0, advanced}
end
return {0, next_free - now, next_free}
"""

#: One write per second per space: ``spaces.messages.create/patch/delete``, ``spaces.patch``,
#: ``spaces.delete``.
SPACE_WRITE_COST_MS: Final[int] = 1000

#: ``media.upload`` shares the space bucket with ``messages.create``, and an attachment
#: message is an upload **and** a create — two writes, two seconds.
UPLOAD_WRITE_COST_MS: Final[int] = 2000

#: How long a bucket key outlives the second it reserves. Long enough that a key is not
#: re-created on every write of a busy space, short enough that a clock-skew poisoned
#: watermark clears on its own. See :data:`SPACE_BUCKET_LUA`.
BUCKET_TTL_MARGIN_MS: Final[int] = 5000

#: Ceiling on how long a blocking :meth:`SpaceRateLimiter.acquire` may pin a worker. The
#: lesson is ``gchat/backoff.py``'s: the client sleeps *synchronously*, so any wait bound
#: multiplied by the retry count is how long one call can hold a queue slot. Past this the
#: acquire gives up and hands the refusal back, and the job is deferred by ``available_at``
#: instead — which is free, because the sweeper is the delivery guarantee anyway.
DEFAULT_MAX_BLOCK_MS: Final[int] = 5000

#: Smallest sleep worth taking. Below this the round trip costs more than the wait.
_MIN_SLEEP_MS: Final[int] = 5


# --- the per-project fixed windows: pure arithmetic ----------------------------------


@dataclass(frozen=True)
class QuotaDecision:
	"""``allowed`` plus the window counter **after** the decision — unchanged on a refusal."""

	allowed: bool
	count: int


def quota_decision(current: int, cost: int, limit: int) -> QuotaDecision:
	"""Fixed-window counter arithmetic. Pure and total.

	Refusing **before** incrementing is the load-bearing detail. The naive "increment, then
	compare" shape lets a rejected charge poison the rest of the window: once the counter is
	over the limit it stays over, so a subsequent cheaper charge that would have fitted is
	refused too, and the window never recovers because every refusal pushes it further out.

	``limit <= 0`` refuses everything — a zero quota is a configured stop, not a disabled
	check, and reading it as "unlimited" is the failure direction that gets an API client
	banned. A caller that wants no limit does not call this.

	``cost <= 0`` is clamped to zero and is always allowed: charging nothing cannot exceed
	anything, and it must not be turned into a refusal by an arithmetic accident.
	"""
	seen = max(int(current), 0)
	price = max(int(cost), 0)
	ceiling = int(limit)

	if ceiling <= 0:
		return QuotaDecision(allowed=False, count=seen)
	if seen + price > ceiling:
		return QuotaDecision(allowed=False, count=seen)
	return QuotaDecision(allowed=True, count=seen + price)


def fixed_window_index(now_ms: int, window_ms: int) -> int:
	"""Which fixed window ``now_ms`` falls in. Part of the cache key, so it must be pure.

	Fixed windows, not a sliding log, and the trade is stated rather than hidden: a fixed
	window permits up to **2× the limit** across a window boundary (the last instant of one
	window plus the first of the next). That is acceptable here and only here, because the
	per-space GCRA already paces every single write: ~20 mirrored rooms at one write per
	second is a theoretical ceiling of 1,200 message writes per minute against Google's
	3,000, so the project quota is a safety net for a runaway loop and not the thing that
	shapes normal traffic. A sliding window would need a sorted set per bucket and a trim on
	every call, to police a limit we are a factor of two away from touching.
	"""
	span = max(int(window_ms), 1)
	return int(now_ms) // span


#: The Lua deployment of :func:`quota_decision`, atomic across workers. Same discipline as
#: :data:`SPACE_BUCKET_LUA`: the branch is the Python branch, and the only side effects are
#: the increment and the expiry.
#:
#: ``PEXPIRE`` runs on **every** allowed charge, not only when the counter is created. The
#: "set the TTL only if this is the first increment" shape is a classic fixed-window bug: a
#: crash between ``INCRBY`` and ``EXPIRE`` leaves a counter with no TTL at all, which never
#: resets and permanently refuses the bucket. The key already carries the window index, so
#: re-arming its expiry is idempotent and costs nothing.
PROJECT_QUOTA_LUA: Final[str] = """
-- KEYS[1] = the window-scoped counter key. ARGV = cost (>= 0), limit, ttl_ms.
-- Returns {allowed, count} -- exactly QuotaDecision, in field order.
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', KEYS[1])) or 0
if limit <= 0 or current + cost > limit then
	return {0, current}
end
local total = redis.call('INCRBY', KEYS[1], cost)
redis.call('PEXPIRE', KEYS[1], ttl)
return {1, total}
"""

#: Google's per-project buckets are **separate counters per category**, not one shared pool
#: (``PHASE2_VERIFIED.md`` §2, correction 3) — budgeting them as one under-uses the API
#: roughly fivefold. These are the bucket names; the limits live in ``Chat Settings`` and
#: are passed to :meth:`ProjectQuota.charge`, because a limit hard-coded here is a limit
#: nobody can lower at 2am when Google changes it.
#:
#: The published ceilings, per 60 seconds, for reference only:
#: message writes 3,000 · message reads 3,000 · membership writes 300 · space writes 60 ·
#: attachment writes 600 · Workspace Events subscription writes 600 (and 100 per user).
BUCKET_MESSAGE_WRITES: Final[str] = "message_writes"
BUCKET_MESSAGE_READS: Final[str] = "message_reads"
BUCKET_MEMBERSHIP_WRITES: Final[str] = "membership_writes"
BUCKET_SPACE_WRITES: Final[str] = "space_writes"
BUCKET_ATTACHMENT_WRITES: Final[str] = "attachment_writes"
BUCKET_SUBSCRIPTION_WRITES: Final[str] = "subscription_writes"

#: Google states every project bucket per 60 seconds.
PROJECT_WINDOW_MS: Final[int] = 60_000


def _now_ms() -> int:
	"""Epoch milliseconds. ``time.time`` and not ``frappe.utils.now_datetime``, deliberately.

	``now_datetime()`` is **site-local**, and this repo has already shipped one bug from
	comparing it to a UTC instant. A rate-limit watermark is a monotonic-ish wall-clock
	number shared between workers and a Lua script; it must be one unambiguous scale, and
	epoch milliseconds is the only one both sides can agree on without a timezone.
	"""
	return int(time.time() * 1000)


def _as_int(raw: object, default: int = 0) -> int:
	"""Coerce a Redis reply to ``int``. ``frappe.cache()`` may hand back ``bytes``."""
	if raw is None:
		return default
	if isinstance(raw, bytes | bytearray):
		raw = raw.decode("utf-8", errors="ignore")
	try:
		return int(float(raw))
	except (TypeError, ValueError):
		return default


class SpaceRateLimiter:
	"""The per-space 1-write/second bucket, shared across workers through Redis.

	Pass the **Google space resource name** (``spaces/AAAA…``) rather than the ERPNext
	``Chat Room`` name: the bucket Google enforces is per space, and keying on the space
	keeps the budget correct even if a room row is ever recreated against an existing space.

	``frappe`` is imported inside the methods. That is not an optimisation — it is what
	keeps :func:`bucket_decision` above importable in the bench-free CI tier, and that tier
	is the only automatic regression protection this arithmetic has.
	"""

	def __init__(
		self,
		*,
		key_prefix: str = "chat:space_write",
		max_block_ms: int = DEFAULT_MAX_BLOCK_MS,
		clock: Callable[[], int] | None = None,
	) -> None:
		self.key_prefix = key_prefix
		self.max_block_ms = max(int(max_block_ms), 0)
		#: Injectable so a caller can drive the limiter from a test clock. Defaults to the
		#: wall clock; nothing in this class reads ``frappe.utils`` (see :func:`_now_ms`).
		self._clock = clock or _now_ms

	def key(self, space: str) -> str:
		"""The Redis key for one space. ``frappe.cache()`` prefixes the site's ``db_name``
		on top of this, so two sites relaying into the same Workspace do **not** share a
		bucket — which is wrong in Google's eyes and right in ours: a staging site must not
		be able to starve production's writes, and Google's own limiter is the backstop."""
		return f"{self.key_prefix}:{(space or '').strip()}"

	def _cache(self):
		# Returns a frappe RedisWrapper. Deliberately unannotated: the return type is a
		# frappe class, and naming it would need a module-scope import of frappe.
		import frappe

		return frappe.cache()

	def _evaluate(self, space: str, cost_ms: int) -> BucketDecision:
		"""One atomic ``.eval()`` of :data:`SPACE_BUCKET_LUA`."""
		cost = max(int(cost_ms), 0)
		ttl_ms = cost + BUCKET_TTL_MARGIN_MS
		reply = self._cache().eval(SPACE_BUCKET_LUA, 1, self.key(space), self._clock(), cost, ttl_ms)
		allowed, wait_ms, next_free_ms = (_as_int(value) for value in reply)
		return BucketDecision(allowed=bool(allowed), wait_ms=max(wait_ms, 0), next_free_ms=next_free_ms)

	def acquire(self, space: str, *, cost_ms: int, block: bool = True) -> BucketDecision:
		"""Claim ``cost_ms`` of this space's budget.

		``block=True`` sleeps out the wait and retries, bounded by ``max_block_ms``; a
		refusal after that bound is returned rather than raised, and the caller defers the
		relay job by pushing ``available_at`` instead. Both behaviours are correct and the
		difference is only latency, because the sweeper — not the queue — is what guarantees
		the write eventually happens.

		Sleeping here is safe in the way sleeping in a web request would not be: this runs
		inside a relay worker that already holds the job's lease, and a room is a strict
		FIFO, so the second it waits is a second nobody else could have used anyway.
		"""
		decision = self._evaluate(space, cost_ms)
		if decision.allowed or not block:
			return decision

		waited = 0
		while waited < self.max_block_ms:
			nap_ms = min(max(decision.wait_ms, _MIN_SLEEP_MS), self.max_block_ms - waited)
			time.sleep(nap_ms / 1000.0)
			waited += nap_ms
			decision = self._evaluate(space, cost_ms)
			if decision.allowed:
				return decision
		return decision

	def peek(self, space: str) -> BucketDecision:
		"""Is this space writable right now, and if not for how long? **Never mutates.**

		Evaluated at zero cost through the same arithmetic rather than a second code path,
		so "peek said yes" and "acquire said yes" can never disagree about the boundary. It
		is still only advisory: another worker may take the second between the peek and the
		acquire, which is precisely why :meth:`acquire` re-evaluates atomically.
		"""
		stored = _as_int(self._cache().get(self.key(space)))
		return bucket_decision(self._clock(), stored, 0)


class ProjectQuota:
	"""Fixed-window counters for Google's per-project, per-60-second buckets.

	One counter per category (see :data:`BUCKET_MESSAGE_WRITES` and friends), because the
	buckets are independent and merging them would waste roughly four fifths of the API.
	Same import discipline as :class:`SpaceRateLimiter`: ``frappe`` inside the methods only.
	"""

	def __init__(
		self,
		*,
		key_prefix: str = "chat:quota",
		window_ms: int = PROJECT_WINDOW_MS,
		clock: Callable[[], int] | None = None,
	) -> None:
		self.key_prefix = key_prefix
		self.window_ms = max(int(window_ms), 1)
		self._clock = clock or _now_ms

	def key(self, bucket: str, now_ms: int) -> str:
		"""Window-scoped, so expiry is the only reset mechanism and no worker has to zero a
		counter — a reset step is a step that can be interrupted halfway."""
		window = fixed_window_index(now_ms, self.window_ms)
		return f"{self.key_prefix}:{(bucket or '').strip()}:{window}"

	def _cache(self):
		# Returns a frappe RedisWrapper. Deliberately unannotated: the return type is a
		# frappe class, and naming it would need a module-scope import of frappe.
		import frappe

		return frappe.cache()

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		"""Charge ``cost`` against ``bucket``; ``True`` if it fitted inside ``limit``.

		``limit`` is passed in on every call rather than held on the instance because the
		live values are ``Chat Settings`` fields an operator can change while the workers
		are running, and a limiter that caches them makes that change take a restart to
		apply — at exactly the moment somebody is trying to throttle a runaway.

		A refusal is **not** an error and must not be logged as one: it means "come back in
		the next window", and the caller defers the job by ``available_at``. The counter is
		left untouched by a refusal (see :func:`quota_decision`).

		``ttl`` is two windows, so a counter written at the very end of a window still
		outlives the window it belongs to and cannot be read as belonging to the next one.
		"""
		now = self._clock()
		reply = self._cache().eval(
			PROJECT_QUOTA_LUA,
			1,
			self.key(bucket, now),
			max(int(cost), 0),
			int(limit),
			self.window_ms * 2,
		)
		allowed = _as_int(reply[0]) if reply else 0
		return bool(allowed)

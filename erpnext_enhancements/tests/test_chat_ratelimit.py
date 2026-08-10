"""Bench-free tests for the Google Chat rate-limit arithmetic.

Subject: the **pure half** of ``erpnext_enhancements.chat.sync.ratelimit`` —
:func:`bucket_decision` (the per-space GCRA), :func:`quota_decision` and
:func:`fixed_window_index` (the per-project fixed windows). Redis is deliberately **not**
tested here: it would need a server, which would put this suite in the tier this repo
cannot run (``CLAUDE.md``: no Frappe integration-test job), and the arithmetic is the part
that can be wrong in a way nobody notices.

The relationship between the two halves is the thing to protect. The Python function is
**the specification**; the Lua script is **the deployment of it**. Nothing forces them to
agree at runtime, so two structural guards stand in for a live comparison: the script may
issue no Redis command outside the allowlist its arithmetic needs, and it must contain the
same branch — anything else is arithmetic that exists in production and was never tested.

Why the arithmetic matters at all, given that the transport retries on 429 anyway: it does
not exist to make 429s impossible — Google publishes two caveats that make that
unachievable — it exists so that a room draining 600 messages after an outage does so at
one write per second instead of hammering a space until Chat's own hidden limits engage.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_ratelimit.py -q`` step in CI.
"""

from __future__ import annotations

import ast
import itertools
import pathlib
import re

from erpnext_enhancements.chat.sync import ratelimit
from erpnext_enhancements.chat.sync.ratelimit import bucket_decision, quota_decision

APP_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]
RATELIMIT_PY: pathlib.Path = APP_DIR / "chat" / "sync" / "ratelimit.py"


def _module_level_import_roots(path: pathlib.Path) -> set[str]:
	"""Top-level packages imported at **module scope** only, ignoring function bodies."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	roots: set[str] = set()
	for node in tree.body:
		if isinstance(node, ast.Import):
			roots.update(alias.name.split(".")[0] for alias in node.names)
		elif isinstance(node, ast.ImportFrom) and node.level == 0:
			roots.add((node.module or "").split(".")[0])
	return roots


def _lua_commands(script: str) -> set[str]:
	"""Every Redis command the script issues."""
	return set(re.findall(r"redis\.call\('([A-Z]+)'", script))


# --- the import discipline that keeps this suite runnable -----------------------


def test_frappe_is_never_imported_at_module_scope() -> None:
	"""The whole reason the arithmetic is testable.

	``SpaceRateLimiter`` and ``ProjectQuota`` need ``frappe.cache()``, and they import it
	**inside their methods**. One ``import frappe`` at the top of the file would make this
	entire suite uncollectable on the bench-free runner, and the failure would look like an
	unrelated CI break rather than a deleted test tier.
	"""
	roots = _module_level_import_roots(RATELIMIT_PY)
	assert (
		"frappe" not in roots and "requests" not in roots
	), f"{RATELIMIT_PY.name} imports {sorted(roots)} at module scope"
	assert roots <= {"__future__", "time", "collections", "dataclasses", "typing"}


def test_frappe_is_actually_imported_somewhere_inside_the_methods() -> None:
	"""Prove the test above is not passing because the Redis half was quietly deleted."""
	source = RATELIMIT_PY.read_text(encoding="utf-8")
	assert source.count("import frappe") >= 2, (
		"the Redis-backed classes no longer import frappe; either they were removed or they "
		"grew a module-scope import that the previous test would have caught differently"
	)


# --- the per-space bucket -------------------------------------------------------


def test_the_costs_are_the_published_ones() -> None:
	"""One write per second per space; ``media.upload`` shares that bucket with
	``messages.create``, so an attachment message costs two seconds."""
	assert ratelimit.SPACE_WRITE_COST_MS == 1000
	assert ratelimit.UPLOAD_WRITE_COST_MS == 2000


def test_an_untouched_bucket_allows_immediately() -> None:
	"""``next_free_ms = 0`` is what a missing Redis key decodes to, so this is the very
	first write to a space and it must not wait."""
	decision = bucket_decision(1_000_000, 0, 1000)
	assert decision.allowed is True
	assert decision.wait_ms == 0
	assert decision.next_free_ms == 1_001_000


def test_a_second_write_in_the_same_second_is_refused_with_the_remaining_wait() -> None:
	decision = bucket_decision(1_000_200, 1_001_000, 1000)
	assert decision.allowed is False
	assert decision.wait_ms == 800
	assert decision.next_free_ms == 1_001_000


def test_the_boundary_instant_is_allowed() -> None:
	"""``next_free <= now``, not ``<``. At exactly the watermark the second has elapsed, and
	a ``<`` would silently halve the throughput of every space forever."""
	decision = bucket_decision(1_001_000, 1_001_000, 1000)
	assert decision.allowed is True
	assert decision.next_free_ms == 1_002_000

	assert bucket_decision(1_000_999, 1_001_000, 1000).allowed is False


def test_a_refusal_never_reserves() -> None:
	"""The watermark comes back unchanged, so a worker that is told to wait and then dies
	has burned nothing. Reserving would be fairer in a queueing sense and strictly worse
	here: the slot would be held for a worker the sweeper only notices when its lease
	expires."""
	for wait in (1, 250, 999):
		decision = bucket_decision(1_000_000, 1_000_000 + wait, 1000)
		assert decision.allowed is False
		assert decision.next_free_ms == 1_000_000 + wait
		assert decision.wait_ms == wait


def test_there_is_no_burst_allowance() -> None:
	"""Ten writes offered at once take ten seconds; nine of them are refused.

	Zero burst tolerance is deliberate. Any allowance would put two ``messages.create``
	calls in the same second on one space, which is exactly what Google's limit forbids —
	and a room is a strict FIFO anyway, so there is no parallelism a burst could recover.
	"""
	now = 5_000_000
	watermark = 0
	allowed = 0
	for _ in range(10):
		decision = bucket_decision(now, watermark, ratelimit.SPACE_WRITE_COST_MS)
		if decision.allowed:
			allowed += 1
			watermark = decision.next_free_ms
	assert allowed == 1


def test_a_draining_room_sustains_exactly_one_write_per_second() -> None:
	"""The number the ADR puts in front of a human: 600 messages take 600 seconds, in order.

	Simulated by advancing the clock to whatever the bucket says and counting: sixty
	messages must occupy sixty seconds, not fifty-nine and not sixty-one.
	"""
	clock = 1_000_000
	watermark = 0
	start = clock
	for _ in range(60):
		decision = bucket_decision(clock, watermark, ratelimit.SPACE_WRITE_COST_MS)
		if not decision.allowed:
			clock += decision.wait_ms
			decision = bucket_decision(clock, watermark, ratelimit.SPACE_WRITE_COST_MS)
		assert decision.allowed
		watermark = decision.next_free_ms
	assert clock - start == 59_000, "the first write is free; the other 59 cost a second each"


def test_an_attachment_message_costs_two_seconds_of_the_space_budget() -> None:
	decision = bucket_decision(2_000_000, 0, ratelimit.UPLOAD_WRITE_COST_MS)
	assert decision.next_free_ms == 2_002_000
	assert bucket_decision(2_001_500, decision.next_free_ms, 1000).wait_ms == 500


def test_a_zero_cost_call_is_gated_but_does_not_advance_the_watermark() -> None:
	"""Used by ``peek``: the same arithmetic at zero cost, so "peek said yes" and "acquire
	said yes" can never disagree about where the boundary is."""
	free = bucket_decision(3_000_000, 0, 0)
	assert free.allowed is True
	assert free.next_free_ms == 3_000_000

	busy = bucket_decision(3_000_000, 3_000_500, 0)
	assert busy.allowed is False
	assert busy.wait_ms == 500


def test_a_negative_cost_is_clamped_rather_than_rewinding_the_bucket() -> None:
	"""A negative cost would move the watermark *backwards* and hand out free writes.
	Clamped, not raised: this is on the hot path of a background job."""
	decision = bucket_decision(4_000_000, 0, -5000)
	assert decision.allowed is True
	assert decision.next_free_ms == 4_000_000


def test_the_function_is_total_over_an_exhaustive_grid() -> None:
	"""Every combination of the interesting magnitudes, including the ones a clock step
	produces. No exception, no negative wait, and the two invariants that define the
	function: allowed exactly when the bucket is free, and the watermark only ever moves
	forward on an allowed call.
	"""
	interesting = (-10_000, -1, 0, 1, 999, 1000, 1001, 1_000_000, 2**40)
	for now, watermark, cost in itertools.product(interesting, repeat=3):
		decision = bucket_decision(now, watermark, cost)
		assert decision.allowed == (watermark <= now), (now, watermark, cost)
		assert decision.wait_ms >= 0, (now, watermark, cost)
		if decision.allowed:
			assert decision.next_free_ms >= now
			assert decision.next_free_ms == now + max(cost, 0)
		else:
			assert decision.next_free_ms == watermark
			assert decision.wait_ms == watermark - now


def test_a_backwards_clock_step_does_not_hand_out_writes() -> None:
	"""If NTP steps the clock back, the stored watermark is in the "future" and every write
	waits — the safe direction. The forward-step case (a watermark far ahead) is bounded by
	the key's TTL rather than by this function, which is why the TTL exists at all."""
	decision = bucket_decision(1_000_000, 1_060_000, 1000)
	assert decision.allowed is False
	assert decision.wait_ms == 60_000
	assert ratelimit.BUCKET_TTL_MARGIN_MS > 0


# --- the Lua deployment must be the same arithmetic -----------------------------


def test_the_space_script_issues_only_the_commands_the_arithmetic_needs() -> None:
	"""Read one value, conditionally write it back with an expiry. Anything else — a
	``DEL``, a second key, a publish — is behaviour that exists only in production and was
	never tested, because the specification above cannot see it."""
	assert _lua_commands(ratelimit.SPACE_BUCKET_LUA) == {"GET", "SET"}


def test_the_space_script_contains_the_same_branch_as_the_python() -> None:
	"""``next_free <= now`` — the same comparison, in the same direction. A ``<`` here and a
	``<=`` in Python would halve throughput in production and pass every test in this file
	except this one."""
	script = ratelimit.SPACE_BUCKET_LUA
	assert "if next_free <= now then" in script
	assert "return {1, 0, advanced}" in script
	assert "return {0, next_free - now, next_free}" in script
	assert "advanced = now + cost" in script


def test_the_space_script_uses_one_key_and_three_arguments() -> None:
	"""A second ``KEYS`` entry would mean the bucket is no longer one integer per space, and
	the ``.eval()`` call site passes ``1`` as the key count."""
	script = ratelimit.SPACE_BUCKET_LUA
	assert set(re.findall(r"KEYS\[(\d+)\]", script)) == {"1"}
	assert set(re.findall(r"ARGV\[(\d+)\]", script)) == {"1", "2", "3"}


def test_the_space_script_sets_an_expiry_on_every_write() -> None:
	"""The TTL is the clock-skew fuse: a watermark written far in the future by an NTP step
	would otherwise wedge that space until real time caught up."""
	assert "'PX', ttl" in ratelimit.SPACE_BUCKET_LUA


def test_the_quota_script_issues_only_the_commands_the_arithmetic_needs() -> None:
	assert _lua_commands(ratelimit.PROJECT_QUOTA_LUA) == {"GET", "INCRBY", "PEXPIRE"}


def test_the_quota_script_refuses_before_it_increments() -> None:
	"""Same ordering as :func:`quota_decision`, and for the same reason: incrementing first
	lets a rejected charge poison the rest of the window."""
	script = ratelimit.PROJECT_QUOTA_LUA
	assert "if limit <= 0 or current + cost > limit then" in script
	assert script.index("return {0, current}") < script.index("INCRBY")


def test_the_quota_script_re_arms_the_expiry_on_every_charge() -> None:
	"""Not "only on the first increment". A crash between ``INCRBY`` and ``EXPIRE`` in that
	shape leaves a counter with no TTL, which never resets and refuses the bucket forever."""
	script = ratelimit.PROJECT_QUOTA_LUA
	assert script.index("INCRBY") < script.index("PEXPIRE")
	assert "if total == cost" not in script


# --- the per-project fixed windows ----------------------------------------------


def test_a_charge_that_fits_is_allowed_and_counted() -> None:
	assert quota_decision(0, 1, 3000) == ratelimit.QuotaDecision(allowed=True, count=1)
	assert quota_decision(2999, 1, 3000) == ratelimit.QuotaDecision(allowed=True, count=3000)


def test_exactly_reaching_the_limit_is_allowed() -> None:
	"""The boundary again: the limit is a maximum, and refusing at it wastes the last unit
	of every window, forever."""
	assert quota_decision(0, 3000, 3000).allowed is True


def test_one_over_the_limit_is_refused() -> None:
	assert quota_decision(3000, 1, 3000).allowed is False
	assert quota_decision(0, 3001, 3000).allowed is False


def test_a_refusal_leaves_the_counter_untouched() -> None:
	"""The load-bearing detail. "Increment then compare" lets one oversized charge push the
	counter permanently past the limit, so every subsequent smaller charge is refused too
	and the window never recovers."""
	refused = quota_decision(2999, 5, 3000)
	assert refused.allowed is False
	assert refused.count == 2999
	# …and the window is still usable for something that does fit.
	assert quota_decision(2999, 1, 3000).allowed is True


def test_a_zero_limit_refuses_everything() -> None:
	"""A configured stop, not a disabled check. Reading zero as "unlimited" is the failure
	direction that gets an API client banned."""
	assert quota_decision(0, 1, 0).allowed is False
	assert quota_decision(0, 0, 0).allowed is False


def test_a_zero_cost_charge_is_always_allowed_when_the_limit_is_positive() -> None:
	assert quota_decision(3000, 0, 3000).allowed is True
	assert quota_decision(0, -5, 3000) == ratelimit.QuotaDecision(allowed=True, count=0)


def test_quota_decision_is_total_over_an_exhaustive_grid() -> None:
	values = (-1, 0, 1, 59, 60, 299, 300, 3000, 10**9)
	for current, cost, limit in itertools.product(values, repeat=3):
		decision = quota_decision(current, cost, limit)
		seen, price = max(current, 0), max(cost, 0)
		expected = limit > 0 and seen + price <= limit
		assert decision.allowed is expected, (current, cost, limit)
		assert decision.count == (seen + price if expected else seen)


def test_the_window_advances_once_per_minute() -> None:
	"""Fixed windows, and the boundary is exact: 59.999 s is still window 0."""
	assert ratelimit.PROJECT_WINDOW_MS == 60_000
	assert ratelimit.fixed_window_index(0, 60_000) == 0
	assert ratelimit.fixed_window_index(59_999, 60_000) == 0
	assert ratelimit.fixed_window_index(60_000, 60_000) == 1
	assert ratelimit.fixed_window_index(3_600_000, 60_000) == 60


def test_a_zero_window_cannot_divide_by_zero() -> None:
	assert ratelimit.fixed_window_index(1_000, 0) == 1_000


def test_the_fixed_window_boundary_burst_is_a_known_and_bounded_trade() -> None:
	"""A fixed window permits up to 2× the limit across a boundary. Pinned as a *known*
	property rather than discovered later: it is acceptable only because the per-space GCRA
	paces every write — ~20 rooms at 1/second is a ceiling of 1,200 message writes per
	minute against Google's 3,000 — so the project quota is a runaway-loop net, not the
	thing that shapes normal traffic. If either number moves, revisit this.
	"""
	limit = 10
	quota = ratelimit.ProjectQuota()
	# One millisecond apart, two different counters — so a full `limit` may be spent on
	# each side of the boundary and 2 x limit lands inside one real minute.
	assert quota.key("message_writes", 59_999) != quota.key("message_writes", 60_000)
	assert quota_decision(0, limit, limit).allowed is True


def test_the_project_buckets_are_separate_names() -> None:
	"""Google's per-project limits are independent counters per category, not one pool;
	merging them under-uses the API roughly fivefold."""
	names = {
		ratelimit.BUCKET_MESSAGE_WRITES,
		ratelimit.BUCKET_MESSAGE_READS,
		ratelimit.BUCKET_MEMBERSHIP_WRITES,
		ratelimit.BUCKET_SPACE_WRITES,
		ratelimit.BUCKET_ATTACHMENT_WRITES,
		ratelimit.BUCKET_SUBSCRIPTION_WRITES,
	}
	assert len(names) == 6, "two project buckets share a name and would share a counter"


# --- the key shapes -------------------------------------------------------------


def test_the_space_key_is_per_space() -> None:
	"""Constructed without touching Redis. ``frappe.cache()`` adds the site's ``db_name`` on
	top, so a staging site cannot spend production's budget."""
	limiter = ratelimit.SpaceRateLimiter()
	assert limiter.key("spaces/AAAA") == "chat:space_write:spaces/AAAA"
	assert limiter.key(" spaces/AAAA ") == limiter.key("spaces/AAAA")


def test_the_quota_key_carries_the_window_so_expiry_is_the_only_reset() -> None:
	"""No worker ever has to zero a counter, and a reset step is a step that can be
	interrupted halfway."""
	quota = ratelimit.ProjectQuota()
	assert quota.key("message_writes", 0).endswith(":0")
	assert quota.key("message_writes", 60_000).endswith(":1")
	assert quota.key("message_writes", 0) != quota.key("message_reads", 0)


def test_a_blocking_acquire_is_bounded() -> None:
	"""A blocking acquire must never pin a worker indefinitely — ``gchat/backoff.py``'s
	lesson, where a 300-second cap would have held a relay worker for twenty minutes on one
	429 storm. Past the bound the refusal is returned and the job is deferred by
	``available_at``, which costs nothing because the sweeper is the delivery guarantee."""
	assert ratelimit.DEFAULT_MAX_BLOCK_MS <= 10_000
	assert ratelimit.SpaceRateLimiter().max_block_ms == ratelimit.DEFAULT_MAX_BLOCK_MS
	assert ratelimit.SpaceRateLimiter(max_block_ms=-1).max_block_ms == 0

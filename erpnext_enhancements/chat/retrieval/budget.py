# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The token ceiling and the degradation ladder. **Pure — imports nothing but the stdlib.**

Zero imports is not tidiness. This repo has no Frappe integration-test job, so anything that
needs a bench is verified by a human or not at all; keeping the arithmetic in an
import-free module moves it into the tier that runs on every push.

--------------------------------------------------------------------------------------
The ceiling is a cost decision, not a model limit
--------------------------------------------------------------------------------------

40,000 tokens of retrieved chat context — excluding the system prompt and tool definitions.
The model's window is far larger. The ceiling exists because cost and latency scale with
input tokens on *every* ``@triton`` mention, and because recall past a few tens of thousands
of tokens is not free either. It is a settings field, the realised count is logged on every
turn, and the intended way to retune it is two weeks of that column rather than an argument.

``T0`` neither borrows nor lends. Its size must stay stable for the prompt cache, so unused
budget flows **T1 → T2 → T3** and stops there.

Note that the ADR's own five tier defaults sum to 46,000 against a 40,000 ceiling. That is
not an error: ``T1 + T2 + T3 + reserve`` is exactly 40,000, and T0 is the cacheable prefix
that the flowing pool does not include. ``Chat Settings`` validates it that way, with the
other reading available behind a flag.

--------------------------------------------------------------------------------------
The ladder is ordered, and rungs 4 and 5 are a promise to the user
--------------------------------------------------------------------------------------

1. drop the lowest-ranked **T3** chunks;
2. drop the lowest-ranked **T2** chunks, but keep at least the top three;
3. replace remaining T2 chunk bodies with their room-digest lines;
4. compress **T1** — thread root plus the last N verbatim, middle replaced by the thread
   digest;
5. **hard floor** — the system prefix plus the last 20 messages of the current thread,
   truncated oldest-first.

**When rung 4 or 5 fires, ``context_truncated`` is set and Triton is instructed to say its
view was cut.** That is a correctness feature rather than a UX nicety: a model answering
silently from a cut view produces a *confident wrong answer*, and the user gets no signal at
all. Rungs 1–3 do not set it, because dropping the least relevant chunks is retrieval working
as designed rather than information being withheld.

T1 never drops below its floor. The current thread is the one thing the asking person can see
with their own eyes, so an answer that has lost it is not degraded, it is broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The ADR's tier split, as the fallback when a settings value is missing or nonsensical.
#: Named constants rather than inline numbers so a drift between here and the DocType JSON
#: is a diff rather than a hunt.
DEFAULT_CEILING: int = 40_000
DEFAULT_T0_STABLE: int = 6_000
DEFAULT_T1_THREAD: int = 18_000
DEFAULT_T1_FLOOR: int = 4_000
DEFAULT_T2_CROSS: int = 14_000
DEFAULT_T3_AUTHORED: int = 6_000
DEFAULT_RESERVE: int = 2_000

#: Rung 2 keeps at least this many cross-room chunks. Below three, "we searched your other
#: rooms" stops being true in any useful sense and the answer silently becomes
#: thread-only — which reads to the user exactly like the retrieval never ran.
T2_MINIMUM_KEPT: int = 3

#: Rung 5's floor, in messages.
HARD_FLOOR_MESSAGES: int = 20

#: Rungs at or above which the user is told. See the module docstring.
TRUNCATION_ANNOUNCED_FROM: int = 4

TIER_T0: str = "T0"
TIER_T1: str = "T1"
TIER_T2: str = "T2"
TIER_T3: str = "T3"

#: The order the ladder consumes tiers in. Fixed, and asserted by test rather than trusted:
#: a ladder that drops T1 before T3 is a ladder that throws away the thread the question was
#: asked in while keeping a chunk from six months ago.
LADDER_ORDER: tuple[str, ...] = (TIER_T3, TIER_T2, TIER_T1)


@dataclass(frozen=True)
class Budget:
	"""The resolved token allowances for one turn."""

	ceiling: int = DEFAULT_CEILING
	t0_stable: int = DEFAULT_T0_STABLE
	t1_thread: int = DEFAULT_T1_THREAD
	t1_floor: int = DEFAULT_T1_FLOOR
	t2_cross: int = DEFAULT_T2_CROSS
	t3_authored: int = DEFAULT_T3_AUTHORED
	reserve: int = DEFAULT_RESERVE

	@property
	def flowing_pool(self) -> int:
		"""What T1, T2 and T3 share. T0 and the reserve are outside it."""
		return max(self.ceiling - self.reserve, 0)

	def fingerprint(self) -> str:
		"""A stable identity for the cache key.

		Sorted and explicit rather than ``repr(self)``: a dataclass repr is stable *today*
		and is not a contract, and a cache key that changes when Python's formatting does
		is a cache that silently stops hitting.
		"""
		parts = (
			f"c{self.ceiling}",
			f"t0{self.t0_stable}",
			f"t1{self.t1_thread}",
			f"f{self.t1_floor}",
			f"t2{self.t2_cross}",
			f"t3{self.t3_authored}",
			f"r{self.reserve}",
		)
		return "-".join(parts)


@dataclass
class Item:
	"""One budgetable unit: a chunk, a digest line, or a thread message.

	``tokens`` is the count sealed at index time, not recomputed here. That is the risk the
	design accepts and names: a systematically wrong count silently over- or under-fills the
	ceiling and nothing errors either way, which is why every chunk records *how* its count
	was arrived at.
	"""

	key: str
	tier: str
	tokens: int
	score: float = 0.0
	#: Set on a T2 item that has a digest line available, so rung 3 can substitute rather
	#: than drop. Without it, rung 3 is indistinguishable from rung 2.
	digest_key: str | None = None
	digest_tokens: int = 0
	payload: Any = None


@dataclass
class Plan:
	"""What survived, how far the ladder went, and whether the user must be told."""

	kept: list[Item] = field(default_factory=list)
	dropped: list[Item] = field(default_factory=list)
	#: T2 items whose body was swapped for a digest line at rung 3.
	summarised: list[Item] = field(default_factory=list)
	rung: int = 0
	context_truncated: bool = False
	total_tokens: int = 0
	#: Which tiers contributed at least one surviving item, in ladder-independent order.
	tiers_used: tuple[str, ...] = ()

	def token_total(self) -> int:
		return self.total_tokens


def tier_allowance(budget: Budget, tier: str) -> int:
	if tier == TIER_T0:
		return max(budget.t0_stable, 0)
	if tier == TIER_T1:
		return max(budget.t1_thread, 0)
	if tier == TIER_T2:
		return max(budget.t2_cross, 0)
	if tier == TIER_T3:
		return max(budget.t3_authored, 0)
	return 0


def plan(items: list[Item], budget: Budget) -> Plan:
	"""Fit ``items`` under ``budget``, degrading in the fixed order when they do not.

	``items`` arrives in **rank order within each tier** — best first. The ladder drops from
	the back, so the caller's ranking is the thing that decides what is lost, and this
	function never re-scores anything.

	Deterministic in its inputs, which is what makes it testable: the same items and the same
	budget produce the same plan, every rung of it.
	"""
	by_tier: dict[str, list[Item]] = {}
	for item in items:
		by_tier.setdefault(item.tier, []).append(item)

	# Tier-local allowances first. Unused T1/T2/T3 budget flows forward in that order; T0
	# neither borrows nor lends, because its size is the prompt cache's stable prefix.
	kept: dict[str, list[Item]] = {}
	dropped: list[Item] = []

	spare = 0
	for tier in (TIER_T0, TIER_T1, TIER_T2, TIER_T3):
		candidates = by_tier.get(tier, [])
		allowance = tier_allowance(budget, tier)
		if tier != TIER_T0:
			allowance += spare
		taken, left_over, unspent = _take_while_fits(candidates, allowance)
		kept[tier] = taken
		dropped.extend(left_over)
		spare = unspent if tier != TIER_T0 else 0

	result = Plan()
	rung = 0

	# Rungs 1 and 2 are already expressed by _take_while_fits having dropped from the back
	# of T3 and T2. Recording WHICH rung that was is what makes the ladder observable, and
	# an unobservable ladder is one nobody can tell fired.
	if any(item.tier == TIER_T3 for item in dropped):
		rung = 1
	if any(item.tier == TIER_T2 for item in dropped):
		rung = 2
		kept[TIER_T2] = _keep_minimum(kept[TIER_T2], dropped, TIER_T2, T2_MINIMUM_KEPT)

	total = sum(item.tokens for item in _flatten(kept))

	# Rung 3: substitute digest lines for the T2 bodies still over budget.
	if total > budget.ceiling:
		kept[TIER_T2], summarised, freed = _summarise_t2(kept[TIER_T2])
		if summarised:
			rung = 3
			result.summarised = summarised
			total -= freed

	# Rung 4: compress T1. The floor is enforced here and nowhere else.
	if total > budget.ceiling:
		kept[TIER_T1], compressed_freed = _compress_t1(kept[TIER_T1], budget)
		if compressed_freed:
			rung = 4
			total -= compressed_freed

	# Rung 5: the hard floor. Everything but T0 and the last HARD_FLOOR_MESSAGES of T1.
	if total > budget.ceiling:
		kept, floored = _hard_floor(kept, budget)
		dropped.extend(floored)
		rung = 5
		total = sum(item.tokens for item in _flatten(kept))

	result.kept = _flatten(kept)
	result.dropped = dropped
	result.rung = rung
	result.context_truncated = rung >= TRUNCATION_ANNOUNCED_FROM
	result.total_tokens = total
	result.tiers_used = tuple(tier for tier in (TIER_T0, TIER_T1, TIER_T2, TIER_T3) if kept.get(tier))
	return result


def _flatten(kept: dict[str, list[Item]]) -> list[Item]:
	out: list[Item] = []
	for tier in (TIER_T0, TIER_T1, TIER_T2, TIER_T3):
		out.extend(kept.get(tier, []))
	return out


def _take_while_fits(items: list[Item], allowance: int) -> tuple[list[Item], list[Item], int]:
	"""Take from the front (best-ranked) while the allowance lasts.

	Returns ``(taken, dropped, unspent)``. **Stops at the first item that does not fit
	rather than skipping it** — a smaller lower-ranked item slipping past a larger
	better-ranked one makes the surviving set depend on token counts rather than on
	relevance, and nothing about the result would say so.
	"""
	taken: list[Item] = []
	spent = 0
	for index, item in enumerate(items):
		if spent + item.tokens > allowance:
			return taken, list(items[index:]), max(allowance - spent, 0)
		taken.append(item)
		spent += item.tokens
	return taken, [], max(allowance - spent, 0)


def _keep_minimum(kept: list[Item], dropped: list[Item], tier: str, minimum: int) -> list[Item]:
	"""Rung 2's floor: restore dropped items until ``minimum`` of this tier survive.

	Over the ceiling by construction when it fires, and that is deliberate — rung 3
	immediately replaces those bodies with digest lines, so the pair together keeps *some*
	cross-room evidence at a fraction of the cost. Dropping to zero instead would make the
	answer thread-only while claiming to have searched.
	"""
	if len(kept) >= minimum:
		return kept
	restorable = [item for item in dropped if item.tier == tier]
	needed = minimum - len(kept)
	for item in restorable[:needed]:
		kept.append(item)
		dropped.remove(item)
	return kept


def _summarise_t2(kept: list[Item]) -> tuple[list[Item], list[Item], int]:
	"""Rung 3: swap each T2 body for its digest line, worst-ranked first.

	Only items that actually *have* a digest are substitutable; one without is left alone
	rather than dropped, because rung 3's promise is "less detail", not "less coverage".
	"""
	summarised: list[Item] = []
	freed = 0
	for item in reversed(kept):
		if item.digest_key is None:
			continue
		saving = item.tokens - item.digest_tokens
		if saving <= 0:
			continue
		item.tokens = item.digest_tokens
		item.payload = None  # the body is gone; the caller renders from digest_key
		summarised.append(item)
		freed += saving
	return kept, summarised, freed


def _compress_t1(kept: list[Item], budget: Budget) -> tuple[list[Item], int]:
	"""Rung 4: trim the middle of the thread down towards the floor.

	Keeps the front (the thread root, which is what the question is *about*) and the back
	(the most recent replies, which is what it is *responding to*), and removes from the
	middle. Stops at the floor even if that leaves the plan over the ceiling — the ceiling
	is a cost control and the floor is a correctness one, so when they conflict the floor
	wins and rung 5 handles the rest.
	"""
	floor = max(budget.t1_floor, 0)
	total = sum(item.tokens for item in kept)
	if total <= floor or len(kept) <= 2:
		return kept, 0

	freed = 0
	head, tail = kept[:1], kept[-1:]
	middle = kept[1:-1]
	while middle and total - freed > floor:
		victim = middle.pop(len(middle) // 2)
		freed += victim.tokens
	return head + middle + tail, freed


def _hard_floor(kept: dict[str, list[Item]], budget: Budget) -> tuple[dict[str, list[Item]], list[Item]]:
	"""Rung 5: T0 plus the last :data:`HARD_FLOOR_MESSAGES` of T1, oldest-first truncation.

	Everything else goes. This is the rung that says "the model is answering from almost
	nothing", and it is exactly why ``context_truncated`` exists.
	"""
	floored: list[Item] = []
	for tier in (TIER_T2, TIER_T3):
		floored.extend(kept.get(tier, []))
		kept[tier] = []

	thread = kept.get(TIER_T1, [])
	if len(thread) > HARD_FLOOR_MESSAGES:
		floored.extend(thread[:-HARD_FLOOR_MESSAGES])
		kept[TIER_T1] = thread[-HARD_FLOOR_MESSAGES:]

	# T0 stays whole. It is the pinned document card and the room digests — the cheapest
	# tokens in the assembly and the ones the prompt cache is paying for.
	remaining = sum(item.tokens for item in _flatten(kept))
	while remaining > budget.ceiling and kept.get(TIER_T1):
		victim = kept[TIER_T1].pop(0)
		floored.append(victim)
		remaining -= victim.tokens

	return kept, floored


def budget_from_settings(values: dict[str, Any]) -> Budget:
	"""Build a :class:`Budget` from a plain dict of settings values.

	A dict rather than a ``Chat Settings`` document, so this module stays import-free and
	the caller owns the one line that reads the Single. Missing or non-positive values fall
	back to the ADR defaults rather than to zero: a zero ceiling would refuse every turn,
	and "the settings row was half-written" must not be the same event as "chat is off".
	"""

	def _positive(key: str, fallback: int) -> int:
		try:
			value = int(values.get(key) or 0)
		except (TypeError, ValueError):
			return fallback
		return value if value > 0 else fallback

	return Budget(
		ceiling=_positive("context_token_ceiling", DEFAULT_CEILING),
		t0_stable=_positive("budget_t0_stable", DEFAULT_T0_STABLE),
		t1_thread=_positive("budget_t1_thread", DEFAULT_T1_THREAD),
		t1_floor=_positive("budget_t1_floor", DEFAULT_T1_FLOOR),
		t2_cross=_positive("budget_t2_cross", DEFAULT_T2_CROSS),
		t3_authored=_positive("budget_t3_authored", DEFAULT_T3_AUTHORED),
		reserve=_positive("budget_reserve", DEFAULT_RESERVE),
	)

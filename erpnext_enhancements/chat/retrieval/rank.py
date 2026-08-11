# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hybrid ranking. **Pure — stdlib only, no ``frappe``, no ``numpy``.**

Two tiers answer different questions and neither can answer the other's. The semantic tier
answers "roughly about this"; the lexical tier answers "contains this exact string". A
coworker asking about ``SINV-04412`` wants the second, and no embedding will reliably give it
to them — the number is a handful of tokens with no meaning to the model, sitting in a chunk
that is topically identical to a hundred others.

--------------------------------------------------------------------------------------
Reciprocal Rank Fusion, and why not a weighted sum
--------------------------------------------------------------------------------------

``score(d) = Σ 1 / (k + rank_i(d))`` over the tiers that returned ``d``.

The alternative — normalise both scores and add them with weights — is the thing that looks
more principled and is worse. A cosine similarity lives in roughly ``[0.6, 0.95]`` for
anything plausible; a MariaDB ``MATCH`` relevance score is unbounded and depends on corpus
statistics. They are not on the same scale, they are not on the same scale *from query to
query*, and adding them means whichever tier happens to have the larger numbers today decides
the ranking. Normalising per-query fixes the units and introduces a new failure: a query where
one tier returned a single weak result now has that result scoring 1.0.

RRF fuses **ranks**, which are comparable by construction. It has one parameter, the shape of
its output does not depend on either tier's scoring internals, and a tier returning nothing
degrades to the other tier's order rather than to noise.

--------------------------------------------------------------------------------------
Recency is a half-life, not a cutoff
--------------------------------------------------------------------------------------

An old but exactly-right answer must still be reachable. A cutoff makes "we discussed this
last year" unanswerable; a decay makes it merely outranked by something equally relevant and
newer, which is the actual preference being expressed.

Boosts are multiplicative and small. The participation boost — the asking person was in the
conversation — is the one that matters in practice, because "what did I agree to" is the
commonest question and the person's own messages are the answer. **It is not a permission
input**: membership decides what may be read, and having spoken is neither necessary nor
sufficient for being a member.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: RRF's smoothing constant. Larger flattens the curve, so a rank-1 hit is less dominant.
#: 60 is the published default and is kept because there is no measurement here to justify
#: anything else — an invented constant that looks tuned is worse than a documented borrowed
#: one.
DEFAULT_RRF_K: int = 60

DEFAULT_HALF_LIFE_DAYS: float = 30.0

#: Multiplicative and deliberately modest. A boost large enough to reorder a clearly better
#: match is a boost that makes retrieval feel broken to the person it is trying to help.
BOOST_PARTICIPANT: float = 1.15
BOOST_SAME_ROOM: float = 1.10
BOOST_SAME_THREAD: float = 1.25


@dataclass
class Candidate:
	"""One rankable chunk. Identity is ``key``; everything else is ranking input."""

	key: str
	room: str
	#: Age at ranking time. Computed by the caller, because "now" is the one value that must
	#: never enter a pure function — a clock read here would make two identical assemblies
	#: differ and silently cost the prompt cache on every turn.
	age_days: float = 0.0
	thread_root: str | None = None
	participants: tuple[str, ...] = ()
	tokens: int = 0
	payload: object = None
	#: Filled by :func:`rank`.
	score: float = field(default=0.0, compare=False)


def rrf_scores(
	rank_lists: list[list[str]],
	k: int = DEFAULT_RRF_K,
) -> dict[str, float]:
	"""Fuse several ranked key lists into one score per key.

	Args:
		rank_lists: one list per tier, best first. An empty list contributes nothing, which
			is how a disabled or failed tier degrades to the other one's order rather than
			to noise.
		k: the smoothing constant.

	Ranks are 1-based, and a key appearing twice in one list is scored from its **first**
	appearance — a duplicate is a bug upstream, and scoring it twice would reward it for
	being one.
	"""
	if k < 1:
		k = 1
	scores: dict[str, float] = {}
	for keys in rank_lists:
		seen: set[str] = set()
		for index, key in enumerate(keys):
			if key in seen:
				continue
			seen.add(key)
			scores[key] = scores.get(key, 0.0) + 1.0 / (k + index + 1)
	return scores


def recency_multiplier(age_days: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
	"""``0.5 ** (age / half_life)``, clamped to ``(0, 1]``.

	A negative age — a clock skew, or a message stamped in the future by an origin whose
	clock is wrong — is treated as zero rather than as a boost. Rewarding a bad timestamp is
	how one badly-stamped message ends up at the top of every answer.
	"""
	if half_life_days <= 0:
		return 1.0
	age = max(age_days, 0.0)
	return float(0.5 ** (age / half_life_days))


def boost_for(
	candidate: Candidate,
	*,
	user: str,
	current_room: str | None = None,
	current_thread: str | None = None,
) -> float:
	"""The multiplicative boosts that apply to one candidate."""
	factor = 1.0
	if user and user in candidate.participants:
		factor *= BOOST_PARTICIPANT
	if current_room and candidate.room == current_room:
		factor *= BOOST_SAME_ROOM
	if current_thread and candidate.thread_root and candidate.thread_root == current_thread:
		factor *= BOOST_SAME_THREAD
	return factor


def rank(
	candidates: list[Candidate],
	*,
	semantic_order: list[str],
	lexical_order: list[str],
	user: str,
	current_room: str | None = None,
	current_thread: str | None = None,
	rrf_k: int = DEFAULT_RRF_K,
	half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
	limit: int | None = None,
) -> list[Candidate]:
	"""Fuse, decay, boost, sort. Returns a new list; ``candidates`` is not reordered in place.

	**Ties break on ``key``**, not on input order. Input order comes from a SQL result set,
	which is not a promise — an unstable sort here would make two assemblies of the same
	inputs differ, which costs the prompt cache silently and makes the assembly-order test
	flap for reasons that have nothing to do with the code under test.

	A candidate ranked by neither tier scores 0 and sorts last rather than being dropped:
	the caller decides what to keep, and a ranker that silently discards its own input is a
	ranker whose ``limit`` means two different things.
	"""
	fused = rrf_scores([semantic_order, lexical_order], k=rrf_k)

	ranked: list[Candidate] = []
	for candidate in candidates:
		base = fused.get(candidate.key, 0.0)
		score = (
			base
			* recency_multiplier(candidate.age_days, half_life_days)
			* boost_for(
				candidate,
				user=user,
				current_room=current_room,
				current_thread=current_thread,
			)
		)
		ranked.append(
			Candidate(
				key=candidate.key,
				room=candidate.room,
				age_days=candidate.age_days,
				thread_root=candidate.thread_root,
				participants=candidate.participants,
				tokens=candidate.tokens,
				payload=candidate.payload,
				score=score,
			)
		)

	ranked.sort(key=lambda c: (-c.score, c.key))
	return ranked[:limit] if limit else ranked

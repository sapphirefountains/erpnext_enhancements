"""The pure half of Phase 5 retrieval: chunking, ranking, budgeting, assembly, citations.

Five of the seven retrieval modules import nothing but the stdlib, and that is what makes this
file possible. This repo has no Frappe integration-test job, so anything needing a bench is
verified by a human or not verified at all — keeping the *judgement* in import-free modules
puts it in the tier that runs on every push, and leaves only fetching behind the bench.

What is asserted here is the behaviour whose failure is **silent**:

* a chunker that is not deterministic makes ``content_hash`` meaningless and re-running the
  indexer duplicative rather than idempotent;
* a ranker that adds raw scores instead of fusing ranks lets whichever tier has the larger
  numbers decide the answer;
* a ladder that degrades out of order throws away the current thread while keeping a chunk
  from six months ago;
* an assembly whose stable prefix is not byte-stable costs the entire prompt-cache discount on
  every request, with no error and no log line;
* a boolean-mode query built from raw user text turns ``-20%`` into an *exclusion* and returns
  fewer rows for no stated reason.

None of those raise. All of them look like the feature working.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_retrieval_pure.py -q`` step in CI.
``python -m unittest`` silently collects zero tests from a file shaped like this and reports
success — this repo lost a suite that way for weeks.
"""

from __future__ import annotations

from erpnext_enhancements.chat.indexing import chunker
from erpnext_enhancements.chat.invoke import envelope
from erpnext_enhancements.chat.retrieval import assemble, budget, citations, lexical, rank

# --------------------------------------------------------------------------- chunking


def _messages(count: int, *, tokens: int = 10, gap: float = 1.0, thread: str | None = None):
	return [
		chunker.Message(
			name=f"m{index}",
			seq=index + 1,
			sender="alice" if index % 2 else "bob",
			text=f"message {index}",
			thread_root=thread,
			minutes_since_previous=0.0 if index == 0 else gap,
			tokens=tokens,
		)
		for index in range(count)
	]


def test_chunking_the_same_stream_twice_produces_the_same_chunks() -> None:
	"""Determinism is what makes ``content_hash`` an identity rather than a number."""
	stream = _messages(50)
	first = chunker.chunk_messages(stream)
	second = chunker.chunk_messages(stream)
	assert [(c.first_seq, c.last_seq, c.reason) for c in first] == [
		(c.first_seq, c.last_seq, c.reason) for c in second
	]


def test_a_chunk_seals_on_the_message_ceiling() -> None:
	chunks = chunker.chunk_messages(_messages(45), seal_messages=20, seal_tokens=10_000)
	assert [c.reason for c in chunks[:2]] == [chunker.REASON_MESSAGES, chunker.REASON_MESSAGES]
	assert all(len(c.messages) <= 20 for c in chunks)


def test_a_chunk_seals_on_the_token_ceiling() -> None:
	chunks = chunker.chunk_messages(_messages(20, tokens=200), seal_tokens=1_200, seal_messages=100)
	assert chunks[0].reason == chunker.REASON_TOKENS
	assert chunks[0].tokens <= 1_200


def test_a_long_silence_ends_the_chunk() -> None:
	"""Two hours apart is two conversations even when it is the same two people."""
	stream = _messages(6)
	stream[3] = chunker.Message(
		name="m3", seq=4, sender="bob", text="later", minutes_since_previous=120.0, tokens=10
	)
	chunks = chunker.chunk_messages(stream, gap_minutes=45)
	assert [c.reason for c in chunks if c.sealed] == [chunker.REASON_GAP]
	assert chunks[0].last_seq == 3


def test_a_thread_boundary_ends_the_chunk() -> None:
	"""A reply belongs with its thread, not with whatever preceded it in seq order."""
	stream = _messages(3) + _messages(3, thread="root-1")
	for index, message in enumerate(stream):
		stream[index] = chunker.Message(
			name=f"m{index}",
			seq=index + 1,
			sender=message.sender,
			text=message.text,
			thread_root=message.thread_root,
			minutes_since_previous=1.0,
			tokens=10,
		)
	chunks = chunker.chunk_messages(stream)
	assert chunks[0].reason == chunker.REASON_THREAD
	assert chunks[1].thread_root == "root-1"


def test_the_tail_stays_open_when_the_idle_time_is_unknown() -> None:
	"""``None`` means "we do not know how long it has been quiet", and the safe direction is
	to leave it open: an unsealed tail costs a little recall, a prematurely sealed one costs a
	re-chunk and a wasted embedding call."""
	chunks = chunker.chunk_messages(_messages(5), tail_idle_minutes=None)
	assert chunks[-1].sealed is False


def test_the_tail_seals_once_it_has_been_quiet_long_enough() -> None:
	chunks = chunker.chunk_messages(_messages(5), tail_idle_minutes=31, idle_tail_minutes=30)
	assert chunks[-1].sealed is True
	assert chunks[-1].reason == chunker.REASON_IDLE


def test_the_thread_boundary_wins_when_two_rules_fire_at_once() -> None:
	"""The order the rules are checked in is part of the contract: two identical streams must
	record the same reason, or a cache keyed on it diverges for no visible cause."""
	stream = [
		chunker.Message(name="a", seq=1, sender="bob", text="x", tokens=10),
		chunker.Message(
			name="b", seq=2, sender="bob", text="y", thread_root="t", minutes_since_previous=999, tokens=10
		),
	]
	chunks = chunker.chunk_messages(stream, gap_minutes=45)
	assert chunks[0].reason == chunker.REASON_THREAD


def test_rendered_bodies_carry_the_author() -> None:
	"""'Did Sam agree to that' is unanswerable from bodies alone."""
	chunk = chunker.chunk_messages(_messages(2))[0]
	body = chunker.render_body(chunk)
	assert "bob: message 0" in body
	assert body == chunker.render_body(chunk)


def test_participants_are_sorted_so_the_hash_cannot_flap() -> None:
	chunk = chunker.chunk_messages(_messages(4))[0]
	assert chunk.participants() == sorted(chunk.participants())


# --------------------------------------------------------------------------- ranking


def test_rrf_rewards_agreement_between_the_tiers() -> None:
	"""A chunk both tiers rank highly must beat one only a single tier found."""
	scores = rank.rrf_scores([["a", "b"], ["a", "c"]])
	assert scores["a"] > scores["b"]
	assert scores["a"] > scores["c"]


def test_rrf_degrades_to_the_surviving_tier_rather_than_to_noise() -> None:
	"""One tier disabled or failed is the normal degradation, not an error state."""
	scores = rank.rrf_scores([[], ["a", "b", "c"]])
	assert list(sorted(scores, key=lambda k: -scores[k])) == ["a", "b", "c"]


def test_a_duplicate_in_one_list_is_scored_once() -> None:
	"""A duplicate is a bug upstream; scoring it twice would reward it for being one."""
	once = rank.rrf_scores([["a", "b"]])
	twice = rank.rrf_scores([["a", "a", "b"]])
	assert once["a"] == twice["a"]


def test_recency_is_a_half_life_and_not_a_cutoff() -> None:
	"""An old but exactly-right answer stays reachable, merely outranked."""
	assert rank.recency_multiplier(0) == 1.0
	assert rank.recency_multiplier(30, 30) == 0.5
	assert rank.recency_multiplier(3650, 30) > 0.0


def test_a_future_timestamp_is_not_a_boost() -> None:
	"""Clock skew is real on this deployment. Rewarding a bad timestamp is how one
	badly-stamped message ends up at the top of every answer."""
	assert rank.recency_multiplier(-500, 30) == 1.0


def test_the_participation_boost_applies_to_the_asking_person() -> None:
	candidate = rank.Candidate(key="a", room="r1", participants=("alice",))
	assert rank.boost_for(candidate, user="alice") > 1.0
	assert rank.boost_for(candidate, user="carol") == 1.0


def test_ties_break_on_the_key_so_two_identical_runs_agree() -> None:
	"""Input order comes from a SQL result set, which is not a promise. An unstable sort
	makes two assemblies of the same inputs differ, which costs the cache silently."""
	candidates = [rank.Candidate(key=key, room="r") for key in ("z", "a", "m")]
	ranked = rank.rank(candidates, semantic_order=[], lexical_order=[], user="alice")
	assert [c.key for c in ranked] == ["a", "m", "z"]


def test_an_unranked_candidate_sorts_last_rather_than_disappearing() -> None:
	"""The caller decides what to keep. A ranker that discards its own input is one whose
	``limit`` means two different things."""
	candidates = [rank.Candidate(key="a", room="r"), rank.Candidate(key="b", room="r")]
	ranked = rank.rank(candidates, semantic_order=["b"], lexical_order=[], user="alice")
	assert [c.key for c in ranked] == ["b", "a"]
	assert len(ranked) == 2


def test_ranking_does_not_mutate_its_input() -> None:
	candidates = [rank.Candidate(key="a", room="r"), rank.Candidate(key="b", room="r")]
	rank.rank(candidates, semantic_order=["b"], lexical_order=[], user="alice")
	assert [c.key for c in candidates] == ["a", "b"]
	assert all(c.score == 0.0 for c in candidates)


# --------------------------------------------------------------------------- budgeting


def _items(tier: str, count: int, tokens: int, *, digest_tokens: int = 0):
	return [
		budget.Item(
			key=f"{tier}-{index}",
			tier=tier,
			tokens=tokens,
			digest_key=f"digest:{index}" if digest_tokens else None,
			digest_tokens=digest_tokens,
			payload={"body": "x"},
		)
		for index in range(count)
	]


def test_everything_fits_means_no_degradation_and_no_notice() -> None:
	plan = budget.plan(_items(budget.TIER_T2, 3, 100), budget.Budget())
	assert plan.rung == 0
	assert plan.context_truncated is False
	assert len(plan.kept) == 3


def test_unused_thread_budget_reaches_the_authored_tier_before_anything_is_dropped() -> None:
	"""T1 → T2 → T3, and nothing is dropped while the *pool* still has room.

	Twenty authored items at 1,000 tokens is over T3's own 6,000 allowance and well under the
	38,000 pool, so an implementation that enforced tier allowances without flow would degrade
	here — needlessly, and invisibly, since a shorter answer looks like a shorter answer.
	"""
	items = _items(budget.TIER_T3, 20, 1_000) + _items(budget.TIER_T2, 2, 100)
	plan = budget.plan(items, budget.Budget())
	assert plan.rung == 0
	assert plan.dropped == []


def test_rung_one_drops_the_authored_tier_first() -> None:
	"""T3 before T2 before T1. A ladder that drops T1 first throws away the thread the
	question was asked in while keeping a chunk from six months ago."""
	items = _items(budget.TIER_T3, 60, 1_000) + _items(budget.TIER_T2, 2, 100)
	plan = budget.plan(items, budget.Budget())
	assert plan.rung == 1
	assert {item.tier for item in plan.dropped} == {budget.TIER_T3}
	assert plan.context_truncated is False


def test_rung_two_keeps_a_minimum_of_cross_room_chunks() -> None:
	"""Dropping to zero would make the answer thread-only while claiming to have searched."""
	items = _items(budget.TIER_T2, 40, 1_000)
	plan = budget.plan(items, budget.Budget())
	kept_t2 = [item for item in plan.kept if item.tier == budget.TIER_T2]
	assert len(kept_t2) >= budget.T2_MINIMUM_KEPT
	assert plan.rung >= 2


def test_rungs_one_to_three_do_not_claim_the_view_was_cut() -> None:
	"""Dropping the least relevant chunks is retrieval working as designed. Announcing it
	would train users to discount every answer."""
	plan = budget.plan(_items(budget.TIER_T3, 30, 1_000), budget.Budget())
	assert plan.rung <= 3
	assert plan.context_truncated is False


def test_rung_four_or_five_sets_the_flag_the_user_is_told_about() -> None:
	"""A model answering silently from a cut view produces a confident wrong answer."""
	items = _items(budget.TIER_T1, 60, 1_000) + _items(budget.TIER_T2, 40, 1_000)
	plan = budget.plan(items, budget.Budget(ceiling=5_000, t1_thread=4_000, t1_floor=1_000))
	assert plan.rung >= 4
	assert plan.context_truncated is True


def test_the_thread_tier_never_drops_below_its_floor() -> None:
	"""The current thread is what the asking person can see with their own eyes. An answer
	that has lost it is not degraded, it is broken."""
	resolved = budget.Budget(ceiling=3_000, t1_thread=2_000, t1_floor=1_000, t2_cross=500, t3_authored=100)
	plan = budget.plan(_items(budget.TIER_T1, 40, 100), resolved)
	kept_t1 = sum(item.tokens for item in plan.kept if item.tier == budget.TIER_T1)
	assert kept_t1 >= resolved.t1_floor or plan.rung == 5


def test_rung_three_substitutes_a_digest_rather_than_dropping_coverage() -> None:
	"""Rung 3's promise is 'less detail', not 'less coverage'."""
	items = _items(budget.TIER_T2, 30, 2_000, digest_tokens=50)
	plan = budget.plan(items, budget.Budget(ceiling=6_000, t2_cross=6_000))
	assert plan.rung >= 3
	assert plan.summarised
	assert all(item.tokens == 50 for item in plan.summarised)


def test_a_t2_item_with_no_digest_is_left_alone_at_rung_three() -> None:
	items = _items(budget.TIER_T2, 30, 2_000)
	plan = budget.plan(items, budget.Budget(ceiling=6_000, t2_cross=6_000))
	assert plan.summarised == []


def test_t0_neither_borrows_nor_lends() -> None:
	"""Its size must stay stable for the prompt cache, so a large T1 must not eat into it."""
	items = _items(budget.TIER_T0, 2, 1_000) + _items(budget.TIER_T1, 100, 1_000)
	plan = budget.plan(items, budget.Budget())
	assert len([item for item in plan.kept if item.tier == budget.TIER_T0]) == 2


def test_unused_budget_flows_forward_from_t1() -> None:
	"""An empty thread should let the cross-room tier use the room."""
	plan = budget.plan(_items(budget.TIER_T2, 30, 1_000), budget.Budget())
	kept = [item for item in plan.kept if item.tier == budget.TIER_T2]
	assert len(kept) > budget.Budget().t2_cross // 1_000


def test_taking_stops_at_the_first_item_that_does_not_fit() -> None:
	"""A smaller lower-ranked item slipping past a larger better-ranked one makes the
	surviving set depend on token counts rather than on relevance, silently."""
	items = [
		budget.Item(key="big", tier=budget.TIER_T2, tokens=13_995),
		budget.Item(key="small", tier=budget.TIER_T2, tokens=10),
	]
	# Asserted against the taking helper rather than through plan(), deliberately: rung 2's
	# minimum-kept floor would restore the small item anyway, so a whole-plan assertion here
	# would pass for the wrong reason and keep passing if the stop-versus-skip rule broke.
	taken, dropped, unspent = budget._take_while_fits(items, 14_000)
	assert [item.key for item in taken] == ["big"]
	assert [item.key for item in dropped] == ["small"]
	assert unspent == 5


def test_a_half_written_settings_row_falls_back_rather_than_refusing_every_turn() -> None:
	"""A zero ceiling would refuse every turn, and 'the settings row was half-written' must
	not be the same event as 'chat is off'."""
	resolved = budget.budget_from_settings({"context_token_ceiling": 0, "budget_t1_thread": None})
	assert resolved.ceiling == budget.DEFAULT_CEILING
	assert resolved.t1_thread == budget.DEFAULT_T1_THREAD


def test_the_budget_fingerprint_is_stable_and_explicit() -> None:
	"""``repr`` of a dataclass is stable today and is not a contract. A cache key that
	changes when Python's formatting does is a cache that silently stops hitting."""
	assert budget.Budget().fingerprint() == budget.Budget().fingerprint()
	assert budget.Budget(ceiling=1).fingerprint() != budget.Budget(ceiling=2).fingerprint()


# --------------------------------------------------------------------------- assembly


def _assembly(**overrides):
	kwargs = {
		"system_prompt": "you are triton",
		"glossary_lines": ["fountain = the thing with water"],
		"user_card_lines": ["alice, service manager"],
		"t0_lines": ["[room summary: r1] they discussed the pump"],
		"t2_t3_lines": ["⟦ref:1⟧ bob (2026-08-01): the pump is fine"],
		"thread_lines": ["⟦ref:2⟧ alice (2026-08-02): is it?"],
		"question": "is the pump fine",
	}
	kwargs.update(overrides)
	return assemble.assemble(**kwargs)


def test_the_segments_are_in_the_one_permitted_order() -> None:
	built = _assembly()
	assert [segment.name for segment in built.segments] == list(assemble.SEGMENT_ORDER)


def test_the_stable_prefix_is_byte_identical_across_two_assemblies() -> None:
	"""Rule 1. A single volatile character at the front invalidates the entire prefix and
	silently costs the full discount on every request."""
	assert _assembly().stable_prefix() == _assembly().stable_prefix()


def test_the_stable_prefix_does_not_change_when_only_the_question_changes() -> None:
	assert _assembly().stable_prefix() == _assembly(question="something else").stable_prefix()


def test_the_stable_prefix_changes_when_a_digest_republishes() -> None:
	"""The named trigger. A prefix that did NOT change here would serve the old summary."""
	other = _assembly(t0_lines=["[room summary: r1] they replaced the pump"])
	assert _assembly().stable_prefix() != other.stable_prefix()


def test_the_truncation_notice_goes_on_the_volatile_turn() -> None:
	"""Putting it in the stable prefix would change the cached prefix on exactly the turns
	that are already the most expensive."""
	built = _assembly(context_truncated=True)
	assert assemble.TRUNCATION_NOTICE not in built.stable_prefix()
	assert assemble.TRUNCATION_NOTICE in built.volatile_suffix()
	assert built.stable_prefix() == _assembly().stable_prefix()


def test_the_question_is_last() -> None:
	built = _assembly()
	assert built.text().rstrip().endswith("is the pump fine")


def test_the_toolset_fingerprint_is_order_independent() -> None:
	"""An unordered iteration here would force a cache rebuild on every single turn — the
	failure the whole ordering discipline exists to avoid, arriving through the back door."""
	assert assemble.toolset_fingerprint(["b", "a"]) == assemble.toolset_fingerprint(["a", "b"])


def test_the_toolset_fingerprint_changes_when_a_tool_appears() -> None:
	"""A tool appearing, vanishing or changing shape is baked into the cache identity by
	design, so this must be visible rather than silent."""
	assert assemble.toolset_fingerprint(["a"]) != assemble.toolset_fingerprint(["a", "b"])


def test_the_context_cache_key_carries_all_three_watermark_values() -> None:
	"""A key built on max(seq) alone is unchanged by a delete, so the cached context
	containing the deleted message is served again."""
	base = assemble.context_cache_key(
		user="alice", room="r1", watermark=(10, 5, "2026-08-01 10:00:00"), budget_fingerprint="b"
	)
	deleted = assemble.context_cache_key(
		user="alice", room="r1", watermark=(10, 4, "2026-08-01 10:00:00"), budget_fingerprint="b"
	)
	edited = assemble.context_cache_key(
		user="alice", room="r1", watermark=(10, 5, "2026-08-01 11:00:00"), budget_fingerprint="b"
	)
	assert base != deleted
	assert base != edited


def test_the_digest_cache_key_carries_the_version() -> None:
	"""Without it a regenerated summary inherits the key of the one it replaced."""
	assert assemble.digest_cache_key("r1", 1) != assemble.digest_cache_key("r1", 2)


def test_cache_keys_are_namespaced_away_from_the_socket_server() -> None:
	"""``redis_cache`` and ``redis_socketio`` are the same instance on the same database."""
	assert assemble.context_cache_key(
		user="a", room="r", watermark=(1, 1, "t"), budget_fingerprint="b"
	).startswith("triton:")


# --------------------------------------------------------------------------- lexical


def test_a_leading_hyphen_does_not_become_an_exclusion() -> None:
	"""``Where's the -20% discount?`` in boolean mode means *exclude documents containing 20*.
	The query runs, returns fewer rows, and nothing says why."""
	built = lexical.build_boolean_query("where is the -20% discount")
	assert "-20" not in built
	assert "+discount" in built


def test_operator_characters_cannot_break_the_expression() -> None:
	for hostile in ('a" b', "a* b", "a( b", "a~ b", "a@ b", "+++", '"""'):
		built = lexical.build_boolean_query(hostile)
		assert '"' not in built
		assert "(" not in built
		assert "*" not in built


def test_every_surviving_term_is_required() -> None:
	built = lexical.build_boolean_query("pump impeller replacement")
	assert built == "+pump +impeller +replacement"


def test_terms_shorter_than_the_index_minimum_are_dropped_and_reported() -> None:
	"""A two-character term is not indexed and matches nothing — silently. 'No results' and
	'your terms were too short' are different answers."""
	assert "+q4" not in lexical.build_boolean_query("q4 revenue")
	assert lexical.dropped_terms("q4 revenue") == ["q4"]


def test_a_query_with_nothing_searchable_returns_an_empty_expression() -> None:
	"""The caller must run no lexical query at all. An empty AGAINST is a full-corpus read
	wearing a search's clothes."""
	assert lexical.build_boolean_query("a b !!") == ""
	assert lexical.build_boolean_query("") == ""


def test_an_identifier_becomes_a_required_conjunction_of_its_parts() -> None:
	"""``SINV-04412`` is the case the lexical tier exists for, and it works *because* both
	parts are required rather than because the identifier survives whole.

	InnoDB's tokenizer splits the stored body on non-word characters too, so the index holds
	``sinv`` and ``04412`` as separate terms — an intact ``sinv04412`` term would match
	nothing at all. Requiring both is what makes the search find that invoice and not merely
	chunks about invoices.
	"""
	built = lexical.build_boolean_query("what happened with SINV-04412")
	assert "+sinv" in built
	assert "+04412" in built


def test_the_term_count_is_capped() -> None:
	built = lexical.build_boolean_query(" ".join(f"word{index}" for index in range(200)))
	assert built.count("+") <= lexical.MAX_TERMS


def test_tokenising_is_idempotent_and_deduplicated() -> None:
	assert lexical.tokenize("pump pump PUMP") == ["pump"]


# --------------------------------------------------------------------------- citations


def _manifest():
	return citations.build_manifest(
		[
			{"room": "r1", "message": "m1", "label": "bob in r1", "kind": "chunk"},
			{"room": "r2", "message": "m2", "label": "alice in r2", "kind": "message"},
		]
	)


def test_refs_are_one_based_and_follow_assembly_order() -> None:
	"""Numbering by rank instead would have the model citing [[ref:1]] for the third thing
	it read."""
	assert [entry.ref for entry in _manifest()] == [1, 2]


def test_a_cited_ref_that_exists_is_kept() -> None:
	resolvable, missing = citations.split_known_refs("see [[ref:1]]", _manifest())
	assert resolvable == [1]
	assert missing == []


def test_an_invented_ref_is_counted_and_stripped_rather_than_rendered() -> None:
	"""A broken citation must not become a broken page — but a rising miss rate is a PROMPT
	regression signal, so it is counted."""
	manifest = _manifest()
	resolvable, missing = citations.split_known_refs("as [[ref:99]] shows", manifest)
	assert missing == [99]
	assert resolvable == []
	assert "[[ref:99]]" not in citations.strip_unknown_refs("as [[ref:99]] shows", manifest)


def test_stripping_does_not_leave_a_visible_gap() -> None:
	"""The point of dropping silently is that the reader cannot tell."""
	cleaned = citations.strip_unknown_refs("the pump [[ref:99]] is fine", _manifest())
	assert "  " not in cleaned


def test_the_parse_tolerates_the_spacing_a_model_will_eventually_produce() -> None:
	assert citations.cited_refs("[[ref: 3 ]] and [[ ref:4 ]]") == [3, 4]


def test_a_repeated_citation_is_reported_once() -> None:
	assert citations.cited_refs("[[ref:1]] and again [[ref:1]]") == [1]


def test_the_tail_buffer_covers_the_longest_marker() -> None:
	"""A citation split across two SSE frames must render as ONE anchor."""
	assert len("[[ref: 999 ]]") <= citations.MAX_MARKER_LENGTH


def test_the_context_label_and_the_emitted_marker_are_different_shapes() -> None:
	"""So that 'the model echoed the label it was shown' is distinguishable from 'the model
	produced a citation' — the difference between working and appearing to work."""
	line = citations.context_line(_manifest()[0], author="bob", timestamp="t", body="hello")
	assert "⟦ref:1⟧" in line
	assert "[[ref:1]]" not in line


def test_a_relative_route_needs_no_site_origin() -> None:
	assert citations.relative_route("r1", "m1").startswith("/chat/")


def test_the_wire_shape_is_the_one_the_phase_3_renderer_already_reads() -> None:
	"""``k`` and ``type``, not ``ref`` and ``kind``.

	The first version of this module invented its own names, which is the mistake worth
	leaving a test against: the renderer shipped eight days earlier and its ``indexManifest``
	**discards** an entry whose ``k`` is not a positive integer — so the mismatch would not
	have raised anything. Every citation would have been a miss and every marker stripped, and
	the answer would have rendered as prose with no numbers in it.

	The keys are re-asserted against the renderer's own source in the wire-contract section
	further down; this row pins the payload itself.
	"""
	payload = _manifest()[0].as_dict()
	for key in ("k", "type", "label", "url", "room", "message"):
		assert key in payload
	assert payload["k"] == 1


# --------------------------------------------------------------------------- the envelope
#
# Locked decision #4 says a mention behaves identically from the native Chat client and from
# the SPA. The strongest form of that assertion needs a bench — two real payloads through two
# real normalisers — so what is asserted here is the half that makes the bench half almost
# redundant: the envelope has no origin field, so there is nothing for the handler to branch
# on. A reviewer cannot miss a branch that cannot be written.


def test_the_envelope_carries_no_origin() -> None:
	"""The structural guarantee behind decision #4, and the reason it is structural: 'the
	handler must not branch on origin' is a rule somebody has to keep, while 'there is no
	origin here' is a fact."""
	fields = set(envelope.Envelope("u", "r", "m", "t").as_job_kwargs())
	assert "origin" not in fields
	assert "space" not in fields
	assert "gchat_message" not in fields


def test_two_envelopes_differing_only_in_transport_are_byte_identical() -> None:
	"""``transport`` is precisely the part that legitimately differs between origins — a
	Google space name exists on one path and not the other. Including it in the canonical
	form would make the identity test impossible to satisfy, and the natural fix would be to
	delete the test."""
	from_chat = envelope.Envelope(
		"alice", "room-1", "msg-1", "what is the status", seq=7, transport={"space": "spaces/x"}
	)
	from_spa = envelope.Envelope("alice", "room-1", "msg-1", "what is the status", seq=7)
	assert from_chat.canonical() == from_spa.canonical()
	assert from_chat.fingerprint() == from_spa.fingerprint()


def test_a_different_question_is_a_different_envelope() -> None:
	a = envelope.Envelope("alice", "room-1", "msg-1", "status")
	b = envelope.Envelope("alice", "room-1", "msg-1", "budget")
	assert a.canonical() != b.canonical()


def test_the_request_id_is_derived_so_a_redelivery_is_one_turn() -> None:
	"""Generating a UUID would make idempotency a property of the transport — and the
	transport is Google's interaction webhook, which is at-least-once by design."""
	first = envelope.derive_request_id("r", "m", "what is the status")
	second = envelope.derive_request_id("r", "m", "what is the status")
	assert first == second


def test_editing_the_mention_makes_it_a_new_question() -> None:
	"""Keying on the message alone would mean editing '@triton what is the status' to
	'@triton what is the budget' returns the first answer, from the cache, forever."""
	before = envelope.derive_request_id("r", "m", "what is the status")
	after = envelope.derive_request_id("r", "m", "what is the budget")
	assert before != after


def test_the_mention_token_is_removed_and_the_question_survives() -> None:
	assert envelope.strip_mention("@triton what is the status") == "what is the status"


def test_removing_the_mention_leaves_no_double_space_or_orphaned_punctuation() -> None:
	"""Cosmetic on its own — but this string is what the model is asked AND what the audit
	row's query hash is computed over, so two spellings of the same question would hash
	differently and read as two different reads."""
	assert envelope.strip_mention("hey @triton, status?") == "hey, status?"
	assert envelope.strip_mention("so @triton what now") == "so what now"


def test_only_the_first_mention_is_removed() -> None:
	"""'ask @triton about the @triton rollout' means the second one literally, and a blanket
	replace would turn the question into nonsense."""
	assert envelope.strip_mention("ask @triton about the @triton rollout") == (
		"ask about the @triton rollout"
	)


def test_a_longer_word_starting_with_the_handle_is_not_a_mention() -> None:
	assert envelope.strip_mention("@tritonics is a company") == "@tritonics is a company"


def test_an_envelope_from_another_release_is_refused_rather_than_reinterpreted() -> None:
	"""A deploy FLUSHDBs the queue, so this is belt and braces — but the field that changed
	is the one nobody would notice."""
	kwargs = envelope.Envelope("u", "r", "m", "t").as_job_kwargs()
	kwargs["version"] = envelope.ENVELOPE_VERSION + 1
	try:
		envelope.from_job_kwargs(kwargs)
	except ValueError as exc:
		assert "version" in str(exc)
	else:  # pragma: no cover - the assertion is the raise
		raise AssertionError("an envelope from another release was accepted")


def test_the_round_trip_through_job_kwargs_is_lossless() -> None:
	original = envelope.Envelope("alice", "r", "m", "q", thread_root="t", seq=3, request_id="x")
	assert envelope.from_job_kwargs(original.as_job_kwargs()) == original


# ------------------------------------------------------- the citation wire contract
#
# The Python manifest and the JavaScript renderer are two halves of one contract written
# eight days apart, and a mismatch between them **fails silently in the worst possible way**:
# `indexManifest` discards any entry whose `k` is not a positive integer, so an entry keyed on
# anything else does not error — it vanishes. Every citation becomes a miss, every marker is
# stripped from the answer, and what the reader sees is prose with no numbers in it and no
# indication that there were supposed to be any.
#
# So the keys are asserted against the renderer's own source rather than against a copy of it
# here. A test carrying its own idea of the contract passes forever while the two sides drift.

import pathlib
import re

_CITATIONS_JS = pathlib.Path(__file__).resolve().parents[1] / "public" / "js" / "chat" / "citations.js"


def _js_source() -> str:
	return _CITATIONS_JS.read_text(encoding="utf-8")


def test_the_renderer_this_contract_is_written_against_exists() -> None:
	"""Not vacuous: every assertion below reads that file."""
	assert _CITATIONS_JS.is_file(), f"{_CITATIONS_JS} is missing"
	assert "indexManifest" in _js_source()


def test_the_manifest_is_keyed_on_k_because_the_renderer_indexes_on_k() -> None:
	"""``indexManifest`` reads ``entry.k`` and DISCARDS anything else. An entry keyed on
	``ref`` would not raise — it would disappear, and so would every citation."""
	assert "Number(entry.k)" in _js_source(), (
		"public/js/chat/citations.js no longer indexes on `k`. Whatever it indexes on now is "
		"what Citation.as_dict must emit, or the manifest silently renders as no citations "
		"at all."
	)
	assert "k" in _manifest_entry()


def test_the_manifest_carries_type_because_the_renderer_switches_on_type() -> None:
	assert "citation.type" in _js_source()
	assert "type" in _manifest_entry()


def test_every_type_the_python_side_emits_is_one_the_renderer_knows() -> None:
	"""``LINKABLE`` is the renderer's allowlist; anything outside it renders as a flat pill.
	That is the correct fate for a digest and the *wrong* fate for a message citation, which
	would silently stop being clickable."""
	source = _js_source()
	linkable = set(re.findall(r'"([a-z_]+)"', source.split("const LINKABLE")[1].split(")")[0]))
	assert citations.TYPE_CHAT_MESSAGE in linkable, (
		f"the renderer no longer treats {citations.TYPE_CHAT_MESSAGE!r} as linkable, so every "
		"message citation would render as a dead pill — visible, and not clickable, with "
		"nothing explaining why."
	)
	assert citations.TYPE_DIGEST not in linkable, (
		"the renderer now treats a digest as linkable. A digest has no single canonical "
		"message to point at, so the link would be a guess presented as a source."
	)


def test_a_chunk_citation_points_at_a_real_message() -> None:
	"""A chunk is a run of messages, so it cites the first of them. The reader lands on text
	they can check rather than on a synthetic object with no address."""
	entry = _manifest_entry(kind="chunk", message="MSG-1")
	assert entry["type"] == citations.TYPE_CHAT_MESSAGE
	assert entry["message"] == "MSG-1"


def test_a_digest_citation_is_typed_as_a_digest() -> None:
	assert _manifest_entry(kind="digest")["type"] == citations.TYPE_DIGEST


def test_an_unknown_kind_degrades_to_a_message_citation_rather_than_vanishing() -> None:
	"""An unmapped kind must not produce a type the renderer drops. Degrading to the common
	case costs a slightly wrong icon; producing an unknown type costs the citation."""
	assert _manifest_entry(kind="something-new")["type"] == citations.TYPE_CHAT_MESSAGE


def test_the_manifest_carries_no_snippet() -> None:
	"""The renderer shows a snippet in the tooltip if it is there. A snippet is message text,
	and the manifest is stored on Triton Invocation Log — which is content-free by
	construction. The label says who and which room; clicking is what shows the message,
	under the full permission check."""
	assert "snippet" not in _manifest_entry()


def _manifest_entry(*, kind: str = "message", message: str | None = "MSG-1") -> dict:
	return citations.build_manifest(
		[{"room": "R1", "message": message, "label": "Jane in #riverwalk", "kind": kind}]
	)[0].as_dict()


# --------------------------------------------------------------------------- database causes


class _OperationalError(Exception):
	"""Shaped like the driver's: ``args = (errno, message)``."""


def test_a_plain_exception_is_still_just_its_class() -> None:
	assert envelope.db_cause(ValueError("boom")) == "ValueError"


def test_a_database_error_carries_its_errno() -> None:
	"""A number cannot contain a message body, which is the whole argument for printing it.

	Production spent an afternoon on ``retrieval failed: OperationalError`` — every ``@triton``
	turn dying before the model, with the reason discarded at the line that logged it.
	``OperationalError`` covers a missing column, a missing table, a missing FULLTEXT index, a
	collation mismatch and a lock wait; those want five different responses and the errno
	separates them exactly.
	"""
	assert envelope.db_cause(_OperationalError(1305, "PROCEDURE does not exist")) == (
		"_OperationalError(1305)"
	)


def test_a_schema_errno_also_carries_the_drivers_message() -> None:
	"""At that point the message names a column, not a row."""
	detail = envelope.db_cause(_OperationalError(1054, "Unknown column 'origin_timestamp' in 'field list'"))
	assert "1054" in detail
	assert "origin_timestamp" in detail


def test_a_duplicate_entry_never_carries_its_message() -> None:
	"""1062's absence from the allowlist is the load-bearing part of the design.

	A duplicate-entry error quotes the offending **value**, and in this package a value is
	somebody's message. Asserted rather than left to a comment, because the fix for the next
	unhelpful error will be to add an errno to that set.
	"""
	said = "the thing a coworker actually said"
	detail = envelope.db_cause(_OperationalError(1062, f"Duplicate entry '{said}' for key 'x'"))
	assert detail == "_OperationalError(1062)"
	assert said not in detail
	assert 1062 not in envelope.SCHEMA_ERRNOS
	assert 1406 not in envelope.SCHEMA_ERRNOS


def test_a_long_schema_message_is_truncated() -> None:
	assert len(envelope.db_cause(_OperationalError(1054, "x" * 900))) <= 240


# --------------------------------------------------------------------------- citation flattening


def test_a_citation_marker_flattens_to_a_bracketed_number() -> None:
	"""``[[ref:7]]`` → ``[7]`` for a surface with no chip and no manifest.

	Google Chat received the raw marker and displayed ``[[ref:25]]`` verbatim, which reads as a
	bug in the bot rather than as a citation. ``[25]`` is what the SPA's chip shows, so the two
	surfaces now agree.
	"""
	assert citations.flatten_refs("errors [[ref:25]], [[ref:12]].") == "errors [25], [12]."


def test_it_tolerates_the_spacing_the_model_actually_produces() -> None:
	"""Same tolerance as ``REF_PATTERN`` itself — a model will write ``[[ref: 3 ]]`` eventually."""
	assert citations.flatten_refs("a [[ref: 3 ]] b") == "a [3] b"


def test_text_with_no_markers_is_returned_unchanged() -> None:
	for text in ("no refs here", "", "brackets [25] already flat", "[[notaref:1]]"):
		assert citations.flatten_refs(text) == text


def test_flattening_keeps_the_sentence_intact() -> None:
	"""The judgement call, asserted so it is not silently reversed later.

	Stripping gives cleaner prose but *"errors [[ref:25]], [[ref:12]]"* becomes *"errors ,"* —
	these sentences are written around the markers. ``strip_unknown_refs`` exists for the other
	job (a marker that resolves to nothing) and is deliberately not what the relay uses.
	"""
	flattened = citations.flatten_refs("running tests [[ref:25]] and hitting errors [[ref:12]]")
	assert "[[" not in flattened
	assert flattened.count("[") == 2
	assert "running tests [25] and hitting errors [12]" == flattened

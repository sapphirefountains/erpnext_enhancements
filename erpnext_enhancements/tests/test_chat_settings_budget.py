"""Bench-free tests for the Chat Settings budget/retention arithmetic.

The subject is ``erpnext_enhancements.chat.doctype.chat_settings.chat_settings_rules``,
which deliberately imports **nothing** — not ``frappe``, not the controller next door. That
is the whole reason these numbers can be tested at all: this repo has no Frappe
integration-test job, so anything requiring a bench is verified by a human or not verified.
Keeping the arithmetic in a zero-import module moves it into the tier that actually runs on
every push.

Two things here are worth knowing before changing anything.

**The T0 exclusion.** ADR §I.8 fixes the ceiling at 40,000 and the split at
T0 6,000 / T1 18,000 / T2 14,000 / T3 6,000 / reserve 2,000. Those five sum to 46,000 —
6,000 *over* the ceiling — while ``T1 + T2 + T3 + reserve`` is exactly 40,000. T0 is the
prompt-cacheable stable prefix that "neither borrows nor lends"; the ceiling governs the
pool that flows. Enforcing the naive reading would make the ADR's own defaults invalid on
the first save, which would fail ``bench migrate`` the moment ``default_chat_settings.py``
seeds the Single. ``include_t0_in_ceiling`` exposes the other reading for the day a human
adjudicates it; both are pinned below so neither can drift unnoticed.

**Boundary direction.** Exactly equal to the ceiling is **valid**. The defaults sit on that
boundary, so an off-by-one in the comparison operator does not fail somewhere obscure — it
fails on the shipped configuration. That is the single most valuable assertion in this file.

Plain pytest functions (no ``TestCase``), so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_settings_budget.py -q`` step in CI.
``python -m unittest`` silently collects zero tests from a file shaped like this and reports
success — this repo lost a suite that way for weeks.
"""

from __future__ import annotations

import ast
import json
import pathlib

from erpnext_enhancements.chat.doctype.chat_settings import chat_settings_rules as rules

APP_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]
RULES_PY: pathlib.Path = (
	APP_DIR / "chat" / "doctype" / "chat_settings" / "chat_settings_rules.py"
)
SETTINGS_JSON: pathlib.Path = APP_DIR / "chat" / "doctype" / "chat_settings" / "chat_settings.json"

#: The documented defaults, in the positional order ``validate_budgets`` takes them:
#: ceiling, t0, t1, t1_floor, t2, t3, reserve.
DEFAULTS: tuple[int, int, int, int, int, int, int] = (
	rules.ADR_CONTEXT_TOKEN_CEILING,
	rules.ADR_BUDGET_T0_STABLE,
	rules.ADR_BUDGET_T1_THREAD,
	rules.ADR_BUDGET_T1_FLOOR,
	rules.ADR_BUDGET_T2_CROSS,
	rules.ADR_BUDGET_T3_AUTHORED,
	rules.ADR_BUDGET_RESERVE,
)


def _json_default(fieldname: str) -> int:
	"""The ``default`` declared on one Chat Settings field, as an int."""
	data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
	for field in data.get("fields") or []:
		if field.get("fieldname") == fieldname:
			return int(field.get("default"))
	raise AssertionError(f"Chat Settings declares no field {fieldname!r} ({SETTINGS_JSON})")


# --- the module stays importable without a bench ------------------------------


def test_the_rules_module_imports_nothing() -> None:
	"""Zero imports is the property that keeps this suite in the tier that runs.

	The moment somebody adds ``import frappe`` for a ``_()`` call or a ``flt``, this file
	stops being collectable on a CI runner with no bench and the budget arithmetic goes
	back to being unverified. Translate in the controller, not here.
	"""
	tree = ast.parse(RULES_PY.read_text(encoding="utf-8"), filename=str(RULES_PY))
	imports = [
		ast.unparse(node)
		for node in ast.walk(tree)
		if isinstance(node, ast.Import | ast.ImportFrom)
	]
	assert not imports, (
		f"{RULES_PY.name} must import nothing at all, found: {imports}. "
		"It is the bench-free half of Chat Settings on purpose — see this test's docstring."
	)


# --- the shipped defaults ------------------------------------------------------


def test_the_documented_defaults_are_a_valid_configuration() -> None:
	"""40000 ceiling; 6000/18000/14000/6000 tiers, 4000 floor, 2000 reserve.

	If this ever fails, ``default_chat_settings.py`` cannot seed the Single and
	``bench migrate`` fails on deploy — the validation would reject the values the same
	commit ships as defaults.
	"""
	assert rules.validate_budgets(*DEFAULTS) == [], (
		"the ADR §I.8 default budget split is rejected by its own validator: "
		f"{rules.validate_budgets(*DEFAULTS)}"
	)


def test_the_defaults_match_the_doctype_json() -> None:
	"""The constants and the DocType JSON are two copies of the same seven numbers.

	Nothing else notices when one is edited and the other is not. The symptom would be a
	Single that seeds with values its own ``validate()`` was never checked against.
	"""
	pairs = (
		("context_token_ceiling", rules.ADR_CONTEXT_TOKEN_CEILING),
		("budget_t0_stable", rules.ADR_BUDGET_T0_STABLE),
		("budget_t1_thread", rules.ADR_BUDGET_T1_THREAD),
		("budget_t1_floor", rules.ADR_BUDGET_T1_FLOOR),
		("budget_t2_cross", rules.ADR_BUDGET_T2_CROSS),
		("budget_t3_authored", rules.ADR_BUDGET_T3_AUTHORED),
		("budget_reserve", rules.ADR_BUDGET_RESERVE),
	)
	drift = [
		f"{fieldname}: JSON default {_json_default(fieldname)} != constant {constant}"
		for fieldname, constant in pairs
		if _json_default(fieldname) != constant
	]
	assert not drift, "chat_settings.json and chat_settings_rules.py disagree: " + "; ".join(drift)


def test_the_defaults_sit_exactly_on_the_ceiling() -> None:
	"""T1 + T2 + T3 + reserve == the ceiling, exactly. The boundary is the shipped state.

	This is why the comparison must be ``pool > ceiling`` and not ``pool >= ceiling``: a
	``>=`` would reject the ADR's own numbers.
	"""
	pool = (
		rules.ADR_BUDGET_T1_THREAD
		+ rules.ADR_BUDGET_T2_CROSS
		+ rules.ADR_BUDGET_T3_AUTHORED
		+ rules.ADR_BUDGET_RESERVE
	)
	assert pool == rules.ADR_CONTEXT_TOKEN_CEILING, (
		f"the flowing pool is {pool} against a ceiling of {rules.ADR_CONTEXT_TOKEN_CEILING}. "
		"If the ADR's split changed, update the constants and this assertion together — but know "
		"that the defaults sitting exactly on the boundary is what makes the off-by-one test below "
		"meaningful."
	)


# --- the ceiling rule ----------------------------------------------------------


def test_tier_budgets_plus_reserve_within_the_ceiling_pass() -> None:
	assert rules.validate_budgets(40_000, 6_000, 10_000, 4_000, 8_000, 4_000, 1_000) == []


def test_exactly_equal_to_the_ceiling_is_valid() -> None:
	"""The boundary, stated as its own test so the operator cannot be "tidied" to >=."""
	assert rules.validate_budgets(100, 10, 40, 10, 30, 20, 10) == [], (
		"a pool exactly equal to the ceiling must be accepted — the shipped defaults are that case"
	)


def test_one_token_over_the_ceiling_is_rejected() -> None:
	errors = rules.validate_budgets(100, 10, 41, 10, 30, 20, 10)
	assert len(errors) == 1, f"expected exactly one error, got {errors}"


def test_exceeding_the_ceiling_says_what_is_wrong_and_by_how_much() -> None:
	"""A validation error an operator cannot act on is a validation error that gets bypassed.

	The message must name the pool total, the ceiling, and which numbers were added — a bare
	"invalid configuration" leaves the person guessing which of six fields to change.
	"""
	errors = rules.validate_budgets(40_000, 6_000, 20_000, 4_000, 14_000, 6_000, 2_000)
	assert len(errors) == 1, f"expected exactly one error, got {errors}"
	message = errors[0]
	assert "42000" in message, f"the message must state the pool total: {message!r}"
	assert "40000" in message, f"the message must state the ceiling: {message!r}"
	assert "T1 + T2 + T3 + reserve" in message, (
		f"the message must name which tiers were summed, so the operator knows T0 is excluded: {message!r}"
	)
	assert "Lower a tier or raise the ceiling" in message, (
		f"the message must say what to do about it: {message!r}"
	)


def test_t0_is_excluded_from_the_ceiling_by_default() -> None:
	"""The T0 exclusion, pinned. See this file's docstring for why it is not the naive rule."""
	assert rules.validate_budgets(*DEFAULTS) == []
	assert rules.validate_budgets(*DEFAULTS, include_t0_in_ceiling=True), (
		"with include_t0_in_ceiling=True the ADR defaults sum to 46,000 against a 40,000 ceiling "
		"and MUST be rejected — if this passes, the flag is not wired to anything"
	)


def test_include_t0_names_the_tiers_it_summed() -> None:
	errors = rules.validate_budgets(*DEFAULTS, include_t0_in_ceiling=True)
	assert len(errors) == 1, f"expected exactly one error, got {errors}"
	assert "T0 + T1 + T2 + T3 + reserve = 46000" in errors[0], errors[0]


def test_a_single_tier_larger_than_the_ceiling_is_reported_on_its_own() -> None:
	"""One oversized tier is a different mistake from an oversized sum, and reads differently."""
	errors = rules.validate_budgets(1_000, 0, 5_000, 0, 0, 0, 0)
	assert any("T1 Thread Budget (5000)" in e and "1000" in e for e in errors), errors


# --- the floor rule ------------------------------------------------------------


def test_t1_floor_above_t1_thread_is_rejected() -> None:
	errors = rules.validate_budgets(40_000, 6_000, 8_000, 9_000, 14_000, 6_000, 2_000)
	assert any("T1 Floor" in e for e in errors), errors
	floor_error = next(e for e in errors if "T1 Floor" in e)
	assert "9000" in floor_error and "8000" in floor_error, (
		f"the message must show both numbers so the operator can see which to move: {floor_error!r}"
	)


def test_t1_floor_equal_to_t1_thread_is_allowed() -> None:
	"""``budget_t1_floor <= budget_t1_thread`` — equal means "T1 never degrades", not an error."""
	assert rules.validate_budgets(40_000, 6_000, 18_000, 18_000, 14_000, 6_000, 2_000) == []


def test_t1_floor_is_checked_independently_of_the_ceiling() -> None:
	"""Both rules can fail at once, and both must be reported — not just the first."""
	errors = rules.validate_budgets(100, 0, 40, 90, 40, 40, 40)
	assert any("T1 Floor" in e for e in errors), errors
	assert any("exceed the Context Token Ceiling" in e for e in errors), errors


# --- zero and negative ---------------------------------------------------------


def test_zero_tiers_are_valid() -> None:
	"""A disabled tier is 0, not a validation error — the tier toggles are separate fields."""
	assert rules.validate_budgets(40_000, 0, 0, 0, 0, 0, 0) == []


def test_a_zero_ceiling_is_rejected_with_exactly_one_error() -> None:
	"""Reporting six consequential errors would hide the one real one.

	Every remaining rule compares against the ceiling, so once the ceiling is meaningless
	the function stops and says the single useful thing.
	"""
	errors = rules.validate_budgets(0, 6_000, 18_000, 4_000, 14_000, 6_000, 2_000)
	assert len(errors) == 1, f"a zero ceiling must produce one error, not a cascade: {errors}"
	assert "greater than zero" in errors[0], errors[0]


def test_a_negative_ceiling_reports_both_the_sign_and_the_zero_rule() -> None:
	errors = rules.validate_budgets(-1, 0, 0, 0, 0, 0, 0)
	assert len(errors) == 2, errors
	assert "cannot be negative" in errors[0], errors
	assert "greater than zero" in errors[1], errors


def test_a_negative_tier_is_rejected() -> None:
	"""A negative tier would *satisfy* the sum check while breaking the assembler downstream.

	That is the whole reason the sign check runs first: ``18000 + 14000 + 6000 + (-5000)``
	is comfortably under the ceiling and completely wrong.
	"""
	errors = rules.validate_budgets(40_000, 6_000, 18_000, 4_000, 14_000, 6_000, -5_000)
	assert any("Reserve cannot be negative" in e for e in errors), errors


def test_every_negative_input_is_named_not_just_the_first() -> None:
	errors = rules.validate_budgets(40_000, -1, -2, -3, -4, -5, -6)
	negatives = [e for e in errors if "cannot be negative" in e]
	assert len(negatives) == 6, f"expected all six tiers named, got {negatives}"


def test_the_error_order_is_stable() -> None:
	"""The order is part of the contract — the controller joins these into one throw."""
	errors = rules.validate_budgets(40_000, 0, 10, 20, 0, 0, 0)
	assert errors == [
		"T1 Floor (20) cannot exceed T1 Thread Budget (10). "
		"The floor is the size T1 is guaranteed to keep when the ladder degrades; "
		"a floor above the budget means T1 can never be satisfied."
	], errors


def test_floats_are_accepted_and_reported_faithfully() -> None:
	"""The signature takes ``float`` — a Frappe Int field arrives as a Python number, and a
	Float field would arrive as a float. The message must not lie about what was entered."""
	errors = rules.validate_budgets(100.0, 0.0, 60.5, 0.0, 40.0, 0.0, 0.0)
	assert len(errors) == 1, errors
	assert "100.5" in errors[0], errors[0]


# --- Phase 5: the retrieval and indexing dials --------------------------------
#
# Only the *incoherent combinations* are tested, because only those are what the rule
# refuses. A dial being unusual is the operator's business; a dial that silently disables a
# feature because it contradicts another dial is what a form has to catch, and every case
# below was chosen because its symptom is "the feature appears to work".


def _shipped_retrieval_kwargs(**overrides) -> dict:
	"""The dials exactly as ``chat_settings.json`` ships them, with overrides applied.

	Read from the JSON rather than restated here, which is the point: a test carrying its
	own copy of the defaults passes forever while the shipped configuration drifts away
	from it. The one assertion that matters most in this section is that the SHIPPED set
	is valid, and that is only true if these numbers come from the file the site installs.
	"""
	shipped = {
		"retrieval_top_k": _json_default("retrieval_top_k"),
		"max_candidate_chunks": _json_default("max_candidate_chunks"),
		"max_rooms_per_retrieval": _json_default("max_rooms_per_retrieval"),
		"rrf_k": _json_default("rrf_k"),
		"recency_half_life_days": _json_default("recency_half_life_days"),
		"embedding_dim": _json_default("embedding_dim"),
		"chunk_seal_tokens": _json_default("chunk_seal_tokens"),
		"chunk_seal_messages": _json_default("chunk_seal_messages"),
		"chunk_seal_gap_minutes": _json_default("chunk_seal_gap_minutes"),
		"chunk_idle_tail_minutes": _json_default("chunk_idle_tail_minutes"),
		"digest_dirty_message_threshold": _json_default("digest_dirty_message_threshold"),
		"digest_dirty_minutes": _json_default("digest_dirty_minutes"),
		"digest_batch_rooms": _json_default("digest_batch_rooms"),
		"thread_digest_min_messages": _json_default("thread_digest_min_messages"),
		"digest_max_rebuild_failures": _json_default("digest_max_rebuild_failures"),
		"digest_staleness_alert_minutes": _json_default("digest_staleness_alert_minutes"),
		"context_token_ceiling": _json_default("context_token_ceiling"),
		"semantic_tier_enabled": bool(_json_default("semantic_tier_enabled")),
		"lexical_tier_enabled": bool(_json_default("lexical_tier_enabled")),
	}
	shipped.update(overrides)
	return shipped


def test_the_shipped_retrieval_configuration_is_valid() -> None:
	"""The one that would otherwise fail on the first save of an untouched form — and it
	would fail during `bench migrate`, when default_chat_settings seeds the Single."""
	assert rules.validate_retrieval(**_shipped_retrieval_kwargs()) == []


def test_a_top_k_above_the_candidate_cap_is_refused() -> None:
	errors = rules.validate_retrieval(**_shipped_retrieval_kwargs(retrieval_top_k=99_999))
	assert any("can never be filled" in e for e in errors), errors


def test_a_chunk_larger_than_the_whole_ceiling_is_refused() -> None:
	"""The symptom is the interesting part: retrieval looks like it is working and every
	answer is missing its best source, because the chunk that matched cannot fit."""
	errors = rules.validate_retrieval(**_shipped_retrieval_kwargs(chunk_seal_tokens=50_000))
	assert any("too large to fit in any assembly" in e for e in errors), errors


def test_an_idle_tail_longer_than_the_gap_rule_is_refused() -> None:
	"""The gap rule would already have sealed the chunk, so the idle rule never fires and
	the tail of a quiet room is never indexed at all."""
	errors = rules.validate_retrieval(
		**_shipped_retrieval_kwargs(chunk_idle_tail_minutes=120, chunk_seal_gap_minutes=45)
	)
	assert any("never fires" in e for e in errors), errors


def test_a_staleness_alarm_inside_the_normal_window_is_refused() -> None:
	"""An alert that is always on is worse than no alert: it trains everyone to ignore the
	channel that carries the real one."""
	errors = rules.validate_retrieval(
		**_shipped_retrieval_kwargs(digest_staleness_alert_minutes=10, digest_dirty_minutes=15)
	)
	assert any("always on" in e for e in errors), errors


def test_switching_off_both_tiers_is_refused() -> None:
	"""Neither tier means retrieval returns nothing while appearing to work — Triton would
	answer every chat question from the current thread alone."""
	errors = rules.validate_retrieval(
		**_shipped_retrieval_kwargs(semantic_tier_enabled=False, lexical_tier_enabled=False)
	)
	assert any("return nothing at all" in e for e in errors), errors


def test_switching_off_only_the_semantic_tier_is_allowed() -> None:
	"""The correct degradation when the embedding provider is unavailable: an answer from
	exact matches beats no answer."""
	assert rules.validate_retrieval(**_shipped_retrieval_kwargs(semantic_tier_enabled=False)) == []


def test_zero_rooms_per_retrieval_means_no_cap_and_is_allowed() -> None:
	"""The one dial where 0 is a value rather than a mistake. Every other dial rejects 0,
	because on those it disables the behaviour it sizes."""
	assert rules.validate_retrieval(**_shipped_retrieval_kwargs(max_rooms_per_retrieval=0)) == []
	assert any(
		"greater than zero" in e
		for e in rules.validate_retrieval(**_shipped_retrieval_kwargs(digest_batch_rooms=0))
	)


def test_a_negative_room_cap_is_refused() -> None:
	errors = rules.validate_retrieval(**_shipped_retrieval_kwargs(max_rooms_per_retrieval=-1))
	assert any("Use 0 for no cap" in e for e in errors), errors


def test_the_controller_passes_every_dial_the_rule_takes() -> None:
	"""A dial the controller forgets to pass reads as its default in the rule signature and
	is never validated — the form would then accept an incoherent value that breaks a
	background job hours later. Asserted by AST rather than by running the controller,
	which needs a bench.

	This is the same class of bug as a typo'd fieldname reading as 0 through
	``getattr(..., None) or 0``: nothing errors, and the check silently stops checking.
	"""
	controller = (
		APP_DIR / "chat" / "doctype" / "chat_settings" / "chat_settings.py"
	).read_text(encoding="utf-8")
	tree = ast.parse(controller)

	call = None
	for node in ast.walk(tree):
		if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "validate_retrieval":
			call = node
	assert call is not None, "chat_settings.py no longer calls validate_retrieval at all"

	passed = {kw.arg for kw in call.keywords if kw.arg}
	expected = set(_shipped_retrieval_kwargs())
	assert passed == expected, (
		f"the controller passes {sorted(passed)} but the rule takes {sorted(expected)}. "
		f"Missing: {sorted(expected - passed)}; unexpected: {sorted(passed - expected)}."
	)

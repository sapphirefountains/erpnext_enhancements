"""Bench-free unit tests for the Training watched-seconds engine.

Stubs a minimal ``frappe`` (no site/bench) so ``training.progress`` runs under
plain unittest. Installed in ``setUpModule`` (execution time), not at import, so
it never fools the bench-only suites' ``import frappe`` skip-guards — same shape
as ``test_training_gcs_media.py``.

This suite is deliberately larger than the module it covers. The interval
arithmetic decides whether somebody passed a compliance course, and every one of
its failure modes is silent: a merge that loses a range under-credits a learner
who did the work, a merge that over-unions credits one who did not, and either
way what shows up is a percentage that looks perfectly plausible. There is no
crash to notice. So the maths is tested against hand-computed expectations
rather than against the implementation's own output.

Three properties get the most attention:

  * **the union is a union** — touching, overlapping, nested and out-of-order
    ranges collapse to exactly the seconds occupied, once each;
  * **the cap over-credits rather than drops** — compaction closes the narrowest
    gaps, so total coverage can only rise; a learner is never quietly penalised
    for scrubbing;
  * **the server clock wins** — a payload claiming more new seconds than wall
    time allows is truncated, not believed.

Run: python -m unittest erpnext_enhancements.tests.test_training_progress
"""

import copy
import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

progress = None

STATE = {}

FIXED_NOW = datetime.datetime(2026, 8, 1, 12, 0, 0)


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _Cache:
	"""frappe.cache() stand-in that round-trips values through a copy.

	The copy matters. Real Redis pickles, so a caller mutating what it got back
	does *not* silently mutate what is stored — and a test whose stub handed out
	live references would pass even if ``save()`` never wrote anything.
	"""

	def __init__(self):
		self.store = {}

	def get_value(self, key):
		if key not in self.store:
			return None
		return copy.deepcopy(self.store[key])

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = copy.deepcopy(value)

	def delete_value(self, key):
		self.store.pop(key, None)


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"now": FIXED_NOW,
			"rows": {},
			"writes": [],
			"errors": [],
			"commits": 0,
			"cache": _Cache(),
			"columns": {"progress_json", "integrity_flags"},
			"settings": {
				"heartbeat_interval_seconds": 15,
				"progress_flush_seconds": 60,
				"max_intervals_per_video": 200,
				"attempt_idle_timeout_minutes": 120,
			},
		}
	)


def _advance(seconds):
	STATE["now"] = STATE["now"] + datetime.timedelta(seconds=seconds)


def _cint(value=0, *args, **kwargs):
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def _flt(value=0, *args, **kwargs):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict()
	frappe.session = _Dict(user="learner@example.com")
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a[0] if a else "")
	frappe.get_traceback = lambda: "traceback"
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.cache = lambda: STATE["cache"]

	def _parse_json(value):
		import json as _json

		return _json.loads(value) if isinstance(value, str) else value

	frappe.parse_json = _parse_json
	frappe.get_cached_doc = lambda *a, **k: _Dict(STATE["settings"])

	def _get_value(doctype, name, fieldname=None, **kwargs):
		row = STATE["rows"].get(name)
		if row is None:
			return None
		if isinstance(fieldname, list | tuple):
			return _Dict({f: row.get(f) for f in fieldname})
		return row.get(fieldname)

	def _set_value(doctype, name, fieldname, value=None, update_modified=True, **kwargs):
		row = STATE["rows"].setdefault(name, {})
		changes = fieldname if isinstance(fieldname, dict) else {fieldname: value}
		row.update(changes)
		STATE["writes"].append({"name": name, "changes": dict(changes), "update_modified": update_modified})

	def _commit():
		STATE["commits"] += 1

	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		set_value=_set_value,
		has_column=lambda doctype, column: column in STATE["columns"],
		commit=_commit,
		exists=lambda *a, **k: None,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = _cint
	utils.flt = _flt
	utils.now_datetime = lambda: STATE["now"]
	frappe.utils = utils
	frappe.__dict__["_"] = lambda s: s

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global progress
	_reset_state()
	_install_frappe_stub()
	from erpnext_enhancements.training import progress as module

	progress = module


def _writes_of(attempt="ATT-0001", field="progress_json"):
	return [w for w in STATE["writes"] if w["name"] == attempt and field in w["changes"]]


# ---------------------------------------------------------------- interval maths


class TestMergeIntervalsUnion(unittest.TestCase):
	"""A union, computed by hand and compared. Every case here is one a real
	player produces: ``timeupdate`` fires irregularly, a seek reorders the list,
	and a re-watch nests inside what is already there."""

	def setUp(self):
		_reset_state()

	def test_touching_ranges_become_one(self):
		self.assertEqual(progress.merge_intervals([[0, 10]], [[10, 20]], 600), [[0, 20]])

	def test_overlapping_ranges_are_not_double_counted(self):
		merged = progress.merge_intervals([[0, 10]], [[5, 15]], 600)
		self.assertEqual(merged, [[0, 15]])
		self.assertEqual(progress.coverage(merged, 600), 15 / 600)

	def test_a_nested_range_changes_nothing(self):
		self.assertEqual(progress.merge_intervals([[0, 100]], [[10, 20]], 600), [[0, 100]])

	def test_out_of_order_input_is_sorted(self):
		self.assertEqual(
			progress.merge_intervals([], [[50, 60], [0, 10], [30, 40]], 600),
			[[0, 10], [30, 40], [50, 60]],
		)

	def test_a_range_repeated_verbatim_earns_nothing_extra(self):
		"""Re-watching is allowed; being paid twice for it is not."""
		merged = progress.merge_intervals([[0, 60]], [[0, 60]], 600)
		self.assertEqual(merged, [[0, 60]])

	def test_many_fragments_of_one_continuous_watch_collapse(self):
		fragments = [[i, i + 1] for i in range(0, 300)]
		self.assertEqual(progress.merge_intervals([], fragments, 600), [[0, 300]])

	def test_empty_inputs_are_fine(self):
		self.assertEqual(progress.merge_intervals([], [], 600), [])
		self.assertEqual(progress.merge_intervals(None, None, 600), [])


class TestMergeIntervalsAdjacency(unittest.TestCase):
	"""The one-second rule. Sub-second jitter in ``timeupdate`` otherwise shreds a
	continuous viewing into fragments differing only by rounding."""

	def setUp(self):
		_reset_state()

	def test_a_one_second_gap_closes(self):
		self.assertEqual(progress.merge_intervals([[0, 10]], [[11, 20]], 600), [[0, 20]])

	def test_a_two_second_gap_stays_open(self):
		self.assertEqual(progress.merge_intervals([[0, 10]], [[12, 20]], 600), [[0, 10], [12, 20]])

	def test_closing_a_gap_credits_the_gap(self):
		"""Documented consequence, asserted so nobody 'fixes' it later: the second
		between 10 and 11 was never watched and is credited anyway."""
		merged = progress.merge_intervals([[0, 10]], [[11, 20]], 600)
		self.assertEqual(progress.coverage(merged, 20), 1.0)


class TestMergeIntervalsBounds(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_beyond_the_duration_is_clipped(self):
		self.assertEqual(progress.merge_intervals([], [[0, 500]], 100), [[0, 100]])

	def test_entirely_beyond_the_duration_is_dropped(self):
		self.assertEqual(progress.merge_intervals([], [[200, 300]], 100), [])

	def test_a_negative_start_is_clipped_to_zero(self):
		self.assertEqual(progress.merge_intervals([], [[-50, 10]], 600), [[0, 10]])

	def test_a_wholly_negative_range_is_dropped(self):
		self.assertEqual(progress.merge_intervals([], [[-50, -10]], 600), [])

	def test_zero_length_and_reversed_ranges_are_dropped(self):
		self.assertEqual(progress.merge_intervals([], [[10, 10], [20, 10]], 600), [])

	def test_unverified_duration_does_not_clip(self):
		"""Duration 0 means 'not probed yet', not 'zero seconds long'. Clipping to
		it would throw away every range until the probe lands."""
		self.assertEqual(progress.merge_intervals([], [[0, 900]], 0), [[0, 900]])

	def test_fractional_seconds_are_truncated(self):
		self.assertEqual(progress.merge_intervals([], [[0.9, 10.9]], 600), [[0, 10]])

	def test_malformed_pairs_are_dropped_not_raised(self):
		"""These arrive from a browser. One bad pair must not cost the learner the
		rest of the beat."""
		junk = [None, "nonsense", [1], {}, ["a", "b"], [], 7]
		self.assertEqual(progress.merge_intervals([[0, 10]], junk, 600), [[0, 10]])


class TestIntervalCap(unittest.TestCase):
	"""Compaction closes the narrowest gaps. It must never drop a range: over-
	crediting a learner who genuinely watched is the kinder error."""

	def setUp(self):
		_reset_state()

	def test_the_smallest_gap_is_collapsed_first(self):
		intervals = [[0, 10], [13, 20], [100, 110], [200, 210]]
		self.assertEqual(
			progress.merge_intervals([], intervals, 600, cap=3),
			[[0, 20], [100, 110], [200, 210]],
		)

	def test_collapsing_continues_until_the_cap_is_met(self):
		intervals = [[0, 10], [13, 20], [100, 110], [200, 210]]
		self.assertEqual(progress.merge_intervals([], intervals, 600, cap=2), [[0, 110], [200, 210]])

	def test_coverage_never_falls_when_the_cap_bites(self):
		intervals = [[i * 10, i * 10 + 4] for i in range(50)]
		before = progress.coverage(progress.merge_intervals([], intervals, 600), 600)
		after = progress.coverage(progress.merge_intervals([], intervals, 600, cap=5), 600)
		self.assertGreaterEqual(after, before)

	def test_equal_gaps_break_deterministically_on_the_earlier(self):
		"""Two learners with identical histories must store identical progress, or
		'why does his say 81% and mine 80%' has no answer."""
		intervals = [[0, 10], [20, 30], [40, 50]]
		self.assertEqual(progress.merge_intervals([], intervals, 600, cap=2), [[0, 30], [40, 50]])

	def test_no_cap_leaves_the_list_alone(self):
		intervals = [[i * 10, i * 10 + 4] for i in range(20)]
		self.assertEqual(len(progress.merge_intervals([], intervals, 600, cap=0)), 20)

	def test_a_list_under_the_cap_is_untouched(self):
		intervals = [[0, 10], [100, 110]]
		self.assertEqual(progress.merge_intervals([], intervals, 600, cap=200), intervals)


class TestCoverage(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_zero_duration_reads_zero_never_full(self):
		"""A video whose length was never probed must not read as fully watched.
		Dividing by a missing duration is how a compliance gate quietly stops
		gating — see Training Video Asset.duration_source."""
		self.assertEqual(progress.coverage([[0, 100]], 0), 0.0)

	def test_negative_duration_reads_zero(self):
		self.assertEqual(progress.coverage([[0, 100]], -600), 0.0)

	def test_intervals_longer_than_the_duration_clamp_to_one(self):
		self.assertEqual(progress.coverage([[0, 5000]], 600), 1.0)

	def test_nothing_watched_is_zero(self):
		self.assertEqual(progress.coverage([], 600), 0.0)

	def test_half_is_half(self):
		self.assertEqual(progress.coverage([[0, 300]], 600), 0.5)

	def test_unmerged_overlaps_are_not_counted_twice(self):
		self.assertEqual(progress.coverage([[0, 10], [0, 10], [5, 10]], 20), 0.5)

	def test_scattered_ranges_sum(self):
		self.assertAlmostEqual(progress.coverage([[0, 100], [200, 300]], 400), 0.5)


class TestClampNewSeconds(unittest.TestCase):
	"""The backstop. A client controls its own timestamps and its own payload; it
	does not control how much wall time passed between two requests the server
	itself timed."""

	def setUp(self):
		_reset_state()

	def test_an_honest_claim_passes_through(self):
		self.assertEqual(progress.clamp_new_seconds(14, 15), 14)

	def test_an_over_claim_is_truncated_to_the_server_clock(self):
		self.assertEqual(progress.clamp_new_seconds(600, 15), 18)

	def test_the_tolerance_allows_a_late_beat(self):
		"""A throttled background tab batches its timers; the seconds it reports
		late were still watched."""
		self.assertEqual(progress.clamp_new_seconds(18, 15), 18)
		self.assertEqual(progress.clamp_new_seconds(19, 15), 18)

	def test_no_elapsed_time_credits_nothing(self):
		self.assertEqual(progress.clamp_new_seconds(600, 0), 0)

	def test_a_backwards_clock_credits_nothing(self):
		self.assertEqual(progress.clamp_new_seconds(600, -3600), 0)

	def test_a_negative_claim_is_not_a_negative_credit(self):
		self.assertEqual(progress.clamp_new_seconds(-500, 15), 0)

	def test_the_rate_is_overridable_for_callers_that_need_stricter(self):
		self.assertEqual(progress.clamp_new_seconds(600, 100, max_rate=1.0), 100)


# --------------------------------------------------------------------- the blob


class TestLoad(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_an_unknown_attempt_loads_an_empty_blob(self):
		self.assertEqual(progress.load("ATT-9999"), {"lessons": {}})

	def test_a_stored_blob_is_parsed(self):
		STATE["rows"]["ATT-0001"] = {"progress_json": '{"lessons": {"L1": {"status": "done"}}}'}
		self.assertEqual(progress.load("ATT-0001")["lessons"]["L1"]["status"], "done")

	def test_the_second_load_comes_from_cache(self):
		STATE["rows"]["ATT-0001"] = {"progress_json": '{"lessons": {"L1": {"status": "done"}}}'}
		progress.load("ATT-0001")
		STATE["rows"]["ATT-0001"]["progress_json"] = '{"lessons": {}}'
		self.assertIn("L1", progress.load("ATT-0001")["lessons"])

	def test_unparseable_json_starts_fresh_and_is_logged(self):
		"""Losing watch history is bad; a learner who cannot open the lesson at all
		until somebody hand-edits JSON in the database is worse."""
		STATE["rows"]["ATT-0001"] = {"progress_json": "{not json"}
		self.assertEqual(progress.load("ATT-0001"), {"lessons": {}})
		self.assertTrue(STATE["errors"])

	def test_a_blob_of_the_wrong_shape_starts_fresh(self):
		STATE["rows"]["ATT-0001"] = {"progress_json": "[1, 2, 3]"}
		self.assertEqual(progress.load("ATT-0001"), {"lessons": {}})

	def test_no_attempt_name_is_not_an_error(self):
		self.assertEqual(progress.load(None), {"lessons": {}})


class TestSaveWriteThrough(unittest.TestCase):
	"""Heartbeats are frequent and individually worthless; transitions are rare and
	individually load-bearing. The write policy is that sentence."""

	def setUp(self):
		_reset_state()

	def test_the_first_save_writes(self):
		progress.save("ATT-0001", {"lessons": {"L1": {"status": "in_progress"}}})
		self.assertEqual(len(_writes_of()), 1)

	def test_never_bumps_modified(self):
		"""doc.save() here would mean a Version row, a full doc_events cycle and a
		modified bump on every heartbeat."""
		progress.save("ATT-0001", {"lessons": {}}, force=True)
		self.assertTrue(all(w["update_modified"] is False for w in _writes_of()))

	def test_a_watch_only_change_inside_the_window_stays_in_cache(self):
		data = {"lessons": {"L1": {"status": "in_progress", "blocks": {"B1": {"iv": [[0, 10]]}}}}}
		progress.save("ATT-0001", data, force=True)
		before = len(_writes_of())

		_advance(20)
		data["lessons"]["L1"]["blocks"]["B1"]["iv"] = [[0, 40]]
		progress.save("ATT-0001", data)
		self.assertEqual(len(_writes_of()), before)

	def test_the_flush_interval_forces_a_write(self):
		data = {"lessons": {"L1": {"status": "in_progress"}}}
		progress.save("ATT-0001", data, force=True)
		before = len(_writes_of())

		_advance(61)
		progress.save("ATT-0001", data)
		self.assertEqual(len(_writes_of()), before + 1)

	def test_finishing_a_lesson_writes_immediately(self):
		data = {"lessons": {"L1": {"status": "in_progress"}}}
		progress.save("ATT-0001", data, force=True)
		before = len(_writes_of())

		_advance(5)
		data["lessons"]["L1"]["status"] = "done"
		progress.save("ATT-0001", data)
		self.assertEqual(len(_writes_of()), before + 1)

	def test_answering_a_checkpoint_writes_immediately(self):
		data = {"lessons": {"L1": {"status": "in_progress", "checkpoints": {}}}}
		progress.save("ATT-0001", data, force=True)
		before = len(_writes_of())

		_advance(5)
		data["lessons"]["L1"]["checkpoints"]["C1"] = {"answered": 1, "correct": 1}
		progress.save("ATT-0001", data)
		self.assertEqual(len(_writes_of()), before + 1)

	def test_a_quiz_run_writes_immediately(self):
		data = {"lessons": {"L1": {"status": "in_progress", "quiz": {"runs": 0}}}}
		progress.save("ATT-0001", data, force=True)
		before = len(_writes_of())

		_advance(5)
		data["lessons"]["L1"]["quiz"] = {"runs": 1, "best": 86.0}
		progress.save("ATT-0001", data)
		self.assertEqual(len(_writes_of()), before + 1)

	def test_force_always_writes(self):
		data = {"lessons": {"L1": {"status": "in_progress"}}}
		progress.save("ATT-0001", data, force=True)
		before = len(_writes_of())
		progress.save("ATT-0001", data, force=True)
		self.assertEqual(len(_writes_of()), before + 1)

	def test_what_is_written_round_trips(self):
		data = {"lessons": {"L1": {"status": "done", "blocks": {"B1": {"iv": [[0, 10]]}}}}}
		progress.save("ATT-0001", data, force=True)
		STATE["cache"].store.clear()
		self.assertEqual(progress.load("ATT-0001"), data)

	def test_an_unwritten_blob_is_registered_for_the_stale_flush(self):
		data = {"lessons": {"L1": {"status": "in_progress"}}}
		progress.save("ATT-0001", data, force=True)
		_advance(20)
		data["lessons"]["L1"].setdefault("blocks", {})["B1"] = {"iv": [[0, 20]]}
		progress.save("ATT-0001", data)
		self.assertIn("ATT-0001", STATE["cache"].get_value(progress.PENDING_KEY))

	def test_writing_clears_the_pending_registration(self):
		data = {"lessons": {"L1": {"status": "in_progress"}}}
		progress.save("ATT-0001", data, force=True)
		_advance(20)
		progress.save("ATT-0001", data)
		progress.save("ATT-0001", data, force=True)
		self.assertNotIn("ATT-0001", STATE["cache"].get_value(progress.PENDING_KEY) or {})

	def test_no_attempt_name_writes_nothing(self):
		progress.save("", {"lessons": {}}, force=True)
		self.assertEqual(STATE["writes"], [])


# ----------------------------------------------------------------- heartbeats


class TestRecordHeartbeat(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def _beat(self, intervals, **extra):
		payload = {"lesson_key": "L1", "block_key": "B1", "duration": 600, "intervals": intervals}
		payload.update(extra)
		return progress.record_heartbeat("ATT-0001", payload)

	def test_credits_watched_seconds_and_reports_server_coverage(self):
		result = self._beat([[0, 10]])
		self.assertEqual(result["credited"], 10)
		self.assertEqual(result["coverage"], round(10 / 600 * 100, 2))
		self.assertEqual(result["flags"], [])

	def test_coverage_is_a_percentage_not_a_fraction(self):
		"""The two units are one factor of 100 apart and both are called coverage."""
		result = self._beat([[0, 300]], duration=600)
		self.assertEqual(result["credited"], 18)  # clamped, but the unit is the point
		self.assertGreater(result["coverage"], 1.0)

	def test_resending_the_same_range_credits_nothing(self):
		self._beat([[0, 10]])
		_advance(15)
		self.assertEqual(self._beat([[0, 10]])["credited"], 0)

	def test_watching_accumulates_across_beats(self):
		self._beat([[0, 10]])
		_advance(15)
		self._beat([[10, 20]])
		stored = progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]
		self.assertEqual(stored["iv"], [[0, 20]])

	def test_an_over_claim_is_truncated_to_the_server_clock(self):
		"""The whole video claimed on the first beat. 15s elapsed buys 18s."""
		result = self._beat([[0, 600]])
		self.assertEqual(result["credited"], 18)
		self.assertIn("over_claim", result["flags"])
		self.assertEqual(progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]["iv"], [[0, 18]])

	def test_an_over_claim_is_recorded_on_the_attempt(self):
		self._beat([[0, 600]])
		flags = STATE["rows"]["ATT-0001"]["integrity_flags"]
		self.assertIn("L1/B1", flags)
		self.assertIn("600", flags)

	def test_the_truncation_keeps_the_earliest_seconds(self):
		"""Truncating the tail leaves the learner's real position intact, so the
		next beat carries on rather than re-litigating the same range."""
		self._beat([[0, 5], [100, 600]])
		stored = progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]["iv"]
		self.assertEqual(stored[0], [0, 5])
		self.assertEqual(progress._covered_seconds(stored), 18)

	def test_a_missing_integrity_flags_column_does_not_break_a_beat(self):
		"""doc_events and jobs run during a migrate in which the field may not
		exist yet."""
		STATE["columns"].discard("integrity_flags")
		self.assertEqual(self._beat([[0, 600]])["credited"], 18)


class TestClaimMismatch(unittest.TestCase):
	"""The player's own second-count, checked against what the server derived.

	``claimed_seconds`` is computed by the player from its bitmap, independently
	of how it encodes the ranges — so the two numbers only diverge when the two
	sides disagree about the *format*. They did, for the whole of v1.235.0: the
	wire's ``[start, end]`` was decoded as ``[start, length]``, every beat after a
	learner's first pause banked roughly double, and this figure was in the
	payload the entire time, unread.

	This is the ninth-wire-mismatch alarm. It flags rather than trims — see the
	note in ``record_heartbeat``.
	"""

	def setUp(self):
		_reset_state()

	def _beat(self, intervals, **extra):
		payload = {"lesson_key": "L1", "block_key": "B1", "duration": 600, "intervals": intervals}
		payload.update(extra)
		return progress.record_heartbeat("ATT-0001", payload)

	def test_an_honest_beat_raises_nothing(self):
		result = self._beat([[0, 10]], claimed_seconds=10)
		self.assertNotIn("claim_mismatch", result["flags"])
		self.assertEqual(result["credited"], 10)

	def test_the_exact_v1_235_0_defect_is_caught(self):
		"""``[[17, 32]]`` is fifteen seconds. Decoded as a length it is thirty-two."""
		result = self._beat([[17, 49]], claimed_seconds=15)
		self.assertIn("claim_mismatch", result["flags"])

	def test_the_mismatch_names_both_numbers_for_an_auditor(self):
		"""Read the mismatch line itself, not the whole field.

		``integrity_flags`` is one shared buffer, and this beat also trips
		``over_claim`` — whose message quotes the very same 32. Asserting the
		numbers appear *somewhere* passed a mutation that deleted them from the
		line under test, which is the same mistake three other suites in this
		module have made.
		"""
		self._beat([[17, 49]], claimed_seconds=15)
		lines = [
			line
			for line in STATE["rows"]["ATT-0001"]["integrity_flags"].splitlines()
			if "player claimed" in line
		]
		self.assertEqual(len(lines), 1, "expected exactly one claim-mismatch line")
		self.assertIn("15", lines[0])
		self.assertIn("32", lines[0])
		self.assertIn("L1/B1", lines[0])

	def test_it_flags_but_does_not_reduce_the_credit(self):
		"""Compaction and adjacency merging over-credit on purpose; trimming here
		would fight those decisions silently. The flag is the deliverable.

		The server-clock clamp must not fire, or both paths recompute ``candidate``
		and the assertion cannot see whether this branch trimmed anything. That
		needs an earlier beat *and then* time: with no ``beat_at`` in the meta yet,
		``_elapsed_seconds`` falls back to a single heartbeat interval however far
		the clock is wound on, so advancing before the first beat buys nothing.
		"""
		self._beat([[0, 5]], claimed_seconds=5)
		_advance(120)
		flagged = self._beat([[17, 49]], claimed_seconds=15)
		self.assertIn("claim_mismatch", flagged["flags"])
		self.assertNotIn("over_claim", flagged["flags"], "the clamp fired; this test proves nothing")
		self.assertEqual(flagged["credited"], 32)
		self.assertEqual(
			progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]["iv"], [[0, 5], [17, 49]]
		)

	def test_adjacency_merging_is_not_mistaken_for_a_mismatch(self):
		"""Two runs a second apart become one interval, which legitimately gains a
		second nobody claimed. A guard that fired on that would cry wolf on every
		learner who scrubbed."""
		result = self._beat([[0, 5], [6, 10]], claimed_seconds=9)
		self.assertNotIn("claim_mismatch", result["flags"])

	def test_a_beat_without_the_key_is_not_flagged(self):
		"""An older player, or the smoke harness, simply does not send it."""
		self.assertNotIn("claim_mismatch", self._beat([[0, 10]])["flags"])

	def test_a_zero_claim_is_not_flagged(self):
		"""``claimed_seconds: 0`` is absence, not an assertion that nothing counts."""
		self.assertNotIn("claim_mismatch", self._beat([[0, 10]], claimed_seconds=0)["flags"])

	def test_seeks_and_hidden_accumulate(self):
		self._beat([[0, 10]], seeks=3, hidden=12)
		_advance(15)
		self._beat([[10, 20]], seeks=1, hidden=4)
		stored = progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]
		self.assertEqual(stored["seeks"], 4)
		self.assertEqual(stored["hidden"], 16)

	def test_negative_counters_are_ignored(self):
		self._beat([[0, 10]], seeks=-50, hidden=-50)
		stored = progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]
		self.assertEqual(stored["seeks"], 0)
		self.assertEqual(stored["hidden"], 0)

	def test_a_shrinking_duration_is_ignored_and_flagged(self):
		"""Halving the duration halves the watching needed — the cheapest possible
		cheat, and the only one the interval maths cannot see."""
		self._beat([[0, 10]], duration=600)
		_advance(60)
		result = self._beat([[10, 20]], duration=30)
		self.assertIn("duration_shrank", result["flags"])
		self.assertEqual(progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]["dur"], 600)

	def test_a_growing_duration_is_accepted(self):
		self._beat([[0, 10]], duration=600)
		_advance(60)
		self._beat([[10, 20]], duration=900)
		self.assertEqual(progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]["dur"], 900)

	def test_an_unprobed_duration_reads_zero_coverage(self):
		self.assertEqual(self._beat([[0, 10]], duration=0)["coverage"], 0.0)

	def test_a_longer_gap_between_beats_buys_more_credit(self):
		self._beat([[0, 10]])
		_advance(300)
		self.assertEqual(self._beat([[10, 400]])["credited"], 375)

	def test_a_beat_opens_the_lesson(self):
		self._beat([[0, 10]])
		self.assertEqual(progress.load("ATT-0001")["lessons"]["L1"]["status"], "in_progress")

	def test_a_beat_does_not_reopen_a_finished_lesson(self):
		self._beat([[0, 10]])
		data = progress.load("ATT-0001")
		data["lessons"]["L1"]["status"] = "done"
		progress.save("ATT-0001", data, force=True)
		_advance(15)
		self._beat([[10, 20]])
		self.assertEqual(progress.load("ATT-0001")["lessons"]["L1"]["status"], "done")

	def test_a_payload_without_keys_is_ignored(self):
		self.assertEqual(
			progress.record_heartbeat("ATT-0001", {"intervals": [[0, 600]]}),
			{"coverage": 0.0, "credited": 0, "flags": ["ignored"]},
		)

	def test_an_empty_payload_is_ignored(self):
		self.assertEqual(progress.record_heartbeat("ATT-0001", None)["credited"], 0)

	def test_the_interval_cap_is_applied_from_settings(self):
		STATE["settings"]["max_intervals_per_video"] = 5
		_advance(600)
		self._beat([[i * 10, i * 10 + 2] for i in range(40)])
		stored = progress.load("ATT-0001")["lessons"]["L1"]["blocks"]["B1"]["iv"]
		self.assertLessEqual(len(stored), 5)


class TestAReplayedBurstIsNotStarved(unittest.TestCase):
	"""The queue drain — the whole reason ``video.js`` keeps beats in sessionStorage.

	A learner in a lift keeps watching while the beats stack up, then the tab
	sends the backlog in one drain, back to back. Every one of those requests used
	to reset the clamp's window to *now*, so the second arrived a fraction of a
	second after the first, ``int(0.2 * 1.25)`` allowed zero seconds, and the rest
	of the backlog was discarded — while ``_flag`` wrote an over-claim note
	against a learner who had done nothing wrong.

	The window now advances by what each beat actually spent, so a burst shares
	one budget.
	"""

	def setUp(self):
		_reset_state()

	def _beat(self, intervals, **extra):
		payload = {"lesson_key": "L1", "block_key": "B1", "duration": 600, "intervals": intervals}
		payload.update(extra)
		return progress.record_heartbeat("ATT-0001", payload)

	def test_the_second_beat_of_a_drain_still_credits(self):
		self._beat([[0, 10]])          # opens the window
		_advance(60)                   # sixty seconds offline, four beats' worth
		first = self._beat([[10, 25]])  # the backlog, replayed back to back
		second = self._beat([[25, 40]])
		third = self._beat([[40, 55]])
		self.assertEqual(first["credited"], 15)
		self.assertEqual(second["credited"], 15, "the second beat of a drain was starved")
		self.assertEqual(third["credited"], 15, "the third beat of a drain was starved")

	def test_a_replayed_burst_is_not_flagged_as_cheating(self):
		"""Checked on the returned list *and* the persisted field.

		The two carry different strings — the token ``over_claim`` only ever
		appears in the reply, while the field gets prose — so asserting the token
		is absent from the text would have passed no matter what happened.
		"""
		self._beat([[0, 10]])
		_advance(60)
		second = self._beat([[10, 25]])
		third = self._beat([[25, 40]])
		self.assertEqual(second["flags"], [])
		self.assertEqual(third["flags"], [])
		self.assertFalse((STATE["rows"]["ATT-0001"].get("integrity_flags") or "").strip())

	def test_the_control_a_real_over_claim_still_flags_here(self):
		"""Guards the test above: it must fail because nothing was flagged, not
		because flagging stopped working in this fixture."""
		self._beat([[0, 10]])
		_advance(5)
		result = self._beat([[10, 600]])
		self.assertIn("over_claim", result["flags"])
		self.assertTrue((STATE["rows"]["ATT-0001"].get("integrity_flags") or "").strip())

	def test_the_burst_still_cannot_exceed_the_time_that_passed(self):
		"""Shared, not unlimited. Sixty seconds buys seventy-five, and no more."""
		self._beat([[0, 10]])
		_advance(60)
		credited = sum(self._beat([[i * 100, i * 100 + 100]])["credited"] for i in range(1, 6))
		self.assertLessEqual(credited, int(60 * progress.MAX_CLAIM_RATE))

	def test_the_window_never_advances_past_now(self):
		"""Otherwise a beat could spend time that has not happened yet, and the
		next one would arrive with a negative gap."""
		self._beat([[0, 10]])
		_advance(600)
		self._beat([[10, 600]])  # credits a lot, spending far less than 600s
		self.assertLessEqual(progress._meta("ATT-0001")["beat_at"], progress.now_datetime())

	def test_an_honest_steady_stream_is_unchanged(self):
		"""The common case must not shift: 15s of watching every 15s."""
		self._beat([[0, 15]])
		for i in range(1, 5):
			_advance(15)
			self.assertEqual(self._beat([[i * 15, i * 15 + 15]])["credited"], 15)


class TestElapsedFallbacks(unittest.TestCase):
	"""What the clamp does when the server cannot measure the gap. All the
	fallbacks resolve to one heartbeat interval, never to zero — zero would mean
	the first beat after any deploy credits nothing, which reads to a learner as
	the video not counting."""

	def setUp(self):
		_reset_state()

	def _beat(self, intervals):
		return progress.record_heartbeat(
			"ATT-0001",
			{"lesson_key": "L1", "block_key": "B1", "duration": 600, "intervals": intervals},
		)

	def test_the_first_beat_gets_one_interval_of_allowance(self):
		self.assertEqual(self._beat([[0, 600]])["credited"], 18)

	def test_a_redis_flush_gives_one_interval_not_zero(self):
		self._beat([[0, 10]])
		STATE["cache"].store.clear()
		_advance(15)
		self.assertEqual(self._beat([[10, 600]])["credited"], 18)

	def test_a_backwards_clock_falls_back_rather_than_zeroing(self):
		"""now_datetime() is site-local, so a DST step can land between two beats."""
		self._beat([[0, 10]])
		_advance(-3600)
		self.assertEqual(self._beat([[10, 600]])["credited"], 18)

	def test_an_absurd_gap_is_capped_at_the_idle_timeout(self):
		"""A month between beats is not a month of watching. The attempt was dead
		long before; the idle timeout is the most wall time a single beat can ever
		be paid for."""
		self._beat([[0, 10]])
		_advance(30 * 24 * 3600)
		credited = progress.record_heartbeat(
			"ATT-0001",
			{
				"lesson_key": "L1",
				"block_key": "B1",
				"duration": 100000,
				"intervals": [[10, 100000]],
			},
		)["credited"]
		self.assertEqual(credited, int(120 * 60 * 1.25))

	def test_missing_settings_fall_back_to_the_shipped_defaults(self):
		"""A fresh site mid-migrate has no Single. A heartbeat must degrade, not
		500 at a learner."""
		STATE["settings"].clear()
		self.assertEqual(self._beat([[0, 600]])["credited"], int(progress.DEFAULT_HEARTBEAT_SECONDS * 1.25))


class TestFlushStaleAttempts(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def _leave_unwritten(self, attempt="ATT-0001"):
		data = {"lessons": {"L1": {"status": "in_progress"}}}
		progress.save(attempt, data, force=True)
		_advance(20)
		data["lessons"]["L1"]["blocks"] = {"B1": {"iv": [[0, 20]]}}
		progress.save(attempt, data)
		return data

	def test_a_closed_tab_is_drained(self):
		self._leave_unwritten()
		_advance(3600)
		before = len(_writes_of())
		progress.flush_stale_attempts()
		self.assertEqual(len(_writes_of()), before + 1)

	def test_what_is_drained_is_the_cached_blob(self):
		self._leave_unwritten()
		_advance(3600)
		progress.flush_stale_attempts()
		STATE["cache"].store.clear()
		self.assertIn("B1", progress.load("ATT-0001")["lessons"]["L1"]["blocks"])

	def test_an_attempt_still_beating_is_left_alone(self):
		self._leave_unwritten()
		before = len(_writes_of())
		progress.flush_stale_attempts()
		self.assertEqual(len(_writes_of()), before)

	def test_a_drained_attempt_leaves_the_registry(self):
		self._leave_unwritten()
		_advance(3600)
		progress.flush_stale_attempts()
		self.assertNotIn("ATT-0001", STATE["cache"].get_value(progress.PENDING_KEY) or {})

	def test_a_lost_cache_entry_is_dropped_rather_than_retried_forever(self):
		self._leave_unwritten()
		STATE["cache"].store.pop(progress._key("ATT-0001"))
		_advance(3600)
		progress.flush_stale_attempts()
		self.assertNotIn("ATT-0001", STATE["cache"].get_value(progress.PENDING_KEY) or {})

	def test_nothing_pending_is_a_no_op(self):
		progress.flush_stale_attempts()
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(STATE["commits"], 0)

	def test_it_does_not_run_during_a_migrate(self):
		self._leave_unwritten()
		_advance(3600)
		before = len(_writes_of())
		sys.modules["frappe"].flags["in_migrate"] = True
		try:
			progress.flush_stale_attempts()
		finally:
			sys.modules["frappe"].flags.pop("in_migrate", None)
		self.assertEqual(len(_writes_of()), before)


if __name__ == "__main__":
	unittest.main()

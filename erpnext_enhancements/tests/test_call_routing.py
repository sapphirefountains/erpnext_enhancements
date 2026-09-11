# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Call routing — the matcher, pinned by the vectors Triton is pinned by too.

Bench-free and **stub-free**: ``ai_governance.call_routing_match`` imports nothing but the
standard library, which is the whole reason it can be copied into Triton and run there on
the Twilio webhook path. If a future edit makes this file need a ``frappe`` stub, that is
the signal that something frappe-shaped has leaked into the matcher and belongs in
``call_routing.py`` instead.

Most of the assertions come from ``tests/data/call_routing_vectors.json``, which is
committed identically in this repo and in Triton. The class of bug that catches is the one
static review is worst at: two implementations of the same algorithm, deployed separately,
quietly disagreeing about who a call should ring — a disagreement whose only symptom is a
phone that does not ring, weeks later, for one caller in ten.

The handwritten tests below it cover the boundaries the vectors would make unreadable:
midnight-wrapping windows, and the E.164 normalisation that exists because production
stores Employee cell numbers as bare digits.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from erpnext_enhancements.ai_governance import call_routing_match as match

VECTORS_PATH = Path(__file__).resolve().parent / "data" / "call_routing_vectors.json"

#: The keys a case's ``expect`` may pin. Anything else in ``expect`` is a typo, and a typo
#: in an expectation is an assertion that silently never runs — see ``test_expect_keys``.
EXPECTABLE = {
	"rule",
	"voicemail",
	"numbers",
	"clients",
	"include_softphones",
	"account_manager",
	"ring_seconds",
}


def load_vectors() -> dict:
	with VECTORS_PATH.open(encoding="utf-8") as fh:
		return json.load(fh)


class TestVectorFile(unittest.TestCase):
	"""The vectors themselves have to stay trustworthy."""

	def setUp(self) -> None:
		self.vectors = load_vectors()

	def test_vectors_cover_something(self) -> None:
		"""Guard against a moved file or an empty list passing every test below."""
		self.assertGreaterEqual(len(self.vectors["cases"]), 10)
		self.assertGreaterEqual(len(self.vectors["e164"]), 5)
		self.assertGreaterEqual(len(self.vectors["number_match"]), 5)

	def test_default_routing_declares_the_current_schema(self) -> None:
		"""A schema bump without a vectors update leaves both repos pinned to nothing.

		Every case would fall into the "payload from the future" branch and assert the
		fallback plan, which passes — vacuously — no matter what the matcher does.
		"""
		self.assertEqual(self.vectors["default_routing"]["schema_version"], match.SCHEMA_VERSION)

	def test_expect_keys(self) -> None:
		for case in self.vectors["cases"]:
			with self.subTest(case=case["name"]):
				unknown = set(case["expect"]) - EXPECTABLE
				self.assertFalse(unknown, f"unknown expectation key(s): {unknown}")


class TestSharedVectors(unittest.TestCase):
	"""Every case in the shared file. Triton runs the identical set."""

	def setUp(self) -> None:
		self.vectors = load_vectors()

	def test_e164(self) -> None:
		for row in self.vectors["e164"]:
			with self.subTest(value=row["in"], why=row.get("why")):
				self.assertEqual(match.to_e164(row["in"]), row["out"])

	def test_number_match(self) -> None:
		for row in self.vectors["number_match"]:
			with self.subTest(value=row["from"], why=row.get("why")):
				self.assertEqual(match.number_matches(row["from"], row["patterns"]), row["expect"])

	def test_decisions(self) -> None:
		default_routing = self.vectors["default_routing"]
		for case in self.vectors["cases"]:
			routing = case.get("routing", default_routing)
			with self.subTest(case=case["name"], why=case.get("why")):
				plan = match.decide(routing, case["facts"])
				for key, expected in case["expect"].items():
					self.assertEqual(plan[key], expected, f"{case['name']}: {key}")


class TestWindows(unittest.TestCase):
	"""Time windows — the part most likely to be subtly wrong and never noticed."""

	def test_normal_window(self) -> None:
		self.assertTrue(match.in_window(600, 480, 1020))
		self.assertFalse(match.in_window(1140, 480, 1020))

	def test_end_is_exclusive_and_start_inclusive(self) -> None:
		"""17:00-17:00 rules do not double-count the boundary minute with 08:00-17:00."""
		self.assertTrue(match.in_window(480, 480, 1020))
		self.assertFalse(match.in_window(1020, 480, 1020))

	def test_window_wraps_past_midnight(self) -> None:
		"""``start > end`` means wrap, not "never".

		A naive ``start <= m < end`` makes 17:00-08:00 — the most obvious after-hours
		window anybody would type — match at no time of day whatsoever, and the symptom is
		an after-hours rule that simply never fires.
		"""
		self.assertTrue(match.in_window(1140, 1020, 480))
		self.assertTrue(match.in_window(120, 1020, 480))
		self.assertFalse(match.in_window(600, 1020, 480))

	def test_open_ended_windows(self) -> None:
		"""A blank time means "no bound", never "no match"."""
		self.assertTrue(match.in_window(600, None, None))
		self.assertTrue(match.in_window(600, 480, None))
		self.assertFalse(match.in_window(300, 480, None))
		self.assertTrue(match.in_window(300, None, 480))

	def test_equal_bounds_cover_the_whole_day(self) -> None:
		"""00:00-00:00 and 09:00-09:00 read as "all day", which is the kinder reading."""
		self.assertTrue(match.in_window(0, 540, 540))
		self.assertTrue(match.in_window(1439, 540, 540))

	def test_parse_hhmm(self) -> None:
		self.assertEqual(match.parse_hhmm("08:00:00"), 480)
		self.assertEqual(match.parse_hhmm("17:30"), 1050)
		self.assertEqual(match.parse_hhmm("24:00"), 1440)
		self.assertIsNone(match.parse_hhmm(""))
		self.assertIsNone(match.parse_hhmm(None))
		self.assertIsNone(match.parse_hhmm("half past four"))
		self.assertIsNone(match.parse_hhmm("99:99"))


class TestSchedules(unittest.TestCase):
	def test_presets(self) -> None:
		self.assertEqual(match.schedule_days("Weekdays", None), [0, 1, 2, 3, 4])
		self.assertEqual(match.schedule_days("Weekends", None), [5, 6])
		self.assertEqual(match.schedule_days("Every day", None), [0, 1, 2, 3, 4, 5, 6])

	def test_any_time_has_no_day_filter(self) -> None:
		"""``[]`` means "do not filter by day", and must not be read as "no days"."""
		self.assertEqual(match.schedule_days("Any time", None), [])

	def test_custom_days_free_text(self) -> None:
		self.assertEqual(match.parse_weekdays("Mon, Wed & Fri"), [0, 2, 4])
		self.assertEqual(match.parse_weekdays("tuesday/thursday"), [1, 3])
		self.assertEqual(match.parse_weekdays("Tues and Thurs"), [1, 3])
		self.assertEqual(match.parse_weekdays(""), [])
		self.assertEqual(match.parse_weekdays("whenever"), [])


class TestFailOpen(unittest.TestCase):
	"""A configuration mistake must never route every caller to voicemail.

	Same direction as Triton's ``_within_business_hours()``, and for the same reason: the
	failure people can live with is "the wrong person's phone rang", not "nobody's did".
	"""

	def _assert_fell_back(self, plan: dict) -> None:
		self.assertIsNone(plan["rule"])
		self.assertFalse(plan["voicemail"])
		self.assertTrue(plan["include_softphones"])

	def test_none(self) -> None:
		self._assert_fell_back(match.decide(None, {}))

	def test_not_a_dict(self) -> None:
		self._assert_fell_back(match.decide("nope", {}))

	def test_empty_facts(self) -> None:
		self._assert_fell_back(match.decide({"schema_version": match.SCHEMA_VERSION, "rules": []}, {}))

	def test_rule_is_not_a_dict(self) -> None:
		routing = {"schema_version": match.SCHEMA_VERSION, "rules": ["nonsense", None, 7]}
		self._assert_fell_back(match.decide(routing, {"weekday": 1, "minutes": 600}))

	def test_unusable_default_number_is_dropped_not_dialed(self) -> None:
		"""An undialable fallback becomes no PSTN leg, never an empty ``<Number>``."""
		routing = {"schema_version": match.SCHEMA_VERSION, "default_forward_number": "ext 204", "rules": []}
		plan = match.decide(routing, {"weekday": 1, "minutes": 600})
		self.assertEqual(plan["numbers"], [])
		self.assertTrue(plan["include_softphones"])


class TestExplainsItself(unittest.TestCase):
	"""The decision has to be able to answer "why did that call not reach me"."""

	def setUp(self) -> None:
		self.vectors = load_vectors()

	def test_passed_over_rules_are_reported_with_a_reason(self) -> None:
		plan = match.decide(
			self.vectors["default_routing"],
			{
				"weekday": 1,
				"minutes": 1140,
				"intent": "Service",
				"from_number": "+13035550000",
				"known_caller": False,
				"is_holiday": False,
			},
		)
		self.assertEqual(plan["rule"], "After hours")
		skipped = {c["rule"]: c["skipped"] for c in plan["considered"]}
		self.assertIn("Service to Rich", skipped)
		self.assertTrue(skipped["Service to Rich"], "a skipped rule with no reason explains nothing")
		self.assertIn("VIP line", skipped)
		self.assertIn("Weekend on-call", skipped)

	def test_fallback_says_why(self) -> None:
		plan = match.decide({"schema_version": match.SCHEMA_VERSION, "rules": []}, {"weekday": 1, "minutes": 1})
		self.assertIn("no rules", plan["reason"])

	def test_paused_says_so(self) -> None:
		routing = {"schema_version": match.SCHEMA_VERSION, "paused": True, "rules": []}
		self.assertIn("paused", match.decide(routing, {"weekday": 1, "minutes": 1})["reason"])


class TestDialLegCap(unittest.TestCase):
	def test_targets_are_capped_at_the_twilio_limit(self) -> None:
		"""Twilio rings at most 10 endpoints on one ``<Dial>``; the 11th is not dialed.

		Capping here as well as in ``compile_rules`` means the limit holds even if a
		payload is hand-built or arrives from an older ERPNext that did not cap.
		"""
		routing = {
			"schema_version": match.SCHEMA_VERSION,
			"default_ring_seconds": 30,
			"rules": [
				{
					"name": "Everyone",
					"intent": "Any",
					"caller_scope": "Any caller",
					"schedule": "Any time",
					"also_ring_softphones": False,
					"targets": [{"type": "number", "value": f"+1801555{i:04d}"} for i in range(14)],
				}
			],
		}
		plan = match.decide(routing, {"weekday": 1, "minutes": 600})
		self.assertEqual(len(plan["numbers"]), match.MAX_DIAL_LEGS)


if __name__ == "__main__":
	unittest.main()

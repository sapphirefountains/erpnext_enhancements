"""Bench-free tests for event-rental booking rules (v1.562.0).

``asset_management/rental_rules.py`` imports no ``frappe`` and ``asset_management/__init__.py``
is empty, so this runs with the stdlib alone.

Run: python -m unittest erpnext_enhancements.tests.test_rental_rules -v

The ones that matter most, because each fails in the direction that looks fine:

* :meth:`TestPeakUsage.test_bookings_that_never_coexist_do_not_add_up` — summing every
  overlapping line would refuse a booking that fits, and nobody would know the pool was
  being under-used.
* :meth:`TestPlanLegChanges.test_changed_window_moves_rather_than_recreates` — re-creating a
  submitted leg would orphan the inspections filed against it.
* :meth:`TestOutOfServiceEnd.test_open_with_no_date_blocks_forever` — a broken fountain with
  no promised date must never read as free.
* :class:`TestSchemaPins` — the Python status list, the doctype's Select options and Asset
  Booking's booking types have to agree, or a status saves that the rules do not know.
"""

import datetime
import itertools
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.asset_management import rental_rules as rules

APP = REPO_ROOT / "erpnext_enhancements"
DOCTYPES = APP / "asset_management" / "doctype"


def dt(day, hour=0):
	return datetime.datetime(2026, 6, day, hour)


class TestTransitions(unittest.TestCase):
	def test_new_booking_starts_as_hold_or_firm(self):
		self.assertTrue(rules.can_transition(None, "Tentative"))
		self.assertTrue(rules.can_transition(None, "Confirmed"))
		self.assertFalse(rules.can_transition(None, "Out"))
		self.assertFalse(rules.can_transition(None, "Closed"))

	def test_lifecycle(self):
		path = ["Tentative", "Confirmed", "Out", "Returned", "Closed"]
		for old, new in itertools.pairwise(path):
			self.assertTrue(rules.can_transition(old, new), f"{old} -> {new}")

	def test_no_skipping_or_reopening(self):
		self.assertFalse(rules.can_transition("Tentative", "Out"))
		self.assertFalse(rules.can_transition("Closed", "Tentative"))
		self.assertFalse(rules.can_transition("Canceled", "Tentative"))
		self.assertFalse(rules.can_transition("Out", "Canceled"))

	def test_expired_hold_can_be_renewed(self):
		self.assertTrue(rules.can_transition("Expired", "Tentative"))

	def test_staying_put_is_allowed(self):
		for status in rules.STATUSES:
			self.assertTrue(rules.can_transition(status, status))

	def test_every_status_has_a_transition_row(self):
		self.assertEqual(set(rules.TRANSITIONS), set(rules.STATUSES))
		for targets in rules.TRANSITIONS.values():
			self.assertTrue(set(targets) <= set(rules.STATUSES))

	def test_holding_and_released_partition_the_statuses(self):
		self.assertEqual(set(rules.HOLDING_STATUSES) | set(rules.RELEASED_STATUSES), set(rules.STATUSES))
		self.assertFalse(set(rules.HOLDING_STATUSES) & set(rules.RELEASED_STATUSES))
		self.assertTrue(set(rules.FIRM_STATUSES) <= set(rules.HOLDING_STATUSES))


class TestWhole(unittest.TestCase):
	def test_zero_is_an_answer(self):
		self.assertEqual(rules.whole(0, 24), 0)
		self.assertEqual(rules.whole("0", 24), 0)

	def test_blank_falls_back(self):
		self.assertEqual(rules.whole(None, 24), 24)
		self.assertEqual(rules.whole("", 24), 24)
		self.assertEqual(rules.whole("abc", 24), 24)

	def test_negative_clamps(self):
		self.assertEqual(rules.whole(-5), 0)


class TestSchedule(unittest.TestCase):
	def test_ok(self):
		self.assertEqual(rules.schedule_problems(dt(1, 8), dt(3, 18), dt(1, 10), dt(2, 18), dt(2, 23)), [])

	def test_missing(self):
		self.assertEqual(len(rules.schedule_problems(None, None)), 2)

	def test_takedown_before_delivery(self):
		self.assertEqual(rules.schedule_problems(dt(3), dt(1)), ["Take-down has to be after delivery."])
		self.assertEqual(rules.schedule_problems(dt(3), dt(3)), ["Take-down has to be after delivery."])

	def test_event_outside_the_window(self):
		problems = rules.schedule_problems(dt(2), dt(3), event_start=dt(1))
		self.assertIn("Event start has to fall between delivery and take-down.", problems)

	def test_event_order(self):
		problems = rules.schedule_problems(dt(1), dt(5), setup=dt(3), event_start=dt(2), event_end=dt(1, 12))
		self.assertIn("The event cannot start before setup.", problems)
		self.assertIn("The event cannot end before it starts.", problems)


class TestLegWindows(unittest.TestCase):
	def test_rental_only_when_no_buffers(self):
		self.assertEqual(rules.leg_windows(dt(1), dt(3)), [(rules.LEG_RENTAL, dt(1), dt(3))])

	def test_prep_and_turnaround_touch_the_rental(self):
		legs = rules.leg_windows(dt(2, 8), dt(4, 20), prep_hours=4, turnaround_hours=24)
		self.assertEqual(
			legs,
			[
				(rules.LEG_PREP, dt(2, 4), dt(2, 8)),
				(rules.LEG_RENTAL, dt(2, 8), dt(4, 20)),
				(rules.LEG_TURNAROUND, dt(4, 20), dt(5, 20)),
			],
		)
		# Touching legs must not overlap, or one rental would collide with itself.
		for a, b in itertools.pairwise(legs):
			self.assertFalse(rules.overlaps(a[1], a[2], b[1], b[2]))
		self.assertEqual(rules.block_span(legs), (dt(2, 4), dt(5, 20)))

	def test_every_leg_maps_to_an_asset_booking_type(self):
		options = json.loads((DOCTYPES / "asset_booking" / "asset_booking.json").read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in options["fields"]}
		booking_types = set(fields["booking_type"]["options"].split("\n"))
		legs = set(filter(None, fields["rental_leg"]["options"].split("\n")))
		self.assertEqual(legs, set(rules.LEG_BOOKING_TYPE))
		self.assertTrue(set(rules.LEG_BOOKING_TYPE.values()) <= booking_types)

	def test_pool_window_adds_turnaround(self):
		self.assertEqual(rules.pool_window(dt(1), dt(2), 24), (dt(1), dt(3)))
		self.assertEqual(rules.pool_window(dt(1), dt(2), None), (dt(1), dt(2)))


class TestOverlaps(unittest.TestCase):
	def test_touching_is_not_overlapping(self):
		self.assertFalse(rules.overlaps(dt(1), dt(2), dt(2), dt(3)))
		self.assertFalse(rules.overlaps(dt(2), dt(3), dt(1), dt(2)))

	def test_overlap(self):
		self.assertTrue(rules.overlaps(dt(1), dt(3), dt(2), dt(4)))
		self.assertTrue(rules.overlaps(dt(1), dt(5), dt(2), dt(3)))

	def test_open_ended(self):
		self.assertTrue(rules.overlaps(dt(10), dt(11), dt(1), None))
		self.assertFalse(rules.overlaps(dt(1), dt(2), dt(2), None))


class TestPeakUsage(unittest.TestCase):
	def test_empty(self):
		self.assertEqual(rules.peak_usage([], dt(1), dt(5)), 0)

	def test_bookings_that_never_coexist_do_not_add_up(self):
		rows = [(dt(1), dt(2, 12), 8), (dt(3), dt(4), 8)]
		self.assertEqual(rules.peak_usage(rows, dt(1), dt(5)), 8)

	def test_bookings_that_coexist_add_up(self):
		rows = [(dt(1), dt(3), 5), (dt(2), dt(4), 4)]
		self.assertEqual(rules.peak_usage(rows, dt(1), dt(5)), 9)

	def test_return_and_reissue_at_the_same_instant(self):
		rows = [(dt(1), dt(2), 6), (dt(2), dt(3), 6)]
		self.assertEqual(rules.peak_usage(rows, dt(1), dt(3)), 6)

	def test_rows_outside_the_window_are_ignored(self):
		rows = [(dt(10), dt(12), 20)]
		self.assertEqual(rules.peak_usage(rows, dt(1), dt(5)), 0)

	def test_only_the_part_inside_the_window_counts(self):
		# Two lines overlap each other on day 6, but the window ends on day 5.
		rows = [(dt(1), dt(7), 3), (dt(6), dt(8), 3)]
		self.assertEqual(rules.peak_usage(rows, dt(1), dt(5)), 3)

	def test_pool_problem(self):
		self.assertIsNone(rules.pool_problem("Uplights", 4, 12, 8))
		self.assertIn("Only 4 of Uplights are free", rules.pool_problem("Uplights", 5, 12, 8))
		self.assertIn("Only 0 of", rules.pool_problem("Uplights", 1, 2, 5))


class TestOutOfServiceEnd(unittest.TestCase):
	def test_open_with_no_date_blocks_forever(self):
		self.assertIsNone(rules.out_of_service_end("Out of Service"))

	def test_open_with_expected_back_blocks_through_that_day(self):
		end = rules.out_of_service_end("Out of Service", expected_back=datetime.date(2026, 6, 10))
		self.assertEqual(end, datetime.datetime(2026, 6, 10, 23, 59, 59))

	def test_returned_blocks_until_it_came_back(self):
		end = rules.out_of_service_end(
			"Returned to Service", returned_on=dt(4, 9), expected_back=datetime.date(2026, 6, 10)
		)
		self.assertEqual(end, dt(4, 9))


class TestPlanLegChanges(unittest.TestCase):
	def leg(self, name, asset, leg, start, end):
		return {"name": name, "asset": asset, "leg": leg, "from": start, "to": end}

	def test_new_booking_creates_everything(self):
		desired = [("F1", "Rental", dt(1), dt(2)), ("F1", "Turnaround", dt(2), dt(3))]
		self.assertEqual(rules.plan_leg_changes(desired, []), (desired, [], []))

	def test_unchanged_does_nothing(self):
		existing = [self.leg("AB-1", "F1", "Rental", dt(1), dt(2))]
		self.assertEqual(rules.plan_leg_changes([("F1", "Rental", dt(1), dt(2))], existing), ([], [], []))

	def test_changed_window_moves_rather_than_recreates(self):
		existing = [self.leg("AB-1", "F1", "Rental", dt(1), dt(2))]
		create, move, remove = rules.plan_leg_changes([("F1", "Rental", dt(1), dt(4))], existing)
		self.assertEqual((create, move, remove), ([], [("AB-1", dt(1), dt(4))], []))

	def test_fountain_taken_off_is_removed(self):
		existing = [self.leg("AB-1", "F1", "Rental", dt(1), dt(2)), self.leg("AB-2", "F2", "Rental", dt(1), dt(2))]
		_create, _move, remove = rules.plan_leg_changes([("F1", "Rental", dt(1), dt(2))], existing)
		self.assertEqual(remove, ["AB-2"])

	def test_buffer_set_to_zero_removes_its_leg(self):
		existing = [self.leg("AB-1", "F1", "Rental", dt(1), dt(2)), self.leg("AB-2", "F1", "Turnaround", dt(2), dt(3))]
		_create, _move, remove = rules.plan_leg_changes([("F1", "Rental", dt(1), dt(2))], existing)
		self.assertEqual(remove, ["AB-2"])

	def test_duplicate_leg_is_removed(self):
		existing = [self.leg("AB-1", "F1", "Rental", dt(1), dt(2)), self.leg("AB-9", "F1", "Rental", dt(1), dt(2))]
		self.assertEqual(rules.plan_leg_changes([("F1", "Rental", dt(1), dt(2))], existing), ([], [], ["AB-9"]))

	def test_released_booking_removes_everything(self):
		existing = [self.leg("AB-1", "F1", "Rental", dt(1), dt(2))]
		self.assertEqual(rules.plan_leg_changes([], existing), ([], [], ["AB-1"]))


class TestMoney(unittest.TestCase):
	def test_total(self):
		self.assertEqual(rules.booking_total([500, None], [(25, 4), (None, 2)], [150, "75.5", None]), 825.5)

	def test_hold_expiry(self):
		self.assertEqual(rules.default_hold_expiry(datetime.date(2026, 9, 29)), datetime.date(2026, 10, 6))


class TestCoveredHours(unittest.TestCase):
	def test_empty(self):
		self.assertEqual(rules.covered_hours([], dt(1), dt(2)), 0)

	def test_clipped_to_the_window(self):
		self.assertEqual(rules.covered_hours([(dt(1, 12), dt(3, 12))], dt(2), dt(3)), 24)

	def test_overlaps_count_once(self):
		"""Two bookings the same afternoon are one afternoon of use, never more hours than exist."""
		windows = [(dt(1, 8), dt(1, 16)), (dt(1, 12), dt(1, 20)), (dt(1, 18), dt(1, 19))]
		self.assertEqual(rules.covered_hours(windows, dt(1), dt(2)), 12)

	def test_separate_windows_add(self):
		self.assertEqual(rules.covered_hours([(dt(1, 0), dt(1, 6)), (dt(1, 12), dt(1, 18))], dt(1), dt(2)), 12)

	def test_outside_the_window_is_ignored(self):
		self.assertEqual(rules.covered_hours([(dt(5), dt(6))], dt(1), dt(2)), 0)


class TestSchemaPins(unittest.TestCase):
	def load(self, name):
		return json.loads((DOCTYPES / name / f"{name}.json").read_text(encoding="utf-8"))

	def test_status_options_match_the_rules(self):
		fields = {f["fieldname"]: f for f in self.load("rental_booking")["fields"]}
		self.assertEqual(tuple(fields["status"]["options"].split("\n")), rules.STATUSES)
		self.assertEqual(fields["status"]["default"], "Tentative")

	def test_out_of_service_statuses_match_the_controller(self):
		fields = {f["fieldname"]: f for f in self.load("asset_out_of_service")["fields"]}
		source = (DOCTYPES / "asset_out_of_service" / "asset_out_of_service.py").read_text(encoding="utf-8")
		for status in fields["status"]["options"].split("\n"):
			self.assertIn(f'"{status}"', source)
		# rental_rules keys "returned" on this exact string.
		self.assertIn("Returned to Service", fields["status"]["options"])

	def test_asset_rental_status_offers_out_of_service(self):
		fixtures = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
		status = next(f for f in fixtures if f["name"] == "Asset-custom_rental_status")
		self.assertIn("Out of Service", status["options"].split("\n"))
		names = {f["name"] for f in fixtures}
		for field in ("custom_rentable", "custom_rental_prep_hours", "custom_rental_turnaround_hours"):
			self.assertIn(f"Asset-{field}", names)
			# The fleet Assets are submitted; a field without allow_on_submit could never be ticked.
			record = next(f for f in fixtures if f["name"] == f"Asset-{field}")
			self.assertEqual(record["allow_on_submit"], 1, field)

	def test_patch_creates_fields_exactly_as_the_fixture_does(self):
		fixtures = {f["name"]: f for f in json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))}
		patch = (APP / "patches" / "seed_rental_fleet_flags.py").read_text(encoding="utf-8")
		for field in ("custom_rentable", "custom_rental_prep_hours", "custom_rental_turnaround_hours"):
			record = fixtures[f"Asset-{field}"]
			for key in ("fieldtype", "label", "insert_after", "default"):
				self.assertIn(f'"{key}": "{record[key]}"', patch, f"{field}.{key}")

	def test_managed_legs_are_edited_on_submit_only_through_allowed_fields(self):
		fields = {f["fieldname"]: f for f in self.load("asset_booking")["fields"]}
		# rental_availability.sync_calendar moves submitted legs in place.
		for name in ("from_datetime", "to_datetime", "location", "customer", "project"):
			self.assertEqual(fields[name].get("allow_on_submit"), 1, name)

	def test_hooks_wire_asset_repair_and_the_project_banner(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertRegex(hooks, re.escape('"Asset Repair": {'))
		self.assertIn("asset_management.out_of_service.on_asset_repair_change", hooks)
		self.assertIn('"public/js/asset_management/project_rental.js"', hooks)
		self.assertTrue((APP / "public" / "js" / "asset_management" / "project_rental.js").exists())

	def test_patch_is_registered(self):
		self.assertIn(
			"erpnext_enhancements.patches.seed_rental_fleet_flags",
			(APP / "patches.txt").read_text(encoding="utf-8"),
		)


if __name__ == "__main__":
	unittest.main()

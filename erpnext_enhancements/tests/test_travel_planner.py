"""Bench-free tests for Plan a Trip: the trip checklist and the booking fan-out.

``travel_management/completeness.py`` decides what a trip is still missing, and
``travel_management/planner.py`` turns the page's booking cards (one flight, four people) into
one Travel Trip row per person and back. Both are written to run without a site; a minimal
``frappe`` stub is installed in ``setUpModule`` before they are imported.

The failures guarded here all leave the screen looking right:

1. **A checklist that says "complete" when someone has no bed.** A shared room has to count for
   everyone in it, a whole-crew row for the whole crew, and a traveler who joins late must not
   be flagged for nights before they arrive.
2. **One person's booking on another person's itinerary.** Each member of a card must become
   their own row, pinned to them, carrying their own confirmation number.
3. **An unrelated edit rewriting data typed on the form.** Two rows of one booking can differ;
   a shared field the page did not change must not be copied across them, and an uneven cost
   split must survive an edit that did not touch the cost or the people.
4. **Money that has left the trip being moved.** A row already on an Expense Claim is never
   deleted and its cost is never rewritten.
5. **The page and the server disagreeing about a field.** The page's list of shared fields must
   equal the server's, or an edit is dropped with no error.

unittest, not pytest, with its own CI step: a pytest-style suite in a unittest list is collected
as nothing, and this module's ``frappe`` stub must not share a process with another suite's.

Run: python -m unittest erpnext_enhancements.tests.test_travel_planner -v
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAVEL_DIR = os.path.join(APP_DIR, "travel_management")
PAGE_DIR = os.path.join(TRAVEL_DIR, "page", "plan_a_trip")

completeness = None
planner = None


def _install_frappe_stub():
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		try:
			result = float(value or 0)
		except (TypeError, ValueError):
			result = 0.0
		return round(result, precision) if precision is not None else result

	def cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def whitelist(*args, **kwargs):
		if args and callable(args[0]) and not kwargs:
			return args[0]
		return lambda fn: fn

	utils.flt = flt
	utils.cint = cint
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = whitelist
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global completeness, planner
	_install_frappe_stub()
	from erpnext_enhancements.travel_management import completeness as c
	from erpnext_enhancements.travel_management import planner as p

	completeness = c
	planner = p


def row(**kwargs):
	kwargs.setdefault("name", None)
	return types.SimpleNamespace(**kwargs)


def traveler(employee, name=None, from_date=None, to_date=None):
	return row(
		name=f"T-{employee}",
		employee=employee,
		employee_name=name or employee,
		from_date=from_date,
		to_date=to_date,
	)


def trip(**kwargs):
	base = dict(
		start_date="2026-10-05",
		end_date="2026-10-08",
		travelers=[traveler("EMP-A", "Ann"), traveler("EMP-B", "Bo")],
		flights=[],
		accommodations=[],
		ground_transport=[],
	)
	base.update(kwargs)
	return types.SimpleNamespace(**base)


def stay(traveler_id, check_in, check_out, **kwargs):
	return row(
		traveler=traveler_id,
		check_in_date=check_in,
		check_out_date=check_out,
		hotel_lodging=kwargs.pop("hotel", "Hilton"),
		booking_confirmation=kwargs.pop("ref", "H1"),
		cost=kwargs.pop("cost", 100),
		**kwargs,
	)


def flight(traveler_id, leg, when, **kwargs):
	return row(
		traveler=traveler_id,
		leg=leg,
		departure_time=when,
		airline=kwargs.pop("airline", "Southwest"),
		flight_number=kwargs.pop("flight_number", "WN1"),
		booking_reference=kwargs.pop("ref", "PNR1"),
		cost=kwargs.pop("cost", 200),
		**kwargs,
	)


def checks(gaps, check):
	return [g for g in gaps if g["check"] == check]


# --------------------------------------------------------------------------- checklist


class TestBedEveryNight(unittest.TestCase):
	def test_a_room_covers_only_the_people_in_it(self):
		t = trip(accommodations=[stay("EMP-A", "2026-10-05", "2026-10-08")])
		gaps = checks(completeness.lodging_gaps(t), "lodging")
		self.assertEqual(len(gaps), 1)
		self.assertEqual(gaps[0]["employee"], "EMP-B")
		self.assertEqual(gaps[0]["nights"], ["2026-10-05", "2026-10-06", "2026-10-07"])

	def test_a_shared_room_counts_for_both(self):
		t = trip(
			accommodations=[
				stay("EMP-A", "2026-10-05", "2026-10-08", booking_group="g1"),
				stay("EMP-B", "2026-10-05", "2026-10-08", booking_group="g1"),
			]
		)
		self.assertEqual(completeness.lodging_gaps(t), [])

	def test_a_whole_crew_row_covers_everyone(self):
		t = trip(accommodations=[stay(None, "2026-10-05", "2026-10-08")])
		self.assertEqual(completeness.lodging_gaps(t), [])

	def test_checkout_day_is_not_a_night(self):
		t = trip(accommodations=[stay(None, "2026-10-05", "2026-10-07")])
		gaps = completeness.lodging_gaps(t)
		self.assertEqual([g["nights"] for g in gaps], [["2026-10-07"], ["2026-10-07"]])

	def test_a_late_arrival_is_not_flagged_before_they_arrive(self):
		t = trip(
			travelers=[traveler("EMP-A", from_date="2026-10-07")],
			accommodations=[stay("EMP-A", "2026-10-07", "2026-10-08")],
		)
		self.assertEqual(completeness.lodging_gaps(t), [])

	def test_a_one_day_trip_needs_no_bed(self):
		t = trip(start_date="2026-10-05", end_date="2026-10-05")
		self.assertEqual(completeness.lodging_gaps(t), [])

	def test_a_room_without_dates_is_flagged_on_its_own(self):
		t = trip(accommodations=[stay(None, None, None, name="R1")])
		kinds = [g["kind"] for g in completeness.lodging_gaps(t)]
		self.assertIn("dates", kinds)
		self.assertEqual(kinds.count("nights"), 2)


class TestTravelBothWays(unittest.TestCase):
	def test_missing_return_is_named(self):
		t = trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00"),
				flight("EMP-B", "Outbound", "2026-10-05 07:00:00"),
				flight("EMP-A", "Return", "2026-10-08 18:00:00"),
			]
		)
		gaps = completeness.travel_gaps(t)
		self.assertEqual([(g["employee"], g["kind"], g["step"]) for g in gaps], [("EMP-B", "Return", "back")])

	def test_a_blank_leg_is_read_from_the_date(self):
		t = trip(
			travelers=[traveler("EMP-A")],
			flights=[flight(None, None, "2026-10-05 07:00:00"), flight(None, None, "2026-10-08 18:00:00")],
		)
		self.assertEqual(completeness.travel_gaps(t), [])

	def test_a_company_truck_counts(self):
		t = trip(
			travelers=[traveler("EMP-A")],
			ground_transport=[
				row(traveler="EMP-A", leg="Outbound", transport_type="Company Fleet", pickup_datetime=None),
				row(traveler="EMP-A", leg="Return", transport_type="Company Fleet", pickup_datetime=None),
			],
		)
		self.assertEqual(completeness.travel_gaps(t), [])

	def test_getting_around_is_not_a_way_there(self):
		t = trip(
			travelers=[traveler("EMP-A")],
			ground_transport=[row(traveler="EMP-A", leg="During Trip", transport_type="Rental/Third Party")],
		)
		self.assertEqual(len(completeness.travel_gaps(t)), 2)


class TestLegFromItsPeoplesDates(unittest.TestCase):
	"""The first real trip on prod (TRIP-2026-00001, entered on the form before the page
	existed): four people, two of whom start a day late, and whole-crew flights with no leg.
	Reading legs off the TRIP's dates put the day-two flight — the late starters' way there —
	under "Getting around"."""

	def real_trip(self):
		return trip(
			start_date="2026-09-27",
			end_date="2026-10-02",
			travelers=[
				traveler("EMP-K", "K", "2026-09-27", "2026-10-02"),
				traveler("EMP-J", "J", "2026-09-27", "2026-10-02"),
				traveler("EMP-B", "B", "2026-09-28", "2026-09-28"),
				traveler("EMP-L", "L", "2026-09-28", "2026-09-29"),
			],
			flights=[
				flight(None, None, "2026-09-27 05:10:00", name="F1", airline="Delta"),
				flight(None, None, "2026-09-28 07:20:00", name="F2", airline="Southwest"),
			],
			ground_transport=[
				row(name="G1", traveler=None, leg=None, transport_type="Rental/Third Party", pickup_datetime=None,
					supplier="FOX", booking_reference=None, cost=0),
			],
		)

	def test_the_day_two_flight_is_a_way_there(self):
		t = self.real_trip()
		self.assertEqual(completeness.booking_leg("flights", [t.flights[1]], t), "Outbound")
		# And the checklist agrees: nobody lacks a way there.
		self.assertEqual({g["kind"] for g in completeness.travel_gaps(t)}, {"Return"})

	def test_an_undated_rental_is_getting_around(self):
		t = self.real_trip()
		self.assertEqual(completeness.booking_leg("ground_transport", t.ground_transport, t), "During Trip")

	def test_fix_links_point_at_the_step_the_page_shows_it_on(self):
		t = self.real_trip()
		t.flights[1].booking_reference = ""  # as on prod: no confirmation numbers yet
		steps = {g["label"]: g["step"] for g in completeness.confirmation_gaps(t)}
		self.assertEqual(steps["Southwest WN1"], "there")
		self.assertEqual(steps["FOX"], "around")

	def test_an_explicit_leg_always_wins(self):
		t = self.real_trip()
		t.flights[1].leg = "During Trip"
		self.assertEqual(completeness.booking_leg("flights", [t.flights[1]], t), "During Trip")

	def test_the_page_uses_the_same_rule(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		body = re.search(r"\n\tinfer_legs\(\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		# Per-person windows, not the trip's dates alone; hired transport undated = around.
		self.assertIn("c.from_date || t.start_date", body)
		self.assertIn('"Rental/Third Party", "Taxi/Rideshare"', body)
		self.assertEqual(
			sorted(completeness.HIRED_TRANSPORT), ["Rental/Third Party", "Taxi/Rideshare"]
		)


class TestConfirmationAndCost(unittest.TestCase):
	def test_only_the_person_without_a_number_is_named(self):
		t = trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref="ABC"),
				flight("EMP-B", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref=""),
			]
		)
		gaps = completeness.confirmation_gaps(t)
		self.assertEqual(len(gaps), 1)
		self.assertEqual(gaps[0]["employee_names"], ["Bo"])
		self.assertEqual(gaps[0]["group"], "g1")
		self.assertEqual(gaps[0]["step"], "there")

	def test_unbooked_vehicles_are_exempt(self):
		for kind in ("Company Fleet", "Personal Vehicle"):
			t = trip(
				ground_transport=[
					row(name="G1", traveler="EMP-A", leg="Outbound", transport_type=kind, booking_reference=None, cost=0)
				]
			)
			self.assertEqual(completeness.confirmation_gaps(t), [], kind)
			self.assertEqual(completeness.cost_gaps(t), [], kind)

	def test_a_booking_with_no_cost_at_all_is_flagged_once(self):
		t = trip(
			accommodations=[
				stay("EMP-A", "2026-10-05", "2026-10-08", booking_group="g1", cost=0),
				stay("EMP-B", "2026-10-05", "2026-10-08", booking_group="g1", cost=0),
			]
		)
		gaps = completeness.cost_gaps(t)
		self.assertEqual(len(gaps), 1)
		self.assertEqual(gaps[0]["step"], "lodging")

	def test_a_cost_on_one_row_of_the_booking_is_enough(self):
		t = trip(
			accommodations=[
				stay("EMP-A", "2026-10-05", "2026-10-08", booking_group="g1", cost=150),
				stay("EMP-B", "2026-10-05", "2026-10-08", booking_group="g1", cost=0),
			]
		)
		self.assertEqual(completeness.cost_gaps(t), [])

	def test_find_gaps_runs_in_step_order(self):
		t = trip(accommodations=[stay(None, "2026-10-05", "2026-10-08", ref="", cost=0)])
		order = [g["check"] for g in completeness.find_gaps(t)]
		self.assertEqual(order, sorted(order, key=["travel", "lodging", "confirmation", "cost"].index))


# --------------------------------------------------------------------------- fan-out


def card(group, values, members, changed=None, origin=None):
	return {
		"group": group,
		"origin": origin if origin is not None else group,
		"values": values,
		"changed": changed or [],
		"members": members,
	}


class TestHelpers(unittest.TestCase):
	def test_split_even_adds_back_up(self):
		self.assertEqual(planner.split_even(100, 3), [33.34, 33.33, 33.33])
		self.assertAlmostEqual(sum(planner.split_even(1640.2, 4)), 1640.2)
		self.assertEqual(planner.split_even(0, 2), [0.0, 0.0])
		self.assertEqual(planner.split_even(50, 0), [])

	def test_group_ids(self):
		self.assertEqual(planner.normalize_group("a1b2c3d4e5f6"), "a1b2c3d4e5f6")
		for key in ("row:TF-0001", "new:3", "x' OR 1=1 --", "", None):
			fresh = planner.normalize_group(key)
			self.assertRegex(fresh, r"^[0-9a-f]{12}$")

	def test_plain_formats_time_values(self):
		from datetime import datetime, timedelta

		self.assertEqual(planner.plain(timedelta(hours=14, minutes=5)), "14:05:00")
		self.assertEqual(planner.plain(datetime(2026, 10, 5, 7, 30)), "2026-10-05 07:30:00")
		self.assertEqual(planner.plain(date(2026, 10, 5)), "2026-10-05")
		self.assertIsNone(planner.plain(""))


class TestMergeBookings(unittest.TestCase):
	def test_a_new_card_becomes_one_row_per_person(self):
		values = {"leg": "Outbound", "airline": "Southwest", "flight_number": "WN1", "cost": 300, "paid_by": "Company"}
		cards = [
			card(
				"g1",
				values,
				[{"traveler": "EMP-A", "ref": "AAA"}, {"traveler": "EMP-B", "ref": "BBB"}],
				changed=list(values),
				origin="new:1",
			)
		]
		rows, notes = planner.merge_bookings([], cards, "flights")
		self.assertEqual(notes, [])
		self.assertEqual([r["traveler"] for r in rows], ["EMP-A", "EMP-B"])
		self.assertEqual([r["booking_reference"] for r in rows], ["AAA", "BBB"])
		self.assertEqual([r["cost"] for r in rows], [150.0, 150.0])
		self.assertTrue(all(r["booking_group"] == "g1" and r["airline"] == "Southwest" for r in rows))

	def test_an_unchanged_field_is_not_copied_across_rows(self):
		a = row(name="F1", traveler="EMP-A", airline="Southwest", departure_time="x", booking_group="g1", cost=60)
		b = row(name="F2", traveler="EMP-B", airline="Delta", departure_time="x", booking_group="g1", cost=40)
		cards = [
			card(
				"g1",
				{"airline": "Southwest", "departure_time": "2026-10-05 09:00:00", "cost": 100},
				[{"name": "F1", "traveler": "EMP-A"}, {"name": "F2", "traveler": "EMP-B"}],
				changed=["departure_time"],
			)
		]
		planner.merge_bookings([a, b], cards, "flights")
		self.assertEqual(b.airline, "Delta")
		self.assertEqual((a.departure_time, b.departure_time), ("2026-10-05 09:00:00",) * 2)
		# Neither the total nor the people changed, so the 60/40 split typed on the form stays.
		self.assertEqual((a.cost, b.cost), (60, 40))

	def test_dropping_a_person_drops_their_row_and_resplits(self):
		a = row(name="F1", traveler="EMP-A", booking_group="g1", cost=60)
		b = row(name="F2", traveler="EMP-B", booking_group="g1", cost=40)
		cards = [card("g1", {"cost": 100}, [{"name": "F1", "traveler": "EMP-A"}])]
		rows, _notes = planner.merge_bookings([a, b], cards, "flights")
		self.assertEqual(rows, [a])
		self.assertEqual(a.cost, 100.0)

	def test_a_claimed_row_is_kept_and_its_money_untouched(self):
		claimed = row(
			name="F1",
			traveler="EMP-A",
			booking_group="g1",
			cost=80,
			paid_by="Employee",
			paid_by_traveler="EMP-A",
			expense_claim="HR-EXP-1",
		)
		other = row(name="F2", traveler="EMP-B", booking_group="g2", cost=10)
		cards = [
			card(
				"g1",
				{"cost": 500, "paid_by": "Company", "paid_by_traveler": None},
				[{"name": "F1", "traveler": "EMP-B"}],
				changed=["cost", "paid_by", "paid_by_traveler"],
			)
		]
		rows, _notes = planner.merge_bookings([claimed, other], cards, "flights")
		self.assertEqual((claimed.cost, claimed.paid_by, claimed.traveler), (80, "Employee", "EMP-A"))
		self.assertNotIn(other, rows)

		rows, notes = planner.merge_bookings([claimed], [], "flights")
		self.assertEqual(rows, [claimed])
		self.assertEqual(len(notes), 1)

	def test_a_whole_crew_row_stays_whole_crew(self):
		legacy = row(name="H1", traveler=None, hotel_lodging="Hilton", booking_confirmation="X", cost=300)
		cards = [card("row:H1", {"cost": 300}, [{"name": "H1", "traveler": "", "ref": "X"}])]
		rows, _notes = planner.merge_bookings([legacy], planner.prepare_cards(cards), "accommodations")
		self.assertEqual(rows, [legacy])
		self.assertIsNone(legacy.traveler)
		self.assertRegex(legacy.booking_group, r"^[0-9a-f]{12}$")
		self.assertEqual(legacy.cost, 300)

	def test_a_booking_with_nobody_on_it_is_refused(self):
		with self.assertRaises(planner.PlanError):
			planner.merge_bookings([], [card("g1", {}, [])], "flights")


class TestMergeMileageTravelersStops(unittest.TestCase):
	def _pv(self, group, driver, miles):
		c = card(
			group,
			{
				"transport_type": "Personal Vehicle",
				"pickup_datetime": "2026-10-05 06:00:00",
				"pickup_location": "Shop",
				"dropoff_location": "Site",
			},
			[{"traveler": driver}],
		)
		c["mileage"] = {"driver": driver, "distance": miles}
		return c

	def test_a_personal_drive_gets_one_mileage_row_for_the_driver(self):
		hand_typed = row(name="M0", booking_group=None, traveler="EMP-B", distance=5)
		rows, _notes = planner.merge_mileage([hand_typed], [self._pv("g1", "EMP-A", 120)], set())
		self.assertEqual(rows[0], hand_typed)
		new = rows[1]
		self.assertEqual(
			(new["traveler"], new["distance"], new["date"], new["booking_group"]),
			("EMP-A", 120.0, "2026-10-05", "g1"),
		)

	def test_removing_the_drive_removes_its_mileage(self):
		old = row(name="M1", booking_group="g1", traveler="EMP-A", distance=120, expense_claim=None)
		rows, _notes = planner.merge_mileage([old], [], {"g1"})
		self.assertEqual(rows, [])

	def test_claimed_mileage_survives(self):
		old = row(name="M1", booking_group="g1", traveler="EMP-A", distance=120, expense_claim="HR-EXP-1")
		rows, notes = planner.merge_mileage([old], [], {"g1"})
		self.assertEqual(rows, [old])
		self.assertEqual(len(notes), 1)

	def test_travelers_keep_their_rows(self):
		existing = row(name="T1", employee="EMP-A", is_trip_lead=1, per_diem_claimed=1)
		rows = planner.merge_travelers(
			[existing],
			[
				{"employee": "EMP-B", "is_trip_lead": 1},
				{"employee": "EMP-A", "is_trip_lead": 0},
				{"employee": "EMP-B"},
			],
		)
		self.assertEqual(len(rows), 2)
		self.assertIs(rows[1], existing)
		self.assertEqual(existing.per_diem_claimed, 1)
		self.assertEqual((rows[0]["is_trip_lead"], existing.is_trip_lead), (1, 0))

	def test_a_stop_that_made_a_lead_is_kept(self):
		made = row(name="S1", date="2026-10-06", outcome_name="CRM-LEAD-1", outcome_doctype="Lead")
		plain_stop = row(name="S2", date="2026-10-06", outcome_name=None)
		rows, notes = planner.merge_stops([made, plain_stop], [])
		self.assertEqual(rows, [made])
		self.assertEqual(len(notes), 1)


class TestFreight(unittest.TestCase):
	def test_a_shipment_is_one_row_and_writes_only_its_fields(self):
		rows, notes = planner.merge_freight(
			[],
			[
				{
					"name": None,
					"carrier": "Old Dominion",
					"tracking_number": "PRO 123",
					"delivery_from": "2026-10-06 08:00:00",
					"delivery_to": "2026-10-06 12:00:00",
					"cost": "310.5",
					"expense_claim": "HR-EXP-SNEAKY",
				}
			],
		)
		self.assertEqual(notes, [])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["cost"], 310.5)
		self.assertEqual(rows[0]["delivery_to"], "2026-10-06 12:00:00")
		self.assertNotIn("expense_claim", rows[0])

	def test_a_claimed_shipment_keeps_its_money_and_survives(self):
		claimed = row(name="FR1", carrier="ODFL", cost=300, paid_by="Employee", expense_claim="HR-EXP-1")
		planner.merge_freight([claimed], [{"name": "FR1", "carrier": "ODFL", "cost": 999, "paid_by": "Company"}])
		self.assertEqual((claimed.cost, claimed.paid_by), (300, "Employee"))
		rows, notes = planner.merge_freight([claimed], [])
		self.assertEqual((rows, len(notes)), ([claimed], 1))

	def test_freight_gaps_ask_for_a_tracking_number_and_a_cost(self):
		t = trip(freight=[row(name="FR1", carrier="ODFL", tracking_number="", cost=0, traveler="EMP-A")])
		confirmation = completeness.confirmation_gaps(t)
		self.assertEqual([(g["table"], g["step"], g["group"]) for g in confirmation], [("freight", "freight", "row:FR1")])
		# The receiver is not whose number is missing.
		self.assertEqual(confirmation[0]["employee_names"], [])
		self.assertEqual([g["step"] for g in completeness.cost_gaps(t)], ["freight"])

	def test_freight_never_counts_as_a_way_there(self):
		t = trip(travelers=[traveler("EMP-A")], freight=[row(name="FR1", traveler="EMP-A", carrier="ODFL")])
		self.assertEqual(len(completeness.travel_gaps(t)), 2)


class TestPlaces(unittest.TestCase):
	def test_a_picked_point_becomes_the_geojson_the_maps_read(self):
		geo = json.loads(planner.poi_geolocation(36.1147, -115.1728))
		self.assertEqual(geo["features"][0]["geometry"], {"type": "Point", "coordinates": [-115.1728, 36.1147]})

	def test_no_point_or_a_bad_one_stores_nothing(self):
		for lat, lng in ((None, None), ("", ""), (0, 0), (91, 10), ("x", 5)):
			self.assertIsNone(planner.poi_geolocation(lat, lng), (lat, lng))


class TestEmailLines(unittest.TestCase):
	def setUp(self):
		from erpnext_enhancements.travel_management import itinerary_text

		self.text = itinerary_text

	def test_times_read_on_a_12_hour_clock(self):
		clock = self.text.clock
		self.assertEqual(clock("07:15:00"), "7:15 AM")
		self.assertEqual(clock("2026-10-05 17:40:00"), "5:40 PM")
		self.assertEqual(clock("12:05:00"), "12:05 PM")
		# A datetime at midnight is "time not known yet"; a bare Time of midnight is real.
		self.assertEqual(clock("2026-10-05 00:00:00"), "")
		self.assertEqual(clock("00:00:00"), "12:00 AM")
		self.assertEqual(self.text.span("08:00:00", "12:00:00"), "8:00 AM – 12:00 PM")

	def test_every_booking_carries_its_number_or_says_it_is_missing(self):
		itinerary = {
			"days": [
				{
					"date": "2026-10-05",
					"items": [
						{
							"type": "flight",
							"airline": "Southwest",
							"flight_number": "WN 1234",
							"departure_airport": "PHX",
							"arrival_airport": "LAS",
							"departure_time": "2026-10-05 07:15:00",
							"booking_reference": "SW4KQ1",
						},
						{"type": "hotel_checkin", "hotel": "Hilton", "time": "15:00:00", "booking_confirmation": None},
						{
							"type": "ground",
							"transport_type": "Company Fleet",
							"pickup_location": "Shop",
							"dropoff_location": "Site",
							"pickup_datetime": "2026-10-05 06:00:00",
							"arrival_datetime": "2026-10-05 11:30:00",
							"cargo": "12 ft basin",
						},
						{"type": "agenda", "activity": "Walk the site", "time": "13:00:00", "end_time": "14:30:00"},
					],
				},
				{
					"date": "2026-10-06",
					"items": [
						{
							"type": "freight",
							"carrier": "Old Dominion",
							"delivery_from": "2026-10-06 08:00:00",
							"delivery_to": "2026-10-06 12:00:00",
							"tracking_number": "PRO 123",
						},
						{"type": "hotel_checkout", "hotel": "Hilton"},
					],
				},
			]
		}
		lines = self.text.booking_lines(itinerary)
		self.assertEqual(len(lines), 4)  # the stop and the check-out are not bookings
		self.assertEqual(lines[0], "✈ Southwest WN 1234 PHX → LAS, Mon Oct 5, departs 7:15 AM · PNR SW4KQ1")
		self.assertIn("from 3:00 PM · no confirmation number yet", lines[1])
		# Our own truck: nothing to confirm, so nothing flagged.
		self.assertIn("6:00 AM – 11:30 AM · hauling 12 ft basin", lines[2])
		self.assertNotIn("confirmation", lines[2])
		self.assertIn("delivers 8:00 AM – 12:00 PM · Tracking PRO 123", lines[3])

		days = self.text.day_lines(itinerary)
		self.assertEqual([d["date"] for d in days], ["2026-10-05", "2026-10-06"])
		self.assertIn("📍 1:00 PM – 2:30 PM · Walk the site", days[0]["lines"])


class FakeDoc(types.SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return False


class TestState(unittest.TestCase):
	def test_rows_of_one_booking_read_back_as_one_card(self):
		doc = FakeDoc(
			name="TRIP-1",
			modified="2026-09-23 12:00:00",
			status="Planning",
			purpose="Install",
			travel_type="Domestic",
			company="SF",
			start_date="2026-10-05",
			end_date="2026-10-08",
			travel_for_doctype=None,
			travel_for_name=None,
			billable=0,
			trip_description=None,
			travelers=[
				row(name="T1", employee="EMP-A", employee_name="Ann", is_trip_lead=1, from_date=None, to_date=None)
			],
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00", name="F1", booking_group="g1", cost=100),
				flight("EMP-B", "Outbound", "2026-10-05 07:00:00", name="F2", booking_group="g1", cost=50),
				flight("EMP-A", "Return", "2026-10-08 07:00:00", name="F3", booking_group=None, cost=90),
			],
			accommodations=[],
			ground_transport=[],
			mileage=[],
			itinerary=[],
		)
		state = planner.get_state(doc)
		cards = state["bookings"]["flights"]
		self.assertEqual([c["group"] for c in cards], ["g1", "row:F3"])
		self.assertEqual([m["traveler"] for m in cards[0]["members"]], ["EMP-A", "EMP-B"])
		self.assertEqual(cards[0]["values"]["cost"], 150)
		self.assertIn("gaps", state)


# --------------------------------------------------------------------------- contracts


def _read(path):
	with io.open(path, encoding="utf-8") as fh:
		return fh.read()


def _load_json(*parts):
	return json.loads(_read(os.path.join(*parts)))


class TestContracts(unittest.TestCase):
	def test_page_and_server_share_the_same_fields(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		block = re.search(r"const TP_SHARED = \{(.*?)\n\};", source, re.S).group(1)
		for table, spec in planner.BOOKING_TABLES.items():
			match = re.search(rf"{table}:\s*\[(.*?)\]", block, re.S)
			self.assertIsNotNone(match, table)
			js_fields = re.findall(r'"([a-z_]+)"', match.group(1))
			self.assertEqual(sorted(js_fields), sorted(spec["shared"]), table)

	def test_page_and_server_share_the_freight_fields(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		block = re.search(r"const TP_FREIGHT = \[(.*?)\];", source, re.S).group(1)
		self.assertEqual(re.findall(r'"([a-z_]+)"', block), list(planner.FREIGHT_FIELDS))
		meta = _load_json(TRAVEL_DIR, "doctype", "trip_freight", "trip_freight.json")
		fields = {f["fieldname"] for f in meta["fields"]}
		self.assertTrue(set(planner.FREIGHT_FIELDS) <= fields)

	def test_freight_is_a_cost_table_everywhere_money_is_counted(self):
		from erpnext_enhancements.travel_management import COST_TABLES

		self.assertEqual(COST_TABLES.get("freight"), "Trip Freight")
		trip_meta = _load_json(TRAVEL_DIR, "doctype", "travel_trip", "travel_trip.json")
		table = next(f for f in trip_meta["fields"] if f["fieldname"] == "freight")
		self.assertEqual((table["fieldtype"], table["options"]), ("Table", "Trip Freight"))
		# A cancelled claim must clear the stamp on freight rows too, or they stay
		# unclaimable forever.
		self.assertIn('"Trip Freight"', _read(os.path.join(TRAVEL_DIR, "integrations.py")))
		self.assertIn('"Trip Freight",', _read(os.path.join(TRAVEL_DIR, "api.py")))
		report = _read(os.path.join(TRAVEL_DIR, "report", "travel_spend_by_category", "travel_spend_by_category.py"))
		self.assertIn('("freight", "Trip Freight", "freight")', report)

	def test_the_time_range_fields_exist(self):
		expected = {
			"trip_ground_transport": {"arrival_datetime": "Datetime", "cargo": "Small Text"},
			"trip_accommodation": {"check_in_time": "Time", "check_out_time": "Time"},
			"trip_agenda": {"end_time": "Time"},
		}
		for doctype, wanted in expected.items():
			meta = _load_json(TRAVEL_DIR, "doctype", doctype, f"{doctype}.json")
			fields = {f["fieldname"]: f["fieldtype"] for f in meta["fields"]}
			for fieldname, fieldtype in wanted.items():
				self.assertEqual(fields.get(fieldname), fieldtype, f"{doctype}.{fieldname}")
				self.assertIn(fieldname, meta["field_order"])

	def test_every_method_the_page_calls_is_whitelisted(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		planner_source = _read(os.path.join(TRAVEL_DIR, "planner.py"))
		called = set(re.findall(r"travel_management\.planner\.(\w+)", source))
		self.assertEqual(called, {"get_plan", "get_recent_plans", "save_plan", "place_to_poi"})
		for method in called:
			self.assertRegex(planner_source, rf"@frappe\.whitelist\([^)]*\)\ndef {method}\(")

	def test_the_page_is_open_to_everyone_who_can_create_a_trip(self):
		page = _load_json(PAGE_DIR, "plan_a_trip.json")
		trip_meta = _load_json(TRAVEL_DIR, "doctype", "travel_trip", "travel_trip.json")
		creators = {p["role"] for p in trip_meta["permissions"] if p.get("create")}
		self.assertEqual({r["role"] for r in page["roles"]}, creators)
		self.assertEqual((page["name"], page["module"]), ("plan-a-trip", "Travel Management"))

	def test_the_child_tables_carry_the_new_fields(self):
		for doctype in ("trip_flight", "trip_ground_transport", "trip_accommodation", "trip_mileage"):
			meta = _load_json(TRAVEL_DIR, "doctype", doctype, f"{doctype}.json")
			fields = {f["fieldname"]: f for f in meta["fields"]}
			self.assertIn("booking_group", fields, doctype)
			self.assertIn("booking_group", meta["field_order"], doctype)
			if doctype in ("trip_flight", "trip_ground_transport"):
				self.assertEqual(fields["leg"]["options"].split("\n"), ["", "Outbound", "Return", "During Trip"])

	def test_ground_transport_offers_every_unbooked_type_and_no_longer_demands_a_vehicle(self):
		meta = _load_json(TRAVEL_DIR, "doctype", "trip_ground_transport", "trip_ground_transport.json")
		fields = {f["fieldname"]: f for f in meta["fields"]}
		options = set(fields["transport_type"]["options"].split("\n"))
		self.assertTrue(completeness.UNBOOKED_TRANSPORT <= options)
		# Neither vehicle master holds a single record on prod; a mandatory Vehicle would make
		# "Company vehicle" impossible to enter.
		self.assertNotIn("mandatory_depends_on", fields["vehicle"])

	def test_the_workspace_tile_opens_the_page(self):
		ws = _load_json(TRAVEL_DIR, "workspace", "travel_management", "travel_management.json")
		shortcut = next(s for s in ws["shortcuts"] if s["label"] == "Plan a Trip")
		self.assertEqual((shortcut["type"], shortcut["link_to"]), ("Page", "plan-a-trip"))
		names = [b["data"].get("shortcut_name") for b in json.loads(ws["content"]) if b["type"] == "shortcut"]
		self.assertIn("Plan a Trip", names)
		self.assertNotIn("New Travel Trip", names)
		# Age-gated on import: an unbumped stamp never reaches an existing site.
		self.assertGreater(ws["modified"], "2026-06-12 14:00:00.000000")

	def test_the_workspace_reload_patch_is_registered(self):
		patches = _read(os.path.join(APP_DIR, "patches.txt"))
		self.assertIn("erpnext_enhancements.patches.reload_travel_workspace_for_plan_a_trip", patches)
		self.assertLess(patches.index("[post_model_sync]"), patches.index("reload_travel_workspace_for_plan_a_trip"))

	def test_node_check(self):
		self._node_check()

	def _node_check(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		for path in (
			os.path.join(PAGE_DIR, "plan_a_trip.js"),
			os.path.join(APP_DIR, "public", "js", "travel_trip.js"),
			os.path.join(APP_DIR, "public", "js", "travel", "travel_trip_list.js"),
			os.path.join(APP_DIR, "public", "js", "travel", "itinerary.js"),
		):
			result = subprocess.run([node, "--check", path], capture_output=True, text=True, check=False)
			self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")


class TestPageRouting(unittest.TestCase):
	"""v1.520.0 shipped a page whose buttons only redrew the landing ("it just refreshes").

	In frappe v16, ``frappe.set_route("plan-a-trip", {trip: X})`` does not put ``?trip=X``
	in the address bar: ``make_url`` moves the object into ``frappe.route_options`` and
	``push_state`` writes the path alone. The page read only ``location.search``, so every
	route it was sent to looked empty. The browser harness that "passed" had faked
	``set_route`` to write the query string, which is exactly the behaviour frappe lacks.
	"""

	def setUp(self):
		self.source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		# Absence checks run on code only: the comments explaining each rule quote the very
		# call they forbid.
		self.code = _strip_js_comments(self.source)

	def test_the_page_reads_frappe_route_options(self):
		body = re.search(r"\n\troute_args\(\) \{(.*?)\n\t\}\n", self.source, re.S)
		self.assertIsNotNone(body, "route_args() is gone")
		self.assertIn("frappe.route_options", body.group(1))
		# ...and consumes it, or a later plain visit replays the last trip.
		self.assertIn("delete options[key]", body.group(1))

	def test_the_page_never_routes_to_itself(self):
		self.assertIsNone(re.search(r"frappe\.set_route\(\s*[\"']plan-a-trip", self.code))

	def test_the_route_is_handled_once_per_show(self):
		# frappe fires on_page_show right after on_page_load; handling the route in the
		# constructor too consumed route_options twice and raced two renders.
		self.assertEqual(self.code.count(".handle_route()"), 1)
		self.assertIn("wrapper.trip_planner.handle_route()", self.code)

	def test_step_changes_are_history_entries(self):
		# Every address write used to be replaceState, so Back from any step left the page. A
		# move the user makes pushes; one Back/Forward asked for (go's from_route) never does.
		self.assertIn("window.history.pushState(", self.code)
		self.assertRegex(self.code, r"\n\tgo\(index, from_route\) \{")
		self.assertIn("this.show_step(index, !from_route)", self.code)
		self.assertIn("this.set_address(this.address_args(), push)", self.code)

	def test_back_is_never_held_up(self):
		# Back used to go through the same blocking save as Next: one half-finished card showed
		# "A few things to fill in first" on every press of Back and overwrote each entry it
		# refused. A move back saves quietly and moves whatever the answer.
		body = re.search(r"\n\tgo\(index, from_route\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(body, "go() is gone")
		back = re.search(
			r"if \(index < this\.step \|\| \(from_route && this\.route_delta > 0\)\) \{(.*?)\n\t\t\}",
			body.group(1),
			re.S,
		)
		self.assertIsNotNone(back, "go() no longer lets a move back through before its checks")
		self.assertIn("this.save({ quiet: true })", back.group(1))
		self.assertNotIn("if (!ok) return", back.group(1))

	def test_a_refused_forward_says_why_after_going_back(self):
		# frappe closes any open dialog on every route change, so a refusal shown before the
		# history.go that undoes the move would vanish unread: it is left for handle_route.
		body = re.search(r"\n\trefuse_route\(delta, say\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(body, "refuse_route() is gone")
		code = body.group(1)
		self.assertLess(code.index("this.return_notice ="), code.index("window.history.go(delta)"))
		self.assertNotIn("say();\n\t\t\twindow.history.go", code)

	def test_the_pages_own_entries_are_told_apart(self):
		# Back onto a "?new=1" entry the page wrote must reopen the trip started there (saved
		# since, with a name), not start a blank one: that entry's history.state says which.
		body = re.search(r"\n\thandle_route\(\) \{(.*?)\n\t\}\n", self.source, re.S)
		self.assertIsNotNone(body, "handle_route() is gone")
		self.assertIn("this.history_mark()", body.group(1))

	def test_back_and_forward_executed(self):
		# The behaviour itself: the real page, run against a port of the v16 router, by
		# scripts/test_wizard_back_forward.mjs. Run from here because a node step of its own
		# would need its own line in ci.yml.
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		harness = os.path.join(os.path.dirname(APP_DIR), "scripts", "test_wizard_back_forward.mjs")
		result = subprocess.run(
			[node, harness, "plan-a-trip"],
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
			check=False,
			timeout=120,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn(" 0 failed", result.stdout)

	def test_no_hand_built_app_links(self):
		# The v16 desk is /desk; the router only intercepts /desk links, so an in-desk
		# href="/app/..." costs a full reload and a redirect.
		for path in (
			os.path.join(PAGE_DIR, "plan_a_trip.js"),
			os.path.join(APP_DIR, "public", "js", "travel_trip.js"),
			os.path.join(APP_DIR, "public", "js", "travel", "travel_trip_list.js"),
		):
			self.assertIsNone(re.search(r"[\"'`]/app/", _strip_js_comments(_read(path))), path)

	def test_the_guards_catch_the_version_that_shipped(self):
		# Each check above, run against the v1.520.0 shape of the page, must fire; a guard
		# that cannot fail guards nothing.
		shipped = (
			"class TripPlanner {\n\tconstructor() {\n\t\tthis.handle_route();\n\t}\n"
			"\thandle_route() { this.route(); }\n"
			"\troute() { const name = frappe.utils.get_url_arg(\"trip\"); }\n"
			"\trender_landing() { frappe.set_route(\"plan-a-trip\", { new: 1 }); }\n}\n"
			"frappe.pages[\"plan-a-trip\"].on_page_show = function (wrapper) {\n"
			"\twrapper.trip_planner.handle_route();\n};\n"
		)
		code = _strip_js_comments(shipped)
		self.assertIsNotNone(re.search(r"frappe\.set_route\(\s*[\"']plan-a-trip", code))
		self.assertNotEqual(code.count(".handle_route()"), 1)
		self.assertIsNone(re.search(r"\n\troute_args\(\) \{", code))


def _strip_js_comments(source):
	"""Drop // line comments and /* */ blocks (good enough for these files: none of them
	carries '//' or '/*' inside a string that matters here)."""
	source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
	return re.sub(r"(^|[^:\"'`])//[^\n]*", r"\1", source)


class TestItineraryBackForward(unittest.TestCase):
	"""/itinerary: the trip on screen is ?trip=<name>, a chip tap is one history entry, and
	Back / Forward load the entry's trip. Before, Back after switching trips left the page (or
	the home-screen app), and a reload or Back from /travel_guidelines showed the default trip
	instead of the one being read."""

	ITINERARY_JS = os.path.join(APP_DIR, "public", "js", "travel", "itinerary.js")
	CONTROLLER = os.path.join(APP_DIR, "www", "itinerary.py")

	def _login_redirect(self):
		"""The real ``login_redirect``, extracted with ast: importing the controller pulls in
		api.travel and the kiosk controller, far more than this suite's stub provides."""
		import ast
		from urllib.parse import quote

		tree = ast.parse(_read(self.CONTROLLER))
		wanted = [
			n
			for n in tree.body
			if (isinstance(n, ast.FunctionDef) and n.name == "login_redirect")
			or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "ROUTE" for t in n.targets))
		]
		namespace = {"quote": quote}
		exec(compile(ast.Module(body=wanted, type_ignores=[]), self.CONTROLLER, "exec"), namespace)
		return namespace["login_redirect"]

	def test_the_login_redirect_keeps_the_trip(self):
		login_redirect = self._login_redirect()
		# A bare visit reads exactly as it always has.
		self.assertEqual(login_redirect("/itinerary?"), "/login?redirect-to=/itinerary")
		self.assertEqual(login_redirect(None), "/login?redirect-to=/itinerary")
		# The trip comes back after login, encoded whole so the login page cannot split it.
		self.assertEqual(
			login_redirect("/itinerary?trip=TRIP-0007&x=1"),
			"/login?redirect-to=/itinerary%3Ftrip%3DTRIP-0007%26x%3D1",
		)
		# Never anywhere but this page.
		self.assertEqual(login_redirect("//evil.example/x"), "/login?redirect-to=/itinerary")

	def test_history_calls_come_only_from_the_tap_and_the_boot(self):
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		self.assertNotIn("beforeunload", code)
		# One push, in writeTripEntry, called with push=true only from the chip's click.
		self.assertEqual(code.count("pushState("), 1)
		self.assertEqual(code.count("writeTripEntry(true"), 1)
		self.assertRegex(
			code,
			r"chip\.addEventListener\('click', function \(\) \{\s*"
			r"if \(trip\.name !== state\.currentTrip\) writeTripEntry\(true, trip\.name\);",
		)
		# loadTrip, which popstate calls, never writes history.
		body = code[code.index("function loadTrip(") : code.index("function defaultTrip(")]
		self.assertNotIn("writeTripEntry", body)
		self.assertNotIn("State(", body)

	def test_the_page_in_a_fake_browser(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		harness = os.path.join(os.path.dirname(APP_DIR), "scripts", "test_web_flow_history.js")
		result = subprocess.run(
			[node, harness, "itinerary"], capture_output=True, text=True, check=False, timeout=120
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
	unittest.main()

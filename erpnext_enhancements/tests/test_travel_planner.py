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
6. **A checklist that asks for what was never needed** (``TestTheKaptureTrip``): a bed for
   someone out and back on one day, half a room's cost for a guest staying free in it, and a
   cost on the flight home of a round-trip ticket, whose fare is one charge on the way there.

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
from unittest import mock

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
		is_trip_lead=0,
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


# The Travel Trip form's checklist headline, executed: public/js/travel_trip.js is run in a node
# vm with just enough of frappe to load it, and show_trip_checklist is called once per list of
# gaps (as __onload.trip_gaps). Prints, per case, every headline call as {html, color}.
_FORM_HEADLINE_JS = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const escape = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const ctx = {
	__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args || [])[i]),
	cint: (v) => parseInt(v, 10) || 0,
	frappe: { ui: { form: { on() {} } }, utils: { escape_html: escape } },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(input.path, "utf8"), ctx, { filename: input.path });
const out = input.cases.map((gaps) => {
	const shown = [];
	ctx.show_trip_checklist({
		doc: { name: "TRIP-2026-00001", status: "Planning", __onload: { trip_gaps: gaps } },
		is_new: () => false,
		dashboard: {
			set_headline_alert: (html, color) => shown.push({ html: String(html), color: color }),
			clear_headline: () => shown.push({ html: "", color: "" }),
		},
	});
	return shown;
});
process.stdout.write(JSON.stringify(out));
"""


def form_headlines(cases):
	node = shutil.which("node")
	if not node:
		raise unittest.SkipTest("node is not installed")
	result = subprocess.run(
		[node, "-e", _FORM_HEADLINE_JS],
		input=json.dumps({"path": os.path.join(APP_DIR, "public", "js", "travel_trip.js"), "cases": cases}),
		capture_output=True,
		text=True,
		encoding="utf-8",
		check=False,
		timeout=60,
	)
	if result.returncode:
		raise AssertionError(result.stderr)
	return json.loads(result.stdout)


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
		# Paperwork (trip files, 2026-09-26) is the fifth check and runs last.
		t = trip(
			accommodations=[
				stay(None, "2026-10-05", "2026-10-08", ref="", cost=0),
				stay(None, None, None, name="R9", booking_group="r9"),
			],
			flights=[flight("EMP-A", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref="ABC")],
		)
		every = ["travel", "lodging", "confirmation", "cost", "documents"]
		order = [g["check"] for g in completeness.find_gaps(t)]
		self.assertEqual(order, sorted(order, key=every.index))
		self.assertEqual(set(order), set(every))


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


# --------------------------------------------------------------------------- beds, guests, fares


class TestTheKaptureTrip(unittest.TestCase):
	"""TRIP-2026-00001 as it stood on prod on 2026-09-25, and what the office said its checklist
	got wrong. Four people: two for the week; one out at 7:20 AM and home at 3:45 PM the same
	day, but down on the crew step for the whole trip; one for a single night, staying free in a
	colleague's upgraded room. Every flight home is the second half of a round-trip ticket with
	the fare on the way there, so the flights home have no cost.

	Before: the checklist wanted five nights of hotel for the day-tripper, a room for the
	one-nighter (and adding him to the room would have split its cost in half), and a cost on
	all four flights home."""

	def real_trip(self):
		return trip(
			start_date="2026-09-27",
			end_date="2026-10-02",
			travelers=[
				traveler("EMP-K", "K", "2026-09-27", "2026-10-02"),
				traveler("EMP-J", "J", "2026-09-27", "2026-10-02"),
				traveler("EMP-B", "B", "2026-09-27", "2026-10-02"),
				traveler("EMP-L", "L", "2026-09-28", "2026-09-29"),
			],
			flights=[
				flight(
					"EMP-K", "Outbound", "2026-09-27 17:10:00", booking_group="k1", ref="KKK111", cost=351.8
				),
				flight(
					"EMP-J", "Outbound", "2026-09-27 17:10:00", booking_group="j1", ref="JJJ222", cost=326.8
				),
				flight(
					"EMP-B", "Outbound", "2026-09-28 07:20:00", booking_group="b1", ref="BBB333", cost=261.81
				),
				flight(
					"EMP-L", "Outbound", "2026-09-28 07:20:00", booking_group="l1", ref="LLL444", cost=732.8
				),
				flight("EMP-B", "Return", "2026-09-28 15:45:00", booking_group="b2", ref="BBB333", cost=0),
				flight("EMP-L", "Return", "2026-09-29 07:30:00", booking_group="l2", ref="LLL444", cost=0),
				flight("EMP-K", "Return", "2026-10-02 13:36:00", booking_group="k2", ref="KKK111", cost=0),
				flight("EMP-J", "Return", "2026-10-02 13:36:00", booking_group="j2", ref="JJJ222", cost=0),
			],
			accommodations=[
				stay(
					"EMP-J",
					"2026-09-27",
					"2026-10-02",
					name="R1",
					booking_group="jroom",
					ref="H-1001",
					cost=942.88,
				),
				stay(
					"EMP-K",
					"2026-09-27",
					"2026-10-02",
					name="R2",
					booking_group="kroom",
					ref="H-1001",
					cost=942.88,
				),
			],
		)

	def test_the_day_tripper_needs_no_bed(self):
		t = self.real_trip()
		brian = t.travelers[2]
		start, end = completeness.stay_window(t, brian)
		self.assertEqual((start, end), (date(2026, 9, 28), date(2026, 9, 28)))
		gaps = checks(completeness.lodging_gaps(t), "lodging")
		self.assertEqual([(g["employee"], g["nights"]) for g in gaps], [("EMP-L", ["2026-09-28"])])

	def test_the_flights_home_need_no_cost(self):
		self.assertEqual(completeness.cost_gaps(self.real_trip()), [])

	def with_the_guest(self):
		"""The trip once the one-nighter is ticked into his colleague's room as a guest: the
		rows ``merge_bookings`` writes, with ``fit_guest_stays`` run."""
		t = self.real_trip()
		cards = [
			card(
				"jroom",
				{
					"hotel_lodging": "The Guild Hotel",
					"check_in_date": "2026-09-27",
					"check_out_date": "2026-10-02",
					"cost": 942.88,
				},
				[
					{"name": "R1", "traveler": "EMP-J", "ref": "H-1001"},
					{"traveler": "EMP-L", "ref": "H-1001", "guest": 1},
				],
			),
			card("kroom", {"cost": 942.88}, [{"name": "R2", "traveler": "EMP-K", "ref": "H-1001"}]),
		]
		rows, notes = planner.merge_bookings(t.accommodations, cards, "accommodations")
		self.assertEqual(notes, [])
		t.accommodations = rows
		planner.fit_guest_stays(t)
		return t

	def test_the_guest_fills_the_last_gap_and_pays_nothing(self):
		t = self.with_the_guest()
		jesse, logan, korben = t.accommodations
		self.assertEqual((jesse.cost, logan["cost"], korben.cost), (942.88, 0.0, 942.88))
		self.assertEqual(logan["guest"], 1)
		# His own night, so his itinerary does not have him checking in before he flies out.
		self.assertEqual((logan["check_in_date"], logan["check_out_date"]), ("2026-09-28", "2026-09-29"))
		self.assertEqual((jesse.check_in_date, jesse.check_out_date), ("2026-09-27", "2026-10-02"))
		# Changed on purpose twice with trip files (was: find_gaps == []). The checklist gained a
		# fifth check, paperwork, and this trip has every confirmation number and no files, so
		# each booking asks for its own: eight flights (one person each) and two rooms. Then Nik
		# made paperwork a separate, quieter tally: the checklist still COUNTS nothing on this
		# trip, as it did after 1.544.0, and the ten are counted apart, as files.
		gaps = completeness.find_gaps(t)
		self.assertEqual(completeness.counted_gaps(gaps), [])
		paperwork = completeness.paperwork_gaps(gaps)
		self.assertEqual(len(paperwork), 10)
		self.assertEqual(
			sorted(g["group"] for g in paperwork),
			sorted(["k1", "j1", "b1", "l1", "b2", "l2", "k2", "j2", "jroom", "kroom"]),
		)
		self.assertEqual(completeness.files_not_attached(gaps), 10)
		# Still data: every consumer gets them, told apart by their check.
		self.assertEqual(len(gaps), 10)
		self.assertTrue(all(completeness.is_paperwork(g) for g in gaps))

	def test_the_forms_headline_reads_all_set_with_a_quiet_files_line(self):
		# travel_trip.js, run: nothing counted is missing, so the headline is green and says so;
		# the ten are one muted line under it, linked to the Files step. Before the quieter
		# tally it read "Trip checklist: 10 missing paperwork" in orange.
		(shown,) = form_headlines([completeness.find_gaps(self.with_the_guest())])
		self.assertEqual(len(shown), 1)
		html, color = shown[0]["html"], shown[0]["color"]
		self.assertEqual(color, "green")
		self.assertTrue(html.startswith("Trip checklist: nothing missing."), html)
		self.assertIn("10 files not attached yet", html)
		self.assertIn('href="/desk/plan-a-trip?trip=TRIP-2026-00001&step=files"', html)
		self.assertIn('class="small text-muted"', html)
		self.assertNotIn("paperwork", html.lower())


class TestStayWindow(unittest.TestCase):
	def test_travel_only_narrows_the_dates(self):
		t = trip(
			travelers=[traveler("EMP-A", from_date="2026-10-06", to_date="2026-10-07")],
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00"),
				flight("EMP-A", "Return", "2026-10-08 18:00:00"),
			],
		)
		self.assertEqual(completeness.stay_window(t, t.travelers[0]), (date(2026, 10, 6), date(2026, 10, 7)))

	def test_a_late_flight_and_an_early_one_home(self):
		t = trip(
			travelers=[traveler("EMP-A")],
			flights=[
				flight("EMP-A", "Outbound", "2026-10-06 07:00:00"),
				flight("EMP-A", "Return", "2026-10-07 18:00:00"),
			],
		)
		self.assertEqual(completeness.stay_window(t, t.travelers[0]), (date(2026, 10, 6), date(2026, 10, 7)))
		self.assertEqual(checks(completeness.lodging_gaps(t), "lodging")[0]["nights"], ["2026-10-06"])

	def test_the_earliest_leg_counts_and_other_peoples_travel_does_not(self):
		t = trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-06 07:00:00"),
				flight("EMP-A", "Outbound", "2026-10-07 07:00:00"),  # a connection
				flight("EMP-B", "Return", "2026-10-05 18:00:00"),  # somebody else's
			],
		)
		self.assertEqual(completeness.stay_window(t, t.travelers[0]), (date(2026, 10, 6), date(2026, 10, 8)))

	def test_undated_or_getting_around_travel_changes_nothing(self):
		t = trip(
			ground_transport=[
				row(traveler=None, leg="Outbound", transport_type="Company Fleet", pickup_datetime=None),
				row(
					traveler=None,
					leg="During Trip",
					transport_type="Rental/Third Party",
					pickup_datetime="2026-10-06 09:00:00",
				),
			],
		)
		self.assertEqual(completeness.stay_window(t, t.travelers[0]), (date(2026, 10, 5), date(2026, 10, 8)))

	def test_the_page_uses_the_same_rule(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		body = re.search(r"\n\tstay_window\(traveler\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		self.assertIn('["flights", "ground_transport"]', body)
		self.assertIn("first.Outbound > from", body)
		self.assertIn("first.Return < to", body)
		draw = re.search(r"\n\tdraw_nights\(\$matrix\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		self.assertIn("this.stay_window(traveler)", draw)


class TestRoundTripFares(unittest.TestCase):
	def test_a_flight_home_on_the_same_ticket_needs_no_cost(self):
		t = trip(
			flights=[
				flight(
					"EMP-A", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref="ABC123", cost=400
				),
				flight("EMP-A", "Return", "2026-10-08 18:00:00", booking_group="g2", ref=" abc123 ", cost=0),
			]
		)
		self.assertEqual(completeness.cost_gaps(t), [])

	def test_a_separate_one_way_ticket_still_needs_its_cost(self):
		t = trip(
			flights=[
				flight(
					"EMP-A", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref="ABC123", cost=400
				),
				flight("EMP-A", "Return", "2026-10-08 18:00:00", booking_group="g2", ref="XYZ789", cost=0),
			]
		)
		self.assertEqual([g["group"] for g in completeness.cost_gaps(t)], ["g2"])

	def test_no_fare_anywhere_flags_both_halves(self):
		t = trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref="ABC123", cost=0),
				flight("EMP-A", "Return", "2026-10-08 18:00:00", booking_group="g2", ref="ABC123", cost=0),
			]
		)
		self.assertEqual([g["group"] for g in completeness.cost_gaps(t)], ["g1", "g2"])

	def test_everyone_on_the_flight_needs_a_paid_ticket(self):
		t = trip(
			flights=[
				flight(
					"EMP-A", "Outbound", "2026-10-05 07:00:00", booking_group="g1", ref="ABC123", cost=400
				),
				flight("EMP-A", "Return", "2026-10-08 18:00:00", booking_group="g2", ref="ABC123", cost=0),
				flight("EMP-B", "Return", "2026-10-08 18:00:00", booking_group="g2", ref="", cost=0),
			]
		)
		self.assertEqual([g["group"] for g in completeness.cost_gaps(t)], ["g2"])

	def test_rooms_sharing_a_confirmation_still_each_need_a_cost(self):
		# Only flights: two rooms on one hotel confirmation are still two charges.
		t = trip(
			accommodations=[
				stay("EMP-A", "2026-10-05", "2026-10-08", booking_group="r1", ref="H1", cost=500),
				stay("EMP-B", "2026-10-05", "2026-10-08", booking_group="r2", ref="H1", cost=0),
			]
		)
		self.assertEqual([g["group"] for g in completeness.cost_gaps(t)], ["r2"])

	def test_the_page_uses_the_same_rule_and_copies_the_confirmation_home(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		body = re.search(r"\n\tfare_card\(card\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		self.assertIn('card.table !== "flights" || flt(card.values.cost)', body)
		self.assertIn("refs.some((ref) => !ref)", body)
		self.assertIn("toUpperCase()", body)
		copy = re.search(r"\n\tcopy_reversed\(\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		self.assertIn("member.ref = source ? source.ref", copy)


class TestRoomGuests(unittest.TestCase):
	def test_a_guest_pays_no_share_and_sorts_after_the_room(self):
		cards = [
			card(
				"g1",
				{"hotel_lodging": "Hilton", "cost": 300},
				[{"traveler": "EMP-B", "guest": 1}, {"traveler": "EMP-A"}, {"traveler": "EMP-C"}],
				changed=["hotel_lodging", "cost"],
				origin="new:1",
			)
		]
		rows, _notes = planner.merge_bookings([], cards, "accommodations")
		self.assertEqual(
			[(r["traveler"], r["guest"], r["cost"]) for r in rows],
			[
				("EMP-A", 0, 150.0),
				("EMP-C", 0, 150.0),
				("EMP-B", 1, 0.0),
			],
		)

	def test_making_someone_a_guest_resplits_without_a_cost_edit(self):
		a = row(name="H1", traveler="EMP-A", booking_group="g1", cost=150, guest=0)
		b = row(name="H2", traveler="EMP-B", booking_group="g1", cost=150, guest=0)
		cards = [
			card(
				"g1",
				{"cost": 300},
				[{"name": "H1", "traveler": "EMP-A"}, {"name": "H2", "traveler": "EMP-B", "guest": 1}],
			)
		]
		planner.merge_bookings([a, b], cards, "accommodations")
		self.assertEqual((a.cost, b.cost, b.guest), (300.0, 0.0, 1))

	def test_a_room_of_guests_only_splits_as_usual(self):
		cards = [
			card(
				"g1",
				{"cost": 200},
				[{"traveler": "EMP-A", "guest": 1}, {"traveler": "EMP-B", "guest": 1}],
				origin="new:1",
			)
		]
		rows, _notes = planner.merge_bookings([], cards, "accommodations")
		self.assertEqual([r["cost"] for r in rows], [100.0, 100.0])

	def test_only_rooms_have_guests(self):
		cards = [card("g1", {"cost": 200}, [{"traveler": "EMP-A", "guest": 1}], origin="new:1")]
		rows, _notes = planner.merge_bookings([], cards, "flights")
		self.assertNotIn("guest", rows[0])
		self.assertEqual(rows[0]["cost"], 200.0)

	def test_a_guest_outside_the_room_keeps_the_rooms_dates(self):
		t = trip(
			travelers=[traveler("EMP-A"), traveler("EMP-B", from_date="2026-10-08", to_date="2026-10-08")],
			accommodations=[
				stay("EMP-A", "2026-10-05", "2026-10-07", booking_group="g1"),
				stay("EMP-B", "2026-10-05", "2026-10-07", booking_group="g1", guest=1),
			],
		)
		planner.fit_guest_stays(t)
		guest = t.accommodations[1]
		self.assertEqual((guest.check_in_date, guest.check_out_date), ("2026-10-05", "2026-10-07"))

	def test_the_card_reads_the_rooms_dates_not_the_guests(self):
		doc = FakeDoc(
			travelers=[],
			accommodations=[
				stay("EMP-B", "2026-10-06", "2026-10-07", name="H2", booking_group="g1", guest=1, cost=0),
				stay("EMP-A", "2026-10-05", "2026-10-08", name="H1", booking_group="g1", guest=0, cost=300),
			],
		)
		(room,) = planner._cards(doc, "accommodations", {})
		self.assertEqual(
			(room["values"]["check_in_date"], room["values"]["check_out_date"]), ("2026-10-05", "2026-10-08")
		)
		self.assertEqual([m["guest"] for m in room["members"]], [1, 0])

	def test_the_page_sends_the_guest_flag_and_the_field_exists(self):
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		payload = re.search(r"\n\tpayload\(\) \{(.*?)\n\t\}\n", source, re.S).group(1)
		self.assertIn('table === "accommodations" ? { guest: m.guest ? 1 : 0 } : {}', payload)
		meta = _load_json(TRAVEL_DIR, "doctype", "trip_accommodation", "trip_accommodation.json")
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual((fields["guest"]["fieldtype"], fields["guest"]["default"]), ("Check", "0"))
		self.assertIn("guest", meta["field_order"])
		# Child doctype JSON is age-gated on migrate: the new field needs a newer stamp.
		self.assertGreater(meta["modified"], "2026-09-23 15:00:00.000000")


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

	def test_every_shipment_gets_an_id_of_its_own_and_keeps_it(self):
		# A file (a bill of lading) belongs to a shipment by its booking_group, so a shipment
		# needs one that does not change: minted once, then kept whatever the page sends.
		legacy = row(name="FR1", carrier="ODFL", booking_group=None, cost=0)
		saved = row(name="FR2", carrier="Estes", booking_group="keepme123456", cost=0)
		group_map = {}
		rows, _notes = planner.merge_freight(
			[legacy, saved],
			[
				{"name": "FR1", "carrier": "ODFL", "booking_group": ""},
				{"name": "FR2", "carrier": "Estes", "booking_group": "otherid99999"},
				{"name": None, "carrier": "Saia", "booking_group": "new:3"},
				{"name": None, "carrier": "XPO", "booking_group": "safeid777777"},
			],
			group_map,
		)
		minted_legacy, kept, minted_new, safe = (planner._get(r, "booking_group") for r in rows)
		self.assertRegex(minted_legacy, r"^[0-9a-f]{12}$")
		self.assertEqual(kept, "keepme123456")
		self.assertRegex(minted_new, r"^[0-9a-f]{12}$")
		self.assertEqual(safe, "safeid777777")
		self.assertEqual(
			group_map,
			{
				"row:FR1": minted_legacy,
				"otherid99999": "keepme123456",
				"new:3": minted_new,
				"safeid777777": "safeid777777",
			},
		)
		# ...and the next save keeps what this one stored.
		again, _notes = planner.merge_freight(rows, [{"name": "FR1", "carrier": "ODFL", "booking_group": ""}])
		self.assertEqual(legacy.booking_group, minted_legacy)
		self.assertEqual(again, [legacy])

	def test_two_shipments_never_share_an_id(self):
		rows, _notes = planner.merge_freight(
			[],
			[
				{"name": None, "carrier": "ODFL", "booking_group": "same00000001"},
				{"name": None, "carrier": "ODFL", "booking_group": "same00000001"},
			],
		)
		self.assertEqual(rows[0]["booking_group"], "same00000001")
		self.assertNotEqual(rows[1]["booking_group"], "same00000001")
		# A new one sent first with a saved shipment's id does not take it from that shipment.
		saved = row(name="FR1", carrier="ODFL", booking_group="saved0000001", cost=0)
		rows, _notes = planner.merge_freight(
			[saved],
			[
				{"name": None, "carrier": "Estes", "booking_group": "saved0000001"},
				{"name": "FR1", "carrier": "ODFL", "booking_group": "saved0000001"},
			],
		)
		self.assertNotEqual(rows[0]["booking_group"], "saved0000001")
		self.assertEqual(saved.booking_group, "saved0000001")

	def test_the_checklist_keys_a_shipment_by_its_id(self):
		t = trip(freight=[row(name="FR1", carrier="ODFL", tracking_number="", cost=0, booking_group="s1s1s1")])
		self.assertEqual([g["group"] for g in completeness.confirmation_gaps(t)], ["s1s1s1"])

	def test_a_shipment_duplicated_on_the_form_does_not_take_the_originals_files(self):
		# The form's grid Duplicate copies the hidden booking_group too (v16 duplicate_row
		# ignores no_copy): two shipments arrive under one id. The page sends both with it. The
		# copy is given a new id, and the original's bill of lading must stay on the original —
		# the last write to group_map used to win, and it moved to the copy.
		doc = files_trip(
			freight=[
				row(name="FR1", carrier="ODFL", tracking_number="PRO1", booking_group="aaaaaaaaaaaa", traveler=None, cost=500),
				row(name="FR2", carrier="ODFL", tracking_number="PRO2", booking_group="aaaaaaaaaaaa", traveler=None, cost=0),
			],
			documents=[doc_row("D1", "Bill of lading", "/f/bol1.pdf", "aaaaaaaaaaaa", title="BOL 1")],
		)
		plan = {
			"freight": [
				{"name": "FR1", "carrier": "ODFL", "tracking_number": "PRO1", "booking_group": "aaaaaaaaaaaa"},
				{"name": "FR2", "carrier": "ODFL", "tracking_number": "PRO2", "booking_group": "aaaaaaaaaaaa"},
			],
			"documents": [
				{"name": "D1", "title": "BOL 1", "kind": "Bill of lading", "file": "/f/bol1.pdf", "booking_group": "aaaaaaaaaaaa"}
			],
		}
		notes = planner.apply_plan(doc, plan)
		self.assertEqual(notes, [])
		original, copy = doc.freight
		self.assertEqual(original.booking_group, "aaaaaaaaaaaa")
		self.assertRegex(copy.booking_group, r"^[0-9a-f]{12}$")
		self.assertNotEqual(copy.booking_group, "aaaaaaaaaaaa")
		self.assertEqual([(d.booking_group, d.booking_label) for d in doc.documents], [("aaaaaaaaaaaa", "ODFL PRO1")])
		# A key a shipment kept as its id stays its own, whichever order they are sent in.
		group_map = {}
		planner.merge_freight(
			[row(name="FR1", carrier="ODFL", booking_group="saved0000001", cost=0)],
			[
				{"name": None, "carrier": "Estes", "booking_group": "saved0000001"},
				{"name": "FR1", "carrier": "ODFL", "booking_group": "saved0000001"},
			],
			group_map,
		)
		self.assertEqual(group_map, {"saved0000001": "saved0000001"})

	def test_the_controller_clears_a_copied_shipments_id(self):
		# What the controller runs on every save (TravelTrip._validate_trip_files): the later of
		# two shipments sharing an id gives it up, and reads as its own row until the page
		# gives it one. Bookings are not touched: sharing an id is how a flight holds four.
		first = row(name="FR1", booking_group="aaaaaaaaaaaa")
		copy = row(name="FR2", booking_group="aaaaaaaaaaaa")
		alone = row(name="FR3", booking_group=None)
		other = row(name="FR4", booking_group="bbbbbbbbbbbb")
		third = row(name="FR5", booking_group="aaaaaaaaaaaa")
		self.assertEqual(completeness.repeated_freight_groups([first, copy, alone, other, third]), [copy, third])
		self.assertEqual(completeness.repeated_freight_groups(None), [])
		# Read through the checklist, the copy stands alone once cleared.
		copy.booking_group = None
		self.assertEqual([completeness.group_key(r) for r in (first, copy)], ["aaaaaaaaaaaa", "row:FR2"])


# --------------------------------------------------------------------------- trip files


def doc_row(name, kind, url, booking_group=None, traveler=None, **kwargs):
	return row(
		name=name, kind=kind, file=url, booking_group=booking_group, traveler=traveler, **kwargs
	)


class FakeTrip(types.SimpleNamespace):
	"""Enough of a Travel Trip for apply_plan: tables that turn dict rows into objects, the way
	``Document.append`` makes a child row of them."""

	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return not self.name

	def set(self, key, value):
		setattr(self, key, list(value))

	def append(self, key, value):
		if isinstance(value, dict):
			value = row(**value)
		getattr(self, key).append(value)
		return value


def files_trip(**kwargs):
	base = dict(
		name="TRIP-1",
		company="SF",
		status="Planning",
		start_date="2026-10-05",
		end_date="2026-10-08",
		travelers=[traveler("EMP-A", "Ann"), traveler("EMP-B", "Bo")],
		flights=[],
		accommodations=[],
		ground_transport=[],
		mileage=[],
		freight=[],
		other_costs=[],
		itinerary=[],
		documents=[],
	)
	base.update(kwargs)
	return FakeTrip(**base)


def attached(*urls, public=()):
	"""A stand-in for the File check (``planner._file_on_trip``): these URLs are attached to
	the trip as private files, ``public`` ones as public files, and nothing else is (None)."""
	return lambda url: True if url in urls else (False if url in public else None)


class TestDocuments(unittest.TestCase):
	"""Trip files: a boarding pass, a hotel confirmation, a bill of lading, or a file for the
	whole trip, one Trip Document row each (``planner.merge_documents``). What must not
	happen: a file for someone not on the trip, a file pointed at a booking that is not there,
	a URL that is not this trip's upload, a receipt shown to the crew, or a file left pointing
	at the page's temporary key for a booking."""

	CREW = {"EMP-A", "EMP-B"}

	def flight_trip(self, **kwargs):
		return files_trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00", name="F1", booking_group="g1flight", ref="A1"),
				flight("EMP-B", "Outbound", "2026-10-05 07:00:00", name="F2", booking_group="g1flight", ref="B1"),
			],
			**kwargs,
		)

	def merge(self, doc, documents, group_map=None, check=None):
		return planner.merge_documents(
			doc, documents, group_map or {}, self.CREW, file_check=check or attached(*[d["file"] for d in documents])
		)

	def test_add_update_and_drop_by_name(self):
		old = doc_row("D1", "Other", "/private/files/old.pdf", title="Old")
		gone = doc_row("D2", "Site map", "/private/files/map.png", title="Map")
		doc = self.flight_trip(documents=[old, gone])
		rows, notes = self.merge(
			doc,
			[
				{"name": "D1", "title": "Job packet", "kind": "Job packet", "file": "/private/files/old.pdf"},
				{"name": None, "title": "", "kind": "Boarding pass", "file": "/private/files/pass.png",
					"traveler": "EMP-B", "booking_group": "g1flight"},
			],
		)
		self.assertEqual(notes, [])
		self.assertIs(rows[0], old)
		self.assertEqual((old.title, old.kind), ("Job packet", "Job packet"))
		self.assertNotIn(gone, rows)  # dropped from the table; its File is left alone
		new = rows[1]
		self.assertEqual(new["title"], "pass.png")  # blank title: the file's name
		self.assertEqual((new["traveler"], new["booking_group"]), ("EMP-B", "g1flight"))
		self.assertEqual(new["booking_label"], "Southwest WN1")

	def test_for_everyone_or_for_one_person(self):
		rows, _notes = self.merge(
			self.flight_trip(),
			[
				{"kind": "Booking confirmation", "file": "/f/conf.pdf", "traveler": "", "booking_group": "g1flight"},
				{"kind": "Boarding pass", "file": "/f/ann.png", "traveler": "EMP-A", "booking_group": "g1flight"},
				{"kind": "Site map", "file": "/f/map.png", "traveler": None, "booking_group": ""},
			],
		)
		self.assertEqual([r["traveler"] for r in rows], [None, "EMP-A", None])
		# A file for the whole trip belongs to no booking and is named after none.
		self.assertEqual((rows[2]["booking_group"], rows[2]["booking_label"]), (None, None))

	def test_someone_not_on_the_trip_is_refused(self):
		# Named with its file: the refusal stops the whole save, so it must say which file.
		with self.assertRaises(planner.PlanError) as refused:
			self.merge(self.flight_trip(), [{"kind": "Other", "file": "/f/x.pdf", "traveler": "EMP-Z"}])
		self.assertEqual(str(refused.exception), "x.pdf: EMP-Z is not on this trip.")
		# ...and so is an existing file the page re-pins to them.
		kept = doc_row("D1", "Other", "/f/x.pdf", traveler="EMP-A", title="Packet")
		with self.assertRaises(planner.PlanError) as refused:
			self.merge(
				self.flight_trip(documents=[kept]),
				[{"name": "D1", "title": "Packet", "kind": "Other", "file": "/f/x.pdf", "traveler": "EMP-Z"}],
			)
		self.assertEqual(str(refused.exception), "Packet: EMP-Z is not on this trip.")

	def test_a_file_left_pinned_to_someone_taken_off_the_crew_on_the_form_comes_off_not_refused(self):
		# Bo was removed on the form's Travelers table, which checks nothing on the Documents
		# tab, and the page sends every file on every save. Refusing his boarding pass refused
		# every save of the trip, each step change included, naming only an employee id.
		stale = doc_row("D1", "Boarding pass", "/f/bo.png", "g1flight", "EMP-C", title="Bo's pass", traveler_name="Bo")
		other = doc_row("D2", "Site map", "/f/map.png", title="Map")
		doc = self.flight_trip(documents=[stale, other])
		rows, notes = self.merge(
			doc,
			[
				{"name": "D1", "title": "Bo's pass", "kind": "Boarding pass", "file": "/f/bo.png", "traveler": "EMP-C", "booking_group": "g1flight"},
				{"name": "D2", "title": "Map", "kind": "Site map", "file": "/f/map.png"},
			],
			check=attached(),
		)
		self.assertEqual(rows, [other])
		self.assertEqual(
			notes,
			["Bo's pass was only for Bo, who is no longer on the trip, so it is off the trip's files. It is still attached to the trip on the full form."],
		)

	def test_a_kind_that_is_not_an_option_is_refused_and_a_blank_one_is_other(self):
		with self.assertRaises(planner.PlanError):
			self.merge(self.flight_trip(), [{"kind": "Receipt", "file": "/f/x.pdf"}])
		rows, _notes = self.merge(self.flight_trip(), [{"kind": "", "file": "/f/x.pdf"}])
		self.assertEqual(rows[0]["kind"], "Other")

	def test_a_row_with_no_file_is_refused(self):
		with self.assertRaises(planner.PlanError):
			self.merge(self.flight_trip(), [{"kind": "Other", "file": " "}])

	def test_a_file_not_attached_to_this_trip_is_refused(self):
		with self.assertRaises(planner.PlanError) as refused:
			self.merge(self.flight_trip(), [{"kind": "Other", "file": "/private/files/hr.pdf"}], check=attached())
		self.assertEqual(str(refused.exception), "That file is not attached to this trip.")

	def test_a_public_file_is_refused_with_its_own_reason(self):
		# frappe's uploader offers "Set all public" unless told not to, and a public File is
		# served to anyone at /files/<the name it was uploaded with>: a boarding pass there is a
		# name and a PNR one guess away. Not "not attached": it is, and that would mislead.
		with self.assertRaises(planner.PlanError) as refused:
			self.merge(
				self.flight_trip(),
				[{"kind": "Boarding pass", "file": "/files/pass.png", "booking_group": "g1flight"}],
				check=attached(public=("/files/pass.png",)),
			)
		self.assertEqual(
			str(refused.exception),
			"pass.png is public: anyone with the link can open it, without signing in. Upload it again as private.",
		)

	def test_the_file_is_checked_only_when_it_is_new_or_changed(self):
		# A File deleted since it was put on the row must not make the trip unsaveable.
		kept = doc_row("D1", "Other", "/private/files/deleted-since.pdf")
		rows, _notes = self.merge(
			self.flight_trip(documents=[kept]),
			[{"name": "D1", "kind": "Other", "file": "/private/files/deleted-since.pdf"}],
			check=attached(),
		)
		self.assertEqual(rows, [kept])
		with self.assertRaises(planner.PlanError):
			self.merge(
				self.flight_trip(documents=[doc_row("D1", "Other", "/private/files/a.pdf")]),
				[{"name": "D1", "kind": "Other", "file": "/private/files/elsewhere.pdf"}],
				check=attached(),
			)

	def test_the_real_check_asks_for_a_file_attached_to_this_trip_and_whether_it_is_private(self):
		calls = []
		found = {}

		def get_all(doctype, filters=None, pluck=None, **kwargs):
			calls.append((doctype, filters, pluck, kwargs))
			return found.get(filters["file_url"], [])

		with mock.patch.object(planner, "frappe", types.SimpleNamespace(get_all=get_all)):
			check = planner._file_on_trip("TRIP-1")
			found.update({"/private/files/pass.png": [1], "/files/pass.png": [0], "/private/files/twice.png": [1, 0]})
			self.assertIs(check("/private/files/pass.png"), True)
			self.assertIs(check("/files/pass.png"), False)
			self.assertIs(check("/private/files/twice.png"), False, "one public copy is a public file")
			self.assertIsNone(check("/private/files/elsewhere.png"))
		self.assertEqual(
			calls[0],
			(
				"File",
				{
					"file_url": "/private/files/pass.png",
					"attached_to_doctype": "Travel Trip",
					"attached_to_name": "TRIP-1",
				},
				"is_private",
				{},
			),
		)

	def test_a_receipt_cannot_become_a_trip_file(self):
		# A receipt is money: it stays on its cost row, which the views never show.
		doc = self.flight_trip(
			other_costs=[row(name="X1", cost=40, attachment="/private/files/parking-receipt.pdf")]
		)
		doc.flights[0].attachment = "/private/files/fare-receipt.pdf"
		for url in ("/private/files/parking-receipt.pdf", "/private/files/fare-receipt.pdf"):
			with self.assertRaises(planner.PlanError):
				self.merge(doc, [{"kind": "Booking confirmation", "file": url, "booking_group": "g1flight"}])

	def test_a_file_that_became_a_receipt_since_is_never_sent_and_comes_off_with_a_note(self):
		# Paperwork first, then the same PDF as the flight's Receipt: frappe reuses the URL, so
		# both name one file. The page is never sent it (a receipt is money), and a save —
		# whether or not an old page sends it back — takes it off rather than refusing every
		# save of the trip.
		url = "/private/files/wn1.pdf"
		became = doc_row("D1", "Booking confirmation", url, "g1flight", title="WN1")
		kept = doc_row("D2", "Site map", "/f/map.png", title="Map")
		doc = self.flight_trip(documents=[became, kept])
		doc.flights[0].attachment = url
		self.assertEqual([d["name"] for d in planner._documents_state(doc)], ["D2"])
		note = "WN1 is a receipt on one of this trip's costs, so it is off the trip's files. A receipt stays with its cost, where the trip views never show it."
		sent_back = {"name": "D1", "title": "WN1", "kind": "Booking confirmation", "file": url, "booking_group": "g1flight"}
		map_row = {"name": "D2", "title": "Map", "kind": "Site map", "file": "/f/map.png"}
		for documents in ([map_row], [sent_back, map_row]):
			rows, notes = self.merge(doc, documents, check=attached())
			self.assertEqual((rows, notes), ([kept], [note]))
		# ...and it covers no booking's paperwork meanwhile.
		self.assertEqual(completeness.receipt_urls(doc), {url})
		self.assertEqual(completeness._papers_by_group(doc), {})

	def test_a_booking_that_is_not_on_the_trip_is_refused(self):
		with self.assertRaises(planner.PlanError):
			self.merge(self.flight_trip(), [{"kind": "Other", "file": "/f/x.pdf", "booking_group": "nothere12345"}])

	def test_a_file_whose_booking_was_removed_becomes_a_file_for_the_whole_trip(self):
		# Removed on the form, or on the page in this same save: the file stays on the trip.
		orphan = doc_row("D1", "Boarding pass", "/f/pass.png", booking_group="gone00000001", title="Pass")
		rows, notes = self.merge(
			self.flight_trip(documents=[orphan]),
			[
				{
					"name": "D1",
					"title": "Pass",
					"kind": "Boarding pass",
					"file": "/f/pass.png",
					"booking_group": "gone00000001",
				}
			],
		)
		self.assertEqual((orphan.booking_group, orphan.booking_label), (None, None))
		self.assertEqual(notes, ["Pass is now a file for the whole trip: its booking was removed."])

	def test_a_trip_not_saved_yet_has_no_files(self):
		with self.assertRaises(planner.PlanError):
			self.merge(files_trip(name=None), [{"kind": "Other", "file": "/f/x.pdf"}])
		rows, _notes = self.merge(files_trip(name=None), [])
		self.assertEqual(rows, [])

	def test_the_pages_card_keys_become_the_stored_ids(self):
		# The page names a booking by its own key: new:<n> for a card not saved yet,
		# row:<name> for one typed on the form. Both get an id in this same save, and the file
		# must be stored under that id, not the key.
		doc = files_trip(
			flights=[
				row(name="F9", traveler="EMP-A", booking_group=None, leg="Outbound", airline="Delta",
					flight_number="DL9", departure_time="2026-10-05 06:00:00", booking_reference="Q1", cost=0),
			],
			freight=[
				row(name="FR1", carrier="ODFL", tracking_number="PRO1", booking_group=None, traveler=None, cost=0)
			],
		)
		plan = {
			"bookings": {
				"flights": [
					{
						"group": "new:1",
						"values": {"leg": "Outbound", "airline": "Southwest", "flight_number": "WN1", "cost": 100},
						"changed": ["leg", "airline", "flight_number", "cost"],
						"members": [{"traveler": "EMP-A", "ref": "A1"}, {"traveler": "EMP-B", "ref": "B1"}],
					},
					{
						"group": "row:F9",
						"values": {"airline": "Delta", "flight_number": "DL9", "cost": 0},
						"members": [{"name": "F9", "traveler": "EMP-A", "ref": "Q1"}],
					},
				]
			},
			"freight": [
				{"name": "FR1", "carrier": "ODFL", "tracking_number": "PRO1", "booking_group": ""},
				{"name": None, "carrier": "Estes", "tracking_number": "E2", "booking_group": "new:7"},
			],
			"documents": [
				{"kind": "Boarding pass", "file": "/f/bo.png", "traveler": "EMP-B", "booking_group": "new:1"},
				{"kind": "Booking confirmation", "file": "/f/dl9.pdf", "booking_group": "row:F9"},
				{"kind": "Bill of lading", "file": "/f/bol1.pdf", "booking_group": "row:FR1"},
				{"kind": "Bill of lading", "file": "/f/bol2.pdf", "booking_group": "new:7"},
				{"kind": "Site map", "file": "/f/map.png", "booking_group": ""},
			],
		}
		with mock.patch.object(planner, "_file_on_trip", lambda trip: (lambda url: True)):
			notes = planner.apply_plan(doc, plan)
		self.assertEqual(notes, [])
		southwest = next(r for r in doc.flights if r.airline == "Southwest").booking_group
		delta = next(r for r in doc.flights if r.airline == "Delta").booking_group
		odfl, estes = (r.booking_group for r in doc.freight)
		for stored in (southwest, delta, odfl, estes):
			self.assertRegex(stored, r"^[0-9a-f]{12}$")
		self.assertEqual(
			[(d.booking_group, d.booking_label) for d in doc.documents],
			[
				(southwest, "Southwest WN1"),
				(delta, "Delta DL9"),
				(odfl, "ODFL PRO1"),
				(estes, "Estes E2"),
				(None, None),
			],
		)
		self.assertEqual([d.idx for d in doc.documents], [1, 2, 3, 4, 5])
		# ...and the state the page gets back names them the same way.
		state_docs = planner._documents_state(doc)
		self.assertEqual([d["booking_group"] for d in state_docs], [southwest, delta, odfl, estes, ""])

	def test_the_doctype_is_the_one_the_planner_writes(self):
		meta = _load_json(TRAVEL_DIR, "doctype", "trip_document", "trip_document.json")
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual((meta["name"], meta["module"], meta["istable"]), ("Trip Document", "Travel Management", 1))
		self.assertEqual(
			meta["field_order"],
			["title", "kind", "file", "traveler", "traveler_name", "booking_group", "booking_label"],
		)
		self.assertEqual(tuple(fields["kind"]["options"].split("\n")), completeness.DOCUMENT_KINDS)
		self.assertEqual((fields["kind"]["default"], fields["kind"]["reqd"]), ("Other", 1))
		self.assertEqual((fields["file"]["fieldtype"], fields["file"]["reqd"]), ("Attach", 1))
		self.assertEqual((fields["traveler"]["options"], fields["traveler"]["ignore_user_permissions"]), ("Employee", 1))
		self.assertEqual(fields["traveler_name"]["fetch_from"], "traveler.employee_name")
		self.assertEqual((fields["booking_group"]["hidden"], fields["booking_group"]["read_only"]), (1, 1))
		self.assertEqual(fields["booking_label"]["read_only"], 1)
		self.assertLessEqual(set(planner.DOCUMENT_FIELDS), set(fields))
		# The server names the booking; the page never writes it.
		self.assertNotIn("booking_label", planner.DOCUMENT_FIELDS)
		controller = _read(os.path.join(TRAVEL_DIR, "doctype", "trip_document", "trip_document.py"))
		self.assertIn("class TripDocument(Document):", controller)

	def test_the_trip_has_a_documents_tab(self):
		meta = _load_json(TRAVEL_DIR, "doctype", "travel_trip", "travel_trip.json")
		fields = {f["fieldname"]: f for f in meta["fields"]}
		order = meta["field_order"]
		self.assertEqual((fields["documents"]["fieldtype"], fields["documents"]["options"]), ("Table", "Trip Document"))
		self.assertEqual(fields["documents"]["label"], "Trip Documents")
		self.assertEqual(fields["documents_tab"]["fieldtype"], "Tab Break")
		self.assertEqual(order.index("documents"), order.index("documents_tab") + 1)
		# The form's Duplicate copies a table's rows unless it is no_copy (v16 copy_doc), and a
		# copy's rows would point at Files attached to the ORIGINAL trip: its crew gets
		# "Forbidden" at the gate, and the checklist counts the old trip's paperwork as done.
		self.assertEqual(fields["documents"].get("no_copy"), 1)
		# Child doctype and parent JSON are age-gated on migrate.
		self.assertGreater(meta["modified"], "2026-09-26 12:00:00.000000")

	def test_booking_paperwork_is_sent_to_its_booking_never_to_the_files_step(self):
		# A file added on the Files step, or on the form's Documents tab, is for the whole
		# trip and can never be moved onto a booking: a boarding pass there is not on the flight
		# at the gate, and its paperwork gap never clears. Every text that says where booking
		# paperwork goes must say "on its booking".
		for doctype in ("trip_flight", "trip_accommodation", "trip_ground_transport", "trip_freight"):
			meta = _load_json(TRAVEL_DIR, "doctype", doctype, f"{doctype}.json")
			receipt = next(f for f in meta["fields"] if f["fieldname"] == "attachment")
			self.assertIn("goes on its booking", receipt["description"], doctype)
			self.assertNotIn("Files", receipt["description"], doctype)
			self.assertGreater(meta["modified"], "2026-09-26 12:00:00.000000", doctype)
		trip_meta = _load_json(TRAVEL_DIR, "doctype", "travel_trip", "travel_trip.json")
		table = next(f for f in trip_meta["fields"] if f["fieldname"] == "documents")
		self.assertIn("always for the whole trip", table["description"])
		policy = re.sub(r"\s+", " ", _read(os.path.join(APP_DIR, "www", "travel_guidelines.html")))
		self.assertNotIn("files instead (Plan a Trip → <i>Files</i>", policy)
		self.assertIn("goes on its booking instead", policy)

	def test_the_row_attachments_are_receipts_in_the_cost_section(self):
		# A receipt is money. Booking paperwork is a Trip Document now; the old per-row field
		# is the cost's Receipt, and sits with the cost. Prod had no row attachments on any
		# travel table (checked 2026-09-26), so nothing moves.
		for doctype in ("trip_flight", "trip_accommodation", "trip_ground_transport", "trip_freight", "trip_expense"):
			meta = _load_json(TRAVEL_DIR, "doctype", doctype, f"{doctype}.json")
			fields = {f["fieldname"]: f for f in meta["fields"]}
			order = meta["field_order"]
			self.assertEqual(fields["attachment"]["label"], "Receipt", doctype)
			if doctype != "trip_expense":  # a misc cost is all cost; its Receipt was already there
				self.assertGreater(order.index("attachment"), order.index("cost_section"), doctype)
				self.assertGreater(meta["modified"], "2026-09-25 16:00:00.000000", doctype)
		freight = _load_json(TRAVEL_DIR, "doctype", "trip_freight", "trip_freight.json")
		group = next(f for f in freight["fields"] if f["fieldname"] == "booking_group")
		self.assertEqual((group["fieldtype"], group["hidden"], group["read_only"]), ("Data", 1, 1))
		self.assertEqual(planner.FREIGHT_FIELDS[-1], "booking_group")


class TestPaperworkGaps(unittest.TestCase):
	"""The fifth check: a booking that is made (it has its confirmation or tracking number)
	but whose paperwork is not on the trip. It flags, never blocks, and never flags a booking
	twice: one with no number is the confirmation check's."""

	def two_on_a_flight(self, documents=(), refs=("A1", "B1"), leg="Outbound", when="2026-10-05 07:00:00"):
		return trip(
			flights=[
				flight("EMP-A", leg, when, name="F1", booking_group="g1", ref=refs[0]),
				flight("EMP-B", leg, when, name="F2", booking_group="g1", ref=refs[1]),
			],
			documents=list(documents),
		)

	def paperwork(self, t):
		return checks(completeness.find_gaps(t), "documents")

	def test_each_person_on_a_flight_needs_their_boarding_pass(self):
		gaps = self.paperwork(self.two_on_a_flight([doc_row("D1", "Boarding pass", "/f/a.png", "g1", "EMP-A")]))
		self.assertEqual(len(gaps), 1)
		gap = gaps[0]
		self.assertEqual(
			(gap["table"], gap["group"], gap["step"], gap["label"], gap["employee_names"]),
			("flights", "g1", "there", "Southwest WN1", ["Bo"]),
		)
		self.assertEqual(gap["kinds"], ["Boarding pass", "Booking confirmation"])
		# Nobody has one: both are named.
		self.assertEqual(self.paperwork(self.two_on_a_flight())[0]["employee_names"], ["Ann", "Bo"])

	def test_a_file_for_everyone_on_the_flight_covers_everyone(self):
		t = self.two_on_a_flight([doc_row("D1", "Booking confirmation", "/f/conf.pdf", "g1", None)])
		self.assertEqual(self.paperwork(t), [])

	def test_a_boarding_pass_for_nobody_in_particular_covers_a_shared_flight_for_nobody(self):
		# One person's boarding pass, uploaded without saying whose it is, used to clear the
		# whole flight: everyone's gap went, and it showed on everyone's itinerary as theirs.
		t = self.two_on_a_flight([doc_row("D1", "Boarding pass", "/f/ann.png", "g1", None)])
		self.assertEqual(self.paperwork(t)[0]["employee_names"], ["Ann", "Bo"])
		# A whole-crew row is a shared flight too: two on the crew.
		t = trip(
			flights=[flight(None, "Outbound", "2026-10-05 07:00:00", name="F1", ref="X1")],
			documents=[doc_row("D1", "Boarding pass", "/f/pass.png", "row:F1", None)],
		)
		self.assertEqual(self.paperwork(t)[0]["employee_names"], ["Ann", "Bo"])
		# On a flight with one person it can only be theirs.
		t = trip(
			flights=[flight("EMP-A", "Outbound", "2026-10-05 07:00:00", name="F1", booking_group="g1", ref="A1")],
			documents=[doc_row("D1", "Boarding pass", "/f/ann.png", "g1", None)],
		)
		self.assertEqual(self.paperwork(t), [])
		# Each person's own pass still covers them, beside a pass for nobody.
		t = self.two_on_a_flight(
			[doc_row("D1", "Boarding pass", "/f/x.png", "g1", None), doc_row("D2", "Boarding pass", "/f/bo.png", "g1", "EMP-B")]
		)
		self.assertEqual(self.paperwork(t)[0]["employee_names"], ["Ann"])

	def test_no_number_no_paperwork_flag(self):
		t = self.two_on_a_flight(refs=("", "B1"))
		self.assertEqual(checks(completeness.find_gaps(t), "confirmation")[0]["employee_names"], ["Ann"])
		# Ann is the confirmation check's; only Bo is asked for paperwork.
		self.assertEqual(self.paperwork(t)[0]["employee_names"], ["Bo"])
		self.assertEqual(self.paperwork(self.two_on_a_flight(refs=("", " "))), [])

	def test_the_wrong_kind_booking_or_an_empty_file_does_not_count(self):
		for document in (
			doc_row("D1", "Site map", "/f/map.png", "g1", None),
			doc_row("D1", "Boarding pass", "/f/pass.png", "other1", None),
			doc_row("D1", "Boarding pass", "", "g1", None),
		):
			self.assertEqual(len(self.paperwork(self.two_on_a_flight([document]))), 1, document.kind)

	def test_a_whole_crew_flight_row_is_everyones_seat(self):
		t = trip(flights=[flight(None, "Outbound", "2026-10-05 07:00:00", name="F1", ref="X1")])
		self.assertEqual(self.paperwork(t)[0]["employee_names"], ["Ann", "Bo"])
		t.documents = [doc_row("D1", "Boarding pass", "/f/ann.png", "row:F1", "EMP-A")]
		self.assertEqual(self.paperwork(t)[0]["employee_names"], ["Bo"])

	def test_the_step_is_the_one_the_confirmation_gap_names(self):
		t = self.two_on_a_flight(refs=("", "B1"), leg="Return", when="2026-10-08 18:00:00")
		self.assertEqual(checks(completeness.find_gaps(t), "confirmation")[0]["step"], "back")
		self.assertEqual(self.paperwork(t)[0]["step"], "back")

	def test_a_room_needs_its_confirmation_once(self):
		rooms = [
			stay("EMP-A", "2026-10-05", "2026-10-08", name="R1", booking_group="r1", ref="H-A"),
			stay("EMP-B", "2026-10-05", "2026-10-08", name="R2", booking_group="r1", ref="H-B"),
		]
		gaps = self.paperwork(trip(accommodations=rooms))
		self.assertEqual(
			[(g["group"], g["step"], g["employee_names"], g["kinds"]) for g in gaps],
			[("r1", "lodging", [], ["Booking confirmation"])],
		)
		# One confirmation on the booking is enough, whoever it names.
		t = trip(accommodations=rooms, documents=[doc_row("D1", "Booking confirmation", "/f/h.pdf", "r1", "EMP-A")])
		self.assertEqual(self.paperwork(t), [])
		# A room where someone still has no number is flagged for that, not for its paperwork.
		rooms[1].booking_confirmation = ""
		self.assertEqual(self.paperwork(trip(accommodations=rooms)), [])

	def test_a_rental_needs_its_agreement_and_our_own_vehicles_do_not(self):
		def ride(kind, **kwargs):
			return row(
				name="G1", traveler=None, booking_group="v1", leg="During Trip", transport_type=kind,
				supplier="Enterprise", booking_reference="RC-1", cost=10, pickup_datetime=None, **kwargs
			)

		gaps = self.paperwork(trip(ground_transport=[ride("Rental/Third Party")]))
		self.assertEqual([(g["group"], g["step"], g["label"]) for g in gaps], [("v1", "around", "Enterprise")])
		agreement = [doc_row("D1", "Rental agreement", "/f/ra.pdf", "v1")]
		self.assertEqual(self.paperwork(trip(ground_transport=[ride("Rental/Third Party")], documents=agreement)), [])
		for kind in ("Company Fleet", "Personal Vehicle", "Taxi/Rideshare"):
			self.assertEqual(self.paperwork(trip(ground_transport=[ride(kind)])), [], kind)

	def test_a_shipment_needs_its_bill_of_lading(self):
		def shipment(tracking):
			return row(name="S1", carrier="ODFL", tracking_number=tracking, booking_group="s1s1s1", cost=5, traveler=None)

		gaps = self.paperwork(trip(freight=[shipment("PRO 1")]))
		self.assertEqual(
			[(g["table"], g["group"], g["step"], g["label"], g["employee_names"]) for g in gaps],
			[("freight", "s1s1s1", "freight", "ODFL PRO 1", [])],
		)
		bol = [doc_row("D1", "Bill of lading", "/f/bol.pdf", "s1s1s1")]
		self.assertEqual(self.paperwork(trip(freight=[shipment("PRO 1")], documents=bol)), [])
		self.assertEqual(self.paperwork(trip(freight=[shipment("")])), [])


class TestTheQuieterTally(unittest.TestCase):
	"""Nik, 2026-09-26: paperwork is a separate, quieter tally. find_gaps keeps it as data; what
	a headline counts is counted_gaps, and the paperwork is counted on its own, as files."""

	def mixed(self):
		# Ann has no number (a counted gap); Bo has his and no boarding pass (paperwork); the
		# room has its number and no confirmation attached (paperwork).
		return trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00", name="F1", booking_group="g1", ref=""),
				flight("EMP-B", "Outbound", "2026-10-05 07:00:00", name="F2", booking_group="g1", ref="B1"),
				flight(
					"EMP-A", "Return", "2026-10-08 18:00:00", name="F3", booking_group="g2", ref="A2", cost=0
				),
				flight(
					"EMP-B", "Return", "2026-10-08 18:00:00", name="F4", booking_group="g2", ref="B1", cost=0
				),
			],
			accommodations=[
				stay("EMP-A", "2026-10-05", "2026-10-08", name="R1", booking_group="r1"),
				stay("EMP-B", "2026-10-05", "2026-10-08", name="R2", booking_group="r1"),
			],
			documents=[],
		)

	def test_the_counted_gaps_are_every_check_but_paperwork(self):
		gaps = completeness.find_gaps(self.mixed())
		counted = completeness.counted_gaps(gaps)
		paperwork = completeness.paperwork_gaps(gaps)
		self.assertEqual(len(counted) + len(paperwork), len(gaps))
		self.assertNotIn("documents", {g["check"] for g in counted})
		self.assertEqual({g["check"] for g in paperwork}, {"documents"})
		self.assertIn("confirmation", {g["check"] for g in counted})
		# Ann's flight home rides on no paid ticket (A2 has no fare anywhere): a cost gap, counted.
		self.assertIn("cost", {g["check"] for g in counted})

	def test_files_are_counted_per_person_on_a_flight_and_one_per_other_booking(self):
		gaps = completeness.find_gaps(self.mixed())
		by_group = {g["group"]: g["employee_names"] for g in completeness.paperwork_gaps(gaps)}
		# The way there: Bo (Ann has no number yet); the way home: both; the room: one file.
		self.assertEqual(by_group, {"g1": ["Bo"], "g2": ["Ann", "Bo"], "r1": []})
		self.assertEqual(completeness.files_not_attached(gaps), 1 + 2 + 1)
		# The same shapes as the page's harness ("3 files not attached yet").
		pair = {"check": "documents", "table": "flights", "employee_names": ["Ana", "Ben"]}
		room = {"check": "documents", "table": "accommodations", "employee_names": []}
		self.assertEqual(completeness.files_not_attached([pair, room, {"check": "cost"}]), 3)
		self.assertEqual(completeness.files_not_attached([]), 0)
		self.assertEqual(completeness.files_not_attached(None), 0)

	def test_it_is_only_ever_the_documents_check(self):
		self.assertEqual(completeness.PAPERWORK_CHECK, "documents")
		self.assertTrue(completeness.is_paperwork({"check": "documents"}))
		for check in ("travel", "lodging", "confirmation", "cost", None):
			self.assertFalse(completeness.is_paperwork({"check": check}), check)
		self.assertFalse(completeness.is_paperwork(None))
		# The page names the same check (TP_PAPERWORK), and so does the form.
		page = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		form = _read(os.path.join(APP_DIR, "public", "js", "travel_trip.js"))
		self.assertIn('const TP_PAPERWORK = "documents";', page)
		self.assertIn("const PAPERWORK_CHECK = 'documents';", form)


class TestTheFormsChecklistHeadline(unittest.TestCase):
	"""The Travel Trip form's headline counts every gap but paperwork. Paperwork is one muted
	line under it ("N files not attached yet", to the Files step), and never turns it orange."""

	CONFIRMATION = {"check": "confirmation", "step": "there", "group": "g1", "employee_names": ["Ann"]}
	PAPER = {
		"check": "documents",
		"step": "there",
		"group": "g2",
		"table": "flights",
		"employee_names": ["Ann", "Bo"],
	}

	def test_the_headline_never_counts_paperwork(self):
		clear, only_paper, mixed, only_counted = form_headlines(
			[[], [self.PAPER], [self.CONFIRMATION, self.PAPER], [self.CONFIRMATION]]
		)
		self.assertEqual(clear, [{"html": "Trip checklist: nothing missing.", "color": "green"}])

		(shown,) = only_paper
		self.assertEqual(shown["color"], "green", "only paperwork open: all set")
		self.assertTrue(shown["html"].startswith("Trip checklist: nothing missing."))
		self.assertIn(">2 files not attached yet</a>", shown["html"])
		self.assertIn("&step=files", shown["html"])

		(shown,) = mixed
		self.assertEqual(shown["color"], "orange")
		headline, _sep, quiet = shown["html"].partition('<div class="small text-muted"')
		self.assertIn("1 missing a confirmation number.", headline)
		self.assertNotIn("paperwork", headline)
		self.assertNotIn("2 missing", headline)
		self.assertIn("&step=review", headline)
		self.assertIn("2 files not attached yet", quiet)
		self.assertIn("&step=files", quiet)

		(shown,) = only_counted
		self.assertEqual(shown["color"], "orange")
		self.assertNotIn("not attached", shown["html"])

	def test_one_file_is_singular(self):
		((shown,),) = form_headlines([[dict(self.PAPER, employee_names=["Ann"])]])
		self.assertIn(">1 file not attached yet</a>", shown["html"])

	def test_the_labels_it_counts_leave_paperwork_out(self):
		form = _strip_js_comments(_read(os.path.join(APP_DIR, "public", "js", "travel_trip.js")))
		labels = re.search(r"const CHECKLIST_LABELS = \{(.*?)\n\};", form, re.S).group(1)
		self.assertEqual(
			re.findall(r"^\t(\w+):", labels, re.M), ["travel", "lodging", "confirmation", "cost"]
		)


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
		self.assertEqual(state["documents"], [])

	def test_the_trips_files_read_back_for_the_page(self):
		doc = files_trip(
			flights=[
				flight("EMP-A", "Outbound", "2026-10-05 07:00:00", name="F1", booking_group="g1", airline="Delta"),
			],
			freight=[row(name="S1", carrier="ODFL", tracking_number="PRO 1", booking_group="s1s1s1", cost=0,
				billable=0, traveler=None)],
			documents=[
				doc_row("D1", "Boarding pass", "/private/files/Ann Pass.PNG", "g1", "EMP-A", title="",
					booking_label="stale"),
				doc_row("D2", "Bill of lading", "/files/bol.pdf?v=1", "s1s1s1", None, title="BOL"),
				doc_row("D3", "Site map", "/files/map.pdf", None, None, title="Site"),
				# Its booking was removed on the form: shown where the next save puts it.
				doc_row("D4", "Other", "/files/x.heic", "gone00000001", None, title="X", booking_label="Gone"),
			],
		)
		doc.modified = "2026-09-26 09:00:00"
		doc.purpose = doc.travel_type = doc.travel_for_doctype = doc.travel_for_name = None
		doc.billable = 0
		doc.trip_description = None
		state = planner.get_state(doc)
		self.assertEqual(
			state["documents"],
			[
				{
					"name": "D1",
					"title": "Ann Pass.PNG",
					"kind": "Boarding pass",
					"file": "/private/files/Ann Pass.PNG",
					"traveler": "EMP-A",
					"booking_group": "g1",
					"booking_label": "Delta WN1",  # read from the booking as it is now
					"file_name": "Ann Pass.PNG",
					"is_image": True,
				},
				{
					"name": "D2",
					"title": "BOL",
					"kind": "Bill of lading",
					"file": "/files/bol.pdf?v=1",
					"traveler": "",
					"booking_group": "s1s1s1",
					"booking_label": "ODFL PRO 1",
					"file_name": "bol.pdf",
					"is_image": False,
				},
				{
					"name": "D3",
					"title": "Site",
					"kind": "Site map",
					"file": "/files/map.pdf",
					"traveler": "",
					"booking_group": "",
					"booking_label": "",
					"file_name": "map.pdf",
					"is_image": False,
				},
				{
					"name": "D4",
					"title": "X",
					"kind": "Other",
					"file": "/files/x.heic",
					"traveler": "",
					"booking_group": "",
					"booking_label": "",
					"file_name": "x.heic",
					"is_image": True,
				},
			],
		)
		# A shipment's id reaches the page with the rest of its fields.
		self.assertEqual(state["freight"][0]["booking_group"], "s1s1s1")


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
		# A shipment's id (trip files, 2026-09-26), last on both lists: the page sends its key
		# there, as a card sends its group.
		self.assertEqual(planner.FREIGHT_FIELDS[-1], "booking_group")

	def test_page_and_server_share_the_document_fields(self):
		# A file's row, as the page sends it and planner.merge_documents reads it. A field on one
		# list and not the other is dropped with no error.
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		block = re.search(r"const TP_DOCUMENT = \[(.*?)\];", source, re.S).group(1)
		self.assertEqual(re.findall(r'"([a-z_]+)"', block), list(planner.DOCUMENT_FIELDS))
		meta = _load_json(TRAVEL_DIR, "doctype", "trip_document", "trip_document.json")
		self.assertTrue(set(planner.DOCUMENT_FIELDS) <= {f["fieldname"] for f in meta["fields"]})

	def test_the_pages_file_kinds_are_the_trip_document_kinds(self):
		# The page's Kind menu, in the Select's order: a kind the page offers and the server does
		# not know makes the whole save refused.
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		kinds = re.findall(r'"([^"]+)"', re.search(r"const TP_DOC_KINDS = \[(.*?)\];", source, re.S).group(1))
		meta = _load_json(TRAVEL_DIR, "doctype", "trip_document", "trip_document.json")
		field = next(f for f in meta["fields"] if f["fieldname"] == "kind")
		self.assertEqual(kinds, [o for o in field["options"].split("\n") if o])
		icons = re.search(r"const TP_DOC_ICONS = \{(.*?)\n\};", source, re.S).group(1)
		keys = [quoted or bare for quoted, bare in re.findall(r'^\t(?:"([^"]+)"|(\w+)):', icons, re.M)]
		self.assertEqual(sorted(keys), sorted(kinds), "every kind has its icon")
		defaults = re.search(r"const TP_DOC_DEFAULT_KIND = \{(.*?)\n\};", source, re.S).group(1)
		self.assertLessEqual(set(re.findall(r':\s*"([^"]+)"', defaults)), set(kinds))

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

	def test_every_travel_endpoint_the_page_calls_is_whitelisted(self):
		# The views (get_trip_views), View as's email preview and the itinerary email live in
		# api/travel.py, not planner.py, so the set above does not see them.
		source = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		api_source = _read(os.path.join(APP_DIR, "api", "travel.py"))
		called = set(re.findall(r"erpnext_enhancements\.api\.travel\.(\w+)", source))
		self.assertEqual(called, {"get_trip_views", "preview_itinerary_email", "send_itinerary_email"})
		for method in called:
			self.assertRegex(api_source, rf"@frappe\.whitelist\([^)]*\)\ndef {method}\(")

	def test_the_forms_trip_views_are_views_the_page_has(self):
		# The form opens the page on a view by its key (frappe.set_route with {trip, view}); a
		# key the page does not know would open the step instead, with no error.
		page = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		form = _read(os.path.join(APP_DIR, "public", "js", "travel_trip.js"))
		views = re.findall(r'"(\w+)"', re.search(r"const TP_VIEWS = \[(.*?)\];", page, re.S).group(1))
		# The Map joined the four views in PR 3 of the program (Nik, 2026-09-26).
		self.assertEqual(views, ["overview", "grid", "compare", "person", "map"])
		block = re.search(r"const TRIP_VIEWS = \[(.*?)\n\];", form, re.S).group(1)
		offered = re.findall(r"\['(\w+)', __\(", block) + re.findall(r"open_trip_view\(frm, '(\w+)'", form)
		self.assertTrue(offered)
		self.assertLessEqual(set(offered), set(views))
		self.assertEqual(set(offered), set(views), "every view is offered on the form")
		# ...and on the page's own view bar (View as is its person menu), and each one is drawn.
		bar = re.search(r"\n\trender_view_bar\(\) \{(.*?)\n\t\}\n", page, re.S).group(1)
		self.assertEqual(set(re.findall(r'\["(\w+)", __\(', bar)) | {"person"}, set(views))
		draw = re.search(r"\n\trender_view\(view\) \{(.*?)\n\t\}\n", page, re.S).group(1)
		self.assertEqual(set(re.findall(r"\n\t\t\t(\w+): \(\) => this\.view_", draw)), set(views))

	def test_the_map_loads_google_maps_only_through_the_shared_loader(self):
		# One loader per page (google_maps_loader.js): the Map asks it for every library whose
		# symbols it uses, or those symbols are undefined at runtime. tests/test_google_maps_loader
		# walks public/js only, and this page lives in the Travel Management module.
		code = _strip_js_comments(_read(os.path.join(PAGE_DIR, "plan_a_trip.js")))
		self.assertNotIn("maps.googleapis.com/maps/api/js", code)
		self.assertNotIn("(g=>{", code.replace(" ", ""))
		self.assertEqual(code.count("EEGoogleMaps.load("), 1)
		self.assertIn("window.EEGoogleMaps.load({ apiKey: maps.api_key, libraries: TP_MAP_LIBRARIES })", code)
		libraries = set(re.findall(r'"(\w+)"', re.search(r"const TP_MAP_LIBRARIES = \[(.*?)\];", code).group(1)))
		needs = {
			"AdvancedMarkerElement": "marker",
			"maps.Marker(": "marker",
			"Geocoder(": "geocoding",
			"DirectionsService(": "routes",
			"computeRoutes": "routes",
			"PlaceAutocompleteElement": "places",
			"spherical": "geometry",
		}
		for symbol, library in needs.items():
			if symbol in code:
				self.assertIn(library, libraries, f"the page uses {symbol} without loading '{library}'")
		# The map's style comes from the loader too: a Map ID or the dark styles, never both.
		self.assertIn("window.EEGoogleMaps.mapOptions(", code)

	def test_the_trip_sheet_is_spelled_in_one_place_and_asks_for_chrome(self):
		# frappe's download_pdf makes the PDF with wkhtmltopdf unless the request names a
		# generator, whatever the Print Format says; the sheet is laid out for Chrome.
		code = _strip_js_comments(_read(os.path.join(PAGE_DIR, "plan_a_trip.js")))
		self.assertEqual(code.count("frappe.utils.print_format.download_pdf"), 1)
		helper = re.search(r"\nfunction tp_sheet_url\(trip, as, base\) \{(.*?)\n\}\n", code, re.S)
		self.assertIsNotNone(helper, "tp_sheet_url() is gone")
		for part in ("doctype=Travel%20Trip", "format=Trip%20Sheet", "no_letterhead=1", "pdf_generator=chrome", "&as="):
			self.assertIn(part, helper.group(1))
		# The server's own address is taken only when it is this site's (a relative path).
		self.assertIn("tp_local_url(base)", helper.group(1))
		local = re.search(r"\nfunction tp_local_url\(url\) \{(.*?)\n\}\n", code, re.S)
		self.assertIsNotNone(local, "tp_local_url() is gone")
		# ...and no backslash anywhere: a browser reads "/\host" as "//host".
		self.assertIn(r"/^\/(?![/\\])[^\s\\]*$/.test(", local.group(1))
		# Review, the Overview and View as each open it.
		self.assertEqual(code.count("this.sheet_link("), 3)

	def test_the_pages_trip_sheet_address_is_the_servers(self):
		# The server spells the Trip Sheet's address in views.trip_sheet_url and sends it
		# (get_trip_views' sheet_url and people_sheet_urls, get_trip_itinerary's sheet_url and
		# my_sheet_url). The page spells it once more, for the Review step, whose answer
		# (get_plan) carries none. Run, not grepped: the two must be the same address, character
		# for character, and so must the two harnesses' fake servers.
		from erpnext_enhancements.travel_management import views

		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		code = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		helpers = "".join(
			re.search(rf"\nfunction {name}\(.*?\n\}}\n", code, re.S).group(0) for name in ("tp_local_url", "tp_sheet_url")
		)
		cases = [["TRIP-2026-00001", ""], ["TRIP-2026-00001", "HR-EMP-00042"], ["TRIP 7/B", "EMP #1&x=2"]]
		script = helpers + f"process.stdout.write(JSON.stringify({json.dumps(cases)}.map(([t, a]) => tp_sheet_url(t, a))));"
		result = subprocess.run(
			[node, "-e", script], capture_output=True, text=True, encoding="utf-8", check=False, timeout=60
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(json.loads(result.stdout), [views.trip_sheet_url(t, a or None) for t, a in cases])
		# The fake servers in both harnesses send what the real one would.
		web = _read(os.path.join(os.path.dirname(APP_DIR), "scripts", "test_web_flow_history.js"))
		self.assertIn(f'const SHEET = "{views.trip_sheet_url("TRIP-A")}";', web)
		wizard = _read(os.path.join(os.path.dirname(APP_DIR), "scripts", "test_wizard_back_forward.mjs"))
		self.assertIn(f'const sheet = "{views.trip_sheet_url("TRIP-5")}";', wizard)

	def test_a_contact_link_is_built_in_one_place_each(self):
		# A phone or an email on the contacts is typed in by people (a Supplier's address, a
		# Contact): tel: is built from digits and a leading + only, mailto: only from an address
		# that is plainly one, and a link the server built is shown only when it is https.
		code = _strip_js_comments(_read(os.path.join(PAGE_DIR, "plan_a_trip.js")))
		for scheme, helper in (("tel:", "tp_tel"), ("mailto:", "tp_mailto")):
			# The scheme as it opens a string (a bare "tel:" is also the end of "hotel:").
			self.assertEqual(len(re.findall(rf"[`\"']{scheme}", code)), 1, scheme)
			body = re.search(rf"\nfunction {helper}\((\w+)\) \{{(.*?)\n\}}\n", code, re.S).group(2)
			self.assertIn(f"`{scheme}", body)
		# Changed on purpose in PR 3's integration: tp_tel follows /itinerary's telHref and the
		# server's views.tel_href (a spelled-out number is not dialed), run below, rather than
		# keeping every digit and plus sign.
		tel = re.search(r"\nfunction tp_tel\(phone\) \{(.*?)\n\}\n", code, re.S).group(1)
		self.assertIn('.replace(/\\D/g, "")', tel)
		contacts = re.search(r"\n\tcontacts_block\(\$parent, contacts, hotels\) \{(.*?)\n\t\}\n", code, re.S).group(1)
		self.assertIn("tp_https(hotel.urgent_care_url)", contacts)
		self.assertIn("tp_https(hotel.directions_url)", contacts)

	#: A phone as people type it, and the tel: link every surface makes of it (None: shown as
	#: typed, never dialed).
	PHONES = (
		("(801) 555-0100", "tel:8015550100"),
		("+1 801.555.0100", "tel:+18015550100"),
		("  +44 20 7946 0958 ", "tel:+442079460958"),
		("801-555-0100 ext. 4", "tel:8015550100"),
		("(801) 555-0100 x12", "tel:8015550100"),
		("801-555-0100#3", "tel:8015550100"),
		("tel:+1 (702) 555-0123;ext=4", "tel:+17025550123"),
		("Front desk: 702-555-0150", "tel:7025550150"),
		("911", "tel:911"),
		("1-800-FLOWERS", None),
		("555-CALL-123", None),
		("702-555-0150 or 702-555-0151", None),
		("12", None),
		("1234567890123456", None),
		("call the front desk", None),
		("", None),
		(None, None),
	)

	def test_every_surface_dials_the_same_number(self):
		# Plan a Trip (tp_tel), /itinerary (telHref) and the server (views.tel_href: the email's
		# Contacts table and the printed Trip Sheet) each make the tel: links for the same
		# contacts card. Before PR 3's integration they had three rules, and the server's and the
		# page's dialed "1-800-FLOWERS" as 1800. Run, not grepped.
		from erpnext_enhancements.travel_management import views

		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		page = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		phone = _read(os.path.join(APP_DIR, "public", "js", "travel", "itinerary.js"))
		tp_tel = re.search(r"\nfunction tp_tel\(phone\) \{.*?\n\}\n", page, re.S).group(0)
		start = phone.index("\n\tfunction telHref(")
		tel_href = phone[start : phone.index("\n\t}\n", start) + 3]
		cases = [value for value, _ in self.PHONES]
		script = (
			tp_tel
			+ tel_href
			+ f"const cases = {json.dumps(cases)};"
			+ "process.stdout.write(JSON.stringify({page: cases.map(tp_tel), phone: cases.map(telHref)}));"
		)
		result = subprocess.run(
			[node, "-e", script], capture_output=True, text=True, encoding="utf-8", check=False, timeout=60
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		got = json.loads(result.stdout)
		expected = [want for _, want in self.PHONES]
		self.assertEqual([views.tel_href(value) for value in cases], expected, "views.tel_href")
		self.assertEqual([value or None for value in got["page"]], expected, "plan_a_trip.js tp_tel")
		self.assertEqual([value or None for value in got["phone"]], expected, "itinerary.js telHref")

	def test_the_harness_knows_every_step(self):
		# scripts/test_wizard_back_forward.mjs records each render by TP_KEYS[this.step], a
		# copy of the page's step order: a step added to one and not the other mislabels every
		# render, and the views, which are not steps, must not be added to either.
		page = _read(os.path.join(PAGE_DIR, "plan_a_trip.js"))
		harness = _read(os.path.join(os.path.dirname(APP_DIR), "scripts", "test_wizard_back_forward.mjs"))
		steps = re.findall(r'key: "(\w+)"', re.search(r"const TP_STEPS = \[(.*?)\n\];", page, re.S).group(1))
		keys = re.findall(r'"(\w+)"', re.search(r"const TP_KEYS = \[(.*?)\];", harness).group(1))
		self.assertEqual(keys, steps)
		self.assertEqual(steps[-1], "review")

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

	def test_the_views_are_read_from_the_route_and_consumed(self):
		# &view= and &as= reach the page in frappe.route_options like &trip= does (the form's
		# "Trip views" buttons, a reload of a view). Read and deleted there, or a later plain
		# visit to the page replays the last view.
		body = re.search(r"\n\troute_args\(\) \{(.*?)\n\t\}\n", self.source, re.S).group(1)
		self.assertIn('view: pick("view")', body)
		self.assertIn('as: pick("as")', body)

	def test_a_views_address_keeps_one_key_order(self):
		# leave_view and back_one_step compare query strings as written: trip, step, view, as.
		body = re.search(r"\n\taddress_args\(\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(body, "address_args() is gone")
		code = body.group(1)
		self.assertIn("{ trip: this.state.name, step: step }", code)
		self.assertLess(code.index("args.view ="), code.index("args.as ="))

	def test_a_view_is_an_entry_the_page_writes_and_redraws(self):
		# Opening a view pushes through set_address once it is drawn (never raw pushState, never
		# frappe.set_route to this page); Back/Forward onto or off a view is handled before
		# jump_to, because go() with the step already on screen draws nothing.
		enter = re.search(r"\n\tenter_view\(view, as, from_route, step\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(enter, "enter_view() is gone")
		self.assertIn("this.set_address(this.address_args(), !from_route)", enter.group(1))
		self.assertNotIn("pushState", enter.group(1))
		self.assertLess(enter.group(1).index("this.render()"), enter.group(1).index("this.set_address("))
		route = re.search(r"\n\troute\(args, mark\) \{(.*?)\n\t\}\n", self.code, re.S).group(1)
		self.assertLess(route.index("this.route_view(args, mark)"), route.index("this.jump_to("))

	def test_the_maps_day_chips_and_the_trip_sheet_write_no_history(self):
		# The Map is a view (its own entry, through enter_view like the others); its day chips are
		# a choice on that screen, and the trip sheet opens in a new tab: neither is a place to go
		# Back to. Nothing in the map's drawing, its Google Maps helper or the sheet's link touches
		# history, routes, or redraws the page (a redraw would rebuild the map from Google).
		for name, signature in (
			("view_map", r"view_map\(\$view, data\)"),
			("map_list", r"map_list\(\$list, places, day_list, day, all_days\)"),
			("sheet_link", r"sheet_link\(\$parent, label, url\)"),
			("contacts_block", r"contacts_block\(\$parent, contacts, hotels\)"),
		):
			body = re.search(rf"\n\t{signature} \{{(.*?)\n\t\}}\n", self.code, re.S)
			self.assertIsNotNone(body, f"{name}() is gone")
			for forbidden in ("history.", "set_address(", "open_view(", "enter_view(", "set_route(", "this.render()", "window.open("):
				self.assertNotIn(forbidden, body.group(1), f"{name} calls {forbidden}")
		tp_map = re.search(r"\nclass TpTripMap \{(.*?)\n\}\n", self.code, re.S)
		self.assertIsNotNone(tp_map, "TpTripMap is gone")
		for forbidden in ("history.", "set_address(", "set_route(", "innerHTML", ".render("):
			self.assertNotIn(forbidden, tp_map.group(1))
		# A marker's popup is built from elements, never from HTML in the data.
		self.assertIn("document.createElement(", tp_map.group(1))
		self.assertIn(".textContent = ", tp_map.group(1))

	def test_the_uploader_is_opened_only_by_attach_a_file_on_a_saved_trip(self):
		# frappe's uploader is a dialog loaded on demand (file_uploader.bundle.js): reached for
		# while a step draws or a route is handled, it is missing on a desk that has not loaded
		# it yet, and in the Back/Forward harness. A File is attached to a trip by its name, so
		# a trip not saved yet cannot have one. attach_files, called only from the button, is
		# the one place that touches it, after its guard.
		body = re.search(r"\n\tattach_files\(target\) \{(.*?)\n\t\}\n", self.code, re.S)
		self.assertIsNotNone(body, "attach_files() is gone")
		code = body.group(1)
		self.assertEqual(self.code.count("frappe.ui.FileUploader"), code.count("frappe.ui.FileUploader"))
		self.assertEqual(self.code.count("new frappe.ui.FileUploader("), 1)
		guard = code.index("if (!s || !s.name || !s.can_write) return;")
		self.assertLess(guard, code.index("new frappe.ui.FileUploader("))
		self.assertIn('doctype: "Travel Trip"', code)
		self.assertIn("make_attachments_public: false", code)
		# ...and the only caller is a click.
		self.assertEqual(
			re.findall(r"[^\n]*this\.attach_files\([^\n]*", self.code),
			['\t\t\t\t.on("click", () => this.attach_files(target));'],
		)

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
	"""/itinerary: the trip, person, screen and picture on screen are
	?trip=<name>&as=<employee|crew>&view=docs&file=<Trip Document>. A chip tap, a person pick,
	"Documents" and a picture are each one history entry, and Back / Forward load the entry's
	trip and person, or only redraw its screen and picture. Before, Back after switching trips
	left the page (or the home-screen app), and a reload or Back from /travel_guidelines showed
	the default trip instead of the one being read. A ?trip= outside the person's own list is
	asked for (the server's read permission decides); only a refusal replaces the entry.

	Since PR 3 of the Plan a Trip program the trip also has a Contacts card at the top and a
	"Print / save as PDF" link to the trip sheet. Neither is an entry: opening or shutting the
	card and opening the sheet write no history."""

	ITINERARY_JS = os.path.join(APP_DIR, "public", "js", "travel", "itinerary.js")
	CONTROLLER = os.path.join(APP_DIR, "www", "itinerary.py")

	def _controller_function(self, name, namespace):
		"""A real function from the controller, extracted with ast: importing the controller
		pulls in api.travel and the kiosk controller, far more than this suite's stub provides."""
		import ast

		tree = ast.parse(_read(self.CONTROLLER))
		wanted = [
			n
			for n in tree.body
			if (isinstance(n, ast.FunctionDef) and n.name == name)
			or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "ROUTE" for t in n.targets))
		]
		exec(compile(ast.Module(body=wanted, type_ignores=[]), self.CONTROLLER, "exec"), namespace)
		return namespace[name]

	def _login_redirect(self):
		from urllib.parse import quote

		return self._controller_function("login_redirect", {"quote": quote})

	def test_the_boot_cannot_end_its_script_block(self):
		"""itinerary.html prints the boot with ``| safe``; a trip purpose is typed by people and
		the boot now carries every trip the person owns."""
		import json
		import types

		fake_frappe = types.SimpleNamespace(as_json=lambda value: json.dumps(value, indent=1, sort_keys=True))
		script_json = self._controller_function("script_json", {"frappe": fake_frappe})
		boot = {"trips": [{"purpose": "</script><script>alert(1)</script> R&D"}]}
		out = script_json(boot)
		for char in "<>&":
			self.assertNotIn(char, out)
		self.assertEqual(json.loads(out), boot)
		self.assertIn("boot_json | safe", _read(os.path.join(APP_DIR, "www", "itinerary.html")))
		self.assertIn("context.boot_json = script_json(boot)", _read(self.CONTROLLER))

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
		# ...and so does the person being viewed.
		self.assertEqual(
			login_redirect("/itinerary?trip=TRIP-0007&as=HR-EMP-00002"),
			"/login?redirect-to=/itinerary%3Ftrip%3DTRIP-0007%26as%3DHR-EMP-00002",
		)
		# ...and the Documents screen, with the picture open on it.
		self.assertEqual(
			login_redirect("/itinerary?trip=TRIP-0007&view=docs&file=a1b2c3"),
			"/login?redirect-to=/itinerary%3Ftrip%3DTRIP-0007%26view%3Ddocs%26file%3Da1b2c3",
		)
		# Never anywhere but this page.
		self.assertEqual(login_redirect("//evil.example/x"), "/login?redirect-to=/itinerary")

	def test_history_calls_come_only_from_the_tap_and_the_boot(self):
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		self.assertNotIn("beforeunload", code)
		# One push, in writeTripEntry. It is called with push=true from exactly four taps, pinned
		# as the whole set: a trip chip (the trip only, which drops ?as=, ?view= and ?file=), a
		# person in the picker (trip and person, keeping the screen), a screen tab ("Documents",
		# or "Day by day" when the day list is not the entry behind) and a picture (the viewer,
		# &file=, over the screen it was opened from). Deliberately four since the Documents
		# screen and the picture viewer (was: two, the chip and the person picker).
		self.assertEqual(code.count("pushState("), 1)
		self.assertEqual(
			sorted(re.findall(r"writeTripEntry\(true[^;]*\);", code)),
			sorted(
				[
					"writeTripEntry(true, trip.name);",
					"writeTripEntry(true, state.currentTrip, choice.as, state.currentView);",
					"writeTripEntry(true, state.currentTrip, state.currentAs, view);",
					"writeTripEntry(true, state.currentTrip, state.currentAs, state.currentView, doc.name);",
				]
			),
		)
		self.assertRegex(
			code,
			r"chip\.addEventListener\('click', function \(\) \{\s*"
			r"if \(trip\.name !== state\.currentTrip\) writeTripEntry\(true, trip\.name\);",
		)
		self.assertRegex(
			code,
			r"chip\.addEventListener\('click', function \(\) \{\s*"
			r"if \(choice\.as !== shown\) writeTripEntry\(true, state\.currentTrip, choice\.as, state\.currentView\);",
		)
		# The screen tab and the picture push only from their own functions, which do nothing
		# while "Report a problem" is open: the panel owns Back and every history write then.
		for name in ("openScreen", "openPicture", "closePicture"):
			start = code.index(f"function {name}(")
			fn = code[start : code.index("\n\tfunction ", start + 1)]
			self.assertIn("captureOpen()", fn, name)
		# Close and Escape go Back onto the screen underneath rather than pushing it again.
		start = code.index("function closePicture(")
		close = code[start : code.index("\n\tfunction ", start + 1)]
		self.assertIn("window.history.back()", close)
		self.assertNotIn("writeTripEntry(true", close)
		# loadTrip, which popstate calls, never writes history...
		body = code[code.index("function loadTrip(") : code.index("function defaultTrip(")]
		self.assertNotIn("writeTripEntry", body)
		self.assertNotIn("State(", body)
		# ...and its answer is only shown while the same trip AND person are still asked for.
		self.assertEqual(body.count("state.currentTrip !== name || state.currentAs !== as"), 2)
		# The one history write a failed load makes is a replace: a refused trip or person
		# falls back in place, never as a new entry.
		start = code.index("function loadFailed(")
		failed = code[start : code.index("\n\tfunction ", start + 1)]
		self.assertIn("writeTripEntry(false", failed)
		self.assertNotIn("writeTripEntry(true", failed)
		self.assertNotIn("pushState", failed)

	def test_today_is_the_phones_date_and_data_is_never_markup(self):
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		# toISOString() is the UTC date: after 5 PM in Arizona "Today" moved to tomorrow.
		self.assertNotIn("toISOString", code)
		# The map popup is built from elements, like every other render (a POI name or an
		# activity is typed in by people).
		self.assertNotIn("bindPopup('", code)
		self.assertNotIn(".innerHTML = '<", code)

	def test_no_receipts_and_no_pdf_in_a_frame(self):
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		# A booking row's own `attachment` is its receipt now: money, which this page never
		# shows. Its files come from Trip Document (`documents`) instead.
		self.assertNotIn("attachment", code.lower())
		self.assertIn("item.documents", code)
		# A PDF opens in a new tab (the phone's own viewer): iOS draws only the first page of a
		# PDF in an iframe. And never as a download, which a boarding pass at the gate is not.
		self.assertNotIn("iframe", code.lower())
		self.assertNotIn(".download", code)
		self.assertNotIn("'download'", code)

	def _js_function(self, code, name):
		start = code.index(f"\n\tfunction {name}(")
		return code[start : code.index("\n\t}\n", start) + 3]

	def test_the_contacts_card_and_the_sheet_link_write_no_history(self):
		"""Opening or shutting the Contacts card is how this person likes the page, not a place
		to go Back to; the trip sheet opens in a new tab. Neither touches history, and the
		card's toggle redraws nothing (a redraw would take focus off the button)."""
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		for name in ("appendContacts", "contactsAreShut", "rememberContacts", "sheetLink", "sheetWhose", "appendPrint"):
			body = self._js_function(code, name)
			for forbidden in ("writeTripEntry", "State(", "history.", "render()", "loadTrip("):
				self.assertNotIn(forbidden, body, f"{name} calls {forbidden}")
		# The sheet is opened as a link in a new tab, never navigated to from script.
		print_link = self._js_function(code, "appendPrint")
		self.assertIn("link.target = '_blank'", print_link)
		self.assertIn("link.rel = 'noopener'", print_link)
		self.assertNotIn("location", print_link)

	def test_a_contact_link_is_built_in_one_place_each(self):
		"""Phones, emails and map links are typed in by people (a Supplier's address, a
		Contact): a tel: link is built from digits and a leading + only, a mailto: only from an
		address that is one, and a web link only from https. One builder each, so no other
		render can put a raw value into an href."""
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		for scheme, builder in (("'tel:'", "telHref"), ("'mailto:'", "mailHref")):
			self.assertEqual(code.count(scheme), 1, scheme)
			self.assertIn(scheme, self._js_function(code, builder))
		self.assertIn(".replace(/\\D/g, '')", self._js_function(code, "telHref"))
		self.assertIn("/^https:\\/\\/[^\\s]+$/i", self._js_function(code, "webHref"))
		# Every value is text: the one innerHTML left is the page clearing itself.
		self.assertEqual(re.findall(r"[^\n]*innerHTML[^\n]*", code), ["\t\troot.innerHTML = '';"])

	def test_local_storage_is_only_touched_inside_a_try(self):
		"""The card remembers being shut in localStorage, which throws in a private window or
		with site data blocked, and is absent in the Back/Forward harness. Every use sits in a
		try, so the page draws either way."""
		code = _strip_js_comments(_read(self.ITINERARY_JS))
		uses = [m.start() for m in re.finditer(r"localStorage", code)]
		self.assertEqual(len(uses), 3)
		for at in uses:
			before = code[:at]
			self.assertGreater(before.rfind("try {"), before.rfind("} catch"), code[at - 80 : at + 40])

	def test_the_page_in_a_fake_browser(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		harness = os.path.join(os.path.dirname(APP_DIR), "scripts", "test_web_flow_history.js")
		result = subprocess.run(
			[node, harness, "itinerary"],
			capture_output=True,
			text=True,
			encoding="utf-8",
			check=False,
			timeout=120,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
	unittest.main()

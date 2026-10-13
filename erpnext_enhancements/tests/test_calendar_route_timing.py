# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar sync C3 (v1.606.0): route stops get real times, a leave-by line, and an opt-in ping.

What this pins, because each of these fails quietly:

* **The times are the route view's.** ``route_slots.day_slots`` gives exactly the arrival and
  departure ``api.project_planner.route_payload`` prints for the same route (leave the shop at the
  day start, drive, work the hours, wait for a slot), and leave-by is the arrival less the drive.
* **Which events are timed.** A routed single-day stop in the near window is a ``dateTime`` event in
  the site's time zone; a full-day task, a multi-day task, a pencil, a day beyond the near window, a
  day whose route failed, and a near window of 0 stay all-day, and the last four never ask for a
  route at all. A past day and finished work keep the times stored on their link.
* **The leave-by text** ("Leave by 7:35 am") leads the description of the first stop only.
* **The lead rule.** One event per task: timed from the lead's route, else the first crew member's,
  else the next one who has a route; "(times differ by person)" when another crew member's slot
  differs. A visit's technician is its lead; a projected visit is found by its key.
* **A change re-times the whole day, coalesced.** An item job queues one day job per touched day
  after commit, with a stable ``job_id`` and ``deduplicate``; a publish of ten tasks on one day is
  one queued day job, not ten (and never a hundred). The day job hash-skips unchanged stops.
* **Content hash and snap-back still hold** for timed events: an unchanged route costs no call, a
  re-timed one updates, and an event moved by hand in Google is rewritten to ERPNext's times.
* **Leave-by pings.** Only opted-in people, only the first stop of their day, never a pencil or a
  day with no hours, only when due, and at most once per stop per day through a database row,
  so a cache flush (the deploy's FLUSHDB) sends nothing twice.
* **The switch off means nothing is sent**: no Google call from any C3 path, no day job, no ping.

Bench-free: reuses ``test_calendar_sync``'s ``frappe`` and ``requests`` stubs (its own copy, set up
and torn down here). Its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_calendar_route_timing
"""

import datetime
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import test_calendar_sync as base

APP = Path(__file__).resolve().parents[1]
DOCTYPES = APP / "project_enhancements" / "doctype"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TODAY = base.TODAY  # Mon 2026-10-12
SITE = base.SITE
TZ = "America/Denver"

SHOP = (40.70, -111.90)
A = (40.75, -111.85)
B = (40.80, -111.80)

frappe = None
payload = None
engine = None
sources = None
timing = None
route_slots = None
routing = None
leave_by = None
notices = None
planner_api = None


def setUpModule():
	global frappe, payload, engine, sources, timing, route_slots, routing, leave_by, notices, planner_api
	base.setUpModule()
	frappe = base.frappe
	frappe.utils.flt = lambda value, precision=None: float(value or 0)
	frappe.utils.get_system_timezone = lambda: TZ
	payload = base.payload
	engine = base.engine
	sources = base.sources
	timing = importlib.import_module("erpnext_enhancements.project_enhancements.calendar_sync.timing")
	route_slots = importlib.import_module("erpnext_enhancements.project_enhancements.route_slots")
	routing = importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	leave_by = importlib.import_module("erpnext_enhancements.project_enhancements.leave_by")
	notices = importlib.import_module("erpnext_enhancements.project_enhancements.planner_notices")
	planner_api = importlib.import_module("erpnext_enhancements.api.project_planner")


def tearDownModule():
	base.tearDownModule()


# ---------------------------------------------------------------------- fixtures

MATRIX = {
	(SHOP, A): {"minutes": 25.0, "km": 20.0, "source": "google"},
	(A, SHOP): {"minutes": 25.0, "km": 20.0, "source": "google"},
	(A, B): {"minutes": 15.0, "km": 9.0, "source": "google"},
	(B, A): {"minutes": 30.0, "km": 9.0, "source": "google"},
	(SHOP, B): {"minutes": 40.0, "km": 30.0, "source": "google"},
	(B, SHOP): {"minutes": 40.0, "km": 30.0, "source": "google"},
}


def _booking(kind, ref, hours, slot=None, key=None, label=None):
	booking = {
		"kind": kind,
		"ref": ref,
		"label": label or ref,
		"project": "PRJ-1",
		"hours": hours,
		"slot": slot,
	}
	if key:
		booking["key"] = key
	return booking


def _route(bookings_and_points, shop=SHOP):
	stops = [routing._stop(booking, point) for booking, point in bookings_and_points]
	return routing.build_route(shop, stops, MATRIX)


def _austin_day(**extra):
	"""Austin's Monday: shop 8:00 → TASK-1 at A (2h) → SMR-1 at B (1.5h) → shop."""
	return _route(
		[
			(_booking("task", "TASK-1", 2.0, label="Pump vault"), A),
			(_booking("visit", "SMR-1", 1.5, key="SMR-1", label="Riverwalk · Spring start"), B),
		]
	)


def _days(**people):
	"""``route_slots.compute`` days: ``{(resource, TODAY): {...}}`` from routes."""
	out = {}
	for resource, route in people.items():
		out[(resource, TODAY)] = {
			"slots": route_slots.day_slots(route, "08:00:00", planner_api.merge_unlocated),
			"capacity": 8.0,
			"off": None,
		}
	return out


def _computed(days=None):
	return {
		"days": days if days is not None else _days(AUSTIN=_austin_day()),
		"user_to_resource": {"austin@x.com": "AUSTIN", "jose@x.com": "JOSE"},
		"labels": {"AUSTIN": "Austin Smith", "JOSE": "Jose Garcia"},
		"users": {"AUSTIN": "austin@x.com", "JOSE": "jose@x.com"},
		"day_start": "08:00:00",
	}


def _task(**overrides):
	item = base._task(
		start=TODAY,
		end=TODAY,
		name="TASK-1",
		hours=2,
		crew=[
			{"name": "Austin Smith", "lead": True, "resource": "AUSTIN"},
			{"name": "Jose Garcia", "resource": "JOSE"},
		],
		weather=[],
	)
	item.update(overrides)
	return item


def _settings(**values):
	doc = base._Doc(dict(frappe.site.settings))
	doc.update(values)
	return doc


class Case(unittest.TestCase):
	def setUp(self):
		base._reset()
		timing.forget()
		self.computed = []
		self.answer = _computed()

		def compute(start, end, resources=None, google=True):
			self.computed.append((start, end, google))
			if isinstance(self.answer, Exception):
				raise self.answer
			return self.answer

		patcher = mock.patch.object(route_slots, "compute", side_effect=compute)
		patcher.start()
		self.addCleanup(patcher.stop)
		today = mock.patch.object(timing, "_today", return_value=TODAY)
		today.start()
		self.addCleanup(today.stop)


# ---------------------------------------------------------------------- the route view's arithmetic


class RouteSlots(unittest.TestCase):
	def setUp(self):
		base._reset()

	def test_slots_match_the_route_view(self):
		route = _austin_day()
		slots = route_slots.day_slots(route, "08:00:00", planner_api.merge_unlocated)
		cell = {"bookings": [s["booking"] for s in route["stops"]], "off": None}
		view = planner_api.route_payload(
			{"name": "AUSTIN", "label": "Austin Smith"},
			TODAY,
			cell,
			route,
			{"day_start_time": "08:00:00"},
			{},
			{},
			{},
			"",
		)
		for stop in view["stops"]:
			key = route_slots.stop_key(stop["kind"], stop["ref"])
			slot = slots[key]
			self.assertEqual(routing.fmt_clock(slot["arrive"]), stop["arrive"])
			self.assertEqual(routing.fmt_clock(slot["depart"]), stop["depart"])
			self.assertEqual(slot["order"], stop["order"])

	def test_leave_the_shop_at_day_start_drive_then_work(self):
		slots = route_slots.day_slots(_austin_day(), "08:00:00", planner_api.merge_unlocated)
		first, second = slots[("task", "TASK-1")], slots[("visit", "SMR-1")]
		self.assertTrue(first["first"])
		self.assertFalse(second["first"])
		self.assertEqual(
			(first["arrive"], first["depart"], first["leave_by"]), (8 * 60 + 25, 10 * 60 + 25, 480)
		)
		self.assertEqual((second["arrive"], second["depart"]), (10 * 60 + 40, 12 * 60 + 10))
		self.assertEqual(second["leave_by"], 10 * 60 + 25)

	def test_a_slotted_first_stop_moves_leave_by_not_day_start(self):
		route = _route([(_booking("task", "TASK-9", 1.0, slot=["10:00", "11:00"]), A)])
		slot = route_slots.day_slots(route, "08:00:00", planner_api.merge_unlocated)[("task", "TASK-9")]
		self.assertEqual(slot["arrive"], 600)
		self.assertEqual(slot["leave_by"], 600 - 25)

	def test_an_unlocated_stop_has_no_leave_by(self):
		route = _route([(_booking("task", "TASK-7", 1.0), None)])
		slot = route_slots.day_slots(route, "08:00:00", planner_api.merge_unlocated)[("task", "TASK-7")]
		self.assertIsNone(slot["leave_by"])

	def test_a_projected_visit_is_keyed_by_its_projection(self):
		self.assertEqual(
			route_slots.stop_key("visit", "MNT-1", "MNT-1|SN|2026-10-12"), ("visit", "MNT-1|SN|2026-10-12")
		)
		self.assertEqual(route_slots.stop_key("rental", "TASK-3"), ("task", "TASK-3"))
		self.assertIsNone(route_slots.stop_key("block", "PBLK-1"))
		self.assertIsNone(route_slots.stop_key("drive", None))


# ---------------------------------------------------------------------- event_times


class EventTimes(Case):
	def attach(self, item, doc=None, link=None, google=True):
		return timing.attach(item, doc or _settings(), link, today=TODAY, google=google)

	def test_a_routed_stop_is_a_timed_event(self):
		item = self.attach(_task())
		times = payload.event_times(item)
		self.assertEqual(times["start"], {"dateTime": "2026-10-12T08:25:00", "timeZone": TZ})
		self.assertEqual(times["end"], {"dateTime": "2026-10-12T10:25:00", "timeZone": TZ})
		self.assertEqual(item["timing"]["leave_by"], 480)

	def test_a_full_day_task_stays_all_day(self):
		item = self.attach(_task(full_day=True))
		self.assertNotIn("timing", item)
		self.assertEqual(payload.event_times(item)["start"], {"date": "2026-10-12"})
		self.assertEqual(self.computed, [])

	def test_a_multi_day_task_stays_all_day(self):
		item = self.attach(_task(end=TODAY + datetime.timedelta(days=2)))
		self.assertEqual(payload.event_times(item)["end"], {"date": "2026-10-15"})
		self.assertEqual(self.computed, [])

	def test_a_pencil_stays_all_day_and_asks_no_route(self):
		item = self.attach(_task(tentative=True))
		self.assertNotIn("timing", item)
		self.assertEqual(self.computed, [])

	def test_a_day_beyond_the_near_window_stays_all_day(self):
		far = TODAY + datetime.timedelta(days=14)
		item = self.attach(_task(start=far, end=far))
		self.assertNotIn("timing", item)
		self.assertEqual(self.computed, [])
		last = TODAY + datetime.timedelta(days=13)
		self.attach(_task(start=last, end=last))
		self.assertEqual(self.computed, [(last, last, True)])

	def test_a_near_window_of_zero_switches_timing_off(self):
		item = self.attach(_task(), doc=_settings(timed_days_ahead=0))
		self.assertNotIn("timing", item)
		self.assertEqual(self.computed, [])

	def test_a_missing_setting_reads_as_fourteen_days(self):
		self.assertEqual(timing.near_days(_settings(timed_days_ahead=None)), 14)
		self.assertEqual(timing.near_window(_settings(), TODAY)[1], TODAY + datetime.timedelta(days=13))

	def test_a_failed_route_falls_back_to_all_day_and_logs_once(self):
		self.answer = RuntimeError("routing broke")
		self.assertNotIn("timing", self.attach(_task()))
		timing.forget()
		self.assertNotIn("timing", self.attach(_task(name="TASK-2")))
		titles = [t for t, _m in frappe.site.logs]
		self.assertEqual(titles, ["Calendar sync: route times could not be worked out"])

	def test_a_day_is_computed_once_per_job(self):
		self.attach(_task())
		self.attach(_task(name="TASK-2"))
		self.assertEqual(len(self.computed), 1)

	def test_a_past_day_and_finished_work_keep_what_they_had(self):
		yesterday = TODAY - datetime.timedelta(days=1)
		stored = {"date": str(yesterday), "start": 500, "end": 620, "leave_by": None, "differs": False}
		link = base._Doc({"timing": json.dumps(stored)})
		item = self.attach(_task(start=yesterday, end=yesterday), link=link)
		self.assertEqual(item["timing"], stored)
		self.assertEqual(self.computed, [])
		self.assertNotIn("timing", self.attach(_task(start=yesterday, end=yesterday), link=None))
		moved = base._Doc({"timing": json.dumps(dict(stored, date="2026-10-01"))})
		self.assertNotIn("timing", self.attach(_task(start=yesterday, end=yesterday), link=moved))
		done = dict(stored, date=str(TODAY))
		item = self.attach(_task(state="finished"), link=base._Doc({"timing": json.dumps(done)}))
		self.assertEqual(item["timing"], done)
		self.assertEqual(self.computed, [])

	def test_blocks_and_day_notes_are_never_timed(self):
		for kind in ("block", "day_note"):
			item = self.attach(
				{"doctype": "X", "name": "N", "kind": kind, "state": "active", "start": TODAY, "end": TODAY}
			)
			self.assertNotIn("timing", item)
		self.assertEqual(self.computed, [])

	def test_the_preview_asks_google_nothing(self):
		self.attach(_task(), google=False)
		self.assertEqual(self.computed, [(TODAY, TODAY, False)])


class FullDayRule(unittest.TestCase):
	def test_the_engines_rule_for_a_whole_day_task(self):
		full = sources.task_is_full_day
		self.assertTrue(full({"expected_time": 0}, []))
		self.assertTrue(full({"expected_time": None}, [{"hours": None}]))
		self.assertFalse(full({"expected_time": 4}, []))
		self.assertFalse(full({"expected_time": 0, "custom_rental_task_kind": "Setup"}, []))
		self.assertFalse(full({"expected_time": 0}, [{"hours": 3}]))
		slot = {
			"expected_time": 0,
			"custom_start_datetime": "2026-10-12 09:00:00",
			"custom_end_datetime": "2026-10-12 11:00:00",
		}
		self.assertFalse(full(slot, []))


# ---------------------------------------------------------------------- leave-by text


class LeaveByText(Case):
	def test_the_text(self):
		self.assertEqual(payload.leave_by_text(455), "Leave by 7:35 am")
		self.assertEqual(payload.leave_by_text(13 * 60), "Leave by 1 pm")
		self.assertEqual(payload.leave_by_text(None), "")

	def test_first_stop_leads_its_description(self):
		item = timing.attach(_task(), _settings(), today=TODAY)
		self.assertEqual(payload.description_for(item).splitlines()[0], "Leave by 8 am")

	def test_a_later_stop_has_no_leave_by(self):
		visit = {
			"doctype": "Sapphire Maintenance Record",
			"name": "SMR-1",
			"kind": "visit",
			"calendar": "maintenance",
			"state": "active",
			"start": TODAY,
			"end": TODAY,
			"crew": [{"name": "Austin Smith", "lead": True, "user": "austin@x.com"}],
			"hours": 1.5,
		}
		item = timing.attach(visit, _settings(), today=TODAY)
		self.assertEqual(item["timing"]["start"], 10 * 60 + 40)
		self.assertIsNone(item["timing"]["leave_by"])
		self.assertNotIn("Leave by", payload.description_for(item))

	def test_an_all_day_event_has_no_timing_lines(self):
		self.assertEqual(payload.timing_lines(_task()), [])


# ---------------------------------------------------------------------- the lead rule


class LeadRule(Case):
	def jose_day(self, hours=2.0):
		# Both slotted, so the order is fixed: TASK-1 is Jose's second stop, in the afternoon.
		return _route(
			[
				(_booking("task", "TASK-0", 1.0, slot=["08:00", "09:00"]), B),
				(_booking("task", "TASK-1", hours, slot=["13:00", "15:00"]), A),
			]
		)

	def test_the_lead_times_the_event_and_a_different_slot_is_noted(self):
		self.answer = _computed(_days(AUSTIN=_austin_day(), JOSE=self.jose_day()))
		item = timing.attach(_task(), _settings(), today=TODAY)
		self.assertEqual(item["timing"]["start"], 8 * 60 + 25)
		self.assertTrue(item["timing"]["differs"])
		self.assertEqual(item["timing"]["person"], "Austin")
		self.assertIn("Times are Austin's route (times differ by person)", payload.description_for(item))

	def test_no_lead_means_the_first_crew_member(self):
		self.answer = _computed(_days(AUSTIN=_austin_day(), JOSE=self.jose_day()))
		crew = [{"name": "Jose Garcia", "resource": "JOSE"}, {"name": "Austin Smith", "resource": "AUSTIN"}]
		item = timing.attach(_task(crew=crew), _settings(), today=TODAY)
		self.assertEqual(item["timing"]["person"], "Jose")
		self.assertIsNone(item["timing"]["leave_by"])  # TASK-1 is Jose's second stop

	def test_a_lead_with_no_route_falls_to_the_next(self):
		self.answer = _computed(_days(JOSE=self.jose_day()))
		item = timing.attach(_task(), _settings(), today=TODAY)
		self.assertEqual(item["timing"]["person"], "Jose")
		self.assertFalse(item["timing"]["differs"])

	def test_same_slots_are_not_noted(self):
		self.answer = _computed(_days(AUSTIN=_austin_day(), JOSE=_austin_day()))
		item = timing.attach(_task(), _settings(), today=TODAY)
		self.assertFalse(item["timing"]["differs"])
		self.assertNotIn("differ", payload.description_for(item))

	def test_nobody_routed_is_all_day(self):
		self.answer = _computed({})
		self.assertNotIn("timing", timing.attach(_task(), _settings(), today=TODAY))

	def test_a_projected_visit_by_its_technician_and_key(self):
		key = "MNT-1|SN-1|2026-10-12"
		route = _route([(_booking("visit", "MNT-1", 2.0, key=key), A)])
		self.answer = _computed(_days(AUSTIN=route))
		item = {
			"doctype": "Sapphire Maintenance Contract",
			"name": "MNT-1",
			"key": key,
			"kind": "projected",
			"calendar": "maintenance",
			"state": "active",
			"tentative": True,
			"start": TODAY,
			"end": TODAY,
			"crew": [{"name": "Austin Smith", "lead": True, "user": "austin@x.com"}],
		}
		timed = timing.attach(item, _settings(), today=TODAY)
		self.assertEqual(timed["timing"]["start"], 8 * 60 + 25)
		self.assertEqual(payload.build_event(timed, SITE)["status"], "tentative")


# ---------------------------------------------------------------------- engine: hash, snap-back, fan-out


class TimedEngine(base.EngineCase):
	def setUp(self):
		super().setUp()
		timing.forget()
		self.answer = _computed()
		patcher = mock.patch.object(route_slots, "compute", side_effect=lambda *a, **k: self.answer)
		patcher.start()
		self.addCleanup(patcher.stop)
		for target in (timing, sources):
			today = mock.patch.object(target, "_today", return_value=TODAY)
			today.start()
			self.addCleanup(today.stop)
		self.states = {}
		self.days_queued = []

		def enqueue(method, **kwargs):
			frappe.site.enqueued.append((method, kwargs))
			if method == engine.DAY_JOB:
				self.days_queued.append(kwargs["job_id"])
				self.states[kwargs["job_id"]] = "queued"

		for patcher in (
			mock.patch.object(frappe, "enqueue", side_effect=enqueue),
			mock.patch.object(engine, "_job_state", side_effect=lambda job_id: self.states.get(job_id)),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_timed_event_is_written_with_its_times_and_stored_on_the_link(self):
		self.assertEqual(self.reconcile(_task())[0], "create")
		event = next(iter(self.google.events.values()))
		self.assertEqual(event["start"]["dateTime"], "2026-10-12T08:25:00")
		self.assertTrue(event["description"].startswith("Leave by 8 am"))
		stored = json.loads(self.link()["timing"])
		self.assertEqual((stored["start"], stored["end"], stored["leave_by"]), (505, 625, 480))

	def test_unchanged_route_costs_no_call_and_a_re_time_updates(self):
		self.reconcile(_task())
		timing.forget()
		calls = len(self.google.calls)
		self.assertEqual(self.reconcile(_task())[0], "unchanged")
		self.assertEqual(len(self.google.calls), calls)
		# Another stop at the same site, booked for 8:00, lands on Austin's day: TASK-1 moves later.
		timing.forget()
		self.answer = _computed(
			_days(
				AUSTIN=_route(
					[
						(_booking("task", "TASK-0", 1.0, slot=["08:00", "09:00"]), A),
						(_booking("task", "TASK-1", 2.0), A),
					]
				)
			)
		)
		self.assertEqual(self.reconcile(_task())[0], "update")
		event = next(iter(self.google.events.values()))
		self.assertEqual(event["start"]["dateTime"], "2026-10-12T09:25:00")
		self.assertNotIn("Leave by", event["description"])

	def test_moving_out_of_the_window_turns_it_all_day(self):
		self.reconcile(_task())
		far = TODAY + datetime.timedelta(days=30)
		self.assertEqual(self.reconcile(_task(start=far, end=far))[0], "update")
		event = next(iter(self.google.events.values()))
		self.assertEqual(event["start"], {"date": str(far)})
		self.assertEqual(self.link()["timing"], "")

	def test_snapback_rewrites_a_timed_event_moved_in_google(self):
		self.reconcile(_task())
		link = self.link()
		cal = "projects@group.calendar.google.com"
		moved = dict(
			self.google.events[(cal, link["event_id"])],
			etag='"someone"',
			start={"dateTime": "2026-10-12T14:00:00", "timeZone": TZ},
		)
		self.google.pages[cal] = [{"items": [moved], "nextSyncToken": "t"}]
		timing.forget()
		with mock.patch.object(sources, "item_for", return_value=_task()):
			engine.snapback()
		self.assertIn(("update", cal, link["event_id"]), self.google.calls)
		self.assertEqual(
			self.google.events[(cal, link["event_id"])]["start"]["dateTime"], "2026-10-12T08:25:00"
		)

	def run_item(self, item, name="TASK-1"):
		timing.forget()
		with mock.patch.object(sources, "item_for", return_value=item):
			engine.run_item_job("Task", name)

	def test_an_item_job_queues_its_day_after_commit(self):
		self.run_item(_task())
		self.assertEqual(self.days_queued, ["calendar_sync::day::2026-10-12"])
		_method, kwargs = frappe.site.enqueued[-1]
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["day"], "2026-10-12")

	def test_a_publish_of_many_tasks_is_one_day_job_not_n_squared(self):
		for n in range(10):
			self.run_item(_task(name=f"TASK-{n}"), f"TASK-{n}")
		self.assertEqual(self.days_queued, ["calendar_sync::day::2026-10-12"])
		# The day job starts (it may have read before the next change): one more is allowed...
		self.states["calendar_sync::day::2026-10-12"] = "started"
		for n in range(10, 15):
			self.run_item(_task(name=f"TASK-{n}"), f"TASK-{n}")
		self.assertEqual(
			self.days_queued, ["calendar_sync::day::2026-10-12", "calendar_sync::day::2026-10-12::again"]
		)
		# ...and with both running, nothing more (the nightly repair is the backstop).
		self.states["calendar_sync::day::2026-10-12::again"] = "started"
		self.run_item(_task(name="TASK-99"), "TASK-99")
		self.assertEqual(len(self.days_queued), 2)

	def test_a_move_queues_the_old_day_and_the_new_one(self):
		self.run_item(_task())
		self.states.clear()
		self.days_queued.clear()
		tomorrow = TODAY + datetime.timedelta(days=1)
		self.run_item(_task(start=tomorrow, end=tomorrow))
		self.assertEqual(
			sorted(self.days_queued), ["calendar_sync::day::2026-10-12", "calendar_sync::day::2026-10-13"]
		)

	def test_a_pencil_and_a_far_day_fan_out_nothing(self):
		self.run_item(_task(tentative=True))
		far = TODAY + datetime.timedelta(days=40)
		self.run_item(_task(name="TASK-F", start=far, end=far), "TASK-F")
		self.assertEqual(self.days_queued, [])

	def test_a_day_job_re_times_every_stop_once_and_is_idempotent(self):
		items = {("Task", f"TASK-{n}"): _task(name=f"TASK-{n}") for n in range(3)}
		with (
			mock.patch.object(sources, "refs_in_window", return_value=list(items)),
			mock.patch.object(sources, "item_for", side_effect=lambda d, n, k=None: items[(d, n)]),
			mock.patch.object(sources, "projected_items", return_value=[]),
		):
			engine.run_day_job("2026-10-12")
			writes = [c for c in self.google.calls if c[0] in ("insert", "update")]
			self.assertEqual(len(writes), 3)
			timing.forget()
			self.google.calls.clear()
			engine.run_day_job("2026-10-12")
		self.assertEqual(self.google.calls, [])
		self.assertEqual(self.days_queued, [])  # a day job fans out nothing

	def test_a_day_job_outside_the_window_does_nothing(self):
		with mock.patch.object(sources, "refs_in_window", side_effect=AssertionError("read")):
			engine.run_day_job("2026-10-01")
			engine.run_day_job("2026-12-01")
		self.assertEqual(self.built, [])

	def test_switch_off_means_no_google_call_no_day_job_and_no_route(self):
		frappe.site.settings["enabled"] = 0
		calls = []
		with (
			mock.patch.object(sources, "item_for", side_effect=AssertionError("read while off")),
			mock.patch.object(sources, "refs_in_window", side_effect=AssertionError("read while off")),
			mock.patch.object(route_slots, "compute", side_effect=lambda *a, **k: calls.append(a)),
		):
			engine.run_item_job("Task", "TASK-1")
			engine.run_day_job("2026-10-12")
		self.assertEqual(self.built, [])
		self.assertEqual(self.google.calls, [])
		self.assertEqual(self.days_queued, [])
		self.assertEqual(calls, [])


# ---------------------------------------------------------------------- leave-by pings


def _ping_days(capacity=8.0, tentative=False, first_located=True):
	route = _austin_day() if first_located else _route([(_booking("task", "TASK-1", 2.0), None)])
	slots = route_slots.day_slots(route, "08:00:00", planner_api.merge_unlocated)
	if tentative:
		for slot in slots.values():
			slot["tentative"] = True
	return {("AUSTIN", TODAY): {"slots": slots, "capacity": capacity, "off": None}}


AUSTIN = {"name": "AUSTIN", "user": "austin@x.com", "resource_name": "Austin Smith"}


class PingSelection(unittest.TestCase):
	def setUp(self):
		base._reset()

	def select(self, days=None, now=7 * 60 + 52, lead=10, people=(AUSTIN,)):
		return leave_by.select(list(people), days if days is not None else _ping_days(), TODAY, now, lead)

	def test_the_first_stop_is_due_from_lead_minutes_before(self):
		found = self.select()
		self.assertEqual(len(found), 1)
		self.assertEqual(found[0]["key"], ("task", "TASK-1"))
		self.assertEqual(found[0]["slot"]["leave_by"], 480)
		self.assertEqual(self.select(now=7 * 60 + 49), [])
		self.assertEqual(len(self.select(now=8 * 60 + 10)), 1)
		self.assertEqual(self.select(now=8 * 60 + 11), [])

	def test_never_a_pencil_a_day_off_or_an_unlocated_stop(self):
		self.assertEqual(self.select(days=_ping_days(tentative=True)), [])
		self.assertEqual(self.select(days=_ping_days(capacity=0)), [])
		self.assertEqual(self.select(days=_ping_days(first_located=False)), [])
		self.assertEqual(self.select(days={}), [])

	def test_nobody_without_a_login(self):
		self.assertEqual(self.select(people=[dict(AUSTIN, user=None)]), [])

	def test_message(self):
		slot = _ping_days()[("AUSTIN", TODAY)]["slots"][("task", "TASK-1")]
		headline, lines = leave_by.message(slot)
		self.assertEqual(headline, "Leave by 8 am for Pump vault")
		self.assertEqual(lines, ["First stop: on site at 8:25 am (25 min drive)"])

	def test_lead_minutes_default_and_bounds(self):
		self.assertEqual(leave_by.lead_minutes(None), 10)
		self.assertEqual(leave_by.lead_minutes(0), 0)
		self.assertEqual(leave_by.lead_minutes(500), 120)

	def test_log_key_fits_its_column(self):
		key = leave_by.log_key("austin@x.com", TODAY, "visit:" + "M" * 300)
		self.assertLessEqual(len(key), 140)


class PingSending(unittest.TestCase):
	def setUp(self):
		base._reset()
		self.rows = {}
		self.sent = []
		self.computed = []
		self.on = True

		def exists(doctype, name=None, *a, **k):
			return doctype == "Planner Ping Log" and name in self.rows

		def get_doc(data, *a, **k):
			rows = self.rows

			class Row(dict):
				def insert(self, ignore_permissions=False):
					if self["log_key"] in rows:
						raise type("UniqueValidationError", (Exception,), {})()
					rows[self["log_key"]] = dict(self)
					return self

			return Row(data)

		def set_value(doctype, name, field, value=None, update_modified=True):
			self.rows[name][field] = value

		def compute(start, end, resources=None, google=True):
			self.computed.append((start, resources, google))
			return {"days": _ping_days()}

		patches = [
			mock.patch.object(frappe.db, "exists", side_effect=exists),
			mock.patch.object(frappe.db, "rollback", create=True),
			mock.patch.object(frappe.db, "set_value", side_effect=set_value, create=True),
			mock.patch.object(frappe, "get_doc", side_effect=get_doc),
			mock.patch.object(leave_by, "is_on", side_effect=lambda: self.on),
			mock.patch.object(leave_by, "opted_in", return_value=[AUSTIN]),
			mock.patch.object(leave_by, "_lead", return_value=10),
			mock.patch.object(route_slots, "compute", side_effect=compute),
			mock.patch.object(notices, "_bell", side_effect=lambda *a, **k: self.sent.append(("bell", a))),
			mock.patch.object(
				notices, "_text", side_effect=lambda *a, **k: self.sent.append(("text", a)) or True
			),
			mock.patch.object(notices, "_email", side_effect=lambda *a, **k: self.sent.append(("email", a))),
			mock.patch.object(notices, "route_url", return_value="https://erp.example.com/route"),
		]
		for patcher in patches:
			patcher.start()
			self.addCleanup(patcher.stop)

	def at(self, hour, minute):
		return datetime.datetime.combine(TODAY, datetime.time(hour, minute))

	def test_one_ping_per_stop_per_day_even_after_a_cache_flush(self):
		self.assertEqual(leave_by.send_leave_by_pings(self.at(7, 50)), 1)
		self.assertEqual([kind for kind, _a in self.sent], ["bell", "text"])
		bell = self.sent[0][1]
		self.assertEqual(bell[0], "austin@x.com")
		self.assertEqual(bell[1], "Leave by 8 am for Pump vault")
		self.assertEqual(bell[3], "TASK-1")
		# The deploy flushes redis: the claim is a database row, so nothing goes twice.
		frappe.site.cache.clear()
		frappe.local = types.SimpleNamespace(site=SITE)
		self.assertEqual(leave_by.send_leave_by_pings(self.at(7, 55)), 0)
		self.assertEqual(leave_by.send_leave_by_pings(self.at(8, 0)), 0)
		self.assertEqual(len(self.rows), 1)
		row = next(iter(self.rows.values()))
		self.assertEqual(
			(row["user"], row["stop_ref"], row["channels"]), ("austin@x.com", "task:TASK-1", "bell, text")
		)

	def test_no_google_request_for_a_ping(self):
		leave_by.send_leave_by_pings(self.at(7, 50))
		self.assertEqual(self.computed, [(TODAY, ["AUSTIN"], False)])

	def test_email_when_there_is_no_cell_number(self):
		with mock.patch.object(notices, "_text", return_value=False):
			leave_by.send_leave_by_pings(self.at(7, 50))
		self.assertEqual([kind for kind, _a in self.sent], ["bell", "email"])

	def test_not_due_yet_sends_nothing_and_claims_nothing(self):
		self.assertEqual(leave_by.send_leave_by_pings(self.at(6, 0)), 0)
		self.assertEqual((self.sent, self.rows), ([], {}))

	def test_switch_off_or_nobody_opted_in_sends_nothing_and_asks_no_route(self):
		self.on = False
		self.assertEqual(leave_by.send_leave_by_pings(self.at(7, 50)), 0)
		self.on = True
		with mock.patch.object(leave_by, "opted_in", return_value=[]):
			self.assertEqual(leave_by.send_leave_by_pings(self.at(7, 50)), 0)
		self.assertEqual((self.sent, self.rows, self.computed), ([], {}, []))


# ---------------------------------------------------------------------- wiring


class Wiring(unittest.TestCase):
	def test_ping_job_is_scheduled_every_five_minutes(self):
		events = base._literal("scheduler_events")
		self.assertEqual(
			events["cron"]["4-59/5 * * * *"],
			["erpnext_enhancements.project_enhancements.leave_by.send_leave_by_pings"],
		)
		self.assertIn(
			"erpnext_enhancements.travel_management.change_alerts.send_due_change_alerts",
			events["cron"]["*/5 * * * *"],
		)
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertEqual(hooks.count('"4-59/5 * * * *"'), 1, "a repeated cron key replaces the first silently")

	def _fields(self, doctype_dir):
		meta = json.loads((DOCTYPES / doctype_dir / f"{doctype_dir}.json").read_text(encoding="utf-8"))
		return meta, {f["fieldname"]: f for f in meta["fields"]}

	def test_settings_fields_and_defaults(self):
		_meta, fields = self._fields("calendar_sync_settings")
		self.assertEqual(fields["timed_days_ahead"]["default"], "14")
		_meta, fields = self._fields("project_planner_settings")
		self.assertEqual(fields["leave_by_pings"]["default"], "0")
		self.assertEqual(fields["leave_by_ping_minutes"]["default"], "10")
		_meta, fields = self._fields("planner_resource")
		self.assertEqual(fields["ping_at_leave_by"]["default"], "0")
		_meta, fields = self._fields("calendar_sync_link")
		self.assertEqual(fields["timing"]["fieldtype"], "Small Text")

	def test_ping_log_is_claimed_by_a_unique_key(self):
		meta, fields = self._fields("planner_ping_log")
		self.assertEqual(meta["autoname"], "field:log_key")
		self.assertEqual(fields["log_key"].get("unique"), 1)
		self.assertEqual(meta["module"], "Project Enhancements")

	def test_defaults_patch_is_registered(self):
		self.assertIn(
			"erpnext_enhancements.patches.materialize_route_timing_settings",
			(APP / "patches.txt").read_text(encoding="utf-8").split(),
		)
		self.assertTrue((APP / "patches" / "materialize_route_timing_settings.py").exists())

	def test_own_ci_step(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_calendar_route_timing",
			CI.read_text(encoding="utf-8"),
		)

	def test_no_google_reminders(self):
		timed = _task(timing={"date": "2026-10-12", "start": 505, "end": 625, "leave_by": 480, "tz": TZ})
		body = payload.build_event(timed, SITE)
		self.assertEqual(body["reminders"], {"useDefault": False, "overrides": []})
		self.assertNotIn("attendees", body)


if __name__ == "__main__":
	unittest.main()

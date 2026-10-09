# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Daily routes and drive time for the planners: ``project_enhancements.routing``.

Pins three things:

* **The ordering math** a planner trusts without looking: slot anchors keep their order, other
  stops go where they add least, a crossed route gets untangled, arrival times wait for a slot
  rather than hiding the wait, and a stop with no location is never driven to.
* **The cost discipline**: a cached leg never reaches Google, the misses go in blocks of at most
  25 × 25, a failure stops Google for an hour and falls back to the straight-line estimate, and
  a planner load with many person-days makes one matrix call.
* **The key never reaches a log.** Every failure path here is forced with the key planted in the
  exception text and in ``get_traceback``; no ``log_error`` call may carry it.

Bench-free: it installs its own ``frappe`` (and ``requests``) stub, runs the real module, and puts
``sys.modules`` back afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_planner_routing
"""

import datetime
import importlib
import itertools
import math
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

KEY = "AIzaSy-SERVER-KEY-DO-NOT-LOG"
NOW = datetime.datetime(2026, 10, 9, 12, 0, 0)

SHOP = (40.884, -111.882)
HIGHLANDS = (40.6, -111.85)
NEAR_HIGHLANDS = (40.62, -111.86)
OGDEN = (41.1, -112.0)

STUBBED = (
	"frappe",
	"frappe.utils",
	"requests",
	"erpnext_enhancements.project_enhancements",
	"erpnext_enhancements.project_enhancements.routing",
	"erpnext_enhancements.project_enhancements.crew_availability",
	"erpnext_enhancements.workforce",
	"erpnext_enhancements.workforce.sites",
	"erpnext_enhancements.api.pickup_routing",
)
_saved_modules = {}
_modules_before = set()
frappe = None
requests = None
routing = None
sites = None


class _Doc(dict):
	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None


class _Duplicate(Exception):
	pass


class _Unique(Exception):
	pass


class _Cache:
	def __init__(self):
		self.values = {}

	def __call__(self):
		return self

	def get_value(self, key, *a, **k):
		return self.values.get(key)

	def set_value(self, key, value, expires_in_sec=None, **k):
		self.values[key] = value

	def delete_value(self, key, *a, **k):
		self.values.pop(key, None)


class _Response:
	def __init__(self, status, payload):
		self.status_code = status
		self._payload = payload
		self.content = b"" if payload is None else b"{}"

	def json(self):
		if isinstance(self._payload, Exception):
			raise self._payload
		return self._payload


class _Inserted(_Doc):
	def insert(self, ignore_permissions=False):
		error = frappe.insert_error
		if error:
			raise error
		frappe.inserted.append(dict(self))
		return self


class _TravelSettings:
	def get_password(self, field, raise_exception=True):
		return frappe.server_key if field == "google_geocoding_api_key" else None


def setUpModule():
	global frappe, requests, routing, sites
	_modules_before.update(sys.modules)
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: NOW
	utils.nowdate = lambda: str(NOW.date())
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.DuplicateEntryError = _Duplicate
	frappe.UniqueValidationError = _Unique
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	requests = types.ModuleType("requests")
	sys.modules.update({"frappe": frappe, "frappe.utils": utils, "requests": requests})
	_reset()

	routing = importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	sites = importlib.import_module("erpnext_enhancements.workforce.sites")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name in ("frappe", "requests") or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset():
	frappe.server_key = KEY
	frappe.cache = _Cache()
	frappe.local = types.SimpleNamespace(flags=types.SimpleNamespace(commit=False))
	frappe.logged = []
	frappe.log_error = lambda *a, **k: frappe.logged.append((a, k))
	# A traceback with locals would carry the key: anything that logs one fails the leak tests.
	frappe.get_traceback = lambda *a, **k: f"Traceback (most recent call last): key = '{KEY}'"
	frappe.tables = {}
	frappe.queries = []
	frappe.inserted = []
	frappe.insert_error = None
	frappe.set_values = []
	frappe.singles_written = []
	frappe.singles = {}
	frappe.cached = {"Travel Settings": _TravelSettings()}
	frappe.enqueued = []
	frappe.get_all = _get_all
	frappe.get_doc = lambda value, name=None: _Inserted(value)
	frappe.get_cached_doc = _get_cached_doc
	frappe.enqueue = lambda *a, **k: frappe.enqueued.append((a, k))
	frappe.db = types.SimpleNamespace(
		has_column=lambda doctype, column: True,
		table_exists=lambda doctype: True,
		get_value=lambda *a, **k: frappe.values.get(a[:2]),
		get_single_value=lambda doctype, field, *a, **k: frappe.singles.get((doctype, field)),
		set_value=lambda *a, **k: frappe.set_values.append((a, k)),
		set_single_value=lambda *a, **k: frappe.singles_written.append((a, k)),
		sql=lambda *a, **k: [],
	)
	frappe.values = {}
	requests.calls = []
	requests.post = _unexpected
	requests.get = _unexpected


def _unexpected(*args, **kwargs):
	raise AssertionError("Google was called")


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	frappe.queries.append((doctype, filters, fields))
	rows = frappe.tables.get(doctype, [])
	rows = rows(filters) if callable(rows) else rows
	if pluck:
		return [row[pluck] for row in rows]
	return [_Doc(row) for row in rows]


def _get_cached_doc(doctype, name=None):
	key = (doctype, name) if name else doctype
	if key not in frappe.cached:
		raise KeyError(key)
	return frappe.cached[key]


def _euclid(a, b):
	return math.dist(a, b) * 100  # "minutes": any metric will do for the ordering math


def _post(status, payload):
	def post(url, json=None, headers=None, timeout=None):
		requests.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
		if isinstance(payload, Exception):
			raise payload
		return _Response(status, payload(json) if callable(payload) else payload)

	return post


def _all_ok(body):
	"""A computeRouteMatrix answer for every element of the request, 10 min / 8 km each."""
	out = []
	for o in range(len(body["origins"])):
		for d in range(len(body["destinations"])):
			element = {
				"destinationIndex": d,
				"duration": "600s",
				"distanceMeters": 8000,
				"condition": "ROUTE_EXISTS",
				"status": {},
			}
			if o:
				element["originIndex"] = o  # proto3 JSON leaves a 0 index out
			out.append(element)
	return out


def _no_key_logged(test):
	for args, kwargs in frappe.logged:
		test.assertNotIn(KEY, repr(args) + repr(kwargs))
		test.assertEqual(args, ())  # keyword arguments only (v16 guesses at positional ones)
		test.assertTrue(kwargs.get("message"), "a log without a message records a traceback with locals")


# ---------------------------------------------------------------------- pure math


class TestDistances(unittest.TestCase):
	def test_haversine_and_the_estimate(self):
		self.assertAlmostEqual(routing.haversine_km((10.0, 10.0), (11.0, 10.0)), 111.19, places=1)
		self.assertEqual(routing.estimate_minutes(0), 4.0)
		self.assertEqual(routing.estimate_minutes(10), round(10 * 1.3 / 56 * 60 + 4, 1))
		self.assertEqual(routing.estimate_minutes(10), 17.9)
		leg = routing.estimate_leg(SHOP, HIGHLANDS)
		self.assertEqual(leg["source"], "estimate")
		self.assertEqual(leg["minutes"], routing.estimate_minutes(routing.haversine_km(SHOP, HIGHLANDS)))
		self.assertEqual(routing.estimate_leg(SHOP, SHOP), {"minutes": 0.0, "km": 0.0, "source": None})

	def test_pair_key_is_directional_and_five_decimals(self):
		self.assertEqual(routing.pair_key(SHOP, HIGHLANDS), "40.88400,-111.88200>40.60000,-111.85000")
		self.assertNotEqual(routing.pair_key(SHOP, HIGHLANDS), routing.pair_key(HIGHLANDS, SHOP))

	def test_a_point_needs_two_non_zero_finite_numbers(self):
		self.assertTrue(routing.valid_point(SHOP))
		for bad in (None, (0, -111.8), (40.8, 0), (float("nan"), 1.0), (95.0, 10.0), (40.0,), ("x", "y")):
			self.assertFalse(routing.valid_point(bad), bad)
		self.assertEqual(routing.make_point("40.1234567", -111.5), (40.123457, -111.5))
		self.assertIsNone(routing.make_point(0.0, 0.0))  # 0 is "unset", never Null Island

	def test_sources_combine(self):
		self.assertEqual(routing.combine_sources(["google", None, "google"]), "google")
		self.assertEqual(routing.combine_sources(["google", "estimate"]), "mixed")
		self.assertIsNone(routing.combine_sources([None]))

	def test_times_parse_every_way_a_time_field_arrives(self):
		self.assertEqual(routing.parse_minutes("07:30"), 450)
		self.assertEqual(routing.parse_minutes("08:00:00"), 480)
		self.assertEqual(routing.parse_minutes(datetime.timedelta(hours=6, minutes=15)), 375)
		self.assertEqual(routing.parse_minutes(datetime.time(9, 5)), 545)
		self.assertEqual(routing.parse_minutes(None), 480)
		self.assertEqual(routing.parse_minutes("soon"), 480)
		self.assertEqual(routing.fmt_clock(545.4), "09:05")
		self.assertEqual(routing.fmt_clock(25 * 60 + 10), "25:10")


class TestOrdering(unittest.TestCase):
	def test_anchors_keep_their_slot_order_whatever_the_map_says(self):
		# B is the nearer site, but A's slot is first; C sits beside A and D beside B.
		start = (0.0, 0.0)
		stops = [
			{"ref": "B", "point": (0.0, 5.0), "slot": ["13:00", "14:00"]},
			{"ref": "A", "point": (5.0, 0.0), "slot": ["09:00", "10:00"]},
			{"ref": "C", "point": (5.0, 0.2)},
			{"ref": "D", "point": (0.2, 5.0)},
		]
		got = [s["ref"] for s in routing.order_stops(start, stops, start, _euclid)]
		self.assertEqual(got, ["A", "C", "D", "B"])
		# Slots out of geographic order still keep slot order.
		stops[0]["point"] = (1.0, 0.0)
		got = [s["ref"] for s in routing.order_stops(start, stops, start, _euclid)]
		self.assertLess(got.index("A"), got.index("B"))

	def test_cheapest_insertion_and_deterministic_ties(self):
		start = (0.0, 0.0)
		line = [{"ref": r, "point": (x, 0.0)} for r, x in (("Z", 3.0), ("X", 1.0), ("Y", 2.0))]
		got = [s["ref"] for s in routing.order_stops(start, line, (4.0, 0.0), _euclid)]
		self.assertEqual(got, ["X", "Y", "Z"])
		# Two stops at one place: ties go to the lower ref, the same way every time.
		twins = [{"ref": "T2", "point": (1.0, 1.0)}, {"ref": "T1", "point": (1.0, 1.0)}]
		for _ in range(3):
			self.assertEqual(
				[s["ref"] for s in routing.order_stops(start, twins, start, _euclid)], ["T1", "T2"]
			)

	def test_two_opt_untangles_a_crossing(self):
		s, a, b, c = (0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)
		crossed = [s, a, b, c, s]  # s→a and b→c cross in the middle of the square
		fixed_ends = routing.two_opt(crossed, _euclid)
		self.assertEqual(fixed_ends[0], s)
		self.assertEqual(fixed_ends[-1], s)
		cost = lambda path: sum(_euclid(p, q) for p, q in itertools.pairwise(path))  # noqa: E731
		self.assertAlmostEqual(cost(fixed_ends), 400.0)  # the perimeter
		self.assertLess(cost(fixed_ends), cost(crossed))
		# A fixed entry in the middle never moves.
		pinned = routing.two_opt(crossed, _euclid, fixed=[True, False, True, False, True])
		self.assertEqual(pinned[2], b)

	def test_no_shop_starts_at_the_first_stop(self):
		stops = [
			{"ref": "A", "point": (0.0, 1.0)},
			{"ref": "B", "point": (0.0, 3.0)},
			{"ref": "C", "point": (0.0, 2.0)},
		]
		got = [s["ref"] for s in routing.order_stops(None, stops, None, _euclid)]
		self.assertIn(got, (["A", "C", "B"], ["B", "C", "A"]))

	def test_insertion_cost(self):
		route = [SHOP, HIGHLANDS, SHOP]
		near = routing.insertion_cost(route, NEAR_HIGHLANDS, _euclid)
		far = routing.insertion_cost(route, OGDEN, _euclid)
		self.assertLess(near, far)
		self.assertEqual(routing.insertion_cost([], OGDEN, _euclid), 0.0)
		self.assertEqual(
			routing.insertion_cost([SHOP], OGDEN, _euclid), round(_euclid(SHOP, OGDEN) * 2, 1)
		)  # an empty day: there and back
		added, position = routing.best_insertion([(0.0, 0.0), (2.0, 0.0), (0.0, 0.0)], (1.0, 0.0), _euclid)
		self.assertEqual((added, position), (0.0, 1))

	def test_route_times_wait_for_a_slot_and_skip_the_unlocated(self):
		a, b = (0.0, 0.3), (0.0, 0.4)
		minutes = {((0.0, 0.0), a): 30, (a, b): 10, (b, (0.0, 0.0)): 15}
		stops = [
			{"ref": "A", "point": a, "hours": 1},
			{"ref": "X", "point": None, "hours": 0.25},  # no location: not driven to
			{"ref": "B", "point": b, "hours": 1, "slot": ["10:00", "11:00"]},
		]
		got = routing.route_times(
			"08:00:00",
			stops,
			lambda p, q: minutes[(p, q)],
			lambda s: s["hours"],
			start=(0.0, 0.0),
			end=(0.0, 0.0),
		)
		self.assertEqual(
			[(t["arrive"], t["depart"], t["drive_minutes"], t["wait_minutes"]) for t in got["stops"]],
			[("08:30", "09:30", 30, 0.0), ("09:30", "09:45", None, 0.0), ("10:00", "11:00", 10, 5.0)],
		)
		self.assertEqual((got["legs"], got["back_minutes"], got["drive_minutes"]), ([30, 10, 15], 15, 55))
		self.assertEqual(got["finish"], "11:15")
		self.assertEqual(got["stops"][2]["from_point"], a)  # B is driven to from A, past X

	def test_build_route_totals_and_unlocated(self):
		matrix = {
			(SHOP, HIGHLANDS): {"minutes": 40.0, "km": 35.0, "source": "google"},
			(HIGHLANDS, SHOP): {"minutes": 42.0, "km": 35.5, "source": "google"},
		}
		route = routing.build_route(
			SHOP,
			[{"ref": "T-H", "point": HIGHLANDS, "slot": None}, {"ref": "T-X", "point": None, "slot": None}],
			matrix,
		)
		self.assertEqual((route["drive_minutes"], route["km"], route["source"]), (82.0, 70.5, "google"))
		self.assertEqual([s["ref"] for s in route["unlocated"]], ["T-X"])
		self.assertEqual(route["points"], [SHOP, HIGHLANDS, SHOP])


class TestMapsUrl(unittest.TestCase):
	def test_at_most_nine_waypoints_and_no_repeats(self):
		points = [(40.0 + i / 100, -111.0) for i in range(12)]
		url = routing.maps_url(SHOP, SHOP, [points[0], points[0], *points[1:]])
		waypoints = url.split("&waypoints=")[1].split("|")
		self.assertEqual(len(waypoints), routing.MAX_WAYPOINTS)
		self.assertEqual(waypoints[0], "40.00000,-111.00000")
		self.assertEqual(waypoints[1], "40.01000,-111.00000")  # the duplicate collapsed
		self.assertTrue(url.startswith("https://www.google.com/maps/dir/?api=1&origin=40.88400,-111.88200"))

	def test_without_a_shop(self):
		self.assertEqual(
			routing.maps_url(None, None, [HIGHLANDS]),
			"https://www.google.com/maps/dir/?api=1&destination=40.60000,-111.85000&travelmode=driving",
		)
		self.assertIn(
			"&origin=40.60000,-111.85000&destination=41.10000,-112.00000",
			routing.maps_url(None, None, [HIGHLANDS, OGDEN]),
		)
		self.assertIsNone(routing.maps_url(None, None, []))


class TestMatrixParsing(unittest.TestCase):
	def test_elements_and_proto3_defaults(self):
		payload = [
			{
				"destinationIndex": 1,
				"duration": "754s",
				"distanceMeters": 1500,
				"condition": "ROUTE_EXISTS",
				"status": {},
			},
			{"originIndex": 1, "duration": "60s", "condition": "ROUTE_EXISTS"},  # destination 0, no distance
			{"originIndex": 1, "destinationIndex": 1, "condition": "ROUTE_NOT_FOUND"},
			{"destinationIndex": 0, "duration": "5s", "condition": "ROUTE_EXISTS", "status": {"code": 5}},
		]
		got = routing.parse_matrix(payload, [SHOP, OGDEN], [HIGHLANDS, NEAR_HIGHLANDS])
		self.assertEqual(
			got,
			{
				(SHOP, NEAR_HIGHLANDS): {"minutes": 12.6, "km": 1.5},
				(OGDEN, HIGHLANDS): {"minutes": 1.0, "km": 0.0},
			},
		)
		self.assertEqual(routing.parse_matrix({"error": {}}, [SHOP], [OGDEN]), {})

	def test_google_error_status_never_the_message(self):
		body = {"error": {"code": 403, "message": f"key {KEY} is not allowed", "status": "PERMISSION_DENIED"}}
		self.assertEqual(routing.google_error_status(body), "PERMISSION_DENIED")
		self.assertEqual(routing.google_error_status([body]), "PERMISSION_DENIED")
		self.assertEqual(routing.google_error_status(None), "")

	def test_blocks_are_at_most_25_by_25(self):
		origins = [(40.0 + i / 1000, -111.0) for i in range(30)]
		dests = [(41.0 + i / 1000, -112.0) for i in range(27)]
		pairs = {(o, d) for o in origins for d in dests}
		blocks = routing.matrix_blocks(pairs)
		self.assertTrue(all(len(o) <= 25 and len(d) <= 25 for o, d in blocks))
		covered = {(o, d) for os_, ds in blocks for o in os_ for d in ds}
		self.assertTrue(pairs <= covered)
		self.assertEqual(len(blocks), 4)


# ---------------------------------------------------------------------- drive_matrix


class TestDriveMatrix(unittest.TestCase):
	ON = {"use_google_routes": 1.0}

	def setUp(self):
		_reset()

	def test_a_cached_leg_never_reaches_google(self):
		frappe.tables["Planner Drive Time"] = [
			{
				"pair_key": routing.pair_key(SHOP, OGDEN),
				"minutes": 31.5,
				"km": 40.2,
				"fetched_on": NOW - datetime.timedelta(days=3),
			}
		]
		got = routing.drive_matrix([(SHOP, OGDEN), (SHOP, SHOP)], self.ON)
		self.assertEqual(got, {(SHOP, OGDEN): {"minutes": 31.5, "km": 40.2, "source": "google"}})
		self.assertEqual(requests.calls, [])
		query = next(q for q in frappe.queries if q[0] == "Planner Drive Time")
		self.assertEqual(query[1], {"pair_key": ["in", [routing.pair_key(SHOP, OGDEN)]]})

	def test_misses_go_to_google_once_and_are_stored(self):
		requests.post = _post(200, _all_ok)
		got = routing.drive_matrix([(SHOP, OGDEN), (OGDEN, SHOP), (SHOP, HIGHLANDS)], self.ON)
		self.assertEqual(len(requests.calls), 1)
		call = requests.calls[0]
		self.assertEqual(call["url"], routing.ROUTES_URL)
		self.assertEqual(call["headers"]["X-Goog-Api-Key"], KEY)
		self.assertEqual(call["headers"]["X-Goog-FieldMask"], routing.ROUTES_FIELD_MASK)
		self.assertEqual(call["timeout"], 10)
		self.assertEqual(
			(call["json"]["travelMode"], call["json"]["routingPreference"]), ("DRIVE", "TRAFFIC_UNAWARE")
		)
		self.assertEqual(
			call["json"]["origins"][0],
			{"waypoint": {"location": {"latLng": {"latitude": SHOP[0], "longitude": SHOP[1]}}}},
		)
		self.assertEqual(got[(SHOP, OGDEN)], {"minutes": 10.0, "km": 8.0, "source": "google"})
		self.assertTrue(
			{routing.pair_key(SHOP, OGDEN), routing.pair_key(OGDEN, SHOP)}
			<= {r["pair_key"] for r in frappe.inserted}
		)
		row = next(r for r in frappe.inserted if r["pair_key"] == routing.pair_key(SHOP, OGDEN))
		self.assertEqual(
			(row["doctype"], row["source"], row["fetched_on"]), ("Planner Drive Time", "Google", NOW)
		)
		self.assertTrue(frappe.local.flags.commit)  # kept even when the planner loaded by GET
		self.assertEqual(frappe.logged, [])

	def test_a_row_another_request_wrote_first_is_fine(self):
		requests.post = _post(200, _all_ok)
		frappe.insert_error = _Duplicate("Duplicate entry")
		got = routing.drive_matrix([(SHOP, OGDEN)], self.ON)
		self.assertEqual(got[(SHOP, OGDEN)]["source"], "google")
		self.assertEqual(frappe.logged, [])

	def test_google_refusing_sets_the_down_flag_and_falls_back(self):
		requests.post = _post(
			403,
			{"error": {"code": 403, "message": f"API key {KEY} not valid", "status": "PERMISSION_DENIED"}},
		)
		got = routing.drive_matrix([(SHOP, OGDEN)], self.ON)
		self.assertEqual(got[(SHOP, OGDEN)], routing.estimate_leg(SHOP, OGDEN))
		self.assertTrue(frappe.cache.values.get(routing.ROUTES_DOWN_FLAG))
		self.assertEqual(len(frappe.logged), 1)
		self.assertEqual(
			frappe.logged[0][1], {"title": routing.DOWN_TITLE, "message": "HTTP 403: PERMISSION_DENIED"}
		)
		_no_key_logged(self)
		# For the next hour nobody asks Google again.
		routing.drive_matrix([(SHOP, HIGHLANDS)], self.ON)
		self.assertEqual(len(requests.calls), 1)
		self.assertEqual(frappe.inserted, [])

	def test_an_exception_carrying_the_key_logs_only_its_class(self):
		requests.post = _post(None, ConnectionError(f"https://routes.googleapis.com/?key={KEY} refused"))
		got = routing.drive_matrix([(SHOP, OGDEN)], self.ON)
		self.assertEqual(got[(SHOP, OGDEN)]["source"], "estimate")
		self.assertEqual(frappe.logged[0][1]["message"], "HTTP -: ConnectionError")
		_no_key_logged(self)

	def test_a_broken_json_body_is_a_failure_not_a_crash(self):
		def post(url, json=None, headers=None, timeout=None):
			requests.calls.append(json)
			return _Response(500, ValueError(f"not json {KEY}"))

		requests.post = post
		self.assertEqual(routing.drive_matrix([(SHOP, OGDEN)], self.ON)[(SHOP, OGDEN)]["source"], "estimate")
		self.assertEqual(frappe.logged[0][1]["message"], "HTTP 500: error")
		_no_key_logged(self)

	def test_google_off_or_no_key_is_estimates_only(self):
		got = routing.drive_matrix([(SHOP, OGDEN)], {"use_google_routes": 0.0})
		self.assertEqual(got[(SHOP, OGDEN)]["source"], "estimate")
		frappe.server_key = ""
		frappe.singles[("Travel Settings", "google_maps_api_key")] = ""
		self.assertEqual(routing.drive_matrix([(SHOP, OGDEN)], self.ON)[(SHOP, OGDEN)]["source"], "estimate")
		self.assertEqual(requests.calls, [])
		self.assertEqual(frappe.logged, [])

	def test_an_unsaved_setting_means_google_on(self):
		frappe.cached["Project Planner Settings"] = _Doc(use_google_routes=None)
		requests.post = _post(200, _all_ok)
		self.assertEqual(routing.drive_matrix([(SHOP, OGDEN)])[(SHOP, OGDEN)]["source"], "google")

	def test_an_old_row_is_refreshed_and_kept_when_google_cannot(self):
		old = {
			"pair_key": routing.pair_key(SHOP, OGDEN),
			"minutes": 33.0,
			"km": 41.0,
			"fetched_on": NOW - datetime.timedelta(days=120),
		}
		frappe.tables["Planner Drive Time"] = [old]
		requests.post = _post(200, _all_ok)
		self.assertEqual(routing.drive_matrix([(SHOP, OGDEN)], self.ON)[(SHOP, OGDEN)]["minutes"], 10.0)
		((args, kwargs),) = frappe.set_values
		self.assertEqual(
			(args[0], args[1], kwargs),
			("Planner Drive Time", routing.pair_key(SHOP, OGDEN), {"update_modified": False}),
		)
		_reset()
		frappe.tables["Planner Drive Time"] = [old]
		frappe.cache.values[routing.ROUTES_DOWN_FLAG] = 1
		self.assertEqual(
			routing.drive_matrix([(SHOP, OGDEN)], self.ON)[(SHOP, OGDEN)],
			{"minutes": 33.0, "km": 41.0, "source": "google"},
		)

	def test_many_points_are_blocked_25_by_25(self):
		points = [(40.0 + i / 100, -111.5) for i in range(30)]
		requests.post = _post(200, _all_ok)
		pairs = [(a, b) for a in points for b in points if a != b]
		got = routing.drive_matrix(pairs, self.ON)
		self.assertEqual(len(got), len(pairs))
		self.assertTrue(
			all(
				len(c["json"]["origins"]) <= 25 and len(c["json"]["destinations"]) <= 25
				for c in requests.calls
			)
		)
		self.assertEqual(len(requests.calls), 4)

	def test_no_cache_table_yet_is_all_misses(self):
		def missing_table(filters):
			raise RuntimeError("Table 'tabPlanner Drive Time' doesn't exist")

		frappe.tables["Planner Drive Time"] = missing_table
		self.assertEqual(
			routing.drive_matrix([(SHOP, OGDEN)], {"use_google_routes": 0})[(SHOP, OGDEN)]["source"],
			"estimate",
		)


class TestRoutesStatus(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.cached["Project Planner Settings"] = _Doc(
			start_latitude=SHOP[0],
			start_longitude=SHOP[1],
			start_geocoded_from="85 W 300 S, Bountiful, UT 84010",
		)

	def test_working_clears_the_down_flag(self):
		frappe.cache.values[routing.ROUTES_DOWN_FLAG] = 1
		requests.post = _post(200, _all_ok)
		got = routing.routes_status()
		self.assertTrue(got["google"])
		self.assertIn("10 min", got["detail"])
		self.assertNotIn(routing.ROUTES_DOWN_FLAG, frappe.cache.values)
		body = requests.calls[0]["json"]
		self.assertEqual((len(body["origins"]), len(body["destinations"])), (1, 1))

	def test_refused_says_how_without_the_key(self):
		requests.post = _post(403, {"error": {"status": "PERMISSION_DENIED", "message": KEY}})
		got = routing.routes_status()
		self.assertFalse(got["google"])
		self.assertIn("HTTP 403: PERMISSION_DENIED", got["detail"])
		self.assertNotIn(KEY, got["detail"])

	def test_no_key(self):
		frappe.server_key = ""
		self.assertFalse(routing.routes_status()["google"])
		self.assertEqual(requests.calls, [])


# ---------------------------------------------------------------------- where things are


class TestStartPoint(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_cached_shop_while_the_address_is_unchanged(self):
		frappe.cached["Project Planner Settings"] = _Doc(
			start_latitude=SHOP[0],
			start_longitude=SHOP[1],
			start_geocoded_from="85 W 300 S, Bountiful, UT 84010",
		)
		self.assertEqual(routing.start_point(), SHOP)
		self.assertEqual(requests.calls, [])

	def test_a_new_shop_address_is_geocoded_and_cached(self):
		frappe.singles[("ERPNext Enhancements Settings", "pickup_route_start_address")] = (
			"1 New Shop Way, Bountiful, UT"
		)
		frappe.cached["Project Planner Settings"] = _Doc(
			start_latitude=SHOP[0],
			start_longitude=SHOP[1],
			start_geocoded_from="85 W 300 S, Bountiful, UT 84010",
		)

		def get(url, params=None, timeout=None):
			requests.calls.append({"url": url, "params": params})
			return _Response(
				200, {"status": "OK", "results": [{"geometry": {"location": {"lat": 40.9, "lng": -111.9}}}]}
			)

		requests.get = get
		# v1.578.1: the Single is made whole BEFORE the cache rows are written, or the first cache
		# write ends new_doc() defaults and every Check (padding, Google) loads as 0.
		order = []
		settings_module = types.ModuleType("_pp_settings_stub")
		settings_module.materialize_defaults = lambda: order.append(("materialize", len(frappe.singles_written)))
		controller = "erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings"
		with mock.patch.dict(sys.modules, {controller: settings_module}):
			self.assertEqual(routing.start_point(), (40.9, -111.9))
		self.assertEqual(order, [("materialize", 0)])
		self.assertEqual(
			requests.calls[0]["params"], {"address": "1 New Shop Way, Bountiful, UT", "key": KEY}
		)
		((args, kwargs),) = frappe.singles_written
		self.assertEqual(
			args,
			(
				"Project Planner Settings",
				{
					"start_latitude": 40.9,
					"start_longitude": -111.9,
					"start_geocoded_from": "1 New Shop Way, Bountiful, UT",
				},
			),
		)
		self.assertEqual(kwargs, {"update_modified": False})
		self.assertTrue(frappe.local.flags.commit)

	def test_an_unsaved_single_uses_the_default_shop(self):
		def get(url, params=None, timeout=None):
			requests.calls.append(params)
			return _Response(
				200,
				{"status": "OK", "results": [{"geometry": {"location": {"lat": SHOP[0], "lng": SHOP[1]}}}]},
			)

		requests.get = get
		self.assertEqual(routing.start_point(), SHOP)
		self.assertEqual(requests.calls[0]["address"], "85 W 300 S, Bountiful, UT 84010")

	def test_a_failed_geocode_is_none_logged_without_the_key_and_not_retried_for_an_hour(self):
		def get(url, params=None, timeout=None):
			requests.calls.append(params)
			raise TimeoutError(f"GET {url}?key={params['key']} timed out")

		requests.get = get
		self.assertIsNone(routing.start_point())
		self.assertIsNone(routing.start_point())
		self.assertEqual(len(requests.calls), 1)
		self.assertIn("TimeoutError", frappe.logged[0][1]["message"])
		_no_key_logged(self)


class TestLocate(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.tables["Task"] = [
			{
				"name": "T-ADDR",
				"project": "PRJ-1",
				"custom_locationaddress_of_task": "ADDR-1",
				"custom_rental_booking": None,
			},
			{
				"name": "T-BLANK",
				"project": "PRJ-1",
				"custom_locationaddress_of_task": "ADDR-0",
				"custom_rental_booking": None,
			},
			{
				"name": "T-RENT",
				"project": None,
				"custom_locationaddress_of_task": None,
				"custom_rental_booking": "RB-1",
			},
			{
				"name": "T-NOWHERE",
				"project": "PRJ-9",
				"custom_locationaddress_of_task": None,
				"custom_rental_booking": None,
			},
		]
		frappe.tables["Rental Booking"] = [{"name": "RB-1", "venue_address": "ADDR-V", "project": "PRJ-2"}]
		frappe.tables["Address"] = [
			{
				"name": "ADDR-1",
				"custom_latitude": OGDEN[0],
				"custom_longitude": OGDEN[1],
				"address_line1": "1 Main",
				"city": "Ogden",
			},
			{"name": "ADDR-0", "custom_latitude": 0, "custom_longitude": 0, "address_line1": "No pin"},
			{
				"name": "ADDR-V",
				"custom_latitude": HIGHLANDS[0],
				"custom_longitude": HIGHLANDS[1],
				"address_line1": "Venue",
			},
		]
		frappe.tables["Project"] = [{"name": "PRJ-1", "custom_project_address": "Highlands Blvd"}]

	def _bulk(self, names):
		self.bulk_calls.append(list(names))
		return {"PRJ-1": {"lat": NEAR_HIGHLANDS[0], "lng": NEAR_HIGHLANDS[1], "source": "Address"}}

	def test_task_address_then_venue_then_project_in_bulk(self):
		self.bulk_calls = []
		bookings = [
			{"kind": "task", "ref": "T-ADDR", "project": "PRJ-1"},
			{"kind": "task", "ref": "T-BLANK", "project": "PRJ-1"},  # its Address has no pin
			{"kind": "rental", "ref": "T-RENT", "project": None},
			{"kind": "task", "ref": "T-NOWHERE", "project": "PRJ-9"},
			{"kind": "visit", "ref": "SMR-1", "project": "PRJ-1"},
			{"kind": "travel", "ref": "TRIP-1", "project": None},  # not a stop
		]
		with mock.patch.object(sites, "site_coordinates_bulk", self._bulk):
			got = routing.locate(bookings)
		self.assertEqual(
			got,
			{
				"T-ADDR": OGDEN,
				"T-BLANK": NEAR_HIGHLANDS,
				"T-RENT": HIGHLANDS,
				"T-NOWHERE": None,
				"SMR-1": NEAR_HIGHLANDS,
			},
		)
		self.assertEqual(len(self.bulk_calls), 1)
		self.assertEqual([q[0] for q in frappe.queries], ["Task", "Rental Booking", "Address"])

	def test_detail_carries_readable_addresses(self):
		self.bulk_calls = []
		with mock.patch.object(sites, "site_coordinates_bulk", self._bulk):
			got = routing.locate(
				[
					{"kind": "task", "ref": "T-ADDR", "project": "PRJ-1"},
					{"kind": "visit", "ref": "SMR-1", "project": "PRJ-1"},
				],
				detail=True,
			)
		self.assertEqual(got["T-ADDR"], {"point": OGDEN, "address": "1 Main, Ogden"})
		self.assertEqual(got["SMR-1"], {"point": NEAR_HIGHLANDS, "address": "Highlands Blvd"})


class TestGeocodeAddress(unittest.TestCase):
	def setUp(self):
		_reset()

		def get(url, params=None, timeout=None):
			requests.calls.append(params)
			return _Response(
				200, {"status": "OK", "results": [{"geometry": {"location": {"lat": 40.5, "lng": -111.9}}}]}
			)

		requests.get = get

	def test_a_google_point_is_written_without_touching_modified(self):
		frappe.values[("Address", "ADDR-1")] = _Doc(
			name="ADDR-1", address_line1="1 Main", city="Draper", custom_latitude=0, custom_longitude=0
		)
		self.assertEqual(routing.geocode_address("ADDR-1"), (40.5, -111.9))
		((args, kwargs),) = frappe.set_values
		self.assertEqual(
			args,
			(
				"Address",
				"ADDR-1",
				{"custom_latitude": 40.5, "custom_longitude": -111.9, "custom_location_source": "Google"},
			),
		)
		self.assertEqual(kwargs, {"update_modified": False})

	def test_never_over_a_manual_address(self):
		frappe.values[("Address", "ADDR-1")] = _Doc(
			name="ADDR-1",
			address_line1="1 Main",
			custom_latitude=0,
			custom_longitude=0,
			custom_location_source="Manual",
		)
		self.assertIsNone(routing.geocode_address("ADDR-1"))
		self.assertEqual((requests.calls, frappe.set_values), ([], []))

	def test_an_address_with_a_point_is_left_alone(self):
		frappe.values[("Address", "ADDR-1")] = _Doc(
			name="ADDR-1", custom_latitude=40.1, custom_longitude=-111.1
		)
		self.assertEqual(routing.geocode_address("ADDR-1"), (40.1, -111.1))
		self.assertEqual(requests.calls, [])


class TestBackfillAndEnqueue(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.cached["Project Planner Settings"] = _Doc(
			start_latitude=SHOP[0],
			start_longitude=SHOP[1],
			start_geocoded_from="85 W 300 S, Bountiful, UT 84010",
		)
		self._crew = sys.modules.get("erpnext_enhancements.project_enhancements.crew_availability")
		engine = types.ModuleType("erpnext_enhancements.project_enhancements.crew_availability")
		engine.FINISHED_STATUSES = ("Completed", "Canceled")
		sys.modules[engine.__name__] = engine

	def tearDown(self):
		name = "erpnext_enhancements.project_enhancements.crew_availability"
		if self._crew is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = self._crew

	def test_it_never_raises_and_logs_with_a_message(self):
		def broken(*a, **k):
			raise RuntimeError("database went away")

		frappe.db.sql = broken
		self.assertEqual(routing.backfill_coordinates(), 0)
		self.assertEqual(frappe.logged[0][1]["title"], "Project Planner: coordinate backfill failed")

	def test_it_is_bounded(self):
		calls = []
		frappe.db.sql = (
			lambda query, values=None, **k: [(f"ADDR-{i}",) for i in range(10)]
			if "tabAddress" in query
			else [("PRJ-1",), ("PRJ-2",)]
		)
		with (
			mock.patch.object(routing, "geocode_address", lambda name: calls.append(name)),
			mock.patch.object(sites, "site_coordinates_bulk", lambda names: {"PRJ-1": {"lat": 1, "lng": 1}}),
			mock.patch.object(sites, "geocode_project", lambda project: calls.append(project)),
		):
			self.assertEqual(routing.backfill_coordinates(limit=5), 5)
			self.assertEqual(len(calls), 5)
			calls.clear()
			self.assertEqual(
				routing.backfill_coordinates(limit=30), 11
			)  # ten addresses (deduplicated) + PRJ-2
		self.assertEqual(calls[-1], "PRJ-2")

	def test_enqueue_is_deduplicated_and_throttled(self):
		self.assertFalse(routing.enqueue_missing([]))
		self.assertTrue(routing.enqueue_missing(["T-1"]))
		self.assertFalse(routing.enqueue_missing(["T-2"]))  # within the hour
		((args, kwargs),) = frappe.enqueued
		self.assertEqual(args, (routing.BACKFILL_JOB,))
		self.assertEqual(kwargs, {"queue": "long", "job_id": routing.BACKFILL_JOB_ID, "deduplicate": True})


class TestPlanRoutes(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_every_day_is_priced_in_one_matrix_call(self):
		calls = []

		def matrix(pairs, settings=None):
			calls.append(set(pairs))
			return {pair: routing.estimate_leg(*pair) for pair in pairs}

		points = {"T-1": HIGHLANDS, "T-2": OGDEN, "SMR-1": NEAR_HIGHLANDS}
		day_bookings = {
			("RES-1", "MON"): [
				{"kind": "task", "ref": "T-1", "hours": 4},
				{"kind": "visit", "ref": "SMR-1", "hours": 2},
			],
			("RES-1", "TUE"): [{"kind": "task", "ref": "T-2", "hours": 8}],
			("RES-2", "MON"): [
				{"kind": "task", "ref": "T-1", "hours": 4},
				{"kind": "travel", "ref": "TRIP-1", "hours": 8},
			],
			("RES-2", "TUE"): [{"kind": "drive", "ref": None, "hours": 1}],
		}
		with (
			mock.patch.object(
				routing,
				"locate",
				lambda bookings, detail=False: {b["ref"]: points.get(b["ref"]) for b in bookings},
			),
			mock.patch.object(routing, "start_point", lambda: SHOP),
			mock.patch.object(routing, "drive_matrix", matrix),
		):
			routes = routing.plan_routes(day_bookings, {})
		self.assertEqual(
			set(routes), {("RES-1", "MON"), ("RES-1", "TUE")}
		)  # travel and nothing-to-route skipped
		self.assertEqual(len(calls), 1)
		# Only legs within a day: Monday's shop/Highlands/near-Highlands (6) and Tuesday's shop/Ogden (2).
		self.assertEqual(len(calls[0]), 8)
		self.assertEqual(routes[("RES-1", "MON")]["points"][0], SHOP)
		self.assertEqual(routes[("RES-1", "MON")]["source"], "estimate")

	def test_nothing_located_never_asks_for_the_shop_or_a_matrix(self):
		with (
			mock.patch.object(
				routing, "locate", lambda bookings, detail=False: {b["ref"]: None for b in bookings}
			),
			mock.patch.object(routing, "start_point", _unexpected),
			mock.patch.object(routing, "drive_matrix", _unexpected),
			mock.patch.object(routing, "enqueue_missing", lambda refs: None),
		):
			routes = routing.plan_routes({("RES-1", "MON"): [{"kind": "task", "ref": "T-1", "hours": 2}]}, {})
		route = routes[("RES-1", "MON")]
		self.assertEqual((route["drive_minutes"], route["source"], len(route["unlocated"])), (0.0, None, 1))


class TestKeyHandling(unittest.TestCase):
	SOURCE = Path(__file__).resolve().parents[1] / "project_enhancements/routing.py"

	def test_the_key_is_read_only_inside_the_two_http_functions(self):
		import ast

		tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
		readers = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef):
				for inner in ast.walk(node):
					if isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "_server_key":
						readers.add(node.name)
		# Geocoding reads no key here at all: it goes through workforce.sites.geocode_text, the
		# app's one server-side geocoder (tests/test_geocoding_key.py enforces that).
		self.assertEqual(readers, {"_google_post", "_has_key"})

	def test_geocoding_goes_through_the_shared_geocoder(self):
		text = self.SOURCE.read_text(encoding="utf-8")
		self.assertIn("from erpnext_enhancements.workforce.sites import geocode_text", text)
		self.assertNotIn("maps.googleapis.com", text)

	def test_no_log_error_without_a_message_and_no_traceback_near_google(self):
		import ast

		tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "log_error":
				self.assertEqual(node.args, [])
				self.assertIn("message", {k.arg for k in node.keywords})
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef) and node.name in (
				"_google_post",
				"_google_geocode",
				"_has_key",
			):
				names = {getattr(n.func, "attr", None) for n in ast.walk(node) if isinstance(n, ast.Call)}
				self.assertFalse(names & {"log_error", "get_traceback"}, node.name)


if __name__ == "__main__":
	unittest.main()

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Daily routes and drive time for the planners (Project Planner, Phase 2).

A person's day is not only the hours on site: they leave the shop, drive between the sites in
some order, and drive back. Phase 1 counted the hours on site only, so a day with four short jobs
across the valley read as half empty. This module works out, for one person on one day, **which
order to visit the stops in and how long the driving takes**, and
``crew_availability._compute`` books that driving against the person's hours ("Driving",
``kind: "drive"``) when Project Planner Settings ``pad_drive_time`` is on.

The pieces:

* **Pure ordering math** (:func:`order_stops`, :func:`two_opt`, :func:`route_times`,
  :func:`insertion_cost`, :func:`haversine_km`, :func:`estimate_minutes`), which take plain points
  and a ``minutes_between(a, b)`` callable, so ``tests/test_planner_routing.py`` runs them without
  a bench. A point is a ``(lat, lng)`` tuple.
* **Where things are** (:func:`locate`): a task's own Address, else a rental task's venue, else
  the project's site (``workforce.sites.site_coordinates_bulk``). Coverage is poor on production
  (2026-10-09: none of the five Active projects with current tasks had coordinates), so every
  caller copes with a stop that has no location: it is listed, counted as ``unlocated``, its legs
  are not driven, and :func:`enqueue_missing` asks the daily backfill to go and find it.
* **How long a leg takes** (:func:`drive_matrix`): the ``Planner Drive Time`` cache first, then
  Google **Routes computeRouteMatrix** for the misses only, then a straight-line estimate. One
  planner load collects every pair it needs and makes one call here, so a week of twenty people is
  a handful of Google requests at most, never one per person-day. Pairs rarely change, so the
  cache is the normal answer and a row is refreshed lazily after ``CACHE_DAYS``.
* **Where the shop is** (:func:`start_point`): every route starts and ends at the shop, the same
  address pick-up runs start from (``api.pickup_routing._depot_address()``: one source of truth).
  No Address record carries its coordinates, so it is geocoded once and cached on Project Planner
  Settings, and re-geocoded only when the shop address text changes.

Things this module is careful about, some of which look like bugs:

* **The Google key never reaches a log or a traceback.** It is read inside :func:`_google_post` /
  :func:`_google_geocode`, which make the HTTP call and catch *every* exception themselves,
  returning only a status code and Google's error status string (or an exception class name). The
  callers log after those functions have returned, so no frame holding the key is live, and
  every ``log_error`` here passes a ``message``: called without one, ``log_error`` records
  ``get_traceback(with_context=True)``, which publishes frame locals. This app has leaked a secret
  exactly that way before.
* **Google failing is an ordinary state.** Nik enabled Routes API on the server key on 2026-10-09
  ("I think"), and the key may still be the referrer-restricted browser key on some sites. Any
  HTTP error or exception sets ``ee_planner_routes_down`` in the cache for an hour, logs once, and
  every leg falls back to :func:`estimate_minutes`; each answer says which source it used.
  ``api.project_planner.check_routes`` (:func:`routes_status`) probes it on demand.
* **Writes during a GET are kept.** Frappe rolls back a GET request's transaction; a cache row
  or the shop's coordinates written while a planner loads would vanish and be fetched (and paid
  for) again on every load. :func:`_keep_writes` sets ``frappe.local.flags.commit`` after such a
  write, which is the framework's own switch for exactly this.
* **0.0 is "no coordinates", not Null Island** (Float fields are ``NOT NULL DEFAULT 0``), as in
  ``pickup_routing._address_coords``.
* **``frappe.get_all`` gets no SQL function strings** (Frappe 16 refuses them) and nullable dates
  are filtered in raw SQL with COALESCE or in Python.
"""

import datetime
import itertools
import math

import frappe

#: Straight-line estimate, used when Google is off, down or has no answer for a pair. Roads are
#: not straight: 1.3 × the great-circle distance is the usual detour factor for a street grid,
#: 56 km/h (about 35 mph) is an average speed across the Salt Lake valley mixing freeway and
#: surface streets, and 4 minutes covers parking and walking to the site.
ROAD_FACTOR = 1.3
AVERAGE_KMH = 56.0
PARK_MINUTES = 4.0

ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
ROUTES_FIELD_MASK = "originIndex,destinationIndex,duration,distanceMeters,status,condition"
TIMEOUT_S = 10

#: computeRouteMatrix takes at most 625 elements per request; 25 × 25 stays inside it.
MATRIX_BLOCK = 25
#: A cached leg older than this is fetched again (lazily, on the next load that needs it).
CACHE_DAYS = 90

#: Cache flags. ``ROUTES_DOWN`` stops every load hammering a refused key for an hour.
ROUTES_DOWN_FLAG = "ee_planner_routes_down"
SHOP_FAILED_FLAG = "ee_planner_shop_geocode_failed"
BACKFILL_QUEUED_FLAG = "ee_planner_backfill_queued"
DOWN_SECONDS = 3600

SETTINGS = "Project Planner Settings"
DRIVE_TIME = "Planner Drive Time"
DOWN_TITLE = "Project Planner: Google Routes unavailable"

#: Booking kinds that are a stop on a route. Travel is not: the person is away that day.
STOP_KINDS = ("task", "rental", "visit")

#: Google Maps links take at most nine waypoints between the origin and the destination.
MAX_WAYPOINTS = 9

#: A stop within this distance of a task's site counts as "already near" for a suggestion.
NEARBY_KM = 10.0

#: Where the daily backfill stops, so one run cannot fire hundreds of Google calls.
BACKFILL_LIMIT = 40
BACKFILL_JOB = "erpnext_enhancements.project_enhancements.routing.backfill_coordinates"
BACKFILL_JOB_ID = "ee_planner_backfill_coordinates"

#: Statuses of a Rental Booking whose venue the crew will drive to. Returned and Closed are
#: firm too (``rental_rules.FIRM_STATUSES``) but behind us: geocoding them buys nothing.
UPCOMING_RENTAL_STATUSES = ("Confirmed", "Out")

DEFAULT_DAY_START = "08:00:00"


# ---------------------------------------------------------------------- pure helpers


def _number(value):
	try:
		number = float(value)
	except (TypeError, ValueError):
		return 0.0
	return number if math.isfinite(number) else 0.0


def make_point(lat, lng):
	"""``(lat, lng)`` rounded to 6 places, or None when either is unset (0) or out of range."""
	lat, lng = _number(lat), _number(lng)
	if not lat or not lng or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
		return None
	return (round(lat, 6), round(lng, 6))


def valid_point(point):
	"""A point is valid when it has two non-zero, finite, in-range numbers."""
	try:
		return point is not None and make_point(point[0], point[1]) is not None
	except (TypeError, IndexError):
		return False


def _point_text(point):
	return f"{point[0]:.5f},{point[1]:.5f}"


def same_point(a, b):
	"""Two points closer than the cache key can tell apart (5 decimals, about a meter)."""
	return a is not None and b is not None and _point_text(a) == _point_text(b)


def pair_key(a, b):
	"""The ``Planner Drive Time`` name of the leg from ``a`` to ``b``. Direction matters: one-way
	streets and freeway ramps make A→B and B→A different drives.

	Joined with ``~``. It was ``>`` until v1.578.1, and Frappe refuses ``<`` and ``>`` in any
	document name (``naming.validate_name``), so every cache write failed and the refusal reached
	the planner as an error dialog on load. Nothing was ever stored under the old form.
	"""
	return f"{_point_text(a)}~{_point_text(b)}"


def haversine_km(a, b):
	"""Great-circle distance in km between two ``(lat, lng)`` points."""
	lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
	h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
	return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(h)))


def estimate_minutes(km):
	"""Driving minutes for a straight-line distance: road factor, average speed, parking.

	``km × 1.3 / 56 × 60 + 4``, to 0.1 minute. See :data:`ROAD_FACTOR` for why those numbers.
	"""
	return round(_number(km) * ROAD_FACTOR / AVERAGE_KMH * 60 + PARK_MINUTES, 1)


def estimate_leg(a, b):
	"""A leg by straight-line estimate: ``{"minutes", "km", "source": "estimate"}``."""
	if same_point(a, b):
		return {"minutes": 0.0, "km": 0.0, "source": None}
	km = haversine_km(a, b)
	return {"minutes": estimate_minutes(km), "km": round(km * ROAD_FACTOR, 2), "source": "estimate"}


def leg(matrix, a, b):
	"""The leg from ``a`` to ``b`` out of a :func:`drive_matrix` answer.

	Zero for the same point (two jobs at one site), None when either end is unknown, and the
	straight-line estimate when the matrix has no entry (it always should).
	"""
	if a is None or b is None:
		return None
	if same_point(a, b):
		return {"minutes": 0.0, "km": 0.0, "source": None}
	return (matrix or {}).get((a, b)) or estimate_leg(a, b)


def combine_sources(sources):
	"""``"google"``, ``"estimate"``, ``"mixed"`` or None for the sources of a day's legs."""
	found = {source for source in sources if source}
	if not found:
		return None
	return found.pop() if len(found) == 1 else "mixed"


def parse_minutes(value, default=DEFAULT_DAY_START):
	"""Minutes after midnight for a Time value: ``"08:00"``, ``"08:00:00"``, a ``timedelta`` (how
	a Time column comes back from the database) or a ``time``. Unparseable → ``default``."""
	if value is None or value == "":
		value = default
	if isinstance(value, datetime.timedelta):
		return value.total_seconds() / 60
	if isinstance(value, datetime.datetime):
		value = value.time()
	if isinstance(value, datetime.time):
		return value.hour * 60 + value.minute + value.second / 60
	for text in (value, default, DEFAULT_DAY_START):
		try:
			parts = [float(p) for p in str(text).strip().split(":")]
			return (
				parts[0] * 60 + (parts[1] if len(parts) > 1 else 0) + (parts[2] / 60 if len(parts) > 2 else 0)
			)
		except (TypeError, ValueError, IndexError):
			continue
	return 480.0


def fmt_clock(minutes):
	"""``"HH:MM"`` for minutes after midnight (rounded to the minute; past midnight keeps counting,
	``"25:10"``, rather than wrapping to a time that reads as the morning)."""
	total = int(round(_number(minutes)))
	return f"{total // 60:02d}:{total % 60:02d}"


def _cost_function(minutes_between):
	def cost(a, b):
		if a is None or b is None or same_point(a, b):
			return 0.0
		return _number(minutes_between(a, b))

	return cost


def _path_cost(points, cost):
	return sum(cost(a, b) for a, b in itertools.pairwise(points))


def two_opt(sequence, minutes_between, fixed=None, key=None):
	"""Untangle a route by reversing runs of movable stops while that shortens it.

	``sequence`` is a list of entries; ``key(entry)`` gives an entry's point (default: the entry
	is the point). ``fixed`` is a parallel list of booleans; fixed entries (the shop at each end,
	time-slot anchors) never move, so only the runs between them are reversed. With no ``fixed``,
	the first and last entries are fixed. The whole run's cost is compared, not just the two end
	legs, because Google's matrix is directional: a reversed run drives every leg the other way.
	Deterministic: the first improving reversal (lowest start, then end) is taken each pass.
	"""
	key = key or (lambda entry: entry)
	cost = _cost_function(minutes_between)
	seq = list(sequence)
	fixed = list(fixed) if fixed is not None else [i in (0, len(seq) - 1) for i in range(len(seq))]

	runs, i = [], 0
	while i < len(seq):
		if fixed[i]:
			i += 1
			continue
		j = i
		while j + 1 < len(seq) and not fixed[j + 1]:
			j += 1
		runs.append((i, j))
		i = j + 1

	for low, high in runs:
		left = low - 1 if low > 0 else None
		right = high + 1 if high + 1 < len(seq) else None
		for _ in range(50):  # each pass strictly shortens the run; this only guards the loop
			improved = False

			def window(entries):
				points = [key(e) for e in entries]
				if left is not None:
					points.insert(0, key(seq[left]))
				if right is not None:
					points.append(key(seq[right]))
				return points

			current = seq[low : high + 1]
			best = _path_cost(window(current), cost)
			for a in range(len(current)):
				for b in range(a + 1, len(current)):
					trial = current[:a] + current[a : b + 1][::-1] + current[b + 1 :]
					trial_cost = _path_cost(window(trial), cost)
					if trial_cost < best - 1e-9:
						seq[low : high + 1] = trial
						improved = True
						break
				if improved:
					break
			if not improved:
				break
	return seq


def order_stops(start, stops, end, minutes_between):
	"""The order to visit ``stops`` in, leaving from ``start`` and finishing at ``end``.

	``stops`` are dicts with ``point`` and optionally ``slot`` (``["HH:MM", "HH:MM"]``) and
	``ref``. A stop with a time slot is an **anchor**: anchors keep their slot order, because the
	customer is expecting the crew then. Every other stop is placed by **cheapest insertion** into
	start → anchors → end (repeatedly the stop and position that add the fewest minutes), then the
	runs between anchors are untangled with :func:`two_opt`. A day is at most a dozen stops, so the
	O(n³) insertion is nothing. Ties go to the lower ``ref`` and the later position, so the same
	day always comes out the same way.

	``start``/``end`` may be None (the shop could not be located): the route then begins at its
	first stop and ends at its last.
	"""
	cost = _cost_function(minutes_between)
	ranked = sorted(enumerate(stops or []), key=lambda e: (str(e[1].get("ref") or ""), e[0]))
	anchors = sorted(
		(s for _, s in ranked if s.get("slot")),
		key=lambda s: (str(s["slot"][0]), str(s.get("ref") or "")),
	)
	remaining = [s for _, s in ranked if not s.get("slot")]

	seq = [(start, None)] + [(s.get("point"), s) for s in anchors] + [(end, None)]
	fixed = [True] * len(seq)
	while remaining:
		best = None
		for index, stop in enumerate(remaining):
			point = stop.get("point")
			for position in range(1, len(seq)):
				before, after = seq[position - 1][0], seq[position][0]
				added = cost(before, point) + cost(point, after) - cost(before, after)
				# On a tie the later position wins: a second job at the same site then lands
				# after the first, so same-place stops keep their ref order.
				candidate = (round(added, 6), index, -position)
				if best is None or candidate < best:
					best = candidate
		_, index, position = best
		position = -position
		stop = remaining.pop(index)
		seq.insert(position, (stop.get("point"), stop))
		fixed.insert(position, False)

	seq = two_opt(seq, minutes_between, fixed=fixed, key=lambda entry: entry[0])
	return [stop for _, stop in seq if stop is not None]


def best_insertion(route_points, new_point, minutes_between):
	"""``(added_minutes, position)`` for the cheapest place to put ``new_point`` in a route.

	``route_points`` is the route as driven, normally ``[shop, stop, ..., shop]``. One point is a
	round trip from it; no points costs nothing.
	"""
	cost = _cost_function(minutes_between)
	points = [p for p in (route_points or []) if p is not None]
	if not points:
		return 0.0, 0
	if len(points) == 1:
		return round(cost(points[0], new_point) + cost(new_point, points[0]), 1), 1
	best = None
	for position in range(1, len(points)):
		before, after = points[position - 1], points[position]
		added = cost(before, new_point) + cost(new_point, after) - cost(before, after)
		if best is None or added < best[0] - 1e-9:
			best = (added, position)
	return round(max(best[0], 0.0), 1), best[1]


def insertion_cost(route_points, new_point, minutes_between):
	"""The fewest minutes ``new_point`` adds to a route, over every place it could go."""
	return best_insertion(route_points, new_point, minutes_between)[0]


def route_times(day_start, ordered, minutes_between, hours_of, start=None, end=None):
	"""Arrival and departure for each stop of an ordered day.

	``ordered`` are stops (dicts with ``point``, which may be None, and optional ``slot``);
	``hours_of(stop)`` is the time on site. The day leaves ``start`` (the shop) at ``day_start``.
	A stop with a slot is arrived at ``max(drive arrival, slot start)``: the wait is reported in
	``wait_minutes``, not hidden. A stop with no point is not driven to: it takes its hours at
	the point of the stop before it, and the next leg leaves from there.

	Returns ``{"stops": [{"arrive", "depart", "drive_minutes", "wait_minutes", "from_point"}],
	"legs": [minutes, ...], "back_minutes", "drive_minutes", "finish"}`` where ``legs`` are the
	driven legs in order (the drive back to ``end`` last), ``finish`` the arrival at ``end``.
	"""
	cost = _cost_function(minutes_between)
	now = parse_minutes(day_start)
	last = start
	out, legs = [], []
	for stop in ordered or []:
		point = stop.get("point")
		drive = None
		origin = None
		if point is not None:
			if last is not None:
				drive = round(cost(last, point), 1)
				origin = last
				legs.append(drive)
			last = point
		arrive = now + (drive or 0)
		wait = 0.0
		if stop.get("slot"):
			slot_start = parse_minutes(stop["slot"][0], default=fmt_clock(arrive))
			if slot_start > arrive:
				wait = slot_start - arrive
				arrive = slot_start
		depart = arrive + max(_number(hours_of(stop)), 0.0) * 60
		out.append(
			{
				"arrive": fmt_clock(arrive),
				"depart": fmt_clock(depart),
				"drive_minutes": drive,
				"wait_minutes": round(wait, 1),
				"from_point": origin,
			}
		)
		now = depart
	back = round(cost(last, end), 1) if (last is not None and end is not None and out) else 0.0
	if back:
		legs.append(back)
	return {
		"stops": out,
		"legs": legs,
		"back_minutes": back,
		"drive_minutes": round(sum(legs), 1),
		"finish": fmt_clock(now + back),
	}


def maps_url(origin, destination, waypoints):
	"""A Google Maps directions link (``api=1``) through the points, at most nine waypoints.

	Consecutive duplicates are dropped (two jobs at one site are one stop to a driver). Past nine
	waypoints the rest are left out: that is Google's limit for the link, and the route view
	still lists every stop.
	"""
	points = [p for p in (waypoints or []) if p is not None]
	if destination is None and points:
		destination = points.pop()
	if origin is None and points:
		origin = points.pop(0)
	if destination is None:
		return None
	unique = []
	for point in points:
		if not unique or not same_point(unique[-1], point):
			unique.append(point)
	unique = unique[:MAX_WAYPOINTS]
	# No origin (the shop could not be located and there is one stop): Google starts from
	# wherever the phone is, which is what a driver opening the link wants anyway.
	url = "https://www.google.com/maps/dir/?api=1"
	if origin is not None:
		url += f"&origin={_point_text(origin)}"
	url += f"&destination={_point_text(destination)}&travelmode=driving"
	if unique:
		url += "&waypoints=" + "|".join(_point_text(p) for p in unique)
	return url


def build_route(shop, stops, matrix, minutes_between=None):
	"""One person-day's route: ordered stops, driven legs and totals.

	``stops`` are dicts with ``point`` (None when unlocated), ``slot``, ``ref``; ``matrix`` is a
	:func:`drive_matrix` answer covering the day's points. Unlocated stops are kept aside in
	``unlocated``; they are listed by the route view but never driven to.
	"""
	located = [s for s in stops if s.get("point") is not None]
	unlocated = [s for s in stops if s.get("point") is None]

	def between(a, b):
		found = leg(matrix, a, b)
		return found["minutes"] if found else 0.0

	ordered = order_stops(shop, located, shop, minutes_between or between) if located else []
	points = [s["point"] for s in ordered]
	if shop is not None and points:
		points = [shop, *points, shop]
	legs = []
	for a, b in itertools.pairwise(points):
		found = leg(matrix, a, b) or {"minutes": 0.0, "km": 0.0, "source": None}
		legs.append({"from": a, "to": b, **found})
	return {
		"shop": shop,
		"stops": ordered,
		"unlocated": unlocated,
		"points": points,
		"legs": legs,
		"drive_minutes": round(sum(_number(l["minutes"]) for l in legs), 1),
		"km": round(sum(_number(l["km"]) for l in legs), 1),
		"source": combine_sources(l.get("source") for l in legs),
	}


def matrix_blocks(pairs, size=MATRIX_BLOCK):
	"""Split the needed ``(origin, destination)`` pairs into requests of at most size × size.

	Origins are chunked first; each chunk asks only for the destinations its origins need,
	chunked again. Returns ``[(origins, destinations)]``, deterministic (sorted points).
	"""
	by_origin = {}
	for a, b in pairs:
		by_origin.setdefault(a, set()).add(b)
	origins = sorted(by_origin)
	blocks = []
	for i in range(0, len(origins), size):
		chunk = origins[i : i + size]
		wanted = sorted(set().union(*(by_origin[o] for o in chunk)))
		for j in range(0, len(wanted), size):
			dests = wanted[j : j + size]
			users = [o for o in chunk if by_origin[o] & set(dests)]
			blocks.append((users, dests))
	return blocks


def _duration_seconds(value):
	if value is None:
		return None
	try:
		return float(str(value).strip().rstrip("s"))
	except (TypeError, ValueError):
		return None


def parse_matrix(payload, origins, destinations):
	"""``{(origin, destination): {"minutes", "km"}}`` from a computeRouteMatrix answer.

	The REST answer is a JSON array of elements. Proto3 JSON leaves default values out, so an
	``originIndex`` of 0 and a ``distanceMeters`` of 0 are simply missing. Only elements with
	``condition == "ROUTE_EXISTS"`` and no error ``status.code`` count.
	"""
	out = {}
	for element in payload if isinstance(payload, list) else []:
		if not isinstance(element, dict) or element.get("condition") != "ROUTE_EXISTS":
			continue
		if (element.get("status") or {}).get("code"):
			continue
		seconds = _duration_seconds(element.get("duration"))
		try:
			origin = origins[int(element.get("originIndex") or 0)]
			destination = destinations[int(element.get("destinationIndex") or 0)]
		except (IndexError, TypeError, ValueError):
			continue
		if seconds is None:
			continue
		out[(origin, destination)] = {
			"minutes": round(seconds / 60, 1),
			"km": round(_number(element.get("distanceMeters")) / 1000, 2),
		}
	return out


def google_error_status(payload):
	"""Google's error status string (``"PERMISSION_DENIED"``) from an error body, else ``""``.

	Never the message: it can carry project ids and links, and the log only needs the status.
	"""
	if isinstance(payload, list) and payload:
		payload = payload[0]
	if isinstance(payload, dict):
		error = payload.get("error")
		if isinstance(error, dict):
			return str(error.get("status") or error.get("code") or "")
		if payload.get("status") and isinstance(payload.get("status"), str):
			return payload["status"]
	return ""


# ---------------------------------------------------------------------- framework glue


def _flag(name):
	try:
		return bool(frappe.cache.get_value(name))
	except Exception:
		return False


def _set_flag(name, seconds=DOWN_SECONDS):
	try:
		frappe.cache.set_value(name, 1, expires_in_sec=seconds)
	except Exception:
		pass


def _clear_flag(name):
	try:
		frappe.cache.delete_value(name)
	except Exception:
		pass


def _keep_writes():
	"""Ask Frappe to commit this request even when it is a GET (see the module docstring)."""
	try:
		frappe.local.flags.commit = True
	except Exception:
		pass


def _settings_doc():
	try:
		return frappe.get_cached_doc(SETTINGS)
	except Exception:
		return None


def _use_google(settings=None):
	"""Project Planner Settings ``use_google_routes``; None (an unsaved new field) means on."""
	if settings is None:
		doc = _settings_doc()
		value = doc.get("use_google_routes") if doc is not None else None
	else:
		value = settings.get("use_google_routes")
	return True if value is None else bool(_number(value))


def _server_key():
	from erpnext_enhancements.workforce.sites import _geocoding_api_key

	return _geocoding_api_key()


def _has_key():
	try:
		return bool(_server_key())
	except Exception:
		return False


def _google_post(body):
	"""POST one computeRouteMatrix request. ``(http_status, payload, problem)``.

	The key is read here and nowhere else in this module, and this function catches everything:
	it returns a status code and Google's error status (or an exception's class name), never a
	message, a URL or a traceback. ``problem`` is None on success.
	"""
	try:
		key = _server_key()
		if not key:
			return None, None, "no server key"
		import requests

		response = requests.post(
			ROUTES_URL,
			json=body,
			headers={
				"X-Goog-Api-Key": key,
				"X-Goog-FieldMask": ROUTES_FIELD_MASK,
				"Content-Type": "application/json",
			},
			timeout=TIMEOUT_S,
		)
		status = int(response.status_code)
		try:
			payload = response.json()
		except Exception:
			payload = None
		if status >= 400:
			return status, None, google_error_status(payload) or "error"
		return status, payload, None
	except Exception as exc:
		return None, None, type(exc).__name__


def _google_geocode(address):
	"""Geocode one address: ``(point, problem)``.

	Delegates to ``workforce.sites.geocode_text``, the app's one server-side geocoder, which
	never raises, never logs and never lets the key into ``problem``
	(``tests/test_geocoding_key.py`` fails the build on a second caller of the Geocoding API).
	"""
	from erpnext_enhancements.workforce.sites import geocode_text

	found, problem = geocode_text(address)
	if not found:
		return None, problem
	point = make_point(found.get("lat"), found.get("lng"))
	return (point, None) if point else (None, "no point")


def _matrix_body(origins, destinations):
	def waypoint(point):
		return {"waypoint": {"location": {"latLng": {"latitude": point[0], "longitude": point[1]}}}}

	return {
		"origins": [waypoint(p) for p in origins],
		"destinations": [waypoint(p) for p in destinations],
		"travelMode": "DRIVE",
		"routingPreference": "TRAFFIC_UNAWARE",
	}


def _routes_down(status, problem):
	"""Google refused or failed: stop asking for an hour and say so once."""
	_set_flag(ROUTES_DOWN_FLAG, DOWN_SECONDS)
	frappe.log_error(
		title=DOWN_TITLE, message=f"HTTP {status if status is not None else '-'}: {problem or '-'}"
	)


def _fetch_google(pairs):
	"""``{pair: {"minutes", "km"}}`` from Google for ``pairs``; ``{}`` (and the down flag) on failure."""
	found = {}
	for origins, destinations in matrix_blocks(pairs):
		status, payload, problem = _google_post(_matrix_body(origins, destinations))
		if problem:
			_routes_down(status, problem)
			break
		found.update(parse_matrix(payload, origins, destinations))
	return found


def _duplicate_errors():
	return tuple(
		error
		for error in (
			getattr(frappe, "DuplicateEntryError", None),
			getattr(frappe, "UniqueValidationError", None),
		)
		if isinstance(error, type)
	)


def _store(fetched, stale):
	"""Write Google's answers to the cache. A row another request wrote first is fine."""
	if not fetched:
		return
	from frappe.utils import now_datetime

	now = now_datetime()
	duplicates = _duplicate_errors()
	wrote = False
	# A refused insert raises through frappe.throw, which queues its message for the browser even
	# when the exception is caught here: the planner would open on an error dialog for a cache
	# miss. Mute it; a real failure is still logged below.
	muted = getattr(frappe.flags, "mute_messages", None)
	frappe.flags.mute_messages = True
	try:
		wrote = _store_rows(fetched, stale, now, duplicates)
	finally:
		frappe.flags.mute_messages = muted
	if wrote:
		_keep_writes()


def _store_rows(fetched, stale, now, duplicates):
	wrote = False
	for (a, b), value in fetched.items():
		# A matrix block is every origin against every destination, so Google also answers each
		# point to itself. drive_matrix never asks for those; storing them is just rows.
		if same_point(a, b):
			continue
		key = pair_key(a, b)
		values = {
			"origin_lat": a[0],
			"origin_lng": a[1],
			"dest_lat": b[0],
			"dest_lng": b[1],
			"minutes": value["minutes"],
			"km": value["km"],
			"source": "Google",
			"fetched_on": now,
		}
		try:
			if key in stale:
				frappe.db.set_value(DRIVE_TIME, key, values, update_modified=False)
			else:
				frappe.get_doc({"doctype": DRIVE_TIME, "pair_key": key, **values}).insert(
					ignore_permissions=True
				)
			wrote = True
		except duplicates:
			continue
		except Exception as exc:
			# The cache is an optimization; a site part-way through a migrate has no table yet.
			frappe.log_error(
				title="Project Planner: drive time cache write failed",
				message=f"{key}: {type(exc).__name__}",
			)
			break
	return wrote


def _read_cache(pairs):
	"""``({pair: row}, {pair: row})``: fresh rows and stale ones, by pair."""
	keys = {pair_key(a, b): (a, b) for a, b in pairs}
	fresh, stale = {}, {}
	if not keys:
		return fresh, stale
	from frappe.utils import now_datetime

	# Site-local time on both sides: ``fetched_on`` is written with now_datetime() too.
	cutoff = now_datetime() - datetime.timedelta(days=CACHE_DAYS)
	names = sorted(keys)
	try:
		for i in range(0, len(names), 500):
			for row in frappe.get_all(
				DRIVE_TIME,
				filters={"pair_key": ["in", names[i : i + 500]]},
				fields=["pair_key", "minutes", "km", "fetched_on"],
				limit_page_length=0,
			):
				pair = keys.get(row.get("pair_key"))
				if not pair:
					continue
				fetched = row.get("fetched_on")
				if isinstance(fetched, str):
					try:
						fetched = datetime.datetime.fromisoformat(fetched[:19])
					except ValueError:
						fetched = None
				if isinstance(fetched, datetime.datetime) and fetched >= cutoff:
					fresh[pair] = row
				else:
					stale[pair] = row
	except Exception:
		# No cache table yet (deploy before migrate): everything is a miss.
		return {}, {}
	return fresh, stale


def drive_matrix(pairs, settings=None, google=True):
	"""``{(a, b): {"minutes", "km", "source"}}`` for every leg asked for.

	Cache first (one query), then Google Routes for the misses when Settings
	``use_google_routes`` is on, a key exists and Google has not failed in the last hour, then a
	straight-line estimate. A stale cached leg Google could not refresh is still used (a
	three-month-old drive time beats a guess). ``source`` is ``"google"`` or ``"estimate"``.
	Pairs of the same point are answered 0 and never sent anywhere.

	``google=False`` skips Google whatever Settings say: cached legs (fresh or stale) and the
	estimate only. The capacity heatmap prices eight weeks of routes this way, so one view can
	never fan out into hundreds of billable requests.
	"""
	wanted = []
	seen = set()
	for a, b in pairs or ():
		if not (valid_point(a) and valid_point(b)) or same_point(a, b) or (a, b) in seen:
			continue
		seen.add((a, b))
		wanted.append((a, b))
	out = {}
	if not wanted:
		return out

	fresh, stale = _read_cache(wanted)
	for pair, row in fresh.items():
		out[pair] = {"minutes": _number(row.get("minutes")), "km": _number(row.get("km")), "source": "google"}
	missing = [pair for pair in wanted if pair not in out]

	fetched = {}
	if missing and google and _use_google(settings) and not _flag(ROUTES_DOWN_FLAG) and _has_key():
		fetched = _fetch_google(missing)
		_store(fetched, {pair_key(*pair) for pair in stale})

	for pair in missing:
		if pair in fetched:
			out[pair] = {**fetched[pair], "source": "google"}
		elif pair in stale:
			row = stale[pair]
			out[pair] = {
				"minutes": _number(row.get("minutes")),
				"km": _number(row.get("km")),
				"source": "google",
			}
		else:
			out[pair] = estimate_leg(*pair)
	return out


def routes_status():
	"""``{"google": bool, "detail": str}``: does Google Routes answer for this site's key now?

	One 1×1 matrix (the shop to a point 0.01° north), ignoring the cache and the down flag, so Nik
	can confirm a Cloud console change without reading a key. Success clears the down flag.
	"""
	if not _has_key():
		return {
			"google": False,
			"detail": "No server Google key is set (Travel Settings: Google geocoding API key).",
		}
	shop = start_point() or (40.8840, -111.8820)  # the shop, roughly, if it cannot be geocoded
	probe = (round(shop[0] + 0.01, 6), shop[1])
	status, payload, problem = _google_post(_matrix_body([shop], [probe]))
	if problem:
		_set_flag(ROUTES_DOWN_FLAG, DOWN_SECONDS)
		return {
			"google": False,
			"detail": f"Google Routes refused the request (HTTP {status if status is not None else '-'}: "
			f"{problem}). Enable the Routes API for the server key in the Google Cloud console.",
		}
	found = parse_matrix(payload, [shop], [probe]).get((shop, probe))
	if not found:
		return {"google": False, "detail": "Google Routes answered but returned no route for the test leg."}
	_clear_flag(ROUTES_DOWN_FLAG)
	note = (
		"" if _use_google() else " The planner's Settings have Google Routes turned off, so it is not used."
	)
	return {
		"google": True,
		"detail": f"Google Routes is working: a test leg from the shop took {found['minutes']:g} min.{note}",
	}


# ---------------------------------------------------------------------- where things are


def _address_text(row):
	from erpnext_enhancements.api.pickup_routing import _address_line

	return _address_line(row) or ""


def _columns(doctype, columns):
	present = []
	for column in columns:
		try:
			if frappe.db.has_column(doctype, column):
				present.append(column)
		except Exception:
			pass
	return present


def _addresses(names, with_text=False):
	"""``{address: {"point", "address"}}`` in one query."""
	names = sorted({n for n in names if n})
	if not names or "custom_latitude" not in _columns("Address", ("custom_latitude",)):
		return {}
	fields = ["name", "custom_latitude", "custom_longitude"]
	if with_text:
		fields += ["address_line1", "address_line2", "city", "state", "pincode", "country"]
		fields += _columns("Address", ("custom_full_address",))
	out = {}
	for row in frappe.get_all("Address", filters={"name": ["in", names]}, fields=fields, limit_page_length=0):
		out[row.get("name")] = {
			"point": make_point(row.get("custom_latitude"), row.get("custom_longitude")),
			"address": _address_text(row) if with_text else None,
		}
	return out


def locate(bookings, detail=False):
	"""Where each stop is: ``{ref: (lat, lng) | None}``.

	* task → its ``custom_locationaddress_of_task`` Address → its project's site;
	* rental task → its Rental Booking's ``venue_address`` → its own Address → the project;
	* visit (stored or projected) → its project's site.

	Bulk queries only: Tasks, Rental Bookings, Addresses, then ``site_coordinates_bulk``. With
	``detail``, ``{ref: {"point", "address"}}`` where ``address`` is readable text for the route
	view (the Address the point came from, else the project's own address).
	"""
	from erpnext_enhancements.workforce.sites import site_coordinates_bulk

	bookings = [b for b in bookings or [] if b.get("ref") and b.get("kind") in STOP_KINDS]
	task_refs = sorted({b["ref"] for b in bookings if b.get("kind") in ("task", "rental")})
	projects = {b["ref"]: b.get("project") for b in bookings if b.get("project")}

	tasks = {}
	if task_refs:
		columns = _columns("Task", ("custom_locationaddress_of_task", "custom_rental_booking"))
		for row in frappe.get_all(
			"Task",
			filters={"name": ["in", task_refs]},
			fields=["name", "project", *columns],
			limit_page_length=0,
		):
			tasks[row.get("name")] = row
			if row.get("project"):
				projects.setdefault(row.get("name"), row.get("project"))

	venues = {}
	bookings_of = {
		r: t.get("custom_rental_booking") for r, t in tasks.items() if t.get("custom_rental_booking")
	}
	if bookings_of and _columns("Rental Booking", ("venue_address",)):
		for row in frappe.get_all(
			"Rental Booking",
			filters={"name": ["in", sorted(set(bookings_of.values()))]},
			fields=["name", "venue_address", "project"],
			limit_page_length=0,
		):
			venues[row.get("name")] = row

	address_names = [t.get("custom_locationaddress_of_task") for t in tasks.values()]
	address_names += [v.get("venue_address") for v in venues.values()]
	addresses = _addresses(address_names, with_text=detail)

	project_names = sorted(
		set(projects.values()) | {v.get("project") for v in venues.values() if v.get("project")}
	)
	sites = site_coordinates_bulk(project_names) if project_names else {}
	project_text = _project_addresses(project_names) if (detail and project_names) else {}

	out = {}
	for booking in bookings:
		ref = booking["ref"]
		task = tasks.get(ref) or {}
		chain = []
		if booking.get("kind") == "rental" or task.get("custom_rental_booking"):
			venue = venues.get(task.get("custom_rental_booking")) or {}
			chain.append(addresses.get(venue.get("venue_address")))
			if venue.get("project"):
				projects.setdefault(ref, venue.get("project"))
		if booking.get("kind") in ("task", "rental"):
			chain.append(addresses.get(task.get("custom_locationaddress_of_task")))
		found = next((entry for entry in chain if entry and entry.get("point")), None)
		project = projects.get(ref)
		if found:
			value = {"point": found["point"], "address": found.get("address")}
		else:
			site = sites.get(project) if project else None
			point = make_point(site.get("lat"), site.get("lng")) if site else None
			value = {"point": point, "address": project_text.get(project)}
		out[ref] = value if detail else value["point"]
	return out


def _project_addresses(projects):
	"""``{project: address text}`` from the Project's own ``custom_project_address``."""
	if not _columns("Project", ("custom_project_address",)):
		return {}
	return {
		row.get("name"): (row.get("custom_project_address") or "").strip() or None
		for row in frappe.get_all(
			"Project",
			filters={"name": ["in", sorted(projects)]},
			fields=["name", "custom_project_address"],
			limit_page_length=0,
		)
	}


def start_point(google=True):
	"""The shop's ``(lat, lng)``, or None when it cannot be located.

	From Project Planner Settings' cached ``start_latitude/longitude`` while
	``start_geocoded_from`` still equals the shop address (``pickup_routing._depot_address()``);
	otherwise the address is geocoded and the three fields are written. A failed geocode is not
	retried for an hour. ``google=False`` never geocodes: the cached point, even one geocoded
	from an older address (the shop rarely moves far), else None.
	"""
	from erpnext_enhancements.api.pickup_routing import _depot_address

	address = (_depot_address() or "").strip()
	doc = _settings_doc()
	cached = make_point(doc.get("start_latitude"), doc.get("start_longitude")) if doc is not None else None
	if cached and (doc.get("start_geocoded_from") or "").strip() == address:
		return cached
	if not google:
		return cached
	if not address or _flag(SHOP_FAILED_FLAG):
		return None
	point, problem = _google_geocode(address)
	if not point:
		_set_flag(SHOP_FAILED_FLAG, DOWN_SECONDS)
		frappe.log_error(
			title="Project Planner: the shop could not be located",
			message=f"Geocoding the shop address ({address}) failed: {problem}",
		)
		return None
	try:
		# Every other default gets its row first. Writing these three rows into a Settings nobody
		# has saved would end load_from_db's new_doc() defaults, and a blank Check loads as 0:
		# padding and Google would switch themselves off (v1.578.1, see the Settings controller).
		from erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings import (
			materialize_defaults,
		)

		materialize_defaults()
		frappe.db.set_single_value(
			SETTINGS,
			{"start_latitude": point[0], "start_longitude": point[1], "start_geocoded_from": address},
			update_modified=False,
		)
		_keep_writes()
	except Exception:
		frappe.log_error(title="Project Planner: could not save the shop's coordinates", message=address)
	return point


def geocode_address(name):
	"""Geocode one Address into ``custom_latitude/longitude`` (source "Google").

	Never over a "Manual" Address (a person pinned it), and a no-op when it already has a point.
	Returns the point or None. **Never raises.**
	"""
	try:
		columns = _columns("Address", ("custom_latitude", "custom_longitude", "custom_location_source"))
		if "custom_latitude" not in columns:
			return None
		fields = ["name", "address_line1", "address_line2", "city", "state", "pincode", "country", *columns]
		fields += _columns("Address", ("custom_full_address",))
		row = frappe.db.get_value("Address", name, fields, as_dict=True)
		if not row:
			return None
		existing = make_point(row.get("custom_latitude"), row.get("custom_longitude"))
		if existing:
			return existing
		if (row.get("custom_location_source") or "") == "Manual":
			return None
		text = _address_text(row)
		if not text:
			return None
		point, problem = _google_geocode(text)
		if not point:
			return None
		values = {"custom_latitude": point[0], "custom_longitude": point[1]}
		if "custom_location_source" in columns:
			values["custom_location_source"] = "Google"
		frappe.db.set_value("Address", name, values, update_modified=False)
		return point
	except Exception:
		frappe.log_error(title="Project Planner: geocode_address failed", message=str(name))
		return None


def _missing_address_names(limit):
	"""Addresses with no point that an open dated Task or an upcoming rental will drive to."""
	from frappe.utils import nowdate

	from erpnext_enhancements.project_enhancements.crew_availability import FINISHED_STATUSES

	names = []
	if not _columns("Address", ("custom_latitude",)):
		return names
	today = nowdate()
	if _columns("Task", ("custom_locationaddress_of_task",)):
		names += [
			row[0]
			for row in frappe.db.sql(
				"""
				SELECT DISTINCT t.custom_locationaddress_of_task
				FROM `tabTask` t
				INNER JOIN `tabAddress` a ON a.name = t.custom_locationaddress_of_task
				WHERE IFNULL(t.status, '') NOT IN %(finished)s
					AND COALESCE(DATE(t.exp_end_date), DATE(t.exp_start_date)) >= %(today)s
					AND (IFNULL(a.custom_latitude, 0) = 0 OR IFNULL(a.custom_longitude, 0) = 0)
				LIMIT %(limit)s
				""",
				{"finished": FINISHED_STATUSES, "today": today, "limit": limit},
			)
		]
	if _columns("Rental Booking", ("venue_address",)):
		names += [
			row[0]
			for row in frappe.db.sql(
				"""
				SELECT DISTINCT rb.venue_address
				FROM `tabRental Booking` rb
				INNER JOIN `tabAddress` a ON a.name = rb.venue_address
				WHERE rb.status IN %(statuses)s
					AND (IFNULL(a.custom_latitude, 0) = 0 OR IFNULL(a.custom_longitude, 0) = 0)
				LIMIT %(limit)s
				""",
				{"statuses": UPCOMING_RENTAL_STATUSES, "limit": limit},
			)
		]
	return [n for n in dict.fromkeys(names) if n]


def _projects_to_locate(limit):
	"""Projects of open dated Tasks and of upcoming visits."""
	from frappe.utils import nowdate

	from erpnext_enhancements.project_enhancements.crew_availability import FINISHED_STATUSES

	today = nowdate()
	names = [
		row[0]
		for row in frappe.db.sql(
			"""
			SELECT DISTINCT t.project
			FROM `tabTask` t
			WHERE IFNULL(t.project, '') != ''
				AND IFNULL(t.status, '') NOT IN %(finished)s
				AND COALESCE(DATE(t.exp_end_date), DATE(t.exp_start_date)) >= %(today)s
			LIMIT %(limit)s
			""",
			{"finished": FINISHED_STATUSES, "today": today, "limit": limit * 5},
		)
	]
	try:
		names += [
			row[0]
			for row in frappe.db.sql(
				"""
				SELECT DISTINCT r.project
				FROM `tabSapphire Maintenance Record` r
				WHERE r.docstatus = 0 AND IFNULL(r.project, '') != ''
					AND r.scheduled_visit_date >= %(today)s
				LIMIT %(limit)s
				""",
				{"today": today, "limit": limit * 5},
			)
		]
	except Exception:
		pass
	return [n for n in dict.fromkeys(names) if n]


def backfill_coordinates(limit=BACKFILL_LIMIT):
	"""Daily job: find the places the planners' routes cannot locate yet.

	Geocodes Addresses used by open dated Tasks and upcoming rentals, and runs
	``workforce.sites.geocode_project`` for the projects of open dated tasks and upcoming visits
	that resolve to nothing. At most ``limit`` attempts per run. Daily because a deploy
	``FLUSHDB``s the queue redis: a geocode enqueued by a planner load may simply never run, so
	this re-drives whatever is still missing. **Never raises.** Returns the attempts made.
	"""
	attempts = 0
	try:
		limit = max(int(limit or BACKFILL_LIMIT), 1)
		start_point()
		for name in _missing_address_names(limit):
			if attempts >= limit:
				break
			geocode_address(name)
			attempts += 1
		if attempts < limit:
			from erpnext_enhancements.workforce.sites import geocode_project, site_coordinates_bulk

			projects = _projects_to_locate(limit)
			located = site_coordinates_bulk(projects) if projects else {}
			for project in projects:
				if attempts >= limit:
					break
				if project in located:
					continue
				geocode_project(project)
				attempts += 1
	except Exception:
		frappe.log_error(title="Project Planner: coordinate backfill failed", message=frappe.get_traceback())
	return attempts


def enqueue_missing(refs):
	"""A planner load found stops with no location: run the backfill soon (once an hour at most).

	Deduplicated by job id, never after-commit (a GET's transaction is rolled back, and an
	after-commit job would never be queued). **Never raises.**
	"""
	if not refs or _flag(BACKFILL_QUEUED_FLAG):
		return False
	try:
		frappe.enqueue(BACKFILL_JOB, queue="long", job_id=BACKFILL_JOB_ID, deduplicate=True)
		_set_flag(BACKFILL_QUEUED_FLAG, DOWN_SECONDS)
		return True
	except Exception:
		return False


# ---------------------------------------------------------------------- per person-day


def _stop(booking, point):
	return {
		"ref": booking.get("ref"),
		"kind": booking.get("kind"),
		"label": booking.get("label"),
		"project": booking.get("project"),
		"slot": booking.get("slot"),
		"hours": _number(booking.get("hours")),
		"point": point,
		"booking": booking,
	}


def plan_routes(day_bookings, settings=None, google=True):
	"""Routes for many person-days at once: ``{key: route}`` (see :func:`build_route`).

	``day_bookings`` is ``{(resource, day): [booking, ...]}``. A day with a travel booking has no
	route (the person is away); a day with no task, rental or visit has nothing to route. Every
	stop is located in one :func:`locate`, every leg any day needs is priced in **one**
	:func:`drive_matrix` call, and then each day is ordered on its own.

	``google=False`` makes no Google request of any kind: the shop comes from its cached point
	(:func:`start_point`) and legs from the cache or the estimate (:func:`drive_matrix`).
	"""
	options = {} if google else {"google": False}
	days = {}
	for key, bookings in (day_bookings or {}).items():
		if any(b.get("kind") == "travel" for b in bookings or []):
			continue
		stops = [b for b in bookings or [] if b.get("kind") in STOP_KINDS and b.get("ref")]
		if stops:
			days[key] = stops
	if not days:
		return {}

	points = locate([b for stops in days.values() for b in stops])
	shop = start_point(**options) if any(points.values()) else None

	day_stops, pairs = {}, set()
	for key, bookings in days.items():
		stops = [_stop(b, points.get(b.get("ref"))) for b in bookings]
		day_stops[key] = stops
		distinct = []
		for point in [shop, *(s["point"] for s in stops)]:
			if point is not None and not any(same_point(point, p) for p in distinct):
				distinct.append(point)
		pairs.update((a, b) for a in distinct for b in distinct if not same_point(a, b))
	matrix = drive_matrix(pairs, settings, **options) if pairs else {}

	routes = {key: build_route(shop, stops, matrix) for key, stops in day_stops.items()}
	missing = sorted({s["ref"] for route in routes.values() for s in route["unlocated"]})
	if missing:
		enqueue_missing(missing)
	return routes

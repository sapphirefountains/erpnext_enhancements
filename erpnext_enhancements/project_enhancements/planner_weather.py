# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Weather flags for outdoor work on the Project Planner (Phase 5, P5.2).

Which days ahead look bad for a task marked *Outdoor work* (``Task.custom_outdoor``)? The answer
comes from **Open-Meteo**'s daily forecast (Nik's choice, 2026-10-09: free, and it needs no API
key). A day is flagged when the forecast crosses one of three lines (the constants below):

* precipitation probability of 60% or more → ``"Rain 70%"``;
* a minimum temperature at or below 0 °C → ``"Freezing −3 °C"``. Fountains care about freezing
  more than most outdoor work does: a pump test or a fill on a night that freezes is wasted;
* wind of 40 km/h or more → ``"Wind 45 km/h"``.

The location is the task's own, found by ``routing.locate`` exactly as the route view finds it
(the task's address, else its project's site), so a chip and a route never disagree about where
the job is.

Things this module is careful about, some of which look like bugs:

* **One request for every uncached point.** Open-Meteo takes comma-separated coordinates and
  answers with a list, so a planner load asks once for all the sites it has not seen in the last
  three hours, never once per task.
* **Cached in redis for three hours per point**, rounded to two decimals (about a kilometre), so
  two tasks on the same site share an answer. The deploy ``FLUSHDB``s redis and loses the cache;
  that is fine, it is a cache.
* **Failures hide the chip and never the planner.** A timeout, an HTTP error or an answer that
  cannot be read leaves the task with no weather. The failure is logged **once an hour** (a flag
  in redis), and for ten minutes after one the planner does not ask again, so a down Open-Meteo
  cannot make every load wait eight seconds.
* **Nothing secret is involved, and nothing that looks like a URL is logged anyway.** The log
  message carries an HTTP status or an exception's class name, never ``str(exc)``: ``requests``
  puts the full URL with its query string into its messages, and the rule in this app is that a
  logged request is described, not quoted.
* **The forecast window is 16 days from today**, Open-Meteo's own limit. A task further out, or
  one with no location, has no weather (None), which is different from a clear forecast ([]).

The parsing and flag wording are pure functions so ``tests/test_planner_phase5.py`` runs them
without a bench.
"""

import datetime

import frappe

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_FIELDS = ("precipitation_probability_max", "temperature_2m_min", "wind_speed_10m_max")
#: The site's own timezone (System Settings), so a forecast "day" is the day the crew works.
TIMEZONE = "America/Denver"
FORECAST_DAYS = 16
TIMEOUT_S = 8
CACHE_SECONDS = 3 * 3600
CACHE_PREFIX = "ee_planner_weather:"
#: Most points in one request; more go in another.
BATCH = 50

# The three lines a forecast must cross to put a chip on an outdoor task. Chosen for fountain work:
# rain at 60% is the point a crew lead starts moving a pour or a fill; freezing at all matters
# because water left in a line or a basin overnight is the damage; 40 km/h of wind blows spray off
# the basin and makes lifts and tarps unsafe.
RAIN_PERCENT = 60
FREEZING_C = 0
WIND_KMH = 40

#: Set for an hour after a failure is logged, so the Error Log gets one entry an hour, not one a load.
LOGGED_FLAG = "ee_planner_weather_logged"
LOG_SECONDS = 3600
#: Set for ten minutes after a failure: the planner does not wait on Open-Meteo again until it lapses.
BACKOFF_FLAG = "ee_planner_weather_backoff"
BACKOFF_SECONDS = 600
LOG_TITLE = "Project Planner: weather unavailable"

MINUS = "−"


# ---------------------------------------------------------------------- pure helpers


def _number(value):
	try:
		if value is None or value == "":
			return None
		return float(value)
	except (TypeError, ValueError):
		return None


def point_key(point):
	"""``"39.74,-104.99"``: a point rounded to two decimals, the cache key and request unit."""
	if not point:
		return None
	lat, lng = _number(point[0]), _number(point[1])
	if lat is None or lng is None or (lat == 0 and lng == 0):
		return None
	return f"{round(lat, 2):.2f},{round(lng, 2):.2f}"


def _whole(value):
	"""``-3.4`` → ``"−3"``: a whole number, with a real minus sign."""
	number = int(round(value))
	return f"{MINUS}{abs(number)}" if number < 0 else str(number)


def day_flags(rain=None, low=None, wind=None):
	"""The chips for one day's forecast, in a fixed order: rain, freezing, wind."""
	out = []
	rain, low, wind = _number(rain), _number(low), _number(wind)
	if rain is not None and rain >= RAIN_PERCENT:
		out.append(f"Rain {int(round(rain))}%")
	if low is not None and low <= FREEZING_C:
		out.append(f"Freezing {_whole(low)} °C")
	if wind is not None and wind >= WIND_KMH:
		out.append(f"Wind {int(round(wind))} km/h")
	return out


def parse_daily(location):
	"""``{"YYYY-MM-DD": [flags]}`` from one location's answer (every forecast day, [] when clear).

	A location with no ``daily`` block, or arrays of the wrong length, reads as {}: no chip.
	"""
	daily = (location or {}).get("daily") if isinstance(location, dict) else None
	if not isinstance(daily, dict):
		return {}
	days = daily.get("time") or []
	columns = [daily.get(field) or [] for field in DAILY_FIELDS]
	out = {}
	for index, day in enumerate(days):
		values = [column[index] if index < len(column) else None for column in columns]
		out[str(day)[:10]] = day_flags(*values)
	return out


def parse_payload(payload, count):
	"""One ``parse_daily`` answer per requested point, in order, or None when unreadable.

	Open-Meteo answers one point with an object and several with a list of objects.
	"""
	if isinstance(payload, dict) and count == 1:
		payload = [payload]
	if not isinstance(payload, list) or len(payload) != count:
		return None
	return [parse_daily(location) for location in payload]


def request_params(keys):
	"""The query for a list of :func:`point_key` values."""
	lats = ",".join(key.split(",")[0] for key in keys)
	lngs = ",".join(key.split(",")[1] for key in keys)
	return {
		"latitude": lats,
		"longitude": lngs,
		"daily": ",".join(DAILY_FIELDS),
		"timezone": TIMEZONE,
		"forecast_days": FORECAST_DAYS,
	}


def window(today):
	"""``(today, last forecast day)``."""
	return today, today + datetime.timedelta(days=FORECAST_DAYS - 1)


def span_weather(span, forecast, today):
	"""``[{"date", "flags"}]`` for the flagged days of ``span`` inside the forecast window.

	None when there is nothing to say (no span, no forecast, or the span is outside the window);
	[] when the forecast is clear for every day of it that the window covers.
	"""
	if not span or forecast is None:
		return None
	first, last = window(today)
	low, high = max(span[0], first), min(span[1], last)
	if low > high:
		return None
	out = []
	day = low
	while day <= high:
		flags = forecast.get(str(day)) or []
		if flags:
			out.append({"date": str(day), "flags": list(flags)})
		day += datetime.timedelta(days=1)
	return out


def flags_on(forecast, day):
	"""The flags for one day, [] when clear or unknown."""
	return list((forecast or {}).get(str(day)) or [])


# ---------------------------------------------------------------------- framework glue


def _flag(name):
	try:
		return bool(frappe.cache.get_value(name))
	except Exception:
		return False


def _set_flag(name, seconds):
	try:
		frappe.cache.set_value(name, 1, expires_in_sec=seconds)
	except Exception:
		pass


def _cached(key):
	try:
		return frappe.cache.get_value(CACHE_PREFIX + key)
	except Exception:
		return None


def _store(key, value):
	try:
		frappe.cache.set_value(CACHE_PREFIX + key, value, expires_in_sec=CACHE_SECONDS)
	except Exception:
		pass


def _failed(problem):
	"""Back off for ten minutes and log once an hour. ``problem`` is a status or a class name."""
	_set_flag(BACKOFF_FLAG, BACKOFF_SECONDS)
	if _flag(LOGGED_FLAG):
		return
	_set_flag(LOGGED_FLAG, LOG_SECONDS)
	try:
		frappe.log_error(
			title=LOG_TITLE,
			message=(
				f"Open-Meteo did not answer the planner's forecast request ({problem}). Outdoor tasks "
				"show no weather chip until it does; nothing else is affected. Logged at most once an hour."
			),
		)
	except Exception:
		pass


def _fetch(keys):
	"""``({key: {date: [flags]}}, problem)`` for one request. Catches everything."""
	try:
		import requests

		response = requests.get(OPEN_METEO_URL, params=request_params(keys), timeout=TIMEOUT_S)
		status = int(response.status_code)
		if status >= 400:
			return None, f"HTTP {status}"
		payload = response.json()
	except Exception as exc:
		# The class name only: a requests message embeds the URL and its query string.
		return None, type(exc).__name__
	results = parse_payload(payload, len(keys))
	if results is None:
		return None, "an answer that could not be read"
	return dict(zip(keys, results, strict=True)), None


def forecasts(points):
	"""``{point_key: {date: [flags]}}`` for ``points`` (an iterable of (lat, lng)).

	Cached answers first; the rest in as few requests as possible. A point whose request failed
	is missing from the answer. Never raises.
	"""
	keys = sorted({key for key in (point_key(p) for p in points or ()) if key})
	out, missing = {}, []
	for key in keys:
		value = _cached(key)
		if isinstance(value, dict):
			out[key] = value
		else:
			missing.append(key)
	if not missing or _flag(BACKOFF_FLAG):
		return out
	for index in range(0, len(missing), BATCH):
		chunk = missing[index : index + BATCH]
		answer, problem = _fetch(chunk)
		if problem:
			_failed(problem)
			break
		for key, value in answer.items():
			_store(key, value)
			out[key] = value
	return out


def task_points(tasks):
	"""``{task name: (lat, lng) | None}`` through ``routing.locate`` (task address, then project)."""
	from erpnext_enhancements.project_enhancements import routing

	bookings = [
		{
			"kind": "rental" if task.get("custom_rental_booking") else "task",
			"ref": task.get("name"),
			"project": task.get("project"),
		}
		for task in tasks or []
		if task.get("name")
	]
	if not bookings:
		return {}
	try:
		return routing.locate(bookings)
	except Exception:
		frappe.log_error(title="Project Planner: weather locate failed", message=frappe.get_traceback())
		return {}


def forecast_for_tasks(tasks):
	"""``{task name: {date: [flags]} | None}`` for ``tasks`` (rows with name/project), never raising."""
	try:
		points = task_points(tasks)
		known = forecasts(p for p in points.values() if p)
		return {name: known.get(point_key(point)) if point else None for name, point in points.items()}
	except Exception:
		frappe.log_error(title="Project Planner: weather failed", message=frappe.get_traceback())
		return {}

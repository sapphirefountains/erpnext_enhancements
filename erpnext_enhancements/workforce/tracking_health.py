# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Tracking health — how much of a clock-in session the location trail actually covers.

Pure functions over timestamps and coordinates. **No ``frappe`` import**, on purpose: the
bench-free suite ``tests/test_workforce_tracking_health.py`` runs these under plain
``unittest`` with no stub, and ``api.time_kiosk`` / ``workforce.sweeper`` /
``workforce.corrections`` call them with data they have already fetched.

The question every function here answers is the one a supervisor asks when a technician's
day looks wrong: *was the phone actually reporting, and if not, for how long?* The kiosk
records a fix at least every ``heartbeat_seconds`` while clocked in and active
(Time Kiosk Settings), so a silence longer than ``tracking_gap_minutes`` means the device
stopped reporting — screen locked, Low Power Mode, permission revoked, no signal — and the
trail for that stretch is unknown rather than stationary.

Definitions, stated once
------------------------
* **Span** — ``[start, end]`` of the interval. An open interval is scored to *now* by the
  caller.
* **Gap** — a stretch of the span with no fix that is longer than ``gap_minutes``. The
  leading stretch (start to first fix) and the trailing one (last fix to end) count, so a
  phone that only woke up an hour into the job shows a one-hour gap at the front. The
  **whole** silent stretch is a gap, not just the middle of it: the settings field says "a
  stretch with no fix longer than this is a gap", and that is what the timeline draws (a
  dashed segment between the two fixes either side). This is deliberately stricter than
  "minutes within ``gap_minutes`` of some fix", which would let a 25-minute silence pass
  silently under a 15-minute setting.
* **Coverage** — the span minus its gaps, as a percentage. A span shorter than a minute is
  100 % covered — there is nothing to be missing from.
* **Health** — ``Off`` when tracking is disabled site-wide (nothing was ever expected),
  ``None`` when the interval has no fixes at all, ``Good`` at 90 % coverage or better,
  ``Gaps`` otherwise.
* **Stop** — a maximal run of consecutive fixes that all sit within ``radius_m`` of the
  run's centroid and that lasts at least ``min_minutes``. Lunch, a site, a supplier
  counter.
* **Dwell** — time between consecutive fixes that are *both* inside the site radius.
  Time is only credited to a site when the trail stayed there, so one fix drifting out and
  back does not break a two-hour dwell into two, but a fix pair straddling the edge is not
  counted either.

Every timestamp is a naive ``datetime`` in site-local time, which is what Frappe stores.
"""

from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6371000.0

HEALTH_OFF = "Off"
HEALTH_NONE = "None"
HEALTH_GOOD = "Good"
HEALTH_GAPS = "Gaps"
HEALTH_PENDING = "Pending"

#: Coverage at or above which an interval is ``Good``.
GOOD_COVERAGE_PCT = 90.0


# ---------------------------------------------------------------------------
# Geometry


def haversine_m(lat1, lng1, lat2, lng2):
	"""Great-circle distance between two WGS84 points, in meters.

	Moved here from ``api.time_kiosk._haversine_m`` (which imports it back) so that the
	stop / distance / dwell functions below have no reason to reach for ``frappe``.
	"""
	lat1, lng1, lat2, lng2 = map(radians, (_f(lat1), _f(lng1), _f(lat2), _f(lng2)))
	a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
	return EARTH_RADIUS_M * 2 * asin(sqrt(a))


def _f(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _minutes(delta):
	return delta.total_seconds() / 60.0


def _ts(point):
	return point["timestamp"] if isinstance(point, dict) else getattr(point, "timestamp")


def _lat(point):
	return _f(point["latitude"] if isinstance(point, dict) else getattr(point, "latitude"))


def _lng(point):
	return _f(point["longitude"] if isinstance(point, dict) else getattr(point, "longitude"))


# ---------------------------------------------------------------------------
# Coverage


def _boundaries(start, end, fix_times):
	"""``start``, the fixes inside the span in order, ``end`` — the points between which
	silences are measured. Fixes outside the span are dropped: a fix from the previous
	job says nothing about this one."""
	inside = sorted(t for t in (fix_times or []) if t is not None and start <= t <= end)
	return [start, *inside, end]


def gaps(start, end, fix_times, gap_minutes=15):
	"""Every silent stretch of ``[start, end]`` longer than ``gap_minutes``.

	Returns ``[{"from", "to", "minutes"}]`` oldest first. ``from``/``to`` are the two
	boundaries either side of the silence — real fixes, or the span's own start/end for a
	leading/trailing gap — which is exactly what the timeline needs to draw the dashed
	segment. No fixes at all yields one gap covering the whole span.
	"""
	if start is None or end is None or end <= start:
		return []
	if _minutes(end - start) < 1:
		return []

	limit = timedelta(minutes=max(_f(gap_minutes), 0))
	found = []
	points = _boundaries(start, end, fix_times)
	for earlier, later in zip(points, points[1:]):
		silence = later - earlier
		if silence > limit:
			found.append({"from": earlier, "to": later, "minutes": round(_minutes(silence), 1)})
	return found


def compute_health(start, end, fix_times, gap_minutes=15, tracking_enabled=True):
	"""Score an interval's trail. See the module docstring for the definitions.

	Returns ``{"fix_count", "last_fix_at", "gap_minutes", "coverage_pct", "health"}``.
	``gap_minutes`` in the *result* is the uncovered total (the input of the same name is
	the threshold); the two share a name because the settings field does.
	"""
	fixes = [t for t in (fix_times or []) if t is not None]
	result = {
		"fix_count": len(fixes),
		"last_fix_at": max(fixes) if fixes else None,
		"gap_minutes": 0.0,
		"coverage_pct": 100.0,
		"health": HEALTH_GOOD,
	}

	if not tracking_enabled:
		result["health"] = HEALTH_OFF
		return result

	if start is None or end is None or end <= start or _minutes(end - start) < 1:
		# Nothing to be missing from. A no-fix zero-length span still reports None so
		# a clock-in/clock-out pair seconds apart with no trail is not called Good.
		result["health"] = HEALTH_GOOD if fixes else HEALTH_NONE
		return result

	span_minutes = _minutes(end - start)
	uncovered = sum(g["minutes"] for g in gaps(start, end, fixes, gap_minutes))
	uncovered = min(uncovered, span_minutes)
	coverage = max(0.0, min(100.0, 100.0 * (span_minutes - uncovered) / span_minutes))

	result["gap_minutes"] = round(uncovered, 1)
	result["coverage_pct"] = round(coverage, 1)

	inside = [t for t in fixes if start <= t <= end]
	if not inside:
		result["health"] = HEALTH_NONE
	elif coverage >= GOOD_COVERAGE_PCT:
		result["health"] = HEALTH_GOOD
	else:
		result["health"] = HEALTH_GAPS
	return result


# ---------------------------------------------------------------------------
# Trail geometry


def path_distance_m(points):
	"""Sum of the great-circle legs between consecutive points, in meters."""
	total = 0.0
	previous = None
	for point in points or []:
		if previous is not None:
			total += haversine_m(_lat(previous), _lng(previous), _lat(point), _lng(point))
		previous = point
	return total


def _centroid(run):
	n = float(len(run))
	return sum(_lat(p) for p in run) / n, sum(_lng(p) for p in run) / n


def _fits(run, radius_m):
	lat, lng = _centroid(run)
	return all(haversine_m(lat, lng, _lat(p), _lng(p)) <= radius_m for p in run)


def detect_stops(points, radius_m=40, min_minutes=5):
	"""Places the trail stayed put.

	``points`` are ``{"timestamp", "latitude", "longitude"}`` in chronological order. A stop
	is a maximal run of consecutive points that all lie within ``radius_m`` of the run's own
	centroid and that spans at least ``min_minutes`` → ``{"lat", "lng", "from", "to",
	"minutes"}``. The centroid is re-checked against every member each time the run grows,
	so a run cannot creep across a car park one fix at a time.
	"""
	points = list(points or [])
	if not points:
		return []
	radius = max(_f(radius_m), 0.0)
	minimum = timedelta(minutes=max(_f(min_minutes), 0.0))

	stops = []
	run = [points[0]]

	def close(current):
		if len(current) < 2:
			return
		duration = _ts(current[-1]) - _ts(current[0])
		if duration >= minimum and duration.total_seconds() > 0:
			lat, lng = _centroid(current)
			stops.append({
				"lat": round(lat, 6),
				"lng": round(lng, 6),
				"from": _ts(current[0]),
				"to": _ts(current[-1]),
				"minutes": round(_minutes(duration), 1),
			})

	for point in points[1:]:
		candidate = [*run, point]
		if _fits(candidate, radius):
			run = candidate
		else:
			close(run)
			run = [point]
	close(run)
	return stops


def dwell_minutes(points, site_lat, site_lng, radius_m):
	"""Minutes between consecutive points that are both inside the site radius."""
	radius = _f(radius_m)
	if radius <= 0 or site_lat is None or site_lng is None:
		return 0.0
	total = 0.0
	previous = None
	previous_inside = False
	for point in points or []:
		inside = haversine_m(site_lat, site_lng, _lat(point), _lng(point)) <= radius
		if previous is not None and inside and previous_inside:
			total += _minutes(_ts(point) - _ts(previous))
		previous, previous_inside = point, inside
	return round(max(total, 0.0), 1)


def travel_minutes(points, stops=None, site_lat=None, site_lng=None, radius_m=0):
	"""Minutes the trail was moving: consecutive-fix pairs that are neither both on
	site nor inside a detected stop. The complement of dwell + stops, measured on the
	same pairs so the three never add up to more than the trail's own span."""
	radius = _f(radius_m)
	windows = [(s["from"], s["to"]) for s in (stops or [])]
	total = 0.0
	previous = None
	previous_inside = False
	for point in points or []:
		inside = False
		if radius > 0 and site_lat is not None and site_lng is not None:
			inside = haversine_m(site_lat, site_lng, _lat(point), _lng(point)) <= radius
		if previous is not None:
			a, b = _ts(previous), _ts(point)
			stationary = (inside and previous_inside) or any(w0 <= a and b <= w1 for w0, w1 in windows)
			if not stationary:
				total += _minutes(b - a)
		previous, previous_inside = point, inside
	return round(max(total, 0.0), 1)


def as_datetime(value):
	"""Coerce a Frappe datetime string or datetime to ``datetime`` without ``frappe``."""
	if value is None or isinstance(value, datetime):
		return value
	text = str(value).strip()
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
		try:
			return datetime.strptime(text, fmt)
		except ValueError:
			continue
	raise ValueError(f"not a datetime: {value!r}")

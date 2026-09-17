# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The weekly regular / overtime split. Pure functions, no ``frappe``.

Utah has no daily overtime: hours past the weekly threshold (40 by default,
``Time Kiosk Settings.overtime_weekly_hours``) in a fixed workweek are overtime, and that is
the entire rule. The workweek starts on the day ``overtime_week_start`` names (Sunday by
default — the FLSA workweek is any fixed, recurring 168 hours, and Sunday is what the
firm's own sheet assumes).

Two things this deliberately does **not** do:

* **It does not compute pay.** ``payroll_export`` writes the split into the provider's
  ``Regular Hours`` / ``Overtime Hours`` columns; ``Qualified OT`` — the federal figure —
  stays blank and stays the firm's, exactly as before (see that module's docstring for why
  reimplementing premium arithmetic is the worst trade available).
* **It does not split an interval across midnight.** An interval is credited whole to the
  day it *starts*, which is what ``payroll_export.worked_hours`` has always done and what the
  manual sheet did before it. An overnight callout that starts Saturday at 22:00 is
  Saturday's hours.

The period being exported is usually not aligned to workweeks (semi-monthly against a
Sunday week), so ``split_hours`` takes the whole overlapping workweeks and counts only the
intervals that *start* inside the period. Hours worked before the period in the same week
still push the threshold: 38 hours Sunday–Thursday before the 16th followed by 10 on the
16th is 2 regular and 8 overtime on the 16th, not 10 regular.

Bench-free suite: ``tests/test_workforce_overtime.py``.
"""

from datetime import date, datetime, timedelta

WEEK_STARTS = ("Sunday", "Monday")


def workweek_start(day, week_start="Sunday"):
	"""The date the workweek containing ``day`` began.

	``date.weekday()`` is Monday = 0 … Sunday = 6, so a Sunday-start week is
	``(weekday + 1) % 7`` days back and a Monday-start week is ``weekday`` days back.
	"""
	if isinstance(day, datetime):
		day = day.date()
	if week_start == "Monday":
		back = day.weekday()
	else:
		back = (day.weekday() + 1) % 7
	return day - timedelta(days=back)


def interval_hours(interval):
	"""Net hours of one interval: span minus paused, never negative."""
	start, end = interval.get("start"), interval.get("end")
	if not start or not end:
		return 0.0
	seconds = (end - start).total_seconds() - float(interval.get("paused_seconds") or 0)
	return max(seconds, 0.0) / 3600.0


def split_hours(intervals, period_from, period_to, week_start="Sunday", weekly_hours=40.0):
	"""Regular and overtime hours for ONE employee over ``[period_from, period_to]``.

	``intervals`` is ``[{"start": datetime, "end": datetime, "paused_seconds": float}]`` and
	may — should — include intervals outside the period from the same workweeks. Within
	each workweek hours accumulate chronologically; once the running total passes
	``weekly_hours`` the remainder is overtime, and the interval that crosses the line is
	split at it. Only intervals that START inside the period are counted in the result.

	Returns ``{"regular_hours", "overtime_hours"}``, both rounded to two places.
	"""
	period_from = _as_date(period_from)
	period_to = _as_date(period_to)
	threshold = max(float(weekly_hours or 0), 0.0)

	by_week = {}
	for interval in intervals or []:
		start = interval.get("start")
		if not start:
			continue
		by_week.setdefault(workweek_start(start, week_start), []).append(interval)

	regular = 0.0
	overtime = 0.0
	for week_intervals in by_week.values():
		running = 0.0
		for interval in sorted(week_intervals, key=lambda i: i["start"]):
			hours = interval_hours(interval)
			room = max(threshold - running, 0.0) if threshold > 0 else hours
			reg = min(hours, room)
			ot = hours - reg
			running += hours
			if period_from <= _as_date(interval["start"]) <= period_to:
				regular += reg
				overtime += ot

	return {"regular_hours": round(regular, 2), "overtime_hours": round(overtime, 2)}


def _as_date(value):
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	return date.fromisoformat(str(value)[:10])

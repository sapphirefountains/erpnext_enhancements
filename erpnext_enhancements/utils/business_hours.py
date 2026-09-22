"""Business-hours arithmetic: "one working hour after this Lead arrived".

``working_days.add_working_days`` counts whole days and keeps the time of day, which
is right for a two-day hand-off SLA and wrong for a one-hour speed-to-lead SLA: a
website enquiry at 16:30 on a Friday is not due at 17:30 on Friday, when nobody is
in, but at 08:30 on Monday. This module counts minutes, and only minutes that fall
inside the working day.

Standard library only, so it is testable with no bench; the caller supplies the
holiday test (``working_days._holiday_checker``) and the site-local ``now``. All
datetimes are naive and site-local, which is how Frappe stores them.
"""

import datetime

#: Monday=0 ... Friday=4.
WORKDAYS = frozenset(range(5))

#: A holiday list that marks every day a holiday must not spin forever.
MAX_DAYS_SCANNED = 400


def _never(_date):
	return False


def is_business_day(day, is_holiday=_never):
	return day.weekday() in WORKDAYS and not is_holiday(day)


def _next_open(cursor, day_start, day_end, is_holiday):
	"""The first business moment at or after ``cursor``."""
	for _ in range(MAX_DAYS_SCANNED):
		day = cursor.date()
		if is_business_day(day, is_holiday):
			opens = datetime.datetime.combine(day, day_start)
			closes = datetime.datetime.combine(day, day_end)
			if cursor < opens:
				return opens
			if cursor < closes:
				return cursor
		cursor = datetime.datetime.combine(day + datetime.timedelta(days=1), day_start)
	raise ValueError("no business day found within MAX_DAYS_SCANNED; check the holiday list")


def add_business_minutes(start, minutes, day_start, day_end, is_holiday=_never):
	"""``start`` plus ``minutes`` of working time.

	Working time is Monday to Friday, ``day_start`` to ``day_end``, excluding any date
	``is_holiday`` returns True for. A ``start`` outside working time counts from the
	next opening, so the clock never runs overnight or across a weekend. A deadline
	that lands exactly on closing time is returned as closing time, not rolled to the
	next morning.

	``minutes <= 0`` returns ``start`` unchanged. Raises ``ValueError`` when the day
	has no length -- the caller falls back to its defaults rather than guessing.
	"""
	if day_end <= day_start:
		raise ValueError(f"business day must end after it starts ({day_start} -> {day_end})")
	remaining = int(minutes or 0)
	if remaining <= 0:
		return start

	cursor = _next_open(start, day_start, day_end, is_holiday)
	for _ in range(MAX_DAYS_SCANNED):
		closes = datetime.datetime.combine(cursor.date(), day_end)
		available = int((closes - cursor).total_seconds() // 60)
		if remaining <= available:
			return cursor + datetime.timedelta(minutes=remaining)
		remaining -= available
		cursor = _next_open(closes, day_start, day_end, is_holiday)
	raise ValueError("deadline beyond MAX_DAYS_SCANNED; check the minutes and the holiday list")

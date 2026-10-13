"""Checklist lines done on set weekdays (v1.618.0).

A maintenance agreement can name a weekly schedule: Valley Fair Mall is visited every day,
and its Exhibit A puts the splash pad nozzle filters on Monday, the cartridge filters on
Tuesday and Friday, the bowl fountain on Wednesday and so on. Before this a template could
only list every line on every visit, so a daily site's form carried eight "(weekly)" tasks
the technician marked N/A six days out of seven, and nothing said which day each was due.

A Section Item's ``days`` names the weekdays its line is done ("Tue, Fri"); blank means every
visit. ``get_visit_payload`` leaves a line off a visit on any other day. The day is the visit's
own (``checklist_day``): the date the work was done, else the day it was scheduled, else today.

Imports only ``frappe`` (lazily), so bench-free suites can load it whole.
"""

#: Canonical spellings, Monday first, as ``date.weekday()`` numbers them.
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_ALIASES = {
	"mon": 0,
	"monday": 0,
	"tue": 1,
	"tues": 1,
	"tuesday": 1,
	"wed": 2,
	"weds": 2,
	"wednesday": 2,
	"thu": 3,
	"thur": 3,
	"thurs": 3,
	"thursday": 3,
	"fri": 4,
	"friday": 4,
	"sat": 5,
	"saturday": 5,
	"sun": 6,
	"sunday": 6,
}


#: Words for several days at once.
_GROUPS = {
	"weekdays": (0, 1, 2, 3, 4),
	"weekends": (5, 6),
	"weekend": (5, 6),
	"daily": (0, 1, 2, 3, 4, 5, 6),
	"everyday": (0, 1, 2, 3, 4, 5, 6),
}


def _day(word):
	if word not in _ALIASES:
		raise ValueError(word)
	return _ALIASES[word]


def parse_days(text):
	"""The weekdays ``text`` names, as sorted ``date.weekday()`` numbers.

	Commas, slashes, "&", "and" and spaces all separate ("Tue & Fri", "Mon/Thu"). A range is
	"Mon-Fri" or "Mon to Fri" (it may wrap: "Sat-Mon"), and "weekdays", "weekends" and "daily"
	are understood. Blank returns ``()``: every visit.

	Raises:
		ValueError: with the word it could not read, so the Section form can say which.
	"""
	text = (text or "").lower().replace("every day", "daily")
	for separator in (",", "/", "&", ";"):
		text = text.replace(separator, " ")
	# A range keeps its ends together: "mon - fri" and "mon to fri" become "mon-fri".
	text = " ".join(text.split()).replace(" - ", "-").replace(" to ", "-")
	days = set()
	for word in text.split():
		word = word.strip(".")
		if not word or word == "and":
			continue
		if word in _GROUPS:
			days.update(_GROUPS[word])
		elif "-" in word:
			first, _, last = word.partition("-")
			start, end = _day(first.strip(".")), _day(last.strip("."))
			days.update((start + step) % 7 for step in range((end - start) % 7 + 1))
		else:
			days.add(_day(word))
	return tuple(sorted(days))


def normalize_days(text):
	"""``text`` as the Section stores it, "Tue, Fri"; "" for every visit. Raises like parse_days.

	All seven days is every visit, so it is stored blank.
	"""
	days = parse_days(text)
	if len(days) == 7:
		return ""
	return ", ".join(DAY_NAMES[day] for day in days)


def scheduled_on(days, day):
	"""Whether a line with ``days`` belongs on a visit on ``day`` (a date).

	Blank is every visit. A value that cannot be read keeps the line: a typo on a Section
	saved before the check existed must not quietly drop work from a visit. So does a day
	that is not a date (``getdate`` returns None for "0000-00-00").
	"""
	if not (days or "").strip() or day is None:
		return True
	try:
		wanted = parse_days(days)
	except ValueError:
		return True
	return not wanted or day.weekday() in wanted


def checklist_day(visit_date=None, scheduled_visit_date=None):
	"""The day a visit's checklist is built for: done on, else scheduled for, else today.

	Not ``visit_compare.visit_day``, which takes a visit record.
	"""
	from frappe.utils import getdate

	# getdate() with nothing is today, in the site's time zone.
	return getdate(visit_date or scheduled_visit_date or None)

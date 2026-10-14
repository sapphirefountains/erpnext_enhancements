"""Checklist lines done on set weekdays (v1.619.0).

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
DAY_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
#: The field holding a checklist row's label, per visit table (consumables carry an item instead).
LABEL_FIELDS = {"cleaning_tasks": "task", "maintenance_results": "question", "chemistry_readings": "reading"}

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
	if day is None:
		return True
	return scheduled_on_weekday(days, day.weekday())


def scheduled_on_weekday(days, weekday):
	"""``scheduled_on`` for a weekday number (0 is Monday) rather than a date."""
	if not (days or "").strip():
		return True
	try:
		wanted = parse_days(days)
	except ValueError:
		return True
	return not wanted or weekday in wanted


def _norm(text):
	return (text or "").strip().lower()


def review_lines(doc):
	"""The visit's day-specific lines, for the Visit Review page (v1.678.0).

	A visit's checklist is built once, on its first open, for the day it was scheduled or done
	(``checklist_day``). A reviewer could not tell which lines were there because of the day,
	nor see that a visit done a day late carried the wrong day's lines. Against the day the
	visit was done (else scheduled), each line of its template's sections that names days is
	one of: on the visit and due that day; due that day but missing; or on the visit but set
	for other days.

	Matched by section and label, across water features, so a line on one fountain's rows counts
	for the site. Chemical Dosing is left out: its lines become consumables, which carry an item,
	not a label. Sections come from the visit's rows and its ``template``, the first one resolved,
	so on a site visit whose fountains use different templates a section with no rows at all on
	the visit is not looked at. Returns None when nothing on those sections names a day.
	"""
	import frappe

	rows = [(table, row) for table in LABEL_FIELDS for row in doc.get(table) or []]
	sections = {row.get("section") for _table, row in rows if row.get("section")}
	if doc.get("template"):
		sections.update(
			frappe.get_all(
				"Sapphire Template Section",
				filters={"parent": doc.template, "parenttype": "Sapphire Maintenance Template"},
				pluck="section",
			)
		)
	sections.discard(None)
	if sections:
		# Only the sections a checklist is built from (get_visit_payload skips a disabled one), and
		# not Chemical Dosing: its lines become consumables, which this cannot match by label.
		sections = frappe.get_all(
			"Sapphire Maintenance Section",
			filters={
				"name": ["in", sorted(sections)],
				"disabled": 0,
				"section_type": ["!=", "Chemical Dosing"],
			},
			pluck="name",
		)
	if not sections:
		return None
	items = frappe.get_all(
		"Sapphire Section Item",
		filters={
			"parent": ["in", sorted(sections)],
			"parenttype": "Sapphire Maintenance Section",
			"days": ["is", "set"],
		},
		fields=["parent", "label", "days", "sequence", "idx"],
		order_by="parent asc, idx asc",
	)
	if not items:
		return None
	day = checklist_day(doc.get("visit_date"), doc.get("scheduled_visit_date"))
	present = {(row.get("section"), _norm(row.get(LABEL_FIELDS[table]))) for table, row in rows}
	on_visit, missing, other_days = [], [], []
	for item in items:
		line = {"label": item.label, "days": item.days}
		due = scheduled_on(item.days, day)
		if (item.parent, _norm(item.label)) in present:
			(on_visit if due else other_days).append(line)
		elif due:
			missing.append(line)
	return {"day": DAY_LONG[day.weekday()], "on_visit": on_visit, "missing": missing, "other_days": other_days}


def checklist_day(visit_date=None, scheduled_visit_date=None):
	"""The day a visit's checklist is built for: done on, else scheduled for, else today.

	Not ``visit_compare.visit_day``, which takes a visit record.
	"""
	from frappe.utils import getdate

	# getdate() with nothing is today, in the site's time zone.
	return getdate(visit_date or scheduled_visit_date or None)

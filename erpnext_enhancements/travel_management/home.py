# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Travel hub's "My Travel" block: one answer to "where is MY information?".

The owner's brief (2026-09-28): the Travel workspace has to work for someone who has no idea
where to go. Two travelers on the one live trip opened it looking for their trip and found a
calendar, a planner and three reports. What that person needs at the top of the page is
about *them*, and a workspace cannot say it:

* **their trip** — the one they are on now, else the next one — with its dates in words,
  "day 2 of 6" or "Starts in 5 days", and one tap to their itinerary, their own trip sheet,
  their documents and who to call;
* **their other trips**, and the ones they booked for a crew they are not on;
* **what is still theirs to do**: receipts, in the week after getting back;
* for a coordinator, **what needs attention** across every trip, and the settings that leave
  travelers without an email or a number to call.

WHY A BLOCK AND ONE ENDPOINT. The same reason as the My Training block
(``training/dashboard.py``): a workspace shortcut, card or quick list carries one static
filter and draws the same thing for every viewer, so "your trip" cannot be one. The block is
a Custom HTML Block (``custom_html_blocks/travel_home.*``) that makes this one call and draws
what comes back. Everything it shows is decided here: which trip is current, how many days
until it starts, whether receipts are due, what counts as needing attention. A date worked
out in the browser is the browser's day, not the site's, and a rule written twice drifts
(``training/dashboard.py`` records two that did). The strings are ready to show and the URLs
ready to open; the block computes no predicates and no dates.

NOTHING HERE IS DEFINED AGAIN. The trips a person travels on and the ones they own are the
queries ``/itinerary`` uses (``api.travel._itinerary_trips``), with a two-week window where
that page keeps one: "recent" on the hub is a fortnight, receipts a week. Who is a
coordinator, who to call, which files a person sees and whether the Trip Sheet exists come
from ``api.travel``; the checklist from ``completeness``; every link from ``views``; the date
span is the Trip Sheet's (``itinerary_text.pretty_date``). ``api.travel`` is imported late,
as ``planner._viewer`` does: it needs far more of frappe than this module's callers always
have.

ALL BUT MONEY, AND THE HUB SHOWS NONE. Crew see everything about a trip except money
(owner's rule, 2026-09-26), and nothing in this answer is money for anyone: no cost, who
paid, per diem, claim or mileage figure, and no receipt. The contacts are the card
``/itinerary`` shows (``api.travel._trip_contacts``, which never returns a crew member's own
details). The "Needs attention" list is a coordinator's only — its "things missing" count
includes the checklist's cost gaps, and "no cost entered" is itself a fact about money — and
it is built only after ``_is_coordinator`` says so: for anyone else ``attention`` is null and
none of its queries runs.

COST. The trip lists are two ``frappe.get_list`` calls (so the Travel Trip permission hooks
decide, not this module) and one ``Trip Traveler`` lookup of the viewer's own Employee. Only
the featured trip is loaded whole; a coordinator's checklist loads at most
``ATTENTION_DOCS`` active trips, soonest first.

NEVER RAISES FOR A VIEWER. Every part is guarded: a trip whose contacts, files or checklist
cannot be read loses that part, with an Error Log, and the rest of the hub still draws. A
hub that will not load is exactly the dead end it exists to remove.
"""

from collections import Counter

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from erpnext_enhancements.travel_management import views as trip_views
from erpnext_enhancements.travel_management.completeness import counted_gaps, files_not_attached, find_gaps
from erpnext_enhancements.travel_management.itinerary_text import pretty_date

#: A trip still ahead or under way (Plan a Trip's "Carry on with a trip").
ACTIVE_STATUSES = ("Planning", "Booked", "In Progress")

#: How long a finished trip stays under "Your other trips". ``/itinerary`` keeps a week.
RECENT_DAYS = 14

#: Receipts are due within a week of getting back (the travel guidelines, section 6), so the
#: reminder shows from the day after a trip ends to the seventh.
RECEIPTS_DAYS = 7

#: A trip still Planning this close to its start needs someone to book it.
SOON_DAYS = 14

#: The coordinator's list: at most this many rows, "and N more" beyond them.
ATTENTION_ROWS = 20

#: Active trips loaded whole for their checklist, soonest first. Each is a full document.
ATTENTION_DOCS = 30

#: How many rows a coordinator's trip queries read at most.
ATTENTION_SCAN = 200

#: The most trips one person's lists read (``api.travel._itinerary_trips``'s own limit).
MAX_TRIPS = 100

#: The travel guidelines. Section 6 ("Expense Reimbursement and Receipts") has no anchor.
GUIDELINES_URL = "/travel_guidelines"

#: Where both setup notes are fixed: Travel Settings' desk route.
SETTINGS_ROUTE = "travel-settings"

#: The contacts under "Who to call", in the order the hub lists them. ``views.contact_list``
#: names each row's role with one of these; 911 comes last, after the people who know the trip.
CONTACT_ORDER = ("Travel desk", "Trip lead", "Booked by", "Job site", "Hotel", "Emergency")

#: The columns every list here is built from. No money column.
_TRIP_FIELDS = ["name", "purpose", "status", "start_date", "end_date", "owner"]


def _travel():
	"""``api.travel``, imported late (see the module docstring)."""
	from erpnext_enhancements.api import travel

	return travel


def _attempt(what, build, fallback=None):
	"""``build()``, or ``fallback`` with an Error Log when it raises: one part of the hub that
	cannot be read never takes the rest with it."""
	try:
		return build()
	except Exception:
		frappe.log_error(title="Travel home", message=f"{what}\n{frappe.get_traceback()}")
		return fallback


def _day(value):
	try:
		return getdate(value) if value else None
	except Exception:
		return None


def _dates(start, end):
	"""'Sun Sep 27 – Fri Oct 2', or one day for a one-day trip: the Trip Sheet's span."""
	first, last = (pretty_date(day) if day else "" for day in (start, end))
	return f"{first} – {last}" if first and last and first != last else first or last


def _starts(days):
	if days <= 0:
		return _("Starts today")
	if days == 1:
		return _("Starts tomorrow")
	return _("Starts in {0} days").format(days)


def _ended(end, today_date):
	"""How long ago a trip ended; "Finished" for one marked Completed before its last day."""
	days = (today_date - end).days if end else 0
	if days <= 0:
		return _("Finished")
	if days == 1:
		return _("Ended yesterday")
	return _("Ended {0} days ago").format(days)


def _headline(start, end, today_date):
	"""The featured card's one line: "You're on this trip now — day 2 of 6", "Starts tomorrow"."""
	if not start or not end:
		return ""
	if start <= today_date <= end:
		total = (end - start).days + 1
		if total <= 1:
			return _("You're on this trip today")
		return _("You're on this trip now — day {0} of {1}").format((today_date - start).days + 1, total)
	return _starts((start - today_date).days)


def _is_ahead(row, today_date):
	"""Still ahead or under way: an active status and not over. A trip marked Completed before
	its last day is over."""
	end = _day(row.get("end_date"))
	return row.get("status") in ACTIVE_STATUSES and end is not None and end >= today_date


def _first_name(user):
	try:
		return str(frappe.db.get_value("User", user, "first_name") or "").strip()
	except Exception:
		return ""


def _office(contact):
	"""The Travel Desk (``api.travel._office_contact``) with its links, or None until it has a
	phone or an email."""
	if not contact:
		return None
	return {
		"label": contact.get("label"),
		"phone": contact.get("phone"),
		"phone_href": trip_views.tel_href(contact.get("phone")),
		"email": contact.get("email"),
		"email_href": trip_views.mailto_href(contact.get("email")),
	}


# --------------------------------------------------------------------------- the viewer's trips


def _my_trips(user, employee, since):
	"""The trips this person travels on, then the ones they own and are not on — the two lists
	``/itinerary`` chips are built from (``api.travel._itinerary_trips``), not Closed and ended
	no earlier than ``since``. Each row gains ``relation``: "traveling" or "organizing".

	Both trip queries are ``frappe.get_list``, so the Travel Trip permission hooks decide. The
	``Trip Traveler`` lookup is the viewer's own Employee's rows only (``get_my_trips`` does the
	same); its trip names still go through ``get_list``. Someone with no read on Travel Trip at
	all gets nothing and no query: ``get_list`` raises for them in v16 rather than returning
	an empty list, and ``has_permission`` with no document queues no message."""
	if not frappe.has_permission("Travel Trip", "read"):
		return []
	common = {"status": ["!=", "Closed"], "end_date": [">=", since]}
	traveling = []
	if employee:
		names = frappe.get_all(
			"Trip Traveler",
			filters={"parenttype": "Travel Trip", "employee": employee},
			pluck="parent",
		)
		if names:
			traveling = frappe.get_list(
				"Travel Trip",
				filters={"name": ["in", sorted(set(names))], **common},
				fields=_TRIP_FIELDS,
				order_by="start_date asc",
				limit_page_length=MAX_TRIPS,
			)
	owned = frappe.get_list(
		"Travel Trip",
		filters={"owner": user, **common},
		fields=_TRIP_FIELDS,
		order_by="start_date asc",
		limit_page_length=MAX_TRIPS,
	)
	trips, seen = [], set()
	for row, relation in [(row, "traveling") for row in traveling] + [(row, "organizing") for row in owned]:
		if row.get("name") in seen:
			continue
		seen.add(row.get("name"))
		trips.append(dict(row, relation=relation))
	return trips


def _featured_row(trips, today_date):
	"""The trip on the card: among the ones this person travels on (never one they only
	organize), the one happening now, else the next — ``/itinerary``'s ``defaultTrip`` rule."""
	pool = sorted(
		(row for row in trips if row["relation"] == "traveling" and _is_ahead(row, today_date)),
		key=lambda row: str(row.get("start_date") or ""),
	)
	current = next((row for row in pool if _has_started(row, today_date)), None)
	return current or (pool[0] if pool else None)


def _has_started(row, today_date):
	start = _day(row.get("start_date"))
	return start is not None and start <= today_date


def _contact_rows(contacts):
	"""The card's "Who to call": ``views.contact_list``'s rows — the Trip Sheet's — in
	:data:`CONTACT_ORDER`, each with its ``tel:`` and ``mailto:`` link. ``detail`` is what else the row says (the job's
	name, a street address); ``links`` are a hotel's nearest urgent care and directions, and
	directions to the job site."""
	rank = {role: index for index, role in enumerate(CONTACT_ORDER)}
	rows = sorted(trip_views.contact_list(contacts), key=lambda row: rank.get(row["role"], len(rank)))
	return [
		{
			"label": _(row["role"]),
			"name": row["name"] or None,
			"detail": " · ".join(part for part in (row["detail"], row["address"]) if part) or None,
			"phone": row["phone"],
			"phone_href": row["tel"],
			"email": row["email"],
			"email_href": trip_views.mailto_href(row["email"]),
			"links": [{"label": _(label), "url": url} for url, label in row["links"]],
		}
		for row in rows
	]


def _featured(row, employee, today_date):
	"""The featured card. The trip's links are built from its name alone, so the card draws
	even when the trip itself cannot be read; its title, files and contacts come from the one
	trip loaded whole here, each part on its own."""
	travel = _travel()
	name = row["name"]
	start, end = _day(row.get("start_date")), _day(row.get("end_date"))
	itinerary_url = trip_views.itinerary_path(name)
	card = {
		"trip": name,
		"purpose": row.get("purpose") or name,
		"status": row.get("status"),
		"dates": _dates(start, end),
		"headline": _headline(start, end, today_date),
		"travel_for": None,
		"itinerary_url": itinerary_url,
		"docs_url": f"{itinerary_url}&view=docs",
		"sheet_url": None,
		"documents": 0,
		"contacts": [],
	}
	doc = _attempt(f"{name}: load", lambda: frappe.get_doc("Travel Trip", name))
	if doc is None or not frappe.has_permission("Travel Trip", "read", doc=doc):
		return card
	card["travel_for"] = (
		_attempt(f"{name}: what it is for", lambda: travel._site_title(doc))
		or doc.get("travel_for_name")
		or None
	)
	# Their own sheet (``&as=``), and only while the format exists: a missing Trip Sheet prints
	# as Standard, costs included (``api.travel._sheet_available``).
	if _attempt(f"{name}: trip sheet", travel._sheet_available, False):
		card["sheet_url"] = trip_views.trip_sheet_url(name, employee)
	names = {t.employee: t.employee_name or t.employee for t in doc.get("travelers") or [] if t.employee}
	# The count on /itinerary's "Documents (N)" tab: the files this person sees, receipts never.
	card["documents"] = _attempt(
		f"{name}: documents",
		lambda: sum(len(files) for files in travel._trip_files(doc, employee, names)[0].values()),
		0,
	)
	card["contacts"] = _attempt(
		f"{name}: contacts",
		lambda: _contact_rows(travel._trip_contacts(doc, employee, hotels=travel._hotel_details(doc, {}))),
		[],
	)
	return card


def _trip_item(row, user, is_coordinator, today_date):
	"""One line under "Your other trips"."""
	start, end = _day(row.get("start_date")), _day(row.get("end_date"))
	ahead = _is_ahead(row, today_date)
	organizing = row["relation"] == "organizing"
	if ahead and organizing:
		note = _("You're organizing this")
	elif ahead and _has_started(row, today_date):
		note = _("You're on this trip now")
	elif ahead:
		note = _starts((start - today_date).days) if start else None
	elif organizing:
		note = _("{0} — you organized it").format(_ended(end, today_date))
	else:
		note = _ended(end, today_date)
	return {
		"trip": row["name"],
		"purpose": row.get("purpose") or row["name"],
		"status": row.get("status"),
		"dates": _dates(start, end),
		"relation": row["relation"],
		"note": note,
		"itinerary_url": trip_views.itinerary_path(row["name"]),
		# "Keep planning" opens Plan a Trip at Review: for the person who organized it, or a
		# coordinator, while the trip is still ahead.
		"can_plan": bool(ahead and (is_coordinator or row.get("owner") == user)),
	}


def _other_trips(trips, featured_name, user, is_coordinator, today_date):
	"""Every trip but the featured one: ahead or under way first, soonest first; then the ones
	that ended, most recent first."""
	rest = [row for row in trips if row["name"] != featured_name]
	ahead = sorted(
		(row for row in rest if _is_ahead(row, today_date)), key=lambda row: str(row.get("start_date") or "")
	)
	ended = sorted(
		(row for row in rest if not _is_ahead(row, today_date)),
		key=lambda row: str(row.get("end_date") or ""),
		reverse=True,
	)
	return [_trip_item(row, user, is_coordinator, today_date) for row in ahead + ended]


def _receipts(trips, today_date):
	"""A reminder per trip this person traveled on that ended one to seven days ago, most recent
	first. A Closed trip is not listed at all: it is locked, and its receipts are settled."""
	due = []
	for row in trips:
		end = _day(row.get("end_date"))
		if row["relation"] != "traveling" or end is None:
			continue
		if 1 <= (today_date - end).days <= RECEIPTS_DAYS:
			due.append((end, row))
	due.sort(key=lambda pair: pair[0], reverse=True)
	return [
		{
			"trip": row["name"],
			"purpose": row.get("purpose") or row["name"],
			"text": _(
				"Back from {0}? Attach your receipts to the trip within a week of getting back, and accounting will reimburse you."
			).format(row.get("purpose") or row["name"]),
		}
		for _end, row in due
	]


# --------------------------------------------------------------------------- coordinators only


def _planning_reason(row, today_date):
	"""A trip still Planning that starts within :data:`SOON_DAYS`, or has started: nobody has
	booked it yet. The daily auto-advance moves a trip inside its dates to In Progress, so the
	second case lasts a day at most."""
	start = _day(row.get("start_date"))
	if row.get("status") != "Planning" or start is None:
		return []
	days = (start - today_date).days
	if days < 0:
		return [_("Under way and still Planning")]
	if days > SOON_DAYS:
		return []
	return [_("{0} and is still Planning").format(_starts(days))]


def _checklist_reasons(name):
	"""The trip checklist's two tallies, worded: every gap but paperwork (the cost gaps
	included — this list is a coordinator's), and the files not attached yet."""
	gaps = find_gaps(frappe.get_doc("Travel Trip", name))
	reasons = []
	missing = len(counted_gaps(gaps))
	if missing:
		reasons.append(_("1 thing missing") if missing == 1 else _("{0} things missing").format(missing))
	files = files_not_attached(gaps)
	if files:
		reasons.append(_("1 file not attached") if files == 1 else _("{0} files not attached").format(files))
	return reasons


def _failed_alerts():
	"""``{trip: how many of its change alerts failed to send}``."""
	rows = frappe.get_all(
		"Trip Change Alert", filters={"status": "Failed"}, fields=["trip"], limit_page_length=ATTENTION_SCAN
	)
	return Counter(row.get("trip") for row in rows if row.get("trip"))


def _setup_notes(office):
	"""What leaves travelers without an email or a number to call. Change alerts being off is
	deliberate (they need the emails on, and the office has not switched them on), so it is not
	a note."""
	from erpnext_enhancements.travel_management.notifications import _notifications_enabled

	notes = []
	if not office:
		notes.append(
			{
				"text": _("Add a Travel Desk phone or email so travelers know who to call"),
				"route": SETTINGS_ROUTE,
			}
		)
	if not _attempt("travel emails", _notifications_enabled, True):
		notes.append({"text": _("Travel emails are off"), "route": SETTINGS_ROUTE})
	return notes


def _attention(today_date, office):
	"""A coordinator's "Needs attention": every trip that needs someone, with why.

	Active trips (Planning, Booked, In Progress, not over), soonest first: still Planning close
	to the start, things missing, files not attached. Then trips with a change alert that
	failed to send, and trips Completed and waiting to be closed. A trip is listed once, with
	every reason; one with none is not listed. At most :data:`ATTENTION_ROWS` rows, and
	``more`` says how many were left off. ``target`` is where its row opens: Plan a Trip's
	Review step, or the form for a finished trip.

	``frappe.get_all``: the caller has already checked this is a coordinator, who sees every
	trip."""
	now = str(today_date)
	entries = {}

	def add(row, reasons):
		entry = entries.setdefault(row.get("name"), {"row": row, "reasons": []})
		entry["reasons"].extend(reasons)

	active = _attempt(
		"active trips",
		lambda: frappe.get_all(
			"Travel Trip",
			filters={"status": ["in", list(ACTIVE_STATUSES)], "end_date": [">=", now]},
			fields=_TRIP_FIELDS,
			order_by="start_date asc",
			limit_page_length=ATTENTION_SCAN,
		),
		[],
	)
	for index, row in enumerate(active):
		name = row.get("name")
		reasons = _planning_reason(row, today_date)
		if index < ATTENTION_DOCS:
			# Called at once, inside the loop: the lambda cannot see a later trip's name.
			reasons += _attempt(f"{name}: checklist", lambda: _checklist_reasons(name), [])
		add(row, reasons)

	failed = _attempt("failed change alerts", _failed_alerts, Counter())
	unseen = [trip for trip in failed if trip not in entries]
	if unseen:
		for row in _attempt(
			"trips with failed change alerts",
			lambda: frappe.get_all(
				"Travel Trip",
				filters={"name": ["in", unseen], "status": ["!=", "Closed"]},
				fields=_TRIP_FIELDS,
				order_by="start_date asc",
			),
			[],
		):
			add(row, [])
	for name, count in failed.items():
		if name in entries:
			entries[name]["reasons"].append(
				_("A change alert failed to send")
				if count == 1
				else _("{0} change alerts failed to send").format(count)
			)

	for row in _attempt(
		"completed trips",
		lambda: frappe.get_all(
			"Travel Trip",
			filters={"status": "Completed"},
			fields=_TRIP_FIELDS,
			order_by="end_date asc",
			limit_page_length=ATTENTION_SCAN,
		),
		[],
	):
		add(row, [_("Finished — ready to close")])

	items = []
	for name, entry in entries.items():
		if not entry["reasons"]:
			continue
		row = entry["row"]
		items.append(
			{
				"trip": name,
				"purpose": row.get("purpose") or name,
				"status": row.get("status"),
				"dates": _dates(_day(row.get("start_date")), _day(row.get("end_date"))),
				"reasons": entry["reasons"],
				"target": "form" if row.get("status") in ("Completed", "Closed") else "plan",
			}
		)
	return {
		"trips": items[:ATTENTION_ROWS],
		"more": max(0, len(items) - ATTENTION_ROWS),
		"setup": _attempt("setup notes", lambda: _setup_notes(office), []),
	}


# --------------------------------------------------------------------------- the endpoint


@frappe.whitelist()
def get_travel_home():
	"""The "My Travel" block's whole answer, for the session user:

	``{viewer, message, office, guidelines_url, featured, trips, receipts, attention}`` —
	``viewer`` is ``{user, employee, first_name, is_coordinator}``; ``message`` the sentence to
	show when the user has no Employee (else None); ``office`` the Travel Desk with its links,
	or None; ``featured`` the trip card or None; ``trips`` every other trip of theirs;
	``receipts`` what is due; ``attention`` a coordinator's list, None for anyone else. See the
	module docstring for what each holds and why none of it is money."""
	travel = _travel()
	user = frappe.session.user
	employee = _attempt("employee", travel._session_employee) or None
	is_coordinator = bool(_attempt("coordinator", travel._is_coordinator, False))
	today_date = getdate(today())
	office = _attempt("travel desk", travel._office_contact)

	trips = _attempt("trips", lambda: _my_trips(user, employee, add_days(today(), -RECENT_DAYS)), [])
	featured_row = _attempt("featured trip", lambda: _featured_row(trips, today_date))
	featured = (
		_attempt(f"{featured_row['name']}: featured", lambda: _featured(featured_row, employee, today_date))
		if featured_row
		else None
	)

	return {
		"viewer": {
			"user": user,
			"employee": employee,
			"first_name": _first_name(user),
			"is_coordinator": is_coordinator,
		},
		"message": None
		if employee
		else _(
			"Your user account isn't linked to an employee record, so your trips can't show here. "
			"Ask the office to link it."
		),
		"office": _office(office),
		"guidelines_url": GUIDELINES_URL,
		"featured": featured,
		"trips": _attempt(
			"other trips",
			lambda: _other_trips(trips, featured and featured["trip"], user, is_coordinator, today_date),
			[],
		),
		"receipts": _attempt("receipts", lambda: _receipts(trips, today_date), []),
		# Built only for a coordinator: nobody else's answer holds the list, or runs its queries.
		"attention": _attempt(
			"attention", lambda: _attention(today_date, office), {"trips": [], "more": 0, "setup": []}
		)
		if is_coordinator
		else None,
	}

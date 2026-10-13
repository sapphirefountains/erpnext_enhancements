# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who is off, and when: reading one event on the Company Holidays & Vacations calendar (C4).

Pure: no frappe import. ``time_off.py`` reads Google and ERPNext and calls :func:`classify`,
:func:`event_days` and :func:`diff_blocks`; ``tests/test_calendar_time_off.py`` runs them without a
bench.

**The rules, in the order they are tried** (Nik, 2026-10-12). The first one that answers wins.

1. **ERPNext's own events are never read.** An event carrying C2's private extended properties
   (``erpnext_site`` / ``erpnext_doctype``, any site's) or C2's "Managed by ERPNext" footer is the
   outbound copy of a Planner Block. Reading it back would import the block a second time and then
   push that out again: a loop.
2. **A deleted event** (``status`` cancelled) imports nothing: its blocks are removed.
3. **Birthdays are skipped**: Google's ``birthday`` event type, and any title with "birthday",
   "bday" or "b-day".
4. **A planner's decision for this event** from the review list (a person, everyone, or ignore).
5. **A planner's decision remembered for this title** ("do the same for every event with this
   title"). The title is compared without case, punctuation or numbers, so "Thanksgiving 2026" and
   "thanksgiving" are one title.
6. **"Company holiday"** in the title means everyone is off.
7. **A name in the title plus a time-off word.** A person's first name, full name, or an alias from
   their Planner Resource ("Names on the shared calendar"), as a whole word, with one of
   :data:`TIME_OFF_WORDS` ("off", "OOO", "out", "vacation", "PTO", "sick", "leave", ...). "Austin
   Off", "Clegg - ooo", "Austin's vacation", "Austin & Clegg out" all match. The longer name wins
   ("Austin Smith" over "Austin"), and a first name two people share is settled by a full name, an
   alias, or who created the event; otherwise it goes to review.
8. **No name, but a time-off word: whoever created the event** ("OOO", "Out sick"), by the creator's
   address (then the organizer's, unless the organizer is the calendar itself) → User → active
   Planner Resource.
9. **Everything else goes to the review list** with the reason and, where there is one, a best
   guess: a name without a time-off word ("Austin - Denver") is a guess, not a match, because the
   calendar also carries things like "Lunch with Austin".

A wrong automatic match is corrected where the truth lives, in Google: rename the event, or add an
alias to the person. The block in ERPNext is read-only.
"""

import datetime
import re

#: C2's private extended-property keys (``payload.PROP_SITE`` / ``PROP_DOCTYPE``). Literal here so
#: this module stays import-free; ``tests/test_calendar_time_off.py`` asserts they match.
ERPNEXT_PROPS = ("erpnext_site", "erpnext_doctype")
#: The start of C2's description footer (``payload.FOOTER``), a second sign of an ERPNext event.
ERPNEXT_FOOTER = "Managed by ERPNext"

#: Words that say "this person is not working", compared as whole words after :func:`normalize`.
TIME_OFF_WORDS = (
	"off",
	"day off",
	"days off",
	"time off",
	"half day",
	"ooo",
	"oof",
	"out",
	"out of office",
	"away",
	"vacation",
	"vacay",
	"pto",
	"sick",
	"leave",
	"on holiday",
	"appointment",
	"appt",
	"doctor",
	"dr",
	"dentist",
	"medical",
	"jury duty",
	"funeral",
	"bereavement",
	"personal day",
)
#: A title with one of these is a company-wide day off.
COMPANY_PHRASES = ("company holiday", "company holidays", "company day off")
#: A title with one of these is somebody's birthday, not time off.
BIRTHDAY_WORDS = ("birthday", "birthdays", "bday", "bdays", "b day")
BIRTHDAY_EVENT_TYPE = "birthday"

#: Outcomes of :func:`classify`.
SKIP = "skip"
IMPORT = "import"
REVIEW = "review"
GONE = "gone"

#: Why (``how``).
HOW_OURS = "ours"
HOW_CANCELLED = "cancelled"
HOW_BIRTHDAY = "birthday"
HOW_DECISION = "decision"
HOW_PATTERN = "pattern"
HOW_COMPANY = "company"
HOW_TITLE = "title"
HOW_CREATOR = "creator"
HOW_IGNORED = "ignored"
HOW_UNMATCHED = "unmatched"

#: The review list's decisions (``Calendar Time Off Review.status``).
STATUS_OPEN = "Open"
STATUS_ASSIGNED = "Assigned"
STATUS_EVERYONE = "Everyone"
STATUS_IGNORED = "Ignored"
DECIDED = (STATUS_ASSIGNED, STATUS_EVERYONE, STATUS_IGNORED)

#: One event never makes blocks for more days than this (a runaway end date).
MAX_DAYS = 366
DAY_MINUTES = 24 * 60
END_OF_DAY = "23:59:00"


# ---------------------------------------------------------------------- text


def normalize(text):
	"""Lower case, possessives dropped, every run of punctuation one space.

	``"Clegg - OOO"`` → ``"clegg ooo"``, ``"Austin's B-Day!"`` → ``"austin b day"``. Letters with
	accents are kept, so "José" still matches "José".
	"""
	text = str(text or "").lower().replace("’", "'").replace("‘", "'")
	text = re.sub(r"'s\b", "", text)
	text = re.sub(r"[\W_]+", " ", text)
	return " ".join(text.split())


def has_phrase(normalized, phrase):
	"""True when ``phrase`` (already normalized) is in ``normalized`` as whole words."""
	return bool(phrase) and f" {phrase} " in f" {normalized} "


def any_phrase(normalized, phrases):
	return any(has_phrase(normalized, phrase) for phrase in phrases)


def title_key(title):
	"""The title as the review list remembers it: normalized, numbers dropped."""
	return " ".join(token for token in normalize(title).split() if not token.isdigit())


def split_aliases(text):
	"""``"AJ, Mr. Smith\\nSmitty"`` → ``["aj", "mr smith", "smitty"]`` (normalized, no blanks)."""
	out = []
	for part in re.split(r"[,;\n]+", str(text or "")):
		value = normalize(part)
		if value and value not in out:
			out.append(value)
	return out


# ---------------------------------------------------------------------- what kind of event


def event_props(event):
	return (((event or {}).get("extendedProperties") or {}).get("private")) or {}


def is_erpnext_event(event):
	"""True for an event ERPNext wrote (any site's): C2's private properties or its footer."""
	props = event_props(event)
	if any(props.get(key) for key in ERPNEXT_PROPS):
		return True
	return ERPNEXT_FOOTER in str((event or {}).get("description") or "")


def is_birthday(event):
	if str((event or {}).get("eventType") or "") == BIRTHDAY_EVENT_TYPE:
		return True
	return any_phrase(normalize((event or {}).get("summary")), BIRTHDAY_WORDS)


def is_company_holiday(title):
	return any_phrase(normalize(title), COMPANY_PHRASES)


def has_time_off_word(title):
	return any_phrase(normalize(title), TIME_OFF_WORDS)


def email_of(person):
	return str((person or {}).get("email") or "").strip().lower()


# ---------------------------------------------------------------------- people


def phrases_for(person):
	"""The normalized names one person answers to: full name, first name, aliases."""
	full = normalize(person.get("full_name"))
	out = []
	for phrase in [full, full.split()[0] if full else "", *split_aliases(person.get("aliases"))]:
		if phrase and phrase not in out:
			out.append(phrase)
	return out


def name_index(people):
	"""``{phrase: {resource, ...}}`` over everyone."""
	index = {}
	for person in people or []:
		for phrase in phrases_for(person):
			index.setdefault(phrase, set()).add(person["resource"])
	return index


def match_names(title, people, index=None):
	"""``(matched, ambiguous)``: the resources named in ``title``, and for each name two or more
	people share and nothing else settles, the set of them.

	A name inside a longer name that also matched is not counted on its own: "Austin Smith off"
	matches Austin Smith, not every Austin.
	"""
	index = name_index(people) if index is None else index
	norm = normalize(title)
	hits = [phrase for phrase in index if has_phrase(norm, phrase)]
	hits = [
		phrase
		for phrase in hits
		if not any(
			other != phrase and len(other) > len(phrase) and has_phrase(other, phrase) for other in hits
		)
	]
	matched, ambiguous = set(), []
	for phrase in sorted(hits):
		who = index[phrase]
		if len(who) == 1:
			matched |= who
		else:
			ambiguous.append(set(who))
	ambiguous = [who for who in ambiguous if not (who & matched)]
	return matched, ambiguous


def creator_resource(event, by_email, calendar_id=None):
	"""The Planner Resource of whoever created the event (else its organizer, unless the organizer
	is the calendar itself, as it is for every event made on a shared calendar), or None."""
	calendar = str(calendar_id or "").strip().lower()
	for key in ("creator", "organizer"):
		email = email_of((event or {}).get(key))
		if not email or email == calendar:
			continue
		if email in (by_email or {}):
			return by_email[email]
	return None


# ---------------------------------------------------------------------- the decision


def _answer(outcome, how, resources=(), everyone=False, reason="", suggested=None):
	return {
		"outcome": outcome,
		"how": how,
		"resources": sorted(resources or ()),
		"everyone": bool(everyone),
		"reason": reason,
		"suggested": suggested,
	}


def _from_decision(row, how):
	status = (row or {}).get("status")
	if status == STATUS_EVERYONE:
		return _answer(IMPORT, how, everyone=True)
	if status == STATUS_ASSIGNED and row.get("resource"):
		return _answer(IMPORT, how, [row["resource"]])
	if status == STATUS_IGNORED:
		return _answer(SKIP, HOW_IGNORED)
	return None


def classify(event, people, by_email=None, decisions=None, patterns=None, calendar_id=None, index=None):
	"""What to do with one event: ``{"outcome", "how", "resources", "everyone", "reason", "suggested"}``.

	``outcome`` is :data:`SKIP` (import nothing, and remove what it imported before), :data:`GONE`
	(deleted in Google), :data:`IMPORT` (``resources``, or ``everyone``) or :data:`REVIEW`
	(``reason``, and ``suggested`` when there is a best guess). See the module docstring for the order.

	``people`` are ``[{"resource", "full_name", "aliases", "user"}]``; ``by_email`` maps a lower-case
	address to a resource; ``decisions`` maps an event id and ``patterns`` a :func:`title_key` to a
	review row ``{"status", "resource"}``.
	"""
	event = event or {}
	if is_erpnext_event(event):
		return _answer(SKIP, HOW_OURS)
	if event.get("status") == "cancelled":
		return _answer(GONE, HOW_CANCELLED)
	if is_birthday(event):
		return _answer(SKIP, HOW_BIRTHDAY)
	decided = _from_decision((decisions or {}).get(event.get("id")), HOW_DECISION)
	if decided:
		return decided
	title = event.get("summary") or ""
	decided = _from_decision((patterns or {}).get(title_key(title)), HOW_PATTERN)
	if decided:
		return decided
	if is_company_holiday(title):
		return _answer(IMPORT, HOW_COMPANY, everyone=True)

	word = has_time_off_word(title)
	creator = creator_resource(event, by_email, calendar_id)
	matched, ambiguous = match_names(title, people, index)
	for who in ambiguous:
		if creator in who:
			matched.add(creator)
	ambiguous = [who for who in ambiguous if not (who & matched)]
	if matched and not ambiguous:
		if word:
			return _answer(IMPORT, HOW_TITLE, matched)
		return _answer(
			REVIEW,
			HOW_UNMATCHED,
			reason="A name, but no word like off, OOO, out or vacation",
			suggested=sorted(matched)[0],
		)
	if ambiguous:
		names = sorted(ambiguous[0])
		return _answer(
			REVIEW,
			HOW_UNMATCHED,
			reason="More than one person goes by that name",
			suggested=names[0] if names else None,
		)
	if word and creator:
		return _answer(IMPORT, HOW_CREATOR, [creator])
	if word:
		return _answer(
			REVIEW, HOW_UNMATCHED, reason="No name in the title, and nobody on the planner created it"
		)
	return _answer(
		REVIEW,
		HOW_UNMATCHED,
		reason="No name or time-off word in the title",
		suggested=creator,
	)


# ---------------------------------------------------------------------- days


def _date(value):
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value).strip()[:10])


def _local(text, zone):
	"""An RFC 3339 ``dateTime`` as a naive local datetime in ``zone`` (a tzinfo or None)."""
	text = str(text or "").strip()
	if text.endswith("Z"):
		text = text[:-1] + "+00:00"
	value = datetime.datetime.fromisoformat(text)
	if value.tzinfo is not None and zone is not None:
		value = value.astimezone(zone)
	return value.replace(tzinfo=None)


def zone_for(name):
	"""A tzinfo for an IANA name, or None (times are then read as written)."""
	if not name:
		return None
	try:
		from zoneinfo import ZoneInfo

		return ZoneInfo(str(name))
	except Exception:
		return None


def _clock(minutes):
	if minutes >= DAY_MINUTES:
		return END_OF_DAY
	return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def event_days(event, zone=None):
	"""``[{"date", "all_day", "from_time", "to_time"}]``, one per day the event covers.

	An all-day event covers ``start.date`` up to the day before ``end.date`` (Google's end is
	exclusive). A timed event is cut at midnight in ``zone`` (the site's time zone): each piece that
	is the whole day is all day, the rest keep their times ("13:00:00" to "17:00:00"; a piece running
	to midnight ends "23:59:00"). An event that ends before it starts covers nothing.
	"""
	start, end = (event or {}).get("start") or {}, (event or {}).get("end") or {}
	if start.get("date"):
		first = _date(start["date"])
		after = _date(end.get("date")) or first + datetime.timedelta(days=1)
		if after <= first:
			after = first + datetime.timedelta(days=1)
		days = min((after - first).days, MAX_DAYS)
		return [
			{"date": first + datetime.timedelta(days=n), "all_day": True, "from_time": None, "to_time": None}
			for n in range(days)
		]
	if not start.get("dateTime"):
		return []
	begin = _local(start["dateTime"], zone)
	finish = _local(end.get("dateTime") or start["dateTime"], zone)
	if finish <= begin:
		return []
	out = []
	day = begin.date()
	while day <= finish.date() and len(out) < MAX_DAYS:
		midnight = datetime.datetime.combine(day, datetime.time())
		piece_from = max(begin, midnight)
		piece_to = min(finish, midnight + datetime.timedelta(days=1))
		if piece_to > piece_from:
			first = int((piece_from - midnight).total_seconds() // 60)
			last = int((piece_to - midnight).total_seconds() // 60)
			whole = first == 0 and last >= DAY_MINUTES
			out.append(
				{
					"date": day,
					"all_day": whole,
					"from_time": None if whole else _clock(first),
					"to_time": None if whole else _clock(last),
				}
			)
		day += datetime.timedelta(days=1)
	return out


def in_window(days, start, end):
	return [d for d in days if start <= d["date"] <= end]


def when_text(days):
	"""``"Thu Oct 15"``, ``"Thu Oct 15 – Sat Oct 17"`` or ``"Thu Oct 15 1:00 pm–5:00 pm"``."""
	if not days:
		return ""

	def label(value):
		return f"{value.strftime('%a %b')} {value.day}"

	first, last = days[0], days[-1]
	if len(days) == 1:
		if first["all_day"]:
			return label(first["date"])
		return f"{label(first['date'])} {_twelve(first['from_time'])}–{_twelve(first['to_time'])}"
	return f"{label(first['date'])} – {label(last['date'])}"


def _twelve(text):
	match = re.match(r"^(\d{1,2}):(\d{2})", str(text or ""))
	if not match:
		return ""
	hour, minute = int(match.group(1)), int(match.group(2))
	suffix = "am" if hour < 12 else "pm"
	return f"{hour % 12 or 12}:{minute:02d} {suffix}"


# ---------------------------------------------------------------------- blocks


def time_text(value):
	"""A Time as ``"HH:MM:SS"`` (MariaDB hands a Time back as a ``timedelta``), or None."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime.timedelta):
		seconds = int(value.total_seconds()) % (24 * 3600)
		return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
	if isinstance(value, datetime.time):
		return value.strftime("%H:%M:%S")
	match = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", str(value))
	if not match:
		return None
	return f"{int(match.group(1)):02d}:{match.group(2)}:{match.group(3) or '00'}"


def wanted_blocks(resources, days, note):
	"""The blocks an event should have: one per person per day."""
	out = []
	for resource in sorted(set(resources or ())):
		for day in days or []:
			out.append(
				{
					"resource": resource,
					"date": day["date"],
					"all_day": 1 if day["all_day"] else 0,
					"from_time": None if day["all_day"] else day["from_time"],
					"to_time": None if day["all_day"] else day["to_time"],
					"note": note,
				}
			)
	return out


def block_key(row):
	return (row.get("resource"), str(_date(row.get("date"))))


def _same(want, have):
	return (
		int(want.get("all_day") or 0) == int(have.get("all_day") or 0)
		and time_text(want.get("from_time")) == time_text(have.get("from_time"))
		and time_text(want.get("to_time")) == time_text(have.get("to_time"))
		and (want.get("note") or "") == (have.get("note") or "")
	)


def diff_blocks(wanted, existing):
	"""``(create, update, delete)`` to turn one event's ``existing`` imported blocks into ``wanted``.

	``create`` are wanted rows; ``update`` are ``(name, wanted row)``; ``delete`` are names. Two
	existing blocks with the same person and day (two runs that raced) keep the first and delete the
	rest.
	"""
	have = {}
	delete = []
	for row in sorted(existing or [], key=lambda r: str(r.get("name") or "")):
		key = block_key(row)
		if key in have:
			delete.append(row["name"])
		else:
			have[key] = row
	create, update = [], []
	seen = set()
	for want in wanted or []:
		key = block_key(want)
		if key in seen:
			continue
		seen.add(key)
		row = have.get(key)
		if row is None:
			create.append(want)
		elif not _same(want, row):
			update.append((row["name"], want))
	delete.extend(row["name"] for key, row in have.items() if key not in seen)
	return create, update, delete


def note_for(title):
	"""What an imported block's (private) note says: where it came from."""
	text = " ".join(str(title or "").split())
	if len(text) > 120:
		text = text[:119].rstrip() + "…"
	return f"From Google Calendar: {text}" if text else "From Google Calendar"

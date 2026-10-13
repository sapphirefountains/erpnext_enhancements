# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What one Google Calendar event says, worked out from one ERPNext item. Pure: no frappe import.

An **item** is the plain dict ``sources.py`` reads out of ERPNext for one thing on a calendar (a
task, a visit, a projected contract visit, a day note, an unavailable block). This module turns it
into the body Google's ``events.insert`` / ``events.update`` takes, and decides what snap-back does
with an event somebody changed in Google. ``tests/test_calendar_sync.py`` runs all of it without a
bench.

Things this module is careful about, some of which look like bugs:

* **All-day ends are exclusive.** Google's ``end.date`` is the day *after* the last day, so a task
  from Mon 12 to Wed 14 is ``start 2026-10-12, end 2026-10-15``. :func:`event_times` is the only
  place that knows it, and it is the seam C3 swaps for timed route slots.
* **Nothing secret goes on a shared calendar.** The description is built only from structured
  fields (hours, crew names, equipment labels, qualification names, weather flags). It never reads a
  task's description, a project's notes, a visit's notes, a Maintenance Profile's ``access_codes``
  or a block's note. The few free-text values that do show (a task subject, a site address, a day
  note) pass through :func:`scrub`, which drops any segment that looks like a gate, door, alarm or
  lockbox code, a PIN or a password. That is defence in depth, not a promise: a bare number typed
  into a subject with no word around it cannot be told from a part number.
* **Deterministic event ids.** Google lets the client pick an event id (base32hex, ``[a-v0-9]``,
  5–1024 characters). :func:`event_id` is ``"erp"`` plus the SHA-1 of site, doctype, name and key,
  in lower-case hex, which is inside that alphabet. A job that ran twice, or ran after a deploy
  flushed the queue, inserts the same id, gets 409 and updates instead: no duplicate.
* **The content hash covers what we send, plus the calendar.** An unchanged item costs no Google
  call; moving an item to another calendar always counts as a change.
* **Snap-back ignores ERPNext's own writes by etag**, not by comparing text: Google may normalize
  what it stores, and a text comparison that never matches would rewrite the same event every ten
  minutes forever.
"""

import datetime
import hashlib
import json
import re

#: The private extended-property keys every ERPNext event carries.
PROP_SITE = "erpnext_site"
PROP_DOCTYPE = "erpnext_doctype"
PROP_NAME = "erpnext_name"
PROP_KIND = "erpnext_kind"
PROP_KEY = "erpnext_key"

ID_PREFIX = "erp"
#: Google's rule for a client-supplied event id.
EVENT_ID_RE = re.compile(r"^[a-v0-9]{5,1024}$")

FINISHED_PREFIX = "✓ "
PENCIL_PREFIX = "✎ "
PROJECTED_MARK = " (projected)"
SEP = " · "
CREW_SEP = " — "
TITLE_MAX = 200
NOTE_MAX = 600

FOOTER = "Managed by ERPNext — edits made here are undone. Change it in the planner: {link}"

KIND_TASK = "task"
KIND_VISIT = "visit"
KIND_PROJECTED = "projected"
KIND_DAY_NOTE = "day_note"
KIND_BLOCK = "block"
KINDS = (KIND_TASK, KIND_VISIT, KIND_PROJECTED, KIND_DAY_NOTE, KIND_BLOCK)

STATE_ACTIVE = "active"
STATE_FINISHED = "finished"
STATE_GONE = "gone"

#: Google colorId per value stream (and the two non-job kinds). Settings rows override one each.
#: 1 Lavender, 2 Sage, 3 Grape, 4 Flamingo, 5 Banana, 6 Tangerine, 7 Peacock, 8 Graphite,
#: 9 Blueberry, 10 Basil, 11 Tomato.
DEFAULT_COLORS = {
	"Design": "3",
	"Build": "9",
	"Service": "10",
	"Events": "6",
	"Delivery": "5",
	"Day Note": "8",
	"Unavailable": "8",
}
#: Project types that are another name for a value stream.
STREAM_ALIASES = {"Rent": "Events"}

#: Snap-back decisions.
IGNORE = "ignore"
REWRITE = "rewrite"
RECREATE = "recreate"
DELETE = "delete"
FORGET = "forget"

#: What the hash covers: everything we send except the id.
HASH_FIELDS = (
	"summary",
	"description",
	"location",
	"start",
	"end",
	"status",
	"colorId",
	"transparency",
	"reminders",
	"extendedProperties",
)

# A segment is dropped when it names a code with a qualifier ("gate code", "alarm code"), a code or
# PIN followed by digits ("code 4412", "PIN: 0911"), a password or passcode, a lockbox or a combo.
# "Building code review" survives: "code" alone, with no digits after it, is not a secret.
_SENSITIVE = re.compile(
	r"(?:\b(?:gate|door|access|entry|alarm|lock\s*box|lockbox|keypad|garage|key|security|wifi|wi-fi)\s*codes?\b)"
	r"|(?:\b(?:codes?|pins?)\s*(?:is|are|=|:|#|no\.?)?\s*[*#]?\d)"
	r"|(?:\bpass\s*(?:word|code|phrase)s?\b)"
	r"|(?:\bpwd\b)"
	r"|(?:\block\s*-?\s*box(?:es)?\b)"
	r"|(?:\bcombo\b|\bcombination\b)",
	re.IGNORECASE,
)
_SEGMENT_SPLIT = re.compile(r"[;,|]")


# ---------------------------------------------------------------------- text


def scrub(text):
	"""``text`` without any segment that looks like a code or password (see :data:`_SENSITIVE`).

	Lines are split on ``;``, ``,`` and ``|``; a segment that matches is dropped and the rest are
	joined with ", ". "123 Main St, Gate code 4412, Denver" becomes "123 Main St, Denver".
	"""
	if text is None:
		return ""
	lines = []
	for line in str(text).splitlines():
		parts = [part.strip() for part in _SEGMENT_SPLIT.split(line)]
		kept = [part for part in parts if part and not _SENSITIVE.search(part)]
		if kept:
			lines.append(", ".join(kept))
	return "\n".join(lines).strip()


def first_line(text, limit=120):
	"""The first non-blank line of ``text``, cut to ``limit`` characters."""
	for line in str(text or "").splitlines():
		line = line.strip()
		if line:
			return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
	return ""


def first_name(label):
	"""``"Austin Smith"`` → ``"Austin"``. A blank label is blank."""
	label = str(label or "").strip()
	return label.split()[0] if label else ""


def crew_first_names(crew):
	"""Comma-joined first names of ``crew`` (``[{"name", "first"}]`` or plain strings), deduplicated
	in order. An entry's ``first`` wins over its name's first word: a subcontractor is its whole name."""
	seen, out = set(), []
	for member in crew or []:
		if isinstance(member, dict):
			shown = member.get("first") or first_name(member.get("name"))
		else:
			shown = first_name(member)
		if shown and shown not in seen:
			seen.add(shown)
			out.append(shown)
	return ", ".join(out)


def crew_full_names(crew):
	out = []
	for member in crew or []:
		if isinstance(member, dict):
			name = member.get("name") or ""
			if name and member.get("lead"):
				name = f"{name} (lead)"
		else:
			name = str(member or "")
		if name and name not in out:
			out.append(name)
	return ", ".join(out)


def fmt_hours(hours):
	"""``8.0`` → ``"8h"``, ``2.5`` → ``"2.5h"``; None or 0 → ``""``."""
	try:
		value = float(hours or 0)
	except (TypeError, ValueError):
		return ""
	if value <= 0:
		return ""
	return f"{value:g}h"


def clock(text):
	"""``"14:30:00"`` → ``"2:30 pm"``, ``"09:00"`` → ``"9 am"``; anything unreadable → ``""``."""
	match = re.match(r"^\s*(\d{1,2}):(\d{2})", str(text or ""))
	if not match:
		return ""
	hour, minute = int(match.group(1)), int(match.group(2))
	if hour > 24 or minute > 59:
		return ""
	suffix = "am" if hour < 12 or hour == 24 else "pm"
	shown = hour % 12 or 12
	minutes = f":{minute:02d}" if minute else ""
	return f"{shown}{minutes} {suffix}"


def time_range(from_time, to_time):
	"""``"2 pm–4 pm"`` (or ``"from 2 pm"`` / ``"until 4 pm"``); ``""`` with neither."""
	start, end = clock(from_time), clock(to_time)
	if start and end:
		return f"{start}–{end}"
	if start:
		return f"from {start}"
	if end:
		return f"until {end}"
	return ""


def _date(value):
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value).strip()[:10])


def day_label(value):
	"""``2026-10-15`` → ``"Thu Oct 15"``."""
	day = _date(value)
	return f"{day.strftime('%a %b')} {day.day}" if day else ""


# ---------------------------------------------------------------------- ids and hashes


def event_id(site, doctype, name, key=None, generation=0):
	"""The deterministic Google event id for one item (see the module docstring).

	``generation`` is only ever above 0 when Google refuses the id outright (an event deleted long
	enough ago to be purged cannot be restored or re-inserted under the same id); it appends ``g<n>``,
	which is still base32hex.
	"""
	raw = "|".join(str(part or "") for part in (site, doctype, name, key or ""))
	value = ID_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()
	if generation:
		value += f"g{int(generation)}"
	return value


def is_valid_event_id(value):
	return bool(EVENT_ID_RE.match(str(value or "")))


def source_ref(doctype, name, key=None):
	"""The Calendar Sync Link's unique ``source_ref``: ``doctype|name|key``."""
	return f"{doctype}|{name}|{key or ''}"


def content_hash(body, calendar_id):
	"""A short hash of what an event says and where it lives (see :data:`HASH_FIELDS`)."""
	data = {field: (body or {}).get(field) for field in HASH_FIELDS if field in (body or {})}
	data["calendar"] = calendar_id or ""
	text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
	return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


# ---------------------------------------------------------------------- the event


def event_times(item):
	"""``{"start", "end"}`` for Google: an all-day event from ``item["start"]`` to ``item["end"]``.

	**The C3 seam.** Timed route slots replace this one function: everything else takes its answer
	as it is. Google's all-day ``end.date`` is exclusive, hence the day added. An end before the
	start is treated as a one-day item.
	"""
	start = _date(item.get("start"))
	end = _date(item.get("end")) or start
	if end < start:
		end = start
	return {
		"start": {"date": start.isoformat()},
		"end": {"date": (end + datetime.timedelta(days=1)).isoformat()},
	}


def is_tentative(item):
	return bool(item.get("tentative")) or item.get("kind") == KIND_PROJECTED


def title_for(item):
	"""``"<site> · <subject or visit label> — <crew first names>"``, with the kind's marks.

	Finished work keeps its event with "✓ " in front; a pencil gets "✎ "; a projected contract
	visit says "(projected)". A day note is "Note: <first line>" (on its project's site when it has
	one, with its group); an unavailable block is "<first name> — Unavailable <time>".
	"""
	kind = item.get("kind")
	site = scrub(item.get("site"))
	if kind == KIND_BLOCK:
		who = first_name(item.get("person")) or "Someone"
		text = f"{who}{CREW_SEP}Unavailable"
		if item.get("time_text"):
			text += f" {item['time_text']}"
		return text[:TITLE_MAX]
	if kind == KIND_DAY_NOTE:
		text = f"Note: {first_line(scrub(item.get('note')), 100) or 'see the planner'}"
		if item.get("audience"):
			text += f" ({item['audience']})"
		if site:
			text = f"{site}{SEP}{text}"
		return text[:TITLE_MAX]
	label = first_line(scrub(item.get("label")), 120) or (
		"Maintenance visit" if kind != KIND_TASK else "Task"
	)
	if kind == KIND_PROJECTED:
		label += PROJECTED_MARK
	text = f"{site}{SEP}{label}" if site else label
	crew = crew_first_names(item.get("crew"))
	if crew:
		text += f"{CREW_SEP}{crew}"
	if item.get("state") == STATE_FINISHED:
		text = FINISHED_PREFIX + text
	elif item.get("tentative") and kind != KIND_PROJECTED:
		text = PENCIL_PREFIX + text
	return text[:TITLE_MAX]


def description_for(item):
	"""The event's description: structured facts only, then the managed-by line and planner link."""
	kind = item.get("kind")
	lines = []
	if kind == KIND_BLOCK:
		lines.append("Unavailable (a personal block on the planner).")
	elif kind == KIND_DAY_NOTE:
		note = scrub(item.get("note"))
		if note:
			lines.append(note[:NOTE_MAX])
		lines.append(f"For: {item.get('audience') or 'everyone'}")
	else:
		if item.get("state") == STATE_FINISHED:
			lines.append("Status: done")
		elif kind == KIND_PROJECTED:
			lines.append("Status: projected from the maintenance contract, not scheduled yet")
		elif item.get("tentative"):
			lines.append("Status: pencilled in, not confirmed")
		hours = "Full day" if item.get("full_day") else fmt_hours(item.get("hours"))
		start, end = _date(item.get("start")), _date(item.get("end"))
		if start and end and end > start:
			span = f"{(end - start).days + 1} days"
			hours = f"{hours} over {span}" if hours else span
		if hours:
			lines.append(f"Hours: {hours}")
		crew = crew_full_names(item.get("crew"))
		if crew:
			lines.append(f"Crew: {crew}")
		if item.get("equipment"):
			lines.append("Equipment: " + ", ".join(scrub(e) for e in item["equipment"] if scrub(e)))
		if item.get("credentials"):
			lines.append("Qualifications needed: " + ", ".join(str(c) for c in item["credentials"] if c))
		for line in item.get("weather") or []:
			lines.append(f"Weather: {line}")
	if item.get("link"):
		lines.append("")
		lines.append(FOOTER.format(link=item["link"]))
	return "\n".join(lines).strip()


def stream_for(item):
	"""The first of the item's value streams that has a color (``item["streams"]`` in priority order)."""
	for stream in item.get("streams") or []:
		stream = STREAM_ALIASES.get(stream, stream)
		if stream in DEFAULT_COLORS:
			return stream
	return None


def color_for(item, overrides=None):
	"""Google ``colorId`` for the item: a Settings override for its stream, else the default, else None."""
	stream = stream_for(item)
	if not stream:
		return None
	value = str((overrides or {}).get(stream) or DEFAULT_COLORS.get(stream) or "").strip()
	return value if value.isdigit() and 1 <= int(value) <= 11 else DEFAULT_COLORS.get(stream)


def properties(item, site):
	props = {
		PROP_SITE: site or "",
		PROP_DOCTYPE: item.get("doctype") or "",
		PROP_NAME: item.get("name") or "",
		PROP_KIND: item.get("kind") or "",
	}
	if item.get("key"):
		props[PROP_KEY] = item["key"]
	return props


def build_event(item, site, colors=None):
	"""The body for ``events.insert`` / ``events.update`` (without ``id``).

	No attendees, ever: these are shared calendars, and an attendee would get an invitation. No
	reminders on the account that writes them either (an override list of none), and transparent,
	so a hundred jobs never mark that account busy.
	"""
	body = {
		"summary": title_for(item),
		"description": description_for(item),
		**event_times(item),
		"status": "tentative" if is_tentative(item) else "confirmed",
		"transparency": "transparent",
		"reminders": {"useDefault": False, "overrides": []},
		"extendedProperties": {"private": properties(item, site)},
	}
	location = scrub(item.get("location"))
	if location:
		body["location"] = location[:500]
	color = color_for(item, colors)
	if color:
		body["colorId"] = color
	return body


def link_kind(item):
	"""The Calendar Sync Link ``kind`` label for an item."""
	kind = item.get("kind")
	if kind == KIND_TASK:
		return "Pencil Task" if item.get("tentative") else "Task"
	if kind == KIND_VISIT:
		return "Pencil Visit" if item.get("tentative") else "Visit"
	return {KIND_PROJECTED: "Projected Visit", KIND_DAY_NOTE: "Day Note", KIND_BLOCK: "Unavailable"}.get(
		kind, ""
	)


# ---------------------------------------------------------------------- snap-back


def event_props(event):
	return (((event or {}).get("extendedProperties") or {}).get("private")) or {}


def is_ours(event, site):
	"""True when the event carries this site's private ERPNext properties."""
	props = event_props(event)
	return bool(site) and props.get(PROP_SITE) == site and bool(props.get(PROP_DOCTYPE))


def snapback_action(event, site, item_state, item_calendar_id, calendar_id, link_etag, expected_id):
	"""What to do with one event Google's incremental list returned for ``calendar_id``.

	* Not ours (no private ERPNext properties for this site): **ignore**. Other people's events,
	  and another site's (staging), are never touched.
	* Its source is gone or canceled: **delete** it, or **forget** it if Google already has it
	  deleted.
	* A copy that is not the item's own event id (someone duplicated it in Google): **delete** it.
	* The item now belongs on another calendar: **delete** this one (the item's job writes the
	  other); if it is already deleted here, **ignore**.
	* Still carries the etag of ERPNext's own last write: **ignore** (it is our own change echoing).
	* Deleted in Google while its source is live: **recreate**.
	* Otherwise somebody edited it: **rewrite** from ERPNext.
	* The item's calendar is not configured: **ignore** (nothing to compare against).
	"""
	if not is_ours(event, site):
		return IGNORE
	cancelled = (event or {}).get("status") == "cancelled"
	if item_state == STATE_GONE:
		return FORGET if cancelled else DELETE
	if expected_id and (event or {}).get("id") != expected_id:
		return IGNORE if cancelled else DELETE
	if not item_calendar_id:
		return IGNORE
	if item_calendar_id != calendar_id:
		return IGNORE if cancelled else DELETE
	if link_etag and (event or {}).get("etag") == link_etag and not cancelled:
		return IGNORE
	return RECREATE if cancelled else REWRITE

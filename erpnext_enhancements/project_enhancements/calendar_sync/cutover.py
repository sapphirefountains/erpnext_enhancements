# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar cutover (C5): retire the hand-made recurring series on the shared Maintenance calendar
without losing what their descriptions know.

Until ERPNext's sync (C2) took over, the maintenance schedule lived on the shared "Maintenance" Google
calendar as one hand-made **recurring series per site**, and the series' descriptions hold site
knowledge nobody wrote down anywhere else: **Google Sites links** and **special instructions**. Nik's
cutover plan (2026-10-12, settled), and the four steps this module provides for it:

1. **Scan** (:func:`scan`, read-only Google). One ``events.list`` pass over the Maintenance calendar with
   ``singleEvents=false``, so Google returns each series' *master* with its RRULE rather than its
   occurrences. Every event carrying C2's private ``erpnext_*`` properties is ignored, of any site.
   Each hand-made series that still runs becomes a **Calendar Cutover Item**: the proposed
   Maintenance Profile (:func:`match_profile`, fuzzy but explained), the Sites links, the special
   instructions with the HTML taken out, any access codes, and the event's location when the site's
   project has no address of its own. A rescan refreshes **Pending** rows only; Approved, Applied and
   Ignored rows keep every proposal and decision a person made.
2. **Apply approved items** (:func:`apply_approved`). Nothing reaches a profile until a person approved
   the row. Links are added to the profile's *Google Sites links* (one per line, never twice); the
   instructions are **appended** to *Special instructions* under a "From the old Maintenance calendar"
   header, never written over what is there; codes go to *Access Codes* when they fit its 140
   characters; the location becomes the project's ``custom_project_address`` **only if that is empty**,
   and the existing geocode is queued for it.
3. **End the series** (:func:`end_series`, the one step that writes to Google). For each reviewed
   series still marked *End at cutover*, the master's RRULE gets ``UNTIL=<the day before cutover>``
   through ``events.patch``, every other part of the rule kept. Past occurrences stay as history;
   future ones stop. It never deletes, never touches a single event and never touches an ERPNext
   event. It always starts as a **dry run**; writing needs ``confirm`` *and* the token of a dry run
   whose plan is still exactly what Google and the review list say now.
4. **Undo** (:func:`undo_end`). Every rewrite is logged in **Calendar Cutover Log** with the original
   recurrence; undo puts it back, but only on a series whose rule is still the one we wrote.

Switching the sync on, and deleting the old "ERPNext Tasks/Project/ToDo" calendars, are Nik's own
steps and are not automated here.

RRULE handling (:func:`rewrite_recurrence`): an open rule gets UNTIL appended; an UNTIL already on or
after the cutover is moved back; an UNTIL already before it is left alone; a COUNT is swapped for UNTIL
(RFC 5545 forbids both) **only when Google's own ``instances`` list shows the rule still produces an
occurrence on or after the cutover**, because swapping the COUNT of a rule that already finished would
*extend* it. EXDATE and EXRULE lines are kept; RDATE dates on or after the cutover are dropped. UNTIL
matches the start's type, as Google requires: a date for an all-day series, the UTC instant one second
before local midnight of the cutover for a timed one.

Secrets: the profile is internal, so codes may land there. C2's event builder reads nothing from a
Maintenance Profile (``sources.py`` / ``payload.py``), and ``tests/test_calendar_cutover.py`` fails the
build if that changes. The proposed address is scrubbed (``payload.scrub``) because it *does* reach
shared calendars as an event's location. Nothing here logs a description, a code or a token.
"""

import datetime
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlparse

import frappe

from erpnext_enhancements.project_enhancements.calendar_sync import engine, google_api, payload

ITEM = "Calendar Cutover Item"
LOG = "Calendar Cutover Log"
PROFILE = "Sapphire Maintenance Profile"
PROJECT = "Project"

PENDING = "Pending"
APPROVED = "Approved"
APPLIED = "Applied"
IGNORED = "Ignored"
STATUSES = (PENDING, APPROVED, APPLIED, IGNORED)
REVIEWED = (APPROVED, APPLIED, IGNORED)

MATCHED = "Matched"
AMBIGUOUS = "Ambiguous"
UNMATCHED = "Unmatched"
BY_HAND = "Chosen by hand"

RUNNING = "Running"
ENDED = "Ended at cutover"
ALREADY_ENDED = "Already ended"

KIND_OURS = "erpnext"
KIND_SERIES = "series"
KIND_EXCEPTION = "exception"
KIND_SINGLE = "single"
KIND_CANCELLED = "cancelled"

LIST_PAGES = 40
MAX_SERIES = 300
MATCH_MIN = 60
AMBIGUOUS_GAP = 10
ADDRESS_MAX = 140  # Project.custom_project_address is Data(140)
ACCESS_CODES_MAX = 140  # Sapphire Maintenance Profile.access_codes is Data(140)
MAX_CUTOVER_DAYS_AHEAD = 366

LINKS_FIELD = "google_sites_links"
INSTRUCTIONS_FIELD = "special_instructions"
CODES_FIELD = "access_codes"
ADDRESS_FIELD = "custom_project_address"
#: What this module may write on a Maintenance Profile, and nothing else.
PROFILE_FIELDS = (LINKS_FIELD, INSTRUCTIONS_FIELD, CODES_FIELD)

PROVENANCE = "From the old Maintenance calendar"

GEOCODE_JOB = "erpnext_enhancements.workforce.sites.geocode_project"


# ---------------------------------------------------------------------- reading an event


def is_erpnext_event(event):
	"""True when an event carries any of C2's private ``erpnext_*`` properties, of any site.

	Wider than ``payload.is_ours`` on purpose: a staging site's events are not hand-made either.
	"""
	props = payload.event_props(event)
	return any(str(key).startswith("erpnext_") for key in props)


def classify(event):
	"""``erpnext`` / ``cancelled`` / ``exception`` (a moved or edited occurrence of a series) /
	``series`` (a recurring master) / ``single``."""
	if is_erpnext_event(event):
		return KIND_OURS
	if (event or {}).get("status") == "cancelled":
		return KIND_CANCELLED
	if (event or {}).get("recurringEventId"):
		return KIND_EXCEPTION
	if (event or {}).get("recurrence"):
		return KIND_SERIES
	return KIND_SINGLE


_BLOCK_TAGS = frozenset("p div ul ol tr table h1 h2 h3 h4 h5 h6 blockquote pre".split())


class _TextParser(HTMLParser):
	"""An event description (Google stores the web UI's as HTML) as plain text, plus its links.

	A parser, never a regex over the markup: a regex rewrite of HTML breaks on the first attribute
	that contains a ``>``.
	"""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.parts = []
		self.links = []
		self._href = None
		self._anchor = []

	def handle_starttag(self, tag, attrs):
		tag = tag.lower()
		if tag == "br":
			self._emit("\n")
		elif tag == "li":
			self._emit("\n- ")
		elif tag in _BLOCK_TAGS:
			self._emit("\n")
		elif tag == "a":
			self._href = unwrap_link(dict(attrs).get("href") or "")
			self._anchor = []

	def handle_startendtag(self, tag, attrs):
		self.handle_starttag(tag, attrs)

	def handle_endtag(self, tag):
		tag = tag.lower()
		if tag == "a" and self._href is not None:
			text, href = "".join(self._anchor).strip(), self._href.strip()
			self._href, self._anchor = None, []
			if href:
				self.links.append(href)
			if href and text and unwrap_link(text) != href and href not in text:
				self.parts.append(f"{text} ({href})")
			else:
				self.parts.append(href or text)
		elif tag in _BLOCK_TAGS or tag == "li":
			self._emit("\n")

	def handle_data(self, data):
		self._emit(data)

	def _emit(self, text):
		if self._href is not None:
			self._anchor.append(text)
		else:
			self.parts.append(text)


def unwrap_link(url):
	"""Google wraps links it rewrote as ``https://www.google.com/url?q=<the link>&sa=D…``: the link."""
	url = str(url or "").strip()
	try:
		parsed = urlparse(url)
		if parsed.netloc.endswith("google.com") and parsed.path == "/url":
			target = (parse_qs(parsed.query).get("q") or [""])[0]
			if target.startswith(("http://", "https://")):
				return target
	except Exception:
		pass
	return url


def html_to_text(description):
	"""``(text, links)``: the description as tidy plain text, and every link it held, in order."""
	parser = _TextParser()
	try:
		parser.feed(str(description or ""))
		parser.close()
	except Exception:
		return str(description or "").strip(), []
	text = "".join(parser.parts).replace("\xa0", " ").replace("\r", "")
	lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
	tidy = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
	return tidy, parser.links


_SITES_RE = re.compile(r"https?://sites\.google\.com/[^\s<>\"'()\[\]]+", re.IGNORECASE)


def _clean_url(url):
	return unwrap_link(url).rstrip(".,;:!?")


def sites_links(text, links=()):
	"""Every Google Sites URL in the description's links and text, deduplicated, in order."""
	out = []
	candidates = [_clean_url(u) for u in links or ()] + [_clean_url(m) for m in _SITES_RE.findall(text or "")]
	for url in candidates:
		if _SITES_RE.match(url) and url not in out:
			out.append(url)
	return out


def instructions_from(text, links):
	"""The description text without the Sites links (they get their own field) and without a line
	that held only a link and its label."""
	out = []
	for line in (text or "").split("\n"):
		original = line
		for url in links:
			line = line.replace(f"({url})", "").replace(url, "")
		line = re.sub(r"\s+", " ", line).strip()
		if original.strip() and line != original.strip():
			# The line held a link: drop what is left when it is only punctuation or a dangling label.
			if not re.search(r"[A-Za-z0-9]", line) or (line.endswith(":") and len(line) <= 40):
				continue
		out.append(line)
	return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def find_codes(text):
	"""Every segment that names an access, gate, alarm or lockbox code, a PIN, a password or a combo
	(C2's own ``payload._SENSITIVE`` rule, so "what counts as a code" has one definition), joined with
	" | "."""
	found = []
	for line in (text or "").split("\n"):
		for part in payload._SEGMENT_SPLIT.split(line):
			part = part.strip(" -\t")
			if part and payload._SENSITIVE.search(part) and part not in found:
				found.append(part)
	return " | ".join(found)


def _event_day(value):
	"""A Google ``start`` / ``originalStartTime`` as a date (the local date of a dateTime)."""
	value = value or {}
	if value.get("date"):
		return datetime.date.fromisoformat(value["date"][:10])
	if value.get("dateTime"):
		return datetime.date.fromisoformat(value["dateTime"][:10])
	return None


def rrule_line(recurrence):
	rules = [line for line in recurrence or [] if str(line).upper().startswith("RRULE")]
	return rules[0] if len(rules) == 1 else ""


def parse_rule(line):
	"""``"RRULE:FREQ=WEEKLY;UNTIL=x"`` → ``("RRULE", [("FREQ", "WEEKLY"), ("UNTIL", "x")])``, order kept."""
	head, _, body = str(line or "").partition(":")
	parts = []
	for piece in body.split(";"):
		if not piece:
			continue
		key, _, value = piece.partition("=")
		parts.append((key.strip().upper(), value.strip()))
	return head, parts


def format_rule(head, parts):
	return f"{head}:" + ";".join(f"{key}={value}" for key, value in parts)


def zone(name):
	"""A ``ZoneInfo`` for an IANA name, or None."""
	if not name:
		return None
	try:
		from zoneinfo import ZoneInfo

		return ZoneInfo(str(name))
	except Exception:
		return None


def until_day(value, tz=None):
	"""The local date an UNTIL value names: ``20261012``, ``20261012T055959Z`` (UTC, shown in ``tz``)
	or a floating ``20261012T090000``. None when unreadable."""
	value = str(value or "").strip().upper()
	try:
		if len(value) == 8:
			return datetime.datetime.strptime(value, "%Y%m%d").date()
		if value.endswith("Z"):
			moment = datetime.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
			return moment.astimezone(tz).date() if tz else moment.date()
		return datetime.datetime.strptime(value[:15], "%Y%m%dT%H%M%S").date()
	except ValueError:
		return None


def until_value(cutover, all_day, tz=None):
	"""The UNTIL that keeps every occurrence before ``cutover`` and none on or after it.

	All-day: the date before. Timed: one second before local midnight of the cutover, in UTC (Google
	refuses a floating UNTIL on a timed series). None for a timed series with no usable time zone.
	"""
	if all_day:
		return (cutover - datetime.timedelta(days=1)).strftime("%Y%m%d")
	if tz is None:
		return None
	midnight = datetime.datetime.combine(cutover, datetime.time(0, 0), tzinfo=tz)
	last = (midnight - datetime.timedelta(seconds=1)).astimezone(datetime.timezone.utc)
	return last.strftime("%Y%m%dT%H%M%SZ")


def _rdate_keep(line, cutover):
	"""An RDATE line without its dates on or after the cutover (None when none are left)."""
	head, _, values = str(line).rpartition(":")
	kept = [v for v in values.split(",") if v.strip() and until_day(v[:8]) and until_day(v[:8]) < cutover]
	return f"{head}:{','.join(kept)}" if kept else None


def rewrite_recurrence(recurrence, cutover, all_day, tz=None, runs_past=None):
	"""What ending a series at ``cutover`` does to its ``recurrence`` lines. Pure.

	Returns ``{"change": bool, "after": [lines], "note": str, "error": str}``. ``runs_past`` is only
	needed for a COUNT rule: Google's own answer to "does this rule still produce an occurrence on or
	after the cutover?" (see the module docstring). ``error`` means "do not touch it".
	"""
	recurrence = [str(line) for line in recurrence or []]
	rule = rrule_line(recurrence)
	if not rule:
		several = sum(1 for line in recurrence if line.upper().startswith("RRULE"))
		reason = "it has several RRULE lines" if several else "it has no RRULE"
		return {"change": False, "after": recurrence, "note": "", "error": f"Not touched: {reason}."}
	value = until_value(cutover, all_day, tz)
	if not value:
		return {
			"change": False,
			"after": recurrence,
			"note": "",
			"error": "Not touched: a timed series with no time zone Google or this server knows.",
		}
	head, parts = parse_rule(rule)
	keys = [key for key, _value in parts]
	last_day = cutover - datetime.timedelta(days=1)
	if "UNTIL" in keys:
		existing = dict(parts)["UNTIL"]
		day = until_day(existing, tz)
		if day is None:
			return {
				"change": False,
				"after": recurrence,
				"note": "",
				"error": "Not touched: its UNTIL is unreadable.",
			}
		if day < cutover:
			return {
				"change": False,
				"after": recurrence,
				"note": f"Already ends on {day.isoformat()}, before the cutover.",
				"error": "",
			}
		parts = [(key, value if key == "UNTIL" else v) for key, v in parts]
		note = f"UNTIL moved from {day.isoformat()} to {last_day.isoformat()}."
	elif "COUNT" in keys:
		if runs_past is None:
			return {
				"change": False,
				"after": recurrence,
				"note": "",
				"error": "Not touched: a COUNT rule needs Google's occurrence list to decide.",
			}
		if not runs_past:
			return {
				"change": False,
				"after": recurrence,
				"note": f"Its COUNT={dict(parts)['COUNT']} already runs out before the cutover.",
				"error": "",
			}
		count = dict(parts)["COUNT"]
		parts = [(key, v) for key, v in parts if key != "COUNT"] + [("UNTIL", value)]
		note = f"COUNT={count} replaced by UNTIL: last occurrence on or before {last_day.isoformat()}."
	else:
		parts = [*parts, ("UNTIL", value)]
		note = f"Open-ended; now ends on or before {last_day.isoformat()}."
	after = []
	dropped_rdates = False
	for line in recurrence:
		if line == rule:
			after.append(format_rule(head, parts))
		elif line.upper().startswith("RDATE"):
			kept = _rdate_keep(line, cutover)
			dropped_rdates = dropped_rdates or kept != line
			if kept:
				after.append(kept)
		else:
			after.append(line)  # EXDATE, EXRULE: kept as they are
	if dropped_rdates:
		note += " Extra dates (RDATE) on or after the cutover dropped."
	return {"change": after != recurrence, "after": after, "note": note, "error": ""}


def parse_series(event, today, calendar_tz=None):
	"""One hand-made series master as the plain dict a review row is made from. Pure."""
	description = (event or {}).get("description") or ""
	text, links = html_to_text(description)
	found = sites_links(text, links)
	recurrence = list((event or {}).get("recurrence") or [])
	rule = rrule_line(recurrence)
	start = (event or {}).get("start") or {}
	tz_name = start.get("timeZone") or calendar_tz or ""
	state = RUNNING
	if rule:
		_head, parts = parse_rule(rule)
		until = dict(parts).get("UNTIL")
		day = until_day(until, zone(tz_name)) if until else None
		if day is not None and day < today:
			state = ALREADY_ENDED
	return {
		"series_id": event.get("id"),
		"title": ((event or {}).get("summary") or "").strip() or "(no title)",
		"recurrence": recurrence,
		"rrule": rule,
		"first_date": _event_day(start),
		"all_day": bool(start.get("date")),
		"time_zone": tz_name,
		"location": ((event or {}).get("location") or "").strip(),
		"html_link": (event or {}).get("htmlLink") or "",
		"description_text": text,
		"links": found,
		"instructions": instructions_from(text, found),
		"codes": find_codes(text),
		"state": state,
	}


# ---------------------------------------------------------------------- matching a profile

#: Words that say nothing about which site a series is: cadence, the trade, legal suffixes.
STOP_WORDS = frozenset(
	"""
	a an and the of at on in for to by with
	fountain fountains water feature features pond ponds pool pools
	maintenance maint service services servicing cleaning clean cleanup visit visits check checks
	pm preventative preventive contract site
	daily weekly biweekly bi monthly bimonthly quarterly annual annually yearly seasonal
	every week weeks month months day days
	inc llc co corp company ltd hoa
	sapphire
	""".split()
)
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_DIRECTIONS = frozenset("n s e w ne nw se sw north south east west".split())


def words(text):
	"""The meaningful lower-case words of a name: no stop words, no years."""
	tokens = re.findall(r"[a-z0-9]+", str(text or "").lower().replace("&", " and "))
	return {t for t in tokens if t not in STOP_WORDS and not _YEAR.match(t)}


def street_key(text):
	"""``("1200", "lincoln")`` for "1200 N Lincoln St, Denver" and "1200 North Lincoln Street": the
	house number that starts a part of the address and the street's first word after any direction.
	None without one. "3000 E 1st Ave" and "3000 East 1st Avenue" agree on ``("3000", "1st")``."""
	for segment in re.split(r"[,;\n]", str(text or "").lower()):
		tokens = re.findall(r"[a-z0-9]+", segment)
		if len(tokens) < 2 or not tokens[0].isdigit() or len(tokens[0]) > 6:
			continue
		rest = [t for t in tokens[1:] if t not in _DIRECTIONS]
		if rest:
			return tokens[0], rest[0]
	return None


def _name_score(title, name):
	tt, nt = words(title), words(name)
	if not tt or not nt:
		return 0, ""
	if tt == nt:
		return 100, f"same name as “{name}”"
	if nt <= tt:
		return 90, f"the title has every word of “{name}”"
	if tt <= nt:
		return 80, f"every word of the title is in “{name}”"
	squashed = "".join(sorted(nt))
	if len(squashed) >= 5 and squashed == "".join(sorted(tt)):
		return 85, f"same words as “{name}”"
	shared = tt & nt
	if not shared:
		return 0, ""
	score = int(round(75 * len(shared) / len(tt | nt)))
	return score, f"shares {', '.join(sorted(shared))} with “{name}”"


def score_candidate(title, location, candidate):
	"""``(score 0–100, reason)`` for one profile. The best of the name scores and the address score,
	plus 10 (to 100) when the name and the address agree."""
	best, reason = 0, ""
	for name in candidate.get("names") or []:
		score, why = _name_score(title, name)
		if score > best:
			best, reason = score, why
	address_score, address_reason = 0, ""
	ours, theirs = street_key(location), street_key(candidate.get("address"))
	if ours and theirs and ours == theirs:
		address_score, address_reason = 85, f"same street address ({' '.join(ours)})"
	elif location:
		for name in candidate.get("names") or []:
			nt = words(name)
			if nt and nt <= words(location):
				address_score, address_reason = 70, f"the location names “{name}”"
				break
	if address_score and best >= MATCH_MIN:
		return min(100, max(best, address_score) + 10), f"{reason}; {address_reason}"
	if address_score > best:
		return address_score, address_reason
	return best, reason


def match_profile(title, location, candidates):
	"""The profile a series belongs to, with the reason a person can check. Pure.

	Returns ``{"profile", "project", "quality", "note", "candidate"}``: **Matched** when the best score
	is at least :data:`MATCH_MIN` and no other profile is within :data:`AMBIGUOUS_GAP` of it,
	**Ambiguous** (no profile proposed, the close ones named) when one is, **Unmatched** otherwise
	(the nearest named when there is one).
	"""
	scored = []
	for candidate in candidates or []:
		score, reason = score_candidate(title, location, candidate)
		if score:
			scored.append((score, reason, candidate))
	scored.sort(key=lambda row: (-row[0], str(row[2].get("profile"))))
	if not scored or scored[0][0] < MATCH_MIN:
		note = "No Maintenance Profile matches the title or the location."
		if scored:
			score, reason, candidate = scored[0]
			note += f" Nearest: {candidate.get('label') or candidate.get('profile')} ({score}: {reason})."
		return {"profile": None, "project": None, "quality": UNMATCHED, "note": note, "candidate": None}
	top_score, top_reason, top = scored[0]
	close = [row for row in scored[1:] if row[0] >= MATCH_MIN and top_score - row[0] < AMBIGUOUS_GAP]
	if close:
		named = "; ".join(
			f"{c.get('label') or c.get('profile')} ({s}: {r})" for s, r, c in [scored[0], *close][:4]
		)
		return {
			"profile": None,
			"project": None,
			"quality": AMBIGUOUS,
			"note": f"More than one profile fits: {named}. Pick one.",
			"candidate": None,
		}
	return {
		"profile": top.get("profile"),
		"project": top.get("project"),
		"quality": MATCHED,
		"note": f"{top.get('label') or top.get('profile')} ({top_score}): {top_reason}.",
		"candidate": top,
	}


def proposed_address(location, candidate):
	"""The site address to fill from the old location: only when the matched project has no
	``custom_project_address``. Scrubbed of codes, because a project address becomes the location of
	every ERPNext event at that site."""
	if not candidate or (candidate.get("project_address") or "").strip():
		return ""
	return payload.scrub(location or "")[:ADDRESS_MAX].strip()


# ---------------------------------------------------------------------- appending to a profile


def merge_links(existing, new_links):
	"""``(text, added)``: the existing lines untouched, then each new link not already there."""
	lines = [line for line in str(existing or "").split("\n")]
	have = {line.strip().rstrip("/") for line in lines if line.strip()}
	added = []
	for url in new_links or []:
		url = str(url).strip()
		if url and url.rstrip("/") not in have:
			have.add(url.rstrip("/"))
			added.append(url)
	if not added:
		return str(existing or ""), []
	kept = str(existing or "").rstrip()
	return (kept + "\n" if kept else "") + "\n".join(added), added


def append_block(existing, text, header):
	"""``(text, appended)``: ``text`` under ``header`` after what is there (a blank line between),
	never over it. Nothing when it is blank or already there word for word."""
	text = str(text or "").strip()
	existing = str(existing or "")
	if not text or text in existing:
		return existing, False
	block = f"{header}:\n{text}"
	return (existing.rstrip() + "\n\n" + block) if existing.strip() else block, True


def append_codes(existing, codes, limit=ACCESS_CODES_MAX):
	"""``(text, outcome)``: ``codes`` after the existing Access Codes with " | ", when they fit.

	``outcome`` is ``"added"``, ``"present"``, ``"too_long"`` or ``""`` (nothing to add).
	"""
	codes = str(codes or "").strip()
	existing = str(existing or "").strip()
	if not codes:
		return existing, ""
	if codes in existing:
		return existing, "present"
	combined = f"{existing} | {codes}" if existing else codes
	if len(combined) > limit:
		return existing, "too_long"
	return combined, "added"


def profile_changes(profile, item, today):
	"""What applying ``item`` writes on ``profile``: ``({field: new value}, [what, for the note])``."""
	header = f"{PROVENANCE} (“{item.get('title')}”, copied {today.isoformat()})"
	changes, notes = {}, []
	links_text, added = merge_links(profile.get(LINKS_FIELD), _lines(item.get("proposed_links")))
	if added:
		changes[LINKS_FIELD] = links_text
		notes.append(f"{len(added)} Sites link(s) added")
	instructions = str(item.get("proposed_instructions") or "").strip()
	codes_text, codes_outcome = append_codes(profile.get(CODES_FIELD), item.get("proposed_access_codes"))
	if codes_outcome == "added":
		changes[CODES_FIELD] = codes_text
		notes.append("access codes added")
	elif codes_outcome == "too_long":
		codes = str(item.get("proposed_access_codes") or "").strip()
		if codes not in instructions:
			instructions = (instructions + "\n\n" if instructions else "") + f"Access details: {codes}"
		notes.append("access codes did not fit Access Codes (140 characters): kept in Special instructions")
	elif codes_outcome == "present":
		notes.append("access codes already there")
	text, appended = append_block(profile.get(INSTRUCTIONS_FIELD), instructions, header)
	if appended:
		changes[INSTRUCTIONS_FIELD] = text
		notes.append("special instructions appended")
	elif instructions:
		notes.append("special instructions already there")
	return changes, notes


def _lines(text):
	return [line.strip() for line in str(text or "").split("\n") if line.strip()]


def plan_token(kind, cutover, rows):
	"""A short hash of exactly what a dry run said would change: confirming needs the same one."""
	data = {
		"kind": kind,
		"cutover": str(cutover or ""),
		"rows": sorted(
			[[r.get("item"), r.get("series_id"), r.get("before"), r.get("after")] for r in rows],
			key=lambda r: str(r[1]),
		),
	}
	return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------- Google


def _events_path(calendar_id, event_id=None):
	path = f"/calendars/{quote(calendar_id, safe='')}/events"
	if event_id:
		path += f"/{quote(event_id, safe='')}"
	return path


def list_calendar(client, calendar_id):
	"""Every event of a calendar with recurring series as masters (``singleEvents=false``).

	Returns ``{"items", "time_zone", "complete"}``; ``complete`` is False when the page limit stopped it.
	"""
	items, page, tz = [], None, ""
	for _ in range(LIST_PAGES):
		params = {"singleEvents": "false", "showDeleted": "false", "maxResults": 250}
		if page:
			params["pageToken"] = page
		answer = client.request("GET", _events_path(calendar_id), params=params) or {}
		tz = tz or answer.get("timeZone") or ""
		items.extend(answer.get("items") or [])
		page = answer.get("nextPageToken")
		if not page:
			return {"items": items, "time_zone": tz, "complete": True}
	return {"items": items, "time_zone": tz, "complete": False}


def runs_past(client, calendar_id, event_id, cutover, tz):
	"""Whether Google still expands a series to an occurrence whose *original* day is on or after the
	cutover. An occurrence dragged past the cutover from earlier does not count: it says nothing about
	the rule."""
	moment = datetime.datetime.combine(cutover, datetime.time(0, 0), tzinfo=tz or datetime.timezone.utc)
	answer = (
		client.request(
			"GET",
			_events_path(calendar_id, event_id) + "/instances",
			params={"timeMin": moment.isoformat(), "maxResults": 50, "showDeleted": "false"},
		)
		or {}
	)
	for instance in answer.get("items") or []:
		day = _event_day(instance.get("originalStartTime") or instance.get("start"))
		if day and day >= cutover:
			return True
	return False


def _organized_here(event, calendar_id):
	"""False when this calendar holds only an invitation copy: patching that copy would not end the
	organizer's series."""
	organizer = (event or {}).get("organizer") or {}
	if not organizer:
		return True
	return bool(organizer.get("self")) or organizer.get("email") == calendar_id


# ---------------------------------------------------------------------- guards


def maintenance_calendar(doc=None):
	return engine.calendar_ids(doc).get("maintenance") or ""


def require_connected(doc=None):
	"""The scan's guard: an authorized account, Google Settings usable, a Maintenance calendar id.
	The master switch is not needed to *read*."""
	doc = doc or engine.settings()
	if not google_api.stored_refresh_token() or not google_api.google_settings_ready():
		frappe.throw(frappe._("Authorize a Google account on Calendar Sync Settings first."))
	calendar_id = maintenance_calendar(doc)
	if not calendar_id:
		frappe.throw(frappe._("Fill in the Maintenance calendar ID on Calendar Sync Settings first."))
	return calendar_id


def require_writable(doc=None):
	"""The guard for anything that writes to Google: the master switch on, an authorized account and
	the Maintenance calendar id."""
	doc = doc or engine.settings()
	if not engine.is_on(doc):
		frappe.throw(
			frappe._(
				"Switch calendar sync on (with an authorized account) before ending the old series. "
				"The dry run needs it too, so what it shows is what would happen."
			)
		)
	calendar_id = maintenance_calendar(doc)
	if not calendar_id:
		frappe.throw(frappe._("Fill in the Maintenance calendar ID on Calendar Sync Settings first."))
	return calendar_id


def _today():
	from frappe.utils import getdate, nowdate

	return getdate(nowdate())


def _now():
	from frappe.utils import now_datetime

	return now_datetime()


def _user():
	return frappe.session.user


# ---------------------------------------------------------------------- step 1: scan


def _short_site(title):
	try:
		from erpnext_enhancements.api.maintenance_planner import short_site_name

		return short_site_name(title) if title else ""
	except Exception:
		return title or ""


def _address_text(row):
	try:
		from erpnext_enhancements.workforce.sites import project_address_text

		return project_address_text(row) or ""
	except Exception:
		return (row.get(ADDRESS_FIELD) or "").strip()


def _has_column(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def profile_candidates():
	"""Every Maintenance Profile with what matching needs: its project's names and address."""
	profiles = frappe.get_all(PROFILE, fields=["name", "project", "customer"], limit_page_length=0)
	projects = {}
	names = [p.get("project") for p in profiles if p.get("project")]
	if names:
		fields = ["name", "project_name"]
		fields += [c for c in (ADDRESS_FIELD, "custom_customer__lead_address") if _has_column(PROJECT, c)]
		for row in frappe.get_all(
			PROJECT, filters={"name": ["in", names]}, fields=fields, limit_page_length=0
		):
			projects[row.get("name")] = row
	out = []
	for profile in profiles:
		project = projects.get(profile.get("project")) or {}
		title = project.get("project_name") or ""
		labels = []
		for value in (title, _short_site(title), profile.get("customer"), profile.get("project")):
			value = str(value or "").strip()
			if value and value not in labels:
				labels.append(value)
		out.append(
			{
				"profile": profile.get("name"),
				"project": profile.get("project"),
				"label": _short_site(title) or profile.get("project") or profile.get("name"),
				"names": labels,
				"address": _address_text(project) if project else "",
				"project_address": (project.get(ADDRESS_FIELD) or "").strip(),
			}
		)
	return out


#: Facts about the series itself: refreshed on every scan, whatever the row's status.
FACT_FIELDS = (
	"title",
	"calendar_id",
	"recurrence",
	"first_date",
	"all_day",
	"time_zone",
	"location",
	"html_link",
	"moved_occurrences",
)
#: What a person reviews: refreshed only while the row is Pending.
PROPOSAL_FIELDS = (
	"proposed_profile",
	"match_quality",
	"match_note",
	"proposed_links",
	"proposed_instructions",
	"proposed_access_codes",
	"proposed_address",
	"current_address",
	"original_description",
)


def _row_values(series, match, candidate, calendar_id, moved):
	facts = {
		"title": series["title"][:140],
		"calendar_id": calendar_id,
		"recurrence": "\n".join(series["recurrence"]),
		"first_date": series["first_date"],
		"all_day": 1 if series["all_day"] else 0,
		"time_zone": series["time_zone"],
		"location": series["location"],
		"html_link": series["html_link"],
		"moved_occurrences": int(moved or 0),
	}
	proposal = {
		"proposed_profile": match["profile"],
		"match_quality": match["quality"],
		"match_note": match["note"],
		"proposed_links": "\n".join(series["links"]),
		"proposed_instructions": series["instructions"],
		"proposed_access_codes": series["codes"],
		"proposed_address": proposed_address(series["location"], candidate),
		"current_address": (candidate or {}).get("address") or "",
		"original_description": series["description_text"],
	}
	return facts, proposal


def _upsert(existing, series, match, candidate, calendar_id, moved, now):
	"""Create or refresh one review row. Returns ``"created"``, ``"refreshed"`` or ``"kept"``."""
	facts, proposal = _row_values(series, match, candidate, calendar_id, moved)
	if existing is None:
		doc = frappe.new_doc(ITEM)
		doc.series_id = series["series_id"]
		doc.status = PENDING
		doc.end_at_cutover = 0 if series["state"] == ALREADY_ENDED else 1
		doc.series_state = series["state"]
		for field, value in {**facts, **proposal}.items():
			doc.set(field, value)
		doc.profile = match["profile"]
		doc.project = match["project"]
		doc.last_scanned = now
		doc.gone_from_calendar = 0
		doc.flags.ignore_permissions = True
		doc.insert()
		return "created"
	doc = frappe.get_doc(ITEM, existing)
	for field, value in facts.items():
		doc.set(field, value)
	if doc.get("series_state") != ENDED:
		doc.series_state = series["state"]
	doc.last_scanned = now
	doc.gone_from_calendar = 0
	outcome = "kept"
	if doc.get("status") == PENDING:
		by_hand = doc.get("match_quality") == BY_HAND and bool(doc.get("profile"))
		for field, value in proposal.items():
			doc.set(field, value)
		if by_hand:
			# A person picked the profile by hand: keep it and say so, whatever the matcher thinks now.
			doc.match_quality = BY_HAND
		else:
			doc.profile = match["profile"]
			doc.project = match["project"]
		outcome = "refreshed"
	doc.flags.ignore_permissions = True
	doc.save()
	return outcome


def scan(include_ended=False, client=None, today=None):
	"""Step 1: read the Maintenance calendar and fill the review list. Read-only on Google.

	Returns the counts and the series titles that matched nothing (so a person knows where to look).
	"""
	doc = engine.settings()
	calendar_id = require_connected(doc)
	client = client or google_api.Client()
	today = today or _today()
	now = _now()
	listing = list_calendar(client, calendar_id)
	counts = {
		"events": len(listing["items"]),
		"erpnext": 0,
		"single": 0,
		"cancelled": 0,
		"exception": 0,
		"series": 0,
		"already_ended": 0,
		"created": 0,
		"refreshed": 0,
		"kept": 0,
		"gone": 0,
	}
	masters, moved = [], {}
	for event in listing["items"]:
		kind = classify(event)
		counts[{KIND_OURS: "erpnext", KIND_SERIES: "series"}.get(kind, kind)] += 1
		if kind == KIND_SERIES:
			masters.append(event)
		elif kind == KIND_EXCEPTION:
			master = event.get("recurringEventId")
			moved[master] = moved.get(master, 0) + 1
	candidates = profile_candidates()
	existing = {
		row.get("series_id"): row.get("name")
		for row in frappe.get_all(ITEM, fields=["name", "series_id"], limit_page_length=0)
	}
	seen, unmatched = set(), []
	for event in masters[:MAX_SERIES]:
		series = parse_series(event, today, listing["time_zone"])
		if not series["series_id"]:
			continue
		if series["state"] == ALREADY_ENDED and not include_ended and series["series_id"] not in existing:
			counts["already_ended"] += 1
			continue
		seen.add(series["series_id"])
		match = match_profile(series["title"], series["location"], candidates)
		if match["quality"] != MATCHED:
			unmatched.append(series["title"])
		outcome = _upsert(
			existing.get(series["series_id"]),
			series,
			match,
			match["candidate"],
			calendar_id,
			moved.get(series["series_id"]),
			now,
		)
		counts[outcome] += 1
	if listing["complete"] and len(masters) <= MAX_SERIES:
		# A row whose series is no longer on the calendar (deleted in Google): flagged, never removed.
		master_ids = {e.get("id") for e in masters}
		for series_id, name in existing.items():
			if series_id not in seen and series_id not in master_ids:
				row = frappe.get_doc(ITEM, name)
				if not row.get("gone_from_calendar"):
					row.gone_from_calendar = 1
					row.flags.ignore_permissions = True
					row.save()
					counts["gone"] += 1
	return {
		"counts": counts,
		"unmatched": unmatched,
		"complete": listing["complete"],
		"calendar": calendar_id,
	}


# ---------------------------------------------------------------------- review


def set_status(names, status):
	"""Approve, ignore or send back to Pending. Returns ``{"done": [...], "skipped": [[name, why]]}``.

	Approving needs a profile. An Applied row stays Applied: what it wrote is on the profile now.
	"""
	if status not in (PENDING, APPROVED, IGNORED):
		frappe.throw(frappe._("Unknown status {0}.").format(status))
	done, skipped = [], []
	for name in names or []:
		doc = frappe.get_doc(ITEM, name)
		if doc.get("status") == APPLIED:
			skipped.append([name, "already applied to its profile"])
			continue
		if status == APPROVED and not doc.get("profile"):
			skipped.append([name, "pick a Maintenance Profile first"])
			continue
		if status == APPROVED:
			doc.project = frappe.db.get_value(PROFILE, doc.get("profile"), "project")
		doc.status = status
		doc.reviewed_by = _user() if status != PENDING else None
		doc.reviewed_on = _now() if status != PENDING else None
		doc.flags.ignore_permissions = True
		doc.save()
		done.append(name)
	return {"done": done, "skipped": skipped}


# ---------------------------------------------------------------------- step 2: apply


def _approved(names=None):
	filters = {"status": APPROVED}
	if names:
		filters["name"] = ["in", list(names)]
	return frappe.get_all(ITEM, filters=filters, pluck="name", order_by="title asc", limit_page_length=0)


def _address_plan(item):
	"""``(project, address)`` to write, or ``(None, why not)``."""
	address = str(item.get("proposed_address") or "").strip()[:ADDRESS_MAX]
	project = item.get("project") or frappe.db.get_value(PROFILE, item.get("profile"), "project")
	if not address:
		return None, ""
	if not project or not _has_column(PROJECT, ADDRESS_FIELD):
		return None, "no project to give the address to"
	current = frappe.db.get_value(PROJECT, project, ADDRESS_FIELD)
	if (current or "").strip():
		return None, "the project already has an address: kept"
	return project, address


def apply_approved(confirm=False, names=None, today=None):
	"""Step 2: write each Approved row to its profile (``confirm``), or say what would be written.

	Returns ``{"rows": [{item, title, profile, notes, address, error}], "applied": n}``. One row that
	fails does not stop the rest; it stays Approved with its error shown.
	"""
	today = today or _today()
	rows, applied = [], 0
	for name in _approved(names):
		item = frappe.get_doc(ITEM, name)
		row = {
			"item": name,
			"title": item.get("title"),
			"profile": item.get("profile"),
			"notes": [],
			"address": "",
			"error": "",
		}
		try:
			if not item.get("profile"):
				raise ValueError("no Maintenance Profile picked")
			profile = frappe.get_doc(PROFILE, item.get("profile"))
			changes, notes = profile_changes(profile, item, today)
			project, address = _address_plan(item)
			row["notes"] = notes
			row["address"] = f"site address set on {project}" if project else address
			if confirm:
				if changes:
					for field, value in changes.items():
						profile.set(field, value)
					profile.save()
					try:
						profile.add_comment(
							"Info",
							f"{PROVENANCE}: copied from the series “{item.get('title')}” ({name}).",
						)
					except Exception:
						pass
				if project:
					frappe.db.set_value(PROJECT, project, ADDRESS_FIELD, address)
					frappe.enqueue(
						GEOCODE_JOB, queue="short", project=project, force=True, enqueue_after_commit=True
					)
				item.status = APPLIED
				item.applied_by = _user()
				item.applied_on = _now()
				item.apply_note = (
					"; ".join(notes + ([row["address"]] if row["address"] else [])) or "nothing new"
				)
				item.flags.ignore_permissions = True
				item.save()
				applied += 1
		except Exception as exc:
			readable = isinstance(exc, ValueError | getattr(frappe, "ValidationError", ValueError))
			row["error"] = (str(exc)[:200] if readable else "") or type(exc).__name__
			if confirm:
				frappe.log_error(
					title="Calendar cutover: an approved item could not be applied",
					message=f"{name}: {row['error']}",
				)
		rows.append(row)
	return {"rows": rows, "applied": applied, "dry_run": not confirm}


# ---------------------------------------------------------------------- step 3: end the series


def _eligible(row):
	"""Why a review row is not ended, or "" when it is."""
	if row.get("status") == PENDING:
		return "not reviewed yet (Pending)"
	if not int(row.get("end_at_cutover") or 0):
		return "End at cutover is unticked"
	if row.get("series_state") == ENDED:
		return "already ended at cutover (Undo first to end it again)"
	if int(row.get("gone_from_calendar") or 0):
		return "no longer on the calendar"
	return ""


def _parse_cutover(cutover_date, today):
	from frappe.utils import getdate

	if not cutover_date:
		frappe.throw(frappe._("Pick the cutover date."))
	cutover = getdate(cutover_date)
	if cutover < today:
		frappe.throw(
			frappe._("The cutover date cannot be in the past: occurrences already past must stay as history.")
		)
	if (cutover - today).days > MAX_CUTOVER_DAYS_AHEAD:
		frappe.throw(frappe._("Pick a cutover date within a year."))
	return cutover


ITEM_FIELDS = ["name", "series_id", "title", "status", "end_at_cutover", "series_state", "gone_from_calendar"]


def end_plan(cutover_date, client=None, today=None):
	"""The dry run of step 3: exactly what :func:`end_series` would change. Reads Google, writes
	nothing anywhere. Needs only a connected account: the switch is checked when it is confirmed."""
	doc = engine.settings()
	calendar_id = require_connected(doc)
	today = today or _today()
	cutover = _parse_cutover(cutover_date, today)
	client = client or google_api.Client()
	listing = list_calendar(client, calendar_id)
	masters, moved_after = {}, {}
	for event in listing["items"]:
		kind = classify(event)
		if kind == KIND_SERIES:
			masters[event.get("id")] = event
		elif kind == KIND_EXCEPTION:
			day = _event_day(event.get("originalStartTime") or event.get("start"))
			if day and day >= cutover:
				master = event.get("recurringEventId")
				moved_after[master] = moved_after.get(master, 0) + 1
	rows = frappe.get_all(ITEM, fields=ITEM_FIELDS, order_by="title asc", limit_page_length=0)
	known = {row.get("series_id") for row in rows}
	changes, unchanged, skipped = [], [], []
	for row in rows:
		reason = _eligible(row)
		event = masters.get(row.get("series_id"))
		if not reason and event is None:
			reason = "not found on the calendar as a recurring series"
		if not reason and not _organized_here(event, calendar_id):
			organizer = ((event.get("organizer") or {}).get("email")) or "another calendar"
			reason = f"only an invitation copy here: end it on {organizer}"
		if reason:
			skipped.append({"item": row.get("name"), "title": row.get("title"), "reason": reason})
			continue
		start = event.get("start") or {}
		all_day = bool(start.get("date"))
		tz = zone(start.get("timeZone") or listing["time_zone"])
		recurrence = list(event.get("recurrence") or [])
		_head, parts = parse_rule(rrule_line(recurrence))
		past = None
		if "COUNT" in dict(parts) and "UNTIL" not in dict(parts):
			past = runs_past(client, calendar_id, event.get("id"), cutover, tz)
		result = rewrite_recurrence(recurrence, cutover, all_day, tz, past)
		entry = {"item": row.get("name"), "series_id": event.get("id"), "title": row.get("title")}
		if result["error"]:
			skipped.append({**entry, "reason": result["error"]})
		elif not result["change"]:
			unchanged.append({**entry, "reason": result["note"]})
		else:
			changes.append(
				{
					**entry,
					"before": recurrence,
					"after": result["after"],
					"note": result["note"],
					"moved_after": moved_after.get(event.get("id"), 0),
				}
			)
	not_reviewed = []
	for series_id, event in masters.items():
		if series_id not in known:
			series = parse_series(event, today, listing["time_zone"])
			if series["state"] == RUNNING:
				not_reviewed.append(series["title"])
	return {
		"cutover": str(cutover),
		"last_day": str(cutover - datetime.timedelta(days=1)),
		"calendar": calendar_id,
		"changes": changes,
		"unchanged": unchanged,
		"skipped": skipped,
		"not_on_review_list": sorted(not_reviewed),
		"complete": listing["complete"],
		"sync_on": engine.is_on(doc),
		"token": plan_token("end", cutover, changes),
	}


def _log(change, action, cutover, before, after, result, error=""):
	"""One row of Calendar Cutover Log: what a series' recurrence was, and what it became."""
	log = frappe.new_doc(LOG)
	log.item = change.get("item")
	log.series_id = change.get("series_id")
	log.title = (change.get("title") or "")[:140]
	log.action = action
	log.cutover_date = cutover
	log.before_recurrence = json.dumps(before or [])
	log.after_recurrence = json.dumps(after or [])
	log.result = result
	log.error = error
	log.undone = 0
	log.done_by = _user()
	log.done_on = _now()
	log.flags.ignore_permissions = True
	log.insert()
	return log


def _patch(client, calendar_id, event_id, recurrence):
	return client.request(
		"PATCH",
		_events_path(calendar_id, event_id),
		params={"sendUpdates": "none"},
		body={"recurrence": recurrence},
	)


def end_series(cutover_date, confirm=False, token=None, client=None, today=None):
	"""Step 3. Without ``confirm`` it is :func:`end_plan` (the dry run). With it, it needs the dry
	run's ``token``, re-plans, and refuses unless the plan is still exactly the one shown."""
	client = client or google_api.Client()
	plan = end_plan(cutover_date, client=client, today=today)
	if not confirm:
		plan["dry_run"] = True
		return plan
	require_writable()
	if not token or token != plan["token"]:
		frappe.throw(
			frappe._(
				"The calendar or the review list changed since the dry run, so it no longer shows exactly "
				"what would happen. Run the dry run again."
			)
		)
	done, failed = [], []
	for change in plan["changes"]:
		try:
			answer = _patch(client, plan["calendar"], change["series_id"], change["after"])
		except google_api.GoogleAuthError as exc:
			_log(change, "End", plan["cutover"], change["before"], change["after"], "Failed", exc.describe())
			frappe.throw(frappe._("Google refused the sign-in: {0}").format(exc.describe()))
		except google_api.GoogleCalendarError as exc:
			_log(change, "End", plan["cutover"], change["before"], change["after"], "Failed", exc.describe())
			failed.append({**change, "error": exc.describe()})
			continue
		written = (answer or {}).get("recurrence") or change["after"]
		_log(change, "End", plan["cutover"], change["before"], written, "Done")
		item = frappe.get_doc(ITEM, change["item"])
		item.series_state = ENDED
		item.ended_for = plan["cutover"]
		item.recurrence = "\n".join(written)
		item.flags.ignore_permissions = True
		item.save()
		done.append(change)
	return {**plan, "dry_run": False, "done": done, "failed": failed}


# ---------------------------------------------------------------------- step 4: undo


def undo_plan(client=None):
	"""The dry run of :func:`undo_end`: each series this tool ended and has not restored, and whether
	its rule is still the one we wrote (only then is it restored).

	Needs a connected account, **not** the switch: undo is the way back when the sync itself is the
	problem and has been switched off."""
	doc = engine.settings()
	calendar_id = require_connected(doc)
	client = client or google_api.Client()
	logs = frappe.get_all(
		LOG,
		filters={"action": "End", "result": "Done", "undone": 0},
		fields=[
			"name",
			"item",
			"series_id",
			"title",
			"cutover_date",
			"before_recurrence",
			"after_recurrence",
		],
		order_by="creation desc",
		limit_page_length=0,
	)
	latest = {}
	for log in logs:
		latest.setdefault(log.get("series_id"), log)
	changes, skipped = [], []
	for series_id, log in latest.items():
		entry = {
			"item": log.get("item"),
			"series_id": series_id,
			"title": log.get("title"),
			"log": log.get("name"),
		}
		before = json.loads(log.get("before_recurrence") or "[]")
		after = json.loads(log.get("after_recurrence") or "[]")
		try:
			event = client.request("GET", _events_path(calendar_id, series_id)) or {}
		except google_api.GoogleAuthError:
			raise
		except google_api.GoogleCalendarError as exc:
			skipped.append({**entry, "reason": f"could not read it: {exc.describe()}"})
			continue
		if is_erpnext_event(event) or event.get("status") == "cancelled":
			skipped.append({**entry, "reason": "it is no longer a hand-made series on the calendar"})
			continue
		current = list(event.get("recurrence") or [])
		if current == before:
			skipped.append({**entry, "reason": "already back to its original rule", "restored": True})
			continue
		if current != after:
			skipped.append(
				{
					**entry,
					"reason": "its rule changed in Google after it was ended: put it back by hand. Original: "
					+ " / ".join(before),
				}
			)
			continue
		changes.append({**entry, "before": after, "after": before})
	return {
		"calendar": calendar_id,
		"changes": changes,
		"skipped": skipped,
		"token": plan_token("undo", "", changes),
	}


def undo_end(confirm=False, token=None, client=None):
	"""Step 4: put back the original rules (``confirm`` + the dry run's ``token``), or the dry run."""
	client = client or google_api.Client()
	plan = undo_plan(client=client)
	if not confirm:
		plan["dry_run"] = True
		return plan
	if not token or token != plan["token"]:
		frappe.throw(frappe._("Something changed since the dry run. Run it again."))
	done, failed = [], []
	for change in plan["changes"]:
		try:
			_patch(client, plan["calendar"], change["series_id"], change["after"])
		except google_api.GoogleAuthError as exc:
			frappe.throw(frappe._("Google refused the sign-in: {0}").format(exc.describe()))
		except google_api.GoogleCalendarError as exc:
			_log(change, "Undo", None, change["before"], change["after"], "Failed", exc.describe())
			failed.append({**change, "error": exc.describe()})
			continue
		_log(change, "Undo", None, change["before"], change["after"], "Done")
		frappe.db.set_value(LOG, change["log"], "undone", 1)
		if change.get("item") and frappe.db.exists(ITEM, change["item"]):
			item = frappe.get_doc(ITEM, change["item"])
			item.series_state = RUNNING
			item.ended_for = None
			item.recurrence = "\n".join(change["after"])
			item.flags.ignore_permissions = True
			item.save()
		done.append(change)
	# A series somebody already put back by hand: its End row is closed too, so it is not offered again.
	for skip in plan["skipped"]:
		if skip.get("restored"):
			frappe.db.set_value(LOG, skip["log"], "undone", 1)
	return {**plan, "dry_run": False, "done": done, "failed": failed}

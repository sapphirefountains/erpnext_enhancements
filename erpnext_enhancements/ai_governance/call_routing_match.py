# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Inbound call routing — the decision, as a pure function.

**This module is deliberately dependency-free.** No ``frappe``, no ``requests``, stdlib
only. It exists because the routing decision has to be made in *Triton*, not here: the
Twilio webhook that builds the ``<Dial>`` is Triton's, and Triton already fetches this
app's ``get_telephony_routing`` on a 60-second cache with a prefetch that runs while the
caller listens to the IVR greeting, precisely so the dial decision never waits on an
ERPNext round trip. Making ERPNext decide per call would put a blocking HTTP request
inside that webhook, which is the failure Triton's own CLAUDE.md records as having frozen
its event loop once.

So ERPNext owns the *configuration* and Triton owns the *execution*, and this file is the
contract between them. It is copied into Triton as ``app/core/call_routing.py``, and both
copies are pinned by the same ``tests/data/call_routing_vectors.json`` — identical bytes in
both repos. Change the behaviour on one side without the other and the other side's suite
goes red, which is the only cheap defence against two implementations drifting.

**Keep it pure.** The moment this imports ``frappe`` it stops being portable, the Triton
copy has to be hand-maintained, and the vectors stop proving anything.

Two decisions encoded here that will otherwise surprise somebody:

* **A matching rule is authoritative about who rings.** If it were additive, a rule could
  never *narrow* anything and "send Saturday calls to Brian only" would be inexpressible.
  The safety valve is per-rule: ``also_ring_softphones`` defaults on, so a rule written
  without thinking about it still rings the desk. Unticking it is the deliberate choice.
* **No match means today's behaviour**, not silence: every softphone plus the default
  forward number. Same for ``paused``, for an empty rule set, and for anything that
  raises. A configuration mistake must never route every caller to voicemail — the same
  fail-open direction as Triton's ``_within_business_hours()``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: Bumped when the payload shape changes incompatibly. Triton refuses to act on a version
#: it does not know and falls back to its env config, so an ERPNext deploy can never hand
#: an older gateway a payload it will silently misread.
SCHEMA_VERSION = 1

# Schedule presets. Stored as the literal Select option so the payload reads the way the
# form reads; the weekday sets live in SCHEDULE_DAYS below.
SCHEDULE_ANY = "Any time"
SCHEDULE_EVERY_DAY = "Every day"
SCHEDULE_WEEKDAYS = "Weekdays"
SCHEDULE_WEEKENDS = "Weekends"
SCHEDULE_CUSTOM = "Custom days"

#: Python ``weekday()`` numbering — Monday is 0.
SCHEDULE_DAYS: dict[str, list[int]] = {
	SCHEDULE_EVERY_DAY: [0, 1, 2, 3, 4, 5, 6],
	SCHEDULE_WEEKDAYS: [0, 1, 2, 3, 4],
	SCHEDULE_WEEKENDS: [5, 6],
}

#: Free-text weekday parsing for the "Custom days" field, e.g. "Mon & Wed", "Tue/Thu".
#: Same token table as ``api/maintenance_dispatch.py`` — duplicated rather than imported
#: because that module imports frappe and this one must not.
WEEKDAY_TOKENS = {
	"monday": 0, "mon": 0,
	"tuesday": 1, "tue": 1, "tues": 1,
	"wednesday": 2, "wed": 2, "weds": 2,
	"thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
	"friday": 4, "fri": 4,
	"saturday": 5, "sat": 5,
	"sunday": 6, "sun": 6,
}

CALLER_ANY = "Any caller"
CALLER_KNOWN = "Known customer"
CALLER_UNKNOWN = "Unknown caller"
CALLER_NUMBERS = "Matching numbers"

TARGET_NUMBER = "number"
TARGET_CLIENT = "client"
TARGET_ACCOUNT_MANAGER = "account_manager"
TARGET_VOICEMAIL = "voicemail"

#: Twilio rings at most 10 endpoints on one ``<Dial>``. Compiled plans are capped here so
#: the overflow is decided by the rule's declared order rather than by whatever Triton
#: happens to append last.
MAX_DIAL_LEGS = 10

#: Fallback ring duration. Twilio's own ``<Dial>`` default is 30s and nothing in Triton has
#: ever passed a ``timeout``, so this keeps today's behaviour when nothing is configured.
DEFAULT_RING_SECONDS = 30

#: Ceiling on the ring timer. Twilio permits up to 600s, but a caller left listening to a
#: ringing tone for minutes has already hung up.
MAX_RING_SECONDS = 120


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

def digits(raw: Any) -> str:
	"""Just the digits of ``raw``. ``None`` and non-strings give ``""``."""
	return re.sub(r"\D", "", str(raw or ""))


def national(raw: Any) -> str:
	"""The 10-digit North American national number, or ``""``.

	``"+1 (702) 521-4969"``, ``"7025214969"`` and ``"17025214969"`` all reduce to
	``"7025214969"``. Anything that is not recognisably NANP returns ``""``; callers treat
	that as "cannot compare" rather than "no match", which matters because
	:func:`number_matches` must not claim a match it cannot justify.
	"""
	d = digits(raw)
	if len(d) == 11 and d.startswith("1"):
		d = d[1:]
	return d if len(d) == 10 else ""


def to_e164(raw: Any) -> str:
	"""``raw`` as a dialable E.164 string, or ``""`` when it cannot be made one.

	Production stores ``Employee.cell_number`` as bare digits (``"7025214969"``), which
	Twilio will not reliably accept on a ``<Number>``; everything therefore goes through
	here on the way into a dial plan. An explicit ``+`` is trusted and passed through so a
	non-US number is not mangled into a US one.
	"""
	text = str(raw or "").strip()
	if text.startswith("+"):
		d = digits(text)
		return "+" + d if 8 <= len(d) <= 15 else ""
	nat = national(text)
	return "+1" + nat if nat else ""


def number_matches(from_number: Any, patterns: Iterable[Any]) -> bool:
	"""True when ``from_number`` starts with any of ``patterns``.

	Comparison is on the 10-digit national number, so a partial entry is a **prefix**:
	``801`` matches every 801 number, ``8015551234`` matches exactly one. An empty pattern
	list never matches — a "Matching numbers" rule with nothing typed in is inert rather
	than a catch-all, which is the safer reading of an unfinished rule.
	"""
	caller = national(from_number)
	if not caller:
		return False
	for pattern in patterns or []:
		prefix = digits(pattern)
		if len(prefix) == 11 and prefix.startswith("1"):
			prefix = prefix[1:]
		if prefix and caller.startswith(prefix):
			return True
	return False


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def parse_weekdays(text: Any) -> list[int]:
	"""Weekday numbers parsed out of free text, sorted. ``"Mon & Wed"`` -> ``[0, 2]``."""
	if not text:
		return []
	found = {
		WEEKDAY_TOKENS[tok]
		for tok in re.split(r"[^a-z]+", str(text).lower())
		if tok in WEEKDAY_TOKENS
	}
	return sorted(found)


def schedule_days(schedule: Any, custom_days: Any = None) -> list[int]:
	"""The weekdays a schedule covers. ``[]`` means "every day, no day filter"."""
	name = (schedule or SCHEDULE_ANY).strip()
	if name == SCHEDULE_CUSTOM:
		return parse_weekdays(custom_days)
	return list(SCHEDULE_DAYS.get(name, []))


def parse_hhmm(value: Any) -> int | None:
	"""``"17:30"`` (or ``"17:30:00"``) as minutes past local midnight. ``None`` if unset.

	Frappe hands a Time field back as ``"HH:MM:SS"`` or as a ``timedelta``; both arrive
	here as strings once the payload has been through JSON, so only the string form is
	handled and anything unparseable degrades to ``None`` — which :func:`in_window` reads
	as "no bound", not as "never".
	"""
	if value is None or value == "":
		return None
	text = str(value).strip()
	m = re.match(r"^(\d{1,2}):(\d{2})", text)
	if not m:
		return None
	hour, minute = int(m.group(1)), int(m.group(2))
	if not (0 <= hour <= 24 and 0 <= minute <= 59):
		return None
	return min(hour * 60 + minute, 24 * 60)


def in_window(minutes: int, start: int | None, end: int | None) -> bool:
	"""Is ``minutes`` inside ``[start, end)``, wrapping past midnight?

	``start > end`` means the window **wraps** — ``17:00`` to ``08:00`` is the evening plus
	the following early morning — rather than meaning "never", which is what a naive
	``start <= m < end`` would silently produce and is the easiest bug to ship here.

	Note what wrapping does *not* do: the day filter is still applied to the current day.
	A "Weekdays, 17:00-08:00" rule covers Monday-Friday evenings and Monday-Friday early
	mornings. It does **not** stretch Friday evening's window into Saturday morning. That
	reading is the explainable one ("on these days, between these times") and it is stated
	in the field description on the form for the same reason.
	"""
	if start is None and end is None:
		return True
	if start is None:
		return minutes < end
	if end is None:
		return minutes >= start
	if start == end:
		return True
	if start < end:
		return start <= minutes < end
	return minutes >= start or minutes < end


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def rule_matches(rule: dict[str, Any], facts: dict[str, Any]) -> str | None:
	"""``None`` when ``rule`` matches ``facts``; otherwise why it did not.

	Returning the *reason* rather than a bool is not decoration. "Why did this call go
	there" is the question that gets asked after a missed call, and a decision that cannot
	answer it sends somebody reading TwiML in a Cloud Run log.
	"""
	intent = (rule.get("intent") or "Any").strip()
	if intent != "Any" and intent != (facts.get("intent") or "General"):
		return f"intent is {facts.get('intent') or 'General'}, rule wants {intent}"

	scope = (rule.get("caller_scope") or CALLER_ANY).strip()
	if scope == CALLER_KNOWN and not facts.get("known_caller"):
		return "caller is not a known customer"
	if scope == CALLER_UNKNOWN and facts.get("known_caller"):
		return "caller is a known customer"
	if scope == CALLER_NUMBERS and not number_matches(
		facts.get("from_number"), rule.get("caller_numbers") or []
	):
		return "caller number is not on the rule's list"

	if rule.get("skip_on_holidays") and facts.get("is_holiday"):
		return "today is a holiday and the rule skips holidays"

	schedule = (rule.get("schedule") or SCHEDULE_ANY).strip()
	if schedule != SCHEDULE_ANY:
		days = rule.get("days")
		if days is None:
			days = schedule_days(schedule, rule.get("custom_days"))
		if days and facts.get("weekday") not in days:
			return f"weekday {facts.get('weekday')} is not in {days}"
		start = parse_hhmm(rule.get("from_time"))
		end = parse_hhmm(rule.get("to_time"))
		if not in_window(int(facts.get("minutes") or 0), start, end):
			return f"time is outside {rule.get('from_time')}-{rule.get('to_time')}"

	return None


def _fallback(routing: dict[str, Any], reason: str) -> dict[str, Any]:
	"""The plan used whenever no rule owns the call: exactly today's behaviour."""
	default_number = to_e164(routing.get("default_forward_number"))
	return {
		"rule": None,
		"reason": reason,
		"ring_seconds": int(routing.get("default_ring_seconds") or 0),
		"voicemail": False,
		"numbers": [default_number] if default_number else [],
		"clients": [],
		"include_softphones": True,
		"account_manager": True,
		"considered": [],
	}


def decide(routing: Any, facts: dict[str, Any]) -> dict[str, Any]:
	"""The dial plan for one inbound call.

	Args:
		routing: the ``routing`` block of ``get_telephony_routing``'s payload.
		facts: ``weekday`` (Mon=0), ``minutes`` past local midnight, ``intent``,
			``from_number``, ``known_caller``, ``is_holiday``. The caller computes the
			first two from its own timezone handling — ERPNext uses ``zoneinfo`` and
			Triton uses ``pytz``, and keeping that difference *outside* this function is
			what lets the two copies stay byte-identical.

	Returns a plan the caller executes without further interpretation:

	* ``numbers`` / ``clients`` — PSTN legs and Twilio Client identities, in rule order.
	* ``include_softphones`` — merge in the normal desk/browser fan-out as well.
	* ``account_manager`` — prepend the caller's account owner, which only Triton can
	  resolve (it holds the CRM lookup).
	* ``voicemail`` — do not dial at all; play the message and record.
	* ``rule`` / ``reason`` / ``considered`` — the audit trail. Log ``rule``.

	Never raises. Anything unexpected — a payload from the future, a rule that is not a
	dict, a bad type anywhere — comes back as the fallback plan with the reason attached.
	"""
	try:
		if not isinstance(routing, dict):
			return _fallback({}, "no routing configuration")
		version = int(routing.get("schema_version") or 0)
		if version != SCHEMA_VERSION:
			return _fallback(
				routing, f"payload schema {version} is not the {SCHEMA_VERSION} this build understands"
			)
		if routing.get("paused"):
			return _fallback(routing, "call routing is paused")

		rules = routing.get("rules") or []
		if not rules:
			return _fallback(routing, "no rules configured")

		considered: list[dict[str, str]] = []
		for rule in rules:
			if not isinstance(rule, dict):
				continue
			name = str(rule.get("name") or "?")
			why_not = rule_matches(rule, facts)
			if why_not:
				considered.append({"rule": name, "skipped": why_not})
				continue

			targets = [t for t in (rule.get("targets") or []) if isinstance(t, dict)]
			numbers: list[str] = []
			clients: list[str] = []
			voicemail = False
			account_manager = False
			for target in targets:
				kind = (target.get("type") or "").strip()
				value = (target.get("value") or "").strip()
				if kind == TARGET_VOICEMAIL:
					voicemail = True
				elif kind == TARGET_ACCOUNT_MANAGER:
					account_manager = True
				elif kind == TARGET_NUMBER and value:
					numbers.append(value)
				elif kind == TARGET_CLIENT and value:
					clients.append(value)

			include_softphones = bool(rule.get("also_ring_softphones"))

			# A rule that resolves to nothing dialable is a configuration mistake, not an
			# instruction to drop the call — most likely every Employee it names is missing
			# a cell number. Fall through to it rather than answering with silence.
			if not (voicemail or numbers or clients or account_manager or include_softphones):
				considered.append({"rule": name, "skipped": "matched but resolved to no reachable target"})
				continue

			# Voicemail is exclusive: "send these callers to voicemail" cannot also mean
			# "and ring six phones".
			if voicemail:
				numbers, clients, include_softphones, account_manager = [], [], False, False

			ring = int(rule.get("ring_seconds") or 0) or int(routing.get("default_ring_seconds") or 0)
			return {
				"rule": name,
				"reason": "matched",
				"ring_seconds": ring,
				"voicemail": voicemail,
				"numbers": numbers[:MAX_DIAL_LEGS],
				"clients": clients[:MAX_DIAL_LEGS],
				"include_softphones": include_softphones,
				"account_manager": account_manager,
				"considered": considered,
			}

		plan = _fallback(routing, "no rule matched")
		plan["considered"] = considered
		return plan
	except Exception as e:
		return _fallback(routing if isinstance(routing, dict) else {}, f"routing failed open: {e}")

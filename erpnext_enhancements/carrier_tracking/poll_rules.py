# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""When a package is checked, and what a carrier's answer does to its row. No Frappe, no I/O.

``tracking.py`` reads rows, calls the client, and writes back exactly what ``apply_result``
returns. Keeping the decisions here means every limit below is tested on plain dicts
(``tests/test_carrier_tracking_poller.py``).

**The limits, and why these numbers.**

* ``BATCH_SIZE = 25`` packages per hourly run, ``RUN_BUDGET_SECONDS = 120``. One Track call per
  package, so a run is at most 25 calls, far under either carrier's published limits (see
  ``client.py``), and short enough that a deploy's queue flush loses at most one run.
* ``RECHECK_AFTER_HOURS = 2``. Carriers post a few scans a day; checking more often finds nothing
  new. Someone waiting on a truck presses Refresh.
* ``NOT_FOUND_GIVE_UP_DAYS = 7``. A number is often "not found" for a day or two after the label is
  printed and before the carrier scans the box. A week of that, with no answer ever, means the
  number is wrong or the supplier never shipped. **Until the carrier has answered once, "not found"
  is governed by this rule alone** and does not add to ``failure_count``: at one check every two
  hours a week is 84 checks, and the 12-failure rule would otherwise always fire first.
* ``NOT_FOUND_GUESS_GIVE_UP_DAYS = 2``. The same for a number whose carrier was *guessed* from its
  length (a bare 12 or 15 digits, ``carriers.detect``): two days of "not found" and it is probably
  not a FedEx number at all, so the row says so and stops.
* ``REJECTED_GIVE_UP_DAYS = 3``. A package whose every answer is an ambiguous refusal (a 400 or
  404 with a code we cannot place, ``client.SCOPE_NUMBER``) and that has never had a real answer is
  stopped after three days, with a sentence telling the person to check the number and the carrier.
  The typical case is a mistyped number on a carrier picked by hand: UPS answers 400. Three days
  rides out a carrier's bad weekend, and is long after a real label would have been scanned. **How
  this keeps carrier trouble from counting against packages:** a refusal that is clearly about the
  account, the login or our request (``client.SCOPE_CARRIER``) never counts; and when two different
  packages of one carrier get the ambiguous refusal in the same run, ``tracking`` treats it as the
  carrier's and counts it for neither. The ambiguous case left is a carrier with a single package
  due in the run: that refusal is taken as being about the number. If the carrier was in fact at
  fault, a person's Refresh restarts the package as soon as the carrier answers.
* ``MAX_ROW_FAILURES = 12`` answers in a row that were about this package and were not usable
  ("not found" after the carrier had answered before, or a response for this package we could not
  read). At one check every two hours that is at least a day.
* ``STALE_AFTER_DAYS = 21``. No change at all for three weeks: a lost package or a number the
  carrier stopped updating. Polling it forever helps nobody.
* ``MAX_TRACKING_DAYS = 90``. Carriers keep tracking history for about that long.
* ``REFRESH_COOLDOWN_SECONDS = 60``. A Refresh button pressed twice asks the carrier once.

**Two kinds of failure.** A failure that is about the carrier, not the package (the carrier is
down, it said "too many requests", our credentials were refused) never counts against a row and
never stops one: when it clears, every package carries on. The poller stops asking that carrier
for the rest of the run instead. A failure about this package counts, and enough of them stop it.

**"Could not check" never changes a stored status.** Only a carrier's answer does.
"""

import re
from datetime import datetime, timedelta

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import client

BATCH_SIZE = 25
RUN_BUDGET_SECONDS = 120
RECHECK_AFTER_HOURS = 2
NOT_FOUND_GIVE_UP_DAYS = 7
NOT_FOUND_GUESS_GIVE_UP_DAYS = 2
REJECTED_GIVE_UP_DAYS = 3
MAX_ROW_FAILURES = 12
STALE_AFTER_DAYS = 21
MAX_TRACKING_DAYS = 90
REFRESH_COOLDOWN_SECONDS = 60

# The client's outcomes (client.py).
OK = client.OK
NOT_FOUND = client.NOT_FOUND
INVALID = client.INVALID
AUTH_FAILED = client.AUTH_FAILED
RATE_LIMITED = client.RATE_LIMITED
UNAVAILABLE = client.UNAVAILABLE
BAD_RESPONSE = client.BAD_RESPONSE
NOT_CONFIGURED = client.NOT_CONFIGURED
UNSUPPORTED = client.UNSUPPORTED
REJECTED = client.REJECTED
OUTCOME_WORDS = client.OUTCOME_WORDS

#: About the carrier or our setup, not the package: never counted, never stops a row.
CARRIER_LEVEL = frozenset({AUTH_FAILED, RATE_LIMITED, UNAVAILABLE, NOT_CONFIGURED, REJECTED})


def is_number_rejection(result):
	"""A refusal that may be about this one number (``client.SCOPE_NUMBER``)."""
	result = result if isinstance(result, dict) else {}
	return result.get("outcome") == REJECTED and result.get("scope") == client.SCOPE_NUMBER


def blocks_carrier(result):
	"""Whether this answer means the carrier should not be asked again in this run: a carrier-level
	failure, except an ambiguous refusal, which may be about one number only."""
	result = result if isinstance(result, dict) else {}
	return result.get("outcome") in CARRIER_LEVEL and not is_number_rejection(result)


#: About this package: counted towards MAX_ROW_FAILURES.
ROW_LEVEL = frozenset({NOT_FOUND, BAD_RESPONSE})

# Why polling stopped (Tracked Package.stop_reason). Plain words: the banner shows them.
STOP_DELIVERED = "Delivered."
STOP_INVALID = "The carrier says this is not a valid tracking number."
STOP_NEVER_FOUND = "The carrier has had no record of this number for a week."
STOP_GUESS_NOT_FOUND = (
	"FedEx has no record of this number after two days, so it is probably not a FedEx number. Remove "
	"it and add it again with the carrier picked."
)
STOP_REJECTED = (
	"The carrier has turned this number down every time for three days and never answered for it. "
	"Check that the number and the carrier are right, then remove it and add it again."
)
STOP_FAILING = "The carrier's answers for this number could not be used, many times in a row."
STOP_STALE = "No news from the carrier for three weeks."
STOP_TOO_OLD = "Tracked for 90 days."
STOP_NOT_TRACKABLE = C.NOT_TRACKABLE_REASON


def _dt(value):
	"""A datetime from a datetime, a date or an ISO string; ``None`` for anything else."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime):
		return value.replace(tzinfo=None)
	try:
		return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
	except Exception:
		try:
			return datetime.strptime(str(value)[:10], "%Y-%m-%d")
		except Exception:
			return None


def _older_than(value, now, **delta):
	moment = _dt(value)
	return moment is not None and now - moment > timedelta(**delta)


def is_due(row, now):
	"""Whether the poller should check this row now. ``row`` is a dict of the row's fields."""
	if row.get("polling_stopped"):
		return False
	if row.get("resolved_carrier") not in C.TRACKABLE:
		return False
	if row.get("status") == C.STATUS_LABELS[C.DELIVERED]:
		return False
	last = _dt(row.get("last_checked"))
	return last is None or now - last >= timedelta(hours=RECHECK_AFTER_HOURS)


def pick(rows, now, limit=BATCH_SIZE):
	"""The rows to check this run: due ones, never-checked first, then oldest-checked first."""
	due = [row for row in rows if is_due(row, now)]
	due.sort(key=lambda row: (_dt(row.get("last_checked")) or datetime.min, str(row.get("name") or "")))
	return due[:limit]


def apply_result(row, result, now, manual=False):
	"""The field updates for ``row`` after the client returned ``result``. Pure.

	``manual`` is a person's Refresh: an answer that shows the package still moving starts
	polling again on a row the poller had stopped (but never one that was delivered).
	Returns a dict of Tracked Package fields to write; never includes a field that should keep
	its value.
	"""
	result = result if isinstance(result, dict) else {}
	outcome = result.get("outcome") or BAD_RESPONSE
	updates = {"last_checked": now}
	failures = int(row.get("failure_count") or 0)

	if outcome == OK:
		status = C.STATUS_LABELS.get(result.get("status"), C.STATUS_LABELS[C.UNKNOWN_STATUS])
		new_values = {
			"status": status,
			"status_description": (result.get("description") or "")[:240],
			"estimated_delivery": result.get("estimated_delivery") or None,
			"delivered_on": result.get("delivered_at") or None,
			"last_location": (result.get("last_location") or "")[:140],
			"destination": (result.get("destination") or "")[:140],
		}
		changed = any(_differs(row.get(key), value) for key, value in new_values.items())
		updates.update(new_values)
		updates.update({"last_success": now, "failure_count": 0, "last_error": ""})
		if changed or not row.get("last_change"):
			updates["last_change"] = now
		if status == C.STATUS_LABELS[C.DELIVERED]:
			updates.update({"polling_stopped": 1, "stop_reason": STOP_DELIVERED})
		elif manual and row.get("polling_stopped") and changed:
			updates.update({"polling_stopped": 0, "stop_reason": ""})
		else:
			reason = _age_stop(row, now, updates)
			if reason:
				updates.update({"polling_stopped": 1, "stop_reason": reason})
		return updates

	updates["last_error"] = (result.get("error") or OUTCOME_WORDS.get(outcome, ""))[:500]

	if outcome == INVALID:
		updates.update({"polling_stopped": 1, "stop_reason": STOP_INVALID})
		return updates

	if outcome == UNSUPPORTED:
		updates.update({"polling_stopped": 1, "stop_reason": STOP_NOT_TRACKABLE, "status": C.NOT_TRACKED})
		return updates

	if outcome in CARRIER_LEVEL:
		# Never counted and never a reason to stop, except the 90-day cap (a carrier that has
		# refused us for three months is not going to answer about this package) and an ambiguous
		# refusal for a package that has never had an answer (REJECTED_GIVE_UP_DAYS).
		if (
			is_number_rejection(result)
			and not row.get("last_success")
			and _older_than(row.get("creation"), now, days=REJECTED_GIVE_UP_DAYS)
		):
			updates.update({"polling_stopped": 1, "stop_reason": STOP_REJECTED})
		elif _older_than(row.get("creation"), now, days=MAX_TRACKING_DAYS):
			updates.update({"polling_stopped": 1, "stop_reason": STOP_TOO_OLD})
		return updates

	if outcome == NOT_FOUND and not row.get("last_success"):
		# Never answered yet: only the week (or two days for a guessed carrier) decides.
		guessed = bool(row.get("carrier_guessed"))
		days = NOT_FOUND_GUESS_GIVE_UP_DAYS if guessed else NOT_FOUND_GIVE_UP_DAYS
		if _older_than(row.get("creation"), now, days=days):
			updates.update(
				{"polling_stopped": 1, "stop_reason": STOP_GUESS_NOT_FOUND if guessed else STOP_NEVER_FOUND}
			)
		return updates

	# ROW_LEVEL, and anything unrecognized, which is treated as unreadable.
	failures += 1
	updates["failure_count"] = failures
	if failures >= MAX_ROW_FAILURES:
		updates.update({"polling_stopped": 1, "stop_reason": STOP_FAILING})
	else:
		reason = _age_stop(row, now, updates)
		if reason:
			updates.update({"polling_stopped": 1, "stop_reason": reason})
	return updates


def _age_stop(row, now, updates):
	"""STOP_TOO_OLD or STOP_STALE when the row has been tracked long enough, else ``None``."""
	if _older_than(row.get("creation"), now, days=MAX_TRACKING_DAYS):
		return STOP_TOO_OLD
	last_change = updates.get("last_change") or row.get("last_change")
	if last_change is not None and _older_than(last_change, now, days=STALE_AFTER_DAYS):
		return STOP_STALE
	return None


def _differs(old, new):
	"""Compare a stored value with a new one the way the database would hold them."""
	return _norm(old) != _norm(new)


def _norm(value):
	if value is None:
		return ""
	if isinstance(value, datetime):
		return value.replace(tzinfo=None).isoformat(sep=" ")[:19]
	text = str(value).strip()
	moment = _dt(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", text) else None
	if moment is not None and len(text) > 10:
		return moment.isoformat(sep=" ")[:19]
	return text

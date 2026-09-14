# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""When an inspection is due, and why it is not — WI-075 sub-phase I.

Sub-phase C seeded seventeen milestones carrying a ``trigger_basis``, and until now **nothing
read it**. The catalog said so in its own docstring: *calendar-driven rather than project-stage
driven, so it needs its own scheduling — which sub-phase I owns.* This is that.

The question is "has this been done", not "is it exactly now"
--------------------------------------------------------------

The obvious implementation of a Build Status trigger is ``current == trigger_value``, and it is
wrong in a way that costs an inspection with nothing to see. A project that moves from
``Procurement`` to ``Ready for Install`` in one save — which happens, because the field is a
Select somebody types into, not a workflow — was never equal to ``QA`` at any moment a sweep
looked. The pre-final commissioning check simply never comes up, and the record afterwards is
indistinguishable from a project that has not got there yet.

So the rule is **reached or passed**: a milestone is due once the project's status is at or
beyond the trigger, and stays due until an inspection exists for it. A project that skips a
stage does not skip its inspection; it acquires an overdue one. That also makes the answer
computable at any time from current state, rather than depending on having observed a transition.

The failure direction that matters
-----------------------------------

A status this module does not recognise — renamed, legacy, blank — cannot be placed on the
scale. Returning "not due" there would be the trailing-space failure again: the sweep reports
clean, every milestone looks like one that has not come round yet, and nobody goes looking. So
an unplaceable status is :data:`STATE_UNKNOWN` and is surfaced, not swallowed.

What it deliberately does not do
---------------------------------

**It never decides that an inspection should be created.** It reports that one is due. Generation
stays a deliberate act by a person, for the same reason severity is never guessed: an
auto-generated inspection reads as though somebody decided to inspect.

**It does not hide a milestone that has no checklist.** A due milestone whose template is missing
or not Active is still due, flagged ``blocked``. Only the Build commissioning checklist has ever
been written down; the rest are Sapphire's standard of care and live in people's heads. Making
that gap visible on the record is worth more than a tidy list that omits it.

Imports nothing. Every input is a plain dict or tuple, so the frappe half can build them from
whatever query it likes and the judgement stays asserted on every push.
"""

from datetime import date, datetime, timedelta

#: A milestone whose trigger has been reached and which has no inspection yet.
STATE_DUE = "due"
#: An inspection exists (and, for a calendar milestone, is still inside its interval).
STATE_DONE = "done"
#: The trigger has not been reached yet.
STATE_WAITING = "waiting"
#: No automatic trigger. A person decides when — Design review gates and Events.
STATE_MANUAL = "manual"
#: Does not apply to this project at all.
STATE_SKIPPED = "skipped"
#: The project's status cannot be placed on the scale, so due-ness is not knowable.
STATE_UNKNOWN = "unknown"

TRIGGER_MANUAL = "Manual"
TRIGGER_BUILD_STATUS = "Build Status"
TRIGGER_CALENDAR = "Calendar Interval"


def status_reached(current, trigger, order):
	"""Whether ``current`` is at or beyond ``trigger`` on the ordered Select.

	Returns ``True``, ``False``, or ``None`` when the question cannot be answered — a blank
	status, or one absent from ``order``. ``None`` is not a polite ``False``: see the module
	docstring for why the difference is the whole point.
	"""
	current = (current or "").strip()
	trigger = (trigger or "").strip()
	if not current or not trigger:
		return None
	places = {value: index for index, value in enumerate(order or ())}
	if current not in places or trigger not in places:
		return None
	return places[current] >= places[trigger]


def assess(milestone, context):
	"""One milestone against one project. Returns ``{state, reason, blocked}``.

	``context`` carries ``build_status``, ``status_order``, ``inspection_count``,
	``last_inspection_on``, ``is_multi_day``, ``has_active_template`` and ``today``.
	"""
	if _get(milestone, "disabled"):
		return _result(STATE_SKIPPED, "this milestone is switched off")

	if _get(milestone, "multi_day_only") and not _get(context, "is_multi_day"):
		# Not dropped silently: "why is my mid-event check not showing" deserves an answer, and
		# on an event with no dates recorded the honest answer is that nobody knows how long it
		# runs -- which is a different problem from the check not applying.
		reason = (
			"this check is for multi-day events, and the project has no start and end date recorded"
			if _get(context, "is_multi_day") is None
			else "this check is for multi-day events only"
		)
		return _result(STATE_SKIPPED, reason)

	blocked = not _get(context, "has_active_template")
	basis = _get(milestone, "trigger_basis") or TRIGGER_MANUAL

	if basis == TRIGGER_CALENDAR:
		return _calendar(milestone, context, blocked)

	if basis == TRIGGER_BUILD_STATUS:
		if _get(context, "inspection_count"):
			return _result(STATE_DONE, "an inspection exists for this milestone")
		reached = status_reached(
			_get(context, "build_status"), _get(milestone, "trigger_value"), _get(context, "status_order")
		)
		if reached is None:
			return _result(
				STATE_UNKNOWN,
				"the project's build status is blank or is not one of the known options, so "
				"whether this milestone has come round cannot be worked out",
				blocked,
			)
		if not reached:
			return _result(STATE_WAITING, f"waiting for build status {_get(milestone, 'trigger_value')}")
		return _result(STATE_DUE, f"build status has reached {_get(milestone, 'trigger_value')}", blocked)

	# Manual. Available whenever somebody decides, never announced as due -- which is the
	# correct answer for a design review gate and for an event nobody has scheduled yet.
	if _get(context, "inspection_count"):
		return _result(STATE_DONE, "an inspection exists for this milestone")
	return _result(STATE_MANUAL, "generated when somebody decides it is time", blocked)


def _calendar(milestone, context, blocked):
	"""A recurring check. Due when the last one has aged past its interval, or never happened."""
	interval = _int(_get(milestone, "interval_days"))
	if not interval or interval < 1:
		return _result(STATE_UNKNOWN, "this recurring check has no interval set, so it can never come round")

	last = _as_date(_get(context, "last_inspection_on"))
	today = _as_date(_get(context, "today"))
	if today is None:
		return _result(STATE_UNKNOWN, "no date to measure the interval against")

	if last is None:
		# True, and deliberately marked. Turning this on for the first time would otherwise page
		# somebody about every service project at once -- and "we have never inspected this" is a
		# different conversation from "this one is overdue".
		return _result(STATE_DUE, "no interval check has ever been recorded for this project", blocked, first_time=True)

	if last + timedelta(days=interval) > today:
		return _result(STATE_DONE, f"last checked {last.isoformat()}, next due in {interval} days")
	return _result(STATE_DUE, f"last checked {last.isoformat()}, more than {interval} days ago", blocked)


def due_milestones(pairs):
	"""Filter ``[(milestone, context), ...]`` down to the ones that are due, in input order.

	Blocked milestones are **included**. A due check with no checklist written is still a due
	check, and dropping it would turn a gap in what the company has written down into a gap
	nobody can see.
	"""
	out = []
	for milestone, context in pairs or []:
		verdict = assess(milestone, context)
		if verdict["state"] == STATE_DUE:
			out.append((milestone, verdict))
	return out


def needs_attention(pairs):
	"""Due milestones **plus** the ones whose due-ness could not be worked out.

	The second group is the one a sweep would otherwise never mention, because an unplaceable
	status produces no due item and no error — it produces silence.
	"""
	out = []
	for milestone, context in pairs or []:
		verdict = assess(milestone, context)
		if verdict["state"] in (STATE_DUE, STATE_UNKNOWN):
			out.append((milestone, verdict))
	return out


def notice_due(last_noticed_on, today, every_days=7):
	"""Whether a due milestone should be mentioned to somebody again.

	Never mentioned — yes. Mentioned inside the window — no. The due list is the durable record
	and it is always there to read; the email is only a prompt, and a prompt that arrives daily
	about something the recipient has already decided to leave is how a mailbox rule gets written.
	"""
	last = _as_date(last_noticed_on)
	today = _as_date(today)
	if today is None:
		return False
	if last is None:
		return True
	return last + timedelta(days=max(_int(every_days) or 7, 1)) <= today


def _result(state, reason, blocked=False, first_time=False):
	return {"state": state, "reason": reason, "blocked": bool(blocked), "first_time": bool(first_time)}


def _as_date(value):
	if not value:
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = str(value).strip()
	if not text:
		return None
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
		try:
			return datetime.strptime(text, fmt).date()
		except ValueError:
			continue
	return None


def _int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0


def _get(obj, field, default=None):
	if obj is None:
		return default
	if isinstance(obj, dict):
		return obj.get(field, default)
	return getattr(obj, field, default)

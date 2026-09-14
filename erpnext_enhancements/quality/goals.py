# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Goals, and what a period review is allowed to conclude — WI-075 sub-phase J.

ERPNext already generates a `Quality Review` on a cadence and copies the goal's objectives into
it. What it does not do is put a **number** in one: a review arrives with targets and blank
actuals, and somebody types a verdict. This module is what lets the actual be computed, and —
more importantly — what stops it being computed wrongly in the three ways that all look like
good news.

Three ways a quality metric reports success while meaning nothing
------------------------------------------------------------------

**1. An empty period divides by zero and rounds up to perfect.** First-pass yield over a quarter
with no inspections is *undefined*, not 100%. The obvious implementation returns 100% — or
returns 0 and reads as catastrophic — and either way a quarter in which nobody inspected anything
produces a confident number. :func:`verdict` returns ``Open`` when the sample is empty, and
:func:`compute` says so in its note.

**2. Half the metrics are better when smaller, and nothing on the record says which.** Core's
``Quality Goal Objective`` carries ``objective``, ``target`` and ``uom`` — no direction. Compare
actual against target the obvious way and "NCRs raised: target 2" reads as *failed* every time
the company does well. So direction is **not** a per-row field anybody can mis-set: it comes from
the metric definition in :data:`METRICS`, where it can be got right once.

**3. ``target`` is a `Data` field.** Somebody will type ``95%``, ``<= 2``, ``2 per project`` or
``two``. A target that cannot be read is not a target of zero, and a review that silently treated
it as zero would mark the goal Passed forever. :func:`parse_target` returns ``None``, and the
verdict is ``Open``.

Why this module owns the Annual cadence and nothing else
---------------------------------------------------------

ERPNext's ``quality_review.review()`` runs daily and branches on Daily, Weekly, Monthly and
Quarterly. It has **no Annual branch**, so adding ``Annual`` by Property Setter produces a goal
that generates nothing, forever, with no error.

So the scheduler here handles **Annual only**. Handling any of the other four would create a
second review every day core already created one, on the same goal, and nobody comparing two
identical reviews would guess the cause. :func:`review_due` refuses to answer for the
frequencies core owns, rather than returning ``False`` for them — a ``False`` would be a
correct-looking answer to a question this module must not be asked.

Imports only ``datetime``. Every number a quality dashboard shows eventually traces back through
here, and a metric that is quietly wrong is worse than a metric nobody computed.
"""

from datetime import date, datetime, timedelta

HIGHER_IS_BETTER = "Higher is better"
LOWER_IS_BETTER = "Lower is better"

#: The computable metrics, and the direction each one runs in. Direction lives here rather than
#: on the objective row because it is a property of the measure, not a choice about one goal —
#: and because getting it wrong inverts the verdict silently.
#:
#: (key, label, direction, uom, note)
METRICS = (
	("first_pass_yield", "First-pass yield", HIGHER_IS_BETTER, "Percent",
	 "Submitted inspections with no failed check, over all submitted inspections."),
	("inspection_fail_count", "Failed checks", LOWER_IS_BETTER, None,
	 "Total failed checks across submitted inspections in the period."),
	("ncrs_raised", "Non-conformances raised", LOWER_IS_BETTER, None,
	 "Non-conformances created in the period."),
	("critical_ncrs", "Critical non-conformances", LOWER_IS_BETTER, None,
	 "Non-conformances raised at Critical severity in the period."),
	("open_punch_items", "Open punch-list items", LOWER_IS_BETTER, None,
	 "Punch-list Quality Actions still open at the end of the period."),
	("actions_reopened", "Fixes that failed re-verification", LOWER_IS_BETTER, None,
	 "Quality Actions reopened at least once by a re-inspection."),
	("avg_days_to_close_action", "Average days to close an action", LOWER_IS_BETTER, "Day",
	 "Mean days from raising a corrective action to closing it."),
)

METRIC_KEYS = tuple(m[0] for m in METRICS)
METRIC_DIRECTION = {m[0]: m[2] for m in METRICS}
METRIC_LABEL = {m[0]: m[1] for m in METRICS}
METRIC_UOM = {m[0]: m[3] for m in METRICS}

#: Verdicts, matching core's `Quality Review Objective.status` options exactly. `Open` is the
#: honest answer whenever the comparison cannot be made, and is the default for a reason: a
#: review that cannot decide should say so rather than pick a side.
PASSED = "Passed"
FAILED = "Failed"
OPEN = "Open"

#: Frequencies ERPNext's own daily `review()` already handles. This module must never generate
#: for one of these — see the module docstring.
CORE_FREQUENCIES = ("Daily", "Weekly", "Monthly", "Quarterly")
ANNUAL = "Annual"

MONTHS = (
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
)


def parse_target(value):
	"""A `Data` target as a number, or ``None`` when it cannot be read.

	Tolerates what people actually type — ``95%``, ``<= 2``, ``≤ 2``, ``2 days``, a stray space —
	because the field accepts free text and somebody will. Returns ``None`` for anything with no
	number in it at all, and ``None`` is **not** zero: see the module docstring.
	"""
	if value is None:
		return None
	if isinstance(value, (int, float)) and not isinstance(value, bool):
		return float(value)
	text = str(value).strip()
	if not text:
		return None
	cleaned = []
	seen_digit = False
	for char in text:
		if char.isdigit():
			seen_digit = True
			cleaned.append(char)
		elif char in ".-" and (not cleaned or char == "." or not seen_digit):
			cleaned.append(char)
		elif seen_digit:
			# Stop at the first thing after the number: "2 per project" is 2, and "2 or 3" is
			# deliberately not averaged into something nobody wrote.
			break
	try:
		return float("".join(cleaned))
	except ValueError:
		return None


def direction_of(metric):
	"""Which way this metric runs. ``None`` when the metric is unknown — never a default.

	A default here would be the silent inversion the module docstring warns about: an unknown
	metric guessed as higher-is-better marks every "fewer is better" goal as failing.
	"""
	return METRIC_DIRECTION.get(metric)


def verdict(actual, target, metric, sample_size=None):
	"""``Passed`` / ``Failed`` / ``Open`` for one objective.

	``Open`` — deliberately, and in every one of these cases:

	* the target cannot be read,
	* the metric is not one this module computes, so there is no direction,
	* no actual was computed,
	* **the sample is empty.** A period with nothing in it does not meet a target and does not
	  miss it; first-pass yield over zero inspections is undefined, and an implementation that
	  returned 100% would report a perfect quarter for a quarter in which nobody inspected
	  anything.
	"""
	if sample_size is not None and not sample_size:
		return OPEN
	if actual is None:
		return OPEN
	target = parse_target(target)
	if target is None:
		return OPEN
	direction = direction_of(metric)
	if direction is None:
		return OPEN
	if direction == LOWER_IS_BETTER:
		return PASSED if float(actual) <= target else FAILED
	return PASSED if float(actual) >= target else FAILED


def floor_violations(project_objectives, company_objectives):
	"""Project objectives that are slacker than the company-wide goal for the same metric.

	The rule from the build spec: *a project-specific goal may only meet or exceed the
	company-wide target.* Matched on **metric**, never on the objective text — two people
	writing "first pass yield" and "First-Pass Yield" is not a disagreement about the standard.

	Returns ``[{metric, project_target, company_target, direction}, ...]``. An objective whose
	metric has no company-wide counterpart is not a violation: the company has said nothing about
	it, and inventing a floor from silence would block goals nobody objected to.
	"""
	floors = {}
	for row in company_objectives or []:
		metric = _get(row, "custom_metric")
		target = parse_target(_get(row, "target"))
		if metric and target is not None:
			floors[metric] = target

	out = []
	for row in project_objectives or []:
		metric = _get(row, "custom_metric")
		if metric not in floors:
			continue
		target = parse_target(_get(row, "target"))
		if target is None:
			# Unreadable, so not comparable. Reported by the caller as its own problem rather
			# than silently passing the floor check.
			continue
		direction = direction_of(metric)
		slacker = target > floors[metric] if direction == LOWER_IS_BETTER else target < floors[metric]
		if slacker:
			out.append(
				{
					"metric": metric,
					"project_target": target,
					"company_target": floors[metric],
					"direction": direction,
				}
			)
	return out


def unreadable_targets(objectives):
	"""Objectives whose target is not a number. Reported rather than skipped.

	A goal carrying an unreadable target can never be judged, and nothing else in the system
	would ever say so — the reviews would simply keep arriving Open.
	"""
	return [
		_get(row, "objective") or _get(row, "custom_metric") or ""
		for row in objectives or []
		if _get(row, "target") not in (None, "") and parse_target(_get(row, "target")) is None
	]


def review_due(frequency, on_date, annual_month=None, day_of_month=None):
	"""Whether an **Annual** goal should generate a review on ``on_date``.

	Raises for any frequency ERPNext's own ``review()`` already handles. That is not defensive
	programming for its own sake: returning ``False`` for Monthly would be a correct-looking
	answer that quietly invites a caller to loop over every goal, and looping over every goal is
	how this module would come to create a second review beside every one core already made.
	"""
	if frequency in CORE_FREQUENCIES:
		raise ValueError(
			f"{frequency} reviews are generated by ERPNext's own scheduler; this module owns "
			f"{ANNUAL} only, and generating for both would double every review."
		)
	if frequency != ANNUAL:
		return False

	on_date = _as_date(on_date)
	if on_date is None:
		return False

	month = (annual_month or MONTHS[0]).strip()
	if month not in MONTHS:
		return False
	if on_date.month != MONTHS.index(month) + 1:
		return False

	# `Quality Goal.date` is a Select of 1..30 -- there is no 31 -- so a goal can never ask for
	# the 31st and a February goal asking for the 30th would never fire. Both are clamped to the
	# last day the month actually has, so a goal cannot be configured into permanent silence.
	wanted = _int(day_of_month) or 1
	return on_date.day == min(wanted, _days_in_month(on_date.year, on_date.month))


def period_bounds(frequency, on_date):
	"""``(start, end)`` of the interval a review dated ``on_date`` reports on.

	The period **ends the day before** the review is generated. A review run on the first of the
	month reports the month that finished, not a month that is one day old — and including the
	current day would mean the same inspection could land in two consecutive periods depending
	on the hour the scheduler ran.
	"""
	on_date = _as_date(on_date)
	if on_date is None:
		return None, None
	end = on_date - timedelta(days=1)
	if frequency == "Daily":
		return end, end
	if frequency == "Weekly":
		return end - timedelta(days=6), end
	if frequency == "Monthly":
		return _add_months(end, -1) + timedelta(days=1), end
	if frequency == "Quarterly":
		return _add_months(end, -3) + timedelta(days=1), end
	if frequency == ANNUAL:
		return _add_months(end, -12) + timedelta(days=1), end
	return None, None


def summarise(rows):
	"""``(passed, failed, open_, total)`` across review objectives — what a digest line needs."""
	rows = rows or []
	statuses = [(_get(r, "status") or OPEN) for r in rows]
	return (
		statuses.count(PASSED),
		statuses.count(FAILED),
		statuses.count(OPEN),
		len(statuses),
	)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _days_in_month(year, month):
	if month == 12:
		return 31
	return (date(year, month + 1, 1) - timedelta(days=1)).day


def _add_months(value, months):
	"""Month arithmetic with the day clamped. 31 March minus one month is 28 February."""
	total = value.month - 1 + months
	year = value.year + total // 12
	month = total % 12 + 1
	return date(year, month, min(value.day, _days_in_month(year, month)))


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

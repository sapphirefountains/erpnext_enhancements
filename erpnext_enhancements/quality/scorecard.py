# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scoring a subcontractor without inventing the numbers — WI-075 sub-phase N.

A vendor scorecard is the most dangerous artifact this programme can produce, because unlike an
inspection or a budget it gets **printed and carried into a negotiation**. Nobody checks the
provenance of a number on a page that says *Scorecard*. So the first question is not how to score
a subcontractor; it is what this system can honestly claim to know about one today.

Measured on production 2026-09-14, the answer is: almost nothing.

* Every quality doctype that would supply a measure holds **zero rows** — Non Conformance,
  Quality Action, Project Quality Inspection, Project Scope of Work, Change Order. That is by
  design, the module is dormant, but it means five of the plan's six measures have no source data
  at all.
* **There are no subcontractor master agreements.** All sixteen `Project Contract` rows are
  ``maintenance`` templates with ``party_type = Customer`` — fifteen Draft, one Signed. The
  ``msa`` contract template exists and has never been used once. So the agreement-currency measure
  has no subjects either, and sub-phase L's expiry machinery has, as yet, nothing to expire.
* **Rework hours are captured nowhere.** No field on any doctype records them. This is the open
  question that keeps KPI #11 at Semi in ``docs/KPI_DASHBOARD_DESIGN.md``, and it is still open.
* **Certificates of insurance are captured nowhere either.** There is no COI field, no expiry, no
  reminder — on Supplier or anywhere else.
* And 908 of the 1181 Suppliers sit in supplier group ``Labels``, so the group column cannot tell
  a subcontractor from a stationery vendor.

Put a scorecard on top of that and **every subcontractor scores perfectly.** Zero NCRs, zero
hold-point failures, nothing overdue. It is the trailing-space failure again, in the costume where
it does the most damage: the number is clean, it is about nothing, and somebody quotes it in a
negotiation.

What this module does about it
------------------------------

**Every measure carries its own coverage, and the vocabulary is imported rather than redefined.**
:func:`erpnext_enhancements.quality.budgets.coverage` already answers "is this figure about the
project or about the instrument", which is the same question here. Importing it means the two
surfaces cannot drift into saying ``Not Tracked`` and ``Untracked``; ``budgets`` is simply where
it was written first. If a third surface needs it, extract it then.

**A measure with no sample has no value — ``None``, never ``0``.** First-pass yield over zero
inspections is not 0% and not 100%; it is unanswerable. :func:`ratio` returns ``None`` rather than
choose. Sub-phases L and M made the identical call for an MSA with no expiry and a percentage of
a zero budget.

**The score is a count, not a weighting.** It is the proportion of *judgeable* measures that met
their threshold — "met 4 of 5" — and nothing else. The tempting shape is a weighted composite, and
inventing weights is the same error as inventing checklists: it produces a number carrying the
authority of the company with nothing behind it. A threshold is a single stated figure somebody
can argue with in a diff; a weight is a judgement disguised as arithmetic. **The thresholds in
:data:`MEASURES` are a starting point to be corrected**, exactly like the strawman checklists in
sub-phase I, and they are constants here so that correcting one is a reviewable change.

**A scorecard with nothing judgeable has no score at all.** :func:`score` returns ``None``, not
zero and not a hundred, and the record then says *not yet measurable*. That is the state every
scorecard on this site will be in on the day this deploys, and printing it is the correct
behaviour — a page that says "we cannot yet measure this subcontractor" is honest, and a page
that says they are flawless is not.

**Two measures declare no source at all, on purpose.** Rework hours and insurance currency are
listed with :data:`SOURCE_NONE` rather than omitted, so the gap appears on the artifact instead of
being forgotten. A measure nobody can see missing is a measure nobody ever builds.

Imports no ``frappe`` beyond its sibling decision modules, all of which are frappe-free: there is
no Frappe integration-test job in CI, so judgement that lives inside a controller does not run
until somebody starts a bench by hand.
"""

from erpnext_enhancements.quality.budgets import (
	COVERAGE_NO_SOURCE,
	COVERAGE_NOT_TRACKED,
	COVERAGE_STATES,
	COVERAGE_TRACKED,
	coverage,
)

__all__ = [
	"COVERAGE_NOT_TRACKED",
	"COVERAGE_NO_SOURCE",
	"COVERAGE_STATES",
	"COVERAGE_TRACKED",
	"MEASURES",
	"adjustment_errors",
	"coverage",
	"engagement_reasons",
	"final_score",
	"headline",
	"judge",
	"measure",
	"measure_keys",
	"meets_threshold",
	"ratio",
	"score",
	"scorecard_state",
]

# --- Where a measure could come from ----------------------------------------------------------

SOURCE_NONE = "None"
SOURCE_NCR = "Non Conformance"
SOURCE_INSPECTION = "Project Quality Inspection"
SOURCE_ACTION = "Quality Action"
SOURCE_CONTRACT = "Project Contract"

SOURCES = (SOURCE_NONE, SOURCE_NCR, SOURCE_INSPECTION, SOURCE_ACTION, SOURCE_CONTRACT)

# --- Direction ---------------------------------------------------------------------------------

LOWER_IS_BETTER = "lower"
HIGHER_IS_BETTER = "higher"

# --- Scorecard state ----------------------------------------------------------------------------

STATE_NOT_MEASURABLE = "Not Measurable"
STATE_PARTIAL = "Partially Measured"
STATE_MEASURED = "Measured"

STATES = (STATE_NOT_MEASURABLE, STATE_PARTIAL, STATE_MEASURED)

#: The measures, in the order they are shown.
#:
#: ``(key, label, uom, direction, source, threshold, note)``
#:
#: ``threshold`` is the figure a measure must reach to count as met, or ``None`` for a measure
#: that is reported but not judged. **These figures are a starting point for Sapphire to
#: correct**, not a standard this system is entitled to assert — the same footing as the strawman
#: checklists seeded in sub-phase I. They live here as constants so that changing one is a
#: reviewable diff rather than a setting somebody adjusts on a Tuesday.
MEASURES = (
	(
		"ncrs_raised",
		"Non-conformances raised",
		"count",
		LOWER_IS_BETTER,
		SOURCE_NCR,
		0,
		"Any non-conformance attributed to this subcontractor in the period. The threshold is "
		"zero because one is worth a conversation; it is not a claim that one is a crisis.",
	),
	(
		"critical_ncrs",
		"Critical non-conformances",
		"count",
		LOWER_IS_BETTER,
		SOURCE_NCR,
		0,
		"Counted separately from the total on purpose. Severity is the whole reason the NCR "
		"carries a severity field, and a scorecard that folded three Minor findings and one "
		"Critical into a single 4 would hide the only one that mattered.",
	),
	(
		"hold_point_failures",
		"Hold-point failures",
		"count",
		LOWER_IS_BETTER,
		SOURCE_INSPECTION,
		0,
		"A hold point is work that cannot proceed until it passes. Failing one costs schedule, "
		"not just rework, which is why it is not simply another failed row.",
	),
	(
		"first_pass_yield",
		"First-pass inspection yield",
		"%",
		HIGHER_IS_BETTER,
		SOURCE_INSPECTION,
		90,
		"Inspections passed on the first attempt, as a proportion of inspections attempted. "
		"Unanswerable rather than zero when they were inspected no times.",
	),
	(
		"days_to_close",
		"Average days to close a corrective action",
		"days",
		LOWER_IS_BETTER,
		SOURCE_ACTION,
		14,
		"From raising the action to it being verified closed. Measures responsiveness rather "
		"than fault -- a subcontractor who fixes things quickly is a different proposition from "
		"one who does not, even at the same defect rate.",
	),
	(
		"recovery_rate",
		"Cost recovered against claimed",
		"%",
		HIGHER_IS_BETTER,
		SOURCE_NCR,
		100,
		"Of the money claimed back from this subcontractor for their own defects, how much was "
		"actually recovered. Unanswerable when nothing was claimed, which is the ordinary case.",
	),
	(
		"rework_hours",
		"Rework hours",
		"hours",
		LOWER_IS_BETTER,
		# NO SOURCE, and listed here rather than omitted precisely so the gap shows up on the
		# artifact. Nothing on this site records rework hours -- not a field, not a timesheet
		# activity type, nothing. It is the open question that keeps KPI #11 at Semi in
		# docs/KPI_DASHBOARD_DESIGN.md, and a measure nobody can see missing is one nobody builds.
		SOURCE_NONE,
		None,
		"Not captured anywhere on this site. Listed so the gap is visible on the scorecard "
		"rather than quietly absent from it. Until something records rework hours, this reports "
		"No Source and is not judged.",
	),
	(
		"agreement_currency",
		"Master agreement in force",
		"state",
		None,
		SOURCE_CONTRACT,
		# Judged, but not against a number -- see `meets_threshold`. `ok` and `warn` both count
		# as met: an agreement 45 days from renewal is in force, and marking it unmet would score
		# a subcontractor down for the calendar.
		None,
		"Whether a signed master agreement covered this subcontractor through the period. "
		"Unknown is never scored as a failure -- it is a gap in our record, not their conduct.",
	),
	(
		"insurance_currency",
		"Certificate of insurance current",
		"state",
		None,
		# Also no source. There is no COI field on Supplier or anywhere else: no certificate, no
		# expiry, no reminder. On a subcontractor scorecard that is a finding worth printing.
		SOURCE_NONE,
		None,
		"Not captured anywhere on this site. There is no certificate-of-insurance field, expiry "
		"or reminder on Supplier or elsewhere. Listed so the gap is visible.",
	),
)

_BY_KEY = {row[0]: row for row in MEASURES}

#: States of a master agreement that count as *in force* for scoring purposes. Imported callers
#: pass `msa.expiry_state` output straight in.
AGREEMENT_MET = ("ok", "warn")
AGREEMENT_FAILED = ("expired",)


def measure_keys():
	"""Every measure key, in display order."""
	return tuple(row[0] for row in MEASURES)


def measure(key):
	"""The ``(key, label, uom, direction, source, threshold, note)`` tuple, or ``None``."""
	return _BY_KEY.get(key)


def _get(obj, field, default=None):
	"""Read ``field`` from a dict or an object, without caring which."""
	if isinstance(obj, dict):
		return obj.get(field, default)
	value = getattr(obj, field, default)
	return default if value is None else value


def _float(value):
	if value is None or value == "":
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


# --- Values -------------------------------------------------------------------------------------


def ratio(numerator, denominator):
	"""A percentage, or ``None`` when there is nothing to take a proportion of.

	Not 0, not 100, not a division error. First-pass yield over zero inspections is unanswerable,
	and a scorecard that answered it would be asserting something about a subcontractor nobody
	inspected. Sub-phase M made the same call for a percentage of a zero budget, and L for an MSA
	with no recorded expiry.
	"""
	den = _float(denominator)
	num = _float(numerator)
	if den is None or den == 0:
		return None
	if num is None:
		return None
	return round(num / den * 100.0, 1)


def average(values):
	"""Mean of the usable values, or ``None`` when there are none.

	Used for days-to-close. An average of an empty list is not zero days.
	"""
	usable = [v for v in (_float(x) for x in (values or ())) if v is not None]
	if not usable:
		return None
	return round(sum(usable) / len(usable), 1)


# --- Judging ------------------------------------------------------------------------------------


def meets_threshold(key, value):
	"""Whether ``value`` meets this measure's threshold.

	Returns ``True``, ``False``, or ``None`` for *not judgeable* — which is a real third answer
	and not a polite failure. A measure is unjudgeable when it has no threshold, when its value is
	unanswerable, or when an agreement's state is unknown.
	"""
	row = _BY_KEY.get(key)
	if row is None:
		return None

	_, _, _, direction, _, threshold, _ = row

	# The two state-valued measures are judged against a vocabulary rather than a number.
	if key in ("agreement_currency", "insurance_currency"):
		if value in AGREEMENT_MET:
			return True
		if value in AGREEMENT_FAILED:
			return False
		# `unknown`, blank, or anything else: a gap in our record is not their failure.
		return None

	if threshold is None:
		return None

	actual = _float(value)
	if actual is None:
		return None

	if direction == LOWER_IS_BETTER:
		return actual <= float(threshold)
	if direction == HIGHER_IS_BETTER:
		return actual >= float(threshold)
	return None


def judge(results):
	"""Annotate each result with whether it was met and whether it could be judged.

	``results`` are dicts carrying at least ``measure_key``, ``value`` and ``coverage``. Returns a
	new list; the caller's rows are not touched.

	A result is **judgeable** only when its coverage is ``Tracked`` *and* ``meets_threshold``
	returns a boolean. Both halves matter: a figure from an instrument nobody uses is not evidence
	however good it looks, and a measure with no threshold was never a test.
	"""
	out = []
	for result in results or ():
		key = _get(result, "measure_key")
		value = _get(result, "value")
		verdict = _get(result, "coverage")

		met = meets_threshold(key, value) if verdict == COVERAGE_TRACKED else None
		row = dict(result) if isinstance(result, dict) else {
			"measure_key": key,
			"value": value,
			"coverage": verdict,
		}
		row["met"] = met
		row["judgeable"] = met is not None
		out.append(row)
	return out


def score(results):
	"""``(percent, met, judgeable, total)`` — the proportion of judgeable measures that were met.

	``percent`` is ``None``, never 0 and never 100, when nothing could be judged. That is the
	state every scorecard on this site will be in the day this deploys, and it is the correct
	answer: a page saying *we cannot yet measure this subcontractor* is honest, and one saying
	they are flawless is not.

	Deliberately **not** a weighted composite. Weights are judgements disguised as arithmetic and
	nobody here has agreed any; a count of thresholds met is explicable in one sentence and every
	threshold behind it is a stated figure somebody can argue with in a diff.
	"""
	judged = judge(results)
	total = len(judged)
	judgeable = [row for row in judged if row["judgeable"]]
	met = [row for row in judgeable if row["met"]]

	if not judgeable:
		return None, 0, 0, total

	return round(len(met) / len(judgeable) * 100.0, 1), len(met), len(judgeable), total


def final_score(base, adjustment):
	"""The score after a person's adjustment, clamped to 0-100.

	``None`` in means ``None`` out: an adjustment to a score that does not exist would conjure
	one, and a scorecard reading "-5" for a subcontractor nobody could measure is worse than a
	blank.
	"""
	if base is None:
		return None
	delta = _float(adjustment) or 0.0
	return round(max(0.0, min(100.0, float(base) + delta)), 1)


def adjustment_errors(adjustment, reason):
	"""Why this manual adjustment cannot be saved.

	An adjustment with no reason is the scorecard's own integrity leaking away: the record exists
	so a disputed number can be opened rather than argued, and a silent override is the one edit
	that defeats that. The plan puts it plainly — the first time a score is wrong and there is no
	way to say so on the record, people stop using it. So the way is provided, and it is signed.
	"""
	errors = []
	delta = _float(adjustment) or 0.0
	if delta and not (reason or "").strip():
		errors.append("Give a reason for the manual adjustment.")
	if abs(delta) > 100:
		errors.append("An adjustment larger than 100 points cannot be applied to a 0-100 score.")
	return errors


def scorecard_state(results):
	"""``Not Measurable`` / ``Partially Measured`` / ``Measured`` for a whole scorecard.

	Reported beside the score rather than instead of it, so a reader sees both the number and how
	much of the picture it came from.
	"""
	judged = judge(results)
	if not judged:
		return STATE_NOT_MEASURABLE
	judgeable = [row for row in judged if row["judgeable"]]
	if not judgeable:
		return STATE_NOT_MEASURABLE
	if len(judgeable) < len(judged):
		return STATE_PARTIAL
	return STATE_MEASURED


def headline(results, base=None, adjustment=None):
	"""One sentence for a list view or an email, saying the number **and its footing**.

	The two never travel separately. A scorecard's number quoted without how much of it is real
	is the thing this module exists to prevent.
	"""
	percent, met, judgeable, total = score(results)
	if percent is None:
		return f"Not yet measurable — none of the {total} measures has usable data."

	shown = final_score(percent, adjustment) if adjustment else percent
	text = f"Met {met} of {judgeable} measures ({shown:g}%)"
	if judgeable < total:
		text += f"; {total - judgeable} of {total} could not be judged"
	if adjustment:
		text += f", after a manual adjustment of {_float(adjustment):+g}"
	return text + "."


# --- Who gets a scorecard -------------------------------------------------------------------------


def engagement_reasons(had_project_order, ncr_count, has_agreement):
	"""Why this supplier is on the list for a period, or an empty list if they are not.

	Scoring all 1181 suppliers would be noise, and supplier group cannot separate them — 908 of
	them sit in the group ``Labels``. So the population is derived from **behaviour**: we score
	the people we actually engaged. A supplier with a signed master agreement is included even
	with no activity, because an agreement in force is itself a thing to report on.
	"""
	reasons = []
	if had_project_order:
		reasons.append("placed a purchase order against a project")
	if ncr_count:
		reasons.append("a non-conformance was attributed to them")
	if has_agreement:
		reasons.append("a master agreement covers them")
	return reasons

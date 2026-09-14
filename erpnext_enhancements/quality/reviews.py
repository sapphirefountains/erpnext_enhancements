# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Putting a number in a period review — WI-075 sub-phase J.

ERPNext generates a `Quality Review` on a cadence and copies the goal's objectives into it, and
then stops: the review arrives with targets and blank actuals, and somebody types a verdict. A
metric nobody computes is a metric nobody trusts, so this module computes it.

Every judgement is in :mod:`erpnext_enhancements.quality.goals`, which imports nothing — what
counts as met, which way a metric runs, how a `Data` target is read, and above all what a review
is allowed to conclude when it has no data. This module is the half that queries.

The sample size is the point
-----------------------------

Each metric here returns a **sample** alongside its value, and the sample is not decoration. It
is the count of real activity the number was computed over, and when it is zero the verdict is
``Open`` rather than a score. First-pass yield over a quarter with no inspections is undefined;
an implementation that returned 100% would report a perfect quarter for a quarter in which
nobody inspected anything, and that number would then be the one on the wall.

For a count metric the sample is the *activity level*, not the count itself. Zero
non-conformances across fifty inspections is genuinely good news; zero non-conformances across
zero inspections is no news at all, and the two must not produce the same green tick.

Annual only
------------

``generate_annual_reviews`` exists because ERPNext's own daily ``review()`` branches on Daily,
Weekly, Monthly and Quarterly and has **no Annual branch** — so an Annual goal would generate
nothing, forever, with no error. It handles that one cadence and refuses the rest: generating for
a frequency core already owns would put a second review beside every one core made, on the same
goal, the same day.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.quality import goals, lifecycle
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: How Quality Settings spells the floor rule's three strengths.
FLOOR_OFF, FLOOR_WARN, FLOOR_BLOCK = "Off", "Warn", "Block"

#: Cap on goals examined per annual sweep. There will never be many, and an unbounded loop in a
#: scheduler job is how one bad goal takes the whole daily queue with it.
SWEEP_LIMIT = 200


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _inspection_filters(start, end, project=None):
	filters = {"docstatus": 1, "inspection_date": ["between", [str(start), str(end)]]}
	if project:
		filters["project"] = project
	return filters


def _activity(start, end, project=None):
	"""Submitted inspections in the period — the denominator for anything inspection-shaped."""
	return frappe.db.count("Project Quality Inspection", _inspection_filters(start, end, project))


def compute(metric, start, end, project=None):
	"""``(value, sample, note)`` for one metric over one period.

	``value`` is ``None`` when it could not be worked out. ``note`` says why, and is written onto
	the review row — a review that decided nothing should say what stopped it.
	"""
	try:
		return _COMPUTERS[metric](start, end, project)
	except KeyError:
		return None, None, _("{0} is not a measure this app computes.").format(metric)
	except Exception:
		frappe.log_error(
			title="Quality: metric computation failed",
			message=f"metric={metric} {start}..{end} project={project}\n\n{frappe.get_traceback()}",
		)
		return None, None, _("This measure could not be computed; the error is in the Error Log.")


def _first_pass_yield(start, end, project):
	sample = _activity(start, end, project)
	if not sample:
		return None, 0, _("No inspections were submitted in this period, so first-pass yield is undefined.")
	filters = _inspection_filters(start, end, project)
	filters["fail_count"] = 0
	clean = frappe.db.count("Project Quality Inspection", filters)
	return round(100.0 * clean / sample, 2), sample, _("{0} of {1} inspections passed with no failed check.").format(clean, sample)


def _inspection_fail_count(start, end, project):
	sample = _activity(start, end, project)
	rows = frappe.get_all(
		"Project Quality Inspection", filters=_inspection_filters(start, end, project), pluck="fail_count"
	)
	total = sum(int(value or 0) for value in rows)
	return float(total), sample, _("{0} failed checks across {1} inspections.").format(total, sample)


def _ncr_count(start, end, project, severity=None):
	filters = {"custom_raised_on": ["between", [str(start), str(end)]]}
	if project:
		filters["custom_project"] = project
	if severity:
		filters["custom_severity"] = severity
	count = frappe.db.count("Non Conformance", filters)
	# The denominator is the inspection activity, not the count itself. Zero non-conformances
	# across fifty inspections is good news; zero across zero inspections is no news.
	sample = _activity(start, end, project)
	return float(count), sample, _("{0} raised against {1} inspections.").format(count, sample)


def _ncrs_raised(start, end, project):
	return _ncr_count(start, end, project)


def _critical_ncrs(start, end, project):
	return _ncr_count(start, end, project, severity=lifecycle.SEVERITY_CRITICAL)


def _open_punch_items(start, end, project):
	"""Punch items still open at the end of the period.

	A point-in-time count, so it carries **no sample**: zero open punch items is meaningful
	whether or not anything happened in the period, which is not true of the rate metrics above.
	"""
	filters = {
		"custom_punch_list": 1,
		"status": ["not in", (lifecycle.ACTION_VERIFIED, lifecycle.ACTION_CLOSED)],
		"creation": ["<=", f"{end} 23:59:59"],
	}
	if project:
		filters["custom_project"] = project
	count = frappe.db.count("Quality Action", filters)
	return float(count), None, _("{0} punch-list items were still open on {1}.").format(count, end)


def _actions_reopened(start, end, project):
	filters = {"custom_reopen_count": [">", 0], "modified": ["between", [str(start), f"{end} 23:59:59"]]}
	if project:
		filters["custom_project"] = project
	count = frappe.db.count("Quality Action", filters)
	verified = {"custom_verification_result": ["is", "set"], "modified": ["between", [str(start), f"{end} 23:59:59"]]}
	if project:
		verified["custom_project"] = project
	sample = frappe.db.count("Quality Action", verified)
	return float(count), sample, _("{0} of {1} re-verified fixes failed and were reopened.").format(count, sample)


def _avg_days_to_close_action(start, end, project):
	filters = {"custom_closed_on": ["between", [str(start), f"{end} 23:59:59"]]}
	if project:
		filters["custom_project"] = project
	rows = frappe.get_all("Quality Action", filters=filters, fields=["date", "custom_closed_on"])
	spans = []
	for row in rows:
		raised, closed = row.get("date"), row.get("custom_closed_on")
		if not raised or not closed:
			continue
		spans.append((getdate(closed) - getdate(raised)).days)
	if not spans:
		return None, 0, _("No corrective actions were closed in this period.")
	return round(sum(spans) / float(len(spans)), 2), len(spans), _("Across {0} actions closed.").format(len(spans))


_COMPUTERS = {
	"first_pass_yield": _first_pass_yield,
	"inspection_fail_count": _inspection_fail_count,
	"ncrs_raised": _ncrs_raised,
	"critical_ncrs": _critical_ncrs,
	"open_punch_items": _open_punch_items,
	"actions_reopened": _actions_reopened,
	"avg_days_to_close_action": _avg_days_to_close_action,
}


# ---------------------------------------------------------------------------
# Quality Review
# ---------------------------------------------------------------------------


def on_review_validate(doc, method=None):
	"""``Quality Review`` ``validate``: fill in the actuals and re-derive the verdicts.

	Runs **after** core's own validate, which has already copied the goal's objectives and called
	``set_status()``. Because this changes the child statuses, ``set_status()`` is called again at
	the end — otherwise the parent would keep the verdict core reached before any actual existed.

	Never raises. A review that could not compute is a review with Open rows and a note saying
	why, which is a worse day than a computed one and a far better day than a lost save.
	"""
	try:
		if not is_enabled():
			return
		_populate(doc)
	except Exception:
		frappe.log_error(
			title="Quality: could not compute a review",
			message=f"review={getattr(doc, 'name', '?')}\n\n{frappe.get_traceback()}",
		)


def _populate(doc):
	frequency = frappe.db.get_value("Quality Goal", doc.goal, "frequency") if doc.goal else None
	start, end = goals.period_bounds(frequency, doc.get("date") or nowdate())
	if not start or not end:
		doc.custom_summary = _(
			"This goal has no cadence, so there is no period to measure. Set a frequency on the goal."
		)
		return

	doc.custom_period_start = start
	doc.custom_period_end = end
	project = doc.get("custom_project") or frappe.db.get_value("Quality Goal", doc.goal, "custom_project")

	metrics_by_objective = _goal_metrics(doc.goal)
	notes = []
	for row in doc.get("reviews") or []:
		metric = row.get("custom_metric") or metrics_by_objective.get((row.objective or "").strip())
		row.custom_metric = metric
		if not metric:
			row.status = goals.OPEN
			row.custom_computation_note = _("No measure is set on this objective, so it cannot be scored.")
			continue

		value, sample, note = compute(metric, start, end, project)
		row.custom_actual_value = value if value is not None else 0
		row.custom_sample_size = sample if sample is not None else 0
		row.custom_computation_note = note
		row.status = goals.verdict(value, row.target, metric, sample)
		if row.status == goals.OPEN:
			notes.append(f"{goals.METRIC_LABEL.get(metric, metric)}: {note}")

	doc.custom_summary = (
		"\n".join(notes)
		if notes
		else _("Every objective was scored against {0} to {1}.").format(start, end)
	)
	# Core set this before any actual existed; re-derive it now that the rows carry verdicts.
	doc.set_status()


def _goal_metrics(goal):
	"""``{objective text: metric}`` from the goal, so a review whose rows predate the metric field
	can still be scored without anybody re-typing them."""
	if not goal:
		return {}
	rows = frappe.get_all(
		"Quality Goal Objective",
		filters={"parent": goal, "parenttype": "Quality Goal"},
		fields=["objective", "custom_metric"],
		ignore_permissions=True,
	)
	return {(r["objective"] or "").strip(): r.get("custom_metric") for r in rows}


# ---------------------------------------------------------------------------
# The floor rule
# ---------------------------------------------------------------------------


def on_goal_validate(doc, method=None):
	"""``Quality Goal`` ``validate``: a project goal may not be slacker than the company's.

	Core's own ``QualityGoal.validate`` is ``pass``, so this needs no class override — unlike
	``Quality Action``, whose one-line validate had to be replaced.

	Enforcement is a dial in Quality Settings: **Off**, **Warn** (the default, until the team has
	lived with the rule) or **Block**.
	"""
	try:
		if not is_enabled():
			return
		unreadable = goals.unreadable_targets(doc.get("objectives"))
		if unreadable:
			# Said out loud rather than skipped. A goal carrying an unreadable target can never
			# be judged, and its reviews would simply keep arriving Open with nobody told why.
			frappe.msgprint(
				_("These targets are not numbers, so no review can ever score them: {0}").format(
					", ".join(unreadable)
				),
				title=_("Target cannot be read"),
				indicator="orange",
			)

		mode = _floor_mode()
		if mode == FLOOR_OFF:
			return
		if (doc.get("custom_goal_type") or "") != "Project-Specific":
			return

		violations = goals.floor_violations(doc.get("objectives"), _company_objectives())
		if not violations:
			return
		lines = "".join(
			"<li>{}: this project allows {:g}, the company target is {:g}</li>".format(
				frappe.utils.escape_html(goals.METRIC_LABEL.get(v["metric"], v["metric"])),
				v["project_target"],
				v["company_target"],
			)
			for v in violations
		)
		message = _("A project goal may only meet or exceed the company-wide target.") + f"<ul>{lines}</ul>"
		if mode == FLOOR_BLOCK:
			frappe.throw(message, title=_("Below the company floor"))
		frappe.msgprint(message, title=_("Below the company floor"), indicator="orange")
	except frappe.ValidationError:
		raise
	except Exception:
		frappe.log_error(
			title="Quality: goal floor check failed",
			message=f"goal={getattr(doc, 'name', '?')}\n\n{frappe.get_traceback()}",
		)


def _floor_mode():
	try:
		return frappe.db.get_single_value("Quality Settings", "company_floor_enforcement") or FLOOR_WARN
	except Exception:
		return FLOOR_OFF


def _company_objectives():
	"""Every objective on every company-wide goal, flattened.

	Flattened rather than per-goal because the floor is a statement about a *measure*, not about
	one goal document: if any company-wide goal sets a target for first-pass yield, that is the
	floor, wherever it was written.
	"""
	goals_list = frappe.get_all(
		"Quality Goal", filters={"custom_goal_type": "Company-Wide"}, pluck="name", limit=SWEEP_LIMIT
	)
	if not goals_list:
		return []
	return frappe.get_all(
		"Quality Goal Objective",
		filters={"parent": ["in", goals_list], "parenttype": "Quality Goal"},
		fields=["objective", "target", "custom_metric"],
		ignore_permissions=True,
	)


# ---------------------------------------------------------------------------
# The Annual cadence ERPNext cannot run
# ---------------------------------------------------------------------------


def generate_annual_reviews():
	"""Daily: create the review for any Annual goal whose day this is.

	**Annual only.** ERPNext's own daily job owns the other four frequencies, and generating for
	one of those here would put a second review beside every one it made.
	"""
	if not is_enabled():
		return
	try:
		today = getdate(nowdate())
		for row in frappe.get_all(
			"Quality Goal",
			filters={"frequency": goals.ANNUAL},
			fields=["name", "frequency", "date", "custom_annual_month"],
			limit=SWEEP_LIMIT,
		):
			if not goals.review_due(row["frequency"], today, row.get("custom_annual_month"), row.get("date")):
				continue
			if _already_reviewed_today(row["name"], today):
				continue
			review = frappe.get_doc(
				{
					"doctype": "Quality Review",
					"goal": row["name"],
					"date": today,
					"custom_generated_by_system": 1,
				}
			)
			review.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Quality: annual review sweep failed", message=frappe.get_traceback())


def _already_reviewed_today(goal, today):
	"""Core's own ``create_review`` has no such guard, so a re-run duplicates. This one does."""
	return bool(frappe.db.exists("Quality Review", {"goal": goal, "date": str(today)}))


# ---------------------------------------------------------------------------
# Quality Meeting agenda
# ---------------------------------------------------------------------------


def on_meeting_validate(doc, method=None):
	"""``Quality Meeting`` ``validate``: fill an empty agenda from what the period actually holds.

	Only when the agenda is empty — the same courtesy core extends to a review's objectives.
	Once somebody has written an agenda, this never touches it again.
	"""
	try:
		if not is_enabled() or doc.get("agenda"):
			return
		start = doc.get("custom_period_from")
		end = doc.get("custom_period_to")
		if not start or not end:
			return
		for line in _agenda_lines(start, end):
			doc.append("agenda", {"agenda": line})
	except Exception:
		frappe.log_error(
			title="Quality: could not build a meeting agenda",
			message=f"meeting={getattr(doc, 'name', '?')}\n\n{frappe.get_traceback()}",
		)


def _agenda_lines(start, end):
	"""What a quality meeting for this period would have to talk about.

	Nothing invented: every line names records that exist. An empty period produces a single line
	saying so, rather than an agenda of plausible-sounding headings nobody raised.
	"""
	window = [str(start), f"{end} 23:59:59"]
	lines = []

	failed = frappe.get_all(
		"Quality Review",
		filters={"status": "Failed", "date": ["between", [str(start), str(end)]]},
		fields=["name", "goal"],
		limit=SWEEP_LIMIT,
	)
	for row in failed:
		lines.append(
			_("<b>Review {0}</b> failed against goal <i>{1}</i>.").format(row["name"], row["goal"])
		)

	critical = frappe.get_all(
		"Non Conformance",
		filters={
			"custom_severity": lifecycle.SEVERITY_CRITICAL,
			"status": ["not in", (lifecycle.NCR_VERIFIED, lifecycle.NCR_CLOSED)],
		},
		fields=["name", "subject", "custom_project"],
		limit=SWEEP_LIMIT,
	)
	for row in critical:
		lines.append(
			_("<b>Critical non-conformance {0}</b> is still open: {1} ({2}).").format(
				row["name"], frappe.utils.escape_html(row.get("subject") or ""), row.get("custom_project") or _("no project")
			)
		)

	reopened = frappe.get_all(
		"Quality Action",
		filters={"custom_reopen_count": [">", 0], "modified": ["between", window]},
		fields=["name", "custom_subject", "custom_reopen_count"],
		limit=SWEEP_LIMIT,
	)
	for row in reopened:
		lines.append(
			_("<b>{0}</b> failed re-verification {1} time(s): {2}").format(
				row["name"], row["custom_reopen_count"], frappe.utils.escape_html(row.get("custom_subject") or "")
			)
		)

	overdue = frappe.get_all(
		"Quality Action",
		filters={
			"custom_due_date": ["<", str(end)],
			"status": ["not in", (lifecycle.ACTION_VERIFIED, lifecycle.ACTION_CLOSED)],
		},
		fields=["name", "custom_subject", "custom_due_date"],
		limit=SWEEP_LIMIT,
	)
	for row in overdue:
		lines.append(
			_("<b>{0}</b> was due {1} and is still open: {2}").format(
				row["name"], row["custom_due_date"], frappe.utils.escape_html(row.get("custom_subject") or "")
			)
		)

	if not lines:
		lines.append(
			_("Nothing in the quality records for {0} to {1} needs this meeting: no failed review, "
			  "no open Critical non-conformance, no reopened fix and no overdue action.").format(start, end)
		)
	return lines

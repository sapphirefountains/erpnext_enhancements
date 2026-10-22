# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The weekly schedule summary (P9 of the planner UX redesign; Nik's D3 and D16, 2026-10-21).

One page for James and the approvers: **what is on next week, by job and by person; what moved in the
last seven days; and what needs a manager.** It is printable from the Overview tab (More > Print >
Weekly summary, ``api/planner_overview.weekly_summary_html``) and, behind a Settings switch that is
**OFF until Nik turns it on** (``Project Planner Settings.weekly_summary_email``), emailed to the
managers named under *Send it to* every Monday at 6:15 AM (:func:`send_weekly_summary`).

:func:`build_summary` is the one read. :func:`summary_document` (print, on the print design system's
chrome) and :func:`summary_email` (the email design system's shell) only draw it, so the page, the paper
and the email say the same thing. Its numbers are the Overview's (``api/planner_overview.collect``): the
same jobs, the same display rule, the same exception list.

Things this module is careful about, some of which look like bugs:

* **Published schedules only.** A Draft schedule is private until it is published (D8): it notifies
  nobody, reaches no calendar. The summary never describes one; it names the jobs that are still Draft
  at the foot, so a manager knows a plan exists. The Overview tab, on screen, does draw them (hatched).
* **"Next week" is the week ahead, not the next calendar week.** :func:`planner_overview.
  default_summary_week` picks the week holding today while it is a day old (the Monday email, a Sunday
  print), else the following week.
* **"What moved" is read from ``tabVersion``, not guessed.** A Task's start or end changed in the last
  seven days, folded to its first old value and last new value (a task moved and moved back is not
  listed), for schedulable work on the jobs above only. A job published in those days says so. If the
  Task doctype's change tracking were off, this section would say nothing moved; the page does not
  pretend to know more than the log does.
* **Exceptions are now, not that week.** Behind baseline, running over and overdue describe the job as
  it stands; conflicts are the next two weeks from today. The email is sent on the Monday it describes,
  so the two agree; a print for a later week carries today's exceptions and says so.
* **At most one email per week, through a database row, not redis.** The page is built first (it only
  reads, so a failure there spends nothing), then the week is claimed by inserting a ``Planner Digest
  Log`` row keyed ``weekly-summary|<first day>`` (unique), committed *before* sending. A second run finds
  it and sends nothing. The production deploy ``FLUSHDB``s redis, so a cache marker would have mailed
  everyone again after every merge; a send that fails after the claim is not retried, as the 6 AM
  digest's, because a missed Monday is better than two.
* **Nothing is sent, and nothing is read, while the switch is off or nobody is named.** The job returns
  before the first query. It never raises: a scheduler job that raises is only noise at 6 AM.
* **Every value is escaped**, in the print document (``print_style.escape_html``) and by the email
  macros. Task subjects, job titles and people's names are text anybody typed.

The pure helpers take plain values so ``tests/test_planner_weekly_summary.py`` runs them without a bench.
"""

import datetime
import json

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.api import planner_overview as overview
from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import planner_notices as notices
from erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings import (
	SETTINGS_DOCTYPE,
	parse_recipients,
)

LOG_DOCTYPE = "Planner Digest Log"
#: The key prefix of a week's claim in the Planner Digest Log (a person's keys are ``user|date``).
CLAIM_PREFIX = "weekly-summary"
#: Lines per job in the week's list before "and N more", and moved tasks named per job.
JOB_LINES = 12
MOVED_LINES = 5
#: The look-back for "what moved", in days.
MOVED_DAYS = 7
#: The most Version rows read for it.
MOVED_ROWS = 2000
DATE_FIELDS = {"exp_start_date": "start", "exp_end_date": "end"}


# ---------------------------------------------------------------------- pure helpers


def day_text(value, with_month=True):
	"""``2026-10-26`` -> ``"Mon Oct 26"`` (``"Mon 26"`` without the month); "" for nothing."""
	day = engine._as_date(value)
	if not day:
		return ""
	return f"{day:%a} {day:%b} {day.day}" if with_month else f"{day:%a} {day.day}"


def range_text(first, last):
	"""``Mon Oct 26`` for one day, ``Mon Oct 26 - Wed Oct 28`` for a span (an en dash)."""
	first_text, last_text = day_text(first), day_text(last)
	if not last_text or last_text == first_text:
		return first_text
	return f"{first_text}–{last_text}"


def week_text(first):
	"""``Oct 26 - Nov 1, 2026``: the seven days from ``first``."""
	first = engine._as_date(first)
	last = first + datetime.timedelta(days=6)
	if first.month == last.month:
		return f"{first:%b} {first.day} – {last.day}, {last.year}"
	return f"{first:%b} {first.day} – {last:%b} {last.day}, {last.year}"


def group_days(entries):
	"""``[(first, last, places)]`` from ``[(date, places)]``: consecutive calendar days with the same
	places are one segment. ``places`` is a tuple of names, compared as a set; an entry with none is
	skipped. Pure; the input need not be sorted."""
	segments = []
	for day, places in sorted(((engine._as_date(d), tuple(p)) for d, p in entries if p), key=lambda e: e[0]):
		if segments:
			first, last, held = segments[-1]
			if set(held) == set(places) and day - last == datetime.timedelta(days=1):
				segments[-1] = (first, day, held)
				continue
		segments.append((day, day, places))
	return segments


def segment_text(segment):
	first, last, places = segment
	return f"{range_text(first, last)}: {', '.join(places)}"


def fold_date_changes(rows):
	"""``{task: {"start": (old, new), "end": (old, new)}}`` from Version rows ``[(task, data_json)]``,
	**oldest first**: the first old value and the last new value of each date, date part only, and a date
	that ended up where it began is dropped. A row that is not JSON, or has no ``changed``, is skipped."""
	first_old, last_new = {}, {}
	for task, text in rows:
		try:
			changed = (json.loads(text) or {}).get("changed") or []
		except (TypeError, ValueError, AttributeError):
			continue
		for entry in changed:
			if not isinstance(entry, (list, tuple)) or len(entry) < 3 or entry[0] not in DATE_FIELDS:
				continue
			key = (task, DATE_FIELDS[entry[0]])
			old, new = str(entry[1] or "")[:10], str(entry[2] or "")[:10]
			first_old.setdefault(key, old)
			last_new[key] = new
	out = {}
	for (task, which), old in first_old.items():
		new = last_new[(task, which)]
		if old != new:
			out.setdefault(task, {})[which] = (old, new)
	return out


def _safe_date(value):
	"""A date from log text, or None for anything that is not one (a logged value is not trusted)."""
	try:
		return engine._as_date(value)
	except (TypeError, ValueError):
		return None


def moved_line(task, changes):
	"""One moved task as ``{task, subject, from, to}`` where ``task`` is its normalized row (its current
	``start``/``end``) and ``changes`` its folded dates: ``from`` is the days it had, ``to`` the days it
	has. None when the two are the same after all."""
	now = (task["start"], task["end"])
	was = (
		_safe_date((changes.get("start") or (None,))[0]) or now[0],
		_safe_date((changes.get("end") or (None,))[0]) or now[1],
	)
	if was == now:
		return None
	return {
		"task": task["name"],
		"subject": task["subject"],
		"from": [str(was[0]), str(was[1])],
		"to": [str(now[0]), str(now[1])],
	}


def span_text(pair):
	first, last = (pair or [None, None])[0], (pair or [None, None])[1]
	return range_text(first, last) if first else _("no dates")


# ---------------------------------------------------------------------- the read


def _crew_labels(names):
	"""``{task: [label]}`` for the week's lines; never raises (a name is better than a missing page)."""
	names = [n for n in names if n and not pp.is_draft_ref(n)]
	if not names:
		return {}
	try:
		return pp._crew_labels(names)
	except Exception:
		frappe.log_error(title="Weekly summary: crew names could not be read", message=frappe.get_traceback())
		return {}


def _places(first, last, skip=()):
	"""``([(label, [segment...])], [label...])``: everyone booked in the week, in the engine's order, and
	the labels of those with nothing booked. One Google-free engine pass.

	``skip`` are the projects the summary must not describe (the Draft schedules). The engine books a Draft
	project's *stored* tasks like any other, so its bookings are dropped here by project, or this section
	would print a plan the rest of the page promises not to."""
	skip = set(skip or ())
	computed = engine._compute(first, last, google=False)
	people = {r["name"]: r.get("label") or r["name"] for r in computed.get("resources") or []}
	stops = [
		b
		for cells in (computed.get("days") or {}).values()
		for cell in cells.values()
		for b in cell.get("bookings") or []
		if b.get("kind") in ("task", "rental") and b.get("project") not in skip
	]
	titles = pp._project_titles(b.get("project") for b in stops)
	booked, free = [], []
	for name, label in people.items():
		entries = []
		for day_text_, cell in sorted(((computed.get("days") or {}).get(name) or {}).items()):
			places = []
			for b in cell.get("bookings") or []:
				kind = b.get("kind")
				if kind in ("task", "rental"):
					if b.get("project") in skip:
						continue
					place = titles.get(b.get("project")) or b.get("label")
				elif kind == "visit":
					place = _("Visit: {0}").format(b.get("label") or "")
				elif kind == "travel":
					place = b.get("label") or _("Travel")
				else:
					continue
				if b.get("tentative"):
					place = _("{0} (pencil)").format(place)
				if place and place not in places:
					places.append(place)
			if places:
				entries.append((day_text_, places))
		segments = group_days(entries)
		(booked if segments else free).append((label, segments))
	return booked, [label for label, _s in free]


def _moved(found, since):
	"""``({project: [moved_line]}, cut)`` for the schedulable work on the summary's jobs, from the Version
	log. The read is limited to the summary's own projects in SQL (a bulk edit elsewhere cannot crowd it
	out), takes the **newest** :data:`MOVED_ROWS` rows and folds them oldest first; ``cut`` says the log was
	longer than that, so an old move may be missing."""
	index = {t["name"]: (project, t) for project, work in found["shown"].items() for t in work}
	if not index:
		return {}, False
	rows = frappe.db.sql(
		"""
		SELECT v.docname, v.data
		FROM `tabVersion` v
		INNER JOIN `tabTask` t ON t.name = v.docname
		WHERE v.ref_doctype = 'Task' AND v.creation >= %(since)s
			AND t.project IN %(projects)s
			AND (v.data LIKE %(start)s OR v.data LIKE %(end)s)
		ORDER BY v.creation DESC
		LIMIT %(limit)s
		""",
		{
			"since": since,
			"projects": tuple(sorted({project for project, _t in index.values()})),
			"start": '%"exp_start_date"%',
			"end": '%"exp_end_date"%',
			"limit": MOVED_ROWS,
		},
		as_dict=True,
	)
	cut = len(rows) >= MOVED_ROWS
	rows = list(reversed(rows))
	folded = fold_date_changes([(r.get("docname"), r.get("data")) for r in rows if r.get("docname") in index])
	out = {}
	for task, changes in folded.items():
		project, row = index[task]
		line = moved_line(row, changes) if row["start"] else None
		if line:
			out.setdefault(project, []).append(line)
	for lines in out.values():
		lines.sort(key=lambda l: (l["to"][0], l["subject"].casefold()))
	return out, cut


def build_summary(start=None, today=None):
	"""Everything the printout and the email draw. ``start`` is any day in the wanted week (the week
	ahead when not given). See the module docstring."""
	today = getdate(today or nowdate())
	first_weekday = pp._site_first_weekday()
	first = (
		pp.week_start(getdate(start), first_weekday)
		if start
		else overview.default_summary_week(today, first_weekday)
	)
	last = first + datetime.timedelta(days=6)
	found = overview.collect(
		draft=False, published_only=True, with_conflicts=True, today=today, cached_conflicts=False
	)
	lines, counts, more = overview.exception_lines(found["jobs"], found["over"], found["conflicts"])

	week = {}
	for job in found["jobs"]:
		work = [
			t
			for t in found["shown"].get(job["name"], [])
			if t["start"] and t["start"] <= last and t["end"] >= first and not overview.is_finished(t)
		]
		work.sort(key=lambda t: (t["start"], t["subject"].casefold(), t["name"]))
		if work:
			week[job["name"]] = work
	labels = _crew_labels([t["name"] for work in week.values() for t in work[:JOB_LINES]])
	jobs = []
	for job in found["jobs"]:
		work = week.get(job["name"])
		if not work:
			continue
		jobs.append(
			{
				"name": job["name"],
				"title": job["title"],
				"pm": job["pm"],
				"lines": [
					{
						"subject": t["subject"],
						"start": str(t["start"]),
						"end": str(t["end"]),
						"crew": labels.get(t["name"]) or [],
					}
					for t in work[:JOB_LINES]
				],
				"more": max(len(work) - JOB_LINES, 0),
			}
		)

	people, free = _places(first, last, found["draft_projects"])
	since = frappe.utils.add_days(frappe.utils.now_datetime(), -MOVED_DAYS)
	moved_by_job, moved_cut = _moved(found, since)
	moved = [
		{
			"name": job["name"],
			"title": job["title"],
			"count": len(moved_by_job[job["name"]]),
			"lines": moved_by_job[job["name"]][:MOVED_LINES],
		}
		for job in found["jobs"]
		if job["name"] in moved_by_job
	]
	published = [
		{"title": job["title"], "on": job["schedule"]["published_on"]}
		for job in found["jobs"]
		if (job.get("schedule") or {}).get("published_on")
		and str(job["schedule"]["published_on"])[:10] >= str(since)[:10]
	]
	return {
		"first": first,
		"last": last,
		"today": today,
		"exceptions": lines,
		"more_exceptions": more,
		"counts": counts,
		"conflicts_ok": found["conflicts"] is not None,
		"jobs": jobs,
		"people": [{"label": label, "segments": segments} for label, segments in people],
		"free": free,
		"moved": moved,
		"moved_total": sum(m["count"] for m in moved),
		"moved_cut": moved_cut,
		"published": published,
		"truncated": bool(found.get("truncated")),
		"drafts_named": found["drafts_named"],
		"job_count": len(found["jobs"]),
	}


# ---------------------------------------------------------------------- the printout


SUMMARY_CSS = """
@page { size: letter portrait; margin: 11mm; }
html, body { margin: 0; background: #ffffff; }
body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.ws-table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 11px; line-height: 1.35; margin: 0 0 4px; }
.ws-table th { text-align: left; vertical-align: bottom; }
.ws-table td { vertical-align: top; word-wrap: break-word; overflow-wrap: anywhere; }
.ws-table tr { page-break-inside: avoid; break-inside: avoid; }
.ws-job { margin: 10px 0 2px; font-weight: 700; page-break-after: avoid; }
.ws-sub { color: #363636; }
.ws-none { color: #363636; font-style: italic; }
.ws-foot { margin-top: 8px; font-size: 10px; color: #363636; }
"""


def _esc(value):
	from erpnext_enhancements import print_style as ps

	return ps.escape_html(value)


def _html_table(headers, rows, widths):
	"""A ruled table: ``headers`` and every cell are escaped here. ``widths`` are percentages."""
	from erpnext_enhancements import print_style as ps

	head = "".join(
		f'<th style="{ps.th()};width:{width}%">{_esc(header)}</th>'
		for header, width in zip(headers, widths, strict=True)
	)
	body = "".join(
		"<tr>" + "".join(f'<td style="{ps.TD}">{_esc(cell)}</td>' for cell in row) + "</tr>" for row in rows
	)
	return f'<table class="ws-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def summary_document(data, printed_on=None):
	"""The printable weekly summary as an HTML document, on the print design system's chrome
	(``print_style``: the stripe, the letterhead, ruled tables). Every value is escaped; the page prints
	it in a browser window, never through the server PDF."""
	from erpnext_enhancements import print_style as ps

	counts = data["counts"]
	span = week_text(data["first"])
	meta = _esc(_("Week of {0}").format(span))
	meta += "<br>" + _esc(
		_("Printed {0}").format(
			printed_on or frappe.utils.format_datetime(frappe.utils.now_datetime(), "MMM d, yyyy h:mm a")
		)
	)
	facts = (
		ps.facts_open()
		+ ps.fact(_esc(_("Need attention")), _esc(str(counts.get("lines", 0))), "25%")
		+ ps.fact(_esc(_("Jobs on this week")), _esc(str(len(data["jobs"]))), "25%")
		+ ps.fact(_esc(_("People booked")), _esc(str(len(data["people"]))), "25%")
		+ ps.fact(
			_esc(_("Tasks moved in {0} days").format(MOVED_DAYS)), _esc(str(data["moved_total"])), "25%"
		)
		+ ps.facts_close()
	)
	parts = [ps.letterhead(None, _esc(_("Schedule")), _esc(_("Weekly summary")), meta), facts]

	parts.append(ps.section_title(_esc(_("Needs attention"))))
	if data["exceptions"]:
		rows = [[_kind_label(line["kind"]), line["text"]] for line in data["exceptions"]]
		parts.append(_html_table([_("What"), _("Where it stands")], rows, [16, 84]))
		if data["more_exceptions"]:
			parts.append(
				f'<div class="ws-foot">{_esc(_("and {0} more").format(data["more_exceptions"]))}</div>'
			)
	else:
		parts.append(f'<div class="ws-none">{_esc(_("Nothing needs a manager this week."))}</div>')
	if not data["conflicts_ok"]:
		parts.append(
			f'<div class="ws-foot">{_esc(_("Conflicts could not be read for this printout."))}</div>'
		)

	parts.append(ps.section_title(_esc(_("This week by job"))))
	if data["jobs"]:
		for job in data["jobs"]:
			heading = job["title"] + (f" · {job['pm']}" if job.get("pm") else "")
			parts.append(f'<div class="ws-job">{_esc(heading)}</div>')
			rows = [
				[range_text(line["start"], line["end"]), line["subject"], ", ".join(line["crew"])]
				for line in job["lines"]
			]
			parts.append(_html_table([_("When"), _("What"), _("Who")], rows, [24, 50, 26]))
			if job["more"]:
				parts.append(f'<div class="ws-foot">{_esc(_("and {0} more").format(job["more"]))}</div>')
	else:
		parts.append(
			f'<div class="ws-none">{_esc(_("No Published job has work scheduled this week."))}</div>'
		)

	parts.append(ps.section_title(_esc(_("This week by person"))))
	if data["people"]:
		rows = [[p["label"], "\n".join(segment_text(s) for s in p["segments"])] for p in data["people"]]
		parts.append(_html_table([_("Person"), _("Where")], rows, [20, 80]).replace("\n", "<br>"))
	else:
		parts.append(f'<div class="ws-none">{_esc(_("Nobody is booked this week."))}</div>')
	if data["free"]:
		parts.append(
			f'<div class="ws-foot">{_esc(_("Nothing booked all week: {0}").format(", ".join(data["free"])))}</div>'
		)

	parts.append(ps.section_title(_esc(_("What moved in the last {0} days").format(MOVED_DAYS))))
	if data["moved"] or data["published"]:
		for item in data["published"]:
			parts.append(
				f'<div>{_esc(_("{0}: schedule published {1}").format(item["title"], day_text(str(item["on"])[:10])))}</div>'
			)
		for job in data["moved"]:
			parts.append(
				f'<div class="ws-job">{_esc(_("{0}: {1} moved").format(job["title"], overview.plural(job["count"], _("{0} task"), _("{0} tasks"))))}</div>'
			)
			rows = [
				[line["subject"], span_text(line["from"]), span_text(line["to"])] for line in job["lines"]
			]
			parts.append(_html_table([_("Task"), _("Was"), _("Now")], rows, [40, 30, 30]))
			if job["count"] > len(job["lines"]):
				parts.append(
					f'<div class="ws-foot">{_esc(_("and {0} more").format(job["count"] - len(job["lines"])))}</div>'
				)
	else:
		parts.append(f'<div class="ws-none">{_esc(_("No task dates moved."))}</div>')

	notes = [
		_(
			"Phase and crew work only, as on the planner's tabs; the template admin and billing checklists are left out. Overdue is counted by end date, never by the Overdue status."
		)
	]
	if data["drafts_named"]:
		notes.append(_("Draft schedules, not described here: {0}.").format(", ".join(data["drafts_named"])))
	notes += data_notes(data)
	parts.append("".join(f'<div class="ws-foot">{_esc(note)}</div>' for note in notes))

	return (
		"<!doctype html><html><head><meta charset='utf-8'>"
		f"<title>{_esc(_('Weekly summary'))} · {_esc(span)}</title>"
		f"<style>{SUMMARY_CSS}</style></head><body>"
		+ ps.page_open(None)
		+ "".join(parts)
		+ ps.page_close(None)
		+ "</body></html>"
	)


def data_notes(data):
	"""The caveats a reader needs when the read was cut: the change log longer than the rows read, or the
	task cap reached. Nothing when neither."""
	notes = []
	if data.get("moved_cut"):
		notes.append(_("The change log was long, so an older move may be missing from what moved."))
	if data.get("truncated"):
		notes.append(_("Only part of the task list could be read; some jobs may show less than they have."))
	return notes


def _kind_label(kind):
	return {
		"behind": _("Behind baseline"),
		"over": _("Running over"),
		"conflicts": _("Conflicts"),
		"overdue": _("Overdue"),
	}.get(kind, kind)


# ---------------------------------------------------------------------- the email


def summary_email(data, subject_prefix=None, note=None):
	"""``(subject, html)``: the summary through the email design system's shell. Nothing here writes
	markup: every piece is a component macro, which escapes its text."""
	from erpnext_enhancements import email_style

	counts = data["counts"]
	span = week_text(data["first"])
	attention = {"label": _("Need attention"), "value": str(counts.get("lines", 0))}
	if counts.get("lines"):
		attention["tone"] = "warning"
	body = email_style.kpis(
		[
			attention,
			{"label": _("Jobs on this week"), "value": str(len(data["jobs"]))},
			{"label": _("People booked"), "value": str(len(data["people"]))},
			{"label": _("Moved in {0} days").format(MOVED_DAYS), "value": str(data["moved_total"])},
		]
	)
	body += email_style.h(_("Needs attention"))
	if data["exceptions"]:
		items = [line["text"] for line in data["exceptions"]]
		if data["more_exceptions"]:
			items.append(_("and {0} more").format(data["more_exceptions"]))
		body += email_style.bullets(items)
	else:
		body += email_style.callout(_("Nothing needs a manager this week."), "success")
	if not data["conflicts_ok"]:
		body += email_style.note(_("Conflicts could not be read for this email."))
	body += email_style.h(_("This week by job"))
	if data["jobs"]:
		rows = []
		for job in data["jobs"]:
			for index, line in enumerate(job["lines"]):
				rows.append(
					[
						job["title"] if index == 0 else "",
						range_text(line["start"], line["end"]),
						line["subject"],
						", ".join(line["crew"]),
					]
				)
			if job["more"]:
				rows.append(["", "", _("and {0} more").format(job["more"]), ""])
		body += email_style.table([_("Job"), _("When"), _("What"), _("Who")], rows)
	else:
		body += email_style.p(_("No Published job has work scheduled this week."))
	body += email_style.h(_("This week by person"))
	if data["people"]:
		body += email_style.table(
			[_("Person"), _("Where")],
			[[p["label"], " · ".join(segment_text(s) for s in p["segments"])] for p in data["people"]],
		)
	else:
		body += email_style.p(_("Nobody is booked this week."))
	if data["free"]:
		body += email_style.note(_("Nothing booked all week: {0}").format(", ".join(data["free"])))
	body += email_style.h(_("What moved in the last {0} days").format(MOVED_DAYS))
	if data["moved"] or data["published"]:
		items = [
			_("{0}: schedule published {1}").format(i["title"], day_text(str(i["on"])[:10]))
			for i in data["published"]
		]
		rows = []
		for job in data["moved"]:
			for index, line in enumerate(job["lines"]):
				rows.append(
					[
						job["title"] if index == 0 else "",
						line["subject"],
						span_text(line["from"]),
						span_text(line["to"]),
					]
				)
			if job["count"] > len(job["lines"]):
				rows.append(["", _("and {0} more").format(job["count"] - len(job["lines"])), "", ""])
		if items:
			body += email_style.bullets(items)
		if rows:
			body += email_style.table([_("Job"), _("Task"), _("Was"), _("Now")], rows)
	else:
		body += email_style.p(_("No task dates moved."))
	if data["drafts_named"]:
		body += email_style.note(
			_("Draft schedules, not described here: {0}.").format(", ".join(data["drafts_named"]))
		)
	for text in data_notes(data):
		body += email_style.note(text)
	body += email_style.button(
		frappe.utils.get_url(f"{notices.PLANNER_ROUTE}/overview/{data['today']}"), _("Open the Overview")
	)
	body += email_style.note(
		note
		or _(
			"You are getting this because you are listed under Project Planner Settings > Telling managers. Untick the box there to stop it."
		)
	)
	subject = _("Weekly schedule summary: week of {0}").format(day_text(data["first"]))
	if subject_prefix:
		subject = f"{subject_prefix}{subject}"
	html = email_style.wrap(body, title=_("Weekly schedule summary"), eyebrow=_("Schedule") + " · " + span)
	return subject, html


# ---------------------------------------------------------------------- the Monday job


def recipients():
	"""The addresses named under *Send it to*, or [] (also when the Settings cannot be read)."""
	try:
		text = frappe.get_cached_doc(SETTINGS_DOCTYPE).get("weekly_summary_recipients")
	except Exception:
		return []
	return parse_recipients(text)[0]


def _claim(first, jobs):
	"""Record the week before sending it. False when it is already recorded (another run got there
	first). The row is committed at once, so a crash while sending does not send again."""
	key = f"{CLAIM_PREFIX}|{first}"
	if frappe.db.exists(LOG_DOCTYPE, key):
		return False
	try:
		frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"log_key": key,
				"user": "Administrator",
				"digest_date": str(first),
				"sent_on": frappe.utils.now_datetime(),
				"bookings": jobs,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return True
	except Exception:
		# Almost always the unique key: a second run claimed the week between the exists() and the
		# insert. Either way the safe answer is "do not send".
		frappe.db.rollback()
		return False


def send_weekly_summary():
	"""Cron, Monday 06:15 (``hooks.py``): email the week's summary to the managers named in Settings.

	Does nothing, before any query, while ``weekly_summary_email`` is off (the default) or nobody is
	named. At most once per week (:func:`_claim`). Never raises.

	The order matters: the read and the email are built first (they only read, so a failure there spends
	nothing and the next run that week can still send), then the week is claimed and committed, then the mail
	goes. A week already claimed is skipped before the costly read."""
	try:
		if not notices.setting("weekly_summary_email"):
			return
		to = recipients()
		if not to:
			return
		today = getdate(nowdate())
		first = overview.default_summary_week(today, pp._site_first_weekday())
		if frappe.db.exists(LOG_DOCTYPE, f"{CLAIM_PREFIX}|{first}"):
			return
		data = build_summary(first, today)
		subject, html = summary_email(data)
		if not _claim(first, len(data["jobs"])):
			return
		frappe.sendmail(recipients=to, subject=subject, message=html)
		frappe.db.set_value(
			LOG_DOCTYPE,
			f"{CLAIM_PREFIX}|{first}",
			{"channels": "email", "bookings": len(data["jobs"])},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Project Planner: weekly summary failed", message=frappe.get_traceback())


def send_preview(user, start=None):
	"""Email ``user`` the summary email for the week holding ``start`` (the week ahead by default), with
	the switch off or on: no claim, nobody else gets it. ``{"sent": bool, "message": str}``."""
	data = build_summary(start)
	to = notices.email_of(user)
	subject, html = summary_email(
		data,
		subject_prefix=_("Preview: "),
		note=_(
			"This is a preview sent to you alone. Nobody else was emailed, and the Monday email is unaffected."
		),
	)
	frappe.sendmail(recipients=[to], subject=subject, message=html)
	return {
		"sent": True,
		"message": _("Sent to {0}: the summary for the week of {1}.").format(to, day_text(data["first"])),
	}

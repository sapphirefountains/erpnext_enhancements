"""Jinja helpers for the Project schedule / task-list Print Formats.

Registered as ``jinja.methods`` in ``hooks.py`` and called from the templates in
``setup_print_formats.py``.

**Why the geometry is computed here and not in the template.** The Project
Schedule format draws real Gantt bars, and a bar needs ``left``/``width`` as a
percentage of the project's whole date span. Frappe's print sandbox is a
restricted Jinja environment — no arbitrary imports, no date arithmetic beyond
what ``frappe.utils`` exposes — so doing that inline would mean a page of
unreadable template expressions recomputing the same min/max on every row.

**Why HTML/CSS bars rather than an SVG or an image.** A Print Format is
rendered server-side with no JavaScript, so the browser-side SVG renderer
(``public/js/gantt_widget/gantt_export.js``) cannot run. Percentage-positioned
divs inside a fixed-width cell are the one construct that survives every PDF
backend, wkhtmltopdf's ancient Qt WebKit included.

Note for anyone testing this: **PDF generation is broken on production** for
environment reasons this repo cannot fix (``docs/pdf-generation.md`` —
wkhtmltopdf segfaults on any input, the bench Chromium traps on startup). The
HTML print *view* renders fine, and browser print-to-PDF works; the server-side
"Download PDF" button does not, and that is not a bug in this module.
"""

import frappe
from frappe.utils import cint, flt, getdate

# A project spanning years would otherwise produce a month header per column at
# an unreadable width; past this the header falls back to quarters.
MAX_MONTH_COLUMNS = 18


def _tasks_for(project):
	"""Flattened task tree for ``project`` (parent before child, with levels)."""
	from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import (
		_flatten_task_tree,
	)

	return _flatten_task_tree(project)


def _span(rows):
	"""(min start, max end) across the tasks that carry dates, else (None, None)."""
	starts = [getdate(t.get("exp_start_date")) for _lvl, t in rows if t.get("exp_start_date")]
	ends = [getdate(t.get("exp_end_date")) for _lvl, t in rows if t.get("exp_end_date")]
	if not starts and not ends:
		return None, None
	start = min(starts) if starts else min(ends)
	end = max(ends) if ends else max(starts)
	if end < start:
		end = start
	return start, end


def _month_columns(start, end, total_days):
	"""Header cells for the timeline, as percentage offsets.

	Steps by calendar month (or quarter when a month-per-column would be too
	narrow) so boundaries land where a reader expects them regardless of how
	many days each month has.
	"""
	from datetime import date

	months = []
	cursor = date(start.year, start.month, 1)
	step = 1
	# Count first, then widen the step, rather than emitting and trimming.
	count = 0
	probe = cursor
	while probe <= end:
		count += 1
		probe = date(probe.year + (probe.month // 12), (probe.month % 12) + 1, 1)
	if count > MAX_MONTH_COLUMNS:
		step = 3

	while cursor <= end:
		nxt = cursor
		for _ in range(step):
			nxt = date(nxt.year + (nxt.month // 12), (nxt.month % 12) + 1, 1)
		visible_start = max(cursor, start)
		visible_end = min(nxt, end)
		width_days = (visible_end - visible_start).days
		if width_days > 0:
			months.append(
				{
					"label": (
						visible_start.strftime("%b %Y")
						if step == 1
						else f"Q{((visible_start.month - 1) // 3) + 1} {visible_start.year}"
					),
					"left_pct": round((visible_start - start).days * 100.0 / total_days, 4),
					"width_pct": round(width_days * 100.0 / total_days, 4),
				}
			)
		cursor = nxt
	return months


def project_schedule_rows(project):
	"""Everything the Project Schedule Print Format needs, pre-computed.

	Returns a dict with ``start``/``end``/``months``/``rows``/``has_dates``.
	Each row carries ``left_pct`` and ``width_pct`` (percentages of the whole
	span) so the template only has to emit a positioned div.

	Undated tasks are kept — they are listed with no bar rather than dropped, so
	a printed schedule shows the work that still needs dates instead of quietly
	pretending it does not exist.
	"""
	if not project:
		return {"has_dates": False, "rows": [], "months": []}

	tree = _tasks_for(project)
	if not tree:
		return {"has_dates": False, "rows": [], "months": []}

	start, end = _span(tree)
	has_dates = bool(start and end)
	# +1 so a single-day project still has a non-zero span to divide by.
	total_days = ((end - start).days + 1) if has_dates else 1
	today = getdate()

	rows = []
	for level, task in tree:
		t_start = getdate(task["exp_start_date"]) if task.get("exp_start_date") else None
		t_end = getdate(task["exp_end_date"]) if task.get("exp_end_date") else None
		if t_start and not t_end:
			t_end = t_start
		if t_end and not t_start:
			t_start = t_end

		left_pct = width_pct = None
		if has_dates and t_start and t_end:
			left_pct = round((t_start - start).days * 100.0 / total_days, 4)
			# +1 day so a task that starts and ends on the same date is a
			# visible bar rather than a zero-width sliver.
			width_pct = round(((t_end - t_start).days + 1) * 100.0 / total_days, 4)
			if left_pct + width_pct > 100:
				width_pct = round(100 - left_pct, 4)

		progress = flt(task.get("progress") or 0)
		rows.append(
			{
				"level": level,
				"name": task["name"],
				"subject": task.get("subject") or "",
				"status": task.get("status") or "",
				"priority": task.get("priority") or "",
				"start": t_start,
				"end": t_end,
				"progress": progress,
				"expected_time": flt(task.get("expected_time") or 0),
				"is_milestone": cint(task.get("is_milestone")),
				"left_pct": left_pct,
				"width_pct": width_pct,
				"overdue": bool(t_end and t_end < today and progress < 100),
				"undated": not (t_start and t_end),
			}
		)

	return {
		"has_dates": has_dates,
		"start": start,
		"end": end,
		"total_days": total_days,
		"months": _month_columns(start, end, total_days) if has_dates else [],
		"rows": rows,
		"undated_count": sum(1 for r in rows if r["undated"]),
	}


def project_task_rows(project):
	"""The flattened task tree for the Project Task List Print Format."""
	data = project_schedule_rows(project)
	return data.get("rows") or []


@frappe.whitelist()
def get_project_schedule_preview(project):
	"""Whitelisted wrapper, for checking the numbers without rendering a format."""
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)
	return project_schedule_rows(project)

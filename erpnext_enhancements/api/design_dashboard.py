# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Design Dashboard widgets.

Water-engineering worklists over ``Water Feature Design``: what is in flight,
what is waiting on a human, what is carrying blockers, and which issued designs
have the least hydraulic margin.

Gating is the shared department contract in ``api/dashboard_widgets.py``: Design
roles per ``api/kpi.py``, plus a per-widget toggle defaulting OFF.

``docstatus < 2`` everywhere — a cancelled design is not work in progress, and
counting it would inflate every one of these lists.
"""

import frappe
from frappe.utils import flt, getdate, nowdate

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed

ROW_LIMIT = 25

# The Status Select, in workflow order (water_feature_design.json).
WIP_STATUSES = ("Draft", "Inputs Gathered", "Calculated", "Reviewed")
SIGNOFF_STATUS = "Reviewed"
ISSUED_STATUS = "Issued"

# Flow margin at or below this reads as thin: the design's delivered flow is
# barely above the circulation the feature requires, leaving nothing for fouling,
# head loss drift, or a pump curve that ages.
THIN_MARGIN_PCT = 10.0


def _days_since(value):
	if not value:
		return None
	return max(0, (getdate(nowdate()) - getdate(value)).days)


def _title(row):
	return row.get("design_title") or row.get("project") or row.get("name")


def _summary(row):
	"""The design's own one-line explanation of what is wrong.

	Prefers ``issue_summary``; falls back to the first non-blank line of
	``next_inputs_needed``. A whitespace-only Small Text has no first line, so the
	fallback is written to return "" rather than index an empty list.
	"""
	if row.get("issue_summary"):
		return row["issue_summary"]
	for line in (row.get("next_inputs_needed") or "").splitlines():
		if line.strip():
			return line.strip()
	return ""


@frappe.whitelist()
@widget_feed("Design", "wip_board")
def get_wip_board():
	"""In-flight designs, grouped by status, oldest touch first within each group.

	Age is measured from ``creation``, not ``modified``: the question this board
	answers is how long a design has been in the pipeline, and a design that gets
	touched weekly for two months is exactly the one worth seeing.
	"""
	rows = fetch_all(
		"Water Feature Design",
		filters={"docstatus": ("<", 2), "status": ("in", WIP_STATUSES)},
		fields=[
			"name",
			"design_title",
			"project",
			"customer",
			"status",
			"completion_percent",
			"blocker_count",
			"warning_count",
			"creation",
			"modified",
		],
		order_by="creation asc",
		limit=ROW_LIMIT,
	)

	designs = [
		{
			"name": r.name,
			"title": _title(r),
			"customer": r.customer or "",
			"status": r.status or "",
			"percent": flt(r.completion_percent),
			"blockers": int(r.blocker_count or 0),
			"warnings": int(r.warning_count or 0),
			"age_days": _days_since(r.creation),
			"idle_days": _days_since(r.modified),
		}
		for r in rows
	]

	# Counts cover the whole WIP population, not just the page shown.
	by_status = {}
	for status in WIP_STATUSES:
		by_status[status] = frappe.db.count(
			"Water Feature Design", {"docstatus": ("<", 2), "status": status}
		)

	return {"designs": designs, "by_status": by_status, "statuses": list(WIP_STATUSES)}


@frappe.whitelist()
@widget_feed("Design", "signoff_queue")
def get_signoff_queue():
	"""Designs that reached Reviewed but have not been Issued — waiting on a person.

	Age here is from ``modified``, which is when the design last moved; for a
	design sitting in Reviewed that is effectively how long it has been waiting.
	"""
	rows = fetch_all(
		"Water Feature Design",
		filters={"docstatus": ("<", 2), "status": SIGNOFF_STATUS},
		fields=[
			"name",
			"design_title",
			"project",
			"customer",
			"completion_percent",
			"blocker_count",
			"warning_count",
			"issue_ready",
			"modified",
		],
		order_by="modified asc",
		limit=ROW_LIMIT,
	)

	designs = [
		{
			"name": r.name,
			"title": _title(r),
			"customer": r.customer or "",
			"percent": flt(r.completion_percent),
			"blockers": int(r.blocker_count or 0),
			"warnings": int(r.warning_count or 0),
			"ready": bool(r.issue_ready),
			"waiting_days": _days_since(r.modified),
		}
		for r in rows
	]
	return {"designs": designs}


@frappe.whitelist()
@widget_feed("Design", "handoff_readiness")
def get_handoff_readiness():
	"""Designs carrying blockers or warnings, blockers first.

	This is the pre-hand-off catch: a design that reaches Production with open
	blockers becomes a build problem, and the build is where it costs money to
	find out. ``issue_summary`` is the design's own one-line explanation, so the
	widget shows the engine's words rather than re-deriving them.
	"""
	rows = frappe.db.sql(
		"""
		select name, design_title, project, customer, status, completion_percent,
		       blocker_count, warning_count, issue_summary, issue_ready, next_inputs_needed
		from `tabWater Feature Design`
		where docstatus < 2
		  and (coalesce(blocker_count, 0) > 0 or coalesce(warning_count, 0) > 0)
		order by coalesce(blocker_count, 0) desc, coalesce(warning_count, 0) desc, modified asc
		limit %(limit)s
		""",
		{"limit": ROW_LIMIT},
		as_dict=True,
	)

	designs = [
		{
			"name": r.name,
			"title": _title(r),
			"customer": r.customer or "",
			"status": r.status or "",
			"blockers": int(r.blocker_count or 0),
			"warnings": int(r.warning_count or 0),
			"ready": bool(r.issue_ready),
			"summary": _summary(r),
		}
		for r in rows
	]
	return {"designs": designs}


@frappe.whitelist()
@widget_feed("Design", "hydraulic_headroom")
def get_hydraulic_headroom():
	"""Issued designs ranked by flow margin — thinnest first.

	Margin is delivered ``design_flow_gpm`` against ``required_circulation_gpm``.
	A design at or below ``THIN_MARGIN_PCT`` has nothing in hand for fouling, head
	loss drift or an ageing pump curve; a negative margin means the selection does
	not meet the requirement at all and is flagged separately.

	Designs with no circulation requirement recorded are skipped rather than shown
	as infinite margin — a missing denominator is a data gap, not good news.
	"""
	rows = frappe.db.sql(
		"""
		select name, design_title, project, customer, selected_pump,
		       design_flow_gpm, required_circulation_gpm, computed_tdh_ft
		from `tabWater Feature Design`
		where docstatus < 2
		  and status = %(issued)s
		  and coalesce(required_circulation_gpm, 0) > 0
		  and coalesce(design_flow_gpm, 0) > 0
		order by (design_flow_gpm - required_circulation_gpm) / required_circulation_gpm asc
		limit %(limit)s
		""",
		{"issued": ISSUED_STATUS, "limit": ROW_LIMIT},
		as_dict=True,
	)

	designs = []
	for r in rows:
		required = flt(r.required_circulation_gpm)
		delivered = flt(r.design_flow_gpm)
		margin = (delivered - required) / required * 100.0 if required else None
		designs.append(
			{
				"name": r.name,
				"title": _title(r),
				"customer": r.customer or "",
				"pump": r.selected_pump or "",
				"required_gpm": required,
				"design_gpm": delivered,
				"tdh_ft": flt(r.computed_tdh_ft),
				"margin_pct": round(margin, 1) if margin is not None else None,
				"under": margin is not None and margin < 0,
				"thin": margin is not None and 0 <= margin <= THIN_MARGIN_PCT,
			}
		)
	return {"designs": designs, "thin_pct": THIN_MARGIN_PCT}

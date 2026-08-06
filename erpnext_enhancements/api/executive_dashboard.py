# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Executive Dashboard widgets.

Unlike the other department dashboards, **every widget here reads the nightly
KPI Snapshot and nothing else.** An executive view exists to be compared across
departments and across days; a number that changes on every refresh cannot do
either job, and the snapshot engine exists precisely so the dashboards never
aggregate on load.

That has one consequence worth stating plainly: these widgets are only as fresh
as last night's run. The Risk Queue therefore reports snapshot staleness as a
first-class risk rather than hiding it — a scorecard quietly serving week-old
numbers is the failure mode this design is most exposed to.

Gating is the shared department contract in ``api/dashboard_widgets.py``:
Executive roles per ``api/kpi.py``, plus a per-widget toggle defaulting OFF.
Drill-through links are only rendered for departments the viewer can actually
open, so an Accounts Manager does not get a link into the HR dashboard.
"""

import frappe
from frappe.utils import flt, getdate, nowdate

from erpnext_enhancements.api.dashboard_widgets import can_view, fetch_all, widget_feed
from erpnext_enhancements.kpi_dashboards import snapshots

# Departments shown on the scorecard, in reading order. Executive is excluded —
# it is a rollup of these, so scoring it alongside them double-counts.
SCORECARD_DEPARTMENTS = ("Finance", "Sales", "Marketing", "Operations", "Design", "Production", "HR")

# A snapshot older than this is called out. The engine runs nightly, so two days
# means at least one run did not land.
SNAPSHOT_STALE_DAYS = 2

RISK_LIMIT = 12

PERIOD = "Daily"


def _latest_snapshot(department):
	"""The department's most recent snapshot header, or None."""
	rows = fetch_all(
		"KPI Snapshot",
		filters={"department": department, "period": PERIOD},
		fields=["name", "snapshot_date", "generated_at", "generation_error"],
		order_by="snapshot_date desc",
		limit=1,
	)
	return rows[0] if rows else None


def _values(snapshot_name):
	return fetch_all(
		"KPI Snapshot Value",
		filters={"parent": snapshot_name},
		fields=["kpi_key", "label", "value", "value_text", "unit", "status", "trend_pct", "is_stale"],
		order_by="idx asc",
		limit=200,
	)


def _value_map(department):
	"""kpi_key -> row, for the departments the rollup widgets pull figures from."""
	snapshot = _latest_snapshot(department)
	if not snapshot:
		return {}, None
	return {v.kpi_key: v for v in _values(snapshot.name)}, snapshot


def _age_days(snapshot):
	if not snapshot or not snapshot.snapshot_date:
		return None
	return (getdate(nowdate()) - getdate(snapshot.snapshot_date)).days


def _figure(values, key):
	"""One KPI as a display-ready dict, or None when the department never
	produced it (a guarded aggregator that stayed silent, e.g. no time data)."""
	row = values.get(key)
	if not row:
		return None
	return {
		"key": key,
		"label": row.label or key,
		"value": flt(row.value),
		"text": row.value_text or "",
		"unit": row.unit or "",
		"status": row.status or "",
		"trend_pct": flt(row.trend_pct) if row.trend_pct is not None else None,
		"stale": bool(row.is_stale),
	}


@frappe.whitelist()
@widget_feed("Executive", "scorecard")
def get_scorecard():
	"""One tile per department: how many of its KPIs are Good / Watch / Bad.

	Unstatused KPIs — the ones with no target set — are counted separately rather
	than folded into Good. A department with no targets configured would otherwise
	show a perfect green card while measuring nothing.
	"""
	cards = []
	for department in SCORECARD_DEPARTMENTS:
		snapshot = _latest_snapshot(department)
		if not snapshot:
			cards.append(
				{
					"department": department,
					"available": False,
					"linkable": can_view(department),
					"reason": "No snapshot yet.",
				}
			)
			continue

		counts = {"good": 0, "watch": 0, "bad": 0, "ungraded": 0}
		stale_values = 0
		for row in _values(snapshot.name):
			status = (row.status or "").lower()
			if status in counts:
				counts[status] += 1
			else:
				counts["ungraded"] += 1
			stale_values += 1 if row.is_stale else 0

		age = _age_days(snapshot)
		cards.append(
			{
				"department": department,
				"available": True,
				"linkable": can_view(department),
				"counts": counts,
				"total": sum(counts.values()),
				"snapshot_date": str(getdate(snapshot.snapshot_date)) if snapshot.snapshot_date else None,
				"age_days": age,
				"stale": age is not None and age > SNAPSHOT_STALE_DAYS,
				"stale_values": stale_values,
				"error": (snapshot.generation_error or "").strip(),
			}
		)

	return {"cards": cards, "stale_after_days": SNAPSHOT_STALE_DAYS}


@frappe.whitelist()
@widget_feed("Executive", "cash_runway")
def get_cash_runway():
	"""Cash, receivables and DSO from the latest Finance snapshot."""
	values, snapshot = _value_map("Finance")
	if not snapshot:
		return {"figures": [], "unavailable": "No Finance snapshot yet — it generates overnight."}

	keys = ("cash_collected_30", "revenue_30", "ar_outstanding", "dso")
	figures = [f for f in (_figure(values, key) for key in keys) if f]
	return {
		"figures": figures,
		"snapshot_date": str(getdate(snapshot.snapshot_date)) if snapshot.snapshot_date else None,
		"age_days": _age_days(snapshot),
	}


@frappe.whitelist()
@widget_feed("Executive", "bookings_backlog")
def get_bookings_backlog():
	"""What has been won, what is in the pipeline, and what is booked but unbuilt.

	Sales and Production snapshots are read separately and their dates reported
	separately: if one department's nightly run failed, the widget should not
	present a stale figure next to a fresh one as though both were current.
	"""
	sales_values, sales_snapshot = _value_map("Sales")
	production_values, production_snapshot = _value_map("Production")

	if not sales_snapshot and not production_snapshot:
		return {"figures": [], "unavailable": "No Sales or Production snapshot yet."}

	figures = []
	for key in ("won_value_30", "open_pipeline_value", "win_rate_90"):
		figure = _figure(sales_values, key)
		if figure:
			figure["source"] = "Sales"
			figure["as_of"] = (
				str(getdate(sales_snapshot.snapshot_date))
				if sales_snapshot and sales_snapshot.snapshot_date
				else None
			)
			figures.append(figure)

	for key in ("backlog_value", "active_builds"):
		figure = _figure(production_values, key)
		if figure:
			figure["source"] = "Production"
			figure["as_of"] = (
				str(getdate(production_snapshot.snapshot_date))
				if production_snapshot and production_snapshot.snapshot_date
				else None
			)
			figures.append(figure)

	return {"figures": figures}


@frappe.whitelist()
@widget_feed("Executive", "risk_queue")
def get_risk_queue():
	"""What is red company-wide: failed or stale snapshot runs, then Bad KPIs.

	Snapshot problems are listed *above* Bad KPIs on purpose. A department whose
	nightly run failed has no Bad KPIs at all — it has no KPIs — and reading its
	silence as health is the specific mistake this ordering prevents.
	"""
	risks = []
	bad = []

	for department in snapshots.AGGREGATORS:
		if department == "Executive":
			continue
		snapshot = _latest_snapshot(department)
		if not snapshot:
			risks.append(
				{
					"kind": "missing",
					"department": department,
					"detail": "No snapshot has ever been generated.",
					"linkable": can_view(department),
				}
			)
			continue

		if (snapshot.generation_error or "").strip():
			risks.append(
				{
					"kind": "error",
					"department": department,
					"detail": snapshot.generation_error.strip()[:200],
					"linkable": can_view(department),
				}
			)

		age = _age_days(snapshot)
		if age is not None and age > SNAPSHOT_STALE_DAYS:
			risks.append(
				{
					"kind": "stale",
					"department": department,
					"detail": f"Last snapshot {age} days ago.",
					"linkable": can_view(department),
				}
			)

		for row in _values(snapshot.name):
			if (row.status or "") == "Bad":
				bad.append(
					{
						"kind": "kpi",
						"department": department,
						"label": row.label or row.kpi_key,
						"detail": row.value_text or "",
						"stale": bool(row.is_stale),
						"linkable": can_view(department),
					}
				)

	return {
		"risks": risks,
		"bad": bad[:RISK_LIMIT],
		"bad_total": len(bad),
		"stale_after_days": SNAPSHOT_STALE_DAYS,
	}

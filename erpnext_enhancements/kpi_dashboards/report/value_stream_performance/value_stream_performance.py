# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Value Stream Performance — the standing weekly meeting view.

Spend, opportunity count, win rate, average deal size and revenue, per value
stream, on one screen.

## Two constraints that shaped this, both from the brief

**It must need no parameters.** Somebody opens it live in a meeting; a report
that starts with an empty filter bar and a "select a date range" prompt wastes
the first ninety seconds of that meeting every week. So it defaults to a rolling
twelve months and the filters are refinements, not prerequisites.

**It must load fast.** Six value streams over a year is not much data, but the
naive shape — one query per stream per metric — is thirty round trips. Everything
here is **four queries total**, each aggregating in SQL and grouping in Python.
That matters more than it looks: the Value Stream child table has ~1,460 rows
across three parent doctypes, so a per-stream subquery re-scans it every time.

**Native ERPNext, not Triton.** Explicitly required — Triton is scoped to
management and exploratory reporting, and this is an operational report that has
to exist for everyone.

## What the numbers mean, and where they are honest about not knowing

* **Opportunities / Won / Win rate** — counted from `Opportunity.custom_value_stream`
  (the Table MultiSelect). An opportunity tagged with two streams counts once
  toward **each**, so the columns deliberately do not sum to the company total.
  That is the correct reading for "how is Events doing", and the wrong one for
  "what is our total pipeline" — the report says so in its own message rather
  than leaving somebody to discover it in a meeting.
* **Win rate** is won / (won + lost), not won / all. Open deals have not lost;
  including them makes every rate look terrible and move whenever somebody adds
  a lead.
* **Revenue** comes from the linked Projects' actual billed amounts where they
  exist, falling back to the opportunity amount. Until WP-7's reconciliation
  backlog is worked through, some won deals have no Project and therefore no
  billed figure — the report counts those on the opportunity amount and reports
  how many it did that for.
* **Spend** is only what has been explicitly allocated to a stream on
  `Marketing Spend.value_stream`. A channel serving several streams is left
  blank on purpose, and shows up in the **Unallocated** row rather than being
  divided arbitrarily. A made-up split is worse than an honest gap, because it
  looks like data.

## Cost per opportunity is deliberately absent from unallocated-heavy periods

If almost all spend is unallocated, a per-stream cost-per-opportunity figure is
noise dressed as insight. The column is computed only where the stream has
allocated spend.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, nowdate

#: Statuses that count as won and lost. "Closed" is neither — on this site it is
#: used for administratively closed deals, and 144 opportunities carry it.
WON_STATUSES = ("Closed Won",)
LOST_STATUSES = ("Lost",)

#: Rolling window when no dates are given. Twelve months is a full seasonal cycle
#: for a business whose Events stream is summer-weighted.
DEFAULT_MONTHS = 12

#: Label for spend that has not been attributed to a stream.
UNALLOCATED = "Unallocated"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date, to_date = _window(filters)

	streams = _all_streams()
	opportunities = _opportunity_rollup(from_date, to_date)
	revenue = _revenue_rollup(from_date, to_date)
	spend = _spend_rollup(from_date, to_date)

	data = _assemble(streams, opportunities, revenue, spend)
	return get_columns(), data, _message(from_date, to_date, spend, revenue), get_chart(data)


def _window(filters):
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -DEFAULT_MONTHS))
	return from_date, to_date


def get_columns():
	return [
		{"label": _("Value Stream"), "fieldname": "value_stream", "fieldtype": "Data", "width": 150},
		{"label": _("Spend"), "fieldname": "spend", "fieldtype": "Currency", "width": 120},
		{"label": _("Opportunities"), "fieldname": "opportunities", "fieldtype": "Int", "width": 120},
		{"label": _("Won"), "fieldname": "won", "fieldtype": "Int", "width": 80},
		{"label": _("Lost"), "fieldname": "lost", "fieldtype": "Int", "width": 80},
		{"label": _("Win Rate"), "fieldname": "win_rate", "fieldtype": "Percent", "width": 100},
		{"label": _("Revenue"), "fieldname": "revenue", "fieldtype": "Currency", "width": 140},
		{"label": _("Avg Deal"), "fieldname": "avg_deal", "fieldtype": "Currency", "width": 130},
		{"label": _("Cost / Opp"), "fieldname": "cost_per_opportunity", "fieldtype": "Currency", "width": 120},
	]


# ------------------------------------------------------------------ the queries
#
# Four, total. Each aggregates in SQL and is grouped in Python — see the module
# docstring for why a per-stream subquery is the wrong shape here.


def _all_streams():
	"""The master list. PLURAL doctype = master, SINGULAR = the child table that
	attaches it to records. Inverted from frappe convention; see
	crm_enhancements/README.md."""
	try:
		return frappe.get_all("Value Streams", pluck="name", order_by="name")
	except Exception:
		return []


def _opportunity_rollup(from_date, to_date):
	"""Opportunity counts per stream. One row per (stream, outcome)."""
	rows = frappe.db.sql(
		"""
		SELECT
			vs.value_stream AS stream,
			SUM(1) AS total,
			SUM(CASE WHEN o.status IN %(won)s THEN 1 ELSE 0 END) AS won,
			SUM(CASE WHEN o.status IN %(lost)s THEN 1 ELSE 0 END) AS lost,
			SUM(CASE WHEN o.status IN %(won)s THEN o.opportunity_amount ELSE 0 END) AS won_amount
		FROM `tabValue Stream` vs
		INNER JOIN `tabOpportunity` o
			ON o.name = vs.parent AND vs.parenttype = 'Opportunity'
		WHERE o.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY vs.value_stream
		""",
		{"won": WON_STATUSES, "lost": LOST_STATUSES, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	return {row.stream: row for row in rows if row.stream}


def _revenue_rollup(from_date, to_date):
	"""Billed revenue per stream, from the Projects linked to won opportunities.

	`total_billed_amount` is the honest figure where a Project exists. Where it
	does not — the WP-7 backlog — the opportunity amount is used instead, and the
	count of those is returned so the report can say so.
	"""
	rows = frappe.db.sql(
		"""
		SELECT
			vs.value_stream AS stream,
			SUM(COALESCE(NULLIF(p.total_billed_amount, 0), o.opportunity_amount, 0)) AS revenue,
			SUM(CASE WHEN p.name IS NULL THEN 1 ELSE 0 END) AS without_project
		FROM `tabValue Stream` vs
		INNER JOIN `tabOpportunity` o
			ON o.name = vs.parent AND vs.parenttype = 'Opportunity'
		LEFT JOIN `tabProject` p ON p.custom_opportunity = o.name
		WHERE o.status IN %(won)s
		  AND o.transaction_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY vs.value_stream
		""",
		{"won": WON_STATUSES, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	return {row.stream: row for row in rows if row.stream}


def _spend_rollup(from_date, to_date):
	"""Allocated spend per stream, plus the unallocated total.

	Guarded: `value_stream` arrived on Marketing Spend in v1.243.0, and this
	report must not 500 on a bench that has not migrated.
	"""
	if not frappe.db.has_column("Marketing Spend", "value_stream"):
		return {}
	rows = frappe.db.sql(
		"""
		SELECT COALESCE(NULLIF(value_stream, ''), %(unallocated)s) AS stream,
		       SUM(amount) AS spend
		FROM `tabMarketing Spend`
		WHERE month BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY stream
		""",
		{"unallocated": UNALLOCATED, "from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	return {row.stream: flt(row.spend) for row in rows}


# ------------------------------------------------------------------- assembly


def _assemble(streams, opportunities, revenue, spend):
	data = []
	for stream in streams:
		opp = opportunities.get(stream) or {}
		rev = revenue.get(stream) or {}
		total = int(opp.get("total") or 0)
		won = int(opp.get("won") or 0)
		lost = int(opp.get("lost") or 0)
		decided = won + lost
		earned = flt(rev.get("revenue"))
		allocated = flt(spend.get(stream))

		data.append({
			"value_stream": stream,
			"spend": allocated,
			"opportunities": total,
			"won": won,
			"lost": lost,
			# won / (won + lost). Including open deals makes every rate look bad
			# and move whenever somebody adds a lead.
			"win_rate": (won / decided * 100.0) if decided else None,
			"revenue": earned,
			"avg_deal": (earned / won) if won else None,
			# Only where spend was actually attributed — a cost-per-opportunity
			# derived from zero allocated spend is a zero pretending to be a fact.
			"cost_per_opportunity": (allocated / total) if (allocated and total) else None,
		})

	data.sort(key=lambda row: flt(row["revenue"]), reverse=True)

	unallocated = flt(spend.get(UNALLOCATED))
	if unallocated:
		data.append({
			"value_stream": UNALLOCATED,
			"spend": unallocated,
			"opportunities": None, "won": None, "lost": None,
			"win_rate": None, "revenue": None, "avg_deal": None,
			"cost_per_opportunity": None,
		})
	return data


def _message(from_date, to_date, spend, revenue):
	notes = [
		_("{0} to {1}. An opportunity tagged with several value streams counts once toward "
		  "<b>each</b>, so these columns do not sum to the company total — that is the right "
		  "reading per stream and the wrong one for total pipeline.").format(from_date, to_date)
	]

	if not spend:
		notes.append(
			_("<b>No marketing spend recorded for this period.</b> Cost per opportunity cannot be "
			  "computed until it is — use <i>Marketing Spend Rollup → Import</i> to load it.")
		)
	elif spend.get(UNALLOCATED):
		allocated = sum(v for k, v in spend.items() if k != UNALLOCATED)
		total = allocated + flt(spend[UNALLOCATED])
		if total and (flt(spend[UNALLOCATED]) / total) > 0.5:
			notes.append(
				_("<b>Most spend is unallocated</b> ({0} of {1}). Per-stream cost figures below "
				  "cover only what was explicitly attributed — they are not a division of the "
				  "whole budget.").format(
					frappe.format_value(spend[UNALLOCATED], {"fieldtype": "Currency"}),
					frappe.format_value(total, {"fieldtype": "Currency"}),
				)
			)

	orphaned = sum(int(row.get("without_project") or 0) for row in revenue.values())
	if orphaned:
		notes.append(
			_("{0} won opportunities in this window have no linked Project, so their revenue is the "
			  "quoted amount rather than the billed one. Work the <i>Closed Won Reconciliation</i> "
			  "report to close that gap.").format(orphaned)
		)

	return "<br><br>".join(notes)


def get_chart(data):
	rows = [row for row in data if row["value_stream"] != UNALLOCATED]
	labels = [row["value_stream"] for row in rows]
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Revenue"), "values": [flt(row["revenue"]) for row in rows]},
				{"name": _("Spend"), "values": [flt(row["spend"]) for row in rows]},
			],
		},
		"type": "bar",
	}

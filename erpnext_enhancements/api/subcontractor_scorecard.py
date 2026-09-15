# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Endpoints for subcontractor scorecards — WI-075 sub-phase N.

Four-space indented, like the rest of ``api/``. See ``api/README.md``.

Two things here are deliberate and worth not undoing.

**Nothing returns a bare score.** Every read hands back ``state`` and ``score_display`` beside the
numeric fields, because a scorecard's number quoted without how much of it is real is the failure
this whole sub-phase exists to prevent — and this is a vendor scorecard, the one artifact in the
programme that gets printed and carried into a negotiation.

**The manual adjustment is signed, by the server.** ``record_adjustment`` stamps the session user
and the server clock; the browser proposes neither. The plan's reasoning is that the first time a
score is wrong with no way to say so on the record, people stop using the record — so the way is
provided, and it leaves a name beside it.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from erpnext_enhancements.quality import scorecard, scorecard_build

DOCTYPE = "Subcontractor Scorecard"


def _shape(doc):
    """One scorecard, with the number and its footing inseparable."""
    return {
        "name": doc.name,
        "supplier": doc.supplier,
        "supplier_name": doc.supplier_name,
        "period_label": doc.period_label,
        "period_start": doc.period_start,
        "period_end": doc.period_end,
        "state": doc.state,
        # The sentence, not the figure. Read this one.
        "score_display": doc.score_display,
        "score_percent": doc.score_percent,
        "final_score_percent": doc.final_score_percent,
        "measures_met": doc.measures_met,
        "measures_judgeable": doc.measures_judgeable,
        "measures_total": doc.measures_total,
        "manual_adjustment": doc.manual_adjustment,
        "adjustment_reason": doc.adjustment_reason,
        "adjusted_by": doc.adjusted_by,
        "adjusted_on": doc.adjusted_on,
        "engagement_reason": doc.engagement_reason,
        "generated_on": doc.generated_on,
        "measures": [
            {
                "measure_key": row.measure_key,
                "label": row.label,
                "value_display": row.value_display,
                "coverage": row.coverage,
                "met": row.met,
                "sample_size": row.sample_size,
                "threshold_display": row.threshold_display,
                "note": row.note,
            }
            for row in (doc.get("measures") or [])
        ],
        "evidence": [
            {
                "measure_key": row.measure_key,
                "document_type": row.document_type,
                "document_name": row.document_name,
                "occurred_on": row.occurred_on,
                "summary": row.summary,
            }
            for row in (doc.get("evidence") or [])
        ],
    }


@frappe.whitelist()
def get_scorecard(scorecard_name):
    """One scorecard in full, measures and evidence included.

    The evidence rows are the reason this record exists rather than a live report: a disputed
    score can be opened rather than argued.
    """
    doc = frappe.get_doc(DOCTYPE, scorecard_name)
    doc.check_permission("read")
    return _shape(doc)


@frappe.whitelist()
def get_supplier_scorecards(supplier, limit=12):
    """A subcontractor's scorecard history, newest first.

    ``state`` travels with every row. A list of scores where some are ``Not Measurable`` zeros and
    some are real would show a trend that never happened.
    """
    frappe.get_doc("Supplier", supplier).check_permission("read")
    return frappe.get_all(
        DOCTYPE,
        filters={"supplier": supplier},
        fields=[
            "name",
            "period_label",
            "period_start",
            "state",
            "score_display",
            "score_percent",
            "final_score_percent",
            "measures_met",
            "measures_judgeable",
            "measures_total",
        ],
        order_by="period_start desc",
        limit_page_length=int(limit or 12),
    )


@frappe.whitelist()
def build_scorecard(supplier, period_start):
    """Build the scorecard for one subcontractor and month, on demand.

    Returns the existing one untouched if it is already there. A scorecard that has been read,
    adjusted and signed is a record of what was known then; rebuilding over it would discard
    somebody's adjustment and the reason they gave.

    Refuses a month that has not finished: half a month of evidence scored against a whole
    month's thresholds reports every subcontractor as improving, every time, until it ends.
    """
    frappe.get_doc("Supplier", supplier).check_permission("read")
    if not frappe.has_permission(DOCTYPE, "create"):
        frappe.throw(_("You are not permitted to build scorecards."), frappe.PermissionError)

    start, end, label = scorecard_build.period_for(period_start)
    closed = [row[0] for row in scorecard_build.closed_periods()]
    if start not in closed:
        frappe.throw(
            _("{0} is not a finished month. Scorecards are only built for months that have ended.")
            .format(label)
        )

    name = scorecard_build.build(supplier, start, end, label)
    frappe.db.commit()
    return _shape(frappe.get_doc(DOCTYPE, name))


@frappe.whitelist()
def record_adjustment(scorecard_name, adjustment, reason):
    """Apply a signed manual adjustment to a scorecard's score.

    The reason is required for any non-zero adjustment — checked here **and** in the controller,
    because a rule enforced on one path only is a rule with a door left open.
    """
    doc = frappe.get_doc(DOCTYPE, scorecard_name)
    doc.check_permission("write")

    errors = scorecard.adjustment_errors(adjustment, reason)
    if errors:
        frappe.throw("<br>".join(errors), title=_("Adjustment cannot be saved"))

    doc.manual_adjustment = adjustment
    doc.adjustment_reason = reason
    doc.save()
    frappe.db.commit()
    return _shape(doc)


@frappe.whitelist()
def get_measure_catalog():
    """What every measure is, what it is judged against, and where it comes from.

    Served so a reader can see that two of the nine measures have **no source at all** — rework
    hours and certificate-of-insurance currency are recorded nowhere on this site. They are listed
    rather than omitted so the gap appears on the artifact instead of being forgotten.
    """
    return [
        {
            "measure_key": key,
            "label": label,
            "uom": uom,
            "direction": direction,
            "source": source,
            "threshold": threshold,
            "note": note,
            "has_source": source != scorecard.SOURCE_NONE,
        }
        for key, label, uom, direction, source, threshold, note in scorecard.MEASURES
    ]


@frappe.whitelist()
def get_period_summary(period_start):
    """Every scorecard for one month, with how many were measurable at all.

    ``not_measurable`` is returned as a first-class figure rather than left to be counted from the
    rows. On this site today it will equal the total, and a summary that made that easy to miss
    would be the polite version of claiming every subcontractor is flawless.
    """
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    start = getdate(scorecard_build.period_for(period_start)[0])
    rows = frappe.get_all(
        DOCTYPE,
        filters={"period_start": start},
        fields=["name", "supplier", "supplier_name", "state", "score_display", "score_percent"],
        order_by="supplier_name asc",
    )
    return {
        "period_start": start,
        "total": len(rows),
        "not_measurable": len([r for r in rows if r.state == scorecard.STATE_NOT_MEASURABLE]),
        "rows": rows,
    }

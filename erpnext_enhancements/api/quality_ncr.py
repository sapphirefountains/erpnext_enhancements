# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Acknowledging a Critical non-conformance alert — WI-075 sub-phase G.

One entry point, and its whole job is to turn "the system emailed three people" into "these
people have seen this", which are not the same claim. The first is a fact about a mail queue.

Two refusals, both deliberate
------------------------------

**Somebody who was never notified cannot acknowledge.** An acknowledgement row from a person
who was never on the list is not evidence of anything — it is a reassuring green tick with
nothing behind it, on the one record in this module whose entire purpose is to be evidence. So
the endpoint refuses rather than recording it. The rule lives in
``quality/alerting.may_acknowledge`` and is tested without a bench.

**Nobody acknowledges on anybody else's behalf.** The stamp is always ``frappe.session.user``;
there is no recipient parameter. An endpoint that accepted one would let a single caller mark
all three rows and produce exactly the record this design exists to prevent.

Indentation note: this file is 4-space, matching the rest of ``api/``. See ``api/README.md``.
"""

import frappe
from frappe import _

from erpnext_enhancements.quality import alerting, critical_alerts


@frappe.whitelist()
def acknowledge_critical_alert(ncr, note=None):
    """Stamp the calling user's acknowledgement row. Returns the acknowledged/total counts.

    Permission is checked explicitly. A whitelisted function is reachable over HTTP by any
    logged-in user, and session permissions are not applied on the caller's behalf.

    ``read`` and not ``write`` is the right gate, and the distinction matters: a President who
    can open the NCR should be able to say they have seen it without holding write access to
    the quality record. Being on the notified list is the real authorisation, and that check is
    the one below it.
    """
    frappe.has_permission("Non Conformance", "read", doc=ncr, throw=True)

    user = frappe.session.user
    if user in alerting.NEVER_NOTIFY:
        frappe.throw(_("Acknowledge this while signed in as yourself."), title=_("Not a recipient"))

    acknowledged, total = critical_alerts.record_acknowledgement(ncr, user, note)
    return {
        "acknowledged": acknowledged,
        "total": total,
        "message": _("{0} of {1} recipients have acknowledged this.").format(acknowledged, total),
    }


@frappe.whitelist()
def critical_alert_state(ncr):
    """Who was told, when, and who has answered — for the button and the form indicator.

    Read-only and cheap, so the form can call it on load. Returns the rows as they stand rather
    than a summary sentence, because "two of three" hides which one is missing, and which one
    is missing is the only actionable part.
    """
    frappe.has_permission("Non Conformance", "read", doc=ncr, throw=True)

    doc = frappe.get_doc("Non Conformance", ncr)
    rows = doc.get(critical_alerts.ACK_FIELD) or []
    acknowledged, total = alerting.acknowledgement_state(rows)
    return {
        "sent_on": doc.get(critical_alerts.SENT_FIELD),
        "acknowledged": acknowledged,
        "total": total,
        "may_acknowledge": alerting.may_acknowledge(rows, frappe.session.user),
        "mine_is_acknowledged": any(
            row.user == frappe.session.user and row.acknowledged_on for row in rows
        ),
        "recipients": [
            {
                "user": row.user,
                "full_name": frappe.utils.get_fullname(row.user),
                "role_label": row.role_label,
                "notified_on": row.notified_on,
                "acknowledged_on": row.acknowledged_on,
                "reminder_count": row.reminder_count or 0,
            }
            for row in rows
        ],
    }

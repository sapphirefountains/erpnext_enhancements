# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Approving a change order — WI-075 sub-phase K.

The approval stamps are read-only on the form and are written only here, from the **server
clock** and the **session user**. That is not ceremony: the old client path on
`process_steps.complete_step` let the browser propose its own timestamp, and the audit that
followed found retroactive box-ticking. On a commercial instrument the date somebody agreed to
pay more is the fact most worth being able to defend.

Two approvals, two different acts
----------------------------------

The **project manager** approves that the work is right and the figure is real. The **customer**
approves that they will pay for it. Both are required before submit, and submit is the lock —
after it the added acceptance criteria start appearing on inspections for the milestones they
name, which is the whole reason a change order is worth raising rather than agreeing verbally.

Recording the customer's approval is deliberately not the same act as the customer clicking
something. Approval arrives by signature, by email, or in a meeting, and `customer_approval_note`
is where a person says which. Pretending a portal click is the only valid form would mean the
common case gets recorded as nothing at all.

Indentation note: this file is 4-space, matching the rest of ``api/``. See ``api/README.md``.
"""

import frappe
from frappe import _

from erpnext_enhancements.quality import change_orders


def _editable(name):
    doc = frappe.get_doc("Change Order", name)
    if doc.docstatus != 0:
        frappe.throw(
            _("{0} is already locked. Approvals are recorded before it is submitted.").format(name),
            title=_("Already locked"),
        )
    return doc


@frappe.whitelist()
def approve_change_order(change_order, note=None):
    """Record the project manager's approval. Returns the change order's derived state.

    Permission is checked explicitly — a whitelisted function is reachable over HTTP by any
    logged-in user, and session permissions are not applied on the caller's behalf. ``submit``
    is the right gate rather than ``write``: approving is the act that makes locking possible,
    so whoever may approve should be somebody who could lock it.
    """
    frappe.has_permission("Change Order", "submit", doc=change_order, throw=True)
    doc = _editable(change_order)

    if doc.pm_approved:
        frappe.throw(
            _("{0} was already approved by {1} on {2}.").format(
                change_order, doc.pm_approved_by, doc.pm_approved_on
            ),
            title=_("Already approved"),
        )

    doc.pm_approved = 1
    doc.pm_approved_by = frappe.session.user
    doc.pm_approved_on = frappe.utils.now_datetime()
    if note:
        doc.notes = f"{doc.notes}\n{note}" if doc.notes else note
    doc.save()
    return _state(doc)


@frappe.whitelist()
def record_customer_approval(change_order, how):
    """Record that the customer has agreed, and **how**.

    ``how`` is required. A customer approval with no account of how it was obtained is the
    sentence missing from every change-order dispute — "they said yes" is not a record, and the
    field exists so that whoever writes it has to think for a second about whether they can point
    at something.
    """
    frappe.has_permission("Change Order", "submit", doc=change_order, throw=True)
    how = (how or "").strip()
    if not how:
        frappe.throw(
            _("Say how the customer approved — a signature, an email, or the meeting it was "
              "agreed in. An approval nobody can point at is not a record."),
            title=_("How did they approve?"),
        )

    doc = _editable(change_order)
    doc.customer_approved = 1
    doc.customer_approved_on = frappe.utils.now_datetime()
    doc.customer_approval_note = how
    doc.save()
    return _state(doc)


@frappe.whitelist()
def revoke_approval(change_order, which):
    """Undo an approval that was recorded in error. ``which`` is ``pm`` or ``customer``.

    Kept deliberately simple and only available before submit. A change order approved by
    mistake is corrected here; one that was *wrong* is cancelled and re-raised, because amending
    is refused — see the controller.
    """
    frappe.has_permission("Change Order", "submit", doc=change_order, throw=True)
    doc = _editable(change_order)

    if which == "pm":
        doc.pm_approved = 0
        doc.pm_approved_by = None
        doc.pm_approved_on = None
    elif which == "customer":
        doc.customer_approved = 0
        doc.customer_approved_on = None
        doc.customer_approval_note = None
    else:
        frappe.throw(_("Approval to revoke must be 'pm' or 'customer'."))

    doc.save()
    return _state(doc)


@frappe.whitelist()
def get_project_change_orders(project):
    """Every change order on a project, with the totals a person actually asks for.

    Additions and credits are reported separately as positive figures alongside the net. A single
    net number hides a job that added fifty thousand and credited forty-eight, which is a very
    different job from one that barely changed.
    """
    frappe.has_permission("Project", "read", doc=project, throw=True)
    if not frappe.db.exists("DocType", "Change Order"):
        return {"change_orders": [], "totals": change_orders.totals([])}

    rows = frappe.get_all(
        "Change Order",
        filters={"project": project, "docstatus": ["!=", 2]},
        fields=[
            "name", "co_number", "status", "cause", "cost_impact", "cost_impact_type",
            "schedule_impact_days", "transaction_date", "pm_approved", "customer_approved",
            "executed_on",
        ],
        order_by="co_number asc",
    )
    # Totals count only what is locked. A draft is a proposal, and a proposal in the contract
    # value is how a number that nobody agreed to ends up on a dashboard.
    locked = [r for r in rows if r.get("status") in (change_orders.STATUS_LOCKED, change_orders.STATUS_EXECUTED)]
    return {
        "change_orders": rows,
        "totals": change_orders.totals(locked),
        "draft_count": len(rows) - len(locked),
    }


def _state(doc):
    return {
        "name": doc.name,
        "status": doc.status,
        "pm_approved": doc.pm_approved,
        "pm_approved_by": doc.pm_approved_by,
        "pm_approved_on": doc.pm_approved_on,
        "customer_approved": doc.customer_approved,
        "customer_approved_on": doc.customer_approved_on,
        "blocking": change_orders.blocking_reasons(doc),
    }

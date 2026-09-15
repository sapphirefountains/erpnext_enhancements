# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Endpoints for project budgets and reallocations — WI-075 sub-phase M.

Four-space indented, like the rest of ``api/``. See ``api/README.md``.

Every approval here is stamped from the **server session and the server clock**, never from a
value the browser proposed. That is not caution for its own sake: ``process_steps.complete_step``
once let the client send the timestamp, and the audit found retroactive box-checking. An approval
whose time the approver chooses is not evidence of anything.

The second-approval endpoint is the one that carries the control. It refuses a session user who
is the requester or the project manager who already approved, because a protected-category rule
satisfiable by one person clicking twice is a control in appearance only. The refusal is a throw
rather than an advisory: unlike the inspector-qualification check in sub-phase H, which is
advisory because a hard gate would stop work being *recorded*, nothing is lost by refusing this
one — the document simply waits for the right signature.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext_enhancements.project_enhancements import budget_rollup
from erpnext_enhancements.quality import budgets

DOCTYPE = "Budget Reallocation"


def _draft(name):
    """Load a reallocation that is still editable, or refuse.

    Approvals are recorded before submission. Once submitted the money has moved and the
    approvals are part of the record; changing them afterwards would rewrite the evidence for a
    decision that has already taken effect.
    """
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw(
            _("{0} has already been submitted, so its approvals can no longer be changed.").format(
                name
            )
        )
    return doc


def _state(doc):
    """The shape every endpoint here returns, so the client has one thing to render."""
    return {
        "name": doc.name,
        "project": doc.project,
        "status": doc.status,
        "from_category": doc.from_category,
        "to_category": doc.to_category,
        "amount": doc.amount,
        "requires_additional_approval": bool(doc.requires_additional_approval),
        "requested_by": doc.requested_by,
        "pm_approved_by": doc.pm_approved_by,
        "pm_approved_on": doc.pm_approved_on,
        "additional_approved_by": doc.additional_approved_by,
        "additional_approved_on": doc.additional_approved_on,
        "blocking": budgets.approval_errors(
            doc.requested_by,
            doc.pm_approved_by,
            doc.additional_approved_by,
            bool(doc.requires_additional_approval),
        ),
    }


@frappe.whitelist()
def approve_reallocation(reallocation):
    """Record the project manager's approval, from the server session and clock."""
    doc = _draft(reallocation)
    doc.pm_approved_by = frappe.session.user
    doc.pm_approved_on = now_datetime()
    doc.save()
    frappe.db.commit()
    return _state(doc)


@frappe.whitelist()
def give_second_approval(reallocation):
    """Record the second approval a protected category requires.

    Refuses the requester and refuses the project manager who already approved. The check is
    repeated in ``before_submit`` rather than trusted from here, because an approval recorded by
    one path and validated only by another is a rule with a door left open.
    """
    doc = _draft(reallocation)

    if not doc.requires_additional_approval:
        frappe.throw(
            _("{0} does not touch a protected category, so it needs no second approval.").format(
                doc.name
            )
        )

    approver = frappe.session.user
    errors = budgets.approval_errors(doc.requested_by, doc.pm_approved_by, approver, True)
    if errors:
        frappe.throw("<br>".join(errors), title=_("This approval cannot be recorded"))

    doc.additional_approved_by = approver
    doc.additional_approved_on = now_datetime()
    doc.save()
    frappe.db.commit()
    return _state(doc)


@frappe.whitelist()
def revoke_approval(reallocation, which):
    """Undo an approval recorded in error. ``which`` is ``pm`` or ``second``."""
    doc = _draft(reallocation)

    if which == "pm":
        doc.pm_approved_by = None
        doc.pm_approved_on = None
    elif which == "second":
        doc.additional_approved_by = None
        doc.additional_approved_on = None
    else:
        frappe.throw(_("Unknown approval {0}.").format(which))

    doc.save()
    frappe.db.commit()
    return _state(doc)


@frappe.whitelist()
def get_project_budget(project):
    """A project's budget by category, with what is spent against each and what that is worth.

    ``unclassified`` is reported beside the categories rather than inside them. It is project
    purchase value that names no budget category, and today it is all of the project purchase
    value on the site — the field to name one with ships in this release, so every existing
    purchase-order line predates it. Folding it into a category would invent an attribution
    nobody made; leaving it out would under-report the job in the direction that looks clean.
    """
    doc = frappe.get_doc("Project", project)
    doc.check_permission("read")

    lines = []
    for row in doc.get(budgets.LINES_FIELD) or []:
        delta, percent = budgets.variance(row.budgeted_amount, row.actual_amount)
        lines.append(
            {
                "category": row.category,
                "budget_key": row.get("budget_key"),
                "budgeted_amount": row.budgeted_amount,
                "committed_amount": row.committed_amount,
                "actual_amount": row.actual_amount,
                "spend_coverage": row.spend_coverage,
                "spend_is_meaningful": budgets.spend_is_meaningful(row.spend_coverage),
                "variance": delta,
                # None, deliberately, when the line is budgeted at zero. A proportion of nothing
                # is unanswerable rather than complete, and rendering it as 0% or as a huge
                # number are both confident wrong answers.
                "variance_percent": percent,
                "is_protected": bool(
                    frappe.db.get_value("Project Budget Category", row.category, "is_protected")
                ),
                "notes": row.get("notes"),
            }
        )

    return {
        "project": project,
        "lines": lines,
        "total_budgeted": budgets.total_budgeted(doc.get(budgets.LINES_FIELD) or []),
        "estimated_costing": doc.estimated_costing,
        "unclassified": budget_rollup.unclassified_for_project(project),
        "duplicate_categories": budgets.duplicate_categories(
            doc.get(budgets.LINES_FIELD) or []
        ),
    }


@frappe.whitelist()
def get_project_reallocations(project):
    """Every reallocation raised on a project, newest first.

    The balances come back as stamped, not recomputed. They are what the two lines held on the
    day the money moved, which is what makes a sequence of these reconstructable.
    """
    frappe.get_doc("Project", project).check_permission("read")

    return frappe.get_all(
        DOCTYPE,
        filters={"project": project},
        fields=[
            "name",
            "transaction_date",
            "status",
            "from_category",
            "to_category",
            "amount",
            "reason",
            "requires_additional_approval",
            "requested_by",
            "pm_approved_by",
            "additional_approved_by",
            "from_balance_before",
            "from_balance_after",
            "to_balance_before",
            "to_balance_after",
        ],
        order_by="transaction_date desc, creation desc",
    )

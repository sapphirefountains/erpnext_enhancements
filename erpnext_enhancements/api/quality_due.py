# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What is due to be inspected, and what is in the way — WI-075 sub-phase I.

Read-only. The sweep in ``quality/scheduling.py`` emails a prompt once a week per milestone;
this is the list that is always there to look at, and it is recomputed from current state every
time rather than stored. Nothing here creates an inspection — generation stays a deliberate act,
the same way severity is never guessed.

It returns every milestone, not only the due ones
--------------------------------------------------

A list showing only what is due answers "what should I do now" and silently loses two questions
worth more: *why has that one not come round yet*, and *why is that one not showing at all*. So
every milestone comes back with its state and the reason for it — waiting, manual, skipped, done,
or unable to be worked out.

Two of those states are findings rather than statuses. **Blocked** means the milestone is due and
nobody has ever written down the checklist for it; only the Build commissioning list exists
today. **Unknown** means the project's build status cannot be placed on the scale, so due-ness is
not knowable — a renamed or blank Select value, which would otherwise make every sweep report
clean forever.

Indentation note: this file is 4-space, matching the rest of ``api/``. See ``api/README.md``.
"""

import frappe
from frappe import _

from erpnext_enhancements.quality import due, scheduling
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: Project fields the assessment needs. Named once so the endpoint and the sweep ask for the
#: same thing, and a field added to one cannot go missing from the other.
PROJECT_FIELDS = (
    "name",
    "project_name",
    "project_type",
    "custom_build_status",
    "custom_project_owner",
    "expected_start_date",
    "expected_end_date",
)


@frappe.whitelist()
def get_project_milestones(project):
    """Every inspection milestone for this project, with its state and the reason for it."""
    frappe.has_permission("Project", "read", doc=project, throw=True)

    row = frappe.db.get_value("Project", project, list(PROJECT_FIELDS), as_dict=True)
    if not row:
        frappe.throw(_("No such project: {0}").format(project))

    verdicts = scheduling.assess_project(row)
    if not verdicts:
        return {
            "enabled": is_enabled(),
            "project": project,
            "milestones": [],
            "note": _("No inspection milestones are configured for project type {0}.").format(
                row.get("project_type") or _("(none set)")
            ),
        }

    return {
        "enabled": is_enabled(),
        "project": project,
        "project_type": row.get("project_type"),
        "build_status": row.get("custom_build_status"),
        "counts": _counts(verdicts),
        "milestones": [
            {
                "milestone": milestone["name"],
                "milestone_key": milestone.get("milestone_key"),
                "title": milestone.get("milestone_title") or milestone["name"],
                "sequence": milestone.get("sequence"),
                "gate_kind": milestone.get("gate_kind"),
                "trigger_basis": milestone.get("trigger_basis"),
                "state": verdict["state"],
                "reason": verdict["reason"],
                "blocked": verdict["blocked"],
                "first_time": verdict["first_time"],
            }
            for milestone, verdict in verdicts
        ],
    }


def _counts(verdicts):
    """A one-line summary for a form indicator.

    ``blocked`` is counted separately from ``due`` rather than folded into it: "three are due"
    and "three are due and one of them has no checklist" are different sentences, and the second
    is the one that needs somebody to do something other than inspect.
    """
    states = [verdict["state"] for _m, verdict in verdicts]
    return {
        "due": states.count(due.STATE_DUE),
        "blocked": sum(1 for _m, v in verdicts if v["state"] == due.STATE_DUE and v["blocked"]),
        "unknown": states.count(due.STATE_UNKNOWN),
        "done": states.count(due.STATE_DONE),
        "total": len(states),
    }


@frappe.whitelist()
def get_my_due_inspections(limit=50):
    """Due milestones across the projects the signed-in user manages.

    Scoped to projects whose ``custom_project_owner`` resolves to this user, because the
    question a project manager has is "what is waiting on me", not "what exists". Anyone wanting
    the whole company's view has the Quality Control workspace.
    """
    employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
    if not employee:
        return {"enabled": is_enabled(), "projects": [], "note": _("You have no Employee record.")}

    rows = frappe.get_all(
        "Project",
        filters={
            "status": ["in", scheduling.ACTIVE_STATUSES],
            "custom_project_owner": employee,
        },
        fields=list(PROJECT_FIELDS),
        order_by="modified desc",
        limit=frappe.utils.cint(limit) or 50,
    )

    order = scheduling.build_status_order()
    out = []
    for row in rows:
        items = [
            (milestone, verdict)
            for milestone, verdict in scheduling.assess_project(row, status_order=order)
            if verdict["state"] in (due.STATE_DUE, due.STATE_UNKNOWN)
        ]
        if not items:
            continue
        out.append(
            {
                "project": row["name"],
                "project_name": row.get("project_name"),
                "milestones": [
                    {
                        "milestone": milestone["name"],
                        "title": milestone.get("milestone_title") or milestone["name"],
                        "state": verdict["state"],
                        "reason": verdict["reason"],
                        "blocked": verdict["blocked"],
                    }
                    for milestone, verdict in items
                ],
            }
        )
    return {"enabled": is_enabled(), "projects": out}

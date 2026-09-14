# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Generating an inspection from a milestone — WI-075 sub-phase D.

One entry point. It resolves the Active master template for the project's stage and milestone,
resolves the project's locked Scope of Work, merges the two into an ordered row list, hashes it,
and writes the result as a draft ``Project Quality Inspection``.

The merge and the hash are in :mod:`erpnext_enhancements.quality.merge`, which imports no
``frappe`` — this module is the part that talks to the database, and is deliberately thin so
that almost nothing of consequence happens outside the tested half.

Four things it refuses to do quietly
-------------------------------------

1. **Generate a second open inspection for the same milestone.** Two drafts of the same check
   means two answers and no way to tell which one counts.
2. **Generate from a Draft or Retired template.** Only Active is the standard of care.
3. **Generate an empty inspection.** A milestone whose template has no checks yet produces a
   clear refusal rather than a document with nothing in it, because an empty inspection that can
   be submitted reads as a clean pass.
4. **Drop a contracted criterion.** A criterion naming no milestone appears on no inspection at
   all. Those are counted, written onto the record's ``generation_note``, and shown to the
   caller. A promise that was sold and never inspected is the exact failure this programme
   exists to end, so it is never filtered away in silence.

Indentation note: this file is 4-space, matching the rest of ``api/``. See ``api/README.md``.
"""

import frappe
from frappe import _

from erpnext_enhancements.quality import carry_forward, merge


def _claim_open_fixes(project, inspection_name):
    """Claim this project's unverified fixes for the inspection being generated.

    The claim is a **stamp written in the same transaction** as the generation, not a query run
    later. Without it two inspections generated the same morning both carry the same item, both
    answer it, and the second one submitted silently overwrites the first one's verdict.

    Synchronous rather than enqueued for the same class of reason: a prod deploy `FLUSHDB`s the
    queue redis and destroys every pending job, so a claim living in a background job could
    vanish between generating an inspection and answering it.

    Returns the claimed action documents, oldest first.
    """
    live = set(
        frappe.get_all(
            "Project Quality Inspection",
            filters={"project": project, "docstatus": ["!=", 2]},
            pluck="name",
        )
    )
    candidates = frappe.get_all(
        "Quality Action",
        filters=carry_forward.claimable_filters(project),
        fields=[
            "name", "custom_subject", "custom_priority", "custom_reopen_count",
            "custom_punch_list", "custom_verifying_inspection", "date",
        ],
        order_by="creation asc",
    )
    claimed = [a for a in candidates if carry_forward.is_claimable(a, live)]
    for action in claimed:
        frappe.db.set_value(
            "Quality Action",
            action.name,
            {"custom_verifying_inspection": inspection_name, "custom_verification_result": None},
            update_modified=False,
        )
    return claimed


def _items_for_section(section):
    """Checks belonging to a reusable section, in their grid order.

    Read through ``get_all`` on the child table rather than loading the parent document: the
    section is only a source to copy from here, and loading it would run its controller for
    nothing.
    """
    return frappe.get_all(
        "Inspection Section Item",
        filters={"parent": section, "parenttype": "Inspection Section"},
        fields=[
            "item_key", "label", "acceptance_criteria", "check_type", "method",
            "uom", "min_value", "max_value", "options", "is_mandatory", "requires_photo",
        ],
        order_by="idx asc",
    )


def _active_template(project_type, milestone):
    names = frappe.get_all(
        "Project Inspection Template",
        filters={"milestone": milestone, "status": "Active"},
        pluck="name",
        limit=2,
    )
    if not names:
        frappe.throw(
            _("No Active inspection template for {0}. Create one, or activate the draft.").format(milestone),
            title=_("Nothing to inspect against"),
        )
    if len(names) > 1:
        # Ambiguity here would mean the checks depend on which row the query read first.
        frappe.throw(
            _("More than one Active template for {0}: {1}. Retire all but one.").format(
                milestone, ", ".join(names)
            ),
            title=_("Ambiguous template"),
        )
    return frappe.get_doc("Project Inspection Template", names[0])


def _locked_scope(project):
    """The project's locked Scope of Work, or None. Absence is allowed, not an error.

    An inspection against the company's standard of care alone is a real thing — most Service
    visits are exactly that — so a missing scope narrows what is checked rather than blocking
    the check.
    """
    names = frappe.get_all(
        "Project Scope of Work",
        filters={"project": project, "docstatus": 1},
        pluck="name",
        limit=1,
    )
    return frappe.get_doc("Project Scope of Work", names[0]) if names else None


@frappe.whitelist()
def generate_inspection(project, milestone):
    """Create a draft inspection for ``project`` at ``milestone``. Returns a summary dict.

    Permission is checked explicitly. A whitelisted function is reachable over HTTP by any
    logged-in user, and session permissions are not applied on the caller's behalf.
    """
    frappe.has_permission("Project Quality Inspection", "create", throw=True)
    if not frappe.db.exists("Project", project):
        frappe.throw(_("No such project: {0}").format(project))

    milestone_doc = frappe.get_doc("Inspection Milestone", milestone)
    if milestone_doc.disabled:
        frappe.throw(_("{0} is disabled.").format(milestone), title=_("Milestone disabled"))

    project_type = frappe.db.get_value("Project", project, "project_type")
    if project_type and milestone_doc.project_type != project_type:
        frappe.throw(
            _("{0} belongs to {1}, but this project is {2}.").format(
                milestone, milestone_doc.project_type, project_type
            ),
            title=_("Milestone is for a different project stage"),
        )

    existing = frappe.get_all(
        "Project Quality Inspection",
        filters={"project": project, "milestone": milestone, "docstatus": 0},
        pluck="name",
        limit=1,
    )
    if existing:
        frappe.throw(
            _("{0} already has an open inspection for this milestone: {1}.").format(project, existing[0]),
            title=_("Already open"),
        )

    template = _active_template(project_type, milestone)
    scope = _locked_scope(project)
    criteria = scope.acceptance_criteria if scope else []

    # Unverified fixes are resolved before the insert so an empty inspection is still refused
    # on the template's own emptiness, but they are CLAIMED after it, because a claim names the
    # inspection that will answer it and that name does not exist yet.
    pending = frappe.get_all(
        "Quality Action",
        filters=carry_forward.claimable_filters(project),
        fields=["name"],
        limit=1,
    )
    rows = merge.merge(
        merge.master_rows(template.sections, _items_for_section),
        merge.addendum_rows(criteria, scope.name if scope else "", milestone),
    )
    if not rows:
        frappe.throw(
            _("{0} produced no checks. An inspection with nothing in it would submit as a clean pass.").format(
                template.name
            ),
            title=_("Nothing to check"),
        )

    duplicates = merge.duplicate_source_keys(rows)
    if duplicates:
        frappe.throw(
            _("The merge produced duplicate rows ({0}). A result recorded against one of them would be ambiguous.").format(
                ", ".join(duplicates)
            ),
            title=_("Duplicated checks"),
        )

    unassigned = merge.unassigned_criteria(criteria)

    doc = frappe.new_doc("Project Quality Inspection")
    doc.update(
        {
            "project": project,
            "milestone": milestone,
            "inspector": frappe.session.user,
            "master_template": template.name,
            "master_template_revision": template.revision,
            "scope_of_work": scope.name if scope else None,
            "snapshot_hash": merge.spec_hash(rows),
            # Server clock and server session. The browser proposes neither.
            "frozen_on": frappe.utils.now_datetime(),
            "frozen_by": frappe.session.user,
            "generation_note": _generation_note(unassigned),
        }
    )
    for row in rows:
        doc.append("results", row)
    doc.insert(ignore_permissions=False)

    # Claim now that the inspection has a name, then append the carried rows and re-hash: the
    # snapshot has to cover what the inspection actually contains, carried fixes included.
    carried = _claim_open_fixes(project, doc.name) if pending else []
    if carried:
        carried_rows = merge.merge(rows, [], carry_forward.carried_rows(carried))[len(rows):]
        for row in carried_rows:
            doc.append("results", row)
        doc.snapshot_hash = merge.spec_hash(rows + carried_rows)
        doc.save(ignore_permissions=True)

    return {
        "name": doc.name,
        "checks": len(doc.results),
        "mandatory": merge.mandatory_count(doc.results),
        "carried_forward": [a.name for a in carried],
        "from_template": template.name,
        "from_scope": scope.name if scope else None,
        "unassigned_criteria": [key for key, _text in unassigned],
    }


def _generation_note(unassigned):
    """Say out loud what was contracted and is not being inspected."""
    parts = []
    if unassigned:
        listed = "; ".join(f"{text} ({key})" for key, text in unassigned)
        parts.append(
            f"{len(unassigned)} contracted criteria name no milestone and therefore appear on "
            f"NO inspection: {listed}. Set 'Inspect at' on each, then regenerate."
        )
    return "\n\n".join(parts)

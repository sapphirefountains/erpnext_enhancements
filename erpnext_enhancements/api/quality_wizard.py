# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Read and write side of the field inspection wizard — WI-075 sub-phase H.

The wizard is a Desk Page a person uses standing in front of a fountain, on a phone, often on a
bad signal. This module is everything it talks to: one bootstrap call, an autosaving patch, and
a finish.

Modelled directly on ``api/maintenance_visit.py``, which solved the same problem for maintenance
visits and has been in technicians' hands since June. Same three ideas — bootstrap once, patch a
field allowlist with optimistic locking, apply the finish server-side — because a second
half-different convention for the same job is how one of them ends up unmaintained.

The allowlist is the freeze's last line of defence
---------------------------------------------------

An ``Inspection Result`` row carries two kinds of field. The **answers** — outcome, measurement,
notes, photo — are what the inspection is for. The **frozen** ones — the check text, the
acceptance criteria, the bounds, whether it is mandatory, where it came from — were copied from
the master template at generation and are the standard being inspected against.

Only the first kind is writable here, and that is not a convenience: a wizard that could write
``min_value`` could turn a failing measurement into a passing one from a phone, on site, with no
trace. The freeze is enforced at generation, in the DocType (those fields are read-only) and
again here, because this endpoint is reachable over HTTP by anybody with a session.

Two refusals
-------------

**A submitted inspection is never patched.** Submit is what makes it evidence.

**A stale write is rejected, not merged.** If the document changed since the wizard loaded it —
somebody opened the desk form, or the same person has the wizard open on two devices — the save
is refused and the wizard reloads. Merging blind would silently overwrite an answer nobody knows
was lost.

Indentation note: this file is 4-space, matching the rest of ``api/``. See ``api/README.md``.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from erpnext_enhancements.quality import merge
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: Parent fields the wizard may write. Everything else in a patch is dropped silently — the
#: wizard never edits the project, the milestone, the template or any frozen field, so the
#: endpoint does not accept them either.
ALLOWED_PARENT_FIELDS = {"remarks", "inspection_date", "inspector_sign_off", "client_sign_off"}

#: Result-row fields the wizard may write: the answer and its evidence, nothing else. See the
#: module docstring for why this list is short on purpose.
ALLOWED_ROW_FIELDS = {"outcome", "measured_value", "notes", "photo"}

#: How many open inspections the landing list will show. A field list longer than this is a
#: planning problem, not a scrolling problem.
OPEN_LIMIT = 40


def _get_inspection(name):
    doc = frappe.get_doc("Project Quality Inspection", name)
    doc.check_permission("read")
    return doc


def _check_not_stale(doc, modified):
    """Optimistic lock: reject a write based on a version somebody else replaced."""
    if modified and str(doc.modified) != str(modified):
        frappe.throw(
            _("This inspection was changed elsewhere since you opened it. Reload to continue."),
            title=_("Inspection out of date"),
        )


def _state(doc):
    """The slice of server truth the wizard re-syncs after every write.

    Progress and the out-of-range flags are returned rather than computed in the browser,
    because the controller derives them in ``validate`` and two implementations of the same
    arithmetic is one more than can stay correct.
    """
    return {
        "name": doc.name,
        "modified": str(doc.modified),
        "docstatus": doc.docstatus,
        "status": doc.status,
        "mandatory_total": doc.mandatory_total,
        "mandatory_answered": doc.mandatory_answered,
        "fail_count": doc.fail_count,
        "completion_percent": doc.completion_percent,
        "rows": [
            {"name": row.name, "out_of_range": row.out_of_range, "outcome": row.outcome}
            for row in doc.get("results", [])
        ],
    }


@frappe.whitelist()
def get_inspection_bootstrap(inspection):
    """Everything the wizard needs to render, in one call.

    One call and not several because the first thing this runs on is a phone on site: three
    round-trips on a bad signal is three chances to half-load.
    """
    frappe.has_permission("Project Quality Inspection", "read", doc=inspection, throw=True)
    doc = _get_inspection(inspection)

    sections = []
    for row in doc.get("results", []):
        title = row.section_title or _("Checks")
        if not sections or sections[-1]["title"] != title:
            sections.append({"title": title, "location_note": row.location_note, "rows": []})
        sections[-1]["rows"].append(
            {
                "name": row.name,
                "idx": row.idx,
                "label": row.label,
                "acceptance_criteria": row.acceptance_criteria,
                "method": row.method,
                "check_type": row.check_type,
                "options": merge.allowed_answers(row),
                "outcome": row.outcome,
                "measured_value": row.measured_value,
                "uom": row.uom,
                "min_value": row.min_value,
                "max_value": row.max_value,
                "out_of_range": row.out_of_range,
                "is_mandatory": row.is_mandatory,
                "requires_photo": row.requires_photo,
                "notes": row.notes,
                "photo": row.photo,
                "source": row.source,
                "non_conformance": row.non_conformance,
            }
        )

    return {
        "enabled": is_enabled(),
        "header": {
            "name": doc.name,
            "project": doc.project,
            "project_name": frappe.db.get_value("Project", doc.project, "project_name")
            if doc.project
            else None,
            "customer": doc.customer,
            "milestone": doc.milestone,
            "gate_kind": doc.gate_kind,
            "inspector": doc.inspector,
            "inspector_name": frappe.utils.get_fullname(doc.inspector) if doc.inspector else None,
            "inspection_date": doc.inspection_date,
            "scheduled_date": doc.scheduled_date,
            "remarks": doc.remarks,
            "inspector_sign_off": doc.inspector_sign_off,
            "client_sign_off": doc.client_sign_off,
            "generation_note": doc.generation_note,
        },
        "instructions": _template_instructions(doc.master_template),
        "sections": sections,
        "state": _state(doc),
    }


def _template_instructions(template):
    """The template's safety and wrap-up guidance, as authored.

    Returned raw because these are Text Editor fields and the wizard renders them through its
    own sanitiser. Passing them through ``xss_sanitise`` here would escape the markup and the
    inspector would read literal ``<ul><li><b>`` tags — the mistake ``test_visit_wizard_markup``
    exists to stop, made once already on the maintenance side.
    """
    if not template:
        return {"safety": None, "wrapup": None}
    row = frappe.db.get_value(
        "Project Inspection Template",
        template,
        ["safety_instructions", "wrapup_instructions"],
        as_dict=True,
    )
    if not row:
        return {"safety": None, "wrapup": None}
    return {"safety": row.get("safety_instructions"), "wrapup": row.get("wrapup_instructions")}


@frappe.whitelist()
def save_inspection(inspection, patch, modified=None):
    """Apply a wizard step's changes and return the re-validated state.

    Args:
        inspection: ``Project Quality Inspection`` name.
        patch: JSON ``{"fields": {...parent...}, "rows": [{"name": row_name, ...changes}]}``.
            Fields outside the allowlists are dropped rather than refused — a wizard version
            that learned a new field before the server did should degrade, not fail a save on
            site.
        modified: the ``modified`` timestamp the client loaded. A mismatch is rejected.

    Returns:
        The state dict: fresh ``modified``, progress, per-row out-of-range flags as the
        controller recomputed them.
    """
    frappe.has_permission("Project Quality Inspection", "write", doc=inspection, throw=True)
    doc = _get_inspection(inspection)
    if doc.docstatus != 0:
        frappe.throw(_("This inspection is already submitted."))
    _check_not_stale(doc, modified)

    patch = frappe.parse_json(patch) or {}

    for field, value in (patch.get("fields") or {}).items():
        if field in ALLOWED_PARENT_FIELDS:
            doc.set(field, value)

    # No append path. Every row on a generated inspection was frozen at generation, and a wizard
    # that could add one could add a check nobody contracted for -- or quietly replace one it
    # could not answer.
    rows_by_name = {row.name: row for row in doc.get("results", [])}
    for row_patch in patch.get("rows") or []:
        row = rows_by_name.get(row_patch.get("name"))
        if row is None:
            frappe.throw(_("Unknown result row {0}.").format(row_patch.get("name")))
        for field, value in row_patch.items():
            if field in ALLOWED_ROW_FIELDS:
                row.set(field, value)

    doc.save()
    return _state(doc)


@frappe.whitelist()
def finish_inspection(inspection, modified=None):
    """Submit the inspection, which is what turns it into evidence.

    Deliberately thin. ``before_submit`` on the controller already refuses an inspection with
    unanswered mandatory checks, a failed check missing the photo its template required, or no
    inspection date — and ``on_submit`` raises the non-conformances and applies the carried-fix
    verdicts. None of that is re-implemented here; this endpoint exists so the wizard does not
    have to know the order.
    """
    frappe.has_permission("Project Quality Inspection", "submit", doc=inspection, throw=True)
    doc = _get_inspection(inspection)
    if doc.docstatus != 0:
        frappe.throw(_("This inspection is already submitted."))
    _check_not_stale(doc, modified)

    doc.submit()
    state = _state(doc)
    state["fail_count"] = doc.fail_count
    return state


def _due_key(row):
    """When an inspection is due: its scheduled date, else its inspection date, else when it
    was raised.

    This is the `coalesce` that used to live in the query. Doing it here is not a workaround
    for its own sake -- ordering by the three fields separately would be a different sort, and
    a wrong one: MariaDB puts NULLs first on an ascending sort, so an inspection with no
    scheduled date would jump ahead of one scheduled this morning.

    The database still applies `limit`, so an inspector holding more than OPEN_LIMIT drafts
    sees their oldest-raised ones rather than their soonest-due. That is the right subset to
    show somebody with a backlog that size, and OPEN_LIMIT is well above anything seen here.
    """
    value = row.get("scheduled_date") or row.get("inspection_date") or row.get("creation")
    if not value:
        # Sorts last. A row with no date at all is not "due first".
        return getdate("2999-12-31")
    # Normalised through `getdate` for one specific reason: `scheduled_date` and
    # `inspection_date` are Date fields and come back as `datetime.date`, while `creation` is a
    # Datetime and comes back as `datetime.datetime`. Python refuses to compare the two --
    # "'<' not supported between instances of 'datetime.datetime' and 'datetime.date'" -- so a
    # list mixing an inspection that has a scheduled date with one that does not would raise
    # inside `sort`, taking the field tool down a second time for a different reason.
    return getdate(value)


@frappe.whitelist()
def get_open_inspections():
    """Draft inspections assigned to the signed-in user, newest first.

    Scoped to ``inspector = session user`` rather than to everything readable: this is the
    landing screen of a field tool, and the question it answers is "what am I here to do", not
    "what exists". A supervisor opening somebody else's by link still works — permission is
    checked per document, not by this list.
    """
    rows = frappe.get_all(
        "Project Quality Inspection",
        filters={"inspector": frappe.session.user, "docstatus": 0},
        fields=[
            "name",
            "project",
            "milestone",
            "status",
            "scheduled_date",
            "inspection_date",
            "creation",
            "completion_percent",
            "mandatory_total",
            "mandatory_answered",
        ],
        # A plain field, because v16 will not take anything else. `frappe.database.query`
        # splits `order_by` on commas and validates each segment against a simple-field
        # pattern, so a SQL expression is rejected outright -- and the comma inside it makes
        # the error read "Invalid field format in Order By: coalesce(scheduled_date", which
        # names half a function and sends you looking for a field of that name. The real sort
        # is done below, where it can express what it actually means.
        order_by="creation asc",
        limit=OPEN_LIMIT,
    )
    rows.sort(key=_due_key)
    projects = {row["project"] for row in rows if row.get("project")}
    names = (
        dict(
            frappe.get_all(
                "Project",
                filters={"name": ["in", list(projects)]},
                fields=["name", "project_name"],
                as_list=True,
            )
        )
        if projects
        else {}
    )
    for row in rows:
        row["project_name"] = names.get(row.get("project"))
    return {"enabled": is_enabled(), "inspections": rows}

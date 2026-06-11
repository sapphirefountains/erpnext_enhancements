"""Backend for the Visit Wizard desk page (``/app/visit-wizard``).

Three whitelisted endpoints drive the technician's guided visit flow:
:func:`get_visit_bootstrap` (load + first-open template instantiation),
:func:`save_visit` (step autosave with optimistic locking and a field
allowlist) and :func:`finish_visit` (workflow-aware finish). Everything reads
and writes the ordinary **Sapphire Maintenance Record**, so the downstream
automation chain — Stock Entry, Timesheet, Warranty Claim, Sales Invoice,
next-visit scheduling — fires exactly as it does for the desk form.

All endpoints run with the session user's permissions (no
``ignore_permissions`` writes); the active "Sapphire Maintenance Workflow"
governs who may edit/advance which state, same as the desk form.
"""

import frappe
from frappe import _

from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
    get_dashboard_context,
    get_visit_payload,
)

# Wizard-writable fields. Anything else in a patch is silently dropped — the
# wizard never edits identity/scheduling fields, so the API doesn't accept
# them either.
ALLOWED_PARENT_FIELDS = {"visit_notes", "safety_acknowledged", "client_sign_off"}
ALLOWED_ROW_FIELDS = {
    "maintenance_results": {"selection", "answer", "other_details", "photo"},
    "chemistry_readings": {"reading_value", "notes", "photo"},
    "cleaning_tasks": {"is_done", "notes", "photo"},
    "consumables": {"item", "qty", "warehouse"},
}
# Tables that accept appended rows from the wizard (the "add other item" flow).
APPENDABLE_TABLES = {"consumables"}

PAYLOAD_TABLE_MAP = {
    "maintenance_results": "results",
    "chemistry_readings": "readings",
    "cleaning_tasks": "tasks",
    "consumables": "consumables",
}


def _get_record(record):
    doc = frappe.get_doc("Sapphire Maintenance Record", record)
    doc.check_permission("read")
    return doc


def _check_not_stale(doc, modified):
    """Optimistic lock: reject writes based on a version someone else replaced."""
    if modified and str(doc.modified) != str(modified):
        frappe.throw(
            _("This visit was changed elsewhere (e.g. on the desk form) since you loaded it. Reload to continue."),
            title=_("Visit Out of Date"),
        )


def _wizard_state(doc):
    """The slice of server truth the wizard re-syncs after every write."""
    return {
        "name": doc.name,
        "modified": str(doc.modified),
        "docstatus": doc.docstatus,
        "workflow_state": doc.get("workflow_state"),
        "completion_percent": doc.completion_percent,
        "has_out_of_range_readings": doc.has_out_of_range_readings,
        "readings": [
            {"name": row.name, "out_of_range": row.out_of_range}
            for row in doc.get("chemistry_readings", [])
        ],
    }


@frappe.whitelist()
def get_visit_bootstrap(record):
    """Everything the wizard needs to render a visit in one round trip.

    Scheduler-drafted records arrive as bare headers; when the caller can
    write and the section tables are empty, the resolved template is
    instantiated **and saved** here so every row has a real ``name`` for
    :func:`save_visit` to patch (the desk form instantiates client-side
    instead — both go through ``get_visit_payload``).

    Returns:
        dict: {"record": full doc dict, "dashboard": on-site briefing
        (safety, access codes, contract context, trends), "state": the
        :func:`_wizard_state` slice}.
    """
    doc = _get_record(record)

    tables_empty = not any(doc.get(table) for table in PAYLOAD_TABLE_MAP)
    if (
        doc.docstatus == 0
        and tables_empty
        and (doc.project or doc.maintenance_contract)
        and frappe.has_permission(doc.doctype, ptype="write", doc=doc)
    ):
        payload = get_visit_payload(
            project=doc.project,
            serial_no=doc.serial_no,
            maintenance_contract=doc.maintenance_contract,
            technician=doc.technician or frappe.session.user,
            visit_label=doc.visit_label,
        )
        appended = 0
        for table, payload_key in PAYLOAD_TABLE_MAP.items():
            for row in payload.get(payload_key) or []:
                doc.append(table, row)
                appended += 1
        if appended:
            if payload.get("template"):
                doc.template = payload["template"]
            # A tech opening the visit claims it (clock autofill + Today's
            # Visits key on technician); a supervisor peeking must not.
            if not doc.technician and "Maintenance User" in frappe.get_roles():
                doc.technician = frappe.session.user
            doc.save()

    return {
        "record": doc.as_dict(),
        "dashboard": get_dashboard_context(doc.project, doc.serial_no) if doc.project else {},
        "state": _wizard_state(doc),
    }


@frappe.whitelist()
def save_visit(record, patch, modified=None):
    """Apply a wizard step's changes and return the re-validated state.

    Args:
        record: Sapphire Maintenance Record name.
        patch: JSON ``{"fields": {...parent...}, "rows": {table: [{"name":
            row_name, ...changes}]}}``. Row entries without a ``name`` are
            appended (consumables only — the ad-hoc item flow). Fields outside
            the allowlists are dropped.
        modified: the ``modified`` timestamp the client loaded — mismatched
            saves are rejected instead of silently overwriting.

    Returns:
        dict: :func:`_wizard_state` — fresh ``modified``, completion percent,
        per-reading out-of-range flags (validate ran server-side), plus
        ``added`` row names for appended rows, in patch order.
    """
    doc = _get_record(record)
    if doc.docstatus != 0:
        frappe.throw(_("This visit is already submitted."))
    _check_not_stale(doc, modified)

    patch = frappe.parse_json(patch) or {}

    for field, value in (patch.get("fields") or {}).items():
        if field in ALLOWED_PARENT_FIELDS:
            doc.set(field, value)

    added = {}
    rows_by_name = {row.name: row for table in ALLOWED_ROW_FIELDS for row in doc.get(table, [])}
    for table, row_patches in (patch.get("rows") or {}).items():
        allowed = ALLOWED_ROW_FIELDS.get(table)
        if not allowed:
            continue
        for row_patch in row_patches or []:
            changes = {field: value for field, value in row_patch.items() if field in allowed}
            row_name = row_patch.get("name")
            if row_name:
                row = rows_by_name.get(row_name)
                if row is None or row.parentfield != table:
                    frappe.throw(_("Unknown row {0} in {1}.").format(row_name, table))
                row.update(changes)
            elif table in APPENDABLE_TABLES:
                new_row = doc.append(table, changes)
                added.setdefault(table, []).append(new_row)

    doc.save()

    state = _wizard_state(doc)
    state["added"] = {
        table: [row.name for row in rows] for table, rows in added.items()
    }
    return state


@frappe.whitelist()
def finish_visit(record, signature=None, modified=None):
    """Finish the visit: sign off and advance it through the house workflow.

    With the "Sapphire Maintenance Workflow" active this applies the first
    workflow action available to the session user from the record's current
    state (a technician's Draft moves to Pending Review; a reviewer's Pending
    Review is approved and submitted — automation fires on docstatus 1). On a
    site without an active workflow the record submits directly.

    ``before_submit`` enforcement (mandatory rows, clock-out autofill) runs
    inside whichever path changes docstatus.
    """
    doc = _get_record(record)
    if doc.docstatus != 0:
        frappe.throw(_("This visit is already submitted."))
    _check_not_stale(doc, modified)

    if signature:
        doc.client_sign_off = signature
        doc.save()

    from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name

    if get_workflow_name(doc.doctype):
        transitions = get_transitions(doc)
        if not transitions:
            frappe.throw(
                _("You don't have a workflow action available from the {0} state.").format(
                    doc.get("workflow_state") or _("current")
                )
            )
        doc = apply_workflow(doc, transitions[0]["action"])
    else:
        doc.submit()

    return _wizard_state(doc)

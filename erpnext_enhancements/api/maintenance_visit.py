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

# "Do Visit Today" creates a record carrying this visit_label. A labelled visit
# is skipped by api.maintenance_scheduling.update_next_visit_dates, so pulling a
# future visit forward is an EXTRA one-off — the feature's regular
# next_visit_date is untouched and its originally scheduled visit still fires.
EXTRA_VISIT_LABEL = "Extra Visit"

# How far ahead get_upcoming_visits looks, and where it starts (just past the
# 7-day scheduler horizon, whose due visits already appear as drafts in the
# kiosk's "Today's Visits").
UPCOMING_WINDOW_DAYS = 30
UPCOMING_WINDOW_START_DAYS = 8
UPCOMING_LIMIT = 50

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


@frappe.whitelist()
def get_upcoming_visits(days=UPCOMING_WINDOW_DAYS):
    """Future contract visits a tech can pull forward and do today.

    Looks past the 7-day scheduler horizon (today's/this-week's due visits are
    already drafted and shown in "Today's Visits") through ``days`` ahead, at
    Active contracts' covered features whose ``next_visit_date`` falls in that
    window and which have **no open draft yet**. Per Site Visit contracts
    collapse to a single earliest-due site entry. Sorted soonest-first, capped.

    Returns:
        list[dict]: [{contract, project, project_title, serial_no, item_name,
        visit_shape, next_visit_date, days_until}].
    """
    from frappe.utils import add_days, cint, date_diff, getdate, nowdate

    days = cint(days) or UPCOMING_WINDOW_DAYS
    today = getdate(nowdate())
    window_start = add_days(today, UPCOMING_WINDOW_START_DAYS)
    window_end = add_days(today, days)

    contracts = frappe.get_list(
        "Sapphire Maintenance Contract",
        filters={"status": "Active"},
        fields=["name", "project", "customer", "visit_shape"],
    )
    by_name = {c.name: c for c in contracts}
    if not by_name:
        return []

    features = frappe.get_all(
        "Sapphire Contract Feature",
        filters={
            "parent": ["in", list(by_name)],
            "parenttype": "Sapphire Maintenance Contract",
            "next_visit_date": ["between", [window_start, window_end]],
        },
        fields=["parent", "serial_no", "next_visit_date"],
        order_by="next_visit_date asc",
    )
    if not features:
        return []

    projects = {by_name[f.parent].project for f in features if by_name[f.parent].project}
    titles = dict(
        frappe.get_all("Project", filters={"name": ["in", list(projects)]},
                       fields=["name", "project_name"], as_list=True)
    ) if projects else {}
    serials = {f.serial_no for f in features if f.serial_no}
    item_names = dict(
        frappe.get_all("Serial No", filters={"name": ["in", list(serials)]},
                       fields=["name", "item_name"], as_list=True)
    ) if serials else {}

    entries = []
    site_seen = set()
    for feature in features:
        contract = by_name[feature.parent]
        if contract.visit_shape == "Per Site Visit":
            # one entry per site, at its earliest-due feature (features are
            # ordered, so the first one wins). Suppress when any open draft
            # already exists for the contract — the scheduler's regular site
            # draft, or an Extra Visit a tech just pulled forward — so a site
            # can't be queued twice.
            if contract.name in site_seen:
                continue
            site_seen.add(contract.name)
            if frappe.db.exists(
                "Sapphire Maintenance Record",
                {"maintenance_contract": contract.name, "docstatus": 0},
            ):
                continue
            serial_no = None
        else:
            if frappe.db.exists(
                "Sapphire Maintenance Record",
                {"project": contract.project, "serial_no": feature.serial_no, "docstatus": 0},
            ):
                continue
            serial_no = feature.serial_no

        entries.append({
            "contract": contract.name,
            "project": contract.project,
            "project_title": titles.get(contract.project) or contract.project,
            "serial_no": serial_no,
            "item_name": item_names.get(serial_no) if serial_no else None,
            "visit_shape": contract.visit_shape,
            "next_visit_date": str(feature.next_visit_date),
            "days_until": date_diff(feature.next_visit_date, today),
        })
        if len(entries) >= UPCOMING_LIMIT:
            break

    return entries


@frappe.whitelist()
def create_visit_today(contract, serial_no=None):
    """Create a draft visit record now for a feature scheduled later.

    The "Do Visit Today" action: an **extra one-off** visit (carries
    :data:`EXTRA_VISIT_LABEL`, so submitting it does not advance the feature's
    cadence — the regularly scheduled visit still happens later). Per Feature
    contracts pass the feature ``serial_no``; Per Site Visit contracts pass
    none (the record covers all features). Created with the caller's own
    permissions; the opening tech claims it as technician.

    Returns:
        str: the new Sapphire Maintenance Record name (the wizard opens it).
    """
    contract_doc = frappe.get_doc("Sapphire Maintenance Contract", contract)
    if contract_doc.status != "Active":
        frappe.throw(_("{0} is not an Active contract.").format(contract))

    if serial_no and serial_no not in [row.serial_no for row in contract_doc.covered_features]:
        frappe.throw(_("{0} is not a covered feature on {1}.").format(serial_no, contract))

    record = frappe.new_doc("Sapphire Maintenance Record")
    record.customer = contract_doc.customer
    record.project = contract_doc.project
    record.maintenance_contract = contract_doc.name
    record.serial_no = serial_no or None
    record.visit_label = EXTRA_VISIT_LABEL
    if "Maintenance User" in frappe.get_roles():
        record.technician = frappe.session.user
    record.insert()
    return record.name

"""Dispatch & technician assignment for maintenance visits.

The daily scheduler drafts bare visit records
(``tasks._draft_maintenance_record``). This module turns those into a real
dispatched schedule:

* :func:`resolve_scheduled_date` — a visit's Scheduled Visit Date = the feature's
  due date shifted forward to the nearest **Preferred Visit Day** on the signed
  agreement (best-effort parse of the free-text days field).
* :func:`default_technician_for` / :func:`assign_to_technician` — a site's
  Maintenance Profile carries a **Default Technician**; drafted visits are
  stamped with them and a silent Frappe assignment (ToDo + share, no
  notification). The morning digest below is the active notification channel.
* :func:`send_morning_digests` — a daily early-morning job that texts (Triton)
  and emails each technician their visits for the day, ordered by a
  nearest-neighbour route from the site coordinates. Gated by "Morning
  Technician Dispatch Digest" in ERPNext Enhancements Settings (off by default).
"""

import math
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, getdate, nowdate

# Free-text preferred-days parsing (e.g. "Mon & Wed", "Tuesday/Thursday").
WEEKDAY_TOKENS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _preferred_weekdays(text):
    if not text:
        return set()
    return {WEEKDAY_TOKENS[tok] for tok in re.split(r"[^a-z]+", str(text).lower()) if tok in WEEKDAY_TOKENS}


def resolve_scheduled_date(due_date, project_contract_name):
    """The due date shifted forward (<=7 days) to the nearest Preferred Visit Day.

    Returns (a date >= today) when there is no linked agreement or no parseable
    preferred days; None only when there is no due date. Overdue features are
    clamped forward to today first, so the result is never in the past — a past
    scheduled date would fall outside the digest's "== today" window and never
    be surfaced.
    """
    if not due_date:
        return None
    # Never schedule into the past: an overdue feature books onto today (or the
    # next preferred day at/after today).
    due = max(getdate(due_date), getdate(nowdate()))
    if not project_contract_name:
        return due
    preferred = _preferred_weekdays(
        frappe.db.get_value("Project Contract", project_contract_name, "preferred_days")
    )
    if not preferred:
        return due
    for offset in range(7):
        candidate = getdate(add_days(due, offset))
        if candidate.weekday() in preferred:
            return candidate
    return due


def default_technician_for(project):
    """The site's owning technician from its Maintenance Profile, or None.

    A dangling link (the User was removed/renamed) or a disabled User is treated
    as "no default technician": stamping one would make ``record.insert`` raise
    ``LinkValidationError`` and abort the whole nightly generation run, and
    dispatching to a departed user is pointless.
    """
    if not project:
        return None
    tech = frappe.db.get_value("Sapphire Maintenance Profile", {"project": project}, "default_technician")
    if tech and not frappe.db.get_value("User", tech, "enabled"):
        return None  # missing (None) or disabled (0)
    return tech


def assign_to_technician(record_name, user):
    """Create a silent Frappe assignment (ToDo + share) for a drafted visit.

    No ``notify`` — an assignment Notification/email per drafted visit would be
    daily noise; the (gated) morning digest is the notification channel instead.
    Best-effort — a duplicate assignment or any failure is logged, never raised
    into the scheduler.
    """
    if not user:
        return
    try:
        from frappe.desk.form.assign_to import add as _assign_add

        _assign_add(
            {
                "assign_to": [user],
                "doctype": "Sapphire Maintenance Record",
                "name": record_name,
                "description": _("Scheduled maintenance visit."),
            }
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Maintenance dispatch assignment failed")


# ---------------------------------------------------------------------------
# Morning digest
# ---------------------------------------------------------------------------


def send_morning_digests():
    """Daily (early AM): text + email each technician their visits for today.

    Gated by "Morning Technician Dispatch Digest" in ERPNext Enhancements
    Settings. Each technician is handled in isolation so one failure does not
    abort the rest. ``dispatch_digest_sent`` is stamped before sending so a
    second run the same day (scheduler catch-up, manual test) no-ops — the SMS
    is billed, so at-most-once matters (same convention as maintenance_nudge_sent).
    """
    if not cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "maintenance_dispatch_digests")):
        return
    today = getdate(nowdate())

    by_tech = {}
    for visit in frappe.get_all(
        "Sapphire Maintenance Record",
        filters={
            "scheduled_visit_date": today,
            "docstatus": 0,
            "technician": ["is", "set"],
            "dispatch_digest_sent": 0,
        },
        fields=["name", "technician", "project", "customer", "serial_no", "visit_label"],
    ):
        by_tech.setdefault(visit.technician, []).append(visit)

    for technician, visits in by_tech.items():
        # Stamp before sending: at-most-once even if this window runs twice.
        for visit in visits:
            frappe.db.set_value("Sapphire Maintenance Record", visit.name, "dispatch_digest_sent", 1)
        try:
            _send_tech_digest(technician, _order_by_route(visits), today)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Dispatch digest failed: {technician}")


def _send_tech_digest(technician, visits, today):
    stops = []
    for visit in visits:
        project_name = frappe.db.get_value("Project", visit.project, "project_name") or visit.project
        what = visit.serial_no or visit.visit_label or _("site visit")
        stops.append({"customer": visit.customer or "", "project": project_name, "what": what})

    count = len(stops)
    when = formatdate(today)

    cell_number = frappe.db.get_value("Employee", {"user_id": technician}, "cell_number")
    if cell_number:
        text_lines = [f"{i}. {s['customer']} — {s['project']} ({s['what']})" for i, s in enumerate(stops, 1)]
        shown = text_lines[:10]
        if count > 10:
            shown.append(_("…and {0} more — see email").format(count - 10))
        message = f"Sapphire Fountains — {count} maintenance visit(s) today ({when}):\n" + "\n".join(shown)
        try:
            from erpnext_enhancements.api.telephony import send_system_sms

            send_system_sms(cell_number, message)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Dispatch digest SMS failed: {technician}")

    email = frappe.db.get_value("User", technician, "email") or technician
    if email:
        items = "".join(
            f"<li>{frappe.utils.escape_html(s['customer'])} — "
            f"{frappe.utils.escape_html(s['project'])} ({frappe.utils.escape_html(str(s['what']))})</li>"
            for s in stops
        )
        html = _("<p>Your maintenance visits for {0}, in route order:</p>").format(when) + f"<ol>{items}</ol>"
        try:
            frappe.sendmail(
                recipients=[email],
                subject=_("Your maintenance route — {0} ({1} visit(s))").format(when, count),
                message=html,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Dispatch digest email failed: {technician}")


def _order_by_route(visits):
    """Order a technician's visits by a greedy nearest-neighbour route from the
    site coordinates on each project's Maintenance Profile. Visits without
    coordinates keep their original order at the end."""
    for visit in visits:
        coord = frappe.db.get_value(
            "Sapphire Maintenance Profile", {"project": visit.project}, ["latitude", "longitude"], as_dict=True
        )
        visit["lat"] = flt(coord.latitude) if coord else 0
        visit["lng"] = flt(coord.longitude) if coord else 0

    located = [v for v in visits if v["lat"] and v["lng"]]
    unlocated = [v for v in visits if not (v["lat"] and v["lng"])]
    if len(located) <= 1:
        return visits

    ordered = [located.pop(0)]
    while located:
        last = ordered[-1]
        nxt = min(located, key=lambda v: _haversine(last["lat"], last["lng"], v["lat"], v["lng"]))
        located.remove(nxt)
        ordered.append(nxt)
    return ordered + unlocated


def _haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (only the ordering matters here)."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))

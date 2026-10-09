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
* :func:`default_crew_for` / :func:`default_crews_for` — the profile's site
  default for multi-person visits (P1.8): ``default_crew``, ``visit_hours``,
  ``visit_full_day``. Copied onto drafted visits and used to project future ones.
* :func:`send_morning_digests` — a daily early-morning job that texts (Triton)
  and emails each technician and crew member their visits for the day, ordered by a
  nearest-neighbour route from the site coordinates. Gated by "Morning
  Technician Dispatch Digest" in ERPNext Enhancements Settings (off by default).
"""

import math
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, getdate, nowdate

from erpnext_enhancements import email_style

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


def default_crews_for(projects):
    """Each site's default crew, length and full-day flag from its Maintenance Profile.

    ``{project: {"crew": [user, ...], "rows": [{"user", "hours"}], "hours": float | None,
    "full_day": bool}}`` for every project asked about that has a profile. ``crew`` is the
    enabled users of ``default_crew`` in row order, never the site's default technician (they go
    on every visit already); ``rows`` is the same people with their own hours (None when blank,
    i.e. the visit's length). ``hours`` is ``visit_hours`` when set, else None (Project Planner
    Settings' maintenance visit hours apply).

    Three queries however many projects. Never raises: drafting and the planners both call it,
    and neither may fail over a crew. A failure is logged and answers {}.
    """
    projects = sorted({p for p in projects or [] if p})
    if not projects:
        return {}
    try:
        profiles = frappe.get_all(
            "Sapphire Maintenance Profile",
            filters={"project": ["in", projects]},
            fields=["name", "project", "default_technician", "visit_hours", "visit_full_day"],
        )
        if not profiles:
            return {}
        rows = frappe.get_all(
            "Sapphire Visit Crew Member",
            filters={
                "parenttype": "Sapphire Maintenance Profile",
                "parent": ["in", [p.name for p in profiles]],
            },
            fields=["parent", "user", "hours", "idx"],
            order_by="parent asc, idx asc",
        )
        users = sorted({r.user for r in rows if r.user})
        enabled = (
            set(frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name"))
            if users
            else set()
        )
        by_parent = {}
        for row in sorted(rows, key=lambda r: (r.parent or "", cint(r.idx))):
            by_parent.setdefault(row.parent, []).append(row)

        out = {}
        for profile in profiles:
            crew_rows = []
            for row in by_parent.get(profile.name, []):
                user = row.user
                if (
                    not user
                    or user not in enabled
                    or user == profile.default_technician
                    or user in [r["user"] for r in crew_rows]
                ):
                    continue
                hours = flt(row.hours)
                crew_rows.append({"user": user, "hours": round(hours, 2) if hours > 0 else None})
            visit_hours = flt(profile.visit_hours)
            out[profile.project] = {
                "crew": [r["user"] for r in crew_rows],
                "rows": crew_rows,
                "hours": round(visit_hours, 2) if visit_hours > 0 else None,
                "full_day": bool(cint(profile.visit_full_day)),
            }
        return out
    except Exception:
        frappe.log_error(title="Maintenance default crew lookup failed", message=frappe.get_traceback())
        return {}


def default_crew_for(project):
    """One site's default crew: ``{"crew": [user...], "rows", "hours": float|None, "full_day": bool}``.

    See :func:`default_crews_for`. A project with no profile (or a failed lookup) answers an
    empty crew, no hours and not full day.
    """
    return default_crews_for([project]).get(project) or {
        "crew": [],
        "rows": [],
        "hours": None,
        "full_day": False,
    }


def has_open_assignment(record_name, user):
    """True when ``user`` already holds an open ToDo on the visit."""
    return bool(
        frappe.db.exists(
            "ToDo",
            {
                "reference_type": "Sapphire Maintenance Record",
                "reference_name": record_name,
                "allocated_to": user,
                "status": "Open",
            },
        )
    )


def assign_to_technician(record_name, user, description=None):
    """Create a silent Frappe assignment (ToDo + share) for a drafted visit.

    No ``notify`` — an assignment Notification/email per drafted visit would be
    daily noise; the (gated) morning digest is the notification channel instead.
    Used for the technician and, since P1.8, each crew member. Someone who
    already holds an open ToDo on the visit is skipped: Frappe's ``add`` reports
    a duplicate through ``msgprint``, which would surface as a warning on the
    Maintenance Planner, and the crew mirror (``visit_crew.on_record_update``)
    has usually assigned a drafted visit's crew already during its insert.
    Best-effort — any failure is logged, never raised into the scheduler.
    """
    if not user:
        return
    try:
        if has_open_assignment(record_name, user):
            return
        from frappe.desk.form.assign_to import add as _assign_add

        _assign_add(
            {
                "assign_to": [user],
                "doctype": "Sapphire Maintenance Record",
                "name": record_name,
                "description": description or _("Scheduled maintenance visit."),
            }
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Maintenance dispatch assignment failed")


# ---------------------------------------------------------------------------
# Morning digest
# ---------------------------------------------------------------------------


def send_morning_digests():
    """Daily (early AM): text + email each person their visits for today.

    Gated by "Morning Technician Dispatch Digest" in ERPNext Enhancements
    Settings. A visit goes to its technician **and every crew member** (P1.8):
    one digest per person, listing all of that person's visits, a helper's line
    saying who they are with. Each person is handled in isolation so one
    failure does not abort the rest.

    **At most once per person per day.** The SMS is billed, so a second run the
    same day (scheduler catch-up, a manual test) must text nobody twice. Each
    send is stamped with today's date *before* it is attempted, on a persisted
    column per (visit, person):

    * the technician's on the record — ``dispatch_digest_sent_on``, as before;
    * a crew member's on their own crew row — ``Sapphire Visit Crew Member.
      digest_sent_on``.

    Persisted rather than a redis key on purpose: the production deploy
    ``FLUSHDB``s redis, so a cache marker would vanish with every merge and a
    re-run after a deploy would re-text everyone. A row on the visit also moves
    with the visit: someone added to the crew after 6 AM has no stamp and is
    included by a later run, and a person who stays on an edited crew keeps
    their row and their stamp (``visit_crew.set_crew``; a Visit Wizard claim
    swaps the technician's and the claimer's stamps along with their places).
    A *date* (not a boolean) is used deliberately: the draft persists and its
    ``scheduled_visit_date`` is mutable, so a visit rescheduled to a later day
    correctly re-enters that day's digest (its stamps are < that day).
    """
    if not cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "maintenance_dispatch_digests")):
        return
    today = getdate(nowdate())

    visits = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={"scheduled_visit_date": today, "docstatus": 0},
        fields=[
            "name", "technician", "project", "customer", "serial_no", "visit_label",
            "dispatch_digest_sent_on",
        ],
    )
    if not visits:
        return
    crew_rows = frappe.get_all(
        "Sapphire Visit Crew Member",
        filters={"parenttype": "Sapphire Maintenance Record", "parent": ["in", [v.name for v in visits]]},
        fields=["name", "parent", "user", "digest_sent_on", "idx"],
        order_by="parent asc, idx asc",
    )
    users = {v.technician for v in visits if v.technician} | {r.user for r in crew_rows if r.user}
    people = {
        u.name: u
        for u in frappe.get_all(
            "User", filters={"name": ["in", sorted(users)]}, fields=["name", "full_name", "enabled"]
        )
    } if users else {}

    def pending(stamp):
        # Not already digested today: never stamped, or stamped on a prior day.
        return not stamp or getdate(stamp) < today

    def name_of(user):
        person = people.get(user)
        return (person.full_name if person else None) or user

    crew_by_visit = {}
    for row in crew_rows:
        crew_by_visit.setdefault(row.parent, []).append(row)

    by_person = {}  # user -> [(visit dict, stamp)]
    for visit in visits:
        lead = visit.technician
        helpers = []
        for row in crew_by_visit.get(visit.name, []):
            if row.user and row.user != lead and row.user not in [h.user for h in helpers]:
                helpers.append(row)
        if lead and pending(visit.dispatch_digest_sent_on):
            names = ", ".join(name_of(h.user) for h in helpers)
            entry = dict(visit, role="lead", with_text=_("with {0}").format(names) if names else "")
            by_person.setdefault(lead, []).append((entry, ("lead", visit.name)))
        for row in helpers:
            person = people.get(row.user)
            if (person and not cint(person.enabled)) or not pending(row.digest_sent_on):
                continue  # a departed helper is not texted; nor is one already texted today
            entry = dict(
                visit,
                role="crew",
                with_text=_("with {0}").format(name_of(lead)) if lead else _("crew"),
            )
            by_person.setdefault(row.user, []).append((entry, ("crew", row.name, visit.name)))

    covered = _combined_digest_covers()
    for person, entries in by_person.items():
        if person in covered:
            # The Project Planner's combined 6 AM digest tells this person their whole day,
            # visits included. Not stamped, so switching the combined digest off later the same
            # morning and re-running this sends them their visits after all.
            continue
        # Stamp before sending: at-most-once/day even if this window runs twice.
        for _entry, stamp in entries:
            _stamp_digest(stamp, today)
        try:
            _send_tech_digest(
                person, _order_by_route([frappe._dict(entry) for entry, _stamp in entries]), today
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Dispatch digest failed: {person}")


def _combined_digest_covers():
    """Users the Project Planner's combined morning digest covers (empty while it is off).

    Project Planner Settings ``combined_morning_digest`` (Phase 3B): while on, one message per
    person replaces this digest for everyone on the planner, so nobody gets three texts. Any
    failure answers the empty set, which leaves this digest exactly as it was.
    """
    try:
        from erpnext_enhancements.project_enhancements.planner_digest import covered_users

        return covered_users()
    except Exception:
        return set()


def _stamp_digest(stamp, today):
    """Record that a person's digest for one visit is being sent today (before sending it).

    A crew row's stamp is a child-row write, which leaves the record's ``modified`` alone; the
    record is touched too, so a desk form loaded before the run cannot save its stale copy of the
    row (and with it a blank stamp) back over this one. The technician's stamp, a write on the
    record itself, bumps ``modified`` already.
    """
    if stamp[0] == "lead":
        frappe.db.set_value("Sapphire Maintenance Record", stamp[1], "dispatch_digest_sent_on", today)
        return
    _kind, row_name, record = stamp
    frappe.db.set_value("Sapphire Visit Crew Member", row_name, "digest_sent_on", today)
    from frappe.utils import now_datetime

    frappe.db.set_value(
        "Sapphire Maintenance Record", record, "modified", now_datetime(), update_modified=False
    )


def _send_tech_digest(technician, visits, today):
    stops = []
    for visit in visits:
        project_name = frappe.db.get_value("Project", visit.project, "project_name") or visit.project
        what = visit.serial_no or visit.visit_label or _("site visit")
        stops.append(
            {
                "customer": visit.customer or "",
                "project": project_name,
                "what": what,
                # A helper's line says who they are with ("with Austin Healey", or "crew" when the
                # visit has no technician yet); the technician's names their crew, if any.
                "with": visit.get("with_text") or "",
            }
        )

    count = len(stops)
    when = formatdate(today)

    cell_number = frappe.db.get_value("Employee", {"user_id": technician}, "cell_number")
    if cell_number:
        text_lines = [
            f"{i}. {s['customer']} — {s['project']} ({s['what']})" + (f" — {s['with']}" if s["with"] else "")
            for i, s in enumerate(stops, 1)
        ]
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
        # A numbered column rather than an <ol>: route order is the whole point
        # of this email, and a table keeps it legible on a phone where a long
        # customer name would otherwise wrap away from its number.
        headers = ["#", _("Customer"), _("Project"), _("What")]
        rows = [[str(i), s["customer"], s["project"], str(s["what"])] for i, s in enumerate(stops, 1)]
        if any(s["with"] for s in stops):
            headers.append(_("Crew"))
            for row, stop in zip(rows, stops):
                row.append(stop["with"])
        html = email_style.p(_("Your maintenance visits for {0}, in route order:").format(when)) + email_style.table(
            headers, rows
        )
        try:
            frappe.sendmail(
                recipients=[email],
                subject=_("Your maintenance route — {0} ({1} visit(s))").format(when, count),
                message=email_style.wrap(
                    html,
                    title=_("Your maintenance route"),
                    eyebrow=_("Maintenance") + " · " + str(when),
                    pillar="service",
                ),
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

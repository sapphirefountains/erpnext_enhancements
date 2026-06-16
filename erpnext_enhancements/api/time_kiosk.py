"""Time-tracking kiosk + geolocation backend.

Whitelisted API powering the standalone Time Kiosk PWA (``public/js/kiosk/app.js``
and the offline service worker ``www/kiosk-sw.js``), plus the manager
"Location Timeline" Desk page
(``enhancements_core/page/location_timeline/location_timeline.js``). The page
context ``www/kiosk.py`` calls ``get_kiosk_bootstrap``.

Core flow: ``log_time`` opens/pauses/resumes/switches/stops "Job Interval"
documents; on completion an interval is synced into a Draft Timesheet
(``sync_interval_to_timesheet``). Geolocation points are streamed in batches
into "Time Kiosk Log" and visualised per interval.

Security:
        - The employee is derived from the SESSION user, never trusted from the
          client, for ``log_time``/``log_geolocation_batch``/``get_kiosk_bootstrap``
          (``_resolve_employee`` rejects a mismatched claimed employee).
        - The legacy ``log_geolocation`` single-point endpoint trusts the
          supplied ``employee`` for back-compat.
        - ``get_location_history`` is role-gated: only ``TIMELINE_MANAGER_ROLES``
          (System Manager / HR Manager) may view another employee's history;
          everyone else sees only their own.
        - Writes use ``ignore_permissions=True`` after the session-based checks.

Scheduler: ``purge_old_location_logs`` runs daily (hooks.py) to enforce the
configured retention window. Settings come from the "Time Kiosk Settings"
Single DocType.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime, flt, cint, add_days
from datetime import datetime, timedelta

from erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings import (
    get_settings,
)

# Roles allowed to view *anyone's* location history. Everyone else can only view
# their own. Kept here (rather than in Settings) so it can't be widened from the UI.
TIMELINE_MANAGER_ROLES = {"System Manager", "HR Manager"}

@frappe.whitelist()
def log_time(project=None, action=None, lat=None, lng=None, description=None, task=None, time_category=None):
    """
    Logs time for the current employee.
    action: "Start", "Stop", "Pause", "Resume", "Switch"
    """
    if not action:
        frappe.throw(_("Action is required."))

    user = frappe.session.user
    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")

    if not employee:
        frappe.throw(_("No Employee record found for this user ({0}).").format(user), frappe.PermissionError)

    now_dt = now_datetime()

    if action == "Start":
        if not project:
            frappe.throw(_("Project is required to start work."))

        existing = frappe.db.exists("Job Interval", {
            "employee": employee,
            "status": ["in", ["Open", "Paused"]]
        })
        if existing:
            frappe.throw(_("You already have an active job interval. Please stop or switch it first."))

        doc = frappe.get_doc({
            "doctype": "Job Interval",
            "employee": employee,
            "project": project,
            "task": task,
            "time_category": time_category,
            "start_time": now_dt,
            "status": "Open",
            "latitude": lat,
            "longitude": lng,
            "description": description,
            "total_paused_seconds": 0.0
        })
        doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Work started.", "doc": doc.name}

    elif action == "Pause":
        open_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": "Open"}, "name")
        if not open_interval:
            frappe.throw(_("No open job found to pause."))
        
        doc = frappe.get_doc("Job Interval", open_interval)
        doc.status = "Paused"
        doc.last_pause_time = now_dt
        doc.save(ignore_permissions=True)
        return {"status": "success", "message": "Work paused.", "doc": doc.name}

    elif action == "Resume":
        paused_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": "Paused"}, "name")
        if not paused_interval:
            frappe.throw(_("No paused job found to resume."))
        
        doc = frappe.get_doc("Job Interval", paused_interval)
        if doc.last_pause_time:
            pause_duration = (now_dt - get_datetime(doc.last_pause_time)).total_seconds()
            doc.total_paused_seconds = flt(doc.total_paused_seconds) + pause_duration
            
        doc.status = "Open"
        doc.last_pause_time = None
        doc.save(ignore_permissions=True)
        return {"status": "success", "message": "Work resumed.", "doc": doc.name}

    elif action == "Switch":
        if not project:
            frappe.throw(_("Project is required to switch work."))

        active_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": ["in", ["Open", "Paused"]]}, ["name", "status", "last_pause_time"], as_dict=True)
        
        if active_interval:
            doc = frappe.get_doc("Job Interval", active_interval.name)
            if doc.status == "Paused" and doc.last_pause_time:
                doc.end_time = doc.last_pause_time
            else:
                doc.end_time = now_dt
            doc.status = "Completed"
            doc.save(ignore_permissions=True)
            sync_interval_to_timesheet(doc)

        new_doc = frappe.get_doc({
            "doctype": "Job Interval",
            "employee": employee,
            "project": project,
            "task": task,
            "time_category": time_category,
            "start_time": now_dt,
            "status": "Open",
            "latitude": lat,
            "longitude": lng,
            "description": description,
            "total_paused_seconds": 0.0
        })
        new_doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Task switched.", "doc": new_doc.name}

    elif action == "Stop":
        active_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": ["in", ["Open", "Paused"]]}, ["name", "status", "last_pause_time"], as_dict=True)

        if not active_interval:
            frappe.throw(_("No active job found to stop."))

        doc = frappe.get_doc("Job Interval", active_interval.name)
        if doc.status == "Paused" and doc.last_pause_time:
            doc.end_time = doc.last_pause_time
        else:
            doc.end_time = now_dt
        doc.status = "Completed"
        doc.save(ignore_permissions=True)

        sync_interval_to_timesheet(doc)

        return {"status": "success", "message": "Work stopped.", "doc": doc.name}

    else:
        frappe.throw(_("Invalid action. Must be 'Start', 'Stop', 'Pause', 'Resume', or 'Switch'."))

def sync_interval_to_timesheet(interval_doc):
    """
    Syncs a completed Job Interval to a Timesheet.
    """
    try:
        employee = interval_doc.employee
        project = interval_doc.project

        if not project:
            interval_doc.db_set("sync_status", "Skipped - No Project")
            return

        start_time = get_datetime(interval_doc.start_time)
        end_time = get_datetime(interval_doc.end_time)

        duration_seconds = (end_time - start_time).total_seconds() - flt(interval_doc.total_paused_seconds)
        hours = max(duration_seconds / 3600.0, 0.0)

        date_key = start_time.date()

        # Find existing Draft Timesheet for this employee and date
        filters = {
            "employee": employee,
            "status": "Draft",
            "start_date": ["<=", date_key],
            "end_date": [">=", date_key]
        }

        timesheet_name = frappe.db.get_value("Timesheet", filters, "name")

        new_log = {
            "project": project,
            "task": interval_doc.task,
            "hours": hours,
            "activity_type": interval_doc.time_category or None,
            "from_time": start_time,
            "to_time": end_time,
            "description": interval_doc.description or "Synced from Job Interval"
        }

        if timesheet_name:
            # Optimized idempotency check using database lookup
            exists = frappe.db.exists("Timesheet Detail", {
                "parent": timesheet_name,
                "project": project,
                "from_time": start_time,
                "hours": ["between", [hours - 0.001, hours + 0.001]]
            })

            if not exists:
                ts_doc = frappe.get_doc("Timesheet", timesheet_name)
                ts_doc.append("time_logs", new_log)
                ts_doc.save(ignore_permissions=True)
                # Ensure ts_doc object is available for subsequent note update
            else:
                # If it already exists, we still need the name for update_timesheet_note
                ts_doc = frappe._dict({"name": timesheet_name})

        else:
            # Create new Timesheet
            ts_doc = frappe.get_doc({
                "doctype": "Timesheet",
                "employee": employee,
                "start_date": date_key,
                "end_date": date_key,
                "time_logs": [new_log]
            })
            ts_doc.insert(ignore_permissions=True)

        # Update Timesheet Note (Aggregate all notes for the day)
        if ts_doc.name:
            update_timesheet_note(ts_doc.name, employee, start_time)

        # Update Job Interval sync status
        interval_doc.db_set("sync_status", "Synced")

    except Exception as e:
        frappe.log_error(f"Failed to sync Job Interval {interval_doc.name} to Timesheet: {str(e)}", "Time Kiosk Sync Error")
        # Update Job Interval sync status to Failed
        interval_doc.db_set("sync_status", "Failed")


def update_timesheet_note(timesheet_name, employee, date_obj):
    """
    Aggregates all Job Interval notes for the given employee and date,
    then updates the Timesheet's 'note' field.
    """
    try:
        if hasattr(date_obj, 'date'):
             date_val = date_obj.date()
        else:
             # If it's already a date object or string, handle accordingly
             # For robustness, we assume date_obj is datetime or date
             date_val = get_datetime(date_obj).date()

        # Define day range
        start_of_day = date_val.strftime("%Y-%m-%d 00:00:00")
        end_of_day = date_val.strftime("%Y-%m-%d 23:59:59.999999")

        intervals = frappe.get_all("Job Interval",
            filters={
                "employee": employee,
                "start_time": ["between", (start_of_day, end_of_day)]
            },
            fields=["project", "description"],
            order_by="start_time asc"
        )

        notes = []
        for interval in intervals:
            if interval.get("description"):
                project_name = interval.get("project")
                # Optional: Fetch project title if needed, but ID is standard
                # project_title = frappe.db.get_value("Project", project_name, "project_name") or project_name

                note_line = f"{project_name} - {interval.get('description')}"
                notes.append(note_line)

        if notes:
            final_note = "\n".join(notes)
            frappe.db.set_value("Timesheet", timesheet_name, "note", final_note)

    except Exception as e:
        frappe.log_error(f"Failed to update Timesheet note: {str(e)}", "Time Kiosk Sync Error")

@frappe.whitelist()
def get_current_status():
    """
    Returns the current active interval for the logged in user.
    Also returns employee info even if no interval is open.
    """
    user = frappe.session.user
    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")

    result = {}
    if employee:
        result["employee"] = employee

    if not employee:
        # If no employee record, we can't really do anything
        return None

    # Get open or paused interval
    interval = frappe.db.get_value("Job Interval", {
        "employee": employee,
        "status": ["in", ["Open", "Paused"]]
    }, ["name", "project", "task", "start_time", "description", "status", "time_category", "total_paused_seconds", "last_pause_time"], as_dict=True)

    if interval:
        # Use dictionary access for compatibility with plain dicts (in case of custom queries/mocks)
        project_title = frappe.db.get_value("Project", interval.get("project"), "project_name")
        interval["project_title"] = project_title or interval.get("project")

        if interval.get("task"):
            task_title = frappe.db.get_value("Task", interval.get("task"), "subject")
            interval["task_title"] = task_title or interval.get("task")

        interval["attachments"] = frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Job Interval", "attached_to_name": interval.get("name")},
            fields=["name", "file_name", "file_url"]
        )

        # Merge interval data into result
        result.update(interval)
        return result

    # Return at least the employee ID if idle
    return result

@frappe.whitelist()
def get_projects():
    """
    Returns list of active projects.
    Filters by 'is_active' = 'Yes' if field exists, else 'status' = 'Open'.
    """
    try:
        meta = frappe.get_meta("Project")
        has_is_active = any(f.fieldname == 'is_active' for f in meta.fields)
    except Exception:
        # Fallback if meta cannot be loaded (unlikely)
        has_is_active = False

    if has_is_active:
        return frappe.get_list("Project",
            filters={"is_active": "Yes"},
            fields=["name", "project_name"])
    else:
        # Fallback to standard status
        return frappe.get_list("Project",
            filters={"status": "Open"},
            fields=["name", "project_name"])


@frappe.whitelist()
def get_kiosk_options():
    """Picker options for the standalone kiosk PWA (which has no desk Link controls):
    active projects and activity types as [{value, label}] lists.

    Projects additionally carry ``lat``/``lng`` when the project has a Sapphire
    Maintenance Profile with site coordinates — the kiosk's searchable picker
    uses them to sort nearest-site-first and show a distance badge. ``value`` is
    the Project docname (PRJ-#####), which the picker also matches against, so
    technicians can search by project number as well as title."""
    sites = {
        s.project: s
        for s in frappe.get_all(
            "Sapphire Maintenance Profile",
            filters={"latitude": ["!=", 0], "longitude": ["!=", 0]},
            fields=["project", "latitude", "longitude"],
        )
        if s.project
    }
    projects = []
    for p in get_projects():
        item = {"value": p["name"], "label": p.get("project_name") or p["name"]}
        site = sites.get(p["name"])
        if site:
            item["lat"] = site.latitude
            item["lng"] = site.longitude
        projects.append(item)
    activity_types = [
        {"value": a["name"], "label": a["name"]}
        for a in frappe.get_all("Activity Type", fields=["name"], order_by="name asc")
    ]
    return {"projects": projects, "activity_types": activity_types}


@frappe.whitelist()
def get_tasks_for_project(project):
    """Open tasks under a project as [{value, label}] for the kiosk task picker."""
    if not project:
        return []
    return [
        {"value": t["name"], "label": t.get("subject") or t["name"]}
        for t in frappe.get_all(
            "Task",
            filters={"project": project},
            fields=["name", "subject"],
            order_by="modified desc",
            limit_page_length=200,
        )
    ]


@frappe.whitelist()
def get_maintenance_context(project=None, since=None):
    """Maintenance-form context for the kiosk's active job card.

    Called by the kiosk PWA when a technician is clocked into a project, and
    again before clock-out / project-switch. A project "has maintenance" when
    it carries an Active Sapphire Maintenance Contract or resolves to an
    Active maintenance form template (project- or customer-scoped).

    Args:
        project (str): Project name (docname).
        since (str, optional): Datetime (the interval's clock-in). When given,
            ``submitted_since`` reports whether the session user submitted a
            Sapphire Maintenance Record for the project after that moment —
            the basis for the "no form submitted" clock-out warning.

    Returns:
        dict: {"required": False} when the project has no maintenance; else {
            "required": True,
            "contract": Active contract name or None,
            "draft": newest open draft record for the project or None,
            "form_route": desk URL — the Visit Wizard when a draft exists,
                else a prefilled new-record desk route (project/customer/
                contract/technician as query params -> frappe.route_options;
                the wizard needs an existing record, so the create path stays
                on the desk form),
            "submitted_since": bool (False when ``since`` not given),
        }
    """
    if not project:
        return {"required": False}

    contract = frappe.db.get_value(
        "Sapphire Maintenance Contract",
        {"project": project, "status": "Active"},
        ["name", "visit_shape"],
        as_dict=True,
    )
    if not contract:
        from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
            resolve_template,
        )
        if not resolve_template(project=project):
            return {"required": False}

    draft = frappe.db.get_value(
        "Sapphire Maintenance Record",
        {"project": project, "docstatus": 0},
        "name",
        order_by="modified desc",
    )

    if draft:
        form_route = "/app/visit-wizard?record=" + draft
    else:
        from urllib.parse import urlencode

        params = {"project": project, "technician": frappe.session.user}
        customer = frappe.db.get_value("Project", project, "customer")
        if customer:
            params["customer"] = customer
        if contract:
            params["maintenance_contract"] = contract.name
        form_route = "/app/sapphire-maintenance-record/new?" + urlencode(params)

    submitted_since = False
    if since:
        user = frappe.session.user
        submitted_since = bool(frappe.get_all(
            "Sapphire Maintenance Record",
            filters={
                "project": project,
                "docstatus": 1,
                "modified": [">=", get_datetime(since)],
            },
            or_filters=[["technician", "=", user], ["owner", "=", user]],
            limit=1,
        ))

    return {
        "required": True,
        "contract": contract.name if contract else None,
        "draft": draft,
        "form_route": form_route,
        "submitted_since": submitted_since,
    }


@frappe.whitelist()
def get_my_visits_today():
    """Open maintenance visit drafts for the kiosk's "Today's Visits" list.

    Draft Sapphire Maintenance Records that are unassigned or assigned to the
    session user (the predictive scheduler creates them as bare headers).
    Returns [{name, project, project_title, serial_no, visit_label, route}],
    oldest first, capped at 10.
    """
    user = frappe.session.user
    drafts = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={"docstatus": 0},
        or_filters=[["technician", "=", user], ["technician", "is", "not set"]],
        fields=["name", "project", "serial_no", "visit_label"],
        order_by="creation asc",
        limit=10,
    )
    projects = {d.project for d in drafts if d.project}
    titles = {}
    if projects:
        titles = dict(frappe.get_all(
            "Project",
            filters={"name": ["in", list(projects)]},
            fields=["name", "project_name"],
            as_list=True,
        ))
    for d in drafts:
        d["project_title"] = titles.get(d.project) or d.project
        d["route"] = "/app/visit-wizard?record=" + d.name
    return drafts


@frappe.whitelist()
def get_nearby_visit(lat=None, lng=None):
    """Geofenced clock-in suggestion for the idle kiosk.

    Compares the device position against Sapphire Maintenance Profile site
    coordinates; within the configured radius (ERPNext Enhancements Settings >
    Site Geofence Radius, 0 = disabled), returns the nearest site that has a
    visit waiting — an open draft record, or an Active contract feature due
    within 7 days. Returns None when there is nothing to suggest.
    """
    if not lat or not lng:
        return None
    radius = cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "geofence_radius_m"))
    if not radius:
        return None

    lat, lng = flt(lat), flt(lng)
    sites = frappe.get_all(
        "Sapphire Maintenance Profile",
        filters={"latitude": ["!=", 0], "longitude": ["!=", 0]},
        fields=["project", "latitude", "longitude"],
    )

    best = None
    for site in sites:
        distance = _haversine_m(lat, lng, site.latitude, site.longitude)
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, site.project)
    if not best:
        return None

    distance, project = best
    has_visit = frappe.db.exists("Sapphire Maintenance Record", {"project": project, "docstatus": 0})
    if not has_visit:
        contract = frappe.db.get_value(
            "Sapphire Maintenance Contract", {"project": project, "status": "Active"}, "name"
        )
        if not contract:
            return None
        has_visit = frappe.db.exists(
            "Sapphire Contract Feature",
            {"parent": contract, "next_visit_date": ["<=", add_days(frappe.utils.nowdate(), 7)]},
        )
    if not has_visit:
        return None

    return {
        "project": project,
        "project_title": frappe.db.get_value("Project", project, "project_name") or project,
        "distance_m": round(distance),
    }


def _haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance between two WGS84 points, in meters."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lng1, lat2, lng2 = map(radians, (flt(lat1), flt(lng1), flt(lat2), flt(lng2)))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 6371000 * 2 * asin(sqrt(a))


@frappe.whitelist()
def link_attachment(file_name, project, task=None):
    """
    After a file is uploaded to a Job Interval, duplicate the File record so
    the same attachment is also visible on the linked Project and Task.
    """
    try:
        original = frappe.get_doc("File", file_name)

        targets = [("Project", project)]
        if task:
            targets.append(("Task", task))

        for doctype, docname in targets:
            if not docname:
                continue
            already_linked = frappe.db.exists("File", {
                "file_url": original.file_url,
                "attached_to_doctype": doctype,
                "attached_to_name": docname
            })
            if not already_linked:
                linked = frappe.get_doc({
                    "doctype": "File",
                    "file_url": original.file_url,
                    "file_name": original.file_name,
                    "attached_to_doctype": doctype,
                    "attached_to_name": docname,
                    "folder": original.folder,
                    "is_private": original.is_private
                })
                linked.insert(ignore_permissions=True)

        return {
            "status": "success",
            "file_name": original.file_name,
            "file_url": original.file_url
        }
    except Exception as e:
        frappe.log_error(f"Failed to link attachment {file_name}: {str(e)}", "Time Kiosk Attachment Error")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Geolocation telemetry
# ---------------------------------------------------------------------------

def _session_employee():
    """Employee linked to the current session user, or None."""
    return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def _resolve_employee(claimed_employee=None):
    """Decide which employee a location point belongs to.

    Security model: if the session user IS an employee, that always wins and a
    mismatched ``claimed_employee`` is rejected (so a worker can't post points as
    a colleague). When there is no session employee (e.g. Administrator / tests),
    fall back to the explicitly supplied employee for back-compat.
    """
    session_emp = _session_employee()
    if session_emp:
        if claimed_employee and claimed_employee != session_emp:
            frappe.throw(_("You can only log location for yourself."), frappe.PermissionError)
        return session_emp
    if not claimed_employee:
        frappe.throw(_("Employee ID is required for logging location."))
    return claimed_employee


def _parse_timestamp(ts):
    """Accept a Frappe datetime string, an ISO-ish 'YYYY-MM-DD HH:MM:SS' string,
    or epoch milliseconds (number or numeric string). Returns a datetime."""
    if ts in (None, ""):
        return now_datetime()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000.0)
    try:
        return get_datetime(ts)
    except Exception:
        # Possibly epoch-ms delivered as a string.
        return datetime.fromtimestamp(float(ts) / 1000.0)


def _valid_coords(lat, lng):
    try:
        lat = flt(lat)
        lng = flt(lng)
    except Exception:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


@frappe.whitelist()
def log_geolocation(employee=None, latitude=None, longitude=None, device_agent=None,
                    log_status=None, timestamp=None, job_interval=None, accuracy=None):
    """
    Logs a single geolocation entry to the Time Kiosk Log.

    Legacy single-point endpoint, kept for back-compat (the PWA uses the batched,
    session-trusted ``log_geolocation_batch`` instead). Trusts the supplied
    ``employee`` for backward compatibility.
    """
    try:
        if not employee:
            frappe.throw(_("Employee ID is required for logging location."))

        doc = frappe.get_doc({
            "doctype": "Time Kiosk Log",
            "employee": employee,
            "user": frappe.session.user,
            "job_interval": _validated_interval(job_interval, employee),
            "timestamp": _parse_timestamp(timestamp),
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "device_agent": device_agent,
            "log_status": log_status or "Success"
        })
        doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Location logged."}
    except Exception as e:
        frappe.log_error(f"Failed to log location: {str(e)}", "Time Kiosk Location Error")
        return {"status": "error", "message": str(e)}


def _validated_interval(job_interval, employee, _cache=None):
    """Return job_interval only if it exists and belongs to ``employee``; else None.

    Pass a dict as ``_cache`` to memoize lookups across a batch.
    """
    if not job_interval:
        return None
    if _cache is not None and job_interval in _cache:
        return _cache[job_interval]
    ok = bool(frappe.db.exists("Job Interval", {"name": job_interval, "employee": employee}))
    result = job_interval if ok else None
    if _cache is not None:
        _cache[job_interval] = result
    return result


@frappe.whitelist()
def log_geolocation_batch(points):
    """
    Bulk-ingest geolocation points captured by the kiosk PWA worker.

    ``points`` is a JSON array (or already-decoded list) of objects, each:
        {
          "client_id": <opaque id the client uses to dedupe its queue>,
          "job_interval": <Job Interval name, optional>,
          "timestamp": <"YYYY-MM-DD HH:MM:SS" local, or epoch ms>,
          "latitude": <float>, "longitude": <float>,
          "accuracy": <m>, "speed": <m/s>, "heading": <deg>, "altitude": <m>,
          "log_status": "Success" | "Offline Sync" | ...,
          "device_agent": <ua string>
        }

    Employee is taken from the session (never trusted from the client); each
    job_interval is verified to belong to that employee. Returns the list of
    accepted client_ids so the worker can clear exactly those from IndexedDB.
    """
    employee = _resolve_employee()
    settings = get_settings()

    if isinstance(points, str):
        points = json.loads(points)
    if not isinstance(points, list):
        frappe.throw(_("'points' must be a list."))

    max_batch = cint(settings.get("max_batch_size")) or 50
    min_accuracy = cint(settings.get("min_accuracy_m"))
    points = points[:max_batch]

    interval_cache = {}
    accepted, rejected = [], []
    user = frappe.session.user
    now_iso = now_datetime()

    for p in points:
        cid = p.get("client_id")
        status = p.get("log_status") or "Success"
        try:
            lat, lng = p.get("latitude"), p.get("longitude")
            if status == "Success":
                if not _valid_coords(lat, lng):
                    rejected.append({"client_id": cid, "reason": "invalid_coords"})
                    continue
                if min_accuracy and p.get("accuracy") and flt(p.get("accuracy")) > min_accuracy:
                    rejected.append({"client_id": cid, "reason": "low_accuracy"})
                    continue

            doc = frappe.get_doc({
                "doctype": "Time Kiosk Log",
                "employee": employee,
                "user": user,
                "job_interval": _validated_interval(p.get("job_interval"), employee, interval_cache),
                "timestamp": _parse_timestamp(p.get("timestamp")) or now_iso,
                "latitude": lat,
                "longitude": lng,
                "accuracy": p.get("accuracy"),
                "speed": p.get("speed"),
                "heading": p.get("heading"),
                "altitude": p.get("altitude"),
                "device_agent": p.get("device_agent"),
                "log_status": status,
            })
            doc.insert(ignore_permissions=True)
            accepted.append(cid)
        except Exception as e:
            frappe.log_error(f"Failed to ingest geo point: {str(e)}", "Time Kiosk Location Error")
            rejected.append({"client_id": cid, "reason": "server_error"})

    return {"status": "success", "accepted": accepted, "rejected": rejected}


@frappe.whitelist()
def get_kiosk_bootstrap():
    """Everything the PWA needs on load: the employee, current interval, and the
    effective tracking settings. Used both by the page context and client refresh."""
    return {
        "employee": _session_employee(),
        "user": frappe.session.user,
        "status": get_current_status(),
        "settings": get_settings(),
        "csrf_token": (frappe.session.data or {}).get("csrf_token"),
    }


def _can_view_employee_logs(employee):
    """True if the session user may view ``employee``'s location history."""
    if TIMELINE_MANAGER_ROLES.intersection(frappe.get_roles()):
        return True
    return _session_employee() == employee


@frappe.whitelist()
def get_location_history(employee, from_datetime=None, to_datetime=None):
    """
    Return successful location points for ``employee`` between the two datetimes,
    grouped by Job Interval (the clock-in session), ordered oldest-first.

    Powers the manager "Location Timeline" page. Permission: manager roles can
    view anyone; everyone else only themselves.
    """
    if not employee:
        frappe.throw(_("Employee is required."))
    if not _can_view_employee_logs(employee):
        frappe.throw(_("Not permitted to view this employee's location history."),
                     frappe.PermissionError)

    if not to_datetime:
        to_datetime = now_datetime()
    if not from_datetime:
        from_datetime = add_days(get_datetime(to_datetime), -1)

    rows = frappe.get_all(
        "Time Kiosk Log",
        filters={
            "employee": employee,
            "log_status": "Success",
            "timestamp": ["between", [from_datetime, to_datetime]],
        },
        fields=["name", "job_interval", "timestamp", "latitude", "longitude",
                "accuracy", "speed", "heading"],
        order_by="timestamp asc",
    )

    # Group by interval, preserving chronological order of first appearance.
    groups = {}
    order = []
    for r in rows:
        key = r.job_interval or "_unassigned"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append({
            "timestamp": r.timestamp,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "accuracy": r.accuracy,
            "speed": r.speed,
            "heading": r.heading,
        })

    # Decorate each interval group with project/task labels.
    interval_meta = {}
    interval_names = [k for k in order if k != "_unassigned"]
    if interval_names:
        for iv in frappe.get_all(
            "Job Interval",
            filters={"name": ["in", interval_names]},
            fields=["name", "project", "task", "start_time", "end_time", "status"],
        ):
            interval_meta[iv.name] = iv

    result = []
    for key in order:
        meta = interval_meta.get(key, {})
        project = meta.get("project") if meta else None
        task = meta.get("task") if meta else None
        result.append({
            "job_interval": None if key == "_unassigned" else key,
            "project": project,
            "project_title": frappe.db.get_value("Project", project, "project_name") if project else None,
            "task": task,
            "task_title": frappe.db.get_value("Task", task, "subject") if task else None,
            "start_time": meta.get("start_time") if meta else None,
            "end_time": meta.get("end_time") if meta else None,
            "points": groups[key],
        })

    return {"employee": employee, "intervals": result, "point_count": len(rows)}


def purge_old_location_logs():
    """Scheduled daily: delete Time Kiosk Log rows older than the configured
    retention window. retention_days <= 0 disables purging (keep forever)."""
    days = cint(get_settings().get("retention_days"))
    if days <= 0:
        return
    cutoff = add_days(now_datetime(), -days)
    frappe.db.delete("Time Kiosk Log", {"timestamp": ["<", cutoff]})
    frappe.db.commit()

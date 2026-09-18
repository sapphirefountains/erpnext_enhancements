"""Time-tracking kiosk + geolocation backend.

Whitelisted API powering the standalone Time Kiosk PWA (``public/js/kiosk/``
and the offline service worker ``www/kiosk-sw.js``), plus the manager
"Location Timeline" Desk page
(``workforce/page/location_timeline/location_timeline.js``). The page
context ``www/kiosk.py`` calls ``get_kiosk_bootstrap``.

Core flow: ``log_time`` opens/pauses/resumes/switches/stops "Job Interval"
documents; on completion an interval is synced into a Draft Timesheet
(``sync_interval_to_timesheet``). Geolocation points are streamed in batches
into "Time Kiosk Log" and visualised per interval.

What was added in v1.480.0 (the kiosk / location overhaul), and where the
logic actually lives — this file stays the HTTP surface:

- clock-in / clock-out **anchor fixes**, the resolved **site**
  (``workforce/sites.py``) and the off-site flag;
- **tracking health** per interval (``workforce/tracking_health.py``, pure
  functions), stamped at close and kept live by ``log_geolocation_batch``;
- **position** and the permlevel-1 **pay/cost** block
  (``workforce/costing.py``), stamped at Start and recomputed by the interval's
  own ``validate``;
- the technician's own views — ``get_my_day``, ``get_my_history``,
  ``get_my_trail``, ``get_shift_summary`` — and **time correction requests**
  (``workforce/corrections.py`` reviews them);
- the manager views — ``get_live_positions``, ``get_employees_for_timeline``,
  an extended ``get_location_history`` and ``export_location_history``.

Security:
        - The employee is derived from the SESSION user, never trusted from the
          client, for every endpoint that reads or writes an employee's own data
          (``_session_employee`` / ``_resolve_employee`` rejects a mismatched
          claimed employee).
        - The legacy ``log_geolocation`` single-point endpoint trusts the
          supplied ``employee`` for back-compat.
        - Manager views are role-gated on ``TIMELINE_MANAGER_ROLES`` (System
          Manager / HR Manager / Projects Manager — the Location Timeline page's
          own role list, and ``tests/test_location_timeline_page.py`` pins the
          two equal); everyone else sees only their own rows.
        - Writes use ``ignore_permissions=True`` after the session-based checks.

Scheduler: ``purge_old_location_logs`` runs daily (hooks.py) to enforce the
configured retention window (0 = keep forever, the default since v1.480.0).
Settings come from the "Time Kiosk Settings" Single DocType.
"""

import csv
import io
import json
from datetime import datetime, timedelta
from xml.sax.saxutils import escape as _xml_escape

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, nowdate

from erpnext_enhancements.workforce import costing, photo_gate, sites, tracking_health
from erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings import (
    get_settings,
)
from erpnext_enhancements.workforce.tracking_health import haversine_m

# Roles allowed to view *anyone's* location history, live positions and the
# employee picker. Everyone else can only view their own. Kept here (rather
# than in Settings) so it can't be widened from the UI. Must equal the roles on
# workforce/page/location_timeline/location_timeline.json.
TIMELINE_MANAGER_ROLES = {"System Manager", "HR Manager", "Projects Manager"}

#: Hard field allowlist for update_interval_times.
#: 36 fields on Job Interval are read_only: 1, and read_only is a Desk hint rather than a server gate.
#: A generic setter here would let a technician clear their own offsite_start, auto_closed or tracking_health.
ALLOWED_UPDATE_FIELDS = {"start_time", "end_time"}

#: Log statuses that are real fixes. ``Low Accuracy`` rows prove the phone was
#: reporting (they count toward coverage) but are excluded from distance and
#: stop detection, where a 300 m fix would invent movement.
TRACKED_STATUSES = ("Success", "Low Accuracy")
PRECISE_STATUS = "Success"

#: Stop detection on the timeline: a run of fixes within this radius lasting
#: at least this long is "stayed put".
STOP_RADIUS_M = 40
STOP_MIN_MINUTES = 5

# Back-compat alias: ``_haversine_m`` moved to workforce/tracking_health.py so
# the pure trail functions have no reason to import frappe.
_haversine_m = haversine_m


@frappe.whitelist()
def log_time(project=None, action=None, lat=None, lng=None, description=None, task=None,
             time_category=None, skip_reason=None, accuracy=None, offsite_acknowledged=None,
             break_minutes=None):
    """
    Logs time for the current employee.
    action: "Start", "Stop", "Pause", "Resume", "Switch"

    ``lat``/``lng``/``accuracy`` are the ANCHOR fix the kiosk took for this
    event (a deliberate high-accuracy read, see ``KioskGeo.anchorFix``). On
    Start / Switch-new they become the start anchor and are compared against
    the project site (``workforce/sites.py``) — outside the geofence sets
    ``offsite_start``, whether or not the kiosk warned. ``offsite_acknowledged``
    records that the technician saw the warning and clocked in anyway. On
    Stop / Switch-old they become the end anchor. ``break_minutes`` is the
    preset chosen on Pause; informational only.

    ``skip_reason`` is only consulted by the two actions that END an interval
    ("Switch" and "Stop"). When the job-photo capture gate is on
    (``workforce/photo_gate.py``), those two actions throw unless the interval
    carries the configured minimum number of photos or a skip reason is supplied.

    The gate runs HERE, on the server, on purpose. The kiosk PWA prompts for a
    photo too, but that prompt runs on a field device that is offline half the
    day and serving a cached bundle of unknown age — client-side validation there
    is a suggestion, not enforcement.

    Pause/Resume are deliberately NOT gated: pausing for lunch is not the end of
    a job, and demanding a photo for it would train everybody to skip.

    Returns ``{"status", "message", "doc", "offsite": {"flagged", "distance_m",
    "radius_m", "site"}}`` — the off-site block describes the start anchor for
    Start/Switch and the end anchor for Stop.
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

        # Serialize concurrent "Start"s for this employee. Lock the Employee row first
        # (it always exists, so it actually serializes — a FOR UPDATE on the zero-match
        # Job Interval query would lock nothing and let both racers through), then read
        # the interval state as a current read so the second request sees the first's
        # committed insert instead of its own older snapshot. Without this, two devices
        # (or a retried request racing its original) both pass the check and both open an
        # interval, and every later Pause/Stop then acts on an arbitrary one.
        frappe.db.get_value("Employee", employee, "name", for_update=True)
        existing = frappe.db.get_value(
            "Job Interval",
            {"employee": employee, "status": ["in", ["Open", "Paused"]]},
            "name",
            for_update=True,
        )
        if existing:
            frappe.throw(_("You already have an active job interval. Please stop or switch it first."))

        resolved_time_category = time_category or frappe.db.get_single_value("Time Kiosk Settings", "default_time_category")
        if not resolved_time_category:
            frappe.throw(_("Activity type is required. Please pick one."))
        doc = _new_interval(employee, project, task, resolved_time_category, description, now_dt,
                            lat, lng, accuracy, offsite_acknowledged)
        doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Work started.", "doc": doc.name,
                "offsite": _offsite_payload(doc, "start")}

    elif action == "Pause":
        open_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": "Open"}, "name")
        if not open_interval:
            frappe.throw(_("No open job found to pause."))

        doc = frappe.get_doc("Job Interval", open_interval)
        doc.status = "Paused"
        doc.last_pause_time = now_dt
        doc.planned_break_minutes = cint(break_minutes) or None
        doc.save(ignore_permissions=True)
        return {"status": "success", "message": "Work paused.", "doc": doc.name,
                "offsite": _offsite_payload(doc, None)}

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
        return {"status": "success", "message": "Work resumed.", "doc": doc.name,
                "offsite": _offsite_payload(doc, None)}

    elif action == "Switch":
        if not project:
            frappe.throw(_("Project is required to switch work."))

        active_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": ["in", ["Open", "Paused"]]}, ["name", "status", "last_pause_time"], as_dict=True)

        if active_interval:
            doc = frappe.get_doc("Job Interval", active_interval.name)
            # Gate BEFORE any mutation: a throw here must leave the outgoing
            # interval open, or a rejected switch would silently close the job
            # the technician is still standing on.
            photo_status = photo_gate.check(doc, skip_reason=skip_reason)
            _close_interval(doc, now_dt, lat, lng, accuracy, photo_status, skip_reason)

        resolved_time_category = time_category or frappe.db.get_single_value("Time Kiosk Settings", "default_time_category")
        if not resolved_time_category:
            frappe.throw(_("Activity type is required. Please pick one."))
        new_doc = _new_interval(employee, project, task, resolved_time_category, description, now_dt,
                                lat, lng, accuracy, offsite_acknowledged)
        new_doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Task switched.", "doc": new_doc.name,
                "offsite": _offsite_payload(new_doc, "start")}

    elif action == "Stop":
        active_interval = frappe.db.get_value("Job Interval", {"employee": employee, "status": ["in", ["Open", "Paused"]]}, ["name", "status", "last_pause_time"], as_dict=True)

        if not active_interval:
            frappe.throw(_("No active job found to stop."))

        doc = frappe.get_doc("Job Interval", active_interval.name)
        # As with Switch: gate before mutating, so a refused clock-out leaves the
        # interval open rather than half-closed.
        photo_status = photo_gate.check(doc, skip_reason=skip_reason)
        _close_interval(doc, now_dt, lat, lng, accuracy, photo_status, skip_reason)

        return {"status": "success", "message": "Work stopped.", "doc": doc.name,
                "offsite": _offsite_payload(doc, "end")}

    else:
        frappe.throw(_("Invalid action. Must be 'Start', 'Stop', 'Pause', 'Resume', or 'Switch'."))



@frappe.whitelist()
def close_interval_for_employee(employee=None, skip_reason=None, source="Triton"):
    caller_emp = _session_employee()
    
    if not employee or employee == caller_emp:
        target_employee = caller_emp
    else:
        if not set(frappe.get_roles(frappe.session.user)).intersection(TIMELINE_MANAGER_ROLES):
            frappe.throw(_("Not permitted to close intervals for other employees."), frappe.PermissionError)
        target_employee = employee
        
    if not target_employee:
        frappe.throw(_("Employee is required."))

    now_dt = now_datetime()
    
    frappe.db.get_value("Employee", target_employee, "name", for_update=True)
    active = frappe.db.get_value("Job Interval", {"employee": target_employee, "status": ["in", ["Open", "Paused"]]}, "name", for_update=True)
    
    if not active:
        frappe.throw(_("No active job found to stop."))
        
    doc = frappe.get_doc("Job Interval", active)
    
    photo_status = photo_gate.resolve(doc, skip_reason=skip_reason)
    
    doc.closed_via = source
    doc.closed_requested_by = frappe.session.user if target_employee != caller_emp else None
    doc.unanchored_close = 1
    
    _close_interval(doc, now_dt, None, None, None, photo_status, skip_reason)
    
    return {
        "status": "success",
        "message": "Work stopped.",
        "doc": doc.name,
        "offsite": _offsite_payload(doc, "end"),
        "closed_via": doc.closed_via,
        "closed_requested_by": doc.closed_requested_by,
        "unanchored_close": doc.unanchored_close
    }


# ---------------------------------------------------------------------------
# The location stash (Triton clock-in)
# ---------------------------------------------------------------------------
#
# Triton may clock somebody IN, and clocking in demands a real browser fix. The
# coordinates must therefore reach the server WITHOUT passing through the
# language model.
#
# A lat/lng supplied as an MCP *tool argument* is a number the model typed. It is
# forgeable and hallucinable, which is exactly what a geofence anchor must never
# be — and it cannot physically arrive that way in any case: the widget's POST
# body is a closed contract, frappe silently drops POST keys absent from the
# Python signature, and ``triton_chat.stream_query`` flattens its structured
# context into prompt *text* before Triton ever sees it.
#
# So the browser hands the fix straight to this endpoint over the user's own
# session, and the clock-in tool reads it back server-side for the calling user
# and nobody else. There is deliberately NO way to stash a fix for another user,
# and no identifier is handed out that could travel through a transcript as a
# bearer token.
#
# The TTL is short on purpose: a fix is evidence of where somebody is standing
# now, not a standing permission.
#
# Note the stash lives in ``frappe.cache()``, which the production deploy
# FLUSHDBs. That is harmless here and fails safe — a lost stash means "share your
# location again", never a wrong anchor.

LOCATION_STASH_TTL_SEC = 120
NO_LOCATION_FIX_MSG = (
    "No recent location fix. Open the Triton widget in ERPNext, share your "
    "location, and try again."
)


def _location_stash_key(user=None):
    return f"ee_kiosk_location_fix::{user or frappe.session.user}"


@frappe.whitelist()
def stash_location_fix(lat=None, lng=None, accuracy=None):
    """Record the caller's own browser geolocation fix for ``LOCATION_STASH_TTL_SEC``.

    Keyed by ``frappe.session.user``. A caller can only ever stash for themselves.
    Returns ``{"ok": True, "expires_in": <seconds>}``.
    """
    if not _valid_coords(lat, lng):
        frappe.throw(_("A valid latitude and longitude are required."))

    payload = {
        "lat": flt(lat),
        "lng": flt(lng),
        "accuracy": flt(accuracy) if accuracy not in (None, "") else None,
        "stamped_at": str(now_datetime()),
    }
    frappe.cache().set_value(
        _location_stash_key(), json.dumps(payload), expires_in_sec=LOCATION_STASH_TTL_SEC
    )
    return {"ok": True, "expires_in": LOCATION_STASH_TTL_SEC}


def get_stashed_location_fix(user=None):
    """The caller's stashed fix, or ``None`` when there is none or it has expired.

    Not whitelisted: this is read server-side by the clock-in path. Nothing may
    ask the server to hand a location back out to a client.
    """
    raw = frappe.cache().get_value(_location_stash_key(user))
    if not raw:
        return None
    try:
        fix = json.loads(raw if isinstance(raw, str) else frappe.safe_decode(raw))
    except Exception:
        return None
    if not _valid_coords(fix.get("lat"), fix.get("lng")):
        return None
    return fix


def consume_stashed_location_fix(user=None):
    """Read the stash and delete it. A fix authorises exactly one clock-in, so a
    second attempt has to be backed by a second, freshly-taken fix."""
    fix = get_stashed_location_fix(user)
    if fix is not None:
        frappe.cache().delete_value(_location_stash_key(user))
    return fix


@frappe.whitelist()
def clock_in_with_stashed_fix(project=None, task=None, time_category=None,
                              description=None, offsite_acknowledged=None, source="Triton"):
    """Clock the CALLER in, anchored to the fix their browser stashed.

    Never accepts an ``employee`` argument: a supervisor's phone is not the crew
    member's, and opening a job is precisely what the geofence exists to prove.
    On-behalf is clock-OUT only (``close_interval_for_employee``).

    No stash, no clock-in — the refusal carries ``NO_LOCATION_FIX_MSG`` so an
    assistant can relay it verbatim.
    """
    fix = consume_stashed_location_fix()
    if not fix:
        frappe.throw(_(NO_LOCATION_FIX_MSG))

    result = log_time(
        project=project,
        action="Start",
        lat=fix["lat"],
        lng=fix["lng"],
        accuracy=fix.get("accuracy"),
        description=description,
        task=task,
        time_category=time_category,
        offsite_acknowledged=offsite_acknowledged,
    )

    if result.get("doc"):
        # Provenance, stamped after the fact rather than threaded through
        # log_time: that signature is the kiosk PWA's contract and is not widened
        # for this. db_set writes the one column without re-running validate,
        # which would recompute the pay block for no reason.
        doc = frappe.get_doc("Job Interval", result["doc"])
        doc.db_set("opened_via", source, update_modified=False)
    result["opened_via"] = source
    return result


def _new_interval(employee, project, task, time_category, description, now_dt,
                  lat, lng, accuracy, offsite_acknowledged):
    """An unsaved Job Interval for a Start / Switch-new, with the start anchor,
    the resolved site, the off-site verdict, the position and the pay rate stamped."""
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
        "total_paused_seconds": 0.0,
    })
    doc.start_accuracy = flt(accuracy) if accuracy not in (None, "") else None
    doc.offsite_acknowledged = cint(offsite_acknowledged)

    site = None
    try:
        site = sites.site_coordinates(project)
    except Exception:
        frappe.log_error(title=f"Time Kiosk: site lookup failed for {project}")
    if site:
        doc.site_latitude = site["lat"]
        doc.site_longitude = site["lng"]
        doc.site_source = site["source"]
        doc.site_radius_m = cint(site["radius_m"])
    else:
        doc.site_radius_m = sites.geofence_radius_m()

    distance = _distance_to_site(doc, lat, lng)
    doc.start_distance_m = distance
    offsite = _is_offsite(doc, distance)
    doc.offsite_start = 1 if offsite else 0 if offsite is False else None

    costing.stamp_position(doc)
    costing.stamp_cost(doc)
    return doc


def _close_interval(doc, now_dt, lat, lng, accuracy, photo_status, skip_reason):
    """Close ``doc`` (Stop / Switch-old): end time, end anchor, off-site verdict,
    photo verdict, tracking health, save (validate stamps the cost), Timesheet sync."""
    if doc.status == "Paused" and doc.last_pause_time:
        doc.end_time = doc.last_pause_time
    else:
        doc.end_time = now_dt
    doc.status = "Completed"

    if _valid_coords(lat, lng):
        doc.end_latitude = flt(lat)
        doc.end_longitude = flt(lng)
        doc.end_accuracy = flt(accuracy) if accuracy not in (None, "") else None
    else:
        doc.end_latitude = None
        doc.end_longitude = None
        doc.end_accuracy = None

    distance = _distance_to_site(doc, lat, lng)
    doc.end_distance_m = distance
    offsite = _is_offsite(doc, distance)
    doc.offsite_end = 1 if offsite else 0 if offsite is False else None

    photo_gate.stamp(doc, photo_status, skip_reason=skip_reason)
    _stamp_tracking_health(doc)
    doc.save(ignore_permissions=True)
    sync_interval_to_timesheet(doc)


def _distance_to_site(doc, lat, lng):
    # Null Island failure: previously, missing lat/lng (None) was converted to 0.0 by flt().
    # This caused the distance to be measured from the site to Null Island (0,0),
    # returning ~11,160,000 m. As a result, an unknown location silently became a confident
    # but false "off-site" verdict (offsite_end = 1). We now require valid coords.
    if not _valid_coords(lat, lng) or not (doc.site_latitude or doc.site_longitude):
        return None
    return round(haversine_m(lat, lng, doc.site_latitude, doc.site_longitude))


def _is_offsite(doc, distance):
    if distance is None:
        return None
    radius = cint(doc.site_radius_m)
    return bool(radius > 0 and distance > radius)


def _offsite_payload(doc, phase):
    """The ``offsite`` block ``log_time`` returns: the start anchor's verdict for
    Start/Switch, the end anchor's for Stop, and a neutral block for Pause/Resume."""
    radius = cint(getattr(doc, "site_radius_m", None))
    site_title = None
    if getattr(doc, "site_latitude", None) or getattr(doc, "site_longitude", None):
        site_title = frappe.db.get_value("Project", doc.project, "project_name") or doc.project
    if phase == "start":
        flagged = bool(doc.offsite_start) if doc.offsite_start is not None else None
        distance = doc.start_distance_m
    elif phase == "end":
        flagged = bool(doc.offsite_end) if doc.offsite_end is not None else None
        distance = doc.end_distance_m
    else:
        flagged, distance = False, None
    return {
        "flagged": flagged,
        "distance_m": cint(distance) if distance is not None else None,
        "radius_m": radius,
        "site": site_title,
    }


def sync_interval_to_timesheet(interval_doc):
    """
    Syncs a completed Job Interval to a Timesheet.

    The new Timesheet Detail line carries ``costing_rate`` / ``costing_amount``
    from the interval's burdened rate (``workforce/costing.py``) — ERPNext's own
    ``TimesheetDetail.update_cost`` keeps a non-zero costing_rate and, for a
    line with no activity type, computes nothing at all — and
    ``custom_job_interval`` (fixture Custom Field) so a correction can find the
    line again (``resync_interval_timesheet``).
    """
    try:
        employee = interval_doc.employee
        project = interval_doc.project

        if not project:
            interval_doc.db_set("sync_status", "Skipped - No Project")
            return

        start_time = get_datetime(interval_doc.start_time)
        end_time = get_datetime(interval_doc.end_time)

        # ERPNext v16 recomputes Timesheet Detail hours from (to_time - from_time) on
        # every save, so subtracting pause time from `hours` alone is silently discarded
        # and the paused time gets billed. Compress the LOGGED window to the billable
        # duration instead — the real clock-out stays on the Job Interval. This also makes
        # the idempotency check below match on re-sync, since stored hours now equal `hours`.
        billable_seconds = max(
            (end_time - start_time).total_seconds() - flt(interval_doc.total_paused_seconds), 0.0
        )
        hours = billable_seconds / 3600.0
        billable_end = start_time + timedelta(seconds=billable_seconds)

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
            "to_time": billable_end,
            "description": interval_doc.description or "Synced from Job Interval"
        }

        rate = flt(getattr(interval_doc, "burdened_rate", None))
        if rate:
            new_log["costing_rate"] = rate
            new_log["costing_amount"] = round(rate * hours, 2)
        if _timesheet_detail_has_interval_link():
            new_log["custom_job_interval"] = interval_doc.name

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
        frappe.log_error(f"Failed to sync Job Interval {interval_doc.name} to Timesheet: {e!s}", "Time Kiosk Sync Error")
        # Update Job Interval sync status to Failed
        interval_doc.db_set("sync_status", "Failed")


def _timesheet_detail_has_interval_link():
    """``has_column`` takes a DOCTYPE and RAISES on an unknown table, hence the guard."""
    try:
        return frappe.db.has_column("Timesheet Detail", "custom_job_interval")
    except Exception:
        return False


def find_timesheet_line(interval_doc):
    """The Timesheet Detail row this interval produced, as ``{name, parent,
    docstatus}`` or None. Keyed on ``custom_job_interval`` when the column exists,
    else on the (employee, project, from_time) triple the sync writes."""
    if _timesheet_detail_has_interval_link():
        row = frappe.db.get_value(
            "Timesheet Detail",
            {"custom_job_interval": interval_doc.name, "parenttype": "Timesheet"},
            ["name", "parent", "docstatus"],
            as_dict=True,
        )
        if row:
            return row
    if not interval_doc.start_time or not interval_doc.project:
        return None
    rows = frappe.db.sql(
        """
        select td.name, td.parent, td.docstatus
        from `tabTimesheet Detail` td
        inner join `tabTimesheet` ts on ts.name = td.parent
        where ts.employee = %(employee)s and td.project = %(project)s and td.from_time = %(from_time)s
        order by td.docstatus desc
        limit 1
        """,
        {"employee": interval_doc.employee, "project": interval_doc.project,
         "from_time": get_datetime(interval_doc.start_time)},
        as_dict=True,
    )
    return rows[0] if rows else None


def resync_interval_timesheet(interval_doc):
    """Remove the Draft Timesheet line this interval produced and sync it again.

    For an approved correction: the times or project moved, so the old line is
    wrong. The line is found by ``custom_job_interval`` (or the triple), removed
    from its Draft Timesheet — the Timesheet is deleted when that was its only
    line, since ERPNext refuses to save an empty one — and ``sync_interval_to_timesheet``
    re-adds it on the right day. Refuses (returns False) when the line sits on a
    SUBMITTED Timesheet; the caller is expected to have checked and said so.
    """
    line = find_timesheet_line(interval_doc)
    if line and cint(line.docstatus) == 1:
        return False
    if line:
        try:
            ts_doc = frappe.get_doc("Timesheet", line.parent)
            remaining = [row for row in ts_doc.time_logs if row.name != line.name]
            if remaining:
                ts_doc.time_logs = []
                for row in remaining:
                    ts_doc.append("time_logs", row.as_dict())
                ts_doc.save(ignore_permissions=True)
            else:
                frappe.delete_doc("Timesheet", ts_doc.name, ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Failed to remove Timesheet line for {interval_doc.name}: {e!s}", "Time Kiosk Sync Error")
            interval_doc.db_set("sync_status", "Failed")
            return False
    if interval_doc.status == "Completed" and interval_doc.end_time:
        sync_interval_to_timesheet(interval_doc)
    return True


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
        frappe.log_error(f"Failed to update Timesheet note: {e!s}", "Time Kiosk Sync Error")


# ---------------------------------------------------------------------------
# Tracking health
# ---------------------------------------------------------------------------

def _fix_times(job_interval, statuses=TRACKED_STATUSES):
    """Timestamps of the interval's real fixes, oldest first."""
    if not job_interval:
        return []
    rows = frappe.get_all(
        "Time Kiosk Log",
        filters={"job_interval": job_interval, "log_status": ["in", list(statuses)]},
        pluck="timestamp",
        order_by="timestamp asc",
    )
    return [get_datetime(t) for t in rows if t]


def _health_inputs(settings=None):
    settings = settings or get_settings()
    return {
        "gap_minutes": cint(settings.get("tracking_gap_minutes")) or 15,
        "tracking_enabled": bool(cint(settings.get("enable_tracking"))),
    }


def _stamp_tracking_health(doc, settings=None):
    """Score ``doc``'s trail and write the result onto it (not saved). Returns the
    dict ``tracking_health.compute_health`` produced. Never raises: an interval
    that cannot be scored still has to close."""
    try:
        end = get_datetime(doc.end_time) if doc.end_time else now_datetime()
        result = tracking_health.compute_health(
            get_datetime(doc.start_time), end, _fix_times(doc.name), **_health_inputs(settings)
        )
    except Exception:
        frappe.log_error(title=f"Time Kiosk: tracking health failed for {doc.name}")
        return None
    doc.fix_count = cint(result["fix_count"])
    doc.last_fix_at = result["last_fix_at"]
    doc.gap_minutes = flt(result["gap_minutes"])
    doc.tracking_coverage_pct = flt(result["coverage_pct"])
    doc.tracking_health = result["health"]
    return result


@frappe.whitelist()
def refresh_tracking_health(job_interval):
    """Recompute one interval's tracking health from the Time Kiosk Log and save
    it. Managers, or the interval's own employee. Returns the stored dict."""
    if not job_interval:
        frappe.throw(_("Job Interval is required."))
    doc = frappe.get_doc("Job Interval", job_interval)
    if not _can_view_employee_logs(doc.employee):
        frappe.throw(_("Not permitted to refresh this interval."), frappe.PermissionError)
    result = _stamp_tracking_health(doc)
    doc.save(ignore_permissions=True)
    return {
        "fix_count": doc.fix_count,
        "last_fix_at": doc.last_fix_at,
        "gap_minutes": doc.gap_minutes,
        "coverage_pct": doc.tracking_coverage_pct,
        "health": doc.tracking_health,
        "computed": bool(result),
    }


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
    }, ["name", "project", "task", "start_time", "description", "status", "time_category",
        "total_paused_seconds", "last_pause_time",
        # v1.480.0: what the Working view shows and what the tracking chip reads.
        "position", "position_tier", "planned_break_minutes", "last_fix_at", "fix_count",
        "site_latitude", "site_longitude", "site_radius_m", "start_distance_m",
        "offsite_start", "tracking_health"], as_dict=True)

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

        # Photos that count toward the capture gate. Pending rows are included:
        # the photo was taken, the bytes are still on the device. The client is
        # told the number so it can prompt accurately, but it only ever raises
        # its own count from this — an offline capture the server has not heard
        # about yet must not be un-counted by a status refresh.
        interval["photo_count"] = _gate_photo_count(interval.get("name"))

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
    active projects and activity types as [{value, label}] lists, the employee's
    recent projects, and the geofence radius.

    Projects additionally carry ``lat``/``lng`` when a site resolves for them
    (``workforce/sites.py``: Maintenance Profile, linked Address, or the
    Project's own geocoded coordinates) — the kiosk's picker uses them to sort
    nearest-site-first and show a distance badge, and to warn before an off-site
    clock-in. ``value`` is the Project docname (PRJ-#####), which the picker also
    matches against, so technicians can search by project number as well as
    title. ``recent_projects`` is the last 10 distinct projects this employee
    clocked into, newest first."""
    projects_raw = get_projects()
    coords = {}
    try:
        coords = sites.site_coordinates_bulk([p["name"] for p in projects_raw])
    except Exception:
        frappe.log_error(title="Time Kiosk: site_coordinates_bulk failed")
    projects = []
    for p in projects_raw:
        item = {"value": p["name"], "label": p.get("project_name") or p["name"]}
        site = coords.get(p["name"])
        if site:
            item["lat"] = site["lat"]
            item["lng"] = site["lng"]
        projects.append(item)
    activity_types = [
        {"value": a["name"], "label": a["name"]}
        for a in frappe.get_all("Activity Type", fields=["name"], order_by="name asc")
    ]

    recent = []
    employee = _session_employee()
    if employee:
        recent = [
            r.project
            for r in frappe.db.sql(
                """
                select project, max(start_time) as last_start
                from `tabJob Interval`
                where employee = %(employee)s and coalesce(project, '') != ''
                group by project
                order by last_start desc
                limit 10
                """,
                {"employee": employee},
                as_dict=True,
            )
        ]

    return {
        "projects": projects,
        "activity_types": activity_types,
        "recent_projects": recent,
        "radius_m": sites.geofence_radius_m(),
    }


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
    sites_rows = frappe.get_all(
        "Sapphire Maintenance Profile",
        filters={"latitude": ["!=", 0], "longitude": ["!=", 0]},
        fields=["project", "latitude", "longitude"],
    )

    best = None
    for site in sites_rows:
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
        frappe.log_error(f"Failed to link attachment {file_name}: {e!s}", "Time Kiosk Attachment Error")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Job photos (WP-2 capture gate / WP-3 routing)
# ---------------------------------------------------------------------------

def _gate_photo_count(job_interval):
    """Photos on an interval that satisfy the capture gate, without loading it.

    Returns 0 rather than raising on a bench where the child table has not been
    created yet — a time clock nobody can read the status of is worse than a
    missing number.
    """
    if not job_interval:
        return 0
    try:
        if not frappe.db.table_exists("Job Interval Photo"):
            return 0
        return frappe.db.count("Job Interval Photo", {
            "parent": job_interval,
            "parenttype": "Job Interval",
            "upload_status": ["in", list(photo_gate.CAPTURED_STATES)],
        })
    except Exception:
        return 0


@frappe.whitelist()
def record_job_photo(job_interval=None, client_uid=None, file_name=None, caption=None,
                     captured_on=None, lat=None, lng=None, upload_failed=None):
    """Register or update one photo on a Job Interval.

    Called by the kiosk PWA twice for the same photo in the normal offline case:

    1. **At capture**, immediately, with only ``client_uid``. The row lands with
       ``upload_status = "Pending"`` and the capture gate is satisfied from that
       moment — before a single byte has moved. That is the whole point: crews
       work sites with no signal, and the requirement is that the job was
       photographed, not that an upload completed on schedule.
    2. **After the upload succeeds**, with ``file_name`` (the File docname
       frappe's upload endpoint returned). The same row flips to ``Uploaded``.

    ``client_uid`` is minted on the DEVICE at capture time and is what makes this
    idempotent: a queued upload that retries over a flaky link updates its row
    instead of piling up duplicates. It is required for exactly that reason.

    ``upload_failed`` marks a row Failed and bumps its attempt counter, mirroring
    the ``sync_status`` / ``sync_attempts`` pattern already on Job Interval.

    Permission model: the interval must belong to the session employee. A
    manager cannot post photos as somebody else through this endpoint — same rule
    as ``log_geolocation_batch``.
    """
    if not client_uid:
        frappe.throw(_("A client capture id is required."))
    client_uid = str(client_uid)[:140]

    employee = _session_employee()
    if not employee:
        frappe.throw(_("No Employee record found for this user."), frappe.PermissionError)

    doc = _resolve_own_interval(job_interval, employee)

    row = None
    for existing in (doc.get("photos") or []):
        if existing.client_uid == client_uid:
            row = existing
            break
    if row is None:
        row = doc.append("photos", {"client_uid": client_uid})
        row.captured_on = _parse_timestamp(captured_on)
        if _valid_coords(lat, lng):
            row.latitude = lat
            row.longitude = lng

    if caption:
        row.caption = str(caption)[:140]

    if cint(upload_failed):
        row.upload_status = "Failed"
        row.sync_attempts = cint(row.sync_attempts) + 1
    elif file_name:
        attached = _attach_photo_file(row, file_name, doc)
        row.upload_status = "Uploaded" if attached else "Pending"
    elif not row.upload_status:
        row.upload_status = "Pending"

    # Keep the roll-up flag honest on every write, not just at close: the Job
    # Photo Compliance report reads it, and a stale flag there is worse than none.
    if hasattr(doc, "photos_pending_upload"):
        doc.photos_pending_upload = 1 if photo_gate.has_unsettled_uploads(doc) else 0

    doc.save(ignore_permissions=True)

    if row.upload_status == "Uploaded" and row.file_doc:
        # WP-3: fan the File out to the Project and mirror it into the customer's
        # Drive folder. Enqueued, never inline — Drive is a third-party API and a
        # technician's clock-out must not wait on it.
        frappe.enqueue(
            "erpnext_enhancements.workforce.photo_routing.route_job_photo",
            queue="long",
            job_interval=doc.name,
            file_name=row.file_doc,
            enqueue_after_commit=True,
        )

    return {
        "status": "success",
        "job_interval": doc.name,
        "client_uid": client_uid,
        "upload_status": row.upload_status,
        "photos_captured": photo_gate.photos_captured(doc),
    }


@frappe.whitelist()
def get_pending_photo_uploads(job_interval=None):
    """Client capture ids the server is still waiting on, for this employee.

    Lets a reinstalled or reset PWA reconcile its local queue against reality
    instead of re-uploading everything (or, worse, dropping photos it thinks it
    already sent).
    """
    employee = _session_employee()
    if not employee:
        return {"pending": []}

    filters = {"employee": employee}
    if job_interval:
        filters["name"] = job_interval

    intervals = frappe.get_all("Job Interval", filters=filters, pluck="name", limit=200,
                               order_by="creation desc")
    if not intervals:
        return {"pending": []}

    rows = frappe.get_all(
        "Job Interval Photo",
        filters={"parent": ["in", intervals], "parenttype": "Job Interval",
                 "upload_status": ["in", list(photo_gate.UNSETTLED_STATES)]},
        fields=["parent as job_interval", "client_uid", "upload_status", "sync_attempts"],
        limit=500,
    )
    return {"pending": rows}


def _resolve_own_interval(job_interval, employee):
    """The interval a photo belongs to: the named one, or the employee's active one."""
    if job_interval:
        doc = frappe.get_doc("Job Interval", job_interval)
        if doc.employee != employee:
            frappe.throw(_("You can only add photos to your own jobs."), frappe.PermissionError)
        return doc

    active = frappe.db.get_value(
        "Job Interval", {"employee": employee, "status": ["in", ["Open", "Paused"]]}, "name"
    )
    if not active:
        frappe.throw(_("No active job to attach a photo to."))
    return frappe.get_doc("Job Interval", active)


def _attach_photo_file(row, file_name, interval):
    """Point ``row`` at an uploaded File, re-asserting privacy. Returns True on success.

    ``is_private`` is re-asserted rather than trusted: these are photographs of
    customers' property, and a public file URL is unauthenticated and effectively
    permanent. Same reasoning as ``fountain_move/photos.py``.
    """
    try:
        file_doc = frappe.get_doc("File", file_name)
    except frappe.DoesNotExistError:
        # The upload did not land. Leave the row Pending so the device retries
        # rather than recording a photo that does not exist.
        return False

    if not cint(file_doc.is_private):
        file_doc.is_private = 1
        file_doc.save(ignore_permissions=True)

    row.file_doc = file_doc.name
    row.image = file_doc.file_url
    if not row.captured_on:
        row.captured_on = file_doc.creation

    # Anchor the File to the interval so it is reachable from the job record even
    # if the child row is later removed.
    if not file_doc.attached_to_doctype:
        frappe.db.set_value("File", file_doc.name, {
            "attached_to_doctype": "Job Interval",
            "attached_to_name": interval.name,
        }, update_modified=False)

    return True


# ---------------------------------------------------------------------------
# The technician's own views (My Day / Map / Clock Out review)
# ---------------------------------------------------------------------------

def _day_bounds(date=None):
    """``(day, "YYYY-MM-DD 00:00:00", "YYYY-MM-DD 23:59:59.999999")`` for a site date."""
    day = getdate(date) if date else getdate(nowdate())
    return day, f"{day} 00:00:00", f"{day} 23:59:59.999999"


def _worked_seconds(row, now=None):
    """Net seconds of an interval row (dict) — closed, paused, or live."""
    now = now or now_datetime()
    start = get_datetime(row.get("start_time"))
    if row.get("end_time"):
        end = get_datetime(row.get("end_time"))
    elif row.get("status") == "Paused" and row.get("last_pause_time"):
        end = get_datetime(row.get("last_pause_time"))
    else:
        end = now
    return max((end - start).total_seconds() - flt(row.get("total_paused_seconds")), 0.0)


def _titles(doctype, names, field):
    names = sorted({n for n in names if n})
    if not names:
        return {}
    return dict(frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field], as_list=True))


def _require_session_employee():
    employee = _session_employee()
    if not employee:
        frappe.throw(_("No Employee record found for this user."), frappe.PermissionError)
    return employee


@frappe.whitelist()
def get_my_day(date=None):
    from erpnext_enhancements.workforce import timesheet_lock
    """The session employee's intervals for one site date (default today), with
    derived durations and the badges the My Day view shows.

    ``worked_seconds`` is live for an open interval and stops at the pause for a
    paused one; ``total_seconds`` sums them. ``sites`` is the distinct project
    titles in clock-in order. ``pending_corrections`` counts the employee's
    ``Requested`` Time Correction Requests (all dates — a pending request is a
    pending request whichever day it is about).
    """
    employee = _require_session_employee()
    day, start_of_day, end_of_day = _day_bounds(date)
    now = now_datetime()

    rows = frappe.get_all(
        "Job Interval",
        filters={"employee": employee, "start_time": ["between", [start_of_day, end_of_day]]},
        fields=["name", "project", "task", "time_category", "start_time", "end_time", "status",
                "total_paused_seconds", "last_pause_time", "tracking_health", "tracking_coverage_pct",
                "offsite_start", "auto_closed", "corrected", "planned_break_minutes", "manual_start"],
        order_by="start_time asc",
    )
    project_titles = _titles("Project", [r.project for r in rows], "project_name")
    task_titles = _titles("Task", [r.task for r in rows], "subject")

    intervals = []
    total = 0.0
    site_titles = []
    for r in rows:
        worked = _worked_seconds(r, now)
        total += worked
        title = project_titles.get(r.project) or r.project
        if title and title not in site_titles:
            site_titles.append(title)
        intervals.append({
            "name": r.name,
            "project": r.project,
            "project_title": title,
            "task": r.task,
            "task_title": task_titles.get(r.task) or r.task,
            "time_category": r.time_category,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "status": r.status,
            "worked_seconds": round(worked),
            "paused_seconds": round(flt(r.total_paused_seconds)),
            "photo_count": _gate_photo_count(r.name),
            "tracking_health": r.tracking_health,
            "tracking_coverage_pct": r.tracking_coverage_pct,
            "offsite_start": cint(r.offsite_start),
            "auto_closed": cint(r.auto_closed),
            "manual_start": cint(r.manual_start),
            "locked": bool(timesheet_lock.submitted_timesheet_for(r.name)),
            "editable": not bool(timesheet_lock.submitted_timesheet_for(r.name)) and r.status in ("Open", "Paused", "Completed"),
            "corrected": cint(r.corrected),
            "planned_break_minutes": r.planned_break_minutes,
        })

    pending = 0
    try:
        pending = frappe.db.count("Time Correction Request", {"employee": employee, "status": "Requested"})
    except Exception:
        pending = 0

    return {
        "date": str(day),
        "intervals": intervals,
        "total_seconds": round(total),
        "first_start": rows[0].start_time if rows else None,
        "last_end": rows[-1].end_time if rows else None,
        "sites": site_titles,
        "pending_corrections": pending,
    }


@frappe.whitelist()
def get_my_history(days=14):
    """One row per calendar day for the last ``days`` days (zero days included),
    newest first: net seconds of Completed intervals, interval count, distinct
    sites. Today additionally includes the live open interval, so the strip
    agrees with My Day."""
    employee = _require_session_employee()
    days = max(1, min(cint(days) or 14, 92))
    today = getdate(nowdate())
    first_day = add_days(today, -(days - 1))

    rows = frappe.db.sql(
        """
        select
            date(start_time) as day,
            sum(greatest(timestampdiff(second, start_time, end_time) - coalesce(total_paused_seconds, 0), 0)) as seconds,
            count(*) as interval_count,
            count(distinct project) as site_count
        from `tabJob Interval`
        where employee = %(employee)s
          and status = 'Completed'
          and end_time is not null
          and start_time >= %(from_dt)s
          and start_time < %(to_dt)s
        group by date(start_time)
        """,
        {"employee": employee, "from_dt": f"{first_day} 00:00:00", "to_dt": f"{add_days(today, 1)} 00:00:00"},
        as_dict=True,
    )
    by_day = {str(r.day): r for r in rows}

    live = frappe.db.get_value(
        "Job Interval",
        {"employee": employee, "status": ["in", ["Open", "Paused"]]},
        ["start_time", "end_time", "status", "total_paused_seconds", "last_pause_time"],
        as_dict=True,
    )

    result = []
    grand_total = 0.0
    for offset in range(days):
        day = add_days(today, -offset)
        key = str(day)
        r = by_day.get(key)
        seconds = flt(r.seconds) if r else 0.0
        count = cint(r.interval_count) if r else 0
        site_count = cint(r.site_count) if r else 0
        if live and getdate(live.start_time) == day:
            seconds += _worked_seconds(live)
            count += 1
            site_count = max(site_count, 1)
        grand_total += seconds
        result.append({
            "date": key,
            "total_seconds": round(seconds),
            "interval_count": count,
            "site_count": site_count,
        })

    return {"days": result, "total_seconds": round(grand_total)}


@frappe.whitelist()
def get_my_trail(date=None):
    """The session employee's own fixes and intervals for one site date, for the
    kiosk's Map tab. Includes ``Low Accuracy`` points (the map draws them hollow)."""
    employee = _require_session_employee()
    day, start_of_day, end_of_day = _day_bounds(date)

    points = frappe.get_all(
        "Time Kiosk Log",
        filters={
            "employee": employee,
            "log_status": ["in", list(TRACKED_STATUSES)],
            "timestamp": ["between", [start_of_day, end_of_day]],
        },
        fields=["timestamp", "latitude", "longitude", "accuracy", "job_interval", "fix_source", "log_status"],
        order_by="timestamp asc",
    )
    intervals = frappe.get_all(
        "Job Interval",
        filters={"employee": employee, "start_time": ["between", [start_of_day, end_of_day]]},
        fields=["name", "project", "start_time", "end_time", "site_latitude", "site_longitude", "site_radius_m"],
        order_by="start_time asc",
    )
    titles = _titles("Project", [i.project for i in intervals], "project_name")
    for i in intervals:
        i["project_title"] = titles.get(i.project) or i.project
        i.pop("project", None)

    return {"date": str(day), "points": points, "intervals": intervals, "radius_m": sites.geofence_radius_m()}


@frappe.whitelist()
def get_shift_summary():
    """What the Clock Out review sheet shows: today's net seconds, this interval's,
    the sites, photos and a LIVE tracking coverage for the open interval."""
    employee = _require_session_employee()
    now = now_datetime()
    _day, start_of_day, end_of_day = _day_bounds()

    rows = frappe.get_all(
        "Job Interval",
        filters={"employee": employee, "start_time": ["between", [start_of_day, end_of_day]]},
        fields=["name", "project", "start_time", "end_time", "status", "total_paused_seconds",
                "last_pause_time", "fix_count", "last_fix_at"],
        order_by="start_time asc",
    )
    titles = _titles("Project", [r.project for r in rows], "project_name")
    today_seconds = sum(_worked_seconds(r, now) for r in rows)
    site_titles = []
    for r in rows:
        title = titles.get(r.project) or r.project
        if title and title not in site_titles:
            site_titles.append(title)

    active = next((r for r in rows if r.status in ("Open", "Paused")), None)
    if not active:
        active_row = frappe.db.get_value(
            "Job Interval",
            {"employee": employee, "status": ["in", ["Open", "Paused"]]},
            ["name", "project", "start_time", "end_time", "status", "total_paused_seconds",
             "last_pause_time", "fix_count", "last_fix_at"],
            as_dict=True,
        )
        active = active_row

    summary = {
        "today_seconds": round(today_seconds),
        "interval_seconds": 0,
        "sites": site_titles,
        "photo_count": 0,
        "fix_count": 0,
        "last_fix_at": None,
        "coverage_pct": None,
        "interval": None,
    }
    if active:
        fixes = _fix_times(active.name)
        health = tracking_health.compute_health(
            get_datetime(active.start_time), now, fixes, **_health_inputs()
        )
        summary.update({
            "interval": active.name,
            "interval_seconds": round(_worked_seconds(active, now)),
            "photo_count": _gate_photo_count(active.name),
            "fix_count": health["fix_count"],
            "last_fix_at": health["last_fix_at"],
            "coverage_pct": health["coverage_pct"],
        })
    return summary


# ---------------------------------------------------------------------------
# Time correction requests (the employee's half; review is workforce/corrections.py)
# ---------------------------------------------------------------------------

TCR_LIST_FIELDS = ["name", "request_type", "status", "job_interval", "reason", "requested_on",
                   "reviewed_on", "review_note", "proposed_start", "proposed_end", "proposed_project"]


@frappe.whitelist()
def submit_correction_request(request_type, reason, job_interval=None, proposed_start=None,
                              proposed_end=None, proposed_project=None, proposed_task=None,
                              proposed_activity=None):
    """File a Time Correction Request for the session employee. The controller
    validates the proposal shape and that ``job_interval`` is theirs; the
    supervisor is emailed on insert."""
    employee = _require_session_employee()
    doc = frappe.get_doc({
        "doctype": "Time Correction Request",
        "employee": employee,
        "job_interval": job_interval or None,
        "request_type": request_type,
        "proposed_start": get_datetime(proposed_start) if proposed_start else None,
        "proposed_end": get_datetime(proposed_end) if proposed_end else None,
        "proposed_project": proposed_project or None,
        "proposed_task": proposed_task or None,
        "proposed_activity": proposed_activity or None,
        "reason": (reason or "").strip(),
        "status": "Requested",
        "requested_on": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_my_correction_requests(limit=20):
    """The session employee's requests, newest first."""
    employee = _require_session_employee()
    return frappe.get_all(
        "Time Correction Request",
        filters={"employee": employee},
        fields=TCR_LIST_FIELDS,
        order_by="requested_on desc, creation desc",
        limit=max(1, min(cint(limit) or 20, 100)),
    )


@frappe.whitelist()
def cancel_correction_request(name):
    """Withdraw one of the session employee's own requests while it is still
    ``Requested``. Anything already decided stays as decided."""
    employee = _require_session_employee()
    doc = frappe.get_doc("Time Correction Request", name)
    if doc.employee != employee:
        frappe.throw(_("You can only cancel your own requests."), frappe.PermissionError)
    if doc.status != "Requested":
        frappe.throw(_("This request has already been {0}.").format(doc.status.lower()))
    doc.flags.ee_review = True
    doc.status = "Canceled"
    doc.reviewed_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"status": doc.status}


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
    if lat in (None, "") or lng in (None, ""):
        return False
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
        frappe.log_error(f"Failed to log location: {e!s}", "Time Kiosk Location Error")
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


FIX_SOURCES = ("Watch", "Heartbeat", "Catch-up", "Anchor")


def _touch_interval_fixes(job_interval, count, latest):
    """Keep ``fix_count`` / ``last_fix_at`` live on an OPEN interval as batches
    land, without bumping ``modified`` (a save would race the clock actions).
    One statement per touched interval; ``greatest`` handles a catch-up batch
    that arrives after a newer heartbeat."""
    if not job_interval or not count:
        return
    frappe.db.sql(
        """
        update `tabJob Interval`
        set fix_count = coalesce(fix_count, 0) + %(count)s,
            last_fix_at = case
                when last_fix_at is null or last_fix_at < %(latest)s then %(latest)s
                else last_fix_at
            end
        where name = %(name)s
        """,
        {"count": cint(count), "latest": latest, "name": job_interval},
    )


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
          "fix_source": "Watch" | "Heartbeat" | "Catch-up" | "Anchor",
          "device_agent": <ua string>
        }

    Employee is taken from the session (never trusted from the client); each
    job_interval is verified to belong to that employee. Returns the list of
    accepted client_ids so the worker can clear exactly those from IndexedDB.

    A fix worse than ``min_accuracy_m`` is stored as ``Low Accuracy`` (and
    reported as accepted) when ``keep_low_accuracy_fixes`` is on — it still
    proves the phone was reporting — and rejected with ``low_accuracy`` when it
    is off. Invalid coordinates are always rejected. Each touched interval's
    ``fix_count`` / ``last_fix_at`` are updated in place so an open interval
    reports live.
    """
    employee = _resolve_employee()
    settings = get_settings()

    if isinstance(points, str):
        points = json.loads(points)
    if not isinstance(points, list):
        frappe.throw(_("'points' must be a list."))

    max_batch = cint(settings.get("max_batch_size")) or 50
    min_accuracy = cint(settings.get("min_accuracy_m"))
    keep_low_accuracy = bool(cint(settings.get("keep_low_accuracy_fixes")))
    points = points[:max_batch]

    interval_cache = {}
    accepted, rejected = [], []
    touched = {}
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
                    if not keep_low_accuracy:
                        rejected.append({"client_id": cid, "reason": "low_accuracy"})
                        continue
                    status = "Low Accuracy"

            source = p.get("fix_source") or "Watch"
            if source not in FIX_SOURCES:
                source = "Watch"
            interval = _validated_interval(p.get("job_interval"), employee, interval_cache)
            timestamp = _parse_timestamp(p.get("timestamp")) or now_iso

            doc = frappe.get_doc({
                "doctype": "Time Kiosk Log",
                "employee": employee,
                "user": user,
                "job_interval": interval,
                "timestamp": timestamp,
                "latitude": lat,
                "longitude": lng,
                "accuracy": p.get("accuracy"),
                "speed": p.get("speed"),
                "heading": p.get("heading"),
                "altitude": p.get("altitude"),
                "device_agent": p.get("device_agent"),
                "log_status": status,
                "fix_source": source,
            })
            doc.insert(ignore_permissions=True)
            accepted.append(cid)
            if interval and status in TRACKED_STATUSES:
                count, latest = touched.get(interval, (0, None))
                touched[interval] = (count + 1, timestamp if latest is None or timestamp > latest else latest)
        except Exception as e:
            frappe.log_error(f"Failed to ingest geo point: {e!s}", "Time Kiosk Location Error")
            rejected.append({"client_id": cid, "reason": "server_error"})

    for interval, (count, latest) in touched.items():
        try:
            _touch_interval_fixes(interval, count, latest)
        except Exception as e:
            frappe.log_error(f"Failed to update fix count on {interval}: {e!s}", "Time Kiosk Location Error")

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
        # The photo gate's configuration, as an unambiguous block rather than
        # leaving the client to pick four keys out of `settings`. A UX hint only:
        # api.time_kiosk.log_time re-reads the settings server-side on every call
        # and is what actually decides, so a device serving a stale cached bundle
        # cannot talk its way past the requirement.
        "photo_gate": photo_gate.bootstrap_payload(),
        "csrf_token": (frappe.session.data or {}).get("csrf_token"),
    }


# ---------------------------------------------------------------------------
# Manager views (Location Timeline page)
# ---------------------------------------------------------------------------

def _is_manager():
    return bool(TIMELINE_MANAGER_ROLES.intersection(frappe.get_roles()))


def _can_view_employee_logs(employee):
    """True if the session user may view ``employee``'s location history."""
    if _is_manager():
        return True
    return _session_employee() == employee


@frappe.whitelist()
def get_employees_for_timeline():
    """The employee picker: every active Employee for managers, only themselves
    for everyone else. ``[{value, label}]``."""
    if _is_manager():
        return [
            {"value": e.name, "label": e.employee_name or e.name}
            for e in frappe.get_all("Employee", filters={"status": "Active"},
                                    fields=["name", "employee_name"], order_by="employee_name asc")
        ]
    employee = _session_employee()
    if not employee:
        return []
    return [{"value": employee, "label": frappe.db.get_value("Employee", employee, "employee_name") or employee}]


@frappe.whitelist()
def get_live_positions():
    """Where everybody who is clocked in was last seen. Managers only.

    One row per Open/Paused interval with its latest real fix (``Success`` or
    ``Low Accuracy``); ``stale`` when that fix is older than the tracking-gap
    setting, which is also returned as ``stale_after_minutes`` so the page can
    say why.
    """
    if not _is_manager():
        frappe.throw(_("Not permitted to view live positions."), frappe.PermissionError)

    gap_minutes = _health_inputs()["gap_minutes"]
    now = now_datetime()

    intervals = frappe.db.sql(
        """
        select ji.name, ji.employee, e.employee_name, ji.project, p.project_name as project_title,
               t.subject as task_title, ji.status, ji.start_time, ji.total_paused_seconds,
               ji.last_pause_time, ji.last_fix_at, ji.tracking_health
        from `tabJob Interval` ji
        left join `tabEmployee` e on e.name = ji.employee
        left join `tabProject` p on p.name = ji.project
        left join `tabTask` t on t.name = ji.task
        where ji.status in ('Open', 'Paused')
        order by ji.start_time asc
        """,
        as_dict=True,
    )
    if not intervals:
        return {"employees": [], "stale_after_minutes": gap_minutes}

    names = tuple(i.name for i in intervals)
    latest = {}
    for row in frappe.db.sql(
        """
        select l.job_interval, l.timestamp, l.latitude, l.longitude, l.accuracy
        from `tabTime Kiosk Log` l
        inner join (
            select job_interval, max(timestamp) as ts
            from `tabTime Kiosk Log`
            where job_interval in %(names)s and log_status in %(statuses)s
            group by job_interval
        ) m on m.job_interval = l.job_interval and m.ts = l.timestamp
        where l.log_status in %(statuses)s
        order by l.timestamp desc
        """,
        {"names": names, "statuses": TRACKED_STATUSES},
        as_dict=True,
    ):
        latest.setdefault(row.job_interval, row)

    employees = []
    for i in intervals:
        fix = latest.get(i.name)
        last_fix_at = get_datetime(fix.timestamp) if fix else (get_datetime(i.last_fix_at) if i.last_fix_at else None)
        stale = last_fix_at is None or (now - last_fix_at) > timedelta(minutes=gap_minutes)
        employees.append({
            "employee": i.employee,
            "employee_name": i.employee_name or i.employee,
            "job_interval": i.name,
            "project": i.project,
            "project_title": i.project_title or i.project,
            "task_title": i.task_title,
            "status": i.status,
            "start_time": i.start_time,
            "elapsed_seconds": round(_worked_seconds(i, now)),
            "last_fix_at": last_fix_at,
            "latitude": fix.latitude if fix else None,
            "longitude": fix.longitude if fix else None,
            "accuracy": fix.accuracy if fix else None,
            "stale": bool(stale),
            "tracking_health": i.tracking_health,
        })
    return {"employees": employees, "stale_after_minutes": gap_minutes}


INTERVAL_META_FIELDS = [
    "name", "project", "task", "start_time", "end_time", "status", "total_paused_seconds",
    "last_pause_time", "latitude", "longitude", "start_accuracy", "end_latitude", "end_longitude",
    "end_accuracy", "site_latitude", "site_longitude", "site_radius_m", "site_source",
    "tracking_health", "tracking_coverage_pct", "gap_minutes", "fix_count",
    "auto_closed", "offsite_start", "offsite_end", "corrected",
]


def _history_window(from_datetime, to_datetime):
    if not to_datetime:
        to_datetime = now_datetime()
    if not from_datetime:
        from_datetime = add_days(get_datetime(to_datetime), -1)
    return get_datetime(from_datetime), get_datetime(to_datetime)


def _history_rows(employee, from_dt, to_dt):
    return frappe.get_all(
        "Time Kiosk Log",
        filters={
            "employee": employee,
            "log_status": ["in", list(TRACKED_STATUSES)],
            "timestamp": ["between", [from_dt, to_dt]],
        },
        fields=["name", "job_interval", "timestamp", "latitude", "longitude",
                "accuracy", "speed", "heading", "fix_source", "log_status"],
        order_by="timestamp asc",
    )


def _interval_meta(employee, names, from_dt, to_dt):
    """Job Interval rows for the grouped points PLUS every interval of the
    employee that started inside the window — an interval with no fixes at all
    (health ``None``) still has to appear on the timeline, or the day it
    describes reads as a day off."""
    filters = [["employee", "=", employee], ["start_time", "between", [from_dt, to_dt]]]
    rows = {iv.name: iv for iv in frappe.get_all("Job Interval", filters=filters, fields=INTERVAL_META_FIELDS)}
    missing = [n for n in names if n not in rows]
    if missing:
        for iv in frappe.get_all("Job Interval", filters={"name": ["in", missing]}, fields=INTERVAL_META_FIELDS):
            rows[iv.name] = iv
    return rows


def _anchor(lat, lng, accuracy):
    if not _valid_coords(lat, lng) or not (flt(lat) or flt(lng)):
        return None
    return {"lat": flt(lat), "lng": flt(lng), "accuracy": flt(accuracy) if accuracy not in (None, "") else None}


def _describe_group(meta, points, from_dt, to_dt, now, settings_inputs):
    """One interval's block for ``get_location_history``: site, anchors, stats,
    gaps, stops, badges. ``meta`` may be empty for the ``_unassigned`` group."""
    precise = [p for p in points if p.get("log_status") == PRECISE_STATUS]
    fix_times = [get_datetime(p["timestamp"]) for p in points]

    site = None
    if meta and (flt(meta.get("site_latitude")) or flt(meta.get("site_longitude"))):
        site = {
            "lat": flt(meta.get("site_latitude")),
            "lng": flt(meta.get("site_longitude")),
            "radius_m": cint(meta.get("site_radius_m")),
            "source": meta.get("site_source") or "",
        }

    if meta:
        start = get_datetime(meta.get("start_time"))
        end = get_datetime(meta.get("end_time")) if meta.get("end_time") else now
    elif fix_times:
        start, end = fix_times[0], fix_times[-1]
    else:
        start, end = from_dt, to_dt
    window_start, window_end = max(start, from_dt), min(end, to_dt)

    stored = bool(meta) and meta.get("end_time") and meta.get("tracking_health") not in (None, "", "Pending")
    if stored:
        health = {
            "coverage_pct": flt(meta.get("tracking_coverage_pct")),
            "gap_minutes": flt(meta.get("gap_minutes")),
            "health": meta.get("tracking_health"),
        }
    else:
        health = tracking_health.compute_health(start, end, fix_times, **settings_inputs)

    gap_list = tracking_health.gaps(window_start, window_end, fix_times, settings_inputs["gap_minutes"]) \
        if window_end > window_start else []
    stops = tracking_health.detect_stops(precise, STOP_RADIUS_M, STOP_MIN_MINUTES)
    for stop in stops:
        stop["at_site"] = bool(
            site and site["radius_m"] > 0
            and haversine_m(stop["lat"], stop["lng"], site["lat"], site["lng"]) <= site["radius_m"]
        )
    dwell = tracking_health.dwell_minutes(precise, site["lat"], site["lng"], site["radius_m"]) if site else 0.0
    travel = tracking_health.travel_minutes(
        precise, stops, site["lat"] if site else None, site["lng"] if site else None, site["radius_m"] if site else 0
    )

    return {
        "site": site,
        "anchors": {
            "start": _anchor(meta.get("latitude"), meta.get("longitude"), meta.get("start_accuracy")) if meta else None,
            "end": _anchor(meta.get("end_latitude"), meta.get("end_longitude"), meta.get("end_accuracy")) if meta else None,
        },
        "stats": {
            "fix_count": len(points),
            "distance_m": round(tracking_health.path_distance_m(precise)),
            "gap_minutes": flt(health["gap_minutes"]),
            "coverage_pct": flt(health["coverage_pct"]),
            "health": health["health"],
            "dwell_minutes": dwell,
            "travel_minutes": travel,
        },
        "gaps": gap_list,
        "stops": stops,
        "auto_closed": cint(meta.get("auto_closed")) if meta else 0,
        "offsite_start": cint(meta.get("offsite_start")) if meta else 0,
        "offsite_end": cint(meta.get("offsite_end")) if meta else 0,
        "corrected": cint(meta.get("corrected")) if meta else 0,
        "worked_seconds": round(_worked_seconds(meta, now)) if meta else 0,
    }


@frappe.whitelist()
def get_location_history(employee, from_datetime=None, to_datetime=None):
    """
    Return location points for ``employee`` between the two datetimes, grouped by
    Job Interval (the clock-in session), ordered oldest-first.

    Powers the manager "Location Timeline" page. Permission: manager roles can
    view anyone; everyone else only themselves.

    Points include ``Low Accuracy`` rows (with ``fix_source`` / ``log_status`` so
    the page can draw them hollow). Each interval group carries the resolved
    ``site``, the two ``anchors``, ``stats`` (fixes, distance, gaps, coverage,
    health, dwell, travel), the ``gaps`` and ``stops`` lists and the
    auto-closed / off-site / corrected badges; the top level adds ``day_totals``.
    Intervals with no fixes still appear, with health ``None``.
    """
    if not employee:
        frappe.throw(_("Employee is required."))
    if not _can_view_employee_logs(employee):
        frappe.throw(_("Not permitted to view this employee's location history."),
                     frappe.PermissionError)

    from_dt, to_dt = _history_window(from_datetime, to_datetime)
    now = now_datetime()
    settings_inputs = _health_inputs()

    rows = _history_rows(employee, from_dt, to_dt)

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
            "fix_source": r.fix_source,
            "log_status": r.log_status,
        })

    interval_meta = _interval_meta(employee, [k for k in order if k != "_unassigned"], from_dt, to_dt)
    for name in interval_meta:
        if name not in groups:
            groups[name] = []
            order.append(name)

    def sort_key(key):
        meta = interval_meta.get(key)
        if meta:
            return (0, get_datetime(meta.start_time))
        first = groups[key][0]["timestamp"] if groups[key] else to_dt
        return (1, get_datetime(first))

    order.sort(key=sort_key)

    project_titles = _titles("Project", [m.get("project") for m in interval_meta.values()], "project_name")
    task_titles = _titles("Task", [m.get("task") for m in interval_meta.values()], "subject")

    result = []
    totals = {"worked_seconds": 0, "distance_m": 0, "dwell_minutes": 0.0, "travel_minutes": 0.0, "gap_minutes": 0.0}
    for key in order:
        meta = interval_meta.get(key, {})
        project = meta.get("project") if meta else None
        task = meta.get("task") if meta else None
        block = _describe_group(meta, groups[key], from_dt, to_dt, now, settings_inputs)
        worked = block.pop("worked_seconds")
        if meta:
            totals["worked_seconds"] += worked
            totals["distance_m"] += block["stats"]["distance_m"]
            totals["dwell_minutes"] += block["stats"]["dwell_minutes"]
            totals["travel_minutes"] += block["stats"]["travel_minutes"]
            totals["gap_minutes"] += block["stats"]["gap_minutes"]
        entry = {
            "job_interval": None if key == "_unassigned" else key,
            "project": project,
            "project_title": project_titles.get(project) if project else None,
            "task": task,
            "task_title": task_titles.get(task) if task else None,
            "start_time": meta.get("start_time") if meta else None,
            "end_time": meta.get("end_time") if meta else None,
            "status": meta.get("status") if meta else None,
            "worked_seconds": worked,
            "points": groups[key],
        }
        entry.update(block)
        result.append(entry)

    totals["dwell_minutes"] = round(totals["dwell_minutes"], 1)
    totals["travel_minutes"] = round(totals["travel_minutes"], 1)
    totals["gap_minutes"] = round(totals["gap_minutes"], 1)

    return {
        "employee": employee,
        "from_datetime": from_dt,
        "to_datetime": to_dt,
        "intervals": result,
        "point_count": len(rows),
        "day_totals": totals,
    }


def _utc_iso(value):
    """A site-local naive datetime as an ISO-8601 UTC string, for GPX."""
    dt = get_datetime(value)
    try:
        from datetime import timezone
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(frappe.utils.get_system_timezone())
        return dt.replace(tzinfo=zone).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")


def history_csv(rows, project_by_interval):
    """The CSV body: one line per fix, the columns the contract names."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["timestamp", "latitude", "longitude", "accuracy", "speed", "heading",
                     "fix_source", "log_status", "job_interval", "project"])
    for r in rows:
        writer.writerow([
            r.get("timestamp"), r.get("latitude"), r.get("longitude"), r.get("accuracy"),
            r.get("speed"), r.get("heading"), r.get("fix_source") or "", r.get("log_status") or "",
            r.get("job_interval") or "", project_by_interval.get(r.get("job_interval")) or "",
        ])
    return out.getvalue()


def history_gpx(rows, project_by_interval, employee):
    """A GPX 1.1 document: one ``<trk>`` per interval (unassigned fixes form their
    own track), precise fixes only — a 300 m fix in a GPX viewer is a lie."""
    tracks = {}
    order = []
    for r in rows:
        if r.get("log_status") != PRECISE_STATUS or not _valid_coords(r.get("latitude"), r.get("longitude")):
            continue
        key = r.get("job_interval") or "_unassigned"
        if key not in tracks:
            tracks[key] = []
            order.append(key)
        tracks[key].append(r)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Sapphire Fountains ERPNext" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <metadata><name>{_xml_escape(str(employee))}</name></metadata>",
    ]
    for key in order:
        label = key if key != "_unassigned" else "Unassigned"
        project = project_by_interval.get(key)
        if project:
            label = f"{label} - {project}"
        lines.append("  <trk>")
        lines.append(f"    <name>{_xml_escape(label)}</name>")
        lines.append("    <trkseg>")
        for r in tracks[key]:
            lines.append(f'      <trkpt lat="{flt(r.get("latitude")):.6f}" lon="{flt(r.get("longitude")):.6f}">')
            lines.append(f"        <time>{_utc_iso(r.get('timestamp'))}</time>")
            lines.append("      </trkpt>")
        lines.append("    </trkseg>")
        lines.append("  </trk>")
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"


@frappe.whitelist()
def export_location_history(employee, from_datetime=None, to_datetime=None, format="csv"):
    """Download an employee's fixes for a window as ``csv`` or ``gpx``. Same gate
    as ``get_location_history``. Sets ``frappe.response`` like
    ``download_payroll_workbook`` does, with the right MIME type."""
    if not employee:
        frappe.throw(_("Employee is required."))
    if not _can_view_employee_logs(employee):
        frappe.throw(_("Not permitted to export this employee's location history."),
                     frappe.PermissionError)
    fmt = (format or "csv").lower()
    if fmt not in ("csv", "gpx"):
        frappe.throw(_("Format must be csv or gpx."))

    from_dt, to_dt = _history_window(from_datetime, to_datetime)
    rows = _history_rows(employee, from_dt, to_dt)
    names = sorted({r.job_interval for r in rows if r.job_interval})
    project_by_interval = {}
    if names:
        for iv in frappe.get_all("Job Interval", filters={"name": ["in", names]}, fields=["name", "project"]):
            project_by_interval[iv.name] = iv.project
        titles = _titles("Project", project_by_interval.values(), "project_name")
        project_by_interval = {k: (titles.get(v) or v) for k, v in project_by_interval.items()}

    stamp = f"{getdate(from_dt)}_to_{getdate(to_dt)}"
    safe_employee = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(employee))
    if fmt == "csv":
        content, content_type = history_csv(rows, project_by_interval), "text/csv"
    else:
        content, content_type = history_gpx(rows, project_by_interval, employee), "application/gpx+xml"

    frappe.response["filename"] = f"location_{safe_employee}_{stamp}.{fmt}"
    frappe.response["filecontent"] = content
    frappe.response["content_type"] = content_type
    frappe.response["type"] = "download"


def purge_old_location_logs():
    """Scheduled daily: delete Time Kiosk Log rows older than the configured
    retention window. retention_days <= 0 disables purging (keep forever — the
    default since v1.480.0)."""
    days = cint(get_settings().get("retention_days"))
    if days <= 0:
        return
    cutoff = add_days(now_datetime(), -days)
    frappe.db.delete("Time Kiosk Log", {"timestamp": ["<", cutoff]})
    frappe.db.commit()


@frappe.whitelist()
def start_backdated(project=None, start_time=None, reason=None, task=None, time_category=None, description=None):
    user = frappe.session.user
    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
    if not employee:
        frappe.throw(_("No Employee record found for this user."), frappe.PermissionError)

    if not project:
        frappe.throw(_("Project is required."))
    if not start_time:
        frappe.throw(_("Start time is required."))
    if not reason or not reason.strip():
        frappe.throw(_("Reason is required."))

    start_dt = get_datetime(start_time)
    now_dt = now_datetime()
    
    if start_dt.date() != now_dt.date():
        frappe.throw(_("Backdating is only allowed for today's date."))
    if start_dt >= now_dt:
        frappe.throw(_("Start time must be strictly before now."))

    resolved_time_category = time_category or frappe.db.get_single_value("Time Kiosk Settings", "default_time_category")
    if not resolved_time_category:
        frappe.throw(_("Activity type is required. Please pick one."))

    # lock and check existing
    frappe.db.get_value("Employee", employee, "name", for_update=True)
    existing = frappe.db.get_value(
        "Job Interval",
        {"employee": employee, "status": ["in", ["Open", "Paused"]]},
        "name",
        for_update=True,
    )
    if existing:
        frappe.throw(_("You already have an active job interval. Please stop or switch it first."))

    doc = _new_interval(employee, project, task, resolved_time_category, description, start_dt,
                        lat=None, lng=None, accuracy=None, offsite_acknowledged=0)
    doc.manual_start = 1
    doc.manual_start_reason = reason.strip()
    doc.insert(ignore_permissions=True)
    
    return {"status": "success", "message": "Backdated work started.", "doc": doc.name}

@frappe.whitelist()
def update_interval_times(interval=None, start_time=None, end_time=None, reason=None):
    if not interval:
        frappe.throw(_("Interval is required."))
    if not reason or not reason.strip():
        frappe.throw(_("Reason is required."))

    doc = frappe.get_doc("Job Interval", interval)
    
    # Permission check
    user = frappe.session.user
    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
    user_roles = frappe.get_roles(user)
    
    is_manager = bool(set(TIMELINE_MANAGER_ROLES).intersection(user_roles))
    if doc.employee != employee and not is_manager and user != "Administrator":
        frappe.throw(_("Not permitted to edit this interval."), frappe.PermissionError)

    # Hard field allowlist
    if start_time:
        doc.start_time = get_datetime(start_time)
    if end_time:
        doc.end_time = get_datetime(end_time)
    
    # Check if manual_start_reason or similar exists. job_interval.json has manual_start_reason.
    # What about edit reason? Let's write it to a comment if there's no edit field, or time_correction_reason if it exists.
    # We will just write it to manual_start_reason for now, or append to it. 
    # Let's check Job Interval fields via getattr. 
    doc.db_set("manual_start", 1)
    doc.db_set("manual_start_reason", reason.strip())

    doc.save(ignore_permissions=True)
    resync_interval_timesheet(doc)
    
    return {"status": "success"}

@frappe.whitelist()
def approve_day(employee=None, date=None):
    from erpnext_enhancements.workforce.approval import approve_day as _approve_day
    return _approve_day(employee, date)

@frappe.whitelist()
def reopen_day(employee=None, date=None, reason=None):
    from erpnext_enhancements.workforce.approval import reopen_day as _reopen_day
    return _reopen_day(employee, date, reason)

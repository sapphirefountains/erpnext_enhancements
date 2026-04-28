import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime, flt
from datetime import timedelta

@frappe.whitelist()
def log_time(project=None, action=None, lat=None, lng=None, description=None, task=None, time_category="On-Site Labor"):
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
            "activity_type": "Execution",
            "from_time": start_time,
            "to_time": end_time,
            "description": interval_doc.description or "Synced from Job Interval"
        }

        if timesheet_name:
            ts_doc = frappe.get_doc("Timesheet", timesheet_name)

            # Simple idempotency check: Check if an identical log exists
            # We compare project and start time
            exists = False
            for row in ts_doc.time_logs:
                # Compare basic fields
                row_start = get_datetime(row.from_time)
                if row.project == project and abs(row.hours - hours) < 0.01 and row_start == start_time:
                    exists = True
                    break

            if not exists:
                ts_doc.append("time_logs", new_log)
                ts_doc.save(ignore_permissions=True)

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
def log_geolocation(employee, latitude, longitude, device_agent, log_status, timestamp):
    """
    Logs a geolocation entry to the Time Kiosk Log.
    """
    try:
        if not employee:
             frappe.throw(_("Employee ID is required for logging location."))

        doc = frappe.get_doc({
            "doctype": "Time Kiosk Log",
            "employee": employee,
            "user": frappe.session.user,
            "timestamp": timestamp,
            "latitude": latitude,
            "longitude": longitude,
            "device_agent": device_agent,
            "log_status": log_status
        })
        doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Location logged."}
    except Exception as e:
        frappe.log_error(f"Failed to log location: {str(e)}", "Time Kiosk Location Error")
        return {"status": "error", "message": str(e)}

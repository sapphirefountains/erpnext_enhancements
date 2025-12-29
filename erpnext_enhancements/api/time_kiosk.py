import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime
from datetime import timedelta

@frappe.whitelist()
def log_time(project, action, lat=None, lng=None, description=None):
    """
    Logs time for the current employee.
    action: "Start" or "Stop"
    """
    user = frappe.session.user
    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")

    if not employee:
        frappe.throw(_("No Employee record found for this user ({0}).").format(user), frappe.PermissionError)

    if action == "Start":
        if not project:
            frappe.throw(_("Project is required to start work."))

        # Check for existing open interval
        existing = frappe.db.exists("Job Interval", {
            "employee": employee,
            "status": "Open"
        })
        if existing:
            frappe.throw(_("You already have an open job interval. Please stop it first."))

        # Create new interval
        doc = frappe.get_doc({
            "doctype": "Job Interval",
            "employee": employee,
            "project": project,
            "start_time": now_datetime(),
            "status": "Open",
            "latitude": lat,
            "longitude": lng,
            "description": description
        })
        doc.insert(ignore_permissions=True)
        return {"status": "success", "message": "Work started.", "doc": doc.name}

    elif action == "Stop":
        # Find open interval
        open_interval = frappe.db.get_value("Job Interval", {
            "employee": employee,
            "status": "Open"
        }, "name")

        if not open_interval:
            frappe.throw(_("No open job found to stop."))

        doc = frappe.get_doc("Job Interval", open_interval)
        doc.end_time = now_datetime()
        doc.status = "Completed"
        doc.save(ignore_permissions=True)

        # Sync to Timesheet
        sync_interval_to_timesheet(doc)

        return {"status": "success", "message": "Work stopped.", "doc": doc.name}

    else:
        frappe.throw(_("Invalid action. Must be 'Start' or 'Stop'."))

def sync_interval_to_timesheet(interval_doc):
    """
    Syncs a completed Job Interval to a Timesheet.
    """
    try:
        employee = interval_doc.employee
        project = interval_doc.project

        # RESCUE MECHANISM: If project is missing (e.g. bad data), allow stop but skip sync
        if not project:
            interval_doc.db_set("sync_status", "Skipped - No Project")
            return

        start_time = get_datetime(interval_doc.start_time)
        end_time = get_datetime(interval_doc.end_time)

        # Calculate hours
        duration_seconds = (end_time - start_time).total_seconds()
        hours = duration_seconds / 3600.0

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
    Returns the current open interval for the logged in user.
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

    # Get open interval
    interval = frappe.db.get_value("Job Interval", {
        "employee": employee,
        "status": "Open"
    }, ["name", "project", "start_time", "description"], as_dict=True)

    if interval:
        # Use dictionary access for compatibility with plain dicts (in case of custom queries/mocks)
        project_title = frappe.db.get_value("Project", interval.get("project"), "project_name")
        interval["project_title"] = project_title or interval.get("project")

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

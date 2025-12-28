import frappe
from frappe import _
from frappe.utils import now_datetime

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
        # We generally don't update description on stop unless provided? Prompt didn't specify.
        # But we don't update lat/lng on stop as per plan decision.
        doc.save(ignore_permissions=True)
        return {"status": "success", "message": "Work stopped.", "doc": doc.name}

    else:
        frappe.throw(_("Invalid action. Must be 'Start' or 'Stop'."))

@frappe.whitelist()
def get_current_status():
    """
    Returns the current open interval for the logged in user.
    """
    user = frappe.session.user
    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")

    if not employee:
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
        return interval

    return None

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

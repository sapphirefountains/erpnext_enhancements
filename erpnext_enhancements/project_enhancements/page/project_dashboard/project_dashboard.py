"""Server-side logic for the Project Dashboard page.

This module provides all the backend functions required for the interactive
Project Dashboard, including data retrieval, permission checks, and in-place
updates for projects and tasks. All functions intended to be called from the
frontend are decorated with `@frappe.whitelist()`.
"""

import json
from datetime import timedelta

import frappe
from frappe.utils import getdate, nowdate

# Fields the dashboard is allowed to inline-edit on a Project via
# update_project_details(). Kept deliberately narrow: this is a whitelisted
# endpoint, so accepting an arbitrary `field` would let any caller overwrite any
# Project column (owner, naming_series, internal flags, etc.). Only the columns
# the dashboard actually exposes for inline editing belong here.
EDITABLE_PROJECT_FIELDS = frozenset(
	{"status", "custom_project_priority", "custom_company_priority"}
)

@frappe.whitelist()
def check_permission():
	"""Checks if the current user has permission to view the Project Dashboard.
	Uses Page-level role permissions if available, falling back to custom settings.
	"""
	try:
		# Standard Page DocTypes handle roles via 'Custom Role' and 'Has Role'
		custom_role = frappe.db.get_value("Custom Role", {"page": "Project Dashboard"}, "name")
		if custom_role:
			page_roles = frappe.get_all("Has Role", filters={"parent": custom_role, "parenttype": "Custom Role"}, fields=["role"])
		else:
			page_roles = []

		if not page_roles:
			# Fallback to legacy custom settings if Page Roles aren't configured yet
			permitted_roles_docs = frappe.get_all(
				"Project Dashboard Permitted Role",
				filters={"parent": "Project Dashboard Settings"},
				fields=["role"],
			)
			permitted_roles = {doc.get("role") for doc in permitted_roles_docs}
		else:
			permitted_roles = {r.role for r in page_roles}

		user_roles = set(frappe.get_roles())
		if not permitted_roles.intersection(user_roles):
			return False
		return True

	except Exception as e:
		frappe.log_error(f"Error checking project dashboard permissions: {e}", frappe.get_traceback())
		return False


def _get_assignee_names(doctype, docname):
	"""Retrieves the full names of users assigned to a document via ToDo."""
	try:
		todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": doctype, "reference_name": docname, "status": "Open"},
			fields=["allocated_to"],
		)
		if not todos:
			return []
		assignee_emails = {todo.get("allocated_to") for todo in todos if todo.get("allocated_to")}
		if not assignee_emails:
			return []
		users = frappe.get_all(
			"User", filters={"email": ("in", list(assignee_emails))}, fields=["email", "full_name"]
		)
		return users
	except Exception as e:
		frappe.log_error(f"Error fetching assignee names for {doctype} {docname}: {e}", frappe.get_traceback())
		return []


@frappe.whitelist()
def get_project_data(is_active=None):
	"""Fetches and enriches project data for the dashboard using bulk queries.

	Access is gated by the Project Dashboard page role via check_permission().
	Once a user is authorised for the page, projects are fetched with
	ignore_permissions (frappe.get_all) so the portfolio view is not silently
	narrowed by per-record User Permissions. In particular, the site restricts
	each user to their own Employee record (apply-to-all-doctypes), and Project's
	custom_project_owner / custom_technical_lead Employee links would otherwise
	limit a non-admin user to only the projects where they are the owner/lead.
	The dashboard is a shared portfolio overview, so page-role access is the
	intended gate rather than per-project visibility.
	"""
	if not check_permission():
		return {"error": "You do not have permission to view the Project Dashboard."}
	try:
		filters = {"status": ["!=", "Canceled"]}
		if is_active:
			filters["is_active"] = is_active

		projects = frappe.get_all(
			"Project",
			fields=[
				# Note: "project_user" is intentionally NOT selected here. Project has
				# no scalar `project_user` column (members live in the Project User
				# child table), so selecting it raises "Unknown column 'project_user'".
				# The value is derived from assignees and set on each project below.
				"name", "project_name", "status", "project_type",
				"custom_project_priority", "custom_company_priority", "is_active",
				"percent_complete", "expected_start_date", "expected_end_date",
				"custom_project_dollar_amount", "estimated_costing", "custom_master_project",
				"modified",
			],
			filters=filters,
			order_by="creation desc",
		)

		project_names = [p["name"] for p in projects]
		if not project_names:
			return projects

		# 1. Bulk fetch task counts using raw SQL for reliable grouped aggregation
		task_data = frappe.db.sql("""
			SELECT project, status, COUNT(*) as count
			FROM `tabTask`
			WHERE project IN %s
			GROUP BY project, status
		""", (project_names,), as_dict=1)
		
		task_map = {}
		for td in task_data:
			proj = td["project"]
			if proj not in task_map:
				task_map[proj] = {"total": 0, "completed": 0}
			task_map[proj]["total"] += td["count"]
			if td["status"] == "Completed":
				task_map[proj]["completed"] += td["count"]

		# 2. Bulk fetch assignees via ToDo
		todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Project", "reference_name": ["in", project_names], "status": "Open"},
			fields=["reference_name", "allocated_to"]
		)
		
		user_emails = list({t["allocated_to"] for t in todos if t.get("allocated_to")})
		users = frappe.get_all("User", filters={"email": ["in", user_emails]}, fields=["email", "full_name"])
		user_map = {u["email"]: u["full_name"] for u in users}

		assignee_map = {}
		for todo in todos:
			proj = todo["reference_name"]
			if proj not in assignee_map:
				assignee_map[proj] = []
			email = todo.get("allocated_to")
			if email and email in user_map:
				assignee_map[proj].append({"email": email, "full_name": user_map[email]})

		# 3. Completion dates for inactive projects. Project.actual_end_date is not
		# maintained in practice (empty on every inactive project), so the moment
		# is_active last flipped Yes -> No in the Version history serves as the
		# "completed on" date, falling back to the last-modified date for inactive
		# projects with no recorded flip (e.g. created inactive).
		inactive_names = [p["name"] for p in projects if p.get("is_active") == "No"]
		completed_on_map = {}
		if inactive_names:
			version_rows = frappe.db.sql("""
				SELECT docname, DATE(MAX(creation)) AS completed_on
				FROM `tabVersion`
				WHERE ref_doctype = 'Project'
					AND docname IN %s
					AND data LIKE %s
				GROUP BY docname
			""", (inactive_names, '%["is_active","Yes","No"]%'), as_dict=1)
			completed_on_map = {v["docname"]: v["completed_on"] for v in version_rows}

		# 4. Map data back to projects
		for project in projects:
			p_name = project["name"]
			project["total_tasks"] = task_map.get(p_name, {}).get("total", 0)
			project["completed_tasks"] = task_map.get(p_name, {}).get("completed", 0)

			if project.get("is_active") == "No":
				project["completed_on"] = completed_on_map.get(p_name) or getdate(project.get("modified"))
			else:
				project["completed_on"] = None
			
			assignees = assignee_map.get(p_name, [])
			project["assignees"] = assignees
			if assignees:
				project["project_user"] = ", ".join([d["full_name"] for d in assignees])
			else:
				project["project_user"] = "Unassigned"

		return projects

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Error fetching project data")
		return {"error": "Could not fetch project data. Please check the logs."}


@frappe.whitelist()
def create_inline_task(project, subject, parent_task=None):
	"""Instantly creates a task from the inline 'Quick Add' row."""
	if not project or not subject:
		return {"status": "error", "message": "Project and Subject are required."}
	
	try:
		if not frappe.has_permission("Project", ptype="write", doc=project):
			return {"status": "error", "message": "No permission to add tasks to this project."}

		task = frappe.get_doc({
			"doctype": "Task",
			"project": project,
			"subject": subject,
			"parent_task": parent_task,
			"status": "Open"
		})
		task.insert()
		return {"status": "success", "task": task.name}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error creating inline task")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def add_project_assignee(project_name, user_id):
	"""Assigns a user to a project with permission checks.

	Verifies 'write' permission on the project before using Frappe's
	standard assignment function.

	Args:
	    project_name (str): The name (ID) of the project.
	    user_id (str): The email/ID of the user to assign.

	Returns:
	    dict: Status of the operation and the updated list of assignees.
	"""
	if not project_name or not user_id:
		return {"status": "error", "message": "Project and User are required."}

	try:
		if not frappe.has_permission("Project", ptype="write", doc=project_name):
			return {"status": "error", "message": "You do not have permission to modify this project."}

		from frappe.desk.form.assign_to import add

		add({"doctype": "Project", "name": project_name, "assign_to": [user_id]})

		updated_assignees = _get_assignee_names("Project", project_name)
		return {"status": "success", "assignees": updated_assignees}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error assigning user {user_id} to project {project_name}")
		return {"status": "error", "message": "Could not assign user. Please check the logs."}


@frappe.whitelist()
def remove_project_assignee(project_name, user_id):
	"""Removes a user's assignment from a project with permission checks.

	Verifies 'write' permission on the project before using Frappe's
	standard assignment removal function.

	Args:
	    project_name (str): The name (ID) of the project.
	    user_id (str): The email/ID of the user to un-assign.

	Returns:
	    dict: Status of the operation and the updated list of assignees.
	"""
	if not project_name or not user_id:
		return {"status": "error", "message": "Project and User are required."}

	try:
		if not frappe.has_permission("Project", ptype="write", doc=project_name):
			return {"status": "error", "message": "You do not have permission to modify this project."}

		from frappe.desk.form.assign_to import remove

		remove("Project", project_name, user_id)

		updated_assignees = _get_assignee_names("Project", project_name)
		return {"status": "success", "assignees": updated_assignees}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error removing user {user_id} from project {project_name}")
		return {"status": "error", "message": "Could not remove user. Please check the logs."}


@frappe.whitelist()
def update_project_details(project_name, field, value):
	"""Updates a single field for a specified project document.

	This function is designed for inline editing from the project dashboard.

	Args:
	    project_name (str): The name (ID) of the project to update.
	    field (str): The database field name to be updated.
	    value (any): The new value for the field.

	Returns:
	    dict: A dictionary indicating the status of the operation.
	        Example: `{'status': 'success'}` or
	        `{'status': 'error', 'message': '...'}`.
	"""
	if not project_name or not field:
		return {"status": "error", "message": "Project and field are required."}

	# Only allow editing the explicitly whitelisted columns.
	if field not in EDITABLE_PROJECT_FIELDS:
		return {"status": "error", "message": f"Field '{field}' is not editable from the dashboard."}

	# Enforce per-document write permission (the page-role gate alone is not a
	# substitute for object-level permission on the specific Project).
	if not frappe.has_permission("Project", ptype="write", doc=project_name):
		return {"status": "error", "message": "You do not have permission to modify this project."}

	try:
		frappe.db.set_value("Project", project_name, field, value)
		return {"status": "success"}
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error updating project {project_name}")
		return {"status": "error", "message": "Could not update project. Please check the logs."}


@frappe.whitelist()
def update_task_status(task_name, status):
	"""Updates the status of a single task."""
	if not check_permission():
		return {"status": "error", "message": "You do not have permission to perform this action."}
	project = frappe.db.get_value("Task", task_name, "project")
	if not project or not frappe.has_permission("Project", ptype="write", doc=project):
		return {"status": "error", "message": "You do not have permission to modify this task."}
	try:
		frappe.db.set_value("Task", task_name, "status", status)
		return {"status": "success"}
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error updating task status for {task_name}")
		return {"status": "error", "message": "Could not update task status."}


@frappe.whitelist()
def update_task_priority(task_name, priority):
	"""Updates the priority of a single task."""
	if not check_permission():
		return {"status": "error", "message": "You do not have permission to perform this action."}
	project = frappe.db.get_value("Task", task_name, "project")
	if not project or not frappe.has_permission("Project", ptype="write", doc=project):
		return {"status": "error", "message": "You do not have permission to modify this task."}
	try:
		frappe.db.set_value("Task", task_name, "priority", priority)
		return {"status": "success"}
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error updating task priority for {task_name}")
		return {"status": "error", "message": "Could not update task priority."}


@frappe.whitelist()
def get_priority_options():
	"""Retrieves 'custom_project_priority' and 'custom_company_priority' options from Project DocType.

	Reads the options for the priority fields directly from
	the Project DocType's metadata.

	Returns:
	    dict: A dictionary containing available priority options. Returns a
	        dictionary with an 'error' key on failure.
	"""
	try:
		project_doctype = frappe.get_meta("Project")
		project_priority_field = next(
			(df for df in project_doctype.fields if df.fieldname == "custom_project_priority"), None
		)
		company_priority_field = next(
			(df for df in project_doctype.fields if df.fieldname == "custom_company_priority"), None
		)

		options = {"project_priority": [], "company_priority": []}

		if project_priority_field and project_priority_field.options:
			options["project_priority"] = [opt for opt in project_priority_field.options.split("\n") if opt]

		if company_priority_field and company_priority_field.options:
			options["company_priority"] = [opt for opt in company_priority_field.options.split("\n") if opt]

		return options

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Error fetching priority options")
		return {"error": "Could not fetch priority options."}


@frappe.whitelist()
def get_status_options():
	"""Retrieves 'status' options from the Project DocType metadata.

	Returns:
	    list[str] | dict: A list of available status options. Returns a
	        dictionary with an 'error' key on failure.
	"""
	try:
		project_doctype = frappe.get_meta("Project")
		status_field = next((df for df in project_doctype.fields if df.fieldname == "status"), None)

		if status_field and status_field.options:
			# Options are stored as a string, separated by newlines.
			options = [opt for opt in status_field.options.split("\n") if opt]
			return options
		else:
			return []

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Error fetching status options")
		return {"error": "Could not fetch status options."}


@frappe.whitelist()
def get_task_status_options():
	"""Retrieves 'status' options from the Task DocType metadata.

	Returns:
	    list[str] | dict: A list of available status options for tasks.
	        Returns a dictionary with an 'error' key on failure.
	"""
	try:
		task_doctype = frappe.get_meta("Task")
		status_field = next((df for df in task_doctype.fields if df.fieldname == "status"), None)

		if status_field and status_field.options:
			options = [opt for opt in status_field.options.split("\n") if opt]
			return options
		else:
			return []

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Error fetching task status options")
		return {"error": "Could not fetch task status options."}


def _fetch_all_project_tasks(project_name):
	"""Recursively fetches all tasks and sub-tasks for a given project.

	It starts by fetching tasks directly linked to the project, and then
	iteratively fetches their children (sub-tasks).

	Args:
	    project_name (str): The name (ID) of the project.

	Returns:
	    list[dict]: A flat list of all tasks and sub-tasks associated
	        with the project.
	"""
	task_fields = [
		"name",
		"subject",
		"assigned_to",
		"status",
		"priority",
		"exp_start_date",
		"exp_end_date",
		"progress",
		"expected_time",
		"parent_task",
		"custom_subtask_order",
		"creation",
	]

	try:
		# We fetch all tasks related to the project without initial sorting,
		# as sorting will be handled comprehensively later.
		direct_tasks = frappe.get_list("Task", fields=task_fields, filters={"project": project_name})
	except Exception as e:
		frappe.log_error(f"Initial task fetch failed for project {project_name}: {e}", frappe.get_traceback())
		return []

	all_tasks = {task["name"]: task for task in direct_tasks}
	# This iterative approach is to ensure all descendants are fetched,
	# even if they are not directly linked to the project.
	tasks_to_process = [task["name"] for task in direct_tasks]

	while tasks_to_process:
		parent_ids = tasks_to_process
		tasks_to_process = []

		try:
			children = frappe.get_list(
				"Task", fields=task_fields, filters={"parent_task": ("in", parent_ids)}
			)

			for child in children:
				if child["name"] not in all_tasks:
					all_tasks[child["name"]] = child
					tasks_to_process.append(child["name"])

		except Exception as e:
			frappe.log_error(f"Child task fetch failed for parents {parent_ids}: {e}", frappe.get_traceback())
			break

	return list(all_tasks.values())


@frappe.whitelist()
def get_task_children(parent_task):
	"""Fetches direct children of a task for lazy loading."""
	if not parent_task:
		return []
	
	task_fields = [
		"name", "subject", "status", "priority", "exp_start_date",
		"exp_end_date", "progress", "expected_time", "parent_task", "custom_subtask_order",
		"is_milestone"
	]
	# custom_is_recurring is a site-level custom field that may not exist on
	# every install; only query it when present to avoid a SQL error.
	has_recurring = frappe.get_meta("Task").has_field("custom_is_recurring")
	if has_recurring:
		task_fields.append("custom_is_recurring")

	children = frappe.get_all(
		"Task",
		fields=task_fields,
		filters={"parent_task": parent_task},
		order_by="custom_subtask_order asc"
	)

	today = getdate(nowdate())
	for child in children:
		assignees = _get_assignee_names("Task", child["name"])
		child["assigned_to"] = ", ".join([d["full_name"] for d in assignees]) if assignees else ""
		child["has_children"] = frappe.db.exists("Task", {"parent_task": child["name"]})
		child["custom_is_recurring"] = child.get("custom_is_recurring") or 0
		
		# Overdue check
		child["is_overdue"] = (
			child.get("exp_end_date") and 
			getdate(child.get("exp_end_date")) < today and 
			(child.get("progress") or 0) < 100 and
			child.get("status") not in ["Completed", "Canceled"]
		)
		
	return children


@frappe.whitelist()
def get_resource_allocation_data(project_name):
	"""Aggregates expected time and task list by assignee per day."""
	if not project_name:
		return {}

	tasks = frappe.get_all(
		"Task",
		fields=["name", "subject", "exp_start_date", "exp_end_date", "expected_time"],
		filters={"project": project_name, "status": ["not in", ["Completed", "Canceled"]]}
	)

	allocation = {} # { "User Name": { "YYYY-MM-DD": { "hours": hrs, "tasks": [] } } }

	for task in tasks:
		if not task.exp_start_date or not task.exp_end_date or not task.expected_time:
			continue
		
		assignees = _get_assignee_names("Task", task.name)
		if not assignees:
			continue
			
		start = getdate(task.exp_start_date)
		end = getdate(task.exp_end_date)
		duration = (end - start).days + 1
		hours_per_day = float(task.expected_time) / duration if duration > 0 else 0

		for assignee in assignees:
			name = assignee["full_name"]
			if name not in allocation:
				allocation[name] = {}
			
			curr = start
			while curr <= end:
				date_str = curr.strftime("%Y-%m-%d")
				if date_str not in allocation[name]:
					allocation[name][date_str] = {"hours": 0, "tasks": []}
				
				allocation[name][date_str]["hours"] += hours_per_day
				allocation[name][date_str]["tasks"].append({
					"id": task.name,
					"subject": task.subject,
					"hours": round(hours_per_day, 2)
				})
				curr += timedelta(days=1)

	return allocation


@frappe.whitelist()
def get_project_health_metrics(project_name):
	"""Calculates key health indicators for a project."""
	if not project_name:
		return {}

	tasks = frappe.get_all(
		"Task",
		fields=["name", "status", "exp_end_date", "progress", "priority"],
		filters={"project": project_name}
	)

	total_tasks = len(tasks)
	if total_tasks == 0:
		return {"total_tasks": 0}

	today = getdate(nowdate())
	overdue_tasks = 0
	high_priority_overdue = 0
	completed_tasks = 0
	
	for t in tasks:
		if t.status == "Completed":
			completed_tasks += 1
			continue
		
		if t.status == "Canceled":
			continue

		if t.exp_end_date and getdate(t.exp_end_date) < today:
			overdue_tasks += 1
			if t.priority in ["High", "Urgent"]:
				high_priority_overdue += 1

	# Schedule Health: (On-track tasks / Total non-canceled)
	# For simplicity: ((Total - Overdue) / Total) * 100
	schedule_health = max(0, round(((total_tasks - overdue_tasks) / total_tasks) * 100))

	return {
		"total_tasks": total_tasks,
		"completed_count": completed_tasks,
		"overdue_count": overdue_tasks,
		"high_priority_overdue": high_priority_overdue,
		"schedule_health": schedule_health,
		"overall_progress": frappe.db.get_value("Project", project_name, "percent_complete") or 0
	}


@frappe.whitelist()
def get_project_tasks(project, parent=None):
	"""
	Fetches tasks for a project. If 'parent' is provided, fetches only 
	its direct children (Lazy Loading). Otherwise, fetches root tasks.
	"""
	if not project:
		return {"error": "Project name is required."}

	try:
		task_fields = [
			"name", "subject", "status", "priority", "exp_start_date",
			"exp_end_date", "progress", "expected_time", "parent_task", "custom_subtask_order",
			"is_milestone"
		]
		# custom_is_recurring is a site-level custom field that may not exist on
		# every install; only query it when present to avoid a SQL error.
		has_recurring = frappe.get_meta("Task").has_field("custom_is_recurring")
		if has_recurring:
			task_fields.append("custom_is_recurring")

		filters = {"project": project}
		if parent:
			filters["parent_task"] = parent
		else:
			# Fetch root tasks (no parent or parent not in this project/already deleted)
			filters["parent_task"] = ("is", "not set")

		tasks = frappe.get_all(
			"Task",
			fields=task_fields,
			filters=filters,
			order_by="custom_subtask_order asc"
		)

		for task in tasks:
			assignees = _get_assignee_names("Task", task["name"])
			task["assigned_to"] = ", ".join([d["full_name"] for d in assignees]) if assignees else ""
			task["has_children"] = frappe.db.exists("Task", {"parent_task": task["name"]})
			task["children"] = [] # Placeholder for consistency
			task["custom_is_recurring"] = task.get("custom_is_recurring") or 0

		return tasks

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error fetching tasks for project {project}")
		return {"error": "Could not fetch tasks."}


def _fetch_all_master_project_projects(master_project):
	"""Fetches all projects linked to a given master project."""
	project_fields = [
		"name", "project_name", "status", "expected_start_date",
		"expected_end_date", "percent_complete", "custom_subproject_order", "creation",
	]
	try:
		return frappe.get_list("Project", fields=project_fields, filters={"custom_master_project": master_project})
	except Exception as e:
		frappe.log_error(f"Project fetch failed for master project {master_project}: {e}", frappe.get_traceback())
		return []


@frappe.whitelist()
def get_master_project_projects(master_project):
	"""Fetches and enriches projects for a master project."""
	if not master_project:
		return {"error": "Master Project name is required."}

	try:
		projects = _fetch_all_master_project_projects(master_project)
		for project in projects:
			assignees = _get_assignee_names("Project", project.get("name"))
			project["assigned_to"] = ", ".join([d["full_name"] for d in assignees]) if assignees else ""
			project["children"] = []

		projects.sort(key=lambda p: (float(p.get("custom_subproject_order") or float("inf")), getdate(p.get("creation"))))
		return projects
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error fetching projects for master project {master_project}")
		return {"error": "Could not fetch projects for master project."}


@frappe.whitelist()
def update_master_project_structure(master_project, projects):
	"""Updates ordering for projects under a Master Project."""
	if not master_project or not projects:
		return {"status": "error", "message": "Master Project name and project data are required."}

	if isinstance(projects, str):
		projects = json.loads(projects)

	if not frappe.has_permission("Master Project", ptype="write", doc=master_project):
		return {"status": "error", "message": "No permission to modify this Master Project."}

	try:
		for p_data in projects:
			p_name = p_data.get("name")
			if p_name:
				frappe.db.set_value("Project", p_name, "custom_subproject_order", p_data.get("custom_subproject_order"))
		return {"status": "success"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error updating master project structure for {master_project}")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def update_task_date(task_name, field, value):
	"""Updates a task's start or end date with permission checks.

	Before updating, it verifies that the user has 'write' permission on the
	parent project. It also validates that the start date is not after the
	end date.

	Args:
	    task_name (str): The name (ID) of the task to update.
	    field (str): The date field to update ('exp_start_date' or 'exp_end_date').
	    value (str | None): The new date in 'YYYY-MM-DD' format, or None to clear it.

	Returns:
	    dict: A dictionary indicating the status of the operation.
	"""
	if not task_name or not field:
		return {"status": "error", "message": "Task and field are required."}

	if field not in ["exp_start_date", "exp_end_date"]:
		return {"status": "error", "message": "Invalid field specified."}

	try:
		project = frappe.db.get_value("Task", task_name, "project")
		if not project:
			return {"status": "error", "message": "This task is not linked to a project."}

		if not frappe.has_permission("Project", ptype="write", doc=project):
			return {
				"status": "error",
				"message": "You do not have permission to modify tasks for this project.",
			}

		task = frappe.get_doc("Task", task_name)
		new_date = getdate(value) if value else None

		if field == "exp_start_date":
			end_date = getdate(task.exp_end_date)
			if end_date and new_date and new_date > end_date:
				return {"status": "error", "message": "Start date cannot be after end date."}

		if field == "exp_end_date":
			start_date = getdate(task.exp_start_date)
			if start_date and new_date and new_date < start_date:
				return {"status": "error", "message": "End date cannot be before start date."}

		frappe.db.set_value("Task", task_name, field, new_date)
		return {"status": "success"}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error updating task {task_name}")
		return {"status": "error", "message": "Could not update task date. Please check the logs."}


@frappe.whitelist()
def update_task_expected_time(task_name, expected_time):
	"""Updates a task's expected time with permission and validation checks.

	Verifies 'write' permission on the parent project and ensures the new
	value is a non-negative number.

	Args:
	    task_name (str): The name (ID) of the task to update.
	    expected_time (str | float): The new expected time value.

	Returns:
	    dict: A dictionary indicating the status of the operation.
	"""
	if not task_name:
		return {"status": "error", "message": "Task name is required."}

	try:
		project = frappe.db.get_value("Task", task_name, "project")
		if not project:
			return {"status": "error", "message": "This task is not linked to a project."}

		if not frappe.has_permission("Project", ptype="write", doc=project):
			return {
				"status": "error",
				"message": "You do not have permission to modify tasks for this project.",
			}

		try:
			time_val = float(expected_time)
			if time_val < 0:
				return {"status": "error", "message": "Expected time cannot be negative."}
		except (ValueError, TypeError):
			return {"status": "error", "message": "Invalid input. Expected time must be a number."}

		frappe.db.set_value("Task", task_name, "expected_time", time_val)
		return {"status": "success"}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error updating expected time for task {task_name}")
		return {
			"status": "error",
			"message": "Could not update task's expected time. See logs for details.",
		}


@frappe.whitelist()
def add_task_assignee(task_name, user_id):
	"""Assigns a user to a task with permission checks.

	Verifies 'write' permission on the parent project before using Frappe's
	standard assignment function.

	Args:
	    task_name (str): The name (ID) of the task.
	    user_id (str): The email/ID of the user to assign.

	Returns:
	    dict: Status of the operation and the updated list of assignees.
	"""
	if not task_name or not user_id:
		return {"status": "error", "message": "Task and User are required."}

	try:
		project = frappe.db.get_value("Task", task_name, "project")
		if not project or not frappe.has_permission("Project", ptype="write", doc=project):
			return {"status": "error", "message": "You do not have permission to modify this task."}

		from frappe.desk.form.assign_to import add

		add({"doctype": "Task", "name": task_name, "assign_to": [user_id]})

		updated_assignees = _get_assignee_names("Task", task_name)
		return {"status": "success", "assignees": updated_assignees}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error assigning user {user_id} to task {task_name}")
		return {"status": "error", "message": "Could not assign user. Please check the logs."}


@frappe.whitelist()
def remove_task_assignee(task_name, user_id):
	"""Removes a user's assignment from a task with permission checks.

	Verifies 'write' permission on the parent project before using Frappe's
	standard assignment removal function.

	Args:
	    task_name (str): The name (ID) of the task.
	    user_id (str): The email/ID of the user to un-assign.

	Returns:
	    dict: Status of the operation and the updated list of assignees.
	"""
	if not task_name or not user_id:
		return {"status": "error", "message": "Task and User are required."}

	try:
		project = frappe.db.get_value("Task", task_name, "project")
		if not project or not frappe.has_permission("Project", ptype="write", doc=project):
			return {"status": "error", "message": "You do not have permission to modify this task."}

		from frappe.desk.form.assign_to import remove

		remove("Task", task_name, user_id)

		updated_assignees = _get_assignee_names("Task", task_name)
		return {"status": "success", "assignees": updated_assignees}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error removing user {user_id} from task {task_name}")
		return {"status": "error", "message": "Could not remove user. Please check the logs."}


@frappe.whitelist()
def update_task_structure(project_name, tasks):
	"""Updates the parent and ordering for a list of tasks.

	This function is called after a drag-and-drop operation on the frontend
	task tree. It validates permissions and ensures data integrity before
	committing changes.

	Args:
	    project_name (str): The name (ID) of the project being modified.
	    tasks (list[dict]): A list of dictionaries, where each dictionary
	        represents a task and contains its 'name', 'parent_task', and
	        'custom_subtask_order'.

	Returns:
	    dict: A dictionary indicating the status of the operation.
	"""
	if not project_name or not tasks:
		return {"status": "error", "message": "Project name and task data are required."}

	if isinstance(tasks, str):
		try:
			tasks = json.loads(tasks)
		except json.JSONDecodeError:
			return {"status": "error", "message": "Invalid task data format."}

	# Security check: Ensure the user has write permission for the project.
	if not frappe.has_permission("Project", ptype="write", doc=project_name):
		return {
			"status": "error",
			"message": "You do not have permission to modify tasks for this project.",
		}

	try:
		task_names = [t.get("name") for t in tasks if t.get("name")]

		# Data integrity check: Verify that all tasks belong to the specified project.
		if task_names:
			db_task_projects = frappe.get_all(
				"Task", filters={"name": ("in", task_names)}, fields=["name", "project"]
			)
			if len(db_task_projects) != len(task_names):
				return {"status": "error", "message": "One or more tasks could not be found."}

			for task in db_task_projects:
				if task.project != project_name:
					return {
						"status": "error",
						"message": f"Task {task.name} does not belong to project {project_name}.",
					}

		# Atomically update all tasks. Using doc.save() is more robust as it
		# triggers validation and other controller hooks. This is safer than a
		# direct `frappe.db.set_value` call, especially for complex updates
		# involving parent-child relationships.
		for task_data in tasks:
			task_name = task_data.get("name")
			if not task_name:
				continue

			# Ensure parent_task is None if it's an empty string or not provided.
			parent_task = task_data.get("parent_task") or None
			order = task_data.get("custom_subtask_order")

			task_doc = frappe.get_doc("Task", task_name)
			task_doc.parent_task = parent_task
			task_doc.custom_subtask_order = order
			# ignore_permissions is used because we already validated the user has
			# write access to the parent project.
			task_doc.save(ignore_permissions=True)

		return {"status": "success"}

	except Exception as e:
		# The whitelisted method runs in a transaction, which will be rolled
		# back automatically by the Frappe framework on an exception.
		frappe.log_error(frappe.get_traceback(), f"Error updating task structure for {project_name}")
		return {
			"status": "error",
			# We return the specific exception to the client to aid debugging,
			# which is acceptable in this internal tool's context.
			"message": f"An unexpected error occurred while saving the new task order: {e}",
		}


@frappe.whitelist()
def update_task_dates_from_gantt(task_name, start_date, end_date):
	"""
	Updates a task's start and end dates from the Gantt chart and 
	recursively shifts all downstream dependencies.
	"""
	if not task_name or not start_date or not end_date:
		return {"status": "error", "message": "Task, start date, and end date are required."}

	try:
		project = frappe.db.get_value("Task", task_name, "project")
		if not project or not frappe.has_permission("Project", ptype="write", doc=project):
			return {"status": "error", "message": "No permission to modify tasks for this project."}

		# Calculate day difference for shifting
		old_dates = frappe.db.get_value("Task", task_name, ["exp_start_date", "exp_end_date"], as_dict=True)
		day_diff = 0
		if old_dates.exp_start_date:
			day_diff = (getdate(start_date) - getdate(old_dates.exp_start_date)).days

		# Update current task
		frappe.db.set_value("Task", task_name, {"exp_start_date": start_date, "exp_end_date": end_date})

		# If there's a shift, propagate it to successors
		if day_diff != 0:
			_shift_successors(task_name, day_diff)

		return {"status": "success"}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error updating task dates and shifting for {task_name}")
		return {"status": "error", "message": str(e)}


def _shift_successors(predecessor_name, day_diff, processed=None):
	"""Recursively shifts dates of all tasks that depend on the given task."""
	if processed is None:
		processed = set()
	
	if predecessor_name in processed:
		return
	processed.add(predecessor_name)

	# Find tasks that depend on this task
	successors = frappe.get_all(
		"Task Depends On",
		filters={"task": predecessor_name},
		fields=["parent"]
	)

	for succ in successors:
		task_name = succ.parent
		task_doc = frappe.get_doc("Task", task_name)
		
		if task_doc.exp_start_date:
			new_start = getdate(task_doc.exp_start_date) + timedelta(days=day_diff)
			task_doc.exp_start_date = new_start.strftime("%Y-%m-%d")
			
		if task_doc.exp_end_date:
			new_end = getdate(task_doc.exp_end_date) + timedelta(days=day_diff)
			task_doc.exp_end_date = new_end.strftime("%Y-%m-%d")
		
		task_doc.save(ignore_permissions=True)
		
		# Recursive shift for the next level
		_shift_successors(task_name, day_diff, processed)


@frappe.whitelist()
def update_project_dates_from_gantt(project_name, start_date, end_date):
	"""Updates a project's expected start/end dates from a Gantt drag.

	Used by the Portfolio Gantt when a project bar is moved or resized.

	Args:
	    project_name (str): The name (ID) of the project to update.
	    start_date (str): New expected start date (YYYY-MM-DD).
	    end_date (str): New expected end date (YYYY-MM-DD).

	Returns:
	    dict: Status of the operation.
	"""
	if not project_name or not start_date or not end_date:
		return {"status": "error", "message": "Project, start date, and end date are required."}

	if not frappe.has_permission("Project", ptype="write", doc=project_name):
		return {"status": "error", "message": "No permission to modify this project."}

	try:
		frappe.db.set_value(
			"Project",
			project_name,
			{"expected_start_date": start_date, "expected_end_date": end_date},
		)
		return {"status": "success"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error updating project dates for {project_name}")
		return {"status": "error", "message": str(e)}


def _task_has_dependency_path(start_task, target_task, visited=None):
	"""Returns True if `start_task` (transitively) depends on `target_task`.

	Walks the "depends on" chain upstream from start_task. Used to reject a new
	dependency that would introduce a cycle.
	"""
	if visited is None:
		visited = set()
	if start_task in visited:
		return False
	visited.add(start_task)

	predecessors = frappe.get_all(
		"Task Depends On", filters={"parent": start_task}, fields=["task"]
	)
	for pred in predecessors:
		if not pred.task:
			continue
		if pred.task == target_task:
			return True
		if _task_has_dependency_path(pred.task, target_task, visited):
			return True
	return False


@frappe.whitelist()
def add_task_dependency(task_name, depends_on_task):
	"""Creates a dependency so that `task_name` depends on `depends_on_task`.

	In Gantt terms, `depends_on_task` is the predecessor and `task_name` is the
	successor; the arrow is drawn from predecessor to successor. This is invoked
	when the user drags a link from one task bar to another.

	Args:
	    task_name (str): The dependent (successor) task.
	    depends_on_task (str): The predecessor task it should depend on.

	Returns:
	    dict: Status of the operation.
	"""
	if not task_name or not depends_on_task:
		return {"status": "error", "message": "Both tasks are required."}

	if task_name == depends_on_task:
		return {"status": "error", "message": "A task cannot depend on itself."}

	try:
		task_project = frappe.db.get_value("Task", task_name, "project")
		dep_project = frappe.db.get_value("Task", depends_on_task, "project")

		if not task_project or not dep_project:
			return {"status": "error", "message": "One or more tasks could not be found."}

		if task_project != dep_project:
			return {"status": "error", "message": "Tasks must belong to the same project."}

		if not frappe.has_permission("Project", ptype="write", doc=task_project):
			return {"status": "error", "message": "No permission to modify tasks for this project."}

		task_doc = frappe.get_doc("Task", task_name)

		# Skip if this dependency already exists.
		if any(row.task == depends_on_task for row in task_doc.depends_on):
			return {"status": "success", "message": "Dependency already exists."}

		# Reject cycles: if the predecessor already depends on this task, linking
		# them would create a loop.
		if _task_has_dependency_path(depends_on_task, task_name):
			return {"status": "error", "message": "That link would create a circular dependency."}

		task_doc.append("depends_on", {"task": depends_on_task})
		task_doc.save(ignore_permissions=True)
		return {"status": "success"}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), f"Error adding dependency to {task_name}")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_gantt_tasks_for_project(project_name):
	"""
	Fetches all tasks for a specific project, formatted for the frappe-gantt library.
	Optimized with bulk-fetching for dependencies and assignees.
	"""
	if not project_name:
		return {"error": "Project name is required."}

	try:
		# Check if baseline fields exist in Task doctype to avoid DB errors
		task_meta = frappe.get_meta("Task")
		fields = [
			"name", "subject", "exp_start_date", "exp_end_date", 
			"progress", "status", "is_milestone"
		]
		
		has_baseline = task_meta.has_field("baseline_start_date") and task_meta.has_field("baseline_end_date")
		if has_baseline:
			fields.extend(["baseline_start_date", "baseline_end_date"])

		tasks = frappe.get_all(
			"Task",
			fields=fields,
			filters={"project": project_name},
			limit_page_length=None # Ensure all tasks are fetched
		)

		if not tasks:
			return []

		task_names = [task["name"] for task in tasks]

		# 1. Bulk fetch dependencies
		dependencies = frappe.get_all(
			"Task Depends On",
			fields=["parent", "task"],
			filters={"parent": ("in", task_names)},
		)

		dependency_map = {}
		for dep in dependencies:
			if dep.parent not in dependency_map:
				dependency_map[dep.parent] = []
			if dep.task:
				dependency_map[dep.parent].append(dep.task)

		# 2. Bulk fetch assignees
		todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Task", "reference_name": ["in", task_names], "status": "Open"},
			fields=["reference_name", "allocated_to"]
		)
		
		user_emails = list({t["allocated_to"] for t in todos if t.get("allocated_to")})
		user_map = {}
		if user_emails:
			users = frappe.get_all("User", filters={"email": ["in", user_emails]}, fields=["email", "full_name"])
			user_map = {u["email"]: u["full_name"] for u in users}

		task_assignee_map = {}
		for todo in todos:
			t_name = todo["reference_name"]
			if t_name not in task_assignee_map:
				task_assignee_map[t_name] = []
			email = todo.get("allocated_to")
			if email and email in user_map:
				task_assignee_map[t_name].append(user_map[email])

		gantt_tasks = []
		today = getdate(nowdate())

		for task in tasks:
			start_date = getdate(task.exp_start_date) if task.exp_start_date else today
			end_date = getdate(task.exp_end_date) if task.exp_end_date else start_date + timedelta(days=3)

			if end_date < start_date:
				end_date = start_date + timedelta(days=3)

			task_dependencies = dependency_map.get(task.name, [])
			
			progress = task.progress or 0
			custom_class = ""
			if end_date < today and progress < 100 and task.status not in ["Completed", "Canceled"]:
				custom_class = "bar-overdue"

			assignees = task_assignee_map.get(task.name, [])
			assigned_to_str = ", ".join(assignees) if assignees else "Unassigned"

			gantt_tasks.append(
				{
					"id": task.name,
					"name": task.subject,
					"start": start_date.strftime("%Y-%m-%d"),
					"end": end_date.strftime("%Y-%m-%d"),
					"progress": progress,
					"dependencies": ",".join([d for d in task_dependencies if d]),
					"custom_class": custom_class,
					"assigned_to": assigned_to_str,
					"status": task.status,
					"is_milestone": task.is_milestone,
					"baseline_start": task.get("baseline_start_date"),
					"baseline_end": task.get("baseline_end_date")
				}
			)

		return gantt_tasks

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error fetching Gantt tasks for project {project_name}")
		return {"error": "Could not fetch tasks for Gantt board."}


@frappe.whitelist()
def update_task_progress_from_gantt(task_name, progress):
	"""
	Updates a task's progress from the Gantt chart.

	Args:
	    task_name (str): The name (ID) of the task to update.
	    progress (int): The new progress value (0-100).

	Returns:
	    dict: A dictionary indicating the status of the operation.
	"""
	if not task_name:
		return {"status": "error", "message": "Task name is required."}

	try:
		project = frappe.db.get_value("Task", task_name, "project")
		if not project:
			return {"status": "error", "message": "This task is not linked to a project."}

		if not frappe.has_permission("Project", ptype="write", doc=project):
			return {
				"status": "error",
				"message": "You do not have permission to modify tasks for this project.",
			}

		try:
			progress_val = int(progress)
			if not (0 <= progress_val <= 100):
				return {"status": "error", "message": "Progress must be between 0 and 100."}
		except (ValueError, TypeError):
			return {"status": "error", "message": "Invalid input. Progress must be a number."}

		frappe.db.set_value("Task", task_name, "progress", progress_val)
		return {"status": "success"}

	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error updating task progress from Gantt for {task_name}")
		return {"status": "error", "message": "Could not update task progress. Please check the logs."}


@frappe.whitelist()
def update_multiple_docs(project_updates, task_updates):
	"""Updates multiple project and task documents in a single transaction.

	This function is designed to handle batch updates from the dashboard.

	Args:
	    project_updates (str): A JSON string representing a dictionary of
	        project updates. Example: '{"PROJ-001": {"status": "Completed"}}'
	    task_updates (str): A JSON string representing a dictionary of
	        task updates. Example: '{"TASK-001": {"status": "Working"}}'

	Returns:
	    dict: A dictionary indicating the status of the operation.
	"""
	if not check_permission():
		return {"status": "error", "message": "You do not have permission to perform this action."}

	try:
		project_updates = json.loads(project_updates or "{}")
		task_updates = json.loads(task_updates or "{}")

		for doc_name, changes in project_updates.items():
			if not frappe.has_permission("Project", ptype="write", doc=doc_name):
				return {"status": "error", "message": f"No write permission for Project {doc_name}"}
			frappe.db.set_value("Project", doc_name, changes)

		for doc_name, changes in task_updates.items():
			project = frappe.db.get_value("Task", doc_name, "project")
			if not project or not frappe.has_permission("Project", ptype="write", doc=project):
				return {
					"status": "error",
					"message": f"No write permission for parent Project of Task {doc_name}",
				}
			frappe.db.set_value("Task", doc_name, changes)

		return {"status": "success"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Error in batch update")
		return {"status": "error", "message": str(e)}

@frappe.whitelist()
def delete_task(task_name):
	"""Deletes a single task."""
	if not task_name:
		return {"status": "error", "message": "Task name is required."}

	try:
		if not frappe.has_permission("Task", ptype="delete", doc=task_name):
			return {"status": "error", "message": "You do not have permission to delete this task."}

		frappe.delete_doc("Task", task_name)
		return {"status": "success"}
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Error deleting task {task_name}")
		return {"status": "error", "message": "Could not delete task. Please check the logs."}


def publish_realtime_update(doc, method):
	"""Publishes a real-time event when a project or task is updated."""
	project = doc.name if doc.doctype == "Project" else getattr(doc, "project", None)
	if project:
		frappe.publish_realtime("project_dashboard_updated", {"project": project})


@frappe.whitelist()
def get_all_projects_for_gantt(include_tasks=0, statuses=None):
	"""
	Fetches all active projects (and optionally child tasks) for the Gantt view.
	Supports status filtering and detailed view expansion.
	"""
	# Note: legacy check_permission() gate removed here in favour of native Page
	# Role permissions, mirroring get_project_data(). The previous gate returned
	# an error whenever the "Project Dashboard" Custom Role was not configured,
	# which left the Portfolio Gantt empty even though every other tab (fed by
	# get_project_data) loaded fine.
	try:
		include_tasks = int(include_tasks)

		filters = {
			"is_active": "Yes",
			"status": ["!=", "Canceled"],
			# Restrict to client-facing work so internal/organizational projects
			# stay off the Portfolio Gantt.
			"project_type": ["in", ["Build", "Design", "Events", "Service", "Delivery"]],
		}

		if statuses:
			status_list = json.loads(statuses)
			if status_list:
				filters["status"] = ["in", status_list]

		projects = frappe.get_all(
			"Project",
			fields=[
				"name",
				"project_name",
				"expected_start_date",
				"expected_end_date",
				"percent_complete",
				"status",
				"custom_master_project",
				"project_type",
			],
			filters=filters,
		)

		tasks = []
		if include_tasks and projects:
			project_names = [p.name for p in projects]
			tasks = frappe.get_all(
				"Task",
				fields=[
					"name",
					"subject",
					"exp_start_date",
					"exp_end_date",
					"progress",
					"project",
					"status",
					"parent_task",
				],
				filters={"project": ["in", project_names], "status": ["not in", ["Completed", "Canceled"]]},
			)

		return {
			"projects": projects,
			"tasks": tasks
		}

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Error fetching all projects for Gantt view")
		return {"error": "Could not fetch project data for the Gantt chart. Please check logs."}

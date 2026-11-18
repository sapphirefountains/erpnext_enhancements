"""Server-side API for the Projects Dashboard.

Backend for the "Projects Dashboard" Custom HTML Block
(``custom_html_blocks/projects_dashboard.js``, embedded on the Home / Projects
workspaces): data retrieval, permission checks, and in-place updates for projects
and tasks. All functions called from the front end are ``@frappe.whitelist()``.
The Products tab reads ``get_products_data`` (v1.824.0, at the end of this module), whose
rules live Frappe-free in ``erpnext_enhancements/product_orders.py``.

NOTE: the standalone desk page that used to live here (``project_dashboard.js`` /
``.json`` + the per-tab ``dashboard_components/*.js``) was retired in v1.159.8 when
the two parallel dashboards were consolidated onto the Custom HTML Block; this
module (the shared API) stays.
"""

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from erpnext_enhancements import product_orders as po
from erpnext_enhancements.utils.spreadsheet import build_payload

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

	except Exception:
		frappe.log_error(title="Error checking project dashboard permissions", message=frappe.get_traceback())
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
	except Exception:
		frappe.log_error(
			title=f"Error fetching assignee names for {doctype} {docname}", message=frappe.get_traceback()
		)
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
		frappe.log_error(title="Error fetching project data", message=frappe.get_traceback())
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
		frappe.log_error(title="Error creating inline task", message=frappe.get_traceback())
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
		frappe.log_error(
			title=f"Error assigning user {user_id} to project {project_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(
			title=f"Error removing user {user_id} from project {project_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(title=f"Error updating project {project_name}", message=frappe.get_traceback())
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
		frappe.log_error(title=f"Error updating task status for {task_name}", message=frappe.get_traceback())
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
		frappe.log_error(
			title=f"Error updating task priority for {task_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(title="Error fetching priority options", message=frappe.get_traceback())
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
		frappe.log_error(title="Error fetching status options", message=frappe.get_traceback())
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
		frappe.log_error(title="Error fetching task status options", message=frappe.get_traceback())
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
	except Exception:
		frappe.log_error(
			title=f"Initial task fetch failed for project {project_name}", message=frappe.get_traceback()
		)
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

		except Exception:
			frappe.log_error(
				title=f"Child task fetch failed for parents {parent_ids}", message=frappe.get_traceback()
			)
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

		return tasks

	except Exception:
		frappe.log_error(title=f"Error fetching tasks for project {project}", message=frappe.get_traceback())
		return {"error": "Could not fetch tasks."}


# ---------------------------------------------------------------------------
# Scope-tab task tree — export and print
# ---------------------------------------------------------------------------

# Untranslated at module scope on purpose: _() here would resolve against
# whichever language the worker happened to import the module under and then
# serve that to every user. Translated per request in _task_tree_rows.
TASK_EXPORT_COLUMNS = (
	"Level",
	"Task",
	"Status",
	"Priority",
	"Assigned To",
	"Start Date",
	"Due Date",
	"% Complete",
	"Expected Time (h)",
	"Task ID",
)


def _flatten_task_tree(project):
	"""Every Task on ``project`` as ``[(level, task), ...]``, parent before child.

	The on-screen tree loads children lazily one level at a time
	(``get_task_children``), which is right for a grid the user is clicking
	through and wrong for an export — a file built from that would contain only
	the branches somebody happened to expand. This reads the whole project in
	ONE query and links it in memory instead.

	Uses ``frappe.get_list``, not ``get_all``: the neighbouring read endpoints
	here predate the permission review and use ``get_all``, but an export
	produces a file that leaves the system, so it goes through the
	permission-checked path. A task the user may not read is not in the file.

	Orphans (``parent_task`` set but pointing outside the project, at a deleted
	task, or at one this user cannot read) are emitted as roots rather than
	dropped — losing rows silently from an export is worse than a flat one.
	"""
	fields = [
		"name",
		"subject",
		"status",
		"priority",
		"exp_start_date",
		"exp_end_date",
		"progress",
		"expected_time",
		"parent_task",
		"custom_subtask_order",
		"is_milestone",
	]
	tasks = frappe.get_list(
		"Task",
		fields=fields,
		filters={"project": project},
		order_by="custom_subtask_order asc, creation asc",
		limit_page_length=0,
	)
	if not tasks:
		return []

	by_name = {t["name"]: t for t in tasks}
	children = {}
	roots = []
	for task in tasks:
		parent = task.get("parent_task")
		if parent and parent in by_name:
			children.setdefault(parent, []).append(task)
		else:
			roots.append(task)

	out = []
	seen = set()

	def walk(nodes, level):
		for task in nodes:
			if task["name"] in seen:
				continue  # a parent_task cycle would otherwise recurse forever
			seen.add(task["name"])
			out.append((level, task))
			walk(children.get(task["name"], []), level + 1)

	walk(roots, 0)
	# Only reachable if parent_task forms a cycle; still belongs in the file.
	for task in tasks:
		if task["name"] not in seen:
			out.append((0, task))
	return out


def _task_tree_rows(project):
	"""``[[header...], [cell...], ...]`` for the task-tree export."""
	rows = [[_(label) for label in TASK_EXPORT_COLUMNS]]
	for level, task in _flatten_task_tree(project):
		assignees = _get_assignee_names("Task", task["name"])
		rows.append(
			[
				level,
				("    " * level) + (task.get("subject") or ""),
				task.get("status") or "",
				task.get("priority") or "",
				", ".join(d["full_name"] for d in assignees) if assignees else "",
				task.get("exp_start_date") or "",
				task.get("exp_end_date") or "",
				flt(task.get("progress") or 0),
				flt(task.get("expected_time") or 0),
				task["name"],
			]
		)
	return rows


@frappe.whitelist()
def get_project_task_tree(project):
	"""The project's full task tree, flattened, for the print view.

	Same data as :func:`export_project_tasks` but as JSON, so the browser can
	lay it out as a printable document. Kept separate from ``get_project_tasks``
	because that one is deliberately lazy and per-level.
	"""
	if not project:
		frappe.throw(_("Project is required"))
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(_("Not permitted to read {0}").format(project), frappe.PermissionError)

	out = []
	for level, task in _flatten_task_tree(project):
		assignees = _get_assignee_names("Task", task["name"])
		out.append(
			{
				"level": level,
				"name": task["name"],
				"subject": task.get("subject") or "",
				"status": task.get("status") or "",
				"priority": task.get("priority") or "",
				"assigned_to": ", ".join(d["full_name"] for d in assignees) if assignees else "",
				"exp_start_date": task.get("exp_start_date"),
				"exp_end_date": task.get("exp_end_date"),
				"progress": flt(task.get("progress") or 0),
				"expected_time": flt(task.get("expected_time") or 0),
				"is_milestone": cint(task.get("is_milestone")),
			}
		)
	return out


@frappe.whitelist()
def export_project_tasks(project, file_format="csv", title=None):
	"""The project's task tree as a base64 CSV/XLSX payload.

	Returns ``{filename, content_type, filecontent}``, or ``None`` when the
	project has no tasks. See ``utils/spreadsheet.py`` for why the bytes come
	back base64 inside JSON rather than as a streamed download.
	"""
	if not project:
		frappe.throw(_("Project is required"))
	if not frappe.has_permission("Project", "read", doc=project):
		frappe.throw(_("Not permitted to read {0}").format(project), frappe.PermissionError)

	return build_payload(
		_task_tree_rows(project),
		file_format,
		title or project,
		sheet_name=_("Tasks"),
	)


def _fetch_all_master_project_projects(master_project):
	"""Fetches all projects linked to a given master project."""
	project_fields = [
		"name", "project_name", "status", "expected_start_date",
		"expected_end_date", "percent_complete", "custom_subproject_order", "creation",
	]
	try:
		return frappe.get_list("Project", fields=project_fields, filters={"custom_master_project": master_project})
	except Exception:
		frappe.log_error(
			title=f"Project fetch failed for master project {master_project}", message=frappe.get_traceback()
		)
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
		frappe.log_error(
			title=f"Error fetching projects for master project {master_project}",
			message=frappe.get_traceback(),
		)
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
		frappe.log_error(
			title=f"Error updating master project structure for {master_project}",
			message=frappe.get_traceback(),
		)
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

		# v1.787.0: a task with subtasks takes its dates from them (task_enhancements/parent_dates.py).
		from erpnext_enhancements.task_enhancements import parent_dates

		refusal = parent_dates.refusal(task_name)
		if refusal:
			return {"status": "error", "message": refusal}

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
		# set_value fires no hook: walk the parents here.
		if task.parent_task:
			parent_dates.sync_task_ancestors(task.parent_task)
		return {"status": "success"}

	except Exception:
		frappe.log_error(title=f"Error updating task {task_name}", message=frappe.get_traceback())
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
		frappe.log_error(
			title=f"Error updating expected time for task {task_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(
			title=f"Error assigning user {user_id} to task {task_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(
			title=f"Error removing user {user_id} from task {task_name}", message=frappe.get_traceback()
		)
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

	Since v1.787.0 a thin wrapper over ``api.scope_tasks.move_tasks``, kept for the
	old ``{"status", "message"}`` contract. The tree itself calls ``move_tasks`` now.
	What this used to do, and why it was replaced: it saved EVERY row the tree sent
	(the whole visible tree, on every drag), with ``ignore_permissions``; a refusal
	from ERPNext's validate (``ParentIsGroupError`` on a drop onto a leaf task, a
	child ending after its parent, an open task under a completed parent) was caught
	after the earlier rows had already been saved, logged as an error, and returned
	as a message the tree never showed -- while the comment here claimed the
	transaction would roll back. It did not: a caught exception commits whatever
	was written before it. ``move_tasks`` saves only the rows that changed, under a
	savepoint it rolls back on refusal, and returns ERPNext's own reason.
	"""
	from erpnext_enhancements.api import scope_tasks

	if not project_name or not tasks:
		return {"status": "error", "message": "Project name and task data are required."}
	try:
		out = scope_tasks.move_tasks(project_name, tasks)
	except Exception as e:
		return {"status": "error", "message": str(e)}
	refused = out.get("refused")
	if refused:
		return {"status": "error", "message": refused.get("reason"), "task": refused.get("name")}
	return {"status": "success", "moved": out.get("moved", []), "converted": out.get("converted", [])}


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

		# v1.787.0: a task with subtasks takes its dates from them (task_enhancements/parent_dates.py).
		from erpnext_enhancements.task_enhancements import parent_dates

		refusal = parent_dates.refusal(task_name)
		if refusal:
			return {"status": "error", "message": refusal}

		# Calculate day difference for shifting
		old_dates = frappe.db.get_value(
			"Task", task_name, ["exp_start_date", "exp_end_date", "parent_task"], as_dict=True
		)
		day_diff = 0
		if old_dates.exp_start_date:
			day_diff = (getdate(start_date) - getdate(old_dates.exp_start_date)).days

		# Update current task
		frappe.db.set_value("Task", task_name, {"exp_start_date": start_date, "exp_end_date": end_date})
		# set_value fires no hook: walk the parents here.
		if old_dates.parent_task:
			parent_dates.sync_task_ancestors(old_dates.parent_task)

		# If there's a shift, propagate it to successors
		if day_diff != 0:
			_shift_successors(task_name, day_diff)

		return {"status": "success"}

	except Exception as e:
		frappe.log_error(
			title=f"Error updating task dates and shifting for {task_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(
			title=f"Error updating project dates for {project_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(title=f"Error adding dependency to {task_name}", message=frappe.get_traceback())
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
		frappe.log_error(
			title=f"Error fetching Gantt tasks for project {project_name}", message=frappe.get_traceback()
		)
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
		frappe.log_error(
			title=f"Error updating task progress from Gantt for {task_name}", message=frappe.get_traceback()
		)
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

		# v1.787.0: a task with subtasks takes its dates from them (task_enhancements/parent_dates.py),
		# and set_value fires no hook, so the parents are walked here after each write.
		from erpnext_enhancements.task_enhancements import parent_dates

		for doc_name, changes in task_updates.items():
			project, parent_task = frappe.db.get_value("Task", doc_name, ["project", "parent_task"]) or (None, None)
			if not project or not frappe.has_permission("Project", ptype="write", doc=project):
				return {
					"status": "error",
					"message": f"No write permission for parent Project of Task {doc_name}",
				}
			if any(field in changes for field in parent_dates.DATE_FIELDS):
				refusal = parent_dates.refusal(doc_name)
				if refusal:
					return {"status": "error", "message": refusal}
			frappe.db.set_value("Task", doc_name, changes)
			if parent_task and any(field in changes for field in parent_dates.DATE_FIELDS):
				parent_dates.sync_task_ancestors(parent_task)

		return {"status": "success"}
	except Exception as e:
		frappe.log_error(title="Error in batch update", message=frappe.get_traceback())
		return {"status": "error", "message": str(e)}

@frappe.whitelist()
def delete_task(task_name):
	"""Deletes a single task.

	The two refusals below are **answers, not faults**, and are handled apart from
	the generic catch for that reason. A task that another task links to (as
	``parent_task`` or through ``depends_on``) raises ``LinkExistsError``, and one
	held by an open lock raises ``DocumentLockedError``; both name the blocker in
	their own message. Collapsing them into "check the logs" told the user nothing
	they could act on, so they re-clicked — 25 identical tracebacks landed in the
	Error Log on 2026-09-14 from one person deleting a subtree, each naming the
	child task that was in the way, none of it ever reaching the screen. Passing
	the message through is the whole fix; deleting children first is the user's
	call, not ours to guess.
	"""
	if not task_name:
		return {"status": "error", "message": "Task name is required."}

	try:
		if not frappe.has_permission("Task", ptype="delete", doc=task_name):
			return {"status": "error", "message": "You do not have permission to delete this task."}

		frappe.delete_doc("Task", task_name)
		return {"status": "success"}
	except (frappe.LinkExistsError, frappe.DocumentLockedError) as e:
		# No log_error: an expected refusal the user can read and act on is not an
		# incident, and a traceback per click is what buried the real ones.
		return {"status": "error", "message": str(e)}
	except Exception:
		frappe.log_error(title=f"Error deleting task {task_name}", message=frappe.get_traceback())
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
		frappe.log_error(title="Error fetching all projects for Gantt view", message=frappe.get_traceback())
		return {"error": "Could not fetch project data for the Gantt chart. Please check logs."}


# ---------------------------------------------------------------------------------------------
# The Products tab (v1.824.0)
# ---------------------------------------------------------------------------------------------
#
# Nikolas, 2026-11-18 (vault decisions/2026-11-18-estop-product-line.md): a product sale is a
# Sales Order, not a Project, and the Projects Dashboard gets a Products tab for Logan, James,
# Brian and him. Version 1 has four sections: the order board by stage, the sales pipeline,
# stock and shortages, and the control panel projects (custom-engineered panels stay Projects
# of type "Products").
#
# Every rule that decides something (which Items are products, an order's stage, which orders
# the shelf covers, the win rate) lives in erpnext_enhancements/product_orders.py, Frappe-free
# and tested bench-free by tests/test_product_orders.py. What is here only gathers the facts.
#
# Permissions: the page gate first (check_permission), as every endpoint in this module. Then
# each section asks for read on the doctypes it shows. A section the person cannot read comes
# back {"allowed": False} and draws the no-access panel, and the other sections still draw. The
# reads are SQL, so per-record User Permissions do not narrow them: the same choice
# get_project_data makes for the portfolio (see its docstring). The paid chip on an order also
# needs Sales Invoice read: Logan builds the units and holds no accounts role.
#
# Aggregates are frappe.db.sql with bound parameters, never a function written as a string field
# in get_all (Frappe 16 refuses those; CLAUDE.md). The configurator's provenance column on Item
# is added in after_migrate, so it is checked before any query names it.

#: Custom-engineered control panels are Projects of this type.
PRODUCTS_PROJECT_TYPE = "Products"
#: The Opportunity value stream the pipeline reads.
PRODUCTS_VALUE_STREAM = "Products"
#: The SQL prefilter for product item codes; product_orders.product_line has the final word.
_PDT_LIKE = "PDT-%"


@frappe.whitelist()
def get_products_data():
	"""Everything the Products tab draws, in one answer.

	``{"today", "lines", "stages", "orders", "pipeline": {"quotations", "opportunities"},
	"stock", "projects"}``. Each section carries ``allowed`` (False: the person cannot read its
	doctypes) and, when its read raised, ``failed`` (the error is in the Error Log). A user
	without the page role gets ``{"error": "You do not have permission ..."}``, as
	get_project_data answers.
	"""
	if not check_permission():
		return {"error": "You do not have permission to view the Project Dashboard."}

	today = getdate(nowdate())
	source_column = _item_has_source_configuration()

	orders = _products_section(("Sales Order",), lambda: _product_orders(today, source_column))
	# What the open orders have already claimed from the shelf, for the stock section. Private:
	# it never goes to the browser.
	committed = orders.pop("_committed", None)

	return {
		"today": str(today),
		"lines": list(po.LINES),
		"stages": [{"key": key, "label": po.STAGE_LABELS[key]} for key in po.STAGES],
		"orders": orders,
		"pipeline": {
			"quotations": _products_section(
				("Quotation",), lambda: _open_product_quotations(today, source_column)
			),
			"opportunities": _products_section(("Opportunity",), lambda: _product_opportunities(today)),
		},
		"stock": _products_section(("Item", "Bin"), lambda: _product_stock(source_column, committed)),
		"projects": _products_section(("Project",), lambda: _product_projects(today)),
	}


def _products_section(doctypes, build):
	"""One section of the Products tab: read permission first, and a failure kept to itself."""
	if not all(frappe.has_permission(doctype, "read") for doctype in doctypes):
		return {"allowed": False}
	try:
		section = build()
	except Exception:
		frappe.log_error(title="Products tab: a section could not be read", message=frappe.get_traceback())
		return {"allowed": True, "failed": True}
	section["allowed"] = True
	return section


def _item_has_source_configuration():
	"""Whether Item has the Product Configurator's provenance column yet.

	product_configurator/setup.py adds it in after_migrate, so a fresh site can lack it, and
	has_column raises on an unknown table rather than answering False.
	"""
	try:
		return bool(frappe.db.has_column("Item", "custom_source_configuration"))
	except Exception:
		return False


def _product_match(item_alias, line_alias, source_column):
	"""The SELECT expression for an Item's provenance and the WHERE prefilter for product lines."""
	code_match = f"{line_alias}.item_code LIKE %(pdt)s"
	if not source_column:
		return "NULL", code_match
	source = f"{item_alias}.custom_source_configuration"
	return source, f"({code_match} OR coalesce({source}, '') <> '')"


def _iso(value):
	return str(value)[:10] if value else None


def _on_hand(item_codes):
	"""Finished units per item code, summed across warehouses."""
	if not item_codes:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT item_code, SUM(actual_qty) AS qty
		FROM `tabBin`
		WHERE item_code IN %(codes)s
		GROUP BY item_code
		""",
		{"codes": tuple(item_codes)},
		as_dict=True,
	)
	return {row.item_code: flt(row.qty) for row in rows}


def _product_orders(today, source_column):
	"""The order board: submitted product Sales Orders not yet delivered, and the ones delivered
	in the last ``po.DELIVERED_WINDOW_DAYS`` days, each with its stage.
	"""
	since = add_days(today, -po.DELIVERED_WINDOW_DAYS)
	source_select, product_where = _product_match("i", "soi", source_column)
	rows = frappe.db.sql(
		f"""
		SELECT so.name, so.customer, so.customer_name, so.transaction_date, so.delivery_date,
			so.status, so.per_delivered, so.per_billed, so.grand_total, so.currency,
			soi.item_code, soi.item_name, soi.qty, soi.delivered_qty,
			{source_select} AS source_configuration
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		LEFT JOIN `tabItem` i ON i.name = soi.item_code
		WHERE so.docstatus = 1
			AND {product_where}
			AND (
				(so.per_delivered < 100 AND so.status NOT IN ('Closed', 'Completed', 'Cancelled'))
				OR EXISTS (
					SELECT 1
					FROM `tabDelivery Note Item` dni
					INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
					WHERE dni.against_sales_order = so.name
						AND dn.docstatus = 1
						AND dn.posting_date >= %(since)s
				)
			)
		ORDER BY so.delivery_date, so.name, soi.idx
		""",
		{"pdt": _PDT_LIKE, "since": since},
		as_dict=True,
	)

	orders = {}
	for row in rows:
		line = po.product_line(row.item_code, row.source_configuration)
		if line is None:
			continue
		order = orders.get(row.name)
		if order is None:
			order = orders[row.name] = {
				"name": row.name,
				"customer": row.customer,
				"customer_name": row.customer_name or row.customer,
				"transaction_date": _iso(row.transaction_date),
				"delivery_date": _iso(row.delivery_date),
				"status": row.status,
				"per_delivered": flt(row.per_delivered),
				"per_billed": flt(row.per_billed),
				"grand_total": flt(row.grand_total),
				"currency": row.currency,
				"items": [],
			}
		order["items"].append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"pending_qty": max(0.0, flt(row.qty) - flt(row.delivered_qty)),
				"line": line,
			}
		)

	paid_visible = bool(frappe.has_permission("Sales Invoice", "read"))
	if not orders:
		return {"rows": [], "paid_visible": paid_visible, "_committed": {}}

	names = tuple(orders)
	work_orders = {}
	for wo in frappe.db.sql(
		"""
		SELECT name, sales_order, production_item, status, docstatus, qty, produced_qty
		FROM `tabWork Order`
		WHERE sales_order IN %(names)s AND docstatus < 2
		ORDER BY creation
		""",
		{"names": names},
		as_dict=True,
	):
		work_orders.setdefault(wo.sales_order, []).append(wo)

	deliveries = {}
	for dn in frappe.db.sql(
		"""
		SELECT DISTINCT dni.against_sales_order AS sales_order, dn.name, dn.posting_date
		FROM `tabDelivery Note Item` dni
		INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
		WHERE dni.against_sales_order IN %(names)s AND dn.docstatus = 1 AND dn.is_return = 0
		""",
		{"names": names},
		as_dict=True,
	):
		deliveries.setdefault(dn.sales_order, []).append(dn)

	invoices = {}
	if paid_visible:
		for si in frappe.db.sql(
			"""
			SELECT DISTINCT sii.sales_order, si.name, si.grand_total, si.outstanding_amount
			FROM `tabSales Invoice Item` sii
			INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
			WHERE sii.sales_order IN %(names)s AND si.docstatus = 1 AND si.is_return = 0
			""",
			{"names": names},
			as_dict=True,
		):
			invoices.setdefault(si.sales_order, []).append(si)

	on_hand = _on_hand({item["item_code"] for order in orders.values() for item in order["items"]})

	facts = []
	for order in orders.values():
		_drafts, live, done = po.work_order_summary(work_orders.get(order["name"], []))
		facts.append(
			{
				"name": order["name"],
				"delivery_date": order["delivery_date"],
				"built": done,
				"has_work_order": bool(live),
				"lines": order["items"],
			}
		)
	covered = po.allocate_stock(facts, on_hand)

	committed = {}
	for fact in facts:
		if fact["built"] or fact["name"] in covered:
			for item in fact["lines"]:
				committed[item["item_code"]] = committed.get(item["item_code"], 0) + item["pending_qty"]

	result = []
	for order in orders.values():
		wos = work_orders.get(order["name"], [])
		stage = po.order_stage(order["per_delivered"], wos, order["name"] in covered, qc=None)
		dns = sorted(deliveries.get(order["name"], []), key=lambda d: (str(d.posting_date), d.name))
		order.update(
			{
				"stage": stage["stage"],
				"note": stage["note"],
				"late": po.is_late(order["delivery_date"], today, stage["stage"]),
				"partly_delivered": 0 < order["per_delivered"] < 100,
				"on_hold": order["status"] == "On Hold",
				"lines": po.lines_of(order["items"]),
				"work_orders": [
					{"name": wo.name, "status": wo.status, "docstatus": wo.docstatus} for wo in wos
				],
				"deliveries": [dn.name for dn in dns],
				"delivered_on": _iso(dns[-1].posting_date) if dns else None,
				"paid": None,
			}
		)
		if paid_visible:
			sis = invoices.get(order["name"], [])
			order["paid"] = po.paid_state(
				sum(flt(si.grand_total) for si in sis),
				sum(flt(si.outstanding_amount) for si in sis),
				order["per_billed"],
			)
		result.append(order)

	return {"rows": result, "paid_visible": paid_visible, "_committed": committed}


def _open_product_quotations(today, source_column):
	"""Open quotations with a product line on them (product_orders.is_open_quotation)."""
	since = add_days(today, -po.QUOTE_OPEN_DAYS)
	source_select, product_where = _product_match("i", "qi", source_column)
	rows = frappe.db.sql(
		f"""
		SELECT q.name, q.quotation_to, q.party_name, q.customer_name, q.transaction_date,
			q.valid_till, q.status, q.docstatus, q.grand_total, q.currency, q.opportunity,
			qi.item_code, {source_select} AS source_configuration
		FROM `tabQuotation Item` qi
		INNER JOIN `tabQuotation` q ON q.name = qi.parent
		LEFT JOIN `tabItem` i ON i.name = qi.item_code
		WHERE q.docstatus < 2
			AND q.status NOT IN %(closed)s
			AND {product_where}
			AND (q.valid_till >= %(today)s OR (q.valid_till IS NULL AND q.transaction_date >= %(since)s))
		ORDER BY q.transaction_date DESC, q.name
		""",
		{"pdt": _PDT_LIKE, "closed": po.QUOTE_CLOSED_STATUSES, "today": today, "since": since},
		as_dict=True,
	)

	quotes = {}
	for row in rows:
		quote = quotes.get(row.name)
		if quote is None:
			quote = quotes[row.name] = {
				"name": row.name,
				"quotation_to": row.quotation_to,
				"party": row.customer_name or row.party_name,
				"transaction_date": _iso(row.transaction_date),
				"valid_till": _iso(row.valid_till),
				"status": row.status,
				"docstatus": row.docstatus,
				"grand_total": flt(row.grand_total),
				"currency": row.currency,
				"opportunity": row.opportunity,
				"_items": [],
			}
		quote["_items"].append({"item_code": row.item_code, "source_configuration": row.source_configuration})

	result = []
	for quote in quotes.values():
		quote["lines"] = po.lines_of(quote.pop("_items"))
		if quote["lines"] and po.is_open_quotation(
			quote["status"], quote["docstatus"], quote["valid_till"], quote["transaction_date"], since, today
		):
			result.append(quote)
	return {"rows": result, "open_days": po.QUOTE_OPEN_DAYS}


def _product_opportunities(today):
	"""Open Opportunities in the Products value stream, and the stream's win rate over a year.

	Historical deals (crm_enhancements/historical.py) are left out of both, as the Value Stream
	Performance report leaves them out of its win rate.
	"""
	from erpnext_enhancements.crm_enhancements.historical import exclude_sql

	try:
		has_name = bool(frappe.db.has_column("Opportunity", "custom_opportunity_name"))
	except Exception:
		has_name = False
	name_select = "o.custom_opportunity_name" if has_name else "NULL"
	rows = frappe.db.sql(
		f"""
		SELECT o.name, o.title, {name_select} AS opportunity_name, o.opportunity_from,
			o.party_name, o.customer_name, o.status, o.opportunity_amount, o.currency,
			o.transaction_date, o.expected_closing, o.opportunity_owner
		FROM `tabOpportunity` o
		WHERE EXISTS (
				SELECT 1 FROM `tabValue Stream` vs
				WHERE vs.parent = o.name AND vs.parenttype = 'Opportunity' AND vs.value_stream = %(stream)s
			)
			AND {exclude_sql("o")}
		ORDER BY o.transaction_date DESC, o.name
		""",
		{"stream": PRODUCTS_VALUE_STREAM},
		as_dict=True,
	)

	since = str(add_days(today, -po.WIN_RATE_DAYS))
	won = lost = 0
	open_rows = []
	for row in rows:
		dated = _iso(row.transaction_date) or ""
		if dated >= since:
			won += row.status in po.WON_STATUSES
			lost += row.status in po.LOST_STATUSES
		if po.is_open_opportunity(row.status):
			open_rows.append(row)

	items = {}
	if open_rows:
		for item in frappe.db.sql(
			"""
			SELECT parent, item_code FROM `tabOpportunity Item`
			WHERE parent IN %(names)s AND parenttype = 'Opportunity'
			""",
			{"names": tuple(row.name for row in open_rows)},
			as_dict=True,
		):
			items.setdefault(item.parent, []).append({"item_code": item.item_code})

	return {
		"rows": [
			{
				"name": row.name,
				"title": row.opportunity_name or row.title or row.name,
				"party": row.customer_name or row.party_name,
				"status": row.status,
				"amount": flt(row.opportunity_amount),
				"currency": row.currency,
				"transaction_date": _iso(row.transaction_date),
				"expected_closing": _iso(row.expected_closing),
				"owner": row.opportunity_owner,
				# An Opportunity often names no item: its lines are then [] and it shows under
				# every product-line chip.
				"lines": po.lines_of(items.get(row.name, [])),
			}
			for row in open_rows
		],
		"won": won,
		"lost": lost,
		"win_rate": po.win_rate(won, lost),
		"window_days": po.WIN_RATE_DAYS,
	}


def _product_stock(source_column, committed):
	"""Finished product units on hand, and the component stock of each product line.

	``committed``: units the open orders have already claimed from the shelf, per item code, or
	None when the person cannot read Sales Orders (the column then shows a dash).
	"""
	source_select, product_where = _product_match("i", "b", source_column)
	group_by = "b.item_code, i.item_name" + (", i.custom_source_configuration" if source_column else "")
	units = []
	for row in frappe.db.sql(
		f"""
		SELECT b.item_code, i.item_name, {source_select} AS source_configuration,
			SUM(b.actual_qty) AS on_hand,
			GROUP_CONCAT(DISTINCT b.warehouse ORDER BY b.warehouse SEPARATOR ', ') AS warehouses
		FROM `tabBin` b
		INNER JOIN `tabItem` i ON i.name = b.item_code
		WHERE b.actual_qty > 0 AND {product_where}
		GROUP BY {group_by}
		ORDER BY b.item_code
		""",
		{"pdt": _PDT_LIKE},
		as_dict=True,
	):
		line = po.product_line(row.item_code, row.source_configuration)
		if line is None:
			continue
		on_hand = flt(row.on_hand)
		claimed = None if committed is None else min(on_hand, flt(committed.get(row.item_code)))
		units.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"line": line,
				"on_hand": on_hand,
				"committed": claimed,
				"free": None if claimed is None else on_hand - claimed,
				"warehouses": row.warehouses,
			}
		)

	group_line = {group: line for line, groups in po.COMPONENT_GROUPS.items() for group in groups}
	components = []
	for row in frappe.db.sql(
		"""
		SELECT i.name AS item_code, i.item_name, i.item_group, i.stock_uom,
			(SELECT COALESCE(SUM(b.actual_qty), 0) FROM `tabBin` b WHERE b.item_code = i.name) AS on_hand,
			(SELECT COALESCE(SUM(b.ordered_qty), 0) FROM `tabBin` b WHERE b.item_code = i.name) AS on_order,
			(
				SELECT COALESCE(SUM(r.warehouse_reorder_level), 0) FROM `tabItem Reorder` r
				WHERE r.parent = i.name AND r.parenttype = 'Item'
			) AS reorder_level
		FROM `tabItem` i
		WHERE i.item_group IN %(groups)s AND i.disabled = 0
		ORDER BY i.name
		""",
		{"groups": tuple(sorted(group_line)) or ("",)},
		as_dict=True,
	):
		components.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"item_group": row.item_group,
				"uom": row.stock_uom,
				"line": group_line.get(row.item_group, po.LINE_OTHER),
				"on_hand": flt(row.on_hand),
				"on_order": flt(row.on_order),
				"reorder_level": flt(row.reorder_level),
				"state": po.component_state(row.on_hand, row.reorder_level),
			}
		)

	return {
		"units": units,
		"components": components,
		"component_groups": {line: list(groups) for line, groups in po.COMPONENT_GROUPS.items()},
	}


def _product_projects(today):
	"""Active Projects of type Products: the custom-engineered control panels."""
	projects = frappe.get_all(
		"Project",
		filters={
			"project_type": PRODUCTS_PROJECT_TYPE,
			"is_active": "Yes",
			"status": ["not in", ["Completed", "Canceled"]],
		},
		fields=[
			"name",
			"project_name",
			"customer",
			"status",
			"percent_complete",
			"expected_start_date",
			"expected_end_date",
		],
	)
	today_iso = str(today)
	rows = []
	for project in projects:
		end = _iso(project.get("expected_end_date"))
		percent = flt(project.get("percent_complete"))
		rows.append(
			{
				"name": project.get("name"),
				"project_name": project.get("project_name") or project.get("name"),
				"customer": project.get("customer"),
				"status": project.get("status"),
				"percent_complete": percent,
				"expected_start_date": _iso(project.get("expected_start_date")),
				"expected_end_date": end,
				"late": bool(end) and end < today_iso and percent < 100,
				"line": po.LINE_PANELS,
			}
		)
	# Soonest end first; undated last.
	rows.sort(key=lambda p: (p["expected_end_date"] or "9999-12-31", p["project_name"] or ""))
	return {"rows": rows}

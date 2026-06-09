"""Project Dashboard Settings doctype controller.

A Single doctype holding the legacy access-control list for the custom Project
Dashboard desk page: the ``permitted_roles`` child table (rows of "Project
Dashboard Permitted Role"). The page's server-side ``check_permission`` (see
page/project_dashboard/project_dashboard.py) prefers native Page role permissions
(Custom Role / Has Role) and only falls back to these settings when no Page roles
are configured. No custom controller logic is required.
"""

import frappe
from frappe.model.document import Document

class ProjectDashboardSettings(Document):
	"""Controller for the Project Dashboard Settings Single doctype (no custom logic)."""
	pass

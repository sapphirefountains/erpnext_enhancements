"""Project Dashboard Permitted Role doctype controller.

Child-table doctype: a single ``role`` Link (to Role). Rows live in the
``permitted_roles`` table of the "Project Dashboard Settings" Single and define
which roles may open the custom Project Dashboard page when native Page role
permissions are not configured (see project_dashboard.py ``check_permission``).
No custom controller logic is required.
"""

import frappe
from frappe.model.document import Document

class ProjectDashboardPermittedRole(Document):
	"""Controller for the Project Dashboard Permitted Role child table (no custom logic)."""
	pass

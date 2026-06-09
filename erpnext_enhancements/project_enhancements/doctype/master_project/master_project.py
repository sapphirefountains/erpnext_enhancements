# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Master Project doctype controller.

A Master Project is a lightweight container that groups several ordinary
``Project`` records together (a program / portfolio). Projects opt into a Master
Project through the ``Project.custom_master_project`` Link field (there is no
child table on this side); ``custom_subproject_order`` on each Project controls
their ordering under the master.

The form (master_project.js, loaded as the doctype's standard controller JS)
renders read-only HTML tables of the member Projects and their Tasks, fed by the
``get_projects_and_tasks`` method below. The Project Dashboard page reuses the
same grouping via project_dashboard.py's ``get_master_project_projects`` /
``update_master_project_structure``.
"""

import frappe
from frappe.model.document import Document


class MasterProject(Document):
	"""Controller for the Master Project doctype (groups Projects into a program)."""

	@frappe.whitelist()
	def get_projects_and_tasks(self):
		"""Return the member Projects and all their Tasks for the form's HTML tables.

		Called from master_project.js (``frm.call`` on form refresh). Finds every
		Project whose ``custom_master_project`` points at this master, then bulk-fetches
		all Tasks belonging to those Projects.

		Returns:
		    dict: ``{"projects": [...], "tasks": [...]}`` where each list holds the
		    field subsets used by the rendered tables. Both lists are empty when no
		    Project is linked to this Master Project.
		"""
		projects = frappe.get_all(
			"Project",
			filters={"custom_master_project": self.name},
			fields=["name", "project_name", "status", "priority", "percent_complete", "expected_end_date"],
		)

		if not projects:
			return {"projects": [], "tasks": []}

		project_names = [p["name"] for p in projects]

		tasks = frappe.get_all(
			"Task",
			filters={"project": ["in", project_names]},
			fields=["name", "subject", "status", "project", "progress", "exp_end_date"],
		)

		return {"projects": projects, "tasks": tasks}

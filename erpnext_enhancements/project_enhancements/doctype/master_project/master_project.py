# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MasterProject(Document):
	@frappe.whitelist()
	def get_projects_and_tasks(self):
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

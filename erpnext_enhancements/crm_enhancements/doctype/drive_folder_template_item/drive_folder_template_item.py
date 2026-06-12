# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Child row of Project Folder Google Drive Settings: one subfolder path to
create inside every new project folder (optionally limited to a project
type). Consumed by ``drive_utils.provision_project_folders``."""

from frappe.model.document import Document


class DriveFolderTemplateItem(Document):
	pass

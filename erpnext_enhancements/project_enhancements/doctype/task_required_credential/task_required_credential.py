# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One qualification a Task needs (`Task.custom_required_credentials`).

Exists only so `Task` can carry a Table MultiSelect of Credential Types: that fieldtype needs a
child doctype whose single Link field points at the thing being picked. No logic -- the Project
Planner reads the rows to show which people on a crew hold the credential, and never blocks on it.

The class is named for Frappe's derivation, `doctype.replace(" ", "")`: `TaskRequiredCredential`.
"""

from frappe.model.document import Document


class TaskRequiredCredential(Document):
	pass

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One person on a Task's crew (`Task.custom_crew`).

A child table with no logic of its own. De-duplication, the single lead and the hours checks are
done once per save on the Task by `project_enhancements/crew_sync.validate_crew`, and the mirror
into ordinary assignments (ToDos) by `crew_sync.on_task_update`. Keeping them on the parent means
a crew cannot be half-validated by whichever row happens to save first.

The class is named for Frappe's derivation, `doctype.replace(" ", "")`: `TaskCrewMember`.
"""

from frappe.model.document import Document


class TaskCrewMember(Document):
	pass

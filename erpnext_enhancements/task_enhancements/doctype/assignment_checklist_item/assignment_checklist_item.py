# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One step of an assignment's checklist (WI-081 Phase 3).

Child table of ToDo, through the Custom Field ``ToDo.custom_checklist``: an assignment can carry
the small steps it takes ("drain the basin", "photo of the pump label", "send the readings"), ticked
off on My Work, on the record's banner or on the ToDo form. A repeating assignment copies its steps,
unticked, onto the next one (``task_enhancements/recurrence.py``).

No logic of its own: ``done_by`` and ``done_on`` are stamped by the endpoint that ticks a step
(``api/my_work.checklist_toggle``), so a tick from the ToDo form's grid carries no name.

The class is named for Frappe's derivation, ``doctype.replace(" ", "")``: ``AssignmentChecklistItem``.
"""

from frappe.model.document import Document


class AssignmentChecklistItem(Document):
	pass

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One vehicle or asset booked on a Task (`Task.custom_equipment`, Project Planner Phase 4).

A child table with no logic of its own. One row per vehicle/asset, the link matching the row's
type, and the `label` (the asset's `asset_name`; a Fleet Vehicle is named by its vehicle name) are
settled once per save on the Task by `project_enhancements/planner_tracking.validate_equipment`.
A single `fetch_from` cannot serve both links, which is why the label is filled there. Whether a
vehicle or asset clashes with another task, is in the shop, or is out on an Asset Booking is the
availability engine's question (`crew_availability.equipment_conflicts`), asked on the planner.

The class is named for Frappe's derivation, `doctype.replace(" ", "")`: `TaskEquipment`.
"""

from frappe.model.document import Document


class TaskEquipment(Document):
	pass

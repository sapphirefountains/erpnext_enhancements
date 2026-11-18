# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One row of a Work Order's build checklist (``Work Order.custom_build_steps``).

Copied from the unit's Product Configuration when the Work Order is made
(``product_fulfillment.build.fill_checklist``) and ticked from the Build Checklist page
(``product_fulfillment.checklist.tick``). A copy, not a link: a step edited on the product later
never changes what a unit already being built was told to do.
"""

from frappe.model.document import Document


class WorkOrderBuildStep(Document):
	pass

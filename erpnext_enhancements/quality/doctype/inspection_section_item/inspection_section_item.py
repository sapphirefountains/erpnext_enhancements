# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One check on a reusable section.

Controller-free: key minting, duplicate rejection and range validation all live on
``Inspection Section``, the parent, for the reason its docstring gives.
"""

from frappe.model.document import Document


class InspectionSectionItem(Document):
	pass

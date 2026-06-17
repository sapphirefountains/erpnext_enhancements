# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Child row of a Document Intake holding one advisory match candidate (a
Purchase Order / Material Request / Sales Invoice etc.) with its fuzzy score
and tier. The reviewer selects at most one; matching never blocks posting."""

from frappe.model.document import Document


class DocumentIntakeMatch(Document):
	pass

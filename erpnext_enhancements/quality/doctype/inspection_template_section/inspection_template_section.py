# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One section's placement on a template.

Controller-free on purpose. Everything worth enforcing — no section listed twice, the revision
bump when the composition changes — belongs to the parent, because a child row's own validate
does not fire on every path by which the parent is saved, and a rule enforced in two places is
a rule enforced in neither.
"""

from frappe.model.document import Document


class InspectionTemplateSection(Document):
	pass

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One item of PPE on a hazard assessment.

No logic. The two decisions live in the JSON descriptions and are worth reading
there: the protection is named specifically (\"chemical splash goggles\", not
\"eye protection\", because a generic word is one everybody satisfies with whatever
they already have), and each row states the hazard it is for -- PPE with no stated
hazard is PPE somebody talks themselves out of on a hot afternoon.

Present because `bench migrate` calls `load_doctype_module()` for every DocType
including child tables.
"""

from frappe.model.document import Document


class PPERequirement(Document):
	pass

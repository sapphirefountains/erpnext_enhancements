# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One energy source on a lockout procedure.

No logic — the parent refuses a row with no verification method, which is the only
rule worth enforcing here and belongs where the whole set can be seen.

Present because `bench migrate` calls `load_doctype_module()` for every DocType
including child tables, and raises `ModuleNotFoundError` without it.
"""

from frappe.model.document import Document


class LockoutEnergySource(Document):
	pass

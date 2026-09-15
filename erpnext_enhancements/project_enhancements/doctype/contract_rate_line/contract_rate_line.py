# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One line of an MSA's rate schedule.

Controller-free on purpose. Everything worth enforcing -- that two lines are not in force at
once for the same classification, that a Statement of Work's frozen snapshot still matches --
belongs to the parent, because a child row's own validate does not fire on every path by which
the parent is saved, and a rule enforced in two places is a rule enforced in neither.
"""

from frappe.model.document import Document


class ContractRateLine(Document):
	pass

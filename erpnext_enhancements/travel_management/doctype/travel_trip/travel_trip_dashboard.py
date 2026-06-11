# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

from frappe import _


def get_data():
	"""Travel Trip form connections: every linked doctype carries a
	``custom_travel_trip`` back-link Custom Field (see fixtures/custom_field.json)."""
	return {
		"fieldname": "custom_travel_trip",
		"transactions": [
			{"label": _("Money"), "items": ["Expense Claim", "Employee Advance"]},
			{"label": _("Operations & Outcomes"), "items": ["Vehicle Log", "Lead", "Opportunity"]},
		],
	}

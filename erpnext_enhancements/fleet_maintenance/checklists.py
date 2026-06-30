"""Default per-cadence checklists for the Vehicle Maintenance Log.

The keys MUST match the ``maintenance_type`` Select options on
``Vehicle Maintenance Log``. The form's client script fetches the matching list
when a type is picked and fills the checklist grid (see
``vehicle_maintenance_log.js``). ``is_mandatory`` rows block submit until they
have a status (enforced server-side in the log's ``before_submit``).

These are the standard items from the fleet maintenance schedule; crew can add
extra rows on any individual log without changing this default.
"""

import frappe

CHECKLISTS = {
	"Weekly": [
		{"task": "Check & restock vehicle inventory/stock", "is_mandatory": 0},
		{"task": "Check engine oil level", "is_mandatory": 1},
		{"task": "Check windshield washer fluid", "is_mandatory": 1},
		{"task": "Check tire pressure (all tires)", "is_mandatory": 1},
		{"task": "Car wash (exterior)", "is_mandatory": 0},
		{"task": "Interior cleaning", "is_mandatory": 0},
	],
	"Oil Change (3-Month)": [
		{"task": "Engine oil & filter changed", "is_mandatory": 1},
		{"task": "Reset oil-life / maintenance indicator", "is_mandatory": 0},
		{"task": "Check other fluid levels", "is_mandatory": 0},
	],
	"Dealership Check-Up (6-Month)": [
		{"task": "Dealership inspection / service completed", "is_mandatory": 1},
		{"task": "Review & note any dealership recommendations", "is_mandatory": 0},
	],
	"Windshield Wipers (6-Month)": [
		{"task": "Windshield wiper blades replaced", "is_mandatory": 1},
	],
	"Other / Repair": [],
}


@frappe.whitelist()
def get_default_checklist(maintenance_type):
	"""Return the standard checklist rows for a maintenance type (or []).

	Read-only lookup of a static table — safe for any authenticated desk user
	(it returns no document data)."""
	return CHECKLISTS.get(maintenance_type, [])

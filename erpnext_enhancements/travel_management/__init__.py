"""Travel Management module — crew trips, logistics, per-diem/mileage and
Expense Claim / Employee Advance integration. See README.md in this package."""

# Roles that see and manage every Travel Trip; everyone else is scoped to
# trips they own or are travelling on (see permissions.py).
TRAVEL_COORDINATOR_ROLES = frozenset({"System Manager", "HR Manager", "Travel Coordinator"})

# Allowed targets for the trip-level "Travel For" dynamic link.
TRAVEL_FOR_DOCTYPES = ("Project", "Opportunity", "Lead", "Customer")

# Allowed targets for the per-agenda-stop related-party dynamic link.
RELATED_PARTY_DOCTYPES = ("Customer", "Lead", "Opportunity", "Contact", "Supplier", "Project")

# Travel Trip child-table fieldname -> child doctype, for the four
# cost-bearing tables that share the paid_by/expense_claim cost block.
COST_TABLES = {
	"flights": "Trip Flight",
	"accommodations": "Trip Accommodation",
	"ground_transport": "Trip Ground Transport",
	"other_costs": "Trip Expense",
}


def expense_claims_available() -> bool:
	"""True when the HR module (Frappe HR / ``hrms``) is installed.

	The travel *finance* surfaces — Expense Claim / Employee Advance / Vehicle
	Log generation and the Expense Claim Type mapping in Travel Settings — live
	on HRMS doctypes. ``hrms`` is an *optional* dependency: when it is absent
	these surfaces degrade gracefully (the create buttons and the Expense Claim
	Types section hide, the create endpoints throw a clear error) instead of
	hard-failing. Everything else — itinerary, logistics, per-diem and mileage
	rates — works without it.

	We probe the ``Expense Claim Type`` doctype rather than the installed-apps
	list: it is the exact thing the link fields resolve against, and a stored
	value pointing at a missing ``Expense Claim Type`` is what 404s the Travel
	Settings form (Frappe core tries to fetch the link title on load)."""
	import frappe

	return bool(frappe.db.exists("DocType", "Expense Claim Type"))

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

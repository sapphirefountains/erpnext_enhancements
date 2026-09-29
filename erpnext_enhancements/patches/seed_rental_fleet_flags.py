"""Mark the existing rental fleet as bookable on Rental Bookings (v1.562.0).

v1.562.0 gives every fountain in the rental fleet its own booking calendar, and a fountain
takes part only when ``Asset.custom_rentable`` ("Available for Event Rental") is ticked.
On the deploy that adds the flag it is 0 on every Asset, so without this patch the fleet
would be invisible to the booking form on day one: nothing to pick, and no error to say why.

The fleet is the Assets in the **Rental Fountain Fleet** category (seeded by
``seed_rental_asset_setup``; 10 Assets on production at v1.459.0), less anything scrapped
or sold. They also get the default 24-hour cleaning turnaround. That is keyed on the rule
("a fleet fountain turns around in a day"), not on the value being empty: the column is
created in this very migrate, so every value in it is the schema's, not a person's.

Ordering: patches run **before** fixture sync, so the three Custom Fields do not exist yet
when this executes. They are created here with the exact fixture definitions and
``is_system_generated=False``; fixture sync then adopts the same records by name later in the
migrate (the ``backfill_stage_changed_on`` precedent).

Runs once. An Asset someone unticks afterwards stays unticked. Safe on a site with no
category or no Assets: it creates the fields and updates nothing.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

FLEET_CATEGORY = "Rental Fountain Fleet"
DEFAULT_TURNAROUND_HOURS = 24

FIELDS = (
	{
		"fieldname": "custom_rentable",
		"fieldtype": "Check",
		"label": "Available for Event Rental",
		"insert_after": "custom_rental_status",
		"default": "0",
		"allow_on_submit": 1,
		"in_standard_filter": 1,
		"description": "Offer this fountain on Rental Bookings. It then has its own booking calendar.",
	},
	{
		"fieldname": "custom_rental_prep_hours",
		"fieldtype": "Int",
		"label": "Rental Prep Hours",
		"insert_after": "custom_rentable",
		"default": "0",
		"allow_on_submit": 1,
		"non_negative": 1,
		"depends_on": "custom_rentable",
		"description": "Hours before delivery the fountain is held for loading and checks.",
	},
	{
		"fieldname": "custom_rental_turnaround_hours",
		"fieldtype": "Int",
		"label": "Rental Turnaround Hours",
		"insert_after": "custom_rental_prep_hours",
		"default": "24",
		"allow_on_submit": 1,
		"non_negative": 1,
		"depends_on": "custom_rentable",
		"description": "Hours after take-down the fountain is held for cleaning before it can go out again.",
	},
)


def execute():
	for field in FIELDS:
		if not frappe.db.exists("Custom Field", f"Asset-{field['fieldname']}"):
			create_custom_field("Asset", dict(field), is_system_generated=False)

	if not frappe.db.exists("Asset Category", FLEET_CATEGORY):
		return

	frappe.db.sql(
		"""
		update `tabAsset`
		set custom_rentable = 1, custom_rental_turnaround_hours = %(turnaround)s
		where asset_category = %(category)s
			and docstatus < 2
			and status not in ('Scrapped', 'Sold')
		""",
		{"category": FLEET_CATEGORY, "turnaround": DEFAULT_TURNAROUND_HOURS},
	)

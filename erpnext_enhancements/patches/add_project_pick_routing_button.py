"""One-time migration patch (post_model_sync; listed in patches.txt).

Adds the Project Budget tab's "Material Pickup" section and its **Pick Routing
Map** button, which opens the supplier pick-up run
(``public/js/project_enhancements/pick_routing_map.js`` ->
``api.pickup_routing.get_pickup_route_data``).

Its own section rather than a seventh button in the Procurement grid: those six
all *create* a document and sit in a deliberate 3x2 layout
(``reorder_procurement_buttons``); this one opens a read-only view and would
otherwise make one column taller than the others.

Also seeds ``pickup_route_start_address`` on ``ERPNext Enhancements Settings``.
The field carries the same default, but a field default only applies when the
Single is first created and it already exists on every live site -- the same trap
``seed_contract_esign_defaults`` documents. Fill-blanks-only, so an operator who
has already moved the shop keeps their value.

Idempotent via ``create_custom_fields(..., update=True)``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_enhancements.api.pickup_routing import DEFAULT_DEPOT_ADDRESS

FIELDNAME = "pickup_route_start_address"


def execute():
	create_custom_fields(
		{
			"Project": [
				{
					"fieldname": "custom_pickup_routing_section",
					"label": "Material Pickup",
					"fieldtype": "Section Break",
					"insert_after": "custom_btn_purchase_invoice",
					"collapsible": 0,
				},
				{
					"fieldname": "custom_btn_pick_routing_map",
					"label": "Pick Routing Map",
					"fieldtype": "Button",
					"insert_after": "custom_pickup_routing_section",
					"description": (
						"Every supplier on this job with material still to collect, in the "
						"shortest driving order out of the shop and back."
					),
				},
			]
		},
		update=True,
	)

	_seed_depot_address()


def _seed_depot_address():
	"""Fill a blank ``pickup_route_start_address`` with the shop address.

	``set_single_value`` rather than ``doc.save()`` on purpose: saving the Single
	runs its ``validate``, which fails closed when the public fountain-move form
	is published without a Turnstile secret -- unrelated config that must never
	be able to abort a migrate.
	"""
	if not frappe.get_meta("ERPNext Enhancements Settings").has_field(FIELDNAME):
		# Fresh install: patches run before the doctype JSON has synced, and the
		# field default covers that case anyway.
		return
	if (frappe.db.get_single_value("ERPNext Enhancements Settings", FIELDNAME) or "").strip():
		return
	frappe.db.set_single_value("ERPNext Enhancements Settings", FIELDNAME, DEFAULT_DEPOT_ADDRESS)
	frappe.db.commit()

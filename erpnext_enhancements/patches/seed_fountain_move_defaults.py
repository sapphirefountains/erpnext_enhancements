"""One-time migration patch (post_model_sync; listed in patches.txt).

Seeds the Fountain Move Intake settings so an operator turning the feature on
finds sensible values rather than a screen of blank mandatory-ish Links.

Why a patch and not field defaults: ``default`` on a Link field is only applied
when a document is *created*, and ERPNext Enhancements Settings is a Single that
already exists on every site. New fields on an existing Single come up empty.

Seeded (each only when currently blank, so operator edits are never clobbered):

* ``fmr_lead_source``   → "Cactus & Tropicals" (seeded by the sibling patch)
* ``fmr_value_stream``  → "Service"
* ``fmr_territory``     → "Utah" — all three C&T stores are in Utah
* ``fmr_customer_group_residential`` / ``_commercial`` → the best available leaf
* ``fmr_company``       → the global default company
* ``fountain_move_locations`` → the three Cactus & Tropicals stores

Deliberately NOT seeded: ``fmr_default_owner``. There is no defensible way to
guess who owns inbound leads, and a wrong guess silently routes real customers
to the wrong person. Conversion fails loudly with a readable error until an
operator sets it, which is the correct failure — the pre-flight checklist in
``crm_enhancements/README.md`` calls it out.

Customer Group selection deliberately avoids the "first leaf" trap documented in
``api/telephony.py::_default_customer_group``: an arbitrary fallback once stamped
every auto-created caller as "Government". We only accept a group whose name we
actually recognise, and leave the field blank otherwise — the conversion engine
inserts Customers with ``ignore_mandatory`` so blank is a working state.
"""

import frappe

from erpnext_enhancements.crm_enhancements.fountain_move import (
	CT_LOCATIONS,
	DEFAULT_LEAD_SOURCE,
	DEFAULT_VALUE_STREAM,
)

#: Preference order for the Residential / Commercial customer groups. The first
#: name that exists as a non-group node wins; nothing is invented.
RESIDENTIAL_GROUP_CANDIDATES = ("Individual", "Residential")
COMMERCIAL_GROUP_CANDIDATES = ("Commercial", "Corporate")


def execute():
	if not frappe.db.exists("DocType", "ERPNext Enhancements Settings"):
		return

	meta = frappe.get_meta("ERPNext Enhancements Settings")
	if not meta.has_field("fmr_lead_source"):
		# Doctype JSON has not synced yet on this site; the next migrate re-runs
		# nothing, so log loudly rather than failing silently.
		frappe.log_error(
			"Fountain Move settings fields absent at patch time — "
			"seed_fountain_move_defaults did nothing. Re-run it manually after migrate.",
			"Fountain Move: defaults not seeded",
		)
		return

	settings = frappe.get_single("ERPNext Enhancements Settings")
	dirty = False

	dirty |= _set_if_blank(settings, "fmr_lead_source", _existing("Lead Source", DEFAULT_LEAD_SOURCE))
	dirty |= _set_if_blank(
		settings, "fmr_value_stream", _existing("Value Streams", DEFAULT_VALUE_STREAM)
	)
	dirty |= _set_if_blank(settings, "fmr_territory", _existing("Territory", "Utah"))
	dirty |= _set_if_blank(
		settings, "fmr_customer_group_residential", _first_leaf_group(RESIDENTIAL_GROUP_CANDIDATES)
	)
	dirty |= _set_if_blank(
		settings, "fmr_customer_group_commercial", _first_leaf_group(COMMERCIAL_GROUP_CANDIDATES)
	)
	dirty |= _set_if_blank(settings, "fmr_company", frappe.defaults.get_defaults().get("company"))
	dirty |= _seed_locations(settings)

	if dirty:
		settings.save(ignore_permissions=True)


def _set_if_blank(settings, fieldname, value):
	"""Assign ``value`` only when the field is currently empty. Returns True if changed."""
	if not value or settings.get(fieldname):
		return False
	settings.set(fieldname, value)
	return True


def _existing(doctype, name):
	"""``name`` if that record exists, else None — never invent taxonomy rows here."""
	if not frappe.db.exists("DocType", doctype):
		return None
	return name if frappe.db.exists(doctype, name) else None


def _first_leaf_group(candidates):
	"""First candidate that exists as a LEAF Customer Group.

	v16 rejects group nodes outright ("Cannot select a Group type Customer
	Group"), so a group-typed match is as useless as no match.
	"""
	if not frappe.db.exists("DocType", "Customer Group"):
		return None
	for name in candidates:
		if frappe.db.exists("Customer Group", name) and not frappe.db.get_value(
			"Customer Group", name, "is_group"
		):
			return name
	return None


def _seed_locations(settings):
	"""Populate the partner-store child table when empty. Returns True if changed."""
	if settings.get("fountain_move_locations"):
		return False
	for location in CT_LOCATIONS:
		settings.append("fountain_move_locations", dict(location))
	return True

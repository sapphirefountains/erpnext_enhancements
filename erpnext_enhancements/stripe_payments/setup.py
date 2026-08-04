# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""after_migrate setup for Stripe Payments.

Idempotently creates the back-reference custom fields the integration writes to
(Stripe ids on Customer / Sales Invoice / Payment Entry) and the two Modes of
Payment used when posting (Stripe for cards, ACH for bank debit), defaulting the
Settings link fields to them on first run. Wired in hooks.py ``after_migrate``,
mirroring ``accounting_intake.setup``.

**These fields are `is_system_generated`, so they are NOT in `fixtures/`.** The
fixture export filters on ``is_system_generated = 0``; this file is their only
source of truth. Change a label or a position here, not in the UI, or the next
migrate will put it back.

``create_custom_fields`` defaults to ``update=True``, so editing a definition
below re-applies to existing sites on the next migrate — which is how the Customer
fields were moved into their own section in v1.242.0 rather than staying wherever
they first landed.

**Ordering dependency:** ``custom_stripe_section`` anchors on
``custom_attribution_captured_on``, a fixture-managed field. That is safe because
``sync_fixtures`` runs during migrate and this is an ``after_migrate`` hook, so the
anchor always exists by the time this runs. If the attribution fields are ever
removed, re-anchor this section rather than leaving it pointing at nothing — a
Custom Field whose ``insert_after`` does not resolve is stranded at the bottom of
the last tab, silently.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_stripe_custom_fields():
	"""Stripe back-reference fields on Customer / Sales Invoice / Payment Entry."""
	create_custom_fields(
		{
			"Customer": [
				# All five Customer fields live in their own collapsed section as of
				# v1.242.0. They used to hang off `customer_primary_contact`, which
				# dropped four read-only integration fields into the MIDDLE of the
				# identity block — the first thing anyone sees when they open an
				# account was a Stripe id nobody types. The section break is what
				# moves them; the chain below just keeps them together.
				{
					"fieldname": "custom_stripe_section",
					"label": "Stripe",
					"fieldtype": "Section Break",
					"insert_after": "custom_attribution_captured_on",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_stripe_customer_id",
					"label": "Stripe Customer ID",
					"fieldtype": "Data",
					"insert_after": "custom_stripe_section",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_stripe_default_payment_method",
					"label": "Stripe Saved Payment Method",
					"fieldtype": "Data",
					"insert_after": "custom_stripe_customer_id",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_stripe_column_break",
					"fieldtype": "Column Break",
					"insert_after": "custom_stripe_default_payment_method",
				},
				{
					"fieldname": "custom_stripe_payment_method_label",
					"label": "Saved Method",
					"fieldtype": "Data",
					"insert_after": "custom_stripe_column_break",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_stripe_autopay_enabled",
					"label": "Stripe Autopay Enabled",
					"fieldtype": "Check",
					"insert_after": "custom_stripe_payment_method_label",
					"no_copy": 1,
					"print_hide": 1,
					"description": "Auto-charge the saved method when this customer's invoices are submitted.",
				},
				{
					"fieldname": "custom_service_hold",
					"label": "Service Hold",
					"fieldtype": "Check",
					"insert_after": "custom_stripe_autopay_enabled",
					"no_copy": 1,
					"print_hide": 1,
					"description": "Pause maintenance visit generation for this customer. Set automatically when declined-card dunning is exhausted; clear manually once payment is resolved to resume service.",
				},
			],
			"Sales Invoice": [
				{
					"fieldname": "custom_stripe_payment_status",
					"label": "Stripe Payment Status",
					"fieldtype": "Select",
					"options": "\nUnpaid\nLink Sent\nProcessing\nPaid\nFailed",
					"insert_after": "status",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_stripe_payment_link",
					"label": "Stripe Payment Link",
					"fieldtype": "Small Text",
					"insert_after": "custom_stripe_payment_status",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_dunning_state",
					"label": "Dunning State",
					"fieldtype": "Select",
					"options": "\nActive\nRecovered\nExhausted\nCancelled",
					"insert_after": "custom_stripe_payment_link",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
					"description": "Declined-card dunning cycle for this invoice's auto-charge.",
				},
				{
					"fieldname": "custom_dunning_attempts",
					"label": "Dunning Attempts",
					"fieldtype": "Int",
					"insert_after": "custom_dunning_state",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_dunning_next_retry",
					"label": "Dunning Next Retry",
					"fieldtype": "Date",
					"insert_after": "custom_dunning_attempts",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_dunning_opened_on",
					"label": "Dunning Opened On",
					"fieldtype": "Date",
					"insert_after": "custom_dunning_next_retry",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_dunning_last_attempt",
					"label": "Dunning Last Attempt",
					"fieldtype": "Datetime",
					"insert_after": "custom_dunning_opened_on",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_dunning_last_error",
					"label": "Dunning Last Error",
					"fieldtype": "Small Text",
					"insert_after": "custom_dunning_last_attempt",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
				},
			],
			"Payment Entry": [
				{
					"fieldname": "custom_stripe_payment_intent",
					"label": "Stripe Payment Intent",
					"fieldtype": "Data",
					"insert_after": "reference_no",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
				{
					"fieldname": "custom_stripe_charge_id",
					"label": "Stripe Charge ID",
					"fieldtype": "Data",
					"insert_after": "custom_stripe_payment_intent",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
			],
		},
		ignore_validate=True,
	)
	frappe.db.commit()


def create_stripe_modes_of_payment():
	"""Create the 'Stripe' and 'ACH' Modes of Payment and default Settings to them.

	Existing site modes (Bank Draft, Wire Transfer, Credit Card, Cash, Check) don't
	cleanly distinguish Stripe card vs ACH settlement, so the integration uses its
	own. Accounts are left unmapped — the reconciler sets ``paid_to`` explicitly.
	"""
	for name in ("Stripe", "ACH"):
		if not frappe.db.exists("Mode of Payment", name):
			frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": name, "type": "Bank"}).insert(
				ignore_permissions=True
			)

	# Default the Settings link fields on first run only (never clobber a choice).
	if not frappe.db.get_single_value("Stripe Payments Settings", "card_mode_of_payment"):
		frappe.db.set_single_value("Stripe Payments Settings", "card_mode_of_payment", "Stripe")
	if not frappe.db.get_single_value("Stripe Payments Settings", "ach_mode_of_payment"):
		frappe.db.set_single_value("Stripe Payments Settings", "ach_mode_of_payment", "ACH")
	frappe.db.commit()

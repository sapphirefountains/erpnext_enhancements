# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""after_migrate setup for Stripe Payments.

Idempotently creates the back-reference custom fields the integration writes to
(Stripe ids on Customer / Sales Invoice / Payment Entry) and the two Modes of
Payment used when posting (Stripe for cards, ACH for bank debit), defaulting the
Settings link fields to them on first run. Wired in hooks.py ``after_migrate``,
mirroring ``accounting_intake.setup``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_stripe_custom_fields():
	"""Stripe back-reference fields on Customer / Sales Invoice / Payment Entry."""
	create_custom_fields(
		{
			"Customer": [
				{
					"fieldname": "custom_stripe_customer_id",
					"label": "Stripe Customer ID",
					"fieldtype": "Data",
					"insert_after": "customer_primary_contact",
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
					"fieldname": "custom_stripe_payment_method_label",
					"label": "Saved Method",
					"fieldtype": "Data",
					"insert_after": "custom_stripe_default_payment_method",
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

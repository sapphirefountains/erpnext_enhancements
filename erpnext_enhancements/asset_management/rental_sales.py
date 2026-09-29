# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The sales side of an event rental: agreement, signing, invoices (v1.564.0).

1. **The Rental Agreement** is the existing Project Contract with ``template_key = "rental"``
   (``SF-RA-``). :func:`make_rental_agreement` builds one from a Rental Booking — dates, one
   equipment row per fountain and accessory, the fees and the security deposit — so nobody retypes
   the booking into the agreement. It is signed through the existing e-sign flow (``/contract-sign``).
2. **Signing confirms the booking.** :func:`on_rental_agreement_signed` (Project Contract
   ``on_submit`` / ``on_update_after_submit``) moves the booking to Confirmed, which firms its
   calendar legs. It runs inside the signer's transaction — and the signer is usually a Guest — so
   it can never be allowed to raise: a refusal (the fountains were taken by someone else while the
   agreement was out) is caught in a savepoint, logged, commented on the booking and sent to the
   booking's owner. The signature itself always stands.
3. **Invoices** are drafted, never posted, per Nik's call on 2026-09-29: the deposit when the booking
   becomes Confirmed, the balance ``balance_days_before_delivery`` days before delivery (daily
   sweep). :func:`submit_and_send` is the one click that posts one and emails the customer its pay
   link.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, nowdate

from erpnext_enhancements.asset_management import rental_rules as rules

RENTAL_TEMPLATE = "rental"
DEPOSIT = "Deposit"
BALANCE = "Balance"


def settings():
	return frappe.get_cached_doc("Rental Settings")


# ---------------------------------------------------------------- agreement


@frappe.whitelist()
def make_rental_agreement(booking):
	"""Create (or return) the Rental Agreement for a booking, filled from it. Returns its name."""
	from erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract import (
		create_contract,
	)

	doc = frappe.get_doc("Rental Booking", booking)
	doc.check_permission("write")
	if doc.status in rules.FROZEN_STATUSES:
		frappe.throw(_("A {0} booking cannot get an agreement.").format(_(doc.status)))
	if doc.rental_agreement and frappe.db.get_value("Project Contract", doc.rental_agreement, "status") != "Void":
		return doc.rental_agreement

	name = create_contract(
		RENTAL_TEMPLATE,
		source_doctype="Project" if doc.project else None,
		source_name=doc.project or None,
		party=doc.customer,
	)
	contract = frappe.get_doc("Project Contract", name)
	fill_agreement(contract, doc)
	contract.save()
	doc.db_set("rental_agreement", contract.name)
	doc.add_comment("Comment", _("Rental Agreement {0} created from this booking.").format(contract.name))
	return contract.name


def fill_agreement(contract, booking):
	"""Copy the booking's dates, equipment and money onto a draft Rental Agreement."""
	contract.rental_start_date = getdate(booking.delivery_datetime)
	contract.rental_end_date = getdate(booking.takedown_datetime)
	contract.set("equipment_items", [])
	for row in booking.fountains or []:
		contract.append(
			"equipment_items",
			{"description": (row.asset_name or row.asset)[:140], "serial_id": row.asset, "notes": row.item_code or ""},
		)
	for row in booking.accessories or []:
		label = frappe.db.get_value("Rental Accessory Pool", row.pool, "pool_name") or row.pool
		contract.append("equipment_items", {"description": f"{cint(row.qty)} × {label}"[:140]})
	contract.base_rental_fee = rules.booking_total(
		[row.rate for row in booking.fountains or []],
		[(row.rate, row.qty) for row in booking.accessories or []],
		[],
	)
	contract.delivery_setup_fee = flt(booking.delivery_setup_fee)
	contract.pickup_removal_fee = flt(booking.pickup_removal_fee)
	contract.other_fee = flt(booking.other_fee)
	if flt(booking.other_fee) and not contract.get("other_fee_label"):
		contract.other_fee_label = _("Other fees")
	contract.security_deposit = flt(booking.security_deposit)
	if booking.contact_person and not contract.get("contact_person"):
		contract.contact_person = booking.contact_person
		contract.contact_email = frappe.db.get_value("Contact", booking.contact_person, "email_id")
	if booking.opportunity and not contract.get("opportunity"):
		contract.opportunity = booking.opportunity
	if booking.project and not contract.get("project"):
		contract.project = booking.project


def on_rental_agreement_signed(doc, method=None):
	"""Project Contract hook: a Rental Agreement that became Signed confirms its booking.

	Never raises (see the module docstring). Detects the transition the way the maintenance
	sibling does: Signed now, and not already Signed before this save.
	"""
	if doc.get("template_key") != RENTAL_TEMPLATE or doc.get("status") != "Signed":
		return
	if method == "on_update_after_submit":
		before = doc.get_doc_before_save()
		if before and before.get("status") == "Signed":
			return
	booking_name = frappe.db.get_value("Rental Booking", {"rental_agreement": doc.name}, "name")
	if not booking_name:
		return

	savepoint = "rental_agreement_signed"
	muted = frappe.flags.mute_messages
	frappe.db.savepoint(savepoint)
	try:
		frappe.flags.mute_messages = True
		confirm_booking(frappe.get_doc("Rental Booking", booking_name), reason=doc.name)
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=f"Rental Agreement {doc.name} signed; booking not confirmed", message=frappe.get_traceback())
		_flag_unconfirmed(booking_name, doc, exc)
	finally:
		frappe.flags.mute_messages = muted


def confirm_booking(booking, reason=None):
	"""Tentative → Confirmed (renewing an Expired hold first). Raises if the dates are gone."""
	booking.flags.ignore_permissions = True
	# Renewing on the way to Confirmed is not a hold the customer needs telling about.
	booking.flags.skip_hold_notice = True
	if booking.status == "Expired":
		booking.status = "Tentative"
		booking.save()
	if booking.status == "Tentative":
		booking.status = "Confirmed"
		booking.save()
		booking.add_comment(
			"Comment",
			_("Confirmed by the signed Rental Agreement {0}.").format(reason) if reason else _("Confirmed."),
		)


def _flag_unconfirmed(booking_name, contract, exc):
	message = _(
		"Rental Agreement {0} was signed, but this booking could not be confirmed: {1} "
		"The signature stands. Free the dates or swap fountains, then press Confirm."
	).format(contract.name, frappe.utils.strip_html(str(exc))[:300])
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Rental Booking",
				"reference_name": booking_name,
				"content": message,
			}
		).insert(ignore_permissions=True)
		owner = frappe.db.get_value("Rental Booking", booking_name, "owner")
		if owner and owner not in ("Administrator", "Guest"):
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": owner,
					"type": "Alert",
					"document_type": "Rental Booking",
					"document_name": booking_name,
					"subject": _("Signed, but not confirmed: {0}").format(booking_name),
					"email_content": message,
				}
			).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Rental: could not flag an unconfirmed booking", message=frappe.get_traceback())


# ---------------------------------------------------------------- invoices


def after_confirmed(booking):
	"""Rental Booking hook, on the save that makes it Confirmed: draft the deposit invoice.

	In its own savepoint, so a missing Rental Settings item never stops a booking being
	confirmed; the booking gets a comment saying what to fix, and Draft Deposit on the form retries.
	"""
	savepoint = "rental_deposit_draft"
	frappe.db.savepoint(savepoint)
	try:
		name = draft_invoice(booking, DEPOSIT)
		if name:
			booking.add_comment(
				"Comment", _("Deposit invoice {0} drafted. Submit & Send posts it and emails the pay link.").format(name)
			)
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=f"Rental deposit invoice not drafted for {booking.name}", message=frappe.get_traceback())
		booking.add_comment(
			"Comment",
			_("The deposit invoice could not be drafted: {0}").format(frappe.utils.strip_html(str(exc))[:300]),
		)


def draft_due_balance_invoices():
	"""Scheduler (daily): draft the balance invoice of every confirmed rental delivering soon."""
	days = cint(settings().balance_days_before_delivery)
	horizon = get_datetime(add_days(nowdate(), days + 1))
	for name in frappe.get_all(
		"Rental Booking",
		filters=[
			["status", "in", ["Confirmed", "Out"]],
			["delivery_datetime", "is", "set"],
			["delivery_datetime", "<", horizon],
			["balance_invoice", "is", "not set"],
		],
		pluck="name",
	):
		savepoint = "rental_balance_draft"
		frappe.db.savepoint(savepoint)
		try:
			booking = frappe.get_doc("Rental Booking", name)
			invoice = draft_invoice(booking, BALANCE)
			if invoice:
				booking.add_comment("Comment", _("Balance invoice {0} drafted.").format(invoice))
			frappe.db.commit()
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(title=f"Rental balance invoice not drafted for {name}", message=frappe.get_traceback())


def _existing_invoice(booking_name, kind):
	return frappe.db.get_value(
		"Sales Invoice",
		{"custom_rental_booking": booking_name, "custom_rental_invoice_kind": kind, "docstatus": ["<", 2]},
		"name",
	)


def invoice_amount(booking, kind, conf=None):
	"""The rental charge on a deposit or balance invoice (before tax, security deposit excluded)."""
	conf = conf or settings()
	total = flt(booking.total_amount)
	if kind == DEPOSIT:
		return round(total * flt(conf.deposit_percent) / 100, 2)
	deposit = _existing_invoice(booking.name, DEPOSIT)
	billed = 0
	if deposit:
		billed = sum(
			flt(r.amount)
			for r in frappe.get_all(
				"Sales Invoice Item", filters={"parent": deposit, "item_code": conf.rental_item}, fields=["amount"]
			)
		)
	return round(max(total - billed, 0), 2)


def draft_invoice(booking, kind):
	"""Draft (never submit) the deposit or balance invoice for ``booking``; return its name.

	Idempotent: an existing non-canceled invoice of that kind is returned as is. Returns None
	when there is nothing to bill. Raises with a sentence naming the missing setting otherwise.
	"""
	existing = _existing_invoice(booking.name, kind)
	if existing:
		_link(booking, kind, existing)
		return existing

	conf = settings()
	if not conf.rental_item:
		frappe.throw(_("Set the Rental Item in Rental Settings before rental invoices can be drafted."))
	amount = invoice_amount(booking, kind, conf)
	security = flt(booking.security_deposit) if kind == BALANCE else 0
	if amount <= 0 and security <= 0:
		return None
	if security and not (conf.security_deposit_item and conf.security_deposit_account):
		frappe.throw(
			_("This rental has a security deposit. Set the Security Deposit Item and Account in Rental Settings first.")
		)

	delivery = getdate(booking.delivery_datetime)
	today = getdate()
	if kind == DEPOSIT:
		due = add_days(today, cint(conf.deposit_due_days))
		label = _("Deposit ({0}%)").format(flt(conf.deposit_percent))
	else:
		due = max(delivery, today)
		label = _("Balance")
	event = booking.event_name or frappe.utils.formatdate(delivery)

	si = frappe.new_doc("Sales Invoice")
	si.customer = booking.customer
	si.company = (
		conf.company
		or (frappe.db.get_value("Project", booking.project, "company") if booking.project else None)
		or frappe.defaults.get_global_default("company")
	)
	si.posting_date = today
	si.set_posting_time = 1
	si.due_date = due
	si.project = booking.project
	si.contact_person = booking.contact_person
	si.custom_rental_booking = booking.name
	si.custom_rental_invoice_kind = kind
	if amount > 0:
		si.append(
			"items",
			{
				"item_code": conf.rental_item,
				"qty": 1,
				"rate": amount,
				"description": _("{0} for fountain rental {1} ({2})").format(label, booking.name, event),
			},
		)
	if security > 0:
		si.append(
			"items",
			{
				"item_code": conf.security_deposit_item,
				"qty": 1,
				"rate": security,
				"description": _("Refundable security deposit for rental {0}").format(booking.name),
			},
		)
	si.set_missing_values()
	for row in si.items:
		# The security deposit is a liability, not income: it is owed back after a clean return.
		# Set after set_missing_values, which would otherwise fill the item's income account.
		if row.item_code == conf.security_deposit_item and kind == BALANCE:
			row.income_account = conf.security_deposit_account
		if conf.cost_center:
			row.cost_center = conf.cost_center
	# Tax is Rental Settings' template or nothing (Nik, 2026-09-29, pending OD-2): never a party's
	# or company's default, which set_missing_values may have picked up.
	si.taxes_and_charges = conf.taxes_and_charges or None
	si.set("taxes", [])
	if si.taxes_and_charges:
		si.append_taxes_from_master()
	si.calculate_taxes_and_totals()
	si.flags.ignore_permissions = True
	si.insert()
	_link(booking, kind, si.name)
	return si.name


def _link(booking, kind, invoice):
	field = "deposit_invoice" if kind == DEPOSIT else "balance_invoice"
	if booking.get(field) != invoice:
		booking.db_set(field, invoice, update_modified=False)


@frappe.whitelist()
def draft_rental_invoice(booking, kind):
	"""The booking form's Draft Deposit / Draft Balance buttons."""
	if kind not in (DEPOSIT, BALANCE):
		frappe.throw(_("Unknown invoice kind."))
	doc = frappe.get_doc("Rental Booking", booking)
	doc.check_permission("write")
	if doc.status not in ("Confirmed", "Out", "Returned"):
		frappe.throw(_("Invoices are drafted once the booking is confirmed."))
	return draft_invoice(doc, kind)


@frappe.whitelist(methods=["POST"])
def submit_and_send(sales_invoice):
	"""Post a drafted rental invoice and email the customer its pay link: the one human click.

	The caller's own permission to submit Sales Invoices is the gate (no ignore_permissions). The
	draft's amount is refreshed from the booking first, so an edit made after drafting is billed.
	Customers on autopay get no link: submitting already charges their saved card
	(``saved_methods.auto_charge_on_invoice_submit``), and an open link would make that charge
	refuse itself.
	"""
	si = frappe.get_doc("Sales Invoice", sales_invoice)
	if not si.get("custom_rental_booking"):
		frappe.throw(_("{0} is not a rental invoice.").format(sales_invoice))
	si.check_permission("submit")
	booking = frappe.get_doc("Rental Booking", si.custom_rental_booking)
	if si.docstatus == 0:
		_refresh_draft(si, booking)
		si.submit()
	elif si.docstatus != 1:
		frappe.throw(_("{0} is canceled.").format(sales_invoice))

	if frappe.db.get_value("Customer", si.customer, "custom_stripe_autopay_enabled"):
		booking.add_comment(
			"Comment", _("{0} submitted; the customer's saved card is charged automatically.").format(si.name)
		)
		return {"submitted": si.name, "emailed": None, "autopay": True}

	from erpnext_enhancements.stripe_payments.core.checkout import create_payment
	from erpnext_enhancements.stripe_payments.core.utils import get_settings, is_enabled

	if not is_enabled(get_settings()):
		return {"submitted": si.name, "emailed": None, "reason": _("Stripe is off, so no pay link was sent.")}
	recipient = _recipient(booking)
	if not recipient:
		return {
			"submitted": si.name,
			"emailed": None,
			"reason": _("No email address on the booking's contact or the customer."),
		}
	link = create_payment(sales_invoice=si.name, channel="Desk")
	_email_pay_link(si, booking, link["checkout_url"], recipient)
	booking.add_comment("Comment", _("{0} submitted and its pay link emailed to {1}.").format(si.name, recipient))
	return {"submitted": si.name, "emailed": recipient}


def _refresh_draft(si, booking):
	kind = si.custom_rental_invoice_kind
	conf = settings()
	amount = invoice_amount(booking, kind, conf)
	changed = False
	for row in si.items:
		if row.item_code == conf.rental_item and flt(row.rate) != amount:
			row.rate = amount
			changed = True
		elif (
			kind == BALANCE
			and row.item_code == conf.security_deposit_item
			and flt(row.rate) != flt(booking.security_deposit)
		):
			row.rate = flt(booking.security_deposit)
			changed = True
	if changed:
		si.calculate_taxes_and_totals()
		si.save()


def _recipient(booking):
	if booking.contact_person:
		email = frappe.db.get_value("Contact", booking.contact_person, "email_id")
		if email:
			return email
	contact = frappe.db.get_value("Customer", booking.customer, "customer_primary_contact")
	return frappe.db.get_value("Contact", contact, "email_id") if contact else None


def _email_pay_link(si, booking, url, recipient):
	from erpnext_enhancements import email_style

	kind = _("deposit") if si.custom_rental_invoice_kind == DEPOSIT else _("balance")
	event = booking.event_name or _("your fountain rental")
	subject = _("Your {0} invoice for {1}").format(kind, event)
	body = (
		email_style.p(_("Hello,"))
		+ email_style.p(_("Here is the {0} invoice for {1}. You can pay it securely online.").format(kind, event))
		+ email_style.kv(
			[
				(_("Invoice"), si.name),
				(_("Amount"), frappe.utils.fmt_money(si.grand_total, currency=si.currency)),
				(_("Due"), frappe.utils.formatdate(si.due_date)),
				(_("Delivery"), frappe.utils.format_datetime(booking.delivery_datetime)),
			]
		)
		+ email_style.button(url, _("Pay now"))
		+ email_style.button_fallback(url)
		+ email_style.p(_("Thank you, Sapphire Fountains"))
	)
	frappe.sendmail(
		recipients=[recipient],
		subject=subject,
		message=email_style.wrap(body, title=subject, eyebrow=_("Rentals"), tagline=True, pillar="rent"),
		reference_doctype="Sales Invoice",
		reference_name=si.name,
	)

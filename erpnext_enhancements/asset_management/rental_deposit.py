# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Releasing a rental's security deposit (v1.566.0).

Nik's call, 2026-09-29: **drafts, then a one-click Stripe refund.** The deposit was collected as its
own line on the balance invoice, posted to a liability account (``rental_sales.draft_invoice``).
Giving it back is three documents, each posted by a person:

1. **A credit note** against the balance invoice for the deposit line only (``Deposit Return``). It
   takes the liability off the books and leaves the customer in credit.
2. **A damage-charge invoice** for any deduction (``Damage``), to income. Submitting it *is* approving
   the deduction; while it is a draft nothing is kept.
3. **The refund**: :func:`refund_deposit` refunds deposit minus deduction on the Stripe payment that
   paid the balance invoice. The existing ``charge.refunded`` webhook then drafts the reversing
   Payment Entry for accounts (``stripe_payments.core.reconcile._draft_refund_reversal``), exactly as
   for any Stripe refund. A balance paid by check is refunded by hand and recorded with
   :func:`mark_refunded`.

**When it starts.** Once every fountain on the rental has a submitted Return inspection
(:func:`after_return_inspection`): a clean return drafts the release straight away and gives the
booking's owner a to-do to approve it; any damage or shortfall instead gives them a to-do to review it
and set the deduction (**Release Deposit…** on the booking), because what a scuff costs is a person's
judgement, not a checklist's.

Refunding money is limited to the roles that may refund on Stripe already (System Manager, Accounts
Manager), and the amount can never exceed what that payment actually took.
"""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from erpnext_enhancements.asset_management.rental_sales import settings

DEPOSIT_RETURN = "Deposit Return"
DAMAGE = "Damage"
REFUND_ROLES = ("System Manager", "Accounts Manager")


def _booking_for_inspection(inspection):
	leg = inspection.get("asset_booking")
	return frappe.db.get_value("Asset Booking", leg, "rental_booking") if leg else None


def after_return_inspection(inspection):
	"""Rental Inspection on_submit (Return): start the release once every fountain is back and checked."""
	name = _booking_for_inspection(inspection)
	if not name:
		return
	booking = frappe.get_doc("Rental Booking", name)
	if flt(booking.security_deposit) <= 0 or booking.deposit_status not in (None, "", "Held"):
		return
	sheets = []
	for row in booking.fountains or []:
		sheet = frappe.db.get_value(
			"Rental Inspection",
			{"asset_booking": row.asset_booking, "direction": "Return", "docstatus": 1},
			["name", "has_damage", "has_shortfall"],
			as_dict=True,
		)
		if not sheet:
			return  # not every fountain is checked in yet
		sheets.append(sheet)
	flagged = [s.name for s in sheets if s.has_damage or s.has_shortfall]
	if flagged:
		_todo(
			booking,
			_("{0} came back with findings ({1}). Review them and use Release Deposit… to set any deduction.").format(
				booking.name, ", ".join(flagged)
			),
		)
		return
	try:
		draft_release(booking, 0, None)
		_todo(
			booking,
			_("{0} came back clean. Submit the deposit credit note, then Refund Deposit.").format(booking.name),
		)
	except Exception as exc:
		frappe.log_error(title=f"Rental deposit release not drafted for {booking.name}", message=frappe.get_traceback())
		booking.add_comment(
			"Comment", _("The deposit release could not be drafted: {0}").format(frappe.utils.strip_html(str(exc))[:300])
		)


def _todo(booking, text):
	owner = booking.owner if frappe.db.get_value("User", booking.owner, "user_type") == "System User" else None
	if owner:
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": owner,
				"reference_type": "Rental Booking",
				"reference_name": booking.name,
				"description": text,
			}
		).insert(ignore_permissions=True)
	booking.add_comment("Comment", text)


def _deposit_row(invoice, conf):
	return next((r for r in invoice.items if r.item_code == conf.security_deposit_item), None)


@frappe.whitelist(methods=["POST"])
def prepare_release(booking, deduction=0, reason=None):
	"""The booking's Release Deposit… dialog. Checks the caller may edit the booking."""
	doc = frappe.get_doc("Rental Booking", booking)
	doc.check_permission("write")
	return draft_release(doc, deduction, reason)


def draft_release(doc, deduction=0, reason=None):
	"""Draft the deposit credit note, and a damage-charge invoice for any ``deduction``.

	In-process only (no permission check; :func:`prepare_release` is the HTTP door). Re-running
	replaces earlier DRAFTS (a changed deduction); once either is submitted it refuses.
	"""
	conf = settings()
	deposit = flt(doc.security_deposit)
	deduction = flt(deduction)
	if deposit <= 0:
		frappe.throw(_("This rental has no security deposit."))
	if not 0 <= deduction <= deposit:
		frappe.throw(_("The deduction has to be between 0 and the deposit ({0}).").format(deposit))
	if deduction and not (reason or "").strip():
		frappe.throw(_("Say what the deduction is for; the customer will see it on the invoice."))
	if not doc.balance_invoice or frappe.db.get_value("Sales Invoice", doc.balance_invoice, "docstatus") != 1:
		frappe.throw(_("The balance invoice, which collected the deposit, has to be submitted first."))
	for field in ("deposit_credit_note", "damage_invoice"):
		existing = doc.get(field)
		if existing and frappe.db.get_value("Sales Invoice", existing, "docstatus") == 1:
			frappe.throw(_("{0} is already submitted. Cancel it to change the release.").format(existing))

	# Unlink before deleting: frappe.delete_doc refuses a document another record Links to.
	stale = [doc.get(f) for f in ("deposit_credit_note", "damage_invoice") if doc.get(f)]
	doc.db_set({"deposit_credit_note": None, "damage_invoice": None}, update_modified=False)
	for name in stale:
		if frappe.db.get_value("Sales Invoice", name, "docstatus") == 0:
			frappe.delete_doc("Sales Invoice", name, ignore_permissions=True)

	credit_note = _draft_credit_note(doc, conf, deposit)
	damage = _draft_damage_invoice(doc, conf, deduction, reason) if deduction else None
	doc.db_set(
		{
			"deposit_credit_note": credit_note,
			"damage_invoice": damage,
			"deposit_deduction": deduction,
			"deposit_deduction_reason": (reason or "").strip() or None,
			"deposit_status": "Release Drafted",
		},
		update_modified=False,
	)
	doc.add_comment(
		"Comment",
		_("Deposit release drafted: credit note {0}{1}.").format(
			credit_note, _(", damage charge {0} ({1})").format(damage, deduction) if damage else ""
		),
	)
	return {"credit_note": credit_note, "damage_invoice": damage}


def _draft_credit_note(doc, conf, deposit):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

	note = make_sales_return(doc.balance_invoice)
	row = _deposit_row(note, conf)
	if row is None:
		frappe.throw(_("{0} has no security-deposit line to return.").format(doc.balance_invoice))
	row.qty = -1
	row.rate = deposit
	note.set("items", [row])
	# The deposit was never taxed income; its return carries no tax either.
	note.taxes_and_charges = None
	note.set("taxes", [])
	note.custom_rental_booking = doc.name
	note.custom_rental_invoice_kind = DEPOSIT_RETURN
	note.calculate_taxes_and_totals()
	note.flags.ignore_permissions = True
	note.insert()
	return note.name


def _draft_damage_invoice(doc, conf, deduction, reason):
	item = conf.get("damage_item") or conf.fee_item or conf.rental_item
	if not item:
		frappe.throw(_("Set the Damage Charge Item in Rental Settings first."))
	balance = frappe.get_doc("Sales Invoice", doc.balance_invoice)
	si = frappe.new_doc("Sales Invoice")
	si.update(
		{
			"customer": doc.customer,
			"company": balance.company,
			"posting_date": frappe.utils.nowdate(),
			"due_date": frappe.utils.nowdate(),
			"project": doc.project,
			"contact_person": doc.contact_person,
			"custom_rental_booking": doc.name,
			"custom_rental_invoice_kind": DAMAGE,
		}
	)
	si.append(
		"items",
		{
			"item_code": item,
			"qty": 1,
			"rate": deduction,
			"description": _("Damage charge for rental {0}: {1}").format(doc.name, (reason or "").strip()),
		},
	)
	si.set_missing_values()
	if conf.cost_center:
		for row in si.items:
			row.cost_center = conf.cost_center
	si.taxes_and_charges = conf.taxes_and_charges or None
	si.set("taxes", [])
	if si.taxes_and_charges:
		si.append_taxes_from_master()
	si.calculate_taxes_and_totals()
	si.flags.ignore_permissions = True
	si.insert()
	return si.name


def refund_amount(doc):
	return round(flt(doc.security_deposit) - flt(doc.deposit_deduction), 2)


def _check_ready(doc):
	if doc.deposit_status != "Release Drafted":
		frappe.throw(_("Draft the release first (Release Deposit…)."))
	for field, label in (("deposit_credit_note", _("credit note")), ("damage_invoice", _("damage charge"))):
		name = doc.get(field)
		if name and frappe.db.get_value("Sales Invoice", name, "docstatus") != 1:
			frappe.throw(_("Submit the {0} {1} first.").format(label, name))
	if not doc.deposit_credit_note:
		frappe.throw(_("There is no deposit credit note."))


@frappe.whitelist(methods=["POST"])
def refund_deposit(booking):
	"""Refund deposit minus deduction on the Stripe payment that paid the balance invoice."""
	frappe.only_for(REFUND_ROLES)
	doc = frappe.get_doc("Rental Booking", booking)
	_check_ready(doc)
	amount = refund_amount(doc)
	if amount <= 0:
		_finish(doc, None)
		return {"refunded": 0}

	payment = frappe.get_all(
		"Stripe Payment",
		filters={"sales_invoice": doc.balance_invoice, "status": "Paid"},
		fields=["name", "stripe_payment_intent", "amount", "amount_refunded", "currency"],
		order_by="creation desc",
		limit_page_length=1,
	)
	if not payment or not payment[0].stripe_payment_intent:
		frappe.throw(
			_("The balance invoice was not paid through Stripe. Refund it by hand, then press Mark Refunded.")
		)
	payment = payment[0]
	if amount > flt(payment.amount) - flt(payment.amount_refunded):
		frappe.throw(_("{0} took less than the refund; refund by hand and press Mark Refunded.").format(payment.name))

	from erpnext_enhancements.stripe_payments.core.client import create_refund
	from erpnext_enhancements.stripe_payments.core.utils import to_minor_units

	refund = create_refund(payment.stripe_payment_intent, amount_minor=to_minor_units(amount, payment.currency or "USD"))
	_finish(doc, refund.get("id"))
	return {"refunded": amount, "refund": refund.get("id")}


@frappe.whitelist(methods=["POST"])
def mark_refunded(booking, reference=None):
	"""A deposit refunded outside Stripe (check, bank transfer): record it."""
	frappe.only_for(REFUND_ROLES)
	doc = frappe.get_doc("Rental Booking", booking)
	_check_ready(doc)
	_finish(doc, (reference or "").strip() or _("Refunded by hand"))
	return {"refunded": refund_amount(doc)}


def _finish(doc, reference):
	amount = refund_amount(doc)
	status = "Retained" if amount <= 0 else "Partly Retained" if flt(doc.deposit_deduction) else "Released"
	doc.db_set(
		{"deposit_status": status, "deposit_refund": reference, "deposit_released_on": now_datetime()},
		update_modified=False,
	)
	doc.add_comment(
		"Comment",
		_("Security deposit {0}: {1} refunded{2}.").format(
			_(status).lower(), amount, _(" (reference {0})").format(reference) if reference else ""
		),
	)


def mark_held(booking_name):
	"""The balance invoice (with its deposit line) was posted: the deposit is now held."""
	frappe.db.set_value("Rental Booking", booking_name, "deposit_status", "Held", update_modified=False)


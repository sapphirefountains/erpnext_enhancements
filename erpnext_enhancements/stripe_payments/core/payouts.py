# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reconcile Stripe payouts into the general ledger.

Stripe holds each captured charge in the deposit/clearing account (the customer
Payment Entry debits it), then periodically **pays out** the accumulated balance
to the real bank account — net of its processing fees. This module turns a
``payout.paid`` webhook into the Journal Entry that moves that money out of
clearing:

    Dr  Payout Bank Account     net (what actually lands in the bank)
    Dr  Merchant Fees           Stripe's per-transaction processing fees
        Cr  Stripe Clearing         net + fees

The entry is self-balancing by construction (``net + fees`` on each side). Since
``net = charges − refunds − fees``, the credit to clearing equals
``charges − refunds`` — so once the customer Payment Entries (Dr clearing = each
charge) and the refund reversals from WI-041 (Cr clearing = each refund) are
posted, the clearing balance for the payout's items nets to exactly zero. This
module deliberately does **not** book the customer side of refunds or disputes
(that is WI-041); a payout carrying balance-transaction categories beyond
charges/refunds/fees is posted (still balanced) but flagged for review so the
residual clearing balance is reconciled by a human.

The journalled fee is always taken from the balance-transaction data, never
estimated; the card/ACH rate constants are used only to raise a soft variance
alert when the effective fee looks wrong.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, get_datetime, today

from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	from_minor_units,
	get_settings,
)

# Balance-transaction categories whose money sat in the clearing account and is
# fully modelled by this reconciliation. Anything else (dispute, adjustment,
# standalone Stripe fee, transfer, …) is flagged for review.
_CHARGE_CATEGORIES = {"charge", "payment"}
_REFUND_CATEGORIES = {"refund", "payment_refund", "partial_capture_reversal"}

# Nominal card economics, used ONLY to sanity-check the actual fee.
_CARD_PCT = 0.029
_CARD_FLAT = 0.30
_FEE_VARIANCE_THRESHOLD = 0.20


def compute_payout_breakdown(payout: dict, balance_txns: list[dict]) -> dict:
	"""Aggregate a payout's balance transactions (pure function — no I/O).

	Works in Stripe minor units (ints) to avoid float drift, then exposes the
	amounts an entry needs in major currency units. ``net`` is taken from the
	payout object itself (authoritative); ``fees`` is the sum of the per-
	transaction Stripe fees. The ``payout`` balance-transaction (the debit that
	represents the payout itself) is excluded from the component aggregation.
	"""
	gross_minor = 0
	refunds_minor = 0
	fees_minor = 0
	other_minor = 0
	charge_count = 0
	other_count = 0

	for bt in balance_txns:
		if bt.get("type") == "payout":
			continue  # the payout line itself, not a component
		amount = int(bt.get("amount") or 0)
		fee = int(bt.get("fee") or 0)
		fees_minor += fee
		category = bt.get("reporting_category") or bt.get("type") or ""
		if category in _CHARGE_CATEGORIES:
			gross_minor += amount
			charge_count += 1
		elif category in _REFUND_CATEGORIES:
			refunds_minor += amount  # already negative
		elif fee and not amount:
			pass  # a pure per-transaction fee already counted in fees_minor
		else:
			other_minor += amount
			other_count += 1

	net_minor = int(payout.get("amount") or 0)
	return {
		"currency": (payout.get("currency") or "usd").upper(),
		"net": from_minor_units(net_minor),
		"fees": from_minor_units(fees_minor),
		"gross": from_minor_units(gross_minor),
		"refunds": from_minor_units(-refunds_minor),  # report as a positive magnitude
		"other_amount": from_minor_units(other_minor),
		"charge_count": charge_count,
		"other_count": other_count,
		"net_minor": net_minor,
		"fees_minor": fees_minor,
	}


def _fee_variance(breakdown: dict) -> str | None:
	"""Return a human note when the actual fee deviates from card-nominal by >20%.

	A heuristic sanity check only (assumes card economics; ACH-heavy payouts may
	trip it) — it never blocks posting, it just annotates the entry for review.
	"""
	gross = flt(breakdown["gross"])
	if gross <= 0:
		return None
	expected = _CARD_PCT * gross + _CARD_FLAT * breakdown["charge_count"]
	actual = flt(breakdown["fees"])
	if expected <= 0:
		return None
	if abs(actual - expected) / expected > _FEE_VARIANCE_THRESHOLD:
		return (
			f"Fee variance: actual {actual:.2f} vs card-nominal {expected:.2f} "
			f"({(actual - expected) / expected * 100:+.0f}%) — review."
		)
	return None


def process_payout(payout: dict):
	"""Post the clearing→bank Journal Entry for a paid Stripe payout (idempotent).

	Guards: requires the integration enabled and the three routing accounts
	(clearing/fee/bank) configured; a payout that is not ``paid`` or is already
	journalled is a no-op. Returns the Journal Entry name, or None when skipped.
	"""
	settings = get_settings()
	payout_id = payout.get("id")
	if not payout_id:
		return None
	if payout.get("status") and payout.get("status") != "paid":
		return None

	if not (settings.deposit_account and settings.fee_expense_account and settings.payout_bank_account):
		# Payout accounting not configured yet — leave it for a re-run once the
		# accounts are set (the caller records the event Ignored, matching the
		# deposit-account guard in reconcile._create_payment_entry).
		frappe.throw(
			"Stripe payout reconciliation needs the Deposit/Clearing, Merchant Fees and "
			"Payout Bank accounts set in Stripe Payments Settings."
		)

	existing = frappe.db.get_value(
		"Journal Entry", {"cheque_no": payout_id, "docstatus": ["<", 2]}, "name"
	)
	if existing:
		return existing

	from erpnext_enhancements.stripe_payments.core.client import (
		list_balance_transactions_for_payout,
	)

	balance_txns = list_balance_transactions_for_payout(payout_id, settings=settings)
	breakdown = compute_payout_breakdown(payout, balance_txns)
	return _build_journal_entry(payout, breakdown, settings)


def _build_journal_entry(payout: dict, breakdown: dict, settings) -> str | None:
	"""Construct + submit the sign-safe clearing-sweep Journal Entry."""
	net = flt(breakdown["net"])
	fees = flt(breakdown["fees"])
	if net == 0 and fees == 0:
		return None  # nothing to move

	# Signed legs (positive = debit, negative = credit); they sum to zero so the
	# entry always balances, and a negative payout (refund-heavy period) flips the
	# bank/clearing legs to the correct side automatically.
	legs = [
		(settings.payout_bank_account, net),
		(settings.fee_expense_account, fees),
		(settings.deposit_account, -(net + fees)),
	]

	notes = [
		f"Stripe payout {payout.get('id')}: net {net:.2f} {breakdown['currency']}, "
		f"fees {fees:.2f}, {breakdown['charge_count']} charge(s), refunds {breakdown['refunds']:.2f}."
	]
	variance = _fee_variance(breakdown)
	if variance:
		notes.append(variance)
	if breakdown["other_count"]:
		notes.append(
			f"REVIEW: {breakdown['other_count']} balance transaction(s) outside charges/refunds "
			f"(net {breakdown['other_amount']:.2f}) — clearing may not fully net to zero until "
			"their customer-side entries (refund/dispute reversals) are posted."
		)

	arrival = payout.get("arrival_date")
	posting_date = get_datetime(arrival).date() if arrival else today()

	company = settings.company or frappe.defaults.get_user_default("Company")
	# The Merchant Fees leg is a P&L account, which requires a cost center on its
	# GL entry; harmless on the bank/clearing legs, so stamp it on all rows. Use the
	# company default, falling back to any leaf cost center for the company.
	cost_center = frappe.get_cached_value("Company", company, "cost_center") or frappe.db.get_value(
		"Cost Center", {"company": company, "is_group": 0}, "name"
	)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = posting_date
	je.cheque_no = payout.get("id")
	je.cheque_date = posting_date
	je.user_remark = " ".join(notes)[:1000]
	for account, signed in legs:
		amount = flt(abs(signed), 2)
		if amount == 0:
			continue
		je.append(
			"accounts",
			{
				"account": account,
				"cost_center": cost_center,
				"debit_in_account_currency": amount if signed > 0 else 0,
				"credit_in_account_currency": amount if signed < 0 else 0,
			},
		)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()

	if breakdown["other_count"] or variance:
		_notify_review(je.name, payout.get("id"), notes)
	return je.name


def _notify_review(je_name: str, payout_id: str, notes: list[str]):
	"""Best-effort alert to Accounts Managers when a payout needs a human look."""
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": f"Stripe payout {payout_id} posted with review flags",
				"email_content": "<br>".join(notes),
				"document_type": "Journal Entry",
				"document_name": je_name,
				"for_user": frappe.session.user,
				"type": "Alert",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: payout review notify failed")

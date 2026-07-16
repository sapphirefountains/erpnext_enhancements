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

import datetime

import frappe
from frappe.utils import flt, today
from frappe.utils.synchronization import filelock

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

_REVIEW_ROLE = "Accounts Manager"


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


def signed_legs(net: float, fees: float, bank: str, fee_account: str, clearing: str) -> list[tuple]:
	"""Return the JE legs as (account, signed_amount): + is debit, − is credit.

	They sum to zero so the entry always balances, and a negative payout (a
	refund-heavy period Stripe debits from the bank) flips the bank and clearing
	legs to the correct side automatically. Pure — unit-tested for sign safety.
	"""
	return [
		(bank, flt(net)),
		(fee_account, flt(fees)),
		(clearing, -(flt(net) + flt(fees))),
	]


def posting_date_from_arrival(arrival) -> object:
	"""Stripe's ``arrival_date`` is a Unix epoch (int seconds); return its date.

	``frappe.utils.get_datetime`` does NOT interpret an int as an epoch (it treats
	any non-string as an invalid date and returns None), so the epoch must be
	converted explicitly. Stripe expresses ``arrival_date`` as 00:00:00 **UTC** of
	the arrival date, so the epoch is read in UTC (not the host's local timezone) —
	otherwise a non-UTC server would shift the payout's posting date back a day and
	could push it into the wrong (possibly closed) period. Falls back to today for a
	missing/garbage value.
	"""
	if arrival in (None, ""):
		return today()
	try:
		return datetime.datetime.fromtimestamp(int(arrival), tz=datetime.timezone.utc).date()
	except (TypeError, ValueError, OSError, OverflowError):
		return today()


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

	No-op (returns None) unless the integration is enabled, the three routing
	accounts are configured, the payout is ``paid`` and in the company currency —
	an unconfigured or foreign-currency payout is skipped, not errored, so the
	webhook event is recorded Ignored (the hourly ``poll_payouts`` backstop will
	post it once configuration is fixed). Serialized per payout id so a webhook
	and the poll cannot both create a Journal Entry. Returns the JE name or None.
	"""
	settings = get_settings()
	payout_id = payout.get("id")
	if not payout_id:
		return None
	if payout.get("status") and payout.get("status") != "paid":
		return None
	if not (settings.deposit_account and settings.fee_expense_account and settings.payout_bank_account):
		# Payout accounting not configured yet — skip (Ignored); the poll backstop
		# reconciles it once the accounts are set. Deliberately not an error.
		return None

	company = settings.company or frappe.defaults.get_user_default("Company")
	company_currency = frappe.get_cached_value("Company", company, "default_currency") or "USD"
	payout_currency = (payout.get("currency") or "usd").upper()
	if payout_currency != company_currency:
		_notify_review(
			None,
			payout_id,
			[
				f"Stripe payout {payout_id} is in {payout_currency}, not the company currency "
				f"{company_currency}. Skipped — book it manually (this module is single-currency)."
			],
		)
		return None

	# Serialize webhook vs. hourly-poll so the check-then-create can't double-post.
	with filelock(f"stripe_payout_{payout_id}", timeout=30):
		existing = frappe.get_all(
			"Journal Entry", filters={"cheque_no": payout_id}, fields=["name", "docstatus"]
		)
		live = [e for e in existing if e.docstatus < 2]
		if live:
			return live[0].name
		if existing:
			# Only cancelled JEs remain: the accountant reversed this payout on
			# purpose — do not resurrect it.
			return None

		from erpnext_enhancements.stripe_payments.core.client import (
			list_balance_transactions_for_payout,
		)

		balance_txns = list_balance_transactions_for_payout(payout_id, settings=settings)
		breakdown = compute_payout_breakdown(payout, balance_txns)
		return _build_journal_entry(payout, breakdown, settings, company)


def _build_journal_entry(payout: dict, breakdown: dict, settings, company: str) -> str | None:
	"""Construct + submit the sign-safe clearing-sweep Journal Entry."""
	net = flt(breakdown["net"])
	fees = flt(breakdown["fees"])
	if net == 0 and fees == 0:
		return None  # nothing to move

	# The Merchant Fees leg is a P&L account, which requires a cost center on its
	# GL entry; harmless on the bank/clearing legs, so stamp it on all rows.
	cost_center = frappe.get_cached_value("Company", company, "cost_center") or frappe.db.get_value(
		"Cost Center", {"company": company, "is_group": 0}, "name"
	)

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

	posting_date = posting_date_from_arrival(payout.get("arrival_date"))

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = posting_date
	je.cheque_no = payout.get("id")
	je.cheque_date = posting_date
	je.user_remark = " ".join(notes)[:1000]
	for account, signed in signed_legs(
		net, fees, settings.payout_bank_account, settings.fee_expense_account, settings.deposit_account
	):
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


def _notify_review(je_name: str | None, payout_id: str, notes: list[str]):
	"""Alert the Accounts Managers when a payout needs a human look."""
	recipients = _accounts_managers()
	subject = f"Stripe payout {payout_id} needs review"
	content = "<br>".join(notes)
	for user in recipients:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": content,
					"document_type": "Journal Entry" if je_name else None,
					"document_name": je_name,
					"for_user": user,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: payout review notify failed")
	if not recipients:
		frappe.log_error(f"{subject}: {content}", "Stripe: payout review (no Accounts Manager to notify)")


def _accounts_managers() -> list[str]:
	"""Enabled users holding the Accounts Manager role (best-effort)."""
	try:
		holders = frappe.get_all(
			"Has Role", filters={"role": _REVIEW_ROLE, "parenttype": "User"}, pluck="parent"
		)
		if not holders:
			return []
		return frappe.get_all(
			"User",
			filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	except Exception:
		return []

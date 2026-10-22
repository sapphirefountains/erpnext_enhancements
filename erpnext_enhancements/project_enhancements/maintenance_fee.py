"""The maintenance agreement's per-period Service Fee: what the client pays each month, quarter or year.

Nik, 2026-10-22: "add a monthly (per-period) fee line to the maintenance agreement template, filled
from the linked maintenance contract's Recurring Amount when set". The agreement priced only per visit
and per event, plus the Annual Service Fee Total, so a client billed a flat amount each month (Day Dairy,
SF-MAINT-2026-0018, $1,490 a month from May through September) had no line saying so.

Where the figure comes from, first match wins:

1. **The maintenance contract's Recurring Amount.** The Sapphire Maintenance Contract whose
   ``project_contract`` is this agreement bills that amount each period
   (``api.maintenance_billing.generate_recurring_invoices``), so the agreement prints what will
   actually be invoiced. Used only when that contract bills on the agreement's own cadence: a
   Recurring Amount is "per period" of *its* cadence, and the agreement's §4.2 prints the agreement's
   cadence, so a monthly amount beside a "Quarterly" tick would contradict the page it is on.
2. **The agreement's Annual Maintenance Fee ÷ billed periods**, the split the maintenance contract
   derives for itself (``SapphireMaintenanceContract._recurring_billing_setup``), with the same helper
   and so the same billing season. Only while there is no maintenance contract yet or it bills on the
   agreement's cadence: a split printed beside a contract that invoices per visit states a fee nobody
   is billed.
3. **Nothing.** The line is left out rather than printed as $0.00 or a blank.

Per Visit agreements have no per-period fee: their price is the per-visit base rate the plan already
prints. Nor does a plan whose ticked Standard Maintenance carries a per-visit rate, whatever it is
invoiced on: its Annual Fee is that rate times the visits (``_annualize_options``), so splitting it
would print a flat monthly price the client never agreed to (Hardware Apartments, $690 a visit, would
have read "$1,495.00 per month"), and a flat Recurring Amount beside a per-visit rate is a question for
whoever priced it, not something to print. An Annually agreement whose fee would be its own Annual Fee
gets no line either: the Annual Service Fee Total already states that figure. The billing season
(Bill From / Bill Through) lives on the maintenance contract and applies only to Monthly billing, as it
does when invoicing.

Never raises and is deterministic for the same records: it runs on the print path and inside the e-sign
drift check, which compares a fresh render with the snapshot the signer was sent.
"""

import frappe
from frappe.utils import cint, flt

#: The agreement's Invoicing Frequency → the word after "per".
PERIOD_WORDS = {"Monthly": "month", "Quarterly": "quarter", "Annually": "year"}


def linked_maintenance_contract(name):
	"""The maintenance contract raised from agreement ``name``: an Active one first, then the newest.

	Canceled contracts are skipped; a renewal can leave an Expired one beside the Active one. Returns a
	``frappe._dict`` of the billing fields, or None.
	"""
	if not name:
		return None
	rows = frappe.get_all(
		"Sapphire Maintenance Contract",
		filters={"project_contract": name, "status": ["!=", "Canceled"]},
		fields=[
			"name",
			"status",
			"recurring_amount",
			"invoicing_frequency",
			"billing_season_start",
			"billing_season_end",
		],
		order_by="modified desc",
		ignore_permissions=True,
	)
	if not rows:
		return None
	return next((row for row in rows if row.get("status") == "Active"), rows[0])


def period_fee(doc, maintenance=None):
	"""``{"amount", "per", "season", "source"}`` for agreement ``doc``, or None when there is no fee to print.

	``maintenance`` is the linked contract (:func:`linked_maintenance_contract`); a caller that has not
	looked it up passes None and gets the Annual Fee split, with no season.
	"""
	from erpnext_enhancements.api.maintenance_billing import SEASONAL_FREQUENCIES, billed_periods_per_year

	frequency = doc.get("invoicing_frequency")
	per = PERIOD_WORDS.get(frequency)
	if not per or _priced_per_visit(doc):
		return None

	same_cadence = bool(maintenance) and maintenance.get("invoicing_frequency") == frequency
	season = (None, None)
	if same_cadence and frequency in SEASONAL_FREQUENCIES:
		start, end = maintenance.get("billing_season_start"), maintenance.get("billing_season_end")
		if start and end:
			season = (start, end)

	amount, source = 0.0, None
	if same_cadence and flt(maintenance.get("recurring_amount")) > 0:
		amount, source = flt(maintenance.get("recurring_amount"), 2), "maintenance_contract"
	elif same_cadence or not maintenance:
		annual = flt(doc.get("annual_maintenance_fee"))
		periods = billed_periods_per_year(frequency, *season)
		if annual > 0 and periods and periods > 1:
			amount, source = flt(annual / periods, 2), "annual_fee"
	if amount <= 0:
		return None
	return {
		"amount": amount,
		"per": per,
		"season": f"{season[0]} through {season[1]}" if season[0] else "",
		"source": source,
	}


def _priced_per_visit(doc):
	"""Whether the plan's ticked Standard Maintenance carries a per-visit base rate."""
	return any(
		row.get("option_key") == "standard" and cint(row.get("included")) and flt(row.get("price")) > 0
		for row in (doc.get("service_options") or [])
	)


def period_fee_for(doc):
	""":func:`period_fee` with the linked maintenance contract looked up. Never raises."""
	try:
		return period_fee(doc, linked_maintenance_contract(doc.get("name")))
	except Exception:
		frappe.log_error(
			title="Maintenance agreement: service fee lookup failed", message=frappe.get_traceback()
		)
		return None

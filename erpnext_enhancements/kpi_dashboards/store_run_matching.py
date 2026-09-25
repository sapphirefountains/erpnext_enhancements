"""Store Run Charge Matching: every rule of Accounting's list of store runs and their card charges.

A store run recorded on the Stock Scan page is a submitted Purchase Receipt per line (Dr 1410
Stock In Hand / Cr 2210 Stock Received But Not Billed, stock lines only). The same purchase
reaches ERPNext again from QuickBooks as a **draft** Journal Entry (Dr the expense QuickBooks
coded / Cr the card, no party). At the QuickBooks cutover step S-D submits the 2026 drafts, and
a draft that pairs with a recorded trip must first have its goods debit moved to 2210 for the
trip's stock lines before tax, or the purchase is booked twice and 2210 never clears (CHANGELOG
1.536.0, "For Accounting" note 2). This module turns the Store Runs KPI's own pairing into that
list, one row per trip, and says what to do with each.

**The pairing is the KPI's, not a copy of it.** Trips and charges are paired by
``metrics.pair_store_runs``, the function ``metrics.combine_store_runs`` counts with, over rows
read by the KPI's own ``snapshots._store_run_rows``. The window is the KPI's too: rows from
``STORE_RUN_LOOKBACK_DAYS`` before the From Date, so a trip just before it still claims its
charge, and charges up to ``STORE_RUN_PAIR_DAYS`` after the To Date, the latest a trip in range
can pair with. A trip in range then pairs exactly as it does in the KPI counted from the From
Date: a trip after the To Date can only take a charge dated after it, a later charge could only
be its choice when no earlier one fits, and every trip in range is paired before any of them in
both passes. **Receipts are not cut at the To Date end**: a trip is keyed by its run id, and a
receipt dated later that carries the same run id (possible from the Desk, not from the page)
still changes that trip's lines and total, and so its pairing. ``tests/test_store_run_matching.py``
checks the equivalence on generated data, multi-day runs included.

Pure: no frappe import, so every rule here runs in the bench-free CI suite. The report
(``report/store_run_charge_matching``) does the reads and nothing else.
"""

from datetime import timedelta

from erpnext_enhancements.kpi_dashboards import metrics

#: The Show filter's buckets. "Needs action" is what Accounting works through at step S-D.
SHOW_ALL = "All"
NEEDS_ACTION = "Needs action"
WAITING = "Waiting"
DONE = "Done"
SHOW_OPTIONS = (SHOW_ALL, NEEDS_ACTION, WAITING, DONE)
_BUCKET_ORDER = {NEEDS_ACTION: 0, WAITING: 1, DONE: 2}

#: Match Basis: which pass of the pairing took the charge.
BASIS_LABELS = {
	metrics.PAIRED_ON_RECEIPT_TOTAL: "Receipt total",
	metrics.PAIRED_ON_LINES_PLUS_TAX: "Lines plus tax",
}
NO_CHARGE_YET = "No charge yet"
BILLED_FROM_RECEIPTS = "Billed from the receipts"

#: Charge Source.
SOURCE_QBO_DRAFT = "QuickBooks draft Journal Entry"
SOURCE_QBO = "QuickBooks Journal Entry"
SOURCE_PURCHASE_INVOICE = "Purchase Invoice"
SOURCE_JOURNAL_ENTRY = "Journal Entry"

#: Named in the advice when a receipt's company has no Stock Received But Not Billed account
#: on record (production's is this one).
DEFAULT_2210 = "2210 - Stock Received But Not Billed - SF"

DRAFT = "Draft"
SUBMITTED = "Submitted"


def _cents(value):
	return round(metrics._amount(value), 2)


def _money(value):
	return f"${_cents(value):,.2f}"


def trips_in_window(
	charges,
	receipts,
	from_date,
	to_date,
	store_key,
	store=None,
	lookback_days=metrics.STORE_RUN_LOOKBACK_DAYS,
	pair_days=metrics.STORE_RUN_PAIR_DAYS,
):
	"""The recorded trips dated From..To, each paired as the Store Runs KPI pairs it.

	``charges`` and ``receipts`` are ``snapshots._store_run_rows`` rows. Rows dated more than
	``lookback_days`` before ``from_date`` take no part, and neither do charges dated more than
	``pair_days`` after ``to_date``; receipts are kept to the end, whole trips being what is
	paired (the module docstring says why this pairs every trip in range as the KPI does). The
	trips dated outside From..To are dropped after the pairing, not before it. ``store`` keeps one
	store's trips, compared through ``store_key`` so "Lowes" also shows the trips recorded at
	"Lowe's": the pairing always runs over every store-run vendor, because the two are one store
	to it.
	"""
	start = metrics._as_day(from_date)
	end = metrics._as_day(to_date)
	if start is None or end is None or start > end:
		return []
	early = start - timedelta(days=lookback_days)
	late = end + timedelta(days=pair_days)

	def dated_from_early(row, until=None):
		day = metrics._as_day(row.get("day"))
		return day is not None and early <= day and (until is None or day <= until)

	trips, _charges = metrics.pair_store_runs(
		[row for row in charges or () if dated_from_early(row, until=late)],
		[row for row in receipts or () if dated_from_early(row)],
		store_key,
		pair_days,
	)
	wanted = store_key(store) if store else None
	return [
		trip for trip in trips if start <= trip["day"] <= end and (wanted is None or trip["store"] == wanted)
	]


def _distinct(values):
	seen = []
	for value in values:
		if value and value not in seen:
			seen.append(value)
	return seen


def _decide(charge, draft, stock, moved, account, receipts_billed, receipt_count, invoices):
	"""``(what to do, bucket, amount still to move)`` for one trip. See :func:`build_rows`."""
	invoice_list = ", ".join(invoices)
	if charge is not None and receipts_billed:
		return (
			f"Billed from the receipts ({invoice_list}) and matched to this charge too: check the "
			"purchase is not booked twice before submitting either",
			NEEDS_ACTION,
			0.0,
		)
	if charge is not None:
		if moved > stock:
			how = "then submit" if draft else "amend it or post a correcting Journal Entry"
			return (
				f"The {account} debit is {_money(moved)} but the stock lines are {_money(stock)}: "
				f"reduce it to {_money(stock)}, {how}",
				NEEDS_ACTION,
				0.0,
			)
		if moved < stock:
			missing = _cents(stock - moved)
			if not draft:
				return (
					f"Submitted with the goods on the expense: move {_money(missing)} from the expense to "
					f"{account} (amend it or post a correcting Journal Entry)",
					NEEDS_ACTION,
					missing,
				)
			if moved > 0:
				return (
					f"Move {_money(missing)} more of the goods debit to {account} ({_money(moved)} is "
					"there already), then submit",
					NEEDS_ACTION,
					missing,
				)
			return (
				f"Move {_money(missing)} of the goods debit to {account}, then submit",
				NEEDS_ACTION,
				missing,
			)
		if not draft:
			return "Done", DONE, 0.0
		if stock == 0:
			return "No stock lines: submit as is", DONE, 0.0
		return f"Goods debit on {account}: submit", DONE, 0.0
	if receipts_billed and receipts_billed == receipt_count:
		return f"{BILLED_FROM_RECEIPTS} ({invoice_list})", DONE, 0.0
	if receipts_billed:
		return (
			f"Billed from {receipts_billed} of {receipt_count} receipts ({invoice_list}): bill the rest",
			NEEDS_ACTION,
			0.0,
		)
	return "Waiting for the card charge", WAITING, 0.0


def build_rows(trips, on_2210=None, billed=None, accounts=None, name_of=None):
	"""One report row per trip from :func:`trips_in_window`, sorted Needs action, Waiting, Done.

	* ``on_2210``: ``{(voucher_type, voucher_no): amount}``, the net debit each matched charge
	  already carries on its company's Stock Received But Not Billed account.
	* ``billed``: ``{receipt name: Purchase Invoice}``, receipts a submitted invoice was made from.
	* ``accounts``: ``{company: its Stock Received But Not Billed account}``.
	* ``name_of``: turns a user id into the name shown under Recorded By.

	**The amount to move** is the trip's *stock lines before tax*: what its receipts credited to
	2210 (``stock_amount``, from the GL). **Moved to 2210** means the matched charge's 2210 debit
	equals it to the cent. **What to do**:

	* a matched draft short of it: move the difference, then submit (**Needs action**);
	* a matched charge already submitted short of it: the purchase is booked twice until the
	  difference is moved from the expense (**Needs action**);
	* more than the stock lines on 2210: reduce it (**Needs action**);
	* a matched draft with it in place, or with no stock lines at all ("submit as is"), or a
	  submitted charge with it in place: **Done** -- the S-D loop submits the drafts;
	* no charge, every receipt billed by a submitted Purchase Invoice (after the cutover a
	  recorded trip is billed from its receipts, which clears 2210 itself): **Done**; some of
	  them only: bill the rest (**Needs action**);
	* a trip matched to a charge *and* billed from its receipts has two records of one purchase
	  (**Needs action**);
	* otherwise **Waiting** for the card charge.
	"""
	on_2210 = on_2210 or {}
	billed = billed or {}
	accounts = accounts or {}
	rows = []
	for trip in trips or ():
		receipts = sorted(
			trip["receipts"],
			key=lambda row: (metrics._as_day(row.get("day")) or trip["day"], str(row.get("receipt") or "")),
		)
		first = receipts[0] if receipts else {}
		account = accounts.get(first.get("company")) or DEFAULT_2210
		stock = _cents(sum(metrics._amount(row.get("stock_amount")) for row in receipts))
		lines = _cents(
			sum(
				metrics._amount(row.get("amount") if row.get("net_amount") is None else row.get("net_amount"))
				for row in receipts
			)
		)
		runs = _distinct(str(row.get("run") or "") for row in receipts)
		numbers = _distinct(str(row.get("receipt_number") or "").strip() for row in receipts)
		run_ref = ", ".join(runs)
		if numbers:
			run_ref = f"{run_ref}; receipt {numbers[0]}" if run_ref else f"receipt {numbers[0]}"
		owners = _distinct(str(row.get("recorded_by") or "") for row in receipts)
		invoices = _distinct(billed.get(row.get("receipt")) for row in receipts)
		receipts_billed = sum(1 for row in receipts if billed.get(row.get("receipt")))

		charge = trip.get("charge")
		charge_row = charge["row"] if charge else {}
		voucher_type = charge_row.get("voucher_type") or ""
		voucher_no = charge_row.get("voucher_no") or ""
		draft = charge is not None and charge_row.get("docstatus") in (0, "0")
		moved = _cents(on_2210.get((voucher_type, voucher_no))) if charge else 0.0
		if charge is None:
			source = ""
		elif charge_row.get("source") == "QuickBooks":
			source = SOURCE_QBO_DRAFT if draft else SOURCE_QBO
		elif voucher_type == "Purchase Invoice":
			source = SOURCE_PURCHASE_INVOICE
		else:
			source = SOURCE_JOURNAL_ENTRY

		action, show, to_move = _decide(
			charge, draft, stock, moved, account, receipts_billed, len(receipts), invoices
		)
		if charge is not None:
			basis = BASIS_LABELS.get(trip.get("basis"), "")
		elif receipts_billed:
			basis = BILLED_FROM_RECEIPTS
		else:
			basis = NO_CHARGE_YET
		rows.append(
			{
				"trip_day": trip["day"],
				"store": first.get("supplier"),
				"run_ref": run_ref,
				"receipts": len(receipts),
				"first_receipt": first.get("receipt"),
				"recorded_by": ", ".join(name_of(owner) if name_of else owner for owner in owners),
				"lines_before_tax": lines,
				"stock_amount": stock,
				"receipt_total": _cents(trip["total"]) or None,
				"charge_type": voucher_type or None,
				"charge": voucher_no or None,
				"charge_date": charge["day"] if charge else None,
				"charge_amount": _cents(charge["amount"]) if charge else None,
				"charge_source": source,
				"match_basis": basis,
				"charge_status": (DRAFT if draft else SUBMITTED) if charge else "",
				"moved_to_2210": 1 if charge is not None and stock > 0 and moved == stock else 0,
				"on_2210": moved,
				"to_move": to_move,
				"action": action,
				"show": show,
			}
		)
	rows.sort(
		key=lambda row: (_BUCKET_ORDER[row["show"]], row["trip_day"], str(row["store"] or ""), row["run_ref"])
	)
	return rows


def filter_rows(rows, show):
	"""The rows the Show filter keeps. Blank, "All" or anything unknown keeps every row."""
	if show in _BUCKET_ORDER:
		return [row for row in rows if row["show"] == show]
	return list(rows)


def summarize(rows):
	"""The figures across the top, over every trip in range (the Show filter does not change them).

	``to_move`` is what the matched charges still have to move to 2210; ``moved`` what they
	already carry there, up to each trip's stock lines.
	"""
	matched = [row for row in rows if row["charge"]]
	return {
		"trips": len(rows),
		"matched": len(matched),
		"needs_action": sum(1 for row in rows if row["show"] == NEEDS_ACTION),
		"waiting": sum(1 for row in rows if row["show"] == WAITING),
		"to_move": _cents(sum(row["to_move"] for row in rows)),
		"moved": _cents(sum(max(0.0, min(row["on_2210"], row["stock_amount"])) for row in matched)),
	}

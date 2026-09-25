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
checks the equivalence on generated data, multi-day runs included. The nightly KPI counts from
30 days back, so the list pairs exactly as the KPI does when counted from the same From Date; a
chain of same-amount trips at one store reaching in from before a different From Date can pair
a trip differently (``test_rows_before_the_lookback_play_no_part`` shows one).

**What the list adds to the KPI's pairing** (none of it changes the KPI):

* **Correcting entries count.** A charge already submitted with the goods on the expense is fixed
  with ONE correcting Journal Entry, Dr 2210 / Cr the expense account the charge used, whose
  Reference Number (``cheque_no``) is the charge's voucher name. The 2210 debit of every submitted
  entry that names a charge that way (``corrections``) is added to the charge's own, so the row
  reaches Done once corrected and an over-correction is flagged. **Amending the charge is never
  advised**: amending a QuickBooks-synced Journal Entry cancels the original, and
  ``tabQuickBooks Sync Mapping`` -- which the KPI's reader follows to find QuickBooks charges --
  stays on the cancelled one, so the charge would drop out of the pairing and its trip would read
  as waiting.
* **A charge carrying 2210 is never lost.** A charge dated in range that paired with no trip but
  carries a net 2210 debit (its own lines plus its correcting entries) -- a draft adjusted for a
  trip whose pairing then changed, because a receipt was cancelled or edited or a back-dated Desk
  receipt took the charge first -- is listed under **Needs action**, to be moved back to the
  expense.
* **A charge found by hand is recognized by its 2210 debit.** Some trips never pair: a bank-feed
  entry dated more than ``STORE_RUN_PAIR_DAYS`` late, an amount outside the tolerance, two runs of
  one purchase. At step S-D Accounting finds such a trip's draft by hand and moves the trip's
  stock lines to 2210 on it. That debit is the link: an unpaired charge in range whose net 2210
  debit equals, to the cent, a waiting trip's stock lines at the same store is shown as that
  trip's charge (Match Basis "Its 2210 debit (found by hand)"), nearest date first. Without it,
  the draft adjusted by hand would be listed as a charge to move back, and the trip as waiting.

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

#: Match Basis: which pass of the pairing took the charge, or why there is none.
BASIS_LABELS = {
	metrics.PAIRED_ON_RECEIPT_TOTAL: "Receipt total",
	metrics.PAIRED_ON_LINES_PLUS_TAX: "Lines plus tax",
}
MATCHED_BY_HAND = "Its 2210 debit (found by hand)"
NO_CHARGE_YET = "No charge yet"
BILLED_FROM_RECEIPTS = "Billed from the receipts"
NO_STORE_RUN = "No recorded store run"

#: The two kinds of row: a recorded trip, or a charge that carries 2210 with no trip.
ROW_TRIP = "Store run"
ROW_CHARGE = "Charge with no store run"

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

#: How a draft is finished. Accounting saves each adjusted draft; the S-D loop submits every 2026
#: draft together (CHANGELOG 1.538.0, "For Accounting").
SAVE_IT = "then save it; the S-D loop submits it"

#: The one fix for a charge already submitted: a correcting Journal Entry naming the charge in its
#: Reference Number, which the report counts. Never an amendment (see the module docstring).
_CORRECTING_ENTRY = "post a correcting Journal Entry for {amount} ({lines}) with Reference Number {charge}"


def _cents(value):
	return round(metrics._amount(value), 2)


def _money(value):
	cents = _cents(value)
	return f"-${-cents:,.2f}" if cents < 0 else f"${cents:,.2f}"


def pair_window(
	charges,
	receipts,
	from_date,
	to_date,
	store_key,
	store=None,
	lookback_days=metrics.STORE_RUN_LOOKBACK_DAYS,
	pair_days=metrics.STORE_RUN_PAIR_DAYS,
):
	"""``(trips, unpaired)``: the recorded trips dated From..To, each paired as the Store Runs KPI
	pairs it, and the charges dated From..To that paired with no trip.

	``charges`` and ``receipts`` are ``snapshots._store_run_rows`` rows. Rows dated more than
	``lookback_days`` before ``from_date`` take no part, and neither do charges dated more than
	``pair_days`` after ``to_date``; receipts are kept to the end, whole trips being what is
	paired (the module docstring says why this pairs every trip in range as the KPI does). Trips
	and charges dated outside From..To are dropped after the pairing, not before it. ``store``
	keeps one store's, compared through ``store_key`` so "Lowes" also shows what was recorded at
	"Lowe's": the pairing always runs over every store-run vendor, because the two are one store
	to it.

	``unpaired`` are ``pair_store_runs`` charge entries (``{index, row, store, day, amount,
	paired}``); :func:`build_rows` lists the ones that carry 2210 and recognizes the ones Accounting
	matched to a trip by hand.
	"""
	start = metrics._as_day(from_date)
	end = metrics._as_day(to_date)
	if start is None or end is None or start > end:
		return [], []
	early = start - timedelta(days=lookback_days)
	late = end + timedelta(days=pair_days)

	def dated_from_early(row, until=None):
		day = metrics._as_day(row.get("day"))
		return day is not None and early <= day and (until is None or day <= until)

	trips, bills = metrics.pair_store_runs(
		[row for row in charges or () if dated_from_early(row, until=late)],
		[row for row in receipts or () if dated_from_early(row)],
		store_key,
		pair_days,
	)
	wanted = store_key(store) if store else None

	def kept(entry):
		return start <= entry["day"] <= end and (wanted is None or entry["store"] == wanted)

	return [trip for trip in trips if kept(trip)], [
		bill for bill in bills if not bill["paired"] and kept(bill)
	]


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
	"""The trips of :func:`pair_window` alone: the recorded trips dated From..To, paired as the KPI
	pairs them."""
	return pair_window(charges, receipts, from_date, to_date, store_key, store, lookback_days, pair_days)[0]


def vouchers(trips, unpaired=()):
	"""``{"Journal Entry": [names], "Purchase Invoice": [names]}``: every charge whose 2210 debit the
	report reads -- the charge each trip took, and every charge that paired with none."""
	names = {"Journal Entry": set(), "Purchase Invoice": set()}
	bills = [trip["charge"] for trip in trips or () if trip.get("charge")]
	for bill in bills + list(unpaired or ()):
		row = bill["row"]
		if row.get("voucher_type") in names and row.get("voucher_no"):
			names[row["voucher_type"]].add(row["voucher_no"])
	return {kind: sorted(found) for kind, found in names.items()}


def _distinct(values):
	seen = []
	for value in values:
		if value and value not in seen:
			seen.append(value)
	return seen


def _is_draft(row):
	return row.get("docstatus") in (0, "0")


def _source(row):
	if row.get("source") == "QuickBooks":
		return SOURCE_QBO_DRAFT if _is_draft(row) else SOURCE_QBO
	if row.get("voucher_type") == "Purchase Invoice":
		return SOURCE_PURCHASE_INVOICE
	return SOURCE_JOURNAL_ENTRY


def _stock(trip):
	"""A trip's stock lines before tax: what its receipts credited to 2210."""
	return _cents(sum(metrics._amount(row.get("stock_amount")) for row in trip["receipts"]))


def _corrected(fixes):
	return f" (corrected by {', '.join(fixes)})" if fixes else ""


def _decide(
	charge, draft, stock, moved, account, receipts_billed, receipt_count, invoices, voucher_no, fixes
):
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
			shown = f"The {account} debit is {_money(moved)} but the stock lines are {_money(stock)}"
			if draft:
				return f"{shown}: reduce it to {_money(stock)}, {SAVE_IT}", NEEDS_ACTION, 0.0
			fix = _CORRECTING_ENTRY.format(
				amount=_money(moved - stock),
				lines=f"Dr the expense account the charge used / Cr {account}",
				charge=voucher_no,
			)
			return f"{shown}{_corrected(fixes)}: {fix}", NEEDS_ACTION, 0.0
		if moved < stock:
			missing = _cents(stock - moved)
			if not draft:
				fix = _CORRECTING_ENTRY.format(
					amount=_money(missing),
					lines=f"Dr {account} / Cr the expense account the charge used",
					charge=voucher_no,
				)
				if moved > 0:
					by = f", corrected by {', '.join(fixes)}" if fixes else ""
					return (
						f"Submitted with {_money(missing)} of the goods still on the expense ({_money(moved)} is "
						f"on {account} already{by}): {fix}",
						NEEDS_ACTION,
						missing,
					)
				return f"Submitted with the goods on the expense: {fix}", NEEDS_ACTION, missing
			if moved > 0:
				return (
					f"Move {_money(missing)} more of the goods debit to {account} ({_money(moved)} is "
					f"there already), {SAVE_IT}",
					NEEDS_ACTION,
					missing,
				)
			return f"Move {_money(missing)} of the goods debit to {account}, {SAVE_IT}", NEEDS_ACTION, missing
		if not draft:
			return f"Done{_corrected(fixes)}", DONE, 0.0
		if stock == 0:
			return "No stock lines: nothing to move; the S-D loop submits it", DONE, 0.0
		return (
			f"Goods debit on {account}{_corrected(fixes)}: nothing to change; the S-D loop submits it",
			DONE,
			0.0,
		)
	if receipts_billed and receipts_billed == receipt_count:
		return f"{BILLED_FROM_RECEIPTS} ({invoice_list})", DONE, 0.0
	if receipts_billed:
		return (
			f"Billed from {receipts_billed} of {receipt_count} receipts ({invoice_list}): bill the rest",
			NEEDS_ACTION,
			0.0,
		)
	if stock > 0:
		before = (
			f"before the cutover, find its QuickBooks draft and move {_money(stock)} of its goods debit "
			f"to {account}"
		)
	else:
		before = "no stock lines, so its QuickBooks draft needs no change before the cutover"
	return f"No card charge paired yet: {before}; after the cutover, bill it from the receipts", WAITING, 0.0


def _match_by_hand(trips, unpaired, carried, billed):
	"""``{position of the trip in trips: charge}``: waiting trips and the unpaired charge Accounting
	adjusted by hand to carry exactly their stock lines on 2210 (see the module docstring).

	A trip qualifies with stock lines and no charge and no receipt billed; a charge, at the same
	store with a net 2210 debit equal to the trip's stock lines to the cent. Trips are taken in
	their order (by day), each the nearest-dated charge, then the earlier, then the first read.
	"""
	found = {}
	taken = set()
	for position, trip in enumerate(trips):
		if trip.get("charge") is not None or any(billed.get(row.get("receipt")) for row in trip["receipts"]):
			continue
		stock = _stock(trip)
		if stock <= 0:
			continue
		options = [
			bill
			for bill in unpaired
			if bill["index"] not in taken and bill["store"] == trip["store"] and carried(bill) == stock
		]
		if not options:
			continue
		best = min(
			options, key=lambda bill: (abs((bill["day"] - trip["day"]).days), bill["day"], bill["index"])
		)
		taken.add(best["index"])
		found[position] = best
	return found


def _charge_row(bill, amount, account, fixes):
	"""The row of a charge that paired with no trip but carries ``amount`` (net) on 2210."""
	row = bill["row"]
	draft = _is_draft(row)
	voucher_no = row.get("voucher_no") or ""
	if amount > 0:
		what = (
			f"Carries {_money(amount)} on {account}{_corrected(fixes)} but matches no recorded store run: "
			"move it back to the expense"
		)
		lines = f"Dr the expense account it used / Cr {account}"
	else:
		# Only a correcting entry that reversed too much gets here: bring the charge back to zero.
		what = (
			f"Takes {_money(-amount)} off {account}{_corrected(fixes)} but matches no recorded store run: "
			"bring it back to $0.00"
		)
		lines = f"Dr {account} / Cr the expense account it used"
	if draft:
		what += f", {SAVE_IT}"
	else:
		what += "; " + _CORRECTING_ENTRY.format(amount=_money(abs(amount)), lines=lines, charge=voucher_no)
	return {
		"trip_day": None,
		"store": row.get("supplier"),
		"run_ref": "",
		"receipts": 0,
		"first_receipt": None,
		"recorded_by": "",
		"lines_before_tax": None,
		"stock_amount": 0.0,
		"receipt_total": None,
		"charge_type": row.get("voucher_type") or None,
		"charge": voucher_no or None,
		"charge_date": bill["day"],
		"charge_amount": _cents(bill["amount"]),
		"charge_source": _source(row),
		"match_basis": NO_STORE_RUN,
		"charge_status": DRAFT if draft else SUBMITTED,
		"moved_to_2210": 0,
		"on_2210": amount,
		"to_move": 0.0,
		"action": what,
		"show": NEEDS_ACTION,
		"row_type": ROW_CHARGE,
	}


def build_rows(
	trips, on_2210=None, billed=None, accounts=None, name_of=None, corrections=None, unpaired=None
):
	"""The report's rows, sorted Needs action, Waiting, Done: one per trip from :func:`pair_window`,
	plus one per unpaired charge that carries 2210.

	* ``on_2210``: ``{(voucher_type, voucher_no): amount}``, the net debit each charge's own lines
	  carry on its company's Stock Received But Not Billed account.
	* ``corrections``: ``{voucher_no: {correcting entry: amount}}``, the net 2210 debit of each
	  submitted Journal Entry whose Reference Number is the charge. It is added to the charge's own.
	* ``unpaired``: the charges of :func:`pair_window` that paired with no trip.
	* ``billed``: ``{receipt name: Purchase Invoice}``, receipts a submitted invoice was made from.
	* ``accounts``: ``{company: its Stock Received But Not Billed account}``.
	* ``name_of``: turns a user id into the name shown under Recorded By.

	**The amount to move** is the trip's *stock lines before tax*: what its receipts credited to
	2210 (``stock_amount``, from the GL). **Moved to 2210** means the charge's net 2210 debit, its
	correcting entries included, equals it to the cent. **What to do**:

	* a matched draft short of it: move the difference, then save it (**Needs action**);
	* a matched charge already submitted short of it: the purchase is booked twice until one
	  correcting Journal Entry naming the charge moves the difference (**Needs action**);
	* more than the stock lines on 2210: reduce it, on the draft or with a correcting entry
	  (**Needs action**);
	* a matched draft with it in place, or with no stock lines at all, or a submitted charge with
	  it in place: **Done** -- the S-D loop submits the drafts;
	* no charge, every receipt billed by a submitted Purchase Invoice (after the cutover a
	  recorded trip is billed from its receipts, which clears 2210 itself): **Done**; some of
	  them only: bill the rest (**Needs action**);
	* a trip matched to a charge *and* billed from its receipts has two records of one purchase
	  (**Needs action**);
	* otherwise **Waiting**: no card charge paired yet -- before the cutover its draft is found by
	  hand (and then recognized by its 2210 debit), after it the trip is billed from its receipts;
	* an unpaired charge carrying a net 2210 debit, not found by hand for a trip: move it back to
	  the expense (**Needs action**).
	"""
	on_2210 = on_2210 or {}
	corrections = corrections or {}
	billed = billed or {}
	accounts = accounts or {}
	unpaired = list(unpaired or ())
	trips = list(trips or ())

	def fixes_of(bill):
		return sorted((corrections.get(bill["row"].get("voucher_no") or "") or {}).keys())

	def carried(bill):
		row = bill["row"]
		own = metrics._amount(on_2210.get((row.get("voucher_type") or "", row.get("voucher_no") or "")))
		fixes = corrections.get(row.get("voucher_no") or "") or {}
		return _cents(own + sum(metrics._amount(amount) for amount in fixes.values()))

	by_hand = _match_by_hand(trips, unpaired, carried, billed)
	rows = []
	for position, trip in enumerate(trips):
		receipts = sorted(
			trip["receipts"],
			key=lambda row: (metrics._as_day(row.get("day")) or trip["day"], str(row.get("receipt") or "")),
		)
		first = receipts[0] if receipts else {}
		account = accounts.get(first.get("company")) or DEFAULT_2210
		stock = _stock(trip)
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

		charge = trip.get("charge") or by_hand.get(position)
		charge_row = charge["row"] if charge else {}
		voucher_type = charge_row.get("voucher_type") or ""
		voucher_no = charge_row.get("voucher_no") or ""
		draft = charge is not None and _is_draft(charge_row)
		moved = carried(charge) if charge else 0.0
		fixes = fixes_of(charge) if charge else []

		action, show, to_move = _decide(
			charge, draft, stock, moved, account, receipts_billed, len(receipts), invoices, voucher_no, fixes
		)
		if trip.get("charge") is not None:
			basis = BASIS_LABELS.get(trip.get("basis"), "")
		elif charge is not None:
			basis = MATCHED_BY_HAND
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
				"charge_source": _source(charge_row) if charge else "",
				"match_basis": basis,
				"charge_status": (DRAFT if draft else SUBMITTED) if charge else "",
				"moved_to_2210": 1 if charge is not None and stock > 0 and moved == stock else 0,
				"on_2210": moved,
				"to_move": to_move,
				"action": action,
				"show": show,
				"row_type": ROW_TRIP,
			}
		)

	found = {bill["index"] for bill in by_hand.values()}
	for bill in unpaired:
		amount = carried(bill)
		if bill["index"] in found or amount == 0:
			continue
		rows.append(
			_charge_row(
				bill, amount, accounts.get(bill["row"].get("company")) or DEFAULT_2210, fixes_of(bill)
			)
		)

	rows.sort(
		key=lambda row: (
			_BUCKET_ORDER[row["show"]],
			row["trip_day"] or row["charge_date"],
			str(row["store"] or ""),
			row["run_ref"],
			str(row["charge"] or ""),
		)
	)
	return rows


def filter_rows(rows, show):
	"""The rows the Show filter keeps. Blank, "All" or anything unknown keeps every row."""
	if show in _BUCKET_ORDER:
		return [row for row in rows if row["show"] == show]
	return list(rows)


def summarize(rows):
	"""The figures across the top, over every row in range (the Show filter does not change them).

	``trips`` and ``matched`` count store runs only; ``to_move`` is what the matched charges still
	have to move to 2210; ``moved`` what they already carry there, up to each trip's stock lines;
	``unmatched_on_2210`` the net 2210 debit of the charges that match no recorded store run.
	"""
	trips = [row for row in rows if row["row_type"] == ROW_TRIP]
	matched = [row for row in trips if row["charge"]]
	return {
		"trips": len(trips),
		"matched": len(matched),
		"needs_action": sum(1 for row in rows if row["show"] == NEEDS_ACTION),
		"waiting": sum(1 for row in rows if row["show"] == WAITING),
		"to_move": _cents(sum(row["to_move"] for row in rows)),
		"moved": _cents(sum(max(0.0, min(row["on_2210"], row["stock_amount"])) for row in matched)),
		"unmatched_on_2210": _cents(sum(row["on_2210"] for row in rows if row["row_type"] == ROW_CHARGE)),
	}

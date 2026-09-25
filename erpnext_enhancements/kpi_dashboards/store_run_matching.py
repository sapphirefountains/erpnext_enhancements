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

* **Explicit links.** A Journal Entry (draft or submitted) whose Reference Number (``cheque_no``)
  lists one or more *trip keys* is that trip's charge, whatever the automatic pairing says. A trip
  key is a receipt's run id (``sr-…``), or the receipt's own name when it has none: the ``run``
  column of ``snapshots._store_run_rows``. Several may be listed, separated by commas, semicolons or
  spaces; each is matched trimmed and ignoring case (:func:`reference_tokens`). This is how
  Accounting records a pair the pairing cannot see -- a bank-feed entry dated more than
  ``STORE_RUN_PAIR_DAYS`` late, an amount outside the tolerance, **two runs of one purchase** (one
  draft, two trips), a draft under a QuickBooks vendor that is not a ticked Store-Run Vendor. A
  link **overrides** the automatic pairing for its trips and its charge: a linked trip's automatic
  charge goes back to unpaired, and a linked charge's automatic trip goes back to waiting (its row
  says so, :func:`build_rows`). A linked charge's **target on 2210 is the sum of its linked trips'
  stock lines**, so one draft for two runs reaches Done once it carries both. **Links resolve by
  key, not by date**: a linked trip outside From..To is not shown, but its charge is never listed
  as having no store run. A submitted entry's Reference Number cannot be changed (v16, not
  ``allow_on_submit``), so a charge already submitted is linked by the Reference Number of a
  correcting entry instead (next item).
* **Correcting entries count.** A charge already submitted with the goods on the expense is fixed
  with ONE correcting Journal Entry, Dr 2210 / Cr the expense account the charge used, whose
  Reference Number names the charge's voucher. The 2210 debit of every submitted entry that names
  a charge that way (``corrections``) is added to the charge's own, so the row reaches Done once
  corrected and an over-correction is flagged. Trip keys listed beside the charge's name in a
  correcting entry link that charge to those trips. **Amending the charge is never advised**:
  amending a QuickBooks-synced Journal Entry cancels the original, and ``tabQuickBooks Sync
  Mapping`` -- which the KPI's reader follows to find QuickBooks charges -- stays on the cancelled
  one, so the charge would drop out of the pairing and its trip would read as waiting.
* **A charge carrying 2210 is never lost.** A charge dated in range that is neither paired nor
  linked but carries a net 2210 debit (its own lines plus its correcting entries) -- a draft
  adjusted for a trip but not linked to it, or whose trip's receipt was cancelled -- is listed
  under **Needs action**: link it to its trip, or move it back to the expense.

**A token that names a charge and a token that names a trip cannot be confused**: run ids start
``sr-`` (``stock_scan_rules.RUN_REF_PREFIX``) and receipts are named on the Purchase Receipt series
(``MAT-PRE-…``), while charges are Journal Entries (``ACC-JV-…``) and Purchase Invoices
(``ACC-PINV-…``). A Reference Number that names a charge makes its entry a correcting entry, never
a charge, as it did before links existed.

Pure: no frappe import, so every rule here runs in the bench-free CI suite. The report
(``report/store_run_charge_matching``) does the reads and nothing else.
"""

import re
from datetime import timedelta

from erpnext_enhancements.kpi_dashboards import metrics

#: The Show filter's buckets. "Needs action" is what Accounting works through at step S-D.
SHOW_ALL = "All"
NEEDS_ACTION = "Needs action"
WAITING = "Waiting"
DONE = "Done"
SHOW_OPTIONS = (SHOW_ALL, NEEDS_ACTION, WAITING, DONE)
_BUCKET_ORDER = {NEEDS_ACTION: 0, WAITING: 1, DONE: 2}

#: A trip's basis when a Reference Number links it to its charge (the pairing's two passes are
#: ``metrics.PAIRED_ON_*``).
LINKED_BY_REFERENCE = "reference_number"

#: Match Basis: which pass of the pairing took the charge, the link, or why there is none.
BASIS_LABELS = {
	metrics.PAIRED_ON_RECEIPT_TOTAL: "Receipt total",
	metrics.PAIRED_ON_LINES_PLUS_TAX: "Lines plus tax",
	LINKED_BY_REFERENCE: "Linked by Reference Number",
}
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

#: What separates the keys of one Reference Number.
_SEPARATORS = re.compile(r"[\s,;]+")


def _cents(value):
	return round(metrics._amount(value), 2)


def _money(value):
	cents = _cents(value)
	return f"-${-cents:,.2f}" if cents < 0 else f"${cents:,.2f}"


def reference_tokens(text):
	"""The keys a Reference Number lists: split at commas, semicolons and spaces, trimmed and
	lower-cased, each once, in the order typed. ``"SR-abc, sr-DEF  "`` -> ``["sr-abc", "sr-def"]``."""
	tokens = []
	for token in _SEPARATORS.split(str(text or "")):
		token = token.strip().lower()
		if token and token not in tokens:
			tokens.append(token)
	return tokens


def _run_key(row):
	"""A receipt's trip key, lower-cased: its run id, or its own name when it has none (the reader's
	``run`` column already falls back to the name)."""
	return str(row.get("run") or row.get("receipt") or "").strip().lower()


def _charge_id(row):
	return (row.get("voucher_type") or "", row.get("voucher_no") or "")


def _sorted_receipts(trip):
	return sorted(
		trip["receipts"],
		key=lambda row: (metrics._as_day(row.get("day")) or trip["day"], str(row.get("receipt") or "")),
	)


def trip_keys(trip):
	"""Every key that links ``trip``, lower-cased: the run id of each of its receipts (a trip of
	two runs sharing a receipt number has two), or a receipt's name where it has no run id."""
	keys = []
	for row in trip["receipts"]:
		key = _run_key(row)
		if key and key not in keys:
			keys.append(key)
	return keys


def link_key(trip):
	"""The key the advice names for ``trip``: its first receipt's run id, or that receipt's name
	when it has none, as typed."""
	receipts = _sorted_receipts(trip)
	first = receipts[0] if receipts else {}
	return str(first.get("run") or first.get("receipt") or "").strip()


def _entry_charge(entry):
	"""A Journal Entry the report read for its Reference Number (or by name), shaped as a charge row
	of ``snapshots._store_run_rows``. It has no store: QuickBooks card purchases carry no party."""
	return {
		"supplier": None,
		"day": entry.get("day"),
		"amount": entry.get("amount"),
		"voucher_type": "Journal Entry",
		"voucher_no": entry.get("name"),
		"docstatus": entry.get("docstatus"),
		"source": entry.get("source"),
		"company": entry.get("company"),
	}


def unresolved_tokens(references, charges, receipts):
	"""The keys the ``references`` list that name neither a trip in ``receipts`` nor a charge in
	``charges`` nor one of the ``references`` themselves, sorted.

	The report looks each one up twice: as the run id (or name) of a receipt the KPI's reader did
	not read -- a trip dated before its lookback, which a link still reaches -- and as the name of
	a Journal Entry a correcting entry names.
	"""
	known = {_run_key(row) for row in receipts or ()}
	known |= {str(row.get("voucher_no") or "").strip().lower() for row in charges or ()}
	known |= {str(entry.get("name") or "").strip().lower() for entry in references or ()}
	found = set()
	for entry in references or ():
		found.update(token for token in reference_tokens(entry.get("reference")) if token not in known)
	return sorted(found)


def resolve_links(references, charges, receipts, far_receipts=(), named=()):
	"""``(links, corrections)``: what every Journal Entry with a Reference Number says.

	* ``references``: ``{name, reference, docstatus, day, company, amount, source}`` for every
	  Journal Entry (draft or submitted) with a Reference Number, as the report reads them.
	* ``charges``, ``receipts``: the KPI reader's rows; ``far_receipts``: the receipts of trips it
	  did not read, looked up by key; ``named``: Journal Entries looked up by name (the
	  ``references`` shape), for a correcting entry that names a charge read nowhere else.

	An entry whose Reference Number names a charge -- any Journal Entry or Purchase Invoice in
	``charges``, ``references`` or ``named`` other than itself -- is a **correcting entry** for the
	first charge it names, unless the entry is itself one of the reader's charges (a charge never
	corrects another, as before). Only a submitted one counts: ``corrections`` is ``{charge voucher:
	[entries]}``, and trip keys listed beside the charge link that charge. Any other entry whose
	Reference Number lists trip keys is itself a charge linked to them. ``links`` is ``{(voucher_type,
	voucher_no): {"row": the charge row, "keys": [trip keys]}}``; the reader's own row is used for a
	charge it read, so its store and source stay the KPI's.
	"""
	keys = {_run_key(row) for row in list(receipts or ()) + list(far_receipts or ())} - {""}
	universe = {}
	for entry in list(named or ()) + list(references or ()):
		universe[str(entry.get("name") or "").strip().lower()] = _entry_charge(entry)
	for row in charges or ():
		universe[str(row.get("voucher_no") or "").strip().lower()] = row
	universe.pop("", None)
	reader = {_charge_id(row) for row in charges or ()}

	links = {}
	corrections = {}

	def link(row, tokens):
		entry = links.setdefault(_charge_id(row), {"row": row, "keys": []})
		entry["keys"].extend(token for token in tokens if token not in entry["keys"])

	for entry in sorted(references or (), key=lambda entry: str(entry.get("name") or "")):
		name = str(entry.get("name") or "").strip()
		tokens = reference_tokens(entry.get("reference"))
		trip_tokens = [token for token in tokens if token in keys]
		charge_tokens = [
			token for token in tokens if token in universe and token not in keys and token != name.lower()
		]
		if ("Journal Entry", name) not in reader and charge_tokens:
			if entry.get("docstatus") in (1, "1"):
				charge = universe[charge_tokens[0]]
				corrections.setdefault(charge.get("voucher_no"), []).append(name)
				if trip_tokens:
					link(charge, trip_tokens)
			continue
		if trip_tokens:
			link(universe.get(name.lower()) or _entry_charge(entry), trip_tokens)
	return links, corrections


def _trip_order(trip):
	return (trip["day"], trip["store"], str(trip["key"]))


def _apply_links(trips, bills, links, far_receipts, store_key):
	"""Make every link in ``links`` override the pairing of ``trips`` and ``bills`` (in place).

	Each linked charge gets a *group*: ``{charge, trips, displaced, contested}``, the trips being
	every trip its keys name, read or not (``far_receipts``: grouped into trips as the pairing
	groups them, and paired with nothing). A key that names no trip is ignored, and a charge whose
	keys name none is not linked at all. A linked trip takes the group as ``group`` and its charge
	as ``charge``; its automatic charge, when another, is unpaired again. A trip whose automatic
	charge is linked elsewhere loses it and records the group as ``displaced_by``. A trip two
	charges link is ``contested`` in each of their groups.
	"""
	far_trips, _bills = metrics.pair_store_runs([], far_receipts or (), store_key)
	by_key = {}
	for trip in list(trips) + far_trips:
		for key in trip_keys(trip):
			by_key.setdefault(key, trip)
	by_charge = {_charge_id(bill["row"]): bill for bill in bills}

	groups = []
	claims = {}
	for charge_id in sorted(links):
		link = links[charge_id]
		targets = []
		for key in link["keys"]:
			trip = by_key.get(key)
			if trip is not None and all(trip is not other for other in targets):
				targets.append(trip)
		if not targets:
			continue
		bill = by_charge.get(charge_id)
		if bill is None:
			row = link["row"]
			bill = {
				"index": None,
				"row": row,
				"store": store_key(row.get("supplier") or ""),
				"day": metrics._as_day(row.get("day")),
				"amount": metrics._amount(row.get("amount")),
				"paired": False,
			}
		group = {"charge": bill, "trips": sorted(targets, key=_trip_order), "displaced": [], "contested": []}
		groups.append(group)
		for trip in targets:
			claims.setdefault(id(trip), []).append(group)
	if not groups:
		return

	group_of = {id(group["charge"]): group for group in groups}
	for trip in trips:
		auto = trip["charge"]
		if auto is None:
			continue
		if id(trip) in claims:
			if all(auto is not group["charge"] for group in claims[id(trip)]):
				auto["paired"] = False
		elif id(auto) in group_of:
			trip["displaced_by"] = group_of[id(auto)]
			group_of[id(auto)]["displaced"].append(trip)
		else:
			continue
		trip["charge"] = None
		trip["basis"] = None
	for group in groups:
		group["charge"]["paired"] = True
		for trip in group["trips"]:
			rivals = claims[id(trip)]
			if len(rivals) > 1:
				group["contested"].append((trip, [rival["charge"] for rival in rivals]))
			if trip.get("group") is None:
				trip["group"] = group
				trip["charge"] = group["charge"]
				trip["basis"] = LINKED_BY_REFERENCE


def pair_window(
	charges,
	receipts,
	from_date,
	to_date,
	store_key,
	store=None,
	lookback_days=metrics.STORE_RUN_LOOKBACK_DAYS,
	pair_days=metrics.STORE_RUN_PAIR_DAYS,
	links=None,
	far_receipts=(),
):
	"""``(trips, unpaired)``: the recorded trips dated From..To, each paired as the Store Runs KPI
	pairs it unless a Reference Number links it, and the charges dated From..To that neither
	paired nor are linked.

	``charges`` and ``receipts`` are ``snapshots._store_run_rows`` rows. Rows dated more than
	``lookback_days`` before ``from_date`` take no part, and neither do charges dated more than
	``pair_days`` after ``to_date``; receipts are kept to the end, whole trips being what is
	paired (the module docstring says why this pairs every trip in range as the KPI does). Trips
	and charges dated outside From..To are dropped after the pairing, not before it. ``store``
	keeps one store's, compared through ``store_key`` so "Lowes" also shows what was recorded at
	"Lowe's": the pairing always runs over every store-run vendor, because the two are one store
	to it.

	``links`` (:func:`resolve_links`) then override that pairing (:func:`_apply_links`): they are
	resolved over every trip read, whatever its date, and over ``far_receipts``, so a link to a
	trip outside From..To still takes its charge out of ``unpaired``. With no links this is the
	KPI's pairing alone.

	``unpaired`` are ``pair_store_runs`` charge entries (``{index, row, store, day, amount,
	paired}``); :func:`build_rows` lists the ones that carry 2210.
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
	if links:
		_apply_links(trips, bills, links, far_receipts, store_key)
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
	report reads -- the charge each trip took or is linked to, and every charge that paired with
	none."""
	names = {"Journal Entry": set(), "Purchase Invoice": set()}
	bills = [trip["charge"] for trip in trips or () if trip.get("charge")]
	for bill in bills + list(unpaired or ()):
		row = bill["row"]
		if row.get("voucher_type") in names and row.get("voucher_no"):
			names[row["voucher_type"]].add(row["voucher_no"])
	return {kind: sorted(found) for kind, found in names.items()}


def correcting_entries(found, corrections):
	"""The correcting entries (:func:`resolve_links`) of the charges in ``found`` (:func:`vouchers`),
	sorted: the Journal Entries whose 2210 lines the report reads besides the charges' own."""
	charges = [name for names in (found or {}).values() for name in names]
	return sorted({entry for charge in charges for entry in (corrections or {}).get(charge, ())})


def correction_amounts(corrections, on_2210):
	"""``{charge: {entry: net 2210 debit}}`` for :func:`build_rows`, from the entries of
	:func:`resolve_links` and the 2210 debits read for them. An entry with no line on 2210 moved
	nothing there and is left out, as the report always did."""
	amounts = {}
	for charge, entries in (corrections or {}).items():
		for entry in entries:
			if ("Journal Entry", entry) in (on_2210 or {}):
				amounts.setdefault(charge, {})[entry] = on_2210[("Journal Entry", entry)]
	return amounts


def receipt_names(trips):
	"""The receipts of ``trips`` and of every trip their charges are linked to, sorted: whether a
	submitted Purchase Invoice was made from any of them decides a row."""
	names = set()
	for trip in trips or ():
		group = trip.get("group")
		for each in [trip] + (group["trips"] if group else []):
			names.update(row.get("receipt") for row in each["receipts"] if row.get("receipt"))
	return sorted(names)


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


def _find_its_draft(stock, account, key):
	"""How to find and link a waiting trip's charge: on its draft, or -- a submitted entry's Reference
	Number cannot be changed -- with a correcting entry that names the charge and the trip."""
	return (
		f"find its QuickBooks draft, move {_money(stock)} of its goods debit to {account}, put this "
		f"trip's run id ({key}) in its Reference Number and save it (if it is already submitted, post a "
		f"correcting Journal Entry for {_money(stock)} (Dr {account} / Cr the expense account it used) "
		f"whose Reference Number is its name followed by {key})"
	)


_ONLY_IF_NONE = "Only if it has no card charge at all, bill it from the receipts after the cutover."


def _waiting(stock, account, key, displaced=None):
	"""``(what to do, bucket)`` for a trip with no charge and no receipt billed.

	Either/or, never both: a trip that has a card charge is fixed on that charge and is never
	billed from its receipts (that would credit the card twice). ``displaced`` is the group of the
	charge the trip was paired with until that charge's Reference Number linked other trips."""
	if stock <= 0:
		return (
			"No card charge paired and no stock lines: if it has a QuickBooks draft, change nothing "
			f"(optionally put {key} in its Reference Number). {_ONLY_IF_NONE}",
			WAITING,
		)
	if displaced is None:
		return f"No card charge paired: {_find_its_draft(stock, account, key)}. {_ONLY_IF_NONE}", WAITING
	row = displaced["charge"]["row"]
	charge = row.get("voucher_no") or ""
	if _is_draft(row):
		add = f"add {key} to that Reference Number"
	else:
		add = (
			f"post a correcting Journal Entry for {_money(stock)} (Dr {account} / Cr the expense account it "
			f"used) whose Reference Number is {charge} followed by {key}"
		)
	keys = ", ".join(link_key(trip) for trip in displaced["trips"])
	return (
		f"Its paired charge {charge} now links only {keys}, by its Reference Number: if {charge} pays "
		f"for this trip too, {add}. If not, {_find_its_draft(stock, account, key)}. {_ONLY_IF_NONE}",
		WAITING,
	)


def _over_draft(shown, target, own, corrections, fixes, account, voucher_no):
	"""A draft whose own lines plus its correcting entries carry more than ``target`` on 2210.

	The correcting entries are named, and the draft's own lines are never cut below the target to
	make up for them (v1.538.0 review): a correcting entry too many is reversed by another one."""
	names = ", ".join(fixes)
	reverse = "Dr the expense account the charge used / Cr " + account
	if corrections > 0 and own <= target:
		excess = _cents(own + corrections - target)
		fix = _CORRECTING_ENTRY.format(amount=_money(excess), lines=reverse, charge=voucher_no)
		return (
			f"{shown}: the draft's own lines already carry {_money(own)} and its correcting entries "
			f"({names}) add {_money(corrections)}, {_money(excess)} too many: reverse {_money(excess)} of "
			f"them; {fix}. The S-D loop submits the draft"
		)
	if corrections > 0:
		fix = _CORRECTING_ENTRY.format(amount=_money(corrections), lines=reverse, charge=voucher_no)
		return (
			f"{shown}: the draft's own lines alone carry {_money(own)} and its correcting entries ({names}) "
			f"add {_money(corrections)}: reduce its own lines to {_money(target)}, then save it, and reverse "
			f"the correcting entries; {fix}"
		)
	return (
		f"{shown}: its correcting entries ({names}) take {_money(-corrections)} off, so reduce the draft's "
		f"own lines to {_money(target - corrections)}, {SAVE_IT}"
	)


def _decide(charge, group, target, own, corrections, fixes, account, invoices, linked):
	"""``(what to do, bucket, amount still to move)`` for a charge and the trips it pays for.

	``target`` is the stock lines of all its trips (one, unless a Reference Number links several);
	``own`` and ``corrections`` what its own lines and its correcting entries carry on 2210."""
	row = charge["row"]
	draft = _is_draft(row)
	voucher_no = row.get("voucher_no") or ""
	moved = _cents(own + corrections)
	keys = [link_key(trip) for trip in group["trips"]]
	if group["contested"]:
		parts = "; ".join(
			f"{link_key(trip)} by {', '.join(bill['row'].get('voucher_no') or '' for bill in rivals)}"
			for trip, rivals in group["contested"]
		)
		return (
			f"Linked from more than one charge's Reference Number ({parts}): keep each run id in one "
			"charge's Reference Number only",
			NEEDS_ACTION,
			0.0,
		)
	if invoices:
		how = "linked to this charge by its Reference Number" if linked else "matched to this charge"
		return (
			f"Billed from the receipts ({', '.join(invoices)}) and {how} too: check the purchase is not "
			"booked twice before submitting either",
			NEEDS_ACTION,
			0.0,
		)
	lines = "the stock lines"
	if linked:
		trips = "the trip" if len(keys) == 1 else "the trips"
		lines = f"the stock lines of {trips} its Reference Number links ({', '.join(keys)})"
	hint = ""
	if linked and draft and group["displaced"]:
		before = ", ".join(link_key(trip) for trip in group["displaced"])
		hint = (
			f". It was paired with {before} until its Reference Number linked {', '.join(keys)}: if it pays "
			f"for {before} too, add {before} to its Reference Number instead"
		)
	if moved > target:
		shown = f"The {account} debit is {_money(moved)} but {lines} are {_money(target)}"
		if draft and fixes:
			return (
				_over_draft(shown, target, own, corrections, fixes, account, voucher_no) + hint,
				NEEDS_ACTION,
				0.0,
			)
		if draft:
			return f"{shown}: reduce it to {_money(target)}, {SAVE_IT}{hint}", NEEDS_ACTION, 0.0
		fix = _CORRECTING_ENTRY.format(
			amount=_money(moved - target),
			lines=f"Dr the expense account the charge used / Cr {account}",
			charge=voucher_no,
		)
		return f"{shown}{_corrected(fixes)}: {fix}", NEEDS_ACTION, 0.0
	together = f" (the stock lines of {', '.join(keys)} together)" if len(keys) > 1 else ""
	if moved < target:
		missing = _cents(target - moved)
		if not draft:
			fix = _CORRECTING_ENTRY.format(
				amount=_money(missing),
				lines=f"Dr {account} / Cr the expense account the charge used",
				charge=voucher_no,
			)
			if moved > 0:
				by = f", corrected by {', '.join(fixes)}" if fixes else ""
				return (
					f"Submitted with {_money(missing)} of the goods still on the expense{together} "
					f"({_money(moved)} is on {account} already{by}): {fix}",
					NEEDS_ACTION,
					missing,
				)
			return f"Submitted with the goods on the expense{together}: {fix}", NEEDS_ACTION, missing
		if moved > 0:
			return (
				f"Move {_money(missing)} more of the goods debit to {account}{together} ({_money(moved)} is "
				f"there already), {SAVE_IT}{hint}",
				NEEDS_ACTION,
				missing,
			)
		return (
			f"Move {_money(missing)} of the goods debit to {account}{together}, {SAVE_IT}{hint}",
			NEEDS_ACTION,
			missing,
		)
	if not draft:
		return f"Done{_corrected(fixes)}", DONE, 0.0
	if target == 0:
		return "No stock lines: nothing to move; the S-D loop submits it", DONE, 0.0
	return (
		f"Goods debit on {account}{_corrected(fixes)}: nothing to change; the S-D loop submits it",
		DONE,
		0.0,
	)


def _charge_row(bill, amount, account, fixes):
	"""The row of a charge that is neither paired nor linked but carries ``amount`` (net) on 2210."""
	row = bill["row"]
	draft = _is_draft(row)
	voucher_no = row.get("voucher_no") or ""
	if amount > 0:
		shown = f"Carries {_money(amount)} on {account}{_corrected(fixes)} but no recorded store run is paired or linked"
		if draft:
			what = (
				f"{shown}: if you moved it for a trip, put that trip's run id in this entry's Reference "
				f"Number; otherwise move it back to the expense. Then save it; the S-D loop submits it"
			)
		else:
			fix = _CORRECTING_ENTRY.format(
				amount=_money(amount),
				lines=f"Dr the expense account it used / Cr {account}",
				charge=voucher_no,
			)
			what = (
				f"{shown}, and a submitted entry's Reference Number cannot be changed: move it back to the "
				f"expense; {fix}. If you moved it for a trip, that trip's row then says how to move it again, "
				"linked"
			)
	else:
		# Only a correcting entry that reversed too much gets here: bring the charge back to zero.
		what = (
			f"Takes {_money(-amount)} off {account}{_corrected(fixes)} but no recorded store run is paired or "
			"linked: bring it back to $0.00"
		)
		if draft:
			what += f", {SAVE_IT}"
		else:
			what += "; " + _CORRECTING_ENTRY.format(
				amount=_money(-amount),
				lines=f"Dr {account} / Cr the expense account it used",
				charge=voucher_no,
			)
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
	* ``corrections``: ``{voucher_no: {correcting entry: amount}}`` (:func:`correction_amounts`), the
	  net 2210 debit of each submitted Journal Entry whose Reference Number names the charge. It is
	  added to the charge's own.
	* ``unpaired``: the charges of :func:`pair_window` that neither paired nor are linked.
	* ``billed``: ``{receipt name: Purchase Invoice}``, receipts a submitted invoice was made from.
	* ``accounts``: ``{company: its Stock Received But Not Billed account}``.
	* ``name_of``: turns a user id into the name shown under Recorded By.

	**The amount to move** is the stock lines before tax of the trips a charge pays for: what their
	receipts credited to 2210 (``stock_amount``, from the GL) -- one trip when paired, every trip
	its Reference Number links when linked (the rows of one charge share one *What to Do*).
	**Moved to 2210** means the charge's net 2210 debit, its correcting entries included, equals it
	to the cent. **What to do**:

	* a charge's draft short of it: move the difference, then save it (**Needs action**);
	* a charge already submitted short of it: the purchase is booked twice until one correcting
	  Journal Entry naming the charge moves the difference (**Needs action**);
	* more than the stock lines on 2210: reduce it, on the draft or with a correcting entry; a draft
	  whose correcting entries over-move is told to reverse them, never to cut its own lines below
	  the target (**Needs action**);
	* a draft with it in place, or with no stock lines at all, or a submitted charge with it in
	  place: **Done** -- the S-D loop submits the drafts;
	* no charge, every receipt billed by a submitted Purchase Invoice (after the cutover a
	  recorded trip is billed from its receipts, which clears 2210 itself): **Done**; some of
	  them only: bill the rest (**Needs action**);
	* a charge whose trip is also billed from its receipts has two records of one purchase, and a
	  trip two charges link has two charges (**Needs action**);
	* otherwise **Waiting**, an either/or: find its charge, move the stock lines and link it by
	  Reference Number; only if it has none, bill it from its receipts after the cutover. A trip
	  whose paired charge a Reference Number took for other trips is told to add itself there if
	  that charge pays for it too;
	* a charge neither paired nor linked that carries a net 2210 debit: link it to its trip, or move
	  it back to the expense (**Needs action**).

	``on_2210`` and ``to_move`` on a trip row are its share of its charge's, for the summary: the
	charge's 2210 debit is laid over its trips in date order, the last taking what is left.
	"""
	on_2210 = on_2210 or {}
	corrections = corrections or {}
	billed = billed or {}
	accounts = accounts or {}
	unpaired = list(unpaired or ())
	trips = list(trips or ())

	def own_of(bill):
		return _cents(metrics._amount(on_2210.get(_charge_id(bill["row"]))))

	def fixes_of(bill):
		return corrections.get(bill["row"].get("voucher_no") or "") or {}

	def carried(bill):
		return _cents(own_of(bill) + sum(metrics._amount(amount) for amount in fixes_of(bill).values()))

	def account_of(trip):
		receipts = _sorted_receipts(trip)
		return accounts.get((receipts[0] if receipts else {}).get("company")) or DEFAULT_2210

	decided = {}

	def decide(group, linked):
		if id(group) in decided:
			return decided[id(group)]
		charge = group["charge"]
		fixes = fixes_of(charge)
		corrected = _cents(sum(metrics._amount(amount) for amount in fixes.values()))
		own = own_of(charge)
		target = _cents(sum(_stock(trip) for trip in group["trips"]))
		receipts = [row for trip in group["trips"] for row in _sorted_receipts(trip)]
		invoices = _distinct(billed.get(row.get("receipt")) for row in receipts)
		action, show, missing = _decide(
			charge,
			group,
			target,
			own,
			corrected,
			sorted(fixes),
			account_of(group["trips"][0]),
			invoices,
			linked,
		)
		moved = _cents(own + corrected)
		shares = {}
		remaining = moved
		last = len(group["trips"]) - 1
		for position, trip in enumerate(group["trips"]):
			stock = _stock(trip)
			share = remaining if position == last else min(remaining, stock)
			remaining = _cents(remaining - share)
			shares[id(trip)] = (_cents(share), _cents(stock - min(share, stock)) if missing else 0.0)
		decided[id(group)] = (action, show, target, moved, shares)
		return decided[id(group)]

	rows = []
	for trip in trips:
		receipts = _sorted_receipts(trip)
		first = receipts[0] if receipts else {}
		account = account_of(trip)
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

		charge = trip.get("charge")
		charge_row = charge["row"] if charge else {}
		draft = charge is not None and _is_draft(charge_row)
		if charge is not None:
			group = trip.get("group") or {
				"charge": charge,
				"trips": [trip],
				"displaced": [],
				"contested": [],
			}
			action, show, target, moved, shares = decide(group, trip.get("group") is not None)
			on, to_move = shares[id(trip)]
			basis = BASIS_LABELS.get(trip.get("basis"), "")
			checked = 1 if target > 0 and moved == target else 0
		else:
			on, to_move, checked = 0.0, 0.0, 0
			if receipts_billed and receipts_billed == len(receipts):
				action, show = f"{BILLED_FROM_RECEIPTS} ({', '.join(invoices)})", DONE
			elif receipts_billed:
				action, show = (
					f"Billed from {receipts_billed} of {len(receipts)} receipts ({', '.join(invoices)}): bill the rest",
					NEEDS_ACTION,
				)
			else:
				action, show = _waiting(stock, account, link_key(trip), trip.get("displaced_by"))
			basis = BILLED_FROM_RECEIPTS if receipts_billed else NO_CHARGE_YET
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
				"charge_type": charge_row.get("voucher_type") or None,
				"charge": charge_row.get("voucher_no") or None,
				"charge_date": charge["day"] if charge else None,
				"charge_amount": _cents(charge["amount"]) if charge else None,
				"charge_source": _source(charge_row) if charge else "",
				"match_basis": basis,
				"charge_status": (DRAFT if draft else SUBMITTED) if charge else "",
				"moved_to_2210": checked,
				"on_2210": on,
				"to_move": to_move,
				"action": action,
				"show": show,
				"row_type": ROW_TRIP,
			}
		)

	for bill in unpaired:
		amount = carried(bill)
		if amount == 0:
			continue
		rows.append(
			_charge_row(
				bill, amount, accounts.get(bill["row"].get("company")) or DEFAULT_2210, sorted(fixes_of(bill))
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

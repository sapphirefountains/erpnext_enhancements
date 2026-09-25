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
  key is a receipt's run id (``sr-…``) or the name of any of the trip's receipts (``MAT-PRE-…``,
  the report's First Receipt). Several may be listed, separated by commas, semicolons or spaces;
  each is matched trimmed and ignoring case (:func:`reference_tokens`). This is how Accounting
  records a pair the pairing cannot see -- a bank-feed entry dated more than
  ``STORE_RUN_PAIR_DAYS`` late, an amount outside the tolerance, **two runs of one purchase** (one
  draft, two trips), a draft under a QuickBooks vendor that is not a ticked Store-Run Vendor. A
  link **overrides** the automatic pairing for its trips and its charge: a linked trip's automatic
  charge goes back to unpaired (its row names the link that took it), and a linked charge's
  automatic trip goes back to waiting (its row says so, :func:`build_rows`). A linked charge's
  **target on 2210 is the sum of its linked trips' stock lines**, so one draft for two runs reaches
  Done once it carries both. **Links resolve by key, not by date**: a linked trip outside From..To
  is not shown, but its charge is never listed as having no store run, and a linked charge in
  range whose trips are all outside it gets a row of its own when it needs action. A submitted
  entry's Reference Number cannot be changed (v16, not ``allow_on_submit``), so a charge already
  submitted is linked by the Reference Number of a correcting entry instead (next item).
* **Correcting entries count, through chains.** A charge already submitted with the goods on the
  expense is fixed with ONE correcting Journal Entry, Dr 2210 / Cr the expense account the charge
  used, whose Reference Number names the charge. A Reference Number that names a correcting entry
  names that entry's charge (followed to the end, loops refused), so a reversal written against
  the first correction still counts for the charge. The 2210 debit of every **submitted**
  correcting entry is added to the charge's own, so the row reaches Done once corrected and an
  over-correction is flagged; a draft one counts for nothing and is listed to be submitted. Trip
  keys listed beside the charge's name link that charge to those trips. **Amending the charge is
  never advised**: amending a QuickBooks-synced Journal Entry cancels the original, and
  ``tabQuickBooks Sync Mapping`` -- which the KPI's reader follows to find QuickBooks charges --
  stays on the cancelled one, so the charge would drop out of the pairing.
* **A charge carrying 2210 is never lost.** A charge dated in range that is neither paired nor
  linked but carries a net 2210 debit is listed under **Needs action**: link it to its trip, or
  move it back to the expense (a Purchase Invoice, which books its stock lines to 2210 itself, is
  left to wait for its receipt).
* **Every 2210 line is accounted for** (:func:`attribute_2210`). Every Journal Entry line on a
  company's Stock Received But Not Billed account dated from the lookback to the To Date must
  belong to a charge the report accounts for -- one with trips, shown or not, or one with none
  dated in range. Any entry whose net 2210 amount nothing accounts for gets a row of its own, and
  the summary's *2210 Not Accounted For* totals them: it must be $0.00 before the S-D loop.
* **Reference Numbers that say nothing usable are listed**: a key that names no recorded store
  run, tokens pointing to two different charges, a card charge naming another charge, a draft
  correcting entry. Never first-token-wins, never silently dropped.
* **not-store-run.** The token ``not-store-run`` in a Journal Entry's Reference Number takes it out
  of all this: it is not a correcting entry, not a link, not in the backstop, and a card charge
  carrying it pairs with no trip (in the report only; the KPI still counts it).

**A token that names a charge and a token that names a trip cannot be confused**: run ids start
``sr-`` (``stock_scan_rules.RUN_REF_PREFIX``) and receipts are named on the Purchase Receipt series
(``MAT-PRE-…``), while charges are Journal Entries (``ACC-JV-…``) and Purchase Invoices
(``ACC-PINV-…``). A Reference Number that names a charge makes its entry a correcting entry, never
a charge, unless the entry is one of the KPI's own charges (a charge never corrects another).

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
REFERENCE_TO_CHECK = "Reference Number to check"
NOT_TIED = "Not tied to a store run"

#: The kinds of row: a recorded trip; a charge that carries 2210 with no trip; a linked charge in
#: range whose trips are all outside it; and a Journal Entry whose Reference Number or 2210 lines
#: need a look.
ROW_TRIP = "Store run"
ROW_CHARGE = "Charge with no store run"
ROW_OUTSIDE = "Charge for store runs outside the range"
ROW_ENTRY = "Entry to check"

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

#: The token that takes a Journal Entry out of the list: not a link, not a correction, not in the
#: 2210 backstop, and -- on a card charge -- paired with no trip.
NOT_STORE_RUN = "not-store-run"

#: What a trip key looks like, lower-cased: a run id (``stock_scan_rules.RUN_REF_PREFIX``, restated
#: so this module imports no frappe-bound code; a test compares the two) or a Purchase Receipt's
#: name (production's series). A token of this shape that names no recorded store run is listed.
RUN_PREFIX = "sr-"
RECEIPT_PREFIX = "mat-pre-"

#: How a draft is finished. Accounting saves each adjusted draft; the S-D loop submits every 2026
#: draft together (CHANGELOG 1.538.0, "For Accounting").
SAVE_IT = "then save it; the S-D loop submits it"

#: The one fix for a charge already submitted: a correcting Journal Entry naming the charge in its
#: Reference Number, which the report counts once submitted. Never an amendment (module docstring).
_CORRECTING_ENTRY = (
	"post and submit a correcting Journal Entry for {amount} ({lines}) with Reference Number {charge}"
)

#: What separates the keys of one Reference Number.
_SEPARATORS = re.compile(r"[\s,;]+")


def _cents(value):
	return round(metrics._amount(value), 2)


def _money(value):
	cents = _cents(value)
	return f"-${-cents:,.2f}" if cents < 0 else f"${cents:,.2f}"


def _lower(value):
	return str(value or "").strip().lower()


def _and(items):
	items = [str(item) for item in items]
	if len(items) < 2:
		return "".join(items)
	return f"{', '.join(items[:-1])} and {items[-1]}"


def _short(account):
	"""The account as a text names it after writing it in full once: its number (``2210``)."""
	head = str(account or "").split(" - ")[0].strip()
	return head or account


def _once(text, account):
	"""``text`` with ``account`` written in full at its first mention and by its number after."""
	short = _short(account)
	head, found, tail = text.partition(account)
	if not found or short == account:
		return text
	return head + found + tail.replace(account, short)


def _typed_tokens(text):
	"""``[(lower-cased, as typed)]``: the tokens of a Reference Number, each once, in the order typed."""
	tokens = []
	seen = set()
	for token in _SEPARATORS.split(str(text or "")):
		typed = token.strip()
		lower = typed.lower()
		if lower and lower not in seen:
			seen.add(lower)
			tokens.append((lower, typed))
	return tokens


def reference_tokens(text):
	"""The keys a Reference Number lists: split at commas, semicolons and spaces, trimmed and
	lower-cased, each once, in the order typed. ``"SR-abc, sr-DEF  "`` -> ``["sr-abc", "sr-def"]``."""
	return [lower for lower, _typed in _typed_tokens(text)]


def is_key_shaped(token):
	"""Whether a lower-cased token looks like a trip key: a run id or a Purchase Receipt's name."""
	return any(
		token.startswith(prefix) and len(token) > len(prefix) for prefix in (RUN_PREFIX, RECEIPT_PREFIX)
	)


def _receipt_keys(row):
	"""A receipt's keys, lower-cased: its run id (the reader's ``run``, which falls back to its name)
	and its own name."""
	return _distinct([_lower(row.get("run")), _lower(row.get("receipt"))])


def _charge_id(row):
	return (row.get("voucher_type") or "", row.get("voucher_no") or "")


def _sorted_receipts(trip):
	return sorted(
		trip["receipts"],
		key=lambda row: (metrics._as_day(row.get("day")) or trip["day"], str(row.get("receipt") or "")),
	)


def trip_keys(trip):
	"""Every key that links ``trip``, lower-cased: the run id of each of its receipts (a trip of two
	runs sharing a receipt number has two) and each receipt's own name."""
	keys = []
	for row in trip["receipts"]:
		keys.extend(key for key in _receipt_keys(row) if key not in keys)
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

	The report looks each one up twice -- as a run id or receipt name of a receipt the KPI's reader
	did not read (a trip dated before its lookback, which a link still reaches), and as the name of
	a Journal Entry -- and repeats with the tokens of what it found, so a chain of correcting entries
	is followed to its charge. ``not-store-run`` is never looked up.
	"""
	known = {key for row in receipts or () for key in _receipt_keys(row)}
	known |= {_lower(row.get("voucher_no")) for row in charges or ()}
	known |= {_lower(entry.get("name")) for entry in references or ()}
	known.add(NOT_STORE_RUN)
	found = set()
	for entry in references or ():
		found.update(token for token in reference_tokens(entry.get("reference")) if token not in known)
	return sorted(found)


def resolve_references(references, charges, receipts, far_receipts=(), named=()):
	"""What every Journal Entry's Reference Number says.

	* ``references``: ``{name, reference, docstatus, day, company, amount, source}`` for every
	  Journal Entry (draft or submitted) with a Reference Number, as the report reads them.
	* ``charges``, ``receipts``: the KPI reader's rows; ``far_receipts``: the receipts of trips it
	  did not read, looked up by key; ``named``: Journal Entries looked up by name (the
	  ``references`` shape), for a token that names an entry read nowhere else.

	Each token of a Reference Number is ``not-store-run``, a trip key (a run id or receipt name of
	a recorded store run), the name of a known entry or charge, a dead key (shaped like a trip key
	but naming no recorded store run), or anything else (ignored: a card number, a bank reference).
	**Naming an entry names what it resolves to**: one of the KPI's charges is itself; an entry
	whose Reference Number names exactly one charge (directly or through other entries) resolves to
	that charge; an entry naming none resolves to itself. A loop, or tokens that lead to two
	different charges, resolve to nothing. So a reversal whose Reference Number names the first
	correcting entry is a correction of the charge, not of the correcting entry.

	Returns ``{"links", "corrections", "drafts", "excluded", "problems", "roots", "entries"}``:

	* ``links``: ``{(voucher_type, voucher_no): {"row", "keys", "carriers"}}`` -- the charge row (the
	  reader's own for a charge it read, so its store and source stay the KPI's), the trip keys that
	  link it, and for each key the entries whose Reference Number carries it (``{name,
	  docstatus, source}``). An entry that is one of the KPI's charges, or whose Reference Number
	  lists trip keys and names no charge, links itself; a submitted correcting entry links its
	  charge to the keys beside the charge's name.
	* ``corrections``: ``{charge voucher: [submitted correcting entries]}``;
	  ``drafts``: ``{charge voucher: [draft correcting entries]}`` (they count for nothing).
	* ``excluded``: the entries whose Reference Number says ``not-store-run`` and lists nothing that
	  contradicts it (entries that are themselves ``not-store-run`` may be named beside it).
	* ``problems``: ``{entry: [(kind, detail)]}`` -- ``dead`` keys, ``both`` (two charges),
	  ``nsr_and`` (not-store-run beside a key or charge), ``names_nsr``, ``charge_names`` (one of the
	  KPI's charges naming another charge), ``unresolved`` (a loop or a conflict further on),
	  ``draft`` (a draft correcting entry). :func:`build_rows` lists them.
	* ``roots``: the charge rows correcting entries resolve to (a charge nothing else pairs with
	  still gets a row, :func:`match_window`); ``entries``: every entry read, by name.
	"""
	keys = set()
	for row in list(receipts or ()) + list(far_receipts or ()):
		keys.update(_receipt_keys(row))
	reader = {}
	for row in charges or ():
		name = _lower(row.get("voucher_no"))
		if name:
			reader.setdefault(name, row)
	entries = {}
	for entry in list(named or ()) + list(references or ()):
		name = _lower(entry.get("name"))
		if name:
			entries[name] = entry
	universe = set(reader) | set(entries)

	def spelled(name):
		if name in reader:
			return reader[name].get("voucher_no") or name
		return (entries.get(name) or {}).get("name") or name

	memo = {}

	def read(name, stack):
		found = {
			"nsr": False,
			"trips": [],
			"typed": [],
			"dead": [],
			"roots": [],
			"nsr_named": [],
			"unresolved": [],
		}
		for token, typed in _typed_tokens((entries.get(name) or {}).get("reference")):
			if token == NOT_STORE_RUN:
				found["nsr"] = True
			elif token in keys:
				found["trips"].append(token)
				found["typed"].append(typed)
			elif token in universe:
				if token == name:
					continue
				kind, where = target(token, stack)
				if kind == "charge":
					if where not in found["roots"]:
						found["roots"].append(where)
				elif kind == "nsr":
					found["nsr_named"].append(typed)
				else:
					found["unresolved"].append(typed)
			elif is_key_shaped(token):
				found["dead"].append(typed)
		found["clean_nsr"] = found["nsr"] and not (
			found["trips"] or found["roots"] or found["dead"] or found["unresolved"]
		)
		return found

	def bad(found):
		return bool(
			found["unresolved"]
			or (found["nsr"] and not found["clean_nsr"])
			or len(found["roots"]) > 1
			or (found["nsr_named"] and not found["nsr"])
		)

	def target(name, stack):
		"""What naming ``name`` points to: ``("charge", root)``, ``("nsr", name)`` or ``("bad", name)``."""
		if name in memo:
			return memo[name]
		if name in stack:
			# A loop: every entry on it resolves to nothing. Not memoized, so an entry reached
			# again outside the loop is read afresh.
			return ("bad", name)
		found = read(name, stack | {name})
		if found["clean_nsr"]:
			result = ("nsr", name)
		elif name in reader:
			result = ("charge", name)
		elif bad(found):
			result = ("bad", name)
		elif found["roots"]:
			result = ("charge", found["roots"][0])
		else:
			result = ("charge", name)
		memo[name] = result
		return result

	links = {}
	corrections = {}
	drafts = {}
	excluded = set()
	problems = {}
	roots = {}

	def charge_row(name):
		return reader.get(name) or _entry_charge(entries[name])

	def link(name, tokens, carrier):
		row = charge_row(name)
		found = links.setdefault(_charge_id(row), {"row": row, "keys": [], "carriers": {}})
		for token in tokens:
			if token not in found["keys"]:
				found["keys"].append(token)
			carriers = found["carriers"].setdefault(token, [])
			if carrier not in carriers:
				carriers.append(carrier)

	for name in sorted(entries):
		entry = entries[name]
		own = entry.get("name") or name
		submitted = entry.get("docstatus") in (1, "1")
		carrier = {"name": own, "docstatus": 1 if submitted else 0, "source": entry.get("source")}
		found = read(name, {name})
		issues = []
		others = found["typed"] + [spelled(root) for root in found["roots"] if root != name] + found["dead"]
		others += found["unresolved"]
		if found["nsr"] and not found["clean_nsr"]:
			issues.append(("nsr_and", others))
		elif found["dead"]:
			issues.append(("dead", found["dead"]))
		if name in reader:
			strays = [spelled(root) for root in found["roots"] if root != name]
			strays += found["nsr_named"] + found["unresolved"]
			if strays and not found["nsr"]:
				issues.append(("charge_names", strays))
			if found["clean_nsr"]:
				excluded.add(own)
			elif found["trips"] and not found["nsr"]:
				link(name, found["trips"], carrier)
		elif found["clean_nsr"]:
			excluded.add(own)
		elif found["nsr"]:
			pass
		elif found["unresolved"]:
			issues.append(("unresolved", found["unresolved"]))
		elif len(found["roots"]) > 1:
			issues.append(("both", [spelled(root) for root in found["roots"]]))
		elif found["nsr_named"]:
			issues.append(("names_nsr", found["nsr_named"]))
		elif found["roots"]:
			root = found["roots"][0]
			charge = spelled(root)
			roots.setdefault(root, charge_row(root))
			if submitted:
				corrections.setdefault(charge, []).append(own)
				if found["trips"]:
					link(root, found["trips"], carrier)
			else:
				drafts.setdefault(charge, []).append(own)
				issues.append(("draft", charge))
		elif found["trips"]:
			link(name, found["trips"], carrier)
		if issues:
			problems[own] = issues
	return {
		"links": links,
		"corrections": corrections,
		"drafts": drafts,
		"excluded": excluded,
		"problems": problems,
		"roots": [roots[name] for name in sorted(roots)],
		"entries": {entry.get("name") or name: entry for name, entry in entries.items()},
	}


def resolve_links(references, charges, receipts, far_receipts=(), named=()):
	"""``(links, corrections)`` of :func:`resolve_references`, for a caller that needs no more."""
	resolved = resolve_references(references, charges, receipts, far_receipts, named)
	return resolved["links"], resolved["corrections"]


def _trip_order(trip):
	return (trip["day"], trip["store"], str(trip["key"]))


def _group(bill, trips=(), linked=False, carriers=None):
	return {
		"charge": bill,
		"trips": list(trips),
		"displaced": [],
		"contested": [],
		"carriers": carriers or {},
		"linked": linked,
	}


def _bill(row, store_key):
	return {
		"index": None,
		"row": row,
		"store": store_key(row.get("supplier") or ""),
		"day": metrics._as_day(row.get("day")),
		"amount": metrics._amount(row.get("amount")),
		"paired": False,
	}


def _apply_links(trips, bills, links, far_receipts, store_key):
	"""Make every link in ``links`` override the pairing of ``trips`` and ``bills`` (in place), and
	return the linked groups.

	Each linked charge gets a *group*: ``{charge, trips, displaced, contested, carriers, linked}``,
	the trips being every trip its keys name, read or not (``far_receipts``: grouped into trips as
	the pairing groups them, and paired with nothing; a far receipt the reader read, or of a run it
	read in part, belongs to the reader's trip). A key that names no trip is ignored, and a charge
	whose keys name none is not linked at all. A linked trip takes the group as ``group`` and its
	charge as ``charge``; its automatic charge, when another, is unpaired again and records the trip
	and the groups that took it (``unlinked``). A trip whose automatic charge is linked elsewhere
	loses it and records the group as ``displaced_by``. A trip two charges link is ``contested`` in
	each of their groups, with the rival groups.
	"""
	by_key = {}
	for trip in trips:
		for key in trip_keys(trip):
			by_key.setdefault(key, trip)
	read = {_lower(row.get("receipt")) for trip in trips for row in trip["receipts"]}
	far = []
	seen = set()
	for row in far_receipts or ():
		name = _lower(row.get("receipt"))
		if (name and name in read) or (name and name in seen):
			continue
		seen.add(name)
		run = _lower(row.get("run"))
		if run in by_key:
			if name:
				by_key.setdefault(name, by_key[run])
			continue
		far.append(row)
	far_trips, _bills = metrics.pair_store_runs([], far, store_key)
	for trip in far_trips:
		for key in trip_keys(trip):
			by_key.setdefault(key, trip)
	by_charge = {_charge_id(bill["row"]): bill for bill in bills}

	groups = []
	claims = {}
	for charge_id in sorted(links or {}):
		link = links[charge_id]
		targets = []
		for key in link["keys"]:
			trip = by_key.get(key)
			if trip is not None and all(trip is not other for other in targets):
				targets.append(trip)
		if not targets:
			continue
		bill = by_charge.get(charge_id) or _bill(link["row"], store_key)
		group = _group(bill, sorted(targets, key=_trip_order), linked=True, carriers=link.get("carriers"))
		groups.append(group)
		for trip in targets:
			claims.setdefault(id(trip), []).append(group)
	if not groups:
		return []

	group_of = {id(group["charge"]): group for group in groups}
	for trip in trips:
		auto = trip["charge"]
		if auto is None:
			continue
		if id(trip) in claims:
			if all(auto is not group["charge"] for group in claims[id(trip)]):
				auto["paired"] = False
				if id(auto) not in group_of:
					auto["unlinked"] = {"trip": trip, "groups": claims[id(trip)]}
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
				group["contested"].append((trip, rivals))
			if trip.get("group") is None:
				trip["group"] = group
				trip["charge"] = group["charge"]
				trip["basis"] = LINKED_BY_REFERENCE
	return groups


def match_window(
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
	excluded=(),
	roots=(),
):
	"""The pairing of the rows for From..To, with every charge's *group*.

	``charges`` and ``receipts`` are ``snapshots._store_run_rows`` rows. Rows dated more than
	``lookback_days`` before ``from_date`` take no part, and neither do charges dated more than
	``pair_days`` after ``to_date``; receipts are kept to the end, whole trips being what is paired
	(the module docstring says why this pairs every trip in range as the KPI does). A charge whose
	voucher is in ``excluded`` (its Reference Number says ``not-store-run``) is left out of the
	pairing. ``links`` (:func:`resolve_references`) then override it (:func:`_apply_links`): they
	are resolved over every trip read, whatever its date, and over ``far_receipts``.

	Every charge ends up in exactly one group ``{charge, trips, displaced, contested, carriers,
	linked, shown, accounted}``: a linked one, an automatic pair, or one with no trips (a charge that
	paired with none, or one of ``roots`` -- a charge correcting entries name that nothing else
	pairs with). ``shown``: a trip of it is dated From..To at ``store`` (compared through
	``store_key`` so "Lowes" also shows "Lowe's"), or it has none and its charge is. ``accounted``:
	it has trips, or its charge is dated From..To -- its 2210 lines are then accounted for
	(:func:`attribute_2210`), whatever the Store filter.

	Returns ``{"trips", "unpaired", "groups", "outside", "early", "start", "end", "store"}``:
	``trips`` the trips shown, ``unpaired`` the charges of the shown groups with no trips (the
	``pair_store_runs`` charge entries ``{index, row, store, day, amount, paired}``), ``outside`` the
	linked groups whose charge is dated From..To and none of whose trips is.
	"""
	start = metrics._as_day(from_date)
	end = metrics._as_day(to_date)
	wanted = store_key(store) if store else None
	window = {
		"trips": [],
		"unpaired": [],
		"groups": [],
		"outside": [],
		"early": None,
		"start": start,
		"end": end,
		"store": wanted,
	}
	if start is None or end is None or start > end:
		return window
	early = start - timedelta(days=lookback_days)
	late = end + timedelta(days=pair_days)
	window["early"] = early
	left_out = {_lower(name) for name in excluded or ()}

	def dated_from_early(row, until=None):
		day = metrics._as_day(row.get("day"))
		return day is not None and early <= day and (until is None or day <= until)

	trips, bills = metrics.pair_store_runs(
		[
			row
			for row in charges or ()
			if dated_from_early(row, until=late) and _lower(row.get("voucher_no")) not in left_out
		],
		[row for row in receipts or () if dated_from_early(row)],
		store_key,
		pair_days,
	)
	groups = _apply_links(trips, bills, links, far_receipts, store_key) if links else []
	for trip in trips:
		if trip["charge"] is not None and trip.get("group") is None:
			trip["group"] = _group(trip["charge"], [trip])
			groups.append(trip["group"])
	placed = {_charge_id(group["charge"]["row"]) for group in groups}
	for bill in bills:
		placed.add(_charge_id(bill["row"]))
		if not bill["paired"]:
			groups.append(_group(bill))
	for row in roots or ():
		if _charge_id(row) not in placed and _lower(row.get("voucher_no")) not in left_out:
			placed.add(_charge_id(row))
			groups.append(_group(_bill(row, store_key)))

	def dated_in(day):
		return day is not None and start <= day <= end

	def kept(entry):
		return dated_in(entry["day"]) and (wanted is None or entry["store"] == wanted)

	for group in groups:
		if group["trips"]:
			group["shown"] = any(kept(trip) for trip in group["trips"])
			group["accounted"] = True
		else:
			group["shown"] = kept(group["charge"])
			group["accounted"] = dated_in(group["charge"]["day"])
	window.update(
		trips=[trip for trip in trips if kept(trip)],
		unpaired=[group["charge"] for group in groups if not group["trips"] and group["shown"]],
		groups=groups,
		outside=[
			group
			for group in groups
			if group["linked"]
			and dated_in(group["charge"]["day"])
			and not any(dated_in(trip["day"]) for trip in group["trips"])
			and (
				wanted is None
				or group["charge"]["store"] == wanted
				or any(trip["store"] == wanted for trip in group["trips"])
			)
		],
	)
	return window


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
	excluded=(),
):
	"""``(trips, unpaired)`` of :func:`match_window`: the recorded trips dated From..To, each paired
	as the Store Runs KPI pairs it unless a Reference Number links it, and the charges dated From..To
	that neither paired nor are linked. With no links and nothing excluded this is the KPI's
	pairing alone."""
	window = match_window(
		charges,
		receipts,
		from_date,
		to_date,
		store_key,
		store,
		lookback_days,
		pair_days,
		links=links,
		far_receipts=far_receipts,
		excluded=excluded,
	)
	return window["trips"], window["unpaired"]


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


def vouchers(trips, unpaired=(), groups=()):
	"""``{"Journal Entry": [names], "Purchase Invoice": [names]}``: every charge whose 2210 debit the
	report reads by name -- the charge each trip took or is linked to, every charge that paired with
	none, and the charges of ``groups`` (the linked charges whose trips are all outside the range)."""
	names = {"Journal Entry": set(), "Purchase Invoice": set()}
	bills = [trip["charge"] for trip in trips or () if trip.get("charge")]
	bills += list(unpaired or ()) + [group["charge"] for group in groups or ()]
	for bill in bills:
		row = bill["row"]
		if row.get("voucher_type") in names and row.get("voucher_no"):
			names[row["voucher_type"]].add(row["voucher_no"])
	return {kind: sorted(found) for kind, found in names.items()}


def correcting_entries(found, corrections, drafts=None):
	"""The correcting entries (:func:`resolve_references`), submitted and draft, of the charges in
	``found`` (:func:`vouchers`), sorted: the Journal Entries whose 2210 lines the report reads
	besides the charges' own."""
	charges = [name for names in (found or {}).values() for name in names]
	entries = set()
	for table in (corrections or {}, drafts or {}):
		entries.update(entry for charge in charges for entry in table.get(charge, ()))
	return sorted(entries)


def correction_amounts(corrections, on_2210):
	"""``{charge: {entry: net 2210 debit}}`` for :func:`build_rows`, from the entries of
	:func:`resolve_references` and the 2210 debits read for them. An entry with no line on 2210 moved
	nothing there and is left out, as the report always did."""
	amounts = {}
	for charge, entries in (corrections or {}).items():
		for entry in entries:
			if ("Journal Entry", entry) in (on_2210 or {}):
				amounts.setdefault(charge, {})[entry] = on_2210[("Journal Entry", entry)]
	return amounts


def receipt_names(trips, groups=()):
	"""The receipts of ``trips``, of every trip their charges are linked to, and of the trips of
	``groups``, sorted: whether a submitted Purchase Invoice was made from any of them decides a
	row."""
	names = set()
	everyone = []
	for trip in trips or ():
		group = trip.get("group")
		everyone += [trip] + (group["trips"] if group else [])
	for group in groups or ():
		everyone += group["trips"]
	for each in everyone:
		names.update(row.get("receipt") for row in each["receipts"] if row.get("receipt"))
	return sorted(names)


def companies(window, journal=(), resolution=None):
	"""The companies whose Stock Received But Not Billed account a row may name: of every receipt and
	charge of the window's groups, and of every entry read."""
	found = set()
	for group in (window or {}).get("groups") or ():
		found.add(group["charge"]["row"].get("company"))
		found.update(row.get("company") for trip in group["trips"] for row in trip["receipts"])
	for trip in (window or {}).get("trips") or ():
		found.update(row.get("company") for row in trip["receipts"])
	found.update(entry.get("company") for entry in journal or ())
	found.update(entry.get("company") for entry in ((resolution or {}).get("entries") or {}).values())
	return sorted(filter(None, found))


def attribute_2210(window, resolution=None, on_2210=None, corrections=None, journal=()):
	"""Where each Journal Entry's net 2210 amount is accounted for.

	``journal`` are the entries the report read for the backstop: every Journal Entry, draft or
	submitted, with a line on its company's Stock Received But Not Billed account, dated from the
	lookback to the To Date (``{name, reference, docstatus, day, company, amount, source}``);
	``on_2210`` their net debits there (and those of every charge and correcting entry read by
	name); ``corrections`` :func:`correction_amounts`.

	An entry is **attributed** when it is the charge of a group, or a submitted correcting entry of
	one, and the group is ``accounted`` (:func:`match_window`: it has trips, shown or not, or its
	charge is dated in range) -- its amount is then in that group's own figure. It is **excluded**
	when its Reference Number says ``not-store-run``. Otherwise it is **unaccounted**, and ``outside``
	names the group when it has one (a charge dated outside the range that pairs with nothing).

	Returns ``{"attributed": {entry: amount}, "unaccounted": {entry: amount}, "excluded": {entry:
	amount}, "outside": {entry: group}}``. The three amounts together are every amount considered:
	each backstop entry, and each entry of an accounted group read by name.
	"""
	on_2210 = on_2210 or {}
	excluded = set((resolution or {}).get("excluded") or ())
	owners = {}
	for group in (window or {}).get("groups") or ():
		row = group["charge"]["row"]
		voucher = row.get("voucher_no") or ""
		if row.get("voucher_type") == "Journal Entry" and voucher:
			owners.setdefault(voucher, group)
		for entry in (corrections or {}).get(voucher) or {}:
			owners.setdefault(entry, group)
	names = {entry.get("name") for entry in journal or () if entry.get("name")}
	names |= {
		name for name, group in owners.items() if group["accounted"] and ("Journal Entry", name) in on_2210
	}
	result = {"attributed": {}, "unaccounted": {}, "excluded": {}, "outside": {}}
	for name in sorted(names):
		amount = _cents(on_2210.get(("Journal Entry", name)))
		group = owners.get(name)
		if name in excluded:
			result["excluded"][name] = amount
		elif group is not None and group["accounted"]:
			result["attributed"][name] = amount
		else:
			result["unaccounted"][name] = amount
			if group is not None:
				result["outside"][name] = group
	return result


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


def _carriers(group, trip):
	"""The entries whose Reference Number carries one of ``trip``'s keys for ``group``'s charge."""
	found = []
	for key in trip_keys(trip):
		for carrier in (group.get("carriers") or {}).get(key, ()):
			if carrier not in found:
				found.append(carrier)
	return found


def _carried_in(group, trip):
	"""Where ``group``'s charge has ``trip``'s run id: "ACC-JV-7 (its own Reference Number, a draft)"."""
	charge = group["charge"]["row"].get("voucher_no") or ""
	where = []
	for carrier in _carriers(group, trip):
		state = "submitted" if carrier["docstatus"] == 1 else "a draft"
		if carrier["name"] == charge:
			where.append(f"its own Reference Number, {state}")
		else:
			where.append(f"the Reference Number of {carrier['name']}, {state}")
	return f"{charge} ({'; '.join(where)})" if where else charge


def _linked_by(groups, trip):
	"""Who took ``trip``: "ACC-JV-7 linked sr-a" or "ACC-JV-900 linked sr-a to ACC-JV-7"."""
	key = link_key(trip)
	said = []
	for group in groups:
		charge = group["charge"]["row"].get("voucher_no") or ""
		carriers = _carriers(group, trip) or [{"name": charge}]
		for carrier in carriers:
			said.append(
				f"{charge} linked {key}"
				if carrier["name"] == charge
				else f"{carrier['name']} linked {key} to {charge}"
			)
	return _and(_distinct(said))


def _find_its_draft(stock, account, key):
	"""How to find and link a waiting trip's charge: on its draft (listing both run ids when that
	draft already pays for another trip), or -- a submitted entry's Reference Number cannot be
	changed -- with a correcting entry that names the charge and the trip."""
	return (
		f"find its QuickBooks draft, move {_money(stock)} of its goods debit to {account}, put this trip's "
		f"run id ({key}) in its Reference Number and save it. If that draft is already another trip's "
		"charge in this list, list both trips' run ids in its Reference Number and move both trips' stock "
		f"lines together. If it is already submitted, post and submit a correcting Journal Entry for "
		f"{_money(stock)} (Dr {account} / Cr the expense account it used) whose Reference Number is its name "
		f"followed by {key}"
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
		add = f"add {key} to that Reference Number and move this trip's {_money(stock)} of stock lines too"
	else:
		add = (
			f"post and submit a correcting Journal Entry for {_money(stock)} (Dr {account} / Cr the expense "
			f"account it used) whose Reference Number is {charge} followed by {key}"
		)
	keys = ", ".join(link_key(trip) for trip in displaced["trips"])
	return (
		f"Its paired charge {charge} now links only {keys}, by its Reference Number. If {charge} pays for "
		f"this trip too, {add}. If not, {_find_its_draft(stock, account, key)}. {_ONLY_IF_NONE}",
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


def _also_pays(group, target, moved, account):
	"""The question a linked draft asks first when its link took another trip's automatic pair:
	"If this draft also pays for sr-a, add sr-a to its Reference Number …"."""
	before = [link_key(trip) for trip in group["displaced"]]
	both = [link_key(trip) for trip in group["trips"]] + before
	alt = _cents(target + sum(_stock(trip) for trip in group["displaced"]))
	together = f"the stock lines of {', '.join(both)} together"
	if moved == alt:
		after = f" (it then carries exactly {together})"
	elif moved < alt:
		more = " more" if moved > 0 else ""
		after = f" and move {_money(alt - moved)}{more} of the goods debit to {account} ({together})"
	else:
		after = f" and reduce its {account} debit to {_money(alt)} ({together})"
	names = _and(before)
	return f"If this draft also pays for {names}, add {names} to its Reference Number{after}"


def _contested(group, account):
	"""A trip two charges' Reference Numbers link: who carries each run id, and how one comes out."""
	parts = []
	for trip, rivals in group["contested"]:
		parts.append(f"{link_key(trip)} by {_and(_carried_in(rival, trip) for rival in rivals)}")
	return (
		f"Linked from more than one charge's Reference Number: {'; '.join(parts)}. Which charge is the "
		"trip's is Accounting's call. Take the run id out of the other one's Reference Number (a draft: "
		"edit it and save it; a submitted correcting entry: cancel it; a submitted charge's own Reference "
		f"Number cannot be changed, so keep that one), and move the dropped charge's debit on {account} "
		"back to the expense; its own row then says how"
	)


def _billed_text(row, group, invoices, linked):
	"""A charge paired or linked with a trip that is billed from its receipts: the invoice, already
	submitted, books the purchase, so the charge must not book it too."""
	bills = _and(invoices)
	how = "linked to this charge by its Reference Number" if linked else "matched to this charge"
	head = (
		f"Billed from the receipts ({', '.join(invoices)}) and {how} too: {bills} already books this "
		"purchase, so this charge must not book it again."
	)
	if linked:
		where = _and(_distinct(_carried_in(group, trip) for trip in group["trips"]))
		other = (
			"If the charge is not this trip's, take this trip's run id out of the Reference Number that links "
			f"it, {where}: edit a draft and save it, cancel a submitted correcting entry."
		)
	elif _is_draft(row):
		other = (
			"If the charge is not this trip's, put its own trip's run id in its Reference Number, or "
			f"{NOT_STORE_RUN} if it pays for no store run, and save it."
		)
	else:
		other = (
			"If the charge is not this trip's, its own trip's row says how to link it (a submitted "
			"entry's Reference Number cannot be changed)."
		)
	return (
		f"{head} {other} If it is, it is the payment of {bills}, not a second purchase: its debit belongs "
		"on the store's payable, not on the expense"
	)


def _decide(charge, group, target, own, corrections, fixes, account, invoices, linked):
	"""``(what to do, bucket, amount still to move)`` for a charge and the trips it pays for.

	``target`` is the stock lines of all its trips (one, unless a Reference Number links several);
	``own`` and ``corrections`` what its own lines and its submitted correcting entries carry on
	2210."""
	row = charge["row"]
	draft = _is_draft(row)
	voucher_no = row.get("voucher_no") or ""
	moved = _cents(own + corrections)
	keys = [link_key(trip) for trip in group["trips"]]
	if group["contested"]:
		return _contested(group, account), NEEDS_ACTION, 0.0
	if invoices:
		return _billed_text(row, group, invoices, linked), NEEDS_ACTION, 0.0
	lines = "the stock lines"
	if linked:
		trips = "the trip" if len(keys) == 1 else "the trips"
		lines = f"the stock lines of {trips} its Reference Number links ({', '.join(keys)})"
	together = f" (the stock lines of {', '.join(keys)} together)" if len(keys) > 1 else ""
	# A linked draft whose link took another trip's automatic pair asks first whether it pays for
	# that trip too (two runs of one purchase), then says what to do if not (v1.538.0 third review).
	ask = _also_pays(group, target, moved, account) if linked and draft and group["displaced"] else ""
	if moved > target:
		shown = f"The {account} debit is {_money(moved)} but {lines} are {_money(target)}"
		if draft and fixes:
			text = _over_draft(shown, target, own, corrections, fixes, account, voucher_no)
			return (f"{ask}. Otherwise: {text}" if ask else text), NEEDS_ACTION, 0.0
		if draft and ask:
			text = f"{shown}. {ask}; otherwise reduce it to {_money(target)}. Then save it; the S-D loop submits it"
			return text, NEEDS_ACTION, 0.0
		if draft:
			return f"{shown}: reduce it to {_money(target)}, {SAVE_IT}", NEEDS_ACTION, 0.0
		fix = _CORRECTING_ENTRY.format(
			amount=_money(moved - target),
			lines=f"Dr the expense account the charge used / Cr {account}",
			charge=voucher_no,
		)
		return f"{shown}{_corrected(fixes)}: {fix}", NEEDS_ACTION, 0.0
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
		if corrections < 0 and own >= target:
			# Its own lines carry the stock lines; its correcting entries took too much back off.
			fix = _CORRECTING_ENTRY.format(
				amount=_money(missing),
				lines=f"Dr {account} / Cr the expense account the charge used",
				charge=voucher_no,
			)
			text = (
				f"The {account} debit is {_money(moved)} but {lines} are {_money(target)}: the draft's own "
				f"lines carry {_money(own)} and its correcting entries ({', '.join(fixes)}) take "
				f"{_money(-corrections)} off, {_money(missing)} too much: {fix}. The S-D loop submits the draft"
			)
			return (f"{ask}. Otherwise: {text}" if ask else text), NEEDS_ACTION, missing
		more = " more" if moved > 0 else ""
		there = f" ({_money(moved)} is there already)" if moved > 0 else ""
		if ask:
			return (
				f"The {account} debit is {_money(moved)} but {lines} are {_money(target)}. {ask}; otherwise "
				f"move {_money(missing)}{more} of the goods debit to {account}. Then save it; the S-D loop "
				"submits it",
				NEEDS_ACTION,
				missing,
			)
		return (
			f"Move {_money(missing)}{more} of the goods debit to {account}{together}{there}, {SAVE_IT}",
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


def _issue_sentences(issues):
	"""What is wrong with a Reference Number, one sentence per problem of :func:`resolve_references`."""
	said = []
	for kind, detail in issues or ():
		if kind == "dead":
			which = "is no recorded store run" if len(detail) == 1 else "are no recorded store runs"
			said.append(f"Its Reference Number lists {_and(detail)}, which {which}.")
		elif kind == "both":
			said.append(
				f"Its Reference Number points to both {_and(detail)}, so the report cannot tell which charge "
				"it adjusts."
			)
		elif kind == "nsr_and":
			said.append(f"Its Reference Number says {NOT_STORE_RUN} and also lists {_and(detail)}.")
		elif kind == "names_nsr":
			said.append(
				f"Its Reference Number names {_and(detail)}, marked {NOT_STORE_RUN}: add {NOT_STORE_RUN} to "
				"this one's too if it has nothing to do with store runs, or name the charge it adjusts."
			)
		elif kind == "charge_names":
			said.append(
				f"It is a card charge itself, and its Reference Number names {_and(detail)}: a charge's "
				"Reference Number lists only the run ids of the trips it pays for."
			)
		elif kind == "unresolved":
			said.append(f"Its Reference Number names {_and(detail)}, which does not lead to one card charge.")
		elif kind == "draft":
			said.append(
				f"It is a draft correcting entry for {detail}: submit it (a draft correcting entry does not "
				f"count until submitted), or delete it if {detail}'s row does not need it."
			)
	return said


def _fix_reference(entry):
	"""How a wrong Reference Number is corrected: on a draft, or -- it cannot be changed once
	submitted -- by cancelling and amending (never a QuickBooks entry)."""
	if _is_draft(entry):
		return "Correct its Reference Number and save it."
	if entry.get("source") == "QuickBooks":
		return (
			"It is a submitted QuickBooks entry, which is never cancelled, so its Reference Number stays as "
			"it is and the report ignores what it lists."
		)
	return "It is submitted, so its Reference Number cannot be changed: cancel it and amend it with the right one."


def _charge_row(bill, amount, account, fixes, issues=()):
	"""The row of a charge that is neither paired nor linked but carries ``amount`` (net) on 2210."""
	row = bill["row"]
	draft = _is_draft(row)
	voucher_no = row.get("voucher_no") or ""
	invoice = row.get("voucher_type") == "Purchase Invoice"
	unlinked = bill.get("unlinked")
	show = NEEDS_ACTION
	if amount > 0 and unlinked:
		key = link_key(unlinked["trip"])
		taken = f"It was paired with {key} until {_linked_by(unlinked['groups'], unlinked['trip'])}"
		if invoice:
			what = (
				f"{taken}: keep one charge for {key}. A Purchase Invoice books its stock lines to {account} "
				f"itself, so do not move them: if this invoice is {key}'s charge, take {key} out of the "
				"Reference Number that links the other one; if not, it waits for its own receipt"
			)
		else:
			how = (
				f", {SAVE_IT}"
				if draft
				else "; "
				+ _CORRECTING_ENTRY.format(
					amount=_money(amount),
					lines=f"Dr the expense account it used / Cr {account}",
					charge=voucher_no,
				)
			)
			what = (
				f"{taken}: keep one charge for {key} and move this one's {_money(amount)} on {account} back to "
				f"the expense{how}"
			)
	elif amount > 0 and invoice:
		# v16 books a stock line of an invoice made without a receipt to 2210 itself, where it waits
		# for the receipt: moving it off would undo a correct booking (v1.538.0 third review).
		what = (
			f"Waiting for its receipt: a Purchase Invoice books its stock lines ({_money(amount)}) to {account} "
			"itself and its Purchase Receipt clears them, so leave its lines as they are. If its goods were "
			"recorded as a store run instead, that trip's row needs no other charge, and that trip must not "
			"be billed from its receipts"
		)
		show = WAITING
	elif amount > 0:
		shown = f"Carries {_money(amount)} on {account}{_corrected(fixes)} but no recorded store run is paired or linked"
		if draft:
			what = (
				f"{shown}. If you moved it for a trip whose row shows no other charge, put that trip's run id in "
				"this entry's Reference Number; otherwise move it back to the expense. Then save it; the S-D "
				"loop submits it"
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
	said = _issue_sentences(issues)
	if said:
		what = " ".join([*said, what])
		show = NEEDS_ACTION
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
		"action": _once(what, account),
		"show": show,
		"row_type": ROW_CHARGE,
	}


def _entry_row(entry, amount, issues, outside, account):
	"""The row of a Journal Entry whose Reference Number says something unusable, or whose net 2210
	``amount`` no charge accounts for (:func:`attribute_2210`)."""
	name = entry.get("name") or ""
	said = _issue_sentences(issues)
	fixable = [kind for kind, _detail in issues or () if kind != "draft"]
	show = NEEDS_ACTION
	if outside is not None:
		charge = outside["charge"]["row"].get("voucher_no") or ""
		day = outside["charge"]["day"]
		subject, whose = (
			("It is a charge", "its")
			if charge == name
			else (f"It corrects {charge}, a charge", "that charge's")
		)
		said.append(
			f"{subject} dated {day}, outside this range, that no recorded store run is paired or linked with: "
			f"widen the range to include {day}, and {whose} row there says what to do."
		)
	if said:
		if fixable:
			said.append(_fix_reference(entry))
			if not amount and not _is_draft(entry) and entry.get("source") == "QuickBooks":
				show = WAITING
		if amount > 0:
			said.append(f"Until then its {_money(amount)} on {account} is not accounted for.")
		elif amount < 0:
			said.append(f"Until then the {_money(-amount)} it takes off {account} is not accounted for.")
		what = " ".join(said)
	else:
		carries = f"Carries {_money(amount)} on" if amount > 0 else f"Takes {_money(-amount)} off"
		if _is_draft(entry):
			how = "Edit its Reference Number and save it."
		elif entry.get("source") == "QuickBooks":
			how = (
				"It is a submitted QuickBooks entry, which is never cancelled: post and submit a correcting "
				f"Journal Entry that takes its {_money(amount)} back off {account}, with Reference Number {name}."
			)
		else:
			how = (
				"It is submitted, so its Reference Number cannot be changed: cancel it and amend it with the "
				"right Reference Number."
			)
		what = (
			f"{carries} {account} that the report cannot tie to any store run or card charge. If it adjusts a "
			"charge, its Reference Number should name that charge (or the trip's run id); if it has nothing to "
			f"do with store runs, add {NOT_STORE_RUN} to its Reference Number. {how}"
		)
	return {
		"trip_day": None,
		"store": None,
		"run_ref": "",
		"receipts": 0,
		"first_receipt": None,
		"recorded_by": "",
		"lines_before_tax": None,
		"stock_amount": 0.0,
		"receipt_total": None,
		"charge_type": "Journal Entry",
		"charge": name or None,
		"charge_date": metrics._as_day(entry.get("day")),
		"charge_amount": _cents(entry.get("amount")),
		"charge_source": _source(dict(entry, voucher_type="Journal Entry")),
		"match_basis": REFERENCE_TO_CHECK if issues else NOT_TIED,
		"charge_status": DRAFT if _is_draft(entry) else SUBMITTED,
		"moved_to_2210": 0,
		"on_2210": _cents(amount),
		"to_move": 0.0,
		"action": _once(what, account),
		"show": show,
		"row_type": ROW_ENTRY,
	}


def build_rows(
	trips,
	on_2210=None,
	billed=None,
	accounts=None,
	name_of=None,
	corrections=None,
	unpaired=None,
	window=None,
	resolution=None,
	journal=None,
):
	"""The report's rows, sorted Needs action, Waiting, Done: one per trip from :func:`match_window`,
	one per unpaired charge that carries 2210, one per linked charge in range whose trips are all
	outside it and that needs action, and one per Journal Entry to check.

	* ``on_2210``: ``{(voucher_type, voucher_no): amount}``, the net debit each charge's own lines
	  (and each Journal Entry of ``journal``) carry on its company's Stock Received But Not Billed
	  account.
	* ``corrections``: ``{voucher_no: {correcting entry: amount}}`` (:func:`correction_amounts`), the
	  net 2210 debit of each submitted Journal Entry whose Reference Number resolves to the charge. It
	  is added to the charge's own.
	* ``unpaired``: the charges of :func:`match_window` with no trips that are shown.
	* ``billed``: ``{receipt name: Purchase Invoice}``, receipts a submitted invoice was made from.
	* ``accounts``: ``{company: its Stock Received But Not Billed account}``.
	* ``name_of``: turns a user id into the name shown under Recorded By.
	* ``window``, ``resolution``, ``journal``: :func:`match_window`, :func:`resolve_references` and
	  the backstop's entries, for the rows beyond the trips and unpaired charges. Without them only
	  those are built.

	**The amount to move** is the stock lines before tax of the trips a charge pays for: what their
	receipts credited to 2210 (``stock_amount``, from the GL) -- one trip when paired, every trip
	its Reference Number links when linked (the rows of one charge share one *What to Do*).
	**Moved to 2210** means the charge's net 2210 debit, its submitted correcting entries included,
	equals it to the cent. **What to do**:

	* a charge's draft short of it: move the difference, then save it (**Needs action**); a linked
	  draft whose link took another trip's automatic pair first asks whether it pays for that trip
	  too;
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
	  Reference Number; only if it has none, bill it from its receipts after the cutover;
	* a charge neither paired nor linked that carries a net 2210 debit: link it to its trip, or move
	  it back to the expense (**Needs action**); a Purchase Invoice waits for its receipt
	  (**Waiting**);
	* a Journal Entry whose Reference Number lists a dead key or points two ways, a draft correcting
	  entry, or one whose 2210 amount no charge accounts for (**Needs action**).

	``on_2210`` and ``to_move`` on a trip row are its share of its charge's, for the summary: the
	charge's 2210 debit is laid over its trips **shown** in date order, the last taking what is
	left, and what is still to move likewise, so the rows of a charge whose trips span From Date
	add up to what the row says.
	"""
	on_2210 = on_2210 or {}
	corrections = corrections or {}
	billed = billed or {}
	accounts = accounts or {}
	unpaired = list(unpaired or ())
	trips = list(trips or ())
	resolution = resolution or {}
	problems = resolution.get("problems") or {}
	shown_ids = {id(trip) for trip in trips}

	def own_of(bill):
		return _cents(metrics._amount(on_2210.get(_charge_id(bill["row"]))))

	def fixes_of(bill):
		return corrections.get(bill["row"].get("voucher_no") or "") or {}

	def carried(bill):
		return _cents(own_of(bill) + sum(metrics._amount(amount) for amount in fixes_of(bill).values()))

	def account_of_company(company):
		return accounts.get(company) or DEFAULT_2210

	def account_of(trip):
		receipts = _sorted_receipts(trip)
		return account_of_company((receipts[0] if receipts else {}).get("company"))

	decided = {}

	def decide(group):
		if id(group) in decided:
			return decided[id(group)]
		charge = group["charge"]
		fixes = fixes_of(charge)
		corrected = _cents(sum(metrics._amount(amount) for amount in fixes.values()))
		own = own_of(charge)
		target = _cents(sum(_stock(trip) for trip in group["trips"]))
		receipts = [row for trip in group["trips"] for row in _sorted_receipts(trip)]
		invoices = _distinct(billed.get(row.get("receipt")) for row in receipts)
		account = account_of(group["trips"][0])
		action, show, missing = _decide(
			charge,
			group,
			target,
			own,
			corrected,
			sorted(fixes),
			account,
			invoices,
			group.get("linked", False),
		)
		moved = _cents(own + corrected)
		# The charge's figures are laid over the trips shown, so the rows add up to the charge.
		shown = [trip for trip in group["trips"] if id(trip) in shown_ids]
		shares = {}
		remaining = moved
		owed = missing
		for position, trip in enumerate(shown):
			stock = _stock(trip)
			last = position == len(shown) - 1
			share = remaining if last else min(remaining, stock)
			remaining = _cents(remaining - share)
			due = owed if last else (_cents(stock - min(share, stock)) if missing else 0.0)
			owed = _cents(owed - due)
			shares[id(trip)] = (_cents(share), _cents(due) if missing else 0.0)
		decided[id(group)] = (_once(action, account), show, target, moved, shares, missing)
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
			group = trip.get("group") or _group(charge, [trip])
			action, show, target, moved, shares, _missing = decide(group)
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
				action = _once(action, account)
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

	merged = set()
	for bill in unpaired:
		amount = carried(bill)
		if amount == 0:
			continue
		row = bill["row"]
		issues = problems.get(row.get("voucher_no")) if row.get("voucher_type") == "Journal Entry" else None
		if issues:
			merged.add(row.get("voucher_no"))
		rows.append(
			_charge_row(bill, amount, account_of_company(row.get("company")), sorted(fixes_of(bill)), issues)
		)

	for group in (window or {}).get("outside") or ():
		action, show, target, moved, _shares, missing = decide(group)
		if show != NEEDS_ACTION:
			continue
		charge_row = group["charge"]["row"]
		receipts = [row for trip in group["trips"] for row in _sorted_receipts(trip)]
		keys = [link_key(trip) for trip in group["trips"]]
		first = receipts[0] if receipts else {}
		owners = _distinct(str(row.get("recorded_by") or "") for row in receipts)
		rows.append(
			{
				"trip_day": None,
				"store": first.get("supplier"),
				"run_ref": ", ".join(keys),
				"receipts": len(receipts),
				"first_receipt": first.get("receipt"),
				"recorded_by": ", ".join(name_of(owner) if name_of else owner for owner in owners),
				"lines_before_tax": _cents(
					sum(
						metrics._amount(
							row.get("amount") if row.get("net_amount") is None else row.get("net_amount")
						)
						for row in receipts
					)
				),
				"stock_amount": target,
				"receipt_total": None,
				"charge_type": charge_row.get("voucher_type") or None,
				"charge": charge_row.get("voucher_no") or None,
				"charge_date": group["charge"]["day"],
				"charge_amount": _cents(group["charge"]["amount"]),
				"charge_source": _source(charge_row),
				"match_basis": BASIS_LABELS[LINKED_BY_REFERENCE],
				"charge_status": DRAFT if _is_draft(charge_row) else SUBMITTED,
				"moved_to_2210": 0,
				"on_2210": moved,
				"to_move": missing,
				"action": (
					f"The store runs its Reference Number links ({', '.join(keys)}) are dated outside this "
					f"range. {action}"
				),
				"show": NEEDS_ACTION,
				"row_type": ROW_OUTSIDE,
			}
		)

	if window and window.get("early") is not None:
		ledger = attribute_2210(window, resolution, on_2210, corrections, journal)
		entries = dict(resolution.get("entries") or {})
		entries.update({entry.get("name"): entry for entry in journal or () if entry.get("name")})
		early, end = window["early"], window["end"]
		names = set(ledger["unaccounted"]) | set(problems)
		for name in sorted(names):
			entry = entries.get(name)
			if entry is None or name in merged or name in ledger["excluded"]:
				continue
			day = metrics._as_day(entry.get("day"))
			if day is None or not early <= day <= end:
				continue
			amount = ledger["unaccounted"].get(name, 0.0)
			issues = problems.get(name)
			if not amount and not issues:
				continue
			rows.append(
				_entry_row(
					entry,
					amount,
					issues,
					ledger["outside"].get(name),
					account_of_company(entry.get("company")),
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
	have to move to 2210 (a linked charge whose trips are outside the range included); ``moved``
	what they already carry there, up to each trip's stock lines; ``unmatched_on_2210`` the net
	2210 debit of the charges under Needs action that match no recorded store run (a Purchase
	Invoice waiting for its receipt is not one); ``not_accounted`` the net 2210 amount of the
	Journal Entries no charge accounts for (:func:`attribute_2210`), which must be $0.00 before the
	S-D loop.
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
		"unmatched_on_2210": _cents(
			sum(
				row["on_2210"]
				for row in rows
				if row["row_type"] == ROW_CHARGE and row["show"] == NEEDS_ACTION
			)
		),
		"not_accounted": _cents(sum(row["on_2210"] for row in rows if row["row_type"] == ROW_ENTRY)),
	}

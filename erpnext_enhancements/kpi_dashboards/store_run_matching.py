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
  charge goes back to unpaired (its row names the link that took it, and asks whether it is that
  trip's charge after all -- under Waiting when it carries nothing on 2210), and a linked charge's
  automatic trip goes back to waiting (its row asks all three ways: the charge pays for both, for
  that trip and not the linked ones, or for the linked ones alone, :func:`build_rows`). A linked
  charge's **target on 2210 is the sum of its linked trips' stock lines**, so one draft for two
  runs reaches Done once it carries both. **Links resolve by key, not by date**: a linked trip
  outside From..To is not shown, but its charge is never listed as having no store run, and a
  linked charge in range whose trips are all outside it gets a row of its own when it needs action.
  A submitted entry's Reference Number cannot be changed (v16, not ``allow_on_submit``), so a
  charge already submitted is linked by the Reference Number of a correcting entry instead (next
  item).
* **A charge never carries more than it paid** (:func:`_fits`). A charge whose trips' stock lines
  add up to more than its own amount (the pairing's tolerance allowed) cannot pay for all of them:
  its rows are Needs action, name the trips and what carries each run id, and ask Accounting to take
  out the one that is not the charge's, or to say the charge paid for it only in part. No amount is
  guessed: the report never says which. The same bound keeps the questions honest -- a charge that
  cannot pay for a displaced trip as well as its own is not asked whether it does.
* **part-paid.** The token ``part-paid`` right after a trip key, in the Reference Number of a charge
  or of a submitted correcting entry of one, says the charge paid for that trip only in part (store
  credit, a second card): the trip leaves the capacity check, its 2210 target is still its whole
  stock lines, and the row reads Done once 2210 carries them. An automatic pair over the charge's
  amount (always a receipt-total pair: the lines-plus-tax pass never exceeds it) is a checkout
  discount or such a part payment, and its row says both.
* **Correcting entries count, through chains.** A charge already submitted with the goods on the
  expense is fixed with ONE correcting Journal Entry, Dr 2210 / Cr the expense account the charge
  used, whose Reference Number names the charge. A Reference Number that names a correcting entry
  names that entry's charge (followed to the end, loops refused), so a reversal written against
  the first correction still counts for the charge. The 2210 debit of every **submitted**
  correcting entry is added to the charge's own, so the row reaches Done once corrected and an
  over-correction is flagged; a draft one counts for nothing and is listed to be submitted. Trip
  keys listed beside the charge's name link that charge to those trips, and every correcting entry
  the report advises for a charge with trips names them all, so a later one for another run of the
  same purchase adds to the link instead of displacing it. A submitted QuickBooks entry whose own
  Reference Number is unusable still names itself, so the correcting entry it is told to post
  counts for it. **Amending the charge is
  never advised**: amending a QuickBooks-synced Journal Entry cancels the original, and
  ``tabQuickBooks Sync Mapping`` -- which the KPI's reader follows to find QuickBooks charges --
  stays on the cancelled one, so the charge would drop out of the pairing.
* **A charge carrying 2210 is never lost.** A charge dated in range that is neither paired nor
  linked but carries a net 2210 debit is listed under **Needs action**: link it to its trip, or
  move it back to the expense (a Purchase Invoice, which books its stock lines to 2210 itself, is
  left to wait for its receipt, Waiting). A Journal Entry a link took from its trip is listed even
  when it carries nothing (Waiting): the link may be the mistake, and its row is the only place
  that asks.
* **Every 2210 line is accounted for** (:func:`attribute_2210`). Every Journal Entry line on a
  company's Stock Received But Not Billed account dated from the lookback to the To Date must
  belong to a charge the report accounts for -- one with trips, shown or not, or one with none
  dated in range. Any entry whose net 2210 amount nothing accounts for gets a row of its own, and
  the summary's *2210 Not Accounted For* totals them: it must be $0.00 before the S-D loop.
* **Reference Numbers that say nothing usable are listed**: a key that names no recorded store
  run, tokens pointing to two different charges, a card charge naming another charge, a draft
  correcting entry (whatever its own date, when its charge is accounted for in range: the S-D loop
  submits it all the same). Never first-token-wins, never silently dropped.
* **not-store-run.** The token ``not-store-run`` in a Journal Entry's Reference Number takes it out
  of all this: it is not a correcting entry, not a link, and a card charge carrying it pairs with
  no trip (in the report only; the KPI still counts it). It leaves the backstop too, with one
  exception: a **QuickBooks entry** marked so whose 2210 (with that of the entries marked
  ``not-store-run`` that name it) is not zero stays listed, because nothing says what that amount
  clears -- **unless its Reference Number also names the Purchase Receipt it clears**, one that is
  not a store run (made from a Purchase Order, a return, or from a supplier not ticked Store-Run
  Vendor: the reader's own definition, negated). Such an entry is left out whatever it carries; the
  report looks the receipt up and lists the entry, saying why, when it is no Purchase Receipt, not
  submitted, or a store run. An ERPNext entry marked ``not-store-run`` is left out whatever it
  carries, as before. A submitted QuickBooks entry marked ``not-store-run`` by mistake can still be
  linked: its own Reference Number cannot change, so a submitted correcting entry that names it
  beside run ids links it, and from then on every entry naming it counts as its correction.

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

#: The token that, right after a trip key, says the charge paid for that trip only in part: the trip
#: leaves the capacity check (:func:`_fits`), its 2210 target unchanged.
PART_PAID = "part-paid"

#: What a 2210 amount on a card charge can clear besides a store run (:func:`resolve_references`).
_OTHER_RECEIPT = "a Purchase Receipt that is not a store run (one made from a Purchase Order, or a return)"

#: What a trip key looks like, lower-cased: a run id (``stock_scan_rules.RUN_REF_PREFIX``, restated
#: so this module imports no frappe-bound code; a test compares the two) or a Purchase Receipt's
#: name (production's series). A token of this shape that names no recorded store run is listed.
RUN_PREFIX = "sr-"
RECEIPT_PREFIX = "mat-pre-"

#: How a draft is finished. Accounting saves each adjusted draft; the S-D loop submits every 2026
#: draft together (``quickbooks_online/MIGRATION_NOTES.md`` section 8).
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


def _and(items, word="and"):
	items = [str(item) for item in items]
	if len(items) < 2:
		return "".join(items)
	return f"{', '.join(items[:-1])} {word} {items[-1]}"


def _or(items):
	return _and(items, "or")


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


def part_paid_keys(text):
	"""The tokens, lower-cased, typed right before each ``part-paid`` of a Reference Number (None for
	one typed first): ``"ACC-JV-1 sr-a part-paid sr-b"`` -> ``["sr-a"]``. Read in the order typed,
	repeats kept, so ``part-paid`` can follow two run ids of one Reference Number."""
	words = [word.lower() for word in _SEPARATORS.split(str(text or "")) if word.strip()]
	return [words[at - 1] if at else None for at, word in enumerate(words) if word == PART_PAID]


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


def unresolved_tokens(references, charges, receipts, other_receipts=()):
	"""The keys the ``references`` list that name neither a trip in ``receipts`` nor a charge in
	``charges`` nor one of the ``references`` themselves nor a Purchase Receipt of
	``other_receipts``, sorted.

	The report looks each one up three ways -- as a run id or receipt name of a receipt the KPI's
	reader did not read (a trip dated before its lookback, which a link still reaches), as the name of
	a Journal Entry, and as the name of any Purchase Receipt (one that is not a store run, which
	``not-store-run`` may name) -- and repeats with the tokens of what it found, so a chain of
	correcting entries is followed to its charge. ``not-store-run`` and ``part-paid`` are never looked
	up.
	"""
	known = {key for row in receipts or () for key in _receipt_keys(row)}
	known |= {_lower(row.get("voucher_no")) for row in charges or ()}
	known |= {_lower(entry.get("name")) for entry in references or ()}
	known |= {_lower(row.get("name")) for row in other_receipts or ()}
	known.update((NOT_STORE_RUN, PART_PAID))
	found = set()
	for entry in references or ():
		found.update(token for token in reference_tokens(entry.get("reference")) if token not in known)
	return sorted(found)


def resolve_references(references, charges, receipts, far_receipts=(), named=(), other_receipts=()):
	"""What every Journal Entry's Reference Number says.

	* ``references``: ``{name, reference, docstatus, day, company, amount, source}`` for every
	  Journal Entry (draft or submitted) with a Reference Number, as the report reads them.
	* ``charges``, ``receipts``: the KPI reader's rows; ``far_receipts``: the receipts of trips it
	  did not read, looked up by key; ``named``: Journal Entries looked up by name (the
	  ``references`` shape), for a token that names an entry read nowhere else; ``other_receipts``:
	  Purchase Receipts looked up by name, ``{name, docstatus, store_run}`` (``store_run``: the
	  reader's own definition of one), for a token that names a receipt no trip has.

	Each token of a Reference Number is ``not-store-run``, ``part-paid``, a trip key (a run id or
	receipt name of a recorded store run), the name of a known entry or charge, the name of another
	Purchase Receipt, a dead key (shaped like a trip key but naming neither), or anything else
	(ignored: a card number, a bank reference). **Naming an entry names what it resolves to**: one of
	the KPI's charges is itself; an entry whose Reference Number names exactly one charge (directly or
	through other entries) resolves to that charge; an entry naming none resolves to itself. A loop,
	or tokens that lead to two different charges, resolve to nothing. So a reversal whose Reference
	Number names the first correcting entry is a correction of the charge, not of the correcting
	entry.

	**A Purchase Receipt that is not a store run** is named beside ``not-store-run`` by an entry whose
	2210 amount clears that receipt (a card charge that paid for a PO receipt, say): the entry is left
	out of the backstop whatever it carries (``clears``). Beside ``not-store-run``, a receipt that is not
	submitted, a store run, or no receipt at all is refused, and the entry listed with the reason;
	named without ``not-store-run``, such a receipt links nothing and is listed.

	**part-paid** right after a trip key says the charge paid for that trip only in part: the link it
	belongs to records the key (``part_paid``), which :func:`_fits` leaves out of the capacity check.
	One with no trip key right before it is listed.

	**A submitted QuickBooks entry marked not-store-run** can never have the mark taken out, so a
	submitted entry that names it beside trip keys links it anyway: it is *overridden* -- still out of
	the automatic pairing, since its own Reference Number says so, but otherwise a charge like any
	other, and every entry naming it is its correction, those marked ``not-store-run`` included (v1.538.0
	fifth review).

	Returns ``{"links", "corrections", "drafts", "excluded", "marks", "clears", "overridden",
	"problems", "roots", "entries"}``:

	* ``links``: ``{(voucher_type, voucher_no): {"row", "keys", "carriers", "part_paid"}}`` -- the
	  charge row (the reader's own for a charge it read, so its store and source stay the KPI's), the
	  trip keys that link it, for each key the entries whose Reference Number carries it (``{name,
	  docstatus, source}``), and the keys a ``part-paid`` follows. An entry that is one of the KPI's
	  charges, or whose Reference Number lists trip keys and names no charge, links itself; a submitted
	  correcting entry links its charge to the keys beside the charge's name.
	* ``corrections``: ``{charge voucher: [submitted correcting entries]}``;
	  ``drafts``: ``{charge voucher: [draft correcting entries]}`` (they count for nothing).
	* ``excluded``: the entries whose Reference Number says ``not-store-run`` and lists nothing that
	  contradicts it (entries that are themselves ``not-store-run`` may be named beside it, and so may
	  a Purchase Receipt that is not a store run): they take no part in the pairing.
	  ``marks``: ``{entry marked not-store-run: [excluded entries naming it, and nothing else]}`` --
	  the correcting entries of a marked QuickBooks entry, whose 2210 counts with its own
	  (:func:`attribute_2210`). ``clears``: ``{excluded entry: [the receipts it names]}``, left out of
	  the backstop whatever they carry. ``overridden``: the excluded entries linked anyway, which the
	  backstop accounts for like any charge.
	* ``problems``: ``{entry: [(kind, detail)]}`` -- ``dead`` keys, ``both`` (two charges),
	  ``nsr_and`` (not-store-run beside a key, a charge, part-paid, or a receipt it cannot take, each
	  with its reason), ``names_nsr``, ``charge_names`` (one of the KPI's charges naming another
	  charge), ``unresolved`` (a loop or a conflict further on), ``draft`` (a draft correcting entry),
	  ``receipt`` (a Purchase Receipt that is not a store run, named without not-store-run),
	  ``part_paid`` (part-paid with no trip key right before it). :func:`build_rows` lists them.
	* ``roots``: the charge rows correcting entries resolve to (a charge nothing else pairs with
	  still gets a row, :func:`match_window`); ``entries``: every entry read, by name.
	"""
	keys = set()
	for row in list(receipts or ()) + list(far_receipts or ()):
		keys.update(_receipt_keys(row))
	others = {}
	for row in other_receipts or ():
		name = _lower(row.get("name"))
		if name and name not in keys:
			others.setdefault(name, row)
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

	def fixed(name):
		"""A submitted QuickBooks entry: never cancelled, its Reference Number never changes."""
		entry = entries.get(name) or {}
		return entry.get("docstatus") in (1, "1") and entry.get("source") == "QuickBooks"

	overridden = set()
	memo = {}

	def read(name, stack):
		found = {
			"nsr": False,
			"trips": [],
			"typed": [],
			"dead": [],
			"roots": [],
			"nsr_named": [],
			"nsr_roots": [],
			"unresolved": [],
			"receipts": [],
			"refused": [],
			"paid": [],
			"loose": False,
		}
		text = (entries.get(name) or {}).get("reference")
		for token, typed in _typed_tokens(text):
			if token == NOT_STORE_RUN:
				found["nsr"] = True
			elif token == PART_PAID:
				continue
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
					found["nsr_roots"].append(where)
				else:
					found["unresolved"].append(typed)
			elif token in others:
				row = others[token]
				if row.get("store_run") in (1, "1", True):
					found["refused"].append((typed, "a recorded store run"))
				elif row.get("docstatus") in (1, "1"):
					found["receipts"].append(typed)
				else:
					found["refused"].append((typed, "a Purchase Receipt that is not submitted"))
			elif is_key_shaped(token):
				found["dead"].append(typed)
		paid = part_paid_keys(text)
		for before in paid:
			if before in keys:
				if before not in found["paid"]:
					found["paid"].append(before)
			elif before is None or not is_key_shaped(before):
				# After a dead key the dead key's own sentence says what is wrong.
				found["loose"] = True
		found["clean_nsr"] = found["nsr"] and not (
			found["trips"]
			or found["roots"]
			or found["dead"]
			or found["unresolved"]
			or found["refused"]
			or paid
		)
		# An entry marked not-store-run that names an overridden entry corrects it (it was written to
		# move that entry's 2210 back while the entry was still marked).
		found["remark"] = bool(
			found["nsr"]
			and len(found["roots"]) == 1
			and found["roots"][0] in overridden
			and not (
				found["trips"]
				or found["dead"]
				or found["unresolved"]
				or found["refused"]
				or found["receipts"]
				or found["nsr_named"]
				or paid
			)
		)
		return found

	def bad(found):
		return bool(
			found["unresolved"]
			or (found["nsr"] and not found["clean_nsr"] and not found["remark"])
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
		if name in overridden:
			result = ("charge", name)
		elif found["clean_nsr"]:
			result = ("nsr", name)
		elif name in reader:
			result = ("charge", name)
		elif bad(found):
			# A submitted QuickBooks entry naming what cannot be resolved still names itself: its
			# Reference Number can never be corrected, so a correcting entry naming it is the only
			# way its 2210 comes back (v1.538.0 fourth review).
			result = ("charge", name) if fixed(name) else ("bad", name)
		elif found["roots"]:
			result = ("charge", found["roots"][0])
		else:
			result = ("charge", name)
		memo[name] = result
		return result

	# A submitted QuickBooks entry marked not-store-run that a submitted entry names beside trip keys
	# is linked anyway (fifth review, item 5): read everything once to find them, then again.
	linked_anyway = set()
	for name in sorted(entries):
		entry = entries[name]
		if name in reader or entry.get("docstatus") not in (1, "1"):
			continue
		found = read(name, {name})
		marked = _distinct(found["nsr_roots"])
		if (
			found["trips"]
			and not (found["nsr"] or found["roots"] or found["unresolved"])
			and len(marked) == 1
			and marked[0] != name
			and fixed(marked[0])
		):
			linked_anyway.add(marked[0])
	overridden.update(linked_anyway)
	memo.clear()

	links = {}
	corrections = {}
	drafts = {}
	excluded = set()
	marks = {}
	clears = {}
	problems = {}
	roots = {}

	def charge_row(name):
		return reader.get(name) or _entry_charge(entries[name])

	def link(name, tokens, carrier, paid=()):
		row = charge_row(name)
		found = links.setdefault(_charge_id(row), {"row": row, "keys": [], "carriers": {}, "part_paid": []})
		for token in tokens:
			if token not in found["keys"]:
				found["keys"].append(token)
			carriers = found["carriers"].setdefault(token, [])
			if carrier not in carriers:
				carriers.append(carrier)
		for token in paid:
			if token not in found["part_paid"]:
				found["part_paid"].append(token)

	def contradicts(found, name):
		"""What ``not-store-run`` is typed beside, each with why the report cannot take it."""
		said = [f"{typed} (a recorded store run)" for typed in found["typed"]]
		said += [spelled(root) for root in found["roots"] if root != name]
		said += [
			f"{typed} (no recorded store run)"
			if typed.lower().startswith(RUN_PREFIX)
			else f"{typed} (no Purchase Receipt)"
			for typed in found["dead"]
		]
		said += [f"{typed} ({why})" for typed, why in found["refused"]]
		said += found["unresolved"]
		if part_paid_keys((entries.get(name) or {}).get("reference")):
			said.append(PART_PAID)
		return said

	for name in sorted(entries):
		entry = entries[name]
		own = entry.get("name") or name
		submitted = entry.get("docstatus") in (1, "1")
		carrier = {"name": own, "docstatus": 1 if submitted else 0, "source": entry.get("source")}
		found = read(name, {name})
		issues = []
		conflict = found["nsr"] and not found["clean_nsr"] and not found["remark"]
		if conflict:
			issues.append(("nsr_and", contradicts(found, name)))
		elif found["dead"] or found["refused"]:
			issues.append(("dead", found["dead"] + [typed for typed, _why in found["refused"]]))
		if found["receipts"] and not found["nsr"]:
			issues.append(("receipt", found["receipts"]))
		if found["loose"] and not found["nsr"]:
			issues.append(("part_paid", None))
		if name in reader and name not in overridden:
			strays = [spelled(root) for root in found["roots"] if root != name]
			strays += found["nsr_named"] + found["unresolved"]
			if strays and not found["nsr"]:
				issues.append(("charge_names", strays))
			if found["clean_nsr"]:
				excluded.add(own)
				if found["receipts"]:
					clears[own] = found["receipts"]
			elif found["trips"] and not found["nsr"]:
				link(name, found["trips"], carrier, found["paid"])
		elif name in overridden:
			# Its own Reference Number still says not-store-run: out of the automatic pairing, linked by
			# the entries that name it.
			excluded.add(own)
		elif found["clean_nsr"]:
			excluded.add(own)
			if found["receipts"]:
				clears[own] = found["receipts"]
			named_nsr = _distinct(found["nsr_roots"])
			if len(named_nsr) == 1 and named_nsr[0] != name:
				marks.setdefault(spelled(named_nsr[0]), []).append(own)
		elif conflict:
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
					link(root, found["trips"], carrier, found["paid"])
			else:
				drafts.setdefault(charge, []).append(own)
				issues.append(("draft", charge))
		elif found["trips"]:
			link(name, found["trips"], carrier, found["paid"])
		if issues:
			problems[own] = issues
	return {
		"links": links,
		"corrections": corrections,
		"drafts": drafts,
		"excluded": excluded,
		"marks": marks,
		"clears": clears,
		"overridden": {spelled(name) for name in overridden},
		"problems": problems,
		"roots": [roots[name] for name in sorted(roots)],
		"entries": {entry.get("name") or name: entry for name, entry in entries.items()},
	}


def resolve_links(references, charges, receipts, far_receipts=(), named=(), other_receipts=()):
	"""``(links, corrections)`` of :func:`resolve_references`, for a caller that needs no more."""
	resolved = resolve_references(references, charges, receipts, far_receipts, named, other_receipts)
	return resolved["links"], resolved["corrections"]


def _trip_order(trip):
	return (trip["day"], trip["store"], str(trip["key"]))


def _group(bill, trips=(), linked=False, carriers=None, part_paid=()):
	return {
		"charge": bill,
		"trips": list(trips),
		"displaced": [],
		"contested": [],
		"carriers": carriers or {},
		"linked": linked,
		"part_paid": list(part_paid),
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

	Each linked charge gets a *group*: ``{charge, trips, displaced, contested, carriers, linked,
	part_paid}`` (``part_paid``: the trips a ``part-paid`` follows a key of),
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
		acknowledged = [
			trip for trip in targets if any(by_key.get(key) is trip for key in link.get("part_paid") or ())
		]
		group = _group(
			bill,
			sorted(targets, key=_trip_order),
			linked=True,
			carriers=link.get("carriers"),
			part_paid=acknowledged,
		)
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
	linked, part_paid, shown, accounted}``: a linked one, an automatic pair, or one with no trips (a charge that
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
	when its Reference Number says ``not-store-run`` (and it is not ``overridden``), with one
	exception: a **QuickBooks entry** whose 2210, with that of the entries marked ``not-store-run``
	that name it (``marks``), does not net to zero, and whose Reference Number names no Purchase
	Receipt it clears (``clears``). Nothing then says what that amount clears, and marking the entry
	hid it from the backstop (v1.538.0 fourth review), so it is unaccounted, and ``marked`` maps each
	such entry to the QuickBooks entry whose row shows them. An entry that names the receipt it
	clears, one that is not a store run (a card charge that paid for a PO receipt, fifth review), is
	excluded whatever it carries, and its amount counts with no other entry's; so is an ERPNext entry
	marked ``not-store-run``: a 2210 adjustment that has nothing to do with store runs is legitimate.
	An ``overridden`` entry (a marked QuickBooks entry linked anyway) is a charge like any other. Otherwise it is **unaccounted**, and ``outside`` names the group when it has one (a
	charge dated outside the range that pairs with nothing).

	Returns ``{"attributed": {entry: amount}, "unaccounted": {entry: amount}, "excluded": {entry:
	amount}, "outside": {entry: group}, "marked": {entry: QuickBooks entry}}``. The three amounts
	together are every amount considered: each backstop entry, and each entry of an accounted group
	read by name.
	"""
	on_2210 = on_2210 or {}
	excluded = set((resolution or {}).get("excluded") or ())
	excluded -= set((resolution or {}).get("overridden") or ())
	clears = set((resolution or {}).get("clears") or ())
	info = dict((resolution or {}).get("entries") or {})
	info.update({entry.get("name"): entry for entry in journal or () if entry.get("name")})
	unit_of = {}
	for marked, entries in ((resolution or {}).get("marks") or {}).items():
		for entry in entries:
			unit_of.setdefault(entry, marked)

	def unit(name):
		"""The marked QuickBooks entry whose 2210 ``name``'s counts with, or None: never an entry that
		names the receipt it clears, nor one such an entry marks."""
		if name not in excluded or name in clears:
			return None
		owner = name if name not in unit_of else unit_of[name]
		if (
			owner in excluded
			and owner not in clears
			and (info.get(owner) or {}).get("source") == "QuickBooks"
		):
			return owner
		return None

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
	result = {"attributed": {}, "unaccounted": {}, "excluded": {}, "outside": {}, "marked": {}}
	nets = {}
	for name in names:
		owner = unit(name)
		if owner is not None:
			nets[owner] = _cents(nets.get(owner, 0.0) + _cents(on_2210.get(("Journal Entry", name))))
	for name in sorted(names):
		amount = _cents(on_2210.get(("Journal Entry", name)))
		group = owners.get(name)
		if name in excluded and unit(name) is not None and nets.get(unit(name)):
			result["unaccounted"][name] = amount
			result["marked"][name] = unit(name)
		elif name in excluded:
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
	"""How to find and link a waiting trip's charge: on its draft, or -- a submitted entry's
	Reference Number cannot be changed -- with a correcting entry that names the charge and the trip.

	A draft that is already another trip's charge in this list lists both trips' run ids **only when
	it pays for both** (v1.538.0 fourth review): said unconditionally, a draft the pairing had given
	to the wrong trip was linked to both, carried both trips' stock lines, and read Done while the
	other trip's own charge was submitted with its goods on the expense."""
	return (
		f"find its QuickBooks draft, move {_money(stock)} of its goods debit to {account}, put this trip's "
		f"run id ({key}) in its Reference Number and save it; if it is already submitted, post and submit a "
		f"correcting Journal Entry for {_money(stock)} (Dr {account} / Cr the expense account it used) whose "
		f"Reference Number is its name followed by {key}. If that charge is already another trip's in this "
		"list and it also pays for that trip, list both trips' run ids (a draft: in its Reference Number, "
		"its 2210 debit carrying both trips' stock lines; submitted: after its name in that correcting "
		"entry). If it does not pay for that trip, take that trip's run id out of what links it to this "
		f"charge ({_TAKE_OUT}), and that trip's row then asks for its own charge"
	)


def _no_stock_find(key):
	"""How a waiting trip with no stock lines finds its charge: nothing moves, but a draft that is
	already another trip's charge is still linked, with the same clause as :func:`_find_its_draft`
	(v1.538.0 fifth review: "change nothing" left the other trip holding this trip's charge, and the
	list read Done)."""
	return (
		f"find its QuickBooks draft, if it has one: nothing on it needs to move (optionally put {key} in its "
		"Reference Number and save it). If that draft is already another trip's charge in this list, put "
		f"{key} in its Reference Number and save it; if it does not pay for that trip, also take that trip's "
		f"run id out of what links it to this charge ({_TAKE_OUT}); that trip's row then asks. A charge "
		"already submitted: change nothing"
	)


_ONLY_IF_NONE = "Only if it has no card charge at all, bill it from the receipts after the cutover."

#: How a run id comes out of the Reference Number that carries it.
_TAKE_OUT = "edit a draft and save it, cancel a submitted correcting entry"


def _out_of(group, trips):
	"""Where the run ids of ``trips`` come out of to unlink them from ``group``'s charge: "ACC-JV-7's
	Reference Number" when the draft's own carries every one, else each run id and what carries it."""
	charge = group["charge"]["row"].get("voucher_no") or ""
	carriers = [carrier for trip in trips for carrier in _carriers(group, trip)]
	if all(carrier["name"] == charge and carrier["docstatus"] == 0 for carrier in carriers):
		return f"{charge}'s Reference Number (edit it and save it)"
	where = "; ".join(f"{link_key(trip)} in {_carried_in(group, trip)}" for trip in trips)
	return f"the Reference Number that links it ({where}; {_TAKE_OUT})"


def _fits(group, extra=0.0):
	"""Whether ``group``'s charge can pay for the stock lines of its trips plus ``extra``: a card
	charge never moves more to 2210 than it paid (v1.538.0 fourth review). The pairing's own
	tolerance is allowed, so no automatic pair on the lines plus tax ever fails it. A trip a
	``part-paid`` acknowledges (the charge paid for it only in part) is left out (fifth review)."""
	paid = group.get("part_paid") or ()
	target = sum(_stock(trip) for trip in group["trips"] if all(trip is not other for other in paid)) + extra
	return _cents(target) <= _cents(group["charge"]["amount"]) + metrics.STORE_RUN_AMOUNT_TOLERANCE


def _waiting(stock, account, key, displaced=None):
	"""``(what to do, bucket)`` for a trip with no charge and no receipt billed.

	Either/or, never both: a trip that has a card charge is fixed on that charge and is never
	billed from its receipts (that would credit the card twice). ``displaced`` is the group of the
	charge the trip was paired with until that charge's Reference Number linked other trips.

	**A displaced trip is asked all three ways** (v1.538.0 fourth review): the charge pays for this
	trip as well as the ones it links, for this trip and not for them (the link is the mistake), or
	for them alone. Asked only "does it pay for this trip too?", a link typed on the wrong trip
	gained this one beside it and read Done. When the charge cannot pay for both (:func:`_fits`),
	the first answer is not offered. Asked even with no stock lines: the link may still be wrong."""
	if displaced is None:
		if stock <= 0:
			return (
				f"No card charge paired and no stock lines: {_no_stock_find(key)}. {_ONLY_IF_NONE}",
				WAITING,
			)
		return f"No card charge paired: {_find_its_draft(stock, account, key)}. {_ONLY_IF_NONE}", WAITING
	row = displaced["charge"]["row"]
	charge = row.get("voucher_no") or ""
	draft = _is_draft(row)
	correct = (
		f"post and submit a correcting Journal Entry for {_money(stock)} (Dr {account} / Cr the expense "
		f"account it used) whose Reference Number is {charge} followed by {key}"
	)
	linked = [link_key(trip) for trip in displaced["trips"]]
	keys, either = _and(linked), _or(linked)
	head = f"Its paired charge {charge} now links only {', '.join(linked)}, by its Reference Number"
	if stock <= 0:
		add = f"add {key} to that Reference Number (this trip has no stock lines to move)" if draft else ""
		link = f"put {key} in its Reference Number and save it" if draft else "the pairing gives it back"
		elsewhere = f"{_no_stock_find(key)}. {_ONLY_IF_NONE}"
	else:
		add = f"add {key} to that Reference Number and move this trip's {_money(stock)} of stock lines too"
		add = add if draft else correct
		link = f"put {key} in its Reference Number, save it, and this trip's row then says what to move"
		link = link if draft else correct
		elsewhere = f"{_find_its_draft(stock, account, key)}. {_ONLY_IF_NONE}"
	swap = f"take {keys} out of {_out_of(displaced, displaced['trips'])}, then {link}"
	if not _fits(displaced, stock):
		theirs = f"{either}'s" if len(linked) == 1 else f"the charge of {either}"
		return (
			f"{head}, and it cannot pay for this trip too: their stock lines and this trip's "
			f"({_money(sum(_stock(trip) for trip in displaced['trips']) + stock)}) are more than {charge} itself "
			f"({_money(displaced['charge']['amount'])}). If {charge} is this trip's charge and not {theirs}, "
			f"{swap}. If not, {elsewhere}",
			WAITING,
		)
	return (
		f"{head}. If {charge} pays for this trip as well as {keys}, {add or 'nothing needs to change'}. If it "
		f"pays for this trip and not for {either}, {swap}. If it does not pay for this trip, {elsewhere}",
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
	"If this draft also pays for sr-a, add sr-a to its Reference Number …; if it pays for sr-a and not
	for sr-b, …". The "otherwise" the caller appends is the third answer: the trips it links alone.

	Both of the first two are asked (v1.538.0 fourth review): asked only whether it *also* pays, a
	link typed on the wrong trip kept it and gained the right one. An answer the charge cannot pay
	for (:func:`_fits`) is not offered. Returns "" when neither is."""
	before = [link_key(trip) for trip in group["displaced"]]
	linked = [link_key(trip) for trip in group["trips"]]
	names = _and(before)
	extra = sum(_stock(trip) for trip in group["displaced"])

	def after(amount, which):
		amount = _cents(amount)
		lines = f"the stock lines of {', '.join(which)}" + (" together" if len(which) > 1 else "")
		if moved == amount:
			return f" (it then carries exactly {lines})"
		if moved < amount:
			more = " more" if moved > 0 else ""
			return f" and move {_money(amount - moved)}{more} of the goods debit to {account} ({lines})"
		return f" and reduce its {account} debit to {_money(amount)} ({lines})"

	said = []
	if _fits(group, extra):
		said.append(
			f"If this draft also pays for {names}, add {names} to its Reference Number"
			f"{after(target + extra, linked + before)}"
		)
	charge = group["charge"]["row"].get("voucher_no")
	own = all(
		carrier["name"] == charge
		for trip in group["trips"]
		for carrier in _carriers(group, trip) or [{"name": charge}]
	)
	if _fits(dict(group, trips=group["displaced"])):
		if own:
			swap = f"put {names} in its Reference Number in place of {_and(linked)}"
		else:
			swap = f"take {_and(linked)} out of {_out_of(group, group['trips'])} and put {names} in its Reference Number"
		subject = "if it" if said else "If this draft"
		said.append(f"{subject} pays for {names} and not for {_or(linked)}, {swap}{after(extra, before)}")
	return "; ".join(said)


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


def _billed_text(row, group, invoices, linked, account):
	"""A charge paired or linked with a trip that is billed from its receipts: the invoice, already
	submitted, books the purchase, so the charge must not book it too.

	Each branch ends in a state the report can see (v1.538.0 fourth review: "its debit belongs on the
	store's payable" left the row in Needs action for good). A draft that is the invoice's payment is
	marked ``not-store-run`` once nothing of it is on the expense or on 2210, which frees the trip; a
	submitted charge's Reference Number cannot change, so the invoice is cancelled and the charge
	books the purchase, as its row then says."""
	bills = _and(invoices)
	how = "linked to this charge by its Reference Number" if linked else "matched to this charge"
	head = (
		f"Billed from the receipts ({', '.join(invoices)}) and {how} too: {bills} already books this "
		"purchase, so this charge must not book it again."
	)
	draft = _is_draft(row)
	if linked:
		where = _and(_distinct(_carried_in(group, trip) for trip in group["trips"]))
		other = (
			"If the charge is not this trip's, take this trip's run id out of the Reference Number that links "
			f"it, {where}: {_TAKE_OUT}."
		)
	elif draft:
		other = (
			"If the charge is not this trip's, put its own trip's run id in its Reference Number and save it; "
			f"if it pays for no store run, put {NOT_STORE_RUN} in its Reference Number and save it (a debit it "
			f"still has on {account} then gets a row of its own)."
		)
	else:
		other = (
			"If the charge is not this trip's, its own trip's row says how to link it (a submitted "
			"entry's Reference Number cannot be changed)."
		)
	if draft:
		tail = (
			f"If it is, it is the payment of {bills}, not a second purchase: move its whole debit to the "
			f"store's payable (none of it on the expense or on {account}), make its Reference Number "
			f"{NOT_STORE_RUN} alone and save it"
		)
	else:
		tail = (
			f"If it is, it books the purchase and a submitted charge's Reference Number cannot be changed: "
			f"cancel {bills} (and any payment made against it), and this row then says what the charge needs"
		)
	return f"{head} {other} {tail}"


def _billed_displaced(invoices, displaced):
	"""A trip billed from its receipts whose paired charge a link took for other trips. Billed, it
	reads Done; but if that charge is this trip's after all, the purchase is booked twice and nothing
	else would say so (v1.538.0 fifth review: a link typed on the wrong trip took a billed trip's own
	charge, and the list read clean). The charge's own row then says what a billed trip's charge needs."""
	row = displaced["charge"]["row"]
	charge = row.get("voucher_no") or ""
	linked = [link_key(trip) for trip in displaced["trips"]]
	return (
		f"{BILLED_FROM_RECEIPTS} ({', '.join(invoices)}). Its paired charge {charge} now links only "
		f"{', '.join(linked)}, by its Reference Number. If {charge} is this trip's charge, {_and(invoices)} and "
		f"{charge} book this purchase twice: take {_and(linked)} out of {_out_of(displaced, displaced['trips'])}, "
		f"and this row then says what {charge} needs. If it is not, nothing needs to change"
	)


def _acknowledge(group, trips, target, moved, account):
	"""Where ``part-paid`` goes to say ``group``'s charge paid for ``trips`` only in part: right after
	each one's run id, in a draft's own Reference Number, or -- a submitted charge's own cannot
	change -- in the correcting entry that moves what is still missing, or in an editable entry that
	already links it (fifth review). A submitted charge with nothing left to move and no such entry
	cannot record it (Known limits)."""
	row = group["charge"]["row"]
	charge = row.get("voucher_no") or ""
	keys = [link_key(trip) for trip in trips]
	after = f"right after {keys[0]}" if len(keys) == 1 else "right after its run id"
	if _is_draft(row):
		if len(keys) == 1:
			return f"put {keys[0]} {PART_PAID} in {charge}'s Reference Number and save it"
		return f"put {PART_PAID} {after} in {charge}'s Reference Number and save it"
	missing = _cents(target - moved)
	if missing > 0:
		named = [link_key(trip) for trip in group["trips"]]
		if len(keys) == 1 and len(named) == 1:
			reference = f"{charge} followed by {keys[0]} {PART_PAID}"
		else:
			reference = f"{charge} followed by {_and(named)}, with {PART_PAID} {after}"
		return _CORRECTING_ENTRY.format(
			amount=_money(missing),
			lines=f"Dr {account} / Cr the expense account the charge used",
			charge=reference,
		)
	editable = [
		carrier
		for trip in trips
		for carrier in _carriers(group, trip)
		if carrier["docstatus"] == 0 or carrier.get("source") != "QuickBooks"
	]
	if editable:
		where = "; ".join(_distinct(_carried_in(group, trip) for trip in trips))
		return (
			f"put {PART_PAID} {after} in the Reference Number that links it ({where}; edit a draft and save it, "
			f"cancel a submitted correcting entry and submit a copy of it with {PART_PAID} added)"
		)
	return (
		f"a submitted charge's own Reference Number cannot be changed and nothing is left to move, so "
		f"{PART_PAID} cannot be recorded here: settle it by hand"
	)


def _over_capacity(group, target, moved, account, linked):
	"""A charge whose trips' stock lines add up to more than the charge itself (:func:`_fits`). No
	amount is guessed (v1.538.0 fourth review): the row names the trips and what carries each run
	id, and asks Accounting which it is.

	An automatic pair over capacity is always a receipt-total pair (the lines-plus-tax pass never
	exceeds the charge): the charge is the receipt's total, so its stock lines are more than what was
	paid for them -- a checkout discount the receipt's rates do not show, or a part payment by other
	means. Only when it is neither is it not the trip's charge (a receipt total typed wrong that
	equals an unrelated charge): the trip's own charge is then linked by its run id, which overrides
	the pair. That answer is offered last and conditionally: offered alone, "it is not this trip's
	charge" sent a coupon's own draft looking for itself (fifth review). A linked charge is asked the
	same three ways. ``part-paid`` answers the part payment (:func:`_acknowledge`), and the 2210
	target stays the whole stock lines."""
	trips = group["trips"]
	amount = _money(group["charge"]["amount"])
	stays = "its 2210 target stays the whole stock lines"
	if not linked:
		key = link_key(trips[0])
		return (
			f"Its stock lines ({_money(target)}) are more than the charge itself ({amount}), and a charge never "
			f"moves more to {account} than it paid: the stock lines exceed what this charge paid. If it is a "
			"checkout discount, correct the receipt's rates to the prices paid. If it is a part payment by "
			f"other means (store credit, a second card), add {PART_PAID} next to the run id: "
			f"{_acknowledge(group, trips, target, moved, account)}; {stays}. If it is neither, it is not this "
			f"trip's charge: put {key} in the Reference Number of the trip's own charge and save it, which "
			"overrides this pair (if that charge is already submitted, post and submit a correcting Journal "
			f"Entry for {_money(target)} (Dr {account} / Cr the expense account it used) whose Reference Number "
			f"is its name followed by {key}). This row then says what to move"
		)
	each = ", ".join(f"{link_key(trip)} {_money(_stock(trip))}" for trip in trips)
	charge = group["charge"]["row"].get("voucher_no") or ""
	if len(trips) == 1:
		key = link_key(trips[0])
		return (
			f"The stock lines of the trip its Reference Number links ({each}) are more than the charge itself "
			f"({amount}). If the difference is a checkout discount, correct the receipt's rates to the prices "
			f"paid. If {charge} paid for {key} only in part (the rest by store credit or a second card), "
			f"{_acknowledge(group, trips, target, moved, account)}; {stays}, and this row then says what to "
			f"move. If it is not {key}'s charge, take {key} out of {_out_of(group, trips)}, and {key}'s row "
			"then asks for its own charge"
		)
	return (
		f"The stock lines of the trips its Reference Number links ({each}: {_money(target)} together) are "
		f"more than the charge itself ({amount}): one of these trips is not this charge's, it paid for one "
		"only in part (the rest by store credit or a second card), or a receipt's rates miss a checkout "
		f"discount. Take the run id of each trip it does not pay for out of {_out_of(group, trips)}, and each "
		"trip taken out asks for its own charge on its own row. For a trip it paid for only in part, "
		f"{_acknowledge(group, trips, target, moved, account)}; {stays}. For a checkout discount, correct "
		f"that receipt's rates to the prices paid. This row then says what the charge keeps on {account}"
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
	if not _fits(group):
		return _over_capacity(group, target, moved, account, linked), NEEDS_ACTION, 0.0
	if invoices:
		return _billed_text(row, group, invoices, linked, account), NEEDS_ACTION, 0.0
	lines = "the stock lines"
	if linked:
		trips = "the trip" if len(keys) == 1 else "the trips"
		lines = f"the stock lines of {trips} its Reference Number links ({', '.join(keys)})"
	together = f" (the stock lines of {', '.join(keys)} together)" if len(keys) > 1 else ""
	# A correcting entry for a submitted charge names the charge and every trip it pays for, so it
	# links them explicitly: a later link naming one more trip then adds to the group instead of
	# displacing the first (v1.538.0 fourth review: the first correction was reversed and posted again).
	reference = f"{voucher_no} followed by {_and(keys)}" if keys else voucher_no
	# A linked draft whose link took another trip's automatic pair asks first whether it pays for
	# that trip too (two runs of one purchase), or for that trip and not the linked ones (the link is
	# the mistake), then says what to do if neither (v1.538.0 third and fourth reviews).
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
			charge=reference,
		)
		return f"{shown}{_corrected(fixes)}: {fix}", NEEDS_ACTION, 0.0
	if moved < target:
		missing = _cents(target - moved)
		if not draft:
			fix = _CORRECTING_ENTRY.format(
				amount=_money(missing),
				lines=f"Dr {account} / Cr the expense account the charge used",
				charge=reference,
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


def _issue_sentences(issues, trips_of=None):
	"""What is wrong with a Reference Number, one sentence per problem of :func:`resolve_references`.

	``trips_of``: ``{charge: [run ids of its trips]}``, so a draft correcting entry names the row that
	says whether it is needed -- the trip's, since a charge with trips has no row of its own (v1.538.0
	fourth review)."""
	trips_of = trips_of or {}
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
		elif kind == "receipt":
			one = len(detail) == 1
			said.append(
				f"Its Reference Number lists {_and(detail)}, "
				+ ("a Purchase Receipt that is" if one else "Purchase Receipts that are")
				+ f" not a store run: put {NOT_STORE_RUN} beside "
				+ ("it" if one else "them")
				+ " if this entry's 2210 amount clears "
				+ ("it." if one else "them.")
			)
		elif kind == "part_paid":
			said.append(
				f"Its Reference Number says {PART_PAID} with no run id right before it: {PART_PAID} goes right "
				"after the run id of the trip the charge paid for only in part."
			)
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
			keys = trips_of.get(detail)
			if keys:
				rows = "row" if len(keys) == 1 else "rows"
				ask = "does" if len(keys) == 1 else "do"
				said.append(
					f"It is a draft correcting entry for {detail}: submit it (a draft correcting entry does not "
					f"count until submitted), or delete it if the {rows} of {_and(keys)} (Matched Charge "
					f"{detail}) {ask} not ask for it."
				)
			else:
				said.append(
					f"It is a draft correcting entry for {detail}, which no recorded store run is paired or "
					f"linked with: delete it, unless {detail} has a row of its own (No recorded store run) that "
					"asks for a correcting entry of this amount; then submit it (a draft correcting entry does "
					"not count until submitted)."
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


def _charge_row(bill, amount, account, fixes, issues=(), trips_of=None):
	"""The row of a charge that is neither paired nor linked but carries ``amount`` (net) on 2210,
	or that a link took from the trip it was paired with (:func:`build_rows`)."""
	row = bill["row"]
	draft = _is_draft(row)
	voucher_no = row.get("voucher_no") or ""
	invoice = row.get("voucher_type") == "Purchase Invoice"
	unlinked = bill.get("unlinked")
	show = NEEDS_ACTION
	if not amount and unlinked:
		# Nothing on 2210, but a link overrode the pairing, and the link may be the mistake: a
		# charge linked to the wrong trip took that trip from its own charge, and nothing else
		# would ever say so (v1.538.0 fourth review).
		key = link_key(unlinked["trip"])
		taken = f"It was paired with {key} until {_linked_by(unlinked['groups'], unlinked['trip'])}"
		if draft:
			other = (
				f"put its own trip's run id in its Reference Number, or {NOT_STORE_RUN} if it pays for no store "
				"run, and save it"
			)
		else:
			other = (
				"nothing on it changes (a submitted entry's Reference Number cannot be), and its own trip's row "
				"says how to link it"
			)
		what = (
			f"{taken}, and carries nothing on {account}. If this one is {key}'s charge, take {key} out of the "
			f"Reference Number that links the other one ({_TAKE_OUT}); if not, {other}"
		)
		show = WAITING
	elif amount > 0 and unlinked:
		key = link_key(unlinked["trip"])
		taken = f"It was paired with {key} until {_linked_by(unlinked['groups'], unlinked['trip'])}"
		if invoice:
			# Either answer leaves a correct booking behind (v16 books the invoice's stock lines to 2210
			# itself), so it waits, like any invoice waiting for its receipt (v1.538.0 fourth review).
			what = (
				f"{taken}: keep one charge for {key}. A Purchase Invoice books its stock lines to {account} "
				f"itself, so do not move them: if this invoice is {key}'s charge, take {key} out of the "
				"Reference Number that links the other one; if not, it waits for its own receipt"
			)
			show = WAITING
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
			# Both answers, as the invoice's text gives them: the link may be the mistake (fourth review).
			what = (
				f"{taken}: keep one charge for {key}, which {key}'s row shows as its Matched Charge now. If this "
				f"one is {key}'s charge, take {key} out of the Reference Number that links the other one "
				f"({_TAKE_OUT}); if not, move this one's {_money(amount)} on {account} back to the expense{how}"
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
		# The debit may clear a receipt that is no store run (a card charge that paid for a PO receipt at
		# a store-run vendor): not-store-run and that receipt's name set it aside (fifth review).
		if draft:
			what = (
				f"{shown}. {_clears_draft()}; if you moved it for a trip whose row shows no other charge, put that "
				"trip's run id in this entry's Reference Number; otherwise move it back to the expense. Then save "
				"it; the S-D loop submits it"
			)
		else:
			fix = _CORRECTING_ENTRY.format(
				amount=_money(amount),
				lines=f"Dr the expense account it used / Cr {account}",
				charge=voucher_no,
			)
			what = (
				f"{shown}, and a submitted entry's Reference Number cannot be changed: move it back to the "
				f"expense; {fix}. {_clears_submitted(amount, account)}. {_AGAIN}"
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
	said = _issue_sentences(issues, trips_of)
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


def _back_to_zero(name, amount, account):
	"""The correcting entry that brings a submitted entry's net 2210 ``amount`` back to $0.00."""
	lines = (
		f"Dr the expense account it used / Cr {account}"
		if amount > 0
		else f"Dr {account} / Cr the expense account it used"
	)
	return _CORRECTING_ENTRY.format(amount=_money(abs(amount)), lines=lines, charge=name)


def _clears_draft():
	"""What a QuickBooks draft whose 2210 amount clears a receipt that is no store run does (fifth
	review: a card charge that paid for a PO receipt, its goods moved to 2210 to clear that receipt,
	was told to move them back, which left the receipt open)."""
	return f"If this amount clears {_OTHER_RECEIPT}, put {NOT_STORE_RUN} followed by that receipt's name in its Reference Number"


def _clears_submitted(amount, account):
	"""The same for a submitted QuickBooks entry, whose Reference Number cannot change: moved back by
	its correcting entry, the amount is posted again by a Journal Entry naming the receipt."""
	lines = (
		f"Dr {account} / Cr the expense account it used"
		if amount > 0
		else f"Dr the expense account it used / Cr {account}"
	)
	return (
		f"If that amount cleared {_OTHER_RECEIPT}, also post and submit a Journal Entry for "
		f"{_money(abs(amount))} ({lines}) with Reference Number {NOT_STORE_RUN} followed by that receipt's "
		"name, which keeps that receipt cleared"
	)


#: Where a trip's 2210 moved on an entry that cannot be linked goes next.
_AGAIN = "If you moved it for a trip, that trip's row then says how to move it again, linked"


def _marked_row_text(entry, amount, account, with_entries=()):
	"""A QuickBooks entry marked ``not-store-run`` that still carries 2210 and names no receipt it
	clears (v1.538.0 fourth review: marking it hid the amount from the backstop, and the report read
	clean with the money still on 2210 and nothing to clear it). Three answers: it clears a receipt
	that is no store run (fifth review), it was moved for a trip, or it goes back."""
	name = entry.get("name") or ""
	carries = f"carries {_money(amount)} on" if amount > 0 else f"takes {_money(-amount)} off"
	also = ""
	if with_entries:
		also = f" (with {_and(with_entries)}, marked {NOT_STORE_RUN}, which names it)"
	head = (
		f"Its Reference Number says {NOT_STORE_RUN}, but it still {carries} {account}{also}, and it names no "
		"Purchase Receipt that amount clears"
	)
	back = "move it back to the expense" if amount > 0 else "bring it back to $0.00"
	if _is_draft(entry):
		return (
			f"{head}. {_clears_draft()}; if you moved it for a trip, put that trip's run id in its Reference "
			f"Number in place of {NOT_STORE_RUN}; otherwise {back}. Then save it; the S-D loop submits it"
		)
	fix = _back_to_zero(f"{name} followed by {NOT_STORE_RUN}", amount, account)
	return (
		f"{head}. It is a submitted QuickBooks entry, which is never cancelled: {back}; {fix}. "
		f"{_clears_submitted(amount, account)}. {_AGAIN}"
	)


def _entry_row(entry, amount, issues, outside, account, trips_of=None, marked=None):
	"""The row of a Journal Entry whose Reference Number says something unusable, or whose net 2210
	``amount`` no charge accounts for (:func:`attribute_2210`). ``marked``: the entries marked
	``not-store-run`` that name it, when it is a QuickBooks entry marked so that still carries 2210
	(``amount`` is then theirs and its own together)."""
	name = entry.get("name") or ""
	quickbooks = entry.get("source") == "QuickBooks"
	if marked is not None:
		return _row_of_entry(entry, amount, issues, _marked_row_text(entry, amount, account, marked), account)
	said = _issue_sentences(issues, trips_of)
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
	stuck = quickbooks and not _is_draft(entry)
	if said:
		if fixable:
			said.append(_fix_reference(entry))
			if not amount and stuck:
				show = WAITING
		if amount and stuck and outside is None:
			# Its Reference Number never changes, so waiting for it would wait for good: its 2210 goes
			# back to the expense with a correcting entry naming it, which counts for it whatever its
			# own Reference Number lists (v1.538.0 fourth review).
			back = "move it back to the expense" if amount > 0 else "bring it back to $0.00"
			held = f"Its {_money(amount)} on" if amount > 0 else f"The {_money(-amount)} it takes off"
			said.append(
				f"{held} {account} is therefore not accounted for: {back}; "
				f"{_back_to_zero(name, amount, account)}. {_clears_submitted(amount, account)}. {_AGAIN}."
			)
		elif amount > 0:
			said.append(f"Until then its {_money(amount)} on {account} is not accounted for.")
		elif amount < 0:
			said.append(f"Until then the {_money(-amount)} it takes off {account} is not accounted for.")
		what = " ".join(said)
	elif quickbooks:
		# A QuickBooks entry is a card charge: not-store-run alone never accounts for its 2210 (fourth
		# review), but not-store-run beside the receipt that amount clears does (fifth review).
		carries = f"Carries {_money(amount)} on" if amount > 0 else f"Takes {_money(-amount)} off"
		back = "move it back to the expense" if amount > 0 else "bring it back to $0.00"
		shown = f"{carries} {account} that the report cannot tie to any store run or card charge"
		if _is_draft(entry):
			what = (
				f"{shown}. {_clears_draft()}; if you moved it for a trip whose row shows no other charge, put "
				f"that trip's run id in its Reference Number; otherwise {back}. Then save it; the S-D loop "
				"submits it"
			)
		else:
			what = (
				f"{shown}. It is a submitted QuickBooks entry, which is never cancelled: {back}; "
				f"{_back_to_zero(name, amount, account)}. {_clears_submitted(amount, account)}. {_AGAIN}"
			)
	else:
		carries = f"Carries {_money(amount)} on" if amount > 0 else f"Takes {_money(-amount)} off"
		if _is_draft(entry):
			how = "Edit its Reference Number and save it."
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
	return _row_of_entry(entry, amount, issues, what, account, show)


def _row_of_entry(entry, amount, issues, what, account, show=NEEDS_ACTION):
	"""The columns of a Journal Entry's own row (:func:`_entry_row`)."""
	name = entry.get("name") or ""
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
	  too, or for that trip and not the linked ones;
	* a charge already submitted short of it: the purchase is booked twice until one correcting
	  Journal Entry naming the charge and its trips moves the difference (**Needs action**);
	* a charge whose trips' stock lines are more than the charge itself: a checkout discount the
	  receipt's rates do not show, a part payment (``part-paid``), or -- only if neither -- a trip that
	  is not its, linked to its own charge (**Needs action**, nothing ticked);
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
	  Reference Number; only if it has none, bill it from its receipts after the cutover. A trip a
	  link took its charge from is asked all three ways;
	* a charge neither paired nor linked that carries a net 2210 debit: link it to its trip, or move
	  it back to the expense (**Needs action**); a Purchase Invoice waits for its receipt
	  (**Waiting**); a Journal Entry a link took from its trip, carrying nothing, is asked whether it
	  is that trip's charge (**Waiting**);
	* a Journal Entry whose Reference Number lists a dead key or points two ways, a draft correcting
	  entry, one whose 2210 amount no charge accounts for, or a QuickBooks entry marked
	  ``not-store-run`` that still carries 2210 and names no receipt it clears (**Needs action**).

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
	trips_of = {}
	for group in (window or {}).get("groups") or ():
		voucher = group["charge"]["row"].get("voucher_no")
		if voucher and group["trips"]:
			trips_of.setdefault(voucher, [link_key(trip) for trip in group["trips"]])

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
			# A charge over its own amount has no target it can reach: nothing is ticked (fourth review).
			checked = 1 if target > 0 and moved == target and _fits(group) else 0
		else:
			on, to_move, checked = 0.0, 0.0, 0
			if receipts_billed and receipts_billed == len(receipts) and trip.get("displaced_by"):
				action, show = _billed_displaced(invoices, trip["displaced_by"]), WAITING
			elif receipts_billed and receipts_billed == len(receipts):
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
		row = bill["row"]
		# A Journal Entry a link took from its trip is listed even with nothing on 2210 (Waiting): the
		# link may be the mistake, and this row is the only place that asks (v1.538.0 fourth review).
		taken = bill.get("unlinked") and row.get("voucher_type") == "Journal Entry"
		if amount == 0 and not taken:
			continue
		issues = problems.get(row.get("voucher_no")) if row.get("voucher_type") == "Journal Entry" else None
		if issues:
			merged.add(row.get("voucher_no"))
		rows.append(
			_charge_row(
				bill, amount, account_of_company(row.get("company")), sorted(fixes_of(bill)), issues, trips_of
			)
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
		# A marked QuickBooks entry still carrying 2210 gets one row, with the 2210 of the entries
		# marked not-store-run that name it (attribute_2210).
		marked = ledger.get("marked") or {}
		units = {}
		for name, owner in marked.items():
			units[owner] = _cents(units.get(owner, 0.0) + ledger["unaccounted"][name])
		names = (set(ledger["unaccounted"]) - set(marked)) | set(problems) | set(units)
		accounted = {
			group["charge"]["row"].get("voucher_no")
			for group in window.get("groups") or ()
			if group["accounted"]
		}
		for name in sorted(names):
			entry = entries.get(name)
			if entry is None or name in merged or name in ledger["excluded"]:
				continue
			day = metrics._as_day(entry.get("day"))
			# A marked entry's row is shown whatever its date: the money is its entries' in range. So
			# is a draft correcting entry of a charge accounted for here, dated after To Date: the S-D
			# loop submits it all the same (v1.538.0 fourth review).
			late_draft = (
				day is not None
				and day >= early
				and problems.get(name)
				and all(kind == "draft" and detail in accounted for kind, detail in problems[name])
			)
			if name not in units and not late_draft and (day is None or not early <= day <= end):
				continue
			issues = problems.get(name)
			if name in units:
				amount = units[name]
				naming = sorted(other for other, owner in marked.items() if owner == name and other != name)
			else:
				amount, naming = ledger["unaccounted"].get(name, 0.0), None
			if not amount and not issues:
				continue
			rows.append(
				_entry_row(
					entry,
					amount,
					issues,
					ledger["outside"].get(name),
					account_of_company(entry.get("company")),
					trips_of,
					naming,
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

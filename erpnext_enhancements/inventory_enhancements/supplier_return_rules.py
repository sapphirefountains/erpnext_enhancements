# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supplier Return: every judgement the process makes. No Frappe, no I/O.

A Supplier Return is the record of sending something we bought back to the supplier: the request
we emailed, the supplier's return number, how it went back, and whether the money came back. It
does not move stock and it does not post to the ledger. ERPNext's own documents do that, and this
module decides which of them a return needs:

* **Purchase Return** (a Purchase Receipt with ``is_return``, against the original receipt). It
  takes a stock item out of stock and reverses the receipt's ledger entry (credit the stock
  account, debit Stock Received But Not Billed). For a non-stock line it posts nothing, but it
  still lowers the received quantity on the purchase order line, which is what lets a replacement
  be received against the same line and makes ERPNext bill the receipt for less.
* **Debit Note** (a Purchase Invoice with ``is_return``, against the bill). Only when a bill
  exists in ERPNext. It lowers what we owe the supplier.
* **Payment Entry** (Receive, from the Supplier). The refund itself.

Read against ERPNext **v16.54.0** (``controllers/sales_and_purchase_return.py``,
``purchase_receipt.make_item_gl_entries`` and ``get_return_status_updaters``,
``purchase_invoice.add_supplier_gl_entry``, ``payment_entry.set_payment_type``), the version
production ran on 2026-11-12. The rules here choose documents; they never restate what those
documents post.

Three kinds of line (:func:`line_kind`):

* ``Stock``: the item keeps stock and the receipt line wrote a stock ledger entry.
* ``Non-stock``: the item keeps no stock. Nothing is in stock to take out.
* ``Record only``: the item keeps stock **now**, but this receipt line never wrote a stock ledger
  entry, because the item was switched to a stock item after it was received. ERPNext would build
  a return's ledger from today's flag and take out stock the line never put in, so
  ``inventory_enhancements.maintain_stock.refuse_unstocked_receipt_return`` refuses it. The return
  is still recorded and tracked here; no stock document is made, and the record says why.

Tabs, per ``CLAUDE.md`` (new file).
"""

import re
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------

DRAFT = "Draft"
REQUESTED = "Requested"
APPROVED = "Approved by Supplier"
DENIED = "Denied"
SENT_BACK = "Sent Back"
REFUND_RECEIVED = "Refund Received"
CREDIT_RECEIVED = "Credit Received"
REPLACEMENT_RECEIVED = "Replacement Received"
CLOSED_NO_CREDIT = "Closed Without Credit"
CANCELED = "Canceled"

#: The Select's options, top to bottom. Adding one needs no data patch; renaming one does.
STATUSES = (
	DRAFT,
	REQUESTED,
	APPROVED,
	DENIED,
	SENT_BACK,
	REFUND_RECEIVED,
	CREDIT_RECEIVED,
	REPLACEMENT_RECEIVED,
	CLOSED_NO_CREDIT,
	CANCELED,
)

#: Nothing more is expected to happen.
FINAL = frozenset(
	{DENIED, REFUND_RECEIVED, CREDIT_RECEIVED, REPLACEMENT_RECEIVED, CLOSED_NO_CREDIT, CANCELED}
)

#: The goods have left the building.
GONE = frozenset({SENT_BACK, REFUND_RECEIVED, CREDIT_RECEIVED, REPLACEMENT_RECEIVED, CLOSED_NO_CREDIT})

#: Returns that never happened: their quantities are free to return again.
NOT_COUNTED = frozenset({DENIED, CANCELED})

# What we ask the supplier for.
REFUND = "Refund"
CREDIT = "Credit on Account"
REPLACEMENT = "Replacement"
RESOLUTIONS = (REFUND, CREDIT, REPLACEMENT)

REASONS = ("Wrong item", "Damaged", "Defective", "Not needed", "Over-shipped", "Other")

CARRIER = "Carrier"
DROPPED_OFF = "Dropped off"
SUPPLIER_PICKUP = "Supplier pickup"
METHODS = (CARRIER, DROPPED_OFF, SUPPLIER_PICKUP)

#: How a request reached the supplier. Only "Email" is sent by this app.
REQUEST_WAYS = ("Email", "Phone", "In person", "Supplier's website")

STOCK = "Stock"
NON_STOCK = "Non-stock"
RECORD_ONLY = "Record only"
LINE_KINDS = (STOCK, NON_STOCK, RECORD_ONLY)

TYPED = "Typed by hand"
FROM_DEBIT_NOTE = "Debit Note"
FROM_PAYMENT = "Payment Entry"
MONEY_SOURCES = (TYPED, FROM_DEBIT_NOTE, FROM_PAYMENT)

#: Said on the record, the print and the report whenever money was typed, never softened.
NOT_BACKED = "Typed by hand. It is not backed by an ERPNext payment."

# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------

#: Start a return, send the request, record the supplier's answer, mark it sent back.
CREATE_ROLES = frozenset(
	{"Purchase User", "Purchase Manager", "Stock User", "Stock Manager", "System Manager"}
)

#: Say the money came back (or that it never will).
MONEY_ROLES = frozenset({"Accounts User", "Accounts Manager", "Purchase Manager", "System Manager"})

#: Undo a step somebody recorded by mistake.
REOPEN_ROLES = frozenset({"Purchase Manager", "Accounts Manager", "System Manager"})

#: Read only. Everybody above, and nobody else, opens the record at all (the DocType's permissions).
ALL_ROLES = CREATE_ROLES | MONEY_ROLES

# action -> (statuses it may start from, status it leads to, roles)
ACTIONS = {
	"request": ((DRAFT,), REQUESTED, CREATE_ROLES),
	"approve": ((DRAFT, REQUESTED), APPROVED, CREATE_ROLES),
	"deny": ((DRAFT, REQUESTED, APPROVED), DENIED, CREATE_ROLES),
	"send_back": ((DRAFT, REQUESTED, APPROVED), SENT_BACK, CREATE_ROLES),
	"refund": ((SENT_BACK,), REFUND_RECEIVED, MONEY_ROLES),
	"credit": ((SENT_BACK,), CREDIT_RECEIVED, MONEY_ROLES),
	"replacement": ((SENT_BACK,), REPLACEMENT_RECEIVED, CREATE_ROLES),
	"close": ((SENT_BACK,), CLOSED_NO_CREDIT, MONEY_ROLES),
	"cancel": ((DRAFT, REQUESTED, APPROVED), CANCELED, CREATE_ROLES),
}

#: Where "Reopen" puts a finished return back to.
REOPEN_TO = {
	DENIED: REQUESTED,
	CANCELED: DRAFT,
	REFUND_RECEIVED: SENT_BACK,
	CREDIT_RECEIVED: SENT_BACK,
	REPLACEMENT_RECEIVED: SENT_BACK,
	CLOSED_NO_CREDIT: SENT_BACK,
}


class RuleError(ValueError):
	"""A refusal, in words meant for the person."""


def may(action, roles):
	"""Whether somebody holding ``roles`` may take ``action``."""
	roles = set(roles or ())
	if action == "reopen":
		return bool(roles & REOPEN_ROLES)
	spec = ACTIONS.get(action)
	return bool(spec and roles & spec[2])


def next_status(action, status):
	"""The status ``action`` leads to from ``status``. Raises :class:`RuleError` when it may not."""
	if action == "reopen":
		target = REOPEN_TO.get(status)
		if not target:
			raise RuleError(f"A return that is {status} has nothing to reopen.")
		return target
	spec = ACTIONS.get(action)
	if not spec:
		raise RuleError(f"Unknown action: {action}.")
	sources, target, _roles = spec
	if status not in sources:
		raise RuleError(_cannot(action, status))
	return target


_VERBS = {
	"request": "be requested",
	"approve": "be marked approved",
	"deny": "be marked denied",
	"send_back": "be marked sent back",
	"refund": "have a refund recorded",
	"credit": "have a credit recorded",
	"replacement": "have a replacement recorded",
	"close": "be closed without credit",
	"cancel": "be canceled",
}


def _cannot(action, status):
	text = f"A return that is {status} can't {_VERBS.get(action, action)}."
	if action in ("refund", "credit", "replacement", "close") and status in (DRAFT, REQUESTED, APPROVED):
		text += " Mark it Sent Back first."
	if action == "cancel" and status in GONE:
		text += " The items have already gone back. Close it without credit instead."
	return text


def allowed_actions(status, roles, money_backed=False):
	"""The actions the form offers, in the order the steps happen.

	``money_backed``: a Debit Note or Payment Entry is linked and submitted, so a typed refund or
	credit is refused (:func:`check_typed_money`); the buttons are not offered.
	"""
	out = []
	for action, (sources, _target, _roles) in ACTIONS.items():
		if status not in sources or not may(action, roles):
			continue
		if money_backed and action in ("refund", "credit"):
			continue
		out.append(action)
	if status in REOPEN_TO and may("reopen", roles):
		out.append("reopen")
	return out


def waiting_on(status, resolution):
	"""Whose move it is: ``"us"``, ``"supplier"``, ``"money"``, ``"replacement"`` or ``""``."""
	if status == DRAFT or status == APPROVED:
		return "us"
	if status == REQUESTED:
		return "supplier"
	if status == SENT_BACK:
		return "replacement" if resolution == REPLACEMENT else "money"
	return ""


def waiting_on_money(status, resolution):
	"""The list's "Waiting on money" filter: sent back, and a refund or credit is still owed."""
	return waiting_on(status, resolution) == "money"


def _as_date(value):
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	if not value:
		return None
	return datetime.fromisoformat(str(value)[:19].replace("T", " ")).date()


def days_waiting(since, today):
	"""Whole days at the current step, never negative. ``None`` when the step has no start."""
	since, today = _as_date(since), _as_date(today)
	if not since or not today:
		return None
	return max((today - since).days, 0)


# ---------------------------------------------------------------------------
# Lines and quantities
# ---------------------------------------------------------------------------


def line_kind(is_stock_item, wrote_stock):
	"""``Stock``, ``Non-stock`` or ``Record only`` for one receipt line.

	``is_stock_item`` is the Item's flag **today**; ``wrote_stock`` is whether the receipt line has a
	stock ledger entry. The test is the ledger, not a date, the same test the Maintain Stock guard
	uses, so the two cannot disagree about a line.
	"""
	if not is_stock_item:
		return NON_STOCK
	return STOCK if wrote_stock else RECORD_ONLY


def returnable_qty(received, claimed_elsewhere=0.0, returned_outside=0.0):
	"""How much of a receipt line may still go back.

	``received``: the line's accepted quantity. ``claimed_elsewhere``: the same line on other
	Supplier Returns that still stand (not Canceled, not Denied). ``returned_outside``: standard
	Purchase Returns against the line that no Supplier Return is linked to (somebody used ERPNext's
	own button). A Supplier Return's own linked Purchase Return is the same goods, so it is in
	neither figure.
	"""
	left = float(received or 0) - float(claimed_elsewhere or 0) - float(returned_outside or 0)
	return round(max(left, 0.0), 6)


def _plain(number):
	number = round(float(number or 0), 6)
	return str(int(number)) if number == int(number) else f"{number:g}"


def quantity_problems(lines):
	"""Why these lines may not be saved, one sentence each. Empty when they may.

	Each line: ``{"idx", "item_code", "qty", "returnable", "received", "reason", "condition_notes",
	"purchase_receipt_item"}``. Two lines naming the same receipt line are judged together.
	"""
	problems = []
	asked = {}
	for line in lines or []:
		idx, item = line.get("idx"), line.get("item_code") or "?"
		qty = float(line.get("qty") or 0)
		if qty <= 0:
			problems.append(f"Row {idx}: enter how many of {item} are going back.")
			continue
		if line.get("reason") not in REASONS:
			problems.append(f"Row {idx}: pick a reason for returning {item}.")
		elif line.get("reason") == "Other" and not (line.get("condition_notes") or "").strip():
			problems.append(f"Row {idx}: the reason is Other, so say what is wrong in Condition Notes.")
		key = line.get("purchase_receipt_item") or ("row", idx)
		asked.setdefault(key, {"qty": 0.0, "line": line})["qty"] += qty
	for entry in asked.values():
		line = entry["line"]
		limit = float(line.get("returnable") or 0)
		if entry["qty"] > limit + 1e-9:
			item, received = line.get("item_code") or "?", float(line.get("received") or 0)
			if limit <= 0:
				problems.append(
					f"Row {line.get('idx')}: all {_plain(received)} of {item} on this receipt line "
					"have already been returned or are on another return."
				)
			else:
				problems.append(
					f"Row {line.get('idx')}: only {_plain(limit)} of {item} can still go back "
					f"({_plain(received)} received, {_plain(received - limit)} already returned or on "
					f"another return). This return asks for {_plain(entry['qty'])}."
				)
	return problems


def tax_share(receipt_tax, receipt_net, return_net):
	"""The receipt's tax, in proportion to what is going back. Zero when there is nothing to share."""
	receipt_net, return_net = float(receipt_net or 0), float(return_net or 0)
	if receipt_net <= 0 or return_net <= 0:
		return 0.0
	return round(float(receipt_tax or 0) * min(return_net / receipt_net, 1.0), 2)


def totals(lines, tax_amount=0.0, restocking_fee=0.0, return_shipping_cost=0.0, shipping_deducted=False):
	"""``{"total_amount", "expected_amount"}``.

	Expected back = the lines + tax to be refunded - the restocking fee, and - return shipping only
	when the supplier takes it out of the refund. Shipping we pay a carrier ourselves is a cost of
	ours, not money the supplier keeps. Never below zero.
	"""
	total = round(sum(float(l.get("qty") or 0) * float(l.get("rate") or 0) for l in lines or []), 2)
	expected = total + float(tax_amount or 0) - float(restocking_fee or 0)
	if shipping_deducted:
		expected -= float(return_shipping_cost or 0)
	return {"total_amount": total, "expected_amount": round(max(expected, 0.0), 2)}


# ---------------------------------------------------------------------------
# Which stock document
# ---------------------------------------------------------------------------


def stock_plan(lines, receipt=""):
	"""Which lines go on the standard Purchase Return, which are record-only, and why.

	``{"return_lines", "record_only_lines", "needs_purchase_return", "takes_stock", "note"}``. A
	Purchase Return carries the ``Stock`` and ``Non-stock`` lines. ``Record only`` lines are left
	off it on purpose (see the module docstring) and ``note`` says so in the record's own words.
	"""
	lines = list(lines or [])
	return_lines = [l for l in lines if l.get("line_kind") in (STOCK, NON_STOCK)]
	record_only = [l for l in lines if l.get("line_kind") == RECORD_ONLY]
	takes_stock = any(l.get("line_kind") == STOCK for l in return_lines)
	note = ""
	if record_only:
		items = ", ".join(sorted({str(l.get("item_code")) for l in record_only}))
		where = f"Purchase Receipt {receipt}" if receipt else "the receipt"
		note = (
			f"No stock document is made for {items}. {where} never added "
			f"{'it' if len(record_only) == 1 else 'them'} to stock: "
			f"{'it was' if len(record_only) == 1 else 'they were'} received before the item kept stock, "
			"and ERPNext would take out stock that was never put in. This return is a record only for "
			f"{'that line' if len(record_only) == 1 else 'those lines'}. If the item has been counted "
			"into stock since, take it out on the Count Desk."
		)
	return {
		"return_lines": return_lines,
		"record_only_lines": record_only,
		"needs_purchase_return": bool(return_lines),
		"takes_stock": takes_stock,
		"note": note,
	}


def stock_state(status, plan, purchase_return=None):
	"""One sentence for the record: where the stock side stands.

	``purchase_return``: ``{"name", "docstatus"}`` of the linked Purchase Return, or ``None``.
	"""
	if status in NOT_COUNTED:
		return "Nothing went back, so stock is unchanged."
	if not plan.get("needs_purchase_return"):
		return "No stock document: this return is a record only."
	linked = purchase_return if purchase_return and int(purchase_return.get("docstatus") or 0) != 2 else None
	what = (
		"takes the items out of stock"
		if plan.get("takes_stock")
		else "lowers the received quantity on the order"
	)
	if linked and int(linked.get("docstatus") or 0) == 1:
		done = (
			"took the items out of stock"
			if plan.get("takes_stock")
			else "lowered the received quantity on the order"
		)
		return f"Purchase Return {linked.get('name')} {done}."
	if linked:
		return f"Purchase Return {linked.get('name')} is a draft. Submit it: that {what}."
	if status not in GONE:
		return f"When this is marked Sent Back, create the Purchase Return: that {what}."
	return f"Not done yet. Create the Purchase Return: that {what}."


def needs_stock_action(status, plan, purchase_return=None):
	"""Whether the form should ask for the Purchase Return now: sent back, and none submitted."""
	if status not in GONE or not plan.get("needs_purchase_return"):
		return False
	return not (purchase_return and int(purchase_return.get("docstatus") or 0) == 1)


def trim_return_rows(rows, wanted):
	"""Fit a mapped Purchase Return's rows to this return's lines.

	``rows``: the standard mapper's rows, ``[{"purchase_receipt_item", "qty", ...}]`` with negative
	quantities (everything still returnable). ``wanted``: ``{receipt line: quantity}`` from the
	return's ``Stock`` and ``Non-stock`` lines. Returns ``(kept, short)``: the rows to keep, each
	with its ``qty`` set to minus what is wanted, and the receipt lines ERPNext could not cover in
	full as ``{receipt line: (wanted, available)}``. Rows of lines not on this return are dropped.
	"""
	kept, short, seen = [], {}, set()
	for row in rows or []:
		key = row.get("purchase_receipt_item")
		want = float((wanted or {}).get(key) or 0)
		if want <= 0 or key in seen:
			continue
		seen.add(key)
		available = abs(float(row.get("qty") or 0))
		if available + 1e-9 < want:
			short[key] = (want, available)
			want = available
		if want <= 0:
			continue
		row = dict(row)
		row["qty"] = -want
		kept.append(row)
	for key, want in (wanted or {}).items():
		if key not in seen and float(want or 0) > 0:
			short[key] = (float(want), 0.0)
	return kept, short


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def _submitted(document):
	return bool(document) and int(document.get("docstatus") or 0) == 1


def money_from_documents(debit_note=None, payment_entry=None):
	"""What the linked accounting documents say about the money.

	``debit_note``: ``{"name", "docstatus", "amount", "owed", "posting_date", "reference"}`` where
	``amount`` is the note's total (positive) and ``owed`` is what the supplier still owes us on it
	(positive; zero once it has been set against a bill or refunded).
	``payment_entry``: ``{"name", "docstatus", "amount", "posting_date", "reference"}``.

	Returns ``{"backed", "status", "source", "amount", "date", "reference", "note"}``:

	* a submitted Payment Entry: **Refund Received**, the payment's amount and date;
	* else a submitted Debit Note with nothing owed on it: **Credit Received**, the note's amount;
	* else a submitted Debit Note the supplier still owes us on: no status yet (``status`` is
	  ``None``), but ``backed`` is true, so nobody types an outcome over it;
	* else ``backed`` is false and the outcome is whatever a person types.
	"""
	blank = {
		"backed": False,
		"status": None,
		"source": "",
		"amount": 0.0,
		"date": None,
		"reference": "",
		"note": "",
	}
	if _submitted(payment_entry):
		return dict(
			blank,
			backed=True,
			status=REFUND_RECEIVED,
			source=FROM_PAYMENT,
			amount=round(float(payment_entry.get("amount") or 0), 2),
			date=payment_entry.get("posting_date"),
			reference=payment_entry.get("reference") or payment_entry.get("name") or "",
			note=f"From Payment Entry {payment_entry.get('name')}.",
		)
	if _submitted(debit_note):
		amount = round(abs(float(debit_note.get("amount") or 0)), 2)
		owed = round(abs(float(debit_note.get("owed") or 0)), 2)
		if owed > 0.005:
			return dict(
				blank,
				backed=True,
				source=FROM_DEBIT_NOTE,
				note=(
					f"Debit Note {debit_note.get('name')} is for {amount:,.2f} and the supplier still "
					f"owes {owed:,.2f} on it. Record the refund as a Payment Entry against it, or set it "
					"against a later bill in Payment Reconciliation."
				),
			)
		return dict(
			blank,
			backed=True,
			status=CREDIT_RECEIVED,
			source=FROM_DEBIT_NOTE,
			amount=amount,
			date=debit_note.get("posting_date"),
			reference=debit_note.get("reference") or debit_note.get("name") or "",
			note=f"From Debit Note {debit_note.get('name')}.",
		)
	return blank


def check_typed_money(kind, amount, on, reference, today, documents=None):
	"""Refuse a typed refund or credit that may not be typed. Raises :class:`RuleError`.

	``documents`` is :func:`money_from_documents`'s answer: once a Debit Note or Payment Entry is
	linked and submitted, the outcome and the amount come from it.
	"""
	if kind not in ("refund", "credit"):
		raise RuleError("Say whether it was a refund or a credit.")
	if documents and documents.get("backed"):
		raise RuleError(
			f"The {documents.get('source')} linked to this return decides the outcome and the amount, "
			"so they can't be typed. " + (documents.get("note") or "")
		)
	try:
		amount = float(amount)
	except (TypeError, ValueError):
		raise RuleError("Enter the amount that came back.") from None
	if amount <= 0:
		raise RuleError(
			"Enter the amount that came back. If nothing is coming back, use Close Without Credit."
		)
	on = _as_date(on)
	if not on:
		raise RuleError("Enter the date the money came back.")
	if on > _as_date(today):
		raise RuleError("The date the money came back can't be in the future.")
	if not (reference or "").strip():
		raise RuleError(
			"Enter a reference: the check number, the credit memo number, or where the refund shows."
		)
	return {"amount": round(amount, 2), "date": on, "reference": reference.strip()}


def check_send_back(method, carrier, tracking_number):
	"""Refuse "Sent Back" without how it went. Raises :class:`RuleError`."""
	if method not in METHODS:
		raise RuleError("Say how it went back: Carrier, Dropped off or Supplier pickup.")
	if method == CARRIER and not ((carrier or "").strip() and (tracking_number or "").strip()):
		raise RuleError("Sent by carrier: enter the carrier and the tracking number.")


def difference(expected, received):
	"""Expected less received: positive when the supplier gave back less than we asked."""
	return round(float(expected or 0) - float(received or 0), 2)


# ---------------------------------------------------------------------------
# The bill
# ---------------------------------------------------------------------------


def bill_notice(returns):
	"""What the person entering a supplier's bill is told, or ``""``.

	``returns``: ``[{"name", "status", "items": [(item_code, qty)], "expected_amount"}]`` for the
	Supplier Returns that touch the receipt lines or order lines on the bill and still stand.
	"""
	if not returns:
		return ""
	parts = []
	for entry in returns:
		items = ", ".join(f"{_plain(qty)} x {code}" for code, qty in entry.get("items") or [])
		gone = "went back to the supplier" if entry.get("status") in GONE else "are being returned"
		parts.append(f"{entry.get('name')} ({entry.get('status')}): {items} {gone}.")
	return (
		"Some of what this bill covers is on a Supplier Return. Check the bill does not charge for "
		"it, or expect a credit: " + " ".join(parts)
	)


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------

#: The Desk Email Template that words the request. Seeded once; after that it is the buyer's.
EMAIL_TEMPLATE = "Supplier Return Request"

DEFAULT_SUBJECT = (
	"Return request {{ name }} from {{ company }}{% if purchase_order %} (PO {{ purchase_order }}){% endif %}"
)

#: Content only: paragraphs and one table, no container, no width, no colors. The shared email
#: shell adds the letterhead. Every value is escaped before it reaches this template.
DEFAULT_BODY = """<p>Hello{% if contact_name %} {{ contact_name }}{% endif %},</p>
<p>We would like to return the items below{% if purchase_order %} from our purchase order {{ purchase_order }}{% endif %}, and we are asking for {{ asking_for }}.</p>
{{ lines_table }}
{% if notes %}<p>{{ notes }}</p>{% endif %}
<p>Please reply with a return authorization number and where to send the items. The attached PDF has the full details.</p>
<p>Thank you,<br>{{ sender_name }}<br>{{ company }}{% if phone %}<br>{{ phone }}{% endif %}</p>"""

#: Where the items table goes in the message the person edits. The Desk's text editor rewrites
#: a table's markup, so the table itself is never in the editable text: this line stands in for
#: it, and the server puts the real table (built from the return's own lines) in its place.
LINES_MARKER = "[The items being returned are listed here]"

ASKING_FOR = {
	REFUND: "a refund",
	CREDIT: "a credit on our account",
	REPLACEMENT: "a replacement",
}


def email_lines(lines):
	"""The request's table rows: item, quantity, reason. Four columns at most fit a phone."""
	rows = []
	for line in lines or []:
		name = line.get("item_name") or line.get("item_code") or ""
		code = line.get("supplier_part_no") or line.get("item_code") or ""
		qty = f"{_plain(line.get('qty'))} {line.get('uom') or ''}".strip()
		reason = line.get("reason") or ""
		notes = (line.get("condition_notes") or "").strip()
		rows.append([code, name, qty, f"{reason}: {notes}" if notes else reason])
	return rows


def email_context(doc, lines, company, sender_name, contact_name="", phone=""):
	"""The template's variables, as plain text. The caller escapes them and renders the table."""
	orders = sorted({l.get("purchase_order") for l in lines or [] if l.get("purchase_order")})
	return {
		"name": doc.get("name") or "",
		"company": company or "",
		"supplier": doc.get("supplier_name") or doc.get("supplier") or "",
		"purchase_order": ", ".join(orders),
		"purchase_receipt": doc.get("purchase_receipt") or "",
		"asking_for": ASKING_FOR.get(doc.get("requested_resolution"), "a refund"),
		"notes": (doc.get("request_notes") or "").strip(),
		"contact_name": (contact_name or "").strip(),
		"sender_name": sender_name or "",
		"phone": phone or "",
	}


def place_lines_table(body, table_html):
	"""The edited message with the items table where :data:`LINES_MARKER` stood.

	A marker alone in its paragraph is replaced whole; a marker inside a sentence is replaced in
	place; a message whose marker was deleted gets the table at the end, because a return request
	with no items in it is not one. Only the first marker is used.
	"""
	body = body or ""
	marker = re.escape(LINES_MARKER)
	paragraph = re.compile(r"<p[^>]*>\s*" + marker + r"\s*</p>")
	if paragraph.search(body):
		placed = paragraph.sub(lambda _m: table_html, body, count=1)
	elif LINES_MARKER in body:
		placed = body.replace(LINES_MARKER, table_html, 1)
	else:
		return body + table_html
	return paragraph.sub("", placed).replace(LINES_MARKER, "")


def recipient_choices(candidates):
	"""Distinct, plausible addresses in the order given: ``[{"email", "label"}]``.

	``candidates``: ``[(email, label)]``, best first (the address typed on the return, the
	supplier's primary contact, its other contacts, the supplier's own email).
	"""
	out, seen = [], set()
	for email, label in candidates or []:
		email = (email or "").strip()
		key = email.lower()
		if not email or key in seen or "@" not in email or " " in email:
			continue
		seen.add(key)
		out.append({"email": email, "label": f"{label} <{email}>" if label else email})
	return out


def check_recipient(email):
	"""One address, shaped like one. Raises :class:`RuleError`."""
	email = (email or "").strip()
	local, _, domain = email.partition("@")
	if not local or "." not in domain or " " in email or "," in email or ";" in email:
		raise RuleError("Enter one email address for the supplier.")
	return email


# ---------------------------------------------------------------------------
# For other modules
# ---------------------------------------------------------------------------

#: The keys of each dict ``supplier_returns.returned_lines`` gives. A contract: the Project
#: Materials report reads them, so they are added to, never renamed.
RETURNED_LINE_KEYS = (
	"supplier_return",
	"status",
	"supplier",
	"purchase_order",
	"purchase_order_item",
	"purchase_receipt",
	"purchase_receipt_item",
	"item_code",
	"project",
	"qty",
	"uom",
	"stock_qty",
	"amount",
	"gone",
	"open",
)


def returned_line(row):
	"""One Supplier Return line as other modules see it, or ``None`` for a return that never happened."""
	status = row.get("status")
	if status in NOT_COUNTED:
		return None
	qty = float(row.get("qty") or 0)
	factor = float(row.get("conversion_factor") or 1) or 1.0
	return {
		"supplier_return": row.get("supplier_return"),
		"status": status,
		"supplier": row.get("supplier"),
		"purchase_order": row.get("purchase_order"),
		"purchase_order_item": row.get("purchase_order_item"),
		"purchase_receipt": row.get("purchase_receipt"),
		"purchase_receipt_item": row.get("purchase_receipt_item"),
		"item_code": row.get("item_code"),
		"project": row.get("project"),
		"qty": qty,
		"uom": row.get("uom"),
		"stock_qty": round(qty * factor, 6),
		"amount": round(float(row.get("amount") or 0), 2),
		"gone": status in GONE,
		"open": status not in FINAL,
	}


# ---------------------------------------------------------------------------
# The report: Supplier Returns Money Outstanding
# ---------------------------------------------------------------------------

VIEW_WAITING = "Waiting on money"
VIEW_SHORT = "Came back short or over"
VIEW_ALL = "All returns"
REPORT_VIEWS = (VIEW_WAITING, VIEW_SHORT, VIEW_ALL)


def in_view(view, status, resolution, expected, received):
	"""Whether a return belongs in a view of the report.

	*Waiting on money*: sent back, with a refund or credit still owed. *Came back short or over*: a
	refund or credit was recorded and it is not what was expected, or the return was closed without
	credit while money was expected. *All returns*: everything but Canceled.
	"""
	if view == VIEW_ALL:
		return status != CANCELED
	if view == VIEW_SHORT:
		if status in (REFUND_RECEIVED, CREDIT_RECEIVED):
			return abs(difference(expected, received)) > 0.005
		return status == CLOSED_NO_CREDIT and float(expected or 0) > 0.005
	return waiting_on_money(status, resolution)


def bill_state(debit_note, bills):
	"""One phrase on where the bill stands, for the person who enters or checks it."""
	if debit_note:
		return f"Debit Note {debit_note}"
	if bills:
		return "Billed: " + ", ".join(bills) + ". Needs a Debit Note"
	return "Not billed in ERPNext. The bill should come in lower"


def report_row(doc, bills, today):
	"""One row of the report from a Supplier Return's header ``doc`` (a dict)."""
	status, resolution = doc.get("status"), doc.get("requested_resolution")
	expected, received = float(doc.get("expected_amount") or 0), float(doc.get("amount_received") or 0)
	waiting = waiting_on_money(status, resolution)
	settled = status in (REFUND_RECEIVED, CREDIT_RECEIVED, CLOSED_NO_CREDIT)
	return {
		"supplier_return": doc.get("name"),
		"supplier": doc.get("supplier"),
		"supplier_name": doc.get("supplier_name"),
		"status": status,
		"requested_resolution": resolution,
		"sent_back_on": doc.get("sent_back_on"),
		"days_waiting": days_waiting(doc.get("sent_back_on") or doc.get("status_changed_on"), today)
		if waiting
		else None,
		"expected_amount": round(expected, 2),
		"amount_received": round(received, 2) if settled else None,
		"outstanding": round(expected, 2)
		if waiting
		else (difference(expected, received) if settled else None),
		"money_source": NOT_BACKED if doc.get("money_source") == TYPED else (doc.get("money_source") or ""),
		"bill": bill_state(doc.get("debit_note"), bills) if status in GONE else "",
		"supplier_return_number": doc.get("supplier_return_number"),
		"purchase_receipt": doc.get("purchase_receipt"),
		"purchase_order": doc.get("purchase_order"),
	}


def report_summary(rows):
	"""The three cards above the report: how many are waiting, how much, and the oldest."""
	waiting = [row for row in rows if row.get("days_waiting") is not None]
	owed = round(sum(float(row.get("outstanding") or 0) for row in waiting), 2)
	oldest = max((row["days_waiting"] for row in waiting), default=0)
	return {"waiting": len(waiting), "owed": owed, "oldest_days": oldest}

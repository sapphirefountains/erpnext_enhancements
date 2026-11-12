# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supplier Return: the reads, the steps, the standard documents and the email (v1.807.0).

Nik, 2026-11-12: "a return process that is simple and tracked", using ERPNext's own pieces where
they exist. ERPNext already returns goods (a Purchase Receipt with ``is_return``), lowers a bill (a
Purchase Invoice with ``is_return``, the Debit Note) and takes a refund (a Payment Entry). It has
no record of the *request*: what we emailed the supplier, the return number they gave, the
tracking number, and whether the money came back. A Supplier Return is that record, and it links
the three standard documents. Every judgement is ``supplier_return_rules``; this module reads,
writes and calls ERPNext.

What is careful here, including the parts that look like omissions:

* **Nothing here writes a ledger entry.** Stock and money move only when a person submits the
  standard document this module drafted. ``make_purchase_return`` and ``make_debit_note`` call
  ERPNext's own mappers (``purchase_receipt.make_purchase_return``,
  ``purchase_invoice.make_debit_note``), cut the result down to this return's lines, and save a
  **draft**. The refund is ERPNext's ``get_payment_entry``, opened unsaved.
* **No Custom Field on Purchase Receipt, Purchase Invoice or Payment Entry.** The link lives on
  the Supplier Return (``purchase_return``, ``debit_note``, ``replacement_receipt`` and the
  ``payments`` table); the standard documents find their return by asking for it
  (:func:`linked_to`), and their doc_events call :func:`on_document_change`. A Custom Field is
  forever on this site (hidden, never deleted), and four of them were not worth that.
* **A record-only line never reaches ERPNext.** A stock item's receipt line with no stock ledger
  entry is left off the Purchase Return (``supplier_return_rules.stock_plan``), because
  ``maintain_stock.refuse_unstocked_receipt_return`` would refuse it, rightly. That guard is not
  touched and not bypassed: ``flags.ignore_maintain_stock_guard`` is never set here.
* **The email goes only from a person's click.** :func:`send_request` is the single caller of
  ``frappe.sendmail`` and is reached only through the POST endpoint
  ``api.supplier_returns.send_request``. Saving, a doc_event and the daily sync never send.
* **The doc_events never raise.** A Supplier Return that failed to refresh is a stale label; a
  hook that raised would be a Purchase Receipt or a payment that cannot be submitted.
* **Money typed by hand says so.** ``money_source`` is "Typed by hand" until a submitted Debit Note
  or Payment Entry is linked; from then on the outcome and the amount are read from that document
  on every sync and cannot be typed (``supplier_return_rules.check_typed_money``).
* **A payment is counted once, and a return can hold several.** Each refund is a row of
  ``payments`` holding *this return's share* of a Payment Entry
  (``supplier_return_rules.split_payment`` and ``by_hand_share``), so one payment covering two
  returns is never 100 on each, and a refund that arrives in parts is two rows. What a person
  types is a row too, without a Payment Entry.
* **A Debit Note this module drafts lowers the bill when the bill can take it.** ERPNext 16.54.0
  books a debit note against itself by default (*Update Outstanding for Self*); see
  :func:`make_debit_note` for why that box is turned off when the bill's outstanding covers the
  note, and left on when it does not.
* **A return that comes back into play is judged again.** While Denied or Canceled its quantities
  were free; reopening it re-runs the quantity limit.
* **A canceled receipt never traps a return.** ERPNext lets the receipt be canceled under a
  return that is not finished. The ways out (cancel, deny, close, and recording what came back)
  stay open, with a plain message on everything else.
* **Reads are safe before the DocType exists.** :func:`returned_lines` is called by other modules'
  reports, which may deploy first.

Tabs, per ``CLAUDE.md`` (new file).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_fullname, getdate, now_datetime, nowdate

from erpnext_enhancements.inventory_enhancements import supplier_return_rules as rules

DOCTYPE = "Supplier Return"
CHILD = "Supplier Return Item"
PRINT_FORMAT = "Supplier Return - Sapphire"

#: Fields only the actions set. A form save or an API write that changes one is put back.
ACTION_FIELDS = (
	"status",
	"status_changed_on",
	"requested_on",
	"requested_how",
	"requested_by",
	"responded_on",
	"sent_back_on",
	"purchase_return",
	"replacement_receipt",
	"amount_received",
	"money_received_on",
	"money_reference",
	"money_source",
	"debit_note",
	"closed_on",
	"money_recorded_by",
	"outcome_notes",
	"money_settled",
)

#: The child table of refunds and credits. Its rows are written by the actions and the sync.
PAYMENT_CHILD = "Supplier Return Payment"

#: The steps that stay open when the receipt a return names has been canceled: the ways out.
WAYS_OUT = ("cancel", "close", "deny")

#: The line fields that say *what* is going back. Locked once the return leaves Draft.
LINE_IDENTITY = ("purchase_receipt_item", "item_code", "qty")

#: :func:`linked_to`'s role for the receipt the items came in on. It is not a document the return
#: waits on, so its submit and cancel do not sync the return.
ORIGINAL_ROLE = "returned on"

#: Which of the return's link fields can name a document of each standard doctype.
LINK_FIELDS = {
	"Purchase Receipt": ("purchase_return", "replacement_receipt"),
	"Purchase Invoice": ("debit_note",),
}

RECEIPT_LINE_FIELDS = [
	"name",
	"parent",
	"idx",
	"item_code",
	"item_name",
	"qty",
	"uom",
	"conversion_factor",
	"rate",
	"project",
	"warehouse",
	"purchase_order",
	"purchase_order_item",
	"supplier_part_no",
]


def _throw(error):
	frappe.throw(_(str(error)))


def installed():
	"""Whether the Supplier Return table exists. False, never an exception, on a site without it."""
	try:
		return bool(frappe.db.table_exists(DOCTYPE))
	except Exception:
		return False


# ---------------------------------------------------------------------------
# For other modules. The name, arguments and keys are a contract: do not change them.
# ---------------------------------------------------------------------------


def returned_lines(purchase_order_items=None, project=None):
	"""Supplier Return lines for some purchase order lines, a project, or both.

	``purchase_order_items``: an iterable of Purchase Order Item names. ``project``: a Project name.
	Give either or both (both narrows); neither gives ``[]``.

	Returns one dict per Supplier Return line with the keys in
	``supplier_return_rules.RETURNED_LINE_KEYS``: ``supplier_return``, ``status``, ``supplier``,
	``purchase_order``, ``purchase_order_item``, ``purchase_receipt``, ``purchase_receipt_item``,
	``item_code``, ``project``, ``qty`` (purchase unit), ``uom``, ``stock_qty``, ``amount``,
	``gone`` (True from Sent Back on: the goods have left) and ``open`` (True until a final status).
	Canceled and Denied returns are left out. Never raises; ``[]`` when the DocType is not
	installed.
	"""
	try:
		# One name given as a string is one name, not its letters.
		order_lines = rules.as_names(purchase_order_items)
		if not (order_lines or project) or not installed():
			return []
		conditions, values = ["p.status not in %(skip)s"], {"skip": tuple(sorted(rules.NOT_COUNTED))}
		if order_lines:
			conditions.append("c.purchase_order_item in %(order_lines)s")
			values["order_lines"] = tuple(order_lines)
		if project:
			conditions.append("c.project = %(project)s")
			values["project"] = project
		rows = frappe.db.sql(
			f"""
			select p.name as supplier_return, p.status, p.supplier, p.purchase_receipt,
				c.purchase_order, c.purchase_order_item, c.purchase_receipt_item, c.item_code,
				c.project, c.qty, c.uom, c.conversion_factor, c.amount
			from `tab{CHILD}` c
			join `tab{DOCTYPE}` p on p.name = c.parent
			where {" and ".join(conditions)}
			order by p.creation asc, c.idx asc
			""",
			values,
			as_dict=True,
		)
		return [line for line in (rules.returned_line(row) for row in rows) if line]
	except Exception:
		frappe.log_error(title="Supplier Return: returned_lines", message=frappe.get_traceback())
		return []


# ---------------------------------------------------------------------------
# The receipt's lines, and how much of each may still go back
# ---------------------------------------------------------------------------


def _stock_items(codes):
	codes = sorted({code for code in codes if code})
	if not codes:
		return set()
	return set(frappe.get_all("Item", filters={"name": ("in", codes), "is_stock_item": 1}, pluck="name"))


def _lines_with_stock(line_names):
	"""Which receipt lines wrote a stock ledger entry: the Maintain Stock guard's own test
	(``maintain_stock._receipt_lines_with_stock``), restated so the two cannot disagree."""
	line_names = sorted({name for name in line_names if name})
	if not line_names:
		return set()
	return set(
		frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": "Purchase Receipt", "voucher_detail_no": ("in", line_names)},
			pluck="voucher_detail_no",
		)
	)


def _claimed_elsewhere(line_names, excluding=None):
	"""``{receipt line: quantity}`` on other Supplier Returns that still stand."""
	if not line_names or not installed():
		return {}
	rows = frappe.db.sql(
		f"""
		select c.purchase_receipt_item, sum(c.qty)
		from `tab{CHILD}` c
		join `tab{DOCTYPE}` p on p.name = c.parent
		where c.purchase_receipt_item in %(lines)s
			and p.status not in %(skip)s
			and p.name != %(excluding)s
		group by c.purchase_receipt_item
		""",
		{"lines": tuple(line_names), "skip": tuple(sorted(rules.NOT_COUNTED)), "excluding": excluding or ""},
	)
	return {name: flt(qty) for name, qty in rows}


def _return_rows(receipt, parents=None, submitted_only=True):
	"""Purchase Return rows against ``receipt``: ``[(receipt line, return, quantity)]``.

	The quantity is positive and in the **receipt line's** unit: a return built by hand in another
	unit is converted through its stock quantity, so 1 Box of 12 is not read as 1 of 12.
	"""
	conditions = [
		"p.is_return = 1",
		"p.return_against = %(receipt)s",
		"ifnull(c.purchase_receipt_item, '') != ''",
		"p.docstatus = 1" if submitted_only else "p.docstatus < 2",
	]
	values = {"receipt": receipt}
	if parents:
		conditions.append("c.parent in %(parents)s")
		values["parents"] = tuple(parents)
	rows = frappe.db.sql(
		f"""
		select c.purchase_receipt_item, c.parent, c.qty, c.stock_qty, r.conversion_factor
		from `tabPurchase Receipt Item` c
		join `tabPurchase Receipt` p on p.name = c.parent
		left join `tabPurchase Receipt Item` r on r.name = c.purchase_receipt_item
		where {" and ".join(conditions)}
		""",
		values,
	)
	out = []
	for line, parent, qty, stock_qty, factor in rows:
		factor = flt(factor) or 1.0
		out.append((line, parent, abs(flt(stock_qty)) / factor if flt(stock_qty) else abs(flt(qty))))
	return out


def _returned_outside(receipt, line_names):
	"""``{receipt line: quantity}`` returned through ERPNext that no Supplier Return accounts for
	(``supplier_return_rules.returned_outside``): a return nobody links, in full, and whatever a
	linked return took beyond what its Supplier Return says."""
	wanted = set(line_names or [])
	if not wanted:
		return {}
	rows = [row for row in _return_rows(receipt) if row[0] in wanted]
	linked = {}
	if rows and installed():
		for parent, line, qty in frappe.db.sql(
			f"""
			select p.purchase_return, c.purchase_receipt_item, sum(c.qty)
			from `tab{CHILD}` c
			join `tab{DOCTYPE}` p on p.name = c.parent
			where p.purchase_receipt = %(receipt)s
				and p.status not in %(skip)s
				and ifnull(p.purchase_return, '') != ''
				and ifnull(c.line_kind, '') != %(record_only)s
			group by p.purchase_return, c.purchase_receipt_item
			""",
			{
				"receipt": receipt,
				"skip": tuple(sorted(rules.NOT_COUNTED)),
				"record_only": rules.RECORD_ONLY,
			},
		):
			linked.setdefault(parent, {})[line] = flt(qty)
	return rules.returned_outside(rows, linked)


def receipt_facts(receipt, excluding=None):
	"""Every line of a receipt with its kind and how much may still go back, in receipt order.

	``excluding`` is the Supplier Return being saved, whose own lines must not count against it.
	"""
	lines = frappe.get_all(
		"Purchase Receipt Item",
		filters={"parent": receipt, "parenttype": "Purchase Receipt"},
		fields=RECEIPT_LINE_FIELDS,
		order_by="idx asc",
	)
	names = [line.name for line in lines]
	stock_items = _stock_items(line.item_code for line in lines)
	with_stock = _lines_with_stock(names)
	claimed = _claimed_elsewhere(names, excluding)
	outside = _returned_outside(receipt, names)
	facts = []
	for line in lines:
		received = flt(line.qty)
		already = flt(claimed.get(line.name)) + flt(outside.get(line.name))
		facts.append(
			{
				"purchase_receipt_item": line.name,
				"idx": line.idx,
				"item_code": line.item_code,
				"item_name": line.item_name,
				"uom": line.uom,
				"conversion_factor": flt(line.conversion_factor) or 1.0,
				"rate": flt(line.rate),
				"project": line.project,
				"warehouse": line.warehouse,
				"purchase_order": line.purchase_order,
				"purchase_order_item": line.purchase_order_item,
				"supplier_part_no": line.supplier_part_no,
				"received_qty": received,
				"already_returned_qty": min(already, received),
				"returnable": rules.returnable_qty(received, claimed.get(line.name), outside.get(line.name)),
				"line_kind": rules.line_kind(line.item_code in stock_items, line.name in with_stock),
			}
		)
	return facts


def _receipt_header(receipt, live=True):
	"""The receipt's header. With ``live``, refuses one that is not submitted."""
	if not receipt:
		frappe.throw(_("Which Purchase Receipt? None was named."))
	header = frappe.db.get_value(
		"Purchase Receipt",
		receipt,
		[
			"name",
			"docstatus",
			"is_return",
			"supplier",
			"company",
			"posting_date",
			"net_total",
			"total_taxes_and_charges",
		],
		as_dict=True,
	)
	if not header:
		frappe.throw(_("Purchase Receipt {0} doesn't exist.").format(receipt), frappe.DoesNotExistError)
	if cint(header.is_return):
		frappe.throw(
			_("{0} is itself a return. Start from the receipt the items came in on.").format(receipt)
		)
	if live and cint(header.docstatus) == 2:
		frappe.throw(_("Purchase Receipt {0} is canceled, so nothing on it can be returned.").format(receipt))
	if live and cint(header.docstatus) != 1:
		frappe.throw(
			_("Purchase Receipt {0} is not submitted, so nothing has been received on it yet.").format(
				receipt
			)
		)
	return header


def receipt_is_live(receipt):
	"""Whether the receipt a return names is still submitted. ERPNext lets a receipt be canceled
	while a Supplier Return names it (a Supplier Return is never submitted, so it does not block)."""
	return cint(frappe.db.get_value("Purchase Receipt", receipt, "docstatus") or 0) == 1 if receipt else False


def _receipt_gone(receipt):
	return _(
		"Purchase Receipt {0} was canceled, so this return can't go any further. Cancel the return, or, if the items already went back, close it without credit or record what came back."
	).format(receipt)


def _supplier_email(supplier):
	"""The supplier's best address: its primary contact's, else its own. ``""`` when none."""
	try:
		row = frappe.db.get_value(
			"Supplier", supplier, ["email_id", "supplier_primary_contact"], as_dict=True
		)
		if not row:
			return ""
		if row.supplier_primary_contact:
			email = frappe.db.get_value("Contact", row.supplier_primary_contact, "email_id")
			if email:
				return email
		return row.email_id or ""
	except Exception:
		return ""


def build_from_receipt(receipt, only_lines=None, purchase_order=None, item_code=None):
	"""An unsaved Supplier Return for a receipt, with a line for everything that may still go back.

	``only_lines`` (receipt line names), ``purchase_order`` and ``item_code`` each narrow it. The
	person trims quantities and picks a reason per line; nothing is saved here.
	"""
	header = _receipt_header(receipt)
	frappe.has_permission("Purchase Receipt", "read", doc=receipt, throw=True)
	only = {name for name in (only_lines or []) if name}
	facts = [
		fact
		for fact in receipt_facts(receipt)
		if (not only or fact["purchase_receipt_item"] in only)
		and (not purchase_order or fact["purchase_order"] == purchase_order)
		and (not item_code or fact["item_code"] == item_code)
	]
	open_facts = [fact for fact in facts if fact["returnable"] > 0]
	if not open_facts:
		frappe.throw(
			_(
				"Nothing on {0} can be returned: every matching line has already been returned or is on another Supplier Return."
			).format(receipt)
			if facts
			else _("No line of {0} matches.").format(receipt)
		)
	doc = frappe.new_doc(DOCTYPE)
	doc.supplier = header.supplier
	doc.purchase_receipt = receipt
	doc.company = header.company
	doc.posting_date = nowdate()
	doc.status = rules.DRAFT
	doc.requested_resolution = doc.get("requested_resolution") or rules.REFUND
	doc.contact_email = _supplier_email(header.supplier)
	for fact in open_facts:
		# The mapper's way of making a child row: it carries a temporary name and ``__islocal``,
		# which is what lets the Desk open the unsaved document.
		row = frappe.new_doc(CHILD, parent_doc=doc, parentfield="items")
		_fill_line(row, fact)
		row.qty = fact["returnable"]
		doc.append("items", row)
	# Tax to Be Refunded is left at zero here and worked out at the first save, from the
	# quantities the person kept rather than from everything that could go back.
	_set_totals(doc, header)
	_set_header_links(doc)
	return doc


def _fill_line(row, fact):
	for field in (
		"purchase_receipt_item",
		"item_code",
		"item_name",
		"uom",
		"conversion_factor",
		"rate",
		"project",
		"warehouse",
		"purchase_order",
		"purchase_order_item",
		"line_kind",
		"received_qty",
		"already_returned_qty",
	):
		row.set(field, fact.get(field))


def _set_header_links(doc):
	orders = {row.purchase_order for row in doc.items if row.purchase_order}
	projects = {row.project for row in doc.items if row.project}
	doc.purchase_order = orders.pop() if len(orders) == 1 else None
	doc.project = projects.pop() if len(projects) == 1 else None


def _tax_share(header, rows):
	net = sum(flt(row.qty) * flt(row.rate) for row in rows)
	return rules.tax_share(header.total_taxes_and_charges, header.net_total, net)


def _tax_is_untouched(doc, before, header):
	"""Whether Tax to Be Refunded is still the figure this module worked out, so a change to the
	lines may work it out again. A figure the person typed is theirs and stays."""
	if not before:
		return not flt(doc.tax_amount)
	if abs(flt(doc.tax_amount) - flt(before.tax_amount)) > 0.005:
		return False
	return abs(flt(before.tax_amount) - _tax_share(header, before.items)) <= 0.005


def _set_totals(doc, header=None, fresh_tax=False):
	for row in doc.items:
		row.amount = flt(flt(row.qty) * flt(row.rate), 2)
	lines = [{"qty": row.qty, "rate": row.rate} for row in doc.items]
	if fresh_tax and header:
		doc.tax_amount = _tax_share(header, doc.items)
	answer = rules.totals(
		lines, doc.tax_amount, doc.restocking_fee, doc.return_shipping_cost, cint(doc.shipping_deducted)
	)
	doc.total_amount = answer["total_amount"]
	doc.expected_amount = answer["expected_amount"]


# ---------------------------------------------------------------------------
# validate (the controller calls these)
# ---------------------------------------------------------------------------


def validate(doc):
	"""Everything a save checks. The controller's ``validate``."""
	before = doc.get_doc_before_save() if not doc.is_new() else None
	by_action = bool(doc.flags.get("ee_action"))
	_protect_action_fields(doc, before)
	# The controller's validate runs before Frappe's own mandatory check, and a read by a blank
	# name would answer with some other receipt.
	if not doc.purchase_receipt or not doc.supplier:
		frappe.throw(_("Pick the supplier and the Purchase Receipt the items came in on."))
	header = _receipt_header(doc.purchase_receipt, live=False)
	if cint(header.docstatus) != 1:
		# The receipt was canceled under the return. Nothing more is read from it: the lines keep
		# what they held, and only a step may save (the ways out, and the sync). ``_advance``
		# decides which steps; a plain save is told why.
		if before is None:
			# A new return: the receipt must be a submitted one (this says which it is not).
			_receipt_header(doc.purchase_receipt)
		if not by_action:
			frappe.throw(_receipt_gone(doc.purchase_receipt))
		_set_totals(doc)
		_set_derived(doc)
		return
	if header.supplier != doc.supplier:
		frappe.throw(
			_("Purchase Receipt {0} is from {1}, not {2}.").format(
				doc.purchase_receipt, header.supplier, doc.supplier
			)
		)
	doc.company = header.company
	if getdate(doc.posting_date) < getdate(header.posting_date):
		frappe.throw(_("The return can't be dated before the receipt ({0}).").format(header.posting_date))
	if before and not by_action:
		_refuse_locked_changes(doc, before)
	facts = {fact["purchase_receipt_item"]: fact for fact in receipt_facts(doc.purchase_receipt, doc.name)}
	draft = doc.status == rules.DRAFT
	if before and before.status != rules.DRAFT:
		_refuse_line_changes(doc, before)
	for row in doc.items:
		fact = facts.get(row.purchase_receipt_item)
		if not fact:
			frappe.throw(
				_(
					"Row {0}: this line is not from Purchase Receipt {1}. Add lines with Get Items From Receipt so each one names the receipt line it returns."
				).format(row.idx, doc.purchase_receipt)
			)
		qty, reason, notes = row.qty, row.reason, row.condition_notes
		_fill_line(row, fact)
		row.qty, row.reason, row.condition_notes = qty, reason, notes
	# The quantities are judged while they can change (Draft), and again whenever a return that
	# did not count comes back into play: while it was Denied or Canceled its lines were free for
	# another return to take.
	back_in_play = bool(before and before.status in rules.NOT_COUNTED and doc.status not in rules.NOT_COUNTED)
	if draft or back_in_play:
		problems = _quantity_problems(doc, facts)
		if problems:
			frappe.throw("<br>".join(frappe.utils.escape_html(problem) for problem in problems))
	if doc.requested_resolution not in rules.RESOLUTIONS:
		frappe.throw(_("Say what we are asking for: a refund, a credit on account or a replacement."))
	for field in ("tax_amount", "restocking_fee", "return_shipping_cost"):
		if flt(doc.get(field)) < 0:
			frappe.throw(_("{0} can't be negative.").format(doc.meta.get_label(field)))
	if doc.status == rules.SENT_BACK and not by_action:
		# How it went back stays correctable while the return waits, and stays complete.
		try:
			rules.check_send_back(doc.return_method, doc.carrier, doc.tracking_number)
		except rules.RuleError as error:
			_throw(error)
	_set_totals(doc, header, fresh_tax=draft and _tax_is_untouched(doc, before, header))
	_set_header_links(doc)
	_set_derived(doc)


def _quantity_problems(doc, facts=None):
	"""``supplier_return_rules.quantity_problems`` for this return's lines against what its
	receipt has left, this return's own lines not counted against it."""
	if facts is None:
		facts = {
			fact["purchase_receipt_item"]: fact for fact in receipt_facts(doc.purchase_receipt, doc.name)
		}
	return rules.quantity_problems(
		[
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"qty": row.qty,
				"returnable": (facts.get(row.purchase_receipt_item) or {}).get("returnable"),
				"received": row.received_qty,
				"reason": row.reason,
				"condition_notes": row.condition_notes,
				"purchase_receipt_item": row.purchase_receipt_item,
			}
			for row in doc.items
		]
	)


def _same(a, b):
	"""Whether two field values are the same thing, whatever type the form sent them as."""
	if a in (None, "") and b in (None, ""):
		return True
	try:
		return abs(float(a or 0) - float(b or 0)) < 0.005
	except (TypeError, ValueError):
		return str(a or "") == str(b or "")


def _refuse_locked_changes(doc, before):
	"""A finished return's fees, shipping and request are part of its outcome
	(``supplier_return_rules.LOCKED_WHEN_FINAL``): a plain save may not change them."""
	for field in rules.locked_fields(before.status):
		if not _same(doc.get(field), before.get(field)):
			frappe.throw(
				_(
					"This return is {0}, so {1} can't be changed. A Purchase Manager or an Accounts Manager can reopen it."
				).format(before.status, doc.meta.get_label(field))
			)


def _payment_signature(doc):
	return sorted(
		(
			row.get("payment_entry") or "",
			round(flt(row.get("amount")), 2),
			str(row.get("received_on") or "")[:10],
			row.get("reference") or "",
			row.get("kind") or "",
		)
		for row in doc.get("payments") or []
	)


def _protect_action_fields(doc, before):
	"""Put back any field only an action may set, unless an action is what is saving."""
	if doc.flags.get("ee_action"):
		return
	if not before:
		for field in ACTION_FIELDS:
			doc.set(field, None)
		doc.set("payments", [])
		doc.status = rules.DRAFT
		doc.status_changed_on = now_datetime()
		return
	for field in ACTION_FIELDS:
		doc.set(field, before.get(field))
	if _payment_signature(doc) != _payment_signature(before):
		frappe.throw(
			_("Refunds and credits are recorded with the buttons at the top of the form, not in the table.")
		)


def _refuse_line_changes(doc, before):
	"""Once the request has gone, what is being returned may not change under the supplier."""
	if doc.supplier != before.supplier or doc.purchase_receipt != before.purchase_receipt:
		frappe.throw(_("The supplier and the receipt can't be changed after the return has left Draft."))
	now = [(row.purchase_receipt_item, row.item_code, flt(row.qty)) for row in doc.items]
	was = [(row.purchase_receipt_item, row.item_code, flt(row.qty)) for row in before.items]
	if sorted(now, key=str) != sorted(was, key=str):
		frappe.throw(
			_(
				"The items and quantities can't be changed after the return has left Draft: the supplier was asked about these. Cancel this return and start a new one."
			)
		)


def _plan(doc):
	return rules.stock_plan(
		[{"item_code": row.item_code, "line_kind": row.line_kind, "qty": row.qty} for row in doc.items],
		doc.purchase_receipt,
	)


def _document(doctype, name, fields):
	if not name:
		return None
	return frappe.db.get_value(doctype, name, ["name", "docstatus", *fields], as_dict=True)


def _set_derived(doc):
	"""The fields that are read off others on every save: stock, waiting, the difference."""
	plan = _plan(doc)
	doc.record_only_note = plan["note"]
	linked = _document("Purchase Receipt", doc.purchase_return, [])
	state = rules.stock_state(doc.status, plan, linked)
	if linked and cint(linked.docstatus) == 1:
		# Somebody may have changed the draft before submitting it. Say so rather than let the
		# record claim quantities ERPNext did not take.
		taken = {}
		for line, _parent, qty in _return_rows(doc.purchase_receipt, parents=[linked.name]):
			taken[line] = taken.get(line, 0.0) + qty
		labels = {row.purchase_receipt_item: row.item_code for row in doc.items}
		mismatch = rules.return_mismatch(_wanted(plan, doc), taken, labels)
		if mismatch:
			state = f"{state} {mismatch}"
	doc.stock_state = state
	doc.waiting_on_money = cint(rules.waiting_on_money(doc.status, doc.requested_resolution))
	if doc.status in (rules.REFUND_RECEIVED, rules.CREDIT_RECEIVED, rules.CLOSED_NO_CREDIT):
		doc.amount_difference = rules.difference(doc.expected_amount, doc.amount_received)
	else:
		doc.amount_difference = 0


def refuse_delete(doc):
	"""Only a return that never went anywhere may be deleted. The controller's ``on_trash``."""
	if doc.status not in (rules.DRAFT, rules.CANCELED):
		frappe.throw(
			_("A return that is {0} is part of the record and can't be deleted. Cancel it instead.").format(
				doc.status
			)
		)
	for field in ("purchase_return", "debit_note"):
		if doc.get(field):
			frappe.throw(_("Remove {0} first.").format(doc.get(field)))


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


def _roles():
	return set(frappe.get_roles())


def load_for(name, action):
	"""The return, once the caller may write it and may take ``action``. Refuses otherwise."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	if not rules.may(action, _roles()):
		frappe.throw(
			_("You don't have a role that may do this on a Supplier Return."), frappe.PermissionError
		)
	return doc


def _save(doc, comment=None):
	doc.flags.ee_action = True
	doc.save()
	if comment:
		doc.add_comment("Info", comment)
	return doc


def _advance(doc, action):
	if action not in WAYS_OUT and not receipt_is_live(doc.purchase_receipt):
		frappe.throw(_receipt_gone(doc.purchase_receipt))
	try:
		doc.status = rules.next_status(action, doc.status)
	except rules.RuleError as error:
		_throw(error)
	doc.status_changed_on = now_datetime()
	if doc.status in rules.FINAL:
		doc.closed_on = nowdate()


def _who():
	return frappe.utils.escape_html(get_fullname(frappe.session.user) or frappe.session.user)


def _esc(value):
	return frappe.utils.escape_html(str(value or ""))


def mark_requested(name, how, notes=None, action_done=False):
	"""Requested: the supplier has been asked. ``how`` is one of ``REQUEST_WAYS``.

	"Email" is only ever passed by :func:`send_request`, after the email is queued.
	"""
	doc = load_for(name, "request")
	if how not in rules.REQUEST_WAYS or (how == "Email" and not action_done):
		frappe.throw(_("Say how the supplier was asked: by phone, in person or on their website."))
	_advance(doc, "request")
	doc.requested_on = now_datetime()
	doc.requested_how = how
	doc.requested_by = frappe.session.user
	if notes and how != "Email":
		doc.supplier_response_notes = notes
	return _save(doc, None if how == "Email" else f"{_who()} asked the supplier ({_esc(how).lower()}).")


def record_approval(name, supplier_return_number=None, notes=None):
	doc = load_for(name, "approve")
	_advance(doc, "approve")
	doc.responded_on = now_datetime()
	if supplier_return_number:
		doc.supplier_return_number = supplier_return_number.strip()
	if notes:
		doc.supplier_response_notes = notes
	number = f" Return number {_esc(doc.supplier_return_number)}." if doc.supplier_return_number else ""
	return _save(doc, f"{_who()} recorded that the supplier approved the return.{number}")


def record_denial(name, notes):
	doc = load_for(name, "deny")
	if not (notes or "").strip():
		frappe.throw(_("Say what the supplier said, so the next person knows why it was denied."))
	_advance(doc, "deny")
	doc.responded_on = now_datetime()
	doc.supplier_response_notes = notes.strip()
	return _save(doc, f"{_who()} recorded that the supplier denied the return: {_esc(notes)}")


def mark_sent_back(name, return_method, carrier=None, tracking_number=None, sent_back_on=None):
	"""Sent Back: the items have left. Returns the return and what its stock side now needs."""
	doc = load_for(name, "send_back")
	try:
		rules.check_send_back(return_method, carrier, tracking_number)
	except rules.RuleError as error:
		_throw(error)
	sent = getdate(sent_back_on or nowdate())
	if sent > getdate(nowdate()):
		frappe.throw(_("The date it went back can't be in the future."))
	if sent < getdate(doc.posting_date):
		frappe.throw(
			_("It can't have gone back before the return was started ({0}).").format(doc.posting_date)
		)
	_advance(doc, "send_back")
	doc.return_method = return_method
	doc.carrier = (carrier or "").strip() if return_method == rules.CARRIER else None
	doc.tracking_number = (tracking_number or "").strip() if return_method == rules.CARRIER else None
	doc.sent_back_on = sent
	how = (
		f"{_esc(doc.carrier)}, tracking {_esc(doc.tracking_number)}"
		if doc.carrier
		else _esc(return_method).lower()
	)
	_save(doc, f"{_who()} marked it sent back ({how}).")
	plan = _plan(doc)
	return {
		"name": doc.name,
		"needs_purchase_return": plan["needs_purchase_return"],
		"takes_stock": plan["takes_stock"],
		"note": plan["note"],
	}


def _money_documents(doc, settled=None, with_typed=True):
	"""The Debit Note, the payments and the typed entries as
	``supplier_return_rules.money_from_documents`` reads them."""
	note = _document(
		"Purchase Invoice", doc.debit_note, ["grand_total", "outstanding_amount", "posting_date", "bill_no"]
	)
	rows = list(doc.get("payments") or [])
	return rules.money_from_documents(
		debit_note=note
		and {
			"name": note.name,
			"docstatus": note.docstatus,
			"amount": abs(flt(note.grand_total)),
			# A debit note the supplier still owes us on carries a negative outstanding amount.
			"owed": abs(min(flt(note.outstanding_amount), 0.0)),
			"posting_date": note.posting_date,
			"reference": note.bill_no,
		},
		payments=[
			{
				"name": row.get("payment_entry"),
				"amount": flt(row.get("amount")),
				"posting_date": row.get("received_on"),
				"reference": row.get("reference"),
			}
			for row in rows
			if row.get("payment_entry")
		],
		typed=[
			{
				"amount": flt(row.get("amount")),
				"date": row.get("received_on"),
				"reference": row.get("reference"),
				"kind": "refund" if row.get("kind") == "Refund" else "credit",
			}
			for row in rows
			if with_typed and not row.get("payment_entry")
		],
		expected=doc.expected_amount,
		settled=cint(doc.get("money_settled")) if settled is None else settled,
	)


def _apply_money(doc, notes=None):
	"""Set the outcome fields, and the status where the money decides it, from the Debit Note,
	the payments and the typed entries. Returns what ``money_from_documents`` said."""
	notes = notes if notes is not None else []
	money = _money_documents(doc)
	doc.amount_received = money["amount"] if money["amount"] or doc.status == rules.CLOSED_NO_CREDIT else None
	doc.money_received_on = money["date"]
	doc.money_reference = money["reference"] or None
	doc.money_source = money["source"] or None
	target = money["status"]
	if target and doc.status in rules.GONE and doc.status != target:
		doc.status = target
		doc.status_changed_on = now_datetime()
		doc.closed_on = getdate(money["date"]) if money["date"] else nowdate()
		notes.append(f"{target}: {frappe.utils.fmt_money(money['amount'])}. {_esc(money['note'])}")
	elif not target and doc.status in (rules.REFUND_RECEIVED, rules.CREDIT_RECEIVED):
		# What made it final no longer stands: a document was canceled, or the supplier owes on
		# the note again.
		doc.status = rules.SENT_BACK
		doc.status_changed_on = now_datetime()
		doc.closed_on = None
		notes.append("What finished this return no longer stands, so it is waiting on money again.")
	return money


def record_money(name, kind, amount, received_on, reference, notes=None, more_expected=False):
	"""A refund or a credit typed by the person who saw it. Refused once a document is linked.

	``more_expected``: this is one part and the rest is still coming, so the return stays Sent
	Back with the part recorded. Without it the entry finishes the return.
	"""
	action = kind if kind in ("refund", "credit") else "refund"
	doc = load_for(name, action)
	if not receipt_is_live(doc.purchase_receipt) and doc.status != rules.SENT_BACK:
		frappe.throw(_receipt_gone(doc.purchase_receipt))
	try:
		rules.next_status(action, doc.status)
		typed = rules.check_typed_money(
			kind, amount, received_on, reference, nowdate(), _money_documents(doc)
		)
	except rules.RuleError as error:
		_throw(error)
	doc.append(
		"payments",
		{
			"received_on": typed["date"],
			"kind": "Refund" if kind == "refund" else "Credit",
			"amount": typed["amount"],
			"reference": typed["reference"],
			"recorded_by": frappe.session.user,
		},
	)
	if not cint(more_expected):
		doc.money_settled = 1
	doc.money_recorded_by = frappe.session.user
	if (notes or "").strip():
		doc.outcome_notes = notes.strip()
	_apply_money(doc)
	what = "a refund" if kind == "refund" else "a credit"
	more = " More is still expected." if cint(more_expected) else ""
	return _save(
		doc,
		f"{_who()} recorded {what} of {frappe.utils.fmt_money(typed['amount'])} "
		f"(reference {_esc(typed['reference'])}). {rules.NOT_BACKED}{more}",
	)


def settle(name, reason=None):
	"""No More Is Coming: finish a return at what has come back so far."""
	doc = load_for(name, "settle")
	if doc.status != rules.SENT_BACK:
		frappe.throw(_("Only a return that is Sent Back and waiting on money can be finished this way."))
	money = _money_documents(doc)
	try:
		rules.check_settle(money["amount"], money)
	except rules.RuleError as error:
		_throw(error)
	doc.money_settled = 1
	doc.money_recorded_by = frappe.session.user
	if (reason or "").strip():
		doc.outcome_notes = reason.strip()
	_apply_money(doc)
	if doc.status == rules.SENT_BACK:
		frappe.throw(_("This return can't be finished yet: {0}").format(money["note"]))
	return _save(
		doc,
		f"{_who()} said no more is coming: finished at {frappe.utils.fmt_money(doc.amount_received)} "
		f"of {frappe.utils.fmt_money(doc.expected_amount)} expected.",
	)


def _replacement_receipt(doc, receipt):
	header = frappe.db.get_value(
		"Purchase Receipt", receipt, ["docstatus", "is_return", "supplier", "posting_date"], as_dict=True
	)
	if not header or cint(header.docstatus) != 1 or cint(header.is_return):
		frappe.throw(_("{0} is not a submitted Purchase Receipt.").format(receipt))
	if header.supplier != doc.supplier:
		frappe.throw(_("{0} is from a different supplier.").format(receipt))
	if receipt == doc.purchase_receipt:
		frappe.throw(_("{0} is the receipt the items came in on, not the replacement.").format(receipt))
	items = {row.item_code for row in doc.items}
	if not frappe.db.exists("Purchase Receipt Item", {"parent": receipt, "item_code": ("in", sorted(items))}):
		frappe.throw(_("{0} has none of the items on this return.").format(receipt))
	return header


def record_replacement(name, replacement_receipt=None, received_on=None, notes=None):
	"""Replacement Received: the new receipt against the same order line, or a dated note."""
	doc = load_for(name, "replacement")
	if replacement_receipt:
		header = _replacement_receipt(doc, replacement_receipt)
		received_on = header.posting_date
	elif not (received_on and (notes or "").strip()):
		frappe.throw(
			_(
				"Pick the Purchase Receipt the replacement came in on. If there is none (a record-only return), enter the date and a note."
			)
		)
	_advance(doc, "replacement")
	doc.replacement_receipt = replacement_receipt or None
	doc.money_received_on = getdate(received_on)
	doc.money_recorded_by = frappe.session.user
	doc.outcome_notes = (notes or "").strip() or None
	where = f" on {_esc(replacement_receipt)}" if replacement_receipt else ""
	return _save(doc, f"{_who()} recorded the replacement received{where}.")


def close_without_credit(name, reason):
	doc = load_for(name, "close")
	if not (reason or "").strip():
		frappe.throw(_("Say why nothing is coming back."))
	money = _money_documents(doc)
	if money["backed"] or flt(money["amount"]) > 0.005:
		frappe.throw(
			_(
				"Something did come back on this return, or a Debit Note or Payment Entry is linked, so it can't be closed without credit. Use No More Is Coming to finish it at what came back."
			)
		)
	_advance(doc, "close")
	doc.amount_received = 0
	doc.money_recorded_by = frappe.session.user
	doc.outcome_notes = reason.strip()
	return _save(doc, f"{_who()} closed it without credit: {_esc(reason)}")


def cancel_return(name, reason):
	doc = load_for(name, "cancel")
	if not (reason or "").strip():
		frappe.throw(_("Say why the return is canceled."))
	live = _document("Purchase Receipt", doc.purchase_return, [])
	if live and cint(live.docstatus) != 2:
		frappe.throw(_("Purchase Return {0} is linked. Cancel or delete it first.").format(live.name))
	_advance(doc, "cancel")
	doc.outcome_notes = reason.strip()
	return _save(doc, f"{_who()} canceled the return: {_esc(reason)}")


def reopen(name, reason):
	"""Undo a step recorded by mistake. A document-backed outcome is undone on its document.

	A return that was Denied or Canceled did not count while it sat there, so another return may
	have taken its quantities: they are judged again before it comes back.
	"""
	doc = load_for(name, "reopen")
	if not (reason or "").strip():
		frappe.throw(_("Say why it is reopened."))
	was = doc.status
	if was in (rules.REFUND_RECEIVED, rules.CREDIT_RECEIVED):
		documents = _money_documents(doc, settled=False, with_typed=False)
		if documents["status"]:
			frappe.throw(
				_(
					"The outcome comes from a {0}. Cancel that document and this return reopens by itself."
				).format(documents["source"])
			)
	try:
		target = rules.next_status("reopen", doc.status)
	except rules.RuleError as error:
		_throw(error)
	if was in rules.NOT_COUNTED:
		if not receipt_is_live(doc.purchase_receipt):
			frappe.throw(_receipt_gone(doc.purchase_receipt))
		problems = _quantity_problems(doc)
		if problems:
			frappe.throw(
				_(
					"This return can't be reopened. While it was {0}, its items were free to go on another return:"
				).format(was)
				+ "<br>"
				+ "<br>".join(frappe.utils.escape_html(problem) for problem in problems)
			)
	doc.status = target
	doc.status_changed_on = now_datetime()
	doc.closed_on = None
	doc.money_settled = 0
	# What a person typed is what is undone. A payment's share stays: it is the payment's.
	doc.set("payments", [row for row in doc.get("payments") or [] if row.get("payment_entry")])
	for field in ("money_recorded_by", "outcome_notes", "replacement_receipt"):
		doc.set(field, None)
	_apply_money(doc)
	return _save(doc, f"{_who()} reopened it from {_esc(was)}: {_esc(reason)}")


# ---------------------------------------------------------------------------
# The standard documents
# ---------------------------------------------------------------------------


def _wanted(plan, doc):
	kinds = {rules.STOCK, rules.NON_STOCK}
	wanted = {}
	for row in doc.items:
		if row.line_kind in kinds:
			wanted[row.purchase_receipt_item] = wanted.get(row.purchase_receipt_item, 0.0) + flt(row.qty)
	return wanted


def _fixed_charges(target):
	"""The draft's fixed-amount charge rows, as ``[(description, amount)]``."""
	return [
		(row.get("description") or row.get("account_head"), abs(flt(row.get("tax_amount"))))
		for row in target.get("taxes") or []
		if row.get("charge_type") == "Actual"
	]


def _say_fixed_charges(target, notice):
	"""Put the fixed-charge notice on the draft itself: its remarks before it is saved, and a
	comment on its timeline after."""
	if notice:
		target.remarks = f"{target.get('remarks') or ''} {notice}".strip()


def make_purchase_return(name):
	"""Draft ERPNext's Purchase Return for this return's lines and link it.
	``{"name", "existing", "notice"}``.

	The person opens the draft, checks the warehouse each line leaves from, and submits it. That
	submit is what takes stock out and posts the ledger.

	ERPNext's mapper reverses a fixed-amount charge in full whatever part is going back. This
	leaves that figure as ERPNext made it and says so on the draft (``notice``).
	"""
	doc = load_for(name, "send_back")
	if doc.status not in rules.GONE:
		frappe.throw(_("Mark the return Sent Back first: stock leaves when the items do."))
	live = _document("Purchase Receipt", doc.purchase_return, [])
	if live and cint(live.docstatus) != 2:
		return {"name": live.name, "existing": True, "notice": ""}
	if not receipt_is_live(doc.purchase_receipt):
		frappe.throw(_receipt_gone(doc.purchase_receipt))
	# Read again: an item may have been switched to a stock item since the return was saved.
	kinds = {
		fact["purchase_receipt_item"]: fact["line_kind"]
		for fact in receipt_facts(doc.purchase_receipt, doc.name)
	}
	for row in doc.items:
		row.line_kind = kinds.get(row.purchase_receipt_item, row.line_kind)
	plan = _plan(doc)
	if not plan["needs_purchase_return"]:
		frappe.throw(plan["note"] or _("This return has no line that needs a Purchase Return."))

	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
		make_purchase_return as erpnext_make_purchase_return,
	)

	target = erpnext_make_purchase_return(doc.purchase_receipt)
	mapped = sum(abs(flt(row.qty)) for row in target.items)
	kept, short = rules.trim_return_rows(
		[{"purchase_receipt_item": row.purchase_receipt_item, "qty": row.qty} for row in target.items],
		_wanted(plan, doc),
	)
	if short:
		by_line = {row.purchase_receipt_item: row.item_code for row in doc.items}
		frappe.throw(
			"<br>".join(
				_("ERPNext can return {0} of {1} on this receipt line, and this return asks for {2}.").format(
					frappe.format_value(available, {"fieldtype": "Float"}),
					_esc(by_line.get(line, line)),
					frappe.format_value(want, {"fieldtype": "Float"}),
				)
				for line, (want, available) in short.items()
			)
		)
	quantities = {row["purchase_receipt_item"]: row["qty"] for row in kept}
	rows = []
	for row in target.items:
		qty = quantities.get(row.purchase_receipt_item)
		if qty is None:
			continue
		row.qty = qty
		row.received_qty = qty
		row.rejected_qty = 0
		row.stock_qty = flt(qty) * flt(row.conversion_factor or 1)
		row.idx = len(rows) + 1
		rows.append(row)
	target.set("items", rows)
	number = f" Supplier's return number {doc.supplier_return_number}." if doc.supplier_return_number else ""
	target.remarks = f"Supplier Return {doc.name}.{number}"
	partial = sum(abs(flt(row.qty)) for row in rows) < mapped - 1e-9 or bool(
		[fact for fact in kinds if fact not in quantities]
	)
	notice = rules.fixed_charge_notice(_fixed_charges(target), partial, "Purchase Return")
	_say_fixed_charges(target, notice)
	target.run_method("calculate_taxes_and_totals")
	target.insert()
	if notice:
		target.add_comment("Comment", _esc(notice))
	doc.purchase_return = target.name
	_save(doc, f"{_who()} drafted Purchase Return {_esc(target.name)}. It moves stock when it is submitted.")
	return {"name": target.name, "existing": False, "notice": notice}


def bills_for(doc):
	"""Submitted bills (not debit notes) that charge for this return's receipt lines, newest first."""
	lines = [row.purchase_receipt_item for row in doc.items if row.purchase_receipt_item]
	order_lines = [row.purchase_order_item for row in doc.items if row.purchase_order_item]
	if not lines:
		return []
	rows = frappe.db.sql(
		"""
		select distinct p.name, p.posting_date, p.bill_no, p.grand_total, p.outstanding_amount, p.status
		from `tabPurchase Invoice Item` c
		join `tabPurchase Invoice` p on p.name = c.parent
		where p.docstatus = 1 and p.is_return = 0
			and (c.pr_detail in %(lines)s
				or (ifnull(c.pr_detail, '') = '' and c.po_detail in %(order_lines)s))
		order by p.posting_date desc, p.name desc
		""",
		{"lines": tuple(lines), "order_lines": tuple(order_lines or [""])},
		as_dict=True,
	)
	return rows


def make_debit_note(name, purchase_invoice):
	"""Draft ERPNext's Debit Note against a bill, cut down to this return's lines, and link it.
	``{"name", "existing", "notice"}``.

	**Which invoice the note lowers.** In ERPNext 16.54.0 a debit note carries *Update Outstanding
	for Self* (``update_outstanding_for_self``), default **on**, and the mapper leaves it on: the
	note is then booked against itself (``purchase_invoice.add_supplier_gl_entry``), the bill
	stays fully unpaid, and somebody has to run Payment Reconciliation before the credit lowers
	anything. A return against a bill we have not paid means "pay this bill less", so when the
	bill's outstanding amount covers the note this turns the box **off**: the note is booked
	against the bill, the bill's outstanding drops, and the note's own is 0
	(``taxes_and_totals.calculate_outstanding_amount``). When the bill cannot absorb the note (it
	is paid, or nearly), the box is left **on**: the note then carries what the supplier owes us,
	which is what a refund Payment Entry or a later reconciliation is made against. ERPNext
	applies the same rule itself when the box is off and the bill is too small
	(``accounts_controller.validate_against_voucher_outstanding``). It is ERPNext's own checkbox,
	visible on the draft, and the person can change it before submitting.
	"""
	doc = load_for(name, "refund")
	if doc.status not in rules.GONE:
		frappe.throw(_("Mark the return Sent Back first."))
	live = _document("Purchase Invoice", doc.debit_note, [])
	if live and cint(live.docstatus) != 2:
		return {"name": live.name, "existing": True, "notice": ""}
	if purchase_invoice not in {row.name for row in bills_for(doc)}:
		frappe.throw(_("{0} is not a submitted bill for the items on this return.").format(purchase_invoice))

	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import (
		make_debit_note as erpnext_make_debit_note,
	)

	target = erpnext_make_debit_note(purchase_invoice)
	mapped = sum(abs(flt(row.qty)) for row in target.items)
	by_line, by_order_line = {}, {}
	for row in doc.items:
		by_line[row.purchase_receipt_item] = by_line.get(row.purchase_receipt_item, 0.0) + flt(row.qty)
		if row.purchase_order_item:
			by_order_line[row.purchase_order_item] = by_order_line.get(row.purchase_order_item, 0.0) + flt(
				row.qty
			)
	rows = []
	for row in target.items:
		want = by_line.pop(row.pr_detail, None) if row.pr_detail else by_order_line.pop(row.po_detail, None)
		if not want:
			continue
		qty = -min(flt(want), abs(flt(row.qty)))
		if not qty:
			continue
		row.qty = qty
		row.received_qty = qty
		row.rejected_qty = 0
		row.stock_qty = flt(qty) * flt(row.conversion_factor or 1)
		row.idx = len(rows) + 1
		rows.append(row)
	if not rows:
		frappe.throw(
			_("{0} has nothing left to credit for the items on this return.").format(purchase_invoice)
		)
	target.set("items", rows)
	# The Purchase Return moves the stock. A debit note that also did would take it out twice.
	target.update_stock = 0
	target.remarks = f"Supplier Return {doc.name}."
	partial = sum(abs(flt(row.qty)) for row in rows) < mapped - 1e-9
	notice = rules.fixed_charge_notice(_fixed_charges(target), partial, "Debit Note")
	_say_fixed_charges(target, notice)
	target.run_method("calculate_taxes_and_totals")
	total = abs(flt(target.get("rounded_total")) or flt(target.get("grand_total")))
	bill_outstanding = flt(frappe.db.get_value("Purchase Invoice", purchase_invoice, "outstanding_amount"))
	# See the docstring: against the bill when the bill can take it, against itself when it can't.
	target.update_outstanding_for_self = 0 if total > 0 and bill_outstanding + 0.005 >= total else 1
	target.insert()
	if notice:
		target.add_comment("Comment", _esc(notice))
	doc.debit_note = target.name
	where = (
		"It is set against the bill, so the bill is paid for less."
		if not cint(target.update_outstanding_for_self)
		else "The bill can't absorb it, so the note carries what the supplier owes us."
	)
	_save(doc, f"{_who()} drafted Debit Note {_esc(target.name)} against {_esc(purchase_invoice)}. {where}")
	return {"name": target.name, "existing": False, "notice": notice}


def refund_source(name):
	"""Which invoice a refund Payment Entry is made against: ``{"doctype", "name"}`` or a refusal.

	ERPNext builds a *Receive* payment from a Purchase Invoice whose outstanding amount is negative:
	the debit note itself, or the bill it was set against once that bill had already been paid.
	"""
	doc = load_for(name, "refund")
	note = _document("Purchase Invoice", doc.debit_note, ["outstanding_amount", "return_against"])
	if not note or cint(note.docstatus) != 1:
		frappe.throw(
			_(
				"A refund payment is made against a submitted Debit Note. Create the Debit Note first; if the bill is in QuickBooks, type the refund with Record Money Received."
			)
		)
	if flt(note.outstanding_amount) < 0:
		return {"doctype": "Purchase Invoice", "name": note.name}
	if (
		note.return_against
		and flt(frappe.db.get_value("Purchase Invoice", note.return_against, "outstanding_amount")) < 0
	):
		return {"doctype": "Purchase Invoice", "name": note.return_against}
	frappe.throw(
		_(
			"Nothing is owed back on Debit Note {0}: it was set against the bill, so the credit has already lowered what we pay."
		).format(note.name)
	)


def _link(doc, field, doctype, name, checks):
	header = frappe.db.get_value(doctype, name, ["name", "docstatus", *checks], as_dict=True)
	if not header or cint(header.docstatus) == 2:
		frappe.throw(_("{0} {1} doesn't exist or is canceled.").format(_(doctype), name))
	taken = frappe.db.get_value(DOCTYPE, {field: name, "name": ("!=", doc.name)}, "name")
	if taken:
		frappe.throw(_("{0} is already linked to {1}.").format(name, taken))
	return header


def link_purchase_return(name, purchase_return):
	"""Link a Purchase Return somebody made with ERPNext's own button.

	It must be a return against this return's receipt **and say what this return says**: the same
	receipt lines at the same quantities (``supplier_return_rules.link_problems``). A link means
	"these are the same goods", and the quantity limit trusts that.
	"""
	doc = load_for(name, "send_back")
	header = _link(
		doc, "purchase_return", "Purchase Receipt", purchase_return, ["is_return", "return_against"]
	)
	if not cint(header.is_return) or header.return_against != doc.purchase_receipt:
		frappe.throw(_("{0} is not a return against {1}.").format(purchase_return, doc.purchase_receipt))
	taken = {}
	for line, _parent, qty in _return_rows(
		doc.purchase_receipt, parents=[purchase_return], submitted_only=False
	):
		taken[line] = taken.get(line, 0.0) + qty
	labels = {row.purchase_receipt_item: row.item_code for row in doc.items}
	for fact in receipt_facts(doc.purchase_receipt, doc.name):
		labels.setdefault(fact["purchase_receipt_item"], fact["item_code"])
	problems = rules.link_problems(_wanted(_plan(doc), doc), taken, labels)
	if problems:
		frappe.throw(
			_("{0} can't be linked to this return, because it does not return the same items:").format(
				purchase_return
			)
			+ "<br>"
			+ "<br>".join(frappe.utils.escape_html(problem) for problem in problems)
			+ "<br>"
			+ _("Start a Supplier Return whose lines match it, or change the draft Purchase Return.")
		)
	doc.purchase_return = purchase_return
	_save(doc, f"{_who()} linked Purchase Return {_esc(purchase_return)}.")
	return sync(doc.name)


def link_debit_note(name, debit_note):
	doc = load_for(name, "refund")
	header = _link(doc, "debit_note", "Purchase Invoice", debit_note, ["is_return", "supplier"])
	if not cint(header.is_return) or header.supplier != doc.supplier:
		frappe.throw(_("{0} is not a Debit Note for {1}.").format(debit_note, doc.supplier))
	doc.debit_note = debit_note
	_save(doc, f"{_who()} linked Debit Note {_esc(debit_note)}.")
	return sync(doc.name)


def link_payment_entry(name, payment_entry, amount=None):
	"""Link a refund Payment Entry by hand: money received from this supplier.

	``amount`` is how much of the payment is for this return; blank takes what other returns have
	not already counted. A payment's amount is never counted twice
	(``supplier_return_rules.by_hand_share``), and a return can hold several payments.
	"""
	doc = load_for(name, "refund")
	header = frappe.db.get_value(
		"Payment Entry",
		payment_entry,
		[
			"name",
			"docstatus",
			"payment_type",
			"party_type",
			"party",
			"paid_amount",
			"posting_date",
			"reference_no",
		],
		as_dict=True,
	)
	if not header or cint(header.docstatus) != 1:
		frappe.throw(_("Payment Entry {0} doesn't exist or is not submitted.").format(payment_entry))
	if header.payment_type != "Receive" or header.party_type != "Supplier" or header.party != doc.supplier:
		frappe.throw(_("{0} is not money received from {1}.").format(payment_entry, doc.supplier))
	if any(row.get("payment_entry") == payment_entry for row in doc.get("payments") or []):
		frappe.throw(_("{0} is already on this return.").format(payment_entry))
	if doc.status not in rules.GONE:
		frappe.throw(_("Mark the return Sent Back first."))
	taken = sum(
		flt(row.amount)
		for row in frappe.get_all(
			PAYMENT_CHILD,
			filters={"payment_entry": payment_entry, "parenttype": DOCTYPE, "parent": ("!=", doc.name)},
			fields=["amount"],
		)
	)
	try:
		still_expected = flt(doc.expected_amount) - flt(_money_documents(doc)["amount"])
		share = rules.by_hand_share(header.paid_amount, taken, amount, still_expected)
	except rules.RuleError as error:
		_throw(error)
	status = sync(
		doc.name,
		attach={
			"payment_entry": payment_entry,
			"amount": share,
			"received_on": header.posting_date,
			"reference": header.reference_no or payment_entry,
			"by_hand": 1,
		},
	)
	frappe.get_doc(DOCTYPE, doc.name).add_comment(
		"Info",
		f"{_who()} linked refund Payment Entry {_esc(payment_entry)}: "
		f"{frappe.utils.fmt_money(share)} of it is for this return.",
	)
	return status


# ---------------------------------------------------------------------------
# Keeping the return in step with its documents
# ---------------------------------------------------------------------------


def sync(name, attach=None):
	"""Read the linked documents and set what follows from them. Returns the status.

	A link to a deleted or canceled document is dropped, and so is the share of a payment that no
	longer stands. ``attach`` adds (or updates) this return's share of one submitted Payment
	Entry: ``{"payment_entry", "amount", "received_on", "reference", "by_hand"}``. Once a Debit
	Note or a payment backs the money, what a person typed gives way to it. The outcome and the
	amounts are then whatever ``supplier_return_rules.money_from_documents`` says.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	notes, lost_replacement = [], False
	for field, doctype in (
		("purchase_return", "Purchase Receipt"),
		("debit_note", "Purchase Invoice"),
		("replacement_receipt", "Purchase Receipt"),
	):
		linked = doc.get(field)
		if not linked:
			continue
		docstatus = frappe.db.get_value(doctype, linked, "docstatus")
		if docstatus is None or cint(docstatus) == 2:
			doc.set(field, None)
			lost_replacement = lost_replacement or field == "replacement_receipt"
			notes.append(f"{doctype} {_esc(linked)} is gone, so its link was removed.")
	rows = []
	for row in doc.get("payments") or []:
		entry = row.get("payment_entry")
		if entry and cint(frappe.db.get_value("Payment Entry", entry, "docstatus") or 0) != 1:
			notes.append(f"Payment Entry {_esc(entry)} no longer stands, so its amount was taken off.")
			continue
		rows.append(row)
	if attach:
		existing = next((row for row in rows if row.get("payment_entry") == attach["payment_entry"]), None)
		if existing:
			existing.amount = attach["amount"]
			attach = None
	doc.set("payments", rows)
	if attach:
		doc.append(
			"payments",
			{
				"payment_entry": attach["payment_entry"],
				"kind": "Refund",
				"amount": attach["amount"],
				"received_on": attach.get("received_on"),
				"reference": attach.get("reference"),
				"by_hand": cint(attach.get("by_hand")),
				"recorded_by": frappe.session.user,
			},
		)
		notes.append(
			f"Payment Entry {_esc(attach['payment_entry'])}: "
			f"{frappe.utils.fmt_money(attach['amount'])} of it is for this return."
		)
	typed = [row for row in doc.get("payments") or [] if not row.get("payment_entry")]
	if typed and _money_documents(doc, with_typed=False)["backed"]:
		total = sum(flt(row.get("amount")) for row in typed)
		doc.set("payments", [row for row in doc.get("payments") or [] if row.get("payment_entry")])
		notes.append(
			f"{frappe.utils.fmt_money(total)} typed by hand was replaced by the linked documents, "
			"which now decide the amount."
		)
	_apply_money(doc, notes)
	if doc.status == rules.REPLACEMENT_RECEIVED and lost_replacement:
		doc.status = rules.SENT_BACK
		doc.status_changed_on = now_datetime()
		doc.closed_on = None
	doc.flags.ee_action = True
	doc.flags.ignore_permissions = True
	doc.save()
	for line in notes:
		doc.add_comment("Info", line)
	return doc.status


def linked_to(doctype, name):
	"""The Supplier Returns a standard document belongs to: ``[{"name", "status", "role"}]``."""
	if not installed() or not name:
		return []
	out = []
	if doctype == "Payment Entry":
		parents = sorted(
			set(
				frappe.get_all(
					PAYMENT_CHILD, filters={"payment_entry": name, "parenttype": DOCTYPE}, pluck="parent"
				)
			)
		)
		if parents:
			for row in frappe.get_all(
				DOCTYPE, filters={"name": ("in", parents)}, fields=["name", "status"], order_by="creation asc"
			):
				out.append({"name": row.name, "status": row.status, "role": "a refund for"})
		return out
	fields = {
		"Purchase Receipt": (
			("purchase_return", "the Purchase Return for"),
			("replacement_receipt", "the replacement for"),
			("purchase_receipt", ORIGINAL_ROLE),
		),
		"Purchase Invoice": (("debit_note", "the Debit Note for"),),
	}.get(doctype, ())
	for field, role in fields:
		for row in frappe.get_all(
			DOCTYPE, filters={field: name}, fields=["name", "status"], order_by="creation asc"
		):
			out.append({"name": row.name, "status": row.status, "role": role})
	return out


def _refund_shares(doc):
	"""``{Supplier Return: share}`` for a submitted Payment Entry, by the invoices it names.

	Only money **received** from a **Supplier**. Each reference row's allocated amount is shared
	once: all of it to the return whose Debit Note the row names, or, when the row names a bill
	that Debit Notes were set against, split between those returns oldest first, each up to its
	own note (``supplier_return_rules.split_payment``).
	"""
	if doc.get("payment_type") != "Receive" or doc.get("party_type") != "Supplier":
		return {}
	allocated = {}
	for row in doc.get("references") or []:
		if row.get("reference_doctype") == "Purchase Invoice" and row.get("reference_name"):
			invoice = row.get("reference_name")
			allocated[invoice] = allocated.get(invoice, 0.0) + abs(flt(row.get("allocated_amount")))
	if not allocated:
		return {}
	standing = {"supplier": doc.get("party"), "status": ("not in", sorted(rules.NOT_COUNTED))}
	shares = {}
	for row in frappe.get_all(
		DOCTYPE, filters=dict(standing, debit_note=("in", sorted(allocated))), fields=["name", "debit_note"]
	):
		shares[row.name] = shares.get(row.name, 0.0) + allocated.pop(row.debit_note, 0.0)
	# What is left names bills. A Debit Note set against a bill that was already paid leaves the
	# money owed on the bill, and the refund names the bill.
	for bill, amount in allocated.items():
		notes = {
			row.name: abs(flt(row.grand_total))
			for row in frappe.get_all(
				"Purchase Invoice",
				filters={"is_return": 1, "docstatus": 1, "return_against": bill},
				fields=["name", "grand_total"],
			)
		}
		if not notes:
			continue
		claims = [
			(row.name, notes[row.debit_note])
			for row in frappe.get_all(
				DOCTYPE,
				filters=dict(standing, debit_note=("in", sorted(notes))),
				fields=["name", "debit_note"],
				order_by="creation asc",
			)
		]
		for name, share in rules.split_payment(amount, claims).items():
			shares[name] = shares.get(name, 0.0) + share
	return {name: round(share, 2) for name, share in shares.items() if share > 0.005}


def on_document_change(doc, method=None):
	"""``Purchase Receipt``, ``Purchase Invoice`` and ``Payment Entry`` → ``on_submit``, ``on_cancel``
	and ``on_trash``: bring the Supplier Returns that link this document up to date.

	A submitted *Receive* Payment Entry that names a return's Debit Note (or the paid bill the note
	was set against) is recorded on that return as a refund, at that return's share of it. Never
	raises.
	"""
	try:
		if not installed() or doc.doctype not in ("Purchase Receipt", "Purchase Invoice", "Payment Entry"):
			return
		names = {row["name"] for row in linked_to(doc.doctype, doc.name) if row["role"] != ORIGINAL_ROLE}
		attach = {}
		if doc.doctype == "Payment Entry" and method == "on_submit":
			for name, share in _refund_shares(doc).items():
				attach[name] = {
					"payment_entry": doc.name,
					"amount": share,
					"received_on": doc.get("posting_date"),
					"reference": doc.get("reference_no") or doc.name,
				}
				names.add(name)
		if method == "on_trash":
			for field in LINK_FIELDS.get(doc.doctype, ()):
				for name in frappe.get_all(DOCTYPE, filters={field: doc.name}, pluck="name"):
					frappe.db.set_value(DOCTYPE, name, field, None, update_modified=False)
					names.add(name)
		for name in sorted(names):
			_sync_quietly(name, attach.get(name))
	except Exception:
		frappe.log_error(
			title="Supplier Return: sync from " + str(getattr(doc, "doctype", "")),
			message=frappe.get_traceback(),
		)


def _sync_quietly(name, attach=None):
	"""``sync`` inside a savepoint: a failure is rolled back and logged, so a half-written Supplier
	Return never rides along in the transaction of the document being submitted."""
	savepoint = "supplier_return_sync"
	try:
		frappe.db.savepoint(savepoint)
		sync(name, attach)
	except Exception:
		message = frappe.get_traceback()
		try:
			frappe.db.rollback(save_point=savepoint)
		except Exception:
			pass
		frappe.log_error(title=f"Supplier Return: sync of {name}", message=message)


def sync_open_returns():
	"""Daily: re-read the Debit Note of each return still waiting on money.

	A debit note set against a later bill in Payment Reconciliation changes its outstanding amount
	without any event this app hears. Never sends anything; never raises.
	"""
	try:
		if not installed():
			return
		for name in frappe.get_all(
			DOCTYPE, filters={"status": rules.SENT_BACK, "debit_note": ("is", "set")}, pluck="name"
		):
			try:
				sync(name)
			except Exception:
				frappe.log_error(
					title=f"Supplier Return: daily sync of {name}", message=frappe.get_traceback()
				)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Supplier Return: daily sync", message=frappe.get_traceback())


def tell_the_bill(doc, method=None):
	"""``Purchase Invoice`` → ``validate``: say so when a bill covers something that is going back.

	An orange message, never a refusal: ERPNext already bills a receipt for less once its Purchase
	Return is submitted, and this covers the bill typed by hand or made from the order. Only in a
	person's web request; silent in imports, jobs, migrate and tests. Never raises.

	A bill row that names its receipt line is matched on that line alone, so the bill for a
	**replacement** receipt on the same order line is not told about the return it replaced. Only
	a row with no receipt line falls back to its order line. Finished returns are left out: their
	refund, credit or replacement already happened.
	"""
	try:
		if cint(doc.get("is_return")) or not getattr(frappe.local, "request", None) or not installed():
			return
		if any(
			getattr(frappe.flags, flag, False)
			for flag in ("in_import", "in_migrate", "in_patch", "in_install", "in_test")
		):
			return
		rows = doc.get("items") or []
		lines = sorted({row.get("pr_detail") for row in rows if row.get("pr_detail")})
		order_lines = sorted(
			{row.get("po_detail") for row in rows if row.get("po_detail") and not row.get("pr_detail")}
		)
		if not (lines or order_lines):
			return
		found = frappe.db.sql(
			f"""
			select p.name, p.status, p.expected_amount, c.item_code, c.qty
			from `tab{CHILD}` c
			join `tab{DOCTYPE}` p on p.name = c.parent
			where p.status in %(open)s and ifnull(p.debit_note, '') = ''
				and (c.purchase_receipt_item in %(lines)s or c.purchase_order_item in %(order_lines)s)
			order by p.creation asc, c.idx asc
			""",
			{
				"open": tuple(rules.BILL_NOTICE_STATUSES),
				"lines": tuple(lines or [""]),
				"order_lines": tuple(order_lines or [""]),
			},
			as_dict=True,
		)
		returns = {}
		for row in found:
			entry = returns.setdefault(
				row.name,
				{"name": row.name, "status": row.status, "expected_amount": row.expected_amount, "items": []},
			)
			entry["items"].append((row.item_code, row.qty))
		notice = rules.bill_notice(list(returns.values()))
		if notice:
			frappe.msgprint(frappe.utils.escape_html(notice), title=_("Supplier Return"), indicator="orange")
	except Exception:
		frappe.log_error(title="Supplier Return: bill notice", message=frappe.get_traceback())


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------


def _template():
	"""``(subject, body, found)``: the Desk's Email Template, else the built-in wording."""
	try:
		if frappe.db.exists("Email Template", rules.EMAIL_TEMPLATE):
			record = frappe.get_doc("Email Template", rules.EMAIL_TEMPLATE)
			body = record.get("response_html") if cint(record.get("use_html")) else record.get("response")
			return record.get("subject") or rules.DEFAULT_SUBJECT, body or rules.DEFAULT_BODY, True
	except Exception:
		pass
	return rules.DEFAULT_SUBJECT, rules.DEFAULT_BODY, False


def _contacts(supplier):
	"""``[(email, label)]`` for the supplier's contacts, the primary one first."""
	rows = frappe.db.sql(
		"""
		select c.email_id, c.first_name, c.last_name, c.is_primary_contact
		from `tabContact` c
		join `tabDynamic Link` l on l.parent = c.name and l.parenttype = 'Contact'
		where l.link_doctype = 'Supplier' and l.link_name = %(supplier)s and ifnull(c.email_id, '') != ''
		order by c.is_primary_contact desc, c.modified desc
		""",
		{"supplier": supplier},
		as_dict=True,
	)
	return [
		(row.email_id, " ".join(part for part in (row.first_name, row.last_name) if part)) for row in rows
	]


def recipients(doc):
	"""The addresses the dialog offers, best first."""
	candidates = [(doc.contact_email, "")]
	candidates += _contacts(doc.supplier)
	candidates.append(
		(frappe.db.get_value("Supplier", doc.supplier, "email_id"), doc.supplier_name or doc.supplier)
	)
	return rules.recipient_choices(candidates)


def _line_dicts(doc):
	parts = {}
	lines = [row.purchase_receipt_item for row in doc.items if row.purchase_receipt_item]
	if lines:
		parts = dict(
			frappe.get_all(
				"Purchase Receipt Item",
				filters={"name": ("in", lines)},
				fields=["name", "supplier_part_no"],
				as_list=True,
			)
		)
	return [
		{
			"item_code": row.item_code,
			"item_name": row.item_name,
			"qty": row.qty,
			"uom": row.uom,
			"reason": row.reason,
			"condition_notes": row.condition_notes,
			"purchase_order": row.purchase_order,
			"supplier_part_no": parts.get(row.purchase_receipt_item),
		}
		for row in doc.items
	]


def _escape(context):
	escape = frappe.utils.escape_html
	return {key: escape(value) if isinstance(value, str) else value for key, value in context.items()}


def draft_request(doc, contact_name=""):
	"""``{"subject", "body", "template_found"}``: the request's subject and editable body.

	The body is content only (paragraphs and the lines table); :func:`wrap_request` adds the
	letterhead when it is sent or previewed. Raises on a template error.
	"""
	from erpnext_enhancements import email_style

	lines = _line_dicts(doc)
	company = frappe.db.get_value("Company", doc.company, "company_name") or doc.company
	phone = frappe.db.get_value("Company", doc.company, "phone_no") or ""
	context = rules.email_context(
		doc.as_dict(), lines, company, get_fullname(frappe.session.user), contact_name, phone
	)
	subject_template, body_template, found = _template()
	subject = " ".join(
		frappe.utils.strip_html_tags(str(frappe.render_template(subject_template, dict(context)))).split()
	)
	escaped = _escape(context)
	# The table is not in the text the person edits (the Desk's editor would rewrite its markup).
	# A marker line stands in for it; ``compose_request`` puts the real table there.
	escaped["lines_table"] = f"<p>{rules.LINES_MARKER}</p>"
	return {
		"subject": subject,
		"body": frappe.render_template(body_template, escaped),
		"template_found": found,
	}


def lines_table(doc):
	"""The items table, in the email design system's own markup, from the return's saved lines."""
	from erpnext_enhancements import email_style

	return email_style.table(["Part", "Item", "Qty", "Reason"], rules.email_lines(_line_dicts(doc)))


def compose_request(doc, body):
	"""The message as it goes: the person's words, sanitized, with the items table put in."""
	return rules.place_lines_table(frappe.utils.sanitize_html(body or ""), lines_table(doc))


def wrap_request(subject, body):
	"""The body inside the app's one email layout. The neutral stripe: a return has no pillar."""
	from erpnext_enhancements import email_style

	return email_style.wrap(body, title=subject, eyebrow=_("Return request"))


def greeting_name(doc, email):
	"""The first name to greet: the supplier Contact whose address this is, else nobody. A
	company's own address gets a plain "Hello,", never "Hello Acme,"."""
	email = (email or "").strip().lower()
	for address, name in _contacts(doc.supplier):
		if (address or "").strip().lower() == email and name:
			return name.split(" ")[0]
	return ""


def _photo_attachments(doc):
	"""The image files attached to the return, as ``frappe.sendmail`` attachment dicts."""
	out = []
	for row in frappe.get_all(
		"File",
		filters={"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name, "is_folder": 0},
		fields=["name", "file_name", "file_url"],
		order_by="creation asc",
	):
		name = (row.file_name or row.file_url or "").lower()
		if name.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif")):
			out.append({"fid": row.name})
	return out


def send_request(name, recipient, subject, body, cc=None, attach_photos=False):
	"""Email the return request to the supplier, log it on the timeline, and mark it Requested.

	Called only by ``api.supplier_returns.send_request`` (POST, from the person's click in the
	Send dialog). ``body`` is what the person saw and edited; it is sanitized, then wrapped in the
	email shell. The PDF of the return is attached. Returns the status.
	"""
	doc = load_for(name, "request")
	try:
		rules.next_status("request", doc.status)
		to = rules.check_recipient(recipient)
		copies = [rules.check_recipient(address) for address in (cc or []) if (address or "").strip()]
	except rules.RuleError as error:
		_throw(error)
	subject = " ".join(frappe.utils.strip_html_tags(str(subject or "")).split())
	words = frappe.utils.strip_html_tags(frappe.utils.sanitize_html(body or "")).replace(
		rules.LINES_MARKER, ""
	)
	if not subject or not words.strip():
		frappe.throw(_("The email needs a subject and a message."))
	body = compose_request(doc, body)
	attachments = [
		frappe.attach_print(DOCTYPE, doc.name, print_format=_print_format(), file_name=doc.name, doc=doc)
	]
	if attach_photos:
		attachments += _photo_attachments(doc)
	frappe.sendmail(
		recipients=[to],
		cc=copies or None,
		subject=subject,
		message=wrap_request(subject, body),
		attachments=attachments,
		reference_doctype=DOCTYPE,
		reference_name=doc.name,
		reply_to=frappe.session.user if "@" in (frappe.session.user or "") else None,
	)
	_record_communication(doc, to, copies, subject, body)
	if not doc.contact_email:
		doc.contact_email = to
	doc.flags.ee_action = True
	doc.save()
	return mark_requested(name, "Email", action_done=True).status


def _print_format():
	return PRINT_FORMAT if frappe.db.exists("Print Format", PRINT_FORMAT) else None


def _record_communication(doc, to, copies, subject, body):
	"""The sent email on the return's timeline, as the Desk's own email composer would leave it."""
	try:
		frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": subject,
				"content": body,
				"sender": frappe.session.user,
				"recipients": to,
				"cc": ", ".join(copies) if copies else None,
				"reference_doctype": DOCTYPE,
				"reference_name": doc.name,
				"status": "Linked",
				"email_status": "Open",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"Supplier Return: timeline entry for {doc.name}", message=frappe.get_traceback()
		)
		doc.add_comment("Info", f"{_who()} emailed the return request to {_esc(to)}.")

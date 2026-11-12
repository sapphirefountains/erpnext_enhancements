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
  the Supplier Return (``purchase_return``, ``debit_note``, ``payment_entry``,
  ``replacement_receipt``); the standard documents find their return by asking for it
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
	"payment_entry",
	"closed_on",
	"money_recorded_by",
	"outcome_notes",
)

#: The line fields that say *what* is going back. Locked once the return leaves Draft.
LINE_IDENTITY = ("purchase_receipt_item", "item_code", "qty")

#: :func:`linked_to`'s role for the receipt the items came in on. It is not a document the return
#: waits on, so its submit and cancel do not sync the return.
ORIGINAL_ROLE = "returned on"

#: Which of the return's link fields can name a document of each standard doctype.
LINK_FIELDS = {
	"Purchase Receipt": ("purchase_return", "replacement_receipt"),
	"Purchase Invoice": ("debit_note",),
	"Payment Entry": ("payment_entry",),
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
		order_lines = sorted({name for name in (purchase_order_items or []) if name})
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


def _returned_outside(receipt, line_names):
	"""``{receipt line: quantity}`` on submitted Purchase Returns no standing Supplier Return links."""
	if not line_names:
		return {}
	linked = set()
	if installed():
		linked = set(
			frappe.get_all(
				DOCTYPE,
				filters={"purchase_receipt": receipt, "status": ("not in", sorted(rules.NOT_COUNTED))},
				pluck="purchase_return",
			)
		)
	rows = frappe.db.sql(
		"""
		select c.purchase_receipt_item, c.parent, abs(c.qty)
		from `tabPurchase Receipt Item` c
		join `tabPurchase Receipt` p on p.name = c.parent
		where p.docstatus = 1 and p.is_return = 1 and p.return_against = %(receipt)s
			and c.purchase_receipt_item in %(lines)s
		""",
		{"receipt": receipt, "lines": tuple(line_names)},
	)
	out = {}
	for line, parent, qty in rows:
		if parent not in linked:
			out[line] = out.get(line, 0.0) + flt(qty)
	return out


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


def _receipt_header(receipt):
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
	if cint(header.docstatus) != 1:
		frappe.throw(
			_("Purchase Receipt {0} is not submitted, so nothing has been received on it yet.").format(
				receipt
			)
		)
	return header


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
	_protect_action_fields(doc, before)
	# The controller's validate runs before Frappe's own mandatory check, and a read by a blank
	# name would answer with some other receipt.
	if not doc.purchase_receipt or not doc.supplier:
		frappe.throw(_("Pick the supplier and the Purchase Receipt the items came in on."))
	header = _receipt_header(doc.purchase_receipt)
	if header.supplier != doc.supplier:
		frappe.throw(
			_("Purchase Receipt {0} is from {1}, not {2}.").format(
				doc.purchase_receipt, header.supplier, doc.supplier
			)
		)
	doc.company = header.company
	if getdate(doc.posting_date) < getdate(header.posting_date):
		frappe.throw(_("The return can't be dated before the receipt ({0}).").format(header.posting_date))
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
	if draft:
		problems = rules.quantity_problems(
			[
				{
					"idx": row.idx,
					"item_code": row.item_code,
					"qty": row.qty,
					"returnable": facts[row.purchase_receipt_item]["returnable"],
					"received": row.received_qty,
					"reason": row.reason,
					"condition_notes": row.condition_notes,
					"purchase_receipt_item": row.purchase_receipt_item,
				}
				for row in doc.items
			]
		)
		if problems:
			frappe.throw("<br>".join(frappe.utils.escape_html(problem) for problem in problems))
	if doc.requested_resolution not in rules.RESOLUTIONS:
		frappe.throw(_("Say what we are asking for: a refund, a credit on account or a replacement."))
	for field in ("tax_amount", "restocking_fee", "return_shipping_cost"):
		if flt(doc.get(field)) < 0:
			frappe.throw(_("{0} can't be negative.").format(doc.meta.get_label(field)))
	_set_totals(doc, header, fresh_tax=draft and _tax_is_untouched(doc, before, header))
	_set_header_links(doc)
	_set_derived(doc)


def _protect_action_fields(doc, before):
	"""Put back any field only an action may set, unless an action is what is saving."""
	if doc.flags.get("ee_action"):
		return
	if not before:
		for field in ACTION_FIELDS:
			doc.set(field, None)
		doc.status = rules.DRAFT
		doc.status_changed_on = now_datetime()
		return
	for field in ACTION_FIELDS:
		doc.set(field, before.get(field))


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
	doc.stock_state = rules.stock_state(
		doc.status, plan, _document("Purchase Receipt", doc.purchase_return, [])
	)
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
	for field in ("purchase_return", "debit_note", "payment_entry"):
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


def _money_documents(doc):
	"""The linked Debit Note and Payment Entry as ``supplier_return_rules.money_from_documents`` reads them."""
	note = _document(
		"Purchase Invoice", doc.debit_note, ["grand_total", "outstanding_amount", "posting_date", "bill_no"]
	)
	payment = _document("Payment Entry", doc.payment_entry, ["paid_amount", "posting_date", "reference_no"])
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
		payment_entry=payment
		and {
			"name": payment.name,
			"docstatus": payment.docstatus,
			"amount": flt(payment.paid_amount),
			"posting_date": payment.posting_date,
			"reference": payment.reference_no,
		},
	)


def record_money(name, kind, amount, received_on, reference, notes=None):
	"""A refund or a credit typed by the person who saw it. Refused once a document is linked."""
	doc = load_for(name, kind if kind in ("refund", "credit") else "refund")
	try:
		typed = rules.check_typed_money(
			kind, amount, received_on, reference, nowdate(), _money_documents(doc)
		)
	except rules.RuleError as error:
		_throw(error)
	_advance(doc, kind)
	doc.amount_received = typed["amount"]
	doc.money_received_on = typed["date"]
	doc.money_reference = typed["reference"]
	doc.money_source = rules.TYPED
	doc.money_recorded_by = frappe.session.user
	doc.outcome_notes = (notes or "").strip() or None
	what = "a refund" if kind == "refund" else "a credit"
	return _save(
		doc,
		f"{_who()} recorded {what} of {frappe.utils.fmt_money(typed['amount'])} "
		f"(reference {_esc(typed['reference'])}). {rules.NOT_BACKED}",
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
	if _money_documents(doc)["backed"]:
		frappe.throw(_("A Debit Note or Payment Entry is linked, so this return did get credit."))
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
	"""Undo a step recorded by mistake. A document-backed outcome is undone on its document."""
	doc = load_for(name, "reopen")
	if not (reason or "").strip():
		frappe.throw(_("Say why it is reopened."))
	if doc.money_source in (rules.FROM_DEBIT_NOTE, rules.FROM_PAYMENT):
		frappe.throw(
			_("The outcome comes from {0}. Cancel that document and this return reopens by itself.").format(
				doc.payment_entry or doc.debit_note
			)
		)
	was = doc.status
	try:
		doc.status = rules.next_status("reopen", doc.status)
	except rules.RuleError as error:
		_throw(error)
	doc.status_changed_on = now_datetime()
	doc.closed_on = None
	for field in (
		"amount_received",
		"money_received_on",
		"money_reference",
		"money_source",
		"money_recorded_by",
		"outcome_notes",
		"replacement_receipt",
	):
		doc.set(field, None)
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


def make_purchase_return(name):
	"""Draft ERPNext's Purchase Return for this return's lines and link it. ``{"name", "existing"}``.

	The person opens the draft, checks the warehouse each line leaves from, and submits it. That
	submit is what takes stock out and posts the ledger.
	"""
	doc = load_for(name, "send_back")
	if doc.status not in rules.GONE:
		frappe.throw(_("Mark the return Sent Back first: stock leaves when the items do."))
	live = _document("Purchase Receipt", doc.purchase_return, [])
	if live and cint(live.docstatus) != 2:
		return {"name": live.name, "existing": True}
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
	target.run_method("calculate_taxes_and_totals")
	target.insert()
	doc.purchase_return = target.name
	_save(doc, f"{_who()} drafted Purchase Return {_esc(target.name)}. It moves stock when it is submitted.")
	return {"name": target.name, "existing": False}


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
	"""Draft ERPNext's Debit Note against a bill, cut down to this return's lines, and link it."""
	doc = load_for(name, "refund")
	if doc.status not in rules.GONE:
		frappe.throw(_("Mark the return Sent Back first."))
	live = _document("Purchase Invoice", doc.debit_note, [])
	if live and cint(live.docstatus) != 2:
		return {"name": live.name, "existing": True}
	if purchase_invoice not in {row.name for row in bills_for(doc)}:
		frappe.throw(_("{0} is not a submitted bill for the items on this return.").format(purchase_invoice))

	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import (
		make_debit_note as erpnext_make_debit_note,
	)

	target = erpnext_make_debit_note(purchase_invoice)
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
	target.run_method("calculate_taxes_and_totals")
	target.insert()
	doc.debit_note = target.name
	_save(doc, f"{_who()} drafted Debit Note {_esc(target.name)} against {_esc(purchase_invoice)}.")
	return {"name": target.name, "existing": False}


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
	"""Link a Purchase Return somebody made with ERPNext's own button."""
	doc = load_for(name, "send_back")
	header = _link(
		doc, "purchase_return", "Purchase Receipt", purchase_return, ["is_return", "return_against"]
	)
	if not cint(header.is_return) or header.return_against != doc.purchase_receipt:
		frappe.throw(_("{0} is not a return against {1}.").format(purchase_return, doc.purchase_receipt))
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


def link_payment_entry(name, payment_entry):
	doc = load_for(name, "refund")
	header = _link(
		doc, "payment_entry", "Payment Entry", payment_entry, ["payment_type", "party_type", "party"]
	)
	if header.payment_type != "Receive" or header.party_type != "Supplier" or header.party != doc.supplier:
		frappe.throw(_("{0} is not money received from {1}.").format(payment_entry, doc.supplier))
	doc.payment_entry = payment_entry
	_save(doc, f"{_who()} linked refund Payment Entry {_esc(payment_entry)}.")
	return sync(doc.name)


# ---------------------------------------------------------------------------
# Keeping the return in step with its documents
# ---------------------------------------------------------------------------


def sync(name):
	"""Read the linked documents and set what follows from them. Returns the status.

	A link to a deleted or canceled document is dropped. A submitted Payment Entry or a settled
	Debit Note sets the outcome and the amount; losing one takes a document-backed outcome back to
	Sent Back. A typed outcome is left alone unless a document now says otherwise.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	changed, lost_replacement = [], False
	for field, doctype in (
		("purchase_return", "Purchase Receipt"),
		("debit_note", "Purchase Invoice"),
		("payment_entry", "Payment Entry"),
		("replacement_receipt", "Purchase Receipt"),
	):
		linked = doc.get(field)
		if not linked:
			continue
		docstatus = frappe.db.get_value(doctype, linked, "docstatus")
		if docstatus is None or cint(docstatus) == 2:
			doc.set(field, None)
			lost_replacement = lost_replacement or field == "replacement_receipt"
			changed.append(f"{doctype} {_esc(linked)} is gone, so its link was removed.")
	money = _money_documents(doc)
	backed_before = doc.money_source in (rules.FROM_DEBIT_NOTE, rules.FROM_PAYMENT)
	if money["status"] and doc.status in rules.GONE:
		if (doc.status, doc.money_source, flt(doc.amount_received)) != (
			money["status"],
			money["source"],
			money["amount"],
		):
			doc.status = money["status"]
			doc.status_changed_on = now_datetime()
			doc.closed_on = getdate(money["date"]) if money["date"] else nowdate()
			changed.append(
				f"{money['status']}: {frappe.utils.fmt_money(money['amount'])}. {_esc(money['note'])}"
			)
		doc.amount_received = money["amount"]
		doc.money_received_on = money["date"]
		doc.money_reference = money["reference"]
		doc.money_source = money["source"]
	elif backed_before:
		# The document that set the outcome was canceled, or the supplier owes on the note again.
		doc.status = rules.SENT_BACK
		doc.status_changed_on = now_datetime()
		doc.closed_on = None
		for field in ("amount_received", "money_received_on", "money_reference", "money_source"):
			doc.set(field, None)
		changed.append(
			"The document behind the outcome no longer stands, so this return is waiting on money again."
		)
	if doc.status == rules.REPLACEMENT_RECEIVED and lost_replacement:
		doc.status = rules.SENT_BACK
		doc.status_changed_on = now_datetime()
		doc.closed_on = None
	doc.flags.ee_action = True
	doc.flags.ignore_permissions = True
	doc.save()
	for line in changed:
		doc.add_comment("Info", line)
	return doc.status


def linked_to(doctype, name):
	"""The Supplier Returns a standard document belongs to: ``[{"name", "status", "role"}]``."""
	if not installed() or not name:
		return []
	fields = {
		"Purchase Receipt": (
			("purchase_return", "the Purchase Return for"),
			("replacement_receipt", "the replacement for"),
			("purchase_receipt", ORIGINAL_ROLE),
		),
		"Purchase Invoice": (("debit_note", "the Debit Note for"),),
		"Payment Entry": (("payment_entry", "the refund for"),),
	}.get(doctype, ())
	out = []
	for field, role in fields:
		for row in frappe.get_all(
			DOCTYPE, filters={field: name}, fields=["name", "status"], order_by="creation asc"
		):
			out.append({"name": row.name, "status": row.status, "role": role})
	return out


def _refunds_through(doc):
	"""Supplier Returns a submitted Payment Entry refunds: by the invoices it names."""
	if doc.get("payment_type") != "Receive" or doc.get("party_type") != "Supplier":
		return []
	invoices = sorted(
		{
			row.get("reference_name")
			for row in doc.get("references") or []
			if row.get("reference_doctype") == "Purchase Invoice" and row.get("reference_name")
		}
	)
	if not invoices:
		return []
	names = set(
		frappe.get_all(
			DOCTYPE,
			filters={
				"debit_note": ("in", invoices),
				"payment_entry": ("is", "not set"),
				"supplier": doc.get("party"),
			},
			pluck="name",
		)
	)
	# A debit note set against a bill that was already paid: the refund names the bill.
	for note in frappe.get_all(
		"Purchase Invoice",
		filters={"is_return": 1, "docstatus": 1, "return_against": ("in", invoices)},
		pluck="name",
	):
		names.update(
			frappe.get_all(
				DOCTYPE,
				filters={
					"debit_note": note,
					"payment_entry": ("is", "not set"),
					"supplier": doc.get("party"),
				},
				pluck="name",
			)
		)
	return sorted(names)


def on_document_change(doc, method=None):
	"""``Purchase Receipt``, ``Purchase Invoice`` and ``Payment Entry`` → ``on_submit``, ``on_cancel``
	and ``on_trash``: bring the Supplier Returns that link this document up to date.

	A submitted *Receive* Payment Entry that names a return's Debit Note (or the paid bill the note
	was set against) is linked as that return's refund. Never raises.
	"""
	try:
		if not installed() or doc.doctype not in ("Purchase Receipt", "Purchase Invoice", "Payment Entry"):
			return
		names = {row["name"] for row in linked_to(doc.doctype, doc.name) if row["role"] != ORIGINAL_ROLE}
		if doc.doctype == "Payment Entry" and method == "on_submit":
			for name in _refunds_through(doc):
				frappe.db.set_value(DOCTYPE, name, "payment_entry", doc.name, update_modified=False)
				names.add(name)
		if method == "on_trash":
			for field in LINK_FIELDS[doc.doctype]:
				for name in frappe.get_all(DOCTYPE, filters={field: doc.name}, pluck="name"):
					frappe.db.set_value(DOCTYPE, name, field, None, update_modified=False)
					names.add(name)
		for name in sorted(names):
			_sync_quietly(name)
	except Exception:
		frappe.log_error(
			title="Supplier Return: sync from " + str(getattr(doc, "doctype", "")),
			message=frappe.get_traceback(),
		)


def _sync_quietly(name):
	"""``sync`` inside a savepoint: a failure is rolled back and logged, so a half-written Supplier
	Return never rides along in the transaction of the document being submitted."""
	savepoint = "supplier_return_sync"
	try:
		frappe.db.savepoint(savepoint)
		sync(name)
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
	"""
	try:
		if cint(doc.get("is_return")) or not getattr(frappe.local, "request", None) or not installed():
			return
		if any(
			getattr(frappe.flags, flag, False)
			for flag in ("in_import", "in_migrate", "in_patch", "in_install", "in_test")
		):
			return
		lines = sorted({row.get("pr_detail") for row in doc.get("items") or [] if row.get("pr_detail")})
		order_lines = sorted({row.get("po_detail") for row in doc.get("items") or [] if row.get("po_detail")})
		if not (lines or order_lines):
			return
		rows = frappe.db.sql(
			f"""
			select p.name, p.status, p.expected_amount, c.item_code, c.qty
			from `tab{CHILD}` c
			join `tab{DOCTYPE}` p on p.name = c.parent
			where p.status not in %(skip)s and ifnull(p.debit_note, '') = ''
				and (c.purchase_receipt_item in %(lines)s or c.purchase_order_item in %(order_lines)s)
			order by p.creation asc, c.idx asc
			""",
			{
				"skip": tuple(sorted(rules.NOT_COUNTED)),
				"lines": tuple(lines or [""]),
				"order_lines": tuple(order_lines or [""]),
			},
			as_dict=True,
		)
		returns = {}
		for row in rows:
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

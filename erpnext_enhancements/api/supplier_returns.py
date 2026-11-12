# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supplier Return: the endpoints behind the form, the start buttons and Stock Scan (v1.807.0).

What question each answers:

* **"What can go back?"** ``receipt_choices`` (the receipts and lines still returnable for an
  order, an item or a supplier) and ``make_from_receipt`` (an unsaved Supplier Return with those
  lines, for ``frappe.model.open_mapped_doc``). ``start_from_scan`` saves a Draft for one line from
  the Stock Scan page, where there is no Desk form to fill.
* **"What happens next?"** ``state`` (the buttons the caller may use, days waiting, what the stock
  and money sides need) and one endpoint per step. ``state`` names a bill only to a caller who
  may read Purchase Invoices.
* **"Send it."** ``request_draft`` gives the email as the person will send it; ``send_request``
  sends what they approved.
* **"Which ERPNext document?"** ``make_purchase_return``, ``make_debit_note``, ``refund_source``
  and the three ``link_*`` for a document somebody made with ERPNext's own button.

What this module is careful about:

* **Every endpoint that changes anything is POST only**, and each loads the return through
  ``supplier_returns.load_for``: write permission on that Supplier Return *and* a role that may
  take that step (``supplier_return_rules.ACTIONS``). The form only hides what would be refused.
* **``send_request`` needs a person at a screen.** A request carrying an ``Authorization`` header
  (an API key or a token), a background job and the console are refused, with the rule the
  Maintain Stock switch and the marketing approvals use
  (``marketing.publish.workflow.signed_in_browser``). An email to a supplier is outward-facing:
  it goes when somebody reads it and clicks Send, never because a script or an assistant called
  an endpoint.
* **Nothing takes a fieldname, a doctype or a filter from the client.** Arguments are names and
  values; every query is written here or in ``inventory_enhancements/supplier_returns.py``.
* **Reads check the document they read.** ``receipt_choices`` lists only receipts the caller may
  read; ``linked`` answers only for a document the caller may read.

The work, and the reasons, are in ``inventory_enhancements/supplier_returns.py`` and
``supplier_return_rules.py``.

Tabs, per ``CLAUDE.md`` (new file).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from erpnext_enhancements.inventory_enhancements import supplier_return_rules as rules
from erpnext_enhancements.inventory_enhancements import supplier_returns as core
from erpnext_enhancements.marketing.publish.workflow import signed_in_browser

DOCTYPE = core.DOCTYPE

#: How many receipts a picker lists. Newest first; older ones are reached from the receipt itself.
CHOICE_LIMIT = 20


def _from_a_person():
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None) if request is not None else None
	authorization = headers.get("Authorization") if headers is not None else None
	return request is not None and signed_in_browser(frappe.session, authorization)


def _list(value):
	"""A JSON list or a real one, as a list of non-empty strings."""
	if isinstance(value, str):
		try:
			value = json.loads(value) if value.strip().startswith("[") else [value]
		except ValueError:
			value = [value]
	return [str(entry) for entry in (value or []) if entry]


def _may_create():
	return bool(frappe.has_permission(DOCTYPE, "create")) and rules.may("send_back", frappe.get_roles())


def _require_create():
	if not _may_create():
		frappe.throw(
			_("You can't start a Supplier Return. Ask purchasing to start it."), frappe.PermissionError
		)


# ---------------------------------------------------------------------------
# What can go back
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET", "POST"])
def receipt_choices(purchase_order=None, item_code=None, supplier=None):
	"""Receipts with something still returnable, newest first, for an order, an item or a supplier.

	``{"allowed", "receipts": [{"name", "posting_date", "supplier", "supplier_name", "lines":
	[{"purchase_receipt_item", "item_code", "item_name", "returnable", "uom", "purchase_order",
	"project", "line_kind"}]}]}``. At least one argument is needed; a search across every receipt
	is not what this is for.
	"""
	if not _may_create():
		return {"allowed": False, "receipts": []}
	if not (purchase_order or item_code or supplier):
		frappe.throw(_("Say which order, item or supplier."))
	conditions = ["p.docstatus = 1", "p.is_return = 0"]
	values = {"limit": CHOICE_LIMIT}
	if purchase_order:
		conditions.append("c.purchase_order = %(purchase_order)s")
		values["purchase_order"] = purchase_order
	if item_code:
		conditions.append("c.item_code = %(item_code)s")
		values["item_code"] = item_code
	if supplier:
		conditions.append("p.supplier = %(supplier)s")
		values["supplier"] = supplier
	receipts = frappe.db.sql(
		f"""
		select distinct p.name, p.posting_date, p.supplier, p.supplier_name
		from `tabPurchase Receipt` p
		join `tabPurchase Receipt Item` c on c.parent = p.name
		where {" and ".join(conditions)}
		order by p.posting_date desc, p.name desc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)
	out = []
	for receipt in receipts:
		if not frappe.has_permission("Purchase Receipt", "read", doc=receipt.name):
			continue
		lines = [
			{
				key: fact[key]
				for key in (
					"purchase_receipt_item",
					"item_code",
					"item_name",
					"returnable",
					"uom",
					"purchase_order",
					"project",
					"line_kind",
				)
			}
			for fact in core.receipt_facts(receipt.name)
			if fact["returnable"] > 0
			and (not purchase_order or fact["purchase_order"] == purchase_order)
			and (not item_code or fact["item_code"] == item_code)
		]
		if lines:
			out.append(dict(receipt, posting_date=str(receipt.posting_date), lines=lines))
	return {"allowed": True, "receipts": out}


@frappe.whitelist(methods=["GET", "POST"])
def make_from_receipt(source_name, target_doc=None, args=None):
	"""An unsaved Supplier Return for a Purchase Receipt (``frappe.model.open_mapped_doc``).

	``args`` may carry ``filtered_children`` (receipt line names: the rows ticked in the grid),
	``purchase_order`` and ``item_code``. Nothing is saved.
	"""
	_require_create()
	if isinstance(args, str):
		args = json.loads(args or "{}")
	# ``frappe.model.mapper.make_mapped_doc`` calls this with the source alone and leaves the
	# caller's ``args`` on ``frappe.flags``.
	args = args or getattr(frappe.flags, "args", None) or {}
	return core.build_from_receipt(
		source_name,
		only_lines=_list(args.get("filtered_children")),
		purchase_order=args.get("purchase_order") or None,
		item_code=args.get("item_code") or None,
	)


@frappe.whitelist(methods=["GET", "POST"])
def lines_for_receipt(purchase_receipt, name=None):
	"""The receipt's lines that may still go back, for the form's Get Items From Receipt.

	``name`` is the Supplier Return being edited, whose own lines do not count against it.
	"""
	_require_create()
	frappe.has_permission("Purchase Receipt", "read", doc=purchase_receipt, throw=True)
	header = core._receipt_header(purchase_receipt)
	facts = [fact for fact in core.receipt_facts(purchase_receipt, name or None) if fact["returnable"] > 0]
	return {
		"supplier": header.supplier,
		"company": header.company,
		"contact_email": core._supplier_email(header.supplier),
		"lines": facts,
	}


@frappe.whitelist(methods=["POST"])
def start_from_scan(purchase_receipt_item, qty, reason, condition_notes=None, requested_resolution=None):
	"""Save a Draft Supplier Return for one receipt line, from the Stock Scan page.

	The person at the shelf says what is wrong and how many; purchasing sends the request. Returns
	``{"name"}``.
	"""
	_require_create()
	receipt = frappe.db.get_value("Purchase Receipt Item", purchase_receipt_item, "parent")
	if not receipt:
		frappe.throw(_("That receipt line doesn't exist."))
	doc = core.build_from_receipt(receipt, only_lines=[purchase_receipt_item])
	row = doc.items[0]
	row.qty = flt(qty)
	row.reason = reason
	row.condition_notes = (condition_notes or "").strip() or None
	doc.set("items", [row])
	if requested_resolution in rules.RESOLUTIONS:
		doc.requested_resolution = requested_resolution
	doc.insert()
	return {"name": doc.name}


# ---------------------------------------------------------------------------
# Where a return stands
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET", "POST"])
def state(name):
	"""What the form draws: the steps the caller may take, days waiting, the stock and money sides."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	money = core._money_documents(doc)
	plan = core._plan(doc)
	purchase_return = core._document("Purchase Receipt", doc.purchase_return, [])
	may_write = bool(doc.has_permission("write"))
	roles = frappe.get_roles()
	# A bill's number is shown only to somebody who may read bills. The Create Debit Note button
	# needs it, and that button is for the money roles.
	may_read_bills = bool(frappe.has_permission("Purchase Invoice", "read"))
	receipt_live = core.receipt_is_live(doc.purchase_receipt)
	return {
		"status": doc.status,
		"actions": _actions(doc, roles, money, receipt_live) if may_write else [],
		"waiting_on": rules.waiting_on(doc.status, doc.requested_resolution),
		"days_waiting": rules.days_waiting(doc.status_changed_on or doc.creation, nowdate()),
		"needs_purchase_return": receipt_live and rules.needs_stock_action(doc.status, plan, purchase_return),
		"purchase_return_is_draft": bool(purchase_return and cint(purchase_return.docstatus) == 0),
		"takes_stock": plan["takes_stock"],
		"record_only_note": plan["note"],
		"receipt_canceled": not receipt_live,
		"receipt_gone": "" if receipt_live else core._receipt_gone(doc.purchase_receipt),
		"money_backed": money["backed"],
		"money_note": money["note"],
		"received": flt(money["amount"]),
		"owed": flt(money["owed"]),
		"locked": doc.status in rules.FINAL,
		"typed_by_hand": doc.money_source == rules.TYPED,
		"not_backed": rules.NOT_BACKED,
		"bills": [
			{
				"name": row.name,
				"bill_no": row.bill_no,
				"posting_date": str(row.posting_date),
				"status": row.status,
			}
			for row in core.bills_for(doc)
		]
		if doc.status in rules.GONE and may_read_bills
		else [],
		"may_money": rules.may("refund", roles) and may_write,
		"may_create": rules.may("send_back", roles) and may_write,
	}


def _actions(doc, roles, money, receipt_live):
	"""The steps offered. With the receipt canceled, only the ways out and the money that is
	already owed: ``supplier_returns._advance`` refuses the rest."""
	# "No More Is Coming" is held back only while a Debit Note says the supplier still owes.
	owed_on_note = money["owed"] if money["source"] == rules.FROM_DEBIT_NOTE else 0.0
	actions = rules.allowed_actions(doc.status, roles, money["backed"], money["amount"], owed_on_note)
	if receipt_live:
		return actions
	keep = set(core.WAYS_OUT) | {"refund", "credit", "settle"}
	return [action for action in actions if action in keep]


@frappe.whitelist(methods=["GET", "POST"])
def linked(doctype, name):
	"""The Supplier Returns a Purchase Receipt, Purchase Invoice or Payment Entry belongs to."""
	if doctype not in ("Purchase Receipt", "Purchase Invoice", "Payment Entry") or not name:
		return []
	if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
		return []
	if not frappe.has_permission(DOCTYPE, "read"):
		return []
	return core.linked_to(doctype, name)


# ---------------------------------------------------------------------------
# The steps. Each is one click in the form.
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def mark_requested(name, how, notes=None):
	"""Requested another way (phone, in person, the supplier's website). Email is ``send_request``."""
	if how == "Email":
		frappe.throw(_("Use Send Return Request to email the supplier."))
	return core.mark_requested(name, how, notes).status


@frappe.whitelist(methods=["POST"])
def record_approval(name, supplier_return_number=None, notes=None):
	return core.record_approval(name, supplier_return_number, notes).status


@frappe.whitelist(methods=["POST"])
def record_denial(name, notes=None):
	return core.record_denial(name, notes).status


@frappe.whitelist(methods=["POST"])
def mark_sent_back(name, return_method, carrier=None, tracking_number=None, sent_back_on=None):
	return core.mark_sent_back(name, return_method, carrier, tracking_number, sent_back_on)


@frappe.whitelist(methods=["POST"])
def record_money(name, kind, amount, received_on, reference, notes=None, more_expected=0):
	"""A refund or credit typed by hand: only while no Debit Note or Payment Entry is linked.
	``more_expected``: this is one part, and the return keeps waiting for the rest."""
	return core.record_money(name, kind, amount, received_on, reference, notes, cint(more_expected)).status


@frappe.whitelist(methods=["POST"])
def settle(name, reason=None):
	"""No More Is Coming: finish the return at what has come back so far."""
	return core.settle(name, reason).status


@frappe.whitelist(methods=["POST"])
def record_replacement(name, replacement_receipt=None, received_on=None, notes=None):
	return core.record_replacement(name, replacement_receipt, received_on, notes).status


@frappe.whitelist(methods=["POST"])
def close_without_credit(name, reason=None):
	return core.close_without_credit(name, reason).status


@frappe.whitelist(methods=["POST"])
def cancel_return(name, reason=None):
	return core.cancel_return(name, reason).status


@frappe.whitelist(methods=["POST"])
def reopen(name, reason=None):
	return core.reopen(name, reason).status


@frappe.whitelist(methods=["POST"])
def refresh(name):
	"""Read the linked documents again (the form's "Check the documents")."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	return core.sync(name)


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET", "POST"])
def request_draft(name):
	"""The return request as it would go: recipients to choose from, subject, editable body."""
	doc = core.load_for(name, "request")
	choices = core.recipients(doc)
	draft = core.draft_request(doc, core.greeting_name(doc, choices[0]["email"]) if choices else "")
	return {
		"recipients": choices,
		"subject": draft["subject"],
		"body": draft["body"],
		"template_found": draft["template_found"],
		"template": rules.EMAIL_TEMPLATE,
		"lines_marker": rules.LINES_MARKER,
		"photos": len(core._photo_attachments(doc)),
		"can_send": doc.status == rules.DRAFT,
	}


@frappe.whitelist(methods=["POST"])
def request_preview(name, subject, body):
	"""The edited message inside the letterhead, exactly as the supplier will see it."""
	doc = core.load_for(name, "request")
	return {"html": core.wrap_request(subject or "", core.compose_request(doc, body))}


@frappe.whitelist(methods=["POST"])
def send_request(name, recipient, subject, body, cc=None, attach_photos=0):
	"""Email the supplier. Only from a person's click in the Send dialog: see the module docstring."""
	if not _from_a_person():
		frappe.throw(
			_(
				"A return request is sent from the Supplier Return form, signed in as yourself. API keys, tokens and background jobs can't send it."
			),
			frappe.PermissionError,
		)
	return core.send_request(name, recipient, subject, body, _list(cc), cint(attach_photos))


# ---------------------------------------------------------------------------
# ERPNext's own documents
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def make_purchase_return(name):
	"""Draft the standard Purchase Return for this return and link it. The person submits it."""
	return core.make_purchase_return(name)


@frappe.whitelist(methods=["POST"])
def make_debit_note(name, purchase_invoice):
	"""Draft the standard Debit Note against a bill and link it. The person submits it."""
	return core.make_debit_note(name, purchase_invoice)


@frappe.whitelist(methods=["GET", "POST"])
def refund_source(name):
	"""The invoice ERPNext's ``get_payment_entry`` should be called with for this return's refund."""
	return core.refund_source(name)


@frappe.whitelist(methods=["POST"])
def link_purchase_return(name, purchase_return):
	return core.link_purchase_return(name, purchase_return)


@frappe.whitelist(methods=["POST"])
def link_debit_note(name, debit_note):
	return core.link_debit_note(name, debit_note)


@frappe.whitelist(methods=["POST"])
def link_payment_entry(name, payment_entry, amount=None):
	"""Record a refund Payment Entry on this return. ``amount``: how much of it is for this
	return (blank: whatever other returns have not already counted)."""
	return core.link_payment_entry(name, payment_entry, amount)

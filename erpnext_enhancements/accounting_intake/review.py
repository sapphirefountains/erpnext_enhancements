# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted review actions for the Document Intake queue.

Two reviewer roles, two gates: the inventory clerk (Stock Manager) approves any
proposed new Items (``approve_items``), then the accountant (Accounts Manager)
approves the document (``approve_document``) — which moves it to ``Approved``.
Approving a document enqueues ``actions.base.post_document``, the per-type posting
handler that turns it into a **draft** (docstatus 0) ERPNext record — so approval has
posting side effects (an enqueued background job); nothing is *submitted* here."""

import html

import frappe
from frappe import _

from erpnext_enhancements.accounting_intake.audit import log_intake

_ITEM_ROLES = {"Stock Manager", "System Manager"}
_APPROVE_ROLES = {"Accounts Manager", "System Manager"}
_PARTY_ACTIONS = {"Create Purchase Invoice", "Create Purchase Receipt", "Create Payment Entry"}


def _require(roles):
	if not (set(frappe.get_roles()) & roles):
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)


@frappe.whitelist()
def approve_items(docname):
	"""Inventory clerk: create Items for rows marked Approved, then advance the
	document to Needs Review once no proposed Item is still Pending."""
	_require(_ITEM_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	to_create = [
		r
		for r in doc.line_items
		if r.new_item_proposed and r.item_review_status == "Approved" and not r.matched_item
	]
	problems = _naming_problems(to_create)
	if problems:
		frappe.throw(
			"<br><br>".join(
				[
					*problems,
					_(
						"Nothing was created. Fix these lines and press <b>Create Approved Items</b> "
						"again, or create the Item from the Item list and set it as the line's "
						"<b>Matched Item</b>."
					),
				]
			),
			title=_("Item naming"),
		)
	created = 0
	for row in to_create:
		row.matched_item = _create_item(row)
		row.new_item_proposed = 0
		created += 1
	pending = [r for r in doc.line_items if r.new_item_proposed and (r.item_review_status or "Pending") == "Pending"]
	doc.item_reviewed_by = frappe.session.user
	if not pending:
		doc.status = "Needs Review"
	doc.save(ignore_permissions=True)
	log_intake("Item Review", "Success", accounting_document=docname, detail=f"{created} item(s) created")
	return {"created": created, "status": doc.status, "pending": len(pending)}


def _proposed_code_and_name(row):
	"""The code and name a line's new Item would get.

	The code is the Stock Manager's *Proposed Item Code* (v1.532.0). Without one it falls
	back to the name, which is what this module always did and which the Item naming guard
	refuses from POL-0602's effective date: a receipt line carries a description and no part
	number, so only the reviewer can supply the code the SOP asks for.
	"""
	name = (row.proposed_item_name or row.description or "Item")[:140]
	code = (row.get("proposed_item_code") or "").strip()[:140] or name
	return code, name


def _existing_item(code, name):
	"""An Item this line already names, by code, by its name used as a code, or by name.

	The filter-dict form of ``db.exists`` on purpose: given a bare name equal to the doctype,
	v16 returns it unchecked (the Single shortcut), so ``exists("Item", "Item")`` -- reachable
	through the ``"Item"`` name fallback -- would claim an Item that does not exist.
	"""
	for candidate in dict.fromkeys((code, name)):
		if frappe.db.exists("Item", {"name": candidate}):
			return candidate
	return frappe.db.get_value("Item", {"item_name": name}, "name")


def _naming_problems(rows):
	"""One message per line the Item naming guard would refuse, before anything is created.

	The guard (``inventory_enhancements.item_naming_guard``; Nik, 2026-09-24, TASK-2026-02238)
	applies here, because a person is approving a new Item inside a web request, so
	``ignore_naming_guard`` is not set on the insert. Checking first, with the guard's own
	rule, is what lets the refusal name the intake line and the field to fix instead of an
	Item form nobody opened, and it refuses the whole batch before the first insert rather
	than part way through. Codes approved earlier in the same batch count as existing, the way
	they would at the second insert. The same code typed on two lines with different names is
	refused too: the second insert would silently link to the first line's new Item, which is
	right for one part bought twice and wrong for a mistyped code. Silent before the guard is
	in force.
	"""
	from erpnext_enhancements.inventory_enhancements import item_naming_guard as guard
	from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

	if not rows or not guard.in_force():
		return []
	existing = frappe.get_all("Item", pluck="name")
	claimed = {}
	problems = []
	for row in rows:
		code, name = _proposed_code_and_name(row)
		if _existing_item(code, name):
			continue
		label = _("Line {0} ({1}):").format(row.idx, html.escape(name))
		earlier = claimed.get(code)
		if earlier and earlier[1] != name:
			problems.append(
				_(
					"{0} has the same Proposed Item Code as line {1} ({2}), <b>{3}</b>. One code makes "
					"one Item: if they are the same part, give both lines the same name; if not, give "
					"this line its own code."
				).format(label, earlier[0], html.escape(earlier[1]), html.escape(code))
			)
			continue
		findings = rules.blocking_findings(code, name, existing)
		if not findings:
			existing.append(code)
			claimed.setdefault(code, (row.idx, name))
			continue
		if not (row.get("proposed_item_code") or "").strip():
			problems.append(
				_(
					"{0} enter the vendor's part number, or the right CON-, PDT- or SRV- code, in "
					"<b>Proposed Item Code</b>. Without one the name would also be the code, which "
					"POL-0602 does not allow for a new Item."
				).format(label)
			)
			continue
		problems.extend(f"{label} {guard.finding_message(code, f)}" for f in findings)
	return problems


def _create_item(row):
	"""Create the Item a reviewer approved, or return the one the line already names.

	The Item naming guard stays on for the insert; :func:`_naming_problems` has already
	checked every line against it, so it is a second lock rather than the message a person
	sees.
	"""
	code, name = _proposed_code_and_name(row)
	existing = _existing_item(code, name)
	if existing:
		return existing
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": name,
			"item_group": row.proposed_item_group or _default_group(),
			"stock_uom": row.proposed_uom or "Nos",
			"is_stock_item": 1 if row.is_stock_item else 0,
		}
	)
	item.flags.ignore_mandatory = True
	item.insert(ignore_permissions=True)
	return item.name


def _default_group():
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"


def _validate_for_approval(doc):
	issues = []
	if (doc.proposed_action or "") in ("", "Ignore"):
		issues.append(_("Choose a proposed action before approving."))
	pending = [r for r in doc.line_items if r.new_item_proposed and (r.item_review_status or "Pending") == "Pending"]
	if pending:
		issues.append(_("{0} proposed Item(s) still need inventory-clerk review.").format(len(pending)))
	if (doc.proposed_action or "") in _PARTY_ACTIONS and not doc.party:
		issues.append(_("Set the Party before approving."))
	issues.extend(_reimbursement_issues(doc))
	return issues


def _reimbursement_issues(doc):
	"""Refuse a reimbursement that cannot say who is being reimbursed.

	Checked here rather than only in the handler, for the same reason the party
	gate above exists: the handler runs in a **background job**, so a problem it
	finds surfaces as a `Failed` document with a traceback rather than as a
	sentence next to the button somebody just pressed. It also burns a retry
	attempt against `retry_limit` on the way.

	`Paid By` is mandatory on the form, and this is the API-side twin -- Frappe
	does not enforce `mandatory_depends_on` against a direct write, and `Document
	Intake` is writable by `Accounts User`.
	"""
	if (doc.proposed_action or "") != "Create Reimbursement Bill":
		return []

	from erpnext_enhancements.accounting_intake.actions import receipt_expense

	employee = receipt_expense.payer(doc)
	if not employee:
		return [
			_(
				"Set <b>Paid By</b> before approving — a reimbursement has to name the person who "
				"paid, and it must be an active Employee."
			)
		]
	if not receipt_expense.reimbursement_supplier(employee):
		name = frappe.db.get_value("Employee", employee, "employee_name") or employee
		return [
			_(
				"{0} has no <b>Reimbursement Supplier</b> on their Employee record, so there is "
				"nothing to bill this to. Set one, or use <b>Create Purchase Invoice</b> if this "
				"went on a company card."
			).format(name)
		]
	return []


@frappe.whitelist()
def approve_document(docname):
	"""Accountant: approve the proposed action and move to Approved, then enqueue
	``actions.base.post_document`` to create the draft ERPNext record."""
	_require(_APPROVE_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	# Refuse a second approval (double-click, or approving an already-posted doc): each call
	# enqueues post_document, and though the post itself is now filelock-guarded, a wasted
	# duplicate job is avoidable here.
	if doc.created_docname or doc.status in ("Approved", "Posting", "Posted"):
		frappe.throw("This document has already been approved.")
	issues = _validate_for_approval(doc)
	if issues:
		frappe.throw("<br>".join(issues))
	doc.reviewed_by = frappe.session.user
	doc.reviewed_on = frappe.utils.now_datetime()
	doc.status = "Approved"
	doc.save(ignore_permissions=True)
	frappe.enqueue(
		"erpnext_enhancements.accounting_intake.actions.base.post_document",
		queue="long",
		enqueue_after_commit=True,
		docname=docname,
	)
	log_intake("Approve", "Success", accounting_document=docname, detail=doc.proposed_action)
	return {"status": doc.status}


@frappe.whitelist()
def reject_document(docname, reason=None):
	_require(_APPROVE_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	doc.status = "Rejected"
	if reason:
		doc.review_notes = reason
	doc.reviewed_by = frappe.session.user
	doc.reviewed_on = frappe.utils.now_datetime()
	doc.save(ignore_permissions=True)
	log_intake("Approve", "Skipped", accounting_document=docname, detail="Rejected")
	return {"status": doc.status}


@frappe.whitelist()
def reprocess(docname):
	"""Re-run extraction for a stuck/failed/changed document."""
	_require(_APPROVE_ROLES | _ITEM_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	doc.db_set("status", "Received", update_modified=False)
	frappe.enqueue(
		"erpnext_enhancements.accounting_intake.intake.run_extraction",
		queue="long",
		enqueue_after_commit=True,
		docname=docname,
	)
	return {"status": "Received"}

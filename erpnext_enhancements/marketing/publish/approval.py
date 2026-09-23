# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Social Post actions: submit, approve, send back, cancel (TASK-2026-01486).

Thin endpoints over the rules in ``workflow.py``, which says who may do what and why. All POST,
none guest. Each loads the post and requires permission to edit it before anything else.

**Approve writes the outbox rows in the same transaction as the approval** (``outbox.enqueue``).
If the outbox refuses the post, the request fails and the approval rolls back with it: nothing is
ever approved that was not queued, or queued that was not approved.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, get_datetime, now_datetime

from erpnext_enhancements.marketing.publish import outbox, workflow

POST = "Social Post"


def browser_request():
	"""Whether this request comes from a person's login (``workflow.signed_in_browser``)."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return False
	return workflow.signed_in_browser(frappe.session, request.headers.get("Authorization"))


def _load(post):
	doc = frappe.get_doc(POST, post)
	doc.check_permission("write")
	return doc


def _refuse(doc, action, problems):
	if problems:
		frappe.throw(workflow.refusal(doc.name, action, problems), title=_("Not done"))


def _set_status(doc, status, **values):
	"""The one way a status reaches a save: the controller refuses it otherwise."""
	doc.flags.status_change = True
	doc.status = status
	doc.update(values)
	doc.save()


def _content_problems(doc):
	from erpnext_enhancements.marketing.publish.sweeper import FrappeStore, post_dict, post_parts

	targets, media = post_parts(doc)
	accounts = FrappeStore().accounts([t["social_account"] for t in targets])
	return outbox.content_problems(post_dict(doc), targets, media, accounts)


def _note(doc, text, reason=None):
	if reason and reason.strip():
		text = f"{text}: {escape_html(reason.strip())}"
	doc.add_comment("Comment", text)


@frappe.whitelist(methods=["POST"])
def submit_for_approval(post):
	"""Draft -> Pending Approval. Anyone who may edit the post."""
	doc = _load(post)
	_refuse(doc, "submitted for approval", workflow.submit_problems(doc.as_dict(), _content_problems(doc)))
	_set_status(doc, outbox.POST_PENDING_APPROVAL)
	return {"status": doc.status}


@frappe.whitelist(methods=["POST"])
def approve(post, modified=None):
	"""Pending Approval -> Approved, with the outbox rows written in the same transaction.

	``modified`` is the post's ``modified`` as the approver saw it. A post edited since then is
	refused, so what is approved is what was reviewed.
	"""
	doc = _load(post)
	user = frappe.session.user
	_refuse(
		doc,
		"approved",
		workflow.approval_problems(
			doc.as_dict(),
			user,
			set(frappe.get_roles(user)),
			browser_request(),
			unchanged=bool(modified) and get_datetime(modified) == get_datetime(doc.modified),
		),
	)
	from erpnext_enhancements.marketing.publish.sweeper import FrappeStore

	_set_status(doc, outbox.POST_APPROVED, approver=user, approved_at=now_datetime())
	try:
		jobs = outbox.enqueue(FrappeStore(), doc.name, now_datetime())
	except ValueError as exc:
		# Raising rolls the whole request back, the approval included.
		frappe.throw(str(exc), title=_("Not done"))
	return {"status": frappe.db.get_value(POST, doc.name, "status"), "jobs": jobs}


@frappe.whitelist(methods=["POST"])
def send_back(post, reason=None):
	"""Pending Approval -> Draft: the approver wants changes, or the author withdraws it."""
	doc = _load(post)
	_refuse(doc, "sent back", workflow.send_back_problems(doc.as_dict()))
	_set_status(doc, outbox.POST_DRAFT)
	_note(doc, _("Sent back to Draft"), reason)
	return {"status": doc.status}


@frappe.whitelist(methods=["POST"])
def cancel(post, reason=None):
	"""Stop a post. Anyone who may edit it; ``outbox.cancel_post`` says what stops."""
	doc = _load(post)
	_refuse(doc, "canceled", workflow.cancel_problems(doc.as_dict()))
	if doc.status in outbox.EDITABLE_POST_STATUSES:
		_set_status(doc, outbox.POST_CANCELED)  # nothing was queued: a plain, versioned save
		status = doc.status
	else:
		from erpnext_enhancements.marketing.publish.sweeper import FrappeStore

		try:
			status = outbox.cancel_post(FrappeStore(), doc.name, frappe.session.user, now_datetime())
		except ValueError as exc:
			frappe.throw(str(exc), title=_("Not done"))
	_note(doc, _("Canceled"), reason)
	return {"status": status}

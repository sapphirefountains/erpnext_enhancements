"""Keep ERPNext's hidden party fields equal to the visible ones (form kit P3.2, TASK-2026-02755).

On Opportunity the form shows **Primary Contact** (``primary_contact``, this app's field) and hides
ERPNext's own **Contact Person** (``contact_person``). People pick the visible one, but ERPNext's
stock code reads the hidden one: *Create > Quotation* (``make_quotation``) maps fields by name, so
the Quotation's contact is the Opportunity's ``contact_person``, and the Prospect's opportunity row
copies it too. When the two disagree, the Quotation goes to someone other than the person on the
deal. Before this rule 9 of the 850 Opportunities on production held two different contacts.

Which pairs are kept equal is the Party block's ``mirrors`` in ``form_kit/blocks.py``:
``{visible: hidden}``. Today that is only Opportunity's ``primary_contact -> contact_person``.

**Not the address pair.** ERPNext's ``customer_address`` was hidden too, and the plan paired it
with Primary Address. Since v1.633.0 it is the visible **Billing Address** (Nik, 2026-10-13), a
different fact from Primary Address, which is the job site, and the Quotation's billing address is
exactly what *Create > Quotation* copies it into. Copying the job site over it would undo that
decision, so the two addresses are left independent.

**Which side wins** (:func:`decide`), when the two differ after a save:

1. On a saved record, a visible field changed in this save wins, cleared included: the person
   chose it.
2. Otherwise a hidden field changed in this save to a value wins and is copied up. That is code
   writing the stock field: ERPNext's form sets ``contact_person`` to the new account's contact
   when the account changes, and an API caller or a mapping may know only the stock field. A
   hidden field *cleared* by code never clears the visible one.
3. Otherwise the visible field wins when it holds a value, and the hidden one is copied up when
   it is the only one set. A new record has no "changed", so it starts here.

A value is copied only when the record it names exists. Runs on ``before_save``, ahead of
``sync_contact.refresh_primary_contact_copies`` (hooks.py), so a contact copied up gets its title,
phone and email the same save. Defensive the way ``doc_events`` must be (CLAUDE.md): a field the
meta does not have yet, during ERPNext's own test bootstrap, is skipped, and the hook never
raises, because a mirror is not worth a failed save.

The rows that disagree today are listed by the *Party Mirror Preview* report, a dry run built on
:func:`plan_stored`. Fixing them waits for Nik's approval; no patch writes them yet.
"""

import frappe

from erpnext_enhancements.form_kit.blocks import mirrors_for

#: What :func:`decide` did. ``None`` when the pair already agrees.
COPY_DOWN = "visible copied to hidden"
COPY_UP = "hidden copied to visible"
CLEARED = "visible cleared, hidden cleared"


def decide(visible, hidden, before=None):
	"""``(visible, hidden, rule)`` after the rule above. ``before`` is ``(visible, hidden)`` as
	stored before this save, or ``None`` for a new record or a stored row being reviewed."""
	visible = visible or ""
	hidden = hidden or ""
	if visible == hidden:
		return visible, hidden, None
	if before is not None:
		was_visible, was_hidden = (before[0] or ""), (before[1] or "")
		if visible != was_visible:
			return visible, visible, (COPY_DOWN if visible else CLEARED)
		if hidden != was_hidden and hidden:
			return hidden, hidden, COPY_UP
	if visible:
		return visible, visible, COPY_DOWN
	return hidden, hidden, COPY_UP


def plan_stored(visible, hidden):
	"""What :func:`decide` would do to a stored row with no save in progress (the dry run)."""
	return decide(visible, hidden, None)


def keep_in_step(doc, method=None):
	"""``before_save`` on every doctype with a Party mirror (Opportunity). Never raises."""
	try:
		doctype = getattr(doc, "doctype", None)
		mirrors = mirrors_for(doctype)
		if not mirrors:
			return
		meta = frappe.get_meta(doctype)
		before = None
		is_new = getattr(doc, "is_new", None)
		if callable(is_new) and not is_new():
			before = doc.get_doc_before_save()
		for visible_field, hidden_field in mirrors.items():
			if not (meta.has_field(visible_field) and meta.has_field(hidden_field)):
				continue
			visible = getattr(doc, visible_field, None) or ""
			hidden = getattr(doc, hidden_field, None) or ""
			stored = None
			if before is not None:
				stored = (before.get(visible_field) or "", before.get(hidden_field) or "")
			new_visible, new_hidden, rule = decide(visible, hidden, stored)
			if rule is None:
				continue
			if new_visible and not _exists(meta, visible_field, new_visible):
				continue
			doc.set(visible_field, new_visible)
			doc.set(hidden_field, new_hidden)
	except Exception:
		try:
			frappe.log_error(
				title="Party mirror not kept in step",
				message=f"{getattr(doc, 'doctype', None)} {getattr(doc, 'name', None)}",
			)
		except Exception:
			pass


def _exists(meta, fieldname, value):
	"""Whether the record a Link value names exists. A non-Link field has nothing to check."""
	df = meta.get_field(fieldname)
	target = getattr(df, "options", None) if df is not None else None
	if not target or getattr(df, "fieldtype", None) != "Link":
		return True
	return bool(frappe.db.exists(target, value))

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

1. **A new record**: the visible field when it holds a value; otherwise the hidden one, set by a
   mapping or an API insert, is copied up.
2. **A saved record, visible field changed in this save**: it wins, and an emptied one empties
   the hidden one too. Note that "emptied" is what reaches ``before_save``, not what the person
   typed: ERPNext's ``Opportunity.validate`` runs ``map_fields`` first, which refills an empty
   ``primary_contact`` from the party's own ``primary_contact`` (a Customer has one), so clearing
   it on a Customer's deal usually ends with the account's primary contact in both fields.
3. **A saved record, only the hidden field changed**: a new value is copied up (ERPNext's form sets
   ``contact_person`` to the new account's contact when the account changes; an API caller may
   know only the stock field). A hidden field emptied by code never empties the visible one: the
   shown contact is copied back down.
4. **A saved record where neither changed**, or whose stored copy cannot be read: left exactly as
   it is. A pair that already disagreed waits for the approved fix; a routine save (a board move,
   a guided step, ERPNext's daily ``auto_close_opportunity``, ``set_multiple_status``) never
   rewrites it.

A value is copied only when the record it names exists. **When the rule changes the hidden
Contact Person it also refreshes ERPNext's copies of it** (:data:`DETAILS`): ``contact_display``,
``contact_mobile`` and ``contact_email``, or empties them with it. v16 fills ``fetch_from`` values
in ``_validate_links``, *before* ``before_save``, so ``contact_email`` (fetched from
``contact_person.custom_email`` by a Property Setter) would otherwise still hold the old person's
email, and the other two are set only by the form's client trigger. *Create > Quotation* copies
all three by name.

Runs on ``before_save``, ahead of ``sync_contact.refresh_primary_contact_copies`` (hooks.py), so a
contact copied up gets its title, phone and email the same save. Defensive the way ``doc_events``
must be (CLAUDE.md): a field the meta does not have yet, during ERPNext's own test bootstrap, is
skipped, and the hook never raises, because a mirror is not worth a failed save.

The rows that disagree today are listed by the *Party Mirror Preview* report, a dry run built on
:func:`plan_stored`. Fixing them waits for Nik's approval; no patch writes them yet.
"""

import frappe

from erpnext_enhancements.form_kit.blocks import mirrors_for

#: What :func:`decide` did. ``None`` when it left the pair as it was.
COPY_DOWN = "visible copied to hidden"
COPY_UP = "hidden copied to visible"
CLEARED = "visible cleared, hidden cleared"

#: ERPNext's copies of a hidden contact field, and the Contact field each is read from:
#: ``{doctype: {hidden field: {copy: Contact field}}}``. ``contact_display`` and ``contact_mobile``
#: as v16 ``frappe.contacts.doctype.contact.contact.get_contact_details`` fills them (full_name,
#: mobile_no); ``contact_email`` from ``custom_email``, as this app's Property Setter
#: ``contact_email.fetch_from = contact_person.custom_email`` fetches it.
DETAILS = {
	"Opportunity": {
		"contact_person": {
			"contact_display": "full_name",
			"contact_mobile": "mobile_no",
			"contact_email": "custom_email",
		},
	},
}


def _settle(visible, hidden):
	"""The pair made equal with nothing to say which changed: the visible field, else the hidden."""
	if visible:
		return visible, visible, COPY_DOWN
	return hidden, hidden, COPY_UP


def decide(visible, hidden, before=None, new=False):
	"""``(visible, hidden, rule)`` after the rule above.

	``new`` is True for a record being inserted. ``before`` is ``(visible, hidden)`` as stored
	before this save, or ``None`` when it cannot be read; a saved record with no ``before`` is
	left alone.
	"""
	visible = visible or ""
	hidden = hidden or ""
	if visible == hidden:
		return visible, hidden, None
	if new:
		return _settle(visible, hidden)
	if before is None:
		return visible, hidden, None
	was_visible, was_hidden = (before[0] or ""), (before[1] or "")
	if visible != was_visible:
		return visible, visible, (COPY_DOWN if visible else CLEARED)
	if hidden != was_hidden:
		if hidden:
			return hidden, hidden, COPY_UP
		return visible, visible, COPY_DOWN
	return visible, hidden, None


def plan_stored(visible, hidden):
	"""What the approved fix would do to a stored row: the pair made equal, visible first."""
	visible = visible or ""
	hidden = hidden or ""
	if visible == hidden:
		return visible, hidden, None
	return _settle(visible, hidden)


def keep_in_step(doc, method=None):
	"""``before_save`` on every doctype with a Party mirror (Opportunity). Never raises."""
	try:
		doctype = getattr(doc, "doctype", None)
		mirrors = mirrors_for(doctype)
		if not mirrors:
			return
		meta = frappe.get_meta(doctype)
		is_new = getattr(doc, "is_new", None)
		new = bool(is_new()) if callable(is_new) else False
		before = None if new else doc.get_doc_before_save()
		for visible_field, hidden_field in mirrors.items():
			if not (meta.has_field(visible_field) and meta.has_field(hidden_field)):
				continue
			visible = getattr(doc, visible_field, None) or ""
			hidden = getattr(doc, hidden_field, None) or ""
			stored = None
			if before is not None:
				stored = (before.get(visible_field) or "", before.get(hidden_field) or "")
			new_visible, new_hidden, rule = decide(visible, hidden, stored, new=new)
			if rule is None:
				continue
			if new_visible and not _exists(meta, visible_field, new_visible):
				continue
			doc.set(visible_field, new_visible)
			doc.set(hidden_field, new_hidden)
			if new_hidden != hidden:
				_refresh_details(doc, meta, doctype, hidden_field, new_hidden)
	except Exception:
		try:
			frappe.log_error(
				title="Party mirror not kept in step",
				message=frappe.get_traceback(),
				reference_doctype=getattr(doc, "doctype", None),
				reference_name=getattr(doc, "name", None),
			)
		except Exception:
			pass


def _refresh_details(doc, meta, doctype, hidden_field, value):
	"""Set ERPNext's copies of the hidden contact from ``value``'s Contact, or empty them."""
	details = (DETAILS.get(doctype) or {}).get(hidden_field) or {}
	fields = [field for field in details if meta.has_field(field)]
	if not fields:
		return
	row = {}
	if value:
		source = frappe.get_meta("Contact")
		columns = sorted({details[field] for field in fields if source.has_field(details[field])})
		if columns:
			row = frappe.db.get_value("Contact", value, columns, as_dict=True) or {}
	for field in fields:
		doc.set(field, row.get(details[field]) or "")


def _exists(meta, fieldname, value):
	"""Whether the record a Link value names exists. A non-Link field has nothing to check."""
	df = meta.get_field(fieldname)
	target = getattr(df, "options", None) if df is not None else None
	if not target or getattr(df, "fieldtype", None) != "Link":
		return True
	return bool(frappe.db.exists(target, value))

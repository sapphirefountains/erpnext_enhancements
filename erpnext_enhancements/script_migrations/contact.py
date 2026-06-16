"""Migrated Contact Server Script, wired via ``hooks.py`` doc_events["Contact"].

Hook wiring (see ``hooks.py``):
  * ``validate`` -> :func:`set_full_name_and_role`

Originally a Frappe "Server Script" stored only in the site DB ("Contact - Set
Full Name and Role Title", Contact / Before Save) that left the
``custom_full_name_and_role`` field blank once disabled during the script
migration. Re-implemented here with the agreed format so the field is populated
again for every Contact.

``custom_full_name_and_role`` is the Contact's "title" field (``read_only``,
``unique``). It does NOT drive the record name — Frappe core ``Contact.autoname``
owns that (full name + ``-N`` on collision), and the active controller is the
``crm`` app's ``CustomContact``, so this is a plain ``validate`` hook rather than
an ``override_doctype_class`` (which would collide with crm).
"""

import frappe

COMPANY_INTERNAL = "Sapphire Fountains"


def set_full_name_and_role(doc, method=None):
	"""Populate ``custom_full_name_and_role`` as ``First Last-Party``.

	Format (agreed):
	  * ``First Last`` (blank name parts collapsed), then
	  * ``-{linked Customer/Supplier}`` when the Contact links to one and is not
	    an internal Sapphire Fountains contact.

	The field is ``unique``; if the computed value already belongs to a different
	Contact, a `` (2)`` / `` (3)`` … suffix is appended so the save never fails on
	the unique index.
	"""
	full_name = " ".join(
		part for part in [(doc.first_name or "").strip(), (doc.last_name or "").strip()] if part
	)
	if not full_name:
		# Nothing to build a title from — leave it to core naming.
		return

	# First Customer/Supplier the Contact is linked to (import contacts have one).
	party = None
	for link in doc.get("links") or []:
		if link.link_doctype in ("Customer", "Supplier"):
			party = (link.link_name or "").strip()
			break

	is_internal = (doc.company_name or "").strip() == COMPANY_INTERNAL
	base = f"{full_name}-{party}" if (party and not is_internal) else full_name

	value, n = base, 2
	while frappe.db.exists(
		"Contact", {"custom_full_name_and_role": value, "name": ["!=", doc.name or ""]}
	):
		value = f"{base} ({n})"
		n += 1

	doc.custom_full_name_and_role = value

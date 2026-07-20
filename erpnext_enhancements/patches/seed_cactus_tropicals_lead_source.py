"""One-time migration patch (post_model_sync; listed in patches.txt).

Seeds the two taxonomy records the fountain-move intake conversion depends on:

1. **Lead Source "Cactus & Tropicals"** — stamped onto the created Customer
   (``custom_lead_source``), Lead and Opportunity so every C&T-referred deal is
   filterable in one click.

2. **UTM Source "Existing Customer"** — load-bearing, not cosmetic. erpnext's
   ``Lead.before_insert`` creates its own Contact for every new Lead *unless*
   ``utm_source`` is exactly ``"Existing Customer"`` and ``customer`` is set.
   The conversion engine creates the Contact itself (it has to — the Contact must
   carry the Customer Dynamic Link *before* insert, because this site's
   ``autoname`` reads ``links[0]``), so without this record every intake would
   leave a stray duplicate Contact behind.

Both need an explicit ``set_name``. ``crm_enhancements/doctype/lead_source`` is
``istable: 1`` with no fields and no ``autoname``, so ``naming.set_new_name``
nulls the name and falls through to ``make_autoname("hash")`` — which is exactly
how the junk ``5grjpdb97i`` / ``5grlinu7mj`` rows in ``Value Streams`` got there.
``Document.insert(set_name=...)`` short-circuits that branch entirely
(``document.py:723`` assigns ``validate_name(doctype, set_name)`` and never calls
``set_new_name``), which works on an istable doctype too.

Insert-only and idempotent: an existing record of either name is left alone.
"""

import frappe

from erpnext_enhancements.crm_enhancements.fountain_move import (
	DEFAULT_LEAD_SOURCE,
	EXISTING_CUSTOMER_UTM_SOURCE,
)

#: Doctypes this patch is allowed to touch. The fallback path below interpolates
#: the doctype into a table name, so the set is closed by construction rather
#: than relying on every caller being a literal.
SEEDABLE = ("Lead Source", "UTM Source")


def execute():
	_ensure_named_record("Lead Source", DEFAULT_LEAD_SOURCE)
	_ensure_named_record("UTM Source", EXISTING_CUSTOMER_UTM_SOURCE)


def _ensure_named_record(doctype, record_name):
	"""Create ``record_name`` in ``doctype`` if absent. Never overwrites."""
	if doctype not in SEEDABLE:
		raise ValueError(f"refusing to seed unexpected doctype {doctype!r}")
	if not frappe.db.exists("DocType", doctype):
		# Taxonomy doctype not installed on this site — nothing to seed, and a
		# hard failure here would abort the whole migrate.
		return
	if frappe.db.exists(doctype, record_name):
		return

	try:
		frappe.get_doc({"doctype": doctype}).insert(
			set_name=record_name, ignore_permissions=True
		)
	except Exception:
		# Fall back to a direct insert. These are contentless taxonomy rows
		# (Lead Source has no fields at all), so there is nothing to lose by
		# bypassing the ORM, and a missing record breaks conversion outright.
		frappe.log_error(
			frappe.get_traceback(),
			f"Fountain Move: ORM seed failed for {doctype} '{record_name}', using direct insert",
		)
		frappe.db.sql(
			f"""INSERT INTO `tab{doctype}` (name, creation, modified, modified_by, owner, docstatus)
				VALUES (%s, NOW(), NOW(), 'Administrator', 'Administrator', 0)""",
			(record_name,),
		)

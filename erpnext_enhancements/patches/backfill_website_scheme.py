"""Prefix https:// onto scheme-less CRM website values (v1.324.0).

The ``before_validate`` hook added alongside this patch
(``crm_enhancements.website_cleanup``) fixes a bare domain on the way in — but only
for records somebody saves. The records that need it most are the ones nobody *can*
save: our own ``{Customer,Lead,Opportunity}-website-options`` Property Setters turned
``website`` into a URL field after the data was imported, and frappe re-validates every
URL field on every save, so a stored ``2xlimaging.com`` rejects an edit to the phone
number, the customer group, or anything a background job touches.

Counted on production the day this was written:

    Customer     281 of 739 with a website
    Opportunity   80 of 506
    Lead          14 of 35
    Lead.custom_account_website   9 of 16

Without this patch each of those unfreezes only when a human happens to open it, fails
at nothing (the hook now heals it), and saves — which is fine for the ones people touch
and useless for the rest. With it they are valid on the next migrate.

**Scope is the three doctypes the hook is wired to.** Supplier (24 rows) and Company
carry the same Property Setter and are deliberately left alone: they are outside the CRM
request this came from, and the QuickBooks sync already heals the Supplier save path that
had the cascade (``mapping._heal_invalid_urls``). Repairing data whose entry path is not
also fixed would be half a job in a way that is easy to mistake for a whole one.

**The predicate is the writer's rule, not a SQL guess.** Rows are selected coarsely
(non-empty) and the decision is made by :func:`normalize_website`, the same pure function
the hook calls — so the patch cannot heal a value the hook would have left alone, or
skip one the hook would have fixed.

Writes with ``db.set_value(update_modified=False)`` rather than ``doc.save()``: saving
399 documents would fire ``on_update`` on each — attribution, the contact/address sync,
the Drive folder hooks and the global Triton ``after_save`` — for a string edit, and
several of those enqueue background work.

Idempotent: a healed value contains ``://``, which the rule then leaves alone.
"""

import frappe

from erpnext_enhancements.crm_enhancements.website_cleanup import normalize_website

#: The doctypes whose entry path this release also fixes. Keep in step with the
#: ``before_validate`` wiring in hooks.py.
DOCTYPES = ("Lead", "Customer", "Opportunity")


def execute():
	for doctype in DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		for fieldname in _url_fieldnames(doctype):
			_heal_column(doctype, fieldname)


def _url_fieldnames(doctype):
	"""URL-type Data fields that really have a column, or []."""
	try:
		fields = frappe.get_meta(doctype).fields or []
	except Exception:
		return []

	names = []
	for df in fields:
		if getattr(df, "fieldtype", None) != "Data" or (getattr(df, "options", "") or "") != "URL":
			continue
		# A Custom Field declared but not yet migrated has meta and no column; the
		# custom ones (Lead.custom_account_website) are created by
		# setup/custom_fields.py on after_migrate, which runs after patches.
		if frappe.db.has_column(doctype, df.fieldname):
			names.append(df.fieldname)
	return names


def _heal_column(doctype, fieldname):
	rows = frappe.get_all(
		doctype, filters={fieldname: ["!=", ""]}, fields=["name", fieldname]
	)
	healed = 0
	for row in rows:
		fixed = normalize_website(row.get(fieldname))
		if fixed is None:
			continue
		frappe.db.set_value(doctype, row["name"], fieldname, fixed, update_modified=False)
		healed += 1

	if healed:
		frappe.logger().info(
			f"backfill_website_scheme: healed {healed} of {len(rows)} {doctype}.{fieldname} values"
		)

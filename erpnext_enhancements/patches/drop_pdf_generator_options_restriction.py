"""Remove the Property Setter that hid `chrome` from Print Format's PDF Generator field.

Frappe ships `Print Format.pdf_generator` as a Select with options ``wkhtmltopdf\\nchrome``,
and its own controller types the field ``DF.Literal["wkhtmltopdf", "chrome"]``. On this site a
Property Setter named ``Print Format-pdf_generator-options`` narrowed those options to
``wkhtmltopdf`` alone, created 2025-11-20 by Administrator with ``is_system_generated = 1``.

The effect was not cosmetic. A Select's options are enforced by ``_validate_selects`` on every
save, so with the restriction in place *no* Print Format could be moved to the chrome backend
at all -- saving one threw::

    PDF Generator cannot be "chrome". It should be one of "wkhtmltopdf"

Its origin could not be established: nothing in any installed app creates it, no app fixture
ships it, and frappe's own
``sets_wkhtmltopdf_as_default_for_pdf_generator_field`` patch only writes *values*, never the
field options. The most plausible reading is that somebody pinned the site to wkhtmltopdf
while the chrome backend was unusable -- which it was, until 2026-08-01. That reason is spent:
chrome now renders every format on this site that has a document to test with.

Deleting it restores frappe's stock options. Nothing regenerates it, so this runs once.
"""

import frappe

PROPERTY_SETTER = "Print Format-pdf_generator-options"


def execute():
	if not frappe.db.exists("Property Setter", PROPERTY_SETTER):
		return

	# Only remove the *restriction*. If someone has since widened it back to include
	# chrome, or replaced it for an unrelated reason, leave their record alone.
	value = frappe.db.get_value("Property Setter", PROPERTY_SETTER, "value") or ""
	if "chrome" in value:
		return

	frappe.delete_doc("Property Setter", PROPERTY_SETTER, force=1, ignore_permissions=True)
	frappe.clear_cache(doctype="Print Format")

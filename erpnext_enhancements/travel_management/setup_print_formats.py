# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Trip Sheet print format on Travel Trip, created idempotently on every migrate.

Same ``after_migrate`` upsert as the other formats this app ships (see
``enhancements_core/setup_print_formats.py`` for the full rationale): the template lives in
the repo, as a file of its own (``print_formats/trip_sheet.html``), and an edit to it
deploys on the next migrate with no export step. ``after_migrate`` runs after fixture sync,
so nothing overrides it.

Three things here are not obvious:

- **The template reads a Jinja global, never the document.** Every money field on Travel
  Trip is permlevel 0 and the Employee role may print a trip, so a template that read
  ``doc.cost`` would put it on a crew member's sheet. ``trip_sheet.html`` hands ``doc`` to
  ``ee_trip_sheet`` (``api/travel.py``, registered in hooks.py) and prints only what that
  returns: no money, except the cost total on a travel coordinator's whole-trip sheet.
- **Order in ``after_migrate`` is load-bearing.** This hook must sit *above*
  ``enhancements_core.setup_print_formats.ensure_chrome_pdf_generator``, which is last on
  purpose and points every format at the chrome PDF backend. The format is also created with
  ``pdf_generator = "chrome"`` itself, and the links name it too
  (``views.trip_sheet_url``), because frappe v16's ``download_pdf`` picks wkhtmltopdf when a
  request names no generator, whatever the format says.
- **It is Travel Trip's default format (Nik, 2026-09-27), set here and not as a fixture.**
  ``_make_default`` writes the ``default_print_format`` Property Setter right after the upsert,
  and only once the format exists. A fixture would name a format that is not there yet in two
  cases, both checked against frappe ``version-16``: ``bench install-app`` syncs fixtures but
  never runs ``after_migrate`` (``installer.install_app``), and a failed upsert here is logged,
  not raised, while the fixture would have imported anyway (a Property Setter's ``value`` is
  Small Text, never checked against Print Format, and fixture import sets ``ignore_links``).
  The desk does not fail on a missing default, it misleads: ``frappe.meta.get_print_formats``
  splices out ``indexOf(default)``, which is -1 for a missing one, so it drops the *last* entry
  of the Print menu, "Standard" itself when the trip has no other format, and lists the
  missing name alone, which ``printview.get_print_format_doc`` then renders as Standard. With
  the format present the menu is "Trip Sheet", then "Standard", which stays selectable. The
  row is ``is_system_generated`` (frappe's default), so the fixture export, which takes only
  ``is_system_generated = 0``, never picks it up, the same disjoint-channel rule as
  ``setup/custom_fields.py`` (fixtures/README.md). The repo owns it: a default chosen on the
  site is put back on the next migrate, like a fixture's.

The chrome is ``print_style``'s (docs/print-design-system.md), through the ``ps_*`` Jinja
globals: the neutral stripe (a trip belongs to no one pillar), the wordmark with our
address, the eyebrow "TRIP SHEET", the trip's purpose as the title. Print-safe CSS only.
"""

import os

import frappe

from erpnext_enhancements.travel_management.views import TRIP_SHEET_FORMAT

MODULE = "Travel Management"
DOCTYPE = "Travel Trip"
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "print_formats", "trip_sheet.html")


def trip_sheet_html():
	"""The Trip Sheet template, as the format stores it."""
	with open(TEMPLATE_PATH, encoding="utf-8") as handle:
		return handle.read()


def ensure_travel_print_formats():
	"""``after_migrate`` entry point. Idempotent (upserts the html every migrate) and
	guarded: a failure logs rather than aborting the migrate, which on this repo is the
	deploy."""
	try:
		if not frappe.db.exists("DocType", DOCTYPE):
			return
		_upsert_print_format(TRIP_SHEET_FORMAT, DOCTYPE, trip_sheet_html())
		_make_default(TRIP_SHEET_FORMAT, DOCTYPE)
		frappe.db.commit()
		frappe.logger().info(f"Travel print formats: ensured {TRIP_SHEET_FORMAT}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Travel print formats")


def _make_default(name, doc_type):
	"""Make ``name`` the default print format of ``doc_type``, only when that format exists
	and only when the default says something else, so a migrate with nothing to change writes
	nothing. See the module docstring for why this is not a fixture.

	``frappe.make_property_setter`` is what the Print Format form's "Set as default" calls
	(``print_format.make_default``): on insert the Property Setter deletes any other
	``default_print_format`` setter on the doctype (``PropertySetter.validate``), so there is
	never more than one, and it clears the doctype's cache.
	"""
	if not frappe.db.exists("Print Format", name):
		return
	current = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doc_type, "doctype_or_field": "DocType", "property": "default_print_format"},
		"value",
	)
	if current == name:
		return
	frappe.make_property_setter(
		{
			"doctype": doc_type,
			"doctype_or_field": "DocType",
			"property": "default_print_format",
			"value": name,
			"property_type": "Data",
		},
		validate_fields_for_doctype=False,
	)


def _upsert_print_format(name, doc_type, html):
	if frappe.db.exists("Print Format", name):
		pf = frappe.get_doc("Print Format", name)
	else:
		pf = frappe.new_doc("Print Format")
		pf.name = name

	pf.doc_type = doc_type
	pf.module = MODULE
	pf.print_format_type = "Jinja"
	pf.custom_format = 1
	pf.standard = "No"
	pf.disabled = 0
	pf.html = html
	if frappe.db.has_column("Print Format", "pdf_generator"):
		pf.pdf_generator = "chrome"
	pf.save(ignore_permissions=True)

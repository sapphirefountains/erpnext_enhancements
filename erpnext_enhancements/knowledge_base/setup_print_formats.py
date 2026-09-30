# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's two print formats, created idempotently on every migrate (2026-09-30).

* **Article Document**, Knowledge Article's default: a published article as its kind's register
  template lays it out (POL-0002, POL-0003, POL-0004), for the printer and the PDF.
* **Article Version Preview**, Knowledge Article Version's default: a draft the same way, marked as
  not approved, which is what a KB Author's Preview button opens.

Each format is one line, ``{{ kb_document(doc) }}``: the Jinja global (``printing.kb_document``)
loads the record again by name, checks the caller may read it and draws it with
``document.render``, so the layout lives in tested Python rather than in a template string, and
nothing the print view is handed as JSON reaches the page (``printing.py`` explains).

The same ``after_migrate`` upsert as every other format this app ships (``travel_management/
setup_print_formats.py`` and ``enhancements_core/setup_print_formats.py`` give the full rationale):
the repo is the source of truth, an admin's edit in the Desk is put back on the next migrate, it is
created with ``pdf_generator = "chrome"``, and the hook sits above ``ensure_chrome_pdf_generator``
in ``hooks.py``. Each is made its doctype's default **only once it exists** (:func:`_make_default`),
for the reason the Trip Sheet's module gives: a default naming a missing format makes the Desk drop
"Standard" from the Print menu. That default is the one Property Setter on either doctype. It widens
nothing, which is what ``tests/test_knowledge_base_schema.py`` guards against in the fixtures; it is
``is_system_generated``, so the fixture export never picks it up.

The margins are the templates' own inch, all round.
"""

import frappe

from erpnext_enhancements.knowledge_base import constants

MODULE = "Knowledge Base"
ARTICLE_FORMAT = "Article Document"
VERSION_FORMAT = "Article Version Preview"

#: Every format's html: the Jinja global does the rest. ``doc`` is only handed over.
FORMAT_HTML = (
	"{#- The Knowledge Base's print format (knowledge_base/setup_print_formats.py). kb_document\n"
	"    loads this record again by name, checks the reader may read it, and draws it as the company\n"
	"    register's template for its kind. Edits here are overwritten on the next migrate. -#}\n"
	"{{ kb_document(doc) }}\n"
)

#: Format name -> the doctype it prints.
FORMATS = {
	ARTICLE_FORMAT: constants.ARTICLE_DOCTYPE,
	VERSION_FORMAT: constants.VERSION_DOCTYPE,
}

#: The templates' page margin, one inch, in the Print Format's millimetres.
MARGIN_MM = 25.4


def ensure_knowledge_base_print_formats():
	"""``after_migrate`` entry point. Idempotent and guarded: a failure is logged rather than
	aborting the migrate, which on this repo is the deploy."""
	try:
		for name, doc_type in FORMATS.items():
			if not frappe.db.exists("DocType", doc_type):
				continue
			_upsert_print_format(name, doc_type)
			_make_default(name, doc_type)
		frappe.db.commit()
		frappe.logger().info("Knowledge Base print formats: ensured")
	except Exception:
		frappe.log_error(title="Knowledge Base print formats", message=frappe.get_traceback())


def _upsert_print_format(name, doc_type):
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
	pf.html = FORMAT_HTML
	for side in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
		if frappe.db.has_column("Print Format", side):
			pf.set(side, MARGIN_MM)
	if frappe.db.has_column("Print Format", "pdf_generator"):
		pf.pdf_generator = "chrome"
	pf.save(ignore_permissions=True)


def _make_default(name, doc_type):
	"""Make ``name`` the default print format of ``doc_type``, only when it exists and only when the
	default says something else, so a migrate with nothing to change writes nothing. The Print Format
	form's own "Set as default" does the same (``frappe.make_property_setter``), which also removes
	any other ``default_print_format`` setter on the doctype. Copied from the Trip Sheet's module,
	which gives the reasons."""
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

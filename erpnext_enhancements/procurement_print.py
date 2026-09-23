"""One PDF of a project's procurement documents, from the Project form's Procurement Tracker.

The tracker lists a job's Material Requests, RFQs, Supplier Quotations, Purchase Orders,
Receipts and Invoices, and until now the only way to get any of them on paper was one at a
time from each document's own form. Its Print dialog prints a whole group — all of it, or
just what is still open — as a single PDF.

**This is frappe's own multi-document print, decorated, not a reimplementation.** It calls
``frappe.utils.print_format.download_multi_pdf`` — the function behind the list view's
Actions → Print — unchanged, and then renames the file. That route names the PDF after the
doctype and nothing else, so every job's open orders would arrive as ``Purchase-Order.pdf``;
the whole reason ``po_pdf_filename`` exists is that the person receiving these cannot tell
which job a file belongs to without opening it. So the file is named for the project and the
choice: ``PRJ-00706-Open-Purchase-Orders.pdf``.

**Permission is frappe's, per document, plus the project.** ``download_multi_pdf`` renders
each document through ``printview``, which checks read/print permission on that document and
refuses drafts or cancelled documents the Print Settings forbid. What it does *not* do is
say so: a document that fails is dropped from the PDF with no message, when ``doctype`` is a
string, which is the only form a GET can send. The browser therefore filters those out
before it asks and tells the user what it left out (see ``project_enhancements.js``); this
end only refuses a request that names nothing, since an empty PDF reads as "there is
nothing open". The Project read check is the same gate every tracker endpoint carries.

**Large groups do not come here.** Over 25 documents the dialog uses frappe's own
``download_multi_pdf_async``, exactly as the list view does, because a synchronous render of
that many PDFs is a request long enough to meet the worker timeout. That path keeps
frappe's filename; no project on production has more than 23 Purchase Orders today.
"""

import json

import frappe
from frappe import _

#: The procurement doctypes the tracker groups, each with the plural its file is named with.
#: Also the allow-list: this is a route for printing a job's purchasing, and a doctype
#: outside it has no business on it. Same six, same order, as
#: ``project_enhancements.PROCUREMENT_DOCTYPE_ORDER`` — a test holds the two together.
PLURALS = {
	"Material Request": "Material Requests",
	"Request for Quotation": "Requests for Quotation",
	"Supplier Quotation": "Supplier Quotations",
	"Purchase Order": "Purchase Orders",
	"Purchase Receipt": "Purchase Receipts",
	"Purchase Invoice": "Purchase Invoices",
}

#: What the dialog can ask for. Only names the file: the browser picks the documents,
#: from the same feed the tracker is showing.
SCOPES = ("all", "open")


def _sanitize(value):
	"""Frappe's rule for a filename segment: no spaces, no path separators."""
	return str(value or "").replace(" ", "-").replace("/", "-").strip("-")


def combined_pdf_filename(project, doctype, scope):
	"""``PRJ-00706-Open-Purchase-Orders.pdf``, or ``PRJ-00706-Purchase-Orders.pdf`` for all.

	Project first, because a folder of these is sorted by job. "All" is not spelled out:
	a file called ``Purchase-Orders`` already reads as all of them, and one called
	``Open-Purchase-Orders`` is what has to stand out.
	"""
	parts = [_sanitize(project)]
	if scope == "open":
		parts.append("Open")
	parts.append(_sanitize(PLURALS.get(doctype, doctype)))
	return "-".join(p for p in parts if p) + ".pdf"


def _names(name):
	"""The document names from the request: a JSON array, as the list view sends it."""
	try:
		names = json.loads(name) if isinstance(name, str) else name
	except ValueError:
		names = None
	if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
		frappe.throw(_("Choose at least one document to print."))
	return names


@frappe.whitelist()
def download_procurement_pdf(
	project,
	doctype,
	name,
	scope="all",
	format=None,
	no_letterhead=0,
	letterhead=None,
	options=None,
):
	"""frappe's ``download_multi_pdf`` for one tracker group, named for the job.

	``name`` is a JSON array of document names, the shape frappe's own function takes. The
	remaining parameters are passed through untouched; they land on frappe's typed,
	``validate_argument_types``-wrapped function, which coerces them there, so their
	annotations are deliberately not copied here. ``pdf_generator`` is not a parameter at all
	and still works: frappe's ``get_print`` reads it straight off ``form_dict``, which is how
	the browser gets the chrome backend for ``Standard`` — a format with no record to carry a
	generator of its own.
	"""
	if doctype not in PLURALS:
		frappe.throw(_("{0} is not a procurement document.").format(doctype))
	if scope not in SCOPES:
		frappe.throw(_("Unknown print choice: {0}").format(scope))
	names = _names(name)

	frappe.get_doc("Project", project).check_permission("read")

	# Imported here, not at module scope, for the same reason as po_pdf_filename: it is a
	# wider import than loading this module needs.
	from frappe.utils.print_format import download_multi_pdf

	result = download_multi_pdf(
		doctype,
		json.dumps(names),
		format=format,
		no_letterhead=no_letterhead,
		letterhead=letterhead,
		options=options,
	)
	frappe.local.response.filename = combined_pdf_filename(project, doctype, scope)
	return result

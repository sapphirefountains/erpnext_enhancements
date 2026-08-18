"""The name a Purchase Order goes by, in its PDF filename and in its printed header
(ER-2026-256847).

Lisa receives Purchase Order PDFs and cannot tell which job each one is for without opening
it, because the file is named after the order and nothing else: ``PO-2026-00262.pdf``. This
makes it ``PO-2026-00262-PRJ-00706.pdf``. Order number first so a folder still sorts the way
everyone already expects; the project is what you scan for once you are looking.

**The printed sheet says the same string**, because ``purchase_order_document_id`` is a jinja
method and the header calls it. That is not decoration: somebody holding a printout next to
the file it came from is exactly who this is for, and the alternative — a template composing
its own version of the same idea — drifts. It did, briefly: this bounds a long tail of jobs
with a ``plus-N`` marker and the template loop it replaced listed every one, so an order
spanning four jobs would have printed one identifier and saved as another.

**Why this is a whitelisted-method override and not a `title_field`.** The obvious fix — give
Purchase Order a title field holding "order + project" and let the PDF route use it — does not
work, and it fails silently, which is worse. Frappe v16 builds the download filename from the
docname and consults `title_field` nowhere on this path::

    # frappe/utils/print_format.py, version-16
    frappe.local.response.filename = "{name}.pdf".format(name=name.replace(" ", "-").replace("/", "-"))

The Desk's Print → PDF button calls exactly that method (`printing/page/print/print.js`
→ `/api/method/frappe.utils.print_format.download_pdf`), and `/api/method/…` reaches it
through `api/v1.handle_rpc_call` → `handler.handle()` → `handler.execute_cmd`, whose first
line is `cmd = frappe.override_whitelisted_method(cmd)`. So `override_whitelisted_methods` is
both available and the only lever on this route. (Read with `git show origin/version-16:…`
— a sibling `../frappe` checkout is on `develop` and its line numbers do not apply here.)

**This override sits on a route every doctype in the system prints through**, so it is a
decoration and not a reimplementation: it calls frappe's own function, unchanged, and only
then rewrites the filename — for Purchase Order alone. Every other doctype leaves byte for
byte as it arrived. The rewrite is inside its own `try`, because a PDF that downloads under
the old name is an annoyance and a PDF that 500s is an outage.

**What this does not rename.** Email attachments and Drive uploads compose their own names
through `frappe.attach_print`, a different code path, and the weasyprint generator has its
own route (`frappe.utils.weasyprint.download_pdf`) that `Purchase Order - Sapphire` does not
use. Only the Desk download is renamed.
"""

import frappe

from erpnext_enhancements.procurement_project import purchase_order_projects

#: Only this doctype gets a rewritten filename. Everything else passes through untouched.
TARGET_DOCTYPE = "Purchase Order"

#: An order spanning more than this many projects prints the first few and a count. No order
#: on production spans even two, but a filename is a thing that has to fit in a folder and on
#: a taskbar, and "silently unbounded" is not a length.
MAX_PROJECTS_IN_NAME = 3


def _sanitize(value):
	"""Frappe's own rule, applied to the part frappe never sees.

	It replaces spaces and slashes in the docname before putting it in `Content-Disposition`;
	the segment we append has to clear the same bar, or a project id could introduce a path
	separator into a filename. Project ids are `PRJ-00706` today and this costs nothing.
	"""
	return str(value or "").replace(" ", "-").replace("/", "-").strip("-")


def purchase_order_document_id(doc):
	"""``PO-2026-00262-PRJ-00706`` — what this order is called, on the page and on disk.

	**The identifier the print format puts in its header and the filename stem are the same
	string because they are the same function call.** They started out as two renderings of
	the same idea, and two renderings drift: this one bounds a long tail of projects with a
	``plus-N`` marker and the template's loop did not, so an order spanning four jobs would
	have printed one thing and saved as another. Somebody comparing a PDF on their desk to
	the file it came from is exactly the person this feature is for.

	Pure, and separate from the override below, so the rule can be tested without a bench.

	A blank project appends nothing rather than a placeholder: 61 of the 158 orders on
	production carry none, and ``PO-2026-00262-none`` would be worse than the plain docname.
	"""
	base = _sanitize(doc.name)
	projects = [_sanitize(p) for p in purchase_order_projects(doc)]
	projects = [p for p in projects if p]
	if not projects:
		return base
	if len(projects) > MAX_PROJECTS_IN_NAME:
		extra = len(projects) - MAX_PROJECTS_IN_NAME
		projects = projects[:MAX_PROJECTS_IN_NAME] + [f"plus-{extra}"]
	return "-".join([base, *projects])


def purchase_order_filename(doc):
	"""The document id with ``.pdf`` on it, which is the whole difference between them."""
	return f"{purchase_order_document_id(doc)}.pdf"


@frappe.whitelist(allow_guest=True)
def download_pdf(
	doctype,
	name,
	format=None,
	doc=None,
	no_letterhead=0,
	language=None,
	letterhead=None,
	pdf_generator=None,
):
	"""frappe's `download_pdf`, with the Purchase Order filename rewritten afterwards.

	The parameter list mirrors frappe's exactly — `frappe.call` matches the request's form_dict
	against *this* signature, so a parameter missing here is a print format or letterhead the
	user picked and silently did not get. Annotations are deliberately not copied: they would
	be a second copy of frappe's types to keep in step, and the values still land on frappe's
	own typed, `validate_argument_types`-wrapped function, which coerces them there.

	`allow_guest=True` because the original carries it — portal and notification links reach
	this route — and permission is enforced inside it, by `validate_print_permission` on the
	document. Dropping the flag here would 403 those links.
	"""
	# Imported here, not at module scope. `purchase_order_document_id` above is now a
	# jinja method, so frappe imports THIS module to build the template environment on
	# every print render — dragging `frappe.utils.print_format` into that is a wider
	# import than the feature needs, and on the PDF route it is the module mid-execution.
	from frappe.utils.print_format import download_pdf as _frappe_download_pdf

	result = _frappe_download_pdf(
		doctype,
		name,
		format=format,
		doc=doc,
		no_letterhead=no_letterhead,
		language=language,
		letterhead=letterhead,
		pdf_generator=pdf_generator,
	)

	if doctype == TARGET_DOCTYPE:
		try:
			# Reloaded rather than taken from `doc`: the caller may pass a document built for
			# rendering, and the project has to be the one that is stored.
			frappe.local.response.filename = purchase_order_filename(frappe.get_doc(doctype, name))
		except Exception:
			# Frappe already set a working filename. Losing the project off the end of it is
			# not worth failing a download that has already been rendered and paid for.
			frappe.log_error(frappe.get_traceback(), "Purchase Order PDF filename")

	return result

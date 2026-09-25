# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The PDF of a customer's own invoice, for the payment portal (``/pay`` and ``/pay-card``).

Answers one question for a signed-in portal customer: "may I read the invoice I am being asked
to pay?" Both pages link to it ("View invoice (PDF)", in a new tab, so the phone's PDF viewer
opens it and the page stays where it was). ``core.api.portal_invoice_pdf`` is the endpoint and
owns the ownership check; this module renders the PDF and writes the response.

What it is careful about, including the things that look like bugs:

- **The print format is pinned to the customer-facing one, and there is no fallback.** Always
  ``SALES_INVOICE_FORMAT`` (``Sales Invoice - Sapphire``), the format
  ``enhancements_core/setup_sales_print_formats.py`` writes on every migrate, and never the
  DocType's ``default_print_format`` as ``www/printview.py`` would resolve it. On this site the
  two agree (the fixture Property Setter names the same format), so the customer sees what the
  Desk prints. Where they disagree, following the default fails open: whatever format a staffer
  makes the default is what customers get, an internal one included, and printview's last resort
  is ``Standard``, which that module itself says "shows internal fields". On v16 ``Standard``
  prints every permlevel-0 field that has a value and no ``print_hide`` — ``cost_center``,
  ``amount_eligible_for_commission``, ``is_internal_customer``,
  ``inter_company_invoice_reference``, and any custom field made on the site outside this repo.
  So if the pinned format is missing, disabled or made for another doctype, the render raises
  ``PrintFormatUnavailable``: the customer gets the "could not produce the PDF" page and the
  Error Log gets the reason. Never ``Standard``.
- **The PDF generator is the one the Desk's Download PDF uses**: the format's own
  ``pdf_generator``, else ``Print Settings.pdf_generator``, else ``wkhtmltopdf``
  (``printing/page/print/print.js`` ``get_pdf_generator``, version-16). Frappe's server-side
  ``get_print`` skips the middle step, so a format whose generator had been cleared would go to
  wkhtmltopdf, which segfaults on this host (docs/pdf-generation.md). The Sapphire format is
  ``chrome``, set on every migrate by ``setup_print_formats.ensure_chrome_pdf_generator``.
- **Print permission is waived for this one render, never granted.** A Website User has neither
  Read nor Print on Sales Invoice, and ERPNext's portal rule
  (``controllers/website_list_for_contact.has_website_permission``) looks the user up in the
  Customer's **Portal Users** table — not how this portal ties a user to a customer (a Contact's
  dynamic link, ``core.api.get_portal_customers``). So printview's ``validate_print_permission``
  would refuse customers their own invoices. The endpoint has already proven ownership by the
  portal's own rule, so the render runs with ``flags.ignore_print_permissions`` — the flag
  Frappe's ``attach_print`` sets to email a document to someone who could not open it — and puts
  it back in a ``finally``. Not ``frappe.set_user``: on a web request that overwrites the
  session id and empties the session, and the customer's next request finds itself logged out.
  No DocPerm is widened: nothing except this code path can read an invoice it could not before.
- **The request's own parameters never reach the render.** ``get_print`` renders through the
  ``printview`` page, which reads ``frappe.form_dict``, and on this route form_dict is the query
  string: ``?pdf_generator=`` would override the generator, and ``?settings=`` is merged into
  Print Settings (``allow_print_for_draft`` among them). The render runs against an empty
  form_dict, and the request's is put back afterwards. One parameter has already been read
  before any of this runs: Frappe sets ``frappe.local.lang`` from ``?_lang=`` when the request
  begins (``translate.get_language``), and printview renders in it — right to left for Arabic.
  So the render also runs in the user's own language (``render_language``), and the request's
  is put back afterwards too.
- **No Frappe letter head, and the page is still the Desk's.** The Desk's print view does not
  read ``Print Settings.with_letterhead``: it picks the invoice's own ``letter_head`` if that one
  is enabled, else the default enabled Letter Head, and sends it with ``no_letterhead=0``
  (``print.js`` ``set_default_letterhead`` and ``render_page``, version-16). But the Sapphire
  format is a custom format, so printview hands a letter head only to the template, as
  ``letter_head`` and ``footer``, and neither the template nor ``print_style`` reads them — it
  draws its own (``print_style.letterhead``), ``printview.html`` injects none, and chrome finds
  no ``#header-html``/``#footer-html`` to lift out. So this render asks for none
  (``no_letterhead=1``): the page is identical, and no Letter Head's Jinja is rendered for
  nothing. Language is the same story: the Desk renders in ``doc.language`` (else the format's,
  else the user's), this in the user's, and the template calls no ``_()``.
- **Bounded twice: for the site, and per customer.** A headless Chrome render is the most
  expensive thing a portal user can ask for. Frappe's concurrency limiter
  (``frappe.concurrent_limit``, the one on its own ``download_pdf``) keys its semaphore by the
  wrapped function, so this render has a pool of its own beside ``download_pdf``'s — and each
  defaults to half the web tier, which together is all of it. So the limit here is explicit and
  small (``RENDER_CONCURRENCY``), and a request waits only ``RENDER_WAIT_SECONDS`` for a slot.
  Then a per-user cap, ``USER_RENDER_LIMIT`` renders per ``USER_RENDER_WINDOW`` seconds, keyed
  on the session user, because ``frappe.rate_limit`` cannot key on one (its identity is the
  client IP or a form_dict value). It is counted before the semaphore, so a customer looping on
  the link is refused at once rather than holding a worker in the queue. Both refusals get the
  "try again" page with a 503 and are not logged.
- **A failed render is a page, not a traceback, and it is logged.** There is no chrome →
  wkhtmltopdf fallback in Frappe. The customer is told to try again in a few minutes; the Error
  Log gets the traceback (without frame locals) through ``defer_insert``, because this is a GET
  and Frappe rolls a GET's transaction back, taking an ordinary ``log_error`` insert with it.
- **Every answer is a web page or the PDF, never JSON.** The link opens in a new tab, where a
  JSON error body is all the customer would see.
"""

from __future__ import annotations

import html
import time

import frappe
from frappe import _

from erpnext_enhancements.enhancements_core.setup_sales_print_formats import SALES_INVOICE_FORMAT

INVOICE_DOCTYPE = "Sales Invoice"
#: Frappe's own last resort, in ``get_print`` and in the Desk's print view alike.
DEFAULT_PDF_GENERATOR = "wkhtmltopdf"
#: Where both pages' "View invoice (PDF)" links point, with ``?invoice=<name>``.
PDF_ROUTE = "/api/method/erpnext_enhancements.stripe_payments.core.api.portal_invoice_pdf"
#: Portal renders in flight at once, site-wide. Portal PDF traffic is light; this is a ceiling.
RENDER_CONCURRENCY = 2
#: Seconds a request waits for a free render slot before its 503, holding a worker meanwhile.
RENDER_WAIT_SECONDS = 3
#: Renders one signed-in user may start per window. A person reading their invoices one after
#: another does not come near it; a loop does.
USER_RENDER_LIMIT = 10
USER_RENDER_WINDOW = 60


class PrintFormatUnavailable(Exception):
	"""The customer-facing Sales Invoice format cannot be used, and no other is used in its place."""


class RenderLimitReached(Exception):
	"""This user has started ``USER_RENDER_LIMIT`` renders in this window. It carries
	``retry_after``, like the concurrency limiter's refusal, so it is answered the same way."""

	def __init__(self, retry_after: int):
		super().__init__("Too many invoice PDFs requested. Please try again in a minute.")
		self.retry_after = retry_after


def _concurrent_limit(**kwargs):
	"""Frappe's PDF concurrency limiter (``frappe.concurrent_limit``, v16.17 and later) with
	``kwargs``, or no limit where it does not exist. It is a no-op outside an HTTP request."""
	limiter = getattr(frappe, "concurrent_limit", None)
	return limiter(**kwargs) if limiter else (lambda fn: fn)


def invoice_print_format() -> str:
	"""The print format customers are shown: always ``SALES_INVOICE_FORMAT``.

	Raises ``PrintFormatUnavailable`` when that format is missing, disabled or made for another
	doctype. There is deliberately no fallback (see the module docstring)."""
	row = frappe.db.get_value("Print Format", SALES_INVOICE_FORMAT, ["doc_type", "disabled"], as_dict=True)
	if not row or row.disabled or row.doc_type != INVOICE_DOCTYPE:
		raise PrintFormatUnavailable(
			f"Print Format {SALES_INVOICE_FORMAT!r} is missing, disabled or not a Sales Invoice "
			"format; the portal will not print an invoice with any other."
		)
	return SALES_INVOICE_FORMAT


def pdf_generator_for(print_format: str) -> str:
	"""The PDF generator the Desk's Download PDF would use for ``print_format``."""
	generator = frappe.db.get_value("Print Format", print_format, "pdf_generator")
	if not generator:
		# A Single: get_single_value, never db.get_value("Singles", ...) (CLAUDE.md).
		generator = frappe.db.get_single_value("Print Settings", "pdf_generator")
	return generator or DEFAULT_PDF_GENERATOR


def pdf_filename(name: str) -> str:
	"""Frappe's own ``download_pdf`` rule: the docname, spaces and slashes made hyphens."""
	return "{}.pdf".format(str(name).replace(" ", "-").replace("/", "-"))


def render_language() -> str:
	"""The language the invoice is rendered in: the session user's own (``User.language``, else the
	site's), as Frappe picks it when a session begins — never the request's ``?_lang=``."""
	from frappe.translate import get_user_lang

	return get_user_lang(frappe.session.user)


def count_render_for_user() -> None:
	"""Count one render against the session user's cap, and raise ``RenderLimitReached`` past it.

	A fixed window in the Redis cache, keyed on the user and the window's number, so a key is
	never read again once its window is over and expires on its own. Like Frappe's limiter it is
	a no-op outside an HTTP request."""
	if getattr(frappe.local, "request", None) is None:
		return
	window, elapsed = divmod(int(time.time()), USER_RENDER_WINDOW)
	key = frappe.cache.make_key(f"portal-invoice-pdf:{window}", user=frappe.session.user)
	count = frappe.cache.incrby(key, 1)
	frappe.cache.expire(key, USER_RENDER_WINDOW)
	if count > USER_RENDER_LIMIT:
		retry_after = max(1, USER_RENDER_WINDOW - elapsed)
		if (headers := getattr(frappe.local, "response_headers", None)) is not None:
			headers.set("Retry-After", str(retry_after))
		raise RenderLimitReached(retry_after)


@_concurrent_limit(limit=RENDER_CONCURRENCY, wait_timeout=RENDER_WAIT_SECONDS)
def render_invoice_pdf(name: str) -> bytes:
	"""The PDF bytes of Sales Invoice ``name`` in the customer-facing format.

	**The caller must already have proven the session user may read this invoice**: print
	permission is waived for the render (see the module docstring for why that and not
	``set_user``).
	"""
	print_format = invoice_print_format()
	generator = pdf_generator_for(print_format)
	language = render_language()
	flags = frappe.local.flags
	waived = flags.get("ignore_print_permissions")
	request_form_dict = frappe.local.form_dict
	request_lang = getattr(frappe.local, "lang", None)
	frappe.local.form_dict = frappe._dict()
	frappe.local.lang = language
	flags.ignore_print_permissions = True
	try:
		return frappe.get_print(
			INVOICE_DOCTYPE,
			name,
			print_format=print_format,
			as_pdf=True,
			# The format draws its own letterhead and never reads Frappe's (module docstring).
			no_letterhead=1,
			pdf_generator=generator,
		)
	finally:
		flags.ignore_print_permissions = waived
		frappe.local.form_dict = request_form_dict
		frappe.local.lang = request_lang


def respond_with_pdf(name: str) -> None:
	"""Answer the request with the PDF of ``name``, shown inline, or with a page saying it could
	not be produced. Frappe's ``type = "pdf"`` response is ``Content-Disposition: inline`` — the
	phone's PDF viewer, not a download — and ``no-store``, like every response it sends."""
	try:
		# Before the render's semaphore, so a refused request does not wait for a slot.
		count_render_for_user()
		pdf = render_invoice_pdf(name)
		if not pdf:
			raise ValueError(f"Empty PDF for Sales Invoice {name}")
	except Exception as exc:
		# Both limiters' refusals carry retry_after; a busy server is not an error.
		busy = getattr(exc, "retry_after", None) is not None
		if not busy:
			frappe.log_error(
				title="Portal invoice PDF",
				message=frappe.get_traceback(),
				reference_doctype=INVOICE_DOCTYPE,
				reference_name=name,
				defer_insert=True,
			)
		frappe.respond_as_web_page(
			_("Invoice PDF unavailable"),
			_(
				"We could not produce the PDF of invoice {0} just now. Please try again in a few "
				"minutes, or contact us."
			).format(html.escape(str(name))),
			http_status_code=503 if busy else 500,
			primary_action="/pay",
			primary_label=_("Back to invoices"),
		)
		return
	frappe.local.response.filename = pdf_filename(name)
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"


def respond_not_available() -> None:
	"""The one answer for a missing name, someone else's invoice, a draft and a canceled one alike,
	so the answer says nothing about whether an invoice exists. It points to ``/pay``, which lists
	the customer's own invoices. (A signed-out tab does not get here: the endpoint is not
	``allow_guest``, so Frappe answers with its own 403 "Not Permitted" page first.)"""
	frappe.respond_as_web_page(
		_("Invoice not available"),
		_("This invoice is not available. Your invoices are listed on the payment page."),
		http_status_code=404,
		primary_action="/pay",
		primary_label=_("Back to invoices"),
	)

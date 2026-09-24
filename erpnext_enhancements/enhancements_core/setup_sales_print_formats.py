"""Customer-facing print formats for Quotation, Sales Order and Sales Invoice (WI-020).

These are the documents customers actually receive, and from 2027-01-01 they leave
ERPNext rather than QuickBooks. Before this there were none: every sales document fell
back to the stock `* Standard` format, which is unbranded and shows internal fields.

Since v1.494.0 the chrome comes from `print_style` — the "Pillar Stripe" concept Nik
picked from the design canvas on 2026-09-21, in the design system's own tokens — and
this module composes only the document: the facts under the letterhead, the line table,
the totals, the terms and the document-specific tail. Same `after_migrate` upsert as
`setup_print_formats.py` (the Purchase Order format), same self-contained
`_upsert_print_format`. Notes worth repeating because each was learned the expensive way:

- **The letterhead is drawn by the template, not by Frappe.** With `custom_format = 1`
  Frappe never injects a letter head — it builds the `#header-html` block only for
  *standard* formats. The Purchase Order format went to suppliers unbranded for a month
  on exactly this. `print_style.letterhead()` inlines the wordmark itself (the same SVG
  the contracts inline) and draws our name, address and phone beside it, so the site's
  `Sapphire Fountains Default` letter head — a bare right-aligned logo — is no longer
  rendered here at all: two logos on one page is worse than one.
- **The address prefers the document's own `company_address_display`** and falls back to
  the constant in `company_contact`; the phone *is* the constant. That module explains
  why, and why the fieldname differs from Purchase Order's. Every rendered address goes
  through `ps_address`, which trims the `<br>` the United States Address Template ends
  each one with — printed raw, it left a blank line under every address on the page.
- **`description` goes through `ps_line`/`ps_rich`, which passes markup through and
  escapes plain text.** It is a Text Editor field: a line authored in the Item master
  holds markup, and escaping it printed a literal `&lt;div&gt;&lt;p&gt;` at the customer.
  But 1,657 of 6,148 Sales Invoice lines were imported from QuickBooks as plain text with
  newlines, and printed as HTML their lines ran together into one paragraph. `ps_line`
  prints the item's name in bold and the description under it only when it says more
  than the name does.
- **Every cell style carries `!important`, through `ps.TD`, `ps.TD_RIGHT`, `ps.th()` and
  the shared `ps.TOTAL_*` / `ps.GRAND`.** Frappe's `standard.css` and the site's
  "Redesign" Print Style force `td`/`th` padding and alignment with `!important`, which
  beats an ordinary inline style — so until v1.533.0 this module's own totals styles
  never reached a page. Only an inline `!important` outranks them; `print_style` says
  more. The totals styles are that module's now, not copies here.
- **Print-safe CSS only** — no flexbox, no grid, `page-break-inside: avoid` on rows, and a
  `thead` that repeats across pages. The PDF backend on this host has a history
  (docs/pdf-generation.md); it is not asked to do anything clever. `print_style` keeps
  the same discipline: its letterhead and facts rows are `display:table`.
- **Quotation has no `customer` and no `project` column on this site.** Its party is
  `party_name` (with `quotation_to`), which `ps_party` knows; a `doc.customer` there is an
  attribute read of a field that does not exist — debug text on the page, not a blank.
  Project is a header field on Sales Order and Sales Invoice only, shown in the facts
  row, never per line.
- **Sales documents take the neutral stripe.** A quotation can be for any of the four
  pillars and nothing on the document says which, so they carry the brand's dark band
  rather than guessing. Deriving the pillar from the linked project's value stream is
  the obvious next step and is deliberately not this one.

What v1.533.0 changed, and why (production, measured 2026-09-24: 1,629 Sales Invoices,
673 Quotations, 0 Sales Orders):

- **The party block is `ps_party(doc)`** (`print_lookup`). The document's own contact
  phone and email are blank on all but 12 of 1,629 invoices, because this app keeps them
  in custom fields that ERPNext does not copy; the lookup walks document, contact,
  address and customer, and formats and de-duplicates what it finds. A **SHIP TO** block
  prints under it only when the document ships somewhere other than where it bills.
- **No item-code column.** Customers do not know our codes; the item's name is the line.
  The columns are Item, Qty, Unit, Rate, Amount — quantities as a person writes them
  (`1`, not `1.0`) and ERPNext's `Nos`, the unit on every one of the 6,148 invoice lines,
  as `ea`.
- **QuickBooks billable expenses print as lines, not as tax.** 154 invoices carry 1,030
  of them in the taxes table (up to 98 on one invoice): the material and its markup,
  booked as `Actual` charges on Cost of Goods Sold and Income accounts because there was
  no Item to put on a line. They printed under "Subtotal" looking like tax. They now
  print under the line table as BILLABLE EXPENSES — the way QuickBooks printed them to
  the customer — and count in the Subtotal, so the Amount column adds up to it.
- **Subtotal is `total` (plus those expenses), not `net_total`.** All 47 discounted
  invoices apply the discount to the Net Total, so `net_total` is already net of it:
  printing it as Subtotal and then subtracting the Discount again took the discount off
  twice, and the page did not add up to its own grand total. Tax rows print the account
  name without ` - SF` or ` - Inactive` (51 invoices said "Utah Sales Tax - Inactive - SF").
- **Sales Order and Sales Invoice say DRAFT or CANCELLED** under their number. A custom
  format never calls Frappe's draft heading, and this site prints drafts — 305 invoices
  are drafts. Quotation does not: all 673 are drafts, and a stamp on every one is noise.
- **PAYMENT DUE reads "Due on receipt"** when the due date is the invoice date, as it is
  on 1,583 of 1,629 — the invoice's own date printed a second time, under a heading that
  asks the customer to work out what it means. The payment terms template (44 invoices)
  prints under a later date, or under "Due on receipt" unless it says the same thing.
- **Payments.** 1,162 of 1,324 submitted invoices are paid in full and 94 part-paid. A
  submitted invoice whose outstanding differs from its total prints Payments received and
  Amount due under the total; a paid one prints "Paid in full — thank you." in place of
  How to pay, because payment instructions on a paid invoice invite a second payment. An
  unpaid invoice prints neither row — its total *is* the amount due — and nor does a
  draft, whose outstanding means nothing yet.
- **Credit notes** (`is_return`; none on production yet) print as a Credit Note, with
  AGAINST INVOICE in place of PAYMENT DUE, "Credit total", and no payment section:
  telling a customer how to pay a negative amount is the wrong instruction.
- **PROJECT prints the project's name** under its number (1,333 invoices carry one, and
  nobody files by PRJ-00706); **Your ref.** prints only when there is one — `po_no` is
  empty on all 1,629 invoices, so the old always-printed YOUR REFERENCE was a dash.
- **Quotation.** `valid_till` is empty on all 673, so VALID UNTIL printed a dash on every
  one and the acceptance line pointed at "the date above" that was not there. It now
  prints VALID UNTIL when there is a date, PAYMENT TERMS when there is a template (40
  quotations), and nothing otherwise. PREPARED BY prints "Sapphire Fountains" for the 24
  quotations owned by Administrator, whose full name is "Administrator".

Deliberate content decisions:

- **A quotation with an expiry prints it twice** — in the facts row and in the acceptance
  line. A quote with no visible expiry is the one that comes back accepted at last year's
  price. It carries acceptance lines, because a quotation is accepted by signing it.
- **The invoice prints Amount due, not just the total**, once anything has been paid,
  because a partly-paid invoice showing only the grand total is the most common cause of
  a duplicate payment.
- **The Stripe pay link renders only when set** (`custom_stripe_payment_link`). Nothing is
  invented when it is absent, and no placeholder button is drawn — an unusable "Pay now"
  is worse than none.
- **Remit-to is the document's company address**, and the company's name when the
  document carries none, rather than printing nothing at all.
"""

import frappe

from erpnext_enhancements import print_style as ps

MODULE = "Enhancements Core"

# All three sales doctypes name the company address `company_address_display`, and all
# three carry one: 673 of 673 Quotations and 1,629 of 1,629 Sales Invoices on production
# (2026-09-24). Purchase Order calls the same thing `billing_address_display`, which is
# why the letterhead takes the fieldname rather than assuming.
ADDRESS_FIELD = "company_address_display"

QUOTATION_FORMAT = "Quotation - Sapphire"
SALES_ORDER_FORMAT = "Sales Order - Sapphire"
SALES_INVOICE_FORMAT = "Sales Invoice - Sapphire"

# --- shared fragments -------------------------------------------------------------
# Composed rather than triplicated: the line table and the totals block are genuinely
# identical across the three documents, and three copies would drift on the first edit.
# Substituted rather than interpolated: the markup is full of Jinja braces, so f-strings
# and `%`/`.format` are both unavailable.

_DASH = f'<span style="color:{ps.INK_700}">&mdash;</span>'


def _date(expr):
	return "{{ frappe.format(" + expr + ', {"fieldtype": "Date"}) }}'


def _date_or_dash(expr):
	return "{% if " + expr + " %}" + _date(expr) + "{% else %}" + _DASH + "{% endif %}"


def _money(expr):
	"""Always with the document's currency: without one, fmt_money prints the system's."""
	return "{{ frappe.utils.fmt_money(" + expr + ", currency=doc.currency) }}"


# Shipping address, only when the goods go somewhere the bill does not: a different
# Address record, with something in it, that does not print the same as the billing one.
_SHIP_TO = (
	'{%- set ship_to = ps_address(doc.get("shipping_address")) %}'
	'{%- if doc.get("shipping_address_name") and doc.get("shipping_address_name") != doc.get("customer_address")'
	' and ship_to and ship_to != ps_address(doc.get("address_display")) %}'
	'<div style="' + ps.LABEL + ';margin-top:8px">SHIP TO</div>{{ ship_to }}'
	"{%- endif %}"
)


def _party(label):
	"""Who the document is for — `ps_party` escapes, formats and de-duplicates it."""
	return ps.fact(
		label,
		"{%- set party = ps_party(doc) %}{% if party %}{{ party }}{% else %}"
		+ _DASH
		+ "{% endif %}"
		+ _SHIP_TO,
		width="40%",
	)


def _project_fact():
	"""The project's number, its name under it when it has a different one (the Purchase
	Order does the same, because nobody files by PRJ-00706), then the customer's own
	reference when they gave one."""
	return ps.fact(
		"PROJECT",
		"{%- set prj = doc.project %}"
		'{%- if prj %}<span style="' + ps.STRONG + '">{{ prj | e }}</span>'
		'{%- set prj_name = frappe.db.get_value("Project", prj, "project_name") %}'
		"{%- if prj_name and prj_name != prj %}<br>{{ prj_name | e }}{% endif %}"
		"{%- else %}" + _DASH + "{% endif %}"
		'{%- if doc.po_no %}<div style="margin-top:6px">Your ref. {{ doc.po_no | e }}</div>{% endif %}',
		width="30%",
	)


def _th(label, width, right=False):
	style = ps.th(right=right) + ";width:" + width + (";white-space:nowrap" if right else "")
	return '        <th style="' + style + '">' + label + "</th>\n"


def _td(content, right=False, extra=""):
	return '        <td style="' + (ps.TD_RIGHT if right else ps.TD) + extra + '">' + content + "</td>\n"


# Column widths line the totals up under the table: the spacer under Item, the labels
# under Qty-Unit-Rate, the values under Amount.
_ITEMS = (
	"{%- set charges = ps_charge_rows(doc) %}\n"
	'<table style="width:100%; border-collapse:collapse; margin-top:4px;">\n'
	'    <thead style="display:table-header-group;">\n'
	"      <tr>\n"
	+ _th("Item", "55%")
	+ _th("Qty", "7%", right=True)
	+ _th("Unit", "7%")
	+ _th("Rate", "13%", right=True)
	+ _th("Amount", "18%", right=True)
	+ "      </tr>\n"
	"    </thead>\n"
	"    <tbody>\n"
	"      {%- for row in doc.items %}\n"
	'      <tr style="page-break-inside:avoid;">\n'
	+ _td("{{ ps_line(row.item_name, row.description) }}")
	+ _td("{{ ps_qty(row.qty) }}", right=True)
	+ _td("{{ ps_uom(row.uom) }}", extra=";white-space:nowrap")
	+ _td(_money("row.rate"), right=True)
	+ _td(_money("row.amount"), right=True)
	+ "      </tr>\n"
	"      {%- endfor %}\n"
	"      {#- QuickBooks billable expenses, which the sync books into the taxes table. They were\n"
	"          billed as lines, so they print as lines; ps_charge_rows has made them HTML-safe. -#}\n"
	"      {%- if charges %}\n"
	'      <tr style="page-break-inside:avoid; page-break-after:avoid;">\n'
	'        <td colspan="5" style="' + ps.TD + ";" + ps.LABEL + ';padding-top:12px !important">'
	"BILLABLE EXPENSES</td>\n"
	"      </tr>\n"
	"      {%- for c in charges %}\n"
	'      <tr style="page-break-inside:avoid;">\n'
	+ _td("{{ c.description }}")
	+ _td("", right=True)
	+ _td("")
	+ _td("", right=True)
	+ _td(_money("c.amount"), right=True)
	+ "      </tr>\n"
	"      {%- endfor %}\n"
	"      {%- endif %}\n"
	"      {%- if not doc.items and not charges %}\n"
	'      <tr><td colspan="5" style="' + ps.TD + ';font-style:italic">No items on this document.</td></tr>\n'
	"      {%- endif %}\n"
	"    </tbody>\n"
	"  </table>\n"
)


def _total_row(label, value, extra=""):
	return (
		"    <tr>\n"
		'      <td style="' + ps.TOTAL_SPACER + '"></td>\n'
		'      <td style="' + ps.TOTAL_LABEL + extra + '">' + label + "</td>\n"
		'      <td style="' + ps.TOTAL_VALUE + extra + '">' + value + "</td>\n"
		"    </tr>\n"
	)


# Subtotal is `total` — the sum of the lines — plus the billable expenses printed as lines
# above, so the Amount column adds up to it. Not `net_total`: every discounted document on
# this site applies its discount to the Net Total, so `net_total` already has it taken off,
# and subtracting the Discount row from it again took it off twice.
_TOTALS_OPEN = (
	"{%- set subtotal = (doc.total or 0) + (charges | sum(attribute='amount')) %}\n"
	'<table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">\n'
	"    <tr>\n"
	'      <td style="width:55%;' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="width:27%;' + ps.TOTAL_LABEL + '">Subtotal</td>\n'
	'      <td style="width:18%;' + ps.TOTAL_VALUE + '">' + _money("subtotal") + "</td>\n"
	"    </tr>\n"
	"    {%- if doc.discount_amount %}\n"
	+ _total_row("Discount", "-" + _money("doc.discount_amount"))
	+ "    {%- endif %}\n"
	"    {#- The real tax rows, labelled by account without the company suffix. Utah taxability\n"
	"        is settled per document by the tax template, so this needs no change for WI-036. -#}\n"
	"    {%- for t in ps_tax_rows(doc) %}\n"
	+ _total_row("{{ t.label }}", _money("t.amount"))
	+ "    {%- endfor %}\n"
	"    <tr>\n"
	'      <td style="' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="' + ps.GRAND + '">__TOTAL_LABEL__</td>\n'
	'      <td style="'
	+ ps.GRAND
	+ ';white-space:nowrap;font-size:14px">'
	+ _money("doc.grand_total")
	+ "</td>\n"
	"    </tr>\n"
)

_TOTALS_CLOSE = "  </table>\n"

_TERMS = (
	"  {%- if doc.terms %}\n"
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("__TERMS_LABEL__")
	+ "    {#- Text Editor field: markup prints as authored, plain text keeps its lines. -#}\n"
	'    <div style="font-size:12px;">{{ ps_rich(doc.terms) }}</div>\n'
	"  </div>\n"
	"  {%- endif %}\n"
)


def _compose(
	*, eyebrow, title, date_field, facts, total_label, terms_label, tail="", closing="", state=False
):
	meta = '<span style="' + ps.STRONG + '">{{ doc.name }}</span><br>' + _date("doc." + date_field)
	if state:
		meta += "{{ ps_state(doc.docstatus) }}"
	html = ps.page_open(None)
	html += ps.letterhead(None, eyebrow, title, meta, ADDRESS_FIELD)
	html += ps.facts_open() + facts + ps.facts_close()
	html += _ITEMS
	html += _TOTALS_OPEN.replace("__TOTAL_LABEL__", total_label)
	html += tail
	html += _TOTALS_CLOSE
	html += _TERMS.replace("__TERMS_LABEL__", terms_label)
	html += closing
	html += ps.page_close(None)
	return html


# --- Quotation --------------------------------------------------------------------
# Validity sits in the facts row under the title when the quotation has one: a quote
# with no visible expiry is the one that comes back accepted at last year's price. None
# on this site does yet, so the payment terms take the slot when there are some, and an
# empty cell holds the row's shape when there are neither.

_QUOTATION_FACTS = (
	_party("PREPARED FOR")
	+ ps.fact(
		"PREPARED BY",
		'{%- if doc.owner == "Administrator" %}Sapphire Fountains'
		'{%- else %}{{ (frappe.db.get_value("User", doc.owner, "full_name") or doc.owner) | e }}{% endif %}',
		width="30%",
	)
	+ "{%- if doc.valid_till %}\n"
	+ ps.fact("VALID UNTIL", _date("doc.valid_till"), width="30%")
	+ '{%- elif doc.get("payment_terms_template") %}\n'
	+ ps.fact("PAYMENT TERMS", '{{ doc.get("payment_terms_template") | e }}', width="30%")
	+ "{%- else %}\n"
	+ '  <div style="display:table-cell;width:30%"></div>\n'
	+ "{%- endif %}\n"
)

_QUOTATION_ACCEPTANCE = (
	'  <div style="margin-top:18px; page-break-inside:avoid;">\n'
	'    <div style="font-size:12px;">'
	"{% if doc.valid_till %}Valid until " + _date("doc.valid_till") + ". {% endif %}"
	"Sign and return to accept this quotation.</div>\n"
	+ ps.signature_lines("ACCEPTED BY", "DATE")
	+ "  </div>\n"
)

_QUOTATION_HTML = _compose(
	eyebrow="QUOTATION",
	title="Quotation",
	date_field="transaction_date",
	facts=_QUOTATION_FACTS,
	total_label="Quotation total",
	terms_label="Terms &amp; conditions",
	closing=_QUOTATION_ACCEPTANCE,
)

# --- Sales Order ------------------------------------------------------------------

_SALES_ORDER_FACTS = (
	_party("BILL TO")
	+ _project_fact()
	+ ps.fact("DELIVERY DATE", _date_or_dash("doc.delivery_date"), width="30%")
)

_SALES_ORDER_HTML = _compose(
	eyebrow="SALES ORDER",
	title="Sales Order",
	date_field="transaction_date",
	facts=_SALES_ORDER_FACTS,
	total_label="Order total",
	terms_label="Terms &amp; conditions",
	state=True,
)

# --- Sales Invoice ----------------------------------------------------------------
# A credit note is a Sales Invoice with `is_return` set, so every heading that would call
# it an invoice asks first.

# "Due on receipt" when the due date is the invoice date (1,583 of 1,629), and the payment
# terms under it unless they say the same thing in other words.
_PAYMENT_DUE = (
	"{%- set due_on_receipt = not doc.due_date or doc.due_date == doc.posting_date %}"
	"{%- if due_on_receipt %}Due on receipt{% else %}" + _date("doc.due_date") + "{% endif %}"
	'{%- set terms_name = (doc.get("payment_terms_template") or "") | trim %}'
	'{%- if terms_name and not (due_on_receipt and (terms_name | lower | replace("upon", "on")) == "due on receipt") %}'
	'<br><span style="font-size:11.5px">{{ terms_name | e }}</span>{% endif %}'
)

_SALES_INVOICE_FACTS = (
	_party("BILL TO")
	+ _project_fact()
	+ "{%- if doc.is_return %}\n"
	+ ps.fact(
		"AGAINST INVOICE",
		"{% if doc.return_against %}{{ doc.return_against | e }}{% else %}" + _DASH + "{% endif %}",
		width="30%",
	)
	+ "{%- else %}\n"
	+ ps.fact("PAYMENT DUE", _PAYMENT_DUE, width="30%")
	+ "{%- endif %}\n"
)

# Payments received and Amount due, under the total, once something has been paid on a
# submitted invoice. A partly-paid invoice that shows only the total is the most common
# cause of a customer paying twice. Rounded before comparing: the difference of two
# floats that should be equal is not always 0.0.
_SALES_INVOICE_TAIL = (
	"    {%- if doc.docstatus == 1 and not doc.is_return and doc.outstanding_amount is not none %}\n"
	"    {%- set paid = ((doc.grand_total or 0) - doc.outstanding_amount) | round(2) %}\n"
	"    {%- if paid %}\n"
	"    {%- if paid > 0 %}\n"
	+ _total_row("Payments received", "-" + _money("paid"), extra=";padding-top:6px !important")
	+ "    {%- endif %}\n"
	+ _total_row("Amount due", _money("doc.outstanding_amount"), extra=";" + ps.STRONG)
	+ "    {%- endif %}\n"
	"    {%- endif %}\n"
)

# How to pay, on anything that is still owed. Not on a credit note (there is nothing to
# pay), not on a cancelled invoice, and not on a paid one, which says so instead.
_SALES_INVOICE_PAYMENT = (
	"  {%- if not doc.is_return and doc.docstatus != 2"
	" and (doc.outstanding_amount is none or doc.outstanding_amount > 0) %}\n"
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("How to pay")
	+ "    {#- The Stripe link renders only when one has actually been generated. No placeholder\n"
	'        button: an unusable "Pay now" is worse than none at all. -#}\n'
	'    {%- if doc.get("custom_stripe_payment_link") %}\n'
	'    <div style="margin-bottom:6px; font-size:12px;">\n'
	'      Pay online: <a href="{{ doc.custom_stripe_payment_link }}" style="color:' + ps.BAHAMA_BLUE + ';">'
	"{{ doc.custom_stripe_payment_link }}</a>\n"
	"    </div>\n"
	"    {%- endif %}\n"
	"    {%- if doc.company_address_display %}\n"
	'    <div style="font-size:12px;">Remit to:<br>{{ ps_address(doc.company_address_display) }}</div>\n'
	"    {%- else %}\n"
	'    <div style="font-size:12px;">Remit to {{ doc.company | e }}.</div>\n'
	"    {%- endif %}\n"
	'    <div style="margin-top:6px; font-size:12px;">Please quote <b>{{ doc.name }}</b> with your payment.</div>\n'
	"  </div>\n"
	"  {%- elif doc.docstatus == 1 and not doc.is_return"
	" and doc.outstanding_amount is not none and doc.outstanding_amount <= 0 %}\n"
	'  <div style="margin-top:16px; font-size:12.5px; '
	+ ps.STRONG
	+ '">Paid in full &mdash; thank you.</div>\n'
	"  {%- endif %}\n"
)

_SALES_INVOICE_HTML = _compose(
	eyebrow="{% if doc.is_return %}CREDIT NOTE{% else %}INVOICE{% endif %}",
	title="{% if doc.is_return %}Credit Note{% else %}Invoice{% endif %}",
	date_field="posting_date",
	facts=_SALES_INVOICE_FACTS,
	total_label="{% if doc.is_return %}Credit total{% else %}Invoice total{% endif %}",
	terms_label="Terms &amp; conditions",
	tail=_SALES_INVOICE_TAIL,
	closing=_SALES_INVOICE_PAYMENT,
	state=True,
)


FORMATS = (
	(QUOTATION_FORMAT, "Quotation", _QUOTATION_HTML),
	(SALES_ORDER_FORMAT, "Sales Order", _SALES_ORDER_HTML),
	(SALES_INVOICE_FORMAT, "Sales Invoice", _SALES_INVOICE_HTML),
)


def ensure_sales_print_formats():
	"""`after_migrate` entry point. Failures log rather than abort the migrate."""
	try:
		ensured = []
		for name, doc_type, html in FORMATS:
			if not frappe.db.exists("DocType", doc_type):
				continue
			_upsert_print_format(name, doc_type, html)
			ensured.append(name)
		frappe.db.commit()
		frappe.logger().info(f"Sales print formats: ensured {', '.join(ensured)}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Sales print formats")


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
	pf.save(ignore_permissions=True)

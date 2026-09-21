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
  why, and why the fieldname differs from Purchase Order's.
- **`description` is rendered unescaped, `item_name` escaped.** The item description is a
  Text Editor field holding markup authored in the Item master; escaping it printed a
  literal `&lt;div&gt;&lt;p&gt;` at the supplier. Every stock ERPNext format does the same.
- **Print-safe CSS only** — no flexbox, no grid, `page-break-inside: avoid` on rows, and a
  `thead` that repeats across pages. The PDF backend on this host has a history
  (docs/pdf-generation.md); it is not asked to do anything clever. `print_style` keeps
  the same discipline: its letterhead and facts rows are `display:table`.
- **No project column on the line table.** Project is a header field and the customer does
  not need it per line; it is shown in the facts row on Sales Order and Sales Invoice.
  Quotation has no `project` column at all on this site — do not add one there.
- **Sales documents take the neutral stripe.** A quotation can be for any of the four
  pillars and nothing on the document says which, so they carry the brand's dark band
  rather than guessing. Deriving the pillar from the linked project's value stream is
  the obvious next step and is deliberately not this one.

Deliberate content decisions:

- **The quotation prints its validity prominently**, in the facts row under the title. A
  quote with no visible expiry is the one that gets accepted at last year's price. It
  also carries acceptance lines, because a quotation is accepted by signing it.
- **The invoice prints Amount Due, not just the total**, because a partly-paid invoice
  showing only the grand total is the most common cause of a duplicate payment.
- **The Stripe pay link renders only when set** (`custom_stripe_payment_link`). Nothing is
  invented when it is absent, and no placeholder button is drawn — an unusable "Pay now"
  is worse than none.
- **Remit-to falls back to the company address** when no explicit payment instructions are
  written on the document, rather than printing nothing at all.
"""

import frappe

from erpnext_enhancements import print_style as ps

MODULE = "Enhancements Core"

# All three sales doctypes name the company address `company_address_display`, and all
# three carry one: 657 of 657 Quotations and every Sales Invoice on production. Purchase
# Order calls the same thing `billing_address_display`, which is why the letterhead takes
# the fieldname rather than assuming.
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


def _date(field):
	return "{{ frappe.format(doc." + field + ', {"fieldtype": "Date"}) }}'


def _date_or_dash(field):
	return "{% if doc." + field + " %}" + _date(field) + "{% else %}" + _DASH + "{% endif %}"


def _optional(field):
	return "{% if doc." + field + " %}{{ doc." + field + " | e }}{% else %}" + _DASH + "{% endif %}"


def _party(label):
	return ps.fact(
		label,
		'<span style="' + ps.STRONG + '">{{ doc.customer_name or doc.customer }}</span>'
		"{% if doc.address_display %}<br>{{ doc.address_display }}{% endif %}"
		"{% if doc.contact_display %}<br>Attn: {{ doc.contact_display }}{% endif %}",
		width="40%",
	)


_ITEMS = (
	'<table style="width:100%; border-collapse:collapse; margin-top:4px;">\n'
	'    <thead style="display:table-header-group;">\n'
	"      <tr>\n"
	'        <th style="' + ps.th() + '">Item</th>\n'
	'        <th style="' + ps.th() + '">Description</th>\n'
	'        <th style="' + ps.th(right=True) + '; white-space:nowrap;">Qty</th>\n'
	'        <th style="' + ps.th() + '">UOM</th>\n'
	'        <th style="' + ps.th(right=True) + '; white-space:nowrap;">Rate</th>\n'
	'        <th style="' + ps.th(right=True) + '; white-space:nowrap;">Amount</th>\n'
	"      </tr>\n"
	"    </thead>\n"
	"    <tbody>\n"
	"      {%- for row in doc.items %}\n"
	'      <tr style="page-break-inside:avoid;">\n'
	'        <td style="' + ps.TD + "; " + ps.STRONG + '">{{ row.item_code | e }}</td>\n'
	"        <!-- Rendered as HTML, NOT escaped: a Text Editor field holding markup from the\n"
	"             Item master. item_name below is plain Data and stays escaped. -->\n"
	'        <td style="' + ps.TD + '">\n'
	"          {%- if row.description %}{{ row.description }}{% else %}{{ row.item_name | e }}{% endif -%}\n"
	"        </td>\n"
	'        <td style="' + ps.TD_RIGHT + '">{{ row.qty }}</td>\n'
	'        <td style="' + ps.TD + '">{{ (row.uom or "") | e }}</td>\n'
	'        <td style="'
	+ ps.TD_RIGHT
	+ '">{{ frappe.utils.fmt_money(row.rate, currency=doc.currency) }}</td>\n'
	'        <td style="'
	+ ps.TD_RIGHT
	+ '">{{ frappe.utils.fmt_money(row.amount, currency=doc.currency) }}</td>\n'
	"      </tr>\n"
	"      {%- endfor %}\n"
	"      {%- if not doc.items %}\n"
	'      <tr><td colspan="6" style="'
	+ ps.TD
	+ '; font-style:italic;">No items on this document.</td></tr>\n'
	"      {%- endif %}\n"
	"    </tbody>\n"
	"  </table>\n"
)

_TOTAL_LABEL_TD = "text-align:right; padding:3px 8px; color:" + ps.INK_700 + ";"
_TOTAL_VALUE_TD = "text-align:right; padding:3px 8px; white-space:nowrap; color:" + ps.INK_700 + ";"
_GRAND_TD = (
	"text-align:right; padding:8px 8px 4px; border-top:2px solid " + ps.DEEP_SEA_BLUE + "; " + ps.STRONG
)

_TOTALS_OPEN = (
	'<table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">\n'
	"    <tr>\n"
	'      <td style="width:56%;"></td>\n'
	'      <td style="width:26%; ' + _TOTAL_LABEL_TD + '">Subtotal</td>\n'
	'      <td style="width:18%; '
	+ _TOTAL_VALUE_TD
	+ '">{{ frappe.utils.fmt_money(doc.net_total, currency=doc.currency) }}</td>\n'
	"    </tr>\n"
	'    {%- if doc.get("discount_amount") %}\n'
	"    <tr>\n"
	"      <td></td>\n"
	'      <td style="' + _TOTAL_LABEL_TD + '">Discount</td>\n'
	'      <td style="'
	+ _TOTAL_VALUE_TD
	+ '">-{{ frappe.utils.fmt_money(doc.discount_amount, currency=doc.currency) }}</td>\n'
	"    </tr>\n"
	"    {%- endif %}\n"
	"    {#- Renders whatever tax rows exist. Utah taxability is settled per document by the\n"
	"        tax template, so this needs no change when WI-036 lands. -#}\n"
	"    {%- for tax in doc.taxes %}\n"
	"    {%- if tax.tax_amount %}\n"
	"    <tr>\n"
	"      <td></td>\n"
	'      <td style="' + _TOTAL_LABEL_TD + '">{{ tax.description | e }}</td>\n'
	'      <td style="'
	+ _TOTAL_VALUE_TD
	+ '">{{ frappe.utils.fmt_money(tax.tax_amount, currency=doc.currency) }}</td>\n'
	"    </tr>\n"
	"    {%- endif %}\n"
	"    {%- endfor %}\n"
	"    <tr>\n"
	"      <td></td>\n"
	'      <td style="' + _GRAND_TD + '">__TOTAL_LABEL__</td>\n'
	'      <td style="' + _GRAND_TD + '; white-space:nowrap; font-size:14px;">'
	"{{ frappe.utils.fmt_money(doc.grand_total, currency=doc.currency) }}</td>\n"
	"    </tr>\n"
)

_TOTALS_CLOSE = "  </table>\n"

_TERMS = (
	"  {%- if doc.terms %}\n"
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("__TERMS_LABEL__")
	+ "    <!-- Text Editor field, rendered as authored. -->\n"
	'    <div style="font-size:12px;">{{ doc.terms }}</div>\n'
	"  </div>\n"
	"  {%- endif %}\n"
)


def _compose(*, label, title, date_field, facts, total_label, terms_label, tail="", closing=""):
	html = ps.page_open(None)
	html += ps.letterhead(
		None,
		label,
		title,
		'<span style="' + ps.STRONG + '">{{ doc.name }}</span><br>' + _date(date_field),
		ADDRESS_FIELD,
	)
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
# Validity sits in the facts row under the title on purpose: a quote with no visible
# expiry is the one that comes back accepted at last year's price.

_QUOTATION_FACTS = (
	_party("PREPARED FOR")
	+ ps.fact(
		"PREPARED BY", '{{ frappe.db.get_value("User", doc.owner, "full_name") or doc.owner }}', width="30%"
	)
	+ ps.fact("VALID UNTIL", _date_or_dash("valid_till"), width="30%")
)

_QUOTATION_ACCEPTANCE = (
	'  <div style="margin-top:18px; page-break-inside:avoid;">\n'
	'    <div style="font-size:12px;">Valid until the date above. Sign and return to accept this quotation.</div>\n'
	+ ps.signature_lines("ACCEPTED BY", "DATE")
	+ "  </div>\n"
)

_QUOTATION_HTML = _compose(
	label="QUOTATION",
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
	+ ps.fact(
		"YOUR REFERENCE",
		_optional("po_no") + '<br><span style="' + ps.LABEL + '">PROJECT</span><br>' + _optional("project"),
		width="30%",
	)
	+ ps.fact("DELIVERY DATE", _date_or_dash("delivery_date"), width="30%")
)

_SALES_ORDER_HTML = _compose(
	label="SALES ORDER",
	title="Sales Order",
	date_field="transaction_date",
	facts=_SALES_ORDER_FACTS,
	total_label="Order total",
	terms_label="Terms &amp; conditions",
)

# --- Sales Invoice ----------------------------------------------------------------
# Amount Due is printed as its own line below the grand total. A partly-paid invoice that
# shows only the total is the most common cause of a customer paying twice.

_SALES_INVOICE_FACTS = (
	_party("BILL TO")
	+ ps.fact(
		"YOUR REFERENCE",
		_optional("po_no") + '<br><span style="' + ps.LABEL + '">PROJECT</span><br>' + _optional("project"),
		width="30%",
	)
	+ ps.fact("PAYMENT DUE", _date_or_dash("due_date"), width="30%")
)

_SALES_INVOICE_TAIL = (
	"    {%- if doc.outstanding_amount is not none and doc.outstanding_amount != doc.grand_total %}\n"
	"    <tr>\n"
	"      <td></td>\n"
	'      <td style="' + _TOTAL_LABEL_TD + ' padding-top:6px;">Amount due</td>\n'
	'      <td style="' + _TOTAL_VALUE_TD + " padding-top:6px; " + ps.STRONG + '">'
	"{{ frappe.utils.fmt_money(doc.outstanding_amount, currency=doc.currency) }}</td>\n"
	"    </tr>\n"
	"    {%- endif %}\n"
)

_SALES_INVOICE_PAYMENT = (
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
	'    <div style="font-size:12px;">Remit to:<br>{{ doc.company_address_display }}</div>\n'
	"    {%- else %}\n"
	'    <div style="font-size:12px;">Remit to {{ doc.company | e }}.</div>\n'
	"    {%- endif %}\n"
	'    <div style="margin-top:6px; font-size:12px;">Please quote <b>{{ doc.name }}</b> with your payment.</div>\n'
	"  </div>\n"
)

_SALES_INVOICE_HTML = _compose(
	label="INVOICE",
	title="Invoice",
	date_field="posting_date",
	facts=_SALES_INVOICE_FACTS,
	total_label="Invoice total",
	terms_label="Terms &amp; conditions",
	tail=_SALES_INVOICE_TAIL,
	closing=_SALES_INVOICE_PAYMENT,
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

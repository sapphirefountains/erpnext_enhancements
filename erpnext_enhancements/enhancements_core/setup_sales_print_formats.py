"""Customer-facing print formats for Quotation, Sales Order and Sales Invoice (WI-020).

These are the documents customers actually receive, and from 2027-01-01 they leave
ERPNext rather than QuickBooks. Before this there were none: every sales document fell
back to the stock `* Standard` format, which is unbranded and shows internal fields.

Follows `setup_print_formats.py` (the Purchase Order format) exactly — same `after_migrate`
upsert, same self-contained `_upsert_print_format`, same print-safe CSS rules and palette.
Notes worth repeating because each was learned the expensive way:

- **The letter head must be rendered by the template.** With `custom_format = 1` Frappe
  never injects one — it builds the `#header-html` block only for *standard* formats. The
  Purchase Order format went to suppliers unbranded for a month on exactly this. A letter
  head exists on this site (`Sapphire Fountains Default`, is_default, logo at
  `/files/Logo-print.png`), so this is the only thing standing between it and the page.
- **And the letter head is only a logo**, so the template draws our name, address and phone
  beside it as well. That block is shared with the Purchase Order format rather than copied
  — see `company_contact`, which also explains why the address prefers each document's own
  `company_address_display` and the phone cannot.
- **`description` is rendered unescaped, `item_name` escaped.** The item description is a
  Text Editor field holding markup authored in the Item master; escaping it printed a
  literal `&lt;div&gt;&lt;p&gt;` at the supplier. Every stock ERPNext format does the same.
- **Print-safe CSS only** — no flexbox, no grid, `page-break-inside: avoid` on rows, and a
  `thead` that repeats across pages. The PDF backend on this host has a history
  (docs/pdf-generation.md); it is not asked to do anything clever.
- **No project column on the line table.** Project is a header field and the customer does
  not need it per line; it is shown in the meta block on Sales Order and Sales Invoice.
  Quotation has no `project` column at all on this site — do not add one there.

Deliberate content decisions:

- **The quotation prints its validity prominently.** A quote with no visible expiry is the
  one that gets accepted at last year's price.
- **The invoice prints Amount Due, not just the total**, because a partly-paid invoice
  showing only the grand total is the most common cause of a duplicate payment.
- **The Stripe pay link renders only when set** (`custom_stripe_payment_link`). Nothing is
  invented when it is absent, and no placeholder button is drawn — an unusable "Pay now"
  is worse than none.
- **Remit-to falls back to the company address** when no explicit payment instructions are
  written on the document, rather than printing nothing at all.
"""

import frappe

from erpnext_enhancements.enhancements_core.company_contact import contact_block

MODULE = "Enhancements Core"

# All three sales doctypes name the company address `company_address_display`, and all
# three carry one: 657 of 657 Quotations and every Sales Invoice on production. Purchase
# Order calls the same thing `billing_address_display`, which is why the block takes the
# fieldname rather than assuming.
ADDRESS_FIELD = "company_address_display"

QUOTATION_FORMAT = "Quotation - Sapphire"
SALES_ORDER_FORMAT = "Sales Order - Sapphire"
SALES_INVOICE_FORMAT = "Sales Invoice - Sapphire"

# --- shared fragments -------------------------------------------------------------
# Composed rather than triplicated: the line table and the totals block are genuinely
# identical across the three documents, and three copies would drift on the first edit.

_OPEN = (
	"""
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">

  {#- A custom Jinja format renders its own letterhead; Frappe injects one only for
      *standard* formats. See the module docstring. The block below draws the logo AND our
      name, address and phone, because the letter head carries none of the latter — it is a
      right-aligned logo and nothing else. Shared with the Purchase Order format so the two
      cannot drift; see `company_contact`. -#}
"""
	+ contact_block(ADDRESS_FIELD)
)

_TITLE_BAR = """
  <div style="display:table; width:100%; border-bottom:2px solid #333; padding-bottom:6px; margin-bottom:12px;">
    <div style="display:table-cell; vertical-align:bottom;">
      <h2 style="margin:0; font-size:20px;">__TITLE__</h2>
    </div>
    <div style="display:table-cell; vertical-align:bottom; text-align:right; color:#777;">
      <div style="font-size:14px; color:#222;"><b>{{ doc.name }}</b></div>
      <div>{{ frappe.format(doc.__DATE_FIELD__, {"fieldtype": "Date"}) }}</div>
    </div>
  </div>
"""

_PARTY_OPEN = """
  <table style="width:100%; border-collapse:collapse; margin-bottom:14px;">
    <tr>
      <td style="width:18%; color:#777; padding:2px 0; vertical-align:top;">To</td>
      <td style="width:32%; padding:2px 0; vertical-align:top;">
        <b>{{ doc.customer_name or doc.customer }}</b>
        {% if doc.address_display %}<div style="color:#555;">{{ doc.address_display }}</div>{% endif %}
        {% if doc.contact_display %}<div style="color:#555;">Attn: {{ doc.contact_display }}</div>{% endif %}
      </td>
"""

_PARTY_CLOSE = """
  </table>
"""

_ITEMS = """
  <table style="width:100%; border-collapse:collapse; font-size:11px;">
    <thead style="display:table-header-group;">
      <tr style="background:#f4f5f7;">
        <th style="text-align:left;  padding:6px 4px; border-bottom:2px solid #ccc;">Item</th>
        <th style="text-align:left;  padding:6px 4px; border-bottom:2px solid #ccc;">Description</th>
        <th style="text-align:right; padding:6px 4px; border-bottom:2px solid #ccc; white-space:nowrap;">Qty</th>
        <th style="text-align:left;  padding:6px 4px; border-bottom:2px solid #ccc;">UOM</th>
        <th style="text-align:right; padding:6px 4px; border-bottom:2px solid #ccc; white-space:nowrap;">Rate</th>
        <th style="text-align:right; padding:6px 4px; border-bottom:2px solid #ccc; white-space:nowrap;">Amount</th>
      </tr>
    </thead>
    <tbody>
      {%- for row in doc.items %}
      <tr style="page-break-inside:avoid;">
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top;">{{ row.item_code | e }}</td>
        <!-- Rendered as HTML, NOT escaped: a Text Editor field holding markup from the
             Item master. item_name below is plain Data and stays escaped. -->
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; color:#555;">
          {%- if row.description %}{{ row.description }}{% else %}{{ row.item_name | e }}{% endif -%}
        </td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; text-align:right; white-space:nowrap;">{{ row.qty }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top;">{{ (row.uom or "") | e }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; text-align:right; white-space:nowrap;">{{ frappe.utils.fmt_money(row.rate, currency=doc.currency) }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; text-align:right; white-space:nowrap;">{{ frappe.utils.fmt_money(row.amount, currency=doc.currency) }}</td>
      </tr>
      {%- endfor %}
      {%- if not doc.items %}
      <tr><td colspan="6" style="padding:8px 4px; color:#999; font-style:italic;">No items on this document.</td></tr>
      {%- endif %}
    </tbody>
  </table>
"""

_TOTALS_OPEN = """
  <table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">
    <tr>
      <td style="width:60%;"></td>
      <td style="width:22%; text-align:right; color:#777; padding:2px 4px;">Subtotal</td>
      <td style="width:18%; text-align:right; padding:2px 4px; white-space:nowrap;">{{ frappe.utils.fmt_money(doc.net_total, currency=doc.currency) }}</td>
    </tr>
    {%- if doc.get("discount_amount") %}
    <tr>
      <td></td>
      <td style="text-align:right; color:#777; padding:2px 4px;">Discount</td>
      <td style="text-align:right; padding:2px 4px; white-space:nowrap;">-{{ frappe.utils.fmt_money(doc.discount_amount, currency=doc.currency) }}</td>
    </tr>
    {%- endif %}
    {#- Renders whatever tax rows exist. Utah taxability is settled per document by the
        tax template, so this needs no change when WI-036 lands. -#}
    {%- for tax in doc.taxes %}
    {%- if tax.tax_amount %}
    <tr>
      <td></td>
      <td style="text-align:right; color:#777; padding:2px 4px;">{{ tax.description | e }}</td>
      <td style="text-align:right; padding:2px 4px; white-space:nowrap;">{{ frappe.utils.fmt_money(tax.tax_amount, currency=doc.currency) }}</td>
    </tr>
    {%- endif %}
    {%- endfor %}
    <tr>
      <td></td>
      <td style="text-align:right; padding:6px 4px; border-top:2px solid #333;"><b>__TOTAL_LABEL__</b></td>
      <td style="text-align:right; padding:6px 4px; border-top:2px solid #333; white-space:nowrap;">
        <b>{{ frappe.utils.fmt_money(doc.grand_total, currency=doc.currency) }}</b>
      </td>
    </tr>
"""

_TOTALS_CLOSE = """
  </table>
"""

_TERMS = """
  {%- if doc.terms %}
  <div style="margin-top:18px; page-break-inside:avoid;">
    <div style="color:#777; border-bottom:1px solid #eee; padding-bottom:3px; margin-bottom:6px;">__TERMS_LABEL__</div>
    <!-- Text Editor field, rendered as authored. -->
    <div style="color:#555;">{{ doc.terms }}</div>
  </div>
  {%- endif %}
"""

_CLOSE = """
</div>
"""


def _meta_cell(label, value_html):
	return (
		'      <td style="width:18%; color:#777; padding:2px 0; vertical-align:top;">'
		+ label
		+ "</td>\n"
		'      <td style="width:32%; padding:2px 0; vertical-align:top;">' + value_html + "</td>\n"
	)


def _date_or_dash(field):
	return (
		"{% if doc." + field + ' %}{{ frappe.format(doc.' + field + ', {"fieldtype": "Date"}) }}'
		'{% else %}<span style="color:#999;">&mdash;</span>{% endif %}'
	)


def _optional(field):
	return '{% if doc.' + field + " %}{{ doc." + field + ' | e }}{% else %}<span style="color:#999;">&mdash;</span>{% endif %}'


def _compose(title, date_field, meta_rows, total_label, terms_label, tail=""):
	html = _OPEN
	html += _TITLE_BAR.replace("__TITLE__", title).replace("__DATE_FIELD__", date_field)
	html += _PARTY_OPEN
	html += meta_rows
	html += _PARTY_CLOSE
	html += _ITEMS
	html += _TOTALS_OPEN.replace("__TOTAL_LABEL__", total_label)
	html += tail
	html += _TOTALS_CLOSE
	html += _TERMS.replace("__TERMS_LABEL__", terms_label)
	html += _CLOSE
	return html


# --- Quotation --------------------------------------------------------------------
# Validity is in the top-right meta block on purpose: a quote with no visible expiry is
# the one that comes back accepted at last year's price.

_QUOTATION_META = (
	_meta_cell("Valid until", _date_or_dash("valid_till"))
	+ "    </tr>\n    <tr>\n"
	+ _meta_cell("Prepared by", '{{ frappe.db.get_value("User", doc.owner, "full_name") or doc.owner }}')
	+ _meta_cell("&nbsp;", "&nbsp;")
	+ "    </tr>\n"
)

_QUOTATION_HTML = _compose(
	title="Quotation",
	date_field="transaction_date",
	meta_rows=_QUOTATION_META,
	total_label="Quotation total",
	terms_label="Terms &amp; conditions",
)

# --- Sales Order ------------------------------------------------------------------

_SALES_ORDER_META = (
	_meta_cell("Delivery date", _date_or_dash("delivery_date"))
	+ "    </tr>\n    <tr>\n"
	+ _meta_cell("Your reference", _optional("po_no"))
	+ _meta_cell("Project", _optional("project"))
	+ "    </tr>\n"
)

_SALES_ORDER_HTML = _compose(
	title="Sales Order",
	date_field="transaction_date",
	meta_rows=_SALES_ORDER_META,
	total_label="Order total",
	terms_label="Terms &amp; conditions",
)

# --- Sales Invoice ----------------------------------------------------------------
# Amount Due is printed as its own line below the grand total. A partly-paid invoice that
# shows only the total is the most common cause of a customer paying twice.

_SALES_INVOICE_META = (
	_meta_cell("Payment due", _date_or_dash("due_date"))
	+ "    </tr>\n    <tr>\n"
	+ _meta_cell("Your reference", _optional("po_no"))
	+ _meta_cell("Project", _optional("project"))
	+ "    </tr>\n"
)

_SALES_INVOICE_TAIL = """
    {%- if doc.outstanding_amount is not none and doc.outstanding_amount != doc.grand_total %}
    <tr>
      <td></td>
      <td style="text-align:right; padding:6px 4px; color:#777;">Amount due</td>
      <td style="text-align:right; padding:6px 4px; white-space:nowrap;">
        <b>{{ frappe.utils.fmt_money(doc.outstanding_amount, currency=doc.currency) }}</b>
      </td>
    </tr>
    {%- endif %}
"""

_SALES_INVOICE_PAYMENT = """
  <div style="margin-top:18px; page-break-inside:avoid;">
    <div style="color:#777; border-bottom:1px solid #eee; padding-bottom:3px; margin-bottom:6px;">How to pay</div>
    {#- The Stripe link renders only when one has actually been generated. No placeholder
        button: an unusable "Pay now" is worse than none at all. -#}
    {%- if doc.get("custom_stripe_payment_link") %}
    <div style="margin-bottom:6px;">
      Pay online: <a href="{{ doc.custom_stripe_payment_link }}" style="color:#0B6E4F;">{{ doc.custom_stripe_payment_link }}</a>
    </div>
    {%- endif %}
    {%- if doc.company_address_display %}
    <div style="color:#555;">Remit to:<br>{{ doc.company_address_display }}</div>
    {%- else %}
    <div style="color:#555;">Remit to {{ doc.company | e }}.</div>
    {%- endif %}
    <div style="color:#555; margin-top:6px;">Please quote <b>{{ doc.name }}</b> with your payment.</div>
  </div>
"""

_SALES_INVOICE_HTML = _compose(
	title="Invoice",
	date_field="posting_date",
	meta_rows=_SALES_INVOICE_META,
	total_label="Invoice total",
	terms_label="Terms &amp; conditions",
	tail=_SALES_INVOICE_TAIL,
).replace(_CLOSE, _SALES_INVOICE_PAYMENT + _CLOSE)


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

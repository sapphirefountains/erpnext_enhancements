"""Our own name, address and phone, as printed at the top of every outgoing document.

One copy, shared by the Purchase Order format (`setup_print_formats`) and the three
customer-facing sales formats (`setup_sales_print_formats`). It lives in its own module
rather than in either of them because it belongs to neither: a supplier and a customer get
the same letterhead, and a second copy would drift on the first edit — which is the same
reason the sales module composes its line table from one fragment instead of three.

**The letter head cannot carry this.** `Sapphire Fountains Default` is a right-aligned logo
and nothing else: no address, no phone, empty footer. Verified on production. So every
custom-format template has to draw the contact details itself, exactly as it already has to
draw the logo (see either setup module for that story).

The address and the phone are here for opposite reasons, and it matters which is which.

The **address is a fallback, not the source.** Each document already carries the company
address it was raised against, and the template prefers it — so a document naming a
different company address prints what it actually says rather than a constant contradicting
it. On production that field is populated on 657 of 657 Quotations, 1000 of 1000 Sales
Invoices, and 147 of the 157 Purchase Orders. The constant is for the remainder: a
customer-facing document with a blank address block is precisely the silent defect these
formats exist to avoid.

Note the field is **not the same on both sides**. Sales documents call it
`company_address_display`; Purchase Order calls it `billing_address_display` and has no
`company_address` at all. That is why `contact_block` takes the fieldname rather than
hard-coding one — getting it wrong prints nothing and raises nothing, since Jinja renders a
missing attribute as empty.

The **phone is not a fallback, because there is nothing to fall back from**: the number is
stored nowhere in ERPNext. `Company.phone_no` is null, and so is `Address.phone` on
`Sapphire Fountain-Billing`, the one company address record on the site. Fill either in and
this can become data-driven like the address; until then `COMPANY_PHONE` is the only copy of
the number anywhere, and the line to edit when it changes.
"""

COMPANY_ADDRESS_HTML = "85 W 300 S<br>Bountiful, UT 84010"
COMPANY_PHONE = "+1 (801)-837-2199"

# Two table cells, not flexbox: the PDF backend on this host does not lay out flex or grid
# reliably (docs/pdf-generation.md). The logo keeps its own cell rather than a row of its
# own so that, bottom-aligned against the address, the two read as one letterhead — and an
# absent letter head leaves the cell empty instead of collapsing the layout.
#
# `{{ letter_head }}` must appear literally: a custom_format template is handed the letter
# head in its render args and Frappe injects nothing, so this markup is the only thing
# standing between the logo and the page.
_BLOCK = """
  <div style="display:table; width:100%; margin-bottom:10px;">
    <div style="display:table-cell; width:55%; vertical-align:bottom; line-height:1.4;">
      <div><b>{{ doc.company | e }}</b></div>
      {#- Rendered as HTML, NOT escaped: __ADDRESS_FIELD__ is a Text Editor field holding
          `line<br>line<br>`, and the party's own `address_display` elsewhere in these
          templates is rendered the same way. -#}
      <div style="color:#555;">
        {%- if doc.__ADDRESS_FIELD__ %}{{ doc.__ADDRESS_FIELD__ }}{% else %}__COMPANY_ADDRESS__{% endif -%}
      </div>
      <div style="color:#555;">__COMPANY_PHONE__</div>
    </div>
    <div style="display:table-cell; width:45%; vertical-align:bottom;">
      {%- if letter_head %}{{ letter_head }}{% endif %}
    </div>
  </div>
"""


def contact_block(address_field):
	"""The letterhead block, wired to whichever company-address field the doctype has.

	`address_field` is `company_address_display` on Quotation / Sales Order / Sales Invoice
	and `billing_address_display` on Purchase Order.

	Substituted rather than interpolated: the markup is full of Jinja braces, so f-strings
	and `%`/`.format` are all unavailable.
	"""
	return (
		_BLOCK.replace("__ADDRESS_FIELD__", address_field)
		.replace("__COMPANY_ADDRESS__", COMPANY_ADDRESS_HTML)
		.replace("__COMPANY_PHONE__", COMPANY_PHONE)
	)

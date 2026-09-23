"""Print formats for the rest of procurement, in the Purchase Order's design.

`Purchase Order - Sapphire` has been on the print design system since v1.495.0; the five
documents either side of it in the buying chain were not, and printed on ERPNext's stock
formats — or, for a Material Request, on nothing but `Standard`. So a job's paperwork came
out in two looks, and the Procurement Tracker's Print dialog (v1.517.0), which prints a
whole group at once, made the difference hard to miss. These are the five:

- **Material Request - Sapphire** — internal. What a job needs, by when, and where it goes.
- **Request for Quotation - Sapphire** — goes to suppliers, one copy per supplier.
- **Supplier Quotation - Sapphire** — our record of a price a supplier gave us.
- **Purchase Receipt - Sapphire** — a goods-received note, with a line to sign.
- **Purchase Invoice - Sapphire** — the supplier's bill as we hold it.

The chrome is `print_style`'s, exactly as the Purchase Order uses it (docs/print-design-system.md):
neutral stripe, the wordmark with our address and phone, eyebrow over a display-face title,
two ruled rows of facts, a ruled line table, and the totals block in the order's own styles
— `tests/test_procurement_print_formats.py` holds those styles equal to the order's, so the
family cannot drift apart one edit at a time. Same `after_migrate` upsert and the same
self-contained `_upsert_print_format` as the order and the sales formats.

Decisions, each for a reason:

- **The neutral stripe, all five.** The order takes it because a job can belong to any
  pillar and nothing on the document says which; the same is true of every document here.
- **Our address comes from `billing_address_display`**, the buying doctypes' name for it —
  the Purchase Order's field, filled on 96 of 97 receipts and every invoice on production.
  A Material Request has no company address at all, so it prints the constant.
- **The RFQ is addressed to `doc.vendor`.** ERPNext prints an RFQ once per supplier: before
  emailing each one it sets `vendor` (and each line's `supplier_part_no`) and renders
  again, and `before_print` does the same with the first supplier for the preview. With no
  vendor set, every supplier on the request is listed instead. No prices on it — the
  supplier supplies those.
- **The receipt prints quantities, not money.** It is the paper a receiver signs against a
  delivery, and it is read at a tailgate. The rejected column appears only when something
  was rejected, which on production has never happened.
- **A return says so in its title** — Purchase Return, Debit Note — because a return
  printed under the ordinary title reads as a second delivery or a second bill.
- **Nothing is invented.** A blank date or reference prints a dash, not a guess; the
  invoice prints Amount due only when it differs from the total, like the sales invoice.
- **`description` is rendered unescaped and `item_name` escaped**, as on every format here:
  the description is a Text Editor field holding markup from the Item master.
"""

import frappe

from erpnext_enhancements import print_style as ps

MODULE = "Enhancements Core"

MATERIAL_REQUEST_FORMAT = "Material Request - Sapphire"
REQUEST_FOR_QUOTATION_FORMAT = "Request for Quotation - Sapphire"
SUPPLIER_QUOTATION_FORMAT = "Supplier Quotation - Sapphire"
PURCHASE_RECEIPT_FORMAT = "Purchase Receipt - Sapphire"
PURCHASE_INVOICE_FORMAT = "Purchase Invoice - Sapphire"

#: Our address on the four supplier-facing buying doctypes — the Purchase Order's field.
ADDRESS_FIELD = "billing_address_display"

# --- the Purchase Order's own styles ------------------------------------------------
# The same values as `setup_print_formats` (the order). Restated rather than imported
# because that module's names are private; the format suite holds them equal.

_DASH = '<span style="color:' + ps.INK_700 + ';">&mdash;</span>'
_TOTAL_LABEL_TD = "text-align:right; padding:3px 8px; color:" + ps.INK_700 + ";"
_TOTAL_VALUE_TD = "text-align:right; padding:3px 8px; white-space:nowrap; color:" + ps.INK_700 + ";"
_GRAND_TD = (
	"text-align:right; padding:8px 8px 4px; border-top:2px solid " + ps.DEEP_SEA_BLUE + "; " + ps.STRONG
)

# --- fragments ----------------------------------------------------------------------
# Concatenated, never interpolated: the markup is full of Jinja braces.


def _date(expr):
	return "{{ frappe.format(" + expr + ', {"fieldtype": "Date"}) }}'


def _date_or_dash(expr):
	return "{% if " + expr + " %}" + _date(expr) + "{% else %}" + _DASH + "{% endif %}"


def _text_or_dash(expr):
	return "{% if " + expr + " %}{{ " + expr + " | e }}{% else %}" + _DASH + "{% endif %}"


def _meta(date_expr):
	return '<span style="' + ps.STRONG + '">{{ doc.name }}</span><br>' + _date(date_expr)


# Who to ask, and who wrote it down — the order's QUESTIONS TO.
_OWNER = '{{ frappe.db.get_value("User", doc.owner, "full_name") or doc.owner }}<br>{{ doc.owner }}'


def _supplier(label):
	return ps.fact(
		label,
		'<span style="' + ps.STRONG + '">{{ (doc.supplier_name or doc.supplier) | e }}</span>'
		"{% if doc.address_display %}<br>{{ doc.address_display }}{% endif %}"
		"{% if doc.contact_display %}<br>Attn: {{ doc.contact_display }}{% endif %}",
		width="40%",
	)


def _project(expr, width="30%"):
	"""The project's number, and its name under it when it has a different one — the
	order's meta block does the same, because nobody files by PRJ-00706."""
	return ps.fact(
		"PROJECT",
		"{%- set prj = " + expr + " %}"
		'{%- if prj %}<span style="' + ps.STRONG + '">{{ prj | e }}</span>'
		'{%- set prj_name = frappe.db.get_value("Project", prj, "project_name") %}'
		"{%- if prj_name and prj_name != prj %}<br>{{ prj_name | e }}{% endif %}"
		"{%- else %}" + _DASH + "{% endif %}",
		width=width,
	)


def _th(label, right=False):
	style = ps.th(right=right) + ("; white-space:nowrap;" if right else "")
	return '        <th style="' + style + '">' + label + "</th>\n"


_ITEM_CELL = '        <td style="' + ps.TD + "; " + ps.STRONG + '">{{ row.item_code | e }}</td>\n'

# Rendered as HTML, NOT escaped: a Text Editor field holding markup from the Item master.
# Escaping it printed a literal "&lt;div&gt;&lt;p&gt;" at the supplier on the order.
_DESCRIPTION_CELL = (
	'        <td style="' + ps.TD + '">\n'
	"          {%- if row.description %}{{ row.description }}{% else %}{{ row.item_name | e }}{% endif -%}\n"
	"__DESCRIPTION_EXTRA__"
	"        </td>\n"
)


def _td(expr_html, right=False):
	return '        <td style="' + (ps.TD_RIGHT if right else ps.TD) + '">' + expr_html + "</td>\n"


def _money(expr):
	return "{{ frappe.utils.fmt_money(" + expr + ", currency=doc.currency) }}"


def _items(headers, cells, colspan, empty_text, description_extra="", preamble=""):
	return (
		preamble + '<table style="width:100%; border-collapse:collapse; margin-top:4px;">\n'
		'    <thead style="display:table-header-group;">\n'
		"      <tr>\n" + "".join(headers) + "      </tr>\n"
		"    </thead>\n"
		"    <tbody>\n"
		"      {%- for row in doc.items %}\n"
		'      <tr style="page-break-inside:avoid;">\n'
		+ _ITEM_CELL
		+ _DESCRIPTION_CELL.replace("__DESCRIPTION_EXTRA__", description_extra)
		+ "".join(cells)
		+ "      </tr>\n"
		"      {%- endfor %}\n"
		"      {%- if not doc.items %}\n"
		'      <tr><td colspan="'
		+ colspan
		+ '" style="'
		+ ps.TD
		+ '; font-style:italic;">'
		+ empty_text
		+ "</td></tr>\n"
		"      {%- endif %}\n"
		"    </tbody>\n"
		"  </table>\n"
	)


def _totals(grand_label, tail=""):
	"""The order's totals block: net, each non-zero tax, then the grand total under a rule."""
	return (
		'<table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">\n'
		"    <tr>\n"
		'      <td style="width:56%;"></td>\n'
		'      <td style="width:26%; ' + _TOTAL_LABEL_TD + '">Net total</td>\n'
		'      <td style="width:18%; ' + _TOTAL_VALUE_TD + '">' + _money("doc.net_total") + "</td>\n"
		"    </tr>\n"
		"    {%- for tax in doc.taxes %}\n"
		"    {%- if tax.tax_amount %}\n"
		"    <tr>\n"
		"      <td></td>\n"
		'      <td style="' + _TOTAL_LABEL_TD + '">{{ tax.description | e }}</td>\n'
		'      <td style="' + _TOTAL_VALUE_TD + '">' + _money("tax.tax_amount") + "</td>\n"
		"    </tr>\n"
		"    {%- endif %}\n"
		"    {%- endfor %}\n"
		"    <tr>\n"
		"      <td></td>\n"
		'      <td style="' + _GRAND_TD + '">' + grand_label + "</td>\n"
		'      <td style="'
		+ _GRAND_TD
		+ '; white-space:nowrap; font-size:14px;">'
		+ _money("doc.grand_total")
		+ "</td>\n"
		"    </tr>\n" + tail + "  </table>\n"
	)


def _terms(label="Terms &amp; conditions"):
	return (
		"  {%- if doc.terms %}\n"
		'  <div style="page-break-inside:avoid;">\n'
		+ ps.section_title(label)
		+ "    <!-- Text Editor field, rendered as authored. -->\n"
		'    <div style="font-size:12px;">{{ doc.terms }}</div>\n'
		"  </div>\n"
		"  {%- endif %}\n"
	)


def _compose(eyebrow, title, meta, address_field, body):
	return (
		ps.page_open(None)
		+ ps.letterhead(None, eyebrow, title, meta, address_field)
		+ body
		+ ps.page_close(None)
	)


# --- Material Request ---------------------------------------------------------------
# Internal: what the job needs, by when, and where it goes. No prices — a request's
# rates are zero on this site, and a column of $0.00 reads as a costing.
# `custom_project` through `doc.get`: it is this app's field, and a site that has not yet
# migrated it must print a dash rather than fail the render.

_MATERIAL_REQUEST_FACTS = (
	ps.facts_open()
	+ _project('doc.get("custom_project")', width="40%")
	+ ps.fact("REQUIRED BY", _date_or_dash("doc.schedule_date"), width="30%")
	+ ps.fact(
		"DELIVER TO",
		"{% if doc.set_warehouse %}{{ doc.set_warehouse | e }}{% else %}See each line{% endif %}",
		width="30%",
	)
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ ps.fact("REQUEST STATUS", "{{ doc.status }}", width="40%")
	+ ps.fact("TYPE", _text_or_dash("doc.material_request_type"), width="30%")
	+ ps.fact("REQUESTED BY", _OWNER, width="30%")
	+ ps.facts_close()
)

_MATERIAL_REQUEST_HTML = _compose(
	"MATERIAL REQUEST",
	"Material Request",
	_meta("doc.transaction_date"),
	None,
	_MATERIAL_REQUEST_FACTS
	+ _items(
		[
			_th("Item"),
			_th("Description"),
			_th("Qty", right=True),
			_th("UOM"),
			_th("Required by"),
			_th("Warehouse"),
		],
		[
			_td("{{ row.qty }}", right=True),
			_td('{{ (row.uom or "") | e }}'),
			_td(_date_or_dash("row.schedule_date")),
			_td('{{ (row.warehouse or "") | e }}'),
		],
		"6",
		"No items on this request.",
	)
	+ _terms(),
)

# --- Request for Quotation -----------------------------------------------------------
# Goes to suppliers. ERPNext renders it once per supplier with `vendor` set; the listing
# fallback covers a render with none.

_RFQ_SUPPLIER = ps.fact(
	"SUPPLIER",
	'{%- if doc.get("vendor") %}'
	'<span style="' + ps.STRONG + '">'
	'{{ (frappe.db.get_value("Supplier", doc.vendor, "supplier_name") or doc.vendor) | e }}</span>'
	"{%- else %}"
	"{%- for s in doc.suppliers %}"
	'<span style="' + ps.STRONG + '">{{ (s.supplier_name or s.supplier) | e }}</span>'
	"{% if not loop.last %}<br>{% endif %}"
	"{%- else %}" + _DASH + "{%- endfor %}"
	"{%- endif %}",
	width="40%",
)

_RFQ_FACTS = (
	ps.facts_open()
	+ _RFQ_SUPPLIER
	+ ps.fact("REQUIRED BY", _date_or_dash("doc.schedule_date"), width="30%")
	+ ps.fact(
		"DELIVER TO",
		"{% if doc.shipping_address_display %}{{ doc.shipping_address_display }}{% else %}Not specified{% endif %}",
		width="30%",
	)
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ _project('doc.get("custom_project")', width="40%")
	+ ps.fact("QUESTIONS TO", _OWNER, width="60%")
	+ ps.facts_close()
)

# The supplier's own part number, when ERPNext has one for this vendor, under the
# description rather than as a column that would be empty on every request so far.
_PART_NO = (
	"          {%- if row.supplier_part_no %}<br>"
	'<span style="' + ps.LABEL + '">YOUR PART NO.</span> {{ row.supplier_part_no | e }}{% endif %}\n'
)

_RFQ_CLOSING = (
	# message_for_supplier is a Text Editor field, so it is rendered as authored.
	"  {%- if doc.message_for_supplier %}\n"
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("Notes")
	+ '    <div style="font-size:12px;">{{ doc.message_for_supplier }}</div>\n'
	"  </div>\n"
	"  {%- endif %}\n"
	+ _terms()
	+ '  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("Your quotation")
	+ '    <div style="font-size:12px;">Please quote <b>{{ doc.name }}</b> on your quotation, with '
	"prices, lead times and any delivery charges, and send it to the buyer named above.</div>\n"
	"  </div>\n"
)

_REQUEST_FOR_QUOTATION_HTML = _compose(
	"REQUEST FOR QUOTATION",
	"Request for Quotation",
	_meta("doc.transaction_date"),
	ADDRESS_FIELD,
	_RFQ_FACTS
	+ _items(
		[_th("Item"), _th("Description"), _th("Qty", right=True), _th("UOM"), _th("Required by")],
		[
			_td("{{ row.qty }}", right=True),
			_td('{{ (row.uom or "") | e }}'),
			_td(_date_or_dash("row.schedule_date")),
		],
		"5",
		"No items on this request.",
		description_extra=_PART_NO,
	)
	+ _RFQ_CLOSING,
)

# --- Supplier Quotation --------------------------------------------------------------
# Our record of a supplier's price. Validity is in the first row for the same reason the
# customer quotation puts it there: an expired quote is the one somebody orders against.

_SUPPLIER_QUOTATION_FACTS = (
	ps.facts_open()
	+ _supplier("SUPPLIER")
	+ ps.fact("VALID UNTIL", _date_or_dash("doc.valid_till"), width="30%")
	+ _project("doc.project")
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ ps.fact("QUOTE STATUS", "{{ doc.status }}", width="40%")
	+ ps.fact("RECORDED BY", _OWNER, width="60%")
	+ ps.facts_close()
)

_PRICED_HEADERS = [
	_th("Item"),
	_th("Description"),
	_th("Qty", right=True),
	_th("UOM"),
	_th("Rate", right=True),
	_th("Amount", right=True),
]
_PRICED_CELLS = [
	_td("{{ row.qty }}", right=True),
	_td('{{ (row.uom or "") | e }}'),
	_td(_money("row.rate"), right=True),
	_td(_money("row.amount"), right=True),
]

_SUPPLIER_QUOTATION_HTML = _compose(
	"SUPPLIER QUOTATION",
	"Supplier Quotation",
	_meta("doc.transaction_date"),
	ADDRESS_FIELD,
	_SUPPLIER_QUOTATION_FACTS
	+ _items(_PRICED_HEADERS, _PRICED_CELLS, "6", "No items on this quotation.")
	+ _totals("Quotation total")
	+ _terms(),
)

# --- Purchase Receipt ----------------------------------------------------------------
# The paper a receiver signs against a delivery: quantities, the order each line came
# from, and where it went. No money.

_RECEIPT_ORDERS = (
	'{%- set receipt_orders = doc.items | map(attribute="purchase_order") | select | unique | list %}'
	"{%- if receipt_orders %}{{ receipt_orders | join(', ') | e }}{% else %}" + _DASH + "{% endif %}"
)

_PURCHASE_RECEIPT_FACTS = (
	ps.facts_open()
	+ _supplier("SUPPLIER")
	+ ps.fact(
		"RECEIVED INTO",
		"{% if doc.set_warehouse %}{{ doc.set_warehouse | e }}{% else %}See each line{% endif %}",
		width="30%",
	)
	+ ps.fact("SUPPLIER DELIVERY NOTE", _text_or_dash("doc.supplier_delivery_note"), width="30%")
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ ps.fact("AGAINST ORDER", _RECEIPT_ORDERS, width="40%")
	+ _project("doc.project")
	+ ps.fact("RECORDED BY", _OWNER, width="30%")
	+ ps.facts_close()
)

# The Rejected column exists only when something was rejected. A column of zeros on every
# receipt would train the eye to skip it on the one that matters.
_HAS_REJECTED = '  {%- set has_rejected = (doc.items | selectattr("rejected_qty") | list | length) > 0 %}\n'

_PURCHASE_RECEIPT_ITEMS = _items(
	[
		_th("Item"),
		_th("Description"),
		_th("Order"),
		_th("Qty", right=True),
		"        {%- if has_rejected %}\n" + _th("Rejected", right=True) + "        {%- endif %}\n",
		_th("UOM"),
		_th("Warehouse"),
	],
	[
		_td('{{ (row.purchase_order or "") | e }}'),
		_td("{{ row.qty }}", right=True),
		"        {%- if has_rejected %}\n"
		+ _td("{% if row.rejected_qty %}{{ row.rejected_qty }}{% endif %}", right=True)
		+ "        {%- endif %}\n",
		_td('{{ (row.uom or "") | e }}'),
		_td('{{ (row.warehouse or "") | e }}'),
	],
	"{{ 7 if has_rejected else 6 }}",
	"No items on this receipt.",
	preamble=_HAS_REJECTED,
)

_PURCHASE_RECEIPT_HTML = _compose(
	"{% if doc.is_return %}PURCHASE RETURN{% else %}PURCHASE RECEIPT{% endif %}",
	"{% if doc.is_return %}Purchase Return{% else %}Purchase Receipt{% endif %}",
	_meta("doc.posting_date"),
	ADDRESS_FIELD,
	_PURCHASE_RECEIPT_FACTS
	+ _PURCHASE_RECEIPT_ITEMS
	+ _terms()
	+ '  <div style="margin-top:18px; page-break-inside:avoid;">\n'
	'    <div style="font-size:12px;">Received in the quantities above, in good order unless noted.</div>\n'
	+ ps.signature_lines("RECEIVED BY", "DATE")
	+ "  </div>\n",
)

# --- Purchase Invoice ----------------------------------------------------------------
# The supplier's bill as we hold it. Their own invoice number sits beside ours, because
# it is the number their accounts department will quote back.

_PURCHASE_INVOICE_FACTS = (
	ps.facts_open()
	+ _supplier("SUPPLIER")
	+ ps.fact(
		"SUPPLIER INVOICE",
		"{% if doc.bill_no %}{{ doc.bill_no | e }}"
		"{% if doc.bill_date %}<br>" + _date("doc.bill_date") + "{% endif %}"
		"{% else %}" + _DASH + "{% endif %}",
		width="30%",
	)
	+ ps.fact("PAYMENT DUE", _date_or_dash("doc.due_date"), width="30%")
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ ps.fact("INVOICE STATUS", "{{ doc.status }}", width="40%")
	+ _project("doc.project")
	+ ps.fact("RECORDED BY", _OWNER, width="30%")
	+ ps.facts_close()
)

# Amount due only when it differs from the total — the sales invoice's rule.
_AMOUNT_DUE = (
	"    {%- if doc.outstanding_amount is not none and doc.outstanding_amount != doc.grand_total %}\n"
	"    <tr>\n"
	"      <td></td>\n"
	'      <td style="' + _TOTAL_LABEL_TD + ' padding-top:6px;">Amount due</td>\n'
	'      <td style="'
	+ _TOTAL_VALUE_TD
	+ " padding-top:6px; "
	+ ps.STRONG
	+ '">'
	+ _money("doc.outstanding_amount")
	+ "</td>\n"
	"    </tr>\n"
	"    {%- endif %}\n"
)

# The order's payment-terms section, unchanged in substance.
_PAYMENT_TERMS = (
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("Payment terms")
	+ """    {%- if doc.payment_terms_template or doc.payment_schedule %}
      {%- if doc.payment_terms_template %}<div>{{ doc.payment_terms_template | e }}</div>{% endif %}
      {%- for term in doc.payment_schedule %}
      {%- set term_label = (term.description or term.payment_term or "") %}
      <div>
        {%- if term_label %}{{ term_label | e }} &mdash; {% endif %}
        {{- frappe.utils.fmt_money(term.payment_amount, currency=doc.currency) }}
        {%- if term.due_date %} due {{ frappe.format(term.due_date, {"fieldtype": "Date"}) }}{% endif %}
      </div>
      {%- endfor %}
    {%- else %}
      <div style="font-style:italic;">As agreed.</div>
    {%- endif %}
  </div>
"""
)

_PURCHASE_INVOICE_HTML = _compose(
	"{% if doc.is_return %}DEBIT NOTE{% else %}PURCHASE INVOICE{% endif %}",
	"{% if doc.is_return %}Debit Note{% else %}Purchase Invoice{% endif %}",
	_meta("doc.posting_date"),
	ADDRESS_FIELD,
	_PURCHASE_INVOICE_FACTS
	+ _items(
		[
			_th("Item"),
			_th("Description"),
			_th("Order"),
			_th("Qty", right=True),
			_th("UOM"),
			_th("Rate", right=True),
			_th("Amount", right=True),
		],
		[_td('{{ (row.purchase_order or "") | e }}'), *_PRICED_CELLS],
		"7",
		"No items on this invoice.",
	)
	+ _totals("Invoice total", tail=_AMOUNT_DUE)
	+ _PAYMENT_TERMS
	+ _terms(),
)


FORMATS = (
	(MATERIAL_REQUEST_FORMAT, "Material Request", _MATERIAL_REQUEST_HTML),
	(REQUEST_FOR_QUOTATION_FORMAT, "Request for Quotation", _REQUEST_FOR_QUOTATION_HTML),
	(SUPPLIER_QUOTATION_FORMAT, "Supplier Quotation", _SUPPLIER_QUOTATION_HTML),
	(PURCHASE_RECEIPT_FORMAT, "Purchase Receipt", _PURCHASE_RECEIPT_HTML),
	(PURCHASE_INVOICE_FORMAT, "Purchase Invoice", _PURCHASE_INVOICE_HTML),
)


def ensure_procurement_print_formats():
	"""`after_migrate` entry point. Failures log rather than abort the migrate."""
	try:
		ensured = []
		for name, doc_type, html in FORMATS:
			if not frappe.db.exists("DocType", doc_type):
				continue
			_upsert_print_format(name, doc_type, html)
			ensured.append(name)
		frappe.db.commit()
		frappe.logger().info(f"Procurement print formats: ensured {', '.join(ensured)}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Procurement print formats")


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

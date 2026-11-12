"""The Supplier Return's print format, in the Purchase Order's design (v1.807.0).

**Supplier Return - Sapphire** is the PDF attached to the return request we email a supplier,
and the paper that goes in the box. It is built from ``print_style``'s chrome and from the
fragments the other buying documents use (``enhancements_core/setup_procurement_print_formats``),
so a job's paperwork stays in one look: neutral stripe, the wordmark with our address and phone,
a display-face title, two ruled rows of facts, a ruled line table and the shared totals styles.

Decisions, each for a reason:

- **The title is "Return Request"**, not "Supplier Return": the supplier reads it, and to them it
  is a request. The eyebrow carries our name for the document.
- **Rates and amounts are printed.** The supplier is being asked for money back, and the figure we
  expect is the first thing their accounts department checks. They are the receipt's own rates.
- **The reason and the condition notes sit under each line's name**, where the person unpacking
  the box will read them, rather than in a column of their own: seven columns is what fits.
- **Money lines print only when they are not zero**, and "Expected back" prints only for a refund
  or a credit. A replacement asks for goods, and a row of $0.00 reads as a costing.
- **The supplier's return number and the tracking number print once they are known**, so the same
  format serves as the request (before) and the packing slip (after).
- **Nothing internal is printed**: no stock handling, no outcome, no "typed by hand". Those are
  ours.
- **``doc.get`` for every field of ours.** A print rendered on a site that has not finished
  migrating must show a dash, not fail.

Same ``after_migrate`` upsert as the other formats. It must sit above
``ensure_chrome_pdf_generator`` in ``hooks.py``, or it renders on wkhtmltopdf.
"""

import frappe

from erpnext_enhancements import print_style as ps
from erpnext_enhancements.enhancements_core import setup_procurement_print_formats as buying

MODULE = "Inventory Enhancements"
DOCTYPE = "Supplier Return"
FORMAT = "Supplier Return - Sapphire"

_DASH = buying._DASH


def _money(expr):
	return "{{ frappe.utils.fmt_money(" + expr + ") }}"


_ORDERS = (
	'{%- set return_orders = doc.items | map(attribute="purchase_order") | select | unique | list %}'
	"{%- if return_orders %}{{ return_orders | join(', ') | e }}{% else %}" + _DASH + "{% endif %}"
)

_RECEIVED_ON = (
	'{%- set received_on = frappe.db.get_value("Purchase Receipt", doc.get("purchase_receipt"), "posting_date") %}'
	'<span style="' + ps.STRONG + '">{{ (doc.get("purchase_receipt") or "") | e }}</span>'
	"{%- if received_on %}<br>{{ ps_date(received_on) }}{% endif %}"
)

_GOING_BACK = (
	'{%- if doc.get("return_method") %}{{ doc.get("return_method") | e }}'
	'{%- if doc.get("carrier") %}<br>{{ doc.get("carrier") | e }}'
	'{%- if doc.get("tracking_number") %} {{ doc.get("tracking_number") | e }}{% endif %}{% endif %}'
	"{%- else %}" + _DASH + "{% endif %}"
)

_FACTS = (
	ps.facts_open()
	+ buying._supplier("SUPPLIER")
	+ ps.fact("WE ARE ASKING FOR", '{{ (doc.get("requested_resolution") or "") | e }}', width="30%")
	+ ps.fact(
		"YOUR RETURN NUMBER",
		'{% if doc.get("supplier_return_number") %}<span style="'
		+ ps.STRONG
		+ '">{{ doc.get("supplier_return_number") | e }}</span>{% else %}'
		+ _DASH
		+ "{% endif %}",
		width="30%",
	)
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ ps.fact("OUR PURCHASE ORDER", _ORDERS, width="40%")
	+ ps.fact("RECEIVED ON", _RECEIVED_ON, width="30%")
	+ ps.fact("QUESTIONS TO", buying._OWNER, width="30%")
	+ ps.facts_close()
)

# Under the item's name: why it is coming back, and the state it is in. Escaped: both are typed.
_DESCRIPTION = (
	'        <td style="' + ps.TD + '">\n'
	'          {{ (row.get("item_name") or "") | e }}\n'
	'          {%- if row.get("reason") %}<div style="font-size:11px; color:' + ps.INK_700 + ';">'
	'{{ row.get("reason") | e }}'
	'{%- if row.get("condition_notes") %}: {{ row.get("condition_notes") | e }}{% endif %}</div>{% endif %}\n'
	"        </td>\n"
)

_ITEMS = (
	'<table style="width:100%; border-collapse:collapse; margin-top:4px;">\n'
	'    <thead style="display:table-header-group;">\n'
	"      <tr>\n"
	+ buying._th("Item")
	+ buying._th("Description")
	+ buying._th("Order")
	+ buying._th("Qty", right=True)
	+ buying._th("UOM")
	+ buying._th("Rate", right=True)
	+ buying._th("Amount", right=True)
	+ "      </tr>\n"
	"    </thead>\n"
	"    <tbody>\n"
	"      {%- for row in doc.items %}\n"
	'      <tr style="page-break-inside:avoid;">\n'
	+ buying._ITEM_CELL
	+ _DESCRIPTION
	+ buying._code_td('{{ (row.get("purchase_order") or "") | e }}')
	+ buying._td('{{ ps_qty(row.get("qty")) }}', right=True)
	+ buying._td('{{ ps_uom(row.get("uom")) }}')
	+ buying._td(_money('row.get("rate")'), right=True)
	+ buying._td(_money('row.get("amount")'), right=True)
	+ "      </tr>\n"
	"      {%- endfor %}\n"
	"      {%- if not doc.items %}\n"
	'      <tr><td colspan="7" style="' + ps.TD + '; font-style:italic;">No items on this return.</td></tr>\n'
	"      {%- endif %}\n"
	"    </tbody>\n"
	"  </table>\n"
)


def _total_row(label, expr, condition=None, sign=""):
	row = (
		"    <tr>\n"
		'      <td style="' + ps.TOTAL_SPACER + '"></td>\n'
		'      <td style="' + ps.TOTAL_LABEL + '">' + label + "</td>\n"
		'      <td style="' + ps.TOTAL_VALUE + '">' + sign + _money(expr) + "</td>\n"
		"    </tr>\n"
	)
	if condition:
		return "    {%- if " + condition + " %}\n" + row + "    {%- endif %}\n"
	return row


_TOTALS = (
	'<table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">\n'
	"    <tr>\n"
	'      <td style="width:56%;' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="width:26%;' + ps.TOTAL_LABEL + '">Items total</td>\n'
	'      <td style="width:18%;' + ps.TOTAL_VALUE + '">' + _money('doc.get("total_amount")') + "</td>\n"
	"    </tr>\n"
	+ _total_row("Tax", 'doc.get("tax_amount")', 'doc.get("tax_amount")')
	+ _total_row(
		"Restocking fee", 'doc.get("restocking_fee")', 'doc.get("restocking_fee")', sign="&minus;&nbsp;"
	)
	+ _total_row(
		"Return shipping",
		'doc.get("return_shipping_cost")',
		'doc.get("shipping_deducted") and doc.get("return_shipping_cost")',
		sign="&minus;&nbsp;",
	)
	+ '    {%- if doc.get("requested_resolution") != "Replacement" %}\n'
	"    <tr>\n"
	'      <td style="' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="' + ps.GRAND + '">Expected back</td>\n'
	'      <td style="'
	+ ps.GRAND
	+ ';white-space:nowrap;font-size:14px">'
	+ _money('doc.get("expected_amount")')
	+ "</td>\n"
	"    </tr>\n"
	"    {%- endif %}\n"
	"  </table>\n"
)

_NOTES = (
	'  {%- if doc.get("request_notes") %}\n'
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("Notes")
	+ '    <div style="font-size:12px; white-space:pre-wrap;">{{ doc.get("request_notes") | e }}</div>\n'
	"  </div>\n"
	"  {%- endif %}\n"
)

_CLOSING = (
	'  <div style="margin-top:14px; page-break-inside:avoid;">\n'
	+ ps.facts_open()
	+ ps.fact("GOING BACK BY", _GOING_BACK, width="40%")
	+ ps.fact("SENT BACK ON", buying._date_or_dash('doc.get("sent_back_on")'), width="30%")
	+ ps.fact("RETURN", '<span style="' + ps.STRONG + '">{{ doc.name }}</span>', width="30%")
	+ ps.facts_close()
	+ '    {%- if not doc.get("supplier_return_number") %}\n'
	'    <div style="font-size:12px;">Please reply with a return authorization number and where to send the items.</div>\n'
	"    {%- endif %}\n"
	"  </div>\n"
)

HTML = buying._compose(
	"SUPPLIER RETURN",
	"Return Request",
	buying._meta('doc.get("posting_date")'),
	None,
	_FACTS + _ITEMS + _TOTALS + _NOTES + _CLOSING,
)


def ensure_supplier_return_print_format():
	"""``after_migrate`` entry point. A failure is logged; it never aborts the migrate."""
	try:
		if not frappe.db.exists("DocType", DOCTYPE):
			return
		if frappe.db.exists("Print Format", FORMAT):
			pf = frappe.get_doc("Print Format", FORMAT)
		else:
			pf = frappe.new_doc("Print Format")
			pf.name = FORMAT
		pf.doc_type = DOCTYPE
		pf.module = MODULE
		pf.print_format_type = "Jinja"
		pf.custom_format = 1
		pf.standard = "No"
		pf.disabled = 0
		pf.html = HTML
		pf.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info(f"Supplier Return print format: ensured {FORMAT}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Supplier Return print format")

"""Purchase Order print format, created idempotently on every migrate.

Lives in Enhancements Core because procurement has no module of its own — its code
(`po_approval`, `po_segregation`, `procurement_project`) sits at the app root, and every
print format in this app needs a real Module Def to belong to. Enhancements Core is the
documented catch-all.

Why an `after_migrate` upsert rather than a Print Format fixture: eight of the ten
formats this app ships already work this way, template edits then deploy on the next
migrate with no export step, and `after_migrate` runs *after* fixture sync so it cannot
be silently overridden. The trade-off is that an admin's UI edit is overwritten on the
next deploy — which is the intended direction here, the repo being the source of truth.
Switching to the `hooks.py` fixtures allowlist instead is a small change if preferred.

Design decisions, all confirmed rather than assumed:

- **Approver name and date are printed.** They come from `custom_approved_by` /
  `custom_approved_on`, stamped by `po_approval.stamp_approval` once both submit gates
  pass. Orders submitted before that shipped print an em dash rather than an invented
  name.
- **Project is printed per line.** `Purchase Order Item.project` is mandatory on this
  site (WI-014), and a supplier delivering to a job site rather than the shop needs it.
- **No item images.** They make a multi-page order much heavier for little gain on
  fittings that are identified by part number.
- **The letter head is rendered explicitly**, at the top of the body. A `custom_format`
  template supplies the whole document, so Frappe never injects one — it only builds the
  `#header-html` block for *standard* formats. `letter_head` is handed to the template in
  the render args and dropped if unused, which is how this format spent its first month
  going to suppliers unbranded.
- **Print-safe CSS only**: no flexbox, no grid, `page-break-inside: avoid` on rows, and a
  `thead` that repeats across pages. The PDF engine on this host has been unreliable
  enough (see docs/pdf-generation.md) without asking it to do anything clever.

House style otherwise follows the other eight: inline styles, no `css` field, no classes,
Helvetica/Arial, and the `#222` / `#777` / `#555` / `#333` / `#ccc` / `#eee` / `#f4f5f7`
palette.
"""

import frappe

MODULE = "Enhancements Core"
PURCHASE_ORDER_FORMAT = "Purchase Order - Sapphire"

_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">

  {#- A custom Jinja format has to render the letterhead itself. Frappe injects it only for
      *standard* formats, via the `#header-html` block that `repeat_header_footer` produces;
      a format with `custom_format = 1` supplies the whole body, so `letter_head` is offered
      to the template and simply dropped if nothing asks for it. That is why this order went
      to suppliers unbranded for its first month while every stock format carried the logo.
      Verified on production: the rendered HTML contains no `#header-html` div at all, and
      the PDF was byte-identical with and without a letter head attached to the document.

      Page one only, and deliberately. The identifier a counter clerk needs on every sheet is
      the PO number, which is in the bar below and repeats through the table header. A logo
      on each page would cost ~52 KB per page for no working benefit. -#}
  {%- if letter_head %}
  <div style="margin-bottom:10px;">{{ letter_head }}</div>
  {%- endif %}

  <div style="display:table; width:100%; border-bottom:2px solid #333; padding-bottom:6px; margin-bottom:12px;">
    <div style="display:table-cell; vertical-align:bottom;">
      <h2 style="margin:0; font-size:20px;">Purchase Order</h2>
    </div>
    <div style="display:table-cell; vertical-align:bottom; text-align:right; color:#777;">
      <div style="font-size:14px; color:#222;"><b>{{ doc.name }}</b></div>
      <div>{{ frappe.format(doc.transaction_date, {"fieldtype": "Date"}) }}</div>
    </div>
  </div>

  <table style="width:100%; border-collapse:collapse; margin-bottom:14px;">
    <tr>
      <td style="width:18%; color:#777; padding:2px 0; vertical-align:top;">Supplier</td>
      <td style="width:32%; padding:2px 0; vertical-align:top;">
        <b>{{ doc.supplier_name or doc.supplier }}</b>
        {% if doc.address_display %}<div style="color:#555;">{{ doc.address_display }}</div>{% endif %}
        {% if doc.contact_display %}<div style="color:#555;">Attn: {{ doc.contact_display }}</div>{% endif %}
      </td>
      <td style="width:18%; color:#777; padding:2px 0; vertical-align:top;">Required by</td>
      <td style="width:32%; padding:2px 0; vertical-align:top;">
        {% if doc.schedule_date %}{{ frappe.format(doc.schedule_date, {"fieldtype": "Date"}) }}{% else %}<span style="color:#999;">Not specified</span>{% endif %}
      </td>
    </tr>
    <tr>
      <td style="color:#777; padding:2px 0; vertical-align:top;">Deliver to</td>
      <td style="padding:2px 0; vertical-align:top;">
        {% if doc.shipping_address %}{{ doc.shipping_address }}
        {% elif doc.shipping_address_display %}{{ doc.shipping_address_display }}
        {% else %}<span style="color:#999;">Collection &mdash; see instructions below</span>{% endif %}
      </td>
      <td style="color:#777; padding:2px 0; vertical-align:top;">Order status</td>
      <td style="padding:2px 0; vertical-align:top;">{{ doc.status }}</td>
    </tr>
    <tr>
      <td style="color:#777; padding:2px 0; vertical-align:top;">Approved by</td>
      <td style="padding:2px 0; vertical-align:top;">
        {%- if doc.get("custom_approved_by") -%}
          {{ frappe.db.get_value("User", doc.custom_approved_by, "full_name") or doc.custom_approved_by }}
          {%- if doc.get("custom_approved_on") %}<div style="color:#555;">{{ frappe.format(doc.custom_approved_on, {"fieldtype": "Datetime"}) }}</div>{% endif -%}
        {%- else -%}
          <span style="color:#999;">&mdash;</span>
        {%- endif -%}
      </td>
      <td style="color:#777; padding:2px 0; vertical-align:top;">Questions to</td>
      <td style="padding:2px 0; vertical-align:top;">
        {{ frappe.db.get_value("User", doc.owner, "full_name") or doc.owner }}
        <div style="color:#555;">{{ doc.owner }}</div>
      </td>
    </tr>
  </table>

  <table style="width:100%; border-collapse:collapse; font-size:11px;">
    <thead style="display:table-header-group;">
      <tr style="background:#f4f5f7;">
        <th style="text-align:left;   padding:6px 4px; border-bottom:2px solid #ccc;">Item</th>
        <th style="text-align:left;   padding:6px 4px; border-bottom:2px solid #ccc;">Description</th>
        <th style="text-align:left;   padding:6px 4px; border-bottom:2px solid #ccc;">Project</th>
        <th style="text-align:right;  padding:6px 4px; border-bottom:2px solid #ccc; white-space:nowrap;">Qty</th>
        <th style="text-align:left;   padding:6px 4px; border-bottom:2px solid #ccc;">UOM</th>
        <th style="text-align:right;  padding:6px 4px; border-bottom:2px solid #ccc; white-space:nowrap;">Rate</th>
        <th style="text-align:right;  padding:6px 4px; border-bottom:2px solid #ccc; white-space:nowrap;">Amount</th>
      </tr>
    </thead>
    <tbody>
      {%- for row in doc.items %}
      <tr style="page-break-inside:avoid;">
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top;">{{ row.item_code | e }}</td>
        <!-- Rendered as HTML, NOT escaped. Purchase Order Item.description is a Text
             Editor field, so it holds markup authored by staff in the Item master —
             escaping it printed a literal "&lt;div&gt;&lt;p&gt;Use for waterproofing…"
             at the supplier. Every stock ERPNext print format renders this field the
             same way. item_name is a plain Data field and stays escaped. -->
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; color:#555;">
          {%- if row.description %}{{ row.description }}{% else %}{{ row.item_name | e }}{% endif -%}
        </td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; color:#555;">{{ (row.project or "") | e }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; text-align:right; white-space:nowrap;">{{ row.qty }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top;">{{ (row.uom or "") | e }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; text-align:right; white-space:nowrap;">{{ frappe.utils.fmt_money(row.rate, currency=doc.currency) }}</td>
        <td style="padding:5px 4px; border-bottom:1px solid #eee; vertical-align:top; text-align:right; white-space:nowrap;">{{ frappe.utils.fmt_money(row.amount, currency=doc.currency) }}</td>
      </tr>
      {%- endfor %}
      {%- if not doc.items %}
      <tr><td colspan="7" style="padding:8px 4px; color:#999; font-style:italic;">No items on this order.</td></tr>
      {%- endif %}
    </tbody>
  </table>

  <table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">
    <tr>
      <td style="width:60%;"></td>
      <td style="width:22%; text-align:right; color:#777; padding:2px 4px;">Net total</td>
      <td style="width:18%; text-align:right; padding:2px 4px; white-space:nowrap;">{{ frappe.utils.fmt_money(doc.net_total, currency=doc.currency) }}</td>
    </tr>
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
      <td style="text-align:right; padding:6px 4px; border-top:2px solid #333;"><b>Grand total</b></td>
      <td style="text-align:right; padding:6px 4px; border-top:2px solid #333; white-space:nowrap;">
        <b>{{ frappe.utils.fmt_money(doc.grand_total, currency=doc.currency) }}</b>
      </td>
    </tr>
  </table>

  <div style="margin-top:18px; page-break-inside:avoid;">
    <div style="color:#777; border-bottom:1px solid #eee; padding-bottom:3px; margin-bottom:6px;">Payment terms</div>
    {%- if doc.payment_terms_template or doc.payment_schedule %}
      {%- if doc.payment_terms_template %}<div>{{ doc.payment_terms_template | e }}</div>{% endif %}
      {%- for term in doc.payment_schedule %}
      {#- Parenthesised on purpose: `a or b or "" | e` binds the filter to the empty
          string alone, so the real values went out unescaped. And the separator is
          emitted only when there is a label, rather than leaving a dangling dash. -#}
      {%- set term_label = (term.description or term.payment_term or "") %}
      <div style="color:#555;">
        {%- if term_label %}{{ term_label | e }} &mdash; {% endif %}
        {{- frappe.utils.fmt_money(term.payment_amount, currency=doc.currency) }}
        {%- if term.due_date %} due {{ frappe.format(term.due_date, {"fieldtype": "Date"}) }}{% endif %}
      </div>
      {%- endfor %}
    {%- else %}
      <div style="color:#999; font-style:italic;">As agreed.</div>
    {%- endif %}
  </div>

  <div style="margin-top:14px; page-break-inside:avoid;">
    <div style="color:#777; border-bottom:1px solid #eee; padding-bottom:3px; margin-bottom:6px;">Delivery &amp; receiving</div>
    {%- if doc.terms %}
      <div style="color:#555;">{{ doc.terms }}</div>
    {%- else %}
      <div style="color:#555;">
        Please quote <b>{{ doc.name }}</b> on all packing slips and invoices, and notify the
        buyer above before delivery or collection.
      </div>
    {%- endif %}
  </div>

</div>
"""


def ensure_enhancements_core_print_formats():
	"""`after_migrate` entry point. Failures log rather than abort the migrate."""
	try:
		if not frappe.db.exists("DocType", "Purchase Order"):
			return
		_upsert_print_format(PURCHASE_ORDER_FORMAT, "Purchase Order", _HTML)
		frappe.db.commit()
		frappe.logger().info(f"Enhancements Core print formats: ensured {PURCHASE_ORDER_FORMAT}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Enhancements Core print formats")


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

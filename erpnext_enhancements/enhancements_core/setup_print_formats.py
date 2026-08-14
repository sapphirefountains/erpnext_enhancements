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
- **Our own address and phone are printed beside the letter head**, because the letter
  head does not carry them: `Sapphire Fountains Default` is a bare right-aligned logo and
  nothing else, so before this the only way for a supplier to reach us was the buyer's
  email in the "Questions to" cell. The block is shared with the three sales formats —
  see `company_contact` for why the address prefers document data and the phone cannot.
- **Print-safe CSS only**: no flexbox, no grid, `page-break-inside: avoid` on rows, and a
  `thead` that repeats across pages. The PDF engine on this host has been unreliable
  enough (see docs/pdf-generation.md) without asking it to do anything clever.

House style otherwise follows the other eight: inline styles, no `css` field, no classes,
Helvetica/Arial, and the `#222` / `#777` / `#555` / `#333` / `#ccc` / `#eee` / `#f4f5f7`
palette.
"""

import frappe

from erpnext_enhancements.enhancements_core.company_contact import contact_block

MODULE = "Enhancements Core"
PURCHASE_ORDER_FORMAT = "Purchase Order - Sapphire"

# The company-address field is called `billing_address_display` here. Purchase Order has no
# `company_address` at all — the sales doctypes' name for the same thing — which is why
# `contact_block` takes the fieldname instead of hard-coding one. Get it wrong and the block
# prints nothing and raises nothing: Jinja renders a missing attribute as empty.
ADDRESS_FIELD = "billing_address_display"

_TEMPLATE = """
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
__CONTACT_BLOCK__

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

# Substituted rather than interpolated: the template is full of Jinja braces, so an
# f-string is not an option and `%`/`.format` would collide with them too.
_HTML = _TEMPLATE.replace("__CONTACT_BLOCK__", contact_block(ADDRESS_FIELD))


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


# Formats deliberately left on wkhtmltopdf. Empty since v1.259.1, and deliberately
# kept rather than deleted.
#
# Its only member was `Test Purchase Order Format`, an abandoned builder experiment
# that tripped a real bug in frappe's chrome path: `pdf_generator/pdf_merge.py`
# merges one header page onto each body page and indexes `header.pages[i]` without
# checking length, so a body longer than the header render raises `IndexError:
# Sequence index out of range`. It reproduced on nothing else here -- every real
# document, up to a 128-line Sales Invoice, rendered fine. The note above this line
# used to end "the guard stays until either the format is deleted or upstream bounds
# that index", and TASK-2026-01237 deleted the format
# (`patches/purge_purchase_order_print_formats.py`).
#
# The upstream bug is still there. Anyone who builds a format with a header shorter
# than its body will meet it again, and this is where the name goes.
CHROME_EXCLUDED_FORMATS: set[str] = set()

# Purchase Order formats superseded by `Purchase Order - Sapphire`, disabled on every
# migrate rather than deleted once.
#
# These three ship with ERPNext (`standard = "Yes"`), and standard formats **re-sync
# from app JSON on migrate** -- the same reason `ensure_chrome_pdf_generator` below
# exists as code rather than as the one-off data fix somebody tried first. A patch
# that deleted or disabled them would come undone at the next `bench migrate`, and
# would look like it had worked until somebody printed a PO weeks later.
#
# The two *custom* PO formats are a different matter and are genuinely deleted, once,
# by the patch: nothing recreates them.
SUPERSEDED_PURCHASE_ORDER_FORMATS = (
	"Purchase Order Standard",
	"Purchase Order with Item Image",
	"Drop Shipping Format",
)


def disable_superseded_print_formats():
	"""Keep the superseded standard PO formats out of the print dropdown.

	Disabled, not deleted: they belong to ERPNext, and deleting a standard format
	means it returns on the next migrate. `disabled = 1` is a field on the record,
	so re-applying it after each sync is what actually holds.

	Uses `frappe.db.set_value` for the same reason as the chrome pass --
	`Print Format.validate` throws "Standard Print Format cannot be updated", so the
	ORM cannot touch these at all.
	"""
	try:
		if not frappe.db.has_column("Print Format", "disabled"):
			return
		disabled = 0
		for name in SUPERSEDED_PURCHASE_ORDER_FORMATS:
			if not frappe.db.exists("Print Format", name):
				continue
			if frappe.db.get_value("Print Format", name, "disabled"):
				continue
			frappe.db.set_value("Print Format", name, "disabled", 1, update_modified=False)
			disabled += 1
		if disabled:
			frappe.db.commit()
			frappe.logger().info(f"Purchase Order print formats: disabled {disabled} superseded")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Superseded print format cleanup")


def ensure_chrome_pdf_generator():
	"""Point every Print Format at the chrome PDF backend, on every migrate.

	Why this is code rather than a one-off data fix, having tried the data fix first:

	- **Standard formats refuse ORM writes.** `Print Format.validate` throws "Standard Print
	  Format cannot be updated", so `doc.save()` cannot touch `Sales Invoice Standard` and
	  the fourteen others like it. `frappe.db.set_value` bypasses the controller, which is
	  what frappe's own `sets_wkhtmltopdf_as_default_for_pdf_generator_field` patch does.
	- **And standard formats re-sync from app JSON on migrate**, so even a successful direct
	  write is not durable. Re-applying after every migrate is the only thing that sticks.

	Note frappe reads this field and *not* `Print Settings.pdf_generator`:
	`print_utils.get_print` resolves `form_dict.pdf_generator` -> explicit argument ->
	`Print Format.pdf_generator` **or the literal string "wkhtmltopdf"**. An empty field is
	therefore not neutral, it means wkhtmltopdf -- which is why blanks are set too rather
	than left alone.

	Chrome was verified against every format on this site that has a document to render:
	sixteen of seventeen produce a valid PDF, including a 128-line Sales Invoice at 7 pages.
	Chrome output is 2-4x larger than wkhtmltopdf for the same document, which matters most
	for emailed attachments.
	"""
	try:
		if not frappe.db.has_column("Print Format", "pdf_generator"):
			return
		names = frappe.get_all(
			"Print Format",
			filters={"disabled": 0},
			pluck="name",
		)
		for name in names:
			if name in CHROME_EXCLUDED_FORMATS:
				continue
			if frappe.db.get_value("Print Format", name, "pdf_generator") == "chrome":
				continue
			# Deliberately the low-level write: see the docstring on standard formats.
			frappe.db.set_value("Print Format", name, "pdf_generator", "chrome", update_modified=False)
		frappe.db.commit()
		frappe.logger().info(f"PDF generator: {len(names)} print formats pointed at chrome")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Chrome PDF generator setup")


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

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
- **The letterhead is drawn by the template, not by Frappe.** A `custom_format`
  template supplies the whole document, so Frappe never injects one — it only builds the
  `#header-html` block for *standard* formats. `letter_head` is handed to the template in
  the render args and dropped if unused, which is how this format spent its first month
  going to suppliers unbranded. Since v1.495.0 `print_style.letterhead()` inlines the
  wordmark itself and draws our name, address and phone beside it — the site's letter
  head is a bare right-aligned logo and is no longer rendered here, since two logos on
  one page is worse than one. The address prefers the document's own
  `billing_address_display` and falls back to the constant in `company_contact`; the phone
  *is* the constant. That module explains why.
- **Print-safe CSS only**: no flexbox, no grid, `page-break-inside: avoid` on rows, and a
  `thead` that repeats across pages. The PDF engine on this host has been unreliable
  enough (see docs/pdf-generation.md) without asking it to do anything clever.
- **The neutral stripe.** An order is raised for a job, and jobs span all four pillars,
  so the format takes the brand's dark band rather than guessing — the same call the
  sales formats make.

The chrome — stripe, wordmark, display-face title, ruled tables, running line — is
`print_style`'s (docs/print-design-system.md); this module composes only the order.
"""

import frappe

from erpnext_enhancements import print_style as ps

MODULE = "Enhancements Core"
PURCHASE_ORDER_FORMAT = "Purchase Order - Sapphire"

# The company-address field is called `billing_address_display` here. Purchase Order has no
# `company_address` at all — the sales doctypes' name for the same thing — which is why
# `print_style.letterhead()` takes the fieldname instead of hard-coding one. Get it wrong and
# the block prints nothing and raises nothing: Jinja renders a missing attribute as empty.
ADDRESS_FIELD = "billing_address_display"

_META = (
	"""
      {#- The identifier carries the job (ER-2026-256847): the person filing these cannot
          tell one PO from another without it. `PO-2026-00262-PRJ-00706`, and NOT
          `doc.name` on one line with the project under it — this is character-for-character
          the PDF's filename, because it is the same function call. Somebody comparing the
          sheet on their desk to the file it came from is who this is for, and two
          renderings of one idea drift: the id bounds a long tail of jobs with `plus-N` and
          a template loop here would not have.

          Via the jinja hook rather than worked out inline for the same reason the id itself
          exists: `doc.project` and `Purchase Order Item.project` can disagree, every report
          here matches on the union, and a template deciding for itself would be a third
          answer to a question this app already answers once. -#}
      <span style=\""""
	+ ps.STRONG
	+ """\">{{ purchase_order_document_id(doc) | e }}</span>
      {#- The readable name under it, because nobody files by PRJ-00706. Dropped entirely
          when the project has no name of its own, rather than printing the number twice.

          Unbounded where the identifier above is capped, deliberately: the id is a *name*
          and has to stay a usable length, while this line is the human aid and truncating
          it would hide a job the order really is for. The `plus-N` on the id is already
          the signal that it is not the whole list. -#}
      {%- set po_projects = purchase_order_projects(doc) %}
      {%- if po_projects %}
      {%- set project_names = [] %}
      {%- for project in po_projects %}
      {%- set project_name = frappe.db.get_value("Project", project, "project_name") %}
      {%- if project_name and project_name != project %}{% set _ = project_names.append(project_name) %}{% endif %}
      {%- endfor %}
      {%- if project_names %}
      <br>{{ project_names | join(", ") | e }}
      {%- endif %}
      {%- endif %}
      <br>{{ frappe.format(doc.transaction_date, {"fieldtype": "Date"}) }}
"""
)

_DASH = '<span style="color:' + ps.INK_700 + ';">&mdash;</span>'

# What the order's state means to the supplier holding it. ERPNext's status word is ours, not
# theirs: "Closed" (104 of 230 orders on production) reads to a supplier as cancelled, and "To
# Bill" (73) as a prompt to send an invoice. The one thing a supplier needs is whether this is
# an order to act on, so a submitted order says "Issued" and the three states that mean "do not
# act on this" say so, in red.
_ORDER_STATUS = (
	"{%- if not doc.docstatus %}"
	'<span style="' + ps.FAIL + '">Draft &mdash; not an order until approved</span>'
	"{%- elif doc.docstatus == 2 %}"
	'<span style="' + ps.FAIL + '">Cancelled &mdash; do not supply</span>'
	'{%- elif doc.status == "On Hold" %}'
	'<span style="' + ps.FAIL + '">On hold &mdash; please do not ship yet</span>'
	"{%- else %}Issued{% endif -%}"
)

_FACTS = (
	ps.facts_open()
	# Name, address, Attn, phone and email, found by `print_lookup.ps_party`: this site keeps
	# them on the Contact, the Address and the Supplier rather than on the order (address on 17
	# of 230 orders, contact phone and email on none).
	+ ps.fact("SUPPLIER", "{{ ps_party(doc) }}", width="40%")
	+ ps.fact(
		"REQUIRED BY",
		'{% if doc.schedule_date %}{{ frappe.format(doc.schedule_date, {"fieldtype": "Date"}) }}'
		"{% else %}Not specified{% endif %}",
		width="30%",
	)
	+ ps.fact(
		"DELIVER TO",
		# The rendered address, never `doc.shipping_address`: that is the Link to the Address
		# record, and printing it put the record's NAME ("Sapphire Fountain-Billing") in front
		# of the supplier on 221 of 230 orders -- every order that had one.
		"{% if doc.shipping_address_display %}{{ ps_address(doc.shipping_address_display) }}"
		"{% else %}Collection &mdash; see instructions below{% endif %}",
		width="30%",
	)
	+ ps.facts_close()
	+ ps.facts_open(top=False)
	+ ps.fact("ORDER STATUS", _ORDER_STATUS, width="40%")
	+ ps.fact(
		"APPROVED BY",
		'{%- if doc.get("custom_approved_by") -%}'
		'{{ frappe.db.get_value("User", doc.custom_approved_by, "full_name") or doc.custom_approved_by }}'
		'{%- if doc.get("custom_approved_on") %}<br>{{ frappe.format(doc.custom_approved_on, {"fieldtype": "Datetime"}) }}{% endif -%}'
		"{%- else -%}" + _DASH + "{%- endif -%}",
		width="30%",
	)
	+ ps.fact(
		"QUESTIONS TO",
		'{{ frappe.db.get_value("User", doc.owner, "full_name") or doc.owner }}<br>{{ doc.owner }}',
		width="30%",
	)
	+ ps.facts_close()
)

# The line table. The item code stays -- receiving and the supplier's counter staff work from
# it -- and never wraps, because a code broken over two lines reads as two codes. Nor does
# the project number: at its hyphen "PRJ-00706" broke into "PRJ-" over "00706".
#
# The description goes through `ps_rich`, NOT `| e`. Purchase Order Item.description is a Text
# Editor field, so a line authored in the Item master holds markup: escaping it printed a
# literal "&lt;div&gt;&lt;p&gt;Use for waterproofing…" at the supplier. But most lines here
# were imported and are plain text with newlines, which printed as HTML ran into a single
# paragraph. `ps_rich` passes markup through and escapes plain text, keeping its line breaks.
# item_name is a plain Data field and stays escaped. Quantities through `ps_qty` ("12", not
# the raw float "12.0"), units through `ps_uom` (ERPNext's "Nos" is "ea").
_ITEMS = (
	'<table style="width:100%; border-collapse:collapse; margin-top:4px;">\n'
	'    <thead style="display:table-header-group;">\n'
	"      <tr>\n"
	'        <th style="' + ps.th() + '">Item</th>\n'
	'        <th style="' + ps.th() + '">Description</th>\n'
	'        <th style="' + ps.th() + '">Project</th>\n'
	'        <th style="' + ps.th(right=True) + '; white-space:nowrap;">Qty</th>\n'
	'        <th style="' + ps.th() + '">UOM</th>\n'
	'        <th style="' + ps.th(right=True) + '; white-space:nowrap;">Rate</th>\n'
	'        <th style="' + ps.th(right=True) + '; white-space:nowrap;">Amount</th>\n'
	"      </tr>\n"
	"    </thead>\n"
	"    <tbody>\n"
	"      {%- for row in doc.items %}\n"
	'      <tr style="page-break-inside:avoid;">\n'
	'        <td style="' + ps.TD + "; " + ps.STRONG + '; white-space:nowrap;">{{ row.item_code | e }}</td>\n'
	'        <td style="' + ps.TD + '">\n'
	"          {%- if row.description %}{{ ps_rich(row.description) }}{% else %}{{ row.item_name | e }}{% endif -%}\n"
	"        </td>\n"
	'        <td style="' + ps.TD + '; white-space:nowrap;">{{ (row.project or "") | e }}</td>\n'
	'        <td style="' + ps.TD_RIGHT + '">{{ ps_qty(row.qty) }}</td>\n'
	'        <td style="' + ps.TD + '">{{ ps_uom(row.uom) }}</td>\n'
	'        <td style="'
	+ ps.TD_RIGHT
	+ '">{{ frappe.utils.fmt_money(row.rate, currency=doc.currency) }}</td>\n'
	'        <td style="'
	+ ps.TD_RIGHT
	+ '">{{ frappe.utils.fmt_money(row.amount, currency=doc.currency) }}</td>\n'
	"      </tr>\n"
	"      {%- endfor %}\n"
	"      {%- if not doc.items %}\n"
	'      <tr><td colspan="7" style="' + ps.TD + '; font-style:italic;">No items on this order.</td></tr>\n'
	"      {%- endif %}\n"
	"    </tbody>\n"
	"  </table>\n"
)

# The totals block, in `print_style`'s shared totals styles (TOTAL_SPACER / TOTAL_LABEL /
# TOTAL_VALUE / GRAND) -- the same ones every priced format uses, and the ones whose padding
# carries the inline `!important` that frappe's print stylesheets otherwise override.
_TOTALS = (
	'<table style="width:100%; border-collapse:collapse; margin-top:10px; page-break-inside:avoid;">\n'
	"    <tr>\n"
	'      <td style="width:56%;' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="width:26%;' + ps.TOTAL_LABEL + '">Net total</td>\n'
	'      <td style="width:18%;'
	+ ps.TOTAL_VALUE
	+ '">{{ frappe.utils.fmt_money(doc.net_total, currency=doc.currency) }}</td>\n'
	"    </tr>\n"
	"    {%- for tax in doc.taxes %}\n"
	"    {%- if tax.tax_amount %}\n"
	"    <tr>\n"
	'      <td style="' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="' + ps.TOTAL_LABEL + '">{{ tax.description | e }}</td>\n'
	'      <td style="'
	+ ps.TOTAL_VALUE
	+ '">{{ frappe.utils.fmt_money(tax.tax_amount, currency=doc.currency) }}</td>\n'
	"    </tr>\n"
	"    {%- endif %}\n"
	"    {%- endfor %}\n"
	"    <tr>\n"
	'      <td style="' + ps.TOTAL_SPACER + '"></td>\n'
	'      <td style="' + ps.GRAND + '">Grand total</td>\n'
	'      <td style="' + ps.GRAND + ';white-space:nowrap;font-size:14px">'
	"{{ frappe.utils.fmt_money(doc.grand_total, currency=doc.currency) }}</td>\n"
	"    </tr>\n"
	"  </table>\n"
)

_TERMS = (
	'  <div style="page-break-inside:avoid;">\n'
	+ ps.section_title("Payment terms")
	+ """    {%- if doc.payment_terms_template or doc.payment_schedule %}
      {%- if doc.payment_terms_template %}<div>{{ doc.payment_terms_template | e }}</div>{% endif %}
      {%- for term in doc.payment_schedule %}
      {#- Parenthesised on purpose: `a or b or "" | e` binds the filter to the empty
          string alone, so the real values went out unescaped. And the separator is
          emitted only when there is a label, rather than leaving a dangling dash.

          Nor when the label only repeats the template's name: a "Net 30" template's one
          schedule row is called "Net 30" too, and the order said "Net 30" twice. -#}
      {%- set term_label = (term.description or term.payment_term or "") %}
      <div>
        {%- if term_label and term_label != doc.payment_terms_template %}{{ term_label | e }} &mdash; {% endif %}
        {{- frappe.utils.fmt_money(term.payment_amount, currency=doc.currency) }}
        {%- if term.due_date %} due {{ frappe.format(term.due_date, {"fieldtype": "Date"}) }}{% endif %}
      </div>
      {%- endfor %}
    {%- else %}
      <div style="font-style:italic;">As agreed.</div>
    {%- endif %}
  </div>
  <div style="page-break-inside:avoid;">
"""
	+ ps.section_title("Delivery &amp; receiving")
	+ """    {%- if doc.terms %}
      <div>{{ doc.terms }}</div>
    {%- else %}
      <div>
        Please quote <b>{{ doc.name }}</b> on all packing slips and invoices, and notify the
        buyer above before delivery or collection.
      </div>
    {%- endif %}
  </div>
"""
)

_TEMPLATE = (
	ps.page_open(None)
	+ """
  {#- A custom Jinja format has to render the letterhead itself. Frappe injects it only for
      *standard* formats, via the `#header-html` block that `repeat_header_footer` produces;
      a format with `custom_format = 1` supplies the whole body, so `letter_head` is offered
      to the template and simply dropped if nothing asks for it. That is why this order went
      to suppliers unbranded for its first month while every stock format carried the logo.
      Verified on production: the rendered HTML contains no `#header-html` div at all, and
      the PDF was byte-identical with and without a letter head attached to the document.

      Since v1.495.0 print_style.letterhead() draws the wordmark itself, so `letter_head`
      is deliberately NOT rendered here: the site's letter head is a bare right-aligned
      logo, and two logos on one page is worse than one.

      Page one only, and deliberately. The identifier a counter clerk needs on every sheet is
      the PO number, which is in the meta block and repeats through the table header. A logo
      on each page would cost ~52 KB per page for no working benefit. -#}
"""
	+ ps.letterhead(None, "PURCHASE ORDER", "Purchase Order", _META, ADDRESS_FIELD)
	+ _FACTS
	+ _ITEMS
	+ _TOTALS
	+ _TERMS
	+ ps.page_close(None)
)

# The composed template. Concatenated rather than interpolated: the markup is full of Jinja
# braces, so f-strings and `%`/`.format` are both unavailable.
_HTML = _TEMPLATE


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
# These three ship with ERPNext (`standard = "Yes"`), so they are disabled, never deleted:
# a deleted standard format has no row, and the next `bench migrate` imports it again from
# erpnext's JSON. A *disable*, on the other hand, survives that import. Frappe v16 lists
# `"Print Format": ["disabled"]` in `ignore_values` (frappe/modules/import_file.py, lines
# 28-30), and `delete_old_doc` copies those fields from the old row onto the re-imported one
# (lines 261-264) -- and a format re-imports only when erpnext ships a newer JSON anyway
# (lines 140-142). This comment used to say the opposite, that a disable would come undone
# at the next migrate; it would not. Re-applying it every migrate is still the right shape,
# for two real reasons: it is idempotent (an already-disabled format is skipped), and it
# re-disables anything an admin has switched back on since.
#
# The two *custom* PO formats are a different matter and are genuinely deleted, once,
# by the patch: nothing recreates them.
SUPERSEDED_PURCHASE_ORDER_FORMATS = (
	"Purchase Order Standard",
	"Purchase Order with Item Image",
	"Drop Shipping Format",
)

# The same, for the other five procurement documents, superseded by the "<Doctype> - Sapphire"
# formats in `setup_procurement_print_formats` (v1.519.0). Measured on production on
# 2026-09-23: nothing references any of them — no Notification, Auto Repeat or Property
# Setter. Two notes:
#
# - `Request for Quotation Print Template` is NOT an ERPNext file (standard = "No"; it is in
#   no version-16 JSON) — somebody made it on the site. Disabled rather than deleted all the
#   same: a disable is one tick to undo, a delete is not, and re-applying it is harmless.
# - `Purchase Receipt Serial and Batch Bundle Print` does a job the Sapphire receipt does not
#   — it prints serial and batch numbers. No receipt on this site has ever carried one (0
#   Serial and Batch Bundles). If that changes, take it off this list.
#
# `Purchase eInvoice` is a Regional format that is already disabled; listed so it stays so.
# Material Request and Supplier Quotation had nothing but `Standard`, which is not a record
# and cannot be disabled.
SUPERSEDED_PROCUREMENT_FORMATS = (
	"Request for Quotation Print Template",
	"Request for Quotation with Item Image",
	"Purchase Receipt Serial and Batch Bundle Print",
	"Purchase Invoice Standard",
	"Purchase Invoice with Item Image",
	"Purchase Auditing Voucher",
	"Purchase eInvoice",
)

# The Sales Invoice formats, and two strays beside them, superseded by the "<Doctype> -
# Sapphire" sales formats (v1.533.0). Nik: "clean up all the print formats for Sales Invoice
# and make the default of the print formats these new ones" -- the defaults are the Quotation,
# Sales Order and Sales Invoice `default_print_format` Property Setter fixtures. Checked on
# production on 2026-09-24: no Notification, Auto Repeat, POS Profile, Process Statement Of
# Accounts, Payment Request, Dunning, Web Form, script or default references any of these,
# and no sales document has ever been emailed from ERPNext. By group:
#
# - **Broken today.** `Sales Invoice Print` was removed from ERPNext in v16.19 and its .html
#   went with it; `Sales Invoice PD Format v2` and `Sales Order PD v2` belong to the Print
#   Designer app, which is not installed. All three raise TemplateNotFoundError, so choosing
#   one prints a blank preview.
# - **`Point of Sale`** is a leftover JS format ERPNext removed in v16.10 (JS formats are
#   already hidden from the dropdown), and production has 0 POS Profiles and 0 POS invoices.
# - **Superseded** by `Sales Invoice - Sapphire`, which prints a return as a Credit Note
#   itself: `Sales Invoice Standard`, `Sales Invoice with Item Image`, `Sales Invoice Return`
#   and `Sales Auditing Voucher`.
# - **Regional** -- `Detailed Tax Invoice`, `Simplified Tax Invoice`, `Tax Invoice` -- are
#   already disabled; listed so they stay so, like `Purchase eInvoice` above.
#
# Deliberately NOT here: `Quotation Standard`, `Quotation with Item Image`, `Sales Order
# Standard` and `Sales Order with Item Image`. Nobody asked for those to go and they still
# render; their doctypes default to the Sapphire formats, which is enough.
SUPERSEDED_SALES_FORMATS = (
	"Point of Sale",
	"Sales Auditing Voucher",
	"Sales Invoice PD Format v2",
	"Sales Invoice Print",
	"Sales Invoice Return",
	"Sales Invoice Standard",
	"Sales Invoice with Item Image",
	"Detailed Tax Invoice",
	"Simplified Tax Invoice",
	"Tax Invoice",
	"Sales Order PD v2",
)


def disable_superseded_print_formats():
	"""Keep the superseded procurement and sales formats out of the print dropdown.

	Disabled, not deleted, and re-applied every migrate: see the comment above
	SUPERSEDED_PURCHASE_ORDER_FORMATS for why. `frappe.db.set_value` because
	`Print Format.validate` refuses ORM writes to a standard format.
	"""
	try:
		if not frappe.db.has_column("Print Format", "disabled"):
			return
		disabled = 0
		for name in (
			SUPERSEDED_PURCHASE_ORDER_FORMATS + SUPERSEDED_PROCUREMENT_FORMATS + SUPERSEDED_SALES_FORMATS
		):
			if not frappe.db.exists("Print Format", name):
				continue
			if frappe.db.get_value("Print Format", name, "disabled"):
				continue
			frappe.db.set_value("Print Format", name, "disabled", 1, update_modified=False)
			disabled += 1
		if disabled:
			frappe.db.commit()
			frappe.logger().info(f"Print formats: disabled {disabled} superseded")
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

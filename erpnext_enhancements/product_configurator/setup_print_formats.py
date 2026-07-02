"""after_migrate setup for the Product Configuration print formats.

Three sheets per configuration, all reading the child rows the controller
persisted on save (no engine calls, no conditional logic in Jinja — steps are
pre-filtered/pre-rendered server-side):

* **Build Instructions** — shop-floor sheet: config decode table + numbered
  build steps grouped by section. No pricing.
* **QC Checklist** — checkbox lines + assembled-by / QC-by sign-off block.
* **Pricing Summary** — quote sheet: module pricing breakdown + parts list.
  No build steps.

Created idempotently on every migrate (Frappe Cloud has no ``bench`` shell)
and guarded so a hiccup only logs; re-upserting the HTML means template edits
deploy on the next migrate.
"""

import frappe

DOCTYPE = "Product Configuration"
MODULE = "Product Configurator"

BUILD_PF = "Product Configuration - Build Instructions"
QC_PF = "Product Configuration - QC Checklist"
PRICING_PF = "Product Configuration - Pricing Summary"

_HEADER = """
  <div style="display:flex; justify-content:space-between; align-items:baseline; border-bottom:2px solid #333; padding-bottom:6px;">
    <h2 style="margin:0;">{{ title }} &mdash; {{ doc.part_number }}</h2>
    <span style="color:#777;">{{ doc.name }} &middot; {{ frappe.utils.formatdate(doc.modified) }}</span>
  </div>

  <table style="width:100%; border-collapse:collapse; margin:10px 0 14px; font-size:12px;">
    <tr>
      <td style="padding:3px 8px; color:#777; width:18%;">Product</td>
      <td style="padding:3px 8px; font-weight:bold; width:32%;">{{ doc.product }}</td>
      <td style="padding:3px 8px; color:#777; width:18%;">Customer</td>
      <td style="padding:3px 8px; font-weight:bold; width:32%;">{{ doc.customer or "&mdash;" }}</td>
    </tr>
    <tr>
      <td style="padding:3px 8px; color:#777;">Title</td>
      <td style="padding:3px 8px; font-weight:bold;">{{ doc.config_title or "" }}</td>
      <td style="padding:3px 8px; color:#777;">Project</td>
      <td style="padding:3px 8px; font-weight:bold;">{{ doc.project or "&mdash;" }}</td>
    </tr>
  </table>
"""

_DECODE_TABLE = """
  <table style="width:100%; border-collapse:collapse; margin:0 0 14px; font-size:12px;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:5px 8px; border-bottom:2px solid #ccc;">Option</th>
      <th style="text-align:left; padding:5px 8px; border-bottom:2px solid #ccc;">Selection</th>
      <th style="text-align:right; padding:5px 8px; border-bottom:2px solid #ccc;">Code / Qty</th>
    </tr></thead>
    <tbody>
      {% for row in doc.options %}
      {% if row.option_type != "Choice" or row.selected %}
      <tr>
        <td style="padding:4px 8px; border-bottom:1px solid #eee;">{{ row.option_label }}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #eee;">{{ row.choice_label if row.option_type == "Choice" else "Quantity" }}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #eee; text-align:right;">{{ row.choice_code if row.option_type == "Choice" else row.qty }}</td>
      </tr>
      {% endif %}
      {% endfor %}
    </tbody>
  </table>

  {% if doc.warnings_text %}
  <div style="margin:0 0 14px; border:1px solid #fedf89; background:#fffaeb; border-radius:4px; padding:6px 10px;">
    <b style="color:#b54708;">&#9888;</b> {{ doc.warnings_text | replace("\\n", " &middot; ") }}
  </div>
  {% endif %}
"""

BUILD_HTML = (
	"""
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
"""
	+ _HEADER.replace("{{ title }}", "Build Instructions")
	+ _DECODE_TABLE
	+ """
  {# sections render in template order (rows are persisted pre-sorted); Jinja's
     groupby would re-sort them alphabetically, so group by loop.changed #}
  {% set build_steps = doc.build_steps | selectattr("step_type", "equalto", "Build") | list %}
  {% for s in build_steps %}
    {% if loop.changed(s.section_title) %}
      {% if not loop.first %}</ol>{% endif %}
      <h3 style="margin:14px 0 4px; border-bottom:1px solid #ccc; padding-bottom:2px;">{{ s.section_title }}</h3>
      <ol style="margin:4px 0 10px 18px; padding:0;">
    {% endif %}
    <li style="padding:2px 0;">{{ s.instruction }}</li>
    {% if loop.last %}</ol>{% endif %}
  {% endfor %}
  {% if not build_steps %}
  <div style="color:#999; font-style:italic;">No build steps &mdash; the product definition has no step templates.</div>
  {% endif %}
</div>
""".strip()
)

QC_HTML = (
	"""
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
"""
	+ _HEADER.replace("{{ title }}", "QC Checklist")
	+ """
  {% set qc_steps = doc.build_steps | selectattr("step_type", "equalto", "QC") | list %}
  {% for s in qc_steps %}
    {% if loop.changed(s.section_title) %}
      <h3 style="margin:14px 0 4px; border-bottom:1px solid #ccc; padding-bottom:2px;">{{ s.section_title }}</h3>
    {% endif %}
    <div style="padding:4px 0 4px 4px;">&#9744;&nbsp; {{ s.instruction }}</div>
  {% endfor %}
  {% if not qc_steps %}
  <div style="color:#999; font-style:italic;">No QC steps defined for this configuration.</div>
  {% endif %}

  <table style="width:100%; border-collapse:collapse; margin-top:28px; font-size:12px;">
    <tr>
      <td style="padding:6px 8px; width:50%;">Assembled by: ______________________&nbsp;&nbsp;Date: ____________</td>
      <td style="padding:6px 8px; width:50%;">QC by: ______________________&nbsp;&nbsp;Date: ____________</td>
    </tr>
  </table>
</div>
""".strip()
)

PRICING_HTML = (
	"""
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
"""
	+ _HEADER.replace("{{ title }}", "Pricing Summary")
	+ _DECODE_TABLE
	+ """
  <h3 style="margin:14px 0 4px;">Pricing Breakdown</h3>
  <table style="width:100%; border-collapse:collapse; font-size:12px;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:5px 8px; border-bottom:2px solid #ccc;">Module</th>
      <th style="text-align:right; padding:5px 8px; border-bottom:2px solid #ccc;">Qty</th>
      <th style="text-align:right; padding:5px 8px; border-bottom:2px solid #ccc;">Price / Unit</th>
      <th style="text-align:right; padding:5px 8px; border-bottom:2px solid #ccc;">Line Price</th>
    </tr></thead>
    <tbody>
      {% for ln in doc.price_lines %}
      <tr>
        <td style="padding:4px 8px; border-bottom:1px solid #eee;">{{ ln.module_label }}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #eee; text-align:right;">{{ ln.qty }}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #eee; text-align:right;">{{ frappe.utils.fmt_money(ln.unit_price) }}</td>
        <td style="padding:4px 8px; border-bottom:1px solid #eee; text-align:right;">{{ frappe.utils.fmt_money(ln.line_price) }}</td>
      </tr>
      {% endfor %}
    </tbody>
    <tfoot>
      <tr>
        <th colspan="3" style="text-align:right; padding:6px 8px; border-top:2px solid #333;">Selling Price</th>
        <th style="text-align:right; padding:6px 8px; border-top:2px solid #333;">{{ frappe.utils.fmt_money(doc.sell_price) }}</th>
      </tr>
    </tfoot>
  </table>

  <h3 style="margin:16px 0 4px;">Parts List</h3>
  <table style="width:100%; border-collapse:collapse; font-size:11px;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:5px 8px; border-bottom:2px solid #ccc;">Item</th>
      <th style="text-align:left; padding:5px 8px; border-bottom:2px solid #ccc;">Component</th>
      <th style="text-align:right; padding:5px 8px; border-bottom:2px solid #ccc;">Qty</th>
      <th style="text-align:left; padding:5px 8px; border-bottom:2px solid #ccc;">Supplier</th>
    </tr></thead>
    <tbody>
      {% for p in doc.parts %}
      <tr>
        <td style="padding:3px 8px; border-bottom:1px solid #eee;">{{ p.item_code }}</td>
        <td style="padding:3px 8px; border-bottom:1px solid #eee;">{{ p.component_name }}</td>
        <td style="padding:3px 8px; border-bottom:1px solid #eee; text-align:right;">{{ p.qty }}</td>
        <td style="padding:3px 8px; border-bottom:1px solid #eee;">{{ p.supplier_name or "" }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
""".strip()
)


def _upsert_print_format(name, html, doc_type=DOCTYPE):
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


def ensure_configurator_print_formats():
	"""after_migrate entry: ship the three Product Configuration print formats.
	Idempotent (upserts the HTML) and guarded (a failure only logs)."""
	try:
		if frappe.db.exists("DocType", DOCTYPE):
			_upsert_print_format(BUILD_PF, BUILD_HTML)
			_upsert_print_format(QC_PF, QC_HTML)
			_upsert_print_format(PRICING_PF, PRICING_HTML)
			frappe.db.commit()
			frappe.logger().info("[product_configurator] ensured print formats")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Product Configurator print formats")

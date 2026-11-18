# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""after_migrate: the serial label and the test certificate (TASK-2026-03230).

* **Product Unit Label** (Serial No): the serial number and the part number, large enough to read
  across the Build Room, on a 4 x 2 inch label. Printed from the serial number or from the Build
  Checklist page once the unit is built.
* **Pre-Shipment Test Certificate** (Quality Inspection): what goes in the box. The product, part
  number and serial number; who built it and who tested it; every check with its criterion, reading
  and result; the result in words; and the warranty (one year parts and labor at the shop, from the
  delivery date). A test that is not submitted prints "DRAFT - NOT SIGNED" across it, and a
  rejected one "FAILED", so neither can pass for a certificate.

Created the way the configurator ships its sheets (``product_configurator/setup_print_formats.py``):
upserted on every migrate so template edits deploy, and guarded so a failure only logs. Plain
inline styles in the body font, nothing that depends on a ``<style>`` block surviving the PDF
renderer.
"""

import frappe

MODULE = "Product Fulfillment"

LABEL_PF = "Product Unit Label"
CERTIFICATE_PF = "Pre-Shipment Test Certificate"

LABEL_HTML = """
<div style="width:3.8in; height:1.8in; box-sizing:border-box; border:2px solid #00263e; border-radius:6px;
            padding:0.12in 0.16in; font-family:Arial, Helvetica, sans-serif; color:#00263e;">
  <div style="font-size:9pt; letter-spacing:1px; text-transform:uppercase;">Sapphire Fountains</div>
  <div style="font-size:10pt; margin-top:2px;">{{ (doc.item_name or doc.item_code) | e }}</div>
  <div style="font-size:8pt; margin-top:8px; text-transform:uppercase; letter-spacing:1px;">Serial number</div>
  <div style="font-family:'Courier New', monospace; font-size:22pt; font-weight:bold; line-height:1.1;">{{ doc.name | e }}</div>
  <div style="font-size:8pt; margin-top:6px; text-transform:uppercase; letter-spacing:1px;">Part number</div>
  <div style="font-family:'Courier New', monospace; font-size:13pt; font-weight:bold;">{{ doc.item_code | e }}</div>
</div>
""".strip()

CERTIFICATE_HTML = """
{%- set signed = doc.docstatus == 1 -%}
{%- set passed = signed and doc.status == "Accepted" -%}
{%- set item_name = frappe.db.get_value("Item", doc.item_code, "item_name") or doc.item_code -%}
{%- set built_by = doc.get("custom_built_by") or (doc.item_serial_no and frappe.db.get_value("Serial No", doc.item_serial_no, "custom_built_by")) -%}
<div style="font-family:Arial, Helvetica, sans-serif; color:#222; font-size:11pt;">
  <div style="border-bottom:3px solid #00263e; padding-bottom:6px; margin-bottom:12px;">
    <div style="font-size:9pt; letter-spacing:1px; text-transform:uppercase; color:#00609c;">Sapphire Fountains</div>
    <div style="font-size:20pt; font-weight:bold; color:#00263e;">Pre-Shipment Test Certificate</div>
  </div>

  {% if not signed %}
  <div style="border:2px solid #bd2e2b; color:#bd2e2b; font-weight:bold; text-align:center; padding:8px; margin-bottom:12px;">
    DRAFT - NOT SIGNED. This test has not been submitted and certifies nothing.
  </div>
  {% elif not passed %}
  <div style="border:2px solid #bd2e2b; color:#bd2e2b; font-weight:bold; text-align:center; padding:8px; margin-bottom:12px;">
    FAILED. This unit did not pass and must not ship until it is fixed and tested again.
  </div>
  {% endif %}

  <table style="width:100%; border-collapse:collapse; margin-bottom:12px;">
    <tr>
      <td style="padding:4px 8px; color:#555; width:22%;">Product</td>
      <td style="padding:4px 8px; font-weight:bold; width:28%;">{{ item_name | e }}</td>
      <td style="padding:4px 8px; color:#555; width:22%;">Serial number</td>
      <td style="padding:4px 8px; font-weight:bold; font-family:'Courier New', monospace;">{{ (doc.item_serial_no or "") | e }}</td>
    </tr>
    <tr>
      <td style="padding:4px 8px; color:#555;">Part number</td>
      <td style="padding:4px 8px; font-weight:bold; font-family:'Courier New', monospace;">{{ doc.item_code | e }}</td>
      <td style="padding:4px 8px; color:#555;">Test date</td>
      <td style="padding:4px 8px; font-weight:bold;">{{ frappe.utils.formatdate(doc.report_date) }}</td>
    </tr>
    <tr>
      <td style="padding:4px 8px; color:#555;">Built by</td>
      <td style="padding:4px 8px; font-weight:bold;">{{ (frappe.get_fullname(built_by) if built_by else "") | e }}</td>
      <td style="padding:4px 8px; color:#555;">Tested by</td>
      <td style="padding:4px 8px; font-weight:bold;">{{ frappe.get_fullname(doc.inspected_by) | e }}</td>
    </tr>
    <tr>
      <td style="padding:4px 8px; color:#555;">Test</td>
      <td style="padding:4px 8px;">{{ (doc.quality_inspection_template or "") | e }}</td>
      <td style="padding:4px 8px; color:#555;">Record</td>
      <td style="padding:4px 8px;">{{ doc.name | e }}</td>
    </tr>
  </table>

  <table style="width:100%; border-collapse:collapse; font-size:10pt;">
    <thead>
      <tr style="background:#f4f5f7;">
        <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #00263e;">Check</th>
        <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #00263e; width:22%;">Criterion</th>
        <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #00263e; width:14%;">Reading</th>
        <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #00263e; width:12%;">Result</th>
      </tr>
    </thead>
    <tbody>
      {% for r in doc.readings %}
      <tr>
        <td style="padding:5px 8px; border-bottom:1px solid #ddd;">{{ r.specification | e }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #ddd;">
          {%- if r.numeric -%}{{ r.min_value }} to {{ r.max_value }}
          {%- elif r.formula_based_criteria -%}Pass, or N/A for this configuration
          {%- else -%}{{ (r.value or "") | e }}{%- endif -%}
        </td>
        <td style="padding:5px 8px; border-bottom:1px solid #ddd;">{{ ((r.reading_1 if r.numeric else r.reading_value) or "") | e }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #ddd; font-weight:bold; color:{{ '#316564' if r.status == 'Accepted' else '#bd2e2b' }};">
          {{ "Pass" if r.status == "Accepted" else "Fail" }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div style="margin-top:14px; padding:10px 12px; border:2px solid {{ '#316564' if passed else '#bd2e2b' }};">
    <b>Result: {{ "PASSED" if passed else ("FAILED" if signed else "NOT SIGNED") }}.</b>
    {% if passed %}This unit passed every check above and was tested by someone other than the person who built it.{% endif %}
  </div>

  <p style="margin-top:14px; font-size:10pt;">
    <b>Warranty.</b> One year, parts and labor, at the Sapphire Fountains shop, from the date this unit is
    delivered. Keep this certificate and quote the serial number for any warranty claim.
  </p>
  {% if doc.remarks %}<p style="font-size:10pt;"><b>Remarks.</b> {{ doc.remarks | e }}</p>{% endif %}
</div>
""".strip()


def _upsert(name, doc_type, html):
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


def ensure_print_formats():
	"""after_migrate entry: the label and the certificate. Idempotent, and a failure only logs."""
	try:
		if frappe.db.exists("DocType", "Serial No"):
			_upsert(LABEL_PF, "Serial No", LABEL_HTML)
		if frappe.db.exists("DocType", "Quality Inspection"):
			_upsert(CERTIFICATE_PF, "Quality Inspection", CERTIFICATE_HTML)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Product Fulfillment print formats", message=frappe.get_traceback())

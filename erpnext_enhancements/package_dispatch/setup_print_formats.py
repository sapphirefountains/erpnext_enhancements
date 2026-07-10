"""after_migrate setup for the Package Dispatch Sheet Print Format.

Ships a clean printable sheet for a Package Dispatch — the ship-to block, the
item list with values, the total declared / insured value, and what to tell the
store is inside. Print a draft to hand over at the counter, or a submitted one as
the record of where the package went. Created idempotently on every migrate
(Frappe Cloud has no ``bench`` shell) and guarded so a hiccup only logs;
re-upserting the HTML means template edits deploy on the next migrate.
"""

import frappe

DOCTYPE = "Package Dispatch"
MODULE = "Package Dispatch"
SHEET_PF = "Package Dispatch Sheet"

SHEET_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  <div style="display:flex; justify-content:space-between; align-items:baseline; border-bottom:2px solid #333; padding-bottom:6px;">
    <h2 style="margin:0;">Package Dispatch</h2>
    <span style="color:#777;">{{ doc.name if doc.docstatus else "DRAFT" }}</span>
  </div>

  <table style="width:100%; border-collapse:collapse; margin:10px 0 6px; font-size:12px;">
    <tr>
      <td style="padding:3px 8px; color:#777; width:18%;">Dispatch Date</td>
      <td style="padding:3px 8px; font-weight:bold; width:32%;">{{ frappe.format(doc.dispatch_date, {"fieldtype": "Date"}) or "______________" }}</td>
      <td style="padding:3px 8px; color:#777; width:18%;">Store / Carrier</td>
      <td style="padding:3px 8px; font-weight:bold; width:32%;">{{ doc.carrier or "______________" }}{% if doc.service_level %} &middot; {{ doc.service_level }}{% endif %}</td>
    </tr>
    <tr>
      <td style="padding:3px 8px; color:#777;">Tracking #</td>
      <td style="padding:3px 8px; font-weight:bold;">{{ doc.tracking_number or "______________" }}</td>
      <td style="padding:3px 8px; color:#777;">Status</td>
      <td style="padding:3px 8px; font-weight:bold;">{{ doc.shipment_status or "" }}</td>
    </tr>
  </table>

  <div style="display:flex; gap:16px; margin:10px 0 14px;">
    <div style="flex:1; border:1px solid #e3e3e3; border-radius:4px; padding:8px 12px;">
      <div style="color:#777; text-transform:uppercase; font-size:10px; letter-spacing:.04em; margin-bottom:4px;">Ship To</div>
      <div style="font-weight:bold; font-size:14px;">{{ doc.recipient_name or "" }}</div>
      {% if doc.recipient_company %}<div>{{ doc.recipient_company }}</div>{% endif %}
      <div>{{ doc.address_line1 or "" }}</div>
      {% if doc.address_line2 %}<div>{{ doc.address_line2 }}</div>{% endif %}
      <div>{{ doc.city or "" }}{% if doc.state %}, {{ doc.state }}{% endif %} {{ doc.pincode or "" }}</div>
      {% if doc.country %}<div>{{ doc.country }}</div>{% endif %}
      {% if doc.recipient_phone %}<div style="margin-top:4px; color:#555;">☎ {{ doc.recipient_phone }}</div>{% endif %}
    </div>
    <div style="width:38%; border:1px solid #cfe0ff; background:#f4f8ff; border-radius:4px; padding:8px 12px;">
      <div style="color:#777; text-transform:uppercase; font-size:10px; letter-spacing:.04em; margin-bottom:4px;">Declared Value</div>
      <div style="font-size:22px; font-weight:bold;">{{ frappe.format(doc.total_declared_value, {"fieldtype": "Currency"}) }}</div>
      <div style="margin-top:6px; color:#555;">Insure for: <b>{{ frappe.format(doc.insured_value or doc.total_declared_value, {"fieldtype": "Currency"}) }}</b></div>
    </div>
  </div>

  {% if doc.contents_summary %}
  <div style="margin:0 0 10px; padding:6px 10px; border-left:3px solid #999; background:#fafafa;">
    <span style="color:#777;">Contents:</span> {{ doc.contents_summary }}
  </div>
  {% endif %}

  <table style="width:100%; border-collapse:collapse;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Item</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc; width:10%;">Qty</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc; width:20%;">Unit Value</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc; width:20%;">Value</th>
    </tr></thead>
    <tbody>
      {% for row in doc.items %}
      <tr>
        <td style="padding:6px 8px; border-bottom:1px solid #eee;">{{ row.description or row.item or "" }}</td>
        <td style="padding:6px 8px; border-bottom:1px solid #eee; text-align:right;">{{ (row.qty | int) if row.qty == (row.qty | int) else row.qty }}</td>
        <td style="padding:6px 8px; border-bottom:1px solid #eee; text-align:right;">{{ frappe.format(row.rate, {"fieldtype": "Currency"}) }}</td>
        <td style="padding:6px 8px; border-bottom:1px solid #eee; text-align:right;">{{ frappe.format(row.amount, {"fieldtype": "Currency"}) }}</td>
      </tr>
      {% endfor %}
      {% if not doc.items %}
      <tr><td colspan="4" style="padding:8px; color:#999; font-style:italic;">No items listed.</td></tr>
      {% endif %}
    </tbody>
    <tfoot><tr>
      <td colspan="3" style="padding:8px; text-align:right; font-weight:bold; border-top:2px solid #ccc;">Total Declared Value</td>
      <td style="padding:8px; text-align:right; font-weight:bold; border-top:2px solid #ccc;">{{ frappe.format(doc.total_declared_value, {"fieldtype": "Currency"}) }}</td>
    </tr></tfoot>
  </table>

  {% if doc.notes %}
  <div style="margin-top:12px;">
    <span style="color:#777;">Notes:</span>
    <div style="border-bottom:1px solid #ccc; min-height:20px;">{{ doc.notes }}</div>
  </div>
  {% endif %}

  <div style="margin-top:20px; color:#777; font-size:11px;">
    Requested by {{ doc.requested_by or "" }}
    {% if doc.delivered_date %} &middot; Delivered {{ frappe.format(doc.delivered_date, {"fieldtype": "Date"}) }}{% endif %}
  </div>
</div>
""".strip()


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


def ensure_package_dispatch_print_formats():
	"""after_migrate entry: ship the Package Dispatch Sheet Print Format.
	Idempotent (upserts the HTML) and guarded (a failure only logs)."""
	try:
		if frappe.db.exists("DocType", DOCTYPE):
			_upsert_print_format(SHEET_PF, SHEET_HTML)
			frappe.db.commit()
			frappe.logger().info("[package_dispatch] ensured Package Dispatch Sheet print format")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Package Dispatch print formats")

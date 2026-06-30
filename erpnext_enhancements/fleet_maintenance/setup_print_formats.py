"""after_migrate setup for the Vehicle Maintenance Log Print Format.

Ships a printable checklist sheet for a Vehicle Maintenance Log — print a draft
of a given type to get a blank checklist to keep in the vehicle, or print a
submitted log as the record of what was done. Created idempotently on every
migrate (Frappe Cloud has no ``bench`` shell) and guarded so a hiccup only logs;
re-upserting the HTML means template edits deploy on the next migrate.
"""

import frappe

DOCTYPE = "Vehicle Maintenance Log"
MODULE = "Fleet Maintenance"
CHECKLIST_PF = "Vehicle Maintenance Checklist"

CHECKLIST_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  <div style="display:flex; justify-content:space-between; align-items:baseline; border-bottom:2px solid #333; padding-bottom:6px;">
    <h2 style="margin:0;">Vehicle Maintenance &mdash; {{ doc.maintenance_type }}</h2>
    <span style="color:#777;">{{ doc.name if doc.docstatus else "" }}</span>
  </div>

  <table style="width:100%; border-collapse:collapse; margin:10px 0 14px; font-size:12px;">
    <tr>
      <td style="padding:3px 8px; color:#777; width:18%;">Vehicle</td>
      <td style="padding:3px 8px; font-weight:bold; width:32%;">{{ doc.vehicle or "" }}</td>
      <td style="padding:3px 8px; color:#777; width:18%;">Service Date</td>
      <td style="padding:3px 8px; font-weight:bold; width:32%;">{{ doc.service_date or "______________" }}</td>
    </tr>
    <tr>
      <td style="padding:3px 8px; color:#777;">Performed By</td>
      <td style="padding:3px 8px; font-weight:bold;">{{ doc.performed_by or "______________" }}</td>
      <td style="padding:3px 8px; color:#777;">Odometer (mi)</td>
      <td style="padding:3px 8px; font-weight:bold;">{{ doc.odometer or "______________" }}</td>
    </tr>
  </table>

  <table style="width:100%; border-collapse:collapse;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc; width:50%;">Task</th>
      <th style="text-align:center; padding:6px 8px; border-bottom:2px solid #ccc; width:18%;">Status</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc; width:32%;">Notes</th>
    </tr></thead>
    <tbody>
      {% for row in doc.checklist %}
      <tr>
        <td style="padding:6px 8px; border-bottom:1px solid #eee;">{{ row.task }}{% if row.is_mandatory %} <span style="color:#b54708;">*</span>{% endif %}</td>
        <td style="padding:6px 8px; border-bottom:1px solid #eee; text-align:center;">{{ row.status or "&#9744;" }}</td>
        <td style="padding:6px 8px; border-bottom:1px solid #eee; color:#555;">{{ row.notes or "" }}</td>
      </tr>
      {% endfor %}
      {% if not doc.checklist %}
      <tr><td colspan="3" style="padding:8px; color:#999; font-style:italic;">No checklist items &mdash; pick a Maintenance Type to load the standard checklist.</td></tr>
      {% endif %}
    </tbody>
  </table>

  {% if doc.issues_found %}
  <div style="margin-top:12px; border:1px solid #fedf89; background:#fffaeb; border-radius:4px; padding:6px 10px;">
    <b style="color:#b54708;">&#9888; Follow-up needed:</b> {{ doc.follow_up_notes or "" }}
  </div>
  {% endif %}

  <div style="margin-top:12px;">
    <span style="color:#777;">Notes:</span>
    <div style="border-bottom:1px solid #ccc; min-height:20px;">{{ doc.notes or "" }}</div>
  </div>

  <div style="margin-top:24px; color:#777; font-size:11px;">
    <span style="color:#b54708;">*</span> required item &middot; &#9744; = to do
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


def ensure_fleet_print_formats():
	"""after_migrate entry: ship the Vehicle Maintenance Checklist Print Format.
	Idempotent (upserts the HTML) and guarded (a failure only logs)."""
	try:
		if frappe.db.exists("DocType", DOCTYPE):
			_upsert_print_format(CHECKLIST_PF, CHECKLIST_HTML)
			frappe.db.commit()
			frappe.logger().info("[fleet_maintenance] ensured Vehicle Maintenance Checklist print format")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fleet Maintenance print formats")

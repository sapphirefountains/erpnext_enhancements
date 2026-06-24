"""after_migrate setup for the Water Feature Design Print Formats.

Ships two views of a design's results, both rendered server-side (Jinja) from the
persisted rollups + ``calc_results`` audit trail:

* **Water Feature Design - Results** — the simple, final end-results: headline
  rollups (basin gallons, circulation, TDH, selected pump, ...) and each
  calculation's final value/unit/status.
* **Water Feature Design - Calculation Audit** — the robust view: for every
  calculation, the exact formula, the inputs (with provenance), the step-by-step
  working, the source citation, and any warnings — laid out to hand-compare
  against the source workbooks.

Created idempotently on every migrate (Frappe Cloud has no ``bench`` shell), and
guarded so a hiccup only logs. Re-upserting the HTML means template edits deploy
on the next migrate.
"""

import frappe

DOCTYPE = "Water Feature Design"
MODULE = "Water Engineering"
RESULTS_PF = "Water Feature Design - Results"
AUDIT_PF = "Water Feature Design - Calculation Audit"

# --- simple "end results" view ----------------------------------------------
RESULTS_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  <h2 style="margin:0;">{{ doc.design_title or doc.name }}</h2>
  <div style="color:#777; margin:2px 0 14px; font-size:11px;">
    {{ doc.name }}{% if doc.project %} &middot; {{ doc.project }}{% endif %}{% if doc.status %} &middot; {{ doc.status }}{% endif %}{% if doc.completion_percent %} &middot; {{ doc.completion_percent }}% complete{% endif %}
  </div>

  <h3 style="margin:0 0 4px; font-size:13px;">Key results</h3>
  <table style="width:100%; border-collapse:collapse; margin-bottom:16px;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Result</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Value</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Unit</th>
    </tr></thead>
    <tbody>
      {% set rollups = [
        ('Total basin volume', doc.total_basin_gallons, 'gal'),
        ('Required circulation', doc.required_circulation_gpm, 'GPM'),
        ('Design feature flow', doc.design_flow_gpm, 'GPM'),
        ('Total dynamic head (TDH)', doc.computed_tdh_ft, 'ft'),
        ('Selected pump', doc.selected_pump, ''),
        ('Chlorinator feed', doc.chlorinator_feed_gph, 'gal/hr'),
        ('Drain capacity', doc.drain_capacity_gpm, 'GPM'),
        ('Surge basin volume', doc.surge_basin_gallons, 'gal')
      ] %}
      {% for label, value, unit in rollups %}{% if value %}
      <tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ label }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right; font-weight:bold;">{{ "%.2f"|format(value) if value is number else value }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#777;">{{ unit }}</td>
      </tr>
      {% endif %}{% endfor %}
    </tbody>
  </table>

  <h3 style="margin:0 0 4px; font-size:13px;">Final calculation values</h3>
  <table style="width:100%; border-collapse:collapse;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Calculation</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Value</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Unit</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Status</th>
    </tr></thead>
    <tbody>
      {% for r in doc.calc_results %}
      <tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ r.calc }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right; font-weight:bold;">{{ r.value }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#777;">{{ r.unit }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ r.status or '' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
""".strip()

# --- robust "formula audit" view --------------------------------------------
AUDIT_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  <h2 style="margin:0;">{{ doc.design_title or doc.name }} &mdash; calculation audit</h2>
  <div style="color:#777; margin:2px 0 14px; font-size:11px;">
    {{ doc.name }}{% if doc.project %} &middot; {{ doc.project }}{% endif %} &middot; every result below with its exact formula, inputs, and step-by-step working for hand-verification against the source workbooks.
  </div>

  {% for r in doc.calc_results %}
  <div style="border:1px solid #ddd; border-radius:5px; margin:0 0 10px; padding:8px 12px; page-break-inside:avoid;">
    <div style="display:flex; justify-content:space-between; align-items:baseline; border-bottom:1px solid #eee; padding-bottom:4px;">
      <span style="font-weight:bold; font-size:13px;">{{ r.calc }}</span>
      <span><b>{{ r.value }}</b> <span style="color:#777;">{{ r.unit }}</span>{% if r.status %} &middot; <span style="color:#555;">{{ r.status }}</span>{% endif %}</span>
    </div>

    {% if r.formula %}
    <div style="margin-top:6px;"><span style="color:#888; font-size:11px;">FORMULA</span><br>
      <code style="background:#f6f8fa; padding:2px 5px; border-radius:3px; font-size:11px;">{{ r.formula }}</code>
    </div>
    {% endif %}

    {% if r.inputs_text %}
    <div style="margin-top:6px;"><span style="color:#888; font-size:11px;">INPUTS</span>
      <table style="width:100%; border-collapse:collapse; font-size:11px; margin-top:2px;">
        {% for line in r.inputs_text.split('\n') %}{% set c = line.split('\t') %}
        <tr>
          <td style="padding:2px 6px; color:#444; white-space:nowrap;">{{ c[0] }}</td>
          <td style="padding:2px 6px; text-align:right; font-weight:bold;">{{ c[1] if c|length > 1 else '' }}</td>
          <td style="padding:2px 6px; color:#777;">{{ c[2] if c|length > 2 else '' }}</td>
          <td style="padding:2px 6px; color:#999;">{{ c[3] if c|length > 3 else '' }}</td>
        </tr>
        {% endfor %}
      </table>
    </div>
    {% endif %}

    {% if r.steps %}
    <div style="margin-top:6px;"><span style="color:#888; font-size:11px;">WORKING</span>
      <pre style="margin:2px 0 0; padding:6px 8px; background:#f6f8fa; border-radius:3px; font-size:11px; white-space:pre-wrap; font-family:'SF Mono',Consolas,monospace;">{{ r.steps }}</pre>
    </div>
    {% endif %}

    {% if r.citations %}<div style="margin-top:5px; font-size:11px; color:#777;">Source: {{ r.citations }}</div>{% endif %}
    {% if r.warnings %}<div style="margin-top:5px; font-size:11px; color:#b54708; background:#fffaeb; border:1px solid #fedf89; border-radius:3px; padding:4px 6px;">&#9888; {{ r.warnings }}</div>{% endif %}
  </div>
  {% endfor %}

  {% if not doc.calc_results %}
  <div style="color:#999; font-style:italic;">No calculations yet — set the design's inputs and recompute.</div>
  {% endif %}
</div>
""".strip()


def _upsert_print_format(name, html):
    """Create or update one custom Jinja Print Format for Water Feature Design."""
    if frappe.db.exists("Print Format", name):
        pf = frappe.get_doc("Print Format", name)
    else:
        pf = frappe.new_doc("Print Format")
        pf.name = name
    pf.doc_type = DOCTYPE
    pf.module = MODULE
    pf.print_format_type = "Jinja"
    pf.custom_format = 1
    pf.standard = "No"
    pf.disabled = 0
    pf.html = html
    pf.save(ignore_permissions=True)


def ensure_water_print_formats():
    """after_migrate entry: ship the Results + Calculation Audit Print Formats.
    Idempotent (upserts the HTML) and guarded (a failure only logs)."""
    try:
        if not frappe.db.exists("DocType", DOCTYPE):
            return
        _upsert_print_format(RESULTS_PF, RESULTS_HTML)
        _upsert_print_format(AUDIT_PF, AUDIT_HTML)
        frappe.db.commit()
        frappe.logger().info("[water_engineering] ensured Water Feature Design print formats")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water Engineering print formats")

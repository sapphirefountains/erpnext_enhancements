"""after_migrate setup for the Water Feature Design Print Formats.

Ships several views of a design, all rendered server-side (Jinja) from the
persisted rollups + ``calc_results`` audit trail:

* **Water Feature Design - Results** — the simple, final end-results: headline
  rollups (basin gallons, circulation, TDH, selected pump, ...) and each
  calculation's final value/unit/status.
* **Water Feature Design - Calculation Audit** — the robust view: for every
  calculation, the exact formula, the inputs (with provenance), the step-by-step
  working, the source citation, and any warnings — laid out to hand-compare
  against the source workbooks.
* **Water Feature Design - Design Calculations** — a single-sheet plan-check
  calc sheet for regulated venues (spa / pool): the vessel, the design-basis
  calcs, and the equipment grouped by hydraulic system (circulation vs. the
  therapy-jet loop) with narrative equipment lines.

Created idempotently on every migrate (Frappe Cloud has no ``bench`` shell), and
guarded so a hiccup only logs. Re-upserting the HTML means template edits deploy
on the next migrate.
"""

import frappe

DOCTYPE = "Water Feature Design"
CONTROL_DOCTYPE = "Control Panel Design"
MODULE = "Water Engineering"
RESULTS_PF = "Water Feature Design - Results"
AUDIT_PF = "Water Feature Design - Calculation Audit"
SCHEDULES_PF = "Water Feature Design - Schedules"
DESIGN_CALC_PF = "Water Feature Design - Design Calculations"
SUBMITTAL_PF = "Control Panel Design - Submittal"
PANEL_SCHEDULE_PF = "Control Panel Design - Panel Schedule"
PACKET_PF = "Water Feature Design - Submittal Packet"

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

  {% set issues = we_design_issues(doc) %}
  {% if issues %}
  <h3 style="margin:16px 0 4px; font-size:13px;">Design review — open issues</h3>
  <table style="width:100%; border-collapse:collapse; margin-bottom:10px;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Severity</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Section</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Issue</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Source</th>
    </tr></thead>
    <tbody>
      {% for i in issues %}
      <tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; font-weight:bold; color:{{ '#b52a2a' if i.severity == 'blocker' else ('#b54708' if i.severity == 'warning' else '#555') }};">{{ i.severity|title }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#777;">{{ i.section }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ i.title }}{% if i.fix_hint %}<br><span style="color:#777; font-size:11px;">Fix: {{ i.fix_hint }}</span>{% endif %}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#999; font-size:11px;">{{ i.citation or '' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if doc.issue_acks %}
  <h3 style="margin:16px 0 4px; font-size:13px;">Acknowledged warnings</h3>
  <table style="width:100%; border-collapse:collapse;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Warning</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Acknowledged by</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">On</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Note</th>
    </tr></thead>
    <tbody>
      {% for a in doc.issue_acks %}
      <tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ a.title or a.issue_key }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ a.acknowledged_by or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#777;">{{ a.acknowledged_on or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#555;">{{ a.note or '' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}
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


# --- Equipment + Piping Schedules (DOC-0121 design-package deliverable) ------
SCHEDULES_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  <h2 style="margin:0;">{{ doc.design_title or doc.name }} &mdash; Schedules</h2>
  <div style="color:#777; margin:2px 0 14px; font-size:11px;">
    {{ doc.name }}{% if doc.project %} &middot; {{ doc.project }}{% endif %} &middot; Equipment &amp; Piping schedules (DOC-0121).
  </div>

  <h3 style="margin:0 0 4px; font-size:13px;">Equipment Schedule</h3>
  <table style="width:100%; border-collapse:collapse; margin-bottom:16px;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Item</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Spec</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Rated GPM</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Rated TDH (ft)</th>
    </tr></thead>
    <tbody>
      {% if doc.selected_pump %}<tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; font-weight:bold;">Selected pump</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ doc.selected_pump }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ "%.0f"|format(doc.design_flow_gpm) if doc.design_flow_gpm else '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ "%.1f"|format(doc.computed_tdh_ft) if doc.computed_tdh_ft else '' }}</td>
      </tr>{% endif %}
      {% for p in doc.pumps %}<tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ p.pump_item or '' }}{% if p.part_number %} ({{ p.part_number }}){% endif %}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ p.pump_description or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ p.rated_gpm or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ p.rated_tdh_ft or '' }}</td>
      </tr>{% endfor %}
      {% for b in doc.basins %}<tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">Basin{% if b.basin_label %} &mdash; {{ b.basin_label }}{% endif %}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ b.shape }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ "%.0f"|format(b.volume_gal) if b.volume_gal else '' }} gal</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;"></td>
      </tr>{% endfor %}
    </tbody>
  </table>

  <h3 style="margin:0 0 4px; font-size:13px;">Piping Schedule</h3>
  <table style="width:100%; border-collapse:collapse;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Segment</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Line</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Material</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Size</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Len (ft)</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Vel (fps)</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Head (ft)</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Fittings / Equipment</th>
    </tr></thead>
    <tbody>
      {% for s in doc.pipe_segments %}<tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ s.segment_label or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ s.line_type or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ s.material or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ s.nominal_size or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ s.pipe_length_ft or '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ "%.2f"|format(s.velocity_fps) if s.velocity_fps else '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right;">{{ "%.2f"|format(s.head_loss_ft) if s.head_loss_ft else '' }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#555;">{{ s.fittings_summary or '' }}{% if s.fittings_summary and s.components_summary %}; {% endif %}{{ s.components_summary or '' }}</td>
      </tr>{% endfor %}
    </tbody>
  </table>

  {% set schedule = we_fitting_schedule(doc) %}
  {% if schedule %}
  <h3 style="margin:16px 0 4px; font-size:13px;">Fitting Schedule</h3>
  <table style="width:100%; border-collapse:collapse;">
    <thead><tr style="background:#f4f5f7;">
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Type</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Fitting / Component</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Material</th>
      <th style="text-align:left; padding:6px 8px; border-bottom:2px solid #ccc;">Size</th>
      <th style="text-align:right; padding:6px 8px; border-bottom:2px solid #ccc;">Qty</th>
    </tr></thead>
    <tbody>
      {% for f in schedule %}
      <tr>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; color:#777;">{{ f.kind }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ f.type }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ f.material }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee;">{{ f.size }}</td>
        <td style="padding:5px 8px; border-bottom:1px solid #eee; text-align:right; font-weight:bold;">{{ f.qty }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}
</div>
""".strip()


# --- Design Calculations (single-sheet plan-check view, regulated venues) ---
# Groups the captured data by hydraulic SYSTEM (vessel -> design basis ->
# circulation loop -> therapy-jet loop -> heating/treatment -> instrumentation
# -> compliance) with narrative equipment lines, the way an AHJ plan-checker
# reads a spa/pool calc sheet. Fixtures/pumps/piping route to a loop via their
# `system` tag; catalog equipment routes by `equipment_class`. All figures are
# the engine's persisted rollups + per-row computed columns — nothing is retyped.
DESIGN_CALC_HTML = """
{%- set tb = we_title_block(doc.name) -%}
{%- macro sechead(no, title, note='') -%}
<table style="width:100%; border-collapse:collapse; margin:20px 0 8px;"><tr>
  <td style="width:26px; font:bold 18px 'Helvetica Neue',Arial,sans-serif; color:#1b5e82; vertical-align:bottom; padding-bottom:3px;">{{ no }}</td>
  <td style="font:bold 13px 'Helvetica Neue',Arial,sans-serif; letter-spacing:0.5px; text-transform:uppercase; vertical-align:bottom; border-bottom:1px solid #cbd6dd; padding-bottom:4px;">{{ title }}</td>
  <td style="text-align:right; font:11px 'Helvetica Neue',Arial,sans-serif; color:#6c7a83; vertical-align:bottom; border-bottom:1px solid #cbd6dd; padding-bottom:4px;">{{ note }}</td>
</tr></table>
{%- endmacro -%}
{%- macro line(role, desc, tag='', ok=false) -%}
<tr>
  <td style="padding:6px 12px 6px 0; vertical-align:top; width:118px; font:bold 10px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; letter-spacing:0.3px; color:#4a5a63; border-bottom:1px solid #e6ebee;">{{ role }}</td>
  <td style="padding:6px 12px 6px 0; vertical-align:top; font:12px 'Helvetica Neue',Arial,sans-serif; color:#16232e; border-bottom:1px solid #e6ebee;">{{ desc }}</td>
  <td style="padding:6px 0; vertical-align:top; text-align:right; white-space:nowrap; font:bold 11px 'Helvetica Neue',Arial,sans-serif; color:{{ '#2f8557' if ok else '#124a67' }}; border-bottom:1px solid #e6ebee;">{% if ok and tag %}&#10003; {% endif %}{{ tag }}</td>
</tr>
{%- endmacro -%}
{%- set ns = namespace(surface=0, jet=false) -%}
{%- for b in doc.basins %}{% set ns.surface = ns.surface + (b.surface_area_sf or 0) %}{% endfor -%}
{%- for fx in doc.venue_fixtures %}{% if fx.fixture_type == 'Therapy Jet' or fx.system == 'Therapy-Jet' %}{% set ns.jet = true %}{% endif %}{% endfor -%}
{%- for p in doc.pumps %}{% if p.system == 'Therapy-Jet' %}{% set ns.jet = true %}{% endif %}{% endfor -%}
{%- for s in doc.pipe_segments %}{% if s.system == 'Therapy-Jet' %}{% set ns.jet = true %}{% endif %}{% endfor -%}
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#16232e; font-size:12px;">

  {% if tb.preliminary %}<div style="text-align:center; color:#8f5a1d; font:bold 9px 'Helvetica Neue',Arial,sans-serif; letter-spacing:1px; border-bottom:1px solid #e6d3bd; padding-bottom:6px; margin-bottom:10px;">PRELIMINARY &mdash; NOT FOR CONSTRUCTION &mdash; SEALED BY P.E. BEFORE ISSUE</div>{% endif %}

  <table style="width:100%; border-collapse:collapse; border-bottom:2px solid #1b5e82;">
    <tr>
      <td style="vertical-align:bottom; padding-bottom:10px;">
        <div style="font:bold 11px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; letter-spacing:1.5px; color:#124a67;">Design Calculations{% if doc.venue_type %} &middot; {{ doc.venue_type }}{% endif %}</div>
        <div style="font:bold 24px 'Helvetica Neue',Arial,sans-serif; margin:5px 0 2px;">{{ tb.project.name }}</div>
        {% if doc.vessel_model %}<div style="color:#5a6a73; font-size:12px;">{{ doc.vessel_model }}</div>{% endif %}
      </td>
      <td style="vertical-align:bottom; padding-bottom:10px; text-align:right; font-size:11px; color:#3a4a53; white-space:nowrap;">
        <div><span style="color:#8794a0;">Design</span> &nbsp;{{ doc.name }}</div>
        {% if doc.governing_code %}<div><span style="color:#8794a0;">Code</span> &nbsp;{{ doc.governing_code }}</div>{% endif %}
        <div><span style="color:#8794a0;">Issue</span> &nbsp;{{ doc.issue_date or '&mdash;' }}</div>
        <div><span style="color:#8794a0;">By</span> &nbsp;{{ doc.drawn_by or tb.contractor.name }}</div>
        <div><span style="color:#8794a0;">Status</span> &nbsp;{{ doc.status or 'Draft' }}</div>
      </td>
    </tr>
  </table>

  {{ sechead('1', 'Vessel & Volume', doc.vessel_listing or '') }}
  {% if doc.vessel_model or doc.vessel_construction %}
  <p style="margin:0 0 8px; font-size:12px;">{% if doc.vessel_model %}<b>{{ doc.vessel_model }}</b>{% endif %}{% if doc.vessel_construction %} &mdash; {{ doc.vessel_construction }}{% endif %}{% if doc.vessel_listing %} Listing {{ doc.vessel_listing }}.{% endif %}</p>
  {% endif %}
  {% for b in doc.basins %}
  <table style="width:100%; border-collapse:collapse; margin-bottom:8px; font-size:11px;">
    <tr>
      {% if b.basin_label %}<td style="border:1px solid #dfe6ea; padding:4px 8px; color:#6c7a83;">Vessel</td><td style="border:1px solid #dfe6ea; padding:4px 8px;">{{ b.basin_label }}</td>{% endif %}
      <td style="border:1px solid #dfe6ea; padding:4px 8px; color:#6c7a83;">Shape</td><td style="border:1px solid #dfe6ea; padding:4px 8px;">{{ b.shape or '&mdash;' }}</td>
      <td style="border:1px solid #dfe6ea; padding:4px 8px; color:#6c7a83;">Volume</td><td style="border:1px solid #dfe6ea; padding:4px 8px;">{{ "%.0f"|format(b.volume_gal) if b.volume_gal else '&mdash;' }} gal</td>
    </tr>
    <tr>
      <td style="border:1px solid #dfe6ea; padding:4px 8px; color:#6c7a83;">Dimensions</td><td style="border:1px solid #dfe6ea; padding:4px 8px;">{% if b.length_in %}{{ "%g"|format(b.length_in) }} &times; {{ "%g"|format(b.width_in) }}{% if b.height_in %} &times; {{ "%g"|format(b.height_in) }}{% endif %} in{% elif b.diameter_in %}&#8960; {{ "%g"|format(b.diameter_in) }}{% if b.height_in %} &times; {{ "%g"|format(b.height_in) }}{% endif %} in{% else %}&mdash;{% endif %}</td>
      <td style="border:1px solid #dfe6ea; padding:4px 8px; color:#6c7a83;">Perimeter</td><td style="border:1px solid #dfe6ea; padding:4px 8px;">{{ "%g"|format(b.perimeter_ft) if b.perimeter_ft else '&mdash;' }} ft</td>
      <td style="border:1px solid #dfe6ea; padding:4px 8px; color:#6c7a83;">Water surface</td><td style="border:1px solid #dfe6ea; padding:4px 8px;">{{ "%g"|format(b.surface_area_sf) if b.surface_area_sf else '&mdash;' }} sq ft</td>
    </tr>
  </table>
  {% endfor %}
  {% if not doc.basins %}<p style="color:#98a4ac; font-style:italic; font-size:11px;">No basin defined.</p>{% endif %}

  {{ sechead('2', 'Design Basis', (("%g"|format(doc.max_turnover_min)) ~ '-min max turnover') if doc.max_turnover_min else '') }}
  <table style="width:100%; border-collapse:collapse; background:#f1f5f8; border-left:3px solid #1b5e82;">
    {% if doc.design_flow_published_gpm or doc.design_flow_gpm %}
    <tr>
      <td style="padding:7px 12px; font-size:12px; border-bottom:1px solid #e0e7eb;">Design flow rate <span style="color:#8794a0; font-size:10px;">(published)</span></td>
      <td style="padding:7px 12px; text-align:right; font-size:12px; color:#5a6a73; border-bottom:1px solid #e0e7eb;"><b style="color:#124a67;">{{ "%.0f"|format(doc.design_flow_published_gpm or doc.design_flow_gpm) }} GPM</b></td>
    </tr>
    {% endif %}
    {% if doc.minimum_flow_gpm %}
    <tr>
      <td style="padding:7px 12px; font-size:12px; border-bottom:1px solid #e0e7eb;">Minimum circulation flow <span style="color:#8794a0; font-size:10px;">(volume &divide; turnover)</span></td>
      <td style="padding:7px 12px; text-align:right; font-size:12px; color:#5a6a73; border-bottom:1px solid #e0e7eb;">{% if doc.total_basin_gallons and doc.max_turnover_min %}{{ "%.0f"|format(doc.total_basin_gallons) }} &divide; {{ "%g"|format(doc.max_turnover_min) }} min = {% endif %}<b style="color:#124a67;">{{ "%.2f"|format(doc.minimum_flow_gpm) }} GPM</b></td>
    </tr>
    {% endif %}
    {% if doc.turnover_time_min %}
    <tr>
      <td style="padding:7px 12px; font-size:12px; border-bottom:1px solid #e0e7eb;">Turnover time at published flow <span style="color:#8794a0; font-size:10px;">(volume &divide; flow)</span></td>
      <td style="padding:7px 12px; text-align:right; font-size:12px; color:#5a6a73; border-bottom:1px solid #e0e7eb;">{% if doc.total_basin_gallons and (doc.design_flow_published_gpm or doc.design_flow_gpm) %}{{ "%.0f"|format(doc.total_basin_gallons) }} &divide; {{ "%.0f"|format(doc.design_flow_published_gpm or doc.design_flow_gpm) }} = {% endif %}<b style="color:#124a67;">{{ "%.2f"|format(doc.turnover_time_min) }} min</b></td>
    </tr>
    {% endif %}
    {% if doc.bather_load %}
    <tr>
      <td style="padding:7px 12px; font-size:12px;">Bather load <span style="color:#8794a0; font-size:10px;">(surface &divide; sq ft per person)</span></td>
      <td style="padding:7px 12px; text-align:right; font-size:12px; color:#5a6a73;">{% if ns.surface and doc.bather_sf_per_person %}{{ "%g"|format(ns.surface) }} &divide; {{ "%g"|format(doc.bather_sf_per_person) }} = {% endif %}<b style="color:#124a67;">{{ doc.bather_load }} persons</b></td>
    </tr>
    {% endif %}
  </table>

  {{ sechead('3', 'Circulation System', (("%.0f"|format(doc.required_circulation_gpm)) ~ ' GPM') if doc.required_circulation_gpm else '') }}
  <div style="border-left:3px solid #1b5e82; padding-left:14px; margin-bottom:6px;">
    <div style="display:inline-block; font:bold 10px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; letter-spacing:0.8px; color:#1b5e82; background:#eef4f8; padding:3px 8px; border-radius:3px; margin-bottom:4px;">Circulation Loop{% if doc.design_flow_gpm %} &middot; {{ "%.0f"|format(doc.design_flow_gpm) }} GPM{% endif %}</div>
    <table style="width:100%; border-collapse:collapse;">
      {% for e in doc.design_equipment if e.equipment_class == 'Filter' %}
        {{ line('Filter', (e.equipment_item ~ ((' · ' ~ e.model_no) if e.model_no else '')), e.rating or '') }}
      {% endfor %}
      {% for fx in doc.venue_fixtures if fx.fixture_type == 'Skimmer' and fx.system != 'Therapy-Jet' %}
        {{ line('Skimmer', (((fx.qty or 1)|string) ~ ' × ' ~ (fx.part_no or 'skimmer') ~ ((' · ' ~ fx.status) if fx.status else '')), (("%.1f"|format(fx.computed_flow_gpm) ~ ' GPM') if fx.computed_flow_gpm else ((("%g"|format(fx.rated_gpm)) ~ ' GPM') if fx.rated_gpm else ''))) }}
      {% endfor %}
      {% if doc.skimmer_count %}{{ line('', 'Skimmers required (1 per ' ~ (("%g"|format(doc.skimmer_sf_each)) if doc.skimmer_sf_each else '100') ~ ' sq ft)', doc.skimmer_count|string) }}{% endif %}
      {% for fx in doc.venue_fixtures if fx.fixture_type == 'Main Drain' and fx.system != 'Therapy-Jet' %}
        {{ line('Main drains', ((fx.part_no or 'Main drain') ~ ', ' ~ ((fx.qty or 1)|string) ~ ' drains' ~ ((' · ' ~ ("%g"|format(fx.open_area_in2)) ~ ' sq in open area') if fx.open_area_in2 else '') ~ ((' · ' ~ ("%.0f"|format(fx.computed_flow_gpm)) ~ ' GPM') if fx.computed_flow_gpm else '')), (fx.status or 'VGB'), ok=true) }}
      {% endfor %}
      {% for fx in doc.venue_fixtures if fx.fixture_type == 'Return Inlet' and fx.system != 'Therapy-Jet' %}
        {{ line('Return inlets', (((fx.qty or 1)|string) ~ ' × ' ~ (fx.part_no or 'return inlet')), ((("%g"|format(fx.rated_gpm)) ~ ' GPM') if fx.rated_gpm else (((fx.qty or 1)|string) ~ ' inlets'))) }}
      {% endfor %}
      {% set circp = namespace(any=false) %}
      {% for p in doc.pumps if p.system != 'Therapy-Jet' and p.is_selected %}{% set circp.any = true %}{% endfor %}
      {% for p in doc.pumps if p.system != 'Therapy-Jet' and (p.is_selected or not circp.any) %}
        {{ line(('Pump ★' if p.is_selected else 'Pump'), ((p.pump_item or p.pump_description or 'Pump') ~ ((' · ' ~ p.part_number) if p.part_number else '')), ((("%.0f"|format(p.rated_gpm)) ~ ' GPM') if p.rated_gpm else '') ~ ((' / ' ~ ("%.0f"|format(p.rated_tdh_ft)) ~ ' ft') if p.rated_tdh_ft else '')) }}
      {% endfor %}
      {% set cs = namespace(suction='', discharge='') %}
      {% for s in doc.pipe_segments if s.system != 'Therapy-Jet' %}{% if s.line_type == 'Suction' and not cs.suction %}{% set cs.suction = s.nominal_size %}{% endif %}{% if s.line_type == 'Discharge' and not cs.discharge %}{% set cs.discharge = s.nominal_size %}{% endif %}{% endfor %}
      {% if cs.suction or cs.discharge %}{{ line('Piping', ((cs.suction ~ ' suction') if cs.suction else '') ~ ((' / ') if (cs.suction and cs.discharge) else '') ~ ((cs.discharge ~ ' return') if cs.discharge else '')) }}{% endif %}
    </table>
  </div>

  {% if ns.jet %}
  {{ sechead('4', 'Therapy-Jet System', 'Independent loop') }}
  <div style="border-left:3px solid #8f5a1d; padding-left:14px; margin-bottom:6px;">
    <div style="display:inline-block; font:bold 10px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; letter-spacing:0.8px; color:#8f5a1d; background:#f6efe6; padding:3px 8px; border-radius:3px; margin-bottom:4px;">Therapy-Jet Loop</div>
    <table style="width:100%; border-collapse:collapse;">
      {% for fx in doc.venue_fixtures if fx.fixture_type == 'Therapy Jet' %}
        {{ line('Therapy jets', ((fx.part_no or 'Therapy jets') ~ ' · ' ~ ((fx.qty or 1)|string) ~ ' jets'), (("%.0f"|format(fx.computed_flow_gpm) ~ ' GPM') if fx.computed_flow_gpm else ((("%g"|format(fx.rated_gpm)) ~ ' GPM') if fx.rated_gpm else ''))) }}
      {% endfor %}
      {% for fx in doc.venue_fixtures if fx.fixture_type == 'Main Drain' and fx.system == 'Therapy-Jet' %}
        {{ line('Main drains', ((fx.part_no or 'Main drain') ~ ', ' ~ ((fx.qty or 1)|string) ~ ' drains' ~ ((' · ' ~ ("%g"|format(fx.open_area_in2)) ~ ' sq in open area') if fx.open_area_in2 else '') ~ ((' · ' ~ ("%.0f"|format(fx.computed_flow_gpm)) ~ ' GPM') if fx.computed_flow_gpm else '')), (fx.status or 'VGB'), ok=true) }}
      {% endfor %}
      {% set jetp = namespace(any=false) %}
      {% for p in doc.pumps if p.system == 'Therapy-Jet' and p.is_selected %}{% set jetp.any = true %}{% endfor %}
      {% for p in doc.pumps if p.system == 'Therapy-Jet' and (p.is_selected or not jetp.any) %}
        {{ line(('Pump ★' if p.is_selected else 'Pump'), ((p.pump_item or p.pump_description or 'Pump') ~ ((' · ' ~ p.part_number) if p.part_number else '')), ((("%.0f"|format(p.rated_gpm)) ~ ' GPM') if p.rated_gpm else '') ~ ((' / ' ~ ("%.0f"|format(p.rated_tdh_ft)) ~ ' ft') if p.rated_tdh_ft else '')) }}
      {% endfor %}
      {% set js = namespace(suction='', discharge='') %}
      {% for s in doc.pipe_segments if s.system == 'Therapy-Jet' %}{% if s.line_type == 'Suction' and not js.suction %}{% set js.suction = s.nominal_size %}{% endif %}{% if s.line_type == 'Discharge' and not js.discharge %}{% set js.discharge = s.nominal_size %}{% endif %}{% endfor %}
      {% if js.suction or js.discharge %}{{ line('Piping', ((js.suction ~ ' suction') if js.suction else '') ~ ((' / ') if (js.suction and js.discharge) else '') ~ ((js.discharge ~ ' return') if js.discharge else '')) }}{% endif %}
    </table>
  </div>
  {% endif %}

  {% set treat = doc.design_equipment | selectattr('equipment_class', 'in', ['Heater', 'Chemical Controller', 'Chemical Feed Pump']) | list %}
  {% if treat or doc.chlorinator_feed_gph %}
  {{ sechead('5', 'Heating & Treatment', doc.chem_water_type or '') }}
  <div style="border-left:3px solid #a6b4bd; padding-left:14px; margin-bottom:6px;">
    <table style="width:100%; border-collapse:collapse;">
      {% for e in doc.design_equipment if e.equipment_class == 'Heater' %}
        {{ line('Heater', (e.equipment_item ~ ((' · ' ~ e.model_no) if e.model_no else '')), e.rating or '') }}
      {% endfor %}
      {% for e in doc.design_equipment if e.equipment_class == 'Chemical Controller' %}
        {{ line('Chem control', (e.equipment_item ~ ((' · ' ~ e.model_no) if e.model_no else ''))) }}
      {% endfor %}
      {% for e in doc.design_equipment if e.equipment_class == 'Chemical Feed Pump' %}
        {{ line('Feeder', (e.equipment_item ~ ((' · ' ~ e.model_no) if e.model_no else '')), (('×' ~ (e.qty|string)) if (e.qty and e.qty > 1) else '')) }}
      {% endfor %}
      {% if doc.chlorinator_feed_gph %}{{ line('Chlorinator', ('Feed at ' ~ (("%g"|format(doc.chem_chlorine_pct)) if doc.chem_chlorine_pct else '10') ~ '% chlorine'), ("%.2f"|format(doc.chlorinator_feed_gph) ~ ' gal/hr')) }}{% endif %}
    </table>
  </div>
  {% endif %}

  {% set meters = doc.design_equipment | selectattr('equipment_class', 'equalto', 'Gauge / Meter') | list %}
  {% if meters %}
  {{ sechead('6', 'Instrumentation') }}
  <div style="border-left:3px solid #a6b4bd; padding-left:14px; margin-bottom:6px;">
    <table style="width:100%; border-collapse:collapse;">
      {% for e in meters %}
        {{ line('Meter / gauge', (e.equipment_item ~ ((' · ' ~ e.model_no) if e.model_no else '')), e.rating or '') }}
      {% endfor %}
    </table>
  </div>
  {% endif %}

  {{ sechead('7', 'Code Compliance') }}
  <table style="width:100%; border-collapse:collapse; font-size:11px;">
    <tr>
      <td style="width:33%; vertical-align:top; padding:8px 10px; background:#eef6f0; border:1px solid #d5e6db;">
        <div style="font:bold 10px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; color:#2f8557;">&#10003; Anti-entrapment</div>
        <div style="color:#4a5a63; margin-top:3px;">{{ doc.main_drain_status or 'Dual-drain, VGB &mdash; &le; 1.5 ft/sec with one drain blocked' }}</div>
      </td>
      <td style="width:33%; vertical-align:top; padding:8px 10px; background:#eef6f0; border:1px solid #d5e6db;">
        <div style="font:bold 10px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; color:#2f8557;">&#10003; Vessel listing</div>
        <div style="color:#4a5a63; margin-top:3px;">{{ doc.vessel_listing or 'Listed shell' }}</div>
      </td>
      <td style="width:34%; vertical-align:top; padding:8px 10px; background:#eef6f0; border:1px solid #d5e6db;">
        <div style="font:bold 10px 'Helvetica Neue',Arial,sans-serif; text-transform:uppercase; color:#2f8557;">&#10003; Turnover</div>
        <div style="color:#4a5a63; margin-top:3px;">{{ "%g"|format(doc.max_turnover_min) if doc.max_turnover_min else '30' }} min max{% if doc.turnover_time_min %} &middot; {{ "%.1f"|format(doc.turnover_time_min) }} min at published flow{% endif %}</div>
      </td>
    </tr>
  </table>

  {% if tb.preliminary %}
  <div style="margin-top:20px; padding-top:10px; border-top:1px solid #cbd6dd; font-size:10px; color:#8794a0;">
    <span style="color:#8f5a1d; font-weight:bold;">PRELIMINARY &mdash; not for construction; sealed by P.E. before issue.</span>
    &nbsp;{{ tb.contractor.name }} &middot; Water Engineering.
  </div>
  {% endif %}
</div>
""".strip()

# --- Control Panel Submittal (DOC-0126, verbatim section wording) -----------
SUBMITTAL_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  <h2 style="margin:0 0 12px; font-weight:bold;">{{ doc.product_family or '(Sapphire product &amp; family name)' }} &ndash; NEMA {{ doc.nema_rating or '(rating)' }} Fountain control panel for {{ doc.project or '(project name)' }}</h2>

  <h3 style="margin:14px 0 4px; font-size:13px;">User Interface</h3>
  {% set ns = namespace(screens=[]) %}
  {% if doc.screen_main %}{% set ns.screens = ns.screens + ['Main'] %}{% endif %}
  {% if doc.screen_run %}{% set ns.screens = ns.screens + ['Run'] %}{% endif %}
  {% if doc.screen_maintenance %}{% set ns.screens = ns.screens + ['Maintenance'] %}{% endif %}
  {% if doc.screen_status %}{% set ns.screens = ns.screens + ['Status'] %}{% endif %}
  <p style="margin:0;">Standard touchscreen with {{ ns.screens|join(', ') }} screens (Add additional screens as requested by customer).{% if doc.additional_screens %} Additional: {{ doc.additional_screens }}.{% endif %}</p>

  <h3 style="margin:14px 0 4px; font-size:13px;">Pump Control &amp; Pumps</h3>
  <p style="margin:0 0 4px; color:#555;">Panel {{ 'will' if doc.panel_includes_contactors_overload else 'will not' }} include motor contactors and overload protection.</p>
  <ul style="margin:0; padding-left:18px;">
    {% for p in doc.pumps %}<li>{{ p.phase or '?' }} phase {{ p.control_method }} for {{ p.function }} Pump; {{ p.voltage }}{{ p.voltage_type }}, {{ p.hp }}HP, {{ p.phase }} phase{% if p.qty and p.qty > 1 %} (x{{ p.qty }}){% endif %}</li>{% endfor %}
  </ul>

  <h3 style="margin:14px 0 4px; font-size:13px;">Inputs</h3>
  <p style="margin:0 0 4px; color:#555;">All inputs will be {{ doc.input_voltage_default or '24 VDC' }}.</p>
  <ul style="margin:0; padding-left:18px;">
    {% for io in doc.io_points %}{% if io.io_type == 'Input' %}<li>{{ io.qty or 1 }} Input{{ 's' if (io.qty or 1) > 1 else '' }} for {{ io.point_name }}{% if io.device %} ({{ io.device }}){% endif %}</li>{% endif %}{% endfor %}
  </ul>

  <h3 style="margin:14px 0 4px; font-size:13px;">Solenoid Valves</h3>
  <p style="margin:0; color:#555;">All solenoid valves will be {{ doc.solenoid_voltage_default or '24 VAC' }}. Each solenoid valve will be individually controlled by a solid-state relay.</p>
  <ul style="margin:4px 0 0; padding-left:18px;"><li>{{ doc.solenoid_valve_qty or 0 }} Solenoid valves ({{ doc.solenoid_relay_count or 0 }} relays)</li></ul>

  <h3 style="margin:14px 0 4px; font-size:13px;">Lights</h3>
  <p style="margin:0; color:#555;">Power for lights will be {{ doc.lighting_voltage }} VDC. A single fused solid-state relay will power up to {{ doc.per_relay_watts }}W at {{ doc.lighting_voltage }}VDC.</p>
  <ul style="margin:4px 0 0; padding-left:18px;">
    {% for l in doc.lights %}<li>{{ l.qty }}, {{ l.part_no_description }}{% if l.watts_each %} ({{ l.watts_each }}W){% endif %}</li>{% endfor %}
  </ul>

  <h3 style="margin:14px 0 4px; font-size:13px;">Interlocks</h3>
  <ul style="margin:0; padding-left:18px;">
    {% for il in doc.interlocks %}{% if il.enabled %}<li>{{ il.action }} &mdash; {{ il.condition }}{% if il.threshold %} ({{ il.threshold }}){% endif %}</li>{% endif %}{% endfor %}
  </ul>

  {% if doc.theory_of_operation %}
  <h3 style="margin:14px 0 4px; font-size:13px;">Theory of Operation</h3>
  <div style="margin:0; white-space:pre-wrap;">{{ doc.theory_of_operation }}</div>
  {% endif %}

  {% if doc.representative_image %}
  <h3 style="margin:14px 0 4px; font-size:13px;">Controller Representative Image</h3>
  <img src="{{ doc.representative_image }}" style="max-width:100%; border:1px solid #ddd;">
  {% endif %}
</div>
""".strip()


PANEL_SCHEDULE_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; font-size:12px;">
  {% set sched = we_panel_schedule(doc) %}
  <h2 style="margin:0 0 4px; font-weight:bold;">Panel Schedule &ndash; {{ doc.name }}</h2>
  <p style="margin:0 0 12px; color:#555;">{{ doc.product_family or '' }}{% if doc.project %} &middot; {{ doc.project }}{% endif %} &middot; NEC 430.24 / 430.62 / Art. 450 (engineering aid &mdash; confirm with the engineer of record).</p>

  <h3 style="margin:10px 0 4px; font-size:13px;">Service</h3>
  <table style="width:100%; border-collapse:collapse; font-size:11px;">
    <tr>
      <td style="padding:3px 6px; border:1px solid #ddd; color:#555;">Main line</td><td style="padding:3px 6px; border:1px solid #ddd;">{{ sched.service.main_line_voltage or '?' }} V &middot; {{ sched.service.phase or '?' }} phase &middot; {{ sched.service.frequency_hz or 60 }} Hz</td>
      <td style="padding:3px 6px; border:1px solid #ddd; color:#555;">Connected load (430.24)</td><td style="padding:3px 6px; border:1px solid #ddd;">{{ sched.connected_load_a or '&mdash;' }} A</td>
    </tr>
    <tr>
      <td style="padding:3px 6px; border:1px solid #ddd; color:#555;">Main breaker (430.62)</td><td style="padding:3px 6px; border:1px solid #ddd;">{{ sched.main_breaker_a or '&mdash;' }} A</td>
      <td style="padding:3px 6px; border:1px solid #ddd; color:#555;">Control transformer</td><td style="padding:3px 6px; border:1px solid #ddd;">{{ sched.control_transformer_va or '&mdash;' }} VA</td>
    </tr>
  </table>

  <h3 style="margin:14px 0 4px; font-size:13px;">Branch Circuits</h3>
  <table style="width:100%; border-collapse:collapse; font-size:11px;">
    <thead><tr style="background:#f4f4f4;">
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:left;">Tag</th>
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:left;">Description</th>
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:right;">FLA (A)</th>
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:left;">V / phase</th>
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:left;">Control</th>
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:right;">Breaker (A)</th>
    </tr></thead>
    <tbody>
      {% for c in sched.circuits %}
      <tr>
        <td style="padding:3px 6px; border:1px solid #ddd;">{{ c.tag }}</td>
        <td style="padding:3px 6px; border:1px solid #ddd;">{{ c.description }}</td>
        <td style="padding:3px 6px; border:1px solid #ddd; text-align:right;">{{ c.load_a or '&mdash;' }}</td>
        <td style="padding:3px 6px; border:1px solid #ddd;">{{ c.voltage }} / {{ c.phase }}&phi;</td>
        <td style="padding:3px 6px; border:1px solid #ddd;">{{ c.control_method }}</td>
        <td style="padding:3px 6px; border:1px solid #ddd; text-align:right;">{{ c.breaker_a or '&mdash;' }}</td>
      </tr>
      {% endfor %}
      {% if not sched.circuits %}<tr><td colspan="6" style="padding:6px; border:1px solid #ddd; color:#999;">No pump loads entered.</td></tr>{% endif %}
    </tbody>
  </table>
  <p style="margin:10px 0 0; color:#999; font-size:10px;">Branch breakers round UP to the next standard size (NEC 240.6); the feeder/main rounds DOWN (NEC 430.62). Motor FLA uses the NEC 430.248/250 table value unless a nameplate FLA is entered. Code-based design aid for the engineer of record to confirm &mdash; not a stamped calculation.</p>
</div>
""".strip()


# --------------------------------------------------------------------------
# Submittal packet — the assembled health-department plan set. Composed from a
# cover (title block + PRELIMINARY watermark), the spa-data + schedules, the
# auto-generated circulation/one-line schematics, the standard notes, and the
# calculation audit, each on its own page. The packet is a DRAFT for a P.E. to
# seal (see packet.py); the per-sheet banner + cover marking say so.
# --------------------------------------------------------------------------

_PACKET_BANNER = (
    "{% if we_title_block(doc.name).preliminary %}"
    '<div style="text-align:center; color:#b00; font-size:9px; font-weight:bold; '
    'letter-spacing:1px; border-bottom:1px solid #f0c0c0; margin-bottom:8px;">'
    "PRELIMINARY &mdash; NOT FOR CONSTRUCTION &mdash; FOR P.E. REVIEW</div>{% endif %}"
)

PACKET_COVER_HTML = """
<div style="font-family:'Helvetica Neue',Arial,sans-serif; color:#222; page-break-after:always;">
  {% set tb = we_title_block(doc.name) %}
  <div style="text-align:center; border-bottom:3px solid #222; padding-bottom:12px; margin-bottom:16px;">
    <div style="font-size:22px; font-weight:bold;">{{ tb.project.name }}</div>
    <div style="font-size:13px; color:#555;">Health Department Submittal{% if tb.governing_code %} &middot; {{ tb.governing_code }}{% endif %}</div>
    {% if tb.preliminary %}<div style="color:#b00; font-weight:bold; letter-spacing:2px; margin-top:6px;">PRELIMINARY &mdash; NOT FOR CONSTRUCTION</div>{% endif %}
  </div>
  <table style="width:100%; font-size:12px; border-collapse:collapse;">
    <tr>
      <td style="vertical-align:top; width:33%; padding:6px;">
        <div style="font-weight:bold; color:#888; font-size:10px;">ENGINEER</div>
        <div>{{ tb.engineer.name or '(engineer of record)' }}</div>
        {% if tb.engineer.license %}<div>P.E. {{ tb.engineer.license }}</div>{% endif %}
        <div style="white-space:pre-wrap;">{{ tb.engineer.address or '' }}</div>
        <div>{{ tb.engineer.phone or '' }}</div>
        <div style="margin-top:8px; width:120px; height:120px; border:1px dashed #bbb; text-align:center; line-height:120px; color:#bbb; font-size:10px;">
          {% if tb.engineer.stamp and not tb.preliminary %}<img src="{{ tb.engineer.stamp }}" style="max-width:118px; max-height:118px; vertical-align:middle;">{% else %}P.E. STAMP{% endif %}
        </div>
      </td>
      <td style="vertical-align:top; width:33%; padding:6px;">
        <div style="font-weight:bold; color:#888; font-size:10px;">CONTRACTOR</div>
        <div>{{ tb.contractor.name }}</div>
        <div style="white-space:pre-wrap;">{{ tb.contractor.address or '' }}</div>
        <div>{{ tb.contractor.phone or '' }}</div>
        <div style="font-weight:bold; color:#888; font-size:10px; margin-top:12px;">PROJECT</div>
        <div>{{ tb.project.name }}</div>
        {% if tb.project.customer %}<div>{{ tb.project.customer }}</div>{% endif %}
      </td>
      <td style="vertical-align:top; width:33%; padding:6px;">
        <div style="font-weight:bold; color:#888; font-size:10px;">ISSUE</div>
        <div>Issue Date: {{ tb.issue_date or '________' }}</div>
        <div>Drawn By: {{ tb.drawn_by or '____' }}</div>
        {% if tb.venue_type %}<div>Venue: {{ tb.venue_type }}</div>{% endif %}
        <div style="font-weight:bold; color:#888; font-size:10px; margin-top:12px;">SHEET INDEX</div>
        <div>Cover &middot; Spa Data &amp; Schedules &middot; Schematics &middot; Standard Notes &middot; Design Calculations</div>
      </td>
    </tr>
  </table>
  <div style="margin-top:16px;">
    <div style="font-weight:bold; color:#888; font-size:10px;">REVISIONS</div>
    <table style="width:100%; border-collapse:collapse; font-size:11px;">
      <tr style="background:#f4f4f4;"><th style="border:1px solid #ddd; padding:3px 6px; text-align:left;">Mark</th><th style="border:1px solid #ddd; padding:3px 6px; text-align:left;">Date</th><th style="border:1px solid #ddd; padding:3px 6px; text-align:left;">Description</th></tr>
      {% for r in tb.revisions %}<tr><td style="border:1px solid #ddd; padding:3px 6px;">{{ r.mark }}</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ r.date }}</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ r.description }}</td></tr>{% endfor %}
      {% if not tb.revisions %}<tr><td colspan="3" style="border:1px solid #ddd; padding:3px 6px; color:#999;">No revisions.</td></tr>{% endif %}
    </table>
  </div>
</div>
""".strip()

PACKET_SPA_DATA_HTML = """
<h3 style="margin:0 0 6px; font-size:14px;">Spa / Pool Data</h3>
<table style="width:100%; border-collapse:collapse; font-size:11px; margin-bottom:14px;">
  <tr><td style="border:1px solid #ddd; padding:3px 6px; color:#555; width:20%;">Volume</td><td style="border:1px solid #ddd; padding:3px 6px; width:30%;">{{ doc.total_basin_gallons or '&mdash;' }} gal</td>
      <td style="border:1px solid #ddd; padding:3px 6px; color:#555; width:20%;">Design Flow</td><td style="border:1px solid #ddd; padding:3px 6px; width:30%;">{{ doc.design_flow_gpm or '&mdash;' }} GPM</td></tr>
  <tr><td style="border:1px solid #ddd; padding:3px 6px; color:#555;">Turnover</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ doc.turnover_time_min or '&mdash;' }} min</td>
      <td style="border:1px solid #ddd; padding:3px 6px; color:#555;">Minimum Flow</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ doc.minimum_flow_gpm or '&mdash;' }} GPM</td></tr>
  <tr><td style="border:1px solid #ddd; padding:3px 6px; color:#555;">Bather Load</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ doc.bather_load or '&mdash;' }}</td>
      <td style="border:1px solid #ddd; padding:3px 6px; color:#555;">Skimmers</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ doc.skimmer_count or '&mdash;' }}</td></tr>
  <tr><td style="border:1px solid #ddd; padding:3px 6px; color:#555;">TDH</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ doc.computed_tdh_ft or '&mdash;' }} ft</td>
      <td style="border:1px solid #ddd; padding:3px 6px; color:#555;">Main Drain (VGB)</td><td style="border:1px solid #ddd; padding:3px 6px;">{{ doc.main_drain_status or '&mdash;' }}</td></tr>
</table>
""".strip()

PACKET_EQUIPMENT_HTML = """
{% if doc.design_equipment %}
<h3 style="margin:0 0 6px; font-size:14px;">Circulation Equipment Schedule</h3>
<table style="width:100%; border-collapse:collapse; font-size:11px; margin-bottom:14px;">
  <thead><tr style="background:#f4f4f4;">
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:left;">Mark</th>
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:left;">Class</th>
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:left;">Equipment / Model</th>
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:right;">Qty</th>
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:left;">Rating</th>
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:left;">Electrical</th>
    <th style="border:1px solid #ddd; padding:4px 6px; text-align:left;">Check</th>
  </tr></thead>
  <tbody>
    {% for e in doc.design_equipment %}
    <tr>
      <td style="border:1px solid #ddd; padding:3px 6px;">{{ loop.index }}</td>
      <td style="border:1px solid #ddd; padding:3px 6px;">{{ e.equipment_class or '' }}</td>
      <td style="border:1px solid #ddd; padding:3px 6px;">{{ e.equipment_item }}{% if e.model_no %} &middot; {{ e.model_no }}{% endif %}</td>
      <td style="border:1px solid #ddd; padding:3px 6px; text-align:right;">{{ e.qty or 1 }}</td>
      <td style="border:1px solid #ddd; padding:3px 6px;">{{ e.rating or '' }}</td>
      <td style="border:1px solid #ddd; padding:3px 6px;">{{ e.electrical or '' }}</td>
      <td style="border:1px solid #ddd; padding:3px 6px;{% if e.note and e.note != 'OK' %} color:#b00;{% endif %}">{{ e.note or '' }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
""".strip()

PACKET_SCHEMATICS_HTML = """
<h3 style="margin:0 0 8px; font-size:14px;">Circulation Equipment Schematic (SP-3)</h3>
<div style="overflow-x:auto;">{{ we_circulation_schematic(doc.name) | safe }}</div>
<h3 style="margin:20px 0 8px; font-size:14px;">Electrical One-Line (SP-6)</h3>
<div style="overflow-x:auto;">{{ we_electrical_oneline(doc.name) | safe }}</div>
<p style="margin:12px 0 0; color:#999; font-size:10px;">Schematics are generated from the design data; the plan-specific site/piping CAD drawings are attached separately.</p>
""".strip()

PACKET_DETAILS_HTML = """
<h3 style="margin:0 0 8px; font-size:14px;">Standard Details (SP-4)</h3>
{% set details = we_standard_details(doc.governing_code) %}
{% if not details %}<p style="color:#999; font-size:11px;">No standard details are configured yet (add them under Standard Detail).</p>{% endif %}
<div style="display:flex; flex-wrap:wrap; gap:16px;">
{% for d in details %}
  <div style="width:46%; min-width:270px;">
    <div style="font-weight:bold; font-size:11px; margin-bottom:4px;">{{ d.title or d.detail_key }}</div>
    <div style="border:1px solid #e2e8f0; padding:6px; overflow-x:auto;">{{ d.svg | safe }}</div>
  </div>
{% endfor %}
</div>
""".strip()

PACKET_NOTES_HTML = """
<h3 style="margin:0 0 8px; font-size:14px;">Standard Notes</h3>
{% set notes = we_standard_notes(doc.governing_code) %}
{% if not notes %}<p style="color:#999; font-size:11px;">No standard notes are configured yet (add them under Standard Note, keyed to the governing code).</p>{% endif %}
{% set ns = namespace(cat='') %}
{% for n in notes %}
  {% if n.category != ns.cat %}<div style="font-weight:bold; color:#888; font-size:10px; margin:10px 0 4px; text-transform:uppercase;">{{ n.category }}</div>{% set ns.cat = n.category %}{% endif %}
  <p style="margin:0 0 6px; font-size:11px;">{% if n.title %}<b>{{ n.title }}.</b> {% endif %}{{ n.body }}</p>
{% endfor %}
""".strip()


def _packet_sheet(html):
    return (
        '<div style="page-break-after:always; font-family:Helvetica,Arial,sans-serif; color:#222;">'
        + _PACKET_BANNER + html + "</div>"
    )


def _packet_html():
    """Compose the master packet from the cover + the existing/new sheet bodies."""
    return (
        PACKET_COVER_HTML
        + _packet_sheet(PACKET_SPA_DATA_HTML + PACKET_EQUIPMENT_HTML + SCHEDULES_HTML)
        + _packet_sheet(PACKET_SCHEMATICS_HTML)
        + _packet_sheet(PACKET_DETAILS_HTML)
        + _packet_sheet(PACKET_NOTES_HTML)
        + _packet_sheet(AUDIT_HTML)
    )


def _upsert_print_format(name, html, doc_type=DOCTYPE, pdf_generator=None):
    """Create or update one custom Jinja Print Format for the given doctype.

    ``pdf_generator`` pins the backend for that format when given (the multi-page
    packet renders with the chrome generator, which handles the sheet breaks +
    inline SVG the wkhtmltopdf build historically choked on)."""
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
    if pdf_generator and pf.meta.has_field("pdf_generator"):
        pf.pdf_generator = pdf_generator
    pf.save(ignore_permissions=True)


def ensure_water_print_formats():
    """after_migrate entry: ship the Water Feature Design (Results, Calculation
    Audit, Schedules, Design Calculations, Submittal Packet) + Control Panel
    Design (Submittal, Panel Schedule) Print Formats. Idempotent (upserts the
    HTML) and guarded (a failure only logs)."""
    try:
        if frappe.db.exists("DocType", DOCTYPE):
            _upsert_print_format(RESULTS_PF, RESULTS_HTML)
            _upsert_print_format(AUDIT_PF, AUDIT_HTML)
            _upsert_print_format(SCHEDULES_PF, SCHEDULES_HTML)
            _upsert_print_format(DESIGN_CALC_PF, DESIGN_CALC_HTML)
            _upsert_print_format(PACKET_PF, _packet_html(), pdf_generator="chrome")
        if frappe.db.exists("DocType", CONTROL_DOCTYPE):
            _upsert_print_format(SUBMITTAL_PF, SUBMITTAL_HTML, doc_type=CONTROL_DOCTYPE)
            _upsert_print_format(PANEL_SCHEDULE_PF, PANEL_SCHEDULE_HTML, doc_type=CONTROL_DOCTYPE)
        frappe.db.commit()
        frappe.logger().info("[water_engineering] ensured Water Engineering print formats")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water Engineering print formats")

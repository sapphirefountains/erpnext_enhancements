"""after_migrate setup for the Water Engineering Print Formats.

**Water Feature Design** (schema 2, Fountain Designer phase G, v1.770.0). Nikolas asked for "a printable
report view and other views with design information for printing and emailing" that match the other
print formats, so every format is the print design system's chrome (``print_style``, the Design pillar:
violet stripe, letterhead, eyebrow, a facts row of customer, project and site, ruled tables, the running
line and closing stripe) around one Jinja block macro (:data:`BLOCK_MACRO`). Each reads one context,
``we_print(doc, sheets)`` (``packet.we_print`` -> ``prints.build``), built from the stored ``model_json``,
``results_json`` and ``bom`` rows: nothing recomputes the engine, and no template formats a number.
Letter size, a page break between sheets.

* **Design Report** (the doctype's default): the client-facing summary.
* **Engineering Calculations**: every ``CalcResult`` by ladder section, then the plan check (the Utah
  R392-302-41 and NEC 680 checklists).
* **Schedules**: equipment, piping, fittings and cut list, structural takeoff, electrical, controls BOM.
* **Design Drawings**, **Budget Proposal**, **Operations and Winterization**.
* **Submittal Packet**: the cover and title block, then the basis, schedules, electrical, structural,
  drawings, plan check, calculations, standard details and notes (never the budget or the O&M).

The formats v1.768.0 and earlier seeded (``prints.SUPERSEDED``) read the retired child tables; they are
disabled here, not deleted, so a site keeps its history and nobody prints an empty one.

**Control Panel Design**: Submittal and Panel Schedule, unchanged.

Created idempotently on every migrate (Frappe Cloud has no ``bench`` shell), and guarded so a
hiccup only logs. Re-upserting the HTML means template edits deploy on the next migrate.
"""

import frappe

from erpnext_enhancements import print_style as ps
from erpnext_enhancements.water_engineering import prints

DOCTYPE = prints.DOCTYPE
CONTROL_DOCTYPE = "Control Panel Design"
MODULE = "Water Engineering"
SUBMITTAL_PF = "Control Panel Design - Submittal"
PANEL_SCHEDULE_PF = "Control Panel Design - Panel Schedule"
PACKET_PF = prints.PF_PACKET

# --- the block macro --------------------------------------------------------
# One macro draws every block prints.build returns. A table with no rows prints its `empty`
# sentence, never a header over nothing. frappe's Jinja has no autoescape, so every piece of
# text is escaped here with `|e` (prints.py hands over plain Unicode, so an ampersand prints once).
# frappe renders with DebugUndefined, which PRINTS a missing name with its braces, so every value
# printed here is a key prints.py always sets; optional keys are only ever tested in an `if`.
_H3 = f"margin:12px 0 4px;font-size:13px;font-weight:700;color:{ps.DEEP_SEA_BLUE};page-break-after:avoid"
_HEADING = (f"margin:18px 0 6px;padding-top:6px;border-top:2px solid {ps.DEEP_SEA_BLUE};font-size:14px;font-weight:700;"
            f"color:{ps.DEEP_SEA_BLUE};page-break-after:avoid")
_NOTE = f"margin:2px 0 10px;font-size:10.5px;color:{prints.MUTED}"
_EMPTY = f"margin:2px 0 10px;font-size:11.5px;font-style:italic;color:{prints.MUTED}"
_TABLE = "width:100%;border-collapse:collapse;margin:0 0 4px;font-size:11.5px"
_FACT_LABEL = f"{ps.TD};width:34%;{ps.LABEL}"
_MONO = "font-family:'SF Mono',Consolas,monospace;font-size:10.5px"

BLOCK_MACRO = (
    "{%- macro we_table(t) -%}"
    "{%- if t.rows -%}"
    '<table style="' + _TABLE + '"><thead><tr>'
    '{%- for c in t.columns %}<th style="{{ c.style }}">{{ c.label|e }}</th>{% endfor -%}'
    "</tr></thead><tbody>"
    "{%- for r in t.rows -%}"
    '{%- if r.group %}<tr><td colspan="{{ r.span }}" style="{{ r.style }}">{{ r.group|e }}</td></tr>'
    "{%- else %}<tr>{% for c in r.cells %}"
    '<td{% if c.cls %} class="{{ c.cls|e }}"{% endif %} style="{{ c.style }}">{% if c.href %}<a href="{{ c.href|e }}">{{ c.text|e }}</a>{% else %}{{ c.text|e }}{% endif %}</td>'
    "{% endfor %}</tr>{% endif -%}"
    "{%- endfor -%}</tbody>"
    '{%- if t.foot %}<tfoot>{% for r in t.foot %}<tr>{% for c in r %}<td style="{{ c.style }}">{{ c.text|e }}</td>'
    "{% endfor %}</tr>{% endfor %}</tfoot>{% endif -%}"
    "</table>"
    '{%- elif t.empty %}<p style="' + _EMPTY + '">{{ t.empty|e }}</p>{% endif -%}'
    "{%- endmacro -%}"
    "{%- macro we_block(b) -%}"
    "{%- if b.kind == 'heading' -%}"
    '<h3 style="' + _HEADING + '">{{ b.title|e }}</h3>'
    "{%- elif b.title %}" '<h3 style="' + _H3 + '">{{ b.title|e }}</h3>{% endif -%}'
    "{%- if b.kind == 'break' -%}"
    '<div style="page-break-after:always"></div>'
    "{%- elif b.kind == 'table' -%}{{ we_table(b) }}"
    "{%- elif b.kind == 'facts' -%}"
    '{%- if b.lines %}<table style="' + _TABLE + '">{% for f in b.lines %}<tr><td style="' + _FACT_LABEL + '">{{ f.label|e }}</td>'
    '<td style="' + ps.TD + ';{{ f.style }}">{{ f.value|e }}</td></tr>{% endfor %}</table>'
    '{%- else %}<p style="' + _EMPTY + '">{{ b.empty|e }}</p>{% endif -%}'
    "{%- elif b.kind == 'drawing' -%}"
    '{%- if b.svg %}<div class="we-drawing" style="page-break-inside:avoid;margin:0 0 8px;text-align:center">{{ b.svg }}</div>'
    '{%- else %}<p style="' + _EMPTY + '">{{ b.empty|e }}</p>{% endif -%}'
    "{%- elif b.kind == 'list' -%}"
    '{%- if b.entries %}<table style="' + _TABLE + '">{% for i in b.entries %}<tr>'
    '<td style="' + ps.TD + ';width:22px">{% if b.boxes %}&#9744;{% else %}{{ loop.index }}.{% endif %}</td>'
    '<td style="' + ps.TD + '">{{ i|e }}</td></tr>{% endfor %}</table>'
    '{%- else %}<p style="' + _EMPTY + '">{{ b.empty|e }}</p>{% endif -%}'
    "{%- elif b.kind == 'text' -%}"
    '<pre style="margin:0 0 4px;padding:8px 10px;white-space:pre-wrap;border:1px solid ' + ps.BORDER_100 + ";" + _MONO + '">{{ b.body|e }}</pre>'
    "{%- elif b.kind == 'calc' -%}"
    '<div style="margin:0 0 8px;page-break-inside:avoid">'
    '<div style="' + ps.STRONG + '">{{ b.value|e }}{% if b.status %} &middot; {{ b.status|e }}{% endif %}</div>'
    '{%- if b.formula %}<div style="margin-top:3px"><span style="' + ps.LABEL + '">FORMULA</span> '
    '<span class="we-working" style="' + _MONO + '">{{ b.formula|e }}</span></div>{% endif -%}'
    "{{ we_table(b.inputs) }}"
    '{%- if b.steps %}<pre class="we-working" style="margin:2px 0;white-space:pre-wrap;' + _MONO + '">{{ b.steps|join("\\n")|e }}</pre>{% endif -%}'
    '{%- if b.citations %}<div class="we-source" style="' + _NOTE + '">Source: {{ b.citations|join("; ")|e }}</div>{% endif -%}'
    '{%- if b.warnings %}<div style="' + ps.FAIL + ';font-size:11px">{{ b.warnings|join(" ")|e }}</div>{% endif -%}'
    "</div>"
    "{%- elif b.kind == 'note' -%}"
    '<p style="' + _NOTE + '">{{ b.text|e }}</p>'
    "{%- endif -%}"
    '{%- if b.note %}<p style="' + _NOTE + '">{{ b.note|e }}</p>{% endif -%}'
    "{%- endmacro -%}"
)

# Letter paper; drawings carry a viewBox, so this sizes them to the sheet.
_PAGE_CSS = "<style>@page{size:letter}.we-drawing svg{width:100%;height:auto;max-height:600px}</style>\n"
_PRELIMINARY = (
    "{% if we_title_block(doc.name).preliminary %}"
    f'<div style="text-align:center;color:{ps.RED};font-size:9px;font-weight:700;letter-spacing:1px;'
    f'border-bottom:1px solid {ps.BORDER_100};padding-bottom:4px;margin-bottom:8px">'
    "PRELIMINARY &mdash; NOT FOR CONSTRUCTION &mdash; FOR P.E. REVIEW</div>{% endif %}"
)
# The meta block beside the title: the design, its date (issue date, else last saved) and the red
# DRAFT a document that is not issued carries (ps_state).
_META = (
    "{% for m in p.meta %}<div>{{ m|e }}</div>{% endfor %}"
    "<div>{{ ps_date(doc.issue_date or doc.modified) }}</div>"
    "{{ ps_state(doc.docstatus) }}"
)
# The facts row: the customer's party block, the project and site, the designer and status.
_FACTS = (
    ps.facts_open()
    + ps.fact("CUSTOMER", "{% if doc.customer %}{{ ps_party(doc) }}{% else %}&mdash;{% endif %}", width="40%")
    + ps.fact("PROJECT AND SITE", "{{ p.facts.project|e }}<br>{{ p.facts.site|e }}", width="32%")
    + ps.fact("DESIGNER", "{{ p.facts.designer|e }}<br>Status: {{ p.facts.status|e }}", width="28%")
    + ps.facts_close()
)


def _sheets_body(sheets, headed=True):
    """Each sheet's blocks, under the sheet's heading when ``headed``; every sheet after the first
    starts a page."""
    keys = "[" + ", ".join(f"'{k}'" for k in sheets) + "]"
    head = ps.section_title("{{ sh.title|e }}") if headed else ""
    return (
        "{% for key in " + keys + " %}{% set sh = p.sheets[key] %}"
        + '<div{% if not loop.first %} style="page-break-before:always"{% endif %}>'
        + head
        + "{% for b in sh.blocks %}{{ we_block(b) }}{% endfor %}</div>{% endfor %}"
    )


def _format_html(print_format):
    """One Water Feature Design format: chrome and facts row, then its sheets from ``prints.FORMATS``."""
    eyebrow, sheets = prints.FORMATS[print_format]
    keys = "[" + ", ".join(f"'{k}'" for k in sheets) + "]"
    preliminary = _PRELIMINARY if "calcs" in sheets else ""
    return (
        "{%- set p = we_print(doc, " + keys + ") -%}"
        + BLOCK_MACRO
        + _PAGE_CSS
        + ps.page_open(prints.PILLAR)
        + preliminary
        + ps.letterhead(prints.PILLAR, eyebrow, "{{ p.title|e }}", _META)
        + _FACTS
        + _sheets_body(sheets, headed=len(sheets) > 1)
        + ps.page_close(prints.PILLAR)
    )


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
      <th style="padding:4px 6px; border:1px solid #ddd; text-align:right;">Max breaker (A)</th>
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
  <p style="margin:10px 0 0; color:#999; font-size:10px;">Max breaker is the largest motor branch breaker NEC 430.52 allows (250% of FLC for an inverse-time breaker, next standard size up per 240.6); a smaller breaker that carries the pump's starting current is permitted, and conductors are sized at 125% of FLC (430.22). The feeder/main rounds DOWN (NEC 430.62). Motor FLA uses the NEC 430.248/250 table value unless a nameplate FLA is entered. Code-based design aid for the engineer of record to confirm &mdash; not a stamped calculation.</p>
</div>
""".strip()


# --------------------------------------------------------------------------
# Submittal packet: the assembled health-department plan set. A cover (title block,
# PRELIMINARY marking, revisions), then the model-2 sheets prints.PACKET_SHEETS names,
# the standard details and the standard notes, each on its own page. The packet is a
# DRAFT for a P.E. to seal (see packet.py); the per-sheet banner and the cover say so.
# --------------------------------------------------------------------------

# `preliminary` and `tb` (the title block) are set once at the top of the packet (_packet_html): each
# sheet repeats the banner, and we_title_block reads Settings and the Project.
_PACKET_BANNER = (
    "{% if preliminary %}"
    f'<div style="text-align:center;color:{ps.RED};font-size:9px;font-weight:700;letter-spacing:1px;'
    f'border-bottom:1px solid {ps.BORDER_100};padding-bottom:4px;margin-bottom:8px">'
    "PRELIMINARY &mdash; NOT FOR CONSTRUCTION &mdash; FOR P.E. REVIEW</div>{% endif %}"
)

_COVER_META = (
    "{% if tb.governing_code %}<div>{{ tb.governing_code|e }}</div>{% endif %}"
    "{% if p.codes %}<div>{{ p.codes|e }}</div>{% endif %}"
    "<div>Issued {{ ps_date(tb.issue_date) if tb.issue_date else '________' }}</div>"
    "<div>Drawn by {{ (tb.drawn_by or '____')|e }}</div>"
    "{% if tb.preliminary %}"
    f'<div style="{ps.FAIL};letter-spacing:1px">PRELIMINARY &mdash; NOT FOR CONSTRUCTION</div>'
    "{% endif %}"
)
def _lines(expr):
    """A multi-line text field as escaped lines, each after a break."""
    return "{% for line in (" + expr + " or '').split('\\n') %}{% if line %}<br>{{ line|e }}{% endif %}{% endfor %}"
_STAMP = (
    f'<div style="width:130px;height:130px;border:1px dashed {ps.BORDER_100};text-align:center;line-height:130px;'
    f'font-size:10px;color:{prints.MUTED}">'
    "{% if tb.engineer.stamp and not tb.preliminary %}"
    '<img src="{{ tb.engineer.stamp|e }}" style="max-width:128px;max-height:128px;vertical-align:middle">'
    "{% else %}P.E. STAMP{% endif %}</div>"
)
_SHEET_INDEX = (
    "Cover",
    "Design basis",
    "Equipment, piping and fitting schedules",
    "Electrical",
    "Structural takeoff",
    "Design drawings",
    "Plan check",
    "Calculations by section",
    "Standard details",
    "Standard notes",
)

# The cover: the print design system's letterhead (Design pillar, HEALTH DEPARTMENT SUBMITTAL), a facts
# row of the engineer, the contractor and the project, the P.E. stamp box beside the sheet index, and the
# revisions as a ruled table.
PACKET_COVER_HTML = (
    ps.letterhead(prints.PILLAR, "HEALTH DEPARTMENT SUBMITTAL", "{{ tb.project.name|e }}", _COVER_META)
    + ps.facts_open()
    + ps.fact(
        "ENGINEER",
        "{{ (tb.engineer.name or '(engineer of record)')|e }}"
        "{% if tb.engineer.license %}<br>P.E. {{ tb.engineer.license|e }}{% endif %}"
        + _lines("tb.engineer.address")
        + "{% if tb.engineer.phone %}<br>{{ tb.engineer.phone|e }}{% endif %}",
    )
    + ps.fact(
        "CONTRACTOR",
        "{{ tb.contractor.name|e }}" + _lines("tb.contractor.address")
        + "{% if tb.contractor.phone %}<br>{{ tb.contractor.phone|e }}{% endif %}",
    )
    + ps.fact(
        "PROJECT",
        "{{ tb.project.name|e }}{% if tb.project.customer %}<br>{{ tb.project.customer|e }}{% endif %}"
        "{% if tb.venue_type %}<br>{{ tb.venue_type|e }}{% endif %}",
    )
    + ps.facts_close()
    + '<div style="display:table;width:100%;margin-top:10px">'
    + '<div style="display:table-cell;width:160px;vertical-align:top">' + _STAMP + "</div>"
    + '<div style="display:table-cell;vertical-align:top">'
    + f'<div style="{ps.LABEL}">SHEET INDEX</div>'
    + '<table style="width:100%;border-collapse:collapse;font-size:11.5px">'
    + "".join(
        f'<tr><td style="{ps.TD};width:26px">{n}</td><td style="{ps.TD}">{title}</td></tr>'
        for n, title in enumerate(_SHEET_INDEX, 1)
    )
    + "</table></div></div>"
    + ps.section_title("Revisions")
    + '<table style="width:100%;border-collapse:collapse;font-size:11.5px"><thead><tr>'
    + f'<th style="{ps.th(prints.PILLAR)}">Mark</th><th style="{ps.th(prints.PILLAR)}">Date</th>'
    + f'<th style="{ps.th(prints.PILLAR)}">Description</th></tr></thead><tbody>'
    + "{% for r in tb.revisions %}"
    + f'<tr><td style="{ps.TD}">{{{{ r.mark|e }}}}</td><td style="{ps.TD}">{{{{ ps_date(r.date) }}}}</td>'
    + f'<td style="{ps.TD}">{{{{ r.description|e }}}}</td></tr>'
    + "{% endfor %}</tbody></table>"
    + "{% if not tb.revisions %}"
    + f'<p style="margin:2px 0 10px;font-size:11.5px;font-style:italic;color:{prints.MUTED}">No revisions yet.</p>'
    + "{% endif %}"
)

# Standard details two to a row, as a table: the PDF backends lay out no flex or grid.
PACKET_DETAILS_HTML = (
    ps.section_title("Standard details (SP-4)")
    + "{% set details = we_standard_details(doc.governing_code) %}"
    + "{% if not details %}"
    + f'<p style="font-size:11.5px;font-style:italic;color:{prints.MUTED}">'
    + "No standard details are configured yet (add them under Standard Detail).</p>"
    + "{% endif %}"
    + '<table style="width:100%;border-collapse:collapse">'
    + "{% for row in details|batch(2) %}<tr>{% for d in row %}"
    + '<td style="width:50%;vertical-align:top;padding:6px !important;page-break-inside:avoid">'
    + f'<div style="{ps.LABEL};margin-bottom:4px">{{{{ (d.title or d.detail_key)|e }}}}</div>'
    + f'<div class="we-drawing" style="border:1px solid {ps.BORDER_100};padding:6px">{{{{ d.svg }}}}</div></td>'
    + "{% endfor %}</tr>{% endfor %}</table>"
)

PACKET_NOTES_HTML = (
    ps.section_title("Standard notes")
    + "{% set notes = we_standard_notes(doc.governing_code) %}"
    + "{% if not notes %}"
    + f'<p style="font-size:11.5px;font-style:italic;color:{prints.MUTED}">'
    + "No standard notes are configured yet (add them under Standard Note, keyed to the governing code).</p>"
    + "{% endif %}"
    + "{% set ns = namespace(cat='') %}"
    + "{% for n in notes %}"
    + "{% if n.category != ns.cat %}"
    + f'<div style="{ps.LABEL};margin:10px 0 4px;text-transform:uppercase">{{{{ n.category|e }}}}</div>'
    + "{% set ns.cat = n.category %}{% endif %}"
    + '<p style="margin:0 0 6px;font-size:11.5px">{% if n.title %}<b>{{ n.title|e }}.</b> {% endif %}{{ n.body|e }}</p>'
    + "{% endfor %}"
)


def _packet_sheet(key):
    """One packet sheet: a page of its own, the preliminary banner, its heading and blocks."""
    return (
        '<div style="page-break-before:always">' + _PACKET_BANNER
        + "{% set sh = p.sheets['" + key + "'] %}" + ps.section_title("{{ sh.title|e }}")
        + "{% for b in sh.blocks %}{{ we_block(b) }}{% endfor %}</div>"
    )


def _packet_html():
    """The health-department plan set: cover and title block, the model-2 sheets
    (``prints.PACKET_SHEETS``), then the standard details and notes. No budget, no O&M."""
    keys = "[" + ", ".join(f"'{k}'" for k in prints.PACKET_SHEETS) + "]"
    return (
        "{%- set p = we_print(doc, " + keys + ") -%}"
        + "{%- set tb = we_title_block(doc.name) -%}{%- set preliminary = tb.preliminary -%}"
        + BLOCK_MACRO
        + _PAGE_CSS
        + ps.page_open(prints.PILLAR)
        + PACKET_COVER_HTML
        + "".join(_packet_sheet(k) for k in prints.PACKET_SHEETS)
        + '<div style="page-break-before:always">' + _PACKET_BANNER + PACKET_DETAILS_HTML + "</div>"
        + '<div style="page-break-before:always">' + _PACKET_BANNER + PACKET_NOTES_HTML + "</div>"
        + ps.page_close(prints.PILLAR)
    )


def water_formats():
    """Every Water Feature Design Print Format this module seeds: ``{name: html}``."""
    out = {pf: _format_html(pf) for pf in prints.FORMATS}
    out[PACKET_PF] = _packet_html()
    return out


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


def set_default_report():
    """Make the Design Report the doctype's default print format, only once it exists and is enabled.

    Not a fixture: a fixture applies whether or not the format seeded, and a default naming a missing
    format breaks the form's Print. A default an admin chose is kept, unless it names a format that is
    gone, disabled, or one of ``prints.SUPERSEDED``. Never raises."""
    try:
        if not frappe.db.exists("Print Format", prints.PF_REPORT) or frappe.db.get_value(
            "Print Format", prints.PF_REPORT, "disabled"
        ):
            return
        current = frappe.db.get_value(
            "Property Setter",
            {"doc_type": DOCTYPE, "doctype_or_field": "DocType", "property": "default_print_format"},
            "value",
        )
        if current == prints.PF_REPORT:
            return
        usable = (
            current
            and current not in prints.SUPERSEDED
            and frappe.db.exists("Print Format", current)
            and not frappe.db.get_value("Print Format", current, "disabled")
        )
        if usable:
            return
        from frappe.custom.doctype.property_setter.property_setter import make_property_setter

        make_property_setter(DOCTYPE, None, "default_print_format", prints.PF_REPORT, "Data", for_doctype=True)
        frappe.clear_cache(doctype=DOCTYPE)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water Feature Design default print format")


def disable_superseded_formats():
    """Disable the formats that read the retired child tables (``prints.SUPERSEDED``). Disabled, not
    deleted: a removed seed leaves the record live, printing empty tables, and a delete would lose its
    history. Re-run every migrate, so one an admin turns back on is turned off again."""
    for name in prints.SUPERSEDED:
        try:
            if frappe.db.exists("Print Format", name) and not frappe.db.get_value("Print Format", name, "disabled"):
                frappe.db.set_value("Print Format", name, "disabled", 1, update_modified=False)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Water Engineering print format {name}")


def ensure_water_print_formats():
    """after_migrate entry: ship the Water Feature Design formats (``water_formats``) and the
    Control Panel Design Submittal and Panel Schedule. Idempotent (upserts the HTML) and guarded
    per format (a failure only logs, and the others still ship)."""
    jobs = []
    if frappe.db.exists("DocType", DOCTYPE):
        for name, html in water_formats().items():
            jobs.append((name, html, DOCTYPE, "chrome" if name == PACKET_PF else None))
    if frappe.db.exists("DocType", CONTROL_DOCTYPE):
        jobs.append((SUBMITTAL_PF, SUBMITTAL_HTML, CONTROL_DOCTYPE, None))
        jobs.append((PANEL_SCHEDULE_PF, PANEL_SCHEDULE_HTML, CONTROL_DOCTYPE, None))
    for name, html, doc_type, generator in jobs:
        try:
            _upsert_print_format(name, html, doc_type=doc_type, pdf_generator=generator)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Water Engineering print format {name}")
    disable_superseded_formats()
    set_default_report()
    try:
        frappe.db.commit()
        frappe.logger().info("[water_engineering] ensured Water Engineering print formats")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Water Engineering print formats")

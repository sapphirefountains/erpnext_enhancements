"""after_migrate setup for the Project schedule / task-list Print Formats.

Ships two branded, letterheaded formats on **Project**:

- **Project Schedule** — the task tree beside real Gantt bars, drawn with
  percentage-positioned divs (see ``print_data.py`` for why HTML/CSS and not an
  SVG or an image: a Print Format renders server-side with no JavaScript).
- **Project Task List** — the same tree as a plain indented table, for when the
  chart is noise and somebody just wants the list.

Created idempotently on every migrate (Frappe Cloud has no ``bench`` shell) and
guarded so a hiccup only logs; re-upserting the HTML means template edits deploy
on the next migrate.

**Known limitation, and it is an environment one.** Server-side PDF is
non-functional on production — both backends fail (``docs/pdf-generation.md``).
These formats' HTML print views render correctly and browser print-to-PDF works;
the desk's own "Download PDF" button will not until that runbook is executed.
That is also why the Gantt widget's own Print action goes through the browser
(``public/js/export_utils.js``'s print window) rather than through these.
"""

import frappe

DOCTYPE = "Project"
MODULE = "Project Enhancements"
SCHEDULE_PF = "Project Schedule"
TASK_LIST_PF = "Project Task List"

# Shared chrome. Kept in one string so the two formats cannot drift apart.
_BASE_CSS = """
<style>
  .sf-sched { font-family: 'Helvetica Neue', Arial, sans-serif; color: #1a1a1a; font-size: 11px; }
  .sf-sched h2 { margin: 0; font-size: 18px; }
  .sf-sched .sf-head { display: flex; justify-content: space-between;
      align-items: baseline; border-bottom: 2px solid #1a1a1a; padding-bottom: 6px; margin-bottom: 10px; }
  .sf-sched .sf-meta { color: #6b7280; font-size: 11px; text-align: right; line-height: 1.5; }
  .sf-sched table { width: 100%; border-collapse: collapse; }
  /* Repeats the column headings on every printed page. Without it a multi-page
     schedule has unlabelled columns from page 2 on. */
  .sf-sched thead { display: table-header-group; }
  .sf-sched tr { page-break-inside: avoid; }
  .sf-sched th { text-align: left; font-size: 9px; text-transform: uppercase;
      letter-spacing: .04em; color: #6b7280; border-bottom: 1px solid #d1d5db; padding: 4px 6px; }
  .sf-sched td { padding: 3px 6px; border-bottom: 1px solid #ececf0; vertical-align: middle; }
  .sf-sched .sf-muted { color: #6b7280; }
  .sf-sched .sf-late { color: #dc2626; font-weight: 600; }
  .sf-sched .sf-note { margin-top: 10px; font-size: 10px; color: #6b7280; font-style: italic; }
  .sf-sched .sf-milestone { color: #7c3aed; }
</style>
"""

_SCHEDULE_CSS = """
<style>
  /* The timeline cell is the positioning context; every bar is a percentage of
     it, so the chart rescales with the page instead of needing a pixel width. */
  .sf-sched .sf-track { position: relative; height: 13px; background: #f4f5f7;
      border-radius: 2px; overflow: hidden; }
  .sf-sched .sf-bar { position: absolute; top: 0; height: 13px; border-radius: 2px;
      background: #bfd4f7; border: 1px solid #2563eb; }
  .sf-sched .sf-bar-late { background: #f8cfcf; border-color: #dc2626; }
  .sf-sched .sf-bar-done { position: absolute; left: 0; top: 0; height: 100%;
      background: #2563eb; border-radius: 2px 0 0 2px; }
  .sf-sched .sf-bar-late .sf-bar-done { background: #dc2626; }
  .sf-sched .sf-scale { position: relative; height: 14px; border-bottom: 1px solid #d1d5db; }
  .sf-sched .sf-scale span { position: absolute; top: 0; font-size: 8px; color: #6b7280;
      border-left: 1px solid #d1d5db; padding-left: 3px; white-space: nowrap; }
  .sf-sched .sf-timeline-col { width: 58%; }
  .sf-sched .sf-undated { font-size: 8px; color: #9ca3af; font-style: italic; }
</style>
"""

_HEADER = """
<div class="sf-head">
  <div>
    <h2>{{ doc.project_name or doc.name }}</h2>
    <div class="sf-muted">{{ doc.name }}{% if doc.customer %} &middot; {{ doc.customer }}{% endif %}</div>
  </div>
  <div class="sf-meta">
    {{ _("Status") }}: <b>{{ doc.status or "" }}</b><br>
    {% if doc.expected_start_date or doc.expected_end_date %}
      {{ frappe.format(doc.expected_start_date, {"fieldtype": "Date"}) or "—" }}
      &ndash;
      {{ frappe.format(doc.expected_end_date, {"fieldtype": "Date"}) or "—" }}<br>
    {% endif %}
    {{ _("Printed") }} {{ frappe.format(frappe.utils.nowdate(), {"fieldtype": "Date"}) }}
  </div>
</div>
"""

SCHEDULE_HTML = (
	_BASE_CSS
	+ _SCHEDULE_CSS
	+ """
<div class="sf-sched">
"""
	+ _HEADER
	+ """
{%- set data = project_schedule_rows(doc.name) -%}
{% if not data.rows %}
  <div class="sf-note">{{ _("This project has no tasks.") }}</div>
{% else %}
  <table>
    <thead>
      <tr>
        <th style="width:26%;">{{ _("Task") }}</th>
        <th style="width:8%;">{{ _("Status") }}</th>
        <th style="width:8%; text-align:right;">{{ _("%") }}</th>
        <th class="sf-timeline-col">
          {% if data.has_dates %}
            <div class="sf-scale">
              {% for m in data.months %}
                <span style="left:{{ m.left_pct }}%;">{{ m.label }}</span>
              {% endfor %}
            </div>
          {% endif %}
        </th>
      </tr>
    </thead>
    <tbody>
      {% for row in data.rows %}
      <tr>
        <td style="padding-left:{{ 6 + row.level * 14 }}px;{% if row.level == 0 %}font-weight:600;{% endif %}">
          {%- if row.is_milestone %}<span class="sf-milestone">&#9670;</span> {% endif -%}
          {{ row.subject }}
        </td>
        <td class="sf-muted">{{ row.status }}</td>
        <td style="text-align:right;" class="{% if row.overdue %}sf-late{% endif %}">
          {{ row.progress | round | int }}%
        </td>
        <td>
          {% if row.width_pct is not none %}
            <div class="sf-track">
              <div class="sf-bar{% if row.overdue %} sf-bar-late{% endif %}"
                   style="left:{{ row.left_pct }}%; width:{{ row.width_pct }}%;">
                {% if row.progress %}<div class="sf-bar-done" style="width:{{ row.progress }}%;"></div>{% endif %}
              </div>
            </div>
          {% else %}
            <span class="sf-undated">{{ _("no dates") }}</span>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% if data.has_dates %}
    <div class="sf-note">
      {{ _("Timeline spans") }}
      {{ frappe.format(data.start, {"fieldtype": "Date"}) }} &ndash;
      {{ frappe.format(data.end, {"fieldtype": "Date"}) }}
      ({{ data.total_days }} {{ _("days") }}){% if data.undated_count %},
      {{ data.undated_count }} {{ _("task(s) have no dates and are listed without a bar") }}{% endif %}.
    </div>
  {% endif %}
{% endif %}
</div>
""".strip()
)

TASK_LIST_HTML = (
	_BASE_CSS
	+ """
<div class="sf-sched">
"""
	+ _HEADER
	+ """
{%- set rows = project_task_rows(doc.name) -%}
{% if not rows %}
  <div class="sf-note">{{ _("This project has no tasks.") }}</div>
{% else %}
  <table>
    <thead>
      <tr>
        <th style="width:40%;">{{ _("Task") }}</th>
        <th style="width:11%;">{{ _("Status") }}</th>
        <th style="width:9%;">{{ _("Priority") }}</th>
        <th style="width:13%;">{{ _("Start") }}</th>
        <th style="width:13%;">{{ _("Due") }}</th>
        <th style="width:7%; text-align:right;">{{ _("%") }}</th>
        <th style="width:7%; text-align:right;">{{ _("Hrs") }}</th>
      </tr>
    </thead>
    <tbody>
      {% for row in rows %}
      <tr>
        <td style="padding-left:{{ 6 + row.level * 14 }}px;{% if row.level == 0 %}font-weight:600;{% endif %}">
          {%- if row.is_milestone %}<span class="sf-milestone">&#9670;</span> {% endif -%}
          {{ row.subject }}
        </td>
        <td class="sf-muted">{{ row.status }}</td>
        <td class="sf-muted">{{ row.priority }}</td>
        <td>{{ frappe.format(row.start, {"fieldtype": "Date"}) or "" }}</td>
        <td class="{% if row.overdue %}sf-late{% endif %}">
          {{ frappe.format(row.end, {"fieldtype": "Date"}) or "" }}
        </td>
        <td style="text-align:right;">{{ row.progress | round | int }}%</td>
        <td style="text-align:right;" class="sf-muted">
          {{ (row.expected_time | round(1)) if row.expected_time else "" }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  <div class="sf-note">{{ rows | length }} {{ _("tasks") }}.</div>
{% endif %}
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


def ensure_project_print_formats():
	"""after_migrate entry: ship the Project Schedule + Task List Print Formats.
	Idempotent (upserts the HTML) and guarded (a failure only logs)."""
	try:
		if frappe.db.exists("DocType", DOCTYPE):
			_upsert_print_format(SCHEDULE_PF, SCHEDULE_HTML)
			_upsert_print_format(TASK_LIST_PF, TASK_LIST_HTML)
			frappe.db.commit()
			frappe.logger().info("[project_enhancements] ensured Project schedule print formats")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Project print formats")

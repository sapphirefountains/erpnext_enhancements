# assistant_tools — custom MCP tools for Frappe Assistant Core

Read-only MCP tools that [Frappe Assistant Core](https://github.com/buildswithpaul/Frappe_Assistant_Core)
(FAC) discovers via the `assistant_tools` hook in `hooks.py` and exposes to AI
assistants (Claude, etc.) connected to the site's MCP endpoint. Companion FAC
*skills* (workflow prompt templates) live in `../data/skills/` and are
registered via the `assistant_skills` hook.

## The FAC-optional invariant

**Nothing inside erpnext_enhancements may import this package.** The import
direction is FAC → us: FAC's tool loader imports each dotted path listed in
the hook, and every import is wrapped in try/except on FAC's side. On sites
without FAC installed the hook entries are inert strings and this package is
never imported, so the app keeps working without FAC. A tripwire test in
`tests/test_assistant_tools_schema.py` enforces this.

For the same reason, do **not** add `frappe_assistant_core` to
`pyproject.toml` dependencies.

## Conventions (enforced by tests)

- One module per tool; **the module filename must equal the tool's `name`**
  (FAC's `custom_tools` plugin derives tool identifiers from the module path).
- Each module defines exactly one `BaseTool` subclass and is listed in
  `hooks.py` under `assistant_tools`.
- Tool names must not collide with FAC's built-in tools (`list_documents`,
  `generate_report`, `run_python_code`, …).
- `source_app = "erpnext_enhancements"`, a non-empty `description`, a
  `requires_permission` DocType (gates both execution and per-user tool
  visibility), and a valid JSON Schema `inputSchema` are required.
- Everything in this first batch is **read-only**. Reused app functions were
  audited for writes; write-capable functions (`update_next_visit_dates`,
  `log_time`, dashboard `update_*`, …) are out of scope until a dedicated
  write-tools batch.
- Permission model: list queries go through `frappe.get_list` (role + user
  permissions enforced); anything that reaches raw SQL or `frappe.get_all`
  inside a reused function is gated first with an explicit
  `frappe.has_permission(..., doc=...)` check (see `_common.require_doc_read`).
- GPS data (Time Kiosk Log) is deliberately not exposed.
- Do **not** register tools through the `assistant_tool_configs` hook —
  Frappe's hook merging list-wraps scalar values and FAC doesn't unwrap them.
  Tool defaults belong in `default_config`; per-site overrides go in
  `site_config.json` under `assistant_tools`.

## Tools

| Tool | Area | Wraps |
|---|---|---|
| `maintenance_day_board` | Maintenance | `api/maintenance_board.py::get_day_board_data` |
| `maintenance_contract_status` | Maintenance | fresh perm-enforced queries on Sapphire Maintenance Contract |
| `maintenance_visit_history` | Maintenance | perm-enforced queries + `_chemistry_trends` |
| `maintenance_site_briefing` | Maintenance | `sapphire_maintenance_record.py::get_dashboard_context` |
| `project_status_overview` | Projects | Project Dashboard `get_project_data` / health / gantt / master-project feeds |
| `project_procurement_status` | Projects | `project_enhancements::get_procurement_status` / `get_procurement_documents` |
| `workforce_time_status` | Time Kiosk | fresh perm-enforced Job Interval queries + `time_kiosk.get_current_status` |

## Deployment notes

- Tools are discovered on FAC startup after `bench restart`; skills are synced
  on `bench migrate` (FAC creates/updates/deletes `FAC Skill` rows from
  `data/assistant_skills.json`).
- FAC's **custom_tools plugin must be enabled** on the site, or external tools
  are skipped entirely.
- On first migrate FAC creates one `FAC Tool Configuration` row per tool
  (enabled, category `read_write`); optionally flip them to `read_only` in the
  FAC admin UI — they are all read-only in implementation regardless.

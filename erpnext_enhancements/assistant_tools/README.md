# assistant_tools — custom MCP tools for Frappe Assistant Core

Read-only MCP tools that [Frappe Assistant Core](https://github.com/buildswithpaul/Frappe_Assistant_Core)
(FAC) discovers via the `assistant_tools` hook in `hooks.py` and exposes to AI
assistants (Claude, etc.) connected to the site's MCP endpoint. Companion FAC
*skills* (workflow prompt templates) live in `../data/skills/` and are
registered via the `assistant_skills` hook.

Since v1.14.0 this package also carries the **AI write-confirmation gate**
(`_gate.py`, applied from `__init__.py`) — see "Write gate" below.

## Write gate (AI Governance, v1.14.0)

When **ERPNext Enhancements Settings → AI Governance → Require Confirmation
for AI Writes** is ON (default OFF — ships dormant):

- Importing this package (which FAC does on every MCP request before
  dispatch) wraps `BaseTool._safe_execute` — the single choke point both FAC
  execution paths converge on. Mutating tools (`create/update/delete/
  submit_document`, `run_workflow`, `run_python_code`, dashboard creation)
  return an **anti-fabrication envelope** instead of executing, and an
  **AI Pending Action** is created for the requesting user (desk notification
  sent). `run_database_query` is exempt (FAC enforces read-only SQL); our own
  tools are explicitly read-only.
- Confirmation is **desk-only by design**: the AI Pending Action form's
  *Confirm & Execute* / *Cancel* buttons call `gating_api.confirm_action` /
  `cancel_action` (dotted path — no Python import, the tripwire stays green).
  There is deliberately **no MCP confirm tool** — a model-callable confirm
  would collapse the human-in-the-loop guarantee under prompt injection.
- The model retrieves the real outcome afterwards via the read-only
  `check_ai_pending_action` tool; the `ee-ai-write-confirmation` skill teaches
  connected assistants the flow.
- Every executed mutation (confirmed or allowlist-exempt) lands in the
  append-only **AI Action Log**; settings allow a per-doctype create/update
  exemption list, a pending TTL (default 1 h, hourly expiry sweep) and an
  optional retention window.
- **FAC-upgrade risk**: `_safe_execute` is private FAC API. `apply_gate()`
  logs an Error Log entry when the seam is missing, and the integration
  canary test (`test_ai_gating_integration.test_gate_marker_present`) fails
  on bench CI. Written against FAC v2.4.3.

Manual smoke test: enable the flag, ask a connected assistant to create a
ToDo → expect the confirmation message + desk notification; confirm in the
desk; ask the assistant to check the action → it reports the created doc.

Note: a desk-side "test tool" execution of a mutating FAC tool is gated too —
any `_safe_execute` of a mutating tool counts as an assistant-channel write.

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
- **Read tools vs. write tools.** Most tools here are **read-only** (including
  `check_ai_pending_action`). The exception is `create_followup_task`
  (v1.29.0) — the first *write* tool. **Every write tool MUST be added to
  `_gate.py`'s `APP_MUTATING` set** so the AI write gate confirms it through a
  human (when gating is on) instead of relying on the fail-closed fallback;
  give it a `summarize_tool_call` case and a `LOW_RISK`/`HIGH_RISK`
  classification too, so the desk confirmation card reads well. A write tool
  must permission-check before it writes (create/`has_permission`, plus
  `require_doc_read` on any referenced record) — the gate re-runs `execute`
  as the *confirming* user, so those checks bind to the human, not the AI's
  identity. Reused app write functions still out of scope (`update_next_visit_dates`,
  `log_time`, dashboard `update_*`, …) until a follow-on batch. The write
  *gate* itself (`_gate.py`/`gating_api.py`) writes AI Pending Action / AI
  Action Log rows but never business documents.
- **Mutation/risk annotations (v1.71.0).** Each *mutating* tool sets
  `self.annotations = annotations_for(self.name)` (from `_gate.py`) in its
  `__init__`. `annotations_for` derives MCP **ToolAnnotations** (`readOnlyHint`
  / `destructiveHint`, plus an `x-ee-mutation` / `x-ee-risk` band) from the
  gate's classification sets, and FAC forwards a tool's `annotations` verbatim
  in `tools/list`. This lets an MCP **client** (e.g. Triton) read a tool's
  mutation/risk from the catalog instead of guessing from its verb — closing a
  safety gap where the oddly-named device tools (`remote_wipe_device`,
  `run_device_script`, …) were guessed read-only and skipped the client's
  confirmation step. `_gate.py` stays the single source of truth; contract tests
  enforce that every registered tool is classified there and that both the
  mutating and the read-only half advertise the matching metadata (see
  "Classification is mandatory" below).
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

Listed in `hooks.py` order. Every tool here must also appear in exactly one
`_gate.py` classification set — see "Classification is mandatory" below.

| Tool | Area | Wraps |
|---|---|---|
| `training_compliance_status` | Training | perm-enforced Training Assignment queries — overdue first, then due-soon, then a per-course summary; never reports watch coverage alone |
| `training_learner_record` | Training | one person's completions, current vs expired vs superseded certifications, assignments and scores; refuses ambiguous names |
| `maintenance_day_board` | Maintenance | `api/maintenance_board.py::get_day_board_data` |
| `maintenance_contract_status` | Maintenance | fresh perm-enforced queries on Sapphire Maintenance Contract |
| `maintenance_visit_history` | Maintenance | perm-enforced queries + `_chemistry_trends` |
| `maintenance_site_briefing` | Maintenance | `sapphire_maintenance_record.py::get_dashboard_context` |
| `project_status_overview` | Projects | Project Dashboard `get_project_data` / health / gantt / master-project feeds |
| `project_procurement_status` | Projects | `project_enhancements::get_procurement_status` / `get_procurement_documents` |
| `workforce_time_status` | Time Kiosk | fresh perm-enforced Job Interval queries + `time_kiosk.get_current_status` |
| `check_ai_pending_action` | AI Governance | read-only status/result lookup of gated AI Pending Actions |
| `create_followup_task` | Productivity | **write (gated)** — creates a ToDo follow-up, optionally linked + assigned |
| `remote_lock_device` / `remote_wipe_device` / `locate_device` / `reboot_device` / `run_device_script` / `deploy_device_patch` | Device Management | **write (gated)** — remote MDM actions via `mdm_integration.actions` (Miradore mobile / Action1 computers); wipe/lock/run-script are HIGH risk |
| `stripe_payment_status` | Accounting | counts by status + unreconciled-paid + failed-webhook signals + recent Stripe Payments (perm-aware `frappe.get_list`) |
| `quickbooks_sync_status` | Accounting | QBO connection state + failed-run count + recent QuickBooks Sync Log rows; pass `sync_log` for one run's summary |
| `document_intake_queue` | Accounting | Accounting Document Intake review queue — counts by status, needs-attention backlog, one doc's lines + matches (companion to Triton's `sfo_extract_document`) |
| `closed_won_handoff_status` | Sales | Closed-Won Opportunities with no project yet (hand-off backlog, oldest first); pass `opportunity` for its hand-off step state |
| `water_calc` | Water Engineering | stateless `water_engineering.engine` dispatch — one hydraulic calc returned with its formula, steps, citations, warnings and A/B/C options |
| `water_design_status` | Water Engineering | a Water Feature Design's rollups, completion %, `next_inputs_needed`, typed issues, readiness gates and calc audit trail; lists designs when `design` is omitted |
| `save_water_design` | Water Engineering | **write (gated)** — creates/updates a Water Feature Design (child tables replaced wholesale), then recomputes |
| `control_panel_status` | Water Engineering | a Control Panel Design's power/nameplate, UI screens, I/O points, interlock checklist and lighting/solenoid rollups |

## Classification is mandatory

Every tool in the hook must appear in exactly one `_gate.py` set —
`EXPLICIT_READONLY` or `APP_MUTATING` — and must set
`self.annotations = annotations_for(self.name)` in `__init__`. Both are enforced
by `tests/test_assistant_tools_schema.py`
(`test_every_registered_tool_is_classified`,
`test_readonly_tools_advertise_readonly_annotation`,
`test_mutating_tools_advertise_mutation_annotations`).

Neither is cosmetic. `is_mutating()` consults the classification sets first and
then falls back to the tool's FAC category — and FAC seeds every external tool
as `read_write`, which matches neither its write branch nor its read branch, so
control reaches the fail-closed `return True`. **An unclassified read tool is
gated as a write**: with `ai_write_gating_enabled` on it records an AI Pending
Action and returns the anti-fabrication envelope instead of answering. That is
what happened to both training tools between v1.216.0 and v1.238.1. The
annotations are the other half: FAC forwards them verbatim in `tools/list`, and
without them an MCP client (Triton) guesses mutation from the tool's verb —
which is how the device tools were mis-read as read-only before v1.71.0.

## Deployment notes

- Tools are discovered on FAC startup after `bench restart`; skills are synced
  on `bench migrate` (FAC creates/updates/deletes `FAC Skill` rows from
  `data/assistant_skills.json`).
- FAC's **custom_tools plugin must be enabled** on the site, or external tools
  are skipped entirely.
- On first migrate FAC creates one `FAC Tool Configuration` row per tool
  (enabled, category `read_write`); optionally flip them to `read_only` in the
  FAC admin UI. Do **not** rely on that flip for gating — `read_write` is the
  category that makes `is_mutating()` fail closed, so classification in
  `_gate.py` is what actually decides, and it is enforced by the contract tests.

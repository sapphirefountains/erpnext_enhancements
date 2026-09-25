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
for AI Writes** is ON. The field's default is OFF, but the v1.525.0 patch
`enable_ai_write_gate` switches it ON on production (ADR 0016 §6):

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
- **Batches (v1.528.0).** The AI Pending Action list decides several at once
  through `my_pending_actions`, `confirm_actions` and `cancel_actions`. They
  cap a batch at 50, run oldest first through the same
  `_confirm_one` / `_cancel_one`, skip anything not Pending or expired, roll
  back and continue after a failure, and start nothing new after a 45-second
  time budget; the list sends one action per request, oldest first. They only cover the
  caller's **own** actions, System Managers included, which is stricter than
  `_check_identity`; the `gating_api` module docstring says why. High-risk,
  hidden-value, submit/cancel and gate-record actions start unticked. See
  [`ai_governance/README.md`](../ai_governance/README.md#deciding-in-batches-v15280).
- **Every `gating_api` endpoint is POST-only and refuses a request that carries
  an `Authorization` header** (v1.528.0, `_require_desk_session`). A GET skips
  Frappe's CSRF check, so a link to `confirm_action` in an assistant reply
  used to run the action on one click; and a token (an MCP client's OAuth
  token, an API key) must never be able to decide its own proposals.
- Confirming executes the arguments **as proposed**. The card shows credential-like values as
  `***REDACTED***`, and `_propose` seals the real ones in the hidden Password field
  `sealed_arguments`. `confirm_action` restores them and refuses if it can't. It masks sealed
  strings of six or more characters out of the stored result and error, and out of FAC's own
  audit row, through `_wrap_log_execution`. The approver can view the hidden values with
  `reveal_sealed`. Before v1.524.1 it executed the redacted card. See
  [`ai_governance/README.md`](../ai_governance/README.md#confirming-runs-what-was-proposed-not-what-the-card-shows).
- **A write that cannot run gets no card (v1.533.0).** Before `_propose`, `_precheck_refusal`
  checks each Select value in a `create_document` or `update_document` call's `data`, including
  child-table rows. It uses the same comparison as Frappe v16's `_validate_selects`, applied only
  to the values in the call. An off-options value, or a DocType that does not exist, returns an
  error with error type `AIGateValidationError`. The error uses Frappe's wording, masks
  credential-like fields and lists at most five problems. It is recorded in AI Action Log, and no
  card is created. The check reads metadata only, so it runs no controller hooks. It never checks
  Link targets, because a later card may depend on a record an earlier card creates. Where it
  can't be sure it queues the card: `fetch_from` Selects, cancels, and any failure of the check
  itself, which also goes to the Error Log. See
  [`ai_governance/README.md`](../ai_governance/README.md#a-write-that-cannot-run-gets-no-card-v15330).
  **Since v1.540.0 an `update_document` with `docstatus` 2 is refused the same way**
  (`_cancel_refusal`), because FAC refuses every change to a submitted document and the card
  could only fail after approval. The refusal names `cancel_document`.
- The model retrieves the real outcome afterwards via the read-only
  `check_ai_pending_action` tool; the `ee-ai-write-confirmation` skill teaches
  connected assistants the flow.
- Every executed mutation (confirmed or allowlist-exempt) lands in the
  append-only **AI Action Log**; settings allow a per-doctype create/update
  exemption list, a pending TTL (default 1 h, hourly expiry sweep) and an
  optional retention window.
- **Exemptions are permanent or time-boxed (v1.525.0).** A row with no
  `exempt_until` is permanent. The patch seeds Comment, ToDo, Sapphire
  Maintenance Template and Section, Serial No and Training Lesson. A create
  that submits, or an update that sets `docstatus`, is never exempt. A row with a time is a window for a
  bulk job: a human opens it on the settings page and it closes itself, because
  `_exempt_doctypes()` compares it with the current time on every call.
  `NEVER_EXEMPT` covers Task and the gate's own records: its settings, the
  exemption table, AI Pending Action and AI Action Log. That means an
  assistant can't open its own window, rewrite a card after it was read, or
  edit its audit trail. Since v1.538.0 it also covers the Knowledge Base's
  two doctypes (WI-080): company knowledge is published only by a person
  approving someone else's draft. It is built from those three kinds (Task,
  `GATE_OWN_DOCTYPES`, `KNOWLEDGE_BASE_DOCTYPES`), and the batch dialog's
  review reason names the kind rather than calling every one the gate's own.
- **FAC-upgrade risk**: `_safe_execute` is private FAC API. `apply_gate()`
  logs an Error Log entry when the seam is missing, and the integration
  canary test (`test_ai_gating_integration.test_gate_marker_present`) fails
  on bench CI. Written against FAC v2.4.3; **re-verified against v3.0.0** (see
  "FAC 3.0.0" below).

## FAC 3.0.0 (tagged and on prod 2026-09-23; verified the same day)

What changed upstream, and what it meant here:

- **The integration contract did not move.** Same three hooks (`assistant_tools`,
  `assistant_skills`, `assistant_tool_configs`), same `BaseTool`, same `_safe_execute` seam.
- **FAC Chat** (Desk widget + `/copilot`; off by default, a paid FAC Cloud service) is not a
  new execution path. Its cloud runtime registers as a per-user OAuth client of this site's
  `handle_mcp`, so every tool call it makes passes through the gate. It sends
  `X-AR-Session-Id`, which FAC puts on `frappe.local.ar_session_id`; the gate now records
  that as the pending action's `session_id` (and `fac-chat` as `client_id`), because FAC's own
  `assistant_session_id` is a fresh UUID per request on a stateless endpoint.
- **New `faco` plugin tools** (disabled on prod until ticked in FAC's plugin settings).
  `send_email` is **HIGH risk** in `EXPLICIT_MUTATING`: it mails any address from the site's
  own account and needs no DocPerm, so it is an exfiltration channel under prompt injection.
  `generate_document` (markdown → private PDF File) is a Low-risk write. The six `browser_*`
  tools are read-only per FAC and pass through; they drive the FAC Chat widget's tab and do
  nothing useful for any other client.
- **`search_doctype` and `search_link` are gone**, folded into `search_documents`
  (`doctype` for one DocType, `purpose="link_value"` for Link-field resolution). Nothing here
  named them; Triton did (fixed there).
- **FAC no longer blocks *reads* of admin DocTypes** for non-System-Managers (Error Log,
  Access Log, Email Queue, OAuth Bearer Token…); Frappe's DocPerms decide. Writes to
  code/schema/permission DocTypes (Server Script, Custom Field, Role, Workflow…) are refused
  over MCP for everyone but System Manager. Our `DENYLIST_DOCTYPES` is unaffected — it exists
  because raw SQL never consulted either layer.
- **Categories override annotations** — not new in 3.0 (it arrived in 2.5.0), but found
  during this review. See "Classification is mandatory" below.

Manual smoke test: enable the flag, ask a connected assistant to create a
ToDo → expect the confirmation message + desk notification; confirm in the
desk; ask the assistant to check the action → it reports the created doc.

Note: a desk-side "test tool" execution of a mutating FAC tool is gated too —
any `_safe_execute` of a mutating tool counts as an assistant-channel write.

## The denylist: doctypes no generic tool may reach

`_gate.DENYLIST_DOCTYPES` names the doctypes that no generic FAC tool may reach, for any role,
with the write gate on or off, and whatever a confirmation says. It holds two today:

| Doctype | Since | Why |
|---|---|---|
| `Triton Chat Attachment` | v1.426.0 (deliberately; incidentally since v1.271.0) | Every employee's private Triton uploads. Its own hooks deny rows a System Manager does not own, and those hooks do nothing to raw SQL |
| `Knowledge Article Version` | v1.538.0 ([WI-080](../../work-items/WI-080-company-knowledge-base.md), [ADR 0017](../../decisions/adr/0017-company-knowledge-lives-in-a-native-module.md)) | Knowledge-base drafts and the history behind them: text no second person has approved. Only approved text may reach an assistant. It refuses KB Authors and Approvers too, who can open drafts in the Desk |

`Knowledge Article`, the **published** doctype, is deliberately **not** on the list. Every staff
user reads it anyway, and its needle (`knowledgearticle`) is a prefix of the Version's, so adding
it would refuse every generic read of the published text and the WI-080 acceptance queries. The
operators' own integrity check over drafts therefore lives in a Script Report (WI-080 PR 4), not in
MCP SQL.

Each entry has its own reason in `_gate.DENYLIST_REASONS`, and the refusal reads
"Refused: <doctype> <reason>". A model and the person behind it read that message, and "private
assistant context" is the wrong explanation for a draft. `test_ai_gate_denylist` fails the build if
the two drift apart.

**History.** `CHAT_DENYLIST_DOCTYPES` made employee chat content unreadable the same way from
v1.271.0 until the chat module was removed in v1.426.0
([ADR 0011](../../decisions/adr/0011-retire-google-chat-and-coworker-chat.md)). It was nearly
deleted outright, and that would have been wrong: its `Chat Attachment` needle was a substring of
`tabtritonchatattachment`, so it had been gating `Triton Chat Attachment` all along, and that
doctype survives. The comment block above `DENYLIST_DOCTYPES` in `_gate.py` records it. (Until
v1.538.0 this section said there was no denylist in `_gate.py` at all; there has been one entry
since v1.426.0.)

**It is not enough to withhold DocPerm.** That closes `get_document` and `list_documents`
and does nothing to the third surface. `run_database_query`'s own stated security model is
*"Restricted to SELECT statements only. Requires System Manager role for security"* — a role
check and a read-only-SQL check. Raw SQL sits *underneath* DocPerm,
`permission_query_conditions` and `has_permission`, so no Frappe permission mechanism
touches it, and a System Manager is otherwise one ``select …`` away from the whole table,
delivered into a model's context window. Note that `run_database_query` is exempt from
*confirmation* (above) and must **not** be exempt from a content denylist. So the refusal
comes in three shapes, because the tools do:

- a **`doctype` argument**, compared on *every* tool rather than a named list, so a tool added to
  FAC tomorrow that takes a `doctype` is covered the day it appears. Case and runs of whitespace
  are folded, since MariaDB resolves a DocType name case-insensitively;
- a **doctype named inside a structured argument** (since v1.538.0): `fetch`'s single `id`,
  `"<doctype>/<name>"`, split on the first slash as FAC splits it; and `run_python_code`'s
  `data_query.doctype`, which FAC pre-loads with `frappe.get_all`, applying no permissions at all.
  Both were open before v1.538.0;
- **free text**: `run_database_query`'s `query` (or `sql`) and `run_python_code`'s `code`.

**And the refusal belongs at the top of `_gated_execute`** — above the confirm-flow bypass
and above the `ai_write_gating_enabled` check. A refusal reachable only while a settings
checkbox is ticked is not an invariant. It is logged to AI Action Log (`success = 0`, High
risk), because an attempt to reach one of these doctypes is the event an operator wants to
find later.
The free-text half refuses on **contact** rather than trying to parse: case-fold, drop every
non-word character, and refuse if the table name survives as a contiguous needle in **either**
of two views of the text, one with SQL comments stripped and one without
(`_denylist_haystacks`). The stripped view alone was a hole from v1.271.0 until v1.538.0:
stripping comments is parsing, and it deleted text MariaDB runs. A `#` or `--` inside a string
literal (`select '#', body from ...`), a `--` with no space after it (`1--1`), and a `/*! ... */`
or `/*M! ... */` executable comment each hid the table name, and `run_database_query` is a read
tool, so nothing raised a card.
Attempting to allow "safe" queries loses to every quoting trick; refusing on contact does
not. Over-refusal costs an analyst one rephrase; under-refusal costs the invariant silently.
(Two accepted over-refusals are pinned in the test: a query that aliases `tabKnowledge Article`
as `version`, and a comment saying `version` right after it.)

`tests/test_ai_gate_denylist.py` (on the AI-gate CI step) covers every path for the Version
doctype, a set of SQL spellings (comments, backticks, double quotes, case, newlines and tabs,
`information_schema`, and the comment markers MariaDB does not honour), the published doctype
and the WI-080 acceptance queries passing, the
Triton Chat Attachment refusal and its message unchanged, and the ordering: it runs
`_gated_execute` with gating off and with the bypass flag set, and asserts that the tool never
runs. Add to the list whenever a doctype's content must not reach a model and its hooks are the
only thing between a System Manager and the rows.

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
  `check_ai_pending_action`). **Since ADR 0014 the gate can also decide per *call*:**
  `_gate.PER_CALL_GATED` maps a tool name to a pure decider over that call's arguments, so
  `workforce_clock_out` executes self-service ("clock me out" — authority the kiosk already
  hands that person) and proposes on-behalf. Splitting those into two tools was rejected
  because the model picks the tool, which would make the model decide whether a human is
  consulted. **Since v1.524.0 FAC's `update_document` has one too** (ADR 0016 §6): a Task
  update executes unless it closes the Task — an allowlist of Open, Working, Pending Review
  and Overdue, so Completed, Canceled, core's "Cancelled" and anything unrecognised wait —
  and every other doctype falls through to the exempt allowlist and then a proposal.
  `NEVER_EXEMPT` strips Task, since v1.525.0 the gate's own records, and since v1.538.0 the
  two Knowledge Base doctypes, from the settings allowlist whatever a row says, because that allowlist ungates `create_document` and
  `update_document` together. **No decider is ever
  registered for a `HIGH_RISK` tool** (`test_ai_gate_per_call` pins the sets disjoint): step 3b
  does not consult `HIGH_RISK`, so a decider would run it unconfirmed. `run_python_code` is
  the case in point — as deployed it hands the caller the whole `frappe` module on a read-write
  connection, so it is arbitrary code, not a read (verified 2026-09-23). The deciders are
  live once `ai_write_gating_enabled` is 1, which the v1.525.0 patch sets. The exception is `create_followup_task`
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
- **Mutation/risk annotations (v1.71.0; extended to read tools in v1.239.1).**
  **Every** tool sets `self.annotations = annotations_for(self.name)` (from
  `_gate.py`) in its `__init__` — mutating and read-only alike. Until v1.239.1
  only the mutating tools did, which left fourteen read tools advertising
  nothing and the client guessing.
  `annotations_for` derives MCP **ToolAnnotations** (`readOnlyHint`
  / `destructiveHint`, plus an `x-ee-mutation` / `x-ee-risk` band) from the
  gate's classification sets, and FAC reads a tool's `annotations` into
  `tools/list` (with its category's hints merged over them — see "Classification
  is mandatory"). This lets an MCP **client** (e.g. Triton) read a tool's
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
| `training_course_catalog` | Training | the course-shaped third: catalogue + gates + version history + aggregate assignment/attempt counts. `item_analysis` is aggregate only and **withholds questions below 5 recorded answers** — a success rate over three people identifies them |
| `draft_course_spec` | Training | **read** — draft a whole course (lessons, blocks, quizzes) as a validated Course Spec from a plain-language brief, via ERPNext's own Vertex client. Writes nothing; returns a preview + a one-hour token. Bounds imported from the Training Question controller; only AI-authorable block types (no uploaded media) |
| `author_training_course` | Training | **write (gated)** — build a Training Course + unpublished draft from a Course Spec (or a `draft_course_spec` token) via the deterministic materializer (`api/training_course_authoring`), reusing the manual builder's own `create_draft_version` + `save_draft_version`. Every quiz question is stamped `ai_generated` with **no** reviewer, so publication stays gated on a human; the course lands as a Draft. Second app write tool after `create_followup_task` |
| `publish_training_course` | Training | **write (gated, one-way)** — freeze a draft course version and make it live via `api/training_author.publish_version`, which materializes `toc_json`/`content_hash` and only then submits. The step that had no tool: publishing is not a document submit, so `submit_document` was refused by `_require_materialized_content`. `change_type` is required and undefaulted — Material Change invalidates every existing completion. Medium risk: freezes lesson titles permanently and can fan assignments out to everyone |
| `maintenance_day_board` | Maintenance | `api/maintenance_board.py::get_day_board_data` |
| `maintenance_contract_status` | Maintenance | fresh perm-enforced queries on Sapphire Maintenance Contract |
| `maintenance_visit_history` | Maintenance | perm-enforced queries + `_chemistry_trends` |
| `maintenance_site_briefing` | Maintenance | `sapphire_maintenance_record.py::get_dashboard_context` |
| `project_status_overview` | Projects | Project Dashboard `get_project_data` / health / gantt / master-project feeds |
| `project_procurement_status` | Projects | `project_enhancements::get_procurement_status` / `get_procurement_documents` |
| `project_pickup_route` | Projects | `api/pickup_routing.py::get_pickup_route_data` — **strips `api_key`** (a live billable Google Maps *browser* key) and `use_routes_api`; keeps address-less suppliers rather than dropping them, since a shorter route that omits them is quietly wrong |
| `contract_signing_status` | Contracts | `esign/api.py::get_signature_state`, `project_contract.py::get_contracts`, and a perm-aware backlog mirroring `esign/tasks.py::digest_awaiting_signature`. `days_out` from **`first_sent_on`**, not `sent_on` — reminders rewrite `sent_on`. Returns **none** of the signing evidence (token hashes, `agreement_html`, `document_snapshot`, `signature_image`, signer IP, `user_agent`, `consent_text`) |
| `kpi_dashboard_status` | Analytics | `api/kpi.py::visible_departments` + `::get_kpi_dashboard`, plus `source_freshness_json` off the snapshot. Unqualified calls return **Watch/Bad only** across visible departments; `refresh_kpi_dashboard` is deliberately not exposed (it commits) |
| `workforce_time_status` | Time Kiosk | fresh perm-enforced Job Interval queries + `time_kiosk.get_current_status` |
| `workforce_clock_in` | Time Kiosk | **write (per-call: never gated)** — starts a session for the **calling user only**. Takes no `employee` and no coordinates: the fix comes from the user's own browser via `time_kiosk.stash_location_fix` and is read back server-side, because a lat/lng arriving as a tool argument is a number the model typed. No fresh fix, no clock-in |
| `workforce_clock_out` | Time Kiosk | **write (per-call: gated only on-behalf)** — no `employee` closes the caller's own session and executes; naming another employee needs `TIMELINE_MANAGER_ROLES`, is stamped with the requester, and becomes an AI Pending Action. Always an unanchored close; the photo gate's `skip_reason` is recorded verbatim |
| `check_ai_pending_action` | AI Governance | read-only status/result lookup of gated AI Pending Actions |
| `create_followup_task` | Productivity | **write (gated)** — creates a ToDo follow-up, optionally linked + assigned |
| `cancel_document` | Documents | **write (gated, HIGH risk, never exempt)** — cancels a submitted document with `doc.cancel()`, so Frappe's cancel permission, linked-document check (`LinkExistsError`, never `ignore_links`) and the doctype's cancel hooks apply, inside a savepoint. Refuses drafts, non-submittable doctypes and a Workflow that owns cancelling (names `run_workflow`); reports an already-cancelled document as done. FAC 3.0.0 has no cancel tool and its `update_document` refuses any change to a submitted document, so a `docstatus: 2` update is refused at queue time and points here (v1.540.0) |
| `remote_lock_device` / `remote_wipe_device` / `locate_device` / `reboot_device` / `run_device_script` / `deploy_device_patch` | Device Management | **write (gated)** — remote MDM actions via `mdm_integration.actions` (Miradore mobile / Action1 computers); wipe/lock/run-script are HIGH risk |
| `stripe_payment_status` | Accounting | counts by status + unreconciled-paid + failed-webhook signals + recent Stripe Payments (perm-aware `frappe.get_list`) |
| `quickbooks_sync_status` | Accounting | QBO connection state + failed-run count + recent QuickBooks Sync Log rows; pass `sync_log` for one run's summary |
| `document_intake_queue` | Accounting | Accounting Document Intake review queue — counts by status, needs-attention backlog, one doc's lines + matches (companion to Triton's `sfo_extract_document`) |
| `closed_won_handoff_status` | Sales | Closed-Won Opportunities with no project yet (hand-off backlog, oldest first); pass `opportunity` for its hand-off step state |
| `water_calc` | Water Engineering | stateless `water_engineering.engine` dispatch — one hydraulic calc returned with its formula, steps, citations, warnings and A/B/C options |
| `water_design_status` | Water Engineering | a Water Feature Design's rollups, completion %, `next_inputs_needed`, typed issues, readiness gates and calc audit trail; lists designs when `design` is omitted |
| `save_water_design` | Water Engineering | **write (gated)** — creates/updates a Water Feature Design (child tables replaced wholesale), then recomputes |
| `control_panel_status` | Water Engineering | a Control Panel Design's power/nameplate, UI screens, I/O points, interlock checklist and lighting/solenoid rollups |
| `item_naming_check` | Inventory | a proposed Item Code/Name against the naming SOP — duplicates, scored neighbours, code family, `PDT-`/`SRV-` block occupancy, every mechanical name defect, and a STOP/FIX/PASS verdict. Advisory: nothing this tool reports blocks a save by itself. From 2026-10-01 (POL-0602; shipped in v1.532.0) the `Item` doc_event refuses a *new* Item for two of these findings only (a code duplicating an existing one after normalisation, a name that is just the code) |
| `party_naming_check` | CRM | whether a Project, Opportunity or Address is named after the party it belongs to — one record, or the whole doctype audited. Advisory; out of scope is NOT a pass, and the payload says which. Spans three doctypes, so it gates visibility on `Address` and re-checks the one asked for inside `execute` |

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
what happened to both training tools between v1.216.0 and v1.239.1. The
annotations are the other half: FAC reads them into `tools/list`, and without
them an MCP client (Triton) guesses mutation from the tool's verb — which is how
the device tools were mis-read as read-only before v1.71.0.

**And FAC's category is a third half.** Since FAC 2.5.0, `tools/list` merges hints
derived from the tool's `FAC Tool Configuration.tool_category` *over* its own
annotations, and FAC seeds every external tool as `read_write`, which it maps to
`readOnlyHint: false`. So every read tool here was advertised to Claude, and to FAC
Chat's approval defaults, as a write (Triton escaped only because it reads
`x-ee-mutation` first, which FAC never touches). `ai_governance/fac_tool_categories.py`
now writes each row's category from the tool's own annotations — `read_only`, `write`,
or `privileged` for `HIGH_RISK` — on insert and on every migrate. The contract test
`test_fac_category_reproduces_each_tools_own_hints` asserts the merge is a no-op for
every registered tool.

## Deployment notes

- Tools are discovered on FAC startup after `bench restart`; skills are synced
  on `bench migrate` (FAC creates/updates/deletes `FAC Skill` rows from
  `data/assistant_skills.json`).
- FAC's **custom_tools plugin must be enabled** on the site, or external tools
  are skipped entirely.
- On first migrate FAC creates one `FAC Tool Configuration` row per tool
  (enabled, category `read_write`). Since v1.521.0 the app sets each row's category
  from the tool's annotations (override on) — see "Classification is mandatory". The
  category shown in FAC's admin UI for these tools is **managed by the app**: change
  `_gate.py`, not the page, which is reset on the next migrate. It never decided
  gating anyway — `is_mutating()` consults `_gate.py`'s sets before any category.

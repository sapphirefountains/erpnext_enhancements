# Module Reorganization Plan

Goal: every custom page/app belongs to a clearly-named module, and every module
with a user-facing surface gets its own **workspace (sidebar)**. Today only 3 of
12 modules have a sidebar.

This is a working/tracking doc for a multi-PR effort. Delete once complete.

## Progress

- [x] **PR 1** — Devices sidebar (Device Management + MDM) — merged (#444, v1.35.0)
- [x] **PR 2** — Inventory / Task / CRM sidebars (no moves) — v1.36.0
  - Correction: `task_enhancements/doctype/task` is a script customization of the standard **Task** (no custom doctype); the sidebar links standard Task.
  - Correction: most CRM doctypes are **child tables** (not linkable). CRM sidebar surfaces Sales Pipeline (page), Value Streams, Sales Activity Settings + convenience links to standard Lead/Opportunity/Customer.
  - Noted (out of scope): a stray **Page** JSON lives at `task_enhancements/doctype/hierarchical_task_view/` (real page is under `page/`).
- [x] **PR 3** — QuickBooks split — v1.37.0
  - `quickbooks_time_integration` → `quickbooks_online` (module + folder); inner engine subpackage `quickbooks_online/` → `core/` (avoids doubled path).
  - New `quickbooks_time` module holds the `qb_timesheet_webhook` (extracted from the shared `api.py`).
  - Module Def rename patch (`rename_quickbooks_module`, pre-model-sync). Both sidebars built.
  - **Deploy:** QBO + QB Time webhook URLs change — update the endpoints in Intuit / QuickBooks Time.
- [x] **PR 4** — Workforce — v1.38.0
  - Moved Time Kiosk Log + Time Kiosk Settings doctypes and the Time Kiosk + Location Timeline pages `enhancements_core` → `workforce`; only `api/time_kiosk.py` needed an import-path fix (everything else refs doctypes by name / `api.time_kiosk`, which stays).
  - `/kiosk` PWA + `api.time_kiosk` stay app-level; sidebar links them. Idempotent backstop patch `move_time_tracking_to_workforce`.
  - Follow-up done (v1.38.1): `Job Interval` (clock-in session doctype) also moved Core → Workforce — no code changes needed (refs are by name / Link field), backstop patch `move_job_interval_to_workforce`.
- [x] **PR 5** — Integrations — v1.39.0
  - Moved GA4 Settings doctype + GA4 Dashboard / Integrations Health pages `enhancements_core` → `integrations` (adopted the empty `integrations/` placeholder as the module folder). No code changes (pure-JS pages; refs by name + `api.analytics`/`api.integrations_health`). Backstop patch `move_analytics_to_integrations`.
  - Hub sidebar: Analytics card + Connected Services card cross-linking QuickBooks Online / MDM / Google Drive / Triton settings Singles.
- [x] **PR 6** — Google Drive — v1.40.0 (heaviest move)
  - Moved drive_link_manager/drive_match/drive_sync/drive_utils + 4 drive doctypes + Drive Link Manager page `crm_enhancements` → `google_drive`. ~19 files: dotted imports, page/settings JS RPC strings, 5 hooks entries, external importers (api/, tests). `crm_enhancements.api` stays in CRM (drive_utils import repointed). Backstop patch `move_drive_to_google_drive`. READMEs split. CRM sidebar already excluded Drive (built that way in PR 2) — no sidebar change.
- [x] **PR 7** — Morning Briefing — v1.41.0
  - Moved Daily Briefing doctype `enhancements_core` → `morning_briefing` (refs by name; `api.briefing` + `/wall` www stay app-level). Backstop patch `move_briefing_to_morning_briefing`. Sidebar links Daily Briefing + `/wall` URL + Enhancements Settings (recipients).
  - `Briefing Recipient` kept in Core — child table of ERPNext Enhancements Settings (same call as `collab_doctype`).
- [x] **PR 8** — Project Enhancements sidebar — v1.43.0
  - Built the module's first sidebar (Project Dashboard, Master Project, Project Contract, Contract Template, Process Step Template, Project Dashboard Settings + standard Project).
  - **Reframed:** the planned `project_note`/`project_reminder_email` move was dropped — both are child tables. `Project Reminder Email` is a child table of ERPNext Enhancements Settings (stays in Core, like `collab_doctype`). `Project Note` (singular) is an **orphan** (no Table field references it; the in-use one is `Project Notes` plural, already here) — flagged for cleanup, not relocated.
- [x] **PR 9** — AI/Triton consolidation — v1.44.0
  - Moved Triton Settings + Training Insight (from Core) and Triton Assistant Settings + Triton Allowed User (child table, from Global) → `ai_governance`. Only 2 path fixes (self-ref in triton_settings.py, RPC string in triton_assistant_settings.js); rest by name. `triton_chat`/`utils.triton_sync` app-level, unchanged. Updated AI Governance sidebar (Triton Assistant card + Triton shortcut + Training Insight). Backstop patch `move_triton_to_ai_governance`.
  - Global Enhancements now holds only `additional_supplier_group` + `directory_link_exclusion` → PR 13 retires it.
- [x] **PR 10** — Asset Management — v1.45.0
  - Moved Asset Booking doctype `enhancements_core` → `asset_management`; repointed the self-enqueue (`update_asset_status`), `check_availability`, and the calendar `get_events_method` (`public/js/asset_booking_calendar.js`) paths. `api/booking.py` stays app-level (creates by name). Sidebar (Asset Booking, Asset). Backstop patch `move_asset_booking_to_asset_management`.
- [x] **PR 11** — Process Documentation — v1.46.0
  - Moved Process Document doctype `enhancements_core` → `process_documentation`. No code changes (refs by name: hooks doctype_js, setup/process_documents.py seeder, Process Step Template Link). Distinct from PRO-0204 `process_steps.py`/Process Step Template (untouched). Backstop patch `move_process_document_to_process_documentation`. Sidebar (Process Document).
- [x] **PR 12** — Travel / Expense Claim Type — v1.47.0
  - **Reframed (no move):** "Expense Claim Type" is a standard ERPNext (HR) doctype — the `enhancements_core/doctype/expense_claim_type/` folder is a logic-free controller stub with no JSON, and nothing sets its module. A standard doctype can't be cleanly re-moduled (ERPNext re-syncs it each migrate). So instead added an **Expense Claim Type** link to the existing Travel sidebar (masters card). Stub left untouched.
- [x] **PR 13** — Retire Global Enhancements + Enhancements Core sidebar — v1.49.0 (capstone)
  - Moved Additional Supplier Group (child table) + Directory Link Exclusion `global_enhancements` → `enhancements_core` (refs by name; module not imported). Removed the `global_enhancements/` folder + modules.txt entry; patch `retire_global_enhancements` reassigns + deletes the orphaned Module Def. Built the Enhancements Core sidebar (Settings + Tools) — the last module to get one.

**✅ Module reorganization complete (PRs 1–13, v1.35.0–1.49.0).** Every page/app lives in a clearly-named module with its own sidebar; 12 → 18 modules. Plus follow-ups: Job Interval→Workforce (#449), stray Page cleanup (#447), Project Note orphan removed (#459). This doc can be deleted once PR 13 merges.

### Also handled (separate cleanups)
- #447 — removed a stray Page JSON misfiled under `task_enhancements/doctype/hierarchical_task_view/`.
- #449 — moved Job Interval Core → Workforce (completing PR 4).
- #459 — dropped the orphaned `Project Note` child-table doctype.

## Decisions (locked)

| # | Decision | Outcome |
|---|---|---|
| 1 | Time-tracking surfaces in Core | **New `Workforce` module** |
| 2 | "QuickBooks Time Integration" is really QBO accounting | **Split → `QuickBooks Online` + `QuickBooks Time`** (keep both) |
| 3 | integrations_health + GA4 in Core | **New `Integrations` module** |
| 4 | Triton split across Core/Global; AI Governance separate | **Consolidate Triton into AI Governance** |
| 5 | Drive Link Manager + drive_* in CRM | **New `Google Drive` module** |
| 6 | /wall + Daily Briefing in Core | **New `Morning Briefing` module** |
| 7 | project_note + project_reminder_email in Core | **Move to Project Enhancements** |
| 8 | Global Enhancements near-empty after Triton leaves | **Fold into Enhancements Core (retire)** |
| 9 | Device Management + MDM Integration | **One shared `Devices` sidebar** (modules stay separate; MDM has no own sidebar) |
| 10 | training_insight, expense_claim_type in Core | **training_insight → AI Governance; expense_claim_type → Travel** |
| 11 | asset_booking, process_document, collab_doctype in Core | **asset_booking → new `Asset Management`; process_document → new `Process Documentation`; collab_doctype stays Core (child table)** |

## Target module list (18; was 12)

`modules.txt` ends up as:

```
Enhancements Core
Travel Management
Sapphire Maintenance
CRM Enhancements
Project Enhancements
Task Enhancements
QuickBooks Online          # renamed from "QuickBooks Time Integration"
QuickBooks Time            # new (split out)
AI Governance
Inventory Enhancements
Device Management
MDM Integration
Workforce                  # new
Integrations               # new
Google Drive               # new
Morning Briefing           # new
Asset Management           # new (asset_booking)
Process Documentation      # new (process_document)
```
Removed: **Global Enhancements** (retired).

**Sidebars:** 17 (every module except MDM Integration, which is covered by the
Devices sidebar). 3 exist today (AI Governance, Sapphire Maintenance, Travel
Management) → **14 new to build + AI Governance updated**.

## Per-module contents & sidebar

Legend: 🆕 new module · ✅ workspace exists · 🔧 workspace to build · ➡️ moving in · ⬅️ moving out

### Device Management 🔧  *(first to build — hosts the shared "Devices" sidebar)*
- Doctypes: managed_device, device_assignment_log, device_compliance_settings
- Pages: device_console, device_fleet_dashboard
- **Devices sidebar** (covers Device Management **and** MDM Integration):
  - **Shortcuts** Device Console · Device Fleet Dashboard · Managed Device
  - **Cards** Devices (Managed Device, Device Assignment Log) · Compliance (Device Compliance Settings) · MDM (MDM Settings, MDM Sync Log, Device Action Log, MDM Raw Payload)

### MDM Integration 🆕no-sidebar
- Doctypes: mdm_settings, mdm_sync_log, mdm_raw_payload, device_action_log
- Stays a separate module for code org; surfaced through the Devices sidebar above (no own workspace).

### Inventory Enhancements 🔧
- Doctypes: inventory_count_session, inventory_count_line, storage_location, inventory_scanner_settings
- Pages: inventory_scanner_audit
- Sidebar: **Shortcuts** Inventory Scanner Audit · Inventory Count Session — **Cards** Counts (Session, Line) · Masters (Storage Location, Inventory Scanner Settings)

### Task Enhancements 🔧
- Doctypes: task, hierarchical_task_view
- Pages: hierarchical_task_view
- Sidebar: **Shortcuts** Hierarchical Task View · Task — **Cards** Tasks (Task, Hierarchical Task View)

### CRM Enhancements 🔧  ⬅️ drive_* + drive_link_manager leave (→ Google Drive)
- Doctypes (after): accounts_lead, accounts_opportunity, accounts_project, lead_source, opportunity_contributor, sales_activity_settings, value_stream, value_streams
- Pages: sales_pipeline
- Sidebar: **Shortcuts** Sales Pipeline · Lead · Opportunity — **Cards** Sales (Lead, Opportunity, Lead Source, Opportunity Contributor, Value Stream) · Settings (Sales Activity Settings)

### Project Enhancements 🔧  ➡️ project_note + project_reminder_email (from Core)
- Doctypes: master_project, project, project_contract, project_stakeholder, contract_* family, *_deliverables/*_customer_requests family, process_step_template, project_process_step, project_dashboard_settings, project_dashboard_permitted_role, project_notes, opportunity, address, **+ project_note, project_reminder_email**
- Pages: project_dashboard
- Sidebar: **Shortcuts** Project Dashboard · Master Project · Project Contract — **Cards** Projects (Master Project, Project, Project Stakeholder) · Contracts (Contract Template, Milestone, Phase, Service Option) · Notes (project_note, project_reminder_email) · Settings (Project Dashboard Settings)

### QuickBooks Online 🔧  *(renamed from "QuickBooks Time Integration")*
- Code: quickbooks_online/ (api, client, mapping, sync, webhooks, tasks)
- Doctypes: quickbooks_online_settings, quickbooks_sync_log, quickbooks_sync_mapping, quickbooks_raw_payload
- Pages: quickbooks_online_dashboard
- Sidebar: **Shortcuts** QuickBooks Online Dashboard · QuickBooks Online Settings — **Cards** Sync (Sync Log, Sync Mapping, Raw Payload) · Settings (QuickBooks Online Settings)

### QuickBooks Time 🆕🔧  *(thin — webhook only for now)*
- Code: the `qb_timesheet_webhook` (move out of QBO module root api.py)
- Doctypes/pages: none yet
- Sidebar (minimal): **Cards** Timesheets (webhook docs / future settings + link to Workforce)

### AI Governance ✅→update  ➡️ triton_settings (Core), triton_assistant_settings + triton_allowed_user (Global), training_insight (Core)
- Doctypes: ai_action_log, ai_pending_action, ai_model_usage, ai_confirmation_exempt_doctype, **+ triton_settings, triton_assistant_settings, triton_allowed_user, training_insight**
- Sidebar update: **Cards** Governance (AI Action Log, AI Pending Action, AI Model Usage, AI Confirmation Exempt DocType, Training Insight) · Triton Assistant (Triton Settings, Triton Assistant Settings, Triton Allowed User)

### Workforce 🆕🔧  ➡️ time_kiosk_log, time_kiosk_settings (from Core)
- Doctypes: time_kiosk_log, time_kiosk_settings
- Pages: time_kiosk, location_timeline · Web: `/kiosk` (stays in www/, linked)
- Sidebar: **Shortcuts** Time Kiosk · Location Timeline · Kiosk (/kiosk) — **Cards** Time Tracking (Time Kiosk Log, Time Kiosk Settings)
- (Cross-links to QuickBooks Time for timesheet export.)

### Integrations 🆕🔧  ➡️ ga4_settings (from Core)
- Doctypes: ga4_settings · Pages: integrations_health, ga4_dashboard
- Sidebar: **Shortcuts** Integrations Health · GA4 Dashboard — **Cards** Monitoring (Integrations Health) · Analytics (GA4 Dashboard, GA4 Settings) · Connected Services → cross-links to QuickBooks Online Settings, MDM Settings, Google Drive Settings, Triton Settings

### Google Drive 🆕🔧  ➡️ from CRM
- Code: drive_link_manager.py, drive_match.py, drive_sync.py, drive_utils.py
- Doctypes: drive_link_candidate, drive_sync_log, drive_folder_template_item, project_folder_google_drive_settings
- Pages: drive_link_manager
- Sidebar: **Shortcuts** Drive Link Manager — **Cards** Drive (Drive Link Candidate, Drive Sync Log) · Templates & Settings (Drive Folder Template Item, Project Folder Google Drive Settings)

### Morning Briefing 🆕🔧  ➡️ from Core
- Doctypes: daily_briefing, briefing_recipient · Web: `/wall` (stays in www/, linked)
- Sidebar: **Shortcuts** Wall / TV Display (/wall) · Daily Briefing — **Cards** Briefing (Daily Briefing, Briefing Recipient)

### Asset Management 🆕🔧  ➡️ asset_booking (from Core)
- Code: api/booking.py · Doctypes: asset_booking (submittable; asset/location/booking_type/from–to datetime + map)
- Sidebar: **Shortcuts** Asset Booking (New) · Asset Booking (List) — **Cards** Bookings (Asset Booking) · (future: booking calendar page)
- Note: `patches/migrate_assets_to_serial_no.py` and hooks.py reference it — fix on move.

### Process Documentation 🆕🔧  ➡️ process_document (from Core)
- Doctypes: process_document (title, mermaid_code, diagram)
- Sidebar: **Shortcuts** Process Document — **Cards** Documentation (Process Document)
- Note: distinct from Project's PRO-0204 hand-off engine (`process_steps.py` / Process Step Template) which stays in Project Enhancements.

### Travel Management ✅  ➡️ expense_claim_type (from Core)
- Existing workspace; add an Expense Claim Type link. Keeps itinerary/travel_guidelines www + 3 reports.

### Sapphire Maintenance ✅
- Existing workspace; no structural change.

### Enhancements Core 🔧  ⬅️ many leave  ➡️ additional_supplier_group, directory_link_exclusion (from Global)
- Doctypes (after): erpnext_enhancements_settings, enhancement_desk_shortcut (+role,+user), process_step references stay in Project, job_interval, status_alert_recipient, collab_doctype, user_form_draft, **+ additional_supplier_group, directory_link_exclusion**
- Pages: none (becomes the settings/infra home)
- Sidebar: **Shortcuts** ERPNext Enhancements Settings — **Cards** Settings (Settings) · Desk Shortcuts (Enhancement Desk Shortcut) · Automation (Job Interval, Status Alert Recipient) · Misc (User Form Draft, Additional Supplier Group, Directory Link Exclusion)
- (collab_doctype is a child table of Settings — no own link.)

## Moves summary

**Doctypes**
- Core → Project: project_note, project_reminder_email *(no external importers — clean)*
- Core → AI Governance: triton_settings *(self-ref only)*, training_insight
- Core → Workforce: time_kiosk_log, time_kiosk_settings *(1 importer: api/time_kiosk.py)*
- Core → Integrations: ga4_settings *(clean)*
- Core → Morning Briefing: daily_briefing, briefing_recipient
- Core → Travel: expense_claim_type
- Core → Asset Management: asset_booking *(importers: api/booking.py, hooks.py, patches/migrate_assets_to_serial_no.py)*
- Core → Process Documentation: process_document
- Global → AI Governance: triton_assistant_settings, triton_allowed_user
- Global → Core: additional_supplier_group, directory_link_exclusion
- CRM → Google Drive: drive_link_candidate, drive_sync_log, drive_folder_template_item, project_folder_google_drive_settings *(heaviest — importers in hooks.py, api/, tests, drive_*.py)*

**Pages**
- Core → Workforce: time_kiosk, location_timeline
- Core → Integrations: ga4_dashboard, integrations_health
- CRM → Google Drive: drive_link_manager

**Web pages (www/) stay put** — `/kiosk`, `/wall`, `/itinerary`, `/travel_guidelines` are app-level routes; only *linked* from the relevant sidebar.

**Stays in Core:** collab_doctype (child table of Settings).

## Migration mechanics (per move)

For each DocType/Page moved between modules:
1. Edit the `"module"` field in its `.json`.
2. Move the folder to the destination module dir.
3. Fix controller **import dotted paths** wherever referenced (grep first — see Moves summary hotspots).
4. Add a **patch** to update existing installs: `frappe.db.set_value` on `tabDocType.module` / `tabPage.module`, then `frappe.reload_doc`; register in `patches.txt`.
5. Verify `hooks.py` (doc_events, scheduler_events, overrides, website routes) still resolves.

New modules: add to `modules.txt`; create `<module>/__init__.py` + `doctype/`, `page/`, `workspace/` dirs; Module Def auto-creates on migrate.

Workspaces: create `<module>/workspace/<slug>/<slug>.json` (Workspace doctype, `is_standard: 1`) — pattern in `sapphire_maintenance/workspace/`. Links reference page route-slugs and doctype names — both stable across module moves.

Retire Global Enhancements: after its 4 doctypes move out, remove from `modules.txt` + patch to delete the orphaned Module Def.

Fixtures: workspaces are `is_standard` (module folders), not in `fixtures/`. Check `fixtures/number_card.json` / `dashboard*.json` for `module` references to renamed modules.

## Suggested PR sequence (each: moves + workspace + version bump)

1. **Devices sidebar** (your example; no moves) — one workspace under Device Management covering MDM too.
2. **No-move sidebars** — Inventory, Task, CRM (pre-drive-split).
3. **QuickBooks split** — rename → QuickBooks Online; create QuickBooks Time; 2 sidebars.
4. **Workforce** — move time_kiosk/location_timeline pages + 2 doctypes; sidebar.
5. **Integrations** — move ga4_dashboard/integrations_health + ga4_settings; sidebar + hub links.
6. **Google Drive** — move page + 4 doctypes + drive_*.py; fix importers; sidebar. *(heaviest)*
7. **Morning Briefing** — move daily_briefing/briefing_recipient; sidebar.
8. **Project consolidation** — move project_note/reminder; Project sidebar.
9. **AI consolidation** — move Triton doctypes + training_insight; update AI Governance sidebar.
10. **Asset Management** — move asset_booking + api/booking.py; sidebar.
11. **Process Documentation** — move process_document; sidebar.
12. **Travel** — move expense_claim_type; add link to existing Travel sidebar.
13. **Retire Global → Core** — move 2 doctypes; remove module; final Core sidebar.

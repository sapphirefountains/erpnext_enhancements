# `api/` — Whitelisted HTTP endpoints

This package holds the app's Frappe **whitelisted endpoints** (`@frappe.whitelist()` functions reachable over HTTP), plus a handful of doc-event hooks, background workers, and scheduler jobs that live alongside them. Endpoints are called from the app's desk client scripts (`public/js/...`), the standalone Time Kiosk PWA, desk Pages, and external server-to-server webhooks (Twilio / the "Triton" telephony gateway).

Every function is documented inline. This README is the map.

> ⚠️ **Mixed indentation:** most files in this folder use 4-space indentation, but `analytics.py`, `collab.py`, `comments.py`, `user_drafts.py`, and `integrations_health.py` use **tabs**. Match the file you are editing.

## File map

| File | Purpose | Key whitelisted functions | Called by | External services |
|---|---|---|---|---|
| `activity_log.py` | Timeline badge counts for a document | `get_activity_counts` | `public/js/activity_log_numbering.js` | — |
| `analytics.py` | GA4 + Search Console dashboard data | `get_ga4_data`, `get_gsc_data` | `enhancements_core/page/ga4_dashboard/ga4_dashboard.js` | Google Analytics 4 Data API, Google Search Console API |
| `booking.py` | Composite (Travel/Rental/Maintenance) asset booking | `create_composite_booking` | booking UI / client script | — |
| `briefing.py` | Per-user Morning Briefing: weekday 06:30 cron pre-generates + caches one briefing per recipient (Daily Briefing doctype) — tasks/calendar/pipeline/ToDos narrated by Gemini with a deterministic markdown fallback; optional per-recipient email | `get_morning_briefing` (`force=1` regenerates); cron `scheduled_briefing_run` → bg `generate_briefings_for_all_users`; daily `purge_old_briefings` | "Morning Briefing" Custom HTML Block | Vertex AI (via `gemini.py`) |
| `call_intelligence.py` | Upserts AI post-call analysis onto the stock **Call Log** (docname == Twilio SID): sentiment, escalation risk, follow-ups, topics, compliance flags, CSAT, IVR intent, agent; idempotent, partial updates never blank fields | `process_call_intelligence`; helper `upsert_call_log` (called by `telephony.process_unified_recording`) | external Triton gateway webhook | — |
| `collab.py` | Live collaborative editing relay — validates and re-publishes field changes + per-field focus presence to the document's realtime room; never writes to the DB | `broadcast_field_update`, `broadcast_focus` | `public/js/collab/live_form_sync.js` | — |
| `comments.py` | Custom comment CRUD + file linking (backs the Vue Comments App) | `get_comments`, `add_comment`, `update_comment`, `delete_comment`, `link_files_to_comment` | `comments.js`, `global_comments.js`, `crm_note_enhancements.js` | — |
| `communication.py` | AI email/SMS reply drafting | `suggest_sms_reply`; hook `after_insert_communication`; worker `generate_draft_response` | `communication.js`; `Communication` `after_insert` hook | Vertex AI (via `gemini.py`) |
| `gantt.py` | Read-only data feed for the embeddable Gantt widget: validates the client-supplied config (doctype, field map, filters, dependency child table) against `frappe.get_meta`, then returns permission-checked rows shaped for DHTMLX (`tasks` + `links`) | `get_gantt_data` | `public/js/gantt_widget/gantt_widget.js` (any embed, e.g. the Project "Timeline" tab) | — |
| `gemini.py` | Vertex AI Gemini REST client (internal helper) | `generate_content_with_vertex_ai` | imported by `communication.py` | Vertex AI `generateContent` |
| `integrations_health.py` | System-Manager-only health snapshot of every external integration (QuickBooks token/CDC/failed-syncs, Drive configured?/sync-log failures, Triton/Twilio, Gemini, GA4/GSC) + scheduler liveness + 24 h Error Log digest; secrets read only as "configured?" booleans. DB-only on load; the one live check (`run_drive_test`) is opt-in | `get_health`, `run_drive_test` | `enhancements_core/page/integrations_health/integrations_health.js` | — (live check proxies `crm_enhancements.drive_sync.test_connection` → Google Drive API) |
| `logger.py` | Client-side error reporting sink | `log_client_error` | browser JS | — |
| `maintenance_scheduling.py` | Predictive next-visit dating: rolls Sapphire Contract Feature dates forward and mirrors them to Sales Order Items | `update_next_visit_dates` (on_submit hook), `calculate_next_date` | `Sapphire Maintenance Record` `on_submit` hook | — |
| `maintenance_visit.py` | Visit Wizard backend: bootstrap (load + server-side template instantiation), autosave with field allowlist + optimistic locking, workflow-aware finish, forward-looking "pull a future visit forward" list + create — session permissions throughout | `get_visit_bootstrap`, `save_visit`, `finish_visit`, `get_upcoming_visits` (un-drafted features due in 8–30 days), `create_visit_today` (extra one-off, `EXTRA_VISIT_LABEL`) | `sapphire_maintenance/page/visit_wizard` | — |
| `maintenance_workflow.py` | Post-submit automation (stock / timesheet / warranty claim / invoice / reading log) | `process_maintenance_submission` (bg worker) + step helpers, `resolve_consumable_warehouse`, `build_stock_entry_rows` | enqueued from the Sapphire Maintenance Record controller | — |
| `procurement.py` | Supplier purchase-link store | `get_item_links`, `save_item_link` | `procurement_links.js` | — |
| `search.py` | AwesomeBar global-search augmentation | `search_global_docs` | `erpnext_enhancements.js` | — |
| `task_dashboard.py` | Morning TV screen data: top-10 ranked projects (PM/tech lead), overdue + today's tasks with assignee names, today's public events; plus the Wall Display payload (per-project task-completion stats, wall settings, deploy version) | `get_task_dashboard_data`, `get_wall_dashboard_data` | "Task Dashboard" Custom HTML Block (`Custom HTML Block/task_dashboard.js`); `/wall` display (`public/js/wall/app.js`) | — |
| `telephony.py` | Triton/Twilio voice + SMS integration | many (see below) | external Triton/Twilio webhooks; `contact.js`/`customer.js`/`lead.js`/`telephony_client.js` | Twilio (signatures, Voice JWT), Triton gateway HTTP API |
| `maintenance_board.py` | Maintenance Day Board feed (scheduled / clocked-in / submitted today / flagged), role-gated | `get_day_board_data` | `sapphire_maintenance/page/maintenance_day_board` | — |
| `time_kiosk.py` | Time tracking + geolocation | `log_time`, `get_current_status`, `get_projects`, `get_kiosk_options`, `get_tasks_for_project`, `get_maintenance_context` (maintenance-form link + submitted-since check for the active job), `get_my_visits_today`, `get_nearby_visit` (geofenced clock-in suggestion), `link_attachment`, `log_geolocation`, `log_geolocation_batch`, `get_kiosk_bootstrap`, `get_location_history`; daily `purge_old_location_logs` | `public/js/kiosk/app.js`, `www/kiosk-sw.js`, `www/kiosk.py`, `location_timeline.js` | — |
| `travel.py` | Travel read-side: desk calendar events, `/itinerary` page data, trip-form map (Google Maps key + POIs), itinerary email trigger | `get_events`, `get_itinerary_bootstrap`, `get_my_trips`, `get_trip_itinerary` (+ reusable `shape_itinerary`), `get_trip_map_data`, `send_itinerary_email` | `public/js/travel_trip_calendar.js`, `www/itinerary.py`, `public/js/travel/itinerary.js`, `public/js/travel/travel_trip_map.js`, `public/js/travel_trip.js` | — |
| `user_drafts.py` | Per-user form autosave | `save_draft`, `delete_draft`; daily `cleanup_stale_drafts` | `erpnext_enhancements.js` | — |
| `workspace_utils.py` | Workspace shortcut helpers | `add_shortcut_to_workspace`, `get_workspaces_for_user` | `erpnext_enhancements.js` | — |

`telephony.py` whitelisted surface: `get_gateway_config`, `append_call_transcript`, `get_call_transcript`, `get_caller_info`, `update_caller_info`, `log_call_transcript`, `process_unified_recording`, `get_softphone_token`, `receive_mms`, `send_voicemail_email`, `trigger_outbound_call`, `get_employee_number`, `log_call_details`, `process_unified_sms`, `send_sms`.

## Security model

- **`allow_guest=True` (unauthenticated) endpoints:**
  - `logger.log_client_error` — writes an Error Log only; the message is untrusted.
  - `telephony.get_gateway_config` — returns only non-sensitive routing config (no secrets).
  - `telephony.append_call_transcript` / `get_call_transcript` / `get_caller_info` / `update_caller_info` / `process_unified_recording` / `process_unified_sms` and `call_intelligence.process_call_intelligence` — guarded by `@validate_webhook_secret` (Bearer shared secret).
  - `telephony.receive_mms` — guarded by `@validate_twilio_request` (HMAC signature).
- **Session-trust:** `time_kiosk` derives the Employee from `frappe.session.user` and rejects a mismatched claimed employee (`_resolve_employee`). The legacy single-point `log_geolocation` still trusts the supplied `employee` for back-compat. `travel.py` uses the same model: `get_my_trips`/`get_itinerary_bootstrap` derive the employee from the session; `get_trip_itinerary`/`get_trip_map_data` gate via `frappe.has_permission`, which the Travel Trip permission hooks scope to owner/crew/coordinators; `send_itinerary_email` lets non-coordinators send only to themselves.
- **Role-gated:** `time_kiosk.get_location_history` — only `System Manager` / `HR Manager` may view *another* employee's history; everyone else sees only their own. `integrations_health.get_health` / `run_drive_test` — `System Manager` only (`frappe.only_for`), and they return integration secrets only as `configured: true/false`, never the values.
- **Owner-gated:** `comments.update_comment` / `delete_comment` allow edits/deletes only when `comment.owner == frappe.session.user`.
- **Write-permission-gated broadcasts:** `collab.broadcast_field_update` / `broadcast_focus` require **write** permission on the specific document, enforce the settings-driven doctype allowlist (`get_collab_doctypes()` — master switch + child table on ERPNext Enhancements Settings) plus field validation and a value-size cap, and only re-publish to the doc's realtime room (whose membership Frappe's socket.io already permission-checks) — they never persist anything.
- **Permission-checked reads:** `activity_log`, `comments` read endpoints, and `search` re-check `frappe.has_permission` / filter with `ignore_permissions=False` before returning data. `gantt.get_gantt_data` treats its whole config as hostile input: `frappe.has_permission` gates the call, rows come from `frappe.get_list` (never `get_all`), every fieldname in the field map / filters / order_by must exist on `frappe.get_meta(doctype)` (start/end restricted to Date/Datetime fieldtypes; uncoercible date values skip the row instead of raising), limits clamp at 1000, and dependency links are dropped unless **both** ends are rows the permission-checked query returned.
- Telephony webhook handlers act as the service user `triton@sapphirefountains.com` and write with `ignore_permissions=True`.

## Gotchas

- `gemini.py` reads the Vertex AI key from the **`maps_api_key`** Password field of Triton Settings (shared with Google Maps) and targets a hard-coded GCP project/model (`sapphire-fountains-poseidon`, `gemini-3.1-pro-preview`, `us-central1`).
- `telephony.get_softphone_token` hard-codes the Twilio identity `"nikolas_erpnext"`.
- `telephony.get_caller_info` has a **write side effect** — it auto-creates a Customer + Contact for unknown numbers and commits, despite reading like a lookup.
- `analytics.py` requires the service-account JSON to be a **Private** file (`/private/files/...`); a public path is rejected to avoid leaking the key. Its docstrings mention raising `ValidationError`, but the code actually returns `{"error": ...}` dicts.
- `maintenance_workflow.check_warranty_and_rma` raises native **Warranty Claims** (one per failed in-warranty water feature); the old Material Request + `"WARRANTY-RETURN-PENDING"` placeholder flow is gone.
- `maintenance_workflow.create_stock_entry` skips consumable rows with qty 0 — dosing sections prefill rows at 0, so untouched chemicals never move stock.
- `workspace_utils.get_workspaces_for_user` returns only **public** workspaces.
- `maintenance_scheduling.update_next_visit_dates` runs once per maintenance submit, from the `on_submit` doc-event in `hooks.py` (the historical duplicate controller call was removed).

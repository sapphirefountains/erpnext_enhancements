# `api/` — Whitelisted HTTP endpoints

This package holds the app's Frappe **whitelisted endpoints** (`@frappe.whitelist()` functions reachable over HTTP), plus a handful of doc-event hooks, background workers, and scheduler jobs that live alongside them. Endpoints are called from the app's desk client scripts (`public/js/...`), the standalone Time Kiosk PWA, desk Pages, and external server-to-server webhooks (Twilio / the "Triton" telephony gateway).

Every function is documented inline. This README is the map.

> ⚠️ **Mixed indentation:** most files in this folder use 4-space indentation, but `analytics.py`, `collab.py`, `comments.py`, and `user_drafts.py` use **tabs**. Match the file you are editing.

## File map

| File | Purpose | Key whitelisted functions | Called by | External services |
|---|---|---|---|---|
| `activity_log.py` | Timeline badge counts for a document | `get_activity_counts` | `public/js/activity_log_numbering.js` | — |
| `analytics.py` | GA4 + Search Console dashboard data | `get_ga4_data`, `get_gsc_data` | `enhancements_core/page/ga4_dashboard/ga4_dashboard.js` | Google Analytics 4 Data API, Google Search Console API |
| `booking.py` | Composite (Travel/Rental/Maintenance) asset booking | `create_composite_booking` | booking UI / client script | — |
| `collab.py` | Live collaborative editing relay — validates and re-publishes field changes + per-field focus presence to the document's realtime room; never writes to the DB | `broadcast_field_update`, `broadcast_focus` | `public/js/collab/live_form_sync.js` | — |
| `comments.py` | Custom comment CRUD + file linking (backs the Vue Comments App) | `get_comments`, `add_comment`, `update_comment`, `delete_comment`, `link_files_to_comment` | `comments.js`, `global_comments.js`, `crm_note_enhancements.js` | — |
| `communication.py` | AI email/SMS reply drafting | `suggest_sms_reply`; hook `after_insert_communication`; worker `generate_draft_response` | `communication.js`; `Communication` `after_insert` hook | Vertex AI (via `gemini.py`) |
| `gemini.py` | Vertex AI Gemini REST client (internal helper) | `generate_content_with_vertex_ai` | imported by `communication.py` | Vertex AI `generateContent` |
| `logger.py` | Client-side error reporting sink | `log_client_error` | browser JS | — |
| `maintenance_scheduling.py` | Predictive next-visit dating: rolls Sapphire Contract Feature dates forward and mirrors them to Sales Order Items | `update_next_visit_dates` (on_submit hook), `calculate_next_date` | `Sapphire Maintenance Record` `on_submit` hook | — |
| `maintenance_workflow.py` | Post-submit automation (stock / timesheet / warranty claim / invoice / reading log) | `process_maintenance_submission` (bg worker) + step helpers, `resolve_consumable_warehouse`, `build_stock_entry_rows` | enqueued from the Sapphire Maintenance Record controller | — |
| `procurement.py` | Supplier purchase-link store | `get_item_links`, `save_item_link` | `procurement_links.js` | — |
| `search.py` | AwesomeBar global-search augmentation | `search_global_docs` | `erpnext_enhancements.js` | — |
| `task_dashboard.py` | Morning TV screen data: top-10 ranked projects (PM/tech lead), overdue + today's tasks with assignee names, today's public events | `get_task_dashboard_data` | "Task Dashboard" Custom HTML Block (`Custom HTML Block/task_dashboard.js`) | — |
| `telephony.py` | Triton/Twilio voice + SMS integration | many (see below) | external Triton/Twilio webhooks; `contact.js`/`customer.js`/`lead.js`/`telephony_client.js` | Twilio (signatures, Voice JWT), Triton gateway HTTP API |
| `time_kiosk.py` | Time tracking + geolocation | `log_time`, `get_current_status`, `get_projects`, `get_kiosk_options`, `get_tasks_for_project`, `get_maintenance_context` (maintenance-form link + submitted-since check for the active job), `link_attachment`, `log_geolocation`, `log_geolocation_batch`, `get_kiosk_bootstrap`, `get_location_history`; daily `purge_old_location_logs` | `public/js/kiosk/app.js`, `www/kiosk-sw.js`, `www/kiosk.py`, `location_timeline.js` | — |
| `user_drafts.py` | Per-user form autosave | `save_draft`, `delete_draft`; daily `cleanup_stale_drafts` | `erpnext_enhancements.js` | — |
| `workspace_utils.py` | Workspace shortcut helpers | `add_shortcut_to_workspace`, `get_workspaces_for_user` | `erpnext_enhancements.js` | — |

`telephony.py` whitelisted surface: `get_gateway_config`, `append_call_transcript`, `get_call_transcript`, `get_caller_info`, `update_caller_info`, `log_call_transcript`, `process_unified_recording`, `get_softphone_token`, `receive_mms`, `send_voicemail_email`, `trigger_outbound_call`, `get_employee_number`, `log_call_details`, `process_unified_sms`, `send_sms`.

## Security model

- **`allow_guest=True` (unauthenticated) endpoints:**
  - `logger.log_client_error` — writes an Error Log only; the message is untrusted.
  - `telephony.get_gateway_config` — returns only non-sensitive routing config (no secrets).
  - `telephony.append_call_transcript` / `get_call_transcript` / `get_caller_info` / `update_caller_info` / `process_unified_recording` / `process_unified_sms` — guarded by `@validate_webhook_secret` (Bearer shared secret).
  - `telephony.receive_mms` — guarded by `@validate_twilio_request` (HMAC signature).
- **Session-trust:** `time_kiosk` derives the Employee from `frappe.session.user` and rejects a mismatched claimed employee (`_resolve_employee`). The legacy single-point `log_geolocation` still trusts the supplied `employee` for back-compat.
- **Role-gated:** `time_kiosk.get_location_history` — only `System Manager` / `HR Manager` may view *another* employee's history; everyone else sees only their own.
- **Owner-gated:** `comments.update_comment` / `delete_comment` allow edits/deletes only when `comment.owner == frappe.session.user`.
- **Write-permission-gated broadcasts:** `collab.broadcast_field_update` / `broadcast_focus` require **write** permission on the specific document, enforce the settings-driven doctype allowlist (`get_collab_doctypes()` — master switch + child table on ERPNext Enhancements Settings) plus field validation and a value-size cap, and only re-publish to the doc's realtime room (whose membership Frappe's socket.io already permission-checks) — they never persist anything.
- **Permission-checked reads:** `activity_log`, `comments` read endpoints, and `search` re-check `frappe.has_permission` / filter with `ignore_permissions=False` before returning data.
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

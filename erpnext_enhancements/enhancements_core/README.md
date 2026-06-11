# Enhancements Core

The catch-all module. It holds the app's **Single settings doctypes**, the **Time Kiosk** data doctypes (the back-end of the `/kiosk` PWA), **Asset Booking**, and three desk **Pages** (GA4 dashboard, Location Timeline map, and a legacy time-kiosk redirect).

## File map

| File | Purpose | Key functions |
|---|---|---|
| `doctype/asset_booking/asset_booking.py` / `.js` | Submittable asset reservation, overlap guard, calendar feed | `validate`/`check_overlap`, `update_asset_status` (bg), whitelisted `check_availability`, `get_events` |
| `doctype/erpnext_enhancements_settings/…py` | App-wide Single config | (stub) |
| `doctype/expense_claim_type/…py` | Customised HR doctype controller | (stub) |
| `doctype/ga4_settings/…py` | GA4 / GSC Single config | (stub) |
| `doctype/job_interval/…py` | Clock-in **session** controller | `validate` (one open session per employee; time sanity) |
| `doctype/time_kiosk_log/…py` | Geolocation **point** doctype | (stub) |
| `doctype/time_kiosk_settings/…py` | Tracking-tuning Single | `get_settings()` (defaults-backed reader) |
| `doctype/project_note/…py` | Rich-text note child | (stub) |
| `doctype/project_reminder_email/…py` | Reminder-recipient child | (stub) |
| `doctype/triton_settings/…py` | AI-gateway / telephony Single config | `on_update` webhook, `trigger_refresh_webhook` (bg), whitelisted `get_gateway_config` |
| `doctype/training_insight/…py` / `.js` | AI training example | (stub) |
| `doctype/user_form_draft/…py` | Per-user autosaved form draft | (stub) |
| `doctype/process_document/…py` | Mermaid.js process documentation (ported from a DB-only custom DocType in v0.8.0; form script: `public/js/process_document.js`; chart content version-controlled in `setup/process_documents.py` and upserted on every migrate since v1.11.0) | (stub) |
| `page/ga4_dashboard/ga4_dashboard.js` | GA4 + GSC charts page | `on_page_load` |
| `page/location_timeline/location_timeline.js` | Leaflet map replay of kiosk points | `on_page_load` |
| `page/time_kiosk/time_kiosk.js` | Legacy redirect to the `/kiosk` PWA | `on_page_load`/`on_page_show` |

## Single settings doctypes

| Doctype | Configures | Consumed by |
|---|---|---|
| **ERPNext Enhancements Settings** | Project reminder recipients (`project_reminder_emails`) + maintenance billing defaults (`maintenance_fee_item`, `maintenance_services_group`) + live collaborative editing (`collab_enabled` master switch + `collab_doctypes` allowlist, child table **Collab Doctype**; seeded by the `seed_collab_doctypes` patch — toggle doctypes with no deploy, clients pick it up on next page load) | `project_enhancements.send_project_start_reminders`, `api.maintenance_workflow`, `api.collab` + `boot.boot_session` |
| **GA4 Settings** | `ga4_property_id`, `gsc_property_url`, attached `credentials_json` | `api.analytics` |
| **Time Kiosk Settings** | Location-tracking toggles/sampling (distance filter, heartbeat, high accuracy, min accuracy, max batch, wake lock) + `retention_days` | `api.time_kiosk`, `public/js/kiosk/geo.js` |
| **Triton Settings** | External AI/telephony gateway: URL, prompts/guidelines, model IDs, Password secrets (Maps/Vertex key, Twilio creds, admin webhook secret) | `api.telephony`, `api.gemini`, `triton_chat.py` |

> Note the two related-but-distinct Triton doctypes: **Triton Settings** (here, the gateway *connection* + secrets) vs **Triton Assistant Settings** (in [Global Enhancements](../global_enhancements/README.md), the in-app *widget* behavior).

## Time Kiosk data model

- **Job Interval** = one clock-in **session** (Employee + Project/Task, start/end, status Open/Paused/Completed, a paused-seconds accumulator, a sync block, and a start location).
- **Time Kiosk Log** = an individual geolocation/clock **point** (timestamp, log_status, GPS fix, device agent). Each Log links to its session via `job_interval` (one session → many points).

Logs are pushed in batches by [`api/time_kiosk.py`](../api/README.md), replayed on the **Location Timeline** map (grouped/colour-coded per interval), and purged after `retention_days` by the daily `api.time_kiosk.purge_old_location_logs`. Sessions are consolidated into ERPNext Timesheets by the standalone [`sync_time_kiosk.py`](../www/README.md#sync_time_kiosk-py) tool. The front-end and PWA shell live in [`public/js/kiosk/`](../public/README.md#kiosk-pwa-front-end) and [`www/`](../www/README.md).

## Desk pages

- **`ga4-dashboard`** (System Manager / Sales roles) — parallel GA4 + GSC fetch, per-section error isolation, `frappe.Chart`s + escaped HTML tables. Setup below.
- **`location-timeline`** (System Manager / HR Manager) — Leaflet map of Time Kiosk Log points via `api.time_kiosk.get_location_history`.
- **`time-kiosk`** — the retired in-desk kiosk UI; now just `location.replace('/kiosk')` to the standalone PWA (the Page record is kept solely as the redirect target).

## Google Analytics 4 & Search Console dashboard

A custom dashboard (page `ga4-dashboard`, `/app/ga4-dashboard`) that renders GA4 + GSC metrics inside ERPNext. Backed by [`api/analytics.py`](../api/README.md).

### Setup

1. **Create a Google Cloud service account** (IAM & Admin → Service Accounts) and download a JSON key.
2. **Grant access in GA4** — Admin → Property Access Management → add the service-account email with the **Viewer** role.
3. **(Optional) Grant access in Search Console** — Settings → Users and permissions → add the same email (Restricted or Full).
4. **Configure ERPNext** — open **GA4 Settings** (a Single doctype):
   - `GA4 Property ID` — from GA4 Admin → Property Settings.
   - `GSC Property URL` — your exact GSC property string (e.g. `https://www.example.com/` or `sc-domain:example.com`).
   - `Credentials JSON` — attach the downloaded key. **Check "Is Private"** so the file lands in the private files directory (`api/analytics.py` rejects a public path to avoid leaking the key).
5. Open the dashboard by searching **ga4-dashboard** or visiting `/app/ga4-dashboard`.

### What it shows

Traffic timeline (Active Users / Sessions, 30 days), acquisition channels (donut), conversions per event (bar), device breakdown (donut), top countries (bar), top pages (table) — plus, from Search Console: search-performance timeline (Clicks/Impressions), top queries, and top landing pages (Clicks/Impressions/CTR/Position).

Read access is granted to **System Manager**, **Sales User**, **Sales Manager**.

### ⚠️ API quota note

Each dashboard load fires ~6 GA4 + ~3 GSC requests concurrently. Concurrency improves load time, but heavy interactive use across the team will exhaust Google API quotas. If you hit rate-limit errors, refactor to a scheduled job that caches GA4/GSC data into a custom doctype and have the dashboard read from MariaDB instead.

## Gotchas

- `update_asset_status` writes Asset fields via `frappe.db.set_value`, intentionally bypassing Asset write-permissions.
- `Triton Settings.get_gateway_config` returns **decrypted** secrets (System Manager only).
- `dashboard_overrides.py` (Employee dashboard) and the GA4/Triton controllers use 4-space indentation; most other files here use tabs. Match the file.

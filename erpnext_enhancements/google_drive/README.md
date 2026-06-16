# Google Drive

Google Drive integration for the ERPNext customizations: per-project/customer folder provisioning, two-way attachment sync, and a System-Manager bulk folder-linking tool. Triggered from CRM (`crm_enhancements.api` provisions a folder tree when an Opportunity becomes a Project) and from the Drive-folder button on Customer/Opportunity/Project forms.

> Split out of `crm_enhancements` (v1.40.0). The CRM module still *triggers* provisioning via `crm_enhancements.api` → `google_drive.drive_utils`; the Drive machinery lives here.

## File map

| File | Purpose | Key functions | Wiring |
|---|---|---|---|
| `drive_utils.py` | Google Drive v3 API wrappers + folder provisioning | `get_drive_service`, `create_folder`, `find_folder`, `rename_folder`, `create_project_subfolders`, `provision_project_folders`, `provision_project_folder_for_opportunity`, `provision_customer_folder`, `provision_opportunity_folder`, `enqueue_opportunity_folder`, `enqueue_customer_folder` | called by `crm_enhancements.api` background worker; Opportunity/Customer `after_insert` |
| `drive_sync.py` | Two-way attachment sync (ERPNext↔Drive) | `on_file_attached`, `upload_attachment_to_drive`, `sync_shadow_attachments` (hourly, recursive), `retry_failed_syncs`, `test_connection`/`backfill_drive_links` (whitelisted) | `File` `after_insert`; hourly + daily scheduler |
| `drive_link_manager.py` | System-Manager bulk folder-linking backend (scan → review → apply) | `scan_drive_links`, `get_candidates`, `set_decision`, `bulk_decision`, `search_folders`, `apply_links` (whitelisted, System-Manager-only) | Desk page `/app/drive-link-manager` |
| `drive_match.py` | Pure fuzzy matcher (no frappe) ranking folders to records | `normalize`, `similarity`, `tier_for_score`, `best_matches` | used by `drive_link_manager`; unit-tested in `tests/test_drive_match.py` |
| `doctype/project_folder_google_drive_settings/*` | Single settings doctype — `service_account_json`, `shared_drive_id` | `ProjectFolderGoogleDriveSettings` | — |
| `doctype/drive_link_candidate/*` | Staging row for Drive Link Manager (suggestion + alternatives + decision + status) | `DriveLinkCandidate` (pass) | created by `scan_drive_links`, consumed by `apply_links` |
| `doctype/drive_sync_log/*` | Audit log for every Drive automation action | `DriveSyncLog` (pass) | written by `drive_sync` / `drive_utils` / `drive_link_manager` |
| `doctype/drive_folder_template_item/*` | Child table — folder-tree template rows | — | — |
| `page/drive_link_manager/*` | System-Manager dashboard to review + apply folder links (`/app/drive-link-manager`) | scan / review / apply UI | calls the `drive_link_manager` whitelisted API |

## Google Drive integration

- **Settings** — `Project Folder Google Drive Settings` (Single) stores `service_account_json` (Google service-account key) and `shared_drive_id` (target Shared Drive ID).
- **Auth** — `get_drive_service()` parses the JSON, builds Drive v3 credentials scoped to `https://www.googleapis.com/auth/drive`, and returns `(service, shared_drive_id)`.
- **Opportunity-stage folder** — for a Customer-party Opportunity (when *Create Opportunity Folders* is enabled), `provision_opportunity_folder` creates `<Shared Drive>/<customer name>/<Opportunity ID> - <Opportunity Name>/` — e.g. `CRM-OPP-2026-00112 - Smith Residence`, where the name comes from `custom_opportunity_name` (falling back to `title`, then the bare ID).
- **Trigger** — when `crm_enhancements.api.create_project_from_opportunity_background` finishes creating a Project that has no `custom_drive_folder_id` yet, it calls `provision_project_folder_for_opportunity(opp_folder_id, project_folder_name, party_name)`.
- **Folder reused/created** — the project folder is named `<project id> - <name>` (e.g. `PRJ-00123 - Smith Residence`). If the source Opportunity already had a folder (`Opportunity.custom_drive_folder_id`), **that folder is renamed in place** (`CRM-OPP-2026-00112 - Smith Residence` → `PRJ-00123 - Smith Residence`) so files uploaded during the opportunity stage carry over; a stale id (404) falls back to a fresh tree. The standard subfolders `Accounting & Legal`, `Build`, `Design`, `Project Management` (and a nested `Pictures` under Project Management) are then find-or-created inside it. The customer folder is reused if it already exists (`find_folder` first).
- **Result handling** — the returned folder id is saved to `Project.custom_drive_folder_id` (via `db_set`); the `webViewLink` is attached to the Project as a public File. Drive failures are caught and logged so they never abort Project creation; success/failure is reported over the `project_creation_status` realtime channel.
- **Resilience** — `create_folder` retries up to 5× with exponential backoff on HTTP 403/429; `find_folder` retries once after 2s; single quotes in folder names are escaped to avoid Drive query-syntax errors.

> `custom_drive_folder_id` is a hidden Custom Field on Project managed by the app fixtures ([`../fixtures/custom_field.json`](../fixtures/custom_field.json)), synced on migrate.

## Drive Link Manager (`/app/drive-link-manager`)

System-Manager dashboard for the one-time job of linking *existing* Drive folders to records that pre-date the provisioner (or were created outside it). A **scan → review → apply** flow:

- **Scan** (`scan_drive_links`) lists every Shared-Drive folder once, then fuzzy-ranks candidates for each unlinked Customer / Project / Opportunity. Matching is hierarchy-aware (customers ↔ root folders; projects/opportunities ↔ their customer-folder's children, widening to the whole drive on a weak match). Scoring is the frappe-free `drive_match.py` (difflib ratio + token overlap + containment, with record-id prefixes stripped), bucketed **High / Medium / Low / None**.
- **Review** stages results as `Drive Link Candidate` rows — High-tier pre-approved, the rest Pending. The page lets you accept, pick an alternative, live-search Drive (`search_folders`), choose **Create New**, or reject; with conflict flagging when one folder is chosen twice. Decisions persist via `set_decision` / `bulk_decision`.
- **Apply** (`apply_links`) links each approved row **independently in its own try/except** (one failure never blocks the rest), writing `custom_drive_folder_id` or provisioning via `drive_utils`, and logging every outcome to Drive Sync Log. Re-scanning is safe — it clears un-applied rows but keeps `Linked` ones. After linking, the two-way sync in `drive_sync.py` takes over.

## Gotchas

- Google Drive provisioning is **non-fatal** — the Project is created even if Drive fails; the user is told via the realtime payload.
- Folder names are environment-specific (Sapphire Fountains' standard project structure).
- The scan/match is indexed/blocked/batched (token inverted index, per-customer folder cache, 200-row insert batches) to survive real-size datasets — see `drive_match.token_index` / `blocked_candidates`.

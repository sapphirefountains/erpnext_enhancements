# CRM Enhancements

Customizes the **Opportunity** doctype and integrates **Google Drive**: when an Opportunity is converted to a Project, the module provisions a per-project folder tree on a shared Drive and links it back to the Project.

## File map

| File | Purpose | Key functions | Wiring |
|---|---|---|---|
| `api.py` | Opportunity→Project conversion + tag sync | `enqueue_project_creation` (whitelisted), `create_project_from_opportunity_background`, `sync_opportunity_tags`, `sync_opportunity_tags_for_existing` (whitelisted) | `sync_opportunity_tags` → `Opportunity` `before_save` |
| `drive_utils.py` | Google Drive v3 API wrappers | `get_drive_service`, `create_folder`, `find_folder`, `provision_project_folders` | called by the background worker in `api.py` |
| `doctype/project_folder_google_drive_settings/*.py` | Single settings doctype controller | `ProjectFolderGoogleDriveSettings` | — |
| `doctype/accounts_lead`, `accounts_opportunity`, `accounts_project`, `lead_source`, `opportunity_contributor`, `value_stream`, `value_streams` | CRM child tables / masters ported from DB-only custom DocTypes (v0.7.0) so fresh installs can import the Custom Field fixtures that reference them | stub controllers | synced on migrate |
| `doctype/sales_activity_settings/…py` | Single: global `inactivity_threshold` (days) — fallback reminder window for `script_migrations.customer.customer_inactivity_reminder` (ported v0.8.0) | `SalesActivitySettings` (pass) | synced on migrate |
| `page/sales_pipeline/*` | TV-friendly realtime funnel board (`/app/sales-pipeline`, v1.2.0) | `get_pipeline_data`, `check_permission` (whitelisted); `stamp_stage_change`, `publish_pipeline_update` | hooks → `Opportunity` `before_save` / `on_update`; see below |

Related client-side code lives in `public/js/crm_enhancements/` (`opportunity.js`, `opportunity_list.js`, `opportunity_kanban_totals.js`, `opportunity_migrated_scripts.js`) — see the [public README](../public/README.md#crm-enhancements).

## Sales Pipeline page (`/app/sales-pipeline`)

The wall-TV funnel board from the Jun 9 process meeting. Columns mirror the live
`Opportunity.status` options (meta-driven — a stage rename reshapes the board without a
deploy), plus a green **Won — awaiting project** column (Closed Won with empty
`custom_created_project`, the PRO-0204 Step 1→2 gap) and a muted **On Hold** column.
Cards age by `custom_stage_changed_on` (stamped on every status change; backfilled from
`modified` by the `backfill_stage_changed_on` patch) and "light up" amber/red past the
thresholds in **ERPNext Enhancements Settings → Sales Pipeline Dashboard** (defaults
7/14 days; the won column runs a tighter 1/3-day clock to match the unconverted nag).
Refreshes via the `sales_pipeline_updated` realtime event on every Opportunity save,
with a 5-minute poll as kiosk fallback. **TV mode** (`/app/sales-pipeline/tv`, or the
header button) hides desk chrome and scales type — point the Raspberry Pi at the `/tv`
route. Access is page-level (shared portfolio display, like the Project Dashboard): a
`Custom Role` record for page `sales-pipeline` wins if present, else any staff role in
`DEFAULT_ROLES`; data is then fetched permission-free so User Permissions can't
silently empty the board.

## Google Drive integration

- **Settings** — `Project Folder Google Drive Settings` (Single) stores `service_account_json` (Google service-account key) and `shared_drive_id` (target Shared Drive ID).
- **Auth** — `get_drive_service()` parses the JSON, builds Drive v3 credentials scoped to `https://www.googleapis.com/auth/drive`, and returns `(service, shared_drive_id)`.
- **Trigger** — when `create_project_from_opportunity_background` finishes creating a Project that has no `custom_drive_folder_id` yet, it calls `provision_project_folders(project_name_full, party_name)`.
- **Folder tree created** — `<Shared Drive>/<customer name>/<project id + name>/` with subfolders `Accounting & Legal`, `Build`, `Design`, `Project Manager` (and a nested `Pictures` under Project Manager). The customer folder is reused if it already exists (`find_folder` first).
- **Result handling** — the returned folder id is saved to `Project.custom_drive_folder_id` (via `db_set`); the `webViewLink` is attached to the Project as a public File. Drive failures are caught and logged so they never abort Project creation; success/failure is reported to the requesting user over the `project_creation_status` realtime channel.
- **Resilience** — `create_folder` retries up to 5× with exponential backoff on HTTP 403/429; `find_folder` retries once after 2s; single quotes in folder names are escaped to avoid Drive query-syntax errors.

> `custom_drive_folder_id` is a hidden Custom Field on Project managed by the app fixtures ([`../fixtures/custom_field.json`](../fixtures/custom_field.json)), synced on migrate.

## Gotchas

- Google Drive provisioning is **non-fatal** — the Project is created even if Drive fails; the user is told via the realtime payload.
- `sync_opportunity_tags` is one of several `Opportunity` `before_save` handlers; the others are Python ports in [`script_migrations/opportunity.py`](../script_migrations/README.md).
- Folder names are environment-specific (Sapphire Fountains' standard project structure).

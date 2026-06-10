# CRM Enhancements

Customizes the **Opportunity** doctype and integrates **Google Drive**: when an Opportunity is converted to a Project, the module provisions a per-project folder tree on a shared Drive and links it back to the Project.

## File map

| File | Purpose | Key functions | Wiring |
|---|---|---|---|
| `api.py` | Opportunity→Project conversion + tag sync | `enqueue_project_creation` (whitelisted), `create_project_from_opportunity_background`, `sync_opportunity_tags`, `sync_opportunity_tags_for_existing` (whitelisted) | `sync_opportunity_tags` → `Opportunity` `before_save` |
| `drive_utils.py` | Google Drive v3 API wrappers | `get_drive_service`, `create_folder`, `find_folder`, `provision_project_folders` | called by the background worker in `api.py` |
| `doctype/project_folder_google_drive_settings/*.py` | Single settings doctype controller | `ProjectFolderGoogleDriveSettings` | — |

Related client-side code lives in `public/js/crm_enhancements/` (`opportunity.js`, `opportunity_list.js`, `opportunity_kanban_totals.js`, `opportunity_migrated_scripts.js`) — see the [public README](../public/README.md#crm-enhancements).

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

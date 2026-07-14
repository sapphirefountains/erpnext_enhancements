# WI-006: Google Drive provisioning — service account config, verification, and cross-link UX check
**Phase:** 0   **Type:** CONFIG   **Size:** S
**Blocked by:** sequencing guard with WI-002 (folder toggles OFF during QBO catch-up) and with any bulk Customer DATA imports   **Blocks:** WI-063

## Why
All provisioning code exists and is wired: project folders via the Closed-Won handoff (`provision_project_folder_for_opportunity`), customer folders via `Customer after_insert → enqueue_customer_folder` gated by `create_customer_folders`, opportunity folders gated by `create_opportunity_folders`, attachment sync by `attachment_sync_enabled` (repo_ops). What's missing is operator configuration and a verified working state — including the documented hard requirement that the service account's client_email be a Content Manager on the Shared Drive or ALL calls fail (repo_ops, code-comment caveat).

## Native-first check
No native ERPNext Google Drive folder-provisioning exists; the custom `google_drive` module is the mechanism. Verdict: configure the existing custom module; CONFIG only.

## Preconditions
- Google Workspace admin access to the Shared Drive; the service-account JSON key.
- Agreement on the folder template (settings child table `project_folder_template`, default tree Accounting & Legal / Build / Design / Project Management/Pictures — repo_ops).

## Scope
Single `Project Folder Google Drive Settings` (fieldnames verified — repo_ops): set `service_account_json` (reqd), `shared_drive_id` (reqd); in Google admin, add the service account client_email as Content Manager on the Shared Drive. Toggle policy with explicit sequencing: `create_customer_folders` and `create_opportunity_folders` remain 0 until (a) the WI-002 QBO catch-up completes and (b) any bulk Customer/Opportunity DATA imports complete — the after_insert hooks fire per created record and would storm folder creation (the historical colon-job orphan-folder incident is exactly this failure — repo_qbo_sync). Then flip both to 1. Decide `attachment_sync_enabled` separately (File after_insert volume).

Verification battery: (1) create a throwaway Customer → folder appears, `Customer.custom_drive_folder_id` populated; (2) create a test Opportunity (Customer party) → folder + `Opportunity.custom_drive_folder_id`; (3) run one Closed-Won handoff on TEST → folder renamed to '<Project ID> - <Name>', `Project.custom_drive_folder_id` set, and the File record carrying the webViewLink is attached and OPENS from the Project form for a normal PM user (the cross-link UX check); (4) confirm Drive failures only log (never abort project creation — repo_ops).

## Acceptance criteria
- tabSingles: `service_account_json` and `shared_drive_id` non-empty.
- SQL: the three test records have non-null `custom_drive_folder_id`.
- `Drive Sync Log` (doctype exists — repo_app_inventory) shows no auth failures over 7 days; daily `retry_failed_syncs` (drive) queue empty.
- Toggle state matches the sequencing gate (0 before imports complete, 1 after).

## Rollback
Toggles to 0; created folders remain (harmless); revoke the service account's Drive membership to hard-stop.

## Explicitly NOT in this work item
Backfilling folders for the ~1,600 existing Customers (separate DATA decision — enumerate later if wanted; NOT default); call-recordings folder (`call_recordings_folder_id`) — Telephony concern.

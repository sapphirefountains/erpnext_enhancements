# WI-002: QBO OAuth reconnect and 2026 operating mode (CDC hourly, conflicts to manual review)
**Phase:** 0   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-001   **Blocks:** WI-045

## Why
QBO remains the book of record until 2027-01-01, but the sync has been dead since 2026-06-23: refresh token invalid_grant, `access_token`/`refresh_token`/`realm_id`/`token_expires_at` all NULL, status='Failed' (prod_qbo_state). App-level OAuth config is intact — `client_id`, `client_secret`, `webhook_verifier_token` set; `redirect_uri` = `https://erp.sapphirefountains.com/api/method/erpnext_enhancements.quickbooks_online.api.oauth_callback` (prod_qbo_state) — so reconnect is a user OAuth re-grant only, no credential re-entry. The fixed sync code (no-op-save-skip + `job_merge_no_project` link-only jobs, PR #571 / v1.151.4) is confirmed merged to origin/main and prod runs 1.155.0 (repo_qbo_sync, prod_finance_native), so a reconnect runs the fixed code. Every week of delay widens the gap the catch-up must cover; CDC lookback at the provider is bounded, so a full Import All may be required to converge — do it under supervision once, then settle into steady state.

## Native-first check
ERPNext has no native live QBO sync in this build (the custom `quickbooks_online` module is the existing, working mechanism; retiring it before cutover violates the migration plan, keeping it alive after violates hard rule 4). Verdict: retain the custom module strictly until cutover; nothing native reimplemented.

## Preconditions
- WI-001 acceptance met (no retriable Failed logs, no Running log).
- `erpnext_enhancements` ≥ 1.151.4 installed on prod (`tabInstalled Application` shows 1.155.0 — prod_finance_native).
- `Project Folder Google Drive Settings.create_customer_folders` = 0 during the catch-up run (any Customers the catch-up creates fire `Customer after_insert → enqueue_customer_folder`; keep folder provisioning OFF until catch-up completes — coordinate with WI-006).
- An operator with an Intuit login authorized on the Sapphire Fountains QBO realm.

## Scope
All on the Single `QuickBooks Online Settings` (fieldnames per repo_qbo_sync) + Intuit developer portal:
1. Re-grant: run the Connect/OAuth flow from the settings form; `oauth_callback` repopulates `access_token`, `refresh_token`, `realm_id`, `token_expires_at`; `status` → 'Connected'.
2. Supervised catch-up: trigger one manual `Import All` (or Run Resync) to converge the ~3-week gap; monitor the resulting `QuickBooks Sync Log` counters (`created_count/updated_count/linked_count/conflict_count/manual_review_count/failed_count`). The merged no-op-save-skip minimizes re-saves; residual changed-doc saves each enqueue one `global_triton_sync` POST — acceptable at catch-up volume, run in business off-hours.
3. Steady state config: `sync_enabled`=1, `cdc_poll_minutes`=15 (scheduler cadence is hourly — the three hooks `tasks.refresh_token_if_needed` / `tasks.cdc_poll` / `tasks.retry_failed_syncs` are hourly, repo_qbo_sync — so effective CDC cadence is hourly regardless), `retry_limit`=3, `environment`='Production', `company`='Sapphire Fountains'.
4. Webhooks: verify/recreate the Intuit webhook subscription against the prod endpoint (note `last_webhook_at` has always been empty on prod — prod_qbo_state — so confirm whether a subscription ever existed; webhooks are additive to hourly CDC, not required for correctness).
5. Operating routine through 2026 (document as a runbook step, owner: accounting): weekly review of `QuickBooks Sync Mapping` rows with `conflict_status` IN ('Conflict','Pending Review') or `match_status`='Pending Review', resolved via the dashboard actions `link_existing_record` / `preview_existing_matches` (repo_qbo_sync); weekly glance at the QuickBooks Online dashboard / `quickbooks_sync_status` tool.

## Acceptance criteria
- tabSingles `QuickBooks Online Settings`: `realm_id` NOT NULL, `status`='Connected', `sync_enabled`=1.
- `SELECT MAX(creation) FROM \`tabQuickBooks Sync Log\` WHERE status='Completed'` > reconnect date, and `last_cdc_sync` advances at least hourly for 48h.
- Catch-up log shows `failed_count`=0 or every failure triaged to a Mapping row.
- 7 days post-reconnect: `SELECT COUNT(*) FROM \`tabQuickBooks Sync Log\` WHERE status='Failed' AND creation > <reconnect date>` = 0.
- Zero new top-level Customers named with the colon-job pattern (the `job_merge_no_project` fix holding).

## Rollback
`api.disconnect` (`quickbooks_online/core/api.py:132`) — revokes at Intuit, clears tokens/`realm_id`, sets `sync_enabled`=0 — returns the system to today's state.

## Explicitly NOT in this work item
Any ERPNext→QBO write-back enablement (`Accounting Intake Settings.qbo_writeback_enabled` stays 0); opening-balance work; retirement (WI-045/WI-052); resolving pre-existing Mapping conflicts en masse (routine handles them incrementally).

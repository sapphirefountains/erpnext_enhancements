# WI-001: Reap the hung QBO sync run and neutralize the 295 Failed sync logs before reconnect
**Phase:** 0   **Type:** DATA   **Size:** S
**Blocked by:** nothing   **Blocks:** WI-002

## Why
The hourly `retry_failed_syncs` job re-runs every `QuickBooks Sync Log` with status='Failed' whose `retry_count` < `retry_limit` the moment the connection is restored (repo_qbo_sync). Prod has 295 Failed run-logs frozen since the 2026-06-23 disconnect plus one log stuck in status='Running' since 2026-06-23 08:00:16 (`QBO-SYNC-2026-206829`, sync_type='Import All', finished_at empty) (prod_qbo_state). If we re-grant OAuth first, the next hourly tick starts chewing a 3-week-stale backlog whose failures were all caused by the dead connection, wasting API quota and muddying diagnostics. Triage decision (this item decides it — it is an ops call, not an OD): **mark-obsolete, do not bulk-retry.** The failed runs are connection-failure artifacts, not per-record data failures; a single supervised catch-up sync after reconnect (WI-002) converges state strictly better than replaying 295 stale runs (the `pending`-set guard would collapse the global ops anyway, per repo_qbo_sync, but replaying is still pointless risk).

## Native-first check
No native feature exists for a custom app's sync-run ledger; this is remediation of custom-app data (`QuickBooks Sync Log` is one of the 3 sync-only doctypes, repo_qbo_sync). Verdict: DATA remediation is the only option; nothing native is being reimplemented.

## Preconditions
- `SELECT status, COUNT(*) FROM \`tabQuickBooks Sync Log\` GROUP BY status` returns Failed=295, Completed=13, Running=1 (prod_qbo_state).
- `QuickBooks Online Settings.realm_id` IS NULL (still disconnected) — this work runs BEFORE re-grant.
- `Settings.retry_limit` = 3 (default, prod_qbo_state).

## Scope
1. Reap the hung run: execute `erpnext_enhancements.quickbooks_online.core.sync.reap_stale_runs()` (verified at `quickbooks_online/core/sync.py:382`; `DEFAULT_STALE_RUN_SECONDS = 2*60*60`, so a 3-week-old Running log qualifies). This flips `QBO-SYNC-2026-206829` to Failed.
2. Mark all Failed logs obsolete so `retry_failed` skips them: `UPDATE \`tabQuickBooks Sync Log\` SET retry_count = 3 WHERE status='Failed' AND retry_count < 3` — via `frappe.db.set_value`/`frappe.db.sql` (NOT `doc.save()`), explicitly to avoid the wildcard `global_triton_sync` after_save hook firing ~296 times (the QuickBooks Online module is not in its exclusion list — verified in `utils/triton_sync.py`).
3. Record the pre-remediation counts in the run notes for audit (log names remain untouched; no deletion).

Population: exactly the rows matching `status='Failed' AND retry_count < 3` (≤295 rows) plus the single `status='Running'` row.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabQuickBooks Sync Log\` WHERE status='Running'` = 0.
- `SELECT COUNT(*) FROM \`tabQuickBooks Sync Log\` WHERE status='Failed' AND retry_count < 3` = 0.
- Total row count unchanged (309): nothing deleted.
- After the next hourly `retry_failed_syncs` execution (Scheduled Job Type last_execution advances), zero new `QuickBooks Sync Log` rows of sync_type='Retry' are created.

## Rollback
Restore prior `retry_count` values from the audit capture (`UPDATE ... SET retry_count=<original>`); reap action on the Running log is not reversible but is also the documented-correct state for an orphaned run.

## Explicitly NOT in this work item
OAuth re-grant (WI-002); resolving `QuickBooks Sync Mapping` conflict/manual-review rows (WI-002 operating routine); deleting any sync log/raw payload data.

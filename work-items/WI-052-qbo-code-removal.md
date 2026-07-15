# WI-052: Remove QBO scheduler hooks and retire/tolerate QBO-dependent surfaces
**Phase:** 2   **Type:** APP_CODE   **Size:** M
**Blocked by:** WI-045 (acceptance met and stable for ≥30 days)   **Blocks:** nothing

## Why
After disconnect, the surfaces that call the live QBO API go stale or error: the 'QuickBooks Online' Dashboard, QuickBooks sync-health Dashboard Charts and Number Cards (all in the hooks.py fixtures allowlists — repo_app_inventory), `assistant_tools/quickbooks_sync_status.py`, `api/integrations_health.py` QBO checks, `kpi_dashboards/snapshots.py` QBO widgets, and the reconcile/compare-balances/opening-balances tools (repo_qbo_sync retirement analysis). Leaving three dead hourly scheduler jobs and error-throwing dashboards in a live ERP erodes trust in every other number on screen.

## Native-first check
Fixtures + hooks are the app's own delivery mechanism; the dashboards being retired are custom fixtures, not native reports (native Balance Sheet/P&L/GL etc. are untouched — rule 6). Verdict: APP_CODE removal in the custom app is the correct and only vehicle.

## Preconditions
- WI-045 acceptance met and stable for ≥30 days (one clean month-end without QBO).
- Confirmation from accounting that the QuickBooks Balance Comparison workflow inside Month-End Close's seeded checklist (repo_ops: default 9-task list includes a QuickBooks comparison task) has been re-pointed to bank-rec / native reports (coordinate with WI-049/WI-043).

## Scope
One release of `erpnext_enhancements` (deployed via main → Frappe Cloud):
- hooks.py: remove the 3 `scheduler_events['hourly']` entries `quickbooks_online.core.tasks.refresh_token_if_needed`, `.cdc_poll`, `.retry_failed_syncs` (repo_qbo_sync: exactly these 3, no daily/weekly QBO jobs exist).
- Make `quickbooks_sync_status` assistant tool and `integrations_health` return an explicit `{"status": "retired"}` instead of erroring (tolerance), or remove the tool registration — pick tolerance for one release, removal in a later one.
- Remove QBO widgets from `kpi_dashboards/snapshots.py`.
- fixtures: drop 'QuickBooks Online' from the Dashboard name-allowlist and the QuickBooks entries from Dashboard Chart / Number Card allowlists (repo_app_inventory lists these fixtures); add a patch to delete the now-unmanaged Dashboard/Chart/Card records on migrate.
- Retain doctypes `QuickBooks Sync Mapping`, `QuickBooks Sync Log`, `QuickBooks Raw Payload` and all their rows as inert audit data (repo_qbo_sync: sync-only doctypes, no independent business value but zero-cost audit trail). Retain `custom_qbo_id` custom fields (harmless, documents lineage).

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabScheduled Job Type\` WHERE method LIKE '%quickbooks_online%'` = 0 after migrate.
- Desk: no 'QuickBooks Online' dashboard; Finance Health / Executive Summary dashboards load without errors.
- `quickbooks_sync_status` tool returns the retired sentinel, not a traceback.
- Sync ledger tables still present with pre-kill row counts.

## Rollback
Revert the release commit; hooks and fixtures reapply on the next migrate (fixtures are the version-control mechanism — repo_app_inventory).

## Explicitly NOT in this work item
Deleting the quickbooks_online module wholesale (defer; the mapping ledger backs audit queries); touching QuickBooks Time (the `quickbooks_time` webhook is WI-046's to retire alongside QB Time itself).

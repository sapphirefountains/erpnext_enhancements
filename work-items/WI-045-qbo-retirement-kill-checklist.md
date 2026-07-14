# WI-045: QBO retirement kill checklist at cutover (webhook step early, Disconnect last)
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-035 (for the final Disconnect step; the early webhook-deletion step is gated only on WI-002's machinery + the final CDC/Import — see Scope); OD-5 (treated fixed at 2027-01-01)   **Blocks:** WI-052

## Why
Nothing keeps QuickBooks alive past cutover (hard rule 4). The kill must be COMPLETE: `sync_enabled`=0 alone only pauses the scheduled CDC poll — it does NOT stop inbound webhooks (`webhooks.handle_webhook` never checks it), manual `import_all`/`sync_entity`/resync paths, or token refresh; the true full kill is Disconnect, which revokes tokens at Intuit, clears `realm_id`, and sets `sync_enabled`=0, after which `ensure_connected` throws and every path no-ops (repo_qbo_sync, verbatim). The Intuit-side webhook subscription must also be removed so Intuit stops POSTing at all.

**Cutover-window ordering is binding (review correction C5)** — this item's two halves execute at OPPOSITE ends of the window: final CDC/Import → `sync_enabled`=0 + Intuit webhook subscription DELETED (partial kill; OAuth tokens stay ALIVE) → WI-028 delete → WI-029 CoA rebuild → WI-030/WI-031 → WI-032 opening TB (uses the live QBO API — this is WHY the tokens must survive the partial kill) → WI-033/WI-034 → WI-035 → THEN this item's full Disconnect. The webhook-deletion step executes EARLY (it is WI-028's precondition); the Disconnect step executes LAST.

## Native-first check
N/A native feature — this is decommissioning of the custom integration. Verdict: procedure item; nothing reimplemented.

## Preconditions
- For Stage A (partial kill): final 2026 QBO postings complete and a last successful CDC/Import run has landed them in ERPNext (`last_cdc_sync` ≥ final QBO posting timestamp).
- For Stage B (full Disconnect): WI-035 opening reconciliation signed off — Opening Entry posted on prod by the Finance workstream (mirroring the tested pattern: TEST has submitted Opening Entry $909,722.12 @ 2025-12-31 — test_vs_prod) and the WI-032 opening trial-balance pull (which uses the live QBO API) complete.
- QBO set to read-only for all users on the Intuit side (org admin action).

## Scope
Ordered checklist (all against prod):

**Stage A — partial kill, executes EARLY in the cutover window (precondition to WI-028):**
1. Run one final manual sync (Entity Sync/CDC) and verify `failed_count`=0.
2. Set `sync_enabled`=0 (pauses the scheduled CDC poll only; OAuth tokens and `realm_id` stay ALIVE so WI-032's opening tools can still call the live QBO API).
3. Delete the webhook subscription for the prod endpoint in the Intuit developer portal (webhooks bypass `sync_enabled` — verified kill-switch nuance, repo_qbo_sync; skipping this step leaves a live inbound path during the WI-028/WI-029 data windows).

**Stage B — full kill, executes LAST (after WI-035 sign-off):**
4. Execute `erpnext_enhancements.quickbooks_online.core.api.disconnect` — revokes at Intuit, clears `access_token`/`refresh_token`/`realm_id`, sets `sync_enabled`=0, `status`='Not Connected' (repo_qbo_sync).
5. Verify `Accounting Intake Settings.qbo_writeback_enabled` = 0 (default OFF — repo_payments); the 'Push to QuickBooks' button path dies with the tokens regardless.
6. Neutralize any residual Failed logs exactly as in WI-001 step 2 (so hourly `retry_failed_syncs` — still hooked until WI-052 — has nothing to chew; with `realm_id` NULL its runs would only error).
7. The three hourly hooks remain in hooks.py until WI-052; they no-op/early-return disconnected (`cdc_poll` gated by `sync_enabled`+`realm_id`; refresh/retry check `realm_id` — repo_qbo_sync; and prod_qbo_state confirms 3 weeks of disconnected hourly executions produced zero new sync logs).

## Acceptance criteria
- After Stage A: `sync_enabled`=0, Intuit developer portal shows no active webhook subscription for erp.sapphirefountains.com, and `realm_id`/tokens still populated (WI-032 can pull).
- After Stage B: tabSingles `realm_id` IS NULL, `sync_enabled`=0, `status`='Not Connected'.
- `SELECT COUNT(*) FROM \`tabQuickBooks Sync Log\` WHERE creation > <disconnect timestamp>` = 0 after 7 days.
- `QuickBooks Sync Mapping` / `Sync Log` / `Raw Payload` row counts unchanged post-kill (audit data retained).

## Rollback
Re-grant OAuth exactly as WI-002 (client credentials remain stored); recreate the Intuit webhook subscription. Fully reversible until WI-052 removes code.

## Explicitly NOT in this work item
Code/hook removal and dashboard decommissioning (WI-052); deleting sync ledger doctypes or data; QBO subscription cancellation (business decision on retention for read-only lookups, typically ≥1 fiscal year).

# WI-045: QBO retirement kill checklist (reports-only on Jan 1, full kill after the tie-out)
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-035 sign-off (for Stage B); go-live on 2027-01-01 (for Stage A)   **Blocks:** WI-052
**Re-sequenced 2026-11-25.** The sync is no longer cut early. The Aug 31 plan deleted the Intuit webhook and set `sync_enabled=0` at the *start* of the cutover window, to protect a draft delete that has since been canceled (WI-028). Now the CDC sync **stays on through the December close**, because QuickBooks' late December entries must reach ERPNext as drafts for Wave 3 (WI-051 runbook §3). Stage A on Jan 1 only restricts QuickBooks users; Stage B, the whole kill, runs on **2027-01-29** after the WI-035 sign-off. ERPNext Task: TASK-2026-00938.

## Why
Nothing keeps QuickBooks alive past cutover (hard rule 4). The kill must be COMPLETE: `sync_enabled`=0 alone only pauses the scheduled CDC poll — it does NOT stop inbound webhooks (`webhooks.handle_webhook` never checks it), manual `import_all`/`sync_entity`/resync paths, or token refresh; the true full kill is Disconnect, which revokes tokens at Intuit, clears `realm_id`, and sets `sync_enabled`=0, after which `ensure_connected` throws and every path no-ops. The Intuit-side webhook subscription must also be removed so Intuit stops POSTing at all.

The tokens must stay alive until the tie-out is signed: WI-035's `compare_account_balances` calls the live QuickBooks API.

## Native-first check
N/A native feature — this is decommissioning of the custom integration. Verdict: procedure item; nothing reimplemented.

## Preconditions
- For Stage A: go-live on Jan 1 (WI-051 runbook §5 GO).
- For Stage B: WI-035 signed off and 2026 frozen (`accounts_frozen_till_date = 2026-12-31`).

## Scope
Ordered checklist (all against prod; WI-051 runbook §7.2–§7.4 and §8.4):

**Stage A — Jan 1, at go-live:**
1. Set every QuickBooks user to **reports-only**, **except Lisa and John**, who finish the December close.
2. Leave the sync **on**: `sync_enabled=1`, hourly CDC, webhook subscription in place.
3. From Jan 1, nothing dated 2027 is entered in QuickBooks. A QuickBooks import dated 2027 duplicates an ERPNext entry: delete the ERPNext draft (never submit it) and void it in QuickBooks (WI-051 runbook §7.4).

**Stage B — Jan 29, after the WI-035 sign-off:**
4. Run one final CDC and verify it lands clean (`failed_count`=0), with no new 2026 draft after the freeze.
5. Set `sync_enabled`=0.
6. Delete the webhook subscription for the prod endpoint in the Intuit developer portal (webhooks bypass `sync_enabled`).
7. Set every QuickBooks user, Lisa and John included, to read-only.
8. Execute `erpnext_enhancements.quickbooks_online.core.api.disconnect` — revokes at Intuit, clears `access_token`/`refresh_token`/`realm_id`, sets `sync_enabled`=0, `status`='Not Connected'.
9. Verify `Accounting Intake Settings.qbo_writeback_enabled` = 0 (default OFF); the 'Push to QuickBooks' button path dies with the tokens regardless.
10. Neutralize any residual Failed logs exactly as in WI-001 step 2 (so hourly `retry_failed_syncs` — still hooked until WI-052 — has nothing to chew; with `realm_id` NULL its runs would only error).
11. The three hourly hooks remain in hooks.py until WI-052; they no-op/early-return when disconnected.

**Never on TEST:** a TEST site restored from a prod backup holds prod's tokens. There the tokens are cleared locally with `core.utils.clear_oauth_tokens`, never with `disconnect`, which would revoke prod's grant (WI-051 runbook §2.5).

## Acceptance criteria
- After Stage A: every QuickBooks user except Lisa and John is reports-only (Intuit user list on the Task); hourly CDC logs still `Completed`.
- After Stage B: tabSingles `realm_id` IS NULL, `sync_enabled`=0, `status`='Not Connected'; Intuit developer portal shows no active webhook subscription for erp.sapphirefountains.com; every QuickBooks user read-only.
- `SELECT COUNT(*) FROM \`tabQuickBooks Sync Log\` WHERE creation > <disconnect timestamp>` = 0 after 7 days.
- `QuickBooks Sync Mapping` / `Sync Log` / `Raw Payload` row counts unchanged post-kill (audit data retained).

## Rollback
Re-grant OAuth exactly as WI-002 (client credentials remain stored); recreate the Intuit webhook subscription; restore user roles in QuickBooks. Fully reversible until WI-052 removes code.

## Explicitly NOT in this work item
Code/hook removal and dashboard decommissioning (WI-052); deleting sync ledger doctypes or data; QuickBooks subscription cancellation (business decision on retention for read-only lookups, typically ≥1 fiscal year).

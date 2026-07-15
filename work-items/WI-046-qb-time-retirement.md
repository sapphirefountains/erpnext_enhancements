# WI-046: QuickBooks Time retirement — unauthenticated guest webhook removal + subscription decommission
**Phase:** 1   **Type:** APP_CODE   **Size:** M
**Blocked by:** WI-021 adoption gate (subscription retirement only — the webhook dies at cutover REGARDLESS, review correction C10)   **Blocks:** WI-051

## Why
Rule 4: nothing keeps QuickBooks alive past cutover. The QuickBooks Time inbound webhook `quickbooks_time/api.py::qb_timesheet_webhook` is `@frappe.whitelist(allow_guest=True)` (line 48) with NO signature verification (inline SECURITY note, lines 61-63 — verified) — an open, unauthenticated endpoint that creates 'Time Log' docs by resolving `custom_quickbooks_user_id`/`custom_quickbooks_jobcode_id`. That is both a rule-4 violation-in-waiting and a live security gap: anyone who discovers the URL can inject Time Log docs today. With the Time Kiosk (WI-021) as the working successor for hours capture, QuickBooks Time and its webhook are decommissioned.

## Native-first check
Native mechanism = removing the endpoint from the custom app; nothing native replaces an integration teardown. (The QBO-sync teardown — the app's own Disconnect kill switch, the 3 hourly scheduler hooks, `sync_enabled=0` — belongs to WI-045/WI-052, not here.)

## Preconditions
- Kiosk adoption metric met (WI-021) so QB Time has a working successor — this gates the SUBSCRIPTION retirement only. Per review correction C10, the unauthenticated `qb_timesheet_webhook` guest endpoint is disabled at cutover REGARDLESS of kiosk adoption (rule 4 + live security gap).
- QuickBooks Time vendor subscription cancellation date agreed with HR/payroll.
- Adoption-failure branch (review correction C10): if the QB Time subscription must linger past Jan 1, that is an explicit brief-owner-signed rule-4 exception with a Feb deadline — taken with the endpoint already dead (QB Time keeps capturing hours in its own UI; nothing flows into ERPNext).

## Scope
- Remove (or convert to hard-404) `erpnext_enhancements/quickbooks_time/api.py::qb_timesheet_webhook` in the cutover release.
- Delete the Intuit webhook subscription on the QB Time side (the QBO-side webhook subscription deletion is sequenced inside WI-045's kill checklist).
- QuickBooks Time vendor subscription cancellation executed per the agreed date (business/ops task, listed in the runbook WI-051).
- Time Log doctype note: the custom 'Time Log' doctype and its existing webhook-created documents stay in place as inert historical data; only the inbound feed is removed.
- Explicitly referenced, NOT repeated here: the three hourly QBO scheduler hooks (`refresh_token_if_needed`, `cdc_poll`, `retry_failed_syncs`), the QBO Disconnect execution, `QuickBooks Online Settings.sync_enabled=0`, and the QBO Dashboard fixture-allowlist removal are owned by WI-045 (runbook/config kill) and WI-052 (code removal).

## Acceptance criteria
- HTTP POST to `/api/method/erpnext_enhancements.quickbooks_time.api.qb_timesheet_webhook` returns 404/403 (not 200) on prod immediately after the cutover deploy.
- Repo: no `allow_guest=True` endpoint remains in `erpnext_enhancements/quickbooks_time/` (grep check in CI/review).
- `SELECT COUNT(*) FROM `tabTime Log` WHERE creation > <cutover deploy timestamp>` = 0 via the webhook path (zero new webhook-created Time Logs after cutover week).
- QB Time subscription cancellation confirmed (or the signed rule-4 exception with its Feb deadline recorded in the runbook — review correction C10).

## Rollback
Revert the api.py commit and redeploy (restores the endpoint); Intuit webhook re-subscription is manual. If retirement slips, the mandated interim hotfix is a signature check or endpoint disable — the endpoint must NOT return to its unauthenticated state.

## Explicitly NOT in this work item
QBO sync-job removal, Disconnect, sync_enabled=0 (WI-045); QBO code deletion (WI-052); QBO data reconciliation/final pull (data workstream — WI-002/WI-028 decision); deleting sync ledger data (QuickBooks Sync Mapping/Log/Raw Payload stay as audit trail); cancelling the QBO subscription itself (business/ops task, listed in runbook).

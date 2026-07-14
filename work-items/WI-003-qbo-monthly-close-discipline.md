# WI-003: Phase-0 monthly close discipline in QuickBooks Online
**Phase:** 0   **Type:** DATA   **Size:** M
**Blocked by:** nothing   **Blocks:** WI-032, WI-033, WI-034, WI-035, WI-053

## Why
Opening balances are only as good as the books they are cut from. QBO is the sole system of record until 2027-01-01 (prod ERPNext has 4 GL Entries total — prod_finance_native), so every month Aug–Dec 2026 must be formally closed in QBO (reconciled banks/CC, accruals posted, AR/AP aging reviewed, close date locked) or the 2026-12-31 Trial Balance used for the ERPNext Opening Entry will shift underneath the migration. This is a human/process deliverable but it hard-gates the entire opening-balance sequence. (Typed DATA per review correction C3: a process deliverable producing the close packages.)

## Native-first check
No ERPNext feature applies pre-cutover (the ledger lives in QBO). The ERPNext-side analogue — `Month-End Close` doctype freezing `Company.accounts_frozen_till_date` (enhancements_core/doctype/month_end_close, verified unwired into hooks) — is adopted POST-cutover in WI-049; it is not used here. Verdict: process work, nothing to build.

## Preconditions
- QBO subscription active and accessible to the finance team (independent of the broken ERPNext OAuth — prod_qbo_state confirms only the ERPNext-side refresh token is dead).
- A named close owner and an external-reviewer role exist (the app's Month-End Close DEFAULT_TASKS names Lisa Symanski / John Juntunen as responsibles — reuse those assignments as the starting draft).

## Scope
- Write the monthly close checklist as a Wiki page (wiki app 3.0.0 is installed on prod — prod_finance_native), modeled on the 9 seeded tasks in `erpnext_enhancements/enhancements_core/doctype/month_end_close/month_end_close.py` DEFAULT_TASKS (reconcile bank/CC, accruals, AR/AP aging review, P&L/BS review, external accountant review, approve statements), minus the ERPNext-specific "Reconcile vs QuickBooks Balance Comparison" task.
- Execute the close monthly for periods 2026-08 through 2026-11; advance QBO's "Close the books" date each cycle with a password.
- Final close of 2026-12 (target completion by 2027-01-15) produces the frozen Trial Balance, AR aging detail, AP aging detail, open-PO list, and bank/undeposited-funds/Stripe-in-transit balances as of 2026-12-31 — the source artifacts for WI-032/WI-033/WI-034.

## Acceptance criteria
- Wiki page for the close checklist exists and is linked from the Finance workspace.
- For each month Aug–Dec 2026: QBO "Close the books" date >= that month-end (evidenced by screenshot/export archived per cycle).
- The 2026-12-31 close package (TB, AR aging, AP aging, open POs, bank recs) is exported to files and archived before WI-032 runs.

## Rollback
None needed (process). QBO close date can be re-opened by an admin with the close password if a correction is required; any re-open after WI-032 posts forces a re-run of WI-035.

## Explicitly NOT in this work item
Any ERPNext configuration; the post-cutover ERPNext close process (WI-049); QBO reconnection of the ERPNext sync (WI-002, sync workstream).

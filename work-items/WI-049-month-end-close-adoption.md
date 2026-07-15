# WI-049: Month-End Close adoption — decision, December dry-run, first live close February 2027
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-011, WI-035   **Blocks:** nothing

## Why
The custom Month-End Close doctype (child Month-End Close Task — exact doctype names hyphenated, verified on prod; review correction C14) is fully built — submit requires all tasks Done/N-A, Accounts Manager only, on_submit sets `Company.accounts_frozen_till_date = period_end_date` (stashing `previous_frozen_till_date` for exact restore on cancel) and forces `role_allowed_for_frozen_entries='Accounts Manager'` (verified in month_end_close.py) — but it is wired into NO hook/scheduler and has never been decided into or out of the process (repo_app_inventory §7, repo_ops §6). January 2027 is the first month-end in ERPNext; drifting into it without a period-lock discipline invites back-dated edits into closed periods. Post-cutover, the close discipline built in WI-003 must move into ERPNext with teeth.

## Native-first check
Native alternatives: manual `Company.accounts_frozen_till_date` + `role_allowed_for_frozen_entries` (ERPNext's GL freeze — exactly what the doctype automates) and **Period Closing Voucher** (fiscal-year P&L close — different purpose, still used at year end; remains the FY-end instrument for closing FY2027 into retained earnings, separate, scheduled Dec 2027). VERDICT: the custom doctype already exists, works standalone (no wiring needed), is a thin already-deployed checklist wrapper that drives the native freeze field; adopt as-is. Building nothing new — adopting it reimplements nothing and requires no new code.

## Preconditions
- WI-011 done (accountant holds Accounts Manager; at least one Accounts Manager role-holder confirmed).
- WI-035 signed off (opening reconciliation complete; books frozen through 2026-12-31).
- Seeded 9-task checklist (from the Finance process map, responsibles Lisa Symanski / John Juntunen — repo_ops §6) reviewed and task list updated for post-QBO reality (e.g., drop the 'QuickBooks comparison' task after retirement, add Stripe payout reconciliation once the payments workstream lands it — WI-040).

## Scope
- Decision record: Month-End Close IS the monthly close vehicle starting with the January 2027 close (period_end_date 2027-01-31, executed/submitted by ~2027-02-10) — one Month-End Close record per month per company from then on.
- Dry-run on TEST for a synthetic December 2026 period: create doc, complete tasks, submit; verify freeze.
- Task list edits per record (rows are editable): replace the seeded 'Reconcile vs QuickBooks Balance Comparison' task (obsolete once QBO is read-only) with the WI-038 sales-tax filing step and a Stripe-clearing reconciliation step; mark obsolete seeds N/A until a one-line DEFAULT_TASKS code cleanup rides a routine release (explicitly out of this item).
- SOP page in the accountant workspace (WI-018); wiki SOP page linking WI-003's close checklist to the ERPNext workflow; train the close owner.

## Acceptance criteria
- TEST dry run: after submit, `SELECT accounts_frozen_till_date, role_allowed_for_frozen_entries FROM tabCompany WHERE name='Sapphire Fountains'` → equals period_end_date / 'Accounts Manager'; a back-dated Journal Entry by a non-Accounts-Manager user is blocked.
- Cancel restores prior frozen-till date (verified behavior — repo_ops §6).
- First live close: `SELECT COUNT(*) FROM `tabMonth-End Close` WHERE docstatus=1 AND period_end_date='2027-01-31'` = 1 (by 2027-02-15).
- `SELECT accounts_frozen_till_date FROM tabCompany WHERE name='Sapphire Fountains'` = '2027-01-31' after that submit; `role_allowed_for_frozen_entries` = 'Accounts Manager'.

## Rollback
Cancel the Month-End Close doc (on_cancel restores `previous_frozen_till_date` natively); decision reversible to manual freeze-date management.

## Explicitly NOT in this work item
Scheduler/hook wiring for auto-creation (explicitly deferred; doctype works standalone); editing DEFAULT_TASKS in code (S-size APP_CODE follow-up, non-blocking); year-end Period Closing Voucher procedure/execution (finance workstream; Dec 2027 task); changing the checklist doctype.

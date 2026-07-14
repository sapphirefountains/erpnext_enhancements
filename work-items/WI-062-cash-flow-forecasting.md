# WI-062: Cash-flow projection / forecasting (native-first)
**Phase:** 2   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-035 (real ledger live) + at least 2 completed monthly close cycles   **Blocks:** nothing

## Why
Leadership wants forward cash visibility after cutover. Before anything is built, the native components must be evaluated against two months of real post-cutover data — most "we need a forecast report" demands are satisfied by the native reports once actual AR/AP with due dates exists (which QBO-era ERPNext never had).

## Native-first check
Native components verified present and enabled on prod: **Cash Flow** (script report), **Accounts Receivable / Accounts Payable** (aging with due dates — populated for the first time by WI-033's opening invoices + live 2027 documents), **Payment Ledger**, **Sales Order / Purchase Order** pipelines (open commitments), and bank balances via WI-042/043. Verdict: **evaluate native for two close cycles before ANY custom work.** If a 13-week direct-method forecast is still demanded after that, the ceiling is ONE Query Report (shipped as FIXTURE) unioning AR due dates + AP due dates + the payroll calendar + recurring JEs — and that report requires its own separate approval against rule 1 before being built. It is enumerated here, not planned.

## Preconditions
- WI-035 signed off; January and February 2027 closes completed (WI-049) so due-date data is real.
- Finance articulates the actual decision the forecast serves (hiring? purchasing timing? line-of-credit draw?) — shapes whether native views suffice.

## Scope
- A documented forecast procedure (wiki): weekly — AR report by due date bucket + AP report by due date bucket + open PO commitments + payroll calendar + current bank balances (Bank Balance Snapshot widget / WI-043 reconciled balances) → a one-page cash outlook.
- Saved report views for each component, linked from the Finance workspace.
- A written evaluation memo after cycle 2: either "native suffices — close this item" or a scoped, separately-approved Query Report proposal with the exact columns the native reports cannot produce.

## Acceptance criteria
- Procedure page exists with saved views; finance produces the weekly outlook unaided for 4 consecutive weeks.
- Back-test: the month-1 outlook reproduces month-1 actual cash movement within the tolerance finance agreed (documented).
- No custom report exists unless the evaluation memo + separate approval authorized it.

## Rollback
None needed (documentation + saved views).

## Explicitly NOT in this work item
Building the Query Report (fallback only, separate approval); Triton/AI-generated statutory or board-reported numbers (rule 7 — AI stays a management/exploratory layer); integration with external treasury tools.

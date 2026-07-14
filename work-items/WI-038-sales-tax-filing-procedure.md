# WI-038: Utah sales-tax liability reporting from native reports (OD-2 direction set)
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-036, WI-037 (OD-2 resolved in direction 2026-07-14: Utah-law stream-differentiated; filing columns split taxable vs exempt by stream)   **Blocks:** nothing

## Why
Finance must file Utah sales tax (TC-62-family) from ERPNext after cutover. The stock 'Tax Detail' report is NOT present in this build (verified missing — prod_finance_native), so the filing procedure must be designed on what IS present.

## Native-first check
Evaluated: **Sales Register** (present, enabled) — per-invoice tax amounts in per-account-head columns, filterable by period → tax collected by rate/jurisdiction; **Item-wise Sales Register** (present) — item-level tax detail with grouping for taxable-base questions; **General Ledger / Trial Balance** on the per-jurisdiction liability sub-accounts (WI-004 design) → liability balance per jurisdiction per period. Verdict: **native is sufficient for day-one filing** — because WI-036 posts each jurisdiction to its own account, the GL itself is the jurisdiction schedule; Sales Register supplies the taxable-sales base. Building a custom report now would violate rule 1; a custom query report is sanctioned ONLY if the CPA's filing workpaper demands a single combined jurisdiction×base×tax grid that the two registers cannot export (revisit as Phase 2 after two real filing cycles).

## Preconditions
- One month of live 2027 postings exists (first real filing rehearsal ~Feb 2027).

## Scope
- Document the monthly filing procedure (wiki): GL by tax sub-account for liability, Sales Register export for base, cross-foot check; save the filter presets as saved report views.
- Add the filing step to the Month-End Close checklist (WI-049).

## Acceptance criteria
- Wiki procedure exists; saved report views exist; a rehearsal filing for Jan 2027 reproduces hand-computed totals from a 10-invoice sample exactly.

## Rollback
None needed (documentation + saved views).

## Explicitly NOT in this work item
Any custom Script Report (explicitly deferred); e-filing integration; use-tax accrual reporting.

# WI-050: Cutover integration-hazard verification — global_triton_sync wildcard hook and AI write gating
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** nothing (must complete BEFORE the cutover DATA windows)   **Blocks:** advisory gate on all workstreams' bulk DATA items

## Why
Two app-level behaviors can silently distort the cutover: (1) the wildcard `'*'` after_save hook `global_triton_sync` — verified in `erpnext_enhancements/utils/triton_sync.py`: NO settings switch and NO frappe.flags guard; it enqueues one background POST per saved business document (child/single doctypes and 9 framework modules excluded; QuickBooks Online, Stripe Payments, Accounts modules NOT excluded). A cutover import that ORM-saves tens of thousands of docs floods the default RQ queue and hammers Triton. (2) AI write surfaces: 30+ assistant tools can mutate documents; the confirmation gate `ai_write_gating_enabled` on ERPNext Enhancements Settings defaults OFF (repo_app_inventory). Statutory numbers must come from native reports only (rule 6) — the risk here is unreviewed AI writes to the books, not AI reporting.

## Native-first check
N/A — this is verification/config of custom-app hooks and flags. The one native anchor: statutory outputs remain the 16 enabled native Script Reports (prod_finance_native); Triton/AI remains a management layer.

## Preconditions
- List of planned cutover bulk DATA runs from all workstreams (open AR/AP, opening entry, master remediation).

## Scope
1. Publish the DATA-run rule (into every workstream's runbook): bulk writes use `frappe.db.set_value`/SQL (bypasses doc_events) wherever business logic permits; where ORM save is required (e.g., submitting Sales Invoices), schedule off-hours, monitor the default queue, and warn Triton ops of the burst. Also re-state the two named per-doctype hazards for DATA authors: `Customer after_insert` Drive folder hook (keep `create_customer_folders`=0 during imports — WI-006 gate) and `Opportunity on_update` closed-won prompt (`prompt_create_project_on_won` fires on transition into 'Closed Won' — avoid status-touching bulk saves on Opportunity; repo_ops).
2. Optional hardening branch (only if the team wants belt-and-braces; escalates to APP_CODE): add a `frappe.flags.in_migrate`/custom-flag early-return to `global_triton_sync` — a 3-line change; enumerate but do not assume.
3. Set `ai_write_gating_enabled`=1 on prod ERPNext Enhancements Settings before cutover week, so AI-initiated writes route through the AI Pending Action confirmation flow (repo_app_inventory) during the highest-stakes period.
4. Verify Accounting Intake auto-posting posture for cutover week: intake actions create DRAFT docs only (repo_payments — all four action types create docstatus-0 records), which is already safe; note it, no change.

## Acceptance criteria
- tabSingles: ERPNext Enhancements Settings `ai_write_gating_enabled`=1 on prod.
- A test AI write on prod creates an `AI Pending Action` requiring confirmation instead of mutating directly.
- Every workstream DATA runbook carries the bulk-write rule (checklist sign-off).
- During the first bulk rehearsal on TEST: background job queue depth observed and documented; no Triton-side incident.

## Rollback
`ai_write_gating_enabled`=0 restores prior behavior; the hardening branch (if taken) reverts with the release.

## Explicitly NOT in this work item
Any Triton feature work; disabling AI tools wholesale; changing the excluded-modules list semantics beyond the optional flag guard.

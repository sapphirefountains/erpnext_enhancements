# WI-022: December parallel run, UAT scripts & user training
**Phase:** 1   **Type:** DATA   **Size:** L
**Blocked by:** Phase-0 set: WI-007, WI-008, WI-009, WI-010, WI-011, WI-012, WI-013, WI-014, WI-016, WI-017, WI-018, WI-019, WI-020; plus WI-048, WI-021   **Blocks:** WI-051

## Why
The team has never operated the O2C/purchasing/time loops in ERPNext (zero Sales Orders ever, zero Job Intervals ever). A structured December 2026 parallel run — real work shadowed in ERPNext while QBO remains the books — is the only way to surface process gaps before the books depend on it.

## Native-first check
Native ERPNext documents themselves are the test vehicle; no tooling to build. TEST site is the venue — it already models cutover (submitted Opening Entry $909,722.12 dated 2025-12-31, 157 GL entries, Stripe enabled with card+ACH+surcharge — test_vs_prod), so parallel transactions post into a realistic ledger. Prod stays clean (its GL has 4 rows and must not accrue December postings before the real Opening Entry).

## Preconditions
- All Phase-0 items deployed to TEST; feature decisions (WI-048 branch, WI-014 branch) taken.
- Pilot cohort named: 1 AE, 1 PM, 1 team lead, accountant, 3-5 field techs, CEO (for escalation tests).
- Master-data deltas on TEST vs prod acknowledged (items 266 vs 583, CoA 281 vs 359 — test_vs_prod); scripts must use entities present on TEST.

## Scope (population = the UAT script inventory below; each script = one checklist row with evidence)
- O2C per stream: Design, Build, Rent — Quotation→won→handoff Project (PRJ- name check)→SO with project→SI→(Stripe link optional); Service — Maintenance Record submit → auto-draft SI with project (api/maintenance_workflow.py:408 behavior verified live).
- Purchasing: MR by team lead → PO by PM under threshold (submits) and over threshold (blocked; CEO submits) → PI → PE through the activated approval workflow with a DIFFERENT approver (self-approval must fail).
- Time: kiosk Start/Switch/Stop day cycle → draft Timesheet → supervisor submit → Payroll Hours Export run → pilot payroll JE (WI-047) on TEST.
- Training: role-based sessions (sales, PM/field, finance) using the SOPs from WI-007/WI-012/WI-021/WI-047/WI-018; attendance recorded.
- Side-effect hygiene during UAT data creation: create test Customers sparingly (each Customer insert fires the Drive-folder hook if `Project Folder Google Drive Settings.create_customer_folders` is on — repo_ops §3; confirm the flag state on TEST first); Opportunity status flips fire the closed-won prompt by design (that IS the test); no bulk scripted saves (wildcard after_save triton sync — repo_app_inventory).

## Acceptance criteria
- UAT tracker: 100% of scripts executed, ≥95% passed, zero open Sev-1 defects.
- SQL evidence on TEST: ≥1 submitted SO per tested stream (`SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1` > 0 — table is 0 today); ≥1 PE in state Approved approved by ≠ creator (`SELECT owner, modified_by FROM `tabPayment Entry` WHERE workflow_state='Approved'`); kiosk metrics from WI-021 met.
- Training attendance = 100% of go-live users.

## Rollback
TEST-only data; bulk-cancel/delete UAT docs afterward or refresh TEST from a clean state (no prod exposure).

## Explicitly NOT in this work item
Prod data entry; opening-balance validation (finance workstream's UAT); performance testing.

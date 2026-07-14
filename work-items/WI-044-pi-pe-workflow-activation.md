# WI-044: Activate Purchase Invoice / Payment Entry approval workflows with real segregation of duties
**Phase:** 1   **Type:** FIXTURE   **Size:** M
**Blocked by:** WI-015 (Phase-0 fixture repair: allow_self_approval 1→0, dormant), WI-011 (≥2 distinct accounts users), WI-033 done (activate AFTER the bulk open-item submissions), Stripe-PE auto-submit test on TEST   **Blocks:** WI-031, WI-051, WI-059

## Why
Both accounting approval workflows ship in the app's fixtures but DORMANT and self-approvable: `Purchase Invoice Approval` and `Payment Entry Approval` have `is_active`=0 and `allow_self_approval`=1 on ALL transitions (repo_ops fixtures/workflow.json — 8 occurrences verified in the fixture file; prod/test parity confirmed — test_vs_prod). Self-approval defeats the point: the Accounts User who keys a vendor bill can approve their own payment. Vendor-bill SoD is a day-one control for the new system of record — money leaving the company needs a real two-person rule from cutover. The `allow_self_approval` 1→0 fixture repair itself is WI-015 (Phase-0 commit; workflows stay `is_active`=0 so nothing behavioral deploys early) — this item is the cutover ACTIVATION and its validation.

## Native-first check
Native Workflow engine (states/transitions/roles) — the fixtures already model Draft→Pending Approval→Approved/Rejected with Accounts User→Accounts Manager (repo_ops); fixing the flags and activating is configuration of the native feature, and building any custom approval doctype would be a defect. Evaluated Authorization Rule (present + 0 rows — prod_finance_native): suitable for value-threshold approval limits but provides no review states or audit of who approved — it is threshold-exemption logic, not a routed two-state approval; the dollar-threshold PO escalation rule is the Approvals workstream's item (WI-013, hard rule 5) and can coexist. Verdict: native Workflow via the existing FIXTURE — editing fixtures/workflow.json, never hand-clicking prod (rule 2).

## Preconditions
- WI-015 merged: `grep -c '"allow_self_approval": 1' fixtures/workflow.json` = 0 for the two finance workflows (repo check, not prod).
- The cutover DATA imports that bulk-submit Purchase Invoices/Payment Entries (WI-033) are complete (an active workflow forces state transitions on new docs; bulk importers would need workflow-state handling otherwise).
- Legacy-draft hazard scheduled: prod holds 1,405 draft Payment Entries and 1 draft Purchase Invoice (prod_customers_items); the opening-balance workstream's bulk disposition of those drafts must complete BEFORE activation, or each touched draft will enter the workflow state machine mid-bulk-run.
- ≥2 distinct humans hold Accounts User and Accounts Manager (23 enabled system users; 'Accounts'/'Finance' Role Profiles exist — prod_finance_native; mapping per WI-011). With only one accountant, name the CFO/CEO as Accounts Manager approver.
- Verified behavior on TEST: a Stripe webhook-created receive PE (submitted by the system path in reconcile.py) must still submit with the workflow active — this is the key design risk.

## Scope
Edit `erpnext_enhancements/fixtures/workflow.json` (deployed via main → migrate; fixtures are the promotion mechanism — repo_app_inventory):
- Both workflows: `is_active`=1 (the cutover release commit).
- All Approve transitions: `allow_self_approval`=0 — already shipped dormant by WI-015 (the SoD fix named in the brief); verify no drift before activating.
- `Payment Entry Approval`: add a workflow `condition` scoping it to outbound payments (payment_type='Pay') so Stripe-created 'Receive' PEs and payroll/system entries bypass approval; if the condition approach proves insufficient on TEST, the fallback branch is handling the workflow state inside `reconcile._create_payment_entry` (that branch escalates this item to APP_CODE — decide on TEST evidence).
- Optional threshold condition (branch, decide with accountant): add transition condition `doc.grand_total > X` so small PEs skip approval — enumerate, default = all PEs route.
- Workflow State / Workflow Action Master fixtures already ship the needed states/actions (Draft, Pending Approval, Approved, Rejected; Submit for Approval, Approve, Reject — repo_ops/repo_app_inventory); the workflow_state custom fields are intentionally excluded from the Custom Field fixture by design (hooks.py exclusion list — repo_ops) — leave that exclusion intact.

## Acceptance criteria
- `SELECT name, is_active FROM tabWorkflow WHERE name IN ('Purchase Invoice Approval','Payment Entry Approval')` → both 1 (prod + test).
- SQL over `tabWorkflow Transition`: zero rows with `allow_self_approval`=1 for these two workflows.
- TEST: PI created by Accounts User A cannot be approved by A (Approve button absent/error), can be by Manager B; approved PI submits.
- TEST: user A (Accounts User) submits-for-approval a Payment Entry; user A CANNOT execute Approve; user B (Accounts Manager) approves and doc reaches Approved/submitted.
- TEST: a Stripe test payment still yields a docstatus=1 Payment Entry with no human touch.
- Fixtures re-export is clean (no prod-only drift): the deployed workflow docs match the repo JSON.

## Rollback
Revert the fixture commit; migrate restores `is_active`=0 (documents already Approved remain submitted; new ones bypass workflow). In-flight docs stuck in Pending Approval are resolved by an Accounts Manager before deactivation.

## Explicitly NOT in this work item
The Phase-0 `allow_self_approval` fixture repair (WI-015 — blocker, not repeated here); dollar-threshold Authorization Rules and the PO escalation ladder (WI-013; percentage rule is Phase 2 per hard rule 5); Expense Claim/PO/Sales Invoice workflows; email/SMS approval notifications (native Notification, later); reworking workflow states; role assignments themselves (WI-011).

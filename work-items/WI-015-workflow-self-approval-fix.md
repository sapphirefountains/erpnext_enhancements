# WI-015: Workflow fixture repair — allow_self_approval 1→0 on the dormant PI/PE approval workflows
**Phase:** 0   **Type:** FIXTURE   **Size:** S
**Blocked by:** nothing   **Blocks:** WI-044

## Why
Two approval workflows ship DORMANT in `erpnext_enhancements/fixtures/workflow.json`: 'Purchase Invoice Approval' and 'Payment Entry Approval', both `is_active=0`, states Draft/Pending Approval/Approved/Rejected, roles Accounts User → Accounts Manager (repo_ops §4; prod/test parity confirmed in test_vs_prod). Critically, ALL transitions carry `allow_self_approval=1` (verified: 8 occurrences in the fixture file) — the submitter can approve their own payment, which defeats segregation of duties entirely. Money leaving the company needs a real two-person rule from cutover. This item is the standalone Phase-0 fixture repair: it fixes the flags EARLY and SAFELY while the workflows stay inactive, so nothing behavioral deploys before the cutover release. Activation (the `is_active` flip, SoD user preconditions, the legacy-draft hazard, and the optional threshold-condition branch) lives in WI-044.

## Native-first check
Native **Workflow** engine — SUFFICIENT and already used (these ARE native Workflow fixtures). Fixing the flags is configuration of the native feature; building any custom approval doctype would be a defect.

## Preconditions
- None operational — this is a repo-only fixture edit; the workflows remain `is_active=0` throughout, so the change is inert on deploy.
- Prod/test parity of the two dormant workflow fixtures confirmed (test_vs_prod).

## Scope
- Edit `erpnext_enhancements/fixtures/workflow.json`: set `allow_self_approval=0` on every transition of 'Purchase Invoice Approval' and 'Payment Entry Approval' (Phase 0 commit; workflows stay `is_active=0` so nothing behavioral deploys early).
- The `workflow_state` custom fields are intentionally excluded from the Custom Field fixture (hooks.py exclusion list — repo_ops §4); leave that exclusion intact.
- Do NOT flip `is_active` here — that promotion is WI-044's cutover-release commit.

## Acceptance criteria
- Repo: `grep -c '"allow_self_approval": 1' fixtures/workflow.json` = 0 for the two finance workflows.
- Post-deploy: `SELECT name,is_active FROM tabWorkflow WHERE name IN ('Purchase Invoice Approval','Payment Entry Approval')` → both still `is_active=0` on test and prod (nothing behavioral changed), and their transitions carry `allow_self_approval=0`.
- CI green on the fixture commit.

## Rollback
Revert the fixture commit and redeploy (no behavioral change either way while the workflows remain dormant).

## Explicitly NOT in this work item
Activating the workflows (`is_active` flip — WI-044); the ≥2-distinct-accounts-users SoD precondition and its verification (WI-044/WI-011); disposition of the 1,405 legacy draft Payment Entries (finance workstream — WI-028 sequencing handled in WI-044); the optional `doc.grand_total > X` threshold-condition branch (enumerated in WI-044); workflows on Sales Invoice or Purchase Order (Authorization Rule covers PO — WI-013); email/SMS approval notifications; reworking workflow states.

# WI-011: Per-employee access mapping (23 system users, 18 employees)
**Phase:** 0   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-010   **Blocks:** WI-012, WI-021, WI-044, WI-048

## Why
23 enabled System Users, 18 Employees (14 Active) exist (prod_finance_native), but nothing documents who should hold which Role Profile at cutover. Controls (workflow SoD, Authorization Rule bypass) and the Time Kiosk (which resolves Employee from the session user — repo_ops §1) all hinge on a correct user↔employee↔role matrix.

## Native-first check
Native **User.role_profile_name** assignment + native **Employee.user_id** link — SUFFICIENT; a spreadsheet-driven Desk exercise, no code.

## Preconditions
- WI-010 profile set finalized.
- HR confirms the 14 Active employees and which of the 23 users map to them (some users are non-employee/system accounts — expect ~5 unmatched, enumerate them).

## Scope
- Produce the matrix: for each enabled User → Role Profile, Employee link, module access notes; get CEO sign-off (it IS the SoD design).
- Apply in Desk: `User.role_profile_name` per user; `Employee.user_id` per active field employee (kiosk precondition); disable any stale users found.
- Verify SoD pairs for WI-044: preparer ≠ approver on Accounts User/Accounts Manager.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabUser WHERE enabled=1 AND user_type='System User' AND (role_profile_name IS NULL OR role_profile_name='')` = 0.
- `SELECT COUNT(*) FROM tabEmployee WHERE status='Active' AND (user_id IS NULL OR user_id='')` = 0 for the kiosk-participating subset (list attached to the item).
- At least one user holds 'Accounts Manager' who is NOT the daily Accounts User preparer (name recorded in runbook).

## Rollback
Matrix snapshot (CSV of user, role_profile_name before/after) enables exact reversal.

## Explicitly NOT in this work item
Creating/removing Role Profiles (WI-010); customer portal users; password/2FA policy.

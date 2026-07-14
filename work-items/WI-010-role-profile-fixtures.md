# WI-010: Fixture-ize Roles and Role Profiles (17 profiles exist on prod, zero in the app)
**Phase:** 0   **Type:** FIXTURE   **Size:** M
**Blocked by:** nothing   **Blocks:** WI-011, WI-013, WI-044, WI-048

## Why
Prod carries 17 Role Profiles (Accounts, Design Team, Executive, Finance, Finance Team, HR, Inventory, Manufacturing, Poseidon, Production Team, Projects & Operations, Purchase, Sales, Sales & Marketing, Sales Team, System Manager, Technician — prod_finance_native) but the app ships NO Role/Role Profile/Authorization Rule fixtures (repo_ops §4). Hand-clicked security architecture that isn't code-managed drifts between test and prod and can't be reviewed — the app's own convention (fixtures = version control) is being violated for its most security-sensitive config.

## Native-first check
Native **Role / Role Profile** doctypes + the app's existing **fixtures** and **patch** mechanisms — SUFFICIENT. The app already seeds roles via idempotent patches (verified `patches/seed_hr_team_role.py`: exists-check → `frappe.new_doc("Role")`, `desk_access=1`); Role Profiles fit the fixtures allowlist pattern used for Dashboards/Notifications (hooks.py fixtures list, repo_app_inventory).

## Preconditions
- Role Profile audit on prod: dump each profile's roles child table; flag legacy 'Poseidon' (Poseidon→Triton rename heritage — repo_app_inventory patches) for retire/rename decision.
- Duplicate-cluster decision: Finance vs Finance Team vs Accounts; Sales vs Sales Team vs Sales & Marketing — consolidate or keep (business call, enumerate; default keep-as-is for cutover, consolidate Phase 2).

## Scope
- hooks.py `fixtures` list: add `{"dt": "Role Profile", "filters": [["name", "in", [<the kept profiles>]]]}` and `{"dt": "Role", "filters": [["name", "in", [<custom roles: 'Employee Self Service' (verified is_custom=1 — prod_finance_native) + any newly seeded>]]]}` — name-in allowlists per the app's existing convention so re-export never sweeps user-created records (repo_app_inventory §hooks fixtures).
- New patch `patches/seed_po_approver_role.py` following seed_hr_team_role verbatim pattern, seeding 'PO Approver' (for WI-013); append to `patches.txt` [post_model_sync].
- `bench export-fixtures` on test → commit `fixtures/role_profile.json` (+ role.json).

## Acceptance criteria
- Repo contains `fixtures/role_profile.json` with exactly the kept profile names; CI passes.
- After deploy: `SELECT COUNT(*) FROM tabRole WHERE name='PO Approver'` = 1 on test and prod.
- Diff check: role sets per profile identical on test and prod (same SQL both sides: `SELECT parent, role FROM `tabHas Role` WHERE parenttype='Role Profile' ORDER BY 1,2`).

## Rollback
Remove the fixture entries from hooks.py + delete the JSON files and redeploy (existing DB rows persist but return to unmanaged state); the seed patch is guarded by exists-check so re-runs are no-ops.

## Explicitly NOT in this work item
Assigning profiles to users (WI-011); DocPerm changes (WI-012); deleting the 'Poseidon' profile without sign-off.

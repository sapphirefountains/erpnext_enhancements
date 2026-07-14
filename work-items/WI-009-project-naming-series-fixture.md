# WI-009: Project naming series continuity (PRJ-) for the Closed-Won handoff
**Phase:** 0   **Type:** FIXTURE   **Size:** S
**Blocked by:** nothing   **Blocks:** WI-048, WI-022

## Why
All 625 existing Projects are named `PRJ-*` (prod_projects_opps), but the handoff engine sets NO naming_series and falls through to the ERPNext default (`PROJ-.####`) (repo_ops: create_project_from_opportunity_background). Post-cutover projects would fork into a second naming scheme, breaking user muscle memory, Drive folder names (`<Project ID> - <Name>`), and any PRJ-prefix assumptions.

## Native-first check
Native **Property Setter** on `Project.naming_series` (set `options`/`default`) — SUFFICIENT; the handoff code reads the doctype default, so no APP_CODE change is needed. Reimplementing naming in the handoff would be a defect.

## Preconditions
- Determine the exact live format: `SELECT name FROM tabProject ORDER BY creation DESC LIMIT 5` (digit width of PRJ-####).
- `SELECT current FROM tabSeries WHERE name='PRJ-'` vs `SELECT MAX(CAST(SUBSTRING(name,5) AS UNSIGNED)) FROM tabProject WHERE name LIKE 'PRJ-%'` — series counter must be ≥ max existing number (bump via Desk **Document Naming Settings** > "Update Series Number" if not; review correction C13: there is no 'Naming Series' DocType in this build — the Update Series Number action lives in Document Naming Settings).

## Scope
- Property Setters on doctype `Project`, field `naming_series`: `options` = the PRJ format, `default` = same. Created on test, exported with `bench export-fixtures`; lands in `erpnext_enhancements/fixtures/property_setter.json` automatically (fixture filter `is_system_generated=0` — repo_ops §4).
- One repo commit; deploys to both sites via Frappe Cloud main-branch deploy + after_migrate.

## Acceptance criteria
- On TEST: drive one Opportunity to 'Closed Won', accept the prompt; resulting Project name matches `^PRJ-` and number = previous max + 1.
- `SELECT COUNT(*) FROM tabProject WHERE name NOT LIKE 'PRJ-%'` remains 0 after one week of parallel-run project creation.
- `grep -c 'Project-naming_series' erpnext_enhancements/fixtures/property_setter.json` ≥ 1 in the repo.

## Rollback
Delete the two Property Setter fixture entries and redeploy; series counter untouched.

## Explicitly NOT in this work item
Renaming any existing project; the project_name_remediation tool (QBO workstream); changing handoff field mappings.

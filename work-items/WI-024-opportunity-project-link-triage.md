# WI-024: Closed-Won Opportunity↔Project linkage — canonicalization and 196-orphan triage
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** nothing   **Blocks:** WI-054 (union report joins), sales-analytics trust generally

## Why
280 Opportunities are 'Closed Won'; THREE linkage fields exist with inconsistent population (Opportunity.custom_project=16, Opportunity.custom_created_project=54, reverse Project.custom_opportunity=80; union has-any=84; **196 have no project by any field** — prod_projects_opps). Won-revenue-to-delivery reporting is untrustworthy until one canonical link exists and the 196 are dispositioned.

## Native-first check
No native Opportunity→Project link exists in ERPNext; the app already owns this relationship — `custom_created_project` is the Closed-Won handoff engine's idempotency + write-back field (repo_ops). Verdict: standardize on the app's existing fields (canonical forward = `Opportunity.custom_created_project`, canonical reverse = `Project.custom_opportunity`); zero new fields, zero new code.

## Preconditions
- Sales leadership triage session scheduled with the 196-row worksheet (opportunity, party, amount, `custom_stage_changed_on` as won-date proxy — populated on all 280 per prod_projects_opps; `custom_date_closed_won` populated on only 57).

## Scope
1. Mechanical backfill (all via `frappe.db.set_value` — hazard H3: Opportunity.on_update fires the closed-won prompt `prompt_create_project_on_won`, so never `doc.save()` Opportunities in bulk; hazard H1: the wildcard `'*'` after_save Triton sync hook, likewise bypassed by db.set_value):
   - Where `custom_project` set and `custom_created_project` empty → copy across (<=16 rows).
   - Where a Project has `custom_opportunity=<opp>` and the opp's `custom_created_project` is empty → set it (<=80 rows).
   - Ensure symmetric reverse links: every `custom_created_project` target Project gets `custom_opportunity` back-filled.
2. Triage the remaining ~196: rule set — (i) won before 2025-01-01 or delivery demonstrably complete → **mark historical**: no retro Project; record disposition 'Historical - no project' in the worksheet (archived; no invented status field); (ii) work active/upcoming → create the Project via the existing handoff path (`enqueue_project_creation` — repo_ops) ONE AT A TIME (it provisions Drive folders and notifications by design), expected to be a small handful.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabOpportunity WHERE status='Closed Won' AND IFNULL(custom_project,'')<>'' AND IFNULL(custom_created_project,'')=''` = 0.
- `SELECT COUNT(*) FROM tabOpportunity o JOIN tabProject p ON p.custom_opportunity=o.name WHERE o.status='Closed Won' AND IFNULL(o.custom_created_project,'')=''` = 0.
- Triage worksheet has a recorded disposition for all 196; count of retro-created projects equals the ratified active list.
- Zero closed-won prompts/SMS fired during the run (verify no new `project_creation_status` realtime storm / no unexpected Projects).

## Rollback
Keyed restore of the three link fields from the pre-run export; retro projects deleted individually if mis-created.

## Explicitly NOT in this work item
Retiring the two redundant link fields (`custom_project` deprecation is a FIXTURE change owned by the CRM workstream); opportunity amount/stage cleanup; `custom_date_closed_won` backfill.

## Decision 2026-10-13 (Nik; TASK-2026-00917) — shipped in v1.629.0
Historical Opportunities stop raising "Closed Won but no linked Project" warnings and are left out of KPI measurements. **This supersedes the "no invented status field" line in Scope (i):** the mechanism is a stored, read-only Check, `Opportunity.custom_is_historical` ("Historical (pre-ERPNext)"), because Number Cards, Dashboard Charts, Report Builder and the list view can only filter on a stored column.

- **Rule:** `creation < 2025-08-01` (every Zoho import, whenever it was won) AND no activity in the last 90 days — a Comment (type Comment/Attachment), a ToDo, or a status-changing Version that was not a Data Import, by anyone other than Administrator/Guest; or a Communication on the deal (by reference or Communication Link) whoever logged it.
- **Computed** nightly by `crm_enhancements/historical.py::refresh_historical_flags` (and once by the `set_historical_opportunities` patch); a person changing a historical deal's status un-flags it in the same save.
- **Applied** to the nag, the reconciliation and hand-off coverage reports ("Include historical" checkbox, off by default), the Sales Dashboard backlog widget, the hand-off KPI and the Closed-Won KPIs, the Sales Pipeline's "Won — awaiting project" column, the Opportunity form's prompts and Hand-Off tab, the AI hand-off tool (which reports how many it left out), and the Closed-Won Number Card and the two by-status charts. See `crm_enhancements/README.md` → Historical opportunities.
- The worksheet disposition "Historical - no project" in Scope (i) is now this flag; the triage of the non-historical remainder is unchanged.

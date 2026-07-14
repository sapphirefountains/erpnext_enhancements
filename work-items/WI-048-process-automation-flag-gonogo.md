# WI-048: process_automation_enabled go/no-go (PRO-0204 7-step tracker + Closed-Won SMS)
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-009, WI-010, WI-011   **Blocks:** WI-022

## Why
The Closed-Won → Project creation engine runs UNGATED today (repo_ops: no feature-flag check on create_project_from_opportunity_background), but the downstream PRO-0204 7-step handoff tracker and Closed-Won team SMS are gated by `ERPNext Enhancements Settings.process_automation_enabled` (default OFF). Cutover needs a deliberate decision: run the tracker from day one (structured handoffs, escalations) or defer (less UI noise for a team learning ERPNext).

## Native-first check
Native **Workflow** and **Assignment Rule** evaluated as alternatives to the custom 7-step tracker — INSUFFICIENT: the tracker (Project.custom_process_steps seeded from Process Step Template, role-resolved owners, daily escalate_overdue_steps — repo_ops §7) is already built, richer, and fixture/patch-managed; re-modelling it as a native Workflow would be reimplementation churn. The flag flip itself is the native staged-rollout mechanism of this app (repo_app_inventory §4).

## Preconditions
- `ERPNext Enhancements Settings.handoff_ar_rep` populated with a real user (repo_ops: AR step owner resolves from this field).
- Roles 'Project Manager' and 'Account Executive' assigned to real users (WI-011), since prompt notify-defaults and step owners resolve by role (repo_ops).
- Triton SMS path reachable if SMS wanted (status_alerts.deliver_closed_won_alerts).

## Scope
- Single Check field: `ERPNext Enhancements Settings.process_automation_enabled` (verified fieldname — repo_app_inventory/repo_ops).
- Branch A (recommended): flip ON at start of December parallel run on TEST; if step noise is acceptable, ON at prod cutover. Branch B: leave OFF for January, revisit Feb — document that Closed-Won SMS also stays off in this branch (same flag gates both).
- Record decision + owner in the runbook.

## Acceptance criteria
- `SELECT value FROM tabSingles WHERE doctype='ERPNext Enhancements Settings' AND field='process_automation_enabled'` equals the decided value on prod at cutover.
- If ON: closing a test Opportunity as Won yields a Project with >0 rows in its `custom_process_steps` child table.

## Rollback
Flip the Check back to 0 (runtime flag; no deploy).

## Explicitly NOT in this work item
Changing handoff engine code; backfilling the 196 Closed-Won opportunities without projects (CRM/data workstream — WI-024); SMS content changes.

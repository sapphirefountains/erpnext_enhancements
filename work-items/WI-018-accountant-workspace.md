# WI-018: Accountant workspace & desk curation
**Phase:** 0   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-011   **Blocks:** WI-022

## Why
The accountant's explicit demand is minimal UI. A stock ERPNext desk plus 26 custom modules (repo_app_inventory) is overwhelming; day-one success for finance depends on a desk where the 8-10 things she does are one click and everything else is invisible.

## Native-first check
Native **Workspace** doctype + native **User.block_modules** + role restriction on the Workspace — SUFFICIENT; no custom pages.

**Corrected 2026-08-03, three ways:**
1. **Workspaces ARE version-controlled here.** This item originally said the config "does not travel via git" and must be hand-recreated on prod. False since: 33 workspaces ship as `<module>/workspace/<slug>/<slug>.json` through app-doctype sync. They **cannot** be fixtures — `remove_orphan_entities` deletes a `public=1, module set, app set` Workspace with no backing *directory* later in the same migrate that imported it. That same sweep means **deleting the JSON deletes the live record**, the opposite of the fixture rule.
2. **The module is load-bearing, not cosmetic.** `Workspace.__init__` raises `PermissionError` when the workspace's module is not in the viewer's `allow_modules`, which is derived from DocType *read* permissions. Put the workspace in a module the accountant cannot read and it silently does not exist for her.
3. **The desk rail is built from `Desktop Icon` records**, not from the workspace query, and nothing on the migrate path creates one (the generator is `after_app_install` plus a one-shot v16 patch). A new workspace can ship correctly and be unreachable. `Finance Hub` already has an icon — the deciding reason to curate it rather than create an `Accounting` workspace, on top of prod already carrying four finance-named surfaces.

## Preconditions
- Task inventory with the accountant: her recurring actions (enter PI, run PE + approval, bank rec, month-end close, AR follow-up, payroll JE, hours export). **Not done.** Part 1 ships additive-only and is used as the prop for that walkthrough; every removal waits on its output.
- WI-011 role mapping done (workspace visibility follows roles). **Not actually done.** Prod shows the accountant holding **89 roles** including `System Manager`, `Script Manager`, and both `Accounts Manager` *and* `Accounts User` — while `wi011-apply-runbook.md` records under a "✅ complete" header that she is "Accounts User, NOT Accounts Manager". The preparer≠approver separation WI-011 claims is not in place. Reconcile before removing any role.
- **`Workspace Manager` must come off the accountant.** `get_workspaces()` short-circuits on `has_access = "Workspace Manager" in frappe.get_roles()`; when true it drops the query filters entirely and bypasses role restrictions, `is_hidden` **and** blocked modules. She holds it. **Until it is removed, every mechanism in this work item is inert.**

## Scope
- One curated 'Accounting' Workspace: shortcuts to Sales Invoice, Purchase Invoice, Payment Entry, Journal Entry, Bank Reconciliation Tool, Month-End Close, the native AR/AP/GL/Trial Balance reports (all verified present — prod_finance_native), 'Invoices without Project' saved filter (WI-008), Payroll Hours Export (WI-017).
- Per-user module blocking for finance users: block the irrelevant custom modules (Water Engineering, Fleet, MDM, etc.) via User > Allow Modules.
- Workspace is deliberately CONFIG (user-editable; Workspace is not in the app's fixture allowlists today — repo_ops §4); if it proves churn-prone, promote to fixture in Phase 2.

## Acceptance criteria

> ⚠️ The original second criterion — `SELECT COUNT(*) FROM \`tabBlock Module\` WHERE parent=<accountant user> > 0` — **could pass while her sidebar was completely unchanged**, because `Workspace Manager` bypasses the blocked-module filter. It measured that a row was inserted, not that anything happened. Replaced.

**Part 1 (additive):**
- The curated Finance Hub renders for a user holding only `Accounts User`, with all six shortcuts and both cards visible and no empty columns.
- `tests/test_workspaces.py` passes: every layout block resolves to a child row and every child row is placed, across all workspace JSONs.
- The workspace's `Desktop Icon` exists and is not hidden.

**Part 2 (removals, gated on the walkthrough):**
- `"Workspace Manager" in frappe.get_roles(<accountant>)` is **False** — assert this first; nothing else is meaningful until it is.
- Logged in as the accountant, the workspace list is the agreed named set, checked by name and not only by count.
- Accountant sign-off recorded after a walkthrough (UAT gate input).

## Rollback
Delete the Workspace record and Block Module rows.

## Explicitly NOT in this work item
Hiding form fields (WI-019); dashboards beyond reusing Finance Health; portal UX.

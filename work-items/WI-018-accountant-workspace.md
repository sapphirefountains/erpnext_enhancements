# WI-018: Accountant workspace & desk curation
**Phase:** 0   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-011   **Blocks:** WI-022

## Why
The accountant's explicit demand is minimal UI. A stock ERPNext desk plus 26 custom modules (repo_app_inventory) is overwhelming; day-one success for finance depends on a desk where the 8-10 things she does are one click and everything else is invisible.

## Native-first check
Native **Workspace** doctype + native **User.block_modules** (allowed-modules per user) + Role-Profile-driven sidebar — SUFFICIENT; no custom pages. The app's existing 'Finance Health' Dashboard (fixtures allowlist — repo_app_inventory) is reused as the workspace's dashboard, not rebuilt.

## Preconditions
- Task inventory with the accountant: her recurring actions (enter PI, run PE + approval, bank rec, month-end close, AR follow-up, payroll JE, hours export).
- WI-011 role mapping done (workspace visibility follows roles).

## Scope
- One curated 'Accounting' Workspace: shortcuts to Sales Invoice, Purchase Invoice, Payment Entry, Journal Entry, Bank Reconciliation Tool, Month-End Close, the native AR/AP/GL/Trial Balance reports (all verified present — prod_finance_native), 'Invoices without Project' saved filter (WI-008), Payroll Hours Export (WI-017).
- Per-user module blocking for finance users: block the irrelevant custom modules (Water Engineering, Fleet, MDM, etc.) via User > Allow Modules.
- Workspace is deliberately CONFIG (user-editable; Workspace is not in the app's fixture allowlists today — repo_ops §4); if it proves churn-prone, promote to fixture in Phase 2.

## Acceptance criteria
- Logging in as the accountant test user lands on/except one click from the Accounting workspace; sidebar shows ≤ the agreed module set.
- `SELECT COUNT(*) FROM `tabBlock Module` WHERE parent=<accountant user>` > 0.
- Accountant sign-off recorded after a walkthrough (UAT gate input).

## Rollback
Delete the Workspace record and Block Module rows.

## Explicitly NOT in this work item
Hiding form fields (WI-019); dashboards beyond reusing Finance Health; portal UX.

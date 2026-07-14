# WI-051: Cutover runbook, go/no-go checklist & day-1 support
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** WI-022 + gates: WI-023, WI-044, WI-046, WI-047, and the finance workstream's opening-balance items (WI-032/WI-033/WI-035)   **Blocks:** nothing (terminal)

## Why
2027-01-01 is fixed; scope flexes, the date does not. A single ordered runbook — who does what, in what order, with verifiable checks and a documented abort path — is the difference between a cutover and a scramble. QBO must end the day read-only (rule 4).

## Native-first check
Process artifact; no software. Where the runbook verifies system state it uses the native reports and SQL checks already defined in the other items' acceptance criteria (no new tooling).

## Preconditions
- All blocking items' acceptance criteria green on TEST; go/no-go meeting scheduled ~Dec 22, 2026 with CEO + accountant + implementation lead.

## Scope
- Runbook sections (each line = owner + timestamp + verification): (1) freeze window & comms; (2) final QBO actions and QBO users set to read-only/reports-only access (rule 4) — QBO remains accessible as archive only; (3) prod deploy of the cutover release (workflow activation flip WI-044, QuickBooks retirement WI-045/WI-046); (4) prod CONFIG replays that are not fixtures: Journal Entry Template (WI-047), Activity Costs (WI-016 rates), Workspace (WI-018), user role assignments verified (WI-011) — the Authorization Rule row is struck from this list (review correction C7): it deploys via the WI-013 fixture/seed patch, not hand replay; (5) go/no-go checklist — one line per work item referencing its SQL acceptance check, plus finance workstream gates (Opening Entry posted on prod; draft JE/SI/PE backlog dispositioned); (6) day-1/week-1 support: named floor-walker rota, a 'Cutover Issues' triage list (native ToDo/Issue), daily 15-min triage standup for two weeks, escalation path to the implementation lead; (7) abort criteria & path (if no-go: stay on QBO for January, ERPNext parallel continues — enumerated, not decided).
- BINDING cutover-window ordering (review correction C5) — the runbook encodes this sequence verbatim and each step gates the next: final CDC/Import → `sync_enabled=0` + Intuit webhook subscription DELETED (partial kill; OAuth tokens stay ALIVE) → WI-028 delete → WI-029 CoA rebuild → WI-030/WI-031 → WI-032 opening TB (uses live QBO API) → WI-033/WI-034 → WI-035 → THEN WI-045 full Disconnect. (WI-045's webhook-deletion step executes EARLY as WI-028's precondition; its Disconnect step executes LAST.)
- Interim AR collection procedure (review correction C12): opening AR cannot exist before the December close completes (~Jan 10–15), so no "Jan 1 with the opening AR in place" assumption anywhere in the runbook. Jan 1–~15, incoming payments for pre-cutover invoices are recorded as unallocated/on-account Payment Entries (party set, no invoice reference), then reconciled to the opening Sales Invoices via native Payment Reconciliation once WI-033 posts. Stripe autopay enrollment stays gated on WI-033.
- Bulk-operation hygiene appendix consolidating the hazards for anyone running cutover-week data scripts: wildcard `'*'` after_save `global_triton_sync` fires on every doc.save (prefer frappe.db.set_value / batches); `Project Folder Google Drive Settings.create_customer_folders` gates Customer after_insert Drive-folder creation (confirm OFF during any bulk customer touch); Opportunity on_update fires the closed-won prompt (avoid bulk status writes via doc.save).

## Acceptance criteria
- Runbook document exists, versioned in the repo docs/ tree, with every checklist line mapped to a machine check (SQL/report/settings value) and an owner, and the C5 ordering encoded as strictly sequential gated steps.
- Go/no-go meeting minutes recorded with explicit GO decision and sign-offs.
- Day 1: first real Quotation, SO, SI, PO, PE, and kiosk interval each created on prod by their business owner (six SQL existence checks, e.g. `SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND transaction_date>='2027-01-01'` ≥ 1 by Jan 8).
- Jan 1–15 interim: on-account Payment Entries for pre-cutover invoices exist with party set and no invoice reference; after WI-033 posts, Payment Reconciliation clears them against the opening Sales Invoices (review correction C12).
- Two-week post-cutover review held; open issues < agreed threshold.

## Rollback
The runbook contains its own abort path (section 7); individual technical rollbacks live in each work item.

## Explicitly NOT in this work item
Opening balances/AR-AP open items (finance workstream — WI-032/WI-033/WI-035, referenced as gates); Stripe key provisioning on prod (payments workstream — WI-039, referenced as a gate); historical period-summary JE import (optional — WI-053).

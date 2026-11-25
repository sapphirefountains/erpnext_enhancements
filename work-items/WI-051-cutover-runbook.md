# WI-051: Cutover runbook, go/no-go checklist & day-1 support
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** WI-022 (finance UAT) + gates: the 2026 posting waves (TASK-2026-01236), WI-003 locks, WI-044, WI-046, WI-047, WI-030, WI-035   **Blocks:** nothing (terminal)
**Rewritten 2026-11-25** for Nikolas's cutover decisions of that day. The runbook is [`docs/migration/wi051-cutover-runbook.md`](../docs/migration/wi051-cutover-runbook.md). ERPNext Task: TASK-2026-00944.

## Why
A single ordered runbook (who does what, in what order, with verifiable checks and a documented abort path) is the difference between a cutover and a scramble. QuickBooks must end read-only and disconnected (rule 4).

## Native-first check
Process artifact; no software. Where the runbook verifies system state it uses native reports and SQL checks already defined in the other items' acceptance criteria (no new tooling).

## The sequence (decisions of 2026-11-25)
- **Go-live Fri 2027-01-01** (first working day Mon Jan 4), with a **hold-or-move check on Fri Dec 11** and a **Feb 1 fallback** cut at the Jan 31 close with the same steps a month later. Go/no-go **Tue Dec 22**, James chairing.
- **No clean cut.** Full history is posted (OD-6 reversal, 2026-08-31), the live chart is kept (WI-029 canceled), and the opening-balance entries are canceled (WI-032 / WI-033 superseded): the posted ledger through 2026-12-31 is the opening position.
- **2026 posts in three waves** as QuickBooks months lock: Jan–Sep on Dec 9, Oct–Nov on Dec 18, December on Jan 14. Each wave runs Sales Invoices → Payment-only resync (Preview, Run, then Refresh drafts for flagged rows) → Payment Entries → Journal Entries, as in [`backlog-gl-posting-runbook.md`](../docs/migration/backlog-gl-posting-runbook.md) §5.
- **QuickBooks users go reports-only on Jan 1**, except Lisa and John. CDC sync stays on through the December close.
- **Tie-out (WI-035) by Jan 22:** the posted ledger at 2026-12-31 equals QuickBooks' December close; then `accounts_frozen_till_date` = 2026-12-31.
- **Disconnect (WI-045) on Jan 29.**
- **TEST** is restored from a prod backup by Dec 2 for the finance UAT (WI-022), with its QuickBooks sync, Plaid, Stripe, email and scheduler off before anyone uses it.
- **James approves bills and payments**, Nikolas backs up, Lisa prepares only (WI-044). Manual sales tax at go-live if John's matrix misses Dec 15.
- **Open:** clock-in (decision 5: WI-021 / WI-016 / QuickBooks Time timing); the 2026 stock postings QuickBooks never saw (a WI-035 call).

## Scope
Runbook sections, each line = owner + timestamp + verification:
- §0 decisions and what the Aug 31 clean-cut version replaced; calendar.
- §1 safety and communications: tested backup and Wave 1 rehearsal, no-merge windows, the WI-050 AI write gate.
- §2 TEST restore and isolation.
- §3 the three posting waves: a per-wave gate (QuickBooks lock, Record Matching queue, QBO-linked population, WI-068 group-account lines, **0 autopay customers**, reminders off, named backup), the run order, post-wave checks, and how a QuickBooks edit to a posted month is handled (cancel and amend, then Keep ERPNext).
- §4 hold-or-move check (Dec 11). §5 go/no-go (Dec 22).
- §6 cutover release and config replays: WI-046 guest webhook dies regardless of decision 5; WI-011, WI-018, WI-047, WI-030, WI-031; WI-044 never activated while a wave's Payment Entry stage runs.
- §7 go-live: QuickBooks reports-only except Lisa and John; no QuickBooks document dated 2027; **customer payments received Jan 4–14 for December invoices recorded unapplied, then reconciled after Wave 3**; day-1 existence checks; week-1 support.
- §8 Wave 3, tie-out and freeze (WI-035), autopay enrollment after the freeze, Disconnect (WI-045).
- §9 abort criteria and path, and the Feb 1 fallback.
- Appendix A: bulk-operation hygiene (Drive-folder provisioning on Customer/Supplier insert, the closed-won prompt, the deploy FLUSHDB hazard).

## Acceptance criteria
- Runbook document exists, versioned in the repo `docs/` tree, with every checklist line mapped to a machine check (SQL / report / settings value) and an owner, and the 2026-11-25 sequence encoded as gated steps.
- Hold-or-move call recorded on Dec 11.
- Go/no-go minutes recorded with an explicit GO decision and sign-offs (James, John, Nikolas).
- Day 1: first real Quotation, SO, SI, PO and PE each created on prod by their business owner (SQL existence checks, e.g. ``SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND transaction_date>='2027-01-01'`` ≥ 1 by Jan 8); a kiosk interval too if decision 5 puts the kiosk live on Jan 1.
- Jan 4–14: unapplied Payment Entries for December invoices exist with party set and no invoice reference; after Wave 3, Payment Reconciliation clears them against the December invoices.
- Two-week post-cutover review held; open issues < agreed threshold.

## Rollback
The runbook contains its own abort path (§9); individual technical rollbacks live in each work item.

## Explicitly NOT in this work item
The posting mechanics themselves (backlog GL-posting runbook, TASK-2026-01236); the tie-out (WI-035, referenced as a gate); Stripe key provisioning (WI-039); historical period-summary JE import (WI-053).

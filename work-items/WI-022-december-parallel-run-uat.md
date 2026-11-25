# WI-022: December finance UAT on TEST (was: December parallel run, UAT scripts & user training)
**Phase:** 1   **Type:** DATA   **Size:** L
**Blocked by:** TEST site restored from a prod backup and isolated (WI-051 runbook §2, by 2026-12-02); Phase-0 config items deployed (WI-007, WI-008, WI-010, WI-011, WI-012, WI-013, WI-014, WI-015, WI-017, WI-018, WI-020)   **Blocks:** WI-051 (Dec 22 go/no-go)
**Re-scoped 2026-11-25** (Nikolas's cutover decision 3): a **finance UAT**, Dec 7–18, run by Lisa and James on TEST restored from a fresh prod backup. The time-and-kiosk scripts wait on decision 5 (clock-in), which is still open. ERPNext Task: TASK-2026-00915, due Dec 18.

## Why
ERPNext has never held a submitted Sales Order, and no bill has been through the approval workflow. From Jan 1 Lisa prepares and James approves every bill and payment in ERPNext. A December rehearsal of those loops on a copy of prod is the only way to find the gaps before the books depend on them; without it the first AP run in ERPNext happens live in January.

## Native-first check
Native ERPNext documents are the test vehicle; there is no tooling to build. The venue is TEST (`beta.erp.sapphirefountains.com`) restored from a fresh prod backup, so the UAT runs against prod's own chart, parties, items and posted history rather than a divergent test dataset. The local mirror is used only for posting rehearsals (WI-051 runbook §1.3), not for this UAT.

## Preconditions
- **TEST restored and isolated by Dec 2** (WI-051 runbook §2): scheduler, email, QuickBooks, Plaid and Stripe off before anyone signs in, and every other outbound integration off. A prod backup carries prod's live tokens and keys.
- Phase-0 config deployed (it reaches TEST with the same `main` deploy as prod).
- UAT cohort named: Lisa (preparer), James (approver), Nikolas (approver backup); a PM and Parker for the purchasing scripts; an AE for the selling scripts.

## Scope (population = the UAT script inventory below; each script = one checklist row with evidence)
- **AP:** Purchase Order → Purchase Receipt → Purchase Invoice → Payment Entry, through the activated approval workflows (WI-044, fixture flipped on TEST only), with Lisa preparing and **James approving**. A self-approval attempt must fail. Over-threshold PO escalation (WI-013).
- **AR:** Quotation → Sales Order (with project) → Sales Invoice → Payment Entry; a Maintenance Record submit that auto-drafts a Sales Invoice with project. Plus **an unapplied receipt** (party set, no invoice reference) cleared later with native Payment Reconciliation, rehearsing WI-051 runbook §7.5.
- **Payroll:** the Payroll Summary Journal Entry from the template (WI-047), from a payroll-firm register sample.
- **Close:** a Month-End Close dry run (WI-049); a bank reconciliation for one account (WI-043).
- **Stripe:** only with Stripe test-mode keys and `environment='Test'` on TEST; never the Live keys the backup carries.
- **Time:** the kiosk day cycle (WI-021), the Timesheet and the Payroll Hours Export run only if decision 5 puts the kiosk on the go-live path.
- **Training:** role-based sessions for finance, purchasing and sales using the WI-007 / WI-012 / WI-018 / WI-047 SOPs; attendance recorded.
- Side-effect hygiene: create test Customers sparingly (Drive folder provisioning stays off on TEST, WI-051 runbook §2.8); Opportunity status flips fire the closed-won prompt by design (that is the test).

## Acceptance criteria
- UAT tracker: 100% of scripts executed, ≥95% passed, zero open Sev-1 defects, by Dec 18. The result goes to the Dec 22 go/no-go (WI-051 runbook §5).
- SQL evidence on TEST: ≥1 submitted Sales Order (``SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1`` > 0); ≥1 Payment Entry and ≥1 Purchase Invoice in state Approved, approved by someone other than the creator (``SELECT name, owner, modified_by FROM `tabPayment Entry` WHERE workflow_state='Approved'``); the unapplied receipt reconciled.
- Training attendance = 100% of finance go-live users.

## Rollback
TEST-only data. Discard it, or restore TEST from a newer prod backup (repeating the isolation steps). No prod exposure.

## Explicitly NOT in this work item
Prod data entry; the 2026 posting waves (rehearsed on the local mirror, WI-051 runbook §1.3); performance testing.

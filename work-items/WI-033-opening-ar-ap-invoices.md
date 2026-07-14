# WI-033: Open AR and AP as individual opening invoices (is_opening='Yes')
**Phase:** 1   **Type:** DATA   **Size:** L
**Blocked by:** WI-032 (transitively WI-003/WI-029/WI-030); hard autopay=0 gate (H4)   **Blocks:** WI-035; Stripe autopay enrollment (WI-039) stays gated on this item (review correction C12)

## Why
A lump AR/AP balance cannot age, cannot be collected invoice-by-invoice, and cannot match Stripe payments. Each QBO invoice open at 2026-12-31 becomes one ERPNext Sales Invoice with `is_opening='Yes'` posting Debit AR / Credit Temporary Opening; each open vendor bill becomes a mirrored opening Purchase Invoice (Credit AP / Debit Temporary Opening). Together they extinguish the Temporary Opening residue left by WI-032, and day-one dunning/payment (including Stripe payment links) works per invoice. Timing correction (review correction C12): opening AR cannot exist before the December close completes (~Jan 10–15) — earlier "AR loads first week of January" phrasing is superseded; the interim-collections procedure below covers Jan 1–~15.

## Native-first check
Native **Opening Invoice Creation Tool** (standard ERPNext Accounts doctype; creates opening SIs/PIs against Temporary Opening in bulk) — verify presence on this v16 build during the TEST rehearsal; if absent, native **Data Import** of Sales Invoice / Purchase Invoice with `is_opening='Yes'` achieves the identical result. Verdict: native sufficient; no custom loader. (The QBO sync's own AR/AP path was rejected: it produces JE party lines, not invoices — see WI-032.)

## Preconditions
- WI-003 AR aging detail + AP aging detail as of 2026-12-31 exported.
- Every open-invoice customer/vendor exists in ERPNext (1,602 customers / 1,156 suppliers already imported — prod_customers_items; the final sync closes any gap). Missing parties are created FIRST with hazard H2 checked (Drive folder toggle: `Project Folder Google Drive Settings.create_customer_folders` OFF before any run that inserts Customers).
- **Hazard H4 (hard gate):** `SELECT COUNT(*) FROM tabCustomer WHERE custom_stripe_autopay_enabled=1` = 0 at load time, and Stripe Payments Settings still disabled on prod — otherwise submitting opening SIs can auto-charge customers (`auto_charge_on_invoice_submit` on Sales Invoice on_submit — repo_payments). Coordinate: Stripe autopay enablement happens strictly AFTER this item.
- Hazard H1: run in batches of 100 with commits; confirm Triton sync pause.

## Scope
- One opening Sales Invoice per open QBO invoice: customer, original posting_date (or 2026-12-31 posting with original due_date if prior FYs' disabled state blocks backdating — decide in rehearsal; note FYs 2025/2026 remain enabled per WI-030 so invoices back to 2025-01-01 can keep true dates), outstanding amount as a single line against Temporary Opening, `is_opening='Yes'`, the WI-030 opening naming series, and `project` set where the QBO invoice's job maps to a PRJ-* Project (linkage via `QuickBooks Sync Mapping`, job handling per repo_qbo_sync).
- Mirror for AP: one opening Purchase Invoice per open bill.
- No taxes on opening invoices (tax was already remitted/accrued in QBO; the TB carries the liability).
- **Interim collections procedure, Jan 1–~15 (review correction C12; also written into the WI-051 runbook):** incoming payments for pre-cutover invoices are recorded as unallocated/on-account Payment Entries (party set, no invoice reference) until this item posts; they are then reconciled to the opening Sales Invoices via native Payment Reconciliation. Stripe autopay enrollment stays gated on this item.

## Acceptance criteria
- Count of submitted opening SIs equals the AR aging line count; `SELECT SUM(outstanding_amount) FROM \`tabSales Invoice\` WHERE is_opening='Yes' AND docstatus=1` equals the QBO AR aging total to the cent; mirrored check for Purchase Invoice vs AP aging.
- GL balance of the Temporary Opening account as of 2027-01-01 = 0 (machine-check via General Ledger report or `SELECT SUM(debit)-SUM(credit) FROM \`tabGL Entry\` WHERE account=<temporary>` = 0).
- Native Accounts Receivable report as of 2027-01-01 reproduces the QBO aging buckets within rounding.
- Zero Stripe charges created during the load (`SELECT COUNT(*) FROM \`tabStripe Payment\`` still 0 — prod_finance_native baseline).
- Interim on-account Payment Entries (Jan 1–~15) fully reconciled against opening invoices via Payment Reconciliation after the load (no unallocated pre-cutover receipts remain).

## Rollback
Cancel the opening invoices (bulk cancel by the opening naming series filter); Temporary Opening returns to the WI-032 residue; re-load.

## Explicitly NOT in this work item
Historical PAID invoices (out of scope per rule 8); credit-note history; Stripe enablement; customer statement print formats.

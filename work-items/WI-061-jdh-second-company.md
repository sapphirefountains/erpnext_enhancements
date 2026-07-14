# WI-061: JDH as second Company — **ON HOLD**
**Phase:** 2   **Type:** CONFIG   **Size:** M
**Blocked by:** OD-1 being REOPENED; WI-004 (the reusable CoA template)   **Blocks:** nothing

> **ON HOLD (2026-07-14): OD-1 was resolved "No" — JDH stays out of ERPNext (branch a).** This item is retained unscheduled solely because the decision is cheap to reverse later: WI-004's company-agnostic numbered CoA template makes adding a second Company a clean import at any time. Do not execute unless the business reopens OD-1.

## Why
The CEO's second company, JDH, may enter ERPNext (OD-1). The Phase-1 CoA was deliberately designed company-agnostic (numbered template, no 'SF' embedded in names) precisely so this item is a clean import rather than a rebuild. Verified: no JDH Company record exists on either instance today.

## Native-first check
Native multi-company is the whole mechanism: per-company chart via **Chart of Accounts Importer** (same CSV, automatic ' - <abbr>' suffixing), native inter-company accounts, native `represents_company` on Customer/Supplier for cross-company charging, per-company Cost Centers/Fiscal-Year defaults, and the native consolidated Balance Sheet/P&L views across companies. Verdict: fully native CONFIG. No custom consolidation tooling — building any would be a defect.

## Preconditions
- OD-1 ruled: JDH enters ERPNext (branch (b) now / (c) Phase 2).
- WI-004's CoA CSV merged and proven by the Sapphire import (WI-029).
- Business facts collected: JDH legal name, abbreviation, fiscal calendar, tax posture, bank accounts, and whether JDH transacts with Sapphire (drives inter-company setup).

## Scope
- Create Company 'JDH' (abbr per business), country US, USD.
- Import the WI-004 CoA CSV under JDH via Chart of Accounts Importer; set JDH Company default accounts (receivable/payable/bank/cash).
- Inter-company: create the inter-company debtor/creditor accounts in both charts; `represents_company` Customer/Supplier pairs if the companies cross-charge.
- Per-company clones of company-scoped controls: the WI-013 Authorization Rule (Authorization Rule is per-company), Mode of Payment Account rows, Bank/Bank Account masters for JDH's real accounts, tax templates per JDH's own OD-2-style ruling.
- **Stripe gap analysis (enumerate, don't assume):** the custom Stripe module is single-company (one `company` field on Stripe Payments Settings). If JDH takes card/ACH payments, that is a separate justified work item (multi-company settings or a second Stripe account strategy) — flagged here, not built.
- JDH opening balances follow the same methodology as WI-032/033 (own trial balance, own open AR/AP) — scoped as its own mini-plan when the data source is known.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabCompany` = 2; JDH chart account count equals the template row count; every JDH leaf account has a non-NULL account_number.
- JDH Trial Balance renders without error; the consolidated Balance Sheet renders across both companies.
- If cross-charging: one test inter-company invoice pair posts and eliminates correctly in the consolidated view.

## Rollback
Delete the JDH Company while its ledger is empty (deletes its chart with it); inter-company accounts on the Sapphire side are disabled, not deleted.

## Explicitly NOT in this work item
JDH historical data migration (own plan once OD-1 lands); Stripe-for-JDH build; payroll for JDH employees (same rule-3 boundary: external firm + summary JE).

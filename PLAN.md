# Sapphire Fountains ERPNext Migration — Master Plan

Sapphire Fountains (fountain/water-feature design, build, service, rentals, products; Utah) migrates from QuickBooks Online + QuickBooks Time to ERPNext v16 (Frappe Cloud) with Stripe payments. **Target cutover: January 1, 2027** — the date is fixed; scope flexes into Phase 2.

This plan was built against the **live systems on 14 July 2026** (production `erp.sapphirefountains.com`, test `sapphirefountainstest.v.frappe.cloud`, and the `erpnext_enhancements` v1.155.0 custom app repo), not against the planning brief's assumptions. Where the brief and the live system disagreed, the live system won and the conflict is flagged below.

Companion documents:
- [`decisions/OPEN-DECISIONS.md`](decisions/OPEN-DECISIONS.md) — the 7 business decisions **with their 14 Jul 2026 resolutions** (OD-1 No-JDH · OD-2 follow-Utah-law/branch-b w/ CPA written confirmation as sign-off gate · OD-3 rename Rent→Events · OD-4 branch a · OD-5 Jan 1 committed, sooner if possible · OD-6 bulk delete · OD-7 no surcharge at launch)
- [`work-items/`](work-items/) — 65 self-contained work items (WI-001 … WI-065; WI-061 ON HOLD per OD-1)

---

## 1. Ground truth — the brief's figures, re-verified 14 Jul 2026

| Brief claim | Verdict | Measured |
|---|---|---|
| QBO disconnected since 23 Jun; refresh token invalid | CONFIRMED | Tokens cleared to NULL by `clear_oauth_tokens` after `invalid_grant`; app OAuth creds (client_id/secret/redirect_uri) intact → reconnect = user OAuth re-grant only. A failed reconnect attempt is evident 30 Jun. |
| 295 records in failed state | CONFIRMED* | 295 failed **sync-log runs** (not entity records), FROZEN since 23 Jun. Plus 1 hung 'Running' log (QBO-SYNC-2026-206829). |
| Sync failing hourly | WRONG | The 3 hourly jobs still fire but early-return while disconnected; zero new log rows since 23 Jun 17:00. The 295 count is static, not growing. |
| 622 projects; estimated_costing = 0 on all | DRIFTED | **625** projects; `estimated_costing` 0/NULL on all 625. `custom_project_dollar_amount` > 0 on **52** (8.3%). |
| 96% unassigned / 84% no end date | CONFIRMED | 600/625 `_assign` empty; 522/625 `expected_end_date` NULL. |
| project_type distribution | DRIFTED | Service 348 · Build 75 · (none) 71 · Rent 61 · Design 47 · Internal 13 · Other 8 · **Group Projects 2** (omitted in brief). |
| 226 closed-won, no Project, no won-dates | WRONG | **280** Closed Won; **196** with no project via ANY of the 3 linkage fields that exist (`Opportunity.custom_project`, `Opportunity.custom_created_project`, reverse `Project.custom_opportunity` — inconsistently populated). Won-dates EXIST: `custom_date_closed_won` (57/280) and `custom_stage_changed_on` (280/280, reliable proxy). |
| 1,351 customers, all customer_group NULL | WRONG | **1,602** customers; 1,146 NULL/empty group; **454 already 'Government'**; `customer_type` already carries Commercial=1040 / Company=365 / Residential=172. |
| Quotation table empty | WRONG | **638 Quotations, all draft** (QBO Estimate imports). |
| Stripe disabled in prod, not configured in test | HALF-WRONG | Prod: unconfigured. **Test: enabled=1, card+ACH+surcharge ON.** The Stripe module is fully merged on `main` — not greenfield. |
| SKU schema CON/SPP/RAW/SUB/FTN/PKG/SVC | DRIFTED | The taxonomy exists as the 16-node Item Group tree; only CON- is applied (91 items, on `item_code`); the field literally named `custom_sku` holds 14 unrelated ad-hoc codes; 257/583 items sit on the root group. |

### Load-bearing discoveries beyond the brief

1. **Prod ERPNext has no posted books.** Every QBO-imported financial document is a docstatus-0 draft: 12,341 Journal Entries, 1,563 Sales Invoices, 1,405 Payment Entries, 638 Quotations. GL Entry = 4 rows; zero Opening Entries; Bank / Bank Account / Bank Transaction all empty. The cutover therefore includes a **draft-mirror quarantine** (WI-028), and "stale data" is really "no books yet."
2. **Test already piloted the accounting cutover**: a submitted Opening Entry of $909,722.12 dated 2025-12-31, 157 GL entries, a full QBO chart (281 accounts), Stripe live. The opening-balance methodology has a validated dry run to re-execute at 2026-12-31.
3. **The brief's §8 dependency chain is built, unused**: the Time Kiosk (Job Interval doctype with `employee` and `project` both required) and the in-app Job Interval→Timesheet conversion (`api/time_kiosk.py::sync_interval_to_timesheet`, fired on Stop/Switch) exist — but `tabJob Interval` has **0 rows ever**. The gap is adoption and costing rates, not code.
4. **The Closed-Won handoff engine exists** (`crm_enhancements/api.py::create_project_from_opportunity_background`): prompt on transition to Closed Won, idempotent on `custom_created_project`, sets `project_type` from the `custom_value_stream` child table (priority Design > Build > Service > Rent), copies ~11 child tables, provisions the Drive folder, notifies AE + PM. The one missing piece: **no Sales Invoice → Project auto-association exists anywhere** (closed natively via the SO chain, WI-008).
5. **Stripe is fully on main** (commits v1.65.0/v1.66.0): hosted Checkout (card + ACH), signature-verified webhooks that create and submit Payment Entries, surcharge pass-through, saved-method autopay, /pay portal. Verified gaps: (a) one combined `deposit_account` — money posts **gross**; (b) Stripe's own fees are never journaled; (c) **no payout ingestion** — no clearing→bank JE; (d) refund GL reversal is manual; (e) disputes unhandled. These gaps are WI-040/WI-041.
6. **Plaid is balances-only** (only `/accounts/balance/get`; writes a snapshot single; creates no Bank Transactions) → native **Bank Statement Import + Bank Reconciliation Tool** is the day-one banking path (WI-042/043); Plaid transaction sync is a Phase-2 enhancement (WI-056).
7. **Native controls are all present and empty**: Budget 0 rows, **Authorization Rule 0 rows (the doctype exists in this v16 build)**, Accounting Dimension 0, Tax Category 0, Tax Rule 0, Payment Gateway Account 0. The 'Purchase Invoice Approval' and 'Payment Entry Approval' workflows ship as app fixtures, `is_active=0`, and **`allow_self_approval=1` on every transition — which defeats segregation of duties and must be fixed before activation** (WI-015/WI-044).
8. **Reports**: 16 of 17 requested native financial reports present and enabled; 'Tax Detail' is absent from this build.
9. **Chart of Accounts**: prod has 359 QBO-imported accounts with '(deleted)' junk and mostly-NULL account numbers; test has a divergent 281. Single company 'Sapphire Fountains' (SF) on both — **no JDH company exists anywhere**. Fiscal years 2008–2026 all open; **FY 2027 does not exist yet**.
10. **Modes of Payment**: 21 enabled, only 2 have default accounts — blocks Payment Entry automation (WI-031).
11. **QBO sync**: one-way QBO→ERPNext across 13 doctypes (Projects link-only, never created; addresses NOT imported). The pre-reconnect fix (PR #571: no-op save-skip + `job_merge_no_project`) is **merged on main**. `sync_enabled` only gates the CDC poll — the full kill is Disconnect + deleting the Intuit webhook subscription + (later) removing the 3 hourly hooks. Sync-only doctypes (inert after retirement): QuickBooks Sync Mapping / Sync Log / Raw Payload.
12. **Promotion path** (verified): one pipeline — Frappe Cloud deploys `main` to BOTH sites; fixtures + `after_migrate` + patches are the config-as-code mechanism; test-vs-prod differentiation is runtime feature flags (Settings singles, mostly default OFF). No Role / Role Profile / Authorization Rule fixtures exist yet — roles ship via the app's `seed_*_role` patch pattern.
13. **Bulk-operation hazards** for every DATA item: a wildcard `'*'` after_save hook → `global_triton_sync` (no flag/settings guard — one queued POST per ORM save); `Customer.after_insert` → Drive folder creation (gated by `create_customer_folders`); `Opportunity.on_update` → closed-won prompt. All remediation scripts use `frappe.db.set_value`/SQL, batching, and toggle checks (WI-050 enforces this).
14. **QuickBooks Time webhook** (`quickbooks_time/api.py::qb_timesheet_webhook`) is guest-callable with **no signature verification** — a live security gap; it dies at cutover regardless of kiosk adoption (WI-046).
15. The custom **'Month-End Close'** doctype (exact hyphenated name) exists — submit freezes `Company.accounts_frozen_till_date`, Accounts-Manager-only — wired to nothing; adopted post-cutover (WI-049).
16. People: 18 Employees (14 active), 23 enabled system users, 17 Role Profiles on prod (none in fixtures). Suppliers 1,156 — **77% 'Staffing'** (subcontract labor cost matters as much as materials).

---

## 2. Native-first audit

| Requirement | Native feature evaluated | Verdict |
|---|---|---|
| Balance Sheet / P&L / GL / Trial Balance / Cash Flow | Stock script reports (verified present + enabled) | **Native. Zero build.** Verification only. |
| Budget variance / profitability | Budget Variance Report, Profitability Analysis (present) | Native; usable in Phase 2 once budgets exist. |
| Quotation→SO→Project→SI chain | Core selling flow; `Sales Order.project`, `Sales Invoice.project` native fields; SO→SI mapping carries project | **Native = CONFIG** (+ Property Setters). No code (WI-007/008). |
| Opportunity-won → Project auto-create | Native `make_project` vs the custom handoff engine (built, richer) | Keep existing APP_CODE (already built); config/verify only (WI-009/048). |
| SI auto-association to Project | Project flows Quotation→SO→SI natively once set on the SO | Native via chain; visibility Property Setter + saved filter (WI-008). |
| PO dollar-threshold CEO escalation | **Authorization Rule** (doctype verified present, 0 rows) | **Native**, shipped version-controlled as fixture/seed patch (WI-013). |
| %-of-budget escalation | Authorization Rule (fixed-value only) / Budget (GL-axis) | Both insufficient → justified Phase-2 APP_CODE (WI-058), blocked on budget data. |
| Project cost attribution | `Purchase Order Item.project`, `Timesheet Detail.project`, Activity Cost costing rates → Project costing | Native CONFIG + adoption (WI-014/016/021). |
| Approval workflows PI/PE | Native Workflow (fixtures exist, dormant) | Native; fix `allow_self_approval` + activate (WI-015/044). |
| SoD / roles | Role Profiles (17 exist) + permissions | Native CONFIG → fixture/patch-ized (WI-010/011/012). |
| Bank feed + reconciliation | Bank / Bank Account / Bank Transaction / **Bank Statement Import** / Bank Reconciliation Tool (all verified present) | **Native day-one** (manual statement import, WI-042/043). Plaid tx sync = optional Phase-2 APP_CODE (WI-056). |
| Stripe checkout / webhooks / autopay | payments-app gateway (unconfigured) vs custom module (built, tested) | Keep the custom module — already supports ACH/surcharge/autopay the native gateway lacks. |
| Stripe fee/payout accounting | Bank Rec Tool (matches, can't author fee-split JEs); nothing native ingests gateway payouts | **Justified APP_CODE gap**: payout→JE + fee expense (WI-040), refund reversal + dispute alerts (WI-041). |
| Payment links | Native Payment Request vs custom hosted-checkout links | Custom module already does links incl. SMS; keep. |
| Sales-tax automation | Tax Category + Tax Rule (present, empty) + Sales Taxes and Charges Templates (3 exist) | Native CONFIG, gated OD-2 (WI-036/037). |
| Sales-tax liability report | Sales Register / Item-wise Sales Register (present); 'Tax Detail' absent | **Native is sufficient for day-one filing**: per-jurisdiction liability sub-accounts make the GL the jurisdiction schedule; Sales Register supplies the base. Custom query report only if two real filing cycles prove the registers can't export the CPA's workpaper (WI-038). |
| Revenue by Commercial/Residential | **Accounting Dimension 'Segment'** (native statements filterable) + Sales Analytics by Customer Group | **Native** (dimension captured at entry; project attribute first, customer fallback). A custom union report is fallback-ONLY, requiring OD-4 to reject the dimension + separate approval (WI-054). |
| Monthly close (post-cutover) | `accounts_frozen_till_date` + Period Closing Voucher; custom 'Month-End Close' doctype exists | Native mechanism + existing custom wrapper; adoption only (WI-049). |
| Hours export to payroll firm | Stock Timesheet reports ('Daily Timesheet Summary' verified present) vs query report | Native report evaluated first; ONE small Query Report fixture if the firm's template demands a different shape (WI-017). |
| Payroll return JE | Journal Entry + Journal Entry Template (verified present); Auto Repeat rejected (amounts vary) | Native (WI-047). |
| Drive folder per project | Custom google_drive module (built, wired) | Built; operator config only — service account must be Content Manager on the Shared Drive (WI-006). |
| Opening balances | Opening Entry JE + `is_opening` invoices + Opening Invoice Creation Tool + Chart of Accounts Importer (all verified present) | Native DATA; the app's own `opening_balances.py` loader is reused (WI-032/033). |
| Doc-AI intake | accounting_intake module (built) | Built; Phase-2 rollout config (WI-059). |

**Field verification:** an adversarial audit verified every native doctype and field named in the work items against live production (18/19 doctypes exact; every field present, including all custom fields cited in acceptance-criteria SQL). The two naming drifts found are already corrected in the work items: the 'Naming Series' tool is **'Document Naming Settings'** in this build, and the close doctype is **'Month-End Close'** (hyphenated).

---

## 3. Phases and rationale

- **Phase 0 (now → Nov 2026)** — everything that changes no production behavior (fixtures land dormant, designs, TEST-side config, master-data prep) or must run for months to be real (QBO monthly closes, the reconnect). Risk-free lead time; all of it promotes through the verified fixtures/feature-flag pipeline.
- **Phase 1 (Dec 2026 → Jan 2027)** — the December window and cutover week: kiosk pilot + parallel run on TEST, then the destructive/ledger operations on prod in strict order inside one freeze window with backups, then go-live config flips.
- **Phase 2 (2027)** — anything requiring people to change habits first (budget discipline → percentage escalation), anything gated on a late-landing business/legal decision (JDH, surcharge, segment), and efficiency upgrades over working manual paths (Plaid transactions, doc-AI, forecasting, perpetual inventory). Nothing in Phase 2 blocks January 1.

### Cutover-window ordering (binding, encoded in WI-051's runbook)

```
final CDC/Import All  →  sync_enabled=0 + DELETE Intuit webhook subscription (partial kill; OAuth tokens stay ALIVE)
  →  WI-028 draft-mirror delete  →  WI-029 CoA rebuild (+ re-point Sync Mapping, Company defaults, Stripe settings)
  →  WI-030 FY/naming + WI-031 modes of payment
  →  WI-032 opening TB JE (pulls the QBO TB via the still-live API)  →  WI-033 opening AR/AP  →  WI-034 open POs
  →  WI-035 tie-out + sign-off  →  QBO to read-only  →  WI-045 full Disconnect
```

Tokens stay alive until after WI-035 because the opening tools (`opening_balances.py`, `compare_account_balances`) call the live QBO API; the webhook deletion happens **first** because inbound webhooks bypass `sync_enabled` and could re-import deleted drafts.

**Opening-AR timing (honest):** opening AR cannot exist before the December close completes (~Jan 10–15). From Jan 1 until WI-033 posts, payments against pre-cutover invoices are recorded as on-account Payment Entries and reconciled to the opening invoices via native Payment Reconciliation afterward. Stripe autopay enrollment is hard-gated on WI-033 acceptance.

---

## 4. Dependency graph

```mermaid
graph TD
  subgraph P0["Phase 0 (Aug–Nov 2026)"]
    WI001[WI-001 neutralize failed logs] --> WI002[WI-002 QBO reconnect]
    WI003[WI-003 monthly QBO closes ▲GATE]
    WI004[WI-004 CoA design + mapping]
    WI005[WI-005 Stripe clearing accts]
    WI010[WI-010 role fixtures] --> WI011[WI-011 user access map]
    WI011 --> WI012[WI-012 MR→PO split]
    WI012 --> WI013[WI-013 $-threshold Auth Rule]
    WI010 --> WI013
    WI016[WI-016 activity costing]
    WI015[WI-015 self-approval fixture fix]
    WI007[WI-007 selling settings] --> WI008[WI-008 SI→project rule]
    WI009[WI-009 PRJ naming fixture]
    WI018[WI-018 workspace] --> WI019[WI-019 form pruning]
    WI020[WI-020 print formats]
  end
  subgraph P1["Phase 1 (Dec 2026 – Jan 2027)"]
    WI011 --> WI021[WI-021 Time Kiosk pilot]
    WI016 --> WI021
    WI021 --> WI022[WI-022 parallel run/UAT]
    WI021 --> WI017[WI-017 hours export validated]
    WI017 --> WI047[WI-047 payroll JE return]
    WI002 --> FINAL[final sync + webhook removed]
    FINAL --> WI028[WI-028 draft-mirror delete]
    WI004 --> WI029[WI-029 CoA rebuild]
    WI028 --> WI029
    WI029 --> WI030[WI-030 FY2027/naming] --> WI032
    WI029 --> WI031[WI-031 modes of payment]
    WI003 --> WI032[WI-032 opening TB JE]
    WI032 --> WI033[WI-033 opening AR/AP] --> WI035[WI-035 tie-out + sign-off]
    WI034[WI-034 open POs] --> WI035
    WI035 --> WI045[WI-045 QBO disconnect]
    WI033 --> WI044[WI-044 workflows active]
    WI015 --> WI044
    WI005 --> WI040[WI-040 payout JE APP_CODE]
    WI005 --> WI039[WI-039 Stripe go-live]
    WI033 --> WI039
    WI042[WI-042 bank masters] --> WI043[WI-043 bank rec runbook]
    WI040 --> WI043
    WI021 --> WI046[WI-046 QB Time retired]
    WI022 --> WI051[WI-051 runbook + go/no-go]
    WI035 --> WI051
    WI023[WI-023 quotation triage] --> WI028
    WI050[WI-050 hazard verification] -.gates ALL bulk DATA incl. WI-023..028.-> WI028
  end
  subgraph P2["Phase 2 (2027)"]
    WI045 --> WI052[WI-052 QBO code removal]
    WI021 --> CHAIN[labor actuals on jobs]
    WI016 --> CHAIN
    WI014[WI-014 project on PO lines] --> CHAIN2[material actuals on jobs]
    CHAIN --> WI057[WI-057 budget discipline]
    CHAIN2 --> WI057
    WI057 --> WI058[WI-058 % escalation 75/85]
    WI013 --> WI058
    WI043 --> WI056[WI-056 Plaid tx sync]
    WI039 --> WI055[WI-055 surcharge gate]
  end
  OD2{{OD-2 RESOLVED: Utah-law branch b — CPA written matrix = sign-off gate}} -.-> WI036[WI-036/037/038 tax config+report]
  WI026[WI-026 customer groups — OD-4 resolved a]
  WI065[WI-065 Rent→Events rename] --> WI027[WI-027 project types]
  OD6{{OD-6 RESOLVED: bulk delete}} -.-> WI028
```

*(Decision resolutions of 14 Jul 2026 baked in: OD-1 No → WI-061 ON HOLD and removed from the graph; OD-3 rename → WI-065 added; OD-4 branch a, OD-6 delete, OD-7 no-surcharge-at-launch recorded in the register.)*

The brief's §8 chain appears explicitly: **WI-021** (kiosk = source of hours) + **WI-016** (costing rates) → labor actuals on jobs; **WI-014** → material actuals; both → **WI-057** (budgets have actuals to calibrate against) → **WI-058** (the 75% rule can finally fire). **WI-046 (QuickBooks Time retirement) is downstream of WI-021 adoption — retiring QB Time is a prerequisite chain-link, not a side quest** (though its unauthenticated guest webhook dies at cutover regardless).

*The graph is a reduced view; the work-item index Blocked-by column is authoritative (transitive edges such as WI-034←WI-003/WI-025, WI-031←WI-042, WI-047←WI-029 are omitted for readability).*

---

## 5. Critical path to January 1

1. **Ledger chain (longest, least compressible):** WI-003 monthly closes (December close lands ~Jan 10–15) → WI-032 → WI-033 → WI-035. Everything upstream (WI-023 → WI-028 → WI-029 → WI-030/031) must complete in the last week of December. The company transacts live in ERPNext from Jan 1 on an empty ledger; opening entries post backdated once the December close freezes. Pre-cutover receivables have no ERPNext invoices until ~Jan 10–15 — the on-account interim procedure covers collections. **Slack: zero after ~Dec 26.**
2. **QBO reconnect (WI-001 → WI-002) is this-sprint urgent** — every week widens the catch-up gap, and it unblocks the opening tools (CSV fallback documented: +2–4 days of manual keying).
3. **People chain:** WI-010 → WI-011 by end-November; WI-021 kiosk pilot needs the FULL December window (human behavior change is not compressible), feeding WI-022 UAT → WI-051 go/no-go (~Dec 22).
4. **Stripe chain:** WI-040 (the only L-size APP_CODE on the path) starts early against Stripe test mode; its degraded fallback (manual clearing-sweep JE per payout, in WI-043) means it cannot block go-live.
5. **Everything else** (tax automation if the CPA confirmation lands late, segment reporting, Plaid, surcharge, doc-AI) has a documented manual fallback or lives in Phase 2. Scope flexes; the date does not (OD-5: Jan 1 committed).
6. **Finishing sooner (OD-5 note):** a cutover is structurally identical at any month-end — the opening TB just cuts from that month's close. The binding constraints on pulling forward are the close discipline being real, the kiosk-adoption month, and the parallel-run month. If every Phase-0 item is green by late October, evaluate a Dec-1 cutover (Nov-30 close) at that point; otherwise hold Jan 1.

---

## 6. Work-item index

| WI | Title | Phase | Type | Size | Blocked by |
|---|---|---|---|---|---|
| [WI-001](work-items/WI-001-qbo-failed-log-neutralization.md) | Reap hung QBO run + neutralize 295 failed sync logs | 0 | DATA | S | — |
| [WI-002](work-items/WI-002-qbo-oauth-reconnect.md) | QBO OAuth reconnect + 2026 operating mode | 0 | CONFIG | S | WI-001 |
| [WI-003](work-items/WI-003-qbo-monthly-close-discipline.md) | Phase-0 monthly close discipline in QBO (**hard gate**) | 0 | DATA | M | — |
| [WI-004](work-items/WI-004-coa-design-and-mapping.md) | CoA target design + 359-row mapping workbook | 0 | DATA | M | (OD-1 enumerated, non-blocking) |
| [WI-005](work-items/WI-005-stripe-clearing-accounts.md) | Stripe Clearing + Merchant Fees accounts + routing | 0 | CONFIG | S | — |
| [WI-006](work-items/WI-006-drive-service-account-config.md) | Google Drive SA config + provisioning verification | 0 | CONFIG | S | sequenced w/ WI-002 |
| [WI-007](work-items/WI-007-selling-settings-o2c-chain.md) | Selling Settings + native O2C chain config | 0 | CONFIG | S | — |
| [WI-008](work-items/WI-008-si-project-association.md) | SI→Project association (native chain + ad-hoc rule) | 0 | CONFIG | S | WI-007 |
| [WI-009](work-items/WI-009-project-naming-series-fixture.md) | Project naming-series PRJ- continuity fixture | 0 | FIXTURE | S | — |
| [WI-010](work-items/WI-010-role-profile-fixtures.md) | Fixture-ize Roles + Role Profiles; seed PO Approver | 0 | FIXTURE | M | — |
| [WI-011](work-items/WI-011-user-access-mapping.md) | Per-employee access mapping (23 users / 18 employees) | 0 | CONFIG | M | WI-010 |
| [WI-012](work-items/WI-012-mr-po-role-split.md) | MR (team lead) → PO (PM) role split | 0 | CONFIG | M | WI-011 |
| [WI-013](work-items/WI-013-po-authorization-rule.md) | PO dollar-threshold CEO escalation (Authorization Rule) | 0 | FIXTURE | S | WI-010, WI-012, threshold decision |
| [WI-014](work-items/WI-014-project-on-purchase-lines.md) | Project on PO/PI lines + 'Internal' overhead pattern | 0 | FIXTURE | S | — |
| [WI-015](work-items/WI-015-workflow-self-approval-fix.md) | Workflow fixture repair: allow_self_approval 1→0 (dormant) | 0 | FIXTURE | S | — |
| [WI-016](work-items/WI-016-activity-cost-labor-costing.md) | Activity Type + Activity Cost labor costing | 0 | CONFIG | M | burdened rates from payroll firm |
| [WI-017](work-items/WI-017-payroll-hours-export.md) | Payroll hours-export report + firm contract | 0 | FIXTURE | M | WI-021 for validation |
| [WI-018](work-items/WI-018-accountant-workspace.md) | Accountant workspace + desk curation | 0 | CONFIG | M | WI-011 |
| [WI-019](work-items/WI-019-form-simplification.md) | Form simplification Property Setter fixtures | 0 | FIXTURE | M | WI-018 |
| [WI-020](work-items/WI-020-sales-print-formats.md) | Print formats: Quotation / SO / SI (+ Letter Head) | 0 | FIXTURE | M | — |
| [WI-021](work-items/WI-021-time-kiosk-rollout.md) | Time Kiosk pilot → field rollout (Dec) | 1 | CONFIG | L | WI-011, WI-016 |
| [WI-022](work-items/WI-022-december-parallel-run-uat.md) | December parallel run, UAT + training (on TEST) | 1 | DATA | L | Phase-0 set |
| [WI-023](work-items/WI-023-quotation-disposition.md) | Disposition of 638 draft QBO-Estimate Quotations | 1 | DATA | M | WI-007 |
| [WI-024](work-items/WI-024-opportunity-project-link-triage.md) | Opp↔Project canonical link + 196-orphan triage | 1 | DATA | M | — |
| [WI-025](work-items/WI-025-item-group-rollout.md) | Item Group rollout (583 items into taxonomy) | 1 | DATA | M | — |
| [WI-026](work-items/WI-026-customer-group-backfill.md) | Customer Group backfill (1,146 ungrouped) | 1 | DATA | M | — (OD-4 resolved: branch a) |
| [WI-027](work-items/WI-027-project-type-backfill.md) | project_type backfill (71 untyped + 2 'Group Projects') | 1 | DATA | S | WI-065 (OD-3 resolved: rename) |
| [WI-028](work-items/WI-028-draft-mirror-quarantine.md) | Draft-mirror quarantine: bulk delete QBO drafts | 1 | DATA | M | WI-023, final sync + webhook removed (OD-6 ratified: delete) |
| [WI-029](work-items/WI-029-coa-rebuild-execution.md) | Execute CoA rebuild on prod | 1 | DATA | L | WI-004, WI-028 |
| [WI-030](work-items/WI-030-fy2027-naming-hygiene.md) | FY2027 + disable legacy FYs + naming-series reset | 1 | CONFIG | S | WI-029 |
| [WI-031](work-items/WI-031-mode-of-payment-rationalization.md) | Mode of Payment rationalization + defaults + AP run | 1 | CONFIG | M | WI-029, WI-042 |
| [WI-032](work-items/WI-032-opening-trial-balance-je.md) | Opening Trial Balance JE @2026-12-31 | 1 | DATA | M | WI-003, WI-029, WI-030 |
| [WI-033](work-items/WI-033-opening-ar-ap-invoices.md) | Opening AR/AP as is_opening invoices | 1 | DATA | L | WI-032; autopay=0 gate |
| [WI-034](work-items/WI-034-open-po-rekey.md) | Re-key open Purchase Orders (SOs confirmed zero) | 1 | DATA | S | WI-003, WI-029, WI-025 |
| [WI-035](work-items/WI-035-opening-reconciliation-signoff.md) | Opening reconciliation + sign-off gate | 1 | DATA | S | WI-032/033/034 |
| [WI-036](work-items/WI-036-sales-tax-templates.md) | Sales-tax templates rebuild (Utah-law branch b) | 1 | CONFIG | M | WI-029; CPA written matrix = sign-off gate |
| [WI-037](work-items/WI-037-tax-category-rule-automation.md) | Tax Category + Tax Rule automation (branch b) | 1 | CONFIG | M | WI-036; CPA written matrix = sign-off gate |
| [WI-038](work-items/WI-038-sales-tax-filing-procedure.md) | Utah sales-tax filing procedure (native reports) | 1 | CONFIG | S | WI-036/037 |
| [WI-039](work-items/WI-039-stripe-production-golive.md) | Stripe production go-live (keys, webhook, /pay, autopay gate) | 1 | CONFIG | M | WI-005; enrollment gated on WI-033 |
| [WI-040](work-items/WI-040-stripe-payout-ingestion.md) | Stripe payout ingestion → clearing-sweep JE + fee expense | 1 | APP_CODE | L | WI-005 |
| [WI-041](work-items/WI-041-stripe-refund-dispute-handling.md) | Stripe refund-reversal PE + dispute alerting | 1 | APP_CODE | M | WI-005/039 |
| [WI-042](work-items/WI-042-bank-masters.md) | Bank + Bank Account masters | 1 | CONFIG | S | account list from finance |
| [WI-043](work-items/WI-043-bank-reconciliation-runbook.md) | Bank rec runbook: statement import + payout matching | 1 | CONFIG | M | WI-042 (WI-040 for auto-match) |
| [WI-044](work-items/WI-044-pi-pe-workflow-activation.md) | Activate PI/PE approval workflows w/ real SoD | 1 | FIXTURE | M | WI-015, WI-011, WI-033 done, Stripe-PE test |
| [WI-045](work-items/WI-045-qbo-retirement-kill-checklist.md) | QBO retirement kill checklist (webhook step early) | 1 | CONFIG | S | WI-035 |
| [WI-046](work-items/WI-046-qb-time-retirement.md) | QB Time retirement + guest-webhook removal | 1 | APP_CODE | M | WI-021 adoption gate (webhook dies at cutover regardless) |
| [WI-047](work-items/WI-047-payroll-summary-je.md) | Payroll summary-JE return (template + mapping) | 1 | CONFIG | S | WI-017, WI-029 |
| [WI-048](work-items/WI-048-process-automation-flag-gonogo.md) | process_automation_enabled go/no-go | 1 | CONFIG | S | WI-009, WI-010, WI-011 |
| [WI-049](work-items/WI-049-month-end-close-adoption.md) | Month-End Close adoption (dry-run Dec; first close Feb) | 1 | CONFIG | S | WI-011, WI-035 |
| [WI-050](work-items/WI-050-integration-hazard-verification.md) | Integration-hazard verification before DATA windows | 1 | CONFIG | S | — (gates all bulk DATA) |
| [WI-051](work-items/WI-051-cutover-runbook.md) | Cutover runbook, go/no-go checklist, day-1 support | 1 | DATA | M | WI-022 + gates |
| [WI-052](work-items/WI-052-qbo-code-removal.md) | Remove QBO hooks; retire/tolerate QBO surfaces | 2 | APP_CODE | M | WI-045 + 30 days |
| [WI-053](work-items/WI-053-period-summary-je-import.md) | Period-summary JE trend import (24 months; optional, recommended) | 2 | DATA | M | WI-003, WI-004, WI-035 |
| [WI-054](work-items/WI-054-revenue-by-segment.md) | Revenue-by-segment via Accounting Dimension (OD-4 branch a) | 2 | CONFIG | M | WI-026, WI-027 |
| [WI-055](work-items/WI-055-stripe-surcharge-gate.md) | Surcharge go-live compliance gate (stays OFF until 8-item list; OD-7: no surcharge at launch) | 2 | CONFIG | M | WI-039, WI-041, 8-item checklist |
| [WI-056](work-items/WI-056-plaid-transactions-sync.md) | Plaid /transactions/sync → Bank Transaction upserts | 2 | APP_CODE | L | WI-042, WI-043 stable 1 mo |
| [WI-057](work-items/WI-057-project-budget-discipline.md) | Project budget discipline | 2 | DATA | M | WI-021 chain live, WI-014 |
| [WI-058](work-items/WI-058-percentage-po-escalation.md) | Percentage-of-budget PO escalation, 85% cap + override | 2 | APP_CODE | M | WI-057, WI-013 |
| [WI-059](work-items/WI-059-document-ai-intake-rollout.md) | Document-AI intake rollout (module built; enable + train) | 2 | CONFIG | M | WI-044 |
| [WI-060](work-items/WI-060-inventory-perpetual-cogs.md) | Inventory: perpetual valuation + COGS reclassification | 2 | CONFIG | L | WI-025, WI-029 |
| [WI-061](work-items/WI-061-jdh-second-company.md) | JDH as second Company — **ON HOLD (OD-1 resolved: No)** | 2 | CONFIG | M | OD-1 reopened, WI-004 |
| [WI-062](work-items/WI-062-cash-flow-forecasting.md) | Cash-flow projection / forecasting (native-first) | 2 | CONFIG | M | WI-035 + 2 close cycles |
| [WI-063](work-items/WI-063-document-hub.md) | Document hub (Drive-backed; define before building) | 2 | CONFIG | S | WI-006 |
| [WI-064](work-items/WI-064-triton-reporting-boundary.md) | Triton management-reporting integration (non-statutory only) | 2 | CONFIG | S | WI-035 |
| [WI-065](work-items/WI-065-rent-to-events-rename.md) | Rename 'Rent' value stream → 'Events' (OD-3; ~60 verified touch points, atomic) | 1 | APP_CODE | M | — |

Type distribution: 17 DATA · 27 CONFIG · 12 FIXTURE · 9 APP_CODE (5 of which are Phase 2) · 0 SERVER_SCRIPT — consistent with the brief's expectation that this project is mostly configuration and data, not code. (WI-061 is ON HOLD per OD-1 and excluded from scheduling.)

---

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Phase-0 close never happens → opening TB from unreconciled books | High | Critical | Hard gate: WI-032 cannot start without the signed-off December close; escalate monthly from Aug (WI-003). |
| QBO reconnect delayed → 3+ weeks of drift compounds | Med | High | WI-001/002 are week-1 work; CSV fallbacks in WI-032/035 cap damage at +2–4 days. |
| Kiosk adoption fails → no labor actuals → Phase-2 % rule slips further | Med | Med | WI-021 has a named owner + weekly usage metric; the dollar-threshold rule (WI-013, no budget dependency) covers day one; QB Time survival past Jan 1 requires an explicit signed rule-4 exception with the webhook already dead. |
| Draft-mirror accidentally submitted post-cutover | Med | High | OD-6 disposition executes before go-live (WI-028); period lock + role restrictions as backstop. |
| Stripe payout accounting wrong → bank never reconciles | Med | High | Clearing-account design (WI-005) + payout-JE APP_CODE with test-mode goldens (WI-040); weekly bank rec in UAT month; manual clearing-sweep fallback (WI-043). |
| Stripe settings dangle after CoA rebuild (Single Link fields skip link-check) | High (as-designed pre-review) | High | WI-029 re-points `Stripe Payments Settings.deposit_account` (+ fee/payout fields); WI-039/040 precondition-verify it resolves. |
| Live-quote deletion in draft-mirror purge | Med (pre-review) | High | WI-023 owns quotation triage and completes before WI-028; one shared keep-list; delete population = marked-historical only. |
| Jan 1–15 AR gap (opening invoices land only after the Dec close) | Certain | Med | Interim on-account Payment Entries, reconciled to opening SIs via native Payment Reconciliation once WI-033 posts (WI-051 runbook). |
| CPA written tax matrix (OD-2 gate) late → tax-config sign-off slips | Med | Med | Direction already set (Utah-law branch b) so design proceeds; rate templates work manually day one; automation is additive; send the CPA request now. |
| allow_self_approval=1 in shipped workflows defeats SoD | Certain (as-is) | Med | WI-015 fixes the fixture early (dormant); WI-044 activates with preparer ≠ approver verified in UAT. |
| Workflow activation breaks Stripe's programmatic Payment Entries | Med | High | WI-044 scopes 'Payment Entry Approval' to payment_type='Pay' and proves the Stripe receive-PE path on TEST before prod. |
| Bulk remediation triggers hook storms (Triton wildcard sync, Drive folders, closed-won prompt) | Med | Med | WI-050 gates every bulk DATA run: `frappe.db.set_value`/SQL, batching, toggle checks, off-hours. |
| Two-instance master-data drift (items 266 vs 583, CoA 281 vs 359) | Certain | Med | Prod is master for masters; UAT scripts pin to entities on both; fixture config is environment-identical by construction. |
| Fiscal-year 2027 missing at rollover | Certain (as-is) | High (blocks all posting) | WI-030 creates FY2027 + disables ancient FYs. |
| Unauthenticated QB Time guest webhook live today | Certain | Med | WI-046 kills it at cutover; if retirement slips, an immediate signature-check/disable hotfix is mandated. |

---

## 8. Test → prod promotion strategy (verified mechanism)

Single pipeline, config-as-code, runtime gating:

1. **Config artifacts** (Custom Fields, Property Setters, Workflows, Print Formats, Notifications, dashboards, Web Pages) ship as **fixtures** in `erpnext_enhancements` (hooks.py fixtures allowlists). Roles / Role Profiles / Authorization Rules have no fixture entries today — new controls ship via the app's established `seed_*` **patch** pattern or new name-in allowlist entries (editing a shipped fixture JSON requires bumping its `modified`, per the app's known sync-skip behavior).
2. **Deploy**: merge to `main` → Frappe Cloud deploys the same code to test and prod; `after_migrate` + patches apply idempotently. GitHub release tags are the deploy log; CI hard-gates version sync.
3. **Runtime gating**: features land dormant (Settings singles default OFF), flip ON in test, validate, then flip ON in prod. This is the promotion path for behavior.
4. **DATA items** are scripted (idempotent, dry-run-by-default, following the app's `job_remediation.py` / `project_name_remediation.py` precedent), rehearsed on test, executed on prod via `bench execute`/enqueue — never hand-clicked. Every DATA WI states its side-effect-storm mitigations.
5. **Never destructive on prod** without a rehearsed, reversible script and a verified Frappe Cloud backup taken immediately before the run.

---

## 9. Review notes (adversarial audit outcomes baked into the work items)

Three independent audits (brief compliance, dependency/sequencing, live field verification) ran against the draft plan; all findings are already applied to the work items. The material ones, kept here for traceability:

- **C1/C7** — the Authorization Rule ships version-controlled (fixture/seed patch), and was struck from the runbook's "hand-replay on prod" list.
- **C8** — WI-023 solely owns the 638-quotation triage and precedes WI-028; one shared keep-list; WI-028 deletes only marked-historical quotations.
- **C9** — the MR/PO Custom DocPerm split ships as fixture/seed patch, not hand-clicked permission state.
- **C10** — the QB Time guest webhook dies at cutover regardless of kiosk adoption; any QB Time survival past Jan 1 is an explicit signed rule-4 exception.
- **C11** — WI-029 re-points `Stripe Payments Settings.deposit_account` (and WI-040's account fields) after the CoA rebuild; WI-039/040 verify resolution before the first live charge.
- **C12** — opening AR lands mid-January; the Jan 1–15 on-account collection procedure is in WI-033/WI-051.
- **C13/C14** — naming corrections verified live: 'Document Naming Settings' (not 'Naming Series'), 'Month-End Close' (hyphenated).

## 10. Definition-of-done cross-check (brief §10)

- [x] Every §1 figure re-verified against the live instance; discrepancies flagged (§1 drift table)
- [x] Every requirement has a recorded native-first check (§2 + per-WI)
- [x] No work item invents a field or doctype name (live-verified; the two naming drifts found were corrected)
- [x] No work item is blocked on an open decision without saying so and enumerating branches (OD-1..OD-7)
- [x] The dependency graph shows the time → cost-attribution → escalation chain, with QB Time retirement as a prerequisite link
- [x] Nothing from §7 appears as planned work (no full-history migration; WI-053 stays within the sanctioned period-summary allowance; no QuickBooks Payroll; no native-report reimplementation; no premature % rule)
- [x] The critical path to January 1 is explicit; overflow moves to Phase 2 rather than compressing the date
- [x] Every work item is executable by an agent with zero prior context

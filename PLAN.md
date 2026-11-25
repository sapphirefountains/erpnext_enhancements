# Sapphire Fountains ERPNext Migration — Master Plan

Sapphire Fountains (fountain/water-feature design, build, service, rentals, products; Utah) migrates from QuickBooks Online + QuickBooks Time to ERPNext v16 (Frappe Cloud) with Stripe payments. **Target cutover: January 1, 2027** — the date is fixed; scope flexes into Phase 2. *(2026-11-25: Jan 1 held, with a hold-or-move check on Dec 11 and a Feb 1 fallback; see §3.)*

This plan was built against the **live systems on 14 July 2026** (production `erp.sapphirefountains.com`, test `sapphirefountainstest.v.frappe.cloud`, and the `erpnext_enhancements` v1.155.0 custom app repo), not against the planning brief's assumptions. Where the brief and the live system disagreed, the live system won and the conflict is flagged below.

Companion documents:
- [`decisions/OPEN-DECISIONS.md`](decisions/OPEN-DECISIONS.md) — the 7 business decisions **with their 14 Jul 2026 resolutions** (OD-1 No-JDH · OD-2 follow-Utah-law/branch-b w/ CPA written confirmation as sign-off gate · OD-3 rename Rent→Events · OD-4 branch a · OD-5 Jan 1 committed, sooner if possible · OD-6 bulk delete · OD-7 no surcharge at launch)
- [`work-items/`](work-items/) — the migration work items WI-001 … WI-070 (WI-061 ON HOLD per OD-1), indexed in §6 below, plus the app-code programmes WI-071 onwards, which are **deliberately not in that index**: §6 is migration-scoped, and the programmes cross-link each other instead.

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
- **Phase 1 (Dec 2026 → Jan 2027)** — the December window and cutover: a finance UAT on TEST, the 2026 ledger posted on prod in three waves as QuickBooks months lock, go-live config flips, then December's wave, the tie-out and the QuickBooks disconnect in January. *(Written 14 Jul as "kiosk pilot + parallel run, then destructive operations inside one freeze window"; superseded by the 2026-11-25 sequence below.)*
- **Phase 2 (2027)** — anything requiring people to change habits first (budget discipline → percentage escalation), anything gated on a late-landing business/legal decision (JDH, surcharge, segment), and efficiency upgrades over working manual paths (Plaid transactions, doc-AI, forecasting, perpetual inventory). Nothing in Phase 2 blocks January 1.

### Cutover sequence (binding, encoded in WI-051's runbook) — rewritten 2026-11-25

The 14 Jul clean cut (final sync → partial kill → WI-028 draft delete → WI-029 chart rebuild → WI-032/WI-033 opening entries in January) no longer applies. Three decisions replaced it: the [OD-6 reversal](decisions/OPEN-DECISIONS.md) (2026-08-31: carry full history; pre-2026 posted that day), keeping the live chart (2026-08-31: WI-029 canceled), and Nikolas's cutover decisions of 2026-11-25 (Jan 1 held with a Dec 11 check and a Feb 1 fallback; WI-032/WI-033 canceled; TEST restored by Dec 2; 2026 posted in waves; James approves bills and payments).

```
WI-003 QBO locked through Sep 30 (Dec 4)  →  Wave 1: Jan–Sep 2026 + pre-2026 leftovers (Dec 9)
  →  hold-or-move check (Dec 11; any miss → Feb 1)
  →  WI-003 locked through Nov 30 (Dec 16)  →  Wave 2: Oct–Nov (Dec 18)
  →  go/no-go (Dec 22)  →  cutover release + config replays (Dec 28–31)
  →  GO-LIVE Jan 1 (first working day Jan 4): QBO users reports-only except Lisa and John; CDC sync stays ON
  →  WI-003 December close locked (Jan 13)  →  Wave 3: December (Jan 14)
  →  WI-035 tie-out: posted ledger at 2026-12-31 == QBO December close; freeze 2026-12-31 (Jan 22)
  →  WI-045 full kill: sync off, webhook deleted, QBO read-only, Disconnect (Jan 29)
```

Each wave runs Sales Invoices → Payment-only resync (Preview, Run, then Refresh drafts for flagged rows) → Payment Entries → Journal Entries, as in [`backlog-gl-posting-runbook.md`](docs/migration/backlog-gl-posting-runbook.md) §5, behind a per-wave gate that includes **0 customers with `custom_stripe_autopay_enabled=1`**: the 365-day `AUTO_CHARGE_MAX_AGE_DAYS` guard does not cover 2026 invoices. The CDC sync and the tokens stay alive until the tie-out, because December's late QuickBooks entries must arrive as drafts for Wave 3 and `compare_account_balances` calls the live QuickBooks API. With no draft delete, there is no longer a reason to cut the webhook early.

**January receipts (honest):** December invoices stay drafts until Wave 3, so a customer payment received Jan 4–14 against one is recorded as an unapplied Payment Entry (party set, no invoice reference) and cleared with native Payment Reconciliation after Wave 3. Stripe autopay enrollment opens only after the WI-035 freeze.

**TEST:** restored from a prod backup by Dec 2 for the finance UAT (WI-022). Its QuickBooks sync, Plaid, Stripe, email and scheduler are off before anyone uses it (WI-051 runbook §2).

**Still open:** clock-in (WI-021 kiosk, WI-016 costing, the QuickBooks Time subscription) is decision 5, not yet taken; none of it gates the books, and the QuickBooks Time guest webhook dies at the cutover release regardless (WI-046).

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
    WI068[WI-068 group-account remap, 2026 window]
  end
  subgraph P1["Phase 1 (Dec 2026 – Jan 2027)"]
    WI011 --> WI021[WI-021 Time Kiosk pilot — timing: decision 5, open]
    WI016 --> WI021
    WI021 --> WI017[WI-017 hours export validated]
    WI017 --> WI047[WI-047 payroll JE return]
    TEST[TEST restored from prod + isolated, Dec 2] --> WI022[WI-022 finance UAT on TEST]
    WI068 --> WAVES
    WI003 --> WAVES[2026 posting waves Dec 9 / Dec 18 / Jan 14 — TASK-2026-01236]
    WI002 --> WAVES
    WAVES --> WI035[WI-035 tie-out to QBO Dec close + freeze]
    WI034[WI-034 re-key missing QBO POs] --> WI035
    WI035 --> WI045[WI-045 QBO disconnect, Jan 29]
    WI030[WI-030 FY2027/naming]
    WI042 --> WI031[WI-031 modes of payment, live chart]
    WI015 --> WI044[WI-044 workflows active — not during a wave]
    WI011 --> WI044
    WI005 --> WI040[WI-040 payout JE APP_CODE]
    WI005 --> WI039[WI-039 Stripe go-live]
    WI035 --> WI039
    WI042[WI-042 bank masters] --> WI043[WI-043 bank rec runbook]
    WI040 --> WI043
    WI021 --> WI046[WI-046 QB Time retired]
    WI022 --> WI051[WI-051 runbook + go/no-go]
    WAVES --> WI051
    WI035 --> WI051
    WI050[WI-050 hazard verification] -.gates ALL bulk DATA incl. each wave.-> WAVES
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
    WI013 --> WI066[WI-066 PO Creator role + SoD gate]
    WI043 --> WI056[WI-056 Plaid tx sync]
    WI039 --> WI055[WI-055 surcharge gate]
  end
  OD2{{OD-2 RESOLVED: Utah-law branch b — CPA written matrix = sign-off gate}} -.-> WI036[WI-036/037/038 tax config+report]
  WI026[WI-026 customer groups — OD-4 resolved a]
  WI065[WI-065 Rent→Events rename] --> WI027[WI-027 project types]
  OD6{{OD-6 REVERSED 2026-08-31: carry full history}} -.-> WAVES
```

*(Decision resolutions of 14 Jul 2026 baked in: OD-1 No → WI-061 ON HOLD and removed from the graph; OD-3 rename → WI-065 added; OD-4 branch a, OD-7 no-surcharge-at-launch recorded in the register. **2026-11-25:** the graph follows the rewritten cutover sequence in §3. WI-028 and WI-029 (canceled 2026-08-31) and WI-032 and WI-033 (superseded 2026-11-25) are no longer in it, and WI-023 moved to Phase 2 with the delete it fed.)*

The brief's §8 chain appears explicitly: **WI-021** (kiosk = source of hours) + **WI-016** (costing rates) → labor actuals on jobs; **WI-014** → material actuals; both → **WI-057** (budgets have actuals to calibrate against) → **WI-058** (the 75% rule can finally fire). **WI-046 (QuickBooks Time retirement) is downstream of WI-021 adoption — retiring QB Time is a prerequisite chain-link, not a side quest** (though its unauthenticated guest webhook dies at cutover regardless).

*The graph is a reduced view; the work-item index Blocked-by column is authoritative (transitive edges such as WI-034←WI-003 are omitted for readability).*

---

## 5. Critical path to January 1

*Rewritten 2026-11-25 for the cutover decisions of that day; the dated plan is the calendar in the [WI-051 runbook](docs/migration/wi051-cutover-runbook.md).*

1. **Ledger chain (longest, least compressible):** WI-003 QuickBooks locks (through Sep 30 by Dec 4, through Nov 30 by Dec 16, December by Jan 13) → the three posting waves (Dec 9, Dec 18, Jan 14) → WI-035 tie-out and freeze (Jan 22) → WI-045 Disconnect (Jan 29). On 2026-11-25 no 2026 month was closed in QuickBooks and every 2026 document in ERPNext was a draft, so **the first QuickBooks lock (Dec 4) and Wave 1 (Dec 9) are the binding dates**: the Dec 11 hold-or-move check tests exactly them. Wave 1 also needs WI-068's 2026-window group-account lines at 0, the 2026 Record Matching queue cleared for Jan–Sep, a tested backup and a rehearsal on the refreshed local mirror.
2. **The go-live position is the posted ledger.** No opening entries (WI-032/WI-033 superseded). The company transacts live in ERPNext from Jan 1 on top of the ledger posted through Nov 30; December posts on Jan 14. Customer payments received Jan 4–14 for December invoices are recorded unapplied and reconciled after Wave 3.
3. **QuickBooks stays connected through the December close.** WI-001/WI-002 are done: QuickBooks is connected and its hourly CDC healthy (verified 2026-11-25). Users go reports-only on Jan 1 except Lisa and John; the full kill waits for the tie-out.
4. **Finance UAT chain:** TEST restored from a prod backup and isolated by Dec 2 → WI-022 finance UAT, Dec 7–18 (Lisa and James) → WI-051 go/no-go (Dec 22). WI-044's approver list (James approves, Nikolas backs up, Lisa prepares only) is confirmed on TEST. The kiosk (WI-021) is not on this chain: clock-in timing is decision 5, still open.
5. **Stripe chain:** WI-040 code is live and closes on the first live payout JE. Autopay enrollment opens only after the WI-035 freeze, and every wave is gated on 0 enrolled customers.
6. **Everything else** (tax automation if John's matrix misses Dec 15: go live with the three existing templates picked by hand; segment reporting; Plaid card feeds; surcharge; doc-AI) has a documented manual fallback or lives in Phase 2.
7. **Fallback (replaces the OD-5 "finishing sooner" note):** a miss at the Dec 11 check, or a no-go on Dec 22, moves go-live to **Feb 1**, cut at the Jan 31 close with the same steps a month later: December posts as Wave 3, January 2027 as a fourth wave, and the tie-out moves to 2027-01-31. The window for finishing sooner than Jan 1 has passed.

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
| [WI-019](work-items/WI-019-form-simplification.md) | Form simplification Property Setter fixtures — moved to Phase 2 on 2026-11-25 (not needed to go live) | 2 | FIXTURE | M | WI-018 |
| [WI-020](work-items/WI-020-sales-print-formats.md) | Print formats: Quotation / SO / SI (+ Letter Head) | 0 | FIXTURE | M | — |
| [WI-021](work-items/WI-021-time-kiosk-rollout.md) | Time Kiosk pilot → field rollout (Dec) | 1 | CONFIG | L | WI-011, WI-016 |
| [WI-022](work-items/WI-022-december-parallel-run-uat.md) | December **finance UAT** on TEST, Dec 7–18 (re-scoped 2026-11-25) | 1 | DATA | L | TEST restored from prod + isolated (Dec 2); Phase-0 config |
| [WI-023](work-items/WI-023-quotation-disposition.md) | Disposition of 638 draft QBO-Estimate Quotations — moved to Phase 2 on 2026-11-25 (it fed the canceled WI-028) | 2 | DATA | M | WI-007 |
| [WI-024](work-items/WI-024-opportunity-project-link-triage.md) | Opp↔Project canonical link + 196-orphan triage | 1 | DATA | M | — |
| [WI-025](work-items/WI-025-item-group-rollout.md) | Item Group rollout (583 items into taxonomy) — moved to Phase 2 on 2026-11-25 | 2 | DATA | M | — |
| [WI-026](work-items/WI-026-customer-group-backfill.md) | Customer Group backfill (1,146 ungrouped) — moved to Phase 2 on 2026-11-25 | 2 | DATA | M | — (OD-4 resolved: branch a) |
| [WI-027](work-items/WI-027-project-type-backfill.md) | project_type backfill (71 untyped + 2 'Group Projects') | 1 | DATA | S | WI-065 (OD-3 resolved: rename) |
| [WI-028](work-items/WI-028-draft-mirror-quarantine.md) | ~~Draft-mirror quarantine: bulk delete QBO drafts~~ **Canceled 2026-08-31** (OD-6 reversed: full history kept) | 1 | DATA | M | — |
| [WI-029](work-items/WI-029-coa-rebuild-execution.md) | ~~Execute CoA rebuild on prod~~ **Canceled 2026-08-31** (live chart kept) | 1 | DATA | L | — |
| [WI-030](work-items/WI-030-fy2027-naming-hygiene.md) | FY2027 + disable legacy FYs + naming-series reset (2027 series by Dec 18) | 1 | CONFIG | S | — (live chart) |
| [WI-031](work-items/WI-031-mode-of-payment-rationalization.md) | Mode of Payment rationalization + defaults (live chart) + AP run | 1 | CONFIG | M | WI-042 |
| [WI-032](work-items/WI-032-opening-trial-balance-je.md) | ~~Opening Trial Balance JE @2026-12-31~~ **Superseded 2026-11-25** (the posted ledger is the opening position) | 1 | DATA | M | — |
| [WI-033](work-items/WI-033-opening-ar-ap-invoices.md) | ~~Opening AR/AP as is_opening invoices~~ **Superseded 2026-11-25** (2026 invoices post as themselves) | 1 | DATA | L | — |
| [WI-034](work-items/WI-034-open-po-rekey.md) | Re-key only the QBO POs missing from ERPNext (SOs confirmed zero), by Jan 15 (re-scoped 2026-11-25) | 1 | DATA | S | WI-003 |
| [WI-035](work-items/WI-035-opening-reconciliation-signoff.md) | Tie-out: posted ledger at 2026-12-31 == QBO December close; freeze + sign-off, by Jan 22 (re-scoped 2026-11-25) | 1 | DATA | S | 2026 posting waves (Wave 3), WI-003, WI-034 |
| [WI-036](work-items/WI-036-sales-tax-templates.md) | Sales-tax templates rebuild (Utah-law branch b) | 1 | CONFIG | M | CPA written matrix = sign-off gate (manual tax at go-live if it misses Dec 15) |
| [WI-037](work-items/WI-037-tax-category-rule-automation.md) | Tax Category + Tax Rule automation (branch b) | 1 | CONFIG | M | WI-036; CPA written matrix = sign-off gate |
| [WI-038](work-items/WI-038-sales-tax-filing-procedure.md) | Utah sales-tax filing procedure (native reports) | 1 | CONFIG | S | WI-036/037 |
| [WI-039](work-items/WI-039-stripe-production-golive.md) | Stripe production go-live (keys, webhook, /pay, autopay gate) | 1 | CONFIG | M | WI-005; enrollment gated on WI-035 |
| [WI-040](work-items/WI-040-stripe-payout-ingestion.md) | Stripe payout ingestion → clearing-sweep JE + fee expense | 1 | APP_CODE | L | WI-005 |
| [WI-041](work-items/WI-041-stripe-refund-dispute-handling.md) | Stripe refund-reversal PE + dispute alerting | 1 | APP_CODE | M | WI-005/039 |
| [WI-042](work-items/WI-042-bank-masters.md) | Bank + Bank Account masters | 1 | CONFIG | S | account list from finance |
| [WI-043](work-items/WI-043-bank-reconciliation-runbook.md) | Bank rec runbook: statement import + payout matching | 1 | CONFIG | M | WI-042 (WI-040 for auto-match) |
| [WI-044](work-items/WI-044-pi-pe-workflow-activation.md) | Activate PI/PE approval workflows w/ real SoD | 1 | FIXTURE | M | WI-015, WI-011, approver list (James approves), Stripe-PE test; never active while a wave posts payments |
| [WI-045](work-items/WI-045-qbo-retirement-kill-checklist.md) | QBO retirement kill checklist (reports-only Jan 1; full kill Jan 29) | 1 | CONFIG | S | WI-035 |
| [WI-046](work-items/WI-046-qb-time-retirement.md) | QB Time retirement + guest-webhook removal | 1 | APP_CODE | M | WI-021 adoption gate (webhook dies at cutover regardless) |
| [WI-047](work-items/WI-047-payroll-summary-je.md) | Payroll summary-JE return (template + mapping) | 1 | CONFIG | S | WI-017 |
| [WI-048](work-items/WI-048-process-automation-flag-gonogo.md) | process_automation_enabled go/no-go | 1 | CONFIG | S | WI-009, WI-010, WI-011 |
| [WI-049](work-items/WI-049-month-end-close-adoption.md) | Month-End Close adoption (dry-run Dec; first close Feb) | 1 | CONFIG | S | WI-011, WI-035 |
| [WI-050](work-items/WI-050-integration-hazard-verification.md) | Integration-hazard verification before DATA windows | 1 | CONFIG | S | — (gates all bulk DATA) |
| [WI-051](work-items/WI-051-cutover-runbook.md) | Cutover runbook, go/no-go checklist, day-1 support (rewritten 2026-11-25) | 1 | DATA | M | WI-022, posting waves, WI-035 + gates |
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
| [WI-066](work-items/WI-066-po-creator-role-and-sod.md) | `PO Creator` role (16 holders → 5) + MR-requester ≠ PO-submitter gate; optional RFQ path documented | 0 | APP_CODE | M | WI-012, WI-013 |
| [WI-067](work-items/WI-067-qbo-mapper-data-fidelity.md) | QBO mapper data fidelity: qty fix + sales-tax import + sell-side reconciliation guard, then resync; unblocks pre-2026 Sales Invoice posting | 0 | DATA | M | v1.244.0 + v1.246.0 deployed |
| [WI-068](work-items/WI-068-group-account-remap.md) | Move 1,813 draft pre-2026 JE lines off group accounts (`- General` children + AR/AP merge); unblocks all pre-2026 GL posting | 0 | DATA | M | verified backup |
| [WI-069](work-items/WI-069-general-ledger-reclassification.md) | Reclassify `- General` balances into real children where the vendor determines it (61500, 60100 only) — **not** blocking | 1 | DATA | S | WI-068 + backlog submitted |
| [WI-070](work-items/WI-070-item-naming-hygiene.md) | Item naming hygiene — normalise 75 live Item Names, retire the 135-row `(deleted)` shadow master, fix the SOP Appendix D defects | 1 | DATA | M | WI-028 (one bucket), WI-050 |

Type distribution: 21 DATA · 27 CONFIG · 12 FIXTURE · 10 APP_CODE (5 of which are Phase 2) · 0 SERVER_SCRIPT — consistent with the brief's expectation that this project is mostly configuration and data, not code. (WI-061 is ON HOLD per OD-1 and excluded from scheduling.)

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

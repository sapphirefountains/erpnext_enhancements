# WI-051 — Cutover Runbook, Go/No-Go Checklist & Day-1 Support

**Phase:** 1  **Type:** DATA (terminal)  **Work item:** [`WI-051`](../../work-items/WI-051-cutover-runbook.md)
**Go-live:** **Fri 2027-01-01** (first working day Mon 2027-01-04). Hold-or-move check **Fri 2026-12-11**; fallback **Mon 2027-02-01**.
**Status:** rewritten 2026-11-25 for the cutover decisions Nikolas made that day. Nothing here runs before its gate is green.

This is the single ordered runbook for the QuickBooks Online (QBO) → ERPNext cutover: who does what, in what order, with a machine check on every line, and an abort path. Each work item keeps its own procedure and rollback; this runbook is the spine and the checklist.

Companion docs: [`backlog-gl-posting-runbook.md`](backlog-gl-posting-runbook.md) (the submit mechanics every wave reuses, §5 in particular), [`PLAN.md`](../../PLAN.md) §3–§6, [`wi068-group-account-remap-runbook.md`](wi068-group-account-remap-runbook.md), [`wi011-apply-runbook.md`](wi011-apply-runbook.md), [`wi007-o2c-chain.md`](wi007-o2c-chain.md).

---

## 0. What this runbook replaces, and the decisions behind it

The Aug 31 version walked a **clean-cut** path: delete the QBO draft mirror (S4, WI-028), rebuild the chart of accounts (S5, WI-029), then post an opening trial-balance JE and opening AR/AP invoices (S7–S8, WI-032/WI-033) in January. None of that applies any more. That version is in this file's history up to v1.843.1.

What now holds, oldest first:

| Date | Decision | Effect here |
|---|---|---|
| 2026-08-31 | [OD-6 reversed](../../decisions/OPEN-DECISIONS.md): carry QuickBooks' full history. Pre-2026 history posted to the GL the same day. | No draft delete. WI-028 **canceled**. |
| 2026-08-31 | Keep the live chart of accounts; no rebuild or renumber (descoped by Nikolas). | WI-029 **canceled**. Defaults (WI-031) target live accounts. |
| 2026-09-21 | Load the full accounting history, not balances only. | Every 2026 QBO transaction posts in ERPNext. |
| 2026-11-25 (1) | Keep Jan 1, with a hard check on Dec 11. Any miss moves go-live to Feb 1, cut at the Jan 31 close with the same steps a month later. | §4, §9. |
| 2026-11-25 (2) | Cancel the opening-balance entries. The posted history through 2026-12-31 **is** the opening position; WI-035 ties it to QBO's December close and books any residual as one JE that John approves. An opening JE on top would double-count. | WI-032 / WI-033 **superseded**. |
| 2026-11-25 (3) | Restore the TEST site from a fresh prod backup by Dec 2, for the finance UAT. | §2, WI-022. |
| 2026-11-25 (4) | Post 2026 in three waves as QBO months lock, not in one run after the December close. Cost accepted: a QBO edit to a month already posted becomes a manual cancel-and-amend in ERPNext. | §3. |
| 2026-11-25 (6) | James approves vendor bills and payments, Nikolas backs him up, Lisa prepares only. | WI-044, §5, §6. |
| 2026-11-25 (7) | If John's tax matrix misses Dec 15, go live with manual tax: the three existing templates, picked by hand. | §5. |

**Still open:**

- **Clock-in (decision 5).** Whether the Time Kiosk (WI-021), labor costing (WI-016) and the QuickBooks Time subscription move off the go-live path is not decided. None of them is a books gate. The QuickBooks Time **guest webhook still dies at the cutover release** regardless (WI-046, review correction C10).
- **2026 stock postings that QBO never saw.** ERPNext's 2026 GL already holds Purchase Receipt, Stock Entry and Stock Reconciliation postings with no QBO counterpart. WI-035 needs a call before Jan 22: book them in QBO's December close, reverse them in ERPNext, or accept them as a written variance. Owner: John, with Lisa.
- **2027 naming series** (WI-030): Lisa picks by Dec 18; must exist before the first 2027 document.

---

## Conventions

- **Every step = Owner + timestamp + verification.** No step is done until its machine check passes and the owner initials it.
- **Machine check** = an exact SQL statement, a settings value, a named report that must render, or an HTTP response code. Copy it at execution time; do not paraphrase.
- **Owners:** **James** Harris (CEO; Accounts Manager; approves bills and payments, chairs go/no-go). **Lisa** Symanski (Accounts User; preparer; checks each posting stage). **Nikolas** Bradshaw (implementation lead; runs the scripts; backs James up as approver). **John** Juntunen (external CPA; signs the tie-out). **Parker** (purchasing). **Meg** (Record Matching queue triage; Lisa decides).
- **`— SF`** on an account name is the ERPNext abbreviation for the single company, Sapphire Fountains.
- **"QBO-linked draft"** = a draft whose name appears as `erpnext_name` on a `QuickBooks Sync Mapping` row with `deleted=0`. Only these are posted in the waves (§3.0).

---

## Calendar

| Date | Step | Owner | Gate it feeds |
|---|---|---|---|
| by Mon Nov 30 | Decisions recorded; Task changes applied on PRJ-00739. | Nikolas | — |
| Wed Dec 2 | TEST site restored from a prod backup and **isolated** (§2). | Nikolas | Finance UAT |
| Fri Dec 4 | QBO closed and locked through **2026-09-30**, bank recs done (WI-003). WI-068 2026-window group-account lines = 0. Jan–Sep Record Matching queue cleared. | Lisa (John reviews); Nikolas; Meg | Wave 1 |
| Mon Dec 7 – Fri Dec 18 | Finance UAT on TEST (WI-022). | Lisa, James | Dec 22 |
| Tue Dec 8 – Wed Dec 9 | **Wave 1**: Jan–Sep 2026, plus pre-2026 leftovers (§3). | Nikolas; Lisa checks | Dec 11 |
| **Fri Dec 11** | **Hold-or-move check** (§4). | Nikolas, James | Jan 1 or Feb 1 |
| Wed Dec 16 | QBO closed and locked through **2026-11-30**. Oct–Nov queue cleared. | Lisa; Meg | Wave 2 |
| Thu Dec 17 – Fri Dec 18 | **Wave 2**: Oct–Nov 2026. | Nikolas; Lisa checks | Dec 22 |
| **Tue Dec 22** | **Go/no-go** (§5). | James chairs | Go-live |
| Mon Dec 28 – Thu Dec 31 | Cutover release and config replays (§6). | Nikolas | Go-live |
| **Fri Jan 1** | **Go-live** (§7). QBO users reports-only except Lisa and John; CDC sync stays on. | Nikolas | — |
| Mon Jan 4 – Thu Jan 14 | Receipts for December invoices recorded **unapplied** (§7.5). | Lisa | Tie-out |
| by Fri Jan 8 | Day-1 existence checks (§7.6). | Business owners | Two-week review |
| Wed Jan 13 | December closed and locked in QBO. | Lisa (John reviews) | Wave 3 |
| Wed Jan 13 – Thu Jan 14 | **Wave 3**: December 2026. | Nikolas; Lisa checks | Tie-out |
| Fri Jan 15 | WI-034 open POs re-keyed (only QBO POs missing from ERPNext). | Parker; Lisa | Tie-out |
| Fri Jan 15 – Fri Jan 22 | **Tie-out** (WI-035): posted ledger at 2026-12-31 = QBO's December close; then freeze 2026-12-31 (§8.2). | Lisa; John signs | Disconnect |
| **Fri Jan 29** | **Disconnect** (WI-045, §8.4). | Nikolas | — |

---

## 1. Safety and communications (before Wave 1)

| # | Step | Owner | Machine check / evidence |
|---|---|---|---|
| 1.1 | Announce the calendar to every QBO user and to finance staff: the three lock dates; **no edit to a locked month**; QBO goes reports-only on Jan 1 except Lisa and John; **nobody enters a 2027-dated transaction in QBO**, ever. Circulate the unapplied-receipt procedure (§7.5). | Nikolas | Email sent; acknowledgements recorded on TASK-2026-00944. |
| 1.2 | Put the Dec 11 check and the Dec 22 go/no-go on James's, John's, Lisa's and Nikolas's calendars. | Nikolas | Invites accepted. |
| 1.3 | **Backup capability** per [`backlog-gl-posting-runbook.md`](backlog-gl-posting-runbook.md) §1: encryption key escrowed off the VM, an off-box backup, test-restored. Then rehearse Wave 1 end to end on the refreshed local mirror and record its wall-clock and failure list. | Nikolas | Restored copy shows prod's draft counts; rehearsal failure list empty or every row explained on TASK-2026-01236. |
| 1.4 | **No-merge windows.** A merge to `main` FLUSHDBs both redis instances and restarts the bench, destroying queued jobs mid-run. No `main` deploy during a wave (Dec 8–9, Dec 17–18, Jan 13–14) or between the cutover release and go-live, except the planned release itself (§6). | Nikolas | Deploy log shows no `main` merge inside a window. |
| 1.5 | **WI-050**: `ERPNext Enhancements Settings.ai_write_gating_enabled = 1` (on in production since v1.525.0, patch `enable_ai_write_gate`, ADR 0016 §6; do not revert it). Confirm one AI write to a non-exempt doctype, for example a Note, yields an `AI Pending Action` instead of a mutation (Comment, ToDo, Sapphire Maintenance Template/Section, Serial No and Training Lesson are permanently exempt and would execute at once). For the bulk writes in Appendix A, Nikolas opens a time-boxed exemption window (**Exempt Until**) on the settings page rather than turning the gate off. | Nikolas | `tabSingles` `ai_write_gating_enabled=1`; one `AI Pending Action` row from the test write. |

---

## 2. TEST site restore and isolation (by Wed Dec 2)

TEST (`beta.erp.sapphirefountains.com`) is restored from a fresh prod backup for the finance UAT. A prod backup carries prod's live integration state: OAuth tokens, Stripe live keys, Plaid tokens, mail accounts and an enabled scheduler. **All of it must be off before anyone uses the site**, and before the scheduler gets a chance to run.

| # | Step | Owner | Machine check |
|---|---|---|---|
| 2.1 | Before the restore, set `maintenance_mode 1` and `pause_scheduler 1` in the TEST site's `site_config.json` (`bench --site <test-site> set-config ...`), and stop the TEST bench's background workers. The restore replaces the database, not the site config, so the first scheduler tick after it does nothing; with the workers stopped, nothing the restore enqueues can run either. | Nikolas | Both keys present in TEST's site config; no worker process running on the TEST bench. |
| 2.2 | Restore the prod backup to TEST. Do not migrate yet. | Nikolas | The restored site's draft counts match prod's at backup time. |
| 2.3 | **Scheduler off for good:** `bench --site <test-site> disable-scheduler`. | Nikolas | `SELECT value FROM tabSingles WHERE doctype='System Settings' AND field='enable_scheduler'` → `0`. |
| 2.4 | **Email off:** `set-config mute_emails 1`; turn off incoming and outgoing on every Email Account; delete unsent Email Queue rows. A TEST copy that pulls the shared mailboxes races prod for the same mail. | Nikolas | `mute_emails=1`; ``SELECT COUNT(*) FROM `tabEmail Account` WHERE enable_incoming=1 OR enable_outgoing=1`` = 0; ``SELECT COUNT(*) FROM `tabEmail Queue` WHERE status='Not Sent'`` = 0. |
| 2.5 | **QuickBooks off:** call `erpnext_enhancements.quickbooks_online.core.utils.clear_oauth_tokens(frappe.get_single("QuickBooks Online Settings"), "TEST copy of prod: not connected")`. It forgets the tokens locally and sets `sync_enabled=0`. **Never press Disconnect on TEST and never call `core.api.disconnect` there:** it revokes the grant at Intuit, and that grant is prod's. Any call TEST makes to Intuit with prod's tokens acts on prod's connection. | Nikolas | On TEST: `sync_enabled=0`, `realm_id` empty, `status='Not Connected'`. On prod: the next hourly CDC log row is `Completed`. |
| 2.6 | **Plaid off:** native `Plaid Settings.enabled=0` and `automatic_sync=0`; `Plaid Banking Settings.plaid_enabled=0`. | Nikolas | All three read 0 on TEST. |
| 2.7 | **Stripe off:** `Stripe Payments Settings.enabled=0`. The backup carries the Live keys, so a Sales Invoice submitted on TEST could charge a real card and a TEST payment link could take real money. If the UAT needs Stripe, enter Stripe **test-mode** keys and set `environment='Test'` first. | Nikolas | `enabled=0`, or `environment='Test'` with test keys. |
| 2.8 | **Every other outbound integration off**: Drive folder provisioning (`Project Folder Google Drive Settings.create_customer_folders` / `create_opportunity_folders`), Google Calendar sync, SMS. Anything that writes to an outside system stays off unless the UAT needs it and points it at a test target. | Nikolas | Each setting read and recorded on the WI-022 Task. |
| 2.9 | Only now: `bench --site <test-site> migrate` if TEST's code is ahead of the backup, start the workers, lift `maintenance_mode`. Keep `pause_scheduler`. Post the isolation record on TASK-2026-00915 (WI-022). | Nikolas | Migrate completes; comment posted; Lisa and James can sign in. |

---

## 3. Posting 2026 in three waves

Execution anchor: `TASK-2026-01236` ("Final Review Before Cutover"). WI-003 (QBO monthly close) is the gate for each wave.

| Wave | Posting dates | QBO locked by | Runs |
|---|---|---|---|
| **1** | 2026-01-01 – 2026-09-30, plus QBO-linked drafts dated before 2026 left over from the Aug 31 posting | Fri Dec 4 | Tue Dec 8 – Wed Dec 9 |
| **2** | 2026-10-01 – 2026-11-30 | Wed Dec 16 | Thu Dec 17 – Fri Dec 18 |
| **3** | 2026-12-01 – 2026-12-31 | Wed Jan 13 | Wed Jan 13 – Thu Jan 14 |

On 2026-11-25 every 2026 Sales Invoice, Payment Entry and Journal Entry was a draft (231, 185 and 2,957), and the pre-2026 leftovers were 126 Sales Invoices, 20 JEs, 2 Payment Entries and 10 Purchase Invoices. Re-measure at each gate.

### 3.0 Gate — before every wave

Replace `<start>` / `<end>` with the wave's dates (Wave 1's `<start>` is the earliest leftover's date). Every line must be green.

| # | Check | Owner | Machine check |
|---|---|---|---|
| G1 | QBO closed and locked through `<end>`, bank recs done for the wave's months. | Lisa; John reviews | Screenshot of the new QBO close date on TASK-2026-00896 (WI-003). |
| G2 | Record Matching queue clear for transactions dated in the wave. | Meg; Lisa decides | The QuickBooks Record Matching page shows 0 Pending Review, 0 QuickBooks changed and 0 Conflict rows dated `<start>`–`<end>`, or each held row is listed on TASK-2026-01236 with Lisa's reason. |
| G3 | Population counted and recorded (QBO-linked drafts only). | Nikolas | For each of `Sales Invoice`, `Purchase Invoice`, `Payment Entry`, `Journal Entry`: ``SELECT COUNT(*) FROM `tab<doctype>` d WHERE d.docstatus=0 AND d.posting_date BETWEEN '<start>' AND '<end>' AND EXISTS (SELECT 1 FROM `tabQuickBooks Sync Mapping` m WHERE m.erpnext_doctype='<doctype>' AND m.erpnext_name=d.name AND m.deleted=0)``. Counts written on TASK-2026-01236. |
| G4 | Drafts **not** linked to QBO in the wave's dates are listed, not posted. They were made natively in ERPNext; posting one that QBO also holds would count it twice. | Lisa | Same query with `NOT EXISTS`; each row decided by Lisa (post only if QBO has no matching transaction) and recorded. |
| G5 | WI-068: no draft JE line in the wave names a group account. | Nikolas | ``SELECT COUNT(*) FROM `tabJournal Entry Account` a JOIN `tabJournal Entry` j ON j.name=a.parent JOIN `tabAccount` acc ON acc.name=a.account WHERE j.docstatus=0 AND j.posting_date BETWEEN '<start>' AND '<end>' AND acc.is_group=1`` = 0. |
| G6 | **Autopay: zero customers enrolled.** Stripe is enabled and Live. `auto_charge_on_invoice_submit` skips only invoices older than `AUTO_CHARGE_MAX_AGE_DAYS` (365 days), and **every 2026 invoice is younger than that at its wave**, so submitting it would charge an enrolled customer's saved card. No Pending `Stripe Autopay Consent` is completed before §8.3. | Nikolas | `SELECT COUNT(*) FROM tabCustomer WHERE custom_stripe_autopay_enabled=1` = 0. |
| G7 | Reminders that would email customers about newly posted invoices are off. | Nikolas | `SELECT value FROM tabSingles WHERE doctype='ERPNext Enhancements Settings' AND field='dunning_enabled'` → `0` or no row; finance-doctype Notifications re-checked inert ([`backlog-gl-posting-runbook.md`](backlog-gl-posting-runbook.md) §3, "Also"). |
| G8 | Nothing is frozen in the wave's dates. | Nikolas | `SELECT accounts_frozen_till_date FROM tabCompany WHERE name='Sapphire Fountains'` is empty or before `<start>`. Fiscal Year 2026 enabled. |
| G9 | Named backup taken immediately before the first submit and copied off-box. | Nikolas | Filename on TASK-2026-01236. |
| G10 | Finance users told to pause entry for the run, and the no-merge window (§1.4) announced. | Nikolas | Message sent. |
| G11 | Wave 1 only: the §1.3 rehearsal passed. | Nikolas | Rehearsal record on TASK-2026-01236. |

### 3.1 Run order — every wave

The order and the submit loop are [`backlog-gl-posting-runbook.md`](backlog-gl-posting-runbook.md) §5, unchanged, restricted to the wave's dates and to QBO-linked drafts: an error-tolerant console loop that commits per document and records failures (not the Desk bulk action). **Stop after each stage. A non-empty failure list means a precondition was missed: do not continue.** Lisa checks each stage before the next starts.

| Stage | Action | Machine check |
|---|---|---|
| **S-A** | Submit the wave's **Sales Invoices** (and any Purchase Invoices from QBO bills, so payments can find them). | Submitted count = G3 count; failure list empty. |
| **S-B** | **Payment-only resync** while the wave's Payment Entries are still drafts: Preview Resync, Run Resync, then **Refresh drafts** on the Record Matching page for the rows it flags (since v1.800.0 a resync flags drafts that differ and never saves over them). | `tabPayment Entry Reference` rows exist for the wave's payments and match the cached QBO `LinkedTxn` allocations. After S-C only Payment Reconciliation can fix a reference (`allow_on_submit=0`). |
| **S-C** | Submit the wave's **Payment Entries**. | Submitted count = G3 count; the outstanding total of the wave's invoices drops by about the allocated amount. If it does not move, allocation did not land: stop. |
| **S-D** | Submit the wave's **Journal Entries**, after clearing store runs with the *Store Run Charge Matching* report as backlog runbook §5 S-D describes (lists stop changing, *Needs action* empty, its not-accounted-for figure at zero). | Submitted count = G3 count; failure list empty; the report still clean after the loop. |

### 3.2 After every wave

| # | Check | Owner | Machine check |
|---|---|---|---|
| A1 | Nothing QBO-linked is left in draft in the wave's dates, except the rows held at G2. | Nikolas | G3 query returns 0 per doctype, or exactly the held rows. |
| A2 | The GL balances for the wave's dates. | Nikolas | ``SELECT SUM(debit)-SUM(credit) FROM `tabGL Entry` WHERE is_cancelled=0 AND posting_date BETWEEN '<start>' AND '<end>'`` = 0. |
| A3 | Tied to QBO at `<end>` (Waves 1 and 2; Wave 3's tie is WI-035). | Lisa | `compare_account_balances(as_of_date='<end>')`: 0 accounts in mismatched / qb_only / erp_only, or each listed with a reason. The 2026 stock postings (§0, still open) are an expected residual: list them, do not fix them per wave. |
| A4 | Fresh backup taken after the run, copied off-box before the on-box sweep. | Nikolas | Filename on TASK-2026-01236. |
| A5 | Record of the wave on TASK-2026-01236: counts per stage, failures, held rows, backups. | Nikolas | Comment posted; finance users told they can resume. |

### 3.3 A QBO edit to a month already posted

The cost decision 4 accepted. QBO's lock should make it rare. When QBO changes a transaction whose ERPNext copy is submitted, the sync never touches the posted document: it flags the mapping `Pending Review` and the Record Matching page lists it under *QuickBooks changed*. Lisa then cancels and amends the ERPNext document to match QBO, chooses **Keep ERPNext** on the Record Matching page, and logs it on TASK-2026-01236. Before each later wave and before the tie-out, the *QuickBooks changed* list holds no posted record.

---

## 4. Hold-or-move check — Fri Dec 11

| Check | Owner | Evidence |
|---|---|---|
| QBO closed and locked through 2026-09-30, with bank recs. | Lisa | G1 screenshot. |
| Wave 1 posted and tied to QBO at 2026-09-30. | Nikolas, Lisa | §3.2 A1–A3 green. |
| Finance UAT running on TEST. | Lisa, James | WI-022 tracker shows the AP and AR scripts started. |

**All three green → hold Jan 1. Any miss → move to Feb 1** (§9.1). James and Nikolas record the call on TASK-2026-00944 the same day.

---

## 5. Go/no-go — Tue Dec 22 (James chairs)

| Check | Owner | Evidence |
|---|---|---|
| Wave 2 posted and tied to QBO at 2026-11-30. | Nikolas, Lisa | §3.2 green for Wave 2. |
| Finance UAT passed: ≥95% of scripts passed, zero open Sev-1. | Lisa | WI-022 tracker. |
| Approver list per decision 6: James approves bills and payments, Nikolas backs up, Lisa prepares only. The Accounts Manager list is confirmed with James so that no preparer can approve their own work. On TEST a bill and a payment were approved by someone other than their creator, and self-approval failed. | James, Nikolas | WI-044 Task comment. |
| 2027 naming series chosen (WI-030). | Lisa | Series recorded on the WI-030 Task. |
| Payroll Summary Journal Entry Template ready (WI-047). | Lisa | Template exists on TEST. |
| Month-End Close dry run done (WI-049). | Lisa | Dry-run record. |
| Sales tax: John's matrix landed, **or** go live with manual tax (decision 7). Not a no-go item. | John; Nikolas chases | Matrix, or a note that the three templates are picked by hand. |
| Explicit **GO**, signed by James, John and Nikolas. | James | Minutes archived on TASK-2026-00944. |

---

## 6. Cutover release and config replays (Dec 28–31)

One planned deploy inside the freeze window (§1.4), plus hand-applied config that does not travel with git.

| # | Step | Owner | Machine check |
|---|---|---|---|
| 6.1 | **WI-046** — the QuickBooks Time guest webhook dies at this release, whatever decision 5 settles. | Nikolas | `POST /api/method/erpnext_enhancements.quickbooks_time.api.qb_timesheet_webhook` returns 404/403 on prod; no `allow_guest=True` left in `erpnext_enhancements/quickbooks_time/`. |
| 6.2 | QuickBooks Time vendor subscription: follows decision 5 (open). Cancel it, or record a signed rule-4 exception with an end date. | Nikolas | Cancellation, or a signed exception with its date, on the WI-046 Task. |
| 6.3 | **WI-011** — the user / employee / role-profile matrix is live; Accounts Manager matches decision 6. | Nikolas | `SELECT COUNT(*) FROM tabUser WHERE enabled=1 AND user_type='System User' AND IFNULL(role_profile_name,'')=''` = 0 (or the documented direct-role users); the Accounts Manager holders match the list James confirmed (§5), plus documented service accounts. |
| 6.4 | **WI-018** — Finance Hub renders; `Workspace Manager` removed from the accountant. | Nikolas, Lisa | `"Workspace Manager" in frappe.get_roles(<accountant>)` is False; `tests/test_workspaces.py` passes. |
| 6.5 | **WI-047** — `Payroll Summary — <frequency>` Journal Entry Template on prod (blank amounts). | Lisa | ``SELECT COUNT(*) FROM `tabJournal Entry Template` WHERE name LIKE 'Payroll Summary%'`` = 1; ``SELECT COUNT(*) FROM `tabSalary Slip` `` = 0 (rule 3). |
| 6.6 | **WI-030** — FY 2027 exists and is enabled; the 2027 naming series are set before the first 2027 document. FY 2025 and 2026 stay enabled (Wave 3 posts into 2026). Disabling FY 2008–2024 waits until no draft dated in them is left. | Nikolas | ``SELECT disabled FROM `tabFiscal Year` WHERE name='2027'`` = 0; the new prefixes appear in the doctypes' naming-series options. |
| 6.7 | **WI-031** — every enabled Mode of Payment has a default account on the live chart. `Stripe` and `ACH` keep their names and accounts (Stripe settings reference them by name). | Nikolas | ``SELECT COUNT(*) FROM `tabMode of Payment` mp WHERE mp.enabled=1 AND NOT EXISTS (SELECT 1 FROM `tabMode of Payment Account` a WHERE a.parent=mp.name AND IFNULL(a.default_account,'')<>'')`` = 0. |
| 6.8 | **WI-016** Activity Cost rates: costing, not the books; timing follows decision 5. Not a go-live gate. | — | — |

**WI-044 approval workflows (target Mon Jan 4).** The fixture flip `is_active=1`, with the `payment_type='Pay'` condition on Payment Entry Approval so Stripe and other "Receive" entries bypass it. **Never activate while a wave's Payment Entry stage runs.** Wave 3 runs on Jan 13–14, after the Jan 4 target, so before activating either rehearse Wave 3's S-C loop on TEST with the workflows active, or activate on Jan 15 after Wave 3. Check: `SELECT name,is_active FROM tabWorkflow WHERE name IN ('Purchase Invoice Approval','Payment Entry Approval')` → both 1; no transition of either with `allow_self_approval=1`; a Stripe test payment still yields a submitted Payment Entry.

---

## 7. Go-live — Fri Jan 1 (first working day Mon Jan 4)

| # | Step | Owner | Machine check / evidence |
|---|---|---|---|
| 7.1 | ERPNext is the book of record for every transaction dated 2027-01-01 or later. | — | — |
| 7.2 | Set every QBO user to **reports-only**, **except Lisa and John**, who finish the December close. | Nikolas (Intuit admin) | Intuit user list screenshot on TASK-2026-00938 (WI-045); a spot-check user cannot create or edit. |
| 7.3 | **QBO CDC sync stays on** through the December close: `sync_enabled=1`, hourly CDC. Lisa and John's December entries must reach ERPNext as drafts for Wave 3. Nothing is switched off until §8.4. | Nikolas | Hourly `tabQuickBooks Sync Log` rows `Completed`. |
| 7.4 | **No QBO-imported document dated 2027.** Anything dated 2027 belongs in ERPNext only, so a QBO import dated 2027 duplicates an ERPNext entry. Delete the ERPNext draft (never submit it) and have Lisa void it in QBO, or the next CDC run brings it back. Check daily in January. | Lisa, Nikolas | For each of `Sales Invoice`, `Purchase Invoice`, `Payment Entry`, `Journal Entry`: ``SELECT d.name FROM `tab<doctype>` d JOIN `tabQuickBooks Sync Mapping` m ON m.erpnext_doctype='<doctype>' AND m.erpnext_name=d.name AND m.deleted=0 WHERE d.posting_date>='2027-01-01'`` → 0 rows. |
| 7.5 | **Unapplied receipts, Jan 4–14** (below). | Lisa | Below. |
| 7.6 | Day-1 existence checks (below). | Business owners | Below. |
| 7.7 | Week-1 support starts (§7.7 table). | Nikolas | Below. |

### 7.5 Customer payments received Jan 4–14 for December invoices

December invoices stay drafts in ERPNext until Wave 3 posts them on Jan 13–14, and a Payment Entry cannot reference a draft. So:

1. A payment received Jan 4–14 against a **December** invoice (or against any 2026 invoice held back from a wave) is recorded as an **unapplied Payment Entry**: party set, no invoice reference, posting date = the day it was received.
2. Payments against invoices already posted in Waves 1–2 reference them as normal.
3. After Wave 3, Lisa clears the unapplied entries against the December invoices with native **Payment Reconciliation**.
4. Stripe autopay enrollment stays closed until the freeze (§8.3).

Checks:
- Interim, expected: ``SELECT COUNT(*) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Receive' AND posting_date>='2027-01-01' AND unallocated_amount>0`` may be > 0.
- After Wave 3, before the tie-out signs: ``SELECT COUNT(*) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Receive' AND posting_date BETWEEN '2027-01-01' AND '2027-01-14' AND unallocated_amount>0`` = 0, or each remaining row is a genuine prepayment, listed on TASK-2026-00928 (WI-035).

### 7.6 Day-1 existence checks (by Fri Jan 8)

Each business owner creates the first real document of their type on prod:
```sql
SELECT COUNT(*) FROM `tabQuotation`      WHERE docstatus=1 AND transaction_date >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabSales Order`    WHERE docstatus=1 AND transaction_date >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabSales Invoice`  WHERE docstatus=1 AND posting_date     >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabPurchase Order` WHERE docstatus=1 AND transaction_date >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabPayment Entry`  WHERE docstatus=1 AND posting_date     >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabJob Interval`   WHERE start_time                        >= '2027-01-01';  -- ≥ 1, only if decision 5 puts the kiosk live on Jan 1
```

### 7.7 Week-1 support

| # | Step | Owner | Evidence |
|---|---|---|---|
| 7.7.1 | Named floor-walker rota for week 1 (per value stream). | Nikolas | Rota published. |
| 7.7.2 | "Cutover Issues" triage list (native ToDo/Issue), one row per reported problem. | Nikolas | Each item has an owner and a status. |
| 7.7.3 | Daily 15-minute triage standup for two weeks. | Nikolas | Notes archived. |
| 7.7.4 | Escalation path to Nikolas documented and circulated. | Nikolas | Path published. |
| 7.7.5 | Two-week post-cutover review; open issues below the agreed threshold. | James, Nikolas | Minutes; open-issue count under threshold. |

---

## 8. Wave 3, tie-out, freeze and disconnect

### 8.1 Wave 3 — Wed Jan 13 – Thu Jan 14

QBO's December close locked by Lisa on Jan 13 (John reviews), then Wave 3 exactly as §3, **including every gate line** (G6 autopay = 0 again). WI-034's open-PO list comes from the same close.

### 8.2 Tie-out — WI-035, Fri Jan 15 – Fri Jan 22

The posted ledger at 2026-12-31 equals QBO's December close. Run every comparison while QBO is still connected.

| # | Check | Owner | Machine check |
|---|---|---|---|
| T1 | Per-account comparison. | Lisa | `compare_account_balances(as_of_date='2026-12-31')`: 0 accounts in mismatched / qb_only / erp_only, or each residual has a written accepted-variance note. |
| T2 | Trial Balance, AR aging, AP aging and every bank account at 2026-12-31 match QBO's December close package. | Lisa | Native Trial Balance, Accounts Receivable, Accounts Payable and General Ledger reports against the QBO reports, each pair archived. |
| T3 | The 2026 stock postings QBO never saw are settled per the decision in §0. | John, Lisa | Decision recorded; the residual follows it. |
| T4 | Any remaining residual is booked as **one** Journal Entry dated 2026-12-31 that John approves (decision 2). | Lisa; John approves | At most one such JE, with John's approval on its timeline. |
| T5 | No balance left on the Temporary account. | Nikolas | ``SELECT SUM(debit)-SUM(credit) FROM `tabGL Entry` WHERE is_cancelled=0 AND account IN (SELECT name FROM tabAccount WHERE account_type='Temporary')`` → 0, or NULL when no such account has entries. |
| T6 | Unapplied January receipts reconciled (§7.5). | Lisa | §7.5 "after Wave 3" check. |
| T7 | No posted record on the Record Matching page's *QuickBooks changed* list (§3.3). | Meg; Lisa | Page shows none. |
| T8 | **Freeze 2026**: `Company.accounts_frozen_till_date = '2026-12-31'`, `role_allowed_for_frozen_entries = 'Accounts Manager'`. | Nikolas | `SELECT accounts_frozen_till_date, role_allowed_for_frozen_entries FROM tabCompany WHERE name='Sapphire Fountains'` → `2026-12-31`, `Accounts Manager`. |
| T9 | John's written sign-off archived. | John | Sign-off on TASK-2026-00928. |

### 8.3 After the freeze

Stripe autopay enrollment may open (WI-039); Pending consents can be completed from here on.

### 8.4 Disconnect — WI-045, Fri Jan 29

In this order, after T9:

| # | Step | Owner | Machine check |
|---|---|---|---|
| D1 | One final CDC run lands clean, and no new 2026 draft arrived after the freeze. | Nikolas | Latest `tabQuickBooks Sync Log` row `Completed` with `failed_count=0`; no QBO-linked draft dated 2026. |
| D2 | `QuickBooks Online Settings.sync_enabled = 0`. | Nikolas | `sync_enabled=0`. |
| D3 | Delete the Intuit webhook subscription for `erp.sapphirefountains.com` in the Intuit developer portal (webhooks bypass `sync_enabled`). | Nikolas (Intuit admin) | Portal shows no active subscription. |
| D4 | Every QBO user, Lisa and John included, set to read-only. | Nikolas (Intuit admin) | Intuit user list. |
| D5 | `erpnext_enhancements.quickbooks_online.core.api.disconnect`: revokes at Intuit, clears tokens and `realm_id`, sets `status='Not Connected'`. | Nikolas | `tabSingles`: `realm_id` empty, `sync_enabled=0`, `status='Not Connected'`. |
| D6 | Neutralize residual Failed logs (WI-001 step 2); confirm `Accounting Intake Settings.qbo_writeback_enabled=0`. | Nikolas | ``SELECT COUNT(*) FROM `tabQuickBooks Sync Log` WHERE creation > '<D5 timestamp>'`` = 0 after 7 days; `QuickBooks Sync Mapping` / `Sync Log` / `Raw Payload` counts unchanged. The 3 hourly QBO hooks stay in `hooks.py`, inert, until WI-052. |

---

## 9. Abort criteria, path and fallback

There is no destructive delete in this plan any more, so no single point of no return. What is hard to undo is a **submitted wave**: cancellation is not rollback on this site (immutable ledger is off, so a cancel inserts reversing rows, and `Amend Counter` renames retried documents away from their sync mappings). And prod is live while the waves run, so a site restore also discards everyone else's work since the backup. Hence the off-hours runs and the pause on finance entry (G10).

| Trigger | Action |
|---|---|
| A wave's gate (§3.0) is not green. | Do not run the wave. Fix and re-gate. If Wave 1 cannot run by Dec 9, the Dec 11 check fails. |
| A wave fails mid-run (counts off, failure list non-empty). | Stop. If nothing else has written to prod since G9, restore the G9 backup. Otherwise fix per document (cancel and amend), never with a site restore that discards other users' work. |
| **Dec 11 check fails.** | Move to Feb 1 (§9.1). |
| **No-go on Dec 22.** | Stay on QBO for January; Feb 1 (§9.1). |
| Tie-out cannot reconcile by Jan 22. | Hold the freeze and the Disconnect. Correct by cancel-and-amend or the one residual JE John approves; a QBO-side error loops back to a WI-003 re-close. Go-live is not reversed: ERPNext has been the book of record since Jan 1. |
| Disconnect needs undoing. | Re-grant OAuth (WI-002) and recreate the Intuit webhook subscription. Reversible until WI-052 removes the code. |

### 9.1 The Feb 1 fallback

The same steps, one month later, cut at QBO's January 31 close: QBO stays the book of record for January; December posts as Wave 3 once it locks; **January 2027 posts as a fourth wave** after the Jan 31 close; go/no-go in late January; go-live Mon Feb 1; the tie-out compares the posted ledger at 2027-01-31 with QBO; the freeze date becomes 2027-01-31; the Disconnect moves to late February. The exact dates are set and recorded at the Dec 11 check (or at the Dec 22 no-go). Jan 1 is preferred because it keeps all of 2026 in one system for taxes and 1099s; under Feb 1, January 2027 still ends up in ERPNext through the fourth wave.

---

## Appendix A — Bulk-operation hygiene (for anyone running cutover data scripts)

The advisory hazard gate is **WI-050**: set `ERPNext Enhancements Settings.ai_write_gating_enabled=1` for the window (AI writes then create an `AI Pending Action` for confirmation instead of mutating directly), and confirm this bulk-write rule is in **every** workstream's DATA runbook.

**Correction (verified 2026-08-31 against `main`):** the wildcard `'*'` `after_save` → `global_triton_sync` hook that PLAN.md, WI-050, WI-023/028/033 and this item's older scope text all warn about is **retired (v1.341.1)** — and the tombstone at `erpnext_enhancements/utils/triton_sync.py` records that it was **inert from the day it was written** (Frappe dispatches no server-side `after_save` document event). So the "one queued Triton POST per ORM save" storm is a non-issue on the current codebase. Triton keeps its index fresh via its own sync engine. **Do not** re-introduce a global save hook without a real design (gateway secret header, per-doc→user mapping, timeout, `enqueue_after_commit=True`).

The two named per-doctype hazards **are still live** and must be respected during bulk touches:

| Hazard | Location | Rule during bulk runs |
|---|---|---|
| Customer Drive-folder provisioning | `Customer.after_insert → google_drive.drive_utils.enqueue_customer_folder` (hooks.py:701), gated by `Project Folder Google Drive Settings.create_customer_folders` | Keep **`create_customer_folders=0`** while inserting Customers (e.g. parties created during Record Matching queue triage). Same for `create_opportunity_folders` + `Opportunity.after_insert` (hooks.py:580). |
| Closed-Won prompt | `Opportunity.on_update → crm_enhancements.project_prompt.prompt_create_project_on_won` (hooks.py:567) | Avoid status-touching bulk `doc.save()` on Opportunity; use `frappe.db.set_value`/SQL. |
| Supplier Drive-folder provisioning | `Supplier.after_insert → accounting_intake.filing.enqueue_supplier_folder` (hooks.py:663) | Same rule as Customer — the enqueue is per inserted row. Bulk-create Suppliers (e.g. AP party creation during triage) with `frappe.db`-level inserts, or off-hours in committed batches. *Missing from the original WI-050 hazard list; added by the 2026-09-01 inventory.* |
| Other per-row `after_insert` hooks | `Project` (process-step seeding + announce, hooks.py:369–371), `Employee` (training assignment, hooks.py:616), `File` (Drive upload half of attachment sync, hooks.py:586 — bails out cheaply unless the file is attached to a Drive-linked Project/Customer/Opportunity), `Communication`, `Activity Log` | Not cutover-DATA doctypes; listed so a bulk import of any of them is planned under the same rule. Complete inventory verified 2026-09-01 — the `Chat Message` hook it also found went with the chat module in v1.426.0 (ADR 0011). |

General rules:
- Prefer `frappe.db.set_value` / direct SQL to **bypass `doc_events`** wherever the logic permits. Where an ORM `save()`/`submit()` is genuinely required (submitting each wave's documents, §3.1), run **off-hours**, in **committed batches**, and monitor the default RQ queue.
- **Deploy hazard:** a merge to `main` FLUSHDBs both redis instances and restarts the bench, silently killing every queued job. Any cutover script that enqueues work must be **re-drivable** after a deploy — never assume an enqueued job survived. Do not deploy mid-DATA-window (§1.4).

### WI-050 verification record — 2026-09-01

Run against `main` v1.359.4 and prod, as the advisory gate before any bulk DATA window. This appendix is the canonical statement of the bulk-write rule; the backlog GL-posting runbook and the WI-068 runbook point here.

| Check | Result |
|---|---|
| Wildcard `'*'` doc_events in `hooks.py` | **None.** `global_triton_sync` retired v1.341.1 (tombstone in `utils/triton_sync.py`); WI-050's optional flag-guard branch is moot. |
| Per-doctype insert/update hazards | Inventory in the table above; Customer (701), Opportunity (580 / 567) and Supplier (663) confirmed live in `hooks.py`. |
| Accounting Intake posting posture | Safe: every handler in `accounting_intake/actions/base.py` creates a **docstatus-0 draft** and never submits. No change needed. |
| `ERPNext Enhancements Settings.ai_write_gating_enabled` | **1 on prod since v1.525.0**, with permanent exemptions and time-boxed windows for bulk jobs (ADR 0016 §6). The paragraph below predates that: The gate (`assistant_tools/_gate.py`) wraps FAC's `BaseTool._safe_execute` — the MCP tools themselves (`create/update/delete/submit_document`, `run_python_code`, `run_workflow`, dashboards, this app's write tools). With it on, *every* AI-initiated write, including routine PRJ-00739 task bookkeeping, records an `AI Pending Action` and waits for desk confirmation. Flip it at **§1.5**, the start of W1, not months early; one Single field, reverts the same way. |
| Acceptance "a test AI write creates an `AI Pending Action`" | Deferred to the §1.5 flip — a live write test before then would itself be an unconfirmed AI write on prod. |

---

## Acceptance criteria (this runbook)

- [x] Runbook exists, versioned in `docs/`, with every checklist line mapped to a machine check (SQL / report / settings value) and an owner, and the 2026-11-25 sequence encoded as gated steps: three waves (§3), hold-or-move check (§4), go/no-go (§5), go-live (§7), Wave 3, tie-out, freeze and disconnect (§8).
- [ ] Hold-or-move call recorded on Dec 11 (§4) — *at execution*.
- [ ] Go/no-go minutes recorded with an explicit **GO** and sign-offs (§5) — *at execution*.
- [ ] Day-1 existence checks all ≥ 1 by Jan 8 (§7.6) — *at execution*.
- [ ] Unapplied January receipts reconciled against the December invoices after Wave 3 (§7.5) — *at execution*.
- [ ] Two-week review held; open issues below threshold (§7.7.5) — *at execution*.

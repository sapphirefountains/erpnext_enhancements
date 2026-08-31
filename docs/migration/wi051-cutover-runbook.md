# WI-051 — Cutover Runbook, Go/No-Go Checklist & Day-1 Support

**Phase:** 1  **Type:** DATA (terminal)  **Work item:** [`WI-051`](../../work-items/WI-051-cutover-runbook.md)
**Cutover date:** **2027-01-01 — fixed (OD-5).** Scope flexes; the date does not.
**Status:** DRAFT authored 2026-08-31. Execution spans the December-2026 freeze window and the January-2027 opening-balance tail. Nothing here is executed until the go/no-go GO (§5B).

> **⚠ Amended 2026-08-31 by the [OD-6 reversal](../../decisions/OPEN-DECISIONS.md) — carry full history.** The historical GL is now posted in a **separate event before cutover** ([`backlog-gl-posting-runbook.md`](backlog-gl-posting-runbook.md)). Under that decision, this runbook's **S4 (draft-mirror delete) is REMOVED** and **S7–S8 (opening-balance JEs) are SUBSUMED** by the already-posted history. Still live at cutover: S1–S3, S5–S6 (config), **S9 tie-out** (now "posted-history balances == QBO close"), S10 disconnect, plus the day-1 checks, week-1 support, abort path, and appendix. The clean-cut sequence below is retained for reference and as the abort branch (if the business ever reverts to clean-cut).

This is the single ordered runbook for the QuickBooks Online → ERPNext cutover: who does what, in what order, with a machine check on every line, and a documented abort path. It integrates the cutover-touching work items; each item keeps its own detailed procedure and rollback — this runbook is the **spine and the checklist**, not a replacement for them.

Companion docs: [`PLAN.md`](../../PLAN.md) §3–§5 (phasing + binding ordering), [`phase0-execution-plan.md`](phase0-execution-plan.md) (the Phase-0 feeder schedule), [`wi011-apply-runbook.md`](wi011-apply-runbook.md) (role matrix already applied), [`wi068-group-account-remap-runbook.md`](wi068-group-account-remap-runbook.md) (pre-2026 GL remap), [`wi007-o2c-chain.md`](wi007-o2c-chain.md) (per-stream O2C SOPs that attach here).

---

## Conventions

- **Every step = Owner + timestamp + verification.** No step is "done" until its machine check passes and the owner initials the timestamp.
- **Machine check** = an exact SQL statement, a settings field = value assertion, a named native report that must render, or an HTTP response code. Copied verbatim from each work item's acceptance criteria — do not paraphrase at execution time.
- **Owners** (from [`wi011-apply-runbook.md`](wi011-apply-runbook.md)): **CEO** = James Harris (Accounts Manager / payment approver); **Accounts User** = Lisa Symanski (preparer); **Impl. lead** = Nikolas Bradshaw (sys-admin, runs the scripts); **CPA** = John Juntunen (external, sign-off); plus workstream owners **Sync** (QBO), **Sales**, **HR/Payroll**, **Controller**.
- **`— SF`** suffix on account names is the ERPNext company abbreviation for *Sapphire Fountains* (single company; no JDH per OD-1).

---

## 0. The two-window shape of this cutover (read first)

The plan's "one freeze window" is idealised. In reality the sequence splits across **two** windows because the December books do not close until ~Jan 10–15:

| Window | What runs | Ledger state |
|---|---|---|
| **W1 — December freeze week** (last week of Dec 2026) | Destructive rebuild: final QBO sync → partial kill → draft-mirror delete → CoA rebuild → FY/naming/modes-of-payment. Steps **S1–S6**. | ERPNext is not yet book of record; the drafts deleted are QBO *mirrors*, not live company transactions. Safe to rebuild. |
| **Jan 1 — go-live** | Company transacts live in ERPNext on the **empty rebuilt chart**. Interim-AR procedure (§C12) covers collections. | Empty ledger; opening balances not yet posted. |
| **W2 — opening-balance tail** (~Jan 10–15, after the Dec close lands) | Opening TB JE → opening AR/AP → open-PO re-key → tie-out + sign-off → full QBO Disconnect. Steps **S7–S10**. | Opening position posted **backdated to 2026-12-31**; period frozen; QBO goes read-only then disconnected. |

**QBO OAuth tokens stay ALIVE from S2 through S9** — the opening tools (`sync_opening_balances`, `compare_account_balances`) call the live QBO API. Only S10 revokes them. This is why the *webhook subscription* is deleted early (S2) but the *Disconnect* is last (S10).

---

## Precondition decisions — settle BEFORE the W1 freeze window

These are not agent-decidable; each must be closed and recorded before S1. Several are tracked on PRJ-00739.

1. **Pre-2026 historical posting — post or discard?** (owner: CEO + CPA; ties to `TASK-2026-01236` "Final Review Before Cutover", and WI-067/WI-068/WI-069/WI-053.) The baseline in this runbook is: the QBO draft mirror is **deleted** (S4/WI-028) and the opening position is seeded by opening balances (S7–S8); optional 24-month **summary** trend JEs may be imported later (WI-053, out of scope here). **If** the decision is instead to post pre-2026 detail, then WI-067 (SI resync) + WI-068 (2026-window group-account remap) + WI-069 (reclass) must be **completed and submitted before S4**, and S4's delete population must exclude anything intended to be retained. *Do not enter the freeze window with this open.*
2. **WI-023 quotation keep-list** (owner: Sales) — the one shared list of live quotes to carry forward; everything not on it is the S4 delete population. See §S4.
3. **New naming-series prefixes** (owner: Controller + finance) — opening series + year-scoped 2027 live series (WI-030). Recorded in this runbook at execution.
4. **Mode-of-Payment keep-list** (owner: Controller) — proposed keep: Cash, Check, ACH, Wire Transfer, Credit Card, Stripe; plus the ACH dual-use resolution (WI-031).
5. **Go/No-Go meeting** scheduled ~Dec 22 2026 with CEO + accountant + impl. lead (§5B).

Non-blocking-but-parallel: CPA Utah tax matrix (WI-036/037 — templates work manually day 1), bank account list from finance (WI-042 — bank rec is post-cutover), burdened labor rates (WI-016 — costing, not book-of-record).

---

## 1. Freeze window & communications

| # | Step | Owner | Machine check / evidence |
|---|---|---|---|
| 1.1 | Announce the freeze window + read-only date to all QBO users; circulate the interim-AR procedure (§C12). | Impl. lead | Calendar invite + email sent; acknowledgements recorded. |
| 1.2 | Confirm the go/no-go GO is recorded (§5B) before any destructive step. | CEO | §5B minutes archived with explicit GO. |
| 1.3 | Take a **verified, download-tested** full Frappe Cloud backup immediately before S1; note the backup id. | Impl. lead | Backup file downloaded and its restore verified on a scratch site (this is the S4/S5 rollback path — see §7). |
| 1.4 | Freeze unrelated merges to `main` for the window. **A merge to `main` FLUSHDBs both redis instances and restarts the bench**, destroying queued jobs mid-run. | Impl. lead | No `main` deploys between S1 and S6 except the planned cutover release (§3). |

---

## 2. Final QBO actions & QBO → reports-only (rule 4) — WI-045 Stage A

QBO ends the day **read-only archive**; its OAuth tokens stay alive for the opening tools.

| # | Step | Owner | Machine check |
|---|---|---|---|
| 2.1 | Run one **final** manual Entity Sync / CDC; confirm it lands clean. | Sync | Latest `tabQuickBooks Sync Log` row `status='Completed'` with **`failed_count=0`**. |
| 2.2 | Set `QuickBooks Online Settings.sync_enabled = 0`. | Sync | `sync_enabled=0`. |
| 2.3 | **DELETE the Intuit webhook subscription** in the Intuit developer portal for `erp.sapphirefountains.com`. *(Inbound webhooks bypass `sync_enabled` — this is the real inbound kill.)* | Org/Intuit admin | Intuit portal shows **no active webhook subscription**; `realm_id` / tokens **still populated** (S7 needs them). |
| 2.4 | Set all QBO users to reports-only / read-only access. | Org/Intuit admin | Spot-check: a QBO user cannot create/edit; can still view reports. |

> **Why S2.3 before S4:** `webhooks.handle_webhook` never checks `sync_enabled`. Leaving the subscription live during the S4/S5 data windows would let an inbound webhook re-import the very drafts S4 deletes (hazard H5).

---

## 3. Prod deploy of the cutover release

One planned deploy of the cutover release, timed inside the freeze window (respecting §1.4).

| # | Step | Owner | Machine check |
|---|---|---|---|
| 3.1 | **WI-044** — activate the two finance approval workflows (fixture flip `is_active=1`) with the `payment_type='Pay'` condition on Payment Entry Approval so Stripe/system "Receive" PEs bypass. Deploy **after** the S8 bulk open-item submissions (see ordering note). | Impl. lead | `SELECT name,is_active FROM tabWorkflow WHERE name IN ('Purchase Invoice Approval','Payment Entry Approval')` → both `1`; zero `tabWorkflow Transition` rows with `allow_self_approval=1` for these two; a Stripe test payment still yields a `docstatus=1` Payment Entry untouched. |
| 3.2 | **WI-046** — the QuickBooks Time guest webhook dies at this deploy **regardless of kiosk adoption** (live security gap, rule 4). | Impl. lead | `POST /api/method/erpnext_enhancements.quickbooks_time.api.qb_timesheet_webhook` returns **404/403** on prod; CI/grep shows **no `allow_guest=True`** remaining in `erpnext_enhancements/quickbooks_time/`. |
| 3.3 | QB Time **vendor subscription** cancellation (gated on WI-021 kiosk adoption) — or record a brief-owner-signed rule-4 exception with a February deadline (the endpoint is already dead from 3.2). | HR/Payroll | Cancellation confirmed, or signed exception with date recorded in this runbook. |

> **WI-044 timing (hazard):** an active workflow forces state transitions on every new PI/PE. Prod holds **1,405 draft Payment Entries + 1 draft Purchase Invoice** — their disposition (part of S4) must complete before 3.1, or each touched draft enters the workflow state machine mid-bulk-run. If the `payment_type='Pay'` condition proves insufficient on TEST for the Stripe receive-PE path, escalate WI-044 to APP_CODE (handle workflow state inside `reconcile._create_payment_entry`) — do **not** activate blind.

---

## 4. Prod CONFIG replays (not fixtures — hand-applied at cutover)

Config that does not travel via git and must be re-created by hand on prod. *(The WI-013 PO Authorization threshold is **not** here — it deploys via its fixture/seed patch, review correction C7.)*

| # | Step | Owner | Machine check |
|---|---|---|---|
| 4.1 | **WI-011** — verify the user↔employee↔role_profile matrix is live (already applied per [`wi011-apply-runbook.md`](wi011-apply-runbook.md); re-verify at cutover). | Impl. lead | `SELECT COUNT(*) FROM tabUser WHERE enabled=1 AND user_type='System User' AND IFNULL(role_profile_name,'')=''` = 0 (or the 4 direct-role-managed users documented); ≥1 `Accounts Manager` who is **not** the daily preparer. |
| 4.2 | **WI-018** — the curated Finance Hub renders and **`Workspace Manager` is removed from the accountant** (until removed, every curation lever is inert). | Accountant + Impl. lead | `"Workspace Manager" in frappe.get_roles(<accountant>)` is **False**; Finance Hub renders for an `Accounts User`-only user; `tests/test_workspaces.py` passes. |
| 4.3 | **WI-016** — enter per-employee **Activity Cost** rates on prod (confidential; not fixtured). Restrict read/write to Accounts Manager + HR first. | Accounts Manager + HR | `SELECT COUNT(*) FROM \`tabActivity Cost\`` ≥ active field-employee count; a non-Accounts user gets `PermissionError` reading Activity Cost; a submitted kiosk Timesheet shows `costing_amount>0`. |
| 4.4 | **WI-047** — re-create the `Payroll Summary — <frequency>` Journal Entry Template on prod (blank amounts; native `Journal Entry Template`). | Controller | `SELECT COUNT(*) FROM \`tabJournal Entry Template\` WHERE name LIKE 'Payroll Summary%'` = 1; `SELECT COUNT(*) FROM \`tabSalary Slip\`` = 0 (permanent guard, rule 3). |

---

## 5. Go/No-Go checklist

### 5A. BINDING cutover-window sequence — strictly sequential, each step GATES the next

Encoded verbatim from [`PLAN.md`](../../PLAN.md) §3 (review correction C5). **Do not start a step until the prior step's machine check is green.**

#### W1 — December freeze week (destructive rebuild)

**S1. Final QBO sync.** — Owner: Sync. → §2.1 check (`failed_count=0`). *Gates S2.*

**S2. Partial kill.** `sync_enabled=0` + Intuit webhook subscription DELETED; tokens ALIVE. — Owner: Sync / Intuit admin. → §2.2–2.3 checks. *Gates S3.*

**S3. Backup + pre-delete export.** Verified full backup downloaded (§1.3) + pre-delete CSV export of the delete population. — Owner: Impl. lead. → backup restore-tested; CSVs exist. *Gates S4. This is the last fully-reversible point (see §7).*

**S4. WI-028 draft-mirror delete.** Batched `frappe.delete_doc(..., ignore_permissions=True)`, commit every 100. Set `deleted=1` on the `QuickBooks Sync Mapping` rows whose `erpnext_name` was removed. — Owner: Impl. lead.
- `SELECT COUNT(*) FROM \`tabJournal Entry\` WHERE docstatus=0` → **0** (was 12,341)
- `SELECT COUNT(*) FROM \`tabSales Invoice\` WHERE docstatus=0` → **0** (was 1,563)
- `SELECT COUNT(*) FROM \`tabPayment Entry\` WHERE docstatus=0` → **0** (was 1,405)
- `SELECT COUNT(*) FROM tabQuotation WHERE docstatus=0` → **0** (delete population = exactly the WI-023 marked-historical drafts; the submitted keep-list stays intact)
- `SELECT COUNT(*) FROM \`tabGL Entry\`` **unchanged (= 4)**; `tabQuickBooks Raw Payload` unchanged (the archive); archival CSVs exist.
- *Precondition:* WI-023 keep-list published; S2 complete. *Gates S5.*

**S5. WI-029 CoA rebuild.** Delete 359 QBO accounts (tree-safe, leaves first; or native rename/merge per `coa_mapping.csv`), import `chart_of_accounts.csv` via the native CoA Importer, re-point Company defaults + `Stripe Payments Settings.deposit_account` + `QuickBooks Sync Mapping`. — Owner: Impl. lead.
- `SELECT COUNT(*) FROM tabAccount WHERE company='Sapphire Fountains' AND account_name LIKE '%(deleted)%'` = **0**
- `SELECT COUNT(*) FROM tabAccount WHERE company='Sapphire Fountains' AND is_group=0 AND IFNULL(account_number,'')=''` = **0**
- `SELECT COUNT(*) FROM \`tabGL Entry\`` = **0**; exactly **1** account with `account_type='Temporary'`; native **Trial Balance** renders for FY2026.
- All four `tabCompany` default account fields non-empty and pointing at new-chart accounts; **`Stripe Payments Settings.deposit_account` resolves to an existing Account** (Single Link fields are not link-checked — assert the value explicitly).
- *Precondition:* S4 done (drafts were the largest Account-reference blocker); legacy submitted purchasing docs + the 4 stray GL Entries cleared; **WI-068 2026-window remap applied or its account numbers re-derived against the new chart** (each affected parent must retain exactly one postable `- General` child or `mapping._ledger_for_posting` returns None and the sync parks transactions). Full TEST dry-run first. *Gates S6.*

**S6. WI-030 FY/naming + WI-031 modes of payment.** — Owner: Controller / Impl. lead.
- `SELECT COUNT(*) FROM \`tabFiscal Year\` WHERE name='2027' AND disabled=0` = **1**; `... WHERE disabled=1` = **17** (2008–2024); **2025 and 2026 stay enabled** (the Opening Entry posts 2026-12-31; WI-053 trend JEs post into 2025/2026).
- New series prefixes appear in the doctypes' naming-series options; `tabSeries` rows exist with `current=0` for each. *(Never reset a series counter before S4.)*
- `SELECT COUNT(*) FROM \`tabMode of Payment\` mp WHERE mp.enabled=1 AND NOT EXISTS (SELECT 1 FROM \`tabMode of Payment Account\` a WHERE a.parent=mp.name AND IFNULL(a.default_account,'')<>'')` = **0**; `Stripe` and `ACH` stay enabled with `default_account='Stripe Clearing - SF'` (do **not** rename/disable them — Stripe settings reference them by name and `create_stripe_modes_of_payment` re-seeds on deploy).
- *Precondition:* S5 done (default-account targets exist). *Gates go-live; S7 waits on the Dec close.*

#### Jan 1 — go-live (empty ledger; interim AR per §C12)

The company transacts live in ERPNext from Jan 1 on the rebuilt empty chart. Opening balances are **not** yet posted — do not assume opening AR exists. Run §C12.

#### W2 — opening-balance tail (~Jan 10–15, after the WI-003 December close lands)

**S7. WI-032 opening Trial Balance JE @2026-12-31.** `sync_opening_balances(as_of_date='2026-12-31', auto_submit=0)`; delete every party line; re-square the `Temporary` row so debit=credit; review vs the Dec close package; submit. — Owner: Accounts / Impl. lead.
- `SELECT COUNT(*) FROM \`tabJournal Entry\` WHERE voucher_type='Opening Entry' AND docstatus=1 AND posting_date='2026-12-31'` = **1**, `total_debit` = QBO TB total to the cent.
- `SELECT COUNT(*) FROM \`tabJournal Entry Account\` WHERE parent=<JE> AND IFNULL(party_type,'')<>''` = **0** (AR/AP load in S8, not here).
- Native Trial Balance @2026-12-31 = QBO close package for every balance-sheet account except AR/AP.
- *Precondition:* WI-003 Dec close frozen; S5/S6 done; `realm_id` set (S2 kept it alive) — else CSV fallback from the close-package TB (+1–2 days). *Gates S8.*

**S8. WI-033 opening AR/AP + WI-034 open POs.** Load one `is_opening='Yes'` Sales Invoice per open QBO AR line and one Purchase Invoice per open bill (batches of 100, commits, no taxes); re-key genuinely-open POs on the new chart. — Owner: Accounts / Procurement.
- `SELECT SUM(outstanding_amount) FROM \`tabSales Invoice\` WHERE is_opening='Yes' AND docstatus=1` = QBO AR aging total to the cent; mirrored PI vs AP aging.
- `SELECT SUM(debit)-SUM(credit) FROM \`tabGL Entry\` WHERE account=<Temporary Opening>` = **0** (residue extinguished).
- **Hard gate H4:** `SELECT COUNT(*) FROM tabCustomer WHERE custom_stripe_autopay_enabled=1` = **0** and Stripe Payments Settings disabled at load time (submitting opening SIs can auto-charge via `auto_charge_on_invoice_submit`). **H2:** create missing parties first with `create_customer_folders=0`.
- `SELECT COUNT(*) FROM \`tabPurchase Order\` WHERE docstatus=1 AND status IN ('To Receive and Bill','To Receive','To Bill')` = open-PO list count; QBO PO#→ERPNext crosswalk archived. *(tabSales Order is empty — nothing to migrate.)*
- *Precondition:* S7 done. *Gates S9.*

**S9. WI-035 tie-out + go-live sign-off.** `compare_account_balances(as_of_date='2026-12-31')`; native TB/AR/AP/bank tie-outs; CPA + close-owner written sign-off; then freeze. — Owner: Close owner + CPA.
- `compare_account_balances('2026-12-31')`: **0** accounts in mismatched/qb_only/erp_only buckets (or each residual has a written accepted-variance note, within $0.01).
- `SELECT SUM(debit)-SUM(credit) FROM \`tabGL Entry\` WHERE account LIKE '%Temporary%'` = **0**.
- `SELECT accounts_frozen_till_date FROM tabCompany WHERE name='Sapphire Fountains'` = **'2026-12-31'**; `role_allowed_for_frozen_entries='Accounts Manager'`.
- Sign-off document archived. *Gates S10. Run the compare while QBO is still reachable.*

**S10. WI-045 Stage B — full Disconnect.** `core.api.disconnect`; neutralize residual Failed logs (WI-001 step 2); confirm `Accounting Intake Settings.qbo_writeback_enabled=0`. — Owner: Sync.
- `tabSingles`: `realm_id` **IS NULL**, `sync_enabled=0`, `status='Not Connected'`.
- `SELECT COUNT(*) FROM \`tabQuickBooks Sync Log\` WHERE creation > <disconnect ts>` = **0** after 7 days.
- `QuickBooks Sync Mapping` / `Sync Log` / `Raw Payload` counts unchanged (audit data retained). *(The 3 hourly QBO hooks stay in hooks.py, inert, until WI-052.)*

### 5B. Go/No-Go decision gate (~Dec 22 2026)

| Check | Owner | Evidence |
|---|---|---|
| All blocking items' acceptance criteria green **on TEST** (WI-022 parallel run passed). | Impl. lead | WI-022 UAT tracker all-green. |
| Precondition decisions (§ above) all closed. | CEO | Recorded in this runbook. |
| Explicit **GO** with sign-offs (CEO + accountant + impl. lead). | CEO | Meeting minutes archived. |

### 5C. Day-1 existence checks (by Jan 8 2027)

Each business owner creates the first real document of their type on prod. Six checks, e.g.:
```sql
SELECT COUNT(*) FROM `tabSales Order`   WHERE docstatus=1 AND transaction_date >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabQuotation`     WHERE docstatus=1 AND transaction_date >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabSales Invoice` WHERE docstatus=1 AND posting_date     >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabPurchase Order`WHERE docstatus=1 AND transaction_date >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabPayment Entry` WHERE docstatus=1 AND posting_date     >= '2027-01-01';  -- ≥ 1
SELECT COUNT(*) FROM `tabJob Interval`  WHERE start_time                        >= '2027-01-01';  -- ≥ 1 (kiosk)
```

---

## 6. Day-1 / Week-1 support

| # | Step | Owner | Evidence |
|---|---|---|---|
| 6.1 | Named floor-walker rota for week 1 (per value stream). | Impl. lead | Rota published. |
| 6.2 | "Cutover Issues" triage list (native ToDo/Issue), one row per reported problem. | Impl. lead | List live; each item has an owner + status. |
| 6.3 | Daily 15-minute triage standup for two weeks. | Impl. lead | Standup notes archived. |
| 6.4 | Escalation path to the impl. lead documented and circulated. | Impl. lead | Path published. |
| 6.5 | Two-week post-cutover review; open issues below the agreed threshold. | CEO + Impl. lead | Review minutes; open-issue count < threshold. |

---

## 7. Abort criteria & path

**Point of no return = S4 (the WI-028 delete).** Before S4, abort is clean: re-enable `sync_enabled=1` and recreate the Intuit webhook subscription (S2 reverses). From S4 onward, the only rollback is **restore the S3 backup** — which is why S4/S5 run in a freeze window with no other writes.

| Trigger | Action |
|---|---|
| S1–S3 fail (dirty final sync, bad backup). | Halt; do not proceed to S4. Fix and re-run; fully reversible. |
| S4/S5 fail mid-run. | Restore the S3 backup; the site returns to pre-freeze state; re-diagnose before re-attempting. |
| S7–S9 tie-out cannot reconcile within tolerance. | Cancel the offending opening docs (S7/S8 native `docstatus=2` rollbacks), correct, re-run. A QBO-side error loops back to WI-003 re-close (out of scope here). |
| **Overall no-go** at the §5B gate. | **Stay on QBO for January**; ERPNext parallel run continues on TEST; re-evaluate a February-month-end cutover (structurally identical — the opening TB just cuts from that month's close, per OD-5). |

Reversibility summary: everything through S9 is reversible (backup restore, or native cancels). **S10 (Disconnect) is reversible only by re-granting OAuth (WI-002) and recreating the Intuit webhook** — and is deliberately last.

---

## Interim-AR procedure (review correction C12) — Jan 1 to ~Jan 15

Opening AR **cannot exist** before the December close completes (~Jan 10–15 → S8). Between Jan 1 go-live and S8:

1. Incoming payments on **pre-cutover** invoices are recorded as **unallocated / on-account Payment Entries** — party set, **no invoice reference**.
2. Once S8 posts the opening Sales Invoices, run native **Payment Reconciliation** to clear those on-account PEs against the opening invoices.
3. **Stripe autopay enrollment stays gated on S8** (WI-033 acceptance) — no `Stripe Autopay Consent` rows until then (hazard H4).

Machine checks:
- Interim: `SELECT COUNT(*) FROM \`tabPayment Entry\` WHERE docstatus=1 AND posting_date>='2027-01-01' AND unallocated_amount>0 AND party IS NOT NULL` may be > 0 (expected).
- After S8: no unallocated pre-cutover receipts remain (Payment Reconciliation cleared them).

---

## Appendix A — Bulk-operation hygiene (for anyone running cutover-week data scripts)

The advisory hazard gate is **WI-050**: set `ERPNext Enhancements Settings.ai_write_gating_enabled=1` for the window (AI writes then create an `AI Pending Action` for confirmation instead of mutating directly), and confirm this bulk-write rule is in **every** workstream's DATA runbook.

**Correction (verified 2026-08-31 against `main`):** the wildcard `'*'` `after_save` → `global_triton_sync` hook that PLAN.md, WI-050, WI-023/028/033 and this item's older scope text all warn about is **retired (v1.341.1)** — and the tombstone at `erpnext_enhancements/utils/triton_sync.py` records that it was **inert from the day it was written** (Frappe dispatches no server-side `after_save` document event). So the "one queued Triton POST per ORM save" storm is a non-issue on the current codebase. Triton keeps its index fresh via its own sync engine. **Do not** re-introduce a global save hook without a real design (gateway secret header, per-doc→user mapping, timeout, `enqueue_after_commit=True`).

The two named per-doctype hazards **are still live** and must be respected during bulk touches:

| Hazard | Location | Rule during bulk runs |
|---|---|---|
| Customer Drive-folder provisioning | `Customer.after_insert → google_drive.drive_utils.enqueue_customer_folder` (hooks.py:701), gated by `Project Folder Google Drive Settings.create_customer_folders` | Keep **`create_customer_folders=0`** while inserting Customers (e.g. S8 party creation). Same for `create_opportunity_folders` + `Opportunity.after_insert` (hooks.py:580). |
| Closed-Won prompt | `Opportunity.on_update → crm_enhancements.project_prompt.prompt_create_project_on_won` (hooks.py:567) | Avoid status-touching bulk `doc.save()` on Opportunity; use `frappe.db.set_value`/SQL. |

General rules:
- Prefer `frappe.db.set_value` / direct SQL to **bypass `doc_events`** wherever the logic permits (e.g. WI-023 back-dates `valid_till` this way). Where an ORM `save()`/`submit()` is genuinely required (e.g. submitting opening Sales Invoices in S8), run **off-hours**, in **batches of 100 with commits**, and monitor the default RQ queue.
- **Deploy hazard:** a merge to `main` FLUSHDBs both redis instances and restarts the bench, silently killing every queued job. Any cutover script that enqueues work must be **re-drivable** after a deploy — never assume an enqueued job survived. Do not deploy mid-DATA-window (§1.4).

---

## Acceptance criteria (this runbook)

- [x] Runbook exists, versioned in `docs/`, with **every checklist line mapped to a machine check** (SQL / report / settings value) and an owner, and the **C5 ordering encoded as strictly sequential gated steps** (§5A, S1→S10).
- [ ] Go/No-Go minutes recorded with an explicit **GO** and sign-offs (§5B) — *at execution*.
- [ ] Day-1 six existence checks all ≥ 1 by Jan 8 (§5C) — *at execution*.
- [ ] Interim on-account PEs reconciled against opening SIs after S8 (§C12) — *at execution*.
- [ ] Two-week review held; open issues below threshold (§6.5) — *at execution*.

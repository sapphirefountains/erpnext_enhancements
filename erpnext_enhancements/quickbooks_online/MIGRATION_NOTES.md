# QuickBooks Online → ERPNext: Data Migration Readiness

Notes for importing a full QuickBooks Online (QBO) company into ERPNext via the
**QuickBooks Online** integration. Written against the real Sapphire Fountains LLC
export (≈190 accounts, 474 customers, ~880 vendors, ~77k journal lines, history
back to **2008**, single currency **USD**). It covers what the integration imports
automatically, the configuration it depends on, and the known limitations a human
should verify after import.

---

## 1. What gets imported, and how

| QBO entity | ERPNext target | Notes |
|---|---|---|
| Account | Account | Hierarchy preserved via `ParentRef`; group vs ledger inferred. **Inactive accounts are now imported** so historical postings resolve. |
| Customer / Vendor / Item / TaxCode | Customer / Supplier / Item / Account | Top-level records only. **QBO sub-customers / jobs** (`Job`/`IsProject`/`ParentRef`) map to an ERPNext **Project** under the parent Customer (see below), not a flat `Parent:Job` Customer. |
| **Term** | Payment Terms Template | Single 100%-portion term (`DueDays` → credit days); linked onto Customer/Supplier `payment_terms`. |
| **Payment Method** | Mode of Payment | Credit-card → Bank type, otherwise Cash. |
| **Class** | Cost Center | Hierarchy preserved via `ParentRef`; parents become group cost centers. |
| Estimate | Quotation | |
| Invoice | Sales Invoice | |
| **Credit Memo** | Journal Entry | Credit A/R (customer as Party), debit each line's item income account. |
| **Sales Receipt** | Sales Invoice | Cash side not linked — see §4. |
| **Refund Receipt** | Journal Entry | Credit the `DepositToAccountRef` account, debit item income. Never touches A/R. |
| Bill | Purchase Invoice | |
| **Vendor Credit** | Journal Entry | Debit A/P, credit expense lines. |
| Payment | Payment Entry (Receive/Pay) | Posts to the company **default bank/cash** account — see §4. |
| **Bill Payment** | Journal Entry | Debit A/P, credit bank/credit-card. |
| **Purchase** (Expense / Check / Credit Card charge) | Journal Entry | Credit funding account, debit expense lines; a `Credit` refund reverses. |
| **Transfer** | Journal Entry | Debit destination, credit source. |
| **Credit Card Payment** | Journal Entry | Debit card liability, credit bank. |
| Journal Entry | Journal Entry | |
| Purchase Order | Purchase Order | |
| **Deposit** | Journal Entry | Debit deposited-to account, credit source lines (e.g. Undeposited Funds). |

**Posting directions for every cash-movement type above were verified against the
actual QBO Journal export** and are covered by unit tests in
`tests/test_quickbooks_online.py`.

### Not auto-mapped (import manually or as adjusting entries)
`Sales Tax Payment`, `Sales Tax Adjustment`, and `Inventory Starting Value` are
**not** mapped. They are low-volume in this dataset and/or depend on item-level GL
accounts QBO doesn't expose in the payload. They are skipped cleanly ("No native
ERPNext mapping"), not failed.

---

## 2. Configuration prerequisites (do these first)

The mappers fill required fields from **Company defaults**. Set these on the ERPNext
Company before importing or transactions will land in **manual review** (or fail to
insert). Every item below was validated live against the Sapphire Fountains instance
by inserting a sample of each document type; the ones marked ✅ have already been set.

- ✅ **Default Receivable Account** — Sales Invoice `debit_to` and customer Payment
  `paid_from`.
- ✅ **Default Payable Account** — Purchase Invoice `credit_to`, Bill Payment / Vendor
  Credit A/P, and vendor Payment `paid_to`.
- ✅ **Default Bank Account** (and/or **Default Cash Account**) — the bank side of
  Payment Entries.
- ✅ **Default Cost Center** (`Main - SF`) — auto-applied to Sales Invoice / Journal
  Entry P&L lines; without it ERPNext throws "Cost Center is mandatory".
- ✅ **Default Expense Account** (Cost of Goods Sold) — fallback expense account for
  Purchase Invoice item lines (imported items carry none).
- ✅ **Default Income Account** — was already set (`4110 - Sales`).
- **Default Currency** = USD (already correct).
- A default **selling Price List** (enabled) — already present (`Standard Selling`).
- ✅ **Perpetual inventory accounts.** This company has perpetual inventory enabled,
  so ERPNext demands the company's stock accounts be configured **even for non-stock
  Purchase Invoices** ("Please set default Stock Received But Not Billed…"). Set
  **Stock Received But Not Billed**, **Default Inventory** (Stock In Hand), and
  **Stock Adjustment**. (`Expenses Included In Valuation` is only used for stock
  landed-cost valuation, which the non-stock import never triggers, and the Company
  doctype resets it on save — leave it unset.)

Other prerequisites:

- ✅ **Fiscal Years back to the oldest transaction (2008).** Already present (2008→2026).
  ERPNext rejects a posting whose date has no Fiscal Year.
- **Imported transactions are created as drafts** (`docstatus = 0`) — they do not hit
  the GL/Trial Balance until submitted. Review, then bulk-submit when ready — **except the
  QuickBooks purchases that match a store run recorded on the Stock Scan page** (v1.536.0):
  - Each recorded line is already a **submitted** Purchase Receipt posting Dr
    `1410 - Stock In Hand - SF` / Cr `2210 - Stock Received But Not Billed - SF` at quantity ×
    the price before tax (a non-stock line posts nothing). QuickBooks holds the same purchase as a
    draft Purchase Journal Entry: Dr the expense account QuickBooks coded, Cr the card account, no
    party. Submitted as it stands, that draft books the goods twice (Dr 1410 from the receipt, Dr
    expense from the draft) and 2210 never clears. Left unsubmitted, the card liability is never
    booked and the card accounts no longer tie out to QuickBooks.
  - So for a draft that matches a recorded trip — same store, dated on the trip's day or up to
    three days after, for the receipt total (or the lines plus tax), the pairing the Store Runs KPI
    uses (`kpi_dashboards/metrics.pair_store_runs`) — **change the goods' debit from the expense
    account to `2210 - Stock Received But Not Billed - SF`**, for what the trip's receipts credited
    there (its stock lines before tax; split a line if need be), **then save it**; the bulk
    submit (runbook step S-D) submits it with the rest. That clears 2210 and still books the card
    liability. The rest — the tax, and any non-stock line — stays on the expense account
    QuickBooks used, or wherever Accounting decides tax goes.
  - **Before the bulk submit, run the *Store Run Charge Matching* report** (KPI Dashboards,
    v1.538.0) from **2026-01-01** to today with **Show = Needs action**, and do what each row's
    *What to Do* column says. From 2026-01-01, not from the day the Stock Scan page shipped: a
    Purchase Receipt with no PO at a flagged store counts as a trip whatever its date, and the page
    can post a run as yesterday. One row per recorded trip: its receipts, the draft it pairs with
    (the KPI's own pairing, so the list pairs exactly as the KPI does when counted from the same
    From Date), the amount to move (*Stock Lines Before Tax (Move to 2210)*: what the receipts
    credited to 2210, read from the GL) and whether the draft already carries it (*Moved to 2210*,
    to the cent). An adjusted draft is **saved, not submitted**; the bulk submit takes it. A row
    leaves *Needs action* once its draft carries exactly that 2210 debit, so re-running the report
    shows what is left. A charge that carries 2210 but matches no recorded store run (a draft
    adjusted for a trip whose receipt was later cancelled, say) is listed there too, to be moved
    back to the expense.
  - **Then set Show = Waiting** and check every trip dated on or before the last QuickBooks sync.
    Its charge did not pair: a bank-feed date more than three days late, an amount outside the
    tolerance, two runs of one purchase. Find its draft by hand and adjust it like a *Needs action*
    row; once the draft carries exactly the trip's stock lines on 2210, the report shows it as the
    trip's charge (Match Basis *Its 2210 debit (found by hand)*) and the row moves to *Done*. Or
    confirm there is none, and bill the trip from its receipts after the cutover (no card charge
    arrives from QuickBooks after it). Submitted unchanged, such a draft books the goods twice,
    and billing the trip from its receipts afterwards would credit the card a second time.
  - **A draft submitted before it was adjusted** reappears under *Needs action* as *Submitted with
    the goods on the expense*. Fix it with **one correcting Journal Entry** — Dr `2210 - Stock
    Received But Not Billed - SF` / Cr the expense account the charge used, for the amount the row
    gives — whose **Reference Number is the charge's name**; the report adds the 2210 debit of every
    submitted entry that names a charge that way, so the row moves to *Done*. **Never amend a
    QuickBooks Journal Entry** to fix it: amending cancels the original, `tabQuickBooks Sync
    Mapping` stays on the cancelled one, and the pairing follows the mapping, so the charge would
    drop out of the report and its trip would read as waiting.
  - Every other draft is reviewed and submitted as above.
- **Chart of Accounts mismatch.** QBO account names carry numeric prefixes
  (`13000 US Bank Checking`). If you let the integration create accounts, expect a
  large COA. If you pre-built a COA, use the **QuickBooks Record Matching** page (Finance
  Hub → QuickBooks Matching; filter to Account) to link QBO accounts to yours first, so
  transactions post to the right ledgers. Linking a QBO account away from one the import
  created folds that created account into the one you chose.

---

## 3. Recommended import procedure

1. **Connect** (OAuth) and set `company` on *QuickBooks Online Settings*.
2. Confirm the Company defaults and Fiscal Years above.
3. Run **Import All** for **masters first** (Account, Customer, Vendor, Item,
   TaxCode) — the integration already orders masters before transactions, but doing a
   masters-only pass lets you review/link the chart of accounts before any posting.
4. Run **Import All** for transactions. Imports are **idempotent** (keyed on QBO id),
   so re-running is safe and resumes/repairs rather than duplicating.
5. Triage **manual review** rows in *QuickBooks Sync Log* (unbalanced JEs, missing
   defaults, unmapped types).

**Volume / performance.** ~77k journal lines is large; the importer pages 100
records per API call sequentially. Expect the full transactional import to run for a
while and to be **rate-limited by Intuit** (≈500 req/min, 100/call). Prefer running
it as a background job / off-hours, and lean on the idempotent re-run to recover from
interruptions rather than restarting from scratch.

---

## 4. Known limitations & things to verify

- **Undeposited Funds double-count (Payment + Deposit).** QBO records a customer
  payment into *Undeposited Funds*, then a *Deposit* moves it to the bank. ERPNext
  Payment Entries here post directly to the **default bank**, while imported Deposits
  *also* post to the bank. Importing **both** can double the bank side and leave
  Undeposited Funds unreconciled. **Choose one** per your reconciliation preference
  (Payments give you A/R clearing; Deposits give exact bank movement), or reconcile
  Undeposited Funds after import. Entity selection in Import All lets you pick.
- **Payment allocation depends on import order.** Imported Payment Entries now
  allocate against the Sales Invoices their QBO `LinkedTxn` names — but only against
  **submitted** ones, because ERPNext refuses to allocate against a draft and this
  integration imports invoices as drafts. During a migration that means most payments
  land unallocated on the first pass. **Re-sync Payments after submitting the
  invoices** and the allocations fill in (the reference table is rebuilt on every
  sync). Until then A/R *totals* are correct but per-invoice aging is not.
- **Sales tax IS imported** (since v1.246.0) as a single `Actual` Sales Taxes and Charges
  row per transaction, from `TxnTaxDetail.TotalTax` — which QBO carries **outside** the
  `Line` array, which is why it was dropped before. All jurisdictions post to one account
  (`QuickBooks Online Settings → Sales Tax Account`, defaulting to account number 25010),
  with the jurisdiction on the row's description, mirroring how QuickBooks itself keeps
  jurisdiction outside the ledger. **Identity comes from `TxnTaxCodeRef`, never
  `TaxRateRef`** — different QBO id spaces, and swapping them resolves silently to a
  real-but-wrong jurisdiction.
- **QBO discounts ARE imported** (since v1.247.0) onto the header `discount_amount` with
  `apply_discount_on = "Net Total"`, matching the order QuickBooks applies them. 36
  pre-2026 invoices carry one, **$89,561.00** in total.
- **Billable-expense passthrough lines ARE imported** (since v1.248.0). When a Bill line is
  flagged billable to a customer and reinvoiced, QBO writes a `SalesItemLineDetail` with an
  **empty `ItemRef`**, naming its destination account on `ItemAccountRef` instead. Each
  becomes an `Actual` charge booked to that account — crediting the COGS account the
  expense came out of, and the markup lines to `46300 Markup on Billable Expenses`. 1,035
  lines across 158 invoices, **$33,024.34**. An invoice whose lines are *all* passthrough
  maps zero item rows and still parks: ERPNext refuses an item-less Sales Invoice (one
  invoice, I100780).
- **A Sales Invoice now has to reconcile to QuickBooks to import.** `_sales_invoice_shortfall`
  compares `qty * rate` plus charges and tax, less discount, against `TotalAmt` and parks
  anything that does not agree — the sell-side twin of the Purchase Invoice guard, and the
  check whose absence let both the zero-quantity overstatement and the dropped sales tax
  survive unnoticed. Modelled against every cached payload at v1.248.0 it parks **54**
  documents, 43 of them pre-2026, each a real difference rather than rounding. **A resync
  parks more than that** — the guard is only one of several reasons a document routes to
  manual review (no customer, no item rows, conflicts), so treat the figure as a floor on
  what clears, not a prediction of the resync summary.
- **Sales tax on purchases.** Expense/Bill-Payment JEs are built from line accounts; a
  transaction carrying separate sales tax may not auto-balance and will route to
  **manual review** (by design — the balance guard refuses to post a lopsided entry).
- **Item-based expense lines** on a Purchase are skipped (their GL account lives on the
  Item, not the line); such a purchase will be flagged unbalanced for review.
- **Sub-customers (jobs)** map to ERPNext **Projects under the parent Customer**, not to
  flat Customers. A job is any QBO Customer flagged `Job`/`IsProject`, carrying a
  `ParentRef`, or at `Level` > 0 (its `FullyQualifiedName` is the colon path
  `Parent:Job`). The job links to an existing ERPNext project by its `PRJ-###` number
  (zero-padding ignored, so QBO `PRJ-401` matches `PRJ-00401`) when one exists, else a
  Project is created under the parent. The job's invoices / sales receipts / payments /
  estimates bill the **parent** Customer and are tagged with the Project for job costing.
  Customers import top-level-first so a job's parent is resolvable when the job is mapped.
  *(Earlier imports created flat `Parent:Job` Customers — and, via the Customer Drive
  hook, orphan top-level Drive folders; a separate remediation consolidates those.)*
- **Inactive entities** import as **enabled** so historical transactions can post.
  Disable them in ERPNext after import if you don't want them selectable.
- **QBO posts to group accounts; ERPNext cannot.** QuickBooks permits booking to an account
  that also has sub-accounts, so ~1,813 imported Journal Entry lines landed on group
  (parent) accounts and ERPNext refuses to submit them. Each affected parent gets one
  `- General` **ledger child** (A/R and A/P instead merge into the real Debtors / Creditors
  ledgers), leaving the parent's rollup identical. `mapping._ledger_for_posting` now
  redirects every resolved group account to that child on import, so it does not recur —
  and a parent with **no** `- General` child resolves to None, which parks the transaction
  for review rather than inventing an account. See
  [`WI-068`](../../work-items/WI-068-group-account-remap.md) and its
  [runbook](../../docs/migration/wi068-group-account-remap-runbook.md).
- **Pause the sync before submitting anything.** ERPNext cannot update a submitted
  document, so once an imported draft is posted every later QBO edit to it becomes a sync
  failure. This applies to both populations being unblocked — the Journal Entries above and
  the Sales Invoices awaiting re-import — and is a single decision, not a per-batch one.

---

## 5. Reconcile & opening balances

- **Automated reconciliation.** After importing (and submitting) transactions, run the
  **QuickBooks Balance Comparison** report (or the dashboard **Compare Balances**
  action). It pulls QBO's Trial Balance via the Reports API and compares it, account by
  account, against each linked ERPNext account's GL balance as of a date — flagging
  mismatches, QuickBooks-only and ERPNext-only accounts. This is the automated form of
  the manual Trial Balance tie-out below. The Reports API returns computed balances even
  when QBO's transaction/statement exports come back empty. **Reconcile Transactions**
  additionally checks each imported document's total against its stored QBO payload.
- **Opening balances (alternative to importing full history).** If you don't import every
  historical transaction, use the dashboard **Import Opening Balances** action to build a
  single balanced *Opening Entry* Journal Entry as of a cutoff date: one line per account
  from the QBO Trial Balance, A/R and A/P broken out **per party** from open
  customer/vendor balances, with any residual squared off against the company's
  **Temporary Opening** account. It is created as a **draft** — review it before
  submitting, paying attention to the reported **stock accounts** (post opening stock via
  a Stock Reconciliation, not the JE) and any **unmapped** QBO accounts. Don't both import
  full history *and* opening balances for the same period.

**Post-import check:** run ERPNext's Trial Balance and compare to the QBO Trial
Balance (`Trial_balance.xlsx`) — or simply run the **QuickBooks Balance Comparison**
report, which does this per account automatically. Investigate any manual-review/failed
rows in the sync log first.

### Reports API modernization (Intuit "v2", 2026 cutover)

Intuit is retiring the legacy Reports service; after the cutover **all** `/reports/`
responses are served by the modernized ("v2") service. ([Upcoming changes to Reports
APIs](https://medium.com/intuitdev/upcoming-changes-to-reports-apis-5083ec9aadce) —
the article states **August 31, 2026**; some third-party summaries cite **June 30,
2026**, so confirm the live date.) This integration uses the Reports API in exactly
one place — QBO's **TrialBalance** report, behind both *Compare Balances* and *Import
Opening Balances*. The reader was made v2-safe: Debit/Credit columns are resolved from
the response header by title rather than fixed index positions, empty amounts (`""`)
coerce to 0, and nested/section rows recurse. The **sync itself is unaffected** — entity
import (`/query`), CDC (`/cdc`) and writes don't use the Reports API.

**Validate early (optional).** To preview the modernized service against real company
data before the cutover, set `quickbooks_reports_testing_migration` truthy in the site
config and re-run *Compare Balances*:

```bash
bench --site <site> set-config quickbooks_reports_testing_migration 1   # 0/unset to disable
```

This adds Intuit's temporary `testing_migration` flag to the TrialBalance request. Run
the balance comparison with it on and off and confirm the per-account figures still
match; then unset it (the flag is temporary and removed once v2 is the only service —
the parser stays correct either way).

---

## 6. Remediation: legacy job-customers (one-off)

An **earlier** version of the importer mapped QBO sub-customers / jobs to flat
`Parent:Job` colon-named **Customers** (and the Customer Drive hook then made an orphan
top-level folder for each). The current importer maps jobs to **Projects** (§1), but
records created before that fix need a one-off cleanup, in
`quickbooks_online/core/job_remediation.py`.

`consolidate_qbo_jobs` walks the QBO job-customers (identified via QBO Sync Mapping +
the raw payload's `Job`/`ParentRef`, **not** a blind name `LIKE '%:%'`), top-level-first,
and for each: links it to the existing ERPNext Project by `PRJ-###` (else creates one
under the parent), tags the job's Sales Invoices with that Project, **merges** the
job-Customer into its top-level parent (`frappe.rename_doc(merge=True)` — moves
invoices/payments/quotations/addresses), repoints the QBO Sync Mapping to the Project,
and cleans the orphan Drive folder (trashes it if empty, else relocates it under the
parent customer folder). It is **dry-run by default**, idempotent, batched/committed,
per-record guarded, and Drive folders are **trashed (recoverable), never hard-deleted**.

Runbook (do this in order, **on a sandbox/test site first**):

1. Deploy the job→Project importer fix together with this module.
2. Preview (writes nothing):
   `bench --site <site> execute erpnext_enhancements.quickbooks_online.core.job_remediation.consolidate_qbo_jobs`
   Review the printed summary (jobs, would-merge, would-tag invoices, folder plan,
   `no_parent`/`ambiguous_project` skips). Optionally `--kwargs "{'limit': 5}"` to scope.
3. Apply: re-run with `--kwargs "{'apply': True}"` (requires System Manager). Re-runnable —
   already-consolidated jobs are skipped.
4. Confirm: 0 Customers with a colon in `customer_name` remain that are QBO jobs; spot-check
   a parent (e.g. *4th West Apartments*) now owns the projects/invoices and the orphan Drive
   folders are gone.
5. **Only then** re-enable the QBO sync (`QuickBooks Online Settings.sync_enabled`). Running
   the remediation first repoints every job's mapping to its Project, so the resumed sync
   updates the Project instead of recreating the flat Customer.

## 7. Remediation: the default-group sweep (v1.496.0)

QuickBooks has no supplier group, customer group or territory, so the importer used to
default them. Until v1.496.0 the default was `frappe.db.get_value(doctype, {"is_group": 0}, "name")`
-- "any leaf" -- and on Frappe v16 a dict-filtered `get_value` with no `order_by` sorts by
`creation` **descending**, so it answered "the leaf somebody created most recently". The
update path re-applied every mapped value on each re-sync, so each new Supplier Group anyone
added became the group of every QBO-linked Supplier on the next scheduled run. Verified on
prod 2026-09-22: all 911 QBO-linked Suppliers moved en bloc five times (Staffing on the
2026-06-18 vendor import, then Event Decor 07-21, Encapsulant 08-19, Labels 09-09, Garbage &
Junk Removal 09-16: 906 rows), and 466 Customers sit in "Government" for the same reason.

The forward fix is in `core/mapping.py`: **no default at all** (`DEFAULT_PARTY_GROUP = None`
-- a wrong group is worse than no group, Nik 2026-09-22), the update path never carries the
three fields whatever the record holds, and they are never QBO-owned.

**Suppliers are fixed by the deploy.** `patches/restore_supplier_groups_after_qbo_sweep`
runs `core/party_group_remediation.py` twice, Suppliers only: for every QBO-linked Supplier
in "Garbage & Junk Removal" the OLD value of the first `tabVersion` change whose NEW value is
a sweep landing group is the pre-sweep group -- a real one is **restored** (~63), anything
else (empty, itself a landing group, deleted since) is **cleared** to NULL, and a record
with no history is cleared only when its mapping says the import created it; then the
audit's hand-curated corrections in `core/supplier_group_corrections.json` (~230 Suppliers,
each with the basis for the call) win over the history walk. Supplier search fields are
recomputed alongside; writes are `frappe.db.set_value` (no doc hooks); per-record guarded,
both passes wrapped, cannot raise, safe twice. The migrate log carries both summaries,
including the names of any no-history rows left alone.

Verify after the deploy (all three from the MCP sandbox or `bench console`):

1. `select count(*) from tabSupplier where supplier_group = 'Garbage & Junk Removal'` -- should
   be 1 (Dumpster Depot).
2. `select count(*) from tabSupplier where supplier_group is null` -- roughly 640: the QBO
   payees that never had a real group (restaurants, fuel, banks, employee reimbursements).
3. Wait for the next `cdc_poll` (hourly, :20) and re-run 1 -- unchanged, because an update
   no longer carries `supplier_group`.

**Customers were fixed by the deploy of v1.498.0**, both fields.
`patches/restore_customer_groups_after_qbo_sweep` runs the same engine over
`Customer.customer_group` (landing value: Government -- the last of four leaves seeded in one
second on 2025-07-08, so the sync's default from day one; 466 Customers, all QBO-linked, one
with a real pre-sweep group) and `Customer.territory` (landing values: Asia, then United States
of America -- 355 Customers; the 2026-06-18 import overwrote 63 real territories with Asia, a
manual clear on 06-23 blanked them, and the 08-19 sync re-filed 178 under United States of
America). A blank territory whose history shows the sweep is a candidate too, which is how the
63 lost values come back; a no-history record is cleared as well (`clear_no_history=True`),
because every candidate is QBO-linked and the landing value is the sync's default; a record a
person re-set afterwards is left alone. Then `core/customer_corrections.json` keeps the 26
customers that really are government bodies in Government and the one territory a person set
by hand (Wadsworth Design Group, the day the leaf was created).

Verify after the deploy:

1. `select count(*) from tabCustomer where customer_group = 'Government'` -- 26.
2. `select count(*) from tabCustomer where territory = 'United States of America'` -- 1
   (Wadsworth Design Group); `... where territory = 'Utah'` -- roughly 180, up from 128.
3. Wait for the next `cdc_poll` and re-run -- unchanged.

Re-check by hand at any time (writes nothing):
`bench --site <site> execute erpnext_enhancements.quickbooks_online.core.party_group_remediation.restore_party_groups --kwargs "{'doctype': 'Customer', 'clear_no_history': True}"`

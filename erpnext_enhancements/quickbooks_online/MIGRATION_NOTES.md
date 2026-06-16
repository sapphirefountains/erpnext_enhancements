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
| Customer / Vendor / Item / TaxCode | Customer / Supplier / Item / Account | Sub-customers ("jobs") import as flat records using their **fully-qualified name** to stay unique. |
| Estimate | Quotation | |
| Invoice | Sales Invoice | |
| **Sales Receipt** | Sales Invoice | Cash side not linked — see §4. |
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
`Credit Memo`, `Refund Receipt`, `Sales Tax Payment`, `Sales Tax Adjustment`, and
`Inventory Starting Value` are **not** mapped. They are low-volume in this dataset
and/or depend on item-level GL accounts QBO doesn't expose in the payload. They are
skipped cleanly ("No native ERPNext mapping"), not failed.

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
  the GL/Trial Balance until submitted. Review, then bulk-submit when ready.
- **Chart of Accounts mismatch.** QBO account names carry numeric prefixes
  (`13000 US Bank Checking`). If you let the integration create accounts, expect a
  large COA. If you pre-built a COA, use the dashboard's **Link Existing Records** to
  map QBO accounts to yours first, so transactions post to the right ledgers.

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
- **Payment allocation.** Imported Payment Entries are **unallocated** (not linked to
  the specific invoices they paid). A/R/A/P *totals* are correct; per-invoice aging is
  not reconstructed. Allocate later if you need invoice-level aging.
- **Sales tax on purchases.** Expense/Bill-Payment JEs are built from line accounts; a
  transaction carrying separate sales tax may not auto-balance and will route to
  **manual review** (by design — the balance guard refuses to post a lopsided entry).
- **Item-based expense lines** on a Purchase are skipped (their GL account lives on the
  Item, not the line); such a purchase will be flagged unbalanced for review.
- **Sub-customers (jobs)** are flattened to ERPNext Customers (no native customer
  hierarchy). Names use the fully-qualified path (`Parent:Job`) to stay unique;
  consider mapping jobs to **Projects** post-import if you want job costing.
- **Inactive entities** import as **enabled** so historical transactions can post.
  Disable them in ERPNext after import if you don't want them selectable.

**Post-import check:** run ERPNext's Trial Balance and compare to the QBO Trial
Balance (`Trial_balance.xlsx`). They should tie out per account; investigate any row
in the sync log's manual-review/failed counters first.

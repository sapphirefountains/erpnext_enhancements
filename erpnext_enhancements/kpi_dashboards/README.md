# `kpi_dashboards/` — nightly department KPI snapshots

Precomputes each department's KPIs overnight into `KPI Snapshot` documents, which the desk
dashboards then read. Dashboards never compute live.

The metric catalogue — every KPI, its definition, its source doctypes, and its target — is
[`docs/KPI_DASHBOARD_DESIGN.md`](../../docs/KPI_DASHBOARD_DESIGN.md). This README covers the
code.

## Why precompute

A dashboard that aggregates on load is slow, hammers the database at exactly the moment
someone is watching, and gives a different answer each refresh. Snapshotting fixes the number
to a point in time, which is also what makes period-over-period trend meaningful.

The engine mirrors the Morning Briefing pattern (`api/briefing.py`): a cron entry checks the
master switch and hands a batch to the `long` queue.

## File map

| File | Purpose |
|---|---|
| `snapshots.py` | The snapshot engine. Builds one `KPI Snapshot` per department, **committing per department** so one slow or broken aggregator cannot sink the rest of the run |
| `metrics.py` | Pure KPI math — **no `frappe` import**, so it runs in the bench-free CI suite. Turns a raw value plus its target into the presentation fields: Good/Watch/Bad status, period-over-period trend, display string, source-staleness check. Deterministic, side-effect free, `now` injectable |
| `store_run_matching.py` | Pure rules of the **Store Run Charge Matching** report (v1.538.0) — **no `frappe` import**: the report's window over the KPI's pairing, one row per recorded store run, the 2210 check, *What to Do*, the Show buckets and the summary |
| `report/store_run_charge_matching/` | Accounting's list of recorded store runs and the card charge each pairs with, for the QuickBooks cutover (runbook step S-D). Read-only; it only reads, the rules are above |

## Aggregators read ERPNext, never the upstream APIs

Each aggregator is a **pure read** over the same doctypes the dashboard catalogue cites —
Sales Invoice / Purchase Invoice / Payment Entry as the post-QBO-sync system of record,
Opportunity / Lead, Sapphire Maintenance Record / Contract, and so on.

It never calls QuickBooks or Stripe live. That keeps the nightly run fast and independent of
third-party availability, at the cost of being only as current as the last sync — which is
why freshness is recorded in `source_freshness_json` on the snapshot, so a stale upstream is
visible on the dashboard instead of silently producing confident wrong numbers.

If you add an aggregator, record its source freshness too. A metric with no staleness signal
is worse than a missing metric.

## Keeping the math testable

`metrics.py` is pure on purpose: grading and trend logic are the parts most likely to be
subtly wrong, and they are unit-testable without a database. Put new grading logic there, not
in `snapshots.py`.

```bash
python -m unittest erpnext_enhancements.tests.test_kpi_metrics -v
```

## DocTypes

| DocType | Role |
|---|---|
| `KPI Snapshot` | One department's snapshot for a period, with `source_freshness_json` |
| `KPI Snapshot Value` | A single metric value on a snapshot (status, trend, display) |
| `KPI Target` | The target a metric is graded against |
| `Marketing Spend` | Manual marketing spend input |
| `Marketing Web Snapshot` | Web/analytics figures |
| `HR Stat Entry` | Manually entered HR statistics |

## Workspaces

One per department, plus the module workspace: `executive_dashboard`, `finance_dashboard`,
`sales_dashboard`, `marketing_dashboard`, `operations_dashboard`, `production_dashboard`,
`service_dashboard`, `design_dashboard`, `product_dashboard`, `hr_dashboard`, `kpi_dashboards`.

Each carries the KPI Cockpit plus that department's **operational widgets** — live worklists
that sit above the snapshot numbers and say what to do about them. They are Custom HTML
Blocks; their sources and the live-vs-snapshot split are documented in
[`custom_html_blocks/README.md`](../custom_html_blocks/README.md), and their feeds are the
`*_dashboard.py` modules in [`api/`](../api/README.md). Product is the one department with no
widgets yet — its dashboard's three roles (Item Manager, Stock Manager, Sales Manager) want
three different boards, and that has not been decided.


## Service split off Operations; Operations is inventory (v1.530.0)

Nik, 2026-09-24: maintenance belongs under Production, not Operations, and anything inventory
belongs on Operations. So:

- **Service** is a tenth department. `_service_metrics` carries the six maintenance KPIs that
  opened Operations, keys unchanged, and the Day Board, Chemistry Alerts and Labor Capture
  widgets moved with them. In the sidebar it sits in a Production group beside the Production
  Dashboard (see [`docs/workspace-sidebars.md`](../../docs/workspace-sidebars.md) for why a
  group). `_EXEC_ROLLUP` reads its two maintenance numbers from Service now; left on
  Operations they would have dropped off the Executive dashboard without an error.
- **Operations** is inventory and purchasing: store runs and their spend, stocked items below
  reorder and out of stock, stock at a placeholder cost, unpriced PO lines, the share of stocked
  items counted in 90 days, and the Stock Scan review queue. The three stock-level KPIs moved
  here from Product unchanged. Device compliance, unsynced time logs and project naming stayed.

**A "stocked item" is an Item with a positive reorder level.** That is ERPNext's own marker for
"we keep this on the shelf", and it is what drives the automatic Material Requests, so there is
no second list to drift from it.

**A "store run" is a purchase from a Supplier ticked *Store-Run Vendor*** (a Check created by
the patch; Home Depot and Lowes are ticked to start). Today the only record of a run is a
QuickBooks card purchase, which the sync imports as a draft Journal Entry with no party, so
`_store_runs` recovers the vendor from the newest raw payload's `EntityRef` (type Vendor only,
card refunds excluded). After the cutover it also counts submitted Purchase Receipts and
Purchase Invoices from those suppliers with no PO behind them. With no supplier ticked the KPI
is not published at all, because the count would be 0 by construction and read as the goal met.
Measured on 2026-09-24: 206 Home Depot and Lowe's card transactions in 12 months (2 of them
returns), $16.3k, and almost none since July 7 on the Amex card that carried 168 of them. That
gap is uncategorized QuickBooks data, not an improvement, until bookkeeping says otherwise.

**Since v1.536.0 a trip is counted once, whichever records it** (`metrics.combine_store_runs`,
pure and tested bench-free; since v1.538.0 the pairing itself is `metrics.pair_store_runs`, which it
calls, so the Store Run Charge Matching report lists exactly the pairs it counts). The Stock Scan page records a run the same day as Purchase Receipts
carrying a run id (`custom_store_run`) and the receipt total; the card charge arrives in QuickBooks
about four weeks later. *Charges* are the money records — QuickBooks card purchases, standalone
Purchase Invoices from a store (an invoice made from a store-run receipt has `purchase_receipt`
set and is the same trip), and, for after the cutover, submitted Journal Entries **crediting** the
store's payable (`metrics.journal_store_charges`: a debit to the store is a payment, and a bill
plus its payment booked as two unlinked entries would otherwise count twice). **Payment Entries are
never counted**: a payment is money for a purchase already booked. *Recorded trips* are the
receipts grouped by run id (or by receipt number at a store on a day). Each trip is paired with at
most one charge at the same store (`store_key`, so Lowes and Lowe's are one) dated **on the trip's
day or up to three days after**, and **only on the amount**: a charge equal to the receipt total,
else one the lines plus tax could make. A charge of any other amount is never taken, however near,
or a trip whose own charge is missing would swallow the next trip's charge at that store. The
window is there because QuickBooks holds some purchases from two feeds and a bank-feed entry
carries the bank's posting date (Lowes $16.60 on the Capital One card: `ACC-JV-2026-27340` from the
receipt email, 2026-02-07, and `ACC-JV-2026-27137`, "LOWES #02662* - 2486", from the feed,
2026-02-09), so a trip can pair with a feed charge when that is its only charge; a feed entry more
than three days late still counts as a second trip, and a purchase QuickBooks holds twice, like that
one, counts twice recorded or not, as in the baseline. Count = trips +
unpaired charges; spend = the charge
for a paired trip, the receipt total for an unpaired one, and every unpaired charge. The source is
*Purchase Receipt + QuickBooks* with **no freshness entry**, so a stale QuickBooks sync no longer
greys out runs recorded today. **When technicians start recording, the 30-day count will rise
from 3 (2026-09-24) toward the real rate** — 231 charges in the 12 months to that day, about 19 a
month: that is the measurement catching up with trips QuickBooks has not categorized yet, not
more trips. With nothing recorded it returns exactly
the old figure. The review-queue KPI (`stock_scan_review_queue`, key unchanged) is now labelled
*Stock Scan Saves Awaiting Review*, since store-run lines join it.

`tests/test_kpi_departments.py` checks that the seven places a department is named agree.

## Store Run Charge Matching: the pairs, listed for Accounting (v1.538.0)

A store run recorded on the Stock Scan page has already posted Dr 1410 / Cr 2210 for its stock
lines before tax, and QuickBooks holds the same purchase as a **draft** Journal Entry (Dr the
expense it coded / Cr the card). At the cutover, step S-D of
[`docs/migration/backlog-gl-posting-runbook.md`](../../docs/migration/backlog-gl-posting-runbook.md)
submits the 2026 drafts; a draft that pairs with a recorded trip must first have its goods debit
moved to 2210, or the purchase is booked twice and 2210 never clears. The **Store Run Charge
Matching** report is the list of those pairs.

**It cannot disagree with the KPI, by construction.** `metrics.pair_store_runs` is the pairing,
extracted from `combine_store_runs` unchanged (`combine_store_runs` calls it; `test_kpi_metrics`
runs every earlier case and 1,500 generated histories through both the new and the v1.536.0 body
and compares the results bit for bit). It returns the trips with their receipts, the charge each
took and **which pass took it** (`receipt_total` or `lines_plus_tax`), and the charges with their
paired flag. The report reads through the KPI's own `snapshots._store_run_rows`, from
`STORE_RUN_LOOKBACK_DAYS` (7, now defined in `metrics`) before the From Date, and keeps charges up
to `STORE_RUN_PAIR_DAYS` (3) after the To Date. Receipts are not cut at that end: a later receipt
carrying a trip's run id still changes the trip's lines. With that window every trip in range
pairs as it does in the KPI, which `test_store_run_matching` checks on generated data. The Store
filter is applied after the pairing, never to the rows, so Lowes and Lowe's still pair as one
store.

`_store_run_rows` gained identifying columns the count never reads: each charge's `voucher_type`,
`voucher_no`, `docstatus` and `source` (`QuickBooks` or `ERPNext`), and each receipt's name,
company, owner, `net_amount`, `is_stock_item` and `stock_amount`. **The stock columns read what the
receipt posted, not the Item's stock flag today.** `stock_amount` is the receipt's net credit to the
company's Stock Received But Not Billed account in the GL, and `is_stock_item` is whether it has a
stock ledger entry. An Item can be made a stock item after its receipt (allowed while it has no
stock ledger), and on production `MAT-PRE-2026-00038` credited $81.00 to 2210 while its Items'
current flags said $205.50 (2026-09-25).

**What the report reads besides the KPI's rows**, all `frappe.db.sql` with bound params:

- the 2210 debit each matched charge already carries, from the voucher's own lines, so a draft
  with no GL yet is read like a submitted one: Journal Entry Account rows, and Purchase Invoice
  Item rows whose expense account is 2210;
- the submitted Purchase Invoices made from the trips' receipts.

**What to Do** (`store_run_matching.build_rows`; *Moved to 2210* means equal to the cent):

| Trip | What to Do | Show |
|---|---|---|
| Matched draft, 2210 short of the stock lines | Move $X (more) of the goods debit to 2210, then submit | Needs action |
| Matched charge already submitted, 2210 short | Move $X from the expense to 2210 (amend, or a correcting Journal Entry) | Needs action |
| More on 2210 than the stock lines | Reduce it | Needs action |
| Matched draft, 2210 exact / no stock lines | *Goods debit on 2210: submit* / *No stock lines: submit as is* | Done |
| Matched and submitted, 2210 exact | Done | Done |
| No charge, every receipt billed by a submitted Purchase Invoice | Billed from the receipts | Done |
| No charge, some receipts billed | Bill the rest | Needs action |
| Matched **and** billed from the receipts | Check the purchase is not booked twice | Needs action |
| No charge | Waiting for the card charge | Waiting |

The summary (store runs, matched, needs action, still to move to 2210, already moved) covers
every trip in range whatever *Show* is set to. Roles: Accounts Manager, Accounts User, Purchase
Manager, System Manager; `ref_doctype` Purchase Receipt, so v16 also requires report permission
on it (every production user holding one of those roles also holds Accounts User, Purchase User
or Stock User, which carry it). No writes and no buttons.

## Item naming, split into backlog and new items (v1.532.0)

Product carries two naming KPIs, both from `inventory_enhancements.item_naming_rules.audit`
(the Item Naming Audit report's own call, so the three cannot disagree):
`item_naming_compliance_pct` over the whole live catalogue, the backlog measure, and
`item_naming_new_compliance_pct`, *Item Naming Compliance (New Items)*, over Items created on
or after `item_naming_rules.NAMING_GO_LIVE` (2026-10-01, POL-0602's effective date). Nik set
the new-items target at 100% on 2026-09-24 (TASK-2026-02238). The second audits the whole
catalogue and then keeps the new rows (`restrict_to`), so a new Item named like an old one
still counts as a collision. It is not published until the first such Item exists, since
`add()` drops `None`. It reuses the backlog figure's audit but has its own `try`, so its
failure cannot take the backlog figure or the Product department with it. Its 100% KPI Target
is seeded by `patches/seed_inventory_kpi_targets` (v1.532.0), with the Operations inventory
targets Nik approved on 2026-09-24: store runs 4, below reorder 5, out of stock 0, counted 100%,
placeholder-cost lines 0, unpriced PO lines 0. Insert-only, so an edited row wins.

## Marketing spend and value-stream reporting (WP-4, v1.243.0)

`marketing_spend_import.py` + the **Marketing Spend Rollup** and **Value Stream
Performance** reports. Operational notes are in
[`docs/marketing-spend-runbook.md`](../../docs/marketing-spend-runbook.md).

`Marketing Spend` held **zero rows**, so there was no budget baseline and no
denominator for any cost-per-lead figure. The importer upserts (a month/channel
pair is unique by the doctype's autoname, so re-importing a corrected export
would otherwise collide on every row), canonicalises channel spellings against
`CHANNEL_ALIASES` and **reports every rename** rather than applying it silently.
An unreadable amount is refused rather than coerced to 0 — note it does not use
`frappe.utils.flt`, which would turn "n/a" into a silent zero in the denominator.

`Marketing Spend.value_stream` is optional and **never apportioned**: a channel
serving several streams stays blank and shows as Unallocated on the dashboard.

**Value Stream Performance takes no required parameters** — it is opened live in a
standing weekly meeting — and runs in four queries total, because the
`Value Stream` child table carries ~1,460 rows across three parent doctypes and a
per-stream subquery re-scans it every time.

### Four KPIs were measuring a dead column

`Lead.source` / `Opportunity.source` lost their DocField when erpnext v15 renamed
them to `utm_source`. frappe never drops columns, so "Unsourced Leads",
"Unsourced Opportunities", "Sourced Pipeline Value" and "Sourced Wins (30d)" all
ran without error against a frozen pre-2023 snapshot. Fixed to read
`custom_lead_source`; the `Unknown (pre-Aug 2026)` bucket counts as unsourced,
because it is a recorded gap rather than a channel. A test parses the AST of
`snapshots.py` and fails if any query references the dead column again.

### Data source failures are now announced

GSC had failed on 40 of 40 nightly pulls with the only trace being `pull_error`.
`_alert_on_source_change` raises a Notification Log to System Managers when a
source starts failing **and** when it recovers — on transitions only, because a
nightly "still broken" alert gets muted and then hides the next real outage.

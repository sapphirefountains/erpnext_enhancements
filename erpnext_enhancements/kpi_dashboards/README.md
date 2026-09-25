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
| `store_run_matching.py` | Pure rules of the **Store Run Charge Matching** report (v1.538.0) — **no `frappe` import**: the report's window over the KPI's pairing, one row per recorded store run, the 2210 check (correcting entries included, through chains), links by Reference Number, `not-store-run` (and the receipt it clears), `part-paid`, *What to Do*, a charge carrying 2210 with no store run, a charge never carrying more than it paid (`_fits`), the 2210 backstop (`attribute_2210`), the Show buckets and the summary |
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
calls, so the Store Run Charge Matching report pairs exactly as it does when counted from the same From Date). The Stock Scan page records a run the same day as Purchase Receipts
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
Matching** report is the list of those pairs. How Accounting uses it, and every Reference Number
token it reads, is in
[`quickbooks_online/MIGRATION_NOTES.md` section 8](../quickbooks_online/MIGRATION_NOTES.md#8-store-runs-at-the-cutover-step-s-d);
this section is how it works.

**It pairs exactly as the KPI does when counted from the same From Date.**
`metrics.pair_store_runs` is the pairing, extracted from `combine_store_runs` unchanged
(`combine_store_runs` calls it; `test_kpi_metrics`
runs every earlier case and 1,500 generated histories through both the new and the v1.536.0 body
and compares the results bit for bit). It returns the trips with their receipts, the charge each
took and **which pass took it** (`receipt_total` or `lines_plus_tax`), and the charges with their
paired flag. The report reads through the KPI's own `snapshots._store_run_rows`, from
`STORE_RUN_LOOKBACK_DAYS` (7, now defined in `metrics`) before the From Date, and keeps charges up
to `STORE_RUN_PAIR_DAYS` (3) after the To Date. Receipts are not cut at that end: a later receipt
carrying a trip's run id still changes the trip's lines. With that window every trip in range
pairs as it does in the KPI counted from the From Date, which `test_store_run_matching` checks on
generated data. The nightly KPI counts from 30 days back and the report defaults to 60, so a chain
of same-amount trips at one store reaching in from before one start but not the other can pair a
trip differently; at step S-D (From Date 2026-01-01) there is nothing earlier. The Store filter is
applied after the pairing, never to the rows, so Lowes and Lowe's still pair as one store.
**Charges are read in a fixed order** (each arm by posting date and voucher name): the pairing
breaks an exact tie by input order, which before this was whatever the database returned.

`_store_run_rows` gained identifying columns the count never reads: each charge's `voucher_type`,
`voucher_no`, `docstatus`, `source` (`QuickBooks` or `ERPNext`) and `company`, and each receipt's name,
company, owner, `net_amount`, `is_stock_item` and `stock_amount`. **The stock columns read what the
receipt posted, not the Item's stock flag today.** `stock_amount` is the receipt's net credit to the
company's Stock Received But Not Billed account in the GL, and `is_stock_item` is whether it has a
stock ledger entry. An Item can be made a stock item after its receipt (allowed while it has no
stock ledger), and on production `MAT-PRE-2026-00038` credited $81.00 to 2210 while its Items'
current flags said $205.50 (2026-09-25).

**What the report reads besides the KPI's rows**, all `frappe.db.sql` with bound params:

- **every Journal Entry's Reference Number** (`cheque_no`), drafts and submitted, dated from the
  reader's lookback, **for every vendor**, not only the store-run vendors': a QuickBooks draft under
  a vendor that is not ticked *Store-Run Vendor* is invisible to the KPI's reader, and linking it is
  how Accounting says it is a trip's charge (below). The QuickBooks mapping is joined once as a
  derived table: `tabQuickBooks Sync Mapping` has no index on `erpnext_name`, and a correlated
  `exists` took 2.8 s over 826 entries on production where the join takes 26 ms;
- the keys those Reference Numbers name that the reader's rows do not resolve, looked up three
  ways: as the run id or name of a receipt of a trip dated before the reader's lookback (read with
  the columns and rules of the reader's receipts query, which a test compares; a receipt named by
  its name brings the rest of its run), as the name of a Journal Entry that nothing else read, and
  as the name of any Purchase Receipt, with whether the reader's own rules make it a store run (the
  receipt a `not-store-run` entry's 2210 clears; by primary key, 0.4 ms on production) -- then again
  with the tokens of what that found, up to four rounds, so a chain of correcting entries is
  followed to its charge;
- **the backstop**: every Journal Entry line, draft or submitted, on its own company's Stock
  Received But Not Billed account, dated from the lookback to the To Date, whatever its Reference
  Number or vendor (0 rows on production, 6.6 ms, 2026-09-25);
- the 2210 debit each charge on a row, and each charge that is neither paired nor linked, already
  carries, from the voucher's own lines, so a draft with no GL yet is read like a submitted one:
  Journal Entry Account rows, and Purchase Invoice Item rows whose expense account is 2210; **plus
  the 2210 lines of their correcting entries**, submitted and draft; company-scoped, as the backstop
  is, so an entry read both ways reads the same;
- the submitted Purchase Invoices made from the receipts of the trips on the rows and of every trip
  their charges are linked to.

These rules are the report's own and change nothing in the KPI (`store_run_matching` module
docstring):

- **Correcting entries count.** The one fix the report advises for a charge already submitted with
  the goods on the expense is a Journal Entry, Dr 2210 / Cr the expense account the charge used,
  whose Reference Number names the charge; every text says to post **and submit** it. A
  **submitted** entry whose Reference Number resolves to a charge (any Journal Entry or Purchase
  Invoice the report knows, other than itself; an entry the KPI reads as a charge never corrects
  another) adds its 2210 debit to the charge's own, so the row reaches Done once corrected and an
  over-correction is flagged. **Naming a correcting entry names its charge** (third review): the
  chain is followed to the end, loops refused, so a reversal written against the first correction
  counts; tokens leading to two different charges count for neither and are listed, never
  first-token-wins. A **draft** correcting entry counts for nothing and is listed until it is
  submitted or deleted (the S-D loop would otherwise submit it beside a later one). The report never
  advises amending a QuickBooks entry: amending a QuickBooks-synced Journal Entry cancels the
  original, `tabQuickBooks Sync Mapping` stays on the cancelled one, and the pairing follows the
  mapping, so the charge would drop out of the list.
- **Links by Reference Number** (v1.538.0 second review). A Journal Entry whose Reference Number
  lists trip keys is those trips' charge: a key is a receipt's run id (`sr-…`), or the name of
  any receipt of the trip (the report's First Receipt, whether or not it has a run id); several may
  be listed, separated by commas, semicolons or spaces, each matched trimmed and ignoring case. A link **overrides** the
  automatic pairing for its trips and its charge: a linked trip's automatic charge goes back to
  unpaired, and a linked charge's automatic trip goes back to Waiting, its row asking whether the
  charge pays for it too. A linked charge's **target is the sum of its linked trips' stock lines**,
  so one draft for two runs of one purchase reaches Done on both rows at once; a linked draft whose
  link took another trip's automatic pair asks **first** whether it pays for that trip too. Links
  **resolve by key, not by date**: a linked trip outside From..To is not shown, but its charge is
  never listed as having no store run; the charge's figures are laid over the trips shown, so a row
  and the summary agree, and a linked charge in range whose trips are all outside it gets a row of
  its own when it needs action. A submitted entry's own Reference Number cannot be changed (v16), so a
  charge already submitted is linked by its correcting entry: Reference Number "*the charge* *run
  id*". A trip two charges link is flagged, naming the entry that carries each run id; a
  key-shaped token (`sr-…`, `MAT-PRE-…`) that names no recorded store run links nothing and is
  listed.
  Run ids (`sr-…`) and receipt names (`MAT-PRE-…`) never look like charge names (`ACC-JV-…`,
  `ACC-PINV-…`), so a token cannot mean both; one that names a charge makes its entry a correcting
  entry. This replaced recognizing a draft "found by hand" by a 2210 debit equal to one waiting
  trip's stock lines, which could not serve two runs of one purchase, lost a trip dated before
  From Date, and could give one trip's draft to another.
- **A charge that carries 2210 is never lost.** A charge dated in range that is neither paired nor
  linked but carries a net 2210 debit gets a *Needs action* row of its own: link it to its trip
  (unless that trip's row already shows another charge), or move it back to the expense; one a link
  took from its trip names that link and asks whether it is that trip's charge after all, and is
  listed under *Waiting* even when it carries nothing (the link may be the mistake). The summary's
  *On 2210 With No Store Run* totals them. A Purchase Invoice is the exception: v16 books its stock
  lines to 2210 itself until its receipt clears them, so it waits for its receipt (*Waiting*) and is
  never told to move them.
- **A charge never carries more than it paid** (fourth review). A charge whose trips' stock lines
  add up to more than its own amount (the pairing's 5-cent tolerance allowed) cannot pay for all of
  them: its rows are *Needs action*, nothing is ticked, and the text names the trips and what
  carries each run id; Accounting takes out the one that is not the charge's, or says the charge
  paid for it only in part. No amount is guessed.
- **`part-paid`** (fifth review) right after a trip key, in the Reference Number of a charge or of a
  submitted correcting entry of one, says the charge paid for that trip only in part (store credit,
  a second card): the trip leaves the capacity check, its 2210 target stays its whole stock lines,
  and the row reads Done once 2210 carries them. A row over capacity asks three ways, in this
  order: a checkout discount (correct the receipt's rates), `part-paid`, and only if it is neither,
  not the trip's charge -- a linked trip's run id comes out of what links it; an automatic pair,
  always a receipt-total pair (say a receipt total typed as an unrelated charge's amount), is
  overridden by putting the run id on the trip's own charge. Offered alone, "not this trip's charge"
  sent a coupon's own draft looking for itself; not offered at all, the typed total could never
  leave *Needs action*.
- **Every 2210 line is accounted for** (third review). Each backstop entry's net 2210 amount must
  belong to exactly one charge's figure -- the charge of a trip (shown, or hidden because its trips
  are outside the range), a submitted correcting entry of one, or a charge with no trip dated in
  range -- or it gets a row of its own saying why, and the summary's *2210 Not Accounted For* totals
  them (`store_run_matching.attribute_2210`; `test_store_run_matching` checks the conservation on
  generated histories). It must read $0.00 before the S-D loop.
- **`not-store-run`** in a Journal Entry's Reference Number takes the entry out of all of this (no
  link, no correction, not in the backstop) and stops a card charge carrying it from pairing with
  any trip, in the report only. **A QuickBooks entry marked so stays in the backstop while it still
  carries 2210** (fourth review), **unless its Reference Number also names the Purchase Receipt that
  amount clears**, one that is not a store run (fifth review: a card charge that paid for a PO receipt,
  its goods moved to 2210 to clear that receipt, was held in *Needs action* and told to move them
  back). Such an entry is left out whatever it carries; the receipt is looked up, and one that does
  not exist, is not submitted or is a store run is refused with the reason. An ERPNext entry marked
  so is left out whatever it carries. Beside a run id, a charge's name or `part-paid` it is a
  contradiction, and listed. A submitted QuickBooks charge marked so by mistake, whose Reference
  Number can never change, is linked anyway by a submitted correcting entry that names it beside run
  ids; every entry naming it then counts as its correction.

**What to Do** (`store_run_matching.build_rows`; *Moved to 2210* means equal to the cent; a charge
linked to several trips has one target, the sum of their stock lines, and one *What to Do* on each
of their rows):

| Row | What to Do | Show |
|---|---|---|
| Paired or linked draft, 2210 short of the stock lines | Move $X (more) of the goods debit to 2210, then save it; the S-D loop submits it | Needs action |
| Paired or linked charge already submitted, 2210 short | Post and submit one correcting Journal Entry for $X (Dr 2210 / Cr the expense account the charge used) with Reference Number = the charge followed by its trips' run ids (so a second run of the same purchase is added, not swapped) | Needs action |
| A charge whose trips' stock lines are more than the charge itself | A checkout discount (correct the receipt's rates), or a part payment (`part-paid` right after its run id: on a draft its own Reference Number, on a submitted charge the correcting entry that moves the rest), or, only if neither, not this charge's trip: linked, take its run id out of what links it (named); an automatic receipt-total pair, put the run id on the trip's own charge, which overrides the pair (a submitted one: a correcting entry naming it and the run id). Nothing ticked | Needs action |
| More on 2210 than the stock lines | Reduce it on the draft, or a correcting entry the other way (Reference Number = the charge and its trips). A draft over-moved by its correcting entries is told to reverse them, never to cut its own lines below the target | Needs action |
| A linked draft whose link took another trip's automatic pair | First, all three answers: *If this draft also pays for sr-a, add sr-a …; if it pays for sr-a and not for sr-b, put sr-a in its Reference Number in place of sr-b …; otherwise* reduce / move the amount for its own trips. An answer the charge cannot pay for is not offered | Needs action |
| Paired or linked draft, 2210 exact / no stock lines | *Goods debit on 2210: nothing to change; the S-D loop submits it* / *No stock lines: nothing to move; …* | Done |
| Paired or linked and submitted, 2210 exact (its correcting entries included) | Done (corrected by …) | Done |
| No charge, every receipt billed by a submitted Purchase Invoice | Billed from the receipts; when a link took the charge it was paired with, it asks whether that charge is its own (the purchase would be booked twice; fifth review) and waits | Done / Waiting |
| No charge, some receipts billed | Bill the rest | Needs action |
| Paired or linked **and** billed from the receipts | The invoice already books the purchase: if the charge is not this trip's, link it to its own trip or (nothing left on 2210) mark it `not-store-run`; if it is, a draft pays the invoice (its whole debit on the store's payable, its Reference Number `not-store-run` alone), and for a submitted charge the invoice is cancelled | Needs action |
| A trip two charges link | Names the entry carrying each run id; which to keep is Accounting's call; the other's run id comes out (a draft: edit; a submitted correcting entry: cancel) and its 2210 goes back to the expense | Needs action |
| No charge | *No card charge paired*: find its QuickBooks draft, move $X of its goods debit to 2210, put the trip's run id in its Reference Number and save it (already submitted: a correcting entry whose Reference Number is its name followed by the run id); if that charge is already another trip's in the list, list both run ids **only if it pays for both**, otherwise take the other trip's run id out of what links it, and that trip's row asks for its own charge. *Only if it has no card charge at all, bill it from the receipts after the cutover.* With no stock lines nothing moves, but a draft already another trip's charge in the list still gets this trip's run id (and loses the other's when it does not pay for it), so that trip's row asks (fifth review: "change nothing" left the other trip Done on this one's charge). A trip whose paired charge a link took for other trips is asked all three ways -- that charge pays for it as well, for it and not the linked ones, or not for it -- with or without stock lines, and only which one when the charge cannot pay for both | Waiting |
| A charge neither paired nor linked, net 2210 debit not zero | *Carries $X on 2210 but no recorded store run is paired or linked*: a draft names the receipt that amount clears beside `not-store-run`, or is linked to its trip (unless that trip's row shows another charge), or is moved back to the expense; a submitted one is moved back with a correcting entry, and its trip's row then moves it again, linked; one a link displaced names the link and gives both answers (it is that trip's charge: take the trip out of the other one's Reference Number; it is not: move it back) | Needs action |
| A Journal Entry a link took from its trip, carrying nothing on 2210 | *It was paired with sr-1 until … linked sr-1, and carries nothing on 2210*: if it is sr-1's charge, take sr-1 out of the other one's Reference Number; if not, a draft gets its own trip's run id or `not-store-run`, and a submitted one's own trip's row says how to link it | Waiting |
| A Purchase Invoice neither paired nor linked, carrying 2210 | *Waiting for its receipt*: leave its lines on 2210 (a displaced one too: either answer leaves a correct booking) | Waiting |
| A linked charge in range, all its trips outside it, needing action | *The store runs its Reference Number links (…) are dated outside this range.* then the charge's own advice | Needs action |
| A Journal Entry whose Reference Number names nothing usable (a dead key, two charges, not-store-run beside a key, a charge, `part-paid` or a receipt it cannot take -- each with the reason --, a Purchase Receipt that is no store run named without not-store-run, `part-paid` with no run id before it, a loop, a card charge naming another), or a draft correcting entry (listed even when dated after To Date, if its charge is accounted for here; it names the row of the charge's trip, or says the charge has none) | Says what is wrong and how to correct it (a draft: edit and save; a submitted ERPNext entry: cancel and amend; a submitted QuickBooks entry is never cancelled: *Waiting*, and with money on 2210 it is told to move it back with a correcting entry naming it, which counts for it) | Needs action |
| A Journal Entry on 2210 no charge accounts for | *Carries $X on 2210 that the report cannot tie to any store run or card charge*: an ERPNext entry names the charge (or the run id) it adjusts, or adds `not-store-run`; a QuickBooks entry (a card charge) names the receipt that amount clears beside `not-store-run`, or is linked to the trip it was moved for, or is moved back to the expense (submitted: moved back by a correcting entry, then posted again naming the receipt if it cleared one); a card charge in the lookback pairing with nothing: widen the range | Needs action |
| A QuickBooks entry marked `not-store-run` that still carries 2210 and names no receipt it clears | *Its Reference Number says not-store-run, but it still carries $X on 2210, and it names no Purchase Receipt that amount clears*: name the receipt, put the trip's run id in place of `not-store-run`, or move it back (a submitted one: a correcting entry naming it followed by `not-store-run`, then a Journal Entry naming the receipt if it cleared one) | Needs action |

The summary (store runs, matched, needs action, still to move to 2210, already moved, on 2210 with
no store run, **2210 not accounted for**) covers every row in range whatever *Show* is set to. The
procedure at step S-D is in MIGRATION_NOTES section 8.

**What the report cannot see** (fourth review): it compares no amounts beyond the KPI's own
pairing, so a wrong automatic pair consistent with every figure, and a link typed on the wrong trip
that contradicts no automatic pair and fits the charge, stay invisible. A simulator that follows
every row's text found no other false *Done* on the generated histories it ran (CHANGELOG 1.538.0,
Tests; the simulator is not in the repo). The limits Accounting settles by hand are in
MIGRATION_NOTES section 8.

**Known limit**: a standalone Purchase Invoice with *Update Stock* ticked pairs as a charge, but
its stock lines post to the warehouse account, not 2210, so the report reads nothing on 2210 for
it and its *What to Do* cannot be computed (the goods would be in stock twice, once from the
receipt and once from the invoice). Production has none (2026-09-25).

Roles: Accounts Manager, Accounts User, Purchase Manager, System Manager; `ref_doctype` Purchase Receipt, so v16 also requires report permission
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

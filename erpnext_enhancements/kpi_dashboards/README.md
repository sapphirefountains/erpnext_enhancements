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

`tests/test_kpi_departments.py` checks that the seven places a department is named agree.

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
failure cannot take the backlog figure or the Product department with it. No KPI Target row
ships for it: targets are site data.

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

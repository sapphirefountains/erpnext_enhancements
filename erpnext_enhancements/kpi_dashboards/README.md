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
`design_dashboard`, `product_dashboard`, `hr_dashboard`, `kpi_dashboards`.

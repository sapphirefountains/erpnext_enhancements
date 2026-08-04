# Marketing spend and value-stream reporting — runbook

WP-4, shipped in v1.243.0. What it does, how to load data, and how to turn it off.

Design rationale lives with the code: `kpi_dashboards/marketing_spend_import.py`
and the two report modules. This is the operational half.

---

## The state this was built against

| | |
|---|---|
| `Marketing Spend` rows | **0** |
| GA4 nightly pull | working, 40/40 days since 2026-06-26 |
| GSC nightly pull | **failing, 40/40 days**, HTTP 403 |
| Opportunities with a real source | ~0% (see the attribution runbook) |

So: there was no budget baseline, organic search figures had been zero for the
entire history of the dataset, and nothing said so.

---

## Loading spend

**Marketing Spend Rollup → Import Spend.** Paste CSV. Headings are matched
loosely — `Month`/`Period`/`Date`, `Channel`/`Source`/`Line Item`,
`Amount`/`Spend`/`Cost`. Optional: value stream, notes.

```csv
Month,Channel,Amount,Notes
2026-01,Google Ads,"$1,200.00",Q1 push
2026-01,Facebook Ads,800,
2026-02,Trade Show,4500,Booth + travel
```

**Preview runs first, always.** It reports how many rows are new, how many will be
updated, every channel name it normalised, and every line it could not read.
Spend is the denominator of every marketing figure in the business — nobody
should discover a misread column by looking at a dashboard next week.

Things worth knowing:

- **Re-importing is safe.** A month + channel pair is unique by the doctype's
  naming rule, so an existing row is **updated in place**, not duplicated. This is
  why the import is bespoke rather than frappe's Data Import, which would collide
  on every row of a corrected re-export.
- **Channel names are canonicalised.** "Google Ads", "google ads", "Google
  AdWords" and "AdWords" all become `Google Ads`. Every rename is listed in the
  preview — a wrong alias silently merges two budgets, so it is shown rather than
  assumed. The alias table is `CHANNEL_ALIASES`; an unknown channel passes through
  unchanged, because an unknown channel is a new channel and not an error.
- **Months snap to the 1st.** Otherwise every `GROUP BY month` produces one bucket
  per invoice date.
- **`(500)` is minus 500.** Accounting exports write credits that way.
- **An unreadable amount is refused, not zeroed.** A cell reading "n/a" or "TBC"
  is reported as a problem line rather than imported as $0 — a silent zero in the
  denominator is indistinguishable from a month where nothing was spent.
- **Batch label** is stamped on every row it writes, so a bad import can be found
  (`source_note`) and undone.

Import is restricted to System Manager / Sales Manager / Accounts Manager.

---

## Value stream allocation

`Marketing Spend.value_stream` is **optional and never apportioned**.

Fill it in where you genuinely know which stream a line was meant to drive. Leave
it blank where a channel serves several — the Value Stream Performance dashboard
reports that as an **Unallocated** line rather than dividing it across streams. A
made-up split is worse than an honest gap, because it looks like data.

When more than half of spend is unallocated the dashboard says so at the top,
because per-stream cost figures then cover only a minority of the budget.

---

## The weekly meeting view

**Value Stream Performance.** No parameters needed — it defaults to a rolling
twelve months and the filters are refinements. Spend, opportunity count, won,
lost, win rate, revenue, average deal and cost per opportunity, per stream.

Read it knowing three things:

1. **Columns do not sum to the company total.** An opportunity tagged with two
   value streams counts once toward **each**. That is the right reading for "how
   is Events doing" and the wrong one for "what is our total pipeline".
2. **Win rate is won / (won + lost)**, not won / all. Open deals have not lost;
   counting them makes every rate look terrible and move whenever somebody adds a
   lead. Note that `Closed` (144 opportunities on this site) is neither won nor
   lost and is excluded from both.
3. **Revenue prefers the billed figure** from the linked Project, falling back to
   the quoted opportunity amount where no Project is linked. The report says how
   many rows it did that for — work the *Closed Won Reconciliation* report to
   shrink that number.

It is a native ERPNext Script Report, not a Triton view, as required.

---

## Data source failures now surface

The nightly pull used to write its failure into `Marketing Web Snapshot.pull_error`
and stop there. That is how GSC failed **40 nights running** without anybody
hearing about it.

Now a **Notification Log** goes to System Managers when a source **changes state**:
once when it starts failing, once when it recovers. Deliberately on transitions
only — a nightly "GSC is still broken" alert is muted within a week, and then the
next real outage is invisible too.

A Notification Log rather than email: it needs no outbound mail configuration and
cannot be filtered into a folder nobody reads.

### Fixing the GSC 403

Not fixable from this repo. The service account used by `GA4 Settings` needs
access to the Search Console property, and the property form must match — a
`sc-domain:` property cannot be queried as a URL prefix. Someone with Google Cloud
console and Search Console admin needs ten minutes. Until then organic clicks and
impressions read zero, and that zero is not real.

---

## Channel breakdown

The snapshot now stores GA4's `sessionDefaultChannelGroup` split in a `channels`
child table. The pull had been fetching it all along and discarding it every
night, so this is retained data rather than a new API call — no extra quota cost.

GA4's own grouping is stored verbatim rather than mapped onto a local taxonomy, so
the numbers reconcile against the GA4 UI when somebody checks them.

---

## Turning it off

There is nothing to turn off. The reports are read-only, the importer is a manual
action, and the alerting only fires on a state change. The one behavioural change
is the `Marketing Spend.validate` normalisation, which cannot be disabled — if a
channel name must be preserved exactly as typed, add it to `CHANNEL_ALIASES` as
its own canonical entry.

The nightly snapshot itself is gated by `kpi_dashboards_enabled` in ERPNext
Enhancements Settings, as before.

---

## Four marketing KPIs were silently wrong before this release

Recorded here because the class of bug is worth recognising. `Lead.source` and
`Opportunity.source` have had **no DocField** since erpnext v15 renamed them to
`utm_source`. frappe never drops columns, so:

- `select ... where coalesce(source,'') = ''` ran without error;
- it returned a plausible number;
- nothing has written to that column since the rename.

So "Unsourced Leads", "Unsourced Opportunities", "Sourced Pipeline Value" and
"Sourced Wins (30d)" were all measuring a frozen pre-2023 snapshot. Sourced
Pipeline could only ever fall and Unsourced could only ever rise, regardless of
what anybody actually did about attribution. All four now read
`custom_lead_source`, and treat the `Unknown (pre-Aug 2026)` bucket as **not**
sourced — it is a recorded gap, not a channel.

A test in `tests/test_marketing_spend.py` parses the AST of `snapshots.py` and
fails if any query string references that column again.

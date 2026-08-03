# WI-007 / WI-008 — The order-to-cash chain, and getting revenue onto jobs

Today the sell side runs outside ERPNext: quote in QuickBooks, project in ERPNext, re-key, invoice in QuickBooks. From cutover the whole chain is authored here, and the thing that makes job profitability work is that **`project` rides the chain natively** — Quotation → Sales Order → Sales Invoice — instead of being typed in three times.

> **Status (2026-08-03).** The settings half of WI-007 was already correct on production and is now verified and version-controlled below. WI-008 ships the two things that make a missing project *visible*. The per-stream SOPs are here. What remains is the TEST walkthrough, which happens inside the December parallel run (WI-022).

## Settings — verified, not changed

| Setting | Value | Why it must stay that way |
|---|---|---|
| `Selling Settings.so_required` | **No** | The maintenance module drafts Sales Invoices with no Sales Order at all (`api/maintenance_workflow.py`), and January's opening-AR invoices have no order behind them either. Setting this to Yes would block both. |
| `Selling Settings.dn_required` | **No** | Stock fulfilment is not in use — 0 Delivery Notes, 0 Stock Entries on production. |

**"Sales Order required" is therefore a procedure, not a system rule.** That is deliberate and it is the one thing to understand about this design: nothing stops someone invoicing without an order. What catches it is the UAT sample during the parallel run and the review habit below.

## The chain, per value stream

Production is a clean slate for this: **0 Sales Orders and 0 Delivery Notes ever created**, so there is no legacy to reconcile — only a habit to establish.

| Stream | Quotation | Sales Order | Sales Invoice |
|---|---|---|---|
| **Design** | Yes — the proposal | Yes, on acceptance | From the SO |
| **Build** | Yes | **Yes — this is the commercial commitment** | From the SO, usually progress-billed |
| **Service** | Usually not | Optional | Drafted per visit by the maintenance module |
| **Events** | Yes | Yes | From the SO |
| **Products** | Optional | Yes for anything non-trivial | From the SO |

### Standard flow

1. **Quotation** — the AE quotes in ERPNext. On Closed Won the existing handoff engine creates the **Project** (that engine already exists and is unchanged by this work item).
2. **Sales Order** — created from the Quotation with `Create → Sales Order`. Set **`project`** to the handoff project, and `order_type` to `Sales` — or `Maintenance` for the maintenance-anchor order, which the maintenance record controller reads.
3. **Sales Invoice** — created from the Sales Order with `Create → Sales Invoice`. **`project` carries across natively; do not re-key it.**

### The two paths that skip the Sales Order, legitimately

- **Maintenance visits.** The Sapphire Maintenance module drafts one Sales Invoice per visit and sets `invoice.project` itself. No order, and none wanted.
- **Opening AR at cutover.** January's opening invoices are historical balances, not orders (WI-033).

Everything else that arrives without an order is worth a question.

## WI-008 — making a missing project visible

Native `Sales Invoice.project` is sufficient; **no code was written for this**, and that was the explicit decision. The gap is only the hand-keyed invoice where someone forgets, and that is closed by making it visible rather than by validation.

Two things ship:

1. **`project` now shows as a column on the Sales Invoice list** (`Sales Invoice-project-in_list_view` Property Setter). A blank cell in a list is noticed; a blank field inside a form is not.
2. **An "Invoices without Project" tile on the Finance Hub** — the accountant's workspace from WI-018. It opens the Sales Invoice list filtered to `project is not set` and `docstatus != 2`.

The second one deliberately is *not* a personal saved filter. A saved filter lives in one browser, belongs to one user, and is invisible to everybody else — including whoever picks the job up next. As a workspace tile it is version-controlled, it is on the page the accountant already works from, and it survives her being on holiday.

### The rule

> **Any invoice for job work must name the Project.** Revenue that genuinely is not job work — bank interest, a rebate, a one-off sale — may either use one of the standing **Overhead** projects or be left blank, but leaving it blank is a decision, not an oversight.

Review the tile weekly. During the parallel run it is a UAT check; afterwards it is part of the close.

### Deliberately not built

A server-side validation that warns or blocks on submit when the customer has an active project and `project` is empty. That stays a documented Phase-2 option, to be built **only if the parallel run shows persistent misses**. Writing it now would be guessing that people will get it wrong.

## Acceptance

- [x] `so_required` = `No` and `dn_required` = `No` on production — verified 2026-08-03.
- [x] `Sales Invoice.project` appears in the list view (Property Setter shipped).
- [x] "Invoices without Project" tile present on the Finance Hub.
- [ ] **On TEST, during WI-022:** one Quotation → Sales Order (with `project`) → Sales Invoice, and the invoice's `project` equals the order's, with nobody having typed it twice.
- [ ] **During the parallel run:** the tile returns zero job-type invoices, reviewed weekly.
- [ ] Native **Profitability Analysis**, filtered by Project, returns revenue rows for the UAT project. (Both it and **Gross Profit** are present and enabled — confirmed.)

## Where this sits today

| | Count |
|---|---|
| Sales Orders ever created | **0** |
| Delivery Notes ever created | **0** |
| Quotations (all draft QBO estimates — WI-023 disposes of these) | 656 |
| Sales Invoices | 1,593 — **1,303 carry a project, 290 do not** |

Those 290 are imported drafts, not a live problem: nothing is submitted, and the backfill decision belongs with opening balances (WI-033). They are the baseline the tile is measured against, not a task list.

## Not in scope

Disposition of the 656 legacy draft Quotations (WI-023) · print formats (WI-020) · tax templates and rules (WI-036 / WI-037) · Stripe payment links (WI-039) · stock and delivery flows · backfilling project on the 290 legacy drafts (WI-033).

One deviation from the work item worth noting: it assigned the `Sales Invoice.project` Property Setter to WI-019's fixture batch. WI-019 is blocked behind the WI-018 accountant walkthrough, and this single setter is independently useful and independently reversible, so it shipped here. The **section move** — relocating `project` higher on the form — stays with WI-019, where the layout generator lives.

# Estimating

Sapphire's estimating tool: an **Estimate** is priced lines grouped by phase, built quickly from a
job-kind template, and it issues a native draft **Quotation**. This is milestone 1 of the full
concept Nik chose at the 2026-11-19 Weekly Operations Review (Estimate → schedule of values → pay
applications with percent-complete billing). The plan is the ERPNext task tree **TASK-2026-02056**;
the decisions it follows are TASK-2026-02149 (D15–D18, the Estimator role and the cost wall), the
2026-11-04 concept page and the v2 mockups (keep it simple, no toggle wall, every screen usable on a
phone).

Milestone 2 adds the **Schedule of Values** and the **Pay Application** (G702/G703, retainage stepping
down per D16, change orders), below. The Sales Invoice with retainage receivable, the G702/G703 print
and portal export, retainage release and percent-complete accounting come in later releases.

## How a price is made

One engine, [`estimate_math.py`](estimate_math.py) (no frappe; its docstring has the model and the
reasons), with a line-for-line browser twin in
[`public/js/estimating/estimate_math.js`](../public/js/estimating/estimate_math.js). Both are held to
the same cases in [`tests/data/estimate_math_cases.json`](../tests/data/estimate_math_cases.json).

```
cost          = qty x unit cost  (+ use tax on material and equipment)
cost of work  = cost + markup by cost type
price         = cost of work + contingency + insurance + fee   (each a % of the cost of work)
commission    = price x commission %                            (internal: out of the margin)
```

Every rate resolves **line → phase → parent phase → estimate** (D15). The estimate's rates are copied
from the **Estimate Rate Card** when it is made, so a later edit to the card never reprices a quote
already sent. A blank override inherits; `0` is a real rate.

## Phases

Phases are the integers `Task.custom_phase_number` already carries. A design-build job's design
stages are **110 Predesign, 120 Schematic design, 130 Design development, 140 Construction
documents**, nested inside **100 Design and planning**; construction is 200 to 700. A job with no
phases (an event) leaves every line at phase 0, and each line is its own line on the quotation.

## Who sees what

Cost, markup, margin, commission and every rate are at **permlevel 1**, readable only by
**Estimator, Finance Team, Executive Team and System Manager** (TASK-2026-02149). In milestone 1 those
four roles are also the only ones that can open an Estimate, a template or a rate card at all. The
Quotation carries one price per phase and never a cost.

**The Estimator role is meant to be paired with a selling role** (Sales User or Sales Manager). On its
own it grants nothing outside this module: issuing a Quotation needs Quotation create, and the form's
Account, Opportunity and Project pickers need read on those doctypes.

## File map

| File | Purpose | Wiring |
|---|---|---|
| `estimate_math.py` | The pricing engine: `price_line`, `price_estimate`, `backfill_unit_cost` (cost from a sheet price, Nik 2026-11-06), banker's rounding, D15 rate resolution. Pure | the Estimate controller; twin in `public/js/estimating/estimate_math.js` |
| `rules.py` | Cost codes checked against `quality/cost_codes.py` (FEE and `01 2116` refused: computed), default phase labels, missing phase rows, and the Quotation's rows (one per phase, allowances named, "by others" as exclusions). Pure | the controller, `api/estimating.py` |
| `doctype/estimate/` | **Estimate** (`EST-.YY.-.####`): header, rates, phases, lines, totals; every save prices it again. Status Draft → Issued → Superseded (a superseded revision is read-only). `estimate.js` mounts the lines editor | form; `api/estimating.py` |
| `doctype/estimate_line/`, `doctype/estimate_phase/` | Child tables: a priced line (inputs, overrides, every computed amount) and a phase (label, customer-facing scope, overrides, totals) | Estimate |
| `doctype/estimate_rate_card/` | Markup by cost type, contingency, insurance, fee, commission, use tax, labor cost per hour. One default | copied onto new Estimates |
| `doctype/estimate_template/` (+ `_phase`, `_line`) | A job kind's phases and a checklist of candidate lines; applying it shows the checklist with the ticked lines pre-selected (concept card 10) | the form's Apply Template |
| `data/estimate_templates.json` | The four seeded templates (Design-Build, Build, Event, Custom Panel). No prices | `patches/seed_estimate_templates.py` |
| `seed.py` | What the seed patches create (EST-SCOPE, its name, its zero price) and `run`, the never-raise wrapper they share | `patches/seed_estimate_*`, `api/estimating.py` |
| `links.py` | `on_trash` for both sides of the Estimate ↔ Quotation link, so either can be deleted first | Quotation `doc_events`; `Estimate.on_trash` |
| `../api/estimating.py` | `issue_quotation` (POST), `new_revision` (POST), `cost_codes` (GET) | the form and the editor |
| `../public/js/estimating/editor.js` | The lines editor (the v2 "Estimate2" board): phases that fold, line rows that become cards under 720px, back-fill from price, totals and the "ready to accept" checks, sticky Save / Issue Quotation | `estimate_editor.bundle.js` + `.css`, loaded by `estimate.js` |
| `../public/js/estimating/opportunity_estimate.js` | Create > Estimate and View > Estimates on an Opportunity, for the roles that may | `doctype_js` Opportunity |

## Seeded on migrate

| Patch | What |
|---|---|
| `seed_estimator_role` (pre) | The **Estimator** role. Granted to nobody |
| `seed_estimate_rate_card` | One empty default **Standard** rate card, every rate 0 (the repo is public; Finance types the rates). Skipped when any card exists |
| `seed_estimate_templates` | The four templates, insert-only by name |
| `seed_estimate_scope_item` | Item **EST-SCOPE** (`SERVICE, ESTIMATE SCOPE`, non-stock, sales only, unit Unit) that every Quotation line uses, and a **0 Item Price** for it on the default selling price list |

Plus the fixture Custom Field `Quotation.custom_estimate` (read-only Link back to the Estimate).

**No seed step can abort the migrate** ([`seed.py`](seed.py) `run`): a failure is rolled back and logged
as one "Estimating seed: <step>" Error Log that says what to do by hand.

## Careful about

- **The deal link is `Quotation.custom_source_opportunity`, never `Quotation.opportunity`**, which
  drives and freezes the Opportunity's own status; the product configurator made the same choice.
- **No sales tax on a build quotation**: use tax is a cost inside the line prices (concept card 14).
  Only a Product job takes the company's default tax template.
- **A draft Quotation is refreshed in place; a submitted or cancelled one is never touched**, so
  issuing again after a change makes a new draft.
- **The editor never redraws under your finger.** Edits update numbers in place; a redraw waits a tick
  and restores focus, and the form script's table-view handlers stand by while the editor writes.
- **A selling Pricing Rule never reprices an estimate's quotation** (`ignore_pricing_rule = 1`), and a
  "valid until" already in the past is dropped rather than refused by ERPNext. A new revision is dated
  today and takes over the old revision's draft quotation, so issuing refreshes it instead of leaving
  a second draft behind.
- **Taxes can come back on a later Desk save of the quotation** if a Tax Rule or "Add Taxes from Taxes
  and Charges Template" is ever turned on (both off on prod, 2026-11-19): `taxes_and_charges` is
  refilled from the customer in validate and the flag that stops it is not saved.
- **EST-SCOPE keeps a 0 list price.** Prod auto-inserts a missing Item Price from the first
  transaction's rate, which would make the first phase price ever issued the item's list price. The
  seed puts a 0 price on the default list and Issue Quotation adds one on any other list before the
  insert; a 0 list price never overrides a line's own rate. The rows get their rate only after
  `set_missing_values`: ERPNext's `insert_item_price` runs there and skips only a row with no rate.
- **An Estimate and its Quotation Link each other**, so `delete_doc` would refuse both. The `on_trash`
  hooks in `links.py` clear the pointing field first (`on_trash` runs before the link check), so either
  can go first.
- **Issue and New Revision check the form is clean after saving.** `frm.save()` at v16 resolves even
  when the server refused the save, so "it resolved" proves nothing.
- **Line `amount`, `unit_price` and `sheet_price`, and Phase `amount`, are permlevel 0.** Fine while
  only the four cost roles can open an Estimate. The 2026-09-25 rule (labor hours shown without price)
  needs permlevel work on them before sales or project roles are given read access.
- **The form script checks the bundle's API level** (`REQUIRED_EDITOR_LEVEL` = bundle `VERSION`), so a
  tab open across a deploy says "reload" instead of throwing. Bump both together.

## Schedule of Values and Pay Application (milestone 2)

**Schedule of Values** (`SOV-.YY.-.####`): the contract broken into the lines the customer signed.
Typed by hand, or filled once from an Estimate (Create > Schedule of Values: one row per phase and cost
type, or one per line). **It carries no cost and no markup field at all**: it goes to a general
contractor, and a field that exists is one export away from leaving the building. It locks when its
lines equal the contract sum; once locked only change-order rows can join, and it unlocks only before
anything is certified. Each row has a `line_key` minted once (`quality.stable_keys`).

**Pay Application** (`PAY-.YY.-.####`, submittable): one period's billing. Start Pay Application on a
locked schedule; the project manager types **this period's percent** per line on site and sends it to
accounting; accounting checks the amount due and **certifies** (submits). Milestone billing bills a
milestone's percent of every line.

The arithmetic is [`pay_app_math.py`](pay_app_math.py) (no frappe; its docstring has the model), with
a browser twin [`public/js/estimating/pay_app_math.js`](../public/js/estimating/pay_app_math.js) held
to [`tests/data/pay_app_math_cases.json`](../tests/data/pay_app_math_cases.json):

```
D from previous      = earlier applications' WORK completed   (not their G: stored is a balance)
E this period        = C x this period's %                     (the percent left closes the line)
G completed + stored = D + E + F            retainage = (D + E) x work % + F x stored %
G702 line 7          = what earlier applications were CERTIFIED for (a short one carries forward)
```

### Careful about

- **Column D is work completed, never column G.** Carrying G forward counts stored materials twice;
  the old workbook does.
- **Retainage steps by the percent complete at the start of the period**, and the rate used is stored
  per line, so a certified application never re-rates.
- **The chain is a stored link** (`previous_application`), never inferred from dates, and lines chain
  by `line_key`, so a reordered schedule never repoints a billed line.
- **A change order joins from the application whose period reaches its approval date**, and counts
  as "approved this month" when it is new on this application, not by its date.
- **A certified application stores every number** and is never re-synced from the schedule.
- **Certify refuses with every reason at once** (`pay_app_math.refusals`); only the latest application
  can be canceled; amending is refused.
- **The Pay Application editor shares the estimate editor's stylesheet** (`estimate_editor.bundle.css`)
  and adds `pay_app_editor.bundle.css`; its bundle level (`REQUIRED_PAY_EDITOR_LEVEL`) equals the bundle's
  `VERSION`, checked in CI.

| File | Purpose | Wiring |
|---|---|---|
| `pay_app_math.py` | `sov_rows` (the projection, an exact partition), `price_application` (G703 + G702), `resolve_rates`, `refusals`. Pure | both controllers, `api/estimating.py`; twin in `public/js/estimating/pay_app_math.js` |
| `doctype/schedule_of_values/` (+ `_line`, `retainage_step`) | The schedule; lock and change-order rules; reuses `Contract Milestone` for milestones | form; `api/estimating.py` |
| `doctype/pay_application/` (+ `_line`) | The chain, the line sync from the schedule, pricing, certify refusals, the latest-only cancel | form; `api/estimating.py` |
| `links.py` `guard_change_order_cancel` | A change order billed on a certified application cannot be canceled | Change Order `doc_events` |
| `../public/js/estimating/pay_app_editor.js` | The PM's editor: phases fold, percent per line, REST, past-scheduled highlight, Amount Due panel, Send to Accounting / Certify | `pay_app_editor.bundle.js` + `.css`, loaded by `pay_application.js` |

## Tests (bench-free, in CI)

`tests/test_estimate_math.py`, `tests/test_estimating_rules.py`, `tests/test_estimate_controller.py`
(frappe stub), `tests/test_estimating_api.py` (Issue Quotation, seeds, delete links; frappe stub) and
`tests/test_pay_app_math.py`, `tests/test_billing_controllers.py` (frappe stub) and
`scripts/test_estimate_math.mjs` (node: both twins, both bundle levels, escaping).

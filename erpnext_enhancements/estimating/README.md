# Estimating

Sapphire's estimating tool: an **Estimate** is priced lines grouped by phase, built quickly from a
job-kind template, and it issues a native draft **Quotation**. This is milestone 1 of the full
concept Nik chose at the 2026-11-19 Weekly Operations Review (Estimate → schedule of values → pay
applications with percent-complete billing). The plan is the ERPNext task tree **TASK-2026-02056**;
the decisions it follows are TASK-2026-02149 (D15–D18, the Estimator role and the cost wall), the
2026-11-04 concept page and the v2 mockups (keep it simple, no toggle wall, every screen usable on a
phone).

The schedule of values, pay applications (G702/G703, retainage stepping down per D16),
percent-complete accounting and the print and export come in later releases, built on these lines.

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
| `../api/estimating.py` | `issue_quotation` (POST), `new_revision` (POST), `cost_codes` (GET) | the form and the editor |
| `../public/js/estimating/editor.js` | The lines editor (the v2 "Estimate2" board): phases that fold, line rows that become cards under 720px, back-fill from price, totals and the "ready to accept" checks, sticky Save / Issue Quotation | `estimate_editor.bundle.js` + `.css`, loaded by `estimate.js` |
| `../public/js/estimating/opportunity_estimate.js` | Create > Estimate and View > Estimates on an Opportunity, for the roles that may | `doctype_js` Opportunity |

## Seeded on migrate

| Patch | What |
|---|---|
| `seed_estimator_role` (pre) | The **Estimator** role. Granted to nobody |
| `seed_estimate_rate_card` | One empty default **Standard** rate card, every rate 0 (the repo is public; Finance types the rates). Skipped when any card exists |
| `seed_estimate_templates` | The four templates, insert-only by name |
| `seed_estimate_scope_item` | Item **EST-SCOPE** (non-stock, sales only) that every Quotation line uses |

Plus the fixture Custom Field `Quotation.custom_estimate` (read-only Link back to the Estimate).

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
- **The form script checks the bundle's API level** (`REQUIRED_EDITOR_LEVEL` = bundle `VERSION`), so a
  tab open across a deploy says "reload" instead of throwing. Bump both together.

## Tests (bench-free, in CI)

`tests/test_estimate_math.py`, `tests/test_estimating_rules.py`, `tests/test_estimate_controller.py`
(frappe stub) and `scripts/test_estimate_math.mjs` (node).

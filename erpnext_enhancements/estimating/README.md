# Estimating

Sapphire's estimating tool: an **Estimate** is priced lines grouped by phase, built quickly from a
job-kind template, and it issues a native draft **Quotation**. This is milestone 1 of the full
concept Nik chose at the 2026-11-19 Weekly Operations Review (Estimate → schedule of values → pay
applications with percent-complete billing). The plan is the ERPNext task tree **TASK-2026-02056**;
the decisions it follows are TASK-2026-02149 (D15–D18, the Estimator role and the cost wall), the
2026-11-04 concept page and the v2 mockups (keep it simple, no toggle wall, every screen usable on a
phone).

Milestone 2 adds the **Schedule of Values** and the **Pay Application** (G702/G703, retainage stepping
down per D16, change orders), below; B9 adds **retainage release and the final application**. The
Sales Invoice with retainage receivable, the G702/G703 print and portal export and percent-complete
accounting come in later releases. Three progress-billing reports sit on the Project Enhancements
workspace, below.

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
G completed + stored = D + E + F            retainage = held before + E x work % now - released
                                                         + F x stored %
G702 line 7          = what earlier applications were CERTIFIED for (a short one carries forward)
```

### Releasing retainage and the final application (B9)

Retainage is held line by line and only comes back when someone releases it. Each application has a
type:

- **Progress**: the monthly one. Accounting may release retainage on it too, as a general contractor
  often does on finished portions of a job.
- **Retainage Release**: releases retainage and bills no new work (its percents are locked).
- **Final**: every line finished, nothing stored, every customer-approved change order of the project
  on it (one never added to the schedule included), certified for the whole payment due. It releases
  all retainage still held, whatever was typed, and **certifying it closes the schedule of values**: no
  further application or change order can join. Canceling the final application reopens it. Its
  computed release is cleared if the type moves off Final.

On a milestone job only a Progress application has to pick a milestone; a Retainage Release keeps
each line's stored-material balance from the previous application.

A release is a per-line amount, `retainage_release`, at **permlevel 1** (accounting's). **RELEASE
RETAINAGE** in the editor fills it from a percent of what each line holds, on every line or only the
finished ones (`pay_app_math.release_amounts`, banker's-rounded per line, so 100% releases every
cent). Anything from nothing to all that is held is accepted; more is refused at certify, never
clipped. Each line carries what was released before and to date, and the schedule of values shows
the **retainage held** and **released to date** from its latest certified application.

Start Pay Application, Start Retainage Release and Start Final Application are on the locked
schedule. The release does not raise an invoice yet: the release Sales Invoice that clears Retainage
Receivable is B8's, and sales tax is unchanged until the CPA confirms WI-036.

### Careful about

- **Column D is work completed, never column G.** Carrying G forward counts stored materials twice;
  the old workbook does.
- **Retainage steps by the percent complete at the start of the period, and keeps holding** (Nik,
  2026-11-19, "Keep holding it"). When 10% steps down to 5% at 50% complete, work billed before the
  step stays held at 10% until final release and only later work is held at 5%. Each line carries what
  the earlier applications held (`retainage_previous`) and adds this period's work at the rate in force;
  a step-down or an override never releases earlier retainage. Only a release does (B9, above).
- **The chain is a stored link** (`previous_application`), never inferred from dates, and lines chain
  by `line_key`, so a reordered schedule never repoints a billed line. **It is derived again on every
  save**, because read_only is not enforced on a save at v16.56: a blanked link would bill the job twice.
- **Status, change-order rows and retainage are the server's.** A schedule's status moves only through
  Lock / Unlock; a change-order row always equals its Change Order and never loses or gains one;
  retainage terms freeze at lock. Overrides and the amount certified are permlevel 1, and are cleared
  on a new application for a user without level-1 access.
- **Only a release releases retainage.** With keep-holding, an override of 0 holds nothing new but
  releases nothing, and held retainage otherwise only comes down with a line's work (capped at the work
  left). A release is its own input, and a Final application releases everything.
- **A closed schedule takes nothing new**, change orders included, and starts no application. The
  status is written by the final application's certify and cancel, never by a save.
- **Milestone billing puts the milestone's percent on base lines only**; change-order rows bill by
  their own typed percent, so approved scope never becomes unbillable.
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
| `pay_app_math.py` | `sov_rows` (the projection, an exact partition), `price_application` (G703 + G702, releases), `release_amounts`, `resolve_rates`, `refusals`. Pure | both controllers, `api/estimating.py`; twin in `public/js/estimating/pay_app_math.js` |
| `doctype/schedule_of_values/` (+ `_line`, `retainage_step`) | The schedule; lock and change-order rules; reuses `Contract Milestone` for milestones | form; `api/estimating.py` |
| `doctype/pay_application/` (+ `_line`) | The chain, the line sync from the schedule, pricing, certify refusals, the latest-only cancel; a certified Final closes the schedule and its cancel reopens it | form; `api/estimating.py` |
| `links.py` `guard_change_order_cancel`, `drop_canceled_change_order` | A change order on a certified application cannot be canceled; one canceled earlier leaves every schedule | Change Order `doc_events` (`before_cancel`, `on_cancel`) |
| `../public/js/estimating/pay_app_editor.js` | The PM's editor: phases fold, percent per line, REST, past-scheduled highlight, Amount Due panel, Send to Accounting / Certify, and accounting's Release Retainage dialog | `pay_app_editor.bundle.js` + `.css`, loaded by `pay_application.js` |

### Progress-billing reports (B9 follow-up)

Three Script Reports, on the **Progress Billing** card of the Project Enhancements workspace with the
Schedule of Values and the Pay Application:

| Report | Rows | Roles (each may report on the reference doctype) |
|---|---|---|
| **Retainage Held** (ref Schedule of Values) | One per schedule holding retainage (or every one): held, released to date, held as a percent of billed, its latest certified application | the schedule's readers who also read Pay Application: System Manager, Projects Manager, Project Manager, Finance Team, Executive Team, Accounts Manager, Accounts User |
| **Pay Applications Outstanding** (ref Pay Application) | Applications still open (Draft, To Review) with days since their period ended; locked jobs whose last certified period ended more than *Due after* days ago (default 30), or that never had one (aged from the day they were locked). A closed schedule never shows, and a job billed by milestones is never "Due" by the calendar | the Pay Application roles above |
| **Billed vs Scheduled** (ref Schedule of Values) | With a schedule picked, its lines by top phase (change orders last; a line by others counts nowhere) or one per line; without, one per locked or closed schedule (drafts on request) | the Schedule of Values roles, Estimator included |

The arithmetic is [`billing_reports.py`](billing_reports.py) (no frappe), in whole cents. The reports
read through `frappe.get_list`, so a person sees only the jobs they may read, and **read permlevel 0
fields only**: a Script Report runs server-side, where the field-level read checks (1.835.0) do not
reach, so `tests/test_billing_reports.py` holds every field they read to permlevel 0 (the amount
certified, permlevel 1, is left out of Outstanding: it shows the payment due). None has a print
format. **Total rows are the reports' own**, computed from cents: frappe's total row averages a
Percent column (`report_column_total` and `query_report.add_total_row` at v16.60.2), so it is off.

**They read live figures.** There is no as-of or month-end view: each run shows the schedules and
applications as they stand, and the retainage figures from each schedule's latest certified
application. There is no company or date-range filter yet.

## Tests (bench-free, in CI)

`tests/test_estimate_math.py`, `tests/test_estimating_rules.py`, `tests/test_estimate_controller.py`
(frappe stub), `tests/test_estimating_api.py` (Issue Quotation, seeds, delete links; frappe stub) and
`tests/test_pay_app_math.py`, `tests/test_billing_controllers.py` (frappe stub),
`tests/test_billing_reports.py` (the three reports, their roles and fields) and
`scripts/test_estimate_math.mjs` (node: both twins, both bundle levels, escaping).

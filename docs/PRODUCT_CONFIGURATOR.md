# Product Configurator

A generic configure-to-order tool: a **Configurable Product** defines a
product's option modules, component parts and build-instruction templates; a
**Product Configuration** picks the options and gets a part number, a live
priced quote, a parts list, config-aware build instructions — and, on demand,
the native ERPNext records (Item, submitted default BOM, Standard Selling Item
Price).

First product: **PDT-0040 STILLWATER E-Stop** (emergency-stop panel for spas,
fountains, pools and other water features), transcribed from three source
documents: *PDT-0040 Stillwater Pricing Calculator.xlsx*, *PDT-0040 STILLWATER
E-Stop BOM.xlsx*, and *Build instructions for PDT-0040 STILLWATER E-Stop.docx*.

## Module map

```
erpnext_enhancements/product_configurator/
    engine/            pure pricing/part-number/steps engine (stdlib only, no frappe)
    seed_data.py       PDT-0040 definition (shared by patch, tests, dev checks)
    doctype/           Configurable Product (+3 child tables)
                       Product Configuration (+4 child tables)
    api/configurator.py    whitelisted endpoints (options, live preview, generate)
    erp_integration.py     atomic Item/BOM/Item Price generation
    setup.py               Item provenance custom field (after_migrate)
    setup_print_formats.py Build Instructions / QC Checklist / Pricing Summary
    dev_checks.py          bench-execute verification harness
    workspace/             "Product Configurator" desk workspace
erpnext_enhancements/tests/test_product_configurator_engine.py   bench-free goldens
erpnext_enhancements/patches/seed_product_engineer_role.py
erpnext_enhancements/patches/seed_pdt_0040_product.py
```

## The data model in one paragraph

An option row is one costed module. A *Choice* group (E-Stop Mounting) is N
rows sharing an `option_key`, each with its own `choice_code` (the part-number
digit) and costs; a *Quantity* option is one row costed per unit; the *Base*
row is always included once. `module_key` joins an option row to its component
rows (which drive the BOM) and its build steps. `qty_multiplier_option` lets a
module's cost scale by another option's quantity — mounting scales with e-stop
count (each e-stop needs its own mounting). `flat_labor_cost` overrides
`labor_hours × labor_rate` (Surface mounting's flat $175). Build-step
templates carry a restricted-expression `condition` (`timer_qty == 2`,
`mounting == "3"`) and `{expr}` placeholders in the text
(`{estop_qty + timer_qty}`); the evaluator is a ~50-line AST whitelist — no
calls, attributes or subscripts, so product editors can't script the server.

## PDT-0040 ground truth (from the source documents)

### Part number

`PDT-0040-{mounting}-{estop_qty}-{timer_qty}-{contactor_qty}-{relay_qty}`

| Digit | Option | Range |
|---|---|---|
| 1st | E-Stop Mounting | 1 = Flush, 2 = Surface, 3 = Pedestal |
| 2nd | E-Stop Button Qty | 1–2 |
| 3rd | Timer & Button Qty | 0–3 |
| 4th | Pump Contactor Qty | 0–3 (3 ⇒ enclosure rotated 90°) |
| 5th | Auxiliary Relay Qty | 0–3 |

Most common: `PDT-0040-1-1-1-2-0`.

**Deliberate divergence:** the workbook's configuration-number formula
multiplies the mounting digit by the e-stop quantity (Flush + 2 e-stops would
read "2" = Surface). The docx decode table is authoritative: the digit is the
raw choice code. Only the mounting *cost* scales with e-stop quantity. A
regression test locks this in.

### Pricing (labor $85/hr, markup 30%)

| Module | Parts $ | Labor | ×1.3 total | Scales by |
|---|---|---|---|---|
| Base "Housing & Label" | 146.50 | 1.0 h | 300.95 | — |
| Mounting: Flush | 30.67 | 1.5 h | 205.621 | e-stop qty |
| Mounting: Surface | 50.00 | flat $175 | 292.50 | e-stop qty |
| Mounting: Pedestal | 750.00 | 2.0 h | 1196.00 | e-stop qty |
| E-Stop Button | 74.50 | 1.0 h | 207.35 | qty |
| Timer & Button | 57.66 | 1.0 h | 185.458 | qty |
| Motor Starter / Contactor | 151.00 | 1.0 h | 306.80 | qty |
| Auxiliary Relay | 23.00 | 0.5 h | 85.15 | qty |

Additional Cost is a **passthrough** (added as-is, no markup — workbook
C7 = B7). Golden totals from the workbook's own worked examples, asserted in
CI and on the bench: `PDT-0040-2-1-1-2-1` → **1685.008**,
`PDT-0040-1-1-1-2-0` → **1512.979**. Market note from the sheet: the Pentair
equivalent runs ≈ $1,000 — the goal is to be at or under that for the common
config's margin review.

**Module parts-cost vs component list:** the workbook's module costs are
estimates and do NOT equal the component-list sums (live vendor prices).
Pricing is driven by the editable module cost fields; the component list
drives the manufacturing BOM. Update either independently.

### Components

23 purchasable parts across the modules (buzzer ECX2071-24R, e-stop GCX3131,
contact block ECX1040-2, ABB AF12Z-30-10-21 contactor, IDEC RV1H-G-D24 relay,
GRT6-M1 timer, Polycase ZH-100806-03 enclosure, Mornsun LM75-23B24 PSU, ABB
1SNA115271R2200 terminals shared by three modules, covers/gaskets/wire/labels/
glands…) with vendors: Amazon, Automation Direct, Grainger, Digikey, Galco,
IFS, RS Online, Standard Electric Supply Co, TRC, Polycase. Items without a
manufacturer part number get `PC-*` codes (e.g. `PC-COVER-2G-SS`).

**Known gaps (deliberate):**

- **Mounting choices have no component rows** — the source BOM never itemizes
  them (Surface references the Polycase SG-12 box; Pedestal is a ~$750
  bollard). Generated BOMs therefore omit mounting hardware; module pricing
  is unaffected. Add component rows on the Configurable Product when the shop
  standardizes the kits.
- **QC checklists are a generic starter** — the docx QC headings (Timer /
  E-stop / Panel) are empty; refine the seeded QC steps on the product form.
- **Contactor pricing assumes ≤15 hp pumps** (sheet note: can be cheaper when
  pump hp is known; AFxx-30-xx-11 covers 25–50 A).
- The BOM spreadsheet's procurement-tracking columns (Approved / Ordered /
  EDD / Received / Delivered) are covered natively by Material Request →
  Purchase Order once the parts are Items — nothing custom was built.

## ERPNext generation

`erp_integration.generate(configuration)` — one whitelisted request, one
implicit transaction, **no commits** — so a failure anywhere rolls back
everything (fail-closed, like the Document Merge engine):

1. **Component Items** (also a standalone button on Configurable Product):
   missing Suppliers (group "Distributor"), Item Groups ("E-Stop Components",
   "Configured Products") and component Items are created; existing records
   are never overwritten. Components carry `valuation_rate` = seeded unit cost
   plus a Standard Buying Item Price for purchasing UX.
2. **Configured Item**: `item_code` = part number, stamped with
   `custom_source_configuration`. An existing configurator-owned Item is
   reused (re-quotes of the same config are legal); a foreign Item with the
   same code throws.
3. **BOM**: components-only (`with_operations 0` — labor lives in the
   configurator price; operations would double-count it),
   `rm_cost_as_per "Valuation Rate"` (fresh sites fall back to
   `Item.valuation_rate`, so BOM cost matches the seed; real moving averages
   take over once receipts exist), rows aggregated by item code (the 2-pole
   terminal appears in three modules; ERPNext rejects duplicate rows),
   submitted with `is_active`/`is_default` (ERPNext auto-points
   `Item.default_bom`). Regenerate: unchanged structure → `update_cost()`;
   changed structure → new BOM version, old one deactivated (never cancelled —
   historical Work Orders keep their reference).
4. **Item Price**: upsert on (item, Standard Selling, Nos) at the rounded
   selling price.

Labor-in-BOM upgrade path (documented, not built): one "Assembly" Operation +
one Workstation at the product's labor rate, `with_operations = 1`,
`time_in_mins = module hours × 60` — then remove labor from the quote or
reconcile deliberately.

**Stock (v1.827.0).** The configured Item is a stock item (set explicitly, not
left to the site's default) with a submitted default BOM, so built-ahead stock
and Work Orders can use it. It also gets an Item Default warehouse for the
company: Manufacturing Settings' Default Finished Goods Warehouse, else Stock
Settings' Default Warehouse, else the company's `Finished Goods - <abbr>`.
Without one, ERPNext refuses to save a Sales Order holding a stock item
("Delivery warehouse required"), and on 2026-11-18 neither setting was filled in
on prod. A reused Item that is not a stock item is switched where ERPNext allows
it and reported where it does not; `Item.default_bom` is re-asserted on every run.
**Not yet:** serial numbers. Decision 2026-11-18 gives every unit one, and
ERPNext locks `has_serial_no` once stock moves, so the Work Order step must turn
it on before the first unit is built.

## Quote to order (v1.827.0)

Decision 2026-11-18: **the Sales Order is the record** for a product sale, and
there is no project per unit. The flow:

1. **Opportunity → configuration.** "Quote STILLWATER E-Stop" on the
   Opportunity (a Desk button, and the guided page's Actions menu, which is how
   most people open a deal). With more than one enabled Configurable Product it
   reads "Quote a Product" and asks which. It calls `start_configuration`, which
   makes a Product Configuration with the deal's Account (or Lead) and the deal
   filled in, on the product's default options, and opens it.
2. **Configuration → Quotation.** "Create Quotation" on the configuration calls
   `create_quotation`: Item + BOM + price (`generate`), then a **draft**
   Quotation for the configuration's Account, else the deal's party (a Lead or a
   Prospect), linked to the deal, with the configured Item at the configured
   price. The line reads the part number, then one plain line per option. The
   Pricing Summary is attached as a private PDF. The configuration and the
   Quotation link to each other (`Product Configuration.quotation`, and
   `custom_product_configuration` on the Quotation line, which Make Sales Order
   copies to the Sales Order line); the Quotation names its deal in
   `custom_source_opportunity`. Pressing it again returns the same Quotation
   while it is live, and says so if the configuration changed since. When
   everything quoted on the deal in ERPNext is a product, the form then asks
   whether the deal is a product sale; the quote itself writes nothing on the
   deal.
3. **Quotation → Sales Order.** ERPNext's own Make Sales Order. The line already
   carries its warehouse.

Both endpoints are POST-only and take plain arguments, so the phone quick-quote
page and the Triton tool can call them: `start_configuration(opportunity,
product, customer, selections)` takes the options as
`{option_key: choice_code | qty}`.

**No project for a product sale** (`crm_enhancements/product_sale.py`): only a
deal a **person** has ticked **Product Sale (No Project)** (on the Hand-Off
Process tab), with nothing else on its linked quotes. A configured quote never
decides it on its own: QuickBooks quotes never link to the deal, so a fountain
deal's real quote can be invisible while its E-Stop add-on quote is linked. The
configurator suggests the tick, and the Closed Won prompt, the Hand-Off tab and
Create Quotation ask. A ticked deal gets no hand-off SLA, no "Create project
now?" and stays out of the won-without-project reminder and backlog counts; its
one button is **Create Project Anyway**, which unticks it and creates the
project. A ticked deal whose linked quote carries anything else (a fountain, a
control panel, labor) is a job. What counts as a product item is
`product_orders`'s rule, shared with the Products tab.

**The deal link.** A configurator Quotation names its deal in
`Quotation.custom_source_opportunity`, not ERPNext's own `opportunity` (which the
Opportunity mapping sets and the configurator clears). A Quotation linked
ERPNext's way writes the deal's status as it is submitted (`Quotation`), ordered
(`Converted`) or lost (`Lost`), and once ordered ERPNext refuses any other status,
so the deal could never be moved to Closed Won. With the custom link the deal's
stage is only ever moved by a person.

**Permissions.** Starting a configuration and quoting it need the Product
Engineer role (create/write on Product Configuration), plus create on Quotation,
Item, Item Price and Supplier and create/submit on BOM for the generation.
Every refusal says, in plain words, what is missing and which role to ask for.

## Master switch

`ERPNext Enhancements Settings → Product Configurator →
product_configurator_enabled` (default **OFF**). Configurations, live pricing,
previews and the three print formats always work — the switch gates only the
endpoints/buttons that create ERPNext masters. Server guards:
`feature_flags.throw_if_product_configurator_disabled()`; desk flag:
`frappe.boot.ee_product_configurator`.

## Operator checklist (post-deploy)

1. Flip **Enable Product Configurator Generation** in ERPNext Enhancements
   Settings.
2. Open **Configurable Product → PDT-0040 → Create Component Items** (creates
   the 10 suppliers and ~23 component Items; safe to re-run).
3. Assign the **Product Engineer** role to the people who quote/configure.
   Generating also needs standard Item/BOM/Item Price create permissions
   (stock + manufacturing roles) — System Managers are covered.
4. Configure away: **Product Configuration → New**, pick PDT-0040, tick/adjust
   options, Save → **Create → Item + BOM + Selling Price**; print the Build
   Sheet / QC Checklist / Pricing Summary from the View menu.
5. To sell one (v1.827.0): **Opportunity → Quote STILLWATER E-Stop**, adjust the
   options, Save → **Create Quotation**. Set **Manufacturing Settings → Default
   Finished Goods Warehouse** if finished units should not live in
   `Finished Goods - <abbr>`.

## Verification

- CI / bench-free: `python -m pytest
  erpnext_enhancements/tests/test_product_configurator_engine.py` (26 tests:
  both workbook goldens, per-module prices, multiplier/digit regression,
  condition-evaluator security cases, step branching, parts explosion).
- Dev bench: `bench --site dev.localhost execute
  erpnext_enhancements.product_configurator.dev_checks.<fn>` for
  `check_golden_pricing`, `check_config_roundtrip`,
  `check_build_step_conditions`, `check_generation` (dev only; creates
  masters), `cleanup_generation_artifacts`.

## Adding the next product

No code: create a new **Configurable Product**, define its options (Base +
Choice groups + Quantity modules with costs), give it a part-number template
whose `{tokens}` are the option keys, add component rows per `module_key`, and
write build-step templates with conditions. Everything else — pricing, live
preview, part numbers, BOM/Item generation, print formats — is generic.

# `product_configurator/` — configure-to-order products

Turns Sapphire's per-product pricing workbooks, BOM spreadsheets and Word build instructions
into a configurable product model: pick options on a form, get a part number, a price, a
printable build sheet, and — on demand — a real ERPNext Item + BOM + Item Price.

Design rationale and the product model are in
[`docs/PRODUCT_CONFIGURATOR.md`](../../docs/PRODUCT_CONFIGURATOR.md). This README is the
code map.

## File map

| File | Purpose |
|---|---|
| `engine/pricing.py` | The Pricing Calculator workbook made generic. Per option row: `unit_labor = flat_labor_cost or labor_hours * labor_rate`; `unit_cost = parts_cost + unit_labor`; `unit_price = unit_cost * (1 + markup_percent/100)`. Effective quantity is the row's own quantity times the quantity of the option named by `qty_multiplier_option` — the "mounting scales with e-stops" rule. "Additional cost" is a passthrough, added without markup, matching the workbook |
| `engine/partnumber.py` | Part-number construction (`PDT-0040-{mounting}-{estop_qty}-…`) and selection validation, over the option context that Quantity and Choice options build |
| `engine/conditions.py` | Restricted expression evaluator — a ~50-line AST whitelist (see below) |
| `engine/buildsteps.py` | Filters and renders step templates into flat printable rows |
| `erp_integration.py` | Generates the ERPNext Item, BOM and Selling Price from a configuration |
| `api/configurator.py` | Whitelisted desk endpoints — thin adapters over the controller and `erp_integration` |
| `seed_data.py` | The PDT-0040 STILLWATER E-Stop seed definition (stdlib only) |
| `setup_print_formats.py` | `after_migrate` — three print formats per configuration |
| `dev_checks.py` | Plain assertion functions runnable via `bench execute` |

## The condition evaluator is a security boundary

Build-step conditions (`timer_qty == 2`) and instruction placeholders
(`"Insert {estop_qty + timer_qty} cable glands"`) are **authored by users** on the
Configurable Product form. They must therefore be evaluated with no sandbox-escape surface.

`engine/conditions.py` is an AST whitelist: boolean, comparison and arithmetic expressions
over the option context only — **no calls, no attribute access, no subscripts, no f-string
tricks**. Anything outside the whitelist raises `ConditionError`.

Do not widen it for convenience. If a product definition needs something the whitelist
doesn't allow, that is a signal to model it as data, not to add `eval`.

## Author mistakes degrade, they never block

A typo on the product definition must never stop someone saving a configuration:

- A bad **condition** skips the step with a warning.
- A bad **placeholder** keeps the raw text with a warning.

Preserve that. Failing hard here punishes the wrong person.

## Atomicity in `erp_integration.py`

One whitelisted request is one implicit transaction, so there is deliberately **no**
`frappe.db.commit()` anywhere in that module. If the Item Price upsert throws last, the Item,
the BOM, the `Item.default_bom` pointer and the configuration's link-backs all roll back
together.

"Item created but BOM failed" is the failure mode this prevents — it leaves a half-built
product in the catalogue that nobody knows is broken. Adding a commit to "make partial
progress survive" reintroduces exactly that.

The same module follows the **"data at rest is ungated"** convention: previews and option
loading always work, and only the ERPNext-mutating endpoints additionally gate on the
`product_configurator_enabled` master switch. The switch guards mutations, not math.

## One deliberate divergence from the source workbook

The workbook's configuration-number formula multiplies the mounting digit by the e-stop
quantity — a spreadsheet bug, under which Flush with 2 e-stops reads as "2" (Surface).
`engine/partnumber.py` does **not** reproduce it. This is the one place the engine knowingly
disagrees with its source; it is documented in the module docstring, and a "fix" that
restores workbook parity would reintroduce the wrong part number.

## Seed data is the tested artifact

`seed_data.py` is the single source of truth for PDT-0040, transcribed from the three source
documents. It is imported by the `seed_pdt_0040_product` patch, by `dev_checks`, **and by the
bench-free unit tests** — so the pricing goldens (1685.008 / 1512.979, from the workbook's own
worked examples) validate the actually-shipped seed rather than a test copy. Keep it that way;
a test fixture that drifts from the seed tests nothing.

## DocTypes

| DocType | Role |
|---|---|
| `Configurable Product` | The product definition: options, components, step templates, part-number template |
| `Configurator Product Option` | An option (Quantity or Choice) on a definition |
| `Configurator Product Component` | A component/part row |
| `Configurator Build Step Template` | A build step with optional condition and placeholders |
| `Product Configuration` | One configured instance |
| `Product Configuration Option` | The selections made |
| `Product Configuration Part` | Resolved parts list |
| `Product Configuration Build Step` | Resolved, pre-rendered build steps |
| `Product Configuration Price Line` | Resolved pricing breakdown |

Print formats read the child rows the controller persisted on save — no engine calls and no
conditional logic in Jinja, because steps are pre-filtered and pre-rendered server-side.

## Tests

```bash
python -m unittest erpnext_enhancements.tests.test_product_configurator_engine -v
```

`bench run-tests` is broken under Python 3.14 on the dev bench, which is why `dev_checks.py`
exists as plain `bench execute` functions:

```bash
bench --site dev.localhost execute \
  erpnext_enhancements.product_configurator.dev_checks.check_golden_pricing
```

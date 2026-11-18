# Product Fulfillment

Building, testing and shipping product units: the Stillwater E-Stop (PDT-0040) first, then any
product the Product Configurator makes. A product sale is a Sales Order (Nikolas, 2026-11-18, vault
`decisions/2026-11-18-estop-product-line.md`); this module carries it from the order to a tested,
serial-numbered unit ready to ship.

Tracking: TASK-2026-03229 (build from a Work Order), TASK-2026-03230 (serial number, QC gate,
warranty) and TASK-2026-03231 (getting it to the customer), under P1 group TASK-2026-03227 on
PRJ-00580. Decision record:
[ADR 0019](../../decisions/adr/0019-product-unit-tests-use-quality-inspection.md).

## The flow

1. **Sales Order submitted.** Each configured product line (an Item with
   `custom_source_configuration`, kept in stock) is planned (`rules.plan_line`): finished units of the
   same Item on the shelf that no other open order claims ship from stock; the rest get **one Work
   Order per unit** from the Item's default BOM. Made as the system, released at once when a
   work-in-progress and a finished warehouse resolve, left as drafts (with a message saying what to
   set) when they do not. The order's **Create > Product Work Orders** runs the same plan again and
   never builds twice.
2. **Built.** The Work Order carries its configuration's build steps (copied when it was made). The
   **Build Checklist** page (`/desk/build-checklist/<WO>`, the Work Order's Build Checklist button)
   is the phone- or tablet-sized view: big tap rows, "I'm building this", and the units once built.
   The builder is recorded on the Work Order (`custom_built_by`), by that button or the first tick.
3. **Finished.** ERPNext's own Manufacture entry makes the unit and its serial number from the Item's
   series (`SF-.YY.-.#####` unless Product Build Settings says otherwise). Our hook stamps the
   serial with its builder and Work Order. **Print label** prints the Product Unit Label.
4. **Tested.** Someone other than the builder presses **Start test** (on the page or the serial
   number) and fills in the Pre-Shipment Functional Test, a Quality Inspection on the template
   Product Build Settings names. Submitting signs it, so the tester submits it. **Print
   Certificate** prints what goes in the box.
5. **Shipped.** A Delivery Note shipping a configured product is refused until every unit on it has
   its serial picked (packed items of a Product Bundle included) and the last submitted test of each
   serial passed, signed by someone other
   than its builder. Delivering the serial starts its warranty: ERPNext sets `warranty_expiry_date`
   from the Item's 365-day `warranty_period`.
6. **To the customer** (v1.827.0). The Delivery Note's **Fulfillment** says how
   (`custom_fulfillment_method`, carried over from the Sales Order's field of the same name), and a
   note shipping a configured product cannot be submitted without it:
   - **Pickup at shop**: from the draft, **Email Ready for Pickup** tells the customer contact, once
     every unit has passed (the gate's own check). The note is submitted when they collect.
   - **Carrier shipping**: ERPNext's own **Carrier** (`transporter`, a Supplier ticked Is
     Transporter) and **Tracking Number** (`lr_no`, relabeled). Saving cleans the number and sets
     the **Tracking Link** to the carrier's public page (UPS and FedEx from `carrier_tracking`, plus
     USPS and DHL; any other carrier through the settings' carrier table). Submitting emails the
     customer contact "Shipped" with the link. **Carrier Status** asks FedEx or UPS through Carrier
     Tracking, when that is switched on; nothing is stored.
   - **Sapphire delivery**: a **Delivery Date** and **Delivered By**; the note is assigned to that
     person, on that date, as soon as it is saved.
   Both emails go through the shared shell (`templates/emails/product_fulfillment/`), queued in
   Email Queue, with a comment on the note saying who was emailed.

The Products tab's Testing column (`project_dashboard._product_qc`) shows built orders whose units
have not passed yet.

## File map

| Path | What it is |
|---|---|
| `rules.py` | Every decision, with no Frappe: the line plan, warehouses, the checklist, tester-is-not-builder, a unit's test state, the Delivery Note gate, the Testing column. `tests/test_product_fulfillment_rules.py` |
| `settings.py` | Reads Product Build Settings as a whole document (never `get_single_value`: a never-saved Single only shows its defaults that way); Item facts the hooks share |
| `build.py` | Sales Order `validate` / `on_submit` and the Make Work Orders endpoint; Work Order `before_insert` copies the build steps |
| `checklist.py` | The Build Checklist page's endpoints: `get_build`, `tick`, `claim_build`. Writes single rows with `db.set_value`, because the Work Order is submitted while it is built |
| `serials.py` | Item `before_validate` (serial numbers, series, warranty, test template on configured stock Items, blanks only); Stock Entry `on_submit` (builder stamp); `set_unit_builder` for a unit from no Work Order |
| `qc_gate.py` | Quality Inspection `before_validate` / `validate` / `before_submit`; Delivery Note `before_submit` (the gate); `start_test`; `delivery_gate_status` for the form |
| `shipping_rules.py` | Getting it to the customer, with no Frappe: what each method needs, which carrier a transporter is, the tracking link, which email a method sends. `tests/test_product_fulfillment_shipping.py` |
| `shipping.py` | Delivery Note `validate` (tracking link), `before_submit` (method requirements, after the gate), `on_submit` (Shipped email, delivery assignment), `on_update`; Email Ready for Pickup, Resend Shipped Email, Carrier Status |
| `../templates/emails/product_fulfillment/` | The Ready for pickup and Shipped email bodies |
| `doctype/product_carrier/` | The settings' carrier table row: a Supplier, which carrier it is, its tracking page |
| `setup_print_formats.py` | `after_migrate`: Product Unit Label (Serial No) and Pre-Shipment Test Certificate (Quality Inspection) |
| `doctype/product_build_settings/` | The Single: build, work-in-progress and finished units warehouses (empty until chosen), the switch for making Work Orders, the serial series, the warranty days, the test switch and template |
| `doctype/work_order_build_step/` | The checklist row on `Work Order.custom_build_steps` |
| `page/build_checklist/` | The page. Styles under `.pfb-` |
| `../public/js/product_fulfillment/` | Form scripts: Work Order, Serial No, Quality Inspection, Delivery Note, Sales Order |
| `../patches/seed_estop_pre_shipment_test.py` | The starter test template, insert-only |

## What to set before the first build

In **Product Build Settings**:

- **Build warehouse**: the Build Room shelf parts are picked from. Empty: each part comes from its
  own Item Default warehouse.
- **Work-in-progress warehouse** and **Finished units warehouse**: ERPNext will not start a Work
  Order without both. Product order lines deliver from the finished units warehouse, so set it to the
  shelf the built-ahead units sit on.
- Leave the serial series, the 365-day warranty and the test template unless the shop wants
  different ones.

Also, outside this module:

- **Quality Inspection rights.** Only the Quality Manager role may create or submit a Quality
  Inspection (ERPNext's own permissions). Each tester needs it, or another way in.
- **Keeping 2-3 built-ahead units.** Build them from a Work Order with no Sales Order; ERPNext's own
  Item Reorder (Material Request type Manufacture) on the common configuration can prompt the next
  build when the shelf runs low.

For shipping, in the same settings: **Pickup Instructions** (printed in the Ready for Pickup email;
empty, it asks the customer to reply), **Email the Customer When It Ships** (on), and a carrier row
for any carrier whose name does not say which it is. And on each carrier's Supplier (UPS, FedEx,
Usps, DHL on prod): tick **Is Transporter**, or ERPNext's own Carrier field will not offer it.

## Not here

- **Pay at order** (the order invoiced at once, Stripe pays it): a later task.
- **Configured Items made before this release** that already moved stock keep Has Serial No off:
  ERPNext refuses to change it once stock has moved. On 2026-11-18 production had no configured Item.

# 0019. A product unit's pre-shipment test is ERPNext's Quality Inspection; the gate is ours

- **Status:** Accepted
- **Date:** 2026-11-18 (v1.826.0)
- **Narrows:** [0012](0012-project-inspections-do-not-use-quality-inspection.md), its invariant that
  `frappe.get_doc("Quality Inspection", …)` in this app is always a mistake. That invariant holds for
  **project** inspections. It does not hold for a product unit, for the reasons below.

## Context

Nikolas decided on 2026-11-18 (vault `decisions/2026-11-18-estop-product-line.md`) that every
Stillwater E-Stop (PDT-0040) gets a serial number and must pass the Pre-Shipment Functional Test
before it ships, that a test certificate goes in the box, and that Logan Penrod or Nathan Cox builds
each unit while the other tests it: **the tester is never the builder.**

Two records could hold the test:

1. **ERPNext's Quality Inspection** (Stock module), with a Quality Inspection Template of
   parameters, `inspected_by` (Link User), `item_serial_no`, readings with Accepted / Rejected, and
   the Item flag `inspection_required_before_delivery` that makes a Delivery Note refuse to submit
   without one.
2. **This app's `quality` module**: `Project Quality Inspection`, generated frozen from a Project
   Inspection Template plus a project's contracted criteria, with a field wizard. The Draft template
   "Products — Pre-Shipment Functional Test" lives there.

ADR 0012 put project inspections on option 2 because Quality Inspection's required fields
(`reference_type`/`reference_name` from a fixed list of stock documents, `item_code`, `sample_size`)
have no meaning for a project milestone, and there is no project, milestone, photo or signature.

## Decision

**The test is a Quality Inspection**, one per unit:

- `item_code` is the configured Item (the part number), `item_serial_no` the unit, `sample_size` 1;
- `reference_type` is **Stock Entry**, `reference_name` the Manufacture entry that made the unit
  (the serial's own `reference_name`, which ERPNext sets when it makes the serial);
- `inspected_by` is the tester; the template is Product Build Settings' **Pre-Shipment Test
  Template**, seeded as "Pre-Shipment Functional Test - E-Stop" (a starter draft Logan refines);
- `Quality Inspection.custom_built_by` fetches the builder from `Serial No.custom_built_by`, which the
  Manufacture entry stamps from the Work Order.

Every one of ADR 0012's objections is a field a product unit fills naturally: an item, a stock
document, a sample of one. That is the record's purpose (ADR 0002, native first).

`Project Quality Inspection` is **not** used: an E-Stop sale has no project (decision pick 1), its
Draft template is written for engineered control panels (HMI screens, IO schedule, pump starters),
and `quality_enabled` is off.

**The gate is ours: `inspection_required_before_delivery` stays off on configured Items.** Read at
ERPNext v16.54.0, the native gate (`stock_controller.validate_inspection`) checks one Quality
Inspection **per Delivery Note line**, linked on the line, and `validate_qi_reference` requires that
inspection to have been made against **that Delivery Note**. So:

- it cannot see serial numbers: a line shipping two units passes with one inspection;
- every unit would wait for a Delivery Note draft before it could be tested, including the
  built-ahead units on the shelf, which are tested in the Build Room when they are built;
- with the flag on, the Delivery Note would demand a second, delivery-time inspection per line on
  top of the build-time test. (With it off, Quality Inspection's own `validate_inspection_required`
  refuses an inspection made against a Delivery Note at all, which is one more reason the test is made
  against the Manufacture entry: Stock Entry references are never refused.)

Instead `product_fulfillment.qc_gate.delivery_note_before_submit` refuses the submit unless every
configured product line has one serial number per unit picked, and the newest submitted test of
each serial is Accepted, on the configured template, for that Item, and signed by someone other
than its builder (`rules.delivery_gate`). It also refuses to let ERPNext pick serials on its own at
submit (`auto_create_serial_and_batch_bundle_for_outward` is on, on prod, first in first out), which
would ship whichever unit is oldest, tested or not.

**The signature is the submit.** `before_submit` refuses when the person submitting is not the
`inspected_by` the test names, so "tested by Nathan" means Nathan submitted it. `validate` refuses a
test whose tester is the recorded builder.

## Consequences

Two quirks of Quality Inspection have to be worked around, both read at v16.54.0:

- **A re-test.** `set_child_row_reference` claims the first row of the referenced Stock Entry with
  no Quality Inspection on it, and refuses with "every row of Item … already has a Quality
  Inspection" when there is none. A unit that failed and was fixed has exactly that row taken by its
  failed test. `quality_inspection_before_validate` names the row that holds the serial
  (`child_row_reference`) first, which ERPNext then accepts as given. The Stock Entry row keeps
  pointing at the first test; the gate never reads that link, it reads `item_serial_no`.
- **Cancelling a test overwrites its status with "Cancelled"**, so a failure that is cancelled
  stops reading as a failure. A re-test is therefore a new test, never a cancelled old one; the
  newest submitted test decides (`rules.unit_state`).

And one of the gate: **one Work Order per unit**. A Manufacture entry for a Work Order of two
makes one finished row with both serials, and a single person recorded as having built both.
Making one Work Order per unit gives each unit its own builder, checklist and serial, and its own
row for its test.

**Permissions.** Only the Quality Manager role may create or submit a Quality Inspection
(ERPNext's own DocPerms, unchanged on prod), and on 2026-11-18 only one of the two builders held it.
Granting it, or another way in, is Nikolas's call and is not shipped as a permission fixture: a Custom DocPerm
row would replace ERPNext's whole permission set for the doctype.

**What would have to change before revisiting.** If ERPNext's native gate learns to check each
serial of a line, and to accept an inspection made against the Stock Entry that produced the unit,
the custom gate could be retired in favour of the Item flag.

# UX: Quick Entry sweep + procurement form layouts

v1.145.0. Two related data-entry simplifications, both shipped as fixtures
(Property Setters + Custom Field tweaks) — applied unconditionally on migrate,
no runtime toggle (per `erpnext_enhancements/fixtures/README.md`, form-level
property setters are low-risk and not gated).

## 1. Quick Entry sweep

Frappe's Quick Entry dialog (list-view **+ New**, awesomebar "new x", and
"Create a new …" inside Link fields) shows every **required** field plus any
field flagged `allow_in_quick_entry` — but only when the doctype has
`quick_entry = 1` **and no required child table**. Doctypes with a required
`items` grid (all the purchasing transactions) can never use it; they got the
form-layout redesign below instead.

### Newly enabled

| DocType | Dialog |
|---|---|
| **Item** (re-enabled) | item_code, item_name, item_group, stock_uom, is_stock_item, is_fixed_asset (+ asset_category when fixed asset) |
| **Customer** (re-enabled) | name, type + ERPNext's built-in primary contact & address fields |
| **Opportunity** | the 9 mandatory fields, 6 prefilled; "Opportunity From" now defaults to Customer and is restricted to Lead/Customer/Prospect inside the dialog (`link_filters` — the full form's script filter doesn't run in dialogs) |
| **Employee** | first name, company, status, gender, DOB, date of joining (+ middle/last name) |
| **Warehouse** | name, company, type, parent warehouse, customer |
| **Item Group / Customer Group / Supplier Group** | name + parent group |
| **Serial No** | serial no, item, company, project |
| **Event** | subject, type (defaults Private now), starts on, ends on |

Item and Customer had Quick Entry *deliberately disabled* in earlier fixtures;
this release reverses that on purpose — the dialogs are now curated.

### Curation added to already-enabled dialogs

- **Supplier**: + supplier group, country, tax ID
- **Lead**: + last name, service interest (email deliberately excluded — it
  lives on a hidden tab, values entered there would be invisible afterward)
- **Project**: + customer, stage, requested start/end dates
- **Task**: + priority, expected end date, description
- **Issue**: + customer, priority, description
- **Batch**: + expiry date
- **Payment Term**: + name, invoice portion, due date basis, credit days —
  this dialog shipped broken upstream (enabled but zero qualifying fields, so
  it silently never appeared)
- **ToDo**: + priority, allocated to
- **Note**: + content

### Deliberately excluded

- **Contact / Address** — ~~dialog-created records would be orphaned: the party
  link (`links` child table) is only wired up by the full-form scripts~~
  **superseded in v1.150.0**: these two now get app-owned quick-entry dialogs
  (`public/js/global_enhancements/contact_address_quick_entry.js`) that resolve
  the party form they were opened from and inject the `links` rows client-side
  before insert. Different mechanism from this sweep on purpose — no
  `quick_entry`/`allow_in_quick_entry` fixtures, so with the "Contact & Address
  Quick Entry" toggle off (or the bundle unloaded) behavior reverts to the
  stock full form, never to an orphan-creating stock dialog.
- **Territory / Sales Person** — rare tree masters; the tree view's own New
  dialog is the right tool.
- Tree masters that ARE enabled (Warehouse, the three group doctypes): the
  tree view keeps its own dialog; Quick Entry only takes over list-view/link
  creation. Saving with a blank parent shows Frappe's "multiple root nodes"
  error — pick the root node as parent.

## 2. Form layout redesign — the 7 procurement doctypes

Item, Material Request, Purchase Order, Purchase Receipt, Purchase Invoice,
Supplier Quotation, Request for Quotation.

**Philosophy:** the first tab is everything the 90 % data-entry case needs —
who/when identity block, the items grid immediately after (currency and
warehouse rows collapsed/compact above it), then taxes and totals. Everything
auxiliary stays reachable but demoted: the five purchase documents share one
tab grammar — **Details → Contacts & Addresses → (Drop Ship) → Terms →
(Payments) → More Info → Connections → Comments** — so learning one form
teaches all of them. More Info absorbs the clutter (status trackers,
accounting dimensions, pricing rules, tax breakup, printing, auto-repeat,
internal-supplier fields) as collapsible sections. Purchase Invoice's
required **Credit To** account moved from a buried accounting section to the
first tab. Nothing was deleted or hidden except the duplicate empty Comments
tab on Purchase Order / Supplier Quotation (a leftover `custom_comments` Tab
Break shadowing the real `custom_comments_tab`).

**Item**: the Details tab now opens with the create-an-item essentials (code,
SKU, identifier, name, group, UOM + the flag column), then attributes/pricing/
description, the Pumps-only spec section, and collapsed tolerances; the domain
tabs are reordered most-used-first (Inventory → Purchasing → Sales → UOM → Tax
→ Accounting → Manufacturing → Quality → Variants → Pricing → Connections →
Comments) with contents untouched.

### How it's built (and maintained)

The `field_order` Property Setter arrays are **generated, not hand-edited**:
`scripts/layout/generate_field_order.py` compiles small declarative specs
(`scripts/layout/specs/*.json`) against the bench's doctype JSONs and rewrites
the fixtures, enforcing hard lints (exact field-universe permutation, no
stray column breaks, no empty visible tabs, required fields on the first tab).
`scripts/layout/code_owned_fields.json` registers custom fields that live in
app code rather than fixtures (Item pump specs, PI intake/QBO/workflow_state).

> **After every ERPNext upgrade:** re-run
> `python scripts/layout/generate_field_order.py` and commit the diff —
> new upstream fields otherwise get appended by fallback placement (harmless
> but untidy). This release also repaired the stale pre-v16 arrays (Purchase
> Invoice was missing 14 fields; Material Request/PI carried phantom
> fieldnames).

### Known trade-offs

- The framework "Standard" print format mirrors field order, so PO/PI print
  output order changes with the layout (deliberately: identity → items →
  taxes → totals first). Shipped Jinja print formats are unaffected.
- Any un-exported Customize Form drift on these 7 doctypes is overwritten on
  deploy (fixtures re-assert — that's the contract).
- Task's `custom_repeat_every` (required, default 1) shows as prefilled noise
  in the Task dialog; follow-up candidate: make it mandatory only when
  recurring.
- Rollback: revert the release and redeploy — fixtures re-assert the old
  values wholesale.

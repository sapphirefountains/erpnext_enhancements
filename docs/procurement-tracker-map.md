# Procurement Tracker — code map

Orientation for the **Procurement Tracker**, the collapsible procurement tree on the Project
form's Budget tab. Written as the shared groundwork for four queued changes (sortable columns,
Requested/Ordered/Received quantities, the item-status rollup bug, and a quick Purchase Receipt
action), because all four land in the same two files and two of them need the same arithmetic.

Nothing here changes behaviour. It records what is true as of v1.193.1.

## First, the name collision

ERPNext ships a **standard Script Report called "Procurement Tracker"** (module Buying,
`ref_doctype` Purchase Order). It is unrelated to this, and it is not in this repo — there is no
`report/` folder under any Buying-ish module here, and no `procurement_tracker.{py,js,json}`
anywhere.

The thing this document describes is an in-house **Vue 3 widget** mounted into the
`custom_material_request_feed` HTML custom field on the Project form. The field's label,
*"Material Request Feed"*, is a historical misnomer — it renders all six procurement doctypes,
not just Material Requests. Code and docs call it the Procurement Tracker
([`public/README.md`](../erpnext_enhancements/public/README.md), `desk_enhancements.bundle.css`),
the field calls it a feed, and the tasks call it a tracker. They are one thing.

## The surface

| Layer | Where |
|---|---|
| Renderer | [`public/js/project_enhancements.js`](../erpnext_enhancements/public/js/project_enhancements.js) — `render_procurement_tracker`, lines 33-308 |
| Endpoint | [`project_enhancements/__init__.py`](../erpnext_enhancements/project_enhancements/__init__.py) — `get_procurement_documents`, lines 355-423 |
| Underneath that | same file — `get_procurement_status`, lines 11-219 (the raw SQL) |
| Styling | [`public/css/desk_enhancements.bundle.css`](../erpnext_enhancements/public/css/desk_enhancements.bundle.css) — `.procurement-tracker` and friends, lines 242-312, 384-455, 473-486, 489-599 |
| Field | `Project-custom_material_request_feed` (HTML, read-only) — a **fixture**, `fixtures/custom_field.json:17428`, inside the equally-fixture section break `custom_section_break_xbww4` (`:20100`), which sits after `per_gross_margin` at the bottom of the Budget tab |
| Also consumes the backend | [`assistant_tools/project_procurement_status.py`](../erpnext_enhancements/assistant_tools/project_procurement_status.py) — the MCP tool |

`project_enhancements.js` does only two things: mounts the Comments App, and renders this
tracker. It is *not* `project_form_script.js` (task tree / Gantt / tab relocation) and not
`project_migrated_scripts.js` — neither mentions procurement.

Vue is loaded ahead of it in `hooks.py:81-85` (`vue.global.js`, `comments.js`,
`project_merge.js`, `project_enhancements.js` — in that order, and the order matters).

## How it renders

**Vue 3, via the global `window.Vue`, with an inline template string.** Not a frappe DataTable,
not `frappe.ui.form.make_control`, not a hand-rolled HTML string. `createApp` at
`project_enhancements.js:57`, `app.mount('#procurement-tracker-app')` at `:289`. The only jQuery
is the one-line wrapper stub at `:46-47`.

That choice is the single most important fact for anyone planning work here: **there is no table
library to configure.** Sorting, column visibility, and per-row actions are all things you write
by hand against the template at `:177-287`.

### Three nesting levels

Only the innermost is a `<table>`.

**Level 1 — DocType group** (`:189-196`). A `div.group-header`: chevron, then
`{{ group.doctype }} ({{ group.documents.length }})`. Collapsed by default (`:64-67`).

**Level 2 — document** (`:201-210`). A flex `div.doc-header`, cells in order:

| # | Content | Source |
|---|---|---|
| 1 | chevron | `isDocCollapsed(...)` |
| 2 | document name, click opens the form | `highlight(doc.name, …)` |
| 3 | date | `formatDate(doc.date)` |
| 4 | supplier | `doc.supplier` or `-` |
| 5 | status badge | `doc.status`, class from `getStatusColorClass` |
| 6 | item count | `{{ doc.items.length }} item(s)` |

**Level 3 — the item table** (`:215-280`), `<table class="glass-table">`. Header at `:216-224`:

| # | Header | Cell |
|---|---|---|
| 1 | `Item Details` | `item_code` + `<small>item_name</small>`; the whole cell opens `source_doc_type`/`source_doc_name` when both exist |
| 2 | `Warehouse` | `warehouse` or `-` |
| 3 | `Qty (Ord / Rec)` | `{{ row.ordered_qty }} / {{ row.received_qty }}` |
| 4 | `Status` | `{{ row.completion_percentage }}% Received` — class `status-complete` at ≥100, else `status-pending` |
| 5 | `Doc Chain` | up to seven conditional sub-rows: MR, RFQ, SQ, PO, PR, PI, SE — each a link plus a status badge (`:239-277`) |

There is **no MR-row-versus-item-row hierarchy inside the table**. The table only appears inside
an already-expanded document, and every `<tr>` is a leaf. "The MR row" means the level-2
`.doc-header`; "the item rows" means the level-3 `<tr>`s. Worth being precise about, because the
status bug below is exactly a confusion between those two levels.

### Expansion and search state

Two plain objects on `data()` (`:58-70`): `collapsedGroups` keyed by doctype, and
`collapsedDocs` keyed `"DocType::name"` via `docKey` (`:121-123`). `toggleDoc` (`:127-131`) uses
a tri-state trick — `this.collapsedDocs[key] = (this.collapsedDocs[key] === false)` — so an
absent key reads as collapsed (`isDocCollapsed`, `:132-135`).

The one interactive control is the search box (`:179-181`), implemented by `filteredGroups`
(`:93-115`), `tokenize` (`:118-120`), `itemMatches` (`:139-142`), `filteredItems` (`:143-146`),
`docLevelMatches` (`:147-151`), `docMatches` (`:152-154`), a watcher that auto-expands anything
containing a match (`:72-91`), and `highlight()` (`:158-166`) which wraps hits in `<mark>`.

**There is no sorting of any kind.** No `@click` on any `<th>` (`:218-222`), no `.sort(` anywhere
in `methods`/`computed` (`:92-176`), no cursor affordance in the CSS (`desk_enhancements.bundle.css:496-504`).
The only ordering that exists is fixed and server-side: doctype groups by `PROCUREMENT_DOCTYPE_ORDER`
(`__init__.py:235-242`), documents within a group newest-first (`__init__.py:420`), and item rows in
whatever order the SQL returned — which from the user's seat is arbitrary.

## How the data is fetched

`frappe.call` to `erpnext_enhancements.project_enhancements.get_procurement_documents`
(`project_enhancements.js:49-53`), which calls `get_procurement_status` and regroups its output.

### `get_procurement_status` — the SQL

`__init__.py:11-219`, `@frappe.whitelist()`, **no permission check of any kind**. One raw-SQL
`UNION ALL` of two shapes:

- **Part 1** (`:27-96`) — the Material Request chain. Roots on `tabMaterial Request Item`, then
  `LEFT JOIN`s outward: RFQ Item → SQ Item → PO Item → PR Item → PI Item, plus Stock Entry Detail.
  `WHERE (mr_item.project = %(project)s OR mr.custom_project = %(project)s OR rfq.custom_project = %(project)s) AND mr.docstatus < 2`.
- **Part 2** (`:100-143`) — direct Purchase Orders with no MR link. Roots on
  `tabPurchase Order Item`, `WHERE po_item.project = %(project)s AND (po_item.material_request_item IS NULL OR = '') AND po.docstatus < 2`.

Then `get_procurement_documents` (`:378-388`) appends **the same row object into every doctype
bucket its chain touches** — so one item row appears once under its Material Request and again
under its Purchase Order. That is deliberate (it is what makes the tree work), but it means a
row is not uniquely owned by a document, which matters if you ever want per-row state.

### The `OR`-join fan-out

`__init__.py:69-72`:

```sql
LEFT JOIN `tabPurchase Order Item` po_item ON (
    po_item.supplier_quotation_item = sq_item.name
    OR po_item.material_request_item = mr_item.name
)
```

One MR line split across two Purchase Orders produces **two rows for that one MR line**. The
Purchase Invoice join (`:80-83`) has the same `OR` shape. **Any future per-item arithmetic has to
de-duplicate on `mr_item.name` first**, or it will double-count. Nothing does today, because
nothing aggregates today.

### No caching, anywhere

Not server-side (no `frappe.cache`, no `@redis_cache`), not client-side. `render_procurement_tracker`
fires on *every* `refresh` — every save, every SPA navigation back to the form — and issues a fresh
`frappe.call` each time. Cost per render is roughly one large `UNION` query plus, for each of the
six doctypes, two or three `get_all` calls for supplementary documents, plus one per supplementary
document for its items.

At current data volumes that is fine (see *Sizing* below). It is worth knowing before anyone adds
a per-row server call.

### The MCP tool shares this backend

`assistant_tools/project_procurement_status.py:48-84`. `view="documents"` calls
`get_procurement_documents`; the default `view="stage_summary"` calls `get_procurement_status` and
sums `ordered_qty` / `received_qty` across stages. It gates on `require_doc_read("Project", …)`
(`assistant_tools/_common.py:37-43`) precisely *because* the underlying feed does not.

**Consequence: the return shape of both functions is a public contract.** Adding keys is safe;
renaming or removing `ordered_qty` / `received_qty` breaks the assistant tool silently.

## Status and quantity logic

Three different things on screen are called "status", and they come from three different places.

**(a) The document badge, level 2** (`project_enhancements.js:208`) — `doc.status`, straight from
the DocType's own `status` field via `_fetch_doc_meta` (`__init__.py:343-352`), falling back to a
docstatus label.

**(b) The item "Status" column, level 3** (`:236-238`) — `{{ row.completion_percentage }}% Received`.
This is a **received** metric only. It is never an *ordered* metric, despite sitting next to a
column headed "Qty (Ord / Rec)".

**(c) The Doc Chain badges, level 3** (`:241-277`) — `row.mr_status`, `row.po_status`, and so on.

### The bug: MR status bleeds onto item rows

`__init__.py:36` selects the **parent** Material Request's header status:

```sql
mr.status as mr_status,
```

The row grain is `mr_item`. So every item row of a given Material Request carries an identical
`mr_status`. Python passes it through unchanged (`:198-199`), and the template paints it once per
item row (`:241-245`).

**`Material Request.per_ordered` and `Material Request Item.ordered_qty` are never queried by the
feed at all.** Repo-wide, the only read of `per_ordered` is
[`po_creation_guard.js:30`](../erpnext_enhancements/public/js/po_creation_guard.js), on the Material
Request form — a different feature entirely.

Two things make it worse:

- **`display_ordered_qty` (`:154-156`)** — `ordered_qty if ordered_qty > 0 else mr_qty`. An MR item
  with nothing ordered falls back to the requested quantity, so it renders `4 / 0` under
  "Qty (Ord / Rec)" and *reads as fully ordered*.
- **`getStatusColorClass` (`project_enhancements.js:167-175`)** — `"Partially Ordered"` and
  `"Ordered"` both match `s.includes('ordered')` and get the same `status-submitted` class. The
  text differs; the colour does not.

**Live reproduction:** `MAT-MR-2026-00001` on `PRJ-00566` — ten lines, nine fully ordered, one
(`437-582`, qty 4) with `ordered_qty = 0`. ERPNext correctly reports `status = "Partially Ordered"`,
`per_ordered = 98.9%`. In the tracker, all ten rows read *"MR: MAT-MR-2026-00001 [Partially Ordered]"*.
Nothing on the row distinguishes the nine that are done from the one that is not.
`MAT-MR-2026-00003` on `PRJ-00706` is a second case: 22 lines, two unordered.

## What the buttons do

Six `custom_btn_*` handlers, `project_enhancements.js:310-383`. Every one repeats the same
unsaved-Project guard.

| Button | Line | Behaviour |
|---|---|---|
| `custom_btn_material_request` | 310-319 | `frappe.new_doc("Material Request", {custom_project, project})` — sets **both** |
| `custom_btn_request_quote` | 321-330 | `frappe.new_doc("Request for Quotation", {custom_project, project})` — both |
| `custom_btn_supplier_quotation` | 332-340 | `frappe.new_doc("Supplier Quotation", {project})` |
| `custom_btn_purchase_order` | 342-363 | as above, plus a WI-066 `frappe.model.can_create` pre-check |
| `custom_btn_purchase_receipt` | 365-373 | `frappe.new_doc("Purchase Receipt", {project})` |
| `custom_btn_purchase_invoice` | 375-383 | `frappe.new_doc("Purchase Invoice", {project})` |

**`custom_btn_purchase_receipt` opens a blank Purchase Receipt with only the header `project`
filled in.** No supplier, no Purchase Order, no items. It does not use
`erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt` (ERPNext's own
mapper), does not use `frappe.model.open_mapped_doc`, and does not prompt for a PO. The user lands
on an empty form and has to reach for "Get Items From → Purchase Order" themselves.

The Purchase Order button is the only one with a role gate, and its comment is the precedent worth
copying for any future guarded quick-create (`:347-359`): `frappe.new_doc` performs no permission
check, so without the pre-check the form opens fully editable and only fails at save — losing
however many lines were typed.

Neither the section nor any of the six buttons is a fixture. All ten fields (section, six buttons,
two column breaks) are created by `patches/add_project_procurement_buttons.py` and re-applied by
`patches/reorder_procurement_buttons.py` (`patches.txt:41-42`); `custom_pickup_routing_section` and
`custom_btn_pick_routing_map` come from `patches/add_project_pick_routing_button.py` (`patches.txt:271`).
`create_custom_fields` stamps `is_system_generated = 1`, and the Custom Field fixture filters on
`is_system_generated = 0` (`hooks.py:607-611`), so they are excluded by design. Only the feed HTML
field and its section break are fixtures.

## Sizing

Measured on production, 2026-07-31. This is what makes the sortable-columns work cheap and what
should stop anyone reaching for server-side pagination.

| Measure | Value |
|---|---|
| Largest project by Purchase Order Item rows | **54** (`PRJ-00567`) |
| Next two | 52 (`PRJ-00566`), 24 (`PRJ-00706`) |
| Material Requests, total / submitted | 19 / 10 |
| Submitted MR states | 1 Ordered, 2 Partially Ordered, 6 Pending, 1 Stopped |
| Purchase Orders | 70 Closed, 31 Draft, 28 To Receive and Bill, 1 To Bill, 1 Cancelled |
| Purchase Orders partially received | **0** |

Two data facts that surprise people:

- **No `Material Request Item` row has `project` set** — all zero of them. The first arm of the
  `WHERE` clause at `__init__.py:92` therefore never matches; Material Requests reach the feed
  exclusively via `Material Request.custom_project` (9 rows) or `rfq.custom_project`. Contrast the
  Pick Routing Map, which unions header and item-row `project` on Purchase Orders precisely because
  *those* two disagree.
- **There are no partially-received Purchase Orders**, so any change whose acceptance criteria
  involve partial receipt needs a case constructed on a test site rather than found on production.

`Material Request Item.ordered_qty` was checked against `SUM(Purchase Order Item.stock_qty)` for
submitted Purchase Orders across 160 submitted MR item rows: **zero discrepancies**. ERPNext's
denormalized quantity fields are trustworthy on this site, which means quantity work can read them
rather than recomputing from child tables — with the caveat that they are denormalized and the
check should be repeated if the numbers ever look wrong.

## Where the queued work lands

| Change | Files | Notes |
|---|---|---|
| Item-status rollup fix | `__init__.py:36, 154-156, 192-217`; `project_enhancements.js:167-175, 241-245` | Root cause is `mr.status` on a child-grain row. Needs per-item ordered-ness, which needs the shared helper below. |
| Requested / Ordered / Received | `__init__.py` SQL + post-processing; `project_enhancements.js:220, 235` (and level 2 at `:201-210` for the rollup) | Same arithmetic as the row above. |
| Sortable columns | `project_enhancements.js` — `data()` `:58-70`, `computed` `:92-116`, template `:216-224`; `desk_enhancements.bundle.css:496-504` | Client-side only at these volumes. Must compose with `filteredGroups`, not replace it. |
| Quick Purchase Receipt | `project_enhancements.js:215-280` (per-row action) and `:365-373` (the existing button) | Reuse ERPNext's `make_purchase_receipt` mapper; gate on `can_create`; never auto-submit. |

**The shared refactor.** The status fix and the quantity columns are the same calculation seen from
two angles: given a Material Request line, how much was requested, how much is on submitted Purchase
Orders, and how much has been received. Implementing that twice is how the two levels drift apart
again. It should land once — with the status fix, which is the higher-priority of the two — and be
consumed by the other. Whatever shape it takes, it has to answer:

- de-duplication across the `OR`-join fan-out (`__init__.py:69-72`);
- which docstatuses count (draft, cancelled, amended);
- returns, i.e. negative Purchase Receipts;
- `uom` versus `stock_uom` — real on this data: `MAT-MR-2026-00001` requests `PD-400-100` in **FT**
  against a stock UOM of **Unit**;
- over-receipt, which should be visible rather than clamped.

## Gotchas

- **The Vue app is never unmounted.** Every `refresh` does `$(wrapper).html(...)` and then
  `createApp(...).mount(...)` on a fresh node, orphaning the previous instance with its watchers
  still live. The mount target is a hard-coded document-global id, `#procurement-tracker-app`.
- **`v-html` on unescaped data.** Used at `:205, 207, 232, 234, 243, 248, 253, 258, 263, 268, 273`.
  `highlight()` (`:158-166`) escapes the *search tokens* for the regex, but never escapes `text` —
  item codes, item names, warehouses, supplier names and document names all reach the DOM raw.
- **`get_procurement_status` and `get_procurement_documents` are whitelisted with no permission
  check.** Anyone who can call the endpoint can read any project's purchasing. The MCP tool
  compensates with its own gate; the browser path does not. `api/README.md` notes the same about
  the Pick Routing Map on the same tab, and that tightening either means tightening both together.
- **`_supplementary_documents` swallows every exception** (`__init__.py:390-398`) — a bare
  `except Exception` with `frappe.log_error`. A doctype whose sweep fails silently vanishes from
  the feed rather than erroring.
- **Item rows are shared objects.** The same dict is appended under each doctype bucket its chain
  touches, so mutating a row in one group mutates it in all of them.
- `Stock Entry` appears in the Doc Chain column but is **not** a group — it is absent from
  `PROCUREMENT_DOCTYPE_ORDER`.
- `custom_pickup_routing_section` and `custom_btn_pick_routing_map` are missing from the
  `Project-main-field_order` Property Setter (`fixtures/property_setter.json:4047-4051`); they were
  added after that array was last exported. Relevant if you touch field ordering.

## Tests

[`tests/test_procurement_status.py`](../erpnext_enhancements/tests/test_procurement_status.py) —
three tests: `test_get_procurement_status_internal_transfer` (`:179`),
`test_get_procurement_status_direct_po` (`:245`),
`test_get_procurement_documents_structure_and_order` (`:281`).

**Nothing tests status text, and nothing tests partial ordering.** That gap is why the bug above
survived. Any fix should arrive with a case built from one fully-ordered, one partially-ordered and
one unordered line on a single Material Request.

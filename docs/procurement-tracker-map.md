# Procurement Tracker — code map

Orientation for the **Procurement Tracker**, the collapsible procurement tree on the Project
form's Budget tab.

Written first as the groundwork for four queued changes, then updated once those landed
(v1.194.0 – v1.197.0). Line references are against that merged state. Where a defect it
originally documented has since been fixed, it says so rather than quietly dropping it — the
reasoning is usually the part worth keeping.

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
| Renderer | [`public/js/project_enhancements.js`](../erpnext_enhancements/public/js/project_enhancements.js) — `render_procurement_tracker`, lines 100-602 |
| Sort registry | same file, `PROCUREMENT_SORT_COLUMNS` at `:30-58` — declared outside the Vue options on purpose |
| Endpoint | [`project_enhancements/__init__.py`](../erpnext_enhancements/project_enhancements/__init__.py) — `get_procurement_documents`, `:492-568` |
| Underneath that | same file — `get_procurement_status`, `:13-277` (the raw SQL) |
| Quantity math | [`procurement_quantities.py`](../erpnext_enhancements/procurement_quantities.py) — app root, pure, no `frappe` import |
| Styling | [`public/css/desk_enhancements.bundle.css`](../erpnext_enhancements/public/css/desk_enhancements.bundle.css) — `.procurement-tracker` and friends |
| Field | `Project-custom_material_request_feed` (HTML, read-only) — a **fixture**, inside the equally-fixture section break `custom_section_break_xbww4`, at the bottom of the Budget tab |
| Also consumes the backend | [`assistant_tools/project_procurement_status.py`](../erpnext_enhancements/assistant_tools/project_procurement_status.py) — the MCP tool |

`project_enhancements.js` does only two things: mounts the Comments App, and renders this
tracker. It is *not* `project_form_script.js` (task tree / Gantt / tab relocation) and not
`project_migrated_scripts.js` — neither mentions procurement.

Vue is loaded ahead of it in `hooks.py` (`vue.global.js`, `comments.js`, `project_merge.js`,
`project_enhancements.js` — in that order, and the order matters).

## How it renders

**Vue 3, via the global `window.Vue`, with an inline template string.** Not a frappe DataTable,
not `frappe.ui.form.make_control`, not a hand-rolled HTML string. `createApp` at `:124`,
`app.mount('#procurement-tracker-app')` at `:583`.

That choice is the single most important fact for anyone planning work here: **there is no table
library to configure.** Sorting, column visibility and per-row actions are all things you write
by hand against the template.

### Three nesting levels

Only the innermost is a `<table>`.

**Level 1 — DocType group.** A `div.group-header`: chevron, then
`{{ group.doctype }} ({{ group.documents.length }})`. Collapsed by default.

**Level 2 — document.** A flex `div.doc-header`, cells in order:

| # | Content | Source |
|---|---|---|
| 1 | chevron | `isDocCollapsed(...)` |
| 2 | document name, click opens the form | `highlight(doc.name, …)` |
| 3 | date | `formatDate(doc.date)` |
| 4 | supplier | `doc.supplier` or `-` |
| 5 | status badge | `doc.status`, class from `getStatusColorClass` |
| 6 | **quantity rollup** (`:477`) | `rollupText(doc)` — `"362 req · 358 ord · 0 rec"`. Hidden entirely when there is nothing to total, so an RFQ header does not sprout a row of zeroes |
| 7 | item count | `{{ doc.items.length }} item(s)` |
| 8 | **Receive button** (`:482`) | Purchase Order group only, and only where receiving makes sense — see *Quick actions* |

One span for the rollup rather than three, because `.doc-supplier` is the only element in that
flex row that grows and three `nowrap` spans squeeze a supplier name to nothing on a narrow
screen.

**Level 3 — the item table**, `<table class="glass-table">`. Headers are rendered from the sort
registry (`:493`), so a column cannot exist in the template and not in the comparator:

| # | Header | Cell | Sortable |
|---|---|---|---|
| 1 | `Item Details` | `item_code` + `<small>item_name</small>`; the whole cell opens `source_doc_type`/`source_doc_name` when both exist | text |
| 2 | `Warehouse` | `warehouse` or `-` | text |
| 3 | `Requested` | `requested_qty` — **`-`, not `0`, on a direct PO line**: nothing was requested, which is a different fact from "requested zero" | number |
| 4 | `Ordered` | `ordered_qty`, plus a muted `+N draft` suffix when quantity sits on unsubmitted orders | number |
| 5 | `Received` | `received_qty` | number |
| 6 | `Status` | the line's own `receive_status` as a badge; the percentage moved into the tooltip with the arithmetic behind it | rank |
| 7 | `Doc Chain` | up to seven conditional sub-rows: MR, RFQ, SQ, PO, PR, PI, SE — each a link plus a badge | **no** |

Doc Chain is deliberately unsortable and carries no click affordance: it is one cell holding up
to seven chain nodes, so there is no single value to order by and a handler would mean inventing
an ordering the column does not show.

Quantity cells carry a UOM tooltip. The figures are stock-UOM on a request line and
transaction-UOM on a direct Purchase Order line, because ERPNext's `status_updater` maintains
each against a different field — see `procurement_quantities.py`. Real on this data:
`MAT-MR-2026-00001` requests `PD-400-100` in **FT** against a stock UOM of **Unit**.

There is **no MR-row-versus-item-row hierarchy inside the table**. The table only appears inside
an already-expanded document, and every `<tr>` is a leaf. "The MR row" means the level-2
`.doc-header`; "the item rows" means the level-3 `<tr>`s. Worth being precise about, because the
status bug this document used to describe was exactly a confusion between those two levels.

### Expansion, search and sort state

Three plain objects on `data()`: `collapsedGroups` keyed by doctype, `collapsedDocs` keyed
`"DocType::name"` via `docKey`, and `sortByDoc` (`:142`) keyed the same way. `toggleDoc` uses a
tri-state trick — `this.collapsedDocs[key] = (this.collapsedDocs[key] === false)` — so an absent
key reads as collapsed.

Sort state is **per document**, matching the collapse state: two documents in a group can want
different sorts, and a global sort would silently reorder collapsed tables nobody asked about.

Search is `filteredGroups` plus `tokenize` / `itemMatches` / `filteredItems` /
`docLevelMatches` / `docMatches`, a watcher that auto-expands anything containing a match, and
`highlight()` which wraps hits in `<mark>`.

**Search filters first, then sort sorts.** Sorting lives in `displayItems` (`:318`), a render
method rather than a computed, so `filteredGroups`, the auto-expand watcher and the highlighting
are untouched by it.

Two Vue traps the sort implementation avoids, both silent if reintroduced:

- `displayItems` **copies before sorting**. `Array.prototype.sort` mutates, and mutating
  `doc.items` during a render is an infinite reactivity loop.
- Rows are keyed on a server-supplied `row_id`, not the array index. Index keys make Vue reuse
  the wrong DOM nodes once rows can reorder, which smears the `<mark>` highlight spans across
  neighbouring rows.

Blanks sort **last in both directions** — the blank comparison returns before the direction is
applied, which is the only way that stays true when flipped. `0` is not blank. Status sorts by
workflow order rather than A–Z, because alphabetical gives *Not Received / Over Received /
Partially Received / Received*, interleaving "done" between two "not done" states.

## How the data is fetched

`frappe.call` to `erpnext_enhancements.project_enhancements.get_procurement_documents`, which
calls `get_procurement_status` and regroups its output.

### `get_procurement_status` — the SQL

`__init__.py:13-277`, `@frappe.whitelist()`, **no permission check of any kind**. One raw-SQL
`UNION ALL` of two shapes:

- **Part 1** — the Material Request chain. Roots on `tabMaterial Request Item`, then `LEFT JOIN`s
  outward: RFQ Item → SQ Item → PO Item → PR Item → PI Item, plus Stock Entry Detail.
- **Part 2** — direct Purchase Orders with no MR link. Roots on `tabPurchase Order Item`.

Both filter cancelled orders. Part 1's `AND po.docstatus < 2` on the Purchase Order join
(`:105`) was added in v1.194.0; Part 2 always had it. Latent only because this site has no
cancelled Purchase Orders.

Part 1 also selects the per-line rollups the arithmetic depends on — `mr_item.name`,
`mr_item.stock_qty`, `mr_item.ordered_qty`, `mr_item.received_qty` — plus `po_item.name`,
`po.docstatus`, and both UOMs.

Then `get_procurement_documents` appends **the same row object into every doctype bucket its
chain touches** — so one item row appears once under its Material Request and again under its
Purchase Order. That is deliberate (it is what makes the tree work), but it means a row is not
uniquely owned by a document.

### The `OR`-join fan-out

```sql
LEFT JOIN `tabPurchase Order Item` po_item ON (
    po_item.supplier_quotation_item = sq_item.name
    OR po_item.material_request_item = mr_item.name
)
```

One MR line split across two Purchase Orders produces **two rows for that one MR line**. The
Purchase Invoice join has the same `OR` shape. Concretely: `MAT-MR-2026-00001`'s **ten** lines
arrive from this query as **nineteen** rows.

**Aggregation de-duplicates on the child row name before summing** —
`procurement_quantities.dedupe_lines`, called by `_document_rollup` (`:460`). Summing the raw
rows reports **720 requested / 716 ordered** against a true **362 / 358**. Rows with no child row
of their own (the supplementary sweep builds those) stay distinct and each count once.

Per-*line* figures are immune by construction, because they are read from the line's own
denormalized rollup rather than summed across rows.

**The display duplication remains**: the same MR line still renders twice in the table when it
is reachable by both join paths. That is a separate, pre-existing defect with no live example on
this site beyond the two Material Requests above, and it is not fixed.

### No caching, anywhere

Not server-side (no `frappe.cache`, no `@redis_cache`), not client-side.
`render_procurement_tracker` fires on *every* `refresh` and issues a fresh `frappe.call` each
time.

At current volumes that is fine — and the quick-receipt action currently relies on it: the
tracker's Received numbers refresh because returning to the Project form re-fetches everything.
Anyone adding caching needs to give that a different mechanism.

### The MCP tool shares this backend

`assistant_tools/project_procurement_status.py`. `view="documents"` calls
`get_procurement_documents`; the default `view="stage_summary"` calls `get_procurement_status`
and sums `requested_qty` / `ordered_qty` / `received_qty` across stages. It gates on
`require_doc_read("Project", …)` precisely *because* the underlying feed does not.

**Consequence: the return shape of both functions is a public contract.** Adding keys is safe;
renaming or removing `ordered_qty` / `received_qty` breaks the assistant tool silently. That is
why v1.194.0 *corrected the meaning* of `ordered_qty` rather than renaming it, and added
`requested_qty` alongside.

## Status and quantity logic

Three different things on screen are called "status", and they come from three different places.

**(a) The document badge, level 2** — `doc.status`, straight from the DocType's own `status`
field via `_fetch_doc_meta` (`:476`), falling back to a docstatus label. For Purchase Orders that
helper also fetches `docstatus` and `per_received`, which drive the Receive action.

**(b) The item "Status" column, level 3** — the line's own `receive_status`, computed from that
line's ordered-versus-received quantities.

**(c) The Doc Chain badges, level 3** — for the Material Request node, the line's own
`order_status`. For the rest, that document's status.

### The bug this document was written for — fixed in v1.194.0

`__init__.py` used to select `mr.status as mr_status` — the **parent** Material Request's header
status — onto a row whose grain is `mr_item`. Every item row of a request therefore carried an
identical status, and the template painted it once per row. On `MAT-MR-2026-00001` that meant
nine fully-ordered lines and one untouched one all reading *"Partially Ordered"*; across
production, **29 of 32 item rows** under a partially-ordered request were mislabelled.

Two things made it worse, both also fixed:

- `display_ordered_qty = ordered_qty if ordered_qty > 0 else mr_qty` — an MR line with nothing
  ordered fell back to the requested quantity and rendered `4 / 0` under a header reading
  "Qty (Ord / Rec)", *reading as fully ordered*.
- `getStatusColorClass` matched on the substring `'ordered'`, which `"Partially Ordered"` and
  `"Ordered"` both contain, so they resolved to the same CSS class. A correct status string alone
  would still have rendered identically.

`mr.status` is **still fetched and still shown** — on the document header, where it belongs, and
in the item row's tooltip alongside the line's own figures. The two legitimately differ, and
seeing them together is what makes it obvious the row is no longer echoing its parent.

The colour mapper now matches our vocabulary exactly first, and its fallback heuristic (kept for
ERPNext's own status strings on the other chain nodes) tests `partial` *before* the generic
terms.

### `procurement_quantities.py`

One place where "how much was asked for, how much is on order, how much arrived" is decided,
because the tracker asks that question at two levels and the two used to answer it differently.
Pure functions, no `frappe` import — which is why it sits at the app root beside
`procurement_project` / `po_approval` / `po_segregation` rather than under
`project_enhancements/`, whose package `__init__` imports `frappe` and so cannot be imported
bench-free however pure a submodule is.

Decisions baked in, each verified against production:

- **Read ERPNext's denormalized rollups; never `SUM` over the feed's join.** They are per-line,
  so the fan-out cannot inflate them, and they already net out amendments, cancellations and
  returns — three cases with almost no live examples here, which is exactly where a hand-rolled
  sum would be wrong unnoticed. Checked against `SUM(Purchase Order Item.stock_qty)` over every
  MR-linked submitted PO line: zero discrepancies.
- **Stock UOM is the basis on the request axis**, transaction UOM on the Purchase Order axis.
  Every line here has `conversion_factor = 1`, so the two are indistinguishable in live data —
  which is precisely why it is written down.
- **Only submitted orders count as ordered.** Ten Purchase Order Item rows against draft orders
  are linked to Material Request lines on production; they used to inflate the tracker while
  ERPNext's own `ordered_qty` excluded them, so the tracker and the Material Request form it
  links to disagreed. Draft quantity is reported as `draft_ordered_qty`, never folded in.
- **Nothing is clamped.** Over-ordering and over-receipt get their own statuses and exceed 100%.
  The defect being fixed was a number quietly substituted to make a line look complete.

## Quick actions

Six `custom_btn_*` handlers create project-linked documents. Every one repeats the same
unsaved-Project guard.

| Button | Behaviour |
|---|---|
| `custom_btn_material_request` | `frappe.new_doc("Material Request", {custom_project, project})` — sets **both** |
| `custom_btn_request_quote` | same shape, both fields |
| `custom_btn_supplier_quotation` | `frappe.new_doc("Supplier Quotation", {project})` |
| `custom_btn_purchase_order` | as above, plus a WI-066 `frappe.model.can_create` pre-check |
| `custom_btn_purchase_receipt` (`:659`) | **PO picker** — see below |
| `custom_btn_purchase_invoice` | `frappe.new_doc("Purchase Invoice", {project})` |

The Purchase Order button's comment is the precedent worth copying for any guarded quick-create:
`frappe.new_doc` performs no permission check, so without the pre-check the form opens fully
editable and only fails at save — losing however many lines were typed.

### Creating a Purchase Receipt (v1.197.0)

Two entry points, both routing through ERPNext's own mapper
`erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt` via
`frappe.model.open_mapped_doc`:

- **Per-PO row in the tracker** — `canReceive` (`:259`) / `receiveAgainst` (`:266`). The row
  button carries `@click.stop`, because `.doc-header`'s own click toggles expansion and without
  it every Receive click would also collapse the row being read.
- **The project-level button** — lists the project's outstanding orders via the whitelisted
  `procurement_project.get_receivable_purchase_orders` and asks which arrived. One outstanding
  order skips the prompt; none says so rather than falling back to a blank form.

`open_mapped_doc` is deliberately called **without** `frm`: it falls back to `frm.doc.name` for
the source, and the source is a Purchase Order while the open form is the Project.

Both land on an **unsaved draft**. A Purchase Receipt is a stock transaction — submitting writes
Stock Ledger and GL entries, and cancelling one afterwards is an accounting event rather than an
undo. Nothing here auto-submits.

Shown only where receiving makes sense: submitted, not `Closed`/`Delivered`, `per_received < 100`
— the rule `api/pickup_routing.py` already settled on, reused rather than reinvented. *Hidden*
rather than disabled without create permission.

> Before v1.197.0, `custom_btn_purchase_receipt` did only
> `frappe.new_doc("Purchase Receipt", {project})` — a blank form with no supplier, no order and
> no items, which the receiver could not link back to the PO afterwards.

## Custom fields

Neither the Procurement section nor any of the six buttons is a fixture. All ten fields
(section, six buttons, two column breaks) are created by
`patches/add_project_procurement_buttons.py` and re-applied by
`patches/reorder_procurement_buttons.py`; `custom_pickup_routing_section` and
`custom_btn_pick_routing_map` come from `patches/add_project_pick_routing_button.py`.
`create_custom_fields` stamps `is_system_generated = 1`, and the Custom Field fixture filters on
`is_system_generated = 0`, so they are excluded by design. Only the feed HTML field and its
section break are fixtures.

## Sizing

Measured on production, 2026-07-31.

| Measure | Value |
|---|---|
| Largest project by Purchase Order Item rows | **54** (`PRJ-00567`) |
| Next two | 52 (`PRJ-00566`), 24 (`PRJ-00706`) |
| Material Requests, total / submitted | 19 / 10 |
| Submitted MR states | 1 Ordered, 2 Partially Ordered, 6 Pending, 1 Stopped |
| Purchase Orders | 70 Closed, 31 Draft, 28 To Receive and Bill, 1 To Bill, 1 Cancelled |
| Purchase Orders partially received | **0** |

This is why sorting is client-side: 54 rows is nothing to paginate.

Four data facts that surprise people:

- **No `Material Request Item` row has `project` set** — all zero of them. The first arm of the
  `WHERE` clause therefore never matches; Material Requests reach the feed exclusively via
  `Material Request.custom_project` (9 rows) or `rfq.custom_project`. Contrast the Pick Routing
  Map, which unions header and item-row `project` on Purchase Orders precisely because *those*
  two disagree.
- **There are no partially-received Purchase Orders**, so anything whose acceptance criteria
  involve partial receipt needs a case constructed on a test site rather than found on production.
- **No line is genuinely part-ordered** (`0 < ordered_qty < stock_qty`), so the item-level
  "Partially Ordered" state — the one most central to the original bug — has no live example and
  is covered only by synthetic tests.
- **Header and item-row `project` now agree on all 70 Purchase Orders**, because
  `cascade_project_to_items` fills blanks on save. It fills *blanks only, on save only*, so the
  union is still the right query.

`Material Request Item.ordered_qty` was checked against `SUM(Purchase Order Item.stock_qty)` for
submitted Purchase Orders across 160 submitted MR item rows: **zero discrepancies**.

## Gotchas

Still open:

- **The Vue app is never unmounted.** Every `refresh` does `$(wrapper).html(...)` and then
  `createApp(...).mount(...)` on a fresh node, orphaning the previous instance with its watchers
  still live. The mount target is a hard-coded document-global id, `#procurement-tracker-app`.
  Fixing this is the prerequisite for any realtime refresh.
- **`v-html` on unescaped data.** `highlight()` escapes the *search tokens* for the regex, but
  never escapes `text` — item codes, item names, warehouses, supplier names and document names
  all reach the DOM raw.
- **`get_procurement_status` and `get_procurement_documents` are whitelisted with no permission
  check.** Anyone who can call the endpoint can read any project's purchasing. The MCP tool
  compensates with its own gate; the browser path does not. `api/README.md` notes the same about
  the Pick Routing Map on the same tab, and tightening either means tightening both together.
- **`_supplementary_documents` swallows every exception** — a bare `except Exception` with
  `frappe.log_error`. A doctype whose sweep fails silently vanishes from the feed rather than
  erroring.
- **Item rows are shared objects.** The same dict is appended under each doctype bucket its chain
  touches, so mutating a row in one group mutates it in all of them.
- **The `OR`-join still duplicates display rows**, even though aggregation no longer
  double-counts.
- `Stock Entry` appears in the Doc Chain column but is **not** a group — it is absent from
  `PROCUREMENT_DOCTYPE_ORDER`.
- `custom_pickup_routing_section` and `custom_btn_pick_routing_map` are missing from the
  `Project-main-field_order` Property Setter; they were added after that array was last exported.

Closed, and worth not reintroducing:

- The item row inheriting its parent's status (v1.194.0).
- `ordered_qty` falling back to the requested quantity (v1.194.0).
- `getStatusColorClass` collapsing partial and complete into one class (v1.194.0).
- Quantities read from one arbitrary row of a fanned-out join (v1.194.0).
- Cancelled Purchase Orders joining the chain in Part 1 (v1.194.0).
- The project-level Purchase Receipt button opening a blank, unlinkable form (v1.197.0).

## Tests

- [`tests/test_procurement_quantities.py`](../erpnext_enhancements/tests/test_procurement_quantities.py)
  — **21 bench-free pytest tests with their own CI step.** The one that matters is
  `test_item_status_is_not_inherited_from_the_document`: three lines of one request, three
  different statuses, one parent whose label happens to be correct for exactly one of them. That
  coincidence is what let the original bug survive a casual look. Also fences the fan-out
  de-duplication against the naive-sum case, so a refactor that drops it fails rather than
  silently doubling.
- [`tests/test_procurement_status.py`](../erpnext_enhancements/tests/test_procurement_status.py)
  — **bench-required**, so it does *not* run in CI. Carries the end-to-end reproduction through
  the real query, plus the draft-PO case.

Note the split is load-bearing: the pytest suite is bench-free and runs on every push; the
unittest-style bench suite needs `bench --site <site> run-tests`. A new *pytest* suite must be
added to a `python -m pytest` step in `ci.yml`, never appended to a unittest module list.

# Procurement Tracker — code map

Orientation for the **Procurement Tracker**, the collapsible procurement tree on the Project
form's Budget tab.

Written first as the groundwork for four queued changes, then updated once those landed
(v1.194.0 – v1.197.0). Line references are against that merged state, and **v1.811.0 moved most
of them**: the widget's sorting moved into its own module and the renderer was restructured, so
read a `:123` below as "about there, in the v1.197.0 file". Where a defect it originally
documented has since been fixed, it says so rather than quietly dropping it — the reasoning is
usually the part worth keeping.

## v1.811.0: where it is now, filters, and the tracker by item

Nik, 2026-11-12: *"it'd be nice to see what's where for items. So enhanced filtering and
formatting of data."* Read this section first; it is the current shape.

| Layer | Where |
|---|---|
| Endpoint the widget calls | `project_enhancements.get_procurement_overview` → `{groups, items, filters, located, today}`. `groups` is `get_procurement_documents`, unchanged in shape |
| A line's state, the by-item rows, the filter choices | [`procurement_overview.py`](../erpnext_enhancements/procurement_overview.py) — app root, pure, no `frappe` import |
| Where an item is now: the rule | [`inventory_enhancements/project_stock_rules.py`](../erpnext_enhancements/inventory_enhancements/project_stock_rules.py) — pure |
| Where an item is now: the queries | [`inventory_enhancements/project_stock.py`](../erpnext_enhancements/inventory_enhancements/project_stock.py) — `places_for(project, item_codes)` |
| The widget's decisions (filters, address, sorting, labels, print) | [`public/js/procurement_tracker/logic.js`](../erpnext_enhancements/public/js/procurement_tracker/logic.js), bundled as `procurement_tracker.bundle.js` → `window.sf_procurement_tracker` |
| The widget itself | `public/js/project_enhancements.js` — `mountProcurementTracker` (the Vue app and template) and `render_procurement_tracker` (load, unmount, mount) |

**Where it is now.** ERPNext keeps on-hand by item and warehouse, with no project on that table.
The stock ledger does carry the project, so the answer is built from the ledger rows that name
the job, added up by item and warehouse and **capped at what the Bin row holds now** (a move or a
count made with no job on it takes stock away without the ledger saying whose it was). At the
job's own site warehouse everything on hand is the job's, so the Bin rows there are read as they
stand. A Material Issue or delivery with the job on it is *taken to the job*; a purchase return is
*returned to the supplier*; what the purchasing documents say arrived and nothing accounts for is
*place not recorded*, said in those words. A non-stock item reads *Location not tracked (not a
stock item)*.

Two refinements, both from the review of the first version:

- **Parts brought back are counted once.** A Material Receipt with the job on it is parts coming
  back from the job (`ledger_kind` → `LEDGER_BROUGHT_BACK`): they are at the place they came back
  to, and what came back is taken off "taken to the job", never below zero.
- **The Project Shelf's number is one number.** With the Project Shelf's module installed,
  `places_for` takes the job's share of the shelf from `project_shelf.shelf_stock` (through
  `project_stock._shelf_rows`, the adapter the Bin Manager also uses) and hands it to
  `item_places` as `held`, instead of its own ledger sum. That function shares a short shelf out
  between the jobs that claim it, so the tracker and the Bin Manager's By project view cannot
  disagree. Without the module the ledger rule is already the answer on both screens.

It is **per item for the project, not per line**: two lines of one item share one answer, and the
cell says so (`where_now_lines`). The old Warehouse column, where the line was *received*, is
still there as "Received at …" under the places, and still on the row as `warehouse` /
`received_at`.

The lookup is a fixed handful of queries for the whole project (the ledger grouped once, the Bin
rows at the warehouses involved, their names, the items, and the job sites), never one per line.
If it fails, the feed still answers, `located` is false, the widget says the locations could not
be read, and the failure is in the Error Log.

**The Project Shelf** is whatever `inventory_enhancements/project_shelf.py::warehouse()` says
(another release's module, settled 2026-11-12). `project_stock.shelf_warehouse` imports it where
it is used; on a site whose code does not have it yet, the setting
`Inventory Scanner Settings.project_shelf_warehouse` is read with `Document.get`, and a site
without that field has no shelf, so the old `Stores` is simply a named shop location.

**Returned to the supplier** has two sources that describe the same goods: the ledger's
purchase-return rows that name the job, and `supplier_returns.returned_lines(project=...)` (the
Supplier Return record, another release again, imported the same way). They are never added; the
larger figure is the place. Each Supplier Return that names the item is listed under the places
with its status (`supplier_returns` on the row), "sent back" once its goods have left and "to go
back" before, for a non-stock item too.

**A line's state** is one of To order, Ordered, Partly received, Received, Late, Closed (plus
Quote only and Bill only for documents that never joined a chain), from the line's own
quantities. It is what the Status column now shows and what the status filter matches;
`order_status` and `receive_status` are still on every row and still drive the Doc Chain badge
and the document rollup. Three decisions worth knowing:

- **Closed is not late.** An order that was closed (or delivered straight to the customer) with
  goods outstanding, or a stopped request, is *Closed*: nobody is chasing it. 148 order lines on
  production sat on orders closed with nothing received.
- **A draft order is not an order**, so a line that sits only on a draft is *To order*. That
  holds for a draft the per-doctype sweep finds too (`_minimal_item_row` reports its quantity as
  `draft_ordered_qty`, as a chain row does).
- **A split line is late per order.** One request line across two orders arrives as two rows,
  each with the request's totals and its own order's date. The order that delivered its share is
  not late because the other one is.

**The by-item view** is one row per item for the project: requested, ordered and received across
every document (de-duplicated on the child row before summing, quotes and bills left out of the
quantities but kept in the Documents column), status, where it is now, the due date, suppliers
and documents. It has its own **Print list**: the items on screen with where each is now, as a
plain page for the browser's print dialog. The document PDFs were left alone on purpose: they go
to suppliers.

**Filters, and the address.** Where (Project Shelf, Job site, Shop bins, Taken to the job,
Returned to supplier, Not received yet, Location not tracked), Status, Supplier and "Needs action
only", plus the search box. Only choices that match at least one line are offered. The view and
every filter are written into the address as `pt_view`, `pt_where`, `pt_status`, `pt_supplier`,
`pt_action` and `pt_q` with `history.replaceState`: they survive a reload, come back with Back and
Forward, and travel in a shared link, and a filter is never a history entry of its own. frappe
copies an address's query into `frappe.route_options` on every route change; the widget takes its
own keys back out so the next list view is not asked to filter on them.

**Status labels** are the Desk's own badge (`.es-badge` with `data-theme`), with fallback colours
for a Desk that predates it. `.status-badge` is no longer used here (the Tasks tree still uses it).

**The address write waits a quarter of a second** (typing must not write it per key), and a write
still waiting when the form refreshes is cancelled with its app (`beforeUnmount`), so the old view
is never put back over the new one.

**Three old gotchas are closed** (see *Gotchas*): the Vue app is unmounted before the next one is
mounted, nothing reaches `v-html` unescaped, and the mount target is no longer a document-global
id. What is expanded and how each table is sorted now survives a form refresh (`frm.__pt_keep`).

**Return (Supplier Returns, v1.807.0).** `canReturn`, `returnToSupplier` and `canReturnLine` live
in `mountProcurementTracker` with the other row actions: a Return button on a submitted receipt's
header and a Return link on a line's receipt step, both through the launcher in
`public/js/supplier_return_start.js` (an unsaved Supplier Return; nothing is saved, emailed or
moved until its own form does it). `tests/test_supplier_returns.py` holds the script to them, and
`tests/test_tracker_supplier_returns.py` runs the tracker's reading of returns against the real
`returned_lines`.

**The assistant tool** gained `view="items"`, and every item row in every view carries the new
keys (`line_state`, `line_states`, `needs_action`, `is_late`, `expected_date`, `supplier`,
`where_now`, `where_now_text`, `location_tracked`, `location_kinds`, `received_at`). Nothing was
renamed or removed.

## v1.814.0: what was spent

Nik, 2026-11-12: *"on the procurement tracker, there should be another column for the price of the
item we spent."* A reading of it, built and open to correction; read it after the v1.811.0 section.

| Layer | Where |
|---|---|
| The rules: what a line costs, no price vs no charge, spent, unit price, a document's total | [`procurement_prices.py`](../erpnext_enhancements/procurement_prices.py) — app root, pure |
| The price columns of the query, who may see them | `project_enhancements/__init__.py` — `_chain_rows(project, prices)`, `_price_access`, `_price_total`, `_line_counts` |
| By-item sums | `procurement_overview.by_item(rows, today, prices=True)` → `procurement_prices.item_spending` |
| The widget | `logic.js` (`price_cells`, `price_text`, `item_price_view`, `doc_price_text`, `spent_summary`, the `rate` / `amount` / `unit_price` / `spent` sort columns, the `pt_noprice` filter) and `mountProcurementTracker` |

- **ERPNext holds no bills.** The only price is the purchase order line's rate. **Spent is what
  was ordered** (the submitted lines' own `base_amount`, company currency); the received value
  (`received_value`, received quantity times `base_rate`) is a second figure. Neither is "billed" or
  "paid", and nothing on screen says so.
- **A missing price is not a zero.** `price_state` is `priced`, `no_price` (rate 0 or blank) or
  `no_charge` (rate 0 and `is_free_item`). A `no_price` line carries no `po_rate` / `po_amount`, is
  left out of every sum, and is counted (`no_price_lines`). Purchase Order Item has no
  `allow_zero_valuation_rate` (Purchase Receipt Item has, and the receipt-only path reads it).
- **What Spent is not.** It is the sum of the order lines' own `base_amount`. It leaves out a
  discount applied to the whole order, taxes and freight, and it counts an order closed short in
  full; a discount on the line itself is in the rate, so it is in the amount. Example: 10 units at
  $10 with a 10% discount on the whole order shows $100, where the order's net is $90. The column's
  tooltip (`SPENT_NOTE` in `logic.js`) says so.
- **A quote is not a spend.** `quote_*` keys, shown on the line as "quote" and "quoted, not
  spent", never in `spent`. A request or an RFQ has no price.
- **Only a document that exists is priced.** The chain query's quotation and receipt joins have no
  docstatus filter (they never did), so a canceled document's line is in the rows. The query selects
  `pr.docstatus` and `sq.docstatus`, and `price_keys` skips a canceled quote or receipt (no quote, no
  value total) and carries a draft one with `quote_docstatus` / `receipt_docstatus` 0, which the
  tracker labels "draft". Which documents are listed is unchanged.
- **A draft is not an order**, as for quantities: `draft_spent`, not `spent`. A cancelled order's
  line (its join comes back empty) has none.
- **Money is keyed on the order line.** One request line split across two orders is one request
  line and two prices; `dedupe_lines` (request-line grain) is the wrong grain for it, so
  `procurement_prices._distinct` keys on `po_item`. An order line seen twice (the receipt and invoice
  joins repeat it) counts once.
- **A receipt is valued at the order's rate**, not its own copied one (`receipt_value`): the receipt
  line's rate is 0 exactly where the price was left off the order and added later with Update Items.
- **An order can span jobs.** `price_total` is this job's lines (`job_lines`), with `doc_lines` from
  one `count(*) group by parent` per doctype, `partial` when they differ and `doc_total` (the
  order's own `base_total`) then. The sweep skips a line that names another job.
- **Who sees it, and the known limit.** The endpoints and the Project form ask only for Project read.
  `_price_access()` returns `None` unless the person may read Purchase Orders (quotes: Supplier Quotation too), and then
  no price key is on any row, item or document, `prices` is absent from the overview and the widget
  draws no price column. `test_procurement_prices.py` holds this, and holds every pre-existing key.
  **Known limit:** the feed applies no User Permissions (it reads with `get_all`), so if someone is
  ever restricted by supplier or by order, the tracker would have to apply that too. On production no
  User Permission restricts Purchase Order or Supplier today.
- **Units.** `po_unit_rate` is per stock unit (`base_rate / conversion_factor`). On production an
  FT line and a Unit line of one item can both have factor 1, so a range can be two prices for two
  things: `price_uoms` lists the units and the cell says "Priced per FT and per Unit".
- **Print.** The by-item Print list gains Unit price and Spent and a total, on a landscape page. The
  document PDFs (`procurement_print.py`) are Frappe's own print formats and go to suppliers: untouched.
- **The address** carries the new filter as `pt_noprice=1` beside the others, written with
  `history.replaceState`; a person who is sent no prices has it switched off on load.

## v1.816.0: saved views, the column picker and filters on columns

Nik, 2026-11-12: *"for the Procurement Tracker, allow users to create special views and columns they
see. That would let them create a view for auditing location of bins, or seeing financial information.
These views should have some default presets, but everyone should be able to make custom views just for
them and the browser remembers the last used layout. Column picking and filtering should be a thing."*
Read it after the two sections above.

| Layer | Where |
|---|---|
| What a view may hold, repair of a stored one, owner / quota / name rules | [`procurement_view_rules.py`](../erpnext_enhancements/procurement_view_rules.py) — app root, pure |
| The columns, tests, limits and the shipped presets (one file, read by both sides) | [`data/procurement_tracker_views.json`](../erpnext_enhancements/data/procurement_tracker_views.json) |
| The endpoints | [`procurement_views.py`](../erpnext_enhancements/procurement_views.py) — `get_views`, `save_view`, `rename_view`, `delete_view` |
| The record | `Procurement Tracker View` — [`project_enhancements/doctype/procurement_tracker_view/`](../erpnext_enhancements/project_enhancements/doctype/procurement_tracker_view/) |
| The widget's decisions | `logic.js`, section *saved views*: `clean_layout`, `resolve_start`, `shown_columns`, `rule_matches`, `write_view_query`, `remember` / `parse_remembered` |
| The widget | `mountProcurementTracker`: the View / Columns / Column filters panels and the filter bar |

**A view** is `{columns, sort, filters}` for one grouping. `filters` is the five that were always on
the toolbar (Where, Status, Supplier, Needs action, No price only) plus `rules`: filters on a column,
each `{column, op, ...}` with *contains* (text), *is any of* (a status, a place or a supplier),
*is between* (a number or a date, either end optional) or *is blank / not blank*. Several rules all
have to hold. A view never holds the search box. Every column of the two tables is in the picker;
**the fields added to the picker are ones the feed already returned**, so there is no new query:

| Column | Grouping | Field |
|---|---|---|
| Still to order, Still to arrive | both | `to_order_qty`, `waiting_qty` |
| Due | by document (by item had it) | `expected_date` |
| Supplier | by document (by item had it) | `supplier` |
| Received at, Unit | both | `warehouse` / `received_at`, `stock_uom` |
| Lines on this job | by item | `line_count` |
| Received value, Lines with no price | by item (prices) | `received_value`, `no_price_lines` + `draft_no_price_lines` |

*Not added, for a later change* (each would need a new query or a new field): the supplier's contact,
what the item cost last time, the bin's own label (the where-now cell names the warehouse, not the
shelf position), the receipt's date, who made the request, the project's cost code.

**The presets** (`presets` in the JSON file; their columns and filters are in the pull request that
added them, and a test holds each one to a layout the server accepts):

| Preset | Grouping | Shows | Filters |
|---|---|---|---|
| Standard | document | the tracker as it was: Item, Where it is now, Requested, Ordered, Received, Rate, Amount, Status, Doc Chain | none |
| Standard | item | Item, Requested, Ordered, Received, Unit price, Spent, Status, Where it is now, Due, Supplier, Documents | none |
| Location audit | item | Item, Unit, Received, Still to arrive, Where it is now, Received at, Status, Due | none; sorted by Status, most urgent first |
| Items with no place recorded | item | Item, Unit, Received, Where it is now, Received at, Status | Where it is now contains "no place recorded" |
| Money (by item) | item | Item, Ordered, Received, Unit price, Spent, Received value, Lines with no price, Supplier, Status | none |
| Money (by document) | document | Item, Ordered, Received, Rate, Amount, Supplier, Status | none |
| Missing prices | item | Item, Ordered, Received, Unit price, Lines with no price, Supplier, Documents | No price only |
| To chase | item | Item, Ordered, Received, Still to arrive, Status, Due, Supplier, Documents | Needs action only; sorted by Status |

Two money views because Rate and Amount exist only by document and Unit price and Spent only by item.
The "no place recorded" preset depends on the words `project_stock_rules.place_text` writes for
`KIND_UNRECORDED` (a test holds the phrase to it).

**Where views live, and why.** `Procurement Tracker View`, owned by whoever made it (Frappe's own
`owner`), not per project. **Not `frappe.model.user_settings`:** on this site user settings are served
from Redis and every deploy `FLUSHDB`s it (see the gotcha in `CLAUDE.md`). The DocType gives **no
role but System Manager any permission**, so `/api/resource` and reports reach none of them; people use
the four endpoints in `procurement_views.py`, which gate on Project read, check the owner before every
read of a record, change and delete (a stranger's view reads as "not found", exactly like one that does
not exist), hold the layout to the strict validator, and write only the caller's own records.
`validate` on the controller is the backstop for the Desk form. A person keeps 50 views, with distinct
names (ignoring case and spacing); a view keeps its grouping, and Save a copy is how a built-in one is
changed. Reading never writes: `get_views` repairs a stored layout on the way out (an unknown column
dropped, a filter that no longer fits dropped, nothing usable becomes the standard layout) and says
`repaired`, so one bad record cannot break the tracker.

**Who sees what is unchanged.** A layout is names, not data. The price keys are not in the response
for a person who may not read Purchase Orders (`_price_access`, v1.814.0), so a view that names a price
column has nothing to draw for them; `shown_columns` leaves it out and the tracker says which columns
of the view are missing and why ("... not shown to you: prices are for people who may read Purchase
Orders"), and a filter on a price column is set aside the same way. The picker lists the price columns
for them as not available. A person without price access saving a copy of the Money view keeps its price
column names; they simply do not draw for them.

**Where a layout comes from when the tracker opens** (`resolve_start`, tested in the node suite):

1. The **address** wins when it names a view or a filter (any `pt_*` key). `pt_layout` is the view's id
   (absent for the two standard views, as a plain address already means them); `pt_view` is the
   grouping. A view in the link that is not the person's (a stranger's id, a deleted one) opens the
   standard view and says so.
2. What the person changed **on top of that same view** (columns, sort, filters on columns, which the
   address does not carry) comes back from what the form kept across a refresh, then from the browser's
   memory, but only when that memory is of the same view. A link to another view does not take them.
3. With **no address at all**, the browser's last layout is used, else Standard by document.

The address also says when a filter the view has on has been turned **off** (`pt_action=0`,
`pt_status=`), otherwise a reload would bring a cleared filter back from the saved view. It is written
with `history.replaceState`, like every other key: **never a history entry, so Back and Forward are
untouched**. Opening a view, changing a column or a filter writes nothing to the Project form
(`frm.doc` is never touched), so the form is not marked changed.

**What the browser remembers** is `localStorage["sf_pt_layout_v1:<user>"]`: per person (a shared
browser does not leak one person's view names to another) and per grouping (going to By item and back
brings By document back as it was), `{id, layout}` each, the search left out. Every access is in
`try`/`catch`; without storage the tracker works and the layout is kept only for the life of the form
(`frm.__pt_keep.layout`). What is read back is cleaned like any stored view, so a layout written by an
older release is mended and an unreadable one is ignored. A write still waiting when the form refreshes
is made, not dropped.

**Switching grouping** takes the layout last used in that grouping (or its Standard view) and carries the
toolbar filters and the search across, as it always has; the filters on columns stay with their view.

**A sort.** A click on a header in By item changes the view's sort (a change to the view). A click on a
document's table is that document's own, as before; the view's sort is the starting point of every
document's table. "Not sorted" in the Columns panel clears it.

**Known limits.** Filters on columns are not in the address (they are in the view and the browser's
memory), so a shared link carries the view's id and the toolbar filters but not a person's unsaved
column filters. A view shared by link opens for somebody else only if it is a built-in one; there is no
sharing of personal views (not asked for). The picker reorders with up and down buttons, not by dragging.
The by-item **Print list** still prints its own fixed columns, not the view's. Not run on a bench: see the
pull request.

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
| Renderer | [`public/js/project_enhancements.js`](../erpnext_enhancements/public/js/project_enhancements.js) — `render_procurement_tracker`, lines 345-877 |
| Sort registry | same file, `PROCUREMENT_SORT_COLUMNS` at `:30-49` — declared outside the Vue options on purpose |
| Endpoint | [`project_enhancements/__init__.py`](../erpnext_enhancements/project_enhancements/__init__.py) — `get_procurement_documents`, `:569-663` |
| Underneath that | same file — `get_procurement_status`, `:33-320` (the raw SQL) |
| Quantity math | [`procurement_quantities.py`](../erpnext_enhancements/procurement_quantities.py) — app root, pure, no `frappe` import |
| Printing | [`procurement_print.py`](../erpnext_enhancements/procurement_print.py) — `download_procurement_pdf`, app root; the Open rule is `procurement_quantities.document_is_open`. See *Printing* |
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
not `frappe.ui.form.make_control`, not a hand-rolled HTML string. `createApp` at `:369`,
`app.mount('#procurement-tracker-app')` at `:858`.

That choice is the single most important fact for anyone planning work here: **there is no table
library to configure.** Sorting, column visibility and per-row actions are all things you write
by hand against the template.

### Three nesting levels

Only the innermost is a `<table>`.

**Level 1 — DocType group.** A `div.group-header`: chevron, then
`{{ group.doctype }} ({{ group.documents.length }})`, then a **Print** button pushed to the far
end (`:727`) — see *Printing*. Collapsed by default.

**Level 2 — document.** A flex `div.doc-header`, cells in order:

| # | Content | Source |
|---|---|---|
| 1 | chevron | `isDocCollapsed(...)` |
| 2 | document name, click opens the form | `highlight(doc.name, …)` |
| 3 | date | `formatDate(doc.date)` |
| 4 | supplier | `doc.supplier` or `-` |
| 5 | status badge | `doc.status`, class from `getStatusColorClass` |
| 6 | **quantity rollup** (`:748`) | `rollupText(doc)` — `"362 req · 358 ord · 0 rec"`. Hidden entirely when there is nothing to total, so an RFQ header does not sprout a row of zeroes |
| 7 | item count | `{{ doc.items.length }} item(s)` |
| 8 | **Receive button** (`:753`) | Purchase Order group only, and only where receiving makes sense — see *Quick actions* |
| 9 | **Print button** (`:757`) | Every group, only where Frappe would actually print the document — see *Printing* |

One span for the rollup rather than three, because `.doc-supplier` is the only element in that
flex row that grows and three `nowrap` spans squeeze a supplier name to nothing on a narrow
screen.

**Level 3 — the item table**, `<table class="glass-table">`. Headers are rendered from the sort
registry (`:768`), so a column cannot exist in the template and not in the comparator:

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
`"DocType::name"` via `docKey`, and `sortByDoc` (`:387`) keyed the same way. `toggleDoc` uses a
tri-state trick — `this.collapsedDocs[key] = (this.collapsedDocs[key] === false)` — so an absent
key reads as collapsed.

Sort state is **per document**, matching the collapse state: two documents in a group can want
different sorts, and a global sort would silently reorder collapsed tables nobody asked about.

Search is `filteredGroups` plus `tokenize` / `itemMatches` / `filteredItems` /
`docLevelMatches` / `docMatches`, a watcher that auto-expands anything containing a match, and
`highlight()` which wraps hits in `<mark>`.

**Search filters first, then sort sorts.** Sorting lives in `displayItems` (`:585`), a render
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

`__init__.py:33-320`, `@frappe.whitelist()`, gated on Project read by `require_project_read`. One raw-SQL
`UNION ALL` of two shapes:

- **Part 1** — the Material Request chain. Roots on `tabMaterial Request Item`, then `LEFT JOIN`s
  outward: RFQ Item → SQ Item → PO Item → PR Item → PI Item, plus Stock Entry Detail.
- **Part 2** — direct Purchase Orders with no MR link. Roots on `tabPurchase Order Item`.

Both filter cancelled orders. Part 1's `AND po.docstatus < 2` on the Purchase Order join
(`:126`) was added in v1.194.0; Part 2 always had it. Latent only because this site has no
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
`procurement_quantities.dedupe_lines`, called by `_document_rollup` (`:504`). Summing the raw
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
field via `_fetch_doc_meta` (`:552`), falling back to a docstatus label. For Purchase Orders that
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
| `custom_btn_purchase_receipt` (`:934`) | **PO picker** — see below |
| `custom_btn_purchase_invoice` | `frappe.new_doc("Purchase Invoice", {project})` |

The Purchase Order button's comment is the precedent worth copying for any guarded quick-create:
`frappe.new_doc` performs no permission check, so without the pre-check the form opens fully
editable and only fails at save — losing however many lines were typed.

### Creating a Purchase Receipt (v1.197.0)

Two entry points, both routing through ERPNext's own mapper
`erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt` via
`frappe.model.open_mapped_doc`:

- **Per-PO row in the tracker** — `canReceive` (`:504`) / `receiveAgainst` (`:511`). The row
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

## Printing (v1.517.0)

Two ways onto paper, both reached from the tracker without leaving the Project form.

- **One document** — the row's **Print** (`printDoc`, `:543`) opens Frappe's own print view,
  `/desk/print/<doctype>/<name>`, in a **new tab**: format, letter head, PDF and Print are all
  there, and the Project form keeps whatever was expanded. `@click.stop` for the same reason as
  Receive.
- **A whole group** — the group header's **Print** (`printGroup`, `:536`) opens a dialog
  (`openProcurementPrintDialog`, `:189`): **All (n)**, or **Open (n)** where the doctype has an
  "open"; print format; letter head. It lists the documents that will print, newest first, and
  the ones left out and why, then opens one PDF from
  `procurement_print.download_procurement_pdf`. That endpoint is Frappe's
  `download_multi_pdf` — the list view's Actions → Print — called unchanged and then renamed
  `PRJ-00706-Open-Purchase-Orders.pdf`, because Frappe would name every job's file
  `Purchase-Order.pdf`. It gates on Project read like every other tracker endpoint.

**"All" means the whole group**, read from `groups`, not the search-narrowed `filteredGroups`
the header may be showing.

**"Open" is decided on the server**, once, by `procurement_quantities.document_is_open`, and
arrives on each document as `is_open` (with the group's rule in words as `open_rule`, shown in
the dialog). `None` means the doctype has no Open at all, and the choice is not offered:

| Doctype | Open means |
|---|---|
| Material Request | Submitted, material not all arrived: Pending, Partially Ordered, Ordered, Partially Received |
| Purchase Order | Submitted, not `Closed`/`Delivered`, `per_received < 100` — `SETTLED_PO_STATUSES`, so exactly what Receive offers |
| Purchase Invoice | Submitted, money owed: Unpaid, Overdue, Partly Paid |
| RFQ, Supplier Quotation, Purchase Receipt | *no Open* — see the comment on `OPEN_RULES` |

Drafts are never open. The Purchase Order rule reads the number, not the label, because billing
lives in QuickBooks: 70 fully received orders sat at `To Bill` on 2026-09-23, and "not
Completed" would have called every one of them open. The same fact is why Purchase Receipt has
no Open — its only outstanding state is `To Bill`.

Three things that would otherwise go wrong silently:

- **Frappe drops a document it refuses without saying so.** `download_multi_pdf` catches
  printview's "not allowed to print draft/cancelled documents" and moves on, so the document is
  just missing from the PDF. `procurementPrintBlocker` (`:113`) applies both Print Settings
  rules first; the dialog names what it left out, and the row hides its Print button. On
  production drafts may be printed and cancelled documents may not — and cancelled receipts and
  invoices *do* reach the tracker, since the chain's PR/PI joins do not filter docstatus.
- **"Standard" would render on wkhtmltopdf.** It has no Print Format record to carry a PDF
  backend, and Frappe falls back to wkhtmltopdf. The dialog sends `pdf_generator` the way the
  print view does (the format's own, else Print Settings' — chrome here); `get_print` reads it
  off `form_dict`.
- **The default format.** Since v1.519.0 every procurement doctype has a
  `default_print_format` — its "<Doctype> - Sapphire" format, set by a Property Setter fixture —
  so both the dialog and the row's print view open on the house design. Before that none did and
  Frappe started on "Standard"; the dialog's fallback (the doctype's one site-built format when
  there is exactly one, else Standard) is kept for a site where the fixture has not synced.

Over 25 documents the dialog uses Frappe's `download_multi_pdf_async` instead, glued exactly as
the list view glues it: a synchronous render that size risks the worker timeout. That path keeps
Frappe's filename and cannot be handed a PDF backend. No project had more than 23 of any one
doctype on 2026-09-23.

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

- The Vue app never being unmounted: every `refresh` mounted a new app over the last one's node
  and left the old one alive with its watchers. `render_procurement_tracker` unmounts it first,
  and mounts on its own node rather than a document-global id (v1.811.0).
- `v-html` on unescaped data: item codes, names, suppliers and document names reached the DOM
  raw. Every `v-html` now goes through `hl()` → `logic.highlight_html`, which escapes each piece
  (v1.811.0; `tests/test_project_stock.py` holds the template to it).
- A submitted Purchase Receipt with no order behind it (a store run) reading "Not Received" on
  the receipt that received it (v1.811.0, `_minimal_item_row`).
- The item row inheriting its parent's status (v1.194.0).
- `ordered_qty` falling back to the requested quantity (v1.194.0).
- `getStatusColorClass` collapsing partial and complete into one class (v1.194.0).
- Quantities read from one arbitrary row of a fanned-out join (v1.194.0).
- Cancelled Purchase Orders joining the chain in Part 1 (v1.194.0).
- The project-level Purchase Receipt button opening a blank, unlinkable form (v1.197.0).
- `get_procurement_status` / `get_procurement_documents` whitelisted with no permission check —
  both gate on Project read via `require_project_read` (v1.341.0).

## Tests

- [`tests/test_procurement_quantities.py`](../erpnext_enhancements/tests/test_procurement_quantities.py)
  — **38 bench-free pytest tests with their own CI step**, the Open rule's among them. The one that matters is
  `test_item_status_is_not_inherited_from_the_document`: three lines of one request, three
  different statuses, one parent whose label happens to be correct for exactly one of them. That
  coincidence is what let the original bug survive a casual look. Also fences the fan-out
  de-duplication against the naive-sum case, so a refactor that drops it fails rather than
  silently doubling.
- [`tests/test_procurement_print.py`](../erpnext_enhancements/tests/test_procurement_print.py)
  — bench-free unittest with its own `frappe` stub and its own CI step. Frappe's multi-PDF
  called once and unchanged, the job-named file, Project read before anything renders, an empty
  request refused, and the tracker script dialling a method that exists. The Open rule's cases
  live in `test_procurement_quantities.py`.
- [`tests/test_procurement_status.py`](../erpnext_enhancements/tests/test_procurement_status.py)
  — **bench-required**, so it does *not* run in CI. Carries the end-to-end reproduction through
  the real query, plus the draft-PO case.

- [`tests/test_procurement_overview.py`](../erpnext_enhancements/tests/test_procurement_overview.py)
  — bench-free **pytest**, own CI step: the where-now rule, the line states, the by-item sums.
- [`tests/test_project_stock.py`](../erpnext_enhancements/tests/test_project_stock.py)
  — bench-free unittest on its own `frappe` stub, own CI step: the lookup's queries, the feed's
  new functions lifted out of the package, and the UNION's two column lists held equal.
- [`scripts/test_procurement_tracker.mjs`](../scripts/test_procurement_tracker.mjs)
  — node, own CI step: the widget's pure logic.

Note the split is load-bearing: the pytest suite is bench-free and runs on every push; the
unittest-style bench suite needs `bench --site <site> run-tests`. A new *pytest* suite must be
added to a `python -m pytest` step in `ci.yml`, never appended to a unittest module list.

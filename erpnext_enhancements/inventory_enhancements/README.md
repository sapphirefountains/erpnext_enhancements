# `inventory_enhancements/` — barcode counting, storage locations and Stock Scan

A resumable physical-count workflow driven by a barcode scanner, a storage-location model
finer-grained than ERPNext's Warehouse, and — since v1.521.0 — the **Stock Scan** page: a QR
label on every stock-holding warehouse that opens a phone page to take parts, receive stock
and put it away.

Backend endpoints are in `api/inventory_scanner.py` (counting) and `api/stock_scan.py`
(Stock Scan); the count page is a Desk page here, and the Stock Scan page is a website route
(`www/stock-scan.html`, front end in `public/js/stock_scan/`). This module holds the data
model, the pure rules and the label reads.

## Contents

| Path | Purpose |
|---|---|
| `doctype/inventory_count_session/` | A resumable physical-count run |
| `doctype/inventory_count_line/` | One counted row |
| `doctype/storage_location/` | Sub-warehouse storage locations |
| `doctype/inventory_scanner_settings/` | Single — scanner configuration, and the Stock Scan page's accounts, cost center, job rule and undo window |
| `doctype/stock_scan_log/` | One row per Stock Scan save: the page's history, its Undo, the review queue for stock added without a PO, and the idempotency key |
| `page/inventory_scanner_audit/` | Desk audit view over count sessions |
| `stock_scan_rules.py` | Every judgement the Stock Scan page makes — what a scan means, whether a quantity is acceptable, who may undo, what a label says and how a sheet is laid out. No Frappe, no I/O |
| `stock_accounts.py` | `difference_account` — the one rule every Stock Entry this app builds uses for a row's other side (the Stock Scan page and the maintenance consumables issue). See below for why ERPNext's own default is not enough |
| `qr_svg.py` | QR codes as one compact inline `<path>` SVG, encoded with the `pyqrcode` Frappe already depends on. No Frappe |
| `warehouse_labels.py` | The reads behind `/warehouse-labels`: which warehouses get a label, their breadcrumbs, their QR codes |
| `item_naming_rules.py` | The Item naming schema as executable rules. No Frappe, no I/O |
| `item_naming.py` | The reads behind it — corpus, brands, reserved codes, and the audit |
| `item_naming_guard.py` | The `Item` → `validate` doc_event: refuses a *new* Item for two findings only |
| `item_naming_digest.py` | Monday's email of last week's new Items that fail the rules |
| `item_naming_triton.py` | The "Check with Triton" button — deterministic findings as context |
| `report/item_naming_audit/` | The naming work list, worst-first |

## The count flow

An **Inventory Count Session** is a persistent, resumable audit run by one clerk. Each
counted row lands in the `lines` child table with **the system-quantity snapshot and variance
captured at scan time** — not recomputed later.

That timing is the point. Stock keeps moving while a count is in progress, so a variance
computed at finalize time would be measured against a different reality than the one the
clerk was standing in.

Finalizing (`api.inventory_scanner.finalize_session`) aggregates the lines per
(item, warehouse) into a **draft** Stock Reconciliation for a Stock Manager to review and
submit; `stock_reconciliation` on the session links back to it.

Nothing in the count flow submits stock movements. The clerk counts, the Stock Manager
decides. (The Stock Scan page, below, is the opposite by design.)

A location is a Storage Location barcode **or** a Stock Scan warehouse label: since v1.521.0
`resolve_scan` reads the label URL with `stock_scan_rules.parse_scan`, and a bare warehouse
name typed at the scan box works too. The Warehouse check comes *after* Storage Location, so
a Storage Location barcode that equals a warehouse name resolves as it did before. A
warehouse location carries `storage_location: None` and the line counts against the
warehouse itself. A group or disabled warehouse comes back `unknown` with a sentence rather
than an item search pre-filled with a URL. The page's camera also works on an iPhone now: it
used `BarcodeDetector` alone, which Safari does not have, so the button never appeared there;
it now falls back to the vendored jsQR decoder (QR only — an item *barcode* on an iPhone
still wants the wedge scanner or "Find item").

**The count page's sheets are route segments, so the phone's Back button closes them.**
Camera Scan is `inventory-scanner-audit/camera` and Find Item is
`inventory-scanner-audit/find`. Frappe v16's router owns `popstate` on the Desk and closes
the open dialog on every route change, so a sheet with no entry of its own made Back leave
the count. Now Back closes the sheet and stays on the count, and Forward opens it again. The
pending-item card is part of the one screen and is not an entry, and a scan never is.

A camera read is not a tap, and Chrome's Back skips an entry a page pushes without one. So a
read is looked up on the camera's own entry. An unknown code that opens Find Item takes that
entry over. Any other result is drawn once the page has stepped back off the entry, so the
counted-qty box keeps its focus. If the lookup fails, the page steps back off the entry once
frappe's error message has been closed: sooner would close the message, and leaving the entry
behind made the clerk's next Back change nothing on screen. Whether a message is up is read from
Bootstrap's own state (`$wrapper.data('bs.modal')._isShown`), not frappe's `is_visible`: the
dialog's X is `data-dismiss="modal"`, which never calls frappe's `hide()`, and msgprint's dialog
is one for the whole session. So after any message had been closed by its X, a lookup that
failed with no dialog up (a dropped connection: frappe puts none up for status 0) waited
forever, and the camera's entry was never stepped off.

A sheet's URL opens the count, never the camera, on a pasted link and when it is reached from
another page. Only an entry the page pushed itself is a sheet's, and it marks each one in
`history.state` (no URL) as it pushes it: Frappe's Route History records every route with a
second segment and the awesome bar offers the most used as links, and a sheet opened from one of
those would have another page behind it for X, or a camera read's step back, to land on. When
such a link is picked on the count itself, the URL is pushed over the count's own entry, and
replacing it would leave two count entries in a row, so the page steps back onto its entry
instead. It knows the count was showing from the `hide` frappe fires on the page it leaves: none
since the last show means nothing else has been. A reload on a sheet entry the page pushed
steps back too: `history.state` survives a reload, and the entry behind a marked one is always
the count's own. Each replace the page asks for (`route_flags.replace_route`) is cleared the
moment `set_route` returns. v16 reads the flag while writing the entry but clears it only once
every request then in flight has landed, and on a first show that includes the bootstrap call:
left set, it turned the clerk's next tap into a replace of the count's own entry, so X on Find
Item, tapped before the bootstrap landed, left the page.

A camera read's reply acts only on the entry the read was taken on, and only while that entry
still holds nothing but the lookup. A camera the clerk opens before the reply lands (on that
same entry, or on a new one after Back) has taken it over, as has a later read, and the reply is
then drawn where the clerk is: it used to step back off the clerk's new camera, closing it, or
replace it with Find Item for the old code.

An unknown code opens Find Item only where it was scanned. A lookup's reply that lands after
the clerk has moved (Back, Forward, a sheet tapped open, another Desk page) used to route
anyway, which dragged the clerk back onto the count from wherever they had gone, or pushed an
entry with no tap behind it; now the code is only reported there. Back or Forward onto a
sheet's entry that cannot be opened again (the camera, with camera scanning since turned off)
steps back onto the count's own entry rather than replacing it with a second copy. The Finalize
message links the draft reconciliation with `get_form_link` (a /desk path), not the
server's /app `reconciliation_url`, which is a full page load on v16. Behaviour tests:
`scripts/test_desk_page_history.js`, run by `tests/test_desk_page_history.py`.

## The Stock Scan page

What Nik asked for, 2026-09-23: a QR code on every location that holds inventory; scan it,
see the items there, press − or +, save, scan the next one. A technician takes what a job
needs; a receiver adds what arrived. `/stock-scan` is that page, `/warehouse-labels` prints
the codes, and `api/stock_scan.py` is every endpoint either of them calls.

**It is on the Desk** (v1.523.0), because nearly everyone who touches inventory works through
it: a **Stock Scan** tile on the `/desk` home grid, beside ERPNext's Stock tile, and a *Stock
Scan* entry in the Home workspace's Desk Shortcuts block — both seeded by
`patches/seed_stock_scan_shortcuts`, both shown only to the page's own roles (Stock User,
Stock Manager, Inventory Clerk, System Manager). The tile is an `External` link like the Time
Kiosk's, and like it renders only because a same-named Workspace Sidebar
(`workspace_sidebar/stock_scan.json`) ships beside it: `get_desktop_icons` drops a `Link` tile
with no sidebar items without a word.

### A label is a URL, not a code

Each label encodes `https://<site>/stock-scan?w=<warehouse name>`
(`stock_scan_rules.scan_url`). The phone's own camera app opens a URL straight into the
browser, so the first scan of a run needs no app and no button — a bare code would open a web
search. From there the page's own scanner reads the next label without leaving the page,
which matters because iOS asks for camera permission again on every page load. The in-page
scanner reads the same labels, and so does the count page, so one label serves all three.

`parse_scan` accepts **any host**: a label printed from the test site still resolves on
production, where the warehouse has the same name. A URL to some other page is returned as
text, never followed. The query string (rather than a path) is deliberate — see
[`www/README.md`](../www/README.md#stock-scan--the-stock-scan-page).

### Four actions, four vouchers, all submitted

Every Save **posts immediately**. That is what the people using it asked for, and it is why
Undo exists.

| On the page | Log `action` | Voucher | Notes |
|---|---|---|---|
| **−** Take | `Take` | Stock Entry, **Material Issue** from the scanned location | Optionally charged to a job picked once per run; the cost reaches the Project through ERPNext's own `update_cost_in_project`. *Require a Job for Every Take* in the settings makes the job mandatory |
| **+** against an open order | `Receive` | **Purchase Receipt** into the scanned location | Through `api.procurement.receive_order_line` → `receive_items`: the same checks, the same `make_purchase_receipt` mapper and the same over-receipt rule as the order's *Receive Items* dialog |
| **+** returned from the run's job | `Add Without PO` (with the job) | Stock Entry, **Material Receipt** at the item's current cost, tagged with the job | Offsets to the *Parts Taken* account, so the credit reverses the account the take charged and the job nets by project in the ledger. Flagged `needs_review` |
| **+** found, or not from a job | `Add Without PO` (no job) | Stock Entry, **Material Receipt** at the item's current cost | Offsets to the *Added Without PO* account. Flagged `needs_review` for a Stock Manager |
| **Move here from…** | `Move` | Stock Entry, **Material Transfer** into the scanned location | Put-away: from wherever the stock is recorded |

**"+" is PO-first, and the fallback is a choice, never a default.** The item's open order
lines are offered first, oldest promise first (`order_line_sort_key`). "Open" is ERPNext's own
"still on order" rule (`stock_balance.get_purchase_order_qty`) plus On Hold, which a receipt
refuses — an exclusion list, because v16 shows an order waiting on an advance as *To Pay*
with nothing received — and what is left is compared with `procurement_quantities.TOLERANCE`,
the receive planner's own, so the page never offers a line the receipt would then call fully
received. After the lines, while a job is picked for the run, comes **"Returned from <job>"**
(`without_po: 1` *with* the job), and last **"Not on a purchase order — found, or not from a
job"** (`without_po: 1` and **no** job); with no open line and no job the page just asks to
confirm the second. The job is sent only when the person says the parts came back from it, so
found stock is never booked against whatever job the run happened to have.
One ERPNext limit to know: `Project.total_consumed_material_cost` sums Material Issue rows
only, so a return does not reduce it (only cancelling the issue does). The ledger by project
*is* right, because the return credits the same account the take debited; that field is
hidden on this site's Project form and nothing here reads it. With neither a line
nor `without_po`, `add` refuses to post: receiving stock that *is* on an order without the order
double-counts it on the day the order's own receipt arrives.

The page counts in the item's stock UOM; an order line may be a Box of 10.
`to_order_uom` converts and **refuses** a quantity that is not whole in a whole-number UOM
rather than rounding it — rounding would receive goods that did not arrive. A receipt row's
project must equal its order line's (`validate_with_previous_doc`), so the job on a receive
comes from the order, not from the page. And the order's `Bin.ordered_qty` falls at the
*line's* warehouse while `actual_qty` rises at the *scanned* one — "on order" drops where it
was ordered for, "on hand" rises where it was put, which is right. Against an order line that
came from a Material Request, a scan receipt fires the *Material Request Received*
notification exactly as any receipt does — and an undo followed by a re-receive fires it again.

### Why every row names its own difference account

**Production's Company has no Stock Adjustment Account.** ERPNext looks for a Stock Entry
row's difference account in the Item Default, then the Item Group, then the Company — and on
production all three are empty, so a Material Issue refuses to insert with "Please enter
Difference Account or set default Stock Adjustment Account". It is also mandatory on a
Material Transfer, where both sides of the GL net to nothing.
`stock_accounts.difference_account` walks ERPNext's order, puts the account chosen in
Inventory Scanner Settings where ERPNext would read the Company's (*Parts Taken: Expense
Account* for a take, a move, a return from a job and the maintenance consumables issue;
*Added Without PO: Offset Account* for found stock), then the
Company's, then **the company's one leaf Stock Adjustment account** (`5119 - Stock Adjustment -
SF` on production), and refuses rather than guesses if there are several. A configured account
that is a group, disabled, another company's or `Stock`-typed is skipped, not posted to.
Setting the Company's Stock Adjustment Account would fix desk Stock Entries and Stock
Reconciliations too; that is a settings decision for accounting, so nothing here depends on it.

The maintenance consumables issue (`api/maintenance_workflow.create_stock_entry`) shipped
without a difference account *and* without a `stock_entry_type` (first bullet below), and had
simply never run on production. Since v1.521.0 it uses the same resolver and sets the type,
and `tests/test_stock_entry_builders.py` fails the build on any Stock Entry builder that does
not.

Three more things the obvious version gets wrong, each verified against v16:

- **`stock_entry_type` is always set.** It is mandatory, and `purpose` is *fetched* from it
  on insert; setting only `purpose` raises `MandatoryError`.
- **A Material Receipt carries an explicit `basic_rate`.** Without one, insert-then-submit
  fails with "Valuation Rate Missing" — and submitting straight from new posts the stock at
  **zero** value, which makes every later issue of it cost nothing. `_receipt_rate` takes the
  last rate at this location, then the company-wide average, then the last purchase rate or
  the Item's Valuation Rate — never the selling price — and a zero refuses the save.
- **A take with a job re-saves the Project.** `update_cost_in_project` runs a full
  `project.save()` inside the submit, with every Project hook in `hooks.py`. A Project that
  cannot be saved makes the take fail, and anyone with that Project open in the Desk gets a
  "document has been modified" on their next save.

Serial, batch, variant, customer-provided and non-stock items are refused up front
(`item_refusal`): the page shows them but withholds the stepper. ERPNext would auto-pick serial
numbers FIFO on an issue, which is wrong for serialised equipment somebody may claim a warranty
on. A take larger than what is on hand is refused before a document exists, in words that say
what to do (`check_take`).

**Permissions are the framework's.** After the role gate (`SCAN_ROLES`: Stock User and up,
plus Inventory Clerk) every voucher is inserted, submitted and cancelled **without**
`ignore_permissions`, so User Permissions on Warehouse or Project still apply and a user who
could not post the voucher in the Desk cannot post it here. Only the log row — this page's own
record — is written with `ignore_permissions`.

### Undo, and why a retried save cannot post twice

**Undo cancels the voucher, as the session user.** The person who saved it may undo it for
the *Undo Window* (Inventory Scanner Settings, default 30 minutes; `0` switches it off for
everyone but Stock Managers); a Stock Manager may undo any save at any time, which is the
power they already have over the voucher in the Desk (`undo_refusal`). ERPNext still decides
whether the cancel is possible: undoing a receipt whose stock has since been taken would drive
the location negative and is refused, and a receipt cannot be cancelled once its order is
Closed or On Hold. Every cancel queues a Repost Item Valuation, which the scheduler works off.

**`client_ref` makes a save idempotent.** The page mints one per intended save and sends the
same one on a retry after a dropped connection. It is **unique** on the log, and the log row is
inserted *before* the voucher in the same transaction — so a double tap or a retry finds the
first save and returns it (`repeated: true`) instead of posting a second, and a save whose
voucher fails rolls its log row back with it. One retry needs its own answer: one that arrives
while the first attempt is **still submitting** (a slow Purchase Receipt outlasting the page's
30-second timeout) finds no committed row, waits on the unique key, and then collides. That is
not a refusal — the first attempt is about to commit — so `_begin_log` raises it as
`DuplicateEntryError` (HTTP 409), and the page treats a 409 like a dropped connection: it keeps
the same `client_ref`, and the next tap gets the first save back. Answered as an ordinary 417,
the page would mint a fresh reference and post the save twice.

The server keeps every reference forever, so the page keeps one for a retry for **ten minutes
only** (`logic.keptRef`, `RETRY_WINDOW_MS`): the same numbers saved later are a new save, not an
"already saved" that posts nothing. An answer with `repeated: true` is said as exactly that —
"That save was already recorded — nothing new was posted" — and the dialled change stays, so a
person who did mean a second one taps Save again and gets a fresh reference.

### The labels page

`/warehouse-labels` prints a label for every warehouse that can hold stock: a leaf, not
disabled, not a Transit warehouse (goods in transit sit on a truck, not a shelf). Each carries
the warehouse's name, the breadcrumb of its groups (`Row 2 › Bay B1 › Shelf B1-2`, the tree
root dropped) and the QR code, drawn server-side as inline SVG by `qr_svg.py` so what prints is
exactly what the preview shows. Sheets are Avery 5160 (30 up), Avery 5163 (10 up) or one 2×1 in
label per page for a label printer (`LABEL_PRESETS`; `preset_fits_page` checks that each
preset's margins, labels and gaps add up to its page, so a typo cannot shift every label a
sixteenth off its die-cut). Labels sort
**naturally** — `Bin B1-2-9` before `Bin B1-2-10` — because the tree's own order is creation
order, and on production that already reads `Bin C2-3-6` before `Bin C2-3-5`: a sheet peeled onto
a shelf in that order puts two labels on the wrong bins.

The Warehouse form carries the doors (`public/js/warehouse_stock_scan.js`): **QR Label** and
**Open Stock Scan** on a location, **Print QR Labels** on a group for everything beneath it.

### Day one: the bins are empty

On the day this shipped all stock sat in `Stores - SF` and `Inventory Room - SF`; the bins under
Inventory → Row → Bay → Shelf — most of the site's 176 leaf warehouses — held nothing. So a
technician who scans a bin and
presses − gets "The system shows none on hand here … use Move here", and that is correct: the
ledger says the part is in Stores. **Move here** (a Material Transfer) is how the bins fill, and
the first weeks are a put-away exercise before Take is useful. Two more facts from the same
day that shape the page: only 111 of 824 Items maintain stock (the rest open as "not tracked"),
and no Item has a barcode or an image, so items are found by location, by name, or by search,
and show a monogram tile.

**Watch** the `needs_review` queue (Stock Scan Log, *Needs Review* ticked, *Reviewed* not): it is
every unit that entered stock without an order, at a cost the page chose. Since v1.530.0 it is a
KPI, *Adds Without PO Awaiting Review*, on the Operations dashboard, which is now the inventory
dashboard: store runs, stocked items below reorder or out, stock at a placeholder cost,
unpriced PO lines and count coverage. See
[`kpi_dashboards/README.md`](../kpi_dashboards/README.md#service-split-off-operations-operations-is-inventory-v15300)
for the definitions, and for the *Store-Run Vendor* flag on Supplier that the store-run KPI
reads.

## Item naming

`item_naming_rules.py` implements the *ERPNext Item Naming Schema* SOP v1.0
([`docs/item-naming-schema.md`](../../docs/item-naming-schema.md)): the seven-segment
`item_name` schema, the four Item Code families, and the approved category vocabulary.
`item_naming.py` does the reads; `assistant_tools/item_naming_check.py` is the MCP surface.

**It is advisory, except for two findings on a new Item.** The SOP says compliance is
procedural, because ERPNext applies no naming series to Item, and a third of the live
catalogue would fail the comma rule, so a block on the full rule set would fire constantly on
legitimate edits to records that were already there.

### What refuses a save (v1.532.0)

Nik decided on 2026-09-24 (TASK-2026-02238; POL-0602 v1.0, effective 2026-10-01) that the
app's first `Item` doc_event, `item_naming_guard.validate_new_item` on `validate`, refuses
saving a **new** Item for exactly two findings, both from `item_naming_rules.blocking_findings`:

- **`duplicate_code_normalised`**: the code matches an existing Item's code once case and
  punctuation are ignored (`806020` against `806-020`). The Item's own code is excluded
  exactly, never by its normalised form, for the reason given under *A proposal and a saved
  record* below. An exact duplicate is ERPNext's to refuse; `item_code` is the primary key.
- **`name_equals_code`**: the name is just the code. A blank name counts, because ERPNext's
  `Item.validate` copies the code into a blank name before any doc_event runs.

**Why only two.** Refusing every STOP was the obvious rule and was rejected. The STOP set
includes `name_category_unapproved`, and several category words are still waiting on a ruling
(TASK-2026-02215: PLMB, BRUSH, BOTTLE), so it would refuse legitimate new items for as long as
a ruling is open. Neither of the two chosen findings depends on a ruling.

**When it runs.** From `item_naming_rules.NAMING_GO_LIVE` (2026-10-01, POL-0602's effective
date), not from the deploy: the conventions were still being finalized the week it shipped, and
the new-items KPI counts from the same day. Only on `is_new()`, so an existing Item is never
refused whatever its name. Never on a variant (`variant_of` set): ERPNext derives a variant's
code and name from its template, and a manufacturer variant copies no name at all. Only inside
a web request, and never while `frappe.flags` has `in_import`, `in_migrate`, `in_install`,
`in_patch`, `in_test` or `in_setup_wizard` set. A background job has nobody to read the
message: the QuickBooks sync creates Items on the scheduler, and a refusal there would park the
record for manual review. Data Import normally runs as a background job too; `in_import` covers
its inline runs. A save is also skipped when `doc.flags.ignore_naming_guard` is set, which only
in-request callers whose user cannot choose the code or the name set:

| In-request Item creator | Guard |
|---|---|
| `product_configurator.erp_integration._ensure_product_item`, the configured product | **Off**. The configurator allocates the part number and the person can change neither it nor the name. The name (`<product> <code>`) can never be just the code, so the flag only exempts part numbers from the case- and punctuation-blind duplicate check; "Item Code Taken" still refuses an exact clash. Nothing reports a near-clash automatically: the digest's `audit` compares names across records, not codes. *Naming → Check naming* and the MCP tool show one on request |
| `product_configurator.erp_integration.ensure_component_items` | On. Component names are the product definition's own words, and a required field |
| `quickbooks_online.core.mapping` create path, for Items | **Off**. A QBO Item with no SKU is name == code by construction, and the dashboard's per-entity Sync runs it inside a request |
| `accounting_intake.review._create_item` (Approve Items) | On. The Stock Manager enters the line's *Proposed Item Code*; `_naming_problems` checks every line with `blocking_findings` before the first insert and refuses the batch naming each line and what to fix. A line with no code would make the name the code, so it is told to enter one |
| `water_engineering.setup` catalogue seeds | On, but they run from `after_migrate`, where the guard is skipped |

The message names the clashing codes, or asks for a descriptive name in schema order (and,
when the name already is one, says the code is what needs changing), and says that nothing
else stops the save.

### The weekly digest (v1.532.0)

`item_naming_digest.send_weekly_digest`, Monday 07:00 site time (`0 7 * * 1`), emails the Items
created in the last seven days that do not PASS, `(deleted)` tombstones excluded, with their
STOP and FIX findings, who created each one and a link to it. The recipients are *Weekly Naming
Digest Recipients* in Inventory Scanner Settings, which has no default;
`patches/seed_naming_digest_recipient` writes the Purchasing Agent's address once, only where
the field has never been stored. Blank sends nothing, and so does a week with nothing failing.
It goes through `email_style` like every other sender.

The rules module lives here rather than under `assistant_tools/` because nothing in the app
outside `assistant_tools/` and `tests/` may import that package (`TestFacOptionalInvariant`),
and Item-master vocabulary has to stay reachable from a report or a patch.

### Six callers, one engine

`item_naming_rules.py` is the only place a naming judgement is made. Everything else is a thin
caller, and that is load-bearing rather than tidy: two definitions of "compliant" that disagree
by one row is a bug report nobody can close.

| Surface | Entry point | Notes |
|---|---|---|
| **Item Naming Audit** report | `report/item_naming_audit/` | The work list. Worst-first, tombstones hidden by default |
| **Item form** | `public/js/item_naming_advisor.js` | Headline on refresh, two buttons under *Naming* |
| **KPIs** `item_naming_compliance_pct`, `item_naming_new_compliance_pct` | `kpi_dashboards/snapshots.py` | Nightly, on the Product dashboard. The second covers items created on or after `NAMING_GO_LIVE` (2026-10-01) |
| **MCP** `item_naming_check` | `assistant_tools/item_naming_check.py` | For Triton and any MCP client |
| **New-Item guard** | `item_naming_guard.py` | Two findings only; see above |
| **Weekly digest** | `item_naming_digest.py` | Last week's new Items that fail |

The KPI for new items and the digest both audit the **whole** catalogue and then keep the rows
they want (`restrict_to`). Auditing only the new items would miss a new item named exactly like
an old one.

Two costs worth knowing before editing the form script. `refresh` calls `check_item(mode="record")`,
which reads **no corpus** — a full check on every form open would read the whole catalogue every
time anybody looked at an Item. Only the buttons call `mode="full"`.

And `audit()` is deliberately **not** `evaluate()` in a loop: `evaluate` scores near-neighbours by
document frequency across the whole corpus, so per-record it is O(n²). `audit()` does one pass of
per-record checks plus one grouping pass for collisions. `tests/test_item_naming_rules.py` poisons
`similar_records` to assert `audit()` never reaches for it, because the obvious simplification
does not look wrong.

### A proposal and a saved record ask opposite questions

`evaluate(..., existing=)` is not a nicety. `item_code` is the primary key, so an exact code
match in the corpus means *a collision* when checking a proposed new Item and *this very row*
when re-checking a saved one. Getting it wrong made every saved Item a STOP (v1.337.1). The form
always sends `existing: 1`; the MCP tool exposes it and defaults to false.

Self-exclusion is matched on the **exact** code, never the normalised one — otherwise it would
also drop a punctuation-variant sibling, which is the collision `duplicate_code_normalised`
exists to find.

### The Triton button

`item_naming_triton.py` runs the deterministic check **first** and sends its findings to Triton as
context, so the model is asked only for what the rules refuse to decide — parsing a vendor
description into segments, choosing a category, and whether two records are the same physical
part. The prompt declares the findings authoritative; without that the model re-litigates them,
and a confident second opinion that contradicts a regex is worse than none.

It goes through `triton_chat._request` rather than being a third Triton client. That seam already
refuses when the assistant is off, mints the token **under the current session user**, retries a
stale-token 401, and scrubs URLs. The rule behind that seam is **never impersonate on the
request path.** The client this warning used to name — `chat/invoke/triton_client.ask()` — went
with the chat module in v1.426.0, but the trap it modelled is a property of Frappe, not of that
file: `frappe.set_user` overwrites `session.sid` with the username and empties the session data,
so calling it unconditionally inside an HTTP request destroys the caller's live session and the
`finally` restore does not undo it. "Expand with AI" shipped that way as v1.325.0 and logged
people out one click into their session (fixed in v1.325.1). This is a desk button: a client
borrowed for it must impersonate **only when the target differs from the session user**, the way
`product_feedback/triton_client._call_as` does.

### Two traps that made the obvious query wrong

Both are why block occupancy is decided in Python and asserted in CI rather than written as a
MariaDB regex, and both were live in a hand-written validator prompt.

- **`item_code REGEXP '^PDT-[0-9]{4}$'` reports `PDT-0008` free.** The `$` rejects the trailing
  text on `PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy`, which is the only record of that
  product. Trailing text does not free a number.
- **Unanchoring it invents taken numbers.** `CAST(REGEXP_SUBSTR(item_code,'[0-9]+'))` swallows
  the five-digit QuickBooks family — `PDT-00000 (deleted)` … `PDT-00013`, `PDT-00040`, and the
  live `PDT-00051` — collapsing them onto four-digit slots that are genuinely free. **`PDT-0051`
  and `PDT-00051` are different items.**

`block_slot()` is the single place that boundary is decided: exactly the declared width in
digits, not followed by another digit, trailing text allowed.

A third trap belongs to the name checks rather than the codes: **MariaDB's PAD SPACE collation
makes `item_name <> TRIM(item_name)` always false**, so the SQL form of the trailing-whitespace
check returns 0 on a corpus with three offenders. Compared by length, in Python, where a
trailing space is still a character. Use `BINARY` if you must write it in SQL.

## Tests

Needs a bench (a `FrappeTestCase`; not in CI):

```bash
bench --site <site> run-tests --app erpnext_enhancements --module erpnext_enhancements.tests.test_inventory_scanner
```

Bench-free:

```bash
python -m unittest erpnext_enhancements.tests.test_item_naming_rules -v
python -m unittest erpnext_enhancements.tests.test_item_naming_guard -v   # frappe stub
python -m unittest erpnext_enhancements.tests.test_item_naming_digest -v  # frappe stub
python -m unittest erpnext_enhancements.tests.test_stock_scan_rules -v    # needs PyQRCode~=1.2.1 for the QR half
python -m unittest erpnext_enhancements.tests.test_stock_scan_surface -v
python -m unittest erpnext_enhancements.tests.test_stock_scan_theme -v
python -m unittest erpnext_enhancements.tests.test_stock_entry_builders -v
node scripts/test_stock_scan_client.mjs
```

`stock_scan_rules.parse_scan` decides every scan — the page sends the raw text to `resolve`
and acts only on the answer. The page's JS twin, `parseScan`, has one job: naming the label in
the error it shows when the server could not be reached ("Couldn't open Bin B1-2-10 - SF: …").
`tests/data/stock_scan_parse_vectors.json` is read by both `test_stock_scan_rules` (Python
`parse_scan`) and `scripts/test_stock_scan_client.mjs` (JS `parseScan`), so the two stay
identical and that name is the one the server would have looked up.

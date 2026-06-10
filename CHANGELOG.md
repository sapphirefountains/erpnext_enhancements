# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.1] - 2026-06-09

### Fixed
- **Kanban hold-to-drag now actually reaches phones and tablets.** The press-and-hold patch suite shipped as raw `/assets/erpnext_enhancements/js/kanban_*.js` scripts, and the server serves `/assets` with `Cache-Control: max-age=31536000, immutable` (verified on the live site) — so a mobile browser keeps executing the *first* copy of each file it ever downloaded, for up to a year, without revalidating even on a normal reload. Desktops used for testing get hard-refreshed; phones never do, which is exactly why the 1-second hold worked with a mouse while touch devices kept grabbing cards instantly when scrolling. The four `kanban_*.js` patches now ship as a single esbuild bundle (`public/js/kanban.bundle.js`, referenced as `kanban.bundle.js` in `app_include_js`, same mechanism as `desk_enhancements.bundle.css`): the built filename carries a content hash, so every deploy gets a new URL and every device — including the stale phones — picks up the current code on its next page load. This was very likely also the root cause of the earlier "the delay never seems to apply" round documented in `kanban_patches.js`. Code-wise nothing else moved: the bundle just imports the four files in their old include order.
- **Backported SortableJS 1.15.4's `pointercancel` handling into `kanban_patches.js`.** Frappe pins SortableJS 1.15.0, whose delay branch cancels a pending hold on `touchend`/`touchcancel`/`mouseup` and on >threshold movement — but never listens for `pointercancel`. Phones are covered (touch events fire alongside pointer events), but on pointer-only inputs (pen/stylus, some Windows-touch configurations) the browser fires *only* `pointercancel` when it claims the gesture for native scrolling, so the pending 1s timer survived the scroll takeover and could fire mid-scroll, grabbing a card nobody was pressing. A document-level `pointercancel` listener now aborts any pending delayed drag (guarded so it never touches a drag that already legitimately started).
- **Every other global desk include migrated to content-hashed bundles too** — the same stale-cache landmine applied to all of them (Comments App, Triton widget, telephony, sidebar/awesomebar/drafts patches, activity numbering, filter help, task tree/gantt preloads, and the five feature stylesheets). New esbuild entries `public/js/erpnext_enhancements.bundle.js` (17 scripts, old include order) and `public/css/desk_addons.bundle.css` (5 stylesheets, listed after `desk_enhancements.bundle.css` so the cascade is unchanged) replace all raw `app_include_js`/`app_include_css` paths. Audited first: every bundled file is a self-contained IIFE or exposes itself via explicit `frappe.provide`/`window` assignment; the two files with top-level declarations (`erpnext_enhancements.js`, `activity_log_numbering.js`) have no external consumers of those identifiers. The two vendored UMD libraries (`vue.global.js`, `frappe-gantt.umd.js`) deliberately stay raw, now loaded first: a bundle import would capture their exports instead of setting `window.Vue`/`window.Gantt`, and their content never changes so stale caching cannot affect them. `doctype_js` files are untouched (`frappe.require` has a version-aware client cache).
- **Login page no longer requests a non-existent stylesheet.** `web_include_css` pointed at `/assets/erpnext_enhancements/css/login_enhancements.css`, which 404s — the on-disk file is `login_enhancements.bundle.css` (confirmed live: the login page requested both the 404 path and the built bundle). The hook now references the bundle name.

## [0.8.0] - 2026-06-09

### Added
- **The last load-bearing DB-only custom DocTypes are now app DocTypes**: `Process Document` (Mermaid.js process docs, 11 documents, form script already shipped in `public/js/process_document.js`) → Enhancements Core; `Sales Activity Settings` (Single) → CRM Enhancements; `Additional Supplier Group` (child table behind `Supplier.custom_additional_supplier_groups`) → Global Enhancements. Generated with frappe's canonical export serializer from the live definitions (only `custom`/`module`/`modified` differ), same as the v0.7.0 port.

### Changed
- **`customer_inactivity_reminder` now has a global fallback.** Customers without a positive per-customer `custom_reminder_days` fall back to the `inactivity_threshold` from the now-shipped Sales Activity Settings Single (live value: 90 days); `custom_reminder_days = -1` opts a customer out, and setting the global threshold to 0 disables the fallback site-wide. Previously such customers were skipped entirely. **Measured against live data, the first daily run after deploy creates ~694 follow-up ToDos** (the backlog of long-inactive customers — 286 owned by Administrator, 212 brian.morisseau, 183 nikolas.bradshaw, the rest spread thin); warn those owners, set the global threshold to 0 until ready, or prune by owner afterwards. (The old DB Server Script "Customer Inactivity Notification" that read this Single is already disabled; app code is now its only consumer.)

### Fixed
- **Follow-up ToDos are now actually assigned.** The ported reminder (and the original server script before it) set `assigned_to`, a field that does not exist on Frappe v16's ToDo — the key was silently dropped, leaving every follow-up ToDo it ever created unassigned and invisible in assignees' lists (confirmed on live: existing Open customer ToDos all have `allocated_to = NULL`). The insert now sets `allocated_to` (the Customer's owner).
- **`setup/supplier_groups.py` no longer creates the "Additional Supplier Group" DocType at runtime** — it ships with the app and is synced by doctype sync before the `after_migrate` hook runs.

### Removed
- **Three abandoned DB-only DocTypes are deleted by patch `delete_abandoned_doctypes`** (sign-off: Nikolas, 2026-06-09): `Materials` (0 rows), `Rental Status` (0 rows), `Water Feature Types` (1 orphan row; superseded by the Serial No migration). All three were referenced by nothing — no DocField, Custom Field, script, or repo code. The patch also deletes the disabled "Mermaid.js Render" Client Script, superseded by the app's Process Document form script. **Deleting a DocType drops its table.**

## [0.7.0] - 2026-06-09

### Added
- **17 DB-only custom DocTypes ported into the app** (closing the fresh-install gap documented in `fixtures/README.md`): the 16 child tables referenced by `fixtures/custom_field.json` Link/Table fields (Accounts Lead/Opportunity/Project, Lead Source, Opportunity Contributor, Value Stream, Project Notes, Project Stakeholder, and the Build/Design/Rent/Service Customer Requests + Deliverables tables) plus the transitively required **Value Streams** master. Each now lives as a standard app DocType under `crm_enhancements/doctype/` (7, from the live `CRM` module) or `project_enhancements/doctype/` (10, from `Projects`), generated with frappe's own canonical export serializer from the live definitions — only `custom` (removed), `module` (remapped) and `modified` (stamped) differ. Because app doctype sync runs **before** fixture sync on migrate and fresh install, `custom_field.json` now imports cleanly on a from-scratch site; previously frappe skipped the entire 425-record file at the first missing Link target.
### Changed
- On the live site, the first migrate of this release flips the 17 from `custom = 1` to app-owned — verified non-destructive against the deployed Frappe 16.20.0 source (the reload path deletes only doctype metadata rows, never the data tables; row counts such as Value Stream's 1310 are untouched). Their definitions are henceforth edited in the repo (UI editing of standard DocTypes requires developer mode), extending the repo-as-source-of-truth model from Phase 2 to the DocTypes themselves. Any UI edit made to these 17 DocTypes between this export and the deploy is overwritten by the repo definitions — deploy promptly.
- **Lead Source**: this ERPNext v16 install no longer ships its historical `Lead Source` doctype (verified: no `erpnext.crm.doctype.lead_source` module on the bench, `Lead.source` absent), so the port is collision-free; if a future ERPNext upgrade reintroduces it, ours must be renamed first. The live record is a one-field husk referenced only by `Customer-custom_lead_source` — ported faithfully, cleanup is a separate decision.

## [0.6.0] - 2026-06-09

### Changed
- **The repo is now the source of truth for all manual customizations (Phase 2).** `fixtures/custom_field.json` and `fixtures/property_setter.json` now carry every manually created Custom Field (425) and Property Setter (349) from the live site — re-exported fresh today and verified byte-identical to the Phase 1 snapshot — and the `fixtures` hook exports/syncs everything with `is_system_generated = 0` minus six records owned by other apps (see `fixtures/README.md`, the new authoritative spec). On deploy, `bench migrate` re-applies these files; Customize Form changes on the site no longer survive fixture-touching deploys. **Back up the DB before the first deploy of this release** (the first sync re-writes values identical to what the site already holds, so it is expected to be a functional no-op).
- **`create_comments_tab` (after_migrate) is now insert-only.** It previously rewrote the fixture-owned Project Comments tab/field with `update=True` and a recomputed `insert_after` on every migrate — running *after* fixture sync, it would have silently overridden any future fixture edit. It now only creates missing fields (fresh installs, Master Project).

### Removed
- **`crm_enhancements/custom/opportunity.json` and `project_enhancements/custom/project.json`** (`sync_on_migrate` customization channels). All 16 of their live-matching records are now fixture-owned; the 17th, `Project-total_expense_claim`, was a frozen 2025 copy of an HRMS-owned field (`is_system_generated = 1`) that the file re-imposed on every migrate — ownership returns to HRMS. These files synced *after* fixtures in the migrate pipeline and would have masked fixture edits.
- **The dead `custom_fields` dict in hooks.py.** Frappe core never reads an app-level `custom_fields` hook and no consumer exists in this repo; its `Project-custom_drive_folder_id` definition had silently drifted from the live record (which the fixtures now own). Corrected the false provenance claim in `crm_enhancements/README.md`.
- **`project_enhancements/setup_address.py`** (manual `bench execute` installer). Its definitions for the two Address map fields had drifted from live, and a re-run would have inserted an orphan `custom_map_section` that exists in no fixture. The fixtures own the real records.
- **`customizations_snapshot/`** (Phase 1). Superseded: the fixture files now carry the same content, and the snapshot README's spec moved to `fixtures/README.md`.

## [0.5.0] - 2026-06-09

### Added
- **Version-controlled snapshot of all manual Customize Form customizations** (`customizations_snapshot/`). All 425 manually created Custom Fields and 349 Property Setters were exported from the live site (read-only, via MCP) and committed as record-keeping JSON — Phase 1 of moving customizations into version control. The directory sits outside the app package's `fixtures/`, so **nothing is imported or applied on migrate**; promoting it to enforced fixtures is a deliberate Phase 2 change. Six records flagged "manual" on the site but actually owned by installed apps (3× `lms` User fields, 1× `frappe_assistant_core` User field, the LMS Certificate default print format, and the workflow engine's auto-created `workflow_state` field) are deliberately excluded — see `customizations_snapshot/README.md` for the audit trail.

### Fixed (documented, not yet applied)
- The audit found `erpnext_enhancements/fixtures/custom_field.json` has drifted from the live site (7 records, mostly comments-tab `insert_after` positions) and wrongly includes the HRMS-owned `Project-total_expense_claim` via its broad `dt = Project` filter. Both are documented in the snapshot README as Phase 2 work; no fixture behavior changes in this release.

## [0.4.0] - 2026-06-09

### Added
- **Full dark-mode ("Timeless Night") support across all customizations.** Every customization now tracks the active Frappe v16 desk theme — **Frappe Light** and **Timeless Night** — instead of assuming a light background. Detection follows Frappe's own mechanism: the *resolved* theme published on `<html data-theme="light|dark">`, so the user's "Automatic" preference is handled for free. CSS keys off `[data-theme="dark"]`; JavaScript reads `document.documentElement.getAttribute('data-theme')` only where a resolved colour string is actually required. Hardcoded colours were replaced with Frappe desk variables (`--card-bg`, `--bg-color`, `--control-bg`, `--subtle-fg`, `--fg-hover-color`, `--text-color`, `--text-muted`, `--border-color`, `--primary`, `--popover-bg`) that auto-switch between themes. Saturated semantic/status colours (success/danger/warning, value-stream and gantt data-viz palettes) and the print/portal templates were intentionally left literal.

### Fixed
- **Stylesheets converted to theme variables.** [`task_enhancements.css`](erpnext_enhancements/public/css/task_enhancements/task_enhancements.css), [`task_tree.css`](erpnext_enhancements/public/css/project_enhancements/task_tree.css), and the Custom HTML Block [`projects_dashboard.css`](Custom%20HTML%20Block/projects_dashboard.css) were fully converted from hardcoded `#fff`/`#333`/`#ddd` surfaces, text and borders to Frappe desk variables. The dashboard's local frappe-gantt palette (`--g-*`) gained an `html[data-theme="dark"]` override mirroring the vendored gantt stylesheet, and its hardcoded SVG text fills now use the themed `--g-*` variables.
- **JavaScript-injected styles** in 15 desk scripts now use theme variables — Portfolio Gantt popups, Project gantt/heatmap/dependency-link styles, the file-manager/file-preview tiles, filter-help mock inputs, the comments UI, contacts/addresses tables, and the column-selector dropdown. The Project Brief follows the theme on screen but keeps an `@media print` block so printed briefs stay dark-on-white. Canvas/image exports (`domtoimage`) resolve `--card-bg` via `getComputedStyle`, which cannot parse `var()`.
- **Server-rendered HTML** now emits theme variables: the Opportunity→Project notes block ([`crm_enhancements/api.py`](erpnext_enhancements/crm_enhancements/api.py)) and the Task hierarchy `<style>` block ([`task.py`](erpnext_enhancements/task_enhancements/doctype/task/task.py)).
- **The Projects Dashboard shell** ([`projects_dashboard.html`](Custom%20HTML%20Block/projects_dashboard.html)) dropped fixed-light Bootstrap utilities (`bg-white`/`bg-light`/`btn-white`) that glared in dark mode, in favour of theme-aware surfaces and `btn-default`.
- **Dark-mode contrast bugs in already-themed files.** The Triton assistant's mermaid diagram box (a white panel inside the dark chat), the high-value Opportunity kanban card (deep-navy card with no edge against the dark desk), and three Time-Kiosk surfaces (outline button, badge, inactive tracking dot) now have proper dark-theme treatments.
## [0.3.4] - 2026-06-09

### Changed
- **Time Kiosk PWA icon/favicon is now the Sapphire Swirl.** Replaced the placeholder clock glyph with the Sapphire Swirl brand mark — the `#00a0dd` swirl on a transparent field — for the standard icon and favicon, and regenerated the 192/512 PNG raster versions. The maskable icon keeps the swirl on a solid white field, since launchers clip maskable icons to a circle/squircle and require an opaque full-bleed background. Updated the PWA `theme_color` to `#00a0dd` and added explicit `<link rel="icon">` favicon tags (SVG + PNG) to the kiosk shell. Bumped the service-worker cache to `time-kiosk-v3` so installed clients fetch the new icons. (`erpnext_enhancements/public/kiosk/icons/*`, `www/kiosk-manifest.json`, `www/kiosk.html`, `www/kiosk-sw.js`)

## [0.3.3] - 2026-06-09

### Fixed
- **Task tree "Assigned To" column is now clickable.** Clicking the assignee link on a task row in the Project form's Scope tab (and the Project Dashboard Tasks tree view) previously did nothing. It now opens an assignment dialog listing current assignees with remove buttons and a User picker to add new ones, wired to the existing `add_task_assignee` / `remove_task_assignee` backend methods. Disabled in read-only mode. (`erpnext_enhancements/public/js/project_enhancements/task_tree_manager.js`)

## [0.3.2] - 2026-06-09

### Added
- **Project-wide documentation.** Added module/function docstrings, JSDoc header blocks, and inline comments across the codebase (200 source files: Python, JavaScript, CSS, HTML) — comments only, no executable code changed. Verified every changed `.py` compiles (`py_compile`) and every changed `.js` passes `node --check`.
- **README files for every subsystem.** Rewrote the top-level [`README.md`](README.md) (architecture overview, 8-module map, annotated `hooks.py` reference, external-integration matrix, dev workflow, conventions, documentation index) and added a `README.md` to each module and cross-cutting folder: `api/`, `project_enhancements/`, `crm_enhancements/`, `quickbooks_time_integration/`, `sapphire_maintenance/`, `enhancements_core/`, `travel_management/`, `task_enhancements/`, `global_enhancements/`, `script_migrations/`, `patches/`, `public/`, `www/`, `tests/`, and `Custom HTML Block/`. Detailed GA4 dashboard setup moved into the Enhancements Core README.

## [0.3.1] - 2026-06-08

### Changed
- **Kanban "press-and-hold to move a card" now applies to mouse *and* touch — and reliably.** A card only starts dragging after a deliberate **1-second press-and-hold** (finger or mouse); a quick swipe scrolls the board sideways or a column up/down, and a quick tap still opens the card. This stops accidental card moves while scrolling, especially on mobile. (`erpnext_enhancements/public/js/kanban_patches.js`)
  - Set SortableJS `delayOnTouchOnly: false` so the 1-second hold gates the **mouse** too. Previously it was touch-only, so a mouse dragged a card instantly.
  - Rebuilt **how** the delay is applied. The old version scanned for SortableJS instances on a fixed `[0, 150, 400, 1000]ms` timeline after `KanbanView.render()`. On the heavy Opportunity board, Vue/SortableJS finish mounting *after* that 1-second window closes, so the scan found nothing — and `kanban_leak_fix.js` short-circuits `render()` on filter refreshes, so the scan was never re-scheduled. Net effect on the live board: the delay never applied and cards grabbed instantly. The patch is now **decoupled from `render()`**: a document-level `MutationObserver` watches for Kanban *container* insertions (board / columns / card-lists, not individual cards) and, debounced, recovers every live SortableJS instance to set `delay` / `delayOnTouchOnly` / `touchStartThreshold`, with a short bounded startup poll as a fallback. Idempotent per instance, and board-agnostic (Task, Opportunity, …).

### Removed
- **Opportunity-board drag lock.** Card dragging on the Opportunity board was fully disabled by `disable_kanban_drag.js` (blocked native `dragstart`) and a `pointer-events: none` rule on `.kanban-card` in `horizontal_scroll.css`. Both only blocked the **mouse** — SortableJS handles touch separately, so on mobile Opportunity cards still moved by accident. Replaced with the unified 1-second hold-to-move above, so cards are movable again but guarded. Deleted `erpnext_enhancements/public/js/global_enhancements/disable_kanban_drag.js`, removed its `doctype_js` hook entry, and dropped the `pointer-events` rules from `horizontal_scroll.css` (the horizontal-scroll layout rules are kept).

## [0.3.0] - 2026-06-08

### Changed
- **Legacy desk Time Kiosk page now redirects to `/kiosk`**: the in-desk `time-kiosk` Page (`/app/time-kiosk`, legacy `/desk/time-kiosk`) previously rendered an older copy of the kiosk UI (jQuery + `geo_worker.js`). Its `on_page_load`/`on_page_show` now simply `window.location.replace('/kiosk')`, so old bookmarks land on the standalone PWA instead of the retired UI. The Page record is retained purely as the redirect target.

### Added
- **`/kiosk` requests location permission on visit**: previously the browser permission prompt only fired on clock-in (the first call to `watchPosition`/`getCurrentPosition`), so visiting the page never asked. Added `KioskGeo.warmup()`, called on page load when tracking is enabled, which surfaces the permission prompt up front (skipping it when the Permissions API reports the choice is already made) and reflects the result in the tracking indicator with a new "Location ready" (solid green) state. No location is logged until the user is clocked in.

### Removed
- **Dead assets from the retired in-desk kiosk**: deleted `public/js/geo_worker.js` (the old desk kiosk's geolocation Web Worker — superseded by `public/js/kiosk/geo.js` + `www/kiosk-sw.js`) and `public/css/time-kiosk.bundle.css` (styled the old desk DOM only: `#tk-current-time`, `#timer-text`, `.btn-lg`, etc. — none of which exist in the new PWA, whose styles live in `css/kiosk/kiosk.css`). Removed the now-stale `time-kiosk.bundle.css` `<link>` from `www/kiosk.html` and its entry from the service-worker precache list.

### Fixed
- **Time Kiosk not installable as a PWA**: `kiosk-manifest.json` listed only SVG icons (`sizes: "any"`). Chrome/Edge require at least one raster PNG icon at 192×192 and one at 512×512 to satisfy their installability criteria, so the "Install app" prompt never appeared. Added PNG icons (`kiosk-icon-192.png`, `kiosk-icon-512.png`, and a maskable `kiosk-maskable-512.png`, rendered to match the existing clock glyph) and listed them first in the manifest; the SVGs are retained as supplementary entries. The `apple-touch-icon` now points at the 192px PNG (iOS ignores SVG touch icons). Bumped the service-worker cache to `time-kiosk-v2` and precached the new icons so existing installs pick up the change.
- **Kiosk clock unreadable in dark mode**: `kiosk.css` switched to its dark palette only via `@media (prefers-color-scheme: dark)`, flipping the text to near-white, while the page body background was pinned to Frappe's light `--bg-color`. The result was light text on a light body (the top wall-clock was nearly invisible). The body background now follows the kiosk's own `--tk-bg`, the dark palette also responds to Frappe's `[data-theme="dark"]` attribute, and the clock/timer have explicit `--tk-text` colors — keeping background and text contrast in sync in every light/dark combination.
## [0.2.9] - 2026-06-08

### Removed
- **Frappe integration-test CI job**: Removed the `integration-tests` job (real bench + ERPNext + `bench run-tests --app erpnext_enhancements`) from `.github/workflows/ci.yml`. On the version-16 toolchain it never reached this app's own assertions — it aborted inside Frappe's test-record auto-generation, which walks the entire ERPNext doctype dependency graph and tripped over a cascade of environment gaps (missing `frappe.utils` helpers, custom fields absent on bootstrap-created Contacts, and uninstalled companion doctypes like `Payment Gateway`). Each fix only exposed the next, so the job gated PRs on upstream/environment churn unrelated to the app's code. CI now relies on the standalone `unit-tests` job. The Frappe-dependent test files under `erpnext_enhancements/` are left in the tree and can still be run against a real bench locally; a CI job can be reintroduced once the upstream harness stabilises. The defensive code fixes made while chasing these failures (`add_to_date`, `getattr`-guarded Contact custom-field reads, `has_column` guard in `sync_from_contact`) are retained as genuine robustness improvements.

## [0.2.8] - 2026-06-08

### Fixed
- **Opportunity save crash `AttributeError: 'Opportunity' object has no attribute 'lead'`**: the migrated `update_lead_status` `before_save` hook (`script_migrations/opportunity.py`) guarded on `doc.lead`, but the Opportunity doctype has no `lead` field — the Lead is referenced via `party_name` when `opportunity_from == "Lead"`. Saving *any* Opportunity (including ones created from a Customer, as in the report) raised the error and blocked the save. The guard now checks `doc.opportunity_from == "Lead" and doc.party_name`, and resolves the Lead via `party_name`.
- **CI: install the `payments` app so test-record generation resolves `Payment Gateway`**: `bench run-tests` aborted with `DoesNotExistError: DocType Payment Gateway not found` during Frappe's test-record dependency walk. ERPNext ships doctypes that Link to `Payment Gateway` (e.g. `Payment Gateway Account`), but in version-16 that doctype lives in the separate `frappe/payments` app and is **not** listed in ERPNext's `required_apps`, so `bench get-app erpnext --resolve-deps` never fetched it. The integration-test job now `bench get-app payments --branch "$FRAPPE_BRANCH"` and `--install-app payments` (between erpnext and erpnext_enhancements), providing the missing doctype so the dependency walker completes.

## [0.2.7] - 2026-06-08

### Fixed
- **Contact sync "Unknown column 'primary_contact'" on fresh DBs**: `sync_from_contact` looped over `PRIMARY_CONTACT_DOCTYPES` (`Project`, `Opportunity`, `Supplier`, `Customer`) and ran `frappe.get_all(dt, filters={"primary_contact": ...})`. `primary_contact` is a custom field, so on a DB where it isn't installed — e.g. ERPNext's test bootstrap, which creates a `User` → `Contact` and fires this `on_update` hook before the app's custom fields exist — the query raised `OperationalError (1054): Unknown column 'primary_contact' in 'WHERE'`, aborting `bench run-tests` during record generation. The loop now skips any doctype lacking the column via `frappe.db.has_column(dt, "primary_contact")`.

## [0.2.6] - 2026-06-08

### Fixed
- **Contact sync `AttributeError` on missing custom fields**: `sync_from_contact` read `doc.custom_title` / `custom_phone_number` / `custom_mobile_number` / `custom_email` as direct attributes, and `sync_from_main_doc` did the same for `contact.custom_*`. When a `Contact` lacks those custom fields — e.g. ERPNext's test bootstrap auto-creates a Contact (via `User.create_contact`) before this app's custom fields exist — the `on_update` hook raised `AttributeError: 'Contact' object has no attribute 'custom_title'`, which aborted the whole `bench run-tests` record-generation phase. All custom-field **reads** now use `getattr(obj, "field", None) or ""`, matching the defensive pattern already used for the `primary_contact_*` fields in the same module. Writes are unchanged (assigning a missing field never raised).

## [0.2.5] - 2026-06-08

### Fixed
- **Test discovery `ImportError`**: `bench run-tests` crashed at discovery time with `cannot import name 'add_hours' from 'frappe.utils'`. Frappe has no `add_hours` helper — the correct utility is `add_to_date(date, hours=N)` (already used elsewhere in this app). The bad import lived in two places: `erpnext_enhancements/api/booking.py` (which would have raised at runtime whenever `create_composite_booking` was called) and `enhancements_core/doctype/asset_booking/test_asset_booking.py` (which broke discovery for the whole app). Both now use `add_to_date`, restoring test discovery and the composite-booking API.

## [0.2.4] - 2026-06-08

### Fixed
- **File preview toolbar icons**: The Download / Open-in-new-tab / Close icons in the file-preview overlay rendered tiny and dark. Frappe icons default to `fill: none; stroke: var(--icon-stroke)` at the 12px `sm` size, so the button's white text colour never reached them. Added overlay-scoped CSS that forces `--icon-stroke`/`stroke` to white and bumps the toolbar icons to 20px.
- **File list grid view default**: The File list now defaults to grid view every time it is opened, not just on a user's first-ever visit. The previous one-time `localStorage` marker meant returning users always landed in list view (since `FileView.before_render()` persists `grid_view=false` after any list render). Grid is now forced from the patched `setup_view()`, which runs once per FileView instantiation, so an in-session toggle back to list still sticks.
- **Kanban drag-to-scroll stutter**: Dragging the Opportunity Kanban board sideways to reveal more columns stuttered badly. A Chrome performance trace traced it to frappe core's `bind_clickdrag` (`kanban_board.bundle.js`): its `mousemove` handler reads `draggable.offsetLeft` right after writing `draggable.scrollLeft`, forcing a synchronous full-document style/layout recalc on every move — ~34,800 elements at up to ~88ms each on a large board (~5 dropped frames per mousemove). New `kanban_scroll_perf.js` installs a single capture-phase pointer handler that reimplements drag-to-scroll from `e.pageX` alone (no layout read — `offsetLeft` is constant during a horizontal scroll and cancels out) and `stopPropagation()`s the move so frappe's reflow-forcing handler is skipped during a drag. frappe's exact ignore-selectors are mirrored, so which areas start a drag-scroll is unchanged. Remove once frappe core stops reading `offsetLeft` on `mousemove`.
- **Kanban touch drag — "hold to grab"**: On touch screens a card could be picked up and dropped into another column from an incidental brush, because Frappe starts a drag the instant a touch lands on a card. The old "drag delay" patch proxied the global `window.Sortable`, but Frappe v16's Kanban imports SortableJS as a bundled module, so the proxy never reached the real card-drag instances and the delay was never applied (it also fully *disabled* Kanban drag). The patch now recovers each card container's live SortableJS instance from the DOM after the board renders and sets `delay: 1000`, `delayOnTouchOnly: true`, and `touchStartThreshold: 8` — so a touch must press-and-hold ~1s before a card can move, a swipe still scrolls the column, and mouse dragging on desktop stays instant.
- **Task tree drag-and-drop intent**: In the Project "Scope" tab task tree, dropping a task onto the middle of a row is meant to nest it as a child while dropping near a row's top/bottom edge reorders it as a sibling. The intent was measured against the whole `.task-node`, whose box spans the entire subtree for an expanded parent, so the "nest" band fell off-screen and nesting only ever worked on leaf tasks. Intent is now measured against the hovered node's own row, so nesting works under expanded parents too.
- **Project Gantt scroll target**: The Schedule-tab Gantt now opens scrolled to the **first task's start date** (the earliest task), instead of the project's `expected_start_date` — which left the viewport on empty space whenever that field was unset or pointed away from the actual work.

## [0.2.3] - 2026-06-08

### Added
- **Automated GitHub Releases**: A new `Release` workflow (`.github/workflows/release.yml`) tags and publishes a GitHub Release whenever a new `__version__` lands on `main`. It reads the version from `erpnext_enhancements/__init__.py`, verifies `package.json` is in sync, skips versions already tagged, and uses this changelog's matching section as the release notes. Because Frappe Cloud deploys from `main`, the repo's Releases page is now a 1:1 log of what is deployed and at which version.

## [0.2.2] - 2026-06-08

### Fixed
- **Frappe integration CI**: Added a `redis:7-alpine` service container and pointed Frappe's `redis_cache`/`redis_queue`/`redis_socketio` at it (`127.0.0.1:6379`). `bench new-site` installs ERPNext, which enqueues a background job (`delete_dynamic_links` via `enqueue_after_commit`) and forces a Redis Queue connection; with `--skip-redis-config-generation` no redis was running, so Frappe fell back to its default `127.0.0.1:11000` and the install died with "Connection refused". Also dropped the apt `redis-server` install, which would collide with the container's `6379` port mapping.

## [0.2.1] - 2026-06-08

### Fixed
- **Frappe integration CI**: Bumped the Node version installed for the integration-tests job from 20 to 24. Frappe `version-16`'s `package.json` declares `engines.node ">=24"`, so `yarn install` aborted during `bench init` ("The engine \"node\" is incompatible with this module"). Mirrors the earlier Python 3.14 bump — both track `version-16`'s moving toolchain floor.

## [0.2.0] - 2026-06-08

### Added
- **Time Kiosk standalone PWA**: The Time Kiosk is now an installable Progressive Web App served at `/kiosk` (web manifest, root-scope service worker, offline app shell) instead of only living inside the desk app. The legacy desk page at `/app/time-kiosk` stays as a fallback and links to the new app.
- **Continuous, battery-aware location tracking**: While clocked in **and active** (not paused), the PWA tracks location on the main thread using `watchPosition` + a movement distance-filter + a periodic heartbeat. Points are persisted to IndexedDB by the service worker and uploaded in batches via a new session-trusted `log_geolocation_batch` endpoint, with Background Sync retry when offline. (Fixes the prior dedicated Web Worker that could never read GPS — `navigator.geolocation` is unavailable in workers.)
- **Location history & timeline**: Each point is tied to its `Job Interval`; new whitelisted `get_location_history` plus a manager-facing **Location Timeline** desk page replay an employee's movements on a Leaflet map.
- **Time Kiosk Settings** (Single doctype): configurable distance filter, heartbeat, GPS accuracy, screen wake-lock, batch size, and retention. A daily scheduled job purges location logs older than the retention window.

### Changed
- **Time Kiosk Log** gains `job_interval`, `accuracy`, `speed`, `heading`, and `altitude` fields, search indexes on `employee`/`timestamp`, and owner-scoped read access for employees.
- **App consolidation**: Merged the previously separate `crm_enhancements`, `global_enhancements`, `project_enhancements`, `task_enhancements`, and `qb_time_integration` apps into `erpnext_enhancements`. Each is now a Frappe module within this single app (CRM Enhancements, Global Enhancements, Project Enhancements, Task Enhancements, QuickBooks Time Integration). Their hooks, patches, fixtures, and public assets were merged; incoming public assets are namespaced under `public/{js,css}/<module>/` to avoid collisions. The standalone apps are no longer required — uninstall them from existing benches after deploying this release.

## [0.1.1] - 2026-01-27

### Fixed
- **List View Sorting**: Fixed an issue where the sort dropdown menu was hidden behind the list header or other elements by increasing its z-index.

## [0.1.0] - 2024-05-22

### Added
- **Time Kiosk**: A simplified, tablet-friendly interface for employees to log time against projects and tasks. Supports geolocation logging and syncing to Timesheets.
- **Project Enhancements**:
    - **Procurement Status**: Calculated status fields on Projects to track material requests and orders.
    - **Project Merge**: Utility to merge duplicate projects.
    - **Attachment Sync**: Automatically syncs attachments from Opportunities to created Projects.
    - **Validation**: Improved status validation logic.
- **Kanban Board Improvements**:
    - **Touch Support**: Patched `Sortable.js` initialization to fix drag-and-drop latency on touch devices.
    - **Scrolling**: Fixed horizontal scrolling issues for large boards.
    - **WIP Limits**: Added custom fields to enforce Work-In-Progress limits on columns.
- **Safe Form Drafts**: Implemented "User Form Draft" mechanism to auto-save unsaved form data to a safe container, preventing data loss on navigation or browser crash.
- **Travel Management**: Custom "Travel Trip" workflow and enhancements for Expense Claims.
- **Dashboard Overrides**: Custom dashboard data logic for Projects and Employees.
- **Comment Enhancements**: Custom Vue.js components for improved commenting experience on various doctypes.

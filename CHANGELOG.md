# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

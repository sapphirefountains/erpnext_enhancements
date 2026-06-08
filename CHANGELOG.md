# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

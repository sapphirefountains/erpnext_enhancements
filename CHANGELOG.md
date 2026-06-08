# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

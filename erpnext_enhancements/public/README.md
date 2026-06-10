# `public/` — Browser (client-side) assets

Everything that runs in the browser: desk form/list scripts, desk-wide patches, the Vue comments app, performance hotfixes, and the Time Kiosk PWA front-end. CSS lives under `css/`, JavaScript under `js/`, and PWA icons under `kiosk/`.

Assets are loaded via [`../hooks.py`](../hooks.py) — `app_include_js`/`app_include_css` (global), `doctype_js`/`doctype_list_js`/`doctype_css`/`doctype_calendar_js` (per-doctype) — **except** the kiosk front-end, which is loaded by `www/kiosk.html`.

Every file has a top-of-file doc block. This README is the architecture map.

## Four kinds of client code

1. **Per-doctype form/list/calendar scripts** — `frappe.ui.form.on(...)` / `frappe.listview_settings[...]`. Loaded per-doctype. Many are explicitly noted as migrated from database Client Scripts into version-controlled files (`*_migrated_scripts.js`).
2. **Global desk patches & services** — loaded once via `app_include_js`; mostly prototype monkey-patches (`Timeline`, `Dialog`, `AwesomeBar`, `KanbanView`, form `Controller`/`Sidebar`) plus shared services (telephony).
3. **The custom Vue "Comments App"** — `comments.js` + vendored `vue.global.js` + `comments_auto.js`, with `global_comments.js` enhancing the *native* timeline.
4. **Perf / upstream-bug hotfixes** — the `kanban_*` suite + `performance_fixes.js`, each with an in-file write-up of the upstream Frappe bug (the [CHANGELOG](../../CHANGELOG.md) has full detail).

> **Vendored libraries — do not edit:** `js/vue.global.js` (Vue 3 global build) and `js/project_enhancements/lib/frappe-gantt.umd.js` (frappe-gantt UMD). Several features need `window.Vue`, so `vue.global.js` is listed alongside `comments.js` in many `doctype_js` entries.

## Top-level `js/` (form scripts, patches, services)

| File | Targets | Purpose | Load |
|---|---|---|---|
| `account.js` / `employee.js` / `timesheet.js` | those forms | Mount the Comments App | doctype_js |
| `activity_log_numbering.js` | all forms (timeline) | "#N" badges on timeline items | app_include_js |
| `asset_booking_calendar.js` | Asset Booking calendar | Calendar field-map + `get_events` source | doctype_calendar_js |
| `comments.js` | forms w/ `custom_comments_field` | Defines `render_comments_app` (Vue notes UI) | app_include_js + doctype_js |
| `comments_auto.js` | `COMMENT_APP_DOCTYPES` | Auto-mounts the Comments App on ~23 doctypes | app_include_js |
| `communication.js` | Communication | "Suggest Reply" AI SMS button | doctype_js |
| `contact.js` | Contact | Comments App + Triton call/SMS + mirror Customer→`custom_account` | doctype_js |
| `crm_note_enhancements.js` | CRM Note dialogs (global) | Patches `Dialog` to add attachments to Add/Edit Note | app_include_js |
| `customer.js` | Customer | Comments App, Triton buttons, "Create" dropdown, reminder cadence, related-doc tables | doctype_js |
| `erpnext_enhancements.js` | whole desk | Awesomebar live search, map placeholder, safe auto-save/drafts, nav guard, "Add to Desk", sidebar/home tweaks, Vue file manager | app_include_js |
| `filter_help.js` | list views (global) | Injects a "?" filter-help dialog button | app_include_js |
| `global_comments.js` | all forms (timeline) | Patches the **native** Timeline to attach files into comments | app_include_js |
| `item.js` / `item_list.js` | Item form/list | Migrated: show item code; widen name/code columns | doctype_js / doctype_list_js |
| `kanban_*.js` | Kanban boards | See [Kanban patch suite](#kanban-patch-suite) | `kanban.bundle.js` (app_include_js) |
| `lead.js` | Lead | Triton call + migrated "Create Opportunity" | doctype_js |
| `opportunity.js` | Opportunity | Thin stub (real logic in `crm_enhancements/`) | doctype_js |
| `opportunity_list.js` | Opportunity list | Redirect fresh list loads to the Kanban board | doctype_list_js |
| `performance_fixes.js` | desk `<head>` | Strips redundant icon-sprite preload links | app_include_js |
| `process_document.js` | Process Document | Lazy-load Mermaid.js; render diagram + live-editor link | doctype_js |
| `procurement_links.js` | Purchase Order / Material Request | Per-item supplier purchase links | doctype_js |
| `project.js` | Project | Mirror name → `custom_project_id` (one of several Project scripts) | doctype_js |
| `project_enhancements.js` | Project | Comments App + Vue Procurement Tracker + `custom_btn_*` doc creators | doctype_js |
| `project_merge.js` | Project | "Merge Project" tool (dry-run stats + execute) | doctype_js |
| `project_migrated_scripts.js` | Project + Stakeholder child | Migrated: hide dashboard, stakeholder contact/address filtering | doctype_js |
| `sales_order_enhancements.js` | Sales Order | Filter `custom_serial_no` query to a specific item | doctype_js |
| `task_enhancements.js` | Task | `custom_create_child_task_btn` quick-entry | doctype_js |
| `telephony_client.js` | whole desk | Twilio softphone + SMS dialer service (`erpnext_enhancements.telephony`) | app_include_js |
| `travel_trip.js` | Travel Trip | Set `transport_ref_doctype` from transport type | doctype_js |

### Module sub-folders under `js/`

- **`crm_enhancements/`** — `opportunity.js` (value-stream tags + Create-Project dialog → background project creation incl. Drive), `opportunity_migrated_scripts.js` (ex-Client-Scripts: rank validation, scope show/hide), `opportunity_list.js` (Kanban card tinting by close date), `opportunity_kanban_totals.js` (per-column amount totals).
- **`global_enhancements/`** — `triton_widget.js` (the AI assistant FAB/chat), `global_sidebar.js` + `auto_collapse_sidebar.js` (sidebar tweaks), `unified_tab_controller.js` (the aggregated contacts/addresses directory + map on party forms), `quill_mentions.js` (`@`-mentions), `unlink_and_delete.js` ("Unlink and Delete" dialog on LinkExistsError), `primary_contact.js` (read-through contact fields), `file_list.js` (grid-default + preview overlay), `supplier_list.js` (group filters/indicators).
- **`project_enhancements/`** — `project_form_script.js` (task tree + Gantt tabs), `project_brief.js`, `task_tree_manager.js` (the `TaskTreeManager` hierarchical grid), `gantt_zoom.js` (shared zoom ladder), `task_gantt.js`, plus `dashboard_components/` (below) and the vendored `lib/frappe-gantt.umd.js`.
- **`task_enhancements/`** — `task_enhancements.js` (patches `TreeView.get_tree_nodes` for the Hierarchical Task View).
- **`kiosk/`** — `app.js` + `geo.js` (the Time Kiosk PWA front-end, below).

## The Comments App

- **`vue.global.js`** (vendored) provides `window.Vue` (Vue 3).
- **`comments.js`** defines `erpnext_enhancements.render_comments_app(frm, field_name)` — mounts a Vue notes UI into a `custom_comments_field` HTML field. CRUD goes through [`api/comments.py`](../api/README.md); attachments are uploaded as standalone `File` docs then `link_files_to_comment`.
- **`comments_auto.js`** holds `COMMENT_APP_DOCTYPES` (~23 doctypes) and auto-mounts the app on each. It **deliberately excludes** Project, Customer, Employee, Account, Timesheet, Contact — because those doctypes' own form scripts already call `render_comments_app` (avoiding a double mount).
- **`global_comments.js`** is separate: it patches the **native** Frappe Timeline (not the Vue app) to add an "Attach File" button. Both paths funnel through the same `link_files_to_comment` API.

## Kanban patch suite

Shipped as one esbuild bundle — `kanban.bundle.js` imports the four files below in
order. The bundle exists for cache busting, not code organisation: raw `/assets`
paths are served with a 1-year **immutable** Cache-Control, so phones kept running
the first copy of each patch they ever downloaded across deploys (the "works on
desktop, mobile still grabs cards" bug, v0.8.1). The hashed bundle filename gives
every deploy a fresh URL.

| File | Fixes |
|---|---|
| `kanban_patches.js` | Press-and-hold (1s) before a card drags, for **mouse and touch**; applied to every board via a document `MutationObserver` (decoupled from `render`, which the leak fix short-circuits). Also backports SortableJS 1.15.4's `pointercancel` cancel for pointer-only inputs (pen, some Windows-touch configs). |
| `kanban_customization.js` | Opportunity-board styling: dark high-value cards + coloured value-stream dots; bulk-fetches the `custom_value_stream` child table the list query omits. |
| `kanban_leak_fix.js` | Hotfix for the Kanban filter memory leak (upstream frappe/frappe#24156): takes the reactive `update_cards` path on same-board refresh instead of re-`init()`. |
| `kanban_scroll_perf.js` | Fixes layout-thrash in core `bind_clickdrag` drag-to-scroll (reads `e.pageX` only, no `offsetLeft`; capture-phase handler `stopPropagation`s core's reflow-forcing handler). |

## Project Dashboard components (`js/project_enhancements/dashboard_components/`)

These plug into the [Project Dashboard](../project_enhancements/README.md#project-dashboard) page. Each tab maps to a component **class** lazily `frappe.require`d on activation, constructed, and `render()`ed; switching away calls `unmount()` (aborting in-flight requests via `AbortController`). `dashboard_api.js` is required first and every component routes server calls through `dashboard_api.call` (8s timeout + abort). `column_selector.js` + `gantt_zoom.js` are preloaded globally so lazy tabs can use them immediately.

| File | Tab | Notes |
|---|---|---|
| `dashboard_api.js` | (shared) | `frappe.call` wrapper with abort + 8s timeout |
| `active_internal_projects.js` | Active Internal Projects | grouped by Master Project; inline status/priority edits |
| `completed_projects.js` | Completed Projects | exponential-backoff retry (≤3) |
| `priority_overview.js` | Priority Overview | `BufferManager` optimistic-edit engine (buffer → auto-commit w/ retry → rollback) |
| `tasks_view.js` | Tasks | per-project Gantt/Tree in-page; Kanban/Calendar route to the Task list |
| `portfolio_gantt.js` | Portfolio Gantt | whole-portfolio Gantt grouped by Master Project, drag-to-reschedule write-back, scroll preservation |
| `column_selector.js` | (shared) | reusable "Columns" dropdown; localStorage-persisted |

## Kiosk PWA front-end (`js/kiosk/`)

`www/kiosk.html` injects a boot payload (`window.KIOSK_BOOT` = employee, settings, current status, csrf) and loads `app.js` + `geo.js`, then registers `/kiosk-sw.js`.

- **`app.js`** — UI / clock state machine. Seeds UI from boot status, confirms via `get_current_status`, and switches the card between idle / active (Open) / paused (break) views. Actions POST to [`api.time_kiosk.log_time`](../api/README.md); it starts/stops `KioskGeo` **only while Open** (never on break) and posts config (csrf, batch size) to the service worker.
- **`geo.js`** — geolocation on the **main thread** (workers can't read GPS). `warmup()` primes the permission on page visit; `start()` runs `watchPosition` **and** a heartbeat interval; `consider()` filters fixes (drop below `min_accuracy_m`; record only when moved ≥ `distance_filter_m` or the heartbeat elapsed). Accepted points are posted to the **service worker** (`{type:'enqueue'}`), which owns durable batching/upload. `visibilitychange`/`online` re-acquire the wake lock, grab a catch-up fix, and flush. Exposes `window.KioskGeo`.

See [`www/README.md`](../www/README.md) for the service-worker / offline side.

## CSS (`css/`)

| File | Styles | Load |
|---|---|---|
| `desk_enhancements.bundle.css` | Desk "Sapphire glass" theme + Procurement Tracker, Comments App, Kanban, activity numbering, filter-help | app_include_css |
| `login_enhancements.bundle.css` | Login/forgot/signup pages | web_include_css (as `login_enhancements.css`) |
| `global_enhancements/horizontal_scroll.css` | Opportunity Kanban horizontal scroll layout | doctype_css["Opportunity"] |
| `global_enhancements/triton_widget.css` | Triton assistant FAB + chat panel | app_include_css |
| `kiosk/kiosk.css` | Standalone Time Kiosk PWA shell | `www/kiosk.html` `<link>` (not hooks) |
| `project_enhancements/frappe-gantt.css` | **Vendored** frappe-gantt styles | app_include_css |
| `project_enhancements/task_tree.css` | Hierarchical task grid + dashboard column selector | app_include_css |
| `quickbooks_time_integration/qb_time_integration.css` | QBO status dashboard | app_include_css |
| `task_enhancements/task_enhancements.css` | Hierarchical task tree connectors | app_include_css |

## Gotchas

- Comments App double-mount avoidance: the six doctypes whose form scripts call `render_comments_app` are intentionally absent from `COMMENT_APP_DOCTYPES`.
- `kiosk.css` is the one CSS file **not** loaded via `hooks.py` (it's referenced by `www/kiosk.html`). `hooks.py` references the compiled `login_enhancements.css`, but the on-disk source is `login_enhancements.bundle.css`.
- Many global monkey-patches (`KanbanView.refresh`, `FileView.setup_view`, `TreeView.get_tree_nodes`, `msgprint`/`show_alert`/`request.error`) are guarded by idempotency flags and **may need revisiting on Frappe upgrades**.
- `task_gantt.js` is a doc-comment stub — the scroll-to-today logic it describes is not in the file body (flagged in-file).
- CDN dependencies: `process_document.js` (Mermaid) and `telephony_client.js` (Twilio SDK) lazy-load third-party scripts from jsDelivr at runtime.

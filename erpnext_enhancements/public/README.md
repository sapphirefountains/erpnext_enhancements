# `public/` — Browser (client-side) assets

Everything that runs in the browser: desk form/list scripts, desk-wide patches, the Vue comments app, performance hotfixes, and the Time Kiosk PWA front-end. CSS lives under `css/`, JavaScript under `js/`, and PWA icons under `kiosk/`.

Assets are loaded via [`../hooks.py`](../hooks.py) — `app_include_js`/`app_include_css` (global), `doctype_js`/`doctype_list_js`/`doctype_css`/`doctype_calendar_js` (per-doctype) — **except** the kiosk front-end, which is loaded by `www/kiosk.html`.

Every file has a top-of-file doc block. This README is the architecture map.

> **Globals ship as bundles — never add raw `/assets` paths to `app_include_*`.**
> `/assets` is served with a 1-year *immutable* Cache-Control and raw paths get
> no content hash, so an edited raw include never reaches a device that already
> cached it (the v0.8.1 "Kanban fix works on desktop, phones still broken" bug).
> Global JS goes through `js/kanban.bundle.js` or `js/erpnext_enhancements.bundle.js`,
> global CSS through `css/desk_enhancements.bundle.css` or `css/desk_addons.bundle.scss`
> — esbuild gives the built files content-hashed names. Where "Load" says
> `app_include_js/css` below, the file is imported by one of those bundles. The
> only raw global includes are the two vendored UMD libs (`vue.global.js`,
> `frappe-gantt.umd.js`): a bundle import would capture their exports instead of
> setting `window.Vue`/`window.Gantt`, and their content never changes, so
> stale caching cannot affect them. (`doctype_js` files are unaffected: they
> load through `frappe.require`'s version-aware cache.)

## Four kinds of client code

1. **Per-doctype form/list/calendar scripts** — `frappe.ui.form.on(...)` / `frappe.listview_settings[...]`. Loaded per-doctype. Many are explicitly noted as migrated from database Client Scripts into version-controlled files (`*_migrated_scripts.js`).
2. **Global desk patches & services** — loaded once via `app_include_js`; mostly prototype monkey-patches (`Timeline`, `Dialog`, `AwesomeBar`, `KanbanView`, form `Controller`/`Sidebar`) plus shared services (telephony).
3. **The custom Vue "Comments App"** — `comments.js` + vendored `vue.global.js` + `comments_auto.js`, with `global_comments.js` enhancing the *native* timeline.
4. **Perf / upstream-bug hotfixes** — the `kanban_*` suite + `performance_fixes.js`, each with an in-file write-up of the upstream Frappe bug (the [CHANGELOG](../../CHANGELOG.md) has full detail).

> **Vendored libraries — do not edit:** `js/vue.global.js` (Vue 3 global build), `js/project_enhancements/lib/frappe-gantt.umd.js` (frappe-gantt UMD), and `js/gantt_widget/lib/dhtmlxgantt.js` + `css/gantt_widget/dhtmlxgantt.css` (DHTMLX Gantt 10 **Standard, MIT edition**). Several features need `window.Vue`, so `vue.global.js` is listed alongside `comments.js` in many `doctype_js` entries. Unlike the first two, the vendored DHTMLX pair is **not** a raw global include: it lazy-loads on the first `erpnext_enhancements.gantt.mount(...)` call — the skin CSS via `frappe.require`, but the library JS is **fetched and evaluated synchronously** inside an atomic `window.Gantt`/`window.gantt` save-restore bracket (its UMD would otherwise clobber the frappe-gantt global for the whole async load window, and `frappe.require` marks even failed loads as executed so they could never retry). Only *vendored, never-edited* files may be lazy-loaded this way — the widget's own `gantt_widget.css` tried it and rotted (see the CSS table).

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
| `erpnext_enhancements.js` | whole desk | Awesomebar live search, map placeholder, safe auto-save/drafts, "Add to Desk", sidebar/home tweaks, Vue file manager | app_include_js |
| `filter_help.js` | list views (global) | Injects a "?" filter-help dialog button | app_include_js |
| `global_comments.js` | all forms (timeline) | Patches the **native** Timeline to attach files into comments | app_include_js |
| `item.js` / `item_list.js` | Item form/list | Migrated: show item code; widen name/code columns | doctype_js / doctype_list_js |
| `kanban_*.js` | Kanban boards | See [Kanban patch suite](#kanban-patch-suite) | `kanban.bundle.js` (app_include_js) |
| `lead.js` | Lead | Triton call + migrated "Create Opportunity" | doctype_js |
| `opportunity.js` | Opportunity | Thin stub (real logic in `crm_enhancements/`) | doctype_js |
| `opportunity_list.js` | Opportunity list | Redirect fresh list loads to the Kanban board | doctype_list_js |
| `performance_fixes.js` | desk `<head>` | Strips redundant icon-sprite preload links | app_include_js |
| `process_document.js` | Process Document | Lazy-load Mermaid.js; brand-themed diagram preview + the **Visual Builder** dialog (split-pane live editor, snippet insertion incl. the SF style pack, zoom, SVG export, mermaid.live link) | doctype_js |
| `procurement_links.js` | Purchase Order / Material Request | Per-item supplier purchase links | doctype_js |
| `project.js` | Project | Mirror name → `custom_project_id` (one of several Project scripts) | doctype_js |
| `project_enhancements.js` | Project | Comments App + Vue Procurement Tracker + `custom_btn_*` doc creators | doctype_js |
| `project_merge.js` | Project | "Merge Project" tool (dry-run stats + execute) | doctype_js |
| `project_migrated_scripts.js` | Project + Stakeholder child | Migrated: hide dashboard, stakeholder contact/address filtering | doctype_js |
| `sales_order_enhancements.js` | Sales Order | Filter `custom_serial_no` query to a specific item | doctype_js |
| `task_enhancements.js` | Task | `custom_create_child_task_btn` quick-entry | doctype_js |
| `telephony_client.js` | whole desk | Twilio softphone + SMS dialer service (`erpnext_enhancements.telephony`) | app_include_js |
| `travel_trip.js` | Travel Trip | Create buttons (per-traveler Expense Claims, Advance, Vehicle Log, Lead/Opportunity from stop, Send Itinerary, coordinator Reopen) + link scoping + billable row defaults — backed by `travel_management/api.py` | doctype_js |
| `travel_trip_calendar.js` | Travel Trip calendar | Calendar field-map + filters; one all-day event per (trip, traveler) via `api.travel.get_events` | doctype_calendar_js |

### Module sub-folders under `js/`

- **`crm_enhancements/`** — `opportunity.js` (value-stream tags + Create-Project dialog → background project creation incl. Drive), `opportunity_migrated_scripts.js` (ex-Client-Scripts: rank validation, scope show/hide), `opportunity_list.js` (Kanban card tinting by close date), `opportunity_kanban_totals.js` (per-column amount totals).
- **`global_enhancements/`** — `triton_widget.js` (the AI assistant FAB/chat), `mermaid_theme.js` (`window.sf_mermaid` — the Sapphire Fountains Mermaid brand theme: Lato + the sapphire/teal palette from sapphirefountains.com, shared by the Process Document preview/builder and the Triton widget's diagram renderer; diagrams stay on a light canvas in both desk themes because the seeded charts use literal pastel classDef fills), `global_sidebar.js` + `auto_collapse_sidebar.js` (sidebar tweaks), `unified_tab_controller.js` (the aggregated contacts/addresses directory + map on party forms), `quill_mentions.js` (`@`-mentions), `unlink_and_delete.js` ("Unlink and Delete" dialog on LinkExistsError), `primary_contact.js` (read-through contact fields), `file_list.js` (grid-default + preview overlay), `supplier_list.js` (group filters/indicators).
- **`gantt_widget/`** — the reusable embeddable Gantt: `gantt_widget.js` defines `erpnext_enhancements.gantt.mount(container, config)` (shipped in `erpnext_enhancements.bundle.js`, so it is available on every desk page). Config-driven (source doctype + field map + filters + optional dependency table + optional toolbar with checkbox-dropdown filters and a Today button/marker/default view; `today`/`tooltip`/`zoom` presets/`templates` passthrough for hosts with their own toolbars; composite `group_by`/`children` configs nest a second doctype under each root with `ref_doctype`/`ref_name` per row), read-only, one DHTMLX instance per mount via `Gantt.getGanttInstance()` so multiple embeds coexist; re-mounting a container destroys the previous instance; `set_zoom()`/`set_filters()`/`scroll_to_today()` for host-driven controls. Data comes only from `api/gantt.py::get_gantt_data` (server-side re-validation of the whole config, toolbar filter selections included). The vendored `lib/dhtmlxgantt.js` (+ `css/gantt_widget/` skin) lazy-loads on first mount — see the vendored-libraries note above for the globals shim. Embeds: the Project Schedule tab (`project_enhancements/project_gantt_widget.js`) and the Projects Dashboard portfolio Gantt (`custom_html_blocks/projects_dashboard.js`, composite mode).
- **`project_enhancements/`** — `project_form_script.js` (task tree tab), `project_brief.js`, `task_tree_manager.js` (the `TaskTreeManager` hierarchical grid), `gantt_zoom.js` (shared frappe-gantt zoom ladder — portfolio Gantt), `task_gantt.js`, `project_gantt_widget.js` (the Schedule tab's `custom_gantt_chart_html` — first real embed of `gantt_widget/`, filtered to the current Project's Tasks with a status filter + Today; replaced the legacy frappe-gantt renderer in `doctype/project/project.js`; placeholder on unsaved docs, destroy-on-refresh, IntersectionObserver lazy mount, realtime refresh), plus `dashboard_components/` (below) and the vendored `lib/frappe-gantt.umd.js` (with the Schedule tab and the portfolio Gantt both on the widget, its only nominal consumer is the `task_gantt.js` doc-comment stub — the UMD, `gantt_zoom.js` and `css/project_enhancements/frappe-gantt.css` are removal candidates).
- **`task_enhancements/`** — `task_enhancements.js` (patches `TreeView.get_tree_nodes` for the Hierarchical Task View).
- **`kiosk/`** — `app.js` + `geo.js` (the Time Kiosk PWA front-end, below).
- **`travel/`** — `travel_trip_map.js` (Google Maps map of agenda-stop POIs in the trip form's `agenda_map_html` field; key + POIs from `api.travel.get_trip_map_data`, map built lazily once its tab is visible to avoid the 0×0-container blank-map bug, pins carry always-visible name labels) and `itinerary.js` (the vanilla-JS `/itinerary` page UI — loaded by `www/itinerary.html` with the `?v=` cache-bust token, NOT via hooks; styles in `css/travel/itinerary.css`, `--ti-*` palette + `prefers-color-scheme` dark).

## The Comments App

- **`vue.global.js`** (vendored) provides `window.Vue` (Vue 3).
- **`comments.js`** defines `erpnext_enhancements.render_comments_app(frm, field_name)` — mounts a Vue notes UI into a `custom_comments_field` HTML field. CRUD goes through [`api/comments.py`](../api/README.md); attachments are uploaded as standalone `File` docs then `link_files_to_comment`.
- **`comments_auto.js`** holds `COMMENT_APP_DOCTYPES` (~23 doctypes) and auto-mounts the app on each. It **deliberately excludes** Project, Customer, Employee, Account, Timesheet, Contact — because those doctypes' own form scripts already call `render_comments_app` (avoiding a double mount).
- **`global_comments.js`** is separate: it patches the **native** Frappe Timeline (not the Vue app) to add an "Attach File" button. Both paths funnel through the same `link_files_to_comment` API.

## Live collaborative editing (`js/collab/`)

Google-Docs-style multi-user form editing for the doctypes configured on **ERPNext Enhancements Settings** (`collab_enabled` master switch + `collab_doctypes` child table — toggle doctypes with no deploy). The list ships to the client as `frappe.boot.collab_doctypes` (`boot.boot_session` via `extend_bootinfo`); [`api/collab.py`](../api/README.md) re-reads the settings as the security authority for every broadcast. One file, shipped via `erpnext_enhancements.bundle.js`:

- **`live_form_sync.js`** — the whole engine, one `LiveFormSync` instance per attached form (`frm._live_sync`):
  - **Outbound:** a wildcard `frappe.model.on` observer (parent + child-table doctypes) captures local edits, debounces 300ms per field, and POSTs them to `api.collab.broadcast_field_update`, which write-permission-checks and re-publishes to the doc's realtime room.
  - **Inbound:** `collab_field_update` events apply via `frappe.model.set_value` behind an origin/echo guard (correctness rests on value-equality loop breakers in both directions, not the guard flag). A field the local user is typing in is never clobbered — remote values park in `pending_remote` and apply on blur only if no newer local edit exists. Last-write-wins per field.
  - **Save sync:** on a collaborator's save (`doc_update`), a dirty form silently fetches the saved doc, merges it (local unsaved edits win per field), adopts the new `modified` timestamp (so the next local save passes `check_if_latest()`), and shows a passive "Updated by …" toast; Frappe's conflict banner is suppressed for collab forms only (guarded prototype patch on `show_conflict_message`). Clean forms keep Frappe's stock silent reload.
  - **Per-field presence:** focusin/focusout broadcast `api.collab.broadcast_focus` events; receivers outline the field (or grid cell) in the sender's deterministic palette color with a name badge ("Jane is editing this field"). A 30s heartbeat + 75s receiver-side TTL make presence self-healing — a crashed tab leaves no ghost highlight. Styles: [`css/collab.css`](#css-css) — **theme-aware**: JS assigns only a palette *class* (`.ee-collab-color-{0..5}`); the actual colors live in CSS with `[data-theme="dark"]` variants, so highlights adapt live when the desk theme switches.
  - **Scope guards:** attaches only to saved drafts (`docstatus === 0`, never new docs); child tables sync cell edits on saved rows only (row add/remove lands at the next save — unsaved rows have per-client local names); document-level "currently viewing" avatars remain Frappe's built-in FormViewers, untouched.

> **Onboarding a new doctype:** add it to the `collab_doctypes` table in ERPNext Enhancements Settings **after** auditing its form scripts for field-level change handlers with non-idempotent side effects — they re-fire on every receiving client when remote values are applied (see the checklist comment in `live_form_sync.js`).

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

The **single Projects Dashboard** is the "Projects Dashboard" Custom HTML Block
(`custom_html_blocks/projects_dashboard.{js,html,css}`), embedded on the Home / Projects
workspaces. It fetches through the whitelisted methods in
[`project_dashboard.py`](../project_enhancements/README.md#project-dashboard) and renders all
tabs itself; only these two **shared** helpers remain in this folder (preloaded globally
so the block can `frappe.require` them):

| File | Notes |
|---|---|
| `column_selector.js` | reusable "Columns" dropdown; localStorage-persisted |
| `column_resizer.js` | drag-to-resize table columns; localStorage-persisted per tab |

> **Consolidated in v1.159.8.** A parallel *desk page* dashboard (`page/project_dashboard/`
> + per-tab components `dashboard_api.js`, `dashboard_view.js`, `priority_overview.js`,
> `active_internal_projects.js`, `completed_projects.js`, `portfolio_gantt.js`,
> `tasks_view.js`) was removed — two ~1,200-line implementations of the same thing.
> Everything now lives in the Custom HTML Block.

## Kiosk PWA front-end (`js/kiosk/`)

`www/kiosk.html` injects a boot payload (`window.KIOSK_BOOT` = employee, settings, current status, csrf; plus `window.KIOSK_BUILD`, the per-deploy cache-bust token) and loads `app.js` + `geo.js` (both URL-versioned with `?v=<build>`), then registers `/kiosk-sw.js?v=<build>`.

- **`app.js`** — UI / clock state machine. Seeds UI from boot status, confirms via `get_current_status`, and switches the card between idle / active (Open) / paused (break) views. Actions POST to [`api.time_kiosk.log_time`](../api/README.md); it starts/stops `KioskGeo` **only while Open** (never on break) and posts config (csrf, batch size) to the service worker. Also owns the deploy-update loop (`registration.update()` on foreground + hourly; one deferred reload when a new worker takes control) and the standalone-mode back/forward/refresh bar (`setupNav` — shown only when there's no browser chrome).
- **`geo.js`** — geolocation on the **main thread** (workers can't read GPS). `warmup()` primes the permission on page visit; `start()` runs `watchPosition` **and** a heartbeat interval; `consider()` filters fixes (drop below `min_accuracy_m`; record only when moved ≥ `distance_filter_m` or the heartbeat elapsed). Accepted points are posted to the **service worker** (`{type:'enqueue'}`), which owns durable batching/upload. `visibilitychange`/`online` re-acquire the wake lock, grab a catch-up fix, and flush. Exposes `window.KioskGeo`.

See [`www/README.md`](../www/README.md) for the service-worker / offline side.

## CSS (`css/`)

| File | Styles | Load |
|---|---|---|
| `desk_enhancements.bundle.css` | Desk "Sapphire glass" theme + Procurement Tracker, Comments App, Kanban, activity numbering, filter-help | app_include_css |
| `desk_addons.bundle.scss` | Imports the six feature stylesheets below (old include order, after `desk_enhancements`). A `.scss` entry **on purpose**: sass inlines its extension-less imports against the real path; a plain `.css` entry's `@import`s get resolved against the postcss plugin's temp dir and ENOENT the whole `bench build` (broke the v0.8.1 Frappe Cloud deploy). Builds to `desk_addons.bundle.css` | app_include_css |
| `collab.css` | Live-collab per-field presence highlights (`.ee-collab-focus*` ring + name badge; palette classes with light/dark `[data-theme]` variants) | `desk_addons.bundle.scss` |
| `login_enhancements.bundle.css` | Login/forgot/signup pages | web_include_css |
| `gantt_widget/dhtmlxgantt.css` | **Vendored** DHTMLX Gantt 10 Standard (MIT) skin | lazy `frappe.require` by `js/gantt_widget/gantt_widget.js` (not bundled — only Gantt-mounting pages pay the 140K; safe as a raw path because a vendored file never changes) |
| `gantt_widget/gantt_widget.css` | Widget container chrome (toolbar/filters/overlays/host sizing), desk-font override (stops the skin's remote Inter fetch) | `desk_addons.bundle.scss` — **must stay bundled**: shipped as a raw `/assets` path in v1.163–1.165 it was served immutable *and* left stale on disk by a deploy, so every rule added after v1.163 never reached browsers (v1.165.1) |
| `global_enhancements/horizontal_scroll.css` | Opportunity Kanban horizontal scroll layout | doctype_css["Opportunity"] |
| `global_enhancements/triton_widget.css` | Triton assistant FAB + chat panel | `desk_addons.bundle.scss` |
| `kiosk/kiosk.css` | Standalone Time Kiosk PWA shell | `www/kiosk.html` `<link>` (not hooks) |
| `project_enhancements/frappe-gantt.css` | **Vendored** frappe-gantt styles | `desk_addons.bundle.scss` |
| `project_enhancements/task_tree.css` | Hierarchical task grid + dashboard column selector | `desk_addons.bundle.scss` |
| `quickbooks_online/qbo_dashboard.css` | QBO status dashboard | `desk_addons.bundle.scss` |
| `task_enhancements/task_enhancements.css` | Hierarchical task tree connectors | `desk_addons.bundle.scss` |

## Gotchas

- Comments App double-mount avoidance: the six doctypes whose form scripts call `render_comments_app` are intentionally absent from `COMMENT_APP_DOCTYPES`.
- `kiosk.css` is the one CSS file **not** loaded via `hooks.py` (it's referenced by `www/kiosk.html`).
- Many global monkey-patches (`KanbanView.refresh`, `FileView.setup_view`, `TreeView.get_tree_nodes`, `msgprint`/`show_alert`/`request.error`) are guarded by idempotency flags and **may need revisiting on Frappe upgrades**.
- `task_gantt.js` is a doc-comment stub — the scroll-to-today logic it describes is not in the file body (flagged in-file).
- CDN dependencies: `process_document.js` (Mermaid) and `telephony_client.js` (Twilio SDK) lazy-load third-party scripts from jsDelivr at runtime.

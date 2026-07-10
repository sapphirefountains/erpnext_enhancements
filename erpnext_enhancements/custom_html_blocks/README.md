# Custom HTML Block — block sources (source of truth)

This folder is the **source of truth** for the Frappe **Custom HTML Blocks** — dashboard widgets embedded on workspaces. The blocks here are *Projects Dashboard*, *Task Dashboard*, *Morning Briefing*, and *Desk Shortcuts*.

> ✅ **Repo is the source of truth (v1.69.0).** On every `bench migrate`, `erpnext_enhancements.setup.custom_html_blocks.sync_custom_html_blocks` (an `after_migrate` hook) **upserts** all four blocks from these files: missing blocks are created and any block whose `html`/`script`/`style` has drifted from the source is **overwritten**, then the blocks are placed on the **Home** workspace (idempotent append). So edit the files here and `bench migrate` to deploy — **UI-side edits to these blocks do not survive a migrate.** (The older insert-only seed patches — `seed_task_dashboard_block`, `seed_morning_briefing_block`, `seed_desk_shortcuts_block` — are now superseded by this seeder and left only for history; they no-op once a block exists.)

## Files — Projects Dashboard

| File | Role |
|---|---|
| `projects_dashboard.html` | The block markup: a tabbed shell (Priority Overview · Active Internal Projects · Completed Projects · Portfolio Gantt) + search + Gantt filter controls + an empty `#dashboard-content`. |
| `projects_dashboard.js` | Runs in the block sandbox (`root_element`). Loads the shared `ColumnSelector` + `ColumnResizer` assets, fetches via the [Project Dashboard page's](../erpnext_enhancements/project_enhancements/README.md#project-dashboard) whitelisted methods, and renders editable tables + a frappe-gantt portfolio chart (collapsible nodes, drag-to-reschedule, scroll preservation). The three list tabs support show/hide columns **and drag-to-resize column widths** (drag a header's right edge; **Reset widths** in the toolbar restores defaults) — widths persist per user in localStorage under `chb_*_widths`. Edits persist back through the same methods. |
| `projects_dashboard.css` | Styles the block. Per-bar Gantt fill colours are injected dynamically by the JS, not defined here. |

## Files — Task Dashboard (morning TV screen, v1.4.0)

The Jun 9 meeting's morning-screen refinements: the **top-10 priority projects as a
list, all at once** (with PM + tech lead per project), **overdue/at-risk tasks**,
**today's tasks with the assigned technicians' names**, and **today's public calendar
events**. Data comes from one whitelisted endpoint —
`erpnext_enhancements.api.task_dashboard.get_task_dashboard_data` — which gates on
staff roles and then fetches permission-free, so per-user User Permissions can't
empty a shared wall display. Refreshes on the `project_dashboard_updated` realtime
event (debounced) plus a 5-minute kiosk fallback; timers/subscriptions are stored on
`window` so workspace re-renders never stack them.

| File | Role |
|---|---|
| `task_dashboard.html` | Panel skeleton: header with live clock, Top-10 projects rail (left), Overdue / Today / Calendar stack (right). |
| `task_dashboard.js` | Block-sandbox script: fetch + render, clock, guarded realtime/interval refresh. |
| `task_dashboard.css` | Shadow-root styles. Structural colors from Frappe CSS variables (they pierce the shadow boundary, so both themes work); literal accents only for priority/overdue semantics. |

**Install:** `bench migrate` creates the block and auto-places it on **Home**
(via `sync_custom_html_blocks`). To show it on another workspace too, edit that
Workspace and add the "Task Dashboard" Custom HTML Block.

## Files — Desk Shortcuts (configurable Home icons, v1.30.0)

A grid of clickable icon tiles for the custom tools (Time Kiosk, Inventory Scanner,
Maintenance Wizard, …), shown on the **Home** workspace. Unlike native workspace
shortcuts — which can only be gated whole-workspace by role — these are **per-user**:
the tile list is curated in the **Enhancement Desk Shortcut** doctype (System Manager
only; per icon: enabled, roles, *and* specific users) and computed for the session user
in `erpnext_enhancements.api.desk_shortcuts.get_visible_shortcuts_for_user`, shipped as
`frappe.boot.ee_desk_shortcuts` by `boot.py`. The block just paints that list, so each
user sees only their applicable tools and config edits apply on the next desk load.

The gating is **cosmetic** — every target page enforces its own permissions, so an
unauthorized click still gets "not permitted." The whole block hides itself when the
user has no visible shortcuts.

| File | Role |
|---|---|
| `desk_shortcuts.html` | Shell: a `Quick Access` header + an empty `#eds-grid` the JS fills (hidden until populated). |
| `desk_shortcuts.js` | Block-sandbox script: reads `frappe.boot.ee_desk_shortcuts`, builds icon tiles, routes on click (Page/DocType/Report via `frappe.set_route`, URL via `window.location`/`window.open`). |
| `desk_shortcuts.css` | Shadow-root styles from Frappe CSS variables (both themes). Icons are **emoji** — `frappe.utils.icon` SVG-sprite icons can't resolve `<use href="#…">` across the shadow boundary. |

**Install:** `bench migrate` creates the block (`patches.seed_desk_shortcuts_block`), seeds
the seven default shortcut rows (`patches.seed_desk_shortcuts`, insert-only), and places the
block on Home (`patches.place_desk_shortcuts_on_home`, idempotent) — no manual placement
step, unlike the other blocks. Add more tools later by creating an **Enhancement Desk
Shortcut** row (no code change).

## Relationship to the desk Project Dashboard

This Custom HTML Block is a lighter, embeddable cousin of the full **Project Dashboard** desk page. It reuses the same server endpoints (`erpnext_enhancements.project_enhancements.page.project_dashboard.*`) and the same shared front-end helpers (`ColumnSelector`, frappe-gantt). For the full-featured, tabbed, realtime experience, see the desk page documented in the [Project Enhancements README](../erpnext_enhancements/project_enhancements/README.md).

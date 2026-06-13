# Custom HTML Block — exported block sources

This folder is the **exported source** of the Frappe **Custom HTML Blocks** — dashboard widgets that are authored and stored **in the Frappe UI** (Custom HTML Block doctype) and embedded on workspaces. The blocks here are *Projects Dashboard*, *Task Dashboard*, *Morning Briefing*, and *Desk Shortcuts*.

> ⚠️ **Source-of-truth caveat.** The live widgets live in the database, edited through the Frappe UI. These files are the version-controlled **backup / source copy**. The two can drift: edit here and paste back into the UI to deploy, or export from the UI after editing there. Keep them in sync manually. (Exception: *Task Dashboard* is **created** from these files by the `seed_task_dashboard_block` patch if the block doesn't exist yet — insert-only, so UI edits after creation still win until you paste.)

## Files — Projects Dashboard

| File | Role |
|---|---|
| `projects_dashboard.html` | The block markup: a tabbed shell (Priority Overview · Active Internal Projects · Completed Projects · Portfolio Gantt) + search + Gantt filter controls + an empty `#dashboard-content`. |
| `projects_dashboard.js` | Runs in the block sandbox (`root_element`). Loads the shared `ColumnSelector` asset, fetches via the [Project Dashboard page's](../erpnext_enhancements/project_enhancements/README.md#project-dashboard) whitelisted methods, and renders editable tables + a frappe-gantt portfolio chart (collapsible nodes, drag-to-reschedule, scroll preservation). Edits persist back through the same methods. |
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

**Install:** `bench migrate` creates the block (patch above); then edit the target
Workspace once and add the "Task Dashboard" Custom HTML Block.

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

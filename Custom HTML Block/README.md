# Custom HTML Block — exported block sources

This folder is the **exported source** of the Frappe **Custom HTML Blocks** — dashboard widgets that are authored and stored **in the Frappe UI** (Custom HTML Block doctype) and embedded on workspaces. Two blocks live here: *Projects Dashboard* and *Task Dashboard*.

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

## Relationship to the desk Project Dashboard

This Custom HTML Block is a lighter, embeddable cousin of the full **Project Dashboard** desk page. It reuses the same server endpoints (`erpnext_enhancements.project_enhancements.page.project_dashboard.*`) and the same shared front-end helpers (`ColumnSelector`, frappe-gantt). For the full-featured, tabbed, realtime experience, see the desk page documented in the [Project Enhancements README](../erpnext_enhancements/project_enhancements/README.md).

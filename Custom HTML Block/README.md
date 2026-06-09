# Custom HTML Block — Projects Dashboard

This folder is the **exported source** of a Frappe **Custom HTML Block** named *Projects Dashboard* — a dashboard widget that is authored and stored **in the Frappe UI** (Custom HTML Block doctype) and embedded on a workspace.

> ⚠️ **Source-of-truth caveat.** The live widget lives in the database, edited through the Frappe UI. These files are the version-controlled **backup / source copy**. The two can drift: edit here and paste back into the UI to deploy, or export from the UI after editing there. Keep them in sync manually.

## Files

| File | Role |
|---|---|
| `projects_dashboard.html` | The block markup: a tabbed shell (Priority Overview · Active Internal Projects · Completed Projects · Portfolio Gantt) + search + Gantt filter controls + an empty `#dashboard-content`. |
| `projects_dashboard.js` | Runs in the block sandbox (`root_element`). Loads the shared `ColumnSelector` asset, fetches via the [Project Dashboard page's](../erpnext_enhancements/project_enhancements/README.md#project-dashboard) whitelisted methods, and renders editable tables + a frappe-gantt portfolio chart (collapsible nodes, drag-to-reschedule, scroll preservation). Edits persist back through the same methods. |
| `projects_dashboard.css` | Styles the block. Per-bar Gantt fill colours are injected dynamically by the JS, not defined here. |

## Relationship to the desk Project Dashboard

This Custom HTML Block is a lighter, embeddable cousin of the full **Project Dashboard** desk page. It reuses the same server endpoints (`erpnext_enhancements.project_enhancements.page.project_dashboard.*`) and the same shared front-end helpers (`ColumnSelector`, frappe-gantt). For the full-featured, tabbed, realtime experience, see the desk page documented in the [Project Enhancements README](../erpnext_enhancements/project_enhancements/README.md).

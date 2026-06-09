# Project Enhancements

Customizes ERPNext's **Project** doctype and the workflow around it. The headline feature is a custom, realtime **Project Dashboard** desk page; the module also adds a **Master Project** doctype (groups projects into a program/portfolio), procurement-status rollups, a project-merge tool, Opportunity→Project conversion, and doctype-dashboard overrides.

Most server entry points are `@frappe.whitelist()` methods called from the page/form scripts; a few are wired through [`../hooks.py`](../hooks.py).

## File map

| File | Purpose | Key functions / classes | Wiring |
|---|---|---|---|
| `__init__.py` | Module helpers: procurement status, attachment sync, start reminders, dashboard override, project comments | `get_procurement_status`, `get_procurement_documents`, `sync_attachments_from_opportunity`, `send_project_start_reminders`, `get_dashboard_data`, `get_project_comments`/`add`/`delete`/`update` | Project `after_save` → `sync_attachments_from_opportunity`; `scheduler.daily` → `send_project_start_reminders`; `override_doctype_dashboards["Project"]` → `get_dashboard_data` |
| `setup_address.py` | One-off installer for Address map Custom Fields | `setup_fields` | Manual (`bench execute`); not in hooks |
| `doctype/master_project/master_project.py` | Master Project controller; rollup of member Projects + Tasks | `MasterProject.get_projects_and_tasks` | Doctype controller |
| `doctype/master_project/master_project.js` | Read-only Projects/Tasks rollup tables on the form | `render_projects_table`, `render_tasks_table` | Doctype form script |
| `doctype/project/project.py` | List-view grouping + printable Project Brief data | `get_project_grouping_option`, `get_project_brief_data` | Whitelisted (client scripts) |
| `doctype/project/project.js` | Interactive Gantt, health banner, resource heatmap, dependency linking, reminder button | two `frappe.ui.form.on("Project", {refresh})` handlers | `doctype_js["Project"]` |
| `doctype/project/project_list.js` | Project list-view tweaks | — | list view |
| `doctype/address/address.js` | Live full-address build + Google Maps embed | Address form handlers | `doctype_js["Address"]` |
| `doctype/project_dashboard_settings/*.py` | Single doctype: legacy permitted-roles list for the dashboard | `ProjectDashboardSettings` | controller |
| `doctype/project_dashboard_permitted_role/*.py` | Child table: one `role` per row | `ProjectDashboardPermittedRole` | child-table controller |
| `page/project_dashboard/project_dashboard.py` | All backend data / permission / inline-edit endpoints for the dashboard | `check_permission`, `get_project_data`, `get_gantt_tasks_for_project`, `get_master_project_projects`, `update_task_*`, `add_task_dependency`, `publish_realtime_update`, … | Whitelisted (page JS); `publish_realtime_update` via `doc_events` |
| `page/project_dashboard/project_dashboard.js` | Page shell: permission gate, tabbed routing, lazy component loading, search/filters, auto-save | `on_page_load`, `initialize_dashboard`, `handleRouteChange` | Frappe Page script for `project-dashboard` |

Related code outside this folder:
- `project_merge.py` (repo root) — merge one Project into another by re-pointing all linked docs. Whitelisted; called from `public/js/project_merge.js`.
- `opportunity_enhancements.py` (repo root) — `make_project` override (stamps the source Opportunity). Wired via `override_whitelisted_methods`.
- `dashboard_overrides.py` (repo root) — adds a "Travel" connections group to the **Employee** dashboard. Wired via `override_doctype_dashboards["Employee"]`.
- The dashboard's tab components live in `public/js/project_enhancements/dashboard_components/` — see the [public README](../public/README.md#project-dashboard-components).

## Project Dashboard

- **Page:** a standard Frappe Page named `project-dashboard` (module "Projects"). `project_dashboard.js` builds an app-page shell with several tabs and routes via `frappe.set_route("project-dashboard", <tab>)`. Each tab is a separate component class lazy-loaded with `frappe.require` from `public/js/project_enhancements/dashboard_components/`.
- **Data source:** the whitelisted methods in `project_dashboard.py`. `get_project_data` uses bulk SQL/`get_all` for task counts and derives assignees from **ToDo** rows (Project has no `project_user` column — selecting one would raise "Unknown column").
- **Realtime:** `publish_realtime_update(doc, method)` fires `frappe.publish_realtime("project_dashboard_updated", …)` and is registered on both **Task** `on_update` and **Project** `on_update`. Client code subscribes and refreshes the health banner / Gantt in place (preserving scroll position).
- **Permission gating:** `check_permission()` prefers native Page role permissions (Custom Role + Has Role for the "Project Dashboard" page), falling back to the legacy `Project Dashboard Settings.permitted_roles` child table. Once page access is granted, list/Gantt reads fetch with ignore-permissions (a portfolio view, so per-record User Permissions don't narrow it); inline-edit/write endpoints still enforce per-document `frappe.has_permission("Project", "write", …)`, and `update_project_details` restricts edits to a whitelisted `EDITABLE_PROJECT_FIELDS` set.

## Master Project

A lightweight container doctype grouping ordinary Projects into a program/portfolio. Projects join via the **`Project.custom_master_project`** Link field (no child table on the Master side); **`Project.custom_subproject_order`** controls ordering under the master. `get_projects_and_tasks` returns member Projects and their Tasks for the form's read-only HTML tables. The dashboard's `get_master_project_projects` / `update_master_project_structure` reuse the same grouping (the latter persists drag-reordering).

## `hooks.py` touchpoints

- `doc_events`: Project `after_save` → `sync_attachments_from_opportunity`; Project/Task `on_update` → `…project_dashboard.publish_realtime_update`.
- `scheduler_events.daily` → `send_project_start_reminders`.
- `override_doctype_dashboards`: `Project` → `get_dashboard_data`; `Employee` → `dashboard_overrides.get_data`.
- `override_whitelisted_methods`: `erpnext…opportunity.make_project` → `opportunity_enhancements.make_project`.

## Gotchas

- **Mixed indentation:** most files use tabs; `dashboard_overrides.py` uses 4 spaces.
- `get_all_projects_for_gantt` deliberately drops the `check_permission()` gate (uses native Page roles) and filters the portfolio Gantt to client-facing `project_type in (Build, Design, Rent, Service)`.
- Several Task fields are queried conditionally via `frappe.get_meta(...).has_field(...)` (`custom_is_recurring`, `baseline_start_date/baseline_end_date`) because they are optional site-level custom fields.
- `merge_projects` uses `frappe.db.set_value` for child tables/Singles (speed) but `doc.save()` for parents (to fire controller logic), and `log_error`s per-doc failures rather than aborting the whole merge. `get_linked_doctypes` discovers Project links dynamically from metadata, so any new Link-to-Project field automatically expands merge scope.
- The Gantt/health logic in `project.js` monkey-patches the HTML field's `.refresh()` and depends on the global frappe-gantt UMD lib + `gantt_zoom.js` (both loaded via `app_include_js`); idempotency flags prevent rebinding across refreshes.

# Custom HTML Block — block sources (source of truth)

This folder is the **source of truth** for the Frappe **Custom HTML Blocks** — dashboard widgets embedded on workspaces. The blocks here are *Projects Dashboard*, *Task Dashboard*, *Morning Briefing*, and *Desk Shortcuts*.

> ✅ **Repo is the source of truth (v1.69.0).** On every `bench migrate`, `erpnext_enhancements.setup.custom_html_blocks.sync_custom_html_blocks` (an `after_migrate` hook) **upserts** all four blocks from these files: missing blocks are created and any block whose `html`/`script`/`style` has drifted from the source is **overwritten**, then the blocks are placed on the **Home** workspace (idempotent append). So edit the files here and `bench migrate` to deploy — **UI-side edits to these blocks do not survive a migrate.** (The older insert-only seed patches — `seed_task_dashboard_block`, `seed_morning_briefing_block`, `seed_desk_shortcuts_block` — are now superseded by this seeder and left only for history; they no-op once a block exists.)

## Files — Projects Dashboard

| File | Role |
|---|---|
| `projects_dashboard.html` | The block markup: a tabbed shell (Priority Overview · Active Internal Projects · Completed Projects · Portfolio Gantt) + search + Gantt filter controls + an empty `#dashboard-content`. |
| `projects_dashboard.js` | Runs in the block sandbox (`root_element`). Loads the shared `ColumnSelector` + `ColumnResizer` assets, fetches via the [Project Dashboard page's](../project_enhancements/README.md#project-dashboard) whitelisted methods, and renders editable tables + the portfolio Gantt via the embeddable Gantt widget (`erpnext_enhancements.gantt.mount`, composite mode: Master Project groups -> Projects -> Task trees through the permission-checked `api/gantt.py::get_gantt_data`; read-only for now — drag-editing returns with the widget's edit opt-in milestone). Each project row expands via its caret to lazily load that project's tasks; bars are coloured by `project_type` (tasks a lighter shade of their project) with an on-screen legend; filters cover a find-a-project search plus status, project, type, customer, a date window and at-risk-only; grid columns are individually toggleable; the whole view (filters, columns, zoom, expanded projects) persists per user. The three list tabs support show/hide columns **and drag-to-resize column widths** (drag a header's right edge; **Reset widths** in the toolbar restores defaults) — widths persist per user in localStorage under `chb_*_widths`. Edits persist back through the same methods. |
| `projects_dashboard.css` | Styles the block, including the Portfolio Gantt's per-level (`pg-master`/`pg-project`/`pg-task`) bar and row styling; the widget lazy-loads the DHTMLX skin itself. |

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

## Files — Department Dashboard widgets (v1.251.0)

Each of the nine department dashboards under `kpi_dashboards/workspace/` used to carry a
single block: the KPI Cockpit. Finance was the exception, with six operational widgets.
This adds four widgets to each of the other seven — Sales, Operations, Design, Production,
Marketing, HR and Executive — so a dashboard shows what to *do*, not only how things went.

| Department | Blocks | Feed |
|---|---|---|
| Sales | Speed to Lead · Stalled Deals · Hand-Off Backlog · Renewal Radar | [`api/sales_dashboard.py`](../api/sales_dashboard.py) |
| Operations | Today's Visits · Fleet & Device Health · Chemistry Alerts · Labor Capture Gaps | [`api/operations_dashboard.py`](../api/operations_dashboard.py) |
| Design | Design WIP · Awaiting Sign-Off · Hand-Off Readiness · Hydraulic Headroom | [`api/design_dashboard.py`](../api/design_dashboard.py) |
| Production | Build WIP & Aging · Milestone Slippage · Material Readiness · Hours vs Budget | [`api/production_dashboard.py`](../api/production_dashboard.py) |
| Marketing | Funnel Cascade · Channel Spend & CPL · Unsourced Leads · Source Health | [`api/marketing_dashboard.py`](../api/marketing_dashboard.py) |
| HR | Training Compliance · Time Capture · Headcount Movement · People Calendar | [`api/hr_dashboard.py`](../api/hr_dashboard.py) |
| Executive | Company Scorecard · Cash & Receivables · Bookings & Backlog · Risk Queue | [`api/executive_dashboard.py`](../api/executive_dashboard.py) |

**Live worklists, snapshot trends.** Anything actionable — an unanswered lead, today's
visits, an overdue sign-off — queries its source doctypes on load, because a queue is only
worth showing if it is true right now. Anything with a trend or a target reads the nightly
`KPI Snapshot` instead. The whole Executive dashboard is on the snapshot side: a
cross-department view exists to be compared across departments and across days, and a number
that changes on every refresh can do neither.

**Every widget is off until someone turns it on.** Each has its own Check on
**ERPNext Enhancements Settings** (grouped by department), defaulting to `0`, exactly like the
Finance widgets. A placed-but-disabled block renders a muted "turned off" notice rather than
an error — so after `bench migrate` the new dashboards look empty until the toggles are
ticked. That is deliberate, and it is the first thing to check when a widget "doesn't work".

**Gating lives in one place.** [`api/dashboard_widgets.py`](../api/dashboard_widgets.py)
holds the `WIDGETS` registry (widget → settings field) and the `widget_feed` decorator that
applies the department role gate and the toggle. Roles are imported from `api/kpi.py`, never
re-declared, so a widget can never be visible to someone who cannot see the same department's
KPI Cockpit. Feeds take **no arguments** — there is nothing to validate, and no way for a
caller to widen a query.

Where a KPI already counts the same population, the widget reuses its threshold (stalled
deals at 14 days, for instance). A widget that disagrees with the number above it is worse
than no widget.

`tests/test_dashboard_widgets.py` holds the four moving parts together — the registry, the
settings fields, the seeder's `BLOCKS` + `DEPARTMENT_DASHBOARD_BLOCKS`, and the shipped
workspace JSONs. Each can drift silently: a placement naming an unseeded block renders an
**empty div** with no error and no log line.

The shared HTML/CSS shell (card → head → rows, plus pills, bars and stat tiles) is
deliberately uniform across all 28 blocks; structural colours come from Frappe CSS variables
so both themes work, and literal colours appear only where the colour *is* the meaning.

## Relationship to the desk Project Dashboard

This Custom HTML Block is a lighter, embeddable cousin of the full **Project Dashboard** desk page. It reuses the same server endpoints (`erpnext_enhancements.project_enhancements.page.project_dashboard.*`) and the same shared front-end helpers (`ColumnSelector`, the embeddable Gantt widget). For the full-featured, tabbed, realtime experience, see the desk page documented in the [Project Enhancements README](../project_enhancements/README.md).

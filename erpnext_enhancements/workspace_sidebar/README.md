# `workspace_sidebar/` — dashboard sidebar definitions

JSON `Workspace Sidebar` records, one per KPI dashboard workspace. They control the sidebar
grouping and ordering that appears alongside each dashboard.

| File | Workspace |
|---|---|
| `executive_dashboard.json` | Executive |
| `finance_dashboard.json` | Finance |
| `sales_dashboard.json` | Sales |
| `marketing_dashboard.json` | Marketing |
| `operations_dashboard.json` | Operations |
| `production_dashboard.json` | Production |
| `design_dashboard.json` | Design |
| `product_dashboard.json` | Product |
| `hr_dashboard.json` | HR |
| `kpi_dashboards.json` | The KPI Dashboards module workspace |

These are app-owned records synced by `bench migrate`, and they pair with the workspaces
under [`../kpi_dashboards/workspace/`](../kpi_dashboards/README.md).

Overrides on **core, erpnext-owned** workspaces and sidebars do not belong here — those are
re-asserted on `after_migrate` by [`../setup/workspace_tweaks.py`](../setup/README.md),
because Frappe syncs standard records from every app and a plain file would be overwritten by
whichever app imported last.

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
under [`../erpnext_enhancements/kpi_dashboards/`](../erpnext_enhancements/kpi_dashboards/README.md).

Overrides on **core, erpnext-owned** workspaces and sidebars do not belong there — those are
re-asserted on `after_migrate` by
[`../erpnext_enhancements/setup/workspace_tweaks.py`](../erpnext_enhancements/setup/README.md),
because Frappe syncs standard records from every app and a plain file would be overwritten by
whichever app imported last.

## Sidebars are also what make a desk tile visible

The tiles on the Desk home grid are `Desktop Icon` records, not workspaces — but a tile of
type `Link` only renders if a `Workspace Sidebar` of the same name exists **and has items**.
`get_desktop_icons()` resolves each one through
`bootinfo.workspace_sidebar_item[label.lower()]` and silently drops any tile whose sidebar is
missing or empty.

That is worth knowing in both directions:

- A `Desktop Icon` created without a sidebar simply never appears, with no error. The stale
  `Learning` tile removed in v1.326.0 had been invisible this way for exactly that reason.
- A workspace added to this app *after* install gets neither record — core only builds them
  during install/upgrade — so it is absent from the desk entirely. Training and Shipping both
  were. [`setup/desktop_icons.py`](../erpnext_enhancements/setup/README.md) now creates the
  pair on `after_migrate`, reusing Frappe's own `add_workspace_to_desktop`, which appends to
  an existing sidebar rather than replacing it.

Tile *artwork* is a separate matter and is **not** `Workspace.icon` — see the setup README.

Note also that `desktop_icon` is an app-level sync folder exactly like `workspace_sidebar`
(`frappe/model/sync.py` scans `["desktop_icon", "workspace_sidebar", "sidebar_item_group"]`),
so the JSON-parse trap described below applies there too if this app ever ships one. It
currently does not, deliberately.

## Why this document is here and not in the directory it describes

It used to live at `erpnext_enhancements/workspace_sidebar/README.md`, and on 2026-07-31 it
took production's `bench migrate` down:

```
bad json: .../erpnext_enhancements/workspace_sidebar/README.md
orjson.JSONDecodeError: unexpected character: line 1 column 1 (char 0)
```

`workspace_sidebar` is not an ordinary source folder. Frappe's `frappe/model/sync.py` keeps a
list of `IMPORTABLE_DOCTYPES`, and for each entry it scans the matching **app-level** directory
and calls `orjson.loads()` on **every file it finds** — there is no `*.json` filter and no
skip-list. Frappe 16.29.0 added `("desk", "workspace_sidebar")` to that list, so a directory
that had been inert for months became an import target, and the first non-JSON file in it
aborted the whole schema sync.

The README had been sitting there since 2026-07-29 without incident. Nothing in this repo
changed to cause it; the Frappe upgrade did.

**So: these directories may contain nothing but `.json`.** As of the fix, the app-level
directories Frappe imports from are `permission_type`, `doctype`, `page`, `report`,
`dashboard_chart_source`, `print_format`, `web_page`, `website_theme`, `web_form`,
`web_template`, `notification`, `print_style`, `workspace`, `workspace_sidebar`,
`onboarding_step`, `module_onboarding`, `form_tour`, `client_script`, `server_script`,
`custom_field` and `property_setter`. `scripts/check_import_dirs.py` enforces it in CI, and
the list is read from the installed Frappe rather than hard-coded, so it keeps up when Frappe
adds another.

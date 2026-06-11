# ERPNext Enhancements

A single [Frappe](https://frappeframework.com/) v16 app that bundles **Sapphire Fountains'** customizations and enhancements to ERPNext. It consolidates what used to be several separate apps (CRM, Global, Project, Task, and QuickBooks Time enhancements) into one installable app made up of eight Frappe **modules**, plus a standalone **Time Kiosk** Progressive Web App and a large library of desk (browser) customizations.

- **App name:** `erpnext_enhancements`
- **Publisher:** Sapphire Fountains · `info@sapphirefountains.com`
- **License:** MIT
- **Requires:** Frappe `>=16.0.0,<17.0.0`, Python `>=3.10`
- **Current version:** see [`erpnext_enhancements/__init__.py`](erpnext_enhancements/__init__.py) and [`CHANGELOG.md`](CHANGELOG.md)

> **New to this codebase?** Read this file top-to-bottom, then jump to the [per-module documentation index](#documentation-index). Every source file in the app also carries inline docstrings/comments describing what it does and how it is wired.

---

## Table of contents

1. [What this app does](#what-this-app-does)
2. [Architecture at a glance](#architecture-at-a-glance)
3. [Module map](#module-map)
4. [How customizations are wired (`hooks.py`)](#how-customizations-are-wired-hookspy)
5. [External integrations](#external-integrations)
6. [Key subsystems](#key-subsystems)
7. [Installation](#installation)
8. [Development workflow](#development-workflow)
9. [Conventions](#conventions)
10. [Documentation index](#documentation-index)

---

## What this app does

ERPNext Enhancements layers Sapphire Fountains' business processes onto a stock ERPNext install without forking ERPNext itself. Broadly, it provides:

- **Project management upgrades** — a custom **Project Dashboard** desk page (tabbed, realtime, portfolio Gantt + resource heatmap), a **Master Project** doctype that groups projects into programs, procurement-status rollups, project merging, and Opportunity→Project conversion with attachment carry-over.
- **Field-service maintenance** (Sapphire Maintenance) — template-driven maintenance records that, on submit, create Stock Entries, Timesheets, warranty RMAs, and draft Sales Invoices, plus a customer portal and predictive scheduling.
- **Time tracking** — a standalone, installable **Time Kiosk PWA** with battery-aware GPS tracking, offline queueing, and automatic consolidation into ERPNext Timesheets.
- **CRM enhancements** — Opportunity customizations and automatic Google Drive project-folder provisioning.
- **Integrations** — QuickBooks Online (accounting sync), Twilio/Triton telephony (click-to-call + SMS), Google Analytics 4 / Search Console dashboard, and the "Triton" in-app AI assistant.
- **Live collaborative editing** — Google-Docs-style multi-user editing on the ten most-used doctypes: field changes stream in real time to everyone viewing the same document, saves apply silently on collaborators' screens (no more "Document has been modified" conflicts between collaborators), and per-field highlights show who is editing what.
- **Desk usability fixes** — Kanban drag/scroll/leak fixes, a custom Vue comments app, safe form drafts, an aggregated contacts/addresses directory, sidebar tweaks, and a pile of performance hotfixes for upstream Frappe bugs.

## Architecture at a glance

This is a conventional Frappe app. If you know Frappe, the layout will be familiar; if not, here is the mental model:

```
erpnext_enhancements/                 ← the Python package (one folder per Frappe module + shared code)
├── hooks.py                          ← THE control center: every customization registers here
├── modules.txt                       ← the 8 Frappe modules this app ships
├── patches.txt                       ← ordered list of one-time migration patches
├── <module>/                         ← one folder per module (see "Module map")
│   ├── doctype/<doctype>/            ← DocType: .json (schema) + .py (controller) + .js (form script)
│   ├── page/<page>/                  ← desk Page: .json + .py (backend) + .js (frontend)
│   └── ...
├── api/                              ← @frappe.whitelist() HTTP endpoints (called by JS / external clients)
├── script_migrations/               ← Python ports of legacy DB-stored Client/Server Scripts
├── patches/                          ← one-time migration scripts (run by `bench migrate`)
├── setup/                            ← idempotent setup run after every migrate (custom fields, etc.)
├── utils/                            ← shared helpers (global Triton sync, delete patching)
├── fixtures/                         ← exported records installed on migrate (custom fields, workflows…)
├── public/                           ← browser assets (JS form scripts, CSS, kiosk front-end)
├── www/                              ← the standalone Time Kiosk PWA (`/kiosk`)
├── templates/                        ← Jinja web templates
└── tests/                            ← test suite

Custom HTML Block/                    ← exported source of a Frappe "Custom HTML Block" dashboard widget
sync_time_kiosk.py                    ← standalone REST tool: Job Intervals → Timesheets
```

**Three layers** carry most of the behavior:

1. **Server (Python).** DocType controllers (`*.py` next to `*.json`), whitelisted endpoints in [`api/`](erpnext_enhancements/api/), document-event handlers, scheduler jobs, and `script_migrations/`. Everything server-side that hooks into ERPNext is registered in [`hooks.py`](erpnext_enhancements/hooks.py).
2. **Client (JavaScript).** Form scripts, list-view scripts, desk-wide patches, the Vue comments app, and the kiosk front-end — all under [`public/`](erpnext_enhancements/public/). Loaded via `hooks.py` (`app_include_js`, `doctype_js`, …) except the kiosk PWA, which is loaded by `www/kiosk.html`.
3. **Data model.** DocTypes (`*.json`) plus Custom Fields, Property Setters, Workflows, and Notifications shipped as [`fixtures/`](erpnext_enhancements/fixtures/) and created by [`setup/`](erpnext_enhancements/setup/) / [`patches/`](erpnext_enhancements/patches/).

## Module map

The app ships eight Frappe modules (see [`modules.txt`](erpnext_enhancements/modules.txt)). Each has its own README:

| Module (folder) | What it covers | README |
|---|---|---|
| **Enhancements Core** (`enhancements_core/`) | Catch-all: app Single settings, Time Kiosk data doctypes (Job Interval, Time Kiosk Log), Asset Booking, GA4 dashboard page, Location Timeline map page | [README](erpnext_enhancements/enhancements_core/README.md) |
| **Project Enhancements** (`project_enhancements/`) | Project Dashboard page, Master Project, procurement status, project merge, dashboard overrides | [README](erpnext_enhancements/project_enhancements/README.md) |
| **Sapphire Maintenance** (`sapphire_maintenance/`) | Template→Record→Result maintenance subsystem, portal, print format, on-submit automation | [README](erpnext_enhancements/sapphire_maintenance/README.md) |
| **CRM Enhancements** (`crm_enhancements/`) | Opportunity customizations + Google Drive project-folder provisioning | [README](erpnext_enhancements/crm_enhancements/README.md) |
| **Global Enhancements** (`global_enhancements/`) | Triton AI assistant settings + Directory Link Exclusion doctype | [README](erpnext_enhancements/global_enhancements/README.md) |
| **Task Enhancements** (`task_enhancements/`) | Overrides the core Task class; Hierarchical Task View page | [README](erpnext_enhancements/task_enhancements/README.md) |
| **Travel Management** (`travel_management/`) | Travel Trip workflow + child tables → draft Expense Claim | [README](erpnext_enhancements/travel_management/README.md) |
| **QuickBooks Time Integration** (`quickbooks_time_integration/`) | QuickBooks Online accounting sync (OAuth2, CDC, webhooks) + QB Time webhook | [README](erpnext_enhancements/quickbooks_time_integration/README.md) |

Shared / cross-cutting code (not a Frappe module):

| Folder | What it covers | README |
|---|---|---|
| `api/` | Whitelisted HTTP endpoints | [README](erpnext_enhancements/api/README.md) |
| `script_migrations/` | Ported Client/Server Scripts (wired via `doc_events`) | [README](erpnext_enhancements/script_migrations/README.md) |
| `patches/` | One-time migration scripts | [README](erpnext_enhancements/patches/README.md) |
| `public/` | Browser assets (JS/CSS) | [README](erpnext_enhancements/public/README.md) |
| `www/` | Time Kiosk PWA shell | [README](erpnext_enhancements/www/README.md) |
| `tests/` | Test suite | [README](erpnext_enhancements/tests/README.md) |
| `Custom HTML Block/` | Exported dashboard-widget source | [README](Custom%20HTML%20Block/README.md) |

> **History:** v0.2.0 merged the previously separate `crm_enhancements`, `global_enhancements`, `project_enhancements`, `task_enhancements`, and `qb_time_integration` apps into this one app. Their public assets are namespaced under `public/{js,css}/<module>/` to avoid collisions. Uninstall the old standalone apps from existing benches after deploying.

## How customizations are wired (`hooks.py`)

[`hooks.py`](erpnext_enhancements/hooks.py) is the single source of truth for how this app attaches to ERPNext. The major registrations:

| Hook | Purpose | Notable entries |
|---|---|---|
| `app_include_js` / `app_include_css` | Desk-wide assets loaded on every page | Kanban patches, the Vue comments app (`vue.global.js` + `comments.js` + `comments_auto.js`), Triton widget, telephony client, perf fixes |
| `doctype_js` / `doctype_list_js` / `doctype_css` / `doctype_calendar_js` | Per-doctype form/list/calendar scripts | Project, Opportunity, Task, Customer, Supplier, Contact, etc. |
| `doc_events` | Document lifecycle handlers | Contact-sync on most party doctypes, `script_migrations.*` ports, dashboard realtime updates, maintenance scheduling, **`"*": {after_save: global_triton_sync}`** (fires on every save site-wide) |
| `scheduler_events` | Background jobs | **daily**: project reminders, predictive maintenance, customer inactivity, elapsed-time refresh, draft cleanup, location-log purge · **hourly**: QuickBooks token refresh, CDC poll, retry failed syncs |
| `override_doctype_class` | Replace a core controller | `Task` → `task_enhancements.doctype.task.task.Task` |
| `override_whitelisted_methods` | Replace a core endpoint | `opportunity.make_project` → `opportunity_enhancements.make_project` |
| `override_doctype_dashboards` | Customize the "connections" dashboard | `Project`, `Employee` |
| `extend_bootinfo` | Per-session data shipped to the desk client | `boot.boot_session` — the live-collab doctype allowlist (`frappe.boot.collab_doctypes`) from ERPNext Enhancements Settings |
| `after_migrate` | Idempotent setup after each migrate | `setup.custom_fields`, `setup.supplier_groups`, `setup.process_documents` (seeds/updates the Mermaid.js Process Document charts — repo is the source of truth) |
| `fixtures` | Records installed on migrate | **All manual customizations** — every manually created Custom Field (425) and Property Setter (349); see [`fixtures/README.md`](erpnext_enhancements/fixtures/README.md) — plus Travel Trip Workflow + states/actions, maintenance Notifications + Print Format |
| `portal_menu_items` | Customer portal links | `/maintenance-records` |

When you add a feature, you almost always register it here. When you are tracing "what runs when X is saved", start here.

## External integrations

This app talks to several third-party services. Credentials live in dedicated **Single** settings doctypes (encrypted Password fields where secret):

| Service | Used for | Configured in |
|---|---|---|
| **Google Drive** | Auto-create per-project folder trees on Opportunity→Project | `Project Folder Google Drive Settings` (service-account JSON) |
| **Google Analytics 4 + Search Console** | Marketing dashboard | `GA4 Settings` (service-account JSON; see [Enhancements Core README](erpnext_enhancements/enhancements_core/README.md#google-analytics-4--search-console-dashboard)) |
| **Vertex AI (Gemini)** | AI email/SMS reply drafting | `Triton Settings` (`maps_api_key` password field) |
| **Twilio + "Triton" gateway** | Click-to-call softphone, SMS, voicemail, call transcripts | `Triton Settings` |
| **QuickBooks Online** | Two-way accounting sync (OAuth2) | `QuickBooks Online Settings` |
| **Google Calendar** | Push Tasks as calendar events | hard-coded shared calendar (see `script_migrations/task.py`) |
| **"Triton" AI assistant** | In-app chat widget + global record sync | `Triton Assistant Settings` (widget) + `Triton Settings` (connection) |

> **Note:** "Triton" appears in two roles — (1) the telephony/AI **gateway** service (service user `triton@sapphirefountains.com`, formerly "Poseidon" — see the rename patches), and (2) the in-app **AI assistant** widget. They share the `Triton Settings` connection but the widget has its own `Triton Assistant Settings`.

## Key subsystems

A few subsystems are large enough to call out; each is documented fully in its module README.

- **Project Dashboard** — a desk Page (`project-dashboard`) with lazily-loaded tab components, realtime updates via `publish_realtime`, an interactive portfolio Gantt, and optimistic inline editing. → [Project Enhancements README](erpnext_enhancements/project_enhancements/README.md)
- **Time Kiosk PWA** — `/kiosk`, an installable offline-capable PWA. The front-end (`public/js/kiosk/`) samples GPS with `watchPosition` + distance filter + heartbeat; the service worker (`www/kiosk-sw.js`) queues points in IndexedDB and batch-uploads with Background Sync. → [www README](erpnext_enhancements/www/README.md) and [public README](erpnext_enhancements/public/README.md#kiosk-pwa-front-end)
- **Live collaborative editing** — Google-Docs-style multi-user form editing, configured per-doctype on **ERPNext Enhancements Settings** (master switch + allowlist child table; toggle doctypes with no deploy — seeded at launch with Task, Project, Opportunity, Customer, Contact, Address, Item, Supplier, Purchase Order drafts, ToDo). A client engine (`public/js/collab/live_form_sync.js`) streams debounced field changes through a permission-checked relay (`api/collab.py`) into Frappe's per-document realtime rooms; collaborators' saves merge silently (adopting the new `modified` timestamp, so `TimestampMismatchError` can't occur between collaborators), and theme-aware per-field presence highlights show who is editing which field. → [API README](erpnext_enhancements/api/README.md) and [public README](erpnext_enhancements/public/README.md#live-collaborative-editing-jscollab)
- **Custom Comments App** — a Vue 3 notes UI mounted on ~23 doctypes. → [public README](erpnext_enhancements/public/README.md#the-comments-app)
- **Contact / primary-contact / directory model** — denormalized primary contacts kept in sync both directions, plus an aggregated contacts/addresses directory with per-document exclusions. → [script_migrations README](erpnext_enhancements/script_migrations/README.md) and `sync_contact.py`
- **QuickBooks Online sync** — OAuth2 → REST client → entity mapping → idempotent upsert → audit log, with CDC polling, webhooks, and retries. → [QuickBooks README](erpnext_enhancements/quickbooks_time_integration/README.md)

## Installation

Install with the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app erpnext_enhancements <repo-url> --branch main
bench install-app erpnext_enhancements
```

Installing the app pulls in the Python dependencies declared in [`pyproject.toml`](pyproject.toml) (Google API + Gemini client libraries). Frappe itself is managed by bench.

After install, configure the integrations you need via their Single settings doctypes (see [External integrations](#external-integrations)). Detailed setup for the GA4 dashboard lives in the [Enhancements Core README](erpnext_enhancements/enhancements_core/README.md#google-analytics-4--search-console-dashboard).

> **HTTPS is required** for the Time Kiosk (geolocation, service workers, and PWA install only work over HTTPS; `localhost` is exempt).

## Development workflow

### Linting & formatting

This repo uses **pre-commit**. Install it once:

```bash
cd apps/erpnext_enhancements
pre-commit install
```

Configured hooks ([`.pre-commit-config.yaml`](.pre-commit-config.yaml)):

- **ruff** — Python lint + format. Note: format uses **tabs** for indentation and double quotes (`line-length = 110`); config in [`pyproject.toml`](pyproject.toml).
- **eslint** — JavaScript lint ([`.eslintrc`](.eslintrc)).
- **prettier** — formatting for JSON/CSS/Markdown/YAML.
- **pyupgrade** — modernizes Python syntax.

### CI & releases

- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the standalone `unit-tests` job. (The heavier Frappe integration-test job was removed in v0.2.9 — see the CHANGELOG — because it gated PRs on upstream/environment churn; the Frappe-dependent tests under `tests/` can still be run against a real bench locally.)
- **Release** ([`.github/workflows/release.yml`](.github/workflows/release.yml)) tags and publishes a GitHub Release whenever a new `__version__` lands on `main`, using the matching `CHANGELOG.md` section as release notes. Because Frappe Cloud deploys from `main`, the Releases page is a 1:1 log of what is deployed.

### Running tests

Most tests under [`tests/`](erpnext_enhancements/tests/) require a real bench (`FrappeTestCase`):

```bash
bench --site <site> run-tests --app erpnext_enhancements
```

Two suites are deliberately bench-free and run with plain `pytest`/`unittest`: `tests/test_quickbooks_online.py` (stubs `frappe`/`requests` in `sys.modules`) and the root `test_sync_time_kiosk.py` (mocks `httpx`). See the [tests README](erpnext_enhancements/tests/README.md).

## Conventions

- **Version bumping.** Bump the app version on every PR/deploy following semver. Three sources must stay in sync: [`erpnext_enhancements/__init__.py`](erpnext_enhancements/__init__.py) (`__version__`), [`package.json`](package.json) (`version`), and a new dated section in [`CHANGELOG.md`](CHANGELOG.md). The Release workflow verifies `__init__.py` and `package.json` agree.
- **Changelog.** Follows [Keep a Changelog](https://keepachangelog.com/); entries are detailed and explain *why* (the CHANGELOG is the best history of upstream-bug context for the Kanban/perf patches).
- **Migrated scripts.** Legacy database-stored Client/Server Scripts were ported into `script_migrations/` (server) and `public/js/*_migrated_scripts.js` (client) so they are version-controlled. The original DB scripts are being disabled as deploys land.
- **Indentation is mixed.** Frappe convention is **tabs** for Python, and most files follow it, but several files use 4-space indentation. **Match the existing file exactly** when editing (ruff/`tabnanny` will catch inconsistencies).
- **Public asset namespacing.** Per-module browser assets live under `public/{js,css}/<module>/` to avoid collisions after the app merge.
- **Defensive hooks.** Because `doc_events` fire during ERPNext's own test bootstrap (before this app's custom fields exist), custom-field reads use `getattr(obj, "field", None) or ""` and column-filtered queries guard with `frappe.db.has_column(...)`. Preserve these guards.

## Documentation index

- **Server modules:** [Enhancements Core](erpnext_enhancements/enhancements_core/README.md) · [Project Enhancements](erpnext_enhancements/project_enhancements/README.md) · [Sapphire Maintenance](erpnext_enhancements/sapphire_maintenance/README.md) · [CRM Enhancements](erpnext_enhancements/crm_enhancements/README.md) · [Global Enhancements](erpnext_enhancements/global_enhancements/README.md) · [Task Enhancements](erpnext_enhancements/task_enhancements/README.md) · [Travel Management](erpnext_enhancements/travel_management/README.md) · [QuickBooks Time Integration](erpnext_enhancements/quickbooks_time_integration/README.md)
- **Cross-cutting:** [API endpoints](erpnext_enhancements/api/README.md) · [Script migrations](erpnext_enhancements/script_migrations/README.md) · [Patches](erpnext_enhancements/patches/README.md) · [Frontend assets](erpnext_enhancements/public/README.md) · [Time Kiosk PWA](erpnext_enhancements/www/README.md) · [Tests](erpnext_enhancements/tests/README.md) · [Custom HTML Block](Custom%20HTML%20Block/README.md)
- **Reference:** [`CHANGELOG.md`](CHANGELOG.md) · [`hooks.py`](erpnext_enhancements/hooks.py) (annotated)

## Contributing

1. Install pre-commit (above) and let it format your changes.
2. Bump the version in all three places and add a `CHANGELOG.md` entry.
3. Keep `hooks.py` and the relevant module README in sync when you add a customization.
4. Don't change executable behavior in a "docs only" change; don't remove the defensive fresh-DB guards.

## License

MIT — see [`license.txt`](license.txt).

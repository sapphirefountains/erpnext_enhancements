# Custom HTML Block — block sources (source of truth)

This folder is the **source of truth** for the Frappe **Custom HTML Blocks** — dashboard widgets embedded on workspaces. There are 41 blocks here (one `<name>.{html,js,css}` triple each, registered in `setup/custom_html_blocks.py` `BLOCKS`): the original four — *Projects Dashboard*, *Task Dashboard*, *Morning Briefing* and *Desk Shortcuts* — plus *KPI Cockpit*, the per-viewer *My Training Dashboard* and *My Travel*, and the 34 department dashboard widgets described below.

> ✅ **Repo is the source of truth (v1.69.0).** On every `bench migrate`, `erpnext_enhancements.setup.custom_html_blocks.sync_custom_html_blocks` (an `after_migrate` hook) **upserts** every block in `BLOCKS` from these files: missing blocks are created and any block whose `html`/`script`/`style` has drifted from the source is **overwritten**, then the blocks are placed on the **Home** workspace (idempotent append), and the Projects Dashboard leads ERPNext's **Projects** workspace (`PROJECTS_LAYOUT`, restored whenever an ERPNext upgrade puts that page back to stock; see [`setup/README.md`](../setup/README.md#erpnexts-projects-workspace-replaced-only-while-it-is-erpnexts)). So edit the files here and `bench migrate` to deploy — **UI-side edits to these blocks do not survive a migrate.** (The older insert-only seed patches — `seed_task_dashboard_block`, `seed_morning_briefing_block`, `seed_desk_shortcuts_block` — are now superseded by this seeder and left only for history; they no-op once a block exists.)

## When a block script runs

The workspace runs a block's whole script, with a fresh shadow root as `root_element`, **only
when it renders the page**: the first visit, and coming back from a *different* workspace. It
does not run it when you come back to the same workspace from a form, a list or a desk page
(the Back button, a breadcrumb, the sidebar). The desk keeps one Workspaces page, and v16's
`Workspace.show()` returns early for the workspace already on screen
(`frappe/public/js/frappe/views/workspace/workspace.js:91`,
`if (this._page?.name === page.name) return;`). Until v1.556.3, thirty of these files said the
opposite in their header comment (that the workspace ran the script again, with a fresh root,
each time you arrived), and every dashboard kept the numbers it first loaded until the browser
tab was reloaded.

So a block that fetches data registers with the desk-wide return helper,
[`public/js/global_enhancements/workspace_block_return.js`](../public/js/global_enhancements/workspace_block_return.js),
which ships in `erpnext_enhancements.bundle.js`:

```js
const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
```

When the route comes back to the workspace the block was drawn on, and the block is still on
the page, the helper calls `load(container)` again. What it does and does not do:

- **One router handler for the whole desk**, bound on the first registration. `frappe.router.off()`
  wraps the handler in a new function before unbinding, so it can never remove one; a block that
  bound its own would add one per page render.
- **No double load on a fresh render.** v16's router renders and *then* fires `change`
  (`router.js` `route()`), and a render draws its blocks asynchronously after that, so a block
  drawn by a navigation registers after that navigation's `change` and has already loaded
  itself. As a guard against that order changing, a block registered under the route that is
  still current is not reloaded.
- **Only the workspace being shown.** Going from Home to Travel fires `change` while Home's
  blocks are still in the document; they are not reloaded. A block whose host has left the
  document is dropped.
- **Never throws.** The router's handlers run in turn under jQuery, and an exception in one
  would stop the others. One block's failing load never stops the rest.
- **Guarded in every block.** A device holding a bundle cached from before the helper has no
  helper; the block still draws, and keeps its first answer.

`load` is given the root. A block whose load reads its first argument as something else
registers a wrapper: the KPI Cockpit and the Morning Briefing register `load(false)`, so a
return never recomputes or regenerates, and only when no answer is already on its way. A load
that the refresh button and a return can both start takes a **ticket** on the root, and only
the newest answer draws, so an older answer arriving late never paints over a newer one.

Two blocks deliberately do not register. **Desk Shortcuts** makes no server call (it paints
`frappe.boot`, which does not change until the desk reloads). **Finance Astrology** is one
server-cached text per sign per day, so asking again would redraw the same words.
`tests/test_workspace_block_return.py` holds that list, and fails the build on a block that
fetches data and does neither, and on any block comment that claims the old lifecycle.
`scripts/test_workspace_block_return.mjs` runs the helper itself, then runs every registering
block against a stand-in desk and checks that leaving asks the server nothing and coming back
really asks it again.

**A block that polls asks the helper whether it is on screen.** Leaving the workspace hides a
block (`display:none` on an ancestor) or, once the page renders again, detaches it, and its
timers keep firing. `workspace_blocks.onScreen(root)` asks the shadow root's host: false once it
has left the document or has no client rects, and **true whenever it cannot tell** (no host, no
layout APIs, an exception), so a block it cannot judge polls as before. The Task Dashboard and
Who's Working skip a timed or realtime fetch while it says false. Each asks through a guard that
answers true when the helper or the function is missing (a device holding an older bundle);
until v1.641.0 each carried its own copy of the check. `tests/test_page_polling_lifecycle.py`
pins the one copy and the guards, and the node harness runs both blocks' poll timers shown,
hidden, and hidden on an older bundle.

## Panels, chips and escaping come from sf_kit

Since v1.649.0 the 34 department dashboard widgets draw their shell from
[sf_kit](../../docs/app-design-system.md#sf_kit-the-shared-page-pieces) instead of each
carrying a copy: no block has its own escape, `muted()` helper, ticket counter or `-pill`
markup any more. Two more helpers on `workspace_blocks`, in the same
`workspace_block_return.js`, hand it over:

```js
const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
if (blocks && blocks.kit && blocks.ticket) {
	blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
	return;
}
// A bundle cached from before the helpers: plain text, and every answer draws.
const words = {
	loading: __("Loading…"),
	empty: __("Nothing to show"),
	error: __("Could not load"),
	"no-access": __("No access"),
};
const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || words[kind] || "");
const escape = frappe.utils.escape_html;
startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
```

- **`blocks.kit(root)`** returns a Promise of the kit. It calls
  `frappe.require(["sf_kit.bundle.js"])` once per page load (memoized; the bundle name, so it
  resolves to the content-hashed file), adopts `sf_kit.CSS` into that root's
  `adoptedStyleSheets` once per root (a page stylesheet does not cross the shadow boundary; the
  `--sf-*` tokens do, so the panels follow the theme), and resolves `window.sf_kit`. It never
  rejects and never leaves a block undrawn. v16's `frappe.require` calls back even when the
  script failed to load (`assets.js`: `script.onerror = resolve`), and marks the path executed
  either way, so a second require never fetches it again: when the kit is not there after the
  callback it never will be, and `kit()` resolves a **stand-in** instead, with `esc`, a
  `state.render` that writes the title and body as plain text and adds a plain button for
  `on_action` (labelled `action_label`, or "Try again"), and a `chip` that is its escaped word.
  The button matters: Weather and Astrology have no refresh button, so without it a failed
  call could never be retried while the kit is missing.
  A ten-second wait resolves it too, in case a callback never comes.
- **`blocks.ticket(root)`** is the ticket the blocks each wrote by hand: every call takes the
  next number on the root, and the function it returns says whether that number is still the
  newest. `const current = ticket(container);` at the top of a load, then
  `if (current()) render(...)` when the answer lands.
- **The fallback rule.** The global bundle is cached on an open Desk, so a block can meet a
  bundle from before these helpers. Every converted block keeps the guard above: without
  `blocks.kit` it draws through a small stand-in of its own: plain text, the panel's title or
  else a short translated word for its kind ("Loading…", "Nothing to show", "Could not load",
  "No access"), and no ticket. It is ugly on purpose and only has to draw, not throw.

**Escape what you join.** A row's fields joined into one line (`[d.customer, d.owner].join(" · ")`)
go into `innerHTML` like anything else: `.map(sf.esc)` before the join, or `sf.esc(...)` on each
field that is text when another item is markup already. `tests/test_block_kit.py` fails on a
joined list carrying a bare row or `message` field.

What a converted block draws:

| Case | Draw |
|---|---|
| first load | `sf.state.render(body, "loading")` (the Finance widgets that blanked on every load still do) |
| switched off, or nothing to show | `sf.state.render(body, "empty", { title })`, the block's own sentence |
| the call failed | `sf.state.render(body, "error", { title, on_action: load })`, a "Try again" that loads again |
| a 403 | `sf.state.render(body, "no-access")` |
| a status label | `sf.chip(label, tone)`, the tones from `sf_kit/chip.js` (`danger`, `warn`, `good`, or neutral) |

The calls still go through `frappe.call`, so its own dialogs and the request counting in the
node harness are unchanged; the 403 is read from the jqXHR's `status`. A switched-off notice
is an *empty* panel, not an error: the widget works, nobody has turned it on.
`tests/test_block_kit.py` fails the build when a converted block grows any of its old copies
back or loses the guard, and makes every seeded block either sit on the kit or say why not.
`scripts/test_workspace_block_return.mjs` runs every converted block on the real
`sf_kit.bundle.js`: a 500 draws the error panel and its Try again asks again, a 403 draws no
access, an empty answer the empty panel, and on an older bundle the block still draws.

**The other six (v1.658.0).** The KPI Cockpit, Morning Briefing, Task Dashboard, Projects
Dashboard, My Training Dashboard and My Travel are on the kit too, so every seeded block is
except Desk Shortcuts, which asks the server nothing and has no panel to convert. Each kept
what made it different:

- **KPI Cockpit and Morning Briefing** still register a wrapper, `load(false)`, so a return
  reads the snapshot or the cached briefing and never recomputes or regenerates, and still skip
  a return while Refresh is out. Their error panels' Try again is a plain read too.
- **Task Dashboard** keeps its debounced realtime refresh, its five-minute fallback, its timers
  on `window` (re-pointed, never stacked), its `onScreen` skip and its clock. Loading, error and
  no access go in the Top 10 panel, and only until it has drawn once: a failed refresh never
  wipes rows already showing. Each of the four panels has its own empty sentence. The
  overdue badge is a `danger` chip.
- **Projects Dashboard** changed only its panels, escaping and the Completed tab's status
  chip. Its tables, column selector and resizer, Gantt and saved per-user view are untouched.
  Its `api_call` now settles on `frappe.call`'s promise rather than the `callback` / `error`
  options, because v16 calls `error` with no arguments on a 403, so a 403 could not be told
  apart from any other failure.
- **My Training and My Travel** still ask the endpoint for the session user's own data and pass
  no name. An overdue course is a `danger` chip, a trip's status a chip in its tone
  (`Planning` warn, `In Progress` good, the rest neutral). My Travel's "No trips coming up"
  is still a notice rather than an empty panel, because the body is never empty: the link to
  everyone's trips and any receipts reminder are drawn under it.

My Training and My Travel build their rows in helpers outside `startApp`, so they hold the kit
in a variable that `startApp` sets before anything draws. The KPI Cockpit and the Task
Dashboard read the kit's escape under the name `esc` (`const { esc } = sf;`, and a variable
the Task Dashboard's `startApp` sets), so the status words that S3.6 (accessibility) adds to
their cards keep working unchanged.

## Status colours come from the `--sf-*` tokens

Good, watch and bad are read from the app's design tokens
([`public/css/sf_tokens.bundle.css`](../public/css/sf_tokens.bundle.css),
[`docs/app-design-system.md`](../../docs/app-design-system.md)), never written as literals. The
tokens are custom properties on `:root`, so they inherit into every block's shadow root the way
Frappe's own variables do, and one file sets every block's status colours in both themes.

| Use | Write |
|---|---|
| status text: good, bad | `var(--sf-good)`, `var(--sf-danger)` |
| status text: watch | `color-mix(in srgb, var(--sf-warn) 50%, var(--sf-text-strong))`. `--sf-warn` is a yellow *fill*, 1.7:1 on white; mixed with the theme's strongest text it is 5.3:1 on a white card and 13:1 on a dark one |
| a fill: a bar, a badge, a stripe | `var(--sf-good)` / `var(--sf-warn)` / `var(--sf-danger)`, with `var(--sf-on-good)` / `--sf-on-warn` / `--sf-on-danger` for a label on it |
| a light tint behind a pill or a notice | `color-mix(in srgb, var(--sf-danger) 12%, transparent)`, the edge at 35%. Warn's edge is the warn-text mix at 40% instead: the yellow at 35% is 1.22:1 on white and the outline vanishes; the mix is 1.76:1 on white and 3:1 on a dark card |

My Travel is the exception that proves it: it uses Frappe's theme-aware indicator pairs
(`--bg-green` / `--text-on-green` ...), and only their fallbacks are tokens.
`tests/test_block_status_tokens.py` fails the build on any of the old literal status hexes (or
their `rgba()` tints) in a block's `.css` or `.js`, and on a `var(--sf-...)` the token file does
not define. Categorical colours (project types, the priority scale, the panel accents, the Desk
Shortcuts palette) are not statuses and stay literal. Two of them happen to be greens the test
bans as a status colour, the Task Dashboard's Projects panel (`#16a34a`) and the Projects
Dashboard's "Avg % Complete" card (`#28a745`), so the test allows exactly those sites.

## Files — Projects Dashboard

| File | Role |
|---|---|
| `projects_dashboard.html` | The block markup: a tabbed shell (Priority Overview · Active Internal Projects · Completed Projects · Portfolio Gantt) + search + Gantt filter controls + an empty `#dashboard-content`. |
| `projects_dashboard.js` | Runs in the block sandbox (`root_element`). Loads the shared `ColumnSelector` + `ColumnResizer` assets, fetches via the [Project Dashboard page's](../project_enhancements/README.md#project-dashboard) whitelisted methods, and renders editable tables + the portfolio Gantt via the embeddable Gantt widget (`erpnext_enhancements.gantt.mount`, composite mode: Master Project groups -> Projects -> Task trees through the permission-checked `api/gantt.py::get_gantt_data`; read-only for now — drag-editing returns with the widget's edit opt-in milestone). Each project row expands via its caret to lazily load that project's tasks; bars are coloured by `project_type` (tasks a lighter shade of their project) with an on-screen legend; filters cover a find-a-project search plus status, project, type, customer, a date window and at-risk-only; grid columns are individually toggleable; the whole view (filters, columns, zoom, expanded projects) persists per user. The three list tabs support show/hide columns **and drag-to-resize column widths** (drag a header's right edge; **Reset widths** in the toolbar restores defaults) — widths persist per user in localStorage under `chb_*_widths`. Edits persist back through the same methods. The **Export** dropdown drives the widget's export methods directly (this block owns its toolbar markup, so switching on the widget's built-in toolbar would render a second control row) and includes **Date range…**, which opens the widget's picker — seeded from the board's own date-window filter when one is set. The two are deliberately different things: the toolbar filter decides which *projects* load, the export range decides what the exported page covers, including the child Tasks that filter never touches. |
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
| `task_dashboard.css` | Shadow-root styles. Structural colors from Frappe CSS variables (they pierce the shadow boundary, so both themes work); overdue and urgent from the `--sf-*` status tokens; the rest of the priority scale and the panel accents (the Projects panel's green included) literal. |

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

## Files — Department Dashboard widgets (v1.252.0)

Each of the department dashboards under `kpi_dashboards/workspace/` (nine when this shipped, ten since v1.530.0) used to carry a
single block: the KPI Cockpit. Finance was the exception, with six operational widgets.
This adds four widgets to each of the other seven — Sales, Operations, Design, Production,
Marketing, HR and Executive — so a dashboard shows what to *do*, not only how things went.

**v1.530.0 split Operations.** Maintenance moved to a tenth dashboard, **Service**, listed
under Production in the sidebar, and Operations became the inventory dashboard. The three
maintenance widgets went with it and were renamed `Service …` (block records, source files
and feed module); `patches/split_service_kpi_dashboard` deletes the three old
`Operations …` block records, because this seeder never deletes. Their settings toggles
keep the `operations_*` fieldnames: renaming a field on a Single drops the stored value.

| Department | Blocks | Feed |
|---|---|---|
| Sales | Speed to Lead · Stalled Deals · Hand-Off Backlog · Renewal Radar | [`api/sales_dashboard.py`](../api/sales_dashboard.py) |
| Operations | Fleet & Device Health | [`api/operations_dashboard.py`](../api/operations_dashboard.py) |
| Design | Design WIP · Awaiting Sign-Off · Hand-Off Readiness · Hydraulic Headroom | [`api/design_dashboard.py`](../api/design_dashboard.py) |
| Production | Build WIP & Aging · Milestone Slippage · Material Readiness · Hours vs Budget | [`api/production_dashboard.py`](../api/production_dashboard.py) |
| Service | Today's Visits · Chemistry Alerts · Labor Capture Gaps | [`api/service_dashboard.py`](../api/service_dashboard.py) |
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
deliberately uniform across all 34 department widgets; structural colours come from Frappe CSS variables
so both themes work, and where the colour *is* the meaning (good / watch / bad) it comes from the
`--sf-*` status tokens (see [Status colours](#status-colours-come-from-the---sf--tokens)).

## Files — My Travel (the Travel hub, v1.554.0)

| File | Role |
|---|---|
| `travel_home.html` | Shell: a `My Travel` header with a refresh button, and an empty body the script fills. |
| `travel_home.js` | Block-sandbox script. It makes one call to `erpnext_enhancements.travel_management.home.get_travel_home` and draws the answer: the current or next trip with its buttons and a closed *Who to call*, other trips, receipts due, and a coordinator's *Needs attention*. It computes no dates or rules. Every value is escaped, and web addresses open in a new tab with `noopener`. Desk routes use `frappe.set_route`; Plan a Trip is reached through `frappe.route_options`. |
| `travel_home.css` | Shadow-root styles from Frappe CSS variables, so both themes work. The primary button uses the theme-aware colors of frappe's own primary button, because `--primary` stays near-black in v16 dark mode. It works from 360px up, with 40px tap targets. |

Registered in `setup/custom_html_blocks.py` `BLOCKS` only. The Travel workspace JSON places it, as the
[Travel README](../travel_management/README.md#the-travel-hub) explains.

## Relationship to the retired Project Dashboard desk page

The Projects Dashboard block is now the **only** projects dashboard surface. The standalone **Project Dashboard** desk page (`/app/project-dashboard`) was retired in v1.159.8 and its `Page` record deleted by `patches/retire_project_dashboard_desk_page.py`. What survives of it is the server side: the block still calls the same whitelisted endpoints (`erpnext_enhancements.project_enhancements.page.project_dashboard.*`) and the shared front-end helpers (`ColumnSelector`, the embeddable Gantt widget). See the [Project Enhancements README](../project_enhancements/README.md) for those endpoints and the permission gate.

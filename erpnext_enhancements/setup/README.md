# `setup/` — migrate-time provisioning

Idempotent provisioning that runs on **every** `bench migrate`, wired through the
`before_migrate` and `after_migrate` hooks in `hooks.py`. This is the third mechanism for
version-controlling site state, alongside `fixtures/` (declarative JSON) and `patches/`
(one-time scripts).

## Which mechanism?

| Need | Use |
|---|---|
| A Custom Field or Property Setter | `fixtures/` |
| A one-time data migration or a seed that must not re-run | `patches/` |
| Something that must be **re-asserted on every migrate** | here |

The distinguishing property is re-assertion. A patch runs once and is recorded; these run
every time, so they both *create* what's missing and *correct* drift.

## File map

| File | Hook | Purpose |
|---|---|---|
| `module_map.py` | `before_migrate` | Rebuilds the app → modules map from every app's `modules.txt` — must run before model sync (see below) |
| `document_locks.py` | `before_migrate` | Clears stale Role Profile document locks — must run before fixture sync (see below) |
| `process_documents.py` | `after_migrate` | Upserts the business's Mermaid.js process charts |
| `custom_html_blocks.py` | `after_migrate` | Upserts the Projects-module dashboard widgets from `custom_html_blocks/`, places them on Home and the department dashboards, and keeps ERPNext's Projects workspace on our layout (see below) |
| `custom_fields.py` | `after_migrate` | Provisions the "Contacts & Addresses" tab — Primary Contact link, directory widgets, primary address + location map — on the party doctypes, plus a Comments tab for Master Project (every other doctype's is fixture-owned) |
| `supplier_groups.py` | `after_migrate` | Relabels `supplier_group` to "Primary Supplier Group" and adds the additional-groups Table MultiSelect plus the denormalized text fields `supplier_query.sync_supplier_groups` populates |
| `workspace_tweaks.py` | `after_migrate` | Re-asserts overrides on core (erpnext-owned) workspaces and sidebars. Hides the "Project" link in the Projects sidebar: drops the row from the `Workspace Sidebar` before frappe 16.50, and from 16.50 sets `hidden` on ERPNext's own `Sidebar Item` row (a `Custom Sidebar` layer would reorder the sidebar; see the function's docstring). Since v1.583.1 it also **adds** the Project Planner (after Task) and the Crew Utilization report (after the module's other reports) to the Projects sidebar (`add_core_sidebar_items`): ERPNext's own rows are inserted at the right `idx` with `db_insert`, a link already there is left alone even if hidden, and the rows come back after an ERPNext re-import. Since v1.735.0 (the dock usage audit of v1.734.0) it puts the Opportunity Kanban, a URL row, at the top of the CRM sidebar, so CRM opens on it; `arrange_core_sidebar_items` moves Purchase Order and Material Request up under Home in Buying and renames CRM's dashboard row from "Home" to "Dashboard", writing `idx` and `label` only where they differ. `drop_unreadable_sidebar_links`, called from `boot.py`, takes the Kanban row away from anyone who cannot read Opportunity, because frappe never permission-checks a URL row |
| `dock_profiles.py` | `after_migrate`, User `on_update`, `extend_bootinfo` (via `boot.py`) | The Dock rail per job (v1.734.0). Writes the site's layer of ERPNext's dock (`SITE_DEFAULT`: Buying, Stock, Projects, Opportunities, CRM) and each enabled System User's own layer from their dock profile: `User.custom_dock_profile` when set, else the first `BY_ROLE_PROFILE` match. Only a layer that is missing or still the app's (last written by Administrator) is written, so one a person arranged in Manage Dock is kept; choosing a Dock Profile on the User form is the one override. Since v1.751.0 the same profile also cuts the field technicians' Desk home grid down to the eight things on their rail (`trim_home_grid`, every boot). See "The Dock per job" below |
| `stock_web_forms.py` | `after_migrate`, `after_install`; `page_renderer`, `website_clear_cache` | Keeps ERPNext's stock web forms `tasks`, `issues` and `addresses` unpublished (v1.755.0); this app's portal pages replace them. While a form stays unpublished, its route and every path under it answer frappe's Not Found page (`UnpublishedStockWebFormPage`, v1.755.2). That covers the paths the form's router owned (the route, `/list`, `/new`, `/<name>`, `/<name>/edit`) and deeper ones too. Bare `/tasks` never reaches it: ERPNext's `website_route_rules` rewrites it to the Task list first, which already answers Not Found. Without it, paths under the Addresses form's route `address` answered an error page. It reads the published state from frappe's own `get_published_web_forms`, so it steps aside for a published form, and it reads the routes from the rows, cached until frappe clears its website cache (`clear_route_cache`). `/addresses` keeps ERPNext's redirect to `address/list` and ends at the same 404. ERPNext re-imports a standard web form, published, whenever its JSON's `modified` moves, and that happens during model sync, so a one-time patch would not hold; `after_install` covers a fresh site, where no `after_migrate` runs. Also disables the Issues (`/issues`) and Addresses (`/addresses`) Portal Settings menu rows, which would otherwise be dead links (`sync_menu` never re-enables a row). Writes only what is still on, forms with `update_modified=False` so a later ERPNext change still imports, prints each flip to the migrate output, then clears the website cache. Never publishes or enables; to bring one back, take it out of `STOCK_WEB_FORMS` and `STOCK_MENU_ROUTES` |
| `desktop_icon_map.py` | — | Data only: the Desk home-grid tile for each module (slug, glyph, colour). Importless on purpose — the generator and the bench-free test read it without a frappe stub |
| `desktop_icons.py` | `after_migrate`, `after_install` | Stamps that artwork onto `Desktop Icon.logo_url`, creates the tile for a workspace core never gave one, appends the `ADD_TO_SAVED_LAYOUTS` tiles to every saved home-screen layout that lacks them, and shows each `DISPLAY_LABELS` tile under its display name (Sapphire Maintenance as **Maintenance**, v1.592.0: label, legacy Workspace Sidebar title and saved layouts, never a docname or URL) |

## The repo is the source of truth — and here that means overwriting

`process_documents.py` and `custom_html_blocks.py` **overwrite** a record whose content has
drifted from the canonical text in the repo. Same philosophy as `fixtures/`: UI edits do not
survive deploys.

Two boundaries keep that from being destructive:

- Documents or blocks created on the site under titles **not listed** in the repo are left
  alone.
- **Nothing is ever deleted.**

So the rule is: if you want a process chart or dashboard widget changed, change it here. A
site-side edit is temporary by design.

### ERPNext's Projects workspace: replaced only while it is ERPNext's

The Projects workspace (the Projects sidebar's **Home** link) belongs to ERPNext, and its
layout here (Projects Dashboard, My Priority Tasks, Morning Briefing, Task Dashboard) was built
on the site. A standard workspace is re-imported whenever the app's JSON carries a newer
`modified` than the database row, and the re-import replaces the row whole, with no Version.
ERPNext 16.50.0 stamped `projects.json` 2026-10-04, so the 2026-10-06 upgrade put the stock
onboarding, chart and number cards back over our layout.

`custom_html_blocks._place_projects_layout` handles it in two cases:

- **The content is exactly what ERPNext ships** (compared with the installed
  `erpnext/projects/workspace/projects/projects.json`, parsed). That is a fresh install or a
  re-import, never a layout someone chose, so it is replaced with `PROJECTS_LAYOUT`. The write
  moves `modified` past ERPNext's stamp, so later migrates leave the page alone until ERPNext
  ships a newer file. That migrate re-imports it, and this hook runs after the re-import in the
  same migrate.
- **Anything else** is a layout someone chose, and it is kept. The only change is that the
  Projects Dashboard goes back at the top if it is missing. Removing one of the other three
  blocks on the site sticks.

"Home Dashboard Tasks" (My Priority Tasks) was made on the site and has no source here. It is
placed only where it exists and never created. The step is wrapped so a failure logs an Error
Log titled `Projects workspace layout not restored` instead of stopping the migrate.

Note the contrast with the seeding patches (`seed_process_step_templates`,
`seed_contract_templates`), which are deliberately *insert-only* so that human edits survive.
Choose based on whether the content is ours to own or theirs to tune.

## Why `module_map.py` runs on `before_migrate`

**Adding a module to `modules.txt` is not sufficient on an already-installed site.**
`frappe.model.sync.sync_for()` iterates `frappe.local.app_modules`, a snapshot taken once in
`frappe.init()` out of the Redis key `app_modules`; `modules.txt` is read only when that key is
empty, and nothing in `bench migrate` rebuilds it (`SiteMigration.setUp`'s `frappe.clear_cache()`
deletes the key but does not call `setup_module_map`). A migrate that starts with a stale
snapshot walks the *previous* release's module list and silently skips a module added in this
one: no DocType imported, no table created, no `Module Def` made — and the migrate exits 0.
That is how v1.261.0 shipped ten DocTypes of the since-retired Chat module and installed none
of them (2026-08-09).

`before_migrate` is Frappe's `pre_schema_updates`, i.e. before **both** patch phases and before
`sync_all()`. That is the only window in which rebuilding the map helps; `after_migrate` is a
whole migrate too late. Deleting the cache key before calling `setup_module_map` is equally
load-bearing — it re-reads the key first and only falls back to `modules.txt` when it is empty.

Guarded in CI by `tests/test_module_installability.py`. The long-form write-up of this trap
lived in `chat/README.md` and went with that module when it was retired in v1.426.0
([ADR 0011](../../decisions/adr/0011-retire-google-chat-and-coworker-chat.md)); the account
above is now the whole of it, and the test is the part that was doing the work anyway.

## Why `document_locks.py` runs on `before_migrate`

`fixtures/role_profile.json` re-imports the Role Profiles on every migrate. Frappe core's
`RoleProfile.on_update` responds by locking the document — and a stale lock left from a
previous run makes the next fixture sync fail.

`before_migrate` puts this in Frappe's `pre_schema_updates`, i.e. **before** fixture sync in
`post_schema_updates`. The ordering is the entire point of the module; moving it to
`after_migrate` makes it useless.

Similarly, `workspace_tweaks.py` is on `after_migrate` so it runs *after* Frappe has synced
standard workspace and Workspace Sidebar records from every app — letting an override survive
a core app re-importing its version earlier in the same migrate.

## Desk tiles: why `logo_url`, and why a hook rather than shipped JSON

The Desk home grid (`/desk/desktop`) draws one `Desktop Icon` per tile, and picks its image
in a fixed order (`frappe/public/js/frappe/ui/desktop_icon.html`): a filename-convention
lookup that needs `Desktop Icon.app`, then `logo_url`/`icon_image`, then the folder
thumbnail, then a **grey letter avatar**.

Every tile this app contributes sat on that last branch, and not for want of configuration.
`create_desktop_icons_from_workspace()` assigns `icon.app_name` — a field `Desktop Icon` does
not have; the real one is `app` — so `app` stays NULL on every auto-created row and the first
branch fails its own guard before it looks at anything else. That is an upstream bug, and it
is why setting `app` on our rows is not the fix: `bench migrate` also runs v16's
`unset_standard_field_for_auto_generated_icons`, which clears `standard` on any icon whose
app ships no on-disk JSON, so the branch-1 route needs the record shipped declaratively too.

Two other things look like the answer and are not:

- **`Module Def` has no `icon` field** in v16. The `module_def/*.json` files still in this
  repo are inert — `module_def` is not in `IMPORTABLE_DOCTYPES`, so they are never imported.
- **`Workspace.icon` does not drive the tile.** Frappe copies it once into the hidden
  `Desktop Icon.icon` field, renders it as a dead `data-icon` attribute, and never reads it
  back. All of our workspaces already set it; it changes nothing.

So `desktop_icons.py` writes `logo_url` — one SVG per tile instead of a subtle/solid pair, no
`app`/`standard` change for that core patch to undo, and independent of the
`Desktop Settings.icon_style` global. ERPNext ships its own Subcontracting tile the same way.

Shipping the records as `desktop_icon/*.json` instead *is* possible (it is an app-level sync
folder in `frappe/model/sync.py`), but `import_file_by_path` is gated on the file's `modified`
beating the row's, and these rows already exist at some arbitrary past timestamp — which turns
"did my change land?" into a timestamp race, and re-imports the whole row, `hidden` and
user-dragged ordering included. Stamping one field is deterministic and surgical.

One cost worth knowing: `/assets` paths are served immutable for a year with no content hash
(see the note in `hooks.py`). New filenames are fine, but **revising an icon in place will
never reach a device that already cached it** — ship a revision under a new filename and
update the map.

The artwork itself is generated: `scripts/build_desktop_icons.py` composes each 28×28 tile
from a lucide glyph in Frappe's own sprite, read at `origin/version-16`. Its output is
committed, and `tests/test_desktop_icons.py` fails the build if the two drift.

**A new tile never reaches someone who has saved their own home-screen layout.** v16 draws
the grid from that person's `Desktop Layout` row whenever it is non-empty
(`desk/page/desktop/desktop.py:16-20`, `desktop.js:220-229`), and the row is a copy of the
icon list frozen at their last Edit Layout save (`desktop_layout.py:28-42`). Nothing in v16
adds a later icon to it, and Edit Layout's "Removed Icons" pane is built from the same copy,
so they cannot add it back either; only Reset Layout does, at the cost of their arrangement.
On prod, Training, Time Kiosk and Quality Control are missing from every layout saved before
they existed. `desktop_icons.py` appends the tiles in `ADD_TO_SAVED_LAYOUTS` (Knowledge Base,
WI-080) to every saved layout that lacks them, after the person's own tiles. **Only a tile
every Desk User may open may go in that list:** a saved layout is drawn verbatim, with none of
`get_desktop_icons`' role or sidebar checks, so a role-gated tile added there would show to
people its page refuses. Adding Time Kiosk (no roles, every signed-in user) would be safe and
is a separate decision; a team hub never is.

### Renaming a tile for display (`DISPLAY_LABELS`)

Nik asked on 2026-10-12 for Sapphire Maintenance to be called **Maintenance** so people find
it on the home page. `desktop_icon_map.DISPLAY_LABELS` maps a tile's docname to the words it
shows. `_sync_display_labels` writes three things: the tile's `label`, the legacy Workspace
Sidebar's `title`, and the tile in every saved layout. The workspace JSON carries the page
heading (`title`).

Nothing that identifies the tile moves. The Desktop Icon docname, the Workspace's name and
`label` (its docname), the module and `/desk/sapphire-maintenance` all stay. Two v16.50 facts
make that safe, and one makes it necessary:

- `get_desktop_icons` stamps each tile with its workspace's `module`, and the client routes
  through `sidebar_for_module(icon.module || icon.label)`. So a site tile's label is display
  text only.
- `create_desktop_icons_from_workspace` looks a tile up by label but inserts it by docname
  with `ignore_if_duplicate`. A relabelled tile is therefore never duplicated by a later
  upgrade.
- **A saved layout is a frozen copy with no `module`**, and ERPNext ships a module called
  Maintenance. Relabelled without its module, a saved tile would route to ERPNext's own
  maintenance pages. So every relabelled layout entry also gets `module`.

## The Dock per job (`dock_profiles.py`)

frappe 16.50 draws the Dock, the icon rail on the left, from three layers of one app's `Dock`:
the app's own file, the site's arrangement (`user = ""`) and each person's own
(`user = <email>`). Later layers win, and a saved layer *is* the rail: an entry it does not name
is dropped, not appended (`frappe/desk/layers.py`, `keep_unnamed=False`). frappe has no per-role
layer, so a dock that differs by job is written one person at a time.

- **One rail.** `dock/erpnext_enhancements/erpnext_enhancements.json` is this app's own dock,
  `mount_on: erpnext`. Its rows are appended to ERPNext's: three URL rows, the Opportunities
  Kanban, Suppliers (`/desk/supplier`) and Accounts (Customers) (`/desk/customer`; Customer is
  relabelled Accounts on prod, and the row's title keeps it apart from ERPNext's Accounts module),
  then Workspace pins for Maintenance, Travel, Training, KPI Dashboards, Water Engineering,
  QuickBooks, HR, Knowledge Base, Fleet, AI Governance and Morning Briefing, then two more URL
  rows, Time Kiosk (`/desk/time-kiosk`) and Stock Scan (`/desk/stock-scan`), v1.751.0. Those two
  name desk pages, not the web apps themselves: a rail row opens through `frappe.set_route`, which
  reads any path as a desk route, so a row naming `/kiosk` would open `/desk/kiosk` and find
  nothing. Each page sends the browser on with `location.replace`, so Back returns to where the
  rail was clicked. **Change a row, move the file's
  `modified` forward**: frappe re-imports it only when that is later than the database row, and
  `test_dock_profiles.SHIPPED_DOCKS` fails the build until you do. The desk then shows ERPNext's rail in
  our modules too (`frappe.boot.app_rail_host`), where before our sidebars drew a second rail:
  22 "(Custom)" entries from the 16.50 conversion. That old site layer (`app =
  erpnext_enhancements`) is left in place and no longer read. Our areas are **Workspace** pins
  rather than Sidebar rows because the workspaces ship from this repo, while the "<area>
  (Custom)" sidebars are rows the conversion wrote on prod.
- **Layers are written whole.** Each names every entry the rail holds, the ones a person should
  not see as `hidden: 1`, which is what frappe's own Manage Dock saves and what puts them on its
  Hidden pane. The site's layer has to name everything for a second reason: a person's layer is
  laid over the site's, and a row naming an entry the site's layer dropped is skipped.
- **Whose layer it is.** A layer last written by Administrator is the app's. Manage Dock saves
  as the person, so once someone arranges their own it reads as theirs and no migrate touches it
  (Nik, 2026-10-23: "Keep their own"). Choosing a Dock Profile on the User form is the one
  override, because it is a deliberate act. A write made inside a User save runs as whoever
  saved it, so `write_layer` puts the stamp back to Administrator. Administrator's own layer is
  never managed, since it could not be told apart from the app's.
- **Who gets which.** `custom_dock_profile` (Select, on the User form beside Role Profiles) wins.
  Blank means the first `BY_ROLE_PROFILE` rule the person's Role Profiles match, and the order is
  the precedence: Design before Field Tech for someone holding both Design Team and Production
  Team. Site Default, or no match, means no layer of their own. Nik, James and Parker hold no
  Role Profile and are set by hand. **CEO** (Projects, Opportunities, CRM, Suppliers, Accounts
  (Customers); James, v1.736.0) is only ever chosen on the User form: no Role Profile maps to it,
  and Executive stays with Management.
- **Nothing is blocked.** The rail only stops *offering* an entry. Search, links and Manage Dock
  still reach every module, and frappe's own reach gate (module visibility, workspace roles)
  still runs on every boot. Blocking a module outright is what Module Profiles are for, and Nik
  chose not to use them.
- **The site's layer** is written once by the patch `set_dock_site_layer`, which overwrote Nik's
  hand-made layer of 2026-10-23 on purpose. After that it is the app's, and `apply_dock_profiles`
  keeps it current until someone arranges it again.

### The field technicians' home grid (v1.751.0)

Nik, 2026-10-27: technicians should see only "Maintenance, Morning Briefing, CRM, Project and Time
Kiosk" when they log in, and then "match them to each other", adding Travel, Training and Stock
Scan. So the **Field Tech** profile's rail and home grid list the same eight, in this order, from
one tuple, `FIELD_TECH`: Maintenance, Morning Briefing, CRM, Projects, Time Kiosk, Travel,
Training, Stock Scan. Each entry pairs the rail's key with the home grid's Desktop Icon docname.

- **Who.** Whoever's dock profile resolves to Field Tech: the Production Team (or Technician)
  Role Profile with no broader one ahead of it in `BY_ROLE_PROFILE`, or Field Tech chosen on the
  User form. On 2026-10-27 that was five enabled users. Production Team holders with another job
  (Projects & Operations, Design Team, Finance Team) and Site Default keep the whole grid, and
  so does anyone holding **System Manager** or **Workspace Manager** (`UNTRIMMED_ROLES`). The
  manager *roles* technicians hold (Maintenance, Projects, Stock Manager and so on, all from the
  Production Team profile) decide nothing.
- **How.** Prod draws the home screen as the Desktop Icon grid (Desktop Settings -> Desktop Page
  is "Desktop Icons"), from `frappe.boot.desktop_icons`. `trim_home_grid`, from `boot.py`'s
  `extend_bootinfo`, keeps only the profile's tiles, shown, at the top level and numbered in
  order. CRM's own tile is hidden on everyone else's grid, which is the flag that matters. (CRM
  and Projects name ERPNext's App icon as their parent; that icon is hidden, and frappe's
  `prepare()` already draws a hidden parent's children at the top level.) It runs on every boot,
  cached or not, so a profile change shows on the next page load. On the Apps screen there is no
  such list and nothing happens. A failure is logged once per worker process, not on every page
  load.
- **Hides, never grants.** It keeps only tiles frappe already allowed the person
  (`get_desktop_icons`' role, module and sidebar checks), so a tile frappe refuses stays out. No
  DocPerm, Block Module, Module Profile or workspace role changes. Timesheets, Maintenance Visits,
  ToDo and My Work are reached exactly as before, through links, search and the rail.
- **Where login lands is not this code's.** After login frappe sends a System User to
  `frappe.apps.get_default_path()`. A `User.default_app` of `erpnext` makes that ERPNext's
  `app_home`, the Home workspace (`/desk/home`), not the grid; one technician had it on
  2026-10-27 and it is cleared on the User form (Default App). The rest land on `/desk` only
  because the apps screen lists two desk apps, ERPNext and Frappe Assistant Core (its
  `add_to_apps_screen` entry counts while `can_use_faco` returns a non-empty dict). With a single
  app `get_default_path` returns that app's route, so without Frappe Assistant Core every
  technician would land on `/desk/home`.
- **A saved home layout.** frappe 16.53 draws a saved `Desktop Layout` only as far as the boot
  list goes (`arrange_layout` in `desktop_icons.bundle.js` leaves out a saved tile not in it), so
  a technician's saved layout cannot bring other tiles back. It never marks a **Folder** not
  permitted, though, so a folder in a saved layout still draws, empty. Two more edges, and no
  technician had a saved layout on 2026-10-27: a layout's own hidden flags win, and a tile it
  never held is not drawn. So someone who saved a layout and then becomes a technician, or saves
  one as a technician and later stops being one, needs **Reset Layout** (the grid's menu) once.
- **A workspace made from the grid** (Edit Layout → "+ Workspace", which Desk User may do) shows
  its tile until the next page load, then `trim_home_grid` drops it. The workspace stays.
- **The rail and the grid can drift.** A technician who rearranges their rail in Manage Dock
  keeps it (the layer is theirs now), while the grid stays at the eight. And a Field Tech who
  also holds System Manager or Workspace Manager gets the eight-entry rail but the whole grid.

## Writing a new one

Every step must be guarded by an existence check so that re-running is a no-op. Register the
entry point in `hooks.py` under the right hook, and add a row to the table above.

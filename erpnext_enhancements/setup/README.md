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
| `custom_html_blocks.py` | `after_migrate` | Upserts the Projects-module dashboard widgets from `custom_html_blocks/` |
| `custom_fields.py` | `after_migrate` | Provisions the "Contacts & Addresses" tab — Primary Contact link, directory widgets, primary address + location map — on the party doctypes, plus a Comments tab for Master Project (every other doctype's is fixture-owned) |
| `supplier_groups.py` | `after_migrate` | Relabels `supplier_group` to "Primary Supplier Group" and adds the additional-groups Table MultiSelect plus the denormalized text fields `supplier_query.sync_supplier_groups` populates |
| `workspace_tweaks.py` | `after_migrate` | Re-asserts overrides on core (erpnext-owned) workspaces and sidebars |
| `desktop_icon_map.py` | — | Data only: the Desk home-grid tile for each module (slug, glyph, colour). Importless on purpose — the generator and the bench-free test read it without a frappe stub |
| `desktop_icons.py` | `after_migrate`, `after_install` | Stamps that artwork onto `Desktop Icon.logo_url`, and creates the tile for a workspace core never gave one |

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
That is how v1.261.0 shipped ten Chat DocTypes and installed none of them (2026-08-09).

`before_migrate` is Frappe's `pre_schema_updates`, i.e. before **both** patch phases and before
`sync_all()`. That is the only window in which rebuilding the map helps; `after_migrate` is a
whole migrate too late. Deleting the cache key before calling `setup_module_map` is equally
load-bearing — it re-reads the key first and only falls back to `modules.txt` when it is empty.

Guarded in CI by `tests/test_module_installability.py`, and explained at length in
[`chat/README.md`](../chat/README.md#the-module-map-trap-modulestxt-is-not-enough-on-an-installed-site).

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

## Writing a new one

Every step must be guarded by an existence check so that re-running is a no-op. Register the
entry point in `hooks.py` under the right hook, and add a row to the table above.

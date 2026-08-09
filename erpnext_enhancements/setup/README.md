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

## Writing a new one

Every step must be guarded by an existence check so that re-running is a no-op. Register the
entry point in `hooks.py` under the right hook, and add a row to the table above.

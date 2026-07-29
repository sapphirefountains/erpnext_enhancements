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

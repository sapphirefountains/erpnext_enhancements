# 0003. The repo, not the site, is the source of truth for customizations

- **Status:** Accepted
- **Date:** 2026-07-29 (recorded retroactively)

## Context

ERPNext invites you to customise it through the UI: Customize Form, Client Scripts, Server
Scripts, workspace edits. All of it is stored in the database, which means none of it is
reviewed, none of it is in a diff, and none of it survives a fresh site.

Sapphire's instance had accumulated exactly that — ~425 Custom Fields, ~349 Property Setters,
plus database-stored Client and Server Scripts — with no record of who changed what or why.
Reproducing the site meant clicking.

## Decision

Everything is version-controlled, through the mechanism that fits its lifecycle:

| Mechanism | For | Behaviour |
|---|---|---|
| `fixtures/*.json` | Custom Fields, Property Setters, roles, permissions, workflows | Re-imported on `bench migrate` |
| `patches/` + `patches.txt` | One-time migrations and seeds | Runs once, recorded, never repeats |
| `setup/` (`before_migrate` / `after_migrate`) | Content that must be **re-asserted** every migrate | Creates what's missing, corrects drift |
| `script_migrations/` + `public/js/*_migrated_scripts.js` | The legacy DB-stored Client/Server Scripts | Ported into the repo; DB originals disabled as deploys land |

A change made in the UI is real but unversioned. Either edit the repo, or make the change on
the site and re-export promptly.

## Consequences

- **Fixture deletion is two steps.** Fixture sync is create/update only: removing a record
  from the JSON stops managing it but does **not** delete it from the database. A deletion
  also needs a patch calling `frappe.delete_doc`. `drop_orphan_source_property_setters` exists
  because three Property Setters were removed from the file alone and stayed silently inert
  for versions.
- **Drift discipline is not automatic.** Whether an *unchanged* fixture file is re-applied
  depends on the bench's Frappe version; empirically on this bench, unchanged deploys have not
  reverted UI drift. So never rely on migrate to discipline drift — re-export and commit, or
  accept the revert on the next fixture-touching deploy.
- **Deploys that touch fixtures are slow.** They re-import all ~774 records; `bench migrate`
  runs noticeably longer.
- **Ordering is load-bearing, and it bites.** Fixtures import in **alphabetical filename
  order**, so `custom_docperm.json` lands before `role.json` — which is why a new Role must be
  created by a `post_model_sync` patch rather than a `role.json` entry, or its permission rows
  reference a Role that doesn't exist yet (`seed_po_creator_role`). Equally,
  `setup/document_locks.py` must run on `before_migrate` because fixture sync happens in
  `post_schema_updates` and a stale Role Profile lock would fail it.
- **A Check field's `default` only applies at Single creation.** Ship a new gate with
  `default: 1` and every existing install gets it *off* — permanently dormant, silently
  enforcing nothing while appearing configured. Existing installs need a patch to flip it
  (`default_po_sod_on`, `default_contacts_ux_on`).
- **`setup/` overwrites, seeds insert.** `setup/process_documents.py` and
  `setup/custom_html_blocks.py` overwrite drifted content, because those charts and widgets
  are ours; the seeding patches are insert-only, because contract templates and process-step
  text carry human edits worth preserving. Pick based on whose content it is.

---
name: fixtures-and-patches
description: Change a Custom Field, Property Setter, Role, permission, or seeded record in erpnext_enhancements, or write a one-time migration. Use when editing fixtures, deleting a customization, writing a patch, or when a site-side change needs to become version-controlled.
---

# Fixtures and patches

**The repo is the source of truth for every manual customization.** ~425 Custom Fields and
~349 Property Setters live in `erpnext_enhancements/fixtures/*.json` and are applied by
`bench migrate`. A change made in Customize Form on the live site is real but *unversioned*,
and a fixture-touching deploy will re-assert every committed value over it.

Full detail: [`fixtures/README.md`](../../../erpnext_enhancements/fixtures/README.md) and
[`patches/README.md`](../../../erpnext_enhancements/patches/README.md). This skill is the
decision guide.

## Which mechanism?

| You want to… | Use |
|---|---|
| Add or change a Custom Field / Property Setter | Edit the JSON in `fixtures/` |
| **Delete** a Custom Field / Property Setter | Edit the JSON **and** write a patch — see below |
| Seed a record (template, role, settings row) | A patch, insert-only |
| Backfill or correct existing data | A patch |
| Rename a DocType before its JSON syncs | A `pre_model_sync` patch |
| Turn on a feature for existing installs | A patch — see the dormant-Check trap below |

## Deletion is always two steps

Fixture sync is **create/update only**. Removing a record from the JSON stops managing it;
it does **not** delete it from the database. You also need a one-shot patch:

```python
frappe.delete_doc("Custom Field", "Lead-source-reqd", ignore_missing=True)
```

`drop_orphan_source_property_setters` exists precisely because three Property Setters were
removed from the fixture file alone and stayed silently inert on the site for versions.

## The dormant-Check trap

A Check field's `default` applies **only when the Single doctype row is first created**. Ship
a new gate as a Check with `default: 1` and every existing install gets it **off** — the
guard is permanently dormant and silently enforces nothing while appearing configured.

Existing installs need a patch to flip it on (`default_po_sod_on`, `default_contacts_ux_on`).
Patches run once, so a later manual opt-out survives, which is the behaviour you want.

## Writing a patch

1. Add `erpnext_enhancements/patches/<name>.py` with a module docstring describing what it
   migrates — the docstring is the documentation.
2. Register it in `patches.txt` under `[pre_model_sync]` or `[post_model_sync]`, with a
   comment naming the version it shipped in.
3. Add a row to the table in `patches/README.md`.

`post_model_sync` is the default and runs **after** schema changes but **before** fixture
sync. That ordering matters: `seed_po_creator_role` has to be a patch rather than a
`role.json` fixture entry, because fixtures import in **alphabetical filename order**, so
`custom_docperm.json` lands before `role.json` and a permission row would reference a Role
that doesn't exist yet.

`pre_model_sync` is only for things that must happen before the new DocType JSON syncs —
renames, mostly.

## Patch conventions this repo already settled

- **Insert-only where a human might have edited the record.** `seed_process_step_templates`
  keys on `step_number`; `seed_contract_templates` keys on `template_key` so site-side legal
  edits survive.
- **Fill blanks, don't overwrite.** `seed_fountain_move_defaults` only fills empty settings,
  and deliberately does *not* guess an owner.
- **Detect drift and back off.** `update_production_procurement_step` logs and leaves the
  document alone rather than overwriting a human's edit.
- **Create the Custom Field first if the patch needs it** — patches run before fixture sync
  (`backfill_stage_changed_on`).
- **`Lead Source` and similar `istable` doctypes have no autoname**, so a plain `insert()`
  hash-names the record. Use `insert(set_name=…)`.

## Re-exporting after site-side work

For large layout changes it is reasonable to work in Customize Form and then export. The
re-export spec is in `fixtures/README.md`. Commit promptly — until you do, the change is
live and unversioned, and the next fixture-touching deploy may revert it.

Expect `bench migrate` to run noticeably longer on any deploy that changes these files: it
re-imports all ~774 records.

## Before you're done

Bump the version and write the changelog entry — see the `release-prep` skill.

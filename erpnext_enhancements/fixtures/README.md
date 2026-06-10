# Fixtures — version-controlled customizations

**The repo is the source of truth for all manual customizations.** Every manually
created Custom Field (425) and Property Setter (349) on the live site lives in
`custom_field.json` / `property_setter.json` here and is applied by `bench migrate`.
Changes made in Customize Form on the site do **not** survive: a deploy whose fixture
files changed re-asserts every committed value, and a fresh site gets everything from
these files. Whether an *unchanged* fixture file is also re-applied depends on the
bench's Frappe version — empirically on this bench, unchanged deploys have **not**
reverted UI drift. So never rely on migrate to discipline drift: if the site has
drifted, re-export and commit (or accept the revert on the next fixture-touching
deploy). Note that deploys which do change these files re-import all ~774 records,
so expect `bench migrate` to run noticeably longer on those deploys.

The other fixture files (workflow\*, notification, print_format) are the original
curated exports and are unchanged by this scheme.

## How to change a customization

1. **Preferred:** edit the JSON here in a PR (or ask Claude to do it), then deploy.
2. **For big layout work:** make the change in Customize Form on the site, then
   re-export (below) and commit. Until that commit deploys, the UI change is live but
   unversioned — don't leave it that way.

**Deletions need two steps:** removing a record from the JSON stops managing it but
does NOT delete it from the database — fixture sync only creates/updates. Also write a
one-shot patch (`frappe.delete_doc("Custom Field", name)`) for the deletion.

## Re-export spec

The fixture content is produced from the live site by exporting **Custom Field** and
**Property Setter** with `is_system_generated = 0`, minus the exclusions below —
exactly what the `fixtures` hook in `hooks.py` declares, so a server-side
`bench --site <site> export-fixtures --app erpnext_enhancements` exports the right
record set (but with different record order and key formatting — see below). The
canonical procedure is the MCP export (ask Claude; this README is the spec):

- records sorted by `name`; serialized with `indent=1`, sorted keys, trailing newline, LF;
- volatile metadata stripped: `modified`, `modified_by`, `creation`, `owner`, `idx`,
  user tags/comments/assignments;
- all other keys kept verbatim, including nulls, so imports reproduce records exactly.

If you must use `bench export-fixtures` instead, reformat afterwards to this spec to
avoid a noisy diff.

## Exclusions — records that look manual but are owned elsewhere

These have `is_system_generated = 0` on the site but were created by other installed
apps or the framework. They are filtered out of the `fixtures` hook in `hooks.py` and
must never be added to these files:

| Record | Owner |
|---|---|
| `User-user_category`, `User-verify_terms`, `User-hide_my_private_information_from_others` | `lms` app |
| `User-assistant_enabled` | `frappe_assistant_core` app |
| `Sapphire Maintenance Record-workflow_state` | Frappe workflow engine (auto-created) |
| `LMS Certificate-main-default_print_format` (Property Setter) | `lms` app |

Also deliberately absent: `Project-total_expense_claim` (Custom Field) — it is
`is_system_generated = 1`, created and owned by **HRMS**. (Our manual property setter
`Project-total_expense_claim-hidden` that hides it *is* in the fixtures.)

## Who else writes customizations (migrate pipeline order)

Within `bench migrate`: one-shot patches → **fixture sync (these files)** →
`sync_customizations` (`<module>/custom/*.json` — none exist in this app anymore) →
`after_migrate` hooks. Things that run *after* fixtures can override them, so the
app keeps those channels disjoint from fixture-owned records:

- `setup/custom_fields.py` (`after_migrate`) provisions the Contacts & Addresses /
  Comments tab fields. All records it manages are `is_system_generated = 1`
  (code-owned, intentionally not in fixtures); for fields that already exist it is
  insert-only or touches only its own system-generated widgets.
- `setup/supplier_groups.py` (`after_migrate`) inserts the
  `Supplier-supplier_group-label` Property Setter **only if missing** with the same
  value the fixture carries — benign duplication, can never override the fixture.
- One-shot patches (`patches/`) own further `is_system_generated = 1` fields (e.g. the
  Project procurement buttons). Fresh installs get those from the patches, not from
  fixtures — keep the patches.

## Fresh-install caveats

These concern building a brand-new site from this app (on the production site every
fixture record already exists; the only production effect noted below is the benign
one-time `custom = 1` → app-owned flip):

- ~~16 of the 28 Link/Table target doctypes are DB-only custom DocTypes~~ —
  **resolved in v0.7.0**: all 17 such DocTypes (the 16 fixture-referenced ones plus
  the transitively required Value Streams) are now proper app DocTypes under
  `crm_enhancements/doctype/` and `project_enhancements/doctype/`. App doctype sync
  runs before fixture sync on both migrate and fresh install, so `custom_field.json`
  imports cleanly. On the live site, the first v0.7.0 migrate flips them from
  `custom = 1` to app-owned (schema and data untouched); from then on their
  definitions are edited in the repo, not the UI. One name to watch: **Lead Source**
  — this ERPNext v16 install no longer ships its old doctype of that name, but if a
  future ERPNext upgrade reintroduces it, the names will collide and ours must be
  renamed first.
- Custom Fields import in file order (alphabetical by name), so a field whose
  `insert_after` points at a field created later in the file may land at the end of
  the form (cosmetic; identical to `bench export-fixtures` behavior).

---
name: add-doctype
description: Add a new custom DocType to erpnext_enhancements, or move one between modules. Use when creating a DocType, writing its controller, registering a module, or when the doctype-module-placement test fails.
---

# Adding a DocType

## Layout

Frappe maps a DocType's `module` field to a directory. Get them out of step and
`bench migrate` places or owns the doctype incorrectly.

```
erpnext_enhancements/<scrubbed_module>/doctype/<scrubbed_doctype>/
    __init__.py
    <scrubbed_doctype>.json      # schema
    <scrubbed_doctype>.py        # controller
    <scrubbed_doctype>.js        # desk form script (optional)
```

`scrub` = lowercase, spaces and hyphens to underscores. So DocType "Project Contract" in
module "Project Enhancements" lives at
`erpnext_enhancements/project_enhancements/doctype/project_contract/`.

## The check that will catch you

`tests/test_doctype_modules.py` asserts, for every DocType JSON in the app, that it sits
under its declared module's directory **and** that the module is registered in
`modules.txt`. It is pure filesystem and JSON — no bench needed — and it runs in CI.

```bash
python -m unittest erpnext_enhancements.tests.test_doctype_modules -v
```

A failure means one of three things: wrong folder, stale or typo'd `module` field in the
JSON, or a module missing from `modules.txt`.

## A new module

If the DocType belongs to a new module, you also need:

- a line in `erpnext_enhancements/modules.txt`
- `erpnext_enhancements/<module>/module_def/` or a `workspace/` entry, following an existing
  module's shape
- a `README.md` in the module directory — every module here documents itself next to its
  code, and the root `README.md` indexes them

## Moving a DocType between modules

Moving the folder is not enough — the live site still has the old `module` on the record.
Write a `post_model_sync` patch that reassigns it. There are many precedents in
`patches.txt` (`move_time_tracking_to_workforce`, `move_drive_to_google_drive`,
`move_briefing_to_morning_briefing`). See the `fixtures-and-patches` skill.

## Controller conventions

Match the surrounding module — indentation in particular varies by directory (Frappe
convention is tabs; parts of this app use 4 spaces).

Two things that are not style preferences:

- **Guard custom-field reads.** `doc_events` fire during ERPNext's own test bootstrap,
  before this app's custom fields exist. Read with `getattr(obj, "field", None) or ""` and
  guard column-filtered queries with `frappe.db.has_column(...)`. Without this, a fresh-DB
  install crashes.
- **Permissions belong in the framework**, not in ad-hoc checks. Custom DocPerm rows ship as
  fixtures; a new Role must be created by a patch rather than a `role.json` entry, because
  fixtures import in alphabetical filename order and `custom_docperm.json` lands first.

## Before you're done

1. Register any hook (`doc_events`, `scheduler_events`, `doctype_js`) in `hooks.py` —
   it is annotated, and the annotation is documentation.
2. Update the module's `README.md` file map.
3. Run the placement test above, plus anything else in the `run-tests` skill.
4. Bump the version and write the changelog entry — see `release-prep`. A new DocType is a
   **MINOR**.

# `utils/` — cross-cutting helpers

Small shared modules that don't belong to any one feature. Two of them monkey-patch or hook
site-wide behaviour, so read those before assuming this package is inert.

| File | Purpose |
|---|---|
| `patch_delete.py` | **Monkey-patches Frappe's delete endpoints** so link conflicts are recoverable (see below) |
| `triton_sync.py` | **Wired to `doc_events["*"]["after_save"]`** — fires on every document save site-wide (see below) |
| `working_days.py` | Business-day date arithmetic for the PRO-0204 hand-off SLAs. Monday–Friday only, skipping weekends and any configured Holiday List, so a 2-day SLA set on a Friday lands on Tuesday rather than Sunday. Holidays come from the standard ERPNext "Holiday List" — no new dependency. Used by `process_steps._refresh_due` |
| `phone.py` | Phone-number normalisation, extracted so the fountain-move intake matcher and any future party resolution agree on what "the same number" means instead of each re-deriving it |
| `deploy.py` | Per-deploy cache-bust token shared by the standalone PWAs (`/kiosk`, `/wall`) so both shells version their assets and service workers off one value |

## `patch_delete.py`

By default `frappe.client.delete` and the report-view bulk delete raise `LinkExistsError`
when a document is still referenced — a dead end for the user.

This module wraps both so that, **on an HTTP POST**, they instead return a JSON
`{"link_exists": True, …}` signal. The frontend reads it and offers the "unlink and delete"
flow backed by `delete_utils.py`. Non-HTTP callers still get the original exception, so
scripts and background jobs keep their fail-closed behaviour.

The patch is idempotent (guarded), which matters because Frappe imports modules more than
once across workers. `tests/test_monkeypatches.py` covers it.

## `triton_sync.py`

`global_triton_sync` runs after **every** document save on the site. It posts a lightweight
"this doctype/name changed" webhook to Triton, which then re-fetches the record itself,
keeping the assistant's index fresh.

Two properties make that safe, and both must be preserved:

- The payload is a **notification, not the document** — no data leaves except a doctype and a
  name.
- The HTTP call is **enqueued to a background worker**, so it can never block or fail a save.
  A synchronous call here would make every save on the site depend on Triton being up.

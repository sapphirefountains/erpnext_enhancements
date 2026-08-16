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
| `spreadsheet.py` | Turns a table of rows into a downloadable CSV/XLSX payload, shared by the Gantt export (`api/gantt.py`) and the Scope-tab task-tree export (`project_dashboard.py`). Returns the bytes **base64 inside the JSON response** rather than as a streamed download: the desk's `frappe.call` parses responses as JSON and would lose a binary body, and the redirect alternative would need the whole widget config in a URL query string. CSV is written with a UTF-8 **BOM** — without it Excel on Windows reads the file as the system codepage and mangles any non-ASCII customer or task name |

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

## `url_safety.py` and `sse_filter.py`

The **server-side** URL boundary for anything a model or a tool authored. Added v1.317.0 for
TASK-2026-01501, whose client-side half shipped in v1.282.3.

Both are deliberately **frappe-free** — standard library only — and that is a design
requirement rather than a happy accident: `is_safe_url` runs inside the SSE relay's per-frame
loop, and both are tested with no bench and no stub. `tests/test_url_safety.py` asserts the
absence of `import frappe`, because a later one added for a single log line would break the
place these exist for and would look harmless in review.

**`url_safety.is_safe_url` is a verdict oracle, not an origin resolver.** It answers yes/no
about a string; it never rewrites, canonicalises or returns a "cleaned" URL, because a
sanitiser that returns a modified string invites callers to trust the modification.

It is **not** a port of the client's `isSafeUrl`, and the reason is worth reading before
anyone "simplifies" it back to `urljoin`: `urlsplit("/\evil.example")` reports an empty
`netloc` and a harmless path, while every browser resolves the same string to
`http://evil.example/` — the exact input the original fix existed to close. Normalising
backslashes first fixes those cases and still leaves hundreds of disagreements on malformed
authorities. So the goal is **soundness, not equivalence**:

    is_safe_url(x) is True  =>  a browser also treats x as safe

and never the converse. Over-refusal costs one link rendered as a plain label; under-refusal
costs the boundary. `scripts/fuzz_url_safety.mjs | scripts/fuzz_url_safety_check.py` enforces
that direction in CI against the real WHATWG parser — it is what caught the invalid-port and
punycode holes, neither of which anyone foresaw.

**`sse_filter`** reassembles the byte stream into frames so each can be inspected. Its one
load-bearing rule: **if the transform changed nothing, emit the ORIGINAL bytes**, never a
re-serialisation. In production that is nearly every frame, so the relay stays byte-identical
to the old pass-through except where a frame was about to poison an `href`. That converts the
risk from *rewriting* (which can corrupt an answer) to *reassembly* (pure and offline-testable
at every byte offset). A broken filter here is worse than the bug it fixes and fails silently:
the widget swallows JSON parse errors and the 200 went out before the first byte, so corruption
presents as "the answer stopped mid-sentence" with no error anywhere.

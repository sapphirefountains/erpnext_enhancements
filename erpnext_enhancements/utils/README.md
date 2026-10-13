# `utils/` — cross-cutting helpers

Small shared modules that don't belong to any one feature. Two of them monkey-patch or hook
site-wide behaviour, so read those before assuming this package is inert.

| File | Purpose |
|---|---|
| `patch_delete.py` | **Monkey-patches Frappe's delete endpoints** so link conflicts are recoverable (see below) |
| `triton_sync.py` | **Wired to `doc_events["*"]["after_save"]`** — fires on every document save site-wide (see below) |
| `working_days.py` | Business-day date arithmetic for the PRO-0204 hand-off SLAs. Monday–Friday only, skipping weekends and any configured Holiday List, so a 2-day SLA set on a Friday lands on Tuesday rather than Sunday. Holidays come from the standard ERPNext "Holiday List" — no new dependency. Used by `process_steps._refresh_due` |
| `phone.py` | Phone-number normalisation, extracted so the fountain-move intake matcher and any future party resolution agree on what "the same number" means instead of each re-deriving it. The exact-match key for *matching*; what a phone should be *stored and shown as* is `normalize.py` |
| `normalize.py` | What a phone, email, website, address part or date looks like, stored and shown: phones stored E.164 (`+18015550100`, extension after a comma) and shown `(801) 555-0100 x123`; dates shown `10/14/2026 2:00 PM`. Pure and frappe-free (`print_style` imports it). Every function returns a `Result`; a value no rule can fix comes back exactly as typed with a reason, never guessed. Its JavaScript twin is `public/js/normalize/normalize.js`; both are held to `tests/fixtures/normalize_cases.json` by `tests/test_normalize.py`. See `docs/data-normalization.md` |
| `normalize_hook.py` | Normalize on save (form kit P2-B): `normalize_doc`, `doc_events["*"]["before_validate"]`, stores every registered value in its one form on a draft save (`docstatus` 0, `_action` "save") — never a submit, update-after-submit or cancel, never during `in_install` — and leaves a value it cannot normalize exactly as typed. One set lookup for an unregistered doctype; never raises, logging at most once per request and never the value. `desk_registry()` is `frappe.boot.sf_normalize`, the phone fields the Desk formats. Kill switch: `bench --site <site> set-config disable_value_normalization 1`, live, both halves |
| `value_display.py` | `sf_phone`: the Jinja global (registered individually in `hooks.py`) for a phone on paper, in an email or an SMS, `(801) 555-0100 x123`, over `normalize.phone_display`. Also `shown_if_stored_phone`, for a renderer that prints every field through one helper (the contract templates' `fill`) and touches only the exact stored shape |
| `normalize_fields.py` | The registry of which fields `normalize.py` may touch (`FIELDS`), which look like contact values and are deliberately left alone (`EXCLUDED`, each with its reason), and which are Singles (reported, never bulk-written) or child tables (`CHILD_TABLE_PARENTS` names the parents that carry them). Read by the *Data Normalization Preview* report and the save hook |
| `deploy.py` | Per-deploy cache-bust token shared by the standalone PWAs (`/kiosk`, `/wall`) so both shells version their assets and service workers off one value |
| `spreadsheet.py` | Turns a table of rows into a downloadable CSV/XLSX payload, shared by the Gantt export (`api/gantt.py`) and the Scope-tab task-tree export (`project_dashboard.py`). Returns the bytes **base64 inside the JSON response** rather than as a streamed download: the desk's `frappe.call` parses responses as JSON and would lose a binary body, and the redirect alternative would need the whole widget config in a URL query string. CSV is written with a UTF-8 **BOM** — without it Excel on Windows reads the file as the system codepage and mangles any non-ASCII customer or task name |
| `business_hours.py` | Working-time arithmetic in minutes (Mon–Fri, business hours, a holiday test) for the speed-to-lead SLA, where `working_days` counts whole days and would put a Friday-evening enquiry's one-hour deadline at 18:30 Friday. Stdlib only; the caller supplies the holiday check and site-local times |
| `popup_once.py` | `first_time(*parts)`: is this the first time the session user has reached this warning on this record? Kept in Redis for a week and fails open. Lets a `validate`-time `msgprint` show once per person per record instead of on every save. The Visit Wizard autosaves on every page, which is why the training and availability advisories use it |
| `date_display.py` | `sf_date` / `sf_datetime` / `sf_time`: the Jinja globals (registered individually in `hooks.py`, `sf_` prefix) for how a date reads on paper, in an email and in an SMS, `10/14/2026 2:00 PM`. Thin wrappers over `normalize.format_*`, imported lazily, falling back to the raw value. The Desk's own format (`10/14/2026`, `14:00`) is System Settings, set by `patches/set_desk_date_and_time_format` |
| `date_pairs.py` | **Wired to `validate` on every doctype in its `PAIRS` registry** (Opportunity, Project, Lead, Task, Sapphire Maintenance Contract, Training Batch, Project Contract, Managed Device). Warns once per person per record, and never blocks, when a start/end pair, or the Opportunity's event schedule, runs backwards. Registers only pairs nothing else validates; the docstring lists what was left out and why. See `docs/app-design-system.md` |
| `client_ip.py` | **Wired to `scheduler_events["daily"]`.** Classifies recorded client addresses (real client / Google Front End / load-balancer address / loopback / private) and checks daily that logins are not arriving from the load balancer. The real `request_ip` behind GCLB depends on a hand-placed nginx file (`infra/configs/nginx-realip.conf`); a rebuilt VM loses it silently and every IP-keyed rate limit becomes one global bucket. Classifier is stdlib-only; `audit_login_ips` is the `bench execute` before/after report |

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

# `product_feedback/` — employees file it, Triton plans it, a human writes it

The route from "I noticed this" to "it's on the board". Employees file bugs and feature
requests at [`/feedback`](../www/feedback.py); a System Manager approves one; Triton proposes
a work breakdown; the reviewer edits it; **then** ERPNext creates `Task` rows on
`PRJ-00580` (ERPNext Enhancements) or `PRJ-00755` (Triton Enhancements), or both.

The design record is [ADR 0010](../../decisions/adr/0010-employee-feedback-to-tasks.md). Read
it before changing anything in here — three of the decisions below look like implementation
detail and are not.

## The one rule

**A model proposes; a human confirms; one module writes.**

`task_writer.py` is the only code in the app that may construct a `Task` from a proposal, and
`tests/test_feedback_endpoint_surface.py` asserts that structurally (with a control asserting
`task_writer` still does, since `x not in source` is true of every `x`).

Triton *can* write to ERPNext — `FrappeClient.create_doc`, `fac_bulk_create_documents` — and
using that would be fewer moving parts. It would also put a model's mistakes straight onto the
two boards this company plans all of its engineering on. Do not add that path.

## Lifecycle

```
Submitted ──approve──► Approved ──worker──► Breakdown Ready ──confirm──► Tasks Created
    │                     │  ▲                    │   │
    │                     ▼  └──── re-run ────────┘   │
    │              Breakdown Failed ─────────────────┘
    └──────────────► Rejected / Duplicate ◄────────────
```

`status` is the machine; the table is in [`states.py`](states.py), which is **stdlib-only** so
it runs in the bench-free CI tier. It is not a Frappe Workflow, and ADR 0010 §3 records why:
three transitions are made by a background worker, and a Workflow transition is a human action
gated by a role.

The three terminal states are terminal. `Tasks Created` cannot be walked back, which is what
stops one proposal being written to a board twice.

## File map

| File | What it does |
|---|---|
| [`states.py`](states.py) | The transition table. Pure, stdlib only, bench-free tier |
| [`proposal.py`](proposal.py) | Parses and validates what the model returned. Pure, stdlib only |
| [`breakdown.py`](breakdown.py) | The background worker: builds the payload, calls Triton, writes the proposal. Also the hourly sweeper |
| [`triton_client.py`](triton_client.py) | HTTP to `POST /api/v1/planning/work-breakdown`, as the approving reviewer |
| [`task_writer.py`](task_writer.py) | **The only `Task` creator.** Runs after the reviewer confirms |
| [`notify.py`](notify.py) | Bell row + email. Four events, one audience each |
| `doctype/` | `Enhancement Request`, its two child tables, the reviewer child table, and the settings Single |

Elsewhere: [`api/feedback.py`](../api/feedback.py) (seven POST-only endpoints),
[`www/feedback.py`](../www/feedback.py) + `feedback.html` (the shell),
[`public/js/feedback/`](../public/js/feedback/) and `public/css/feedback.bundle.css` (the SPA),
[`patches/seed_product_feedback_settings.py`](../patches/seed_product_feedback_settings.py).

## DocTypes

| DocType | Role |
|---|---|
| `Enhancement Request` | `ER-{YYYY}-{#####}`. What was filed, what was decided, and the proposal. Not submittable |
| `Enhancement Request Proposed Task` | One proposed task. Nothing here has been written anywhere until `created_task` is stamped |
| `Enhancement Request Duplicate Candidate` | An existing `Task` the model thinks already covers this. Advisory |
| `Product Feedback Reviewer` | Who is *told* a request arrived. Grants nothing |
| `Product Feedback Settings` | Single: `paused`, the two board ids, the caps, the notify list |

Permissions on `Enhancement Request`: `System Manager` full; `{"role": "All", "read": 1,
"if_owner": 1}`. **The requester deliberately has no write.** Write would let them move
`status`, and `Submitted -> Approved` is a *legal* transition — the table would wave a
self-approval straight through. That is also why attachments are linked server-side by
`api.feedback.submit_request` rather than uploaded onto the request.

## Four things that look defensive and are load-bearing

**The kill switch is named `paused`, not `enabled`.** A brand-new Single has no rows in
`tabSingles` until something saves it, so every `get_single_value` answers `None` on the day it
ships — an `enabled` field would have shipped the feature dead on arrival. Naming it for the
off state makes the absent-row state the running state. `get_settings()` applies every fallback
itself; the seed patch is for visibility in the desk, not for correctness.

**The status is the outbox.** The prod deploy `FLUSHDB`s the queue redis and destroys queued
jobs silently. A request in `Approved` with no proposal *is* a lost job, it is visible in the
review queue, and `sweep_stalled_breakdowns` re-drives it hourly. Do not add a `Chat Relay
Job`-shaped table here — and do not add `deduplicate=True`, which drops the new enqueue while
an existing job is QUEUED **or STARTED**.

**The model names a `target`, never a Project.** `proposal.py` maps `"erpnext"`/`"triton"` to
an id from settings. The prompt reasons over prose an employee typed; a model that could emit a
project id could write onto a live customer job. Same rule for `parent_task` and every
duplicate id — both must be in the list ERPNext sent, because a model asked to cite a task id
invents a plausible one.

**`Product Feedback` is excluded from `global_triton_sync`.** One request is saved several
times during a breakdown, and the thing doing the breaking down is Triton. See
[`utils/triton_sync.py`](../utils/triton_sync.py).

## Notifications

`notify.py` inserts a `Notification Log` of type **`Alert`** and sends its own email. That is
not a double-send: Frappe's own `hooks.py` ships `notification_skip_email_types = ["Alert"]`
and that check runs *before* the user's own settings, so an `Alert` row is bell-only by
construction. `Alert` is also in `notification_self_notify_types`, which matters because the
reviewer who approves is the person the breakdown-ready notification goes to.

`status_alerts._deliver` was the obvious reuse and was rejected — it also fires SMS through the
Triton gateway, and a feature request is not an operational alert.

## Triton dependency

Needs Triton `>= 0.70.0` for `POST /api/v1/planning/work-breakdown`. Until that deploys,
approving a request lands it in `Breakdown Failed` with a legible reason and a re-run button:
the feature degrades to a triage queue rather than breaking.

The client reads `Triton Settings.gateway_url` / `admin_webhook_secret` through
`triton_chat.get_settings()` but deliberately **ignores** its `enabled` flag — that is the desk
widget's switch, and turning the floating chat bubble off should not silently break feature
intake.

## Tests

```bash
python -m unittest erpnext_enhancements.tests.test_feedback_states -v
python -m unittest erpnext_enhancements.tests.test_feedback_endpoint_surface -v
python -m pytest erpnext_enhancements/tests/test_feedback_breakdown_parse.py -q
```

All three are bench-free and in CI. The parse suite is **pytest-style** and has its own step —
`python -m unittest` collects nothing from plain `def test_*` functions and reports success.

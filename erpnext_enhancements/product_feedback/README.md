# `product_feedback/` — employees file it, Triton plans it, a human writes it

The route from "I noticed this" to "it's on the board". Employees file bugs and feature
requests at [`/feedback`](../www/feedback.py); a System Manager approves one; Triton proposes
a work breakdown; the reviewer edits it; **then** ERPNext creates `Task` rows on
`PRJ-00580` (ERPNext Enhancements) or `PRJ-00755` (Triton Enhancements), or both.

The design record is [ADR 0010](../../decisions/adr/0010-employee-feedback-to-tasks.md). Read
it before changing anything in here — three of the decisions below look like implementation
detail and are not.

**Being built:** [ADR 0016](../../decisions/adr/0016-every-source-files-an-enhancement-request.md)
amends 0010 — the capture widget and Design Review will file requests through the same door,
the breakdown will read the files a request is about, and shipped work will report back through
the changelog. The build is [WI-079](../../work-items/WI-079-feedback-capture-and-design-review.md).
Slice 1 (v1.524.0) is in: every Task the writer creates, groups included, carries
`custom_enhancement_request` (backfilled for the 37 it wrote before — leaves from `created_task`,
groups from their origin note, never from their children); the code map sends every CLAUDE.md
gotcha headline instead of a 6,000-character prefix, plus the real size of each capped listing
under `totals`; and the one-writer test is an AST check that also catches `frappe.new_doc("Task")`.
Slices 2 to 4 are in as well: the capture widget (section below), the code anchors
(`code_anchors.py` in the file map), and, in v1.531.0, the Claude Code brief and the hourly release sync that moves shipped Tasks to
`Pending Review` (see "The brief and the status return").

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

**A confirm with nothing ticked is refused, not written.** `create_tasks` throws before the
writer runs, and the writer's own empty return carries the full result shape;
`tests/test_feedback_endpoint_surface.py` pins both with AST. The first zero-task breakdown,
ER-2026-458194, found the gap as a 500 on the button (v1.474.2). A request that needs no work
is closed with Reject or Duplicate — `Tasks Created` means there is work on a board.

**Which is also why the board shows a "Tasks Completed" pill that is not a status.** A request
whose tasks are all finished stays on `Tasks Created` forever — correctly; nothing may move it
— so it read that way while the Work column beside it said `2/2`. The finished state is a
*display* rule over the task counts the API already returns
([`public/js/feedback/status.js`](../public/js/feedback/status.js)), not an eighth option in
the Select. It costs no column, no patch and no hook on Task, and it goes back off by itself
when a task is reopened, because the label **is** the tasks. `tests/test_feedback_states.py`
asserts the derived label never becomes a real `RequestState` — if it ever does, two different
things are spelled the same and they disagree the first time somebody reopens a task.

## File map

| File | What it does |
|---|---|
| [`states.py`](states.py) | The transition table. Pure, stdlib only, bench-free tier |
| [`proposal.py`](proposal.py) | Parses and validates what the model returned. Pure, stdlib only |
| [`breakdown.py`](breakdown.py) | The background worker: builds the payload, calls Triton, writes the proposal. Also the hourly sweeper |
| [`code_anchors.py`](code_anchors.py) | Where in the code one request points (WI-079 slice 3): the `www/` page behind its route, its doctype's fields (restricted ones flagged), controller outline and this app's hooks on it, the module README and the CHANGELOG lines that name them. Sent as `anchors` in payload schema 2 beside `codemap.py`'s whole-repo map. Schema and code facts only, never a docname or a value; deterministic, 40,000 characters at most, `{}` when it cannot build |
| [`triton_client.py`](triton_client.py) | HTTP to `POST /api/v1/planning/work-breakdown`, as the approving reviewer |
| [`task_writer.py`](task_writer.py) | **The only `Task` creator.** Runs after the reviewer confirms. Also `mark_shipped`, the second writer: a shipped feedback Task goes to `Pending Review` (WI-079 slice 4) |
| [`brief.py`](brief.py) | The Claude Code brief for a request whose Tasks exist (WI-079 slice 4): Markdown in the work-item shape (Why, Scope, Acceptance criteria, Explicitly NOT in this work item) plus a `json` Data block with the Task ids, the anchors, the design notes and the exact `Refs:` line, which is also printed bare in a block of its own to paste. Closed leaves are marked and kept off that line. Pure, imports no frappe; no requester, no docname; at most 60,000 characters, whatever the input. Served by `api.feedback.claude_code_brief` and the form's "Claude Code Brief" button |
| [`release_sync.py`](release_sync.py) | Hourly. Reads the installed `CHANGELOG.md` and hands every `TASK-…` on a `Refs:` line of a release at or below the installed version (`tabInstalled Application`) to `task_writer.mark_shipped`, with the line's `ER-…` ids as a cross-check. The parser is strict so the CHANGELOG's own examples stay inert. Marker: `Product Feedback Settings.release_sync_last_version`, no default, absent = process everything |
| [`notify.py`](notify.py) | Bell row + email. Four events, one audience each |
| [`capture_jobs.py`](capture_jobs.py) | The capture widget's scheduled work: daily retention of screenshots + context files (and of screenshot uploads that never reached a request), hourly Error Log matching (WI-079 slice 2) |
| `doctype/` | `Enhancement Request`, its two child tables, the reviewer child table, and the settings Single |

Elsewhere: [`api/feedback.py`](../api/feedback.py) (eleven POST-only endpoints, `file_request` among its helpers),
[`doctype/enhancement_request/enhancement_request.js`](doctype/enhancement_request/enhancement_request.js) (the "Claude Code Brief" button),
[`www/feedback.py`](../www/feedback.py) + `feedback.html` (the shell),
[`public/js/feedback/`](../public/js/feedback/) and `public/css/feedback.bundle.css` (the SPA),
[`patches/seed_product_feedback_settings.py`](../patches/seed_product_feedback_settings.py).

## The capture widget (WI-079 slice 2)

A "Report a problem" that works from any Desk screen, the kiosk, and a short list of signed-in
web pages. It files an ordinary Enhancement Request with `source = Capture`, and everything
downstream (review, breakdown, confirm) is unchanged. ADR 0016 §4 holds the rules; these are
the ones that shape the code.

- **One way in.** `api.feedback.file_request` is the only code that creates a request. It holds
  the validation, the provenance stamps and the per-person limit: ten a minute, keyed on the
  session user, a 429 after that. `submit_request` (the `/feedback` form) and `submit_capture`
  (the widget) both call it, and Design Review promotion will too.
- **Provenance is never client input.** `source`, `source_doctype`, `source_ref` and
  `context_release` are set by the endpoint, absent from `SUBMIT_ALLOWED_FIELDS`, and in
  `_FROZEN_FIELDS`. `source` defaults to `Feedback form`, so the ALTER labeled the existing rows
  with no patch.
- **An allowlist, never `web_include_js`.** The recorder is the first import of the Desk
  bundle, and `capture.bundle.js` is included only by `www/kiosk.html`, `feedback.html`,
  `itinerary.html` and `travel_guidelines.html`. Guest, token and customer pages load neither.
  `tests/test_feedback_capture_surface.py` checks every template in the app as text and follows
  every bundle's imports, so a bundle that pulled the recorder in would fail it too.
- **System Users only**, on the server. The launcher's `system_user` cookie check is a hint.
- **The snapshot is a private File, not a field.** It holds page state, the last 20 console
  errors, the last 20 failed or slow requests (never bodies) and the last 10 routes, and it is
  stored as `capture-context-<ER>.json`, because Triton's bulk sync reads the request's fields.
  The person sees all of it before sending.
- **Error Logs are matched later, by a job.** v16 writes a 5xx's Error Log through
  `deferred_insert`, flushed every 15 minutes with the scheduler as owner and the flush time as
  `creation`. So `capture_jobs.match_capture_error_logs` runs hourly and matches on the row's
  `metadata` (user, verb, path) inside that window, correcting for the browser's clock from the
  `sent_at` the panel stamps as it sends. It notes
  each match as a Comment on the request, which outlives Error Log's 14-day clearing.
- **Retention.** `capture_jobs.purge_expired_capture_files` deletes the screenshots and the
  context file 180 days after the request closed, and keeps the request, its text and its Task
  links. The clock is `terminal_at`, which the controller stamps on entering a terminal state,
  falling back to `modified`, which can only keep files longer. The join is on the capture
  artifacts only, so a cleaned request drops out even when it keeps a PDF. The same run deletes
  the panel's screenshot uploads (`capture-shot-*`) still unattached after a day, from filings
  that failed after the upload.
- **A resend is not a second report.** Each report carries an id (`client_id`). A lost response
  looks offline to the panel, which saves a draft; when the draft is sent, `submit_capture`
  returns the request it already filed. The id is remembered per user, after commit, for 8
  days. A pause raises `FeedbackPausedError`, so a saved draft survives it.
- **The screenshot is pasted or uploaded, then annotated in the browser.** The tools are box,
  arrow, pen, text, blur and crop. Blur is burned in, and only the flattened image is uploaded.
  Automatic capture of the page waits for the spike the WI describes.

## The brief and the status return (WI-079 slice 4)

ADR 0016 §5. The far end of the route: confirmed Tasks go to a Claude Code session as a brief,
and a release that ships them reports back on the board.

- **The brief.** A reviewer presses "Claude Code Brief" on the Enhancement Request form, or calls
  `api.feedback.claude_code_brief`. It needs Tasks, not a status: `created_task` rows, or Tasks
  back-linked through `custom_enhancement_request`. The Markdown follows `work-items/*.md`,
  headings included (`## Explicitly NOT in this work item`), and its last acceptance criterion
  is the exact `Refs:` line, printed bare in a fenced block of its own with a note to paste it
  without backticks or bold. A leaf that is `Completed`, `Canceled`, `Cancelled` or `Invoiced`
  is marked in Scope and left off that line and the boxes. It never carries the requester or the
  docname. The group Task's origin note is removed because it names both the requester and the
  approver, and the path goes through `code_anchors.parse_path`. It is not a Triton or assistant
  tool.
- **The `Refs:` convention.** To mark feedback Tasks shipped, a release's CHANGELOG section
  carries one line anywhere in it:

  ```
  Refs: ER-2026-00012, TASK-2026-00345, TASK-2026-00346
  ```

  **The parser is strict on purpose**: the CHANGELOG documents this convention with examples,
  and its example Task ids are real Tasks on production (checked 2026-09-24). `Refs:` is
  case-sensitive and starts
  its line, after at most three spaces and an optional list marker (`- `, `* `, `+ `, `1. `).
  A line that starts with a backtick or `**`, one indented four spaces or a tab, one inside a
  fenced block (backticks or tildes, closed only by the same character at least as many times,
  like the one above) or inside an HTML comment, and a mention mid-sentence, are none of them
  Refs lines. `tests/test_feedback_release_sync.py` runs the parser over the real CHANGELOG and
  fails the build on a Refs line naming a Task unless its `SHIPPED_REFS` lists the release, so a
  release that really ships feedback Tasks adds its line there in the same change.
  Only `TASK-…` ids move Tasks, so a release that ships part of a request names only the Tasks it
  shipped. `ER-…` ids are a cross-check: when the line names any, a Task that belongs to another
  request is skipped, so a typo cannot move a neighbor's Task. Anything else on the line, such as
  a `WI-079`, is ignored. The brief writes the line for you: the request plus its open leaf
  Tasks.
- **`release_sync`, hourly.** It acts only on sections at or below the version
  `tabInstalled Application` records. v16 writes that near the end of a migrate that got far
  enough, so a half-installed deploy's CHANGELOG is never believed. Each `TASK-…` goes to
  `task_writer.mark_shipped`, which moves a Task this pipeline created from `Open`, `Working` or
  `Overdue` to `Pending Review`, sets `review_date` 14 days out (so ERPNext's overdue job leaves
  it alone), and comments with the version. It never moves a Task to `Completed`: a person closes
  shipped work. It skips a Task id that does not exist (a typo must not hold the marker), a Task
  of a request the line does not name, and an `Overdue` Task whose last status change on record
  (its newest `Version` rows) was to `Canceled`, `Cancelled`, `Invoiced`, `Completed` or
  `Template`: ERPNext v16's overdue job exempts only `Cancelled` and `Completed`, so it flips
  this site's `Canceled` to `Overdue`, and that flip is a `db_set` that leaves no Version. With
  those rules a replay is harmless, and that is why the marker (`release_sync_last_version`)
  has no default: absent means "process everything". The marker moves to the installed version
  only after a clean run, and otherwise only as far as the last release before the first
  failure; one Error Log records the failures. A run makes at most 500 save attempts, and a skip
  is not one, so a replay costs nothing and a capped run is followed by one that gets further.
  The next hour retries, so a deploy's `FLUSHDB` costs an hour, not a transition.

## DocTypes

| DocType | Role |
|---|---|
| `Enhancement Request` | `ER-{YYYY}-{#####}`. What was filed, what was decided, and the proposal. Not submittable |
| `Enhancement Request Proposed Task` | One proposed task. Nothing here has been written anywhere until `created_task` is stamped |
| `Enhancement Request Duplicate Candidate` | An existing `Task` the model thinks already covers this. Advisory |
| `Product Feedback Reviewer` | Who is *told* a request arrived. Grants nothing |
| `Product Feedback Settings` | Single: `paused`, the two board ids, the caps, the notify list, and `release_sync_last_version` (the release sync's marker, no default) |

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
review queue, and `sweep_stalled_breakdowns` re-drives it hourly. Do not add a separate
relay-job table to track what was enqueued — that was the shape the retired chat module used
(`Chat Relay Job`, gone in v1.426.0), and a second record of what still owes work is exactly
the job the status is already doing. And do not add `deduplicate=True`, which drops the new
enqueue while an existing job is QUEUED **or STARTED**.

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
python -m unittest erpnext_enhancements.tests.test_feedback_brief -v
python -m unittest erpnext_enhancements.tests.test_feedback_release_sync -v
```

All of them are bench-free and in CI. The last two each have their own step: the brief suite
installs an empty `frappe` placeholder, and the release sync suite installs a `frappe` stub. The parse suite is **pytest-style** and has its own step —
`python -m unittest` collects nothing from plain `def test_*` functions and reports success.

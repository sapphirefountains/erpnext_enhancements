# WI-079: Capture anywhere, Design Review, and a repo-aware task generator

**Phase:** 2   **Type:** APP_CODE   **Size:** L (five slices, M to L)
**Blocked by:** nothing for slice 1 except the gate decision named there; slice 3 needs a Triton release (prompt repair, schema v2)
**Blocks:** nothing
**Decision record:** [ADR 0016](../decisions/adr/0016-every-source-files-an-enhancement-request.md), amending [ADR 0010](../decisions/adr/0010-employee-feedback-to-tasks.md)

## Why

Nik asked on 2026-09-23 for four things, and settled the open choices the same day by taking the
recommended defaults:

1. A **feedback capture on every surface** — Desk, PWAs, SPA pages and web pages — that takes a
   screenshot the user can annotate, snapshots the page state, and records console errors and
   failed network requests.
2. A **generic Design Review module** to replace the one-off artifact ballots, with voting and
   notes gated to ERPNext users. Approved notes and decisions go to the same task generator as
   Enhancement Requests.
3. A **breakdown that actually reads this repository**.
4. A **hand-off Claude Code can execute**, with status flowing back.

Why now, in numbers (production, read-only, 2026-09-23): 16 Enhancement Requests from 5
requesters since 2026-08-17, **none of them with a captured page**; the pipeline created 37 Tasks
(30 leaves and 7 groups), 33 of them Completed, none on the Triton board. The pipeline works and
is used; its intake is blind and its planner is blind. ADR 0016 records the reasoning; this item is
the build.

The defaults Nik accepted:

| Question | Decision |
|---|---|
| Who plans | Triton, now with the real files; revisit once the cost is measured |
| Where captured data may go | ERPNext only; briefs name files but never carry screenshots; the breakdown stops receiving document names |
| Promoted design decision | Counts as the first approval for a human System Manager; the confirm step still runs |
| AI write gate | Turned on, scoped as ADR 0016 §6: new Tasks and Task closures wait for a human; other Task updates and Comments do not |
| `WI-xxx` Tasks through `task_writer` | Later, once the status return exists |
| Who votes on a design review | ERPNext users on the review's participant list; no login, no vote |

## Native-first check

Checked against Frappe and ERPNext `version-16` (`git show origin/version-16:…`) and production,
2026-09-23.

| Candidate | Verdict |
|---|---|
| ERPNext `Issue` | **Rejected**, as in ADR 0010 §4: customer-shaped (SLA, support queue). |
| Frappe `standard_help_items` hook (`frappe/hooks.py:494`) | **Adopted** for the Desk entry point: a "Report a problem" item in the Help menu, declared with `"is_standard": 1` so it syncs on every migrate and is removed by deleting the hook entry. No injected navbar script, no Navbar Settings fixture. |
| Frappe `frappe.request.report_error` (the Desk's Server Error dialog: Report / copy) | **Rejected as the intake, reused as a hook.** Desk-only and server-errors-only, and it emails a traceback outside ERPNext. The recorder captures the same request metadata, without bodies, and the dialog can offer "Report a problem" alongside its own buttons. |
| Frappe `Error Log` | **Adopted** as the server-side half of a failed request: the widget links to it rather than copying tracebacks. Matched by user, method and time, because `trace_id` is written only when `monitor` is on in site config and production has written none since 2026-07-17. |
| ERPNext `Task` status `Pending Review` | **Adopted** as the shipped-but-not-verified state (`task.json:124`; the site's options come from the `Task-status-options` Property Setter, which keeps it). No custom status. `mark_shipped` sets `review_date`, because ERPNext's daily `set_tasks_as_overdue` otherwise moves a Pending Review Task whose expected end has passed to `Overdue`. |
| Frappe `Workflow` | **Adopted for Design Review's lifecycle** (Draft → Open → Closed → Decided): every transition is a human action gated by a role, which is exactly what a Workflow is. Still rejected for the Enhancement Request, for ADR 0010 §3's reason. |
| Frappe `Comment` for design notes | **Rejected.** A design note needs an element code, a status, a raiser distinct from the typist, and a promotion link. |
| Frappe `@rate_limit` | **Rejected** for the submission limit: it keys by IP (one budget for a whole office) or by a value the client supplies, never by session user. |
| Voting, screenshot capture, client error capture | **None found** in Frappe or ERPNext. |

## Preconditions

- Slice 3 needs a Triton release for the prompt repair (render totals as "first N of M", render the
  doctypes and environment it drops, a test pinning the rendered prompt) and the v2 request schema.
  The ERPNext side ships first and degrades to today's behavior when Triton answers v1.
- Slice 5's frame rendering depends on the viewer spike's outcome (below).
- The gate patch in slice 1 waits for Nik's call on `run_python_code` (below).

## Scope

### Slice 1 — Repair, back-link, close the side door [M]

- ERPNext sends every `CLAUDE.md` gotcha **headline** rather than a 6,000-character prefix
  (`codemap.py:61`), and sends each capped listing's total alongside it (`codemap.py:60` truncates
  silently today). The prompt says "first N of M" once slice 3's Triton release renders it.
- `Task.custom_enhancement_request` (Link, fixture), stamped by `task_writer` on every Task it
  creates, groups included (`_ensure_groups` as well as `_create_leaves`). An idempotent backfill patch
  stamps the 37 existing Tasks: the 30 leaves from `Enhancement Request Proposed Task.created_task`,
  the 7 groups from the "Raised from ER-…" line `_origin_note` writes into their description.
- The one-writer test (`tests/test_feedback_endpoint_surface.py:168`) also matches
  `frappe.new_doc("Task")`, and its scan covers the new modules as they land.
- **AI write gate**, as ADR 0016 §6:
  - Inventory from `tabAssistant Audit Log` (the `AI Action Log` records nothing while the gate is
    off). On 2026-09-23 the last 30 days held 232 Task creates, 419 Task updates, 214 Comment creates
    and 1,595 `run_python_code` calls, all by one user.
  - A `PER_CALL_GATED` decider on `update_document`: executes when `doctype == "Task"` and the new
    status is not `Completed` or `Canceled`; needs a human for everything else.
  - `Comment` added to the exempt doctypes. `Task` never is: the exemption step applies to create
    and update alike.
  - **Nik decides `run_python_code`** before the patch runs: gated (every probe a confirmation) or
    ungated through a decider, which is defensible only once its sandbox is verified read-only.
  - Then `ai_write_gating_enabled` is turned on through a patch, recorded in the changelog.

**Status, v1.524.0 (2026-09-23).** Everything above except the settings patch shipped. The
back-link, backfill, code-map repair and one-writer test are live; the `update_document` decider
and the `NEVER_EXEMPT` guard are in `_gate.py` and dormant while the flag is 0. The flag and the
`Comment` exemption row were **held**, because the condition on `run_python_code` failed:

- **Its sandbox is not read-only.** As deployed (FAC 3.0.0, not overridden here) the child process
  gets the whole `frappe` module and `frappe.get_doc` on a normal read-write connection; only the
  local `db` variable is wrapped, and `db._original_db` gets past even that. `frappe.os` and
  `frappe.get_module` reach the operating system. Production holds rows it created: in the 30 days
  to 2026-09-23, 19 successful calls contained `db.commit`, and on 2026-09-14 calls created 6 Items,
  10 Assets and a Comment. It is arbitrary code for any System Manager (Administrator, Nik,
  `triton@`), and ungating it would also ungate every Task create and close the gate exists for.
  It stays gated; `test_ai_gate_per_call` now pins `PER_CALL_GATED` disjoint from `HIGH_RISK`.
- **The gate's load is larger than the inventory above said.** Re-measured on 2026-09-23, the last
  30 days under the §6 scope hold about **928** confirmations — 245 Task creates, 233 Task closes and
  about 450 other writes (maintenance templates, Training Lessons, ToDos, Serial Nos…), which ADR
  0006's default gates — plus about 1,600 `run_python_code` calls while it stays gated. Before the
  flag goes on, Nik chooses how to carry that load (for example routine diagnostics through the
  SELECT-only `run_database_query`, which the gate already lets through).
- Found alongside, and outside this slice: `tabAssistant Audit Log` row
  `ASST-AUDIT-2026-09-17-00048` holds the site database user's password hash in its output, readable
  by anyone who can read that log; and `gating_api.confirm_action` re-executes the *sanitized*
  arguments stored on the Pending Action, so a confirmed write whose data key contains a sensitive
  substring (for example `author`) would write `***REDACTED***`.

### Slice 2 — Capture anywhere, v1 [M]

- `file_request(values, requested_by, source, source_ref)` extracted from `submit_request`
  (`api/feedback.py:422`). New Enhancement Request fields `source`, `source_doctype`, `source_ref`
  (Dynamic Link on `source_doctype`) and `context_release`, all in `_FROZEN_FIELDS` and none accepted
  from client input. The context snapshot is a private JSON File on the request, not a field.
- A per-user limit in `file_request` itself — `frappe.cache` counter on the session user, 10 per 60
  seconds, raising `frappe.RateLimitExceededError`. Submission has no limit today.
- `public/js/feedback/context.js:43` learns `/desk/…` paths; it parses only `/app/…` today.
- **Entry points:** the Desk Help item; a small launcher on web pages and SPAs **on the allowlist
  only**; a row in the kiosk's Settings tab. Submission requires a System User on the server.
- **The recorder** starts at page load and is a few KB: in the Desk bundle (`app_include_js`) and,
  for web pages, in its own `capture.bundle.js`, included only by the allowlisted templates' own
  script blocks — **never** in `web_include_js`, which Frappe emits on every website page. Every page
  not on the allowlist, including every guest, token and customer page (`/pay`, `/pay-card`,
  `/stripe-return`, `/contract-sign`, `/fountain-move`, `/maintenance-records`, `/training_certificate`,
  `/update-password`, `/login`, `/qrcode`, `/confirm_workflow_action`, the policy Web Pages, public Web
  Forms and the ERPNext portal) and unattended displays such as `/wall`, loads neither the recorder
  nor the launcher. A bench-free test asserts that `web_include_js` does not name the capture bundle
  and that only allowlisted templates include it.
  - It keeps the last 20 console errors, uncaught errors and unhandled rejections, collapsed when
    repeated, text scrubbed of email addresses and tokens; the last 20 failed or slow requests
    (method, path without tokens, status, duration, exception type) — **never bodies**; and the last
    ten routes. Everything inside `try/catch`: a recorder fault must never break the page it watches.
- **Page state** on open: Desk route, query string and title; form doctype, name, `docstatus`,
  unsaved flag and changed field *names*; list and report filters, lifted out of `triton_widget.js` so
  both widgets share one implementation; a `registerCaptureState(fn)` hook for SPAs and PWAs (the kiosk
  registers clock status, offline queue length, last sync and location permission); service-worker
  version, online state, viewport, pixel ratio, theme, locale, and whether the page runs installed.
- **The panel** (lazy-loaded): description, type, the captured context shown in full before sending,
  and a screenshot by paste or upload. **Annotation** works on any image: box, arrow, pen, text, blur,
  crop, undo, retake; blur is burned in before upload; the original never leaves the browser.
  Touch-sized controls for the kiosk.
- **Failed requests are matched to their `Error Log`** by user, method and a few seconds' window.
- **Retention:** a daily, idempotent job deletes a request's screenshots and context file 180 days
  after it reaches a terminal state (see "Decided with the item").
- **Kiosk drafts** made offline live in their own IndexedDB store keyed to the user, with a time
  limit, dropped on logout or a user change — not in Cache Storage, which both service workers clear.
- **The screenshot spike** (two days, ends the slice): rasterize the visible viewport with a vendored,
  lazy-loaded DOM-capture library on six fixed screens (a Desk form, a Desk list, the Desk learn page,
  `/kiosk`, `/feedback`, `/stock-scan`). DOM capture has failed here once already
  (`public/js/gantt_widget/gantt_export.js:8-23`). Ship automatic capture only if it passes; otherwise
  paste and upload remain the method. Maps, Stripe frames and video render blank and that is accepted.

### Slice 3 — Repo-aware breakdown [M]

- `product_feedback/code_anchors.py`: from a request's context, pick the `www/` controller for a
  route; the doctype JSON (fields, types, options, permlevel > 0 flagged) and the controller's function
  outline for a doctype; the module README; the recent `CHANGELOG.md` sections that name them. Read
  from the installed app. Capped at about 40,000 characters; deterministic, no search index.
- The Triton payload keeps the doctype and the path without its query string, and **drops the
  document name** (`breakdown.py:314-335` sends it today).
- Payload v2 to Triton (Triton release in Preconditions).
- Measure prompt size and cost before and after on the next ten requests.

### Slice 4 — Claude Code brief and the status return [M]

- A reviewer-only endpoint and a Desk button that render a confirmed request as a brief: Markdown in
  the work-item shape (Why, Scope, Acceptance criteria, NOT in scope) plus a JSON block with the Task
  ids, the anchors and any accepted design notes by element code. **Not** a Triton assistant tool.
- The changelog convention `Refs: ER-…, TASK-…`, documented in the `release-prep` skill.
- An hourly, idempotent `release_sync` that reads the installed `CHANGELOG.md`, acts only on sections
  at or below the version `Installed Applications` records (written only when a migrate completes),
  and calls a new `task_writer.mark_shipped` for each referenced Task.
- `mark_shipped` acts only on Tasks carrying `custom_enhancement_request`, only from `Open`, `Working`
  or `Overdue`; it moves them to `Pending Review`, sets `review_date` to 14 days out, and comments with
  the version. It never touches `Completed`, `Canceled`, `Invoiced`, `Template` or a Task already in
  `Pending Review`. With those rules, replaying the whole history is harmless, so the last release
  processed is kept on the settings Single, named so that an absent row means "process everything".

### Slice 5 — Design Review [L]

- **Spike first (two days):** render stored, sanitized concept HTML in a frame with
  `sandbox="allow-same-origin"` and never `allow-scripts`, and prove the pin overlay and click-through
  still work across the frame boundary. The prototype (the Training Concept Viewer,
  https://claude.ai/artifact/HampNeHhPEPKYVVN2FMvFb) clones each screen into a scaled div in the same
  document, with no iframe, so measuring pins across the boundary is the spike's real question. The
  generator and viewer move into the repo as scripts.
- **A new `Design Review` module** with: `Design Review` (the round, a participant table of System
  Users, lifecycle by Workflow), `Design Option`, `Design Screen`, `Design Part` (append-only element
  codes, enforced on import), `Design Note` (code, text, the Employee who raised it, the participant who
  typed it, status Open / Accepted / Rejected / Done), `Design Vote` (ranked, per participant per
  track), `Design Verdict` (yes / maybe / no per screen per participant), `Design Decision`.
  `Design Vote`, `Design Verdict` and `Design Note` grant no create or write permission to any role;
  only the whitelisted endpoints write them, after the participant check.
- **Promotion:** a human System Manager promotes a Decision and picks the target board(s);
  `file_request` files an Enhancement Request with `source = Design Review`, created `Approved` (ADR
  0016 §2), and promotion enqueues the breakdown. The endpoint refuses service accounts (`triton@`).
  `EnhancementRequest.validate` gains its first insert-time rule: new requests are `Submitted` unless
  that condition holds.
- **Import the five artifact-era reviews** — options, screens, codes and notes. Their ballots, which
  include proxy votes, are stored as imported records with the artifact URL and the original voter as
  text, shown apart from live votes and exempt from the participant check.

## Acceptance criteria

- **Slice 1:** `SELECT COUNT(*) FROM tabTask WHERE IFNULL(custom_enhancement_request,'') != ''` = 37
  after the patch. The one-writer test fails when a `frappe.new_doc("Task")` is added to
  `product_feedback/` (reintroduce and revert, as ADR 0010 did). With the gate on: an MCP
  `create_document` of a `Task` produces an `AI Pending Action`, not a Task; an MCP `update_document`
  setting a Task to `Working` executes; one setting it to `Completed` produces an `AI Pending Action`;
  an MCP `create_document` of a `Comment` executes.
- **Slice 2:** a report filed from a Desk form at `/desk/<doctype>/<name>` arrives with `context_url`,
  the doctype, the name and the unsaved flag filled; a report filed after forcing a 500 carries that
  request with its status and exception type, its console error, and its Error Log where one matches.
  Loading `/pay`, `/contract-sign` and `/wall` fetches no capture code (check the network panel). A
  blurred region is unrecoverable in the uploaded file. The eleventh submission in a minute from one
  user is refused. A request closed 181 days ago has no screenshot or context file left and still has
  its text; one closed 179 days ago keeps both.
- **Slice 3:** for a request filed from a doctype form, the payload's anchors contain that doctype's
  field list and controller outline, the permlevel > 0 fields are flagged, the payload stays under the
  cap, and it contains no document name. Triton's rendered prompt no longer contains "nothing else in
  these directories" for a capped listing (pinned by a Triton test).
- **Slice 4:** a release whose changelog carries `Refs: TASK-…` for a feedback Task moves it to
  `Pending Review` with a future `review_date` within an hour of a completed deploy, and never to
  `Completed`; running the sync twice changes nothing the second time; a deploy's `FLUSHDB` does not
  lose the transition; a `Completed` Task referenced by an old release stays `Completed` after a full
  replay; a `Refs:` naming a Task without `custom_enhancement_request` changes nothing.
- **Slice 5:** a user without Desk access cannot open a review; a System User not on the participant
  list cannot vote or note, through the page or `/api/resource`; importing a revision that renumbers an
  existing part is refused; a promoted decision produces an Enhancement Request in `Approved` with the
  promoter as `decided_by` and its breakdown enqueued; `triton@` cannot promote.

## Rollback

Each slice is independent. The Help item is declared `"is_standard": 1`, so deleting it from
`standard_help_items` removes it on the next migrate; the recorder is removed with the capture bundle
and the allowlisted templates' includes. With those two changes the widget is off, with no data patch.
`release_sync` is a scheduler entry. The gate is a single setting. New fields and doctypes remain in
place when disabled; removing them later is the two-step deletion in `fixtures/README.md`.

## Explicitly NOT in this work item

- **Session replay** (rrweb or similar): it records every value and keystroke. Rejected.
- **Server-side rendering** of the user's page: it renders a different session, not their screen.
- **A search or embedding index** over the code. Revisit only if the anchors prove insufficient.
- **Moving the breakdown to Claude Code.** Recorded as the alternative in ADR 0016 §3.
- **Sending screenshots, the context file or document names to any model**, Triton or otherwise.
- **Enabling `monitor`** in site config for trace ids. Matching by user, method and time is enough.
- **Creating Tasks for `WI-xxx` files through `task_writer`.** Deferred until slice 4 has run.
- **Guest or proxy voting on live design reviews.**
- **Automatic `Completed`.** A person closes shipped work.

## Decided with the item (Nik, 2026-09-23)

1. **Retention of captured artifacts.** Screenshots and the context file are deleted 180 days after
   the request reaches a terminal state (`Tasks Created`, `Rejected` or `Duplicate`); the request's
   text, decision and Task links stay. A daily job does it, idempotently, in slice 2.
2. **The first capture allowlist.** The Desk (which includes the learner player at `/app/learn`,
   served under `/desk/` in v16),
   `/kiosk`, `/feedback`, `/itinerary` and `/travel_guidelines` — signed-in pages only. `/training`
   was proposed and is left off: its controller sends every System User to the Desk and every guest
   to login, so the Desk entry already covers it. A page joins the list only after it has been
   classified as login-required.

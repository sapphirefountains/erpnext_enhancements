# WI-079: Capture anywhere, Design Review, and a repo-aware task generator

**Phase:** 2   **Type:** APP_CODE   **Size:** L (five slices, M to L)
**Blocked by:** nothing for slice 1 (the gate switched on in v1.525.0); slice 3 needs a Triton release (prompt repair, schema v2)
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
- The gate patch in slice 1 shipped in v1.525.0, with `run_python_code` gated (below).

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
  - **Update, v1.525.0:** the gate switches on (patch `enable_ai_write_gate`). Nik chose
    exemptions: permanent for Comment, ToDo, Sapphire Maintenance Template and Section, Serial No
    and Training Lesson (Maintenance Profile dropped on review: site access codes and geofence), plus time-boxed windows (`exempt_until`) for bulk jobs. `NEVER_EXEMPT` now also covers
    the gate's own records. `run_python_code` stays gated, and AI sessions move their diagnostics
    to `run_database_query`.
  - **Update, v1.524.1:** `confirm_action` is fixed. The redacted values are sealed in a Password
    field and restored at confirm, and the action fails closed if they can't be. This blocker to
    switching the gate on is gone. The same hash is also in `ASST-AUDIT-2026-09-23-00365`, the
    audit record of the query that found it. Nik approved redacting both rows on 2026-09-23.

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

**Status, v1.526.0 (2026-09-23).** Shipped: `file_request` and `submit_capture`; the four
provenance fields plus `terminal_at`; the per-person limit; the `/desk/` parser; the recorder,
page state and `registerCaptureState` (the kiosk registers clock status, queues, last sync and
location permission); the panel with annotation; kiosk offline drafts; the three entry points;
retention; and Error Log matching. Deviations and gaps:

- **Error Log matching is an hourly job, not a "few seconds' window" at filing.** v16 writes a
  5xx's Error Log through `deferred_insert`, flushed every 15 minutes with `owner` = scheduler
  and `creation` = flush time. So it matches on `metadata` user, verb and path inside that
  window, and notes each match as a Comment on the request.
- **The screenshot spike has not been run.** It needs a signed-in browser on the six screens.
  Paste and upload are the method until it passes. Masking permlevel > 0 and Password fields,
  and switching capture off on pay, labor-cost, payments and QuickBooks pages, belong to
  automatic capture and arrive with it.
- The spike's sixth screen, `/stock-scan`, is not on the capture allowlist. It is either added
  (after classification) or swapped for another screen when the spike runs.
- "Point at it" (ADR 0016 §4) and a "Report a problem" button in the Server Error dialog are not
  built.
- **Kiosk drafts are not dropped at sign-out.** The kiosk has no sign-out of its own to hook.
  Another user's drafts are dropped when the next person's kiosk loads the panel (it preloads
  it once idle) or opens it, and never sent as them: the server says who is signed in.
- Drafts are offered at page load on the kiosk only. On the Desk and web pages the panel bundle
  loads on the first open, so a draft saved before a reload is offered there.

### Slice 3 — Repo-aware breakdown [M]

- `product_feedback/code_anchors.py`: from a request's context, pick the `www/` controller for a
  route; the doctype JSON (fields, types, options, permlevel > 0 flagged) and the controller's function
  outline for a doctype; the module README; the recent `CHANGELOG.md` sections that name them. Read
  from the installed app. Capped at about 40,000 characters; deterministic, no search index.
- The Triton payload keeps the doctype and the path without its query string, and **drops the
  document name** (`breakdown.py:314-335` sends it today).
- Payload v2 to Triton (Triton release in Preconditions).
- Measure prompt size and cost before and after on the next ten requests.

**Status, v1.527.0 (2026-09-23).** The ERPNext half shipped. Triton v0.80.0 renders it; until
that deploys, Triton v0.79 ignores the new keys and the prompt is unchanged.

- `product_feedback/code_anchors.py` builds the anchors from the stored `context_url` and
  `context_doctype`. For a web route: the `www/` controller and template (through
  `website_route_rules`), the controller's outline and the `www/README.md` paragraphs that name
  the path. For a doctype: its fields with permlevel > 0 and Password fields listed as
  restricted, the controller outline, this app's `doc_events`, `doctype_js`, class override and
  property setters. Then the module README and the CHANGELOG lines that name any of it. It is
  deterministic, capped at 40,000 characters with each cut named in `truncated`, and a failed
  build sends `{}`.
- The payload is schema 2. It carries `anchors`, no `docname` key, and the path without its query
  string **or its record**: the widget stores `location.pathname`, so `/desk/item/PUMP-001`
  would have carried the name that the dropped field used to. A Desk path keeps its doctype
  slug, a web path its first segment.
- `Enhancement Request.breakdown_stats` records the latest call's payload, anchors, code map
  and prompt size, its tokens, and `attempts`: 1, or 2 when Triton's empty-plan retry ran, in
  which case the tokens are summed across both calls but the prompt size is the first
  attempt's. An older Triton writes `schema` 1 and `null` for `prompt_chars` and `attempts`. It
  is written on the proposal and on either failure after Triton answered. The breakdown's `AI
  Model Usage` row now carries `reference_doctype`/`reference_name`; every earlier row has
  neither, which is the baseline.
- The code map's doctype names now come from each doctype's JSON. Title-casing the folder had
  sent 27 wrong names since v1.321.0 (`Project Scope Of Work`, `Ai Model Usage`), plus four
  override-only folders listed as doctypes. That was harmless until Triton v0.80.0 started
  rendering the list.
- **Measurement, not run yet.** After ten requests have been broken down on v1.527.0 with
  Triton v0.80.0 live, run these two queries. They are read-only, so `run_database_query` can
  run them. Put the time Triton v0.80.0 deployed in place of the placeholder, in site-local
  time, which is how `now_datetime()` writes `u.timestamp`. After:

  ```sql
  SELECT er.name, u.timestamp, u.model,
         JSON_VALUE(er.breakdown_stats, '$.payload_chars')  AS payload_chars,
         JSON_VALUE(er.breakdown_stats, '$.anchors_chars')  AS anchors_chars,
         JSON_VALUE(er.breakdown_stats, '$.codebase_chars') AS codebase_chars,
         JSON_VALUE(er.breakdown_stats, '$.prompt_chars')   AS prompt_chars,
         u.prompt_tokens, u.total_tokens
  FROM `tabAI Model Usage` u
  JOIN `tabEnhancement Request` er ON er.name = u.reference_name
  WHERE u.feature = 'feedback_work_breakdown'
    AND u.reference_doctype = 'Enhancement Request'
    AND u.timestamp >= '<Triton v0.80.0 deploy time>'
    AND JSON_VALUE(er.breakdown_stats, '$.schema') = 2
    AND JSON_VALUE(er.breakdown_stats, '$.attempts') = 1
    AND u.timestamp = (
          SELECT MAX(u2.timestamp)
          FROM `tabAI Model Usage` u2
          WHERE u2.feature = 'feedback_work_breakdown'
            AND u2.reference_doctype = 'Enhancement Request'
            AND u2.reference_name = er.name
        )
  ORDER BY u.timestamp
  LIMIT 10;
  ```

  Every filter sits inside the query, ahead of `ORDER BY` and `LIMIT`, so the ten rows are ten
  schema-2 calls rather than the first ten rows, most of which would have been Triton v0.79
  calls made between the two deploys. `breakdown_stats` is per request, not per call: a re-run
  overwrites it. So only each request's latest usage row is kept, the `MAX(timestamp)`
  subquery, because that is the one call the stored sizes describe. An earlier call on the
  same request would otherwise show the latest call's sizes beside its own tokens. A retried
  breakdown (`attempts` 2) is left out, because its tokens are two calls' worth against one
  prompt's size. To see how often that happens, run the query again with `= 2` in place of
  `= 1`. Run it soon after the tenth request: a request re-run later shows only its newer call,
  which can push it past the first ten.

  Before, the last ten from the releases before v1.527.0:

  ```sql
  SELECT timestamp, model, prompt_tokens, total_tokens
  FROM `tabAI Model Usage`
  WHERE feature = 'feedback_work_breakdown'
    AND IFNULL(reference_name, '') = ''
  ORDER BY timestamp DESC
  LIMIT 10;
  ```

  Compare the average `prompt_tokens` and `total_tokens` of the two sets. Only the after set
  has a prompt size in characters (`prompt_chars`), because Triton reported none before
  v0.80.0. The before set cannot leave out retried calls the way the after set does, because
  those rows carry no attempt count. A breakdown answered by a Triton older than v0.80.0 after
  v1.527.0 deployed has a reference but `schema` 1, so it belongs to neither set, and the
  after query's filters already skip it.

### Slice 4 — Claude Code brief and the status return [M]

- A reviewer-only endpoint and a Desk button that render a confirmed request as a brief: Markdown in
  the work-item shape (Why, Scope, Acceptance criteria, Explicitly NOT in this work item) plus a JSON block with the Task
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

**Status, v1.529.0 (2026-09-24).** Shipped: the brief, its endpoint and Desk button, the `Refs:`
convention, `mark_shipped` and the hourly `release_sync`. Deviations and gaps:

- **The brief works from Tasks, not from a status.** `api.feedback.claude_code_brief` needs
  `created_task` rows or Tasks back-linked through `custom_enhancement_request`. A confirm that
  partly failed leaves real Tasks while the request stays in `Breakdown Ready`, and those get a
  brief too. The Desk button shows for `Tasks Created`, or when a proposal row has
  `created_task`.
- **The brief's Refs line names the request and its open leaf Tasks, not the group Tasks.** A
  group is a container. It stays open for a person to close once its children are reviewed. A
  `Completed`, `Canceled`, `Cancelled` or `Invoiced` leaf is marked in Scope and left off the
  line and the acceptance boxes.
- **The brief prints the Refs line bare, in a fenced block of its own**, with a note to paste it
  without backticks or bold. The parser ignores a backticked or bolded line on purpose, so shown
  inline in a code span the line would have been pasted that way and silently ignored.
- **The Refs parser is strict, and a test holds the real CHANGELOG to it.** `Refs:` at the start
  of its line after at most three spaces and a list marker; never inside a fence (either kind,
  closed only by the same character at least as long) or an HTML comment. The CHANGELOG
  documents the convention with examples, and its example Task ids are real Tasks on production
  (checked 2026-09-24), so
  `test_feedback_release_sync` fails the build on a Refs line naming a Task unless its
  `SHIPPED_REFS` lists the release. A release that really ships feedback Tasks adds its line
  there in the same change.
- **Only `TASK-…` ids move anything; an `ER-…` id is a cross-check.** A release that ships part
  of a request moves only the Tasks it names, and when the line names a request, a Task of any
  other request is skipped, so a mistyped Task id cannot move a neighbor's work. A Task id that
  does not exist is skipped, never failed, so a typo cannot hold the marker. Ids have five or
  more digits: production's request counter already runs to six (`ER-2026-458194`).
- **`mark_shipped` also skips a Task that already has this version's shipped comment.** A
  replay after a failed run cannot undo a person reopening a marked Task.
- **`mark_shipped` skips an `Overdue` Task a person had closed.** See the last bullet: when the
  newest `Version` that changed `status` set `Canceled`, `Cancelled`, `Invoiced`, `Completed`
  or `Template`, the `Overdue` is ERPNext's flip, not real work, and the result is
  `skipped:overdue after <status>`. The flip is a `db_set`, which writes no Version on
  `version-16`.
- **The 500 cap counts save attempts, not skips.** Counting skips let one Task that could never
  save starve every release behind it: each hour replayed the same 499 skips and stopped at the
  same place. The marker moves to the installed version after a clean run, and otherwise to the
  last release finished before the first failure. A release naming more than 500 Tasks finishes
  over two runs, and a run that is both capped and failing says so in its Error Log.
- **Design notes are `[]` in the brief's Data block.** Slice 5 fills them.
- **The `/feedback` SPA has no "Copy brief" link.** It has no clipboard or notification helper,
  so the Desk button is the one way in. `test_feedback_endpoint_surface` records the exemption.
- **Still to see on production.** The acceptance criteria need a release that carries a real
  `Refs: TASK-…` for a feedback Task. v1.529.0's own line is `Refs: WI-079`, which moves
  nothing. The first hourly run after the deploy replays the whole CHANGELOG (the marker is
  absent), finds no Task ids, and moves the marker to 1.529.0.
- Noticed while reading ERPNext `version-16`, guarded here and not fixed at the root:
  `set_tasks_as_overdue` and `Task.update_status` exempt only `Cancelled` and `Completed`, so
  this site's `Canceled` and `Invoiced` Tasks whose expected end has passed are flipped to
  `Overdue` daily. The brief and `mark_shipped` now guard the feedback pipeline against it (see
  their bullets above), but every other `Canceled` Task on the site is still flipped. The lasting
  fix is a `Canceled`/`Invoiced` guard in this app's Task override, a separate change. Not
  checked against production.

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

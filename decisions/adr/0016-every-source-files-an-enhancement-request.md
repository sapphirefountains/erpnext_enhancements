# 0016. Every source files an Enhancement Request; the breakdown reads the real files; shipped work reports back

- **Status:** Accepted
- **Date:** 2026-09-23
- **Amends:** [0010](0010-employee-feedback-to-tasks.md)

## Context

[ADR 0010](0010-employee-feedback-to-tasks.md) built one route from "I noticed this" to "it's
on the board": an employee files an `Enhancement Request` at `/feedback`, a System Manager
approves it, Triton proposes a work breakdown, the reviewer confirms, and
`product_feedback/task_writer.py` — the only module allowed to — writes the `Task`s.

On 2026-09-23 Nikolas asked for two more ways in, a planner that reads the code, and for the far
end of the route to be closed:

1. **A capture widget on every surface** — the Desk, the PWAs, the SPA pages and the other web
   pages — that takes a screenshot the user can annotate, snapshots the page state, and records
   console errors and failed network requests, so a report can be filed from the screen where
   the problem is.
2. **A generic Design Review module.** Five concept reviews ran between 2026-09-19 and
   2026-09-21 (Training, Desk, UI concepts, Estimating and Billing, Email and Print), each as a
   set of bespoke claude.ai artifacts; the four with ballots or note viewers keep their votes and
   notes outside ERPNext. Voting and commenting are to be **gated to ERPNext users**; people
   without an ERPNext login do not vote at all. Approved notes and decisions go to the same task
   generator as Enhancement Requests.
3. **A breakdown that genuinely looks at this repository.**
4. **A hand-off that Claude Code can execute**, with status coming back.

Measured on production, read-only, 2026-09-23:

| Fact | Value |
|---|---|
| Enhancement Requests since the first on 2026-08-17 | 16, from 5 requesters |
| Of those, with a captured page (`context_url`, `context_doctype`, `context_docname`) | **0 of 16** |
| Tasks the pipeline created | 37 — 30 leaf Tasks recorded in `created_task`, plus 7 group Tasks that are not — of which 33 are Completed |
| Of those, on the Triton board `PRJ-00755` | 0 |
| Human System Managers who review requests | 1 (the other enabled holder besides Administrator is the `triton@` service account) |

Two findings shaped the decision more than the requests did.

**The captured page has never been recorded.** `context_user_agent` and `context_app_version` (a
build token) are filled on every request; the three page fields are empty on all 16. `/feedback`
reads the page the user came from out of `document.referrer` (`public/js/feedback/app.js:71-78`),
and nothing in the app links to `/feedback`, so the referrer is empty or foreign. Even a Desk
referrer would not help: `public/js/feedback/context.js:43` parses only `/app/…` paths, and the v16
Desk is served at `/desk/…`.

**The breakdown sees names, not code.** `product_feedback/codemap.py` sends Triton the module map,
the file names in five package directories capped at 60 per directory (`MAX_FILES_PER_DIR`,
line 60), and the first 6,000 characters of the `CLAUDE.md` conventions
(`MAX_CONVENTION_CHARS`, line 61). No file contents, and no count of what the cap left out. Triton
then renders each listing under the heading "These exist; nothing else in these directories does"
(`triton/backend/app/core/work_breakdown.py:204`), which is false whenever the cap was hit —
`patches/` alone has over 200 files — and drops the doctype list and environment facts that
ERPNext sent (lines 178-228). A model told that a listing is complete will not propose a file
that is missing from it.

There is also a side door. [ADR 0014](0014-ai-write-gating-decides-per-call.md) records that
`ai_write_gating_enabled` was **0** on production on 2026-09-17. With the gate off, any MCP client
can `create_document` a `Task` with no human confirming it. ADR 0010's rule holds for the feedback
feature's own code and is asserted by a test; it does not hold for the platform. The traffic the
gate would see is not small: in the 30 days to 2026-09-23 the connector made 232 Task creates,
419 Task updates, 214 Comment creates and 1,595 `run_python_code` calls, all by one user
(`tabAssistant Audit Log`).

## Decision

### 1. One way in: every source files an Enhancement Request

The body of `api/feedback.py:submit_request` moves into an internal
`file_request(values, requested_by, source, source_ref)`. The `/feedback` form, the capture
widget and Design Review promotion all call it. The Enhancement Request gains:

- `source` — `Feedback form`, `Capture` or `Design Review`. A **normal** doctype's column default
  reaches existing rows through the `ALTER` (see `CLAUDE.md`), so the 16 existing requests read
  `Feedback form` without a patch.
- `source_doctype` (Link → DocType) and `source_ref` (Dynamic Link on `source_doctype`) — back to
  what it came from (a Design Decision, a Design Note).
- `context_release` — the deployed release, as distinct from `context_app_version`, which keeps its
  existing meaning.

The page state, error list and request list are **not** a field: they are a private JSON File
attached to the request, like the screenshot, so nothing that reads the request's fields — including
Triton's bulk sync, which does not exclude this module — carries them. `source`, `source_doctype`,
`source_ref` and `context_release` join `_FROZEN_FIELDS` (`enhancement_request.py:42-50`) and are
never accepted from client input.

**No new statuses.** The confirm step is unchanged. `task_writer.py` gains the request back-link
and `mark_shipped` (§5), the lifecycle gains one insert rule for Design Review (§2), and the
breakdown gains anchors (§3).

A new `Task` field, `custom_enhancement_request`, links every Task the writer creates — groups
included, so `_ensure_groups` stamps it too — back to its request. The 37 existing Tasks are
backfilled: the 30 leaves from `Enhancement Request Proposed Task.created_task`, the 7 groups from
the "Raised from ER-…" line `_origin_note` writes into their description.

### 2. Design Review is a Desk module, and it files requests rather than Tasks

Design Review is a new `Design Review` module that lives in the Desk, so the Desk's own refusal of
Guests and Website Users is the first gate, and the endpoints check again. Each review has a
participant list of System Users; only a participant may vote, cast a verdict or write a note.
`Design Vote`, `Design Verdict` and `Design Note` grant no create or write permission to any role —
they are written only by the whitelisted endpoints after the participant check, so the REST API
cannot go around it. Every vote, verdict and note is stamped with the session user. There is no
proxy voting and no anonymous author: the artifact ballots needed proxy voting because the company
has no Claude accounts, and everyone who votes here has an ERPNext login.

One distinction is deliberate. A **Design Note** may record the Employee who *raised* it — someone
in the meeting who need not have a login — while the participant who typed it is the author of
record. Votes and verdicts have no such field.

A **Design Decision** ("C3 for authoring, with notes L3-S04-E05 and C3-S04-E02") is promoted by a
System Manager, and promotion calls `file_request` with `source = Design Review`. Because a System
Manager is exactly who approves requests, **a promoted decision is filed already `Approved`**, with
`decided_by` and `decided_at` set to the promoter, and the promoter picks the target board(s) at
promotion as at approval. Promotion enqueues the breakdown itself; the hourly sweeper is the
fallback, not the path. The promote endpoint refuses service accounts, `triton@` included, which
also holds System Manager.

Today `EnhancementRequest.validate` checks nothing on insert (`enhancement_request.py:54-57`), so the
rule is new: a new request must be `Submitted`, unless `source == "Design Review"` and the session
user is a human System Manager, when it may be `Approved`. `states.py`'s transition table and
`is_legal` are unchanged.

Concept frames are model-written HTML rendered inside the Desk's origin, where a System Manager
session lives. They are sanitized on import — no `<script>`, no event-handler attributes, no
external URLs except the font allowlist — and rendered in a frame with `sandbox="allow-same-origin"`
(the page must read the frame's layout to place pins) and **never** with `allow-scripts` alongside
it, a pair that lets a frame lift its own sandbox.

Element codes (`L3-S04-E05`) are **append-only**, enforced on import: a part's number is never
reassigned, so a note pinned in one revision still points at the same part in the next.

The five artifact-era reviews are imported for their options, screens, codes and notes. Their
ballots, which include proxy votes, are kept as imported records carrying the artifact URL and the
original voter as text, shown apart from live votes and exempt from the participant check.

### 3. The breakdown reads the files the report is about; Claude Code does the building

Triton stays the planner. Three changes make it see the code without widening what it sees of the
business:

- **Repair the frame.** ERPNext sends each capped listing with its total, and Triton renders it as
  "first N of M"; Triton also renders the doctype list and environment facts it currently drops.
  ERPNext sends every gotcha headline from `CLAUDE.md` rather than a 6,000-character prefix.
- **Send anchors.** A new `product_feedback/code_anchors.py` picks the files the request is
  *about*, deterministically, from its context — the `www/` controller for a route, the doctype
  JSON (fields, types, options, with permlevel > 0 fields flagged) and the controller's function
  outline for a doctype, the module README, and the recent `CHANGELOG.md` sections that mention
  them — read from the installed app, which is a checkout of `main`
  (`infra/cloudbuild-deploy.yaml:36`). Capped at about 40,000 characters. No GitHub token, no
  search index, no new dependency.
- **Stop sending the document.** Today `breakdown.py` forwards the context URL, doctype, docname
  and app version (`breakdown.py:314-335`). It keeps the doctype and the path without its query
  string, and stops sending the document name — customer names appear in document names, and the
  widget is about to start filling that field.

Deep reading of the code is Claude Code's job, because that is where the work already happens.
Moving the breakdown itself into Claude Code was considered and deferred until the anchors' cost
and quality are measured.

### 4. What the capture widget collects, and where it may go

Recording starts when the page loads, so a report carries what went wrong before the user decided
to report it. A report holds:

- **Page state** — the path (and on the Desk the route and query string; on web pages the query
  string is never read, because token pages carry secrets there), the page title, the last ten
  routes; on a form the doctype, name, `docstatus`, the unsaved flag and the *names* of changed
  fields; on a list or report the active filters; for an SPA or PWA whatever state it registers
  through a small hook (the kiosk: clock status, offline queue length, last sync, location
  permission); the service-worker version, online state and device facts.
- **Console errors, uncaught errors and unhandled rejections** — the last 20, collapsed when
  repeated, with message text scrubbed of email addresses and tokens.
- **Failed and slow network requests** — method, path with tokens removed, status, duration and the
  server's exception type. **Never request or response bodies.** Each is matched to its `Error Log`
  by user, method and time. Frappe's `trace_id` would make that exact, but it is written only when
  `monitor` is enabled in site config, and production has written none since 2026-07-17.
- **A screenshot the user can annotate** — box, arrow, pen, text, blur and crop, plus "point at it",
  which records the element the user tapped. Blur is burned into the image before upload; the
  unblurred original never leaves the browser.

Everything is shown to the user before it is sent.

**Captured data stays in ERPNext.** Screenshots and the context file are private Files on the
request and are never sent to Triton or any model. The breakdown receives the requester's text, the
doctype and path, and the anchors ERPNext chose from them, which are code, not user data. The Claude
Code brief names files and quotes accepted notes; it never embeds a screenshot.

The widget mounts on an **allowlist** of pages, not a denylist, and submission requires a System
User on the server; the `system_user` cookie outlives the session and is only a hint. The recorder
ships in the Desk bundle and, for web pages, in its own bundle that **only allowlisted templates
include** — never in `web_include_js`, which Frappe emits on every website page, `/pay` and
`/contract-sign` included. Every page not on the allowlist, and in particular every guest, token and
customer page, loads neither. Before any image is made, controls at permlevel > 0 and Password
fields are masked, and screenshots are off by default on pages showing pay, labor cost, payments or
QuickBooks data.

### 5. Shipped work reports back through the changelog, never to Completed

A changelog entry that closes feedback work carries a line `Refs: ER-…, TASK-…`. An hourly,
idempotent `release_sync` reads the installed `CHANGELOG.md` and acts only on sections at or below
the version `bench migrate` last recorded in `Installed Applications`, which Frappe writes only when
a migrate completes (`frappe/migrate.py:198`). The file on disk alone is not proof: the deploy resets
the checkout before migrate runs (`infra/cloudbuild-deploy.yaml:36`), and a migrate that aborts
leaves the new file behind — the v1.395.0 half-install in `CLAUDE.md`.

For each referenced Task, a new `task_writer.mark_shipped` moves it to the native `Pending Review`
status, sets `review_date` (ERPNext's daily `set_tasks_as_overdue` otherwise flips a Pending Review
Task whose expected end has passed to `Overdue`), and comments with the version. It acts only on
Tasks that carry `custom_enhancement_request`, only from `Open`, `Working` or `Overdue`, and never
touches `Completed` or `Canceled` — so replaying the whole history is harmless. A person sets
`Completed`.

A confirmed request also yields a **Claude Code brief**: Markdown in the work-item shape plus a JSON
block, citing the real Task ids and the accepted notes by element code. It is served by a
reviewer-only endpoint and a Desk button, and is deliberately **not** a Triton assistant tool —
those appear in every employee's tool list and cost context on every chat.

### 6. The side door closes, scoped to what it is for

The AI write gate is turned on in production, scoped so that it stops the actions this ADR cares
about without turning a month of routine work into a few thousand Desk confirmations:

- `create_document` of a `Task` waits for a human, for every AI client, including the owner's own
  Claude Code sessions.
- `update_document` of a `Task` executes, unless the new status is `Completed` or `Canceled`, which
  waits. This is a `PER_CALL_GATED` decider over the call's arguments
  ([ADR 0014](0014-ai-write-gating-decides-per-call.md)'s mechanism); it returns "needs a human" for
  every other doctype, so it narrows nothing else.
- `Comment` becomes an exempt doctype. A timeline note changes no state.
- Everything else behaves as ADR 0006 intends: a write waits for a human.

Two things are **not** decided here and must be before the patch runs. First, how
`run_python_code` is treated: it is on the gate's high-risk list, and 1,595 calls in 30 days would
each become a confirmation; ungating it is defensible only if its sandbox is verified read-only.
Second, the exemption mechanism must never be used for `Task`: `_gated_execute`'s exemption step
(`name in EXEMPTABLE_TOOLS and doctype in _exempt_doctypes()`) applies one exemption to
`create_document` and `update_document` alike, so exempting `Task` would ungate creation too.

**Decided 2026-09-23, when the gate was switched on (v1.525.0).** Both open questions are
settled:
- `run_python_code` stays gated. Its sandbox is arbitrary code, not a read.
- Task is never exempt, enforced by `NEVER_EXEMPT`.

Nik chose to carry the remaining load with exemptions. Because the 30-day inventory showed that
nearly all non-Task volume was one-day bulk work, there are two kinds:
- **Permanent** exemptions for the low-risk records assistants write: Comment, ToDo, the maintenance
  checklists (Sapphire Maintenance Template and Section), Serial No and Training Lesson. Sapphire
  Maintenance Profile was proposed as catalog data and dropped on review: it holds a site's access
  codes, the Time Kiosk geofence coordinates and the default technician.
- **Time-boxed windows** (`exempt_until`) that a person opens on the settings page for a bulk job,
  and that close by themselves.

`NEVER_EXEMPT` grows to the gate's own records: its settings, the exemption table, AI Pending
Action and AI Action Log. That way no assistant can open its own window, alter a card after it was
read, or edit its audit trail. Money, stock, contracts, permissions, Items and Item Prices stay
behind a card.

The gate covers MCP tool calls only. Writes over REST — Triton's
`/api/v1/integrations/erpnext/{doctype}` route, or any API-key call — never pass through it. ADR
0014 already says the gate is the human-in-the-loop layer, not the authorization.

## Consequences

- **`task_writer.py` gains `mark_shipped`, and is the only code in this app that changes a feedback
  Task's status automatically.** People, AI clients through the gate, and ERPNext's own overdue job
  still change it. The one-writer test grows with it: it also matches `frappe.new_doc("Task")`, which
  the current `"doctype": "Task"` pattern cannot see, and it scans the Design Review module and the
  capture endpoints. Task creation elsewhere in the app for human-driven reasons (for example
  corrective actions in `hr_enhancements/safety.py`) is outside this rule and stays so.
- **The breakdown prompt grows.** Anchors are capped, and the prompt size is measured before and
  after; if the cost or the quality disappoints, moving the breakdown to Claude Code is the recorded
  alternative, not a larger cap.
- **Screenshot masking is best effort.** It covers form controls. Customer names in document names,
  list columns, print views and dashboards remain visible, which is why the data may not leave ERPNext
  rather than relying on masking to make it safe to send.
- **A new intake needs a rate limit.** `submit_request` has none today, and every submission emails
  the reviewer; one-click filing makes that matter. Frappe's `@rate_limit` keys by IP or by a value the
  client supplies, so the per-user limit lives in `file_request`.
- **Turning the gate on changes the owner's workflow.** New Tasks raised over the connector, and
  Tasks closed over it, become AI Pending Actions to confirm in the Desk. That is the point; the scope
  in §6 keeps it to those.
- **Revisit** if request volume outgrows one reviewer — production has one human System Manager — or
  if the anchors prove insufficient: the two conditions under which a search index or a Claude Code
  planner earns its cost.

# 0010. Employees file feedback in ERPNext; Triton proposes the tasks and ERPNext writes them

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

Staff hit ERPNext and Triton bugs and think of improvements every week. Until now the route
from "I noticed this" to "it's on the board" was a Google Chat message and somebody
transcribing it by hand.

The two boards are real production Projects, measured on 2026-08-17:

| Project | Name | Open + closed tasks |
|---|---|---|
| `PRJ-00580` | ERPNext Enhancements | 287 |
| `PRJ-00755` | Triton Enhancements | 51 |

Both are curated by one person, and the curation is visible in the records: leaf subjects state
an outcome or a defect, descriptions open with a bolded status line, and PRJ-00755's `notes`
field spells the methodology out. That is the constraint the design has to respect from both
sides at once. Letting employees write to the boards directly destroys the curation. Requiring
a human to transcribe every request keeps the bottleneck that already exists.

So the split is: employees file freely, **nothing reaches a Project without an approval**, and
the tedious half — turning a paragraph of complaint into properly shaped tasks — is done by a
model and then *reviewed* before it is written.

Nikolas settled the product questions before any code: the SPA lives in ERPNext at `/feedback`;
anybody signed in may file; `System Manager` approves; the approver is told by desk bell and
email; the AI's breakdown is **reviewed before it writes**; the model proposes which board(s)
a request targets and the reviewer can override; likely duplicates against existing tasks are
flagged; the requester sees their own list, the resulting tasks, and a notification on the
decision; the form captures an attachment, the requester's impact assessment, auto-captured
page/browser/version context, and steps-to-reproduce for bugs; and it ships live rather than
dormant.

## Decision

### 1. ERPNext writes every `Task`. Triton only proposes them.

Triton already has three ways to write to ERPNext — `FrappeClient.create_doc`, the
`/api/v1/integrations/erpnext/{doctype}` route, and the `fac_bulk_create_documents` MCP tool,
whose own description names `Task` as an example. Using any of them would have been fewer
moving parts.

It would also put a model's mistakes directly onto the two boards this company plans all of
its engineering on, at a moment when `TASK-2026-01583` is open on shrinking exactly that
write surface (the Triton service account holds 90 roles including System Manager).

The shape adopted instead is the one [`api/training_ai.py`](../../erpnext_enhancements/api/training_ai.py)
established for quiz drafting, and its docstring already argues it: the drafting call
**persists nothing**, and the accept call *is* the human review — it is what writes rows and
stamps a named person against a model's output.

`tests/test_feedback_endpoint_surface.py` asserts this structurally rather than in prose: only
`product_feedback/task_writer.py` may construct a `Task`, with a control asserting that module
still does. (`x not in source` is true of every `x`, including in a repository where the
feature was deleted.) Verified by reintroducing the bug.

**Do not add a write path to Triton's planning endpoint.** It is the whole decision.

### 2. A task names a `target`, never a Project.

Each proposed task carries `target` — the string `"erpnext"` or `"triton"` — and ERPNext maps
that to a Project id from its own settings. The model never sees or emits a project id.

This is a security property, not a convention. The prompt reasons over prose an employee
typed; a model that could emit `"project": "PRJ-00123"` could write work onto a live customer
job. With this shape the worst a prompt injection in a bug report achieves is naming a target
that is not in the map, and the row is dropped.

The same rule covers `parent_task` and every duplicate candidate: both must appear in the
open-task list ERPNext sent. A model asked to cite a task id will invent a plausible one
(`TASK-2026-99999`) rather than admit it has none, and an invented parent silently reparents
nothing while an invented duplicate sends a reviewer looking for a task that does not exist.

A consequence worth stating: the reviewer's "ERPNext only" override is enforced by **narrowing
the enum before generation**, not by filtering afterwards. Filtering would leave them
wondering why half the plan vanished.

### 3. The lifecycle is a `status` field with a transition table, not a Frappe Workflow.

[ADR 0002](0002-native-first.md) makes a custom mechanism a defect where a native one
suffices, so this deviation is recorded rather than assumed.

A Frappe Workflow transition is *a human action gated by a role*. Three of this machine's
transitions have no human behind them — `Approved -> Breakdown Ready` and
`Approved -> Breakdown Failed` are written by a background worker — and the terminal
`Breakdown Ready -> Tasks Created` is an accept-the-edited-proposal call rather than a
`docstatus` bump. Expressing that as a Workflow means either a second status field for the
machine states or transitions no human ever performs.

`docstatus` is wrong for the same reason
[`chat_export_request.py`](../../erpnext_enhancements/chat/doctype/chat_export_request/chat_export_request.py)
gives: *a governance record that can be cancelled is a governance record with an undo button.*

The table lives in `product_feedback/states.py`, which is stdlib-only so it sits in the
bench-free CI tier, and `tests/test_feedback_states.py` pins the enum against the DocType's own
`Select` options and enumerates the whole transition cross-product.

### 4. Native-first check

Verified against production via MCP on 2026-08-17.

| Native candidate | Verdict |
|---|---|
| **ERPNext `Issue`** | **Rejected.** One record on prod (`ISS-2025-00002`, July 2025, customer-linked). It is customer-shaped: Support Settings, SLA and response-time machinery, agent groups, the email inbox. It would still need ~10 custom fields, and every internal bug report would land in the customer support queue. |
| **Frappe `Workflow`** | **Rejected for the lifecycle.** See §3. Prod has 3 Workflows, 1 active. |
| **Frappe `Notification` fixture** | **Rejected.** `tests/test_notification_recipients.py` pins that recipients must be group addresses rather than roles; there is no dev group address, and the audience is a short list of named people. |
| **`Task` + `parent_task`** | **Adopted.** No hierarchy invented; the Task class override already promotes a parent to `is_group = 1`. |
| **ToDo assignment**, **`Notification Log`** | **Adopted** as-is. |

### 5. Durability is the status field, not a second queue table

The production deploy `FLUSHDB`s the queue redis, so an ordinary successful deploy destroys
every job that was enqueued and had not yet run — silently, because `enqueue` already returned
success. `Chat Relay Job` exists for that reason.

This feature deliberately does not get one. **The status is the outbox:** a request sitting in
`Approved` with no proposal *is* a lost job, it is visible in the review queue, and an hourly
sweeper re-drives it. A durable outbox row is for work a human believes already happened
(a message they think they sent); this is work a human is waiting on and can also re-drive
with a button.

`deduplicate=True` is not used. It drops the new enqueue while an existing job is QUEUED **or
STARTED**, so a running job would swallow exactly the re-run meant to replace it.

### 6. The kill switch is named `paused`, not `enabled`

A brand-new Single doctype has no rows in `tabSingles` until something saves it, so every
`get_single_value` answers `None` on the day it ships. An `enabled` field would therefore have
shipped this dead on arrival — the trap `CLAUDE.md` records from the Chat Settings incident,
arriving from the other direction. Naming the switch for the *off* state makes the absent-row
state the running state.

`patches/seed_product_feedback_settings.py` writes the row anyway, so the two Project ids are
visible and editable in the desk rather than being constants a reader has to find in Python —
but `get_settings()` applies every fallback itself and does not depend on that patch having
run. A feature whose correctness needs a patch to have succeeded is a feature that ships broken
on any site where it did not.

## Consequences

- **A new module, `Product Feedback`**, with five DocTypes and a portal SPA at `/feedback`.
  `hooks.py` gains a `website_route_rules` entry and an hourly sweeper.
- **Triton gains one route**, `POST /api/v1/planning/work-breakdown` (Triton `0.70.0`), which
  returns schema-forced JSON and writes nothing. Its ownership row is in Triton's
  `docs/convergence.md`.
- **The task-shaping doctrine is now shared**, in Triton's `core/task_doctrine.py`, between the
  `triton_sdlc_planner` agent and the new endpoint. They want different output formats and must
  not disagree about what a good task is.
- **`Product Feedback` is excluded from `global_triton_sync`.** An `Enhancement Request` is
  saved several times during one breakdown and the thing doing the breaking down is Triton, so
  each save would be a webhook about a conversation Triton is already in.
- **Until Triton `0.70.0` deploys, approving a request lands it in `Breakdown Failed`** with a
  legible reason and a re-run button. The feature degrades to a triage queue rather than
  breaking.
- **The requester never gets write permission on their own request.** They hold `read` with
  `if_owner`. Write would let them move `status`, and `Submitted -> Approved` is a *legal*
  transition — the table would wave a self-approval through. Attachments are therefore linked
  server-side after an ownership check, rather than uploaded onto the request.

## Open

- **Nothing here decides whether an approval card belongs in Google Chat.**
  [ADR 0009](0009-erpnext-google-chat-triton.md) §I.12 carries that as an unresolved question
  for Nikolas and this record does not pick a branch. The chat module also ships dormant on
  every site, which is why the notification path here is bell + email.

# `ai_governance/` — human-in-the-loop for AI writes, and the Triton contract

The DocTypes behind the AI write-confirmation gate, plus the Triton assistant's settings,
allowlist, and the inbound call-routing rules the Triton voice gateway reads. The gate's *logic* lives in [`../assistant_tools/`](../assistant_tools/README.md);
this module holds the records it creates and the configuration it reads.

## What the gate does

When **ERPNext Enhancements Settings → AI Governance → Require Confirmation for AI Writes**
is on, `assistant_tools/_gate.py` wraps
`BaseTool._safe_execute`, the single choke point both Frappe Assistant Core execution paths
converge on. A mutating tool then does not execute: an **AI Pending Action** is recorded, a
desk notification goes out, and the model receives an anti-fabrication envelope telling it
the action has **not** run and how a human confirms it.

The field defaults to **off**. On production the v1.525.0 patch `enable_ai_write_gate` turns it
**on**, after seeding the permanent exemptions Nik chose on 2026-09-23 from the 30-day audit log.

## A write that cannot run gets no card (v1.533.0)

A card spends a person's attention, so the gate refuses a `create_document` or `update_document`
before queueing it when one of its Select values isn't an option, which Frappe would refuse on
save, or when its DocType doesn't exist. On 2026-09-24 an assistant queued 21 cards that each set
a Task's status to "Cancelled". This site's option is "Canceled". All 21 were confirmed in one
batch, and all 21 failed on execution.

`_gate._precheck_refusal` checks each Select value in the call's `data` against the DocType meta,
child-table rows included. It uses Frappe v16's own comparison from `_validate_selects`: the value
is stripped and the options aren't, and `naming_series` and empty values are skipped. The error
message uses Frappe's wording, shows a credential-like field's value as `***REDACTED***`, and
lists at most five problems. When a value is refused:

- the model gets the error back at once, with error type `AIGateValidationError`;
- the refusal is recorded in AI Action Log as a failed, not-queued row;
- no AI Pending Action is created.

What it deliberately doesn't do:

- **It runs no validation code.** Calling `doc.validate()` or FAC's `validate_only` would run
  controller hooks that send email and enqueue jobs for a write nobody has confirmed.
- **It doesn't check Link targets.** A card that moves Items into a new Item Group can depend on
  an earlier card that creates the group, and the group doesn't exist until that card is
  confirmed. Select options change only through a schema write (a Property Setter or Custom
  Field). A model that queues "add this option" must wait for that confirmation before it can use
  the new option.
- **It never blocks a write by failing.** If the check itself raises, the card is queued exactly
  as before and the failure goes to the Error Log.

Where it can't be sure, it queues the card:

- A Select with `fetch_from` and no `fetch_if_empty` is skipped, because Frappe overwrites it from
  the linked record before it validates.
- A cancel (`docstatus` 2) is skipped, because Frappe skips `_validate()` on cancel.
- An `update_document` row marked `_delete` with a `name` is skipped, because FAC removes it
  without validating it. Every other row is checked.

**Known gap:** the check sees the proposed value, not what a controller might change it to.
Frappe runs the controller's `validate` before `_validate()`, so a controller that rewrote an
off-options value into a valid one would get past Frappe but be refused here. The refusal lists
the valid options, so the model can send one of them.

## Exemptions: permanent, or a window that closes itself

A row in **Confirmation-Exempt Doctypes** lets an assistant's `create_document` and
`update_document` on that doctype execute without a card. It is still logged in AI Action Log as
Auto Approved. Delete, submit, workflow and code execution are never exempt. That includes a
submit dressed as a create or update: `create_document` with `submit`, or `update_document`
with `docstatus` in its data, always goes to a card (`_changes_docstatus`).

- **Permanent** means **Exempt Until** is empty. The patch seeds Comment, ToDo, Sapphire
  Maintenance Template and Section (checklists and their instructions), Serial No and Training
  Lesson: low-risk records that assistants actually write. Sapphire Maintenance Profile is not
  on the list, because it holds a site's access codes, the Time Kiosk geofence coordinates and
  the default technician. Money, stock, contracts, permissions, Items and Item Prices stay
  gated. Item stays gated because creating one with a `standard_rate` also writes an Item Price.
- **A window** means **Exempt Until** holds a time. This is how a bulk job is carried, for example
  "Item until 18:00" for an inventory session. Without a window, 328 Items would mean 328 cards.
  The window closes by itself: `_exempt_doctypes()` compares it with the current time on every
  call, in site-local time on both sides. A window that can't be read counts as closed. Delete
  the row afterwards to keep the table tidy.
- **Only a person can open one.** An assistant would have to update the settings or the exemption
  table, and both are in `NEVER_EXEMPT`, so that update is itself a card. AI Pending Action and AI
  Action Log are in `NEVER_EXEMPT` too. Otherwise an assistant could rewrite a card's arguments
  after someone had read it, or edit its own audit trail. Task is there because exempting it would
  ungate Task creation along with its updates (ADR 0016 §6). Knowledge Article and Knowledge Article
  Version are there since v1.538.0 (WI-080, ADR 0017): company knowledge is published only by a
  person approving someone else's draft. The Version doctype is also on the gate's denylist, so a
  generic tool cannot reach it at all.

## Confirmation is desk-only, on purpose

There is deliberately **no MCP-exposed confirm tool**. A model-callable confirm would reduce
the human-in-the-loop guarantee to a convention that prompt injection walks straight through.
Confirm and Cancel happen only through the whitelisted endpoints in
`assistant_tools/gating_api.py`, which the `AI Pending Action` form's buttons call by dotted
path. The model can only *read* the outcome afterwards, via `check_ai_pending_action`.

Do not add a confirm tool. Do not relax the direct-status-edit block on `AI Pending Action` —
blocking desk status edits is what keeps the lifecycle honest.

## Deciding in batches (v1.528.0)

The AI Pending Action list has a **Review My Pending (N)** button, shown only when N is above
zero, and two actions-menu items for ticked rows, **Confirm & Execute Selected** and **Cancel
Selected** (`ai_pending_action_list.js`). The button recounts on full list refreshes and on the
list's realtime updates, and its page-menu twin (the only entry point on a phone) keeps the same
count. They call three endpoints in `gating_api`:

- `my_pending_actions` lists your own Pending, unexpired actions, oldest first, at most 100. It
  says whether each has hidden values without reading or decrypting them. A row starts unticked
  (`batch_default` false, with a `review_reason`) for High risk, hidden values, a submit or cancel
  (`_gate._changes_docstatus`), a write to one of `_gate.NEVER_EXEMPT`, or unreadable arguments.
  For a never-exempt target the reason names its kind (`gating_api._never_exempt_reason`): a Task,
  the gate's own records, or since v1.538.0 the company knowledge base.
  It reads the redacted `arguments` for that and never returns them.
- `confirm_actions` / `cancel_actions` take up to 50 names. The actions run oldest first, through
  the same `_confirm_one` / `_cancel_one` the form's buttons use. An action that is not yours, not
  Pending or expired is skipped with the reason. A failure is rolled back and recorded and the
  batch carries on. Messages are muted per action, so the outcome is one results table.

**A batch is not atomic.** `_confirm_one` commits Confirmed before it runs the tool, so a worker
killed mid-tool leaves that action Confirmed with its write rolled back, and nothing re-drives or
fails it. So a request starts no new action after `BATCH_TIME_BUDGET_SECONDS` (45) and returns the
rest as Skipped, and the list sends one action per request, oldest first, stopping at the first
request that fails or runs out of time. The dialog ticks at most 50 and refuses more.

**All six `gating_api` endpoints are POST-only and refuse a request with an `Authorization`
header** (`_require_desk_session`). A GET skips Frappe's CSRF check, so a link to `confirm_action`
in an assistant reply used to run the action on one click. And an OAuth client holding the
user's token, such as an MCP client, must never decide its own proposals.

**A batch only covers your own actions, even for a System Manager.** On a single card
`_check_identity` lets a System Manager decide anybody's action. That is the exception, for a card
that has to be decided while its requester is away, and it is decided after reading it. A
confirmed action runs as the person who confirms it, so bulk-confirming someone else's queue would
run every proposal in it with System Manager rights, and any of those proposals could have been
planted by a prompt injection in that person's session. Another person's action is skipped
without its tool or summary being read out, and a System Manager can still decide it from its form.

Every batch asks a second time and says how many actions will run and how many of them are
high-risk. The batch path adds no MCP tool and nothing to FAC's configuration. It is the same
desk-only door with a wider frame, and the door now checks that it is the desk.

## Confirming runs what was proposed, not what the card shows

`AI Pending Action.arguments` is the copy people read, and credential-like keys in it show as
`***REDACTED***`. The key test is FAC's name heuristic, and it catches ordinary data too: its
substring `auth` matches `author`, and its token rule matches `author_training_course`'s
`draft_token`. So the values it replaces are **sealed** in `sealed_arguments`, a hidden Password
field (encrypted in `__Auth`, asterisks in the column). `confirm_action` restores them into the
parsed card and executes that. Until v1.524.1 it executed the card itself, which wrote the
placeholder into real records.

- **The seal lives only while the action is Pending.** The controller clears it on every save
  that leaves it any other status, including the Confirmed transition before execution. Frappe
  runs `validate()` before `_save_passwords()`, so that one save deletes the `__Auth` row.
  Deleting the document removes it too.
- **It must stay a Password field.** The doctype has `track_changes`, so every save writes the
  old and new values into `tabVersion`. A Password column only ever holds asterisks. Any other
  fieldtype would copy the secret into Version on the save that clears it.
- **It fails closed.** If the seal is missing, undecryptable, or disagrees with the card, or if
  a placeholder is still sitting under a credential-like key after restoring, the action goes to
  **Failed** without running. Executing is not an option in that state: it would write the
  placeholder.
- **The confirmed call's result and error are masked** before they reach this doctype, AI Action
  Log, or the thrown message, because a validation message can quote the value it rejected. Only
  sealed **strings** of six or more characters are masked. Shorter ones and non-string values are
  not, because masking replaces every occurrence. FAC's own **Assistant Audit Log** row for the
  call is masked too. `_gate._wrap_log_execution` gives FAC recursively redacted arguments while
  `frappe.flags.ai_gate_sealed` is set, because FAC's own sanitizer only looks at top-level keys.
  FAC's file log and Sentry are out of reach and receive FAC's raw error text on a failure.
- **The person confirming can read what they approve.** The Confirm dialog lists the hidden
  fields by path. **Show Hidden Values** calls `gating_api.reveal_sealed`, which is limited to the
  requester or a System Manager, while Pending and unexpired. It leaves a comment each time. Most
  of what the heuristic hides is ordinary data, and an injected value there would otherwise go
  through unseen.
- **`args_hash` is an HMAC** keyed by the site encryption key. It is computed over the raw
  arguments, and the rest of the hashed text sits in `arguments`, so a plain hash of a short
  password could be brute-forced by anyone who can read the row.
- **Locals in the confirm path are named `secret_*`.** Frappe's 5xx Error Log snapshot prints
  frame locals, and it blanks only names on its blocklist.

## DocTypes

| DocType | Role |
|---|---|
| `AI Pending Action` | A proposed AI mutation awaiting human confirmation. Created by the gate; transitions only via `gating_api`. Direct status edits in the desk are blocked. `sealed_arguments` holds the redacted values while Pending (see above). The list view decides your own actions in batches (see above) |
| `AI Action Log` | Append-only record of AI actions |
| `AI Model Usage` | Model usage accounting |
| `AI Confirmation Exempt Doctype` | Doctypes exempted from the confirmation requirement: permanent rows, or time-boxed windows via `exempt_until` (see above) |
| `Triton Settings` | Single — connection settings for the Triton assistant |
| `Triton Assistant Settings` | Assistant behaviour configuration |
| `Triton Allowed User` | Per-user access to the assistant |
| `Triton Chat Attachment` | Anchor row for one file attached to a Triton chat turn. Owner-scoped (`permissions.py`, wired under both `permission_query_conditions` and `has_permission`). It exists so the uploaded `File` has something to be attached *to*: an orphan private File is readable only through `File.has_permission`'s `doc.owner == user` shortcut — an accident rather than a policy — and nothing in core ever garbage-collects one. Attached here, the file inherits this row's hook and is deleted with it. Behaviour lives in [`triton_attachments.py`](../triton_attachments.py) |
| `Training Insight` | Captured insights for assistant tuning |
| `Call Routing Settings` | Single — the fallback inbound calls land on when no rule matches: default forward number, ring duration, Holiday List, voicemail wording. Its kill switch is `paused`, not `enabled`, for the never-saved-Single reason in `CLAUDE.md` |
| `Call Routing Rule` | One "when a call looks like this, ring these people" clause. Ordered by `priority`, **first match wins**. Conditions: menu selection, known/unknown/matching caller, days and times |
| `Call Routing Target` | Child table — one Employee, softphone user, account manager, or voicemail box on a rule |

## Inbound call routing

Configured here, executed by Triton. A System Manager writes rules in the desk; the gateway
matches each incoming call against them and builds the `<Dial>`.

| File | Role |
|---|---|
| [`call_routing_match.py`](call_routing_match.py) | The decision, as a pure function. **Standard library only** |
| [`call_routing.py`](call_routing.py) | The Frappe binding: read the records, compile them into dial legs, explain the result |
| [`../api/telephony.py`](../api/README.md) | `get_telephony_routing` carries the compiled payload; `preview_call_routing` backs the "Test Routing" button |
| [`../tests/data/call_routing_vectors.json`](../tests/data/call_routing_vectors.json) | Shared test vectors, committed identically in Triton |
| `doctype/*/…py` | `on_update` / `on_trash` ping the gateway so an edit lands now, not within 60s |

**Why the decision is made over there.** Triton owns the Twilio webhook, and it already
fetches `get_telephony_routing` on a 60-second cache with a prefetch that fires while the
caller is still listening to the phone menu — built that way so the dial decision never
waits on us. Making ERPNext decide per call would put a blocking HTTP request inside a live
call, which is the failure Triton's own `CLAUDE.md` records as having frozen its event loop.
So we compile and it matches.

**Why the compile step is not laziness.** Resolving Employees to E.164 numbers here means
the gateway needs no lookup of its own, *and* it means an unreachable target is reported
while somebody is editing the rule instead of being discovered as a phone that did not ring.
That matters more than it sounds: on 2026-09-11, **2 of 20** Employee records had a
`cell_number` at all, and those stored it as bare digits. `compile_rules` surfaces every
dropped target in the payload, the form, and the preview.

**Edits reach the gateway twice over.** Saving `Call Routing Settings` or a
`Call Routing Rule` (or deleting one) enqueues a ping to Triton's `/refresh-settings`, which
drops its cached payload so the change takes effect immediately rather than within the
60-second TTL. It reuses `Triton Settings`' own `trigger_refresh_webhook` — one
implementation of "ping the gateway" — but reads the secret inside the worker rather than
passing it as a `frappe.enqueue` kwarg, because those are serialised into redis. The ping is
best-effort: it is suppressed during migrate/install/patch/import/test, and if it is lost
(worker down, or a deploy's `FLUSHDB` eating the queued job) the cache expiry is the backstop.
A settings page must never refuse to save because a gateway is unreachable.

**The cost, and the defence.** The matcher exists in two repos that deploy independently, so
both load the same vectors file. Change behaviour on one side without the other and the
other side's CI goes red — which is the only cheap alarm for a bug whose symptom is a phone
that does not ring, for one caller in ten, noticed weeks later.

Three behaviours worth knowing before editing rules:

- **A matching rule is authoritative.** If it were additive no rule could ever *narrow*
  anything. The safety valve is per-rule — `also_ring_softphones` defaults on, so a rule
  written without thinking about it still rings the desk.
- **No match means today's behaviour**, not silence: every softphone plus the default
  forward number. Same for `paused`, an empty rule set, a payload from a newer ERPNext, and
  anything that raises. A configuration mistake must never send every caller to voicemail.
- **A time window that ends earlier than it starts wraps past midnight**, and the day filter
  still applies to the day the call arrives. "Weekdays 17:00-08:00" covers Monday-Friday
  evenings *and* Monday-Friday early mornings; it does not stretch Friday evening into
  Saturday.

## FAC tool categories (`fac_tool_categories.py`)

Frappe Assistant Core keeps one `FAC Tool Configuration` row per tool, and since FAC 2.5.0 the
row's **category** is merged *over* the tool's own MCP annotations in `tools/list`. FAC seeds
every external tool as `read_write` (→ `readOnlyHint: false`), so all of this app's read tools
were advertised to every MCP client as writes. `fac_tool_categories.py` writes the category
that reproduces each tool's declared annotations — `read_only`, `write`, or `privileged` for
the `HIGH_RISK` tools — with `category_override` set, so FAC's own re-detection leaves it alone.

It runs twice over, because this app is installed *before* FAC and so our `after_migrate`
runs before FAC's creates the row for a new tool: a `before_insert` doc_event on
`FAC Tool Configuration` stamps a row as FAC creates it, and `sync_fac_tool_categories`
(`after_migrate`) repairs the rows that exist. The category shown on FAC's admin page for these
tools is therefore **managed by the app** — change `assistant_tools/_gate.py`, not the page; a
change made on the page is undone at the next migrate. Neither entry point imports
`assistant_tools` (tools are resolved from the hook by dotted path, as FAC does), both are
inert without FAC, and neither can raise.

## Relationship to Triton

Triton confirmation-gates writes in its own chat UI (`PendingAction` + `IntegrationAuditLog`);
this module gates Frappe Assistant Core tool execution at the MCP layer. **There is no
overlap and both stay** — they cover different entry points into the same data. See Triton's
`docs/convergence.md`.

FAC 3.0.0's in-Desk **FAC Chat** (off on prod; a paid FAC Cloud service) is a third entry
point but not a third path: its cloud runtime calls this site's `handle_mcp` endpoint as the
user, so its tool calls pass through the same gate. Pending actions it causes carry the chat
conversation id as `session_id` and `fac-chat` as `client_id`.

## Tests

```bash
python -m unittest \
  erpnext_enhancements.tests.test_ai_gate_unit \
  erpnext_enhancements.tests.test_ai_gating_integration -v
```

`test_ai_gate_unit` is bench-free and runs in CI — it is the guard on a security boundary, so
keep it green and keep it in the CI list. The FAC category sync is covered by
`TestFacToolCategorySync` in `test_assistant_tools_schema` (same CI step), which also asserts
that FAC's category hints, merged over each tool's annotations, change nothing.

```bash
python -m unittest \
  erpnext_enhancements.tests.test_call_routing \
  erpnext_enhancements.tests.test_call_routing_gateway_notify -v
```

`test_call_routing` is bench-free **and stub-free**, because `call_routing_match` imports
nothing but the standard library. If it ever needs a `frappe` stub, something frappe-shaped
has leaked into the matcher and belongs in `call_routing.py` instead.
`test_call_routing_gateway_notify` does stub `frappe` — which is why the two have separate
CI steps, and why the first one can stay clean.

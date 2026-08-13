# Chat

An ERPNext-owned employee chat system that mirrors bidirectionally to **Google Chat**.
Messages live here, in ERPNext DocTypes, and sync *out* to Google Chat; Google Chat is
transport and a companion client, never the master copy. A coworker with the native Chat
app open and a coworker in ERPNext are in the same conversation.

The relay authors each message **as the real person**, via domain-wide delegation — not as
a bot with a name attached. That single decision shapes most of this package; see
[CQ-1](#cq-1-human-attribution-and-what-it-costs) below.

> **Why, not what.** This README describes what the code does. Every *decision* — the data
> model, the sync protocol, the notification truth table, the Triton budget — lives in
> [`decisions/adr/0009-erpnext-google-chat-triton.md`](../../decisions/adr/0009-erpnext-google-chat-triton.md)
> (§F data model, §G sync, §H notifications, §I Triton, §J infrastructure). The
> file-by-file build order is
> [Appendix B](../../decisions/adr/0009-appendix-b-implementation-plan.md).
>
> **Phase 5 diverged from both in six places, deliberately.** The ADR is immutable, so what
> Phase 5 decided — and every place the build contradicts the plan, with the reason — is in
> [Addendum 1](../../decisions/adr/0009-addendum-1-phase-5-decisions.md). Read it before
> "fixing" anything under `retrieval/`, `indexing/` or `invoke/` back towards the plan.

---

## Phase status — read this before you go looking for something

**Phases 1, 2 and 3 are complete: schema, auth, transport, the bidirectional sync engine,
and — new in Phase 3 — the read/write HTTP surface and the chat application itself.** There
is now a chat window: a SPA at `/chat`, and a coworker surface inside the floating Triton
bubble on every Desk page.

| | State |
|---|---|
| DocType schema, indexes, `Chat Settings`, roles | **built** (Phase 1) |
| Keyless domain-wide-delegation auth (`gchat/auth.py`) | **built** (Phase 1) |
| The one Google Chat transport client (`gchat/client.py`) | **built** (Phase 1) |
| JWT-verified inbound webhook (`gchat/webhook.py`) | **built** (Phase 1) |
| Permission hooks, deep links, feature-flag endpoint | **built** (Phase 1) |
| Transactional outbox + `seq` allocation (`sync/outbox.py`) | **built** (Phase 2) |
| Relay worker, state machine, Redis token bucket, sweeper, kill switches | **built** (Phase 2) |
| Pub/Sub puller, inbound ingest, the echo ladder, edit/delete propagation | **built** (Phase 2) |
| Space provisioning (3 modes), membership sync, attachments both ways | **built** (Phase 2) |
| Subscription lifecycle, reconciliation sweep, `health.py`, the Phase 4/5 seams | **built** (Phase 2) |
| The read/write HTTP surface (`chat/api/`) — rooms, history, threads, search, compose | **built** (Phase 3) |
| The SPA at `/chat`, deep links, virtualised transcript, composer, attachments | **built** (Phase 3) |
| Presence, typing, read receipts (**ERPNext-side, Redis, no DocType**) | **built** (Phase 3) |
| Dual-surface bubble, bubble→SPA handoff, unread badge | **built** (Phase 3) |
| Inline `[[ref:N]]` citation rendering, feature-degrading to today's behaviour | **built** (Phase 3) |
| Notifications, Web Push, VAPID, the suppression matrix | Phase 4 |
| The MCP denylist — chat is unreadable through the generic AI tools | **built** (Phase 5) |
| The retrieval source scan (one door, `allowed_rooms` first) | **built** (Phase 5) |
| Phase 5 schema — chunk, two digests, invocation log, FULLTEXT index | **built** (Phase 5) |
| The gated retrieval module, ranking, budget ladder, assembly, citations | **built** (Phase 5) |
| One `@triton` handler for both origins, and the Triton client | **built** (Phase 5) |
| The index writer — chunking, embeddings, rolling digests, invalidation | **built** (Phase 5) |
| Citations on the wire, and the `@triton` readiness report | **built** (Phase 5) |
| The bench suite and the ADR addendum | **built** (Phase 5) |
| The live round trip, and the evaluation baseline | Phase 5, remaining — **needs a bench and a human** |
| Export, audit writes, drift reports, pilot rollout | Phase 6 |

**Phase 3 built the entire API surface, not just a UI on top of one.** Phase 2's brief said
"no SPA and no UI of any kind" and was followed literally, so `chat/` exposed six
whitelisted methods and every one was operational — the inbound webhook, attachment
download, relay retry, two provisioning starters, document-room creation. There was no
`get_rooms`, no `get_messages`, no `send_message`. Everything under `chat/api/` is new.

**The SPA does not need Google.** ERPNext is the source of truth and the SPA reads ERPNext,
so the whole application works with chat dormant — `enabled = 0`, no relay, no
subscriptions, no Google credentials in play. Composing a message writes a row and a
`Chat Relay Job` that never leaves `Pending`. Nothing has to be armed to use the UI.

**The system ships dormant, and Phase 2 did not change that.** `Chat Settings.enabled = 0`,
`dry_run_mode = 1`, `restrict_to_whitelist = 1`, `google_sync_enabled = 0`,
`relay_outbound_enabled = 0`, `relay_inbound_enabled = 0` out of the box. With `enabled`
off, `auth.get_delegated_token` refuses to mint a credential — the master switch is
enforced at the *bottom* of the stack as well as the top, so a new caller added later
cannot route around it. **Every scheduler entry Phase 2 registered no-ops while the flags
are off**, which is why they are registered now rather than by hand on go-live day: an
entry added later is an entry somebody forgets.

The three things Phase 1 shipped as *fields with no automation* — provisioning modes
(`Chat Room.provisioning_mode`), relay bookkeeping (`Chat Relay Job`) and inbound
bookkeeping (`Chat Inbound Event`, `Chat Event Subscription`) — are all live now. Two more
tables were added for Phase 2 (`Chat Message Revision`, `Chat Provisioning Run`) and both
are written from day one.

## The shape of it

| Path | What it is |
|---|---|
| `doctype/chat_room/` | DM / group chat / named space. Carries `gchat_space_name` (unique), the `linked_doctype` + `linked_document` pair for per-document spaces, and the four denormalised last-message columns (`last_message`, `last_message_at`, `last_message_sender`, `last_message_preview`) that make the room list a zero-join render. **The only chat DocType with a DocPerm row.** |
| `doctype/chat_message/` | The hot table. `autoname: hash`, `sort_field: creation` (never `modified` — editing an old message must not teleport it to the bottom of the transcript), `track_changes = 0`, no `in_global_search`. Unique on `gchat_message_name`, and on `(room, seq)` and `(room, client_message_id)` via the index patch. |
| `doctype/chat_room_member/` | Membership plus the read high-water mark (`last_read_seq` / `last_read_at`) — one row per `(room, user)`, never one per message. Leaving is **soft**: `is_active = 0` plus `left_seq`. `left_seq` is recorded but **grants nothing today** — see [departed members](#departed-members-left_seq-grants-nothing-yet). |
| `doctype/chat_mention/` | Child table of `Chat Message`. Small and bounded, which is the only shape a child table is right for. |
| `doctype/chat_attachment/` | One row per attachment, with `source` splitting `Uploaded` from `Drive Link` (the third option is `ERPNext`). Those are the literal Select values and the `SOURCE_*` constants in `chat_attachment.py` — Google's own `UPLOADED_CONTENT` / `DRIVE_FILE` are the *inbound* spelling and are translated by `GOOGLE_SOURCE_MAP`, so compare against the constants, never the Google names. That split is a permission decision wearing a data-model costume — see [attachments](#attachments-is_private--1-always). |
| `doctype/chat_relay_job/` | The outbox. One row per pending write to Google, `unique(room, job_seq)`, drained in `job_seq` order. `status` is the state machine; `lease_expires_at` is simultaneously the in-flight claim the inbound pipeline reads and the crashed-worker detector the sweeper reads. **This table, not the queue, is the delivery guarantee** — see [scheduler entries](#scheduler_events--the-only-delivery-guarantee-there-is). |
| `doctype/chat_inbound_event/` | Raw inbound events, unique on `pubsub_message_id` — that index is what turns Pub/Sub's at-least-once delivery into a no-op instead of a duplicate. Written and committed **before** the ack, so the only window left open produces a duplicate rather than a loss. `defer_count` / `available_at` carry the `DEFER` budget. |
| `doctype/chat_event_subscription/` | Workspace Events subscription bookkeeping — `expire_time` (read off Google's response, never computed from a constant), `renew_after`, `state`, `event_count`, `last_event_at`, failure counters. One row per coworker (shape B). An expired subscription is permanently **deleted** by Google and cannot be renewed, which is why its expiry is tracked in a row rather than assumed. |
| `doctype/chat_message_revision/` | **New in Phase 2.** The edit/delete audit trail §4.F requires and the ADR never named. `unique(message, revision_no)` by patch, `text_before` / `text_after`, `change_type`, `actor`, `origin`, `origin_timestamp`. **Zero DocPerm, tighter than `Chat Message` itself** — this is where superseded and deleted content lives, so it is reachable only through the oversight role. |
| `doctype/chat_provisioning_run/` | **New in Phase 2.** The checkpoint row that makes a bulk org sweep resumable rather than restartable: `mode`, `dry_run` (defaults **on**), `status`, `cursor`, the four counts, `log`. Zero DocPerm. One run is one mode, because a run meaning "departments then teams" could not be resumed without re-deriving where the boundary fell. |
| `doctype/chat_context_chunk/` | **New in Phase 5.** The semantic index: a run of consecutive messages in **one** room, sealed at a boundary, with one embedding. `body` holds the messages **verbatim**, so this is not a derived artefact needing lighter handling — it is the transcript, pre-assembled into prose, and it is treated exactly like `Chat Message`. A chunk never spans rooms, and that is the permission boundary rather than a chunking heuristic: the gate filters candidates on `room` before a vector is loaded, so a two-room chunk is a chunk that *cannot be filtered*. `unique(room, first_seq)` by patch, `(room, last_seq)` for the bounded candidate scan, and a raw-DDL **FULLTEXT** index on `body` that is the whole lexical tier. |
| `doctype/chat_room_digest/`, `doctype/chat_thread_digest/` | **New in Phase 5.** Rolling summaries, one per room and one per long thread. The docname **is** the room / the thread root, so concurrent generation is a failed insert rather than two summaries that disagree. Both carry the **three-value watermark** (`watermark_seq`, `watermark_count`, `watermark_modified`) — see [the watermark](#the-three-value-watermark-and-why-one-value-is-a-privacy-bug). `poisoned` is deliberately separate from `is_stale`: "nobody has rebuilt this yet" and "this cannot be rebuilt" need different answers from an operator. |
| `doctype/triton_invocation_log/` | **New in Phase 5.** One row per `@triton` turn: tokens, cache-hit tokens, candidate counts, citation misses, four timings. **Instrumentation, not audit** — every write is best-effort and a failure is swallowed, which is the *opposite* posture from `Chat Retrieval Audit`. An audit that fails open is not an audit; instrumentation that fails closed is an outage caused by a metric. `request_id` is derived from the triggering mention and unique, so a redelivered interaction event produces one turn rather than two answers. |
| `sync/states.py` | **Pure.** The one relay-job transition table, its projection onto `Chat Message.sync_state`, and the jitter-free `available_at` delay. `assert_transition` is the only gate on a status write — **no bare `db_set` on either field anywhere.** There is deliberately no `Retrying` state: a transient failure returns to `Pending` with `attempts` incremented. |
| `sync/decisions.py` | **Pure, and the heart of it.** `classify_inbound()` — the whole echo ladder as a total function — plus `parse_pubsub_envelope()`, the idempotency keys, and the bounded fallback heuristic that ships disabled. Names no Google host, so the guardrail test stays true. |
| `sync/budget.py` | **Pure.** The 32,000-byte fit. Truncates on a codepoint boundary and reserves room for the deep-link suffix inside the limit. |
| `sync/ratelimit.py` | Pure GCRA arithmetic, then a Redis deployment of exactly that arithmetic (`SpaceRateLimiter`, `ProjectQuota`). The Lua is printed under the function it mirrors so the two can be read side by side. Membership writes and `spaces.setup` **do not** go through the space bucket. |
| `sync/outbox.py` | The write path — invariant I1. `seq` allocation under a row lock, `client_message_id` derivation, preview/text_plain denormalisation, and the `Chat Relay Job` row written **in the same transaction** as the message. The only thing the insert path knows about Google. |
| `sync/outbound.py` | The relay worker: claim, lease, drain a room as a strict FIFO at one write/second, transition, retry, dead-letter. Owns the circuit breaker, `sweep_relay_jobs`, and the operator's `retry_relay_job`. |
| `sync/inbound.py` | One raw event → gather facts → `classify_inbound` → apply. Re-implements no rule. Owns Rule 3's timezone-explicit conflict resolution, late-arrival and skew instrumentation, and `sweep_stuck_inbound_events`. |
| `sync/pubsub.py` | The bounded synchronous pull, driven by a one-minute cron — see [why a cron](#why-the-pubsub-puller-is-a-cron-and-not-a-daemon). Write the row, **commit**, ack, then enqueue. |
| `sync/provisioning.py` | The three §4.H modes: lazy on first message, resumable org batch, user-initiated document rooms. Nothing here is automatic and nothing runs on install. |
| `sync/membership.py` | Membership both ways, keyed on **membership resource names**, never on email — a `spaces.members.list` under user auth returns `users/{opaque id}` and nothing matchable. A live membership ERPNext cannot name is *reported*, never removed. `max_reverts_per_hour` is what stops two systems fighting forever. |
| `sync/attachments.py` | Attachments both ways, shaped by one verified asymmetry: **a Chat app cannot upload** (`chat.bot` is absent from `media.upload`'s scopes) but it **can** download. Bytes are uploaded; a private ERPNext URL never is. Owns the `download` endpoint, which is the only byte path a room member has. |
| `sync/subscriptions.py` | The component whose failure is silent, total and permanent: one `spaces/-` subscription per coworker, renewed off the `expireTime` Google actually granted. |
| `sync/reconcile.py` | The sweep that turns a missed renewal from **data loss** into **lag** — `spaces.messages.list` with a `createTime` filter, ingested through the same idempotent inbound path, with a genuine two-layer Pub/Sub envelope so there is no second ingest to get subtly wrong. |
| `testing/fake_chat.py` | An in-memory Google Chat, injected as `GoogleChatClient(transport=…)` so the tests exercise the real builders, the real retry loop and the real `_request` contract. Enforces the real quotas, returns the real AIP-193 429 shape with **no** `Retry-After`, and injects faults including the event-before-response race. Stdlib only. **Production code with its own tests** — nothing under `sync/` may import it. |
| `testing/fixtures.py` | The byte-shaped event payloads `parse_pubsub_envelope` is tested against. **Every payload is constructed, not captured**, and its docstring marks each field documented / inferred. Read that before trusting a byte. |
| `retrieval/gate.py` | **New in Phase 5.** The **only** module in the app that may query the chat index. `retrieve()` derives the room set from the caller's own membership and has no parameter by which one can be supplied; every private search function takes `allowed_rooms` as a **required first positional**; the filter is in the `WHERE` before any vector loads; the audit row is committed before content is returned; `Administrator` raises. `retrieve_for_oversight()` is a separate function rather than a flag — a boolean is one typo from being `True` — and pays for its exemption with the configured oversight role, a mandatory reason and explicitly named rooms. |
| `retrieval/rank.py`, `budget.py`, `assemble.py`, `lexical.py` | **New in Phase 5. Pure, stdlib only.** RRF hybrid ranking (ranks, never a weighted sum of raw scores — a cosine and a FULLTEXT relevance are not on the same scale); the ceiling and the ordered degradation ladder; S0–S5 assembly with **no clock read above S5**; the BOOLEAN MODE query builder, which strips operators rather than escaping them. |
| `retrieval/vectors.py`, `citations.py` | **New in Phase 5.** The two-method `VectorBackend` adapter over base64 `float32`, with normalisation applied on the way in *and* asserted on the way out; and the citation manifest with **server-side** URL resolution, so no model-authored string ever becomes an `href`. |
| `indexing/` | **New in Phase 5.** The index **writer** — `chunker.py` (pure, five boundary rules), `embed.py` (Vertex AI over `requests`, no SDK), `indexer.py` (the chunk and embedding passes, deliberately separate jobs), `digest.py` (the five-minute batch over a **derived** dirty predicate) and `invalidate.py` (the staleness writer the Phase 2 seam was waiting for). It runs on the scheduler with no session user and reads every room by design, which is exactly why its *output* is governed at the point of consumption: **no whitelisted method anywhere in the package**, every public function named and justified in `tests/test_chat_gate_source_scan.py`, and nothing under `chat/api/` may import it. |
| `invoke/` | **New in Phase 5.** `@triton` from both origins into one handler. The envelope carries **no origin field**, so the handler has nothing to branch on; origin is recorded on `Triton Invocation Log` by the normalisers. Retrieval and tool calls run as the mentioning human; the reply is posted by the bot. Acknowledge and enqueue, never answer inline — Google's interaction deadline is a hard 30 seconds. |
| `invoke/triton_link.py` | **New in Phase 5.** Credentials the **bot** inside Triton, which cannot be done the way a human does it. Triton builds its ERPNext client for the turn's identity *eagerly*, before the model runs, so an identity it cannot call ERPNext back as fails every turn with `401 erpnext_link_required` — and `triton@sapphirefountains.com` is a Google **group**, so the browser OAuth flow that fixes that for a person has no session to run in and never will. Triton's documented API-key fallback is used instead, written over `PUT /api/v1/assistant/profile` with a machine-minted bot token. It **never generates the key** (`generate_keys` resets the secret and saves the whole `User`) and never returns or logs one. |
| `seams.py` | `notify_new_message` (Phase 4) and `mark_room_context_stale` (Phase 5) as call sites wired now, plus the Redis-backed counters `health.py` reads. `notify_new_message` firing **exactly once per genuinely new message and zero times for echoes** is the cheapest proof the mirror is not looping. |
| `realtime.py` | The **only** publish in this package — a security module, not a wrapper. Always an explicit `room=`, always `after_commit=True`, and `list_update` / `docinfo_update` are refused with a `ValueError` rather than documented as a hazard. |
| `rollout.py` | **New in Phase 5.** `bench execute`-able: who can use `@triton` and who has not completed the ERPNext OAuth link. It imports `handler.has_erpnext_link` rather than re-implementing the check — a readiness report that disagrees with the code enforcing readiness is worse than none, because it is confidently wrong exactly when somebody trusts it. Not whitelisted, reads one column (`user`), and prints what it *cannot* see: ERPNext is the OAuth provider, so a grant here is authoritative about this side only. |
| `health.py` | `bench execute`-able report, written for somebody at 2am who did not write this. Every number is named, carries its unit, and is judged. **Never raises, never reads message text, and is deliberately not whitelisted.** |
| `gchat/events_client.py` | `workspaceevents.googleapis.com` — a different host, therefore a different module, because the guardrail test confines each Google host to one place. Builders only; execution goes through `GoogleChatClient.execute`, so there is one retry loop and one dry-run short-circuit. |
| `doctype/chat_settings/` | The Single. Identifiers, feature flags, kill switches, quotas, retention, Triton budgets. **No secret-bearing field, ever.** `chat_settings_rules.py` holds the pure validators (budget arithmetic, retention coherence, endpoint URL, secret-material detection) so they can be tested without a bench. |
| `doctype/chat_allowed_user/` | Child of `Chat Settings`; the pilot whitelist `restrict_to_whitelist` reads. |
| `gchat/auth.py` | Keyless domain-wide delegation: build the assertion claim set (pure), sign it with IAM Credentials `signJwt` using the VM's own service account, exchange it for an access token, cache per subject. **No key file exists, on disk or in git.** Also the app-identity token path for Triton. |
| `gchat/client.py` | The **only** module in this codebase that speaks HTTP to `chat.googleapis.com`. Seven methods, one `_request()` choke point, every decision in a pure function above it. |
| `gchat/backoff.py` | `compute_backoff` / `classify_error` / `should_retry` / `parse_retry_after`. Pure, full jitter, retries 429 + 5xx + timeouts and **never** any other 4xx. |
| `gchat/ids.py` | `client_message_id()`, `request_id()` and their checks. This derivation *is* invariant I3. |
| `gchat/dryrun.py` | Deterministic, **visibly fake** synthetic responses (`spaces/DRYRUN-…`) so the whole system runs with no network I/O and Phase 2's reconciliation can detect and skip them. |
| `gchat/webhook.py` | The `allow_guest=True` inbound endpoint. Verifier first, handler second. |
| `gchat/smoke_test.py` | Phase 1's checkpoint gate — eleven steps against real Google Chat. See [the smoke test](#the-smoke-test). |
| `permissions.py` | `permission_query_conditions` and `has_permission` for the four user-facing DocTypes, plus `membership_filter_sql` / `visible_room_names` for raw SQL. Read its module docstring before writing any query here. |
| `links.py` | `build_message_deep_link()` — one function, three consumers (the SPA router, the notification deep link, Triton's citation resolver). Written in Phase 1 precisely so those three do not diverge; `public/js/chat/routes.js` and `tests/test_chat_api_contracts.py` assert the same route table on both sides. |
| `api/_common.py` | **New in Phase 3.** The gate (`require_session` / `require_room` / `require_message`) and the serialisers. **The only place a message body is emitted**, which is what makes "a deleted row cannot leak its text" structural rather than a rule four read paths have to remember. |
| `api/conversations.py` | **New in v1.265.0.** Starting a conversation — the one thing Phase 3 shipped without. `create_direct_message` (idempotent: `unique(dm_user_1, dm_user_2)` plus the controller's canonical sort mean A→B and B→A are one room), `create_group`, and the people picker's `search_people`. **Not a second room creator** — both writes go through Phase 2's already-race-safe `_insert_room_deduped` and `insert_room_member`. |
| `api/rooms.py` | **New in Phase 3.** Room list (zero joins — it renders from the denormalised `last_message_*` columns), room detail, member list, the SPA bootstrap, and the Redis-backed `last_open_room` hint. |
| `api/history.py` | **New in Phase 3.** Transcript paging, thread paging, and the "page containing this message" read a deep link resolves to. **Keyset on `seq`, never `OFFSET`, never a timestamp.** |
| `api/compose.py` | **New in Phase 3.** Send / edit / delete and the upload gate. Goes through `sync.outbox.insert_message` rather than around it, so Phase 2's document events still do all the work and Phase 3 adds no second copy of any of it. Idempotent on `client_message_id`, catching **both** `UniqueValidationError` and `DuplicateEntryError`. |
| `api/search.py` | **New in Phase 3.** Room-scoped and global search. `LIKE` rather than `MATCH` — the column exists, the FULLTEXT index does not — stated explicitly rather than left implicit, with the upgrade path written down. The oversight-role read is the one place `note_privileged_read` fires on a search. |
| `api/mentions.py` | **New in Phase 3.** `@mention` autocomplete, members first. `@triton` is offered in every room. Non-members are *offerable* but not *mentionable* — the write side drops them. |
| `api/readstate.py` | **New in Phase 3.** `mark_read`, the wholesale unread count, per-member read marks, and the `after_insert` counter fan-out that drives the room-list indicator and the bubble badge. |
| `api/presence.py` | **New in Phase 3.** Typing (no database write at all) and presence (**Redis with a TTL, never a DocType**). `focus_state` is the pure multi-tab union Phase 4's suppression matrix will read. |
| `../../www/chat.html` + `chat.py` | **New in Phase 3.** The SPA shell. No hyphen in either filename — `scripts/check_www_controllers.py` guards it. Serves every sub-path of `/chat` via the `website_route_rules` entry in `hooks.py`. |
| `../../public/js/chat/` | **New in Phase 3.** The SPA, as plain DOM modules. **No Vue**, which is how the two-runtime hazard is closed structurally rather than by configuration. |
| `../../public/js/global_enhancements/chat_surface.js` | **New in Phase 3.** The coworker surface inside the floating bubble. Shares the SPA's transport, handoff, optimistic and signal modules, so the two halves cannot disagree about the API shape or the idempotency rule. |
| `../api/chat.py` | The whitelisted feature-flag endpoint: `get_settings_public()`, flags only. The real surface is `chat/api/` — it lives under `chat/` so `tests/test_chat_rawsql_guard.py` scans it. |

## Indentation

**Everything under `erpnext_enhancements/chat/**` is TABS** — Frappe convention, and what
this repo's `ruff format` is configured for (tabs, double quotes, `line-length = 110`).

**The one exception is `erpnext_enhancements/api/chat.py`, which is FOUR SPACES** to match
the majority of its neighbours in `api/`. See [`api/README.md`](../api/README.md) for which
files in that package are tabs.

Match the file you are editing and never normalise one you are only touching. Do not run a
repo-wide `ruff --fix` or `ruff format` — `ruff check` is advisory in CI because of a known
pre-existing backlog, and a drive-by format buries your actual change in thousands of lines.

---

## The raw-SQL review checklist

**`permission_query_conditions` does not protect `frappe.db.sql`.** A query-condition
fragment is appended by `frappe.model.db_query.DatabaseQuery` — that is, by
`frappe.get_list` and by the desk's list and report views, and by *nothing else*. Raw SQL
bypasses the entire permission stack: no DocPerm, no query condition, no `has_permission`.
So does `frappe.get_all`, which is `get_list(ignore_permissions=True)` wearing a friendlier
name.

History paging and search will be written in raw SQL — keyset paging and `MATCH` leave no
choice. **This is the single most likely route to a real data leak in this system.**

Run down this list for **every** query added under `erpnext_enhancements/chat/**`:

- [ ] **Is it `frappe.db.sql`, `frappe.qb`, `frappe.get_all`, or a `db.get_list` with
      `ignore_permissions=True`?** If yes, no hook is protecting it. Continue.
- [ ] **Does it constrain rows to the caller's rooms?** The one correct way is
      `permissions.membership_filter_sql(...)` or `permissions.visible_room_names(...)`.
      A second hand-written membership subquery is a second thing to keep in sync, and the
      copy that drifts is always the one furthest from `permissions.py`.
- [ ] **Is the user value escaped with `frappe.db.escape(user)`?** Always, without
      exception. There is no parameter binding on this seam; escaping is the whole defence.
- [ ] **Does it constrain `Chat Message` to rooms the caller is an *active* member of?**
      Departed members read nothing at all until CQ-10 is answered — see
      [departed members](#departed-members-left_seq-grants-nothing-yet). The rule is
      implemented once, in `permissions._message_scope_sql` / `_may_read_message`; a query
      that hand-rolls a `left_seq` bound re-opens the question in code.
- [ ] **Does it bypass the filter for an admin?** Then it must be gated on the role read
      from `Chat Settings.admin_oversight_role` — never a literal role name, never a
      caller-supplied flag — and it must call `permissions.note_privileged_read(...)`,
      which is the single hook point, and which now **writes** a `Chat Retrieval Audit` row
      rather than returning `None`. If the query is about to return message bodies, call
      `chat.audit.record_or_refuse(...)` instead: it records the rooms and the seq ranges and
      refuses the read if it cannot record it. See [the audit](#the-decision-12-audit).
- [ ] **Does it join to a table this checklist has not considered?** `Chat Attachment` and
      `Chat Mention` reach message content transitively. Filter on the message's room, not
      on the child row.
- [ ] **Would this query survive being run from the desk's report view by a non-member?**
      That is the standing acceptance bar (ADR §9-F): *even a raw report view cannot leak
      another user's rooms.*

Two facts that make the checklist load-bearing rather than ceremonial:

- **Every chat DocType ships with an empty `permissions` array except `Chat Room` and
  `Chat Settings`.** On a DocType with no DocPerm row the permission stack refuses before
  any hook is consulted — so for `Chat Message`, `Chat Room Member` and `Chat Attachment`
  the hook pairs are *defence in depth, not the live gate*. The day somebody adds a
  `System Manager` DocPerm row so they can look at a message in the desk, those hooks
  become the only thing standing up. They are written and tested now for that day.
- **On Frappe v16 a `has_permission` hook that falls off the end returns `None`, which now
  DENIES.** Every path in `permissions.py` returns an explicit boolean, exception paths
  included. The failure direction is safe (a lockout, not a leak) but it is still a
  production outage nobody can debug from the symptom.
- **v16's `get_list` no longer routes through `frappe/model/db_query.py`.** It goes via
  `frappe.model.qb_query` → `frappe/database/query.py`'s `Engine` (the docstring on
  `frappe.get_list` still says otherwise and is stale). Two consequences on this boundary.
  The Engine wraps each hook's condition in **its own parentheses** where the legacy path
  joined with a bare `" and "` — under which `a = 1 or b = 2` combined with `c = 3` reads as
  a **silent widening** — so **write pre-parenthesised fragments regardless of path**;
  Phase 1's are single `exists (...)` expressions and are already safe. And **DocShare beats
  the filter**: after AND-ing every condition the Engine does
  `where_condition |= table.name.isin(shared_docs)`, commented *"shared docs trump all other
  restrictions"*. Zero DocPerm makes that unreachable on `Chat Message` today, but
  `Chat Room` carries a `read` DocPerm — a `DocShare` row on a room widens a read that no
  hook can narrow. `VERIFY:` that with a test.

**Phase 2's own position on this checklist:** the sync engine writes with
`ignore_permissions=True` from background jobs, where there is no session user to filter
against and the membership question has already been answered upstream — the outbox only
ever relays a message that was inserted through the write path, and the inbound pipeline
writes rows a room's own subscription delivered. What it must never do is *read* on a
user's behalf without the filter. `health.py` is the deliberate reference case: all raw
`frappe.db.sql`, all aggregates and identifiers, **no content**, and not whitelisted.

---

## Hook index

Everything this module registers in [`hooks.py`](../hooks.py). `hooks.py` is annotated in
this repo and the annotation is part of the documentation — keep the two in sync, and add a
row here when you add an entry. `tests/test_hooks_integrity.py` rejects duplicate keys and
handlers; `tests/test_hook_targets_resolve.py` rejects a dangling dotted path.

### `permission_query_conditions` — list and report reads

| DocType | Handler | What it does |
|---|---|---|
| `Chat Room` | `chat.permissions.chat_room_query` | Rooms where the caller is an **active** member. Active only: room read is the doc-room join, and a join is evaluated once and never re-checked, so a departed member would otherwise get a live feed. |
| `Chat Room Member` | `chat.permissions.chat_room_member_query` | Member rows for rooms the caller can see. |
| `Chat Message` | `chat.permissions.chat_message_query` | Messages in rooms the caller is an **active** member of. Departed members see nothing — see [departed members](#departed-members-left_seq-grants-nothing-yet). |
| `Chat Attachment` | `chat.permissions.chat_attachment_query` | Attachments whose message is in scope by the same rule. |

### `has_permission` — single-document access

| DocType | Handler | What it does |
|---|---|---|
| `Chat Room` | `chat.permissions.chat_room_has_permission` | **The realtime security boundary (invariant I8).** socket.io's `doc_subscribe` calls back into Python and runs the full document-level permission stack — including this hook, under the joining user's own session — before joining `doc:Chat Room/<room>`. Get it right and socket security is free; get it wrong and realtime leaks message content with every REST endpoint locked down. |
| `Chat Room Member` | `chat.permissions.chat_room_member_has_permission` | Same scope as the query, per document. |
| `Chat Message` | `chat.permissions.chat_message_has_permission` | Same scope as the query, per document. |
| `Chat Attachment` | `chat.permissions.chat_attachment_has_permission` | Same scope as the query, per document. |

Query/`has_permission` **parity is house doctrine** — the register sits at an exact match
today. A new chat DocType that gets one gets both, in the same commit.

**Phase 2 added no permission hook and changed none.** The sync engine writes with
`ignore_permissions=True` from background jobs and reads through `permissions.py`'s helpers
like everything else; `sync/attachments.download` calls
`permissions.chat_attachment_has_permission` **directly** rather than adding a fifth hook,
so there is still exactly one expression of the membership rule.

### `scheduler_events` — the only delivery guarantee there is

Read this before deciding a sweeper is redundant with the queue. **It is the other way
round: the queue is the optimisation and the sweeper is the guarantee.** Three facts, all
verified, and each one alone is enough:

- the production deploy runs `redis-cli -p 11000 FLUSHDB` against the **queue** Redis and
  restarts the whole honcho group, so every queued-but-unrun job is destroyed on an
  ordinary *successful* release — silently, because the enqueue already returned;
- **Frappe v16 wires no RQ retries at all.** It never passes `retry=` and never constructs
  an `rq.Retry`. A worker exception is simply the end of that job;
- a job can be SIGKILLed mid-HTTP-call, so a `finally` block is not cleanup you can rely on
  — which is why the relay's in-flight claim is a **lease on a row**, not a Redis key.

Every entry below no-ops while `Chat Settings.enabled` is 0. All of them live in the `cron`
block of `hooks.py`; `hooks.py` is annotated and that annotation is part of the
documentation, so change the two together.

| Cron | Handler | What it guarantees, and for which direction |
|---|---|---|
| `* * * * *` | `chat.sync.pubsub.pull_inbound_events` | **INBOUND delivery.** Takes a Redis lock, pulls for a bounded slice of the minute, writes and **commits** each `Chat Inbound Event` row before acking Pub/Sub, then enqueues processing. Killed mid-slice by a deploy, the unacked deliveries are simply redelivered. |
| `* * * * *` | `chat.sync.inbound.sweep_stuck_inbound_events` | **INBOUND processing**, and separately the **defer timer**. Re-drives `Received` rows nobody finished. `_defer` cannot schedule a delayed job — `frappe.enqueue` reaches no RQ scheduler and RQ's own lives in the Redis a deploy flushes — so it writes `available_at` and stops, which makes *this interval* the real granularity of a defer. At ten minutes a three-defer budget is a thirty-minute worst case for one message; at one minute it is three. `Failed` rows are **not** re-driven: they carry evidence a human needs. |
| `*/5 * * * *` | `chat.sync.outbound.sweep_relay_jobs` | **OUTBOUND delivery.** Re-drives `Pending` rows past `available_at` and returns `In Progress` rows whose lease expired — a crashed worker — to `Pending`. This is the job that makes a release survivable. |
| `*/10 * * * *` | `chat.sync.provisioning.sweep_pending_provisioning` | **Outbound prerequisite:** a room owed a space. The low-latency path is the write path enqueueing `provision_room_job`, and that enqueue is exactly what a deploy destroys; a room whose only claim on a space was a vanished enqueue would otherwise wait forever, and the symptom is one conversation that silently never mirrors. Also recovers rooms stuck in `Provisioning` by a killed worker. |
| `*/10 * * * *` | `chat.sync.attachments.sweep_pending_attachments` | **Inbound prerequisite:** attachment bytes whose download never finished. Only re-picks rows quiet for the cooldown, so a permanently failing blob is not retried once a minute forever. |
| `25 * * * *` | `chat.sync.subscriptions.renew_due_subscriptions` | **That inbound exists at all** — the highest-consequence entry here. Google *permanently deletes* an expired subscription: there is no expired state, nothing to reactivate, no patch that brings it back. Nothing raises, no job fails, no Error Log row appears; inbound simply stops and the first symptom is a coworker asking three days later why their reply never arrived. Hourly against a lifetime measured in days is deliberate over-frequency — the job is idempotent, and the cost of running it needlessly is one cheap read. |
| `50 * * * *` | `chat.sync.reconcile.reconcile_due_rooms` | **INBOUND catch-up.** Workspace Events has no replay: events delivered while a subscription was lapsed or the puller was down are gone. This asks each stale room directly with `spaces.messages.list` + a `createTime` filter and feeds the results through the *same* idempotent inbound path. It is the difference between "inbound was down for six hours" being a latency incident and being a permanent hole in the record. |
| `30 4 * * *` | `chat.sync.provisioning.sweep_orphaned_document_rooms` | Hygiene: a linked document deleted or cancelled must not leave a Google space nobody owns. |
| `*/10 * * * *` | `chat.indexing.indexer.sweep_chunks` | **THE SEMANTIC INDEX EXISTS AT ALL.** Reads messages past each room's derived watermark and writes sealed chunks. No network I/O. The watermark is `max(last_seq)` over that room's chunks rather than a stored cursor — a cursor is a second source of truth for a fact the rows already state. |
| `*/10 * * * *` | `chat.indexing.indexer.sweep_embeddings` | **A separate job from the one above, on purpose.** Chunking is cheap, local and always correct; embedding is a paid external call that can fail, rate-limit or hang. Fused, an embedding outage stops the index advancing and a room's history silently stops being searchable *at all* rather than only semantically. |
| `*/5 * * * *` | `chat.indexing.digest.sweep_digests` | **The rolling summaries, as a BATCH over a dirty predicate.** Never a per-message enqueue: `deduplicate=True` drops a new enqueue when an existing job is `QUEUED` *or* `STARTED`, so a digest job that is running swallows exactly the messages that made it stale — silently, one `ERROR` line and a `return`, for weeks. Five minutes plus the 15-minute dirty age bounds staleness at ~20 minutes. |
| `35 * * * *` | `chat.indexing.digest.check_digest_staleness` | **The failure being watched for is silence, not an error.** A summariser that has quietly stopped produces no log line, no exception and no complaint, because stale summaries keep answering. `:35` because QuickBooks owns `:00`/`:20`/`:40` and chat sync owns `:25`/`:50`. |

**`:25` and `:50`, not `:20` and `:40`.** Those two keys are QuickBooks' `cdc_poll` and
`retry_failed_syncs`, and **a duplicate key in a Python dict literal does not warn** — the
later entry silently replaces the earlier one. Reusing them would have deleted two live
QuickBooks jobs with nothing reporting it. `tests/test_hooks_integrity.py` exists for
exactly this; pick a free minute and let it prove you did.

### Why the Pub/Sub puller is a cron and not a daemon

ADR §G.4.2 names a long-running streaming-pull worker as the ideal and a bounded
synchronous pull as the shipping-first fallback. **This repo takes the fallback for a
structural reason, not a preference: there is no Procfile here.** The bench generates one
on the VM and systemd runs `honcho start` against it; neither file is under version
control in this repository, so *a supervised worker is not a change this repo is able to
make*. A `scheduler_events` cron entry is.

**The cost is stated rather than hidden: up to a minute of inbound latency** on a message
a coworker sends from the native Chat client. Outbound is unaffected — the write path
enqueues immediately. If a supervisor convention ever lands on the host, the shape to
change is `pubsub.run_pull_cycle`'s bound, not the ingest below it.

### Whitelisted endpoints

| Path | Guest? | What it does |
|---|---|---|
| `erpnext_enhancements.api.chat.get_settings_public` | no | Feature flags as booleans, from a positive allowlist. No identifier, no topic name, no service-account address — those are not secrets, but they are reconnaissance and a browser has no use for them. |
| `erpnext_enhancements.chat.gchat.webhook.handle` | **yes** (`allow_guest=True`, POST only) | Google's inbound interaction events. World-reachable, so the JWT is the only thing between it and an open relay: signature, **issuer `chat@system.gserviceaccount.com`**, audience byte-exact against `Chat Settings.interaction_endpoint_url`, and expiry — all **before** body parsing and before any DB access. Anything else gets `401`. Phase 1's handler logs the event type and returns `200` empty; dispatch is Phase 5's. |
| `erpnext_enhancements.chat.sync.attachments.download` | no | **The** byte path for a chat attachment, and it exists because the obvious one cannot work: `Chat Message` ships zero DocPerm, so `File.has_permission` denies `/private/files/…` to everyone but Administrator — members included. The alternative is a DocPerm on `Chat Message`, which would open the desk's report view onto every message body in the company. Decides with `permissions.chat_attachment_has_permission`, refuses `Guest` first, and returns the **same 403** whether the row is missing, unreadable or empty. |
| `erpnext_enhancements.chat.sync.outbound.retry_relay_job` | no, `System Manager` | Operator "try again". Never calls Google — it writes a status through the same transition table the worker uses and wakes a worker. See [Dead](#dead-is-terminal-and-that-is-a-design-decision). |
| `erpnext_enhancements.chat.sync.provisioning.enroll_org_units` | no | Creates `Chat Room` rows for named org units and performs **zero Google I/O**. This is the per-entity opt-in: enrolling a hundred departments creates zero spaces. |
| `erpnext_enhancements.chat.sync.provisioning.start_org_mirror` | no | Opens a `Chat Provisioning Run`. `dry_run` defaults to **1** and the default is the point — the thing being planned creates spaces in twenty people's clients and cannot be undone without a call this codebase refuses to make. |
| `erpnext_enhancements.chat.sync.provisioning.create_document_room` | no | A per-document room, **user-initiated and registered in no hook.** Never automatic: 60 project-wide space writes per minute means a rule creating a space per Project would saturate the budget for hours and leave thousands of empty spaces behind. Permission is the *document's* permission, checked here — a room hung off a document must not be an easier door than the document. |

**Phase 3's read/write surface — everything under `chat/api/`.** Every one begins with
`_common.require_session` or `require_room`, which resolves the caller, refuses Guest,
enforces the pilot whitelist, and (for room-scoped calls) asserts active membership through
`chat_room_has_permission` — the *same* function the socket server calls on `doc_subscribe`,
so the REST boundary and the realtime boundary cannot disagree about who is in a room.
`tests/test_chat_api_contracts.py` walks the AST and fails the build on a whitelisted
function that never calls a gate.

| Path | What it does |
|---|---|
| `chat.api.conversations.create_direct_message` | Open (or create) the DM with one person. Idempotent — clicking somebody you already talk to opens that room. |
| `chat.api.conversations.create_group` | A named group room, caller as Manager. Deliberately **not** deduplicated: two rooms may legitimately share a title, and only DMs and document rooms have an identity the database can enforce. |
| `chat.api.conversations.search_people` | The picker used *before* a room exists, so unlike `search_mention_targets` it is not room-scoped. Reads `tabUser` only. |
| `chat.api.rooms.get_bootstrap` | Everything the SPA needs for its first frame, in one round trip. Deliberately **not** `extend_bootinfo`: bootinfo is serialised into every desk page load for every user, and the room list belongs to the one page that renders it. |
| `chat.api.rooms.get_rooms` / `get_room` / `get_members` | The room list (zero joins), room detail plus roster, roster alone. |
| `chat.api.rooms.set_last_open_room` | §4.3 tier 3. **Redis, not a DocType**, debounced server-side to one write per 30 s per user. It is a hint: losing it costs one click, which does not justify a table. A prod deploy flushes Redis, so the first visit after a deploy lands on the room list — documented behaviour, not a bug. |
| `chat.api.history.get_messages` | Keyset paging on `seq`. Three cursors, one query shape: newest page, `before_seq` scrollback, and `after_seq` — **the reconnect backfill**, which is the reconciliation path that makes realtime an accelerator rather than a dependency. |
| `chat.api.history.get_thread` | A thread root plus its replies. One indexed range scan on `(thread_root, seq)`; threading is exactly one level deep by construction. **Google cannot represent this at all** — `spaceThreadingState` is output-only and `spaces.setup` states verbatim that threaded replies are unsupported — so the structure is ERPNext's alone and the thread pane works with chat dormant. |
| `chat.api.history.get_message_context` | The history page *containing* a message, plus one page either side. What a deep link, a citation and a search result all resolve to. |
| `chat.api.compose.send_message` | Idempotent on `client_message_id`. A retry after an unseen success returns the existing row **as success** — catching both `UniqueValidationError` (the one that actually fires; it is not the primary key) and `DuplicateEntryError`. |
| `chat.api.compose.edit_message` / `delete_message` | Author only. Delete is a **tombstone**: `is_deleted = 1` and the body stays, because Google's tombstone is content-free and ERPNext is the only copy. |
| `chat.api.compose.prepare_upload` | Gates an upload and returns the limits, before the bytes move. Uploads go through Frappe's own `upload_file` with `is_private = 1`, attached to the room, and `send_message` re-points the `File` at the message. |
| `chat.api.search.search_messages` | Room-scoped and global. Returns snippet **offsets**, never pre-wrapped `<mark>` HTML — message bodies are user-authored and handing the client a string to `innerHTML` is the stored-XSS vector. |
| `chat.api.mentions.search_mention_targets` | Members first, then other users, `@triton` always. |
| `chat.api.readstate.mark_read` / `mark_all_read` / `get_unread_state` / `get_read_marks` | The high-water mark, the wholesale count, and the per-member marks receipts render from. Monotonicity is enforced by the statement (`where coalesce(last_read_seq,0) < :seq`), not by trusting the client. |
| `chat.api.presence.set_typing` | **Writes nothing to the database.** Check membership, publish, done. |
| `chat.api.presence.heartbeat` / `goodbye` / `get_presence` | Redis with a 75-second TTL, keyed per (user, client). `goodbye` is an optimisation; the TTL is the mechanism. |

### `after_migrate` / `after_install`

`patches/add_chat_phase2_indexes.ensure_chat_phase2_indexes` is registered on **both**, the
same two-entry-point shape as `ensure_chat_indexes` and for the same reason: composites
Frappe's DocType JSON cannot express exist *only* if a patch runs, and `bench install-app`
marks the whole of `patches.txt` executed without running any of it. It checks
`information_schema` before any DDL and never raises. `unique(message, revision_no)` on
`Chat Message Revision` is correctness rather than speed — every writer of that table is
retried, so a duplicate revision row is scheduled, not unlikely.

It is a **second patch file** rather than rows appended to `add_chat_indexes`, because that
one is already in `Patch Log` on every site and `run_all()` has no re-run path — a row
added there would create the index on a fresh site and never on production.

### `boot`

`boot.py` adds exactly one flag, `ee_chat`. `extend_bootinfo` runs on every desk load for
every user, which is why the existing entries are all single booleans and why this one is
too.

### Entries this module deliberately does **not** register

Absences that a reader will otherwise assume are oversights:

- **No `doc_events` on any chat DocType — still true after Phase 2.** Invariant I1: no code
  path calls the Google Chat API synchronously from a document lifecycle event. A hook that
  reaches Google runs inside the inserting transaction on a web worker, so a Google timeout
  becomes a *failed message insert* — and ERPNext is the system of record, so a mirror that
  can refuse a write is not a mirror. The controllers' own `before_insert` / `after_insert`
  / `on_update` delegate to `sync/outbox.py`, which writes a `Chat Relay Job` row **in the
  same transaction** and stops; the relay is enqueued with `enqueue_after_commit=True` and
  guaranteed by `sweep_relay_jobs`. `tests/test_chat_guardrails.py` asserts — transitively,
  including function-local imports — that nothing reachable from a document event reaches
  the transport, and `tests/test_chat_outbox.py` re-checks it with an AST pass over the
  write path. That is also why `sync/provisioning.py` is enqueued by **dotted string**: the
  write path may not import it.
- **No `website_route_rules`.** Phase 3's SPA needs
  `{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}` — this would be the app's
  first such rule. Declaring it before the page it routes to exists means a live route
  serving a 404 to anyone who guesses the URL. Phase 3 declares it with the page.
- **No `app_include_js` / `app_include_css`, and no file in `public/js/chat/`.** Phase 1
  ships no browser code. When Phase 3 does, it ships as an esbuild bundle
  (`name.bundle.js`), never a raw `/assets` path — raw paths are served immutable for a
  year with no content hash, which is the "fixed on desktop, phones still broken" bug.
- **No `fixtures` entry.** The `Chat User` and `Chat Auditor` roles are created by *patch*,
  not fixture. A profiled user's direct roles are rebuilt from their Role Profiles on every
  save, so a directly-granted role is wiped; and a Role Profile shipped as a fixture has
  crashed `bench migrate` on this site before (`DocumentLockedError`, fixed in v1.171.1).
- **`"Chat"` is on `utils/triton_sync.py`'s `excluded_modules` list** (invariant
  CHAT-EXCL-1). Without it every chat DocType write announces itself to Triton's index
  webhook — a self-inflicted DoS on the webhook queue *and* an unreviewed egress of
  employee-private message content to an external service. It fails **silently**: the
  writes all succeed.

---

## Rules that are easy to get wrong

### The three-value watermark, and why one value is a privacy bug

Every digest, chunk and context cache key is keyed on **`(max(seq), count(*), max(modified))`**
over the covered span, and all three are load-bearing:

| Value | What it catches | What it misses |
|---|---|---|
| `watermark_seq` | a new message | **an edit, and a delete** — neither advances `seq` |
| `watermark_modified` | an **edit** | a hard delete |
| `watermark_count` | a **hard delete** | — |

A digest keyed on `seq` alone is *unchanged* by a delete. So its cache key is unchanged, so
the summary containing the message somebody just deleted is served again — and it keeps being
served until the TTL happens to roll. That is a privacy failure that presents as a caching
bug, and R03 names it "the single most common bug in this design; write the test first."

Two consequences the implementation must keep:

- **Retrieval skips a stale digest or chunk outright** rather than serving it with a caveat,
  and the rebuild is a **full regeneration from source**, never an incremental append. A
  rolling summary can add information but it cannot unsay it. ERPNext holds the *only* copy of
  a deleted body — Google's tombstone is content-free — so a stale row served once is deleted
  text back in a model's context window, with the person who deleted it unable to know.
- **Every freshness comparison uses `frappe.utils.now_datetime()` on both sides, or converts
  explicitly.** The production database runs UTC while Frappe writes `creation`/`modified` in
  site-local time, so a naive `TIMESTAMPDIFF(MINUTE, MAX(creation), NOW())` reports a row
  written one minute ago as **361 minutes** old. Every SQL-side freshness predicate built that
  way — the dirty-room predicate, the staleness alarm, the cache-invalidation window — is wrong
  in the same direction, and the direction is "nothing is ever fresh".

**The dirty-room predicate is derived, not counted, and that is a deliberate divergence from
the ADR.** §I.6 specifies `unsummarized_count >= 25 OR digest_dirty_since < now() - 15 min`,
which implies two counter columns maintained on the message write path. This build computes the
same predicate from facts the room already stores — `Chat Room.seq_high_water` minus the
digest's `watermark_seq` *is* the unsummarised count, and `Chat Room.last_message_at` against
the digest's `generated_at` *is* the dirty age. The reasoning is the one this repo applies
everywhere else: a counter is a second source of truth for a number already known, it needs a
write on the hottest path in the feature, and the copy that drifts is the one nobody notices.
Derived is self-correcting; counted is not.

### A unique-index collision on `gchat_message_name` is SUCCESS, not an error

`Chat Message.gchat_message_name` carries a `UNIQUE` index. That is invariant I2 made
**structural**: a duplicate insert fails at the database, not at an `if`.

Pub/Sub delivers at least once. Two workers can process the same redelivered event
concurrently. The correct inbound writer therefore:

```python
# chat/sync/inbound.py — savepoint_catching_duplicates() is this, packaged.
from frappe.database.database import savepoint

with savepoint(catch=(frappe.DuplicateEntryError, frappe.UniqueValidationError)):
    doc.insert(ignore_permissions=True)
# Already ingested. This is the happy path, not an error: the unique index just did the
# deduplication for us, atomically, with no race window.
```

**Catch BOTH, and this corrects what Phase 1 wrote here.** `frappe.DuplicateEntryError` is
raised **only for a primary-key collision** — a duplicate `name`. Any *other* unique index,
which is exactly what `gchat_message_name`, `(room, client_message_id)` and `(room, seq)`
are, raises **`frappe.UniqueValidationError`**. The two share no base beyond `Exception`
(`DuplicateEntryError(NameError)` versus `UniqueValidationError(ValidationError)`), so the
single-exception form documented in ADR §G.3.1 and in Phase 1's own docstrings **would not
catch the collision it exists to catch** — structural dedupe would fail open into a logged
error on every redelivery, the precise opposite of the intent. `sync/inbound.py` and
`sync/membership.py` each expose `duplicate_errors()` / `savepoint_catching_duplicates()`
so no call site has to remember.

Three details that are not optional:

- **The savepoint is mandatory, not hygiene.** A failed statement can poison the
  surrounding transaction; Frappe's own docstring models exactly this pattern.
- `show_unique_validation_message` calls `frappe.msgprint` **before** raising, so an
  expected duplicate leaks a user-visible *"must be unique"* message unless it is cleared —
  which is what `clear_expected_duplicate_message()` is for.
- `insert(ignore_if_duplicate=True)` covers **only** the primary-key branch. Not enough.

Consequences that follow from that, and are not optional:

- **Never `SELECT`-then-`INSERT`.** It is a TOCTOU bug at exactly the moment it matters —
  two workers, one redelivered event. Attempt the insert; let the index arbitrate.
- **Never log either exception from this path as a failure.** An Error Log row per
  redelivery turns normal operation into a wall of noise and trains everyone to ignore it.
- The same reasoning applies to `Chat Inbound Event.pubsub_message_id`,
  `Chat Attachment.gchat_attachment_name`, `unique(room, client_message_id)`,
  `unique(room, seq)` and `unique(message, revision_no)` — all unique for the same reason.
- One driver detail worth carrying: MariaDB's duplicate text is `Duplicate entry 'x' for key
  'PRIMARY'`, which satisfies **both** `is_primary_key_violation` and
  `is_unique_key_violation`. Frappe's own `db_insert` is correct only because it tests the
  primary-key predicate first. **Never reuse `is_unique_key_violation` standalone** to mean
  "not a PK collision".

### Ordering, edits and deletes — four rules, by name

ADR §G.8. Each has a test named after it; refer to them by name in review.

- **Rule 1 — CREATE-BEFORE-EDIT.** `unique(room, job_seq)` makes a room a strict FIFO. A job
  whose predecessor is not `Done` is **deferred** (`available_at` pushed forward), never
  failed — failing it would convert a five-second ordering wait into a dead letter. Inbound,
  an `updated` event naming a message we have never stored is applied as a **create** from
  the fetched resource; a `deleted` for an unknown message is recorded `Ignored` — **and the
  convergence that makes `Ignored` safe is on the create side**: when that message's `created`
  is finally processed, its `messages.get` returns Google's tombstone and
  `inbound._apply_created` inserts the row **already deleted**, never alive-then-deleted, and
  fires no notification. Without that half, a `deleted` overtaking its `created` left the
  message alive in ERPNext and dead in Chat permanently with nothing in any log — which is
  what the 200-message soak caught and no review did.
- **Rule 2 — FIRST-WRITER-WINS ON THE RESOURCE NAME.** `unique(gchat_message_name)` decides,
  atomically. **Never `SELECT`-then-`INSERT`** — that is a TOCTOU bug at exactly the moment
  it matters, two workers with one redelivered event.
- **Rule 3 — LAST-WRITER-WINS BY `lastUpdateTime`, ERPNEXT BREAKS TIES.** Equal, or either
  missing ⇒ ERPNext wins. **The `else` branch is the part people leave out:** discarding an
  inbound edit must *re-queue an outbound `Message Update`*, otherwise the two sides stay
  permanently divergent with nothing to notice. **Convert timezones explicitly** — Google
  returns RFC-3339 UTC and `edited_at` is Frappe-written site-local, so a naive comparison
  is wrong by the site offset **in the direction that always makes Google win**, silently
  inverting decision #1. `inbound.parse_google_timestamp` / `utc_to_site` / `site_to_utc`
  exist so no call site does it by hand.
- **Rule 4 — CATCH-UP BY SWEEPER, NOT BY QUEUE.** See
  [the scheduler entries](#scheduler_events--the-only-delivery-guarantee-there-is). **The
  number to put in front of a human: a room that accumulated 600 messages during an outage
  takes 600 seconds to drain, in order.**

**Ordering is `seq`, never a timestamp** — not `creation`, not Google's `createTime`. `seq`
is immutable once assigned and never renumbered, because Phase 5's digest watermarks and
cache keys derive from it. Store both timestamps; display the origin's, sort by `seq`.

**Soft delete keeps the body on the row.** Google's tombstone is rich in metadata and
**empty of content** — `showDeleted=true` returns the delete time and metadata, never the
text — so if ERPNext does not keep it, nobody has it. Every read path filters
`is_deleted = 0`; only the oversight role sees through, and Phase 6 makes it pay with an
audit row. `deletionMetadata.deletionType` maps cleanly onto `deletion_source`
(`CREATOR_VIA_APP` is a DWD-impersonated delete on behalf of the author,
`SPACE_OWNER_VIA_APP` an impersonated manager), so attribution needs no guessing. This is a
deliberate divergence from the Phase 2 brief, which moves the body to the audit table and
clears the live row.

**No code path calls `spaces.delete` without an explicit human confirmation step.** It
cascades and takes a company's conversation history with it in one call; `chat.delete` /
`chat.app.delete` are not granted in V1 and must not be.

### The echo-suppression ladder, in five sentences

Every rung lives in `sync/decisions.classify_inbound()` and nowhere else; `sync/inbound.py`
gathers facts, calls it, and acts on the verdict.

1. **Structural dedupe** — probe `unique(gchat_message_name)`; a hit is `DUPLICATE`, which
   is a **success**, not an error.
2. **`messages.get`** the resource name, because the event carries nothing else.
3. **Echo check** on the fetched `clientAssignedMessageId` against
   `unique(room, client_message_id)`; a hit is `ECHO` — bind the resource name to the
   existing row, do not insert, do not notify, do not relay.
4. A `client-` prefixed id with **no** local row is `ECHO_ORPHAN`: **alarm, and do not
   guess** — a guess here either duplicates a message or eats one.
5. No client id at all is `NEW`, and it gets ingested.

**`messages.get` is the NORMAL path, not a fallback.** The subscription runs
`payloadOptions.includeResource: false` because it is the only configuration with a
**7-day** TTL ceiling — 4 hours with resource data, 24 hours with resource data *and* DWD,
and that 24-hour figure raises the include-resource ceiling, **not** the 7-day one, so DWD
buys no TTL here. Confirmed live against this tenant on 2026-08-09: the same `validateOnly`
create with `includeResource: true` and a seven-day ttl is refused *"The subscription
expiration time exceeds the maximum allowed."* So an inbound event carries a resource name
and nothing else — no body, no sender, no client id — and the Phase 2 brief's *"Layer 1:
read the client id off the payload"* **does not exist in this deployment**. Almost every
genuine inbound message therefore costs one `spaces.messages.get`, budgeted on purpose
against the 3,000-reads-per-minute project bucket. **Deleting that read does not save a
call**; it removes the only source of the information the echo check needs, and the failure
mode is that every message ERPNext sends comes back and is stored a second time.

The bounded fallback heuristic (exact normalised body hash, sender match, a 120-second
window) exists for the residue where no client id exists at all — an older build, a manual
API call, an import backfill. It ships **disabled** (`Chat Settings.echo_fallback_enabled`
defaults to 0), it alarms on every firing, and **more than one candidate matching returns
`(None, "ambiguous")`**, which the caller treats as `NEW`. That last guard is what
deterministically kills the objection to heuristics — two identical "ok" messages seconds
apart — rather than arguing about it.

### Attachments: `is_private = 1`, always

Every chat attachment is a **private** `File` **attached to the `Chat Message`**
(`attached_to_doctype = "Chat Message"`, `attached_to_name = <message>`). Both halves
matter:

- **Private**, because public files live in `sites/<site>/public/files/` and are served by
  the web server **with no authentication at all**. A public chat attachment is a
  world-readable chat attachment.
- **Attached to the message**, because Frappe's file permission check then delegates to
  `has_permission` on `Chat Message` — so row-level chat security covers files by
  construction rather than by a second, parallel rule.

`Chat Attachment.source` splits `Uploaded` from `Drive Link` (ADR §F.8's vocabulary; the
third option is `ERPNext`), and the split is a permission decision: copying a `Drive Link`'s
bytes into ERPNext detaches the file from Drive's ACL, which is the governing permission
model for it. Phase 3 owns the attachment UI; Phase 1 owns the convention and the field that
stops Phase 2 from "just downloading it".

**Write `SOURCE_UPLOADED` / `SOURCE_DRIVE_LINK` / `SOURCE_ERPNEXT` from
`chat_attachment.py`, not Google's names.** Google's `Attachment.source` enum spells these
`UPLOADED_CONTENT` and `DRIVE_FILE`; `chat_attachment.GOOGLE_SOURCE_MAP` translates them at
the boundary. A Phase 2 ingest that compares a stored `source` to `"DRIVE_FILE"` silently
never matches, and the row it should have skipped gets downloaded.

**Phase 2 found the corollary the convention implies and the design did not spell out: the
delegation denies *members* too.** `Chat Message` ships zero DocPerm, so
`File.has_permission` — which delegates to the attached document — refuses
`/private/files/…` to everybody but Administrator. Row-level security by construction, and
also no byte path at all. The answer is
`chat.sync.attachments.download`, which decides with the *same*
`permissions.chat_attachment_has_permission` the hook uses; the alternative — a DocPerm on
`Chat Message` — would have opened the desk's report view onto every message body in the
company. Do not "fix" the 403 by adding that DocPerm.

`VERIFY:` on a real bench, that a non-member gets **403** on another room's private
attachment URL. Frappe has a long tail of reported issues where private files are more
accessible than expected, and this is cheap to check and expensive to assume. The assertion
is written — `tests/test_chat_attachments_bench.py::test_private_file_url_is_forbidden_for_a_non_member`
— and **has not been executed**, because CI cannot run it.

### 1 write per second, per space — and `media.upload` shares that bucket

Google's binding rate limit is **one write per second per space**. Project-level limits are
roughly 1000× headroom at 50 users, so this is the one that will actually bite.

- **`media.upload` charges against the same per-space budget as `messages.create`.**
  Relaying one message with an attachment costs **two seconds** of that space's budget, not
  one. This is the "someone drops a screenshot into a busy space" failure.
- Project-wide: **space writes 60/minute**, **membership writes 300/minute**. Bulk org
  provisioning is therefore a throttled, resumable job — not a loop.
- **The client does not rate-limit and must not.** A token bucket inside
  `GoogleChatClient` would be per-process, and therefore wrong the moment a second worker
  starts. **`sync/ratelimit.py` owns it** (not `chat/bucket.py`, which Phase 1 planned and
  Phase 2 did not build): pure GCRA arithmetic plus a Redis deployment of exactly that
  arithmetic, shared across workers, surviving restarts, charging an attachment message
  `(n + 1) × 1000` ms. Retrying in a tight loop against one space converts a 429 into five.
- **The bucket is an optimisation; backoff is the correctness mechanism.** Google publishes
  two caveats no design removes — *"Additional rate limit checks on the Chat backend might
  also generate the same error response"* and *"High API traffic targeting the same space
  can trigger additional internal limits that aren't visible in the Quotas page."* Staying
  under one write per second does **not** guarantee no 429. Never delete the retry loop
  because the limiter is in place.
- **Membership writes and `spaces.setup` / `spaces.create` do NOT consume the per-space
  bucket** — they appear in no per-space row of Google's table, and in the `setup` case the
  space does not exist yet. They are charged to `ProjectQuota` only (300/min and 60/min
  respectively). Charging them to the space bucket would throttle provisioning and
  membership reconciliation roughly 300× harder than necessary.
- **The per-project 3,000/60 s read limit is several independent buckets** by category
  (messages, memberships, spaces, attachments), not one shared pool. Budgeting them as one
  under-uses the API about fivefold.
- Message bodies cap at **32,000 bytes** for the whole message resource — not just `text`,
  and bytes rather than characters, so an emoji-heavy message is a quarter of the length
  its character count suggests. `sync/budget.py` is the arithmetic.

### No credential is stored anywhere, and nothing here needs one

`Chat Settings` holds **identifiers only** — project ids, project numbers, service-account
*email addresses*, topic names, the audience URL, the Workspace domain. There is no
`Password` field on it and there must never be one. The keyless DWD design means there is
nothing to store: the VM's attached service account signs the assertion through IAM
Credentials `signJwt`, and no private key exists on disk or in git.

**If you find yourself adding a `Password`-fieldtype field for a Google credential, the
design has gone wrong** — go back to keyless rather than storing a key.
`chat_settings_rules.detect_secret_material()` refuses PEM blocks and service-account JSON
pasted into an identifier field, and `scripts/check_no_committed_secrets.py` scans the
working tree *and git history* as a blocking CI gate.

### Never log a message body at INFO

Chat content is employee-private, and decision #12 audits non-participant *reads* — a log
file that quietly contains every message routes around that audit more completely than any
UI could. `client.build_log_record()` emits `text_length`, `text_bytes` and a truncated
hash instead. Body logging exists behind `Chat Settings.log_message_bodies`, defaults off,
and turning it on emits a warning line **on every call** saying chat content is being
written to logs.

Related, and this repo's own scar tissue: every exception raised out of `gchat/` uses
`raise … from None`. A bare re-raise out of a background job publishes the failing frames'
locals into the Error Log, and those frames hold an `Authorization: Bearer` header. This
app has already leaked private key material exactly that way once.

### Realtime events must always be targeted

`frappe.publish_realtime`'s final fallback is `get_site_room()` — a **site-wide broadcast**.
A chat event that forgets its `room=` / `user=` / `doctype=`+`docname=` argument broadcasts
message bodies to every connected session. And `list_update` / `docinfo_update` are worse
than useless as event names: Frappe **overwrites an explicitly passed `room=`** for those
two. `tests/test_chat_guardrails.py` asserts both, by regex over the package source.

**Phase 2 turned that rule into a module: `realtime.py` is now the only publish in this
package**, and there is a third trap it exists to close that a rule cannot. Before the
targeting chain runs, `publish_realtime` does `if not task_id and hasattr(frappe.local,
"task_id"): task_id = frappe.local.task_id` — and `task_id` **outranks `user=`**. Inside a
background job that attribute is set, so a call that carefully passes `user=` or
`doctype=`/`docname=` and no `room=` is retargeted to `task_progress:<task_id>`, a room any
client may join with **no permission check at all**. The chat relay runs in background
jobs; this is the live trap, not the theoretical one. So `publish_room_event` passes an
explicit `room=` (the only thing that short-circuits the whole chain) *and*
`doctype`/`docname` (which state the intent), always `after_commit=True`, and **refuses the
two poisoned event names with a `ValueError`** rather than documenting them as a hazard.
Payloads carry identifiers and let the client fetch — never file bytes, never long bodies.

### Granting `Chat User` is a ROLLOUT STEP, and nothing does it for you

`patches/seed_chat_roles.py` **creates** `Chat User` and `Chat Auditor` and deliberately
stops — its docstring says so and ends *"This command has not been run."* It still has not
been, and on 2026-08-10 that produced a live pilot in which **nobody held the role**.

`Chat Room` carries exactly one DocPerm: `read` for `Chat User`. That single row is what
lets the realtime doc-room join reach `chat_room_has_permission` at all. Without it:

| path | works? | why |
|---|---|---|
| The whole REST surface (`chat/api/**`) | **yes** | raw SQL + `membership_filter_sql`, and `require_room` calls the hook directly — no DocPerm in the way |
| The realtime doc-room join | **no** | `frappe.realtime.has_permission` → `frappe.has_permission(..., throw=True)` — the FULL stack, DocPerm first |

So chat looks like it works — rooms list, messages send and store — and **no live update ever
arrives**, because a refused join is silent and its promise never settles. Measured on prod:
`chat_room_has_permission` returned `True` and `frappe.has_permission` returned `False` for
the same user and room.

**Grant it directly to a user with no Role Profile; grant it via a profile to a user who has
one.** Both directions of that rule are load-bearing and the patch documents them: a role
granted directly to a *profiled* user is wiped on the next save of that user, and assigning a
profile to a *profile-less* user regenerates their roles from it — wiping `System Manager`.
Check `User.role_profile_name` before choosing, every time.

### The realtime event contract

Every event this package publishes, and which room it belongs in. The client generates its
handler map from the same list (`public/js/chat/socket.js::EVENT_NAMES`) and
`scripts/test_chat_source_rules.js` fails the build when the two disagree — a client
listening for an event nobody publishes is indistinguishable, from the client's side, from a
quiet room.

**The room split is invariant I8, and it is what makes socket security free.** A *document*
room is permission-checked when a client joins it: the socket server calls back into
`chat_room_has_permission` under the joining user's own session. A *user* room is not
joinable by anybody else — there is no `user_subscribe` verb a client can call — but it is
not scoped to a conversation either. So content and content-adjacent state go to the doc
room, counters go to the user room, and never the other way round.

| Event | Room | Payload | Published by |
|---|---|---|---|
| `chat_message_created` | doc | `{message, room, seq, sender, sender_kind, message_type, thread_root, has_attachments}` | `sync/outbox.py` (`after_insert`) |
| `chat_message_edited` | doc | `{message, room, seq, origin}` | `sync/inbound.py` for a Google-side edit; `api/compose.py` for an ERPNext-side one — see the note below |
| `chat_message_deleted` | doc | `{message, room, seq, origin}` | same pair |
| `chat_typing` | doc | `{room, user, thread_root?}` | `api/presence.py`. **No database write at all** |
| `chat_typing_stopped` | doc | `{room, user}` | `api/presence.py` |
| `chat_presence` | doc | `{user, state, ts}` | `api/presence.py`, **transitions only** — never the heartbeat |
| `chat_read_receipt` | doc | `{room, user, last_read_seq}` | `api/readstate.py`. A high-water mark, not per message |
| `chat_room_updated` | doc | `{room, changed}` | reserved; title/membership/archive changes |
| `chat_unread_updated` | **user** | `{room?, unread?, mention, total_unread?, total_mentions?}` | `api/readstate.py`. Counters only, ≤512 bytes |
| `chat_mention` | **user** | `{room, message, thread_root, by}` | reserved for Phase 4's ping |

Every one of them is `after_commit=True`, without exception. The consumer immediately
re-reads the database, so an event that beats its own transaction points at a row that does
not exist yet — an intermittent 404 that reproduces only under load.

**Names use an underscore (`chat_message_created`), not a colon.** The Phase 3 brief's §4.5
table spells them `chat:message_created`; Phase 1 shipped the underscore form and Phase 2
publishes it, so the colon form would be a silent break of the sync engine's own events.
`realtime.py::_EVENT_PREFIXES` accepts both, and the underscore form is what exists.

**A reported Phase 2 gap.** `sync/inbound.py::_publish` fans out `chat_message_edited` /
`chat_message_deleted` for a change arriving *from Google*; `outbox.propagate_message_change`
— its ERPNext-side twin — relays the change onward but publishes **no realtime event at
all**. So before Phase 3, an edit made in ERPNext reached the Google space and reached no
other ERPNext client until they refreshed. Phase 3's own write path
(`api/compose.py::_publish_change`) announces its own writes, which closes it for the SPA
without touching the sync engine. It does **not** close the general case: an edit made
through the desk, a patch, or a future admin tool still publishes nothing. That belongs to
whoever owns `propagate_message_change`.

### The sources row: an approved exception to "preserve exactly"

Locked decision #7 says the Triton sources dropdown is **preserved exactly**. Research 03
§12.6 proposed the opposite — show the full manifest with cited entries marked and sorted
first — and was explicit that it required a human yes rather than a unilateral edit. It was
raised at the Phase 3 checkpoint and **approved on 2026-08-10**.

What that means concretely, because "approved" is not the same as "safe":

* **`renderSources()` is untouched.** Same container class, same chip class, same
  `label || title || url` fallback, same `textContent`, same `target="_blank"
  rel="noopener"`. It is still the path taken by every turn on every site — no manifest
  exists until Phase 5 emits one.
* **`renderManifestSources()` is a separate function beside it**, not an edit to it. That
  separation is what makes "with no manifest, nothing changed" a fact rather than a claim,
  and `scripts/test_triton_widget_guards.js` fails the build if either the old renderer
  changes shape or the new one is folded back into it.
* **The row is still the whole retrieved set** — uncited entries are dimmed, never dropped.
  That superset is the property decision #7 was protecting; hiding retrieved-but-unused
  sources removes exactly what somebody checking an answer wants.
* **Ordering is by id within each group**, because the inline markers read `[1]`, `[2]`,
  `[3]`. `citations.js::orderManifestForDisplay` is pure and carries the rule.
* **Marking is live, reordering happens once**, in `finishStreaming` (and once more if the
  turn is later restored from history). A row that reshuffles while the reader is using it
  moves the chip they were about to click. The sort is gated on the caller —
  `orderManifestForDisplay(manifest, cited, {sortCitedFirst})` — because the first revision
  of this feature *said* "one reorder, nowhere else" and then re-sorted from the mid-stream
  `citations_append` arm. The rule is positional, so `test_triton_widget_guards.js` asserts
  which call sites may ask for it.
* **The row is created in place, never repositioned.** The pre-Phase-3 row was not last: the
  `done` handler runs `renderSources()` and only then `renderChart()`. Forcing the manifest
  row to the end moved it, and made a live turn disagree with the same turn re-opened from
  history.

If a future phase wants the row back to strictly-preserved, delete the `citations` case's
call to `renderManifestSources` — the old renderer is still there and still correct.

### Presence, typing and read receipts are ERPNext-sourced — and a coworker in the native Google Chat client shows as offline

This is invariant I14, it is a real product limitation, and it must not be discovered in
production.

Google Chat exposes **none** of the three. It has no typing-indicator API at all, for
anyone, ever. Its `users.availability` and read-state surfaces are **self-scoped** — a caller
reads their own, never a coworker's — so they cannot power a presence dot next to another
employee's name. All three signals here are therefore built on Frappe realtime and on the
user's **ERPNext session**.

The consequence: a colleague who is chatting happily from the native Google Chat client
contributes no typing signal and no presence signal to this application, and renders as
offline. The UI states that rather than implying they are away from their desk — the member
list carries the sentence "Presence and typing come from ERPNext. Someone using only Google
Chat shows as offline", and each offline dot carries the same explanation for a screen
reader. **No code path may infer a coworker's presence or read state from a Google Chat API
response.**

**The caption is only true if every ERPNext surface actually beats.** It shipped with one
writer — the SPA — while the bubble, which is where the pilot users live, published typing
but never presence. So a colleague working in ERPNext all day rendered offline under a
sentence saying they had no ERPNext session, beside a live "…is typing" line for the same
person. The limitation above is about Google Chat; a missing heartbeat is a bug wearing its
costume, and it is indistinguishable from the real thing on screen. Any new surface that
renders chat must call `presence.heartbeat`, and `scripts/test_chat_source_rules.js` rule 11
fails the build if one does not.

Two mechanical consequences worth knowing:

* **Presence is Redis with a TTL, never a DocType.** At fifty users heartbeating every 30 s a
  DocType would take ~144,000 writes a day for data worthless after sixty seconds. More
  importantly the TTL *is* the crash handling — a dead browser, a closed lid, a killed tab
  all simply expire — so there is deliberately **no cleanup code path** for the crash case.
  A "goodbye" on `pagehide` exists as an optimisation and correctness never depends on it.
* **Keys are per (user, client), not per user.** A user has three tabs. Presence is the union
  across their clients, and focus is evaluated as "**no** client of this user has that room
  focused". Keying on the user alone lets the last tab to heartbeat overwrite the others,
  which reads as "notifications stopped working when I opened a second tab" and is the single
  most likely thing to get wrong here. `presence.focus_state` is the pure function that union
  is written in, it has its own test, and `public/js/chat/signals.js::unionPresence` is its
  client-side twin tested against the same table of cases.

---

## Operating it

The section to read at 2am. Nothing here needs the sync engine's source.

### Start here

```bash
bench --site <site> execute erpnext_enhancements.chat.health.report
bench --site <site> execute erpnext_enhancements.chat.health.report --kwargs "{'room': 'abc123'}"
```

`health.py` prints the flags, the outbox depth, the oldest **due** Pending job, expired
leases, per-room state, subscription expiry and the seam counters — each number named,
carrying its unit, and *judged* (`[ALARM] the relay worker is not draining`, not a bare
timestamp). It **never raises**: every query is guarded individually, so a half-configured
site still produces a report with the missing pieces named at the bottom. It reads counts,
states, timestamps and room names — **never message text** — and it is deliberately not
`@frappe.whitelist()`, so there is no HTTP surface to scope.

**"Oldest Pending job" and "oldest Pending job that is already due" are different numbers
on purpose.** A room drains at one write per second, so a room that accumulated 600
messages during an outage takes 600 seconds to drain **and that is the system working**. A
job deferred by ordering or backoff is healthy. Only a *due* job still waiting means nobody
is draining.

### Kill switches

All on `Chat Settings`, all ship off/paused-safe, and **none of them drops anything** — a
paused relay leaves every row where it is and the `order by job_seq` claim is what makes
the drain on re-enable ordered rather than merely eventual.

| Field | Effect |
|---|---|
| `enabled` | The master switch. Off ⇒ `auth.get_delegated_token` refuses to mint a credential, so every job below no-ops at the bottom of the stack as well as the top. |
| `dry_run_mode` | Deterministic, **visibly fake** responses (`spaces/DRYRUN-…`). Zero network I/O, and reconciliation knows to skip them. |
| `google_sync_enabled` | Off ⇒ ERPNext-only chat. The outbox marks new messages `Not Mirrored` instead of queuing a relay. |
| `pause_outbound` | **The incident lever for an echo storm.** Stops the relay claiming; rows accumulate in `job_seq` order. |
| `relay_outbound_enabled` | The outbound feature flag; the dormant-by-default half of the same answer. |
| `pause_inbound` | Stops the puller and the reconciliation sweep. Deliberately does **not** stop `renew_due_subscriptions` — pausing ingestion must not silently let subscriptions die, because that failure is unrecoverable. |
| `relay_inbound_enabled` | The inbound feature flag. |
| `restrict_to_whitelist` | Pilot gate (`Chat Allowed User`). |
| `circuit_breaker_threshold` / `_cooldown_seconds` | M consecutive failures opens the breaker for the cooldown rather than dead-lettering a queue's worth of rows against an outage. |
| `max_reverts_per_hour` | The cap that stops ERPNext and a human in the native client reverting each other forever. |
| `echo_fallback_enabled` | The bounded heuristic. **Leave it at 0** unless you are working a specific residue case. |

### `Dead` is terminal, and that is a design decision

`Dead` means a relay job exhausted its attempts. `retry_relay_job` **refuses** it, and the
refusal is the interesting part: `job_seq` is the room's FIFO position, later jobs have
already drained past it, and reinstating a stale position would replay an edit before the
create it edits. **Re-sending means a new relay job with a new `job_seq`**, which the
outbox mints. The button also refuses an `In Progress` row whose lease is still live — a
worker is holding it, and a button may not steal a live claim; wait for the lease to expire
and the sweeper reclaims it. `reset_attempts` defaults on, because an operator pressing
retry means "try again" and leaving `attempts` at the maximum would route the row straight
back to `Dead` and make the button look like a bug.

Retry is `System Manager`-only and **never calls Google**: it writes a status through the
same transition table the worker uses and wakes a worker. A button that talked to Google
directly would be a second, untested relay path that only ever runs on a day somebody is
already having a bad one.

### The one thing never to do

**Do not fill `Chat Settings.admin_oversight_role` casually.** It ships **blank**, and
while it is blank there is *no role in the system* that can read a room it is not a member
of — `permissions._oversight_role()` returns `""`, `_has_oversight()` returns `False`, and
the unrestricted `""` query condition is unreachable. Filling it hands every holder of that
role the entire company's private conversations, including deleted bodies (`is_deleted = 1`
rows keep their text; Google's tombstone has none, so ERPNext is the only copy). It is a
governance decision with an audit obligation attached — every privileged read routes
through `permissions.note_privileged_read()`, which writes a `Chat Retrieval Audit` row.

**That obligation is now met, which it was not until v1.268.0.** Until then the hook returned
`None`, so filling this field granted the whole company's private conversations to a role and
logged nothing at all. It was briefly set on production on 2026-08-10 and reverted the same
day for exactly that reason. If you are reading a note that says "setting it before Phase 6
means privileged reads that are not recorded anywhere", that note is out of date — but read
[the audit](#the-decision-12-audit) for what *is* still missing before you turn it on, because
the recording is not the same thing as the reviewing.

If you need one person to see one thing, add them to the room.

### The decision #12 audit

`chat/audit.py` is the **only** module that writes a `Chat Retrieval Audit` row.

**The row is written by the endpoint that returns the content, never by a permission hook.**
That is the rule the whole design turns on, and it was learned the hard way: v1.268.0 wrote
from inside `note_privileged_read()`, and every serious defect that release shipped came from
that one decision.

- It **committed inside other requests' transactions.** The row must be durable before
  content is returned, so the writer commits — and a hook fires part-way through arbitrary
  requests. `announce_unread` is a `Chat Message.after_insert`, so this reached the relay.
- It **recorded reads that were then denied.** A `has_permission` hook runs before the answer.
- It **recorded almost nothing** — a hook knows the scope is unrestricted, not which rooms
  will be read. Real rows from that design carried no rooms, no counts and no query.
- It **fired for `Administrator`**, because `membership_filter_sql` grants the unrestricted
  scope to `Administrator` *or* the oversight role. The log filled with rows naming an
  identity the schema's own field description calls meaningless — and, note, this means **the
  privileged branch is reachable with `admin_oversight_role` blank.**

So `note_privileged_read()` now only marks memory (`audit.mark_privileged_scope`), and
endpoints record. Rule 2 in `tests/test_chat_audit_immutability.py` fails the build if a
function that consumes the unrestricted scope does not.

**The chain signs `recorded_at`, not `creation`.** `Document.insert()` calls
`set_user_and_timestamp()` first thing, which assigns `creation = modified = now()`
unconditionally for a new document — so a caller-supplied `creation` never survives, and
signing it meant signing a value the database never stored. v1.268.0 reported its very first
row as tampered for exactly this reason, confirmed on production before it was rewritten.
`recorded_at` is this app's own field and Frappe has no opinion about it.

**Writes are serialised by a MariaDB advisory lock.** Two overlapping privileged reads that
both read the same chain head both sign it, the chain forks, and `verify_chain` reports a
permanent break indistinguishable from tampering. That is the normal case rather than a rare
race — the SPA issues its room list, unread counts and transcript as parallel requests. If the
lock cannot be taken the write fails and the read is refused, because a forked chain is worse
than a refused search. `GET_LOCK` is connection-scoped rather than transaction-scoped, which
is why `SELECT … FOR UPDATE` cannot do this job: the critical section contains a commit.

Immutability is four layers, because each is bypassed by the next one down: DocPerm (bypassed
by `ignore_permissions`, which the writer needs), the controller's `before_save`/`on_trash`
(bypassed by `db_set`/`db.set_value`/raw SQL, none of which load a document),
`tests/test_chat_audit_immutability.py` (the only layer that can see the previous one's blind
spot), and `chain_hash`. The chain makes tampering **detectable, not impossible** — anyone
with database write access can rewrite a row and recompute the tail. Verify it with:

```bash
bench --site <site> execute erpnext_enhancements.chat.audit.verify_chain
```

**What is still missing before oversight is genuinely reviewable**, and why turning the field
on is still a decision rather than a formality:

- Nothing *reads* the log yet. There is no oversight viewer, no `Chat Access Report`, and no
  scheduled chain verification — so a privileged read is recorded and nobody is told.
- A row written from a permission hook records that a privileged read happened, not what it
  reached; only `search_messages` records rooms and seq ranges today.
- `reason` is a free-text field that nothing enforces, because the thing that should collect
  it — the viewer — does not exist. The ADR requires a minimum-length reason per viewer
  session for Admin reads.

## What is deliberately lossy

Six places where the mirror is knowingly not lossless. None is a bug and none should be
"fixed" without re-opening the decision.

- **32,000 bytes.** An oversized message is relayed **truncated with a deep link back to
  ERPNext** and `Chat Message.truncated_for_relay = 1`; the full body always stays on the
  ERPNext row, which is the source of truth. Rejecting at compose time was refused — it
  would let Google's limit dictate what an employee may say inside ERPNext, which inverts
  decision #1.
- **Drive-linked attachments stay references.** `source = Drive Link` is relayed and stored
  as a link, never copied. Copying the bytes into ERPNext detaches the file from Drive's
  ACL, which is the governing permission model for it — that is a permission decision
  wearing a data-model costume, not an optimisation.
- **External users.** A Chat member with no ERPNext `User` is stored with `sender_email`
  and no `sender` link. Their messages are kept because ERPNext is the record of what was
  said, but they are not a person the system can attribute, notify, impersonate or grant
  anything to. `Chat Room.external_users_allowed` is the explicit per-room policy.
- **A message deleted in Chat before we ingested it keeps no body, because there is none.**
  Google's tombstone is metadata only, so if the `deleted` event overtakes the `created` the
  text was never delivered to anybody. The row is still stored — ERPNext is the record that
  the message existed — already tombstoned, with `inbound.TOMBSTONE_BODY_NOTICE` in `text`
  rather than `""`, so the one reader it will ever have can tell "never received" from "blank
  message" and from "the soft delete wrongly cleared the row".
- **No cards under user auth.** `cardsV2` and `accessoryWidgets` are app-authentication
  features and the relay authors as the real human ([CQ-1](#cq-1-human-attribution-and-what-it-costs)),
  so a relayed message is **text only**. Rich replies are the app identity's job.
- **No threading at all, and this one is not a choice.** `Space.spaceThreadingState` is
  marked **Output only** in the discovery document, `spaces.setup` says verbatim *"Spaces
  with threaded replies aren't supported"*, and `spaces.patch`'s updateMask does not
  include it — three independent confirmations that **the API cannot create a threaded
  space.** So there is no create-time decision to get right: `Chat Settings.threading_enabled`
  stays 0, `Chat Message.thread_root` is ERPNext-side only (which its own field description
  already anticipated), and Phase 5's in-thread Triton replies need re-planning. Two further
  traps if it is ever revisited: `thread.threadKey` is scoped **per Chat app**, so a space
  also written to by a human or another app contains threads our threadKey cannot address;
  and `messageReplyOption` is *"Only supported in named spaces"* and is ignored entirely
  when responding to user interactions.

## CQ-1: human attribution, and what it costs

**Resolved 2026-08-08 in favour of human attribution.** The coworker relay authors messages
as the real person through domain-wide delegation. Two consequences you will meet in the
code:

**1. `createMessageNotificationOptions` is plumbed but never used by the relay.** The
parameter exists on `messages.create`, and it is confirmed to require **app**
authentication — which is mutually exclusive with authoring a message as a human.
`NOTIFICATION_TYPE_SILENT` is therefore unavailable to the relay.
`client.build_create_message_call()` **raises** if notification options are passed under
`AuthIdentity.USER`, so the trade-off cannot be quietly re-made in Phase 4 by someone who
only wanted to suppress a duplicate ping. It stays plumbed because Triton's replies *are*
app-identity and may legitimately use it.

**2. Locked decision #3 now reads:** *"exactly two notifications are fired by ERPNext;
native Google Chat client users additionally receive Chat's own, which is expected and not
a defect."* A user with the Chat app open gets Google's notification as well as ERPNext's.
That is the accepted price of the message saying the right person's name. Do not open a bug
for it; do not try to fix it with `spaceNotificationSetting.patch` without re-opening CQ-1
with a human.

Two more consequences of user authentication, worth knowing before they surprise you:

- **Text only.** No `cardsV2` under user auth. Fine — Triton's rich replies *should* be
  bot-badged, and they are the app identity's job.
- **Editing or deleting a human's message requires impersonating that human.** App auth may
  only touch messages the app itself created. An edit retry that drifts to a different
  principal fails with a 403 that reads like a scope problem.

**The second half arrived in v1.279.0.** Everything above described two identities, and the
relay implemented one: `build_client` hardcoded `AuthIdentity.USER` and `_subject_for` said
*"there is no app-identity fallback"* — true of a coworker mirror, and read for months as
though it were true of all outbound. Triton's replies now carry
`Chat Relay Job.auth_identity = APP`, decided from `sync_origin` at enqueue time and frozen on
the row like `impersonate_user`, for the same anti-drift reason.

Two guards stop applying to `APP`, and both are the point rather than a concession:

- **No subject.** The app grant is the two-legged service-account flow with no `sub` claim.
  Passing one now raises rather than being ignored — a subject that silently does nothing is
  how somebody later concludes the reply is attributed to a person.
- **No membership row.** A Chat app is *installed* in a space, not a member of it, so
  `_require_joined_author` would defer every Triton reply forever while reporting a membership
  sync that is not late for anybody. That is exactly what production showed.

**What this buys is a Workspace licence.** A `USER` write needs a real, licensed account that
has joined the space. The bot has neither, and the previous plan — create one — meant paying a
seat so a message nobody attributes to a person could be posted by a fake person. The App badge
is the price, and on a bot reply it is not a price at all.

---

## The module-map trap: `modules.txt` is not enough on an installed site

**This module shipped and installed nothing, and the deploy said success.** v1.261.0 merged
on 2026-08-09 with the `Chat` line in `modules.txt`, ten DocType JSONs on disk and three
patches; `bench migrate` exited 0 having created no table, no `Module Def` and no `Chat
Settings` row. Read this before you add module #30.

`frappe.model.sync.sync_for()` does not read `modules.txt`. It iterates
`frappe.local.app_modules`, a snapshot built **once** in `frappe.init()` by
`frappe.setup_module_map()` — which reads `modules.txt` only when the Redis key `app_modules`
comes back empty. Nothing in `bench migrate` rebuilds it: `SiteMigration.setUp`'s
`frappe.clear_cache()` deletes the key but does not call `setup_module_map` (only
`clear_global_cache()` does), so the map stays as `init` found it for the whole process. This
deploy pipeline then makes staleness likely rather than exotic — `infra/cloudbuild-deploy.yaml`
is `reset → bench migrate → bench build → FLUSHDB both redis → restart`, so the cache flush
happens *after* the migrate and the map is whatever a pre-reset process (usually the
once-a-minute scheduler tick) last wrote: the previous release's module list. A module that is
new in the release being deployed is invisible; its folder is never walked; nothing raises,
because there is no file to fail on. It is a race, which is why Fleet Maintenance and Package
Dispatch installed first time while Product Configurator (+5 days), Offsite Backup (+1 day) and
Training (+12 minutes, one redeploy) did not.

Two corollaries worth having in your head, because both invert the obvious guess:

- **A `Module Def` row is a consequence, not a precondition.** Nothing validates it at import
  time — model sync runs with `ignore_links`, `ignore_validate` and `ignore_mandatory` — and
  `DocType.on_update` → `make_module_and_roles` *creates* the row when the module's first
  DocType imports. `add_module_defs` exists but is called only from `install_app`, never on
  migrate. So a hand-written `Module Def` fixes nothing, and a declarative
  `<module>/module_def/<name>.json` fixes less than nothing: `module_def` is not in
  `IMPORTABLE_DOCTYPES`, and it would have to be found by walking the folder that is being
  skipped. (Four other modules ship such a file. All four rows in production have live insert
  timestamps that do not match the JSON, i.e. none of them was ever imported.)
- **A patch that no-ops is spent forever.** `patch_handler.run_all` filters the pending list
  against `Patch Log` and has no re-run path, so `add_chat_indexes` (11 composites skipped,
  tables missing) and `default_chat_settings` (returned at its `db.exists("DocType", …)`
  guard) were both recorded as executed with `skipped = 0`. They will never run again on that
  site. This is why a patch that touches its own module's brand-new tables on the very deploy
  that introduces the module needs an idempotent `after_migrate` twin — `ensure_chat_indexes`
  had one and it is the only reason invariant I2's index set exists in production today.

What ships as a result, all of it repo-side — production is never hand-patched:

| Piece | Where | What it does |
|---|---|---|
| `refresh_app_module_map` | `setup/module_map.py`, `before_migrate` | Deletes the `app_modules` key **then** calls `setup_module_map(include_all_apps=True)`, so `sync_all()` sees the current `modules.txt`. Runs before both patch phases. Order matters: rebuild without deleting and you re-seat the stale value. |
| `patches/refresh_module_map.py` | `[pre_model_sync]` | The same three lines as a one-shot, inside the same `@atomic` phase as `sync_all()`. Deliberately redundant with the hook; it is the guarantee that survives someone trimming a hook list. |
| `patches/finish_chat_bootstrap.py` | `[post_model_sync]` | New Patch Log identity that calls the **original** `add_chat_indexes.execute()` and `default_chat_settings.execute()`. The originals are untouched, so a fresh install still runs each exactly once. |
| `ensure_chat_settings` | `patches/default_chat_settings.py`, `after_migrate` | The dormancy seed's non-raising every-migrate twin, matching `ensure_chat_indexes`. |
| `tests/test_module_installability.py` | CI | Fails the PR if a module ships DocTypes without being registered, if a registered module has no package, or if the map refresh is not wired before model sync. |

**Adding a new module?** Nothing to do — the `before_migrate` hook covers it. Do *not* write a
`Module Def` JSON, and do not assume a `post_model_sync` patch can touch tables its own module
introduces in the same release without an idempotent `after_migrate` backstop.

---

## Tests

### Bench-free — these run in CI and block the PR

**The CI split is load-bearing and this repo has lost a suite to getting it wrong.**
`python -m unittest` silently cannot collect pytest-style function tests: the QuickBooks
suite ran nowhere and broke unnoticed for weeks. A new bench-free **pytest** suite needs its
**own `python -m pytest <one file> -q` step** in the `unit-tests` job (`name: Standalone
unit tests`) — never an append to a unittest module list. Each suite installs its own
`frappe` stub in `setUpModule`, which is also why several get their own step: sharing a
process is how they cross-talk.

| Suite | Style | CI step |
|---|---|---|
| `tests/test_chat_guardrails.py` | unittest | its own `python -m unittest erpnext_enhancements.tests.test_chat_guardrails -v` step (dotted module path, not a file path) |
| `tests/test_chat_gchat_client.py` | pytest | its own `python -m pytest erpnext_enhancements/tests/test_chat_gchat_client.py -q` step |
| `tests/test_chat_auth_claims.py` | pytest | its own `python -m pytest …/test_chat_auth_claims.py -q` step |
| `tests/test_chat_webhook_verify.py` | pytest | its own `python -m pytest …/test_chat_webhook_verify.py -q` step |
| `tests/test_chat_settings_budget.py` | pytest | its own `python -m pytest …/test_chat_settings_budget.py -q` step |
| `tests/test_module_installability.py` | unittest | its own `python -m unittest erpnext_enhancements.tests.test_module_installability -v` step — the guard on [the module-map trap](#the-module-map-trap-modulestxt-is-not-enough-on-an-installed-site) |
| `scripts/check_no_committed_secrets.py` | script | `python scripts/check_no_committed_secrets.py` — **blocking**, not advisory |

Phase 2 added twelve more, **one file per step for the same cross-talk reason** — each
installs its own `frappe` stub in `setUpModule`, and run together in one process they go
red for reasons that have nothing to do with the code under test. All pytest.

| Suite | What it pins |
|---|---|
| `tests/test_chat_sync_states.py` | Every legal transition, every illegal one raising, and the enum against the DocType JSON so the two cannot drift. |
| `tests/test_chat_sync_decisions.py` | **The decision matrix IS the echo-suppression specification.** Look here first when it goes red. The failure directions are asymmetric — a duplicated message is visible and annoying, a *suppressed* one is a coworker's message that silently never arrives — and the test names say which direction each defends. |
| `tests/test_chat_budget.py` | 32,000 bytes with a 4-byte emoji and a CJK string; never a split codepoint. |
| `tests/test_chat_ratelimit.py` | The GCRA arithmetic, and that the Lua issues no Redis command outside the allowlist the arithmetic needs. |
| `tests/test_chat_realtime_targeting.py` | Never a bare room, never `task_id`, always `after_commit`. |
| `tests/test_chat_fake_api.py` | The harness itself: quotas, `requestId` replay, tombstones, the event-before-response race. |
| `tests/test_chat_outbox.py` | `seq` allocation, the AST refusal of a Google call from a document event, edit + delete ordering. |
| `tests/test_chat_outbound.py` | State machine, token bucket, sweeper, kill switch, chaos 4–9. |
| `tests/test_chat_inbound.py` | The echo ladder end to end, the §4.D race, the defer timer. |
| `tests/test_chat_provisioning.py` | Three modes, revert cap, the converging membership diff. |
| `tests/test_chat_attachments.py` | Permission parity, upload cost, Drive links staying links. |
| `tests/test_chat_subscriptions.py` | Subscription lifecycle and the reconciliation sweep (chaos 10). |

Phase 3 added four more — one pytest suite and three plain-`node` scripts. The JS ones need
no runner and no `npm install`, which is the same shape as the repo's three existing JS
guards and the reason they run at all: a framework that needs `npm ci` is a framework this
deploy pipeline does not have, and one that is installed but never invoked is worse than
none because it looks like coverage.

| Suite | Style | What it pins |
|---|---|---|
| `tests/test_chat_api_contracts.py` | pytest | A deleted row never emits its body; the membership fragment never returns `""`; page sizes are clamped; search escapes the user's own `LIKE` wildcards; paging is keyset on `seq` and never `OFFSET` or a timestamp; the Python and JS route builders produce the same bytes; every whitelisted endpoint calls a gate. |
| `scripts/test_chat_citations.mjs` | node | The whole §4.10 degradation table: unknown `k` dropped silently, malformed tokens left literal, split tokens reassembled, **the tail buffer flushed on stream end**, `javascript:` refused, a `digest` rendered as a non-navigating pill, and — the row that lets this phase ship before Phase 5 — **no `citations` event renders identically to today**. |
| `scripts/test_chat_client_logic.mjs` | node | Routes and the three-tier restoration precedence (**the URL wins, always**), the handoff record's nonce and TTL, optimistic reconciliation in all four orderings, the read batcher's monotonicity and dwell, typing throttle and expiry, and the multi-tab presence union. |
| `scripts/test_chat_source_rules.js` | node | No `innerHTML` anywhere in the chat client; no Vue in the SPA bundle; the realtime event names matching between `realtime.py` and `socket.js`; **every cross-module name resolving** (esbuild fails on a bad import *path*, but a name that is never imported compiles to a global lookup and throws only in the browser); and `www/chat.html` loading bundles rather than raw `/assets` paths. |

All of them were **mutation-tested in both directions** when they were written: a planted
defect turns each one red naming the offender, and removing it turns them green. That is not
a formality here — between them they found **five real defects before the code ever ran**:

- a zero-initialised timestamp in the read batcher and another in the typing throttle, each
  swallowing its own first emission, both invisible against a real clock;
- three composers carrying only half the IME rule (`isComposing` without the legacy 229),
  i.e. fixed everywhere except the engines the fix exists for;
- `isComposingKey` used in the widget and never imported — a bare global reference that would
  have thrown `ReferenceError` at load and taken the whole assistant down on every desk page.

The CI runner installs only `httpx pytest jinja2`, so a bench-free suite must stub
`requests` the way `tests/test_triton_personas.py` already does. That is also why
`gchat/client.py` and `gchat/smoke_test.py` import `frappe` and `requests` *inside*
functions rather than at module scope — a module that cannot be imported without a bench is
a module whose pure helpers cannot be tested without one either.

**Prove a new step actually executes** by making one test fail on purpose and watching CI go
red. "I added tests" is not evidence.

### Bench-required — these are **not** in CI

There is no Frappe integration-test job in this repo (v16's test-record auto-generation
walks the whole ERPNext doctype dependency graph and aborts on environment gaps), so these
run only when a human runs them. Their value depends entirely on that happening and on the
result being recorded at the phase checkpoint.

```bash
bench --site <site> migrate                                          # on a FRESH site, not just an incremental
bench --site <site> run-tests --app erpnext_enhancements
bench --site <site> run-tests --doctype "Chat Message"
bench --site <site> run-tests --module erpnext_enhancements.tests.test_chat_permissions_bench
bench --site <site> run-tests --module erpnext_enhancements.tests.test_chat_attachments_bench
bench --site <site> execute erpnext_enhancements.chat.health.report
```

`test_chat_permissions_bench.py` is the security test CI does not run: a non-member is
denied on the list path **and** the single-document path **and** the report view;
`has_permission` returns an explicit boolean on every path including exception paths; and
the oversight role's `""` escape hatch is role-gated.

`test_chat_attachments_bench.py` is Phase 2's equivalent and it is bench-only for a reason
that cannot be stubbed: it needs a real `File` row on a real disk, a real `DatabaseQuery`
and the real `download_private_file` route. Stubbing any of those asserts that the stub
works — which is exactly the reassurance §4.I says not to accept, given Frappe's long tail
of reported issues where private files are more accessible than expected. **A human must
run it and record the result at the Phase 2 checkpoint.**

Two schema facts to confirm on the bench, because a patch that silently no-ops leaves the
design's central uniqueness claims unbacked:

```sql
SHOW INDEX FROM `tabChat Message`;   -- expect UNIQUE gchat_message_name, UNIQUE (room, seq),
                                     -- UNIQUE (room, client_message_id)
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://<erp-host>/api/method/erpnext_enhancements.chat.gchat.webhook.handle \
  -H 'Content-Type: application/json' -d '{}'
# expect: 401
```

### The smoke test

`gchat/smoke_test.py` is Phase 1's checkpoint gate — eleven steps against **real** Google
Chat, with `dry_run_mode` off:

```bash
bench --site <site> execute erpnext_enhancements.chat.gchat.smoke_test.run \
  --kwargs '{"as_user": "<a-real-employee>@<domain>", "with_user": "<second-employee>@<domain>"}'
```

It refuses to run in dry-run mode, never prints a token, proves `spaces:setup` idempotency
by re-running the identical call rather than assuming it, **stops and asks a human** whether
the message renders as the real person with no `App` badge (the API response cannot answer
that), prints post-delete resource behaviour verbatim, and deletes its test space —
orphan spaces are visible to real employees. It is safe to re-run.

**As of Phase 1 authoring it had never been executed.** Nothing it describes may be quoted
as evidence until a human runs it and pastes the transcript into the checkpoint.

---

## Open `VERIFY:` items this module carries

Ranked by which phase they endanger. Each is written where it applies as well as here.

| Item | Where | Endangers |
|---|---|---|
| ~~`spaceThreadingState`~~ **SETTLED 2026-08-09 — the API cannot create a threaded space.** Three independent confirmations (discovery `readOnly: true`; `spaces.setup`'s verbatim *"Spaces with threaded replies aren't supported."*; `spaces.patch`'s updateMask omits it). There was never a create-time decision to get wrong. `threading_enabled` stays 0 and the relay does not thread — see [what is deliberately lossy](#what-is-deliberately-lossy). *Residual:* which state an API-created named space actually lands in. `GROUPED_MESSAGES` is the only plausible reading and no document states it, so `provisioning.threading_state_of` **reads it back** off the created space into `Chat Room.gchat_threading_state` rather than assuming. | `client.ThreadReply`, `sync/provisioning.py` | Phase 5 (decision #5's in-thread Triton replies) |
| **Is `clientAssignedMessageId` actually populated in a `messages.get` response?** The proto declares it `OPTIONAL` — not `OUTPUT_ONLY` — and the discovery doc does not mark it `readOnly`, so the design is not inverted; but **no Google document or sample anywhere shows it populated in a response**, and the one published event example is a human-sent message that would carry no custom id. **Blocks the echo ladder's primary path**: if it returns empty, rung 3 collapses onto the disabled fallback. `testing/fake_chat.py` carries a switch to return it empty precisely so that path stays testable. Settle with one live round trip — create with a `client-` id, `messages.get` it, print the field. | `sync/decisions.py` rung 3, `sync/inbound.resource_client_id` | Phase 2 correctness, Phase 3 rollout |
| **Shape B as an ordinary user.** A `validateOnly` `subscriptions.create` for `//chat.googleapis.com/spaces/-` under DWD returned **200** from the production VM on 2026-08-09, which settled the phase-blocking question. The test account is almost certainly a super-admin, and shape B needs one subscription **per coworker** — re-run it as an ordinary pilot user before rollout. Cheap, and the only thing that run does not prove. | `sync/subscriptions.py` | Phase 3 pilot |
| **The literal Chat 429 body.** The AIP-193 envelope family is confirmed by a live probe, but on a **401** — the 429's own `message` string and whether `details[]` is populated are not. The fixture's message string is commented as a guess. Falsification plan: the first real production 429 logs its raw body and headers verbatim and gets diffed against the fixture. Also: **treat `Retry-After` as absent** — it appears zero times on Google's Chat limits page and zero times in `google-api-python-client`. | `testing/fixtures.py`, `gchat/backoff.parse_retry_after` | Phase 2 backoff |
| **Every Pub/Sub payload in `testing/fixtures.py` is CONSTRUCTED, not captured.** Assembled from primary documentation on 2026-08-09; the docstring marks each field documented or inferred. Until the first real envelope has been diffed against `message_created_event()`, every assertion built on it tests **our own consistency**, not Google's. | `testing/fixtures.py` | Phase 2 inbound |
| **Whether DWD impersonation of a space *manager* can delete another member's message.** The `SPACE_OWNER_VIA_APP` deletion enum implies it; no page states the rule. | `sync/outbound._handle_message_delete` | Phase 6 moderation |
| The field **shape** of `createMessageNotificationOptions` was never read from the reference. It is transcoded as flat `createMessageNotificationOptions.<key>` query parameters, which is Google's general REST rule for a message-typed query parameter. Do not guess a nested shape. | `client.build_create_message_call` | Phase 4 |
| `requestId` has no documented character set, maximum length, or deduplication **window**. "Same requestId returns the same resource" is an optimisation, not the uniqueness guarantee — `unique(gchat_space_name)` and `unique(linked_doctype, linked_document)` are. | `client.validate_request_id` | Phase 2 provisioning |
| Post-delete resource behaviour: whether `deleteTime` / `deletionMetadata` come back and whether `showDeleted` returns tombstones. Decision #12's retained audit trail is designed against the answer. Smoke test step 8 prints whatever it observes. | `smoke_test._step_8_delete` | Phase 6 audit |
| Deleting a **space** is gated by `chat.delete`, which is deliberately **not** in `auth.RELAY_SCOPES`. The smoke test's cleanup mints its own token with it; if the Admin console never authorised that scope, cleanup fails and the space survives. | `smoke_test.CLEANUP_SCOPES` | Phase 1 checkpoint hygiene |
| What credential material a Cloud-console-configured Chat app actually holds for `chat.bot` calls, and whether the self-signed-assertion route is the supported one — or whether IAM Credentials `generateAccessToken` is preferred. | `auth.get_app_token` | Phase 5 (Triton's leg) |
| The exact v16 signatures of `frappe.db.add_index` / `frappe.db.add_unique`, and the exact `frappe.cache()` method names. Both are used against the running v16 by ~10 modules here, so the risk is bounded — but they are recorded as read, not as executed. | `patches/add_chat_indexes.py`, `auth._cache_get` | Phase 1 schema |
| The exact `frappe.logger(...)` signature on v16 and whether it accepts `allow_site`. Attempted first, bare form as fallback, rather than asserting a signature nobody here has run. | `client._get_logger` | logging only |
| That the v16 site-URL helper returns the external HTTPS origin behind the load balancer, not an internal address. The classic failure is `http://localhost` deep links in production pushes. | `links.build_message_deep_link` | Phase 4 |
| That a non-member gets **403** on another room's private attachment URL, on a real bench. The assertion now exists (`tests/test_chat_attachments_bench.py`) and **has still never been run** — CI cannot run it. | attachments convention, `sync/attachments.download` | Phase 3 |
| Whether Google has published stable Chat egress IP ranges by 2026. None were found at research time — which is why the webhook is JWT-verified and **never** IP-allowlisted (accepted risk R04-V15). | `gchat/webhook.py` | Phase 2 inbound |
| **CQ-10 is formally open**: whether a departed member keeps history up to `left_seq`. Answered **closed** in code until a human says otherwise — see [departed members](#departed-members-left_seq-grants-nothing-yet). | `permissions.py` | Phase 1 schema, Phase 5 gate |
| **`Chat Message.creation` may have no index.** ADR §F.19 lists it as "Frappe default", but that was inferred from a `tabNotification Log` observation and Frappe's MariaDB table template indexes `modified`, not `creation`. Settle with ``SHOW INDEX FROM `tabChat Message`;`` after `bench migrate`; if no key covers `creation`, add `("Chat Message", ("creation",), "creation_index")` to `INDEXES` in `patches/add_chat_indexes.py`. | ADR §F.19, `patches/add_chat_indexes.py` | Phase 6 retention purge |

### Departed members: `left_seq` grants nothing yet

**Divergence from ADR §F.5 — the ADR does not mention `left_seq` at all.** `grep left_seq`
over `0009-erpnext-google-chat-triton.md` returns nothing; §F.5's field table is 14 rows and
this is not one of them, and CQ-10's own text names a `left_on` *timestamp*, not a sequence
bound. The column is this package's invention.

`Chat Room Member.left_seq` therefore exists as **schema only**. It is stamped on leave and
it **grants no read access**: `permissions._message_scope_sql`, `_may_read_message` and
`chat_attachment_query` all require *active* membership, so a departed member sees nothing —
the same rule `Chat Room` already used. Departed-member visibility is deliberately **closed**
until CQ-10 is answered by a human.

The column stays because it is cheap now and expensive to add after data lands. **Do not
re-open the grant in a query, a report or a Phase 5 retrieval filter** — answering CQ-10 in
the access-widening direction is a decision, not a patch, and it belongs in the ADR first.

## Related reading

- [`decisions/adr/0009-erpnext-google-chat-triton.md`](../../decisions/adr/0009-erpnext-google-chat-triton.md) — the record. §F data model, §G sync protocol, §H notifications, §I Triton, §J infrastructure.
- [`decisions/adr/0009-appendix-a-widget-behavior-inventory.md`](../../decisions/adr/0009-appendix-a-widget-behavior-inventory.md) — what the existing floating Triton widget already does and must not break.
- [`decisions/adr/0009-appendix-b-implementation-plan.md`](../../decisions/adr/0009-appendix-b-implementation-plan.md) — the file-by-file plan for Phases 1–6, with a risk rating per row. **What lands when.**
- [`api/README.md`](../api/README.md) — the whitelisted-endpoint map, including `api/chat.py`'s indentation.
- [`fixtures/README.md`](../fixtures/README.md) — why the roles here are a patch and not a fixture.

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

---

## Phase status — read this before you go looking for something

**Phase 1 is complete: schema, auth, transport client, inbound webhook.** That is all. If
you came here expecting a chat window, it does not exist yet.

| | State |
|---|---|
| DocType schema, indexes, `Chat Settings`, roles | **built** (Phase 1) |
| Keyless domain-wide-delegation auth (`gchat/auth.py`) | **built** (Phase 1) |
| The one Google Chat transport client (`gchat/client.py`) | **built** (Phase 1) |
| JWT-verified inbound webhook (`gchat/webhook.py`) | **built** (Phase 1) |
| Permission hooks, deep links, feature-flag endpoint | **built** (Phase 1) |
| Outbound relay worker, token bucket, ingest, echo suppression, reconciliation | Phase 2 |
| Any UI at all — SPA, `www/` page, bundles, widget changes | Phase 3 |
| Notifications, Web Push, VAPID, presence, typing, read receipts | Phase 4 |
| Triton integration, retrieval gate, embeddings, digests | Phase 5 |
| Export, audit writes, drift reports, pilot rollout | Phase 6 |

**The system ships dormant.** `Chat Settings.enabled = 0`, `dry_run_mode = 1`,
`restrict_to_whitelist = 1` out of the box. With `enabled` off, `auth.get_delegated_token`
refuses to mint a credential — the master switch is enforced at the *bottom* of the stack
as well as the top, so a new caller added later cannot route around it.

Three things exist as *fields* with no *automation* behind them, deliberately:
space provisioning modes (`Chat Room.provisioning_mode`), relay bookkeeping
(`Chat Relay Job`) and inbound bookkeeping (`Chat Inbound Event`,
`Chat Event Subscription`). Phase 2 owns every one of them. Schema now, worker later, so
Phase 2 has somewhere to write on day one.

## The shape of it

| Path | What it is |
|---|---|
| `doctype/chat_room/` | DM / group chat / named space. Carries `gchat_space_name` (unique), the `linked_doctype` + `linked_document` pair for per-document spaces, and the four denormalised last-message columns (`last_message`, `last_message_at`, `last_message_sender`, `last_message_preview`) that make the room list a zero-join render. **The only chat DocType with a DocPerm row.** |
| `doctype/chat_message/` | The hot table. `autoname: hash`, `sort_field: creation` (never `modified` — editing an old message must not teleport it to the bottom of the transcript), `track_changes = 0`, no `in_global_search`. Unique on `gchat_message_name`, and on `(room, seq)` and `(room, client_message_id)` via the index patch. |
| `doctype/chat_room_member/` | Membership plus the read high-water mark (`last_read_seq` / `last_read_at`) — one row per `(room, user)`, never one per message. Leaving is **soft**: `is_active = 0` plus `left_seq`. `left_seq` is recorded but **grants nothing today** — see [departed members](#departed-members-left_seq-grants-nothing-yet). |
| `doctype/chat_mention/` | Child table of `Chat Message`. Small and bounded, which is the only shape a child table is right for. |
| `doctype/chat_attachment/` | One row per attachment, with `source` splitting `Uploaded` from `Drive Link` (the third option is `ERPNext`). Those are the literal Select values and the `SOURCE_*` constants in `chat_attachment.py` — Google's own `UPLOADED_CONTENT` / `DRIVE_FILE` are the *inbound* spelling and are translated by `GOOGLE_SOURCE_MAP`, so compare against the constants, never the Google names. That split is a permission decision wearing a data-model costume — see [attachments](#attachments-is_private--1-always). |
| `doctype/chat_relay_job/` | Outbox rows for Phase 2's relay. **Schema only; inert.** |
| `doctype/chat_inbound_event/` | Raw inbound events, unique on `pubsub_message_id`. **Schema only; inert.** That unique index is what turns Pub/Sub's at-least-once delivery into a no-op instead of a duplicate. |
| `doctype/chat_event_subscription/` | Workspace Events subscription bookkeeping — `expire_time`, `state`, failure counters. **Schema only.** An expired subscription is permanently deleted by Google and cannot be renewed, which is why its expiry is tracked in a row rather than assumed. |
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
| `links.py` | `build_message_deep_link()` — one function, three future consumers (the SPA router, the notification deep link, Triton's citation resolver). Written in Phase 1 precisely so those three do not diverge. |
| `../api/chat.py` | The whitelisted HTTP surface: `get_settings_public()`, feature flags only. Phase 3 fills it. |

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
      which is the single hook point Phase 6 turns into an audit row.
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

### Whitelisted endpoints

| Path | Guest? | What it does |
|---|---|---|
| `erpnext_enhancements.api.chat.get_settings_public` | no | Feature flags as booleans, from a positive allowlist. No identifier, no topic name, no service-account address — those are not secrets, but they are reconnaissance and a browser has no use for them. |
| `erpnext_enhancements.chat.gchat.webhook.handle` | **yes** (`allow_guest=True`, POST only) | Google's inbound interaction events. World-reachable, so the JWT is the only thing between it and an open relay: signature, **issuer `chat@system.gserviceaccount.com`**, audience byte-exact against `Chat Settings.interaction_endpoint_url`, and expiry — all **before** body parsing and before any DB access. Anything else gets `401`. Phase 1's handler logs the event type and returns `200` empty; dispatch is Phase 2's. |

### `boot`

`boot.py` adds exactly one flag, `ee_chat`. `extend_bootinfo` runs on every desk load for
every user, which is why the existing entries are all single booleans and why this one is
too.

### Entries this module deliberately does **not** register

Absences that a reader will otherwise assume are oversights:

- **No `doc_events` on any chat DocType.** Invariant I1: there is no code path that calls
  the Google Chat API synchronously from a document lifecycle hook. A hook that reaches
  Google turns the one-write-per-second-per-space budget into dropped messages during
  ordinary typing. Phase 2's relay is a *transactional outbox*, enqueued after commit —
  not a hook. `tests/test_chat_guardrails.py` asserts no `doc_events`-registered chat
  function reaches the transport.
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

### `frappe.DuplicateEntryError` on `gchat_message_name` is SUCCESS, not an error

`Chat Message.gchat_message_name` carries a `UNIQUE` index. That is invariant I2 made
**structural**: a duplicate insert fails at the database, not at an `if`.

Pub/Sub delivers at least once. Two workers can process the same redelivered event
concurrently. The correct inbound writer therefore:

```python
try:
    doc.insert(ignore_permissions=True)
except frappe.DuplicateEntryError:
    # Already ingested. This is the happy path, not an error: the unique index just
    # did the deduplication for us, atomically, with no race window.
    return
```

Consequences that follow from that, and are not optional:

- **Never `SELECT`-then-`INSERT`.** It is a TOCTOU bug at exactly the moment it matters —
  two workers, one redelivered event. Attempt the insert; let the index arbitrate.
- **Never log a `DuplicateEntryError` from this path as a failure.** An Error Log row per
  redelivery turns normal operation into a wall of noise and trains everyone to ignore it.
- The same reasoning applies to `Chat Inbound Event.pubsub_message_id` and
  `Chat Attachment.gchat_attachment_name`, both unique for the same reason.

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

`VERIFY:` on a real bench, that a non-member gets **403** on another room's private
attachment URL. Frappe has a long tail of reported issues where private files are more
accessible than expected, and this is cheap to check and expensive to assume.

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
  starts. **Phase 2 owns `chat/bucket.py`: a per-space token bucket in Redis, shared across
  workers, surviving restarts, charging uploads two tokens.** Retrying in a tight loop
  against one space converts a 429 into five 429s.
- Message bodies cap at **32,000 bytes** for the whole message — bytes, not characters, so
  an emoji-heavy message is a third of the length its character count suggests.

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

---

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
```

`test_chat_permissions_bench.py` is the security test CI does not run: a non-member is
denied on the list path **and** the single-document path **and** the report view;
`has_permission` returns an explicit boolean on every path including exception paths; and
the oversight role's `""` escape hatch is role-gated.

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
| `spaceThreadingState` is documented **Output only**, and `spaces.setup` states *"Spaces with threaded replies aren't supported."* Whether `messageReplyOption` does anything at all in a `GROUPED_MESSAGES` space is unresolved. Settle with one `spaces.get` against a throwaway space and set `Chat Settings.threading_enabled` only if it passes. | `client.ThreadReply` | Phase 5 (decision #5's in-thread Triton replies), Phase 2 threading |
| The field **shape** of `createMessageNotificationOptions` was never read from the reference. It is transcoded as flat `createMessageNotificationOptions.<key>` query parameters, which is Google's general REST rule for a message-typed query parameter. Do not guess a nested shape. | `client.build_create_message_call` | Phase 4 |
| `requestId` has no documented character set, maximum length, or deduplication **window**. "Same requestId returns the same resource" is an optimisation, not the uniqueness guarantee — `unique(gchat_space_name)` and `unique(linked_doctype, linked_document)` are. | `client.validate_request_id` | Phase 2 provisioning |
| Post-delete resource behaviour: whether `deleteTime` / `deletionMetadata` come back and whether `showDeleted` returns tombstones. Decision #12's retained audit trail is designed against the answer. Smoke test step 8 prints whatever it observes. | `smoke_test._step_8_delete` | Phase 6 audit |
| Deleting a **space** is gated by `chat.delete`, which is deliberately **not** in `auth.RELAY_SCOPES`. The smoke test's cleanup mints its own token with it; if the Admin console never authorised that scope, cleanup fails and the space survives. | `smoke_test.CLEANUP_SCOPES` | Phase 1 checkpoint hygiene |
| What credential material a Cloud-console-configured Chat app actually holds for `chat.bot` calls, and whether the self-signed-assertion route is the supported one — or whether IAM Credentials `generateAccessToken` is preferred. | `auth.get_app_token` | Phase 5 (Triton's leg) |
| The exact v16 signatures of `frappe.db.add_index` / `frappe.db.add_unique`, and the exact `frappe.cache()` method names. Both are used against the running v16 by ~10 modules here, so the risk is bounded — but they are recorded as read, not as executed. | `patches/add_chat_indexes.py`, `auth._cache_get` | Phase 1 schema |
| The exact `frappe.logger(...)` signature on v16 and whether it accepts `allow_site`. Attempted first, bare form as fallback, rather than asserting a signature nobody here has run. | `client._get_logger` | logging only |
| That the v16 site-URL helper returns the external HTTPS origin behind the load balancer, not an internal address. The classic failure is `http://localhost` deep links in production pushes. | `links.build_message_deep_link` | Phase 4 |
| That a non-member gets **403** on another room's private attachment URL, on a real bench. | attachments convention | Phase 3 |
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

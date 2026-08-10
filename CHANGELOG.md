# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.262.0] - 2026-08-09

Phase 2 of the ERPNext-owned employee chat system (ADR 0009): the message core and the
bidirectional sync engine. ERPNext is the source of truth; Google Chat is a mirrored
transport. Everything below ships **dormant** — `Chat Settings.enabled` is still `0` and
`dry_run_mode` is still `1`, so no code path in this release performs network I/O against
Google until somebody deliberately arms it.

### Added

- **The outbound relay**, as a transactional outbox rather than a queue. `Chat Relay Job`
  rows are written in the same transaction as the message they relay, and a scheduler sweep
  drains them. **The queue is a latency optimisation and never the delivery guarantee**,
  because the production deploy issues `FLUSHDB` against the queue Redis on port 11000 — an
  ordinary successful deploy destroys every enqueued-but-unrun job, silently, since the
  enqueue call already returned success. This has cost us missing Drive folders before. A row
  survives the deploy; the worst a lost enqueue costs is one sweep interval.

- **Echo suppression**, which is the reason this phase exists. A message ERPNext relays to
  Google comes back over the Workspace Events subscription as a `message.v1.created`. Stored
  naively it would be relayed again, and at one write per second per space the room fills with
  duplicates in seconds.

  The ladder is: structural dedupe on `unique(gchat_message_name)`; then
  `spaces.messages.get` on the resource name; then a check of the fetched
  `clientAssignedMessageId` against `unique(room, client_message_id)`. A hit is definitionally
  our own echo — bind the resource name, insert nothing, notify nothing, relay nothing.

  **The `messages.get` is not a fallback, it is the normal path**, and the reason is a
  configuration choice made in Phase 0: the subscription runs with
  `payloadOptions.includeResource: false`, because that is the only configuration with a
  seven-day TTL ceiling (four hours with resource data). The trade is one extra read per
  event, budgeted against the 3,000-reads-per-minute project quota, in exchange for renewing
  roughly twenty subscriptions once a week instead of every two hours — and for the fact that
  the twelve-hour expiry reminder cannot fire at all on a four-hour TTL.

- **The `messageId` / `requestId` split, and why only one of them is load-bearing.** Both are
  sent on every `messages.create`. `requestId`'s deduplication window is **undocumented** —
  absent from the REST reference, the discovery document, the proto comment and the guide, and
  AIP-155 explicitly leaves it to the implementer. Outside whatever that window is, a replay
  creates a second message. `messageId` carries a hard, permanent, server-enforced uniqueness
  constraint within the space with no expiry. So `messageId` is the durable idempotency key and
  `requestId` is a best-effort optimisation for an immediate network retry. No window figure
  appears anywhere in this codebase, because no Chat documentation supports one.

- **The inbound pipeline** — a bounded Pub/Sub pull driven by a one-minute `cron`, writing the
  untouched delivery to `Chat Inbound Event`, committing, acking, and only then enqueuing
  processing. Acking before the row commits loses events on a worker crash; doing real work
  before acking causes redelivery storms.

  A supervised streaming-pull worker would have lower latency and **is not available to us**:
  there is no `Procfile` in this repository. The bench's Procfile is generated on the VM by
  `bench` itself, `systemd` runs `honcho start` against it, and neither is under version
  control here — so a long-running worker is not a repo-committable change. ADR 0009 §G.4.2
  names the cron as the shipping-first fallback and prices it at up to a minute of added
  latency for a coworker message.

- **`Chat Message Revision`** — the edit and delete audit trail. Holds the body before and
  after every change, the actor, the origin and the origin timestamp, and it is **never**
  deleted by a user-facing delete. Its read permission is tighter than the message table's,
  not the same, because this is where superseded and deleted content lives.

- **`Chat Provisioning Run`** — the checkpoint row that makes a bulk org-structure sweep
  resumable. Space writes are capped at sixty per minute project-wide, so a first-run sweep
  over the whole org trips the quota immediately; an interrupted run must resume rather than
  restart, and it defaults to dry-run.

- **A Redis-backed per-space token bucket**, shared across workers. An in-process bucket is
  wrong the moment a second worker starts. The arithmetic is a pure function tested in the
  bench-free tier and the Lua script deploys exactly that arithmetic and nothing else.

- **An in-memory fake Chat API with fault injection**, used as a `transport=` double so the
  tests exercise the real builders, the real retry loop and the real `_request` contract. A
  client subclass would have tested the fake instead of the code.

- **The two seams later phases wire into**: `notify_new_message` and
  `mark_room_context_stale`. Both are stubs. The tests assert `notify_new_message` is called
  exactly once per genuinely new message and zero times for echoes, replays, reconciliations
  and outbound relays — which is the cheapest possible proof that the sync engine is not
  duplicating.

### Fixed

- **`frappe.DuplicateEntryError` is the wrong exception for a unique index, and ADR 0009 says
  to catch it in two places.** It is raised *only* for a primary-key collision — a duplicate
  `name`. Every other unique index, including `gchat_message_name`, `(room, seq)`,
  `(room, client_message_id)` and `pubsub_message_id`, raises `frappe.UniqueValidationError`,
  and the two share no base beyond `Exception`: `DuplicateEntryError` derives from Frappe's own
  `NameError`, `UniqueValidationError` from `ValidationError`.

  So the instruction to "treat `DuplicateEntryError` as success" — in ADR §G.3.1, in ADR §G.8
  Rule 2, and in `chat_inbound_event.py`'s docstring — **would not have caught the collision it
  exists to catch.** Structural dedupe would have failed open into a logged error on every
  at-least-once Pub/Sub redelivery, which is the precise opposite of the design's intent. Every
  insert on a chat dedupe key now catches both, inside `frappe.database.database.savepoint`,
  because a failed statement can otherwise poison the surrounding transaction. Verified against
  Frappe v16 `model/base_document.py:db_insert` on 2026-08-09.

### Changed

- **The relay does not thread, and `Chat Settings.threading_enabled` stays off.**
  `Space.spaceThreadingState` is **output only** — `spaces.setup` states verbatim that *"Spaces
  with threaded replies aren't supported"*, and `spaces.patch`'s `updateMask` does not include
  the field. The Google Chat API offers no way to create a threaded space at all, so this was
  never a "get it right at creation time" risk; there is no create-time decision to get wrong.
  Threading remains ERPNext-side only, which `Chat Message.thread_root`'s field description
  already anticipated: ERPNext holds the complete thread structure and only the Chat-side
  rendering is lossy. Phase 5's in-thread Triton replies need re-planning on this basis.

- **Membership writes and `spaces.setup` no longer count against the per-space write budget**,
  because they never did. `members.create`, `members.delete`, `spaces.setup` and
  `spaces.create` appear in no per-space quota row — memberships consume only the 300-per-minute
  project bucket, and a space cannot have a per-space budget before it exists. Charging them to
  the one-write-per-second space bucket would have throttled membership reconciliation roughly
  three hundred times harder than necessary.

- **`Chat Message.sender` is no longer required.** A Chat member who maps to no ERPNext `User`
  — an external participant, or a Workspace user without an account — must be stored, never
  dropped, because ERPNext is the record of what was said. With `sender` required those messages
  could not be inserted at all. The row now carries `sender` **or** `sender_email`, never
  neither, and the controller enforces exactly that.

- Subscription renewal now schedules from the `expireTime` **returned by Google**, never from a
  configured constant. `ttl` is input-only and the published ceilings are "up to" figures;
  nothing guarantees the server grants one. `Chat Settings.subscription_ttl_seconds` is a
  request, not a promise.

## [1.261.2] - 2026-08-09

### Fixed

- **Adding a module to `modules.txt` is not enough on an already-installed site, and the
  migrate that silently installs nothing still exits 0.** v1.261.0 shipped ten Chat
  DocTypes and installed none of them. The deploy reported success.

  `frappe.model.sync.sync_for()` iterates `frappe.local.app_modules`, **not**
  `modules.txt`. That map is snapshotted once in `frappe.init()` out of the redis key
  `app_modules`, and nothing in `bench migrate` rebuilds it — `SiteMigration.setUp()`
  deletes the key but never calls `setup_module_map()`. A migrate that begins with a
  stale snapshot walks the *previous* release's module list, so a module added in the
  release being deployed is never walked: no DocType imported, no table created, no
  `Module Def` made, and every `post_model_sync` patch touching those tables burns its
  Patch Log entry against a schema that isn't there. There is no exception to fail on,
  because there is no file to import.

  It is a race, not a certainty — this deploy `FLUSHDB`s the cache *after* the migrate,
  so whether the key is stale depends on what last wrote it. **It has fired before:**
  `Training` hit exactly this on 2026-08-01 and was papered over by a redeploy 12 minutes
  later that nobody connected to it. Chat is the second occurrence and the first one
  anybody diagnosed.

  The fix rebuilds the map in `before_migrate` (Frappe's `pre_schema_updates`, the only
  window before both patch phases and `sync_all()`), with a one-shot patch twin for the
  site that is already wrong, and the same refresh on `before_install` — core guards that
  path on the *app*, not the module, and `setup_module_map(include_all_apps=True)` maps
  every app on the bench whether installed here or not, so its condition is already false
  and no rebuild happens.

  Guarded by `tests/test_module_installability.py`, which fails if any module folder
  shipping DocTypes is missing from `modules.txt`, if the refresh leaves `before_migrate`,
  if the one-shot twin is demoted out of `pre_model_sync`, or if either bootstrap loses
  its backstop. Proven to go red on seven distinct mutations rather than assumed to work.

  Two things this episode corrected in the earlier record. The composite indexes were
  **not** lost — the `after_migrate` backstop that shipped in v1.261.0 created all eleven
  once the schema existed, which is direct evidence the two-entry-point pattern earns its
  keep. And invariant I2 was never at risk: uniqueness on `gchat_message_name` is a
  field-level `unique: 1` on the DocType, not something the patch creates. The only
  unrecovered damage was `Chat Settings` never being materialised, which had no backstop
  and now has one.

- **`after_migrate` does not run during `bench install-app`.** Core runs `before_install`,
  `after_install` and `after_sync` there and nothing else, while `install-app` writes the
  whole of `patches.txt` to Patch Log as already-executed. Both chat bootstraps justified
  themselves by the second half of that sentence while being registered only on
  `after_migrate`, so on a genuinely fresh site neither would ever have fired. Both are
  now on `after_install` as well.

## [1.261.1] - 2026-08-09

### Fixed

- **The load balancer closes idle connections after 30 seconds, and Terraform never set
  otherwise.** `timeout_sec` is now `3600` on both `production-vm-backend` and
  `spot-vm-backend` in `infra/configs/load_balancer.yaml`.

  On an external Application Load Balancer the backend service timeout is **not** a
  request deadline — it is how long an idle connection may live. At Google's 30-second
  default the LB closes Frappe's socket.io WebSocket every thirty seconds, taking
  presence, typing indicators, read receipts and live message delivery with it, and it
  504s Triton's SSE stream mid-answer. Both work perfectly against localhost, which is
  what makes it expensive to diagnose: the bug only exists where the load balancer is.

  **The only thing hiding it today is socket.io's own 25-second ping beating the cut by
  five seconds** — a margin nobody chose and nothing protects. Raise the ping interval
  for efficiency, or let a slow turn idle the socket, and it surfaces.

  Set in Terraform rather than with `gcloud compute backend-services update`, which is
  what the vendor runbook says. The gcloud form takes effect immediately and is then
  silently reverted by the next `terraform apply` — this LB is fully managed by
  `infra/load_balancer.tf` through the vendored `net-lb-app-ext` module, which already
  exposes `timeout_sec` as an optional field on `backend_service_configs`. **Requires
  `terraform apply` to take effect; it is not applied by the app deploy.**

  Spot matches production deliberately. A spot environment on the default timeout is not
  somewhere you can reproduce a realtime or streaming bug, and the first symptom of that
  is somebody concluding a WebSocket fault is environmental.

  Verify, and note this one command also answers three other open questions — whether a
  Cloud Armor policy exists outside Terraform, the edge policy, and session affinity:

  ```
  gcloud compute backend-services describe production-glb-production-vm-backend \
    --global --project=erpnext-465317 \
    --format="yaml(name,timeoutSec,securityPolicy,edgeSecurityPolicy,sessionAffinity)"
  ```

  Session affinity stays `NONE` and that is correct while each LB has exactly one VM NEG
  behind it — there is nothing to be sticky to. It becomes mandatory the moment either
  becomes a MIG with more than one instance, because socket.io's handshake is several
  HTTP requests before the upgrade and they would land on different backends.

## [1.261.0] - 2026-08-08

### Added

- **The `Chat` module: storage schema, Google authentication, a typed Chat API client
  and an authenticated inbound webhook** — Phase 1 of the ERPNext ⇄ Google Chat build
  described in ADR 0009. Ten DocTypes, three patches, four permission-hook pairs, one
  whitelisted endpoint, one guest webhook, five bench-free test suites and a blocking
  secret-scan CI gate.

  **It ships dormant on purpose.** `chat_enabled = 0`, `dry_run_mode = 1` and
  `restrict_to_whitelist = 1` with an empty allow-list, seeded by patch. Nothing in this
  release talks to Google until someone deliberately turns it on. There is also no relay
  worker, no subscription manager and no `doc_events` wiring — Phase 2 owns all of it.
  The client exists and is callable; nothing calls it.

  The parts that will look like mistakes to someone who wasn't here:

  - **The keyless delegation assertion is hand-rolled.** `google.auth.impersonated_credentials`
    still has no `subject` parameter and no `with_subject` method, so impersonating a user
    without a downloaded service-account key means building the JWT claim set by hand and
    signing it through IAM Credentials `signJwt`. This is not a gap waiting for the library
    to catch up — it is why a small crypto module exists, and it keeps us consistent with
    ADR 0004, on a host whose deploy pipeline has no `pip install` step at all.

  - **`gchat_message_name` is `Data` at length 255, not the default.** Frappe's `Data`
    defaults to `varchar(140)`, and a Google Chat message resource name has no documented
    maximum. At 140 the unique index would truncate-collide two genuinely different Google
    messages into one row — silent message loss, discovered long after the fact. The unique
    constraint is declared on the DocField rather than only in a patch, because Frappe's
    empty-string-to-NULL coercion only fires for fields carrying `unique: 1`, and without it
    the second un-relayed message in the table fails to insert.

  - **Chat DocTypes ship with an empty `permissions` array**, with exactly one exception:
    `Chat Room` carries a single bare `read` row for the `Chat User` role. Both halves are
    deliberate. No DocPerm is what stops the third-party MCP tool server — which lives in
    neither of our repositories and cannot be edited here — from reading chat tables through
    its generic document tools. The one exception exists because Frappe's v16 socket server
    permission-checks a `doc_subscribe` join by calling back into Python under the joining
    user's own session, and a zero-DocPerm room refuses that join *silently*, which would
    take realtime delivery down with no error anywhere.

  - **Messages sort by `creation`, never `modified`.** Sorting a transcript by modification
    time teleports an edited message to the bottom. The DocType also sets `track_changes = 0`,
    because the audit trail decision #12 requires is served by a purpose-built log whose shape
    we control, not by Frappe's `Version` table whose shape we do not.

  - **Departed-member visibility fails closed.** A `left_seq` column exists on
    `Chat Room Member` but grants nothing. What a person may still read after leaving a room
    is an open governance question (CQ-10 in ADR 0009) that has not been answered, and an
    adversarial review caught the first implementation answering it in code, in the
    access-widening direction. It now denies until a human decides.

  - **The outbox sweeper, not the job queue, is the delivery guarantee.** The production
    deploy issues `FLUSHDB` against the queue Redis, so any relay job enqueued and not yet run
    is destroyed by an ordinary release. `Chat Relay Job` is schema in this version; the
    scheduled sweeper that re-drives it is load-bearing rather than belt-and-braces, and
    Phase 2 must not replace it with a plain queue.

  - **Google Chat still notifies people, and that is the accepted answer.** Suppressing
    Chat's own notification requires app authentication, and app authentication makes every
    relayed message arrive from a bot with an `App` badge. The two cannot both be true, and
    there is no sender override outside one-time import mode. Resolved in favour of human
    attribution, so anyone running the native Chat client receives a third ping. Documented,
    expected, not a defect. `createMessageNotificationOptions` is plumbed through the client
    but deliberately unused.

  - **Exactly one module speaks HTTP to Google**, enforced by a test rather than by
    convention, because the containment is what makes the permission story auditable.

## [1.260.4] - 2026-08-07

### Added

- **ADR 0009 — the ERPNext ⇄ Google Chat ⇄ Triton employee chat architecture**, with
  Appendix A (the behaviour inventory of the existing floating Triton widget) and
  Appendix B (the file-by-file plan for the six implementation phases). Docs only:
  no executable behaviour changes.

  **Accepted 2026-08-08, resolving the trilemma below in favour of human
  attribution.** Locked decision #3 is restated project-wide: ERPNext fires exactly
  two notifications, and users running the native Google Chat client additionally
  receive Chat's own. The third ping is documented and expected, not a defect.

  Three findings in it are the kind this changelog exists to preserve, because each
  is a workaround whose reason is invisible from the code that will implement it:

  - **Human attribution and notification suppression are mutually exclusive in the
    Google Chat API.** `NOTIFICATION_TYPE_SILENT` — the only documented way to stop
    Chat firing its own notification for a message — requires *app* authentication,
    and under app auth Chat makes the app the sender and stamps an `App` badge.
    There is no `sender` override outside one-time import mode. So a relay that
    posts as the real human cannot post silently, and one that posts silently
    cannot post as the human. Two further constraints tighten it: a silent message
    can neither start nor reply to a thread, and app auth cannot upload attachments
    at all. The record carries this as the project's first open question rather
    than resolving it, because it changes the product and not just the code.

  - **Keyless domain-wide delegation is hand-rolled because `google-auth` cannot do
    it.** `google.auth.impersonated_credentials` still has no `subject` parameter
    and no `with_subject` method, so impersonating a user without a downloaded
    service-account key means building the JWT assertion by hand and signing it via
    IAM Credentials `signJwt`. This is not an oversight to be tidied away into the
    library later; it is the reason a small crypto module exists at all, and it
    keeps the deployment consistent with ADR 0004's no-vendor-SDK constraint.

  - **The outbox sweeper, not the job queue, is the delivery guarantee.** The
    production deploy issues `FLUSHDB` against the queue Redis, so any relay job
    enqueued and not yet run is destroyed by an ordinary deploy. A queue-only
    design would silently drop messages on every release. The scheduled sweeper
    that re-drives uncommitted outbox rows is therefore load-bearing rather than
    belt-and-braces.

  The record deliberately lives at `decisions/adr/0009-…` rather than the
  `docs/adr/0001-…` path its source prompt specified: this repo's ADR convention
  predates that prompt, `0001` is taken, and a second ADR namespace under `docs/`
  would have split the register. The deviation is stated in the record itself.

## [1.260.3] - 2026-08-07

### Fixed

- **A branch whose name merely contained "main" deployed to production.** Cloud
  Build branch filters are **regexes**, and `cloud_build_deploy_branch` defaulted
  to a bare `main` — so `app-deploy-prod` matched any branch containing that
  substring. It is now `^main$`, which is what `deploy_branch_regex` three lines
  above it in the same file has always used.

  This was not theoretical. The PR that carries this change was on a branch named
  `claude/ci-no-cancel-on-main`, and every push to it ran `app-deploy-prod`
  against `production-erpnext-standard-vm`: SSH in, reset the app to
  `upstream/main`, `bench migrate`, `bench build`, `FLUSHDB` on both redis
  instances, restart the bench.

  It stopped at `bench migrate`, which failed on the unrelated patch bug fixed in
  v1.260.1 — and because the remote command is a single `&&` chain under `set
  -e`, nothing after it ran. **A broken migration is the only reason a pull
  request did not flush the production job queue and restart the site.** Flushing
  that queue silently destroys pending background jobs, which this repo has been
  bitten by before.

  Requires `terraform apply` to take effect; the trigger's current filter lives in
  Google Cloud, not in this file.

- **CI no longer cancels a `main` run when the next merge lands.** Part of
  TASK-2026-01243, found while verifying that task's own fix (v1.253.1, PR #732)
  against real run history rather than a throwaway PR.

  That fix worked: across the 49 runs since it merged, duplicated commits fell
  from 16 to 1 and stray push runs from 12 branches to 2 — and all of the residue
  is transitional, on branches created before it. For a `push` event GitHub uses
  the workflow file *on the pushed branch*, so a stale branch still carried the
  unscoped `on: push:` until it caught up. Nothing after 23:28 that same evening.

  What it did not address is `cancel-in-progress` on `main`. Cancellation is
  right for a PR — a newer commit supersedes the older run, same branch, same
  question. On `main` it is wrong: every push is a *different* commit, so
  cancelling one to start the next means the superseded commit's CI never
  finishes. On 2026-08-07 four `main` runs were cancelled, three within 31
  seconds, because PRs were merged back to back.

  Each of those commits had passed CI as a PR — but a PR runs against the merge
  ref as `main` stood at that moment, so the run on `main` is the only check of
  the tree that actually resulted. Three merged states went unvalidated, on a
  branch that deploys automatically.

  `cancel-in-progress` is now `${{ github.event_name == 'pull_request' }}`:
  supersede on PRs, never on main. Back-to-back merges queue instead of
  collapsing, which is the correct trade for the deploy branch.

## [1.260.2] - 2026-08-07

### Fixed

- **Course coverage and pass thresholds always displayed as 0%.** Closes
  TASK-2026-01176. `get_course` groups the course policy under a `gates` key of
  its own, and `load()` has always stored it as `state.gates` — but all three
  readers in `player.js` took the values off `state.course`, where they have
  never been. `undefined` becomes 0 through `pct()`, and 0 renders as "no
  requirement", so the advisory panel told every learner they had nothing to
  reach.

  Advisory only: `evaluate_gates` is the authority and was always correct, so
  nobody was let through a gate they should not have passed. What was broken is
  that a learner could not see what was being asked of them.

  This is the eleventh defect of this exact shape in this module, and the second
  where the payload was *stored and never read* — `state.gates` was referenced
  exactly once in the whole file, which was the assignment itself.

### Changed

- **The boundary contract test now pins object identity for the `gates` group,
  and its allowlist stops lying.** The suite added in v1.256.0 was built for
  precisely this bug class and came within one step of catching it:
  `min_video_coverage` was sent, and the string appeared in the player, so a set
  comparison of names matched and passed. What differed was the object.

  Worse, its allowlist *recorded the defect as intended behaviour* — the reason
  fields read "read off the course object" and "read as
  `state.course.passing_score`" — and the module docstring claimed the
  wrong-object case "is every defect this module has actually shipped", which was
  already untrue when it was written. A reason field that documents a bug as a
  design is how an allowlist stops being a safeguard.

  `TestGateThresholdBinding` closes it: keys the server nests under `gates` must
  be read off a `gates` binder, and must be read *at all* — both halves, because
  this defect had both. Deliberately narrow, one envelope group pinned by path,
  rather than a general type checker.

  It earned itself immediately. A manual sweep found two call sites; the test
  found a third in `lessonPercent()` that the sweep had deduplicated away.

## [1.260.1] - 2026-08-07

### Fixed

- **Production migrations have been wedged since 13:09 today. This unwedges
  them.** `patches/rename_gallon_uk_uom.py` called
  `frappe.rename_doc(..., ignore_permissions=True, ...)`. That keyword does not
  exist on Frappe 16.30 — the signature is `(doctype, old, new, force, merge,
  ignore_if_exists, show_alert, rebuild_search)` — so the call raised
  `TypeError`.

  A patch that raises aborts the **entire** `bench migrate`, so this did not fail
  as one bad patch. It failed as *no patches at all*. On production the last
  successful entry in Patch Log is `purge_purchase_order_print_formats` at
  13:09:27; everything queued behind it never ran:

  - `rename_gallon_uk_uom` itself (`Gallon (UK)` is still there, `Gallon` is not)
  - `disable_unused_uoms`
  - `set_default_stock_uom_unit`
  - `repoint_notifications_to_group_emails`
  - `rename_payment_step_title` (v1.260.0, shipped hours later)

  So the hand-off step still reads "Receive Customer Payment" in production, and
  will keep doing so until this merges — the rename was fine, it just never got
  to run.

  `force=True` already does what the bad keyword was reaching for: it skips the
  permission and link checks a migrate has no user to answer.

  Found by the `app-deploy-prod` Cloud Build check on an unrelated PR, which is
  the only reason it surfaced today rather than at the next deploy.

### Changed

- **`test_uom_cleanup` now checks the call signature, not just the call.** The
  existing assertion was `assertIn("frappe.rename_doc(", source)` — true of a
  call with a keyword that does not exist. Nothing else could have caught this
  either: ruff's F821 sees undefined *names*, not wrong *keywords*, and the
  bench-free suites never import frappe.

  The Frappe 16.30 parameter list is now written down and checked by AST against
  **every** `frappe.rename_doc` call in the app, not only this patch's. The other
  six call sites were swept and are clean.

## [1.260.0] - 2026-08-07

### Added

- **A follow-up reminder button on the initial-payment step.** Step 5 was the
  only actionable hand-off step with nothing to *do*. That is deliberate — it
  carries `sla_business_days = 0` and is excluded from overdue escalation,
  because when a customer pays is not something anybody here can be nagged into
  fixing, and nagging about it only teaches people to filter the emails. But the
  result was a step you could look at and not act on.

  The button schedules the next chase: a core **Reminder** (the same record the
  bell icon's "Remind Me" creates, so it fires through Frappe's own cron and
  needs no delivery machinery of ours), defaulting to **two business days** out.
  Business days are counted by the same `add_working_days` the step due dates
  use, so a Thursday click lands Monday rather than Saturday, and the hand-off
  Holiday List is honoured.

  It is a manual chain by design, not a recurrence: each reminder that fires
  brings you back to set the next one. Rescheduling replaces the outstanding
  reminder rather than stacking — but only un-fired ones, so the record of
  chases already made survives. When one is scheduled, the date shows beside the
  step, which is the only place it is visible given the step has no due date.

  The reminder is addressed to the **Finance & Accounting Manager** resolved for
  that project, not to whoever clicked — a PM chasing a project can put it on the
  right person's list. That resolution goes through the existing
  `_resolve_responsible`, so "who gets reminded", "who gets nagged" and "who may
  tick the step" all read the same setting and cannot drift. Two whitelisted
  endpoints: `get_payment_followup` (read-only, what the button shows) and
  `schedule_payment_followup` (gated on write access to the Project, since it
  writes a Reminder for another user).

- **`tests/test_payment_followup_wire.py`** (9 tests, its own CI step). Static:
  parses the Python with AST and the JS with regex, imports neither. The suites
  that exercise the real behaviour need a bench and never run in CI, so the seam
  is pinned here instead — a renamed endpoint or a dropped `STEP_ACTIONS` entry
  raises nowhere, the button simply stops working in production.

### Changed

- **Hand-off step 5 renamed from "Receive Customer Payment" to "Initial Payment
  From Customer."** `step_title` is *copied onto each project's child rows* when
  the process is seeded rather than looked up, so renaming the template alone
  would leave the site showing two names for the same step depending on which
  project you opened — 88 rows across 85 open projects. `rename_payment_step_title`
  migrates them, modelled on `rename_handoff_ar_role`.

  Nothing here fails silently the way the role rename would have: every consumer
  of `step_title` only displays it. That was **verified rather than assumed** —
  the one title-based match in the codebase, `/task/i` in `process_steps.js`, is
  step 6 — because a title match hiding somewhere is what would have turned a
  cosmetic rename into a broken process.

## [1.259.4] - 2026-08-07

### Changed

- **The activity feed now shows on the first tab only.** Frappe renders the
  timeline into `.form-footer`, which is a *sibling* of the tab panes rather than a
  child of one — so it sits below whichever tab is open. Read the Details tab and
  the conversation is where it belongs; open Contacts, or Budget, or Dispatch, and
  the same wall of comments is still underneath, pushing the thing you came for
  off the screen. Closes TASK-2026-00353.

  **One shared script, not a per-doctype hook**, as the task asked: a
  `form-refresh` handler plus a scoped `MutationObserver`, the same shape
  `activity_log_numbering.js` already uses for global timeline work. Frappe
  rebuilds `.form-footer` after a save and when it constructs the tab list, and
  fires no event for either.

  Three decisions worth recording, because each is a way this could have been
  subtly wrong:

  - **The first tab is found positionally, not by name.** Several of our doctypes
    have already renamed Details to Overview or Summary, so a title check would
    have quietly stopped matching and the feed would follow you around again.
  - **A form with no tabs is untouched.** There the timeline is simply the bottom
    of the form, and there is no other tab for it to be wrong on.
  - **A mid-render form defaults to showing the feed.** Guessing "hidden" while
    the active tab is still being marked flashes it away and back on every paint.

  A class on the form wrapper rather than an inline style, so the rule lives with
  the rest of the desk CSS, survives Frappe re-rendering the footer, and is
  legible in devtools. `display: none` rather than `visibility: hidden` — the
  footer is tall, and leaving it occupying space trades a wall of comments for a
  wall of nothing. The comment box goes with it: a reply typed under the Budget
  tab still attaches to the same document, so offering it there is offering a
  second place to do one thing.

### Added

- `tests/test_activity_first_tab_only.py`, own CI step. The JS and the CSS are two
  halves of one behaviour and fail silently apart — a class nothing styles, or a
  rule nothing sets — so the suite pins both, that no second script toggles the
  same class, and that the first tab is located positionally.
## [1.259.3] - 2026-08-07

### Changed

- **Twelve Notifications now go to group addresses instead of role holders.**
  A role recipient resolves to whoever currently holds that role, so a
  departmental alert lands in a handful of personal inboxes — and stops landing
  anywhere the day somebody leaves or a role is reshuffled. A group address
  survives both. Closes TASK-2026-01240.

  | Notification | Was | Now |
  |---|---|---|
  | Compliance Flag on Call | Call Center Supervisor | `service_repair@` |
  | High Escalation Risk Call | Call Center Supervisor | `service_repair@` |
  | Maintenance Reading Out of Range | Maintenance Supervisor | `service_repair@` |
  | Maintenance Review Needed | Projects Manager | `operations@` |
  | Maintenance Contract Renewal Due | Projects Manager | `operations@` |
  | Maintenance Finalized | Accounts Manager | `billing@` |
  | New Lead Created | Sales Team | `sales@` |
  | New Opportunity | Sales Team | `sales@` |
  | New Project Created | Finance Team | `billing@` |
  | Project Type Change Alert | Finance Team, System Manager, Operations Team | `billing@`, `operations@` |
  | Task Completed | Operations Team | `operations@` |
  | New Fiscal Year Created | Accounts User, Accounts Manager | `billing@` |

  **Nine are deliberately untouched**, and the reasons are in the code rather than
  only in this entry: `Error Log` and `Integration Request` stay on System Manager
  (a role follows whoever is actually administering the system; a shared inbox
  nobody owns does not); `New ToDo Created`, `Remind Me Email` and `Material
  Request Receipt Notification` are addressed by document field and aimed at one
  person **by design**, which is the exception the task calls out; `Email Team on
  Opportunity Won` and the two Material Request alerts were already on groups.

  **`company@` is the whole-company mailing list**, so it is not a target for any
  routine alert. A test asserts it appears nowhere.

  **Split by how each is managed, on purpose.** The six repo-managed notifications
  are repointed in `fixtures/notification.json` and re-applied on every migrate.
  The other six were created in the UI and carry 2.4k–3k characters of HTML body
  each; fixturing them would drag ~15k characters of email template into the repo
  to drift against whatever anybody edits on the site next, so a patch repoints
  their recipients and leaves the templates alone. The consequence, stated rather
  than hidden: a later UI edit to those six is not corrected on migrate, whereas
  the fixtured six are.

### Added

- `tests/test_notification_recipients.py`, own CI step. It pins the two failure
  modes that are invisible when they happen — a routine alert routed to
  `company@` mails the entire company, and applying "point everything at groups"
  literally would strip the document-field recipients from the three
  notifications where a named individual is the whole purpose — plus the fixture
  invariant this repo has been bitten by before: a Notification fixture without an
  explicit `enabled: 1` imports **disabled** and re-disables itself on every
  migrate, which is how an alert ends up configured, present and silent.

## [1.259.2] - 2026-08-07

### Changed

- **The UOM `Gallon (UK)` is now just `Gallon`.** We mean the one gallon, and the
  disambiguating suffix ERPNext ships is noise on a purchase order. **Exact match
  only:** eight other seeded UOMs contain the word "Gallon", and
  `Pound/Gallon (UK)`, `Ounce/Gallon (UK)` and `Grain/Gallon (UK)` are
  *densities* — a substring rename would turn them into `Pound/Gallon` and quietly
  merge two different units in a system that prices by them. `frappe.rename_doc`
  does the cascade, rewriting every Link column database-wide rather than the
  handful we happen to know about today.

- **235 unused UOMs are disabled, leaving the five this business actually uses**
  (`Unit`, `Nos`, `FT`, `Square Foot`, `Gallon`). Closes TASK-2026-01238.

  **Disabled, not deleted, and the task said "purge".** Deleting looked right at
  first — UOMs are seeded by two *patches*, both already in `tabPatch Log`, so
  unlike a standard Print Format they do not come back on migrate. Counting the
  references turned it over: **235 of the 240 are named by `UOM Conversion
  Factor`**, ERPNext's seeded conversion matrix. Treating that as "in use" leaves
  nothing deletable; ignoring it and deleting anyway means tearing 235 rows out of
  ERPNext's own reference data. Meanwhile `enabled = 0` already does exactly what
  was asked — `frappe/desk/search.py` filters `enabled = 1` for any doctype with
  that field, so a disabled UOM disappears from every link picker, and the list
  "grows as needed" with one tick rather than a re-creation.

  That the mechanism is safe on a *referenced* record was already demonstrated
  here: `Nos` has been `enabled = 0` on this site while 281 Items use it as their
  stock UOM, with no ill effect. Disabling hides a UOM from new entry; it does not
  invalidate documents that already carry it.

  Usage is computed from `INFORMATION_SCHEMA` at run time, not hard-coded: there
  are **119** UOM-bearing columns across frappe, ERPNext and this app, and a list
  written today is wrong after the next app install.

- **`Unit` is the default UOM for new Items**, in the repo rather than only on the
  site. Closes TASK-2026-01239. Set through `Stock Settings.save()` and not
  `db.set_value`, which is the whole trick: `Item.stock_uom` carries no default of
  its own and reads `tabDefaultValue`, and the only thing that writes that row is
  `Stock Settings.on_update`. A direct write would leave the settings page reading
  "Unit" while every new Item still defaulted to the old value — correct-looking
  and wrong.
## [1.259.1] - 2026-08-07

### Removed

- **The two abandoned custom Purchase Order print formats.**
  `Purchase Order - Sapphire` is the only one we print. `Test Purchase Order
  Format` (a builder experiment last touched 2025-10-23) and `PO Test Print
  Format` are deleted by a one-shot patch. Closes TASK-2026-01237.

### Changed

- **The other three superseded PO formats are *disabled on every migrate*, not
  deleted — and the difference is the whole of this change.** `Purchase Order
  Standard`, `Purchase Order with Item Image` and `Drop Shipping Format` ship with
  ERPNext, and **standard formats re-sync from their app's JSON on migrate**. That
  is the same fact which forced `ensure_chrome_pdf_generator` to be an
  every-migrate hook rather than the one-off data fix somebody tried first. A
  patch that deleted them would appear to work and undo itself at the next `bench
  migrate` — the worst shape of failure, because nobody looks again until they
  print a PO weeks later. `disable_superseded_print_formats` re-applies
  `disabled = 1` after each sync, using `frappe.db.set_value` because
  `Print Format.validate` refuses ORM writes to standard formats outright.

- **`CHROME_EXCLUDED_FORMATS` is now empty, and deliberately kept.** Its only
  member was `Test Purchase Order Format`, excluded because its header was shorter
  than its body, which trips an unbounded index in frappe's
  `pdf_generator/pdf_merge.py`. The note there said the guard stays "until either
  the format is deleted or upstream bounds that index" — this is that deletion.
  The upstream bug is unchanged, so the mechanism and the explanation stay for the
  next format that meets it.

  The patch checks every column on the site that can name a print format (plus
  Property Setters, which name theirs in `value` rather than a Link column) and
  **logs and bails rather than deleting** anything still referenced — a dangling
  Link is worse than an extra row in a dropdown. Nothing referenced any of them at
  the time of writing; the check is for the next person. Idempotent.

## [1.259.0] - 2026-08-07

### Added

- **Four read-only assistant tools.** Each wraps an existing function and adds no
  business logic of its own. Closes TASK-2026-01188.

  - **`contract_signing_status`** — where contracts stand in the e-signature flow:
    the outstanding backlog, one contract's latest request, or every contract on a
    project or customer. `days_out` is measured from **`first_sent_on`**, not
    `sent_on`: every reminder re-sends and rewrites `sent_on`, so a figure driven
    by it resets to zero on each nudge and reports the most-chased contract as the
    freshest — the same trap `esign/tasks.py` documents for the weekly digest. The
    backlog uses `frappe.get_list`, not `get_all`, because it is the one view not
    anchored to a document the caller named.
  - **`kpi_dashboard_status`** — called without a department it returns only the
    Watch and Bad values across every department the caller may see. Nine
    departments in full is a context bomb: a hundred healthy numbers burying the
    four that are not. Every reply carries `source_freshness`, read off the
    snapshot document because `api/kpi.py::_serialize` does not include it — a KPI
    built from a feed that last updated a week ago is a plausible number that is
    not true today, and the value cannot say so itself. `refresh_kpi_dashboard`
    is deliberately not exposed: it rebuilds and commits.
  - **`project_pickup_route`** — the physical collection run for a project.
    Suppliers with no address are reported rather than filtered out, because
    dropping them produces a shorter route that is quietly wrong; "these vendor
    records need an address" is part of the answer.
  - **`training_course_catalog`** — the course-shaped third training tool:
    catalogue, gates, version history and aggregate assignment/attempt counts,
    with optional per-question `item_analysis`.

- **`training_compliance_status` gained `expiring_within_days`.** Recertification
  is the same compliance question one step into the future — an expiring
  certificate becomes an overdue assignment the moment the recert job runs — so it
  belongs on the tool that already answers "who is about to fall out of
  compliance" rather than in a fifth tool the model would have no reason to call.
  Already-lapsed certificates are included, because a window that silently started
  today would hide them behind a question that sounds like it covers them.

- `tests/test_assistant_tools_redaction.py`, with its own CI step.

### Security

- **Three of the four wrapped functions return more than a reader should see, and
  the tools strip it.** These are not tidying decisions:

  - `get_pickup_route_data` returns `api_key` — a live, **billable** Google Maps
    *browser* key. Its real caller is a dialog that has to draw a map; an MCP
    client is not a browser, and a key in a chat transcript gets quoted back,
    logged, and eventually pasted somewhere it can be spent. Stripped along with
    `use_routes_api`, the other half of a client rendering instruction.
  - Contract Signature Request carries the signing **evidence** — `token_hash` and
    `previous_token_hash` (the security boundary of the whole flow),
    `agreement_html` / `document_snapshot` (the text as signed),
    `signature_image`, `signer_ip_claimed` / `signer_ip_peer` / `user_agent`, and
    `consent_text`. The projection is written out rather than taken from the meta,
    so a field added later is excluded by default.
  - Training Certificate carries `verification_code`, which is what proves a
    certificate genuine to a third party. Not read.

  `item_analysis` is aggregate only and **withholds questions with fewer than five
  recorded answers**: a question answered by three people, reported as "33%
  correct", is a statement about one identifiable person's answer wearing a
  percentage as a disguise. The withheld count is reported, because an item
  analysis that silently omits its thin questions reads as though every question
  is well answered.

  The redaction tests check each sensitive field against the **live doctype JSON**,
  so a rename cannot leave a stale list quietly guarding nothing, and they strip
  docstrings before searching — every one of these fields is named in prose
  explaining why it is withheld, and a search that counted comments would find
  them everywhere and prove nothing.

## [1.258.1] - 2026-08-07

### Changed

- **`KPI Snapshot` grants read to the nine departments' roles, not System Manager
  alone.** A tool's `requires_permission` controls per-user *visibility* as well as
  execution, so the planned `kpi_dashboard_status` assistant tool would have been
  invisible to exactly the department managers who own the numbers — even though
  `api/kpi.py::_can_view` would happily let, say, an Accounts Manager read Finance.
  Closes TASK-2026-01187.

  **Written into the doctype's own `permissions` block, not a Custom DocPerm
  fixture as the task suggested.** `KPI Snapshot` is our doctype, so its
  permissions belong in its JSON, which `bench migrate` applies. The Custom DocPerm
  route is for doctypes we do not own; its export filter is scoped to Material
  Request and Purchase Order (`hooks.py`), and Custom DocPerm rows *fully replace*
  a doctype's standard perms, so that route would have meant re-authoring the
  System Manager row by hand for no benefit.

### Added

- **A row filter, because a DocPerm is doctype-wide and the widening above is not
  safe without one.** An Accounts Manager granted `read` on KPI Snapshot can read
  *every* snapshot — Sales, HR, Executive — through the desk list view or
  `/api/resource/KPI Snapshot`, whatever `_can_view` says, because that function
  guards two whitelisted endpoints and not the doctype. The task's own framing was
  that this widens who can *see the tool*, not who can see another department's
  numbers; that is only true with `kpi_dashboards/permissions.py` registered on
  `permission_query_conditions` and `has_permission`. `_can_view` remains the
  authority on which departments; the new module is the same rule expressed where
  Frappe enforces reads.

- `tests/test_kpi_snapshot_permissions.py`, with its own CI step, pinning both
  halves together — neither is correct alone. It also pins that **`HR User` is
  never granted**: it is one keystroke from the correct role, every employee on
  this site holds it, and the first pass at generating these rows did grant it,
  by regexing `DEPARTMENT_ROLES` out of the source and matching the comment that
  says in as many words not to use it. The generator now reads the assignment
  through the AST. Comments are not data, and on this doctype the difference was
  whether the whole company could read the HR KPIs.

## [1.258.0] - 2026-08-07

### Added

- **`tests/test_training_boundary_contract.py` — the test that catches this whole
  bug class instead of one instance of it.** Closes TASK-2026-01184.

  Twelve-plus defects in this module have shared one shape: a key one side writes
  and the other never reads, or reads under a different name. Nothing throws — a
  missing key in JavaScript is `undefined`, `undefined || []` is an empty list,
  `pct(undefined)` is 0, and all of those render perfectly. The suites either side
  of this one each pin *one* side of a seam, which is precisely why they stayed
  green through eight releases of the same defect.

  This enumerates both sides and diffs them. **Sent:** every `@frappe.whitelist()`
  reply in `api/training.py`, followed through subscript assignment
  (`payload["x"] = ...`), `dict(other)` copies, local names, and calls into
  `training/grading.py` and `training/progress.py` — because a key added by a
  delegate is on the wire just as surely as one written in the endpoint. **Read:**
  every `<binder>.<key>` in the four player files and `www/training.html`, where
  the binder holds a server reply. Anything on one side and not the other fails.

  Deliberate asymmetries live in two allowlists, and **each entry must carry a
  reason** — a test enforces that, and two more prune entries that have stopped
  being asymmetric. An allowlist nobody prunes stops being a list of exceptions
  and becomes a list of things that used to be true.

  Scope is the reply *envelope*. Content nested inside a reply — course cards,
  outline rows, lesson blocks — is deliberately out, because it already has its
  own comparisons in `test_training_boot_wire`. What the test cannot catch is the
  right key read off the wrong object; it is a set comparison, not a type checker,
  and it catches names that exist on one side only, which is every defect this
  module has actually shipped.

  Runs as its own CI step, like every other training suite, because the frappe
  stubs cross-talk in one process.

### Fixed

- **The quiz result's "7 of 10 correct" line has never rendered** — found by the
  new contract test on its first run. `quiz.js` reads `correct_count` and
  `question_count`; `submit_quiz` sent neither, and the line is guarded by
  `!= null`, so it silently drew nothing. Both are now counted from
  `per_question`, so they cannot disagree with the breakdown printed underneath
  them.

## [1.257.1] - 2026-08-07

### Fixed

- **A deliberately dormant training module told every visitor "Nothing is assigned
  to you right now."** `Training Settings.training_enabled` is the staged-rollout
  switch, and the server has always answered a dormant site with
  `{enabled: false, message}` from `_unavailable()`. The player read neither key,
  fell through to the catalogue, and rendered the empty-catalogue sentence —
  which is a statement about that *person*, is wrong, and is the one thing
  guaranteed to stop them asking why the page looks empty. Closes TASK-2026-01181.

  The dormant view renders the **server's** message rather than a second copy
  written here: the server is the only side that knows whether the module is not
  open yet or off for maintenance, and a duplicate sentence in the client is a
  second thing to keep true. The check is an explicit `=== false`, so a payload
  that omits the key cannot black out a working site — absent means "the server
  did not say", which is not "off".

  Checked before any deep link, deliberately. `get_course` throws once the runtime
  gate refuses, so a bookmarked course URL opened on a dormant site would have
  shown an error page where the server had a sentence ready for the learner.

### Changed

- `tests/test_training_boot_wire.py`'s boot-key scan now unions in `_unavailable()`
  — the *other* shape that one call can return. It previously read only the
  happy-path dict literal, so `b.message` looked like a key the server never
  sends. The boundary contract test (TASK-2026-01184) has to generalise this: a
  function's returned keys are the keys of everything it can return, including
  from the helpers it delegates to.

## [1.257.0] - 2026-08-07

MINOR rather than the PATCH the tasks called for: two new Training Settings fields
ship here. The tasks assumed the fields existed and only the boot payload was
missing them — see below.

### Added

- **`Training Settings.max_playback_rate` and `Training Settings.doc_min_dwell_seconds`.**
  Both tasks describe these as settings the boot payload forgot to send. Neither
  field existed. The client had been reading settings nobody could set, falling
  back to constants, for as long as both features have shipped. Closes
  TASK-2026-01182.

  `max_playback_rate` can only *tighten* the speed menu: `video.js` still clamps
  to 1.25 whatever arrives, because `progress.clamp_new_seconds` truncates claimed
  seconds at elapsed × 1.25. A higher setting would not raise the ceiling — it
  would silently cost a learner watch time and earn them an integrity flag for it.

### Fixed

- **PDF and Downloadable File blocks recorded nothing at all.** `blocks.js` has
  sent `kind`, `played`, `claimed` and `ack` since the document blocks were
  written; the server read none of the four. Dwell credited zero seconds and the
  explicit "I have read it" — the module README's documented substitute for
  per-page tracking, and the thing an auditor would actually be shown — was stored
  nowhere. `player.js` then read `response.ack` back off a reply that had never
  carried it. Closes TASK-2026-01178.

  `_normalise_beat` now translates the document vocabulary onto the one the engine
  speaks: `played` → `intervals` (same half-open encoding, different word),
  `claimed` → `claimed_seconds`. That second rename matters more than it looks:
  `record_heartbeat` cross-checks the claimed total against the seconds it derives
  from the ranges — the check that would have caught the v1.235.0 half-open
  misread three releases earlier — and for document blocks it was comparing
  against a key that was never sent, so it silently checked nothing.

- **The document dwell target is the server's, not the payload's.**
  `_resolve_duration` trusts the client's `duration` for want of anything better.
  That is safe for video, where the Training Video Asset row carries a verified
  duration and a shrinking one is refused outright, and unsafe here, because a
  document has no asset to check against — a payload claiming `duration: 1` would
  reach full coverage after one second. `record_heartbeat` now takes the divisor
  from `doc_min_dwell_seconds`.

- **`ack` round-trips and survives a reload.** Stored on the block, returned on
  every beat, and set-only: a beat replayed out of the offline queue carries
  whatever was true when it was built, and the last one to land must not be able
  to withdraw a statement of fact recorded against a compliance record. The `0` is
  written on the first document beat so the gate can distinguish "opened, not yet
  acknowledged" from "not a tracked block at all" — `player.js` treats an absent
  `ack` as untracked and will not hold a learner on a gate nobody is evaluating.
  A video beat returns `ack: None`, which is what that check wants.

## [1.256.0] - 2026-08-07

### Changed

- **Steps 6 (Outline Tasks) and 7 (Hold Project Launch Meeting) no longer wait behind step 5
  (Receive Customer Payment).** The tracker used to have exactly one "current" step — the
  lowest-numbered Pending row — so a slow-paying customer stalled the PM out of outlining
  tasks or booking the launch meeting even though neither depends on money having arrived.
  Customer payment can take weeks; production work doesn't.

  `process_steps.py` replaces the single-current assumption with
  `_actionable_steps()`: every Pending step whose predecessors are all resolved, where step 5
  is exempted from *blocking* (not from anything else — it still escalates the same as
  before, just never gates what comes after it). Step 4 (send invoice) is unaffected and
  still gates normally; step 6 still has to finish before step 7 opens up. Four call sites
  moved from "the current step" to "every actionable step": `_refresh_due` (each newly
  actionable step starts its own SLA clock rather than only the numerically-first one),
  `notify_step_transitions` (diffs actionable sets before/after a save via
  `get_doc_before_save()` so each step's owner is notified exactly once, the moment their
  step opens up — not step 5's owner re-pinged every time something downstream of it later
  changes), `deliver_step_notice` (now targets an explicit `step_name` for "up" notices, not
  just "overdue" ones), and `escalate_overdue_steps` (re-nags every overdue actionable step
  per project, not only the first).

  `process_steps.js` mirrors the same rule client-side (`actionable_steps()`) — the Project
  form's hand-off bar can now show two action rows at once (e.g. "5. Receive Customer
  Payment" and "6. Outline Tasks...", each with its own due date, highlight and "Mark Step N
  Complete" button) instead of hiding step 6 behind step 5. The **Hand-Off Process Coverage**
  report's "Current Step" column is renamed **Active Step(s)** and now lists every actionable
  step per project (via the same `_actionable_steps`) instead of only the first pending one.
  The **Hand-Off SLA Compliance** report needed no change — it already reads `due_by`/
  `status` per row rather than assuming one active step per project, so steps 6/7 simply stop
  reading as "Not Started" (blocked upstream) once they have a due date of their own.

  New coverage in `tests/test_process_steps.py` (`TestActionableSteps`,
  `TestPaymentDoesNotBlockLater`) and a constant-shape check in `tests/test_handoff_gate.py`.
  These are `FrappeTestCase`-based and need a real bench to execute; verified by hand-tracing
  against the implementation and by `python -m py_compile`, not by an actual test run — see
  `run-tests` skill.

### Fixed

- **CRM-OPP-2026-00150 ("Millcreek City - DMX Repair") had `custom_handoff_gate_applies`
  stuck at 0 despite closing after the hand-off gate shipped**, so the Opportunity's
  Hold/Mark-Complete/Skip buttons never appeared and PRJ-00753 was created without the
  formal hand-off flow ever running. Root cause: the Opportunity transitioned to Closed Won
  at 2026-08-06 08:33:47, in the window before that day's deploy had applied the
  `custom_handoff_gate_applies` column and wired `stamp_handoff_gate` — so the schema guard
  in `stamp_handoff_gate` correctly no-opped (the field didn't exist yet), and the column
  later arrived with its bare default of `0`. The one-time backfill patch
  (`ensure_handoff_gate_fields`) only stamps rows that are still `NULL` and had already run
  before this Opportunity existed, so it never touched this record either — a deal can close
  in the gap between "the gate's fields land" and "the gate's own hook is live" and fall on
  the wrong side of the exemption with nothing left to retroactively catch it.

  Corrected by hand: `custom_handoff_gate_applies` set to `1`, and the hand-off recorded via
  the skip path (`custom_handoff_meeting_held=1` with a reason) rather than as a fabricated
  completion — no meeting was ever booked through the system for this deal, and marking it
  "held" would have recreated the exact retroactive box-checking PRO-0204 exists to prevent.
  One-off data correction; not scripted as a patch since it is specific to one record found
  by inspection, not a population that can be queried for safely (a deal can legitimately
  have `gate_applies = 0` for the intended reason — the pre-2026-08-06 backlog).

## [1.255.5] - 2026-08-07

### Fixed

- **"Passed – 0%" on full-marks work.** Re-opening a completed course hits
  `finish_attempt`'s already-finished early return, which did not carry `score`.
  `pct(undefined)` is 0 and the player draws what it is handed, so a learner who
  had scored 100% was shown a confident nought. Closes TASK-2026-01180.

  `finish_attempt` had **three** exits, each assembling its own dict, and the
  differences between them were invisible until you hit the right one: the pass
  carried `score`; the already-finished return did not; and the outstanding-gates
  return carried neither `score` nor `completion`, both of which the client reads.
  There is one `_finished_attempt_payload` assembler now, and the exits differ
  only in what they pass to it. A test counts the call sites, so a fourth exit
  that hand-rolls its own dict fails CI rather than shipping with a missing key.

  The re-opened path takes the score **off the record** rather than recomputing
  it — recomputing could quietly disagree with the completion certificate already
  issued against that attempt.

- **A missing score is now `None`, not `0.0`, and the player renders nothing
  rather than "0%".** Zero is a real score a learner can get, so a missing one
  dressed up as a zero is indistinguishable from a genuine nought out of ten.
  This matters beyond the fix above: an attempt predating `score_percent` still
  has no score to report, and saying nothing is the honest answer to not knowing.
  Same shape as the v1.217.0 certificate bug — present, plausible, wrong.

## [1.255.4] - 2026-08-07

### Fixed

- **Opening a lesson erased every other lesson's progress from the outline — and
  its own.** `get_lesson` sends the progress of the one lesson it was asked for,
  `{status, blocks, checkpoints, quiz}`; `player.js` assigned that over the slot
  holding the whole `{lessons: {...}}` map adopted at attempt start. Closes
  TASK-2026-01177.

  The second-order effect was the worse one. `lessonProgress()` reads
  `state.progress.lessons`, which the assignment left `undefined`, so it returned
  `{}` for *every* lesson including the one just opened — and a learner who had
  finished three lessons and reopened the first saw a course that had never been
  started. Nothing errored; `undefined` propagated politely all the way to a
  rendered zero.

  Merged on the client rather than reshaping the endpoint, per the task's own
  preference and because the file already speaks that shape: `mergeHeartbeat` and
  `recordQuizRun` both fold their replies into this same map with the same
  defaulting idiom. The sweep the task asked for found no other instance —
  `adoptAttempt` does replace `lessons` wholesale, but with the server's own full
  map, which is the authoritative one. A test now pins that `state.progress` is
  never reassigned to anything else.

- **`next_checkpoints` is read at last, and it closes a real hole in v1.255.2's
  checkpoint fix.** `get_lesson` has sent `{block_key: at_seconds}` since Phase 2
  and nothing had ever read it. That looked like dead weight until the arming path
  was rebuilt: beats do not start until roughly ten seconds of credited playback,
  and the mount-time `armNext()` can only reach a checkpoint within
  `CHECKPOINT_TOLERANCE` of the resume position. A checkpoint in the opening
  seconds of a video fell between the two — too late for the arm, too early for
  the first beat — and by the time a beat arrived the playhead was already past
  it, so it never fired. `next_checkpoints` seeds the video's re-arm at mount,
  which is exactly what the endpoint's docstring always said it was for.

## [1.255.3] - 2026-08-07

### Fixed

- **The quiz "Try again" button has never rendered, and the score breakdown has
  always been blank.** A learner who failed with two attempts still in hand was
  shown no way to use them. Four names disagreed across `submit_quiz`; none of the
  four raised anything, because a missing key in JavaScript is `undefined` and
  every branch that read one failed closed. Closes TASK-2026-01175.

  `canRetry` asked for `can_retry`, which nothing sent, then fell back to
  `attempts_remaining`, which no endpoint has ever sent either — so both arms were
  `undefined` and it returned false for everyone. `attemptsText` read the same
  absent key and returned an empty string. `renderReview` read `entry.earned`
  where grading sends `awarded`, so the per-question "3/5" never drew.

  **The client moved, not the server — which is the opposite of what the task
  assumed.** The task asked us to confirm nothing else reads `attempts_left` or
  `awarded` before renaming them, and something does, in both cases:
  `player.js` reads `attempts_left` on the lesson result panel, and grading reads
  `awarded` on its way to the Training Attempt Question row's `points_awarded`.
  Renaming either would have moved the break rather than closed it. The client
  names had no readers at all.

- **`can_retry` is now derived on the server instead of inferred on the client.**
  `quiz.js` was right to demand explicit permission and treat silence as no —
  offering a retry the server will refuse spends a learner's goodwill on a dead
  button — but nothing ever said yes. The rule is one line and belongs where
  `max_attempts` and the run count already are. Passing does not offer a retry:
  `best` keeps the highest score so a resit cannot cost anything, but "Try again"
  under a pass reads as though the pass did not count.

  `attempts_used` and `max_attempts` now ride along too, so the player can say
  "Attempt 2 of 3" — which `attempts_left` alone cannot phrase, and cannot phrase
  at all on an unlimited course, where it is `None`.

- **The builder's quiz preview omitted the point fields entirely**, so an author
  checking their per-question weightings saw the same blank breakdown a learner
  did, and a previewed failure offered no Try again. It now mirrors the runtime:
  `points`/`awarded` per question, plus `can_retry` and the attempt counters.

### Added

- Quiz-seam coverage in `tests/test_training_boot_wire.py`, including a test that
  `attempts_left` still has its second reader in `player.js` — pinned so that a
  future tidy-up does not rename it and reopen this from the other end.

## [1.255.2] - 2026-08-07

### Fixed

- **In-video checkpoints have never fired, on any attempt, since the endpoint was
  written — and there were three independent reasons, not one.** The anti-cheat
  machinery was inert in production: every attempt stored `checkpoints == {}`, and
  `require_checkpoints_answered` — a completion gate — stood on an event that could
  not happen. Closes TASK-2026-01174, TASK-2026-01179 and TASK-2026-01183.

  1. **The envelope.** `open_checkpoint` replies `{enabled, checkpoint}`, the same
     shape `get_lesson` and `get_quiz` use. `video.js` read `checkpoint_key` off
     the envelope itself, found `undefined`, and armed nothing. `st.armed` was null
     on every tick and `maybeFireCheckpoint` returned on its first line.

  2. **The field names.** Five of seven disagreed. The runtime sent `at`, `type`,
     `question`, `rewind`, `pause`; both consumers — `video.js` and the builder's
     preview harness — spelled out the Training Checkpoint field names. So the two
     halves independently agreed with the doctype and disagreed with the one
     function between them. The runtime moved, because a name that states its unit
     (`at_seconds`) beats one that saves four characters, in a module whose whole
     defect history is units and names.

  3. **Nothing re-armed.** This is the one no task had spotted, and fixing only
     (1) and (2) would have left checkpoints just as dead. `armNext()` runs once at
     mount, and `open_checkpoint` deliberately returns only a checkpoint the
     playhead has *already reached* — a reply naming a future timestamp would be a
     map of where to skip to. So something has to notice the playhead arriving, and
     that something is `next_checkpoint_at`, which the server has put on every
     heartbeat since the endpoint was written and which nothing read. A checkpoint
     at 0:30 of a 90-second video was unreachable. `applyBeatResult` now keeps it
     and `maybeFireCheckpoint` arms on arrival.

  Three keys left the payload rather than being renamed, each sent and read by
  nobody: `counts_toward_score` (scoring is server-side; there was nothing the
  player could correctly do with it), `rewind_seconds_on_wrong` (superseded, see
  below), and `pause_video` (the player pauses unconditionally when it opens the
  scrim, so there is no behaviour behind the flag to switch — restoring it is a
  player change, not a payload one).

- **Two sources of truth for where playback resumes after a wrong answer.**
  `answer_checkpoint` returns an authoritative `resume_at` and `rewind_applied`;
  `video.js` ignored both and recomputed the position from a
  `rewind_seconds_on_wrong` the payload never carried under that name — so the
  subtraction was against `undefined`, and **no wrong answer has ever rewound**.
  Renaming the key alone would have left two implementations of one decision that
  did not agree: `grading._checkpoint_result` rewinds only when an attempt is still
  left, the client rewound on any wrong answer. The client derivation is deleted.
  `answer_checkpoint` also bolted a raw `rewind` onto its reply — unread, and
  contradicting the `rewind_applied` beside it; that is gone too.

- **The builder's preview harness followed the player into the bug, on purpose,
  and has been brought back with it.** `training_builder.js` served checkpoints
  flat with a note explaining that it mirrored the player's misreading rather than
  the real endpoint — the right call while the player was wrong, and the wrong one
  now. It also carried `at` *and* `at_seconds`, a hedge against not knowing which
  the runtime meant, which is its own small evidence that the two names were a
  problem. It now matches `_checkpoint_payload` key for key, reports
  `next_checkpoint_at`, and returns `resume_at`/`rewind_applied` so an author
  testing a pin sees what a learner will.

### Added

- Checkpoint-seam coverage in `tests/test_training_boot_wire.py` (the module that
  already asks "does the player read the keys the server actually sends?"): the
  payload's field names, the envelope in both directions, the single rewind
  authority, the re-arm path, and a key-for-key comparison between
  `_checkpoint_payload` and `training_builder.preview_checkpoint` — the drift that
  caused this, now pinned.
- `tests/test_training_grading.py` pins what `require_checkpoints_answered` does
  with an empty checkpoints map. Silently waived and permanently blocked are both
  defensible readings and very different bugs, and nothing said which this was. It
  is neither, because "empty" is two situations: no checkpoints *authored* opens
  the gate (fail-open, so turning the flag on at course level cannot strand every
  lesson without pins), while checkpoints authored and none answered closes it —
  which is why shipping the arming fix also releases learners who were stuck behind
  a question they were never asked.

## [1.255.1] - 2026-08-07

### Fixed

- **"Hand-Off SLA Compliance" and "Hand-Off Process Coverage" 500'd on open —
  `ModuleNotFoundError`.** Both reports installed fine, listed fine, and failed the moment
  anyone clicked them:

  ```
  No module named
  'erpnext_enhancements.project_enhancements.report.hand_off_sla_compliance'
  ```

  Frappe never reads the directory to find a script report. It *computes* the import path
  from the record — `<app>/<scrub(module)>/report/<scrub(report_name)>/<scrub(report_name)>.py`
  — and loads the `.js` from the matching path. `frappe.scrub` replaces spaces **and
  hyphens** with underscores, so `Hand-Off SLA Compliance` resolves to `hand_off_sla_compliance`,
  with the hyphen contributing its own underscore. Both reports were written into
  `handoff_*` folders, which reads correctly to a human and is unreachable to Frappe.
  Renamed the directories and their `.py`/`.js`/`.json` files to the scrubbed names; the
  report records themselves are unchanged, so no rename patch is needed and the desk links,
  workspace shortcuts and `get_url_to_report()` calls all keep working.

  The same typo also broke the Friday SLA digest (`process_steps.send_weekly_sla_digest`),
  which imports the report module to render its numbers — but there the failure was
  swallowed by the surrounding `except Exception: frappe.log_error(...)`, so the email
  simply stopped arriving instead of erroring.

### Added

- **`tests/test_report_modules.py` — a bench-free guard for the above.** `bench migrate`
  validates none of this: a misnamed report folder installs cleanly and the defect is only
  found by a user opening the report, the same shape as the hyphenated-`www/`-controller
  bug that `scripts/check_www_controllers.py` guards. The test asserts, for every Report
  JSON in the app, that `report_name` matches the record `name`, that the module is in
  `modules.txt` and matches the containing directory, that the folder and every
  `.py`/`.js`/`.json` stem equal `scrub(report_name)`, and that each Script Report's module
  defines a top-level `execute()`. Wired into `ci.yml` as its own unittest step beside the
  DocType placement check.

## [1.255.0] - 2026-08-07

### Added

- **The WI-068 group-account remap now takes a `window` argument, and knows about the 2026
  backlog.** 315 draft Journal Entry lines ($154,602.92 across 15 group accounts, in 193
  entries, spanning 2026-01-01 to 2026-08-01) post to group accounts, and ERPNext refuses
  to submit a Journal Entry whose line names one. They block the 2026 half of the
  QuickBooks backlog GL posting that TASK-2026-01236 gates on.

  WI-068 scoped itself to `posting_date < 2026-01-01` **by design** — at the time only the
  pre-2026 backlog was being posted — so this set was left alone deliberately, not missed.
  `CUTOFF_DATE` is therefore untouched and `pre-2026` remains the default window: that run
  is already applied to production and its report is the record of what it did, so it has
  to keep reproducing exactly. The date range moved into a `WINDOWS` table instead, each
  entry carrying the population measured against production beside the dates it describes.

  **Widening the date range alone would not have been enough.** `52000 Service COGS`
  appeared in neither routing table, so 3 of the 315 lines had nowhere to go and the
  cutover would have failed on them *after* the remap reported success. Added as
  `52001 - Service COGS - General`, consistent with the `50001`/`51001` siblings (`52100`
  onward were already taken). It carries `0, 0.00` in the routing table because it has no
  pre-2026 lines, which is what keeps the `pre-2026` expected totals unchanged at
  1,813 / $724,230.37.

  `20000 Accounts Payable` needed no new child — it merges into `2110 - Creditors`, which
  the existing `MERGE_INTO_EXISTING` table already routes. But Creditors is a **Payable**
  ledger and ERPNext requires a party on every line posted to one, while the group parent
  does not. A partyless line is therefore legal where it sits and illegal where it is
  going, which would have traded a "cannot post to a group account" failure for a "party
  required" one at the same point in the cutover. All 10 lines carry a Supplier (verified
  2026-08-07) and the script now re-checks it per run, skipping the account with an error
  naming the offending rows rather than moving them into a different failure.

### Fixed

- **A re-run no longer mints ledgers the window does not need.** The routing table spans
  every window, so `_build_plan` acting on all of it regardless meant a `pre-2026` re-run
  would create `52001` for an account only 2026 ever touched. Child creation is now guarded
  on the window actually having lines for that parent, restoring "re-running any window is
  a true no-op".

### Changed

- **An ad-hoc `from_date`/`to_date` range reports its population instead of asserting it.**
  Named windows carry a measured expected line count and gross that the run checks before
  moving anything. An unmeasured range has nothing to be right or wrong about, so
  inheriting a named window's figures would fail loudly on correct data and teach the
  operator to ignore the one check that matters.

  Three tests cover the new logic and each was confirmed to **fail against the pre-change
  code**: removing the `52000` route, making the upper bound inclusive (which would put
  2026-01-01 in both windows), and letting an ad-hoc range claim to be measured. 182/182
  pass in the QuickBooks suite.

  Documented in the runbook's new *The 2026 window* section, which also records something
  that differs in kind rather than degree: `mapping._ledger_for_posting` redirects a group
  account to its `- General` child, and `20000` has none, so on a re-sync those 10 AP lines
  **park for manual review rather than reverting**. The remap is not undone either way.
  This is inherited from the pre-2026 run, where `10000 Accounts Receivable` merges into
  Debtors on identical terms.

## [1.254.1] - 2026-08-07

Follow-up to v1.254.0. An adversarial audit of that release — run after it merged —
found a defect in the circuit breaker itself, a test stub that was certifying it,
and a factual error in its changelog. All three are fixed here, and the five
behaviour changes v1.254.0 shipped without tests now have them.

### Fixed

- **`utils/error_throttle.py` wrote its counter to one Redis key and read it from
  another, so `reset()` was a silent no-op and `suppressed_count()` always
  returned 0.** Frappe's `RedisWrapper` namespaces keys only inside the `*_value`
  family: `set_value` / `get_value` / `delete_value` all route through
  `make_key()`, which returns `f"{db_name}|{key}"`. The raw redis-py methods —
  `incr`, `expire`, `get`, `delete` — are **not overridden at all** and take the
  key verbatim. v1.254.0 wrote with `incr` and read/cleared with
  `get_value`/`delete_value`, so the two families never addressed the same slot.
  (A second mismatch rode along on the same line: `get_value` unpickles, while
  raw `incr` stores a plain integer string.)

  The suppression itself always worked, because `incr` and `expire` at least
  agreed with each other, and neither broken helper had a production caller — so
  nothing user-facing regressed. What did not work was the operator promise in
  `reset()`'s own docstring: fix the cause, reset, see the next failure logged in
  full. That silently did nothing.

  This module needs `incr` for atomicity — a counter a race can undercount is a
  counter that lets a storm through — so it stays on the raw family and now does
  its own namespacing: `_signature()` builds `f"{site}|ee_error_throttle::{sha1}"`
  and all four operations use raw `incr`/`expire`/`get`/`delete` against it.

  That also fixes a consequence worth naming separately: because the old key
  skipped `make_key`, it was the only cache key in the app not scoped to a site.
  On a bench where several sites share a Redis they shared one throttle budget, so
  one site's storm could suppress another site's first, genuinely-distinct error —
  precisely the masking this module exists to prevent.

- **The test stub was more forgiving than production, so it certified the bug.**
  `_FakeCache` was a flat dict in which `incr`, `get_value` and `delete_value` all
  addressed the same unprefixed key with no pickling. That collapsed the very
  distinction the defect lived in, and `test_reset_restores_the_budget` passed —
  18/18 green — against code where `reset()` provably did nothing on a real bench.
  The stub now reproduces `RedisWrapper`'s asymmetry deliberately: `make_key` +
  pickle on the `*_value` family, verbatim keys and integer payloads on the raw
  one. Re-introducing the v1.254.0 key handling now fails five tests.

- **A factually wrong claim in v1.254.0's comment and changelog.** Both stated that
  `is_new()` "is itself defined as `not self.get("creation")`". It is not: on
  Frappe v16 it is `bool(self.get("__islocal"))` (`base_document.py:631`). The
  permission-hook change is still correct — "has no creation timestamp" is the
  right test *for those two hooks*, since all they need to know is whether there
  is a saved row whose owner to compare against — but the stated justification was
  wrong, and an equivalence claim like that is exactly the kind of thing someone
  copies somewhere it does matter. The comment now says what is actually true, and
  the 1.254.0 entry carries an inline correction.

### Added

- **`tests/test_error_log_followup.py` — 21 tests covering the five v1.254.0
  changes that shipped with none.** The two that had tests (date coercion, the
  throttle) were the two whose failure was loud. The five that did not were the
  quiet ones: a swallowed exception, a skipped seed, a missing retry, a permission
  check returning the wrong bool.

  - **Finance Calendar** — a 403 names sharing and a 404 names the calendar id,
    rather than both reporting a Google outage; a permanent refusal is cached so
    the next dashboard load does not re-ask; a *transient* 500 is deliberately not
    cached; and no failure shape reaches the caller as an exception.
  - **`ensure_training_categories`** — seeds on a healthy site, no-ops silently
    when the DocType is not installed yet, never re-seeds an existing category, and
    one bad record cannot abort a migrate.
  - **Drive retries** — the metadata read passes `num_retries`, and a 404 still
    propagates rather than being retried away (a deleted folder is permanent, and
    the caller flags it on the record).
  - **Both permission hooks** — neither raises on any plausible dict shape, and a
    saved record belonging to someone else is still refused. These pin the
    *behaviour* and deliberately do not assert equivalence with `is_new()`.

  Its own CI step, per the stub-isolation convention, and it stubs
  `googleapiclient` because the runner does not install it while both modules
  under test import `HttpError` at module scope.

  Every one of the four is mutation-checked: reverting the production change it
  guards fails the suite.

- **Six tests pinning the throttle's key agreement** in
  `tests/test_error_log_fixes.py` — one slot per counter, `suppressed_count` reads
  what the throttle wrote, `reset` actually clears it, the key is namespaced by
  site, two sites do not share a budget, and a missing site conf still throttles
  rather than crashing.

## [1.254.0] - 2026-08-06

A debugging pass over the production **Error Log** (78,648 rows). Four code
defects fixed, one latent one hardened, and a circuit breaker added so that a
single failing credential can never again account for 88% of the table. The
issues that are *not* code — expired tokens, unshared calendars, missing IAM
grants, absent binaries — are written up in
[`docs/error-log-runbook.md`](docs/error-log-runbook.md) with PowerShell and Bash
commands for each.

### Fixed

- **Every Task created through the desk failed to reach the shared Google
  Calendar.** `script_migrations/task.py` built its event payload with
  `doc.exp_start_date.isoformat()`. That field is a `datetime.date` once the row
  has been read back from the database, but on the in-memory doc that
  `after_insert` receives — built straight from the request payload — it is still
  a plain `str`, and a `str` has no `.isoformat()`. So the hook raised
  `AttributeError: 'str' object has no attribute 'isoformat'`, logged a "Google
  Calendar Sync Failed" row, and left an apologetic comment on the Task. 541 rows
  in total, 299 of them in the last month, still firing the day this was written.

  Dates now go through `frappe.utils.getdate`, which accepts either shape. Two
  further bugs behind the first one, neither of which had ever been reached:

  - The `else` branch fell back to `doc.get_formatted("creation")`, which returns
    a *display* string in the user's date format ("06-08-2026 16:09:03"). Google
    would have rejected it.
  - The payload put a date-only value in `start.dateTime`. Google requires a full
    RFC 3339 timestamp there; Task's expected start/end are Date fields with no
    time component. These are now `start.date` / `end.date` all-day events, which
    is also the honest representation — a task due Thursday is not a task due
    00:00 Thursday. All-day `end.date` is exclusive, so the end is stamped one day
    on; without that, every task rendered a day short and single-day tasks
    collapsed to zero length and vanished from the calendar grid.

- **The hourly Drive shadow sync aborted a customer at a time on transient Google
  500s.** The Drive API answers a plain metadata GET with `HttpError 500 "Unknown
  Error."` often enough that Google documents it as expected and tells clients to
  retry with backoff. We were not retrying, so each blip aborted one record's
  shadow sync and wrote an Error Log row — 61 in a month, every one for a folder
  that was healthy on the next attempt. The two read calls in the walk now pass
  `num_retries` to googleapiclient, whose built-in retry covers 429/500/502/503/504
  with randomised backoff. The existing 404 handling is unchanged: a *deleted*
  folder is still flagged on the record rather than retried.

- **`ensure_training_categories` crashed on any migrate that ran it before the
  Training DocTypes were installed.** `ensure_training_badges` had guarded on
  `frappe.db.exists("DocType", "Training Badge")` since it was written; the
  categories seeder never got the same guard. The two existence checks are not
  interchangeable — `frappe.db.exists("Training Category", name)` queries
  `tabTraining Category`, which survives from an earlier migrate, so it answers
  happily while the *DocType row* is still missing. `frappe.get_doc` then asks for
  a controller, Frappe reads a blank `module` off the absent row, falls back to
  Core, and raises `No module named 'frappe.core.doctype.training_category'` — a
  confusing way to say "not migrated yet".

- **Two Google integrations logged a full traceback per attempt for a permanent
  misconfiguration.** Search Console (`api/analytics.py`) returns 403 because the
  service account is not a user on the property; the Finance Calendar
  (`api/finance_calendar.py`) returns 404 because its configured id does not exist
  or was never shared with the service account. Neither is an outage and neither
  can be retried into working, but both re-logged 40 lines of traceback on every
  scheduled run and every dashboard load — 34 and 14 rows respectively. Both now
  detect the permanent statuses, log once (throttled) with the remedy rather than
  the stack, and return an error string that names the fix. The calendar also
  caches the refusal for 120s so a dashboard reload stops re-asking Google; the TTL
  is deliberately far below the 30-minute success cache, so an admin who has just
  corrected the id sees the widget recover in a couple of minutes rather than
  wondering for half an hour whether the fix took.

### Added

- **`utils/error_throttle.py` — a circuit breaker for `frappe.log_error`.** Error
  Log is a DocType, so every `log_error` is an INSERT, and a caller that fails
  inside a loop writes one row per failure. On 2026-06-15/16 the MDM retry loop hit
  a standing Miradore `401` and wrote **44,069 rows in about thirty hours** — 88% of
  the table — for what was in substance one fact: a credential had expired. A
  QuickBooks import storm added ~27k more over the next two days.

  `log_error_throttled` logs the first `limit` occurrences of a signature inside a
  `window` in full, logs the next one as a suppression notice, and drops the rest.
  Throttling is per-signature and takes an optional `key`, so a dead Miradore
  credential can never mask an unrelated Action1 failure — hiding a *second*
  problem would make this worse than the storm it replaces. A cache failure falls
  back to logging unconditionally: losing an error is worse than writing a
  duplicate.

  Applied at the four call sites that can repeat unboundedly (Task calendar sync,
  Drive shadow sync, GSC, Finance Calendar). Deliberately **not** a blanket
  replacement for `frappe.log_error` — a one-shot failure in a request handler is a
  fact, not a storm, and should still log every time.

- **[`docs/error-log-runbook.md`](docs/error-log-runbook.md)** — the operator half
  of this pass. Every Error Log signature that is *not* a code defect, with what it
  means, how to confirm it, and the fix in both PowerShell and Bash: the Search
  Console grant, the Finance calendar share, the `sa-training-media` GCS IAM
  binding, the Workspace SMTP relay IP registration, the missing `wkhtmltopdf` and
  Chromium binaries, and stale bench filelocks — plus a section on why the Error
  Log's 78,648 rows are *not* a retention problem (retention is configured at 90
  days and working; the table is ninety days of history that happens to contain
  one 44,069-row incident, and that incident ages out on its own in September).

### Changed

- **`has_permission` hooks for Managed Device and Travel Trip no longer call
  `doc.is_new()`.** Frappe hands these hooks a plain dict on some paths, and a dict
  has no `.is_new()` — it raises `AttributeError: 'dict' object has no attribute
  'is_new'` from inside a permission check, which surfaces as an
  unrelated-looking save failure. Both hooks now test `not doc.get("creation")`,
  which works on either shape. **Correction (see 1.254.1):** this entry originally
  claimed `is_new()` "is itself defined as `not self.get("creation")`". That is
  false — on Frappe v16 it is `bool(self.get("__islocal"))`. The change is still
  right for these two hooks, but not for the reason given here.
  Hardening in the same spirit as the `getattr(obj, "field", None)` custom-field
  reads: this is a latent defect on both hooks, and it is not the cause of the
  `Document Update Error` rows carrying that message — those come from
  `frappe_assistant_core`'s `update_document` tool, which is outside this repo.
## [1.253.2] - 2026-08-06

### Changed

- **Cleared the safe half of the ruff backlog: 429 findings → 73.** `ruff check --fix`
  applied 306 fixes across 96 files — trailing/blank-line whitespace (`W291`/`W293`),
  import sorting (`I001`), f-string conversions (`RUF010`, `UP030`/`UP032`), stale
  `# noqa` directives (`RUF100`), and dead `from __future__ import unicode_literals`
  headers (`UP009`). Only ruff's **safe** fixes were taken — `--unsafe-fixes` was not
  used — so nothing whose behaviour ruff could not guarantee was touched.

  Every one of the 56 bench-free CI commands was then run, each in its own process, and
  all pass. That mattered more than usual: `I001` reorders imports across 76 files, and
  `E402` sits on the ignore list precisely because this codebase does not always import
  at top of file.

- **`RUF001`/`RUF002`/`RUF003` are now ignored in `pyproject.toml` rather than fixed.**
  All 50 occurrences were deliberate typography — 30 en dashes in prose, 13 `×` in
  dimensions, 7 true minus signs in the water-engineering text. Auto-fixing them would
  have rewritten docstrings and user-facing strings into ASCII and quietly degraded the
  writing. The rules are suppressed with that reasoning recorded beside them; a
  genuinely confusable *identifier* is what `F821` — already a hard gate — is for.

### Notes

- **The remaining 73 are judgement calls, not bugs**, and deliberately left: `B905`
  (`zip()` without `strict=`) is a decision per call site, `E722` (bare `except`) is
  deliberate in this app's defensive handlers, and the rest are idiom preferences
  (`UP030`/`UP031`, `RUF005`/`007`/`012`/`015`/`046`/`059`, `B007`, `E701`, `E731`).
  Clearing those plus `ruff format` is what remains before `continue-on-error` can come
  off the lint job. Tracked as TASK-2026-01241 on PRJ-00580.
- `ruff format` was **not** run. It is a ~68k-line diff across 391 files that would
  conflict with anything in flight, and `CLAUDE.md` warns against it as a drive-by for
  exactly that reason. It wants its own PR, timed for a quiet moment.

## [1.253.1] - 2026-08-06

### Fixed

- **CI ran the entire workflow twice on every PR commit, and the two runs cancelled
  each other into false red X's.**

  `on:` listed a bare `push:` alongside `pull_request:`, so each commit on a PR branch
  fired the workflow twice. The two events carry different `github.ref` values
  (`refs/heads/<branch>` vs `refs/pull/<n>/merge`), so the concurrency group — keyed on
  `github.ref` — put them in *different* groups and deduplicated neither. Four jobs
  became eight, competing for runners on every push.

  That was merely wasteful until a GitHub Actions capacity incident on 2026-08-06 made
  it expensive. The doubled demand met a starved queue, `cancel-in-progress` cancelled
  the losers, and **a cancelled job makes its whole run report `failure`** — so PRs
  showed red X's for jobs that had never executed, on commits with nothing wrong at all.
  Both #728 and #729 carried those marks while being entirely healthy.

  `push` is now scoped to `main`, which keeps CI on the deploy branch without re-running
  the same commit twice; every branch here merges via a PR, so the `pull_request` run is
  the one that matters. The concurrency group now keys on the PR number, so cancellation
  means what it should — a newer commit superseding an older run for the same PR —
  rather than two events racing over one commit.

  Trade-off, stated plainly: a branch pushed with **no open PR** now gets no CI until
  the PR is opened.

  The deeper reason this was worth fixing is not the wasted minutes. A red X that means
  "a duplicate got cancelled" trains people to skim past red X's, which is the same rot
  as an advisory lint job nobody reads. CI signals are only worth having if a red one
  means something.

## [1.253.0] - 2026-08-06

### Added

- **Project Priority now ranks 1–30 instead of 1–10.** The `custom_project_priority` Select on
  **Project** offered ranks 1–10 while its sibling `custom_company_priority` already offered
  1–30, so a value stream with more than ten live projects had no way to order the tail —
  everything past rank 10 collapsed into "Not Assigned". The two fields now carry the same
  numeric range; the non-numeric options (`Not Assigned`, `Maintenance`, `Repair Visit`,
  `Delivery`) are unchanged and keep their existing order.

  Purely a fixture change (`fixtures/custom_field.json`), applied by `bench migrate`. Nothing
  downstream needed widening: the Projects Dashboard reads both fields' options from the
  Project meta rather than hardcoding them (`get_priority_options`), its sort weight parses
  any integer, and its badge hue already scaled across 1–30. Existing values are untouched —
  this only widens what can be selected.

## [1.252.0] - 2026-08-06

### Added

- **28 operational widgets across seven department dashboards.** Every department dashboard
  under `kpi_dashboards/workspace/` shipped with exactly one block on it — the KPI Cockpit.
  Finance was the sole exception, carrying six operational widgets since v1.244.0. So eight
  dashboards told each team how the last 30 days went and nothing about what to do this
  morning.

  Sales, Operations, Design, Production, Marketing, HR and Executive each get four widgets:

  | Department | Widgets |
  |---|---|
  | Sales | Speed to Lead · Stalled Deals · Hand-Off Backlog · Renewal Radar |
  | Operations | Today's Visits · Fleet & Device Health · Chemistry Alerts · Labor Capture Gaps |
  | Design | Design WIP · Awaiting Sign-Off · Hand-Off Readiness · Hydraulic Headroom |
  | Production | Build WIP & Aging · Milestone Slippage · Material Readiness · Hours vs Budget |
  | Marketing | Funnel Cascade · Channel Spend & CPL · Unsourced Leads · Source Health |
  | HR | Training Compliance · Time Capture · Headcount Movement · People Calendar |
  | Executive | Company Scorecard · Cash & Receivables · Bookings & Backlog · Risk Queue |

  **Live worklists, snapshot trends.** Anything actionable queries its source doctypes on
  load — a queue is only worth showing if it is true right now. Anything carrying a trend or
  a target reads the nightly `KPI Snapshot`. The entire Executive dashboard is on the
  snapshot side: a cross-department view exists to be compared across departments and across
  days, and a number that changes on every refresh can do neither.

  Where a KPI already counts the same population the widget reuses its threshold — stalled
  deals at 14 days, matching `stalled_opportunities`. A widget that disagrees with the number
  directly above it is worse than no widget.

- **`api/dashboard_widgets.py`** — the gating contract the Finance widgets established,
  generalised. `WIDGETS` maps each widget to its settings Check; the `widget_feed` decorator
  applies the department role gate and the toggle, returning `{"enabled": false}` so a block
  renders a muted notice instead of an error. Roles are **imported from `api/kpi.py`**, never
  re-declared, so a widget can never be visible to someone who cannot already see the same
  department's KPI Cockpit. Feeds take **no arguments** by design: nothing to validate, and
  no way for a caller to widen a query.

- **28 new Check fields on ERPNext Enhancements Settings**, grouped into seven per-department
  sections, all defaulting to `0`. This matches the Finance widgets and it means a
  `bench migrate` changes nothing a user sees until someone ticks a box — the new dashboards
  will look empty until then, which is the first thing to check when a widget "doesn't work".

- **`tests/test_dashboard_widgets.py`**, on its own CI step. Four things have to agree for a
  widget to work: the `WIDGETS` registry, the settings Check fields, the seeder's `BLOCKS` and
  `DEPARTMENT_DASHBOARD_BLOCKS`, and the shipped workspace JSONs. Every one of them fails
  *silently*. A placement naming an unseeded block renders an empty div — no error, no log
  line. A mistyped toggle fieldname is indistinguishable from "nobody has enabled it yet",
  precisely because the toggles default OFF. The suite also guards the Marketing feeds against
  the orphaned `Lead.source` column (see v1.243.0), scanning string literals rather than file
  text so the prose warning about the bug doesn't trip the check on the bug.

### Changed

- `setup/custom_html_blocks.py`'s Finance-only `FINANCE_DASHBOARD_BLOCKS` becomes
  `DEPARTMENT_DASHBOARD_BLOCKS`, one entry per department workspace, and the placement loop
  runs over it. Finance's own placement is unchanged — the shipped JSON is byte-identical.

### Notes

- Two Project fields disagree about units, and the Hours vs Budget widget is written around
  that rather than through it: `custom_total_time_elapsed` is a **Duration** (seconds) while
  `custom_time_budget_in_hours` is a **Data** field holding hours. The widget divides actual
  by 3600 before comparing, and skips a project whose budget text does not parse to a
  positive number rather than ranking it as an infinite overrun. `_production_metrics`'s
  `labor_budget_utilization` KPI in `kpi_dashboards/snapshots.py` divides the seconds sum by
  the hours sum directly and is left untouched *in this release* — changing a published KPI's
  value is not a docs-adjacent change. It is fixed under ### Fixed below, in this same release.

### Fixed

- **Labor Budget Utilization read roughly 3600x high.** `_production_metrics` computed the
  KPI as `sum(custom_total_time_elapsed) / sum(custom_time_budget_in_hours) * 100` in a single
  SQL statement. The two fields do not share a unit:

  | Field | Fieldtype | Actually holds |
  |---|---|---|
  | `custom_total_time_elapsed` | Duration | **seconds** |
  | `custom_time_budget_in_hours` | Data | free text, nominally **hours** |

  So a project 60% through its hours budget reported about 217,000%. On PRJ-00580 alone the
  elapsed figure is 23.8M — 6,616 hours — being divided as though it were already hours.

  What kept it invisible for so long is that **this KPI has no `KPI Target`**. Grading in
  `metrics.py` only produces Good/Watch/Bad when a target exists, so the number rendered as a
  plain grey figure with nothing next to it to contradict it. A wrong number that is never
  graded is never argued with.

  Summing in SQL had a second, quieter problem: `custom_time_budget_in_hours` is a varchar, so
  MySQL coerced it, and a budget of `"n/a"` became `0` without a warning. Both sides are now
  parsed in Python with `flt()`, and a project whose budget text does not parse to a positive
  number is excluded from **both** sums — dropping it from the numerator only would re-inflate
  the ratio in a subtler way.

  `api/production_dashboard.py::get_hours_variance` (added above in this release) already converted, so
  the widget and the KPI above it now agree. `tests/test_dashboard_widgets.py` fails if either
  side drops the conversion, if the two columns are ever summed against each other in SQL
  again, or if either side stops skipping unparseable budgets. All three tests were confirmed
  to fail against the pre-fix code.

  **The number will drop sharply on the next nightly run.** That is the fix landing, not a
  collapse in productivity.

## [1.251.0] - 2026-08-06

Moves the Closed-Won hand-off **in front of** project creation, and turns the hand-off
steps from things you tick into things that do the work.

A process-compliance audit on 2026-08-06 measured the existing tracker: anchored
(automatic) steps 1/3/5 completed **100%** of the time, manual steps **5%**; **17 of 17**
pending hand-off meetings were past SLA; **8 of 28** new projects had been created outside
the process path entirely. None of that surfaced anywhere until somebody went looking.

The cause was structural rather than behavioural. The 7-step tracker lived on the
**Project**, so step 2 — *Hold Hand-Off Meeting* — only existed once the project had been
created. The step meant to gate project creation sat downstream of it. This release
reverts the June decision that permitted project-first creation.

Three design intents from the meeting, applied throughout: make the compliant path the
easiest path, make skipping visible instead of silent, and make lateness loud.

### Added

- **The hand-off gate ([`crm_enhancements/handoff.py`](erpnext_enhancements/crm_enhancements/handoff.py)).**
  A Project cannot be created from a Closed-Won Opportunity until its hand-off meeting is
  recorded. Nine new Opportunity Custom Fields hold that state.

  **The enforcement is on `Project.before_insert`, and that is not a stylistic choice.**
  The obvious home is `validate` — but `crm_enhancements/api.py`'s
  `create_project_from_opportunity_background` sets `flags.ignore_validate = True` before
  inserting, and Frappe's `run_before_save_methods()` returns early on that flag. A
  `validate` gate would have passed review and then silently never fired on the path that
  creates most projects. `before_insert` survives both `ignore_validate` and
  `ignore_permissions`, which is the coverage the audit's eight off-process projects
  actually need. `make_project` and the background job refuse as well, but only so the
  message arrives somewhere readable; `before_insert` is the authority.

  **The gate keys on a flag, not a date.** `custom_handoff_gate_applies` is set only on a
  genuine *transition* into Closed Won. A date comparison would have been unsafe:
  `stamp_won_date` fills a blank `custom_date_closed_won` on **any** Closed-Won save, so
  merely re-saving one of the 227 already-won opportunities would have dragged it into the
  gate. That backlog is deliberately exempt (WI-024 owns it) and a patch stamps the
  exemption explicitly, so it is a fact the report can count rather than an absence nobody
  decided.

  **Skipping is allowed; silence is not.** `skip_handoff` is System Manager only and
  requires a written reason, which lands on the record and on the Project's step 2 row
  prefixed `[SKIPPED]` — so it reads as a skip in the tracker and the compliance report,
  never as a completion.

- **Buttons that do the step, not just record it.** On a Closed-Won Opportunity, *Hold
  Hand-Off Meeting* opens a dialog prefilled with Sales, Production and Billing, proposes
  the next business-day slot (15 minutes), then creates a Frappe `Event` and emails the
  invite; afterwards it becomes *Mark Hand-Off Complete*. On the Project, the current step
  carries an inline action: step 4 opens a billing email, step 6 the task list, step 7 the
  same meeting scheduler. Attendees are configured in **ERPNext Enhancements Settings →
  Hand-Off Attendee Roles** (explicit address, or the holders of a Role), so changing who
  attends is never a deploy.

  Two things about `Event` are worth recording. Its `Event Participants` child marks
  *both* reference fields `reqd` — `email` is only an optional extra — so a group address
  like `production@` cannot be expressed as a participant row at all; those attendees get
  the invite email, which is the part that has to work. And `Event` on this site grants
  create to System Manager and Finance Team only, so a Sales user cannot insert one under
  their own permissions: the endpoint checks **Opportunity write** and then inserts with
  `ignore_permissions`. The calendar attachment reuses `travel_management/ics.py`, which
  implements `METHOD:PUBLISH` only — an "add to calendar" file, not an RSVP-tracking
  invite.

- **Hand-Off SLA Compliance** Script Report, plus a Friday-morning email to a configurable
  recipient list (`"30 7 * * 5"` — a cron entry because Frappe's `weekly` bucket cannot
  pin a weekday). Reports on-time % per role and per step, the overdue list with days
  over, and the 7-business-day Closed-Won→Launch metric. Steps whose `due_by` never
  started get their own bucket rather than being counted as overdue: they are blocked
  upstream, not late, and merging the two would overstate lateness while hiding a stall.

- **`Hand-Off Attendee Role`** child DocType, and Settings fields `handoff_gate_enabled`,
  `handoff_invoice_flow`, `handoff_escalation_fallback`, `handoff_attendees`,
  `handoff_report_recipients`.

### Changed

- **The finance hand-off role is now "Finance & Accounting Manager"**, renamed from
  "Accounts Receivable" on both **Process Step Template** and **Project Process Step**
  (steps 4 and 5), on the Hand-Off SLA Compliance report's role filter, and on the
  Settings label.

  `responsible_role` is a Select whose value is *stored on every row*, not looked up, so
  the rename ships with `patches.rename_handoff_ar_role` to migrate the 132 existing rows
  (2 templates + 130 project steps). Without that migration the change would fail in
  three places at once and none of them would raise: Frappe renders a Select whose stored
  value is not among its options as **blank**, the report's role filter stops matching
  those rows, and `process_steps._resolve_responsible` — which compares the stored string
  against `ROLE_AR` — stops resolving a recipient, so the finance steps quietly stop
  notifying anybody.

  The Settings *fieldname* stays `handoff_ar_rep` on purpose. Renaming a Single's field
  would orphan the Employee already configured there for no benefit, so only the label
  moved.

- **Step 2's SLA anchors on the Opportunity.** Marking a deal Closed Won now stamps
  `custom_handoff_due_by` (2 business days) and `custom_launch_deadline` (7 business
  days) alongside the existing won-date stamp. Both are stored rather than recomputed, so
  a later edit to the Holiday List cannot silently move a deadline already reported on.
  Like `custom_stage_changed_on`, these stamps are deliberately **not** gated on the
  feature flag — `feature_flags.py` documents why silent data stamps stay ungated: the
  data has to already be meaningful on the day somebody flips a switch.

- **Step 7 shows two clocks.** Its own SLA *and* the 7-business-day launch goal. A step
  can be perfectly on time against the step before it and still miss the launch goal,
  because the goal measures the whole chain — the disagreement between them is the signal,
  so neither is derivable from the other and both are displayed.

- **Overdue steps escalate to the responsible person *and* their manager**, daily while
  they stay late, by email as well as Notification Log, with a subject line carrying the
  customer, the step and the days overdue. Manager resolution uses `Employee.reports_to`
  (populated on 13 of 15 active employees) with a configurable fallback address for the
  rest — "my manager isn't set up" must not mean "nobody hears about this".
  `status_alerts._deliver` gained an opt-in `email` parameter; every existing caller keeps
  behaving exactly as before.

- **Step 5 (Receive Customer Payment) no longer escalates.** When a customer pays is not
  something anyone here can be nagged into fixing, and a daily email about it teaches
  people to filter these messages — which costs us the steps that *are* ours. Step 4,
  sending the invoice, stays in.

- **A new sweep chases step 2 on the Opportunity.** The existing escalation queries
  `Project Process Step`, and step 2 now happens before any such row exists — without
  this the one step this release is about would be the one step nobody chases.

- **Manual completions go through whitelisted `process_steps.complete_step`**, which
  stamps `completed_on`/`completed_by` from the server clock and session and checks the
  step's responsible role. The old client path
  (`frappe.model.set_value(row, "status", "Completed")` then `frm.save()`) let the browser
  propose `completed_on`; the audit found retroactive box-checking.

- **Step 2 completion carries across into the Project.** When the project is finally
  created, the step 2 row inherits the real timestamp and user from the Opportunity rather
  than being stamped complete at creation time — otherwise project-side reporting would
  record every hand-off as instantaneous.

### Notes

- `handoff_invoice_flow` defaults to **Manual Billing Email**. ERPNext is not the
  accounting system yet, so the compliant step 4 action is still a note to Billing; the
  setting switches it to a draft Sales Invoice when invoicing goes live, without a deploy.
- **The business-day maths currently skips weekends only.** `handoff_holiday_list` points
  at *"Utah, USA Holidays 2025"*, which is the site's **only** Holiday List and spans
  `2025-01-01`–`2025-12-31` with nothing in 2026 — so there is no correct list to
  re-point it at; a 2026 one has to be created. Because a holiday not skipped is consumed
  as a working day, every 2026 due date lands *earlier* than the agreed SLA. This is data,
  not code, and it affects `process_steps._refresh_due` for all step due dates, not just
  the new hand-off SLAs. Tracked as TASK-2026-01242 on PRJ-00580.

## [1.250.1] - 2026-08-06

### Fixed

- **The marketing data-source alert had never fired.**
  `kpi_dashboards/snapshots.py` used `_()` in five places inside
  `_alert_on_source_change` but never imported it — the module had `import frappe` and
  `from frappe.utils import …` and no `from frappe import _`. Every call raised
  `NameError`.

  What made it invisible rather than loud is the function's own guard. Its whole body is
  wrapped in `except Exception: frappe.log_error(...)`, which is correct and stays —
  a notification problem must not cost the nightly snapshot it runs at the head of. But
  it meant the `NameError` was caught and written to the Error Log under the title
  *"KPI marketing web — source alert"*, which reads as **the alerting broke**, not
  **the data source broke**. So a dead GA4 or Search Console feed produced no
  notification, and the one thing standing between a stale marketing figure and somebody
  reading it as real had never once run. In the function's own words: *"a zero looks like
  a real number on a dashboard."*

  Found by reading the advisory ruff job's output on an unrelated PR rather than by
  anything going wrong, which is the point — the symptom of this bug was the absence of
  a symptom.

### Added

- **`No undefined names (F821)` is now a hard CI gate**, carved out as its own job from
  the advisory `Lint (ruff, advisory)`. F821 is not style: an undefined name is a runtime
  `NameError`, and this app deliberately swallows exceptions in exactly the places one is
  most likely to bite — scheduler jobs, `doc_events`, notification helpers — so it
  surfaces as silence rather than a stack trace. One rule only; the wider 433-finding
  style backlog stays advisory and untouched. Currently passes at zero violations.

- `tests/test_kpi_source_alert.py` (bench-free, own CI step). The static gate catches the
  class; this pins the behaviour the class was hiding. Its load-bearing assertions are
  "a failing source notifies somebody" and "no Error Log row is written" — before the
  fix, both were exactly inverted. Verified to fail (6 failures + 1 error) with the
  import removed.

## [1.250.0] - 2026-08-06

Regroups the **Opportunity** form. Fixtures only — no controller, hook or client-script
change; every field keeps its data.

### Changed

- **The Opportunity form's field groupings are now logical.** The form had accreted
  sections in `insert_after` order rather than in any order a salesperson would recognise.
  The whole layout is rewritten through the `Opportunity-main-field_order` Property Setter,
  which now enumerates **all 181 fields** instead of the 165 it listed before — the sixteen
  it omitted (the attribution block, the Drive-folder pair, the Hand-Off Process tab) were
  being positioned purely by their `insert_after` chains, which is what produced most of the
  damage below. Each of the app's own Custom Fields had its `insert_after` corrected to agree
  with the new order, so the two descriptions of the layout can't drift apart again.

  What actually moved, and why each one was wrong:

  - **The header is three columns again.** `custom_attribution_section` — a Section Break —
    had been inserted *inside the first column* of the header, between "Opportunity Summary"
    and the Scope/Schedule/Budget Rank trio. A Section Break terminates the section it lands
    in, so everything after it (the ranks, and the entire Status / Expected Closing /
    Probability / Territory / Created Project column) was pushed **below** a block of raw UTM
    data instead of sitting beside the name and owner. Attribution now sits after
    Organization, and the header reads identity | ownership + ranks | pipeline state.
    Two fields with the same `insert_after` (`custom_lead_source` and `custom_scope_rank`
    both claimed `custom_opportunity_summary`) is how the wedge got there; `custom_scope_rank`
    now follows `custom_lead_source` explicitly.
  - **Money left the "Analytics" section.** `custom_materials_budget` pointed its
    `insert_after` at `utm_medium`, which dragged Materials Budget, Estimated Cost, Exchange
    Rate, Time Budget and the Time & Materials checkbox inside erpnext's UTM analytics
    section on the **Budget** tab. The Budget tab is now two honest sections: *Opportunity
    Amount* (amount, currency, company-currency mirror, exchange rate) and *Estimated Cost*
    (estimated cost, materials budget, time budget, T&M) — the latter finally matching the
    "Estimated Cost" label it has carried since it held nothing but
    `base_opportunity_amount`. `column_break_17` is unhidden to give that section its column
    split.
  - **The Activities tab was empty.** `open_activities_html` and `all_activities_html` sat at
    the bottom of the Details tab while the tab literally named "Activities" rendered nothing.
    Both activity widgets (and the "Tasks and Events" section break) now live under
    `activities_tab`, which shortens the Details tab considerably.
  - **Hand-Off Process moved up.** `custom_process_tab` was pinned after the hidden
    Connections tab, i.e. dead last. It now follows Budget, so the tab strip runs
    Details · Contacts & Addresses · Scope · Schedule · Budget · Hand-Off Process ·
    Activities · Comments.
  - **Lost Reasons only appears on a lost deal.** The section carried no condition while the
    `order_lost_reason` field inside it already had `eval:doc.status==="Lost"` — so an open
    opportunity showed an empty "Lost Reasons" heading with a stray Competitors box. The
    section break now carries the same condition, and sits directly under the header where a
    status-driven block belongs.
  - **`more_info` collapses by default**, matching `organization_details_section`. Company,
    Opportunity Date, Print Language, First Response Time and the two hidden audit fields are
    reference material, not daily entry.
  - The hidden legacy "Opportunity Description" block (hidden, and de-`reqd`'d, in v1.34.2;
    superseded by the Scope tab's General Scope Description) moved from the middle of the
    Details tab to its end, so the JSON reads in the same order as the form.

  The **Contacts & Addresses** tab is deliberately unchanged: its widgets are code-owned by
  `setup/custom_fields.py`, which reconciles their `insert_after` on every migrate, and
  fighting it from a fixture would produce a layout that flips on each deploy.

- **erpnext's own `utm_source` / `utm_medium` / `utm_campaign` / `utm_content` are hidden on
  Opportunity.** They were visible in a section labelled "Analytics" directly beside our
  `custom_utm_*` capture fields, which is an invitation to type into them — and per the
  v1.241.0 schema decision, writing a campaign name into `utm_source` silently starts
  duplicating Contacts (`Lead.before_insert` suppresses its stray Contact only when
  `utm_source == "Existing Customer"`), while `utm_medium` and `utm_campaign` are **Links**
  that spawn junk taxonomy rows. Measured on production before the change: `utm_source` was
  set on 1 of 816 opportunities and `utm_campaign` on 0, against `custom_lead_source` on 814.
  Hidden by Property Setter rather than removed — the fields, their data and their propagation
  path are untouched, and they are parked in the field order immediately after our Attribution
  section so unhiding one puts it somewhere sensible. Lead and Customer are not affected;
  Property Setters are per-doctype.

### Notes

Applies on `bench migrate` (fixture sync re-asserts `custom_field.json` and
`property_setter.json`). No patch is needed — nothing is deleted, and the already-executed
`ensure_opportunity_handoff_fields` backstop still hardcodes `insert_after: dashboard_tab`
for the Hand-Off tab, which is harmless: patches run *before* fixture sync, so the fixtures
have the last word on a fresh site.

Two pre-existing oddities were left alone rather than guessed at, because both are data
decisions rather than layout ones: `custom_contacts__address_table` is a `Project
Stakeholder` child table labelled just **"Table"**, visible at the bottom of the Contacts &
Addresses tab with 33 rows on production; and `custom_estimated_cost` stays hidden inside the
section named after it (it is written by hand nowhere and copied to the Project's
`custom_project_cost` on hand-off).

## [1.249.0] - 2026-08-05

Adds an **Offsite Backup** module: Frappe's own backups, pushed to a Google Drive Shared
Drive, verified byte-for-byte, pruned on a retention policy, and watched for silence.

Ships dormant. No credentials, no folder, nothing enabled — the service account and the
Drive folder are created by hand in the Google Cloud Console, and the module does nothing
at all until somebody pastes a key into Offsite Backup Settings and ticks the switch.

### Added

- **`offsite_backup/` — offsite backups to a Google Drive Shared Drive.** Three cron jobs
  merged into the existing `scheduler_events["cron"]`: `0 2 * * *` database only,
  `0 3 * * 0` database + public files + private files, `0 8 * * *` a staleness watchdog.
  The slots are deliberately clear of the existing cluster at 05:00, 06:00, 06:30, 07:00
  and 07:15. Both backup entry points are thin shims — check the switch, reconcile any
  stranded run, hand off to the **long** queue with a 4h timeout. The scheduler is one
  shared process walking every job on the site; a multi-hour dump running inside it would
  stall the QuickBooks pulls and the KPI snapshots with it.

  Frappe v16 **removed** the built-in Google Drive / Dropbox / S3 backup doctypes from
  core, splitting them into a separate `frappe/offsite_backups` app. That app is
  deliberately not installed: it has no retention logic, and unbounded growth in a Shared
  Drive is its own outage.

  Two new doctypes — `Offsite Backup Settings` (Single) and `Offsite Backup Log` (one row
  per run) — plus `drive.py` (Drive v3 transport) and `backup.py` (orchestration).
  Deliberately separate from `google_drive/`, on a different Shared Drive with a different
  service account and its own client builder: the duplication *is* the isolation, so that
  a compromise of the widely-shared project-folder credential does not also hand over the
  encrypted database dumps.

- **Manual backups.** `Run Backup Now` on the settings form prompts for *Database only* or
  *Full (database + files)* and queues it; the whitelisted `run_backup_now()` is System
  Manager only and validates its `backup_type` against `{Manual, Manual Full}` rather than
  trusting it. The button works with the module still disabled, so the setup can be proved
  before the schedule is switched on. A `Recent Runs` table on the form dashboard answers
  "is this working?" without navigating away. From a VM,
  `bench --site <site> execute erpnext_enhancements.offsite_backup.backup.execute_backup
  --kwargs '{"backup_type": "Manual Full"}'` runs it in the foreground, where failures
  print to the terminal.

### Why it is built the way it is

Each of these is a failure mode that looks like a working backup until the day you need it.

- **`site_config.json` is never uploaded.** Only `backup_path_db`, plus
  `backup_path_files` and `backup_path_private_files` on a full run. Never
  `backup_path_conf`. Frappe's `backup_encryption()` encrypts exactly three things — the
  database dump and the two file archives. `copy_site_config()` writes a **verbatim
  plaintext copy** and it is never encrypted, *even though Frappe still gives it the same
  `-enc` suffix as its encrypted siblings*. That is the trap: the filename claims
  encrypted and the bytes are not. The file holds `db_password`, `encryption_key` and
  `backup_encryption_key`, so shipping it beside the ciphertext would put the decryption
  passphrase in the same folder as the thing it decrypts and make `encrypt_backup`
  ornamental.

- **The destination must be a Shared Drive.** The run raises when `check_folder` returns no
  `driveId`. A service account has no Drive storage quota of its own, so a My Drive folder
  charges every upload to the *folder owner's* personal quota — it works right up until
  their Drive fills, then fails in a way that reads as an API problem. There is
  deliberately no Shared Drive ID settings field: the id is read off the folder metadata
  every run, so it cannot go stale the day the folder moves.

- **`canDeleteChildren`, not `canDelete`.** `canDelete` is the right to delete *the folder
  itself*; pruning needs the right to delete the folder's *contents*. Checking the wrong
  one passes the connection test and then fails every night at prune time.

- **The Drive retry budget is per-stall, not per-file.** A 20 GiB tarball is roughly a
  thousand 20 MiB chunks and can spend three hours on the wire, where five transient 5xx
  is normal Drive behaviour rather than a broken upload. `upload_file` resets its attempt
  counter *and* its backoff every time a chunk lands, so five **consecutive** failures
  abort and five failures over three hours of steady progress do not.

- **An unverified upload is deleted, not kept.** Drive's reported `size` is compared with
  the local byte count, and where Drive returns an `md5Checksum` it is compared with a
  locally computed MD5 streamed in 8 MiB blocks (a multi-GB file is never read into
  memory). Any mismatch deletes the remote object and fails the run: a truncated upload
  left in the folder is worse than no upload, because it looks like a backup. Which check
  actually ran (`size` vs `size + md5`) is recorded per artefact.

- **Retention has two independent floors.** The newest `min_keep` objects are never pruned
  whatever their age, and *nothing* is pruned when the listing returns fewer than
  `min_keep` objects — a partial listing must not be read as "the archive is small" and
  cascade into deleting its tail. `createdTime` is parsed to an aware UTC datetime and
  compared against an aware UTC cutoff; anything unparseable is left alone, because an
  unreadable timestamp is not evidence that a file is old.

- **`Running` rows are the concurrency guard, so they have to be reconcilable.** The log
  row is inserted and **committed** before any work starts, because an uncommitted row is
  invisible to the process that needs to see it. The cost is that a run only ever leaves
  `Running` from inside its own process: a SIGKILL, an OOM mid-dump or a plain `bench
  restart` strands the row forever, and the stranded row then blocks every future run in
  silence. `reconcile_stale_runs()` fails anything past the job timeout and is called at
  the top of every entry point.

- **Skips are logged.** A run that bails because another is in flight writes a `Skipped`
  row. A line in a log file nobody reads is how a weekly backup quietly stops happening
  for a year.

- **The watchdog checks the two tiers separately** — database against
  `alert_if_older_than_hours`, full against `alert_if_full_older_than_hours`. Checked
  together, a healthy nightly database backup masks a weekly file backup that has been
  skipped every Sunday for months. It is also the only check that catches *nothing running
  at all*: a failure email only fires when a job actually ran and threw, so a disabled
  scheduler or a dead worker produces silence, not alerts.

- **Nothing renders frame locals, and there are two doors to shut.** Failures record
  `frappe.get_traceback()` with the default `with_context=False`, because with context on
  the rendered locals of a failing Drive call include the parsed service account key. The
  back door is Frappe's own: `background_jobs.execute_job` logs any *escaping* exception
  with `frappe.log_error(title=method_name)` and no message, and `log_error` with no
  message falls back to `get_traceback(with_context=True)` — while its sanitiser redacts
  only the exact dict keys `password`, `passwd`, `secret`, `token`, `key` and `pwd`, and a
  service account key's field is named **`private_key`**, which is not one of them. A plain
  `raise` would therefore have published the key into the Error Log however carefully this
  module logged its own traceback, so `execute_backup` re-raises a message-only
  `RuntimeError(...) from None` pointing at the log row (`from None` matters — implicit
  chaining renders the original frames anyway). A `_redact()` pass strips PEM private-key
  blocks from anything stored or emailed as a third line of defence.

- **The failure alert is committed, not merely queued.** `frappe.sendmail` only *inserts*
  an Email Queue row. On the failure path the exception then reaches `execute_job`, which
  calls `frappe.db.rollback(chain=True)` **before** it logs — so an uncommitted alert is
  discarded and a failed backup notifies nobody. Success alerts return normally and are
  committed by `execute_job`, so this would have been invisible in any test with
  `notify_on_success` on and only bitten on the exact path the alert exists for.

- **`SystemExit` is caught too.** With `encrypt_backup` on, Frappe's
  `BackupGenerator.backup_encryption()` calls `sys.exit(1)` when `gpg` is missing — so the
  single most likely failure in the run is not an `Exception`. Catching only `Exception`
  would have skipped the bookkeeping and finalised a `Failed` row with a blank error and an
  alert reading "No traceback was captured". `SystemExit` and `KeyboardInterrupt` are
  re-raised unchanged so a worker shutdown still shuts the worker down.

### Changed

- `erpnext_enhancements/modules.txt` — appended `Offsite Backup`.
- `erpnext_enhancements/hooks.py` — three entries **merged into** the existing
  `scheduler_events["cron"]` dict. A second `scheduler_events = {...}` assignment would
  have silently overwritten the first and dropped the Google Drive and QuickBooks Online
  jobs with no error.

## [1.248.0] - 2026-08-05

Imports QuickBooks **billable-expense passthrough lines**, the fifth and last sell-side
fidelity gap — found by chasing what the first post-1.247.0 resync parked, which turned out
not to be what the guard said it was.

### Added

- **Billable-expense passthrough lines are imported.** When a Bill or Expense line is
  flagged billable to a customer and reinvoiced, QuickBooks writes a `SalesItemLineDetail`
  with an **empty `ItemRef`**, naming its destination account on `ItemAccountRef` instead,
  alongside `MarkupInfo` and `ServiceDate`. `_sales_items` requires a resolvable `ItemRef`,
  so every one of them was dropped in silence: **1,035 lines across 158 invoices,
  $33,024.34** over every cached Invoice payload (**1,013 lines / 153 invoices /
  $31,727.29** in the pre-2026 slice). Invoice I101635 is typical — 4 of its 9 lines,
  `HAS15841 HASA MURIATIC ACID DISPOSABLE S` at $70.26 beside `25% markup for HAS15841…` at
  $17.57 — and I101332 is the extreme, **97 of 102 lines**.

  `_sales_passthrough_charges` turns each into an `Actual` Sales Taxes and Charges row
  booked to its own `ItemAccountRef` account, exactly as `_purchase_charges` already
  handles the account-based lines of a Bill. That is the posting QuickBooks itself makes:
  crediting `52100 Service Materials` reduces the COGS the expense was originally booked
  to, which is what reimbursing a billable expense means, while the 511 markup lines credit
  `46300 Markup on Billable Expenses` as income. All six destination accounts resolve to
  postable ERPNext ledgers and **every one of the 1,035 lines carries an `ItemAccountRef`**,
  so nothing here is guessed.

  Charges rather than item rows because there is no Item to put on one and no quantity
  either — **not one of the 1,035 lines carries a `Qty`**. Inventing a placeholder Item
  would put fictitious stock movement and an invented income account behind real money.
  Like `_sales_charges`, and unlike `_purchase_charges`, the row sets no `category` or
  `add_deduct_tax`: those are Purchase-only and Frappe drops them silently.

- **The Journal Entry twin, for CreditMemo and RefundReceipt.** `_item_line_income_account`
  falls back to the same account when a line carries no `ItemRef`. **Dormant on this site**
  — both gained mappers in 1.244.0 and have no cached payloads yet, so unlike the invoice
  half it cannot be measured against real data, only reasoned from the identical line
  shape. It is here because leaving one of two identically-shaped paths unfixed is exactly
  how the zero-quantity bug survived a release (see `_line_qty_rate`).

### Fixed

- **`_sales_invoice_shortfall_causes` no longer reports passthrough lines as missing
  Items.** The cause tested only whether a line's `ItemRef` *resolved*, so a line carrying
  no `ItemRef` at all was reported as referencing "QuickBooks items not imported into
  ERPNext" — which was false on every one of the **157 invoices** the first post-1.247.0
  resync parked, while **all 265 QBO Items were in fact imported and mapped, a 1:1 match**.
  The message even rendered the missing ids as `None, None, None`, which was the only
  visible tell. It now requires an `ItemRef` value before blaming a missing Item.

  A wrong triage message is not cosmetic: it is the entire product of a guard whose job is
  to say *why* a document parked, and this one sent a day of work after Items that were
  never missing.

### Changed

- **A third shortfall cause: a passthrough line whose account will not resolve.** Named
  separately from a missing Item because it is fixed differently — by importing or mapping
  the *account*, not an item — and it names the `ItemAccountRef`, including the case where
  the account is a group with no `- General` ledger child.

### Notes

- **Where this lands.** Modelling the mapper against every cached payload (2026-08-05), the
  reconciliation guard parks **43 of 1,416** pre-2026 invoices, down from 193; across the
  full population **54 of 1,560**, down from 209. **155 invoices heal and none regress.**
  Of the 158 carrying passthrough lines, three still park: I100853 (a genuine −$304.37
  discrepancy already on the known list), I101044 (3¢, just over its tolerance), and
  I100780, whose lines are *all* passthrough — it maps zero item rows and ERPNext refuses
  an item-less Sales Invoice. That one is left parked rather than given a placeholder Item.
- This models the **reconciliation guard only**. A resync parks documents for other reasons
  too (no customer, no item rows at all, conflicts), so it is a floor on what clears, not a
  prediction of the resync summary. The 1.247.0 estimate of 42 understated the real 248
  precisely because it modelled arithmetic and assumed every `ItemRef` resolved.
- **Resync required**, and nothing in this release changes the procedure:
  `preview_resync(entity_types=["Invoice", "SalesReceipt"])` → read the preview →
  `run_resync(preview_id)`. No new fields, no patch, no configuration.

## [1.247.0] - 2026-08-05

Closes the two gaps [1.246.0](#12460---2026-08-05) recorded as open, on the owner's
decisions. Together they take the pre-2026 population from **1,315 of 1,413** reconciling
to **1,371**, and the parked queue from **98 to 42**.

### Added

- **QBO discounts are imported.** A `DiscountLineDetail` is a **transaction-level** line
  carrying a *positive* `Amount` subtracted from the subtotal — verified exactly across
  every affected invoice: `sum(item lines) − discount + TotalTax == TotalAmt`. It maps to
  ERPNext's header `discount_amount` with `apply_discount_on = "Net Total"`, which is the
  same order QuickBooks applies it in. **36 of the 1,413 pre-2026 invoices carry one,
  $89,561.00 in total — a bigger gap than the sales tax fixed in 1.246.0.**

  On the header rather than spread across item rows: QuickBooks records no per-line
  discount to spread, so allocating one figure over N lines would invent detail the source
  never had and reintroduce exactly the per-row rounding drift `_sales_invoice_shortfall`
  exists to catch. Booking it as a negative tax charge was the other option considered and
  is worse still — it would post a discount into a tax account. A percent-based discount
  uses the amount QuickBooks already resolved (33% of $150.00 recorded as $49.50) rather
  than recomputing it, so the two systems cannot disagree in the last cent.

### Changed

- **`_sales_invoice_shortfall` now tolerates one cent per item row, floor two cents.**
  Not a fudge factor but the arithmetic ceiling on unavoidable rounding: ERPNext rounds
  every item row to two decimals, so N rows can move the total by up to N/2 cents, and
  QuickBooks stores unit prices and quantities at more decimals than ERPNext keeps
  (`UnitPrice 2051.9872727`, `Qty 0.6666999`) — on those invoices the two systems
  genuinely cannot agree to the penny and no mapping would fix it.

  Deliberately tighter than the drift bound so it stays inside "a penny here or there": a
  2-line invoice tolerates 2¢ and still parks over the $22.98 differences in the data,
  while a 17-line invoice tolerates 17¢, which is what its rounding can actually produce.
  **The trade is explicit and accepted: an error smaller than a cent per line now imports
  silently**, because at that size it is indistinguishable from the rounding it sits beside,
  and a guard that cried wolf on 19 penny differences would stop being read.

- `_sales_invoice_shortfall_causes` no longer names discount lines as a cause — they are
  imported now, so a discounted invoice that still fails to reconcile falls to the
  catch-all rather than being blamed on its discount. Two causes remain: unimported items,
  and tax that could not be booked.

### Known gaps

- **42 invoices still do not reconcile**, and they are real differences rather than
  rounding or mapper gaps: I100853 −$304.37, I101039 +$258.31, I100936 −$187.50,
  I101473 −$118.70, down to $0.30. Each parks with its computed-vs-QuickBooks figures, so
  clearing them is per-invoice accounting work rather than code. Across the full mapped
  population including 2026, 53 of 1,556 park.

### Deployment notes

- **This must be resynced together with 1.246.0**, not separately — same population, same
  `preview_resync` → read the preview → `run_resync` procedure, and the two releases pull
  in opposite directions on any invoice carrying both a discount and tax.
- Nothing else changes: no new fields, no patch, no configuration.

## [1.246.0] - 2026-08-05

Closes the sales-tax gap recorded in 1.244.0, and adds the guard whose absence let both
that gap and the 1.244.0 quantity bug survive the entire import unnoticed.

QuickBooks carries invoice tax **outside** the `Line` array, on `TxnTaxDetail`, and
`_sales_items` reads only `Line` — so every taxed invoice imported at the net of its
lines. Invoice **I100549** (Myers Mortuary, 2022-09-08) is **$385.56** in QuickBooks:
$360.00 of lines plus **$25.56** of Utah tax at 7.1%. It imported as **$360.00**, with an
empty taxes table.

### Added

- **Sales tax is imported as a Sales Taxes and Charges row.** `_sales_charges` turns
  `TxnTaxDetail.TotalTax` into a single `charge_type: "Actual"` charge, wired into
  `_map_sales_invoice` (and therefore `_map_sales_receipt`, which delegates to it).
  Measured against every cached payload 2026-08-05, this creates tax rows on **509
  documents totalling $71,141.69** — of which the pre-2026 Sales Invoice slice quoted in
  1.244.0 is **424 invoices / $58,162.96**; the rest is 81 invoices dated 2026 and 4 Sales
  Receipts ($133.39).

  **Identity comes from `TxnTaxCodeRef`, never `TaxRateRef`.** They are different QBO id
  spaces and confusing them resolves *silently* to a real-but-wrong account: on I100549
  `TxnTaxCodeRef` 8 is `Utah - Weber - Ogden - Inactive - SF`, while `TaxRateRef` 15 read
  as a TaxCode is `Sandy Utah - SF` — a different city, no error, wrong jurisdiction. Both
  rows exist, so neither lookup fails. TaxRate is not imported by this integration at all.
  A test asserts the two ids give *different* answers, so an edit that reaches for the
  wrong ref fails CI rather than mis-booking tax.

  **All tax posts to one account** — `25010 Sales Tax Agency Payable`, configurable via
  the new `QuickBooks Online Settings.sales_tax_account`, defaulting to account **number**
  25010 on the configured Company (a number, not a name, because ERPNext appends the
  company abbreviation). QBO TaxCodes are *rate definitions, not GL accounts*: QuickBooks
  books all sales tax to one agency-payable account and keeps jurisdiction in its Sales
  Tax Centre, outside the ledger. Posting to the per-TaxCode accounts `_map_tax_code`
  creates would build a parallel tax-liability structure that could never tie back.
  Measured: those 63 TaxCode mappings collapse onto only **36 distinct Accounts**, every
  one with `tax_rate 0.0`, and **none has ever received a Journal Entry line or a GL row**.
  The jurisdiction survives on each charge row's `description` instead.

- **`_sales_invoice_shortfall`, the Sales Invoice reconciliation guard.** The buy side has
  compared its mapped total against QuickBooks' `TotalAmt` since the mixed-Bill fix; the
  sell side never did, so a Sales Invoice could validate, save and look entirely clean
  while posting a number QuickBooks disagreed with. **That absence is the reason both this
  bug and the 1.244.0 quantity bug went unnoticed — it would have caught both on the first
  import, and it is worth more than either fix.**

  It sums **`qty * rate`**, not the informational `amount` the mapper carries across —
  ERPNext *recomputes* the line amount on save, so that product is the only number whose
  agreement with QuickBooks means anything. **The rounding order is the whole trick:**
  ERPNext's `round_floats_in` rounds `rate` to currency precision and `qty` to float
  precision *first*, then multiplies, using Frappe's half-to-even `flt` rather than
  Python's `round`. An earlier revision of this guard multiplied then rounded, and an
  adversarial review caught it against real data — QBO invoice **I101613** carries
  `Qty 0.6666999`, which ERPNext stores as `0.667` and posts at **$54,527.25** against
  QuickBooks' **$54,502.72**, and the naive guard called it reconciled. Modelling the
  order correctly catches **37 more** wrong pre-2026 invoices (61 → 98).
  `_sales_invoice_shortfall_causes` names which of three things went wrong: unimported
  items, tax that could not be booked, or unmodelled discount lines.

- **Tax legs for CreditMemo and RefundReceipt.** Both map to Journal Entries, which have no
  taxes child table, and both credit the tax-inclusive `TotalAmt` while debiting only the
  net item lines — so a taxed one could not balance and would park. `_sales_tax_ledger_line`
  debits the tax account, reversing the liability the original sale credited. Neither entity
  has ever been fetched (both were only added to the catalogue in 1.244.0), so this ships
  correct-by-construction and **cannot be validated against existing data** — check it on
  staging after the first full import.

- **`patches/set_sales_tax_account_type.py`** sets `account_type = "Tax"` on the
  destination account when it is blank. ERPNext keys the taxes `account_head` picker and
  tax-report grouping off that field, so an account created without one is invisible to an
  accountant correcting a parked invoice by hand. Fills a blank only; an account somebody
  deliberately typed otherwise is logged and left alone. Runs on `bench migrate`, i.e.
  before any resync.

  *(Corrected after release: this entry originally said the QBO Account import left `25010`
  blank. It did not — `25010` was typed `Tax` by hand on 2026-07-31, so the patch is a
  no-op on the Sapphire Fountains production site and exists for staging, fresh sites, and
  a re-pointed destination account.)*

### Known gaps

- **QBO discount lines are not imported — FIXED IN [1.247.0](#12470---2026-08-05).**
  36 of the 1,413 pre-2026 invoices carry `DiscountLineDetail` totalling **$89,561.00** —
  *more than the $58,162.96 of tax this release fixes*.
- **62 further invoices** do not reconcile for other reasons — chiefly QBO unit prices and
  quantities carrying more decimals than ERPNext stores (a `UnitPrice` of 2051.9872727 is
  simply not representable at 2-dp rate, so that invoice genuinely cannot post
  QuickBooks' total), plus real differences. **1.247.0 accepts a per-invoice rounding
  tolerance**, which together with the discount fix leaves 42.
- **A tax code created in QuickBooks today does not arrive until the next full import.**
  `TaxCode` is deliberately absent from `CDC_ENTITIES` (QBO's CDC endpoint does not support
  it). Until it arrives, its invoices still import the correct tax **amount** under the
  label `"Sales Tax"`; a later full import plus resync fills the jurisdiction in. Losing a
  label beats losing the money. Currently **0 of 46** referenced tax code ids are unmapped,
  so no money is at risk today.

### Deployment notes

**Use resync, not Import All.** Deploying code corrects nothing already imported.

1. `preview_resync(entity_types=["Invoice", "SalesReceipt"])` — writes nothing; stores a
   per-record plan against a `preview_id`.
2. **Read the preview.** Expect roughly 615 invoice updates from the 1.244.0 quantity fix
   plus 509 documents gaining a tax row.
3. `run_resync(preview_id)` — replays the stored payloads with `overwrite=True`.

`import_all` does **not** pass `overwrite`, so any record where a user edited a QBO-owned
field returns a conflict instead of updating. Conversely `overwrite=True` resolves
conflicts in QuickBooks' favour and **will discard manual edits** — which is why step 2 is
not optional.

- **Validate each fix separately on STAGING** so you know which code did what; production
  gets a **single** resync once both are deployed. The two defects pull in opposite
  directions (quantity overstates, tax understates), so fixing one alone moves the
  aggregate in a direction that looks wrong.
- **Combined success criterion:** every pre-2026 Sales Invoice has `base_grand_total`
  equal to its QBO `TotalAmt`. Binary, no attribution needed. Baseline: ERPNext
  $6,996,286.32 vs QuickBooks $4,365,679.06, 615 differing. **Honest target after this
  release: 1,315 of 1,413** — the remaining 98 (36 discount + 62 rounding/other) park for
  review with a named cause rather than importing wrong. Across the full mapped population
  including 2026, 120 of 1,556 park. **[1.247.0](#12470---2026-08-05) takes this to
  1,371 / 42.**
- **`run_resync` has never executed on this site**, so the `overwrite=True` path is
  untested in production. Rehearse it on staging.
- **The guard runs before the overwrite check**, so `run_resync(overwrite=True)` cannot
  force-heal an invoice the guard rejects — it parks either way. That is intended: overwrite
  resolves *user-edit conflicts*, not reconciliation failures.
- **Do not submit anything to the GL until the criterion passes, and pause the QuickBooks
  sync before submitting** — ERPNext cannot update a submitted document, so every later QBO
  edit to a posted one becomes a sync failure. Shared with
  [WI-068](work-items/WI-068-group-account-remap.md); it is one decision, not two.

## [1.245.0] - 2026-08-04

Unblocks the pre-2026 GL posting. **1,726 draft Journal Entries cannot be submitted**
because 1,813 of their lines — **$724,230.37** gross across **22 accounts** — post to
group (parent) accounts, and ERPNext refuses to submit a Journal Entry whose line names
one. Nothing pre-2026 reaches the ledger until that is cleared.

QuickBooks permits posting to an account that also has sub-accounts; ERPNext does not.
QBO genuinely booked this money **at the parent level**, so there is no finer-grained
truth in the source to recover — the import did not lose a classification, the
classification was never made. The chosen fix is a `- General` ledger child under each
affected parent: the parent's rollup total stays identical to the penny, and the child
represents "posted at this level in QuickBooks" honestly rather than inventing a split
nobody chose.

### Fixed

- **The QBO mapper resolved to group accounts, so the remap would have undone itself.**
  This is the half that matters more than the data migration. `_resolve_account` returned
  whatever ERPNext Account a QBO account id was mapped to, group accounts included — and
  because these 1,726 entries are **drafts**, they stay re-syncable indefinitely. The next
  CDC poll touching any of them would rewrite the line straight back to the group parent,
  silently reverting the migration.

  `_ledger_for_posting` now redirects a resolved group Account to its `- General` ledger
  child at **every** point where a QBO account reference becomes a *posting* account:
  `_resolve_account` (the main chokepoint), `_item_expense_account` and
  `_item_income_account` (an Item Default can name a group account too), and
  `_journal_accounts` — which is the easy one to miss, because it calls `_linked_name`
  directly rather than `_resolve_account` and is the native JournalEntry mapper behind 52
  of the 1,726. Paths that merely test whether a QBO account is *mapped*, or that pick a
  **parent** for a new account, deliberately do not redirect; a group is the right answer
  there.

  **The mappers stay read-only.** A missing `- General` child resolves to None and the
  existing balance guard parks the transaction. `_ensure_group_parent` sets the opposite
  precedent — it writes during mapping — and it was considered and not followed: promoting
  an existing parent to a group is a reversible property change on a record the sync
  already owns, whereas creating a ledger invents chart-of-accounts structure mid-payload-
  transform, in a path with no review step and nobody watching. Parking is recoverable; a
  silently invented ledger with money in it is not.

- **The same falsy-zero bug on the buy side.** `_purchase_items` still carried the
  `detail.get("Qty") or 1` / `detail.get("UnitPrice") or line.get("Amount")` pattern that
  v1.244.0 removed from `_sales_items`, feeding Purchase Invoices (from QBO Bills) and
  Purchase Orders. Both paths now share one `_line_qty_rate`, renamed from
  `_sales_line_qty_rate` — they were separate copies of the same three lines, one got fixed
  and the other did not, and nothing would have caught them drifting again.

  **Latent, not actively wrong**, and **no re-import is needed for this part** — do not
  conflate it with the Sales Invoice re-import the v1.244.0 fix requires. Verified across
  every cached payload: Bill (1,237 payloads, 19 item-based lines) and PurchaseOrder (31
  payloads, 49 lines) carry **zero** `Qty: 0` lines. One PurchaseOrder line does carry a
  quantity with no `UnitPrice` and so hit the second-order bug — harmless only because its
  quantity happened to be 1. Blast radius is 68 lines.

### Added

- **`quickbooks_online/core/group_account_remap.py`** — the repo-tracked, idempotent
  migration for WI-068. Creates 20 `- General` ledger children (each inheriting its
  parent's `root_type` and `account_type`) and moves the draft pre-2026 lines onto them.
  A/R and A/P are deliberate **exceptions**: their 8 and 32 lines merge into the real
  `1310 Debtors` and `2110 Creditors` ledgers instead, which is what puts those balances
  into AR/AP aging. The 8 receivable lines include two for Crystal Fountains totalling
  $20,082.04 that exactly match three existing payments from that customer — split them
  across two accounts and those payments become unexplained credits.

  Dry-run by default, batched with commits, re-running is a no-op, and scoped strictly to
  `docstatus = 0 AND posting_date < 2026-01-01`. It prints its own before/after
  verification: per-parent counts and totals against the expected figures, every touched
  entry still balancing, zero in-scope lines left on a group account, and each parent's
  subtree total unchanged. **Not wired to migrate or the scheduler** — it is run by hand,
  on staging first, against a verified backup, and this release does **not** execute it
  anywhere.

### Ordering — these are not independent

The group-account remap unblocks **Journal Entries**. The qty fix (shipped in v1.244.0) and
the still-outstanding sales-tax gap unblock **Sales Invoices**. Different populations, but
one shared constraint: **the QuickBooks sync must be paused before anything is submitted**,
because ERPNext cannot update a submitted document and every later QBO edit to a posted
document becomes a sync failure. Submission and sync-pause are one decision, not a
per-batch one.

For WI-068 specifically the forward fix must be deployed **with or before** the data script,
or the remap reverts on the next sync. The runbook's step 6 — re-sync one remapped entry and
confirm the line still points at the `- General` child — is the check that proves it, and
the one most likely to be skipped.

### Work items

- **[WI-068](work-items/WI-068-group-account-remap.md)** (DATA) — this remap: the forward
  fix, the 20 accounts, the AR/AP merge, the staging rehearsal and the backup precondition.
  Cross-referenced to **WI-029** (chart-of-accounts rebuild), whose scope covers these 20
  new accounts and which may fold them in.
- **[WI-069](work-items/WI-069-general-ledger-reclassification.md)** (DATA, **not**
  blocking) — later reclassification of `- General` balances into real children where the
  vendor makes it determinable: 61500 Accounting & Bookkeeping (QuickBooks Online vs
  QuickBooks Payments Fees) and 60100 Auto and Trailer (Gasoline vs Vehicle Maintenance).
  Records that 60300 R&D (4 value-stream children) and 53100 Rent Materials (6 fountain-
  product children) were **assessed and rejected** as not determinable from the import —
  the determining fact is which project or product the work belonged to, and the imported
  lines do not carry it.
- **[Runbook](docs/migration/wi068-group-account-remap-runbook.md)** for the apply
  procedure and rollback.

## [1.244.0] - 2026-08-04

Three defects in the QuickBooks Online sync mapper, found by reconciling the
imported backlog against the QuickBooks trial balance as of 2025-12-31. Over the
1,413 unposted pre-2026 Sales Invoices ERPNext totalled **$6,996,286.32** against
QuickBooks' **$4,365,679.06** — **615 invoices (43.5%) wrong** — and every one of
the 1,314 pre-2026 payments ($4,069,818.35) was unallocated.

### Fixed

- **Zero-quantity invoice lines were billed at full price.** `_sales_items` read
  `"qty": detail.get("Qty") or 1`. QuickBooks writes an explicit `Qty: 0` on the
  lines of a **progress-billing** invoice that are not being billed this period —
  the invoice lists the whole contract and only the lines carrying a quantity are
  due. Python's `or` treats that legitimate `0` as falsy and substituted `1`, and
  ERPNext then recomputes `amount = qty * rate` on save, so every unbilled contract
  line was invoiced at its full unit price.

  QBO invoice **I100900** (Salt Development, 2024-03-29) is the worked example: 123
  lines, of which exactly one carries a quantity (0.2 @ $10,500 = $2,100). QuickBooks
  totals it at **$2,100.00**, fully paid; ERPNext imported **$570,650.00** and showed
  the whole thing outstanding. Summing the unit prices of every `Qty: 0` line across
  the backlog predicts **$2,321,939.62** of the $2,630,607.26 overstatement — 88%,
  concentrated in 96 invoices. It does **not** explain all of it; roughly $308.7k
  remains, part of which is the missing sales tax noted under *Known gaps*.

  The mapper now distinguishes an absent `Qty` from a `Qty` of zero. Absent (QBO
  omits it for a single unit) → qty 1 at the line's UnitPrice, else its Amount.
  Non-zero → that quantity, at a rate chosen so `qty * rate` reproduces QuickBooks'
  own line Amount. Zero with a zero Amount → the line is dropped; it is worth nothing
  and ERPNext rejects a zero-quantity Sales Invoice line outright. Zero with an
  Amount → one unit at the line amount, so the money survives.

  Deriving the rate also fixes a second-order bug on its own: a line with a quantity
  but no UnitPrice took the **whole line amount** as its rate and was then multiplied
  by the quantity again (2 × $500 billed $1,000 for a $500 line). `_sales_items` is
  shared with the Estimate → Quotation mapper, so imported quotations were wrong the
  same way and are fixed by the same change.

- **Payment Entries never allocated against the invoices they settle.**
  `_map_payment_entry` built no `references` at all, so all 1,314 pre-2026 payments
  imported fully unallocated and A/R aging showed every invoice outstanding no matter
  what had been received against it. QuickBooks records the settlement on each payment
  line's `LinkedTxn` (`TxnType: "Invoice"`, `TxnId`) with that line's Amount as the
  amount applied — data already sitting in every cached `QuickBooks Raw Payload` row,
  simply never read. Payments now allocate from it, summed per invoice (ERPNext
  rejects a duplicated reference) and ignoring a payment's top-level `LinkedTxn`,
  which is the Deposit that later swept it to the bank rather than an allocation.

  **Only submitted Sales Invoices are referenced.** ERPNext refuses to allocate
  against a draft and this integration deliberately imports invoices as drafts for
  review before they reach the GL — which is almost certainly why allocation was
  never implemented in the first place. A payment whose invoice is not yet posted
  imports unallocated rather than failing; because the reference table is rebuilt on
  every sync, **re-syncing Payments after submitting the invoices fills the
  allocations in**. Relaxing that guard to "the invoice exists" would turn a clean,
  correctable import into a hard ValidationError on every payment in the backlog.

### Added

- **CreditMemo and RefundReceipt are now imported.** Neither appeared in any entity
  list or in the mapper registry, so every customer credit and POS refund stayed in
  QuickBooks and imported A/R was overstated by exactly the credits never brought
  across. Both map to a **Journal Entry**, matching how `VendorCredit` is already
  handled rather than ERPNext's native credit note — a return Sales Invoice needs
  negative quantities and a `return_against` link to the invoice being credited, and
  QuickBooks supplies neither (its credit stands against the customer's balance, not
  a named invoice).

  A **CreditMemo** credits A/R for `TotalAmt` carrying the customer as Party (the
  sell-side mirror of the existing `_supplier_payable_line`) and debits each item
  line's income account. A **RefundReceipt** credits the `DepositToAccountRef`
  account the money left and debits income; it never touches A/R, because QuickBooks
  settles it immediately. The new `_item_income_account` mirrors `_item_expense_account`
  — read `Item Default.income_account`, fall back to the Company's
  `default_income_account`, and return **None** rather than guessing, so an
  unresolvable line drops out and the balance guard parks the entry instead of the
  ledger quietly absorbing a wrong-but-balanced posting. On this dataset the Company
  fallback is the usual path: no imported Item carries an income account of its own.

  Both entities are wired into `ACCOUNTING_ENTITIES`, `TRANSACTION_ENTITIES`,
  `CDC_ENTITIES`, `ENTITY_DOCTYPE_MAP`, the mapper registry and the dashboard's
  `QBO_ENTITIES` list.

### Known gaps

- **Sales tax on invoices is not imported — FIXED IN [1.246.0](#12460---2026-08-05).**
  QuickBooks carries invoice tax on `TxnTaxDetail.TotalTax`, outside the `Line` array
  `_sales_items` reads, so ERPNext imported the **net of the lines** and dropped the
  tax. Invoice I100549 (Myers Mortuary, 2022-09-08) is $385.56 in QBO — $360.00 of
  lines plus $25.56 of Utah tax at 7.1% — and imported as $360.00. It *understates*
  invoices and so partly offsets the overstatement fixed above, which is why
  correcting only one of the two moves the totals in an unexpected direction. Both
  must be resynced together; see 1.246.0 for the procedure and the combined
  reconciliation criterion.

### Deployment notes

- **Existing drafts are not corrected by this release.** The mappers only run on
  import; the ~1,413 already-imported pre-2026 Sales Invoices keep their wrong
  quantities until they are **re-imported**. Re-import is idempotent (keyed on QBO
  id), so re-running Import All for Invoice / Estimate / Payment repairs in place.
- **Do not submit the backlog to the GL until after the re-import.** A submitted
  document cannot be updated by a later sync, so anything posted now freezes the
  overstatement into the ledger and will need a manual credit note to undo.
- **Order matters for payment allocation.** Re-import invoices, submit them, *then*
  re-sync Payments — payments synced while their invoices are drafts import
  unallocated by design (see above).
- **CreditMemo and RefundReceipt are new coverage CI cannot exercise** (there is no
  Frappe integration-test job, and no such payload has ever been fetched into the
  raw-payload cache). **Validate both on staging** against a real QuickBooks company
  before running them against production, paying particular attention to entries that
  park for review with an unbalanced-journal message.

## [1.243.0] - 2026-08-04

WP-4: the marketing spend baseline and the per-value-stream dashboard — plus four
marketing KPIs that turned out to be measuring a column nothing has written to
since erpnext v15.

### Added

- **Marketing spend import (WP-4).** `Marketing Spend` held **zero rows**, so
  there was no budget baseline and no denominator for any cost-per-lead figure.
  The new importer (Marketing Spend Rollup → **Import Spend**) takes CSV with
  loosely-matched headings, **previews before writing**, and **upserts**: a
  month/channel pair is unique by the doctype's own autoname, so re-importing a
  corrected export would collide on every row through frappe's Data Import. That
  collision is the reason this is bespoke rather than stock.

  Channel spellings are canonicalised — "Google Ads", "google ads", "Google
  AdWords" and "AdWords" are one line item to a marketer and four to a `GROUP BY`
  — and **every rename is reported**, because a wrong alias silently merges two
  budgets. An unknown channel passes through unchanged; an unknown channel is a
  new channel, not an error.

  An unreadable amount is **refused, not zeroed**. Deliberately not
  `frappe.utils.flt`, which coerces "n/a" to 0.0: a silent zero in the denominator
  of every cost-per-lead figure is indistinguishable from a month where nothing
  was spent.

- **Marketing Spend Rollup report.** Two shapes for two questions: a
  channel-per-column grid by month (what did we spend, and is a channel being
  quietly switched off), and a per-channel view with totals, monthly average,
  cost per lead and cost per opportunity. The cost columns stay blank where
  attribution has produced no denominator rather than printing a division by zero
  with a currency symbol on it.

- **Value Stream Performance report.** Spend, opportunities, won, lost, win rate,
  revenue, average deal and cost per opportunity, per value stream. **No required
  parameters** — it is opened live in a standing weekly meeting, and a report that
  greets you with an empty filter bar wastes the first ninety seconds of it every
  week. Four queries total: the `Value Stream` child table carries ~1,460 rows
  across three parent doctypes, so a per-stream subquery re-scans it every time.

  Three things it is explicit about rather than leaving to be discovered in a
  meeting: columns do not sum to the company total (an opportunity tagged with two
  streams counts once toward each); win rate is won / (won + lost), because
  including open deals makes every rate look terrible and move whenever somebody
  adds a lead; and revenue falls back to the quoted amount where no Project is
  linked, reporting how many rows it did that for.

- **`Marketing Spend.value_stream`** — optional, and **never apportioned**. A
  channel serving several streams stays blank and appears as an Unallocated line.
  A made-up split is worse than an honest gap, because it looks like data. When
  most spend is unallocated the dashboard says so.

- **Channel breakdown on `Marketing Web Snapshot`** via the new `Marketing Web
  Channel` child table. The GA4 pull has been fetching
  `sessionDefaultChannelGroup` all along and discarding it every night — this is
  retained data, not a new API call, so it costs no extra quota. GA4's own
  grouping is stored verbatim so the numbers reconcile against the GA4 UI.

- **Data-source failure alerting.** GSC had failed on **40 of 40** nightly pulls
  since the dataset began, with the only trace being a `pull_error` field nobody
  opens. A Notification Log now goes to System Managers when a source starts
  failing **and** when it recovers — on transitions only, because a nightly "still
  broken" alert gets muted within a week and then hides the next real outage.

### Fixed

- **Four marketing KPIs were measuring a dead column.** `Lead.source` and
  `Opportunity.source` lost their DocField when erpnext v15 renamed them to
  `utm_source`. frappe never drops columns, so `has_column("Opportunity", "source")`
  returned True, the queries ran without error, and four plausible numbers came
  back — from a frozen pre-2023 snapshot that nothing has written to since:

  | KPI | Was measuring | Consequence |
  |---|---|---|
  | Unsourced Leads (30d) | `coalesce(source,'')=''` | every new Lead scored unsourced forever |
  | Unsourced Opportunities | same | could only ever rise |
  | Sourced Pipeline Value | `coalesce(source,'')<>''` | could only ever fall |
  | Sourced Wins (30d) | same | same |

  All four now read `custom_lead_source`, and treat the `Unknown (pre-Aug 2026)`
  bucket as **not** sourced — it is a recorded gap, not a channel, and counting it
  as attributed would launder the exact hole WP-1 exists to expose.

  A test parses the AST of `snapshots.py` — string constants only, so the
  explanatory comment does not trip it — and fails if any query references that
  column again.

### Notes

The value-stream dashboard will render thin until two things happen: marketing
spend is actually loaded, and WP-1's website capture script goes live so
attribution has a denominator. Both are stated in the reports themselves with
live numbers rather than left to be inferred from an empty chart.

The GSC 403 is still not fixable from this repository — it needs Search Console
access granted to the GA4 service account, and the property form to match
(`sc-domain:` cannot be queried as a URL prefix).

## [1.242.0] - 2026-08-04

WP-5 account data hygiene and WP-7 pipeline reconciliation, plus the requested
Customer form layout rework.

### Added

- **Account Data Quality report + assisted bulk assignment (WP-5).** Of 1,621
  Customers, 1,292 have no industry, 1,160 no customer group and 1,118 no value
  stream — so every downstream package (value-stream dashboards, territory-filtered
  target lists) is reporting on a fraction of the book. The report is sorted by
  project count then opportunity count, so the accounts actually worked for come
  first; an alphabetical list would put six hundred dormant records ahead of the
  fifty that matter and be abandoned on day two.

  `bulk_assign` / `assign_value_streams` apply a value **a human picked** to rows
  **a human selected**, skip rows that already carry a value rather than
  overwriting, and are role-gated. Nothing infers an industry from a company name:
  a wrong industry is invisible once written and every downstream report silently
  inherits it.

- **Industry required on commercial accounts (WP-5)** — via
  `data_quality.enforce_industry`, **not** `reqd` / `mandatory_depends_on`.

  This was asked for as "make industry required", and it is. The declarative form
  would have taken the QuickBooks sync down: `_validate_mandatory` runs on every
  save by every caller, and `quickbooks_online/core/mapping.py` inserts Customers
  with `ignore_permissions=True` but **not** `ignore_mandatory`. QBO payloads carry
  no industry, so a `reqd` flag would fail validation on every synced customer and
  park it in manual review — on the system that is the book of record until the
  1 January accounting go-live, and which is explicitly ring-fenced.

  The hook can tell the cases apart: it honours `doc.flags.ignore_mandatory` (which
  `api/telephony.py` and `fountain_move/conversion.py` already set, so neither
  needed changing), exempts background jobs (`frappe.request is None` — a scheduled
  poll is not a person who can be asked for a value), exempts bulk contexts, and
  never asks a residential account for an industry. Two settings:
  `require_industry_on_commercial` (**on**, new records) and
  `require_industry_on_edit` (off — turn on once the 732-row backlog is cleared).

- **Closed Won Reconciliation report + assisted linking (WP-7).** 200 Closed Won
  Opportunities have no linked Project. 199 are `opportunity_from = "Customer"`, so
  `party_name` is a real Customer id rather than a fuzzy name; 138 belong to a
  customer who already owns a Project. Candidates are ranked within a customer by
  date proximity (50), value proximity (30) and whether the project is already
  spoken for (20) — customer identity is a **precondition, not a score component**.

  **Nothing links automatically.** A wrong link corrupts revenue attribution, which
  is the exact defect WP-1 exists to fix, so an auto-linker would manufacture the
  problem the programme is trying to remove. Scoring is read-only, every candidate
  carries its reasons so a reviewer can disagree on sight, and
  `link_opportunity_to_project` writes one link at a time, refuses to overwrite,
  and guards both directions so two Projects cannot claim one Opportunity.

- **Named Account Targets report (WP-7)** — outreach list by industry, territory,
  customer type, account status and value stream, sorted by staleness rather than
  alphabetically. Deliberately has **no scale filter**: the event-planner scale
  taxonomy is undecided, and a filter built against a guessed field would quietly
  match nothing. The report states its own data-quality caveat with live numbers.

- **`docs/industry-type-proposal.md`** — a keep/merge/retire proposal for the 89
  Industry Type values. 42 have never been used on any Customer, Opportunity or
  Lead (including a stray record literally named `mark`). The one that costs
  reporting accuracy today: `Event Planner` (15) and `Event Planning` (18) are the
  same thing spelled two ways, so event planners are currently the 5th *and* 8th
  largest segments instead of the third largest at 33. **Proposal only — nothing
  executed**, per the original instruction.

### Changed

- **Customer form layout.** `customer_type`, `territory`, `custom_parent_account`
  and `custom_description` moved into the first section, with classification
  (type / status / group / industry / value stream) in column one and relationship
  and contact fields in column two.

- **The five Stripe fields moved to their own collapsed section.** They were
  anchored on `customer_primary_contact`, which dropped four read-only integration
  fields into the middle of the identity block — the first thing anyone saw on
  opening an account was a Stripe id nobody types. Changed in
  `stripe_payments/setup.py`, which is their only source of truth: they are
  `is_system_generated`, so the fixture export filters them out.

- **The Attribution section no longer splits the Customer identity block.** v1.241.0
  anchored it on `custom_lead_source`, which put a Section Break mid-section and cut
  the first section in two. Re-anchored, and the whole Customer `field_order` is now
  explicit about the attribution and Stripe fields so placement is deterministic
  rather than depending on how `insert_after` and `field_order` resolve against each
  other.

### Fixed

- `patches.retype_legacy_company_customers` finishes a half-done migration: this
  site extended `customer_type` with Commercial/Residential, but 364 rows were left
  on the legacy `Company` value. Those 364 are **100% missing both industry and
  customer group** (Commercial is 70%/67%) — an un-migrated import, not a
  classification. Scoped to that **full signature** rather than to `customer_type`
  alone, so a genuine `Company` carrying real data is never touched. Every affected
  id is logged to the Error Log **before** the update, because the two values are
  indistinguishable afterwards and the change would otherwise be one-way.

### Notes

The `require_industry_on_edit` toggle should stay off until the 732 commercial
customers with no industry have been cleared through the new report. Turning it on
first means editing a phone number on any of them fails with a mandatory-field
error about a field the user never touched.

Order of operations for the Industry Type proposal, if approved: **merge first,
then retire** (retiring a value a merge was about to fold in strands its records
with a blank industry), and do both **before** enabling `require_industry_on_edit`.

## [1.241.0] - 2026-08-04

Marketing and field systems, from the 4 August leadership review: attribution capture
(WP-1), the job-photo capture gate (WP-2), photo routing (WP-3) and the payroll hours
export (WP-8). Everything ships **off by default** behind settings toggles.

### Added

- **Lead attribution pipeline (WP-1).** `crm_enhancements/attribution.py` captures raw
  acquisition data in a collapsed "Attribution" section on Lead, Opportunity **and**
  Customer, and propagates it first-touch across Lead → Opportunity → Customer. New fields:
  `custom_utm_source/_medium/_campaign/_content/_term`, `custom_gclid`,
  `custom_landing_page`, `custom_first_referrer`, `custom_attribution_captured_on`.

  **Why these are our own fields and not erpnext's.** erpnext v16 already ships
  `utm_source`/`utm_medium`/`utm_campaign`/`utm_content` on Lead and Opportunity, and we
  deliberately do not write campaign data into them. `Lead.before_insert` mints a stray
  second Contact unless `utm_source == "Existing Customer"` and `customer` is set — the
  suppression the fountain-move conversion depends on — so a campaign name there would
  silently start duplicating Contacts. And `utm_medium`/`utm_campaign` are **Links** into
  taxonomies, while raw capture has to accept whatever arbitrary string is in a URL: a Link
  either rejects the submission or spawns junk taxonomy rows. `custom_lead_source`
  (Link → `Lead Source`) stays the single human-facing channel, because that is the list this
  site actually populates (~696 Customers) while `UTM Source` is a near-identical parallel
  list that is empty apart from the suppression sentinel.

  `_fill_blanks` is the only function that writes attribution onto a document, so
  "first touch wins" has exactly one implementation to audit.

- **Website lead ingress (WP-1).** `crm_enhancements/web_lead.submit_web_lead` — a
  machine-to-machine POST endpoint gated by a Bearer shared secret (constant-time, fails
  closed when unset), rate limited, with a field allowlist rather than a payload splat.
  Guest inserts require `ignore_permissions=True`, under which frappe's permlevel check
  returns early, so `read_only` in a DocType JSON stops nothing and the allowlist is the
  actual control. The full payload contract is in `docs/attribution-runbook.md`.

  **The capture script is not in this repo.** The public site is WordPress on WP Engine
  behind Cloudflare — a different host from ERPNext — so the browser-side snippet that reads
  `utm_*`/`gclid`/referrer belongs there. Only the ERPNext half is here.

- **Attribution Gaps report** (CRM Enhancements). Leads and Opportunities with no
  acquisition channel, grouped by owner, separating *live* process failures (blank, only
  possible after 2026-08-01) from the historical backfill bucket. Explicit roles.

- **Job-photo capture gate (WP-2).** New `Job Interval Photo` child table plus `photos`,
  `photo_status`, `photo_skip_reason` and `photos_pending_upload` on `Job Interval`; a
  "Job Photo Capture" section in Time Kiosk Settings (`require_job_photos`,
  `min_photos_per_interval`, `allow_photo_skip`, `require_skip_reason`); and enforcement in
  `workforce/photo_gate.py`, called from `api.time_kiosk.log_time` for **Switch** and
  **Stop**. Pause/Resume are not gated — pausing for lunch is not the end of a job, and
  requiring a photo for it would train everybody to skip.

  **A Pending upload counts as captured.** The photo row is written the instant the shutter
  fires, keyed on a device-minted `client_uid`; the bytes follow whenever there is signal.
  A technician on a site with no coverage can still clock out. This is deliberate: somebody
  physically unable to leave a site because an upload is failing would destroy trust in the
  system faster than the missing data justifies. `allow_photo_skip = 0` turns the gate into
  a hard block with no escape hatch — **that remains an open people decision** and ships off.

  Enforcement is server-side. The kiosk prompts too, but a field device is offline half the
  day and serving a cached bundle of unknown age, so that prompt is a courtesy.

- **Offline photo queue in the kiosk PWA.** Captures register with the server before their
  bytes upload, survive a page reload in `localStorage`, and drain on the `online` event.
  The camera button routes through `capturePhoto`, not the generic attachment upload — a PDF
  of a delivery note is not a job photo and must not satisfy a photo requirement.

- **Photo routing (WP-3).** `workforce/photo_routing.py` copies each photo onto the Project
  and Task and tags it (`job-photo`, `cust:…`, `vs:…`, `shot:…`). It **does not call Google
  Drive**: `google_drive/drive_sync.on_file_attached` already owns that end to end and is
  already idempotent (it bails once `custom_drive_file_id` is set) with quota/auth failures
  logged replayably. A second upload path would mean a second idempotency bug. Routing's job
  is to ensure the File is attached to a document that *has* a folder — the Project normally,
  falling back to the Customer when the Project has no folder or carries
  `custom_drive_folder_missing`.

- **Job Photo Library** and **Job Photo Compliance** reports (Workforce), and **Featured
  Jobs** (Project Enhancements) with a new `Project.custom_feature_this_job` flag. The
  library is the marketing browse view — filter by customer, project, value stream and date,
  thumbnails inline, no knowledge of the field operation required. Featured Jobs sorts
  upcoming work first so photography can be arranged *before* the crew leaves.

- **Payroll hours export (WP-8).** `workforce/payroll_export.py` reproduces the Shaw &
  Nielsen semi-monthly workbook (firm `SHAWA2530`, client `5813`) from Job Interval data —
  header block, three-row stacked column header, 14 fixed-position columns, Totals row,
  sheet name — transcribed from a real submitted file rather than inferred. Salaried
  employees report a flat 86.67 hours (2080 / 24). Adds a `Payroll Hours Export` report with
  a "Download Workbook" button (a Query Report's own Excel export cannot reproduce the
  provider's header block) and `Employee.custom_payroll_classification` /
  `custom_healthcare_stipend`.

  **The overtime columns are emitted blank, and that is a refusal rather than an omission.**
  The work package assumed rates were configured in ERPNext; they are not, and there is
  nowhere for them to be — **`hrms` is not installed on this site** (no Salary Structure, no
  salary slips, no payroll module, 0 Timesheets). `Qualified OT` is a federal tax figure, and
  reimplementing FLSA premium arithmetic to save a payroll bureau a calculation they already
  perform correctly is the worst trade available.

### Changed

- `api.time_kiosk.log_time` accepts `skip_reason`, consulted only by Switch and Stop. Both
  gate **before** mutating the interval, so a refused close leaves the job open rather than
  half-ended.
- `get_current_status` returns `photo_count`; `get_kiosk_bootstrap` returns a `photo_gate`
  block. The client only ever *raises* its local count from that number — an offline capture
  the server has not heard about must not be un-counted by a status refresh.
- `Lead` gains a `doc_events` block (it had none), and `Customer`/`Opportunity` gain
  attribution handlers.

### Fixed

- Nothing. This release adds; the correction it carries is documentary — see below.

### Notes

Three findings from the pre-build survey that contradict the assumptions the work was
scoped against, recorded here because they are the kind of thing that is expensive to
rediscover:

1. **`Opportunity.source` does not exist as a field.** The 370/815 "missing source" figure
   reproduces exactly, but against a *dead column* — erpnext v15 renamed the field to
   `utm_source` and frappe never drops columns. Live attribution coverage was ~0% (814/815
   with no `utm_source`, 809/815 with no `custom_lead_source`), not 55%.
2. **Google Search Console has never worked.** `Marketing Web Snapshot` has pulled nightly
   since 2026-06-26: GA4 succeeded 40/40 days, GSC failed 40/40 with `HTTP 403`. Organic
   clicks and impressions have been 0 for the entire history of the dataset. This is a
   Google-side grant, not a code bug.
3. **The time clock is unused.** 0 Job Intervals and 16 Time Kiosk Logs in production, so
   WP-8 cannot be reconciled against a manual pay period yet, and the photo gate has never
   met a real crew. Do not schedule a payroll cutover against this until a full period has
   been clocked.

`Value Stream` / `Value Streams` were investigated and **not** changed — findings and a
recommendation are in `crm_enhancements/README.md`.
Both are in daily use (the plural is the 6-row master, the singular is the 1,460-row child
table); only two genuinely dead Link fields were found, and removing them needs sign-off.

## [1.240.1] - 2026-08-03

### Fixed

- **Six `doc_events` handlers were silently dead in production.** `hooks.py` is one large
  dict literal, and Python resolves a repeated key by keeping the **last** one and
  discarding everything under the earlier — no error, no warning at import, nothing at
  runtime. Two doctypes were registered twice, so the first block of each was thrown away:

  | Doctype | Lost |
  |---|---|
  | `Task` | elapsed-time calculation (`before_save`) · Google Calendar sync (`after_insert`) · **recurring-task generation**, project-dashboard realtime updates and **project date sync** (`on_update`) · project date sync (`on_trash`) |
  | `Sapphire Maintenance Record` | **next-visit-date update** (`on_submit`) |

  Both overriding blocks are the Training module's compliance hooks, so this arrived with
  that module. The symptom is absence — recurring tasks quietly stop generating, project
  dates quietly stop moving — which is why it went unnoticed and why anyone chasing it
  would have been looking at the wrong code entirely.

  Fixed by folding the training `validate` hooks into the existing blocks, keeping their
  explanatory comments. All seven Task handlers and both maintenance handlers now
  register; verified by walking the parsed hook tree, not by eye.

### Added

- **A CI guard so it cannot recur** (`tests/test_hooks_integrity.py`). Reads `hooks.py`
  with `ast` — importing it would pull in `frappe` — and fails on a duplicate key in
  **any** dict literal, a handler registered twice for one event, a repeated entry in a
  hook list such as `after_migrate`, or a handler that is not a dotted path into this app.
  It also names the six lost handlers explicitly as a regression guard.

  <why a test and not the linter: ruff already reports this as `F601`, but the lint job is
  `continue-on-error` on this repo because of a pre-existing backlog, so it cannot fail a
  PR. Verified the guard actually catches the defect by re-introducing it — three of the
  six tests fail, from three different angles — rather than assuming it would.>

## [1.240.0] - 2026-08-03

### Added

- **Customers now get a branded quote, order and invoice** (WI-020). There were no print
  formats for Quotation, Sales Order or Sales Invoice at all — every sales document fell
  back to the stock `* Standard` layout, which is unbranded and shows internal fields.
  From 2027-01-01 these are what leaves the building, so this is a hard cutover gate.

  Three Jinja formats — `Quotation - Sapphire`, `Sales Order - Sapphire`,
  `Sales Invoice - Sapphire` — following the Purchase Order format shipped in v1.201.0
  exactly: same `after_migrate` upsert, same print-safe CSS rules, same palette. Registered
  **above** `ensure_chrome_pdf_generator` in the hook list, which is last on purpose and
  has to see them to point them at the right PDF backend.

  <the trap this design exists to avoid: a template with `custom_format = 1` gets **no**
  letterhead injected — Frappe builds the `#header-html` block only for *standard* formats.
  The Purchase Order format went to suppliers unbranded for a month on exactly that, so all
  three render `letter_head` themselves and a test asserts it.>

  Content decisions worth knowing: the quotation prints its **validity** prominently,
  because a quote with no visible expiry is the one that comes back accepted at last
  year's price. The invoice prints **Amount Due** as well as the total, since a
  part-paid invoice showing only the grand total is the most common cause of a customer
  paying twice. The **Stripe pay link renders only when one exists** — no placeholder
  button, because an unusable "Pay now" is worse than none.

- **The sales formats are compiled *and rendered* in CI**
  (`tests/test_sales_print_formats.py`, 23 tests). A print format fails in the worst
  available way: the deploy succeeds, nothing logs, and the defect is discovered by a
  customer holding the PDF. So the suite renders all three against sample documents —
  one item, ten items, no items, no taxes, and every optional field blank at once — and
  checks the failure modes that are silent: the letterhead, `description` rendered as
  markup while `item_name` stays escaped, no flexbox or grid (the PDF backend on this
  host does not lay them out reliably), a table header that repeats across pages, and
  every `fmt_money` carrying an explicit currency.

  It also asserts the Quotation template never references `doc.project` — **Quotation has
  no `project` column on this site**, and Jinja would render it blank rather than error.

## [1.239.1] - 2026-08-03

### Added

- **The CPA's Utah sales-tax guidance is on the record** (WI-036 / OD-2), reproduced
  verbatim in `docs/migration/wi036-utah-tax-guidance.md`. OD-2 named the CPA's written
  confirmation as the go-live sign-off gate, so what he actually said belongs in the repo
  rather than in an inbox. **Docs only** — nothing configured, and production still has
  0 Tax Rules and 0 Tax Categories.

  <what it settles: the default flips to **taxable unless specifically exempt**, and
  exemption turns out to be a property of the *customer* — resale, exempt organization,
  contractual — each evidenced by a certificate. That maps onto native Tax Category on
  Customer driving a Tax Rule: set once, survives staff turnover. He also asked for a
  QuickBooks-style per-invoice checkbox.>

  <what it does NOT settle, and it is the big one: the guidance never mentions
  **real-property improvement**, which is the premise the whole chart was designed
  around. `COA_DESIGN.md` §6 treats Build as improvement to real property — Sapphire
  consumes the materials, pays tax on purchase, does not charge the customer — and from
  that derives `2136 Use Tax Payable` and `60920 Sales & Use Tax Expense`. Read literally
  the guidance points the other way: a Build customer is an end-user, not a reseller and
  not exempt, so Build would charge tax. Those are opposite tax positions, and the plan is
  explicit that neither it nor anyone executing it is the tax authority. Recorded as
  blocking rather than resolved by assumption.>

  Also recorded: an **exemption-certificate gap**. The guidance recommends gathering a
  certificate twice, and nothing in the plan stores one — a claimed exemption with no
  certificate on file is an undefended position, and certificates expire with nothing
  today to notice. WI-036 covers rates and templates and is silent on evidence.

### Fixed

**Both training tools were gated as writes, so with AI write gating on they
returned a confirmation card instead of an answer.** `training_compliance_status`
and `training_learner_record` shipped in v1.216.0 without being added to either
classification set in `assistant_tools/_gate.py`.

That is not a missing label, it is a functional failure. `is_mutating()` checks
`EXPLICIT_MUTATING`/`APP_MUTATING`, then `EXPLICIT_READONLY`, then the tool's FAC
category — and FAC seeds every external tool as category `read_write`, which
matches neither the write branch nor the read branch. Control reaches the
fail-closed `return True`. The fallback is correct in principle (a wrongly gated
read is friction, a wrongly executed write is damage), but it means an
unclassified *read* tool records an AI Pending Action and hands the model the
anti-fabrication envelope — "this has NOT run, a human must confirm it in the
desk" — for a question that only ever read. Asking whether the team was current
on its training produced a confirmation prompt nobody could meaningfully approve.

Both names are now in `EXPLICIT_READONLY`.

**Twelve more read-only tools advertised no MCP annotations at all.** Only the
v1.71.0 device batch and the water tools set `self.annotations`; every read tool
older than that shipped without them, so `tools/list` said nothing about their
mutation state and an MCP client (Triton) fell back to guessing from the tool's
verb. The guess happened to be right for all twelve — but guessing that happens
to be right is not a contract, and it is the same mechanism that mis-read
`remote_wipe_device` as read-only before v1.71.0. All fourteen read-only tools
now call `annotations_for(self.name)`.

### Added

- Two contract tests in `tests/test_assistant_tools_schema.py` that make this
  class of defect unrepeatable: `test_every_registered_tool_is_classified`
  (every hook-registered tool lands in exactly one `_gate.py` set) and
  `test_readonly_tools_advertise_readonly_annotation` (the read-only mirror of
  the existing mutating-tool assertion). Both were confirmed to fail against the
  pre-fix code before being kept.
- A "Classification is mandatory" section in `assistant_tools/README.md`
  explaining the fail-closed path, plus the six tool rows the table had been
  missing since v1.90.0 (`training_compliance_status`, `training_learner_record`,
  `water_calc`, `water_design_status`, `save_water_design`,
  `control_panel_status`).
## [1.239.0] - 2026-08-03

### Added

- **A missing Project on a Sales Invoice is now something you can see** (WI-008). Job
  profitability needs every revenue dollar tagged to a job, and until now nothing linked
  the two: `project` sat inside the form where a blank field is invisible, and no code
  associated invoices with projects at all.

  Two changes, no code. `project` becomes a **column on the Sales Invoice list**, so a
  blank cell is noticed in passing. And an **"Invoices without Project" tile** joins the
  Finance Hub built in v1.237.0, opening the list filtered to `project is not set` and
  not cancelled.

  The tile deliberately replaces the "saved List View filter" the work item called for.
  A saved filter lives in one person's browser, belongs to one user, and vanishes when
  they are on holiday or leave. As a workspace tile it is version-controlled, sits on the
  page the accountant already works from, and outlives whoever set it up.

  <deliberately not built: a submit-time validation that warns when a customer has an
  active project and `project` is empty. That stays a documented Phase-2 option, to be
  built only if the December parallel run shows persistent misses — writing it now would
  be assuming people will get it wrong.>

### Changed

- **The order-to-cash chain is documented per value stream** (WI-007), which is most of
  what that work item actually was: `Selling Settings.so_required` and `dn_required` were
  already `No` on production, and both **must stay that way** — the maintenance module
  drafts Sales Invoices with no Sales Order behind them, and January's opening-AR invoices
  have no order either. Setting them to `Yes` would block both.

  So "a Sales Order is required for Build work" is a *procedure*, not a system rule, and
  the SOP says so plainly rather than implying enforcement that does not exist. What
  catches a miss is the UAT sample during the parallel run and the weekly review of the
  new tile.

  Production is a clean slate for this — **0 Sales Orders and 0 Delivery Notes have ever
  been created** — so there is no legacy chain to reconcile, only a habit to establish.

  SOP: `docs/migration/wi007-o2c-chain.md`

## [1.238.0] - 2026-08-03

### Changed

- **The accountant no longer holds `Workspace Manager`** (WI-018, part two). This is the
  one change that makes the rest of WI-018 possible, and on its own it changes nothing
  she can do with a document.

  `get_workspaces()` opens with `has_access = "Workspace Manager" in frappe.get_roles()`,
  and when that is true it **drops the query filters entirely** — role restrictions on a
  Workspace, `is_hidden`, and blocked modules are all bypassed. While she held the role we
  could restrict the Finance Hub by role, hide the six empty hubs and block twenty
  modules, and she would still have seen all 61 workspaces. Every lever in the work item
  was inert.

  It also meant WI-018's original acceptance criterion — `COUNT(*) FROM tabBlock Module
  WHERE parent=<accountant> > 0` — **could pass with her sidebar completely unchanged**.
  It measured that a row had been inserted, not that anything had happened. Replaced in
  the work item with an assertion that `has_access` is false, checked first.

  <what she actually loses: editing workspace layouts in the Desk. Nothing else.
  `Workspace Manager` and `block_modules` are read only by the desk's workspace and
  desktop-icon code; `frappe/permissions.py` never consults either, so read/write on every
  document is untouched. She can still create a private workspace of her own, because
  `Desk User` — auto-granted to every System User — holds create on Workspace.>

  Deliberately shipped alone, on its own release, because it is the one change in WI-018
  that will feel like something was taken away. Undoing it is a re-tick in the Desk and
  takes seconds. Six other holders are untouched (Administrator, the CEO, the sys-admin,
  the purchasing agent, the external CPA, and the Triton service account); trimming those
  is a WI-011 amendment, not this work item.

  <mechanism note: written with `frappe.db.delete` so an 89-role User is not re-validated
  to remove one row, which means the document cache must be cleared by hand.
  `frappe.clear_cache(user=...)` does **not** reach it — that clears `user:<email>*` keys
  while the User doc is cached separately under `document_cache::User::<email>` for an
  hour, and `get_workspaces()` reads roles from `get_cached_doc`. Without the explicit
  `clear_document_cache` the change looks like it silently did nothing.>

## [1.237.0] - 2026-08-03

### Added

- **The Finance Hub is now a place you can actually work from** (WI-018, part one).
  It has existed since February as an empty shell — `content = "[]"`, no shortcuts, no
  links — sitting at the top of everyone's sidebar rendering a blank page. It now
  carries the accountant's six daily entry points (Purchase Invoice, Sales Invoice,
  Payment Entry, Journal Entry, Bank Reconciliation Tool, Document Intake) and two
  cards: the five reports she actually runs, and the period-end set.

  It **curates the existing workspace rather than adding an `Accounting` one**, which
  matters more than it sounds: production already carries *Finance Hub*, *Finance
  Dashboard*, *Invoicing* and *Financial Reports*, and a work item whose whole premise
  is minimal UI should not answer four finance-named surfaces with a fifth. The
  deciding detail is that **Finance Hub already has a `Desktop Icon`** — the desk's left
  rail is built from those, and nothing on the `bench migrate` path creates one, so a
  brand-new workspace could have shipped perfectly and been unreachable.

  Hosted in the **Accounting Intake** module deliberately. `Workspace.__init__` raises
  `PermissionError` when a workspace's module is not in the viewer's `allow_modules`,
  and that list is built from DocType *read* permissions — so the module choice decides
  whether the page exists for her at all. Accounting Intake is the app module with the
  most doctypes readable by the accounting roles, which leaves the most margin.

  Restricted to `Accounts Manager` / `Accounts User` / `System Manager`, matching the
  sibling Finance Dashboard. <what this takes away: about thirteen users stop seeing a
  workspace that renders a blank page for them today.>

  This is the additive half. It removes nothing from the accountant and is meant to be
  the thing she is walked through — that session is what produces the task inventory
  and sign-off WI-018 requires before anything is hidden.

### Fixed

- **Two workspaces carried a shortcut that has never once rendered**, both the same
  defect class as v1.146.0. On *Project Enhancements* the layout block asked for
  `Project Dashboard` while the row was named `Projects Dashboard` — a singular/plural
  typo, so the page drew an empty column *and* swallowed the real shortcut. On
  *KPI Dashboards*, `KPI Target` and `KPI Snapshot` existed as rows with no layout block
  at all, so they sat in config and appeared nowhere.

  A workspace stores its layout (`content`) and its data (the child tables) separately
  and joins them by label; disagreeing either way fails silently, in a way no diff
  review or smoke test catches. `tests/test_workspaces.py` now checks both directions
  across all 33 workspace JSONs, plus `link_count` on every card, and runs in CI. It
  found both of these on its first run.

## [1.236.0] - 2026-08-03

### Fixed

**Watch coverage inflated after the first pause, then froze at 100%.** The wire's
run-length ranges are half-open `[start, end]` — `rle()` in `video.js` has said so
in a worked example since it was written. `_normalise_beat` read the second element
as a *length*.

The two agree on exactly one shape: a run starting at second 0. So a learner who
played from the beginning was credited correctly, every time, and the very first
run that did not start at zero — the first beat after a pause — was inflated.
Verified against production: `[[17, 32]]` means fifteen seconds and was banked as
thirty-two. Three beats later the intervals had swallowed a 90-second video whole,
coverage pinned at 100%, and every beat after that gained nothing.

Nothing raised, because an interval is an interval. There is no translation here
any more, only a rename.

- **A refused heartbeat blocked every heartbeat behind it, forever.** `drainQueue`
  sends oldest-first and had a single `.catch` on the whole chain, so the first
  rejection skipped every later beat *and* left the refused payload at the head of
  the queue to fail again on the next drain. One beat the server would never accept
  — a stale attempt, a lesson key from a republished version — stopped delivery for
  the life of the tab. Failures are now handled per beat; a refused beat moves to
  the back and is abandoned after eight tries.

- **Nothing ever retried a stranded queue.** Beats built while a drain was in
  flight were not in that drain's snapshot, and the only thing that started a drain
  was another beat's worth of credited playback. Pausing right after a flush left
  them there indefinitely. There is now a backing-off retry timer.

- **A replayed backlog was thrown away and the learner flagged for it.** Every
  heartbeat reset the clamp's window to *now*, so the second beat of a drain
  arrived a fraction of a second after the first, `int(0.2 × 1.25)` allowed zero
  seconds, and the rest of the backlog was discarded — with an over-claim note
  written against somebody who had done nothing but ride a lift. That is precisely
  the path the offline queue exists for. The window now advances by what each beat
  actually spent, so a burst shares one budget.

- **Four separate ways for watch credit to latch off permanently.** `seeking`,
  `stalled`, `blurredSince` and `onScreen` are each set by one DOM event and
  cleared by a different one — and browsers decline to fire the second one more
  often than is comfortable: an aborted seek fires no `seeked`, `stalled` fires at
  an idle paused video, a window may never regain focus, an IntersectionObserver
  goes quiet in native fullscreen. Any one of them meant the learner watched the
  rest of the lesson for nothing and was told nothing. Media time advancing on a
  playing element now clears them, which gives away no credit: a real seek still
  fails the two-clock allowance check, which is the actual anti-skip machinery.

- **A queue that could not drain was invisible.** `emit("offline")` had no
  subscriber anywhere in the app, so it looked exactly like nobody watching. The
  learner is now told, and told that their progress is safe on the device.

- **One hung request stopped delivery for the life of the page.** `fetch` has no
  default timeout, and a socket an intermediary drops without a FIN leaves its
  promise pending forever. The `flushing` latch is released only when that promise
  settles, so every later beat queued itself and returned without sending. Driven
  in a harness: exactly one beat attempted, three stacked in `sessionStorage`,
  coverage `null` permanently — which is indistinguishable, from outside, from a
  learner who walked away. It also matches the production state of the reported
  attempt exactly: one `record_heartbeat`, ever.

  The page transport now gives every call a 20s `AbortSignal` (with an
  `AbortController` fallback for the older mobile Safari most likely to lose a
  socket), and the player no longer trusts that it did — a latch held longer than
  45s is treated as stranded, released, and counted in `self_healed`.

### Added

- **`claim_mismatch`.** The player has always sent `claimed_seconds` — its own
  count of the seconds in a beat, arrived at independently of how it encodes the
  ranges — and the server has never read it. Those two numbers are a check on each
  other, and they were a factor of two apart for the whole of v1.235.0. The server
  now compares them and flags a disagreement for the auditor. It flags rather than
  trims: compaction and adjacency merging over-credit on purpose, and trimming here
  would fight those decisions quietly.

- `self_healed` on the beat, counting the latches the watchdog had to clear. Zero
  on a healthy browser; anything else names a player bug that would otherwise
  present as "the meter stopped" and be indistinguishable from a learner who walked
  away.

### Notes

- `tests/test_training_heartbeat_wire.py` pinned `[start, length]` and separately
  asserted the player still emitted a key *named* `ranges`. Both halves were
  checked and neither was checked against the other, so the server was free to
  disagree with the client about what the numbers meant. It now derives the
  expected shape from `rle()`'s own worked example, and fails if that example drifts
  from the code above it.

- The mutation harness had a trap worth recording: swapping `if depth == 0` for
  `if depth >= 0` is the same byte length, so the restored file kept its size and
  mtime-second and CPython served the **mutated** bytecode from `__pycache__` on the
  next run. It reported a failure that no longer existed. Mutation runs now use
  `-B`, and one suite per process — `test_training_progress` and
  `test_training_heartbeat_wire` each install their own `frappe` stub and cannot
  share one.

## [1.235.0] - 2026-08-03

### Fixed

**Watch coverage never moved, because every heartbeat since Phase 2 was a no-op.**

`heartbeat(attempt, payload=None)` is the only runtime endpoint that takes a nested body.
The player spread the beat across the top level instead — which is right for every other
endpoint, and here meant Frappe bound `attempt`, left `payload` at its default of `None`,
and the server recorded an **empty beat**.

Nothing errored. An empty beat is a perfectly valid beat that credits nothing. So coverage
stayed at 0%, the gate never opened, and the meter never moved — through every deploy since
the telemetry was written. The beat now travels under `payload`, where the endpoint expects
it.

- **The pagehide flush discarded beats it never sent.** `video.js` drops a beat from its
  retry queue on a truthy return from `heartbeatBeacon`, because `navigator.sendBeacon`
  returns a boolean. The generic transport wrapper returned a **Promise** — always truthy —
  so every queued beat was dropped as delivered whether or not it left the machine. That is
  the exact path meant to protect a learner whose phone locks mid-video.

  `heartbeatBeacon` is now a real `sendBeacon` returning its own boolean, with the CSRF token
  in the body (it cannot set headers) and the beat under `payload` like the normal path.
  Deliberately `csrf_token` and never a key named `sid` — Frappe's auth pops that one and
  reports a session expiry that did not happen.

### Added

- Assertions on the heartbeat's *shape*, checked against the endpoint's real signature so a
  future flattening of the server fails here rather than silently zeroing coverage again.

### Notes

- The earlier transport-argument scanner could not have caught this: it only inspects call
  sites written as object literals, and `transport.heartbeat(payload)` passes a variable. The
  new assertions check the **adapter**, which is where the shaping actually happens.

- This is the seventh wire mismatch in this module and the last of the ones a learner could
  hit. The Python was correct at every one of them.

- One mutation initially survived: swapping the beacon's send for a fetch, because
  `navigator.sendBeacon` still appeared in the capability guard on the line above. Presence
  is not a return path — the same lesson, for the sixth time in this module's history.

## [1.234.0] - 2026-08-03

### Fixed

- **The smoke test reported a security regression that had not happened.** Its sign-off step
  calls `finish_attempt` before recording a sign-off and expects a refusal — which is only
  true the first time it runs. A sign-off is matched on the **course**, not the course
  version, so the previous run's `Competent` sign-off is still valid, the gate correctly
  opens, and the assertion failed.

  That matching is deliberate: somebody who was watched draining a basin last month has been
  watched draining a basin, and a reworded paragraph should send them back through the
  material rather than back in front of a supervisor. The step now detects a prior sign-off
  and skips with the reason and the record's name.

  A harness that cries wolf on a re-run is worse than one that skips a step, because the next
  person either stops trusting it or "fixes" a gate that was working.

### Added

- Assertions pinning that `_signoff_outstanding` scopes to the course and **not** the version
  — the decision a later reader is most likely to "tighten" while believing they are
  hardening it — and that the harness skips rather than fails.

### Notes

- Verified against production after the 1.233.0 deploy: `after_migrate` completes (all five
  starter badges seeded with the criteria the awarder evaluates), no errors in three hours,
  and the server-side end-to-end passes 16 of 17 with the one skip above. In the browser the
  action bar is visible, the outline lists all three lessons, the quiz mounts with three
  questions and nine options, no answer markers reach the client, and the video block reports
  "Watch more of the video — 0% of 80% so far." — the coverage gate wired to the real
  threshold and refusing completion.

- Three of this change's own mutations initially missed, each for a different reason worth
  recording: one patched the wrong occurrence of `"course": doc.course,` (the same literal
  appears in `_issue_completion`), so the assertion under test was never exercised; one
  checked that a message existed rather than that it was reachable; and one compared position
  against the wrong occurrence of the word "skipped". A mutation that does not hit the code
  under test proves nothing, and reads exactly like a passing one.

## [1.233.0] - 2026-08-03

### Added

**A Drive folder id outlives the folder, and nothing here ever noticed.** Clicking
**Open Drive Folder** on PRJ-00706 opened a bare Google 404. The stored id
(`1pG6Usz7YoSX_W8ch_A1-E3WA2alNzMZC`) was fine, the link shape was fine, the service
account was fine — the folder had simply been deleted in Drive, and `custom_drive_folder_id`
went on pointing at it. Verified against production: of 243 distinct Project/Opportunity
folder ids, three are gone (PRJ-00706, PRJ-00695, CRM-OPP-2026-00113), and Google answers
404 for them to everyone, service account included. Everything else resolves, so this was
never a broken-link-builder bug — the links are correct, some of their targets stopped
existing.

The shadow sync already *knew* about some of these. `_sync_folder_shadows` flags an
unreachable root folder as `Stale` in the Drive Sync Log — 13 such rows sat there from
2026-07-24 — but only for documents that hourly walk happened to reach, and it only ever
wrote to the log. The record was never told, so the form had no way to know, and the
button had nothing to read.

- **`reconcile_drive_links` (daily)** probes every `custom_drive_folder_id` on Project /
  Customer / Opportunity via the existing `drive_utils.get_folder_meta` — which already
  returned `None` on a 404, it had just never been pointed at this — and stamps the new
  hidden Check field `custom_drive_folder_missing` on the ones that are gone. **Trashed
  counts as gone**: a folder in the Shared Drive trash still resolves for the API but is
  not a place you can send a person, and "can I send someone here" is the only question
  the button needs answered.
- **The flag lives on the record, not in the log.** That is the whole point — the button
  reads it off the loaded document for free. Probing Drive on click would put a network
  round-trip in front of every folder open.
- **Both directions.** A folder restored from the trash, or re-shared with the service
  account, clears its own flag on the next run. Its old `Stale` rows are then marked
  `Skipped`, because `_flag_missing_drive_item` dedupes on the Drive id — leaving them
  would silence the *next* disappearance of that folder permanently.
- **Gated on the service account being configured, not on `attachment_sync_enabled`.**
  The button is there whether or not the shadow sync is on, so its links need checking
  either way.
- **`Check Drive Links`** on Project Folder Google Drive Settings runs it on demand; the
  point of finding a dead link is usually that somebody is looking at one right now.
  Results land in the Drive Sync Log under the new `Reconcile Link` action.
- The hourly shadow sync now stamps the same flag when it finds a linked root folder
  missing, so the common cases are caught within the hour instead of the day.

Failure containment copies the shadow sync's hard-won contract rather than reinventing it:
this walk also holds a DB connection across minutes of uninterrupted Google traffic, so one
record's failure goes through `_recover_after_document_failure` — recover *before* logging,
because `frappe.log_error` needs the connection too.

### Changed

**The Open Drive Folder button stops offering a link it knows is dead.** When
`custom_drive_folder_missing` is set it renders as **Drive Folder Missing** and explains
what happened — the folder id, that Google would answer 404, and where to fix it (Drive
Link Manager, or restore from the Shared Drive trash and re-run the check). It stays a
button rather than disappearing: the link being dead is information the person wants, and
silently hiding the control just sends them hunting for something that used to be there.

Guarded by `tests/test_drive_link_reconcile.py` (bench-free, own CI step — it installs its
own `frappe` stub in `setUpModule`, so it must not share a process with the other
stub-installing suites).

## [1.232.0] - 2026-08-03

### Added

**The commission report now keeps its own dates and goes out on the pay periods it
already claimed to.** "Brian Commissions Report" has told its recipients since the day
it was written that reports arriving on the 16th cover the 1st–15th and reports arriving
on the 1st cover the 16th–end of the previous month. Nothing implemented that. The
underlying `Brian's Closed Won` report is a Report Builder report whose saved filters
carried a hand-retyped `custom_date_closed_won > 2026-07-16`, and the email went out
weekly on Mondays — so the window only moved when somebody remembered to move it, and
the send day matched neither half of the promise.

- `crm_enhancements/pay_period_reports.py` owns the window and the send, on a **daily**
  `0 7 * * *` cron. `pay_period_bounds` / `previous_pay_period` are generic semi-monthly
  date arithmetic (1st–15th, 16th–last day of month) that WI-017's payroll hours export
  should reuse rather than re-derive.

- **None of the three native routes work, and each is worth recording because each looks
  like it should.** `Auto Email Report`'s dynamic date filters — `from_date_field`,
  `to_date_field`, `dynamic_date_period` — are applied only when the report type is *not*
  Report Builder; `get_report_content` guards them with
  `if self.report_type != "Report Builder"`, so setting them here does nothing at all.
  `Auto Email Report.filters` are **appended** to the report's saved filters by
  `Report.run_standard_report`, never substituted, so the email cannot override or widen
  a date baked into the report. And `frequency` offers only Daily / Weekdays / Weekly /
  Monthly, with `dynamic_date_period` only Daily…Yearly — no vocabulary in the doctype
  can express "twice a month, on the 1st and the 16th".

- So the window is written into the report's saved `json`, which is also what makes the
  desk view correct: opening the report shows the pay period **in progress**. The email
  needs the period that just *closed*, and a Report Builder report holds exactly one
  filter set, so the send borrows the window for the length of one `get_report_content()`
  call and the running window is re-applied immediately afterwards — on every path,
  including a send that raised. The cron is daily rather than `1,16` for that same
  reason: a missed tick or a half-dead send self-heals the next morning instead of
  leaving the desk report on a stale period for up to sixteen days.

- Rendering deliberately goes through the existing Auto Email Report doc, so recipients
  keep getting exactly the email they get today — same letterhead table, same
  description, same recipient list, editable in the desk without a code change — only
  with the right rows in it. The subject now carries the period, because the doc name
  alone is identical every time and two statements were otherwise indistinguishable in
  an inbox.

### Fixed

**Deals closed on the 1st or the 16th were falling into neither pay period.** The window
was a `>` against the period's first day, which excludes the boundary. `CRM-OPP-2026-00108`
($500, closed won 2026-07-16) is absent from the live report for exactly this reason. The
window is now `between`, inclusive at both ends, and a test walks all 365 days of a year
asserting every date lands in exactly one period with no gap or overlap between
consecutive ones.

### Changed

- The Weekly/Monday schedule on "Brian Commissions Report" is **disabled** by
  `patches/switch_commission_report_to_pay_periods.py`. That is what takes Frappe's
  `send_weekly` off the doc — left enabled, billing would receive both the weekly mail
  and the new pay-period one. The doc itself is still in active use for its recipients,
  description and rendering; disabled is the off switch here, not a deletion marker, and
  the patch and both READMEs say so. The same patch applies the running window at migrate
  so the report is correct as the deploy finishes rather than at the next 07:00 tick.

- Rewriting the window replaces **only** the date rows, leaving the owner and Closed Won
  filters as found, and writes with `update_modified=False` — a scheduled window move is
  not a person editing the report, and bumping `modified` would hand a
  `TimestampMismatchError` to anyone who had it open. An unchanged window does not write
  at all, so the daily no-op tick is a single `get_value`.

- Re-runs are guarded by a `DefaultValue` key holding the last period actually emailed:
  running the job by hand cannot produce a second statement for a period already paid,
  and a period whose send failed is retried on the next tick rather than skipped.

## [1.231.1] - 2026-08-03

### Fixed

**The Project Brief button is back on the Project form.** It was in the DOM and fully wired
up the whole time — clicking it would have worked — but a stylesheet made it invisible, so
the feature has been unreachable since the day it shipped.

`project_form_script.js` did not want ERPNext's native **Gantt Chart** and **Kanban Board**
entries on the toolbar (the Schedule and Scope tabs replace both), and it removed them the
blunt way: injecting `display: none` on `.inner-group-button[data-label="View"]`, which is
the *whole dropdown*, not those two items. That was harmless while the group held nothing
else. Then v1.183.0 consolidated the seven-button Project toolbar by folding our own
read-only buttons into ERPNext's existing dropdowns — and **View** is precisely where
Project Brief, the **Maintenance Contract** view branch and the **Drive Folder** button went.
All three went dark in the same release that grouped them, and nothing raised: a hidden
button is not an error.

- Both unwanted entries now come off by label, via `frm.remove_custom_button(__("Gantt
  Chart"), __("View"))` and the same for Kanban Board. Frappe's `remove_inner_button` drops
  an inner group once its last item is gone, so a project carrying none of our buttons still
  shows no empty dropdown — the original intent, without the collateral damage.
- Also removed a `setTimeout` calling `frm.page.clear_custom_button('View')`. There is no
  such method on `frappe.ui.Page` (it is `clear_inner_toolbar`), and the call sat behind an
  `if (frm.page.clear_custom_button)` guard, so it had never run and never warned. The CSS
  was doing all of the work, and the comment claiming an "API + CSS fallback" was fiction.
- The style element went into `document.head` under a one-time id guard and was never
  removed, so the damage outlived the form: after a single visit to a Project, any **View**
  group on any other doctype stayed hidden for the rest of the session.
- The stylesheet also targeted `data-label="View Tasks"`, aimed at a legacy Client Script
  button ("Project - Add View Tasks Button", disabled on site). Nothing in the app renders
  that group any more; the selector went with the rest.

### Changed

- `tests/test_project_toolbar.py` gains a guard that no script under `public/js` may hide a
  toolbar group by selecting `.inner-group-button[data-label=…]` / `.custom-btn-group[data-label=…]`
  — unwanted buttons come off one label at a time. It reads sources with comments stripped,
  so the comment above the fix can name the selector it replaced.
- **That suite now actually runs in CI.** It shipped with v1.183.0 asserting the very
  grouping that this release repairs, and was never added to a workflow step, so it ran
  nowhere — the failure mode `CLAUDE.md` warns about, this time by omission rather than by
  the unittest/pytest split. It is a `python -m unittest` step in `ci.yml` now.

## [1.231.0] - 2026-08-03

### Changed

**The fountain-move form now only accepts Monday, Tuesday and Wednesday as preferred days.**
Crews run moves at the start of the week; the form was happily collecting Thursdays, Fridays
and weekends that staff then had to talk the customer back out of on the quote call.

- `PREFERRED_WEEKDAYS = (0, 1, 2)` in `crm_enhancements/fountain_move/__init__.py` is the one
  place the rule lives, with `preferred_weekdays_label()` rendering it as
  "Monday, Tuesday or Wednesday". The server check, the page hint and the browser-side check
  all read those two, so the sentence a customer is shown cannot drift from the rule that
  refuses them.

- Enforced in `intake._normalize_preferred_slots`, which is the **only** real control:
  `type=date` has `min` and `max` but no weekday attribute, so there is no markup to lean on
  and a hand-built POST or a browser that degraded the input to plain text arrives unfiltered.
  The spam path keeps dropping bad slots instead of throwing, as every other slot rule does —
  a new throw path there would hand bots a differentiated response.

- Client-side the rule is applied as `setCustomValidity` on each preferred-date input rather
  than as a bespoke check, so the existing "filled in but invalid" path — the submit-button
  gate and the named hint under it — carries it with no special-casing. Dates are parsed as
  `new Date(iso + "T00:00:00")`: a bare ISO string is parsed as **UTC**, which in Mountain time
  is the evening before, and every date would have reported the previous weekday.

- The lead-time floor (`min_preferred_date`, 3 business days) is unchanged and stays a lower
  bound, not an offer — it can still land on a Thursday, which the weekday rule then refuses.

## [1.230.0] - 2026-08-03

### Fixed

**`bench migrate` died on every deploy, taking the build with it.**

```
Executing `after_migrate` hooks...
AttributeError: module 'erpnext_enhancements.training.gamification'
                has no attribute 'ensure_training_badges'
```

The hook was registered in Phase 4 and the function was never written. Nothing caught it,
because a hook path is only a string until Frappe resolves it — and `after_migrate` is the
**last** thing a migrate does, so the first thing to find out was the deploy, after every
schema change had already been applied.

- `ensure_training_badges` now exists, in `training/setup.py` beside the starter categories
  rather than in `gamification.py`, which is the runtime awarding logic. Insert-only and
  idempotent like the categories, guarded on the Training Badge doctype existing (Phase 4 may
  not have migrated on a given site), and never fatal — badges are decoration, a deploy is not.

- Five starter badges, and every `criteria_type` is one `_badge_is_earned` actually evaluates.
  That is not a detail: an unknown criterion awards nothing, deliberately, so a badge whose
  rule the module cannot answer would sit in the list forever being quietly unearnable. The
  two link-based criteria are deliberately not seeded — they need a course or category that
  may not exist on a fresh site.

### Added

- `tests/test_hook_targets_resolve.py`, in CI. It resolves **every** dotted path in `hooks.py`
  statically — parsing the target module's AST rather than importing it, so it needs no bench
  and runs on every push. A hook naming a missing function is not a subtle bug; it is a typo
  that only a full migrate could surface, and it should cost seconds in CI rather than a
  failed build. The sweep found no others.

- Assertions that every seeded badge's criterion is both evaluated by the awarder and a valid
  option on the DocType's own Select.

### Notes

- Six mutations, all caught, including reintroducing the exact string that broke the deploy.

- Two of the test's own guards were wrong first time and both would have made it silently
  useless: it treated `erpnext_enhancements.bundle.js` (an asset filename) as a module, and
  it located `after_migrate` by the first mention of the word — which is in the module
  docstring 650 lines above the list — and read an empty list from it. A guard that matches
  nothing passes everything.

## [1.229.0] - 2026-08-03

### Fixed

Two causes behind "the quiz still isn't visible and the watched % isn't tracking", and the
first one is not a training bug at all.

- **The kiosk service worker was serving the whole app stale JavaScript.** `kiosk-sw.js` is
  registered at **root scope**, and its fetch handler answered *any* request under
  `/assets/erpnext_enhancements/` cache-first with `ignoreSearch: true`. Its comment argued
  that was safe because "the cache only ever holds this deploy's entries" — but a service
  worker is only replaced when its own script URL changes, and that only happens when
  somebody opens `/kiosk`. So a browser that opened the kiosk once kept serving **that**
  deploy's JavaScript to every other page in the app, indefinitely.

  `ignoreSearch` reduced the `?v=` deploy token to decoration: a brand-new token matched a
  months-old entry. Found in the wild with a training player four releases stale, in a
  browser whose worker cache was named after a deploy from weeks earlier — and nothing the
  page could do reached it, not a new `?v=`, not `cache: "reload"`, not a random query
  string.

  The worker now answers only for its own precached shell, matched against an explicit list
  derived from `PRECACHE`. Anything else goes to the network like a normal request. This was
  a site-wide staleness bug that happened to be found through training.

- **The player's action bar was hidden by the page's own CSS.** `player.js` builds it as
  `<footer class="tr-bottom">`, and the chrome-removal block listed a bare `footer` at
  `display: none !important`. So the element holding **Start the quiz**, **Finish this
  lesson**, the resume button and the gate reasons rendered correctly, with the right label,
  at zero height — invisible to every learner since the page was written. `main` is now
  qualified for the same reason: `main.tr-view` is the player's own.

### Notes

- Verified in a real browser after clearing the stale worker: the quiz mounts with all three
  questions and nine options, and the video block loads its GCS signed URL with
  `readyState: 4` at the full 90 seconds.

- **Watch coverage is not confirmed.** Testing it through browser automation credits nothing,
  because `creditBlocked()` correctly refuses to count a hidden tab and a CDP-driven tab
  reports `visibilityState: "hidden"`. The anti-cheat worked; the test was invalid. The
  reported symptom is fully explained by the stale JavaScript — with no `TR.Video.mount` the
  block rendered "the video player did not load" and never instantiated a player, so no
  sampler ever ran — but that is an explanation, not a measurement, and it needs a real
  visible tab to confirm.

## [1.228.0] - 2026-08-03

### Fixed

**Every video block showed "The video player did not load", and every quiz rendered empty.**
Both since Phase 2. Both hidden by the builder.

- **`TR.Video.mount` did not exist.** `blocks.js` calls it and guards on
  `typeof TR.Video.mount === "function"`; `video.js` exported only the constructor. So the
  guard fired — on a player that had loaded perfectly well — and told the learner to refresh
  a page that was never going to help. `Video.mount` now lives in `video.js` beside the
  constructor it builds, translating the published block shape (`duration_s`, `min_coverage`)
  into the spec the constructor wants (`duration`, `min_coverage_percent`).

  It routes the heartbeat through the player's wrapper rather than the raw transport, because
  that wrapper is what stamps the lesson key, merges the response into progress and refreshes
  the gate — going direct leaves the coverage meter and the Finish button frozen while the
  video plays.

- **The quiz was mounted with the wrong signature.** `quiz.js` is
  `mount(root, ctx, transport)` with the questions at `ctx.quiz` and submission at
  `transport.submitQuiz`; `player.js` passed the payload *as* `ctx` and everything else in the
  third argument. `normalise(ctx.quiz)` therefore got `undefined`: no questions, no attempt,
  no lesson key, and a Submit that went nowhere.

### Changed

- **Removed the builder's runtime shims.** They patched `TR.Video.mount` into existence and
  re-shaped `TR.Quiz.mount`'s arguments, and their own comment conceded they were "not a
  substitute for fixing them". Nothing then fixed them — for four releases — precisely because
  the preview an author checks their work in was the one place the breakage could not be seen.

  A preview that repairs the runtime on its way past is worse than no preview: it removes the
  only place the break would have been noticed. The preview now drives exactly what a learner
  gets, which is the only thing that makes it worth having.

- **The GCS connection test asked for a permission the module never uses.** It pre-flighted
  with `buckets().get()`, which needs `storage.buckets.get` — and **`roles/storage.objectAdmin`
  does not grant it**. So a service account configured exactly as this module documents failed
  the test with a 403, and the error then advised granting objectAdmin: the role it already
  had. Following our own message would have changed nothing.

  The pre-flight now lists one object instead, which distinguishes the same three cases
  (wrong bucket, missing binding, no network) inside the permissions the app actually needs.
  The 403 message names `roles/storage.objectAdmin` **on the bucket**, and says a
  project-level grant works while a grant on a different bucket does not.

### Notes

- This was predicted in PR #690 and written down at the time: *"the preview shims
  `TR.Video.mount` into existence… if `/training` has the same mismatch, the preview works and
  the real player doesn't."* It did. Four releases passed between recording that and a learner
  hitting it, which is the argument for fixing a known seam rather than noting it.

- The load-bearing new assertion is not about either bug: it is that **the builder may not
  assign `TR.Video.mount`, `TR.Quiz.mount` or `TR.Player`** at all. Six mutations, all caught
  first time.

## [1.227.1] - 2026-08-03

### Fixed

**The hourly Google Drive shadow sync aborted whenever its DB connection dropped, and left
no evidence that it had.**

The reported traceback was `MySQLdb.OperationalError: (2006, 'Server has gone away')` raised
three times over — once from the sync, once from the `frappe.db.rollback()` meant to handle
it, and once from RQ's own `rollback(chain=True)`. MariaDB was not, in fact, gone: it has been
up continuously since 24 Jul with no restart and no crash, `max_allowed_packet` is 512 MB
against a worst-case query of ~70 KB, and `wait_timeout` is 8 hours against a worst-case idle
gap of about two minutes. None of the usual explanations apply. What is true is that a single
connection died, and it died where it always dies: at the first query after a Drive walk.

`run_shadow_sync` is the only job in this app that holds a DB connection open across minutes
of uninterrupted Google API traffic — in production it walks 737 linked documents and takes
~22 minutes per hourly run, and within one document the connection sits untouched for the
whole tree walk. Roughly once a day the socket does not survive that. The same call site has
produced both 2006 ("server has gone away") and 2013 ("lost connection during query"), which
is the signature of the client's socket being severed rather than the server going anywhere.

That is the trigger. **The defect is what happened next.** The per-document `except` exists so
that one document's failure is logged and skipped without aborting the run — that promise is
in the docstring. But its first act was `frappe.db.rollback()`, which issues SQL: on a dead
connection it raised the identical error straight back out of the handler that was supposed to
contain it. So a failure scoped to one customer killed the entire run, every hour, and every
document after the failing one went unsynced. Worse, `frappe.log_error` needs the connection
too, so it raised as well — which is why the Error Log contains **zero** rows for this, and
the only trace anywhere on the box was an RQ stack in `worker.error.log`.

The handler now recovers before it logs. A lost connection is identified by driver error code
(2006/2013/2055 — stable across the two wordings) and answered with an explicit reconnect;
anything else rolls back as before, with a reconnect as fallback if the rollback itself fails.
Only then is the error logged, on a connection that works. If the reconnect cannot be made the
run stops cleanly rather than raising, since there is nothing left to write a log with.

The explicit reconnect is necessary because Frappe's own auto-reconnect does not exist:
`frappe/database/mariadb/database.py` sets `conn.auto_reconnect = True`, but mysqlclient
removed that feature, so the line is an inert attribute assignment on a Python object and a
dead connection stays dead for the remainder of the job.

Guarded by `tests/test_drive_sync_recovery.py` (bench-free, own CI step — it installs its own
frappe stub). The suite asserts the contract rather than the mechanism: whatever one document
does, the run reaches the last document and every failure is recorded. Reverting the handler
makes it fail exactly as production did — the run stops at the failing document.
## [1.227.0] - 2026-08-03

### Added

The two gaps that made a training video impossible to add from the builder and impossible to
debug once added.

- **"Add from Drive…" in the video block.** The only control was a Link to an *already
  registered* Training Video Asset, so there was no way to register one from the builder at
  all — `upload_video` even told the author to go and register one, with no door to walk
  through. The Desk form was the wrong door anyway: it lets a duration be typed while
  `duration_source` still reads `Probed`, which is the one combination that makes
  `evaluate_gates` score the coverage gate against a number nobody checked.

  The dialog reports `duration_probed` honestly. A `false` is not a detail — it means the
  service account could not read the file, the length is a placeholder, and the coverage gate
  will be waived rather than enforced. It says so.

- **A pasted Drive link is accepted, and the two wrong ones are refused up front.**
  `drive_file_id_from` takes a bare id, a `/file/d/…` link or an `?id=` link, and rejects a
  folder link and a shared-drive id by name. All three mistakes otherwise surface an hour
  later as `404 File not found`, which points nowhere near the cause — Drive answers 404
  rather than 403 for anything the service account cannot reach.

- **A failed copy is now visible, explained and retryable.** The asset carried
  `status = Error` and a full traceback, and the builder said only "This video asset is
  Error" — so a broken video and one that had simply not finished copying looked identical,
  and neither said what to do. The block now shows which of the three states it is in, and
  translates the commonest failure: a Drive 404 almost always means the file is not shared
  with the service account rather than missing. **Retry the copy** runs it inline and
  reports what happened, so fixing sharing no longer requires republishing the whole version.

- The **unverified-duration** case is called out where the author is looking. A `Manual`
  duration means `evaluate_gates` waives the coverage gate entirely; an author who believes
  they set an 80% gate should not have to read the source to discover it is not applied.

### Notes

- Mutation-tested: 17 mutations, all caught, but **six of them were missed on the first pass
  and every one exposed a weak assertion rather than a weak fix.** Three checked a function's
  whole body where a field still named in the SQL `fields=[...]` satisfied a check for
  something dropped from the returned dict — the returned literal is the contract, the column
  list is not, and the test now parses it. Two more were the "complete and unreachable" shape
  that has now appeared four times in this module: `register_drive_video` still defined with
  its button unwired, and the 404 explanation still in the file behind a condition replaced
  by `false`.

## [1.226.0] - 2026-08-03

### Fixed

**Submitting a Training Course Version from the Desk form published a hollow version.**

Publishing and submitting are different acts, and almost everything publishing means happens
outside the DocType controller. `publish_version` writes each lesson's answer-free payload and
its separate answer key, the table of contents, the totals and the content hash — and only then
calls `submit()`. The controller's `on_submit` merely stamps the timestamps and makes the
version live.

So pressing **Submit** froze a version that looked published and was empty: no table of
contents (the course outline rendered nothing at all), lessons whose published payload was zero
bytes, `content_hash` null — and, worst, **no answer key**, so the quiz could not be graded at
all, because `grading.answer_key()` throws when the key is missing. It also replaced a
perfectly good live version.

`before_submit` now refuses it and points at the Publish action. The check is against the
materialised fields rather than a flag, so the invariant holds however the submit is triggered —
a script or a bulk action included. `_require_content` only ever checked that lessons *existed*,
which a hollow version passes.

**Nothing ever called `gcs_media.copy_from_drive`.** Written in Phase 2b, complete, and invoked
from nowhere — so `gcs_object` stayed empty on every asset ever registered and `get_media_url`
answered `not_available` for every video block. The player rendered an empty frame and the
coverage gate had nothing to measure. The entire video pipeline was one call short of working,
and the only symptom was a video that did not appear.

Publishing now queues the copy for every video the version uses. Backgrounded (a 300 MB copy
inside the publish request would time out the author's browser), idempotent (a republish does
not re-upload gigabytes), and non-fatal (a publish that succeeded except for the video must not
roll back text lessons that are live and correct).

### Notes

- Both were found by taking the first real course rather than by any test. That is now seven of
  the eight defects in this module found that way.

- Mutation-tested. One mutation is worth naming: moving `toc_json`/`content_hash` to *after*
  `doc.submit()` in `publish_version` is caught, because it would lock the new guard against the
  only correct way to publish.

## [1.225.0] - 2026-08-03

### Fixed

**The lesson outline never showed progress.** `outlineRow` read `row.status`, `row.locked`
and `row.lock_reason`; a toc row carries `{lesson_key, chapter_key, title, minutes, has_quiz,
blocks}` and nothing else. All three were permanently `undefined`, so every lesson drew the
"not started" circle and offered **Open** however much of it the learner had finished — from
the course page there was no way to see where you were.

Status is now derived from the attempt's own per-lesson progress map, and the button reads
Open / Resume / Review accordingly.

- **The attempt's progress never reached the course view.** `state.progress` was only ever
  populated by `get_lesson`, and the outline lives on the course page, where no lesson has
  been loaded. `adoptAttempt` now takes the `lessons` map off the attempt, which
  `get_course` and `start_attempt` have both returned all along.

- **Removed the lock UI.** `row.locked` being undefined is why nothing was ever locked, and
  that is correct: the server recommends an order through `next_lesson_key` and deliberately
  does not enforce one — the outline is meant to let a learner open any lesson. The branch
  was UI for a feature that does not exist, and `.tr-outline-reason` went with it.

### Added

- The wire suite now checks **toc row** fields too, against the `toc.append({...})` in
  `api/training_author.py` — the publisher, which is the only place that shape is written
  down. `_public_toc` merely reloads `toc_json` and strips the internal docname, so comparing
  the player against the runtime would have been comparing it to a `json.loads`.

### Notes

- Nothing was wrong with the quiz: it sits on the last lesson of *Using the Training Module*,
  and the outline gave no sign of how far through the course you were. Two reports, one cause.

- Mutation-tested; one assertion again passed its mutation by matching a mention rather than
  the assignment — `if (attempt.lessons)` satisfied a check for `attempt.lessons` while the
  body assigned `{}`. Tightened to the assignment.

## [1.224.0] - 2026-08-03

### Fixed

Opening a course 500'd: `get_lesson() missing 1 required positional argument: 'attempt'`.

**`player.js` was written against an API that was never built.** Its own header comment
documented the contract it assumed — `getLesson({course, lesson_key})`,
`startQuiz({lesson_key})`, `completeLesson({lesson_key})` — a design where the server tracks
the learner's current attempt in session. The real runtime keeps the attempt explicit and
requires it on every call. Every one of those was wrong the same way.

- **The course flow now matches the API that exists.** `get_course` describes the course and
  returns the learner's open attempt if they have one; `start_attempt` mints one if not, and
  only when they actually enter a lesson — opening a course to read its outline must not mark
  the assignment In Progress for somebody who only glanced at it. Every later call carries the
  attempt.

- **Nothing ever called `finish_attempt`.** It was mapped in the transport on day one and
  called from nowhere, so completing the last lesson simply returned the learner to the course
  page. **No Training Completion, no certificate, and the assignment left open** — after doing
  all of the work. Wired to the end of the last lesson, with the sign-off view when a course
  demands hands-on verification.

- `open_checkpoint` was called without its required `at`, so **every in-video checkpoint
  raised a TypeError** before reaching the endpoint. It is the playhead position.

- `get_media_url` returns `{url, embed_url, reason, poster, duration_seconds, …}`; `blocks.js`
  expects a URL **string**. The adapter now unwraps it — previously `img.src` would have been
  set to `[object Object]` — and a `reason` from the server reaches the console instead of
  vanishing into a bare `.catch` that returned `""`.

- Four dead reads removed rather than tolerated: `course.name`, `course.course_title`,
  `course.estimated_minutes`, and `lesson_key`/`block_key` passed to endpoints that declare
  neither. All were unreachable second terms or dropped kwargs, and every one made the source
  claim the server might send something it never sends.

### Added

- The wire suite now checks **transport arguments**, not just names: every call site is parsed
  and compared against the endpoint's real signature, in both directions. It also asserts every
  mapped transport method is called from somewhere — which is what surfaced `finishAttempt`.

### Notes

- **This is the fifth wire mismatch in this module**, and the fourth found by a person using
  the product rather than by a test. The Python has been correct at every one of them.

- Mutation-tested throughout, and the mutations earned their keep three times over. They caught
  a **stray control character in the test's own regex** that had silently blinded it to
  `video.js` entirely — the "does the scan work" guard passed anyway, because it only asked for
  "more than five call sites". They caught an assertion that verified `finishCourse` existed
  while a mutation had unhooked it from `finishLesson`, leaving it complete and unreachable.
  And replacing a naive `index("function ", …)` slice with real brace matching immediately
  exposed a dead field read the old slice had been hiding.

## [1.223.0] - 2026-08-03

### Fixed

**`/training` told every learner "Nothing is assigned to you right now" — always, however
much was assigned to them.** Not an error and not a blank page: a confident, reassuring,
wrong sentence.

- `get_learner_bootstrap` returns `assigned` and `library`. The player read
  `b.courses || (b.catalog && b.catalog.courses) || []`. **Neither key has ever existed.**
  A missing key in JavaScript is `undefined`, `undefined || []` is an empty list, and an
  empty list renders as a perfectly good empty state — so nothing at runtime could notice.

- **The optional library was never rendered at all.** The server has separated `assigned`
  from `library` since Phase 2; the player knew about neither, so self-enrolment courses
  were invisible. They are now shown under their own heading, still separate: a due course
  displayed among things nobody has to do is close to not showing it.

- Two more of the same in the course card. It read `progress_percent` and `status` where
  the server sends `percent_complete` and `assignment_status`, so **the progress bar never
  drew and the action always said "Start"**, even half way through a course. Both failed in
  the direction that looks like a design choice rather than a bug.

- A third, harmless but misleading: `course.estimated_minutes || course.minutes`. The first
  term is the DocType's field name, not the card's; the fallback made it work while reading
  as though the server might send either. Removed.

### Added

- `tests/test_training_boot_wire.py`, in CI. It parses the dict literals
  `get_learner_bootstrap` and `_course_card` actually return and checks every key the player
  reads against them — in both directions, so a rename on the *server* side fails too.
  It also cross-checks the template's transport map against the `@frappe.whitelist()`
  definitions.

### Notes

- This is the **fourth** wire mismatch in this module: the heartbeat (`ranges` vs
  `intervals`), the builder's `change_type` (a truncated Select value), the stylesheet (an
  entirely different class vocabulary), and now the bootstrap. They share one shape — two
  halves written against different assumptions, each perfectly correct read on its own, with
  no runtime signal when they disagree.

  Every one was found by a person looking at the product, never by a test, and every one is
  cheap to catch statically once you know to look. The four contract suites now in CI
  (`heartbeat_wire`, `builder_entry`, `player_css_contract`, `boot_wire`) exist to make that
  the last time each is found the hard way.

- Seven mutations tested, all caught — including two applied to `api/training.py` rather than
  the client, to confirm the check fails from either side of the seam.

## [1.222.0] - 2026-08-02

### Added

- **Chapters can be created, renamed, reordered and deleted from the builder** (⋯ → Chapters).
  Closes the gap recorded in 1.219.0.

  Every other half of this was already built and correct: `save_draft_version` accepts a
  `chapters` array, `_apply_chapters` replaces the table in order and refuses to orphan a
  lesson, `TrainingCourseVersion._assign_chapter_keys` mints the keys, and the outline already
  groups lessons by chapter. The client simply never assigned `this.dirty.chapters` — it was
  read in two places and written in none. On a new course, whose first draft clones nothing,
  the chapter list was permanently empty and every lesson sat under "Unfiled" with no way out.

  Three decisions worth recording:

  - **Existing `chapter_key`s are carried through untouched, and a new chapter sends none.**
    The key is what every lesson points at; regenerating one silently unfiles every lesson in
    that chapter. New keys are minted server-side and adopted from the save response.
  - **Reordering is buttons, not drag.** A drag handle cannot be operated from a keyboard, and
    reordering is the main reason the dialog exists — the same reasoning as the lesson rail.
  - **Deleting a chapter that still holds lessons is refused in the dialog**, not left to the
    server. The server does refuse it, but on the *next autosave* — seconds later, with the
    dialog closed, against a save the author did not knowingly trigger.

### Notes

- Mutation-tested; eight of eleven were caught first time. The two that were not were both
  weaknesses in the *test*: one mutation was partial (a selector appears twice and only one was
  changed) and one assertion was line-anchored, so a stray field appended to an existing line
  slipped past. Both assertions were tightened.

## [1.221.0] - 2026-08-02

### Fixed

**"Your training" was a black heading on a black background.** The layout fixes in 1.220.0
landed correctly — one column, cards, sane spacing — but the page was still barely readable,
because the palette was never reaching the elements that needed it.

Frappe's website stylesheet, which this page renders inside, declares explicit colours on the
tags we use most:

```
h1,h2,h3,h4,h5,h6 { color: #171717 }      a      { color: #171717 }
body              { color: #525252 }      strong { color: #383838 }
label             { color: #777 }
```

`.tr-shell` sets `color: var(--tr-text)`, and **an inherited value loses to any explicit
declaration**, however specific the inheriting selector is. So every one of those tags kept a
near-black light-theme colour on our dark surface. The heading was invisible until you selected
the text; the paragraph beside it was legible only because `.tr-empty` happens to declare a
colour of its own.

Worth being precise about the diagnosis, because the obvious one is wrong: this was **not** a
theme-detection problem. The dark palette was matching and applying exactly as intended. The
cascade simply never carried it to the tags the host had already coloured.

Added a scoped reset that states the colour on those tags rather than trusting inheritance —
headings, paragraphs, list items, `strong`, `label`, `figcaption`, `small`, links (accent, not
the host's `#06c`, which is close to unreadable on the dark surface) and buttons. Scoped under
`.tr-shell` so it cannot repaint the surrounding site chrome.

### Notes

- Four assertions guard the reset, because it looks like redundant boilerplate and the obvious
  future "cleanup" is to delete it.

- Mutation-tested, and the first version of those assertions **survived three of its four
  mutations** — it substring-searched the stylesheet, so `.tr-shell a-GONE` still satisfied a
  check for `.tr-shell a`. They now parse the rules and compare selectors as tokens. That is the
  fourth release running where an assertion passed its mutation and had to be rewritten; every
  one was a string search standing in for a structural check.

## [1.220.0] - 2026-08-02

### Fixed

**The learner page at `/training` was essentially unstyled, and had been since Phase 2.**
Opening it showed a near-invisible heading and two narrow columns of one-word-per-line text.

- **The stylesheet and the player scripts were written against different class vocabularies.**
  Of the 138 `tr-*` classes the four scripts emit, **130 had no rule anywhere in
  `player.css`**; of the 43 the stylesheet defined, 35 were emitted by nothing. `player.css`
  described the Phase-2a design (`.tr-header`, `.tr-main`, `.tr-bar`, `.tr-coverage`,
  `.tr-checkpoint`, `.tr-block--video`) while the scripts had moved on through video,
  checkpoints and quiz (`.tr-subhead`, `.tr-view`, `.tr-bottom`, `.tr-video-meter`,
  `.tr-video-takeover`, `.tr-block-video` — one dash, not two). Two thirds of the file's rule
  blocks styled elements that no longer existed.

- **`.tr-shell` was on two nested elements.** The Jinja template puts it on the mount point
  and `player.js` built another one inside it, so at >= 900px the grid applied twice —
  the inner grid laid out inside a single 260px column of the outer one. That is what turned
  every sentence into a column of single words. `player.js` no longer adds a wrapper.

  The page still *looked* styled, which is why this survived: the eight classes that did
  overlap included `:root`, so the palette applied and the background was correctly dark.
  Partial application was worse than none.

- **The >= 900px layout declared a left rail that cannot exist.** It placed
  `.tr-outline` as a grid sibling of the main column, but `player.js` renders the outline as
  an `<ol>` *inside* the view. The column now simply widens at each breakpoint, matching the
  DOM the scripts actually build.

- Written fresh: the page chrome, catalog cards, lesson outline, content blocks, image
  lightbox, PDF acknowledgement row, video meter and checkpoint overlay, and the whole quiz
  and review screen — against the real DOM, mobile-first, every target >= 44px, and with the
  checkpoint overlay positioned against the video *wrapper* so it survives fullscreen.

### Added

- `tests/test_training_player_css_contract.py`, wired into CI. It asserts that every class
  the scripts render has a rule, and that no rule styles an element nothing renders. Both
  directions matter: the dead half is the tell that the two have drifted.

### Notes

- Mutation-tested. One assertion initially **missed** its mutation — a new `chip()` tone
  added in JS with no rule in CSS passed cleanly, because the tone list was hardcoded in the
  test. Tones are now read out of the call sites, so the list cannot go stale. That is the
  third time in three releases a hardcoded or comment-polluted assertion has been caught only
  by deliberately breaking the code.

- Worth stating plainly: this was found by a person opening the page, not by any test, and it
  had been true since Phase 2. Everything verified on the learner side until now was
  **server-side** — the 17/17 smoke test drives the Python endpoints and never loads the page.
  The builder's "Preview as learner" would have shown it, and that never worked either (fixed
  in 1.219.0). Video playback, watch telemetry and HTTP-206 range serving remain unverified in
  a browser.

## [1.219.0] - 2026-08-02

### Fixed

The course builder was unusable, and an author hit both halves of it within a minute of
opening it. Auditing the rest of the authoring path turned up 20 confirmed defects; this
release fixes the ones between an author and a finished course.

- **The Course form's "Open Builder" button said the builder did not exist yet.** It was a
  Phase-2 placeholder that outlived the thing it stood in for by three releases — the builder
  shipped in Phase 3. Anyone who believed the button never found the builder at all. It now
  routes to `/app/training-builder` with the course.

- **"This version is published and frozen" was shown when no version was loaded at all.**
  `get_builder_bootstrap` returns the open draft and nothing else, so `version` is null
  whenever a course has no draft — which is where *every* course sits the moment it is
  published, because publishing consumes the draft. So the normal second edit of any course
  produced a message describing a state that did not exist, sending the author off to find a
  version switcher instead of telling them to raise a draft. Both branches now name the real
  state and offer **New Draft Version** as the primary action.

- **The builder could never publish.** `change_type` is a Select whose stored values carry a
  parenthetical (`Minor Edit (keep completions)`); the builder's two dialogs offered the bare
  `Minor Edit` / `Material Change`, which `publish_version` rejects outright. Creating a draft
  failed the same way the moment an author picked a change type rather than leaving it blank.
  The Course form had the full strings all along, which is exactly why this survived — the
  same flow worked from the other entry point.

- **Deleting the lesson you were looking at could poison the whole session.** The rail row
  greyed out but the canvas kept painting the lesson and the inspector kept it editable, so
  the obvious next action queued a patch against a name the autosave was about to delete. The
  server 404s and rolls back the *entire* payload, the client re-merges the doomed patch, and
  it retries every four seconds indefinitely — taking every other lesson's edits with it.
  Reloading was the only escape and it discarded everything since the last good save.
  `state.deleted` had been returned by the server from the first day and read by nobody.

- **`flush_save()` returned a resolved promise while a save was still in flight** —
  indistinguishable, to a caller, from "everything is stored". `publish()` chains off it, so
  publishing within a few seconds of typing froze a version missing the author's last words,
  and a published version cannot be edited. It now returns the in-flight chain, so the promise
  means what every caller already assumed.

- **A failed save silently abandoned whatever it was chained to.** Skipping the action is
  right — going ahead would discard the edits — but the dialog had already closed and the only
  thing on screen was an error about the *autosave*. Adding a lesson simply did nothing. The
  abandoned action is now named in its own message.

- **Preview failed 100% of the time.** `frappe.assets.extn()` works out an asset's type by
  splitting the URL on `?` and taking the last segment, so `player.js?v=1.219.0` reports its
  extension as `0` and matches neither the js nor the css branch — the file was silently never
  loaded. Dropping the token is not the fix (raw `/assets` are served immutable for a year and
  this repo has shipped that cache bug twice), so the player is loaded by hand, in order, with
  the token intact. A load failure now says so instead of leaving a blank pane.

- Smaller ones on the same path: **Publish** and **Submit for Review** did nothing at all from
  the menu when no draft was loaded — no alert, no message, which reads as a broken button;
  the **Preview** button was live with nothing to preview; the builder's own empty state told
  the author to press a button named "Build" that does not exist; **Reload** discarded unsaved
  edits without warning, one menu item below "Save now"; and a course opened by URL left its
  name in `frappe.route_options`, which Frappe then applies as a filter to the next list view.

### Changed

- `training/README.md` documents the draft/publish cycle, which is the part that surprises
  people: publishing consumes the draft, so the next edit starts a new one.

### Notes

- 17 mutations tested, all caught. Three assertions initially **passed** their mutation for the
  same reason: the comment explaining a bug quotes the bug, so a source-text search cannot tell
  prose from code. Comment-stripping is now the default for these checks — worth knowing, since
  the same trap cost an assertion in 1.218.0.

- Still open, and deliberately not in this release: the preview shims `TR.Video.mount` into
  existence and re-shapes `TR.Quiz.mount`, which means the builder preview may work where the
  real `/training` player does not. That is the learner runtime rather than the builder, it is
  the same class as the heartbeat wire mismatch fixed in Phase 3, and it needs a browser to
  confirm. Also open: the builder cannot create, rename or delete a **chapter** — the client
  never assigns `dirty.chapters`, though the server accepts them. Lessons fall back to
  "Unfiled" and work, so this is an absent feature rather than a broken one.

## [1.218.0] - 2026-08-02

### Fixed

Follow-on from the first end-to-end run (see 1.217.0). Two more of the same family: code
that reported a confident, plausible, wrong number rather than failing.

- **Nothing ever wrote `Training Attempt Question`.** The doctype, its controller, its
  permission hooks and the `training_question_analytics` Script Report that groups over it were
  all built and correct. No row was ever inserted. The report did not error — it returned an
  empty set, which on screen is indistinguishable from *"no problems found"*. That report exists
  because a question everybody misses usually means the content is unclear rather than the
  learners, and it could not have told anybody that.

  Quiz and checkpoint answers are now filed from `training/grading.py`, the one place holding
  both the drawn options and the verdict. Every checkpoint *attempt* is recorded, not just the
  last one — a checkpoint failed twice and passed on the third go is precisely the signal the
  report is for, and keeping only the final answer would render it a clean pass.

  `given_answer` stores the option **text**, not the option key. Keys are stable across learners
  so either would group correctly, but the report's most useful column names the dominant
  distractor — *"half the misses picked B"* — and `opt_3` is not a sentence an author can act on.

- **Every Training Completion recorded 0% video coverage.** `video_coverage_percent` was read
  off the attempt and written onto the permanent record, and nothing populated it — on the
  attempt or anywhere else. The **gate was never affected**: it computes coverage live from the
  stored watch intervals, so nobody passed a course they should not have. But the completion is
  the artefact you would produce in a dispute, and it understated everyone who took a course
  with a video in it.

  Coverage is now taken from the gate's own verdict rather than recomputed, so the number filed
  on the permanent record is the number the learner was shown and graded on. `evaluate_gates`
  gained a `gated` count so a course with **no** video writes nothing rather than a fabricated
  `0` — otherwise a text-only course would read as though the learner sat through nothing, which
  is the same bug wearing a different hat.

- `quiz_runs`, `checkpoints_answered` and `checkpoints_correct` on Training Attempt were declared
  and never written either; all three read a confident zero. Now derived from the progress blob
  at completion, rather than incremented as things happen — a counter incremented on every
  heartbeat drifts the first time a write is lost.

- `verify_certificate` returns ISO date strings instead of `date` objects. Frappe's encoder
  always handled these, so this was never broken over HTTP; but it is a public endpoint that
  third parties and our own scripts consume, and one that survives a plain `json.dumps` is a
  better contract.

- Two bugs in the smoke harness itself: it asked for the Employee record of the *session* user
  when naming a supervisor (Administrator has none), and it reported the completion step as a
  **pass while printing `score 0.0`** for a learner who had just scored 100. The score is now
  asserted, not merely printed. A smoke test that displays a wrong value without failing is
  worth very little.

### Added

- Smoke-test steps covering the analytics rows and the attempt's summary counters, including a
  check that `given_answer` holds readable text rather than an opaque option key.

### Notes

- Every assertion added here was mutation-tested — eleven deliberate reintroductions of these
  bugs, each confirmed to fail the suite. One assertion **passed** a mutation and had to be
  rewritten: it grepped the whole function for `update_modified=False`, and the explanatory
  comment directly above the call contains that string, so deleting it from the code left the
  test green. Reading the test would not have caught that.

## [1.217.0] - 2026-08-02

### Fixed

The Training module was executed end to end for the first time. Four phases of review had found
a lot; running it once found four more, and three of them were the kind that report a real,
plausible, wrong answer rather than failing.

- **The supervisor sign-off gate did not gate.** `finish_attempt` enforced every lesson gate and
  never looked at `require_supervisor_signoff`, so a technician could be certified competent to
  drain a basin having only answered questions about it. The Phase-4 contract was written
  precisely to protect this class of safeguard and did not catch it, because those assertions
  checked *source presence* rather than behaviour.

  Now reported as an outstanding item like any other unmet gate, so the player tells the learner
  what is left rather than erroring. Only a **submitted** sign-off recording *Competent* counts: a
  draft is a request, and "Needs More Practice" is the supervisor explicitly declining. Matched on
  the course rather than the version — somebody watched draining a basin last month has been
  watched draining a basin, and a rewritten paragraph should send them back through the material,
  not back in front of a supervisor.

- **A learner who scored 100 got a certificate reading 0.** `_issue_completion` wrote `"score"`
  where the field is `score_percent`, and `_doc_payload` discarded the unknown key **silently**.
  Two more of the same: `"course_title"` where the field is `course_title_snapshot`, and a
  `"customer"` on Training Attempt, which declares no such field.

  `_doc_payload` now **logs** what it drops. The filter stays — an insert that dies in front of a
  learner is worse than a missing value — but its docstring claimed the gap would "show up in the
  record, where somebody will notice it", and for four phases nobody did. Defensive must not mean
  silent, or the defence becomes the bug.

- The completion now records **which** sign-off unlocked it, so it carries its own evidence.

### Added

- `tests/test_training_runtime_regressions.py`. The load-bearing test is static: it parses every
  `_doc_payload(...)` call and cross-checks each field name against the DocType JSON. That would
  have caught the score bug before it ever ran, without a bench, and it generalises to any field
  added later. It immediately found two drops beyond the one that prompted it.

- `training/smoke_test.py` — the end-to-end harness itself:
  `bench --site <site> execute erpnext_enhancements.training.smoke_test.run`. Twelve steps through
  the real code paths. It asserts gates by *trying to pass them and requiring a refusal*, walks the
  live published payload for answer markers, and checks the public certificate verification leaks
  no PII. Safe to re-run.

### Notes

- Every assertion added here was mutation-tested — the code deliberately broken to confirm the test
  fails. Both the score typo and the removed sign-off gate were verified to fail before the fixes
  were kept.

- Worth recording plainly: reading found the ordering bugs, the answer-key leak and the wire
  mismatches. It did not find any of these four. A gate that is simply absent, and a field name
  that is quietly wrong, both look completely fine on the page.

## [1.216.0] - 2026-08-02

### Added

- **Two read-only MCP tools for Training**, so the module is reachable from the assistant rather
  than only from the desk.

  `training_compliance_status` answers *"is my team current?"* and leads with the **exceptions** —
  overdue first, then due-soon, then a per-course summary. A tool that answers "everybody is fine"
  with two hundred rows gets ignored, so the full roster is the last thing it returns, not the first.

  `training_learner_record` answers *"can I send this person to that job?"*. It takes an employee
  docname, a user email or just a name, and separates **current** certifications from ones that
  have lapsed *or been superseded* — a completion is pinned to the course version that was passed,
  so a course that has materially changed since shows as superseded rather than quietly current.
  An ambiguous name is refused rather than guessed: answering about the wrong technician is worse
  than answering nothing.

  **Neither tool reports watch coverage on its own.** Coverage measures time elapsed with a video
  playing and visible, not attention. Beside a score it is context; alone it reads as proof of
  engagement it cannot support, and an assistant is exactly the surface where that misreading would
  spread. Both say so in their description and in the payload.

### Notes

- `frappe.utils` is stubbed narrowly by the assistant-tool contract test, so these import only
  `date_diff` / `nowdate`. Importing anything more exotic fails at import time and takes the whole
  tool registry down with it — a wider blast radius than the tool itself.

## [1.215.0] - 2026-08-02

### Added

- **Training Phase 4 — certificates, recertification, portal access, sign-off, Q&A and
  gamification.** The last of the four phases; the module is now feature-complete.

  Six DocTypes: **Training Certificate** (submittable, stores its *rendered* HTML so editing a
  print format next year never rewrites a document somebody already holds — the same reasoning
  as the e-sign snapshot), **Training Signoff**, **Training Question Thread**, and the
  gamification trio **Badge / Badge Award / Learner Stat**.

  **Certificates** are issued on `Training Completion.on_submit` rather than from the endpoint,
  so a completion recorded by a manager by hand gets identical treatment to one a learner earned.
  `on_cancel` expires the certificate *and* re-opens the assignment — a revoked pass that still
  reads as compliant would defeat the point of revoking. Every step is independently wrapped: a
  failed badge award must not roll back a completion somebody earned.

  **The public verify route** (`/training_certificate?code=…`) returns only
  `{valid, course_title, holder_initials, issued_on, expires_on, status}`. That link gets shared
  with third parties, so a full name, an email or an employee id would turn proof-of-training into
  disclosure. Controller filename is underscored, or Frappe would never import it.

  **Recertification** expires completions past `expires_on` and raises the next assignment, so
  "does this technician hold a current certification" stays one indexed query rather than a date
  calculation at read time. It re-dates an existing open assignment rather than inserting a
  second, so it cannot collide with a material-change retake.

  **Supervisor sign-off** for the things a quiz cannot prove, **ask-the-author Q&A** whose answers
  the author can publish to future learners, **gamification** with staff and customer leaderboards
  kept strictly apart, and two **Script Reports** — completion matrix and per-question analytics.
  The second is the quiet win: a question everybody misses usually means the *content* is unclear,
  not the learners.

- **The compliance check warns and never blocks.** `Sapphire Maintenance Record` and `Task`
  validate now flag a technician without a current certification — as an orange message, a
  timeline comment and a supervisor notification, never a refusal. By the time it fires a truck is
  usually already at a site, and blocking the form would mean the visit happens with **no record
  at all**, which is worse than the uncertified assignment it was flagging. Gated by
  `warn_on_uncertified_dispatch`, and wrapped so a failure in the advisory can never break a save.
  `tests/test_training_compliance.py` asserts `frappe.throw` appears nowhere in that module.

### Fixed

- **The Phase-4 doctypes named the learner column three different ways, and none of them was the
  one that works.** They shipped `holder_user`, `learner_user` and `asked_by`; every consumer
  module used `user`, and so does `Training Assignment` / `Attempt` / `Completion` — and
  `training/permissions.py` scopes every learner-owned row on a column of exactly that name.
  Renamed to `user` throughout. Left as built, the portal certificate page would have raised
  `Unknown column` and row-level scoping would have silently applied to nothing.

  This one was **my specification error, not the agents'** — the contract fixed the behavioural
  seams but let the shared *data shape* be inferred, so four agents inferred four different things.

- Restored `Training Assignment.completion` and added `Training Completion.signoff` /
  `.certificate`. Phase 1 deferred these because a Link naming a non-existent DocType fails
  `bench migrate`; those doctypes now exist. `test_training_phase4_contracts` generalises that
  lesson into an assertion that **every** Link in every Training doctype names something this app
  actually ships.

### Notes

- Registration order matters and is asserted:
  `training.setup_print_formats.ensure_training_print_formats` sits **above**
  `ensure_chrome_pdf_generator` in `after_migrate`, which is last on purpose and has to see the
  certificate format to point it at the right PDF backend.

- Contracts-first held up again. Phase 2 (no contracts) shipped four integration breaks to main;
  Phase 3 shipped none; Phase 4 surfaced its mismatches *before* merge, and one agent found them
  by reading its siblings rather than me finding them afterwards. Several of its reported
  mismatches were stale — it had read the sibling files mid-flight — so each was re-verified
  empirically rather than taken at face value.

- **Nothing in Phases 2–4 has ever executed.** No course, no video asset, and the byte-range check
  is written but unrun. Every defect across four phases was found by reading, which has worked
  better than it should have. One real course would exercise more than every static check to date.

## [1.214.0] - 2026-08-02

### Added

- **Training Phase 3 — the course builder and AI drafting.** This is the phase that delivers the
  original brief: anyone with something to teach can build a course through the UI, without a
  deploy and without going through one person. Everything before it was machinery underneath.

  **The builder** (`/app/training-builder`, Training Author and above) is a three-pane desk page:
  lesson outline, sortable block canvas, inspector. Autosave copies the visit wizard's
  dirty-accumulator with its re-merge-on-failure path, so a save that fails does not discard what
  the author typed while it was in flight. Reordering is drag **and keyboard** — drag-only is an
  accessibility failure and this repo has been bitten by touch-drag before. Deleting a block is
  soft for the session; a block with learner progress in a published version cannot be
  hard-deleted at all, only retired.

  **Checkpoint authoring** is a timeline strip with draggable pins: drop one at the current frame,
  live-seek while dragging so you see where you land, and *Test this checkpoint* runs the real
  learner overlay through `TR.Video` rather than a mock of it.

  **Preview as learner** constructs the actual `TR.Player` with an injected preview transport.
  That seam was the stated Phase-2 exit criterion and this is what it was for — the builder drives
  the same player a learner gets, so a fix never has to be made twice.

- **`api/training_ai.py`** — quiz-question drafting and checkpoint-timestamp suggestions through
  the existing Vertex AI client, so token accounting stays uniform with the rest of the app.

  The load-bearing behaviour is what it refuses to do. Drafting **never** stamps `ai_reviewed_by`:
  a draft that arrives pre-reviewed walks straight past the `submit_for_review` gate, so only an
  author accepting one may mark it reviewed. `suggest_checkpoints` **refuses** without a timed
  transcript rather than letting a model invent timestamps — without timings it will invent them
  confidently, and that single constraint is the whole integrity story for the feature. Model
  output is parsed strictly: prose, markdown fences or a partial object yield zero suggestions and
  a message, never a half-built question.

- **`save_draft_version`** applies an explicit field allowlist and a **mandatory** optimistic lock.
  Anything not allowlisted is reported back in `rejected` rather than dropped — a silent drop would
  have autosave answer "saved" while the author's paragraph went nowhere, which is strictly worse
  than refusing. The lock is mandatory unlike `maintenance_visit.save_visit`, because a caller with
  no token never loaded the draft, and honouring that write is exactly the clobber it exists to stop.

### Fixed

- **A module-scope import in `api/training_author.py` broke a previously-passing suite.** Importing
  the Training Settings controller at import time made the whole file unloadable to any bench-free
  test that stubs `frappe` without also stubbing that module, and `test_training_grading` went from
  green to an ImportError. Now imported lazily inside the one function that needs it, and swallowed
  — a settings read is not worth denying an author their builder over; the AI panel just stays hidden.

### Notes

- Built by four parallel agents against `tests/test_training_phase3_contracts.py`, which was written
  **before** the code and whose every assertion had been mutation-tested. All 20 contract assertions
  now pass with **zero skips**. That is a real improvement on Phase 2, where four integration breaks
  reached main; the one regression this time was caught by the existing suite rather than by reading.

- Caveat worth stating plainly: the seam checks are presence and shape assertions, not proof of
  behaviour. Nothing in Phases 2 or 3 has ever executed. One real course with one real video would
  exercise more than every static check to date.

## [1.213.0] - 2026-08-02

### Added

- **The byte-range check, in Test GCS Connection.** Seeking in a `<video>` is an HTTP Range
  request, and the entire watch-coverage model assumes the server honours it. If a seek returns
  `200` with the whole file instead of `206` with a slice, the player still *works* — it just
  reports numbers that are wrong rather than absent, which is the worst failure mode this feature
  has. The probe now fetches `bytes=10-19` from its own signed URL and checks for a 206 with the
  right slice, so the question is answered without anybody authoring a course first.

  A failed range check **does not fail the connection test**: everything except seeking still
  works, and reporting "connection failed" when the connection is fine sends an operator looking
  in the wrong place. It is reported loudly on its own terms instead.

- **An executable contract for the Phase 3 seams, written before the code.**
  `tests/test_training_phase3_contracts.py` pins the joins Phase 3 has to satisfy: the preview
  transport implementing every method the player calls, the autosave field allowlist and its
  optimistic lock, the AI drafting endpoint stamping the fields the review gate reads (and never
  marking its own output reviewed), the checkpoint-suggestion transcript requirement, and the
  builder page's role gate.

  Phase-3 assertions skip until those files exist — they are acceptance criteria, not decoration,
  and `test_phase3_is_actually_built` exists so a skipped suite is never mistaken for a passing
  one. The Phase 2 contracts they build on are enforced now so they cannot drift underneath the
  build.

  **Why this file exists:** four integration breaks got through the Phase 2 fan-out build, and
  every one was two correct halves with a wrong join — `ranges` vs `intervals`, an object vs an
  integer `seeks`, a nested vs top-level `hidden`, and three transport methods naming endpoints
  that do not exist. None was a hard bug; each was a name nobody owned. Every assertion here was
  mutation-tested — deliberately broken to confirm it fails — because a contract test that cannot
  fail is worse than none.

## [1.212.1] - 2026-08-02

### Fixed

- **Three of the player's transport methods pointed at endpoints that do not exist.** The player
  knows only the transport's *function names*; `www/training.html` maps those to whitelisted
  methods. `startQuiz` named `start_quiz` and `mediaUrl` named `media_url` — the endpoints are
  `get_quiz` and `get_media_url` — so the quiz would never load and no video URL would ever
  resolve. `heartbeatBeacon` was called by `video.js` but absent from the map entirely, so the
  `pagehide` flush would have thrown on `undefined` and silently lost the final beat of every
  session.

  Nothing caught this because both halves are individually valid: the endpoints exist, the player
  is correct, and only the map between them was wrong. Same class as the heartbeat wire mismatch
  in 1.212.0, and the same cause — parallel work with an unowned seam.

  `tests/test_training_heartbeat_wire.py` now cross-reads the map against
  `@frappe.whitelist()` definitions and against every `transport.*` call in the player, so a
  rename on either side fails there. Verified the new assertions do fail against the original
  typo rather than merely passing against the fix.

## [1.212.0] - 2026-08-02

### Added

- **Training Phase 2 — the learner player.** `/training` is a standalone mobile-first page (not a
  desk page, because customers and field crew on phones both have to reach it), with watch
  telemetry, in-video checkpoints and the quiz runtime. Ships behind the existing dormant
  switches: `training_enabled` plus `portal_enabled`.

  Three new DocTypes: **Training Attempt** (one row per learner per course-version run, with all
  progress in a single `progress_json` blob), **Training Attempt Question** (one row per graded
  response — top-level, not a child table, because it is written one row at a time and
  per-question analytics is a `GROUP BY question` across every attempt), and **Training
  Completion** (submittable; `cancel()` = revoked).

  `training/progress.py` holds the watched-seconds arithmetic. Intervals are merged, clipped and
  — past a cap — collapsed **smallest-gap-first**, because over-crediting a learner who genuinely
  watched is the kinder error and dropping an interval is not. Writes are cache-first and
  throttled: `frappe.db.set_value(..., update_modified=False)`, never `doc.save()`, since a full
  save per heartbeat would write a Version row containing two copies of the blob per learner per
  minute.

  `training/grading.py` is the only module permitted to read an answer key. Shuffling is
  deterministic from the attempt's seed, so re-opening the same run shows the same order — a
  reshuffle mid-attempt reads as the app having lost your answers.

### Fixed

- **The player and the progress store did not speak the same heartbeat format.** The player emits
  run-length-encoded `ranges` (compact for a phone that has been scrubbing), structured
  `seeks: {forward, backward}` and a nested `discount.hidden`; `progress` reads `intervals`, an
  integer `seeks` and a top-level `hidden`. Shipped as written, **coverage would have been zero
  forever** — no video lesson completable, and nothing raising anywhere.

  `api.training._normalise_beat` is now the single place that knows both shapes, and
  `tests/test_training_heartbeat_wire.py` pins the translation *and* re-reads both sides' source,
  so a rename on either side fails there rather than in production. Only forward seeks count
  toward integrity; rewatching is exactly the behaviour the feature wants to encourage.

### Notes

- The `TR.Player(rootEl, boot, transport)` seam is in place and was the stated Phase-2 exit
  criterion: the player never reads `window.TRAINING_BOOT` and never calls `fetch` itself, so
  Phase 3's builder can inject a preview transport instead of forking the player.

- Watch coverage still measures *time elapsed with the video playing and visible*, never
  attention. The compliance artefact remains coverage **plus** checkpoint accuracy **plus** quiz
  score.

- Built by eight parallel agents against a fixed contract, then integrated by hand. Every suite
  passed individually while the feature as a whole was broken — the wire mismatch above sat
  exactly on the seam none of them owned, which is the failure mode this kind of build has.

## [1.211.0] - 2026-08-02

### Security

- **The Google Drive service-account key was stored in cleartext; it is now encrypted.**
  `Project Folder Google Drive Settings.service_account_json` was a `Code` field, so a
  full-Drive-scope private key sat verbatim in `tabSingles` **and was rendered back onto the
  form**. Anyone who reached that page as Administrator could read it off the screen — no
  database access, no server script, no decryption. Every other secret on the site (Stripe,
  QuickBooks, MDM, Triton) is a `Password` field and shows asterisks in exactly that situation;
  this one did not. After the 2026-08-02 intrusion, in which someone logged in as Administrator
  ten times, that stopped being hypothetical.

  Changing the fieldtype alone would have been worse than doing nothing: `bench migrate` would
  leave the cleartext in `tabSingles` while the app started reading an asterisk placeholder — so
  Drive **and** Calendar would break and the secret would still be exposed. The move is done by
  `patches/encrypt_drive_service_account_json.py`, which relocates the value into `__Auth`,
  verifies the column no longer holds it, and overwrites it directly if the save did not.

  **Rotate the key regardless.** Encrypting it now does not un-expose what was readable before,
  and nothing can tell us who read it.

  All nine readers were audited. Four only test truthiness ("is Drive configured") and still work,
  because the placeholder is non-empty exactly when a key is stored. The three that actually parse
  the key — `drive_utils.get_drive_service`, `google_calendar.calendar_utils.get_calendar_service`
  and the Drive health check — now go through a single `get_service_account_info()` accessor.
  **Reading the field directly is now a bug that looks like working code:** it returns a truthy
  placeholder and fails later inside `json.loads`, so the accessor's docstring says so and the
  controller docstring repeats it.

  Entry is via a **Set Service Account Key** dialog, the same pattern used for the Training media
  key: a real multi-line box, with the paste validated (parses, is a service account, `private_key`
  retains its BEGIN marker, and the credential actually constructs) before anything is stored.

## [1.210.0] - 2026-08-03

### Added

- **Email alerts on every `Administrator` authentication, success or failure.** On 2026-08-02 an
  automated scanner logged into production as `Administrator` with a working password and planted
  a cryptominer dropper plus two safe_exec escape scripts. Nothing alerted. It surfaced two days
  later only because the failed payload was hooked to `User` → Before Save and was crashing every
  User save with `ImportError: __import__ not found` — the sandbox rejecting the dropper's first
  line. The payload never ran, but the detection gap is the part worth fixing.

  `Administrator` is the one account ERPNext's own 2FA can never cover:
  `frappe.twofactor.two_factor_is_enabled_for_` returns `False` for it unconditionally, before it
  looks at any role. Enabling 2FA site-wide does not touch it, so the account with the most power
  is the one with the least protection. Once it is reduced to break-glass use, every login is
  either a planned emergency or an intrusion, and both deserve an email.

  Hooked to `Activity Log` `after_insert` rather than `on_session_creation`, because Frappe writes
  an authentication row for **failed** attempts too and one code path then covers both. The
  failures are the early warning — the August break was preceded by roughly thirty failed probes,
  hours ahead of the payload. Failure emails are throttled to one per 15 minutes (that scanner
  sent ~30 in 24 seconds); successes are never throttled.

  Recipients come from `admin_alert_recipients` in **site_config.json**, deliberately not from a
  DocType: an attacker holding `Administrator` can disable a Notification record or a Server
  Script from the Desk in seconds but cannot edit site_config without shell access. Point it at an
  address hosted elsewhere, so an alert about this mail server does not have to travel through it.
  Falls back to enabled System Managers when unset, and logs when there is nobody to tell.

  Delivery is enqueued after commit and the whole hook is wrapped in try/except. This runs inside
  the login request: an exception here would turn monitoring into an outage, locking everyone out
  of the account used to fix outages.

  **Known gap:** API-key auth (`Authorization: token <key>:<secret>`) creates no Activity Log row
  and no session, so it cannot alert from here. Catching it needs a `before_request` check on the
  hot path — deliberately out of scope. Rotating or clearing `Administrator`'s API key closes that
  gap more cheaply than watching for it.

## [1.209.3] - 2026-08-02

### Fixed

Both found by running **Test GCS Connection** on the real setup, where it returned a dialog
reading `Upload failed:` with nothing after the colon.

- **A failure with no message is worse than no failure report at all.** The diagnostic used
  `str(exc)` alone, and several exceptions reachable here carry no message — a bare
  `TimeoutError()` is the obvious one — so the operator got a colon and empty space. `_describe()`
  now always reports the exception class, adds the message when there is one, and translates the
  HTTP status out of a googleapiclient error into the thing it actually implies: **404** no such
  bucket, **403** the service account lacks objectAdmin or the IAM binding has not propagated,
  **401** the stored key was rejected. The full traceback goes to the Error Log, which the old
  path never wrote to either — so nothing about the failure survived anywhere.

  `test_connection` also now checks the bucket resolves *before* attempting an upload, so a wrong
  name reports itself as a wrong name rather than as a generic write failure.

- **A service account pasted into the bucket field is now rejected on save.** The runbook prints
  `training_media_bucket` and `training_media_service_account` as adjacent Terraform outputs, and
  on the first real setup the service account went into **GCS Video Bucket**. Nothing validated
  it, so the only symptom was a 404 several steps later — which reads as "the integration is
  broken" rather than "one field is wrong". `Training Settings.validate` now refuses a value
  containing `@`, ending in `.iam.gserviceaccount.com`, or beginning `sa-`, and says which field
  the value belongs in. The same check runs in `test_connection` for anything already stored.

### Notes

- No schema change and nothing to migrate; a site with the wrong bucket name simply gets a clear
  error on the next save of Training Settings instead of a silent 404.

## [1.209.2] - 2026-08-02

### Fixed

Both of these were found by actually walking the GCS setup runbook rather than by reading it.
Neither is a code defect exactly — they are places where the procedure could not be followed as
written, which is the same thing from the operator's side.

- **The training media bucket is now switched on in `prod.tfvars`.** `infra/storage.tf` guards
  the bucket with `count = var.enable_training_media_bucket ? 1 : 0` and the variable defaults
  to false, so following the runbook produced a puzzling *"No changes. Your infrastructure
  matches the configuration"* — the resource did not exist to plan, and
  `-target google_storage_bucket.training_media` matched nothing either, because at count 0 the
  address is `...training_media[0]`. The runbook said "ships inert, behind
  `enable_training_media_bucket`" in prose and then gave commands that never set it.

  The flag is set in tfvars rather than passed as `-var` on the command line **deliberately**: an
  apply that does not carry the flag evaluates the count to 0 and plans to *destroy* the bucket.
  `force_destroy = false` saves it once objects exist, but an empty bucket would go without
  complaint. Keeping the flag in tfvars means every apply agrees with every other one.

- **The GCS signing key could not be pasted into its own field.** It was a `Password`, which
  Frappe renders as a **single-line masked input** — a control that cannot take a 2 KB
  multi-line service-account JSON by paste without mangling or truncating it, and which then
  hides the damage behind asterisks. Capacity was never the issue (`__Auth.password` is TEXT,
  64 KB); the control was.

  The field is now read-only and the key goes in through a **Set Service Account Key** dialog
  with a real multi-line box. The validation is the more useful half: it checks the JSON parses,
  that `type` is `service_account`, that `private_key` still carries its BEGIN/END markers
  (losing those is the classic single-line-paste casualty — it parses fine and fails only at
  signing time), and then actually **signs a probe string** to prove the key works rather than
  trusting that it looked right. It warns without blocking when the key belongs to an account
  other than `sa-training-media`, since pasting the wrong file out of a downloads folder is easy.
  Each of those turns an opaque 403 on a learner's first video into a sentence at the moment of
  paste.

  **Test GCS Connection** is now a button on the same form, so runbook step 5 no longer needs a
  bench console.

  Storage stays encrypted; only the entry path changed. Worth recording that the original choice
  diverged from the repo's own precedent without cause —
  `Project Folder Google Drive Settings.service_account_json`, the same kind of value, has always
  been a `Code` field. Training keeps `Password` because it is the narrower credential and
  encryption at rest is worth having, now that pasting actually works.

### Notes

- These two commits were pushed to the branch behind PR #680 after it had already been merged, so
  they were never part of that release. Hence a separate 1.209.2 rather than an amendment to
  1.209.1.

## [1.209.1] - 2026-08-01

### Fixed

An adversarial review of the Training module after it merged found four real defects, all of
which shipped in 1.207.0-1.209.0. Three are **ordering** bugs — work done in `validate()` that
Frappe needs *before* `validate()` runs — and the existing test suites could not have caught
any of them, because they stub `frappe` wholesale and so never exercise the framework's own
sequencing. The new `test_training_regressions.py` asserts that sequencing directly, against
the real Frappe source where a bench checkout is present.

- **No course could ever have a second version.** `Training Course Version` is named
  `format:{course}-V{version_number}`, but `version_number` was assigned in `validate()`.
  `Document.insert` calls `set_new_name()` at line 729 and only reaches `validate` at line 734,
  so the number did not exist yet: every version was named `<course>-V` with no number, and
  creating a second one died on a duplicate primary key. That is the entire premise of the
  doctype — "fix a typo without invalidating completions" was unreachable in practice. Moved to
  `before_naming()`, which `frappe.model.naming.set_new_name` invokes (naming.py line 156)
  before resolving the format string. The same trap is solved the same way on
  `kpi_dashboards/doctype/hr_stat_entry`.

- **No targeted assignment rule could be saved.** `applies_to_value` is a Dynamic Link resolved
  through `applies_to_doctype`, which was stamped in `Course.validate()`. But `_validate_links()`
  runs at `Document.insert` line **727** — before `before_insert`, before naming, before
  everything — so any rule other than "All Employees" was rejected with the unhelpful
  "Applies To DocType must be set first". There is no server hook early enough, so the field is
  now stamped client-side in `training_course.js` the moment an author picks a target (on both
  change *and* row-add), with a backfill for rows saved earlier. The server keeps the stamp as a
  backstop for later saves and now also verifies the target record actually exists.

- **Role Profile rules silently matched nobody.** The rule compared against the legacy scalar
  `User.role_profile_name`, but users here hold several profiles at once (Brian is Sales + Sales
  Team; Lian is Production Team + Technician) — and this module's own patch adds a
  "Training Learner" profile alongside everybody's real one, so secondary profiles are the norm
  on this site rather than the exception. A rule targeting a secondary profile assigned nobody
  and reported nothing. Now resolved against the `User Role Profile` child rows, with the scalar
  kept only as a fallback for a site that never used the table.

- **The True-False normaliser guessed answer keys.** It inferred intent by looking for an option
  literally spelled "false" and defaulted to *True is correct* when it could not find one — so
  Yes/No, T/F or Correct/Incorrect phrasing had its answer key silently rewritten, with the form
  simply coming back saying True was right. Guessing an answer key is the one thing this module
  must not do. An unrecognised pair is now an error the author has to resolve, and the options
  table is no longer rebuilt.

### Notes

- Nothing here needs a data patch. The naming bug prevented bad rows from being created rather
  than creating them, and the module shipped dormant, so on this site the blast radius was
  "authoring a second course version would have failed the first time it was tried".

- The review ran 21 agents across four independent lenses and raised 17 candidates; 9 were
  refuted on verification and 8 confirmed (4 unique, each found by two lenses independently).
  Worth repeating for any future module of this size — the value was concentrated entirely in
  the ordering findings, which no amount of stubbed unit testing would have surfaced.

## [1.209.0] - 2026-08-01

### Added

- **Training video delivery: a private GCS bucket and hand-rolled V4 signed URLs.** The
  Phase-2 prerequisite, landed ahead of the player so the spike is a verification exercise
  rather than a build. Ships **inert** — the Terraform is behind `enable_training_media_bucket`
  (default false) and the app treats an unconfigured bucket as "video delivery not available",
  degrading to text, image and PDF blocks plus quizzes rather than erroring.

  **Why the video is copied out of Drive at all.** A Drive preview frame is cross-origin: the
  player cannot read `currentTime`, cannot `pause()`, and cannot observe seeking. That kills
  in-video checkpoints *and* watch-coverage measurement — two of the three ways the module
  checks whether somebody engaged with a lesson. A signed URL on a real `<video>` element
  restores both, serves correct byte ranges so seeking works, costs no bench disk or
  bandwidth, and works for a customer who has no Google account.

  **Why the signing is hand-rolled.** `google-cloud-storage` is not a dependency and cannot be
  pip-installed on the host — the same constraint that made `stripe_payments` talk to Stripe
  over plain REST. But `google-auth` already is a dependency, and a service-account credential
  built from it exposes an RSA signer, which is the only primitive a V4 signature needs. So
  `training/gcs_media.py` assembles the canonical request by hand and signs it with a library
  we already have. **No new package.**

  `infra/storage.tf` creates one private bucket (uniform access, public-access prevention
  *enforced*, no lifecycle deletion, `force_destroy = false`) in `us-east4` — the region the
  production VM runs in, so playback egress to the bench is same-region and free — plus a
  narrow `sa-training-media` service account holding `objectAdmin` on that bucket and nothing
  else. The CORS origin is read from the live site (`https://erp.sapphirefountains.com`), not
  guessed; without it the browser blocks playback outright, because the player sets
  `crossorigin="anonymous"`.

  The service-account key is deliberately **not** a Terraform resource:
  `google_service_account_key` writes the private key in plaintext into the state file, and
  that state lives in a bucket several people can read. It is created by hand and pasted into
  Training Settings, which stores it in a Password field.

### Notes

- **Signing must use UTC, and this is easy to get wrong here.** The timestamp and the
  date-scoped credential are both part of the signature, so signing against site-local time
  produces URLs that validate only when the site happens to be on UTC. `frappe.utils.now_datetime()`
  is site-local — the code uses `datetime.now(timezone.utc)` and says why in a comment, because
  this app has already been bitten once by exactly this confusion (the Turnstile always-fail bug).

- **A signed URL cannot be revoked.** Once minted it works until it expires, even if the
  learner's access is pulled a minute later. The 15-minute TTL is the mitigation, which is why
  it is a setting rather than a constant, and why it is clamped at both ends.

- The new test suite **rebuilds the string-to-sign independently from the spec and compares**,
  rather than asserting the implementation's own output. That distinction is the whole value: a
  subtly wrong signature still produces a perfectly well-formed URL, and the only symptom is an
  opaque 403 from GCS that cannot be diagnosed by inspection. It gets its own CI step, like the
  other Training suites, for the stub cross-talk reason already documented in `ci.yml`.

- The full operator runbook — apply, key creation, configuration, eight acceptance criteria,
  rollback and cost — lives on ERPNext task **TASK-2026-01150**, not in the repo, because it is
  a one-time operational procedure with credential steps that must not be scripted.

## [1.208.0] - 2026-08-01

### Added

- **A Training module — courses anyone can author, without a deploy.** This is phase 1 of
  four: the data model, desk authoring, and the assignment engine. The learner-facing
  player, the drag-and-drop builder and certificates follow in later releases. Everything
  ships **dormant** (`Training Settings → Training Enabled` is off), so nothing
  auto-assigns and nothing is emailed until it is switched on deliberately.

  Built standalone. This site has neither `hrms` (so no `Training Program` / `Training
  Event` / `Training Result`) nor `lms` — the lms-related exclusions in `hooks.py` are
  stale defensiveness, not evidence of an install. Every DocType is prefixed `Training ` and
  deliberately avoids the six hrms names, so installing hrms later cannot collide. Unrelated
  to `Training Insight` in `ai_governance`, which is AI training data; both READMEs now say so.

  **Content is versioned; progress is not — and the docstatus is the mechanism.** A
  `Training Course` is the stable identity (title, slug, audience, policy, gates) and holds
  no content. A `Training Course Version` holds the content and is *submittable*: authors
  edit `docstatus 0`, the runtime will only ever read `docstatus 1`. Publishing **is**
  `submit()`. So "an author saved a half-finished edit into a live course" is not a bug
  prevented by discipline — it cannot be expressed. Amend is disabled, because an amendment
  would be a second document claiming to be the same version, and completions record version
  numbers.

  Publishing asks the author one question that cannot be defaulted: **minor edit** (a typo,
  clearer wording — everyone's completion stays valid) or **material change** (the course now
  teaches something different — completions are superseded and retakes raised). Deciding it
  lazily at read time would make a completion's validity depend on when you asked.

  `create_draft_version` deep-clones the live version **preserving every `lesson_key`,
  `block_key` and `checkpoint_key`**. Regenerating them would have been simpler and would
  have silently reset every in-flight learner to lesson one on the next typo fix.

  Lessons are top-level records pointing back at a chapter's key, not child rows of a
  chapter — Frappe has no grandchild tables, and a chapter is already a child row.

- **Answer keys are structurally unable to reach a browser.** At publish, each lesson is
  materialized into two payloads: `published_content_json` (what the player renders, built
  with every correct-answer marker stripped) and `answer_key_json` at **permlevel 1**.
  Learner roles hold *no DocPerm at all* on `Training Course` / `Version` / `Lesson` /
  `Content Block` / `Checkpoint` / `Question` / `Answer Option`, so `/api/resource/Training
  Question` refuses them outright — a defence that survives a future endpoint being careless
  with `fields=["*"]`.

  `_split_lesson` is the single function permitted to build a learner-facing payload, and
  `tests/test_training_publish.py` walks its serialized output *to any depth* looking for
  answer markers, rather than asserting that today's known keys are absent. That distinction
  earned its keep immediately: the test caught the first implementation shipping each
  option's `explanation` — text written specifically to say why an option is right or wrong —
  to the client *before* the learner answered. Fixed; explanations now live in the key and
  are revealed after answering.

  In-video checkpoint timestamps are not shipped either. The public payload carries a
  per-block *count*; a list of `at_seconds` is a map of exactly where to skip to, so the
  runtime will hand out the next one at a time.

- **Assignment engine.** Required courses carry rules ("everyone in Production", "every
  Senior Technician", "anyone on the Technician role profile") which resolve to people on
  hire and on department/designation/grade/employment-type/role-profile change. First match
  wins, so somebody caught by two rules is still assigned once and the rule recorded is the
  one that explains why. One *open* assignment per (course, user), enforced in `validate`
  rather than by a unique index — because a second row is exactly what recertification is,
  and an index could not tell the two cases apart.

  Both doc_event guards are load-bearing. `Employee.on_update` compares against
  `get_doc_before_save()` and returns immediately unless a field a rule keys off actually
  moved; without it every Employee save enqueues a full sweep. `User.on_update` guards on a
  roles-set diff and enqueues after commit, because it fires on paths adjacent to login and
  a slow sweep must never delay one.

- **Video is authored on Drive but will be served from GCS, and that indirection is the
  feature.** A Drive preview frame is cross-origin: the player cannot read `currentTime`,
  cannot `pause()`, and therefore cannot run in-video checkpoints *or* measure watch
  coverage. Embedding from Drive would have quietly cost two of the three attention
  mechanisms. `Training Video Asset` therefore records both the Drive source and the GCS
  object the player streams via a short-lived signed URL. An `External Embed` block type is
  still allowed for low-stakes content — but the server refuses to apply a coverage gate to
  it and stamps the reason, so a compliance course cannot lose its teeth because somebody
  picked the convenient block type.

  `duration_source` matters more than it looks: coverage divides by `duration_seconds`, so a
  hand-typed 600 against a real 900-second video lets an 80% gate pass on 53% of an actual
  watch. Duration is probed from Drive's `videoMediaMetadata.durationMillis` and the field is
  locked when probed; manual entry survives only as the flagged fallback.

### Notes

- **Granting `Training Learner` is not `add_roles`, and getting this wrong fails silently.**
  `User.validate` calls `populate_role_profile_roles`, which — for any user holding at least
  one Role Profile — rebuilds `roles` from the union of those profiles on *every* save.
  Direct roles are dropped, not merged. So `add_roles` appears to work, survives until that
  user is next saved for any reason, and then vanishes. On this site that is **11 of 15**
  active employees. The inverse is worse: giving a Role Profile to a profile-less user
  regenerates their roles from it and wipes `System Manager` — and the four profile-less
  users here include the System Managers.

  `training/roles.py` holds the two correct paths (extra single-role profile for profiled
  users, direct grant for the rest, never swapped) and is shared by the seeding patch and the
  new-hire hook so they cannot drift. `tests/test_training_roles.py` pins it, including a
  test asserting that a *direct* grant to a profiled user still evaporates — so if that
  assertion ever stops failing, the stub has stopped modelling reality.

- **`Training Learner` keeps `desk_access = 0`** and must continue to. It is held by customer
  contacts as well as staff; desk access would turn each of them into a billable System User.
  Nothing at runtime would complain. Pinned in a test.

- The three new bench-free suites each get **their own CI step**. Running them as one
  `unittest a b c` cross-talks: the roles suite imports the real
  `erpnext_enhancements.training.roles`, and `from ... import roles` in `training.assignment`
  then resolves the attribute already set on the package rather than the assignment suite's
  stub. Individually all three pass; combined, one fails. Same class of trap as the
  QuickBooks pytest/unittest split.

- Watch coverage measures **time elapsed with the video playing and visible** — never
  attention. A learner can start a video and walk away. That is precisely why all three
  mechanisms exist together, and why the compliance artefact is always coverage **plus**
  checkpoint accuracy **plus** quiz score. The module README says so in as many words, so
  that nobody discovers the nuance during a disciplinary conversation.

- Assignment rules can target Department, Designation, Role or Role Profile. **Employee Grade
  and Employment Type are listed but refused on this site**, because those doctypes ship with
  hrms and are not installed here — even though `Employee.grade` and `Employee.employment_type`
  exist as columns (ERPNext ships the fields as Links to doctypes that arrive with hrms). A
  column guard alone would not have caught it: the rule would have saved and then produced a
  Dynamic Link to a missing doctype, failing at read time rather than where the mistake was
  made. The course now refuses the row with a message saying why, and the options stay listed
  so nothing needs changing if hrms is ever installed.

- No `portal_menu_items` entry and no `/training` route yet — both would point at a page that
  arrives in phase 2, and a dead menu item teaches people to ignore the menu.

## [1.207.0] - 2026-08-01

### Added

- **Latitude and Longitude on Address are now editable, so a site with no findable
  address can still be located.** New construction is the case that prompted it: a lot
  number, a stake in a field, a parcel off an unnamed road — nothing Google can resolve from
  text, so before this the site simply could not be put on a map. Type a pair and every map
  in the app uses it: the Pick Routing Map, the Travel Trip map, the POI picker, the
  `/itinerary` page and the Address form's own preview. None of those needed changing — they
  have preferred a stored point over geocoding since v1.206.0 and cannot tell where it came
  from.

  **The hard part was not making the fields writable — it was that the existing rule would
  have eaten what you typed.** Since v1.205.0 the coordinates are wiped whenever any address
  component is hand-edited. That rule is load-bearing: a point Google derived from an address
  that has since changed will route somebody to the wrong building, and nothing on screen
  looks wrong. But applied to a typed point it is exactly backwards — you entered
  coordinates *because* the text cannot locate the site, so correcting a typo in the city
  must not throw them away.

  So the rule now turns on **provenance**, recorded in a new read-only **Location Source**
  field:

  | Source | Set when | Survives an address edit? |
  |---|---|---|
  | `Google` | picked from the autocomplete | no — it described the old text |
  | `Manual` | typed or pasted into the form or the dialog | yes |
  | blank | pre-v1.207.0 rows, imports, API writes | treated as `Google` |

  Blank meaning `Google` is what makes this a no-patch change: every coordinate stored before
  today was written by a pick, so the legacy reading is already the true one.

  **The server deliberately does not stamp a provenance on a blank one**, and review is why.
  The first cut had `before_save` promote blank to `Manual`, reasoning that a point written by
  an import or the API would otherwise vanish on the next address edit. But that hook cannot
  tell an API write from the far commoner case of a legacy row being re-saved for an unrelated
  reason — ticking *Is Primary Address*, a party link, a patch. All of those carry a
  Google-derived point and a blank source, so the stamp would have migrated the entire existing
  Address table to `Manual`, the one value in which clear-on-edit stops firing, silently and on
  a read-only field. Retarget such a record months later and it keeps the old building's
  coordinates under the new address — exactly the failure the provenance split exists to
  prevent. So blank stays blank and is read as `Google`: a point of unknown origin is assumed
  derived from the text and dies with it. Wrong in the safe direction. An importer that wants
  its points to survive sets `custom_location_source` itself.

  **Provenance is not inferred from the Place ID**, which was the obvious shortcut and is
  wrong twice over. A pick stores `meta.latitude || 0`, so a real Place ID can already sit
  beside 0/0 — and more fundamentally the ID identifies the *address text*, not the point, so
  using it as the coordinate flag would mean deleting it whenever somebody nudged a pin,
  discarding the one field Google's terms let us cache indefinitely.

  **A hand edit is detected with `df.onchange`, not a field handler.** A
  `frappe.ui.form.on("Address", { custom_latitude })` handler fires identically for a person
  typing and for our own `frm.set_value` during a pick — the framework does not forward that
  distinction to form scripts. `onchange` is reached only from the control's own
  validate-and-set path, so it means "a person edited this" and nothing else. It has to be
  re-planted on every `refresh`, because the layout swaps `df` for a per-docname copy before
  the form script runs.

  **Pasting "40.889402, -111.880771" into one box is intercepted**, and this is the reason
  the feature needed a paste handler rather than just a range check. Frappe's Float control
  runs its input through `frappe.utils.eval_expression`, which literally `eval()`s anything
  that parses as arithmetic — so that string becomes `40.889402 - 111.880771` = **-70.99**, a
  perfectly valid latitude in the Southern Ocean. No validation anywhere can reject it,
  because there is nothing wrong with the number. Pasting the pair is the single most likely
  thing anyone does with these fields, so it is caught before the control sees it and split
  across both boxes.

  **Two failures are now refused on save**, in a new `Address` `before_save` hook:

  - **Half a pair.** Every consumer reads a zero axis as *no point at all*, so a filled
    Latitude with an empty Longitude saved cleanly and located nothing — the worst kind of
    bug, because the form said it worked.
  - **Out of range**, which in practice means a transposed pair. A Utah longitude is not a
    valid latitude, and left alone it puts the site in the wrong hemisphere.

  **The form's map preview now follows the point.** For a site located by coordinates the
  address text is by definition the thing that could not find it, so embedding the text would
  show the wrong place — and typing a pair with nothing moving on screen reads as "it didn't
  work". The embed also gets `z=17`, building scale, which the text form cannot ask for.

  The coordinates are also on the **quick-entry dialog**, in a collapsed "Coordinates"
  section — that dialog is the main creation path when `ee_contacts_ux` is on, and creating a
  new-construction site there only to reopen it on the full form to place it would be an odd
  gap. The paste guard is shared with the form rather than reimplemented, so both surfaces
  behave identically.

  One more case review caught: **picking a second address whose Place resolves without a
  location** used to leave the first pick's coordinates sitting under the new text, which
  every map then preferred over that text. A pick that brings no point now clears a
  Google-derived one — and still leaves a `Manual` one alone, since that was typed precisely
  because the address cannot locate the site.

### Notes

- **0.0 remains the "absent" sentinel**, so the equator and the prime meridian cannot be
  stored. The columns are `NOT NULL DEFAULT 0` and making them nullable would mean changing
  the guard in four consumers to gain coordinates nobody here will ever need. Sapphire's
  service area is Utah and Arizona.
- **`no_copy` stays on**, so duplicating an Address for a neighbouring lot drops the point
  rather than inheriting it. That is plainly right for a picked point and arguably wrong for a
  hand-surveyed one, but relaxing it would start copying Google points too.
- The **Place ID stays read-only**. A user typing a `ChIJ…` string would be forging
  provenance against their own point.
- **Ctrl+Z does not round-trip the provenance stamp** — frappe's undo applies changes through
  `set_value`, which by design does not fire `onchange`. Undoing a typed latitude restores the
  number while leaving the source as `Manual`. Self-correcting on the next real edit.
- A new bench-free suite, `tests/test_address_coordinates.py`, covers the gate; both of its
  guards were mutation-checked (deleting either fails the suite rather than passing quietly).

## [1.206.0] - 2026-08-01

### Changed

- **Maps now reuse the coordinates an address was picked with, instead of geocoding its
  text.** v1.205.0 started storing an exact point on every Address chosen from the Places
  autocomplete. Three consumers were still throwing that away and asking Google to re-derive
  it from the address string on every load: the Pick Routing Map, the Travel Trip agenda map,
  and the Travel POI form's location picker.

  Two wins, and the second is the one that matters. Every geocode is **billable** and needs
  the Geocoding API enabled — a stored column read is neither. And a geocode returns Google's
  best interpretation of a *string*, whereas the stored point is the building somebody
  actually picked out of a dropdown. For a supplier's will-call counter on a industrial
  street, those are not reliably the same place.

  **It is an optimisation layered on the text, never a replacement for it.** The
  overwhelming majority of Addresses predate v1.205.0 or were typed by hand and have no
  point at all, so every path keeps its geocode fallback and a single run mixes the two
  freely. Nothing gets worse for a coordinate-less Address — that is the whole design
  constraint, and it is what the new tests pin down.

  Where the point is now used:

  | Surface | Before | Now |
  |---|---|---|
  | Pick Routing Map — fallback pins | one billable geocode per stop | stored point, else geocode |
  | Pick Routing Map — `DirectionsService` waypoints | address string | stored point, else string |
  | Travel Trip agenda map | geocode, cached back onto the POI | resolved server-side, no client change |
  | Travel POI form picker | geocode on open and on "Locate from linked address" | stored point, else geocode |
  | `/itinerary` mobile map | **could not plot at all** without a POI Geolocation | stored point places it |

  That last row is a capability, not a saving: the `/itinerary` map is Leaflet with no
  geocoder, so a POI without its own Geolocation was simply absent from it.

  **0.0 is the "no point" sentinel, and that drove the guard.** Float custom fields are
  created `NOT NULL DEFAULT 0`, so every Address in the table reads back as `0.0` rather than
  `NULL` — and the autocomplete blanks the pair to a literal `0` when the address is edited
  by hand. An `is not None` check would therefore be true for the entire table and route
  every legacy stop to 0°N 0°E in the Gulf of Guinea, which Google will accept without
  complaint. So the guard is non-zero **plus** a range check: a lat/lng written the wrong way
  round is a valid-looking pair that lands in the wrong hemisphere, and a Utah longitude is
  not a valid latitude. Half a pair is refused outright.

  **A place ID does not imply coordinates.** The autocomplete stores `meta.latitude || 0`, so
  a pick whose Place carried no location persists a real place ID next to 0/0. Every check
  here branches on the numbers, never on the ID.

  **Every read of those columns is guarded, because a missing one fails hard.** `main`
  auto-deploys with no staging gate, so this code can be live before `bench migrate` has
  created the v1.205.0 fixtures on a site. Server-side that is a raw SQL error on every call
  rather than a graceful `None` — frappe's query builder validates the *format* of a select
  field, never its existence — so both Python readers gate on `frappe.db.has_column`, the
  same idiom as `api/comments.py`.

  The **client** read needed a different answer: `frappe.db.get_value` has no `has_column`,
  and asking for a field that is not in meta is rejected outright ("Field not permitted in
  query"), not returned as null. Since every consumer of that lookup sits inside its `.then`,
  an unguarded request would have taken the entire Travel POI picker down with it — no map,
  no pin, and not even the "link an Address first" message, which lives in the same dead
  callback. It now retries with the six fields that have always existed, so the form geocodes
  exactly as it did before. Caught in review, not in production.

### Notes

- **The Routes API engine deliberately still sends address text for its waypoints**, even
  where the legacy engine now sends coordinates. `Waypoint.location` is documented to accept
  a string; that it also accepts a bare `{lat,lng}` is inference from the origin/destination
  union and has not been checked against a live key. A rejected shape there does not fail one
  stop — it throws the whole request, and the catch latches `routesUnavailable` for the life
  of the dialog and only `console.warn`s. One coordinate-bearing stop would silently demote
  every re-route to the legacy engine with nothing on screen to explain it. Verify against a
  live key, record it in `docs/pick-routing-map-po-details.md`, then switch.
- **The depot and the typed finish point cannot carry coordinates.**
  `pickup_route_start_address` is a Data field with no Address record behind it, and the
  dialog's "custom" finish is typed live. Making either exact is a schema change, not a code
  one. It is one geocode for the same address every request, so Google caches it hard.
- **Address-derived points are not written back onto a Travel POI.** `cache_poi_geocode`
  never overwrites, so the copy would become permanent and outlive corrections to the
  Address — inverting the v1.205.0 rule that editing an address clears its point. There is no
  geocode being saved either, so the cost motive is absent.
- **The clearing guarantee remains client-side only.** No Address hook invalidates the
  coordinates server-side, so a REST write, Data Import or bulk edit that changes
  `address_line1` leaves the old point in place. The range guard cannot catch a stale point
  that is still a valid coordinate. Worth knowing before trusting these for anything
  automated.
- Maps deep links and the printed pick sheet still use the address **text** everywhere. A
  driver reading `40.889,-111.881` off a printed sheet would be a regression, and Maps URLs
  require `waypoint_place_ids` to pair 1:1 with `waypoints`, which a partially-covered run
  cannot satisfy.

## [1.205.0] - 2026-08-01

### Added

- **Addresses autocomplete as you type.** Start typing in **Address line 1** and Google
  suggests real addresses; pick one and line 1, line 2, city, state, ZIP and country fill
  themselves in. It works on the full Address form *and* in the Address quick-entry dialog.

  **Both surfaces, because one of them is where the work actually happens.** With
  `ee_contacts_ux` on, "New Address" from a list, the awesomebar, a link field's *Create a
  new…*, or a party form's Contacts & Addresses section all open a quick-entry **dialog**,
  not the form. That dialog is a `frappe.ui.Dialog`, so `frappe.ui.form.on("Address")` never
  fires for it — a form-only implementation would have covered the minority of real address
  entry and been reported as "the autocomplete is intermittent". So the widget is a global
  (`erpnext_enhancements.address_autocomplete.attach`) that both surfaces call, which is
  also why it is in `erpnext_enhancements.bundle.js` rather than `doctype_js["Address"]`:
  the dialog opens from any doctype.

  **Why a hand-rolled combobox and not Google's own widget.** There is no supported way to
  attach Google's UI to a field Frappe built. `places.Autocomplete` — the widget that used
  to bind itself to an `<input>` — is closed to new customers, and its replacement
  `PlaceAutocompleteElement` is a sealed custom element that renders its own input and
  cannot wrap an existing one. What is left is the Autocomplete **Data** API
  (`AutocompleteSuggestion`), which returns predictions and leaves the UI to the caller. So
  this renders its own WAI-ARIA listbox — arrow keys, Enter, Escape, `aria-activedescendant`
  — and carries the "Powered by Google" mark the data API's policy requires whenever
  predictions are shown off a map. The public fountain-move form already went through this
  reasoning; this is that implementation, ported to the desk and corrected in four places
  (below).

  **This needs a Google console change that no deploy can make.** The key is the shared desk
  browser key, `Travel Settings.google_maps_api_key`, and it now needs **Places API (New)**
  enabled on it — the *legacy* "Places API" is a different Cloud service and does not
  authorise these calls. A key with Maps JavaScript but without Places (New) is the nasty
  case: the bootstrap loads, `importLibrary("places")` resolves, `AutocompleteSuggestion`
  exists, and then **every** request 403s. There is no init-time signal to check, so the
  widget counts consecutive failures and after three unbinds itself and hands the field back
  as plain text. The field description in Travel Settings now lists every API the desk maps
  features need, since that is where an admin actually looks.

  **Four corrections to the ported implementation**, each of which would have been a real
  bug here:

  | Ported as | Changed to | Because |
  |---|---|---|
  | No `language` on the request | `language: "en"` | Component text follows the *browser* locale otherwise. A desk in `es-MX` would return `Estados Unidos`, and country is a Link to Country. |
  | Country never read | Country resolved via `shortText` → `Country.code` | `longText` is localised; the ISO code is not. Looking the Country up by code is exact, and if there is no match the field is simply left alone rather than filled with a value that fails validation on save. |
  | `includedRegionCodes: ["us"]` hardcoded | Derived from the record's own `country` | The public form is US-only; the Address doctype is not. Non-US addresses would have returned nothing at all. |
  | New session token minted in `.then()` | Minted in `.finally()` | A *failed* details call left the spent token in place, and Google bills a reused token as if none had been sent. |

  Also: `state` now falls back to the long name when Google has no two-letter form, which
  outside the US is most of the time — writing "Nordrhein-Westfalen" beats writing nothing.
  And the street line follows the *local* order rather than always leading with the number:
  half the world writes "Hauptstrasse 12". The order is read back off the place's own
  formatted address instead of a hardcoded list of countries, falling back to number-first
  whenever it cannot be determined — which includes the common US case where the formatted
  line abbreviates the route ("Pkwy" for "Parkway") and so cannot be matched against it.

  **A pick replaces the address; it does not merge into it.** The public form fills empty
  fields, so "only overwrite what Google actually knows" was safe there. On a saved Address
  it is not. Correct an existing record from "100 First St, Suite 5, Phoenix AZ 85004" to a
  place with no `subpremise`, and the old rule leaves "Suite 5" behind — the record now
  reads as a real, deliverable address carrying the unit number of a different building.
  So line 2, city, state and ZIP are written even when empty. Two deliberate exceptions:
  line 1 is never blanked (picking a locality or a POI returns no street at all, and the
  field is mandatory), and country is only ever written when the code lookup resolves,
  rather than emptying a mandatory Link.

  **Escape closes the list, and nothing else.** Left to bubble, it reaches bootstrap's modal
  handler and frappe's window-level `handle_escape_key` → `cur_dialog.cancel()` — so
  dismissing a suggestion list in the quick-entry dialog would have discarded every field
  already typed, with no confirmation. Frappe stops Escape for its own dropdowns, but only
  for controls wrapped in `.awesomplete`, and a Data field is not one. Tab keeps its default
  and still moves to the next field.

  **One pick, one map redraw.** The Address form already rebuilds its Google Maps embed
  whenever any component field changes. Filling five fields fires that five times, so the
  join is suspended while the pick is applied and run once at the end. The fields go in as a
  single `frm.set_value({...})` object call, which also skips fields that do not exist
  instead of throwing.

  **Bind-once, reset-per-document.** There is one `Form` object — and one `<input>` — per
  doctype for an entire page load; routing to another Address just refreshes it. Binding on
  each `refresh` would stack listeners on the same node, so the widget attaches once per
  input (compared by *node*, not a boolean — a layout rebuild would replace the input while
  leaving a flag looking satisfied) and each refresh only resets the search session, minting
  a fresh token so one document's typing is not billed against the next one's.

  The listbox is appended to `<body>` and positioned `fixed` at `z-index: 1100`: the dialog
  scrolls its own body and stacks at 1050, and the desk's own Awesomplete listbox sits at 4
  — under both the sticky form tab bar (5) and the page head (6).

  Degrades to a plain text field on every failure path — no key configured, blocked script,
  wrong API tier — and says so with `console.warn`. Deliberately never silent: a swallowed
  `TypeError` in this exact code once looked identical to "no key configured" for days
  (v1.160.2).

  **The picked place is recorded, not just its text.** Three new read-only Custom Fields on
  Address — **Google Place ID**, **Latitude**, **Longitude** — filled from the same Place
  Details call the address text comes from, so they cost nothing extra. Coordinates remove a
  billable, approximate geocode for every map that would otherwise look the address up from
  its text again (the trip map, the POI pins, the Pick Routing Map all do this today), and
  the place ID is the one field Google explicitly exempts from its no-caching rule — it can
  be stored indefinitely and survives the address text being reformatted.

  They are **cleared the moment the address is edited by hand.** Coordinates still pointing
  at the previously picked building, while the visible address says somewhere else, is worse
  than storing nothing: anything downstream that trusts the coordinates over the text routes
  to the wrong place, and nothing on screen looks wrong. On the form that is a handler on
  every component field; in the quick-entry dialog, which shows no place fields to watch,
  the check happens once on save by comparing line 1 against what the pick actually filled
  in.

  New bench-free node test, `scripts/test_address_components.js`, covers the Google →
  Frappe component mapping (the `locality` → `postal_town` → `sublocality` city ladder, the
  short-vs-long state rule, the bare ZIP that must not become ZIP+4). It exercises the
  UK/Nordic and no-short-form branches, which a US-only production site never can.

### Notes

- **The three new fields are added to the pinned Address `field_order` Property Setter**, not
  just to `custom_field.json`. That layout is customised, so `insert_after` alone would not
  have put them on the form.
- **The widget itself stores nothing.** It hands `{place_id, formatted_address, latitude,
  longitude}` to whoever attached it and lets them decide; the Address-specific field names
  live in the two callers. That is what keeps it attachable to a second doctype later.
- **The attribution image is still the `maps.gstatic.com` hotlink** the public form uses,
  with the same `onerror` fallback to plain text. It is an undocumented legacy Maps v3
  asset; current Google guidance points at a downloadable logo pack instead. Kept identical
  to the portal so there is one attribution mechanism rather than two, and the text fallback
  keeps the policy satisfied if it ever disappears.
- Frappe v16 ships its own `AddressAutocompleteDialog` (Geoapify/HERE/Nominatim), gated on
  **Geolocation Settings**. It is a separate search-then-create dialog and does not touch
  `address_line1`. If that setting is ever switched on, two autocomplete UIs will coexist.

## [1.204.2] - 2026-08-01

### Fixed

- **A single-stop run degraded to geocoded pins instead of drawing its route.** The 1.204.1
  guard logged, correctly, `unusable stop order from routes: [-1] for 1 stops`.

  **`-1` is not corruption — it is Google's sentinel for "I did not reorder these."** A run
  with one intermediate waypoint has nothing to optimise, so that is the expected answer,
  and rejecting it threw away a perfectly good route. The documented behaviour ("empty when
  optimisation is off") does not mention the sentinel; only a live key surfaced it.

  An all-`-1` response now falls back to submission order and the route draws normally. The
  1.204.1 validation is unchanged for genuinely malformed input — a *mixed* array containing
  `-1` alongside real indices is still rejected, because that is not a shape with an obvious
  reading.

- **The pick sheet could claim drive-time order for a run that was never reordered.** "We
  have an order" and "the order is optimised" are different things, and the map looks
  identical either way. Both engines now report whether optimisation actually happened —
  Routes via the `-1` sentinel, DirectionsService via the presence of `waypoint_order` — and
  the sheet says *"purchase-order sequence"* unless the run was really optimised.

  A single stop is exempt: there is exactly one possible order, so the caveat would be
  noise.

  This is the third time in this feature that a status line could not distinguish two
  different states. It is the recurring failure here, more than any individual API call.

## [1.204.1] - 2026-08-01

### Fixed

- **The Routes engine could crash the map with a stack trace three frames from its cause.**
  With "Use Routes API" on, production threw:

  ```
  TypeError: Cannot read properties of undefined (reading 'key')
      at PickRoutingMap.placeMarkers
      at PickRoutingMap.drawRoute
  ```

  `drawRoute` trusted `optimizedIntermediateWaypointIndices` to be a complete, in-range
  permutation of the stops submitted. When it was not, `stops[originalIdx]` came back
  `undefined` and the failure only surfaced later, inside the marker loop, reading `.key`
  off nothing. `safeRoute()` caught it and degraded, so the map stayed usable — but the
  reported error named the wrong function.

  The order is now validated rather than trusted: every index must be a whole number inside
  `stops`, with no duplicates, and the array must cover every stop exactly once. An
  over-long array is rejected too, because something like `[0,1,2]` for two stops filters
  down to a plausible-looking `[0,1]`, and accepting that would mean quietly reinterpreting
  a response whose convention we evidently do not understand.

  **The dangerous case here was never the crash.** An order that is merely *incomplete*
  would have dropped a supplier from a driver's run while still looking like a valid
  optimised route. That is why an unusable order now degrades to purchase-order sequence
  with an honest message instead of being patched up.

  The mismatch is logged with both the raw value and the stop count, so a recurrence names
  itself instead of needing another round of production archaeology.

- **Localized distance/duration strings are type-checked.** Observed on a live key,
  `leg.localizedValues` logs as an obfuscated object, so `.distance` returning a string is
  not safe to assume. A non-string would have passed the `||` fallback in `drawRoute` and
  printed `[object Object]` as a distance on a driver's pick sheet. Only real strings are
  used now; anything else falls back to formatting `distanceMeters` client-side.

### Notes

Three of the unknowns shipped in 1.204.0 were settled against a live key and need no further
work: `optimizedIntermediateWaypointIndices` returns a genuine `Array` with the documented
zero-based semantics (`[1, 0]` for a two-stop run), `'viewport'` **is** a legal field-mask
string and comes back as a real `LatLngBounds`, and `leg.startLocation.lat` **is** a number
property rather than a method. Note `route.legs` has one more entry than there are stops
(origin→A, A→B, B→finish), which is the same convention the legacy engine used.

## [1.204.0] - 2026-08-01

### Added

- **Pick Routing Map can route via the Routes API** (`routes.Route.computeRoutes`), behind
  a new **Travel Settings → "Use Routes API (beta)"** check, **off by default**.

  Google deprecated `DirectionsService`, `DirectionsRenderer` and `Marker`. Nothing is
  scheduled for removal and at least 12 months' notice is promised, so this is longevity
  work with no deadline — which is exactly why it ships switched off, with the legacy engine
  retained as an automatic fallback rather than replaced.

  **Why a setting and not a constant.** Routes needs "Routes API" enabled on the Cloud
  project *and* added to the key's restriction list. That is a Google console change no
  deploy or rollback can perform, and this app auto-deploys from `main` with no staging
  gate, so a code-only switch would black out the map for every driver between merge and
  somebody remembering to flip it in Google. The setting is also the kill switch for the one
  failure the fallback cannot catch — a Routes call that *succeeds* while we read its output
  wrongly. A fallback only helps when the call fails.

  Both engines are normalised to one shape, so `drawRoute` does not know which ran. Three
  renames in that mapping are silent-corruption traps rather than compile errors, and each is
  commented where it happens:

  | Legacy | Routes | Trap |
  |---|---|---|
  | `leg.duration.value` (seconds) | `leg.durationMillis` | **milliseconds** — copying the arithmetic is 1000x wrong |
  | `routes[0].waypoint_order` | `optimizedIntermediateWaypointIndices` | plural *Indices*; the REST API uses the singular |
  | `leg.distance.text` | `leg.localizedValues.distance` | may be absent from the `legs` field mask; renders blank without erroring |

  The last one now falls back to formatting `distanceMeters` client-side rather than showing
  an empty row.

  **Computing happens inside the engine's `try`; painting happens outside it.** Getting this
  wrong — which the first cut did — means a rendering exception is caught by the *engine's*
  handler, blamed on the engine, and a correct optimised route thrown away to re-run the
  other one. On a key that has moved to Routes and no longer permits Directions, that second
  call is denied and the driver ends up in purchase-order sequence being told to enable an
  API that was never the problem. Caught in review before it shipped.

  A failed Routes call also latches the engine off for the life of the dialog. Without that,
  every checkbox toggle pays another doomed *billable* request before falling back, and this
  dialog re-routes on every toggle.

### Fixed

- **`ensureGoogleMaps` could hand back a Google Maps namespace missing the library its caller
  needed.** It short-circuited on `window.google && window.google.maps`, but that singleton
  is shared with `travel_trip_map.js` and `travel_poi.js`, neither of which imports a
  library — so whichever consumer loaded first decided what was present.
  `google.maps.routes` and `google.maps.marker` stay `undefined` until `importLibrary` is
  awaited even though the root namespace looks complete. It now resolves on the imported
  library. Latent before this change; load-bearing now.

- **An async `route()` could have failed invisibly.** Adding `await` made an uncaught
  rejection possible, which would skip all four degradation rungs at once and leave a blank
  dialog with no message — the exact failure v1.202.3 was about. Every call now goes through
  `safeRoute()`, which degrades with a message instead. `isStale()` is re-checked after every
  `await` rather than once, because `await` gives more suspension points than the old
  callback did and the user re-ticks stops mid-flight.

- **`degrade()` only tore down the legacy renderer**, so a Routes polyline would have
  survived underneath the geocoded pins, drawing a route the code had just disowned. Both
  engines now tear down through `clearRouteLine()`.

- **`startMap()` could latch the map off permanently.** It set `mapBuilt = true`, then
  returned silently if the generation had moved on while Google was loading — leaving the
  latch set with no map ever created, so `buildMap()` would never retry. Ticking a stop
  while the API was still loading was enough. Only `destroyed` aborts now: the map object
  is not generation-specific, and `route()` does its own staleness checks. Found by review,
  and the same silent-blank-pane class as v1.202.3.

### Notes

`MAX_ROUTE_STOPS` deliberately stays at **23**, not the 25 Routes documents. No lower cap for
optimisation is documented, but that is a claim from *absence*, and the legacy engine is still
rung 2 where 23 is the real ceiling. Raise it only after 25 intermediates with
`optimizeWaypointOrder` has run against a live key.

## [1.203.0] - 2026-08-01

### Changed

- **PDF rendering moves from wkhtmltopdf to the Chromium backend.** wkhtmltopdf has been
  unmaintained since the project was archived in 2023 and `0.12.6.1-3` is its final release;
  chrome is the backend with a future. Chromium was only proven working on this host on
  2026-08-01 (see 1.202.x), so this is the first point at which the switch was possible.

  Two obstacles had to be removed, and neither was the obvious one.

  **A Property Setter was blocking it site-wide.** `Print Format-pdf_generator-options`
  (created 2025-11-20, `is_system_generated`) narrowed the field's Select options to
  `wkhtmltopdf` alone. Select options are enforced by `_validate_selects` on every save, so
  **no** format could be moved to chrome at all — saving one threw `PDF Generator cannot be
  "chrome". It should be one of "wkhtmltopdf"`. Frappe ships the field as
  `wkhtmltopdf\nchrome` and types it `DF.Literal["wkhtmltopdf", "chrome"]`. Removed by
  `patches/drop_pdf_generator_options_restriction`, which backs off if the value has since
  been widened.

  **Standard formats refuse ORM writes and re-sync on migrate.** `Print Format.validate`
  throws "Standard Print Format cannot be updated", so `doc.save()` cannot touch
  `Sales Invoice Standard` or the fourteen like it — and because those formats re-sync from
  their app's JSON on every migrate, even a successful direct write would not survive the
  next deploy. Handled by `ensure_chrome_pdf_generator()` on `after_migrate`, using
  `frappe.db.set_value` (the same low-level write frappe's own
  `sets_wkhtmltopdf_as_default_for_pdf_generator_field` patch uses) and re-applied every
  migrate rather than once.

  Blank values are set too, not skipped: `print_utils.get_print` resolves
  `Print Format.pdf_generator` **or the literal `"wkhtmltopdf"`**, so an empty field is not
  neutral — it means wkhtmltopdf.

  **Verified before switching**, both engines against every format on this site that has a
  document to render — 16 of 17 produce a valid PDF under chrome, including a 128-line Sales
  Invoice at 7 pages and a 91-line Quotation. Two caveats found and recorded:

  - **Chrome output is 2–4x larger** for the same document (a 7-page invoice goes 106 KB →
    473 KB). That matters most for emailed attachments.
  - **Pagination is not identical.** `Quotation Standard` goes 5 → 6 pages on the longest
    quotation; two formats go 2 → 1.

  `Test Purchase Order Format` is **excluded and stays on wkhtmltopdf**. It trips a genuine
  bug in frappe's chrome path: `pdf_generator/pdf_merge.py` merges one header page onto each
  body page and indexes `header.pages[i]` without a length check, raising `IndexError:
  Sequence index out of range` when the body outruns the header render. It reproduces on
  nothing else here — every real document renders — and the format is an abandoned builder
  experiment last touched 2025-10-23. The exclusion is a named constant with the reason
  beside it, so it can be dropped when the format is deleted or upstream bounds that index.

## [1.202.3] - 2026-08-01

### Fixed

- **The Pick Routing Map never drew a map, and said nothing about it.** The map pane was
  blank on every open; no route, no console error, and no notice.

  `buildMap()` needs a sized container, because a Google map initialised in a 0x0 box
  renders blank. It waited for dimensions by re-scheduling itself on
  `requestAnimationFrame` while the modal animated in. That chain could stop, and when it
  did the method returned before ever calling `ensureGoogleMaps()` — so the feature died
  *upstream of every error path it has*. All three degradation messages, carefully written
  to explain each failure, live past that early return.

  Verified on production: `document.querySelectorAll('script[src*=maps.googleapis]')` was
  empty and `window.__eeGoogleMapsPickupReady` undefined, proving the loader was never
  invoked, while the container measured 850x601 by the time anyone looked. Changing any
  control re-rendered against a sized container and the map appeared immediately.

  Replaced with a `ResizeObserver` on the container, a slow interval as a backstop (a
  `ResizeObserver` does not fire under a `display:none` ancestor on every engine), and a
  hard 8-second deadline that **shows a message** instead of failing silently. `buildMap()`
  is now also called from Bootstrap's `shown.bs.modal` — the event that exists for precisely
  this — so it no longer has to win a race against the show animation. It is idempotent
  behind the `mapBuilt` latch, so both entry points are safe.

- **The printed pick sheet could not distinguish "Google refused" from "routing never
  started".** It printed the same sentence — *"Stops are in purchase-order sequence, not
  drive-time order"* — for every unoptimised case. That ambiguity sent a real diagnosis at
  the Google Directions API and its Cloud Console key restrictions for hours, while the
  actual fault was the 0x0 container above and no request was ever made. The sheet now
  appends the specific reason from `this.notice`, which already held it.

  Worth stating plainly: a status line that cannot tell "it failed" from "it never ran" is
  worse than no status line, because it points confidently at the wrong system.

## [1.202.2] - 2026-07-31

### Fixed

- **Purchase Orders were going to suppliers with no company branding.** `Purchase Order -
  Sapphire` now renders the letter head.

  A print format with `custom_format = 1` supplies the entire document body, so Frappe does
  **not** inject the letter head into it — it only emits the `#header-html` block that
  `repeat_header_footer` builds for *standard* formats. `letter_head` is offered to the
  template in the render args (`www/printview.py`, the same `args` dict used for both code
  paths) and is silently dropped if the template never asks for it. Every stock ERPNext
  format asks; ours did not.

  This was invisible from the outside and easy to misread. The rendered HTML *does* contain
  the string `letter-head` — but only as a CSS rule (`.print-format .letter-head { ... }`),
  which is what a naive grep finds. The real signal is that the PO HTML has no
  `#header-html` div at all where a Sales Invoice has one, and that the PO's PDF was
  **byte-identical** (18,475) with and without a letter head attached to the document.

  Rendered on page one only, deliberately: the identifier a counter clerk needs on every
  sheet is the PO number, which is already in the header bar and repeats via the table
  header. A logo on every page would add roughly 52 KB per page for no working benefit.

  Related: the logo only reaches any PDF at all as of the letter head change made the same
  day — it is now inlined as a base64 data URI, because wkhtmltopdf's separate
  `--header-html` sub-render cannot fetch `/private/files/…`. See `docs/pdf-generation.md`.

## [1.202.1] - 2026-07-31

### Fixed

- **`docs/pdf-generation.md` gave a command that installed nothing, and named the wrong
  setting.** Docs-only; no executable behaviour changes. Corrected with a shell on the VM,
  which the two earlier passes did not have — and which is why they misdiagnosed it.
  - **The host is Debian 12 (bookworm), not Ubuntu.** The runbook's library step listed
    `libasound2t64`, an Ubuntu 24.04 `time_t`-transition name that does not exist on
    bookworm. `apt-get install` aborts wholesale on one unknown package, so that single
    word meant **none** of the other fifteen packages installed — while appearing to be a
    normal failed attempt. Same root cause explains the leftover `rc wkhtmltox …jammy`
    entry in dpkg: the Ubuntu build depends on `libjpeg-turbo8`, which bookworm does not
    have.
  - **`Print Settings.pdf_generator` is not what the server reads.** `print_utils.py`
    resolves the generator from **`Print Format.pdf_generator`**; Print Settings is only
    read client-side and appended to the download URL. Frappe's own patch
    `sets_wkhtmltopdf_as_default_for_pdf_generator_field` has run here and pinned **28
    formats to `wkhtmltopdf`** — including this app's `Purchase Order - Sapphire`. So the
    `chrome` setting never governed server-side rendering, and fixing wkhtmltopdf is a
    prerequisite for the PO print format rather than an alternative.
  - **Both backends are non-functional rather than degraded.** Debian's wkhtmltopdf
    **segfaults (rc=139)** on a one-line HTML file with no flags — the `unpatched qt`
    lines are warnings, not the cause. The bench's Chromium **traps (rc=133, SIGTRAP)** on
    `--version`, writing zero bytes to stdout and stderr, which rules out the timeout,
    memory and `/dev/shm` theories the document previously offered.
  - **Retracted: the missing-shared-libraries theory.** All 53 `ldd` entries resolve. Also
    ruled out and recorded so nobody re-checks: glibc (needs ≤2.25, host has 2.36), CPU
    features, ASLR (`setarch -R`), seccomp, `/dev/shm` (16 GB), and a corrupt download.
  - The runbook now leads with a verified patched-Qt **bookworm** package
    (`wkhtmltox_0.12.6.1-3.bookworm_amd64.deb`, with its sha256), notes it installs to
    `/usr/local/bin` which precedes `/usr/bin` on the bench PATH, and points Chromium
    re-provisioning at `bench setup-chrome`.
  - Restart guidance now states this box runs **systemd** (`frappe-bench.service`) and has
    **no supervisor**, so the common `supervisorctl restart all` idiom fails here.

- **`infra/` provisioned a rebuilt VM with the segfaulting wkhtmltopdf.** Not docs — this
  changes what a rebuild installs. `startup_script_packages` listed the Debian
  `wkhtmltopdf` package, which is the unpatched-Qt build proven above to segfault on any
  input. A rebuilt VM would have come up with a `/usr/bin/wkhtmltopdf` that looks
  provisioned and cannot render, which is worse than having none at all. Removed from both
  `variables.tf` and `terraform.tfvars.template`, with the reason recorded next to the list
  — the working patched-Qt build is a `.deb` that is not in apt and therefore cannot be
  expressed there. Added `libvulkan1`, the one entry in the bench Chromium's own
  `deb.deps` that the `chromium` package does not pull in.

## [1.202.0] - 2026-07-31

### Added

- **Pick Routing Map now shows what you are collecting, on screen and on paper**
  (WI/TASK-2026-01077, options (b) and (c) of the spike in
  `docs/pick-routing-map-po-details.md`).
  - **In the dialog**, each stop gains a collapsed disclosure listing its Purchase
    Order lines, grouped by PO: item code, description, quantity still to collect,
    and — where any has been received — an "(n of m received)" note. Fully received
    lines are dimmed rather than hidden, because at a will-call counter "it is not
    on the list" is ambiguous between *already collected* and *never ordered*.
  - **On paper**, a "Print pick sheet" action produces a route-ordered sheet with a
    tick box per line, addresses, contact numbers, and a sign-off line.

  Both read the payload `get_pickup_route_data` already returns — the item lines
  were on the wire and simply unrendered — so there is **no new endpoint, no second
  query, and no extra network call at the moment a driver in a truck needs the
  detail**. Opening a stop's lines deliberately does not `recompute()`: every
  re-route is a billable Directions call, and looking at what you are collecting
  must not cost one.

  The disclosure's open/closed state lives on the controller rather than in the DOM,
  because `renderList()` rebuilds every row on each tick, re-route and reorder;
  reading it back off the old nodes meant unticking one stop silently collapsed the
  lines being read on another.

  The sheet is generated in the browser rather than as a Frappe Print Format, which
  is a deliberate departure from how this app ships its other print surfaces. Two
  reasons, both structural: the optimised stop order exists **only** in the browser
  (Google returns it to the dialog; the server never sees it), so a Print Format
  would have re-queried and printed in purchase-order sequence — a sheet that
  contradicts the screen it came from, which is the divergence the spike rejected
  option (a) over. And browser print needs neither of the server-side PDF backends,
  which are both currently broken on this host (see `docs/pdf-generation.md`). The
  cost of that choice is stated in the code: paper is a snapshot, so a PO received
  after printing is invisible on the sheet.

  Line arithmetic (ordered / received / still to collect, and the fully-received
  tolerance) is computed once in `lineModel()` and consumed by both surfaces, so the
  sheet in a driver's hand cannot disagree with the screen it was printed from.

## [1.201.4] - 2026-07-31

### Fixed

- **The Purchase Order print format showed suppliers raw HTML markup.**
  `Purchase Order Item.description` is a **Text Editor** field, so it holds markup authored
  by staff in the Item master. The template escaped it, which printed a literal
  `<div><p>Use for waterproofing, first apply primer.</p>…` on a document that goes to a
  vendor. It now renders as HTML, which is what every stock ERPNext print format does with
  this field; `item_name`, a plain Data field, is still escaped.

  Only visible by rendering the format against real data — `PO-2026-00028` carries a
  389-character rich-text description. A one-line Purchase Order with a plain description
  looks perfect, which is why the structural checks (valid HTML, no Jinja errors, every
  section present) all passed.

- **Nothing in the payment-terms line was being escaped.** `a or b or "" | e` is a Jinja
  precedence trap: the filter binds to the empty string alone, so the two real values went
  out unescaped. Parenthesised into a `set`. The separator dash is now emitted only when
  there is a label, instead of leaving a dangling `—` in front of the amount.

  Both fixes verified by rendering against `PO-2026-00028` on the live site.
## [1.201.3] - 2026-07-31

### Changed

- **`docs/procurement-tracker-map.md` refreshed against the merged code.** It was written as
  groundwork *before* the four changes it was groundwork for, so once those merged it
  described a tracker that no longer exists — the old five-column layout, "there is no
  sorting of any kind", and the item-status bug presented as current.

  Now accurate to v1.198.0: the seven-column layout and which columns sort, the per-document
  quantity rollup and the Receive action on the header row, the sort state and the two silent
  Vue traps its implementation avoids, `procurement_quantities.py` and the four decisions
  baked into it, and the Purchase Receipt flow through ERPNext's own mapper.

  Fixed defects are **dated, not deleted**. Gotchas is split into *still open* — the
  never-unmounted Vue app, the `v-html` XSS surface, the two whitelisted endpoints with no
  permission check, the bare `except` around the supplementary sweep, the surviving display
  duplication — and *closed, and worth not reintroducing*. The reasoning behind a fix is
  usually more useful than the fix, and a reader who remembers the old wording should be able
  to see what changed.

  Two facts recorded that were not known when it was first written: the `OR`-join fan-out
  turns ten request lines into nineteen rows and a naive document total reports **720**
  against a true **362**; and header and item-row `project` now agree on all 70 Purchase
  Orders, but only because `cascade_project_to_items` fills blanks *on save*, so the union
  query is still the right one.

  Every `file:line` anchor was re-checked against the merged tree.

## [1.201.2] - 2026-07-31

### Fixed

- **`bench migrate` aborted on production, taking every pending patch with it.**

  ```
  bad json: .../erpnext_enhancements/workspace_sidebar/README.md
  orjson.JSONDecodeError: unexpected character: line 1 column 1 (char 0)
  ```

  Frappe's `frappe/model/sync.py` keeps a list of `IMPORTABLE_DOCTYPES`, and for each entry
  it scans the matching **app-level** directory and calls `orjson.loads()` on **every file
  it finds** — there is no `*.json` filter and no skip-list. The first non-JSON file aborts
  the entire schema sync, and with it the migrate.

  **Nothing in this repo changed to cause it.** `workspace_sidebar/README.md` had been
  sitting there since 2026-07-29 doing no harm. Frappe **16.29.0**, which landed in the same
  deploy, added `("desk", "workspace_sidebar")` to the importable list — so a directory that
  had been inert for months became an import target overnight, and a documentation file
  became a production outage.

  The README moved to [`docs/workspace-sidebars.md`](docs/workspace-sidebars.md), which also
  records why it cannot live beside the files it describes.

  Consequence while it was broken: the code from v1.194.2–v1.201.1 was deployed and live, but
  **no patch, fixture or `after_migrate` hook from any of it had applied** — so the
  Opportunity Kanban swap, its backfill, the primary-flag repair, the Purchase Order print
  format and the approval-stamp custom fields were all absent on a site whose version said
  otherwise.

### Added

- **`scripts/check_import_dirs.py`** + a CI step — fails the build if a non-JSON file appears
  in any app-level directory Frappe imports from.

  This class of bug deserves a guard rather than a note: the blast radius is the whole
  migrate, the failure is remote (it surfaces on deploy, not in review), and the triggering
  change lives in *someone else's* dependency — nothing in a PR diff would have shown it
  coming. Same shape as the hyphenated `www/` controller that `check_www_controllers.py`
  guards.

  The directory list is read from the **installed Frappe** rather than hard-coded, so it
  keeps up when Frappe adds another importable doctype; it falls back to a pinned list so CI
  still runs bench-free. Verified against the real failure: the guard flags the README before
  the fix and passes after it.

## [1.201.1] - 2026-07-31

### Added

- **`docs/pick-routing-map-po-details.md`** — a spike, not a feature: three costed options
  for showing Purchase Order item detail on the Pick Routing Map, with a recommendation.
  No behaviour change.

  The finding that changes the estimate: **the item lines are already on the wire.**
  `get_pickup_route_data` already returns `item_code`, `item_name`, `qty`, `received_qty`
  and `uom` for every line of every order behind every stop — they are simply not
  rendered. This is a rendering question, not a data question, so no new endpoint and no
  second round-trip.

  Recommendation is the inline HTML block, and the deciding argument is *agreement* rather
  than effort. The map already decides which suppliers have material outstanding using a
  rule that took production data to get right (`per_received`, not the status label). A
  separate report or print sheet re-derives that rule somewhere else, and the moment the
  two disagree the driver is holding a sheet that contradicts the screen — the same
  divergence the Procurement Tracker had to be fixed for. Rendering the payload the map is
  already drawing cannot diverge from it.

  Secondary argument: this is used in the field, and the inline option is the only one that
  needs no network at the moment it is read.

  Per the decision on this task, **no pricing appears**: a driver needs to know what to
  collect and check it at the counter, not what it cost. `grand_total` and `currency` stay
  in the payload and go unrendered.

  Two constraints recorded for whoever builds it: every re-route is a **billable**
  Directions call (the existing code re-routes on blur rather than keystroke for exactly
  this reason), and the map **degrades in three steps** — a detail view that only works at
  step one would be a regression in the field.
## [1.201.0] - 2026-07-31

### Added

- **A supplier-facing Purchase Order print format** — `Purchase Order - Sapphire`. The
  site had two abandoned print-format-builder attempts (`Test Purchase Order Format`,
  `PO Test Print Format`, both untouched since October 2025) and three ERPNext standards,
  none of which is something you would send a vendor.

  Contents, each confirmed rather than assumed: letterhead, PO number and date,
  required-by date, supplier with address and contact, deliver-to, itemised lines with
  **project per line** (`Purchase Order Item.project` is mandatory here under WI-014, and
  a supplier delivering to a job site needs it), net total, taxes, grand total, payment
  terms, and delivery/receiving instructions. **No item images** — heavy on a multi-page
  order and little help for parts identified by number.

  Print-safe CSS only: no flexbox, no grid, `page-break-inside: avoid` on rows and
  totals, and a `thead` that repeats across pages. The PDF engine on this host has been
  unreliable enough (see `docs/pdf-generation.md`) without asking it to do anything
  clever.

  It lives in **Enhancements Core** because procurement has no module of its own —
  `po_approval`, `po_segregation` and `procurement_project` all sit at the app root, and
  a Print Format needs a real Module Def to belong to.

- **`custom_approved_by` / `custom_approved_on` on Purchase Order**, and this is the part
  worth reading. The format prints an approver, and **there was nowhere truthful to read
  one from**: Purchase Order has no approver field, and `modified_by` is whoever touched
  the document *last* — which after any post-submit edit is not the approver at all.
  Printing that would have been confidently wrong on a document that goes to a vendor.

  So the two gates that already establish the fact now record it.
  `po_approval.stamp_approval` runs **last** in the `before_submit` chain, after
  `enforce_requester_separation` and `enforce_threshold` have both passed, so the stamp
  means "this order cleared both gates in this person's hands" rather than "somebody
  pressed submit". Orders submitted before this shipped print an em dash rather than an
  invented name.

### Notes

- **Not signed off.** The task's own acceptance criteria require generating real PDFs for
  a one-line order, a 30-line multi-page order, and one with very long descriptions — and
  PDF generation is broken on this host in both backends (v1.199.1). This format has been
  validated as HTML and its Jinja parses; it has **not** been through the PDF engine,
  because there currently is no working PDF engine to put it through. That was true
  before this change and is the reason the two earlier attempts were abandoned.
- Shipped via the `after_migrate` upsert that eight of the ten existing formats use,
  rather than the `hooks.py` fixtures allowlist that the other two use. Template edits
  then deploy on the next migrate with no export step, and `after_migrate` runs after
  fixture sync so it cannot be silently overridden. The trade-off — an admin's UI edit is
  overwritten on the next deploy — is the intended direction, the repo being the source
  of truth. Switching to the fixtures allowlist is a small change if preferred.
## [1.200.1] - 2026-07-31

### Added

- **`docs/pdf-generation.md`** — diagnosis and runbook for the broken PDF generator.

  **Both backends fail, so there is no working way to produce a PDF at all.** The default
  path (`Print Settings.pdf_generator = "chrome"`) raises `Chromium took too long to
  start.`; the manual fallback in the print view raises `No wkhtmltopdf executable found:
  "b''"` — the empty `b''` being `which wkhtmltopdf` returning nothing. Eleven logged
  failures between 2026-07-20 and 07-28. The volume is low only because the failure is
  total: people try once and stop. One of them is literally an attempt to PDF a Purchase
  Order print format.

  Root cause is the host, not the app: `infra/variables.tf` provisioned the VM with
  `["curl", "git", "nginx", "python3", "python3-pip", "python3-venv", "pipx"]` — no
  browser, no wkhtmltopdf, no fonts — and nothing in this repo installed either. The bench
  was built on a host that never had a PDF toolchain.

  Worth knowing for anyone reading the error: Frappe 16 does not shell out to a binary for
  the chrome backend. It drives a headless Chromium over the **DevTools Protocol**
  (`frappe/utils/pdf_generator/browser.py`), so "took too long to start" covers three
  different causes — no binary, a binary that cannot launch (sandbox, missing shared
  libraries, 64 MB `/dev/shm`), or one that starts too slowly under memory pressure. The
  runbook is diagnostic-first for that reason: steps 1–3 are read-only and decide which
  fix applies.

  Also documented: the two callers that have been failing **silently** rather than
  erroring, so nobody reported them — `esign/lifecycle.py` builds the signed-contract PDF
  inside a bare `try/except` that logs and returns `None`, and
  `api/maintenance_workflow.py` calls `frappe.attach_print` inside a `sendmail`, so
  customer maintenance reports have been going out without their attachment.

### Changed

- **`infra/variables.tf` and `infra/terraform.tfvars.template`** gain `chromium`,
  `wkhtmltopdf`, `fonts-liberation`, `fonts-dejavu-core` and `fontconfig`, so a rebuilt VM
  inherits a working toolchain.

  This **does not fix the running VM**, and the comment in `variables.tf` says so: the
  `apt-get` in `configs/startup_script.sh` is guarded behind `SKIP_FIRST_BOOT` and runs
  only on first boot. Two changes were needed and only one of them lives in this repo.

  Fonts are in the list deliberately. Without them a headless browser renders boxes or
  substitutes silently — producing a PDF that "succeeds" and looks wrong, which is a worse
  failure than the current one because nobody finds out.

- **Corrected the deployment target in the docs.** `docs/development.md`, `README.md` and
  `.claude/skills/release-prep/SKILL.md` all still said *"Frappe Cloud deploys from
  `main`"*. Production is a **self-hosted bench on a Google Cloud VM**
  (`production-erpnext-standard-vm`), provisioned by `infra/configs/startup_script.sh`.

  This was not a cosmetic inaccuracy. "Frappe Cloud" implies a managed host where packages
  cannot be installed — which is exactly the stated reason `stripe_payments` ships without
  the Stripe SDK. Reading the repo honestly led to the conclusion that the PDF toolchain
  *could not* be installed, when on our own VM it always could. `docs/development.md` now
  carries a note naming the stale claim, so anyone who remembers the old wording sees why
  it changed.
## [1.200.0] - 2026-07-31

### Fixed

- **The Opportunity Kanban's name field showed the wrong contact.** The card was
  configured with `contact_person`, not the Opportunity's Primary Contact.

  It was hard to spot because Opportunity carries **two** Link-to-Contact fields and
  *both were labelled "Full Name"* — so the field picker offered the same label twice
  and the board was configured with whichever one came first. `contact_person` is now
  labelled **"Contact Person"** (Property Setter fixture), which is the change that
  stops this recurring.

### Changed

- **Opportunity Kanban cards now show `primary_contact`.** `opportunity_amount` stays in
  the card field list on purpose: `opportunity_kanban_totals.js` sums it per column and
  receives it *via the board's own field list* — it is not in
  `crm_enhancements/opportunity_list.js`'s `add_fields`, so dropping it would have
  silently emptied the column totals rather than erroring.

  Shipped as a **patch, not a fixture**, because `Kanban Board` is not in the `fixtures`
  list in `hooks.py`: the board exists only as a live database record, so a UI edit would
  not survive a fresh site build and would never reach a second site at all. The patch
  swaps the one entry in place, leaves `field_name` / `filters` / `private` alone, and
  backs off with a log line if the board has since been re-arranged by hand.

### Added

- **`patches.backfill_opportunity_primary_contact`** — and without it this change would
  have made the board worse. Of 814 Opportunities on production, 154 have
  `contact_person` and only **21** have `primary_contact`; 142 have the first and not the
  second. A straight field swap would have blanked 142 cards that show a name today.

  The backfill fills those 142 (all of them — no dangling Contact links) and leaves alone
  the **five** where both fields are set and genuinely differ, e.g. `CRM-OPP-2026-00120`
  (primary *Nick Hess*, contact person *Kaia Whetman*). Where somebody drew a distinction
  between "the contact on this deal" and "the primary contact", this patch is not
  entitled to collapse it.

  Net effect measured against production: **154 cards show a name today, 163 after.** The
  swap is an improvement rather than a regression only because of this ordering, which
  `patches.txt` enforces.

  Writes with `db.set_value(..., update_modified=False)` rather than `doc.save()`, so a
  142-row backfill does not fire `on_update` — and through it `sync_from_main_doc`, the
  Drive folder hooks and the global Triton `after_save` — 142 times.

## [1.199.0] - 2026-07-31

### Fixed

- **Setting a Primary Contact or Primary Address on a Project or Opportunity silently
  re-pointed the Customer's.** Both handlers in the directory widget derived their
  account as
  `frm.doc.customer || frm.doc.supplier || frm.doc.party_name || frm.doc.name`:

  - On a **Project** that resolves to the Project's Customer. One click cleared
    `is_primary_contact` across every contact on that account and set it on the chosen
    one — a project-level decision rewriting a company-level fact, with no indication
    anything outside the Project had changed.
  - On an **Opportunity** it resolved to the incoherent pair `("Opportunity", <a
    Customer id>)`, because Opportunity's party discriminator is `opportunity_from`, not
    `party_type`. The `Dynamic Link` query behind "unset the others" matched nothing, so
    the flag was set without the previous one being cleared — which is how four
    Customers ended up with two contacts each flagged primary.

  The root cause is architectural, which is why the fix is a rule rather than a patch:
  `Contact.is_primary_contact` and `Address.is_primary_address` are columns on the
  Contact/Address **record**, not on the `Dynamic Link` row. Setting one is a statement
  about a whole account, and there is physically nowhere in that scheme to record
  "primary for *this* Project". Only Customer and Supplier are accounts in that sense.
  Every other form — Project, Opportunity, Master Project, Contact — now records its
  primary in its own doc-local `primary_contact` / `primary_address` Link field
  (`setup/custom_fields.py`) and touches nothing else. Customer, Supplier and the
  supplier pick-up address ordering in `api/pickup_routing.py` are unchanged by design.

  Note the ticket named `sync_contact.sync_from_main_doc` as the prime suspect. That
  function only ever writes the Contact's three convenience fields, has no
  Project→Customer edge at all, and was never involved; the propagation was entirely
  client-side. A test now pins that down so the diagnosis does not have to be redone.

  Visible consequence worth watching: the Project Brief's owner contact
  (`project_enhancements/doctype/project/project.py`) and ERPNext's own default-address
  pick on a Customer's transactions both read the global flag. They now reflect the
  Customer's real primary rather than whatever a Project user last clicked — correct,
  but it may be a different name than yesterday.

- **`frm.save().done(...)`** in both handlers. `frm.save()` returns a native Promise,
  which has no `.done`, so the post-save re-render and the "Primary contact updated"
  toast never ran — the action worked but appeared not to. Replaced with the documented
  `frm.save("Save", callback)` signature already used in `task_enhancements.js`.

- **The primary-contact auto-fill ran almost nowhere, and read the wrong columns where
  it did.** `primary_contact.js` binds five doctypes but was registered under the
  **Lead** entry only, and `frappe.ui.form.on` registrations are global once a file is
  parsed — so on a Project it ran only if the user happened to have opened a Lead
  earlier in the same session, and did nothing otherwise. That reads as flakiness rather
  than a bug. It also fetched `Contact.phone` / `mobile_no` while the server writes
  `custom_phone_number` / `custom_mobile_number`, so on the occasions it *did* run it
  blanked the phone. Both halves are fixed together: fixing only the registration would
  have spread the second defect to four more forms.

### Added

- **`patches.dedupe_party_primary_flags`** — clears the stray flags the Opportunity path
  left behind. On production that is four Customers with two primary contacts (AE URBIA,
  Insomniac, Kapture Vision, Michael Stone) and one with two primary addresses (Hess
  Construction LLC): **four flags cleared across five accounts**.

  It only ever *removes* a duplicate. It never invents a primary and never moves one
  where exactly one exists, because the Project path overwrote the account's previous
  value with no record anywhere of what it had been — that class is genuinely
  unrecoverable and pretending otherwise would be worse than leaving it.

  The winner is whichever record the account's own `customer_primary_contact` /
  `*_primary_address` field points at — ERPNext maintains those and `sync_contact` has
  never written them, so where one is set it is the only uncorrupted witness. Two of the
  four were decided that way; the rest fall back to the least recently modified, on the
  reasoning that the strays are what the buggy widget added on top. Writes with
  `update_modified=False`, because a real `doc.save()` would fan out through
  `sync_from_contact` into a re-save of every party naming that Contact.

  One live edge case it handles rather than mangles: Customer "Michael  Stone" has the
  *same* Contact linked twice by duplicate `Dynamic Link` rows. That is a different
  defect and emphatically not two competing primaries — clearing "the other one" would
  have unflagged the only primary there is.

- **`tests/test_sync_contact_primary.py`** — 13 bench-free tests with their own CI step.
  `sync_contact` had **zero** tests, which is how a module that writes across document
  boundaries went this long unfenced.

### Security

- `sync_contact.set_primary_contact` / `set_primary_address` were whitelisted with **no
  permission check of any kind** — any logged-in user could re-point any customer's
  primary contact. They now require write permission on the account and reject any
  `account_doctype` other than Customer or Supplier. The doctype guard is defence in
  depth behind the client fix: it makes the bug above unrepresentable rather than merely
  unwritten.
## [1.198.0] - 2026-07-31

### Added

- **Create a Purchase Receipt straight from the Procurement Tracker.** A quiet
  **Receive** action on each Purchase Order row, so a delivery can be booked from the
  screen the buyer is already looking at rather than by navigating out to the order.

  It runs ERPNext's own mapper,
  `erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt`, so
  supplier, items, warehouse and — on a partly-received order — only the outstanding
  quantity all come across. Nothing is hand-rolled.

  It lands on an **unsaved draft** and stops there. A Purchase Receipt is a stock
  transaction: submitting writes Stock Ledger and GL entries, and cancelling one
  afterwards is an accounting event rather than an undo. There is no version of this
  that submits on click.

  Shown only where receiving makes sense — submitted, not `Closed`/`Delivered`, and
  `per_received < 100`. That is the rule `api/pickup_routing.py` already settled on, and
  it is reused rather than reinvented: two different answers to "is there anything left
  to collect" on the same Project form would be worse than either alone. It is *hidden*,
  not disabled, for anyone without create permission on Purchase Receipt, because
  `frappe.new_doc` and the mapper both perform no permission check and a visible button
  would open a form that only fails at save — the same reasoning as the PO Creator gate
  on **+ Purchase Order**.

### Changed

- **The Project form's "+ Purchase Receipt" button now asks which order arrived.** It
  used to open a blank Purchase Receipt carrying nothing but the project — no supplier,
  no order, no lines — so the receiver retyped a delivery ERPNext already knew about and
  could not link it back to the order afterwards. On this site that also meant the
  receipt landed unattributed, because the `Purchase Order Item.project` cascade has no
  equivalent for a hand-built receipt.

  It now lists the project's outstanding orders and routes the chosen one through the
  same mapper. A single outstanding order skips the prompt. Where there are none it says
  so rather than falling back to the blank form — a receipt with no order behind it is
  the thing this replaced.

  New whitelisted `procurement_project.get_receivable_purchase_orders(project)` backs
  it, matching on the union of the header `Purchase Order.project` and the item-row
  `Purchase Order Item.project`. Those agree on all 70 orders here today, but only
  because `cascade_project_to_items` fills blank item rows on save — blanks only, on
  save only. An order written before that hook, or re-pointed by a path that bypasses
  it, can still carry one and not the other; that is the state the pick-up routing map
  found on 44 of 204 lines. The union costs one query and cannot be wrong.

  Note there are **no** partly-received Purchase Orders on production, so the
  outstanding-quantity path has no live example and needs a constructed case on a test
  site before sign-off.

## [1.197.0] - 2026-07-31

### Added

- **Sortable columns in the Procurement Tracker.** Click a header to sort the item table
  ascending, again for descending, a third time to clear it. The active column and
  direction are shown by an indicator, and the header carries `aria-sort` so a screen
  reader gets the same information. Keyboard-reachable: the headers are focusable and
  respond to Enter and Space.

  Sorting is client-side. The largest project on this site is 54 item rows, so there is
  nothing to paginate and no reason to make the server do it.

  Decisions worth knowing before "fixing" any of them:

  - **Blanks sort last in both directions.** A missing warehouse is not "before A" — it
    is absent information, and absent information belongs at the bottom whichever way you
    sorted. The blank comparison deliberately returns before the direction is applied.
  - **Zero is not blank.** An ordered quantity of `0` is a real value and sorts at the
    numeric bottom with the numbers. `Requested` on a direct Purchase Order line *is*
    blank — there is no request behind it — and sorts with the blanks.
  - **Status sorts by workflow order, not alphabetically.** A–Z gives *Not Received /
    Over Received / Partially Received / Received*, interleaving "done" between two "not
    done" states. Worst-first ascending means one click surfaces exactly the lines
    somebody has to chase. An unrecognised status sorts after every known one rather than
    first.
  - **Item codes sort numerically.** This site's codes are `417-080`, `417-100`,
    `2622-010`; a plain string compare puts `2622-010` in the middle.
  - **Doc Chain is not sortable**, and has no click affordance. It is one cell holding up
    to seven chain nodes; there is no single value to order by, and giving it a handler
    would mean inventing an ordering the column does not show.
  - **Sort state is per document**, keyed like the existing collapse state. Two documents
    in a group can want different sorts, and a global sort would silently reorder
    collapsed tables nobody asked about.
  - **Search filters first, then sort sorts.** Sorting is applied in a render method
    rather than inside `filteredGroups`, so the existing filter, the auto-expand watcher
    and the match highlighting are untouched.

  Two Vue traps avoided, both silent: the sort copies the array before sorting, because
  `Array.prototype.sort` mutates and mutating `doc.items` during a render is an infinite
  reactivity loop; and rows are keyed on a new server-supplied `row_id` rather than the
  array index, because index keys make Vue reuse the wrong DOM nodes once rows can
  reorder — which smears the search-highlight spans across neighbouring rows.

  Headers are rendered from the same registry the comparator reads, so a column cannot
  exist in one and not the other.

## [1.196.0] - 2026-07-31

### Added

- **Requested / Ordered / Received as real quantities in the Procurement Tracker**, per
  item line and totalled per document. Previously one column headed "Qty (Ord / Rec)"
  carried two numbers, the first of which was the *requested* quantity whenever nothing
  had been ordered — so an untouched line read as ordered-and-awaiting-delivery. Three
  separate columns, three separate facts.

  Three columns rather than one combined `4 / 4 / 0` cell for two reasons. A combined
  cell cannot be sorted without inventing hidden sort keys for one header, and column
  sorting lands next. And the ambiguity of a slash-pair is what let the original defect
  hide; a slash-triple is no clearer.

  - **Requested** prints `-`, not `0`, on a direct Purchase Order line. There is no
    request behind one, and "nobody asked" is a different fact from "asked for none" —
    the feed used to print both as zero.
  - **Ordered** carries a muted `+N draft` suffix where quantity is sitting on
    unsubmitted Purchase Orders, so the excluded amount is visible without being counted.
  - **Status** now shows the line's own receive status as a badge rather than a bare
    "N% Received" percentage; the percentage moved into the cell tooltip alongside the
    arithmetic behind it.
  - Quantities are in the stock UOM, and the cell tooltip says so — naming the line's own
    UOM when it differs. Real on this data: `MAT-MR-2026-00001` requests `PD-400-100` in
    **FT** against a stock UOM of **Unit**.

- **Per-document totals on the Material Request / Purchase Order header row** —
  `362 req · 358 ord · 0 rec`, computed server-side so the `project_procurement_status`
  MCP tool gets them too. One span rather than three, because the supplier name is the
  only element in that flex row that grows and three `nowrap` spans squeeze it to
  nothing on a narrow screen. Hidden entirely when there is nothing to total, so an RFQ
  header does not sprout a row of zeroes that reads as a failure rather than an absence.

- **`procurement_quantities.dedupe_lines`** — and it is the reason the totals are
  right. The feed's Purchase Order join matches `supplier_quotation_item OR
  material_request_item`, so a request line reachable by both paths comes back more than
  once: `MAT-MR-2026-00001`'s **ten** lines arrive as **nineteen** rows. Summing those
  directly reports **720 requested / 716 ordered** against a true **362 / 358**. Keying
  on the child row name collapses the duplicates and reproduces the child table exactly.
  Rows with no child row of their own — the supplementary sweep builds those for
  documents that never joined a chain — stay distinct and each count once.

  Three more bench-free tests cover it, including the naive-sum case, so a future
  refactor that drops the de-duplication fails rather than silently doubling.

## [1.195.0] - 2026-07-31

A fix, not a feature — but it adds a module and a CI step, so it is a MINOR bump.

### Fixed

- **Every line of a partially-ordered Material Request read "Partially Ordered" in the
  Procurement Tracker, including the lines that were fully ordered.** On
  `MAT-MR-2026-00001` (PRJ-00566) that is nine fully-ordered lines and one untouched
  one, all wearing the same badge; on `MAT-MR-2026-00003` it is twenty and two. Across
  production, 29 of 32 item rows under a partially-ordered request were mislabelled.

  Root cause is one line. `get_procurement_status` selected `mr.status` — the *parent
  request's header status* — onto a row whose grain is the **item**, so a single value
  was fetched once and painted once per line. `Material Request.per_ordered` and
  `Material Request Item.ordered_qty` were never queried by the feed at all.

  The second half of the same defect was in the colour mapper: `getStatusColorClass`
  matched on the substring `'ordered'`, which `"Partially Ordered"` and `"Ordered"`
  both contain, so the two resolved to the same CSS class. A correct status string
  alone would still have rendered identically. Same collision existed on
  `'received'`. Both vocabularies now match exactly first, and the fallback heuristic
  kept for ERPNext's own status strings tests `partial` before the generic terms.

  Item rows now carry `order_status` and `receive_status` computed from that line's own
  quantities. The request's header status is still fetched, still shown on the document
  header where it belongs, and is deliberately still visible in the item row's tooltip
  alongside the line's own figures — the two legitimately differ, and seeing them
  together is what makes it obvious the row is no longer echoing its parent.

- **A Material Request line with nothing ordered rendered as though it were fully
  ordered.** The feed substituted the requested quantity whenever no Purchase Order
  line joined (`ordered_qty if ordered_qty > 0 else mr_qty`), so an untouched line
  showed `4 / 0` under a column headed "Qty (Ord / Rec)" and read as ordered-and-
  awaiting-delivery. It now reads `0`, and the completion percentage is measured
  against what was ordered rather than against a denominator that fell back to the ask.

- **Quantities were read from one arbitrary row of a fanned-out join.** The feed's
  Purchase Order join matches `supplier_quotation_item OR material_request_item`, so a
  request line split across two Purchase Orders arrives as two rows — and `po_item.qty`
  was being read as the line's total. Likewise `COALESCE(pr_item.qty, sed.qty)` reported
  one receipt for a line received over several. Both now come from ERPNext's per-line
  rollups, which are immune to the duplication by construction.

- **Cancelled Purchase Orders still joined the Material Request chain.** Part 2 of the
  query has always filtered them; Part 1 never did. Latent only because this site has no
  cancelled Purchase Orders yet.

### Changed

- **`ordered_qty` counts only submitted Purchase Orders.** Ten Purchase Order Item rows
  against draft orders are linked to Material Request lines on production today. They
  inflated the tracker while ERPNext's own `ordered_qty` excluded them — so the tracker
  and the Material Request form it links to disagreed. Draft quantity is still reported,
  as `draft_ordered_qty`, and surfaces in the item row's tooltip.

  This is a genuine semantic change and figures will *drop* for anyone with draft orders
  outstanding. On PRJ-00566, 14 of 61 rows change; on PRJ-00567 (54 rows, the largest
  project) nothing changes at all.

- **`project_procurement_status` (MCP tool)** gains `total_requested_qty` and reports the
  per-line statuses. Its `total_ordered_qty` was accidentally carrying the *requested*
  figure whenever nothing had been ordered, so a project with untouched lines looked
  fully ordered to an assistant. The tool description was updated in the same change —
  a stale description is what an assistant actually reads.

### Added

- **`erpnext_enhancements/procurement_quantities.py`** — one place where "how much was
  asked for, how much is on order, how much arrived" is decided. The tracker asked that
  question at two levels and answered it two different ways; that divergence *is* the
  bug, and the next two tracker changes both need the same arithmetic.

  It reads ERPNext's denormalized rollups rather than recomputing. They are per-line, so
  the join fan-out cannot inflate them; they already net out amendments, cancellations
  and returns — three cases with almost no live examples here, which is exactly where a
  hand-rolled `SUM` would be wrong and nobody would notice; and they are the same numbers
  the Material Request form, the MR list status and `po_creation_guard.js` already show.
  Verified against `SUM(Purchase Order Item.stock_qty)` over every MR-linked submitted
  Purchase Order line on production: zero discrepancies.

  Stock UOM is the basis on the request axis and transaction UOM on the Purchase Order
  axis, because `status_updater` maintains each against a different field. Every line on
  this site currently has `conversion_factor = 1`, so the two are indistinguishable in
  live data — which is precisely why the choice is written down rather than discovered
  later by a 120 FT line reading as 12000% ordered.

  Nothing is clamped: over-ordering and over-receipt get their own statuses and exceed
  100%. The defect being fixed was a number quietly substituted to make a line look
  complete.

  It lives at the app root beside `procurement_project` / `po_approval` /
  `po_segregation` rather than under `project_enhancements/`, because that package
  imports `frappe` and a submodule of it cannot be imported bench-free however pure it
  is. Same invariant as `water_engineering/engine`, reached from the other direction.

- **`tests/test_procurement_quantities.py`** — 18 bench-free pytest tests with their own
  CI step. Zero lines on this site are genuinely part-ordered, so the item-level
  "Partially Ordered" state has no live example and is covered synthetically; without
  that, the state most central to the bug would ship untested.

- Two CSS classes the tracker never had: `.status-partial` (something has happened, but
  not all of it — distinct from `.status-pending`, which means nothing has) and
  `.status-warning` for the over-ordered / over-received anomalies. Both with dark
  variants, because the tracker's `th` and badge rules hard-code light-mode colours and a
  new class written only for light mode is illegible in dark.

## [1.194.1] - 2026-07-31

### Changed

- **OAuth Bearer Token retention raised from 30 days to 90** (`patches/set_oauth_token_retention.py`).
  Frappe's log-clearing job treats `OAuth Bearer Token` as a log doctype, but the row *is*
  the refresh token: Frappe mints a new row on every refresh and never revokes the old
  ones, so a stored refresh token stays usable only while its row survives. At 30 days,
  anyone who left the integration alone for a month came back to a dead grant.

  The failure was worth documenting because it does not look like an expiry. Triton's
  refresh POST is unauthenticated by design (client credentials in the body), so it runs as
  `Guest`; when `validate_refresh_token` finds no Active row, Frappe's
  `check_doctype_permission` rewrites the resulting `DoesNotExistError` into a
  `PermissionError` — anti-enumeration hardening — and the client sees
  `403 {"exc_type":"PermissionError", … "User <strong>Guest</strong> does not have doctype
  access … <strong>OAuth Bearer Token</strong>"}` instead of OAuth's `400 invalid_grant`.
  Nothing in this app is involved, and no permission change fixes it.

  The client-side half (refresh ahead of expiry, and a relink prompt when a grant really
  is dead) ships in Triton 0.38.0 (sapphirefountains/triton#272). The patch never lowers
  a retention that has been raised deliberately, and appends the row on sites where Log
  Settings has not seeded it.

## [1.194.0] - 2026-07-31

### Added

- **Branding on generated contracts.** Every agreement the contract generator produces —
  all eight types — now opens with the Sapphire Fountains wordmark over a navy rule, sets
  its numbered sections in sans-serif navy against a serif body, bands the label column of
  its data tables, and carries a running footer with the contract number and page numbers.
  New module `project_enhancements/contract_style.py` builds that chrome; the
  `Project Contract Print` fixture's CSS is the stylesheet.

  Two constraints shaped the implementation, and both are worth knowing before changing it.

  **The chrome is emitted by the wrapper, never by the templates.** A contract's legal text
  lives in the site-editable `Contract Template` record, not in `templates/contracts/*.html`
  (`seed_contract_templates.py` is insert-only, so editing the repo file changes nothing on
  a live site), and a *signed* contract prints the frozen `agreement_html` snapshot from its
  Contract Signature Request. Chrome placed inside either one would have been unreachable —
  the first without a data patch, the second forever. Placed in the wrapper it needs no
  patch, and it appears on contracts that were signed before it existed, without altering a
  word of what they say.

  **The document title is styled by position, not by tag.** The eight templates disagree:
  `<h3>` opens the document in six of them but is a mid-document section heading in the
  employee-contractor agreement and the NDA, and the architect agreement and NDA have no
  title block at all. So the stylesheet targets "the `<h3>` immediately after the
  letterhead" and the run of `<p>` after that. It is inert everywhere it should be.

  The logo goes in as **inline `<svg>`**, not `<img src>`, because wkhtmltopdf renders the
  latter unreliably; and the footer carries **inline** styles because
  `frappe.utils.pdf` lifts `#footer-html` out of the document and renders it as a separate
  wkhtmltopdf input, where the print format's stylesheet may not reach. The page numbers
  come from frappe's own `pdf_header_footer.html`, which fills elements classed `page` and
  `topage` from wkhtmltopdf's query string — which is why those two class names are asserted
  in tests rather than left to be discovered when a footer silently prints "Page  of ".

### Fixed

- **The contract looked like three different documents depending on where you read it.**
  The print styling had been hand-copied into three places and had drifted: the
  `Project Contract Print` fixture (Georgia 10.5pt, `#444`/`#000` borders), `_print_wrapper`
  in `project_enhancements/esign/lifecycle.py` (Times New Roman 11pt, `#999`/`#333`) and
  `public/css/contract_sign/contract_sign.css` (Times New Roman again). Those are, in order,
  the document staff print, **the executed PDF emailed to the customer after signing**, and
  **the page the customer reads while deciding whether to sign** — the two nobody compares
  against the desk view. Both now read the one stylesheet on the Print Format record, which
  `_contract_css()` was already serving to the on-screen viewer for exactly this reason.

### Security

- The public signing page now embeds the contract stylesheet in a `<style>` element, so
  `www/contract_sign.py` strips `<` from it first (`_safe_css`). The desk viewer publishes
  the same CSS through `style.textContent`, where markup cannot escape; on an
  unauthenticated page a `</style>` in a site-edited Print Format would have ended the
  element and let everything after it parse as content.

## [1.194.2] - 2026-07-31

### Added

- **`docs/procurement-tracker-map.md`** — a code map for the Project form's **Procurement
  Tracker**, the collapsible procurement tree on the Budget tab. It had no documentation
  anywhere: no section in the Project Enhancements README, no CHANGELOG entry of its own
  (it landed in `a8021db`, before the per-module README pass and before this changelog
  became a discipline), and only a single table row in the public README. Four queued
  changes all land in the same two files, so the groundwork is written down once.

  What it records, beyond the file map:

  - **The name collision.** ERPNext ships a *standard* Script Report called "Procurement
    Tracker" (module Buying, `ref_doctype` Purchase Order). It is unrelated and not in this
    repo. The thing on the Project form is an in-house Vue 3 widget, and the field it mounts
    into is labelled "Material Request Feed" — a misnomer, it renders all six procurement
    doctypes.
  - **There is no table library.** It is Vue 3 with an inline template string, not a frappe
    DataTable, so sorting and per-row actions are hand-written work rather than configuration.
  - **The `OR`-join fan-out** at `project_enhancements/__init__.py:69-72`: one Material
    Request line split across two Purchase Orders produces two rows for that one line. Nothing
    de-duplicates today because nothing aggregates today — but any future per-item arithmetic
    has to, or it double-counts.
  - **Three different things on screen are called "status"**, from three different sources —
    and the item-row Doc Chain badge reads the *parent* Material Request's header status
    (`mr.status`, `:36`) on a child-grain row, so every line of a partially-ordered MR reads
    "Partially Ordered" whether or not that particular line is fully ordered. Reproduction on
    `MAT-MR-2026-00001` / `PRJ-00566`. `Material Request.per_ordered` and
    `Material Request Item.ordered_qty` are never queried by the feed at all.
  - **The return shape is a public contract.** `assistant_tools/project_procurement_status.py`
    consumes both endpoints, so renaming `ordered_qty` / `received_qty` breaks the MCP tool
    silently.
  - **Production volumes**, so nobody reaches for pagination: the largest project is 54
    Purchase Order Item rows. Also that *no* `Material Request Item` row has `project` set —
    Material Requests reach the feed only via `Material Request.custom_project` — and that
    `Material Request Item.ordered_qty` agreed with the child tables on all 160 submitted rows
    checked, so quantity work can trust the denormalized fields.
  - Gotchas worth the reading: the Vue app is never unmounted (every form refresh orphans the
    previous instance with its watchers live, onto a hard-coded document-global element id),
    `v-html` renders item codes and supplier names unescaped, both endpoints are whitelisted
    with **no** permission check, and `_supplementary_documents` swallows every exception so a
    failing doctype silently vanishes from the feed instead of erroring.

### Changed

- **`project_enhancements/README.md`** gains the Procurement Tracker section it never had, and
  `docs/README.md` indexes the new map. Documentation only — no executable behaviour changed.

## [1.193.1] - 2026-07-29

### Added

- **`CLAUDE.md`** — the repo had none, which meant every AI contributor rediscovered the
  same expensive facts from scratch: that indentation is mixed and must be matched
  per-file, that `ruff check` is advisory because of a known backlog, that bench-free
  suites split between `unittest` and `pytest` and `python -m unittest` silently collects
  *nothing* from a pytest-style suite, that removing a fixture record does not delete it
  from the database, that a `www/` controller with a hyphen in its filename is never
  imported by Frappe, and that `stripe_payments` ships without the Stripe SDK on purpose.

  Written to Anthropic's [new rules of context engineering for Claude 5 generation
  models](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models):
  short, spent on gotchas rather than on facts inferable from the file tree, with
  procedures pushed into skills that load only when relevant.

- **`.claude/skills/`** — six skills covering the procedures that used to live only in
  people's heads: `add-doctype` (including the module-placement test), `add-endpoint`
  (permission and validation conventions, plus the AI-tool rules), `run-tests` (which suite
  runs where, and the unittest/pytest collection trap), `fixtures-and-patches` (which of the
  three version-control mechanisms to use, the two-step deletion, the dormant-Check trap),
  `release-prep`, and `work-item` (the WI lifecycle and the native-first rule).

- **`decisions/adr/`** — eight architecture decision records, alongside the existing `OD-n`
  *business* register in `decisions/OPEN-DECISIONS.md` and explicitly distinguished from it.
  They capture reasoning that was real but scattered across `pyproject.toml` comments,
  `ci.yml` comments, `patches/README.md` table rows and the CHANGELOG: native-first, the
  repo as source of truth for customizations, no vendor SDKs, bench-free-only CI, desk-only
  AI write confirmation, tolerating mixed indentation, and bundle-only global assets.

- **READMEs for the 19 module directories that had none**, following the house style
  `api/README.md` set. The largest gaps were `water_engineering` (6.5k LOC across 64 files),
  `stripe_payments` (3.7k), `product_configurator` (2.5k), `kpi_dashboards`,
  `accounting_intake`, `setup` and `plaid_banking`. Each maps the module's files and
  DocTypes and records the decisions a reader would otherwise mistake for oversights — that
  `water_engineering/engine/` may never import `frappe`, that `product_configurator`'s
  condition evaluator is an AST whitelist because users author the expressions, that
  `erp_integration.py` contains no `frappe.db.commit()` on purpose, that Plaid's
  `plaid_auth_blocked` flag exists to prevent retry storms.

- **`docs/README.md`** and **`docs/development.md`** — a documentation index, and a
  development guide covering bench setup, the three ways tests run, the lint posture, the
  version gate, where a change goes, and the deploy path.

- A module docstring for **`hooks.py`**, which carried extensive inline annotation but no
  statement of what the file is or why its comments are load-bearing.

### Fixed

- **Broken and stale links in `README.md`.** The module map advertised "eight Frappe
  modules" and listed two that no longer exist under those names — `global_enhancements`
  (folded into AI Governance and Enhancements Core) and `quickbooks_time_integration`
  (split into `quickbooks_online` and `quickbooks_time`) — so three README links 404'd, as
  did the root `Custom HTML Block/` entry after that directory moved into the package as
  `custom_html_blocks/`. `modules.txt` actually registers 26 modules; the map now lists them
  all. `enhancements_core/README.md` pointed at the removed `global_enhancements` README for
  the Triton Assistant Settings cross-reference and now points at AI Governance.

- **The "Running tests" section understated what CI covers.** It described exactly two
  bench-free suites; there are now many, split across `unittest` and `pytest` steps. It now
  states the collection trap that silently disabled the QuickBooks suite and points at
  `ci.yml` as the authoritative list.

## [1.193.0] - 2026-07-28

### Changed

- **Only five people can now raise a Purchase Order** (WI-066, subtractive half).
  `Purchase User` and `Purchase Manager` lose create / write / submit / cancel / amend /
  delete on Purchase Order; `PO Creator` is the only role that carries them. Before this
  release **18 enabled users could create and submit a PO**, because `Purchase User` held
  those bits and four Role Profiles hand out `Purchase User`. Five people actually buy.

  This is the half that closes the control. It ships **after** the additive release
  (v1.191.0) and after the five grantees were confirmed to hold `PO Creator` in
  production — deliberately, because role assignment is a manual step and shipping both
  halves together would have left a window in which nobody but `Administrator` could
  raise a Purchase Order.

  The two rows are edited **in place**, not deleted. Removing a record from a fixture
  file stops managing it but does **not** remove it from the database — fixture sync only
  creates and updates. A deletion would have read in git as though the control shipped
  while changing nothing on production, which is the worst possible failure mode for a
  permission change.

  `Purchase User` and `Purchase Manager` keep **read / report / print / email**. That is
  not leniency: the Procurement and Executive Summary dashboards evaluate under the
  viewing user's permissions, so dropping `read` would blank those charts for eleven
  people, and a PM still needs to print a committed PO or email it to a supplier.
  Reading and transmitting a purchase order is not committing one.

  <what moves with it: ERPNext gates the PO **Close / Hold / Re-open** buttons on
  `submit` permission (`update_status`, purchase_order.py), not `write` — so closing a PO
  out is now a `PO Creator` action too. Lisa (AP) and Parker (the main purchaser) both
  hold the role, so the people who actually do it still can.>

  Request for Quotation and Supplier Quotation are untouched and stay on standard
  permissions, so the eleven people losing PO creation can still raise an RFQ and shop
  the market — they just cannot commit the money.

## [1.192.0] - 2026-07-28

### Added

- **Version-controls the `PO Approvers` role profile** (WI-066 follow-up). WI-066 gave
  Lisa Symanski the `PO Approver` role so a Purchase Order consolidating requests from
  the only two existing approvers could not become unsubmittable by anyone but
  `Administrator`. Because she carries a role profile, Frappe rebuilds her roles from the
  union of her profiles on every save — a direct grant would not have survived — so the
  role had to reach her through a profile.

  The plan was to add `PO Approver` to the departmental **`Finance Team`** profile, which
  has exactly one member. That plan was wrong and is not what shipped. A departmental
  profile carrying an approval authority means **every future finance hire silently gains
  the power to approve POs over the threshold**, with nobody deciding it — which is
  precisely how `Purchase User` grew to sixteen holders and made WI-066 necessary in the
  first place. An authority now gets its own single-role profile, handed to named people:
  `PO Approvers` alongside `PO Creators`.

  The profile was created by hand during the production apply, so this commit is what
  stops it being unversioned config — without it a fresh site would never get it and a
  fixture re-export would not capture it, the exact gap WI-010 exists to close. Fixture
  sync is an upsert, so it adopts the existing record rather than duplicating it.

## [1.191.0] - 2026-07-28

### Added

- **Purchase Order creation now belongs to a dedicated `PO Creator` role** (WI-066).
  WI-012 shipped the Material Request → Purchase Order split and, on its face, reserved
  PO creation for purchasing. It did not: it granted create/write/submit to
  **`Purchase User`**, and four Role Profiles carry that role — Production Team, Design
  Team, Finance Team and Sales Team. The effective permission set let **sixteen enabled
  users commit company money.** Five people actually buy (Parker 74 POs, James 24,
  Nikolas 23, Daniel 3, Clegg 2), so the fix costs almost nothing operationally and
  closes a control that until now existed only on paper.

  This release is the **additive half** and takes nothing away. It seeds the role, adds
  the `PO Creator` permission rows, and leaves `Purchase User` / `Purchase Manager`
  untouched. The subtractive half ships separately, *after* the five people actually
  hold the role — because role assignment is a manual Desk step, and doing both at once
  would leave a window in which nobody but `Administrator` could raise a Purchase Order.

  Two mechanics are load-bearing and easy to get wrong. **The role is seeded by a patch,
  not a fixture:** fixture files import in *alphabetical filename order* — not the order
  of the `hooks.py` list, which governs export only — so `custom_docperm.json` lands
  before `role.json`, and a permission row would name a Role that does not exist yet.
  **And a user who carries a Role Profile cannot hold a direct role at all:**
  `User.validate` rebuilds `roles` from the union of their profiles on *every* save, and
  the Desk disables the Roles grid outright. So Clegg and Lisa get the role through a
  new single-role **`PO Creators`** profile added alongside their existing one (Frappe
  unions multiple profiles, already in live use here), while James, Nikolas and Parker —
  who have no profile — take it directly. Giving *them* a profile would have wiped
  `System Manager` and `PO Approver`.

- **Whoever raised the request can no longer be the one who buys it** — a separation-of-
  duties gate on Purchase Order submit (`po_segregation.py`). Of 127 Purchase Orders only
  7 carry a Material Request link, and in **all 7 the PO owner was the MR owner**: every
  requisition ever converted on this site was self-approved.

  **No role clears this** — not `Purchase Manager`, not `PO Approver`, not the CEO; only
  `Administrator`, which is break-glass and also voids the WI-013 threshold, so it should
  be a named-account decision rather than a habit. It runs *ahead* of the threshold gate
  <why: leading with "only a PO Approver can submit it" would imply self-submission
  becomes possible at some amount, and reads as flatly wrong when the CEO is himself the
  requester>. The identity checked is whoever presses Submit, not the drafter, so
  handing a draft to a colleague is the intended path and amended POs work with no
  special case. A dangling Material Request link fails **open** — a deleted MR can never
  brick a Purchase Order. Killable without a deploy: *Settings → Purchasing Controls →
  Enforce PO Separation of Duties*.

  Scope is deliberately narrow and worth stating plainly: the gate only fires on POs
  whose lines carry a Material Request link, which is 7 of 127 today. It is traceability
  hygiene, not a spend control — and it creates a mild perverse incentive, since linking
  an MR is now what makes a PO *harder* to submit. The work item carries a 90-day
  re-measure for exactly that reason.

- **Anyone employed here can now raise a Material Request.** `Employee Self Service`
  gains create/write/submit on Material Request and joins the `HR` role profile. Kendalyn
  Harris held none of the five roles with MR access and literally could not file a
  request — which contradicted the rule the whole purchasing flow rests on. Deliberately
  not granted to role `All`, which includes Guest and Website Users and would have
  exposed request creation to the portal.

- **Lisa Symanski is now a third `PO Approver`.** Above the threshold only an approver may
  submit, and the new SoD gate does not exempt them — so a PO built from one approver's
  own request could only be submitted by the other, and a PO consolidating requests from
  both by nobody but `Administrator`. 37 POs a year worth $221,685 (97% of PO spend) sat
  behind two people's availability.

### Changed

- **The optional supplier-quoting path is documented for the first time.** Nothing in the
  migration design covered Request for Quotation, Supplier Quotation or competitive
  bidding — while the seeded "Buying and Procurement" diagram told staff that quoting was
  mandatory *and the only route to a PO*, contradicting the purchasing SOP, which said
  Material Request → PO with nothing in between. Both seeded diagrams are corrected and
  the SOP now describes RFQ → Supplier Quotation as a real, encouraged, **ungated** step.
  No dollar amount requires a quote and skipping one blocks nothing.

  Request for Quotation and Supplier Quotation keep **standard** ERPNext permissions —
  deliberately no Custom DocPerm rows <why: one row flips a doctype to fully-overridden
  permanently, and RFQ has a supplier *portal* flow that would then break silently>. So
  the eleven people losing PO creation can still shop the market; they just cannot commit.

- The "Create → Purchase Order" affordances now disappear for users who cannot act on
  them. ERPNext's own button on Material Request carries no permission check and fails
  late, in a red dialog; worse, the Project form's **+ Purchase Order** called
  `frappe.new_doc`, which opens a *fully editable* form and only fails at save — a user
  could type a dozen line items and lose all of them.

- `docs/migration/wi011-apply-runbook.md` claimed this site has no multi role-profile
  support. It does, four users already have two, and WI-066 depends on it.

## [1.190.0] - 2026-07-28

### Added

- **The Project Budget tab can now plan the material pick-up run.** A job's material sits
  at several vendors' will-call counters at once, and nothing in Desk answered the two
  questions a driver actually has: what is still out there, and what is the shortest way
  round to collect it. The Budget tab's Procurement block could *create* six kinds of
  purchasing document and tell you nothing about fetching what they bought.

  A new **Material Pickup** section carries one button, **Pick Routing Map**. It opens an
  extra-large dialog — ordered stop list on the left, Google map on the right — with every
  supplier on the job that still owes material, in drive-time order out of the shop
  (85 W 300 S, Bountiful) and back. Tick stops off the run, finish at the shop, the job
  site or a typed address, and hand the whole thing to Google Maps on the phone.

  The optimisation is Google's `DirectionsService` with `optimizeWaypoints: true`, run in
  the browser against plain address strings, so **nothing is geocoded, cached or stored
  server-side** and the feature needs no Google credentials of its own — it reuses the
  referrer-restricted browser key already in Travel Settings. That key now needs the
  **Directions API** enabled on it as well as Maps JavaScript.

  New `api/pickup_routing.py::get_pickup_route_data` builds the payload, gated on read
  access to the Project. Purchase Orders are the union of the header `project` and the
  item-row `project` — on prod those two disagree (POs exist with a blank header but a
  filled line, and the reverse), so querying either alone silently drops stops. "Still to
  collect" is `docstatus = 1`, `status` not `Closed`/`Delivered`, and `per_received < 100`:
  the number, not the label, because `Closed` is the one status that can hide a PO whose
  goods never arrived. Two other scopes — every submitted PO, and drafts too — are one
  dropdown away.

  Settings gains `pickup_route_start_address` under Purchasing Controls (blank falls back
  to the shop), seeded on existing sites by `patches.add_project_pick_routing_button` —
  the usual Single trap, where a field default only applies at creation and the record
  already exists everywhere.

### Notes

- **Where a stop actually is takes four tries, and the answer is shown.** Only a minority
  of Purchase Orders carry a `supplier_address` at all, and on prod 10 of the 21 suppliers
  with `supplier_primary_address` set have no `Dynamic Link` row on that Address — so
  neither lookup path alone is sufficient. The chain is `po.dispatch_address` →
  `po.supplier_address` → `Supplier.supplier_primary_address` → the Address directory
  (preferring a `Shipping`/`Warehouse`/`Shop`/`Plant` address type over Billing). Which
  step won comes back as `address_source`.

  `po.shipping_address` is **deliberately not in that chain**: on this site it is our own
  yard on essentially every PO, and routing to it would send the truck home between every
  stop. A supplier that resolves to nothing is still listed — under "No address on file",
  linked to the vendor record — rather than dropped, because a pick list that quietly
  omits a stop is worse than one that admits it cannot place it.

- **Three degradation steps, each still usable.** Optimised route with per-leg distance and
  time; then, when the key loads Maps but lacks the Directions API, geocoded pins in
  purchase-order order with a banner saying so; then, with no key at all, an ordered list
  of Google Maps links. "Open in Google Maps" works at every step. Google's own ceilings
  are surfaced rather than silently applied — stops past the 23-waypoint optimisation limit
  are listed under their own heading, and the 9-stop cap on a Maps URL raises an alert on
  click instead of quietly truncating the link.

## [1.189.0] - 2026-07-27

### Added

- **The Triton widget can now pick — and author — AI personas.** Triton gained named,
  switchable system-prompt profiles (sapphirefountains/triton#267); this is the ERPNext
  half, so someone who lives in Desk all day never has to open the Triton site to use or
  create one.

  Personas are stored **in Triton, not here** — no new DocType, no `hooks.py` change, no
  fixtures. The identity bridge already exchanges the gateway secret for a per-user Triton
  JWT, so an ERPNext user resolves to the *same* Triton `User` the web app uses; a persona
  created in either place shows up in the other with no sync to build.

  `triton_chat.py` gains whitelisted pass-throughs — `list_personas`, `create_persona`,
  `update_persona`, `delete_persona`, `duplicate_persona`, `set_default_persona` — and
  `start_session` / `stream_query` now forward `persona_key`. `stream_query` previously
  forwarded only `prompt`, `hidden` and `model_name`, so a persona was literally
  unreachable from ERPNext until this change.

  The widget gets a persona `<select>` beside the model picker (`optgroup`-grouped Built
  in / Yours / Shared, plus a `⚙ Manage…` sentinel), a slide-over manage panel reusing the
  history panel's classes, and a `frappe.ui.Dialog` create/edit form. The pick persists in
  `localStorage` under `triton_persona_key`, mirroring `triton_model`, and survives
  `newChat()` — it is a preference, not session state.

### Security

- **`list_personas` caches under a per-user key** (`triton_personas::{user}`, 60s TTL,
  invalidated on every mutation). This deliberately departs from `list_models`, which uses
  a single site-wide `triton_models_list` key — that is safe only because the model list is
  identical for everyone. A persona list is not: it contains the caller's own **private**
  personas, so reusing the site-wide pattern would have served one user's personas to the
  whole site. `tests/test_triton_personas.py` asserts a second user misses the first user's
  cache.

## [1.188.0] - 2026-07-27

### Added

- **The Project Schedule tab's Gantt can now follow the task order set on the Scope tab.**
  The chart was hardwired to oldest start date first, so the sequence a project manager
  had already worked out by dragging the Scope tab's Tasks Tree into the order the job
  actually runs in was visible only on that tab — the Gantt re-sorted it back to dates.

  A **Sort** picker in the Gantt toolbar now offers two views:
  - **Start Date** — the existing behaviour, and still the default.
  - **Scope Order** — `Task.custom_subtask_order`, the field the Scope tab's tree already
    writes when a task is dropped. The chart's own `parent_task` nesting supplies the
    levels, so the bars come out in the tree's row order. Tasks never dragged share
    order 0, so start date is the tie-breaker: they group above the ranked rows, oldest
    first.

  The choice is view-only — nothing about it writes task order, and the sort is not
  persisted on the Project. It is remembered per user and per project in `localStorage`,
  so reopening that project's Schedule tab restores the last view used there while other
  projects and other users are unaffected. Reordering the Scope tree already publishes
  `project_dashboard_updated`, which the Gantt listens to, so the chart re-sorts live
  without a reload.

  Scoped to the Project form. The Projects Dashboard Gantt is untouched.

### Changed

- `api/gantt.py` `_sanitize_order_by` accepts comma-separated sort keys (capped at
  `MAX_ORDER_BY_KEYS = 3`) instead of exactly one. Each key still faces the same
  validation as before — the fieldname must exist on the doctype's meta and the
  direction must be `asc`/`desc` — so nothing is interpolated unvalidated; the cap keeps
  a client-supplied config from asking the database for an unbounded sort. Needed
  because a manual-rank column ties on every unranked row, and without a tie-breaker the
  same chart comes back in a different order on each refresh.

- The Gantt widget gained a reusable `toolbar.sort` control (`options[].order_by`,
  `selected`, `on_change`) plus `set_sort()` / `get_sort()`. The active option's
  `order_by` overrides `config.order_by` on the next fetch. Any embed can use it; only
  the Project form does today.

## [1.187.0] - 2026-07-27

### Added

- **Buying office supplies no longer dead-ends at a mandatory Project.** WI-014 made
  `Purchase Order Item.project` mandatory so material and subcontract cost lands on the
  job — but it shipped without the one thing that requirement assumed: somewhere for
  non-job spend to go. The 13 `Internal` projects are all specific R&D jobs
  (IDP000–IDP016), so a purchaser buying printer paper had nothing legitimate to pick
  and was blocked at save. The WI-014 migration note flagged this as an unmet
  precondition; this closes it.

  New `patches.seed_overhead_projects` creates an **`Overhead` Project Type** and five
  standing buckets — Office & Admin, Shop & Warehouse, Fleet & Vehicles, IT & Software,
  Marketing & Trade Shows. Their own Project Type rather than `Internal`, so overhead
  stays separable from R&D in reporting and the Projects Dashboard keeps excluding them
  (it lists only Build/Design/Events/Service/Delivery). Insert-only and matched on
  `project_name`, so a later rename survives re-migration.

### Fixed

- **A Purchase Order that names its job on the header no longer refuses to save.**
  ERPNext never pushes `Purchase Order.project` down to `Purchase Order Item.project`,
  which the mandatory flag turned from a cosmetic gap into a hard block: on prod, **44 of
  the 204** lines sitting under a PO that *did* have a header project were still blank.
  The purchaser had to re-pick the same project on every row.

  New `public/js/purchase_order_project.js` fills blank line projects from the header —
  on header change, on row add, and once more on `validate` for rows pulled in by "Get
  Items From", which bypasses the row-add event. The client copy is the one that matters
  in the Desk, because the mandatory check runs in the browser before the request is
  sent; `procurement_project.cascade_project_to_items` (`before_validate`) repeats it for
  the REST API, data import and Material-Request-mapped documents.

  **Blank rows only** — a PO legitimately spanning two jobs keeps its per-line
  attribution. A draft PO with no project at all now shows a dashboard hint naming the
  five overhead buckets, so the non-job case is signposted instead of being a dead end.

## [1.186.0] - 2026-07-27

### Added

- **Credit cards can now be surcharged on one-off portal payments, compliantly.**
  v1.185.0 made hosted Checkout fee-free because it fixes line items when the Session
  is created — before the payer's card, and so its funding type, exists. New
  `stripe_payments/core/card_element.py` and the `/pay-card` page close that gap using
  Stripe's **ConfirmationToken** two-step confirmation, which is entirely GA API: no
  preview version header, and no third-party surcharge app (Yeeld/InterPayments).

  A Payment Element mounts in deferred-intent mode, so nothing is priced until the
  payer submits. Stripe.js then returns a ConfirmationToken whose
  `payment_method_preview.card.funding` the server reads **before any amount is
  committed**, prices the surcharge through the same `_compute_surcharge` gate every
  other path uses, and shows the payer the true total. The fee line appears only when
  a fee genuinely applies; a debit or prepaid payer is told plainly that none does,
  and can go back and use another method — the disclosure and opt-out the card
  networks require.

  The quote is **bound to the ConfirmationToken it was priced against**, and
  confirming accepts only that token. Without this a client could take a quote on one
  card and pay with another, which is exactly the debit-surcharge case being
  prevented. Replay of an already-Paid or Processing row is rejected, and the
  PaymentIntent is created under an idempotency key derived from the ledger row, so a
  double-click cannot produce two charges.

  Reconciliation is untouched: the PaymentIntent carries the usual metadata and
  `payment_intent.succeeded` posts the Payment Entry and companion surcharge Journal
  Entry exactly as before.

### Changed

- **The portal offers Card and Bank as separate routes**, because they now run on
  genuinely different implementations rather than at different prices. Cards go to the
  Element page; bank payments stay on hosted Checkout. Neither button quotes a fee —
  for cards it isn't knowable at that point, and ACH never has one.
- **ACH deliberately stays on hosted Checkout.** A bank debit has no funding type to
  detect and never carries a fee, so the Payment Element would buy it nothing while
  adding Financial Connections (with its own per-account pricing), the microdeposit
  fallback and its 10-day verification window, and Nacha mandate collection at
  confirmation — all of which hosted Checkout already handles correctly.
- `Stripe Payment.confirmation_token` records which card a quote was priced against.

### Known gap

- **Emailed payment links and the desk "Pay" button remain fee-free.** Both create a
  hosted Checkout Session, which cannot price a surcharge, and the Element page
  requires a logged-in portal session. Surcharging those would need a tokenized guest
  route to `/pay-card` — an unauthenticated payment page, which deserves its own
  review rather than being folded in here.

## [1.185.0] - 2026-07-27

### Fixed

- **Debit, prepaid and ACH payments can no longer be surcharged.** Card-network rules
  ban surcharging debit and prepaid cards outright — there is no cost-of-acceptance
  exception, unlike credit surcharges, which are merely capped. `_compute_surcharge`
  keyed off the *chosen method* (`"card"`), but Stripe's `"card"` covers credit, debit
  and prepaid alike, and the real funding type only arrives afterwards as
  `charge.payment_method_details.card.funding`. A debit customer was therefore shown a
  "Card processing fee" line on the Checkout page and charged it. Production was Live
  with `surcharge_enabled=1` at 2.9% when this was found; no payment had gone through
  it yet (0 `Stripe Payment` rows), and it was switched off the same day.

  The gate now returns a fee for exactly one input combination — `pm_type == "card"`
  **and** `funding == "credit"`. `debit`, `prepaid`, `unknown` and not-yet-known all
  return zero, as does every non-card method. This is structural: no settings value
  can override it.

- **`ach_fee_percent` / `ach_fee_flat` removed** rather than set to zero. Fields the
  code refuses to honour invite someone to set one, see no fee, and file a bug. ACH
  convenience fees are lawful (ACH is outside card-network rules); we simply don't
  charge one, and re-adding the capability should be a deliberate change.

- **Hosted Checkout can no longer price a surcharge at all.** It fixes line items when
  the Session is created, which is before the payer's card — and therefore its funding
  type — exists, so any fee there is a guess. It now passes `funding=None` through the
  same gate and always gets zero. The method-first "Choose payment method" dialog and
  the `(+2.9% fee)` button labels are gone with it: they existed only to disclose a fee
  that can no longer be quoted up front.

- **The surcharge cap is now cost-aware.** `_validate_surcharge` enforced only the 3%
  network ceiling, but against a cost of acceptance of 2.9% + $0.30 a 3% surcharge
  exceeds cost on any invoice over ~$300 — a violation everywhere. New
  `cost_of_acceptance_percent` / `_flat` settings (2.9 / 0.30) are enforced
  componentwise: percent ≤ cost percent **and** flat ≤ cost flat, which is provably
  within cost at every invoice total.

### Added

- **Off-session charges are now surcharged correctly.** `charge_saved_method` (autopay
  and dunning) previously applied no surcharge at all. It now reads the saved
  PaymentMethod before charging — the one path with no timing problem — and prices a
  credit-card fee up front. Debit, prepaid and bank accounts get none.
- **`Stripe Payment.card_funding`** records what the card actually turned out to be, so
  the surcharge decision is auditable after the fact. It rides on the charge the
  reconciler already fetches, so it costs no extra API round-trip.
- **Surcharge void backstop.** If a booked fee meets positively-known non-credit
  funding at reconcile time, the fee is refunded, the income Journal Entry is skipped,
  and Accounts is alerted (`surcharge_voided` / `surcharge_refund_id`). Idempotent on
  the persisted refund id — Stripe's own Idempotency-Keys expire after 24 hours, so a
  webhook redelivered a day later would otherwise refund twice. Funding that we simply
  *could not determine* (a failed lookup) alerts instead of refunding, so a Stripe blip
  never claws back a legitimate credit surcharge.
- **Conditional disclosure.** The surcharge text no longer claims "a processing fee
  applies to card payments" to a debit customer. `autopay_consent_text` appends a
  surcharge sentence only while surcharging is on, derives the rate from settings, and
  is the same text shown on the Stripe page, rendered in the portal, and stored as
  proof of authorization — autopay enrolment being where an off-session card fee has to
  be disclosed, since the customer isn't present when it's charged.

## [1.184.0] - 2026-07-27

### Fixed

- **QuickBooks Purchases with item lines now post their expense debit.**
  `_map_purchase` credited the funding account for the whole transaction total but
  only debited `AccountBasedExpenseLineDetail` lines, so any Expense / Check /
  Credit Card charge carrying an `ItemBasedExpenseLineDetail` was self-inconsistent
  and the balance guard parked it. Purchase 3815 credited the bank $75.00 against
  no debit at all.

  This was deliberate, and its premise expired: an item line names only an Item, and
  no ERPNext Item carried a default expense account, so there was nothing to resolve.
  Now that the referenced Items have them, each item line resolves
  `ItemRef` → ERPNext Item → that Item's `Item Default.expense_account` for the
  company, and emits the debit — the same account ERPNext itself fills in on a
  Purchase Invoice line, looked up explicitly because a Journal Entry has no item
  rows to resolve it from. Falls back to the Company's `default_expense_account`.

  Resolves the 18 records parked on `Journal Entry is unbalanced`; each now balances
  with `total_debit` equal to the QuickBooks `TotalAmt`.

  An item whose account still cannot be resolved is **not** given a stand-in: the
  line is left out, the entry fails the balance guard, and the record parks naming
  the item — `Item "X" has no default expense account for company Y`. A
  wrong-but-balanced journal entry posts to the ledger and nobody looks at it again;
  a parked one gets fixed.

- **The unbalanced-Journal-Entry message no longer claims item lines are always
  skipped.** That was true when every item line was dropped; now the three outcomes
  need different fixes and read differently: the QBO item was never imported (import
  it), the Item has no expense account (set one), or both resolve but this entity's
  mapper reads only account-based lines. A line that produced a row is not reported
  at all, so the message goes quiet for the Purchases that now map — while staying
  specific for the ones that genuinely cannot.

## [1.183.0] - 2026-07-27

### Changed

- **The Project form's toolbar is five buttons shorter.** It had grown to
  `Merge into… | Actions | Merge Project | Maintenance Contract | Project Brief |
  Create | Open Drive Folder | ⋯ | Save` — a button per feature, because the five
  form scripts that build it are independent and none can see the others, so each
  defaulted to a top-level button.

  They now declare a group, folding into the dropdowns ERPNext already puts on
  Project rather than inventing new ones:

  - **Actions** (alongside Duplicate Project / Update Costing / Set Project
    Status) — `Merge Project`, `Merge into…`. Both are rare and irreversible, so
    they belong with the other whole-document operations rather than next to Save.
  - **View** (alongside Gantt Chart / Kanban Board) — `Project Brief`,
    `Open Drive Folder`, and `Maintenance Contract` when a contract already
    exists. All three are ways of looking at the project.
  - **Create** — unchanged: `Maintenance Contract` when there is none yet, and
    `Generate Contract`.

  The toolbar reads `Actions | View | Create | ⋯ | Save`. Nothing was removed and
  no behaviour changed; every action is one click deeper, inside a labelled menu.

  Two of these buttons are shared. `Open Drive Folder` also appears on Customer
  and Opportunity, and `Merge into…` is registered on *every* desk form — both
  keep their existing top-level placement everywhere else, since those forms have
  no such group and a dropdown holding a single item costs a click while reading
  worse than the button it replaced. The grouping is an explicit per-doctype
  opt-in in both files.

## [1.182.0] - 2026-07-27

### Fixed

- **The Contracts tab was empty on every project.** It listed only Project
  Contracts — the signed agreements — so a site whose maintenance work lives on
  **Sapphire Maintenance Contract** saw "No contracts on this project yet" on a
  project that plainly had one. The tab now lists both kinds side by side,
  because "what are we committed to here" is one question even though the data
  model has two answers.

  An operational maintenance contract mapped from a signed agreement opens that
  agreement's full text. One created directly has no legal language behind it,
  and the row now says so — *"Schedule only — no signed agreement linked"* —
  rather than leaving a reader to wonder why nothing opens. It deliberately does
  **not** render the agreement template over operational data: a document nobody
  signed must never be displayed as though it were a contract.

### Added

- **The same Contracts tab on Customer**, after Accounting: every agreement that
  customer is a party to, plus their maintenance contracts, each openable in
  place to its full text. Scoped to contracts they are actually a party to — a
  subcontractor SOW issued on their job is our commitment to a supplier, not
  theirs, and stays off their form.

## [1.181.0] - 2026-07-27

### Added

- **Reading the contract, not just filling it in.** Until now the only way to see
  what an agreement actually says was to save it and open the print view. A
  **Preview Contract** button on Project Contract now renders the whole document
  on screen — the Contract Template's legal language with this contract's data in
  it — **from the values currently on the form**, unsaved edits and brand-new
  drafts included. So the sentence a customer will read can be checked while it
  is being written, rather than discovered after the fact.

  A signed contract shows its **executed instrument** instead of a fresh render:
  the document the customer actually signed, labelled as such. Both the HTML and
  the styling come from the same print format that produces the PDF, so the
  agreement on screen and the agreement in the customer's hands cannot drift into
  saying — or looking like — two different things.

- **A Contracts tab on Project.** Every agreement issued on a job, in one place
  between Budget and Costing: customer agreements and subcontractor paper grouped
  apart, each with its status, party, date and headline figure, and each opening
  **in place** to its full text. Bodies are fetched only when a row is opened, so
  a project carrying a dozen agreements still lists in one small query.

- **"View Agreement" on Sapphire Maintenance Contract**, which reads the signed
  Maintenance Services Agreement it was mapped from. The operational contract
  holds the schedule; the language lives on the agreement, and now says so.

### Changed

- **Sapphire Maintenance Contract's terminal status is spelled `Canceled`**, not
  `Cancelled` — matching Project and Task, which already use the single-l
  spelling. Stored rows are carried over by patch, without which a contract left
  on the old spelling would refuse to save the next time anyone touched it.

### Fixed

- **Three fields the maintenance agreement was printing blank.** §4.2's invoicing
  cadence, §9.1's initial term and the Service Plan Selection's **Annual Service
  Fee Total** all rendered as empty paper placeholders on the live template, even
  though every one of them is filled in on the form — and the annual fee is one
  the system computes for you. A maintenance agreement therefore went out to
  customers without saying how often they would be billed, how long the term ran,
  or what the year cost.

  The repo template already bound all three; the live Contract Template did not,
  because `seed_contract_templates` is insert-only and a repo edit never reaches
  a site that already has the record — the same trap the e-signature block hit.
  A patch closes the gap, replacing each block only where it matches the expected
  wording exactly and logging (rather than overwriting) any section a site has
  rewritten. Each block stands alone, so bespoke wording in one does not cost the
  others their fix.

  A data-binding fix only: not one word of the agreement changes, and the patched
  live template comes out byte-identical to the one in the repo.

  Found by the preview above — the first time anyone could read a filled-in
  maintenance agreement without printing it.

## [1.180.0] - 2026-07-27

### Fixed

- **QuickBooks Bills that mix item lines with expense-account lines no longer
  import short.** A QBO Bill was treated as having one of two shapes — item-based
  (a Purchase Invoice) or expense-account-based (a Journal Entry). A bill carrying
  *both* took the Purchase Invoice branch, and every `AccountBasedExpenseLineDetail`
  line was silently discarded. Bill 19019 (SCE Saginaw, `2132402.01`) imported at
  345.47 against a QuickBooks total of 413.01, its 67.54 freight line gone, and
  nothing flagged it: Purchase Invoices had no balance guard, so it validated,
  saved and looked like a clean import.

  Mixed bills now stay Purchase Invoices and fold their account lines into
  **Purchase Taxes and Charges** as `Actual` charges booked to each line's own
  account, carrying the QuickBooks line description. That is the same posting the
  Journal Entry branch makes, and the same correction an accountant applies by
  hand. The charges table is rewritten wholesale on every sync, so re-syncing a
  hand-corrected invoice reconciles with it rather than double-counting.

- **A Purchase Invoice that does not reconcile to QuickBooks is now parked, not
  imported.** Mapped item amounts plus charges must equal the QBO `TotalAmt`;
  anything short goes to manual review naming what could not be carried across —
  an item or expense account that is not imported yet, or a residual no line
  explains. This is the guard whose absence let the bug above go undetected, and
  it catches the next variant nobody anticipated. No fallback account is ever
  invented to close a gap: a wrong-but-plausible document that posts to the ledger
  is worse than a parked one, because nobody looks at it again.

- **`conflict_status = "Ignored"` is now durable.** Records a human closed out —
  voided or $0.00 QuickBooks transactions that can never produce an ERPNext
  document — had no branch in `upsert_entity`. Their preflight failed again on
  every run and reset *both* `conflict_status` and `match_status` to
  `Pending Review`, so one full **Import All** silently reverted all 184 of them.
  An Ignored mapping is now returned untouched before preflight runs. The one way
  back in is QuickBooks itself moving: a `SyncToken` (or `LastUpdatedTime`) past
  the stored one is re-evaluated normally, so un-voiding a transaction does not
  leave it permanently invisible. Runs report an **Ignored Count** so those
  records read as a category rather than as an absence.

- **A QuickBooks payload with no `Id` is now skipped instead of being written
  under the id `"None"`.** `str(payload.get("Id"))` produced the *truthy* string
  `"None"` for a missing id, so the `if not qbo_id` guard on the next line was
  dead code. Such a payload sailed past it and keyed every downstream write on
  that literal — one `QBO-MAP-<entity>-None` mapping per entity type, each
  silently overwriting the last. The same hole let a CDC delete with no id report
  a clean "deleted" for a mapping it never found. Nothing in production was
  affected: no `None`-keyed mapping or raw payload exists, so this closes a
  latent hole rather than repairing damage.

- **The unbalanced-Journal-Entry message now names the actual cause.** It always
  said lines "may reference QuickBooks accounts not yet imported" — wrong for all
  18 Purchase records parked today, where every account is correctly mapped and
  the debit is missing because the mapper reads only account-based lines. It now
  distinguishes a line skipped during mapping from an `AccountRef` that could not
  be resolved, and names the accounts in the latter case. The two need opposite
  fixes.

## [1.179.0] - 2026-07-25

### Added

- **Chasing an unsigned agreement, and seeing the ones that stalled.** A signing
  link that goes unanswered is a stalled job, and nobody watches a list. Three
  additions close that:

  - **Automatic reminders** on a configurable cadence (**Signature Reminder Gaps**,
    default `3,7` — nudge after 3 quiet days, then 7 more, then stop). Each nudge
    issues a **fresh link**, because the original token is stored only as a hash
    and cannot be recovered; anyone opening the older email is told their link was
    replaced and offered a working one. Counters are stamped *before* each send,
    so a scheduler window evaluated twice cannot double-chase a customer. Blank
    means never chase.
  - **A one-time "still unsigned" alert** to the contract owner once the last
    reminder goes unanswered — the point at which it needs a phone call rather
    than another email.
  - **A weekly "awaiting signature" digest** to the notification list, showing how
    many days each has been out, whether the customer ever opened it, and how many
    times they have been chased. Silent when nothing is outstanding.

  Reminders deliberately do **not** clear the email-confirmation lockout: an
  automated chase is not a staff decision to unlock someone. A **staff** resend
  is, and now restarts everything — lockout, reminder schedule and stale alert —
  so a contract chased to exhaustion can be chased again after a phone call.

- **Signature request list view.** Rows are coloured by what they need from a
  human rather than by raw status: a link nobody has opened reads differently
  from one opened and abandoned, and both differ from one already chased. A
  signed contract whose text drifted between sending and signing is flagged
  amber rather than green.

## [1.178.0] - 2026-07-25

### Added

- **Autopay enrolment offered right after signing.** Once a customer has signed
  online, the page offers to save a card for automatic payments — the secure
  payment link that Exhibit B of the maintenance agreement already promises them.
  This closes the loop through the rest of the automation: a card on file means
  the recurring-billing engine's invoices auto-charge, and the dunning engine can
  recover a declined one.

  It is **offered, never required**, and that is enforced structurally rather
  than by convention: the card is only offered on a response whose contract is
  already Signed and committed, and both endpoints refuse a session that has not
  signed. Declining therefore cannot affect the signature, and neither can a
  Stripe outage — any failure returns a soft "we'll follow up about payment setup
  separately" rather than an error on a page that has just told someone their
  agreement is executed.

  The offer is skipped for a customer who already has a card on file, for
  non-Customer parties, and when either the feature flag or Stripe itself is off.
  Outcome is tracked on the signature request (`Not Offered` / `Declined` /
  `Started` / `Enrolled`), with `Enrolled` stamped when Stripe confirms the setup.

  The authorization record gains a distinct **"Contract Signing"** channel, so a
  card saved this way is distinguishable from a logged-in portal enrolment — the
  web session is anonymous, so the signer's identity evidence lives on the linked
  Contract Signature Request rather than in `accepted_by`.

## [1.177.0] - 2026-07-25

### Added

- **Online contract signing (e-signature).** Closes the last manual step in the
  maintenance-contract lifecycle. Everything downstream — the operational
  maintenance contract, scheduling, dispatch, billing, dunning — already fires
  off a Project Contract reaching **Signed**; until now a person had to chase a
  paper signature and flip that status by hand.

  Staff click **Send for Signature** on a submitted contract. The customer gets a
  tokenised link, reads the full agreement on a mobile-friendly page at
  `/contract-sign`, **types or draws** their signature, confirms the email address
  it was sent to, and signs. The contract flips to Signed, the automation chain
  fires, and they are emailed a fully executed PDF which is also attached to the
  contract. The paper route (**Mark as Signed**) stays, now recording *how* it was
  signed so every executed contract carries an evidence trail.

  Gated by two Settings switches, both **off by default** — a master one and a
  separate one to publish the public page, because this is the app's second
  unauthenticated write path and the first that mutates a legally operative
  document.

  Notable design points, each documented at its call site:
  - The signing token is **256-bit and stored only as a SHA-256**. It is
    *authorisation*, not attribution (the inverse of the intake invite's token),
    so a database copy must not carry it. Resending supersedes rather than
    extends, leaving exactly one live credential.
  - **Identity** = possession of the link + re-typing the recipient address
    (compared constant-time against a copy frozen at send) + a bot check. Wrong
    email and failed bot check return the same message, and the expected address
    is never echoed — not even masked-in-error.
  - The **agreement is snapshotted** when sent and the signed PDF is built from
    that snapshot, so a later edit to the (site-editable) contract template can
    never change what a signed contract says. The desk print serves the same
    stored instrument.
  - Signing flips the contract with a real `doc.save()`; a `db.set_value` would
    silently skip the entire downstream chain.
  - The Turnstile verifier is **reused, not re-implemented** — re-implementing is
    how the site-local-vs-UTC freshness bug comes back. Its policy is inverted
    here: an *unreachable* Cloudflare accepts the signature and flags it, because
    the token has already eliminated automated abuse and the resulting maintenance
    contract is a draft a human still activates.
  - Ships the **E-SIGN consumer disclosure** (right to a paper copy, how to
    withdraw consent, hardware needed), which the app did not previously have
    anywhere; §16.6 of the agreement asserts electronic execution is binding,
    which is not the same thing.

### Fixed

- **`autocreate_maintenance_contract_on_signed` leaked internal instructions.** It
  `msgprint`ed "create it manually via Create > Maintenance Contract" on failure,
  which would render to a customer once a Guest could trigger the hook. Staff
  still get the toast; a Guest-path failure now notifies the contract owner.

## [1.176.0] - 2026-07-24

### Added

- **Declined-card dunning.** Closes the collection loop for Stripe auto-charges:
  previously a declined card only stamped the invoice `Failed` and alerted
  Accounts once — nothing ever retried it. A new daily job
  (`stripe_payments.core.dunning.run_dunning_cycle`) now **enrolls** any
  outstanding invoice whose auto-charge failed and **retries the saved card** on
  a configurable schedule (**Dunning Retry Schedule**, default `2,4,7` days →
  four attempts over ~a week), re-charging under a distinct **`Dunning`** channel
  so the built-in per-failure Accounts alert stays suppressed and the engine owns
  the customer emails. The **customer is emailed on every failed attempt**. On
  **recovery** (the card clears or the invoice is paid) the invoice is marked
  `Recovered`; on **exhaustion** (all retries fail, or the card was removed) it
  alerts Accounts Managers, **turns off the customer's autopay**, and applies a
  **Service Hold** on the Customer that **pauses maintenance visit generation**
  until staff clear it. State lives on the Sales Invoice (`custom_dunning_*`),
  the hold on the Customer (`custom_service_hold`). Gated by a new
  **"Declined-Card Dunning"** Settings flag (**off by default**).

## [1.175.0] - 2026-07-24

### Added

- **Maintenance dispatch & technician assignment.** Drafted visits are no longer
  dateless, tech-less headers: the daily scheduler now stamps each with a
  **Scheduled Visit Date** (the feature's due date shifted to the nearest
  **Preferred Visit Day** on the agreement) and the site's **Default Technician**
  (a new field on the Maintenance Profile), and creates a silent Frappe
  assignment (ToDo + share). A new early-morning job **texts (Triton) and emails
  each technician their day's visits, ordered by a nearest-neighbour route** from
  the site coordinates — this digest is the active notification channel and is
  gated by a new **"Morning Technician Dispatch Digest"** Settings flag (**off by
  default**). A technician actually clocked into a site can still take ownership
  of a visit pre-assigned to the site's default tech, so clock autofill and
  attribution follow the real performer. Turns the self-claim pull model into a
  real dispatched schedule.

## [1.174.1] - 2026-07-24

### Fixed

- **Site-wide portal/website outage: `SapphireMaintenanceRecord has no attribute
  'website'`.** The `Sapphire Maintenance Record` doctype has `has_web_view` set
  (for its `/maintenance-records` customer-portal view) but its controller
  subclasses plain `Document` instead of `WebsiteGenerator`, so it never defined
  the `website` attribute. On Frappe v16, `DocumentPage.get_condition_field`
  dereferences `controller.website.condition_field` for **every** web-view
  doctype while resolving any unmatched path, so the missing attribute raised
  `AttributeError` and brought down all website/portal rendering — even the desk
  failed to load. The lookup is Redis-cached, which is why a deploy's cache flush
  suddenly exposed a latent defect. Fix: give the controller an explicit
  `website = frappe._dict(condition_field="docstatus")`, mirroring
  `WebsiteGenerator`'s default and gating web routing to submitted visits.

## [1.174.0] - 2026-07-24

### Added

- **Recurring maintenance billing (§4.2).** Monthly/Quarterly/Annually contracts
  are now billed in arrears by a new daily job: at each period close it drafts one
  Sales Invoice = a flat **Recurring Amount** (auto-filled from the linked
  agreement's Annual Fee ÷ cadence, overridable) **plus rolled-up consumables**
  from every submitted-but-unbilled visit since the last invoice. The invoice is a
  **draft** for review — submitting it fires the existing Stripe auto-charge — and
  each covered visit is stamped so nothing bills twice. Gated by a new
  **"Recurring Maintenance Billing"** Settings flag (**off by default**). Closes
  the gap where non-per-visit plans never auto-invoiced at all.

## [1.173.1] - 2026-07-24

### Changed

- **WI-014 (Branch A): require `Project` on Purchase Order lines.** Adds a
  `reqd=1` Property Setter on `Purchase Order Item.project` so material and
  subcontract cost must be booked to a job — alongside the existing `in_list_view`
  setters that surface the Project column on PO and PI line grids. Purchase
  Invoice lines stay optional (rapid bill entry; the WI-008 "Invoices without
  Project" filter drives adoption). **Note:** needs a generic overhead `Internal`
  project (e.g. `Internal - Shop Overhead`) so non-job POs have a target — the 13
  existing `Internal` projects are specific R&D projects. SOP:
  `docs/migration/wi014-project-on-purchase-lines.md`.

## [1.173.0] - 2026-07-24

### Added

- **WI-013: configurable Purchase Order approval threshold (CEO escalation).** A
  Purchase Order whose grand total exceeds a configurable threshold can only be
  *submitted* by a user holding the `PO Approver` role (the CEO) — everyone else
  saves the draft and hands it to the approver (a `before_submit` gate, no custom
  approval doctype). The threshold lives in **ERPNext Enhancements Settings →
  Purchasing Controls → PO Approval Threshold** (Currency, default $500; set 0 to
  disable) and is defaulted on existing installs by a patch. The resolution point
  (`po_approval.get_effective_threshold`) is structured so a future per-project
  override — a fixed amount or a percentage of the project budget — can slot in
  without touching the enforcement (WI-058). Bench-free unit tests cover the gate.
  SOP: `docs/migration/wi013-po-approval-threshold.md`.

## [1.172.0] - 2026-07-24

### Added

- **Maintenance contract renewal & rate-adjustment engine.** At a fixed-term
  contract's End Date, the daily scheduler now **auto-renews** it for another
  one-year term (§9.2) — rolling End Date forward and keeping it Active — unless
  a **Non-Renewal Notice** is set, in which case it expires as before. Gated by a
  new **"Auto-Renew Maintenance Contracts"** Settings flag (**off by default**,
  so behaviour is unchanged until enabled); each renewal notifies the Projects
  Manager and rolls the T-30 renewal reminder to the new term.
- **Scheduled rate changes (§4.5).** New `scheduled_rate` / `rate_effective_date`
  fields let staff schedule an annual per-visit rate change; Accounts is alerted
  30 days before the effective date to send the client written notice and update
  the Sales Order rate. Advisory only — nothing auto-changes billing, and it
  re-notifies if the schedule is edited.

## [1.171.1] - 2026-07-24

### Fixed

- **`bench migrate` no longer aborts with `DocumentLockedError` on Role Profile
  fixture sync.** Frappe core's `RoleProfile.on_update` locks the document and
  defers "re-save all users on this profile" to the long queue; the deploy's
  Redis `FLUSHDB` destroys that job before it can release the lock, orphaning it
  for up to 3h (Frappe's `DOCUMENT_LOCK_EXPIRY`). A second migrate inside that
  window then crashed on the first re-imported Role Profile, leaving the site
  down ("no healthy upstream" at the load balancer). A new `before_migrate` hook
  (`setup/document_locks.py`) sweeps stale Role Profile locks before fixture
  sync, so migrate is self-healing regardless of the FLUSHDB race or the expiry
  window.

## [1.171.0] - 2026-07-24

### Added

- **Maintenance Services Agreement — term & fees are captured and printed.** An
  Initial Term (§9.1) field drives the operational contract's End Date; the
  printed agreement's invoicing frequency (§4.2), Annual Service Fee Total and
  term checkboxes now render from the record instead of static blanks. The annual
  fee auto-computes from the included, priced service options by visit frequency
  (per-visit standard × visits/year, seasonal per-event, package per-year;
  derive-unless-overridden, left blank when a per-visit plan has no concrete
  cadence), and new maintenance contracts seed each option's unit and the
  standard per-visit price from the maintenance-fee Item.
- **Signed → operational handoff automation.** Signing a maintenance agreement
  auto-drafts the operational Sapphire Maintenance Contract (draft only —
  activation stays the human gate); the handoff also prefills covered features
  from the project's Serial Nos when there is no Maintenance Sales Order and
  resolves the default form template.
- **Contract renewal reminder.** A 30-day "Days Before" Notification warns the
  Projects Manager before a fixed-term contract's End Date, so a contract does
  not silently lapse now that End Date drives the daily auto-expire.
- **Maintenance Profile auto-stub on activation.** Activating a contract creates
  the site's Maintenance Profile if missing (access codes prefilled from the
  agreement's gate/key fields) and raises a ToDo to add the safety notes and the
  coordinates that power the Time Kiosk geofence. Broadened Profile permissions
  to Projects Manager / Maintenance Supervisor / Maintenance User.
- **Customer service-report portal.** Enabled the `/maintenance-records` portal,
  scoped per customer (a customer sees only their own submitted visits via a new
  permissions module + `permission_query_conditions` / `has_permission` hooks).
  Optionally emails the service report (Maintenance Record Print) to the
  customer's primary contact on finalize — Settings-gated, **off by default**.
- **Declined auto-charge alert (Stripe).** A failed automatic off-session charge
  now notifies Accounts Managers and stamps the Sales Invoice `Failed` instead of
  ageing silently in AR — covering both the synchronous card-decline path and
  async (ACH) failures via the `payment_intent.payment_failed` webhook.

### Fixed

- **Notification fixtures imported disabled.** Every record in
  `fixtures/notification.json` now sets `enabled: 1` explicitly; without it the
  fixtures imported disabled (and were re-disabled on every migrate), so the
  existing maintenance/call notifications — and the new renewal reminder — could
  silently never fire.

## [1.170.1] - 2026-07-24

### Fixed

- **`custom_docperm.json` fixture was missing `doctype`, blocking every deploy.**
  The Custom DocPerm records added in WI-012 (v1.170.0) carried no `doctype` key,
  so `sync_fixtures` aborted on every `bench migrate` with `KeyError: 'doctype'`
  (`import_file_by_path` → `doc["doctype"]`) — the prod deploy has failed since
  #622 merged. Added `"doctype": "Custom DocPerm"` to each of the 10 records
  (matching every other fixture file). All five referenced roles already exist in
  prod, so this is the only change needed to make migrate complete.

## [1.170.0] - 2026-07-24

### Added

- **WI-012: version-controlled the Material Request → Purchase Order permission
  split.** Field team leads (`Stock User`) raise Material Requests but cannot
  create Purchase Orders; PMs (`Purchase User` / `Purchase Manager`) convert MRs
  to POs. The applied Custom DocPerm rows for both doctypes are now a
  `bench migrate`-managed fixture (`fixtures/custom_docperm.json` + a `hooks.py`
  allowlist), so the split is code-reviewed and reproducible on fresh sites
  instead of hand-clicked. Buying Settings `po_required` / `pr_required` are
  deliberately left `No` (subcontract-labour bills arrive without POs day one).
  SOP: `docs/migration/wi012-purchasing-flow.md`.

## [1.169.3] - 2026-07-24

### Added

- **WI-011 apply runbook** (`docs/migration/wi011-apply-runbook.md`) — the exact,
  ordered role-cleanup change set, split into **Group A** (safe now — strips
  over-granted finance/admin roles by reassigning lean Role Profiles) and
  **Group B** (CEO sign-off — the preparer≠approver split with approver = CEO,
  plus service-account scoping). Records the `role_profile_name` replace-on-assign
  mechanism and the profile-bloat caveat, and marks John (CPA) as
  keep-access / never-`PO Approver` in the access matrix.

## [1.169.2] - 2026-07-24

### Changed

- **Renamed the `Accounts` Role Profile to `Accounting`** (WI-011 follow-up). This
  site UI-relabels the Customer DocType to "Accounts", so a profile named
  "Accounts" read as customer management when it actually bundles the accounting
  roles (Accounts Manager + Accounts User — GL / AP / AR authority). Done via an
  idempotent rename patch (`rename_accounts_role_profile`, post_model_sync so it
  runs before fixture sync) that also repoints any `User.role_profile_name` links,
  plus the fixture + `hooks.py` allowlist. No user was assigned the profile, so no
  access changed.

## [1.169.1] - 2026-07-24

### Added

- **Phase-0 execution plan** (`docs/migration/phase0-execution-plan.md`) — the
  dependency-ordered, topology-validated schedule for the 20 Phase-0 work items:
  waves with date windows, the `WI-010 → WI-011 → …` critical path, gates
  (monthly-close, the WI-017/WI-021 cross-phase trap), and the Nov-30 exit
  criteria feeding the December parallel run.
- **WI-011 access matrix & SoD design** (`docs/migration/wi011-access-matrix.md`)
  — the per-user Role Profile / Employee target matrix built from a live prod
  audit, plus segregation-of-duties findings (finance-authority
  over-provisioning, the shared `billing@` account holding approver rights,
  preparer=approver on AP/AR) and the preparer≠approver remediation, for HR
  confirmation + CEO sign-off. Documentation only; no config applied to prod.

## [1.169.0] - 2026-07-24

### Added

- **WI-010: version-control the security architecture (Role Profiles + Roles).**
  The 17 hand-built Role Profiles (Accounts, Design Team, Executive, Finance,
  Finance Team, HR, Inventory, Manufacturing, Poseidon, Production Team,
  Projects & Operations, Purchase, Sales, Sales & Marketing, Sales Team, System
  Manager, Technician) and the one `is_custom` role (`Employee Self Service`) are
  now `bench migrate`-managed fixtures (`fixtures/role_profile.json`,
  `fixtures/role.json`) via name-in allowlists — so the app's most
  security-sensitive config is code-reviewed and no longer drifts between test
  and prod. Adds the `PO Approver` role via an insert-only patch
  (`seed_po_approver_role`) for the upcoming WI-013 PO-threshold Authorization
  Rule. Fixtures were generated from production (the source of truth); role sets
  are unchanged. Retire/rename of the legacy `Poseidon` profile is deferred
  (needs sign-off). Assigning profiles to users is WI-011.

## [1.168.2] - 2026-07-24

### Fixed

- **WI-004 CoA design doc: corrected the numbering-convention wording.** §2 read
  "Groups end in `00`/`000`", but 15 of the 46 group rows are third-level
  sub-groups ending in a single trailing `0` (e.g. `1110` Cash In Hand, `2130`
  Sales Tax Payable, `5110` Stock Expenses). Reworded to the actual three-tier
  scheme (roots `000`, section groups `00`, sub-groups `x0`) to match the §2
  range table. Documentation only — the `chart_of_accounts.csv` /
  `coa_mapping.csv` artifacts are unchanged and were independently verified
  against live production (359/359 accounts reconcile, importer preview clean).

## [1.168.1] - 2026-07-24

### Fixed

- **Dark-theme (Timeless Night) safety on the Gantt canvas.** The chart canvas
  stays on the light DHTMLX skin in both desk themes (like the Mermaid
  charts), so anything drawn on it must use light-safe *literal* colours;
  several rules used desk variables that flip under Timeless Night. The side
  label (added in v1.168.0) used `var(--text-color)`, which goes near-white on
  dark and vanished against the white chart; the portfolio group-row shade used
  `var(--control-bg)` and the loading-placeholder text `var(--text-muted)`,
  both flipping dark/light and clashing with the grid. All are now literals,
  and the today marker's reds are pinned too. The container chrome (toolbar,
  menus, overlays) still follows the desk theme, and stays internally readable
  (dark background, light text) in dark mode. The invariant — *on-canvas =
  literal, chrome = desk variables* — is documented in both stylesheets.

## [1.168.0] - 2026-07-24

### Added

- **Drag-to-edit on the Gantt** (phases 1–2 of the editable-Gantt plan): drag a
  task bar to reschedule it, drag an edge to extend or shrink the due date, or
  drag the progress handle. Enabled on both the Project form Schedule tab and
  the Projects Dashboard portfolio Gantt.
- **`api/gantt.py::update_gantt_row`** — the write half of the widget's
  contract, as narrow as the read half. The target doctype must be one the
  same config already addresses; only `start`/`end`/`progress` may change, and
  only through the validated field map, so a write can never reach a field the
  chart does not plot; `frappe.has_permission(..., doc=name)` is checked on
  the specific document; submitted documents are refused; and the row is saved
  with `doc.save()` — never `db.set_value` — so controller validation, doc
  events and the realtime broadcast all fire.
- **Editing is default-deny per row.** DHTMLX's global `config.readonly` stays
  true and only rows the server reports writable (`meta.can_write`, one check
  per doctype) are marked editable, so group rows, project rows and lazy-load
  placeholders can never be dragged. Keeping the global flag also keeps the
  grid's "+" add-task button hidden — it checks the raw config rather than
  `isReadonly()`.
- **Optimistic edits with rollback.** DHTMLX moves the bar before
  `onAfterTaskDrag` fires, so the pre-drag state is snapshotted and restored if
  the write is refused, with the reason surfaced. A concurrent edit is detected
  through the row's `modified` stamp (now returned with every row) and reported
  as a conflict that reloads rather than clobbers, and a refresh arriving
  mid-edit is deferred instead of wiping the optimistic bar.

### Fixed

- **Bar labels were clipped and unreadable.** Two causes: the dashboard forced
  `height: 14px !important` on task bars, which left DHTMLX's inline
  line-height sized for the original height so the text overflowed and was cut
  off top and bottom; and a short bar (a one-day task in Week/Month view is a
  few pixels wide) had its name chopped mid-word inside it. The height override
  is gone — the lighter shade already distinguishes a task from its project —
  and labels now render **inside the bar when they fit, beside it when they do
  not**, using the skin's own side-label slot, with ellipsis and padding for
  in-bar text. Side labels are recoloured for the light canvas (the skin styles
  them for a dark one, i.e. invisible here).
- **Extending a project's last task no longer fails validation.** ERPNext's
  `Task.validate_parent_project_dates` throws `InvalidDates` when a task ends
  after its project's `expected_end_date` — and on this site that field is
  *derived* (`sync_project_dates_from_tasks` recomputes it as MAX(task end) and
  the form shows it read-only), so the latest task always sits exactly on the
  project end. Every project sampled was in that state, meaning the commonest
  edit of all would have been rejected. The write path now widens the derived
  window first; the sync hook sets the authoritative value straight after.

### Notes

- Project bars stay read-only by design: their dates are derived from tasks, so
  a drag would be silently reverted by the next task save.
- The four superseded `*_from_gantt` endpoints in `project_dashboard.py` remain
  in place for now (they have no callers); they are removed in a later phase
  along with dependency editing and the quick-edit dialog.

## [1.167.0] - 2026-07-23

### Fixed

- **Projects had no expand caret at all.** DHTMLX's own dynamic branch
  loading (`config.branch_loading` + `$has_child`) is **not implemented in
  the Standard/MIT build** — the key sits in the config defaults and nothing
  ever reads it — so a project whose tasks were not yet fetched rendered
  with `gantt_blank` and could never be opened. Each unexpanded project now
  gets a single hidden placeholder child, which is what makes DHTMLX draw
  the caret; the real tasks replace it on expand. Verified against the
  rendered DOM, not just the payload.
- **Clicking a caret navigated away** instead of expanding — DHTMLX fires
  `onTaskClick` for the expander too, so opening a project type jumped to
  the Project Type document. Clicks landing on the tree icon no longer
  reach the click handler, and group rows never navigate at all.
- **The filter dropdowns were misaligned.** Bootstrap's `.custom-control`
  absolutely positions its input, which stranded the tick as soon as a long
  customer name wrapped to a second line. Every filter list now uses a plain
  flex row (`.pg-check`).

### Changed

- **Project types no longer draw a bar on the calendar** — a type is a
  heading, not scheduled work. The row keeps its grid entry and caret, so it
  still expands and collapses.
- **The toolbar was reorganised** into two aligned rows — *what* is shown
  (search + filters) and *how* it is shown (zoom, Today, columns) with the
  colour key — replacing the ad-hoc margin utilities that let it drift as it
  wrapped.
- **Week is now the default view** (was Month).
- **PNG export removed** at request.

### Added

- **Find a project** — a search box filters the chart in place, without
  leaving the dashboard.
- **Optional grid columns** — Type / Start / End / % toggle individually
  from a Columns dropdown and persist per user with the rest of the saved
  view; the grid sizes itself to the columns actually shown.
- **Mobile support** for both Gantts: toolbars stack full-width instead of
  half-wrapping, dropdown menus stay inside the viewport, touch targets
  grow, and the charts take 60vh. Verified at a real 375px viewport — no
  horizontal page scroll, menus in bounds, chart rendering.

## [1.166.1] - 2026-07-23

### Fixed

- **The Portfolio Gantt threw on every load in v1.166.0** — no project got
  its expand caret. **Frappe v16 rejects SQL aggregates passed as strings**
  in a `fields` list ("SQL functions are not allowed as strings in SELECT
  ... Use dict syntax like `{'COUNT': '*'}`"), for `get_list` **and**
  `get_all`. Two call sites used the string form: the Gantt widget's lazy
  child-count query (v1.166.0), and `fleet_maintenance/status.py`'s
  `max(odometer) as max_odo` odometer roll-up — the latter broken for every
  caller of `refresh_vehicle_status` since the v16 upgrade (fleet
  maintenance is dormant on this site, so it never surfaced).
  Both now pass the aggregate as a dict. Frappe aliases the result column
  itself (`COUNT(*)`, ``MAX(`odometer`)``), so both read the value
  defensively rather than depending on that spelling — a missed alias would
  silently yield 0, which in the Gantt's case would drop every expand caret
  with no error at all. Regression-guarded: the lazy-count test asserts the
  dict form is used and that no string field contains `(`.

## [1.166.0] - 2026-07-23

### Added

- **Portfolio Gantt: expand a project to see its tasks.** Each project row
  now carries a caret; opening it loads *that project's* tasks onto the
  chart (nested sub-tasks and dependency arrows included) and collapsing
  hides them again. Loading is lazy by design — the portfolio holds 1,433
  tasks and one project alone has 360, so the initial chart fetches only a
  grouped count per project (`children.lazy` on `get_gantt_data`, surfaced
  as DHTMLX's `branch_loading` + `$has_child`) and pulls a subtree only when
  asked. The global "Show Tasks" checkbox is gone, replaced by this.
- **Colour coding by project type, with an on-screen key.** Project bars take
  their `project_type` colour (Build blue · Design purple · Service green ·
  Events orange · Delivery teal · anything else grey) and their tasks a
  lighter shade of the same colour, so a subtree reads as one family. A
  legend under the toolbar explains the palette plus the overdue marker.
- **More ways to filter the portfolio**, alongside the existing status and
  project pickers: **project type**, **customer**, a **date window** (only
  projects overlapping the next 30/90/180/365 days), and **at-risk only**
  (past its expected end date and under 100% complete). All are applied
  server-side through the validated filter path.
- **Overdue highlighting** — projects and tasks past their end date and not
  complete get a red outline and red grid label.
- **Grid columns** beside the name: Type, Start, End and % complete. End
  dates display the inclusive day the user entered (the API's `end_date` is
  exclusive).
- **PNG export** is back (the frappe-gantt swap had dropped it), rendered
  **client-side with dom-to-image** — deliberately *not* DHTMLX's
  `exportToPNG()`, which uploads the chart to `export.dhtmlx.com`; project
  schedules must not leave the browser.
- **The view is remembered per user** — filters, zoom level and which
  projects are expanded persist in localStorage, with a **Reset view**
  button to clear them.
- Grouping now coalesces: a project is filed under its **Master Project**
  when it has one, otherwise under its **project type** (no project
  currently sets a Master Project, so the old grouping row never appeared).
- Supporting `get_gantt_data` capabilities: `extra_fields` (validated raw
  column values passed through per row, capped and blocked from shadowing
  the keys the shaper owns), `group_by` accepting a list to coalesce, and
  `children.lazy`. Widget: `lazy_children`, `on_task_expand` /
  `on_task_collapse`, `add_rows()` and `open_task_ids()`.

## [1.165.2] - 2026-07-23

### Fixed

- **The Projects Dashboard Gantt rendered completely unstyled** (the v1.165.1
  fix corrected the Project form, but not the dashboard). Frappe renders a
  **Custom HTML Block inside a shadow root**, and document-level stylesheets
  do not cross a shadow boundary — so neither the widget's chrome nor the
  vendored DHTMLX skin (both attached to `document.head`) ever applied inside
  the block, no matter how they were delivered. Confirmed live: the block's
  shadow root contained only Frappe's own `desk.bundle.css`, the chart div had
  grown to 3,039px, and injecting the two stylesheets into that shadow root
  immediately restored it (638px, timeline scale rendered, level colours
  applied).
  The widget now attaches its stylesheets **per root node**: it resolves
  `this.el.getRootNode()` on mount and links the skin + chrome into the shadow
  root when there is one, always keeping a document-level copy as well so the
  skin's `@font-face` (grid expander icons) still registers. Styles are
  awaited before `gantt.init()`, since DHTMLX measures its container there.
  The widget chrome moved from `desk_addons.bundle.scss` to its own hashed
  entry, **`public/css/gantt_widget.bundle.css`**, resolved at runtime through
  `assets.json` (`frappe.assets.bundled_asset`, with a raw-path fallback) —
  content-hashed as v1.165.1 required, linkable into any root, and lazy again
  rather than global.

## [1.165.1] - 2026-07-23

### Fixed

- **The Gantt widget rendered unstyled everywhere** (Project form Schedule
  tab and the Projects Dashboard portfolio Gantt): collapsed to a thin
  strip, status-filter menu stuck open, dashboard reduced to a plain list
  of project names. The widget's own stylesheet was lazy-loaded from a raw
  `/assets/erpnext_enhancements/css/gantt_widget/gantt_widget.css` path,
  and browsers were being served the **v1.163.0** copy of it — verified on
  production: `ETag` length `0x76c` (1,900 bytes) with `last-modified`
  from the v1.163.0 deploy, while the widget JS beside it carried the
  current build. So none of the rules added in v1.164/v1.165 existed
  client-side: no `.ee-project-gantt` height (the stale file still defined
  the pre-rename `.ee-timeline-gantt`), no `display: none` on the filter
  menu, no flex/chart-wrap layout.
  Two compounding causes, one rule violated: `/assets` is served with a
  1-year **immutable** `Cache-Control`, *and* the deploy left the raw file
  stale on disk — the exact hazard `desk_addons.bundle.scss` and
  `kanban.bundle.js` were created for (v0.8.1). Our own CSS must ship
  content-hashed, so `gantt_widget.css` now builds into
  **`desk_addons.bundle.scss`** and every deploy gets a fresh URL. Only
  the **vendored** 140K DHTMLX skin stays a lazy raw include — a file that
  never changes cannot go stale. The height cap is written as
  `height` + `max-height` rather than `min()` (sass shadows `min()`; the
  plain form does not depend on how a given sass version treats mixed
  units).

## [1.165.0] - 2026-07-23

### Changed

- **The Projects Dashboard portfolio Gantt now renders through the
  embeddable Gantt widget** instead of frappe-gantt. The "Projects
  Dashboard" Custom HTML Block mounts `erpnext_enhancements.gantt.mount`
  in the new **composite mode**: Master Project groups → Project rows →
  Task trees, all through the permission-checked
  `api/gantt.py::get_gantt_data`. Feature parity carried over: the
  project-status filter, the project picker (options always come from the
  unfiltered result set so unchecked projects stay re-checkable), the
  "Show Tasks" toggle, view-mode zoom (Quarter Day … Month), hover
  tooltips, per-level bar/row styling, opens-at-today with a today
  column, and a Today button. **Deliberate changes:** read-only for now
  (drag-to-reschedule returns with the widget's per-embed edit opt-in
  milestone); rows come via `frappe.get_list`, so the caller's
  Project/Task read permissions now apply (the retired
  `get_all_projects_for_gantt` read with `get_all`); undated rows —
  projects as well as tasks — are skipped and counted in the chart's
  "unscheduled" note instead of drawn with fabricated fallback dates
  (an undated project whose tasks are visible still appears, as a
  container bar derived from them; note that on this site many projects
  carry no expected dates, so the portfolio shows fewer bars than the
  old chart's fabricated ones — by design).
  The retired frappe-gantt code paths (fallback dataset, collapse-state
  and scroll bookkeeping, dynamic color injection, dead `.gantt` CSS)
  are removed; `get_all_projects_for_gantt` and the gantt drag-update
  endpoints remain whitelisted but now have no JS consumers (flagged as
  removal candidates in the module README).

### Added

- **Composite mode for `get_gantt_data`**: optional `group_by` (field
  values become synthetic parent rows) and `children` (a second doctype
  nested under each root via a validated Link `link_field`, with its own
  field map / filters / dependencies / limit). The child doctype gets its
  own `frappe.has_permission` gate and validated `frappe.get_list`;
  child rows are constrained to roots the root query returned (children
  of missing roots are dropped and counted); undated roots that still
  anchor children are emitted as dateless `type: "project"` containers
  (DHTMLX derives their bar) instead of silently hiding the subtree; all
  ids are prefixed (`G::`/`P::`/`C::`) and every row carries
  `ref_doctype`/`ref_name` for click routing. Covered by new bench-free
  tests (33 total in `tests/test_gantt_api.py`).
- **Widget capabilities for host-driven embeds**: `config.today`
  (today column + default view without the widget toolbar),
  `config.tooltip` (bundled DHTMLX tooltip extension with a safe default
  template), `config.zoom` + `set_zoom()` (scale presets mirroring the
  legacy view modes), `config.templates` passthrough, `set_filters()`,
  `on_task_click(id, task)` now passing the task row, and `widget.data`
  exposing the last response. Fixed in the same pass: the today-range
  calculation now ignores dateless rows — a composite payload's group
  rows previously collapsed the scale to today±7d, which made DHTMLX
  drop every task outside it.

## [1.164.0] - 2026-07-23

### Changed

- **The Project Gantt widget moved into the Schedule tab's existing
  `custom_gantt_chart_html` field** (per review of the v1.163.0 "Timeline"
  tab). The embed script is renamed
  `public/js/project_enhancements/project_timeline_gantt.js` →
  `project_gantt_widget.js`; it shows the current project's Tasks (tree via
  `parent_task`, dependency arrows from `depends_on`), refreshes in place on
  the `project_dashboard_updated` realtime event with scroll preserved, and
  keeps the placeholder-on-unsaved / destroy-on-refresh /
  IntersectionObserver-lazy-mount behavior.
  The legacy frappe-gantt renderer in
  `project_enhancements/doctype/project/project.js` is removed with it —
  including, for now, its drag-to-reschedule/progress editing, dependency
  drag-linking, resource heatmap and PNG export (editing returns with the
  widget's per-embed edit opt-in milestone; the health banner and reminder
  button remain). The Projects Dashboard portfolio Gantt still uses
  frappe-gantt and is unchanged — its swap to this widget is a planned
  follow-up.
- **The v1.163.0 "Timeline" tab is removed again**: `project_enhancements/
  setup.py` and its `after_migrate` entry are deleted, and the
  `remove_project_timeline_fields` patch drops the `custom_timeline_tab` /
  `custom_timeline_gantt_html` Custom Fields that the v1.163.0 deploy
  already created on the sites (idempotent — a bench that never ran
  v1.163.0 is a no-op).

### Added

- **Gantt widget toolbar** (generic, opt-in per embed via
  `config.toolbar`): checkbox-dropdown **value filters** applied as
  server-validated `["in", ...]` filters with a debounced refetch, and a
  **Today** button. With `toolbar.today` the chart opens scrolled to today
  (the default view), highlights today's column via core cell-class
  templates (the DHTMLX marker extension is not in the Standard single-file
  bundle — its bundled extensions are only fullscreen/keyboard_navigation/
  quick_info/tooltip/export_api), and pads its scale so today is always in
  range; subsequent refreshes preserve the scroll position. The Project
  embed uses both: a **task-status filter** (all 8 statuses shown by
  default) and Today.

## [1.163.0] - 2026-07-23

### Added

- **Reusable embeddable Gantt widget** (milestones 1–2 of the embeddable-Gantt
  plan). A globally available `erpnext_enhancements.gantt.mount(container,
  config)` (`public/js/gantt_widget/gantt_widget.js`, shipped in the global
  bundle) renders a **DHTMLX Gantt 10 Standard — MIT edition** chart into any
  div. The 600K vendored library (`public/js/gantt_widget/lib/dhtmlxgantt.js`
  + skin CSS) lazy-loads on first mount only — styles via `frappe.require`,
  the library JS **fetched and evaluated synchronously inside an atomic
  `window.Gantt`/`window.gantt` save-restore bracket**, so the vendored
  frappe-gantt global (Schedule tab, Task list gantt) is never clobbered even
  transiently, and a failed load genuinely retries on the next mount
  (`frappe.require` would have marked the failed asset as executed forever).
  Each mount gets its own instance via `Gantt.getGanttInstance()`, so
  multiple embeds coexist; re-mounting a container destroys the previous
  instance. Widgets are read-only (per-embed edit opt-in is a later
  milestone).
- **Whitelisted read endpoint `api/gantt.py::get_gantt_data`.** The embed
  config is client-supplied, so the endpoint treats it as hostile:
  `frappe.has_permission` gates the call, rows come from `frappe.get_list`
  (role/user permissions + `permission_query_conditions` apply), every
  fieldname in the field map / filters / order_by is validated against
  `frappe.get_meta` (`start`/`end` additionally restricted to Date/Datetime
  fieldtypes — a Time or free-text column would crash date coercion), limits
  clamp at 1000 rows, and dependency links are returned only when both
  endpoints are rows the permission-checked query produced. Rows with
  missing or uncoercible dates are skipped and counted (never a 500), and
  parent references are re-rooted against the tasks actually emitted so an
  undated group task can never silently hide its whole subtree. Covered by a
  bench-free pytest suite (`tests/test_gantt_api.py`, wired into CI).
- **First real embed: a read-only "Timeline" tab on the Project form.**
  `project_enhancements/setup.py` (after_migrate, insert-only) adds a Tab
  Break + HTML host field; `public/js/project_enhancements/
  project_timeline_gantt.js` mounts the widget filtered to the current
  project's Tasks — task tree via `parent_task`, dependency arrows from
  `depends_on` — handling unsaved docs (placeholder) and destroy-on-refresh.
  The mount is gated on an **IntersectionObserver**: this Frappe build
  renders every tab's fields eagerly into hidden panes, so the widget mounts
  (and fetches data) only when the Timeline pane actually becomes visible —
  no wasted 1000-row fetches on every Project open, and DHTMLX always
  initializes with a real container size. The interactive frappe-gantt on
  the Schedule tab is untouched.

## [1.162.1] - 2026-07-23

### Changed

- **The intake form header now uses the marketing-site logo** (the SVG from
  sapphirefountains.com), shipped as an app asset under
  `public/images/fountain_move/logo.svg` rather than hotlinked or DB-hosted —
  versioned with the deploy query param because `/assets` is served immutable
  for a year. Vector, 276×100, dark-navy-on-white; sanity-checked for scripts
  and external references before shipping. Replaces the near-square blue PNG
  from the site's File store.

## [1.162.0] - 2026-07-23

### Changed

- **Fountain intake page rebranded to Sapphire Fountains, and "move" became
  "installation" everywhere a customer reads.** The header now carries the
  blue Sapphire wordmark (the site-hosted `/files/22-02-Sapphire-Logo-Blue.png`
  — a public File, not an app asset, so it matches the rest of the site and
  survives deploys), a **"Starting at $500 for installation"** badge, and the
  inclusions list (minimum two professional technicians; pick-up from the
  fountain's location; delivery; leveling, filling and pump speed adjustment).
  Cactus &amp; Tropicals now appears only in the purchase-location dropdown.
  The customer-facing invite email and SMS say "installation" too. Public
  copy only: the URL stays `/fountain-move` so circulated invite links keep
  working, and the doctype/desk/staff surfaces keep their internal names.
  The logo carries its real pixel dimensions (no layout shift on slow
  connections) and hides itself if the file is ever missing.

- **Address autocomplete moved INTO the Address line 1 field** — the separate
  "Search for the address" box is gone. The legacy `places.Autocomplete`
  widget (which could attach to an input) is closed to new customers, and
  `PlaceAutocompleteElement` is a sealed custom element that cannot wrap an
  existing field — so this uses the Autocomplete **Data API**
  (`AutocompleteSuggestion`) with a self-rendered WAI-ARIA combobox listbox
  under the input: debounced fetches, stale-response guard, session tokens
  renewed after each pick (billing correctness), keyboard navigation
  (arrows/Enter/Escape), mousedown-before-blur selection, and the "Powered by
  Google" attribution the data API's policy requires off-map. Browser autofill
  is disabled on that one input only once suggestions actually initialise —
  the degraded page (no key, blocked script) keeps a plain input with native
  autofill, exactly as before.

## [1.161.0] - 2026-07-23

### Added

- **Fountain-move intake: optional scheduling preference.** A new "When works
  best?" section lets the customer rank up to three preferred days, each with
  an optional Morning/Afternoon window. Preference only, by design — nothing
  reads or reserves real availability, nothing about our schedule leaks to a
  public page; staff confirms the actual date during the quote call, and the
  desk fields say so.
  - **Rules:** entirely optional; the earliest requestable day is 3 *business*
    days out (the chosen day itself may be a Saturday — that's staff's call),
    and nothing beyond 180 days. The floor/ceiling render into the date
    inputs' `min`/`max` (so the picker itself steers right) and are re-checked
    server-side against site-local "today" — deliberately `getdate()`, not
    UTC: three business days out is a Utah business rule, and at 5 pm Mountain
    the UTC date is already tomorrow.
  - **Normalisation at the guest boundary:** slots dedupe, compact upward
    ("only slot 3 filled" lands in slot 1, so staff can trust slot 1 = first
    choice), a window without a date is dropped rather than an error, and
    non-ISO dates, out-of-window dates or invented window values throw
    friendly messages. Six new allowlisted fields on the request doctype;
    a new test asserts every `INTAKE_FIELD_MAP` target exists on the doctype,
    because frappe silently ignores `set()` on unknown fieldnames.
  - **Staff surfaces:** the new-request email, the Lead/Opportunity details
    block and the scheduling note all carry the preferences as one line
    (`2026-08-12 (morning); 2026-08-14`), each explicitly marked unconfirmed.
  - The submit-button gating now also flags *optional* fields that are filled
    but invalid (an out-of-range preferred date) with the same named hint —
    submitting would only bounce off the server. `badInput` is caught too: a
    keyboard-typed impossible date (Feb 31) reports `value === ""` while still
    showing in the control, and would otherwise vanish silently at submit.
  - Hardened after adversarial review: the duplicate-collapse fingerprint now
    includes the normalized slots ("same details, now with dates" is added
    information, not a double-click — it previously collapsed into the old row
    and silently discarded the dates); the spam path *drops* invalid slots
    instead of throwing (a Spam row must still be inserted, and a new throw
    path would hand bots a differentiated response); fallback browsers that
    render `type=date` as plain text get a `placeholder`/`pattern` format cue
    and a server message that names the `YYYY-MM-DD` format instead of
    referencing a calendar that never rendered.

## [1.160.4] - 2026-07-23

### Fixed

- **Every Turnstile verdict came back Failed — "Please complete the
  verification check first." on every photo upload — because the challenge
  freshness check compared UTC against Mountain time.** Cloudflare's
  `challenge_ts` is ISO-8601 UTC (trailing `Z`); `_challenge_fresh` stripped
  the `Z` and compared against `now_datetime()`, which is site-local
  (America/Denver), so a solve from 45 seconds ago measured ~6 hours out and
  failed the 300-second replay window. Cloudflare itself said *success* on
  every one of these — our own assertion then threw the verdict away.

  Verified against a live production session before fixing: hostname and
  action asserted fine, `challenge_ts 2026-07-23T16:07:50Z` vs server
  `10:08:35` local. Nobody could see this before v1.160.3 because every
  request died earlier on the reserved-`sid` bug; peeling that layer exposed
  this one. The check now parses the stamp timezone-aware and compares
  against UTC. The decision-table tests had stubbed `_challenge_fresh` out
  entirely, so a new test pins the UTC behaviour with the real function —
  including the exact "solved moments ago on a UTC-6 site" case production
  rejected.

## [1.160.3] - 2026-07-23

### Fixed

- **Every fountain-move submission failed with "Your session has expired",
  because the intake session id was posted as `sid` — a name frappe owns.**
  Frappe treats a request parameter literally named `sid` as a *login* session
  id: `sessions.py` (`Session.__init__`) runs `frappe.form_dict.pop("sid",
  None)` during auth, before the handler binds a whitelisted method's
  arguments. So our value was popped out of the body, tried (and failed) as a
  user session — flagging `session_expired: 1` on every response and running
  the request as Guest — and the endpoint received `sid=None`. `begin_intake`
  therefore minted a fresh session on every call (orphaning uploads), and
  `submit_intake` / `upload_intake_photo` threw the session-expired error for
  every visitor, guest and staff alike, on every attempt.

  Diagnosed against production: the Redis session written by `begin_intake`
  was verifiably present and readable server-side while the very next web
  request claimed it had expired — and `begin_intake` echoed a *new* sid when
  sent an existing one, proving the parameter never arrived. The pop applies
  to JSON bodies, form bodies and query strings equally.

  The session id now travels as `intake_sid` on all three endpoints. Nothing
  may ever POST a key named `sid` to a frappe site unless it means "log me in
  as this session".

- **A photo removed after its upload finished was silently attached anyway.**
  The Remove button only cleared the browser UI; the server session kept the
  file handle and `submit_intake` attached everything in it. For "I uploaded
  an interior photo of the wrong room" that is a real privacy failure. The
  client now sends `photos_present` (the kinds the customer can still see) and
  the server intersects it with its own session record — the client can only
  *shrink* the set, never name files, so the never-trust-the-payload rule for
  Files holds. Remove now also aborts an in-flight upload (AbortController),
  so a stalled request on flaky mobile data no longer locks the submit button
  behind a "waiting for photos" hint about a photo that is no longer there.

### Changed

- **The "Send my request" button now stays disabled until the form is
  actually sendable** — every required field filled and valid, no photo
  upload in flight — with a hint under the button saying what unlocks it.
  Validation on submit remains as the backstop, and both paths share one
  completeness check so they cannot disagree. The completeness poll runs on
  every input/change event (and after Google fills the address, which fires
  no events) and deliberately never touches `aria-invalid` — a poll must not
  mutate accessibility state while someone is mid-correction.

  Details that came out of adversarial review of the first cut:
  - *Filled-but-invalid* fields (weight `112.5` against `step=1`, email
    `john@`) get their own hint naming the field and the problem — the
    generic "fill in the required fields" line is a lie in that state, and
    with the button disabled, onSubmit's focus-and-name diagnosis can never
    run. The hint is the button's `aria-describedby` and a polite live region
    (identical-text writes are skipped so it announces once, not per
    keystroke).
  - When a Turnstile widget is rendered but unsolved, submitting is
    *guaranteed* to end in the spam queue, so onSubmit now asks for the solve
    instead of accepting a submission we know we'll bin. Every degraded path
    (no sitekey, dead script, unreachable Cloudflare) still submits, and
    `ensureSession` now carries the widget's live token when one exists — a
    solve whose `begin_intake` POST was lost to a network blip becomes a
    Passed verdict at submit instead of a spam-parked row.
  - `.fm-submit:disabled` now shows `not-allowed` instead of `progress`;
    disabled mostly means "waiting on the customer" now, and a spinner cursor
    there claimed the page was busy while it was idle. The genuinely busy
    states (sending, photo uploading) keep `progress` via a busy class.

## [1.160.2] - 2026-07-21

### Fixed

- **Address autocomplete never initialised, and the reason was invisible.** The
  page loaded `maps/api/js?...&loading=async` as a plain `<script>` tag and then
  called `google.maps.importLibrary("places")` in its `onload`. But that URL
  returns a *loader* which injects `main.js`/`places.js` afterwards — verified by
  fetching it: the returned bootstrap contains **zero** occurrences of
  `importLibrary`. So `onload` fired while `importLibrary` was still `undefined`,
  the call threw `TypeError`, and a deliberately silent `.catch()` swallowed it.

  Diagnosed against production in a real browser: the key was correct, both APIs
  enabled, referrer restriction fine, Maps fully loaded (`places.js` present),
  `importLibrary` a function *after* load, and every step of the init worked when
  replayed manually — yet nothing rendered and nothing was logged.

  Now uses Google's official inline bootstrap loader, which defines
  `importLibrary` synchronously and queues calls until the library is ready,
  removing the race. **Failures now `console.warn` instead of vanishing** — the
  silence was the real defect; it made a one-line bug look like a configuration
  problem for days.

- **The form was unreadable for anyone whose OS is in dark mode.** The stylesheet
  carried a `prefers-color-scheme: dark` block, but frappe's *website* chrome does
  not follow the OS — so the cards went dark while the surrounding page stayed
  white, and because frappe sets an explicit colour on `h1`/`h2` the section
  headings kept their dark value and became near-invisible against the dark cards.

### Changed

- **Redesigned the public intake form.** Light-only, with colour set explicitly on
  every element it renders so no host stylesheet can half-apply to it. Numbered
  step chips per section, Residential/Commercial as selectable tiles rather than
  bare radio dots, dashed drop-zone styling for the photo inputs, a proper focus
  ring, tabular reference numbers, a check-mark confirmation state, and styling
  hooks for the Places widget host so it matches the surrounding inputs instead of
  reading as a foreign object.

## [1.160.1] - 2026-07-21

### Fixed

- **The fountain-move form was completely broken for anyone signed in.** Every
  guest endpoint returned **HTTP 400** for a logged-in visitor, so `begin_intake`
  never opened a session and nothing could be submitted.

  The page shipped with no `csrf_token` in its boot payload, on the reasoning that
  guests carry no saved token and frappe's `validate_csrf_token` short-circuits
  for them (`auth.py:86`). That is true — and only half the picture. This page is
  equally reachable by **staff previewing it**, whose session *does* carry a
  token, so validation is enforced and a POST without the `X-Frappe-CSRF-Token`
  header throws `CSRFTokenError` — which is a 400. Same-origin does not help:
  `is_allowed_referrer` only passes hosts explicitly listed in site config's
  `allowed_referrers`, which is empty by default.

  The controller now emits the session's **existing** token (or `""`), and the
  client sends the header only when it is non-empty. Deliberately not
  `frappe.sessions.get_csrf_token()`, which *mints* a token that `Session.update`
  never persists for Guest — a guest would then send one the server has never
  stored. The anonymous path is unchanged: empty token, no header, no validation,
  with Turnstile + honeypot + rate limiting still the actual protection.

  Two regression tests guard it, both AST-based rather than substring — the code
  comments legitimately mention `get_csrf_token()` and `Content-Type` while
  explaining why neither is used, and substring checks tripped on the
  documentation. Mutation-checked: dropping the header, dropping the boot entry,
  minting a token, or setting `Content-Type` on the multipart upload each fail
  the suite.

## [1.160.0] - 2026-07-21

> **Builds on v1.159.11**, which is merged into this release. The conversion
> engine stamps `Lead.custom_lead_source`, `Opportunity.custom_lead_source` and
> `Lead.custom_opportunity`; those Custom Fields ship in v1.159.11 and are
> present here. Frappe silently drops writes to fields that do not exist, so the
> two must ship together — they now do.

### Added

- **Public fountain-move intake form for the Cactus & Tropicals partnership**
  (`/fountain-move`, default OFF). A customer who buys a fountain at Cactus &
  Tropicals is referred to us to move it; until now that arrived as a phone call
  and a manual CRM entry. The form collects the customer's details, the
  destination address (with Google Places autocomplete), the property type, the
  fountain's weight, water/electricity access at the destination, and photos of
  both the fountain and the route it has to travel — the two things that decide
  how many people and what equipment to send.

  Submissions land as a **Fountain Move Request** and convert, in a background
  job, into a linked **Customer → Address → Contact → Lead → Opportunity** set.
  The staging doctype is deliberate: spam never reaches CRM, a partial failure is
  resumable rather than duplicating master data, and the original payload is
  preserved for audit. Staff email the link from the desk ("Send Intake Link"),
  and the same URL works bare so it can be printed as a QR code at the till.

  Customer naming follows the operator's rule — Residential becomes
  "<First> <Last> Residence", Commercial just "<First> <Last>". Everything the
  form asks that has no native Lead field is written into `Lead.custom_lead_details`.

- **This is the app's first unauthenticated write path**, so its controls are new
  rather than inherited: Cloudflare Turnstile (verified server-side, fail-closed —
  an outage parks the submission for a human instead of auto-converting it), a
  honeypot keyed on the *presence* of the field in the raw body (frappe sanitises
  a guest's `form_dict` before the endpoint sees it, which can blank the value),
  a minimum time-on-form, per-endpoint rate limits plus session- and email-keyed
  counters, magic-byte image sniffing with a total-pixel cap, and a strict
  20-key field allowlist. `read_only` in a DocType JSON is a UI hint, not
  authorisation — under `ignore_permissions` a splatted payload could otherwise
  set `status` or the `created_*` links directly.

- New `/terms-of-use` Web Page (DRAFT pending counsel review, matching the
  existing payment-terms and refund-policy pages) — the form's consent checkbox
  links to it and to the existing Privacy Policy.

### Fixed

- `utils/triton_sync.py` filtered by *module* only, so the new guest-submitted
  doctypes — which carry unauthenticated PII and, pre-conversion, may be
  arbitrary bot input — would have been announced to Triton. Both are now
  excluded by name.

### Changed

- New CI step: the bench-free `test_fountain_move` pytest suite.

- The page's controller is `www/fountain_move.py` while its template is
  `www/fountain-move.html` — **underscored on purpose.** Frappe maps a template's
  basename `-` to `_` when locating its controller, so a hyphenated `.py` is
  silently never imported and its `get_context` never runs. The route is
  unaffected (it comes from the template). This same trap had already broken
  `www/stripe-return.py`; the fix and the CI guard that prevents recurrence ship
  separately in v1.159.10.

## [1.159.11] - 2026-07-21

### Fixed

- **Lead-source attribution was never recorded on Lead or Opportunity, and the
  Lead → Opportunity back-link never persisted.** Two independent instances of
  the same failure mode: a customization pointed at a field that does not exist,
  and Frappe said nothing.

  1. **Three orphan Property Setters.** ERPNext v15 renamed `Lead.source` and
     `Opportunity.source` to `utm_source`; `Lead-source-reqd`,
     `Opportunity-source-reqd` and `Lead-source-label` were never repointed. A
     Property Setter for a missing field is not an error — Frappe simply never
     finds a docfield to apply it to — so all three have been inert ever since,
     and the "source is mandatory" rule they were meant to enforce has never
     applied to a single record. Deleted by patch (removing them from
     `fixtures/property_setter.json` alone is insufficient; fixture sync is
     create/update-only).

  2. **`update_lead_status` assigned to a non-existent attribute.** It set
     `lead_doc.opportunity = doc.name`, but ERPNext's Lead has no `opportunity`
     field, and Frappe silently discards unknown attributes on save. Every Lead
     converted to an Opportunity was marked `Converted` and then pointed at
     nothing.

  Attribution now lives in real Custom Fields — `Lead.custom_lead_source` and
  `Opportunity.custom_lead_source`, both Link → **Lead Source** — matching the
  already-populated `Customer.custom_lead_source` (set on ~694 customers). The
  parallel `UTM Source` taxonomy carries the same 22 members but is effectively
  unused here (0 Leads, 1 Opportunity), so reviving that path would have meant
  migrating live data onto the emptier of two identical lists.

  Deliberately **not** re-applied as `reqd`. The old setters intended a mandatory
  source but never took effect, so no existing record has one; making it
  mandatory now would block every save of the ~200 existing Leads until someone
  backfilled them by hand. That is a migration decision, not a side effect of
  deleting dead configuration.

### Added

- `Lead.custom_opportunity` (Link → Opportunity, read-only) and
  `patches.backfill_lead_opportunity_link`, which reconstructs the historical
  back-links exactly from the forward `Opportunity.party_name` pointer rather
  than guessing. Verified against live data first: every Lead that has an
  Opportunity has exactly one, so collapsing the relationship into a single Link
  field loses nothing. Insert-only (never overwrites a hand-set link), skips
  Leads that no longer exist, and does not touch Lead status — some of these sit
  at `Opportunity` or `Lost Quotation` rather than `Converted`, which is
  deliberate pipeline state.

## [1.159.10] - 2026-07-21

### Fixed

- **The Stripe Checkout return page told customers who cancelled that their
  payment was going through.** `www/stripe-return.py` had never executed — not
  once since it was written. Frappe locates a page controller from the
  *template's* basename with hyphens replaced by underscores, so for
  `stripe-return.html` it looks for `stripe_return.py`, which did not exist. The
  hyphenated file was simply never imported.

  Nothing errored. The template rendered as normal, with every context variable
  undefined — so `outcome == "cancel"` was false and the page fell to the `else`
  branch. Verified against the real renderer on a bench: before the fix,
  `?status=cancel`, `?status=success` and a bare request all produced the
  identical page, "Thank you! Your payment is being processed." Someone who
  deliberately cancelled at Stripe was thanked and told it was processing.
  A card payment that had already settled was also mislabelled as processing,
  because `payment_status` (looked up from `?sp=`) was never set either.

  Fixed by renaming the controller to `www/stripe_return.py`. **The public route
  is unchanged** — it comes from the template, which keeps its hyphen — so
  Stripe's configured `success_url` / `cancel_url` keep working and no
  customer-facing URL moves. Confirmed on a bench that cancel now renders
  "Payment cancelled", a `Paid` row renders "Your payment was received", a
  `Processing` row renders "is being processed", and an unknown `?sp=` still
  renders safely.

### Added

- `scripts/check_www_controllers.py` + a CI step failing the build if any
  `www/*.py` basename contains a hyphen, so this class of silent breakage cannot
  recur. There is no exemption list: the only offender is fixed above.

### Changed

- Corrected `erpnext_enhancements/www/README.md`, which claimed a hyphenated
  route required a hyphenated controller filename. The opposite is true, and that
  note is what let the bug survive review. Replaced with an explanation of the
  actual mapping and the failure mode.

## [1.159.9] - 2026-07-20

### Changed

- Version bump for CI/CD deployment testing (no functional changes).

## [1.159.8] - 2026-07-17

### Removed

- **Consolidated the two parallel Projects Dashboards into one.** The app had *two*
  ~1,200-line implementations of the same dashboard — the **Custom HTML Block**
  (embedded on Home / Projects, what users see) and a standalone **desk page**
  (`/app/project-dashboard`). They drifted independently (a change to one didn't touch
  the other), which is what made recent edits appear not to take. The Custom HTML Block
  is now the single dashboard; the desk page and its per-tab components were removed:
  - Deleted `page/project_dashboard/project_dashboard.{js,json}` (the desk Page) and the
    desk-only components `dashboard_api.js`, `dashboard_view.js`, `priority_overview.js`,
    `active_internal_projects.js`, `completed_projects.js`, `portfolio_gantt.js`,
    `tasks_view.js`. Kept the shared backend `project_dashboard.py` (the block calls it)
    and the shared `column_selector.js` / `column_resizer.js`.
  - Removed the now-unused `get_dashboard_metrics` endpoint (the block's Dashboard tab
    computes its metrics client-side).
  - The desk shortcut and the Project Enhancements workspace link that pointed at the
    retired page now open the **Projects workspace** (`/app/projects`), where the block
    lives. Existing sites are updated by `patches.retire_project_dashboard_desk_page`
    (deletes the leftover `Page` record + repoints the seeded shortcut).
  - Note: the desk page's **Tasks View** tab and page-role gating did not carry over
    (the block gates by workspace visibility, and its Portfolio Gantt covers task
    scheduling). Say the word if you want Tasks View ported onto the block.

## [1.159.7] - 2026-07-17

### Added

- **The Projects Dashboard changes now apply to the surface users actually see — the
  "Projects Dashboard" Custom HTML Block** (rendered on the Home / Projects workspaces).
  v1.159.4 added the buttons / Dashboard tab / internal filter to the *desk page*
  (`/app/project-dashboard`), a separate, parallel implementation, so the workspace
  dashboard was unchanged. Ported the same three changes to
  `custom_html_blocks/projects_dashboard.{js,html}` (auto-deployed by
  `setup.custom_html_blocks.sync_custom_html_blocks` on migrate):
  - **New Project** / **New Master Project** quick-create buttons in the toolbar.
  - A **Dashboard** tab (last) — a native module overview computed client-side from the
    already-fetched `project_data` (no extra server call): headline number cards
    (active, overdue, avg % complete, open tasks, master projects, completed) plus
    CSS-bar breakdowns by status / type / completion. Uses inline styles / CSS bars
    rather than `frappe.Chart`, whose injected styles don't cross the block's shadow root.

### Changed

- **Active Internal Projects (Custom HTML Block) now lists only internal projects** —
  active projects whose `project_type` is Internal / Organizational Projects / Group
  Projects / Other (was: every active project). Mirrors the page dashboard's
  `INTERNAL_PROJECT_TYPES`. Verified against live data: 14 active internal projects.

## [1.159.6] - 2026-07-17

### Changed

- **Hid the "Project" link in the default Projects module sidebar** (user request, for
  now). The core Projects Workspace Sidebar carried a `Project` DocType link; a new
  `after_migrate` hook (`setup.workspace_tweaks.hide_core_sidebar_items`) removes it. The
  Workspace Sidebar Item child has no `hidden` field, so the row is dropped rather than
  flagged; everything else stays — including the "Dashboard" link (also points at Project
  but is a Dashboard link), Task, Timesheet, Setup, and the reports. Idempotent, and it
  re-applies after any core re-sync (the hook runs after Frappe syncs the standard
  sidebars). Verified against the live sidebar: 18 items → 17, only the `Project` DocType
  row removed.

## [1.159.5] - 2026-07-17

### Fixed

- **Opportunities with a corrupted Primary Address could not be saved.** On
  Opportunity/Project/Master Project, `primary_address` is a **Link** to Address, but
  legacy data (Zoho import / an old migration) had stored the rendered address *display*
  — HTML with `<br>` tags, e.g. `2600 Taylorsville BLVD<br>…` — in the field instead of
  an Address docname. The Link then failed validation (`Could not find Address: …`) on
  every save, making those records un-editable (5 Opportunities on production).
  - A new `before_validate` hook (`sync_contact.sanitize_primary_address_link`, wired on
    Opportunity/Project/Master Project) clears any `primary_address` that doesn't resolve
    to a real Address, so the record saves; the user re-picks it from the directory UI.
    It only acts on the Link-type field — Customer/Supplier `primary_address` is a
    read-only Text Editor display where HTML is expected and is left untouched.
  - `patches.clear_invalid_primary_address_links` proactively nulls the existing bad
    values across the three doctypes on deploy (idempotent).

## [1.159.4] - 2026-07-17

### Added

- **Projects Dashboard: "New Project" / "New Master Project" header buttons.** Two
  quick-create actions in the page header open the respective new-document forms.
- **Projects Dashboard: a native "Dashboard" tab.** A new first/default tab renders a
  Projects-module overview from real elements (no iframe): headline number cards
  (active, overdue, avg % complete, open tasks, master projects, completed) plus charts
  for active projects by status, by type, and by completion bucket. Charts use the desk
  `frappe.Chart` global with a CSS-bar fallback. Backed by a new whitelisted
  `get_dashboard_metrics` (page-role gated, portfolio-wide aggregates). It is the last
  tab; Priority Overview remains the default landing tab.

### Changed

- **Active Internal Projects now lists only genuinely internal projects.** Previously it
  showed every active project; it now filters to active projects whose `project_type` is
  one of Internal, Organizational Projects, Group Projects, or Other (client-facing
  streams — Design/Build/Service/Events/Delivery/External — and untyped projects are
  excluded). The set lives in `INTERNAL_PROJECT_TYPES` (server + client, kept in sync).

### Fixed

- **Projects Dashboard tabs failed to load (all of them).** Since the app-consolidation
  commit, `project_dashboard.js` required its tab components (and the shared
  `dashboard_api.js`) from `/assets/erpnext_enhancements/js/dashboard_components/…`, but
  the files actually live under `…/js/project_enhancements/dashboard_components/…` — the
  short path 404s, so the shell couldn't even load the API helper. Corrected every
  `frappe.require` URL to the real path. (Verified against the live site: the short path
  returns 404, the corrected path returns the JS.)

## [1.159.3] - 2026-07-17

### Fixed

- **The Contact form no longer shows two tabs both labeled "Comments".** One is the
  real Comments tab (`custom_comments` → the Comments-app widget); the other,
  `custom_comments_tab`, was mislabeled — it actually holds a "More Information"
  section and the core contact-detail fields (middle name, email, designation,
  salutation, department, phones, image, etc.). Relabeled that tab to **"Additional
  Information"** (fixture label change in `custom_field.json`; fixture sync applies it
  on migrate, no patch). Deliberately not "Details": `setup.custom_fields.create_unified_tabs`
  has a Contact special-case that adopts any Tab Break labeled exactly "Details" as the
  address-widget host, so that name would have relocated the address/location widgets.
  Verified against the live meta: Contact now shows one "Comments" tab (the widget) plus
  "Additional Information", and no "Details" tab is introduced. Completes the
  duplicate-Comments-tab cleanup started in v1.159.2.

## [1.159.2] - 2026-07-17

### Fixed

- **Removed a blank, duplicate "Comments" tab that showed on 12 doctypes.** The
  canonical Comments UX is a `custom_comments` Tab Break followed by the
  `custom_comments_field` widget, but many forms had accumulated a *second*
  "Comments" Tab Break (usually `custom_comments_tab`) sitting empty — rendering as
  a clickable, blank duplicate tab. Determined per-doctype from the live meta which
  tab was empty (the one without the widget under it; on Employee, Purchase Order and
  Supplier Quotation the empty one was `custom_comments`, inverted from the rest) and
  deleted only that orphan. Affected: Address, Batch, Delivery Note, Employee, Lead,
  Project, Purchase Order, Quotation, Serial No, Stock Entry, Supplier Quotation, Task.
  - The surviving tab + widget are untouched; fixtures repoint `custom_comments_field`
    (and Task's `custom_timeline`, Address's `custom_comments`) off the deleted field
    so no dangling `insert_after` is left behind, and the deleted field is dropped from
    each affected `field_order`. Removal is applied on existing sites by
    `patches.remove_duplicate_comments_tabs`. Verified by simulating each form against
    the live meta: every one ends with exactly one Comments tab holding the widget.
  - `setup.custom_fields.create_primary_contact_fields` no longer calls
    `create_comments_tab("Project")` — that would have resurrected Project's empty
    `custom_comments_tab`; Project's Comments tab is fixture-owned. Master Project (no
    fixture Comments tab) still uses `create_comments_tab`.
  - **Not** included: `Contact`, whose second "Comments" tab is *not* empty (it holds a
    "More Information" section and contact fields) — a mislabel, tracked separately.

## [1.159.1] - 2026-07-17

### Fixed

- **The "Created from Travel Trip" field (`custom_travel_trip`) no longer sits in the
  wrong tab on Opportunity and Lead.** Its `insert_after` pointed at `source`, a field
  that does not exist on either doctype in this ERPNext version (dangling reference), so
  Frappe stranded it at the end of the form — on Opportunity that put it inside the
  **Hand-Off Process** tab; on Lead, inside the **Comments** tab. It is a read-only
  provenance back-link, so it now anchors next to the external-id provenance field
  (`custom_zoho_crm_opportunity_id` on Opportunity, `custom_zoho_id` on Lead), landing
  in the **Details** tab's More Information / Additional Information section. Fixed in
  the fixtures: `insert_after` repointed in `custom_field.json` and the field inserted at
  the matching spot in each doctype's `field_order` property setter. Verified by
  simulating the resulting form layout against the live meta on both doctypes.
- Audit note: a sweep of all 643 site custom fields for dangling `insert_after` found no
  other of our fields stranded into a visibly wrong tab. Separately, it surfaced a
  legacy **duplicate empty "Comments" tab** (`custom_comments_tab` alongside the real
  `custom_comments` + `custom_comments_field`) on ~13 doctypes; that is a distinct
  cleanup tracked separately.

## [1.159.0] - 2026-07-17

### Removed

- **The Opportunity "Lost Reason" / "Lost To (Competitor)" custom fields are gone.**
  They duplicated capture ERPNext already ships natively on the Lost section of the
  Opportunity form — `lost_reasons` (a Table MultiSelect onto the curated
  `Opportunity Lost Reason` master) and `competitors`. The duplicate `custom_lost_reason`
  Select was only ever added (v1.122.0) because a Property Setter had hidden the native
  `lost_reasons` field, making it invisible to the win/loss KPI work. Worse, since
  v1.149.0 removed `custom_won_reason` the Select's `insert_after` pointed at that
  now-missing field, so Frappe stranded it — and `custom_lost_competitor` — at the
  bottom of the form, inside the unrelated **Hand-Off Process** tab, where nobody found
  it (0 of 780 opps filled it on TEST; 3 of 798 on prod, all "Other" and each already
  carrying an equal-or-better native reason). Removed via
  `patches.remove_opportunity_lost_reason`; no data migrated (verified lossless on both
  sites).

### Changed

- **Loss capture + analytics now run on the native `lost_reasons` field.** The Property
  Setter that hid `lost_reasons` is flipped to visible, so marking an Opportunity Lost
  reveals the reason picker in its proper place (the Lost section). `validate_close_reason`
  now requires at least one native `lost_reasons` row on the transition to Lost (was:
  `custom_lost_reason`). The two Sales KPIs — **Lost to Competitor (90d)** and
  **Loss-Reason Capture (90d)** (same `close_reason_capture_90` key, history carries
  over) — read the native child table instead of the removed Select; both had been
  reporting zero because the old field was unfindable. On TEST they now read 2 and 69.2%.
- **The "Opportunity Loss Reasons" donut is now backed by a Script Report.** A Group By
  dashboard chart can only group on a base-doctype column and cannot traverse the
  `lost_reasons` child table, so the chart is repointed to a new **Opportunity Loss
  Reasons** report (`crm_enhancements/report/opportunity_loss_reasons`) that joins the
  child rows to their Lost parent and counts distinct opportunities per reason.

## [1.158.3] - 2026-07-16

### Fixed

- **Stripe surcharge could never post to the ledger.** `_apply_surcharge` inflated the
  Payment Entry's `received_amount` above `paid_amount` and added a negative deduction
  — but erpnext forces `received == paid` on a same-currency Receive Payment Entry, so
  every surcharged card/ACH payment died at submit with *"Difference Amount must be
  zero."* The surcharge is now booked as income by a **companion Journal Entry**
  (`Dr Deposit/Clearing / Cr Surcharge Income`) while the Payment Entry settles the
  invoice at face value; the deposit account ends at charge + surcharge, matching the
  real Stripe deposit that the payout sweep (WI-040) later moves to the bank. The JE is
  idempotent on the Stripe charge/PaymentIntent id and posts inside the same
  transaction as the Payment Entry, so any failure rolls the whole reconciliation back
  together. Surcharge remains OFF at launch (OD-7); this unblocks the Phase-2 surcharge
  go-live (WI-055). Surfaced by the first true end-to-end card charge on TEST.

## [1.158.2] - 2026-07-16

### Fixed

- **Stripe webhook reconciliation ran as Guest.** The Stripe webhook endpoint is
  `allow_guest`, so the enqueued `reconcile.process_event` job inherited the Guest
  session and `get_payment_entry`'s permission-checked Sales Invoice read raised
  `PermissionError` — silently failing **every** unattended live charge (the Stripe
  Event was left in `Error`, no Payment Entry posted). `process_event` now elevates a
  Guest session to `Administrator` for the reconciliation via a
  `_reconcile_as_system_user` context manager and restores the caller's user
  afterwards; the scheduled retry and manual reprocess (already system users) are
  untouched. Surfaced by the first true end-to-end card charge on TEST.

## [1.158.1] - 2026-07-16

### Fixed
- **Stripe payout Journal Entries date correctly regardless of the server's timezone.** Stripe's `arrival_date` is a Unix timestamp at 00:00:00 **UTC** of the payout's arrival date; the conversion now reads it in UTC explicitly instead of the host's local timezone. On the current (UTC) infrastructure the date was already correct, but a non-UTC host would have shifted a payout's posting date back a day — which for a financial entry could land it in the wrong (possibly closed) period. Added a unit test covering the epoch → date conversion. *(WI-040 hardening)*

## [1.158.0] - 2026-07-16

### Added
- **Stripe payouts now post their own reconciling Journal Entry, so the bank actually ties out.** Card and ACH charges land in the Stripe clearing account gross; Stripe then pays out the accumulated balance to the real bank account net of its processing fees, in lagged batches. Previously nothing booked that sweep, so the clearing account grew forever and the bank never reconciled. On each `payout.paid` webhook (with an hourly backstop poll for missed webhooks) the integration now fetches the payout's balance transactions and posts one Journal Entry — **Dr Payout Bank (net) + Dr Merchant Fees (Stripe's fees) / Cr Stripe Clearing (net + fees)** — self-balancing, sign-safe for refund-heavy (negative) payouts, and idempotent on the payout id. The journalled fee always comes from Stripe's data (the card/ACH rate constants only raise a soft variance flag); a payout carrying anything beyond charges/refunds/fees (a dispute or adjustment) still posts but is flagged for an Accounts Manager to reconcile the customer side. `payout.failed` raises an alert and books nothing. Two new fields on **Stripe Payments Settings** — *Merchant Fees Account* and *Payout Bank Account* — configure it; unset, payout reconciliation stays off. The clearing account nets to zero per payout once the customer Payment Entries and the refund reversals (a later change) are posted. *(WI-040)*

### Fixed
- The bench-free Stripe unit suite (`test_stripe_payments.py`) now runs in CI — like the QuickBooks suite before it, it existed but was wired into no CI step, so it ran nowhere.

## [1.157.1] - 2026-07-16

### Fixed
- **The hourly QuickBooks Online sync jobs no longer race each other into intermittent failures.** The three QBO jobs — proactive token refresh, CDC change-poll, and failed-run retry — all fired at the top of the hour together. Each one writes the single *QuickBooks Online Settings* document (the token refresh in particular must save through the document so the encrypted token fields are re-encrypted, so it can't use the lightweight `db.set_value` path the cursor writes use), and when two of those saves landed within the same fraction of a second the loser aborted with a `TimestampMismatchError` — roughly two CDC runs a day showed up as "Failed" even though the sync was healthy (the next hourly run always caught up via the stored cursor, so no data was ever lost). The three jobs are now staggered across the hour — token refresh at :00, CDC poll at :20, retry at :40 — so their Settings writes can't collide, and the refresh still runs before the poll that depends on a fresh token. No behavioural change beyond timing; every one of these jobs already self-throttles and no-ops while disconnected, and all three are removed wholesale at QuickBooks retirement (WI-052).

## [1.157.0] - 2026-07-14

### Changed
- **The two vendor-payment approval workflows now enforce a real two-person rule (still dormant).** The shipped `Purchase Invoice Approval` and `Payment Entry Approval` workflows had `allow_self_approval` on every transition, which would have let whoever keyed a bill approve their own payment. The **Approve** and **Reject** transitions (the Accounts-Manager decisions) now forbid self-approval, while the preparer's own **Submit for Approval** transitions keep it (so the person who raised a draft can still send it up for review). Both workflows remain `is_active = 0` — nothing changes behaviourally until they are activated at cutover (WI-044). *(WI-015)*
- **The `project` column now shows in the Purchase Order and Purchase Invoice line-item grids** (Property Setters), so job costing is visible at a glance while entering purchase lines. This is the visibility half of the change; whether `project` becomes *mandatory* on PO lines is deferred to the accountant. *(WI-014)*

### Fixed
- **The `PRJ-` project-numbering scheme is now version-controlled.** Every project is named `PRJ-#####` by a Document Naming Rule that existed only as a hand-created database record — a fresh site, a restore, or a second company would have silently fallen back to the stock `PROJ-.####` series and broken naming continuity (Drive folder names, PRJ- references, the migration's project IDs). An idempotent seed patch now establishes that rule on any site that lacks it, starting its counter after any existing `PRJ-` projects so it can never re-mint a name; sites that already have the rule (production/test) are untouched, including their live counter. The stale `naming_series` fallback default was aligned to `PRJ-.#####` for consistency. *(WI-009)*

## [1.156.2] - 2026-07-14

### Added
- **The migration's target Chart of Accounts design + the 359-row mapping workbook (docs only — nothing imports until the WI-029 cutover-window rebuild).** `docs/migration/chart_of_accounts.csv` is the full 213-account numbered chart in the native Chart of Accounts Importer format — validated by the actual importer on the test site with zero errors — with per-stream income (Design/Build/Service/Events/Products) matched by per-stream COGS including first-class Subcontract Labor lines, the Stripe Clearing / Undeposited Funds / Merchant Fees payment plumbing, Utah sales- and use-tax liability sub-accounts, payroll summary-JE landing spots, perpetual-inventory structure for Phase 2, exactly one Temporary opening account, and a Historical P&L Offset equity account — all company-agnostic (no "SF" in names). `docs/migration/coa_mapping.csv` maps every one of the 359 production accounts (264 MAP / 95 RETIRE with reasons). `docs/migration/COA_DESIGN.md` carries the rationale, the Company default designations, and the explicit CPA ratification checklist (tax bucket shape, use-tax treatment, meals/entertainment deductibility, LLC member equity, the retirement list). Adversarially reviewed (mechanical constraints + accountant lens; all 11 findings applied, including Use Tax Payable and the meals/entertainment deductibility split).

## [1.156.1] - 2026-07-14

### Fixed
- **Custom Field fixtures now actually sync on production again.** Discovery: three Travel-Management custom fields target hrms-app doctypes (`Employee Advance`, `Expense Claim`, `Vehicle Log`) that were never installed on the production/test sites; importing any one of them raises DocType-not-found, and Frappe's fixture sync responds by **silently skipping the entire `custom_field.json` file** — so every custom-field fixture change since those records entered the file never reached production (this is also the long-unexplained cause of past "fixture didn't apply on deploy" incidents that needed backstop patches, e.g. v1.68.0). The three records now live in their own `fixtures/custom_field_hrms.json` (fixture sync skips a failing file per-file, so it degrades gracefully on hrms-less sites and still applies on dev benches that have hrms), the fixture export filter excludes those doctypes so `bench export-fixtures` cannot reintroduce them, and the deploy's migrate re-imports the cleaned `custom_field.json` in full — landing, among the accumulated drift, the v1.156.0 "Events" form labels and the Lead Service Interest options.

## [1.156.0] - 2026-07-14

### Changed
- **The "Rent" value stream is now called "Events" everywhere** (migration decision OD-3, resolved 2026-07-14: rentals and events are one value stream, and the term changes). A one-time migration patch renames the three master records — **Project Type** `Rent`→`Events` (carrying every project's `project_type` with it), the **Value Streams** multiselect master, and the Opportunity **Rent tag** — and backfills the plain-string places the rename can't reach (Lead "Service Interest" values, cached `_user_tags`, process-map text). Everything that *compared against* the old name was updated in the same release so nothing breaks silently: the Closed-Won handoff's stream priority, the KPI snapshot rental queries (which would otherwise have quietly reported zero rentals), the Projects Dashboard / Priority Overview / Gantt stream lists, the Opportunity tag sync and kanban color, the contract scope builder, and the AI email/SMS guideline prompts. Form labels follow suit ("Events Schedule", "Events Scope", "Events Customer Requests", "Events Deliverables", "Events Guidelines", and the Lead Service Interest option). Internal identifiers deliberately keep their old names for stability (the `Rent Customer Requests` / `Rent Deliverables` child DocTypes and every `custom_rent_*` fieldname) — labels only. Idempotent patch; fresh sites and re-migrations are safe.

## [1.155.1] - 2026-07-14

### Added
- **The QuickBooks→ERPNext migration master plan now lives in the repo (documentation only — no code or behavior changes).** `PLAN.md` carries the plan built against the live systems on 14 Jul 2026: every planning-brief figure re-verified with discrepancies flagged (prod's accounting is an unposted QBO draft mirror; test already piloted the opening balances), a native-first audit of every requirement, the phase plan with the binding cutover-window ordering, the dependency graph and critical path to the 2027-01-01 cutover, a risk register, and the test→prod promotion strategy. `decisions/OPEN-DECISIONS.md` records the seven business decisions **with their 14 Jul 2026 resolutions** (no JDH company; follow-Utah-law stream-differentiated tax with the CPA's written matrix as the go-live sign-off gate; Rent renamed to Events; segment = project attribute with customer fallback; Jan 1 committed; draft-mirror bulk delete ratified; no card surcharge at launch). `work-items/` holds 65 self-contained work items (WI-001..WI-065, each with native-first check, verified field names, machine-checkable acceptance criteria, and rollback; WI-061 JDH is on hold per OD-1). Execution is tracked as Tasks under project **PRJ-00739 – ERPNext Accounting Migration** on the live site, mirroring this register including its dependency graph.

## [1.155.0] - 2026-07-10

### Changed
- **The Global Search "DocTypes" section now offers each DocType's standard views (List, Report, Dashboard, Kanban, Calendar/Gantt, Tree, …), not just one link.** Building on v1.154.0: instead of a single "List" entry per matched DocType, the results now expand each into every view frappe's own list-view switcher would offer — **List**, **Report** (when you can access it), **Dashboard**, **Kanban** (opened via frappe's real board lookup / "create board" flow), **Calendar** and **Gantt** (when a standard calendar is registered for the DocType), **Tree** (for tree DocTypes), and **Image**/**Map** (when the DocType's meta supports them). Availability uses the same conditions as `list_view_select.js`, all evaluated client-side so results stay instant. It never routes to the DocType definition form for editing — only to data views; single-type DocTypes (System Settings, etc.) keep a single link to their settings form, since that is their only view. Same gating as before (unfiltered global-search mode only; drill-down filters and `#tag` searches untouched). Implemented in `public/js/global_enhancements/global_search_doctypes.js`.

## [1.154.0] - 2026-07-10

### Fixed
- **Desk Global Search now lists matching DocTypes, so pressing Enter in the search bar no longer "loses" them.** Typing in the top desk search bar (awesomebar) shows DocTypes live in the dropdown, but pressing **Enter** opens Frappe's full Global Search page, which only searches document *content* (the `__global_search` index) and never listed DocTypes — so a DocType you saw a moment ago vanished, and a DocType name with no matching document content produced "No Results found". The Global Search results page now leads with a **DocTypes** section built from the *same* `frappe.search.utils.get_doctypes()` the dropdown uses (one primary navigation entry per matched DocType — its List/Tree, or the form for single-types), rendered through the dialog's existing `fetch_type: "Nav"` support and clickable straight through to the list. Injection is scoped to unfiltered global-search mode: drilling into a single DocType's content via a filter pill, and `#tag` searches, are left untouched. Implemented as a focused `SearchDialog.parse_results` prototype patch in `public/js/global_enhancements/global_search_doctypes.js`; complements the existing awesomebar live-search enhancement.

## [1.153.0] - 2026-07-10

### Added
- **Package Dispatch — an official form for sending packages out.** A new self-contained module (surfaced on the **Shipping** desk workspace) replaces the messy, handwritten one-off with a repeatable document. Each dispatch captures: **what's being sent, with a value per item** — add a line per item and optionally pick a catalog **Item** to pull its name and selling value (Standard Selling price, falling back to the Item master rate), or type a one-off description and value by hand; the form totals a **Total Declared Value** (your reference for how much to insure) and shows an "Insure for …" headline. A **structured recipient address** (name, company, street, city, state, ZIP, phone) you type — no handwriting, searchable later — with an optional **Customer** picker that auto-fills from their primary address. A plain-English **"what's being sent" summary** to tell the store, auto-written from the item list if left blank. And **delivery tracking** — store/carrier, tracking number, shipped/delivered dates, and a Not Shipped → Shipped → Delivered status derived from those dates, plus a **Mark Delivered** button on submitted dispatches. Submit to finalize it as a locked official record; print the **Package Dispatch Sheet** to hand over at the counter or keep on file. Permissions go to **System Manager** and a new insert-only **Dispatch User** role. The two auto-fill conveniences (catalog value + customer address) are gated by a default-OFF **Package Dispatch Auto-fill** switch (ERPNext Enhancements Settings → Package Dispatch) — the form, totals, print sheet and submit all work regardless; flip it on to enable auto-fill (no deploy needed).

## [1.152.0] - 2026-07-10

### Added
- **Drag-to-resize column widths on the Projects Dashboard tables and the Tasks tree.** On the **Projects Dashboard** Custom HTML Block, the three list tabs (Priority Overview, Active Internal Projects, Completed Projects) now let you drag a column header's right edge to widen/narrow it; a **Reset widths** button in each tab's toolbar restores the defaults. On the **Tasks tree** (the `custom_tasks_html` HTML field on the Project form's Scope tab and the Project Dashboard "Tasks" tab), every column except the elastic Task column and the tiny Actions column gained the same drag handle, with a **Reset column widths** entry in the existing Columns (⧉) dropdown. Widths are saved **per user** in the browser (localStorage), alongside the existing per-user show/hide-columns choices — one person's sizing never changes anyone else's. Implemented as a reusable `ColumnResizer` component (sibling of `ColumnSelector`); the dashboard tables switch to a fixed table layout so the chosen widths are authoritative and the tables scroll horizontally once the columns outgrow the widget, while the flex-based tree redistributes width within the widget.

## [1.151.4] - 2026-07-10

### Fixed
- **CI now actually runs the bench-free QuickBooks Online test suite.** The `unit-tests` job runs suites via `python -m unittest` with an explicit module list, which cannot collect `tests/test_quickbooks_online.py` — its 100+ tests are plain pytest functions (the `monkeypatch` fixture), so the whole QBO suite (mapping, ordering, signature, datetime, preflight, result tracking) ran nowhere in CI and one test sat silently broken for weeks. The job gains a dedicated `python -m pytest` step for it (pytest added to the job's installs; all unittest steps unchanged), and the tests README now says where a new bench-free suite must be registered. A sweep confirmed no other pytest-only test files are missing from the matrix. (The underlying `.flags` doc-stub fix for `test_save_or_manual_review_parks_validation_errors` already landed via #571/v1.151.3; this PR is what makes CI enforce it.)

## [1.151.3] - 2026-07-10

### Fixed
- **QBO sync no longer re-saves documents that didn't change.** The already-linked update path applied every mapped value and unconditionally `doc.save()`d, so a full import or CDC replay re-saved 1000+ value-identical Customers/Suppliers/Items/Projects per run — churning `modified`/`modified_by`, firing `doc_update` realtime events at anyone viewing those records, and (before v1.150.2) minting phantom Version rows. `apply_values` now reports whether any value actually moved (same normalization as conflict detection, with a numeric fallback so `1` vs `1.0` isn't "changed"; child tables conservatively always are, so transactions still save), and a value-identical re-sync skips the document save entirely — it only refreshes the QBO Sync Mapping bookkeeping (SyncToken/cursor, conflict status) and reports a new `unchanged` action (uncounted in sync-log tallies, like `skipped`).
- **Live sync never creates Projects from QBO jobs anymore (link-only).** A new QBO job (sub-customer) still auto-links to its existing `PRJ-###` Project, but when no matching Project exists the sync now consolidates the job onto its top-level parent Customer (`job_merge_no_project`, the same policy as the colon-bug remediation — transactions roll up to the parent untagged) instead of minting a Project, and parks the job for manual review when the parent Customer isn't imported yet. Once a matching Project appears later, the existing doctype-flip guard flags the mapping for relinking via the job remediation tool. This was the remaining blocker for re-enabling the paused QBO sync.
- Repaired `test_save_or_manual_review_parks_validation_errors` (its doc stub predated the `ignore_links` flag and had been failing silently — CI doesn't run the pytest-based QBO suite).

## [1.151.2] - 2026-07-10

### Added
- **"Hand-Off Process Coverage" report** (CRM Enhancements → Reports, `ref_doctype` Opportunity). One row per Opportunity that has a linked Project, showing whether that project's hand-off tracker (PRO-0204 Project Process Steps) has been started, the step count, and the currently-live step. It surfaces the population that used to render a blank "Hand-Off Process" tab — Closed-Won opportunities whose linked project has no started tracker. Default filters (Opportunity Status = Closed Won, Tracker = Not Started) land exactly on that set, so the report doubles as an audit of hand-off coverage. Roles: System Manager, Sales Manager, Sales User.

## [1.151.1] - 2026-07-10

### Fixed
- **The Opportunity "Hand-Off Process" tab no longer renders blank when the linked project's tracker was never started.** The tab mirrors the linked Project's first three hand-off steps; when that project had no steps (which is the normal state for in-flight projects — they're not auto-seeded and opt in via the Project's "Start Hand-Off Process" button, plus anything imported or created before the automation switch was on), the client blanked the field instead of falling back, so the tab looked empty/broken. On production this affected **47 of the 56** Closed-Won opportunities that have a linked project. The client now falls back to a **project-aware derived view** (Mark Won ✓, Hold Hand-Off Meeting = live step, Create Project ✓) with a pointer to the linked project, and also renders the derived view if the mirror call errors — so the tab is never blank for a saved Opportunity while the master switch is on. No data change: the underlying projects keep their (empty) trackers until someone starts them.

## [1.151.0] - 2026-07-10

### Added
- **Contact & Address quick-entry dialogs — creating a contact no longer leaves the page.** Every "new Contact/Address" entry point (the Contacts & Addresses section buttons, list **+ New**, the awesome bar, a link field's *Create a new…*) now opens a quick-entry dialog instead of routing to the full form. Opened from a Customer, Opportunity, Project, Master Project, Supplier, Lead or Prospect form, the dialog pre-fills the **Account**, shows *"Will be linked to …"*, and links the new record automatically — from an Opportunity/Project it links to **both** the customer and the Opportunity/Project itself, with the party row first so the record keeps its familiar `Name-Customer` naming. An **Edit Full Form** escape hatch remains, and the injected links survive into the full form. Gated by a new default-ON **Contact & Address Quick Entry** toggle (ERPNext Enhancements Settings → Contacts & Addresses); off restores the stock full-form flow on the next page load.
- **The directory widget can now create, not just link.** The Contact/Address directory on Customer/Supplier/Opportunity/Project/Master Project forms gained primary **New Contact / New Address** buttons (quick-entry dialogs; the New Address button defers to the Geolocation autocomplete dialog when that feature is on) — the old "Add" button is now labeled **Link Existing**.

### Changed
- **Contact's "Account" field is editable now** and kept in true two-way sync with the Links grid server-side (`contacts_ux.sync_contact_account_links`, on every save path including list-view bulk edit): changing the Account **replaces** the contact's Customer link in place (other links untouched), clearing it removes the link, and editing the grid updates the field. Replaces the old read-only client-side mirror, which only persisted when someone happened to save the form. A one-time patch normalizes every existing Contact's Account to its first Customer link (stale and orphaned values corrected). The sync is deliberately NOT behind the toggle — an editable field with a disabled sync would silently drift.

### Fixed
- **Contacts & Addresses section no longer shows stale data after you create or edit a contact via the full form.** Frappe routes back to the party form without reloading it, so the section re-rendered from the old server payload. Contact/Address saves now push fresh directory data into every open party form they link to (no reload — unsaved edits on the party form are preserved), and dialog-created records refresh both the stock section and the directory widget in place.
- **Opportunity's directory widget now includes the party's contacts/addresses.** The source scan checked `party_type`, but Opportunity's discriminator field is `opportunity_from`, so the Customer/Lead/Prospect behind the deal was missed entirely.
- The directory widget re-registered its form event handlers on every refresh, piling up duplicate handlers that all re-rendered the tables on each customer/party field change.

## [1.150.3] - 2026-07-10

### Fixed
- **Triton caller_resolved replays no longer touch unchanged Customers/Contacts.** The gateway replays `update_caller_info` on every call, usually with the name already on file, and the handler unconditionally rewrote `customer_name` and the Contact's first/last name — bumping `modified` on both records each call, which broadcast a `doc_update` to any open desk form and set up `TimestampMismatchError` for users mid-edit. The handler now compares before writing and skips entirely when nothing differs, and the response's `updated` flag reports whether anything was actually written (it previously just meant "not established").

## [1.150.2] - 2026-07-10

### Fixed
- **No-op Customer re-saves no longer manufacture "last activity" Version diffs.** The `set_last_activity` before-save hook stamped `custom_last_activity_date = today()` on **every** Customer save, so even a value-identical background re-save (bulk edits, sync/webhook replays — e.g. the 2026-07-10 "Unknown Caller" cleanup burst) produced a genuine one-field diff, a Version record, and an "updated" realtime event. The stamp now fires only on create or when the save changes something besides the stamp itself (measured with the same diff engine Frappe's Version feature uses) — so a truly no-op `doc.save()` mints no Version at all, and a manual edit of the date survives instead of being clobbered to today. The inactivity-reminder semantics are unchanged: any real edit still counts as activity.

## [1.150.1] - 2026-07-10

### Fixed
- **Live-collab forms no longer show phantom "Updated by <name>" toasts for background writes.** Frappe's `doc_update` event fires for *every* server-side save — scheduler jobs, webhooks, API sessions, list-view bulk edits — and carries no author, so the collab layer's save toast surfaced any background write to an open document as if a person had just edited the page (e.g. the 2026-07-10 bulk cleanup of "Unknown Caller" customer groups toasted 30 times to anyone with one of those Customers open). The toast is now **presence-gated**: it only shows when the writer demonstrably has the same document open (Frappe's `doc_viewers` roster, live collab field edits, or focus events — with a 30s grace window so a peer who saves and immediately closes the form still toasts). Administrator/Guest writes never toast (background jobs run as Administrator), hidden tabs skip the 3s alert, and a stale-fetch race (doc re-saved between event and author lookup) no longer misattributes the author. Background saves keep the existing behavior minus the toast: clean forms silently reload, dirty forms silently merge.

## [1.150.0] - 2026-07-10

### Added
- **HR joins the KPI Dashboards module as the 9th department** — aggregator, dept-locked cockpit workspace, sidebar, native charts, and a manual-entry doctype. Grounded-data notes: the hrms app is **not installed** on this site (no Attendance/Leave/Recruiting/Payroll/Appraisal tables; payroll lives in QuickBooks), so the 12 automatic KPIs read only the fully-populated `tabEmployee`: active headcount, full-time count, employment-type completeness, hires/separations (90d + 1y), net headcount change, **turnover rate (12m)** (two-point average headcount reconstructed from joining/relieving dates — pure helper `metrics.turnover_rate_pct`, unit-tested), average tenure, tenure at exit, and span of control. Small-n stance for a 14-person team: headline KPIs are counts and the only rate windows are 365-day (one exit moves turnover ~7 points); demographic KPIs are deliberately excluded for privacy.
- **3 workforce-time KPIs that wake up with the time-kiosk rollout** — field labor hours (30d) and distinct field staff clocking in (30d) from Job Interval, plus submitted Timesheet hours (30d). All guarded and sum-based, so they stay silent (skipped, not zero) until real intervals/timesheets exist.
- **HR Stat Entry doctype** (KPI Dashboards module) — the one-row-per-month manual paste for **Open Positions** and **eNPS** (there is no Job Opening doctype or survey tool on this site). Month normalizes to the first of the month (autoname enforces one row per month); eNPS 0 is documented as "not surveyed"; an entry older than the previous calendar month flags the source stale (Watch badge). Linked from the KPI Setup sidebar group and the KPI Overview workspace.
- **HR Dashboard workspace** (`/app/hr-dashboard`, sequence 57 with Executive bumped to 58 so Executive stays last) with the KPI Cockpit auto-locked to HR by route, plus an "HR" nav link and the HR Stat Entry setup link in all 10 workspace sidebars. Executive's rollup now re-surfaces the HR turnover rate (its own headcount/revenue-per-employee KPIs are unchanged).
- **Access is gated to HR Manager + HR Team** — deliberately not HR User, which every employee on this site holds. HR Team is an instance-created role; a new insert-only patch (`seed_hr_team_role`) makes fresh sites match so the workspace/doctype role references never dangle.
- **"HR Overview" native dashboard** — Active Headcount by Department (donut), Active Headcount by Employment Type (donut), Hires by Month (bar), fixture-filtered by name like the other shipped dashboards.
- Docs: `docs/KPI_DASHBOARD_DESIGN.md` gains the full 17-KPI **HR (People)** section (12 Auto / 3 Semi / 2 Manual) with data-gaps and minimal-manual-entry guidance; catalog now 131 KPIs across 8 departments.
- Rollout: everything rides the existing `kpi_dashboards_enabled` master switch (default off; already enabled on production). Operator follow-ups: assign the HR Team role, fill the 3 blank employment_type values, optionally seed KPI Targets (turnover ≤ 15, completeness = 100, open positions = 0) and a first HR Stat Entry row.

## [1.149.0] - 2026-07-10

### Removed
- **The "Won Reason" field is gone from Opportunities.** Marking an Opportunity **Closed Won** no longer captures or requires a reason — the `custom_won_reason` Select field (Price / Relationship / Product Fit / Timing / Other) and its required-on-win validation were both removed. Existing sites drop the leftover field (and its stored values) on migrate via `patches.remove_opportunity_won_reason`. **Lost Reason is unchanged** — it's still shown on Lost opportunities and still required when marking an Opportunity Lost.

### Changed
- The Sales **Close-Reason Capture (90d)** KPI is now **Loss-Reason Capture (90d)** — with won reasons gone, it measures the share of *Lost* opportunities that recorded a Lost Reason (same `close_reason_capture_90` key, so the history carries over). The **Opportunity Loss Reasons** donut and **Lost to Competitor (90d)** KPI are unaffected.

## [1.148.2] - 2026-07-10

### Fixed
- **Customers auto-created from incoming Triton calls no longer default to the "Government" customer group.** Same arbitrary "first non-group leaf" fallback as the territory bug — with no Customer Group configured in Selling Settings, the unknown-caller auto-create landed every caller in **Government**. `_default_customer_group()` now returns the Selling Settings default when it's a usable (non-group) leaf, and otherwise leaves the field **blank** (via `ignore_mandatory`) instead of picking an arbitrary group. Also fixes the `update_caller_info` rename-create path, which hardcoded `"All Customer Groups"` — a group node that erpnext v16 rejects outright.

## [1.148.1] - 2026-07-10

### Fixed
- **Customers auto-created from incoming Triton calls no longer default to the "Asia" territory.** The unknown-caller auto-create picked the first non-group Territory when Selling Settings had no default configured, which landed on **Asia**. New callers are now created with **no territory** set (the field is left blank via `ignore_mandatory`), on both the `get_caller_info` auto-create and the `update_caller_info` rename-create paths.

## [1.148.0] - 2026-07-07

### Changed
- The Daily Horoscope widget's default sign is now **Capricorn** (was Leo). Applies only to browsers that haven't picked a sign yet — a sign chosen via the widget's dropdown is remembered per browser (localStorage) and always wins.

## [1.147.0] - 2026-07-07

### Fixed
- **Daily Horoscope widget works again — the free horoscope API moved.** `horoscope-app-api.vercel.app` now 308-redirects to **freehoroscopeapi.com**, which renamed the response field (`horoscope_data` → `horoscope`). Our fetch followed the redirect but parsed the old field, so the widget always showed "No horoscope available." The default API base is now `https://freehoroscopeapi.com` (skipping the redirect hop) and the parser accepts both field names, so a custom `horoscope_api_base` pointing at either style keeps working. No configuration needed: leave *Horoscope API Base URL* blank to use the new default.

## [1.146.0] - 2026-07-07

### Fixed
- **Custom HTML Block widgets actually render on v16 workspaces now.** The v16 desk renders a workspace's `custom_block` content entry only when the Workspace doc *also* carries a matching **Workspace Custom Block child row** (the server builds the block payload from `doc.custom_blocks`, not from content) — and every placement we'd ever made wrote content only, so the KPI Cockpit and the six Finance widgets rendered as empty divs everywhere (Home included; this was the real "can't see Department KPIs on Home" root cause). Fixed in both layers: the dashboard workspace JSONs now ship the child rows for their blocks, and the seeder's `_append_custom_blocks` gained `_ensure_custom_block_rows`, which idempotently inserts missing rows even where content already lists the block — repairing Home and any other pre-existing placement on the next migrate.

### Changed
- **The 8 department dashboards (Finance, Sales, Operations, Design, Production, Marketing, Product, Executive) are now real module pages.** They previously lived as loose site workspaces (no module, `app: erpnext` or none, sequence 0 — the Finance one was hand-made in February and the rest piggy-backed on it), so they didn't group with the app's other sidebars in the desk's app-scoped sidebar. Each now ships as a standard **KPI Dashboards**-module workspace JSON (`kpi_dashboards/workspace/`) with `app: erpnext_enhancements`, its department role gate (per `api/kpi.py` DEPARTMENT_ROLES + System Manager) baked in, and sequence 50–57 so the dashboards sit together. On migrate, Frappe's workspace sync replaces the existing site docs in place — names, routes, and the KPI Cockpit's route-based department auto-lock are unchanged, and the Finance Dashboard JSON carries its exact production layout (6 finance widgets, cockpit last) so nothing moves visually.
- The `setup_department_kpi_workspaces` patch (v1.140.0) is retired — fresh installs now get the dashboards, roles included, from JSON sync. The Custom HTML Block seeder still appends the cockpit/widget blocks as a backstop if a site's workspace loses one.
- **The dashboards now ship real sidebars.** The v16 desk resolves a workspace route to the *Workspace Sidebar doc named exactly like the workspace* — and Frappe's v16 upgrade had auto-created one-link stubs ("Home" → the workspace itself) for each dashboard, which is why opening the Finance Dashboard showed a useless one-item sidebar. Nine standard sidebar JSONs now ship in the app-level `workspace_sidebar/` folder (one per dashboard + a "KPI Dashboards" one that also suppresses the auto-generated module sidebar), each carrying the same items: KPI Overview (the cockpit-with-picker workspace) → the 8 department dashboards (role-filtered per user by the desk) → a collapsed *KPI Setup* group (KPI Target, Marketing Spend, KPI Snapshot, Enhancements Settings). Sync replaces the site's stubs in place (verified untouched on production), and because sidebars with those names now exist, Frappe's `after_app_install` stub generator can never recreate the junk.
- **Stale browser sidebar picks self-heal.** The desk remembers which sidebar to show per route in localStorage and trusts it blindly (entries are appended, never validated), so browsers that had visited a dashboard would keep resolving to the now-deleted stub and the sidebar would silently stop switching. A new global desk script (`global_enhancements/sidebar_pref_heal.js`) prunes remembered names that no longer exist in the boot payload.
- Trade-off note: the dashboards are no longer freely site-editable across releases — a future change to a dashboard's JSON (with a bumped `modified` stamp) re-imports it and replaces that site's layout edits. Repo is the source of truth, same philosophy as the widget seeder.

## [1.145.0] - 2026-07-07

### Added
- **Quick Entry across the common masters.** The lightweight "+ New" dialog (list views, awesomebar, "Create a new…" inside link fields) now works on 22 frequently-used doctypes, each showing every required field plus a curated handful of high-value ones. Newly enabled: **Opportunity** (with "Opportunity From" defaulting to Customer and correctly restricted to Lead/Customer/Prospect inside the dialog), **Employee, Warehouse, Item Group, Customer Group, Supplier Group, Serial No, Event** — and **Item + Customer are re-enabled** (reversing the old deliberate disables) with curated dialogs. Existing dialogs got useful fields added: Supplier (group/country/tax ID), Lead (last name, service interest), Project (customer, stage, requested dates), Task, Issue, Batch, ToDo, Note — and **Payment Term's dialog, which shipped broken upstream (enabled but empty, so it silently never opened), now works**. Contact & Address are deliberately excluded (dialog-created records would be orphaned — their party links are only wired on the full form). Details in `docs/UX_QUICK_ENTRY_AND_FORM_LAYOUTS.md`.

### Changed
- **The 7 procurement forms redesigned around the 90 % data-entry case** — Item, Material Request, Purchase Order, Purchase Receipt, Purchase Invoice, Supplier Quotation, Request for Quotation. First tab = identity block → items grid → taxes → totals; the five purchase documents now share one tab grammar (**Details → Contacts & Addresses → Terms → More Info → Connections → Comments**) so learning one form teaches all; auxiliary sections (accounting dimensions, pricing rules, tax breakup, printing, auto-repeat, status trackers) moved to More Info as collapsible sections. Purchase Invoice's required **Credit To** moved up to the first tab. Item's Details tab opens with the create-an-item essentials and its domain tabs are reordered most-used-first. Nothing deleted or hidden — except the **duplicate empty "Comments" tab on Purchase Order / Supplier Quotation**, which is fixed. Also repairs the stale pre-v16 `field_order` arrays (Purchase Invoice was missing 14 v16 fields; MR/PI carried phantom fieldnames; Item was missing the pump-spec fields).
- The `field_order` fixtures are now **generated** from declarative specs by `scripts/layout/generate_field_order.py` (hard lints: exact field coverage, no stray column breaks, no empty tabs, required fields on the first tab). **Re-run it after every ERPNext upgrade** to keep the arrays fresh.

## [1.144.0] - 2026-07-07

### Added
- **Water Feature Design "Design Health" — missing information and constraint/legal violations are now typed, counted, and findable.** The calc engine's free-form warning strings become structured **design issues** — `blocker` (legal/safety/physical failure: velocity over the legally-defensible limit, pipe pressure under-rated, VGB/ANSI-APSP-16 entrapment risk, cavitation, water hammer over rating), `warning` (engineering limits: recommended-velocity band, under-sheeted weir edge, component over rated flow, unresolved/flow-only pump match, CYA chlorine floor), and `info` (advisories) — each with the section it belongs to, the exact child row it points at, a fix hint, and its source citation (`water_engineering/issues.py`; derived entirely from persisted state, so submitted designs' numbers are untouched):
  - **Design Health panel** on the form's live dashboard: severity chips, issues grouped in design order with **click-to-jump** to the offending row (flash highlight), expandable fix hints + citations, and a per-warning **Acknowledge…** action that records who/when/why (new `Water Design Issue Ack` rows; stale acks drop automatically when an issue clears, deleting a row un-acknowledges). Saved designs with blockers show a red headline on every tab.
  - **Itemized readiness checklist** behind the old completion %: two gates — *To calculate* (basin → features → tiers → piping → pump) and *To issue the package* (title, electrical loads, chemistry, drain, fittings captured, zero blockers, all warnings acknowledged, per DOC-0121) — each missing item says *why* it's needed and links to its field. Persisted as a new `issue_ready` flag + `blocker_count`/`warning_count` counters.
  - **List view + workspace triage**: red *N Blockers* / orange *N Warnings* / green *Package Ready* indicators, `blocker_count` as a standard filter, and two Water Engineering workspace number cards (*Designs with Blockers*, *Designs Ready to Issue*).
  - **Per-segment pipe-pressure status**: every discharge segment row now carries `pressure_status` / `pressure_margin_psi` (system TDH vs. the pipe's temperature-derated rating, DOC-0049 Pipe Specs) — previously buried in the audit trail.
  - **New engine surfacing checks** (values unchanged, all cited): a *Below Self-Cleaning* velocity band (< 0.5 fps — solids settle; `size_pipe` now recommends the smallest run with a flushing advisory for tiny flows instead of a misleading "no size fits"), a drain-slope band warning (outside 1/16–1/2 in/ft, DOC-0119), a drain full-pipe divergence note (why DOC-0119's tables read ~2–3× the engine's conservative half-full figure), and **CYA/free-chlorine inputs** on the Treatment tab so the DOC-0119 chlorine floor (≥ max(2, 7.5 % × CYA) ppm) actually warns.
  - **Fitting Schedule** (DOC-0121): fittings/components aggregated across segments — new table in the *Schedules* print format and in the design-state payload; the *Results* print format gains a **Design Review** section (open issues with citations + the acknowledgement sign-off table).
  - **Form reorganized to the documented design procession** (DOC-0119): Concept & Site → Design Health → 1·Basin & Volume (turnover moved beside the basin) → 2·Water Features → 3·Piping (pipe material / Hazen-Williams C / static lift moved next to the segments) → 4·Pump & Electrical → 5·Treatment & Drainage → Review & Issue (sign-offs + results + audit trail). Field-level unit/why-this-default help added throughout.
  - The wizard page's Designs pane and the Triton `water_design_status` MCP tool surface the same typed issues/readiness (all API changes additive — `warnings`, `next_inputs_needed`, canvas, and rollup shapes unchanged).

### Changed
- **BEHAVIOR: submitting or setting a Water Feature Design to Reviewed/Issued is now hard-gated.** Blocker-severity issues (legal/safety) block Reviewed, Issued, and submit outright; Issued additionally requires the package-readiness gate and an acknowledgement on every open warning. Previously an over-limit design submitted with only a soft popup. The gate fires on the status *transition* (setting a design to Reviewed/Issued), so documents already sitting in those states remain saveable. `has_warnings` also widens to count chemistry/drainage warnings (it previously only tracked hydraulic-spine warnings), so some designs will newly show as warned on their next save — that is the fix for the audit's "CYA floor never warns" gap. The re-definition also re-baselines the two Design-KPI metrics that filter on it (*Designs w/ Warnings*, *Clean-Issue Rate*): expect a one-time discontinuity in the nightly KPI snapshots after deploy. A migration patch backfills the issue counters on existing designs from their persisted audit rows (no recompute; submitted documents byte-identical).

## [1.142.0] - 2026-07-02

### Added
- **Product Configurator — configure-to-order tool, seeded with the PDT-0040 STILLWATER E-Stop.** A **Configurable Product** defines a product's option modules (base unit, pick-one choices, 0–N quantity modules) with per-module parts cost, labor hours (or a flat labor amount) and part-number digits, plus its component parts and condition-driven build-instruction templates. A **Product Configuration** picks the options with a **live price preview** and computes the part number (`PDT-0040-1-1-1-2-0`), a module-by-module pricing breakdown (labor rate × hours, 30% markup, additional-cost passthrough), the exploded parts list, and config-aware build instructions ("2 timers → use triple-pole terminals", "insert 3 cable glands"). On demand it generates the native ERPNext records **atomically**: the configured **Item** (part number = item code), a **submitted default BOM** from the parts list (duplicate components aggregated, costed by valuation with seeded unit costs), and a **Standard Selling Item Price** — plus, one click on the product, the ~23 component **Items with Suppliers** (Amazon, Automation Direct, Grainger, Digikey, Galco, …) and buying prices. Three print formats ship per configuration: shop-floor **Build Instructions**, **QC Checklist** with sign-off, and a **Pricing Summary** quote sheet. The PDT-0040 seed reproduces the source pricing workbook's own worked examples to the third decimal (goldens 1685.008 / 1512.979, verified bench-free in CI), fixes the workbook's mounting-digit bug (digit no longer multiplied by e-stop qty; the cost still is), and future products are added entirely through the UI. Gated behind the new **default-OFF** `product_configurator_enabled` switch (ERPNext Enhancements Settings → Product Configurator): configurations, pricing and printing always work; only ERPNext-master generation is switched. New "Product Engineer" role; docs in `docs/PRODUCT_CONFIGURATOR.md`.

## [1.141.0] - 2026-06-30

### Added
- **Document Merge tool — consolidate any two non-submitted documents of the same doctype.** A System Manager gets a **"Merge into…"** button on any non-submitted form (and a **"Merge Selected…"** bulk action in list views) that absorbs a duplicate ("loser") into the record you keep ("survivor"): every reference on the site is repointed at the survivor — standard **Link** fields, **Dynamic Links**, **child-table** references and **Single** docs (via the reference-discovery engine shared with "Unlink and Delete"), **plus** the framework's "soft" references that aren't declared link fields — **attachments/Files, Comments, ToDos/assignments, Communications/emails, Tags, Versions, Notification Logs**. The survivor keeps all its own values; only its **blank** fields are backfilled from the loser (a real `0` is never overwritten), and the loser's **child rows are appended** (exact duplicates skipped). The loser is then **deleted**. A mandatory **side-by-side preview** shows exactly what is kept, discarded and backfilled, the full count of references to be repointed, and any free-text mentions of the loser's name flagged for **manual review** (those are never auto-rewritten); a **typed confirmation** of the loser's name guards the irreversible delete, and a **Swap** control flips which document survives. Every merge is recorded in an append-only **Document Merge Log**. Large merges (> 2,000 references) run as a **background job** and notify you on completion. Gated behind the new **default-OFF** `document_merge_enabled` switch (ERPNext Enhancements Settings → Document Merge); the server endpoints refuse while it's off. (The pre-existing Project-only merge button is unchanged.)

## [1.140.0] - 2026-06-30

### Changed
- **Department KPI dashboards are now role-gated, editable Workspaces (replacing the v1.139.0 desk pages).** The desk-page approach couldn't host Custom HTML Blocks (those only work on Workspaces), so each department's KPIs live on its own **Workspace** again — `Finance Dashboard`, `Sales Dashboard`, `Operations Dashboard`, `Design Dashboard`, `Production Dashboard`, `Marketing Dashboard`, `Product Dashboard`, `Executive Dashboard` — now **restricted to that department's roles + System Manager**, so a workspace can be shared with just that team. Because they're ordinary (non-standard) workspaces, an admin can **add more Custom HTML Blocks** to any of them, and those edits survive migrations (the block seeder only *appends* the KPI Cockpit, never overwriting added content). A one-time patch (`setup_department_kpi_workspaces`) role-gates each workspace and creates any that are missing (incl. the new Product Dashboard); the v1.139.0 desk pages + their renderer bundle are removed (the pages drop out as orphans on migrate). The six Finance operational widgets remain on the Finance Dashboard workspace.

## [1.139.0] - 2026-06-30

### Changed
- **Each department now has its own shareable KPI page.** Per-department KPIs moved off the department workspaces onto dedicated, role-gated desk pages — `/app/finance-kpi`, `/app/sales-kpi`, `/app/operations-kpi`, `/app/marketing-kpi`, `/app/design-kpi`, `/app/production-kpi`, `/app/product-kpi`, `/app/executive-kpi` — so a single department's metrics can be shared by URL with just the people who should see them (each page is restricted to that department's roles + System Manager, and the underlying KPI API is role-gated too). The pages render the same precomputed KPI Snapshot the cockpit shows, via a shared renderer (`public/js/kpi_dashboard_page.bundle.js`). The **KPI Cockpit** (with its department picker) stays on Home and the KPI Dashboards workspace as an overview, and is now linked from there to each department page; it is no longer placed on the seven department workspaces (a one-time patch strips the existing placement). The six Finance operational widgets remain on the Finance Dashboard workspace.

## [1.138.0] - 2026-06-30

### Added
- **Fleet Maintenance module — track routine company-vehicle maintenance.** A new module for the weekly / 3-month / 6-month vehicle maintenance schedule:
  - **`Fleet Vehicle`** master — one record per vehicle (make/model/year, plate, VIN, fuel type, assigned driver, odometer, photo) plus a "last done" date per cadence. Each save derives the matching "due" dates and a headline **Maintenance Status** (`No Data` / `OK` / `Due Soon` / `Overdue`) from those dates and the configured intervals.
  - **`Vehicle Maintenance Log`** (submittable) — the form crew fill in. Picking a **Maintenance Type** (Weekly / Oil Change (3-Month) / Dealership Check-Up (6-Month) / Windshield Wipers (6-Month) / Other / Repair) auto-loads that type's standard checklist (`Vehicle Maintenance Task` child rows); required items block submit until they have a status. On submit it rolls the vehicle's matching last-done date forward, advances the odometer (forward only), and recomputes status; on cancel it re-derives from the remaining submitted logs.
  - **Nightly status refresh + reminders** — ages every non-retired vehicle's status as dates pass and, when reminders are on, sends a desk notification to fleet managers (users with the *Fleet Manager* or *Maintenance Manager* role, else System Managers) the day a vehicle newly becomes Due Soon / Overdue.
  - **In-desk `Fleet Maintenance Schedule` page** (the cadence reference, incl. the standing daily "check gas, refill if ≤ half" instruction), a **Fleet Maintenance** workspace, and a printable **Vehicle Maintenance Checklist** Print Format (print a draft for a blank sheet to keep in the vehicle).
  - New **Fleet Manager** role (seeded, assign post-deploy) with full access to both doctypes alongside the stock Maintenance Manager / Maintenance User roles.
  - Gated by a new **ERPNext Enhancements Settings → Fleet Maintenance** section: a default-OFF master switch (`Enable Fleet Maintenance`), a default-ON `Send Fleet Reminders` toggle, and the four cadence intervals + the Due Soon window. The forms work regardless; only the background job + reminders are gated. Design + SOP in `docs/FLEET_VEHICLE_MAINTENANCE.md`; module notes in `erpnext_enhancements/fleet_maintenance/README.md`.

## [1.137.0] - 2026-06-30

### Fixed
- **Custom HTML Block sources now ship inside the Python package** (`erpnext_enhancements/custom_html_blocks/`) instead of a repo-root `Custom HTML Block/` folder that lived *outside* the package. The seeder reads from the in-package location (with a repo-root fallback), so the blocks are created on every install and partial sync — previously a bench synced without the external folder would skip creating **all** blocks (KPI Cockpit included), leaving the dashboards blank. The old `seed_*_block` patches use the same resilient resolver.

### Added
- **Six new operational widgets on the Finance Dashboard.** Each ships as a self-contained Custom HTML Block (shadow-DOM, role-gated, individually toggleable), placed on the **Finance Dashboard** workspace by the after-migrate block seeder alongside the existing KPI Cockpit:
  - **New Jobs Queue** — the most recently created Active Projects (customer, owner, age, source Opportunity link).
  - **Who's Working** — a time-clock admin view of who is currently clocked in (employee, project/task, elapsed time), read from open/paused `Job Interval`s.
  - **Bank Balances** — a live snapshot of bank account balances (KeyBank checking + any linked accounts) via a new **Plaid** integration (hand-rolled `requests` REST client — no SDK; durable `Bank Balance Snapshot` cache refreshed on a throttled schedule; the read widget never calls Plaid directly).
  - **Weather** — an HQ weather chip reusing the existing keyless Open-Meteo path.
  - **Astrology** — a daily horoscope with a pick-a-sign selector (free public API, server-side cached per sign per day; chosen sign remembered in the browser).
  - **Finance Calendar** — upcoming events from a Google Calendar named "Finance" (Google Calendar API via the existing Drive service account).
- **`Plaid Settings`** single doctype (new **Plaid Banking** module) isolates the encrypted Plaid credentials + connection state (System Manager full; Accounts Manager read). Plaid Link runs on the Settings form (**Connect Bank / Reconnect / Disconnect / Test Connection / Refresh Balances Now**); a non-retryable Plaid error pauses the integration (`plaid_auth_blocked`) instead of retrying, mirroring the MDM auth-block pattern.
- **Finance Dashboard Widgets** section on **ERPNext Enhancements Settings**: per-widget enable toggles (default OFF), the Finance Google Calendar id + event cap, and an optional horoscope API base.

## [1.136.0] - 2026-06-29

### Changed
- **Field descriptions now render as a hover "ⓘ" info icon instead of inline help text.** Across all desk forms, any field that has a description gets a small ⓘ next to its label; hovering (or keyboard-focusing / tapping) it reveals the text in a floating tooltip, decluttering the form. The text comes from Frappe's own (already translated, HTML/link-formatted) help-box, which is hidden via CSS rather than removed, so it stays the live source. New global desk script `public/js/global_enhancements/field_description_icons.js` + styles in `desk_addons.bundle.scss` (theme-aware via Frappe CSS variables, so Timeless Night just works). Gated by a new **ERPNext Enhancements Settings → Desk Experience → Field Description Info Icons** switch (default ON), shipped to the client via `frappe.boot.ee_field_description_icons` — toggling needs no deploy; clients pick it up on their next page load. Child-table (grid) fields are untouched (Frappe already tooltips their column headers).

## [1.135.0] - 2026-06-29

### Added
- **Travel maps locate POIs by their linked Address (geocoding).** A Travel POI is often located by its **Address** field rather than a dropped pin, which left the trip agenda map blank (it only plotted POIs with coordinates). Now:
  - The **trip agenda map** geocodes any stop that has an address but no point, plots it, and **caches** the result back onto the POI (new whitelisted `api.travel.cache_poi_geocode`, write-gated and non-clobbering) so it isn't re-geocoded next time. `get_trip_map_data` now returns a geocodable `address` string for coordless POIs.
  - The **Travel POI map** centres on the linked Address (geocoded) when no point is set, so the POI shows on Google Maps from its address alone; a **"Locate from linked address"** button (and manual click/drag) set an exact point.
  - Requires the **Geocoding API** enabled on the Maps key (in addition to the Maps JavaScript API). Without it, maps fall back to a list of Google Maps links by address.

## [1.134.0] - 2026-06-29

### Added
- **Google Maps location picker on the Travel POI form.** Replaces the native `Geolocation` field's OpenStreetMap/Leaflet map (now hidden) with a Google map in a new **Map** field: click the map or drag the pin to set the POI's coordinates. The point is written back into the hidden `geolocation` field as a GeoJSON `Point` — the exact shape `api/travel.py` `_poi_latlng` already reads — so the trip agenda map and `/itinerary` page consume it unchanged. Read-only viewers get a non-editable map; with no API key set, a prompt to configure one. New whitelisted `api.travel.get_maps_api_key` serves the (browser, referrer-restricted) key to the form. This also unblocks the trip agenda map: it only plots stops whose POI has coordinates, so POIs need a point set here first.

## [1.133.2] - 2026-06-29

### Fixed
- **Travel Trip save crashed with `TableMissingError: ('DocType', 'Expense Claim')` on HRMS-less sites.** The trip controller's financial rollups and trash-cleanup guarded their HRMS links (`Expense Claim` / `Employee Advance` / `Vehicle Log`) with `frappe.db.has_column(doctype, "custom_travel_trip")`, assuming a missing back-link returns `False`. But `has_column` *raises* `TableMissingError` when the **table itself** is absent (it only returns `False` for a missing column on an existing table) — so on a site without HRMS installed, `validate` → `_compute_rollups` blew up on every save. Added a `_has_travel_backlink()` helper that gates on `frappe.db.table_exists()` first; all three call sites now skip absent HRMS doctypes cleanly. (HRMS is not installed on production — see the optional-HRMS work in v1.77.0.)

## [1.133.1] - 2026-06-29

### Fixed
- **Travel Trip save crashed with `Unknown column 'company'`.** The Travel Trip `company` field carried `default: ":Company"`, which Frappe resolves as "fetch the `company` field *from* the Company doctype" — but Company has no such column, so `_set_defaults` (run by `frappe.new_doc` on every save) raised `OperationalError (1054, "Unknown column 'company' in 'SELECT'")`. Latent since the module was effectively unused; surfaced on the first real trip save. Removed the default — Frappe still auto-fills the company from the user's default Company, exactly as ERPNext's own company fields do (Sales Invoice/Order/Project carry no default). Verified against the production bench.

## [1.133.0] - 2026-06-29

### Added
- **Google Maps on the Travel Trip agenda map.** The "Map" section on a Travel Trip's Itinerary tab now renders a real Google map of the agenda-stop POIs, each pin carrying an **always-visible name label** plus a click bubble (category, the dates that visit it, and Open POI / Directions links). Driven by a new **Google Maps API Key** field in **Travel Settings** (the browser Maps JavaScript API key — enable that API for the key and restrict it by HTTP referrer). With no key set, the section degrades to a list of Google Maps links so the stops stay usable. New whitelisted `api.travel.get_trip_map_data` returns the key + mappable POIs in one permission-gated call (replaces `get_trip_pois`).

### Fixed
- **Blank trip "Map" box.** The agenda map lives on a tab that is hidden at form load, so the map was being initialised inside a 0×0 container and never laid out (a classic map-in-hidden-tab bug). It is now built lazily, the moment its tab/container is actually on screen and sized (`IntersectionObserver`).
- **Unfriendly POI location names.** The itinerary "Location" column showed each Travel POI's raw hash id (e.g. `dfhasj23p8`) instead of its name. `Travel POI` now declares its `poi_name` as the title field with `show_title_field_in_link`, so every link to a POI displays the friendly name.

## [1.132.0] - 2026-06-26

### Added
- **Remaining-department process maps (Phase 5 — completes the process-mapping program).** Seeded a `Process Document` (Mermaid flow + `Process Document Step` RACI grid) for the six departments not covered in Phase 0: **Sales** (Lead → Closed-Won → hand-off), **Design** (Water Feature Design: create → calc → review → issue → control panel), **Operations** (maintenance visit lifecycle), **Marketing** (lead-source + spend → CPL/web KPIs), **Product Management** (catalog/SKUs, inventory/reorder, rentals), and **Executive** (nightly KPI rollup → review → approve). People are from the Jun 2026 process interview (e.g. Sales = Brian; Design = Daniel Blass / Nathan Cox with James Harris reviewing; Maintenance crew of 7 with Austin Healey / Clegg Mabey approving; Marketing = Richard Hansen; Product = Parker Bailey; Executive sign-off = James Harris).
  - Most steps are already automated in the app, so the maps document **who owns each step and how it's enforced** (coverage is largely *Built / Existing*); design review and executive sign-off are flagged *Manual / Process-Only*. Insert-only patch (site edits survive); `erpnext_doctype` links guarded against missing doctypes. With Phase 0 this brings all **8 departments** under documented, RACI-backed process maps.

## [1.131.0] - 2026-06-26

### Added
- **Month-End Close checklist + period lock (Phase 4 of the process-mapping program).** New submittable **`Month-End Close`** doctype (one per period/company) with a **`Month-End Close Task`** child checklist auto-seeded from the Finance — Month-End Close process map (reconcile bank/CC, post accruals, review AR/AP aging, reconcile vs QuickBooks, review P&L/BS, external-accountant review, approve statements). Each task has a responsible person + status; completion is auto-stamped.
  - **Gated close:** the record can only be **submitted** (= period Closed) once every task is Done or N/A, and submit/cancel is permitted only to **Accounts Manager** (Accounts User can build and work the checklist). Status rolls up Open → In Progress → Closed.
  - **The teeth (period lock):** on submit, the controller sets the **Company's `accounts_frozen_till_date`** to the period end date — ERPNext's GL `check_freezing_date` then blocks any posting on or before that date for everyone except the `role_allowed_for_frozen_entries` role (defaulted to *Accounts Manager* so genuine corrections remain possible). Cancelling restores the previous frozen-till date (the prior value is stashed on the record), re-opening the period.
  - Uses the v16 freezing fields (moved from Accounts Settings to **Company** in ERPNext v16). App-owned doctypes in *Enhancements Core* — no fixtures needed.

## [1.130.0] - 2026-06-26

### Added
- **Production build-phase tracking (Phase 3 of the process-mapping program).** Closes the biggest operations gap from the process maps — after design, builds had no status tracking. New **Production Tracking** fields on `Project` (in `fixtures/custom_field.json`): `custom_build_status` (Select: Design Complete → Procurement → Assembly → QA → Ready for Install → Installed → Commissioned), `custom_bid_cost` (Currency — the baseline the existing gross-margin-vs-bid KPI needs), `custom_production_started` / `custom_production_completed` (Date), and `custom_quality_sign_off_by` (User) / `custom_quality_sign_off_date` (Date) for the QC gate.
  - A native **"Production Builds" Kanban board** (seeded via patch) on `Project` keyed by `custom_build_status` — drag a build across phases; columns are colour-coded. Chosen over a bespoke page because build status is single-doctype, so the native Kanban gives a maintainable drag-and-drop board with no custom UI code. (The maintenance-style day board stays bespoke because it aggregates live cross-doctype data.)
  - The Kanban patch carries a `create_custom_fields` **ordering backstop** for `custom_build_status` (fixtures sync after patches on migrate), so the board's field exists on the first migrate; the fixture remains the source of truth for the full field set.
  - `custom_bid_cost` makes the Production aggregator's "Project Gross Margin vs Bid" KPI computable; `custom_build_status` is available for future build-throughput KPIs.

## [1.129.0] - 2026-06-26

### Added
- **Department Role Profiles + process-map visibility (Phase 1 of the process-mapping program).** Seeded four onboarding **Role Profiles** — *Finance*, *Sales & Marketing*, *Projects & Operations*, *Executive* — each bundling the standard ERPNext roles for that department, so a new hire gets the right access from a single profile. Role-existence-guarded and insert-only (optional roles like Marketing Manager / Maintenance Manager are skipped on sites where that module isn't installed; site-side edits survive). **No new roles created** — the small team reuses the standard Accounts / Sales / Projects / Maintenance roles.
  - Opened **`Process Document` read access to `Employee`** (read / report / print / email / share) so the Phase 0 process maps + RACI are visible to the team; `System Manager` keeps full control.
  - `DEPARTMENT_ROLES` (`api/kpi.py`) reviewed against the org RACI — already correct, left unchanged. Assigning the profiles/roles to the actual users (Lisa, James, Brian, Clegg) is an operator step (User form) — the people's accounts aren't created in code.

## [1.128.0] - 2026-06-26

### Added
- **Finance approval workflows (Phase 2 of the process-mapping program) — Purchase Invoice + Payment Entry.** Two Frappe Workflows that route bills and payments **Draft → Pending Approval → Approved** (with a **Rejected** branch that loops back for resubmission), role-gated to match the small-team RACI from the Finance process maps: `Accounts User` submits for approval, `Accounts Manager` approves/rejects (the Approve transition submits the document). Models "Lisa drafts → James (or Lisa) approves", with the external accountant as the compensating reviewer.
  - **Shipped dormant (`is_active: 0`).** The workflows exist after `bench migrate` but enforce nothing until switched on per-doctype in the Workflow list. This is deliberate: activating a workflow on Purchase Invoice / Payment Entry changes how every user submits those documents **and can interfere with the QuickBooks importer/sync**, which creates and submits these documents programmatically. Enable once roles are confirmed and (ideally) after the QBO cutover.
  - **Enabler:** widened the `Workflow` / `Workflow State` / `Workflow Action Master` fixture filters in `hooks.py` (previously scoped to the Sapphire Maintenance Record workflow only) so these definitions are version-controlled; added the new states (Pending Approval / Approved / Rejected, styled) and actions (Submit for Approval / Approve / Reject) to the fixture files; excluded the engine-created `Purchase Invoice-workflow_state` / `Payment Entry-workflow_state` custom fields from export, matching the maintenance precedent.
  - **Self-approval is allowed** (`allow_self_approval: 1`) so one person can both draft and approve when needed — the small team's reality (Lisa drafts and may approve), with the external accountant as the compensating reviewer. Set it to `0` on the Approve transition later for a hard two-person rule.
  - **No payment threshold** (single approver per the process interview — James, no $ limit set yet); a per-amount second-approver tier can be added later via transition `condition` strings. **Expense Claim approval deferred** (HRMS-dependent; not guaranteed installed on production).

## [1.127.0] - 2026-06-26

### Added
- **Process mapping — structured RACI on Process Documents (capture layer for the QuickBooks→ERPNext process-mapping program).** The `Process Document` doctype (Mermaid flow diagrams) now carries a **Process Steps & RACI** grid backed by a new child doctype **`Process Document Step`**. Each row captures one process step: the action, the RACI (**R**esponsible / **A**ccountable / **C**onsulted / **I**nformed — free text so a small team can name roles and/or people), and *how the step is enforced in ERPNext* — `erpnext_doctype` + `erpnext_action`, an `enforcement` Select (Workflow Transition / DocPerm / User Permission / Row Filter / Notify / Manual), a `coverage` Select (Built / Existing · Config Needed · Gap — To Build · Manual / Process-Only), and a `target_artifact` pointer to the file the config lands in.
  - A `post_model_sync` patch (`seed_process_maps_finance_production`) seeds five maps — **Finance: Bill Payment (AP), Customer Invoicing (AR), Month-End Close, Job Costing; Production: Job Production** — each with its Mermaid flow and a fully populated RACI grid (people from the Jun 2026 process interview). Insert-only, so site-side edits survive re-migration; `erpnext_doctype` links are guarded so the patch never fails on a not-yet-built doctype (e.g. the future Month-End Close).
  - Both app-owned doctypes (module *Process Documentation*), created by `bench migrate` — no fixture entry needed for the schema. Additive only; the existing Mermaid fields and form script are untouched.

## [1.126.0] - 2026-06-26

### Changed
- **QuickBooks Reports API — made the Trial Balance reader compatible with Intuit's modernized ("v2") Reports service** ([Upcoming changes to Reports APIs](https://medium.com/intuitdev/upcoming-changes-to-reports-apis-5083ec9aadce)). Intuit is retiring the legacy Reports service in 2026 — after the cutover *all* `/reports/` responses are served by the modernized service. The integration touches the Reports API in exactly one place: QBO's **TrialBalance** report, consumed by both **Compare Balances** (reconciliation) and **Import Opening Balances**. Audit findings and the fix:
  - **Only `/reports/` is affected — the sync is not.** Entity import (`/query`), Change Data Capture (`/cdc`), single-entity fetches and all writes go through different endpoints the article doesn't touch, so master/transaction sync, webhooks and write-back are unchanged.
  - **No deprecated parameters in use.** We send none of `group_by`, `qzurl`, or `summarize_column_by`, so those v2 removals/changes don't affect us. The only params sent to TrialBalance are `start_date`/`end_date`.
  - **Stopped relying on fixed column index positions** (Intuit: *"Do not rely on row index positions"*). `_parse_trial_balance` now resolves the **Debit** and **Credit** columns from the report's `Columns` header by title (case-insensitive — v1 sometimes returned `ColTitle` in ALL CAPS, v2 standardizes to Title Case) instead of assuming `ColData[1]`/`ColData[2]`, falling back to the historical positions when no header is present. A reordered or relabelled report now parses with the correct sign.
  - **Empty amounts already handled.** v2 always returns `""` (never `0`/`null`) for an empty Debit/Credit; `flt("")` is `0`, and the cell reader is bounds-safe.
  - **Deeper account nesting absorbed.** v2 *"child accounts are always nested under their parent"*; the parser already recurses sections to any depth and keys rows by account id, so the extra nesting (and v2's empty-`Section` rows) is handled without change.
  - **Early-validation hook.** A new `report(..., testing_migration=True)` passthrough adds Intuit's temporary `testing_migration` flag to route a request through the modernized service. Off by default; an operator can opt in by setting `quickbooks_reports_testing_migration` truthy in the site config to validate reconciliation/opening balances against real data before the cutover (see MIGRATION_NOTES §5). Pure read-path change — no schema, data, or endpoint changes.

## [1.125.1] - 2026-06-26

### Fixed
- **@-mentions in comments rendered as raw HTML (e.g. `@<a class="mention-link" href=...>Name`) instead of a styled mention.** Root cause is an upstream inconsistency in Frappe core ~v16.24.x: the mention module (`quill-mention/quill.mention.js` `getItemData()`) still hands the embed blot the display value as an HTML anchor string, but the blot (`quill-mention/blots/mention.js` `MentionBlot.create`) was hardened to insert it via `valueSpan.textContent` — which *escapes* the markup, so the anchor shows up as literal text. This affects every linked mention on the affected build (the custom Comments App "New Note"/"Reply" dialogs and Frappe's own timeline box); it can look fine on an older dev bench (v16.22 used `innerHTML +=`).
  - `global_enhancements/quill_mentions.js` now overrides `MentionBlot.create` to render a proper, XSS-safe mention: a real `<a class="mention-link">` built from the trusted server `data.link`, with the visible name set via `textContent` (so HTML in a user's name stays inert). All `data-*` attributes are preserved byte-identical to core, so `extract_mentions` still notifies tagged users via `data-id` and saved content round-trips unchanged.
  - The override is applied once, the first time a text editor mounts, by reaching the shared blot through that editor's own Quill instance (no second Quill bundled), and it only engages when the running blot actually escapes a linked value — so it is a no-op on Frappe builds that fix the upstream bug. (The previous contents of this file were dead code: `frappe.ui.form.on("ControlTextEditor", …)` is not a valid registration for a control and never executed.)
  - Note: comments saved *while the bug was live* persisted the escaped markup in the DB and are not retroactively repaired by this client-side fix.

## [1.125.0] - 2026-06-26

### Fixed
- **KPI Dashboards — Production/Build (and the Executive backlog rollup) KPIs were silently zero on production.** The Production aggregator filtered Projects by the stock status `'Open'`, but this deployment books live projects as `status='Active'` — **no Project row is ever `'Open'`** (prod distribution: Active 438, Completed 183, Paid 1). So *Active Projects, Overdue Projects, Avg Project Completion, Active Builds, Overdue Builds, Build Backlog Value, Labor Budget Utilization,* and *Backlog (Open Project Value)* all returned 0 / None — and the Executive cockpit's **Backlog** rolled up empty as a result. All eight Project filters now accept `status in ('Open','Active')`, matching how the Product aggregator already handles rentals, so the KPIs populate regardless of which status convention a site uses. Terminal-ish states (`Completed`/`Cancelled`/`Paid`/`Invoiced`) remain excluded from "active". Pure read-path change — no schema, data, or endpoint changes.

## [1.124.0] - 2026-06-26

### Added
- **KPI Dashboards — Product Management department (fountain product catalog).** A new **Product** aggregator on the existing snapshot spine, picked up automatically by the nightly batch, the cockpit selector (`AVAILABLE_DEPARTMENTS` derives from the registry), and the role-gated endpoints (viewer roles: Item Manager / Stock Manager / Sales Manager). Registered **before** Executive so a future exec rollup can read it. No new doctypes — a defensive read over `Item` / `Bin` / `Sales Invoice Item` / `Project`, with `has_column` guards and skip-None throughout.
  - **Auto KPIs:** Catalog Revenue (30d / 1y, submitted Sales Invoice lines); Active SKUs; New Items (90d / 1y); Active Rentals, Rentals Started (30d) and Rental Backlog Value (`Project` `project_type='Rent'`); Inventory Stock Value (`Bin.stock_value`); Out-of-Stock Sellable Items; and catalog data-quality completeness — SKU %, Item Identifier %, and Pump Spec % (`item_group='Pumps'` with rated GPM + HP).
  - **Semi (guarded, emit only when computable):** Gross Margin % (needs perpetual-inventory COGS via Sales Invoice line `incoming_rate`); Items Below Reorder (`Item Reorder` vs on-hand `Bin` qty); Distinct Fountain Types Designed (`Water Feature Design.fountain_type`).
  - **Reporting:** two Dashboard Charts — **Catalog by Item Group** (Group-By on `Item`) and **Catalog Additions (Monthly)** (new sellable Items over time) — on a new **Product Catalog** dashboard fixture; all registered in `hooks.py` fixtures. "Product" added to the `department` Select on both KPI Snapshot and KPI Target.
  - **Grounded-data notes:** the product taxonomy is `item_group` (no custom segment field). Two findings from probing prod shaped the design: (1) **invoice lines do not carry the leaf product group** — every `Sales Invoice Item` row defaults to the root `All Item Groups`, so a per-product-group revenue split is degenerate; the revenue KPIs/chart therefore report total catalog revenue rather than a misleading 0% "Products" share, and the catalog-mix chart is built on the `Item` master (which does carry the leaf groups). (2) Inventory value uses **`Bin.stock_value`** (the stock-ledger valuation), not the Item-master `valuation_rate`, which materially under-reports. This site also books live projects as `status='Active'` (not 'Open'), so the rental KPIs accept both; and QBO-synced Sales Invoices are currently **drafts** (`docstatus=0`), so the submitted-only revenue/margin KPIs populate once invoices are submitted — the same convention as the Finance aggregator.

## [1.123.0] - 2026-06-26

### Added
- **KPI Dashboards — KPI Cockpit surfaced on Home and the department dashboards.** The KPI Cockpit (until now only on its own *KPI Dashboards* workspace) is additionally placed on **Home** and on each of the seven department dashboards — *Finance / Sales / Operations / Design / Production / Marketing / Executive Dashboard* (previously empty) — so the numbers show up where each team already works.
  - On a **department dashboard** the cockpit **auto-locks to that department** (detected from the workspace route, with a page-title fallback) and hides the picker; a viewer who can see exactly one department gets it locked everywhere. On **Home** and the *KPI Dashboards* overview it keeps the full department selector. Role-gating is unchanged — the locked view still only renders departments the user may see.
  - Placement is idempotent on `bench migrate` (`setup.custom_html_blocks`): the cockpit is appended only if absent, and any department dashboard that doesn't exist on the site is skipped silently. No data, schema, or endpoint changes — the snapshot engine is untouched.

## [1.122.1] - 2026-06-26

### Changed
- **Projects Dashboard — value-stream group order.** On the Priority Overview, when grouped by **Project Priority** (value stream), the stream groups now follow the business-preferred order **Design, Build, Service, Rent** instead of alphabetical. Any other stream (e.g. Delivery, Uncategorized) falls after these, alphabetically. Applies to both the Project Dashboard page (`priority_overview.js`) and the Projects Dashboard Custom HTML Block.

## [1.122.0] - 2026-06-25

### Added
- **Opportunity win/loss reason capture + Sales KPIs.** Three custom fields on Opportunity, shown only at close (depends-on status): **Won Reason** (Price / Relationship / Product Fit / Timing / Other), **Lost Reason** (Price / Competitor / No Budget / Timing / No Decision / Other), and **Lost To (Competitor)** (shown when the reason is Competitor). A reason is **required when an Opportunity transitions to Closed Won or Lost** (enforced on the transition only — editing a historical closed Opportunity is not retroactively blocked), mirroring the existing required-ranks-on-won validation.
  - Feeds two new Sales snapshot KPIs — **Lost to Competitor (90d)** and **Close-Reason Capture (90d)** (share of closed opps that recorded a reason) — and an **Opportunity Loss Reasons** donut on the Sales Pipeline dashboard.
  - The reason option lists are plain Select options, safe to edit to match your taxonomy. Fields are provisioned idempotently on migrate (`setup.custom_fields.create_opportunity_winloss_fields`).

## [1.121.0] - 2026-06-25

### Added
- **KPI Dashboards — Marketing web metrics (GA4 / Search Console), snapshotted.** A new **Marketing Web Snapshot** doctype caches the daily 30-day web totals; ``snapshot_marketing_web`` pulls GA4 + Search Console **once at the head of the nightly batch** and stores them, so the Marketing aggregator surfaces **Web Sessions, Active Users, Organic Clicks, and Organic Impressions** by reading the cache — never calling Google in the snapshot path (honouring the "no live external calls in aggregation" rule). Fully guarded: an unconfigured or failing pull stores a status and the Marketing snapshot simply omits the web KPIs (no misleading zeros), and a slow pull can never block the batch.

### Notes
- The web-metric **plumbing** (the cache doctype, the batch hook, the aggregator read, and the unconfigured/failure paths) is live-verified on the dev bench; the **live GA4/GSC parse** itself can only be exercised on a GA4-configured site, so it should be sanity-checked once after the first real pull on production.

## [1.120.0] - 2026-06-25

### Added
- **KPI Dashboards — Phase 4 (start): Marketing Spend → Cost Per Lead.** A new **Marketing Spend** doctype (one row per channel per month: month, channel, amount) — the single piece of genuine manual entry for Marketing, a monthly paste until ad-platform connectors exist. The Marketing snapshot now computes **Marketing Spend (MTD)** and **Cost Per Lead (MTD)** (spend ÷ new leads) from it, both guarded so they only appear once spend is entered. Linked from the KPI Dashboards workspace; editable by System Manager / Sales Manager. This begins to close Marketing — the weakest-data department — with the lightest possible capture.

## [1.119.0] - 2026-06-25

### Added
- **KPI Dashboards — segment-aware build throughput (Production).** Four new build-specific KPIs using the existing `project_type` field: **Builds Completed (30d)**, **Active Builds**, **Overdue Builds**, and **Build Backlog Value** (open Build projects' contract value). Surfaces the Build segment specifically rather than lumping it with Service/Rent/Design work.

### Notes
- Inspection of real data (361 completed projects) found that the "enabler fields" anticipated in the Phase 3 plan are unnecessary or unusable: **`project_type` already encodes the revenue/cost segment** (Build / Service / Rent / Design), so no separate segment field is added; and **`Project.actual_end_date` is populated on 0 projects**, so a true on-time-delivery KPI is deferred until that field is captured (the snapshot uses overdue-vs-`expected_end_date` as the available signal in the meantime).

## [1.118.0] - 2026-06-25

### Added
- **KPI Dashboards — Phase 3: KPIs on the `/wall` TV display.** The wall now rotates a **KPI band** between the briefing band and the project carousel, cycling one department's latest snapshot at a time (Executive rollup + Operations board — the natural TV content) in lock-step with the carousel rotation. Cards show value, Good/Watch/Bad colour, and trend. Gated by a new **Show KPI Band on Wall** toggle in ERPNext Enhancements Settings (default off; only available once KPI Dashboards is enabled). The payload reads the latest snapshots (no recompute) and is fully defensive — a disabled or hiccuping KPI feed renders nothing and never disturbs the 24/7 wall.
  - _Remaining in Phase 3:_ a dedicated Executive desk page, the light enabler custom fields, and the deferred GA4 web-metrics snapshot.

## [1.117.0] - 2026-06-25

### Added
- **KPI Dashboards — Phase 3 (start): Executive rollup.** A seventh aggregator that rolls the company up to one view. It re-surfaces a curated C-suite set from the freshest department snapshots — Revenue & Cash Collected (30d), AR & DSO, Open Pipeline & Win Rate, Backlog & On-Time Milestone Rate, Active Maintenance Contracts & Out-of-Range Rate — plus two direct computes (Active Headcount, Revenue per Employee). Executive is built **last** in the nightly batch so its rollup reads the same night's Finance/Sales/Production/Operations snapshots. It appears automatically in the KPI Cockpit (the selector and endpoints derive from the aggregator registry), gated to the Executive viewer roles. No new doctypes.
  - _Still to come in Phase 3:_ a dedicated one-screen Executive desk page, the `/wall` TV rotation, the light enabler fields (revenue/cost segment on Project, scheduled/actual dates), and the deferred GA4 web-metrics snapshot.

## [1.116.0] - 2026-06-25

### Added
- **KPI Dashboards — Phase 2: Design, Production & Marketing aggregators.** Three more department snapshots on the Phase 1 spine, so the KPI Cockpit now covers **six** departments (Finance, Sales, Operations, Design, Production, Marketing). No new doctypes — each is a defensive read over existing data, picked up automatically by the nightly batch, the cockpit selector, and the role-gated endpoints (`AVAILABLE_DEPARTMENTS` now derives from the engine's aggregator registry so the two can't drift).
  - **Design (Water Engineering):** designs created/issued (30d), Design WIP, designs-with-warnings, **Clean-Issue Rate**, open revisions, and average WIP completion — from Water Feature Design status/`has_warnings`/`completion_percent`/`amended_from`.
  - **Production (Build):** projects completed (30d), active/overdue projects, average completion, **labor budget utilisation** (elapsed vs budgeted hours), backlog value, overdue milestones, **on-time milestone rate** (90d), and contract change-orders — from Project + Project Process Step + Project Contract (custom budget/hours fields guarded with `has_column`).
  - **Marketing:** new leads (30d/MTD), **lead-conversion rate** (90d), unsourced-lead count, and source-attributed pipeline / wins / unsourced-opportunity count — from Lead + Opportunity `source`. GA4 / Search Console web metrics are intentionally deferred to a follow-up that snapshots the daily GA4 pull (no live external calls in the nightly batch).

## [1.115.0] - 2026-06-25

### Added
- **Department KPI Dashboards — Phase 1 (the snapshot spine).** A new **KPI Dashboards** module that precomputes per-department KPIs nightly and surfaces them on the desk, designed to cover each department's whole job scope (not just ERPNext data) with automation prioritised over manual entry. See `docs/KPI_DASHBOARD_DESIGN.md` for the full 114-KPI catalog and roadmap.
  - **Three new doctypes:** **KPI Snapshot** (one durable row per department/period/day, idempotent `KPI-{department}-{period}-{date}` autoname, modelled on Daily Briefing so a `bench migrate` can't vaporise it), its **KPI Snapshot Value** child (value, target, Good/Watch/Bad status, period-over-period trend, source-freshness flag), and **KPI Target** — the tiny, high-leverage table managers edit to light up every "vs target" badge without a budgeting module.
  - **Nightly snapshot engine** (`kpi_dashboards/snapshots.py`): a `0 5 * * *` cron enqueues a per-department batch onto the `long` queue (commits per department so one slow aggregator can't sink the rest). Phase 1 ships **Finance, Sales, and Operations** aggregators — ~12 KPIs each — read from the post-QBO-sync system-of-record doctypes (Sales/Purchase Invoice, Payment Entry, Opportunity, Lead, Sapphire Maintenance Record/Contract, etc.); QBO/Stripe are never called live in the render path, and their sync freshness is recorded so stale upstreams show a Watch badge. A daily purge trims snapshots past the retention window.
  - **KPI Cockpit** Custom HTML Block on a new **KPI Dashboards** workspace: a role-gated department selector + Refresh, rendering each KPI's value, target, trend, and status. Endpoints `erpnext_enhancements.api.kpi.*` are role-gated per department (System Manager sees all).
  - **Finance Health** native ERPNext dashboard (AR/AP outstanding, overdue + draft invoices, monthly revenue, invoice-status mix) — stock-field cards/charts that render immediately, independent of the snapshot engine.
  - **Master switch** in ERPNext Enhancements Settings → *KPI Dashboards* (`kpi_dashboards_enabled`, default off per the staged-rollout convention) + snapshot retention days.

## [1.114.0] - 2026-06-24

### Added
- **Water Feature Design — a Fountain Type field that switches the design.** A new **Fountain Type** Select on the design (next to the title) lists every starter we offer; picking one **loads that type's template**, replacing the basin / feature / piping / tier rows (with a confirmation prompt if the design already has rows — cancel reverts the field). Clicking a "New from Template" button now also sets the field, so the two stay in sync.
- **Nine more templates / fountain types**, on top of the existing eight feature-type starters: **Reflecting pool, Bubbler / boil, Geyser / foam jet, Laminar clear-stream, Interactive plaza jets, Wall spout / mask, Pond / naturalistic, Pondless urn, Grand cascade (multi-tier)** — 18 in total, each mapped to one of the engine's feature flow calcs. The Fountain Type field options are kept exactly in sync with the template catalog. `fountain_type` is also accepted on the wizard / AI save path.

## [1.113.0] - 2026-06-24

### Added
- **Design-package print formats (DOC-0121 / DOC-0126).** Two new Jinja print formats, shipped on migrate alongside the existing Results / Calculation Audit:
  - **Water Feature Design — Schedules:** an Equipment Schedule (selected pump, pump candidates, basins) and a Piping Schedule (every segment with material, size, length, velocity, head loss, and its fittings/equipment), per the DOC-0121 design-package requirement.
  - **Control Panel Design — Submittal:** reproduces the DOC-0126 submittal verbatim — title line, User Interface (the enabled screens), Pump Control & Pumps (per-pump line), Inputs, Solenoid Valves, Lights, Interlocks, Theory of Operation, and the representative image — generated from the panel's fields.

## [1.112.0] - 2026-06-24

### Added
- **Control Panel Design — captures the full controls intake (DOC-0025/0123/0127).** New fields: **Theory of Operation** (Long Text — DOC-0127 makes this mandatory), System & Control Panel descriptions, the three design/construction parties (Fountain Design / Construction / Controls company, defaulting to Sapphire Fountains — feeds the O&M manual), a second control voltage, a power **source-of-confirmation**, and a **Fuses** child table (new `Control Fuse` doctype: qty, rating, replacement part, protects — for the O&M fuse schedule).
- **Standard input checklist is now seeded.** A fresh panel seeds the standard inputs (E-stop, water-level controller, wind sensor) the way it already seeds the standard interlocks, so the I/O list starts from the DOC-0126 baseline instead of empty.
- **`controller_hardware`** now offers the LCD + 4-button platform (DOC-0062 EDP001/SDP001) alongside the Nextion HMI and Allen-Bradley PLC options.

### Changed
- **Wind interlock split into two thresholds** (DOC-0123 Wind Control): *above medium → VFDs ramp to windy speed* and *above high → feature pumps stop*, replacing the single "wind high → stop" row that under-modeled the real two-stage behavior.

## [1.111.0] - 2026-06-24

### Added
- **Two new design calcs from DOC-0049 D/G-Program.** `lighting_design` recommends total underwater-light wattage from the water-surface area and pool class, using the watts/SF design bands (shallow pond 0.25–0.75 → competition 2.0–3.0) — so the engine can *recommend* lighting load, not just roll up fixtures already chosen. `overflow_check` computes the peak rainfall overflow a basin must shed (`SA × in/hr/12 × 7.48 / 60`, 7.9 in/hr design) and checks an overflow standpipe (3"/4"/6") against it, recommending the smallest size that handles the peak. Both on the desk endpoint + AI `water_calc`.

## [1.110.0] - 2026-06-24

### Added
- **Pipe pressure ratings (DOC-0049 sheets 1–3).** Loaded the full per-size pressure/weight spec (OD, wall, dry/wet weight, max temp, PSI @73°F & @110°F) for SCH40/SCH80 PVC + Type K copper. Two new calcs — `pipe_pressure_rating` (max psi at a temperature; PVC derates linearly to half by 110°F) and `pipe_pressure_check` (psi margin vs the system pressure) — exposed on the desk endpoint and the AI `water_calc`. The spine sizes pipe by *velocity*; it now **also** checks pressure: once TDH is known it converts to psi (~TDH/2.31) and flags any discharge run whose pipe isn't rated for it (with a `pipe_pressure_check` audit card so it shows in "Show the math").
- **DOC-0119 CYA-coupled chlorine floor.** `chemistry_targets` accepts optional `cya_ppm` / `free_cl_ppm` and computes the free-chlorine floor `max(2.0, 7.5% of CYA)`, warning when the standard target range or the planned level falls below it (under-sanitized water).

### Changed
- **Component head-loss now uses the real (nonlinear) manufacturer curves.** `component_loss` previously linearized each filter/skimmer/heater to a single ft/GPM coefficient; it now interpolates the actual DOC-0049 sheet-7 curves (`COMPONENT_CURVES`, 16 components) — a convex filter is no longer mis-stated across its range — and **warns when a component runs past its rated `max_gpm`**. The old coefficients remain as a fallback + the desk-picker hint. (This shifts computed TDH on designs with components — it's a correctness improvement.)

## [1.109.0] - 2026-06-24

### Added
- **Source-document data audit + roadmap.** Audited all 11 Sapphire design documents (DOC-0025/0028/0048/0049/0062/0092/0119/0121/0123/0126/0127) against what the engine actually uses; the prioritized gap analysis lives in `water_engineering/SOURCE_DATA_AUDIT.md`. First batch of findings implemented below.
- **Weir / edge sheet-rate design guidance (DOC-0049 B — Surge Basin).** `weir_flow` and `tiered_fountain_flow` now report flow **per linear foot of edge**, classify it into the workbook's wind band (minimum wet edge → light/medium/strong breeze → conservative), and surface the design rule: *operate edges near 0.5 GPM/ft but engineer water-in-transit & plumbing for 4–6 GPM/ft*. An edge running below ~0.5 GPM/ft now warns that the sheet may break into rivulets. (`engine/feature.py:edge_sheet_guidance`.)

### Fixed
- **Corrected the 0.5 GPM/ft edge/tier sheet-rate citation.** It was attributed to DOC-0119, but the rate and its wind-tolerance bands actually come from **DOC-0049 sheet B (Surge Basin)** — fixed in `feature.py` and the Water Feature Tier field help.

### Changed
- **`Control Panel Design.product_family` is now a Select** (Splash Wizard Basic / PLUS / MAX) instead of free text, matching the DOC-0062 platform taxonomy.

## [1.108.0] - 2026-06-24

### Added
- **Water Feature Design — per-segment pipe / fitting / component math in the audit trail.** "Show the math" already expanded basin, weir, and pump calcs, but a pipe run only showed the rolled-up Total Dynamic Head with one-line per-segment steps. The spine now emits a full `CalcResult` envelope for each segment's **friction** (Hazen-Williams major loss), **fitting** minor loss (the K-factor working), and **component** loss (equipment head), so every pipe run's and fitting's formula, inputs, step-by-step working, and citation render as their own cards when the toggle is on — the same transparency as the rest of the design. (`engine/tdh.py:segment_loss_results`, wired into `run_spine`.)

### Changed
- **AI `save_water_design` is now fitting/component catalog-aware.** The tool schema previously told the assistant `fittings_json` / `components_json` took JSON but not the valid `type` values, so the AI could emit a name the engine silently ignores. The `pipe_segments` schema now lists the exact catalog names (generated from the engine's `FITTING_K` / `COMPONENT_COEFF` tables, so it can't drift) plus the `{"type", "qty"}` shape and the Discharge/Suction & material enums — the same single-source-of-truth the desk picker uses.

## [1.107.0] - 2026-06-24

### Changed
- **Water Feature Design — pick pipe fittings & equipment instead of typing JSON.** Each pipe segment's fittings/valves and equipment/components used to require hand-typed JSON (`[{"type":"ELL 90","qty":2}]`), where the `type` had to exactly match an engine catalog key — far too fiddly for a designer. Now each segment row has **Edit Fittings & Valves** and **Edit Equipment & Components** buttons that open a picker: choose an item from a dropdown and set a quantity, add as many rows as needed. The row shows a plain-language summary (e.g. *"2× ELL 90, 1× EXIT"*), and the live head-loss / TDH updates on Apply.
  - The dropdown options come straight from the engine's own K-factor and coefficient tables via a new `get_loss_catalog` endpoint, so the desk choices can never drift from the math (and an invalid hand-typed type is no longer possible). Each option shows its coefficient as a hint (`K 0.81`, `0.077 ft/GPM`).
  - The raw JSON is still the stored value the engine reads — it's just hidden behind the picker now. Designs saved before this change get their summaries back-filled from the existing JSON on open.

## [1.106.0] - 2026-06-24

### Changed
- **Water Feature Design — Project is optional; Customer can stand alone.** A design no longer needs a Project — the Project link is documented as optional (leave it blank for a standalone design or quote). The **Customer** field is now directly editable: it still auto-fills from the Project's customer when a Project is set (`fetch_if_empty`, so a hand-entered customer is never overwritten), but you can pick a customer on a project-less design. The wizard / FAC MCP save path accepts `customer` too. *(Project was never marked required in the doctype; if a form shows it as mandatory, that's a saved Customize Form override on the instance, not the app.)*

### Added
- **Water Feature Design — a quick-start template for every water feature type.** The **New from Template** menu now covers all the feature types we build, one per type, instead of three samples: **Weir basin**, **Spilling weir (scupper)**, **Vanishing edge (weir wall)**, **Waterwall (sheet)**, **Nozzle-array pool**, **Orifice nozzle jet**, **Splash pad**, **Rain curtain**, and **Tiered fountain (cascade)**. Each template's `feature_type` matches the engine's flow-calc routing, pre-fills a representative basin + feature + discharge run, and the **Tiered fountain** template lands a 3-tier cascade in the Tiers table. Applying a template now also clears/fills the **Tiers** table (previously it only touched basins, features, and piping, so a tiered template couldn't load its tiers). The Orifice nozzle template leaves the Nozzle Profile to pick (orifice flow is sourced from the manufacturer cut sheet) and pre-fills the supply head.

## [1.105.0] - 2026-06-24

### Added
- **Water Feature Design — "Show the math" toggle on the live form.** Every engine calc already produces its formula, step-by-step working, inputs (each tagged with where it came from), and citations — and the Calculation Audit print format already renders them — but the *live* dashboard only showed the rollups + schematic. A **Show the math** toggle in the dashboard header now expands a card per calculation, inline, showing the formula, the inputs table (value / unit / source), the working, the source citation, and any warnings — the same transparency as the printed audit, live as you model. `preview_design` now returns the `calc_results` envelope (`recompute()` already populated it in memory — zero extra compute); the toggle re-renders client-side with no round-trip. Mirrors the Calculation Audit print format's markup.

## [1.104.0] - 2026-06-24

### Fixed
- **Water Feature Design — engine correctness hardening (from a system audit).**
  - **Undersized-pump bug:** a pipe segment with a blank flow used to compute **zero friction loss**, silently yielding a TDH of just the static lift and a far-too-small pump — on a perfectly normal workflow. The spine now defaults a segment with no flow to the **design flow** (most segments carry the full system flow), the controller's per-row velocity/head-loss uses the same effective flow, and a length-bearing segment with no flow *and* no design flow to infer from now emits a warning instead of failing silently.
  - **Garbage-in guards:** negative basin/feature dimensions (which produced negative gallons/weight/flow) are rejected with a warning; `pipe_velocity` / `hazen_williams_loss` now guard `inside diameter == 0` (was a 500 on the stateless calc / MCP path); negative nozzle counts and tier diameters are clamped with a warning.
  - **No more silent drops:** when the recommended pump's item code isn't a real Item, the design now says so in *Next inputs needed* instead of just showing "no pump"; and submitting a design that still has warnings or unresolved inputs shows a non-blocking "not fully resolved" notice.
  - Golden tests for each guard.

## [1.103.0] - 2026-06-24

### Added
- **Water Feature Design — tiered (cascading) fountains, with a variable number of tiers.** A new `Tiered Fountain` feature type plus a **`Tiers` child table** on the design (one row per tier — diameter, rim height, and per-foot sheet rate). The tier count and dimensions are fully variable.
  - **Cascade flow logic** (`tiered_fountain_flow`): each tier is a circular weir; the same recirculated water sheets every tier in series, so the required flow is the **largest tier's** rim demand — `Q = max over tiers of (π·D/12)·gpm_per_ft`. The controller pulls the tier rows into the spine for the tiered feature, so the design flow and pump size account for it.
  - **Canvas schematic** — the fountain canvas draws the tiers as a stack of bowls (largest at the bottom) on a central column with water spilling tier-to-tier, sized from the actual tier diameters. Variable tier count in, matching stack out; mirrored in the Triton chat renderer.
  - `feature_flow_category` / `feature_visual_kind` gain a `tiered` case; `_canvas_state` returns the tier list; `preview_design` accepts the `tiers` table so the live preview redraws as tiers are edited. Golden test for the cascade flow.

## [1.102.0] - 2026-06-24

### Added
- **Water Feature Design — more feature types, each with its own canvas schematic.** The `feature_type` options expand from Weir / Nozzle Array / Orifice Nozzle to also include **Spilling Weir, Waterwall, Splash Pad, and Rain Curtain**, and the fountain canvas now draws a distinct picture for each instead of a generic jet:
  - **Waterwall** — a back wall with water sheeting down its face into the basin.
  - **Spilling Weir / Weir** — water spilling over a raised crest into the basin.
  - **Splash Pad** — a flat deck with ground jets and splash rings (no deep basin).
  - **Rain Curtain** — an overhead manifold dropping a curtain of streams into the basin.
  - **Nozzle Array / Orifice Nozzle** — the central jet plume (with the jet-height callout).
  - Two shared engine classifiers (`feature_flow_category`, `feature_visual_kind`) drive both the flow calc and the drawing: sheet/crest features (weir, spilling weir, waterwall) size by the Francis weir formula; discrete-jet features (nozzle array, splash pad, rain curtain) size by count × GPM-each. The classifier replaces the ad-hoc substring checks in the engine spine and the controller, so flow and schematic always agree. `_canvas_state` now returns `feature_kind`, and the renderer (`fountain_canvas.js`, mirrored in the Triton chat) branches on it.

## [1.101.0] - 2026-06-24

### Added
- **Water Feature Design — illustrative fountain canvas + pump duty-point chart.** Builds on the modeling-UX live preview (v1.100.0) with the visuals an engineer actually reads:
  - **Fountain canvas** — the abstract box-row in the live dashboard is replaced by a to-scale fountain schematic (basin drawn from the real dimensions, water level, a jet whose height comes from the supply head, pump + supply riser **color-coded by the worst pipe-segment velocity status**, plus a metric strip and a status legend). It redraws live as the design changes. Rendered by a new shared, dependency-free `window.WaterFountain.canvasSvg(state)` ([`fountain_canvas.js`](erpnext_enhancements/public/js/water_engineering/fountain_canvas.js)) — written so the Triton chat walkthrough can reuse the exact same picture.
  - **Pump duty-point chart** — `window.WaterFountain.dutySvg(...)` plots the selected pump's performance curve with **this design's duty point** (design flow @ TDH) marked and colored green/red by whether it sits on or below the curve; when the pump has no curve on file it shows a clear "add Pump Curve points" prompt. So pump adequacy is visual, not just a "matched on flow" warning.
  - The canvas state (basin, flow, jet height, pump + curve, duty point, per-segment status, worst-velocity color) is built once server-side by `api._canvas_state(doc)` and returned by **both** `preview_design` (desk) and `design_state` (so `fac_water_design_status` surfaces it) — so the Triton chat walkthrough can draw the identical picture. Theme-aware (Frappe CSS vars for text, literal mid-tones for the figure, so they read in Light and Timeless Night); loaded via a `doctype_js` entry so the renderer is available before the form script runs.

## [1.100.0] - 2026-06-23

### Added / Changed
- **Water Feature Design — modeling UX overhaul.** The desk form is now a responsive modeling surface instead of save-and-wait:
  - **Live preview** — as you edit basins, features, piping, or any input, the design is recomputed in memory server-side (new `preview_design` endpoint, no save) and the rollups, per-row velocity/flow/head-loss, completion, and warnings update live. It calls the **same** controller `recompute()` as a save, so what you see equals what gets saved.
  - **Schematic dashboard** — the summary panel is now a live hydraulic schematic (`Basin → Features → Pump → Piping`) with the key numbers, a static-vs-friction **TDH breakdown bar**, a per-segment list with color-coded velocity **status badges** (green Okay / amber Increase Size / red Exceeds Legal), a completion bar, and a warnings list. Pipe-segment grid rows also get their `velocity_status` cell color-coded live. Theme-aware (Frappe CSS vars + `indicator-pill` classes — works in Light and Timeless Night).
  - **Tabbed layout** — the long single-column form is split into `Model` (live summary + inputs + basin/features/piping/pumps), `Treatment & Drainage`, and `Results & Audit` tabs to cut scrolling and group the modeling stages.
  - **Quick-start templates** — a `New from Template` button pre-fills a common fountain type (Rectangular weir basin / Spray-jet pool / Vanishing edge) so a design starts in seconds; replacing existing rows asks for confirmation.

## [1.99.0] - 2026-06-23

### Added
- **Water Feature Design — two Print Formats** (simple results + a robust formula audit) so a design's output can be reviewed and hand-checked against the source workbooks. Both render server-side (Jinja) from the persisted rollups + `calc_results` audit trail; created idempotently on `after_migrate` (Frappe-Cloud-safe, no shell needed).
  - **`Water Feature Design - Results`** — the simple, final end-results: a Key Results table (basin gallons, required circulation, design flow, TDH, selected pump, chlorinator feed, drain capacity, surge basin — only the values that are set) plus a compact final value/unit/status table for every calculation.
  - **`Water Feature Design - Calculation Audit`** — the robust view: for each calculation, the exact **formula**, the **inputs with provenance** (value, unit, and where each number came from — user / lookup / prior calc / default / standard, plus the source cell), the step-by-step **working**, the source **citation**, and any **warnings** — laid out to hand-compare against the spreadsheet.
  - To feed the audit view, the `Water Feature Calc Result` child table now also persists `status`, `inputs_text` (tab-delimited, rendered as a table since the print Jinja sandbox can't parse JSON), and `inputs_json` (the exact structured input set with provenance); the three audit writers in the controller were refactored to one `_calc_row()` helper that captures the complete envelope. Open a design → **Print** → pick either format (or download PDF).

## [1.98.0] - 2026-06-23

### Added
- **Water Engineering — treatment, thermal & jet calc pack** (`engine/treatment.py` + `jet_trajectory` in `engine/feature.py`, exposed via `run_calc` + the `fac_water_calc` MCP tool). Eight more calculations that complete the calc-expansion roadmap:
  - **`jet_trajectory`** — the most client-facing spec. A free jet rises to `k · supply_head` (k de-rates for drag/aeration: ~0.9 solid, ~0.6 aerated). Give a supply head/pressure to get the realistic plume height, or a target height to back-solve the required nozzle pressure (which then drives the existing TDH + pump chain) — with a basin-edge ≥ jet-height setback. *(30 ft head → 27 ft plume; 20 ft target → 9.62 psi.)*
  - **`lsi_index`** — Langelier Saturation Index `LSI = pH + TF + CF + AF − TDS_const` (interpolated factor tables) → Balanced / Corrosive / Scaling; ties the existing chemistry ranges into one balance number. *(pH 7.5, 80 °F, CH 300, TA 100 → +0.15 Balanced.)*
  - **`evaporation_rate`** — ASHRAE pool evaporation `ER = 0.1·A·AF·(Pw−Pa)` → daily make-up + latent heat, replacing the flat 0.25 in/day assumption.
  - **`make_up_water`** — daily make-up demand (evaporation + splash + backwash) and the smallest D-sheet auto-fill valve that refills it in the fill window. *(124.7 gal/day → 3/4″ valve.)*
  - **`heating_load`** — DOC-0049 O-sheet heat-loss model: `multiplier = water_wt · cover · depth · wind`, BTU/day, monthly gas cost, and a warm-up heater BTU/hr. *(5,984 gal, ΔT 11 °F, solid cover → $78.83/mo — matches the O-sheet.)*
  - **`chemical_dose`** — acid / bicarbonate / CYA / salt dose to hit a target (scales with volume and the gap). Flagged as a buffering-dependent estimate — retest before re-dosing.
  - **`uv_dose`** — UV design dose / RED at the recirculation flow (60 mJ/cm² dechloramine, 40 for 4-log), the modern complement to the existing ozone path.
  - **`filtration_area`** — required filter media area = design GPM ÷ max rate (sand capped at 3 GPM/SF per Utah R392-302-1; cartridge/DE per NSF) + backwash flow.
  - Golden-value tests in `test_water_engine.py` (heating reproduces the O-sheet; LSI/jet/make-up/dose/filtration to their references). Bench-free suite green (92); ruff clean. The treatment/thermal formulas not in the workbooks (LSI, ASHRAE evaporation, UV, dosing) are flagged as engineering-standard in their citations.
- **Calc-expansion roadmap complete** — together with v1.96.0 (safety) and v1.97.0 (workbook sheets), the engine now covers all the additional calculations identified in the gap research (safety, energy/thermal, channels, treatment, aesthetics) on top of the original hydraulic spine.

## [1.97.0] - 2026-06-23

### Added
- **Water Engineering — workbook hydraulic & planning sheets** (`engine/workbook.py`, exposed via `run_calc` + the `fac_water_calc` MCP tool). Five more calculations extracted from the DOC-0049 workbook, each reproducing that sheet's own cached worked example (golden tests):
  - **`electric_cost`** (E - Elec Costs) — annual pump operating cost: `WHP = SG·TDH·Q/3960` → BHP (÷pump eff) → HP (÷motor eff) → kW (×0.7457) → $/yr. Turns the engine from a sizing tool into a quoting tool. *(50 GPM @ 35 ft → $194.74/yr.)*
  - **`vertical_pipe`** (K - Vert Pipe) — standpipe/vertical-pipe discharge `Q = 5.68·H^0.5·K·ID²`, K = 0.82 + 0.025·ID, in three solve modes (flow from head+ID, head from flow+ID, or recommend an ID from flow+head). *(20 in over a 3″ pipe → 214.4 GPM.)*
  - **`open_channel_flow`** (J - Channel) — rectangular runnel/rill flow via Manning `Q = 1.486·A·R^(2/3)·S^0.5/n` with Froude (tranquil/critical/shooting) and Reynolds (laminar/turbulent) regime classification. *(4″×4″ @ 1% → 114.18 GPM, subcritical.)*
  - **`lazy_river_hp`** (L - Lazy) — current-generation design horsepower: Manning slope to sustain a target current → friction head over the loop → water HP × safety factor. *(7×3.75 ft, 175 ft loop @ 5 ft/s → 6.418 HP.)*
  - **`program_rules`** (D - Program) — programmatic sub-rules from the water surface area: bather load (15 SF/user pool, 9 SF spa), skimmer count (1 per 400 SF), minimum solar-panel area (0.8×SA), perimeter-overflow trigger (>5,000 SF).
  - Golden-value tests in `test_water_engine.py`; bench-free suite green (81); ruff clean. Note: the open-channel/lazy-river Manning roughness defaults to the conservative workbook `n` (0.015 / 0.0155), not DOC-0119's lower 0.009–0.011.

## [1.96.0] - 2026-06-23

### Added
- **Water Engineering — safety-critical calc pack** (`engine/safety.py`, exposed via `run_calc` + the `fac_water_calc` MCP tool). Three gates that protect people and equipment, each returning the full math envelope + a pass/risk status:
  - **`suction_outlet_vgb`** — VGB / ANSI-APSP-16 drain-cover anti-entrapment. `Q = AR·(F/(C·ρ/2·AB))^0.5` with F=120 lbf, C=2.1, ρ=1.940 — extracted and **verified verbatim against the DOC-0049 `P - Suction Outlets` worked example** (reproduces to the cell). Returns the cover's max safe GPM (entrapment- or approach-velocity-limited, whichever governs), the worst-case per-outlet flow with one outlet blocked, and a dual-drain requirement flag for single outlets. Always flags that it's an engineering aid, not a substitute for a listed cover's stamped rating.
  - **`npsh_available`** — pump cavitation go/no-go. `NPSHa = Ha + Hz − Hf − Hvp` (atmospheric head de-rated for site altitude, signed static suction, suction friction, temperature-interpolated vapor pressure); compares to the pump's NPSHr + a 2–3 ft margin → Okay / Marginal / Cavitation Risk. Reuses the suction-side friction the TDH calc already produces.
  - **`water_hammer`** — Joukowsky surge. `ΔH = a·ΔV/g` with material-specific wave speed (PVC ≈1300 ft/s), scaled down for slow valve closure (when closure time exceeds the `2L/a` reflection period); peak (static + surge) vs. the pipe's pressure rating.
  - Golden-value tests in `test_water_engine.py` (VGB reproduces the P-sheet example; NPSH altitude/temperature/status bands; Joukowsky instantaneous + slow-closure + rating check). Bench-free suite green (76); ruff clean. NPSH and water-hammer constants are flagged as engineering standards (Hydraulic Institute / Joukowsky), not source-document formulas.

## [1.95.0] - 2026-06-23

### Added
- **Water Engineering — pump performance curves (true duty-point selection).** Pump selection previously matched on the max-flow / max-head *envelope*, which is optimistic (a pump can't deliver max flow and max head at once). This adds real curve-based sizing:
  - New **`Pump Curve Point`** child table on Item (custom field `custom_pump_curve`): a handful of `(flow GPM, head ft)` points read off the manufacturer curve, plus a `custom_pump_cut_sheet` Attach field for the curve PDF (both created on migrate via `create_pump_item_fields`).
  - **`head_at_flow(curve, flow)`** linearly interpolates a pump's head at a given flow; **`select_pump` now prefers the curve** — a pump is adequate only if its interpolated head at the design flow ≥ the required TDH (and the flow is within the curve's range). It falls back to the rated max-flow/max-head envelope when no curve is on file, and to flow-only matching (with the verify warning) when neither is. The chosen option reports `head_at_duty_ft` and `head_basis` (curve / rating / flow-only).
  - The desk `get_pump_candidates` endpoint and the design controller's catalog resolver both attach each pump's curve points, so form, wizard, and the `fac_water_calc` selector all size against the real curve. Golden-value tests in `test_water_engine.py`.
  - **Curve chart on the Item form:** a `custom_pump_curve_chart` HTML field renders the points as a live head-vs-flow line chart (`frappe.Chart`, redrawn as you edit the points) so the curve is readable at a glance instead of a table of numbers ([`pump_curve_chart.js`](erpnext_enhancements/public/js/water_engineering/pump_curve_chart.js)).
  - **Managing it:** enter 4–6 curve points per pump (or just the two endpoints — max flow at ~0 head, shutoff head at ~0 flow); the engine interpolates the rest and the chart plots them.

## [1.94.0] - 2026-06-23

### Added
- **Water Engineering — Nozzle Profile catalog (orifice nozzles now compute real flow).** Orifice nozzle flow was a deliberate stub because the discharge coefficient + orifice size aren't in the Sapphire source documents. This adds the missing catalog so it works:
  - New **`Nozzle Profile`** DocType (reference master): per-nozzle discharge coefficient (Cd) + orifice diameter/area, **or** a rated GPM @ rated head, plus manufacturer/model/cut-sheet. Engineers populate it from manufacturer data.
  - **`nozzle_flow`** now computes from a profile's sourced coefficients — `Q = Cd·A·√(2gh)` (textbook orifice physics; Cd/area from the catalog, not invented) or `Q = rated_gpm·√(head/rated_head)` — driven by a per-feature **supply head**. With no profile it still returns a clear "needs a Nozzle Profile" warning rather than a fabricated number.
  - `Water Feature Nozzle` rows gain a **Nozzle Profile** link + **Supply Head (ft)**; the design controller resolves the profile and computes orifice-feature flow in `recompute()`. Exposed through the desk `run_calc` endpoint and the `fac_water_calc` tool (pass `nozzle_profile` + `supply_head_ft`).
  - **Generic starter profiles auto-seed on migrate** (`ensure_nozzle_profiles`, idempotent + guarded — Frappe Cloud gets them on deploy) for smooth-bore / aerating / geyser / spray / cascade, **clearly flagged "generic estimate — replace with manufacturer cut-sheet data"** (the same coefficients the legacy assistant used). A Nozzle Profile workspace link is added. Golden-value tests in `test_water_engine.py`.

## [1.93.0] - 2026-06-23

### Added
- **Water Engineering — Phase 3 (drainage + surge basin) and Phase 4 (controls / control-panel submittal).** Two more calc areas for the design tooling, verified against DOC-0049 (`10 - Gravity` / `G - Gravity` / `B - Surge Basin`) and DOC-0126/0127/0025; both wired into the engine, the desk `run_calc` endpoint, and the `fac_water_calc` MCP tool.
  - **Drainage (Manning's):** `manning_drain_flow` (gravity-drain capacity GPM = `A·(1.486/n)·R^(2/3)·S^(1/2)·7.48·60`, half-full area `3.14·D²/8/144`, `R=(D/4)/12`, slope = in/ft ÷ 12, n by size 0.012–0.016) and `size_drain` (smallest drain for a required GPM). Uses **DOC-0049 as the authority, not DOC-0119** — the latter's 1.49 / n=0.009 / full-pipe form over-predicts capacity ~3× and is internally inconsistent (its own worked example is off ~2.9×). Golden-tested to the workbook cells (3″@¼″/ft → 30.37 GPM, 4″ → 58.21, 6″ → 162.0).
  - **Surge basin:** `surge_basin_volume` — basin depth (overflow + freeboard + swimmer-displacement + precipitation + evaporation + vortex) and normal-operating gallons, with the DOC-0049 green-cell defaults (evap 0.25 in/day, precip 1.0 in, vortex 12 in, freeboard/overflow 3 in).
  - The **`Water Feature Design`** form gains a **Drainage & Surge Basin** section (drain size + slope → capacity; pool/basin areas → surge gallons), computed in `recompute()` and logged to the audit trail.
  - **Controls:** a new **`Control Panel Design`** doctype (the "controller document" / DOC-0126 submittal) with child tables for **Control Pump**, **Control IO Point**, **Control Interlock**, and **Control Light**. Its controller seeds the standard interlock checklist (circ-pump / low-water / high-wind / E-stop / thermal / power-up safe-state — DOC-0126/0127) on a fresh panel and rolls up the lighting load + relay counts. Sizing calcs `calc_lighting` (total W, current, fused-SSR relay count at 60 W/12 VDC — DOC-0126) and `calc_solenoid_relays` (one SSR per valve) are exposed via `fac_water_calc`. New read-only **`control_panel_status`** MCP tool + a Control Panel Design workspace link. Control-transformer VA is a manual field (flagged business rule — not in the source docs).
  - Golden-value tests for all of the above in `test_water_engine.py`.

## [1.92.0] - 2026-06-23

### Added
- **Water Engineering — Phase 2: water chemistry / treatment.** Adds the chemical-treatment calculations to the design tooling (engine + desk API + `water_calc` MCP tool + a Water Chemistry section on the design), all verified against DOC-0049 `C - Chemicals` and DOC-0119:
  - **`chlorinator_feed`** — minimum liquid-chlorinator feed rate (gal/hr) for a system volume: `vol × 3 / (24 × 10000)` at 10% chlorine (IBC 3133B.1), scaled by `10 / strength` for other concentrations. (50,000 gal → 0.625 gal/hr.)
  - **`chemistry_targets`** — water-balance target ranges (free chlorine / pH / cyanuric acid) by water type (outdoor / indoor / saltwater), including the "free Cl ≥ 7.5% of CYA" rule (DOC-0119).
  - **`ozone_sidestream`** — ozone side-stream sizing: full flow → side-stream flow → contact-tank adequacy check (from the verified `ContactTanks` catalog) → contact time → ozone required (g/hr) from the USEPA Cryptosporidium CT value (4.9 / 7.4 mg·L⁻¹·min for 2-log / 3-log). (40,000 gal example → 7.15 g/hr at 2-log.)
  - The **`Water Feature Design`** form gains a **Water Chemistry** section (water type + chlorine %); `recompute()` sizes the chlorinator off the system volume and records the target ranges + the chemistry envelopes in the audit trail. The three calcs are also exposed through `fac_water_calc` and the desk `run_calc` endpoint. Golden-value tests added to `test_water_engine.py`.

## [1.91.0] - 2026-06-23

### Added
- **Water Engineering — pump catalog, so the design spine resolves a pump end-to-end.** Phase 1 could size everything up to the duty point but couldn't pick a pump (Items carried no ratings, so designs capped at 75%). This adds the catalog:
  - **Pump-spec custom fields on Item** (a "Pump Specifications" section shown only for the Pumps item group): `custom_rated_gpm`, `custom_rated_tdh_ft`, plus nameplate `custom_pump_hp` / `custom_pump_phase` / `custom_pump_voltage` / `custom_pump_fla_amps`. Created idempotently via `water_engineering/setup.py::create_pump_item_fields`, wired into `after_migrate`.
  - **The starter catalog is seeded automatically on migrate** (`ensure_pump_catalog`, wired into `after_migrate`) — so **Frappe Cloud gets it on deploy with no shell/`bench execute` needed**. It creates the **Pumps** item group and the 5 DOC-0028 Pump-category part numbers; idempotent (skips existing item codes, never overwrites) and guarded (a seed error only logs, never breaks the deploy). Each pump's **rated flow is derived from the GPH in its DOC-0028 description** (GPH ÷ 60); the head ("max lift") isn't in the source data, so it's left blank. `seed_pump_catalog` remains callable directly (bench console / FAC `run_python_code`) if a manual run is ever wanted.
  - **`Water Feature Design` auto-sources pump candidates from the catalog** (`item_group` "Pumps") when the design has no explicit pump rows, so `recompute()` resolves `selected_pump` and reaches 100% completion automatically.
  - **`select_pump` matches on flow when a head rating is absent** (fountain submersibles are spec'd by GPH) and flags the chosen pump to verify its head against the manufacturer pump curve — rather than refusing to select. A pump that *has* a head rating below the duty head is still excluded.

## [1.90.0] - 2026-06-23

### Added
- **Water Engineering module — Phase 1 (the hydraulic spine).** First slice of the AI-driven fountain design tooling: size a water feature from basin dimensions through to a pump + electrical load, with every number traceable to a formula and a source sheet. The ERPNext side ships complete here (engine + DocTypes + desk wizard + FAC MCP tools); the Triton chat wizard follows in a coupled Triton release.
  - **Pure calculation engine** ([`water_engineering/engine/`](erpnext_enhancements/water_engineering/engine)) — stdlib-only (never imports `frappe`), so it is bench-free unit-testable and is the single source of math shared by the desk endpoints and the AI tools. **Verified, not approximated:** every formula/constant was extracted from the source workbooks' formula cells with openpyxl and pinned by golden-value tests — basin volume/turnover (DOC-0048 `Basin`), weir/slot flow via the Francis formula (DOC-0049 `I - Weir`), pipe velocity + Hazen-Williams with the workbook's own `10.44`/`1.85`/`4.8655` constants (DOC-0049 `A - Pipe Size`), K-factor minor loss + ft/GPM component loss + Total Dynamic Head (DOC-0049 `H - TDH`), and the Sch40/Sch80/copper ID + per-material velocity-limit tables (DOC-0049 `SUPPORT`). Each calc returns a `CalcResult` envelope (value, inputs-with-provenance, formula, step-by-step working, citations, warnings, A/B/C options) so the AI and the wizard can show their work.
  - **Honest gaps, not fabrication.** Calculations absent from the source docs are flagged: orifice/nozzle `Cd` flow is a warning stub (DOC-0048 enters feature flow manually — no `Cd` lookup exists; the existing Triton tool had invented this), pump selection is a catalog lookup against ERPNext Items (no curve formula in the sheets), and breaker sizing (125% FLA) is labelled a business rule to confirm.
  - **Persistent design document.** A submittable `Water Feature Design` DocType plus six child tables (basin, nozzle/weir, pipe segment, pump, electrical load, calc-result audit trail). Its controller's `recompute()` is the only frappe↔engine bridge — it runs the spine on `validate`, writes the rollups + per-row computed columns + the audit trail, and tracks `completion_percent` / `next_inputs_needed`. New **Water Engineer** role; a Water Engineering workspace + module.
  - **Desk wizard + API.** A guided [`water-engineering-wizard`](erpnext_enhancements/water_engineering/page/water_engineering_wizard) desk page (Quick Calculator that shows each calc's math + a design-state overview) and whitelisted endpoints ([`api/water_design.py`](erpnext_enhancements/water_engineering/api/water_design.py): `run_calc`, `get_design_state`, `save_inputs`, `get_pump_candidates`) that call the same engine — so desk and AI produce byte-identical results. The DocType form gains a Recalculate button + a hydraulic-summary panel.
  - **FAC MCP tools** for the Triton assistant: `water_calc` (stateless calculator → math envelope), `water_design_status` (read a design's state / list designs — drives the chat wizard's "what to ask next"), both read-only; and `save_water_design` (create/update a design), a gated write (`_gate.py` `APP_MUTATING`, Low risk) that proposes an AI Pending Action when write-gating is on. All pass the assistant-tools schema tripwire.
  - Bench-free tests: [`test_water_engine.py`](erpnext_enhancements/tests/test_water_engine.py) (28 golden-value cases) and [`test_water_design_controller.py`](erpnext_enhancements/tests/test_water_design_controller.py) (controller helpers). `Water Engineering` added to [`modules.txt`](erpnext_enhancements/modules.txt).

## [1.89.0] - 2026-06-23

### Fixed
- **QuickBooks import no longer prefixes Project titles with their own number.** A QBO sub-customer / job carries a `DisplayName` that mirrors the ERPNext project it belongs to, prefixed with that project's number (`PRJ-401 - 4th West Fountain`, `PRJ000062 - Terror Ride Fountain`). The importer linked the job to its existing Project by `PRJ-###` number, but the **in-place update path** (`apply_values`) then re-applied every mapped value — including `project_name` — so the job's prefixed DisplayName overwrote the project's clean title on every re-sync. The result was ~377 projects reading `PRJ-00581 Myers Mortuary` instead of `Myers Mortuary` (the number is already the Project's `name`, so it was pure duplication). Two changes to [`core/mapping.py`](erpnext_enhancements/quickbooks_online/core/mapping.py):
  - **Strip the prefix on create.** `_map_qbo_job_to_project` now derives the title via a shared `strip_prj_prefix` helper (`_job_project_title`), which drops a single leading `PRJ-###` token and its separator. It tolerates every spelling the data carries (`PRJ-96`, `PRJ00097`, `PRJ-111 District …` with a space-only separator) and never blanks a title that is only a number.
  - **Stop the update path overwriting a set title.** A new `_protect_existing_project_title` guard drops `project_name` from the mapped values when the linked Project already has a non-blank title — *before* conflict detection, the field write, and the owned-field snapshot — so a job's DisplayName can never re-clobber a curated project title (and a manual rename no longer trips a false conflict). The title is set once on create and then owned by ERPNext.
- **Data remediation for titles already prefixed.** New manual, dry-run-by-default tool [`core/project_name_remediation.py`](erpnext_enhancements/quickbooks_online/core/project_name_remediation.py) (`strip_project_name_prefixes`) strips the redundant leading `PRJ-###` from existing Project titles using the same `strip_prj_prefix` transform. It is idempotent, batched/committed, per-record guarded, and writes via `frappe.db.set_value` (no doc hooks — a pure display-field denormalisation). Number-only titles (`PRJ-00614`) and mid/trailing-`PRJ` titles (`Ogden Temple PRJ-00612`) are reported and left untouched for manual review.

## [1.88.0] - 2026-06-23

### Added
- **"Prospect" checkbox on Customer gates the activity-reminder follow-ups.** A new `custom_prospect` Check field sits at the top of the second column of the Customer **Details** top section (`insert_after: column_break0`). It is the master switch for the inactivity reminder:
  - The **Activity Reminder** section (`custom_activity_reminder` — Reminder Days / Last Activity / Reminder Assignee) is now hidden unless Prospect is checked (`depends_on: eval:doc.custom_prospect`), so the reminder controls only surface for accounts that should send follow-ups.
  - The daily scheduler [`customer.customer_inactivity_reminder`](erpnext_enhancements/script_migrations/customer.py) now filters on `custom_prospect = 1`, so only flagged Prospect accounts generate the inactivity follow-up ToDo. The reminder window itself is unchanged — per-customer `custom_reminder_days`, falling back to the global `inactivity_threshold` (**Sales Activity Settings**).
  - The follow-up ToDo is now allocated to the account's **Reminder Assignee** (`custom_reminder_assignee`, the field surfaced in the Activity Reminder section) — so the assignment email reaches the chosen account executive — and only falls back to the document owner when that field is blank. Previously the field was a no-op and reminders always went to the owner (often the data-importer/admin).
  - **Account Status sync:** the Customer form ([customer.js](erpnext_enhancements/public/js/customer.js)) now auto-ticks `custom_prospect` when **Account Status** is set to `Prospect` or `Champion` (the statuses we actively follow up with; `Champion` already carries the 90-day cadence), and clears it otherwise — alongside the existing `custom_reminder_days` defaulting. The checkbox remains manually toggleable for other statuses.
  - **Behaviour change:** because the field defaults to unchecked, existing customers stop generating reminders until they are explicitly flagged as Prospects (or have their Account Status set/re-saved to Prospect/Champion) — this is the intended "prospects only" scoping. Flag the accounts that should receive follow-ups after deploy.

## [1.87.0] - 2026-06-22

### Fixed
- **QuickBooks sync stops churning QBO jobs into "Pending Review" on every Import All.** Two idempotency bugs in `upsert_entity` ([core/mapping.py](erpnext_enhancements/quickbooks_online/core/mapping.py)) meant a re-run re-flagged ~460 already-resolved jobs each time:
  - **Stale review flag never cleared.** When the already-linked update path re-saved a record cleanly it only set `conflict_status="Clean"`, leaving a stale `match_status="Pending Review"` (stamped by an earlier transient failure, e.g. the Project-Manager save error) in place — so ~280 jobs correctly linked to their Projects stayed stuck in review forever. The update path now also restores `match_status` to `Auto Matched` when it was parked, so a clean re-sync releases it.
  - **Consolidated no-Project jobs re-parked by the doctype-flip guard.** The remediation tool consolidates a QBO job with no matchable Project onto its parent **Customer**, but the live mapper always resolves a job to **Project**; the doctype-flip guard then saw "mapping=Customer vs run=Project" and re-parked the job (~180 of them, 90 "Sapphire Fountains Internal") on **every** run. A new *settled job-consolidation guard* runs before the flip guard: if the job is a QBO sub-customer already consolidated to a parent Customer and still has no matching Project, it's left consolidated (transactions roll up untagged) and its stale review flag cleared, instead of being re-parked. If a matching Project later appears, it falls through to the flip guard to be relinked by remediation.
  - Net effect: once deployed, the first healthy Import All settles the previously re-parked jobs automatically (no separate data restore needed) instead of churning them.

## [1.86.0] - 2026-06-22

### Fixed
- **QuickBooks sync no longer re-parks a record over a pre-existing invalid Link it doesn't manage.** When the sync **re-saves an existing record** (an update, or an auto-link), ERPNext re-validates *every* field on the document — including ones the sync never set. A live example: a `Project` (linked to a QBO job) whose `custom_project_owner` field (label **"Project Manager"**, a Link to **Employee**) was left holding an **email** (`james.harris@…`) by an earlier data load. ERPNext rejected the save with *"Could not find Project Manager: …@…"*, so the job was routed to manual review — and re-parked on **every** subsequent import, undoing the job→Project mapping each run (the Projects themselves were never harmed). `_persist_or_manual_review` now sets `doc.flags.ignore_links` on the **save** path so the sync doesn't fail on (and doesn't alter) such latent invalid links — the sync's own links are already resolved to real records via `_linked_name`, so nothing unvalidated is introduced. **Inserts (brand-new records) still validate links normally.** (Operationally, the ~351 Project Manager values that mapped to a real Employee were also corrected; ~64 with no Employee record were left for manual review — this fix is what stops them re-parking jobs in the meantime.)

## [1.85.0] - 2026-06-22

### Fixed
- **Drive shadow sync no longer crashes on a deeply-nested folder whose path-prefixed name exceeds 140 chars.** `run_shadow_sync` builds each shadow `File.file_name` as the `/`-joined folder path plus the item name; for a deep tree this can exceed the `File.file_name` `Data(140)` limit, raising `CharacterLengthExceededError` on insert and failing the *entire* sync for that Customer/Project (seen on **CEM Aquatics** after orphan-job folders were relocated under their parent during the QBO job remediation, deepening a few trees). The shadow name is now capped to 140 characters, keeping the meaningful tail (the actual file/folder name) so the link stays useful.

## [1.84.0] - 2026-06-22

### Fixed
- **QuickBooks import: recover three classes of records that were parking for manual review.** A triage of the ~323 still-parked QBO records found three fixable mapper gaps (≈70 records, plus cascades):
  - **Scheme-less website → URL validation failure (22 masters, + cascades).** A pre-existing ERPNext Customer/Supplier with a bare-domain `website` (e.g. `www.fountainpeople.com`, `teamcomma.com`) fails ERPNext's whole-doc URL re-validation when the sync auto-links and saves it, parking the master — which then **cascades** (that vendor never maps, so its Bills/Bill Payments can't resolve a supplier party and park too). `_persist_or_manual_review` now heals scheme-less URL-type fields (prefix `https://`) before saving, via `_heal_invalid_urls`. Recovers the masters and, on the next sync, their downstream transactions.
  - **Purchase Order missing "Required By" (20).** QBO POs carry no delivery date, so `_map_purchase_order` left `schedule_date` empty and ERPNext rejected the PO ("Please enter the Required By."). It now defaults the header and each line's `schedule_date` to the PO's `DueDate` (else its `TxnDate`).
  - **Journal Entry line to A/R / A/P with no party (10).** A QBO `JournalEntry` line posting to a Receivable/Payable control account carries an `Entity` (the Customer/Vendor), which `_journal_accounts` ignored — so the line failed "...requires a Party for Receivable/Payable account" and the whole entry parked. It now carries the QBO line `Entity` through as the line's `party_type`/`party` (a Customer `Entity` that is a job resolves to its top-level Customer), via `_journal_line_party`. Validated: all 10 parked JEs resolve a party.

  Validated against live data; no change to what's imported beyond these previously-parked records. (The companion operational step — setting the Company's **Stock Received But Not Billed** default account, which recovers ~8 stock-item Bills — is a config change, not code.)

## [1.83.0] - 2026-06-22

### Fixed
- **QuickBooks sync can no longer auto-create a duplicate/orphan Project from a QBO job.** The live sync routes a QBO sub-customer/job to an ERPNext **Project**, linking to an existing one via its `PRJ-###` number (else `project_name + customer`). An adversarial audit found two paths where it instead *created* a new Project unsafely — automatic via the hourly `cdc_poll`/`retry_failed_syncs` whenever `sync_enabled` is on:
  - **Doctype flip:** a QBO Customer id first imported as an ERPNext **Customer**, then reclassified as a job, now resolves to **Project**. The in-place-update guard keys on the freshly-resolved DocType, so `frappe.db.exists("Project", <customer-name>)` is False and the update is skipped; `_match_project` scans only `tabProject` and can't relink the Customer, so a **new Project is created and the Customer is orphaned** — a Customer+Project pair for one qbo_id.
  - **Unlinked Project:** a net-new job with no `PRJ-###` number whose parent Customer isn't mapped yet — `_match_project`'s `project_name + customer` fallback drops the empty customer filter and (on no title match) the create path makes a **Project with no customer**, which can also collide with another customer's same-titled job.

  Three guards, all biased to **manual review** (never a silent create):
  - `upsert_entity` now detects a **doctype flip** (the qbo_id is already linked to a *different*, still-existing DocType) and defers to manual review instead of creating — the `job_remediation` tool relinks/merges these.
  - `validate_mapped_values` blocks creating a **Project with no resolved parent Customer** (create-time only — a `PRJ-###` job still links first); a later sync creates it linked once the parent imports.
  - `_match_project` no longer falls back to a **title-only** lookup when the parent Customer is unresolved (which could mislink to a same-named project under a different customer).

  Validated against live data: all 15 already-created Projects and 60/60 sampled jobs have a PRJ# and a resolvable customer, so legitimate jobs still link/create unchanged — only the junk/flip cases (which were the ones producing duplicates) now park for review.

## [1.82.0] - 2026-06-22

### Fixed
- **QuickBooks "Conflict" status no longer fires on fields ERPNext normalises (≈3,600 false positives).** `detect_conflicts` flags a record as conflicted when its ERPNext value differs from the last QBO-synced value in `owned_fields`. But `save_mapping` snapshotted `owned_fields` from the mapper's **input** values, while ERPNext **rewrites** several on save — `conversion_rate` / `plc_conversion_rate` / `source`/`target_exchange_rate` `1 → 1.0`, a Payment Entry's auto-generated `remarks`, an Item `description` stripped of HTML. The stored value never matched the snapshot, so every Invoice / Payment / Estimate / SalesReceipt / Item was flagged `Conflict` on the next sync — which *also blocks* those records from receiving further QBO updates (a non-overwrite resync skips conflicted rows). `owned_fields` is now snapshotted via `_owned_snapshot`, which reads each mapped scalar field back **off the saved record**, so the baseline reflects what ERPNext actually kept and a record only conflicts when its value genuinely moves. Validated on live data: every sampled false conflict (`conversion_rate`/`remarks`/`description`/exchange rates) drops to none. Child-table fields are unaffected (detect_conflicts already skips them). *Existing* `Conflict` rows clear when each record is next synced (or via a one-off re-snapshot); this stops new ones from forming.

## [1.81.0] - 2026-06-22

### Changed
- **QBO job remediation now LINKS existing Projects only — it never creates one.** Sapphire already holds every QBO project in ERPNext (often named differently than the QBO job), so `consolidate_qbo_jobs` matches a job to an existing Project by its `PRJ-###` number (or an exact `project_name`+customer) and links it; a job with **no** match (the internal / `(deleted)` / differently-named ones) is consolidated into its parent Customer with **no project** — its invoices roll up to the parent untagged. The QBO Sync Mapping is repointed to the matched Project, or to the parent Customer when there is none, so the paused sync resumes against a real record rather than recreating the `Parent:Job` colon customer. Supersedes v1.79.0's create-if-missing behaviour, which produced duplicate projects. Drive-folder cleanup is also guarded to genuine colon-job merges so a re-run can never trash a parent customer's real folder. *(Forward-fix create path in `mapping.py` for the live sync is unchanged here and still needs the same no-create treatment before the sync is re-enabled.)*

## [1.80.0] - 2026-06-22

### Fixed
- **A long QuickBooks import/CDC no longer fails at the finish line with `TimestampMismatchError`.** `import_all` (and `run_cdc`) load the `QuickBooks Online Settings` Single once at the start and saved it at the very end to stamp `status` / `last_full_import` / `last_cdc_sync`. But a full import runs for ~80 minutes, which spans the **hourly token refresh** — and that scheduled task modifies the same Settings doc. So the end-of-run `settings.save()` on the now-stale doc raised `TimestampMismatchError: ... has been modified after you have opened it` and **failed an otherwise-successful run** (observed on a live import: all data committed fine via the per-batch commits, but the run was marked Failed and `last_full_import` never stamped — which also makes the scheduler retry it). The end-of-run status fields are now written with `frappe.db.set_value` (a new `_record_settings_status` helper) — an atomic per-field write with no whole-doc optimistic-lock check and no race, leaving the token fields the refresh wrote untouched. `fail_log` uses the same helper so the error path can't raise a *second* `TimestampMismatchError` that masks the original failure.

## [1.79.0] - 2026-06-22

### Fixed
- **QBO job→Project mapping: resolve Project `status` against the site's options instead of hard-coding `"Open"`.** Sites customize the Project `status` Select via Property Setter (Sapphire uses `Active / Client Hold / Parked / Completed / Invoiced / Paid / Canceled` — no `Open`). `_map_qbo_job_to_project` hard-coded `status="Open"`, so creating a Project for a QBO job (the live sync's path for any new job, and the v1.78.0 remediation's path for jobs with no existing project) failed validation with *"Status cannot be 'Open'"*. It now uses `_select_option("Project", "status", ("Open", "Active"))` (and the remediation marks `(deleted)` jobs `Canceled`/`Cancelled` via the same resolver), falling back to the field's first option — valid on both stock and customized sites. **This unblocks the live sync's project creation, not just the remediation.**
- **Job remediation: fix the customer merge call + an empty `_assign` crash.** (1) `frappe.rename_doc()` on this Frappe version takes no `ignore_permissions` keyword — the v1.78.0 call raised `TypeError` on every merge; removed it. (2) `rename_doc(merge=True)` runs `orjson.loads` on each party's `_assign`/`_liked_by`, and customers carrying an empty string there (left by an earlier import) crashed the merge with `JSONDecodeError: zero-length document`; the remediation now normalizes those `""`→`NULL` on both the job and its parent before merging.

## [1.78.0] - 2026-06-22

### Fixed
- **One-off remediation to consolidate the legacy `Parent:Job` Customers + orphan Drive folders left behind by the pre-1.76.0 importer** (`quickbooks_online/core/job_remediation.py`, run manually via `bench execute` — *not* on migrate). v1.76.0 fixed the importer going forward; this cleans up the records the old behaviour already created. `consolidate_qbo_jobs` walks the QBO job-customers (identified via QBO Sync Mapping + the raw payload's `Job`/`ParentRef`, never a blind name `LIKE '%:%'`, so it skips a legitimately colon-named non-job customer and still catches jobs that auto-linked to a non-colon-named customer), top-level-first, and for each: links/creates the Project, tags the job's Sales Invoices, **merges** the job-Customer into its top-level parent (`frappe.rename_doc(merge=True)`, moving invoices/payments/quotations/addresses), repoints the QBO Sync Mapping to the Project, and cleans the orphan Drive folder (trashes if empty, else relocates under the parent customer folder via new `drive_utils.move_folder`/`trash_folder`/`get_folder_meta` helpers). **Dry-run by default**, idempotent, batched/committed, per-record guarded; Drive folders are trashed (recoverable), never hard-deleted. See `MIGRATION_NOTES.md` §6 for the runbook. *(Production QBO sync was paused pending deploy; run the remediation before re-enabling it so the resumed sync updates Projects instead of recreating the flat Customers.)*

## [1.77.0] - 2026-06-22

### Fixed
- **Travel Settings (and the rest of the Travel module) no longer 404 / crash on a site without the HR module — HRMS is now treated as the optional dependency it always was.** Opening **Travel Settings** returned a hard `404` (`frappe.desk.form.load.getdoc` → `DoesNotExistError: DocType Expense Claim Type not found`). The Single doc itself was fine; the failure was in Frappe core's link-*title* resolution: Travel Settings has six `Link → Expense Claim Type` fields (`flight/hotel/ground/misc/per_diem/mileage_expense_type`), the stored singles row had them populated (`Travel`/`Others`), and `Expense Claim Type` is an **HRMS** doctype — which is **not installed** on this site. `getdoc` tried to fetch the link titles, `frappe.get_meta("Expense Claim Type")` threw, and the HTTP layer turned that into a 404, leaving the form impossible to even open and clear. (The doctype only ever came from `hrms`; the app ships an `enhancements_core` controller *stub* with no JSON, so `bench migrate` never creates it.)

  HRMS was always meant to be optional — the travel *finance* surfaces (Expense Claim / Employee Advance / Vehicle Log generation, Expense Claim Type mapping) ride on it, while itinerary, logistics, per-diem and mileage do not. They now degrade gracefully instead of hard-failing:
  - New `travel_management.expense_claims_available()` probes for the `Expense Claim Type` doctype (the exact thing the links resolve against).
  - **Travel Settings** clears the six Expense Claim Type fields on save when the doctype is absent (so a stored value can never re-404 the form) and flags availability to the client; the new `travel_settings.js` hides the *Expense Claim Types* section and explains why, keeping the per-diem/mileage/automation settings usable.
  - **Travel Trip**: the *Create → Expense Claims / Employee Advance / Vehicle Log* buttons are hidden when HRMS is absent (the Lead/Opportunity/Itinerary actions stay), and the `api.py` create endpoints (`create_expense_claim(s)`, `create_employee_advance`, `create_vehicle_log`) now throw a clear "HR module not installed" error instead of a raw `DoesNotExistError`.
  - *Production data fix:* the six orphaned `*_expense_type` values already stored on the live Travel Settings singles row were cleared (they referenced the missing doctype), which immediately restored the page. Re-pick them under Travel Settings → Expense Claim Types if HRMS is later installed.

## [1.76.0] - 2026-06-22

### Fixed
- **QuickBooks sub-customers / "jobs" now import as ERPNext Projects under the parent Customer — not as flat colon-named Customers (which also spawned orphan Drive folders).** A QBO job (a sub-customer flagged `Job`/`IsProject`, carrying a `ParentRef`, or at `Level` > 0) was mapped to a flat ERPNext Customer named with QBO's `FullyQualifiedName` — the colon path `Parent:Job` (e.g. `4th West Apartments:PRJ-401 4th West Fountain Control & Pump Repair`). Each such Customer then tripped the `Customer` `after_insert` Google Drive hook, creating a malformed **top-level** Drive folder that never nested under the real customer folder — hundreds of orphans, one per job. The customer mapper now detects a job and routes it to a **Project** under the top-level parent Customer: it links to the existing ERPNext project by its `PRJ-###` number (zero-padding ignored, so QBO `PRJ-401` matches `PRJ-00401`), else creates one. A job's invoices, sales receipts, payments and estimates resolve to the **parent** Customer and tag the Sales Invoice with the Project for job costing (`_resolve_customer_ref`). Customers are imported top-level-first (`sync.query_entity_payloads`) so a job's parent is mapped before the job resolves. *(Forward fix only — existing flat `Parent:Job` Customers and their orphan Drive folders are cleaned up by a separate remediation; production QBO sync was paused pending deploy.)*

## [1.75.0] - 2026-06-22

### Changed
- **QuickBooks Online sync now commits every 100 records, not just per entity (bounds the shared naming-series lock).** Follow-up hardening to v1.73.0. Every document insert takes a `FOR UPDATE` lock on the `{#####}` naming-series counter — a `tabSeries` row keyed by the empty string and **shared by every `format:…{#####}` doctype on the site** — held until the transaction commits. v1.73.0 committed per *entity*, so a large entity (e.g. ~2,000 `Purchase`s) still held that global counter for the minutes it took to fetch + upsert, briefly blocking unrelated record creation across the whole site during a full import. `import_all`, `preview_resync`, `run_cdc` and `run_resync` now commit every `QBO_COMMIT_EVERY` (100) records, bounding the lock hold to a few seconds while keeping commit overhead negligible; each entity's tail is still flushed at its boundary, and progress remains durable/idempotent on a late failure. (`run_resync`'s prior fixed 200-record batch now uses the same constant.) No behavior change to what gets imported — only commit cadence.

## [1.74.1] - 2026-06-22

### Fixed
- **Google Drive shadow sync no longer times out (and stops spamming the document timeline).** The hourly `Drive → ERPNext` shadow walk ran on a short 300s worker, and a large first-time sync of a project's Drive tree blew that budget mid-insert (`JobTimeoutException` on PRJ-00275). Two compounding causes, both fixed:
  - The hourly entry now just **hands the walk to the `long` queue** (`timeout=3600`) instead of doing all the work inline on the 300s `default` worker, and it **commits per document** so a later failure or kill no longer discards every finished document's shadows.
  - Each shadow `File` was inserted with `attached_to_*` set, firing Frappe's `after_insert → create_attachment_record`, which adds an "Attachment" comment to the reference doc **and publishes realtime per file** — the exact slow path the timeout fired in, and on a first-time sync it buried the Project/Customer/Opportunity timeline under one *"Added &lt;file&gt;"* comment per shadow. Shadows are now inserted unattached and linked via `db_set` (which runs no hooks), so they still appear in the attachment sidebar without the comment, realtime, or per-file cost.

## [1.74.0] - 2026-06-22

### Fixed
- **QuickBooks Online: grouped bank deposits now import, by modeling Undeposited Funds correctly** (follow-up to the v1.73.0 *Import All* fix). In QBO, a customer payment with no `DepositToAccountRef` lands in **Undeposited Funds**, and a later **Deposit** sweeps it to the bank via `Line` entries that carry a `LinkedTxn` (to the Payment) and **no `AccountRef`**. The importer mishandled both legs:
  - `_map_payment_entry` posted *every* customer receipt straight to the default **bank**.
  - `_map_deposit` couldn't resolve the AccountRef-less sweep lines, so it dropped the credit leg — leaving the deposit with only a bank debit and parking it as *"Journal Entry is unbalanced (debit … vs credit 0.00)"*. This was the bulk of the parked `Deposit` backlog.

  Had we only credited the bank's counter-account on deposits, the bank would have been **double-counted** (once by the payment, once by the deposit). The coupled fix:
  - Customer **Payment Entries** now set `paid_to` = the payment's `DepositToAccountRef` when present, else **Undeposited Funds** (falling back to the bank only when no UF account is imported, preserving prior behavior).
  - **Deposit** sweep lines (a `LinkedTxn`/`PaymentMethodRef` line with no `AccountRef`) now **credit Undeposited Funds**.

  The two legs net Undeposited Funds to zero and the bank is counted once — correct double-entry, and the deposits import instead of parking. New helper `_undeposited_funds_account(settings)` resolves the account by QBO's standard name, scoped to the company. Vendor payments and account-referenced deposit lines are unchanged.
  - *Verification note:* if a Payment Entry ever fails to validate with `paid_to = Undeposited Funds`, the create path's `_persist_or_manual_review` routes that record to **manual review** rather than failing the run — so the change can only ever match or improve on the current parking behavior, never regress to a hard failure. Worth confirming on the dev instance during the first import.
- **Not addressed here (correctly parked, not mapper bugs):** account-based QBO `PurchaseOrder`s (ERPNext POs require item lines) and `Estimate`s whose `CustomerRef` isn't mapped yet (resolve once masters import) continue to route to manual review.

## [1.73.0] - 2026-06-22

### Fixed
- **QuickBooks Online "Import All" (and CDC / Resync) silently did nothing — fixed the single-transaction naming-series lock.** Clicking *Import All* enqueued the job, a worker ran it, but it died immediately with `(1205, 'Lock wait timeout exceeded; try restarting transaction')` at `sync.start_log` → `log.insert` → the `format:QBO-SYNC-{YYYY}-{#####}` naming-series increment — **before any Sync Log row was created**, so nothing appeared in the dashboard and the click looked like a no-op. Root cause: every batch operation (`import_all`, `preview_resync`, `run_cdc`, `run_resync`) opened its `QuickBooks Sync Log` and then did the **entire run inside one uncommitted transaction**, committing only at the very end. That single transaction held the row lock on the `QBO-SYNC-{YYYY}-` `tabSeries` counter for its full duration (minutes to hours, or forever if the worker hung), so **every other run's `start_log` blocked on the series and timed out** — one stuck/long import wedged all subsequent imports, CDC polls and resyncs. The held log row was also uncommitted, hence invisible to the dashboard *and* to the `run_in_progress` / `api.import_all` "already_running" guards, so duplicate runs piled up instead of being refused. Fixes:
  - `start_log` (and `_resume_or_start_log`) now **commit immediately** after creating/transitioning the log — releasing the naming-series lock at once and making the Running run visible to the dashboard and the concurrency guards.
  - `import_all` and `preview_resync` now **commit after each entity**, `run_cdc` **after each entity batch**, and `run_resync` **every 200 records** — so a long run no longer holds locks across its whole duration, live counters surface as it works, and a late failure keeps the progress already made (the upserts are idempotent, so a retry resumes cleanly) instead of rolling the entire run back.
  - Operational note: an in-flight run started under the old code holds the lock until its worker is recycled; a `bench restart` (which a deploy performs) clears it. After deploying this, *Import All* creates a visible Running log and proceeds.

## [1.72.0] - 2026-06-21

### Fixed
- **QuickBooks Online sync: stopped the runaway retry storm and the concurrency races it caused.** The integration had accumulated **hundreds of Failed CDC runs** (and would have detonated on the next reconnect). Root cause: `sync.retry_failed` re-ran the **global** `run_cdc()` / `import_all()` **once per failed log** — and since every failed run creates another failed log, N failures spawned N re-runs that spawned more, never converging. Those re-runs also overlapped and raced on the same `QuickBooks Sync Mapping` rows, throwing `TimestampMismatchError` (which then failed otherwise-fine records like vendor `Purchase` entries). Fixes:
  - `retry_failed` now re-runs each global operation **at most once per pass** (it still bumps each eligible log's `retry_count` to record the attempt), so the failed list drains instead of amplifying.
  - `run_cdc` and `import_all` gained a **concurrency guard** (`run_in_progress`) so two polls/imports can't execute at once. The guard is **stale-aware**: a Running/Queued log past a per-type window (1h for CDC, >10h for an import) is treated as orphaned so a crashed run can't block new ones forever.
  - New `reap_stale_runs` (run at the top of `retry_failed`) marks orphaned Running/Queued logs Failed — clearing, among others, a stuck "Running" Import All that was silently blocking **every** future import via the dashboard guard.
  - `safe_upsert` now **retries a record once** on `TimestampMismatchError` (the upsert re-reads the mapping and target doc on entry) instead of parking a good record as Failed.
- **Old-dated transactions no longer hard-fail the whole import.** A QBO transaction whose date falls outside every configured ERPNext Fiscal Year (e.g. a 2022 `Estimate` on a company whose earliest Fiscal Year was 2025) threw `FiscalYearError` on insert and failed the entire run — which the retry storm then re-failed forever. The create path now mirrors the existing update/link path: an insert-time `ValidationError` routes that one record to **manual review** with the validation message instead of aborting the batch (`mapping._insert_or_manual_review`). (Operationally, Fiscal Years 2020–2024 were also added on the instance so the historical estimates import rather than parking.)
- **Journal-line mapping hardened against a zero/zero row.** `mapping._journal_accounts` now drops a `JournalEntry` line whose `Amount` is non-zero but whose `PostingType` is missing/unexpected — previously it produced a row with both Debit and Credit zero, which ERPNext rejects ("Both Debit and Credit values cannot be zero"). (The `_ledger_line`-based mappers were already safe; this closes the same gap on the native `JournalEntry` path.)
- **Bounded the 401 token refresh/retry.** `client.request` / `upload_attachable` refreshed the access token and retried on a 401 with **no cap**: a persistently-401 response recursed forever, re-rotating (and thus invalidating) the refresh token on every pass — a self-inflicted way to kill the grant. They now refresh-and-retry **at most once**, then surface the error.
- **Four AI status tools were returning a 500 instead of a status.** `quickbooks_sync_status`, `stripe_payment_status`, `document_intake_queue` and `closed_won_handoff_status` built their counts with `frappe.get_list(fields=["count(name) as count"])`, which this Frappe version rejects ("SQL functions are not allowed as strings in SELECT") — so each tool failed outright. Replaced with permission-aware tallies over the user's visible rows (preserving the tools' documented permission-awareness).

## [1.71.0] - 2026-06-19

### Added
- **AI tools now advertise their mutation/risk to MCP clients (device-safety hardening).** Every *mutating* assistant tool — the six MDM device actions (`remote_lock_device` / `remote_wipe_device` / `locate_device` / `reboot_device` / `run_device_script` / `deploy_device_patch`) plus `create_followup_task` — now sets an `annotations` attribute derived from `_gate.py`'s single-source classification via a new pure `annotations_for()` helper. FAC forwards a tool's `annotations` verbatim in its MCP `tools/list` response, so a connected MCP client (notably **Triton**) can read each tool's mutation flag (`readOnlyHint` / `x-ee-mutation`) and risk band (`destructiveHint` / `x-ee-risk` = low/medium/high) from the catalog instead of *guessing from the verb*. That guess was the bug: Triton's verb-based classifier treated the oddly-named device tools as read-only and ran them **without its confirmation step**, so a model hallucination or prompt injection could lock/wipe a device or run a remote script with no human in the loop. (ERPNext's own server-side AI write-gate still applies — but it ships dormant by default, so the client gate mattered.) A contract test now enforces that every `APP_MUTATING` tool advertises the metadata, so a future write tool can't ship without it.

### Notes
- ERPNext-side change is purely **additive** — older MCP clients simply ignore the new `annotations` field. The companion change that *consumes* these annotations (and adds a hardened device fallback) lands in the `triton` repo. Discovered by FAC on `bench restart`; no `bench migrate` required.

## [1.70.0] - 2026-06-19

### Added
- **AI assistant status tools for four subsystems that had no AI surface.** The Frappe Assistant Core (FAC) tool catalog had been frozen since the Maintenance/Project/Workforce/Device batches, so every subsystem shipped since (Stripe, QuickBooks Online, Accounting Intake, the Closed-Won hand-off) was invisible to the assistant — and, because Triton discovers ERPNext's tools through FAC at runtime, invisible to Triton too. Four new **read-only** tools close that gap (in `assistant_tools/`, registered in `hooks.py`, classified read-only in `_gate.py` `EXPLICIT_READONLY`):
  - **`stripe_payment_status`** — connection/config state, a count of Stripe Payments by status, and the two trouble signals (`unreconciled_paid` = Paid but not yet reconciled to a Payment Entry; `failed_webhooks` = errored Stripe Events), plus recent payments. Gates on **Stripe Payment**.
  - **`quickbooks_sync_status`** — QBO connection state (connected, realm bound, token expiry, last full-import/CDC/webhook), the count of Failed sync runs, and recent **QuickBooks Sync Log** rows; pass `sync_log` for one run's summary + error. Gates on **QuickBooks Sync Log**.
  - **`document_intake_queue`** — the Accounting Document Intake review queue: counts by workflow status, the needs-attention backlog (Needs Review + Needs Item Review + Failed), and one intake's extracted header/lines/proposed-matches. The read companion to Triton's `sfo_extract_document` filing tool. Gates on **Document Intake**.
  - **`closed_won_handoff_status`** — Opportunities marked **Closed Won** with no project created yet (the hand-off backlog), oldest first with days-waiting and total count; pass `opportunity` for its first-three hand-off step state. Gates on **Opportunity**.
- All four are read-only and **permission-aware**: counts and lists go through `frappe.get_list` (so a user only sees rows they may read), auxiliary settings/event reads are guarded with `frappe.has_permission`, and single-document detail paths call `_common.require_doc_read` first. No new Python dependency; FAC-optional invariant preserved (nothing in the app imports `assistant_tools`).

### Notes
- Tools are discovered by FAC on **`bench restart`** (and by Triton within its 10-minute MCP discovery cache thereafter). FAC's **custom_tools plugin must be enabled** on the site. No `bench migrate` required.
- This is PR 1 of a cross-repo feature-sync; a follow-up advertises each tool's mutation/risk class to Triton so its client-side confirmation gate stops mis-classifying the gated device tools as read-only.

## [1.69.0] - 2026-06-19

### Added
- **"Delivery" project category + "Products" value stream.** Added **Delivery** as a selectable option across the Projects-Dashboard category fields on **Project** (and the shared value stream on **Opportunity** / **Customer**): the `project_type` ("Project Stage") Link gains a **Delivery** Project Type, `custom_value_stream` gains a **Delivery** Value Stream, and the `custom_project_priority` / `custom_company_priority` Selects gain a **Delivery** option. Also added **Products** as a new **Value Stream** option. The Priority Overview and Portfolio Gantt now treat Delivery as client-facing alongside Build/Design/Rent/Service, and the Opportunity value-stream tag sync manages the new Delivery/Products tags. Masters are seeded idempotently by `seed_delivery_and_products_categories`; the Select options ship in the custom-field fixtures.
- **Custom HTML Blocks auto-import from source.** The four dashboard widgets (Projects Dashboard, Task Dashboard, Morning Briefing, Desk Shortcuts) are now upserted from their repo-root `Custom HTML Block/` sources on every `bench migrate` and placed on the **Home** workspace (`setup/custom_html_blocks.sync_custom_html_blocks`, an `after_migrate` hook). The repo is now the source of truth — editing the source files and migrating redeploys the block — superseding the older insert-only seed patches and the manual copy-paste-into-the-UI workflow.

## [1.68.0] - 2026-06-18

### Fixed
- **Opportunity "Hand-Off Process" tab now reliably appears.** The v1.67.0 fixtures that add the tab's custom fields (`custom_process_tab` / `custom_process_progress`) weren't applied on some deploys (model-sync + patches ran, but fixture sync didn't create them), so the tab was missing from the Opportunity form. Added an idempotent backstop patch (`ensure_opportunity_handoff_fields`) that creates the fields via `create_custom_fields` on migrate — patches always run, so it's deploy-agnostic. The fixtures remain the source of truth.

## [1.67.0] - 2026-06-18

### Added
- **Closed Won now prompts "Create project now?" (form + Kanban).** Marking an Opportunity **Closed Won** asks whether to create the Project right away — coupling the win to the hand-off so a deal can't sit "won but unconverted" by accident (PRO-0204 Step 1 → Step 2). The transition is detected server-side on the Opportunity `on_update` hook (so it fires from the form, a **Kanban** drag, a list edit, or the API) and the popup is shown via a global realtime listener.
  - **Yes** opens the existing create-project dialog (Project Template + Users to Notify), with **Users to Notify defaulting to the Account Executive + Project Manager role holders** (still editable), then runs the same background project creation as before.
  - **No** rolls the status back to its previous value and clears the `custom_date_closed_won` stamp.
  - Opening an Opportunity that is **already** Closed Won with no Project yet re-shows the prompt once (there, **No** simply dismisses — it was won intentionally earlier).
- **Hand-off step due dates are now counted in business days (Mon–Fri, skipping holidays).** A 2-day SLA set on a Friday now lands the following **Tuesday**, not Sunday. A new `sla_business_days` field on **Process Step Template** / **Project Process Step** drives `due_by` (the daily overdue escalation follows automatically); an optional **Hand-Off → Holiday List** setting (`handoff_holiday_list`) skips holidays in addition to weekends (blank = weekends only). Legacy `sla_hours` is retained and back-filled (`ceil(hours/24)`: 0→0, 24→1, 48→2).
- **Hand-off sequence reordered so the internal hand-off leads.** Steps are now **1** Mark Won → **2** Hold Hand-Off Meeting → **3** Create Project → **4** Create Accounting Project → **5** Receive Payment → **6** Outline Tasks → **7** Launch Meeting (the hand-off meeting now precedes project creation). The Project's Hand-Off Process tab follows the new order; existing projects/templates are renumbered by a patch.
- **Opportunity gets a read-only "Hand-Off Process" tab** showing the first three steps (Mark Won · Hold Hand-Off Meeting · Create Project) — the opportunity→project handover. Once a project exists the rows mirror that Project's live step statuses; the full 7-step tracker continues on the Project.

### Changed
- **The manual "Create Project" button on Opportunity was removed** (both the rich variant and its shadowed duplicate). Project creation is now reachable only through the Closed-Won prompt; any standard "Create → Project" entry is defensively hidden.
- **The Closed-Won team SMS is deferred until the Project is actually created** (enqueued from the project-creation background job) instead of firing on the won-save — so answering "No" to the prompt sends nothing. The alert now links to the created Project.

### Notes
- Requires **`bench migrate`** (new `handoff_holiday_list` setting; `sla_business_days` field + backfill; Opportunity Hand-Off Process tab fields; step-reorder patch) and **`bench build`** (bundled global desk JS). No new Python dependency.
- Optionally set **ERPNext Enhancements Settings → Hand-Off Process → Holiday List**; confirm the relevant users hold the **Account Executive** / **Project Manager** roles so the notify default resolves.

## [1.66.0] - 2026-06-18

### Added
- **Stripe Payments — payment authorization records + legal policy pages.** Compliance groundwork for surcharging and stored-payment/recurring charging:
  - **Autopay authorization (proof of authorization).** New **Stripe Autopay Consent** doctype records each stored-payment authorization — the exact consent text + a fingerprint, who accepted it, their IP/user-agent, channel, the Stripe setup session, and the saved method — captured at enrollment and activated when the setup-mode Checkout completes. Mirrors Nacha/card-network requirements (amount or how determined, timing/frequency, revocation) and retention. The autopay consent text is strengthened accordingly, and the customer portal now requires an explicit **consent checkbox** before enrolling.
  - **Revoke autopay.** A **"Revoke Autopay"** button (Customer form) and a portal **"Cancel autopay"** action detach the saved method at Stripe, clear the customer's autopay flags, and mark the consent **Revoked** (record retained) — the revocation path the authorization promises. New `client.detach_payment_method`, `saved_methods.revoke_autopay`, and `api.revoke_autopay` / `api.portal_revoke_autopay`.
  - **Policy pages** (guest-accessible Web Page fixtures, **counsel-review-pending**): **/payment-terms** (payment methods; credit-card surcharge — credit-only, ≤ cost/cap, debit excluded, ACH-to-avoid, disclosed + itemized; ACH debits; stored-payment & recurring authorization; taxes; receipts/PCI) and **/refund-policy** (refunds incl. surcharge returned full/prorated, cancellations, revoking autopay/ACH). Linked from the **/pay** portal; registered in `hooks.fixtures`.

### Notes
- Requires `bench migrate` (new doctype + Web Pages) and `bench build` (portal/Customer JS). No new dependency. **Surcharge sales tax is intentionally not applied to the fee** in this setup — confirm taxability with your CPA. The legal wording in the policy pages and the autopay authorization is **draft pending counsel review**, and surcharging remains gated by the go-live checklist in `docs/stripe_surcharging_compliance.md`.

## [1.65.0] - 2026-06-18

### Added
- **Stripe surcharging / fee pass-through (configurable, default OFF).** Pass card/ACH processing fees to customers, the compliant way. Because hosted Checkout can't detect debit vs credit, the flow is **method-first**: the payer (desk dialog or portal buttons) picks **Card** or **Bank (ACH)** and sees the fee before paying; the session is locked to that method and the fee is added as a **separate, labelled line item** with a **pre-payment disclosure** (Checkout `custom_text`). Settings: `surcharge_enabled`, `card_surcharge_percent` (validation caps it at the US 3% network max), `card_surcharge_flat`, `ach_fee_percent`, `ach_fee_flat`, `surcharge_income_account`, `surcharge_label`, `surcharge_disclosure`. Accounting: the invoice amount is allocated to the invoice and the surcharge is booked to the income account via a Payment Entry deduction (the bank/clearing account receives invoice + fee). A full compliance reference — US 3% cap, debit/prepaid prohibition, state bans (CT/MA/ME/PR) + California, Visa/Mastercard/Amex/Discover registration + disclosure, and the rule that refunds must return the surcharge — is documented in **`docs/stripe_surcharging_compliance.md`** with a go-live checklist. **Ships OFF**; complete the checklist (including 30-day advance notice to the networks/Stripe) before enabling.
- **Stripe Payments Phase 2 — saved payment methods + recurring / off-session charging.**
  - **Save a method:** a **"Set up Autopay"** action (Customer form + the customer portal `/pay`) starts a Checkout Session in **setup mode** with consent text; the webhook stores the payment method + a display label on the Customer (`custom_stripe_default_payment_method`, `custom_stripe_payment_method_label`, `custom_stripe_autopay_enabled`).
  - **Charge off-session:** a manual **"Charge Saved Method"** button (Customer form) and an **automatic charge when a Sales Invoice is submitted** for an autopay-enrolled customer — which also covers **scheduled / maintenance-contract billing**, since maintenance generates Sales Invoices. Off-session PaymentIntents post a Payment Entry on success (ACH `processing` handled), deduped by PaymentIntent. New `Stripe Payment` channel "Auto".
  - **Refunds:** a **"Refund"** button on Stripe Payment (operator-only) issues a full or partial Stripe refund; the `charge.refunded` webhook records the refunded amount + status. (Booking the GL reversal is a manual step for now.)

### Notes
- No new Python dependency (uses `requests`). Requires `bench migrate` (new Customer/Settings fields + `Stripe Payment` channel option) and `bench build` (form/portal JS). Verify on the Stripe **sandbox** before enabling surcharge or autopay.

## [1.64.0] - 2026-06-18

### Added
- **Stripe Payments integration — Phase 1 (sandbox-only).** A new `stripe_payments` module (Frappe module **"Stripe Payments"**) for taking customer payments via **Stripe-hosted Checkout** and recording them back into ERPNext as **Payment Entries**. Realizes the long-planned "text-to-pay via Stripe" (June 9 invoice-processing Phase 5). Mirrors the QuickBooks Online module's conventions (encrypted secrets, role-gated whitelisted RPCs, signature-verified webhook, audit/idempotency ledger). **Test mode only** for now — the Stripe account isn't live yet.
  - **Stripe Payments Settings** (Single): `environment` (Test/Live, default **Test**), `enabled`, `company`, publishable/secret keys + webhook signing secret (encrypted **Password** fields), deposit/clearing account, card & ACH Modes of Payment, `enable_card`/`enable_ach`, redirect routes. A **sandbox guard** (`get_api_key()`) refuses an `sk_live_` key while Environment is Test (and vice-versa), so the build cannot accidentally transact live. **No third-party SDK** — the integration talks to the Stripe REST API with `requests` (a Frappe dependency) and hand-rolls webhook signature verification, mirroring the QuickBooks client, so nothing needs to be `pip install`ed on the managed host.
  - **Payments against Sales Invoices and ad-hoc amounts**, with **cards + ACH** (`us_bank_account`). Initiation from **both** a **"Pay with Stripe"** button on submitted/outstanding Sales Invoices (open / copy / **email** / **text** the link — SMS reuses the existing Triton `send_system_sms`) and a **customer self-service portal** at **`/pay`** (Customer-role users pay their own invoices; added to the portal menu). Stripe redirects to a public **`/stripe-return`** landing page.
  - **Signature-verified webhook** (`…stripe_payments.api.stripe_webhook`, the only guest endpoint) with hand-rolled verification of Stripe's `t=`/`v1=` HMAC-SHA256 signature (constant-time, with replay-window tolerance); events are recorded as **Stripe Event** rows whose name *is* the Stripe event id, so a redelivery can't be ingested twice. Background reconciler posts a **Payment Entry** on success and handles **ACH's delayed settlement** (`checkout.session.completed` → Processing, then `checkout.session.async_payment_succeeded` → Paid), plus failed/expired/refunded transitions. Posting is deduped against existing Payment Entries by PaymentIntent id — **no double charges, no double posting**.
  - **Stripe Payment** ledger doctype (one row per checkout) + **desk dashboard** ("Stripe Payments") showing connection/config readiness, status counts and recent payments. Hourly scheduler tasks back-stop missed webhooks (`poll_pending`) and retry errored events (`retry_failed`).
  - `after_migrate` adds Stripe id back-reference custom fields (Customer / Sales Invoice / Payment Entry) and creates **"Stripe"** + **"ACH"** Modes of Payment, defaulting the Settings to them.

### Notes
- **Deploy:** `bench migrate` (new doctypes + custom fields + Modes of Payment) and `bench build` (form/portal/dashboard JS). **No new Python dependency** — uses `requests` (already a Frappe dependency), so no `pip install` is required (the host is a managed server).
- **Config before use:** enter test keys (`sk_test_`/`pk_test_`), set a **Deposit / Clearing Account**, and register the webhook endpoint (shown read-only on Settings; locally use `stripe listen --forward-to …/api/method/erpnext_enhancements.stripe_payments.api.stripe_webhook`) and paste its `whsec_…` signing secret. Verify on the Stripe **sandbox** before the account goes live.
- **Phase 2 (next):** saved payment methods + off-session/recurring charging for maintenance contracts (Stripe SetupIntents, ACH mandates, consent) and refund-initiation UI. This phase records refunds but does not yet reverse the Payment Entry.

## [1.63.1] - 2026-06-18

### Fixed
- **QuickBooks "Import All" and "Preview Resync" returned a 504 Gateway Timeout on real-sized companies.** `import_all`, `preview_resync`, `run_resync` and `retry_failed` ran the full multi-thousand-record sync **synchronously inside the HTTP request**, which exceeded the gateway/worker timeout (they page the QBO API sequentially and can run for many minutes). They now **enqueue a background job on the `long` queue** (10h timeout) and return immediately; progress is tracked in QuickBooks Sync Log exactly as before.
  - `import_all` no-ops with `{"status": "already_running"}` when an import is already running.
  - `preview_resync` pre-creates a Queued sync log and returns its id; the dashboard polls a new read-only `get_sync_log_summary` endpoint until the preview completes, then shows the change summary and offers Run Resync (`run_resync`, validated synchronously and also backgrounded). Enqueued with `enqueue_after_commit` so the worker never races the un-committed log row.
  - The dashboard "Import All" / "Preview Resync" / "Retry Failed" actions now report that work started in the background (watch Recent Sync Logs) instead of freezing the browser until completion.
  - Deploy: `bench build` (dashboard JS) and ensure a worker is serving the `long` queue.

## [1.63.0] - 2026-06-17

### Security
- **QuickBooks Online — access-control hardening on the whitelisted RPCs** (readiness for Intuit's app security review, which tests functional-level access control). The sync engine runs with `ignore_permissions=True`, so the `@frappe.whitelist()` entry points are the only access-control boundary — and they previously enforced none, meaning **any logged-in user** could invoke `import_all`, `disconnect`, `run_resync`, `sync_entity`, `sync_opening_balances`, etc. directly via `/api/method`. Each privileged endpoint now calls a new `_require_qbo_operator()` guard (`frappe.only_for(("System Manager", "Accounts Manager"))`) before doing any work. The two guest callbacks (`oauth_callback`, `quickbooks_webhook`) stay exempt by necessity — they remain gated by the one-time OAuth `state` token and the webhook HMAC signature.
- **Bounded API error bodies in logs.** `QuickBooksClient` now passes failed-response bodies through a new `_error_snippet()` (caps at 500 chars) before putting them in `QuickBooksAPIError` messages, so a failed write can't dump QuickBooks data wholesale into the Error Log / Sync Log (Intuit's "do not log QuickBooks data" requirement).

### Notes
- No schema/migration change; `bench build` not required (Python-only). Behavior is unchanged for System Manager / Accounts Manager users; other roles now receive a `PermissionError` from the QBO RPCs (as intended).

## [1.62.0] - 2026-06-17

### Added
- **QuickBooks Online — Disconnect / revoke + Intuit Disconnect-URL handler.** The integration can now cleanly tear down a connection in both directions, which production-keys setup needs (Intuit's app profile asks for a Disconnect URL):
  - **Disconnect button** on the QuickBooks Online Settings form (shown once connected) and the dashboard toolbar → new `api.disconnect` RPC. It best-effort revokes the OAuth2 grant at Intuit via new `QuickBooksClient.revoke_tokens()` (POST the refresh token to Intuit's `…/v2/oauth2/tokens/revoke`, `client_secret_basic` auth; new `REVOKE_URL` constant), then forgets the stored tokens/realm and marks the connection **Not Connected**.
  - **`api.disconnect_callback`** — register as the app's **Disconnect URL** in the Intuit developer portal (`…/api/method/erpnext_enhancements.quickbooks_online.api.disconnect_callback`). When a user disconnects the app from Intuit's My Apps page, this clears the now-dead local tokens and redirects to the dashboard. **Requires login** (not `allow_guest`), so it can't be used to force a disconnect anonymously.
  - New `utils.clear_oauth_tokens()` deletes the encrypted access/refresh token rows directly (`set_secret` no-ops on empty, so it can't clear a Password field), clears realm id / expiry, disables sync, and sets status — but keeps the client id/secret + webhook verifier so reconnect is one click. **Reconnect** is simply the existing Connect flow run again (same Redirect URI).
  - **Resilience:** the hourly `tasks.refresh_token_if_needed` now treats an `invalid_grant` refresh failure (refresh token revoked/expired — e.g. disconnected from Intuit's side) as a disconnect, clearing the dead tokens instead of erroring every run.

### Notes
- No schema/migration change (the Settings doc already holds these fields). `bench build` to ship the form/dashboard JS. After taking production keys, set the Intuit app's **Disconnect URL** to the `disconnect_callback` method URL above.

## [1.61.0] - 2026-06-17

### Added
- **Accounting Document Intake → QuickBooks write-back (Phase 2, part 2): attach the scan.** When the **"Push to QuickBooks"** button creates the QBO **Bill** / **Payment**, the original scanned document is now uploaded to that transaction as a QuickBooks **Attachable**, so the source paperwork lives alongside the entry in QBO (mirroring the attachment we already file in ERPNext + Drive).
  - New `QuickBooksClient.upload_attachable(...)`: a `multipart/form-data` POST to `/v3/company/{realm}/upload` (a JSON `file_metadata_0` part carrying the `AttachableRef → EntityRef` link + the binary `file_content_0`). Unlike `request`, it lets `requests` set the multipart boundary; refreshes the token once on 401, like the other calls.
  - `writeback.py::_attach_scan` resolves the scan from the originating **Document Intake** (`source_file`, falling back to a File attached to the posted doc), reads its bytes, and uploads it. **Best-effort:** any failure is logged and the push still succeeds — the Bill/Payment is already created and mapped, so attachment is never allowed to undo it.

### Notes
- Completes Phase 2 of the Accounting Document Intake → QBO write-back. Still gated by `Accounting Intake Settings.qbo_writeback_enabled`; verify on the QBO **Sandbox** before production.

## [1.60.0] - 2026-06-17

### Added
- **Accounting Document Intake → QuickBooks write-back (Phase 2, part 1).** A **"Push to QuickBooks"** button on a *submitted*, intake-created **Purchase Invoice** or **Payment Entry** creates the matching **Bill** / **Payment** in QuickBooks Online and links it back. New `quickbooks_online/core/writeback.py::push_to_qbo`:
  - Builds the QBO payload (inverse of the importer's `_map_purchase_invoice` / `_map_payment_entry`), resolving the Vendor/Customer/Account QBO ids via reverse `QuickBooks Sync Mapping` lookups.
  - **Loop-guard:** immediately seeds the Sync Mapping ledger for the new QBO id, so the hourly CDC importer's "already mapped → update" branch links the echoed transaction to the existing ERPNext record instead of importing a duplicate.
  - **Fails clearly** (never auto-creates QBO masters) if the supplier/customer or an expense account isn't yet linked to a QBO Vendor/Customer/Account.
  - Gated by `Accounting Intake Settings.qbo_writeback_enabled` (default off); restricted to Accounts Manager / System Manager; only intake-created docs, never re-pushed.
  - Adds `custom_source_document_intake` + `custom_qbo_id` to Purchase Invoice / Payment Entry (via `after_migrate`); the posting handler stamps the back-reference.

### Notes
- Scope: Purchase Invoice → Bill, Payment Entry (Receive) → Payment. **Attaching the scanned document onto the QBO transaction (Attachable/Upload API) is the next change (Phase 2, part 2).** Verify on the QBO **Sandbox** before production. Requires `bench migrate` (adds the custom fields) + `bench build` (the form button).

## [1.59.0] - 2026-06-16

### Added
- **Accounting Document Intake — intake channels (E5).** Documents now flow in automatically, all funneling through the single `intake.ingest_document` door:
  - **Email** (`channels.email_from_communication`, on Communication `after_insert`): PDF/image attachments of inbound emails received at the configured intake Email Account are ingested.
  - **Google Drive watched folder** (`channels.poll_watched_folder`, hourly): new files dropped into the configured Drive folder are downloaded, ingested, and (optionally) moved to a processed folder. Deduped by Drive file id.
  - **Mobile** (`channels.ingest_mobile_photo`, whitelisted): a phone-captured photo/scan ingested as the Mobile channel.
  - **Scheduler maintenance**: `retry_failed_intakes` (daily — re-enqueue Failed extraction/posting steps from their retry payloads) and `purge_old_intake_logs` (daily, 90-day retention).
  - Chat-origin documents are created directly by Triton (the `Document Intake` doctype already supports the Chat channel); that tool lands in the Triton T2 change.

### Notes
- The email channel needs an inbound **Email Account** configured + selected in Accounting Intake Settings; the Drive channel needs the watched-folder id set. Both are gated by `intake_enabled` (master switch, default off). Scheduler changes take effect after a `bench restart`.

## [1.58.0] - 2026-06-16

### Added
- **Accounting Document Intake — customer remittance & packing-slip handlers (E4).** Completes the four document-type posting handlers (Approved → draft, never submitted).
  - **Customer Remittance → Payment Entry** (`actions/customer_remittance.py`): a draft "Receive" Payment Entry for the customer, allocated against the reviewer-selected Sales Invoice when there is one (otherwise left on-account for the accountant to allocate). Recipe mirrors `quickbooks_online/core/mapping.py::_map_payment_entry`.
  - **Packing Slip → Purchase Receipt** (`actions/packing_slip.py`): a draft Purchase Receipt against the matched Purchase Order (ERPNext-only — packing slips have no QuickBooks counterpart). Requires a matched PO.
  - Both register with the `post_document` dispatcher (`actions/base.py`).

## [1.57.0] - 2026-06-16

### Added
- **Accounting Document Intake — posting handlers + filing (E3).** Approving a Document Intake now creates the draft ERPNext record and files the source document.
  - **Posting dispatch** (`actions/base.py`): `review.approve_document` enqueues `post_document`, which routes by `proposed_action`, creates a **draft** (docstatus 0 — never submitted), records `created_doctype`/`created_docname`, moves the document to **Posted**, then files it. Idempotent.
  - **Vendor Bill → Purchase Invoice** (`actions/vendor_bill.py`): invoices a matched Purchase Order (creating a draft Purchase Receipt first when the PO carries stock items — 3-way match), or builds a standalone PO-less PI from the line items. Also serves company-card receipts (`Create Purchase Invoice`). Field recipe mirrors `quickbooks_online/core/mapping.py`.
  - **Receipt / Expense → Expense Claim** (`actions/receipt_expense.py`): a draft Expense Claim for the reviewer's Employee.
  - **Filing** (`filing.py`): attaches the scan to the created record and (when enabled) pushes it to the party's Google Drive folder — find-or-creating an "Accounting & Legal" subfolder, provisioning per-supplier folders under a configurable Shared Drive + parent. Drive filing is best-effort and never fails the posting.
  - Adds `custom_drive_folder_id` to Supplier (via `after_migrate`) + a `Supplier` `after_insert` hook to provision its Drive folder.

## [1.56.0] - 2026-06-16

### Added
- **Accounting Document Intake — extraction wiring + review (E2).** Builds on v1.55.0: a received document is now extracted via Triton and staged for human review.
  - **Extraction mapping** (`extraction.py`): maps Triton's normalized Document AI output onto the Document Intake — header fields (party, number, dates, totals, PO number), line items, and `field_confidence`. `intake.run_extraction` now delegates here.
  - **Advisory matching** (`matching.py`, reusing `google_drive/drive_match.py`): suggests the Supplier/Customer and the source Purchase Order / Sales Invoice. Suggestions never block — the reviewer always decides.
  - **Item generation with review**: line items resolve to existing Items (by code/name); unmatched lines propose a new Item and route the document to **Needs Item Review** for the inventory clerk (Stock Manager) to approve before the Item is created (`review.approve_items`).
  - **Accountant review form** (`document_intake.js`): a document preview (PDF/image), an extraction-confidence indicator, and role-gated **Approve / Reject / Create Approved Items / Re-extract** actions (`review.py`). Approval moves the document to **Approved**; the per-type posting handler that creates the draft ERPNext record lands in the next PR.
  - The Triton client now authenticates with `Authorization: Bearer <ERPNEXT_GATEWAY_SECRET>`, matching Triton's `POST /api/v1/document-ai/extract`.

### Changed
- `Document Intake Line.item_review_status` is now editable (the inventory clerk sets Approved/Rejected); added a `Document Preview` HTML field to Document Intake.

## [1.55.0] - 2026-06-16

### Added
- **Accounting Document Intake — foundation (new `Accounting Intake` module).** First PR of a multi-PR feature: scan a document once, extract it with Google Document AI (via Triton), file it against the right party, and create the correct ERPNext record — with humans reviewing everything before anything is saved or submitted. This PR lays the spine:
  - **`Document Intake`** — the review-queue doctype (non-submittable) with a guarded status state machine (`Received → Extracting → Needs Item Review → Needs Review → Approved → Posting → Posted`, plus `Failed`/`Rejected`/`Duplicate`). Holds the source file, extracted header fields + raw JSON, proposed party/matches/action, line items, and outcome — built to honour **review-everything** (no auto-posting) and a two-stage review (inventory clerk approves new Items, accountant approves the transaction).
  - **`Document Intake Line`** (line items + advisory `matched_item` + a proposed-new-Item block gated by `item_review_status` for the inventory clerk) and **`Document Intake Match`** (advisory candidates with fuzzy score/tier) child tables.
  - **`Accounting Intake Settings`** (single) — Triton extraction-service connection (gateway + service secret, falling back to `Triton Settings`), intake-channel config, and Google Drive filing targets including a **configurable Shared Drive + parent folder** for per-supplier folders. **`Accounting Intake Log`** — audit + retry-payload log (same contract as `Drive Sync Log`).
  - **`triton_client.py`** — posts document bytes to Triton's `/api/v1/document-ai/extract`; extraction runs on Triton (which holds the GCP credentials and processor map), so ERPNext takes **no `google-cloud-documentai` dependency**.
  - **Intake door + dedup + manual upload** — one `ingest_document()` entry point with sha256 content-hash dedup, plus an "Upload Document" button on the Document Intake list view (`ingest_upload`).
  - Extraction wiring is gated by `intake_enabled` (default off) and inert until Triton is configured; the accountant review form, inventory-clerk Item review, per-type posting handlers, Drive filing, and the email/Drive/mobile/chat channels land in subsequent PRs.

## [1.54.0] - 2026-06-16

### Added
- **Public legal pages: End-User License Agreement (`/eula`) and Privacy Policy (`/privacy-policy`).** Two guest-accessible **Web Page** records shipped as version-controlled fixtures (`fixtures/web_page.json`, registered in `hooks.py`) — not authored on the site, so they deploy via `bench migrate` and round-trip through `export-fixtures`. Content is grounded in the app's actual data practices (customer/contact data, field-staff GPS via the Time Kiosk, call recordings/transcripts, MDM device identifiers + remote lock/wipe incl. BYOD, QuickBooks financial sync, and AI/LLM processing via Google Vertex AI and Anthropic). The Privacy Policy includes the **Google API Services Limited Use** disclosure (for Google verification), **A2P/Twilio SMS** consent + STOP/HELP terms, and **CCPA/CPRA** and **GDPR** sections; the EULA covers both customer-portal end-users and internal employees/contractors, governed by Utah law. Naming the entity **Sapphire Fountains LLC** with contact `info@sapphirefountains.com`.
  - Contact details: Sapphire Fountains LLC, 85 W 300 S, Bountiful, UT 84010, +1 (801) 837-2199, info@sapphirefountains.com.
  - **Login page footer links.** The login screen now shows "Privacy Policy · End-User License Agreement" links beneath the sign-in card, injected on `/login` by a new `login_enhancements.bundle.js` (registered via `web_include_js`) and styled (theme-aware) in `login_enhancements.bundle.css`. No Frappe template override.
  - Post-deploy: `bench migrate` publishes both pages, and `bench build` compiles the login bundles. Have counsel review before relying on the documents for any formal verification (e.g. submitting the URLs to Google/Intuit/Twilio).

## [1.53.0] - 2026-06-16

### Added
- **QuickBooks Online balance reconciliation (Reports API).** New `core/reconcile.py` plus a **"QuickBooks Balance Comparison"** script report (Accounts roles) that pulls QBO's **Trial Balance** report as of a date and compares it, account by account, against each linked ERPNext account's General Ledger balance — bucketing accounts as matched / mismatched / QuickBooks-only / ERPNext-only. This automates the post-import Trial Balance reconciliation in `MIGRATION_NOTES.md`. The Reports API returns computed ledger balances even when transaction/statement exports come back empty, which is what made the manual cut-over hard. `client.report()` is the new generic Reports-API helper (`/v3/company/{realm}/reports/{name}`); `reconcile_transactions()` additionally cross-checks each imported transaction's amount against its stored QBO raw payload. Surfaced on the dashboard as **Compare Balances** and **Reconcile Transactions**.
- **Opening-balance import from QuickBooks.** New `core/opening_balances.py` builds one balanced **Opening Entry** Journal Entry from the QBO Trial Balance and open customer/vendor balances: a line per leaf account, A/R and A/P broken out by party (ERPNext requires a party on Receivable/Payable lines), stock accounts excluded (post via Stock Reconciliation — reported back), and any residual squared off against the company's **Temporary Opening** account. Created as a **draft by default** for review; the dashboard **Import Opening Balances** action takes an as-of date and an opt-in submit. Audited via a new **Opening Balances** `QuickBooks Sync Log` type.
- **Three new master entities are now imported and synced:** QBO **Term → Payment Terms Template**, **PaymentMethod → Mode of Payment**, and **Class → Cost Center** (hierarchical, parents become group cost centers like Accounts). Terms/Payment Methods are imported before Customers/Vendors so a party's `SalesTermRef`/`TermRef` links to its Payment Terms Template, and all three auto-link to pre-existing ERPNext records by name. Added to `ACCOUNTING_ENTITIES`, `CDC_ENTITIES`, the dashboard entity list, and the import order.
- **QuickBooks Online operational dashboard** — native Number Cards (Failed Syncs, Records Mapped, Open Conflicts, Pending Review) and Dashboard Charts (Sync Runs daily, Syncs by Type, Syncs by Status) over the Sync Log / Sync Mapping doctypes, plus a "QuickBooks Online" Dashboard. Shipped as version-controlled fixtures.

### Notes
- All additions are read-only except the opening-balance Journal Entry, which is a reviewable draft unless explicitly submitted. No schema migration beyond the new "Opening Balances" Sync Log option and the new report/cards/charts; run `bench migrate` and `bench --site <site> export-fixtures` is not required (fixtures ship in-repo).

## [1.52.1] - 2026-06-16

### Fixed
- **MDM Integration spammed the Error Log with a retry storm on a standing Miradore `401 Unauthorized`.** A persistently-failing provider fanned out into an unbounded pile of failed syncs (and a `frappe.log_error` traceback for each): `sync.retry_failed` re-ran failed logs by calling `run_device_sync`, which **created a brand-new `MDM Sync Log` every time** instead of re-running the existing one — so the per-log `retry_limit` cap was defeated and the number of failed runs roughly *doubled each cycle*. A bad/expired API key (401) can never be fixed by retrying, so this never converged.
  - **Errors are now classified.** `MDMProviderError` carries the HTTP `status_code`; `routing.is_retryable_status` marks **400/401/403/404 as permanent** (auth/permission/not-found/bad-request) and everything else (5xx, 429, network timeouts) as transient. Missing-credential guards are flagged permanent too.
  - **Permanent failures pause the provider instead of retrying.** A non-retryable error sets a per-provider `*_auth_blocked` flag on **MDM Settings**; `sync_devices`, `retry_failed`, and `refresh_action1_token` all skip a paused provider. The pause **self-heals**: it clears on the next successful sync, on a passing **Test Connection**, or when the provider's credentials are re-saved in MDM Settings.
  - **Retries no longer fan out.** `run_device_sync(provider, log=…)` re-runs a failed log **in place** (resetting its run state, preserving `retry_count`) rather than spawning a new row.
  - **Handled provider errors no longer hit the Error Log.** Auth/API failures are recorded on the `MDM Sync Log` and the provider `status_message`; only genuinely unexpected exceptions are written to the Frappe Error Log.
  - Deploy: `bench migrate` (adds the two hidden `*_auth_blocked` fields). Existing piled-up *Failed* `MDM Sync Log` rows and old Error Log entries are inert after deploy and can be bulk-deleted; once Miradore's API key is corrected, run **Test Connection** (or re-save MDM Settings) to lift the pause.

## [1.52.0] - 2026-06-16

### Changed
- **Creating a Project from an Opportunity now renames the Opportunity's Drive folder in place** instead of creating a separate one. When the source Opportunity already has a Drive folder (`Opportunity.custom_drive_folder_id`), the "Create Project" flow renames that folder from `CRM-OPP-YYYY-##### - <name>` to `PRJ-##### - <name>` (e.g. `CRM-OPP-2026-00112 - Smith Residence` → `PRJ-00123 - Smith Residence`) and find-or-creates the standard project subfolders inside it — so any files uploaded during the opportunity stage carry straight over to the project, and no duplicate folder is left behind. If the Opportunity has no folder (folders were off, a Lead-party opportunity, or provisioning had failed) — or its stored id is stale (the folder was deleted/moved, surfacing as a 404) — it falls back to creating a fresh project folder tree under the customer, as before. New orchestrator `drive_utils.provision_project_folder_for_opportunity` (with `rename_folder`); the project-folder name format also changed from `PRJ-##### <name>` (space) to `PRJ-##### - <name>` (hyphen) to match the swapped opportunity prefix.
- **Opportunity Drive folder name now uses the Opportunity Name, not the built-in title.** `provision_opportunity_folder` names the folder `<Opportunity ID> - <custom_opportunity_name>` (falling back to `title`, then the bare ID) — e.g. `CRM-OPP-2026-00112 - Smith Residence`.
- **Default project subfolder renamed `Project Manager` → `Project Management`** (with its nested `Pictures`). Affects newly provisioned project folders that use the built-in template. ⚠️ If a custom subfolder template is configured on the live instance (`Project Folder Google Drive Settings → Subfolders`), update its `Project Manager` row to `Project Management` there too — that table is data, not code, and overrides the default when present. Existing project folders are **not** renamed retroactively.

### Added
- **Project Drive-folder provisioning is now recorded in the Drive Sync Log** (action `Provision Folder`, reference `Project`), matching the existing Opportunity/Customer provisioning entries — so the rename/create is visible in the audit log used to debug "didn't sync".

## [1.51.0] - 2026-06-16

### Added
- **Contact "Full Name and Role" title restored as app code.** The `custom_full_name_and_role` field (the Contact's `read_only`, `unique` title) was left **blank on every new Contact** after its source Server Script ("Contact - Set Full Name and Role Title", Contact / Before Save) was **disabled during the script migration and never ported**. Re-implemented as `erpnext_enhancements.script_migrations.contact.set_full_name_and_role`, wired to Contact **`validate`** (same trigger as the original), producing **`First Last-Party`** — the linked Customer/Supplier name as the suffix, no suffix for internal Sapphire Fountains contacts — with ` (2)`/` (3)` … disambiguation so the unique index never blocks a save.
  - Implemented as a **`doc_events` hook, not `override_doctype_class`** — the active Contact controller is the `crm` app's `CustomContact`, so overriding the class would collide. The field does **not** drive the record `name` (Frappe core `Contact.autoname` owns that: full name + `-N`); this only populates the title.
  - Context: preparing the QBO → ERPNext contact import, whose contacts carry this value explicitly so the load is independent of when this change deploys.

## [1.50.1] - 2026-06-16

### Fixed
- **CRITICAL — `bench migrate` deleted Frappe's built-in integration doctypes.** The module added in v1.39.0 was named **`Integrations`**, which **collides with Frappe core's own `Integrations` module**. On migrate, Frappe resolved its OWN integration doctypes (OAuth Settings/Client, Webhook, Integration Request, Google Calendar/Contacts/Settings, LDAP Settings, Social Login Key, Connected App, Token Cache, Geolocation/Push Notification Settings, …) to `erpnext_enhancements/integrations/`, didn't find them, and **deleted them as "orphaned"** — breaking login/website with 500 errors.
  - **Fix:** renamed the module **`Integrations` → `Integration Hub`** (folder `integrations/` → `integration_hub/`; GA4 Settings doctype + GA4 Dashboard / Integrations Health pages + the hub workspace reassigned). Patch `fix_integrations_module_collision` reassigns our surfaces and restores the `Integrations` Module Def to the `frappe` app; Frappe **re-creates its deleted integration doctypes** from `frappe/integrations/` on the next sync.
  - **⚠️ Deploy:** apply this **before** re-running migrate on any site whose integration doctypes are still intact (e.g. a site whose earlier migrate aborted before the orphan step). On a site already hit, re-running migrate restores the doctype *structure*, but rows in those tables (OAuth tokens, Social Login Keys, Connected Apps, Webhooks, Google creds) are lost and must be reconfigured.

## [1.50.0] - 2026-06-16

### Fixed
- **`bench migrate` crashed in the QuickBooks-split patch** — `rename_quickbooks_module` (v1.37.0) called `frappe.rename_doc("Module Def", …)`, but Frappe **forbids renaming app-owned (non-custom) Module Defs** (`ValidationError: Only Custom Modules can be renamed`), aborting the whole migration. Rewrote the patch to **not rename**: it now runs **post-model-sync** (after sync has created the `QuickBooks Online` Module Def from `modules.txt` and the QBO doctypes/page have reconciled to it via their JSON) and simply drops the orphaned `QuickBooks Time Integration` Module Def via a direct `frappe.db.delete` (reassigning any stragglers first). No data is touched — the QuickBooks Online Settings Single carries across.
- **Same hazard pre-empted in `retire_global_enhancements`** (v1.49.0): switched its Module Def removal from `frappe.delete_doc` to `frappe.db.delete` — `delete_doc`'s `on_trash` would try to delete the already-removed module folder and can balk on non-custom modules. (`move_*` backstop patches were already safe — they only use `db.set_value`.)

> Deploy: re-run `bench migrate`. The QB patch failed before being logged, so it re-runs cleanly (now in the post-model-sync phase).
- **QuickBooks Online customer/vendor Payments now import.** Live verification against the production ERPNext company surfaced that `Payment Entry` insert fails with "Reference No and Reference Date is mandatory for Bank transaction" — ERPNext requires those once a bank account is involved, and the mapper (added in v1.42.0) didn't set them. `_map_payment_entry` now sets `reference_no` (QBO `PaymentRefNum`/`DocNumber`/`Id`) and `reference_date` (`TxnDate`).

### Changed
- **`quickbooks_online/MIGRATION_NOTES.md` prerequisites expanded** from the live verification (a sample Journal Entry, Sales Invoice, Purchase Invoice and Payment Entry were inserted against the real company): documents the Company **default cost center** and **default expense account**, and — because this company runs **perpetual inventory** — the **Stock Received But Not Billed / Inventory / Stock Adjustment** accounts ERPNext demands even for non-stock Purchase Invoices. Also notes imported transactions are created as drafts (`docstatus = 0`).

## [1.49.0] - 2026-06-16

### Changed
- **Retired the `Global Enhancements` module** (module-reorganization PR 13 — the capstone). After Triton left for AI Governance (v1.44.0), Global held only two doctypes; both fold into Enhancements Core and the empty module is deleted:
  - **Additional Supplier Group** (child table) and **Directory Link Exclusion** moved `global_enhancements` → `enhancements_core`. No code changes — both are referenced only by name (`setup/supplier_groups.py`, `sync_contact.py`), and the `global_enhancements` Python module was imported nowhere.
  - Removed the `global_enhancements/` folder and the module from `modules.txt`.
  - Patch `retire_global_enhancements` (post-model-sync) reassigns the doctypes (+ any stragglers) to Enhancements Core and deletes the orphaned `Global Enhancements` Module Def. Idempotent; no data moves.
- **Enhancements Core workspace (sidebar)** added at `/app/enhancements-core` — the last module to get one: shortcuts to Enhancements Settings + Desk Shortcuts; a Settings card (ERPNext Enhancements Settings) and a Tools card (Enhancement Desk Shortcut, User Form Draft, Directory Link Exclusion).

### Module reorganization complete
- Every page/app now lives in a clearly-named module, and every user-facing module has its own sidebar (PRs 1–13, v1.35.0–1.49.0). Net: **12 → 18 modules** (Global Enhancements retired; QuickBooks split; Workforce, Integrations, Google Drive, Morning Briefing, Asset Management, Process Documentation added). `MODULE_PLAN.md` can be deleted.

## [1.48.0] - 2026-06-16

### Removed
- **Dropped the orphaned `Project Note` child-table doctype.** "Project Note" (singular, `istable`) was a leftover with **no parent** — a repo-wide grep for `"options": "Project Note"` is empty, and nothing imports or references it by name. The in-use project-notes child table is **`Project Notes`** (plural, on the Project Custom Field), which is untouched. Removed the `enhancements_core/doctype/project_note/` folder and added a **guarded, idempotent** patch (`drop_orphan_project_note`, post-model-sync) that deletes the DocType + its table — skipping (and logging) if `tabProject Note` somehow holds rows, so data is never silently dropped.

> Version note: this independent cleanup is numbered 1.48.0 to sit above the in-flight 1.47.0 PR (#458, Travel/Expense Claim Type); merge that first to keep the changelog ordered.

## [1.47.0] - 2026-06-16

### Added
- **Expense Claim Type on the Travel sidebar** (module-reorganization PR 12) — added an **Expense Claim Type** link to the Travel workspace's masters card (alongside Travel Trip / POI / Settings), since Travel's `trip_expense` and the six `Travel Settings` per-category fields all reference it.

### Notes
- The planned *move* of `expense_claim_type` Core → Travel was **not** done: "Expense Claim Type" is a **standard ERPNext (HR) doctype**, not a custom one — the `enhancements_core/doctype/expense_claim_type/` folder is a logic-free controller stub with no JSON, and nothing in the app sets the doctype's module. A standard ERPNext doctype can't be cleanly re-moduled (ERPNext re-syncs its JSON on every migrate and would reset it), so it's surfaced on the Travel sidebar instead. The vestigial stub in Core is left untouched.

## [1.46.0] - 2026-06-16

### Changed
- **New `Process Documentation` module** (module-reorganization PR 11) — the **Process Document** doctype (mermaid process diagrams) moves out of Enhancements Core into its own module + sidebar (`/app/process-documentation`):
  - Doctype moved `enhancements_core` → `process_documentation`. **No code changes** — it's referenced only by doctype name (hooks `doctype_js`, the `setup/process_documents.py` seeder, and a Link field on Process Step Template), and its form JS / public asset paths are unchanged.
  - Distinct from the **PRO-0204 hand-off engine** (`process_steps.py` / Process Step Template in Project Enhancements), which is untouched — Process Step Template's Link to "Process Document" resolves by name.
  - Sidebar: Process Document shortcut; a Documentation card (Process Document).
- Idempotent backstop patch `move_process_document_to_process_documentation` (post-model-sync) reassigns the `module` on existing installs — no data moves.

## [1.45.0] - 2026-06-16

### Changed
- **New `Asset Management` module** (module-reorganization PR 10) — the **Asset Booking** doctype (a submittable booking doc with a calendar view + map) moves out of Enhancements Core into its own module + sidebar (`/app/asset-management`):
  - Doctype moved `enhancements_core` → `asset_management`. The self-referencing enqueue paths (`update_asset_status`), the `check_availability` form call, and the calendar's `get_events_method` (`public/js/asset_booking_calendar.js`) were repointed to `asset_management.doctype.asset_booking.*`.
  - The app-level **`api/booking.py`** stays in `api/` (it creates Asset Booking docs by name) — consistent with the other app-level API modules.
  - Sidebar: Asset Booking + Asset shortcuts; a Bookings card (Asset Booking, Asset).
- Idempotent backstop patch `move_asset_booking_to_asset_management` (post-model-sync) reassigns the `module` on existing installs — no data moves; submitted bookings carry across.

## [1.44.0] - 2026-06-16

### Changed
- **AI/Triton consolidation** (module-reorganization PR 9) — the Triton assistant config and AI training doctypes are pulled into the **AI Governance** module so the AI surface lives in one place:
  - From Enhancements Core: **Triton Settings** (Single), **Training Insight**.
  - From Global Enhancements: **Triton Assistant Settings** (Single), **Triton Allowed User** (child table of Triton Assistant Settings).
  - Only two path references needed fixing (everything else is by doctype name): a self-referencing enqueue path in `triton_settings.py` and the `test_connection` RPC method string in `triton_assistant_settings.js`. The app-level `triton_chat.py` / `utils.triton_sync` are unchanged.
  - **AI Governance sidebar updated:** added a Triton Settings shortcut, a **Triton Assistant** card (Triton Settings, Triton Assistant Settings), and Training Insight to the Records card.
- After this, **Global Enhancements** holds only `additional_supplier_group` + `directory_link_exclusion` (to be folded into Core when Global is retired).
- Idempotent backstop patch `move_triton_to_ai_governance` (post-model-sync) reassigns the four doctypes' `module` on existing installs — no data moves; the Triton Singles' gateway URL / secrets / prompts / Twilio creds / allowed users carry across.

## [1.43.0] - 2026-06-16

### Added
- **Project Enhancements workspace (sidebar)** at `/app/project-enhancements` (module-reorganization PR 8) — the module's first sidebar: shortcuts to **Project Dashboard**, **Master Project**, **Project Contract**; cards for **Projects** (Master Project, Project), **Contracts** (Project Contract, Contract Template), and **Process and Settings** (Process Step Template, Project Dashboard Settings).

### Notes
- The planned move of `project_note` / `project_reminder_email` out of Enhancements Core was **dropped** after inspection: both are **child tables**, not standalone doctypes.
  - `Project Reminder Email` is a child table of `ERPNext Enhancements Settings`, so it stays in Core with its parent (same call as `collab_doctype` / `briefing_recipient`).
  - `Project Note` (singular) is an apparent **orphan** — no Table field references it; the in-use child table is `Project Notes` (plural, already in Project Enhancements). Flagged for separate cleanup rather than relocating dead code.

## [1.42.0] - 2026-06-16

### Fixed
- **QuickBooks Online CDC poll no longer fails with `400 ValidationFault` on `changedSince`.** `run_cdc` passed a raw Python `datetime` into the query param, which serialized as `2026-06-09 13:01:02.412672` (space separator, microseconds, no timezone) — QBO rejects that as an invalid/unsupported `changedSince`. A new `utils.format_qbo_datetime` converts the cursor (a naive system-tz datetime) to the ISO-8601 UTC form QBO requires (`2026-06-09T20:01:02Z`), and the client uses it for CDC. The cursor is additionally **clamped into QBO's 30-day CDC window** (`_clamp_cdc_cursor`, `CDC_MAX_LOOKBACK_DAYS`) so a long pause degrades to a recent window instead of a hard error; anything older is reconciled by the next full import.
- **Transactions no longer get parked in manual review over fields ERPNext fills itself.** The create-time required-field check now skips `naming_series` (autoname), `read_only` computed fields (`grand_total`, `base_*_amount`, …) and `fetch_from` fields (e.g. account currencies). This was blocking **every** Estimate/Invoice/Bill/Payment/Journal Entry/Purchase Order with spurious "Missing required field" issues.

### Changed
- **Transaction mappers now populate the fields ERPNext can't infer**, so records insert instead of failing: Sales Invoice/Quotation get `currency`/`conversion_rate`/`selling_price_list`/`price_list_currency`/`plc_conversion_rate` and a `debit_to` (company default receivable); Purchase Invoice gets `credit_to` (default payable); Payment Entry gets `paid_from`/`paid_to`/`paid_amount`/`received_amount` and exchange rates (Receive for customers, Pay for vendors). Single-currency companies resolve everything from Company defaults.
- **Master imports now include inactive QBO records** (`where Active in (true, false)`). Historical transactions frequently reference **deactivated accounts/items/parties** (15 deactivated accounts appear in this company's journal); without them those references couldn't resolve and journal entries imported unbalanced.
- **Sub-customers/sub-vendors use their fully-qualified name** (`Parent:Job`) instead of the bare leaf, so QBO "jobs" stay unique across parents.
- Account-type mapping now recognises **Cost of Goods Sold**.

### Added
- **New QBO transaction types are imported** (previously skipped — together the bulk of a real company's journal): **Purchase** (Expense/Check/Credit Card charge), **Transfer**, **Bill Payment**, **Credit Card Payment**, **Vendor Credit** → balanced **Journal Entries**; **Sales Receipt** → Sales Invoice; **Deposit** is now a Journal Entry (was an unparty-able Payment Entry that silently skipped). Posting directions were verified against the real QBO Journal export.
- **Journal-balance guard:** any entity mapped to a Journal Entry whose debits/credits don't tie is routed to **manual review** with a clear reason ("some lines may reference accounts not yet imported") instead of failing on insert with ERPNext's opaque balance error.
- **`quickbooks_online/MIGRATION_NOTES.md`** — data-migration readiness guide (config prerequisites incl. Fiscal Years back to 2008, import order, volume/rate-limit guidance, Undeposited-Funds double-count caveat, and post-import Trial-Balance reconciliation).

## [1.41.0] - 2026-06-16

### Changed
- **New `Morning Briefing` module** (module-reorganization PR 7) — the morning-briefing surfaces move out of Enhancements Core into their own module + sidebar (`/app/morning-briefing`):
  - **Daily Briefing** doctype moved `enhancements_core` → `morning_briefing`. No code changes — it's referenced only by doctype name, and the generator/scheduler (`api.briefing`) and the `/wall` TV display (`www/wall`) are app-level and don't move.
  - **`Briefing Recipient` stays in Core** — it's a child table of `ERPNext Enhancements Settings` (the `briefing_recipients` field), so it stays with its parent (same call as `collab_doctype`).
  - Sidebar: Wall / TV Display (`/wall` URL shortcut) + Daily Briefing shortcuts; a Briefing card (Daily Briefing) and a Settings card linking ERPNext Enhancements Settings (where the recipient opt-in lives).
- Idempotent backstop patch `move_briefing_to_morning_briefing` (post-model-sync) reassigns the Daily Briefing `module` on existing installs (no data moves).

## [1.40.0] - 2026-06-16

### Changed
- **New `Google Drive` module** (module-reorganization PR 6 — the heaviest move) — the Google Drive integration is split out of CRM Enhancements into its own module + sidebar (`/app/google-drive`):
  - Moved `crm_enhancements` → `google_drive`: the code modules `drive_link_manager.py`, `drive_match.py`, `drive_sync.py`, `drive_utils.py`; the doctypes **Drive Link Candidate**, **Drive Sync Log**, **Drive Folder Template Item**, **Project Folder Google Drive Settings**; and the **Drive Link Manager** page.
  - ~19 files updated: all dotted import paths, the page + settings JS RPC method strings (`drive_link_manager.*`, `drive_sync.*`), 5 `hooks.py` doc_events/scheduler entries, and external importers (`api/call_recording_export.py`, `api/integrations_health.py`, `tests/test_drive_match.py`). `crm_enhancements.api` **stays in CRM** but its `drive_utils` import was repointed (CRM still triggers provisioning).
  - **Drive sidebar:** Drive Link Manager + Drive Settings shortcuts; Drive card (Drive Link Candidate, Drive Sync Log) + Settings card (Project Folder Google Drive Settings).
  - READMEs split: the Drive docs moved to `google_drive/README.md`; the CRM README trimmed to CRM + Sales Pipeline.
- Idempotent backstop patch `move_drive_to_google_drive` (post-model-sync) reassigns the records' `module` on existing installs — no data moves; the settings Single's service-account JSON / shared-drive id carry across.

## [1.39.0] - 2026-06-16

### Changed
- **New `Integrations` module** (module-reorganization PR 5) — the integration monitoring + analytics surfaces move out of Enhancements Core into a dedicated hub module + sidebar (`/app/integrations`):
  - **GA4 Settings** doctype and the **GA4 Dashboard** (`ga4-dashboard`) + **Integrations Health** (`integrations-health`) desk pages moved `enhancements_core` → `integrations` (adopting the previously-empty `integrations/` placeholder package as the module folder).
  - No code changes: the pages are pure-JS, and everything references GA4 Settings by name and the `api.analytics` / `api.integrations_health` endpoints (app-level, unchanged). Page/doctype routes are unchanged (route = name, not module).
  - **Integrations hub sidebar:** shortcuts to Integrations Health + GA4 Dashboard; an **Analytics** card (GA4 Dashboard, GA4 Settings) and a **Connected Services** card cross-linking the QuickBooks Online, MDM, Google Drive, and Triton settings Singles.
- Idempotent backstop patch `move_analytics_to_integrations` (post-model-sync) reassigns the records' `module` on existing installs (no data moves; GA4 credentials carry across).

## [1.38.1] - 2026-06-16

### Changed
- **Moved the `Job Interval` doctype `enhancements_core` → `workforce`** — follow-up to the Workforce module (v1.38.0). Job Interval is the Time Kiosk clock-in *session*, so it now sits with the other time-tracking doctypes instead of in Core. No code changes were needed (it's referenced only by doctype name and via the `job_interval` Link field on Time Kiosk Log); the Workforce sidebar already linked it. Idempotent backstop patch `move_job_interval_to_workforce` (post-model-sync) reassigns the `module` on existing installs — no data moves.

## [1.38.0] - 2026-06-16

### Changed
- **New `Workforce` module** (module-reorganization PR 4) — the time-tracking surfaces move out of the Enhancements Core grab-bag into their own module + sidebar (`/app/workforce`):
  - Doctypes **Time Kiosk Log** and **Time Kiosk Settings** and desk pages **Time Kiosk** (`time-kiosk`) and **Location Timeline** (`location-timeline`) moved `enhancements_core` → `workforce`.
  - The `/kiosk` PWA and the `api.time_kiosk` endpoints are app-level (in `www/` and `api/`) and do **not** move; only one import path needed updating (`api/time_kiosk.py`).
  - Workforce sidebar: shortcuts to Time Kiosk, Location Timeline, and the `/kiosk` PWA; a Time Tracking card linking **Job Interval** (the clock-in session doctype, still in Core), Time Kiosk Log, and Time Kiosk Settings.
- Idempotent backstop patch (`move_time_tracking_to_workforce`, post-model-sync) reassigns the four records' `module` on existing installs (no data moves — records are keyed by name).

## [1.37.1] - 2026-06-16

### Fixed
- **Removed a stray Page definition misfiled under a DocType folder.** `task_enhancements/doctype/hierarchical_task_view/` held a `"doctype": "Page"` JSON (a byte-for-byte duplicate of the real page) plus an `__init__.py`, sitting in a `doctype/` directory that `bench migrate` scans for DocTypes. Frappe imports by the JSON's `doctype` field, so it was redundantly upserting the same `hierarchical-task-view` Page that the canonical `task_enhancements/page/hierarchical_task_view/` (with `.js`/`.py`) already manages. Deleted the stray folder; nothing referenced it, and the real page is unaffected (no DB patch needed — the Page record is keyed by name and still synced from `page/`).

## [1.37.0] - 2026-06-15

### Changed
- **QuickBooks module split** (module-reorganization effort). The module historically (mis)named **"QuickBooks Time Integration"** was really the QuickBooks **Online** (QBO) accounting integration. It is now two correctly-named modules:
  - **QuickBooks Online** — renamed from `quickbooks_time_integration` → `quickbooks_online`; its QBO engine subpackage moved from the doubled `quickbooks_online/quickbooks_online/` to `quickbooks_online/core/`. All four QBO doctypes (QuickBooks Online Settings, Sync Log, Sync Mapping, Raw Payload) and the dashboard Page are reassigned to the `QuickBooks Online` module. Adds a **QuickBooks Online** workspace (`/app/quickbooks-online`).
  - **QuickBooks Time** — new module holding the standalone `qb_timesheet_webhook` (moved out of the shared `api.py`). Adds a thin **QuickBooks Time** workspace.
- A pre-model-sync patch (`rename_quickbooks_module`) renames the `Module Def` so existing installs carry the QBO doctypes/page (and the stored OAuth tokens on the Settings Single) across cleanly; idempotent and a no-op on fresh installs.

### Deploy notes (webhook URLs changed)
- The QBO Intuit webhook + OAuth-callback now resolve at `...quickbooks_online.api.*` (was `...quickbooks_time_integration.api.*`).
- The QuickBooks **Time** webhook is now `/api/method/erpnext_enhancements.quickbooks_time.api.qb_timesheet_webhook`. **Update the endpoint configured in QuickBooks Time / Intuit after deploying.**

## [1.36.0] - 2026-06-15

### Added
- **Workspaces (sidebars) for Inventory, Task, and CRM Enhancements** — batch 2 of the module-reorganization effort (no doctype moves):
  - **Inventory Enhancements** (`/app/inventory-enhancements`): shortcuts to Inventory Scanner Audit + Inventory Count Session; cards Counts (Inventory Count Session) and Masters (Storage Location, Inventory Scanner Settings).
  - **Task Enhancements** (`/app/task-enhancements`): shortcuts to the Hierarchical Task View page + Task list; card Tasks. (`task_enhancements/doctype/task` is a script customization of the standard ERPNext Task — there is no custom Task doctype — so the sidebar links the standard Task.)
  - **CRM Enhancements** (`/app/crm-enhancements`): shortcuts to Sales Pipeline + Lead + Opportunity; cards Pipeline (Lead, Opportunity, Customer) and Enhancements (Value Streams, Sales Activity Settings). The module's other doctypes (Accounts Lead/Opportunity/Project, Lead Source, Opportunity Contributor, Value Stream) are **child tables** and can't be sidebar links; Drive Link Manager + `drive_*` are intentionally left off here as they move to a dedicated Google Drive module in a later PR.

## [1.35.0] - 2026-06-15

### Added
- **Devices workspace (sidebar)** — Device Management now has its own desk workspace at `/app/devices` (`device_management/workspace/devices/`), the first sidebar of the module-reorganization effort. Shortcuts to the **Device Console** and **Device Fleet Dashboard** pages plus the **Managed Device** list; cards group **Devices** (Managed Device, Device Assignment Log), **Compliance** (Device Compliance Settings) and **MDM** (MDM Settings, MDM Sync Log, Device Action Log, MDM Raw Payload) — so the native registry and the MDM provider layer share one navigation home. Roadmap for the full reorg is in `erpnext_enhancements/MODULE_PLAN.md`.

## [1.34.6] - 2026-06-15

### Fixed
- **Device Fleet Dashboard failed to load** (`/app/device-fleet-dashboard`) with `SQL functions are not allowed as strings in SELECT: count(name) as n. Use dict syntax like {'COUNT': '*'} instead.` (`api/device_dashboard.py`, `_by()`). The per-field grouped-count helper passed a raw `"count(name) as n"` aggregate **string** in the `frappe.get_all` `fields` list, which Frappe's query builder rejects (the same SELECT/ORDER-BY restriction behind the v1.34.3 Drive Link Manager fix). Rewrote the query with the pypika builder — `frappe.qb.from_(md).select(col, Count(md.name).as_("n")).groupby(col)` — the idiom already used in `task_dashboard.py` and `travel_management/integrations.py`; the surrounding `_by()` contract (result keys, `values` ordering) is unchanged, so every tile that calls it (status, compliance, platforms, ownership) renders again.

## [1.34.5] - 2026-06-15

### Fixed
- **Drive Link Manager scan overwhelmed the server** (intermittent `502 Bad Gateway` on socket.io and `scan_status` while a scan ran) on a real-size dataset — ~1,350 customers, ~600 projects, ~770 opportunities against the whole Shared Drive. The matcher compared every record against every folder (O(records × folders) — millions of `SequenceMatcher` calls), re-resolved each record's customer folder, and committed thousands of inserts in one transaction, pegging CPU/DB so other requests couldn't get a worker. Now: an **inverted token index** (`drive_match.token_index` / `blocked_candidates`) scores each record only against folders that share a word with it (rarest token first, capped); each **customer's folder is resolved once** and cached; and inserts **commit in batches of 200**. Pure indexing/blocking logic is unit-tested bench-free (`tests/test_drive_match.py`).

## [1.34.4] - 2026-06-15

### Fixed
- **Drive Link Manager scan timed out** (`net::ERR_CONNECTION_CLOSED`) on real data (`crm_enhancements/drive_link_manager.py`). The scan listed the entire Shared Drive and fuzzy-matched every unlinked Customer / Project / Opportunity (hundreds of records — ~780 opportunities alone) **inline in one HTTP request**, exceeding the gateway timeout. It now runs on the **long background queue** (`_run_scan_job`): `scan_drive_links` enqueues and returns immediately, and the dashboard polls a new `scan_status` endpoint (`queued → running → done/error`, tracked in the Frappe cache), reloading when it completes. Re-running stays safe (clears prior un-applied rows; `Linked` kept).

## [1.34.3] - 2026-06-15

### Fixed
- **Drive Link Manager dashboard failed to load** with `417 Expectation Failed` — `ValidationError: Invalid field format in Order By` (`crm_enhancements/drive_link_manager.py`, `get_candidates`). The candidate query ordered by a raw SQL `FIELD(reference_doctype, 'Customer', 'Project', 'Opportunity')` expression, which Frappe's query builder rejects (it validates `order_by` against real field names — SQL functions aren't allowed). Now orders by `score desc` and applies the Customer → Project → Opportunity group ordering in Python (the dashboard groups in that order regardless), and returns the summary tallies as plain dicts. Verified against the live Frappe build.

## [1.34.2] - 2026-06-15

### Fixed
- **Opportunity "Description" field is no longer mandatory** (`fixtures/custom_field.json`, `Opportunity-custom_description`). The field carried `reqd: 1` while living under the hidden `Opportunity Description` section break — a hidden-yet-mandatory combination that makes Frappe block saving an Opportunity on a field nobody can see. Set `reqd: 0` and made the field explicitly `hidden: 1` so it stays hidden and can never block a save. Applies on `bench migrate` (re-syncs the Custom Field from the fixture).

## [1.34.1] - 2026-06-15

### Changed
- **Opportunity Drive folders now use an `ID - Name` schema** — e.g. `CRM-OPP-2026-00112 - Pool Reno` — replacing the previous em-dash form (`… — …`) in `drive_utils.provision_opportunity_folder`. Applies to newly provisioned Opportunity folders, including the Drive Link Manager's "Create New" action (which calls the same provisioner). Existing folders are not renamed.

## [1.34.0] - 2026-06-15

### Added
- **Drive Link Manager — a System-Manager dashboard for bulk-linking existing Google Drive folders to ERPNext records** before the two-way sync runs (new Desk page `/app/drive-link-manager`, route gated to System Manager). Turns the one-time onboarding chore of matching hundreds of Customer / Project / Opportunity records to their already-existing Drive folders into a reviewed, fault-tolerant flow.
  - **Fuzzy auto-matching, human-reviewed before anything links.** A *Scan* lists the whole Shared Drive's folders once (one flat, paginated listing) and ranks candidate folders for every *unlinked* record. Matching is **hierarchy-aware** — customers match Shared-Drive-root folders; projects/opportunities are scored against the children of their customer's folder first, widening to the whole drive only when that yields no confident match — and reuses the same `<id> <name>` naming conventions the provisioner mints. Scoring (`crm_enhancements/drive_match.py`) blends difflib ratio + token-set overlap + a containment bonus, strips record-id prefixes (so `PRJ-00694 Smith Residence` matches a plain `Smith Residence` folder), and buckets into **High / Medium / Low / None** tiers.
  - **Staging layer (`Drive Link Candidate` doctype), not direct writes.** Scan results are staged as candidate rows — suggested folder + ranked alternatives + tier + the reviewer's decision + processing status. **High-confidence matches arrive pre-approved**; everything else waits as Pending. The dashboard lets you accept a suggestion, pick a ranked alternative, **search Drive live** for a manual override, mark a record **Create New** (provision a fresh folder), or reject — with confidence bars, per-type grouping, filters, bulk "approve all suggested" / "reject remaining", and **conflict flagging** when one folder is chosen for two records.
  - **Robust Apply — one row at a time.** Applying writes `custom_drive_folder_id` (or provisions a new folder via the existing retry-hardened `drive_utils` provisioning) **each in its own try/except**, so a single failure is recorded to Drive Sync Log and the rest still link. Re-scanning is safe (clears prior un-applied rows, keeps `Linked` ones for audit). After linking, the existing upload hook + recursive shadow sync take over automatically.
  - Backend is whitelisted + `System Manager`-only throughout (`crm_enhancements/drive_link_manager.py`); the pure matcher is unit-tested bench-free (`tests/test_drive_match.py`, wired into the CI `unit-tests` job).

## [1.33.0] - 2026-06-15

### Fixed
- **Google Drive → ERPNext shadow sync now brings in added files *and* folders, and no longer crashes on a stale folder link** (`crm_enhancements/drive_sync.py`). Three problems were stopping inbound sync entirely:
  - **Hourly job crashed on a deleted/moved Drive folder.** When a linked document's `custom_drive_folder_id` pointed at a Drive folder that no longer exists (or is no longer shared with the service account), `_drive_id_of` raised `HttpError 404` and aborted that document's sync every hour (observed continuously on `PRJ-00694`). The job now catches the 404, records it **once** as a `Stale` Drive Sync Log row, and moves on — one bad link can't break the run.
  - **Subfolders were never scanned.** The folder listing was non-recursive (`'<folder>' in parents`), so any file dropped inside a provisioned subfolder (Build, Design, Project Manager, …) was invisible. The sync now **walks the full folder tree** (`_walk_drive_folder`), depth-capped at 10 with a cycle guard for Drive shortcuts.
  - **Folders were excluded outright.** The query filtered out `mimeType = folder`. Subfolders are now mirrored as **link-only `File` shadows** too (`file_url` = the folder's Drive link), so the folder structure is visible on the Project/Customer/Opportunity. Nested item names are path-prefixed (e.g. `Design/Renderings/front.png`) and folders carry a trailing slash, keeping the flat attachment list legible.
- Sync remains **link-only and de-duplicated** — no bytes are copied (Drive stays the source of truth) and shadows are keyed on `custom_drive_file_id`, so re-runs never create duplicate links. Deletions still never propagate; a vanished file/folder is flagged `Stale`, never removed.

## [1.32.2] - 2026-06-15

### Fixed
- **"Unlink and Delete" crashed on multi-word doctypes** (`delete_utils.py`). The flow reads the target doctype out of a desk URL, so it arrives as the route *slug* — lowercased with spaces turned into hyphens (e.g. `sapphire-maintenance-contract` for `Sapphire Maintenance Contract`). The server only corrected *casing* (via the case-insensitive `name` collation, which is why single-word `task`→`Task` worked) but never undid the space→hyphen swap, so `frappe.get_doc()` failed to import the controller and raised `ModuleNotFoundError: No module named 'frappe.core.doctype.sapphire_maintenance_contract'` / `ImportError … the DocType you're trying to open might be deleted`. A new `_resolve_doctype()` helper now matches the real DocType name in two steps (exact case-insensitive match, then hyphens→spaces), used by both `get_blocking_links` and `unlink_and_delete` — and in `unlink_and_delete` it runs *before* the permission check (which previously also ran against the unresolved slug). Single-word and already-correct doctype names are unaffected.

## [1.32.1] - 2026-06-13

### Changed
- **CI now guards DocType module placement** (`.github/workflows/ci.yml` + `tests/test_doctype_modules.py`). A new bench-free unit test asserts every custom DocType's `module` field matches both its on-disk directory (Frappe's `scrub(module)` mapping) *and* an entry in `modules.txt` — so a DocType created in the wrong module folder, or with a stale/typo'd `module`, fails CI before merge instead of silently migrating into the wrong module. Green across all current app DocTypes.

## [1.32.0] - 2026-06-13

### Added
- **Device Management Phase 2 — provider integration** (new **MDM Integration** module, `mdm_integration/`). Makes the Phase-1 registry *live* by syncing real device state and enabling governed remote actions, via a **two-provider split routed by device class**:
  - **Miradore = mobile MDM** (Phone/Tablet) — Miradore API v2, `X-API-Key` + `X-Instance-Name` auth (no OAuth). Inventory + compliance pull, and remote **lock / wipe / locate**.
  - **Action1 = computer RMM** (Laptop/Desktop) — Action1 REST `api/3.0`, OAuth2 client-credentials (token refresh mirrors the QuickBooks client). Inventory + patch/compliance pull, and remote **reboot / run-script / deploy-patch** (it is RMM, so no device-wipe).
  - **Provider-agnostic adapter** (`client.py`): a `MDMProvider` base → normalized `ProviderDevice`, with `MiradoreProvider`, `Action1Provider`, and a **`MockProvider`** (canned devices + recorded actions). `MDM Settings.provider_mode` defaults to **Mock**, so the entire sync → reconcile → action → audit pipeline runs and is testable **before any credentials are entered**; flip to Live to hit the real APIs. A device routes to its provider by class (`routing.provider_key_for_device`), and a `supports()` capability map means a "wipe" can never be dispatched to an Action1 computer.
  - **Sync + reconciliation** (`sync.py`, `mapping.py`): scheduled per-provider pull (hourly, throttled by `sync_poll_minutes`) into **MDM Sync Log** + **MDM Raw Payload**; each provider device is matched to a Managed Device by provider-id → serial → IMEI and has its compliance posture overwritten (`compliance_source = "Provider"`). A provider device with no match becomes a **Discovered** Managed Device for a human to confirm; a registry device the feed stops returning is flagged **Unmanaged** (never deleted). Four provider-managed fields were added to **Managed Device** (`mdm_provider`, `mdm_provider_device_id`, `mdm_link_state`, `mdm_last_seen`).
  - **Remote actions through one guarded executor** (`actions.py`): the manager-UI buttons on the Managed Device form *and* six new gated AI assistant tools (`remote_lock_device`, `remote_wipe_device`, `locate_device`, `reboot_device`, `run_device_script`, `deploy_device_patch`) converge on `execute_device_action`, which routes to the provider, enforces capabilities, applies the **BYOD wipe guard** (a personally-owned device is always coerced to a selective wipe; a full wipe is refused — `routing.resolve_wipe_mode`), and writes an immutable **Device Action Log** row on every attempt.
  - **AI governance:** the six device tools are wired into the existing write-confirmation gate (`assistant_tools/_gate.py`) — added to `APP_MUTATING`, with **wipe / lock / run-script classified HIGH risk** (a remote wipe is irreversible and arbitrary remote code is as dangerous as `run_python_code`), and `summarize_tool_call` templates so the desk confirmation card reads e.g. "Remote WIPE (selective) device DEV-2026-00012". With AI write gating on, nothing executes until a human clicks Confirm & Execute (the gate re-runs the tool as that user, so the manager-role + `Managed Device` permission check binds to them).
  - **Secured inbound webhooks** (`webhooks.py`) — a guest endpoint per provider, Bearer-secret verified constant-time before parsing, archives the payload and enqueues a resync (polling remains the primary path).
  - **Two Integrations Health tiles** (Miradore, Action1) added to `api/integrations_health.py` — mode, connection, last-sync age, device count, failed syncs (7d), non-compliant count — surfaced with no page-JS change. Secrets are read only as booleans.
  - Routing, the capability map, and the BYOD wipe guard live in the frappe-free `mdm_integration/routing.py` and are unit-tested bench-free in `tests/test_mdm_integration.py` (wired into the CI `unit-tests` job). The Live Miradore/Action1 adapters are scaffolded against the providers' documented REST shapes; exact JSON field names are confirmed against each vendor's Swagger when real credentials are added (parsing is defensive).

## [1.31.0] - 2026-06-13

### Added
- **Device Management (MDM/EMM) — native device registry** (new **Device Management** module). Phase 1 of a phased plan to manage Sapphire Fountains' mixed fleet (company Android/iOS phones & tablets, laptops/desktops, and BYOD). ERPNext becomes the system of record for who holds which device, its lifecycle, warranty, and security posture — standalone, with no external MDM provider required. (Phase 2 will layer a provider integration — Intune/Hexnode — for live compliance and remote lock/wipe; the compliance fields below are designed so a provider feed simply overwrites them.)
  - **Managed Device** doctype — a device's identity (asset tag, type, platform, make/model), hardware identifiers (serial, IMEI, MAC, SIM number — held at **`permlevel: 1`** so they are visible to Device Managers only, which matters for BYOD privacy), procurement/warranty, and a guarded lifecycle `status` (In Stock → Assigned → In Repair → Lost/Stolen → Retired; illegal jumps are rejected). Non-submittable with a stable barcode, so the device keeps its identity across re-assignment and re-tagging.
  - **Custody history** — every check-out / check-in / transfer appends to a **Device Assignment Log** child table (the open row, with no Returned On, is the current holder), so "who had this device, and when" is always answerable. The current assignee exists exactly when the device is Assigned; every other state clears it.
  - **Self-attested compliance** — `screen_lock_enabled` / `encryption_enabled` / `os_version` with a derived `compliance_status` (Compliant only when the device both locks and encrypts) and a `compliance_source` flag (Manual now; Provider in Phase 2). An employee confirms their own device's posture via an **Attest** action — identity-gated to the device's assignee.
  - **Device Console** (`/app/device-console`, mobile-first) — scan a barcode / IMEI / asset tag (keyboard-wedge scanner or the device camera via the `BarcodeDetector` API, reusing the Inventory Scanner pattern) to pull up a device and check it out (with an employee picker) / in / transfer / send to repair / flag lost; an unknown scan offers to enroll a new device pre-filled with the scanned code.
  - **Device Fleet Dashboard** (`/app/device-fleet-dashboard`) — a green/amber/red snapshot (status mix, compliance split, stale attestations, warranty expiries, platform/ownership breakdown, plus a "held by inactive staff" reclaim tile), modelled on the Integrations Health page and reusing its tone helpers. DB-only; Device-Manager / System-Manager gated, with deep links into filtered device lists.
  - **BYOD privacy** is enforced two ways: the sensitive hardware identifiers sit at `permlevel: 1`, and a `permission_query_conditions` / `has_permission` hook (`device_management/permissions.py`) scopes non-managers to *only the device assigned to them* — an employee can see and attest their own phone but cannot browse the fleet or read others' serials.
  - New **Device Manager** role (seeded by `patches.create_device_manager_role`) gates the doctypes and pages alongside `System Manager`; `HR Manager` gets read access and an **Assigned Devices** panel on the Employee form.
  - **Scheduled nudges** (`device_management/tasks.py`, daily) — warn Device Managers as each device enters its warranty-expiry lead window, and remind a holder to re-attest when the last check is older than the attestation interval. Both follow the app's stamp-first / at-most-once pattern and read their cadence from the new **Device Compliance Settings** single.
  - API in `api/device_management.py` (scan resolution + lifecycle + attestation) and `api/device_dashboard.py` (fleet health); the lifecycle/compliance *rules* live in the frappe-free `device_management/compliance.py` and are unit-tested bench-free in `tests/test_device_management.py` (wired into the CI `unit-tests` job).

## [1.30.1] - 2026-06-13

### Fixed
- **Triton/Twilio webhook endpoints crashed with `TypeError: ... got an unexpected keyword argument 'cmd'`** (`api/telephony.py`). The `@validate_webhook_secret` and `@validate_twilio_request` decorators defined their `wrapper(*args, **kwargs)` *without* `functools.wraps(func)`, which breaks the `__wrapped__` chain. Frappe dispatches `/api/method/...` calls via `frappe.call(method, **frappe.form_dict)` — and `form_dict` carries the `cmd` key Frappe injects (`handle_rpc_call` sets `frappe.form_dict.cmd = method`). `frappe.call` normally drops form keys the target doesn't declare by inspecting its signature, but with the chain broken it inspected the bare wrapper, saw `**kwargs`, and forwarded the entire `form_dict` — `cmd` included — into the real handler. Endpoints with no `**kwargs` of their own to absorb it raised `TypeError`. Adding `@functools.wraps(func)` to both decorators lets `frappe.call` see each endpoint's true signature and filter `cmd` as it always should. Fixes six affected endpoints: `get_telephony_routing`, `append_call_transcript`, `get_call_transcript`, `get_caller_info`, `update_caller_info` (Triton gateway), and `receive_mms` (Twilio MMS webhook). Handlers that already declared `**kwargs` (`notify_incoming_call`, `process_unified_recording`, `process_unified_sms`, `process_call_intelligence`) were never affected and behave identically after the fix.

## [1.30.0] - 2026-06-13

### Added
- **Configurable desk shortcut icons on Home** — a new **Enhancement Desk Shortcut** config doctype (System Manager only) lets an admin curate the icon tiles shown on the desk **Home** workspace and control, **per icon**, who sees it: everyone (`visible_to_all`), specific **roles**, and/or specific **users**. The tiles render from a per-user boot payload (`frappe.boot.ee_desk_shortcuts`, built in `api/desk_shortcuts.py` and shipped via `boot.py`) inside a new **"Desk Shortcuts"** Custom HTML Block placed on Home — so each user sees only the tools relevant to them, and config edits apply on the user's next desk load with no deploy.
  - Seeded with seven defaults (`patches.seed_desk_shortcuts`, insert-only so admin edits persist): **Time Kiosk** and **Project Dashboard** (everyone); **Inventory Scanner** (Stock Manager / Inventory Clerk / System Manager); **Maintenance Wizard** and **Maintenance Day Board** (Maintenance User/Supervisor / Projects Manager); **Sales Pipeline** (Sales + Projects roles); **Integrations Health** (System Manager). System Manager / Administrator always see every enabled shortcut.
  - The gating is **cosmetic, not a security boundary**: every target page enforces its own role permissions, so an unauthorized click still gets "not permitted" — this only keeps each user's desk tidy and relevant. Adding more tools later (e.g. Wall Display, Travel Itinerary, Daily Briefing) is just a new config row, no code change.
  - Block sources live in repo-root `Custom HTML Block/desk_shortcuts.{html,js,css}` (seeded by `patches.seed_desk_shortcuts_block`, insert-only) and are placed on Home by `patches.place_desk_shortcuts_on_home` (idempotent). Icons are emoji glyphs (robust inside the block's shadow-DOM sandbox, where Frappe's SVG-sprite icons can't resolve) and are editable per shortcut; tiles are themed with Frappe CSS variables for Light + Timeless Night.

## [1.29.0] - 2026-06-13

### Added
- **Inventory Scanner Audit** — a mobile-friendly desk page (`/app/inventory-scanner-audit`) for warehouse clerks to run physical stock counts by scanning shelf/bin and item barcodes (USB/Bluetooth keyboard-wedge scanner, or the device camera via the `BarcodeDetector` API) and typing the counted quantity. Ships as a new **Inventory Enhancements** module.
  - **Storage Location** doctype — scannable shelf/bin sub-locations beneath a Warehouse, each with its own barcode. A location scan resolves the bin to its stock-bearing warehouse (stock is tracked at warehouse granularity).
  - **Inventory Count Session** (+ Inventory Count Line) — a resumable, per-clerk audit record. Each counted line snapshots the system on-hand quantity (`erpnext.stock.utils.get_stock_balance`) and its variance at scan time; one open session per clerk.
  - **Finalize → draft Stock Reconciliation** — counts aggregate per (item, warehouse) into a *draft* Stock Reconciliation (never auto-submitted) for a Stock Manager to review and submit, so valuation/ledger changes always get a second set of eyes. Counts across several bins of one warehouse sum into a single reconciliation row.
  - **Inventory Scanner Settings** (single) — fallback default warehouse, require-variance-reason, block-negative-counts, allow-unknown-item (manual item search), and enable-camera-scan.
  - New **Inventory Clerk** role (seeded by `patches.create_inventory_clerk_role`) gates the page alongside `Stock Manager` / `System Manager`.
  - API in `api/inventory_scanner.py` (whitelisted scan resolution + session lifecycle + finalize); tests in `tests/test_inventory_scanner.py`. Serialized/batch items are flagged but reconcile at plain warehouse qty in this version.
- **Integrations Health dashboard** (`/app/integrations-health`, System Manager only — `api/integrations_health.py` + the Enhancements Core page). This app depends on a lot of third parties (QuickBooks Online, Google Drive, Twilio/"Triton", Vertex AI/Gemini, GA4/Search Console) and each fails *quietly* in its own corner — a QuickBooks OAuth token lapses, the Drive service account was never pasted in, an hourly sync errors into the Error Log. The new page rolls all of it into one green/amber/red tile per integration plus two panels:
  - **Per-integration tiles** — QuickBooks (connection status, OAuth-token countdown, last CDC poll age, failed syncs in 7 days), Google Drive (service account configured?, attachment-sync on/off, Drive Sync Log failures in 24 h + Stale shadows), Telephony (gateway URL / Twilio creds / caller-ID number / softphone-answerer count), AI Drafting (Gemini key present?), Analytics (GA4 property + credentials + GSC). Each tile's overall colour is the worst of its metrics, with deep links to the relevant Settings + log.
  - **Background jobs** panel — Frappe scheduler enabled? (red if disabled — the #1 silent failure), this app's registered job count, failed Scheduled Job Logs in 24 h, and the most recent failures.
  - **Errors (24 h)** panel — Error Log volume + the top categories by count.
  - Cheap and **DB-only on load** (no outbound API calls); the Drive tile's **Test connection** button runs the one live check on demand (proxied to the existing `drive_sync.test_connection`). Secrets (`service_account_json`, tokens, Twilio/Gemini keys, webhook secrets) are read **only as `configured: true/false`** and never returned. Per-section try/except so one missing Single doctype can't blank the page; themed with Frappe CSS vars for Light + Timeless Night.
- **First gated AI *write* tool — `create_followup_task`** (`assistant_tools/create_followup_task.py`). The MCP assistant tools were read-only by design ("out of scope until a dedicated write-tools batch"); this opens that batch. The tool proposes a ToDo follow-up — optionally linked to a record (Opportunity / Project / Customer / Contact / Sapphire Maintenance Record) and assigned to a user with a due date and priority — so the assistant can *act on* a next step the Morning Briefing / Call Intelligence / maintenance read tools surface. It is wired into the existing **AI write-confirmation gate** (`_gate.py`): added to the new `APP_MUTATING` set (so it gates deterministically, not via the fail-closed fallback), classified **Low** risk (a create), and given a `summarize_tool_call` template so the desk confirmation card reads "Create follow-up task “…” on Project PROJ-0042". With AI write gating **on**, the tool returns the `awaiting_user_confirmation` envelope and an **AI Pending Action** instead of writing; the ToDo is created only after a human clicks *Confirm & Execute* (the gate re-runs `execute` as the confirming user, so its permission checks — `has_permission("ToDo", "create")` + `require_doc_read` on any linked record — bind to the human, not the AI). With gating off it creates the ToDo immediately (still FAC-audited). The `ee-ai-write-confirmation` skill now names it.

### Changed
- **CI now runs the bench-free unit suites** (`.github/workflows/ci.yml`). The `unit-tests` job previously ran only the Time Kiosk consolidation test; it now also runs the AI write-gate classifier (`test_ai_gate_unit`), the FAC assistant-tool contract (`test_assistant_tools_schema`), and the Integrations Health tone helpers (`test_integrations_health`) as plain `unittest` (they import their tool modules under the existing `sys.modules` stubs — no live frappe/FAC). This actually gates the security-sensitive write gate on every push, where before its tests only ran locally.

## [1.28.1] - 2026-06-12

### Fixed
- **Morning Task Dashboard top-10 sorting** (`api/task_dashboard.py`). `custom_company_priority` is a Select field, so it is stored as text; the top-projects query sorted it lexically (`1, 10, 11, 12, …, 2`), which not only mis-ordered the list but — because the `LIMIT 10` ran after the bad sort — surfaced the *wrong* ten projects, burying ranks 2–9 under every `1x` rank. Now sorted numerically (matching `priority_overview.js`'s `get_priority_weight`) before slicing, with `modified desc` preserved as the within-rank tiebreaker. The wall display inherits the fix via `get_wall_dashboard_data`.

## [1.28.0] - 2026-06-12

### Added
- **Google Drive sync subsystem** (`crm_enhancements/drive_sync.py` + Drive Sync Log doctype). Building on the linked folders:
  - **Two-way attachment sync** (opt-in: settings → Enable Attachment Sync). ERPNext → Drive: every new attachment on a Drive-linked Project/Customer/Opportunity is uploaded to its folder in the background (`File.custom_drive_file_id` stamps the mirror and prevents echo loops). Drive → ERPNext: an hourly job creates **link-only shadow attachments** (File rows opening the Drive file — no bytes copied) for Drive files ERPNext doesn't know. **Deletions never propagate**; a shadow whose Drive file vanished is flagged Stale in the log.
  - **Drive Sync Log** — desk-visible audit of every provision/upload/shadow/export/backfill with status and error; Failed rows carry a retry payload that a nightly job re-enqueues (max 3 attempts).
  - **Test Connection button** on the Drive settings page — validates the service-account JSON, Drive API reachability, and access to each configured Drive/folder, surfacing the service-account email to add as a Shared Drive member.
  - **Link Existing Folders button** — backfill that connects pre-existing Customers and Projects to their already-existing Drive folders by name (never creates anything).
  - **Open Drive Folder buttons** on Project, Customer and Opportunity forms (the folder-ID fields stay hidden).
  - **Opportunity folders** (opt-in toggle): each new Customer-party Opportunity gets `<Customer>/<Opportunity — Title>`, stored on the new `Opportunity.custom_drive_folder_id`.
  - **Configurable project folder template** — the subfolder tree is now a settings child table (paths nest with `/`, rows optionally scoped to a Project Type); empty table keeps the legacy defaults.
  - **Transcript companions** — every exported call recording/voicemail now gets a sibling `.txt` with the summary + transcript in the monthly folder.

## [1.27.0] - 2026-06-12

### Fixed
- **Desk SMS crashed with "Cannot select a Group type Customer Group"** — the unknown-caller auto-create hard-coded `customer_group: "All Customer Groups"` and `territory: "All Territories"`, both GROUP tree nodes that erpnext v16 rejects, breaking every auto-create path (desk SMS, inbound-call caller creation). Auto-created callers now use the Selling Settings defaults when they're leaf nodes, else the first leaf (e.g. "Individual"). Desk SMS additionally stops auto-creating Customers entirely (`create_if_missing=False`) — texting an arbitrary number links existing CRM records but never mints an "Unknown Caller" Customer.

### Changed
- **Google Drive settings consolidated on "Project Folder Google Drive Settings"** — now the single home for all Drive automation: the service account + Shared Drive ID (projects), a new **Create Customer Folders on Customer Creation** toggle (auto-provisions the customer's top-level Shared Drive folder on insert — the same folder project trees nest under — storing its ID on the new hidden `Customer.custom_drive_folder_id` field), and a new **Call Recordings Folder ID** field that the recordings/voicemail export (1.26.0) reads first (the Triton Settings field remains as fallback).

## [1.26.0] - 2026-06-12

### Added
- **Call recordings + voicemails mirrored to Google Drive** (`api/call_recording_export.py`). Every recording ingested by `process_unified_recording` and every Twilio voicemail ingested by `process_call_intelligence` is uploaded in a background job to the folder configured in the new `Triton Settings.call_recordings_drive_folder` (empty = feature off), organised into monthly `YYYY_MM` subfolders. Filenames: `2026-06-12 1530 — Inbound — Caller Name (+1801…) — <CallSid>.wav`, voicemails prefixed `Voicemail — `. Auth reuses the project-folders service account (Project Folder Google Drive Settings) with Shared Drive support; uploads are idempotent per Call SID (webhook retries dedupe against Drive); answered-call audio is read from the already-saved private File, voicemail audio is fetched from Twilio with the stored credentials. Export failures only log to Error Log ("Call Recording Export") — webhooks are never affected.

## [1.25.0] - 2026-06-12

### Changed
- **"Call via Triton" resolves the rep's number from Employee OR User profile.** The button rang only `Employee.cell_number` and threw for anyone without it. Now: Employee Cell Number (source of truth) → `User.phone` → `User.mobile_no`, with a clearer error when none is set. A new Employee `on_update` hook keeps the linked User's `phone` synced from the Employee Cell Number (erpnext core syncs name/DOB/image but not phone), and a one-time patch backfills existing Employees so the sync holds immediately after deploy.

## [1.24.2] - 2026-06-12

### Fixed
- **Sending an SMS from the desk crashed** ("Missing or Invalid Authorization Header" masked by an `UnboundLocalError`). Two stacked bugs: (1) `send_sms` called the whitelisted `get_caller_info`, whose `@validate_webhook_secret` guard reads the request's Authorization header — desk sessions authenticate by cookie and have none, so the internal call threw; the same flaw silently broke **inbound MMS** (`receive_mms` is Twilio-signature-guarded, also no Authorization header). The caller-lookup logic now lives in an auth-free internal `_get_caller_info` used by every server-side call site; the guarded wrapper remains the HTTP boundary for the Triton gateway. (2) A function-local `import requests` inside `send_sms` made `requests` a local name for the whole function, so the `except requests.exceptions...` clause raised `UnboundLocalError` when anything threw before the import line — both stray local imports removed (the module-level import is in scope).

## [1.24.1] - 2026-06-12

### Fixed
- **Desk softphone stopped ringing in tabs older than an hour** — the Twilio access token expires after 1h and the device silently unregisters, so a desk tab opened in the morning showed "Registered" in the console but never received the incoming-call leg in the afternoon (the call panel appeared without Answer/Decline). The client now refreshes the token on the SDK's `tokenWillExpire` event (`device.updateToken`); if the user has been removed from `softphone_users` meanwhile, the device is released instead.

## [1.24.0] - 2026-06-12

### Fixed
- **Supplier (and Customer) primary address + Google Maps box in the unified Contacts & Addresses tab.** The directory widget read `frm.doc.primary_address` as if it were the Address docname — but on stock Customer/Supplier that field is the read-only TEXT display (HTML), so the map box always showed "Invalid address reference format" and Set Primary wrote a docname into the display field. The widget now resolves the real Link field per doctype (`customer_primary_address` / `supplier_primary_address` / the custom `primary_address` Link on Project/Master Project) for the map, the Primary badge, the Set Primary action, and the link-field query.

### Changed
- **Supplier's read-only "Primary Address" text now shows the Address's `custom_full_address`** (new Supplier `validate` hook) instead of frappe's multi-line address-template rendering, falling back to the stock text when the custom field is empty. The Google Maps embed feeds from the same `custom_full_address` (existing behavior, now actually reachable on Supplier since the docname resolves).

## [1.23.2] - 2026-06-12

### Fixed
- **/wall TV display crashed with "SQL functions are not allowed as strings in SELECT"** — frappe 16 rejects aggregate functions passed as `get_all` field strings. Rewrote the wall's per-project task-stats query with the query builder, plus the three other sites with the same latent pattern (Travel Trip claim/advance rollups in `travel_trip.py` and `integrations.py`, and the Trip Cost Summary traveler counts). Both query shapes verified against the live frappe build.
- **Console spam "Failed to execute 'clone' on 'Response'" on every desk page** — the kiosk service worker (whose scope covers the whole site) cloned asset responses inside an async `caches.open()` callback, by which time the page had usually consumed the body. The clone now happens synchronously before the response is handed back.
- **Squished avatars in the Comments App** — profile photos that aren't square stretched to fill the round frame (which is why only some users looked squished). Avatar images now use `object-fit: cover` and the frames no longer flex-shrink.

## [1.23.1] - 2026-06-12

### Fixed
- **"New Note" crashed with `'EmployeeProject' object has no attribute 'add_note'` on Project** (and would equally crash on Customer, Supplier, Master Project, Contact). `unified_tab_controller.js` mounted ERPNext's stock CRMNotes widget on `custom_comments_field` for every wired doctype, but the widget's buttons call the `add_note`/`edit_note`/`delete_note` document methods that only the CRM doctypes (Lead/Opportunity/Prospect) implement — and the mount also fought the threaded Comments App for the same field, which is why the Notes tab sometimes showed the wrong UI. CRMNotes is now mounted only on the CRM doctypes (Opportunity keeps its 852 existing CRM Notes); everything else renders the Comments App.

## [1.23.0] - 2026-06-12

### Fixed
- **Per-user desk softphone identities** (pairs with Triton >= 0.8.0; fixes Answer/Decline never appearing on the desk call panel). All desk sessions previously registered ONE shared Twilio identity, so with several `softphone_users` configured only the most recently opened desk could ever ring — and whether the desk rang at all depended on a Triton env var matching the hard-coded identity. Now each configured answerer registers their own `erpnext_<email>` identity, and the new `get_telephony_routing` webhook (Bearer/`token`-guarded) hands Triton the identity list plus the business caller-ID number (`Triton Settings.primary_twilio_number`) — Triton dials every answerer in parallel and no env configuration is needed. With `softphone_users` empty, the legacy shared identity is kept for backward compatibility. (1.22.0 is the threaded-comments release on PR #420.)

## [1.22.0] - 2026-06-12

### Added
- **Threaded replies in the Comments App ("Notes" tab).** Every note gains a **Reply** button that opens the composer pre-filled with an @mention of that note's author — frappe core notifies mentioned users on insert, so the tagged person automatically gets the native bell/email notification. Replies render indented and chronological under their top-level note; threads are single-level (Slack-style): replying to a reply @tags that reply's author but joins the same thread (the server resolves any parent to the thread root). Implementation: new hidden `Comment.custom_parent_comment` custom field (fixtures), `add_comment(parent_comment=...)` with parent validation + root resolution, thread grouping in a Vue computed (`comments.js`), compact reply rows + thread rail styles (`desk_enhancements.bundle.css`, both themes). Deleting a note that has replies keeps the replies, shown under a "(deleted note)" placeholder. `get_comments` guards the new column behind `has_column` so a code deploy that beats `bench migrate` degrades to the old flat list.

## [1.21.0] - 2026-06-12

### Added
- **Real-time incoming-call notifications + answer from the desk** (pairs with Triton >= 0.7.0; each side degrades gracefully without the other):
  - New `notify_incoming_call` webhook (`api/telephony.py`, Bearer/`token`-guarded): the Triton voice gateway POSTs every call state change (ringing in IVR → ringing agents → caller resolved → answered → ended/missed) and the endpoint republishes it as the `triton_incoming_call` realtime event to every open desk. On the first ringing event the caller is enriched against the CRM (`get_caller_info` with `create_if_missing=False` — a ringing robocall must not mint a junk Customer) including customer/contact links and open Opportunity/Project context.
  - `telephony_client.js`: the blocking incoming-call modal is replaced by a non-blocking floating call panel (Frappe CSS vars, light + dark themes) that shows the enriched caller, IVR stage/intent, Accept/Decline/End when this browser's softphone leg rings, and "Answered by X" / "Missed Call" outcomes; plus a desktop Notification per call. The real caller number/name is read from the TwiML `<Parameter>`s (Twilio's `<Dial callerId>` rewrites the leg's `From` to the business number — the old dialog showed the company's own number for every call).
  - `Triton Settings.softphone_users` (new field): limits which users register the shared desk answer device — every desk session registers the same Twilio identity and only the most recent registration rings, so unrestricted registration let any open desk tab silently steal the answer device. Listed users register; everyone else still gets the realtime call notifications. Empty = legacy behavior (everyone registers); `get_softphone_token` now returns null for non-answerers instead of a token.

## [1.20.0] - 2026-06-12

### Fixed
- **AI Governance & Sapphire Maintenance workspace fixtures** — same defect class as the Travel workspace crash fixed in 1.19.0:
  - `ai_governance.json` lacked the mandatory `type` field (its synced DB row had `type` NULL, so any save of the workspace failed with a MandatoryError) and its two shortcuts (Pending Confirmations / Action Log) were defined but never rendered because the content had no shortcut blocks. Now ships `"type": "Workspace"` and a shortcuts row.
  - `sapphire_maintenance.json` sat directly in `workspace/` instead of the required `workspace/<name>/<name>.json` layout, so module sync never imported it — the workspace didn't exist on the live site at all. Moved to the correct path and rebuilt with content blocks, "Maintenance" and "Tools" cards (now also linking Service Plan, Visit Wizard, and Day Board), shortcuts (Visit Wizard / Day Board / Add Maintenance Record), and the `type` field.
  - Both live rows were hot-patched/created to the same state so the workspaces work before the deploy; fixture `modified` stamps are later so `bench migrate` still re-syncs them.

## [1.19.0] - 2026-06-12

### Fixed
- **Travel workspace crashed on open** (`/desk/travel` rendered blank with a `Cannot read properties of null (reading 'length')` console error). The shipped Workspace JSON had no `content` blocks and no Card Break grouping its links, so the synced DB row's `content` was NULL and Frappe's workspace renderer threw. The fixture now ships proper content (shortcuts row + "Travel" records card + "Reports" card) and re-syncs on migrate; the live row was also hot-patched so the page works before the deploy.

### Added
- **Google Maps for Travel POIs.** The Travel POI form gains an "Open in Google Maps" button (uses the Geolocation pin's coordinates; falls back to a text search on the linked Address). The Travel Trip itinerary map and the /itinerary day map marker popups now include a Google Maps link per stop (tiles stay Leaflet/OSM — multi-marker embeds need an API key; the external links are Google Maps, matching the itinerary's existing "Open in Maps" links).

## [1.18.0] - 2026-06-12

### Added
- **Projects Dashboard: "Completed On" column on the Completed Projects tab, sorted newest-first by default.** `Project.actual_end_date` turned out to be empty on every inactive project in production, so `get_project_data` now derives each inactive project's completion date from its Version history — the most recent moment `is_active` flipped Yes → No (100% coverage today; falls back to the project's last-modified date if no flip was ever recorded). Both dashboard variants show the date and default-sort by it:
  - Desk page (`completed_projects.js`): the tab's table headers are now clickable to sort (the page variant previously had no sorting at all — sort styles ship with the component, themed via Frappe CSS vars).
  - Custom HTML Block (`projects_dashboard.js`): new column wired into the existing sortable headers and column selector; first click on the date column sorts newest-first, and dateless projects sink to the bottom in either direction.

## [1.17.0] - 2026-06-11

Maintenance UX follow-ups to v1.16.0: readable template names, a much larger ready-made catalog, and a way for techs to pull a future visit forward.

### Added
- **Expanded preseeded catalog** (`patches/seed_maintenance_catalog`, insert-only/idempotent): nine more Sections (Advanced Water Chemistry, Pump & Filter Service, Lighting Inspection, Auto-Fill & Water Level, Algae & Water Clarity, Spring Startup Steps, Winterization Steps, Interior Fountain Care, Safety & Electrical), six Templates (Spray Feature / Pondless / Interior Fountain / Large Display Fountain Maintenance, plus full Spring Startup and Winterization), and five Service Plans (Weekly Spray Feature, Bi-Weekly Pondless, Monthly Interior Fountain, Monthly Large Display Per-Site, Seasonal Service Only). All seeded as starting points to trim/rename in the UI. No new Chemical Dosing sections are seeded (those need site-specific Item links) — the new templates reuse the existing dosing section. Each new Section ships with example step instructions.
- **Visit Wizard "Do Visit Today"** — the wizard's picker now shows, below Today's Visits, an **Upcoming** list of Active-contract feature visits due in the next 8–30 days that have no draft yet (`get_upcoming_visits`; Per Site Visit contracts collapse to one earliest-due site entry). Tapping **Do Visit Today** (`create_visit_today`) creates a record dated today and opens the wizard on it. It's an **extra one-off** — the record carries an "Extra Visit" `visit_label`, so `update_next_visit_dates` leaves the feature's cadence untouched and the originally scheduled visit still happens later.
- **Per-step instructions in the Visit Wizard.** `Sapphire Maintenance Section` gains a `step_instructions` (Text Editor) field and a `step_images` table (new child doctype `Sapphire Section Image`: photo + caption). As the tech moves through the wizard, each section-backed step shows a collapsible (collapsed by default) "ℹ️ How to do this" panel with the instructions and any how-to images. Authored once per Section, surfaced via `get_visit_bootstrap`; the wizard groups a step's rows by section and leads each group with its panel.
- **Safety & Wrap-up step guidance.** Templates gain `safety_instructions`/`wrapup_instructions` (Text Editor) plus `safety_images`/`wrapup_images` tables for the wizard's two fixed steps ("Before you start" / "Wrapping up" collapsible panels); the Maintenance Profile gains site-specific `wrapup_instructions`, stacked into the same wrap-up panel. The profile's safety text stays prominent in the red banner. Seeded catalog templates ship generic safety/wrap-up guidance.
- **Step locations.** Each Template step row can record where on the property the step happens: `location_note`, `location_photo`, and optional `latitude`/`longitude`. The wizard shows an always-visible 📍 line above the step's cards — with a tap-to-navigate Google Maps link when coordinates are set — and folds the spot photo into the step's panel. Authored on the template (customer/project-scoped), so each site's form carries its own spots.

### Changed
- **Larger, dark-mode-readable Visit Wizard text** — bumped font sizes across the wizard and set explicit `--text-color` on cards/tabs/steppers so everything reads clearly in Frappe Light and Timeless Night.

### Fixed
- **Kiosk "Today's Visits" links were invisible on dark mode** — the list items reused `.tk-attachment-item`, a bare `<a>` with no `color`, so they fell back to the browser default blue against the dark card. Given an explicit `var(--tk-text)` colour (and a larger font / bigger tap target).

### Changed
- **Sapphire Maintenance Template now names by `template_name`** (`autoname: field:template_name`) instead of an opaque hash — readable in link fields, the contract form, and Service Plans. `patches/rename_maintenance_templates` renames existing rows (cascading through every link field via `frappe.rename_doc`); collisions are logged and skipped rather than merged.

### Notes
- Re-run `bench migrate` to apply the rename + catalog seed (both idempotent; safe to run twice). The new templates seed as **Draft** — set them Active (or reference them from a Service Plan) before they resolve automatically for a project/customer.

## [1.16.0] - 2026-06-11

Maintenance UX overhaul (pre-deployment, so schema moved freely): the contract form refills in clicks instead of grids, and techs get a guided touch-first visit wizard inside the desk.

### Added
- **Sapphire Service Plan** — one-pick contract preset (default frequency, form template, visit shape, invoicing cadence, seasonal startup/winterization defaults). Picking a plan **stamps** its values onto the contract (never a live link — later plan edits don't ripple). Four standard plans seeded by `patches/seed_service_plans` (Weekly/Bi-Weekly/Monthly Full Service, Quarterly Inspection Only).
- **"Add Water Features" batch dialog** on the contract — the project's water features pre-checked with native Select All, one shared frequency and first-visit date; a 12-fountain contract is ~10 interactions end to end. Backed by new whitelisted `get_project_water_features` (permission-respecting). New feature rows inherit the contract's Visit Frequency and anchor their next visit to the Start Date.
- **Visit Wizard** (`/app/visit-wizard`, new desk Page + `api/maintenance_visit.py`): Safety briefing/PPE gate → Water Chemistry cards with range chips and server-confirmed out-of-range flags → Chemicals pre-listed from the template with `[−]/[+]` steppers (first tap jumps to the item's usual dose; ad-hoc item add) → Inspection segmented buttons (custom per-question options honored; Fail/Replace reveal notes + photo) → tap-toggle Cleaning checklist → Wrap-up with dictation, hand-drawn signature pad, and a workflow-aware Finish. Per Site Visit records get per-feature tabs. Steps autosave through a field-allowlisted patch API with optimistic locking; everything reads/writes the ordinary Maintenance Record, so stock/timesheet/warranty/invoice automation is untouched and the desk form remains the supervisor surface. Kiosk "Maintenance Form" buttons and Today's Visits now deep-link into the wizard; the record form gains an "Open Visit Wizard" button.
- **Mandatory template items enforced** — `Sapphire Section Item.is_mandatory` now travels onto visit rows and blocks submit while unanswered (consumables exempt: qty 0 = "none used"). Previously the flag (and custom inspection `options`) were silently dropped by `get_visit_payload`.
- Template dosing items carry `default_qty` (usual dose) and `qty_step` (stepper increment); consumable rows display `item_name`/`uom`; all visit rows carry `section_title` for grouped renders.

### Changed
- **Contract form restructured for fill-out speed** — reads top-to-bottom as who → plan → features → seasonal: the standard startup/winterization pair are now **flat checkbox + month fields** (legal-agreement mapping fills them automatically); the `seasonal_visits` child table remains only for custom annual visits, in a collapsed section. Billing/source links and visit-shape/template settings fold into collapsed Billing & Advanced sections; the features grid shows just Feature / Frequency / Next Visit with template+warehouse overrides in a collapsed row section. All seasonal consumers (scheduler, template resolution, assistant tool) go through the single `iter_seasonal_visits()` helper; the assistant tool still reports one merged `seasonal_visits` list.
- Contract `validate()` materializes the contract-level default frequency onto blank feature rows and backfills blank `next_visit_date` (start date, else today) — a manually added row can no longer be silently unschedulable.
- `Sapphire Maintenance Result.selection` widened Select → **Data**: server-side Select validation rejected the custom per-question inspection options templates can define. Standard Pass/Fail/Replace/Other stay the default button set; Fail/Replace warranty behavior unchanged.
- **Workflow fixture roles fixed** (pre-deployment deadlock): Draft is editable and "Request Review" runnable by **Maintenance User** (was System Manager — techs couldn't fill their own drafts); Projects Manager gains a permission row with submit on the record so "Approve & Submit" can actually submit (previously the approving role had no doctype permission at all).

### Fixed
- `Serial No` picks on contract feature rows now also filter to the selected Project's serials.


### Fixed
- **`bench migrate` crashed in `drop_legacy_travel_trips`** (`Unknown column 'custom_travel_trip' in 'WHERE'`, seen twice on the test site — the fix was pushed to the #408 branch minutes after the PR merged, so main never got it; re-landed here). Root cause: `frappe.delete_doc` in a **pre-model-sync** patch loads the document with the NEW controller and meta against the OLD schema — `get_doc` queries child tables model sync hasn't created yet (`tabTrip Traveler`, …) and the new `on_trash` filters on the `custom_travel_trip` Custom Field that fixtures only create later in the same migrate. The patch now deletes **raw rows only** (the two legacy trips, their old child-table rows, and the sidecars `delete_doc` would have cleaned: Comments, Versions, ToDos, DocShares, Workflow Actions).
- `retire_travel_trip_workflow` deleted Workflow Action rows by a `workflow` column that does not exist (verified live) — the same 1054 crash waiting one patch later. It now clears them by `reference_doctype`.
- Defense in depth: `TravelTrip.on_trash`, `_linked_total` and `integrations._refresh_linked_total` guard every `custom_travel_trip` query with `frappe.db.has_column`, so a missing fixture field can never crash a save or delete mid-migrate or on a partially set-up site.

## [1.15.1] - 2026-06-11

### Fixed
- **CHANGELOG: duplicate `1.12.0` headings merged.** Two parallel branches (PR #407 Mermaid visual builder, PR #409 Triton feature ports) both claimed 1.11.0 and 1.12.0; the 1.11.0 sections were consolidated when the branches merged, the 1.12.0 ones were not. Both feature sets keep their original numbers under one heading each, with a note explaining the collision — entries are deliberately **not renumbered** (the numbers appear in commit messages and PR descriptions, and `__init__.py`/`package.json` only ever moved forward on main, so no code version was wrong).
- `www/README.md`: the PR #408 / main merge had stacked two competing intro sections (kiosk/itinerary/guidelines vs. kiosk/wall); unified into a single four-page index — the fix was pushed after the PR merged, re-landed here.
- **Maintenance Record crash for users with User Permissions** — opening a Sapphire Maintenance Record (and any other `has_permission` check on it: the activity-timeline counts, sharing, list/report access) raised `Table 'tabSapphire Historical Visit' doesn't exist` for any user constrained by a User Permission. The `historical_visits` field was a Table pointing at the `is_virtual` **Sapphire Historical Visit** child doctype, whose rows were faked by a `cached_property`; that hack didn't shadow Frappe's child-table loader, so `has_user_permission` → `get_all_children(include_computed=True)` SQL-loaded the non-existent table. System Managers and unrestricted users never hit the crash, which is why it surfaced only for scoped users.

### Changed
- **Historical Visits reimplemented as a read-only HTML panel.** The crashing virtual child table is replaced by a `historical_visits` **HTML field** rendered client-side (`render_historical_visits`) from the new permission-respecting whitelisted `get_historical_visits` (last 5 submitted visits for the Project, with a link back to each record). HTML fields are never SQL-loaded or walked by the permission machinery, so the failure mode can't recur. The virtual **Sapphire Historical Visit** doctype and the `cached_property` are gone; the orphaned doctype metadata is cleared by patch `remove_historical_visit_doctype`.

## [1.15.0] - 2026-06-11

> Numbered 1.15.0 because 1.11.0–1.14.0 were claimed by branches still in flight when this work started (see the version-collision note under 1.12.0).

### Changed
- **Travel Management redesigned ground-up** — the unused submittable Travel Trip + 9-state System-Manager-only workflow (2 production drafts ever, both stuck in Draft; deleted by patch) is replaced by a crew-based, collaboratively-edited trip hub:
  - **Travel Trip is no longer submittable.** Lifecycle is a plain status — *Planning → Booked → In Progress → Completed → Closed*; In Progress/Completed auto-advance from trip dates (daily job, Travel Settings kill-switch), Booked/Closed stay manual. Closed locks the document; a coordinator-only *Reopen Trip* action is the escape hatch. The Travel Trip Workflow, its states and actions are removed from fixtures and deleted by patch (`retire_travel_trip_workflow`).
  - **Crew trips**: a new *Trip Traveler* child table replaces the single `employee` field — every traveler has their own date range, per-diem calc, and Expense Claim/Advance links. Plain employees see and **edit** trips they own or are travelling on (hook-based row scoping that tracks the travelers table live); the new **Travel Coordinator** role (seeded by patch), HR Manager and System Manager see everything. The Employee dashboard's Travel count keeps working through the child-table fieldname fallback — the parent must never regain an `employee` field.
  - **Business reason linkage**: a *Travel For* dynamic link (Project / Opportunity / Lead / Customer) with read-only `project`/`customer` mirrors; the linked doctype's dashboard grows a Travel connections group (dynamic-link counts on Opportunity/Lead/Customer, mirror-field count on Project). Agenda stops keep their own related-party links, gain `visit_notes`, and can **quick-create a Lead/Opportunity from a stop** (auto-linked back via `outcome_*` + a "Created from Travel Trip" provenance field).

### Added
- **Travel finance engine**:
  - Every cost row (Flights / Accommodation / Ground Transport / new *Trip Expense* misc table) carries the shared cost block — `estimated_cost`, `cost`, `paid_by` Company/Employee + `paid_by_traveler`, `billable`, claim stamp. Ground transport rows finally have costs (typed `supplier`/`vehicle` links replace the dynamic-link crutch; Company Fleet rows are forced company-paid and link draft **Vehicle Logs**).
  - **Per diem** (rate rules by travel type in the new *Travel Settings* Single, first/last-day percent, per-traveler override, frozen once claimed) and **personal-vehicle mileage** (new *Trip Mileage* table, settings rate). Rates seed at 0 — set real numbers in Travel Settings.
  - **Per-traveler draft Expense Claims** created explicitly from the form (never as a save side-effect): employee-paid rows + mileage + per diem per traveler, expense types mapped in Travel Settings (claim generation now *throws a configuration error* instead of the old silent "Travel" fallback), `project` pushed to claim header and billable detail rows. Three-layer dedupe: row stamps + `per_diem_claimed` + doc_events that clear stamps when a claim is cancelled/deleted. Draft **Employee Advances** per traveler; claim/advance status mirrors back onto the trip.
  - Trip rollups: estimated vs actual, company-paid vs employee-paid, per-diem/mileage/claimed/advance totals.
- **Travel UI surfaces**: Calendar view on Travel Trip (one all-day event per trip-traveler, status colors); **`/itinerary`** — a mobile, chrome-free traveler page (kiosk shell pattern, `?v=` cache-busting, dark-scheme aware) with day-by-day cards, tap-to-copy PNRs/confirmations, attachments, per-day Leaflet maps and Google-Maps deep links; a Leaflet **POI map on the trip form** (Itinerary tab); a **Travel workspace**; three Script Reports — *Travel Trip Cost Summary* (estimate/actual/claimed/unclaimed), *Travel Spend by Category* (pivot by Trip/Project/Employee), *Unclaimed Travel Expenses*.
- **Travel notifications** (all gated behind *Travel Settings → Send Travel Notifications*, off by default): code-driven emails on booked / traveler added / claims drafted / closed-with-unclaimed-expenses, each with a **stable-UID ICS calendar attachment** (dependency-free RFC 5545 builder — re-sends update, not duplicate); a *Send Itinerary* form button (works regardless of the gate); daily **pre-travel reminders** (~48h before each traveler's departure) and a single-shot **post-trip expense nudge**, both stamp-first idempotent.
- Tests: bench-free ICS suite (folding/escaping/UTC/UID stability — runs in CI) + a FrappeTestCase suite covering per-diem math, rollups, traveler validation, claim dedupe/stamp-clearing and crew permission scoping.
- **Company travel guidelines page** at `/travel_guidelines` (login-gated, version-controlled): the General Travel Guidelines policy (one-adult-per-room lodging near the site, nearest-Home-Depot site info, direct 8am–5pm flights with the >1-layover approval rule, practical rentals, per diem, itemized-receipt and one-week submission rules, flight-time clock-in policy), each section with an "In the system" callout mapping the rule onto the new module — Travel POIs (*Hardware Store* category for Home Depot) instead of the old "Travel Site Information" page, per-diem amounts read from the trip's Travelers table, receipts attached to cost rows, claims via Create → Expense Claim, timekeeping via the Time Kiosk. Linked from the Travel workspace, the `/itinerary` footer, and the booked/traveler-added emails.

### Removed
- Travel Trip Workflow fixtures (workflow/states/actions), the `workflow_state`/`custom_expense_claim` fields, and the trip-level single-employee model. The `accommodation` table fieldname is now `accommodations`.

## [1.14.0] - 2026-06-11

### Added
- **AI Governance — write-confirmation gating, append-only audit, and token accounting** (ported from Triton's pending-action/audit model; new `AI Governance` module). Ships **dormant** behind `ai_write_gating_enabled` (default OFF — the app's staged-rollout contract):
  - **Write gate**: importing `assistant_tools/` (which FAC does on every MCP request before dispatch) wraps `BaseTool._safe_execute` — the single choke point both FAC execution paths converge on. With gating ON, AI-proposed mutations (`create/update/delete/submit_document`, `run_workflow`, `run_python_code`, dashboard creation) do **not** execute: an **AI Pending Action** is recorded (risk-classified High/Medium/Low, args credential-redacted, deduped by fingerprint so model retries don't spawn duplicate cards, TTL default 1 h) and the model receives Triton's proven **anti-fabrication envelope** ("NOT executed, no output exists, do not fabricate; ask the user to confirm"). Reads pass through untouched; gate failures **fail closed** for mutations; `run_database_query` is exempt (FAC enforces read-only SQL).
  - **Desk-only confirmation by design**: the requesting user (or a System Manager) confirms/cancels via form buttons → `gating_api.confirm_action` re-executes through FAC's registry *as the confirming user* (re-running FAC's accessibility + permission checks and its own audit log), records the outcome, and rolls back partial writes on failure before persisting the Failed state. There is deliberately **no MCP confirm tool** — a model-callable confirm would collapse human-in-the-loop under prompt injection. The model fetches the real result afterwards via the new read-only **`check_ai_pending_action`** FAC tool; a new bundled skill (`ee-ai-write-confirmation`) teaches assistants the flow. Desk notification + realtime ping on every proposal; hourly expiry sweep.
  - **AI Action Log** (append-only: read+create perms only — even System Manager can't edit/delete; controller-enforced; deletable only by the retention job): every executed AI mutation — confirmed or allowlist-exempt (`auto_approved`) — including attempts that failed before touching a document. Complements (not replaces) native Version history and FAC's Assistant Audit Log: adds intent, risk, the human decision link, and app-controlled retention.
  - **Exempt-doctype allowlist** (settings child table): AI create/update on listed doctypes skips confirmation but still logs; delete/submit/workflow/code execution never skip.
  - **AI Model Usage** token accounting: `api/gemini.py` now records `usageMetadata` (prompt/candidates/thoughts/total tokens) per call with a `feature` tag (`email_draft`, `sms_draft`, `morning_briefing`) — best-effort, can never fail the calling draft; toggleable. No cost field on purpose: rates change, tokens are the durable fact.
  - **Surfaces**: AI Governance workspace (Pending Confirmations shortcut, the three doctypes, FAC's Assistant Audit Log) + three Dashboard Chart fixtures (AI Tokens per Day, AI Actions by Status, AI Mutations by Risk). New **AI Auditor** role (read/report/export on all three doctypes, patch-seeded); requesters see their own pending actions via `if_owner`.
  - AI Governance module excluded from the all-doctype Triton sync webhook (high-volume log rows would spam the queue).
  - Tests: `test_ai_gate_unit.py` (bench-free under the FAC stubs: risk/mutation classification, summary templates, anti-fabrication envelope, fingerprint stability, credential redaction, truncation, stub no-op safety) and `test_ai_gating_integration.py` (bench+FAC, self-skipping: gate-marker canary, dormancy proof, envelope + dedupe, confirm/cancel/expire paths, append-only enforcement, read-tool passthrough).
  - **Known risk**: `_safe_execute` is private FAC API — a FAC upgrade could rename the seam. Mitigations: `apply_gate()` logs an Error Log when the seam is missing, and the integration canary fails on bench CI. Written against FAC v2.4.3.

## [1.13.0] - 2026-06-11

### Added
- **Wall / TV Display at `/wall`** — the dedicated TV screen, ported from Triton's wall dashboard and built on the proven Time Kiosk PWA architecture:
  - **What it shows**: a briefing band (today's tasks with assignees / overdue-at-risk with days-late / today's schedule, 4 rows + "+N more" each — pure structured data, zero LLM dependency), an **auto-rotating one-project-per-screen carousel** of the top-10 company-priority projects (rank, PM, tech lead, percent-complete meter, SVG task-completion donut), manual pips + pause toggle + per-slide progress bar, a clock, and an **Open-Meteo weather chip** (client-side keyless fetch, WMO-code icons, °F, configurable coordinates — defaults Bountiful UT).
  - **Architecture** (mirrors `www/kiosk.*`): `www/wall.py` controller (guest → `/login?redirect-to=/wall`, staff-role gate, server-injected `WALL_BOOT` first paint), chrome-free `wall.html`, root-scope `wall-sw.js` (kiosk-sw minus the geolocation queue: precache + offline shell + last-good data responses so brief outages don't blank the screen), vanilla `public/js/wall/app.js` + standalone dark `wall.css` — **perf-lite by construction** (no backdrop-filter, no animations beyond the progress bar; Raspberry-Pi friendly).
  - **Deploy pickup, two belts**: the service worker is registered with the per-deploy `?v=` token (extracted `get_deploy_version()` into shared `utils/deploy.py`; kiosk re-exports it), re-checked every 60s, page reloads immediately on `controllerchange`; and every data refresh compares the server's `deploy_version` against `WALL_BUILD` and reloads on mismatch — a 24/7 screen converges on a deploy within ~a minute even if the worker never installed.
  - **Auth**: new low-privilege **Wall Display** role (seed patch, `desk_access=0`) added to the Task Dashboard's staff-role gate; sign each TV in once with a dedicated user holding only that role. Data endpoint `get_wall_dashboard_data` = the Task Dashboard payload + per-project `GROUP BY` task stats + settings + deploy version (role-gate-then-permission-free, same rationale as the block). 401/403 on refresh reloads through login.
  - **Settings** (ERPNext Enhancements Settings → Wall / TV Display): carousel rotation seconds (60), data refresh seconds (300), weather toggle/lat/lon/label.
  - **Donut semantics**: `Completed` + `Invoiced` count as done; `Canceled`/`Template` excluded from both slices.
  - Tests: `tests/test_wall_dashboard.py` (stats math, payload shape, settings defaults, role gate incl. guest denial, deploy-token stability).

## [1.12.0] - 2026-06-11

> Two branches in flight both claimed **1.11.0 and 1.12.0**: the Mermaid visual-builder PR ([#407](https://github.com/sapphirefountains/erpnext_enhancements/pull/407), merged first — the site moved to 1.12.0) and the Triton feature-port PR ([#409](https://github.com/sapphirefountains/erpnext_enhancements/pull/409) — its four features actually reached the site together when the version moved to 1.14.0). Both branches' entries are kept under shared 1.11.0/1.12.0 headings rather than renumbered, since the numbers appear in commit messages and PR descriptions. `__init__.py`/`package.json` only ever moved forward on main (1.12.0 → 1.14.0), so no code version was wrong.

### Added
- **Morning Briefing — per-user daily AI digest** (ported from Triton's briefing scheduler):
  - **Pre-generated weekday mornings**: new cron scheduler entry (`30 6 * * 1-5`, evaluated in the site's System Settings timezone — verify it's America/Denver) enqueues a long-queue batch that builds one briefing per enabled recipient and caches it in the new **Daily Briefing** doctype (one row per user/day via `format:` autoname; durable on purpose — Redis caches are flushed by migrate/clear-cache exactly when 24/7 displays churn). 60-day retention via a daily purge job.
  - **Contents** (all native data, queries shared with the Task Dashboard backend): today's and overdue Tasks assigned to the user (`_assign`), today's public + own calendar Events, the user's open Opportunity pipeline, and due ToDos.
  - **Narrative**: Gemini via the existing `api/gemini.py` wrapper with strict only-reference-live-data guardrails and fixed sections (📅 Schedule / 📋 Tasks / ⚠️ Overdue / 💼 Pipeline Pulse / 🎯 Top 3 Priorities). When Gemini fails or is switched off, a **deterministic markdown fallback** composed from the same data ships instead — never a dead apology; `narrative_source` records which path ran.
  - **Surfaces**: new "Morning Briefing" **Custom HTML Block** (insert-only seed patch, same repo-source model as the Task Dashboard block; renders via `frappe.markdown`, Refresh button force-regenerates) and an optional **per-recipient email** (markdown → HTML, sent only by the scheduled batch).
  - **Settings** (ERPNext Enhancements Settings → Morning Briefing): `briefing_enabled` master switch (default OFF, staged-rollout convention), `briefing_use_gemini` cost switch, `briefing_recipients` child table (new **Briefing Recipient** doctype) with per-row email opt-in. Any staff role can still pull a briefing on demand from the block; recipients govern the batch + email.
  - Endpoint: `get_morning_briefing(force=0)` — session user only, role-gated like the Task Dashboard.
  - Tests: `tests/test_briefing.py` (fallback composition incl. empty-day friendliness, prompt guardrails, per-day cache idempotency, force regeneration, recipient batch, master-switch gates, purge retention) — Gemini stays off throughout, so no network calls.
- **Process Document Visual Builder** — an in-app split-pane Mermaid editor on the Process Document form (custom button + a link above the preview), so charts can be built without leaving ERPNext:
  - Live preview: code on the left, diagram on the right, re-rendered (debounced) as you type; `mermaid.parse` runs first so syntax errors surface in a non-destructive error bar while the last good diagram stays on screen.
  - **Insert…** menu drops building blocks at the cursor: starter flowchart, step/decision/start–end/external-system nodes, labeled and dotted arrows, subgraphs, and the **Sapphire Fountains style pack**.
  - Zoom in/out/fit, Copy Code, Download SVG, and a deep link to mermaid.live; **Apply to Document** writes back to `mermaid_code` (normal save flow — nothing is saved behind your back).
  - Builder chrome uses Frappe CSS variables (works in Frappe Light and Timeless Night per the repo's dark-theme convention).
- **Sapphire Fountains brand theme for every Mermaid diagram** (`public/js/global_enhancements/mermaid_theme.js`, shipped in the global bundle as `window.sf_mermaid`). Branding extracted from sapphirefountains.com (Bricks theme globals): **Lato** plus the sapphire/teal palette (`#00609C` primary, `#00A0DF` sky, `#62CBC9` teal, `#00263E` navy, `#B14FC5` orchid, `#F8F8F8` mist). Applied via Mermaid's `base` theme variables to the Process Document preview, the Visual Builder, and the Triton widget's fenced-code diagrams — one source of truth, no more per-file `theme: "default"`.
  - Diagrams deliberately render on a **light canvas in both desk themes**: the seeded charts class their nodes with literal pastel fills, which would pair with light theme text under a dark Mermaid theme and become unreadable. Diagrams are treated like print (literal colors, light surface); the surrounding UI follows the desk theme.
  - The canonical brand `classDef` pack (eight classes, explicit `color:`) ships as `window.sf_mermaid.CLASS_PACK` and as a builder Insert snippet.

### Changed
- **"Sapphire Fountains Enhancements Flow" restyled to the brand palette** (seeder + live site updated; render-verified through the real `mermaid_theme.js` against `mermaid@11.15.0`). The eleven legacy ERPNext charts keep their Material module colors deliberately — eight semantic categories need more distinct hues than the brand palette offers; the brand theme still restyles their typography, edges, and subgraphs at the renderer level.

## [1.11.0] - 2026-06-11

> Shared heading — two branches claimed this number; see the version-collision note under 1.12.0.

### Added
- **Call Intelligence — the stock Call Log becomes the system of record for phone calls** (first feature ported natively from Triton). Triton keeps handling Twilio + post-call AI analysis; ERPNext now stores and surfaces the results so calls are browsable without Triton's frontend:
  - **Call Log upsert** (`api/call_intelligence.py`): idempotent by Twilio Call SID (docname == SID via the stock `field:id` autoname). Native fields reused — direction → `type`, Triton status mapped onto stock options (`missed` → No Answer), `duration`, executive summary → `summary` (written only while empty so manual edits via the native Call Summary dialog survive re-deliveries), IVR intent → `type_of_call` (Telephony Call Type, get-or-created), agent → `employee_user_id`/`call_received_by`, Customer/Contact/Lead in `links`. `recording_url` is rewritten to the **private File URL** of the WAV so the stock audio player works in the desk (the Twilio URL is Basic-auth-protected).
  - **15 fixture-owned custom fields** on Call Log for the AI analysis: caller name, sentiment, escalation risk, CSAT score, agent display name, voicemail URL, link to the transcript Communication, follow-up actions, topics, compliance flags (+ hidden flag bit), and the raw analysis JSON. Sentiment / escalation / caller name are list-view columns and standard filters; partial webhook re-deliveries never blank stored fields.
  - **Ingestion**: `process_unified_recording` (existing gateway webhook) now consumes the intelligence payload keys it previously ignored and upserts the Call Log after creating the Communication + File; Communication dedup now goes through the Call Log's `custom_communication` link first (the subject-LIKE-SID match stays as fallback). A new standalone guest endpoint `process_call_intelligence` (same Bearer-secret guard) ingests recording-less calls — e.g. missed calls with a voicemail URL. **Missed calls never auto-create Customers** (`get_caller_info` gained `create_if_missing`; robocall protection).
  - **Call archive UI**: list view renders sentiment/escalation as theme-aware indicator pills (+ 🚩 on compliance-flagged calls); the form gets View Transcript (dialog reading the linked Communication), Open Communication, and Call Back (Triton) buttons. Search fields include the resolved caller name.
  - **Call Center dashboard** (native Dashboard, `/app/dashboard-view/Call Center`): daily call volume, sentiment / escalation / direction / intent donuts; number cards for total, high-risk, missed calls and average CSAT (weekly % stats). Shipped as curated fixtures filtered by name. Known v1 gap: group-by charts have no rolling time window.
  - **Notifications**: "High Escalation Risk Call" and "Compliance Flag on Call" (Value Change on the fixture fields) email the new **Call Center Supervisor** role (seeded by patch with read/write/report on Call Log; stock perms already give every Employee read access to the archive).
  - **Triton Settings**: new "Send Call Email Digest" checkbox (default on) — the per-call email to info@ can now be switched off once the desk archive + notifications replace it.
  - Telephony module excluded from the all-doctype Triton sync webhook (`utils/triton_sync.py`) — ingested Call Logs no longer echo a change-webhook straight back to Triton.
  - Tests: `tests/test_call_intelligence.py` (upsert idempotency, status/direction/sentiment mapping, partial-update no-blank, manual-summary preservation, call-type get-or-create, invalid-SID guard, voicemail missed-call path).
  - Gateway contract (Triton-side, optional fields on the existing POST): `to_number`, `status`, `start_time`, `sentiment`, `escalation_risk`, `analysis` (object: sentiment, escalation_risk, customer_satisfaction, topics[], compliance_flags[]), `ivr_selection`, `agent_user`, `agent_name`, `voicemail_url`. Already-sent fields now consumed: `direction`, `duration`, `caller_name`, `follow_up_actions`. Once deployed, Triton can drop its direct Call Log REST write and unset `VOICE_NOTIFY_EMAIL` (the desk notification replaces it).
- **Process Document charts are version-controlled and app-seeded.** All Mermaid.js Process Documents now live in the repo (`setup/process_documents.py`) and are upserted by a new `after_migrate` hook: missing charts are created, and a chart whose `mermaid_code` drifted from the canonical text is overwritten on the next migrate — the same repo-is-source-of-truth philosophy as `fixtures/`. Site-created documents under other titles are untouched and nothing is deleted.
  - The eleven charts authored on the site (ERPNext Flow, Sales and CRM, Buying and Procurement, Inventory, Manufacturing, Accounting, HR and Projects, Lead to Project, How to Order, Document Linking, Customer Management) were ported verbatim (whitespace-normalized).
  - New chart **"Sapphire Fountains Enhancements Flow"** documents the app's own processes and where they hand off to stock ERPNext: Opportunity → custom Create Project → hand-off process steps (anchored auto-completion, SLA notifications, daily escalation); Maintenance Contract → scheduler-drafted visit records → Stock Entry / Warranty Claim / draft Sales Invoice / Timesheet; Time Kiosk clock-ins (Job Interval → GPS logs → Timesheet consolidation, kiosk Maintenance Form button); the Travel Trip workflow → draft Expense Claim; and the background integrations (Google Drive folder provisioning, QuickBooks Online two-way sync, Google Calendar task push, GA4 dashboard, Triton telephony/AI, read-only MCP tools).
  - **Charts must not contain raw HTML** (no `<br/>` line breaks, no `<-->` arrows): Frappe HTML-sanitizes the Markdown Editor field on save as soon as a value looks like HTML, mangling every `-->` into `--&gt;` — which breaks the rendered diagram *and* would make the seeder's drift check rewrite the document on every migrate. Multi-line labels use quoted strings with real newlines instead (caught live: the first version of the new chart was stored escaped). Enforced by the new bench-free `tests/test_process_documents.py` (extracts the chart dict via `ast`, so it runs without Frappe), alongside title/flowchart shape checks. All 12 charts were also render-verified against the exact pinned mermaid CDN build the form script loads.
  - The live site was seeded directly (all 12 documents now match the repo byte-for-byte), so the first post-deploy migrate is a no-op for them.

### Changed
- **Mermaid.js bumped 11.12.0 → 11.15.0** (latest) in the two CDN loaders: the Process Document form renderer (`public/js/process_document.js`) and the Triton widget's fenced-code diagram renderer (`triton_widget.js`). All 12 seeded charts re-render-verified against 11.15.0 with identical node counts. The exact-version pin stays deliberate: an unpinned `mermaid@11` would let CDN updates change rendering behavior with no deploy.

## [1.10.0] - 2026-06-11

### Added
- **Searchable, nearest-first Project & Task pickers on the Time Kiosk** — the plain dropdowns become type-to-filter comboboxes:
  - Search matches the displayed title **and** the docname, so technicians can find a project by number (`PRJ-0123`) as well as by name; each option shows the docname as a sub-line when it differs from the title. The task picker gets the same treatment (matches `TASK-####` too).
  - **Nearest site first**: `get_kiosk_options` now attaches each project's Sapphire Maintenance Profile site coordinates; the kiosk sorts the picker by distance from the device (one shared position fix, refreshed when the picker opens and at most every 2 minutes) and shows a distance badge ("350 m" / "1.2 km") next to projects with known sites. Projects without coordinates follow alphabetically; with no fix (permission denied / indoors) the list is simply alphabetical.
  - Touch-friendly: large rows, a ✕ clear button, and keyboard support (arrows / Enter / Escape) for kiosks with a keyboard. The geofenced "clock in here?" suggestion now feeds the same picker and reuses the shared position fix instead of requesting its own.
- **Frappe Assistant Core (FAC) integration — custom MCP tools + skills.** AI assistants connected to the site's MCP endpoint (Claude via FAC) can now reach the app's cross-doctype business logic, not just generic document CRUD. Seven **read-only** tools in the new `assistant_tools/` package, registered via the `assistant_tools` hook and auto-discovered by FAC's custom_tools plugin:
  - *Maintenance ops*: `maintenance_day_board` (wraps the Day Board feed: scheduled drafts, techs clocked in, submitted today, flagged), `maintenance_contract_status` (contract terms, per-feature cadence, upcoming/overdue visit list), `maintenance_visit_history` (submitted visits list/detail incl. readings-with-ranges, cleaning tasks, consumables + chemistry trends), `maintenance_site_briefing` (safety instructions, access codes, contract context, last visits, open drafts — wraps the technician dashboard context).
  - *Projects*: `project_status_overview` (portfolio / single-project / master-project scopes: health metrics, hand-off process-step state, optional Gantt task list), `project_procurement_status` (stage-graduation rollup or document tree over the MR→RFQ→SQ→PO→PR/SE→PI chain).
  - *Time kiosk*: `workforce_time_status` (who's clocked in now, per-user status, daily/range hour rollups per employee/project, Timesheet sync health). GPS/location data is deliberately not exposed.
  - Permission model: each tool gates on a `requires_permission` DocType (also controls per-user tool visibility in FAC); list queries use `frappe.get_list` (role + user permissions); reused raw-SQL feeds get explicit `has_permission` document gates.
- **Three bundled FAC skills** (workflow prompt templates, synced into `FAC Skill` rows on migrate via the `assistant_skills` hook): *Maintenance Dispatcher*, *Project Status Reporter*, *Time Kiosk Analyst* — step-by-step multi-tool playbooks with data-model pitfalls, in `data/skills/`.
- **No hard FAC dependency**: the hooks are inert strings on sites without frappe_assistant_core; nothing in app code imports the new package (tripwire-tested). Two new test suites: `test_assistant_tools_schema.py` (bench-free contract tests: tool/module naming, FAC built-in collision guard, inputSchema validity, skills manifest, FAC-optional tripwire) and `test_assistant_tools_integration.py` (bench + FAC only, self-skipping: registry discovery, execution smoke tests, fixture-backed assertions, roleless-user denial).

## [1.9.0] - 2026-06-10

### Added
- **Modular maintenance visit forms (Google Forms replacement)** — the per-project / per-water-feature maintenance forms move into ERPNext as composable building blocks:
  - **Sapphire Maintenance Section** (+ Section Item child): a reusable, typed form block — Chemical Dosing, Water Chemistry, Equipment Inspection, or Cleaning Tasks — authored once and shared across every form template. **Sapphire Maintenance Template** is now *composed* of sections (`sections` child table) instead of owning flat question rows; the superseded `Sapphire Template Item` doctype is dropped by patch.
  - **Maintenance Record reshaped**: two new typed child tables (`chemistry_readings`, `cleaning_tasks`) join the existing results/consumables; every section row carries `section` + `serial_no` columns. The form instantiates all four tables from the resolved template via the new whitelisted `get_visit_payload` (replaces `get_template_items`), including on first open of scheduler-drafted records.
  - **Chemical dosing reduces stock the same way everywhere**: dosing sections prefill consumable rows at qty 0 mapped to real Items; on submit only rows with qty > 0 become the Material Issue Stock Entry. Per-row warehouse defaults resolve feature's on-site store → technician's vehicle (new `Employee.custom_default_vehicle_warehouse`) → new Settings default.
  - **Water chemistry ranges + supervisor alerts**: readings carry min/max targets (per-feature overrides via the new `Serial No.custom_reading_overrides` table, matched by reading label); `validate` computes `out_of_range` flags and the new "Maintenance Reading Out of Range" Notification emails the new **Maintenance Supervisor** role on submit, with a timeline Comment for the audit trail.
- **Sapphire Maintenance Contract** (+ Contract Feature / Seasonal Visit children) — the operational contract driving visit scheduling. Created via "Create → Maintenance Contract" from a submitted Sales Order *or* a Signed Maintenance Services Agreement (Project Contract), linking both: the legal doc contributes frequency, invoicing cadence, start date and included seasonal options (startup/winterization months); the Sales Order contributes the covered water features. `visit_shape` decides whether the scheduler drafts one record per feature or one per site visit (per-feature rows inside a single form); seasonal rows draft once a year in their target month. One Active contract per project, auto-expired past `end_date`.
- Seed patch `seed_maintenance_sections`: four sample sections (dosing mapped to the live chemical items, pH/chlorine/ORP/alkalinity ranges, inspection + cleaning checklists), three Draft templates (Standard Fountain Maintenance, Seasonal Startup, Winterization), the supervisor role, and Settings defaults. Insert-only and existence-guarded.
- **Project-only contract flow for verbal/legacy arrangements** — a third "Create → Maintenance Contract" entry point on the Project form, for maintenance relationships with no Sales Order or written agreement: prefills the customer, the covered features from the project's own Serial Nos (`custom_project`, filtered to the configured water-feature Item), and the resolved Active template; links a Maintenance Sales Order or Signed agreement when one happens to exist, requiring neither. Projects with an Active contract get a jump-to button instead.
- **Field & supervisor conveniences** for the maintenance forms:
  - *Today's Visits* on the idle kiosk (the tech's open visit drafts, one tap from each form) and a *geofenced clock-in suggestion* — site coordinates on the Maintenance Profile + a Settings radius make the kiosk offer the nearby project when a visit is due.
  - *Offline tolerance*: the kiosk service worker now serves the last good kiosk API responses (options, status, visits, maintenance context) in dead zones; desk form edits were already preserved by the app-wide autosave (localStorage + User Form Draft).
  - *Clock auto-fill*: a record's blank clock-in/out and paused time fill themselves from the technician's kiosk Job Interval on save/submit — no double entry.
  - *Per-row photo capture* (Attach Image on inspection/reading/cleaning rows) and a *🎤 Dictate Note* button (Web Speech API → new `visit_notes` field; Chrome/Android).
  - *SMS nudge* (Settings toggle): hourly job texts a tech via the Triton gateway when they clocked out of a maintenance project 1–4 hours ago without submitting a form; each interval is evaluated exactly once (hidden `maintenance_nudge_sent` flag).
  - *Visit completeness score* (`completion_percent`, computed in validate; consumable prefills excluded) shown in the list view and as a form indicator.
  - *Out-of-range follow-up automation* (Settings days, 0=off): a flagged submission drafts a deduped "Chemistry Follow-Up" visit plus a dated ToDo for the technician; labelled visits don't advance the regular cadence.
  - *Chemistry trend sparklines* on the technician dashboard — last 5 visits per reading with red out-of-range dots.
  - **Maintenance Day Board** (`/app/maintenance-day-board`; System Manager / Maintenance Supervisor / Projects Manager): scheduled drafts, techs currently clocked into maintenance projects, submitted today, and flagged visits from the last 7 days; auto-refreshes every 60 s.
  - *Weekly truck restock suggestions* (Settings toggle + source warehouse): one draft Material Transfer request per technician vehicle warehouse replenishing the past week's issued consumables.
- **Time Kiosk knows about maintenance visits** — clocking into a project with an Active Maintenance Contract (or Active form template) shows a **Maintenance Form** button on the kiosk's active-job card (new `get_maintenance_context` endpoint: links the newest open draft, else a prefilled new record; opens in a new tab so the clock keeps running, with a one-time "this project requires a maintenance visit form" toast). Clocking out — or switching to a *different* project — re-checks the server and warns with a confirm (same OK-to-go-back semantics as the attachments prompt) when the technician hasn't submitted a maintenance record for that project since clock-in. Offline or non-maintenance projects are unaffected. Field technicians get desk access to the visit form via the native **Maintenance User** role (create/write/submit on Maintenance Record, read on Contract/Template — previously System Manager-only, which would have blocked techs entirely); Maintenance Supervisor gets read/write on records.

### Changed
- **Predictive scheduling is contract-driven**: the daily generator reads Active Maintenance Contracts' feature rows; submit-time scheduling (`update_next_visit_dates`, renamed from `update_sales_order_next_visit`) rolls contract dates forward and *mirrors* them to the Sales Order Item custom fields for existing reports. Projects without an Active contract keep the legacy Sales-Order-Item path. The historical double-fire on submit is gone (doc-event only).
- **Warranty flow uses the native Warranty Claim** — one draft claim per failed in-warranty water feature (complaint lists the failed checks), replacing the Material Transfer Material Request and its `WARRANTY-RETURN-PENDING` placeholder item.
- **Per-visit invoicing respects the contract**: the draft Sales Invoice is only auto-created when the contract bills "Per Visit" (looked up via the contract's Sales Order first); consumables bill only consumed quantities.
- **No more hardcoded item codes** — new Settings fields: *Water Feature Item* (drives the Serial No pickers on Sales Order and the contract; was the literal `"Customer Water Feature"`), *Consumables Item Group* (the record's item picker filtered on the literal `"Consumables"` while the chemicals live in "Service"), and *Default Consumables Warehouse*. Runtime code never matches items by code string; sections store Item links, which survive renames.
- Technician dashboard widget now also surfaces contract context (gate code, key location, preferred days/time from the signed agreement) and the Project's Service-stream deliverables; it renders for per-site records without a header serial. Portal/print format gains Water Chemistry (with out-of-range badges) and Cleaning Tasks tables, per-feature columns on per-site records, and hides untouched consumable prefills.

## [1.8.1] - 2026-06-10

### Fixed
- **Saving a new Project Contract crashed** (`AttributeError: 'ProjectContract' object has no attribute 'amended_from'` — hit twice in production testing: saving a new MSA from the desk, and Generate Contract from a Project). Two defects, both fixed:
  - The validate/autoname path read fields as **bare attributes**. New documents omit unset fields (desk saves strip nulls before POSTing; `frappe.new_doc` only initializes fields that exist in meta) and `BaseDocument` raises `AttributeError` for unset attributes — so the first save of *any* contract type crashed on either path, and an Owner Contract with untouched fee fields would have crashed in `_compute_totals` the same way. Every read in `autoname` / `validate` / `_stamp_revision` / `validate_msa_gate` / `_compute_totals` / `on_submit` / `render_body` now goes through `self.get(...)`. New regression suite (`TestNewDocAttributeSafety`) exercises the controller on documents with the attributes entirely absent — exactly what reaches the server.
  - **`amended_from` was missing from the doctype definition** (dropped when the schema was regenerated in v1.5.0; confirmed absent on the live site — which is also why `frappe.new_doc` never initialized it). Beyond the crash, this would have silently broken the revision lineage: amending a cancelled contract had no column to store the predecessor in. The field is now declared explicitly (Link → Project Contract, read-only, no-copy, print-hide), as a submittable doctype requires; `bench migrate` adds the column.

## [1.8.0] - 2026-06-10

### Added
- **Master switch for the entire Jun 9 process-automation suite** — new checkbox **ERPNext Enhancements Settings → Process Automation → Enable Process Automation Suite**, default **OFF**, so production can deploy this code completely dormant (behaving exactly as before) while the test environment runs with it on; flip production when test is producing what you want. No deploy to flip: server hooks read the live value on every event, desk UI reads `frappe.boot.ee_process_automation` (new bootinfo flag, same model as live-collab) on each page load.
  - **Gated when OFF**: Closed-Won SMS, the won-but-unconverted daily reminder, Payment Received comment + PM/AE alerts, hand-off step seeding on new projects, step notifications + SLA escalations, the Project form's hand-off progress bar / Start button (`start_process` also server-throws), the Generate Contract buttons + `create_contract`, and the Sales Pipeline board (friendly "switched off" notice instead of data).
  - **Deliberately not gated** (invisible, zero flow impact — and they make the eventual flip seamless): the silent `custom_stage_changed_on` / payment-date stamps (so stage-aging history is already accumulated the day the board goes live), schema/fixtures/seeded templates (data at rest), and the Task Dashboard block (only appears where explicitly added to a Workspace). Two always-on form-level conveniences that a runtime flag cannot gate are called out in the settings description: the Lead quick-entry dialog and the Opportunity field descriptions (both fixture property setters; low-risk).
  - Guard logic centralized in `feature_flags.py` (`process_automation_enabled` / `throw_if_process_automation_disabled`); test suites pin the flag on and new tests assert the off state is fully silent (no seeding, no alerts, whitelisted endpoints throw a clear message).

## [1.7.2] - 2026-06-10

### Changed
- **All agreements now carry the current office address (85 W 300 S, Bountiful, UT 84010)** — user-directed update, Jun 10 2026. The three retained originals predated the move: the NDA's intro recited "3176 South 400 East" and the Architect Agreement's two notice-address blocks (agreement + embedded SOW signature pages) listed "3176 S 400 E". The corrections live in the regeneration pipeline (`scripts/contract_templates/jinjify.py`, count-asserted like every other transform), so re-running against the source .docx files preserves them; the revised-suite templates already used the new address and reproduced byte-identically. No "3176" remains in any template.

## [1.7.1] - 2026-06-10

### Changed
- **Contract numbers now carry the generation year**: `SF-OC-2026-0001` instead of `SF-OC-0001` (all eight series: `SF-{MSA,SOW,OC,RA,MAINT,NDA,ARCH,EC}-YYYY-####`). Frappe keys the series counter on the resolved prefix, so numbering restarts at 0001 each January — the year in the number is also the year of the counter. No migration needed: the contract system is unreleased, so no existing names are affected.

## [1.7.0] - 2026-06-10

The original DOC-#### agreement packet, reconciled against the Contract Comparison Report and the **three retained agreements** added as generatable templates. The other five originals are deliberately NOT templated: the report marks them superseded by the revised suite already shipped in v1.5.0 (DOC-0034→MSA, DOC-0099→SOW, DOC-0100→Maintenance, DOC-0032→Rental, DOC-0102→Owner Contract) — templating them would have resurrected retired legal documents.

### Added
- **Mutual Non-Disclosure Agreement** (`nda`, DOC-0033, series `SF-NDA-`): effective date renders as "this {day} day of {month}, {year}" from the contract date; counterparty name/address fill from the party; the recital's "[proposed business relationship]" is overridable via a new `nda_purpose` field (generic wording kept when empty); signature blocks stay handwritten. Added to the Supplier Generate-Contract button (the common pre-MSA flow) and creatable standalone for anyone else.
- **Architect Agreement** (`architect`, DOC-0101, series `SF-ARCH-`, party **Customer** — the architect engages and pays Sapphire as design subconsultant): header and embedded-SOW tables fill with architect + effective date; the recital's Owner / Prime Agreement date come from new `architect_owner` / `architect_owner_agreement_date` fields; the embedded SOW's number is the contract's own name, and its "Description of Services to be Provided by Sapphire" renders the `scope_of_work` field (writing lines when empty).
- **Employee-Contractor Agreement** (`employee_contractor`, DOC-0137, series `SF-EC-`): printed name prefils from the party; signature/date stay handwritten. The comparison report flags this one "REVIEW RECOMMENDED" — the template is verbatim; route wording changes through legal, then edit the site record.
- **Flexible party model.** `Contract Template.party_type` gains **"Any Party"**; `Project Contract.party_type` is now pickable (Customer / Supplier / **Employee** — Employee joins for the EC agreement) when the template is flexible, and stamped/enforced by the controller when fixed. Party-name resolution covers all three doctypes.
- The conversion pipeline (`scripts/contract_templates/`) covers all eight documents, skips files missing from a given folder so it can be run per-source-folder — and reproduced the five committed templates **byte-identically** when re-run, proving the determinism the README promises.

### Notes
- Both retained supplier-facing docs carry the old office address (3176 S 400 E) and DOC-0137 predates the revised suite's legal pass — kept **verbatim** per the report's "retained" status; wording/address updates belong to a legal review, then a site-record edit.
- New templates seed on migrate (insert-only, existing sites get just the three new records).

## [1.6.0] - 2026-06-10

The SOW's scope of work now comes from the meeting's scope model instead of free typing.

### Added
- **SOW scope composes from the source's scope tables.** `compose_scope_of_work` (whitelisted) walks the four value streams' `custom_{design,build,service,rent}_customer_requests` / `_deliverables` child tables — which exist identically on **both** Opportunity and Project — and renders each non-empty stream as "Customer Requests" (the customer's words, entered by Sales) and "Deliverables" (the PM/Design breakdown, PRO-0204 Step 6) lists. Wired three ways: (1) **prefill at generation** — an SOW created from a Project uses the project's tables, falling back to the source Opportunity's ("depending on which stage the contract is in"); (2) **auto-pull on link** — setting Project/Opportunity on an empty-scope SOW draft fills it silently; (3) **"Pull Scope from Source"** form button re-pulls deliberately, confirming before overwriting existing scope.
- **SOW generation from Project and Opportunity** (not just Supplier): the Generate Contract dialog gains a Subcontractor (Supplier) picker for the SOW type, so the PM can issue an SOW from the project they're staffing — party, scope, project link and supplier billing address all prefill in one step. The Signed-MSA gate applies on every path (check up front, offer to create the MSA when missing).
- Scope-composition tests: stream ordering, request/deliverable sectioning, HTML escaping, multiline rows, omission of empty streams and whitespace-only rows.

### Changed
- `create_contract` now resolves the party billing address generically (Customer or Supplier) instead of Customer-only.

## [1.5.0] - 2026-06-10

Phase 4 contract generation: Brian's revised agreement suite (Apr 2026) generates inside ERPNext — auto-populated, print-friendly, revision-tracked. Five agreements: Master Subcontractor Agreement, Statement of Work, Owner Contract, Rental Agreement, Maintenance Services Agreement. (The sixth document in the packet, the Contract Comparison Report, is an internal review doc — not a generation target.)

### Added
- **Templates as data: new `Contract Template` doctype** holding each agreement as Jinja HTML, seeded **insert-only** by the `seed_contract_templates` patch from `templates/contracts/*.html` — legal-text edits happen on the site record, no deploy. The bodies were converted **mechanically** from the .docx sources (never retyped — legal text transfers verbatim) by a two-step pipeline preserved in `scripts/contract_templates/` (docx→HTML converter + an assertion-checked Jinja injector whose every replacement asserts its expected match count, so a future docx revision that moves a fill point fails loudly instead of shipping a dead blank). Every template parses and renders against both an empty and a fully populated context (tested).
- **`Project Contract` — the generated, revision-tracked instance** (submittable, ~110 fields, per-type sections via `template_key`). Structured deal data per the meeting decision: child tables for Owner phases (fee/retainer per Design/Construction/Maintenance), payment **milestones** (SOW compensation + Owner construction schedule), rental **equipment items**, and maintenance **service options** — with computed totals (`total_contract_value`, `total_due_at_signing`, `total_rental_amount`, `total_design_fee`, `milestones_total`) that only count included phases. **Revision tracking** is native: submit = issued; cancel + amend = Revision N (`revision` increments off the predecessor, `amended_from` keeps the lineage, the print shows "Revision N (supersedes …)"), and `track_changes` records field-level draft history. Naming series per type: `SF-MSA-` / `SF-SOW-` / `SF-OC-` / `SF-RA-` / `SF-MAINT-` (`SF-SOW` is the templates' own convention).
- **The MSA→SOW sequencing rule, enforced.** A Statement of Work validates that its linked MSA (a) is an MSA, (b) belongs to the same Supplier (the party model matches ERPNext's Subcontracting module), (c) is submitted **and Signed** — and stamps the MSA effective date into the SOW header. The Supplier-side button checks `get_signed_msa` up front and offers to create the MSA first instead of letting the user fill a doomed form.
- **Generation buttons** (`public/js/contracts.js`, added to Opportunity/Project/Supplier `doctype_js`): Create > Generate Contract → type picker → whitelisted `create_contract` prefils the draft from the source: party, contacts, billing/site addresses (directory model), project name/description (Opportunity summary), **value streams preselect the Owner Contract phases**, rental dates from delivery/take-down datetimes, and rent-deliverable rows become equipment lines. Owner Contracts can be generated from a won Opportunity *before* the project exists (Brian's "on your phone in front of them" flow) or from the Project (the PRO-0204 nominal flow).
- **Print-friendly output**: "Project Contract Print" Jinja print format (fixtures, hooks filter extended) renders `doc.render_body()` with serif contract styling, bordered tables that never split across pages, headings that keep their content, and — the paper-fallback guarantee — **every empty field prints as a classic fillable line**, so a half-filled contract is still usable on a clipboard. Checkboxes render as ☒/☐ from the structured data (MSA tier, property type, visit/invoicing frequency, phase selection).
- **Card data never enters ERPNext.** The Maintenance Agreement's payment-authorization section is driven by a per-contract choice: *Payment Link* prints secure-link/QR instructions (pairs with Phase 5 text-to-pay), *Manual Card Authorization* prints the blank handwritten form, *Both* prints both — in every case the card-number/CVV fields are blank lines on paper only.
- **Project Contract form** (`project_contract.js`): MSA link filtered to the party's Signed MSAs, a "Mark as Signed" action on submitted contracts (stamps signed by/on — the paper flow until e-sign lands), and a Preview/Print shortcut.
- Test suite (`tests/test_project_contract.py`): the MSA gate matrix (signed passes + stamps effective date; unsigned / unsubmitted / wrong-supplier / missing all block), per-type total computations, revision stamping, and the all-templates render check.

### Notes
- E-signature integration is a planned follow-up (provider TBD); the status workflow (Draft → Out for Signature → Signed) is already in place.
- Subcontractors are **Supplier** records (per the Subcontracting module); create the Supplier before issuing their MSA.

## [1.4.0] - 2026-06-10

The parked Phase 2 item from the Jun 9 meeting: the morning task dashboard refinements — "I'd like to see all of them at once… just a list of the top 10" and "it won't say Joe and Bob are going to this site today — we can" — shipped as a new **"Task Dashboard" Custom HTML Block** (the existing TV screen wasn't in the repo or among the site's blocks, so this is a fresh, version-controlled widget rather than a guess-edit of whatever the screen runs today).

### Added
- **`api/task_dashboard.py` — one whitelisted endpoint (`get_task_dashboard_data`) feeding the whole screen**: (1) *Top 10 priority projects as a list, all at once* — Active projects with `custom_company_priority` 1–30 (the same eligibility rule the home widget uses), each row carrying rank, **PM and tech lead resolved to names** (bulk Employee fetch), and a percent-complete bar; (2) *Overdue / at-risk* — open tasks past `exp_end_date`, oldest first, with a days-overdue badge (capped at 20 with a "+" indicator); (3) *Today's tasks with technicians* — open tasks whose expected window **spans** today (a 3-day task shows all 3 days; single-dated edge cases included), each with its assignees (`_assign`) resolved to full names in two bulk queries; (4) *Today's calendar* — public, open Events overlapping today. Open-task filtering matches this site's **customized Task statuses** (`Canceled` one-L, plus `Invoiced`/`Template` excluded — the existing home widget filters on spellings this site doesn't use). Access mirrors the Sales Pipeline model: staff-role gate, then permission-free fetch, so the Employee user-permission cascade can't empty a shared wall display.
- **Block sources, version-controlled** in `Custom HTML Block/task_dashboard.{html,js,css}` (the folder's established export convention, README updated): header with live clock + last-updated stamp; Top-10 rail left, Overdue/Today/Calendar stack right (single column under 900px); priority-tinted task rows; assignee chips ("Unassigned" dashed); empty states. Refresh = the existing `project_dashboard_updated` realtime event (published on every Project/Task save) debounced 5s, plus a 5-minute interval as kiosk fallback, both skipped while the tab is hidden — and because a workspace re-render re-runs block scripts with a fresh `root_element`, all timers and the single realtime subscription live on `window` and are **re-pointed, never stacked**. Styles run inside the block's shadow root and take structural colors from Frappe CSS variables (custom properties pierce shadow boundaries, so Frappe Light and Timeless Night both work without `[data-theme]` selectors); only priority/overdue accents are literal.
- **`seed_task_dashboard_block` patch** — creates the Custom HTML Block from the repo source files on migrate, **insert-only** (UI edits after creation are never clobbered; the repo→UI paste-back workflow from the folder README applies for later code changes). Logs and skips gracefully if the source folder is missing.

### Notes
- One manual step after deploy: edit the target Workspace (Home, or whatever the TV shows) and add the "Task Dashboard" block.
- If this should *replace* the current morning TV screen, point the TV's browser at a workspace containing the block; the old screen (wherever it lives) is untouched.

## [1.3.0] - 2026-06-10

Phase 3 of the Jun 9 Projects/Invoice-processing meeting: the PRO-0204 "Won Opportunity Hand-Off" process engine — "when it hits one step, it automatically sends a notification to who's responsible for the next step… time stamp… auto reminder after 24 hours." The system now holds everyone accountable, step by step.

### Added
- **Process definition as data: new `Process Step Template` doctype** (Project Enhancements; System Manager / Projects Manager editable, Employee read). One record per step: number, title, responsible **role**, optional auto-complete anchor, SLA hours (0 = no escalation), description, and a link to the source Process Document so the encoded process and the official PRO-0204 can't drift silently. The `seed_process_step_templates` patch creates the seven steps — with the meeting's amendments baked into the descriptions (Step 3 customer-data verification first, Step 4 attendees = AE + PM + tech lead, Step 5 exception policy) — **insert-only by step number**, so site-side rewording/SLA tuning/disabling survives every future migrate.
- **Per-project tracker: new `Project Process Step` child table** on a new "Hand-Off Process" tab (fixture fields `custom_process_tab` / `custom_process_progress` / `custom_process_steps`, spliced into the Project field_order; the table is `no_copy` so a duplicated project starts fresh). Each row: step, title, responsible role, status (Pending/Completed/Skipped), completed on/by, due-by, free-text notes (meeting attendees, verifications).
- **The engine** (`process_steps.py`, wired in hooks):
  - *Seeding* — `before_insert` copies enabled templates onto every Project created from an Opportunity; *Opportunity Won* retro-completes from `custom_date_closed_won`, *Project Created* from creation. Per the meeting, in-flight projects are **not** back-filled — the form shows a "Start Hand-Off Process" button instead (whitelisted `start_process`, retro-completing anchors including an already-received payment).
  - *Hand-off notifications* — `after_insert` tells the first pending step's owner their step is up; for a fresh project that is the AR rep ("project number exists — start accounting setup"), exactly the hand-off that kept dropping. Every later completion notifies the new current step's owner (SMS + Notification Log through the v1.1.0 `status_alerts` plumbing); completing the last step posts a "Hand-off process complete" comment on the Project.
  - *Anchors* — ticking **Payment Received** (v1.1.0) now also auto-completes the Step 5 row in the same save (`sync_process_steps` runs after `stamp_payment_received_date` in the `before_save` chain — ordering is load-bearing and commented in hooks.py).
  - *Role resolution, never name-storage* — PM → `Project.custom_project_owner`; AE → source Opportunity's `opportunity_owner` (reverse lookup fallback); AR → new **Hand-Off Process** settings section, `handoff_ar_rep` (Link Employee). Changing the PM mid-project redirects future notifications automatically.
  - *Escalation* — daily scheduler nags the **current** step's owner once it's past `due_by` (set to now + SLA hours when the step becomes current), at most once per day per step (`last_reminded_on`), only on Active projects; later pending steps never escalate (not actionable yet).
- **Progress bar on the Project form** (`public/js/project_enhancements/process_steps.js`, added to Project `doctype_js`): ✓-segments with who/when, the current step highlighted with its due date (red glow when overdue, matching the pipeline lights), a one-click "Mark Step N Complete" button, and the meeting's "signal, not a gate" on the Outline-Tasks step — a live open-task count badge. All writes are click-driven (collab-safe: no field-change handlers). Styles are inline with Frappe CSS variables, so both themes work without a bundle change.
- **Sales Pipeline board: "Hand-off in progress" rail** (v1.2.0 page, extended). Under the funnel columns, every Active project mid-process shows as a chip — "Step N/total · current step title" — overdue ones glowing red and sorted first (cap 14, "+N" overflow). The rail is best-effort by design: any failure logs and returns empty rather than taking down the board.
- Engine test suite (`tests/test_process_steps.py`): seeding with retro anchors (incl. no-opportunity and missing-field inertness), payment-anchor auto-completion, completion stamping, due-date computation, and the transition matrix (completion notifies the new current owner exactly once; the final completion comments instead; inserts and no-op saves are silent).

### Fixed
- **The Opportunity→Project forward link is now actually persisted — it never was.** Both conversion paths were audited while wiring the engine's seeding trigger: the desk path (`opportunity_enhancements.make_project` override) stamped `custom_sales_opportunity`, **a field that does not exist on the site** (no Custom Field record, no column — verified against production) so the value was silently dropped on insert; the background creator (`crm_enhancements.api.create_project_from_opportunity_background`) set no forward link at all, only the reverse `Opportunity.custom_created_project`. Knock-on casualty: `sync_attachments_from_opportunity` (Project `after_save`) keyed on the phantom field, so Opportunity→Project attachment sync only ever worked *within the creation request* and was dead on every subsequent save. Both paths now stamp the real `Project.custom_opportunity` Link field before insert; the attachment sync reads it (it was already idempotent by file name, so per-save re-runs just pick up late additions — and a stale-client fallback to the legacy in-memory attribute is kept); and the one-shot `backfill_project_opportunity_link` patch fills the link for existing projects from the reverse stamp. AE resolution for payment/step alerts no longer depends on the reverse-lookup fallback for new projects.

### Notes
- Behavior-neutral until configured: set **Settings → Hand-Off Process → Accounts Receivable Rep** (Lisa) — without it, AR-step notifications simply have no recipient. Templates are editable under *Process Step Template*.
- Existing projects show the "Start Hand-Off Process" button rather than gaining steps automatically.
- Fixtures changed again this release (~785 records re-import on migrate).

## [1.2.1] - 2026-06-10

### Fixed
- **`twilio` is now a declared dependency** (`pyproject.toml`: `twilio>=8,<10`). `api/telephony.py` imports it at module top (`twilio.request_validator.RequestValidator` for webhook signature validation, `twilio.jwt.access_token` for softphone Voice JWTs), but it was never in the `dependencies` array — it only worked because the production bench happened to have the package installed, and a fresh `bench get-app` install would have raised `ImportError` on every request importing the telephony module. The constraint admits the 8.x/9.x SDKs (the used APIs are stable across both) and guards against a breaking 10.x. An AST audit of every import across the app found no other gaps: `google*` packages are declared, and `requests`/`werkzeug` are provided by frappe itself (bench-managed, deliberately undeclared per the existing `frappe~=16.0.0` comment). Existing benches are unaffected; the declaration only matters for fresh installs and `bench setup requirements`.

## [1.2.0] - 2026-06-10

Phase 2 of the Jun 9 Projects/Invoice-processing meeting: the sales pipeline on a wall TV ("two-three days to build that out… get another Raspberry Pi and another screen").

### Added
- **Sales Pipeline board — new desk page `/app/sales-pipeline`** (`crm_enhancements/page/sales_pipeline/`, module CRM Enhancements). A funnel-column view of open Opportunities whose columns are read from the live `Opportunity.status` meta (a stage rename on the site reshapes the board without a code change), plus two special columns: green **"Won — awaiting project"** (Closed Won with empty `custom_created_project` — the PRO-0204 Step 1→2 gap, on the wall where it can't hide) and a muted, never-stale **On Hold**. Cards show customer, summary one-liner, amount, AE, and a days-in-stage badge; columns show count + $ total (totals always cover the full set; cards cap at 30 per column with a "+N more" footer). Stalest cards sort to the top.
- **Staleness lights ("it lights up if it's been sitting too long").** New Opportunity field `custom_stage_changed_on` (read-only, no_copy, fixtures + field_order splice) is stamped by a `before_save` hook on every entry into a new stage — an edit inside the same stage keeps the clock running. Cards turn **amber** / **red** (red pulses; disabled under `prefers-reduced-motion`) past the thresholds in **ERPNext Enhancements Settings → Sales Pipeline Dashboard** (new Ints, defaults 7/14 days); the won column ages on a tighter hardcoded 1/3-day clock to match the unconverted nag from v1.1.0. One-shot patch `backfill_stage_changed_on` seeds the stamp from `modified` for pre-existing opportunities — and because patches run *before* fixture sync, it creates the Custom Field itself if missing (`is_system_generated=False`, same name) so the fixture adopts the record later in the same migrate.
- **Realtime + kiosk-resilient refresh.** Every Opportunity save publishes `sales_pipeline_updated` (an `on_update` hook, mirroring the Project Dashboard's pattern); open boards refetch behind a 2s debounce, with a 5-minute poll as fallback for wall TVs that miss socket reconnects (both skipped while the tab is hidden or routed away). Footer shows last-updated time and the active thresholds.
- **TV mode.** `/app/sales-pipeline/tv` (the route the Raspberry Pi should bookmark) or the header button: desk chrome hidden, typography scaled for across-the-room reading, fullscreen requested. Styling is theme-aware per the app convention — structural colors from Frappe CSS variables, literal amber/red accents with `[data-theme="dark"]` contrast overrides. Page CSS/JS ship through the Page asset mechanism (served via `getpage`, not the immutable `/assets` cache, so deploys reach the kiosk).
- **Access model** mirrors the Project Dashboard: page-level gate (a `Custom Role` for page `sales-pipeline` wins if configured; otherwise any staff role — deliberately broad for a wall display), then permission-free data fetch so per-user User Permissions can't silently empty a shared board.
- Test suite (`tests/test_sales_pipeline.py`): stale-level matrix (incl. 0-threshold disable), stage-stamp guard (insert / stage change / same-stage edit), live board-shape assertions (column order follows meta, won column placement, card cap + overflow accounting, parked never stale), and permission-denied behavior.

### Notes
- The Opportunity form gains the read-only "Stage Changed On" timestamp under Date Closed Won (the meeting asked for visible timestamps).
- Deploy is behavior-neutral for existing screens; the board only exists at its own route. `bench migrate` re-imports fixtures again this release (~782 records).

## [1.1.0] - 2026-06-10

Phase 1 of the Jun 9 Projects/Invoice-processing meeting: the hand-off automations for PRO-0204 "Won Opportunity Hand-Off" Steps 1 and 5, plus the agreed Opportunity/Lead form standardization. New module `status_alerts.py`; all recipient configuration lives in **ERPNext Enhancements Settings → Status Change SMS Alerts** (new section + child doctype **Status Alert Recipient**), so the team list changes without a deploy.

### Added
- **Closed-Won SMS alerts (PRO-0204 Step 1 "system sends auto-alerts").** When an Opportunity *transitions* into status "Closed Won" (`status_alerts.notify_closed_won`, Opportunity `on_update`, guarded via `get_doc_before_save` so a re-save of a won opportunity never re-sends), every opted-in recipient is texted "WON: {customer} — {summary}" with a deep link to review/convert — so the project number gets created and QuickBooks setup starts without waiting on email. Texts go out through the existing Triton gateway via a new system-level sender (`api.telephony.send_system_sms`: no session Employee, no signature logic, no Communication record — it's an internal alert, not customer correspondence; 15s timeout). Delivery runs in a background job (`enqueue_after_commit`) so a slow/failing gateway can never block or roll back a save; each SMS recipient with a linked User also gets a Notification Log entry as the in-app audit trail, and per-recipient failures are logged and skipped. Recipient numbers resolve from `Employee.cell_number` at send time.
- **Won-but-unconverted daily reminder — closes the Step 1 → Step 2 gap** (the "won it in Vegas, project created a week later" failure). A daily scheduler job (`status_alerts.nag_unconverted_opportunities`) lists Closed Won opportunities whose `custom_created_project` is still empty after `unconverted_nag_hours` (new settings Int, unset → 24, explicit 0 disables) and texts the opted-in recipients a summary (first 3 names + count) with a link to the filtered list. Re-nags daily until converted — deliberate pressure. Day-granular by design (`custom_date_closed_won` is a Date): won today is never nagged, won yesterday is.
- **"Payment Received" on Project (PRO-0204 Step 5), Budget tab.** New fixture fields `custom_payment_received` (Check) + `custom_payment_received_on` (Date, auto-stamped today on tick via `before_save`, adjustable) + `custom_payment_method` (Select: Check / ACH / Credit Card / Cash / Other), all `no_copy` so a duplicated project never claims to be paid; spliced into the `Project-main-field_order` property setter after the QuickBooks Jobcode (new section "Customer Payment"). Ticking the box posts a timeline comment ("financially cleared to proceed") and alerts **the project's PM (`custom_project_owner`) and the Account Executive** (owner of the source Opportunity, via `custom_opportunity` with a `custom_created_project` reverse-lookup fallback) — exactly the two roles PRO-0204 Step 5 names, not a broadcast list. An AE without an Employee record still gets the in-app notification (no SMS). Unticking and re-ticking re-alerts on purpose (a corrected-then-confirmed payment is news).
- **Lead quick capture: name + phone + what they want** (meeting decision: leads are minimal; convert only when real). Lead quick entry was *off* (`quick_entry = 0` on this install — "+ Add Lead" opened the full form); a new `Lead-main-quick_entry` property setter turns the dialog on, and it shows exactly First Name (already required), Phone (new `allow_in_quick_entry` setter), Lead Details (existing custom Text Editor, now quick-entry-enabled with a "what do they want?" description), Lead Source (already required), and Status (pre-filled default).
- **Opportunity form freeze: field definitions shipped as descriptions** (meeting decision: "stop moving the target"). The agreed who-fills-what is now baked into the form: `custom_opportunity_summary` ("one plain-English line… written by the AE"), all four `*_customer_requests` tables ("the customer's ask, in their own words — entered by Sales; don't break it down here") and all four `*_deliverables` tables ("the internal breakdown — translated by PM/design at Step 6; not filled in by Sales"), on **both** Opportunity and Project so the definitions survive conversion. The full guide — including the comments-vs-scope rule and the QuickBooks estimate revision naming convention (duplicate + R1/R2, never edit a sent estimate) — lives in `docs/opportunity-field-guide.md`.
- Guard-logic test suite (`tests/test_status_alerts.py`): closed-won transition matrix (transition / direct-create / re-save / other statuses / migrate-context), payment-received tick semantics (tick → comment + enqueue; re-save silent; re-tick re-alerts), and date stamping.

### Notes
- The alert hooks are inert until recipients are added in settings — deploy is behavior-neutral.
- `bench migrate` will be slower on this deploy: the fixture files changed, so all ~781 customization records re-import (known cost, see fixtures/README.md).

## [1.0.4] - 2026-06-10

### Fixed
- **QuickBooks Online import: group-account conversion no longer fails with `Cannot covert to Group because Account Type is selected.`** (upstream typo). v1.0.3 started setting `is_group = 1` on parent accounts, but ERPNext's `Account.validate_group_or_ledger` refuses a ledger→group conversion while the account's **Account Type** field is set — and the affected accounts had one, both from the pre-existing chart of accounts and from the sync's own mapper. Since group accounts never receive GL postings, the type is informational and safe to drop, so the sync now clears it wherever a conversion happens: `_ensure_group_parent` clears it when promoting a ledger parent, the new `_clear_account_type_for_group_conversion` clears it on the update and auto-link paths (also dropping `account_type` from the recorded QBO-owned fields so the cleared value isn't flagged as a user conflict on the next sync), and `_map_account` no longer assigns an `account_type` to accounts it already knows are groups. Accounts with existing GL entries still refuse conversion (a different, intentional ERPNext guard). Re-run **Import All** / **Retry Failed** after deploying.

## [1.0.3] - 2026-06-10

### Fixed
- **QuickBooks Online import: Customers no longer fail with `Account Type cannot be "Company". It should be one of "Commercial", "Residential", "Partnership"`.** The site customizes `Customer.customer_type`'s Select options via Property Setter (Commercial/Residential/Partnership), but `_map_customer` hardcoded ERPNext's stock values, so every Customer create failed validation. The mapper now resolves the value against the field's *actual* options (`_select_option` in `quickbooks_online/mapping.py`): a QBO customer with a `CompanyName` prefers Company → Commercial, an individual prefers Individual → Residential, falling back to the field's first option. Stock sites keep the old Company/Individual behavior; `supplier_type` got the same treatment for symmetry.
- **QuickBooks Online import: sub-accounts no longer fail with `Parent account <X> can not be a ledger`.** QBO parent accounts (Automobile, Cost of Labor, Job Expenses, Job Materials, Insurance, …) were auto-linked to pre-existing chart-of-accounts rows that are *leaf* accounts — and the link path only fills blank fields, so `is_group` stayed 0 and every child account under them was rejected. Two fixes in `upsert_entity`: the auto-link path now promotes a linked ledger to a group when QBO says the account has children, and `_ensure_group_parent` converts a ledger `parent_account` to a group before any child Account is written under it (covering parents that pre-date the sync entirely). The conversion goes through the Account controller, so parents that already have GL entries still refuse — those genuinely need manual chart restructuring. Re-running **Import All** (or Retry Failed) after deploying picks up all previously failed accounts idempotently.

## [1.0.2] - 2026-06-10

### Fixed
- **The app switcher (and every other module-list consumer) no longer 500s when one app's module cache is poisoned with `None`.** Loading the app list calls CRM's `crm.api.check_app_permission` → `get_modules_from_all_apps_for_user()`, which walks every installed app with `modules_list += get_modules_from_app(app)` (`frappe/utils/modules.py`) — unguarded. `get_modules_from_app` is `@redis_cache`-decorated, and that decorator *intentionally* returns `None` when a `None` result was previously cached for a key (`frappe/utils/caching.py`, the "Edge Case: None can mean cache miss or the result itself is None" branch). So a single transient empty/failed `Module Def` query — observed for the **`telephony`** app — gets cached as `None`, and for the rest of that entry's TTL every `list += None` raises `TypeError: 'NoneType' object is not iterable`, taking down the app switcher, Dashboard / Dashboard Chart / Number Card, and the User / Module Profile forms. The function itself can't produce `None` (its body is `frappe.get_all(...)`, always a list) — the `None` comes purely from the cache layer. Rather than edit `apps/frappe` (overwritten on `bench update`), the fix is a runtime monkeypatch carried in app code (new `monkeypatches.py`, applied once per worker from the bottom of `hooks.py` — where Frappe imports every app's hooks): `get_modules_from_app` is wrapped to coerce `None → []`, equivalent to the upstream `get_modules_from_app(app) or []` guard but covering every caller, not just the one loop. The patch is idempotent and self-guarding (a failure logs and is skipped, never breaking hook loading), and is covered by `tests/test_monkeypatches.py`, which reproduces the original crash and asserts it's gone. Immediate recovery on an already-affected site is `bench --site <site> clear-cache`, which evicts the poisoned entry.

## [1.0.1] - 2026-06-10

### Fixed
- **Buttons inside HTML-field widgets work again on collab-enabled doctypes — the Comments App "New Note" button (Project, Task, …) and the Project Gantt toolbar (Today / zoom) were both broken.** Collab's per-field presence broke them: those widgets mount inside HTML fields (`custom_comments_field`, `custom_gantt_chart_html`), so clicking any of their buttons fired a `focusin` that `live_form_sync.js` broadcast as "editing" that host field — and the relay's strict validation threw **"Invalid field"** (HTML is a no-value fieldtype), popping an error modal over whatever the click was doing (HTTP 417 + the `aria-hidden`-on-focused-modal console warning; the modal is what killed the opening New Note dialog). Fixed generically on both ends, so every widget hosted in an HTML field (task tree, unified contacts/addresses tabs, …) is covered: the client no longer broadcasts focus for non-value fieldtypes (HTML, Button, Table wrappers — `_resolve_focus_target` now checks `frappe.model.no_value_type`, same rule as field sync), and `broadcast_focus` now **silently drops** invalid field targets instead of throwing — presence is best-effort by design, and stale cached clients must not be able to trigger user-facing modals. Allowlist and write-permission checks still throw, and `broadcast_field_update` keeps its strict validation.
- **Task tree styles no longer 404.** `task_tree_manager.js` still `frappe.require`d `/assets/erpnext_enhancements/css/task_tree.css` — a path that hasn't existed since the styles moved into `desk_addons.bundle.css` (the file lives under `css/project_enhancements/`), so every Project form logged a refused-stylesheet MIME error and the on-demand load contributed nothing. The dead require is removed; the bundle already ships the styles globally.

## [1.0.0] - 2026-06-10

Live collaborative editing — the headline feature that earns the 1.0. Two or more people can now work on the same document like a Google Doc: every field change streams to everyone viewing it, saves apply on collaborators' screens silently, and you can see exactly which field each person is editing.

### Added
- **Live collaborative form editing**, configured per-doctype in **ERPNext Enhancements Settings** (new "Live Collaborative Editing" section: `collab_enabled` master switch + `collab_doctypes` allowlist child table, new child doctype **Collab Doctype**) — doctypes can be toggled **without a deploy**; the list ships to clients in bootinfo (`extend_bootinfo` → `boot.boot_session` → `frappe.boot.collab_doctypes`) and the server relay re-reads settings as the security authority on every broadcast. The `seed_collab_doctypes` patch seeds the launch allowlist and switches the feature on, so the deploy is behavior-neutral. Launch list = the ten most-used doctypes (chosen from live `tabVersion` data, 180 days, by edit volume + multi-editor activity): **Task, Project, Opportunity, Customer, Contact, Address, Item, Supplier, Purchase Order** (drafts only — the engine never attaches to submitted docs), **ToDo**. Built on Frappe's own realtime plumbing (socket.io + Redis doc rooms, permission-checked room membership): a client engine (`public/js/collab/live_form_sync.js`, shipped in `erpnext_enhancements.bundle.js`) observes local field changes via a wildcard model observer, debounces 300ms per field, and POSTs them to a server relay (`api/collab.py` — doctype allowlist, **write**-permission check per broadcast, field validation, 140KB value cap) which re-publishes to the document's room; receivers apply values behind an origin/echo guard. Conflict model is last-write-wins per field, with one guarantee: a field you are actively typing in is never clobbered mid-keystroke — remote values for it are parked and applied on blur only if you didn't type something newer. Child tables sync cell edits on saved rows live; row add/remove propagates at the next save. The relay never writes to the database — persistence happens only through normal saves.
- **Seamless saves between collaborators.** When anyone saves, other viewers' forms silently adopt the saved state and the new `modified` timestamp (a passive "Updated by …" toast is the only signal), keeping their own unsaved edits layered on top — so the "Document has been modified after you have opened it" error (`TimestampMismatchError`) can no longer happen between people using collab-enabled forms. Frappe's conflict banner is suppressed for those forms only (guarded prototype patch on `show_conflict_message`); everything else keeps stock behavior, including the built-in silent reload of clean forms.
- **"Jane is editing this field" highlights.** Focusing a field broadcasts a presence event (`api.collab.broadcast_focus` → `collab_focus`); collaborators see the field outlined in the editor's color (deterministic per-user palette, same color on every screen) with a name badge — including individual grid cells. Presence is best-effort by design: a 30s heartbeat re-asserts a held focus and receivers expire highlights after a 75s TTL, so a crashed tab or dropped connection self-heals without a stale "ghost editor". Styles ship in `public/css/collab.css` via `desk_addons.bundle.scss` and are **theme-aware**: JS assigns only a palette class (`.ee-collab-color-{0..5}`), the colors live in CSS with `[data-theme="dark"]` overrides (deeper shades on light, brighter on dark, badge text flipped for contrast), so highlights adapt live when the desk theme switches.
- Relay test suite (`tests/test_collab.py`): allowlist, permission, field/child-table validation, size cap, and publish-payload assertions for both endpoints.

### Changed
- **Opportunity tag sync hardened for collab** (`public/js/crm_enhancements/opportunity.js`): the branch that persists `_user_tags` directly via API now explicitly skips when a change was applied by the live-sync engine, so only the originating client performs the write. (It was already effectively unreachable on receivers — remote applies mark the form dirty first — this removes the reliance on that ordering.)
- Audited every enabled doctype's field-level form-script handlers for non-idempotent side effects (they re-fire on receiving clients when remote values are applied): the unified contacts/addresses controller's field handlers are read-only re-renders (all writes live behind button clicks, which remote changes cannot trigger); no other risky handlers found. The audit checklist lives next to the `COLLAB_DOCTYPES` constants for future doctype onboarding.

## [0.9.0] - 2026-06-10

Deploy-staleness audit, kiosk edition. Verified first that the **server and desk need no new cache plumbing**: Frappe 16's `bench migrate` (which every deploy runs — it's also what re-applies the fixtures) starts with `frappe.clear_cache()` (full Redis flush + `metadata_version` bump, which makes desk clients drop their localStorage doctype/asset caches on next boot) and ends with `clear_website_cache()` plus a realtime `version-update` event that pops a non-dismissable "Version Updated — please refresh" dialog on every connected desk tab; combined with 0.8.1's content-hashed bundles, that closes the desk/server side. The one place deploys still did **not** reach was the installed Time Kiosk PWA — fixed below.

### Fixed
- **`bench build` no longer fails on `desk_addons` — the v0.8.1 Frappe Cloud deploy was broken.** The deploy image build died with five `ENOENT` errors resolving `desk_addons.bundle.css`'s relative `@import`s under a `/tmp/tmp-…` path. Root cause: frappe's esbuild runs every style entry through `@frappe/esbuild-plugin-postcss2`, which writes the postcss output to a **temp directory** and hands esbuild that path — for a plain `.css` entry the plugin leaves `@import` statements untouched (it uses no `postcss-import`), so esbuild resolves them relative to the temp copy, where the five sibling files don't exist. A `.scss` entry doesn't have this problem because the plugin compiles it with sass *against the original path* — sass inlines the imports itself before the temp hop — which is exactly why frappe core's own multi-file bundles are all `*.bundle.scss`. The entry is now `desk_addons.bundle.scss` with extension-less imports (sass inlines an extension-less import of a `.css` file; with the `.css` extension it would pass through as a runtime `@import` and 404). Verified with dart-sass: all five stylesheets inline, no `@import` survives in the output. The built asset name is unchanged (`desk_addons.bundle.css` via assets.json), so `hooks.py` and the cascade order are untouched. `desk_enhancements.bundle.css` / `login_enhancements.bundle.css` are unaffected — they carry their rules directly and import nothing.
- **The Time Kiosk PWA now picks up every deploy automatically — no more hand-bumped `time-kiosk-vN`, no more year-stale assets on employees' phones.** Two staleness layers were stacked on the kiosk: (1) `kiosk.html` loaded `kiosk.css` / `geo.js` / `app.js` as raw `/assets` URLs, served with `Cache-Control: max-age=31536000, immutable` — the same landmine 0.8.1 fixed for desk includes, but the kiosk can't use desk bundles (it's loaded by a web page, not hooks); (2) the service worker served those assets cache-first from a cache that only rotated when somebody remembered to bump `CACHE = 'time-kiosk-v3'` — and even a bump re-precached *through* the HTTP cache, so it could re-store the very stale bytes it was meant to evict. Now: `kiosk.py` exposes a deploy token (mtime of `sites/assets/assets.json`, rewritten by every `bench build`; falls back to the app version — deliberately not frappe's random-string fallback, which would re-bust on every page view), `kiosk.html` appends it as `?v=` to every mutable asset URL (icons stay unversioned — content never changes), `app.js` registers the worker as `/kiosk-sw.js?v=<token>`, and the worker derives its cache name from its own registration query. A deploy is therefore a new worker URL: it installs immediately (`skipWaiting`), precaches with `cache: 'reload'` (bypassing the immutable HTTP cache), and its `activate` deletes every other cache. Asset matching uses `ignoreSearch` — safe, the cache only ever holds the current deploy's entries. And because installed PWAs can stay open for days while browsers only check for a new worker on navigation, `app.js` now calls `registration.update()` on every return to the foreground and hourly, then reloads the page once when the updated worker takes control — deferred until the app is hidden, so a half-typed note is never eaten. Already-installed clients converge on their next online launch: the byte-changed worker at the old unversioned URL installs and precaches fresh, and the network-first `/kiosk` navigation delivers the new shell, which re-registers at the versioned URL.

### Added
- **CI now hard-gates version sync between `__init__.py` and `package.json`.** release.yml has always refused to tag when the two disagree, but it only runs *after* a merge to main — which is exactly how this release was initially blocked: the 0.9.0 bump touched only `__init__.py`, the post-merge Release run failed with "Version drift", and no v0.9.0 tag was cut until `package.json` was bumped in a follow-up. The same comparison now runs in ci.yml on every push/PR, so a half-finished bump fails before merge. (Note: release.yml triggers on pushes that touch `__init__.py`; a sync-fix merge that only touches `package.json` needs a manual `workflow_dispatch` of the Release workflow.)

- **Back / forward / refresh navigation in the installed kiosk.** The manifest now declares `display_override: ["minimal-ui"]`, so Chromium installs (Android, desktop) get the browser's own back/forward/reload chrome. Platforms that don't support it (iOS) keep running `standalone`; there `app.js` shows an in-app ‹ › ⟳ bar (`history.back()`/`forward()` + `location.reload()`), which is the only way back after following e.g. the "View My History" link onto the desk. The bar renders only when the app is actually chrome-less (`display-mode: standalone`/`fullscreen`, or iOS `navigator.standalone`); in a normal browser tab — or when the browser honors minimal-ui — it stays hidden. Note for already-installed Android apps: Chrome applies manifest changes on one of its periodic manifest re-checks (typically within a day of launches); a reinstall applies it immediately.

## [0.8.1] - 2026-06-09

### Fixed
- **Kanban hold-to-drag now actually reaches phones and tablets.** The press-and-hold patch suite shipped as raw `/assets/erpnext_enhancements/js/kanban_*.js` scripts, and the server serves `/assets` with `Cache-Control: max-age=31536000, immutable` (verified on the live site) — so a mobile browser keeps executing the *first* copy of each file it ever downloaded, for up to a year, without revalidating even on a normal reload. Desktops used for testing get hard-refreshed; phones never do, which is exactly why the 1-second hold worked with a mouse while touch devices kept grabbing cards instantly when scrolling. The four `kanban_*.js` patches now ship as a single esbuild bundle (`public/js/kanban.bundle.js`, referenced as `kanban.bundle.js` in `app_include_js`, same mechanism as `desk_enhancements.bundle.css`): the built filename carries a content hash, so every deploy gets a new URL and every device — including the stale phones — picks up the current code on its next page load. This was very likely also the root cause of the earlier "the delay never seems to apply" round documented in `kanban_patches.js`. Code-wise nothing else moved: the bundle just imports the four files in their old include order.
- **Backported SortableJS 1.15.4's `pointercancel` handling into `kanban_patches.js`.** Frappe pins SortableJS 1.15.0, whose delay branch cancels a pending hold on `touchend`/`touchcancel`/`mouseup` and on >threshold movement — but never listens for `pointercancel`. Phones are covered (touch events fire alongside pointer events), but on pointer-only inputs (pen/stylus, some Windows-touch configurations) the browser fires *only* `pointercancel` when it claims the gesture for native scrolling, so the pending 1s timer survived the scroll takeover and could fire mid-scroll, grabbing a card nobody was pressing. A document-level `pointercancel` listener now aborts any pending delayed drag (guarded so it never touches a drag that already legitimately started).
- **Every other global desk include migrated to content-hashed bundles too** — the same stale-cache landmine applied to all of them (Comments App, Triton widget, telephony, sidebar/awesomebar/drafts patches, activity numbering, filter help, task tree/gantt preloads, and the five feature stylesheets). New esbuild entries `public/js/erpnext_enhancements.bundle.js` (17 scripts, old include order) and `public/css/desk_addons.bundle.css` (5 stylesheets, listed after `desk_enhancements.bundle.css` so the cascade is unchanged) replace all raw `app_include_js`/`app_include_css` paths. Audited first: every bundled file is a self-contained IIFE or exposes itself via explicit `frappe.provide`/`window` assignment; the two files with top-level declarations (`erpnext_enhancements.js`, `activity_log_numbering.js`) have no external consumers of those identifiers. The two vendored UMD libraries (`vue.global.js`, `frappe-gantt.umd.js`) deliberately stay raw, now loaded first: a bundle import would capture their exports instead of setting `window.Vue`/`window.Gantt`, and their content never changes so stale caching cannot affect them. `doctype_js` files are untouched (`frappe.require` has a version-aware client cache).
- **Login page no longer requests a non-existent stylesheet.** `web_include_css` pointed at `/assets/erpnext_enhancements/css/login_enhancements.css`, which 404s — the on-disk file is `login_enhancements.bundle.css` (confirmed live: the login page requested both the 404 path and the built bundle). The hook now references the bundle name.

### Removed
- **The blocking "You have unsaved changes. Are you sure you want to leave?" navigation guard.** Its worst false positive was the most common flow there is: saving a *new* document — frappe routes from `new-…` to the real name while the dirty flag is still being cleared, so the dialog fired right after the user pressed Save. It also fired for forms that scripts dirty programmatically (several of our migrated form scripts set fields on load), and its three monkey-patches were fragile: the `router.render` interception had a documented URL/history desync when choosing "stay", and the patched `frappe.set_route` returned a rejected Promise that core callers never handle. Meanwhile it protected little: navigating away in-app keeps the dirty doc in `locals` for the session (going back shows it still dirty), and the "Safe Auto-Save" draft system — which stays — already snapshots every edit of an existing doc to localStorage + server on each change and offers a Restore banner when the doc is reopened. Navigation away from a dirty form is now silent, stock-Frappe behaviour, with the draft cache as the safety net. Known gap (pre-existing): drafts skip brand-new unsaved documents (`frm.is_new()`), so a hard refresh mid-compose of a new doc still loses it — extending drafts to new docs is the right follow-up, not a blocking dialog.

## [0.8.0] - 2026-06-09

### Added
- **The last load-bearing DB-only custom DocTypes are now app DocTypes**: `Process Document` (Mermaid.js process docs, 11 documents, form script already shipped in `public/js/process_document.js`) → Enhancements Core; `Sales Activity Settings` (Single) → CRM Enhancements; `Additional Supplier Group` (child table behind `Supplier.custom_additional_supplier_groups`) → Global Enhancements. Generated with frappe's canonical export serializer from the live definitions (only `custom`/`module`/`modified` differ), same as the v0.7.0 port.

### Changed
- **`customer_inactivity_reminder` now has a global fallback.** Customers without a positive per-customer `custom_reminder_days` fall back to the `inactivity_threshold` from the now-shipped Sales Activity Settings Single (live value: 90 days); `custom_reminder_days = -1` opts a customer out, and setting the global threshold to 0 disables the fallback site-wide. Previously such customers were skipped entirely. **Measured against live data, the first daily run after deploy creates ~694 follow-up ToDos** (the backlog of long-inactive customers — 286 owned by Administrator, 212 brian.morisseau, 183 nikolas.bradshaw, the rest spread thin); warn those owners, set the global threshold to 0 until ready, or prune by owner afterwards. (The old DB Server Script "Customer Inactivity Notification" that read this Single is already disabled; app code is now its only consumer.)

### Fixed
- **Follow-up ToDos are now actually assigned.** The ported reminder (and the original server script before it) set `assigned_to`, a field that does not exist on Frappe v16's ToDo — the key was silently dropped, leaving every follow-up ToDo it ever created unassigned and invisible in assignees' lists (confirmed on live: existing Open customer ToDos all have `allocated_to = NULL`). The insert now sets `allocated_to` (the Customer's owner).
- **`setup/supplier_groups.py` no longer creates the "Additional Supplier Group" DocType at runtime** — it ships with the app and is synced by doctype sync before the `after_migrate` hook runs.

### Removed
- **Three abandoned DB-only DocTypes are deleted by patch `delete_abandoned_doctypes`** (sign-off: Nikolas, 2026-06-09): `Materials` (0 rows), `Rental Status` (0 rows), `Water Feature Types` (1 orphan row; superseded by the Serial No migration). All three were referenced by nothing — no DocField, Custom Field, script, or repo code. The patch also deletes the disabled "Mermaid.js Render" Client Script, superseded by the app's Process Document form script. **Deleting a DocType drops its table.**

## [0.7.0] - 2026-06-09

### Added
- **17 DB-only custom DocTypes ported into the app** (closing the fresh-install gap documented in `fixtures/README.md`): the 16 child tables referenced by `fixtures/custom_field.json` Link/Table fields (Accounts Lead/Opportunity/Project, Lead Source, Opportunity Contributor, Value Stream, Project Notes, Project Stakeholder, and the Build/Design/Rent/Service Customer Requests + Deliverables tables) plus the transitively required **Value Streams** master. Each now lives as a standard app DocType under `crm_enhancements/doctype/` (7, from the live `CRM` module) or `project_enhancements/doctype/` (10, from `Projects`), generated with frappe's own canonical export serializer from the live definitions — only `custom` (removed), `module` (remapped) and `modified` (stamped) differ. Because app doctype sync runs **before** fixture sync on migrate and fresh install, `custom_field.json` now imports cleanly on a from-scratch site; previously frappe skipped the entire 425-record file at the first missing Link target.
### Changed
- On the live site, the first migrate of this release flips the 17 from `custom = 1` to app-owned — verified non-destructive against the deployed Frappe 16.20.0 source (the reload path deletes only doctype metadata rows, never the data tables; row counts such as Value Stream's 1310 are untouched). Their definitions are henceforth edited in the repo (UI editing of standard DocTypes requires developer mode), extending the repo-as-source-of-truth model from Phase 2 to the DocTypes themselves. Any UI edit made to these 17 DocTypes between this export and the deploy is overwritten by the repo definitions — deploy promptly.
- **Lead Source**: this ERPNext v16 install no longer ships its historical `Lead Source` doctype (verified: no `erpnext.crm.doctype.lead_source` module on the bench, `Lead.source` absent), so the port is collision-free; if a future ERPNext upgrade reintroduces it, ours must be renamed first. The live record is a one-field husk referenced only by `Customer-custom_lead_source` — ported faithfully, cleanup is a separate decision.

## [0.6.0] - 2026-06-09

### Changed
- **The repo is now the source of truth for all manual customizations (Phase 2).** `fixtures/custom_field.json` and `fixtures/property_setter.json` now carry every manually created Custom Field (425) and Property Setter (349) from the live site — re-exported fresh today and verified byte-identical to the Phase 1 snapshot — and the `fixtures` hook exports/syncs everything with `is_system_generated = 0` minus six records owned by other apps (see `fixtures/README.md`, the new authoritative spec). On deploy, `bench migrate` re-applies these files; Customize Form changes on the site no longer survive fixture-touching deploys. **Back up the DB before the first deploy of this release** (the first sync re-writes values identical to what the site already holds, so it is expected to be a functional no-op).
- **`create_comments_tab` (after_migrate) is now insert-only.** It previously rewrote the fixture-owned Project Comments tab/field with `update=True` and a recomputed `insert_after` on every migrate — running *after* fixture sync, it would have silently overridden any future fixture edit. It now only creates missing fields (fresh installs, Master Project).

### Removed
- **`crm_enhancements/custom/opportunity.json` and `project_enhancements/custom/project.json`** (`sync_on_migrate` customization channels). All 16 of their live-matching records are now fixture-owned; the 17th, `Project-total_expense_claim`, was a frozen 2025 copy of an HRMS-owned field (`is_system_generated = 1`) that the file re-imposed on every migrate — ownership returns to HRMS. These files synced *after* fixtures in the migrate pipeline and would have masked fixture edits.
- **The dead `custom_fields` dict in hooks.py.** Frappe core never reads an app-level `custom_fields` hook and no consumer exists in this repo; its `Project-custom_drive_folder_id` definition had silently drifted from the live record (which the fixtures now own). Corrected the false provenance claim in `crm_enhancements/README.md`.
- **`project_enhancements/setup_address.py`** (manual `bench execute` installer). Its definitions for the two Address map fields had drifted from live, and a re-run would have inserted an orphan `custom_map_section` that exists in no fixture. The fixtures own the real records.
- **`customizations_snapshot/`** (Phase 1). Superseded: the fixture files now carry the same content, and the snapshot README's spec moved to `fixtures/README.md`.

## [0.5.0] - 2026-06-09

### Added
- **Version-controlled snapshot of all manual Customize Form customizations** (`customizations_snapshot/`). All 425 manually created Custom Fields and 349 Property Setters were exported from the live site (read-only, via MCP) and committed as record-keeping JSON — Phase 1 of moving customizations into version control. The directory sits outside the app package's `fixtures/`, so **nothing is imported or applied on migrate**; promoting it to enforced fixtures is a deliberate Phase 2 change. Six records flagged "manual" on the site but actually owned by installed apps (3× `lms` User fields, 1× `frappe_assistant_core` User field, the LMS Certificate default print format, and the workflow engine's auto-created `workflow_state` field) are deliberately excluded — see `customizations_snapshot/README.md` for the audit trail.

### Fixed (documented, not yet applied)
- The audit found `erpnext_enhancements/fixtures/custom_field.json` has drifted from the live site (7 records, mostly comments-tab `insert_after` positions) and wrongly includes the HRMS-owned `Project-total_expense_claim` via its broad `dt = Project` filter. Both are documented in the snapshot README as Phase 2 work; no fixture behavior changes in this release.

## [0.4.0] - 2026-06-09

### Added
- **Full dark-mode ("Timeless Night") support across all customizations.** Every customization now tracks the active Frappe v16 desk theme — **Frappe Light** and **Timeless Night** — instead of assuming a light background. Detection follows Frappe's own mechanism: the *resolved* theme published on `<html data-theme="light|dark">`, so the user's "Automatic" preference is handled for free. CSS keys off `[data-theme="dark"]`; JavaScript reads `document.documentElement.getAttribute('data-theme')` only where a resolved colour string is actually required. Hardcoded colours were replaced with Frappe desk variables (`--card-bg`, `--bg-color`, `--control-bg`, `--subtle-fg`, `--fg-hover-color`, `--text-color`, `--text-muted`, `--border-color`, `--primary`, `--popover-bg`) that auto-switch between themes. Saturated semantic/status colours (success/danger/warning, value-stream and gantt data-viz palettes) and the print/portal templates were intentionally left literal.

### Fixed
- **Stylesheets converted to theme variables.** [`task_enhancements.css`](erpnext_enhancements/public/css/task_enhancements/task_enhancements.css), [`task_tree.css`](erpnext_enhancements/public/css/project_enhancements/task_tree.css), and the Custom HTML Block [`projects_dashboard.css`](Custom%20HTML%20Block/projects_dashboard.css) were fully converted from hardcoded `#fff`/`#333`/`#ddd` surfaces, text and borders to Frappe desk variables. The dashboard's local frappe-gantt palette (`--g-*`) gained an `html[data-theme="dark"]` override mirroring the vendored gantt stylesheet, and its hardcoded SVG text fills now use the themed `--g-*` variables.
- **JavaScript-injected styles** in 15 desk scripts now use theme variables — Portfolio Gantt popups, Project gantt/heatmap/dependency-link styles, the file-manager/file-preview tiles, filter-help mock inputs, the comments UI, contacts/addresses tables, and the column-selector dropdown. The Project Brief follows the theme on screen but keeps an `@media print` block so printed briefs stay dark-on-white. Canvas/image exports (`domtoimage`) resolve `--card-bg` via `getComputedStyle`, which cannot parse `var()`.
- **Server-rendered HTML** now emits theme variables: the Opportunity→Project notes block ([`crm_enhancements/api.py`](erpnext_enhancements/crm_enhancements/api.py)) and the Task hierarchy `<style>` block ([`task.py`](erpnext_enhancements/task_enhancements/doctype/task/task.py)).
- **The Projects Dashboard shell** ([`projects_dashboard.html`](Custom%20HTML%20Block/projects_dashboard.html)) dropped fixed-light Bootstrap utilities (`bg-white`/`bg-light`/`btn-white`) that glared in dark mode, in favour of theme-aware surfaces and `btn-default`.
- **Dark-mode contrast bugs in already-themed files.** The Triton assistant's mermaid diagram box (a white panel inside the dark chat), the high-value Opportunity kanban card (deep-navy card with no edge against the dark desk), and three Time-Kiosk surfaces (outline button, badge, inactive tracking dot) now have proper dark-theme treatments.

## [0.3.4] - 2026-06-09

### Changed
- **Time Kiosk PWA icon/favicon is now the Sapphire Swirl.** Replaced the placeholder clock glyph with the Sapphire Swirl brand mark — the `#00a0dd` swirl on a transparent field — for the standard icon and favicon, and regenerated the 192/512 PNG raster versions. The maskable icon keeps the swirl on a solid white field, since launchers clip maskable icons to a circle/squircle and require an opaque full-bleed background. Updated the PWA `theme_color` to `#00a0dd` and added explicit `<link rel="icon">` favicon tags (SVG + PNG) to the kiosk shell. Bumped the service-worker cache to `time-kiosk-v3` so installed clients fetch the new icons. (`erpnext_enhancements/public/kiosk/icons/*`, `www/kiosk-manifest.json`, `www/kiosk.html`, `www/kiosk-sw.js`)

## [0.3.3] - 2026-06-09

### Fixed
- **Task tree "Assigned To" column is now clickable.** Clicking the assignee link on a task row in the Project form's Scope tab (and the Project Dashboard Tasks tree view) previously did nothing. It now opens an assignment dialog listing current assignees with remove buttons and a User picker to add new ones, wired to the existing `add_task_assignee` / `remove_task_assignee` backend methods. Disabled in read-only mode. (`erpnext_enhancements/public/js/project_enhancements/task_tree_manager.js`)

## [0.3.2] - 2026-06-09

### Added
- **Project-wide documentation.** Added module/function docstrings, JSDoc header blocks, and inline comments across the codebase (200 source files: Python, JavaScript, CSS, HTML) — comments only, no executable code changed. Verified every changed `.py` compiles (`py_compile`) and every changed `.js` passes `node --check`.
- **README files for every subsystem.** Rewrote the top-level [`README.md`](README.md) (architecture overview, 8-module map, annotated `hooks.py` reference, external-integration matrix, dev workflow, conventions, documentation index) and added a `README.md` to each module and cross-cutting folder: `api/`, `project_enhancements/`, `crm_enhancements/`, `quickbooks_time_integration/`, `sapphire_maintenance/`, `enhancements_core/`, `travel_management/`, `task_enhancements/`, `global_enhancements/`, `script_migrations/`, `patches/`, `public/`, `www/`, `tests/`, and `Custom HTML Block/`. Detailed GA4 dashboard setup moved into the Enhancements Core README.

## [0.3.1] - 2026-06-08

### Changed
- **Kanban "press-and-hold to move a card" now applies to mouse *and* touch — and reliably.** A card only starts dragging after a deliberate **1-second press-and-hold** (finger or mouse); a quick swipe scrolls the board sideways or a column up/down, and a quick tap still opens the card. This stops accidental card moves while scrolling, especially on mobile. (`erpnext_enhancements/public/js/kanban_patches.js`)
  - Set SortableJS `delayOnTouchOnly: false` so the 1-second hold gates the **mouse** too. Previously it was touch-only, so a mouse dragged a card instantly.
  - Rebuilt **how** the delay is applied. The old version scanned for SortableJS instances on a fixed `[0, 150, 400, 1000]ms` timeline after `KanbanView.render()`. On the heavy Opportunity board, Vue/SortableJS finish mounting *after* that 1-second window closes, so the scan found nothing — and `kanban_leak_fix.js` short-circuits `render()` on filter refreshes, so the scan was never re-scheduled. Net effect on the live board: the delay never applied and cards grabbed instantly. The patch is now **decoupled from `render()`**: a document-level `MutationObserver` watches for Kanban *container* insertions (board / columns / card-lists, not individual cards) and, debounced, recovers every live SortableJS instance to set `delay` / `delayOnTouchOnly` / `touchStartThreshold`, with a short bounded startup poll as a fallback. Idempotent per instance, and board-agnostic (Task, Opportunity, …).

### Removed
- **Opportunity-board drag lock.** Card dragging on the Opportunity board was fully disabled by `disable_kanban_drag.js` (blocked native `dragstart`) and a `pointer-events: none` rule on `.kanban-card` in `horizontal_scroll.css`. Both only blocked the **mouse** — SortableJS handles touch separately, so on mobile Opportunity cards still moved by accident. Replaced with the unified 1-second hold-to-move above, so cards are movable again but guarded. Deleted `erpnext_enhancements/public/js/global_enhancements/disable_kanban_drag.js`, removed its `doctype_js` hook entry, and dropped the `pointer-events` rules from `horizontal_scroll.css` (the horizontal-scroll layout rules are kept).

## [0.3.0] - 2026-06-08

### Changed
- **Legacy desk Time Kiosk page now redirects to `/kiosk`**: the in-desk `time-kiosk` Page (`/app/time-kiosk`, legacy `/desk/time-kiosk`) previously rendered an older copy of the kiosk UI (jQuery + `geo_worker.js`). Its `on_page_load`/`on_page_show` now simply `window.location.replace('/kiosk')`, so old bookmarks land on the standalone PWA instead of the retired UI. The Page record is retained purely as the redirect target.

### Added
- **`/kiosk` requests location permission on visit**: previously the browser permission prompt only fired on clock-in (the first call to `watchPosition`/`getCurrentPosition`), so visiting the page never asked. Added `KioskGeo.warmup()`, called on page load when tracking is enabled, which surfaces the permission prompt up front (skipping it when the Permissions API reports the choice is already made) and reflects the result in the tracking indicator with a new "Location ready" (solid green) state. No location is logged until the user is clocked in.

### Removed
- **Dead assets from the retired in-desk kiosk**: deleted `public/js/geo_worker.js` (the old desk kiosk's geolocation Web Worker — superseded by `public/js/kiosk/geo.js` + `www/kiosk-sw.js`) and `public/css/time-kiosk.bundle.css` (styled the old desk DOM only: `#tk-current-time`, `#timer-text`, `.btn-lg`, etc. — none of which exist in the new PWA, whose styles live in `css/kiosk/kiosk.css`). Removed the now-stale `time-kiosk.bundle.css` `<link>` from `www/kiosk.html` and its entry from the service-worker precache list.

### Fixed
- **Time Kiosk not installable as a PWA**: `kiosk-manifest.json` listed only SVG icons (`sizes: "any"`). Chrome/Edge require at least one raster PNG icon at 192×192 and one at 512×512 to satisfy their installability criteria, so the "Install app" prompt never appeared. Added PNG icons (`kiosk-icon-192.png`, `kiosk-icon-512.png`, and a maskable `kiosk-maskable-512.png`, rendered to match the existing clock glyph) and listed them first in the manifest; the SVGs are retained as supplementary entries. The `apple-touch-icon` now points at the 192px PNG (iOS ignores SVG touch icons). Bumped the service-worker cache to `time-kiosk-v2` and precached the new icons so existing installs pick up the change.
- **Kiosk clock unreadable in dark mode**: `kiosk.css` switched to its dark palette only via `@media (prefers-color-scheme: dark)`, flipping the text to near-white, while the page body background was pinned to Frappe's light `--bg-color`. The result was light text on a light body (the top wall-clock was nearly invisible). The body background now follows the kiosk's own `--tk-bg`, the dark palette also responds to Frappe's `[data-theme="dark"]` attribute, and the clock/timer have explicit `--tk-text` colors — keeping background and text contrast in sync in every light/dark combination.

## [0.2.9] - 2026-06-08

### Removed
- **Frappe integration-test CI job**: Removed the `integration-tests` job (real bench + ERPNext + `bench run-tests --app erpnext_enhancements`) from `.github/workflows/ci.yml`. On the version-16 toolchain it never reached this app's own assertions — it aborted inside Frappe's test-record auto-generation, which walks the entire ERPNext doctype dependency graph and tripped over a cascade of environment gaps (missing `frappe.utils` helpers, custom fields absent on bootstrap-created Contacts, and uninstalled companion doctypes like `Payment Gateway`). Each fix only exposed the next, so the job gated PRs on upstream/environment churn unrelated to the app's code. CI now relies on the standalone `unit-tests` job. The Frappe-dependent test files under `erpnext_enhancements/` are left in the tree and can still be run against a real bench locally; a CI job can be reintroduced once the upstream harness stabilises. The defensive code fixes made while chasing these failures (`add_to_date`, `getattr`-guarded Contact custom-field reads, `has_column` guard in `sync_from_contact`) are retained as genuine robustness improvements.

## [0.2.8] - 2026-06-08

### Fixed
- **Opportunity save crash `AttributeError: 'Opportunity' object has no attribute 'lead'`**: the migrated `update_lead_status` `before_save` hook (`script_migrations/opportunity.py`) guarded on `doc.lead`, but the Opportunity doctype has no `lead` field — the Lead is referenced via `party_name` when `opportunity_from == "Lead"`. Saving *any* Opportunity (including ones created from a Customer, as in the report) raised the error and blocked the save. The guard now checks `doc.opportunity_from == "Lead" and doc.party_name`, and resolves the Lead via `party_name`.
- **CI: install the `payments` app so test-record generation resolves `Payment Gateway`**: `bench run-tests` aborted with `DoesNotExistError: DocType Payment Gateway not found` during Frappe's test-record dependency walk. ERPNext ships doctypes that Link to `Payment Gateway` (e.g. `Payment Gateway Account`), but in version-16 that doctype lives in the separate `frappe/payments` app and is **not** listed in ERPNext's `required_apps`, so `bench get-app erpnext --resolve-deps` never fetched it. The integration-test job now `bench get-app payments --branch "$FRAPPE_BRANCH"` and `--install-app payments` (between erpnext and erpnext_enhancements), providing the missing doctype so the dependency walker completes.

## [0.2.7] - 2026-06-08

### Fixed
- **Contact sync "Unknown column 'primary_contact'" on fresh DBs**: `sync_from_contact` looped over `PRIMARY_CONTACT_DOCTYPES` (`Project`, `Opportunity`, `Supplier`, `Customer`) and ran `frappe.get_all(dt, filters={"primary_contact": ...})`. `primary_contact` is a custom field, so on a DB where it isn't installed — e.g. ERPNext's test bootstrap, which creates a `User` → `Contact` and fires this `on_update` hook before the app's custom fields exist — the query raised `OperationalError (1054): Unknown column 'primary_contact' in 'WHERE'`, aborting `bench run-tests` during record generation. The loop now skips any doctype lacking the column via `frappe.db.has_column(dt, "primary_contact")`.

## [0.2.6] - 2026-06-08

### Fixed
- **Contact sync `AttributeError` on missing custom fields**: `sync_from_contact` read `doc.custom_title` / `custom_phone_number` / `custom_mobile_number` / `custom_email` as direct attributes, and `sync_from_main_doc` did the same for `contact.custom_*`. When a `Contact` lacks those custom fields — e.g. ERPNext's test bootstrap auto-creates a Contact (via `User.create_contact`) before this app's custom fields exist — the `on_update` hook raised `AttributeError: 'Contact' object has no attribute 'custom_title'`, which aborted the whole `bench run-tests` record-generation phase. All custom-field **reads** now use `getattr(obj, "field", None) or ""`, matching the defensive pattern already used for the `primary_contact_*` fields in the same module. Writes are unchanged (assigning a missing field never raised).

## [0.2.5] - 2026-06-08

### Fixed
- **Test discovery `ImportError`**: `bench run-tests` crashed at discovery time with `cannot import name 'add_hours' from 'frappe.utils'`. Frappe has no `add_hours` helper — the correct utility is `add_to_date(date, hours=N)` (already used elsewhere in this app). The bad import lived in two places: `erpnext_enhancements/api/booking.py` (which would have raised at runtime whenever `create_composite_booking` was called) and `enhancements_core/doctype/asset_booking/test_asset_booking.py` (which broke discovery for the whole app). Both now use `add_to_date`, restoring test discovery and the composite-booking API.

## [0.2.4] - 2026-06-08

### Fixed
- **File preview toolbar icons**: The Download / Open-in-new-tab / Close icons in the file-preview overlay rendered tiny and dark. Frappe icons default to `fill: none; stroke: var(--icon-stroke)` at the 12px `sm` size, so the button's white text colour never reached them. Added overlay-scoped CSS that forces `--icon-stroke`/`stroke` to white and bumps the toolbar icons to 20px.
- **File list grid view default**: The File list now defaults to grid view every time it is opened, not just on a user's first-ever visit. The previous one-time `localStorage` marker meant returning users always landed in list view (since `FileView.before_render()` persists `grid_view=false` after any list render). Grid is now forced from the patched `setup_view()`, which runs once per FileView instantiation, so an in-session toggle back to list still sticks.
- **Kanban drag-to-scroll stutter**: Dragging the Opportunity Kanban board sideways to reveal more columns stuttered badly. A Chrome performance trace traced it to frappe core's `bind_clickdrag` (`kanban_board.bundle.js`): its `mousemove` handler reads `draggable.offsetLeft` right after writing `draggable.scrollLeft`, forcing a synchronous full-document style/layout recalc on every move — ~34,800 elements at up to ~88ms each on a large board (~5 dropped frames per mousemove). New `kanban_scroll_perf.js` installs a single capture-phase pointer handler that reimplements drag-to-scroll from `e.pageX` alone (no layout read — `offsetLeft` is constant during a horizontal scroll and cancels out) and `stopPropagation()`s the move so frappe's reflow-forcing handler is skipped during a drag. frappe's exact ignore-selectors are mirrored, so which areas start a drag-scroll is unchanged. Remove once frappe core stops reading `offsetLeft` on `mousemove`.
- **Kanban touch drag — "hold to grab"**: On touch screens a card could be picked up and dropped into another column from an incidental brush, because Frappe starts a drag the instant a touch lands on a card. The old "drag delay" patch proxied the global `window.Sortable`, but Frappe v16's Kanban imports SortableJS as a bundled module, so the proxy never reached the real card-drag instances and the delay was never applied (it also fully *disabled* Kanban drag). The patch now recovers each card container's live SortableJS instance from the DOM after the board renders and sets `delay: 1000`, `delayOnTouchOnly: true`, and `touchStartThreshold: 8` — so a touch must press-and-hold ~1s before a card can move, a swipe still scrolls the column, and mouse dragging on desktop stays instant.
- **Task tree drag-and-drop intent**: In the Project "Scope" tab task tree, dropping a task onto the middle of a row is meant to nest it as a child while dropping near a row's top/bottom edge reorders it as a sibling. The intent was measured against the whole `.task-node`, whose box spans the entire subtree for an expanded parent, so the "nest" band fell off-screen and nesting only ever worked on leaf tasks. Intent is now measured against the hovered node's own row, so nesting works under expanded parents too.
- **Project Gantt scroll target**: The Schedule-tab Gantt now opens scrolled to the **first task's start date** (the earliest task), instead of the project's `expected_start_date` — which left the viewport on empty space whenever that field was unset or pointed away from the actual work.

## [0.2.3] - 2026-06-08

### Added
- **Automated GitHub Releases**: A new `Release` workflow (`.github/workflows/release.yml`) tags and publishes a GitHub Release whenever a new `__version__` lands on `main`. It reads the version from `erpnext_enhancements/__init__.py`, verifies `package.json` is in sync, skips versions already tagged, and uses this changelog's matching section as the release notes. Because Frappe Cloud deploys from `main`, the repo's Releases page is now a 1:1 log of what is deployed and at which version.

## [0.2.2] - 2026-06-08

### Fixed
- **Frappe integration CI**: Added a `redis:7-alpine` service container and pointed Frappe's `redis_cache`/`redis_queue`/`redis_socketio` at it (`127.0.0.1:6379`). `bench new-site` installs ERPNext, which enqueues a background job (`delete_dynamic_links` via `enqueue_after_commit`) and forces a Redis Queue connection; with `--skip-redis-config-generation` no redis was running, so Frappe fell back to its default `127.0.0.1:11000` and the install died with "Connection refused". Also dropped the apt `redis-server` install, which would collide with the container's `6379` port mapping.

## [0.2.1] - 2026-06-08

### Fixed
- **Frappe integration CI**: Bumped the Node version installed for the integration-tests job from 20 to 24. Frappe `version-16`'s `package.json` declares `engines.node ">=24"`, so `yarn install` aborted during `bench init` ("The engine \"node\" is incompatible with this module"). Mirrors the earlier Python 3.14 bump — both track `version-16`'s moving toolchain floor.

## [0.2.0] - 2026-06-08

### Added
- **Time Kiosk standalone PWA**: The Time Kiosk is now an installable Progressive Web App served at `/kiosk` (web manifest, root-scope service worker, offline app shell) instead of only living inside the desk app. The legacy desk page at `/app/time-kiosk` stays as a fallback and links to the new app.
- **Continuous, battery-aware location tracking**: While clocked in **and active** (not paused), the PWA tracks location on the main thread using `watchPosition` + a movement distance-filter + a periodic heartbeat. Points are persisted to IndexedDB by the service worker and uploaded in batches via a new session-trusted `log_geolocation_batch` endpoint, with Background Sync retry when offline. (Fixes the prior dedicated Web Worker that could never read GPS — `navigator.geolocation` is unavailable in workers.)
- **Location history & timeline**: Each point is tied to its `Job Interval`; new whitelisted `get_location_history` plus a manager-facing **Location Timeline** desk page replay an employee's movements on a Leaflet map.
- **Time Kiosk Settings** (Single doctype): configurable distance filter, heartbeat, GPS accuracy, screen wake-lock, batch size, and retention. A daily scheduled job purges location logs older than the retention window.

### Changed
- **Time Kiosk Log** gains `job_interval`, `accuracy`, `speed`, `heading`, and `altitude` fields, search indexes on `employee`/`timestamp`, and owner-scoped read access for employees.
- **App consolidation**: Merged the previously separate `crm_enhancements`, `global_enhancements`, `project_enhancements`, `task_enhancements`, and `qb_time_integration` apps into `erpnext_enhancements`. Each is now a Frappe module within this single app (CRM Enhancements, Global Enhancements, Project Enhancements, Task Enhancements, QuickBooks Time Integration). Their hooks, patches, fixtures, and public assets were merged; incoming public assets are namespaced under `public/{js,css}/<module>/` to avoid collisions. The standalone apps are no longer required — uninstall them from existing benches after deploying this release.

## [0.1.1] - 2026-01-27

### Fixed
- **List View Sorting**: Fixed an issue where the sort dropdown menu was hidden behind the list header or other elements by increasing its z-index.

## [0.1.0] - 2024-05-22

### Added
- **Time Kiosk**: A simplified, tablet-friendly interface for employees to log time against projects and tasks. Supports geolocation logging and syncing to Timesheets.
- **Project Enhancements**:
    - **Procurement Status**: Calculated status fields on Projects to track material requests and orders.
    - **Project Merge**: Utility to merge duplicate projects.
    - **Attachment Sync**: Automatically syncs attachments from Opportunities to created Projects.
    - **Validation**: Improved status validation logic.
- **Kanban Board Improvements**:
    - **Touch Support**: Patched `Sortable.js` initialization to fix drag-and-drop latency on touch devices.
    - **Scrolling**: Fixed horizontal scrolling issues for large boards.
    - **WIP Limits**: Added custom fields to enforce Work-In-Progress limits on columns.
- **Safe Form Drafts**: Implemented "User Form Draft" mechanism to auto-save unsaved form data to a safe container, preventing data loss on navigation or browser crash.
- **Travel Management**: Custom "Travel Trip" workflow and enhancements for Expense Claims.
- **Dashboard Overrides**: Custom dashboard data logic for Projects and Employees.
- **Comment Enhancements**: Custom Vue.js components for improved commenting experience on various doctypes.


# The Nik runbook — what only you can do

**Written 2026-08-14**, after reconciling all 149 open task records on PRJ-00580 and PRJ-00755
against the actual code on `main` in both repositories.

Everything in this file is something an agent **cannot** do: file an application with an external
platform, decide a policy, run a command against production, hold a phone, or be a person who is
not an engineer. The ordering is by **lead time, not by importance** — the items at the top have
queues measured in weeks and half of them gate the other half.

> Nothing here is a request for a status update. Each item names what to do, where, what must be
> true first, and how you know it worked.

---

## 0. What changed today, before you read the rest

**Six pull requests are open. None is merged; `main` auto-deploys to production, so merging is
yours.**

| Order | PR | Repo | Version | What it is |
|---|---|---|---|---|
| 1 | [#340](https://github.com/sapphirefountains/triton/pull/340) | triton | 0.68.0 | The cached-token share, from provider to response |
| 2 | [#341](https://github.com/sapphirefountains/triton/pull/341) | triton | 0.68.1 | A drift guard that had skipped for its entire life |
| 3 | [#342](https://github.com/sapphirefountains/triton/pull/342) | triton | 0.68.2 | Two claims that were written down and never asserted |
| — | [#828](https://github.com/sapphirefountains/erpnext_enhancements/pull/828) | erpnext_enhancements | 1.288.3 | Pre-existing — a failed Triton turn recorded *that* it failed, not why |
| 4 | [#829](https://github.com/sapphirefountains/erpnext_enhancements/pull/829) | erpnext_enhancements | 1.288.4 | The invocation log's cost columns were confidently wrong |
| — | [#830](https://github.com/sapphirefountains/erpnext_enhancements/pull/830) | erpnext_enhancements | 1.288.5 | This document |

**The order is load-bearing for the three Triton ones.** `#342` is stacked on `#341`, which is
stacked on `#340` — merging out of order shows the wrong diff. `#829` is harmless alone but only
*does* anything once `#340` is **deployed**, because it reads a field Triton does not publish
until then; until it lands, those columns read zero, which is the intended visible-absence state
rather than a wrong number. `#828`, `#829` and `#830` picked non-colliding version numbers
deliberately, so they can merge in any order relative to each other.

**The task board was reconciled.** 57 tasks that had already shipped were sitting at *Overdue* or
*Pending Review*; they are now Completed with `completed_on` set to the **actual release date from
the changelog**, not to today. Twelve tasks that claimed to be finished turned out not to be, and
were moved back to Open with a comment naming exactly what is left. PRJ-00580 went from 52 tasks
at *Pending Review* to 4.

The full per-task evidence is in [§5](#5-appendix--the-reconciliation-in-full) and in each task's
own timeline in ERPNext.

---

## 1. Start the clocks — do these first, none of them needs code

These are the critical path for the entire marketing programme. Every one has a queue measured in
weeks, and **filing wave one is what makes wave two possible at all.** The filing material — exact
scopes, the use-case text to paste, the screencast scripts — is already written in
[`docs/marketing-platform-approvals.md`](marketing-platform-approvals.md). This section is only
the order and the traps.

### 1.1 Meta Business Verification — **file today**

Business Manager → Business settings → Security Centre. Company documents only; no working app
needed. **It is the documented prerequisite for App Review**, so filing App Review before this
clears means waiting for it anyway with the review clock already spent. Budget six weeks.

**Before you file, check one thing that sinks submissions:** is the Instagram account a
*professional* account, and is it linked to the Facebook Page **inside Business Manager**? Check at
Business Manager → Accounts → Instagram accounts. If Instagram was linked from the Instagram app
rather than from Business Manager, the link often does not exist on the Business Manager side — and
no amount of approval works around it. It is a ten-minute fix that is routinely not done.

### 1.2 Google Ads developer token → Basic Access — **file today**

From the **manager (MCC) account** → Tools → API Center. Not a customer account, not a test
account. If there is no MCC, one has to be created and the production ad account linked to it
first.

This gates the **largest single spend channel**, so it is the first of the ads gates. Review is
documented at ~5 business days. Set the API contact email to a mailbox someone actually reads —
Google uses it for compliance notices, and an unread mailbox is how tokens get suspended.

### 1.3 Google Business Profile access — **file today, because of a hard 60-day wait**

You must have managed a **verified, active Business Profile for at least 60 days** before Google
will grant API access. **If any location is unverified, that clock has not started** — and no
application shortcuts it. That is the only reason this sits in wave one; the GBP work itself is
late in Phase 3.

Say explicitly in the request that you need **reviews**, because reviews and local posts are not
on the modern APIs — they are still on the legacy Google My Business v4.9, which needs *additional*
allowlisting beyond the standard grant. Approval signal: the API quota in Cloud Console goes from
**0 QPM to 300 QPM**. There is no other reliable indicator and the approval email is easy to miss.

### 1.4 LinkedIn Community Management, Development Tier — **file today, on a brand-new app**

LinkedIn's own FAQ: the Community Management Dev Tier request is only available on **new developer
applications that do not have access to other API products** — the option is greyed out otherwise.
So if the Advertising API lands on the app first, recovering means creating a second app, taking
Dev Tier there, filming the Standard Tier screencast against it, requesting Community Management on
the original using the second app's client id, and discarding the throwaway.

> **Plan two LinkedIn apps from the start**: one for Community Management (organic publishing), one
> for the Marketing Developer Platform (ads reporting). This costs nothing and sidesteps the trap.

**A LinkedIn rejection burns the app.** Their documentation is explicit for both tiers: if your
application is rejected you must create a **new app** and submit again. Treat the first submission
as the only cheap one.

**Two things to establish before filing:** who holds **super admin** (not admin) on the Sapphire
Fountains Company Page — they must click the verification link — and a business email on the
company domain, because personal addresses fail vetting.

### 1.5 Search Console property grant — **file today, ~15 minutes, and it is silently costing you now**

Not an approval; a permissions grant. **Google Search Console has returned HTTP 403 on every run
since 2026-06-26 — GA4 succeeded 40/40 days, GSC failed 40/40.** Organic clicks and impressions
have therefore read **zero for the entire history** of `Marketing Web Snapshot`, and right now that
reads as a real business fact. It is not one.

Two candidate causes, possibly both: the GA4/GSC service account is not a user on the Search
Console property, or the property is a `sc-domain:` property being requested as a URL prefix (or
vice versa).

1. Find out **who administers the Search Console property** — this is the open question.
2. Add the service account as a user on the property.
3. Confirm the property type matches what `api/analytics.py` requests.
4. Backfill the affected range.

### 1.6 Wave two — after Phase 2 has something to demonstrate

Meta App Review (Advanced Access), LinkedIn Community Management Standard Tier, LinkedIn Marketing
Developer Platform, and the YouTube Data API audit all review a **working, reachable integration**.
Meta rejects a submission outright if reviewers cannot access the app to test it, which is the most
common failure mode.

> **Do not bulk-upload a YouTube back catalogue before the audit clears.** Videos uploaded through
> an unaudited project are locked private and **the lock cannot be appealed**. The only remedies are
> to re-upload through an audited project or upload by hand. Test with throwaway content only.

---

## 2. Decisions only you can make

Each has a recommendation attached; most are a yes or a redirect rather than fresh thinking. Two
are cheap now and expensive later.

### 2.1 Chat governance — three still open (TASK-2026-01512)

| # | Decision | Recommendation | Why it is still open |
|---|---|---|---|
| **D-10** | A nightly off-box append-only copy of the audit rows | **Offered without one** | The hash chain makes tampering *detectable, not impossible* — anyone with database write access or root on the VM can rewrite a row and recompute the tail. A bucket with object versioning and a retention lock is the only durable fix, and it is a real recurring cost. This is a budget decision, not a technical one. |
| **D-11** | Import back-fill of historical Google Chat content | **No** | Silence is not a no here, because guessing wrong costs in both directions: it cannot be undone once done, and it cannot be done at all after 90 days. |
| **D-8** | Who owns the matching **Google Vault** retention rule | Needs an owner, not an answer | This system cannot purge Google's copies — they are governed by Workspace retention in a console it cannot reach. Deleting from ERPNext while Chat keeps everything forever is a reporting gap, not a retention policy. Currently unowned. |

### 2.2 Phase 5 — four decisions built on a recommendation (TASK-2026-01366)

These were **implemented to the ADR's own written recommendation** rather than to an answer, and
each is cheap to reverse *now*. Confirming them turns four silent assumptions into four decisions.

- **Token ceiling (CQ-17)** — built at 40,000 as a Chat Settings field with the realised count
  logged every turn, so the answer can come from two weeks of data rather than a guess.
- **Triton's reply exemption (CQ-18)** — built with the narrow exemption: a conversational reply to
  a mention, same room and thread, no external mutation, bot identity, still writing an audit row.
  Declining does not mean gating the reply; it means Phase 5 has to build an approval surface
  inside Google Chat, which is a design task with a schedule.
- **Sources chip row (CQ-14)** — built additively; the chip row renders exactly what it renders
  today.
- **Vector storage (CQ-23)** — base64 float32 scored with in-process numpy behind a two-method
  adapter, so the backend is a one-file swap. Production MariaDB is 10.11 with no vector type and
  the deploy pipeline cannot pip-install, so this adds no dependency.

### 2.3 Two small ones blocking code

- **`agent_user` is NULL for inbound, missed and voicemail calls** (TASK-2026-01384). Either
  populate it on every call path, or decide explicitly how unattributed rows are surfaced.
  **Scoping voice reads per-agent without answering this replaces "agents see nothing" with "agents
  see a partial archive and cannot tell", which is worse.**
- **`text-sapphire-light` fails contrast at 2.4–2.7:1 on light panels** (TASK-2026-01417). It is a
  brand accent (`#00a8e8`), not a status colour, so nothing flips it in light mode. The plumbing is
  done and allow-listed with a test that fails if it ever starts passing — **the fix is a colour
  choice.** Pick a darker blue for small uppercase labels on light backgrounds, or accept the
  exception on the record.

---

## 3. Things that cannot be done from a development machine

`docs/chat-phase6-plan.md` §5 states this up front so the checkpoint is not "full of unverified
checkboxes". These are in dependency order.

### 3.1 Redeploy the 13 Triton agents — **one command, settles three open items**

```bash
cd backend && python -m scripts.deploy_agents
```

Nothing in CI and nothing in the VM deploy runs this; it is manual, and there is no record of it
having been run since v0.42.0. It is simultaneously:

- the residual on **TASK-2026-01191** — the 53-tool ERPNext snapshot is committed and correct, and
  has never reached the deployed agents, because Agent Engine freezes the tool set at deploy time;
- the untested fix for **TASK-2026-01391** — "0 events received". v0.67.5 found that
  `deploy_agents.py` shipped `google-adk[a2a]>=1.33.0` uncapped, unpickling a 1.33 agent against
  ADK 2.6.3 and producing `200 OK` with `Content-Length: 0` on a background-thread `AttributeError`.
  That matches the symptom exactly, and the cap has never been exercised;
- and the delivery mechanism for **Deep Research**, which v0.67.8 records as still broken in
  production — engine `7827804258916368384` needs deleting and recreating.

**Afterwards, confirm it worked** by asking a deployed agent to name `fac_training_compliance_status`.
If it cannot, the snapshot did not land.

### 3.2 One read-only `gcloud` command — settles a contradiction and ~$25–50/month

Two documents claim opposite things about whether a Cloud Armor policy is attached to the live
backend service, and **nobody has ever actually read it**.

```bash
gcloud compute backend-services describe triton-backend-svc --global --format="value(securityPolicy)"
```

Empty output means no policy. Then, for the orphaned stack (TASK-2026-01390) — it serves 404s, so
it is not an exposure, but **it holds the project's only Cloud Armor policy**, so decide the policy
before tearing it down rather than after:

```bash
gcloud compute backend-services list --global
gcloud compute url-maps list
gcloud sql instances list   # settles TASK-2026-01392: does triton-main-db exist, and what does it cost?
```

**One caveat that matters before you attach anything:** OWASP preconfigured rules
**false-positive on user-typed chat text** — a coworker pasting a SQL snippet or an HTML tag gets a
403 with no feedback. Attach in preview mode first.

### 3.3 The pilot walkthrough — ~20 minutes, you and James (TASK-2026-01363)

**This is the single highest-value item in section 3.** Every Phase 3 feature is built and
deployed, and six of them have been used **zero times in production** — mentions, attachments,
threads, edits, deletes, revisions. Whole feature set to date: 1 room, 5 messages, 2 users.

Twelve tasks were sitting at *Pending Review* waiting on this evidence rather than on code. It is
one sitting, ten actions in order, and it either produces the evidence or produces a defect list.
The script is on the task.

### 3.4 Run the bench suites — the security model is unproven by execution

**15 of the 22 named Phase 6 tests need a real bench, and three bench suites from earlier phases
have never been executed at all.** CI has no bench and will not get one, so these are worth exactly
as much as the discipline of running them.

```bash
bench --site <site> run-tests --app erpnext_enhancements --module erpnext_enhancements.tests.test_chat_attachments_bench
bench --site <site> run-tests --app erpnext_enhancements --module erpnext_enhancements.tests.test_chat_triton_bench
bench --site <site> run-tests --app erpnext_enhancements
```

The first is the residual on TASK-2026-01313: it asserts a non-member gets a 403 on an attachment,
authenticated and unauthenticated. That claim is currently supported by code review, not by running
it.

**And one specific question to answer while you have a bench** (TASK-2026-01289) — does `migrate`
drop the hand-added FULLTEXT index?

```bash
bench --site <site> migrate
bench --site <site> mariadb -e "show index from \`tabChat Context Chunk\`"   # look for chunk_body_fulltext
bench --site <site> migrate
bench --site <site> mariadb -e "show index from \`tabChat Context Chunk\`"
```

Until that is answered, `test_record_here_whether_migrate_drops_it` is a placeholder that asserts
nothing, and whether the `after_migrate` backstop is load-bearing or decorative is unknown.

### 3.5 The live round trips

- **Google Chat, real space, real credentials** (TASK-2026-01331). The 200-message soak ran
  entirely against an in-memory fake **written from documentation rather than observation**. It
  proves the engine is internally consistent; it cannot prove that `clientAssignedMessageId` is
  actually populated in a `messages.get` response, which is the assumption echo suppression rests on.
- **The Phase 5 gate** (TASK-2026-01419) — the same question asked from both clients, answered
  in-thread, with citations that resolve.
- **The notification matrix** (TASK-2026-01303) — two browser profiles, one phone, production.
  Script it first, then run it live, saying out loud what should happen before it happens.
- **Web Push on a real phone**, and check what origin `frappe.utils.get_url()` returns from behind
  the load balancer. That is not cosmetic: an `http://localhost` origin would be baked into a
  downloaded legal artefact.

### 3.6 The roster chase — an afternoon, but only if someone schedules it (TASK-2026-01267)

**Everyone Triton may answer for must have clicked "Link ERPNext" once, or the turn dies before it
starts.** Auto-provisioning somebody a Triton account does *not* auto-provision their ERPNext grant.
Across the full staff roster that is on the order of fifty people.

Left until the phase is being tested, it presents as an intermittent Phase 5 bug that only affects
some people — which is the most expensive way to discover it. Enumerate the roster, check each
account for a grant, chase the remainder, and **decide the fallback for an unlinked mentioning
user**: a mandatory link prompt, a degraded answer without ERPNext tools, or an explicit refusal.

### 3.7 Marketing — two production actions

- **Turn on lead attribution** (TASK-2026-01472). `lead_attribution_enabled` is `0` today, so the
  whole WP-1 feature is off in production. Staged order matters, per
  [`docs/attribution-runbook.md`](attribution-runbook.md): `lead_attribution_enabled` alone, left
  for a week; then `require_lead_source_on_opportunity`; then `require_lead_source_on_lead` last.
  The gate is a hook rather than `reqd = 1` **precisely so unticking one box unblocks the sales team
  mid-day with no deploy, no restart and no cache clear.** Monitor via the Attribution Gaps report.
- **The WordPress capture snippet** (TASK-2026-01471) — described in the plan as the highest-value
  item in it. `submit_web_lead` shipped in v1.241.0 and **has never been called**. Fluent Forms Pro
  ships a native webhook integration with custom headers, so no plugin code is needed for the POST
  itself. What is needed: a first-party **cookie** (not `localStorage` — it must survive a subdomain
  hop) capturing `utm_*`, `gclid`, `document.referrer` and the landing path on entry, never
  overwriting a non-empty value within the session; hidden fields populated from it; and the
  `hp_company_url` honeypot. Contract: [`docs/attribution-runbook.md`](attribution-runbook.md).

---

## 4. The review queue

After reconciliation, **PRJ-00580 has 4 tasks at *Pending Review* and PRJ-00755 has 1** — down from
52 and 18. The rest were either genuinely complete (now Completed, with their real ship dates) or
genuinely unfinished (now Open, with a comment naming the residual).

That is the useful outcome: *Pending Review* now means "code merged, needs your eye" and nothing
else. The twelve tasks that were sitting there waiting on **evidence** rather than on review are
now Open and cross-referenced into [§3](#3-things-that-cannot-be-done-from-a-development-machine),
which is where they actually belong.

---

## 5. Appendix — the reconciliation in full

One row per task. **Verdict** is what the evidence supported after an adversarial second pass over
every "shipped" claim; **11 of those claims were overturned** and are marked `↺`.

### Partial — shipped in part, with named residual work

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-00353 |  | Hide Activity | Make the hide rule actually win: either add `!important` to `.form-page.ee-activity-off-tab .form-footer { display: none; }` at desk_enhancements.bundle.css:1378, or narrow/remove the blanket `display: block !important` on `.form-footer` at line 1152. Then extend tests/test_activity_first_tab_only.py to assert no… |
| TASK-2026-01157 | ↺ | Supervisor sign-off, ask-the-author Q&A, gamification &… | Learner-facing surfaces for two strands were never built. (1) Ask-the-author Q&A: add ask_question and get_lesson_questions to the METHOD map in www/training.html and build the lesson-level ask/read UI in public/js/training/player.js — today training/qa.py's three whitelisted functions have no caller in the app, so no… |
| TASK-2026-01191 | ↺ | Triton: deployed ADK agents cannot name a single ERPNext… | Run `python -m scripts.deploy_agents` (all 13 Agent Engine agents) from backend/ so the committed 53-tool snapshot actually reaches the deployed agents, then confirm one deployed agent can name e.g. fac_training_compliance_status. Neither CI nor the VM deploy performs this step, and there is no record it has been done… |
| TASK-2026-01241 |  | Lint backlog: 433 ruff findings keep the lint job advisory | Run the `ruff format` pass (457 files) as its own PR at a quiet moment; clear or consciously ignore the remaining 152 `ruff check` findings; then delete `continue-on-error: true` from the lint job in .github/workflows/ci.yml and refresh the stale comment above it that still says 73. |
| TASK-2026-01278 |  | Chat Auditor role and a threaded oversight viewer, not a list… | Build the threaded oversight viewer as a new separate surface (not a mode of the employee chat app): sequence-number ordering rather than timestamp, edited messages expanding to their revision chain, deleted messages as tombstones whose original content is a separately audited expand, a member timeline of who could… |
| TASK-2026-01280 | ↺ | Bell rows with deep links that resolve on a cold page load | Write the bell-surface tests the task asked for and wire them into ci.yml: (1) the named acceptance test that zero rows land in `tabEmail Queue` for a Chat Message / Chat Mention notification even for a user who has explicitly opted that type in - today nothing anywhere asserts `notification_skip_email_types` is… |
| TASK-2026-01288 | ↺ | Native Web Push with hand-rolled VAPID, because no push… | Honour Retry-After on a 429 in chat/notifications/webpush/sender.py. Today _note_retry_after only logs the header at debug level and the push is discarded; the task requires the rate-limited case to respect the retry header (a delayed re-send or a small retry queue). 404/410 pruning and the 413… |
| TASK-2026-01289 | ↺ | Chunking, embeddings and the lexical tier that makes an exact… | Run the bench procedure and record the answer: `bench --site <site> migrate`, then `show index from \`tabChat Context Chunk\`` to see whether chunk_body_fulltext survives, repeat once more, and write the result into the ADR addendum. Until that is done,… |
| TASK-2026-01293 |  | One definition of a non-participant read - unified audit,… | 1) Build the unified Chat Access Report (the planned chat/governance/access_report.py) that unions Chat Retrieval Audit and Chat Audit Log, and make every consumer in the phase read it. 2) Establish the one-record-per-non-participant-read property across every path once those paths exist - viewer, oversight search,… |
| TASK-2026-01295 | ↺ | Cross-surface read and dismiss sync, and a badge that… | Write the badge-vs-coalescing test the task demands: twenty messages delivered to an away recipient must produce an unread badge of 20, exactly one Chat Room bell row (bell.notify_room's dedupe_unread path), and at most two push banners. bell.notify_room / bell.has_unread_row are currently imported by no test. The… |
| TASK-2026-01296 |  | Permission hooks for Chat Room and Chat Message, registered… | Run the bench-required suite on a real site - bench --site <site> run-tests --app erpnext_enhancements --module erpnext_enhancements.tests.test_chat_permissions_bench - and record the result at the checkpoint. No code change is expected. |
| TASK-2026-01297 |  | Attachments and inline images, with the private-file 403… | 1) Fix prepare_upload to read the real AttachmentLimits surface (outbound_ceiling()/inbound_ceiling() or chat_bytes/erpnext_bytes) so max_file_bytes is non-zero, and add a contract test - the current getattr defaults hide the mismatch silently. 2) Store image width/height on Chat Attachment at ingest and emit them (or… |
| TASK-2026-01308 |  | Space provisioning in all three modes, and membership sync… | "A Chat member with no ERPNext account is stored by email rather than dropped" is not implemented. Chat Room Member has a `user` Link and no email column; membership.insert_room_member returns "" when resolve_user finds no User, and _accept_inbound writes an audit note instead of a member row. The divergence is… |
| TASK-2026-01310 | ↺ | Search that cannot leak a room you are not in, and the… | Move the Desk Triton bubble's full-screen-sheet breakpoint in erpnext_enhancements/public/css/global_enhancements/triton_widget.css from max-width:480px to ~767px (both the .triton-panel block at line 576 and the header-picker block at line 1244), and perform the on-handset check that the composer stays above the… |
| TASK-2026-01311 |  | Security hardening pass where every item is a command and its… | 1) No rate limit on any chat whitelisted method and no written exemption - chat/endpoints.py says the classification is the precondition for it, and only gchat/webhook.py carries @rate_limit. 2) Attachment content types are still taken from Google's contentType / the upload header, never sniffed from the bytes;… |
| TASK-2026-01313 | ↺ | Attachments both directions - each end has a permission… | Execute erpnext_enhancements/tests/test_chat_attachments_bench.py on a real bench (bench --site <site> run-tests) and confirm the parity-row 403s for a non-member, authenticated and unauthenticated. This is a bench/live verification step, not more code - CI has no bench and will not get one. |
| TASK-2026-01315 |  | Inline citations added without regressing the sources chip… | Nothing emits a `citations` SSE frame. The Desk widget already handles `citations` and `citations_append` events and reads meta.citations off `done`, but triton_chat.stream_query only forwards Triton's stream verbatim and the Triton backend (app/api/v1/endpoints/streaming.py, app/core/intelligence.py) emits `sources`… |
| TASK-2026-01316 |  | Health dashboard with real thresholds, and drift repair that… | 1) There is no dashboard a human can look at - health.py is deliberately NOT @frappe.whitelist() and is not in hooks.py, so it is bench-only; api/integrations_health.py has a single small chat tile, not the panel set. 2) Thresholds are hard-coded module constants (DUE_PENDING_WARN_SECONDS etc.), explicitly documented… |
| TASK-2026-01318 | ↺ | The Triton surface - inline citation links added without… | No producer emits a `citations` / `citations_append` SSE event on the Desk Triton widget's stream, so live.manifest stays null and applyCitations is a permanent no-op there; and no stored turn carries meta.citations. Wire the widget's backend turn to emit the manifest (the work tracked as TASK-2026-01315) before this… |
| TASK-2026-01321 |  | Fail-closed retrieval audit trail, plus an invocation log… | 1) There is no report or chart over Triton Invocation Log, so "what did Triton cost last week" still requires SQL. No Report or Dashboard Chart exists for it anywhere in the repo; the panel added in v1.288.2 lives in chat/health.py and prints only the last ten turns by status, and the table's zero DocPerm closes the… |
| TASK-2026-01328 |  | Prove the permission boundary still holds now rows arrive… | Run erpnext_enhancements/tests/test_chat_permissions_bench.py against a real bench on a non-production site, and resolve the DocShare result: if a DocShare row on Chat Room does widen a non-member's read (Chat Room is the one chat doctype carrying a read DocPerm), that is a design finding needing either a DocShare… |
| TASK-2026-01381 |  | Withdraw lead-time modelling (done); decide on the open-PO… | Someone in procurement must confirm the open-PO overdue list is real before anything is built on it: 71 overdue lines across 5 projects (PRJ-00566 38 lines / oldest 306d, PRJ-00567 22 / 67d, PRJ-00219 5 / 235d, PRJ-00694 4 / 74d, PRJ-00438 2 / 2d). If it is noise — every open project line being overdue may just mean… |
| TASK-2026-01385 | ↺ | Cookie-bound nonce for the OAuth state parameter | Single-use is only enforced on the success path. Clear the state cookie on rejection too — either by catching the 400 in google_callback and returning a response that carries response.delete_cookie(_OAUTH_STATE_COOKIE, path='/'), or by deleting it in the StarletteHTTPException handler for this route. Then make… |
| TASK-2026-01389 | ↺ | aria-expanded on the four remaining disclosure controls | frontend/src/views/ChatView.vue:696 — the 'View Proposed Plan Parameters' details-accordion-toggle button still has no aria-expanded and no aria-controls, while the region it controls (the v-if at line 712) has no id. Give the button :aria-expanded="!!msg.ui_metadata._details_open" and an aria-controls pointing at a… |
| TASK-2026-01391 |  | Deployed Vertex Agent Engine returns "0 events received" | Production remediation, all outside the repo: delete reasoning engine 7827804258916368384; run python -m scripts.deploy_agents --agent research (it will report created); put the new full projects/.../reasoningEngines/... path into REASONING_ENGINE_RESEARCH_RESOURCE in Secret Manager; run sudo… |
| TASK-2026-01471 |  | Fluent Forms Pro → submit_web_lead, plus the first-touch UTM… | On WordPress: install the mu-plugin, create the hidden attribution fields plus the hp_company_url honeypot in each Fluent Forms builder, and add a webhook per form posting to submit_web_lead with the bearer header. In ERPNext: set web_lead_shared_secret and web_lead_default_owner, then enable web_lead_ingress_enabled.… |
| TASK-2026-01479 |  | marketing/ module scaffold + Module Def patch + Marketing… | Still to build: core/{client,api,constants,utils,tasks,oauth}.py, the platforms/ and publish/ packages, the top-level api.py re-export that keeps registered webhook URLs on a stable short path, and the scheduler entries — hooks.py currently registers no marketing scheduler_events, which is exactly the 'added later, by… |
| TASK-2026-01501 |  | Two live URL sinks in the widget's own sources row | A server-authoritative safe_url (§4.G.7 item 1) — the client-side check is a rendering decision, not a boundary. Also still true: no Content-Security-Policy anywhere in the app. Both will matter again for the export's transcript.html, which is the same renderer writing a file that leaves the building. |
| TASK-2026-01502 |  | The oversight hatch granted write, and three read paths… | The DocShare finding (F-2). A DocShare row is still ORed past the permission_query_conditions hook on Chat Room, and assign_to.add auto-creates one — there is no DocShare validate guard, no sweep, and no decision recorded on whether assignment on Chat Room is possible at all. The only coverage is… |
| TASK-2026-01503 |  | Endpoint posture: two ungated writers, and 37 endpoints that… | Rate limiting (§4.G.4). No chat endpoint carries frappe.rate_limiter.rate_limit — the only @rate_limit in the package is the pre-existing one on gchat/webhook.py:675 for Google's inbound webhook. Still to build: the decorator placed below @frappe.whitelist(), a zero-arg callable for limit= so the count is… |
| TASK-2026-01504 |  | Attachment serving: the four-extension list is not the whole… | Content-type validation at the two entry points. There is no byte-signature sniffing anywhere in the chat package; ERPNext-side uploads still write no content_type, and sync/attachments.py:626 still takes Google's contentType verbatim with only a fallback default. And image/svg+xml is still in the client's… |
| TASK-2026-01505 |  | Audit immutability: consolidate the vault, do not rebuild it | 1) The oversight role still holds read on neither audit table: chat_retrieval_audit.json and chat_audit_log.json each grant read/report to System Manager only, and hooks.py registers no has_permission hook for either, so an auditor still cannot read the trail they are meant to review. 2) No on_change runtime tripwire… |

### Human action — not code

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-01150 |  | SPIKE: GCS signed-URL video pipeline — infra applied, 206… | Run the one-time operator procedure: confirm `terraform apply` covers infra/storage.tf with enable_training_media_bucket on, create the sa-training-media JSON key by hand and paste it into Training Settings via the key dialog (never into the field or Terraform state), then execute Test GCS Connection on prod and… |
| TASK-2026-01265 |  | Open decisions from the chat ADR that need Nikolas to answer… | The CQs still unanswered, per addendum A2.3-A2.4: gate.py's own two doors; the off-box append-only audit copy; import-mode back-fill of history; and who owns the matching Google Vault retention rule (console work this system cannot reach and currently unowned). Plus the DocShare-past-permission_query hole on Chat… |
| TASK-2026-01267 |  | Everyone Triton may answer for must link ERPNext first, or… | Run erpnext_link_report on a bench against the live roster, then chase the ~50 people it lists to click Link ERPNext once, with a named owner and a scheduled afternoon. Left until the phase is tested, this presents as an intermittent Phase 5 bug affecting only some people. |
| TASK-2026-01300 |  | Prove the edit and delete trail end to end, both directions,… | Run the demonstration on a real bench in both directions (deleted from the chat app, then deleted from the native client), asserting the same end state each time: no deleted content on an ordinary read, the revision row holding the original with actor/origin/timestamp, the message gone from the other surface, the… |
| TASK-2026-01303 |  | Run the whole notification matrix live on production before… | Script the walkthrough first, then run it live on production: walk each presence state saying the expected outcome aloud before it happens with debug.explain shown alongside; a twenty-messages-in-twenty-seconds burst to someone absent showing badge 20, one bell row and at most two banners; a read on the phone clearing… |
| TASK-2026-01322 |  | Pilot rollout behind a server-side flag, layered rollback,… | 1) Choose and brief the 5-8 pilot users in writing about oversight access, assistant reads and retention. 2) Point the Chat app's own visibility setting at that same group in the Workspace admin console, and confirm both sides name the same people. 3) Write the layered-rollback table (per layer: blast radius, what is… |
| TASK-2026-01330 |  | Run the bench-required chat suites - nothing in Phase 2 has… | On a non-production site: `bench --site <site> execute erpnext_enhancements.chat.bench_verify.run` and read the printed PASS/FAIL block, then run the three *_bench.py suites (permissions, attachments, triton) which no runner currently invokes. |
| TASK-2026-01331 |  | Live round trip against real Google Chat - what the fake… | Run the live pilot round trip, log the first real event payload verbatim and diff it against chat/testing/fixtures.py, capture a real 429 body, and confirm a relayed message shows in the native Chat client authored by the real person with no App badge. Re-disarm enabled/dry_run_mode afterwards. |
| TASK-2026-01332 |  | Re-run C2 as an ordinary pilot user, not a super-admin,… | On production-erpnext-standard-vm: `python3 ~/c2_events_subscription_check.py <ordinary-pilot-user>@sapphirefountains.com`. Every call carries validateOnly=true so nothing is created. If Q1 fails for an ordinary user, shape B does not scale to the roster and the inbound design needs revisiting before rollout. |
| TASK-2026-01363 |  | Pilot walkthrough - exercise the six Phase 3 features that… | Run the ten steps in the stated order (order matters: step 2 depends on step 1, and step 7 destroys the message step 6 needs), then say "walkthrough done" so the database verification pass can run in one go. |
| TASK-2026-01366 |  | Confirm the four Phase 5 decisions built on a recommendation,… | Get Nikolas's yes/no on four items: CQ-17 the 40,000-token context ceiling (per-turn realised counts are being logged, so this can be answered from data); CQ-18 the narrow confirmation exemption for Triton's conversational reply (declining means Phase 5 must build an approval surface inside Google Chat, which is a… |
| TASK-2026-01384 |  | agent_user is NULL for inbound, missed and voicemail rows | Decide explicitly how unattributed call rows are surfaced if per-agent scoping is ever adopted — an agent seeing a partial archive with no indication that rows are missing is worse than the current role-based all-or-nothing. Only after that is settled can the scoping approach be weighed against the role approach that… |
| TASK-2026-01390 |  | Orphaned Cloud Run load balancer stack - decide Cloud Armor… | Decide whether the Cloud Armor policy on the orphaned triton-backend-service should be reattached to the live triton-backend-svc or deliberately dropped; then delete backend service triton-backend-service, url map triton-load-balancer and NEG triton-frontend-network-endpoint-group, and tick the box in… |
| TASK-2026-01392 |  | Cloud SQL triton-main-db - verify existence and cost | Confirm whether triton-497321:us-central1:triton-main-db still exists, whether anything connects to it, and what it costs; if it is an orphan, fold it into the load-balancer teardown decision (TASK-2026-01390) and update deploy/MIGRATION.md section 8. |
| TASK-2026-01416 |  | Run the Phase 5 bench suite and record the evaluation baseline | On a real site, run bench --site <site> run-tests --app erpnext_enhancements --module erpnext_enhancements.tests.test_chat_triton_bench, and record: the result of test_a_non_members_message_appears_in_no_tier (the one that matters), the fail-closed audit group, the three-value watermark group, the two-identities… |
| TASK-2026-01419 |  | The Phase 5 gate: the same question from both clients,… | Type the same question as an @triton mention in the native Google Chat client and in the ERPNext chat window, and observe: the same answer, posted in the same thread, authored by the bot rather than the asking person, with citation links that resolve to real messages. Prerequisites to sort first: the participants must… |
| TASK-2026-01465 |  | Meta App Review - publishing + ads_read in one submission | Complete Meta Business Verification first (company documents only). Pre-flight before submitting: confirm the Instagram account is a Business account, not Creator or personal, and that it is linked to the Facebook Page inside Business Manager - without that link IG publishing is impossible regardless of approval. Then… |
| TASK-2026-01466 |  | LinkedIn Community Management API access (organic publishing) | Confirm who holds LinkedIn Page super admin; create a brand-new app; file Development Tier, build against it, then file Standard Tier with the required screencast. Update the status table in docs/marketing-platform-approvals.md as each moves. |
| TASK-2026-01467 |  | LinkedIn Marketing Developer Platform access (ads reporting) | File the Advertising API request from a second LinkedIn app and track it in the status table. No code is blocked on it today. |
| TASK-2026-01468 |  | YouTube Data API audit — lifts the private/unlisted upload… | Answer whether the YouTube channel is a Brand Account and who owns it, then file the audit form. Separately, the authorization-code OAuth flow it implies is TASK-2026-01480 and is not started. |
| TASK-2026-01469 |  | Google Ads developer token (basic access, from an MCC) | Decide which MCC holds the token, apply for Basic Access from its API Center, and record the outcome in the status table. |
| TASK-2026-01470 |  | Google Business Profile API access request | Confirm the GBP locations are verified and 60+ days old, file the access-request form, then watch the Cloud Console quota for 0 → 300 QPM. |
| TASK-2026-01472 |  | Enable lead attribution, in the runbook's staged order | In ERPNext Enhancements Settings, tick lead_attribution_enabled and leave it for a week; then require_lead_source_on_opportunity; then require_lead_source_on_lead. Monitor the Attribution Gaps report between steps — its blank/red rows are live process failures, the 'Unknown (pre-Aug 2026)' bucket is expected history. |
| TASK-2026-01474 |  | Fix the Search Console 403 — organic has read 0 for the… | Identify the Search Console property administrator; add the GA4/GSC service account as a user on the property; confirm the property type (sc-domain: vs URL-prefix) matches what gsc_property_url requests; then backfill the affected range so the historical zeros stop reading as real organic figures. |
| TASK-2026-01478 |  | Security pre-flight: verify the X-Forwarded-For chain before… | Infrastructure: find the nginx directive putting the load-balancer address into the first X-Forwarded-For entry (likely proxy_set_header X-Forwarded-For $remote_addr) and identify what changed on 2026-07-18; then re-key the rate limiter once a real client address is available. Repo-side leftover: the X-Forwarded-For… |
| TASK-2026-01500 |  | Verify Triton deploy: import check locally, confirm rollback… | After the call-tracking work lands: run `python -c "import app.main"` locally; confirm a failed deploy still auto-rolls-back; and confirm the Twilio number-pool credentials and the hard provisioning cap are present in the deployed environment, not just in a local .env. |
| TASK-2026-01510 |  | Rollout: pilot gating server-side, and a degradation that is… | Agent-draftable and not yet written: the 24-step non-engineer checklist with expected results and the four gate steps; the layer-by-layer rollback table (including the Workspace Events subscription that cannot be undone in place, only recreated plus a reconciliation sweep); the disclosure note for Nikolas to send; and… |
| TASK-2026-01512 |  | Governance decisions: eight answered 2026-08-13, three still… | Still needing Nikolas: D-10, whether to fund a nightly off-box append-only copy of the audit rows to a bucket with object versioning and a retention lock (offered without a recommendation; the hash chain makes tampering detectable, not impossible). D-11, whether to import historical Google Chat content —… |

### Not started

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-01194 |  | Triton: agents, personas and domain guides for training,… | Everything: the DOMAIN_GUIDES dict keyed by pack name (moving FOUNTAIN_DESIGN_GUIDE into it under 'water' in the same change), the four guides (training, contracts, finance-close, procurement), the three personas (training_officer, contract_clerk, buyer) plus extending bean_counter to carry the finance pack, the four… |
| TASK-2026-01195 |  | Triton: convergence.md has no row for Training, Contracts,… | Add ownership-matrix rows for: Training/LMS; Contracts + e-signature; KPI dashboards (ERPNext against Triton's kiosks); Gantt (ERPNext api/gantt.py against Triton's DynamicGantt); Google Drive; and the call-intelligence READ-UI overlap (Triton /voice/analytics against ERPNext's Call-Center desk - data ownership is… |
| TASK-2026-01285 |  | Cross-room oversight search and a hash-manifested export an… | Everything: the separate oversight search endpoint with its six filters, server-side role re-check and rate limiting; the one-audit-row-with-a-child-row-per-hit-room wiring; and the whole export (request record, background job, ZIP with sha256 manifest, lawyer-readable README, self-contained printable transcript,… |
| TASK-2026-01306 |  | Retention: the policy value, the dry run, and what survives a… | No message retention purge job exists on main. `message_retention_days` and `hard_delete_after_days` are still read only by Phase 1's `chat_settings_rules.py` validation; hooks.py registers no purge in any scheduler slot; there is no run-record DocType, no dry-run path, and no `retention_mode` or… |
| TASK-2026-01417 |  | text-sapphire-light fails contrast on light panels (~2.4:1) | Pick one of the two options (light-mode darker variant of the accent, or a role token that resolves per theme), apply it, then delete KNOWN_FAILING_FG and the 'is still failing' test from frontend/e2e/contrast.spec.ts and confirm the four view scans stay green with the allowlist gone. |
| TASK-2026-01473 |  | Make the Lead stage real — owner, triage queue, speed-to-lead… | None of this task's deliverables landed. What exists is the inputs the task itself names as already built: get_speed_to_lead in api/sales_dashboard.py (a dashboard widget listing leads with no Sent Communication — a list, not an SLA with a threshold or an alert), attribution.propagate_to_opportunity, and web_lead.py's… |
| TASK-2026-01476 |  | Nightly ad-spend connectors: Google Ads, Meta Ads, LinkedIn… | No connector code exists. erpnext_enhancements/marketing/ contains only __init__.py, README.md and doctype/ — there is no core/ package, no client.py, no platforms/ directory and no tasks.py. hooks.py registers no marketing scheduler_events and no marketing doc_events; its only marketing entry is the Marketing… |
| TASK-2026-01477 |  | The join that justifies the project: spend → Lead →… | The join does not exist. Nothing in the repo reads Ad Daily Metric except the module's own doctype code — the only other mention anywhere is a docstring in tests/test_marketing_settings.py. What exists is a different, older thing that should not be mistaken for it: kpi_dashboards/report/marketing_spend_rollup computes… |
| TASK-2026-01480 |  | Per-platform OAuth: start / callback / refresh / disconnect | No OAuth code exists for marketing. A case-insensitive grep for 'oauth' across erpnext_enhancements/marketing/ returns nothing, and the only start_oauth / oauth_callback / disconnect_callback in the whole app are the QuickBooks ones in quickbooks_online/core/api.py — the very pattern this task says to copy. Marketing… |
| TASK-2026-01481 |  | Publishing doctypes + the outbox and its sweeper | No code on main. The marketing module contains only the Phase 1 ads data model (Ad Account, Ad Campaign, Ad Daily Metric, Marketing Raw Payload, Marketing Settings, Marketing Sync Log). None of the six publishing doctypes exist -- a repo-wide search for 'Social Account', 'Social Post', 'Social Post Target', 'Social… |
| TASK-2026-01482 |  | Rate limiter + backoff: pure decision functions, Redis Lua… | No code on main. There is no marketing/publish/ratelimit.py and no marketing backoff module; the marketing package is doctype/ plus README only. The two source files this task says to copy are present and unchanged -- chat/sync/ratelimit.py and chat/gchat/backoff.py -- so the template exists but nothing has been… |
| TASK-2026-01483 |  | Meta publisher — Facebook Pages + Instagram content publishing | No code on main. There is no marketing/platforms/ directory and no meta.py; no Facebook Pages or Instagram Content Publishing client exists anywhere in the repo. No per-network validation (aspect ratio, caption length, hashtag limits) has been written. The only Meta material in the repo is planning prose in… |
| TASK-2026-01484 |  | LinkedIn publisher — organization posts | No code on main. No marketing/platforms/linkedin.py, no organization-URN post client, no image/video upload registration step. Nothing in the repo references LinkedIn outside the two marketing planning docs. Still blocked on Community Management API approval; note the filing constraint recorded in CHANGELOG v1.278.4… |
| TASK-2026-01485 |  | YouTube uploader — resumable upload, quota-aware queue | No code on main. No marketing/platforms/youtube.py, no resumable-upload implementation, no quota accounting, and no OAuth token storage for a channel owner. The GCS pattern the task says to reuse does exist at training/gcs_media.py. Two corrections for whoever builds this, from CHANGELOG v1.278.4: an upload costs 1… |
| TASK-2026-01486 |  | Draft → approve → publish workflow + marketing roles | No code on main. There is no Social Post doctype for a workflow to attach to, so the draft/approve/publish workflow cannot and does not exist -- fixtures/workflow.json carries no marketing workflow, and there are no approver or approval-timestamp fields anywhere. On roles: a 'Marketing Manager' role already exists and… |
| TASK-2026-01487 |  | /marketing SPA — calendar, composer, media picker, approval… | No code on main. erpnext_enhancements/www/ contains no marketing.py and no marketing.html; hooks.py website_route_rules (line 923) has exactly one entry, for /chat. No marketing bundle exists -- the only marketing-named JS in the repo is the four pre-existing KPI custom_html_blocks widgets and the Marketing Spend… |
| TASK-2026-01488 |  | Engagement metrics pull-back — close the loop from post to… | No code on main. The Social Post Metric doctype this task writes into does not exist, and neither does any nightly engagement pull -- hooks.py registers no marketing scheduler_events at all. The upsert-on-restate convention the task says to mirror is implemented for paid ads (marketing/doctype/ad_daily_metric with its… |
| TASK-2026-01489 |  | Field photo capture → content pipeline (Time Kiosk +… | No code on main for the marketing extension. The base this task builds on is real and unchanged: api/time_kiosk.record_job_photo (line 696) still keys on the device-minted client_uid, and workforce/photo_routing.py (WP-3, pre-existing) already copies a job photo onto the Project and tags it cust:/vs:/shot:. But none… |
| TASK-2026-01490 |  | Index Drive project folders into Marketing Media Asset | No code on main. The Marketing Media Asset doctype does not exist, so there is nothing to index into, and no indexing job of any kind has been written. The google_drive module and its per-project folder provisioning are unchanged, and the scar the task warns about is still intact and must stay that way:… |
| TASK-2026-01491 |  | AI caption and reply drafting via Vertex (drafts only, into… | No code on main. No caption drafting and no GBP review-reply drafting exists. The infrastructure the task reuses is all present -- api/gemini.py, the existing email/SMS reply drafting in api/communication.py, and the ai_governance module with its doctypes and tasks.py -- but nothing marketing-related calls into any of… |
| TASK-2026-01492 |  | Campaign landing pages + UTM link builder | No code on main. There are no campaign landing pages in erpnext_enhancements/www/ and no UTM link builder or short-link minting anywhere -- a repo search for a link builder returns only chat/links.py, which is unrelated Chat URL handling. The precedent the task names is present and is the right template:… |
| TASK-2026-01493 |  | Newsletter + SMS nurture on Frappe Newsletter and the… | No code on main. Nothing in the app touches Frappe Newsletter or Email Group -- the only two 'newsletter' hits in the codebase are string literals in channel-classification lists (crm_enhancements/attribution.py:124 EMAIL_MEDIUMS, kpi_dashboards/marketing_spend_import.py:55), not integration code. No segmentation… |
| TASK-2026-01494 |  | Marketing consent model + SMS STOP handling (TCPA) | No code on main, in either repo. There are no marketing email or SMS opt-in fields on Contact -- the sole 'unsubscribed' hit in fixtures/custom_field.json is an insert_after anchor referencing a stock Frappe field, not a consent field of ours. No source/timestamp/IP consent evidence is captured, no global unsubscribe,… |
| TASK-2026-01495 |  | Google Business Profile — reviews, AI-drafted replies, local… | No code on main. No marketing/platforms/gbp.py, no review ingestion or alerting, no local posts, and no automatic review requests after a completed Project or maintenance visit. Every GBP mention in the repo is prose -- docs/marketing-platform-plan.md, docs/marketing-platform-approvals.md, and the CHANGELOG v1.278.4… |
| TASK-2026-01496 |  | Receive attributed inbound calls and stamp campaign… | No code exists for this. api/telephony.py has no endpoint that receives a tracking-number-derived campaign, never imports crm_enhancements.attribution, and nothing in the repo maps a phone number to a campaign (the only 'tracking_number' hits are shipping tracking in package_dispatch). attribution._fill_blanks still… |
| TASK-2026-01498 |  | Tracking number pool: provision, assign to campaign, release | Triton (0.67.8) has a Twilio voice gateway — telephony_gateway.py, voice.py, the TWILIO_* settings — but no number pool of any kind: no provision/release calls, no purchase cap, no campaign-to-number assignment, and the word 'campaign' appears nowhere in backend/app outside the ADK agent definitions. There is no… |
| TASK-2026-01499 |  | Inbound call webhook — report the attributed call to ERPNext | No inbound handler for a pool number exists in Triton, and nothing reports a dialled tracking number, disposition or call SID to ERPNext for attribution. Both ends are missing: the sender here, and the ERPNext receiver (TASK-2026-01496). The idempotency requirement (unique index on the Twilio call SID) has no table to… |
| TASK-2026-01508 |  | The oversight read path, and the function nothing has ever… | retrieve_for_oversight still has zero production callers. Every reference on main is a mention rather than a call: the __all__ tuples in retrieval/gate.py:75 and retrieval/__init__.py:46, a docstring cross-reference in api/_common.py:124, and four test files. No new oversight module exists (chat/ contains only api,… |
| TASK-2026-01509 |  | Export: the artefact that leaves the building | Nothing exists. There is no Chat Export Request DocType (the chat/doctype directory holds 20 DocTypes, none of them an export request), no export module, no builder for manifest.json / transcript.html / messages.jsonl / revisions.jsonl / members.csv, and no include_deleted_content setting on Chat Settings. The… |
| TASK-2026-01511 |  | Triton: surface the cached-token split the cost dashboard… | Triton is at 0.67.8 and the change has not been made. The query response's ui_metadata is assembled at assistant.py:716-727 and carries only 'commands' and 'sources' — no prompt/candidates split, no cached_content_token_count, no model_name. core/gemini.py does read prompt_token_count and candidates_token_count from… |

### Shipped — verified in code on `main`, not just in a changelog line

| Task | Version | Released | PR | What it was |
|---|---|---|---|---|
| TASK-2026-01154 | v1.214.0 | 2026-08-02 | #686 | Training Builder desk page (block canvas + checkpoint scrubber) — v1.214.0,… |
| TASK-2026-01155 | v1.214.0 | 2026-08-02 | #686 | AI question drafting + Preview as learner — v1.214.0, PR #686 |
| TASK-2026-01156 | v1.215.0 | 2026-08-02 | #687 | Certificates, recertification & customer portal access — v1.215.0, PR #687 |
| TASK-2026-01174 | v1.255.2 | 2026-08-07 | #738 | Checkpoints have never armed - open_checkpoint's reply shape does not match… |
| TASK-2026-01175 | v1.255.3 | 2026-08-07 | #739 | Quiz retry button and score breakdown never render - submit_quiz's reply keys… |
| TASK-2026-01176 | v1.260.2 | 2026-08-07 | #753 | Course-level coverage and pass thresholds never display - get_course nests… |
| TASK-2026-01177 | v1.255.4 | 2026-08-07 | #740 | get_lesson returns one lesson's progress where the player expects the whole… |
| TASK-2026-01178 | v1.257.0 | 2026-08-07 | #743 | PDF and Downloadable File blocks speak a vocabulary the server has never known |
| TASK-2026-01179 | v1.255.2 | 2026-08-07 | #738 | Checkpoint payload and video.js disagree on five of seven field names |
| TASK-2026-01180 | v1.255.5 | 2026-08-07 | #741 | finish_attempt's already-finished early return omits `score`, so a passed… |
| TASK-2026-01181 | v1.257.1 | 2026-08-07 | #744 | The player never checks boot.enabled, so a dormant module says "Nothing is… |
| TASK-2026-01182 | v1.257.0 | 2026-08-07 | #743 | Boot settings omit two keys the client reads: max_playback_rate and… |
| TASK-2026-01183 | v1.255.2 | 2026-08-07 | #738 | answer_checkpoint returns the authoritative rewind position and the client… |
| TASK-2026-01184 | v1.258.0 | 2026-08-07 | #745 | Build the boundary contract test that would have caught all of these at once |
| TASK-2026-01186 | v1.239.1 | 2026-08-03 | #714 | Both training tools were gated as writes, so AI write gating made them… |
| TASK-2026-01187 | v1.258.1 | 2026-08-07 | #746 | Widen KPI Snapshot DocPerms before the KPI assistant tool is worth building |
| TASK-2026-01188 | v1.259.0 | 2026-08-07 | #747 | Four new read-only assistant tools: contracts, KPI, pick-routing, training… |
| TASK-2026-01189 | v0.38.1 | 2026-08-07 | #273 | Triton: delete the dead gws_manage_calendar branch and fix the /tools… |
| TASK-2026-01190 | v0.39.0 | 2026-08-07 | #274 | Triton: re-enable the orphan chart/gantt/kanban renderers and delete the dead… |
| TASK-2026-01192 | v0.40.1 | 2026-08-07 | #276 | Triton: drift guard so a dispatched-but-undeclared tool fails CI |
| TASK-2026-01193 | v0.41.0 | 2026-08-07 | #278 | Triton: the per-turn tool payload is unbounded and grows with every ERPNext… |
| TASK-2026-01237 | v1.259.1 | 2026-08-07 | #748 | Purge old PO print formats |
| TASK-2026-01238 | v1.259.2 | 2026-08-07 | #749 | Purge UOM stuff |
| TASK-2026-01239 | v1.259.2 | 2026-08-07 | #749 | Make UOM default Unit |
| TASK-2026-01240 | v1.259.3 | 2026-08-07 | #750 | Make all notification emails use group emails |
| TASK-2026-01243 | v1.260.3 | 2026-08-07 | #756 | CI runs the whole workflow twice per PR, and the two runs cancel each other… |
| TASK-2026-01268 | v1.270.0 | 2026-08-11 | #779 | Server-owned presence and focus, as a Redis heartbeat with an expiry |
| TASK-2026-01271 | v1.274.0 | 2026-08-11 | #782 | One @triton handler for both origins, acknowledging inside Chat's hard… |
| TASK-2026-01272 | v1.270.0 | 2026-08-11 | #779 | Suppression decided on the server by one pure function matching the decision… |
| TASK-2026-01275 | v1.264.0 | 2026-08-10 | #767 | SPA shell and the rooms list - DMs, group spaces and honest unread counts |
| TASK-2026-01276 | v1.262.0 | 2026-08-09 | #760 | Outbound relay state machine - per-space token bucket, retry, dead letter and… |
| TASK-2026-01279 | v1.273.0 | 2026-08-11 | #782 | One gated retrieval entry point that derives its own room set and filters… |
| TASK-2026-01282 | v1.264.0 | 2026-08-10 | #767 | Deep links that survive a hard refresh, and the bubble-to-SPA state handoff |
| TASK-2026-01284 | v1.262.0 | 2026-08-09 | #760 | Inbound ingest writes a raw event row, commits, then acks |
| TASK-2026-01287 | v1.264.0 | 2026-08-10 | #767 | Threaded replies in a side pane, shareable and one level deep |
| TASK-2026-01292 | v1.262.0 | 2026-08-09 | #760 | Echo suppression - client id first, resource name second, in-flight claim… |
| TASK-2026-01299 | v1.275.0 | 2026-08-11 | #782 | Hybrid ranking, and rolling digests that fully rebuild rather than patch… |
| TASK-2026-01301 | v1.262.0 | 2026-08-09 | #760 | Edit and delete both ways, with tombstones and one stated rule per conflict… |
| TASK-2026-01304 | v1.264.0 | 2026-08-10 | #767 | Presence, typing and read receipts - all sourced from ERPNext, never from… |
| TASK-2026-01309 | v1.273.0 | 2026-08-11 | #782 | The token budget ladder, and an assembly order the prompt cache can actually… |
| TASK-2026-01319 | v1.262.0 | 2026-08-09 | #760 | Subscription renewal, the reconciliation sweep, and the 200-message soak that… |
| TASK-2026-01325 | v1.262.0 | 2026-08-09 | #760 | Phase 2 schema - two new DocTypes, the policy fields, and the second index… |
| TASK-2026-01326 | v1.262.0 | 2026-08-09 | #760 | The fake Chat API and the pure decision modules - the only tier CI can… |
| TASK-2026-01327 | v1.262.0 | 2026-08-09 | #760 | Seams, realtime targeting and observability - wiring Phases 4 and 5 need in… |
| TASK-2026-01362 | v1.267.0 | 2026-08-10 | #774 | Post-pilot defect sweep - 13 confirmed UI and serialisation defects found… |
| TASK-2026-01364 | v1.270.1 | 2026-08-11 | #780 | Notification Log had no retention and had never been trimmed (CQ-21) |
| TASK-2026-01365 | v1.270.2 | 2026-08-11 | #781 | The two chat retention settings had never deleted anything |
| TASK-2026-01367 | v1.271.0 | 2026-08-11 | #782 | Shut the generic AI tools out of chat data - the surface no Frappe permission… |
| TASK-2026-01379 | v0.55.7 | 2026-08-11 | #319 | Kanban focus is lost for the duration of the save |
| TASK-2026-01382 | v0.57.0 | 2026-08-11 | #321 | Seed Roles + Permissions and add a grant endpoint |
| TASK-2026-01383 | v0.57.0 | 2026-08-11 | #321 | Introduce voice:read and re-gate the six voice routes |
| TASK-2026-01386 | v0.57.1 | 2026-08-11 | #322 | Make the ten composited wrappers theme-aware |
| TASK-2026-01387 | v0.57.1 | 2026-08-11 | #322 | Contrast test that composites the backdrop instead of reading the token |
| TASK-2026-01388 | v0.58.0 | 2026-08-11 | #323 | WeatherWidget city search - withdrawn, the branch was unreachable |
| TASK-2026-01393 | v0.59.0 | 2026-08-11 | #325 | /api/twiml/bridge - the low-risk assessment was wrong |
| TASK-2026-01411 | v1.272.0 | 2026-08-11 | — | Phase 5 schema - four new DocTypes, the dials, and the third index patch |
| TASK-2026-01418 | v0.58.1 | 2026-08-11 | #324 | Record the chat ownership split in the Triton repo - the cross-repo half |
| TASK-2026-01475 | v1.280.0 | 2026-08-13 | #805 | Ad spend data model: Ad Account / Ad Campaign / Ad Daily Metric + Marke |

### Superseded

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-00269 |  | summarize the child tasks (gantt view) | No changelog entry, commit or code comment anywhere in the repo references TASK-2026-00269, and the ERPNext record itself is already Canceled. The capability it asked for now exists as a property of the Gantt rewrite rather than as a discrete piece of work: api/gantt.py::_build_composite emits group rows and undated… |


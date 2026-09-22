# The Nik runbook — what only you can do

**Written 2026-08-14**, after reconciling all 149 open task records on PRJ-00580 and PRJ-00755
against the actual code on `main` in both repositories. **Re-reconciled 2026-08-15** across the
121 records still open after the first pass — see [§0](#0-what-changed-today-before-you-read-the-rest).

Everything in this file is something an agent **cannot** do: file an application with an external
platform, decide a policy, run a command against production, hold a phone, or be a person who is
not an engineer. The ordering is by **lead time, not by importance** — the items at the top have
queues measured in weeks and half of them gate the other half.

> Nothing here is a request for a status update. Each item names what to do, where, what must be
> true first, and how you know it worked.

> **2026-09-13 — the chat programme is withdrawn, and a large part of this runbook went with it.**
> v1.426.0 deleted the Google Chat integration *and* the ERPNext coworker chat product
> ([ADR 0011](../decisions/adr/0011-retire-google-chat-and-coworker-chat.md), which supersedes ADR
> 0009 and both its addenda). The chat items below have been **removed rather than ticked off** —
> the governance decisions, the four Phase 5 confirmations, the pilot walkthrough, the live Google
> round trip, the notification matrix, the chat bench suites, and their per-task residuals in
> [§5](#5-appendix--the-reconciliation-in-full). None of them can be done any more, and a runbook
> that keeps undoable items is a runbook that stops being read. **The Triton widget is untouched**
> — it was always a separate surface with a separate server side — so what is here about the
> deployed agents, the ERPNext link grant, the citation manifest and the widget's own sources row
> is still live and still yours. One chat item is *added* rather than removed: the Google-side
> estate is not deleted by the deploy and has to be torn down by hand, which is
> [§3.3](#33-tear-down-the-google-chat-estate--console-work-the-deploy-cannot-do).

---

## 0. What changed today, before you read the rest

*Updated 2026-08-15. Everything the earlier version of this section listed as open — erpnext
`#828`–`#831`, triton `#340`/`#343` — has since merged. `erpnext_enhancements` is on **1.299.0**,
`triton` on **0.68.2**.*

**Two PRs were open when this was written. Both have since merged.**

| PR | Version | What it is |
|---|---|---|
| [#853](https://github.com/sapphirefountains/erpnext_enhancements/pull/853) | 1.299.1 | The oversight viewer returned an **empty transcript** on every call — and wrote a full audit row saying it had not. The viewer it fixed was itself deleted in v1.426.0 |
| [#854](https://github.com/sapphirefountains/erpnext_enhancements/pull/854) | 1.299.2 | "Hide the activity feed on every tab but the first" has done **nothing** since v1.259.4, and its test passed the whole time |

`#854` was **stacked on** `#853` rather than independent only because both bump the version files,
which conflict on every line otherwise.

> Both were found the same way: **re-reading the code behind a task that claimed to be shipped.**
> Neither feature has ever been used in production, which is exactly why a silent failure in each
> could survive — six weeks in one case, four releases in the other. Both fixes ship with a guard
> that was **verified failing against the pre-fix code before being kept**, because the defect in
> both cases was a test that could not fail.

**The board was re-reconciled — 121 records, and this time the parents were checked too.**
Every non-Completed task on both projects was re-verified against the code, and every *shipped*
verdict was then handed to a second agent told to refute it. **Three of seven were overturned.**
The two that mattered most, though, were ones the refuter never looked at: it only attacked
`shipped`, and two *parent* verdicts proposed closing containers that still had open children —
Chat Phase 1 (one child, itself unfinished) and Chat Phase 2 (**six** open children, three of them
the production-evidence tasks that have never run). Both were rejected by querying the children
rather than reading the verdict.

**What that leaves is the honest shape of the work:** almost everything that is code is done, and
almost everything that is left needs production — a console, a deployed environment, a roster of
people to chase, or a decision only you can make. That is what the rest of this document is.

Parent progress figures are now a computed roll-up of each container's children (Completed counted
as 100), capped at 95 while any child is open — so a container can no longer read 99% because it
has a long tail of finished leaves and two real gaps.

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

### 1.3 Google Business Profile access — **check verification today; the 60 days is a prerequisite, not a queue**

**The clock runs on profile verification, not on your application.** Google will not grant API
access unless you have *already* managed a **verified, active Business Profile for at least 60
days** — so this is a wait you either finished two months ago or have not started.

That makes today's action a check rather than a filing:

- **every location already verified for 60+ days** → apply now; there is no wait at all;
- **any location unverified** → verifying it starts the 60-day clock, and only then can you
  apply. Nothing shortcuts it.

That is the only reason this sits in wave one; the GBP work itself is late in Phase 3. The
profile must also list an official website.

> This section previously read *"file today, because of a hard 60-day wait"*, which invites
> precisely the wrong action — filing an application that will be refused, rather than
> verifying the locations that start the clock. Corrected 2026-08-15.

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

#### 1.4.1 The procedure, in order — roughly 25 minutes

The order is the whole point: **the Community Management request must be the first product ever
requested on its app.** Everything below is a human action at developer.linkedin.com; nothing
here can be automated, and a rejection means starting the app again.

**Before you open the browser** — four things, because each has sunk a submission:

- [ ] **Super admin** on the Sapphire Fountains Company Page identified. Not admin. They must
      click the verification link, and the review checks for it.
- [ ] **Business email on the company domain.** Personal addresses fail vetting.
- [ ] **Privacy policy URL live** and describing the data LinkedIn will ask about. Meta and
      Google check it too, so this is worth doing once properly.
- [ ] **Two app names chosen** that contain no part of "LinkedIn" or "Microsoft" — LinkedIn's
      rule names the substrings **"Linked"** and **"In"**. Read that literally when naming:
      *Integration*, *Insights* and *Internal* all begin with "In". Safe pairs:
      `Sapphire Fountains Social` and `Sapphire Fountains Ads Reporting`.

**Then, in this order:**

1. **Create app A.** Associate it with the Sapphire Fountains Company Page. Do **not** request
   any product yet.
2. **On app A, request Community Management → Development Tier, first, before anything else.**
   This is the step the whole ordering exists for: the option is greyed out on any app that
   already has another API product.
3. **Have the super admin verify app A** against the Page. Check spam for the verification
   mail — it routinely lands there.
4. **Create app B**, separately, also associated with the Page.
5. **On app B, request the Marketing Developer Platform** (ads reporting). App B never touches
   Community Management, and app A never touches the ads product.

**Scopes to request on app A:** `w_organization_social` (post as the organisation) and
`r_organization_social` (read posts, comments, reactions).

**Do not request `r_member_social`** — LinkedIn's FAQ says it is a **closed permission** and
they are not accepting requests. Nothing here needs it: we post as the organisation, never as a
member. Asking for a closed permission is a way to have a submission refused on a scope we do
not want.

**What you get from Dev Tier:** 500 requests per app per day, 100 per member per day. Enough to
build against; Standard Tier comes later and needs a screencast of the finished integration,
which is why it is wave two rather than today.

**Record both client IDs** when you have them. They do not go in this repo — nothing under
`marketing/` stores platform credentials yet, and when that is built it must hold **two** sets
for LinkedIn (see the approvals doc §5a). Until then, keep them wherever secrets normally live.

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

Two are left, and both block code rather than a schedule. Each is a choice between options that
are already written out, not fresh thinking.

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

Every item here needs something a development machine does not have — a Google Cloud console, a
deployed environment, or a person. Naming them separately is what keeps the rest of the board from
filling up with unverified checkboxes. These are in dependency order.

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
**false-positive on user-typed prose** — someone pasting a SQL snippet or an HTML tag into a
question for Triton gets a 403 with no feedback. Attach in preview mode first.

### 3.3 Tear down the Google Chat estate — console work the deploy cannot do

v1.426.0 deleted the code, and `patches/delete_chat_module.py` deletes the database residue on the
next `bench migrate`. **Neither touches Google.** What is still standing in `erpnext-465317` is the
Chat app registration, one Workspace Events subscription per coworker, the Pub/Sub topic and its
subscription, the domain-wide delegation grant, and the `serviceAccountTokenCreator` binding that
made the keyless design work. None of it is under Terraform — `grep -ril chat infra/ modules/`
returns nothing — so `terraform plan` shows no drift before or after, and will not do this for you.

The ordered checklist is [`docs/google-chat-teardown.md`](google-chat-teardown.md). Three things
from it are worth knowing before you open a console:

- **The resource names were only ever written down in `Chat Settings`.** The patch prints every
  Google identifier it held into the deploy log, under `delete_chat_module: Google resources this
  integration named`, immediately before deleting the rows. Capture that while the log still
  exists; the original provisioning runbook is not in this repository.
- **Two projects, easy to confuse.** The Chat estate is in `erpnext-465317`. Triton's Drive picker
  is in `triton-497321` and is **not part of this teardown** — deleting the wrong project's OAuth
  client breaks the Drive attach button in the widget this whole change exists to keep.
- **Two steps are shared with things that still work.** The delegation service account may also
  sign for Drive and Calendar, so remove the `chat.*` scopes rather than the entry; and
  `iamcredentials.googleapis.com` stays enabled for the same reason.

On the ERPNext side there is exactly one manual item: remove `chat_vapid_private_key` and
`chat_vapid_public_key` from `site_config.json`. They are the Web Push keypair for the coworker
product, nothing in the code will clean them up, and they will otherwise sit in every backup.

### 3.4 The roster chase — an afternoon, but only if someone schedules it (TASK-2026-01267)

**Everyone Triton may answer for must have clicked "Link ERPNext" once, or the turn dies before it
starts.** Auto-provisioning somebody a Triton account does *not* auto-provision their ERPNext grant.
Across the full staff roster that is on the order of fifty people.

Left until people are using the widget in anger, it presents as an intermittent bug that only
affects some of them — which is the most expensive way to discover it. Enumerate the roster, check
each account for a grant, chase the remainder, and **decide the fallback for an unlinked user**: a
mandatory link prompt, a degraded answer without ERPNext tools, or an explicit refusal.

### 3.5 Marketing — two production actions

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
which is where they actually belong — most of them were chat, and went with it on 2026-09-13.

---

## 5. Appendix — the reconciliation in full

One row per task. **Verdict** is what the evidence supported after an adversarial second pass over
every "shipped" claim; **11 of those claims were overturned** and are marked `↺`.

> **Thirty-five rows were removed on 2026-09-13** — every open chat task, across *Partial*, *Human
> action* and *Not started*. They named residual work on a product that no longer exists, so there
> is no verdict left to record and nothing a reader could act on; the tasks themselves are in
> ERPNext and the reasoning is in [ADR 0011](../decisions/adr/0011-retire-google-chat-and-coworker-chat.md).
> The counts above describe the original pass, not the rows still printed here. *Shipped* was left
> whole on purpose — see the note under it.

### Partial — shipped in part, with named residual work

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-00353 | ↺ | Hide Activity | **Fixed in PR #854, unmerged.** The feature shipped in v1.259.4 and never worked: the hide rule at `desk_enhancements.bundle.css:1378` had no `!important`, and the older "Restore Missing Comment Box" block at `:1152` declares `.form-footer { display: block !important }` — an important declaration beats a non-important one regardless of specificity, so the footer was never hidden on any form. The test asserted the rule's *text* was present, which it was, so it passed for six weeks. `#854` adds the `!important` and replaces the string check with one that resolves the cascade. |
| TASK-2026-01157 | ↺ | Supervisor sign-off, ask-the-author Q&A, gamification &… | Learner-facing surfaces for two strands were never built. (1) Ask-the-author Q&A: add ask_question and get_lesson_questions to the METHOD map in www/training.html and build the lesson-level ask/read UI in public/js/training/player.js — today training/qa.py's three whitelisted functions have no caller in the app, so no… |
| TASK-2026-01191 | ↺ | Triton: deployed ADK agents cannot name a single ERPNext… | Run `python -m scripts.deploy_agents` (all 13 Agent Engine agents) from backend/ so the committed 53-tool snapshot actually reaches the deployed agents, then confirm one deployed agent can name e.g. fac_training_compliance_status. Neither CI nor the VM deploy performs this step, and there is no record it has been done… |
| TASK-2026-01241 |  | Lint backlog: 433 ruff findings keep the lint job advisory | Run the `ruff format` pass (457 files) as its own PR at a quiet moment; clear or consciously ignore the remaining 152 `ruff check` findings; then delete `continue-on-error: true` from the lint job in .github/workflows/ci.yml and refresh the stale comment above it that still says 73. |
| TASK-2026-01310 | ↺ | Search that cannot leak a room you are not in, and the… | Move the Desk Triton bubble's full-screen-sheet breakpoint in erpnext_enhancements/public/css/global_enhancements/triton_widget.css from max-width:480px to ~767px (both the .triton-panel block at line 576 and the header-picker block at line 1244), and perform the on-handset check that the composer stays above the… |
| TASK-2026-01315 |  | Inline citations added without regressing the sources chip… | Nothing emits a `citations` SSE frame. The Desk widget already handles `citations` and `citations_append` events and reads meta.citations off `done`, but triton_chat.stream_query only forwards Triton's stream verbatim and the Triton backend (app/api/v1/endpoints/streaming.py, app/core/intelligence.py) emits `sources`… |
| TASK-2026-01318 | ↺ | The Triton surface - inline citation links added without… | No producer emits a `citations` / `citations_append` SSE event on the Desk Triton widget's stream, so live.manifest stays null and applyCitations is a permanent no-op there; and no stored turn carries meta.citations. Wire the widget's backend turn to emit the manifest (the work tracked as TASK-2026-01315) before this… |
| TASK-2026-01381 |  | Withdraw lead-time modelling (done); decide on the open-PO… | Someone in procurement must confirm the open-PO overdue list is real before anything is built on it: 71 overdue lines across 5 projects (PRJ-00566 38 lines / oldest 306d, PRJ-00567 22 / 67d, PRJ-00219 5 / 235d, PRJ-00694 4 / 74d, PRJ-00438 2 / 2d). If it is noise — every open project line being overdue may just mean… |
| TASK-2026-01385 | ↺ | Cookie-bound nonce for the OAuth state parameter | Single-use is only enforced on the success path. Clear the state cookie on rejection too — either by catching the 400 in google_callback and returning a response that carries response.delete_cookie(_OAUTH_STATE_COOKIE, path='/'), or by deleting it in the StarletteHTTPException handler for this route. Then make… |
| TASK-2026-01389 | ↺ | aria-expanded on the four remaining disclosure controls | frontend/src/views/ChatView.vue:696 — the 'View Proposed Plan Parameters' details-accordion-toggle button still has no aria-expanded and no aria-controls, while the region it controls (the v-if at line 712) has no id. Give the button :aria-expanded="!!msg.ui_metadata._details_open" and an aria-controls pointing at a… |
| TASK-2026-01391 |  | Deployed Vertex Agent Engine returns "0 events received" | Production remediation, all outside the repo: delete reasoning engine 7827804258916368384; run python -m scripts.deploy_agents --agent research (it will report created); put the new full projects/.../reasoningEngines/... path into REASONING_ENGINE_RESEARCH_RESOURCE in Secret Manager; run sudo… |
| TASK-2026-01471 |  | Fluent Forms Pro → submit_web_lead, plus the first-touch UTM… | On WordPress: install the mu-plugin, create the hidden attribution fields plus the hp_company_url honeypot in each Fluent Forms builder, and add a webhook per form posting to submit_web_lead with the bearer header. In ERPNext: set web_lead_shared_secret and web_lead_default_owner, then enable web_lead_ingress_enabled.… |
| TASK-2026-01479 |  | marketing/ module scaffold + Module Def patch + Marketing… | Still to build: core/{client,api,constants,utils,tasks,oauth}.py, the platforms/ and publish/ packages, the top-level api.py re-export that keeps registered webhook URLs on a stable short path, and the scheduler entries — hooks.py currently registers no marketing scheduler_events, which is exactly the 'added later, by… |
| TASK-2026-01501 |  | Two live URL sinks in the widget's own sources row | A server-authoritative safe_url — the client-side check in `public/js/triton/citations.js` is a rendering decision, not a boundary. Also still true: no Content-Security-Policy anywhere in the app. The chat export that would have carried the same renderer into a downloadable file is gone, so this is now about the widget's own sources row and nothing else. |

### Human action — not code

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-01150 |  | SPIKE: GCS signed-URL video pipeline — infra applied, 206… | Run the one-time operator procedure: confirm `terraform apply` covers infra/storage.tf with enable_training_media_bucket on, create the sa-training-media JSON key by hand and paste it into Training Settings via the key dialog (never into the field or Terraform state), then execute Test GCS Connection on prod and… |
| TASK-2026-01267 |  | Everyone Triton may answer for must link ERPNext first, or… | Run erpnext_link_report on a bench against the live roster, then chase the ~50 people it lists to click Link ERPNext once, with a named owner and a scheduled afternoon. Left until people are using the widget in anger, this presents as an intermittent bug affecting only some of them. |
| TASK-2026-01384 |  | agent_user is NULL for inbound, missed and voicemail rows | Decide explicitly how unattributed call rows are surfaced if per-agent scoping is ever adopted — an agent seeing a partial archive with no indication that rows are missing is worse than the current role-based all-or-nothing. Only after that is settled can the scoping approach be weighed against the role approach that… |
| TASK-2026-01390 |  | Orphaned Cloud Run load balancer stack - decide Cloud Armor… | Decide whether the Cloud Armor policy on the orphaned triton-backend-service should be reattached to the live triton-backend-svc or deliberately dropped; then delete backend service triton-backend-service, url map triton-load-balancer and NEG triton-frontend-network-endpoint-group, and tick the box in… |
| TASK-2026-01392 |  | Cloud SQL triton-main-db - verify existence and cost | Confirm whether triton-497321:us-central1:triton-main-db still exists, whether anything connects to it, and what it costs; if it is an orphan, fold it into the load-balancer teardown decision (TASK-2026-01390) and update deploy/MIGRATION.md section 8. |
| TASK-2026-01465 |  | Meta App Review - publishing + ads_read in one submission | Complete Meta Business Verification first (company documents only). Pre-flight before submitting: confirm the Instagram account is a Business account, not Creator or personal, and that it is linked to the Facebook Page inside Business Manager - without that link IG publishing is impossible regardless of approval. Then… |
| TASK-2026-01466 |  | LinkedIn Community Management API access (organic publishing) | Confirm who holds LinkedIn Page super admin; create a brand-new app; file Development Tier, build against it, then file Standard Tier with the required screencast. Update the status table in docs/marketing-platform-approvals.md as each moves. |
| TASK-2026-01467 |  | LinkedIn Marketing Developer Platform access (ads reporting) | File the Advertising API request from a second LinkedIn app and track it in the status table. No code is blocked on it today. |
| TASK-2026-01468 |  | YouTube Data API audit — lifts the private/unlisted upload… | Answer whether the YouTube channel is a Brand Account and who owns it, then file the audit form. Separately, the authorization-code OAuth flow it implies is TASK-2026-01480 and is not started. |
| TASK-2026-01469 |  | Google Ads developer token (basic access, from an MCC) | Decide which MCC holds the token, apply for Basic Access from its API Center, and record the outcome in the status table. |
| TASK-2026-01470 |  | Google Business Profile API access request | Confirm the GBP locations are verified and 60+ days old, file the access-request form, then watch the Cloud Console quota for 0 → 300 QPM. |
| TASK-2026-01472 |  | Enable lead attribution, in the runbook's staged order | In ERPNext Enhancements Settings, tick lead_attribution_enabled and leave it for a week; then require_lead_source_on_opportunity; then require_lead_source_on_lead. Monitor the Attribution Gaps report between steps — its blank/red rows are live process failures, the 'Unknown (pre-Aug 2026)' bucket is expected history. |
| TASK-2026-01474 |  | Fix the Search Console 403 — organic has read 0 for the… | Identify the Search Console property administrator; add the GA4/GSC service account as a user on the property; confirm the property type (sc-domain: vs URL-prefix) matches what gsc_property_url requests; then backfill the affected range so the historical zeros stop reading as real organic figures. |
| TASK-2026-01478 |  | Security pre-flight: verify the X-Forwarded-For chain before… | **Already fixed on the VM 2026-08-03** (`/etc/nginx/conf.d/00-realip.conf`); codified in the repo and guarded by a daily check in v1.500.0. Only left for you: the next time Terraform is applied, confirm the VM's startup script picked up the realip install block, and after any VM rebuild run `bench --site erp.sapphirefountains.com execute erpnext_enhancements.utils.client_ip.check_client_ip_derivation` (expect `"proxy": 0`). |
| TASK-2026-01500 |  | Verify Triton deploy: import check locally, confirm rollback… | After the call-tracking work lands: run `python -c "import app.main"` locally; confirm a failed deploy still auto-rolls-back; and confirm the Twilio number-pool credentials and the hard provisioning cap are present in the deployed environment, not just in a local .env. |

### Not started

| Task | ↺ | What it was | Where it actually stands / what is left |
|---|---|---|---|
| TASK-2026-01194 |  | Triton: agents, personas and domain guides for training,… | Everything: the DOMAIN_GUIDES dict keyed by pack name (moving FOUNTAIN_DESIGN_GUIDE into it under 'water' in the same change), the four guides (training, contracts, finance-close, procurement), the three personas (training_officer, contract_clerk, buyer) plus extending bean_counter to carry the finance pack, the four… |
| TASK-2026-01195 |  | Triton: convergence.md has no row for Training, Contracts,… | Add ownership-matrix rows for: Training/LMS; Contracts + e-signature; KPI dashboards (ERPNext against Triton's kiosks); Gantt (ERPNext api/gantt.py against Triton's DynamicGantt); Google Drive; and the call-intelligence READ-UI overlap (Triton /voice/analytics against ERPNext's Call-Center desk - data ownership is… |
| TASK-2026-01417 |  | text-sapphire-light fails contrast on light panels (~2.4:1) | Pick one of the two options (light-mode darker variant of the accent, or a role token that resolves per theme), apply it, then delete KNOWN_FAILING_FG and the 'is still failing' test from frontend/e2e/contrast.spec.ts and confirm the four view scans stay green with the allowlist gone. |
| TASK-2026-01473 |  | Make the Lead stage real — owner, triage queue, speed-to-lead… | None of this task's deliverables landed. What exists is the inputs the task itself names as already built: get_speed_to_lead in api/sales_dashboard.py (a dashboard widget listing leads with no Sent Communication — a list, not an SLA with a threshold or an alert), attribution.propagate_to_opportunity, and web_lead.py's… |
| TASK-2026-01476 |  | Nightly ad-spend connectors: Google Ads, Meta Ads, LinkedIn… | No connector code exists. erpnext_enhancements/marketing/ contains only __init__.py, README.md and doctype/ — there is no core/ package, no client.py, no platforms/ directory and no tasks.py. hooks.py registers no marketing scheduler_events and no marketing doc_events; its only marketing entry is the Marketing… |
| TASK-2026-01477 |  | The join that justifies the project: spend → Lead →… | The join does not exist. Nothing in the repo reads Ad Daily Metric except the module's own doctype code — the only other mention anywhere is a docstring in tests/test_marketing_settings.py. What exists is a different, older thing that should not be mistaken for it: kpi_dashboards/report/marketing_spend_rollup computes… |
| TASK-2026-01480 |  | Per-platform OAuth: start / callback / refresh / disconnect | No OAuth code exists for marketing. A case-insensitive grep for 'oauth' across erpnext_enhancements/marketing/ returns nothing, and the only start_oauth / oauth_callback / disconnect_callback in the whole app are the QuickBooks ones in quickbooks_online/core/api.py — the very pattern this task says to copy. Marketing… |
| TASK-2026-01481 |  | Publishing doctypes + the outbox and its sweeper | No code on main. The marketing module contains only the Phase 1 ads data model (Ad Account, Ad Campaign, Ad Daily Metric, Marketing Raw Payload, Marketing Settings, Marketing Sync Log). None of the six publishing doctypes exist -- a repo-wide search for 'Social Account', 'Social Post', 'Social Post Target', 'Social… |
| TASK-2026-01482 |  | Rate limiter + backoff: pure decision functions, Redis Lua… | No code on main. There is no marketing/publish/ratelimit.py and no marketing backoff module; the marketing package is doctype/ plus README only. The two source files this task says to copy -- chat/sync/ratelimit.py and chat/gchat/backoff.py -- went with the chat module in v1.426.0, so the template now has to be read out of git history (`git show v1.423.0:erpnext_enhancements/chat/sync/ratelimit.py`) rather than off the tree. |
| TASK-2026-01483 |  | Meta publisher — Facebook Pages + Instagram content publishing | No code on main. There is no marketing/platforms/ directory and no meta.py; no Facebook Pages or Instagram Content Publishing client exists anywhere in the repo. No per-network validation (aspect ratio, caption length, hashtag limits) has been written. The only Meta material in the repo is planning prose in… |
| TASK-2026-01484 |  | LinkedIn publisher — organization posts | No code on main. No marketing/platforms/linkedin.py, no organization-URN post client, no image/video upload registration step. Nothing in the repo references LinkedIn outside the two marketing planning docs. Still blocked on Community Management API approval; note the filing constraint recorded in CHANGELOG v1.278.4… |
| TASK-2026-01485 |  | YouTube uploader — resumable upload, quota-aware queue | No code on main. No marketing/platforms/youtube.py, no resumable-upload implementation, no quota accounting, and no OAuth token storage for a channel owner. The GCS pattern the task says to reuse does exist at training/gcs_media.py. Two corrections for whoever builds this, from CHANGELOG v1.278.4: an upload costs 1… |
| TASK-2026-01486 |  | Draft → approve → publish workflow + marketing roles | No code on main. There is no Social Post doctype for a workflow to attach to, so the draft/approve/publish workflow cannot and does not exist -- fixtures/workflow.json carries no marketing workflow, and there are no approver or approval-timestamp fields anywhere. On roles: a 'Marketing Manager' role already exists and… |
| TASK-2026-01487 |  | /marketing SPA — calendar, composer, media picker, approval… | No code on main. erpnext_enhancements/www/ contains no marketing.py and no marketing.html; hooks.py website_route_rules has exactly one entry, and since v1.426.0 removed /chat it is for /feedback. No marketing bundle exists -- the only marketing-named JS in the repo is the four pre-existing KPI custom_html_blocks widgets and the Marketing Spend… |
| TASK-2026-01488 |  | Engagement metrics pull-back — close the loop from post to… | No code on main. The Social Post Metric doctype this task writes into does not exist, and neither does any nightly engagement pull -- hooks.py registers no marketing scheduler_events at all. The upsert-on-restate convention the task says to mirror is implemented for paid ads (marketing/doctype/ad_daily_metric with its… |
| TASK-2026-01489 |  | Field photo capture → content pipeline (Time Kiosk +… | No code on main for the marketing extension. The base this task builds on is real and unchanged: api/time_kiosk.record_job_photo (line 696) still keys on the device-minted client_uid, and workforce/photo_routing.py (WP-3, pre-existing) already copies a job photo onto the Project and tags it cust:/vs:/shot:. But none… |
| TASK-2026-01490 |  | Index Drive project folders into Marketing Media Asset | No code on main. The Marketing Media Asset doctype does not exist, so there is nothing to index into, and no indexing job of any kind has been written. The google_drive module and its per-project folder provisioning are unchanged, and the scar the task warns about is still intact and must stay that way:… |
| TASK-2026-01491 |  | AI caption and reply drafting via Vertex (drafts only, into… | No code on main. No caption drafting and no GBP review-reply drafting exists. The infrastructure the task reuses is all present -- api/gemini.py, the existing email/SMS reply drafting in api/communication.py, and the ai_governance module with its doctypes and tasks.py -- but nothing marketing-related calls into any of… |
| TASK-2026-01492 |  | Campaign landing pages + UTM link builder | No code on main. There are no campaign landing pages in erpnext_enhancements/www/ and no UTM link builder or short-link minting anywhere -- a repo search for a link builder now returns nothing at all, chat/links.py having gone with the chat module in v1.426.0. The precedent the task names is present and is the right template:… |
| TASK-2026-01493 |  | Newsletter + SMS nurture on Frappe Newsletter and the… | No code on main. Nothing in the app touches Frappe Newsletter or Email Group -- the only two 'newsletter' hits in the codebase are string literals in channel-classification lists (crm_enhancements/attribution.py:124 EMAIL_MEDIUMS, kpi_dashboards/marketing_spend_import.py:55), not integration code. No segmentation… |
| TASK-2026-01494 |  | Marketing consent model + SMS STOP handling (TCPA) | No code on main, in either repo. There are no marketing email or SMS opt-in fields on Contact -- the sole 'unsubscribed' hit in fixtures/custom_field.json is an insert_after anchor referencing a stock Frappe field, not a consent field of ours. No source/timestamp/IP consent evidence is captured, no global unsubscribe,… |
| TASK-2026-01495 |  | Google Business Profile — reviews, AI-drafted replies, local… | No code on main. No marketing/platforms/gbp.py, no review ingestion or alerting, no local posts, and no automatic review requests after a completed Project or maintenance visit. Every GBP mention in the repo is prose -- docs/marketing-platform-plan.md, docs/marketing-platform-approvals.md, and the CHANGELOG v1.278.4… |
| TASK-2026-01496 |  | Receive attributed inbound calls and stamp campaign… | No code exists for this. api/telephony.py has no endpoint that receives a tracking-number-derived campaign, never imports crm_enhancements.attribution, and nothing in the repo maps a phone number to a campaign (the only 'tracking_number' hits are shipping tracking in package_dispatch). attribution._fill_blanks still… |
| TASK-2026-01498 |  | Tracking number pool: provision, assign to campaign, release | Triton (0.67.8) has a Twilio voice gateway — telephony_gateway.py, voice.py, the TWILIO_* settings — but no number pool of any kind: no provision/release calls, no purchase cap, no campaign-to-number assignment, and the word 'campaign' appears nowhere in backend/app outside the ADK agent definitions. There is no… |
| TASK-2026-01499 |  | Inbound call webhook — report the attributed call to ERPNext | No inbound handler for a pool number exists in Triton, and nothing reports a dialled tracking number, disposition or call SID to ERPNext for attribution. Both ends are missing: the sender here, and the ERPNext receiver (TASK-2026-01496). The idempotency requirement (unique index on the Twilio call SID) has no table to… |

### Shipped — verified in code on `main`, not just in a changelog line

> Two dozen of these rows are chat, and they stay. This is a dated ledger of what was verified in
> code on 2026-08-15, and each row was true when it was written; deleting them would make the
> reconciliation look like it found less than it did, and would quietly erase the record of what
> v1.426.0 cost. **Read every chat row below as history rather than as inventory** — the feature it
> names shipped, and was then removed by [ADR 0011](../decisions/adr/0011-retire-google-chat-and-coworker-chat.md).

| Task | Version | Released | PR | What it was |
|---|---|---|---|---|
| TASK-2026-01511 | v0.68.0 | 2026-08-14 | [#340](https://github.com/sapphirefountains/triton/pull/340) | Triton: surface the cached-token split the cost dashboard needs — verified across **all three** answer routes (assistant, streaming, research), each writing `cached_tokens` rather than a silent zero, plus the `ALTER TABLE … ADD COLUMN IF NOT EXISTS` that ADR-0002 requires. The ERPNext half landed too (`triton_client.py:202`, v1.288.2). |
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


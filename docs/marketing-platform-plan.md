# Marketing platform — build plan

A `marketing/` module covering social publishing, paid-ads attribution, demand capture and
content supply. This document is the plan of record; the work is tracked as tasks under
**TASK-2026-00866 "Marketing Tools"** on **PRJ-00580**, with Triton-side work on **PRJ-00755**.

Decisions were taken on 2026-08-13. They are recorded in [Decisions](#decisions-taken) below
so a future reader does not have to re-litigate them.

---

## The finding that shapes everything

Two marketing work packages already shipped — WP-1 lead attribution (v1.241.0) and WP-4
marketing spend (v1.243.0). Both are **switched off and starved of input**. Measured against
production on 2026-08-13:

| What exists | Live state |
|---|---|
| `lead_attribution_enabled` | `0` — the entire WP-1 feature is off |
| `web_lead_ingress_enabled` / `web_lead_shared_secret` / `web_lead_default_owner` | off / unset / unset |
| `submit_web_lead` endpoint | never called — the WordPress capture script was never written |
| Leads carrying `custom_utm_source` | **0 of 225** |
| Opportunities carrying `custom_gclid` | **0 of 819** |
| Opportunity `custom_lead_source` | 809 of 819 are the `Unknown (pre-Aug 2026)` backfill bucket; ~8 carry a real source |
| `Marketing Spend` rows | **0, ever.** The channel-spend and cost-per-lead widgets render nothing |
| `Marketing Web Snapshot` (GA4) | working — 48 snapshots, current |
| Search Console | HTTP 403 on every run since 2026-06-26; organic clicks have always been 0 |
| Leads created since 2026-08-01 | **0**, against 23 Opportunities in the same window |

So this is not a greenfield build. **Phase 1 is not new features — it is connecting pipes
that already exist to inputs that do not.** Building a publishing tool first would produce
posts that still could not be attributed to anything.

That last row is the sharpest one: sales works Opportunities directly and never creates a
Lead. An attribution model that hangs off `Lead` currently has nothing to hang on. The
decision taken was to **keep the Lead stage and fix the process**, because lead-to-opportunity
conversion and contact rate cannot be computed without it — but that makes the process work
in Phase 1 load-bearing, not optional.

---

## The critical path is approvals, not code

Every platform gate below has a lead time measured in weeks and none of them can be
compressed by writing code faster. **These start on day one, in parallel with Phase 1.**

| Gate | For | Notes |
|---|---|---|
| Meta App Review — `pages_manage_posts`, `pages_read_engagement`, `instagram_content_publish`, `instagram_manage_insights`, `ads_read` | FB + IG publishing, Meta ads reporting | Request the ads and publishing scopes in **one** submission. Requires Business Verification first. |
| LinkedIn Community Management API (`w_organization_social`, `r_organization_social`) | LinkedIn publishing | App must be verified against the Company Page by a Page admin. |
| LinkedIn Marketing Developer Platform | LinkedIn ads reporting | Separate application, historically the slowest of the set. |
| YouTube Data API audit | YouTube uploads | Until the project passes audit, API-uploaded videos are locked to private/unlisted regardless of the `privacyStatus` requested. |
| Google Ads developer token (basic access) | Google Ads reporting | Applied for from a manager (MCC) account. |
| Google Business Profile API access | GBP reviews + posts | Access-request form, reviewed by Google. |
| Search Console property grant | fixes the standing 403 | Not an approval — a config grant. See Phase 1. |

Confirm each platform's current quota and scope names at build time; these change without
notice and the numbers below are the shape of the constraint, not a contract.

**Quotas that constrain the design, not just the runtime:**

- **Instagram content publishing: 25 posts per rolling 24 h, per IG account.** This is a
  product constraint, not just a retry concern — the calendar must surface remaining quota.
- **YouTube Data API: 10,000 units/day, and a video insert costs 1,600.** That is roughly
  six uploads per day for the whole app. Uploads must be queued and quota-aware.
- **Meta** returns `X-App-Usage` / `X-Business-Use-Case-Usage` headers; treat them as the
  authoritative signal and back off on them rather than guessing.

---

## Auth: what can be keyless and what cannot

`chat/gchat/auth.py` is the house reference for a new Google integration — keyless
domain-wide delegation via `projects.serviceAccounts.signJwt`, no private key on disk. It
does not apply to most of this work, and the reason is worth stating plainly:

- **A service account cannot own or post to a YouTube channel, a Google Ads account, or a
  Google Business Profile location.** Those are user-owned assets. They need
  authorization-code OAuth with a stored refresh token.
- Only **GA4 and Search Console** work service-account style, and both already do.

So the auth pattern here is **QuickBooks, not Chat**: `start_oauth` with a cached one-time
CSRF `state`, a guest `oauth_callback`, tokens in `Password` fields on the module Single
accessed only through `core/utils.get_secret` / `set_secret`, proactive hourly refresh plus
reactive refresh on a 401 retry, and `invalid_grant` clearing the tokens and marking the
connection dead rather than erroring on every run.

Meta, LinkedIn, YouTube, Google Ads and GBP each get their own token set and their own
refresh cadence. Token lifetimes differ per platform (Meta Page tokens derived from a
long-lived user token, LinkedIn's 60-day access / 365-day refresh, Google's non-expiring
refresh tokens) — the refresh job must be per-platform, not one shared timer.

> **Never let a token reach the Error Log.** Every raise out of these clients uses
> `raise … from None`. This app has already published private key material by letting a
> background job re-raise with frame locals intact.

---

## Module shape

One new Frappe module, `marketing/`, following the house layout used by `quickbooks_online`,
`stripe_payments` and `plaid_banking`:

```
marketing/
├── core/
│   ├── client.py        ← shared REST transport (requests, no SDK), backoff, quota headers
│   ├── api.py           ← @frappe.whitelist() RPC + permission gate
│   ├── constants.py
│   ├── utils.py         ← settings, get_secret / set_secret
│   ├── tasks.py         ← scheduler shims: check switch, self-throttle, enqueue onto `long`
│   └── oauth.py         ← start_oauth / oauth_callback / disconnect, per platform
├── platforms/
│   ├── meta.py          ← FB Pages + IG Content Publishing + ads_read insights
│   ├── linkedin.py      ← UGC posts + ads reporting
│   ├── youtube.py       ← resumable upload + Analytics
│   ├── google_ads.py    ← read-only campaign + daily metrics
│   └── gbp.py           ← reviews, replies, local posts
├── publish/
│   ├── outbox.py        ← Social Publish Job state machine
│   ├── sweeper.py       ← re-drives Pending past available_at; reclaims expired leases
│   └── ratelimit.py     ← pure decision functions + Redis Lua, per chat/sync/ratelimit.py
├── attribution/         ← the spend ↔ pipeline join
├── doctype/
└── README.md
```

### Traps this module must not walk into

All of these are documented elsewhere in the repo and all of them have bitten before:

1. **`modules.txt` is not enough on an installed site.** Adding the module to the file does
   not create the `Module Def` row on production. That needs a patch.
2. **A `default` on a new field of a Single never reaches the existing row.** `Marketing
   Settings` ships with a large number of flags and thresholds; **it must ship with a
   backfill patch in the same PR**, modelled on
   `patches/backfill_chat_settings_defaults.py`. This is exactly the dormant-feature shape
   that made Chat Settings unsaveable in v1.277.3 — the settings page nobody has opened yet
   is the one where the first save is the one that matters and the one that fails.
3. **The production deploy runs `redis-cli FLUSHDB`,** destroying every queued-but-unrun job,
   and Frappe v16 wires no RQ retries. `frappe.enqueue` reaches no RQ scheduler, so
   **`available_at` + a cron sweep is the defer timer** — a scheduled post held in the queue
   would silently vanish on the next deploy. The outbox + sweeper is the delivery guarantee.
4. **A duplicate cron key in `scheduler_events` silently replaces the earlier entry.**
   `tests/test_hooks_integrity.py` exists because this nearly deleted two live QuickBooks
   jobs. New cron entries go in at unused minutes.
5. **A `www/` controller whose filename contains a hyphen is never imported.** The SPA shell
   is `www/marketing.py`, never `www/marketing-app.py`.
6. **Global assets ship as esbuild bundles**, never raw `/assets` paths — `/assets` is served
   immutable for a year with no content hash.
7. **Do not write to erpnext's `utm_source` / `utm_medium` / `utm_campaign`.** `utm_source`
   is reserved for stray-Contact suppression and the other two are Links that would spawn
   junk taxonomy. Raw values go in the `custom_utm_*` Data fields.
   `attribution._fill_blanks` is the single writer and it is first-touch-wins.
8. **Read `custom_lead_source`, never `source`.** `Lead.source` / `Opportunity.source` have
   had no DocField since erpnext v15; the column survives and reads as a frozen pre-2023
   snapshot. Four KPIs were measuring it. A test AST-parses `snapshots.py` to stop it
   happening again.

---

## Phase 1 — Foundation: make the numbers real

Goal: every figure on the existing Marketing workspace becomes true, and a paid click can be
traced to a signed project. No new UI.

### 1.1 Website capture (the highest-value item in the whole plan)

The site is WordPress on WP Engine behind Cloudflare; forms are **Fluent Forms Pro**, which
ships a native Webhook integration supporting custom request headers. So the ERPNext half
needs no new code — it needs turning on and wiring.

- A first-touch UTM snippet (mu-plugin or Code Snippets) that on entry reads `utm_*`,
  `gclid`, `document.referrer` and the landing path into a **first-party cookie**, and
  **never overwrites a non-empty value** within the session. Cookie, not `localStorage` — it
  must survive a subdomain hop.
- Hidden fields on each Fluent Form populated from that cookie, plus the `hp_company_url`
  honeypot.
- A Fluent Forms Pro webhook per form → `POST /api/method/…web_lead.submit_web_lead` with
  `Authorization: Bearer <web_lead_shared_secret>`, mapped to the payload contract in
  [attribution-runbook.md](attribution-runbook.md).
- Set `web_lead_shared_secret` and `web_lead_default_owner`; enable `web_lead_ingress_enabled`.
- **Never send a field named `sid`** — frappe pops it during auth and the request silently
  downgrades to Guest.
- Decide what the form does when ERPNext is unreachable. The endpoint is not a queue; Fluent
  Forms keeps its own entry, and that copy is the fallback.

### 1.2 Turn on attribution, in the runbook's order

`lead_attribution_enabled` alone first, left for a week so capture and propagation run with
nothing blocked. Then `require_lead_source_on_opportunity`. `require_lead_source_on_lead`
last. The master switch is a hook and not `reqd = 1` precisely so that one tickbox unblocks
the sales team mid-day.

### 1.3 Make the Lead stage real

The decision was to keep Lead and fix the process, which means this is a prerequisite for
the funnel metrics, not a nice-to-have:

- Web submissions land as Leads with an owner and a triage queue.
- Speed-to-lead SLA and alerting — `api/sales_dashboard.py` already computes speed-to-lead.
- A documented qualification path Lead → Opportunity, with attribution propagating (already
  built in `attribution.propagate_to_opportunity`).
- Process documentation + training for sales. If nobody owns the Lead queue daily, this
  reverts and the funnel metrics stay uncomputable.

### 1.4 Fix the Search Console 403

GSC has failed 40/40 days. It is a Google-side grant, not a code bug: the service account is
not a user on the property, and/or a `sc-domain:` property is being requested as a URL prefix.
Add the SA to the property, confirm the property type matches the request, and backfill.

### 1.5 Ad spend: campaign × day, auto-pulled

New model — `Marketing Spend` stays for offline spend (trade shows, print, sponsorship) and
both roll into one report:

| DocType | Purpose |
|---|---|
| `Ad Account` | platform, external id, currency, connection status, cursors |
| `Ad Campaign` | ad account, external id, name, objective, status, dates |
| `Ad Daily Metric` | campaign × date; impressions, clicks, spend, conversions. Unique on (campaign, date) so a re-pull is an upsert, not a duplicate |
| `Marketing Sync Log` | per run: platform, type, status, counters, retry count |
| `Marketing Raw Payload` | append-only verbatim archive |
| `Marketing Settings` | Single: master switch, per-platform flags, cursors, thresholds — **plus its backfill patch** |

Nightly pulls for Google Ads, Meta Ads and LinkedIn Ads, read-only scopes only. Restate the
last N days on every run because platforms revise conversion and spend figures after the
fact; the unique key makes that safe. The cursor advances only on a clean run.

### 1.6 The join that justifies the project

`Ad Daily Metric` → `custom_utm_campaign` / `custom_gclid` on Lead → Opportunity → Project →
Sales Invoice, producing cost per lead, cost per won project, and ROAS by campaign against
**booked revenue**, not platform-reported conversions. This is the thing no off-the-shelf
tool can do for you, because the revenue side already lives here.

Attribution model: first-touch is what `attribution.py` implements today. Position-based or
last-touch comparisons are a reporting-layer concern; do not change the capture semantics.

### 1.7 Verify the `X-Forwarded-For` chain

`auth.py` takes the first entry of `X-Forwarded-For` unconditionally. If the
Cloudflare → GCLB → bench chain **appends** rather than **overwrites**, every IP-keyed rate
limit in the app is spoofable — including the web-lead ingress and the public fountain-move
form. The runbook flags this as never formally verified. It should be verified before the
ingress is enabled, not after.

---

## Phase 2 — Publishing

Gated by the Phase 0 approvals, so it starts as soon as the first platform clears rather than
waiting for all of them. Ships dormant behind `Marketing Settings.enabled = 0` with
per-platform flags, and posts require draft → approve → publish.

| DocType | Purpose |
|---|---|
| `Social Account` | platform, page/channel/organization id, connection + token status, quota state |
| `Social Post` | the content unit: campaign, body, media, status, scheduled time, approver, approval timestamp |
| `Social Post Target` | child — one row per destination account, with per-network variant text, first comment, and link |
| `Social Publish Job` | the outbox: target, state, `available_at`, lease, attempt count, last error |
| `Social Post Metric` | target × date engagement pulled back after publish |
| `Marketing Media Asset` | Drive file id or GCS object, source Project, usage rights, tags, dimensions |

**Publishing is an outbox, not a direct call.** Approve writes a `Social Publish Job` row;
a cron sweeper drives it. That is the only design that survives a deploy `FLUSHDB` and the
only one where "scheduled for 9am Tuesday" is a promise rather than a hope.

Rate limiting copies the chat pattern exactly: pure decision functions in the bench-free CI
tier, deployed as Redis Lua so the limiter is shared across workers, with the Lua printed
next to the function it mirrors. And the standing rule — **the bucket is an optimisation;
backoff is the correctness mechanism.** Never retry a 4xx other than 429; a 403 is a config
fault and retrying turns a fast legible failure into a slow confusing one.

### The `/marketing` SPA

A chrome-free `www/` shell plus a bundle, following the Chat SPA, which is the most recent
substantial UI in the app and the best model:

- **No Vue**, **no `innerHTML`** — not "none with user data", none — enforced by a
  build-blocking source-rule test in `scripts/`, not by discipline.
- `--ee-brand` (`#00a0dd`) may never carry text; use `--ee-brand-ink` for accent-as-text and
  `--ee-brand-surface` for accent-as-background.
- One route rule serving the whole subtree; the server never parses the path.
- Measure and document the bundle cost, as Chat did.

Surfaces: month/week content calendar with drag-to-reschedule, composer with per-network
preview and character/aspect-ratio validation, media picker over `Marketing Media Asset`,
approval queue, and per-post analytics.

---

## Phase 3 — Content supply and demand generation

- **Field photo capture.** Photos live on crews' phones. Extend the Time Kiosk PWA and the
  maintenance visit flow with a marketing-photo capture that tags to a Project, carries a
  usage-rights flag, and queues a post draft. `api/time_kiosk.record_job_photo` already keys
  on a device-minted `client_uid` so an offline retry updates rather than duplicates — reuse
  it. This turns operations into content supply, which is the only sustainable answer to
  "what do we post".
- **Drive project-folder indexing** into `Marketing Media Asset`, so the existing per-project
  `Pictures` folders become a browsable library.
- **AI captions** via Vertex, using the existing `Triton Settings` connection. Drafts only —
  they enter the same approval gate.
- **Landing pages + UTM builder.** Campaign landing pages as `www/` pages with first-party
  forms (the fountain-move page is the working precedent, Turnstile and honeypot included),
  plus a link builder that mints consistently tagged URLs. Inconsistent hand-tagging is the
  usual reason attribution reports lie.
- **Newsletter + SMS nurture** on Frappe Newsletter and the existing Twilio/Triton SMS path,
  with segmentation from ERPNext data.
- **Consent.** Per-Contact marketing email and SMS opt-in with source, timestamp and IP;
  unsubscribe honoured across both channels; deletion requests supported. **SMS opt-out
  (STOP) handling is not optional — it is a TCPA requirement.** No cookie banner: first-party
  UTM cookies for your own attribution are low-risk under Utah's UCPA, which has no opt-in
  requirement.
- **Google Business Profile.** Pull reviews, alert on new ones, draft AI replies through the
  approval gate, publish local posts, and request reviews after a completed Project or
  maintenance visit. Local search is where fountain buyers actually look, and this needs no
  ad-account approval.

---

## Phase 4 — Call tracking (Triton)

Phone is a real inbound channel here, so without this a meaningful share of ad spend stays
permanently unattributable. Dynamic number insertion swaps the displayed number by campaign;
the inbound call then carries the same attribution as a form fill.

The number pool and the inbound webhook are Triton-side work — **tracked on PRJ-00755** —
with the ERPNext half receiving the attributed call and stamping it onto the Lead. Per the
standing ownership split, ERPNext owns the domain tools and Triton stays a pure MCP client.

---

## Decisions taken

Recorded 2026-08-13 so they are not re-opened without cause.

| # | Decision | Rationale |
|---|---|---|
| 1 | Direct platform APIs, not an aggregator | Matches the house no-SDK REST pattern; no vendor fee or lock-in on the publish path |
| 2 | Networks: Facebook, Instagram, LinkedIn, YouTube | Deliberately excludes X (paid API tier), TikTok (audit) and Pinterest |
| 3 | Ads are read-only | Reporting and attribution only. No automation can touch spend |
| 4 | Ad platforms: Google, Meta, LinkedIn | |
| 5 | Keep the Lead stage; fix the process | Lead-to-opportunity conversion and contact rate cannot be computed without it |
| 6 | Spend at campaign × day, auto-pulled | Month × channel cannot answer "which campaign". `Marketing Spend` retained for offline spend |
| 7 | Foundation before publishing | Posts published onto broken attribution cannot be measured |
| 8 | Dedicated `/marketing` SPA | Daily-driver tool for a non-technical hire; keeps them out of Desk |
| 9 | Dormant by default, draft → approve → publish | Nothing reaches a public account by accident |
| 10 | Single company, no company scoping | Sapphire Fountains only |
| 11 | Consent tracking + unsubscribe, no cookie banner | Utah UCPA has no opt-in requirement; a banner would measurably cut attribution coverage |
| 12 | Email/SMS on Frappe Newsletter + Twilio/Triton | No new vendor |

## Open questions

- Who administers the Search Console property, and is it `sc-domain:` or URL-prefix?
- Does the Cloudflare → GCLB → bench chain overwrite or append `X-Forwarded-For`?
- Who owns the Lead triage queue daily once web capture is live? Without a named owner,
  decision 5 does not hold.
- Attribution window and model for reporting — first-touch is what capture implements; the
  reporting layer may want last-touch and position-based alongside it.

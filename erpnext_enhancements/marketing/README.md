# Marketing

Social publishing, read-only paid-ads reporting, and the spend ↔ pipeline join. The plan of
record is [`docs/marketing-platform-plan.md`](../../docs/marketing-platform-plan.md);
the external approvals that gate publishing are in
[`docs/marketing-platform-approvals.md`](../../docs/marketing-platform-approvals.md).

**Everything here is dormant.** `Marketing Settings.enabled` is `0` and every per-platform
flag is `0` independently — the master switch alone turns nothing on. Since v1.503.0 the
**read-only ad-spend connectors** (Google Ads, Meta, LinkedIn) exist and write to the doctypes
below once a platform is switched on and connected; setup, checks and troubleshooting are in
[`docs/marketing-connectors-runbook.md`](../../docs/marketing-connectors-runbook.md).
Since v1.507.0 the **publishing switches and their gate** exist too (Phase 2's scaffold; see
[Publishing](#publishing-phase-2) below). They switch nothing on by themselves: no publisher
is installed yet. Since v1.508.0 the publishing connections, and since v1.509.0 the posts,
accounts, media and the outbox that will publish them. Every network has had a publisher since
v1.513.0 (TASK-2026-01483 to 01485), and since v1.514.0 a post can be approved (01486; see
[Roles and approval](#roles-and-approval)). Nothing goes out until the switches are on and a
network's platform approval has cleared.

## Files

| Path | What it is |
|---|---|
| `core/constants.py` | Pinned API versions (Google Ads v25, Meta v26.0, LinkedIn 202608), OAuth endpoints and read-only scopes, the **read-only allowlist**, and `SPEND_CAPABLE_SCOPES`, the scopes nothing in this module may request |
| `core/client.py` | The one HTTP transport (`requests`, no SDK): refuses anything off the allowlist, retries only 408/429/5xx, raises `from None` with redacted messages |
| `core/oauth.py` | Connect flow per platform: user-bound one-time `state`, code exchange, refresh, revoke |
| `core/sync.py` | The engine: accounts → campaigns → campaign-day metrics (→ Google clicks), restate + upsert, cursor only on a clean run, Sync Log, raw-payload archive, prune |
| `core/tasks.py` | Scheduler shims: master switch → 20 h throttle → only connected platforms → one `long` job |
| `core/api.py` | System-Manager endpoints: POST-only except the OAuth callback (GET, logged-in, state-bound, rate-limited) |
| `api.py` | Stable short path for the redirect URI registered in each platform console |
| `core/roas.py` | The spend → Lead → Opportunity → Project → invoice join, **pure**: utm_id then gclid, paid-but-unjoinable as its own row, lead-month cohorts, a 365-day window, contract value and invoiced revenue (TASK-2026-01477) |
| `report/ad_spend_roas/` | **Ad Spend ROAS** Script Report over `core/roas.py`: cost per lead, cost per won project, ROAS on contract and on invoiced. System Manager / Sales Manager |
| `platforms/{google_ads,meta_ads,linkedin_ads}.py` | Per-platform request builders and **pure** parsers, tested against `tests/data/marketing_api_fixtures.json` |
| `publish/constants.py` | The four publishing networks (Facebook, Instagram, LinkedIn, YouTube) and each one's switch, named apart from the ad platforms |
| `publish/gate.py` | **Pure:** whether an approved post may go out to a network. Master switch AND the network's own switch; never a substitute for approval |
| `publish/client.py` | The publishing transport: its own allowlist (never an ad endpoint), reads retry, **writes never retry on their own**, one retry after a 401 with a refreshed token |
| `publish/oauth.py` | The three publishing connections: Connect (Meta keeps only the Page token and refuses a login that grants a spend-capable permission), token use and refresh, and the per-connection daily upkeep that clears a dead credential instead of retrying it |
| `publish/tasks.py` | Scheduler shim for that upkeep: master switch, then only *Connected* connections |
| `publish/outbox.py` | **The outbox state machine**, pure over a store: enqueue an approved post, claim, dispatch, retry or hold, and the rule that a job which may have sent goes to **Unconfirmed**, never back to Pending (TASK-2026-01481). Since v1.514.0 every outcome adds an **attempt-log** entry in the same write as the state, and `cancel_post` stops what has not gone out |
| `publish/sweeper.py` | The five-minute sweep and `FrappeStore`: reclaim expired leases, claim due jobs atomically, hand each to `run_dispatch` on `long`; `resolve_job` (POST; a Marketing Manager or System Manager, from a signed-in browser) for a person's answer on an Unconfirmed or Failed job |
| `publish/workflow.py` | **Pure:** draft → approve → publish and who may do each (TASK-2026-01486). Approver ≠ author or last editor; only from a signed-in browser (`signed_in_browser`: a token, job or console request has `sid == user`); status, approver and approval time move only through the actions |
| `publish/approval.py` | The four Social Post actions, all POST: **Submit for Approval**, **Approve** (writes the outbox rows in the same transaction, and refuses a post edited since the approver opened it), **Send Back**, **Cancel** |
| `publish/accounts.py` | Social Account rows from what a publishing connection reaches, written on Connect |
| `publish/ratelimit.py` | Rate limits (TASK-2026-01482): each rule a **pure** function (the spec) plus a **Redis Lua** script printed next to it, run against each other in CI. Instagram's rolling 24 h per account (live limit when known, 25 until then), YouTube's Pacific-day budgets, and connection pauses from Meta's usage headers or a 429's Retry-After. The sweep asks it before claiming; the publish transport reports every response to it. **The bucket is an optimisation; backoff is the correctness mechanism** |
| `publish/publishers/` | The per-network publisher registry and its **two-phase contract**: `prepare()` does everything non-public (retried freely), `send()` only the public step (never re-sent on an ambiguous failure). Networks without a module are not sendable |
| `publish/publishers/meta.py` | **Facebook Page + Instagram** (TASK-2026-01483). Facebook: photos uploaded unpublished, then one feed post; a video by URL. Instagram: live limit read, containers built and waited on in prepare, `media_publish` in send; a timed-out publish is settled by asking the container (PUBLISHED / FINISHED), not by re-sending. First comment and permalink failures are warnings on a success |
| `publish/publishers/linkedin.py` | **LinkedIn Company Page** (TASK-2026-01484). Images and documents registered, PUT and read back AVAILABLE in prepare (private files fine: we upload the bytes); `POST /rest/posts` in send, the URN from `x-restli-id`; a link post is an `article` with our *Link Title*; little-text escaping that keeps hashtags; a timed-out post is looked for among the Page's latest, never re-sent. No video yet |
| `publish/publishers/youtube.py` | **YouTube** (TASK-2026-01485). prepare opens a resumable upload session (no video exists yet); send PUTs 32 MiB chunks, and a failed chunk is settled by asking the session (308 resumes from YouTube's Range, 201 is the video, endless incomplete is `NotPublished`). Quota refusals (403/400 reasons) wait as 429s. Thumbnail and playlist are best-effort. Locked private until the YouTube audit passes |
| `publish/validation.py` | **Pure** pre-approval checks per network with Meta's figures (caption, hashtags, mentions, JPEG, ratios, Reel length, carousel size, one FB video). Fills Social Post's *Network Check*; `outbox.enqueue` refuses a post with any problem |
| `publish/media.py` | A URL each Marketing Media Asset can be fetched from: public file, or a 6-hour signed GCS URL (training's signer). Private files and Drive are refused before approval |
| `doctype/marketing_connections/` | Single, **System Manager only**: OAuth apps, the Google developer token, and the tokens (hidden, encrypted, set only by Connect), for the three ad platforms and, since v1.508.0, the three publishing connections. Connect / Test / Disconnect per connection; Sync now (ads) |
| `doctype/ad_click/` | One Google click (gclid → campaign, date): decision D's fallback join. Named by gclid |
| `doctype/ad_account/` | One row per connected advertising account. Identity is (platform, external_id) |
| `doctype/ad_campaign/` | One row per campaign. Identity is (ad_account, external_id) |
| `doctype/ad_daily_metric/` | Campaign × day. Identity is (campaign, metric_date) — the key that makes restating safe. Carries `upsert()` |
| `doctype/marketing_sync_log/` | One row per connector run: window covered, counters, error |
| `doctype/marketing_raw_payload/` | Append-only verbatim response archive, pruned on retention |
| `doctype/marketing_settings/` | Single: master switch, ad-platform and publishing switches, sync dials. Change-tracked, so the Version log shows who turned a switch on |
| `doctype/social_account/` | One Page, Instagram account, Company Page or channel. Identity (network, platform ID), set by Connect; a person only ticks **Enabled**. Token status is read from Marketing Connections, not copied here |
| `doctype/social_post/` (+ `social_post_target/`, `social_post_media/`) | The post, the accounts it goes to, its media in order. **Locked once approved** (`SocialPost.validate` compares `outbox.content_signature`); status, approver and approval time refused in an ordinary save; deletable only before anything was queued. Form buttons for the four actions. Change-tracked |
| `doctype/social_publish_job/` (+ `social_publish_attempt/`) | The outbox row. `external_post_id` is **unique**; indexes on (state, available_at) and (state, lease_expires_at) for the sweep; form buttons to resolve an Unconfirmed job. Its **Attempt Log** keeps every attempt and every person's answer |
| `doctype/social_post_metric/` | Job × day engagement, named from (job, date) so a restated day upserts (TASK-2026-01488 fills it) |
| `doctype/marketing_media_asset/` | A photo or video: where it lives, the Project it shows, and **usage rights**. Only *Cleared for social* can be queued; new assets start as *Needs client approval* |

## Publishing (Phase 2)

Publishing is the first code in this module that writes to the outside world, arriving in a
module whose guarantee so far was "read-only". Three rules keep that guarantee true for
spend, and the first two are enforced by `tests/test_marketing_publishing.py`:

1. **Two switches, and approval on top.** An approved post may go out to a network only when
   `enabled` *and* that network's publishing switch are on (`publish/gate.py`). The ad
   switches play no part, so turning on Meta reporting does not bring Facebook publishing
   one checkbox closer. The gate is asked at send time, so switching a network off stops even
   posts already approved and scheduled. It never replaces approval (decision 9).
2. **No spend-capable scope, anywhere.** `core/constants.SPEND_CAPABLE_SCOPES` lists the OAuth
   scopes that can create, change or fund advertising. The build fails if one appears in any
   string in `marketing/`. The one a publisher would reach for is Meta's `pages_manage_ads`,
   which lets a post be **boosted** into paid spend. Google Ads' only scope is full access,
   and it stays confined to `core/constants.py`.
3. **Never the ad connectors' transport.** `core/client.py` refuses every Meta and LinkedIn
   POST by design and does not grow a write. Publishing has its own, `publish/client.py`,
   whose allowlist holds only the identity reads the Connect flow needs until the publishers
   (TASK-2026-01483 to 01485) add their writes. It never lists an ad endpoint, and each
   transport refuses the other's paths.

### Roles and approval

Since v1.514.0 (TASK-2026-01486), decided by Nik on 2026-09-22:

| Role | Can | Role profile |
|---|---|---|
| **Marketing Team** | Draft and edit posts, upload media, submit for approval, send back, cancel; read accounts, jobs and metrics. **Nothing outside this module**: no customers, invoices or financials | `Marketing` (it alone: what a marketing hire gets) |
| **Marketing Manager** | The same, plus **approve**, and resolve an Unconfirmed or Failed job | `Marketing Approvers` (a single-role add-on, like the PO profiles) |
| System Manager | Everything, including the credentials and switches, which neither marketing role can see. **Cannot approve** without Marketing Manager | n/a |

Approval is never by the post's author or its last editor, and only from a signed-in browser: an
API key, an OAuth token, a background job or the console is refused whatever roles it holds.
Triton's service account holds Marketing Manager, and this is what keeps it from approving. A
post edited after the approver opened it is refused too, so what is approved is what was reviewed.
Stopping is never gated like that: anyone who may edit a post may cancel it.

The Marketing department's dashboard gate (`api/kpi.py`, `DEPARTMENT_ROLES`) already names
Marketing Manager; the approver is the same role. A Campaign link on a post needs read access to
ERPNext's Campaign, which neither marketing role has: granting it would take a Custom DocPerm,
and that replaces Campaign's standard permissions wholesale.

The publishing **connections** (v1.508.0) are separate from the ad ones: their own Connect,
their own fields (`meta_publishing_*`, `linkedin_publishing_*`, `youtube_publishing_*`, a
prefix set disjoint from the ads one) and their own token. Setup, lifetimes and troubleshooting
are in [the connectors runbook](../../docs/marketing-connectors-runbook.md#publishing-connections-phase-2).

## There is deliberately no `module_def/` here

Several older modules carry a `module_def/<name>.json`. **It does nothing**, and this one
does not have it. `module_def` is not in Frappe's `IMPORTABLE_DOCTYPES`, so the file is never
imported — and it would in any case have to be discovered by walking the very module folder
that the failure it appears to guard against is skipping.

On migrate, a `Module Def` row appears only as a side effect of `DocType.on_update` →
`make_module_and_roles`: it is a **consequence** of the DocType import, never a precondition
for it. What actually makes a new module install on an existing site is
[`setup/module_map.py`](../setup/module_map.py) on `before_migrate`, which rebuilds the
app → modules map that `sync_for()` iterates. That is already in place and covers this
module; see [`tests/test_module_installability.py`](../tests/test_module_installability.py),
which asserts it and which this module is automatically covered by.

## Why the names look like that

`Ad Daily Metric` is named `format:ADM-{campaign}-{metric_date}`, and `Ad Campaign` and
`Ad Account` are similarly derived from their natural keys. That is load-bearing rather than
cosmetic.

Platforms **revise spend and conversion figures for days that have already closed**, so a
correct connector re-pulls a trailing window on every run rather than only fetching
yesterday. With a hash or a serial name, that window would deposit a fresh set of rows every
night and every spend total downstream would drift upward for as long as nobody checked. A
deterministic name makes the re-pull an upsert against the primary key.

The key fields are therefore `set_only_once`: changing one would orphan every row already
filed under the old name.

Use `ad_daily_metric.upsert()` rather than a bare `insert()`. It is written as "update if
present" because restating is the normal path, not the exceptional one — and it catches
**both** `DuplicateEntryError` and `UniqueValidationError`, because Frappe raises the first
for a primary-key clash and the second for a non-primary unique index, and code that catches
only one of them treats "no exception" as "inserted cleanly" and quietly stops de-duplicating.

## The Single-defaults trap, and the two defences against it

**A `default` on a new field of a Single never reaches the row that already exists.** A
Single stores one row per field in `tabSingles`; `bench migrate` adds no row for a newly
declared field and `load_from_db` applies no defaults — they fire in `new_doc()`, on a fresh
install and never again.

Chat Settings hit this in v1.277.3 and its settings page became **unsaveable**: `validate`
refused the zeros the row had never been given, so opening the page and pressing Save
returned fifteen errors about fields nobody had touched. That Single no longer exists — it
went with the chat module in v1.426.0 — but the trap is a property of how Frappe stores a
Single, not of that feature, and this module is still standing in front of it. Note the
shape — saving a Single deletes and re-inserts every field row, so a page people use
self-heals on the next save. The ones that bite are the settings for **dormant** features,
where the first save is the one you need and the one that fails. This module is dormant by
design.

So two things ship together:

1. [`patches/backfill_marketing_settings_defaults.py`](../patches/backfill_marketing_settings_defaults.py)
   fills the missing rows. In `patches.txt` **and** on `after_migrate`, so a site whose Patch
   Log already carries the entry still gets it.
2. `MarketingSettings.validate` **coerces** a missing or non-positive dial back to its
   declared default instead of raising, so the page repairs itself and cannot brick.

Coercion applies **only to the positive-integer dials**, never to the `Check` fields. A dial
of `0` is meaningless — zero retries, zero-second timeout. A checkbox of `0` is somebody
deliberately switching a connector off, and restoring that to `1` would turn a platform back
on behind their back.

## What must never be written here

`Marketing Sync Log` and `Marketing Raw Payload` are readable by **Sales Manager**. No token,
no secret, no `Authorization` header may reach either — not in `error_message`, not in
`endpoint`, not in the archived body. This app has published private key material to a log
before, by letting a background job re-raise with frame locals intact.

## Registered in `hooks.py`

| Hook | Entry | Why |
|---|---|---|
| `after_migrate` | `backfill_marketing_settings_defaults` | Backstop for the Single-defaults trap above |
| `scheduler_events.cron` `"25 3 * * *"` | `core.tasks.nightly_ad_spend_sync` | The nightly pull (a thin shim; the work runs on `long`) |
| `scheduler_events.cron` `"35 3 * * *"` | `publish.tasks.maintain_publishing_tokens` | Daily upkeep of the publishing tokens, one rule per connection (v1.508.0) |
| `scheduler_events.cron` `"2-59/5 * * * *"` | `publish.sweeper.sweep_publish_jobs` | The outbox sweep, the timer for scheduled posts (v1.509.0). Returns at once while the module is off |
| `scheduler_events.daily` | `core.tasks.daily_prune` | Raw payloads past retention; clicks past 180 days no Lead carries |

Credentials live on **Marketing Connections**, not on Marketing Settings: Settings is readable
by Sales Manager, and the rule above ("no secret on anything beyond System Manager") holds for
it too. The connectors are built against recorded-shape fixtures because API access is still
pending (Phase 0); replace fixture entries with real, redacted captures once a platform is
connected.

## Not here on purpose

- **`Marketing Spend` stays in `kpi_dashboards`.** It is retained for *offline* spend — trade
  shows, print, sponsorship — and is not superseded by `Ad Daily Metric`. Both roll into one
  report. It also remains the fallback if LinkedIn's Marketing Developer Platform access
  never clears.
- **Currency conversion.** Spend is stored exactly as the platform reports it, in the
  account's own currency. A rate applied at ingest cannot be corrected afterwards, and the
  reporting layer is where a rate belongs.
- **`utm_source` / `utm_medium` / `utm_campaign`.** Never write erpnext's own fields — the
  first is reserved for stray-Contact suppression and the other two are Links that would
  spawn junk taxonomy. Raw values live in the `custom_utm_*` Data fields, and
  `crm_enhancements.attribution._fill_blanks` is their single writer.

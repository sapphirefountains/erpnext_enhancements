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

## Files

| Path | What it is |
|---|---|
| `core/constants.py` | Pinned API versions (Google Ads v25, Meta v26.0, LinkedIn 202608), OAuth endpoints and read-only scopes, and the **read-only allowlist** |
| `core/client.py` | The one HTTP transport (`requests`, no SDK): refuses anything off the allowlist, retries only 408/429/5xx, raises `from None` with redacted messages |
| `core/oauth.py` | Connect flow per platform: user-bound one-time `state`, code exchange, refresh, revoke |
| `core/sync.py` | The engine: accounts → campaigns → campaign-day metrics (→ Google clicks), restate + upsert, cursor only on a clean run, Sync Log, raw-payload archive, prune |
| `core/tasks.py` | Scheduler shims: master switch → 20 h throttle → only connected platforms → one `long` job |
| `core/api.py` | System-Manager endpoints: POST-only except the OAuth callback (GET, logged-in, state-bound, rate-limited) |
| `api.py` | Stable short path for the redirect URI registered in each platform console |
| `core/roas.py` | The spend → Lead → Opportunity → Project → invoice join, **pure**: utm_id then gclid, paid-but-unjoinable as its own row, lead-month cohorts, a 365-day window, contract value and invoiced revenue (TASK-2026-01477) |
| `report/ad_spend_roas/` | **Ad Spend ROAS** Script Report over `core/roas.py`: cost per lead, cost per won project, ROAS on contract and on invoiced. System Manager / Sales Manager |
| `platforms/{google_ads,meta_ads,linkedin_ads}.py` | Per-platform request builders and **pure** parsers, tested against `tests/data/marketing_api_fixtures.json` |
| `doctype/marketing_connections/` | Single, **System Manager only**: OAuth apps, the Google developer token, and the tokens (hidden, encrypted, set only by Connect). Connect / Test / Disconnect / Sync now buttons |
| `doctype/ad_click/` | One Google click (gclid → campaign, date): decision D's fallback join. Named by gclid |
| `doctype/ad_account/` | One row per connected advertising account. Identity is (platform, external_id) |
| `doctype/ad_campaign/` | One row per campaign. Identity is (ad_account, external_id) |
| `doctype/ad_daily_metric/` | Campaign × day. Identity is (campaign, metric_date) — the key that makes restating safe. Carries `upsert()` |
| `doctype/marketing_sync_log/` | One row per connector run: window covered, counters, error |
| `doctype/marketing_raw_payload/` | Append-only verbatim response archive, pruned on retention |
| `doctype/marketing_settings/` | Single: master switch, per-platform flags, sync dials |

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

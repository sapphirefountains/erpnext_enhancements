# Marketing

Social publishing, read-only paid-ads reporting, and the spend ↔ pipeline join. The plan of
record is [`docs/marketing-platform-plan.md`](../../docs/marketing-platform-plan.md);
the external approvals that gate publishing are in
[`docs/marketing-platform-approvals.md`](../../docs/marketing-platform-approvals.md).

**Everything here is dormant.** `Marketing Settings.enabled` is `0` and every per-platform
flag is `0` independently — the master switch alone turns nothing on. As of v1.280.0 this
module is **data model only**: the doctypes exist and nothing writes to them yet.

## Files

| Path | What it is |
|---|---|
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
returned fifteen errors about fields nobody had touched. Note the shape — saving a Single
deletes and re-inserts every field row, so a page people use self-heals on the next save. The
ones that bite are the settings for **dormant** features, where the first save is the one you
need and the one that fails. This module is dormant by design.

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

No `doc_events`, no `scheduler_events` yet — the connectors that need them are
TASK-2026-01476 and are blocked on the Phase 0 platform approvals.

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

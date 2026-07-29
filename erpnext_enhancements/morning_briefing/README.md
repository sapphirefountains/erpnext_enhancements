# `morning_briefing/` — the per-user daily briefing

Holds the `Daily Briefing` doctype. The generation logic is in `api/briefing.py`; this module
is the storage and the workspace. Moved out of `enhancements_core` in v1.41.0
(`move_briefing_to_morning_briefing`).

## `Daily Briefing`

One row per (user, date), enforced by the `format:BRIEF-{date}-{user}` autoname — so a
regeneration overwrites rather than accumulating duplicates.

Rows are written **exclusively** by `api.briefing`, either the weekday 06:30 scheduler batch
or a desk force-refresh (`get_morning_briefing(force=1)`). Direct desk access is System
Manager only; users read their own briefing through the gated `get_morning_briefing`
endpoint.

## Why a DocType and not `frappe.cache`

Redis gets flushed — by a deploy, a restart, or a memory-pressure eviction — and a briefing
that vanishes at 07:00 because someone deployed is worse than no briefing. This is durable on
purpose.

Old rows are purged by a daily `purge_old_briefings` job, so durability doesn't become
unbounded growth.

## Content

Tasks, calendar, pipeline and ToDos, narrated by Gemini via `api/gemini.py`, with a
**deterministic markdown fallback** when the model is unavailable. Optional per-recipient
email. The master switch is `briefing_enabled` in ERPNext Enhancements Settings.

The fallback matters: a briefing is a daily habit, and a habit that intermittently produces
nothing gets abandoned. Keep the non-AI path working.

## Overlap with Triton

Triton has its own morning-briefing generator and its own weekday 06:30 cron. **Until one
side is disabled, both run every weekday morning** into separate stores. Setting
`BRIEFING_SCHEDULER_ENABLED=false` on Triton hands the slot to this implementation with no
code change on either side. See Triton's `docs/convergence.md`.

## Tests

```bash
python -m unittest erpnext_enhancements.tests.test_briefing -v
```

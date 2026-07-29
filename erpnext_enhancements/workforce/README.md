# `workforce/` — time tracking and the Time Kiosk

Field-crew time capture: the **Time Kiosk** PWA, the clock-in sessions behind it, and the
location timeline. The doctypes moved here out of `enhancements_core` in v1.38.0
(`move_time_tracking_to_workforce`, `move_job_interval_to_workforce`).

The kiosk **PWA shell** lives in [`../www/`](../www/README.md); the backend endpoints are
`api/time_kiosk.py`. This module holds the desk pages and the data model.

> **HTTPS is required.** Geolocation, service workers, and PWA install only work over HTTPS
> (`localhost` is exempt). A kiosk that "won't clock in" on a plain-HTTP host is working as
> designed.

## Contents

| Path | Purpose |
|---|---|
| `page/time_kiosk/` | The desk-side Time Kiosk page |
| `page/location_timeline/` | Per-employee location timeline view |
| `doctype/job_interval/` | One clock-in **session** |
| `doctype/time_kiosk_log/` | Raw kiosk event log |
| `doctype/time_kiosk_settings/` | Single — kiosk configuration |

## `Job Interval` is the core record

One Time Kiosk clock-in session for an Employee against a Project/Task: `start_time` →
`end_time` with a status of Open / Paused / Completed, plus accumulated
`total_paused_seconds` and `last_pause_time` for pause/resume, a `sync_status` /
`sync_attempts` block for QuickBooks Time sync, and the location the session started at.

Two things follow from that shape:

- **Elapsed time is derived, not stored.** It is the span minus `total_paused_seconds`.
  Persisting a duration alongside the timestamps creates two sources of truth that disagree
  the first time someone edits one.
- **`sync_attempts` is a retry budget**, not a diagnostic. It exists so a permanently failing
  sync stops rather than retrying forever.

## Consolidation

The kiosk records fine-grained sessions; payroll wants consolidated hours. The consolidation
algorithm lives in the repo-root `sync_time_kiosk.py` and has its own bench-free suite —
the first thing CI runs:

```bash
python -m unittest test_sync_time_kiosk.py -v
```

## Related

- **WI-021** — Time Kiosk rollout
- **WI-017** — payroll hours export
- **WI-016** — Activity Cost / labour costing
- `quickbooks_time/` — the QuickBooks Time integration these sessions sync to

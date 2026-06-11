# Time Kiosk Analyst Workflow

Use this workflow when the user asks "who is clocked in", "how many hours did
the crew log", or wants to audit time tracking / Timesheet sync health. Field
crews clock in and out through the Time Kiosk PWA, which creates **Job
Interval** documents that a background job consolidates into draft Timesheets.

## The tool

`workforce_time_status` has four modes:

- `{"mode": "now"}` (default) — every interval currently Open or Paused: who
  is clocked in, on which project, since when, and `worked_hours` so far.
- `{"mode": "me"}` — the calling user's own clock status (includes task and
  attachments). Use when the user asks about *their* clock.
- `{"mode": "day_summary", "date": "YYYY-MM-DD"}` — one day's intervals
  (default today) with `totals.by_employee` and `totals.by_project` hour
  rollups.
- `{"mode": "history", "from_date": ..., "to_date": ...}` — the same rollups
  over a range (default last 7 days) plus `timesheet_sync` health.

Filter any mode with `employee` and/or `project`.

## Semantics that matter

- `worked_hours` excludes paused time: wall clock minus
  `total_paused_seconds`, minus the still-running pause when the interval is
  currently Paused. Don't recompute from `start_time`/`end_time` alone.
- `sync_status = "Failed"` means the interval never reached its draft
  Timesheet (`timesheet_sync.failed_intervals` lists them). To triage,
  cross-check with `list_documents` on "Timesheet" filtered to the employee,
  `docstatus: 0`, and the date range — the hours may simply be missing there.
- An interval left Open across midnight inflates the day it started; flag
  intervals with implausible `worked_hours` (> 12) instead of silently summing
  them.
- `history` mode caps the interval list (default 50, max 200) — for payroll-
  grade totals over long ranges, narrow by employee or iterate week by week.

## Boundaries

- Visibility follows Job Interval permissions: regular employees usually see
  only their own intervals; supervisors see everyone. An empty result for a
  non-supervisor may mean "not permitted", not "nobody worked".
- GPS/location data (Time Kiosk Log) is intentionally not exposed through
  MCP; the desk Location Timeline (System Manager / HR Manager) is the only
  consumer.
- This tool is read-only — clocking in/out happens in the kiosk, not here.

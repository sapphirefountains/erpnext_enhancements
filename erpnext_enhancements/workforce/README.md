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
| `doctype/job_interval_photo/` | Child table — one captured job photo |
| `photo_gate.py` | The job-photo capture gate (WP-2) |
| `photo_routing.py` | Photo fan-out onto Project/Task + Drive hand-off (WP-3) |
| `payroll_export.py` | Semi-monthly hours in the payroll provider's workbook format (WP-8) |
| `report/job_photo_compliance/` | Which closed jobs have photos, and which do not |
| `report/job_photo_library/` | Marketing-facing browse view over field photography |
| `report/payroll_hours_export/` | Desk view of the payroll workbook + its download button |
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


## The job-photo capture gate (WP-2, v1.241.0)

Crews are expected to photograph every job; the time clock is where that is enforced,
because it is the one thing a technician always touches.

`Job Interval` carries a `photos` child table (`Job Interval Photo`), a server-set
`photo_status`, a `photo_skip_reason` and a `photos_pending_upload` roll-up. The gate lives
in `photo_gate.py` and runs inside `api.time_kiosk.log_time` for the two actions that END an
interval — **Switch** and **Stop**. Pause and Resume are deliberately not gated: pausing for
lunch is not the end of a job, and demanding a photo for it would train everybody to skip.

Configuration is the "Job Photo Capture" section of **Time Kiosk Settings**:
`require_job_photos` (off by default), `min_photos_per_interval`, `allow_photo_skip` and
`require_skip_reason`.

### The two decisions worth knowing

**A Pending upload counts as captured.** A photo row is written the instant the shutter
fires on the device, carrying only a device-minted `client_uid`; the bytes follow whenever
there is signal. So an interval closes cleanly with every photo still queued on the phone.
This is deliberate. A technician physically unable to leave a site because an upload is
failing would destroy trust in the system faster than any amount of missing data justifies.
Pending photos are recorded, reported on (`Job Photo Compliance`) and retried by the device.

**Skipping is allowed by default.** Setting `allow_photo_skip = 0` turns the gate into a
hard block with no escape hatch — a genuine case (customer refused permission, camera
broken, nothing visible to photograph) then has no way through except phoning the office.
**That is a people decision, not an engineering one, and it is still open.** The default
ships permissive.

### Enforcement is server-side, and that phrase is load-bearing

The kiosk prompts in the browser too (`public/js/kiosk/app.js`), but that prompt is a
courtesy. A field device is offline half the day and serving a cached bundle of unknown age;
client-side validation there is a suggestion. `log_time` re-reads Time Kiosk Settings on
every call and is what actually decides.

### Turning it off

`require_job_photos` in Time Kiosk Settings. Off takes effect on the next call — no deploy,
no restart. Photos already captured are untouched.

## Photo routing (WP-3)

`photo_routing.route_job_photo` is enqueued (never inline — Drive is a third-party API and a
clock-out must not wait on it) when a photo's bytes land. It copies the File onto the
Project and Task, tags it (`job-photo`, `cust:<customer>`, `vs:<value stream>`,
`shot:<date>`) and — this is the part that reads oddly — **does not talk to Google Drive**.

`google_drive/drive_sync.py` already owns Drive upload end to end: `on_file_attached` fires
on every `File` `after_insert`, finds the folder id on the attached document, and enqueues
the upload. It is already idempotent (it bails the moment `custom_drive_file_id` is set) and
already handles quota and auth failures by logging a replayable payload and leaving the
local copy alone. Re-implementing that here would mean a second upload path with its own
idempotency bug. So this module's job is narrower: **make sure the File is attached to a
document that has a Drive folder.** Normally the Project. When the Project has no folder, or
its `custom_drive_folder_missing` flag is set, it falls back to the Customer.

## Payroll hours export (WP-8)

`payroll_export.py` reproduces the **Shaw & Nielsen** semi-monthly workbook (firm code
`SHAWA2530`, client code `5813`) from Job Interval data. The layout — six-line header block,
three-row stacked column header, trailing Totals row, sheet name — is transcribed from a
real submitted file, because a payroll clerk matches this against what they already have.

**It computes hours worked. It does not compute the overtime split, and it must not.**
`hrms` is **not installed on this site**: there is no Salary Structure, no salary slips, no
payroll module of any kind. So `Qualified OT`, `Overtime`, `PTO`, `Holiday`, `Bonus`,
`Commission`, `Reimbursement` and `Services` are emitted **blank**, in position, for the
provider to fill exactly as they do today. `Qualified OT` is a federal tax figure;
reimplementing FLSA premium arithmetic to save a payroll bureau a calculation they already
perform correctly is the worst trade available.

Salaried employees report a flat 86.67 hours (2080 / 24), matching the submitted file.
The provider's employee number lives in core `Employee.employee_number`, seeded by
`patches.seed_payroll_employee_numbers`; an employee without one still appears in the
output, flagged, because silently dropping somebody from a payroll file is the worst failure
mode this module has.

> **Not ready for cutover.** The acceptance criterion is that this reconciles exactly
> against a manually produced sheet for a complete pay period. As of 2026-08-04 that is
> impossible: `tabJob Interval` has **0 rows** in production and `tabTime Kiosk Log` has 16.
> There is nothing to reconcile until the kiosk has been in real use for a full period.

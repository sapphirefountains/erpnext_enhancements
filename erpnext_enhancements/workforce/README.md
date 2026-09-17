# `workforce/` — time tracking and the Time Kiosk

Field-crew time capture: the **Time Kiosk** PWA, the clock-in sessions behind it, the
location timeline, and — since v1.480.0 — what happens to a session after it closes:
tracking health, auto-close, corrections, labour costing, the overtime split and the
supervisor digest. The doctypes moved here out of `enhancements_core` in v1.38.0
(`move_time_tracking_to_workforce`, `move_job_interval_to_workforce`).

The kiosk **PWA shell** lives in [`../www/`](../www/README.md); the backend endpoints are
`api/time_kiosk.py`. This module holds the desk pages, the data model and the mechanisms.

> **HTTPS is required.** Geolocation, service workers, and PWA install only work over HTTPS
> (`localhost` is exempt). A kiosk that "won't clock in" on a plain-HTTP host is working as
> designed.

## Contents

| Path | Purpose |
|---|---|
| `page/time_kiosk/` | The desk-side Time Kiosk page |
| `page/location_timeline/` | Per-employee location timeline view (trail + live) |
| `doctype/job_interval/` | One clock-in **session** (+ `job_interval.js`: health indicator, View on Timeline, Refresh Tracking Health) |
| `doctype/job_interval_photo/` | Child table — one captured job photo |
| `doctype/employee_pay_rate/` | Child table on Employee (permlevel 1) — one effective-dated pay rate |
| `doctype/time_correction_request/` | A technician's ask to change a session; Approve / Decline on the form |
| `doctype/time_kiosk_log/` | Raw kiosk event log — every location fix |
| `doctype/time_kiosk_settings/` | Single — kiosk configuration |
| `permissions.py` | Row scoping for Job Interval and Time Correction Request (hooks.py) |
| `tracking_health.py` | **Frappe-free** trail scoring: coverage, gaps, stops, distance, dwell |
| `overtime.py` | **Frappe-free** weekly regular / overtime split |
| `costing.py` | Pay rate → interval cost → Activity Cost |
| `sites.py` | Where a project's site is; Google geocoding of project addresses |
| `corrections.py` | Reviewing a Time Correction Request: apply, re-sync, email |
| `sweeper.py` | Hourly auto-close of forgotten clock-outs |
| `digest.py` | The 06:45 supervisor digest |
| `photo_gate.py` | The job-photo capture gate (WP-2) |
| `photo_routing.py` | Photo fan-out onto Project/Task + Drive hand-off (WP-3) |
| `payroll_export.py` | Semi-monthly hours in the payroll provider's workbook format (WP-8) + the Internal Costing sheet |
| `report/job_photo_compliance/` | Which closed jobs have photos, and which do not |
| `report/job_photo_library/` | Marketing-facing browse view over field photography |
| `report/payroll_hours_export/` | Desk view of the payroll workbook + its download button |
| `report/labor_cost_analysis/` | Hours and burdened labour cost from the stamped Job Interval fields, grouped by project / employee / position / activity type, with the project's labour budget beside its actual |

## `Job Interval` is the core record

One Time Kiosk clock-in session for an Employee against a Project/Task: `start_time` →
`end_time` with a status of Open / Paused / Completed, plus accumulated
`total_paused_seconds` and `last_pause_time` for pause/resume, a `sync_status` /
`sync_attempts` block for QuickBooks Time sync, and the location the session started at.

Since v1.480.0 it also carries: the clock-in and clock-out **anchor** fixes and the
resolved **site** (with the geofence radius in force and the off-site verdicts); a
**tracking health** block; the **position** the employee held when it started; an
**auto-close** block; a **corrections** block; and a permlevel-1 **pay & cost** block.

Two things follow from that shape:

- **Elapsed time is derived, not stored.** It is the span minus `total_paused_seconds`.
  Persisting a duration alongside the timestamps creates two sources of truth that disagree
  the first time someone edits one. The same rule keeps worked hours off the pay block:
  `labor_cost` is recomputed by `validate` on every save that has an `end_time`.
- **`sync_attempts` is a retry budget**, not a diagnostic. It exists so a permanently failing
  sync stops rather than retrying forever.

### Who sees which rows

Job Interval grants `read` to the `Employee` role, which every staff account holds, and a
DocPerm is doctype-wide — so `permissions.py` scopes it (hooks.py,
`permission_query_conditions` + `has_permission`): System Manager, HR Manager, Accounts
Manager and Projects Manager see everything; everyone else sees rows where `employee` is
their own session Employee. The pay block is a second gate: permlevel 1, readable by System
Manager, HR Manager (both write) and Accounts Manager (read) only.

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
- **WI-016** — Activity Cost / labour costing (superseded in part, see *Pay rates and costing*)
- `quickbooks_time/` — the QuickBooks Time integration these sessions sync to

## Location Timeline (v1.480.0)

`page/location_timeline/` is the manager's map — System Manager, HR Manager and Projects
Manager; the page JSON's roles equal `api.time_kiosk.TIMELINE_MANAGER_ROLES`, and
`tests/test_location_timeline_page.py` fails the build if they drift apart, because a page
whose roles are wider than its endpoint's gate shows a permission error to exactly its
intended reader.

**Trail** replays one employee's date range from `get_location_history`: a polyline per Job
Interval, In / Out anchor markers, hollow points for `Low Accuracy` fixes, dashed red segments
across tracking gaps, labelled stop circles ("18 min at <site>"), site geofence circles from
`site.radius_m`, an accuracy-ring toggle, and a playback scrubber (1× / 4× / 16×; at 1× a
minute of the day passes per second) with a moving marker. The side panel shows day totals,
one card per interval (health pill, off-site / auto-closed / corrected badges, stats; click
to zoom) and Export CSV / GPX, which open `export_location_history` with the on-screen
filters. **Live** polls `get_live_positions` every 30 s only while the tab is visible and this
page is on screen (it stops on `visibilitychange` and on the desk's `hide`); each open interval
is a marker with a stale badge, and clicking a row opens today's trail for that person.

Tiles are OpenStreetMap in light and CartoDB `dark_all` in dark, switched live from a
`MutationObserver` on `<html data-theme>` — the desk always stamps one. Leaflet is frappe's
vendored copy and the stylesheet is `public/css/workforce/location_timeline.css`, both through
`frappe.require` with **bare** paths: v16 appends `?v=<build>` itself, and a path that already
carries one loads as nothing. Opened pre-filled via `frappe.route_options {employee, from_date,
to_date}` from the Job Interval form's **View on Timeline** button and the Employee form's
**Location Timeline** button.

One thing worth knowing about the "not permitted" state: a `PermissionError` arrives as HTTP
403, and frappe's handler calls the error callback with **no argument**, so `frappe.xcall`
rejects with `undefined`. The page reads the status off the jqXHR that `frappe.call` returns
instead, which is the only way that state ever renders.

## Site coordinates (v1.480.0)

`sites.py` answers "where is this project's site?" from three places, in a fixed order:
the project's **Sapphire Maintenance Profile** (a tech pinned it) → the linked **Address**'s
`custom_latitude/longitude` → the Project's own `custom_site_latitude/longitude`
(`Geocoded` by the app, or `Manual`) → nothing. `radius_m` is `ERPNext Enhancements
Settings.geofence_radius_m`; 0 disables the geofence and nothing is ever flagged off-site.
`site_coordinates_bulk` does it for every active project in three queries and feeds
`get_kiosk_options`, so the picker can sort nearest-first and warn before an off-site
clock-in.

On 2026-09-17 almost nothing could answer the question (0 of 16 profiles with coordinates,
0 active projects linking an Address, 11 of 1,024 Addresses geocoded), so the module also
**geocodes** a project's address text (`custom_project_address`, else the linked Address)
through the Google Geocoding REST API with `requests` — no SDK, per ADR 0004 — into the new
Project fields, source `Geocoded`. Three drivers: Project `on_update` enqueues a geocode when
the address changed or coordinates are missing (a `Manual` pin is never overwritten); the
`backfill_project_site_coordinates` patch enqueues a bounded batch once; and
`backfill_missing_site_coordinates` runs daily to re-drive whatever is still missing, because
**a deploy `FLUSHDB`s the queue and destroys enqueued jobs**. `geocode_project` is
idempotent and never raises. Note the key (`Travel Settings.google_maps_api_key`) is
described there as a browser key restricted by HTTP referrer; if the console restricts it,
server-side calls return `REQUEST_DENIED`, which is logged once per project and otherwise
harmless — a project without coordinates simply gets no off-site check.

## Tracking health (v1.480.0)

`tracking_health.py` is pure Python — no `frappe` — so `tests/test_workforce_tracking_health.py`
pins it without a stub. The kiosk records a fix at least every `heartbeat_seconds` while
clocked in, so a silence longer than `tracking_gap_minutes` (Settings, 15) is a **gap**: the
phone stopped reporting and the trail for that stretch is unknown, not stationary. The whole
silent stretch counts, including a leading one (phone woke up an hour in) and a trailing
one. **Coverage** is the span minus its gaps; **health** is `Off` (tracking disabled
site-wide), `None` (no fixes), `Good` (≥ 90 %) or `Gaps`. Also here: `detect_stops` (a run
of fixes within 40 m of its own centroid for ≥ 5 min — the centroid is re-checked against
every member so a run cannot creep across a car park), `path_distance_m`, `dwell_minutes`
(only fix pairs *both* inside the site radius) and `travel_minutes`.

`api.time_kiosk._stamp_tracking_health` scores an interval at close (Stop / Switch /
auto-close / approved correction) from its `Success` + `Low Accuracy` fixes and stores the
result on the row; `log_geolocation_batch` keeps `fix_count` / `last_fix_at` live on an open
interval in one `UPDATE` (no `modified` bump, no save racing the clock actions). **Refresh
Tracking Health** on the form recomputes on demand, for a catch-up batch that arrived after
close. `Low Accuracy` rows (fixes worse than `min_accuracy_m`, kept when
`keep_low_accuracy_fixes` is on) count toward coverage — they prove the phone was reporting —
but are excluded from distance and stop detection, where a 300 m fix would invent movement.

## Pay rates and costing (v1.480.0)

**WI-016 chose costing-rate-only**: keep pay out of ERPNext, give each employee an Activity
Cost row with a typed-in costing rate, and let Timesheets cost from that. **Nik reversed that
on 2026-09-17.** Pay now lives on the Employee as effective-dated `Employee Pay Rate` rows
(`custom_pay_rates`, a fixture Custom Field at **permlevel 1** — HR Manager, Accounts Manager,
System Manager; `patches/seed_employee_pay_visibility` grants the read), and everything
downstream is *derived* by `costing.py`:

- `rate_for(employee, on_date)` — the row with the latest `effective_from` on or before the
  day; a Salaried row is costed at `annual_salary / 2080`; a blank burden takes
  `Time Kiosk Settings.default_burden_pct` (an explicit 0 does not); `burdened_rate =
  pay_rate × (1 + burden / 100)`.
- `stamp_position` / `stamp_cost` — at Start the interval gets the position and the rate;
  `labor_cost = worked hours × burdened rate` is written by the interval's own `validate`
  whenever `end_time` is set, so an approved correction keeps it honest. No rate → the block
  stays blank, so a missing rate reads as missing rather than as zero cost.
- The Timesheet Detail line the interval syncs to gets `costing_rate` / `costing_amount` from
  the burdened rate, because ERPNext's `TimesheetDetail.update_cost` keeps a non-zero
  costing rate and, for a line with no activity type, computes nothing at all. It also gets
  `custom_job_interval` (fixture) so a correction can find the line again. `Project Budget
  Category` "Labor" already sums `costing_amount` of submitted Timesheets.
- `sync_activity_costs` upserts `Activity Cost` per (employee, Activity Type) with
  `costing_rate = burdened rate as of today` — new rows with `billing_rate 0`, an existing
  `billing_rate` never touched — so anything that still costs from Activity Cost (a Timesheet
  typed by hand) agrees with the kiosk. Triggers: Employee `on_update` when the rates changed
  (compared against `get_doc_before_save()`), Activity Type `after_insert`, and a daily
  `sync_all_activity_costs` for future-dated rows. Every write is `ignore_permissions`, every
  failure is logged and swallowed: **a costing table must never block an Employee save.**

`validate_employee_pay_rates` (Employee `validate`) checks each row has the amount its type
needs, no two rows share an effective date, sorts them and fills `hourly_equivalent`. It reads
the table through `getattr(doc, "custom_pay_rates", None)` because the hook fires during
ERPNext's own test bootstrap before the custom field exists.

### Labor Cost Analysis report

`report/labor_cost_analysis/` is the reading surface for the stamps above: hours (net of
pauses, credited whole to the day the interval starts — the same rule as the payroll
workbook, so the two agree on any period), burdened labour cost, straight-time pay and an
average burdened rate, grouped by **Project**, **Employee**, **Position**, **Activity Type**
or **Project and Employee**. Three things it does on purpose. **It reads the stamped
`labor_cost`, never a live rate** — re-pricing history from today's rate would move every past
number on the day of a pay review. **Unrated intervals are counted, not hidden**: an interval
closed before the employee had a pay-rate row carries no cost, and the `Unrated` column says
how many rows are understating the job. **Budget appears only when grouped by Project**, from
the budget line whose category declares Timesheets as its actual source — the budget model's
own definition of labour, rather than the label "Labor" that somebody can rename. Roles are
the four that read Job Interval at permlevel 0 other than `Employee`;
`tests/test_workforce_report_labor_cost.py` pins the set, the grouping list against the JS
Select, and that every money column is Currency.

## Overtime (v1.480.0)

`overtime.py` is frappe-free (`tests/test_workforce_overtime.py`). Utah has no daily rule:
hours past `overtime_weekly_hours` (40) in a fixed workweek starting on
`overtime_week_start` (Sunday) are overtime, the interval that crosses the line is split at
it, and hours worked earlier in the same workweek — even before the pay period began — push
the threshold. An interval is credited whole to the day it *starts*, as
`payroll_export.worked_hours` always has.

`payroll_export.py` writes the split into the provider sheet's `Regular Hours` and
`Overtime Hours`; the 14-column contract is untouched and **`Qualified OT` stays blank** — it
is the federal figure and stays the firm's, for the reasons that module's docstring has always
given. Salaried rows are unchanged (flat 86.67, no overtime). The workbook gains a second
sheet, **Internal Costing** (Emp Num, Employee, Position, Tier, Pay Type, Hourly Rate,
Regular, OT, Straight-time Gross = (reg + ot) × rate with no premium math, Burden %, Burdened
Labor Cost), whose first row says it is not part of the submission. It is built with
`openpyxl` because v16's `frappe.utils.xlsxutils.make_xlsx` writes one sheet through
`xlsxwriter`. The desk report `Payroll Hours Export` shows Regular and Overtime hours; the
download is still gated on `_may_export` (System Manager / HR Manager / Accounts Manager),
which is also the set that can read pay.

## Corrections (v1.480.0)

Nobody edits a Job Interval by hand. A technician files a `Time Correction Request` from the
kiosk's My Day view (`Adjust Times`, `Change Project`, `Missed Clock-Out`, or `Missed Entry`
for a session that never existed; the controller enforces which proposals each needs and
that the interval is theirs), the supervisor is emailed, and a reviewer decides on the desk
form. Reviewers are HR Manager, Projects Manager, System Manager **or the employee's
`reports_to` user**, who need hold none of those roles (`permissions.can_review`; the
`has_permission` hook grants them read on the row).

`corrections.approve_request` refuses when the interval's Timesheet line is on a
**submitted** Timesheet (cancel or amend it first — the message says so), records the
`original_*` values on the first correction only, applies the proposal (a Missed Entry
creates a Completed interval; a Missed Clock-Out closes the open one, stamping the photo
gate through `photo_gate.resolve`, which never prompts), saves (validate recomputes the cost),
recomputes tracking health, re-syncs the Timesheet line (`api.time_kiosk.resync_interval_timesheet`
removes the old Draft line — deleting the Timesheet when that was its only line — and adds it
again on the right day) and emails the technician. `decline_request` emails too. Statuses:
Requested → Approved / Declined, or **Canceled** (one *l*, house style) by the employee while
Requested.

## Sweeper (v1.480.0)

`sweeper.auto_close_stale_intervals` runs hourly. An interval still Open or Paused
`auto_close_after_hours` (14) after it started is closed with the most defensible end time:
the **pause time** if Paused, else the **latest location fix** if it is after the start, else
**start + the limit** — `decide_end_time` is a pure function (`tests/test_workforce_sweeper.py`)
that also refuses a fix from before the start or from a phone whose clock is in the future.
The row is flagged `auto_closed` with the rule that decided; the photo gate is stamped without
prompting (there is nobody to ask); health and cost are computed; the Timesheet line is
synced; the employee and their supervisor are emailed. Stamp-first, at-most-once, per-interval
try/except with a commit each, so one bad row cannot stop the sweep and a closed row is never
revisited. The technician's remedy is a Missed Clock-Out correction with the real time.

## Supervisor digest (v1.480.0)

`digest.send_supervisor_digests`, cron `45 6 * * *`, gated by
`Time Kiosk Settings.send_supervisor_digest`. Every `reports_to` user gets their team; every
enabled HR Manager gets the company. Yesterday's hours per person (Completed, net of pauses),
auto-closed intervals, intervals with health `Gaps`/`None`, off-site clock-ins, and the team's
`Requested` correction requests, with links to the Location Timeline and the request list. A
recipient with no intervals and no pending requests gets nothing — an empty digest trains
people to delete the digest.

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

The kiosk prompts in the browser too (`public/js/kiosk/`), but that prompt is a courtesy. A
field device is offline half the day and serving a cached bundle of unknown age;
client-side validation there is a suggestion. `log_time` re-reads Time Kiosk Settings on
every call and is what actually decides. The two closers with nobody to prompt — the sweeper
and an approved Missed Clock-Out — use `photo_gate.resolve`, which returns the verdict
`check` would have stamped without ever throwing.

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

**It computes hours worked, and since v1.480.0 the Regular / Overtime hours split** (see
*Overtime* above). **It does not compute qualified overtime, and it must not.** `hrms` is
**not installed on this site**: there is no Salary Structure, no salary slips, no payroll
module of any kind. So `Qualified OT`, `PTO`, `Holiday`, `Bonus`, `Commission`,
`Reimbursement` and `Services` are emitted **blank**, in position, for the provider to fill
exactly as they do today. `Qualified OT` is a federal tax figure; reimplementing FLSA premium
arithmetic to save a payroll bureau a calculation they already perform correctly is the
worst trade available.

Salaried employees report a flat 86.67 hours (2080 / 24), matching the submitted file.
The provider's employee number lives in core `Employee.employee_number`, seeded by
`patches.seed_payroll_employee_numbers`; an employee without one still appears in the
output, flagged, because silently dropping somebody from a payroll file is the worst failure
mode this module has.

> **Not ready for cutover.** The acceptance criterion is that this reconciles exactly
> against a manually produced sheet for a complete pay period. As of 2026-09-17 that is
> impossible: `tabJob Interval` has **0 rows** in production. There is nothing to reconcile
> until the kiosk has been in real use for a full period.

## Bench-free suites

| Suite | Stubs `frappe`? |
|---|---|
| `tests/test_workforce_tracking_health.py` | no |
| `tests/test_workforce_overtime.py` | no |
| `tests/test_workforce_costing.py` | yes — own CI step |
| `tests/test_workforce_sweeper.py` | yes — own CI step |
| `tests/test_time_correction_requests.py` | no (filesystem + `ast`) |

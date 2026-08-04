# Field photos and payroll hours — runbook

Operational notes for WP-2 (photo capture gate), WP-3 (photo routing) and WP-8 (payroll
hours export), all shipped in v1.241.0.

Design rationale lives next to the code — see
[`workforce/README.md`](../erpnext_enhancements/workforce/README.md) and the module
docstrings in `workforce/photo_gate.py`, `workforce/photo_routing.py` and
`workforce/payroll_export.py`. This file is what you need at 7am.

---

## Before you switch anything on

**The time clock is essentially unused.** As of 2026-08-04 production has **0 Job Intervals**
and 16 Time Kiosk Logs. Everything below is built and tested but has never met a real crew.
Roll the kiosk out first; switch the photo gate on second, and not on the same day.

---

## WP-2 — the photo capture gate

### Turning it on

**Time Kiosk Settings → Job Photo Capture.**

| Setting | Default | Effect |
|---|---|---|
| `require_job_photos` | **off** | Master switch. A technician cannot Switch or Stop without a photo or a reason. |
| `min_photos_per_interval` | 1 | How many photos satisfy the gate. A configured 0 is floored to 1. |
| `allow_photo_skip` | **on** | Let a technician close with a reason instead of a photo. |
| `require_skip_reason` | on | A skip must carry text. |

Changes take effect on the next `log_time` call. No deploy, no restart, and no waiting for a
device to pick up a new bundle — the server re-reads settings every time.

### Turning it off

Untick `require_job_photos`. Photos already captured are untouched; intervals already closed
keep their recorded `photo_status`.

### What is enforced, and where

Server-side, in `api.time_kiosk.log_time`, for **Switch** and **Stop** only. Pause and Resume
are not gated — pausing for lunch is not the end of a job, and requiring a photo for it would
train everybody to skip.

The kiosk also prompts in the browser. That prompt is a courtesy: a field device is offline
half the day and serving a cached bundle of unknown age, so client-side validation there is a
suggestion, not enforcement.

### The offline behaviour — read this before fielding a complaint

**A photo still queued on a phone satisfies the gate.** The row is written to the Job Interval
the instant the shutter fires, carrying a device-minted `client_uid`; the bytes upload
whenever there is signal. So:

- a technician on a site with no coverage **can** clock out;
- the photo is not lost — it shows as `Pending` on the interval and in Job Photo Compliance;
- if the upload never completes, that is visible as a pending row rather than a missing one.

This was a deliberate choice and it is the difference between the feature shipping and it
being switched off in week two. **`allow_photo_skip = 0` turns the gate into a hard block with
no escape hatch — that remains an open people decision, not an engineering one.**

### When somebody says "it won't let me clock out"

1. Are they trying to Switch/Stop, or Pause? Pause is never gated.
2. Did the camera capture actually register? Check the Job Interval's Photos table. A row
   with status `Pending` is enough.
3. Is `allow_photo_skip` off? Then there is no way through by design — that is the setting to
   revisit, not the technician.
4. Emergency: untick `require_job_photos`. Everyone can clock out immediately.

---

## WP-3 — where the photos go

A photo whose bytes have landed is enqueued to `photo_routing.route_job_photo` (long queue,
`enqueue_after_commit`). It:

1. copies the File onto the **Project** and the **Task**;
2. tags every copy `job-photo`, `cust:<customer>`, `vs:<value stream>`, `shot:<YYYY-MM-DD>`;
3. attaches a copy to the **Customer** *only* when the Project cannot carry it into Drive.

**It never calls Google Drive.** `google_drive/drive_sync.on_file_attached` already fires on
every `File` `after_insert`, finds the folder id on the attached document, and enqueues the
upload — idempotently (it bails once `custom_drive_file_id` is set) and with quota/auth
failures logged to Drive Sync Log with a replayable payload. So routing's job is to make sure
the File is attached to something that *has* a folder.

### Finding photos

**Job Photo Library** (Workforce; System Manager / Sales Manager / Sales User / Projects
Manager). Filter by customer, project, value stream, featured-only, and date range. Thumbnails
inline. No knowledge of which technician clocked onto what is required — that is the whole
point.

Only `Uploaded` photos are listed. A pending row has nothing to show, and a library rendering
broken thumbnails teaches people it is broken. Pending photos live in Job Photo Compliance.

### Featuring a job for marketing

Tick **Feature This Job** on the Project and write a Feature Note. The **Featured Jobs**
report (Project Enhancements) sorts upcoming work first, so a photographer can be booked
*before* the crew leaves — a story needs the before, the middle and the after, and two of
those are unrecoverable once the truck pulls away. A flagged job with zero photos shows in red.

### If photos are not reaching Drive

1. Is Drive sync enabled at all? (`google_drive` settings.)
2. Does the Project have a `custom_drive_folder_id`? If not, routing should have fallen back
   to the Customer — check the Customer's attachments.
3. Is `custom_drive_folder_missing` set? Somebody deleted the folder in Drive;
   `drive_sync.reconcile_drive_links` sets that flag and routing honours it.
4. Check **Drive Sync Log** for `Upload to Drive` / `Failed` rows. The payload there is
   replayable.

The local copy is never at risk in any of these paths.

---

## WP-8 — payroll hours export

### What it produces

The **Shaw & Nielsen** semi-monthly workbook — firm code `SHAWA2530`, client code `5813` —
reproduced from Job Interval data: six-line header block, three-row stacked column header,
14 columns in fixed positions, trailing Totals row, sheet named
`5813 7-16-2026 to 7-31-2026`. Transcribed from a real submitted file, not inferred.

Open **Payroll Hours Export** (Workforce; System Manager / HR Manager / Accounts Manager),
check the numbers on screen, then press **Download Workbook**. Leaving the dates blank gives
you the *previous* period — the one actually being submitted.

### What it will not do

`Qualified OT`, `Overtime`, `PTO`, `Holiday`, `Bonus`, `Commission`, `Reimbursement` and
`Services` are emitted **blank**, in position, for the provider to fill exactly as they do
today.

This is a refusal, not an omission. The work package assumed "rates are already configured in
ERPNext" — they are not, and there is nowhere for them to be. **`hrms` is not installed on
this site**: no Salary Structure doctype, no salary slips, no payroll module, 0 Timesheets.
`Qualified OT` is a federal tax figure; reimplementing FLSA premium arithmetic from scratch to
save a payroll bureau a calculation they already perform correctly is the worst available
trade. If leadership wants ERPNext to own the OT split, that is an `hrms` installation
decision and a separate work package.

Salaried employees report a flat **86.67** hours (2080 / 24), matching the submitted file.

### Employee numbers

The provider keys on their own number (22, 7, 17 …), which is not the ERPNext employee id.
It lives in core `Employee.employee_number` and was null for all 15 active employees;
`patches.seed_payroll_employee_numbers` transcribes the 16 rows of the submitted file, along
with the four salaried classifications and the three £/$125 healthcare stipends.

Matching is exact on first + last name and **ambiguity is a refusal** — two employees matching
one entry means neither is touched and the collision is logged, because a payroll number on
the wrong person is invisible whereas a missing one is flagged in red in the report.

A new hire will have no number until somebody sets it. They still appear in the export, with
a blank first column, flagged. Silently dropping somebody from a payroll file is the worst
failure mode this module has, so it does not do that.

### Do not cut over yet

The acceptance criterion is that this reconciles **exactly** against a manually produced
spreadsheet for at least one complete pay period. That is currently impossible — there are 0
Job Intervals in production. Sequence:

1. Kiosk in genuine daily use by the whole crew.
2. One complete semi-monthly period clocked.
3. Export it; diff it line by line against the manual sheet for that period.
4. Only then discuss cutover.

Expect the first diff to be non-zero and to be about interval boundaries rather than
arithmetic — an interval spanning midnight into the next period is credited whole to the
period it *started* in, matching how the manual sheet has always handled an overnight
callout. Splitting it would be defensible, but it would silently disagree with history.

---

## Retention

**Still an open decision.** `Time Kiosk Settings.retention_days` covers location logs only and
is enforced by `purge_old_location_logs`. **No retention policy has been set or implemented
for job photos**, deliberately — that decision was flagged as leadership's and nothing here
quietly assumes an answer. Photos accumulate indefinitely until one is made.

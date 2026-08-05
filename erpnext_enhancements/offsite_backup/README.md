# Offsite Backup

Uploads Frappe's own backups to a **Google Drive Shared Drive**, proves each upload
arrived intact, prunes on a retention policy, and shouts when backups stop
happening.

Frappe v16 **removed** the built-in Google Drive / Dropbox / S3 backup doctypes from
core — they were split out into a separate `frappe/offsite_backups` app. That app is
deliberately not installed here: it has no retention logic at all, and unbounded
growth in a Shared Drive is its own outage. So this module is that job done
properly, in about 700 lines.

| File | What it is |
|---|---|
| [`drive.py`](drive.py) | Drive v3 transport — auth, folder probe, resumable upload, paginated list, delete |
| [`backup.py`](backup.py) | Orchestration — the scheduled shims, `execute_backup`, verification, retention, the watchdog |
| [`doctype/offsite_backup_settings/`](doctype/offsite_backup_settings/) | Single: credentials, folder, retention, alert thresholds, the two buttons |
| [`doctype/offsite_backup_log/`](doctype/offsite_backup_log/) | One row per run |

## What runs when

Registered in [`hooks.py`](../hooks.py) under `scheduler_events["cron"]`, in site
timezone (`America/Denver`):

| Cron | Job | Does |
|---|---|---|
| `0 2 * * *` | `run_daily_backup` | Database only |
| `0 3 * * 0` | `run_weekly_backup` | Database + public files + private files |
| `0 8 * * *` | `watchdog` | Alerts when either tier has gone stale |

The slots are deliberately clear of the existing cron cluster at 05:00, 06:00,
06:30, 07:00 and 07:15.

**Every scheduled entry point is a thin shim.** It checks the master switch, calls
`reconcile_stale_runs()`, bails if a run is already in flight, and enqueues the real
work on the **long** queue with a 4h timeout. The scheduler is a single shared
process walking every job on the site; a multi-hour dump running inside it would
stall the QuickBooks sync and everything else on the bench.

## Setup

Done once, by hand, in the Google Cloud Console — this module deliberately creates
neither the service account nor the folder.

1. Create a **service account dedicated to backups**. Not the one
   [`../google_drive/`](../google_drive/README.md) uses: that credential is shared
   across project-folder provisioning, and the encrypted database dumps should not
   be reachable from it. It needs no IAM roles and no domain-wide delegation.
2. Download its JSON key.
3. Create (or pick) a folder on a **Shared Drive**, dedicated to these backups.
4. Share that folder with the service account's `client_email` as **Content
   manager**. Contributor is not enough — pruning needs delete rights on the
   folder's *contents*.
5. In **Offsite Backup Settings**: paste the key, paste the folder ID from its
   Drive URL (`.../folders/<this>`), press **Test Connection**, then press **Run
   Backup Now** and watch it succeed before ticking **Enable Offsite Backups**.

There is **no Shared Drive ID field**, on purpose. The drive id is read off the
folder metadata every run, so it cannot go stale the day somebody moves the folder.

## Taking a backup by hand

From the UI: **Run Backup Now** on the settings form prompts for *Database only* or
*Full (database + files)* and queues it. The button works whether or not
`enabled` is ticked, so the configuration can be proved before the schedule is
switched on.

From the VM, in the foreground — which is what you want when you are debugging
rather than scheduling, because failures print to the terminal instead of only
landing in a log row:

```bash
cd /home/frappe/frappe-bench
bench --site <site> execute erpnext_enhancements.offsite_backup.backup.execute_backup --kwargs '{"backup_type": "Manual Full"}'
```

`backup_type` is one of `Daily`, `Weekly`, `Manual`, `Manual Full`.

## The parts that are load-bearing

Every one of these is a failure mode that looks like success until the day you need
the backup.

### site_config.json is never uploaded

`_create_backup()` ships `backup_path_db`, plus `backup_path_files` and
`backup_path_private_files` on a full run. It never ships `backup_path_conf`.

Frappe's `backup_encryption()` encrypts exactly three things — the database dump and
the two file archives. `copy_site_config()` writes a **verbatim plaintext copy** of
site_config.json and it is never encrypted, *even though Frappe still gives it the
same `-enc` suffix as its encrypted siblings*. That is the trap: the filename says
encrypted and the bytes are not. The file contains `db_password`, `encryption_key`
and `backup_encryption_key`. Uploading it beside the encrypted dump would put the
decryption passphrase in the same folder as the ciphertext, and `encrypt_backup`
would be ornamental — anyone who could read the backup folder could read the
database.

### The destination must be a Shared Drive

`execute_backup` raises if `check_folder` returns no `driveId`. A service account has
no Drive storage quota of its own, so a folder in somebody's My Drive charges every
upload to *that person's* quota. It works, right up until their Drive fills, and then
fails in a way that reads as an API problem.

### `canDeleteChildren`, not `canDelete`

`canDelete` is the caller's right to delete **the folder itself**. Pruning needs the
right to delete the folder's **contents**. Checking the wrong one passes Test
Connection and then fails every night at prune time.

### The retry budget is per-stall, not per-file

A 20 GiB tarball is roughly a thousand 20 MiB chunks and can be three hours on the
wire. Five transient 5xx spread over three hours is normal Drive behaviour, not a
broken upload — so `upload_file` resets its attempt counter *and* its backoff every
time a chunk lands. Five **consecutive** failures abort; five failures over three
hours of steady progress do not.

### An unverified upload is deleted, not kept

Drive's reported `size` is compared to the local byte count, and where Drive returns
an `md5Checksum` it is compared to a locally computed MD5 (streamed in 8 MiB blocks
— a multi-GB file is never read into memory). On any mismatch the remote object is
**deleted** and the run fails. A truncated upload left in the folder is worse than no
upload, because it looks like a backup. Which check actually ran (`size` vs
`size + md5`) is recorded per artefact in the log's Details.

### Retention has two independent floors

Objects older than `retention_days` are deleted, except:

- the newest `min_keep` objects are never pruned, whatever their age; and
- **nothing** is pruned when the listing returns fewer than `min_keep` objects, so a
  partial or truncated listing cannot cascade into deleting the tail of the archive.

`createdTime` is parsed as RFC-3339 into an aware UTC datetime and compared against
an aware UTC cutoff. Anything unparseable is left alone — an unreadable timestamp is
not evidence that a file is old.

Pruning is best-effort: the upload has already succeeded by then, so a listing or
delete failure is recorded in the log and reported, not turned into a failed run.

> The folder must be **dedicated** to these backups. Retention deletes by age, not
> by filename.

### `Running` rows are the concurrency guard, so they must be reconcilable

A run inserts its log row as `Running` and **commits immediately** — an uncommitted
row is invisible to the process that needs to see it. The cost of that design is
that a run only ever leaves `Running` from inside its own process: a SIGKILL, an OOM
during a multi-GB dump, or a plain `bench restart` mid-upload strands the row
forever, and the stranded row then blocks every future run, silently.

`reconcile_stale_runs()` fails any `Running` row older than the job timeout plus a
grace margin, and is called at the top of **every** entry point including the
watchdog.

### Skips are logged

A run that bails because another is in flight writes a `Skipped` row. A line in a log
file nobody reads is how a weekly backup quietly stops happening for a year.

### The watchdog checks the two tiers separately

Database (any type) against `alert_if_older_than_hours`, full (`Weekly` /
`Manual Full`) against `alert_if_full_older_than_hours`. Checked together, a healthy
nightly database backup masks a weekly file backup that has been skipped every Sunday
for months.

This is also the only check that catches *nothing running at all*. A failure email
only ever fires when a job actually ran and threw; a disabled scheduler, a dead
worker or a dropped hook produce no failures to report, just silence.

### Nothing ever renders frame locals — and there are two doors

Failures record `frappe.get_traceback()` with the default `with_context=False`. With
context on, the rendered locals of a failing Drive call include the parsed service
account key — which would then sit in the log row, the Error Log **and** the outbound
failure email. That closes the front door.

The back door is Frappe's own. `background_jobs.execute_job` logs any *escaping*
exception with `frappe.log_error(title=method_name)` and **no message**, and
`log_error` with no message falls back to `get_traceback(with_context=True)`. Frappe's
sanitiser redacts only the exact dict keys `password`, `passwd`, `secret`, `token`,
`key` and `pwd` — and a service account key's field is named **`private_key`**, which
is not one of them. So a plain `raise` would publish the key into the Error Log no
matter how carefully this module logged its own traceback.

`execute_backup` therefore re-raises a message-only `RuntimeError(...) from None`
pointing at the log row. `from None` matters: implicit chaining would render the
original frames anyway. `_redact()` strips PEM private-key blocks from anything stored
or emailed as a third line of defence.

### The failure alert is committed, not just queued

`frappe.sendmail` only *inserts* an Email Queue row; it does not commit. On the failure
path the exception then reaches `execute_job`, which calls `frappe.db.rollback(chain=True)`
**before** it logs — so an alert queued and left uncommitted is thrown away, and a failed
backup notifies nobody. Success alerts return normally and are committed by `execute_job`,
so the bug would have been invisible in any test with `notify_on_success` on and only bitten
on the path the alert exists for. The `finally` commits after notifying.

(`sendmail(now=True)` is not a fix here — on v16 it defers via `frappe.db.after_commit`,
which the same rollback resets.)

### `SystemExit` is caught too

`execute_backup` catches `BaseException`, not `Exception`. With `encrypt_backup` on,
Frappe's `BackupGenerator.backup_encryption()` calls `sys.exit(1)` when `gpg` is missing
— so the single most likely failure in the whole run raises `SystemExit`. Catching only
`Exception` would skip the bookkeeping entirely and finalise a `Failed` row with a blank
error and an alert reading "No traceback was captured", which is the least useful possible
report of the most likely failure. `SystemExit` and `KeyboardInterrupt` are re-raised
unchanged so a worker shutdown still shuts the worker down.

## Relationship to `google_drive/`

None, deliberately. [`../google_drive/`](../google_drive/README.md) provisions
per-project folders on a different Shared Drive with a different service account and
its own client builder. The duplication between `drive.py` and `drive_utils.py` is
the isolation: a compromise of the widely-shared project Drive credential must not
also hand over the encrypted database dumps. Do not consolidate them.

## Alerts

`alert_recipients` on the settings form, comma separated; falls back to
`admin_alert_recipients` in `site_config.json` when blank, so the first failure after
go-live is not reported to nobody. `notify_on_success` is off by default — the
watchdog already reports silence, and a nightly "it worked" email is a nightly email
nobody reads.

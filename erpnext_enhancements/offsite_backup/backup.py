# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Orchestration for offsite backups to a Google Drive Shared Drive.

Frappe v16 removed the built-in Google Drive / Dropbox / S3 backup doctypes from
core; they were split out into a separate ``frappe/offsite_backups`` app that has
no retention policy at all. This module is that app's job done properly for this
site: take the dump, ship it to a Shared Drive, *prove* the bytes arrived, prune on
a policy with two independent floors under it, and shout when backups stop
happening.

Three scheduler entry points, all registered in ``hooks.py``:

* :func:`run_daily_backup` — 02:00 site time, database only.
* :func:`run_weekly_backup` — Sunday 03:00 site time, database + public files +
  private files.
* :func:`watchdog` — 08:00 site time, alerts when either tier has gone stale.

Plus :func:`run_backup_now`, the whitelisted endpoint behind the settings form's
**Run Backup Now** button, and :func:`execute_backup`, which is also the
foreground entry point documented in the module README for running a backup by
hand from the VM.

Design notes that are load-bearing rather than stylistic:

**The scheduler tick never does the work.** Every scheduled entry point is a shim
that checks the master switch and hands off to the long queue. The scheduler is a
single shared process walking every job on the site; a multi-hour dump running
inside it stalls the QuickBooks sync, the KPI snapshots and everything else.

**The concurrency guard is a committed database row, not a lock.** ``Running``
rows are visible to other processes only once committed, so :func:`execute_backup`
commits its log row before it does anything else. The cost of that design is that
a killed worker strands the row — which is what :func:`reconcile_stale_runs`
exists to undo, and why it is called at the top of every entry point.

**Nothing here ever renders frame locals** — and there are *two* doors to keep
shut. Failures record ``frappe.get_traceback()`` with the default
``with_context=False``, because with context on the rendered locals of a failing
Drive call include the parsed service account key. That closes the front door.
The back door is Frappe's own: ``background_jobs.execute_job`` logs any escaping
exception with ``frappe.log_error(title=method_name)`` and no message, and
``log_error`` with no message falls back to ``get_traceback(with_context=True)``
— so a bare re-raise would publish the key into the Error Log regardless of what
this module logged. :func:`execute_backup` therefore re-raises a message-only
error ``from None``. :func:`_redact` is the third line of defence.

**site_config.json is never uploaded** — see :func:`_create_backup`, which is the
single place that decides what gets shipped and explains why.
"""

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

import frappe
from frappe import _
from frappe.utils import (
	add_to_date,
	cint,
	escape_html,
	now_datetime,
	time_diff_in_hours,
	time_diff_in_seconds,
)

from erpnext_enhancements import email_style
from erpnext_enhancements.offsite_backup import drive

# Matches the ``timeout`` the jobs are enqueued with. Also the basis for deciding
# that a ``Running`` row is a corpse rather than a run.
JOB_TIMEOUT = 4 * 60 * 60
STALE_RUN_GRACE_SECONDS = 15 * 60

# Refuse to start a dump with less headroom than this on the backup volume.
# Filling a production disk mid-``mysqldump`` takes the site down; failing the
# backup does not.
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024

# Streamed in blocks — a multi-GB tarball must never be read into memory to be
# checksummed.
MD5_BLOCK = 8 * 1024 * 1024

# Runs that include the files archives as well as the database.
FULL_TYPES = ("Weekly", "Manual Full")
SCHEDULED_TYPES = ("Daily", "Weekly")
# Everything :func:`run_backup_now` is allowed to start. It is a whitelisted
# endpoint, so its argument is untrusted input, not a hint.
MANUAL_TYPES = ("Manual", "Manual Full")
ALL_TYPES = SCHEDULED_TYPES + MANUAL_TYPES

_PRIVATE_KEY_RE = re.compile(
	r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S
)


# ------------------------------------------------------------------- settings


def _settings():
	"""The Single, or ``None`` when it cannot be read.

	``None`` is returned rather than raised for the same reason the doc_events in
	this app guard their custom-field reads: these functions run on the scheduler,
	including during a migrate on a site where the doctype does not exist yet.
	Dormant is the safe reading.
	"""
	try:
		return frappe.get_cached_doc("Offsite Backup Settings")
	except Exception:
		return None


def _service_account_key(settings):
	return settings.get_password("service_account_json", raise_exception=False)


def _recipients(settings):
	"""Alert recipients: the settings field, falling back to site_config.

	The fallback matters because the field ships empty — without it the first
	failure after go-live would be reported to nobody.
	"""
	raw = (settings.get("alert_recipients") or "").strip() if settings else ""
	if not raw:
		configured = frappe.conf.get("admin_alert_recipients") or ""
		raw = ", ".join(configured) if isinstance(configured, (list, tuple)) else str(configured)
	return [address.strip() for address in raw.replace(";", ",").split(",") if address.strip()]


def _redact(text):
	"""Strip anything shaped like a PEM private key out of ``text``.

	Belt and braces over the ``with_context=False`` rule: tracebacks should never
	carry key material, and this makes sure that a future library that decides to
	put a key in an exception message cannot quietly change that.
	"""
	if not text:
		return text
	return _PRIVATE_KEY_RE.sub("[redacted private key]", str(text))


# -------------------------------------------------------------- entry points


def run_daily_backup():
	"""Scheduler entry point — 02:00 site time. Database only."""
	_schedule("Daily")


def run_weekly_backup():
	"""Scheduler entry point — Sundays 03:00 site time. Database + files."""
	_schedule("Weekly")


def _schedule(backup_type):
	"""The thin half of a scheduled run: guard, then enqueue. Never dumps."""
	settings = _settings()
	if not settings or not cint(settings.enabled):
		return

	reconcile_stale_runs()

	running = _running_run()
	if running:
		# Logged, not merely returned. A weekly backup that silently stops
		# happening because a daily one is wedged is the exact failure this module
		# is supposed to make impossible, and a line in a log file nobody reads is
		# not a signal.
		_log_skipped(
			backup_type,
			f"An offsite backup was already running ({running.get('name')}, "
			f"{running.get('backup_type')}, started {running.get('started_at')}). "
			"This run was skipped rather than queued behind it.",
		)
		return

	_enqueue(backup_type)


def _enqueue(backup_type):
	frappe.enqueue(
		"erpnext_enhancements.offsite_backup.backup.execute_backup",
		queue="long",
		timeout=JOB_TIMEOUT,
		job_id=f"offsite-backup::{backup_type}",
		deduplicate=True,
		enqueue_after_commit=True,
		backup_type=backup_type,
	)


@frappe.whitelist()
def run_backup_now(backup_type="Manual Full"):
	"""Start a manual backup from the settings form.

	Deliberately does **not** require ``enabled``: the point of the button is to
	prove the configuration works before the nightly schedule is switched on.
	It does require the folder and the key, because without them there is nothing
	to prove.
	"""
	frappe.only_for("System Manager")

	backup_type = (backup_type or "").strip()
	if backup_type not in MANUAL_TYPES:
		# Whitelisted endpoint: the argument arrives from the network, so it is
		# validated against the allow-list rather than passed through. Nothing here
		# should be able to start a run labelled as a scheduled one.
		frappe.throw(
			_("{0} is not a manual backup type. Choose {1} or {2}.").format(
				frappe.bold(backup_type or "(blank)"), "Manual", "Manual Full"
			)
		)

	settings = _settings()
	if not settings:
		frappe.throw(_("Offsite Backup Settings is not available on this site."))
	if not (settings.drive_folder_id or "").strip():
		frappe.throw(_("Set a Drive Folder ID before running a backup."))
	if not _service_account_key(settings):
		frappe.throw(_("Paste the Google service account key before running a backup."))

	reconcile_stale_runs()

	running = _running_run()
	if running:
		frappe.throw(
			_("An offsite backup is already running ({0}, started {1}). Wait for it to finish.").format(
				running.get("name"), running.get("started_at")
			)
		)

	_enqueue(backup_type)
	return {
		"ok": True,
		"message": _("{0} backup queued on the long queue. Progress is written to Offsite Backup Log.").format(
			backup_type
		),
	}


# ------------------------------------------------------------------ the work


def execute_backup(backup_type="Daily"):
	"""Take a backup, ship it, verify it, prune, and record what happened.

	Runs on the long queue under the scheduled and manual entry points, and in the
	foreground when invoked through ``bench execute`` (see the module README) —
	which is the right way to debug, because failures print to the terminal
	instead of only landing in a log row.
	"""
	backup_type = _validated_type(backup_type)
	include_files = backup_type in FULL_TYPES

	log = _start_log(backup_type)
	summary = {"files_uploaded": 0, "bytes_uploaded": 0, "pruned_count": 0}
	details = {"backup_type": backup_type, "includes_files": include_files}

	settings = None
	status = "Failed"
	error_text = None

	try:
		settings = _settings()
		if not settings:
			raise RuntimeError("Offsite Backup Settings is not available on this site.")

		folder_id = (settings.drive_folder_id or "").strip()
		if not folder_id:
			raise RuntimeError("No Drive Folder ID is configured on Offsite Backup Settings.")

		service = drive.get_drive_service(_service_account_key(settings))
		folder = drive.check_folder(service, folder_id) or {}
		drive_id = folder.get("driveId")
		details["folder"] = {
			"id": folder.get("id"),
			"name": folder.get("name"),
			"drive_id": drive_id,
		}

		if not drive_id:
			# A service account has no Drive storage quota of its own. A folder in
			# somebody's My Drive charges every upload to *that person's* quota, so
			# the backups work until the day their Drive fills up and then fail in a
			# way that looks like an API problem. Refusing here is the only place
			# this is cheap to catch.
			raise RuntimeError(
				f"Drive folder {folder.get('name') or folder_id!r} is not on a Shared Drive "
				"(the API returned no driveId). Offsite backups must target a Shared Drive: "
				"a My Drive folder consumes the folder owner's personal storage quota, which "
				"the service account cannot see and cannot extend."
			)

		details["disk"] = _assert_disk_headroom()

		paths = _create_backup(include_files)
		details["artifacts"] = []
		for path in paths:
			artefact = _upload_and_verify(service, path, folder_id)
			details["artifacts"].append(artefact)
			summary["files_uploaded"] += 1
			summary["bytes_uploaded"] += artefact["bytes"]

		pruned, retention = _prune(service, folder_id, drive_id, settings)
		summary["pruned_count"] = pruned
		details["retention"] = retention

		status = "Success"
	except BaseException as exc:
		# BaseException, not Exception. Frappe's own BackupGenerator.backup_encryption()
		# calls sys.exit(1) when gpg is missing, and encrypt_backup is on for this
		# site — so the most likely single point of failure in the whole run raises
		# SystemExit. Catching only Exception would let it skip this block entirely
		# and finalise a Failed row with an empty error and an alert reading "No
		# traceback was captured", which is the least useful possible report of the
		# most likely failure.
		#
		# with_context=False (the default) on purpose — see the module docstring.
		error_text = frappe.get_traceback()
		# The enclosing job rolls back on its way out. Commit here so the outcome
		# survives that rollback rather than leaving a row stuck on Running.
		frappe.db.commit()

		if isinstance(exc, Exception):
			# Deliberately NOT a bare re-raise, and this is the important part.
			#
			# frappe.utils.background_jobs.execute_job logs any escaping exception
			# with frappe.log_error(title=method_name) and **no message**, and
			# frappe.utils.error.log_error falls back to
			# get_traceback(with_context=True) when it has no message — rendered
			# frame locals. drive.get_drive_service holds the parsed service account
			# key in its `info` local while it builds the credentials, and Frappe's
			# sanitizer redacts only the exact dict keys password / passwd / secret /
			# token / key / pwd. A service account key's field is named
			# `private_key`, which is not one of them.
			#
			# So a bare re-raise writes the private key into the Error Log through
			# Frappe's own back door, defeating the with_context=False rule above.
			# Raising a message-only error `from None` means the traceback handed to
			# the job runner contains no frame that ever held the key, and suppresses
			# the implicit __context__ chain that would otherwise render the original
			# frames anyway. The real traceback is already on the log row (redacted)
			# and in the failure alert.
			raise RuntimeError(
				f"Offsite backup ({backup_type}) failed — see Offsite Backup Log {log.name} "
				"for the traceback. It is deliberately not repeated here: Frappe logs an "
				"escaping exception with frame locals rendered, which would publish the "
				"service account key."
			) from None
		# SystemExit / KeyboardInterrupt: a worker shutdown, not a backup failure.
		# Propagate it unchanged so the worker's own shutdown path still works — and
		# it cannot carry a credential-bearing frame, because the only such frame
		# belongs to a call that has already returned by then.
		raise
	finally:
		# Guarded separately: a failure to write the log row must not replace the
		# real exception with a confusing one about the log row.
		try:
			_finalise(log, status, summary, details, error_text)
			frappe.db.commit()
		except Exception:
			try:
				frappe.log_error(frappe.get_traceback(), "Offsite Backup: log row not finalised")
			except Exception:
				# Writing the Error Log needs the same database that just failed.
				# Letting this escape would abandon the alert below and replace the
				# real exception with a confusing one about logging.
				pass
		# After the row is finalised, so the email can point at a row that says
		# what the email says.
		_notify(backup_type, log, status, summary, details, error_text, settings)
		# frappe.sendmail only INSERTs an Email Queue row; it does not commit. On the
		# failure path the exception is about to reach execute_job, which calls
		# frappe.db.rollback(chain=True) *before* it logs — which would throw the
		# queued alert away and leave a failed backup notifying nobody, while success
		# alerts (a normal return, committed by execute_job) sent fine. That is a bug
		# that only ever shows up on the path the alert exists for. Guarded so a
		# commit failure cannot mask the real error.
		try:
			frappe.db.commit()
		except Exception:
			pass

	return {
		"status": status,
		"log": log.name,
		"files_uploaded": summary["files_uploaded"],
		"bytes_uploaded": summary["bytes_uploaded"],
		"pruned_count": summary["pruned_count"],
	}


def _validated_type(backup_type):
	backup_type = (backup_type or "").strip()
	if backup_type not in ALL_TYPES:
		raise ValueError(f"Unknown backup type {backup_type!r}. Expected one of {', '.join(ALL_TYPES)}.")
	return backup_type


def _start_log(backup_type):
	"""Insert the ``Running`` row and commit it immediately.

	The commit is the whole point: the concurrency guard is a read from another
	process, and an uncommitted row is invisible to it.
	"""
	log = frappe.get_doc(
		{
			"doctype": "Offsite Backup Log",
			"backup_type": backup_type,
			"status": "Running",
			"started_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	return log


def _finalise(log, status, summary, details, error_text=None):
	log.status = status
	log.ended_at = now_datetime()
	log.duration_seconds = int(max(0, time_diff_in_seconds(log.ended_at, log.started_at)))
	log.files_uploaded = cint(summary.get("files_uploaded"))
	log.bytes_uploaded = cint(summary.get("bytes_uploaded"))
	log.pruned_count = cint(summary.get("pruned_count"))
	log.details = json.dumps(details or {}, indent=2, default=str)
	log.error = _redact(error_text) if error_text else None
	log.save(ignore_permissions=True)


# ----------------------------------------------------------------- artefacts


def _assert_disk_headroom():
	"""Refuse to start a dump that could fill the backup volume.

	``get_backup_path()`` returns a path relative to the bench ``sites``
	directory, which is the worker's working directory — resolved here rather
	than assumed, and reported in the log details so a surprising number is
	traceable to the volume it was measured on.
	"""
	from frappe.utils.backups import get_backup_path

	path = _nearest_existing_dir(os.path.abspath(get_backup_path()))
	usage = shutil.disk_usage(path)
	info = {"path": path, "free_bytes": usage.free, "total_bytes": usage.total}
	if usage.free < MIN_FREE_BYTES:
		raise RuntimeError(
			f"Only {usage.free / (1024 ** 3):.2f} GiB free on the backup volume ({path}); "
			f"at least {MIN_FREE_BYTES / (1024 ** 3):.0f} GiB is required. Refusing to start "
			"a dump that could fill the disk out from under the running site."
		)
	return info


def _nearest_existing_dir(path):
	current = path
	while current and not os.path.isdir(current):
		parent = os.path.dirname(current)
		if parent == current:
			break
		current = parent
	return current or "."


def _create_backup(include_files):
	"""Take the local dump and return **only** the artefacts we are willing to ship.

	``site_config.json`` is deliberately not in this list, and must never be added.

	Frappe's ``backup_encryption()`` encrypts exactly three things: the database
	dump, the public files archive and the private files archive.
	``copy_site_config()`` writes a **verbatim plaintext copy** of site_config.json
	and it is never encrypted — while Frappe still gives it the same ``-enc``
	suffix as its encrypted siblings, which is precisely the trap. That file
	contains ``db_password``, ``encryption_key`` and ``backup_encryption_key``.
	Uploading it alongside the encrypted dump would put the decryption passphrase
	in the same folder as the ciphertext and make ``encrypt_backup`` ornamental:
	anyone who can read the backup folder could read the database.

	So the shipped set is ``backup_path_db`` always, plus ``backup_path_files`` and
	``backup_path_private_files`` on a full run. Never ``backup_path_conf``.
	"""
	from frappe.utils.backups import new_backup

	backup = new_backup(
		ignore_files=not include_files,
		force=True,
		compress=include_files,
		verbose=False,
	)

	paths = [backup.backup_path_db]
	if include_files:
		paths.append(backup.backup_path_files)
		paths.append(backup.backup_path_private_files)

	missing = [path for path in paths if not path or not os.path.exists(path)]
	if missing:
		raise RuntimeError(
			"Frappe reported a backup but these artefacts are not on disk: "
			f"{', '.join(str(path) for path in missing)}."
		)
	return paths


def _upload_and_verify(service, local_path, folder_id):
	name = os.path.basename(local_path)
	local_bytes = os.path.getsize(local_path)

	remote = drive.upload_file(service, local_path, folder_id, name) or {}
	file_id = remote.get("id")
	if not file_id:
		raise RuntimeError(f"Drive accepted {name} but returned no file id.")

	try:
		verified = _verify(name, local_path, local_bytes, remote)
	except Exception:
		# A truncated object left in the folder is worse than no object at all: it
		# is indistinguishable from a good backup right up until somebody needs it.
		try:
			drive.delete_file(service, file_id)
		except Exception:
			frappe.log_error(
				f"Could not remove the unverified Drive object {file_id} ({name}) after a "
				"failed integrity check. Delete it by hand — it looks like a backup and "
				f"is not one.\n\n{_redact(frappe.get_traceback())}",
				"Offsite Backup",
			)
		raise

	return {
		"name": name,
		"bytes": local_bytes,
		"drive_file_id": file_id,
		"verified": verified,
		"created_time": remote.get("createdTime"),
	}


def _verify(name, local_path, local_bytes, remote):
	"""Prove the bytes in Drive are the bytes on disk. Returns which checks ran."""
	remote_size = remote.get("size")
	if remote_size is None:
		raise RuntimeError(f"Drive returned no size for {name}; the upload cannot be verified.")
	if int(remote_size) != local_bytes:
		raise RuntimeError(
			f"Size mismatch for {name}: {local_bytes} bytes uploaded, {int(remote_size)} bytes "
			"reported by Drive."
		)

	remote_md5 = remote.get("md5Checksum")
	if not remote_md5:
		# Drive omits md5Checksum for some object types. Size alone is a weaker
		# check, so which check actually ran is recorded rather than implied.
		return "size"

	local_md5 = _local_md5(local_path)
	if local_md5.lower() != str(remote_md5).lower():
		raise RuntimeError(
			f"MD5 mismatch for {name}: {local_md5} locally, {remote_md5} in Drive."
		)
	return "size + md5"


def _local_md5(path):
	# usedforsecurity=False: this is an integrity comparison against Drive's own
	# MD5, not a security primitive, and the flag keeps it working on a FIPS host.
	digest = hashlib.md5(usedforsecurity=False)
	with open(path, "rb") as handle:
		for block in iter(lambda: handle.read(MD5_BLOCK), b""):
			digest.update(block)
	return digest.hexdigest()


# ----------------------------------------------------------------- retention


def _prune(service, folder_id, drive_id, settings):
	"""Delete objects past ``retention_days``, behind two independent floors.

	Never prune below the newest ``min_keep`` objects, and do not prune at all if
	the listing came back with fewer than ``min_keep`` objects — a partial or
	failed listing must not be read as "there is nothing here to keep" and
	cascade into deleting the tail of the archive.

	Everything here is best-effort. The upload has already succeeded by the time
	this runs; a backup that could not tidy up is still a backup, so a listing or
	delete failure is recorded and reported rather than turned into a failed run.

	The destination folder must be **dedicated to these backups** — this deletes
	by age, not by filename, so anything else parked in the folder is in scope.
	"""
	retention_days = cint(settings.retention_days)
	min_keep = max(1, cint(settings.min_keep))
	info = {"retention_days": retention_days, "min_keep": min_keep}

	if retention_days <= 0:
		info["action"] = "Retention is off (retention_days = 0). Nothing pruned."
		return 0, info

	try:
		listing = drive.list_backup_files(service, folder_id, drive_id)
	except Exception:
		info["action"] = "Could not list the backup folder. Nothing pruned."
		info["error"] = _redact(frappe.get_traceback())
		frappe.log_error(info["error"], "Offsite Backup: prune listing failed")
		return 0, info

	info["listed"] = len(listing)
	if len(listing) < min_keep:
		info["action"] = (
			f"Only {len(listing)} object(s) listed, fewer than the min_keep floor of "
			f"{min_keep}. Nothing pruned — a short listing is not evidence that the "
			"archive is small."
		)
		return 0, info

	datable = []
	undatable = 0
	for item in listing:
		created = _parse_drive_time(item.get("createdTime"))
		if created is None:
			# An unreadable timestamp is not evidence that a file is old.
			undatable += 1
			continue
		datable.append((created, item))
	datable.sort(key=lambda pair: pair[0], reverse=True)
	info["unparseable_created_time"] = undatable

	cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
	info["cutoff_utc"] = cutoff.isoformat()

	deleted = []
	errors = []
	for created, item in datable[min_keep:]:
		if created >= cutoff:
			continue
		try:
			drive.delete_file(service, item.get("id"))
			deleted.append(
				{
					"name": item.get("name"),
					"id": item.get("id"),
					"created_time": item.get("createdTime"),
				}
			)
		except Exception:
			errors.append({"name": item.get("name"), "id": item.get("id")})
			frappe.log_error(
				f"Could not prune Drive object {item.get('id')} ({item.get('name')}).\n\n"
				f"{_redact(frappe.get_traceback())}",
				"Offsite Backup: prune failed",
			)

	info["deleted"] = deleted
	info["delete_errors"] = errors
	info["action"] = f"Deleted {len(deleted)} object(s) older than {retention_days} days."
	return len(deleted), info


def _parse_drive_time(value):
	"""Drive's RFC-3339 ``createdTime`` as an aware UTC datetime, or ``None``.

	``None`` means "leave this object alone". Guessing at an unparseable or naive
	timestamp is how retention deletes the wrong thing.
	"""
	if not value:
		return None
	try:
		parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		return None
	if parsed.tzinfo is None:
		return None
	return parsed.astimezone(timezone.utc)


# ------------------------------------------------------------- concurrency


def _running_run():
	try:
		return frappe.db.get_value(
			"Offsite Backup Log",
			{"status": "Running"},
			["name", "backup_type", "started_at"],
			as_dict=True,
		)
	except Exception:
		return None


def reconcile_stale_runs():
	"""Fail any ``Running`` row that can no longer be running.

	A run only ever leaves ``Running`` from inside its own process. A SIGKILL, an
	OOM during a multi-GB dump, or a plain ``bench restart`` mid-upload strands the
	row forever — and because the concurrency guard reads exactly that column, the
	stranded row then blocks every future run, silently, for as long as nobody
	looks. Anything past the job timeout plus a grace margin is by definition not
	running any more.

	Called at the top of every entry point, including the watchdog, so a wedged row
	cannot outlive the next tick.
	"""
	horizon = JOB_TIMEOUT + STALE_RUN_GRACE_SECONDS
	try:
		cutoff = add_to_date(now_datetime(), seconds=-horizon)
		stale = frappe.get_all(
			"Offsite Backup Log",
			filters={"status": "Running", "started_at": ["<", cutoff]},
			pluck="name",
		)
	except Exception:
		# Doctype not installed yet (fresh site mid-migrate). Nothing to reconcile.
		return []

	for name in stale:
		try:
			row = frappe.get_doc("Offsite Backup Log", name)
			row.status = "Failed"
			row.ended_at = now_datetime()
			row.duration_seconds = int(max(0, time_diff_in_seconds(row.ended_at, row.started_at)))
			row.error = (
				"Marked Failed by reconcile_stale_runs(): this run was still marked Running "
				f"more than {horizon // 3600} hours after it started, which is past the "
				f"{JOB_TIMEOUT // 3600} hour job timeout. The worker was almost certainly killed "
				"(bench restart, OOM, or host reboot) before it could record an outcome. "
				"Do not assume a usable backup came out of this run."
			)
			row.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Offsite Backup: stale-run reconcile failed")

	if stale:
		frappe.db.commit()
	return stale


def _log_skipped(backup_type, reason):
	try:
		stamp = now_datetime()
		frappe.get_doc(
			{
				"doctype": "Offsite Backup Log",
				"backup_type": backup_type,
				"status": "Skipped",
				"started_at": stamp,
				"ended_at": stamp,
				"duration_seconds": 0,
				"details": json.dumps({"reason": reason}, indent=2),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Offsite Backup: skip row not written")


# ------------------------------------------------------------------ watchdog


def watchdog():
	"""Alert when either backup tier has gone stale.

	The two tiers are checked **separately and against their own thresholds**,
	because they fail independently: a healthy nightly database backup would
	otherwise mask a weekly file backup that has been skipped every Sunday for
	months, and the aggregate "last successful backup" would look perfect the
	whole time.

	This is also the only check that catches the failure mode where *nothing runs
	at all* — a failure email only ever fires when a job actually ran and threw.
	A disabled scheduler, a dropped hook or a worker that never starts produce no
	failures to report, just silence.
	"""
	settings = _settings()
	if not settings or not cint(settings.enabled):
		return

	reconcile_stale_runs()

	tiers = (
		("database", None, cint(settings.alert_if_older_than_hours) or 36),
		("full (database + files)", FULL_TYPES, cint(settings.alert_if_full_older_than_hours) or 192),
	)

	problems = []
	for label, types, limit_hours in tiers:
		last = _last_success(types)
		if not last:
			problems.append(f"No {label} backup has ever completed successfully.")
			continue
		age = time_diff_in_hours(now_datetime(), last.ended_at)
		if age > limit_hours:
			problems.append(
				f"The last successful {label} backup ({last.name}) finished {age:.1f} hours ago, "
				f"past its {limit_hours} hour threshold."
			)

	if not problems:
		return

	body = (
		f"Offsite backups on {frappe.local.site} are stale.\n\n"
		+ "\n".join(f"  - {problem}" for problem in problems)
		+ "\n\nNothing may be failing — a backup that never runs produces no failure "
		"email. Check that the scheduler is enabled, that the long queue worker is "
		"alive, and that Offsite Backup Settings is still enabled."
	)
	frappe.log_error(body, "Offsite Backup: backups are stale")
	_send(
		_recipients(settings),
		f"[{frappe.local.site}] Offsite backups are stale",
		body,
	)


def _last_success(backup_types=None):
	filters = {"status": "Success", "ended_at": ["is", "set"]}
	if backup_types:
		filters["backup_type"] = ["in", list(backup_types)]
	try:
		rows = frappe.get_all(
			"Offsite Backup Log",
			filters=filters,
			fields=["name", "backup_type", "ended_at"],
			order_by="ended_at desc",
			limit=1,
		)
	except Exception:
		return None
	return rows[0] if rows else None


# -------------------------------------------------------------------- alerts


def _notify(backup_type, log, status, summary, details, error_text, settings):
	try:
		recipients = _recipients(settings)
		if not recipients:
			return
		if status == "Failed":
			_send(
				recipients,
				f"[{frappe.local.site}] Offsite backup FAILED ({backup_type})",
				(
					f"The {backup_type} offsite backup failed.\n\n"
					f"Log: {log.name}\n\n"
					f"{_redact(error_text) or 'No traceback was captured.'}"
				),
			)
		elif status == "Success" and settings and cint(settings.notify_on_success):
			artefacts = ", ".join(
				f"{item['name']} ({item['verified']})" for item in (details.get("artifacts") or [])
			)
			_send(
				recipients,
				f"[{frappe.local.site}] Offsite backup OK ({backup_type})",
				(
					f"The {backup_type} offsite backup succeeded.\n\n"
					f"Log: {log.name}\n"
					f"Files uploaded: {summary.get('files_uploaded')}\n"
					f"Bytes uploaded: {summary.get('bytes_uploaded')}\n"
					f"Pruned: {summary.get('pruned_count')}\n"
					f"Verified: {artefacts or 'n/a'}\n"
				),
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Offsite Backup: alert not sent")


def _send(recipients, subject, message):
	"""Send an alert. A dead outgoing email account must not swallow the event it
	was trying to report, so the failure to send is logged and then dropped."""
	if not recipients:
		return
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=email_style.wrap(
				(email_style.callout("This backup did not complete.", "danger") if "fail" in subject.lower() else "")
				+ email_style.code(message),
				title=subject,
				eyebrow="Offsite backup",
			),
		)
	except Exception:
		frappe.log_error(
			f"Could not send the offsite backup alert {subject!r}.\n\n{_redact(frappe.get_traceback())}",
			"Offsite Backup: alert not sent",
		)

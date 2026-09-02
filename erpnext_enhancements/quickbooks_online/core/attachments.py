# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Mirror QuickBooks Online attachments into ERPNext as native File attachments.

QBO stores files as ``Attachable`` entities, each linked to one or more transactions
through ``AttachableRef``. This module queries those Attachables, resolves each to the
ERPNext document the QBO transaction was imported as (via the ``QuickBooks Sync
Mapping``), downloads the file from its pre-signed ``TempDownloadUri``, and saves it as a
**private File attached to that document** -- so the receipt/scan lives on the ERPNext
doc and is visible to anything that reads ERPNext (notably Triton, which indexes ERPNext,
not Google Drive).

Idempotent: every mirrored File records its source Attachable id in
``custom_qbo_attachable_id``, keyed together with the target document, so a re-run (or the
daily scheduled pass) downloads only what is not already mirrored to that document. An
Attachable can be linked to several transactions, so it may legitimately be mirrored onto
more than one ERPNext document.

QBO remains the read-only source of the originals -- nothing here deletes from QBO, and a
mirrored file that is later removed in ERPNext can simply be re-downloaded on the next
run. (A Google-Drive backup mirror is a deferred v2 follow-on; see WI-071.)

Run modes:
  * **Backfill** (one-time): drive ``sync_attachments(start_position=N, max_scan=W)`` as a
    resumable chunk loop -- each chunk a fresh, OS-``timeout``-wrapped ``bench execute`` over
    a bounded window, advancing the cursor by ``W`` every iteration. No single call is
    long-lived, committed files persist per chunk, and a killed chunk's window is simply
    re-covered on a follow-up pass (idempotent). ``sync_attachments()`` with no window still
    pages the whole list in one process, but a single long process is exactly what a
    pathological attachment once wedged, so prefer the chunk loop for the real backfill.
  * **Ongoing** (steady state): ``tasks.sync_attachments_scheduled`` calls this with a
    ``max_new`` cap so a single scheduled run never runs away.

Self-protection (steady state): the daily pass runs in an RQ worker, where the per-file
SIGALRM guard below is a deliberate no-op (RQ owns SIGALRM), so a malformed PDF would hang
``File.insert()`` until RQ's death penalty killed the whole job -- and, with nothing
recording that this Attachable is poison, re-stall it every following day. So every
download+insert is bracketed by a **write-ahead attempt marker**: a ``QuickBooks Sync
Mapping`` row (``qbo_entity_type="Attachable"``, ``qbo_id=<Id>``) whose ``owned_fields``
JSON records ``{"state": "attempting", "attempts": n, "started_at": ...}``, committed
BEFORE the attempt and deleted on success. A marker still saying ``attempting`` on a later
run belonged to a run that never completed this file: it is settled as ``hung`` (one Error
Log, ``match_status`` = Pending Review) and skipped from then on -- unless the attempt ran
under the SIGALRM guard (the marker records ``guarded``), which settles real hangs itself,
so a guarded marker left behind means the process was killed from outside and the file is
retried as one ordinary failure. Ordinary exceptions count ``attempts`` and the file is
skipped after ``MAX_ATTEMPTS``; ``FAILURE_STREAK_LIMIT`` failures back to back abort the
run as an environment fault and rewind those markers instead of charging them. Rows, not
redis, so the record survives the deploy FLUSHDB. ``reset_attachable`` forgets a marker so
the file is retried.

Fresh download URLs: ``TempDownloadUri`` is a pre-signed ticket that expires within
minutes, so a page of up to 1000 Attachables queried up front had its tail rejected (HTTP
401) by the time the downloads reached it -- ~139 of them in the backfill. URIs are
therefore re-queried in batches of ``DOWNLOAD_URI_BATCH`` immediately before each batch's
downloads, and a rejected ticket is re-queried exactly once before counting as a failure.
"""

from __future__ import annotations

import signal
import threading
from contextlib import contextmanager

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from erpnext_enhancements.quickbooks_online.core.client import (
	QuickBooksAPIError,
	QuickBooksClient,
	QuickBooksDisconnectedError,
	QuickBooksDownloadTicketError,
)
from erpnext_enhancements.quickbooks_online.core.utils import get_settings, json_dumps, json_loads

# QBO caps a query page at 1000 rows; page through with STARTPOSITION.
QBO_ATTACHABLE_PAGE = 1000

# File.file_name is a Data(140) column; QBO filenames can exceed it (see
# _bounded_file_name).
MAX_FILE_NAME_LENGTH = 140

# Hard wall-clock cap on one attachment's download + File.insert(). Frappe runs a
# synchronous JavaScript-in-PDF security scan on every PDF attachment whose bytes are
# passed in memory (core file.py -> pdf_contains_js -> pypdf PdfReader); a malformed
# scan sends pypdf's object-stream parser into a pathological loop that never returns,
# and one such file among QBO's thousands wedged the whole backfill for six hours with
# no error at all. The parse is CPU-bound, so notification muting and request timeouts
# do nothing for it -- only a hard timeout does. The cap turns "hang forever" into
# "skip this one file, log it, carry on". Chosen generously so a legitimately large
# scan (download + parse) is never falsely skipped -- the pathological case loops
# unboundedly, so any sane ceiling separates the two; the cost of a high ceiling is
# only that a genuinely bad file wastes this many seconds before it is skipped.
SAVE_TIMEOUT_SECONDS = 90

# Fresh pre-signed TempDownloadUris are re-queried this many Attachables at a time,
# immediately before those downloads (see _fresh_download_uris). A ticket lives minutes:
# the backfill's 200-URL chunks were rejected on their tail, its 50-URL chunks were not.
DOWNLOAD_URI_BATCH = 50

# Ordinary (exception-raising) failures tolerated per Attachable before the daily pass
# stops retrying it and leaves it for a human (see _check_marker / reset_attachable).
MAX_ATTEMPTS = 3

# Consecutive per-file failures after which a run stops and rewinds the markers of the
# files in the streak: ten files failing back to back is the environment (Intuit's file
# host, our storage, the database), not ten bad files, and charging each an attempt would
# park them all for good after three such runs (see _abort_on_failure_streak).
FAILURE_STREAK_LIMIT = 10

# An "attempting" marker younger than this may belong to a run still in flight (the daily
# job and a manual pass overlapping), so it is skipped without judgement. Older than this
# no live run can own it -- RQ's job timeout (300s on the default queue the daily job runs
# on, 1500s on long) and the backfill's OS-level timeout are all far shorter -- so the run
# that wrote it died on this file: settled as hung when the attempt ran unguarded (the RQ
# worker), or as one ordinary failure when it ran under the SIGALRM guard, which would
# have settled a real hang itself (see _check_marker).
STALE_ATTEMPT_SECONDS = 2 * 60 * 60

# The attempt marker rides on QuickBooks Sync Mapping rows of this entity type (row name
# QBO-MAP-Attachable-<Id> via the doctype's autoname). No AttachableRef ever points at an
# Attachable, so _mapped_document never sees them, and every other reader of the ledger
# filters on its own entity types or on erpnext_doctype/erpnext_name, which these leave
# empty. match_rule tags the rows so their origin is greppable.
MARKER_ENTITY_TYPE = "Attachable"
MARKER_MATCH_RULE = "qbo_attachment_mirror"

# _check_marker verdict -> summary counter.
_SKIP_COUNTER = {"in_flight": "skipped_in_flight", "hung": "skipped_hung", "failed": "skipped_failed"}


class _AttachmentTimeout(Exception):
	"""Raised when one attachment's File.insert() blows ``SAVE_TIMEOUT_SECONDS``."""


def _in_background_job():
	"""True inside an RQ worker job, where we must NOT arm our own SIGALRM timer.

	RQ implements its job timeout with ``signal.setitimer(ITIMER_REAL)`` + SIGALRM;
	arming and then clearing ours would clobber RQ's death penalty for the rest of
	the job. The one-shot backfill runs under ``bench execute`` (no RQ) so it still
	gets the guard; the daily scheduled pass runs in a worker and relies on RQ's own
	job timeout instead."""
	try:
		from rq import get_current_job

		return get_current_job() is not None
	except Exception:
		return False


def _is_rq_job_timeout(exc):
	"""True when ``exc`` is RQ's own job-timeout exception, which must never be swallowed.

	On the scheduled (RQ worker) path our SIGALRM guard is a deliberate no-op and RQ's
	death penalty is the ONLY timeout. RQ raises ``JobTimeoutException`` (a subclass of
	``Exception``, so a bare ``except Exception`` would catch it) via a *one-shot* alarm
	that is not re-armed once it fires -- catching it here would spend that alarm and let
	the rest of the run proceed untimed, re-creating the very hang the guard exists to
	prevent. So the per-file handler re-raises it and lets RQ fail the job as designed."""
	try:
		from rq.timeouts import BaseTimeoutException

		if isinstance(exc, BaseTimeoutException):
			return True
	except Exception:
		pass
	return type(exc).__name__ in ("JobTimeoutException", "BaseTimeoutException")


def _guard_armed(seconds=SAVE_TIMEOUT_SECONDS):
	"""True when :func:`_save_time_limit` will actually arm SIGALRM in this process.

	Recorded on every attempt marker (``guarded``) so a later run can tell what a marker
	left behind means: a guarded attempt cannot leave a hang the guard missed (the guard
	settles hangs itself), so its marker means the process was killed from outside while
	a healthy file was in flight; an unguarded one (the RQ worker) means the file hung."""
	return bool(
		seconds
		and hasattr(signal, "SIGALRM")
		and threading.current_thread() is threading.main_thread()
		and not _in_background_job()
	)


@contextmanager
def _save_time_limit(seconds):
	"""Bound the wrapped block to ``seconds`` of wall-clock via SIGALRM.

	A no-op unless we are on the main thread of a non-RQ process on a platform that
	has SIGALRM (i.e. the ``bench execute`` backfill on Linux): signals can only be
	installed from the main thread, and must not be touched inside an RQ worker (see
	:func:`_in_background_job`). In practice the interrupt lands during the network
	download or pypdf's pure-Python parse -- where the DB connection is idle, so the
	caller's ``frappe.db.rollback()`` recovers cleanly -- rather than the sub-millisecond
	``INSERT`` itself; the budget is far larger than any real insert, so an interrupt
	mid-query is not a case worth engineering around."""
	if not _guard_armed(seconds):
		yield
		return

	def _handler(_signum, _frame):
		raise _AttachmentTimeout(f"File insert exceeded {seconds}s")

	old_handler = signal.signal(signal.SIGALRM, _handler)
	signal.setitimer(signal.ITIMER_REAL, seconds)
	try:
		yield
	finally:
		signal.setitimer(signal.ITIMER_REAL, 0)
		signal.signal(signal.SIGALRM, old_handler)


def sync_attachments(entity_types=None, max_new=None, settings=None, start_position=1, max_scan=None):
	"""Mirror QBO Attachables onto their mapped ERPNext documents (idempotent).

	``entity_types`` -- optional list of QBO entity types to restrict to (e.g.
	``["Bill", "Invoice"]``); ``None`` mirrors every linked type we have a mapping for.
	``max_new`` -- cap on how many NEW files this run downloads, so a bounded scheduled
	pass cannot run away; ``None`` (the backfill) processes everything.
	``start_position`` / ``max_scan`` -- run over a bounded window of the Attachable
	list starting at ``start_position`` (QBO's 1-based STARTPOSITION) and scanning at
	most ``max_scan`` Attachables. This lets the backfill run as a **resumable chunk
	loop** (each chunk a fresh, OS-timeout-wrapped ``bench execute``) so no single call
	is long-lived and a killed chunk resumes from where the next one starts.

	Returns a summary dict: ``scanned`` (Attachables seen), ``mirrored`` (files saved),
	``skipped_existing`` (already mirrored to that doc), ``no_mapping`` (linked entity not
	imported into ERPNext), ``no_file`` (a Note / metadata-only Attachable), ``errors``
	(per-file exceptions this run), ``skipped_hung`` / ``skipped_failed`` (files the marker
	says to leave alone -- a human should look; see the module docstring),
	``skipped_in_flight`` (a marker young enough to belong to an overlapping run),
	``url_refreshes`` (rejected download tickets re-queried), ``uri_query_fallbacks``
	(batches whose ``WHERE Id IN`` re-query was refused and fetched one by one),
	``aborted`` (``"failure_streak"`` when the run stopped early because
	``FAILURE_STREAK_LIMIT`` files failed back to back -- an environment fault, whose
	files were rewound rather than charged an attempt), plus ``next_start`` (the
	STARTPOSITION a follow-on chunk should resume from) and ``exhausted`` (True once the
	Attachable list ran out -- the backfill is complete).
	"""
	settings = settings or get_settings()
	client = QuickBooksClient(settings)
	summary = {
		"scanned": 0,
		"mirrored": 0,
		"skipped_existing": 0,
		"no_mapping": 0,
		"no_file": 0,
		"errors": 0,
		"skipped_hung": 0,
		"skipped_failed": 0,
		"skipped_in_flight": 0,
		"url_refreshes": 0,
		"uri_query_fallbacks": 0,
		"aborted": None,
		"next_start": start_position,
		"exhausted": False,
	}
	# Mute notifications/emails for the run. Bulk File inserts (and any Error Log we write
	# on a mirror hiccup) otherwise fire per-doc Notifications; in a bench/background
	# context a Notification whose send fails logs an Error Log, whose own insert re-fires
	# notifications and recurses until the DB connection wedges ("Commands out of sync",
	# MySQL 2014 -- observed on the first backfill). This is data mirroring, not a
	# user-facing event. Restored in the finally so a long-lived worker is unaffected.
	prev_import, prev_mute = frappe.flags.in_import, frappe.flags.mute_emails
	frappe.flags.in_import = True
	frappe.flags.mute_emails = True
	try:
		_mirror_all(client, summary, entity_types, max_new, start_position, max_scan)
	finally:
		frappe.flags.in_import = prev_import
		frappe.flags.mute_emails = prev_mute
	return summary


def _mirror_all(client, summary, entity_types, max_new, start_position=1, max_scan=None):
	"""Page through QBO Attachables and mirror each (see :func:`sync_attachments`).

	Split out so ``sync_attachments`` can mute notifications around the whole run in a
	try/finally without re-indenting the loop. Scans from ``start_position`` and, when
	``max_scan`` is set, stops after that many Attachables so the caller can drive a
	bounded, resumable chunk loop. Each page is first reduced to the (Attachable, target
	document) pairs that actually need a download (``_collect_pending``), then those are
	worked in ``DOWNLOAD_URI_BATCH``-sized batches whose download URIs are re-queried
	right before use (``_fresh_download_uris``). Mutates ``summary`` in place
	(``next_start`` / ``exhausted`` track resume state); returns None.
	"""
	start = start_position
	scanned_this_run = 0
	streak = []  # consecutive per-file failures, see FAILURE_STREAK_LIMIT
	while True:
		page = QBO_ATTACHABLE_PAGE
		if max_scan is not None:
			page = min(page, max_scan - scanned_this_run)
			if page <= 0:
				summary["next_start"] = start
				return
		resp = client.query(f"SELECT * FROM Attachable STARTPOSITION {start} MAXRESULTS {page}")
		rows = ((resp or {}).get("QueryResponse") or {}).get("Attachable") or []
		if not rows:
			# Ran off the end of the Attachable list: the backfill is complete.
			summary["exhausted"] = True
			summary["next_start"] = start
			return
		pending = _collect_pending(rows, summary, entity_types)
		for offset in range(0, len(pending), DOWNLOAD_URI_BATCH):
			batch = pending[offset : offset + DOWNLOAD_URI_BATCH]
			# Query this batch's tickets only now, so none is minutes old by the time its
			# download starts. An Attachable QBO no longer returns a URI for falls back to
			# the page's: it then either works or is refreshed once on rejection.
			fresh = _fresh_download_uris(client, [item["att_id"] for item in batch], summary)
			for item in batch:
				outcome = _mirror_one(client, summary, item, fresh.get(item["att_id"]) or item["temp_uri"])
				if outcome.get("outcome") == "failed":
					streak.append(outcome)
					if len(streak) >= FAILURE_STREAK_LIMIT:
						# Ten in a row is the environment, not the files: stop, and give
						# the streak its attempts back. A re-run picks this page up again
						# from `start`; everything already mirrored is committed.
						_abort_on_failure_streak(streak, summary)
						summary["next_start"] = start
						return
				else:
					streak = []
				if max_new and summary["mirrored"] >= max_new:
					summary["next_start"] = start
					return
		scanned_this_run += len(rows)
		start += len(rows)
		summary["next_start"] = start
		# A short page is the last page: the Attachable list is exhausted.
		if len(rows) < page:
			summary["exhausted"] = True
			return
		if max_scan is not None and scanned_this_run >= max_scan:
			return


def _collect_pending(rows, summary, entity_types):
	"""Reduce one page of Attachables to the (Attachable, target document) pairs that
	need a download, counting everything else in ``summary``.

	DB lookups only, no network, so the page's tickets are not aging while this runs.
	An Attachable's marker is consulted once per page, and only when at least one of its
	links needs a download, so a settled (hung / failed) file never has a fresh ticket
	fetched for it. Returns a list of dicts consumed by :func:`_mirror_one`.
	"""
	pending = []
	for att in rows:
		summary["scanned"] += 1
		file_name = att.get("FileName")
		temp_uri = att.get("TempDownloadUri")
		att_id = str(att.get("Id") or "")
		# A Note (text-only) or a metadata Attachable with no downloadable file.
		if not file_name or not temp_uri or not att_id:
			summary["no_file"] += 1
			continue
		verdict = None
		for ref in att.get("AttachableRef") or []:
			entity = ref.get("EntityRef") or {}
			qbo_type = entity.get("type")
			qbo_id = str(entity.get("value") or "")
			if not qbo_type or not qbo_id:
				continue
			if entity_types and qbo_type not in entity_types:
				continue
			target = _mapped_document(qbo_type, qbo_id)
			if not target:
				summary["no_mapping"] += 1
				continue
			doctype, name = target
			if _already_mirrored(att_id, doctype, name):
				summary["skipped_existing"] += 1
				continue
			if verdict is None:
				verdict = _check_marker(att_id)[0]
			if verdict != "go":
				summary[_SKIP_COUNTER[verdict]] += 1
				continue
			pending.append(
				{
					"att_id": att_id,
					"file_name": file_name,
					"temp_uri": temp_uri,
					"doctype": doctype,
					"name": name,
				}
			)
	return pending


def _mirror_one(client, summary, item, uri):
	"""Download + insert one (Attachable, target document) pair under a write-ahead marker.

	The order is the whole point: (1) re-read the marker -- a sibling link of the same
	Attachable earlier in this batch, or an overlapping run, may have settled it since
	``_collect_pending`` looked; (2) write ``attempting`` and COMMIT, so a hang that ends
	in a SIGKILL still leaves its evidence behind; (3) download (one fresh-ticket retry
	on a rejection) and insert; (4) delete the marker and commit -- in the same
	transaction as the File, so success is never recorded without the file, nor the file
	without clearing the marker. Failures settle in :func:`_record_failure`; RQ's own
	timeout is released or settled by :func:`_release_on_job_timeout` and always re-raised.

	Returns ``{"outcome": "mirrored" | "skipped" | "failed"}``; a failure also carries the
	marker's row name and its state before this attempt, so :func:`_mirror_all` can rewind
	a streak of them (:func:`_abort_on_failure_streak`).
	"""
	att_id, doctype, name = item["att_id"], item["doctype"], item["name"]
	verdict, marker, previous = _check_marker(att_id)
	if verdict != "go":
		summary[_SKIP_COUNTER[verdict]] += 1
		return {"outcome": "skipped"}
	state = {
		"state": "attempting",
		"attempts": cint(previous.get("attempts")) + 1,
		"started_at": str(now_datetime()),
		"target": f"{doctype}/{name}",
		"file_name": item["file_name"],
		# Whether the SIGALRM guard is armed for this attempt. A marker left behind by a
		# guarded attempt cannot be a hang the guard missed, so a later run reads it as
		# "killed from outside" and retries instead of settling it hung (_check_marker).
		"guarded": _guard_armed(SAVE_TIMEOUT_SECONDS),
	}
	try:
		marker = _write_marker(marker, att_id, state)
		# One hard wall-clock cap over the whole per-file op: the download (a trickling
		# stream outlasts requests' per-read timeout) AND the insert (a malformed PDF
		# makes Frappe's synchronous JS-in-PDF pypdf scan loop forever). SIGALRM reliably
		# interrupts a blocked socket read, which closing the socket from a timer thread
		# does not guarantee on Linux. A no-op on the RQ worker path, where RQ's own
		# death penalty is the cap -- and the marker just written is what makes that
		# path self-protecting across runs.
		with _save_time_limit(SAVE_TIMEOUT_SECONDS):
			content = _download(client, att_id, uri, summary)
			_save_attachment(content, item["file_name"], att_id, doctype, name)
		_clear_marker(marker)
		# Commit each mirror on its own so a long backfill makes durable, resumable
		# progress and never holds one giant transaction over thousands of File inserts
		# (which would also block progress-monitoring and roll everything back on a
		# single late failure).
		frappe.db.commit()
		summary["mirrored"] += 1
		return {"outcome": "mirrored"}
	except Exception as exc:
		# RQ's own job-timeout must propagate, never be counted as a per-file error: on
		# the scheduled (worker) path it is the only timeout, and swallowing it spends
		# its one-shot alarm and lets the rest of the run go untimed (see
		# _is_rq_job_timeout).
		if _is_rq_job_timeout(exc):
			_release_on_job_timeout(marker, att_id, state)
			raise
		if isinstance(exc, QuickBooksDisconnectedError):
			# The grant died mid-run (revoked at Intuit, or a token rotation lost to a
			# concurrent worker). Not this file's fault: forget the attempt and let the
			# run abort with the client's reconnect message, rather than charge every
			# remaining file an attempt for it.
			_release_marker(att_id)
			raise
		if marker is None and _is_duplicate_row(exc):
			# An overlapping run inserted this Attachable's marker between our
			# _check_marker read and our insert (a REPEATABLE READ snapshot can predate
			# its commit): that run owns the file. Skip without judgement, exactly as
			# _check_marker would have -- no log, no attempt charged to the file.
			frappe.db.rollback()
			summary["skipped_in_flight"] += 1
			return {"outcome": "skipped"}
		frappe.db.rollback()
		summary["errors"] += 1
		frappe.log_error(
			frappe.get_traceback(),
			f"QBO attachment mirror {att_id} -> {doctype} {name}",
		)
		_record_failure(marker, att_id, state, exc, hung=isinstance(exc, _AttachmentTimeout))
		return {"outcome": "failed", "att_id": att_id, "name": marker, "previous": previous}


def _download(client, att_id, uri, summary):
	"""Download ``uri``; on a rejected ticket, re-query a fresh one and retry exactly once.

	A ticket is rejected (401/403) when the pre-signed URI has expired -- expected
	whenever a download runs minutes after its query, and nothing to do with the OAuth
	grant -- so one fresh URI settles it. A second rejection is a real failure and
	propagates to the per-file handler."""
	try:
		return client.download_attachable(uri)
	except QuickBooksDownloadTicketError:
		fresh = _fresh_download_uris(client, [att_id], summary).get(att_id)
		if not fresh:
			raise
		summary["url_refreshes"] += 1
		return client.download_attachable(fresh)


def _fresh_download_uris(client, att_ids, summary=None):
	"""Re-query ``TempDownloadUri`` for ``att_ids`` (at most ``DOWNLOAD_URI_BATCH`` at a
	time) and return ``{Id: uri}`` -- called immediately before those downloads so no
	ticket is minutes old when used.

	``Id`` accepts only ``=`` and ``IN`` in QBO's query grammar and projections are not
	supported, hence ``SELECT * ... WHERE Id IN (...)``. Should QBO ever reject the ``IN``
	form, fall back to one ``get_entity`` per id -- counted in ``summary`` and reported
	once per run, because a permanent refusal would otherwise degrade every batch to 51
	calls in silence. An Attachable missing from the result (deleted since the page was
	read) is simply absent from the dict; the caller falls back to the page's URI for it.
	A dead grant (``QuickBooksDisconnectedError``) is a run-level condition and propagates:
	the per-id fallback could only re-raise it as a less helpful error."""
	ids = []
	for att_id in att_ids:
		att_id = str(att_id or "").replace("'", "")
		if att_id and att_id not in ids:
			ids.append(att_id)
	if not ids:
		return {}
	quoted = ", ".join(f"'{att_id}'" for att_id in ids)
	try:
		resp = client.query(f"SELECT * FROM Attachable WHERE Id IN ({quoted}) MAXRESULTS {len(ids)}")
		rows = ((resp or {}).get("QueryResponse") or {}).get("Attachable") or []
	except QuickBooksDisconnectedError:
		raise
	except QuickBooksAPIError as exc:
		if summary is not None:
			summary["uri_query_fallbacks"] += 1
			if summary["uri_query_fallbacks"] == 1:
				frappe.log_error(
					title="QBO attachment mirror: fresh-ticket IN query refused, fetching one by one",
					message=(
						"`SELECT * FROM Attachable WHERE Id IN (...)` was rejected, so this run "
						f"fetches download tickets with one GET per Attachable ({len(ids)} in this "
						"batch). Reported once per run; see `uri_query_fallbacks` in the run summary.\n"
						f"{str(exc)[:1000]}"
					),
				)
		rows = _fetch_attachables_singly(client, ids)
	return {
		str(row.get("Id")): row.get("TempDownloadUri")
		for row in rows
		if row.get("Id") and row.get("TempDownloadUri")
	}


def _fetch_attachables_singly(client, ids):
	"""Per-id fallback for :func:`_fresh_download_uris`: ``GET /attachable/{id}`` each.

	An Attachable that no longer exists (QBO fault 610 "Object Not Found") is skipped;
	anything else is a run-level API failure and propagates, as the page query's would."""
	rows = []
	for att_id in ids:
		try:
			row = ((client.get_entity("Attachable", att_id) or {}).get("Attachable")) or {}
		except QuickBooksAPIError as exc:
			if "Object Not Found" in str(exc) or " 404 " in str(exc):
				continue
			raise
		if row:
			rows.append(row)
	return rows


def _mapped_document(qbo_entity_type, qbo_id):
	"""Resolve a QBO ``(type, id)`` to the ERPNext ``(doctype, name)`` it was imported as.

	Returns ``None`` when there is no mapping, the mapping has no ERPNext record, or the
	record no longer exists (e.g. a mapping soft-deleted after the linked doc was removed).
	"""
	row = frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": qbo_entity_type, "qbo_id": qbo_id},
		["erpnext_doctype", "erpnext_name"],
		as_dict=True,
	)
	if (
		row
		and row.erpnext_doctype
		and row.erpnext_name
		and frappe.db.exists(row.erpnext_doctype, row.erpnext_name)
	):
		return row.erpnext_doctype, row.erpnext_name
	return None


def _already_mirrored(att_id, doctype, name):
	"""True when this Attachable is already mirrored onto this document."""
	return bool(
		frappe.db.exists(
			"File",
			{
				"custom_qbo_attachable_id": att_id,
				"attached_to_doctype": doctype,
				"attached_to_name": name,
			},
		)
	)


def _bounded_file_name(file_name):
	"""Fit a QBO filename into ``File.file_name``'s 140-char ``Data`` limit.

	QBO hands back filenames that are a long base64 token plus an extension, often well
	over 140 chars; Frappe raises ``CharacterLengthExceededError`` on insert if the name
	exceeds the field length (observed as ~19 skipped attachments in the first full
	backfill). Keep the extension and the distinctive tail, marking the cut with a
	leading "..."; idempotency and the QBO link ride on ``custom_qbo_attachable_id``, not
	the name, so trimming it is cosmetic. Same approach as the Drive shadow sync's
	``MAX_FILE_NAME_LENGTH`` handling."""
	if not file_name or len(file_name) <= MAX_FILE_NAME_LENGTH:
		return file_name
	root, dot, ext = file_name.rpartition(".")
	suffix = f".{ext}" if dot and 1 <= len(ext) <= 10 else ""
	head = "..."
	base = root if suffix else file_name
	keep = MAX_FILE_NAME_LENGTH - len(suffix) - len(head)
	return f"{head}{base[-keep:]}{suffix}"


def _save_attachment(content, file_name, att_id, doctype, name):
	"""Save downloaded bytes as a private File attached to the ERPNext document.

	Mirrors the ``accounting_intake.channels`` pattern (create a File with ``content``);
	Frappe writes the bytes to storage on insert. ``custom_qbo_attachable_id`` stamps the
	source so re-runs skip it; the filename is bounded to the field limit first.
	"""
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": _bounded_file_name(file_name),
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"is_private": 1,
			"content": content,
			"custom_qbo_attachable_id": att_id,
		}
	).insert(ignore_permissions=True)


# --------------------------------------------------------------------- attempt markers
#
# The write-ahead record that makes the scheduled pass self-protecting (module docstring).
# One QuickBooks Sync Mapping row per Attachable that is, or was, being attempted; the
# state machine lives in its owned_fields JSON:
#
#   (none) --write-ahead--> attempting --success--> (row deleted)
#                                     --exception--> failed (attempts n)  --n >= MAX--> skipped
#                                     --_AttachmentTimeout / old RQ timeout--> hung   --> skipped
#                                     --left behind > STALE_ATTEMPT_SECONDS, unguarded--> hung
#                                     --left behind > STALE_ATTEMPT_SECONDS, guarded--> failed
#                                       (the process was killed from outside; retryable)
#                                     --left behind, younger--> in_flight (skipped, no judgement)
#                                     --RQ timeout < 90s in / dead grant / overlap loser--> (row deleted)
#
# `hung` and an exhausted `failed` flip match_status to Pending Review so the list view
# shows them; reset_attachable (or deleting the row) puts the file back in play. A streak
# of FAILURE_STREAK_LIMIT failures aborts the run and rewinds those rows to their
# pre-run state (_abort_on_failure_streak).


def _load_marker(att_id):
	"""Return ``(row name, state dict)`` for the Attachable's marker, or ``(None, {})``.

	Reads defensively -- a hand-edited or truncated ``owned_fields`` reads as an empty
	state, which :func:`_check_marker` treats as retryable."""
	row = frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": MARKER_ENTITY_TYPE, "qbo_id": str(att_id)},
		["name", "owned_fields"],
		as_dict=True,
	)
	if not row:
		return None, {}
	state = json_loads(getattr(row, "owned_fields", None) or "", default={})
	return row.name, state if isinstance(state, dict) else {}


def _write_marker(name, att_id, state, match_status="Not Matched"):
	"""Upsert the marker row and COMMIT it -- the write-ahead half of the protocol.

	Committed on its own so the evidence survives whatever the attempt does next, up to
	and including a SIGKILL of the worker. ``match_status`` is the human-facing signal:
	``Pending Review`` (a real Select option, filterable in the list view) once the file
	has been given up on. ``last_synced_at`` mirrors ``started_at`` so staleness is also
	visible in SQL. Returns the row name."""
	values = {
		"owned_fields": json_dumps(state),
		"last_synced_at": state.get("started_at"),
		"match_rule": MARKER_MATCH_RULE,
		"match_status": match_status,
	}
	if name:
		frappe.db.set_value("QuickBooks Sync Mapping", name, values)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "QuickBooks Sync Mapping",
				"qbo_entity_type": MARKER_ENTITY_TYPE,
				"qbo_id": str(att_id),
				**values,
			}
		)
		doc.insert(ignore_permissions=True)
		name = doc.name
	frappe.db.commit()
	return name


def _clear_marker(name):
	"""Delete the marker row. NOT committed here: the success path commits it together
	with the File it belongs to, so the two are atomic."""
	if name:
		frappe.db.delete("QuickBooks Sync Mapping", {"name": name})


def _attempt_age_seconds(state):
	"""Seconds since the marker's ``started_at``; ``None`` when unreadable (= stale)."""
	started_at = state.get("started_at")
	if not started_at:
		return None
	try:
		return (now_datetime() - get_datetime(started_at)).total_seconds()
	except Exception:
		return None


def _check_marker(att_id):
	"""Classify an Attachable from its marker: ``(verdict, row name, state)``.

	``go`` -- no marker, or a still-retryable failure; ``in_flight`` -- an ``attempting``
	marker young enough to belong to a run still working on it (skip, no judgement);
	``hung`` -- a settled hang, or an ``attempting`` marker too old for any live run to
	own, which is settled here (one Error Log, Pending Review) so later runs only count
	it; ``failed`` -- ``MAX_ATTEMPTS`` ordinary failures."""
	name, state = _load_marker(att_id)
	if not name:
		return "go", None, {}
	status = state.get("state")
	if status == "attempting":
		age = _attempt_age_seconds(state)
		if age is not None and age < STALE_ATTEMPT_SECONDS:
			return "in_flight", name, state
		if state.get("guarded"):
			# Written under the SIGALRM guard, which settles a real hang itself
			# (_AttachmentTimeout -> _record_failure(hung=True)). A guarded marker the
			# guard never got to settle means the process died from outside -- the
			# backfill's OS `timeout`, a deploy restart -- while a healthy file was in
			# flight. One ordinary failure, not a hang: retry it, up to MAX_ATTEMPTS.
			return _settle_killed(name, att_id, state)
		_settle_hung(
			name,
			att_id,
			state,
			"Attempt marker left behind by a run that never completed this file "
			f"(started {state.get('started_at')}): the download or File.insert() hung.",
		)
		return "hung", name, state
	if status == "hung":
		return "hung", name, state
	if status == "failed" and cint(state.get("attempts")) >= MAX_ATTEMPTS:
		return "failed", name, state
	return "go", name, state


def _settle_hung(name, att_id, state, reason):
	"""Durably mark the Attachable hung and report it ONCE -- the state change is the
	once-guard: later runs read ``hung`` and only count it.

	The Error Log is written BEFORE the marker so both ride on the marker's commit. On
	the RQ-timeout path the caller re-raises into frappe's job wrapper, which rolls back
	everything still uncommitted -- an alert written after the commit would be lost,
	leaving a Pending Review row nobody was told about."""
	state["state"] = "hung"
	state["last_error"] = reason
	try:
		name = name or _load_marker(att_id)[0]
		frappe.log_error(
			title=f"QBO attachment {att_id} skipped: hung",
			message=(
				f"{reason}\nTarget: {state.get('target')}\nFile: {state.get('file_name')}\n"
				f"Attempts: {state.get('attempts')}\n"
				"Every run will skip it until the marker is cleared: "
				f"attachments.reset_attachable('{att_id}')."
			),
		)
		_write_marker(name, att_id, state, match_status="Pending Review")
	except Exception as exc:
		if _is_rq_job_timeout(exc):
			raise
		_swallow_rollback()


def _settle_killed(name, att_id, state):
	"""A guarded attempt's marker was left behind: the process died from outside while
	this (healthy, as far as anyone knows) file was in flight. Turn it into one ordinary
	failure -- the attempt was already counted when the marker was written -- and return
	the verdict :func:`_check_marker` gives such a failure."""
	state["state"] = "failed"
	state["last_error"] = (
		"Run killed from outside while this file was in flight "
		f"(started {state.get('started_at')}; the SIGALRM guard was armed, and it settles a "
		"real hang itself)."
	)
	give_up = cint(state.get("attempts")) >= MAX_ATTEMPTS
	try:
		_write_marker(name, att_id, state, match_status="Pending Review" if give_up else "Not Matched")
	except Exception as exc:
		if _is_rq_job_timeout(exc):
			raise
		_swallow_rollback()
	return ("failed" if give_up else "go"), name, state


def _record_failure(name, att_id, state, exc, hung=False):
	"""Settle the marker after a per-file exception (already logged by the caller).

	The SIGALRM guard's ``_AttachmentTimeout`` IS the hang signal, so it settles the file
	as hung at once rather than spending two more 90s attempts on it; any other exception
	counts one attempt, and the file is given up on at ``MAX_ATTEMPTS``."""
	state["state"] = "hung" if hung else "failed"
	state["last_error"] = str(exc)[:500]
	give_up = hung or cint(state.get("attempts")) >= MAX_ATTEMPTS
	try:
		_write_marker(name, att_id, state, match_status="Pending Review" if give_up else "Not Matched")
	except Exception as exc:
		if _is_rq_job_timeout(exc):
			raise
		_swallow_rollback()


def _release_on_job_timeout(name, att_id, state):
	"""RQ's death penalty landed while this file was in flight -- decide what that means.

	RQ times the whole job (300s on the default queue the daily job runs on), so its
	alarm lands on whichever file is in flight when the run's budget expires, healthy or
	not. The file's own elapsed time tells the two apart: under ``SAVE_TIMEOUT_SECONDS``
	it is a healthy file caught by the run's clock, so the marker is released for the
	next run; at or over it, the file itself hung, and it is settled as such. Both by the
	marker's natural key rather than the local row name: the alarm can land after
	``_write_marker``'s commit and before its return, leaving ``name`` None for a row
	that now exists. Best-effort throughout: nothing here may mask RQ's exception, which
	the caller re-raises regardless."""
	age = _attempt_age_seconds(state)
	try:
		if age is not None and age < SAVE_TIMEOUT_SECONDS:
			_release_marker(att_id)
		else:
			frappe.db.rollback()
			_settle_hung(
				name or _load_marker(att_id)[0],
				att_id,
				state,
				f"RQ's job timeout landed on this file after {age:.0f}s in flight."
				if age is not None
				else "RQ's job timeout landed on this file (attempt age unreadable).",
			)
	except Exception as exc:
		if _is_rq_job_timeout(exc):
			raise
		_swallow_rollback()


def _release_marker(att_id):
	"""Forget the attempt marker WITHOUT judgement: the run was cut short, not the file.

	By natural key, so it also finds a row whose name the caller never learned (an alarm
	between the insert's commit and its return). Committed on its own."""
	frappe.db.rollback()
	frappe.db.delete(
		"QuickBooks Sync Mapping", {"qbo_entity_type": MARKER_ENTITY_TYPE, "qbo_id": str(att_id)}
	)
	frappe.db.commit()


def _is_duplicate_row(exc):
	"""True for the primary-key collision an overlapping run's marker insert raises.

	The marker row's only uniqueness is its name (``QBO-MAP-Attachable-<Id>``), so the
	collision is frappe's ``DuplicateEntryError`` -- not ``UniqueValidationError``, which
	a non-PK unique index raises and which is deliberately not matched here."""
	dup = getattr(frappe, "DuplicateEntryError", None)
	return bool(dup) and isinstance(exc, dup)


def _abort_on_failure_streak(streak, summary):
	"""``FAILURE_STREAK_LIMIT`` files failed back to back: stop the run and give them
	their attempts back.

	One bad file is one bad file; ten in a row is Intuit's file host, our storage or the
	database, and charging each an attempt would park every pending file for good after
	three such runs. Each marker is rewound to what it was before this run touched it (a
	fresh row is deleted; a retryable failure gets its previous count back), one Error Log
	names the streak, and the caller returns so a later run -- or a human -- starts this
	page over. The per-file Error Logs already written are kept."""
	for entry in streak:
		att_id, previous = entry.get("att_id"), entry.get("previous") or {}
		try:
			if previous:
				give_up = cint(previous.get("attempts")) >= MAX_ATTEMPTS
				_write_marker(
					entry.get("name") or _load_marker(att_id)[0],
					att_id,
					previous,
					match_status="Pending Review" if give_up else "Not Matched",
				)
			else:
				_release_marker(att_id)
		except Exception as exc:
			if _is_rq_job_timeout(exc):
				raise
			_swallow_rollback()
	summary["aborted"] = "failure_streak"
	try:
		frappe.log_error(
			title=f"QBO attachment mirror aborted: {len(streak)} consecutive failures",
			message=(
				f"{len(streak)} files failed back to back, which is an environment fault rather "
				"than bad files. The run stopped and those files' attempt markers were rewound so "
				"they are not charged for it; the next run starts this page over. Attachables: "
				+ ", ".join(str(entry.get("att_id")) for entry in streak)
				+ "\nThe failures themselves are in the per-file 'QBO attachment mirror' Error Logs."
			),
		)
		frappe.db.commit()
	except Exception as exc:
		if _is_rq_job_timeout(exc):
			raise
		_swallow_rollback()


def _swallow_rollback():
	"""Roll back after a failed marker write without letting a dead connection raise --
	marker bookkeeping must never mask the file error already logged, nor RQ's timeout."""
	try:
		frappe.db.rollback()
	except Exception:
		pass


def reset_attachable(att_id):
	"""Forget the attempt marker for one QBO Attachable so the next pass retries it.

	The manual recovery for a file reported as hung or failed (Error Log title
	``QBO attachment <Id> skipped: hung``, or the QuickBooks Sync Mapping list filtered on
	``qbo_entity_type = Attachable``)::

	    bench execute erpnext_enhancements.quickbooks_online.core.attachments.reset_attachable \\
	        --kwargs "{'att_id': '123'}"

	Deleting the mapping row by hand is equivalent."""
	frappe.db.delete(
		"QuickBooks Sync Mapping", {"qbo_entity_type": MARKER_ENTITY_TYPE, "qbo_id": str(att_id)}
	)
	frappe.db.commit()

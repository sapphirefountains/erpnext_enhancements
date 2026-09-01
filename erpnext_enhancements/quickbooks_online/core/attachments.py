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
"""

from __future__ import annotations

import signal
import threading
from contextlib import contextmanager

import frappe

from erpnext_enhancements.quickbooks_online.core.client import QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.utils import get_settings

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
	if (
		not seconds
		or not hasattr(signal, "SIGALRM")
		or threading.current_thread() is not threading.main_thread()
		or _in_background_job()
	):
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
	imported into ERPNext), ``no_file`` (a Note / metadata-only Attachable), ``errors``,
	plus ``next_start`` (the STARTPOSITION a follow-on chunk should resume from) and
	``exhausted`` (True once the Attachable list ran out -- the backfill is complete).
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
	bounded, resumable chunk loop. Mutates ``summary`` in place (``next_start`` /
	``exhausted`` track resume state); returns None.
	"""
	start = start_position
	scanned_this_run = 0
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
		for att in rows:
			summary["scanned"] += 1
			file_name = att.get("FileName")
			temp_uri = att.get("TempDownloadUri")
			att_id = str(att.get("Id") or "")
			# A Note (text-only) or a metadata Attachable with no downloadable file.
			if not file_name or not temp_uri or not att_id:
				summary["no_file"] += 1
				continue
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
				try:
					# One hard wall-clock cap over the whole per-file op: the download (a
					# trickling stream outlasts requests' per-read timeout) AND the insert (a
					# malformed PDF makes Frappe's synchronous JS-in-PDF pypdf scan loop
					# forever). SIGALRM reliably interrupts a blocked socket read, which
					# closing the socket from a timer thread does not guarantee on Linux. A
					# no-op on the RQ worker path, where RQ's own death penalty is the cap.
					with _save_time_limit(SAVE_TIMEOUT_SECONDS):
						content = client.download_attachable(temp_uri)
						_save_attachment(content, file_name, att_id, doctype, name)
					# Commit each mirror on its own so a long backfill makes durable,
					# resumable progress and never holds one giant transaction over
					# thousands of File inserts (which would also block progress-monitoring
					# and roll everything back on a single late failure).
					frappe.db.commit()
					summary["mirrored"] += 1
				except Exception as exc:
					# RQ's own job-timeout must propagate, never be counted as a per-file
					# error: on the scheduled (worker) path it is the only timeout, and
					# swallowing it spends its one-shot alarm and lets the rest of the run
					# go untimed (see _is_rq_job_timeout).
					if _is_rq_job_timeout(exc):
						raise
					frappe.db.rollback()
					summary["errors"] += 1
					frappe.log_error(
						frappe.get_traceback(),
						f"QBO attachment mirror {att_id} -> {doctype} {name}",
					)
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

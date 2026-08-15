# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Requesting, building and downloading an export. Phase 6 §4.C, the half that touches Frappe.

**Separate from `export.py`, and that separation is load-bearing.** That module decides what
leaves the building and imports nothing from Frappe, so the question *"does a deleted
message's text end up in the bundle"* is answerable by a test that needs no database. Putting
`frappe.enqueue` next to it would have cost exactly that.

--------------------------------------------------------------------------------------
Three things the ADR asks for that are easy to get subtly wrong
--------------------------------------------------------------------------------------

**Never synchronous.** A room with a year of history exceeds any request timeout, and a
partial download is worse than none — it looks complete. So the endpoint records the request
and returns; the bundle arrives on the row.

**The download endpoint writes its OWN audit row.** `export_requested` and
`export_downloaded` are two events because they are two acts, often days apart and sometimes
by different people. One row for both would make "who actually took a copy" unanswerable,
which is the only question that matters after the fact.

**The bundle is reachable only through here.** It is stored as a private `File` attached to
the `Chat Export Request`, and that DocType ships zero DocPerm — Frappe's private-file check
delegates to the attached document's `has_permission`, so `/private/files/…` is closed to
everyone but Administrator. The audited endpoint below is the only door, which is what makes
the audit row a fact about access rather than a note about one path to it.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, now

from erpnext_enhancements import __version__ as APP_VERSION
from erpnext_enhancements.chat import audit, permissions
from erpnext_enhancements.chat.doctype.chat_export_request.chat_export_request import (
	STATUS_COMPLETE,
	STATUS_FAILED,
	STATUS_IN_PROGRESS,
	STATUS_PENDING,
)
from erpnext_enhancements.chat.governance import access_report, drift_rules, export

DOCTYPE = "Chat Export Request"

#: The worker, as a dotted string. `frappe.enqueue` takes a path rather than a function
#: object everywhere in this package: a function object pickles its module, which ties the
#: queueing process to the running one across a deploy.
WORKER_PATH = "erpnext_enhancements.chat.governance.export_runner.run_export_job"
EXPORT_QUEUE = "long"
EXPORT_TIMEOUT = 1800

#: Caps. Not permissions — the permission is the role — but an export naming a hundred rooms
#: is a fishing expedition wearing a reason, and the audit row would record it as one
#: justified act.
MAX_ROOMS = 25
MAX_MESSAGES = 50_000
MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024


class ExportRefused(frappe.ValidationError):
	"""Refused before anything was written."""


# --------------------------------------------------------------------------------------
# Requesting
# --------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def request_export(
	rooms: Any = None,
	reason: str = "",
	reason_category: str = "",
	subject: str | None = None,
	include_deleted_content: int = 0,
) -> dict[str, Any]:
	"""Record the request, write the audit row, and queue the build.

	**The audit row is written before the job is queued, and that order is the point.** If the
	queue is unavailable the request still exists and is still recorded as having been made;
	the reverse — a bundle built from a request nobody recorded — is the failure the whole
	phase is arranged to prevent.
	"""
	user = _require_auditor()
	cleaned_reason, category = _require_reason(reason, reason_category)
	named = _rooms(rooms)
	include_deleted = 1 if cint(include_deleted_content) else 0

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"status": STATUS_PENDING,
			"requested_by": user,
			"requested_at": now(),
			"rooms": ",".join(named),
			"subject_user": (subject or "").strip() or None,
			"reason": cleaned_reason,
			"reason_category": category,
			"include_deleted_content": include_deleted,
		}
	)
	doc.insert(ignore_permissions=True)

	# `export_requested` is one of the three events whose reason the Chat Audit Log controller
	# refuses below twelve characters. It cannot fail on that here — `_require_reason` grades
	# with the stricter `reason_quality`, which rejects everything the controller does and
	# placeholders besides.
	recorded = audit.record_governance_event(
		event_type="export_requested",
		actor=user,
		subject_user=(subject or "").strip(),
		reference_doctype=DOCTYPE,
		reference_name=doc.name,
		reason=cleaned_reason,
		detail=_request_detail(named, include_deleted, category),
		affected_count=len(named),
	)

	if not recorded:
		# `record_governance_event` swallows its own failures and returns None — a lock it
		# could not take, a chain head it could not read. The first version of this discarded
		# the return and queued the build anyway, which would have produced a downloadable
		# bundle with no `export_requested` row: exactly what this module's docstring calls
		# the failure the whole phase is arranged to prevent, and exactly what
		# `download_export` already refuses. Two call sites in one file must not make opposite
		# trades for the same failure.
		frappe.db.set_value(
			DOCTYPE,
			doc.name,
			{"status": STATUS_FAILED, "error": "the export_requested audit row could not be written"},
			update_modified=False,
		)
		raise ExportRefused(frappe._("This export could not be recorded, so it was not started."))

	_enqueue(doc.name)
	return {"export_request": doc.name, "status": doc.status, "rooms": named}


def _request_detail(rooms: list[str], include_deleted: int, category: str) -> str:
	"""What the audit row says about the request. **Never message text.**

	`record_governance_event`'s own docstring forbids content here, and an export's detail is
	the most tempting place to put a sample of what was exported.
	"""
	return json.dumps(
		{
			"rooms": sorted(rooms),
			"include_deleted_content": bool(include_deleted),
			"reason_category": category,
		},
		sort_keys=True,
	)[:1000]


def _enqueue(name: str) -> None:
	"""Queue the build. Swallows, deliberately.

	A queue that will not accept work must not fail the transaction that produced the row —
	the row plus a human re-requesting is the guarantee, not the enqueue. Every other
	`frappe.enqueue` in this package is wrapped the same way and for the same reason.

	`export_request` as the kwarg name, **not** `event`, `job_name`, `queue` or `timeout`:
	`frappe.enqueue`'s own signature eats those, and this package has been bitten twice —
	once by a kwarg named `event` reaching nothing, once by a rename to `job_name` trading
	"unexpected keyword argument" for "missing required positional argument".
	"""
	try:
		frappe.enqueue(
			WORKER_PATH,
			queue=EXPORT_QUEUE,
			timeout=EXPORT_TIMEOUT,
			enqueue_after_commit=True,
			job_id=f"chat-export::{name}",
			deduplicate=True,
			export_request=name,
		)
	except Exception:
		try:
			frappe.log_error(title="chat export: could not queue the build", message=name)
		except Exception:
			pass


# --------------------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------------------


def run_export_job(export_request: str) -> None:
	"""Build the bundle. The worker. Never raises out of itself.

	A job that dies leaves the row in ``In Progress`` forever, which reads as "still working"
	rather than as "broken" — so every exit writes a terminal status. The error text is
	recorded on the row where the person who asked can see it, rather than only in an Error
	Log they have no reason to open.
	"""
	name = str(export_request or "").strip()
	if not name:
		return

	row = frappe.db.get_value(
		DOCTYPE,
		name,
		["name", "status", "requested_by", "rooms", "subject_user", "include_deleted_content"],
		as_dict=True,
	)
	if not row or row.get("status") != STATUS_PENDING:
		# Already claimed, already done, or gone. Not an error: `deduplicate=True` drops a
		# second enqueue while the first is queued *or started*, but a sweeper or a retry can
		# still arrive after a completed run.
		return

	_set_status(name, STATUS_IN_PROGRESS)
	try:
		result = _build(row)
		# Inside the try, and that is the fix rather than a tidy-up. These three statements
		# were outside it, so a lock-wait timeout on the UPDATE or a connection closed by
		# `wait_timeout` during a long build — the shape a 30-minute export actually has —
		# escaped this function and left the row at `In Progress` with the ZIP already on
		# disk and nothing pointing at it. `status != STATUS_PENDING` then blocks every
		# re-run, so the export could never complete and never be retried.
		frappe.db.set_value(DOCTYPE, name, result, update_modified=False)
		_set_status(name, STATUS_COMPLETE)
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		_fail(name, exc)
		return


def _set_status(name: str, status: str) -> None:
	frappe.db.set_value(DOCTYPE, name, "status", status, update_modified=False)
	frappe.db.commit()


def _fail(name: str, exc: Exception) -> None:
	"""Record the failure on the row, in words, and never re-raise.

	``repr(exc)`` rather than ``str(exc)``: a bare message from an arbitrary exception can be
	empty, and "the export failed: " is a worse answer than the class name. It is truncated
	because an exception carrying a query can carry a row with it.
	"""
	try:
		frappe.db.set_value(
			DOCTYPE,
			name,
			{"status": STATUS_FAILED, "error": repr(exc)[:2000], "completed_at": now()},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		pass
	try:
		frappe.log_error(title="chat export: build failed", message=f"{name}: {exc!r}"[:2000])
	except Exception:
		pass


def _build(row: dict[str, Any]) -> dict[str, Any]:
	"""Assemble the bundle and store it. Returns the fields to stamp on the request."""
	rooms = [r.strip() for r in str(row.get("rooms") or "").split(",") if r.strip()]
	include_deleted = bool(cint(row.get("include_deleted_content")))

	messages = _messages(rooms)
	revisions = _revisions(rooms)
	# **Refuse rather than truncate.** The caps used to be `LIMIT`s, which silently returned
	# the first 50,000 rows while `manifest.json` counted them as the whole range and
	# `README.txt` described the bundle as a copy of the records. A partial export that
	# announces itself is a narrower request; a partial export that does not is a false
	# statement in a legal disclosure. This module's own docstring already says a partial
	# download is worse than none because it looks complete — the caps were the one place
	# that did not follow it.
	if len(messages) > MAX_MESSAGES or len(revisions) > MAX_MESSAGES:
		raise ValueError(
			f"This range holds {len(messages)} messages and {len(revisions)} revisions, above "
			f"the {MAX_MESSAGES} per-export limit. Narrow the rooms or the date range and "
			"request again — the export was refused rather than silently shortened."
		)
	attachments, attachments_omitted = _attachments(rooms)

	message_records = [export.message_record(m, include_deleted_content=include_deleted) for m in messages]
	timezone = _timezone()
	files: dict[str, bytes] = {
		"messages.jsonl": export.canonical_jsonl(message_records),
		"revisions.jsonl": export.canonical_jsonl(export.revision_record(r) for r in revisions),
		"members.csv": export.members_csv(_members(rooms)),
		"transcript.html": export.transcript_html(
			message_records, export_id=str(row["name"]), timezone=timezone
		).encode("utf-8"),
	}
	for path, payload in attachments.items():
		files[path] = payload

	files["README.txt"] = export.readme_text(
		export_id=str(row["name"]),
		rooms=rooms,
		include_deleted_content=include_deleted,
		app_version=APP_VERSION,
		timezone=timezone,
		drift_note=_drift_note(rooms),
	).encode("utf-8")

	manifest = export.build_manifest(
		files,
		app_version=APP_VERSION,
		git_commit=_git_commit(),
		export_id=str(row["name"]),
		generated_at=now(),
		rooms=rooms,
		include_deleted_content=include_deleted,
		message_count=len(messages),
		revision_count=len(revisions),
	)
	if attachments_omitted:
		manifest["attachments_omitted"] = sorted(attachments_omitted)
	files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")

	blob = export.build_zip(files)
	file_name = _store(row["name"], blob)
	return {
		"bundle_file": file_name,
		"bundle_sha256": export.sha256_hex(blob),
		"bundle_bytes": len(blob),
		"message_count": len(messages),
		"revision_count": len(revisions),
		"attachment_count": len(attachments),
		"completed_at": now(),
		"error": None,
	}


def _timezone() -> str:
	"""The site's time zone name, for the README and the transcript header.

	Frappe stores naive site-local datetimes, so every timestamp in this bundle is a wall
	clock with no zone attached. Naming it is the difference between a document a reader can
	order events from and one where they will assume their own zone, or UTC, and be wrong
	roughly half the time.

	Returns ``""`` rather than guessing when it cannot be read — the README has a sentence for
	the unknown case, and "we do not know" is a true statement where "UTC" would be a false
	one. Never raises: a missing time zone must not fail an export.
	"""
	try:
		from frappe.utils import get_system_timezone

		return str(get_system_timezone() or "").strip()
	except Exception:
		return ""


def _room_sql(rooms: list[str]) -> tuple[str, dict[str, Any]]:
	"""A bound IN-list. Never interpolated — these names arrive from a request body."""
	keys = {f"room{i}": name for i, name in enumerate(rooms)}
	placeholders = ", ".join(f"%({k})s" for k in keys) or "''"
	return f"({placeholders})", keys


def _messages(rooms: list[str]) -> list[dict[str, Any]]:
	"""Every message in the named rooms, oldest first.

	**No membership filter, and that is the one place in this package where that is correct.**
	An export is decision #12's privileged read at its widest: the auditor is a participant in
	none of these rooms, so filtering by membership would return an empty bundle and look
	correct doing it. The gate is the role and the reason, checked at `request_export`, and
	the record is the audit row written there.
	"""
	if not rooms:
		return []
	clause, values = _room_sql(rooms)
	# One over the cap, so `_build` can SEE the overflow rather than infer it from a
	# result that happens to be exactly the limit.
	values["limit"] = MAX_MESSAGES + 1
	return frappe.db.sql(
		f"""
		select {", ".join(f"`{f}`" for f in export.MESSAGE_FIELDS)}
		from `tabChat Message`
		where `room` in {clause}
		order by `room` asc, `seq` asc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)


def _revisions(rooms: list[str]) -> list[dict[str, Any]]:
	"""The edit and delete trail. Carries the bodies a deleted message no longer shows."""
	if not rooms:
		return []
	clause, values = _room_sql(rooms)
	# One over the cap, so `_build` can SEE the overflow rather than infer it from a
	# result that happens to be exactly the limit.
	values["limit"] = MAX_MESSAGES + 1
	return frappe.db.sql(
		f"""
		select {", ".join(f"`{f}`" for f in export.REVISION_FIELDS)}
		from `tabChat Message Revision`
		where `room` in {clause}
		order by `room` asc, `message` asc, `revision_no` asc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)


def _members(rooms: list[str]) -> list[dict[str, Any]]:
	"""Every membership row for the named rooms, **including the departed ones**.

	Unscoped for the same reason as :func:`_messages`, and departed rows are the point rather
	than an oversight: ``left_seq`` composes with a message's ``seq`` to answer "was this
	person still in the room when that was said" without involving a clock. Filtering to
	``is_active = 1`` would make somebody who read six months of a room and then left look
	like they were never there — which is the single most useful thing this file can say and
	the easiest to delete by accident.
	"""
	if not rooms:
		return []
	clause, values = _room_sql(rooms)
	values["limit"] = MAX_MESSAGES + 1
	return frappe.db.sql(
		f"""
		select {", ".join(f"`{f}`" for f in export.MEMBER_FIELDS)}
		from `tabChat Room Member`
		where `room` in {clause}
		order by `room` asc, `user` asc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)


def _drift_note(rooms: list[str]) -> str:
	"""A sentence for ``README.txt`` when the mirror and this system are known to disagree.

	This is the one input to the bundle that can only ever *lower* the confidence it projects,
	which is why it is built here and not left to a reader to go and check. The rooms in an
	export are mirrored into Google Chat; when the nightly sweep has a live finding for one of
	them, the honest thing to hand a lawyer alongside a completeness claim is the fact that
	the two sides did not agree and that this copy is the ERPNext side.

	Live means ``Open`` **or** ``Accepted``. Accepted is not resolved — it means somebody
	looked and decided to live with it, which is exactly the finding a reader of this bundle
	is entitled to hear about. Only ``Cleared`` is silence.

	Best effort. A drift table that cannot be read must not fail an export: the bundle is
	still true, and refusing to produce it because the caveat is unavailable trades a complete
	document with a missing footnote for no document at all. The failure is logged and the
	README says the check could not be made, which is itself the honest sentence.
	"""
	if not rooms:
		return ""
	try:
		clause, values = _room_sql(rooms)
		state_keys = {f"state{i}": s for i, s in enumerate(sorted(drift_rules.LIVE_STATES))}
		values.update(state_keys)
		state_clause = ", ".join(f"%({k})s" for k in state_keys)
		findings = frappe.db.sql(
			f"""
			select `drift_class`, `room`, `summary`, `observations`, `last_seen_at`
			from `tabChat Drift Report`
			where `room` in {clause} and `state` in ({state_clause})
			order by `drift_class` asc, `room` asc
			limit 50
			""",
			values,
			as_dict=True,
		)
	except Exception:
		frappe.log_error(title="chat export drift check failed", message=frappe.get_traceback())
		return (
			"The automated consistency check between this system and the Google Chat\n"
			"spaces it mirrors could not be run when this bundle was produced. That is\n"
			"not a finding of a problem; it means the check is unavailable and this\n"
			"bundle carries no assurance either way."
		)

	if not findings:
		return ""

	lines = [
		"This system mirrors messages to and from Google Chat. A nightly check compares",
		"the two sides, and at the time this bundle was produced it had the following",
		"unresolved findings for these rooms. They do not mean this copy is wrong — it is",
		"the ERPNext side of the mirror, and it is the system of record — but they do mean",
		"the two sides were not in agreement, and you should not treat the absence of a",
		"message here as proof it never existed:",
		"",
	]
	for finding in findings:
		lines.append(
			f"  - {finding.get('drift_class') or 'unknown'}"
			f" (room {finding.get('room') or '?'}, seen {cint(finding.get('observations'))} time(s),"
			f" last {finding.get('last_seen_at') or 'unknown'})"
			f"\n      {finding.get('summary') or ''}".rstrip()
		)
	return "\n".join(lines)


def _attachments(rooms: list[str]) -> tuple[dict[str, bytes], list[str]]:
	"""Files sent in these rooms, keyed ``attachments/<message>-<name>``.

	Named in the ADR row and in the README, and absent from `export.BUNDLE_FILES` because it
	is a directory rather than a fixed file — the manifest hashes each entry individually, so
	a missing attachment is caught the same way a tampered one is.

	**Bounded by total bytes rather than by count.** One 400 MB video is the case that turns
	an export into an incident, and a count limit does not see it.
	"""
	if not rooms:
		return {}, []
	clause, values = _room_sql(rooms)
	rows = frappe.db.sql(
		f"""
		select `name`, `message`, `file`, `file_name`
		from `tabChat Attachment`
		where `room` in {clause} and ifnull(`file`, '') != ''
		order by `message` asc, `name` asc
		""",
		values,
		as_dict=True,
	)

	out: dict[str, bytes] = {}
	omitted: list[str] = []
	total = 0
	for row in rows:
		data = _file_bytes(str(row.get("file") or ""))
		if not data:
			omitted.append(f"{row.get('name')}: no stored bytes")
			continue
		if total + len(data) > MAX_ATTACHMENT_BYTES:
			# Never truncate a file — a bundle short one attachment is legible, a bundle
			# containing half a PDF is not. And never omit one silently: the name goes into
			# the manifest, because "this export is missing something" has to be a fact a
			# reader can find rather than a difference they would have to notice.
			omitted.append(f"{row.get('name')}: over the {MAX_ATTACHMENT_BYTES} byte budget")
			continue
		total += len(data)
		safe = str(row.get("file_name") or "attachment").replace("/", "_").replace("\\", "_")
		out[f"attachments/{row.get('message')}-{safe}"] = data
	return out, omitted


def _file_bytes(file_name: str) -> bytes:
	"""Bytes for a `Chat Attachment.file`, which is a **File docname, not a URL**.

	`Chat Attachment.file` is a Link to File and holds `file_doc.name` — set that way in
	`api/compose.py` and `sync/attachments.py`. Looking it up by `{"file_url": ...}` matched
	nothing, raised `DoesNotExistError`, and was swallowed into `b""` — so **every attachment
	was silently dropped from every bundle** while `README.txt` told the reader the
	`attachments/` directory was there. The sibling reader in `sync/attachments.py` gets this
	right; this one did not.

	Resolved by primary key, and the parameter is renamed so the next reader is not misled.
	"""
	if not file_name:
		return b""
	try:
		return frappe.get_doc("File", file_name).get_content() or b""
	except Exception:
		return b""


def _git_commit() -> str:
	"""The commit that produced this bundle, or ``"unknown"``.

	In the manifest because **a hash proves the bytes and not the rules**: two exports of the
	same range can legitimately differ if the redaction rule changed between them, and without
	this the only visible fact is that the hashes disagree — which looks exactly like
	tampering.
	"""
	try:
		import subprocess
		from pathlib import Path

		root = Path(__file__).resolve().parents[3]
		out = subprocess.run(
			["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
			capture_output=True,
			text=True,
			timeout=5,
			check=False,
		)
		return (out.stdout or "").strip() or "unknown"
	except Exception:
		return "unknown"


def _store(request_name: str, blob: bytes) -> str:
	"""Store the bundle as a private File attached to the request.

	`attached_to_doctype` + `is_private` is the security model: Frappe's private-file check
	delegates to the attached document's `has_permission`, and `Chat Export Request` ships
	zero DocPerm — so `/private/files/…` is closed to everyone but Administrator. The audited
	endpoint below is the only door.
	"""
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"chat-export-{request_name}.zip",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": request_name,
			"is_private": 1,
			"content": blob,
		}
	)
	doc.insert(ignore_permissions=True)

	# **Ownership is taken off the requester, and this is a real hole rather than hygiene.**
	# `frappe.enqueue` captures the session user and the worker runs as them, so
	# `set_user_and_timestamp` stamps `owner` = the auditor who asked. Frappe's File
	# permission check grants the owner access *before* it delegates to the attached
	# document — so the requester could GET `/private/files/chat-export-<name>.zip` directly
	# and receive every message body, every deleted body in `revisions.jsonl` and every
	# attachment, with **no `export_downloaded` row and no `download_count`**. The audited
	# endpoint would have been one door among two, and the audit row a note about one path
	# rather than a fact about access.
	#
	# Reassigned rather than set at insert, because `set_user_and_timestamp` overwrites
	# `owner` unconditionally for a new document. `update_modified=False` so the row's
	# modified stamp still means "when the bundle was written".
	frappe.db.set_value("File", doc.name, "owner", "Administrator", update_modified=False)
	return doc.name


# --------------------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------------------


@frappe.whitelist()
def download_export(export_request: str) -> None:
	"""Stream the bundle, and record that somebody took a copy.

	**Not POST**, unlike the rest of this surface: the browser has to *navigate* to it for
	`frappe.local.response.type = "download"` to reach a file on disk — an XHR would receive
	the bytes and drop them. That is also why there is no reason parameter here; the reason
	was given when the export was requested, and asking again would invite a second, weaker
	one.

	**Its own audit row (`export_downloaded`), separate from the request's.** They are two
	acts, often days apart and sometimes by different people, and one row for both makes "who
	actually took a copy" unanswerable — the only question that matters afterwards.

	The audit row is written **before** the bytes are attached to the response. A download
	that happened without a record is the half of decision #12's trade nobody agreed to.
	"""
	user = _require_auditor()
	name = str(export_request or "").strip()
	if not name:
		raise frappe.PermissionError(frappe._("Not permitted"))

	row = frappe.db.get_value(
		DOCTYPE,
		name,
		[
			"name",
			"status",
			"bundle_file",
			"reason",
			"reason_category",
			"subject_user",
			"rooms",
			# Selected because the increment below reads it. Without it, `row.get` returns
			# None, `cint(None)` is 0, and every download resets the counter to 1 — which
			# would report a bundle taken nine times as taken once.
			"download_count",
		],
		as_dict=True,
	)
	# One uniform refusal for missing, unfinished and file-less. Distinguishing them would
	# answer "does an export of this room exist" to somebody who may not read it.
	if not row or row.get("status") != STATUS_COMPLETE or not row.get("bundle_file"):
		raise frappe.PermissionError(frappe._("Not permitted"))

	blob = _bundle_bytes(str(row["bundle_file"]))
	if not blob:
		raise frappe.PermissionError(frappe._("Not permitted"))

	recorded = audit.record_governance_event(
		event_type="export_downloaded",
		actor=user,
		subject_user=str(row.get("subject_user") or ""),
		reference_doctype=DOCTYPE,
		reference_name=name,
		reason=str(row.get("reason") or ""),
		detail=json.dumps({"reason_category": row.get("reason_category")}, sort_keys=True)[:1000],
	)
	if not recorded:
		# `record_governance_event` swallows its own failures and returns None. Fail closed:
		# an unrecorded download is precisely the thing the two-event split exists to make
		# impossible, and a caller that cannot tell is a caller that assumes it worked.
		raise frappe.ValidationError(frappe._("This download could not be recorded, so it was refused."))

	frappe.db.set_value(
		DOCTYPE,
		name,
		"download_count",
		cint(row.get("download_count")) + 1,
		update_modified=False,
	)
	frappe.db.commit()

	frappe.local.response.filename = f"chat-export-{name}.zip"
	frappe.local.response.filecontent = blob
	frappe.local.response.type = "download"


def _bundle_bytes(file_name: str) -> bytes:
	try:
		return frappe.get_doc("File", file_name).get_content() or b""
	except Exception:
		return b""


# --------------------------------------------------------------------------------------
# Gates, shared with the viewer's
# --------------------------------------------------------------------------------------


def _require_auditor() -> str:
	user = frappe.session.user
	if not user or user == "Guest":
		raise ExportRefused(frappe._("An export requires a signed-in user."))
	if not permissions._has_oversight(user):
		raise ExportRefused(
			frappe._(
				"An export requires the role named in Chat Settings → Admin Oversight Role. "
				"That field ships blank, so this fails closed until somebody configures it."
			)
		)
	return user


def _require_reason(reason: str | None, category: str | None) -> tuple[str, str]:
	"""The same grader the viewer and the compliance report use. One threshold, not three."""
	cleaned = (reason or "").strip()
	grade = access_report.reason_quality(cleaned)
	if grade != access_report.REASON_OK:
		raise ExportRefused(
			frappe._(
				"An export needs a stated reason of at least {0} characters that says "
				"something ({1}). This bundle may leave the building; the reason is what "
				"makes that defensible later."
			).format(access_report.REASON_MIN_LENGTH, grade)
		)
	normalised = audit.normalise_reason_category(category)
	if not normalised:
		raise ExportRefused(frappe._("Choose a reason category."))
	return cleaned, normalised


def _rooms(raw: Any) -> list[str]:
	if isinstance(raw, str):
		raw = raw.strip()
		try:
			parsed = json.loads(raw) if raw.startswith("[") else raw.split(",")
		except ValueError:
			parsed = raw.split(",")
	else:
		parsed = raw or []
	names = sorted({str(r).strip() for r in parsed if str(r or "").strip()})
	if not names:
		raise ExportRefused(frappe._("Name the rooms to export."))
	if len(names) > MAX_ROOMS:
		raise ExportRefused(frappe._("One export may name at most {0} rooms.").format(MAX_ROOMS))
	return names

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled work for the capture widget (WI-079 slice 2).

Two jobs, both idempotent, both safe to run late or twice:

- :func:`purge_expired_capture_files` (daily). It deletes a request's screenshots and its
  capture-context file 180 days after the request closed, and keeps the request itself: its
  text, decision and Task links. Nik decided this on 2026-09-23.
- :func:`match_capture_error_logs` (hourly). It links a failed request in a capture snapshot to
  the ``Error Log`` row the server wrote for it.

Why matching is a job and not part of filing
--------------------------------------------
Frappe v16 writes a 5xx's Error Log late. ``frappe/app.py`` calls ``log_error_snapshot``, which
calls ``log_error(..., defer_insert=True)`` and pushes the row onto a redis list. The list is
flushed by ``frappe.deferred_insert.save_to_db`` on the ``0/15`` cron. The insert then runs
``set_user_and_timestamp`` again, so the row's ``owner`` is the scheduler user and its
``creation`` is the flush time, up to 15 minutes after the failure.

At the moment somebody files a report, the row usually does not exist yet, and when it does,
``owner`` and ``creation`` both mislead. The facts that identify it are in its ``metadata``
JSON: ``user``, ``method`` (the HTTP verb) and ``path``. So this job runs hourly over recent
captures, filters on a window wide enough for the flush, and confirms each match from
``metadata``.

A match is written as a Comment on the request, and that is deliberate:
- Error Log rows are cleared after 14 days (``default_log_clearing_doctypes``), so the comment
  copies the facts that matter rather than relying on the link.
- The comment is on the timeline, not in a field, so it stays out of Triton's bulk sync, as the
  context file does (ADR 0016 §1).

Frappe's ``trace_id`` would make the match exact. It is written only when ``monitor`` is on,
and turning that on is out of scope for WI-079.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import frappe

from erpnext_enhancements.product_feedback.states import TERMINAL_STATES

DOCTYPE = "Enhancement Request"
SOURCE_CAPTURE = "Capture"
CAPTURE_CONTEXT_PREFIX = "capture-context-"

#: Decided with the item (WI-079): 180 days after the request reaches a terminal state.
RETENTION_DAYS = 180

#: Image files are the screenshots. A PDF or a log someone attached on the /feedback form is
#: not, and the decision covers screenshots and the context file only.
SCREENSHOT_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "heif"})

#: Bounded runs, so one bad day cannot turn into a job that never finishes.
MAX_REQUESTS_PER_RUN = 200

#: Matching looks at captures filed between 36 hours ago and 20 minutes ago. The lower bound
#: gives the deferred-insert flush (every 15 min) time to write the row. The upper bound covers
#: a day and a half of hourly runs, so one missed run or a deploy costs nothing.
MATCH_LOOKBACK = timedelta(hours=36)
MATCH_SETTLE = timedelta(minutes=20)

#: Error Log `creation` is the flush time, so a row appears between the failure and about 16
#: minutes after it. A little slack either side absorbs clock skew between browser and server.
FLUSH_BEFORE = timedelta(minutes=2)
FLUSH_AFTER = timedelta(minutes=17)

COMMENT_MARKER = "Matched server error"


# ------------------------------------------------------------------------------ retention


def is_capture_artifact(file_name: str) -> bool:
	"""A screenshot or the capture-context file. Nothing else attached to a request is."""
	name = (file_name or "").strip().lower()
	if name.startswith(CAPTURE_CONTEXT_PREFIX) and name.endswith(".json"):
		return True
	return "." in name and name.rsplit(".", 1)[1] in SCREENSHOT_EXTENSIONS


def purge_expired_capture_files() -> dict[str, int]:
	"""Daily. Delete the screenshots and context file of requests closed 180+ days ago.

	The clock is ``terminal_at``. For a request with no ``terminal_at`` it falls back to
	``modified``, which is never earlier than when the request really closed, so the fallback
	can only keep files longer. The query joins to ``File`` and returns only requests that
	still have attachments, so a request already cleaned never comes back.

	Each deletion commits on its own. ``File.on_trash`` removes the bytes before the
	transaction commits, and a rollback would leave a row pointing at nothing.
	"""
	cutoff = frappe.utils.add_days(frappe.utils.now_datetime(), -RETENTION_DAYS)
	requests = frappe.db.sql(
		"""
		select distinct er.name
		from `tabEnhancement Request` er
		join `tabFile` f
			on f.attached_to_doctype = %(doctype)s and f.attached_to_name = er.name
		where er.status in %(terminal)s
			and coalesce(er.terminal_at, er.modified) < %(cutoff)s
		order by er.name
		limit %(limit)s
		""",
		{
			"doctype": DOCTYPE,
			"terminal": tuple(sorted(TERMINAL_STATES)),
			"cutoff": cutoff,
			"limit": MAX_REQUESTS_PER_RUN,
		},
		as_dict=True,
	)

	deleted = 0
	failed = 0
	for row in requests:
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": DOCTYPE, "attached_to_name": row.name},
			fields=["name", "file_name"],
		)
		for f in files:
			if not is_capture_artifact(f.file_name):
				continue
			try:
				frappe.delete_doc("File", f.name, ignore_permissions=True, delete_permanently=True)
				frappe.db.commit()
				deleted += 1
			except Exception:
				frappe.db.rollback()
				failed += 1
				frappe.log_error(title=f"Capture retention could not delete {f.name} on {row.name}")
	return {"requests": len(requests), "deleted": deleted, "failed": failed}


# --------------------------------------------------------------------------- error logs


def _parse_iso(value: Any) -> datetime | None:
	"""A browser ISO timestamp as a naive datetime in its own frame, or None."""
	if not isinstance(value, str) or not value.strip():
		return None
	text = value.strip().replace("Z", "+00:00")
	try:
		parsed = datetime.fromisoformat(text)
	except ValueError:
		return None
	return parsed.replace(tzinfo=None) if parsed.tzinfo is None else parsed.astimezone().replace(tzinfo=None)


def failed_server_requests(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
	"""The snapshot's requests that can have an Error Log: status 500 and up."""
	out = []
	for req in (snapshot or {}).get("requests") or []:
		if not isinstance(req, dict):
			continue
		try:
			status = int(req.get("status") or 0)
		except (TypeError, ValueError):
			continue
		if status >= 500 and req.get("path"):
			out.append(req)
	return out


def estimate_server_time(request_at: Any, captured_at: Any, filed_at: datetime) -> datetime:
	"""When a failure happened, in the server's frame.

	The browser's clock can be minutes off. The snapshot says how long before capture the
	failure happened (``captured_at - request_at``), and the server knows when the report
	arrived (``filed_at``). Subtracting the first from the second removes the skew. When
	either browser timestamp is unreadable, it assumes the failure happened at filing.
	"""
	r_at = _parse_iso(request_at)
	c_at = _parse_iso(captured_at)
	if r_at is None or c_at is None:
		return filed_at
	delta = c_at - r_at
	if delta < timedelta(0):
		delta = timedelta(0)
	return filed_at - delta


def match_error_logs(
	failed: list[dict[str, Any]],
	error_rows: list[dict[str, Any]],
	user: str,
	captured_at: Any,
	filed_at: datetime,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
	"""Pair each failed request with at most one Error Log row: same user, verb and path, with
	``creation`` inside the flush window, nearest first. A row is used once."""
	used: set[str] = set()
	matches = []
	for req in failed:
		when = estimate_server_time(req.get("at"), captured_at, filed_at)
		lo, hi = when - FLUSH_BEFORE, when + FLUSH_AFTER
		method = str(req.get("method") or "").upper()
		path = str(req.get("path") or "")
		best = None
		best_gap = None
		for row in error_rows:
			name = row.get("name")
			if not name or name in used:
				continue
			meta = row.get("metadata")
			if isinstance(meta, str):
				try:
					meta = json.loads(meta)
				except ValueError:
					continue
			if not isinstance(meta, dict):
				continue
			if (meta.get("user") or "") != user:
				continue
			if str(meta.get("method") or "").upper() != method or (meta.get("path") or "") != path:
				continue
			created = row.get("creation")
			if isinstance(created, str):
				created = frappe.utils.get_datetime(created)
			if not isinstance(created, datetime) or not (lo <= created <= hi):
				continue
			gap = abs((created - when).total_seconds())
			if best is None or gap < best_gap:
				best, best_gap = row, gap
		if best is not None:
			used.add(best["name"])
			matches.append((req, best))
	return matches


def _read_snapshot(request_name: str) -> dict[str, Any] | None:
	file_name = frappe.db.get_value(
		"File",
		{
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": request_name,
			"file_name": ("like", f"{CAPTURE_CONTEXT_PREFIX}%"),
		},
		"name",
	)
	if not file_name:
		return None
	try:
		content = frappe.get_doc("File", file_name).get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8", "replace")
		data = json.loads(content or "{}")
		return data if isinstance(data, dict) else None
	except Exception:
		return None


def _already_noted(request_name: str, error_log: str) -> bool:
	return bool(
		frappe.db.exists(
			"Comment",
			{
				"reference_doctype": DOCTYPE,
				"reference_name": request_name,
				"comment_type": "Comment",
				"content": ("like", f"%{error_log}%"),
			},
		)
	)


def _comment_text(req: dict[str, Any], row: dict[str, Any]) -> str:
	title = frappe.utils.escape_html(str(row.get("method") or "")[:200])
	path = frappe.utils.escape_html(str(req.get("path") or "")[:300])
	method = frappe.utils.escape_html(str(req.get("method") or "").upper()[:10])
	status = frappe.utils.escape_html(str(req.get("status") or ""))
	exc = frappe.utils.escape_html(str(req.get("exc_type") or "")[:140])
	parts = [
		f"<p><b>{COMMENT_MARKER}</b> for <code>{method} {path}</code> ({status}{', ' + exc if exc else ''}):",
		f" Error Log <code>{frappe.utils.escape_html(row['name'])}</code>, written {frappe.utils.escape_html(str(row.get('creation') or ''))}.</p>",
		f"<p>{title}</p>",
		"<p><i>Error Log rows are cleared after 14 days; this note keeps the facts.</i></p>",
	]
	return "".join(parts)


def match_capture_error_logs() -> dict[str, int]:
	"""Hourly. Note on each recent capture which Error Log belongs to each failed request."""
	now = frappe.utils.now_datetime()
	requests = frappe.get_all(
		DOCTYPE,
		filters=[
			["source", "=", SOURCE_CAPTURE],
			["requested_at", "is", "set"],
			["requested_at", ">=", now - MATCH_LOOKBACK],
			["requested_at", "<=", now - MATCH_SETTLE],
		],
		fields=["name", "requested_by", "requested_at"],
		order_by="requested_at asc",
		limit_page_length=MAX_REQUESTS_PER_RUN,
	)

	noted = 0
	for req_row in requests:
		try:
			snapshot = _read_snapshot(req_row.name)
			failed = failed_server_requests(snapshot or {})
			if not failed:
				continue
			filed_at = frappe.utils.get_datetime(req_row.requested_at)
			windows = [
				estimate_server_time(r.get("at"), snapshot.get("captured_at"), filed_at) for r in failed
			]
			lo = min(windows) - FLUSH_BEFORE
			hi = max(windows) + FLUSH_AFTER
			rows = frappe.db.sql(
				"""
				select name, method, creation, metadata
				from `tabError Log`
				where creation between %(lo)s and %(hi)s
					and metadata like %(user_like)s
				order by creation
				limit 500
				""",
				{"lo": lo, "hi": hi, "user_like": f"%{req_row.requested_by}%"},
				as_dict=True,
			)
			for req, row in match_error_logs(
				failed, rows, req_row.requested_by, snapshot.get("captured_at"), filed_at
			):
				if _already_noted(req_row.name, row["name"]):
					continue
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Comment",
						"reference_doctype": DOCTYPE,
						"reference_name": req_row.name,
						"content": _comment_text(req, row),
					}
				).insert(ignore_permissions=True)
				noted += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Capture Error Log match failed for {req_row.name}")
	return {"requests": len(requests), "noted": noted}

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Telling people things: a bell row and an email, and nothing else.

Four events — a request was filed, its breakdown finished, a reviewer decided, tasks were
created — each going to exactly one audience. Every function here is best-effort and never
raises: a notification that fails must not roll back the decision that triggered it.

--------------------------------------------------------------------------------------
Why this is not ``status_alerts._deliver``, and not a Notification fixture either
--------------------------------------------------------------------------------------

``status_alerts._deliver`` is the app's existing bell+email helper and was the obvious reuse.
It also sends **SMS**, through the Triton gateway to ``Employee.cell_number``, and it is not
optional in that function. A feature request is not an operational alert; texting somebody
about a nice-to-have at 9pm is how a channel gets muted for the things that matter.

A ``Notification`` fixture was the other candidate and is what the maintenance workflow uses.
``tests/test_notification_recipients.py`` pins an invariant that rules it out here:
recipients must be **group addresses**, never roles, because a role recipient resolves to
whoever holds it today and stops working when somebody leaves. There is no dev group address
to point at, and the audience is a short list of named people — which is what
``Product Feedback Settings.reviewer_recipients`` is.

--------------------------------------------------------------------------------------
The bell type is ``Alert``, and that choice is what makes the email exactly one email
--------------------------------------------------------------------------------------

``NotificationLog.after_insert`` sends its own email whenever
``is_email_notifications_enabled_for_type(for_user, type)`` — so inserting a bell row and
*also* calling ``sendmail`` would double-send for most types. It does not for this one:
Frappe's own ``hooks.py`` ships ``notification_skip_email_types = ["Alert"]`` (read from the
v16 tree, 2026-08-17), and that check runs **before** the user's own settings, so no per-user
opt-in can turn it back on. An ``Alert`` row is therefore bell-only by construction, and the
email below is the only one that goes out.

``Alert`` is also in Frappe's ``notification_self_notify_types``, which matters here: the
reviewer who approves a request is the same person the breakdown-ready notification goes to,
and every other type suppresses a notification whose recipient is also the actor.

Two smaller traps, both confirmed against the v16 tree:

* **``type`` is a Link to a real ``Notification Type`` record.** Production holds exactly
  five — Alert, Assignment, Energy Point, Mention, Share. Inventing a sixth here would be a
  link-validation failure on insert, so this module uses a shipped one and installs nothing.
* **``for_user`` is a ``User`` docname**, and this module inserts rows directly. The
  email-matching trap in ``chat/notifications/bell.py`` applies to
  ``enqueue_create_notification``, which filters on ``User.email``; the email address below
  is resolved explicitly for the same reason.

Links point at ``/feedback/...``, not at the desk form. Requesters may be Website Users with
``desk_access = 0``, for whom a desk link is a permission error rather than a destination.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings import (
	reviewer_users,
)

#: A shipped Frappe ``Notification Type``. See the module docstring — this specific value is
#: what guarantees the bell row does not send a second email.
BELL_TYPE = "Alert"

DOCTYPE = "Enhancement Request"


def request_submitted(request_name: str) -> None:
	"""A new request is waiting for review. Goes to the reviewer list."""
	if _suppressed():
		return
	row = _request(request_name)
	if not row:
		return
	recipients = [u for u in reviewer_users() if u != row.get("requested_by")]
	if not recipients:
		return

	who = _full_name(row.get("requested_by"))
	kind = (row.get("request_type") or "request").lower()
	_send(
		recipients,
		subject=f"New {kind} from {who}: {row.get('title') or request_name}",
		body=(
			f"<p>{frappe.utils.escape_html(who)} filed a {frappe.utils.escape_html(kind)} "
			f"marked <b>{frappe.utils.escape_html(row.get('impact') or '')}</b>.</p>"
			f"<p>Nothing reaches a project until you approve it.</p>"
		),
		request_name=request_name,
	)


def breakdown_finished(request_name: str, *, ready: bool) -> None:
	"""The proposal is ready to review, or it failed. Goes to whoever approved it."""
	if _suppressed():
		return
	row = _request(request_name)
	if not row:
		return
	recipient = row.get("decided_by")
	if not recipient:
		return

	title = row.get("title") or request_name
	if ready:
		subject = f"Work breakdown ready: {title}"
		body = "<p>Triton proposed a breakdown. Review and edit it before anything is created.</p>"
	else:
		subject = f"Work breakdown failed: {title}"
		body = (
			"<p>Triton could not produce a breakdown. The request is waiting with the reason "
			"recorded; you can re-run it.</p>"
			f"<p><i>{frappe.utils.escape_html((row.get('breakdown_error') or '')[:400])}</i></p>"
		)
	_send([recipient], subject=subject, body=body, request_name=request_name)


def decision_made(request_name: str) -> None:
	"""A reviewer approved, rejected or closed the request. Goes to whoever filed it."""
	if _suppressed():
		return
	row = _request(request_name)
	if not row:
		return
	recipient = row.get("requested_by")
	if not recipient:
		return

	status = row.get("status") or ""
	title = row.get("title") or request_name
	reason = frappe.utils.escape_html((row.get("decision_reason") or "").strip())

	if status == "Rejected":
		body = "<p>Your request will not be picked up.</p>"
		if reason:
			body += f"<p><b>Reason:</b> {reason}</p>"
	elif status == "Duplicate":
		body = (
			"<p>Your request is already covered by existing work "
			f"({frappe.utils.escape_html(row.get('duplicate_of_task') or '')}).</p>"
		)
		if reason:
			body += f"<p>{reason}</p>"
	else:
		body = "<p>Your request was approved and is being broken into tasks.</p>"

	_send([recipient], subject=f"{status}: {title}", body=body, request_name=request_name)


def tasks_created(request_name: str, task_names: list[str]) -> None:
	"""Work exists now. Goes to whoever filed it, naming the tasks."""
	if _suppressed():
		return
	row = _request(request_name)
	if not row or not task_names:
		return
	recipient = row.get("requested_by")
	if not recipient:
		return

	count = len(task_names)
	listed = "".join(f"<li>{frappe.utils.escape_html(name)}</li>" for name in task_names[:12])
	_send(
		[recipient],
		subject=f"{count} task{'' if count == 1 else 's'} created: {row.get('title') or request_name}",
		body=f"<p>Your request is now on the board.</p><ul>{listed}</ul>",
		request_name=request_name,
	)


# ------------------------------------------------------------------------------ plumbing


def _suppressed() -> bool:
	"""True while migrating/installing/patching/importing — never notify from those.

	Lifted from ``status_alerts._in_maintenance_context``. Without it, a migrate that touches
	rows mails everybody about decisions taken months ago.
	"""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _request(request_name: str) -> dict[str, Any]:
	try:
		row = frappe.db.get_value(
			DOCTYPE,
			request_name,
			[
				"name",
				"title",
				"status",
				"request_type",
				"impact",
				"requested_by",
				"decided_by",
				"decision_reason",
				"duplicate_of_task",
				"breakdown_error",
			],
			as_dict=True,
		)
	except Exception:
		return {}
	return dict(row or {})


def _full_name(user: str) -> str:
	if not user:
		return "Someone"
	try:
		return frappe.db.get_value("User", user, "full_name") or user
	except Exception:
		return user


def _send(recipients: list[str], *, subject: str, body: str, request_name: str) -> None:
	"""One bell row and one email per recipient. Never raises.

	Each recipient is delivered independently: one bad address must not cost the others their
	notification, which is the failure mode ``status_alerts._deliver`` also guards.
	"""
	link = f"/feedback/request/{request_name}"
	for user in recipients:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": BELL_TYPE,
					"document_type": DOCTYPE,
					"document_name": request_name,
					"subject": subject[:140],
					"email_content": body,
					# The SPA, not the desk form — see the module docstring.
					"link": link,
				}
			).insert(ignore_permissions=True)
		except Exception:
			_log(f"Enhancement Request bell row failed for {user}")

		try:
			address = frappe.db.get_value("User", user, "email")
			if not address:
				continue
			frappe.sendmail(
				recipients=[address],
				subject=subject[:140],
				message=f"{body}<p><a href='{frappe.utils.get_url(link)}'>Open the request</a></p>",
				reference_doctype=DOCTYPE,
				reference_name=request_name,
				# Queued, not sent inline: this runs inside the transaction that made the
				# decision, and a slow SMTP handshake would hold it open.
				now=False,
			)
		except Exception:
			_log(f"Enhancement Request email failed for {user}")


def _log(title: str) -> None:
	"""Error Log write that cannot itself explode the caller.

	``frappe.log_error`` opens a transaction, so on a dead connection it raises from inside
	the ``except`` that was handling the first failure — which is how a job aborts with an
	empty Error Log.
	"""
	try:
		frappe.log_error(frappe.get_traceback(), title[:140])
	except Exception:
		pass

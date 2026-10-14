"""What the Desk form's sidebar and timeline know about a record, for the guided page's record tools.

Nik, 2026-10-14: "We shouldn't have lost any fidelity or information on any of the UI/UX changes."
The guided page (and the board's side panel) replaced the Desk form, and with it the form's sidebar
(attachments, assigned to, tags, follow, shared) and its timeline (versions, emails, assignment and
attachment logs). The form reads all of that from ONE call, ``frappe.desk.form.load.get_docinfo``,
which answers in ``frappe.response["docinfo"]`` rather than in ``message``; the page's one request
wrapper (sf_kit.call) reads only ``message``. So ``form_kit.api.record_info`` runs that same
function, with its own permission check (``get_lazy_doc(..., check_permission=True)``: read on the
record), and hands back what it found, shaped for a page that writes text:

- every user string that frappe stores as HTML (an email body, a ToDo description, a log line) comes
  back as plain text (``plain``), so the page writes it with ``.text()`` and never as markup;
- users come back with their full names beside the ids;
- an email body is cut to ``BODY_LIMIT`` characters; the Communication itself is one tap away.

Plus what ERPNext's CRM Activities tab shows on top (``erpnext.crm.utils.get_open_activities``: the
record's open ToDos and Events), the record's own created / last-edited stamps, which the timeline
draws from ``frm.doc``, and its Google Drive folder (the form's "Open Drive Folder" reads two hidden
fields no guided step carries), each field only where the caller may read it.

The Version rows keep their raw ``data``: the page turns them into sentences the way frappe's own
``version_timeline_content_builder.js`` does, field by field, with the Desk's display rule
(``frappe.perm.get_field_display_status``), so a field the user cannot read is never named.

Bench-free tests: ``tests/test_guided_record_tools.py``.
"""

import html
import json
import re

import frappe

#: An email body longer than this is cut (the Communication form has the rest).
BODY_LIMIT = 1200
#: Any other text (a ToDo description, a log line).
TEXT_LIMIT = 400

#: The record's permissions the page acts on, from docinfo's ``get_doc_permissions``.
PERMISSION_KEYS = (
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"print",
	"email",
	"share",
	"report",
	"export",
)


def plain(value, limit=TEXT_LIMIT):
	"""HTML as plain text with its line breaks, cut to ``limit`` characters with an ellipsis."""
	text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", "", str(value or ""))
	text = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>|</tr\s*>", "\n", text)
	text = re.sub(r"<[^>]*>", "", text)
	text = html.unescape(text).replace("\xa0", " ").replace("\r", "")
	text = re.sub(r"[ \t]+", " ", text)
	text = re.sub(r" ?\n ?", "\n", text)
	text = re.sub(r"\n{3,}", "\n\n", text).strip()
	if limit and len(text) > limit:
		text = text[: limit - 1].rstrip() + "…"
	return text


def _names(users):
	"""Full names for a set of user ids (``frappe.utils.get_fullname`` is cached per request)."""
	out = {}
	for user in users:
		if user and user not in out:
			out[user] = frappe.utils.get_fullname(user) or user
	return out


def _when(value):
	return str(value) if value else ""


def _attachments(rows):
	files = [row for row in rows or [] if row.get("name")]
	stamps = {}
	if files:
		for row in frappe.get_all(
			"File",
			filters={"name": ["in", [row.get("name") for row in files]]},
			fields=["name", "owner", "creation"],
		):
			stamps[row.name] = row
	people = _names([(stamps.get(row.get("name")) or {}).get("owner") for row in files])
	out = []
	for row in files:
		stamp = stamps.get(row.get("name")) or {}
		owner = stamp.get("owner")
		out.append(
			{
				"name": row.get("name"),
				"file_name": row.get("file_name") or "",
				"file_url": row.get("file_url") or "",
				"file_type": row.get("file_type") or "",
				"file_size": row.get("file_size") or 0,
				"is_private": 1 if row.get("is_private") else 0,
				"attached_to_field": row.get("attached_to_field") or "",
				"owner": owner or "",
				"owner_name": people.get(owner, owner or ""),
				"creation": _when(stamp.get("creation")),
			}
		)
	out.sort(key=lambda row: row["creation"], reverse=True)
	return out


def _communications(rows):
	out = []
	for row in rows or []:
		attachments = row.get("attachments")
		if isinstance(attachments, str):
			try:
				attachments = json.loads(attachments)
			except ValueError:
				attachments = []
		out.append(
			{
				"name": row.get("name"),
				"type": row.get("communication_type") or "",
				"medium": row.get("communication_medium") or "",
				"subject": plain(row.get("subject"), TEXT_LIMIT),
				"sender": row.get("sender") or "",
				"sender_name": row.get("sender_full_name") or row.get("sender") or "",
				"recipients": row.get("recipients") or "",
				"cc": row.get("cc") or "",
				"at": _when(row.get("communication_date") or row.get("creation")),
				"status": row.get("delivery_status") or "",
				"text": plain(row.get("content"), BODY_LIMIT),
				"attachments": len(attachments or []),
			}
		)
	return out


def _logs(docinfo, people):
	"""The timeline's comment-kind logs (assigned, attached, info, workflow) as text lines."""
	kinds = (
		("assignment_logs", "assignment"),
		("attachment_logs", "attachment"),
		("info_logs", "info"),
		("workflow_logs", "workflow"),
	)
	out = []
	for key, kind in kinds:
		for row in docinfo.get(key) or []:
			owner = row.get("owner")
			out.append(
				{
					"kind": kind,
					"comment_type": row.get("comment_type") or "",
					"owner": owner or "",
					"owner_name": people.get(owner, owner or ""),
					"at": _when(row.get("creation")),
					"text": plain(row.get("content"), TEXT_LIMIT),
				}
			)
	return out


def _open_activities(doctype, name):
	"""ERPNext's CRM Activities: the record's open ToDos and Events (empty without ERPNext)."""
	try:
		from erpnext.crm.utils import get_open_activities
	except ImportError:
		return {"tasks": [], "events": []}
	found = get_open_activities(doctype, name) or {}
	people = _names([row.get("allocated_to") for row in found.get("tasks") or []])
	tasks = [
		{
			"name": row.get("name"),
			"text": plain(row.get("description"), TEXT_LIMIT),
			"allocated_to": row.get("allocated_to") or "",
			"allocated_name": people.get(row.get("allocated_to"), row.get("allocated_to") or ""),
			"date": _when(row.get("date")),
		}
		for row in found.get("tasks") or []
	]
	events = [
		{
			"name": row.get("name"),
			"subject": plain(row.get("subject"), TEXT_LIMIT),
			"category": row.get("event_category") or "",
			"starts_on": _when(row.get("starts_on")),
			"ends_on": _when(row.get("ends_on")),
			"text": plain(row.get("description"), TEXT_LIMIT),
		}
		for row in found.get("events") or []
	]
	return {"tasks": tasks, "events": events}


def _readable(meta, fieldname):
	"""Whether the caller may read ``fieldname``: it exists, and its permlevel is one they read."""
	field = meta.get_field(fieldname)
	if not field:
		return False
	level = int(field.permlevel or 0)
	if not level:
		return True
	try:
		return level in (meta.get_permlevel_access("read") or [])
	except Exception:
		return False


def _drive_folder(doctype, name):
	"""The Google Drive folder the form's "Open Drive Folder" button opens (drive_folder_button.js):
	``custom_drive_folder_id`` and the daily reconcile's ``custom_drive_folder_missing``, where the
	DocType has them. The fields are hidden on the form, so they are in no guided step."""
	meta = frappe.get_meta(doctype)
	if not _readable(meta, "custom_drive_folder_id"):
		return {"id": "", "missing": 0}
	fields = ["custom_drive_folder_id"]
	if _readable(meta, "custom_drive_folder_missing"):
		fields.append("custom_drive_folder_missing")
	row = frappe.db.get_value(doctype, name, fields, as_dict=True, order_by=None) or {}
	return {
		"id": row.get("custom_drive_folder_id") or "",
		"missing": 1 if row.get("custom_drive_folder_missing") else 0,
	}


def build(doctype, name):
	"""The record's docinfo, shaped for the guided page (see the module head). Raises
	PermissionError, as the form's own load does, when the caller cannot read the record."""
	from frappe.desk.form.load import get_docinfo

	previous = frappe.response.get("docinfo")
	get_docinfo(doctype=doctype, name=name)
	docinfo = frappe._dict(frappe.response.get("docinfo") or {})
	# get_docinfo answers into frappe.response; this endpoint answers in `message` only.
	if previous is None:
		frappe.response.pop("docinfo", None)
	else:
		frappe.response["docinfo"] = previous

	stamps = (
		frappe.db.get_value(
			doctype,
			name,
			["owner", "creation", "modified_by", "modified", "docstatus"],
			as_dict=True,
			order_by=None,
		)
		or frappe._dict()
	)

	users = set()
	users.update(row.get("owner") for row in docinfo.get("versions") or [])
	users.update(row.get("owner") for row in docinfo.get("assignments") or [])
	users.update(row.get("owner") for row in docinfo.get("milestones") or [])
	for key in ("assignment_logs", "attachment_logs", "info_logs", "workflow_logs"):
		users.update(row.get("owner") for row in docinfo.get(key) or [])
	users.update(row.get("user") for row in docinfo.get("shared") or [] if isinstance(row, dict))
	users.update([stamps.get("owner"), stamps.get("modified_by")])
	people = _names(users)

	permissions = docinfo.get("permissions") or {}
	tags = [tag for tag in str(docinfo.get("tags") or "").split(",") if tag.strip()]
	return {
		"doctype": doctype,
		"name": name,
		"created": {
			"by": stamps.get("owner") or "",
			"by_name": people.get(stamps.get("owner"), stamps.get("owner") or ""),
			"at": _when(stamps.get("creation")),
		},
		"modified": {
			"by": stamps.get("modified_by") or "",
			"by_name": people.get(stamps.get("modified_by"), stamps.get("modified_by") or ""),
			"at": _when(stamps.get("modified")),
		},
		"docstatus": int(stamps.get("docstatus") or 0),
		"permissions": {key: 1 if permissions.get(key) else 0 for key in PERMISSION_KEYS},
		"attachments": _attachments(docinfo.get("attachments")),
		"communications": _communications(docinfo.get("communications")),
		"versions": [
			{
				"name": row.get("name"),
				"owner": row.get("owner") or "",
				"owner_name": people.get(row.get("owner"), row.get("owner") or ""),
				"at": _when(row.get("creation")),
				"data": row.get("data") or "",
			}
			for row in docinfo.get("versions") or []
		],
		"milestones": [
			{
				"owner": row.get("owner") or "",
				"owner_name": people.get(row.get("owner"), row.get("owner") or ""),
				"at": _when(row.get("creation")),
				"track_field": row.get("track_field") or "",
				"value": plain(row.get("value"), TEXT_LIMIT),
			}
			for row in docinfo.get("milestones") or []
		],
		"logs": _logs(docinfo, people),
		"assignments": [
			{
				"name": row.get("name"),
				"user": row.get("owner") or "",
				"user_name": people.get(row.get("owner"), row.get("owner") or ""),
				"text": plain(row.get("description"), TEXT_LIMIT),
				"status": row.get("status") or "",
			}
			for row in docinfo.get("assignments") or []
		],
		"tags": tags,
		"followed": bool(docinfo.get("is_document_followed")),
		"shared": [
			{
				"user": row.get("user") or "",
				"user_name": people.get(row.get("user"), row.get("user") or "") if row.get("user") else "",
				"everyone": 1 if row.get("everyone") else 0,
				"read": 1 if row.get("read") else 0,
				"write": 1 if row.get("write") else 0,
				"share": 1 if row.get("share") else 0,
			}
			for row in docinfo.get("shared") or []
			if isinstance(row, dict)
		],
		"document_email": docinfo.get("document_email") or "",
		"open": _open_activities(doctype, name),
		"drive_folder": _drive_folder(doctype, name),
	}

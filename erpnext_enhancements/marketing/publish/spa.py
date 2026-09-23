# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The /marketing app's endpoints (TASK-2026-01487).

Thin frappe over ``spa_rules.py`` (the rules) and ``approval.py`` (the four post actions, which
the app calls directly rather than through a second copy here). Every endpoint:

* is **POST**, never guest (``tests/test_marketing_spa.py`` asserts both on the decorators);
* starts with ``_require()`` -- Marketing Team, Marketing Manager or System Manager -- and then
  lets the DocPerms decide, through ``frappe.get_list`` and ``check_permission``;
* returns labels, never raw identifiers: a ``User`` docname is an email address, so a person
  is always shown by full name.

**Nothing here writes status, approver or approval time.** ``save_post`` takes an allowlist of
fields (``spa_rules.clean_post_input``) and the controller refuses those three in any ordinary
save anyway.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_system_timezone, now_datetime, nowdate

from erpnext_enhancements.marketing.core.utils import get_settings
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import gate, outbox, spa_rules, validation, workflow
from erpnext_enhancements.marketing.publish.approval import browser_request

POST = "Social Post"
TARGET = "Social Post Target"
MEDIA = "Social Post Media"
ACCOUNT = "Social Account"
ASSET = "Marketing Media Asset"
JOB = "Social Publish Job"
ATTEMPT = "Social Publish Attempt"
METRIC = "Social Post Metric"

POST_LIST_FIELDS = [
	"name",
	"title",
	"status",
	"scheduled_at",
	"owner",
	"modified",
	"modified_by",
	"network_check",
]
ASSET_FIELDS = [
	"name",
	"title",
	"asset_type",
	"usage_rights",
	"source",
	"file",
	"mime_type",
	"width",
	"height",
	"duration_seconds",
	"alt_text",
]
MEDIA_PAGE = 40


def _require():
	if not spa_rules.can_use(frappe.get_roles()):
		frappe.throw(
			_("The marketing app is for the Marketing Team, Marketing Managers and System Managers."),
			frappe.PermissionError,
		)


def _refuse(message):
	frappe.throw(str(message), title=_("Not done"))


def _payload(value):
	return frappe.parse_json(value) if isinstance(value, str) else value


def _full_names(users):
	users = sorted({u for u in users if u})
	if not users:
		return {}
	rows = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	names = {row.name: row.full_name for row in rows}
	# Never the docname: on this site it is an email address.
	return {user: names.get(user) or _("Unknown user") for user in users}


def _asset_summary(row):
	row = dict(row)
	path = row.get("file") or ""
	previewable = row.get("source") == "File" and row.get("asset_type") in ("Image", "Video") and path
	return {
		"name": row.get("name"),
		"title": row.get("title") or row.get("name"),
		"asset_type": row.get("asset_type"),
		"usage_rights": row.get("usage_rights"),
		"source": row.get("source"),
		"preview_url": path if previewable else None,
		"private": path.startswith("/private/"),
		"mime_type": row.get("mime_type"),
		"width": row.get("width"),
		"height": row.get("height"),
		"duration_seconds": row.get("duration_seconds"),
		"alt_text": row.get("alt_text"),
	}


def _assets(names):
	names = [n for n in names if n]
	if not names:
		return {}
	rows = frappe.get_list(ASSET, filters={"name": ["in", names]}, fields=ASSET_FIELDS, limit_page_length=0)
	return {row.name: _asset_summary(row) for row in rows}


def _decorate(posts):
	"""List rows for the calendar and the queue: networks, author and problem count."""
	names = [p.name for p in posts]
	networks = {}
	if names:
		for row in frappe.get_all(
			TARGET,
			filters={"parent": ["in", names], "parenttype": POST},
			fields=["parent", "network"],
			order_by="idx asc",
		):
			bucket = networks.setdefault(row.parent, [])
			if row.network and row.network not in bucket:
				bucket.append(row.network)
	people = _full_names([p.owner for p in posts] + [p.modified_by for p in posts])
	user = frappe.session.user
	return [
		{
			"name": p.name,
			"title": p.title,
			"status": p.status or outbox.POST_DRAFT,
			"scheduled_at": p.scheduled_at,
			"modified": p.modified,
			"networks": networks.get(p.name, []),
			"author": people.get(p.owner),
			"last_edited_by": people.get(p.modified_by),
			"yours": p.owner == user,
			"you_edited_last": p.modified_by == user,
			"editable": (p.status or outbox.POST_DRAFT) in outbox.EDITABLE_POST_STATUSES,
			"problems": len([line for line in (p.network_check or "").split("\n") if line.strip()]),
		}
		for p in posts
	]


def _post_payload(doc):
	user = frappe.session.user
	post = doc.as_dict()
	media_names = [row.asset for row in doc.get("media") or [] if row.asset]
	assets = _assets([*media_names, doc.get("video_thumbnail")])
	accounts = {
		row.name: row
		for row in frappe.get_list(
			ACCOUNT,
			filters={"name": ["in", [t.social_account for t in doc.get("targets") or []] or [""]]},
			fields=["name", "network", "account_name", "handle"],
			limit_page_length=0,
		)
	}
	people = _full_names([doc.owner, doc.modified_by, doc.get("approver")])
	return {
		"name": doc.name,
		"title": doc.title,
		"status": doc.status or outbox.POST_DRAFT,
		"scheduled_at": doc.scheduled_at,
		"modified": doc.modified,
		"body": doc.body or "",
		"link": doc.link or "",
		"link_title": doc.link_title or "",
		"link_description": doc.link_description or "",
		"video_title": doc.video_title or "",
		"video_tags": doc.video_tags or "",
		"video_thumbnail": doc.video_thumbnail or "",
		"youtube_playlist_id": doc.youtube_playlist_id or "",
		# Read-only here; the preview needs it because the link's utm_campaign carries it.
		"campaign": doc.get("campaign") or "",
		"author": people.get(doc.owner),
		"last_edited_by": people.get(doc.modified_by),
		"approver": people.get(doc.get("approver")) if doc.get("approver") else None,
		"approved_at": doc.get("approved_at"),
		"network_check": [line for line in (doc.network_check or "").split("\n") if line.strip()],
		"targets": [
			{
				"social_account": row.social_account,
				"network": row.network or (accounts.get(row.social_account) or {}).get("network"),
				"label": (accounts.get(row.social_account) or {}).get("account_name") or row.social_account,
				"variant_text": row.variant_text or "",
				"first_comment": row.first_comment or "",
				"status": row.status or "",
				"permalink": row.permalink or "",
			}
			for row in doc.get("targets") or []
		],
		"media": [assets[name] for name in media_names if name in assets],
		"thumbnail": assets.get(doc.get("video_thumbnail")),
		"actions": spa_rules.post_actions(
			post,
			user,
			set(frappe.get_roles(user)),
			bool(frappe.has_permission(POST, "write", doc=doc)),
			browser_request(),
		),
	}


@frappe.whitelist(methods=["POST"])
def get_bootstrap():
	"""Who is asking, what they may reach, and the numbers the composer counts against."""
	_require()
	user = frappe.session.user
	settings = get_settings()
	accounts = frappe.get_list(
		ACCOUNT,
		fields=[
			"name",
			"network",
			"account_name",
			"handle",
			"enabled",
			"quota_remaining",
			"quota_checked_at",
		],
		order_by="network asc, account_name asc",
		limit_page_length=0,
	)
	return {
		"user": user,
		"full_name": _full_names([user]).get(user),
		"can_approve": workflow.APPROVER_ROLE in set(frappe.get_roles(user)),
		"accounts": [
			{
				"name": a.name,
				"network": a.network,
				"label": a.account_name or a.handle or a.network,
				"handle": a.handle or "",
				"enabled": bool(cint(a.enabled)),
			}
			for a in accounts
		],
		"quota": spa_rules.quota_view([dict(a) for a in accounts], now_datetime()),
		"publishing": {
			"enabled": bool(cint(settings.get("enabled"))),
			"networks": {network: gate.network_enabled(network, settings) for network in P.PUBLISH_NETWORKS},
		},
		"limits": spa_rules.limits(),
		"networks": list(P.PUBLISH_NETWORKS),
		"asset_types": list(spa_rules.ASSET_TYPES),
		"usage_rights": list(spa_rules.USAGE_RIGHTS),
		"today": nowdate(),
		"now": now_datetime().replace(microsecond=0),
		"timezone": get_system_timezone(),
		"week_start": frappe.db.get_single_value("System Settings", "first_day_of_the_week") or "Sunday",
		"pending_approval": len(
			frappe.get_list(
				POST, filters={"status": outbox.POST_PENDING_APPROVAL}, pluck="name", limit_page_length=500
			)
		),
	}


@frappe.whitelist(methods=["POST"])
def get_calendar(start, end):
	"""Posts scheduled between ``start`` and ``end`` (dates, inclusive), and unscheduled drafts."""
	_require()
	try:
		first, last = spa_rules.calendar_window(start, end)
	except ValueError as exc:
		_refuse(exc)
	posts = frappe.get_list(
		POST,
		filters=[["scheduled_at", ">=", first], ["scheduled_at", "<=", last]],
		fields=POST_LIST_FIELDS,
		order_by="scheduled_at asc",
		limit_page_length=500,
	)
	unscheduled = frappe.get_list(
		POST,
		filters=[
			["scheduled_at", "is", "not set"],
			["status", "in", sorted(outbox.EDITABLE_POST_STATUSES)],
		],
		fields=POST_LIST_FIELDS,
		order_by="modified desc",
		limit_page_length=50,
	)
	return {"posts": _decorate(posts), "unscheduled": _decorate(unscheduled)}


@frappe.whitelist(methods=["POST"])
def get_post(name):
	"""One post, as the composer and the post page need it, with what the caller may do to it."""
	_require()
	doc = frappe.get_doc(POST, name)
	doc.check_permission("read")
	return _post_payload(doc)


@frappe.whitelist(methods=["POST"])
def check_post(values):
	"""What each network would refuse in the composer's current state, saved or not.

	``general`` is what is wrong whatever the network (no accounts, media not cleared, an account
	switched off); ``by_network`` is each network's own refusals. The same checks the outbox runs
	before it queues anything, so the composer never promises what the outbox would refuse.
	"""
	_require()
	from erpnext_enhancements.marketing.publish.sweeper import FrappeStore, post_dict, post_parts

	try:
		fields, targets, media = spa_rules.clean_post_input(_payload(values))
	except ValueError as exc:
		return {"general": [str(exc)], "by_network": {}}
	doc = frappe.new_doc(POST)
	doc.update(fields)
	for target in targets:
		doc.append("targets", target)
	for asset in media:
		doc.append("media", {"asset": asset})
	target_rows, media_rows = post_parts(doc)
	post = post_dict(doc)
	accounts = FrappeStore().accounts([t["social_account"] for t in target_rows])
	by_network = {}
	for target in target_rows:
		network = (accounts.get(target["social_account"]) or {}).get("network")
		if not network:
			continue
		found = by_network.setdefault(network, [])
		text = (target.get("variant_text") or "").strip() or (post.get("body") or "")
		for problem in validation.problems(
			network,
			text,
			post.get("link"),
			media_rows,
			target.get("first_comment") or "",
			post.get("link_title") or "",
			post,
		):
			if problem not in found:
				found.append(problem)
	per_network = {p for problems in by_network.values() for p in problems}
	general = [
		p for p in outbox.content_problems(post, target_rows, media_rows, accounts) if p not in per_network
	]
	return {"general": general, "by_network": by_network}


@frappe.whitelist(methods=["POST"])
def save_post(values, name=None, modified=None):
	"""Create a draft, or save one that is still a Draft or Pending Approval.

	``modified`` is the post's ``modified`` as the composer loaded it: a post somebody else saved
	since is refused rather than overwritten.
	"""
	_require()
	try:
		fields, targets, media = spa_rules.clean_post_input(_payload(values))
	except ValueError as exc:
		_refuse(exc)
	if not targets:
		_refuse(_("Choose at least one account to post to."))

	if name:
		doc = frappe.get_doc(POST, name)
		doc.check_permission("write")
		if not modified or get_datetime(modified) != get_datetime(doc.modified):
			_refuse(
				_("Somebody saved this post after you opened it. Reload it, then make your change again.")
			)
		if (doc.status or outbox.POST_DRAFT) not in outbox.EDITABLE_POST_STATUSES:
			_refuse(
				_(
					"This post is {0} and locked to what was approved. Cancel it and duplicate it to change it."
				).format(doc.status)
			)
		before = get_datetime(doc.scheduled_at).replace(microsecond=0) if doc.scheduled_at else None
	else:
		doc = frappe.new_doc(POST)
		before = None

	if fields["scheduled_at"] != before:
		problem = spa_rules.schedule_problem(fields["scheduled_at"], now_datetime())
		if problem:
			_refuse(problem)

	doc.update(fields)
	existing = [{"name": row.name, "social_account": row.social_account} for row in doc.get("targets") or []]
	update, add, remove = spa_rules.plan_targets(existing, targets)
	rows = {row.name: row for row in doc.get("targets") or []}
	for row_name, values_for_row in update:
		rows[row_name].update(values_for_row)
	doc.set("targets", [row for row in doc.get("targets") or [] if row.name not in remove])
	for values_for_row in add:
		doc.append("targets", values_for_row)
	doc.set("media", [{"asset": asset} for asset in media])

	if name:
		doc.save()
	else:
		doc.insert()
	return _post_payload(doc)


@frappe.whitelist(methods=["POST"])
def reschedule(name, scheduled_at, modified):
	"""Move a Draft or Pending Approval post to a new time: the calendar's drag and drop.

	An approved post's time is part of what was approved (``outbox.content_signature``), so it
	is refused: cancel it and duplicate it to move it.
	"""
	_require()
	doc = frappe.get_doc(POST, name)
	doc.check_permission("write")
	if not modified or get_datetime(modified) != get_datetime(doc.modified):
		_refuse(_("Somebody saved this post after the calendar loaded. Reload, then move it again."))
	if (doc.status or outbox.POST_DRAFT) not in outbox.EDITABLE_POST_STATUSES:
		_refuse(
			_(
				"This post is {0}: its time was approved with it. Cancel it and duplicate it to move it."
			).format(doc.status)
		)
	try:
		when = spa_rules.parse_local_datetime(scheduled_at)
	except ValueError as exc:
		_refuse(exc)
	problem = spa_rules.schedule_problem(when, now_datetime())
	if problem:
		_refuse(problem)
	doc.scheduled_at = when
	doc.save()
	return {"name": doc.name, "scheduled_at": doc.scheduled_at, "modified": doc.modified}


@frappe.whitelist(methods=["POST"])
def approval_queue():
	"""Posts waiting for approval, oldest first, with whether the caller may approve each."""
	_require()
	posts = frappe.get_list(
		POST,
		filters={"status": outbox.POST_PENDING_APPROVAL},
		fields=POST_LIST_FIELDS,
		order_by="modified asc",
		limit_page_length=100,
	)
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	browser = browser_request()
	rows = _decorate(posts)
	for row, post in zip(rows, posts, strict=True):
		row["approve_problems"] = workflow.approval_problems(dict(post), user, roles, browser, unchanged=True)
	return {"posts": rows}


@frappe.whitelist(methods=["POST"])
def delete_post(name):
	"""Delete a draft. The controller refuses anything that was ever queued."""
	_require()
	doc = frappe.get_doc(POST, name)
	doc.check_permission("delete")
	frappe.delete_doc(POST, doc.name)
	return {"deleted": doc.name}


@frappe.whitelist(methods=["POST"])
def list_media(search=None, asset_type=None, cleared_only=0, start=0):
	"""A page of Marketing Media Assets for the picker, newest first."""
	_require()
	filters = []
	if asset_type in spa_rules.ASSET_TYPES:
		filters.append(["asset_type", "=", asset_type])
	if cint(cleared_only):
		filters.append(["usage_rights", "=", outbox.CLEARED_FOR_SOCIAL])
	text = (search or "").strip()[:100]
	if text:
		filters.append(["title", "like", f"%{text}%"])
	rows = frappe.get_list(
		ASSET,
		filters=filters,
		fields=ASSET_FIELDS,
		order_by="modified desc",
		limit_start=max(cint(start), 0),
		limit_page_length=MEDIA_PAGE + 1,
	)
	return {"assets": [_asset_summary(row) for row in rows[:MEDIA_PAGE]], "more": len(rows) > MEDIA_PAGE}


@frappe.whitelist(methods=["POST"])
def create_asset(
	file,
	title,
	asset_type,
	usage_rights=None,
	alt_text=None,
	mime_type=None,
	width=None,
	height=None,
	duration_seconds=None,
):
	"""A Marketing Media Asset for a file the caller just uploaded.

	The app uploads with no doctype (``upload_file`` then needs no write permission on anything)
	and names the File here. Only the caller's own, still-unattached upload is accepted: anybody
	else's File, or one already attached, is somebody else's. Saving the asset attaches the File
	to it -- Frappe's own ``attach_files_to_document`` finds the unattached File by its URL --
	which is what lets the rest of the team open a private one.
	"""
	_require()
	if not frappe.has_permission(ASSET, "create"):
		frappe.throw(_("You cannot add media."), frappe.PermissionError)
	try:
		values = spa_rules.asset_input(
			{
				"title": title,
				"asset_type": asset_type,
				"usage_rights": usage_rights,
				"alt_text": alt_text,
				"mime_type": mime_type,
				"width": width,
				"height": height,
				"duration_seconds": duration_seconds,
			}
		)
	except ValueError as exc:
		_refuse(exc)
	row = frappe.db.get_value(
		"File", file, ["name", "owner", "attached_to_doctype", "file_url"], as_dict=True
	)
	if not row:
		_refuse(_("That upload was not found."))
	if row.owner != frappe.session.user:
		frappe.throw(_("That file was uploaded by somebody else."), frappe.PermissionError)
	if row.attached_to_doctype:
		_refuse(_("That file is already attached to something else."))
	doc = frappe.get_doc({"doctype": ASSET, "source": "File", "file": row.file_url, **values}).insert()
	return _asset_summary(doc.as_dict())


@frappe.whitelist(methods=["POST"])
def get_results(name):
	"""What happened to each account a post went to: job state, attempts, link and engagement."""
	_require()
	doc = frappe.get_doc(POST, name)
	doc.check_permission("read")
	jobs = frappe.get_list(
		JOB,
		filters={"social_post": doc.name},
		fields=[
			"name",
			"social_account",
			"network",
			"state",
			"available_at",
			"attempts",
			"external_post_id",
			"permalink",
			"published_at",
			"last_error",
		],
		order_by="creation asc",
		limit_page_length=0,
	)
	job_names = [job.name for job in jobs]
	attempts = {}
	metrics = {}
	if job_names:
		for row in frappe.get_all(
			ATTEMPT,
			filters={"parent": ["in", job_names], "parenttype": JOB},
			fields=["parent", "at", "attempt", "outcome", "http_status", "sent", "by_user", "message"],
			order_by="idx asc",
		):
			attempts.setdefault(row.parent, []).append(row)
		for row in frappe.get_list(
			METRIC,
			filters={"publish_job": ["in", job_names]},
			fields=[
				"publish_job",
				"metric_date",
				"impressions",
				"reach",
				"engagements",
				"clicks",
				"video_views",
			],
			order_by="metric_date asc",
			limit_page_length=0,
		):
			metrics.setdefault(row.publish_job, []).append(row)
	people = _full_names([row.by_user for rows in attempts.values() for row in rows])
	labels = {
		row.name: row.account_name or row.name
		for row in frappe.get_list(
			ACCOUNT,
			filters={"name": ["in", [job.social_account for job in jobs] or [""]]},
			fields=["name", "account_name"],
			limit_page_length=0,
		)
	}
	return {
		"name": doc.name,
		"title": doc.title,
		"status": doc.status or outbox.POST_DRAFT,
		"jobs": [
			{
				"name": job.name,
				"network": job.network,
				"account": labels.get(job.social_account) or job.social_account,
				"state": job.state,
				"available_at": job.available_at,
				"attempts": job.attempts,
				"permalink": job.permalink or "",
				"published_at": job.published_at,
				"last_error": job.last_error or "",
				"log": [
					{
						"at": row.at,
						"attempt": row.attempt,
						"outcome": row.outcome,
						"http_status": row.http_status,
						"sent": bool(row.sent),
						"by": people.get(row.by_user) if row.by_user else None,
						"message": row.message or "",
					}
					for row in attempts.get(job.name, [])
				],
				"metrics": {
					"days": [dict(row) for row in metrics.get(job.name, [])],
					"totals": spa_rules.metric_totals(metrics.get(job.name, [])),
				},
			}
			for job in jobs
		],
	}

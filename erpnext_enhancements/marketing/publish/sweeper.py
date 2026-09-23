# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The five-minute sweep that drives the outbox, and the one background job it starts.

``sweep_publish_jobs`` (cron, hooks.py) does, in order:

1. **Master switch.** ``Marketing Settings.enabled`` off -> return. Dormant means nothing runs.
2. **Reclaim.** In Progress jobs whose lease ran out: back to Pending if their request never
   left ERPNext, otherwise Unconfirmed (``outbox.after_lease_expiry``).
3. **Claim.** Due Pending jobs, oldest first, whose network may send *now*
   (``can_send``): the network's publishing switch on, its connection Connected, the account
   enabled, and a publisher installed. Anything else stays Pending, untouched. Then the rate
   limiter (``ratelimit.admit``, TASK-2026-01482): a no moves the job's ``available_at`` to
   when quota returns, or when a paused connection resumes. The claim is one
   conditional ``UPDATE ... WHERE state = 'Pending'`` followed by a read-back of the lease ID, so
   two sweeps racing for a job cannot both win.
4. **Hand each claimed job to ``run_dispatch`` on the ``long`` queue.** If a deploy flushes
   that queue before it runs, nothing was dispatched, and the lease running out returns the
   job to Pending: the flush costs half an hour, never a post.

``run_dispatch`` re-checks everything, prepares (loads the post, gets a token), records
``dispatched_at`` -- committed before the request leaves -- and only then sends.
"""

import secrets

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.marketing.core.utils import field, get_credentials, get_settings
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import gate, outbox, ratelimit
from erpnext_enhancements.marketing.publish.publishers import publisher_for

JOB = "Social Publish Job"
POST = "Social Post"
TARGET = "Social Post Target"
ACCOUNT = "Social Account"
ASSET = "Marketing Media Asset"
JOB_FIELDS = [
	"name",
	"social_post",
	"target",
	"social_account",
	"network",
	"state",
	"available_at",
	"attempts",
	"lease_id",
	"lease_expires_at",
	"dispatched_at",
	"external_post_id",
]
OPERATOR_ROLE = "System Manager"


def make_sendable(settings, creds):
	"""``sendable(job)`` bound to one read of the settings and connections."""
	account_enabled = {}

	def sendable(job):
		network = job.get("network")
		connection = P.CONNECTION_FOR.get(network)
		if not connection:
			return False
		name = job.get("social_account")
		if name not in account_enabled:
			account_enabled[name] = frappe.db.get_value(ACCOUNT, name, "enabled")
		return outbox.can_send(
			network,
			settings,
			creds.get(field(connection, "connection_status")) or "",
			bool(creds.get(field(P.CONNECTION_META, "instagram_user_id"))),
			account_enabled[name],
			publisher_for(network) is not None,
		)

	return sendable


class FrappeStore:
	"""The outbox's view of the database. ``outbox`` only ever talks to this."""

	def load_post(self, name):
		post = frappe.get_doc(POST, name)
		assets = [row.asset for row in post.media]
		rights = {}
		if assets:
			# Fresh from the asset, not the fetched copy on the row, which can be stale.
			for row in frappe.get_all(
				ASSET, filters={"name": ["in", assets]}, fields=["name", "usage_rights"]
			):
				rights[row.name] = row.usage_rights
		media = [{"asset": a, "usage_rights": rights.get(a)} for a in assets]
		targets = [
			{
				"name": row.name,
				"social_account": row.social_account,
				"variant_text": row.variant_text,
				"first_comment": row.first_comment,
			}
			for row in post.targets
		]
		return post.as_dict(), targets, media

	def accounts(self, names):
		names = [n for n in names if n]
		if not names:
			return {}
		rows = frappe.get_all(
			ACCOUNT, filters={"name": ["in", names]}, fields=["name", "enabled", "network", "connection"]
		)
		return {row.name: dict(row) for row in rows}

	def jobs_for_post(self, post):
		return [
			dict(r)
			for r in frappe.get_all(JOB, filters={"social_post": post}, fields=["name", "target", "state"])
		]

	def create_job(self, values):
		doc = frappe.get_doc({"doctype": JOB, **values})
		doc.insert(ignore_permissions=True)
		return doc.name

	def update_target(self, post, row, values):
		frappe.db.set_value(TARGET, row, values, update_modified=False)

	def set_post_status(self, post, status):
		frappe.db.set_value(POST, post, "status", status)

	def expired_leases(self, now):
		return [
			dict(r)
			for r in frappe.get_all(
				JOB,
				filters={"state": outbox.IN_PROGRESS, "lease_expires_at": ["<", now]},
				fields=JOB_FIELDS,
				limit_page_length=100,
			)
		]

	def due_jobs(self, now, limit):
		return [
			dict(r)
			for r in frappe.get_all(
				JOB,
				filters={"state": outbox.PENDING, "available_at": ["<=", now]},
				fields=JOB_FIELDS,
				order_by="available_at asc",
				limit_page_length=limit,
			)
		]

	def claim(self, name, lease_id, now, lease_until):
		frappe.db.sql(
			"""update `tabSocial Publish Job`
			set state = %(in_progress)s, lease_id = %(lease_id)s, lease_expires_at = %(until)s,
				attempts = attempts + 1, dispatched_at = null, modified = %(now)s
			where name = %(name)s and state = %(pending)s and available_at <= %(now)s""",
			{
				"in_progress": outbox.IN_PROGRESS,
				"pending": outbox.PENDING,
				"lease_id": lease_id,
				"until": lease_until,
				"now": now,
				"name": name,
			},
		)
		frappe.db.commit()
		return frappe.db.get_value(JOB, name, "lease_id") == lease_id

	def get_job(self, name):
		row = frappe.db.get_value(JOB, name, JOB_FIELDS, as_dict=True)
		return dict(row) if row else None

	def update_job(self, name, values):
		doc = frappe.get_doc(JOB, name)
		doc.update(values)
		frappe.db.savepoint("social_publish_job_update")
		try:
			doc.save(ignore_permissions=True)
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
			# A non-primary unique index raises UniqueValidationError, not DuplicateEntryError;
			# catching only one would let a duplicate through as a crash. Only this write is
			# undone: a savepoint, not a rollback of the whole sweep's transaction.
			frappe.db.rollback(save_point="social_publish_job_update")
			raise outbox.DuplicatePostId(values.get("external_post_id")) from None

	def defer(self, name, until, note):
		"""Leave a Pending job Pending, not before ``until`` (the rate limiter said not yet)."""
		frappe.db.set_value(
			JOB, name, {"available_at": until, "last_error": (note or "")[:1000]}, update_modified=False
		)

	def set_quota(self, account, remaining, when):
		frappe.db.set_value(
			ACCOUNT, account, {"quota_remaining": remaining, "quota_checked_at": when}, update_modified=False
		)

	def mark_dispatched(self, name, when):
		frappe.db.set_value(JOB, name, "dispatched_at", when, update_modified=False)
		frappe.db.commit()  # durable before the request leaves ERPNext

	def notify(self, job, message):
		post = (
			frappe.db.get_value(POST, job["social_post"], ["title", "owner", "approver"], as_dict=True) or {}
		)
		subject = _("Social post {0} on {1} {2}").format(
			post.get("title") or job["social_post"], job.get("network") or "", message
		)
		for user in {post.get("owner"), post.get("approver")} - {None, "", "Administrator", "Guest"}:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"subject": subject[:140],
					"document_type": JOB,
					"document_name": job["name"],
				}
			).insert(ignore_permissions=True)


def _lease_id(_job):
	return secrets.token_hex(16)


def sweep_publish_jobs():
	"""Cron entry (hooks.py). Returns a summary, or None while dormant."""
	settings = get_settings()
	if not cint(settings.get("enabled")):
		return None
	now = now_datetime()
	store = FrappeStore()
	moved = outbox.reclaim_expired(store, now)
	frappe.db.commit()
	claimed = []
	if gate.enabled_networks(settings):
		sendable = make_sendable(settings, get_credentials())
		limiter = ratelimit.RedisLimiter()
		claimed = outbox.claim_due(
			store, now, sendable, _lease_id, admit=lambda job: ratelimit.admit(job, limiter)
		)
		for name, lease_id in claimed:
			# Not job_name=: that is frappe.enqueue's own parameter (the RQ job label) and would
			# never reach run_dispatch.
			frappe.enqueue(
				"erpnext_enhancements.marketing.publish.sweeper.run_dispatch",
				queue="long",
				timeout=outbox.DISPATCH_TIMEOUT_SECONDS,
				job_id=f"social_publish::{name}",
				deduplicate=True,
				publish_job=name,
				lease_id=lease_id,
			)
	frappe.db.commit()
	return {"reclaimed": moved, "claimed": [name for name, _ in claimed]}


def publish_context(job):
	"""What a publisher needs, as plain dicts."""
	post = frappe.get_doc(POST, job["social_post"])
	target = next((row for row in post.targets if row.name == job["target"]), None)
	media = [frappe.get_doc(ASSET, row.asset).as_dict() for row in post.media]
	return {
		"job": dict(job),
		"post": post.as_dict(),
		"target": target.as_dict() if target else {},
		"account": frappe.get_doc(ACCOUNT, job["social_account"]).as_dict(),
		"media": media,
	}


def run_dispatch(publish_job, lease_id):
	"""The background job for one claimed Social Publish Job."""
	from erpnext_enhancements.marketing.publish import oauth as publish_oauth

	settings = get_settings()
	creds = get_credentials()
	sendable = make_sendable(settings, creds)

	def prepare(job):
		publisher = publisher_for(job["network"])
		connection = P.CONNECTION_FOR[job["network"]]
		context = publish_context(job)
		transport = publish_oauth.transport_for(connection, creds, settings=settings)
		return lambda: publisher.publish(context, transport)

	def notify_auth_failure(job, message):
		connection = P.CONNECTION_FOR.get(job["network"])
		if connection:
			publish_oauth.mark_dead(creds, connection, message)
			creds.save(ignore_permissions=True)

	state = outbox.dispatch(
		FrappeStore(), publish_job, lease_id, now_datetime, sendable, prepare, notify_auth_failure
	)
	frappe.db.commit()
	return state


@frappe.whitelist(methods=["POST"])
def resolve_job(job, outcome, external_post_id=None, permalink=None):
	"""A person's answer for an Unconfirmed or Failed job. System Manager only.

	``published`` records it (with the post's ID or link if known), ``retry`` sends it again,
	``cancel`` stops it.
	"""
	if OPERATOR_ROLE not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can resolve a publish job."), frappe.PermissionError)
	try:
		state = outbox.resolve(
			FrappeStore(), job, outcome, frappe.session.user, now_datetime(), external_post_id, permalink
		)
	except ValueError as exc:
		frappe.throw(str(exc))
	frappe.db.commit()
	return {"state": state}

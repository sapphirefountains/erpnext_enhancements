# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The nightly engagement pull-back: frappe around ``metrics.py`` and ``insights.py`` (TASK-2026-01488).

``nightly_social_metrics`` (cron, hooks.py, 03:50 -- after the 03:35 token upkeep, so the tokens
it uses were checked minutes before) keeps the ad connectors' gates, in order:

1. **Master switch.** ``Marketing Settings.enabled`` off -> return. Dormant means nothing runs.
2. **Connected only.** Only networks whose publishing connection is *Connected* are read. The
   per-network publishing switches play no part: they decide whether a post may go out, and a
   post already out keeps being measured while its network is switched off.
3. **Something to read.** No published post inside ``metrics.TRACK_DAYS`` -> return.
4. **Enqueue on ``long``** with a fixed ``job_id``, so an overlapping trigger is dropped. A
   deploy that FLUSHDBs the queue costs one night: every job's cursor stayed where it was, and
   the next run restates from it.

``pull_metrics_now`` (POST, System Manager) queues the same job on demand.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.core.utils import field, get_credentials, get_settings
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import insights, metrics

JOB = "Social Publish Job"
METRIC = "Social Post Metric"
ACCOUNT = "Social Account"
QUEUE = "long"
JOB_ID = "marketing-social-metrics"
TIMEOUT_SECONDS = 45 * 60
JOB_FIELDS = [
	"name",
	"social_post",
	"social_account",
	"network",
	"state",
	"external_post_id",
	"published_at",
	"metrics_through",
]
OPERATOR_ROLE = "System Manager"


class FrappeMetricsStore:
	"""``metrics.pull``'s view of the database."""

	def upsert(self, job, row):
		metric_date = str(row["metric_date"])
		values = {key: row.get(key) for key in metrics.FIGURES}
		name = f"SPM-{job['name']}-{metric_date}"
		if frappe.db.exists(METRIC, name):
			doc = frappe.get_doc(METRIC, name)
			doc.update(values)
			doc.save(ignore_permissions=True)
			return
		frappe.get_doc(
			{
				"doctype": METRIC,
				"publish_job": job["name"],
				"metric_date": metric_date,
				"social_post": job.get("social_post"),
				"social_account": job.get("social_account"),
				"network": job.get("network"),
				**values,
			}
		).insert(ignore_permissions=True)

	def mark_done(self, name, through, now):
		frappe.db.set_value(
			JOB,
			name,
			{"metrics_through": through, "metrics_checked_at": now, "metrics_error": None},
			update_modified=False,
		)
		frappe.db.commit()  # one post at a time: a later failure costs nothing already pulled

	def mark_failed(self, name, message, now):
		frappe.db.set_value(
			JOB, name, {"metrics_checked_at": now, "metrics_error": message[:1000]}, update_modified=False
		)
		frappe.db.commit()


def connected_networks(creds):
	"""The networks whose publishing connection is *Connected*."""
	from erpnext_enhancements.marketing.publish import oauth

	return [
		network
		for network, connection in P.CONNECTION_FOR.items()
		if (creds.get(field(connection, "connection_status")) or "") == oauth.STATUS_CONNECTED
	]


def due_jobs(networks, today):
	"""Published jobs on ``networks`` inside the tracking window, with what their reads need."""
	if not networks:
		return []
	since = get_datetime(add_days(today, -metrics.TRACK_DAYS))
	jobs = [
		dict(row)
		for row in frappe.get_all(
			JOB,
			filters=[
				["state", "=", "Published"],
				["network", "in", networks],
				["published_at", ">=", since],
				["external_post_id", "is", "set"],
			],
			fields=JOB_FIELDS,
			order_by="published_at asc",
			limit_page_length=0,
		)
	]
	if not jobs:
		return []
	accounts = {
		row.name: row.external_id
		for row in frappe.get_all(
			ACCOUNT,
			filters={"name": ["in", sorted({j["social_account"] for j in jobs})]},
			fields=["name", "external_id"],
		)
	}
	with_video = {
		row.parent
		for row in frappe.get_all(
			"Social Post Media",
			filters={"parent": ["in", sorted({j["social_post"] for j in jobs})], "asset_type": "Video"},
			fields=["parent"],
		)
	}
	for job in jobs:
		job["account_external_id"] = accounts.get(job["social_account"])
		job["has_video"] = job["social_post"] in with_video
	return jobs


def nightly_social_metrics():
	"""Cron entry (hooks.py). See the module docstring for the gates, in order."""
	if not cint(get_settings().get("enabled")):
		return None
	networks = connected_networks(get_credentials())
	if not due_jobs(networks, getdate(nowdate())):
		return None
	return enqueue()


def enqueue():
	frappe.enqueue(
		"erpnext_enhancements.marketing.publish.metrics_sync.run_metrics",
		queue=QUEUE,
		timeout=TIMEOUT_SECONDS,
		job_id=JOB_ID,
		deduplicate=True,
	)
	return JOB_ID


def run_metrics(http=None, today=None):
	"""The background job: pull every due post's figures. Returns ``metrics.pull``'s counters."""
	from erpnext_enhancements.marketing.publish import oauth

	settings = get_settings()
	creds = get_credentials()
	today = getdate(today or nowdate())
	jobs = due_jobs(connected_networks(creds), today)
	transports = {}

	def transport(network):
		connection = P.CONNECTION_FOR[network]
		if connection not in transports:
			try:
				transports[connection] = oauth.transport_for(connection, creds, http=http, settings=settings)
			except Exception as exc:  # a dead token: every post on it fails with the same reason
				transports[connection] = exc
		value = transports[connection]
		if isinstance(value, Exception):
			raise value
		return value

	def fetch(job, _start, day):
		return insights.fetch(transport(job["network"]), job, day)

	return metrics.pull(
		FrappeMetricsStore(),
		jobs,
		fetch,
		today,
		now_datetime(),
		restate_days=cint(settings.get("restate_days")) or 7,
	)


@frappe.whitelist(methods=["POST"])
def pull_metrics_now():
	"""Queue the engagement pull now. System Manager only; the master switch still applies."""
	if OPERATOR_ROLE not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can pull engagement now."), frappe.PermissionError)
	if not cint(get_settings().get("enabled")):
		frappe.throw(_("Marketing is switched off in Marketing Settings, so nothing is pulled."))
	return {"queued": enqueue()}

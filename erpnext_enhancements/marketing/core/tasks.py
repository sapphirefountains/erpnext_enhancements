# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduler entry points. Thin shims: every decision that can say "not now" happens here,
and the work happens in a background job on the ``long`` queue.

The order is the contract (TASK-2026-01476):

1. **Master switch.** ``Marketing Settings.enabled`` off -> return. Nothing runs.
2. **Self-throttle.** A run inside ``SYNC_MIN_INTERVAL_HOURS`` of the last one -> return.
   The cron fires once a night; the throttle is what makes a manual "Sync now" and the
   cron on the same evening one pull instead of two.
3. **No-op when disconnected.** Only platforms that are switched on *and* show
   ``Connected`` on Marketing Connections are queued. An *Auth Failed* platform waits for a
   human to reconnect rather than failing every night.
4. **Enqueue on ``long``** with a fixed ``job_id``, so an overlapping trigger is dropped
   rather than run twice.

A deploy FLUSHDBs the queue redis and destroys a queued job. That costs one night at most:
the cursor did not move, so the next run restates the same window.
"""

from erpnext_enhancements.marketing.core import constants as C


def _due(settings, now):
	from frappe.utils import get_datetime

	last = settings.get("last_sync_started_at")
	if not last:
		return True
	return (now - get_datetime(last)).total_seconds() >= C.SYNC_MIN_INTERVAL_HOURS * 3600


def runnable_platforms(settings=None, creds=None):
	"""Platforms switched on AND connected. Pure given its inputs' ``get``."""
	from erpnext_enhancements.marketing.core.utils import (
		get_credentials,
		get_settings,
		is_connected,
		platform_enabled,
	)

	settings = settings or get_settings()
	creds = creds or get_credentials()
	return [p for p in C.PLATFORMS if platform_enabled(p, settings) and is_connected(p, creds)]


def nightly_ad_spend_sync():
	"""Cron entry (hooks.py). See the module docstring for the gates, in order."""
	from frappe.utils import cint, now_datetime

	from erpnext_enhancements.marketing.core.utils import get_settings

	settings = get_settings()
	if not cint(settings.get("enabled")):
		return None
	now = now_datetime()
	if not _due(settings, now):
		return None
	platforms = runnable_platforms(settings)
	if not platforms:
		return None
	return enqueue_sync(platforms, now)


def enqueue_sync(platforms, now=None):
	import frappe
	from frappe.utils import now_datetime

	now = now or now_datetime()
	frappe.db.set_single_value(C.SETTINGS_DOCTYPE, "last_sync_started_at", now)
	frappe.db.set_single_value(C.SETTINGS_DOCTYPE, "last_sync_status", "Queued")
	frappe.enqueue(
		"erpnext_enhancements.marketing.core.tasks.run_sync",
		queue=C.SYNC_QUEUE,
		timeout=C.SYNC_TIMEOUT_SECONDS,
		job_id=C.SYNC_JOB_ID,
		deduplicate=True,
		platforms=list(platforms),
	)
	return list(platforms)


def run_sync(platforms):
	"""The background job: each platform in turn. One platform's failure does not stop the next."""
	import frappe

	from erpnext_enhancements.marketing.core.sync import run_platform

	outcomes = []
	for platform in platforms:
		try:
			log = run_platform(platform)
			outcomes.append(f"{platform}: {frappe.db.get_value('Marketing Sync Log', log, 'status')}")
		except Exception:
			# A bug, not a platform failure (those are recorded on the Sync Log). Plain
			# get_traceback() -- never with_context=True, which would publish frame locals,
			# and the transport's locals hold a bearer token.
			frappe.log_error(
				f"Ad-spend sync crashed on {platform}\n\n{frappe.get_traceback()}", "Marketing sync crashed"
			)
			outcomes.append(f"{platform}: crashed")
	frappe.db.set_single_value(C.SETTINGS_DOCTYPE, "last_sync_status", "; ".join(outcomes)[:140])
	frappe.db.commit()


def daily_prune():
	"""Raw payloads past retention; clicks past retention that no Lead carries."""
	from frappe.utils import cint

	from erpnext_enhancements.marketing.core.sync import prune
	from erpnext_enhancements.marketing.core.utils import get_settings

	if not cint(get_settings().get("enabled")):
		return
	prune()

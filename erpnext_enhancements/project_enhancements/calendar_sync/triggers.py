# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``doc_events`` that queue calendar sync after a change is committed.

Registered in ``hooks.py`` on Task, Sapphire Maintenance Record, Sapphire Maintenance Contract,
Planner Day Note, Planner Block and Project. Each handler only **enqueues**; the job
(``engine.run_item_job`` and friends) reads the record again in the worker, so it always writes what
was committed, not what the form held.

Things these handlers are careful about:

* **Never raise into the user's save.** Everything is inside one ``try``; a failure is logged and the
  save goes on. A calendar is a copy, and a copy must not refuse the original.
* **Nothing while the switch is off.** :func:`_enabled` reads the cached Settings; a site where the
  doctype does not exist yet (a fresh install, ERPNext's own test bootstrap, where ``doc_events`` fire
  before this app's tables exist) answers False.
* **Skipped during import, patch, install and migrate**, and in tests.
* **Coalesced.** A Planner publish (``frappe.flags.planner_publishing``) saves dozens of tasks, and a
  task can be saved several times in one request (crew sync, dependants). The first event for a record
  in a request enqueues its job, ``enqueue_after_commit=True`` with a stable ``job_id``; the rest are
  dropped. A rolled-back request enqueues nothing. A job that runs twice anyway costs one read and a
  hash compare.
* **Re-drivable.** The prod deploy flushes redis, destroying queued jobs; the nightly repair and the
  10-minute snap-back put right whatever a lost job would have done.
"""

import frappe

ITEM_JOB = "erpnext_enhancements.project_enhancements.calendar_sync.engine.run_item_job"
PROJECTIONS_JOB = "erpnext_enhancements.project_enhancements.calendar_sync.engine.run_projections_job"
PROJECT_JOB = "erpnext_enhancements.project_enhancements.calendar_sync.engine.run_project_job"
SETTINGS = "Calendar Sync Settings"
LOCAL_KEY = "ee_calendar_sync_queued"
SKIP_FLAGS = ("in_import", "in_patch", "in_install", "in_migrate")
#: A Project change that alters what its tasks' and visits' events say.
PROJECT_FIELDS = (
	"project_name",
	"status",
	"project_type",
	"custom_project_address",
	"custom_customer__lead_address",
)


def _skip():
	flags = frappe.flags
	if any(getattr(flags, flag, False) for flag in SKIP_FLAGS):
		return True
	return bool(getattr(frappe, "in_test", False) or getattr(flags, "in_test", False))


def _enabled():
	try:
		return bool(int(frappe.get_cached_doc(SETTINGS).get("enabled") or 0))
	except Exception:
		return False


def _queue(method, job_id, **kwargs):
	"""Enqueue once per request per ``job_id``, after commit."""
	queued = getattr(frappe.local, LOCAL_KEY, None)
	if queued is None:
		queued = set()
		setattr(frappe.local, LOCAL_KEY, queued)
	if job_id in queued:
		return False
	queued.add(job_id)
	frappe.enqueue(method, queue="default", enqueue_after_commit=True, job_id=job_id, **kwargs)
	return True


def queue_item(doctype, name):
	return _queue(ITEM_JOB, f"calendar_sync::{doctype}::{name}", doctype=doctype, name=name)


def queue_projections():
	return _queue(PROJECTIONS_JOB, "calendar_sync::projections")


def _log():
	try:
		frappe.log_error(title="Calendar sync: change not queued", message=frappe.get_traceback())
	except Exception:
		pass


def on_change(doc, method=None):
	"""Task, visit, day note or block saved, submitted, canceled or deleted: queue its sync. A
	visit change also re-projects its contract (a drafted visit replaces a projected one)."""
	try:
		if _skip() or not _enabled():
			return
		if doc.doctype == "Planner Block" and (getattr(doc, "source", None) or "") == "Google":
			# C4: read in from the vacations calendar, so already there. Never written back out
			# (``sources.block_item`` reads it as gone too; this just saves the job).
			return
		queue_item(doc.doctype, doc.name)
		if doc.doctype == "Sapphire Maintenance Record" and getattr(doc, "maintenance_contract", None):
			queue_projections()
	except Exception:
		_log()


def on_contract_change(doc, method=None):
	"""A maintenance contract's dates, frequencies or features changed: re-project every visit."""
	try:
		if _skip() or not _enabled():
			return
		queue_projections()
	except Exception:
		_log()


def on_project_change(doc, method=None):
	"""A Project's name, status, type or address changed: its events' titles and places did too."""
	try:
		if _skip() or not _enabled():
			return
		before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
		if before is not None and not any(
			getattr(before, field, None) != getattr(doc, field, None) for field in PROJECT_FIELDS
		):
			return
		_queue(PROJECT_JOB, f"calendar_sync::project::{doc.name}", project=doc.name)
	except Exception:
		_log()

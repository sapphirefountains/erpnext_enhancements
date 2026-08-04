# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""WP-3: get a job photo out of the technician's phone and somewhere useful.

The failure this fixes is plumbing, not willingness. Crews take photos; the
photos then sit on personal handsets across mixed iOS and Android with no path
into shared storage, and every manual step in that path is a place the process
stops. So there are no manual steps here — a photo attached to a Job Interval
ends up on the Project and in Drive without anyone doing anything.

## What this module does, and what it deliberately does not

It does three things:

1. **Copies the File onto the Project** (and the Task, if the interval names one).
2. **Tags it** with the customer, each value stream, and the capture date, so the
   marketing browse view can filter without anybody opening a job record.
3. **Makes sure a Drive-linked document owns a copy**, so the existing mirror
   picks it up.

It does **not** talk to Google Drive. That was the main design decision here and
it is worth being explicit about, because "mirror it into Drive" reads like it
should mean an API call in this file.

``google_drive/drive_sync.py`` already owns that job end to end: ``on_file_attached``
fires on every ``File`` ``after_insert``, finds the folder id on the attached
document (``SYNCED_DOCTYPES`` covers Project, Customer and Opportunity), and
enqueues ``upload_attachment_to_drive`` onto the long queue. It is idempotent —
it bails out the moment ``custom_drive_file_id`` is set, which is exactly the
"the same File must never be pushed twice" requirement — and it already handles
quota and auth failures by logging to Drive Sync Log with a replayable payload
and leaving the local copy untouched.

Re-implementing any of that here would mean a second upload path with its own
idempotency bug. So the job of this module is narrower and more useful: **make
sure the File is attached to a document that has a Drive folder.** The Project
normally has one. When it does not — no folder provisioned, or
``custom_drive_folder_missing`` set because the folder was deleted out from under
us — it falls back to attaching a copy to the Customer, whose folder is the one
``Customer.custom_drive_folder_id`` points at.

## Copies, never re-points

Each target gets its own ``File`` row pointing at the same bytes, via frappe's
``File.create_attachment_copy``. Re-pointing the original would strip the photo
off the Job Interval — the record of what the technician actually did — and lose
it entirely if a target is later deleted. Byte sharing is safe: ``File`` only
unlinks the underlying blob when no other row shares its ``content_hash``. Same
pattern, and the same reasoning, as ``crm_enhancements/fountain_move/photos.py``.

``is_private = 1`` is re-asserted on every copy rather than trusted to carry
over. These are photographs of customers' property; a public file URL is
unauthenticated and effectively permanent.

## Idempotent

Every step is keyed on something stable and checks before writing: a target that
already has a File with the same ``file_url`` is skipped, and tags are added
through frappe's own ``add_tag``, which no-ops on a duplicate. The whole function
is safe to re-run, which matters because it is enqueued from a device that
retries.
"""

import frappe
from frappe.utils import cint, getdate

#: Tag prefixes. Namespaced so a marketing user scanning the tag list can see at
#: a glance which tags this pipeline owns and which a human added by hand.
TAG_JOB_PHOTO = "job-photo"
TAG_CUSTOMER_PREFIX = "cust:"
TAG_STREAM_PREFIX = "vs:"
TAG_CAPTURED_PREFIX = "shot:"

#: Frappe caps a tag at 140 chars; keep well inside it.
TAG_MAX_LENGTH = 120


def route_job_photo(job_interval, file_name):
	"""Background job (enqueued by ``api.time_kiosk.record_job_photo``).

	Never raises into the worker: a routing failure must not lose the photo, and
	the photo is already safely on the Job Interval by the time this runs.
	"""
	try:
		if not frappe.db.exists("File", file_name):
			return
		if not frappe.db.exists("Job Interval", job_interval):
			return

		interval = frappe.get_doc("Job Interval", job_interval)
		source = frappe.get_doc("File", file_name)

		project = interval.project
		customer = _customer_of(project)

		copies = []
		for doctype, docname in _targets(interval, project, customer):
			copy = _copy_to(source, doctype, docname)
			if copy:
				copies.append(copy)

		for file_doc in [source] + copies:
			_tag(file_doc, customer, project, interval)

	except Exception:
		frappe.log_error(
			f"Job photo routing failed for File {file_name} on {job_interval}\n{frappe.get_traceback()}",
			"Job Photo Routing",
		)


def _targets(interval, project, customer):
	"""Which documents get their own copy of this photo.

	The Project first, always — it is where anybody looking for "photos of this
	job" will look, and it is normally the Drive-linked document.

	The Customer only as a **fallback**, when the Project cannot carry the photo
	into Drive. Attaching every job photo to the Customer unconditionally would
	bury the account record under hundreds of images and mirror them into the top
	of the customer's folder rather than the job's.
	"""
	targets = []
	if project:
		targets.append(("Project", project))
	if getattr(interval, "task", None):
		targets.append(("Task", interval.task))
	if customer and not _drive_reachable("Project", project):
		targets.append(("Customer", customer))
	return targets


def _drive_reachable(doctype, docname):
	"""True if ``docname`` has a Drive folder that is not flagged missing.

	``custom_drive_folder_missing`` is set by ``drive_sync.reconcile_drive_links``
	when a folder id no longer resolves — somebody deleted the folder in Drive.
	Treating that as "has a folder" would mirror the photo into a black hole.
	"""
	if not docname:
		return False
	if not frappe.db.has_column(doctype, "custom_drive_folder_id"):
		return False

	folder_id = frappe.db.get_value(doctype, docname, "custom_drive_folder_id")
	if not folder_id:
		return False

	if frappe.db.has_column(doctype, "custom_drive_folder_missing"):
		if cint(frappe.db.get_value(doctype, docname, "custom_drive_folder_missing")):
			return False
	return True


def _customer_of(project):
	if not project:
		return None
	return frappe.db.get_value("Project", project, "customer")


def _copy_to(source, doctype, docname):
	"""Attach a copy of ``source`` to ``(doctype, docname)``. Returns the new File.

	Skips silently when the target already carries a File with the same
	``file_url`` — which is what makes a retried job harmless.
	"""
	if not docname or not frappe.db.exists(doctype, docname):
		return None

	if frappe.db.exists("File", {
		"file_url": source.file_url,
		"attached_to_doctype": doctype,
		"attached_to_name": docname,
	}):
		return None

	try:
		copy = source.create_attachment_copy(doctype, docname)
	except Exception:
		frappe.log_error(
			f"Could not copy File {source.name} onto {doctype} {docname}\n{frappe.get_traceback()}",
			"Job Photo Routing",
		)
		return None

	# Re-assert privacy rather than trusting it to carry over.
	if not cint(copy.is_private):
		copy.db_set("is_private", 1, update_modified=False)

	return copy


def _tag(file_doc, customer, project, interval):
	"""Tag a File so the marketing browse view can find it without a job record.

	Tags rather than new columns: ``File`` is a core doctype used by everything on
	the site, and adding four Custom Fields to it to serve one browse view would
	touch every attachment in the system. Tags are already indexed
	(``tabTag Link``), already filterable in list view, and cost nothing to the
	files this pipeline never touches.
	"""
	from frappe.desk.doctype.tag.tag import add_tag

	tags = [TAG_JOB_PHOTO]
	if customer:
		tags.append(TAG_CUSTOMER_PREFIX + str(customer))
	for stream in _value_streams(project):
		tags.append(TAG_STREAM_PREFIX + stream)

	captured = getattr(interval, "start_time", None)
	if captured:
		tags.append(TAG_CAPTURED_PREFIX + str(getdate(captured)))

	for tag in tags:
		tag = tag[:TAG_MAX_LENGTH]
		try:
			add_tag(tag, "File", file_doc.name)
		except Exception:
			# A tag is a convenience. Losing one must never cost the photo.
			frappe.log_error(
				f"Could not tag File {file_doc.name} with {tag!r}\n{frappe.get_traceback()}",
				"Job Photo Routing",
			)


def _value_streams(project):
	"""Value stream names on the Project's ``custom_value_stream`` Table MultiSelect.

	Note the naming trap: the MASTER list is the doctype called ``Value Streams``
	(plural, 6 rows) and the CHILD TABLE is ``Value Stream`` (singular, ~1,460
	rows). They are inverted from the usual frappe convention and both are in
	daily use — see the survey notes in docs/. Reading the child rows off the
	parent document avoids having to care which is which.
	"""
	if not project:
		return []
	if not frappe.db.has_column("Project", "custom_value_stream"):
		return []
	try:
		rows = frappe.get_all(
			"Value Stream",
			filters={"parent": project, "parenttype": "Project"},
			fields=["value_stream"],
		)
	except Exception:
		return []
	return [r.value_stream for r in rows if r.value_stream]

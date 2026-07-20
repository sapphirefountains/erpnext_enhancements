# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Move the two intake photos onto the records the conversion created.

The submitter uploads a photo of the fountain and a photo of the route from the
street to where it is going. Both matter operationally — the second one decides
how many people and what equipment show up — so they must be visible from the
Lead, the Customer and the Opportunity, not just from the staging row.

**Copies, never re-points.** Attaching the original File rows to the CRM records
would strip them off the Fountain Move Request, which has to stay a faithful copy
of what was submitted, and would lose them entirely if a target record is later
deleted. So each target gets its own File row pointing at the same bytes, via
frappe's own ``File.create_attachment_copy`` — which exists for precisely this and
sets ``flags.copy_from_existing_file`` so ``before_insert`` skips re-saving and
re-hashing a blob that is already on disk.

That byte sharing is safe by design: ``File`` only unlinks the underlying file
when no *other* row shares its ``content_hash``.

**Private, always.** ``is_private = 1`` is re-asserted on every copy rather than
trusted to carry over. These are photographs of the inside of someone's property;
a public file URL is unauthenticated and effectively permanent.
"""

import frappe
from frappe.utils import cint

from erpnext_enhancements.google_drive import drive_sync

#: Fields on the request holding the two photos.
PHOTO_FIELDS = ("fountain_photo", "path_photo")

#: Give the Drive folder-provisioning jobs this many tries to finish first.
MAX_DRIVE_ATTEMPTS = 3


def fan_out(req, targets):
	"""Copy both photos onto each ``(doctype, docname)`` in ``targets``.

	Idempotent: a target that already has a File with the same ``file_url`` is
	skipped, so a retried conversion does not pile up duplicates.
	"""
	sources = _source_files(req)
	if not sources:
		return 0

	created = 0
	for doctype, docname in targets:
		if not docname:
			continue
		for source in sources:
			if _copy_to(source, doctype, docname):
				created += 1

	if created:
		frappe.db.commit()
	return created


def _source_files(req):
	"""The File rows behind the request's two photo fields."""
	urls = [req.get(field) for field in PHOTO_FIELDS]
	urls = [url for url in urls if url]
	if not urls:
		return []
	return frappe.get_all(
		"File",
		filters={
			"file_url": ["in", urls],
			"attached_to_doctype": "Fountain Move Request",
			"attached_to_name": req.name,
		},
		pluck="name",
	)


def _copy_to(source_name, doctype, docname):
	"""Attach one photo to one record. Returns True when a row was created."""
	source = frappe.get_doc("File", source_name)

	existing = frappe.db.exists(
		"File",
		{
			"file_url": source.file_url,
			"attached_to_doctype": doctype,
			"attached_to_name": docname,
		},
	)
	if existing:
		return False

	try:
		copy = source.create_attachment_copy(
			attached_to_doctype=doctype,
			attached_to_name=docname,
			ignore_permissions=True,
		)
		# Re-assert rather than trust the copy: a public photo of the inside of
		# someone's property is not recoverable once the URL is out.
		if not cint(copy.is_private):
			copy.db_set("is_private", 1, update_modified=False)
		return True
	except Exception:
		# One target failing must not abort the conversion — the CRM records are
		# far more valuable than the attachment.
		frappe.log_error(
			frappe.get_traceback(),
			f"Fountain Move: photo copy to {doctype} {docname}",
			defer_insert=True,
		)
		return False


def mirror_to_drive(docname, attempt=1):
	"""Push the copied photos into each record's Google Drive folder.

	Separate from :func:`fan_out` because of an ordering problem:
	``drive_sync.on_file_attached`` (the ``File.after_insert`` hook) bails out
	when the target has no ``custom_drive_folder_id``, and the jobs that create
	those folders are themselves queued ``after_commit`` by the Customer and
	Opportunity ``after_insert`` hooks. At the moment we attach the photos, the
	folders usually do not exist yet.

	So this re-checks and re-queues itself a few times. ``Lead`` is deliberately
	absent from ``drive_sync.SYNCED_DOCTYPES`` upstream, so Lead attachments are
	never mirrored — noted rather than fixed, since changing that set affects
	every Lead attachment on the site, not just ours.
	"""
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		req = frappe.get_doc("Fountain Move Request", docname)
		if cint(req.photos_mirrored):
			return

		pending = []
		for doctype, field in drive_sync.SYNCED_DOCTYPES.items():
			target = _target_for(req, doctype)
			if not target:
				continue
			if not frappe.db.get_value(doctype, target, field):
				pending.append((doctype, target))
				continue
			_upload_for(doctype, target)

		if pending and attempt < MAX_DRIVE_ATTEMPTS:
			frappe.enqueue(
				"erpnext_enhancements.crm_enhancements.fountain_move.photos.mirror_to_drive",
				queue="long",
				enqueue_after_commit=True,
				job_id=f"fmr-drive-{docname}-{attempt + 1}",
				deduplicate=True,
				docname=docname,
				attempt=attempt + 1,
			)
			return

		req.db_set("photos_mirrored", 1, update_modified=False)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: Drive mirror", defer_insert=True)
	finally:
		frappe.set_user(previous_user)


def _target_for(req, doctype):
	return {
		"Customer": req.created_customer,
		"Opportunity": req.created_opportunity,
		"Project": None,
	}.get(doctype)


def _upload_for(doctype, docname):
	"""Queue the Drive upload for every not-yet-mirrored File on a record."""
	filters = {"attached_to_doctype": doctype, "attached_to_name": docname}
	# custom_drive_file_id is a Custom Field — absent until fixtures have synced,
	# and a filter on a missing column raises rather than returning nothing.
	if frappe.db.has_column("File", "custom_drive_file_id"):
		filters["custom_drive_file_id"] = ["in", (None, "")]
	files = frappe.get_all("File", filters=filters, pluck="name")
	for file_name in files:
		frappe.enqueue(
			"erpnext_enhancements.google_drive.drive_sync.upload_attachment_to_drive",
			queue="long",
			enqueue_after_commit=True,
			file_docname=file_name,
		)


def sweep_unmirrored_photos():
	"""Daily backstop for requests whose Drive folders showed up late.

	``mirror_to_drive`` gives up after a few attempts so a Drive outage cannot
	spin the queue forever. This catches whatever it left behind.
	"""
	stale = frappe.get_all(
		"Fountain Move Request",
		filters={"status": "Converted", "photos_mirrored": 0},
		pluck="name",
		limit=50,
	)
	for docname in stale:
		mirror_to_drive(docname, attempt=MAX_DRIVE_ATTEMPTS)

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt
"""The Time Kiosk's uploaded files: when one may be made private, and relinking the photo rows
an old page left Pending (v1.751.1).

## When a public File may be made private

Making a File private in frappe 16.53.0 (``File.handle_is_private_changed``) moves the bytes from
``public/files`` to ``private/files`` and repoints **every** File row with the same
``content_hash`` (``update_existing_file_docs``). Two things make that dangerous for a kiosk file:

* ``File.save_file`` reuses an existing File's URL when an upload has the same bytes and the same
  ``is_private``. A public kiosk upload of a picture that is also a public website asset (the logo,
  an Item image, a Web Page image) shares that asset's URL and hash.
* ``handler.upload_file`` accepts a bare ``file_url``, and ``validate_private_file_access`` guards
  only private URLs, so anybody can make a row of their own that points at any public file.

Either way, making the kiosk row private would take the asset off the public site, while Website
Settings, the Item and any HTML still point at ``/files/…``. So ``why_not_movable`` refuses unless
every other row sharing the bytes or the URL is one of the uploader's own kiosk rows: attached to a
Job Interval, Project or Task (the interval it was uploaded to, and the copies ``link_attachment``
makes), owned by the same user, at the same URL. ``api.time_kiosk`` applies it before its two
privacy flips, and ``patches/privatize_kiosk_attachment_files`` before each save.

## Relinking photo rows

Until v1.751.1 the kiosk registered an "Add files" image with the file's display name instead of
its docname, so ``_attach_photo_file`` never found the File and the ``Job Interval Photo`` row
stayed ``Pending``. A device keeps its cached ``app.js`` for at least one session after a deploy
(``kiosk-sw.js`` serves it cache-first), so such rows keep appearing for a while after the fix
ships. ``link_stuck_photo_rows`` links them. The patch calls it, and it is safe to call again::

	bench --site <site> execute erpnext_enhancements.workforce.kiosk_files.link_stuck_photo_rows

The kiosk wrote the row less than a second after the upload, in the same chain of calls (upload,
``link_attachment``, ``record_job_photo``). So the match is an image File on the same interval that
is no row's ``file_doc`` yet and was created at most ``LINK_WINDOW`` before the row's
``captured_on``, and a row is linked only when that pairing is one to one. A linked row gets
``file_doc``, ``image`` and ``Uploaded``, the interval's ``photos_pending_upload`` is recomputed,
and the photo is routed (``photo_routing.route_job_photo``, called directly: it is synchronous and
idempotent, it never raises, and a deploy flushes the job queue). Each interval runs in its own
savepoint and is committed on its own. Nothing is linked to a file that is still public.
"""

import os
from collections import Counter
from datetime import datetime, timedelta

import frappe

#: Where a kiosk file's own rows live: the interval it was uploaded to, and the Project and Task
#: copies ``link_attachment`` makes.
KIOSK_FILE_DOCTYPES = ("Job Interval", "Project", "Task")
#: The kiosk's own test for an image (``countAttachedImageAsJobPhoto`` in ``public/js/kiosk/app.js``).
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp")
#: The longest a photo row can have been written after the upload it registers. On production on
#: 2026-10-27 the three gaps were 0.5 to 0.9 seconds.
LINK_WINDOW = timedelta(seconds=10)
#: ``photo_gate.UNSETTLED_STATES``; a blank status counts as Pending there too.
UNSETTLED = ("", "Pending", "Failed")
SAVEPOINT = "kiosk_files_relink"


# ---------------------------------------------------------------------------
# Making a file private
# ---------------------------------------------------------------------------


def rows_sharing_bytes(name, content_hash, file_url):
	"""Every other File row with the same ``content_hash`` or, byte for byte, the same URL.

	``BINARY``: MariaDB's collation would match ``image.jpg`` to ``Image.jpg``.
	"""
	return frappe.db.sql(
		"""select name, owner, file_url, attached_to_doctype from `tabFile`
		where name != %s
			and ((ifnull(content_hash, '') != '' and content_hash = %s) or binary file_url = binary %s)""",
		(name, content_hash or "", file_url or ""),
		as_dict=True,
	)


def why_not_movable(file_row, others):
	"""Why ``file_row`` must stay where it is, or ``None`` when it may be made private. Pure.

	``others`` are the rows ``rows_sharing_bytes`` returns for it.
	"""
	for other in others:
		if (other.get("file_url") or "") != (file_row.get("file_url") or ""):
			return f"File {other.get('name')} holds the same contents under another URL"
		if other.get("attached_to_doctype") not in KIOSK_FILE_DOCTYPES:
			return f"File {other.get('name')} uses it outside the kiosk"
		if other.get("owner") != file_row.get("owner"):
			return f"File {other.get('name')} belongs to somebody else"
	return None


# ---------------------------------------------------------------------------
# Relinking photo rows (the pairing rule is pure, so the tests run it without a site)
# ---------------------------------------------------------------------------


def is_image(name):
	return os.path.splitext(str(name or "").lower())[1] in IMAGE_EXTENSIONS


def match_rows_to_files(rows, files, window=LINK_WINDOW):
	"""Pair stuck photo rows with the image Files they were registered for, one to one.

	A File is a candidate for a row when it is an image and was created no later than the row's
	``captured_on`` and no more than ``window`` before it. A pair is returned only when the row
	has exactly one candidate and no other row claims that File. Returns ``[(row, file), ...]``
	in row order.
	"""
	candidates = {}
	for row in rows:
		at = _dt(row.get("captured_on"))
		hits = []
		if at is not None:
			for file in files:
				made = _dt(file.get("creation"))
				if made is None or not is_image(file.get("file_name") or file.get("file_url")):
					continue
				if timedelta(0) <= at - made <= window:
					hits.append(file)
		candidates[row["name"]] = hits
	claims = Counter(file["name"] for hits in candidates.values() for file in hits)
	return [
		(row, candidates[row["name"]][0])
		for row in rows
		if len(candidates[row["name"]]) == 1 and claims[candidates[row["name"]][0]["name"]] == 1
	]


def link_stuck_photo_rows(problems=None, route=True):
	"""Link every Pending photo row the old kiosk page left without its file. Safe to run again.

	Returns ``{"linked": n, "problems": [...]}``. ``problems`` may be a list to append to.
	"""
	problems = [] if problems is None else problems
	if not frappe.db.table_exists("Job Interval Photo"):
		return {"linked": 0, "problems": problems}
	rows = frappe.db.sql(
		"""select name, parent, captured_on from `tabJob Interval Photo`
		where parenttype = 'Job Interval' and upload_status = 'Pending' and ifnull(file_doc, '') = ''
		order by parent, idx""",
		as_dict=True,
	)
	linked = 0
	for parent in sorted({row.parent for row in rows if row.parent}):
		files = frappe.db.sql(
			"""select f.name, f.file_name, f.file_url, f.is_private, f.creation from `tabFile` f
			where f.attached_to_doctype = 'Job Interval' and f.attached_to_name = %s
				and ifnull(f.is_folder, 0) = 0
				and not exists (select 1 from `tabJob Interval Photo` p where p.file_doc = f.name)
			order by f.creation, f.name""",
			(parent,),
			as_dict=True,
		)
		pairs = match_rows_to_files([row for row in rows if row.parent == parent], files)
		if pairs:
			linked += _link_interval(parent, pairs, problems, route)
	return {"linked": linked, "problems": problems}


def _link_interval(parent, pairs, problems, route):
	"""Link one interval's pairs in a savepoint and commit. Returns how many rows it linked."""
	done = 0
	try:
		frappe.db.savepoint(SAVEPOINT)
		for row, file in pairs:
			if not int(file.is_private or 0):
				problems.append(f"{parent}: photo row {row.name} not linked; its file is still public.")
				continue
			frappe.db.set_value(
				"Job Interval Photo",
				row.name,
				{"file_doc": file.name, "image": file.file_url, "upload_status": "Uploaded"},
				update_modified=False,
			)
			done += 1
			if route:
				# Never raises: route_job_photo logs its own failures.
				from erpnext_enhancements.workforce.photo_routing import route_job_photo

				route_job_photo(job_interval=parent, file_name=file.name)
		if done:
			_refresh_pending_flag(parent)
	except Exception as exc:
		try:
			frappe.db.rollback(save_point=SAVEPOINT)
		except Exception:
			pass
		problems.append(f"{parent}: photo rows not linked: {type(exc).__name__}: {exc}")
		done = 0
	try:
		frappe.db.commit()
	except Exception:
		pass
	return done


def _refresh_pending_flag(parent):
	"""Recompute ``photos_pending_upload`` the way ``photo_gate.has_unsettled_uploads`` does."""
	if not frappe.db.has_column("Job Interval", "photos_pending_upload"):
		return
	unsettled = frappe.db.sql(
		"""select count(*) from `tabJob Interval Photo`
		where parent = %s and parenttype = 'Job Interval' and ifnull(upload_status, '') in %s""",
		(parent, UNSETTLED),
	)[0][0]
	frappe.db.set_value(
		"Job Interval", parent, "photos_pending_upload", 1 if unsettled else 0, update_modified=False
	)
	frappe.clear_document_cache("Job Interval", parent)


def _dt(value):
	if isinstance(value, datetime):
		return value
	if not value:
		return None
	try:
		return datetime.fromisoformat(str(value))
	except ValueError:
		return None

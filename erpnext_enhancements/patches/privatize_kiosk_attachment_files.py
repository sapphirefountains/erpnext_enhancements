"""Make the Time Kiosk's "Add files" uploads private, and link the photo rows they left Pending (v1.750.1).

Until v1.750.1 the kiosk's **Add files** button uploaded with ``is_private=0``, so every file it
took landed under ``/files/``, where anybody holding the URL could download it without signing
in. ``api.time_kiosk.link_attachment`` then copied each one onto the job's Project and Task, public
too. And the photo row the same button registers was handed the file's display name instead of
its docname, so ``_attach_photo_file`` never found the File and the row stayed ``Pending``.
v1.750.1 fixes all three for new uploads. This patch fixes what they already stored.

**What it changes.**

1. **Every public File attached to a Job Interval goes private,** with one ``File.save`` per
   stored file. In frappe 16.53.0, ``File.handle_is_private_changed`` moves the bytes from
   ``public/files`` to ``private/files``, and ``update_existing_file_docs`` repoints every File
   row that shares the ``content_hash``. That is how the Project and Task copies go private with
   it. A ``Job Interval Photo.image`` that held the old URL is repointed too.
   On production on 2026-10-27 this was 6 files (5 PNG screenshots and 1 JPEG job photo) plus
   6 Project copies and 4 Task copies: 16 File rows.
2. **A Pending photo row with no file is linked to the image it was registered for.** It gets
   ``file_doc``, ``image`` and ``Uploaded``, the three values ``_attach_photo_file`` would have
   written. The kiosk wrote the row less than a second after the upload, in the same chain of
   calls. So the match is an image File on the same interval that is no row's ``file_doc`` yet
   and was created at most ``LINK_WINDOW`` before the row's ``captured_on``. A row is linked
   only when that pairing is one to one; anything ambiguous is left alone and reported. Then
   the interval's ``photos_pending_upload`` is recomputed.
   On production this was 3 rows on three intervals. Two more Pending rows, on a fourth
   interval, are camera captures whose bytes never arrived. No file exists for them, so they
   stay Pending, which is the truth.

**A name already taken in ``private/files``.** Frappe refuses the move with ``FileExistsError``
when ``private/files`` already holds a file of that name. Production has both ``image.jpg`` and
``Image.jpg`` there, and iOS names every capture ``image.jpg``. In that case the file moves under
a free name in the form frappe's own uploads use (``get_file_name``: the stem, the last six
characters of the ``content_hash``, then the extension), and the File rows sharing its hash are
repointed in SQL, as ``update_existing_file_docs`` would. The move is the last step, so if it
fails there is nothing to undo.

**What it never changes.**

- A stored file with the same contents under another URL. Frappe's sibling update would repoint
  it, so a group whose rows do not all share one URL is skipped and reported. URLs are compared
  byte for byte, because MariaDB's collation matches ``image.jpg`` to ``Image.jpg``.
- A file with no ``content_hash``, or one missing from disk. Each is skipped and reported.
- The attachment lines already in a document's timeline. They keep the old URL, as history.

No routing job is enqueued either: the Project and Task copies exist already, and a deploy
flushes the queue.

**Savepoints and the disk.** Each file runs in its own savepoint. Rolling back to a savepoint
undoes the database only. In 16.53.0 frappe runs its file-moving rollback watchers on a full
rollback alone (``Database.rollback``). So when a save fails after frappe has moved the bytes,
the patch moves them back itself. It also commits after every file. That makes each move durable
together with its rows, and it clears the rollback watcher that would otherwise try the move
back a second time.

The counts before and after are printed. The patch never raises, because a patch that raises
aborts ``bench migrate``, which here is the deploy. It is safe to run twice: the second run
finds no public File on a Job Interval and no row it can pair.
"""

import os
import shutil
from collections import Counter
from datetime import datetime, timedelta

import frappe

LOG = "privatize_kiosk_attachment_files"
SAVEPOINT = "privatize_kiosk_attachment_file"
#: The kiosk's own test for an image (``countAttachedImageAsJobPhoto`` in ``public/js/kiosk/app.js``).
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp")
#: The longest a photo row can have been written after the upload it registers. The kiosk calls
#: upload_file, then link_attachment, then record_job_photo; on production the gaps were all
#: under a second.
LINK_WINDOW = timedelta(minutes=2)
#: ``photo_gate.UNSETTLED_STATES``; a blank status counts as Pending there too.
UNSETTLED = ("", "Pending", "Failed")


def execute():
	try:
		privatize_kiosk_attachment_files()
	except Exception:
		try:
			frappe.log_error(title="Kiosk attachment privacy patch failed", message=frappe.get_traceback())
		except Exception:
			pass
		print(f"{LOG}: aborted early; see the Error Log.")


def privatize_kiosk_attachment_files():
	"""Returns ``{"before", "after", "made_private", "renamed", "linked", "problems"}``."""
	before = _counts()
	print(f"{LOG}: before: {_describe(before)}.")
	made_private, renamed, problems = 0, 0, []
	for name in _public_interval_files():
		outcome = _privatize(name, problems)
		if outcome:
			made_private += 1
			renamed += outcome == "renamed"
	linked = _link_stuck_rows(problems)
	after = _counts()
	print(
		f"{LOG}: after: {_describe(after)}. Made {made_private} stored files private "
		f"({renamed} under a new name), linked {linked} photo rows, {len(problems)} problems."
	)
	if problems:
		for line in problems:
			print(f"{LOG}: {line}")
		try:
			frappe.log_error(
				title="Kiosk attachment privacy patch skipped some files", message="\n".join(problems)
			)
		except Exception:
			pass
	return {
		"before": before,
		"after": after,
		"made_private": made_private,
		"renamed": renamed,
		"linked": linked,
		"problems": problems,
	}


# ---------------------------------------------------------------------------
# Rules (pure, so the tests can run them without a site)
# ---------------------------------------------------------------------------


def is_image(name):
	return os.path.splitext(str(name or "").lower())[1] in IMAGE_EXTENSIONS


def free_private_name(stored_name, content_hash, exists):
	"""A name for ``stored_name`` that ``exists`` says is free in ``private/files``.

	Frappe's own form first (``get_file_name``: stem, the last six characters of the hash, then
	the extension, no separator), then numbered after it. ``None`` if twenty tries are all taken.
	"""
	stem, ext = os.path.splitext(stored_name)
	suffix = (content_hash or "")[-6:]
	for candidate in [f"{stem}{suffix}{ext}"] + [f"{stem}{suffix}-{n}{ext}" for n in range(1, 20)]:
		if not exists(candidate):
			return candidate
	return None


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


def _dt(value):
	if isinstance(value, datetime):
		return value
	if not value:
		return None
	try:
		return datetime.fromisoformat(str(value))
	except ValueError:
		return None


# ---------------------------------------------------------------------------
# Step 1: the files
# ---------------------------------------------------------------------------


def _public_interval_files():
	return frappe.db.sql_list(
		"""select name from `tabFile`
		where attached_to_doctype = 'Job Interval' and is_private = 0 and ifnull(is_folder, 0) = 0
			and file_url like %s
		order by creation, name""",
		("/files/%",),
	)


def _privatize(name, problems):
	"""Make one stored file private. Returns ``"moved"``, ``"renamed"`` or ``None``."""
	row = frappe.db.get_value("File", name, ["name", "file_url", "is_private", "content_hash"], as_dict=True)
	if not row or int(row.is_private or 0):
		# A File sharing its contents was saved first, and frappe repointed this one with it.
		return None
	old_url = row.file_url or ""
	stored_name = old_url.split("/")[-1]
	if not row.content_hash:
		problems.append(f"{name}: no content_hash, so its copies cannot be found; left public.")
		return None
	public_path = frappe.get_site_path("public", "files", stored_name)
	private_path = frappe.get_site_path("private", "files", stored_name)
	if not os.path.exists(public_path):
		problems.append(f"{name}: {old_url} is not on disk; left as it is.")
		return None
	strangers = _rows_sharing_contents_elsewhere(row.content_hash, old_url)
	if strangers:
		problems.append(
			f"{name}: {len(strangers)} File rows hold the same contents under another URL; left public."
		)
		return None

	private_existed = os.path.exists(private_path)
	outcome = None
	try:
		frappe.db.savepoint(SAVEPOINT)
		doc = frappe.get_doc("File", name)
		doc.is_private = 1
		doc.save(ignore_permissions=True)
		_repoint_photo_images(old_url, doc.file_url)
		outcome = "moved"
	except FileExistsError:
		_rollback_file()
		outcome = _privatize_under_free_name(row, public_path, problems)
	except Exception as exc:
		_rollback_file()
		if not private_existed:
			_move_back(private_path, public_path, name, problems)
		problems.append(f"{name}: {type(exc).__name__}: {exc}")
	_commit()
	return outcome


def _privatize_under_free_name(row, public_path, problems):
	"""The ``FileExistsError`` case: move under a free name and repoint the rows in SQL."""
	old_url = row.file_url
	try:
		new_name = free_private_name(
			old_url.split("/")[-1],
			row.content_hash,
			lambda candidate: os.path.exists(frappe.get_site_path("private", "files", candidate)),
		)
		if not new_name:
			problems.append(f"{row.name}: no free name in private/files; left public.")
			return None
		new_url = f"/private/files/{new_name}"
		frappe.db.savepoint(SAVEPOINT)
		frappe.db.sql(
			"""update `tabFile` set file_url = %s, is_private = 1
			where content_hash = %s and binary file_url = binary %s""",
			(new_url, row.content_hash, old_url),
		)
		_repoint_photo_images(old_url, new_url)
		# Last, so that a failure here has only the database to roll back.
		shutil.move(public_path, frappe.get_site_path("private", "files", new_name))
	except Exception as exc:
		_rollback_file()
		problems.append(f"{row.name}: could not move it under a new name: {type(exc).__name__}: {exc}")
		return None
	return "renamed"


def _rows_sharing_contents_elsewhere(content_hash, file_url):
	return frappe.db.sql_list(
		"""select name from `tabFile`
		where content_hash = %s and (file_url is null or binary file_url != binary %s)""",
		(content_hash, file_url),
	)


def _repoint_photo_images(old_url, new_url):
	if old_url == new_url or not frappe.db.table_exists("Job Interval Photo"):
		return
	frappe.db.sql(
		"update `tabJob Interval Photo` set image = %s where binary image = binary %s", (new_url, old_url)
	)


def _move_back(private_path, public_path, name, problems):
	"""Undo frappe's move after a savepoint rollback, which never undoes it on its own."""
	try:
		if os.path.exists(private_path) and not os.path.exists(public_path):
			shutil.move(private_path, public_path)
	except Exception as exc:
		problems.append(f"{name}: the save failed and moving the file back failed too: {exc}")


# ---------------------------------------------------------------------------
# Step 2: the photo rows
# ---------------------------------------------------------------------------


def _link_stuck_rows(problems):
	if not frappe.db.table_exists("Job Interval Photo"):
		return 0
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
		if not pairs:
			continue
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
			if done:
				_refresh_pending_flag(parent)
		except Exception as exc:
			_rollback_file()
			problems.append(f"{parent}: photo rows not linked: {type(exc).__name__}: {exc}")
			done = 0
		linked += done
		_commit()
	return linked


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


# ---------------------------------------------------------------------------
# Counts and transaction helpers
# ---------------------------------------------------------------------------


def _counts():
	public_files = frappe.db.sql(
		"""select count(*) from `tabFile`
		where attached_to_doctype = 'Job Interval' and is_private = 0 and ifnull(is_folder, 0) = 0"""
	)[0][0]
	public_copies = frappe.db.sql(
		"""select count(*) from `tabFile` f
		where f.is_private = 0 and ifnull(f.is_folder, 0) = 0
			and ifnull(f.attached_to_doctype, '') != 'Job Interval'
			and f.content_hash in (
				select content_hash from `tabFile`
				where attached_to_doctype = 'Job Interval' and ifnull(content_hash, '') != ''
			)"""
	)[0][0]
	pending_rows = 0
	if frappe.db.table_exists("Job Interval Photo"):
		pending_rows = frappe.db.sql(
			"""select count(*) from `tabJob Interval Photo`
			where parenttype = 'Job Interval' and upload_status = 'Pending' and ifnull(file_doc, '') = ''"""
		)[0][0]
	return {"public_files": public_files, "public_copies": public_copies, "pending_rows": pending_rows}


def _describe(counts):
	return (
		f"{counts['public_files']} public Files on Job Intervals, {counts['public_copies']} public copies "
		f"of them elsewhere, {counts['pending_rows']} Pending photo rows with no file"
	)


def _rollback_file():
	try:
		frappe.db.rollback(save_point=SAVEPOINT)
	except Exception:
		pass
	try:
		frappe.clear_messages()
	except Exception:
		pass


def _commit():
	try:
		frappe.db.commit()
	except Exception:
		pass

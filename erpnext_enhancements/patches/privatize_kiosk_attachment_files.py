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
2. **A Pending photo row with no file is linked to the image it was registered for, and
   routed.** That is ``workforce.kiosk_files.link_stuck_photo_rows``, which is safe to run again
   after the deploy for rows a device's cached page goes on writing; its docstring has the
   pairing rule and the command. On production this was 3 rows on three intervals. Two more
   Pending rows, on a fourth interval, are camera captures whose bytes never arrived. No file
   exists for them, so they stay Pending, which is the truth.

**A name already taken in ``private/files``.** Frappe refuses the move with ``FileExistsError``
when ``private/files`` already holds a file of that name. Production has both ``image.jpg`` and
``Image.jpg`` there, and iOS names every capture ``image.jpg``. In that case the file moves under
a free name in the form frappe's own uploads use (``get_file_name``: the stem, the last six
characters of the ``content_hash``, then the extension), free both on disk and among File rows,
and the File rows sharing its hash are repointed in SQL, as ``update_existing_file_docs`` would,
and dropped from the document cache. The move is the last step, so if it fails there is nothing
to undo.

**What it never changes.**

- A file whose bytes are used outside the kiosk (``kiosk_files.why_not_movable``). Frappe's upload
  reuses an existing File's URL for identical bytes, so a public kiosk upload of a picture that is
  also a website asset (the logo, an Item image) shares that asset's URL and hash, and saving the
  kiosk row would move the asset off the public site. So a group is skipped and reported unless
  every row sharing its hash or its URL is attached to a Job Interval, Project or Task, belongs to
  the Job Interval File's owner and has the same URL. URLs are compared byte for byte, because
  MariaDB's collation matches ``image.jpg`` to ``Image.jpg``.
- A file with no ``content_hash``, or one missing from disk. Each is skipped and reported.
- The attachment lines already in a document's timeline. They keep the old URL, as history.

**Savepoints and the disk.** Each file runs in its own savepoint. Rolling back to a savepoint
undoes the database only. In 16.53.0 frappe runs its file-moving rollback watchers on a full
rollback alone (``Database.rollback``). So when a save fails after frappe has moved the bytes,
the patch moves them back itself. It also commits after every file. That makes each move durable
together with its rows, and it clears the rollback watcher that would otherwise try the move
back a second time.

The counts before and after are printed; "public Files" counts exactly the selection. The patch
never raises, because a patch that raises aborts ``bench migrate``, which here is the deploy. It
is safe to run twice: the second run finds no public File on a Job Interval and no row it can
pair.
"""

import os
import shutil

import frappe

from erpnext_enhancements.workforce import kiosk_files

LOG = "privatize_kiosk_attachment_files"
SAVEPOINT = "privatize_kiosk_attachment_file"
#: The selection, shared with ``_counts`` so the counts describe exactly what the patch acts on.
PUBLIC_INTERVAL_FILES = """from `tabFile`
	where attached_to_doctype = 'Job Interval' and is_private = 0 and ifnull(is_folder, 0) = 0
		and file_url like %s"""


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
	linked = kiosk_files.link_stuck_photo_rows(problems)["linked"]
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


def free_private_name(stored_name, content_hash, taken):
	"""A name for ``stored_name`` that ``taken`` says is free in ``private/files``. Pure.

	Frappe's own form first (``get_file_name``: stem, the last six characters of the hash, then
	the extension, no separator), then numbered after it. ``None`` if twenty tries are all taken.
	"""
	stem, ext = os.path.splitext(stored_name)
	suffix = (content_hash or "")[-6:]
	for candidate in [f"{stem}{suffix}{ext}"] + [f"{stem}{suffix}-{n}{ext}" for n in range(1, 20)]:
		if not taken(candidate):
			return candidate
	return None


# ---------------------------------------------------------------------------
# Step 1: the files
# ---------------------------------------------------------------------------


def _public_interval_files():
	return frappe.db.sql_list(f"select name {PUBLIC_INTERVAL_FILES} order by creation, name", ("/files/%",))


def _privatize(name, problems):
	"""Make one stored file private. Returns ``"moved"``, ``"renamed"`` or ``None``."""
	row = frappe.db.get_value(
		"File", name, ["name", "owner", "file_url", "is_private", "content_hash"], as_dict=True
	)
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
	reason = kiosk_files.why_not_movable(row, kiosk_files.rows_sharing_bytes(name, row.content_hash, old_url))
	if reason:
		problems.append(f"{name}: {reason}; left public.")
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
		new_name = free_private_name(old_url.split("/")[-1], row.content_hash, _private_name_taken)
		if not new_name:
			problems.append(f"{row.name}: no free name in private/files; left public.")
			return None
		new_url = f"/private/files/{new_name}"
		frappe.db.savepoint(SAVEPOINT)
		group = frappe.db.sql_list(
			"select name from `tabFile` where content_hash = %s and binary file_url = binary %s",
			(row.content_hash, old_url),
		)
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
	for name in group:
		try:
			frappe.clear_document_cache("File", name)
		except Exception:
			pass
	return "renamed"


def _private_name_taken(candidate):
	"""Taken on disk, or by a File row already pointing at that private URL."""
	if os.path.exists(frappe.get_site_path("private", "files", candidate)):
		return True
	return bool(
		frappe.db.sql_list(
			"select name from `tabFile` where binary file_url = binary %s limit 1",
			(f"/private/files/{candidate}",),
		)
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
# Counts and transaction helpers
# ---------------------------------------------------------------------------


def _counts():
	public_files = frappe.db.sql(f"select count(*) {PUBLIC_INTERVAL_FILES}", ("/files/%",))[0][0]
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

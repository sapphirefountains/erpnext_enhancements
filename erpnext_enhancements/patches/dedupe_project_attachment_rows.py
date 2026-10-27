"""Remove the extra copies of a file a Project's attachment list piled up (v1.749.1).

Creating a Project from an Opportunity copied each attachment twice: once from the Project
``on_update`` hook ``sync_attachments_from_opportunity`` inside the creation's insert, and again
from the creation job's own loop in ``crm_enhancements/api.py``. A file the Opportunity held twice
came over four times. ``utils/attachment_copies.files_to_copy`` stops new copies; this removes the
ones already stored. Nik approved removing them on 2026-10-27: "keep one of each, never delete the
actual files".

**What it changes.** For each Project with more than one File row pointing at the same stored file
(same ``file_url``), the oldest row is kept (``creation``, then ``name``) and the others are deleted.
Folders and rows with no URL are never touched, and two different files that only share a name both
stay. On production on 2026-10-27: 71 extra rows across 20 Projects. 67 came from the double copy.
The other 4, on three Projects, are one file uploaded to the Project twice, which Frappe stores once
and points both rows at; they are the same stored file too, so they go as well. Their kept row is
then the file's only row, so deleting it later from the sidebar removes the file, as it would for
any attachment held once. Every kept row that came from an Opportunity still shares its file with
the Opportunity's row.

**What it never changes.** The stored file. The rows are deleted with ``frappe.db.delete``, a plain
``DELETE`` on ``tabFile``, so ``File.on_trash`` and its file removal never run. Every kept row, and
the Opportunity's own row, still points at the same file. No document is saved, no Version is
written, and the attachment lines already in a Project's timeline stay as history.

Each Project runs in its own savepoint. A failure rolls that Project back whole, is counted and
logged, and the rest go on. The counts before and after are printed. It never raises, because a
patch that raises aborts ``bench migrate``, which here is the deploy. It is safe to run twice: the
second run finds no repeated URL and writes nothing.
"""

import frappe

from erpnext_enhancements.utils.attachment_copies import extra_copies

LOG = "dedupe_project_attachment_rows"
SAVEPOINT = "dedupe_project_attachment_rows"


def execute():
	try:
		dedupe_project_attachment_rows()
	except Exception:
		try:
			frappe.log_error(title="Project attachment dedupe patch failed", message=frappe.get_traceback())
		except Exception:
			pass
		print(f"{LOG}: aborted early; see the Error Log.")


def dedupe_project_attachment_rows():
	"""Returns ``{"before", "after", "projects", "removed", "failed"}``."""
	before = _count_rows()
	projects, removed, failed = 0, 0, []
	for project in _projects_with_repeats():
		try:
			frappe.db.savepoint(SAVEPOINT)
			dropped = _dedupe(project)
		except Exception as exc:
			try:
				frappe.db.rollback(save_point=SAVEPOINT)
			except Exception:
				pass
			failed.append(f"{project}: {type(exc).__name__}")
			continue
		if dropped:
			projects += 1
			removed += dropped
	after = _count_rows()
	print(
		f"{LOG}: Project File rows {before} -> {after}; removed {removed} extra copies "
		f"from {projects} Projects; {len(failed)} Projects failed."
	)
	if failed:
		try:
			frappe.log_error(
				title="Project attachment dedupe patch skipped some Projects", message="\n".join(failed)
			)
		except Exception:
			pass
	return {"before": before, "after": after, "projects": projects, "removed": removed, "failed": failed}


def _count_rows():
	return frappe.db.sql(
		"select count(*) from `tabFile` where attached_to_doctype = 'Project' and ifnull(is_folder, 0) = 0"
	)[0][0]


def _projects_with_repeats():
	rows = frappe.db.sql(
		"""select attached_to_name from `tabFile`
		where attached_to_doctype = 'Project' and ifnull(is_folder, 0) = 0 and ifnull(file_url, '') != ''
		group by attached_to_name, file_url having count(*) > 1"""
	)
	return sorted({row[0] for row in rows if row[0]})


def _dedupe(project):
	"""Delete one Project's extra copies. Returns how many rows it deleted."""
	rows = frappe.db.sql(
		"""select name, file_url from `tabFile`
		where attached_to_doctype = 'Project' and attached_to_name = %s
			and ifnull(is_folder, 0) = 0 and ifnull(file_url, '') != ''
		order by creation, name""",
		(project,),
		as_dict=True,
	)
	drop = [name for name in extra_copies(rows) if name]
	if not drop:
		return 0
	frappe.db.delete(
		"File", {"name": ("in", drop), "attached_to_doctype": "Project", "attached_to_name": project}
	)
	frappe.clear_document_cache("Project", project)
	return len(drop)

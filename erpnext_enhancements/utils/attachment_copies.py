"""Which of an Opportunity's files a Project still needs, and which of its copies are extra (v1.749.1).

Pure: no frappe, so the tests run it as it is.

Two paths copy an Opportunity's attachments onto a new Project, and both run on every creation:

* the Project ``on_update`` hook ``sync_attachments_from_opportunity`` (``project_enhancements``),
  which fires inside the creation job's ``project.insert``, and again on every later save;
* the creation job's own "attachments" step (``crm_enhancements/api.py``), a moment later.

Until v1.749.1 the job copied every file again without looking, and the hook compared names only
against what the Project held before its loop began, so a file the Opportunity carries twice came
over twice from each path: four rows of one PDF on PRJ-00777. On production on 2026-10-27, 20
Projects held 71 extra rows, from 2025-09-09 on. Both paths now ask :func:`files_to_copy`, and
``patches/dedupe_project_attachment_rows`` removes the stored extras with :func:`extra_copies`.

Every copy is a File row pointing at the same stored file (``file_url``); nothing here or in the
patch touches the file on disk.
"""


def _text(value):
	return str(value or "").strip()


def file_keys(row):
	"""What makes a source file one the record already has: the same stored URL, or the same name.

	The name is the rule the hook has always used, and the URL is what makes two rows the same
	stored file.
	"""
	keys = set()
	url = _text(row.get("file_url"))
	name = _text(row.get("file_name"))
	if url:
		keys.add(("url", url))
	if name:
		keys.add(("name", name))
	return keys


def files_to_copy(existing, sources):
	"""The source rows still to copy, in source order.

	None the record already holds (``existing``, its File rows), and one of each file even when the
	sources repeat it, because the set of what the record holds grows as rows are picked. A source
	row with neither a URL nor a name is skipped.
	"""
	held = set()
	for row in existing or []:
		held |= file_keys(row)
	picked = []
	for row in sources or []:
		keys = file_keys(row)
		if not keys or keys & held:
			continue
		held |= keys
		picked.append(row)
	return picked


def extra_copies(rows):
	"""Names of the File rows to delete from one record: each row whose stored URL a kept row has.

	``rows`` come oldest first (``creation``, then ``name``), so the first row of each URL is the one
	kept. Only an exact URL match counts, character for character (not trimmed, not case-folded,
	unlike MariaDB's PAD SPACE ``_ci`` comparison that finds the candidates), so two different files
	that share a name both stay. A row with no URL is never touched.
	"""
	kept = set()
	drop = []
	for row in rows or []:
		url = str(row.get("file_url") or "")
		if not url.strip():
			continue
		if url in kept:
			drop.append(row.get("name"))
		else:
			kept.add(url)
	return drop

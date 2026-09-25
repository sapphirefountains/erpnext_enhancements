# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Files attached to the Knowledge Base are always private, and a published article keeps its images.

WI-080 PR 2, ADR 0017 ("Every attached file is private"). Two hooks on the core ``File`` doctype,
registered in ``hooks.py``. **Both run for every File on the site**, so each returns at once for a
File that is not attached to the Knowledge Base, after reading the File's attachment (and, on an
update, the stored row's, which v16 has already loaded: no query). They read with
``getattr(doc, "...", None) or ""`` and never raise for an unrelated File: File inserts happen
during ERPNext's own test bootstrap and in every upload on the site, and a hook that raised there
would break all of them.

:func:`force_private` (``doc_events["File"]["before_insert"]`` and ``["before_validate"]``).
A File is public when ``is_private`` is 0, and nginx then serves it to anyone who has the URL, with
no permission check and no login (a public file never reaches Python). Pasted images are already
private: v16 extracts them with ``is_private=True`` unless the doctype sets
``make_attachments_public``, which neither KB doctype does (frappe ``origin/version-16``
``core/doctype/file/utils.py:219-223``). What is not: an upload through the attachment sidebar
with its Private box unticked, an existing public file picked from the library, and a REST or
code insert that leaves ``is_private`` at 0.

**Setting the flag is not enough, because Frappe has already written the bytes.** A ``doc_events``
handler runs *after* the controller's own method of the same name (v16 ``Document.hook``,
``model/document.py:1633-1649``: ``compose`` calls the controller first, then the hooks), and
``File.before_insert`` has by then saved the upload into ``public/files`` and set a ``/files/``
URL (``core/doctype/file/file.py:107-144``). Flipping ``is_private`` alone would leave the bytes
public and the row claiming otherwise, and ``File.validate`` would then refuse the insert with
"The File URL you've entered is incorrect". So on insert this re-saves the content through
File's own ``save_file`` as a private file (or onto an identical private one), and deletes the
public copy **only if this insert wrote it**: ``flags.new_file`` says the save path ran, and no
other File row may point at that URL. A public copy another row uses (a deduplicated upload, a
library pick, a copy) belongs to that row and is left alone. On an **update** (``before_validate``,
which runs before ``File.validate``), setting the flag is enough: ``File.validate`` sees
``is_private`` change and moves the bytes itself, with its own rollback
(``handle_is_private_changed``, ``file.py:324-389``). That closes the other way a KB file could
turn public: its owner editing the File and unticking Private.

**A KB File stays attached where it is** (the same hook, on an update). ``attached_to_doctype`` and
``attached_to_name`` are only ``read_only`` in v16's ``file.json``, which the server never
enforces: ``frappe.client.set_value`` and ``PUT /api/resource/File`` apply the new values and
save (``client.py:208-215``, ``api/v1.py:50-58``), and the write-permission check then runs on the
*updated* row (``model/document.py:584``), which ``File.has_permission`` grants its owner. So the
owner of a published article's image could detach it in one call and delete it in the next (the
delete check below would see no article), or detach it and untick Private in the same call (the
KB test would see no KB doctype). On an update this compares the attachment with the stored row
(``get_doc_before_save()``, loaded ``FOR UPDATE`` by ``check_if_latest``, ``:1097``) and refuses
any change to ``attached_to_doctype`` or ``attached_to_name`` of a File the stored row attaches to
either KB doctype, unless KB code sets ``flags.kb_action``; and a File is treated as a KB File if
either the stored row or the new one says so. A permission hook could not do this, since it sees
only the updated row. ``before_validate`` runs even under ``flags.ignore_validate``
(``model/document.py:1404-1405``, before the return at ``:1407-1408``), so no flag skips it.
``api/comments.link_files_to_comment`` moves Files with ``db_set``, which runs no hook at all, so
it refuses KB Files itself.

:func:`file_has_permission` (``has_permission["File"]``). v16 protects attachments from deletion
only on a *submitted* document whose doctype sets ``protect_attached_files``
(``File.validate_protected_file``, ``file.py:586-614``), and a Knowledge Article is never
submitted. ``File.has_permission`` lets a File's owner delete it whatever it is attached to
(``file.py:996-1033``). So the author of a published article, or anyone who uploaded one of its
images, could delete a picture out of approved text with no review. This refuses ``delete`` on a
File attached to a Knowledge Article. **It is a permission hook, not an ``on_trash`` hook, on
purpose:** ``File.on_trash`` deletes the bytes from disk before any ``doc_events["File"]
["on_trash"]`` handler runs (the same controller-first order), so refusing there would roll back
the row and leave it pointing at a file that is already gone. A permission check runs in
``delete_doc`` before ``on_trash`` (``model/delete_doc.py:173-176``), while nothing has been
touched.

**The flag for PR 3.** The Knowledge Base's own code may delete such a File, or move a KB File to
another document, only by saying so: ``file.flags.kb_action = True`` before ``file.save()``, or
``frappe.delete_doc("File", name, flags={"kb_action": True})`` (``delete_doc`` copies ``flags``
onto the document before it checks permission). **Publishing moves a draft's Files onto the
Article, so PR 3 sets it on each File it moves.** Nothing in v1 deletes one (retiring keeps them).
Code running with ``ignore_permissions`` does not consult permission hooks at all; that is Frappe's
rule for every doctype, and the Knowledge Base Integrity report (PR 4) is what would notice.
Administrator is never refused a delete, by Frappe.
"""

import frappe
from frappe.utils import cint

from erpnext_enhancements.knowledge_base.constants import ARTICLE_DOCTYPE, KB_DOCTYPES

#: The flag KB code sets on a File to delete one attached to a Knowledge Article, or to move a
#: File off either KB doctype.
ACTION_FLAG = "kb_action"


def force_private(doc, method=None):
	"""Make a File attached to either KB doctype private, bytes included, and keep it attached
	where it is. See the module docstring."""
	attached = getattr(doc, "attached_to_doctype", None) or ""
	# Only a saved File has a stored row, and before_insert never sees one.
	stored = _stored_row(doc) if method == "before_validate" else None
	was_attached = (_read(stored, "attached_to_doctype") or "") if stored is not None else ""
	if attached not in KB_DOCTYPES and was_attached not in KB_DOCTYPES:
		return
	if was_attached in KB_DOCTYPES:
		_refuse_moving(doc, stored)
	if cint(getattr(doc, "is_private", 0)):
		return
	file_url = getattr(doc, "file_url", None) or ""
	if method == "before_insert" and file_url.startswith("/files/"):
		_resave_private(doc, file_url)
	else:
		# An update (File.validate moves the bytes), or a link to somewhere else: http(s) URLs
		# point at nothing of ours on disk, so the flag is all there is.
		doc.is_private = 1


def _resave_private(doc, public_url):
	wrote_public_copy = bool(_flag(doc, "new_file"))
	# Read while is_private is still 0: File.get_content validates the URL against the folder
	# is_private names, and a /files/ URL is outside the private one.
	content = doc.get_content()
	doc.is_private = 1
	doc.file_url = None
	doc.content_hash = None
	doc.content = content
	doc.decode = False
	# Marks what follows as a new file, so File.on_rollback deletes the private copy if this
	# insert fails later, rather than trying to restore it.
	doc.flags.new_file = True
	doc.save_file(content=content)
	if wrote_public_copy and not frappe.db.exists("File", {"file_url": public_url}):
		from frappe.core.doctype.file.utils import delete_file

		delete_file(public_url)


def file_has_permission(doc, ptype=None, user=None, debug=False):
	"""Refuse ``delete`` on a File attached to a Knowledge Article, unless KB code flags it.

	On v16 a ``has_permission`` hook that returns anything falsy **denies**
	(``permissions.py:483-500``), so every other path returns ``True`` explicitly: this hook only
	ever takes a right away, and only that one.
	"""
	if ptype != "delete":
		return True
	if (getattr(doc, "attached_to_doctype", None) or "") != ARTICLE_DOCTYPE:
		return True
	if _flag(doc, ACTION_FLAG):
		return True
	return False


def _refuse_moving(doc, stored):
	"""Refuse a change to where a KB File is attached, unless KB code flags it."""
	if _flag(doc, ACTION_FLAG):
		return
	fields = ("attached_to_doctype", "attached_to_name")
	before = tuple(str(_read(stored, f) or "") for f in fields)
	after = tuple(str(getattr(doc, f, None) or "") for f in fields)
	if before == after:
		return
	frappe.throw(
		frappe._(
			"{0} is attached to {1} {2}, and a file attached to the knowledge base stays there: it "
			"cannot be detached or moved to another document. A published article keeps its "
			"images, and every knowledge base file stays private. To take a picture out of a draft, "
			"delete it from the draft."
		).format(getattr(doc, "file_name", None) or getattr(doc, "name", None) or "This file", *before),
		title=frappe._("Knowledge base files stay attached"),
	)


def _stored_row(doc):
	"""The File as stored, which v16's ``check_if_latest`` loaded before any hook ran; no query."""
	getter = getattr(doc, "get_doc_before_save", None)
	return getter() if callable(getter) else None


def _read(obj, key):
	getter = getattr(obj, "get", None)
	return getter(key) if callable(getter) else getattr(obj, key, None)


def _flag(doc, name):
	flags = getattr(doc, "flags", None)
	if flags is None:
		return None
	getter = getattr(flags, "get", None)
	return getter(name) if callable(getter) else getattr(flags, name, None)

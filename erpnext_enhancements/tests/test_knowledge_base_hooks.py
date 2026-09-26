# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's hooks: private Files, protected article images, and the Version controller.

WI-080 PR 2, ADR 0017. Bench-free, with its own ``frappe`` stub. What it pins:

* **The two File hooks run for every File on the site**, so an unrelated File is returned at once:
  no attribute written, no query made, nothing raised, even for an object with no File attributes
  at all (``doc_events`` fire during ERPNext's own test bootstrap).
* **A File on either KB doctype ends up private with its bytes.** A doc_events ``before_insert``
  runs after ``File.before_insert`` has written a public upload, so the hook re-saves the content
  through ``save_file`` as private, reading it *before* flipping ``is_private`` (File validates the
  URL against the folder ``is_private`` names), and deletes the public copy only when this insert
  wrote it and no other File row uses it. On an update it only sets the flag.
* **A KB File stays attached where it is.** On an update, a change to ``attached_to_doctype`` or
  ``attached_to_name`` of a File the stored row attaches to either KB doctype is refused unless KB
  code sets ``flags.kb_action``; otherwise its owner could detach an article's image and then
  delete it, or detach it and make it public in one call. ``link_files_to_comment``, which moves
  Files with ``db_set`` and so runs no hook, skips KB Files itself.
* **Delete is refused on a File attached to a Knowledge Article**, and only delete, and only there,
  unless KB code sets ``flags.kb_action``. Every other answer is ``True`` exactly: on v16 a falsy
  permission-hook answer denies, and this hook sees every File permission check on the site.
* **The hooks are registered** where ``hooks.py`` says.
* **The Version controller** strips presentation and records contributors on a content save,
  refuses a content edit outside Draft or one carrying a secret (in ``before_validate``, which
  ``flags.ignore_validate`` does not skip), and refuses a submit that breaks any approval rule, in
  ``before_submit`` and again in ``on_submit``: Administrator (which holds every role), Guest and
  any account whose ``User.user_type``, read from the User row at approval, is not System User
  included.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_hooks -v
"""

import ast
import datetime
import importlib
import inspect
import sys
import textwrap
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

FILES_MODULE = "erpnext_enhancements.knowledge_base.files"
VERSION_MODULE = "erpnext_enhancements.knowledge_base.doctype.knowledge_article_version.knowledge_article_version"
COMMENTS_MODULE = "erpnext_enhancements.api.comments"
ARTICLE = "Knowledge Article"
VERSION = "Knowledge Article Version"
AUTHOR = "parker@example.com"
APPROVER = "james@example.com"
OPENED = "2026-09-25 10:15:00.123456"
OPENED_AT = datetime.datetime(2026, 9, 25, 10, 15, 0, 123456)
#: Built by concatenation, never a literal (push protection refused one once).
STRIPE_KEY = "sk" + "_live_" + "a1B2" * 6


# ------------------------------------------------------------------ the frappe stub


class Refused(Exception):
	"""What the stub's ``frappe.throw`` raises."""


class _Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Document:
	"""Just enough of ``frappe.model.document.Document`` for the Version controller's hooks."""

	def __init__(self, **values):
		object.__setattr__(self, "flags", _Flags())
		object.__setattr__(self, "_doc_before_save", None)
		for key, value in values.items():
			setattr(self, key, value)

	def get(self, key, default=None):
		return getattr(self, key, default)

	def get_doc_before_save(self):
		return self._doc_before_save


STATE = {}


def _reset(**overrides):
	STATE.clear()
	STATE.update(
		{
			"roles": {APPROVER: ["KB Approver", "Desk User"], AUTHOR: ["KB Author", "Desk User"]},
			# User.user_type as v16 stores it; Administrator and Guest as v16 fixes them (user.py:406).
			"user_types": {
				APPROVER: "System User",
				AUTHOR: "System User",
				"Administrator": "System User",
				"Guest": "Website User",
			},
			"user_type_reads": [],
			"file_rows": set(),
			"exists_calls": [],
			"deleted": [],
			"db_forbidden": False,
			"file_docs": {},
			"errors": [],
		}
	)
	STATE.update(overrides)
	frappe = sys.modules["frappe"]
	frappe.session = _Flags(user=APPROVER, sid="3f1c9a7b2e5d")
	frappe.flags = _Flags()
	frappe.local = types.SimpleNamespace(request=types.SimpleNamespace(headers={}))


def _throw(message, exc=None, title=None, **kwargs):
	raise Refused(message)


def _exists(doctype, filters=None):
	if STATE["db_forbidden"]:
		raise AssertionError(f"an unrelated File reached the database: exists({doctype!r}, {filters!r})")
	STATE["exists_calls"].append((doctype, filters))
	assert doctype == "File", doctype
	return filters["file_url"] in STATE["file_rows"]


def _get_value(doctype, name, fieldname, *args, **kwargs):
	"""``frappe.db.get_value`` for the one read the Version controller makes: a User's user_type."""
	assert (doctype, fieldname) == ("User", "user_type"), (doctype, fieldname)
	STATE["user_type_reads"].append(name)
	return STATE["user_types"].get(name)


def _delete_file(path):
	STATE["deleted"].append(path)


def _cint(value):
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return 0


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda message, *a, **k: message
	frappe.throw = _throw
	frappe.get_roles = lambda user=None: list(STATE["roles"].get(user, ()))
	frappe.db = types.SimpleNamespace(exists=_exists, get_value=_get_value)
	# For api/comments.py's link_files_to_comment.
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.has_permission = lambda *a, **k: True
	frappe.get_doc = lambda doctype, name: STATE["file_docs"][name]
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	frappe.get_traceback = lambda *a, **k: "traceback"

	utils = types.ModuleType("frappe.utils")
	utils.cint = _cint
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	frappe.model = model

	core = types.ModuleType("frappe.core")
	core_doctype = types.ModuleType("frappe.core.doctype")
	file_pkg = types.ModuleType("frappe.core.doctype.file")
	file_utils = types.ModuleType("frappe.core.doctype.file.utils")
	file_utils.delete_file = _delete_file

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.core": core,
			"frappe.core.doctype": core_doctype,
			"frappe.core.doctype.file": file_pkg,
			"frappe.core.doctype.file.utils": file_utils,
		}
	)


files = None
version_controller = None
comments = None


def setUpModule():
	global files, version_controller, comments
	_install_frappe_stub()
	for name in (FILES_MODULE, VERSION_MODULE, COMMENTS_MODULE):
		sys.modules.pop(name, None)
	files = importlib.import_module(FILES_MODULE)
	version_controller = importlib.import_module(VERSION_MODULE)
	comments = importlib.import_module(COMMENTS_MODULE)


# ------------------------------------------------------------------ a File, as far as the hook sees one


class FakeFile:
	"""The parts of v16 ``File`` the hook touches, with the one rule that bites: ``get_content``
	validates the URL against the folder ``is_private`` names (``File.validate_file_path``)."""

	def __init__(self, stored=None, **values):
		self.flags = _Flags()
		self.calls = []
		self.content = b""
		self.decode = False
		self.content_hash = "hash-public"
		self.file_name = "slip.png"
		self.bytes_on_disk = b"\x89PNG screenshot"
		# The row as stored, as v16's check_if_latest loads it before any hook; None for a new File.
		self.stored = stored
		for key, value in values.items():
			setattr(self, key, value)

	def get_doc_before_save(self):
		return self.stored

	def db_set(self, fieldname, value, update_modified=True):
		self.calls.append(("db_set", fieldname, value))
		setattr(self, fieldname, value)

	def get_content(self):
		self.calls.append(("get_content", self.is_private, self.file_url))
		if _cint(self.is_private) and (self.file_url or "").startswith("/files/"):
			raise AssertionError("File.validate_file_path would refuse: a /files/ URL read as private")
		return self.content or self.bytes_on_disk

	def save_file(self, content=None, decode=False, ignore_existing_file_check=False, overwrite=False):
		self.calls.append(("save_file", self.is_private, self.file_url, content))
		if not self.content:
			raise AssertionError("File.is_remote_file would be True with no content: save_file writes nothing")
		self.file_url = "/private/files/" + self.file_name


class Untouchable:
	"""An unrelated File: any attribute write, or any read but its attachment and the stored row
	v16 has already loaded (no query), is a failure."""

	def __init__(self, attached_to_doctype, stored_doctype=None):
		object.__setattr__(self, "attached_to_doctype", attached_to_doctype)
		object.__setattr__(
			self, "_stored", {"attached_to_doctype": stored_doctype, "attached_to_name": "X-1"}
		)

	def get_doc_before_save(self):
		return self._stored

	def __setattr__(self, key, value):
		raise AssertionError(f"wrote {key} on an unrelated File")

	def __getattr__(self, key):
		raise AssertionError(f"read {key} on an unrelated File")


# ------------------------------------------------------------------ the fast path


class TestUnrelatedFilesAreLeftAlone(unittest.TestCase):
	def setUp(self):
		_reset(db_forbidden=True)

	def test_force_private_returns_at_once(self):
		for doctype in ("Opportunity", "Project", "Knowledge Base", "knowledge article", "", None):
			for stored in ("Opportunity", doctype, "", None):
				for method in ("before_insert", "before_validate"):
					with self.subTest(doctype=doctype, stored=stored, method=method):
						files.force_private(Untouchable(doctype, stored), method)

	def test_an_object_with_no_file_attributes_does_not_raise(self):
		for doc in (object(), types.SimpleNamespace(), types.SimpleNamespace(attached_to_doctype=None)):
			files.force_private(doc, "before_insert")
			self.assertIs(files.file_has_permission(doc, "delete"), True)

	def test_the_permission_hook_says_true_for_every_other_file_and_right(self):
		for doctype in ("Opportunity", VERSION, "", None):
			for ptype in ("read", "select", "write", "create", "delete", "share", "print", "email", None):
				with self.subTest(doctype=doctype, ptype=ptype):
					doc = types.SimpleNamespace(attached_to_doctype=doctype, flags=_Flags())
					self.assertIs(files.file_has_permission(doc, ptype), True)


# ------------------------------------------------------------------ private files


class TestForcePrivate(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_an_already_private_file_is_untouched(self):
		for doctype in (ARTICLE, VERSION):
			for private in (1, "1", True):
				with self.subTest(doctype=doctype, private=private):
					doc = FakeFile(attached_to_doctype=doctype, is_private=private, file_url="/private/files/a.png")
					files.force_private(doc, "before_insert")
					self.assertEqual(doc.calls, [])
					self.assertEqual(doc.file_url, "/private/files/a.png")

	def test_a_new_public_upload_is_re_saved_private_and_its_public_copy_deleted(self):
		for doctype in (ARTICLE, VERSION):
			with self.subTest(doctype=doctype):
				_reset()
				doc = FakeFile(
					attached_to_doctype=doctype,
					is_private=0,
					file_url="/files/slip.png",
					content=b"\x89PNG uploaded",
				)
				doc.flags.new_file = True
				files.force_private(doc, "before_insert")
				self.assertEqual(doc.calls[0], ("get_content", 0, "/files/slip.png"))
				self.assertEqual(doc.calls[1], ("save_file", 1, None, b"\x89PNG uploaded"))
				self.assertEqual(doc.is_private, 1)
				self.assertEqual(doc.file_url, "/private/files/slip.png")
				self.assertIsNone(doc.content_hash)
				self.assertTrue(doc.flags.new_file)
				self.assertEqual(STATE["exists_calls"], [("File", {"file_url": "/files/slip.png"})])
				self.assertEqual(STATE["deleted"], ["/files/slip.png"])

	def test_a_public_copy_another_file_uses_is_never_deleted(self):
		"""A deduplicated upload or a library pick shares the public bytes with another row."""
		_reset(file_rows={"/files/logo.png"})
		doc = FakeFile(attached_to_doctype=ARTICLE, is_private=0, file_url="/files/logo.png")
		doc.flags.new_file = True
		files.force_private(doc, "before_insert")
		self.assertEqual(doc.file_url, "/private/files/slip.png")
		self.assertEqual(doc.content, b"\x89PNG screenshot")
		self.assertEqual(STATE["deleted"], [])

	def test_a_copy_that_wrote_nothing_deletes_nothing(self):
		"""create_attachment_copy and a normalised same-site URL never set flags.new_file."""
		doc = FakeFile(attached_to_doctype=VERSION, is_private=0, file_url="/files/logo.png")
		files.force_private(doc, "before_insert")
		self.assertEqual(doc.is_private, 1)
		self.assertEqual(STATE["exists_calls"], [])
		self.assertEqual(STATE["deleted"], [])

	def test_a_link_elsewhere_only_gets_the_flag(self):
		doc = FakeFile(attached_to_doctype=ARTICLE, is_private=0, file_url="https://example.com/a.png")
		files.force_private(doc, "before_insert")
		self.assertEqual(doc.is_private, 1)
		self.assertEqual(doc.calls, [])

	def test_an_update_only_sets_the_flag_and_lets_file_validate_move_the_bytes(self):
		doc = FakeFile(attached_to_doctype=ARTICLE, is_private=0, file_url="/files/slip.png")
		files.force_private(doc, "before_validate")
		self.assertEqual(doc.is_private, 1)
		self.assertEqual(doc.file_url, "/files/slip.png")
		self.assertEqual(doc.calls, [])
		self.assertEqual(STATE["deleted"], [])


# ------------------------------------------------------------------ deleting an article's image


class TestArticleImagesCannotBeDeleted(unittest.TestCase):
	def test_delete_is_refused_on_an_article_file(self):
		doc = types.SimpleNamespace(attached_to_doctype=ARTICLE, flags=_Flags())
		self.assertIs(files.file_has_permission(doc, "delete", user=AUTHOR), False)

	def test_every_other_right_on_an_article_file_is_left_to_frappe(self):
		doc = types.SimpleNamespace(attached_to_doctype=ARTICLE, flags=_Flags())
		for ptype in ("read", "select", "write", "create", "share", "print", "email", None):
			with self.subTest(ptype=ptype):
				self.assertIs(files.file_has_permission(doc, ptype), True)

	def test_kb_code_may_delete_by_saying_so(self):
		doc = types.SimpleNamespace(attached_to_doctype=ARTICLE, flags=_Flags(kb_action=True))
		self.assertIs(files.file_has_permission(doc, "delete"), True)
		self.assertEqual(files.ACTION_FLAG, "kb_action")

	def test_the_publish_flag_is_not_the_delete_flag(self):
		doc = types.SimpleNamespace(attached_to_doctype=ARTICLE, flags=_Flags(kb_publish=True))
		self.assertIs(files.file_has_permission(doc, "delete"), False)

	def test_once_detached_the_delete_check_sees_no_article(self):
		"""Why a KB File must not be detachable: the delete check reads the row as it now is."""
		detached = types.SimpleNamespace(attached_to_doctype="", flags=_Flags())
		self.assertIs(files.file_has_permission(detached, "delete"), True)


# ------------------------------------------------------------------ a KB File stays attached


def _stored_file(doctype, name):
	return {"attached_to_doctype": doctype, "attached_to_name": name, "is_private": 1}


def _update(stored_doctype, stored_name="KB-0612", **values):
	"""A File on its update path: the stored row, and the row as the request left it."""
	current = {
		"attached_to_doctype": stored_doctype,
		"attached_to_name": stored_name,
		"is_private": 1,
		"file_url": "/private/files/slip.png",
	}
	current.update(values)
	return FakeFile(stored=_stored_file(stored_doctype, stored_name), **current)


class TestKbFilesStayAttached(unittest.TestCase):
	"""frappe.client.set_value / PUT /api/resource/File apply read_only fields and save, and the
	write check runs on the updated row, which File.has_permission grants its owner."""

	def setUp(self):
		_reset()

	def test_an_articles_image_cannot_be_detached_or_moved(self):
		for changes in (
			{"attached_to_doctype": "", "attached_to_name": ""},
			{"attached_to_doctype": None, "attached_to_name": None},
			{"attached_to_doctype": "ToDo", "attached_to_name": "a1b2c3"},
			{"attached_to_name": "KB-0613"},
			{"attached_to_doctype": VERSION, "attached_to_name": "KBV-00012"},
		):
			with self.subTest(changes=changes):
				doc = _update(ARTICLE, **changes)
				with self.assertRaises(Refused) as caught:
					files.force_private(doc, "before_validate")
				message = str(caught.exception)
				self.assertIn("slip.png is attached to Knowledge Article KB-0612", message)
				self.assertIn("cannot be detached or moved", message)

	def test_a_drafts_file_cannot_be_moved_off_it_either(self):
		"""Detached, it would no longer be a KB File, and its owner could make it public."""
		doc = _update(VERSION, "KBV-00012", attached_to_doctype="", attached_to_name="")
		with self.assertRaises(Refused):
			files.force_private(doc, "before_validate")

	def test_detaching_and_making_public_in_one_call_is_refused(self):
		doc = _update(ARTICLE, attached_to_doctype="", attached_to_name="", is_private=0)
		with self.assertRaises(Refused):
			files.force_private(doc, "before_validate")

	def test_a_save_that_leaves_the_attachment_alone_passes(self):
		files.force_private(_update(ARTICLE, file_name="slip-renamed.png"), "before_validate")
		doc = _update(ARTICLE, is_private=0)
		files.force_private(doc, "before_validate")
		self.assertEqual(doc.is_private, 1)
		self.assertEqual(doc.calls, [])

	def test_kb_code_may_move_a_file_by_saying_so_and_it_stays_private(self):
		"""Publishing moves a draft's Files onto the Article (PR 3), with flags.kb_action."""
		doc = _update(VERSION, "KBV-00012", attached_to_doctype=ARTICLE, attached_to_name="KB-0612")
		doc.flags.kb_action = True
		files.force_private(doc, "before_validate")
		doc = _update(ARTICLE, attached_to_doctype="", attached_to_name="", is_private=0)
		doc.flags.kb_action = True
		files.force_private(doc, "before_validate")
		self.assertEqual(doc.is_private, 1)

	def test_the_publish_flag_does_not_move_a_file(self):
		doc = _update(VERSION, "KBV-00012", attached_to_doctype=ARTICLE, attached_to_name="KB-0612")
		doc.flags.kb_publish = True
		with self.assertRaises(Refused):
			files.force_private(doc, "before_validate")

	def test_a_file_from_elsewhere_may_be_attached_and_is_made_private(self):
		doc = FakeFile(
			stored=_stored_file("Opportunity", "CRM-OPP-2026-00001"),
			attached_to_doctype=VERSION,
			attached_to_name="KBV-00012",
			is_private=0,
			file_url="/files/slip.png",
		)
		files.force_private(doc, "before_validate")
		self.assertEqual(doc.is_private, 1)

	def test_an_insert_has_no_stored_row_to_compare(self):
		doc = FakeFile(
			attached_to_doctype=ARTICLE,
			attached_to_name="KB-0612",
			is_private=1,
			file_url="/private/files/a.png",
		)
		for method in ("before_insert", "before_validate"):
			files.force_private(doc, method)
		self.assertEqual(doc.calls, [])


class TestCommentsNeverMoveKbFiles(unittest.TestCase):
	"""link_files_to_comment moves a caller's own Files with db_set, which runs no File hook."""

	def setUp(self):
		_reset()
		sys.modules["frappe"].session.user = AUTHOR

	def _files(self, *docs):
		STATE["file_docs"].update({doc.name: doc for doc in docs})

	def test_an_articles_image_is_not_moved_to_the_callers_todo(self):
		kb = FakeFile(name="f-kb", owner=AUTHOR, attached_to_doctype=ARTICLE, attached_to_name="KB-0612")
		draft = FakeFile(
			name="f-draft", owner=AUTHOR, attached_to_doctype=VERSION, attached_to_name="KBV-00012"
		)
		mine = FakeFile(name="f-mine", owner=AUTHOR, attached_to_doctype=None, attached_to_name=None)
		self._files(kb, draft, mine)
		comments.link_files_to_comment(["f-kb", "f-draft", "f-mine"], "c-1", "ToDo", "todo-1")
		for doc, doctype, name in ((kb, ARTICLE, "KB-0612"), (draft, VERSION, "KBV-00012")):
			with self.subTest(file=doc.name):
				self.assertEqual((doc.attached_to_doctype, doc.attached_to_name), (doctype, name))
				self.assertEqual(doc.calls, [])
		self.assertEqual((mine.attached_to_doctype, mine.attached_to_name), ("ToDo", "todo-1"))
		self.assertEqual(STATE["errors"], [])

	def test_not_even_for_a_system_manager(self):
		STATE["roles"][None] = ["System Manager"]
		kb = FakeFile(name="f-kb", owner=APPROVER, attached_to_doctype=ARTICLE, attached_to_name="KB-0612")
		self._files(kb)
		comments.link_files_to_comment(["f-kb"], "c-1", "ToDo", "todo-1")
		self.assertEqual(kb.attached_to_doctype, ARTICLE)

	def test_a_kb_file_already_on_that_document_is_left_where_it_is(self):
		kb = FakeFile(name="f-kb", owner=AUTHOR, attached_to_doctype=ARTICLE, attached_to_name="KB-0612")
		self._files(kb)
		comments.link_files_to_comment(["f-kb"], "c-1", ARTICLE, "KB-0612")
		self.assertEqual((kb.attached_to_doctype, kb.attached_to_name), (ARTICLE, "KB-0612"))


# ------------------------------------------------------------------ registration


def _hooks_assignment(name):
	for node in ast.parse((APP / "hooks.py").read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name:
			return node.value
	raise AssertionError(f"{name} not in hooks.py")


def _dict_get(node, key):
	for k, v in zip(node.keys, node.values, strict=True):
		if isinstance(k, ast.Constant) and k.value == key:
			return v
	raise AssertionError(f"{key!r} not in dict")


class TestRegistration(unittest.TestCase):
	def test_force_private_runs_before_insert_and_before_validate(self):
		file_events = _dict_get(_hooks_assignment("doc_events"), "File")
		for event in ("before_insert", "before_validate"):
			with self.subTest(event=event):
				self.assertEqual(ast.literal_eval(_dict_get(file_events, event)), f"{FILES_MODULE}.force_private")
		self.assertEqual(
			ast.literal_eval(_dict_get(file_events, "after_insert")),
			"erpnext_enhancements.google_drive.drive_sync.on_file_attached",
		)

	def test_the_permission_hook_is_registered_for_file(self):
		self.assertEqual(
			ast.literal_eval(_dict_get(_hooks_assignment("has_permission"), "File")),
			f"{FILES_MODULE}.file_has_permission",
		)

	def test_the_registered_functions_exist_with_the_signatures_frappe_calls(self):
		# v16 passes the event name ("before_insert") only to a handler with two positional
		# parameters (document.py _accepts_method_argument), and force_private branches on it.
		positional = [
			p
			for p in inspect.signature(files.force_private).parameters.values()
			if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
		]
		self.assertGreater(len(positional), 1)
		# frappe.call passes has_permission hooks doc=, ptype=, user=, debug= by keyword.
		self.assertIs(
			files.file_has_permission(doc=types.SimpleNamespace(), ptype="read", user="x", debug=False), True
		)


# ------------------------------------------------------------------ the Version controller


def _stored(**values):
	stored = {
		"review_state": "In Review",
		"owner": AUTHOR,
		"submitted_by": AUTHOR,
		"contributors": AUTHOR,
		"ai_requested_by": None,
		"modified": OPENED_AT,
		"title": "Receiving a PO",
		"department_block": "06 Operations",
		"summary": "How to receive.",
		"keywords": "PO",
		"process_owner": AUTHOR,
		"review_every_months": 6,
		"body": "<p>Scan the slip.</p>",
		"change_note": "First version.",
	}
	stored.update(values)
	return stored


CONTENT = ("title", "department_block", "summary", "keywords", "process_owner", "review_every_months", "body", "change_note")


def _version(stored, **values):
	current = {k: v for k, v in (stored or {}).items() if k in CONTENT or k in ("contributors", "owner")}
	current.update(values)
	doc = version_controller.KnowledgeArticleVersion(name="KBV-00012", **current)
	object.__setattr__(doc, "_doc_before_save", stored)
	return doc


class TestVersionContentRules(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_new_draft_is_stripped_and_its_creator_recorded(self):
		sys.modules["frappe"].session.user = AUTHOR
		doc = _version(None, title="T", body='<p style="color: white;">hidden</p>')
		doc.before_validate()
		self.assertEqual(doc.body, "<p>hidden</p>")
		self.assertEqual(doc.contributors, AUTHOR)

	def test_an_edit_to_a_draft_records_the_editor(self):
		stored = _stored(review_state="Draft")
		doc = _version(stored, body="<p>Scan the slip twice.</p>")
		doc.before_validate()
		self.assertEqual(doc.contributors, f"{AUTHOR}\n{APPROVER}")

	def test_a_save_with_no_content_change_records_nobody_and_scans_nothing(self):
		"""A state change by the KB's own actions (request changes, withdraw) must not be blocked
		by text that predates a stricter scan: it is scanned again before approval."""
		stored = _stored(body=f"<p>{STRIPE_KEY}</p>")
		doc = _version(stored, review_state="Draft", review_note="Please fix.")
		doc.before_validate()
		self.assertEqual(doc.contributors, AUTHOR)

	def test_content_cannot_change_in_review(self):
		doc = _version(_stored(), body="<p>Scan the slip twice.</p>")
		with self.assertRaises(Refused) as caught:
			doc.before_validate()
		self.assertIn("In Review", str(caught.exception))

	def test_a_presentation_only_change_in_review_is_not_an_edit(self):
		doc = _version(_stored(), body='<p style="color: red;">Scan the slip.</p>')
		doc.before_validate()
		self.assertEqual(doc.body, "<p>Scan the slip.</p>")
		self.assertEqual(doc.contributors, AUTHOR)

	def test_a_discarded_version_is_history(self):
		doc = _version(_stored(review_state="Discarded"), title="New title")
		with self.assertRaises(Refused):
			doc.before_validate()

	def test_a_secret_is_refused_by_line_and_kind_never_by_value(self):
		stored = _stored(review_state="Draft")
		doc = _version(stored, body=f"<p>ok</p><p>{STRIPE_KEY}</p>")
		with self.assertRaises(Refused) as caught:
			doc.before_validate()
		message = str(caught.exception)
		self.assertIn("Body line 2 looks like a Stripe secret key", message)
		self.assertNotIn(STRIPE_KEY[-10:], message)
		self.assertEqual(doc.get("contributors"), AUTHOR)

	def test_an_amendment_is_still_refused(self):
		"""before_insert runs before before_validate on insert (document.py:480, then :486)."""
		doc = _version(None, title="T", amended_from="KBV-00001")
		for hook in ("before_insert", "validate"):
			with self.subTest(hook=hook), self.assertRaises(Refused) as caught:
				getattr(doc, hook)()
			self.assertIn("amended", str(caught.exception))

	def test_the_content_rules_run_where_ignore_validate_cannot_reach(self):
		"""v16 runs before_validate before it looks at flags.ignore_validate (document.py:1404-1405,
		then :1407-1408), which skips validate, before_save and before_submit. So the rules run
		there, and once. Read from the syntax tree, so a comment naming the call cannot satisfy or
		break it."""
		cls = version_controller.KnowledgeArticleVersion

		def calls(hook):
			method = getattr(cls, hook, None)
			if method is None:
				return set()
			tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
			return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

		self.assertIn("_apply_content_rules", calls("before_validate"))
		for hook in ("validate", "before_save", "before_submit"):
			with self.subTest(hook=hook):
				self.assertNotIn("_apply_content_rules", calls(hook))


class TestVersionApprovalGate(unittest.TestCase):
	def setUp(self):
		_reset()

	def _publishing(self, stored=None, **values):
		stored = _stored() if stored is None else stored
		doc = _version(stored, review_state="Published", approved_by=APPROVER, **values)
		doc.flags.kb_publish = True
		doc.flags.kb_opened_modified = OPENED
		return doc

	def _refused_in_both_hooks(self, doc, phrase):
		for hook in ("before_submit", "on_submit"):
			with self.subTest(hook=hook), self.assertRaises(Refused) as caught:
				getattr(doc, hook)()
			self.assertIn("KBV-00012 cannot be approved", str(caught.exception))
			self.assertIn(phrase, str(caught.exception))

	def test_a_second_person_from_a_browser_publishes(self):
		doc = self._publishing()
		doc.before_submit()
		doc.on_submit()

	def test_without_the_publish_flag_nothing_reaches_the_rules(self):
		doc = self._publishing()
		doc.flags.kb_publish = None
		for hook in ("before_submit", "on_submit"):
			with self.subTest(hook=hook), self.assertRaises(Refused) as caught:
				getattr(doc, hook)()
			self.assertIn("Approve and Publish", str(caught.exception))

	def test_the_approver_role_is_required(self):
		STATE["roles"][APPROVER] = ["KB Author", "System Manager"]
		self._refused_in_both_hooks(self._publishing(), "only a KB Approver")

	def test_a_job_or_the_console_cannot_publish(self):
		sys.modules["frappe"].local.request = None
		self._refused_in_both_hooks(self._publishing(), "browser")

	def test_a_token_cannot_publish(self):
		frappe = sys.modules["frappe"]
		frappe.local.request = types.SimpleNamespace(headers={"Authorization": "token abc:def"})
		self._refused_in_both_hooks(self._publishing(), "browser")
		frappe.local.request = types.SimpleNamespace(headers={})
		frappe.session.sid = APPROVER
		self._refused_in_both_hooks(self._publishing(), "browser")

	def test_a_confirmed_ai_card_cannot_publish(self):
		sys.modules["frappe"].flags.ai_gate_bypass = True
		self._refused_in_both_hooks(self._publishing(), "AI assistant")

	def test_the_author_cannot_publish_their_own(self):
		STATE["roles"][AUTHOR] = ["KB Approver"]
		sys.modules["frappe"].session.user = AUTHOR
		self._refused_in_both_hooks(self._publishing(), "different KB Approver")

	def test_a_contributor_cannot_publish(self):
		self._refused_in_both_hooks(self._publishing(_stored(contributors=f"{AUTHOR}\n{APPROVER}")), "changed its content")

	def test_the_copy_must_be_the_one_the_approver_opened(self):
		doc = self._publishing()
		doc.flags.kb_opened_modified = "2026-09-25 10:14:59.000000"
		self._refused_in_both_hooks(doc, "changed after you opened it")
		doc.flags.kb_opened_modified = None
		self._refused_in_both_hooks(doc, "changed after you opened it")

	def test_only_a_version_in_review_is_published(self):
		self._refused_in_both_hooks(self._publishing(_stored(review_state="Draft")), "not In Review")

	def test_the_rules_read_the_stored_version_not_the_copy_in_memory(self):
		"""Code calling submit() could clear contributors in memory; the stored row still says."""
		doc = self._publishing(_stored(contributors=f"{AUTHOR}\n{APPROVER}"), contributors=AUTHOR)
		self._refused_in_both_hooks(doc, "changed its content")

	def test_the_copy_published_must_be_the_copy_submitted(self):
		doc = self._publishing(body="<p>Scan the slip, then shred it.</p>")
		self._refused_in_both_hooks(doc, "not the copy that was submitted")

	def test_nothing_stored_cannot_be_published(self):
		doc = self._publishing()
		object.__setattr__(doc, "_doc_before_save", None)
		self._refused_in_both_hooks(doc, "no saved version")

	def test_a_secret_is_refused_at_approval_too(self):
		stored = _stored(body=f"<p>{STRIPE_KEY}</p>")
		doc = self._publishing(stored)
		self._refused_in_both_hooks(doc, "looks like it contains a secret")

	def test_administrator_cannot_publish_though_it_holds_every_role(self):
		"""v16 gives Administrator every role (permissions.py:546-547) and makes it a System User,
		so only its name gives it away. The runbook uses it to grant roles, never to approve."""
		STATE["roles"]["Administrator"] = ["Administrator", "System Manager", "KB Approver", "KB Author"]
		sys.modules["frappe"].session.user = "Administrator"
		self._refused_in_both_hooks(self._publishing(), "Administrator is a shared account")

	def test_an_account_that_is_not_a_system_user_cannot_publish(self):
		for user_type, phrase in (("Website User", "has user type Website User"), (None, "has no user type")):
			with self.subTest(user_type=user_type):
				STATE["user_types"][APPROVER] = user_type
				self._refused_in_both_hooks(self._publishing(), "only a System User")
				self._refused_in_both_hooks(self._publishing(), phrase)

	def test_guest_cannot_publish(self):
		STATE["roles"]["Guest"] = ["Guest", "KB Approver"]
		sys.modules["frappe"].session.user = "Guest"
		self._refused_in_both_hooks(self._publishing(), "nobody is signed in")

	def test_the_user_type_is_read_from_the_signed_in_users_row(self):
		"""Read at approval from the User table, not taken from the session, which recorded it at
		login; each hook reads it again."""
		doc = self._publishing()
		doc.before_submit()
		doc.on_submit()
		self.assertEqual(STATE["user_type_reads"], [APPROVER, APPROVER])


if __name__ == "__main__":
	unittest.main()

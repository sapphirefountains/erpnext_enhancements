"""Bench-free tests for the Time Kiosk's "Add files" privacy fix (v1.751.1).

What was wrong, confirmed on production on 2026-10-27:

* ``uploadFile`` in ``public/js/kiosk/app.js`` uploaded with ``is_private '0'``, so every file
  landed under ``/files/``, downloadable without signing in.
* ``api.time_kiosk.link_attachment`` copied each File onto the Project and Task with the same
  ``is_private``, so the copies were public too. It also trusted the Project and Task the page
  sent, and inserted the copies with ``ignore_permissions``.
* ``linkFile`` handed ``countAttachedImageAsJobPhoto`` the display name (``r.file_name``) where
  ``record_job_photo`` wants the File's docname. ``_attach_photo_file`` could not find it, and the
  ``Job Interval Photo`` row stayed Pending.
* ``record_job_photo(file_name=<any File docname>)`` attached that File to the caller's job,
  made it private and fanned it out, whoever it belonged to. And a row of one's own can point at
  anybody's public bytes (frappe reuses a URL for identical bytes, and ``upload_file`` takes a bare
  ``file_url``), so making it private could take a website asset off the public site.

Covered here:

* the kiosk page: a private upload, the docname and the upload's interval passed through, and no
  Project or Task sent;
* the server, by running the real ``_assert_kiosk_file``, ``_make_kiosk_file_private``,
  ``_attach_photo_file`` and ``link_attachment`` source against a fake ``frappe``;
* ``workforce/kiosk_files``: the "may this move" rule, the pairing rule and the reusable relink
  step, routing included;
* the one-shot ``patches/privatize_kiosk_attachment_files`` against an in-memory ``tabFile`` and a
  real temporary ``public/files`` and ``private/files``. The fake ``File.save`` does what frappe
  16.53.0's ``handle_is_private_changed`` does: it refuses a missing source or a taken target,
  moves the bytes and repoints every row that shares the ``content_hash``.

A minimal ``frappe`` stub (and a recording ``photo_routing``) is installed in ``setUpModule`` and
removed afterwards, so this suite has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_attachment_privacy -v
"""

import ast
import contextlib
import copy
import importlib
import io
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
APP_JS = APP / "public" / "js" / "kiosk" / "app.js"
TIME_KIOSK = APP / "api" / "time_kiosk.py"
PATCHES_TXT = APP / "patches.txt"
PATCH_MODULE = "erpnext_enhancements.patches.privatize_kiosk_attachment_files"
KIOSK_FILES_MODULE = "erpnext_enhancements.workforce.kiosk_files"
ROUTING_MODULE = "erpnext_enhancements.workforce.photo_routing"

USER = "tech@example.com"
OTHER_USER = "someone.else@example.com"
EMPLOYEE = "HR-EMP-90001"
OTHER_EMPLOYEE = "HR-EMP-90002"
INTERVAL = "JOB-INT-90001"
OTHER_INTERVAL = "JOB-INT-90002"
PROJECT = "PRJ-90001"
OTHER_PROJECT = "PRJ-90002"
TASK = "TASK-2026-90001"

STATE = {}
_SAVED_MODULES = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key)

	def __setattr__(self, key, value):
		self[key] = value


class _DoesNotExistError(Exception):
	pass


class _PermissionError(Exception):
	pass


def _throw(msg, exc=None):
	raise (exc or Exception)(msg)


def _cint(value):
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _function(body, name):
	"""The source of a top-level kiosk function, up to its closing brace at column 2."""
	start = body.index("function " + name + "(")
	end = body.index("\n  }", start)
	return body[start:end]


def _sharing(files, name, content_hash, url):
	"""What ``kiosk_files.rows_sharing_bytes`` selects, over an in-memory table."""
	return [
		_Dict(
			name=f["name"],
			owner=f.get("owner"),
			file_url=f.get("file_url"),
			attached_to_doctype=f.get("attached_to_doctype"),
		)
		for f in files
		if f["name"] != name
		and ((content_hash and f.get("content_hash") == content_hash) or f.get("file_url") == url)
	]


# ---------------------------------------------------------------------------
# The stub frappe: an in-memory site with a real files directory
# ---------------------------------------------------------------------------


class _FakeDb:
	"""``tabFile``, ``tabJob Interval Photo`` and ``tabJob Interval`` in memory.

	It answers exactly the queries the patch and ``kiosk_files`` send, matched on their text.
	Savepoints snapshot the tables. A commit is counted.
	"""

	def __init__(self):
		self.tables = {"File": {}, "Job Interval Photo": {}, "Job Interval": {}}
		self.snapshots = {}
		self.commits = 0
		self.writes = []
		self.fail_set_value_for = set()

	def savepoint(self, name):
		self.snapshots[name] = copy.deepcopy(self.tables)

	def rollback(self, save_point=None):
		self.tables = copy.deepcopy(self.snapshots[save_point])

	def commit(self):
		self.commits += 1

	def table_exists(self, doctype):
		return doctype in self.tables

	def has_column(self, doctype, column):
		return True

	def get_value(self, doctype, name, fields, as_dict=False):
		row = self.tables[doctype].get(name)
		if row is None:
			return None
		if isinstance(fields, str):
			return row.get(fields)
		return _Dict({f: row.get(f) for f in fields}) if as_dict else tuple(row.get(f) for f in fields)

	def set_value(self, doctype, name, field, value=None, update_modified=True):
		if name in self.fail_set_value_for:
			raise RuntimeError("simulated write failure")
		updates = field if isinstance(field, dict) else {field: value}
		self.tables[doctype][name].update(updates)
		self.writes.append((doctype, name, dict(updates)))

	def _public_interval(self, prefix):
		return [
			f
			for f in self.tables["File"].values()
			if f.get("attached_to_doctype") == "Job Interval"
			and not f.get("is_private")
			and not f.get("is_folder")
			and (f.get("file_url") or "").startswith(prefix)
		]

	def sql_list(self, query, values=()):
		q = " ".join(query.split())
		files = self.tables["File"].values()
		if q.startswith(
			"select name from `tabFile` where attached_to_doctype = 'Job Interval' and is_private = 0"
		):
			rows = self._public_interval(values[0].rstrip("%"))
			return [f["name"] for f in sorted(rows, key=lambda f: (f["creation"], f["name"]))]
		if q == "select name from `tabFile` where content_hash = %s and binary file_url = binary %s":
			content_hash, url = values
			return [
				f["name"] for f in files if f.get("content_hash") == content_hash and f.get("file_url") == url
			]
		if q == "select name from `tabFile` where binary file_url = binary %s limit 1":
			return [f["name"] for f in files if f.get("file_url") == values[0]][:1]
		raise AssertionError(f"unexpected sql_list: {q}")

	def sql(self, query, values=(), as_dict=False):
		q = " ".join(query.split())
		files = self.tables["File"]
		photos = self.tables["Job Interval Photo"]
		if q.startswith("select name, owner, file_url, attached_to_doctype from `tabFile` where name != %s"):
			return _sharing(files.values(), *values)
		if q.startswith("update `tabFile` set file_url = %s, is_private = 1"):
			new_url, content_hash, old_url = values
			for f in files.values():
				if f.get("content_hash") == content_hash and f.get("file_url") == old_url:
					f.update(file_url=new_url, is_private=1)
			return ()
		if q.startswith("update `tabJob Interval Photo` set image = %s"):
			new_url, old_url = values
			for row in photos.values():
				if row.get("image") == old_url:
					row["image"] = new_url
			return ()
		if q.startswith("select name, parent, captured_on from `tabJob Interval Photo`"):
			return [
				_Dict(name=r["name"], parent=r["parent"], captured_on=r.get("captured_on"))
				for r in sorted(photos.values(), key=lambda r: (r["parent"], r.get("idx", 0)))
				if r.get("parenttype") == "Job Interval"
				and r.get("upload_status") == "Pending"
				and not r.get("file_doc")
			]
		if q.startswith("select f.name, f.file_name, f.file_url, f.is_private, f.creation from `tabFile` f"):
			(parent,) = values
			used = {r.get("file_doc") for r in photos.values() if r.get("file_doc")}
			return [
				_Dict({k: f.get(k) for k in ("name", "file_name", "file_url", "is_private", "creation")})
				for f in sorted(files.values(), key=lambda f: (f["creation"], f["name"]))
				if f.get("attached_to_doctype") == "Job Interval"
				and f.get("attached_to_name") == parent
				and not f.get("is_folder")
				and f["name"] not in used
			]
		if q.startswith("select count(*) from `tabJob Interval Photo` where parent = %s"):
			parent, states = values
			return [
				[
					sum(
						1
						for r in photos.values()
						if r["parent"] == parent and (r.get("upload_status") or "") in states
					)
				]
			]
		if q.startswith(
			"select count(*) from `tabFile` where attached_to_doctype = 'Job Interval' and is_private = 0"
		):
			return [[len(self._public_interval(values[0].rstrip("%")))]]
		if q.startswith("select count(*) from `tabFile` f where f.is_private = 0"):
			hashes = {
				f["content_hash"]
				for f in files.values()
				if f.get("attached_to_doctype") == "Job Interval" and f.get("content_hash")
			}
			return [
				[
					sum(
						1
						for f in files.values()
						if not f.get("is_private")
						and not f.get("is_folder")
						and f.get("attached_to_doctype") != "Job Interval"
						and f.get("content_hash") in hashes
					)
				]
			]
		if q.startswith("select count(*) from `tabJob Interval Photo` where parenttype = 'Job Interval'"):
			return [
				[
					sum(
						1
						for r in photos.values()
						if r.get("parenttype") == "Job Interval"
						and r.get("upload_status") == "Pending"
						and not r.get("file_doc")
					)
				]
			]
		raise AssertionError(f"unexpected sql: {q}")


class _FakeFile:
	"""A File document whose ``save`` behaves like frappe 16.53.0's on an ``is_private`` change."""

	def __init__(self, row):
		self.__dict__["_row"] = row
		self.__dict__["_was_private"] = row.get("is_private")

	def __getattr__(self, key):
		return self._row.get(key)

	def __setattr__(self, key, value):
		self.__dict__.setdefault("_pending", {})[key] = value

	def save(self, ignore_permissions=False):
		pending = self.__dict__.get("_pending", {})
		STATE["saves"].append(self._row["name"])
		if pending.get("is_private") and not self._was_private:
			file_name = self._row["file_url"].split("/")[-1]
			source = Path(STATE["site"], "public", "files", file_name)
			target = Path(STATE["site"], "private", "files", file_name)
			if not source.exists():
				raise FileNotFoundError(f"Cannot find file {source} on disk")
			if target.exists():
				raise FileExistsError(f"A file with same name {target} already exists")
			shutil.move(source, target)
			if self._row["name"] in STATE["fail_after_move"]:
				raise RuntimeError("simulated failure after the move")
			new_url = f"/private/files/{file_name}"
			db = STATE["db"]
			db.tables["File"][self._row["name"]].update(file_url=new_url, is_private=1)
			for other in db.tables["File"].values():
				if other["name"] != self._row["name"] and other.get("content_hash") == self._row.get(
					"content_hash"
				):
					other.update(file_url=new_url, is_private=1)
			self.__dict__["_row"] = db.tables["File"][self._row["name"]]


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		raise AssertionError("the patch never inserts a document")
	row = STATE["db"].tables[doctype].get(name)
	if row is None:
		raise _DoesNotExistError(name)
	return _FakeFile(dict(row)) if doctype == "File" else _Dict(row)


def _install_frappe_stub():
	for name in ("frappe", "frappe.utils", ROUTING_MODULE):
		_SAVED_MODULES[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe.db = None
	frappe._ = lambda s, *a, **k: s
	frappe.get_doc = _get_doc
	frappe.get_site_path = lambda *parts: os.path.join(STATE["site"], *parts)
	frappe.clear_messages = lambda: STATE["cleared_messages"].append(1)
	frappe.clear_document_cache = lambda doctype, name=None: STATE["cache_cleared"].append((doctype, name))
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or (a[0] if a else ""))
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.DoesNotExistError = _DoesNotExistError
	frappe.PermissionError = _PermissionError
	frappe.throw = _throw
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.session = _Dict(user=USER)
	utils = types.ModuleType("frappe.utils")
	utils.cint = _cint
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# route_job_photo is the real job's entry point; here it only records that it was called.
	routing = types.ModuleType(ROUTING_MODULE)
	routing.route_job_photo = lambda job_interval, file_name: STATE["routed"].append(
		(job_interval, file_name)
	)
	sys.modules[ROUTING_MODULE] = routing
	return frappe


FRAPPE = None


def setUpModule():
	global FRAPPE
	FRAPPE = _install_frappe_stub()
	for name in (PATCH_MODULE, KIOSK_FILES_MODULE):
		sys.modules.pop(name, None)


def tearDownModule():
	for name, module in _SAVED_MODULES.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module
	for name in (PATCH_MODULE, KIOSK_FILES_MODULE):
		sys.modules.pop(name, None)


def _reset(site):
	db = _FakeDb()
	FRAPPE.db = db
	FRAPPE.session = _Dict(user=USER)
	FRAPPE.get_doc = _get_doc
	STATE.clear()
	STATE.update(
		db=db,
		site=site,
		saves=[],
		fail_after_move=set(),
		cleared_messages=[],
		cache_cleared=[],
		errors=[],
		inserted=[],
		routed=[],
		employee=EMPLOYEE,
	)
	return db


def _patch():
	return importlib.import_module(PATCH_MODULE)


def _kiosk_files():
	return importlib.import_module(KIOSK_FILES_MODULE)


# ---------------------------------------------------------------------------
# The kiosk page
# ---------------------------------------------------------------------------


class TestTheKioskPage(unittest.TestCase):
	def setUp(self):
		self.js = APP_JS.read_text(encoding="utf-8")

	def test_add_files_uploads_privately(self):
		fn = _function(self.js, "uploadFile")
		self.assertIn("fd.append('is_private', '1');", fn)
		self.assertNotIn("'is_private', '0'", fn)

	def test_no_kiosk_upload_is_public(self):
		self.assertNotIn("'is_private', '0'", self.js)
		self.assertEqual(self.js.count("fd.append('is_private', '1');"), 2)

	def test_the_upload_hands_the_docname_and_its_interval_to_link_file(self):
		fn = _function(self.js, "uploadFile")
		self.assertIn("var interval = ci.name;", fn)
		self.assertIn("fd.append('docname', interval);", fn)
		self.assertIn("linkFile(data.message.name, interval)", fn)

	def test_link_file_passes_the_docname_not_the_display_name(self):
		"""The regression: the photo row was registered with ``r.file_name``."""
		fn = _function(self.js, "linkFile")
		self.assertIn("function linkFile(fileDoc, interval)", fn)
		self.assertIn("countAttachedImageAsJobPhoto(fileDoc, r.file_name || r.file_url, interval)", fn)
		self.assertNotRegex(fn, r"countAttachedImageAsJobPhoto\(\s*r\.file_name")

	def test_link_file_sends_no_project_or_task(self):
		"""The server takes them from the File's own interval; a job switched mid-upload cannot move them."""
		fn = _function(self.js, "linkFile")
		self.assertNotIn("project:", fn)
		self.assertNotIn("task:", fn)
		self.assertNotIn("app.currentInterval", fn)

	def test_the_photo_row_names_the_file_by_docname_on_its_upload_interval(self):
		fn = _function(self.js, "countAttachedImageAsJobPhoto")
		self.assertIn("job_interval: interval,", fn)
		self.assertIn("file_name: fileDoc,", fn)
		self.assertIn("interval = interval || ci.name;", fn)

	def test_the_image_test_reads_the_display_name(self):
		fn = _function(self.js, "countAttachedImageAsJobPhoto")
		self.assertIn(".test(String(displayName || ''))", fn)

	def test_the_chip_moves_only_for_the_current_job(self):
		fn = _function(self.js, "countAttachedImageAsJobPhoto")
		guard = fn.index("if (interval !== (app.currentInterval || {}).name) return;")
		self.assertLess(guard, fn.index("app.photoCount = "))


# ---------------------------------------------------------------------------
# The server, run from the real source against the stub
# ---------------------------------------------------------------------------

SERVER_FUNCTIONS = ("link_attachment", "_attach_photo_file", "_assert_kiosk_file", "_make_kiosk_file_private")


def _load_server_functions():
	tree = ast.parse(TIME_KIOSK.read_text(encoding="utf-8"))
	nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in SERVER_FUNCTIONS]
	if {n.name for n in nodes} != set(SERVER_FUNCTIONS):
		raise AssertionError(f"missing from time_kiosk.py: {set(SERVER_FUNCTIONS) - {n.name for n in nodes}}")
	namespace = {
		"frappe": FRAPPE,
		"_": lambda s, *a, **k: s,
		"cint": _cint,
		"kiosk_files": _kiosk_files(),
		"_session_employee": lambda: STATE.get("employee"),
	}
	exec(compile(ast.Module(body=nodes, type_ignores=[]), str(TIME_KIOSK), "exec"), namespace)
	return namespace


class _ServerFile:
	"""A File for the endpoint tests: records saves and inserts, never touches a disk."""

	def __init__(self, **fields):
		self.__dict__.update(fields)
		self.saved = 0

	def as_dict(self):
		return _Dict({k: v for k, v in self.__dict__.items() if k != "saved"})

	def save(self, ignore_permissions=False):
		self.saved += 1
		STATE["saves"].append(self.name)
		if self.is_private and self.file_url.startswith("/files/"):
			self.file_url = "/private/files/" + self.file_url.split("/")[-1]

	def insert(self, ignore_permissions=False):
		STATE["inserted"].append(dict(self.__dict__))


class _ServerDb:
	def __init__(self, intervals, files):
		self.intervals = intervals
		self.files = files
		self.set_values = []

	def get_value(self, doctype, name, fields, as_dict=False):
		assert doctype == "Job Interval" and fields == ["employee", "project", "task"] and as_dict
		return self.intervals.get(name)

	def exists(self, doctype, filters):
		return False

	def set_value(self, *args, **kwargs):
		self.set_values.append(args)

	def sql(self, query, values=(), as_dict=False):
		q = " ".join(query.split())
		assert q.startswith(
			"select name, owner, file_url, attached_to_doctype from `tabFile` where name != %s"
		), q
		return _sharing([vars(f) for f in self.files.values()], *values)


class _ServerCase(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.mkdtemp()
		_reset(self.tmp)
		self.files = {}
		self.server_db = _ServerDb(
			{
				INTERVAL: _Dict(employee=EMPLOYEE, project=PROJECT, task=TASK),
				OTHER_INTERVAL: _Dict(employee=OTHER_EMPLOYEE, project=OTHER_PROJECT, task=None),
			},
			self.files,
		)
		FRAPPE.db = self.server_db
		FRAPPE.get_doc = self._fake_get_doc
		self.ns = _load_server_functions()

	def tearDown(self):
		FRAPPE.get_doc = _get_doc
		shutil.rmtree(self.tmp, ignore_errors=True)

	def _fake_get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			return _ServerFile(**doctype)
		if name not in self.files:
			raise _DoesNotExistError(name)
		return self.files[name]

	def add_file(
		self,
		name,
		owner=USER,
		doctype="Job Interval",
		parent=INTERVAL,
		private=0,
		file_name=None,
		url=None,
		content_hash=None,
	):
		file_name = file_name or f"{name}.jpg"
		self.files[name] = _ServerFile(
			name=name,
			owner=owner,
			attached_to_doctype=doctype,
			attached_to_name=parent,
			is_private=private,
			file_name=file_name,
			file_url=url or (("/private/files/" if private else "/files/") + file_name),
			content_hash=content_hash or f"hash-{name}",
			folder="Home/Attachments",
			creation="2026-10-27 09:00:00",
		)
		return self.files[name]

	def website_asset_shared_by(self, kiosk_file):
		"""The site's logo, whose bytes and URL frappe reused for the kiosk upload."""
		return self.add_file(
			"site-logo",
			owner="Administrator",
			doctype="Website Settings",
			parent="Website Settings",
			url=kiosk_file.file_url,
			content_hash=kiosk_file.content_hash,
		)


class TestAssertKioskFile(_ServerCase):
	def test_a_file_on_this_interval_is_allowed_whoever_uploaded_it(self):
		self.ns["_assert_kiosk_file"](self.add_file("f-1", owner=OTHER_USER), INTERVAL)

	def test_the_callers_own_unattached_upload_is_allowed(self):
		self.ns["_assert_kiosk_file"](self.add_file("f-1", doctype=None, parent=None), INTERVAL)

	def test_somebody_elses_unattached_file_is_refused(self):
		with self.assertRaises(_PermissionError):
			self.ns["_assert_kiosk_file"](
				self.add_file("f-1", owner=OTHER_USER, doctype=None, parent=None), INTERVAL
			)

	def test_a_file_on_another_interval_is_refused_even_if_the_caller_uploaded_it(self):
		with self.assertRaises(_PermissionError):
			self.ns["_assert_kiosk_file"](self.add_file("f-1", parent=OTHER_INTERVAL), INTERVAL)

	def test_a_file_on_another_document_is_refused(self):
		with self.assertRaises(_PermissionError):
			self.ns["_assert_kiosk_file"](self.add_file("f-1", doctype="Project", parent=PROJECT), INTERVAL)

	def test_no_interval_allows_only_the_callers_unattached_upload(self):
		with self.assertRaises(_PermissionError):
			self.ns["_assert_kiosk_file"](self.add_file("f-1"), None)
		self.ns["_assert_kiosk_file"](self.add_file("f-2", doctype=None, parent=None), None)


class TestMakeKioskFilePrivate(_ServerCase):
	def test_a_public_upload_nothing_else_uses_is_made_private(self):
		file = self.add_file("f-1")
		self.ns["_make_kiosk_file_private"](file)
		self.assertEqual((file.is_private, file.saved), (1, 1))

	def test_its_own_project_and_task_copies_do_not_block_it(self):
		file = self.add_file("f-1")
		self.add_file(
			"f-1-prj", doctype="Project", parent=PROJECT, url=file.file_url, content_hash=file.content_hash
		)
		self.add_file(
			"f-1-task", doctype="Task", parent=TASK, url=file.file_url, content_hash=file.content_hash
		)
		self.ns["_make_kiosk_file_private"](file)
		self.assertEqual(file.saved, 1)

	def test_a_row_pointing_at_a_website_asset_is_refused_and_nothing_moves(self):
		"""M2: upload_file(file_url=/files/<logo>) on one's own interval, then the kiosk endpoint."""
		file = self.add_file("f-1", url="/files/logo.png", content_hash="hash-logo")
		self.website_asset_shared_by(file)
		with self.assertRaises(_PermissionError):
			self.ns["_make_kiosk_file_private"](file)
		self.assertEqual((file.is_private, file.saved), (0, 0))

	def test_the_same_bytes_under_another_owner_are_refused(self):
		file = self.add_file("f-1")
		self.add_file(
			"their-copy",
			owner=OTHER_USER,
			doctype="Task",
			parent=TASK,
			url=file.file_url,
			content_hash=file.content_hash,
		)
		with self.assertRaises(_PermissionError):
			self.ns["_make_kiosk_file_private"](file)

	def test_a_private_file_is_left_alone_without_a_lookup(self):
		file = self.add_file("f-1", private=1)
		self.server_db.sql = None  # any lookup would now fail
		self.ns["_make_kiosk_file_private"](file)
		self.assertEqual(file.saved, 0)


class TestAttachPhotoFile(_ServerCase):
	def interval(self, name=INTERVAL):
		return _Dict(name=name)

	def test_a_file_uploaded_to_the_interval_is_linked_and_made_private(self):
		file = self.add_file("f-1")
		row = _Dict(captured_on=None)
		self.assertTrue(self.ns["_attach_photo_file"](row, "f-1", self.interval()))
		self.assertEqual((file.is_private, file.saved), (1, 1))
		self.assertEqual(row.file_doc, "f-1")
		self.assertEqual(row.image, "/private/files/f-1.jpg")

	def test_a_display_name_finds_no_file_and_leaves_the_row_pending(self):
		"""What the kiosk sent until v1.751.1."""
		self.add_file("f-1", file_name="IMG_0001.jpeg")
		row = _Dict(captured_on=None)
		self.assertFalse(self.ns["_attach_photo_file"](row, "IMG_0001.jpeg", self.interval()))
		self.assertNotIn("file_doc", row)

	def test_somebody_elses_file_is_refused_before_anything_is_written(self):
		file = self.add_file("f-1", owner=OTHER_USER, doctype=None, parent=None)
		row = _Dict(captured_on=None)
		with self.assertRaises(_PermissionError):
			self.ns["_attach_photo_file"](row, "f-1", self.interval())
		self.assertEqual((file.is_private, file.saved), (0, 0))
		self.assertEqual(self.server_db.set_values, [])
		self.assertNotIn("file_doc", row)

	def test_a_file_on_another_job_is_refused(self):
		file = self.add_file("f-1", parent=OTHER_INTERVAL)
		with self.assertRaises(_PermissionError):
			self.ns["_attach_photo_file"](_Dict(captured_on=None), "f-1", self.interval())
		self.assertEqual(file.saved, 0)

	def test_a_row_of_ones_own_pointing_at_a_website_asset_is_refused(self):
		file = self.add_file("f-1", url="/files/logo.png", content_hash="hash-logo")
		self.website_asset_shared_by(file)
		row = _Dict(captured_on=None)
		with self.assertRaises(_PermissionError):
			self.ns["_attach_photo_file"](row, "f-1", self.interval())
		self.assertEqual((file.is_private, file.saved), (0, 0))
		self.assertNotIn("file_doc", row)

	def test_the_callers_own_unattached_upload_is_anchored_to_the_interval(self):
		self.add_file("f-1", doctype=None, parent=None)
		self.assertTrue(self.ns["_attach_photo_file"](_Dict(captured_on=None), "f-1", self.interval()))
		self.assertEqual(len(self.server_db.set_values), 1)
		self.assertEqual(self.server_db.set_values[0][2]["attached_to_name"], INTERVAL)


class TestLinkAttachment(_ServerCase):
	def test_copies_are_private_and_the_reply_names_the_file(self):
		self.add_file("f-1", private=1)
		reply = self.ns["link_attachment"]("f-1")
		self.assertEqual(reply["status"], "success")
		self.assertEqual(reply["name"], "f-1")
		self.assertEqual(
			[(c["attached_to_doctype"], c["is_private"]) for c in STATE["inserted"]],
			[("Project", 1), ("Task", 1)],
		)

	def test_copies_go_to_the_files_own_interval_whatever_the_page_sends(self):
		"""L1: a cached page still sends project and task; they are ignored."""
		self.add_file("f-1", private=1)
		self.ns["link_attachment"]("f-1", OTHER_PROJECT, "TASK-2026-99999")
		self.assertEqual(
			[(c["attached_to_doctype"], c["attached_to_name"]) for c in STATE["inserted"]],
			[("Project", PROJECT), ("Task", TASK)],
		)

	def test_a_public_original_is_made_private_before_it_is_copied(self):
		"""A page cached before v1.751.1 still uploads public."""
		file = self.add_file("f-1", private=0)
		self.ns["link_attachment"]("f-1")
		self.assertEqual((file.is_private, file.saved), (1, 1))
		self.assertEqual(STATE["inserted"][0]["file_url"], "/private/files/f-1.jpg")
		self.assertEqual(STATE["inserted"][0]["is_private"], 1)

	def test_a_row_pointing_at_a_website_asset_is_refused_and_not_copied(self):
		file = self.add_file("f-1", url="/files/logo.png", content_hash="hash-logo")
		self.website_asset_shared_by(file)
		with self.assertRaises(_PermissionError):
			self.ns["link_attachment"]("f-1")
		self.assertEqual((file.is_private, file.saved, STATE["inserted"]), (0, 0, []))

	def test_a_file_on_another_employees_interval_is_refused(self):
		file = self.add_file("f-1", owner=OTHER_USER, parent=OTHER_INTERVAL)
		with self.assertRaises(_PermissionError):
			self.ns["link_attachment"]("f-1")
		self.assertEqual((file.saved, STATE["inserted"]), (0, []))

	def test_a_file_not_on_an_interval_is_refused(self):
		"""Without an interval there is no Project or Task to copy it to."""
		for name, doctype, parent in (("f-1", None, None), ("f-2", "Project", PROJECT)):
			with self.subTest(attached_to=doctype):
				self.add_file(name, doctype=doctype, parent=parent)
				with self.assertRaises(_PermissionError):
					self.ns["link_attachment"](name)
		self.assertEqual(STATE["inserted"], [])

	def test_a_user_with_no_employee_is_refused(self):
		self.add_file("f-1")
		STATE["employee"] = None
		with self.assertRaises(_PermissionError):
			self.ns["link_attachment"]("f-1")

	def test_the_privacy_save_is_outside_the_try(self):
		"""A failure after frappe has moved the bytes must roll the request back, because that
		rollback is what moves them back; inside the try it would be swallowed and committed."""
		fn = next(
			n
			for n in ast.parse(TIME_KIOSK.read_text(encoding="utf-8")).body
			if isinstance(n, ast.FunctionDef) and n.name == "link_attachment"
		)
		for node in ast.walk(fn):
			if isinstance(node, ast.Try):
				self.assertNotIn("_make_kiosk_file_private", ast.unparse(node))
		self.assertIn("_make_kiosk_file_private(original)", ast.unparse(fn))


# ---------------------------------------------------------------------------
# workforce/kiosk_files: pure rules
# ---------------------------------------------------------------------------

T0 = datetime(2026, 10, 27, 9, 0, 0)


def _row(name, parent=INTERVAL, captured=None, idx=1, status="Pending", file_doc=None, image=None):
	return _Dict(
		name=name,
		parent=parent,
		parenttype="Job Interval",
		idx=idx,
		upload_status=status,
		file_doc=file_doc,
		image=image,
		captured_on=captured,
	)


def _f(name, created, file_name="photo.jpg", **extra):
	return _Dict(name=name, creation=created, file_name=file_name, file_url=f"/files/{file_name}", **extra)


class TestWhyNotMovable(unittest.TestCase):
	def setUp(self):
		self.why = _kiosk_files().why_not_movable
		self.file = _Dict(name="f-1", owner=USER, file_url="/files/shot.png")

	def other(self, **kwargs):
		return _Dict(
			{
				"name": "f-2",
				"owner": USER,
				"file_url": "/files/shot.png",
				"attached_to_doctype": "Project",
				**kwargs,
			}
		)

	def test_nothing_else_or_only_its_own_kiosk_rows(self):
		self.assertIsNone(self.why(self.file, []))
		rows = [self.other(attached_to_doctype=d) for d in ("Job Interval", "Project", "Task")]
		self.assertIsNone(self.why(self.file, rows))

	def test_a_same_url_website_row(self):
		self.assertIn(
			"outside the kiosk", self.why(self.file, [self.other(attached_to_doctype="Website Settings")])
		)
		self.assertIn("outside the kiosk", self.why(self.file, [self.other(attached_to_doctype=None)]))

	def test_a_row_of_somebody_elses(self):
		self.assertIn("somebody else", self.why(self.file, [self.other(owner=OTHER_USER)]))

	def test_the_same_contents_under_another_url(self):
		self.assertIn("another URL", self.why(self.file, [self.other(file_url="/files/Shot.png")]))


class TestMatchRowsToFiles(unittest.TestCase):
	def setUp(self):
		self.match = _kiosk_files().match_rows_to_files

	def test_a_row_written_just_after_its_upload_pairs_with_it(self):
		"""The production shape: upload, link_attachment, then the row, all within a second."""
		row = _row("r-1", captured=T0 + timedelta(milliseconds=850))
		file = _f("f-1", T0)
		self.assertEqual(self.match([row], [file]), [(row, file)])

	def test_a_string_timestamp_works_too(self):
		row = _row("r-1", captured="2026-10-27 09:00:00.807665")
		self.assertEqual(len(self.match([row], [_f("f-1", "2026-10-27 09:00:00.289730")])), 1)

	def test_a_camera_capture_with_no_file_stays_unpaired(self):
		self.assertEqual(self.match([_row("r-1", captured=T0)], []), [])

	def test_a_file_created_after_the_row_is_not_its_upload(self):
		self.assertEqual(self.match([_row("r-1", captured=T0)], [_f("f-1", T0 + timedelta(seconds=1))]), [])

	def test_the_window_is_ten_seconds(self):
		self.assertEqual(self.match([_row("r-1", captured=T0 + timedelta(seconds=11))], [_f("f-1", T0)]), [])
		self.assertEqual(
			len(self.match([_row("r-1", captured=T0 + timedelta(seconds=10))], [_f("f-1", T0)])), 1
		)

	def test_a_document_is_not_a_photo(self):
		row = _row("r-1", captured=T0 + timedelta(seconds=1))
		self.assertEqual(self.match([row], [_f("f-1", T0, file_name="quote.pdf")]), [])

	def test_two_candidate_files_pair_with_nothing(self):
		row = _row("r-1", captured=T0 + timedelta(seconds=5))
		self.assertEqual(self.match([row], [_f("f-1", T0), _f("f-2", T0 + timedelta(seconds=1))]), [])

	def test_two_rows_claiming_one_file_pair_with_nothing(self):
		rows = [
			_row("r-1", captured=T0 + timedelta(seconds=1)),
			_row("r-2", captured=T0 + timedelta(seconds=2)),
		]
		self.assertEqual(self.match(rows, [_f("f-1", T0)]), [])

	def test_two_uploads_a_minute_apart_pair_one_to_one(self):
		rows = [
			_row("r-1", captured=T0 + timedelta(seconds=1)),
			_row("r-2", captured=T0 + timedelta(minutes=1, seconds=1)),
		]
		files = [_f("f-1", T0), _f("f-2", T0 + timedelta(minutes=1))]
		self.assertEqual(
			[(r.name, f.name) for r, f in self.match(rows, files)], [("r-1", "f-1"), ("r-2", "f-2")]
		)

	def test_a_row_with_no_timestamp_pairs_with_nothing(self):
		self.assertEqual(self.match([_row("r-1", captured=None)], [_f("f-1", T0)]), [])

	def test_is_image(self):
		is_image = _kiosk_files().is_image
		self.assertTrue(is_image("IMG_0001.JPEG"))
		self.assertTrue(is_image("/files/screen shot.png"))
		self.assertFalse(is_image("quote.pdf"))
		self.assertFalse(is_image(None))


class TestFreePrivateName(unittest.TestCase):
	def setUp(self):
		self.free = _patch().free_private_name

	def test_frappes_own_form_first(self):
		self.assertEqual(self.free("image.jpg", "0123456789abcdef", lambda n: False), "imageabcdef.jpg")

	def test_numbered_after_it_when_that_is_taken(self):
		taken = {"imageabcdef.jpg", "imageabcdef-1.jpg"}
		self.assertEqual(self.free("image.jpg", "0123456789abcdef", taken.__contains__), "imageabcdef-2.jpg")

	def test_none_when_every_try_is_taken(self):
		self.assertIsNone(self.free("image.jpg", "0123456789abcdef", lambda n: True))


# ---------------------------------------------------------------------------
# The patch and the relink step: whole runs against the in-memory site
# ---------------------------------------------------------------------------


class _PatchCase(unittest.TestCase):
	def setUp(self):
		self.site = tempfile.mkdtemp()
		for folder in ("public", "private"):
			os.makedirs(os.path.join(self.site, folder, "files"))
		self.db = _reset(self.site)

	def tearDown(self):
		shutil.rmtree(self.site, ignore_errors=True)

	def disk(self, folder, name, content=b"bytes"):
		Path(self.site, folder, "files", name).write_bytes(content)

	def on_disk(self, folder, name):
		return Path(self.site, folder, "files", name).exists()

	def file(
		self,
		name,
		url,
		doctype="Job Interval",
		parent=INTERVAL,
		content_hash="hash-aaaaaa",
		private=0,
		created=T0,
		file_name=None,
		owner=USER,
	):
		self.db.tables["File"][name] = _Dict(
			name=name,
			owner=owner,
			file_url=url,
			file_name=file_name or url.split("/")[-1],
			is_private=private,
			is_folder=0,
			content_hash=content_hash,
			attached_to_doctype=doctype,
			attached_to_name=parent,
			creation=created,
		)

	def photo(self, name, parent=INTERVAL, **kwargs):
		self.db.tables["Job Interval Photo"][name] = _row(name, parent=parent, **kwargs)
		self.db.tables["Job Interval"].setdefault(parent, _Dict(name=parent, photos_pending_upload=1))

	def run_patch(self):
		with contextlib.redirect_stdout(io.StringIO()) as out:
			result = _patch().privatize_kiosk_attachment_files()
		return result, out.getvalue()

	def add_files_upload(
		self,
		stem="shot",
		interval=INTERVAL,
		project=PROJECT,
		task=TASK,
		content_hash="hash-aaaaaa",
		created=T0,
		row=True,
	):
		"""One "Add files" image as the kiosk stored it before v1.751.1."""
		url = f"/files/{stem}.png"
		self.disk("public", f"{stem}.png")
		self.file(f"{stem}-ji", url, parent=interval, content_hash=content_hash, created=created)
		self.file(f"{stem}-prj", url, doctype="Project", parent=project, content_hash=content_hash)
		if task:
			self.file(f"{stem}-task", url, doctype="Task", parent=task, content_hash=content_hash)
		if row:
			self.photo(f"{stem}-row", parent=interval, captured=created + timedelta(milliseconds=600))
		return url


class TestThePatchMakesFilesPrivate(_PatchCase):
	def test_one_save_per_file_takes_its_copies_private(self):
		self.add_files_upload()
		result, out = self.run_patch()
		files = self.db.tables["File"]
		self.assertEqual(
			{f.name: (f.is_private, f.file_url) for f in files.values()},
			{name: (1, "/private/files/shot.png") for name in ("shot-ji", "shot-prj", "shot-task")},
		)
		self.assertEqual(STATE["saves"], ["shot-ji"])
		self.assertTrue(self.on_disk("private", "shot.png"))
		self.assertFalse(self.on_disk("public", "shot.png"))
		self.assertEqual((result["made_private"], result["renamed"], result["problems"]), (1, 0, []))
		self.assertEqual(result["before"], {"public_files": 1, "public_copies": 2, "pending_rows": 1})
		self.assertEqual(result["after"], {"public_files": 0, "public_copies": 0, "pending_rows": 0})
		self.assertIn("before: 1 public Files on Job Intervals, 2 public copies", out)

	def test_a_photo_image_holding_the_old_url_is_repointed(self):
		url = self.add_files_upload(row=False)
		self.photo("cam-row", status="Uploaded", file_doc="shot-ji", image=url, captured=T0)
		self.run_patch()
		self.assertEqual(self.db.tables["Job Interval Photo"]["cam-row"]["image"], "/private/files/shot.png")

	def test_a_same_url_website_row_keeps_the_group_public(self):
		"""M1: frappe reused the logo's URL for a kiosk upload of the same bytes."""
		self.add_files_upload()
		self.file(
			"site-logo",
			"/files/shot.png",
			doctype="Website Settings",
			parent="Website Settings",
			owner="Administrator",
		)
		result, _ = self.run_patch()
		self.assertEqual(STATE["saves"], [])
		self.assertEqual({f.is_private for f in self.db.tables["File"].values()}, {0})
		self.assertTrue(self.on_disk("public", "shot.png"))
		self.assertIn("outside the kiosk", result["problems"][0])
		self.assertEqual(result["made_private"], 0)

	def test_an_unattached_row_with_the_same_bytes_keeps_the_group_public(self):
		self.add_files_upload()
		self.file("someones-upload", "/files/shot.png", doctype=None, parent=None, owner=OTHER_USER)
		result, _ = self.run_patch()
		self.assertEqual(STATE["saves"], [])
		self.assertIn("outside the kiosk", result["problems"][0])

	def test_a_copy_owned_by_somebody_else_keeps_the_group_public(self):
		self.add_files_upload()
		self.db.tables["File"]["shot-task"]["owner"] = OTHER_USER
		result, _ = self.run_patch()
		self.assertEqual(STATE["saves"], [])
		self.assertIn("somebody else", result["problems"][0])

	def test_contents_shared_under_another_url_are_left_alone(self):
		"""Frappe's sibling update would repoint that other file at this one."""
		self.add_files_upload()
		self.file("other-name", "/files/Shot.png", doctype="Task", parent=TASK)
		result, _ = self.run_patch()
		self.assertEqual(self.db.tables["File"]["shot-ji"]["is_private"], 0)
		self.assertEqual(self.db.tables["File"]["other-name"]["file_url"], "/files/Shot.png")
		self.assertTrue(self.on_disk("public", "shot.png"))
		self.assertIn("under another URL", result["problems"][0])

	def test_a_taken_name_moves_under_frappes_hash_suffix(self):
		"""Production already has /private/files/image.jpg; iOS names every capture that."""
		self.disk("public", "image.jpg", b"new photo")
		self.disk("private", "image.jpg", b"an older, different photo")
		self.file("img-ji", "/files/image.jpg", content_hash="hash-123456")
		self.file(
			"img-prj", "/files/image.jpg", doctype="Project", parent=PROJECT, content_hash="hash-123456"
		)
		result, _ = self.run_patch()
		files = self.db.tables["File"]
		self.assertEqual(
			{(f.is_private, f.file_url) for f in files.values()}, {(1, "/private/files/image123456.jpg")}
		)
		self.assertEqual(Path(self.site, "private", "files", "image123456.jpg").read_bytes(), b"new photo")
		self.assertEqual(
			Path(self.site, "private", "files", "image.jpg").read_bytes(), b"an older, different photo"
		)
		self.assertFalse(self.on_disk("public", "image.jpg"))
		self.assertEqual((result["made_private"], result["renamed"]), (1, 1))
		self.assertTrue(STATE["cleared_messages"])
		self.assertIn(("File", "img-ji"), STATE["cache_cleared"])
		self.assertIn(("File", "img-prj"), STATE["cache_cleared"])

	def test_a_new_name_a_file_row_already_holds_is_not_used(self):
		"""Free on disk is not enough: a row may already point at that private URL."""
		self.disk("public", "image.jpg")
		self.disk("private", "image.jpg")
		self.file("img-ji", "/files/image.jpg", content_hash="hash-123456")
		self.file(
			"ghost",
			"/private/files/image123456.jpg",
			doctype="Project",
			parent=OTHER_PROJECT,
			content_hash="hash-999999",
			private=1,
			owner=OTHER_USER,
		)
		self.run_patch()
		self.assertEqual(self.db.tables["File"]["img-ji"]["file_url"], "/private/files/image123456-1.jpg")
		self.assertTrue(self.on_disk("private", "image123456-1.jpg"))

	def test_a_failed_move_under_the_new_name_rolls_the_rows_back(self):
		self.disk("public", "image.jpg")
		self.disk("private", "image.jpg")
		self.file("img-ji", "/files/image.jpg", content_hash="hash-123456")
		patch = _patch()
		real_move = patch.shutil.move
		patch.shutil.move = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
		try:
			result, _ = self.run_patch()
		finally:
			patch.shutil.move = real_move
		self.assertEqual(self.db.tables["File"]["img-ji"]["file_url"], "/files/image.jpg")
		self.assertEqual(self.db.tables["File"]["img-ji"]["is_private"], 0)
		self.assertTrue(self.on_disk("public", "image.jpg"))
		self.assertEqual(result["made_private"], 0)
		self.assertIn("could not move it under a new name", result["problems"][0])

	def test_a_save_that_fails_after_the_move_puts_the_bytes_back(self):
		"""A savepoint rollback restores the rows but never the disk."""
		self.add_files_upload()
		STATE["fail_after_move"].add("shot-ji")
		result, _ = self.run_patch()
		self.assertTrue(self.on_disk("public", "shot.png"))
		self.assertFalse(self.on_disk("private", "shot.png"))
		self.assertEqual({f.is_private for f in self.db.tables["File"].values()}, {0})
		self.assertEqual(result["made_private"], 0)
		self.assertIn("RuntimeError", result["problems"][0])
		self.assertIn("Kiosk attachment privacy patch skipped some files", STATE["errors"])

	def test_one_failing_file_does_not_stop_the_others(self):
		self.add_files_upload("one", content_hash="hash-111111", created=T0)
		self.add_files_upload("two", content_hash="hash-222222", created=T0 + timedelta(minutes=10))
		STATE["fail_after_move"].add("one-ji")
		result, _ = self.run_patch()
		self.assertEqual(self.db.tables["File"]["two-ji"]["is_private"], 1)
		self.assertEqual(self.db.tables["File"]["one-ji"]["is_private"], 0)
		self.assertEqual(result["made_private"], 1)

	def test_a_file_missing_from_disk_is_reported_not_saved(self):
		self.file("gone-ji", "/files/gone.png")
		result, _ = self.run_patch()
		self.assertEqual(STATE["saves"], [])
		self.assertIn("is not on disk", result["problems"][0])

	def test_a_file_with_no_hash_is_reported_not_saved(self):
		self.disk("public", "nohash.png")
		self.file("nohash-ji", "/files/nohash.png", content_hash=None)
		result, _ = self.run_patch()
		self.assertEqual(STATE["saves"], [])
		self.assertIn("no content_hash", result["problems"][0])

	def test_files_on_other_documents_private_files_and_remote_urls_are_not_touched(self):
		self.disk("public", "brochure.pdf")
		self.file(
			"brochure", "/files/brochure.pdf", doctype="Project", parent=PROJECT, content_hash="hash-999999"
		)
		self.file("cam-ji", "/private/files/cam.jpg", private=1, content_hash="hash-888888")
		self.file("link-ji", "https://example.com/plan.pdf", content_hash=None)
		result, _ = self.run_patch()
		self.assertEqual(STATE["saves"], [])
		self.assertEqual(self.db.tables["File"]["brochure"]["is_private"], 0)
		self.assertEqual(result["made_private"], 0)

	def test_the_public_count_is_exactly_the_selection(self):
		"""L4: a remote URL on an interval is neither acted on nor counted."""
		self.file("link-ji", "https://example.com/plan.pdf", content_hash=None)
		result, _ = self.run_patch()
		self.assertEqual(result["before"]["public_files"], 0)

	def test_it_commits_after_every_file(self):
		self.add_files_upload("one", content_hash="hash-111111", row=False)
		self.add_files_upload("two", content_hash="hash-222222", row=False)
		self.run_patch()
		self.assertGreaterEqual(self.db.commits, 2)


class TestThePatchLinksStuckRows(_PatchCase):
	def test_the_stuck_row_is_linked_routed_and_the_flag_cleared(self):
		self.add_files_upload()
		result, _ = self.run_patch()
		row = self.db.tables["Job Interval Photo"]["shot-row"]
		self.assertEqual(
			(row.file_doc, row.image, row.upload_status), ("shot-ji", "/private/files/shot.png", "Uploaded")
		)
		self.assertEqual(self.db.tables["Job Interval"][INTERVAL]["photos_pending_upload"], 0)
		self.assertIn(("Job Interval", INTERVAL), STATE["cache_cleared"])
		self.assertEqual(STATE["routed"], [(INTERVAL, "shot-ji")])
		self.assertEqual(result["linked"], 1)

	def test_a_camera_capture_whose_bytes_never_arrived_stays_pending(self):
		self.photo("lost-1", parent=OTHER_INTERVAL, captured=T0)
		self.photo("lost-2", parent=OTHER_INTERVAL, captured=T0 + timedelta(seconds=30), idx=2)
		result, _ = self.run_patch()
		self.assertEqual(
			{r.upload_status for r in self.db.tables["Job Interval Photo"].values()}, {"Pending"}
		)
		self.assertEqual(self.db.tables["Job Interval"][OTHER_INTERVAL]["photos_pending_upload"], 1)
		self.assertEqual((result["linked"], STATE["routed"]), (0, []))

	def test_the_flag_stays_up_while_another_row_is_unsettled(self):
		self.add_files_upload()
		self.photo("lost", captured=T0 - timedelta(hours=1), idx=2)
		self.run_patch()
		self.assertEqual(self.db.tables["Job Interval"][INTERVAL]["photos_pending_upload"], 1)

	def test_a_file_another_row_already_holds_is_not_linked_again(self):
		self.disk("private", "cam.jpg")
		self.file("cam-ji", "/private/files/cam.jpg", private=1, content_hash="hash-777777", created=T0)
		self.photo(
			"cam-row", status="Uploaded", file_doc="cam-ji", image="/private/files/cam.jpg", captured=T0
		)
		self.photo("stuck", captured=T0 + timedelta(seconds=1), idx=2)
		self.run_patch()
		self.assertIsNone(self.db.tables["Job Interval Photo"]["stuck"]["file_doc"])

	def test_a_row_whose_file_stayed_public_is_not_linked_or_routed(self):
		self.add_files_upload()
		STATE["fail_after_move"].add("shot-ji")
		result, _ = self.run_patch()
		self.assertEqual(self.db.tables["Job Interval Photo"]["shot-row"]["upload_status"], "Pending")
		self.assertTrue(any("still public" in p for p in result["problems"]))
		self.assertEqual(STATE["routed"], [])

	def test_a_failing_write_rolls_that_interval_back_and_the_rest_go_on(self):
		self.add_files_upload("one", interval=INTERVAL, content_hash="hash-111111")
		self.add_files_upload("two", interval=OTHER_INTERVAL, content_hash="hash-222222")
		self.db.fail_set_value_for.add("one-row")
		result, _ = self.run_patch()
		self.assertEqual(self.db.tables["Job Interval Photo"]["one-row"]["upload_status"], "Pending")
		self.assertEqual(self.db.tables["Job Interval Photo"]["two-row"]["upload_status"], "Uploaded")
		self.assertEqual(result["linked"], 1)


class TestTheRelinkStepIsReusable(_PatchCase):
	"""L2: a device's cached page goes on registering by display name for a session or more."""

	def test_a_row_written_after_the_patch_is_linked_by_a_later_run(self):
		self.add_files_upload()
		self.run_patch()
		# The cached page uploads, link_attachment makes it private, and the row is registered by name.
		self.disk("private", "late.png")
		self.file(
			"late-ji",
			"/private/files/late.png",
			private=1,
			content_hash="hash-555555",
			created=T0 + timedelta(hours=20),
		)
		self.photo("late-row", captured=T0 + timedelta(hours=20, seconds=1), idx=2)
		STATE["routed"].clear()
		result = _kiosk_files().link_stuck_photo_rows()
		self.assertEqual(result, {"linked": 1, "problems": []})
		self.assertEqual(self.db.tables["Job Interval Photo"]["late-row"]["file_doc"], "late-ji")
		self.assertEqual(STATE["routed"], [(INTERVAL, "late-ji")])

	def test_a_second_run_links_nothing(self):
		self.add_files_upload()
		self.run_patch()
		self.db.writes.clear()
		STATE["routed"].clear()
		self.assertEqual(_kiosk_files().link_stuck_photo_rows(), {"linked": 0, "problems": []})
		self.assertEqual((self.db.writes, STATE["routed"]), ([], []))

	def test_routing_can_be_turned_off(self):
		self.disk("private", "a.png")
		self.file("a-ji", "/private/files/a.png", private=1)
		self.photo("a-row", captured=T0 + timedelta(seconds=1))
		_kiosk_files().link_stuck_photo_rows(route=False)
		self.assertEqual(STATE["routed"], [])
		self.assertEqual(self.db.tables["Job Interval Photo"]["a-row"]["upload_status"], "Uploaded")


class TestThePatchIsSafe(_PatchCase):
	def test_a_second_run_writes_nothing(self):
		self.add_files_upload()
		self.run_patch()
		tables = copy.deepcopy(self.db.tables)
		STATE["saves"].clear()
		STATE["routed"].clear()
		self.db.writes.clear()
		result, _ = self.run_patch()
		self.assertEqual(self.db.tables, tables)
		self.assertEqual((STATE["saves"], self.db.writes, STATE["routed"]), ([], [], []))
		self.assertEqual((result["made_private"], result["linked"], result["problems"]), (0, 0, []))

	def test_execute_never_raises(self):
		patch = _patch()
		real = patch._counts
		patch._counts = lambda: (_ for _ in ()).throw(RuntimeError("database gone"))
		try:
			with contextlib.redirect_stdout(io.StringIO()) as out:
				patch.execute()
		finally:
			patch._counts = real
		self.assertIn("aborted early", out.getvalue())
		self.assertIn("Kiosk attachment privacy patch failed", STATE["errors"])

	def test_the_patch_is_registered_after_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text.split("[post_model_sync]", 1)[1]
		self.assertIn(PATCH_MODULE + "\n", post)

	def test_the_tests_use_no_real_docnames(self):
		"""The repo is public."""
		source = Path(__file__).read_text(encoding="utf-8")
		self.assertIsNone(re.search(r"JOB-INT-000\d\d|PRJ-00[0-8]\d\d", source))


if __name__ == "__main__":
	unittest.main()

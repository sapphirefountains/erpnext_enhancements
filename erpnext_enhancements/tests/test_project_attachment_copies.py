"""Bench-free tests for one copy of each file on a Project made from an Opportunity (v1.749.1).

The bug: creating a Project copied every Opportunity attachment twice. The Project ``on_update``
hook ``sync_attachments_from_opportunity`` copied them inside the creation's ``project.insert``,
then the creation job's own loop in ``crm_enhancements/api.py`` copied every one again without
looking. The hook also compared names only against what the Project held before its loop, so a
file the Opportunity carries twice came over twice from each path: four rows of one PDF on
PRJ-00777, and 71 extra rows on 20 Projects by 2026-10-27.

Covered here:
* the two pure rules in ``utils/attachment_copies`` (``files_to_copy``, ``extra_copies``);
* the hook, run against an in-memory ``tabFile``, including a second save;
* the one-shot cleanup ``patches/dedupe_project_attachment_rows``, which must keep one row per
  stored file, never touch folders, URL-less rows or the Opportunity's own rows, never delete a
  stored file, survive a failing Project and be safe twice;
* that the creation job's step and ``patches.txt`` still use them.

The job itself is run in ``test_project_creation_progress``. A minimal ``frappe`` stub is installed
in ``setUpModule`` and removed afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_project_attachment_copies -v
"""

import contextlib
import importlib
import io
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
RULES_MODULE = "erpnext_enhancements.utils.attachment_copies"
PATCH_MODULE = "erpnext_enhancements.patches.dedupe_project_attachment_rows"
HOOK_PACKAGE = "erpnext_enhancements.project_enhancements"

#: The in-memory ``tabFile`` and what the stub saw.
STATE = {}
_SAVED_MODULES = {}
#: Why ``project_enhancements`` would not import under the stub, if it would not.
HOOK_IMPORT_ERROR = None


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key)

	def __setattr__(self, key, value):
		self[key] = value


def _matches(row, filters):
	for key, want in (filters or {}).items():
		value = row.get(key)
		if isinstance(want, (list, tuple)) and len(want) == 2 and want[0] == "in":
			if value not in want[1]:
				return False
		elif value != want:
			return False
	return True


def _file(name, parent, url, file_name=None, creation="2026-10-27 09:00:00", doctype="Project", **extra):
	return _Dict(
		name=name,
		attached_to_doctype=doctype,
		attached_to_name=parent,
		file_url=url,
		file_name=file_name if file_name is not None else (url or "").rsplit("/", 1)[-1],
		is_private=1,
		is_folder=extra.pop("is_folder", 0),
		creation=creation,
		folder=extra.pop("folder", "Home/Attachments"),
		**extra,
	)


class _FakeDb:
	def sql(self, query, values=None, as_dict=False):
		STATE["sql"].append(query)
		if STATE.get("sql_raises") and STATE["sql_raises"](query, values):
			raise RuntimeError("the database is unhappy")
		files = STATE["files"]
		text = " ".join(query.split())
		if text.startswith("select count(*) from `tabFile`"):
			return [[sum(1 for f in files if f.attached_to_doctype == "Project" and not f.is_folder)]]
		if "group by attached_to_name, file_url having count(*) > 1" in text:
			groups = {}
			for f in files:
				if f.attached_to_doctype == "Project" and not f.is_folder and (f.file_url or "") != "":
					# MariaDB's default collation is PAD SPACE and case-insensitive: the candidates are
					# found loosely, and extra_copies decides exactly.
					key = (f.attached_to_name, (f.file_url or "").rstrip().lower())
					groups.setdefault(key, 0)
					groups[key] += 1
			return [(parent,) for (parent, _url), count in groups.items() if count > 1]
		if "order by creation, name" in text:
			(parent,) = values
			rows = [
				f
				for f in files
				if f.attached_to_doctype == "Project"
				and f.attached_to_name == parent
				and not f.is_folder
				and (f.file_url or "") != ""
			]
			rows.sort(key=lambda f: (f.creation, f.name))
			return [_Dict(name=f.name, file_url=f.file_url) for f in rows] if as_dict else rows
		raise AssertionError(f"unexpected SQL: {text}")

	def delete(self, doctype, filters):
		assert doctype == "File", doctype
		STATE["deletes"].append(filters)
		STATE["files"] = [f for f in STATE["files"] if not _matches(f, filters)]

	def savepoint(self, name):
		STATE["savepoints"].append(name)

	def rollback(self, save_point=None):
		STATE["rollbacks"].append(save_point)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.db = _FakeDb()
	frappe._ = lambda text, *args, **kwargs: text

	def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, **kwargs):
		assert doctype == "File", doctype
		rows = [f for f in STATE["files"] if _matches(f, filters)]
		if order_by == "creation asc":
			rows.sort(key=lambda f: (f.creation, f.name))
		if pluck:
			return [row.get(pluck) for row in rows]
		return [_Dict({key: row.get(key) for key in (fields or row.keys())}) for row in rows]

	class _NewFile:
		def __init__(self, values):
			self.values = _Dict(values)

		def insert(self, ignore_permissions=False):
			STATE["counter"] += 1
			row = _file(f"NEW-{STATE['counter']}", self.values.attached_to_name, self.values.file_url)
			row.update(self.values)
			row.creation = f"2026-10-27 10:00:{STATE['counter']:02d}"
			STATE["files"].append(row)
			STATE["inserted"].append(row)
			return self

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			assert doctype["doctype"] == "File", doctype
			return _NewFile(doctype)
		if doctype == "Opportunity":
			return types.SimpleNamespace(opportunity_from="Customer", party_name="Acme Water Co")
		raise AssertionError(f"unexpected get_doc({doctype!r})")

	def delete_doc(*args, **kwargs):
		STATE["delete_doc"].append((args, kwargs))

	frappe.get_all = get_all
	frappe.get_doc = get_doc
	frappe.delete_doc = delete_doc
	frappe.log_error = lambda title=None, message=None, **kwargs: STATE["errors"].append((title, message))
	frappe.get_traceback = lambda: "Traceback"
	frappe.msgprint = lambda message, *args, **kwargs: STATE["messages"].append(message)
	frappe.clear_document_cache = lambda doctype, name=None: STATE["cleared"].append((doctype, name))
	frappe.whitelist = (
		lambda *args, **kwargs: (lambda fn: fn) if not (args and callable(args[0])) else args[0]
	)
	frappe.utils = types.ModuleType("frappe.utils")
	frappe.utils.nowdate = lambda: "2026-10-27"
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils
	return frappe


def setUpModule():
	global rules, patch, hook_package, HOOK_IMPORT_ERROR
	for name in ("frappe", "frappe.utils", RULES_MODULE, PATCH_MODULE):
		_SAVED_MODULES[name] = sys.modules.get(name)
	_install_frappe_stub()
	rules = importlib.import_module(RULES_MODULE)
	patch = importlib.import_module(PATCH_MODULE)
	hook_package = None
	try:
		_SAVED_MODULES[HOOK_PACKAGE] = sys.modules.get(HOOK_PACKAGE)
		hook_package = importlib.import_module(HOOK_PACKAGE)
	except Exception as exc:  # pragma: no cover - reported by the hook test
		HOOK_IMPORT_ERROR = exc


def tearDownModule():
	for name, module in _SAVED_MODULES.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset(files=()):
	STATE.clear()
	STATE.update(
		files=[_Dict(f) for f in files],
		sql=[],
		deletes=[],
		savepoints=[],
		rollbacks=[],
		errors=[],
		messages=[],
		cleared=[],
		delete_doc=[],
		inserted=[],
		counter=0,
	)


def _project_rows(parent):
	return sorted(
		(f for f in STATE["files"] if f.attached_to_doctype == "Project" and f.attached_to_name == parent),
		key=lambda f: f.name,
	)


SPECS = "/private/files/Design Specs - Waterfall Feature.pdf"
ESTIMATE = "/private/files/Estimate 1655.pdf"
PLANS = "/private/files/CONSTRUCTION SET.pdf"


class TestFilesToCopy(unittest.TestCase):
	def test_a_file_the_record_holds_by_url_or_name_is_skipped(self):
		existing = [
			{"file_url": ESTIMATE, "file_name": "Estimate 1655.pdf"},
			{"file_url": "/x", "file_name": "notes.txt"},
		]
		sources = [
			{"file_url": ESTIMATE, "file_name": "renamed.pdf"},
			{"file_url": "/private/files/other.txt", "file_name": "notes.txt"},
			{"file_url": PLANS, "file_name": "CONSTRUCTION SET.pdf"},
		]
		self.assertEqual([row["file_url"] for row in rules.files_to_copy(existing, sources)], [PLANS])

	def test_a_file_the_sources_carry_twice_is_picked_once_in_source_order(self):
		sources = [
			{"file_url": SPECS, "file_name": "Design Specs - Waterfall Feature.pdf"},
			{"file_url": ESTIMATE, "file_name": "Estimate 1655.pdf"},
			{"file_url": SPECS, "file_name": "Design Specs - Waterfall Feature.pdf"},
		]
		self.assertEqual([row["file_url"] for row in rules.files_to_copy([], sources)], [SPECS, ESTIMATE])

	def test_two_sources_that_only_share_a_name_are_both_picked(self):
		sources = [
			{"file_url": "/private/files/Site plan.pdf", "file_name": "Site plan.pdf"},
			{"file_url": "https://drive.example/site-plan", "file_name": "Site plan.pdf"},
		]
		self.assertEqual(len(rules.files_to_copy([], sources)), 2)
		# Against what the record already holds, the name still counts.
		self.assertEqual(rules.files_to_copy([sources[0]], sources), [])

	def test_two_url_less_sources_with_one_name_are_picked_once(self):
		sources = [{"file_url": "", "file_name": "notes.txt"}, {"file_url": None, "file_name": "notes.txt"}]
		self.assertEqual(len(rules.files_to_copy([], sources)), 1)

	def test_a_row_with_no_url_and_no_name_is_skipped(self):
		self.assertEqual(rules.files_to_copy([], [{"file_url": "", "file_name": None}]), [])
		self.assertEqual(rules.files_to_copy(None, None), [])


class TestExtraCopies(unittest.TestCase):
	def test_the_first_row_of_each_url_is_kept_and_the_rest_dropped(self):
		rows = [
			{"name": "A", "file_url": SPECS},
			{"name": "B", "file_url": ESTIMATE},
			{"name": "C", "file_url": SPECS},
			{"name": "D", "file_url": SPECS},
			{"name": "E", "file_url": ESTIMATE},
		]
		self.assertEqual(rules.extra_copies(rows), ["C", "D", "E"])

	def test_only_an_exact_url_counts(self):
		rows = [
			{"name": "A", "file_url": "/private/files/plan.pdf"},
			{"name": "B", "file_url": "/private/files/PLAN.pdf"},
			{"name": "C", "file_url": "/private/files/plan.pdf "},
			{"name": "D", "file_url": "/private/files/elsewhere/plan.pdf"},
		]
		self.assertEqual(
			rules.extra_copies(rows), [], "case, a trailing space or another folder is another file"
		)

	def test_a_row_with_no_url_is_never_dropped(self):
		rows = [
			{"name": "A", "file_url": ""},
			{"name": "B", "file_url": None},
			{"name": "C", "file_url": "  "},
		]
		self.assertEqual(rules.extra_copies(rows), [])


class TestTheHook(unittest.TestCase):
	def setUp(self):
		if hook_package is None:
			self.fail(f"project_enhancements did not import under the stub: {HOOK_IMPORT_ERROR!r}")
		_reset(
			[
				_file("O-1", "CRM-OPP-1", SPECS, creation="2026-04-25 12:08:32", doctype="Opportunity"),
				_file("O-2", "CRM-OPP-1", SPECS, creation="2026-04-25 12:08:59", doctype="Opportunity"),
				_file("O-3", "CRM-OPP-1", ESTIMATE, creation="2026-10-27 09:20:27", doctype="Opportunity"),
			]
		)
		self.project = types.SimpleNamespace(
			name="PRJ-0001", get=lambda key: {"custom_opportunity": "CRM-OPP-1"}.get(key)
		)

	def test_each_file_arrives_once_even_when_the_opportunity_holds_it_twice(self):
		hook_package.sync_attachments_from_opportunity(self.project, "on_update")
		self.assertEqual(sorted(f.file_url for f in _project_rows("PRJ-0001")), sorted([SPECS, ESTIMATE]))

	def test_a_later_save_copies_nothing_more(self):
		hook_package.sync_attachments_from_opportunity(self.project, "on_update")
		hook_package.sync_attachments_from_opportunity(self.project, "on_update")
		self.assertEqual(len(_project_rows("PRJ-0001")), 2)

	def test_after_the_hook_the_jobs_rule_finds_nothing_left_to_copy(self):
		hook_package.sync_attachments_from_opportunity(self.project, "on_update")
		held = [f for f in STATE["files"] if f.attached_to_doctype == "Project"]
		sources = [f for f in STATE["files"] if f.attached_to_doctype == "Opportunity"]
		self.assertEqual(rules.files_to_copy(held, sources), [])

	def test_the_opportunitys_own_rows_are_untouched(self):
		hook_package.sync_attachments_from_opportunity(self.project, "on_update")
		self.assertEqual(
			sorted(f.name for f in STATE["files"] if f.attached_to_doctype == "Opportunity"),
			["O-1", "O-2", "O-3"],
		)


def _prj_00777_shape():
	"""Four rows of one PDF, two of the others, beside rows the patch must leave alone."""
	return [
		_file("F-01", "PRJ-0777", SPECS, creation="2026-10-27 09:23:48.146"),
		_file("F-02", "PRJ-0777", SPECS, creation="2026-10-27 09:23:48.322"),
		_file("F-03", "PRJ-0777", SPECS, creation="2026-10-27 09:23:48.708"),
		_file("F-04", "PRJ-0777", SPECS, creation="2026-10-27 09:23:48.713"),
		_file("F-05", "PRJ-0777", ESTIMATE, creation="2026-10-27 09:23:47.516"),
		_file("F-06", "PRJ-0777", ESTIMATE, creation="2026-10-27 09:23:48.421"),
		_file("F-07", "PRJ-0777", PLANS, creation="2026-10-27 09:23:47.710"),
		_file("F-08", "PRJ-0777", PLANS, creation="2026-10-27 09:23:48.434"),
		# Not duplicates by URL: same name, another stored file.
		_file("F-09", "PRJ-0777", "/private/files/photo.jpg", file_name="photo.jpg"),
		_file("F-10", "PRJ-0777", "/private/files/photo-2.jpg", file_name="photo.jpg"),
		# Folders and URL-less rows are never touched, even when they repeat.
		_file("F-11", "PRJ-0777", "https://drive.example/folder/1", is_folder=1),
		_file("F-12", "PRJ-0777", "https://drive.example/folder/1", is_folder=1),
		_file("F-13", "PRJ-0777", "", file_name="Design (folder)"),
		_file("F-14", "PRJ-0777", "", file_name="Design (folder)"),
		# Another Project, clean.
		_file("F-20", "PRJ-0800", ESTIMATE),
		# The Opportunity's own rows point at the same stored files.
		_file("O-1", "CRM-OPP-1", SPECS, doctype="Opportunity"),
		_file("O-2", "CRM-OPP-1", SPECS, doctype="Opportunity"),
	]


class TestThePatch(unittest.TestCase):
	def setUp(self):
		_reset(_prj_00777_shape())

	def _run(self):
		with contextlib.redirect_stdout(io.StringIO()) as out:
			result = patch.dedupe_project_attachment_rows()
		return result, out.getvalue()

	def test_one_row_per_stored_file_the_oldest_kept(self):
		result, printed = self._run()
		kept = {f.name for f in _project_rows("PRJ-0777")}
		self.assertEqual(kept, {"F-01", "F-05", "F-07", "F-09", "F-10", "F-11", "F-12", "F-13", "F-14"})
		self.assertEqual((result["removed"], result["projects"], result["failed"]), (5, 1, []))
		self.assertEqual((result["before"], result["after"]), (13, 8))
		self.assertIn("Project File rows 13 -> 8; removed 5 extra copies from 1 Projects", printed)

	def test_it_never_deletes_a_stored_file_or_another_records_row(self):
		self._run()
		self.assertEqual(STATE["delete_doc"], [], "File.on_trash would remove the stored file")
		for filters in STATE["deletes"]:
			self.assertEqual(filters["attached_to_doctype"], "Project")
		self.assertEqual(
			sorted(f.name for f in STATE["files"] if f.attached_to_doctype == "Opportunity"), ["O-1", "O-2"]
		)
		self.assertEqual([f.name for f in _project_rows("PRJ-0800")], ["F-20"])
		# Every URL a deleted row pointed at is still referenced by a kept row.
		urls = {f.file_url for f in STATE["files"]}
		self.assertTrue({SPECS, ESTIMATE, PLANS} <= urls)

	def test_only_the_changed_project_is_cleared_from_the_cache(self):
		self._run()
		self.assertEqual(STATE["cleared"], [("Project", "PRJ-0777")])

	def test_a_second_run_writes_nothing(self):
		self._run()
		STATE["deletes"].clear()
		result, _printed = self._run()
		self.assertEqual((result["removed"], STATE["deletes"]), (0, []))

	def test_a_failing_project_rolls_back_and_the_rest_go_on(self):
		STATE["files"].extend(
			[_file("G-1", "PRJ-0900", PLANS), _file("G-2", "PRJ-0900", PLANS, creation="2026-10-28")]
		)
		STATE["sql_raises"] = lambda query, values: values == ("PRJ-0777",)
		result, _printed = self._run()
		self.assertEqual(result["failed"], ["PRJ-0777: RuntimeError"])
		self.assertIn(patch.SAVEPOINT, STATE["rollbacks"])
		self.assertEqual([f.name for f in _project_rows("PRJ-0900")], ["G-1"])
		self.assertEqual(len(_project_rows("PRJ-0777")), 14, "the failed Project is left whole")
		self.assertTrue(any("skipped some Projects" in (title or "") for title, _m in STATE["errors"]))

	def test_execute_never_raises(self):
		STATE["sql_raises"] = lambda query, values: True
		with contextlib.redirect_stdout(io.StringIO()) as out:
			patch.execute()
		self.assertIn("aborted early", out.getvalue())


class TestTheWiring(unittest.TestCase):
	def test_the_creation_jobs_step_asks_the_rule(self):
		source = (APP / "crm_enhancements" / "api.py").read_text(encoding="utf-8")
		step = source.split('step = "attachments"', 1)[1].split('step = "link"', 1)[0]
		self.assertIn("attachment_copies.files_to_copy(held, attachments)", step)
		self.assertIn('"attached_to_doctype": "Project"', step, "the rule needs what the Project holds")

	def test_the_hook_asks_the_rule(self):
		source = (APP / "project_enhancements" / "__init__.py").read_text(encoding="utf-8")
		body = source.split("def sync_attachments_from_opportunity", 1)[1].split("\ndef ", 1)[0]
		self.assertIn("attachment_copies.files_to_copy(existing_files, source_files)", body)

	def test_the_patch_is_registered_after_model_sync(self):
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		post = patches.split("[post_model_sync]", 1)[1]
		self.assertIn("erpnext_enhancements.patches.dedupe_project_attachment_rows", post)
		readme = (APP / "patches" / "README.md").read_text(encoding="utf-8")
		self.assertRegex(readme, re.compile(r"^\| `dedupe_project_attachment_rows` \| post \|", re.M))


if __name__ == "__main__":
	unittest.main()

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's locked schema: drafts are never readable by a reader role. Bench-free.

WI-080 PR 1, ADR 0017. The Knowledge Base is private by construction, and the construction is
two DocType JSONs and two controllers. Every property below is one a later edit could loosen
without anything else failing, because CI runs no migrate and prod is the only place a permission
row is ever exercised:

* **The flags.** No web view, no guest view, not in global search, attachments private by
  default, no import, and ``track_changes`` off on the Version doctype, because core ``Version``
  rows are readable by System Manager, which includes the ``triton@`` service identity.
* **The exact DocPerm matrix.** Article: ``Desk User`` read/report/print, System Manager read.
  Version: KB Author and KB Approver read/create/write/print/report at level 0, and read only at
  level 1, where every server-set field sits (v16 enforces permlevel on a save and never
  ``read_only``, so this is what stops a KB role rewriting ``contributors``). ``share`` is 0 everywhere,
  because v16 ``assign_to.add`` shares a document with an assignee who cannot read it; nobody may
  submit, cancel, amend, delete, export, import or email; and no System Manager, ``Desk User``,
  ``All`` or ``Guest`` row exists on the Version doctype. Compared as a whole, so an added row
  fails as surely as a changed one.
* **Nothing outside the JSON widens it**: no Custom DocPerm, Property Setter or Custom Field
  fixture targets either doctype.
* **The Select options** are exactly ``knowledge_base/constants.py``, which the publishing code
  writes, and a required Select with no default starts blank, because v16 otherwise defaults it
  to its first option and ``reqd`` never fires. ``review_every_months`` defaults to
  ``constants.DEFAULT_REVIEW_EVERY_MONTHS`` on both doctypes, the cadence POL-0001 sets.
* **The controllers** carry the class names Frappe derives (a mismatch gets a DocType
  force-deleted on migrate), and refuse what they must refuse, in both the hook a flag can skip
  and the one it cannot.
* **The seed patch** is registered once under ``[post_model_sync]``, seeds every role the DocPerm
  rows name with ``desk_access = 1`` plus the one-role "KB Approvers" Role Profile, is insert-only
  and idempotent, and cannot raise.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_schema -v
"""

import ast
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

MODULE = "Knowledge Base"
MODULE_DIR = APP / "knowledge_base"
ARTICLE = "Knowledge Article"
VERSION = "Knowledge Article Version"
DOCTYPE_DIRS = {
	ARTICLE: MODULE_DIR / "doctype" / "knowledge_article",
	VERSION: MODULE_DIR / "doctype" / "knowledge_article_version",
}
PATCH = "erpnext_enhancements.patches.seed_knowledge_base_roles"

#: Every right a DocPerm row can carry in v16 (frappe ``permissions.py`` ``std_rights`` plus the
#: level/owner qualifiers). A right missing from a row is 0.
RIGHTS = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"print",
	"email",
	"report",
	"import",
	"export",
	"share",
)
FORBIDDEN_RIGHTS = ("share", "submit", "cancel", "amend", "delete", "export", "import", "email")

#: Keyed on ``(role, permlevel)``. The Version's level-1 rows are read-only on purpose: the
#: server-set fields live there, and only server code running with ``ignore_permissions`` may
#: write them (v16 enforces permlevel on save, and never ``read_only``).
EXPECTED_PERMISSIONS = {
	ARTICLE: {
		("Desk User", 0): {"read", "report", "print"},
		("System Manager", 0): {"read"},
	},
	VERSION: {
		("KB Author", 0): {"read", "create", "write", "print", "report"},
		("KB Approver", 0): {"read", "create", "write", "print", "report"},
		("KB Author", 1): {"read"},
		("KB Approver", 1): {"read"},
	},
}

#: Fields that hold no value, so "every field is read-only" does not apply to them.
LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading", "Fold"}

ARTICLE_FIELDS = {
	"kb_number",
	"title",
	"department_block",
	"status",
	"summary",
	"keywords",
	"body",
	"body_md",
	"content_hash",
	"version_number",
	"live_version",
	"change_note",
	"author",
	"approved_by",
	"approved_on",
	"first_published_on",
	"process_owner",
	"review_every_months",
	"review_by",
	"last_reviewed_on",
	"last_reviewed_by",
	"ai_drafted",
	"retired_on",
	"retired_by",
	"retired_reason",
}

#: The fields an author types into. Everything else on a Version is set by the KB's own code.
VERSION_CONTENT_FIELDS = {
	"title",
	"department_block",
	"summary",
	"keywords",
	"process_owner",
	"review_every_months",
	"body",
	"change_note",
}
VERSION_FIELDS = VERSION_CONTENT_FIELDS | {
	"article",
	"version_number",
	"base_version",
	"review_state",
	"reviewer",
	"submitted_by",
	"submitted_on",
	"approved_by",
	"approved_on",
	"review_note",
	"contributors",
	"ai_drafted",
	"ai_requested_by",
	"source_url",
	"source_drive_file_id",
	"source_modified",
	"imported_on",
	"amended_from",
}


def _load(doctype):
	folder = DOCTYPE_DIRS[doctype]
	return json.loads((folder / f"{folder.name}.json").read_text(encoding="utf-8"))


def _value_fields(meta):
	return [f for f in meta["fields"] if f["fieldtype"] not in LAYOUT_TYPES]


def _field(meta, fieldname):
	matches = [f for f in meta["fields"] if f["fieldname"] == fieldname]
	return matches[0] if matches else None


def _frappe_classname(doctype):
	"""Frappe v16's own derivation: strip spaces and hyphens, never title-case."""
	return doctype.replace(" ", "").replace("-", "")


# ------------------------------------------------------------------ the frappe stub


class Refused(Exception):
	"""What the stub's ``frappe.throw`` raises."""


class _Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Document:
	"""Just enough of ``frappe.model.document.Document`` to run the KB controllers' hooks."""

	def __init__(self, **values):
		object.__setattr__(self, "flags", _Flags())
		for key, value in values.items():
			setattr(self, key, value)

	def get(self, key, default=None):
		return getattr(self, key, default)

	def get_doc_before_save(self):
		# A new document: PR 2's content rules then treat every field as the creator's work.
		return None


STATE = {}


def _reset_patch_state(roles=(), profiles=(), doctypes=("Role Profile",)):
	STATE.clear()
	STATE.update(
		{
			"doctypes": set(doctypes),
			"roles": {name: 0 for name in roles},
			"profiles": {name: [] for name in profiles},
			"inserted_roles": [],
			"inserted_profiles": [],
			"unlocked": [],
			"commits": 0,
			"rollbacks": 0,
			"errors": [],
			"fail_role_insert": set(),
			"fail_profile_insert": False,
			"fail_unlock": False,
		}
	)


class _RoleDoc:
	def __init__(self):
		self.role_name = None
		self.desk_access = None

	def insert(self, ignore_permissions=False):
		if self.role_name in STATE["fail_role_insert"]:
			raise RuntimeError("role insert failed")
		STATE["roles"][self.role_name] = self.desk_access
		STATE["inserted_roles"].append((self.role_name, self.desk_access, ignore_permissions))


class _ProfileDoc:
	def __init__(self, data):
		self.data = data

	def insert(self, ignore_permissions=False):
		if STATE["fail_profile_insert"]:
			raise RuntimeError("profile insert failed")
		STATE["profiles"][self.data["role_profile"]] = list(self.data.get("roles") or [])
		STATE["inserted_profiles"].append((self.data, ignore_permissions))

	def unlock(self):
		if STATE["fail_unlock"]:
			raise OSError("lock file already gone")
		STATE["unlocked"].append(self.data["role_profile"])


def _exists(doctype, name=None):
	if doctype == "DocType":
		return name in STATE["doctypes"]
	if doctype == "Role":
		return name in STATE["roles"]
	if doctype == "Role Profile":
		return name in STATE["profiles"]
	raise AssertionError(f"unexpected exists({doctype!r}, {name!r})")


def _new_doc(doctype):
	assert doctype == "Role", doctype
	return _RoleDoc()


def _get_doc(data):
	assert isinstance(data, dict) and data.get("doctype") == "Role Profile", data
	return _ProfileDoc(data)


def _commit():
	STATE["commits"] += 1


def _rollback():
	STATE["rollbacks"] += 1


def _throw(message, exc=None, title=None, **kwargs):
	raise Refused(message)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda message, *a, **k: message
	frappe.throw = _throw
	frappe.db = types.SimpleNamespace(exists=_exists, commit=_commit, rollback=_rollback)
	frappe.new_doc = _new_doc
	frappe.get_doc = _get_doc
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	# The Version's validate records the saver as a contributor (PR 2).
	frappe.session = types.SimpleNamespace(user="author@example.com")

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document


knowledge_article = None
knowledge_article_version = None
seed = None
constants = None


def setUpModule():
	global knowledge_article, knowledge_article_version, seed, constants
	_install_frappe_stub()
	for name in (
		"erpnext_enhancements.knowledge_base.doctype.knowledge_article.knowledge_article",
		"erpnext_enhancements.knowledge_base.doctype.knowledge_article_version.knowledge_article_version",
		PATCH,
	):
		sys.modules.pop(name, None)
	knowledge_article = importlib.import_module(
		"erpnext_enhancements.knowledge_base.doctype.knowledge_article.knowledge_article"
	)
	knowledge_article_version = importlib.import_module(
		"erpnext_enhancements.knowledge_base.doctype.knowledge_article_version.knowledge_article_version"
	)
	seed = importlib.import_module(PATCH)
	constants = importlib.import_module("erpnext_enhancements.knowledge_base.constants")


# ------------------------------------------------------------------ placement and flags


class TestPlacement(unittest.TestCase):
	def test_the_module_is_registered_and_importable(self):
		modules = (APP / "modules.txt").read_text(encoding="utf-8").splitlines()
		self.assertIn(MODULE, [line.strip() for line in modules])
		self.assertTrue((MODULE_DIR / "__init__.py").exists())
		self.assertTrue((MODULE_DIR / "doctype" / "__init__.py").exists())
		self.assertTrue((MODULE_DIR / "README.md").exists())

	def test_each_doctype_sits_in_its_module_with_a_controller(self):
		for doctype, folder in DOCTYPE_DIRS.items():
			with self.subTest(doctype=doctype):
				meta = _load(doctype)
				self.assertEqual(meta["name"], doctype)
				self.assertEqual(meta["module"], MODULE)
				self.assertEqual(meta["doctype"], "DocType")
				self.assertTrue((folder / "__init__.py").exists())
				self.assertTrue((folder / f"{folder.name}.py").exists())

	def test_controller_class_names_are_the_ones_frappe_derives(self):
		"""A mismatch makes get_controller raise, and remove_orphan_doctypes force-deletes the
		DocType on the same migrate that created it (v1.452.1, v1.452.2)."""
		for doctype, folder in DOCTYPE_DIRS.items():
			with self.subTest(doctype=doctype):
				tree = ast.parse((folder / f"{folder.name}.py").read_text(encoding="utf-8"))
				classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
				expected = _frappe_classname(doctype)
				self.assertIn(expected, classes)
				bases = [getattr(base, "id", None) for base in classes[expected].bases]
				self.assertEqual(bases, ["Document"])

	def test_the_expected_class_names(self):
		self.assertEqual(_frappe_classname(ARTICLE), "KnowledgeArticle")
		self.assertEqual(_frappe_classname(VERSION), "KnowledgeArticleVersion")


class TestFlags(unittest.TestCase):
	COMMON = {
		"has_web_view": 0,
		"allow_guest_to_view": 0,
		"show_in_global_search": 0,
		"make_attachments_public": 0,
		"allow_import": 0,
		"index_web_pages_for_search": 0,
		"email_append_to": 0,
		"track_seen": 0,
		"track_views": 0,
		"quick_entry": 0,
		"allow_rename": 0,
	}

	def test_both_doctypes_are_private_by_construction(self):
		for doctype in (ARTICLE, VERSION):
			meta = _load(doctype)
			for flag, value in self.COMMON.items():
				with self.subTest(doctype=doctype, flag=flag):
					self.assertIn(flag, meta, f"{doctype} must state {flag} explicitly")
					self.assertEqual(meta[flag], value)
			with self.subTest(doctype=doctype, flag="in_global_search"):
				self.assertEqual([f["fieldname"] for f in meta["fields"] if f.get("in_global_search")], [])
			with self.subTest(doctype=doctype, flag="istable/issingle"):
				self.assertFalse(meta.get("istable"))
				self.assertFalse(meta.get("issingle"))

	def test_the_article(self):
		meta = _load(ARTICLE)
		self.assertEqual(meta["autoname"], "field:kb_number")
		self.assertEqual(meta.get("naming_rule"), "By fieldname")
		self.assertEqual(meta["track_changes"], 1)
		self.assertFalse(meta.get("is_submittable"))
		self.assertEqual(meta["title_field"], "title")

	def test_the_version(self):
		meta = _load(VERSION)
		self.assertEqual(meta["autoname"], "KBV-.#####")
		self.assertEqual(meta["is_submittable"], 1)
		# Core Version rows are readable by System Manager, triton@ included.
		self.assertEqual(meta["track_changes"], 0)
		self.assertEqual(meta["title_field"], "title")


# ------------------------------------------------------------------ permissions


def _matrix(meta):
	matrix = {}
	for row in meta["permissions"]:
		key = (row["role"], row.get("permlevel") or 0)
		if key in matrix:
			raise AssertionError(f"{meta['name']} has two DocPerm rows for {key}")
		matrix[key] = {right for right in RIGHTS if row.get(right)}
	return matrix


class TestPermissions(unittest.TestCase):
	def test_the_exact_matrix(self):
		for doctype, expected in EXPECTED_PERMISSIONS.items():
			with self.subTest(doctype=doctype):
				self.assertEqual(_matrix(_load(doctype)), expected)

	def test_no_row_carries_a_forbidden_right(self):
		for doctype in (ARTICLE, VERSION):
			for row in _load(doctype)["permissions"]:
				for right in FORBIDDEN_RIGHTS:
					with self.subTest(doctype=doctype, role=row["role"], right=right):
						self.assertFalse(row.get(right), f"{doctype}/{row['role']} has {right}")

	def test_no_row_is_owner_scoped(self):
		for doctype in (ARTICLE, VERSION):
			for row in _load(doctype)["permissions"]:
				with self.subTest(doctype=doctype, role=row["role"], permlevel=row.get("permlevel")):
					self.assertFalse(row.get("if_owner"))

	def test_every_article_row_is_level_zero(self):
		for row in _load(ARTICLE)["permissions"]:
			with self.subTest(role=row["role"]):
				self.assertFalse(row.get("permlevel"))

	def test_nobody_can_write_above_level_zero_on_the_version(self):
		"""The server-set fields are at level 1. A write right there is a KB role able to rewrite
		the contributors or the AI requester, which is what PR 2's approval rules read."""
		for row in _load(VERSION)["permissions"]:
			if not row.get("permlevel"):
				continue
			with self.subTest(role=row["role"], permlevel=row["permlevel"]):
				self.assertEqual({right for right in RIGHTS if row.get(right)}, {"read"})

	def test_every_field_level_is_readable_by_both_kb_roles(self):
		"""A field at a level a role cannot read vanishes from that role's form and list."""
		meta = _load(VERSION)
		levels = {field.get("permlevel") or 0 for field in _value_fields(meta)}
		readable = {
			(row["role"], row.get("permlevel") or 0) for row in meta["permissions"] if row.get("read")
		}
		for role in ("KB Author", "KB Approver"):
			for level in levels:
				with self.subTest(role=role, permlevel=level):
					self.assertIn((role, level), readable)

	def test_no_reader_row_on_the_version(self):
		roles = {row["role"] for row in _load(VERSION)["permissions"]}
		for role in ("System Manager", "Desk User", "All", "Guest", "Administrator"):
			with self.subTest(role=role):
				self.assertNotIn(role, roles)

	def test_nobody_can_write_the_article(self):
		for row in _load(ARTICLE)["permissions"]:
			with self.subTest(role=row["role"]):
				self.assertFalse(row.get("write"))
				self.assertFalse(row.get("create"))

	def test_no_guest_or_all_row_anywhere(self):
		for doctype in (ARTICLE, VERSION):
			roles = {row["role"] for row in _load(doctype)["permissions"]}
			with self.subTest(doctype=doctype):
				self.assertFalse(roles & {"Guest", "All"})

	def test_no_fixture_widens_either_doctype(self):
		"""Custom DocPerm REPLACES a doctype's standard rows wholesale, and a Property Setter can
		flip any flag above. Neither may target the KB doctypes."""
		checks = (
			("custom_docperm.json", "parent"),
			("property_setter.json", "doc_type"),
			("custom_field.json", "dt"),
		)
		for filename, key in checks:
			path = APP / "fixtures" / filename
			if not path.exists():
				continue
			rows = json.loads(path.read_text(encoding="utf-8"))
			with self.subTest(fixture=filename):
				offenders = [row.get("name") for row in rows if str(row.get(key) or "").startswith("Knowledge Article")]
				self.assertEqual(offenders, [])


# ------------------------------------------------------------------ fields


class TestFields(unittest.TestCase):
	def test_the_article_has_exactly_the_spec_fields(self):
		self.assertEqual({f["fieldname"] for f in _value_fields(_load(ARTICLE))}, ARTICLE_FIELDS)

	def test_every_article_field_is_read_only(self):
		for field in _value_fields(_load(ARTICLE)):
			with self.subTest(field=field["fieldname"]):
				self.assertEqual(field.get("read_only"), 1)

	def test_the_version_has_exactly_the_spec_fields(self):
		self.assertEqual({f["fieldname"] for f in _value_fields(_load(VERSION))}, VERSION_FIELDS)

	def test_only_content_fields_are_editable_on_a_version(self):
		for field in _value_fields(_load(VERSION)):
			with self.subTest(field=field["fieldname"]):
				editable = not field.get("read_only")
				self.assertEqual(editable, field["fieldname"] in VERSION_CONTENT_FIELDS)

	def test_every_server_set_field_is_at_permlevel_one(self):
		"""``read_only`` is a Desk hint; v16 enforces only permlevel on a save
		(``validate_higher_perm_levels``, ``model/document.py:1021-1044``). At level 0, a KB Author
		or Approver could clear ``contributors`` or ``ai_requested_by`` with
		``frappe.client.set_value`` and then approve their own edit. ``amended_from`` is Frappe's
		own amendment link, stays at level 0, and is refused by the controller instead."""
		for field in _value_fields(_load(VERSION)):
			name = field["fieldname"]
			expected = 0 if name in VERSION_CONTENT_FIELDS or name == "amended_from" else 1
			with self.subTest(field=name):
				self.assertEqual(field.get("permlevel") or 0, expected)

	def test_no_article_field_is_above_level_zero(self):
		"""Nobody writes the Article at any level; a raised level would only hide fields from readers."""
		for field in _load(ARTICLE)["fields"]:
			with self.subTest(field=field["fieldname"]):
				self.assertFalse(field.get("permlevel"))

	def test_a_reqd_select_cannot_default_to_a_real_value(self):
		"""v16 gives a Select with no ``default`` its FIRST option on every new document, on the
		server (``model/create_new.py:117-118``, via ``Document._set_defaults``) and in the Desk
		(``model/create_new.js:107-114``). So ``reqd`` on such a field never fires unless the first
		option is blank. On ``department_block`` that decides a KB number nobody can rename."""
		for doctype in (ARTICLE, VERSION):
			for field in _load(doctype)["fields"]:
				if field["fieldtype"] != "Select" or not field.get("reqd") or "default" in field:
					continue
				with self.subTest(doctype=doctype, field=field["fieldname"]):
					self.assertEqual(field["options"].split("\n")[0], "")

	def test_department_block_is_required_and_starts_blank(self):
		version_field = _field(_load(VERSION), "department_block")
		self.assertEqual(version_field.get("reqd"), 1)
		self.assertNotIn("default", version_field)
		for doctype in (ARTICLE, VERSION):
			field = _field(_load(doctype), "department_block")
			with self.subTest(doctype=doctype):
				self.assertEqual(field["options"].split("\n", 1)[0], "")
				self.assertNotIn("default", field)

	def test_only_review_state_changes_after_submit(self):
		meta = _load(VERSION)
		self.assertEqual([f["fieldname"] for f in meta["fields"] if f.get("allow_on_submit")], ["review_state"])
		self.assertEqual([f["fieldname"] for f in _load(ARTICLE)["fields"] if f.get("allow_on_submit")], [])

	def test_field_order_lists_every_field_once(self):
		for doctype in (ARTICLE, VERSION):
			meta = _load(doctype)
			with self.subTest(doctype=doctype):
				self.assertEqual(meta["field_order"], [f["fieldname"] for f in meta["fields"]])
				self.assertEqual(len(set(meta["field_order"])), len(meta["field_order"]))

	def test_the_body_is_one_text_editor_and_the_markdown_is_hidden(self):
		for doctype in (ARTICLE, VERSION):
			with self.subTest(doctype=doctype):
				self.assertEqual(_field(_load(doctype), "body")["fieldtype"], "Text Editor")
		body_md = _field(_load(ARTICLE), "body_md")
		self.assertEqual(body_md["fieldtype"], "Long Text")
		self.assertEqual(body_md.get("hidden"), 1)

	def test_live_version_is_data_not_a_link(self):
		"""Readers cannot open a version, so a Link's title lookup would fail for every one of them."""
		field = _field(_load(ARTICLE), "live_version")
		self.assertEqual(field["fieldtype"], "Data")
		self.assertNotIn("options", field)

	def test_the_version_links_to_the_article_and_the_amendment_link_is_its_own(self):
		meta = _load(VERSION)
		self.assertEqual(_field(meta, "article")["options"], ARTICLE)
		self.assertEqual(_field(meta, "amended_from")["options"], VERSION)
		self.assertEqual(_field(meta, "review_state").get("default"), "Draft")

	def test_the_kb_number_names_the_article(self):
		field = _field(_load(ARTICLE), "kb_number")
		self.assertEqual(field["fieldtype"], "Data")
		self.assertEqual(field.get("reqd"), 1)
		self.assertEqual(field.get("unique"), 1)


class TestSelectOptionsMatchTheCode(unittest.TestCase):
	def _options(self, doctype, fieldname):
		return tuple(_field(_load(doctype), fieldname)["options"].split("\n"))

	def test_article_status(self):
		self.assertEqual(self._options(ARTICLE, "status"), constants.ARTICLE_STATUSES)
		self.assertEqual(constants.ARTICLE_STATUSES, ("Published", "Retired"))

	def test_review_state(self):
		self.assertEqual(self._options(VERSION, "review_state"), constants.REVIEW_STATES)
		self.assertEqual(
			constants.REVIEW_STATES, ("Draft", "In Review", "Published", "Superseded", "Discarded")
		)
		self.assertTrue(set(constants.OPEN_REVIEW_STATES) <= set(constants.REVIEW_STATES))

	def test_department_blocks(self):
		for doctype in (ARTICLE, VERSION):
			with self.subTest(doctype=doctype):
				self.assertEqual(
					self._options(doctype, "department_block"), constants.DEPARTMENT_BLOCK_SELECT_OPTIONS
				)
		self.assertEqual(constants.DEPARTMENT_BLOCK_SELECT_OPTIONS, ("", *constants.DEPARTMENT_BLOCK_OPTIONS))
		codes = [code for code, _label in constants.DEPARTMENT_BLOCKS]
		self.assertEqual(codes, [f"{n:02d}" for n in range(10)])
		self.assertIn("06 Operations", constants.DEPARTMENT_BLOCK_OPTIONS)
		# The blank is a way of saying "not chosen yet", never a block.
		self.assertNotIn("", constants.DEPARTMENT_BLOCK_OPTIONS)
		self.assertIsNone(constants.block_code(constants.DEPARTMENT_BLOCK_SELECT_OPTIONS[0]))

	def test_review_every_months_defaults_to_the_policy_cadence(self):
		"""POL-0001 sets the cadence (six months on 2026-09-25). One constant, and both JSONs carry
		it: the Version's default is what a new draft starts with, and the Article's copy matches."""
		self.assertIsInstance(constants.DEFAULT_REVIEW_EVERY_MONTHS, int)
		self.assertGreater(constants.DEFAULT_REVIEW_EVERY_MONTHS, 0)
		for doctype in (ARTICLE, VERSION):
			field = _field(_load(doctype), "review_every_months")
			with self.subTest(doctype=doctype):
				self.assertEqual(field["fieldtype"], "Int")
				self.assertEqual(field.get("default"), str(constants.DEFAULT_REVIEW_EVERY_MONTHS))

	def test_block_code_is_strict(self):
		self.assertEqual(constants.block_code("06 Operations"), "06")
		self.assertEqual(constants.block_code("00 Company Wide"), "00")
		for bad in ("06", "06 operations", "Operations", "", None, 6, "10 Anything"):
			with self.subTest(value=bad):
				self.assertIsNone(constants.block_code(bad))


# ------------------------------------------------------------------ controller refusals


class TestArticleController(unittest.TestCase):
	def _doc(self, **values):
		return knowledge_article.KnowledgeArticle(name="KB-0612", **values)

	def test_a_save_without_the_kb_action_flag_is_refused_in_both_hooks(self):
		doc = self._doc()
		for hook in ("validate", "on_update"):
			with self.subTest(hook=hook), self.assertRaises(Refused):
				getattr(doc, hook)()

	def test_a_save_with_the_flag_passes(self):
		doc = self._doc()
		doc.flags.kb_action = True
		doc.validate()
		doc.on_update()

	def test_the_publish_flag_is_not_the_action_flag(self):
		doc = self._doc()
		doc.flags.kb_publish = True
		with self.assertRaises(Refused):
			doc.validate()

	def test_delete_and_rename_are_always_refused(self):
		doc = self._doc()
		doc.flags.kb_action = True
		for call in (doc.on_trash, doc.after_delete, lambda: doc.before_rename("KB-0612", "KB-0613")):
			with self.assertRaises(Refused):
				call()


class TestVersionController(unittest.TestCase):
	def _doc(self, **values):
		return knowledge_article_version.KnowledgeArticleVersion(name="KBV-00001", **values)

	def test_a_submit_without_the_publish_flag_is_refused_in_both_hooks(self):
		doc = self._doc()
		for hook in ("before_submit", "on_submit"):
			with self.subTest(hook=hook), self.assertRaises(Refused):
				getattr(doc, hook)()

	def test_the_action_flag_does_not_publish(self):
		doc = self._doc()
		doc.flags.kb_action = True
		for hook in ("before_submit", "on_submit"):
			with self.subTest(hook=hook), self.assertRaises(Refused):
				getattr(doc, hook)()

	def test_a_submit_with_the_publish_flag_reaches_the_approval_rules(self):
		"""The flag is necessary, not sufficient: from PR 2 both hooks then apply the approval rules,
		which ``test_knowledge_base_hooks`` exercises with a stub that has a session and a request."""
		doc = self._doc()
		doc.flags.kb_publish = True
		reached = []
		doc._refuse_unless_approvable = lambda: reached.append(True)
		doc.before_submit()
		doc.on_submit()
		self.assertEqual(reached, [True, True])

	def test_cancel_is_always_refused_in_both_hooks(self):
		doc = self._doc()
		doc.flags.kb_action = True
		doc.flags.kb_publish = True
		for hook in ("before_cancel", "on_cancel"):
			with self.subTest(hook=hook), self.assertRaises(Refused):
				getattr(doc, hook)()

	def test_an_update_after_submit_needs_the_action_flag(self):
		doc = self._doc()
		for hook in ("before_update_after_submit", "on_update_after_submit"):
			with self.subTest(hook=hook), self.assertRaises(Refused):
				getattr(doc, hook)()
		doc.flags.kb_action = True
		doc.before_update_after_submit()
		doc.on_update_after_submit()

	def test_an_amendment_is_refused_on_insert_and_on_validate(self):
		doc = self._doc(amended_from="KBV-00000")
		doc.flags.kb_action = True
		doc.flags.kb_publish = True
		for hook in ("before_insert", "validate"):
			with self.subTest(hook=hook), self.assertRaises(Refused):
				getattr(doc, hook)()

	def test_an_ordinary_draft_saves(self):
		doc = self._doc(amended_from=None)
		doc.before_insert()
		doc.validate()

	def test_delete_and_rename_are_always_refused(self):
		doc = self._doc()
		doc.flags.kb_action = True
		for call in (doc.on_trash, doc.after_delete, lambda: doc.before_rename("KBV-00001", "KBV-00002")):
			with self.assertRaises(Refused):
				call()


# ------------------------------------------------------------------ the seed patch


def _post_model_sync_lines():
	sections, current = {}, None
	for raw in (APP / "patches.txt").read_text(encoding="utf-8").splitlines():
		line = raw.strip()
		if not line:
			continue
		if line.startswith("[") and line.endswith("]"):
			current = line[1:-1]
			sections[current] = []
			continue
		if line.startswith("#") or current is None:
			continue
		sections[current].append(line)
	return sections


class TestSeedPatchRegistration(unittest.TestCase):
	def test_registered_once_under_post_model_sync_with_no_trailing_text(self):
		"""A trailing comment on a patch line becomes part of its Patch Log key in Frappe."""
		sections = _post_model_sync_lines()
		self.assertEqual(sections.get("post_model_sync", []).count(PATCH), 1)
		for name, lines in sections.items():
			if name != "post_model_sync":
				with self.subTest(section=name):
					self.assertNotIn(PATCH, lines)

	def test_it_seeds_every_role_the_docperm_rows_name(self):
		version_roles = {row["role"] for row in _load(VERSION)["permissions"]}
		self.assertEqual({name for name, _desk in seed.ROLES}, version_roles)
		for name, desk_access in seed.ROLES:
			with self.subTest(role=name):
				self.assertEqual(desk_access, 1)

	def test_the_profile_carries_the_approver_role_only(self):
		"""Plural like the other one-role profiles ("PO Approvers" holds "PO Approver")."""
		self.assertEqual(seed.APPROVER_PROFILE, "KB Approvers")
		self.assertEqual(seed.APPROVER_ROLE, "KB Approver")
		self.assertNotEqual(seed.APPROVER_PROFILE, seed.APPROVER_ROLE)
		self.assertIn(seed.APPROVER_ROLE, {name for name, _desk in seed.ROLES})

	def test_it_is_not_also_a_fixture(self):
		"""Owned by the patch. Fixture sync would re-insert the profile on every migrate."""
		owned = (("role.json", ("KB Author", "KB Approver")), ("role_profile.json", ("KB Approvers",)))
		for filename, names in owned:
			path = APP / "fixtures" / filename
			if not path.exists():
				continue
			present = {row.get("name") for row in json.loads(path.read_text(encoding="utf-8"))}
			with self.subTest(fixture=filename):
				self.assertFalse(present & set(names))


class TestSeedPatchBehaviour(unittest.TestCase):
	def setUp(self):
		_reset_patch_state()

	def test_a_fresh_site_gets_both_roles_and_the_profile(self):
		seed.execute()
		self.assertEqual(
			STATE["inserted_roles"], [("KB Author", 1, True), ("KB Approver", 1, True)]
		)
		self.assertEqual(len(STATE["inserted_profiles"]), 1)
		data, ignore_permissions = STATE["inserted_profiles"][0]
		self.assertTrue(ignore_permissions)
		self.assertEqual(data["role_profile"], "KB Approvers")
		self.assertEqual(data["roles"], [{"role": "KB Approver"}])
		# No members: assigning the profile is a Desk step, never this patch's.
		self.assertEqual(set(data), {"doctype", "role_profile", "roles"})
		self.assertEqual(STATE["unlocked"], ["KB Approvers"])
		self.assertEqual(STATE["errors"], [])

	def test_it_is_idempotent(self):
		seed.execute()
		first = (list(STATE["inserted_roles"]), list(STATE["inserted_profiles"]))
		seed.execute()
		self.assertEqual((STATE["inserted_roles"], STATE["inserted_profiles"]), first)
		self.assertEqual(STATE["errors"], [])

	def test_it_is_insert_only(self):
		"""Model sync usually creates both roles first (make_module_and_roles). A role or profile
		that exists is never touched, whatever it holds."""
		_reset_patch_state(roles=("KB Author", "KB Approver"), profiles=("KB Approvers",))
		seed.execute()
		self.assertEqual(STATE["inserted_roles"], [])
		self.assertEqual(STATE["inserted_profiles"], [])
		self.assertEqual(STATE["roles"], {"KB Author": 0, "KB Approver": 0})
		self.assertEqual(STATE["profiles"], {"KB Approvers": []})

	def test_existing_roles_still_get_the_profile(self):
		_reset_patch_state(roles=("KB Author", "KB Approver"))
		seed.execute()
		self.assertEqual(STATE["inserted_roles"], [])
		self.assertEqual(len(STATE["inserted_profiles"]), 1)

	def test_a_failed_role_insert_is_logged_and_the_profile_waits(self):
		STATE["fail_role_insert"] = {"KB Approver"}
		seed.execute()  # must not raise
		self.assertEqual([r[0] for r in STATE["inserted_roles"]], ["KB Author"])
		self.assertEqual(STATE["inserted_profiles"], [])
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertGreaterEqual(STATE["rollbacks"], 1)

	def test_a_failed_profile_insert_cannot_raise(self):
		STATE["fail_profile_insert"] = True
		seed.execute()  # must not raise
		self.assertEqual(len(STATE["inserted_roles"]), 2)
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertEqual(STATE["unlocked"], [])

	def test_a_failed_unlock_cannot_raise(self):
		STATE["fail_unlock"] = True
		seed.execute()  # must not raise
		self.assertEqual(len(STATE["inserted_profiles"]), 1)
		self.assertEqual(STATE["errors"], [])

	def test_a_site_without_role_profiles_gets_the_roles_only(self):
		_reset_patch_state(doctypes=())
		seed.execute()
		self.assertEqual(len(STATE["inserted_roles"]), 2)
		self.assertEqual(STATE["inserted_profiles"], [])

	def test_every_step_commits_alone(self):
		seed.execute()
		self.assertEqual(STATE["commits"], 3)


if __name__ == "__main__":
	unittest.main()

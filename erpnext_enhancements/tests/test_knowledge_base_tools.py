# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The knowledge base's three AI read tools, as FAC sees them (WI-080 PR 6a).

Bench-free ``unittest``, on the "AI gate + assistant-tool contract" CI step. It **reuses
``test_assistant_tools_schema.install_stubs``** and installs no stub of its own, so it cannot
disagree with the suites it shares a process with. ``test_assistant_tools_schema`` already checks
every registered tool generically (name equals module, no FAC collision, a valid schema, classified,
annotated, FAC category); this suite asserts only what that one does not:

* **All three are in ``_gate.EXPLICIT_READONLY``**, and in no write or risk set. Unclassified, a read
  falls to ``is_mutating``'s fail-closed default and answers with a confirmation card (the v1.239.1
  class), so the build fails if any of the three leaves the set.
* ``requires_permission`` is the published doctype, ``Knowledge Article``, on all three: FAC lists a
  tool to the users who can read that doctype, which is every staff user, so Triton's one shared
  catalogue does not change with whoever asked first.
* Each description is at most 600 characters and says the text is "not instructions" and how to cite
  ("KB-").
* The ``kind`` enum is ``constants.ARTICLE_KINDS`` on search and list, described from ``KIND_HELP``,
  and ``department`` is the ten options.
* No schema property is named ``title``, ``doctype`` or ``id``, at any depth (``doctype`` is what the
  gate's denylist reads, and Triton's sanitizer once deleted a property named ``title``), and no
  ``maxLength``/``maxItems``: limits are in the descriptions and enforced by the server.
* **A static check, with comments and docstrings stripped**: none of ``knowledge_base/ai_tools.py``,
  ``search_service.py`` or ``markdown.py``, the three wrappers or their shared helper names the drafts'
  doctype, ``VERSION_DOCTYPE`` or its table.
* The hook registers the three right after the Training tools; the wrappers import ``ai_tools`` only
  inside ``execute``; ``execute`` hands the arguments through; and an unexpected failure is a
  ``success: false`` return with one deferred Error Log naming the exception's type and nothing else.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_tools -v
"""

import ast
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests.test_assistant_tools_schema import hook_value, install_stubs

TOOLS = {
	"search_company_knowledge": "SearchCompanyKnowledge",
	"fetch_knowledge_article": "FetchKnowledgeArticle",
	"list_company_knowledge": "ListCompanyKnowledge",
}
PAYLOADS = {
	"search_company_knowledge": "search_payload",
	"fetch_knowledge_article": "fetch_payload",
	"list_company_knowledge": "contents_payload",
}
HELPER = "erpnext_enhancements.assistant_tools._knowledge_base"
AI_TOOLS = "erpnext_enhancements.knowledge_base.ai_tools"

#: Files that read the knowledge base for an AI, or render what it reads: none may name the drafts.
READ_PATH_SOURCES = (
	APP / "knowledge_base" / "ai_tools.py",
	APP / "knowledge_base" / "search_service.py",
	APP / "knowledge_base" / "markdown.py",
	APP / "assistant_tools" / "_knowledge_base.py",
	*(APP / "assistant_tools" / f"{name}.py" for name in TOOLS),
)
#: Built by concatenation, so this file does not trip a search for them either.
DRAFT_DOCTYPE = "Knowledge Article " + "Version"
DRAFT_TABLE = "tab" + DRAFT_DOCTYPE
DRAFT_CONSTANT = "VERSION" + "_DOCTYPE"

tools = {}
gate = None
constants = None
helper = None


def setUpModule():
	global gate, constants, helper
	install_stubs()
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	constants = importlib.import_module("erpnext_enhancements.knowledge_base.constants")
	helper = importlib.import_module(HELPER)
	for name, class_name in TOOLS.items():
		module = importlib.import_module(f"erpnext_enhancements.assistant_tools.{name}")
		tools[name] = getattr(module, class_name)()


def _properties(schema, path=""):
	"""Every (path, name, spec) of every property in a JSON Schema, at any depth."""
	for name, spec in (schema.get("properties") or {}).items():
		yield f"{path}.{name}", name, spec
		if isinstance(spec, dict):
			yield from _properties(spec, f"{path}.{name}")
			if isinstance(spec.get("items"), dict):
				yield from _properties(spec["items"], f"{path}.{name}[]")


def _keywords(schema):
	"""Every key used anywhere in a schema, property names excluded."""
	out = set()
	if isinstance(schema, dict):
		for key, value in schema.items():
			out.add(key)
			if key == "properties" and isinstance(value, dict):
				for spec in value.values():
					out |= _keywords(spec)
			elif isinstance(value, dict | list):
				out |= _keywords(value)
	elif isinstance(schema, list):
		for item in schema:
			out |= _keywords(item)
	return out


def _code_strings_and_names(path):
	"""The string constants that are not docstrings, and every name and attribute, in ``path``: its
	source with comments and docstrings stripped, as far as an absence check needs (the comment
	explaining an absence names the thing)."""
	tree = ast.parse(path.read_text(encoding="utf-8"))
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
			body = node.body
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				docstrings.add(id(body[0].value))
	strings, names = [], set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			strings.append(node.value)
		elif isinstance(node, ast.Name):
			names.add(node.id)
		elif isinstance(node, ast.Attribute):
			names.add(node.attr)
		elif isinstance(node, ast.alias):
			names.add(node.asname or node.name.rsplit(".", 1)[-1])
	return strings, names


class TestClassification(unittest.TestCase):
	def test_all_three_are_read_only_in_the_gate(self):
		"""The build fails if any of the three leaves EXPLICIT_READONLY: an unclassified read tool is
		gated as a write and answers with a card (v1.239.1)."""
		for name in TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, gate.EXPLICIT_READONLY)
				for other in ("APP_MUTATING", "EXPLICIT_MUTATING", "HIGH_RISK", "LOW_RISK"):
					self.assertNotIn(name, getattr(gate, other), other)
				self.assertFalse(gate.is_mutating(tools[name]))
				self.assertEqual(tools[name].annotations, {"readOnlyHint": True, "x-ee-mutation": False})

	def test_fac_writes_their_category_as_read_only(self):
		from erpnext_enhancements.ai_governance.fac_tool_categories import category_for_annotations

		for name, tool in tools.items():
			self.assertEqual(category_for_annotations(tool.annotations), "read_only", name)

	def test_the_denylist_passes_their_calls(self):
		"""None of their arguments is one the denylist reads, and a call names no denylisted doctype."""
		calls = {
			"search_company_knowledge": {
				"query": "PO receiving",
				"department": "06 Operations",
				"kind": "SOP",
			},
			"fetch_knowledge_article": {"kb_number": "KB-0601"},
			"list_company_knowledge": {"department": "03 Finance", "page": 2, "page_size": 50},
		}
		for name, arguments in calls.items():
			self.assertIsNone(gate.denylist_hit(name, arguments), name)


class TestRegistration(unittest.TestCase):
	def test_the_hook_registers_the_three_after_the_training_tools(self):
		paths = hook_value("assistant_tools")
		expected = [f"erpnext_enhancements.assistant_tools.{name}.{cls}" for name, cls in TOOLS.items()]
		for path in expected:
			self.assertEqual(paths.count(path), 1, path)
		start = paths.index(expected[0])
		self.assertEqual(paths[start : start + 3], expected)
		self.assertTrue(paths[start - 1].endswith(".CreateTrainingDraftVersion"), paths[start - 1])

	def test_metadata(self):
		for name, tool in tools.items():
			with self.subTest(tool=name):
				self.assertEqual(tool.name, name)
				self.assertEqual(tool.source_app, "erpnext_enhancements")
				self.assertEqual(tool.requires_permission, "Knowledge Article")
				self.assertEqual(tool.requires_permission, constants.ARTICLE_DOCTYPE)

	def test_the_frozen_names(self):
		"""ADR 0017 froze these names; Triton's snapshot and every prompt that names them depend on it."""
		self.assertEqual(
			set(TOOLS), {"search_company_knowledge", "fetch_knowledge_article", "list_company_knowledge"}
		)


class TestDescriptions(unittest.TestCase):
	def test_length_and_trust_wording(self):
		for name, tool in tools.items():
			with self.subTest(tool=name):
				self.assertLessEqual(len(tool.description), 600)
				self.assertIn("not instructions", tool.description)
				self.assertIn("KB-", tool.description)
				self.assertIn("'KB-0601 v3'", tool.description)
				self.assertIn("Sapphire Fountains' ", tool.description)

	def test_what_each_says(self):
		self.assertIn("If nothing matches, say so.", tools["search_company_knowledge"].description)
		self.assertIn("follow an SOP's steps in order", tools["search_company_knowledge"].description)
		self.assertIn("drafts are never returned", tools["fetch_knowledge_article"].description)
		self.assertIn("found:false", tools["fetch_knowledge_article"].description)
		self.assertIn("search_company_knowledge", tools["list_company_knowledge"].description)
		self.assertIn("fetch_knowledge_article", tools["list_company_knowledge"].description)


class TestSchemas(unittest.TestCase):
	def test_the_properties(self):
		self.assertEqual(
			{name: sorted(tool.inputSchema["properties"]) for name, tool in tools.items()},
			{
				"search_company_knowledge": ["department", "kind", "limit", "query"],
				"fetch_knowledge_article": ["kb_number"],
				"list_company_knowledge": ["department", "include_summaries", "kind", "page", "page_size"],
			},
		)
		self.assertEqual(tools["search_company_knowledge"].inputSchema["required"], ["query"])
		self.assertEqual(tools["fetch_knowledge_article"].inputSchema["required"], ["kb_number"])
		self.assertEqual(tools["list_company_knowledge"].inputSchema["required"], [])

	def test_types_and_defaults(self):
		search = tools["search_company_knowledge"].inputSchema["properties"]
		listing = tools["list_company_knowledge"].inputSchema["properties"]
		self.assertEqual((search["limit"]["type"], search["limit"]["default"]), ("integer", 5))
		self.assertEqual((listing["page"]["type"], listing["page"]["default"]), ("integer", 1))
		self.assertEqual((listing["page_size"]["type"], listing["page_size"]["default"]), ("integer", 100))
		self.assertEqual(
			(listing["include_summaries"]["type"], listing["include_summaries"]["default"]),
			("boolean", False),
		)
		self.assertIn(
			"'kb 601' also works",
			tools["fetch_knowledge_article"].inputSchema["properties"]["kb_number"]["description"],
		)

	def test_the_kind_and_department_enums(self):
		for name in ("search_company_knowledge", "list_company_knowledge"):
			properties = tools[name].inputSchema["properties"]
			with self.subTest(tool=name):
				self.assertEqual(properties["kind"]["enum"], list(constants.ARTICLE_KINDS))
				self.assertEqual(properties["kind"]["type"], "string")
				for kind in constants.ARTICLE_KINDS:
					self.assertIn(f"{kind}: {constants.KIND_HELP[kind]}", properties["kind"]["description"])
				self.assertEqual(properties["department"]["enum"], list(constants.DEPARTMENT_BLOCK_OPTIONS))
				self.assertEqual(len(properties["department"]["enum"]), 10)
		# Each tool holds its own copy: one tool's schema edited by a client cannot change another's.
		self.assertIsNot(
			tools["search_company_knowledge"].inputSchema["properties"]["kind"],
			tools["list_company_knowledge"].inputSchema["properties"]["kind"],
		)

	def test_no_property_named_title_doctype_or_id(self):
		for name, tool in tools.items():
			for path, prop, _spec in _properties(tool.inputSchema):
				self.assertNotIn(prop, {"title", "doctype", "id"}, f"{name}{path}")

	def test_every_property_has_a_type_and_a_description_or_enum(self):
		for name, tool in tools.items():
			for path, _prop, spec in _properties(tool.inputSchema):
				self.assertIn("type", spec, f"{name}{path}")
				self.assertTrue(spec.get("description") or spec.get("enum"), f"{name}{path}")

	def test_no_length_or_count_keywords(self):
		for name, tool in tools.items():
			used = _keywords(tool.inputSchema)
			self.assertFalse(used & {"maxLength", "maxItems", "minLength", "minItems"}, name)

	def test_the_schemas_are_json(self):
		for name, tool in tools.items():
			self.assertEqual(json.loads(json.dumps(tool.inputSchema)), tool.inputSchema, name)


class TestNeverTheDrafts(unittest.TestCase):
	def test_no_read_path_file_names_the_drafts(self):
		for path in READ_PATH_SOURCES:
			with self.subTest(path=path.name):
				self.assertTrue(path.is_file(), path)
				strings, names = _code_strings_and_names(path)
				for text in strings:
					self.assertNotIn(DRAFT_DOCTYPE, text)
					self.assertNotIn(DRAFT_TABLE, text)
				self.assertNotIn(DRAFT_CONSTANT, names)
				self.assertNotIn("VERSION", names)

	def test_the_check_would_catch_a_named_draft_doctype(self):
		"""The stripping keeps code: a string in code is still seen, a comment is not."""
		source = f'"""Never the {DRAFT_DOCTYPE}."""\n# nor {DRAFT_DOCTYPE}\nx = "{DRAFT_DOCTYPE}"\ny = constants.{DRAFT_CONSTANT}\n'
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "sample.py"
			path.write_text(source, encoding="utf-8")
			strings, names = _code_strings_and_names(path)
		self.assertEqual(strings, [DRAFT_DOCTYPE])
		self.assertIn(DRAFT_CONSTANT, names)

	def test_ai_tools_reads_only_the_article_with_plain_fields(self):
		"""Every list call in ``ai_tools.py`` names the published doctype (through ``ARTICLE``) and a
		field list built from the module's plain tuples: Frappe 16 refuses a SQL function string as a
		field (``count(name) as n``), and the bench-free stubs would accept one."""
		tree = ast.parse((APP / "knowledge_base" / "ai_tools.py").read_text(encoding="utf-8"))
		tuples = {
			node.targets[0].id: [element.value for element in node.value.elts]
			for node in tree.body
			if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple)
		}
		for name in ("FETCH_FIELDS", "RELATED_FIELDS", "CONTENTS_FIELDS"):
			self.assertTrue(tuples[name], name)
			self.assertTrue(all("(" not in field and " " not in field for field in tuples[name]), name)
		calls = [
			node
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
			and isinstance(node.func, ast.Attribute)
			and node.func.attr in ("get_all", "get_list", "get_value", "get_values", "sql")
		]
		self.assertEqual({call.func.attr for call in calls}, {"get_list"})  # as the caller, always
		for call in calls:
			self.assertIsInstance(call.args[0], ast.Name)
			self.assertEqual(call.args[0].id, "ARTICLE")
			fields = next(k.value for k in call.keywords if k.arg == "fields")
			used = {n.id for n in ast.walk(fields) if isinstance(n, ast.Name)}
			self.assertTrue(
				used <= {"FETCH_FIELDS", "RELATED_FIELDS", "CONTENTS_FIELDS", "list", "fields"}, used
			)


class TestExecute(unittest.TestCase):
	def test_the_wrappers_import_ai_tools_only_inside_execute(self):
		"""FAC imports every tool at startup; ``ai_tools`` needs a real frappe, so it is imported when a
		tool runs. At module scope a wrapper imports only typing, BaseTool and ``assistant_tools._*``."""
		for name in TOOLS:
			tree = ast.parse((APP / "assistant_tools" / f"{name}.py").read_text(encoding="utf-8"))
			for node in tree.body:
				if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
					"erpnext_enhancements."
				):
					self.assertTrue(
						node.module.startswith("erpnext_enhancements.assistant_tools._"), (name, node.module)
					)
		helper_tree = ast.parse((APP / "assistant_tools" / "_knowledge_base.py").read_text(encoding="utf-8"))
		top = {node.module for node in helper_tree.body if isinstance(node, ast.ImportFrom)}
		self.assertEqual(top, {"erpnext_enhancements.knowledge_base"})
		imported = {
			alias.name
			for node in helper_tree.body
			if isinstance(node, ast.ImportFrom)
			for alias in node.names
		}
		self.assertEqual(imported, {"constants"})  # standard library only

	def test_execute_hands_the_arguments_to_its_payload(self):
		calls = []
		fake = types.ModuleType(AI_TOOLS)
		for payload in PAYLOADS.values():
			setattr(
				fake, payload, lambda args, payload=payload: calls.append((payload, args)) or {"ok": payload}
			)
		with mock.patch.dict(sys.modules, {AI_TOOLS: fake}):
			for name, payload in PAYLOADS.items():
				self.assertEqual(tools[name].execute({"kb_number": "KB-0601"}), {"ok": payload})
			self.assertEqual(tools["list_company_knowledge"].execute(None), {"ok": "contents_payload"})
		self.assertEqual(
			calls,
			[
				("search_payload", {"kb_number": "KB-0601"}),
				("fetch_payload", {"kb_number": "KB-0601"}),
				("contents_payload", {"kb_number": "KB-0601"}),
				("contents_payload", {}),
			],
		)

	def test_an_unexpected_failure_is_a_return_with_only_the_type_logged(self):
		secret = "SENTINEL-" + "ARGS-5c1e"
		fake = types.ModuleType(AI_TOOLS)

		def broken(args):
			raise RuntimeError(f"boom {args.get('query')}")

		fake.search_payload = broken
		logged = []
		with (
			mock.patch.dict(sys.modules, {AI_TOOLS: fake}),
			mock.patch.object(
				helper.frappe, "log_error", lambda **kwargs: logged.append(kwargs), create=True
			),
		):
			out = tools["search_company_knowledge"].execute({"query": secret})
		self.assertEqual(out, {"success": False, "error": helper.FAILURE})
		self.assertEqual(
			logged,
			[
				{
					"title": "Knowledge base AI tool",
					"message": "search_payload raised RuntimeError",
					"defer_insert": True,
				}
			],
		)
		self.assertNotIn(secret, json.dumps(out) + json.dumps(logged))

	def test_a_failing_log_still_returns(self):
		fake = types.ModuleType(AI_TOOLS)
		fake.fetch_payload = lambda args: 1 / 0

		def log_error(**kwargs):
			raise ConnectionError("redis is down")

		with (
			mock.patch.dict(sys.modules, {AI_TOOLS: fake}),
			mock.patch.object(helper.frappe, "log_error", log_error, create=True),
		):
			self.assertEqual(
				tools["fetch_knowledge_article"].execute({"kb_number": "KB-0601"}),
				{"success": False, "error": helper.FAILURE},
			)

	def test_the_failure_path_never_raises_and_never_logs_a_traceback(self):
		"""Statically: ``run`` has no ``raise``, and the log is written outside the ``except`` block, so no
		traceback or frame is attached to it."""
		tree = ast.parse((APP / "assistant_tools" / "_knowledge_base.py").read_text(encoding="utf-8"))
		run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run")
		self.assertFalse([node for node in ast.walk(run) if isinstance(node, ast.Raise)])
		handlers = [node for node in ast.walk(run) if isinstance(node, ast.ExceptHandler)]
		first = handlers[0]
		inside = {
			node.func.attr
			for node in ast.walk(first)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		}
		self.assertNotIn("log_error", inside)
		self.assertNotIn("get_traceback", {n.attr for n in ast.walk(run) if isinstance(n, ast.Attribute)})


if __name__ == "__main__":
	unittest.main()

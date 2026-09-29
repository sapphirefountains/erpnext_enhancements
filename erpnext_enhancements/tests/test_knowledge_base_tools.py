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
  ``success: false`` return with one deferred Error Log naming the exception's type and nothing else,
  built by ``run`` itself and never through ``frappe.log_error``, whose v16 ``metadata`` would hold the
  request's form_dict, which for an MCP call is the JSON-RPC body with the arguments (a stub shaped like
  v16's shows it would).

**PR 6b (v1.560.0), the drafting tool, ``draft_knowledge_article``**, on the same stubs:

* It is a write in ``APP_MUTATING`` and in no other set, so ``classify_risk`` is **Medium** and the
  annotations say ``x-ee-risk: "medium"`` with ``destructiveHint: false``; FAC's category is ``write``.
* ``requires_permission`` is the drafts' doctype (so FAC lists it to KB roles only); its description is
  at most 600 characters; no property is named ``title``, ``doctype`` or ``id``; ``submit_for_review`` is
  a boolean defaulting to false; the ``kind`` enum is ``constants.ARTICLE_KINDS``.
* The four card lines ``summarize_tool_call`` writes, and the card's target (``_call_target``).
* The gate's two refusals without a card, through ``_gated_execute`` with the real ``insert_action_log``:
  the tool's own precheck (a secret, here), and the step-0 denylist refusal of a smuggled ``doctype``.
  Neither AI Action Log row holds any sentinel in ``arguments``, ``summary`` or ``error``: the text is
  ``<withheld: N characters>`` and the summary the fixed text. A precheck that raises queues the card
  and logs the exception's type only, never through ``frappe.log_error``. A queued card keeps the whole
  proposal and targets the drafts' doctype.
* ``ai_draft``'s pure checks: a secret named by argument, line and kind and never its value (the scan
  failing closed); pictures refused by position and host, never by URL, a reference-style one included;
  raw HTML in the Markdown escaped into text.
* **A static allowlist on ``knowledge_base/ai_draft.py``**, comments and docstrings stripped: no
  ``.submit(`` and no ``transition(``; none of ``approve_and_publish``, ``request_changes``, ``withdraw``,
  ``discard``, ``retire``, ``confirm_still_accurate`` or ``supersede`` named; every attribute it reads
  from ``publish`` and ``api.knowledge_base`` one of ``run``, ``asker``, ``open_version``,
  ``start_revision``, ``article_row`` and ``submit_version``; and of the drafts' doctype it reads only a
  version's ``owner``. A control shows the check catches each of those.

The drafting tool's behavior end to end (a card queued, confirmed by the right and the wrong person,
submitted, approved by someone else) is ``AiDraftTest`` in ``test_knowledge_base_actions``.

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

#: PR 6b: the drafting tool and the module that holds its rules.
DRAFT_TOOL = "draft_knowledge_article"
AI_DRAFT = "erpnext_enhancements.knowledge_base.ai_draft"
AI_DRAFT_SOURCE = APP / "knowledge_base" / "ai_draft.py"

tools = {}
gate = None
constants = None
helper = None
draft_tool = None
ai_draft = None


def setUpModule():
	global gate, constants, helper, draft_tool, ai_draft
	install_stubs()
	gate = importlib.import_module("erpnext_enhancements.assistant_tools._gate")
	constants = importlib.import_module("erpnext_enhancements.knowledge_base.constants")
	helper = importlib.import_module(HELPER)
	for name, class_name in TOOLS.items():
		module = importlib.import_module(f"erpnext_enhancements.assistant_tools.{name}")
		tools[name] = getattr(module, class_name)()
	draft_tool = importlib.import_module(f"erpnext_enhancements.assistant_tools.{DRAFT_TOOL}").DraftKnowledgeArticle()
	ai_draft = importlib.import_module(AI_DRAFT)


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
		kb_number = tools["fetch_knowledge_article"].inputSchema["properties"]["kb_number"]["description"]
		self.assertIn("'kb 601' also works", kb_number)
		# The citation every note tells the model to write is accepted back (review fix, FAC-1).
		self.assertIn("'KB-0601 v3'", kb_number)

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
		"""The one Error Log is exactly a ``method`` and an ``error``, queued by ``deferred_insert``, and
		the request's form_dict (for an MCP call, the JSON-RPC body with the arguments) reaches nothing:
		``frappe.log_error``, which would store it, is never called. Review fix (spec-2, FAC-2, SEC-2)."""
		secret = "SENTINEL-" + "ARGS-5c1e"
		fake = types.ModuleType(AI_TOOLS)

		def broken(args):
			raise RuntimeError(f"boom {args.get('query')}")

		fake.search_payload = broken
		queued, through_log_error = [], []
		with (
			mock.patch.dict(sys.modules, {AI_TOOLS: fake}),
			mock.patch.object(
				helper.frappe,
				"form_dict",
				_rpc_body("search_company_knowledge", {"query": secret}),
				create=True,
			),
			mock.patch.object(helper.frappe, "get_doc", _queueing_get_doc(queued), create=True),
			mock.patch.object(helper.frappe, "log_error", _v16_log_error(through_log_error), create=True),
		):
			out = tools["search_company_knowledge"].execute({"query": secret})
		self.assertEqual(out, {"success": False, "error": helper.FAILURE})
		self.assertEqual(
			queued,
			[
				{
					"doctype": "Error Log",
					"method": "Knowledge base AI tool",
					"error": "search_payload raised RuntimeError",
				}
			],
		)
		self.assertEqual(through_log_error, [])
		self.assertNotIn(secret, json.dumps(out) + json.dumps(queued))

	def test_log_error_would_have_logged_the_arguments(self):
		"""What makes the test above mean something: v16's ``log_error``, called as this path called it
		before the review, stores the JSON-RPC body in the log's ``metadata``, the arguments included,
		whatever the message says. ``sanitized_dict`` masks only a top-level key named like a secret."""
		secret = "SENTINEL-" + "ARGS-77d0"
		recorded = []
		with mock.patch.object(
			helper.frappe,
			"form_dict",
			_rpc_body("fetch_knowledge_article", {"kb_number": secret}),
			create=True,
		):
			_v16_log_error(recorded)(
				title=helper.LOG_TITLE, message="fetch_payload raised RuntimeError", defer_insert=True
			)
		self.assertIn(secret, json.dumps(recorded))

	def test_a_failing_log_still_returns(self):
		fake = types.ModuleType(AI_TOOLS)
		fake.fetch_payload = lambda args: 1 / 0

		def get_doc(values):
			raise ConnectionError("the database is gone")

		def unqueueable(values):
			doc = types.SimpleNamespace(**values)

			def deferred_insert():
				raise ConnectionError("redis is down")

			doc.deferred_insert = deferred_insert
			return doc

		for failing in (get_doc, unqueueable):
			with (
				self.subTest(failing=failing.__name__),
				mock.patch.dict(sys.modules, {AI_TOOLS: fake}),
				mock.patch.object(helper.frappe, "get_doc", failing, create=True),
			):
				self.assertEqual(
					tools["fetch_knowledge_article"].execute({"kb_number": "KB-0601"}),
					{"success": False, "error": helper.FAILURE},
				)

	def test_the_failure_path_never_raises_and_never_logs_a_traceback(self):
		"""Statically: ``run`` has no ``raise``; the log is written outside the ``except`` block, so no
		traceback or frame is attached to it; and ``run`` never calls ``log_error``, whose metadata
		holds the request's form_dict."""
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
		self.assertFalse(inside & {"log_error", "get_doc", "deferred_insert", "insert"}, inside)
		attributes = {n.attr for n in ast.walk(run) if isinstance(n, ast.Attribute)}
		self.assertNotIn("get_traceback", attributes)
		self.assertNotIn("log_error", attributes)
		self.assertIn("deferred_insert", attributes)


# ================================================================== PR 6b: the drafting tool

#: Proposal text that must never reach a log row with no card, or any result. Built by concatenation.
DRAFT_SENTINELS = {
	"article_title": "Winterizing a fountain pump " + "SENTINEL-" + "TITLE-6b21",
	"summary": "How to drain and store a pump. " + "SENTINEL-" + "SUMMARY-6b21",
	"keywords": ["pump", "SENTINEL-" + "KEYWORD-6b21"],
	"body_markdown": "Drain the basin first.\n\n" + "SENTINEL-" + "BODY-6b21",
	"change_note": "First draft, from SOP-9001. " + "SENTINEL-" + "NOTE-6b21",
}
#: Invented, and built by concatenation so push protection never sees a key shape in this file.
STRIPE_KEY = "sk" + "_live_" + "a1B2" * 6
WRITTEN_PASSWORD = "Pass" + "word: Otter#" + "2931"
REQUESTER = "james@example.com"


def _draft_args(**changes):
	"""A new article's arguments, the sentinels in every text field."""
	args = {
		"department": "06 Operations",
		"kind": "SOP",
		**{key: (list(value) if isinstance(value, list) else value) for key, value in DRAFT_SENTINELS.items()},
	}
	args.update(changes)
	return args


def _sentinels():
	out = []
	for value in DRAFT_SENTINELS.values():
		for text in value if isinstance(value, list) else [value]:
			if "SENTINEL-" in text:
				out.append(text[text.index("SENTINEL-") :])
	return out


class _GateSite:
	"""Just enough of frappe for the gate's refusal path and ``ai_draft.precheck`` of a new article: one
	enabled staff user with a KB role, an AI Action Log table, a deferred-insert queue, cards that
	``_propose`` would have written, and a ``log_error`` that records every call (none is expected)."""

	def __init__(self, test, *, gating=True, roles=("KB Approver",), user=REQUESTER):
		self.logged, self.deferred, self.log_error_calls, self.proposed, self.executed = [], [], [], [], []
		site = self

		def get_doc(values):
			doc = types.SimpleNamespace(**values)
			doc.name = f"{values['doctype']}-{len(site.logged) + 1}"
			doc.insert = lambda ignore_permissions=False: site.logged.append(dict(values))
			doc.deferred_insert = lambda: site.deferred.append(dict(values))
			return doc

		def get_value(doctype, name, fieldname=None, as_dict=False, **kwargs):
			if doctype == "User" and name == user:
				row = {"enabled": 1, "user_type": "System User"}
				return row if as_dict else row.get(fieldname)
			return None

		frappe = gate.frappe
		patches = [
			mock.patch.object(gate, "_gating_enabled", lambda: gating),
			mock.patch.object(gate, "_propose", lambda tool, args: site.proposed.append(args) or {"success": True, "result": "card"}),
			mock.patch.object(frappe, "session", types.SimpleNamespace(user=user), create=True),
			mock.patch.object(frappe, "flags", types.SimpleNamespace(ai_gate_bypass=False), create=True),
			mock.patch.object(frappe, "get_doc", get_doc, create=True),
			mock.patch.object(frappe, "get_roles", lambda user=None: list(roles), create=True),
			mock.patch.object(
				frappe, "db", types.SimpleNamespace(get_value=get_value, set_value=lambda *a, **k: None), create=True
			),
			mock.patch.object(frappe, "log_error", lambda *a, **k: site.log_error_calls.append((a, k)), create=True),
			mock.patch.object(frappe, "get_traceback", lambda *a, **k: "Traceback: stub", create=True),
		]
		for patch in patches:
			patch.start()
			test.addCleanup(patch.stop)

	def run(self, arguments, tool=None):
		def original(tool, args):
			self.executed.append(args)
			return {"success": True, "result": "ran"}

		return gate._gated_execute(tool or draft_tool, original, arguments)

	def action_log_rows(self):
		return [row for row in self.logged if row.get("doctype") == "AI Action Log"]


class TestDraftToolClassification(unittest.TestCase):
	def test_a_medium_risk_app_write(self):
		"""Not Low (one of its modes puts a version in front of the approvers and emails them), not High
		(nothing is destroyed, and the author's side can withdraw and discard in the Desk)."""
		self.assertIn(DRAFT_TOOL, gate.APP_MUTATING)
		for other in ("LOW_RISK", "HIGH_RISK", "EXPLICIT_READONLY", "EXPLICIT_MUTATING", "EXEMPTABLE_TOOLS"):
			self.assertNotIn(DRAFT_TOOL, getattr(gate, other), other)
		self.assertTrue(gate.is_mutating(draft_tool))
		self.assertEqual(gate.classify_risk(DRAFT_TOOL), "Medium")
		self.assertEqual(gate.classify_risk(DRAFT_TOOL, "write"), "Medium")
		self.assertEqual(
			draft_tool.annotations,
			{
				"readOnlyHint": False,
				"destructiveHint": False,
				"idempotentHint": False,
				"x-ee-mutation": True,
				"x-ee-risk": "medium",
			},
		)
		from erpnext_enhancements.ai_governance.fac_tool_categories import category_for_annotations

		self.assertEqual(category_for_annotations(draft_tool.annotations), "write")
		self.assertEqual(gate.classify_risk("create_training_draft_version"), "Medium")  # the same band

	def test_the_gate_entries(self):
		self.assertIn(DRAFT_TOOL, gate.APP_PRECHECKED_TOOLS)
		self.assertNotIn(DRAFT_TOOL, gate.PRECHECKED_TOOLS)
		self.assertEqual(
			gate.WITHHELD_WHEN_UNQUEUED[DRAFT_TOOL],
			("article_title", "summary", "keywords", "body_markdown", "change_note"),
		)
		self.assertEqual(set(gate.WITHHELD_WHEN_UNQUEUED[DRAFT_TOOL]), set(ai_draft.SCANNED))
		self.assertEqual(gate.TOOL_TARGET_DOCTYPES, {DRAFT_TOOL: constants.VERSION_DOCTYPE})
		self.assertIn(constants.VERSION_DOCTYPE, gate.KNOWLEDGE_BASE_DOCTYPES)
		self.assertIn(constants.VERSION_DOCTYPE, gate.NEVER_EXEMPT)

	def test_what_the_gate_did_not_change(self):
		"""The denylist, NEVER_EXEMPT and the exemptable tools are exactly what PR 1 left them."""
		self.assertEqual(gate.DENYLIST_DOCTYPES, frozenset({"Triton Chat Attachment", constants.VERSION_DOCTYPE}))
		self.assertEqual(gate.EXEMPTABLE_TOOLS, {"create_document", "update_document"})
		self.assertEqual(gate.NEVER_EXEMPT, frozenset({"Task"}) | gate.GATE_OWN_DOCTYPES | gate.KNOWLEDGE_BASE_DOCTYPES)

	def test_the_denylist_still_reads_its_arguments(self):
		self.assertIsNone(gate.denylist_hit(DRAFT_TOOL, _draft_args(kb_number="KB-0601")))
		self.assertEqual(
			gate.denylist_hit(DRAFT_TOOL, {**_draft_args(), "doctype": constants.VERSION_DOCTYPE}),
			constants.VERSION_DOCTYPE,
		)

	def test_the_cards_target_is_the_drafts_doctype_whatever_the_arguments_say(self):
		self.assertEqual(gate._call_target(DRAFT_TOOL, _draft_args()), (constants.VERSION_DOCTYPE, None))
		self.assertEqual(
			gate._call_target(DRAFT_TOOL, {**_draft_args(), "doctype": "Task", "name": "TASK-1"}),
			(constants.VERSION_DOCTYPE, None),
		)
		self.assertEqual(gate._call_target("update_document", {"doctype": "Task", "name": "TASK-1"}), ("Task", "TASK-1"))
		self.assertEqual(gate._call_target("update_document", None), (None, None))


class TestDraftToolContract(unittest.TestCase):
	def test_metadata(self):
		self.assertEqual(draft_tool.name, DRAFT_TOOL)
		self.assertEqual(draft_tool.source_app, "erpnext_enhancements")
		self.assertEqual(draft_tool.requires_permission, "Knowledge Article " + "Version")
		self.assertEqual(draft_tool.requires_permission, constants.VERSION_DOCTYPE)

	def test_the_description(self):
		text = draft_tool.description
		self.assertLessEqual(len(text), 600)
		for phrase in (
			"Sapphire Fountains' company knowledge base",
			"submit_for_review",
			"the person who asked confirms the card in ERPNext",
			"check_ai_pending_action",
			"It never approves or publishes",
			"Draft text is never returned.",
		):
			self.assertIn(phrase, text)

	def test_the_schema(self):
		schema = draft_tool.inputSchema
		self.assertEqual(
			sorted(schema["properties"]),
			sorted(ai_draft.ARGUMENTS),
		)
		self.assertEqual(schema["required"], ["article_title", "kind", "summary", "body_markdown", "change_note"])
		self.assertEqual(schema["required"], list(ai_draft.REQUIRED))
		properties = schema["properties"]
		self.assertEqual(
			(properties["submit_for_review"]["type"], properties["submit_for_review"]["default"]),
			("boolean", False),
		)
		self.assertIn("recorded as its submitter", properties["submit_for_review"]["description"])
		self.assertEqual(properties["kind"]["enum"], list(constants.ARTICLE_KINDS))
		for kind in constants.ARTICLE_KINDS:
			self.assertIn(f"{kind}: {constants.KIND_HELP[kind]}", properties["kind"]["description"])
		self.assertEqual(properties["department"]["enum"], list(constants.DEPARTMENT_BLOCK_OPTIONS))
		self.assertEqual((properties["keywords"]["type"], properties["keywords"]["items"]), ("array", {"type": "string"}))
		for name, limit in (("article_title", "140"), ("summary", "500"), ("body_markdown", "60,000"), ("change_note", "1,000")):
			self.assertIn(limit, properties[name]["description"], name)
		self.assertIn("what changed and why, and where it came from", properties["change_note"]["description"].lower())

	def test_no_property_named_title_doctype_or_id_and_no_length_keywords(self):
		for path, prop, spec in _properties(draft_tool.inputSchema):
			self.assertNotIn(prop, {"title", "doctype", "id"}, path)
			self.assertIn("type", spec, path)
			self.assertTrue(spec.get("description") or spec.get("enum"), path)
		self.assertFalse(_keywords(draft_tool.inputSchema) & {"maxLength", "maxItems", "minLength", "minItems"})
		self.assertEqual(json.loads(json.dumps(draft_tool.inputSchema)), draft_tool.inputSchema)

	def test_the_hook_registers_it_right_after_the_read_tools(self):
		paths = hook_value("assistant_tools")
		path = f"erpnext_enhancements.assistant_tools.{DRAFT_TOOL}.DraftKnowledgeArticle"
		self.assertEqual(paths.count(path), 1)
		self.assertTrue(paths[paths.index(path) - 1].endswith(".ListCompanyKnowledge"))

	def test_the_wrapper_imports_only_assistant_tools_at_module_scope(self):
		tree = ast.parse((APP / "assistant_tools" / f"{DRAFT_TOOL}.py").read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("erpnext_enhancements."):
				self.assertTrue(node.module.startswith("erpnext_enhancements.assistant_tools._"), node.module)

	def test_execute_and_precheck_hand_the_call_to_ai_draft(self):
		calls = []
		fake = types.ModuleType(AI_DRAFT)
		fake.from_card = lambda args: calls.append(("from_card", args)) or {"ok": 1}
		fake.precheck = lambda args, requester: calls.append(("precheck", args, requester)) or ["a problem"]
		with (
			mock.patch.dict(sys.modules, {AI_DRAFT: fake}),
			mock.patch.object(helper.frappe, "session", types.SimpleNamespace(user=REQUESTER), create=True),
			mock.patch("erpnext_enhancements.knowledge_base.ai_draft", fake, create=True),
		):
			self.assertEqual(draft_tool.execute({"kind": "SOP"}), {"ok": 1})
			self.assertEqual(draft_tool.precheck({"kind": "SOP"}), ["a problem"])
			self.assertEqual(draft_tool.precheck(None), ["a problem"])
		self.assertEqual(
			calls,
			[("from_card", {"kind": "SOP"}), ("precheck", {"kind": "SOP"}, REQUESTER), ("precheck", {}, REQUESTER)],
		)

	def test_an_unexpected_failure_is_a_return_with_only_the_type_logged(self):
		fake = types.ModuleType(AI_DRAFT)

		def broken(args):
			raise RuntimeError("boom " + json.dumps(args))

		fake.from_card = broken
		queued, through_log_error = [], []
		with (
			mock.patch.dict(sys.modules, {AI_DRAFT: fake}),
			mock.patch("erpnext_enhancements.knowledge_base.ai_draft", fake, create=True),
			mock.patch.object(helper.frappe, "form_dict", _rpc_body(DRAFT_TOOL, _draft_args()), create=True),
			mock.patch.object(helper.frappe, "get_doc", _queueing_get_doc(queued), create=True),
			mock.patch.object(helper.frappe, "log_error", _v16_log_error(through_log_error), create=True),
		):
			out = draft_tool.execute(_draft_args())
		self.assertEqual(out, {"success": False, "error": helper.DRAFT_FAILURE})
		self.assertEqual(
			queued,
			[{"doctype": "Error Log", "method": "Knowledge base AI draft", "error": "from_card raised RuntimeError"}],
		)
		self.assertEqual(through_log_error, [])
		for sentinel in _sentinels():
			self.assertNotIn(sentinel, json.dumps(out) + json.dumps(queued))


class TestDraftCardLines(unittest.TestCase):
	def test_the_four_summaries(self):
		base = {"article_title": "Winterizing a fountain pump", "department": "06 Operations", "kind": "SOP"}
		lines = {
			(False, False): "Draft a new knowledge article “Winterizing a fountain pump” (06 Operations, SOP), a Draft only",
			(False, True): (
				"Draft a new knowledge article “Winterizing a fountain pump” (06 Operations, SOP) and SUBMIT it "
				"for review as you"
			),
			(True, False): "Draft a revision of KB-0601: “Winterizing a fountain pump”, a Draft only",
			(True, True): "Draft a revision of KB-0601: “Winterizing a fountain pump” and SUBMIT it for review as you",
		}
		for (revision, submit), expected in lines.items():
			with self.subTest(revision=revision, submit=submit):
				args = {**base, "submit_for_review": submit}
				if revision:
					args["kb_number"] = "KB-0601"
				self.assertEqual(gate.summarize_tool_call(DRAFT_TOOL, args), expected)

	def test_only_a_real_true_submits(self):
		"""The card says SUBMIT exactly when ai_draft would submit (``is True``); anything else is refused
		by the precheck anyway."""
		for value in ("true", 1, "yes", None):
			with self.subTest(value=value):
				line = gate.summarize_tool_call(DRAFT_TOOL, {"article_title": "T", "submit_for_review": value})
				self.assertTrue(line.endswith(", a Draft only"), line)


class TestDraftGateRefusals(unittest.TestCase):
	"""Through ``_gated_execute`` with the real ``insert_action_log``: a call the tool's precheck refuses,
	and one the denylist refuses, each leave one AI Action Log row with no card, and no sentinel of the
	proposal in its ``arguments``, ``summary`` or ``error``."""

	def assertNoSentinel(self, row):
		text = json.dumps({key: row.get(key) for key in ("arguments", "summary", "error")}, default=str)
		for sentinel in [*_sentinels(), STRIPE_KEY, "Otter#"]:
			self.assertNotIn(sentinel, text)

	def test_a_precheck_refusal_is_not_queued_and_its_log_row_withholds_the_text(self):
		site = _GateSite(self)
		args = _draft_args(body_markdown=DRAFT_SENTINELS["body_markdown"] + "\n\nKey: " + STRIPE_KEY)
		out = site.run(args)
		self.assertFalse(out["success"])
		self.assertEqual(out["error_type"], "AIGateValidationError")
		self.assertTrue(out["error"].startswith("Not queued: the text looks like it contains a secret ("), out["error"])
		self.assertIn("body_markdown line 5 looks like a Stripe secret key", out["error"])
		self.assertIn("Nothing was queued for confirmation", out["error"])
		self.assertNotIn(STRIPE_KEY, out["error"])
		self.assertEqual((site.proposed, site.executed), ([], []))
		(row,) = site.action_log_rows()
		self.assertEqual(row["summary"], gate.WITHHELD_SUMMARY)
		self.assertEqual(row["summary"], "Draft knowledge article (text withheld)")
		self.assertEqual((row["success"], row["error_type"], row["pending_action"]), (0, "AIGateValidationError", None))
		self.assertEqual((row["target_doctype"], row["target_name"]), (constants.VERSION_DOCTYPE, None))
		arguments = json.loads(row["arguments"])
		for key in gate.WITHHELD_WHEN_UNQUEUED[DRAFT_TOOL]:
			self.assertRegex(arguments[key], r"^<withheld: \d+ characters>$", key)
		self.assertEqual(arguments["article_title"], f"<withheld: {len(args['article_title'])} characters>")
		self.assertEqual((arguments["kind"], arguments["department"]), ("SOP", "06 Operations"))
		self.assertNoSentinel(row)
		self.assertEqual(site.log_error_calls, [])

	def test_a_written_out_password_is_refused_before_any_card(self):
		"""The WI-080 acceptance example: an invented password written out in the text."""
		site = _GateSite(self)
		out = site.run(_draft_args(change_note=WRITTEN_PASSWORD))
		self.assertIn("change_note line 1 looks like a written-out password", out["error"])
		self.assertNotIn("Otter#", out["error"])
		self.assertEqual(site.proposed, [])
		(row,) = site.action_log_rows()
		self.assertNoSentinel(row)

	def test_a_smuggled_doctype_is_refused_by_the_denylist_with_the_text_withheld(self):
		for gating in (True, False):
			with self.subTest(gating=gating):
				site = _GateSite(self, gating=gating)
				out = site.run({**_draft_args(), "doctype": constants.VERSION_DOCTYPE})
				self.assertFalse(out["success"])
				self.assertTrue(out["error"].startswith(f"Refused: {constants.VERSION_DOCTYPE} "))
				self.assertEqual((site.proposed, site.executed), ([], []))
				(row,) = site.action_log_rows()
				self.assertEqual((row["risk"], row["summary"]), ("High", gate.WITHHELD_SUMMARY))
				self.assertNoSentinel(row)

	def test_a_passing_precheck_queues_the_card(self):
		site = _GateSite(self)
		out = site.run(_draft_args(submit_for_review=True))
		self.assertEqual(out, {"success": True, "result": "card"})
		self.assertEqual(len(site.proposed), 1)
		self.assertEqual(site.action_log_rows(), [])

	def test_a_precheck_that_raises_queues_the_card_and_logs_only_the_type(self):
		site = _GateSite(self)
		with (
			mock.patch.object(gate.frappe, "form_dict", _rpc_body(DRAFT_TOOL, _draft_args()), create=True),
			mock.patch.object(ai_draft, "precheck", mock.Mock(side_effect=KeyError("the users table is gone"))),
		):
			out = site.run(_draft_args())
		self.assertEqual(out, {"success": True, "result": "card"})
		self.assertEqual(len(site.proposed), 1)
		self.assertEqual(site.log_error_calls, [])
		(logged,) = site.deferred
		self.assertEqual(logged["doctype"], "Error Log")
		self.assertEqual(logged["method"], f"AI gate pre-check failed for {DRAFT_TOOL}")
		self.assertIn("precheck raised KeyError", logged["error"])
		self.assertEqual(set(logged), {"doctype", "method", "error"})

	def test_someone_without_a_kb_role_is_refused_before_any_card(self):
		site = _GateSite(self, roles=("Desk User",))
		out = site.run(_draft_args())
		self.assertIn("only a KB Author or KB Approver can have an AI draft a knowledge-base article", out["error"])
		self.assertEqual(site.proposed, [])

	def test_a_gate_failure_on_this_tool_logs_only_the_type(self):
		site = _GateSite(self)
		with (
			mock.patch.object(gate, "_propose", mock.Mock(side_effect=ValueError("card insert failed"))),
			mock.patch.object(gate.frappe, "form_dict", _rpc_body(DRAFT_TOOL, _draft_args()), create=True),
		):
			out = site.run(_draft_args())
		self.assertFalse(out["success"])
		self.assertIn("internal error", out["error"])
		self.assertEqual(site.log_error_calls, [])
		self.assertEqual(
			site.deferred, [{"doctype": "Error Log", "method": f"AI gate failure for {DRAFT_TOOL}", "error": "ValueError"}]
		)

	def test_a_queued_cards_rows_keep_the_whole_proposal(self):
		"""Decided 2026-09-28: the card is how the person who asked reads what they confirm."""
		site = _GateSite(self)
		args = _draft_args()
		gate.insert_action_log(user=REQUESTER, tool_name=DRAFT_TOOL, arguments=args, success=1, pending_action="AI-PA-1")
		(row,) = site.action_log_rows()
		self.assertEqual(json.loads(row["arguments"]), args)
		self.assertEqual(row["summary"], gate.summarize_tool_call(DRAFT_TOOL, args))
		self.assertEqual(row["target_doctype"], constants.VERSION_DOCTYPE)

	def test_withholding_touches_only_the_listed_tools_and_keys(self):
		args = _draft_args()
		self.assertIs(gate.withhold_arguments("create_document", args), args)
		self.assertEqual(gate.withhold_arguments(DRAFT_TOOL, "not a dict"), "not a dict")
		kept = gate.withhold_arguments(DRAFT_TOOL, {**args, "kb_number": "KB-0601"})
		self.assertEqual((kept["kb_number"], kept["kind"]), ("KB-0601", "SOP"))
		self.assertEqual(kept["keywords"], f"<withheld: {len(json.dumps(args['keywords']))} characters>")


class TestDraftProposeCard(unittest.TestCase):
	"""The real ``_propose``: the card targets the drafts' doctype with no name yet, carries the card
	line, and keeps the proposal whole."""

	def test_the_card(self):
		inserted = []

		class Card:
			def __init__(self, values):
				self.__dict__.update(values)
				self.name = "AI-PA-2026-00001"

			def insert(self, ignore_permissions=False):
				inserted.append(dict(self.__dict__))

		frappe = gate.frappe
		db = types.SimpleNamespace(get_value=lambda *a, **k: None, get_single_value=lambda *a, **k: 1, commit=lambda: None)
		with (
			mock.patch.object(frappe, "session", types.SimpleNamespace(user=REQUESTER), create=True),
			mock.patch.object(frappe, "db", db, create=True),
			mock.patch.object(frappe, "get_doc", lambda values: Card(values), create=True),
			mock.patch.object(frappe.utils, "cint", lambda v: int(v or 0), create=True),
			mock.patch.object(frappe.utils, "add_to_date", lambda *a, **k: "2026-09-28 19:00:00", create=True),
			mock.patch.object(gate, "_tool_category", lambda tool: "write"),
			mock.patch.object(gate, "_notify_requester", lambda action: None),
			mock.patch.object(gate, "_fingerprint_key", lambda: "site-key"),
		):
			args = _draft_args(submit_for_review=True)
			out = gate._propose(draft_tool, args)
		self.assertTrue(out["success"])
		(card,) = inserted
		self.assertEqual((card["target_doctype"], card["target_name"]), (constants.VERSION_DOCTYPE, None))
		self.assertEqual((card["risk"], card["tool_name"], card["status"]), ("Medium", DRAFT_TOOL, "Pending"))
		self.assertEqual(card["summary"], gate.summarize_tool_call(DRAFT_TOOL, args))
		self.assertIn("SUBMIT it for review as you", card["summary"])
		self.assertEqual(json.loads(card["arguments"]), args)
		self.assertIsNone(card["sealed_arguments"])  # no argument name looks like a credential


class TestAiDraftPureChecks(unittest.TestCase):
	def test_a_secret_is_named_by_argument_line_and_kind_never_by_value(self):
		problems = ai_draft.secret_problems(
			{
				"article_title": "Pump care",
				"summary": "fine",
				"keywords": ["pump", WRITTEN_PASSWORD],
				"body_markdown": "Step one.\nStep two.\nUse " + STRIPE_KEY + " here.",
				"change_note": "ok",
			}
		)
		(problem,) = problems
		self.assertIn("keyword 2 looks like a written-out password", problem)
		self.assertIn("body_markdown line 3 looks like a Stripe secret key", problem)
		self.assertNotIn(STRIPE_KEY, problem)
		self.assertNotIn("Otter#", problem)
		self.assertEqual(ai_draft.secret_problems({"body_markdown": "Nothing secret here."}), [])

	def test_the_markdown_source_is_scanned_because_markdown2_mangles_underscores(self):
		"""``sk_live_...`` converts to ``sk<em>live</em>...``, which no longer looks like a key."""
		html = ai_draft.markdown_html("Use sk" + "_live_abc_def here.")
		self.assertIn("<em>live</em>", html)
		self.assertTrue(ai_draft.secret_problems({"body_markdown": STRIPE_KEY}))

	def test_the_scan_fails_closed(self):
		with mock.patch.object(ai_draft.content, "secret_findings", mock.Mock(side_effect=ValueError("regex"))):
			self.assertEqual(ai_draft.secret_problems(_draft_args()), [ai_draft.SECRETS_UNCHECKED])

	def test_html_in_the_markdown_becomes_visible_text(self):
		html = ai_draft.markdown_html(
			'Press <b>Save</b>.\n\n<script>alert(1)</script>\n\n<p class="hidden">hidden</p>\n\n'
			'<img src="https://example.org/x.png">'
		)
		self.assertIn("&lt;b&gt;Save&lt;/b&gt;", html)
		self.assertIn("&lt;script&gt;", html)
		self.assertIn("&lt;p class=\"hidden\"&gt;", html)
		for tag in ("<b>", "<script", "<p class", "<img"):
			self.assertNotIn(tag, html)
		self.assertEqual(ai_draft.picture_problems(html), [])  # an escaped <img> is text, not a picture

	def test_a_reference_style_picture_is_refused_by_position_and_host_never_by_url(self):
		url = "https://evil.example.org:8443/deep/path/secret.png?token=abc123"
		html = ai_draft.markdown_html(
			"![own](https://erp.example.com/private/files/slip.png?fid=file-own)\n\n![ref][1]\n\n[1]: " + url
		)
		(problem,) = ai_draft.picture_problems(
			html, kb_number="KB-0601", allowed_fids={"file-own"}, site_url="https://erp.example.com"
		)
		self.assertIn("picture 2, from evil.example.org", problem)
		self.assertIn("KB-0601's own pictures", problem)
		self.assertNotIn("picture 1", problem)
		for piece in ("/deep/path", "secret.png", "token", "abc123", "8443", "https://"):
			self.assertNotIn(piece, problem)

	def test_a_new_article_embeds_no_picture(self):
		html = ai_draft.markdown_html(
			"![a](/private/files/x.png)\n\n![b](https://example.org/y.png)\n\n![c](data:image/png;base64,AAAA)"
		)
		(problem,) = ai_draft.picture_problems(html)
		self.assertIn("picture 1, a file on this site", problem)
		self.assertIn("picture 2, from example.org", problem)
		self.assertIn("picture 3, embedded data", problem)
		self.assertIn("add pictures in the Desk", problem)

	def test_a_revision_keeps_the_articles_own_pictures_by_fid_or_path(self):
		html = ai_draft.markdown_html(
			"![a](https://erp.example.com/private/files/a.png?fid=file-a)\n\n![b](/private/files/b%20c.png)"
		)
		self.assertEqual(
			ai_draft.picture_problems(
				html,
				kb_number="KB-0601",
				allowed_fids={"file-a"},
				allowed_paths={"/private/files/b c.png"},
				site_url="https://erp.example.com",
			),
			[],
		)
		# The same site's file that is not the article's, and a look-alike host, are refused.
		(problem,) = ai_draft.picture_problems(
			ai_draft.markdown_html(
				"![x](/private/files/other.png?fid=file-x)\n\n![y](https://erp.example.com.evil.org/private/files/a.png?fid=file-a)"
			),
			kb_number="KB-0601",
			allowed_fids={"file-a"},
			site_url="https://erp.example.com",
		)
		self.assertIn("picture 1, a file on this site", problem)
		self.assertIn("picture 2, from erp.example.com.evil.org", problem)


class TestAiDraftStaysADraft(unittest.TestCase):
	"""Statically, comments and docstrings stripped: ``ai_draft.py`` submits nothing, moves nothing but
	through ``submit_version``, names no other move, reads only the allowed ``publish`` and
	``api.knowledge_base`` attributes, and reads nothing of a version but its owner."""

	ALLOWED = {"run", "asker", "open_version", "start_revision", "article_row", "submit_version"}
	FORBIDDEN = {
		"approve_and_publish",
		"request_changes",
		"withdraw",
		"discard",
		"retire",
		"confirm_still_accurate",
		"supersede",
	}
	MODULES = {"erpnext_enhancements.knowledge_base.publish", "erpnext_enhancements.api.knowledge_base"}

	def facts(self, source):
		"""What the check needs from ``source``: its calls, identifiers, non-docstring strings, the
		names bound to the two modules, what is read from them, and every call naming the drafts'
		doctype by the ``VERSION`` constant."""
		tree = ast.parse(source)
		docstrings = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
				body = node.body
				if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
					docstrings.add(id(body[0].value))
		aliases, imported = set(), set()
		for node in ast.walk(tree):
			if isinstance(node, ast.ImportFrom):
				for alias in node.names:
					if f"{node.module}.{alias.name}" in self.MODULES:
						aliases.add(alias.asname or alias.name)
					elif node.module in self.MODULES:
						imported.add(alias.name)
			elif isinstance(node, ast.Import):
				for alias in node.names:
					if alias.name in self.MODULES:
						aliases.add(alias.asname or alias.name)
		reads, bases, identifiers, strings, calls, version_calls = set(), set(), set(), set(), set(), []
		for node in ast.walk(tree):
			if isinstance(node, ast.Attribute):
				identifiers.add(node.attr)
				if isinstance(node.value, ast.Name) and node.value.id in aliases:
					reads.add(node.attr)
					bases.add(id(node.value))
			elif isinstance(node, ast.Name):
				identifiers.add(node.id)
			elif isinstance(node, ast.FunctionDef | ast.ClassDef):
				identifiers.add(node.name)
			elif isinstance(node, ast.alias):
				identifiers.add(node.asname or node.name.rsplit(".", 1)[-1])
			elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
				strings.add(node.value)
			if isinstance(node, ast.Call):
				func = node.func
				calls.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
				if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "VERSION":
					version_calls.append(node)
		loose = [
			node.id
			for node in ast.walk(tree)
			if isinstance(node, ast.Name) and node.id in aliases and id(node) not in bases
		]
		return {
			"aliases": aliases,
			"imported": imported,
			"reads": reads,
			"loose": loose,
			"identifiers": identifiers,
			"strings": strings,
			"calls": calls,
			"version_calls": version_calls,
		}

	def problems(self, source):
		facts = self.facts(source)
		found = []
		if "submit" in facts["calls"]:
			found.append(".submit(")
		if "transition" in facts["calls"]:
			found.append("transition(")
		named = {name.casefold() for name in facts["identifiers"]} | facts["strings"]
		found += sorted(self.FORBIDDEN & named)
		found += sorted(facts["reads"] - self.ALLOWED)
		found += sorted(facts["imported"] - self.ALLOWED)
		found += [f"{name} used other than by attribute" for name in facts["loose"]]
		for call in facts["version_calls"]:
			func = call.func.attr if isinstance(call.func, ast.Attribute) else ""
			fields = [arg.value for arg in call.args[2:] if isinstance(arg, ast.Constant)]
			if func in ("get_url_to_form", "has_permission"):
				continue
			if func == "get_value" and fields == ["owner"]:
				continue
			found.append(f"{func} reads the drafts' doctype")
		return found, facts

	def test_ai_draft_keeps_to_the_allowlist(self):
		problems, facts = self.problems(AI_DRAFT_SOURCE.read_text(encoding="utf-8"))
		self.assertEqual(problems, [])
		# Both modules are really used, through the allowed names, so the check is not vacuous.
		self.assertEqual(facts["aliases"], {"publish", "kb_api"})
		self.assertEqual(facts["reads"], self.ALLOWED)
		self.assertNotIn("Knowledge Article " + "Version", facts["strings"])
		self.assertNotIn("log_error", facts["calls"])

	def test_the_check_catches_each_forbidden_move(self):
		samples = {
			"doc.submit()": ".submit(",
			"publish.transition(doc, 'x')": "transition(",
			"kb_api.approve_and_publish(doc)": "approve_and_publish",
			"workflow.WITHDRAW": "withdraw",
			"getattr(kb_api, 'retire')": "retire",
			"publish.publish(doc, None)": "publish",
			"x = kb_api": "kb_api used other than by attribute",
			"frappe.get_doc(VERSION, name)": "get_doc reads the drafts' doctype",
			"frappe.db.get_value(VERSION, name, 'body')": "get_value reads the drafts' doctype",
		}
		header = (
			"from erpnext_enhancements.knowledge_base import publish\n"
			"from erpnext_enhancements.api import knowledge_base as kb_api\n"
		)
		for line, expected in samples.items():
			with self.subTest(line=line):
				problems, _facts = self.problems(header + line + "\n")
				self.assertIn(expected, problems)
		problems, _facts = self.problems("from erpnext_enhancements.knowledge_base.publish import transition\n")
		self.assertIn("transition", problems)
		# A comment or a docstring naming a move is not a move.
		problems, _facts = self.problems('"""It never approves (approve_and_publish)."""\n# nor .submit(\n')
		self.assertEqual(problems, [])


def _rpc_body(name, arguments):
	"""What v16's ``make_form_dict`` makes ``frappe.form_dict`` for a call to FAC's ``handle_mcp``: the
	whole JSON-RPC message, a JSON body being loaded as it is (``app.py:363-376``)."""
	return {
		"jsonrpc": "2.0",
		"id": 7,
		"method": "tools/call",
		"params": {"name": name, "arguments": arguments},
	}


def _queueing_get_doc(queued):
	"""``frappe.get_doc(values)`` whose ``deferred_insert`` queues the values as they stand."""

	def get_doc(values):
		doc = types.SimpleNamespace(**values)
		doc.deferred_insert = lambda: queued.append(dict(values))
		return doc

	return get_doc


def _v16_log_error(recorded):
	"""v16's ``frappe.log_error`` as far as what its Error Log stores: ``metadata`` from
	``get_error_metadata``, which for a web request is ``sanitized_dict(frappe.form_dict)``
	(``utils/error.py:81``, ``:159``), masking only a top-level key that contains a secret's word
	(``utils/logger.py:115-134``)."""
	blocklist = ("password", "passwd", "secret", "token", "key", "pwd")

	def log_error(
		title=None, message=None, reference_doctype=None, reference_name=None, *, defer_insert=False
	):
		form_dict = {
			key: "********" if any(word in key for word in blocklist) else value
			for key, value in dict(getattr(helper.frappe, "form_dict", None) or {}).items()
		}
		metadata = {"type": "http_request", "form_dict": form_dict}
		recorded.append({"method": title, "error": message, "metadata": json.dumps(metadata)})

	return log_error


if __name__ == "__main__":
	unittest.main()

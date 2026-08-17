# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The feedback API's HTTP surface: what is exposed, and how it may be called.

Same three properties ``test_training_endpoint_surface`` pins for the learner runtime, for
the same reasons — that file records what it cost to discover them late.

1. **Every whitelisted endpoint declares POST.** Asserted on the decorator, not on a list
   somebody maintains. These calls carry a request id and a reviewer's decision, and a GET
   puts both in the query string: the web server's access log, the browser's history, and the
   ``Referer`` header of whatever the reader clicks next.
2. **Every method name the SPA can dial resolves to one of them.** A rename in
   ``api/feedback.py`` with no matching edit to ``transport.js``'s ``M`` map is a 404 the user
   sees and CI does not.
3. **Set equality, not a subset check.** A new endpoint cannot be added and left both
   un-wired and un-explained: it is either in the map or in
   :data:`NOT_DIALLED_BY_THE_SPA` with a reason. Asymmetries are allowed; silence about them
   is not.

A fourth property is specific to this feature and is the one worth the most:

4. **Nothing writes a ``Task`` outside ``task_writer``.** The whole design is that a model's
   output reaches a live project board only through a call a human made. That is easy to
   restate in a docstring and easy to undo in a hurry, so it is asserted structurally.

Bench-free: AST over ``api/feedback.py`` plus a text read of the transport module.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_endpoint_surface
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
API = APP / "api" / "feedback.py"
TRANSPORT = APP / "public" / "js" / "feedback" / "transport.js"
MODULE = APP / "product_feedback"

#: Whitelisted endpoints the SPA's ``M`` map deliberately does not carry. Each needs a reason
#: that survives somebody reading it.
NOT_DIALLED_BY_THE_SPA: dict[str, str] = {}


def _tree():
	return ast.parse(API.read_text(encoding="utf-8"))


def _whitelisted():
	"""Every whitelisted function in ``api/feedback.py``, mapped to its decorator node."""
	found = {}
	for node in ast.walk(_tree()):
		if not isinstance(node, ast.FunctionDef):
			continue
		for dec in node.decorator_list:
			target = dec.func if isinstance(dec, ast.Call) else dec
			if getattr(target, "attr", "") == "whitelist":
				found[node.name] = dec
	return found


def _declares_post(decorator) -> bool:
	"""Does this ``@frappe.whitelist(...)`` carry ``methods=["POST"]``?"""
	if not isinstance(decorator, ast.Call):
		return False
	for keyword in decorator.keywords:
		if keyword.arg != "methods":
			continue
		if not isinstance(keyword.value, ast.List):
			return False
		values = [
			element.value
			for element in keyword.value.elts
			if isinstance(element, ast.Constant) and isinstance(element.value, str)
		]
		return values == ["POST"]
	return False


def _method_map():
	"""The endpoint names ``transport.js`` can dial, from its exported ``M`` map.

	Read from the map's own braces rather than by grepping the file for identifiers: the
	module names endpoints in prose too, and a scan that cannot tell a comment from a dispatch
	table is satisfied by deleting the comment.
	"""
	src = TRANSPORT.read_text(encoding="utf-8")
	match = re.search(r"export const M = \{(.*?)\n\};", src, re.S)
	assert match, "the M map has moved or changed shape; re-derive this scan"
	body = re.sub(r"//.*$", "", match.group(1), flags=re.M)
	dotted = re.findall(r'"([A-Za-z0-9_.]+)"', body)
	return {name.rsplit(".", 1)[-1] for name in dotted}, dotted


class TestEndpointSurface(unittest.TestCase):
	def test_every_endpoint_declares_post_only(self):
		offenders = [
			name for name, decorator in _whitelisted().items() if not _declares_post(decorator)
		]
		self.assertEqual(
			offenders,
			[],
			"These endpoints answer a GET as well as a POST, which writes the request id and "
			"the reviewer's decision into access logs, history and Referer: "
			+ ", ".join(offenders),
		)

	def test_no_endpoint_allows_guest(self):
		"""``allow_guest`` on any of these would expose the whole queue to the internet."""
		for name, decorator in _whitelisted().items():
			with self.subTest(endpoint=name):
				if not isinstance(decorator, ast.Call):
					continue
				for keyword in decorator.keywords:
					self.assertNotEqual(
						keyword.arg, "allow_guest", f"{name}() is whitelisted for guests"
					)

	def test_every_dialled_name_resolves(self):
		dialled, dotted = _method_map()
		endpoints = set(_whitelisted())
		missing = sorted(dialled - endpoints)
		self.assertEqual(
			missing,
			[],
			"transport.js dials these and api/feedback.py has no such function: " + ", ".join(missing),
		)

		# The dotted path has to be right too — a correct function name under the wrong module
		# is the same 404 with a more confusing traceback.
		for path in dotted:
			with self.subTest(path=path):
				self.assertTrue(
					path.startswith("erpnext_enhancements.api.feedback."),
					f"{path} does not point at api/feedback.py",
				)

	def test_every_endpoint_is_dialled_or_explained(self):
		dialled, _ = _method_map()
		endpoints = set(_whitelisted())
		unexplained = sorted(endpoints - dialled - set(NOT_DIALLED_BY_THE_SPA))
		self.assertEqual(
			unexplained,
			[],
			"These endpoints are neither dialled by the SPA nor listed in "
			"NOT_DIALLED_BY_THE_SPA with a reason: " + ", ".join(unexplained),
		)

	def test_stale_exemptions_are_removed(self):
		endpoints = set(_whitelisted())
		stale = sorted(set(NOT_DIALLED_BY_THE_SPA) - endpoints)
		self.assertEqual(stale, [], "NOT_DIALLED_BY_THE_SPA names functions that no longer exist")


class TestOnlyOneWriterCreatesTasks(unittest.TestCase):
	"""The boundary the whole feature exists to hold.

	A model proposes; a human confirms; **one** module writes. Restating that in prose is
	cheap and undoing it in a hurry is cheaper, so it is asserted against the source.
	"""

	#: The one module allowed to construct a ``Task``.
	WRITER = "task_writer.py"

	def test_no_other_module_constructs_a_task(self):
		pattern = re.compile(r'["\']doctype["\']\s*:\s*["\']Task["\']')
		offenders = []
		for path in sorted(MODULE.rglob("*.py")) + [API]:
			if path.name == self.WRITER:
				continue
			source = path.read_text(encoding="utf-8")
			# Strip docstrings and comments before matching: this rule is discussed at length
			# in several of these files, and a scan that matches its own prose inverts the
			# assertion. That mistake inverted four assertions in the chat work.
			stripped = _strip_prose(source)
			if pattern.search(stripped):
				offenders.append(str(path.relative_to(APP)))
		self.assertEqual(
			offenders,
			[],
			"Only product_feedback/task_writer.py may create a Task — a proposal must reach a "
			"live board through a call a human made. Offending modules: " + ", ".join(offenders),
		)

	def test_the_writer_really_does_create_tasks(self):
		"""The control for the assertion above.

		``x not in source`` is true of every x, including in a repository where the feature was
		deleted. Without this the test above passes against nothing at all.
		"""
		source = _strip_prose((MODULE / self.WRITER).read_text(encoding="utf-8"))
		self.assertIn('"doctype": "Task"', source)


def _strip_prose(source: str) -> str:
	"""Source with docstrings and ``#`` comments removed."""
	tree = ast.parse(source)
	docstrings = []
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				if isinstance(body[0].value.value, str):
					docstrings.append(ast.get_source_segment(source, body[0].value) or "")
	for doc in docstrings:
		if doc:
			source = source.replace(doc, "", 1)
	return re.sub(r"#.*$", "", source, flags=re.M)


if __name__ == "__main__":
	unittest.main()

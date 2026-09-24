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
NOT_DIALLED_BY_THE_SPA: dict[str, str] = {
	"claude_code_brief": (
		"Desk only (WI-079 slice 4): the 'Claude Code Brief' button on the Enhancement Request "
		"form, product_feedback/doctype/enhancement_request/enhancement_request.js, which calls it "
		"by dotted path through frappe.call. The SPA has no copy affordance to put it behind yet."
	),
}

#: The Desk scripts that dial an endpoint the SPA does not. Each exemption above must be dialled
#: by one of them, or it is an endpoint nothing calls.
DESK_CALLERS = {
	"claude_code_brief": APP / "product_feedback" / "doctype" / "enhancement_request" / "enhancement_request.js",
}


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

	def test_every_exemption_is_dialled_by_its_desk_caller(self):
		"""An endpoint the SPA does not dial is still dialled by something, by its full path."""
		self.assertEqual(set(DESK_CALLERS), set(NOT_DIALLED_BY_THE_SPA))
		for name, script in DESK_CALLERS.items():
			with self.subTest(endpoint=name):
				source = re.sub(r"/\*.*?\*/", "", script.read_text(encoding="utf-8"), flags=re.S)
				source = re.sub(r"//.*$", "", source, flags=re.M)
				self.assertIn(f'"erpnext_enhancements.api.feedback.{name}"', source)


class TestOnlyOneWriterCreatesTasks(unittest.TestCase):
	"""The boundary the whole feature exists to hold.

	A model proposes; a human confirms; **one** module writes. Restating that in prose is
	cheap and undoing it in a hurry is cheaper, so it is asserted against the source.
	"""

	#: The one module allowed to construct a ``Task``.
	WRITER = "task_writer.py"

	def test_every_scanned_root_exists(self):
		"""Control: a root renamed away would make the scan below pass over nothing."""
		for root in SCANNED:
			self.assertTrue(root.exists(), f"{root} is scanned but does not exist")

	def test_no_other_module_constructs_a_task(self):
		offenders = []
		for path in _scanned_files():
			if path.name == self.WRITER:
				continue
			lines = _task_constructions(path.read_text(encoding="utf-8"))
			if lines:
				offenders.append(f"{path.relative_to(APP)}:{','.join(map(str, lines))}")
		self.assertEqual(
			offenders,
			[],
			"Only product_feedback/task_writer.py may create a Task — a proposal must reach a "
			"live board through a call a human made. Offending sites: " + "; ".join(offenders),
		)

	def test_the_writer_really_does_create_tasks(self):
		"""The control for the assertion above.

		``x not in source`` is true of every x, including in a repository where the feature was
		deleted. Without this the test above passes against nothing at all. The writer builds a
		group Task and a leaf Task, so the detector must find both.
		"""
		source = (MODULE / self.WRITER).read_text(encoding="utf-8")
		self.assertGreaterEqual(len(_task_constructions(source)), 2)

	def test_the_detector_catches_every_way_to_construct_a_task(self):
		# The regex this replaced matched only the dict form, so `frappe.new_doc("Task")` — which
		# hr_enhancements/safety.py really uses — walked straight past it (ADR 0016).
		for snippet in (
			'frappe.new_doc("Task")',
			"frappe.new_doc('Task')",
			'frappe.new_doc(doctype="Task")',
			'new_doc("Task")',
			'frappe.get_doc({"doctype": "Task"})',
			'frappe.get_doc(doctype="Task", subject="x")',
			'frappe.get_doc(dict(doctype="Task"))',
			'frappe.get_doc(frappe._dict(doctype="Task"))',
		):
			with self.subTest(snippet=snippet):
				self.assertTrue(_task_constructions(snippet), snippet)

	def test_the_detector_ignores_reads_and_prose(self):
		for snippet in (
			'frappe.get_doc("Task", name)',
			'frappe.new_doc("Task Type")',
			'frappe.get_all("Task")',
			'# frappe.new_doc("Task")',
			'def f():\n\t"""frappe.new_doc("Task")"""\n\treturn 1',
		):
			with self.subTest(snippet=snippet):
				self.assertEqual(_task_constructions(snippet), [], snippet)

	def test_every_task_the_writer_builds_carries_its_request(self):
		"""Groups included — a group cannot be traced back through its children (ADR 0016 §1)."""
		tree = ast.parse((MODULE / self.WRITER).read_text(encoding="utf-8"))
		task_dicts = [node for node in ast.walk(tree) if _is_task_dict(node)]
		self.assertGreaterEqual(len(task_dicts), 2, "expected the group dict and the leaf dict")
		for node in task_dicts:
			keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
			self.assertIn(
				"custom_enhancement_request",
				keys,
				f"the Task built at task_writer.py:{node.lineno} does not stamp its request",
			)


class TestOnlyTheWriterShipsTasks(unittest.TestCase):
	"""``mark_shipped`` is the second Task writer and lives in ``task_writer`` too (ADR 0016 §5).

	``release_sync`` finds the Tasks a release shipped and hands each one over; it never moves
	one itself. Asserted as: outside ``task_writer`` nothing in the scanned code names the
	``Pending Review`` status as a value, and nothing calls ``set_value`` on a Task.
	"""

	WRITER = "task_writer.py"

	def test_the_writer_really_names_the_shipped_status(self):
		"""Control: the scan below must be able to see the constant it forbids elsewhere."""
		self.assertIn("Pending Review", _code_strings((MODULE / self.WRITER).read_text(encoding="utf-8")))

	def test_no_other_module_moves_a_task_to_pending_review(self):
		offenders = []
		for path in _scanned_files():
			if path.name == self.WRITER:
				continue
			source = path.read_text(encoding="utf-8")
			if "Pending Review" in _code_strings(source) or _task_set_values(source):
				offenders.append(str(path.relative_to(APP)))
		self.assertEqual(offenders, [], "Only task_writer.mark_shipped moves a feedback Task's status")

	def test_release_sync_hands_tasks_to_the_writer(self):
		source = (MODULE / "release_sync.py").read_text(encoding="utf-8")
		calls = [
			node
			for node in ast.walk(ast.parse(source))
			if isinstance(node, ast.Call) and _callee_name(node) == "mark_shipped"
		]
		self.assertTrue(calls, "release_sync no longer calls task_writer.mark_shipped")


class WriterResultShape(unittest.TestCase):
	"""Every dict ``create_tasks_for`` returns carries every key the endpoint reads off it.

	``create_tasks`` in ``api/feedback.py`` does ``result["complete"]`` with no default, which
	is right — a missing key there is a bug, not a case. The early return for a proposal with
	nothing ticked sent three keys where the full return sent five, and the first zero-task
	breakdown (ER-2026-458194) turned that into a 500 on the confirm button. Asserted with AST
	so the next early return cannot repeat it, plus the guard that now makes the early return
	unreachable from the endpoint: a confirm with nothing ticked is refused *before* the
	writer runs, so ``Tasks Created`` keeps meaning there is work on a board.
	"""

	WRITER = MODULE / "task_writer.py"

	def _keys_read_by_endpoint(self):
		endpoint = next(
			node
			for node in ast.walk(_tree())
			if isinstance(node, ast.FunctionDef) and node.name == "create_tasks"
		)
		keys = set()
		for node in ast.walk(endpoint):
			if (
				isinstance(node, ast.Subscript)
				and isinstance(node.value, ast.Name)
				and node.value.id == "result"
				and isinstance(node.slice, ast.Constant)
			):
				keys.add(node.slice.value)
		return keys

	def _returned_dicts(self):
		tree = ast.parse(self.WRITER.read_text(encoding="utf-8"))
		writer = next(
			node
			for node in ast.walk(tree)
			if isinstance(node, ast.FunctionDef) and node.name == "create_tasks_for"
		)
		return [
			{key.value for key in node.value.keys if isinstance(key, ast.Constant)}
			for node in ast.walk(writer)
			if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
		]

	def test_the_endpoint_really_reads_keys_off_the_result(self):
		"""Control: an empty ``needed`` set would make the assertion below pass vacuously."""
		self.assertTrue({"created", "complete"} <= self._keys_read_by_endpoint())

	def test_every_return_carries_every_key_the_endpoint_reads(self):
		needed = self._keys_read_by_endpoint()
		returns = self._returned_dicts()
		self.assertGreaterEqual(len(returns), 2, "expected the early return and the full one")
		for keys in returns:
			self.assertTrue(
				needed <= keys,
				f"a return of create_tasks_for lacks {sorted(needed - keys)}; "
				"the endpoint subscripts it without a default",
			)

	def test_a_confirm_with_nothing_ticked_is_refused_before_the_writer_runs(self):
		source = _strip_prose(API.read_text(encoding="utf-8"))
		body = source[source.index("def create_tasks(") :]
		self.assertLess(
			body.index("pending_rows("),
			body.index("create_tasks_for("),
			"create_tasks must refuse an empty proposal before it calls the writer",
		)


#: Where the one-writer rule is enforced. Add the Design Review module and the capture endpoints
#: here as they land (WI-079 slices 2 and 5). Task creation elsewhere in the app for human-driven
#: reasons, such as corrective actions in hr_enhancements/safety.py, is outside this rule.
SCANNED = (MODULE, API)


def _scanned_files():
	files = []
	for root in SCANNED:
		files.extend(sorted(root.rglob("*.py")) if root.is_dir() else [root])
	return files


def _is_task_const(node) -> bool:
	return isinstance(node, ast.Constant) and node.value == "Task"


def _is_task_dict(node) -> bool:
	"""``{"doctype": "Task", ...}``."""
	return isinstance(node, ast.Dict) and any(
		isinstance(key, ast.Constant) and key.value == "doctype" and _is_task_const(value)
		for key, value in zip(node.keys, node.values)
	)


def _callee_name(call) -> str:
	func = call.func
	if isinstance(func, ast.Attribute):
		return func.attr
	if isinstance(func, ast.Name):
		return func.id
	return ""


def _task_constructions(source: str) -> list[int]:
	"""Line numbers where ``source`` builds a new ``Task``, in any of the three Frappe forms.

	``frappe.get_doc(dict)`` (and ``dict(doctype="Task")`` or ``frappe._dict(...)``), ``frappe.get_doc(**kwargs)`` with no
	positional argument, and ``frappe.new_doc("Task")``. A positional ``get_doc("Task", name)`` is
	a load and is ignored, as are comments and docstrings, which the AST does not contain as code.
	"""
	lines = set()
	for node in ast.walk(ast.parse(source)):
		if _is_task_dict(node):
			lines.add(node.lineno)
		elif isinstance(node, ast.Call):
			name = _callee_name(node)
			doctype_kw = any(kw.arg == "doctype" and _is_task_const(kw.value) for kw in node.keywords)
			if name == "new_doc" and ((node.args and _is_task_const(node.args[0])) or doctype_kw):
				lines.add(node.lineno)
			elif name in ("get_doc", "dict", "_dict") and not node.args and doctype_kw:
				lines.add(node.lineno)
	return sorted(lines)


def _code_strings(source: str) -> set[str]:
	"""Every string constant in ``source`` that is code, not a docstring."""
	tree = ast.parse(source)
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				docstrings.add(id(body[0].value))
	return {
		node.value
		for node in ast.walk(tree)
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
	}


def _task_set_values(source: str) -> list[int]:
	"""Lines calling ``set_value("Task", ...)`` (``frappe.db.set_value`` and ``frappe.set_value``)."""
	return [
		node.lineno
		for node in ast.walk(ast.parse(source))
		if isinstance(node, ast.Call)
		and _callee_name(node) == "set_value"
		and node.args
		and _is_task_const(node.args[0])
	]


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

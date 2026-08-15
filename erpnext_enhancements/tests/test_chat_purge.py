# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The purge. Bench-free, AST — the deletions themselves need a database.

`chat/governance/purge.py` is the only code in this system that deliberately destroys a
message. Almost all of it is ordering, and almost all of this suite asserts that ordering,
because every one of these was got wrong in a design that was reviewed and rewritten:

1. **The audit row before anything is destroyed, and a failed write refuses the purge.**
   `outbox.refuse_hard_delete`'s own docstring names the requirement — the escape hatch exists
   for *"the Phase 6 retention/erasure path, which has to write its audit row first"* — and
   `record_governance_event` swallows its failures and returns `None`.
2. **The message, then its sidecars.** The reverse leaves a live message stripped of the only
   copies of its superseded bodies when the delete fails, which it is allowed to do.
3. **`delete_permanently=True`.** Without it the whole document, body included, is copied into
   `tabDeleted Document` — a purge that reports success and retains everything.
4. **The retirement mark last**, and only over the contiguous purged prefix.
"""

import ast
import pathlib
import re
import unittest

_CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
PURGE = _CHAT / "governance" / "purge.py"
HOOKS = _CHAT.parent / "hooks.py"

_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def _func(name):
	text = PURGE.read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(text)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found")


def _body(name):
	"""A function's source without its docstring — every docstring here names the thing its
	assertion looks for."""
	text = PURGE.read_text(encoding="utf-8")
	node = _func(name)
	src = ast.get_source_segment(text, node) or ""
	body = node.body
	if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
		doc = ast.get_source_segment(text, body[0].value)
		if doc:
			src = src.replace(doc, "", 1)
	return _LINE.sub("", src)


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
	return out


def _delete_doc_calls():
	text = PURGE.read_text(encoding="utf-8")
	return [
		node
		for node in ast.walk(ast.parse(text))
		if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "delete_doc"
	]


class TheScanActuallyScansTest(unittest.TestCase):
	def test_it_finds_the_deletes(self):
		self.assertGreaterEqual(len(_delete_doc_calls()), 2)


class DeletePermanentlyTest(unittest.TestCase):
	"""The single likeliest way to ship a retention feature that retains everything."""

	def test_every_delete_is_permanent(self):
		for call in _delete_doc_calls():
			kwargs = {kw.arg: kw.value for kw in call.keywords}
			self.assertIn(
				"delete_permanently",
				kwargs,
				"a delete_doc without delete_permanently=True copies the whole document — body "
				"included — into tabDeleted Document. The purge would report success and move "
				"every message to another table.",
			)
			self.assertIs(kwargs["delete_permanently"].value, True)

	def test_no_delete_passes_ignore_links(self):
		"""It is not a parameter — the signature has `ignore_doctypes`. Passing it is a
		TypeError, not a no-op, and the crash would be inside the destructive loop."""
		for call in _delete_doc_calls():
			self.assertNotIn("ignore_links", {kw.arg for kw in call.keywords})

	def test_the_message_delete_carries_the_hard_delete_flag(self):
		"""`update_flags` runs before `on_trash`, so this satisfies `outbox.refuse_hard_delete`
		rather than skipping it. NOT `ignore_on_trash`, which would skip the hook for every
		doctype in the call and for whatever a future maintainer writes into it."""
		src = _body("_destroy")
		self.assertIn("chat_allow_hard_delete", src)
		self.assertNotIn("ignore_on_trash", src)


class OrderingTest(unittest.TestCase):
	def test_the_audit_row_precedes_any_deletion(self):
		src = _body("_purge_room")
		self.assertLess(src.index("record_governance_event"), src.index("_destroy("))

	def test_a_failed_audit_write_refuses_the_purge(self):
		"""`record_governance_event` swallows its failures and returns None, so a caller that
		ignores the return has assumed a record that does not exist — and bodies destroyed
		with nothing saying so is the one outcome this phase exists to prevent."""
		fn = _func("_purge_room")
		guards = [
			n
			for n in ast.walk(fn)
			if isinstance(n, ast.If)
			and isinstance(n.test, ast.UnaryOp)
			and isinstance(n.test.op, ast.Not)
			and getattr(n.test.operand, "id", "") == "recorded"
		]
		self.assertEqual(len(guards), 1, "no `if not recorded:` guard")
		self.assertTrue([n for n in ast.walk(guards[0]) if isinstance(n, ast.Raise)])

	def test_the_message_is_destroyed_before_its_sidecars(self):
		"""The order a review of the earlier design corrected. `delete_doc` may raise, so this
		is allowed to fail — and failing with the sidecars already gone leaves a LIVE message
		stripped of the only copies of its superseded bodies, which nothing can restore. An
		orphaned revision is findable. Fail toward the repairable state."""
		src = _body("_destroy")
		self.assertLess(src.index("delete_doc"), src.index("_destroy_sidecars"))

	def test_retirement_happens_after_the_deletions(self):
		src = _body("_purge_room")
		self.assertLess(src.index("_destroy("), src.index("_retire("))

	def test_the_mark_stops_below_the_lowest_survivor(self):
		"""`set_retirement_mark` refuses unless EVERY message at or below it is gone, so the
		mark cannot be the batch's high seq: a message held by an open relay job or a live
		thread reply is still there."""
		src = _body("_retire")
		self.assertIn("order_by=", src)
		self.assertIn("- 1", src)


class GateTest(unittest.TestCase):
	def test_dry_run_defaults_to_on(self):
		"""The difference between the two is irreversible, and the shape of the mistake is
		somebody running the obvious incantation to see what it would do."""
		fn = _func("run_purge")
		defaults = dict(zip([a.arg for a in fn.args.args], fn.args.defaults, strict=False))
		self.assertEqual(defaults["dry_run"].value, 1)

	def test_a_dry_run_destroys_nothing(self):
		src = _body("run_purge")
		self.assertLess(src.index('summary["dry_run"]'), src.index("_purge_room("))

	def test_zero_retention_days_refuses(self):
		"""0 means keep forever — decision D-6, and the shipped state."""
		src = _body("_gate")
		self.assertIn("message_retention_days", src)

	def test_the_gate_consults_the_disposition_table(self):
		"""`can_enable` reads the survives-a-purge table rather than a flag, so a future
		finding turns the purge off by being recorded rather than by somebody remembering."""
		self.assertIn("can_enable", _calls(_func("_gate")))

	def test_it_never_raises_out_of_the_entry_point(self):
		fn = _func("run_purge")
		handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
		self.assertTrue(handlers)
		for handler in handlers:
			self.assertFalse([n for n in ast.walk(handler) if isinstance(n, ast.Raise)])


class SurfaceTest(unittest.TestCase):
	def test_there_is_no_http_endpoint(self):
		for node in ast.walk(ast.parse(PURGE.read_text(encoding="utf-8"))):
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					target = dec.func if isinstance(dec, ast.Call) else dec
					self.assertNotEqual(getattr(target, "attr", ""), "whitelist", node.name)

	def test_it_is_not_scheduled(self):
		"""A job that destroys conversation on a timer is not something to add and then
		remember to think about."""
		self.assertNotIn("governance.purge", HOOKS.read_text(encoding="utf-8"))

	def test_eligibility_is_not_restated_here(self):
		"""One place decides. The planner and the purge ask the same question, so their
		answers cannot disagree — and a second copy of the hold rules is how a purge starts
		destroying something the report said was held."""
		src = _body("_eligible")
		self.assertIn("retention.plan", src)
		self.assertNotIn("HOLD_", src)

	def test_attachment_bytes_go_with_the_attachment(self):
		"""`Chat Attachment.file` is a File docname, so deleting the row alone leaves bytes on
		disk reachable by nothing — unreadable AND undeleted, the worst of both."""
		src = _body("_destroy_sidecars")
		self.assertIn("FILE_DOCTYPE", src)


if __name__ == "__main__":
	unittest.main()

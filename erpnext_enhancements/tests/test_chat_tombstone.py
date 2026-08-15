# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Seeing through a delete. Bench-free, AST — the rows themselves need a database.

`tombstone_expanded` is the third event `Chat Audit Log` has declared since v1.285.0 with
nothing able to produce it. This is the producer, and the three properties that make it safe:

1. **The audit row is written before the body is returned, and a failed write refuses the
   read.** `record_governance_event` swallows its failures and returns `None`, so a caller
   that ignores the return has assumed a record that does not exist. `request_export` made
   exactly that mistake in the first version of v1.289.5.
2. **Only a deleted message.** A live one goes through the oversight viewer, which records it
   there. Accepting either would make `tombstone_expanded` mean two things and stop it being
   countable.
3. **One grader.** The same `reason_quality` the viewer, the export and the compliance report
   use — two thresholds would admit an expansion at the door that the report then marks
   non-compliant.

All assertions walk the AST. Six text-matching assertions in this series flagged prose rather
than code, every one the sentence explaining why a thing is absent.
"""

import ast
import pathlib
import unittest

MODULE = (
	pathlib.Path(__file__).resolve().parents[1] / "chat" / "governance" / "tombstone.py"
)


def _tree():
	return ast.parse(MODULE.read_text(encoding="utf-8"))


def _func(name):
	for node in ast.walk(_tree()):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found")


def _src(name):
	return ast.get_source_segment(MODULE.read_text(encoding="utf-8"), _func(name))


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
	return out


class AuditBeforeContentTest(unittest.TestCase):
	"""The property the whole module exists for."""

	def test_the_audit_row_is_written_before_the_body_is_returned(self):
		fn = _func("expand")
		calls = _calls(fn)
		self.assertIn("record_governance_event", calls)
		returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
		self.assertTrue(returns)
		src = _src("expand")
		self.assertLess(
			src.index("record_governance_event"),
			src.rindex("return {"),
			"the body is returned before the expansion is recorded",
		)

	def test_a_failed_audit_write_refuses_the_read(self):
		"""`record_governance_event` returns None on failure and raises nothing.

		The first version of `request_export` discarded that return and carried on. This is
		the same failure with a worse consequence: an unrecorded look through a tombstone.
		"""
		fn = _func("expand")
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

	def test_it_uses_its_own_event_type(self):
		self.assertIn('event_type="tombstone_expanded"', _src("expand"))

	def test_the_audit_detail_carries_no_message_text(self):
		"""An expansion's detail is the most tempting place to put the very body it revealed."""
		src = _src("expand")
		detail = src[src.index("detail=json.dumps(") : src.index("affected_count=")]
		for forbidden in ("text", "body", "text_before", "text_after"):
			self.assertNotIn(f'"{forbidden}"', detail)


class ScopeTest(unittest.TestCase):
	def test_only_a_deleted_message_can_be_expanded(self):
		"""Otherwise `tombstone_expanded` means two things and stops being countable."""
		src = _src("expand")
		self.assertIn('not row.get("is_deleted")', src)

	def test_missing_and_not_deleted_get_the_same_refusal(self):
		"""Distinguishing them answers 'does this message exist' to somebody who has not paid
		for an answer."""
		fn = _func("expand")
		checks = [
			n
			for n in ast.walk(fn)
			if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.Or)
		]
		self.assertTrue(checks, "missing and not-deleted are refused separately")

	def test_the_role_is_checked_before_the_reason(self):
		calls = _calls(_func("expand"))
		self.assertLess(calls.index("_require_auditor"), calls.index("_require_reason"))

	def test_both_gates_precede_any_read(self):
		calls = _calls(_func("expand"))
		self.assertLess(calls.index("_require_reason"), calls.index("_message"))


class ReasonTest(unittest.TestCase):
	def test_it_uses_the_shared_grader(self):
		"""One threshold across the viewer, the export, this, and the compliance report."""
		src = _src("_require_reason")
		self.assertIn("access_report.reason_quality", src)
		self.assertIn("REASON_OK", src)
		self.assertNotIn("len(cleaned) <", src)

	def test_a_category_is_required(self):
		src = _src("_require_reason")
		self.assertIn("normalise_reason_category", src)

	def test_every_gate_raises_rather_than_returning_empty(self):
		for name in ("_require_auditor", "_require_reason"):
			raises = [n for n in ast.walk(_func(name)) if isinstance(n, ast.Raise)]
			self.assertTrue(raises, f"{name} does not refuse")


class SurfaceTest(unittest.TestCase):
	def test_the_endpoint_is_post_only(self):
		"""The reason travels in the body; a reason in a query string lands in the access
		log, the browser history and the next Referer header."""
		for dec in _func("expand").decorator_list:
			if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "whitelist":
				methods = [k for k in dec.keywords if k.arg == "methods"]
				self.assertTrue(methods)
				self.assertEqual([e.value for e in methods[0].value.elts], ["POST"])

	def test_it_is_rate_limited(self):
		limited = any(
			isinstance(d, ast.Call) and getattr(d.func, "id", "") == "rate_limit"
			for d in _func("expand").decorator_list
		)
		self.assertTrue(limited)

	def test_no_guest_reaches_it(self):
		self.assertNotIn("allow_guest", MODULE.read_text(encoding="utf-8"))

	def test_the_returned_fields_are_an_allowlist(self):
		"""A column added next year is not automatically something an auditor sees through a
		tombstone. The opposite fails silently."""
		src = MODULE.read_text(encoding="utf-8")
		self.assertIn("MESSAGE_FIELDS = (", src)
		self.assertIn("REVISION_FIELDS = (", src)
		self.assertIn("for key in MESSAGE_FIELDS", src)

	def test_text_plain_is_not_returned(self):
		"""The search-index copy of the body. Returning both invites a reader to treat a
		mismatch as significant."""
		from_module = MODULE.read_text(encoding="utf-8")
		fields_block = from_module[from_module.index("MESSAGE_FIELDS = (") : from_module.index("REVISION_FIELDS")]
		self.assertNotIn("text_plain", fields_block)


class TrailTest(unittest.TestCase):
	def test_the_whole_revision_trail_is_returned_oldest_first(self):
		"""A message deleted after three edits has four bodies in its past. Returning only
		the last answers 'what did it say' with the least interesting of them."""
		src = _src("_revisions")
		self.assertIn("order by `revision_no` asc", src)
		self.assertNotIn("limit", src.lower())

	def test_the_revision_query_binds_its_parameter(self):
		src = _src("_revisions")
		self.assertIn("%(message)s", src)

	def test_the_message_is_read_by_primary_key(self):
		"""Not by a filter dict — the export runner's attachment bug was exactly that."""
		src = _src("_message")
		self.assertIn("frappe.db.get_value(", src)
		self.assertNotIn('{"', src.split("get_value(")[1][:120])


class EditHistoryTest(unittest.TestCase):
	"""`edit_history` is `expand`'s other half, and the pair must leave no gap and no overlap.

	`expand` refuses anything not deleted; this refuses anything that is. A single endpoint
	serving both would be a second door onto deleted bodies that skipped `expand`'s refusal
	and its `tombstone_expanded` event — the shape v1.301.0 closed elsewhere. Two endpoints
	that each refuse the other's domain have no such door, and that is what is asserted here
	rather than assumed.
	"""

	def test_it_refuses_a_deleted_message(self):
		"""The mirror of `expand`'s refusal. Without it the pair overlaps and the two event
		types stop being countable against each other."""
		self.assertIn('row.get("is_deleted")', _src("edit_history"))

	def test_the_two_refusals_are_complementary(self):
		"""`expand` wants deleted; this wants live. If both ever wanted the same thing, one of
		them is a door nobody is watching."""
		self.assertIn('not row.get("is_deleted")', _src("expand"))
		self.assertNotIn('not row.get("is_deleted")', _src("edit_history"))

	def test_missing_and_deleted_get_the_same_refusal(self):
		fn = _func("edit_history")
		checks = [
			n
			for n in ast.walk(fn)
			if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.Or)
		]
		self.assertTrue(checks, "missing and deleted are refused separately")

	def test_it_uses_its_own_event_type(self):
		"""One label for both acts would let the rarer hide inside the commoner."""
		self.assertIn('event_type="revision_history_read"', _src("edit_history"))
		self.assertNotIn('event_type="tombstone_expanded"', _src("edit_history"))

	def test_the_role_is_checked_before_the_reason_and_both_before_any_read(self):
		calls = _calls(_func("edit_history"))
		self.assertLess(calls.index("_require_auditor"), calls.index("_require_reason"))
		self.assertLess(calls.index("_require_reason"), calls.index("_message"))

	def test_a_failed_audit_write_refuses_the_read(self):
		"""Fail-closed, like its sibling: the body is not returned if the record is not written."""
		src = _src("edit_history")
		self.assertIn("if not recorded", src)
		self.assertLess(src.index("if not recorded"), src.index("return {"))

	def test_the_audit_detail_carries_no_message_text(self):
		"""The most tempting place to put the very body the read revealed."""
		src = _src("edit_history")
		detail = src[src.index("detail=json.dumps(") : src.index("affected_count=")]
		for forbidden in ("text", "body", "text_before", "text_after"):
			self.assertNotIn(f'"{forbidden}"', detail)

	def test_it_reuses_the_one_revision_reader(self):
		"""A second query would be a second place to get the ordering or the field list wrong,
		and a second entry in a waiver whose value is being short."""
		self.assertIn("_revisions(", _src("edit_history"))
		self.assertNotIn("tabChat Message Revision", _src("edit_history"))

	def test_the_endpoint_is_post_only_and_rate_limited(self):
		fn = _func("edit_history")
		decorators = [ast.dump(d) for d in fn.decorator_list]
		self.assertTrue(any("rate_limit" in d for d in decorators))
		self.assertTrue(any("'POST'" in d or '"POST"' in d for d in decorators))

	def test_the_returned_fields_are_the_same_allowlist_as_expand(self):
		"""Two shapes for one body of evidence is how a reader concludes they differ."""
		src = _src("edit_history")
		self.assertIn("MESSAGE_FIELDS", src)
		self.assertIn("REVISION_FIELDS", src)
		self.assertNotIn("text_plain", src)


if __name__ == "__main__":
	unittest.main()

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The shape of the drift census. Bench-free, AST — the rows themselves need a database.

:mod:`test_chat_drift_rules` executes the judgement. This asserts the promises
:mod:`chat.governance.drift` makes that no unit test can see, and that a later change could
quietly break:

1. **It makes no Google call of any kind.** Not a listing, not a `get`, not a write. Every
   class in this change is answerable from ERPNext's own tables, which is what makes it cheap
   enough to run nightly and impossible to get wrong through a partial listing.
2. **It repairs nothing.** No relay job, no ingest replay, no watermark write. Three
   independent reviews of a repairing design each killed the one class that looked safe; the
   worst failure was a repairer that inserts a *second* live row for one Google message which
   no shipped path can merge or relay away.
3. **It never reads message text.** Not into a row, not into an alert, not into a log line.
4. **It never raises**, and it refuses on the master switch before the feature switch.
5. **It clears findings it stops observing**, because a census that can only open findings
   produces a board of things that were once true.

Scans docstring-stripped source. Six text-matching assertions in this series have flagged
prose rather than code, and this module's own docstrings name every forbidden thing.
"""

import ast
import pathlib
import unittest

_GOV = pathlib.Path(__file__).resolve().parents[1] / "chat" / "governance"
DRIFT = _GOV / "drift.py"
RULES = _GOV / "drift_rules.py"


def _tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def _func(path, name):
	for node in ast.walk(_tree(path)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in {path.name}")


def _src(path, name):
	return ast.get_source_segment(path.read_text(encoding="utf-8"), _func(path, name))


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
	return out


def _all_calls(path):
	return _calls(_tree(path))


def _imported_modules(path):
	out = set()
	for node in ast.walk(_tree(path)):
		if isinstance(node, ast.ImportFrom) and node.module:
			out.add(node.module)
			for alias in node.names:
				out.add(f"{node.module}.{alias.name}")
		elif isinstance(node, ast.Import):
			for alias in node.names:
				out.add(alias.name)
	return out


def _docstring_ids(tree):
	out = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			body = getattr(node, "body", None)
			if not body:
				continue
			first = body[0]
			if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
				if isinstance(first.value.value, str):
					out.add(id(first.value))
	return out


def _code_strings(path):
	tree = _tree(path)
	skip = _docstring_ids(tree)
	return [
		n.value
		for n in ast.walk(tree)
		if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
	]


class TheScanActuallyScansTest(unittest.TestCase):
	"""An empty walk passes every assertion below it."""

	def test_the_code_string_scan_finds_strings(self):
		self.assertGreater(len(_code_strings(DRIFT)), 40)

	def test_the_call_walk_finds_calls(self):
		self.assertGreater(len(_all_calls(DRIFT)), 30)

	def test_the_scan_excludes_docstrings(self):
		"""This module's docstrings name every forbidden thing at length. If the scan saw
		them, the assertions below would fail on prose — the mistake, in the other direction,
		that this helper exists to prevent."""
		self.assertIn("Chat Drift Report", _code_strings(DRIFT))
		self.assertFalse([s for s in _code_strings(DRIFT) if len(s) > 400])


class NoGoogleCallTest(unittest.TestCase):
	def test_it_imports_no_transport(self):
		for module in _imported_modules(DRIFT):
			self.assertNotIn("gchat", module, f"drift imports {module}")

	def test_it_calls_no_listing_or_write(self):
		forbidden = {
			"list_messages",
			"get_message",
			"get_space",
			"create_message",
			"delete_message",
			"execute",
			"build_message_filter",
		}
		self.assertFalse(forbidden & set(_all_calls(DRIFT)))

	def test_the_only_reconcile_import_is_the_subject_chooser(self):
		"""Delegated so the two cannot disagree: a drift report that is wrong about its own
		blind spots is worse than not having one. Anything more would be a second sweep."""
		self.assertIn("_subject_for_room", _calls(_func(DRIFT, "_subject_for_room")))
		reconcile_calls = {c for c in _all_calls(DRIFT) if "reconcile" in c.lower()}
		self.assertFalse(reconcile_calls - {"_subject_for_room", "reconcile_stale_hours"})


class NoRepairTest(unittest.TestCase):
	def test_it_imports_no_write_path(self):
		for module in _imported_modules(DRIFT):
			self.assertNotIn("outbox", module)
			self.assertNotIn("sync.inbound", module)
			self.assertNotIn("api.compose", module)

	def test_it_calls_nothing_that_repairs(self):
		forbidden = {
			"create_relay_job",
			"enqueue_recovered",
			"_enqueue_recovered",
			"process_inbound_event",
			"insert_message",
			"send_message",
			"reconcile_room",
		}
		self.assertFalse(forbidden & set(_all_calls(DRIFT)))

	def test_it_never_writes_a_reconcile_watermark(self):
		"""`last_reconcile_at` is the sweep's exclusive privilege, and the sweep only advances
		it when its listing was not truncated. A drift scan moving it would silently shrink
		the next sweep's window."""
		for value in _code_strings(DRIFT):
			self.assertNotEqual(value, "last_reconcile_at_written")
		writes = _src(DRIFT, "_write") + _src(DRIFT, "_clear_absent")
		self.assertNotIn("last_reconcile_at", writes)

	def test_the_only_doctype_it_writes_is_its_own(self):
		for name in ("_write", "_clear_absent", "accept"):
			src = _src(DRIFT, name)
			for token in ("Chat Message", "Chat Relay Job", "Chat Inbound Event", "Chat Room"):
				self.assertNotIn(f'"{token}"', src, f"{name} writes {token}")


class NoMessageTextTest(unittest.TestCase):
	def test_no_query_selects_a_body_column(self):
		"""A census of what diverged, not a copy of what was said. A drift table holding
		bodies would be a second transcript outside the membership model."""
		for value in _code_strings(DRIFT):
			self.assertNotIn(value, ("text", "text_plain"))

	def test_the_message_read_selects_only_the_binding(self):
		src = _src(DRIFT, "_message_binding")
		self.assertIn("gchat_message_name", src)
		self.assertNotIn("text", src)

	def test_the_inbound_read_takes_the_error_and_not_the_payload(self):
		"""`last_error` is an exception string; `payload` is the message."""
		src = _src(DRIFT, "_abandoned_inbound")
		self.assertIn("last_error", src)
		self.assertNotIn('"payload"', src)


class GateTest(unittest.TestCase):
	def test_the_master_switch_is_checked_before_the_feature_switch(self):
		"""On a site where chat was never turned on, every class is vacuously empty and the
		scan would report a clean estate — a true answer that reads as reassurance about a
		mirror that does not exist."""
		src = _src(DRIFT, "_gate")
		self.assertLess(src.index('"enabled"'), src.index('"drift_detection_enabled"'))

	def test_the_gate_runs_before_any_collection(self):
		src = _src(DRIFT, "run_drift_scan")
		self.assertLess(src.index("_gate()"), src.index("_collect("))

	def test_the_scan_never_raises(self):
		fn = _func(DRIFT, "run_drift_scan")
		handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
		self.assertTrue(handlers)
		for handler in handlers:
			self.assertFalse([n for n in ast.walk(handler) if isinstance(n, ast.Raise)])

	def test_there_is_no_http_endpoint(self):
		for node in ast.walk(_tree(DRIFT)):
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					target = dec.func if isinstance(dec, ast.Call) else dec
					self.assertNotEqual(getattr(target, "attr", ""), "whitelist", node.name)


class HaltTest(unittest.TestCase):
	def test_a_halted_run_writes_nothing(self):
		"""The first 200 findings of a suspect ten thousand is a table that looks like a
		small, credible problem — worse than an empty one plus an alarm."""
		src = _src(DRIFT, "run_drift_scan")
		halt = src.index("VERDICT_HALT")
		self.assertLess(halt, src.index("_write("), "the halt branch does not precede the write")
		branch = src[halt : src.index("_write(")]
		self.assertIn("return summary", branch)

	def test_the_cap_is_evaluated_on_findings(self):
		self.assertIn("run_verdict(len(findings)", _src(DRIFT, "run_drift_scan"))

	def test_the_halt_alert_is_critical(self):
		self.assertIn("SEVERITY_CRITICAL", _src(DRIFT, "_halt_alert"))

	def test_the_halt_alert_clears_when_a_run_succeeds(self):
		"""Otherwise the board keeps a critical open from a night that is over."""
		src = _src(DRIFT, "run_drift_scan")
		self.assertIn('clear_alert(subsystem="drift", kind="run_halted")', src)


class ClearingTest(unittest.TestCase):
	def test_the_scan_closes_findings_it_no_longer_observes(self):
		"""A census that can only open findings produces a board of things that were once
		true, and a board nobody trusts is one nobody reads."""
		self.assertIn("_clear_absent", _calls(_func(DRIFT, "run_drift_scan")))

	def test_a_repeat_observation_updates_rather_than_inserts(self):
		src = _src(DRIFT, "_write")
		self.assertIn("_live_finding(", src)
		self.assertIn("set_value", src)

	def test_the_live_lookup_uses_the_shared_state_set(self):
		"""A hand-written state list would drift from LIVE_STATES and make every scan open a
		second row instead of updating the first."""
		self.assertIn("drift_rules.LIVE_STATES", _src(DRIFT, "_live_finding"))

	def test_per_class_alerts_go_through_check_so_they_resolve(self):
		self.assertIn("check", _calls(_func(DRIFT, "_class_alerts")))

	def test_the_count_is_in_the_detail_and_not_the_kind(self):
		"""A key carrying the number that changes deduplicates nothing while every surface
		claims it does."""
		fn = _func(DRIFT, "_class_alerts")
		for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
			for kw in call.keywords:
				if kw.arg == "kind":
					self.assertIsInstance(kw.value, ast.Name)


class PurityTest(unittest.TestCase):
	def test_the_rules_module_imports_nothing_at_all(self):
		"""Not just "nothing from frappe". The bench-free tier is the point, and an import
		added later would quietly undo it."""
		allowed = {"__future__", "__future__.annotations", "typing", "typing.Final"}
		self.assertEqual(_imported_modules(RULES) - allowed, set())


if __name__ == "__main__":
	unittest.main()

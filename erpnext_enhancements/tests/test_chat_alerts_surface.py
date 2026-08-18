# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The shape of the alert path. Bench-free, AST — the rows themselves need a database.

:mod:`test_chat_alert_rules` executes the judgement; this asserts the five structural
properties of the module that performs it, none of which a unit test can see:

1. **The log-file line happens first, before any database work.** ``frappe.logger`` writes
   to a file; ``Error Log`` is a table. The failure class where an alert matters most — the
   database is unhappy — is exactly the class where a database-backed alert is least likely
   to survive, so the floor of the path must be off the floor being reported on.
2. **Nothing raises.** An alerting path that can abort the job reporting a problem converts a
   warning into an outage.
3. **No membership bypass.** The ops-space post goes through ``compose.send_message`` with no
   ``ignore_permissions``, because the alternative is a write-into-any-room primitive living
   in the codebase with a comment saying it is only for alerts.
4. **Email is ``frappe.sendmail``, never a ``Notification Log`` row** (G6-18). Routing an
   operational alert through ``Notification Log`` puts it on the same wire as the
   deliberately suppressed chat-notification email path — and that fails by somebody
   widening the notification path to make alerts work.
5. **No HTTP surface.** Acknowledging is ``bench execute``; an endpoint here would be one
   more thing to rate-limit, scope and audit for the sake of a button.

Every assertion walks the AST or reads comment-stripped source. Six text-matching assertions
in this series flagged prose rather than code, every one of them the sentence explaining why
a thing is absent — and this file's own docstring names ``Notification Log`` twice.
"""

import ast
import pathlib
import unittest

_HERE = pathlib.Path(__file__).resolve().parents[1] / "chat" / "governance"
ALERTS = _HERE / "alerts.py"
RULES = _HERE / "alert_rules.py"
DELIVERY = _HERE / "alert_delivery.py"


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


def _docstring_nodes(tree):
	"""Every string node that is a docstring, so prose can be excluded from a scan.

	This is the guard against the mistake this series made six times: asserting that a
	forbidden name is absent, and matching the paragraph that explains why it is absent.
	"""
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
	"""Every string literal that is *not* a docstring."""
	tree = _tree(path)
	skip = _docstring_nodes(tree)
	return [
		node.value
		for node in ast.walk(tree)
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
	]


class TheScanActuallyScansTest(unittest.TestCase):
	"""An empty walk passes every assertion below it. Asserted, not assumed."""

	def test_the_code_string_scan_finds_strings(self):
		strings = _code_strings(ALERTS)
		self.assertGreater(len(strings), 20)

	def test_the_code_string_scan_excludes_docstrings(self):
		"""The module docstring discusses the forbidden name at length. If the scan saw
		docstrings, the G6-18 assertion would fail on prose — which is the mistake, in the
		other direction, that this helper exists to prevent."""
		strings = _code_strings(ALERTS)
		self.assertFalse([s for s in strings if "operational alert" in s and len(s) > 400])
		self.assertIn("Chat Ops Alert", strings)


class LogFirstTest(unittest.TestCase):
	def test_the_log_line_precedes_any_database_work(self):
		src = _src(ALERTS, "raise_alert")
		self.assertLess(
			src.index("_logline("),
			src.index("_record("),
			"the alert is recorded before it is logged; a database failure would lose it",
		)

	def test_the_log_line_goes_to_a_file_and_not_to_the_error_log_table(self):
		self.assertIn("frappe.logger", _src(ALERTS, "_logline"))
		self.assertNotIn("log_error", _src(ALERTS, "_logline"))

	def test_the_logline_helper_cannot_raise(self):
		"""It is the floor. A floor that can give way is not one."""
		handlers = [n for n in ast.walk(_func(ALERTS, "_logline")) if isinstance(n, ast.ExceptHandler)]
		self.assertTrue(handlers)
		for handler in handlers:
			self.assertFalse([n for n in ast.walk(handler) if isinstance(n, ast.Raise)])

	def test_an_unrecordable_alert_still_reaches_the_error_log(self):
		"""The one case where the desk table is the fallback rather than the extra."""
		src = _src(ALERTS, "raise_alert")
		tail = src[src.index("except Exception") :]
		self.assertIn("_error_log(", tail)


class NeverRaisesTest(unittest.TestCase):
	ENTRY_POINTS = ("raise_alert", "clear_alert")

	def test_the_entry_points_swallow_everything(self):
		for name in self.ENTRY_POINTS:
			fn = _func(ALERTS, name)
			handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
			self.assertTrue(handlers, f"{name} has no exception handler")
			for handler in handlers:
				raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
				self.assertFalse(raises, f"{name} re-raises out of the alert path")

	def test_the_reentrancy_guard_exists_and_is_reset_in_a_finally(self):
		"""Every step can fail, and the obvious handling for a failure is to alert about it.
		That loop ends in a full disk."""
		for name in self.ENTRY_POINTS:
			fn = _func(ALERTS, name)
			tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
			self.assertTrue(tries, f"{name} does not reset the guard in a finally")
			assigned = [
				t.targets[0].id
				for t in ast.walk(tries[0])
				if isinstance(t, ast.Assign) and isinstance(t.targets[0], ast.Name)
			]
			self.assertIn("_ACTIVE", assigned, f"{name} never clears _ACTIVE")

	def test_a_reentered_call_returns_instead_of_recursing(self):
		fn = _func(ALERTS, "raise_alert")
		guards = [n for n in ast.walk(fn) if isinstance(n, ast.If) and getattr(n.test, "id", "") == "_ACTIVE"]
		self.assertEqual(len(guards), 1)
		self.assertTrue([n for n in ast.walk(guards[0]) if isinstance(n, ast.Return)])

	def test_each_transport_is_guarded_separately(self):
		"""One channel failing must not take the others with it, which is the entire reason
		for having more than one."""
		fn = _func(ALERTS, "_notify")
		self.assertGreaterEqual(
			len([n for n in ast.walk(fn) if isinstance(n, ast.Try)]),
			3,
			"the transports share an exception handler",
		)


class OffTheSaveTransactionTest(unittest.TestCase):
	"""``test_chat_guardrails`` caught this and it was right.

	``alerts.py`` importing ``chat.api.compose`` created the static path
	``chat.permissions -> chat.audit -> alerts -> compose -> sync.attachments ->
	gchat.client``, which put two ``doc_events`` handlers one import from the Google
	transport. The fix is not a narrower guardrail: delivering inline was wrong anyway,
	because a caller's rollback would take the post with it — alerting that vanishes when
	things go wrong is alerting that works only when it is not needed.
	"""

	def test_alerts_does_not_import_the_delivery_module(self):
		"""The static edge is absent because the runtime edge is: the worker is named as a
		dotted string and reached only through the queue."""
		for node in ast.walk(_tree(ALERTS)):
			if isinstance(node, ast.ImportFrom):
				self.assertNotIn("alert_delivery", node.module or "")
				self.assertNotIn("api.compose", node.module or "")
			elif isinstance(node, ast.Import):
				for alias in node.names:
					self.assertNotIn("alert_delivery", alias.name)

	def test_the_worker_path_names_a_function_that_exists(self):
		"""A dotted string is not checked by anything. This is the check."""
		src = ALERTS.read_text(encoding="utf-8")
		self.assertIn(
			'_SPACE_WORKER = "erpnext_enhancements.chat.governance.alert_delivery.post_to_ops_space"',
			src,
		)
		_func(DELIVERY, "post_to_ops_space")

	def test_the_post_is_enqueued_after_commit(self):
		"""The alert row is written in the caller's transaction. A post queued before commit,
		with a caller that then rolls back, announces an incident whose record does not
		exist."""
		fn = _func(ALERTS, "_to_ops_space")
		enqueues = [
			n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "enqueue"
		]
		self.assertEqual(len(enqueues), 1)
		kwargs = {kw.arg: kw.value for kw in enqueues[0].keywords}
		self.assertIn("enqueue_after_commit", kwargs)
		self.assertIs(kwargs["enqueue_after_commit"].value, True)

	def test_no_job_kwarg_collides_with_an_enqueue_reserved_name(self):
		"""``method``, ``queue``, ``timeout``, ``event``, ``is_async``, ``job_name``, ``now``
		and ``enqueue_after_commit`` are eaten before the job sees them. This package has been
		bitten once already, by a kwarg called ``event``."""
		reserved = {"method", "event", "is_async", "job_name", "now"}
		fn = _func(ALERTS, "_to_ops_space")
		enqueue = next(
			n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "enqueue"
		)
		passed = {kw.arg for kw in enqueue.keywords} - {"queue", "timeout", "enqueue_after_commit"}
		self.assertFalse(passed & reserved)
		worker_args = {a.arg for a in _func(DELIVERY, "post_to_ops_space").args.args}
		self.assertTrue(passed <= worker_args, f"{passed} is not accepted by the worker")


class NoBypassTest(unittest.TestCase):
	def test_the_ops_space_post_passes_no_ignore_permissions(self):
		fn = _func(DELIVERY, "post_to_ops_space")
		for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
			for kw in call.keywords:
				self.assertNotEqual(
					kw.arg,
					"ignore_permissions",
					"the alert path bypasses membership to post into a room",
				)

	def test_it_posts_through_the_one_write_path(self):
		self.assertIn("send_message", _calls(_func(DELIVERY, "post_to_ops_space")))

	def test_it_restores_the_session_user_in_a_finally(self):
		"""``set_user`` is global. A worker that alerts and then continues as somebody else is
		a worse bug than the one being alerted about."""
		fn = _func(DELIVERY, "post_to_ops_space")
		tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
		self.assertTrue(tries)
		self.assertIn("set_user", _calls(ast.Module(body=tries[0].finalbody, type_ignores=[])))

	def test_the_worker_records_its_failure_instead_of_dying(self):
		"""A background job that dies while reporting an incident adds a second incident."""
		fn = _func(DELIVERY, "post_to_ops_space")
		handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
		self.assertTrue(handlers)
		for handler in handlers:
			self.assertFalse([n for n in ast.walk(handler) if isinstance(n, ast.Raise)])
		self.assertIn("_record_failure", _calls(fn))

	def test_the_channel_is_off_unless_both_halves_are_configured(self):
		"""A room with nobody configured to post into it reports as configured and delivers
		nothing, which is the one failure mode an alerting system may not have."""
		src = _src(ALERTS, "_channels")
		self.assertIn("_post_as()", src)
		self.assertIn("_ops_room()", src)


class EmailTest(unittest.TestCase):
	def test_email_is_sendmail(self):
		self.assertIn("sendmail", _calls(_func(ALERTS, "_to_email")))

	def test_no_notification_log_row_is_ever_created(self):
		"""G6-18. Scans code strings only — this file and that module both discuss
		``Notification Log`` in prose, at length, which is why it is absent from the code."""
		for value in _code_strings(ALERTS):
			self.assertNotIn("Notification Log", value)
			self.assertNotIn("Notification Type", value)

	def test_it_is_not_filed_against_a_document(self):
		"""An alert is not correspondence with anybody and should not land in a timeline."""
		fn = _func(ALERTS, "_to_email")
		sendmail = [
			n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "sendmail"
		]
		self.assertEqual(len(sendmail), 1)
		refs = {
			kw.arg: kw.value
			for kw in sendmail[0].keywords
			if kw.arg in ("reference_doctype", "reference_name")
		}
		self.assertEqual(set(refs), {"reference_doctype", "reference_name"})
		for value in refs.values():
			self.assertIsInstance(value, ast.Constant)
			self.assertIsNone(value.value)

	def test_the_body_is_escaped(self):
		"""It carries a caller-supplied detail string into HTML.

		The escaping moved rather than went away (v1.331.0): the body now goes
		through ``email_style.code()``, and the macro escapes its own argument.
		This asserts the chain of custody — that the detail string reaches a
		component instead of being interpolated raw — while
		``test_email_design.test_text_macros_escape_their_argument`` is what
		proves ``code()`` actually escapes, by rendering it.
		"""
		calls = _calls(_func(ALERTS, "_to_email"))
		self.assertIn("code", calls, "the alert body no longer goes through an escaping component")
		self.assertIn("wrap", calls)


class SurfaceTest(unittest.TestCase):
	def test_there_is_no_http_endpoint(self):
		tree = _tree(ALERTS)
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					target = dec.func if isinstance(dec, ast.Call) else dec
					self.assertNotEqual(
						getattr(target, "attr", ""),
						"whitelist",
						f"{node.name} is whitelisted; alerting has no HTTP surface",
					)

	def test_the_rules_module_imports_nothing_from_frappe(self):
		"""The split is what makes the judgement testable without a bench, and an import
		added later would quietly undo it."""
		for node in ast.walk(_tree(RULES)):
			if isinstance(node, ast.Import):
				for alias in node.names:
					self.assertNotIn("frappe", alias.name)
			elif isinstance(node, ast.ImportFrom):
				self.assertNotIn("frappe", node.module or "")

	def test_recording_is_not_gated_on_the_delivery_switch(self):
		"""``alerts_enabled`` means 'do not page me', not 'stop noticing'. The rows written
		while nobody was watching are the first thing anybody wants afterwards."""
		self.assertNotIn("_enabled()", _src(ALERTS, "_record"))
		self.assertNotIn("_enabled()", _src(ALERTS, "raise_alert"))
		self.assertIn("_enabled()", _src(ALERTS, "_channels"))


class DedupWiringTest(unittest.TestCase):
	def test_the_live_lookup_filters_on_the_shared_state_set(self):
		"""A hand-written state list here would drift from ``LIVE_STATES`` and make new
		occurrences open a second row instead of updating the first."""
		self.assertIn("alert_rules.LIVE_STATES", _src(ALERTS, "_live_alerts"))

	def test_a_repeat_updates_rather_than_inserts(self):
		src = _src(ALERTS, "_bump")
		self.assertIn("set_value", src)
		self.assertNotIn(".insert(", src)

	def test_severity_ratchets_up_and_never_down(self):
		"""A problem that was critical once is not made routine by a later milder measurement
		of the same problem."""
		self.assertIn("SEVERITY_CRITICAL", _src(ALERTS, "_bump"))

	def test_the_rate_limit_counts_incidents_and_not_occurrences(self):
		"""What makes a limit of twenty safe: a check firing every minute updates one row and
		spends one of the budget."""
		self.assertIn("db.count", _src(ALERTS, "_recent_incident_count"))

	def test_the_storm_alert_is_not_itself_rate_limited(self):
		"""A rate-limited notice that the rate limit is engaged is one nobody ever sees, and
		the suppression is the news."""
		src = _src(ALERTS, "_open_storm_alert")
		self.assertNotIn("rate_limit_verdict", src)
		self.assertIn("STORM_KEY", src)


if __name__ == "__main__":
	unittest.main()

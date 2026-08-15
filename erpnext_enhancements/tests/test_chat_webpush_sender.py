# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The push sender's status handling, which its own docstring calls "the module".

The transport is trivial; the decisions are all in what each HTTP status means about the
*subscription*. Getting one wrong is invisible in both directions — retire too eagerly and a
transient outage wipes the roster, retire too reluctantly and the table fills with dead rows —
and nobody notices for weeks either way, because the only symptom is a notification that did
not arrive.

Nothing asserted any of it until v1.299.5. These are bench-free: the sender needs `requests`,
a VAPID keypair and a live push service, so the checks here are structural.

The load-bearing one is :class:`AlertDeliveryTest`. A 429 now raises an ops alert, and an
alert *about push* must never be delivered *by push* — the ops-space channel is a
``Chat Message``, whose banner is the thing that just failed. §4.H already encodes that as
``alert_rules.SELF_DELIVERING``, so the assertion is that this caller picked a subsystem
inside that set rather than that somebody remembered to think about it here.

Run: python -m unittest erpnext_enhancements.tests.test_chat_webpush_sender
"""

import ast
import pathlib
import unittest

_CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
SENDER = _CHAT / "notifications" / "webpush" / "sender.py"
RULES = _CHAT / "governance" / "alert_rules.py"


def _tree():
	return ast.parse(SENDER.read_text(encoding="utf-8"))


def _func(name):
	for node in ast.walk(_tree()):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in sender.py")


def _src(name):
	"""A function's source minus its docstring.

	Every docstring in this module names the statuses and the mechanisms it is reasoning
	about, so a text assertion that includes them matches the prose explaining the decision
	rather than the code making it — and is then satisfied by deleting the explanation.
	"""
	text = SENDER.read_text(encoding="utf-8")
	node = _func(name)
	src = ast.get_source_segment(text, node) or ""
	body = node.body
	if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
		doc = ast.get_source_segment(text, body[0].value)
		if doc:
			src = src.replace(doc, "", 1)
	return src


def _self_delivering():
	"""``alert_rules.SELF_DELIVERING``, read from source rather than imported.

	Importing it would pull in ``frappe``; this tier runs without one.
	"""
	for node in ast.walk(ast.parse(RULES.read_text(encoding="utf-8"))):
		if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "SELF_DELIVERING":
			return set(ast.literal_eval(ast.unparse(node.value).replace("frozenset", "", 1).strip()))
	raise AssertionError("SELF_DELIVERING not found; re-derive this scan")


class StatusTableTest(unittest.TestCase):
	def test_only_404_and_410_retire_a_subscription(self):
		"""The distinction the module exists for. 403 is almost always a VAPID mismatch, and
		retiring on it would delete every subscription on the site the moment somebody
		rotated a key by mistake."""
		for node in ast.walk(_tree()):
			if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TERMINAL_STATUSES":
				value = ast.unparse(node.value)
				self.assertIn("404", value)
				self.assertIn("410", value)
				for wrong in ("403", "429", "500", "503", "413"):
					self.assertNotIn(wrong, value, f"{wrong} must not retire a subscription")
				return
		self.fail("TERMINAL_STATUSES not found")

	def test_every_status_in_the_table_is_actually_handled(self):
		"""The docstring table is the specification. A code documented and unhandled falls
		through to the generic branch, which is silently the wrong answer for 413 and 429."""
		src = _src("_post")
		for status in ("413", "429"):
			self.assertIn(status, src, f"_post does not branch on {status}")

	def test_a_429_is_flagged_even_without_a_retry_after_header(self):
		"""The header is optional. Keying the signal off it alone would count a 429 that sent
		no Retry-After as no rate limiting at all — the case most worth hearing about,
		because it is the one where we cannot even say how long to wait."""
		src = _src("_post")
		self.assertIn("rate_limited=True", src)

	def test_the_retry_after_reader_cannot_raise(self):
		"""It runs inside the failure path of a background fan-out."""
		fn = _func("_retry_after")
		self.assertTrue([n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)])


class OneAlertPerFanOutTest(unittest.TestCase):
	def test_send_one_does_not_raise_the_alert_itself(self):
		"""It is called in a loop over every device one person owns. Alerting there would
		open the same incident twenty times for one rate-limited service."""
		self.assertNotIn("_alert_rate_limited", _src("send_one"))

	def test_the_fan_out_alerts_after_the_loop(self):
		"""After, not during: the count is the thing worth reporting and it is not known
		until the loop ends."""
		src = _src("push_to_user")
		self.assertIn("_alert_rate_limited", src)
		self.assertLess(src.index("for row in"), src.index("_alert_rate_limited("))

	def test_the_accumulator_is_a_parameter_not_module_state(self):
		"""This runs in background workers, where module state is shared between unrelated
		jobs and outlives the one that wrote it — so a counter kept there would attribute one
		fan-out's 429s to the next fan-out, or to another site's."""
		self.assertIn("outcomes", [a.arg for a in _func("send_one").args.args])
		self.assertIn("outcomes", [a.arg for a in _func("_accumulate").args.args])


class AlertDeliveryTest(unittest.TestCase):
	"""An alert about push must never be delivered by push."""

	def test_the_subsystem_is_self_delivering(self):
		"""Not "somebody chose email here" — the subsystem has to be one §4.H already knows
		cannot carry its own bad news, so the rule holds even if this call site is rewritten.

		The ops-space channel is a Chat Message. Its banner is the thing that just failed.
		"""
		call = None
		for node in ast.walk(_func("_alert_rate_limited")):
			if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "raise_alert":
				call = node
		self.assertIsNotNone(call, "_alert_rate_limited does not call raise_alert")
		kwargs = {kw.arg: kw.value for kw in call.keywords}
		self.assertIn("subsystem", kwargs)
		subsystem = ast.literal_eval(kwargs["subsystem"])
		self.assertIn(
			subsystem,
			_self_delivering(),
			f"the push sender alerts under subsystem {subsystem!r}, which is NOT in "
			"alert_rules.SELF_DELIVERING — so §4.H would try to deliver a 'push is failing' "
			"alert through the ops space, whose notification is a push",
		)

	def test_the_dedup_scope_is_the_service_not_the_count(self):
		"""A key carrying the number that changes deduplicates nothing while looking like it
		does. The count belongs in detail."""
		call = None
		for node in ast.walk(_func("_alert_rate_limited")):
			if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "raise_alert":
				call = node
		kwargs = {kw.arg: kw.value for kw in call.keywords}
		self.assertIn("scope", kwargs)
		self.assertNotIn("count", ast.unparse(kwargs["scope"]))

	def test_it_returns_early_when_nothing_was_rate_limited(self):
		"""The overwhelmingly common case is zero, and it must cost nothing — no import, no
		query, no row."""
		fn = _func("_alert_rate_limited")
		self.assertTrue(
			any(isinstance(n, ast.Return) for n in ast.walk(fn.body[1] if len(fn.body) > 1 else fn)),
			"no early return for the zero case",
		)


if __name__ == "__main__":
	unittest.main()

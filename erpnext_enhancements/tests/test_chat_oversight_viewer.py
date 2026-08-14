# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The oversight viewer's gates, asserted where a database is not needed. Bench-free, AST.

The viewer is the surface that **collects** a reason, which is why v1.288.6 built
`reason_quality` and deliberately did not enforce it. Enforcement lands here, and these are
the three properties that make this surface safe to switch on:

1. **G6-10 — a bad reason is refused, not accepted and blanked.** Including the placeholder
   that clears the length bar, because the grade comes from the same function the compliance
   report grades by. Two thresholds would mean rows that passed the door and fail the report.
2. **G6-6 — no realtime.** A socket join on a room the auditor is not in is a read this
   module cannot audit: the audit row is written when the transcript is fetched, and a live
   event arriving afterwards was never asked for. Asserted as an absence, because a viewer
   that quietly went live would look identical until somebody read the network tab.
3. **The reason never reaches the subject.** Only the category does.

The reads themselves need a bench and are asserted there.
"""

import ast
import pathlib
import unittest

CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
WWW = pathlib.Path(__file__).resolve().parents[1] / "www"
VIEWER = CHAT / "governance" / "viewer.py"


def _tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def _func(path, name):
	for node in ast.walk(_tree(path)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in {path.name}")


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			if isinstance(f, ast.Name):
				out.append(f.id)
			elif isinstance(f, ast.Attribute):
				out.append(f.attr)
	return out


def _strip_comments(text: str) -> str:
	"""Template text with Jinja comments and JS line comments removed.

	Needed because every assertion in this file is about what the code *does*, and every one
	of them is at risk of matching the prose that explains what it deliberately does not do.
	"""
	import re

	text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
	return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def _whitelisted(path):
	names = []
	for node in ast.walk(_tree(path)):
		if not isinstance(node, ast.FunctionDef):
			continue
		for dec in node.decorator_list:
			target = dec.func if isinstance(dec, ast.Call) else dec
			if isinstance(target, ast.Attribute) and target.attr == "whitelist":
				names.append(node)
	return names


class NoRealtimeTest(unittest.TestCase):
	"""G6-6, asserted as an absence."""

	FORBIDDEN = ("publish_realtime", "doc_subscribe", "subscribe_doc", "socketio")

	def test_the_viewer_module_opens_no_socket(self):
		"""Walks the AST, not the text.

		The text scan this started as flagged the module's own docstring, which has to *name*
		`publish_realtime` in order to explain why it does not call it. A guard that cannot
		tell an explanation from a call is one that gets satisfied by deleting the
		explanation.
		"""
		called = set()
		for node in ast.walk(_tree(VIEWER)):
			if isinstance(node, ast.Call):
				f = node.func
				name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
				called.add(name)
		hits = sorted(called & set(self.FORBIDDEN))
		self.assertEqual(
			hits,
			[],
			f"chat/governance/viewer.py CALLS {hits} — a realtime subscription to a room the "
			"auditor is not in is a read this module cannot audit, because the audit row is "
			"written when the transcript is fetched",
		)

	def test_the_page_opens_no_socket_either(self):
		"""The template is where a socket would actually be opened."""
		src = (WWW / "chat_admin.html").read_text(encoding="utf-8")
		for token in ("socket.io", "frappe.realtime", "doc_subscribe", "setInterval"):
			self.assertNotIn(token, src, f"chat_admin.html uses {token}")

	def test_the_controller_reads_no_chat_content(self):
		"""A transcript pre-loaded 'for convenience' is an unaudited read on page load.

		It would also be the most frequent one, since it happens whether or not the auditor
		goes on to search.
		"""
		src = (WWW / "chat_admin.py").read_text(encoding="utf-8")
		for token in ("Chat Message", "get_messages", "retrieve_for_oversight", "access_rows"):
			self.assertNotIn(token, src, f"chat_admin.py touches {token} during page render")


class ReasonGateTest(unittest.TestCase):
	"""G6-10. Refused, not accepted-and-blanked."""

	def test_the_search_endpoint_requires_a_reason_before_it_reads(self):
		"""Order matters: the refusal must precede the read, not annotate it afterwards."""
		fn = _func(VIEWER, "search")
		calls = _calls(fn)
		self.assertIn("_require_reason", calls)
		self.assertIn("retrieve_for_oversight", calls)
		self.assertLess(
			calls.index("_require_reason"),
			calls.index("retrieve_for_oversight"),
			"the reason is checked AFTER the read — by then the content has been returned",
		)

	def test_the_role_is_checked_before_the_reason(self):
		"""Cheapest refusal first, and it avoids telling a stranger which reasons are valid."""
		calls = _calls(_func(VIEWER, "search"))
		self.assertLess(calls.index("_require_auditor"), calls.index("_require_reason"))

	def test_the_grade_comes_from_the_shared_function(self):
		"""One threshold, not two.

		A door with its own minimum would admit reads the compliance report then grades as
		non-compliant — which reads as tampering rather than as two components disagreeing.
		"""
		src = VIEWER.read_text(encoding="utf-8")
		self.assertIn("access_report.reason_quality", src)
		self.assertIn("access_report.REASON_OK", src)
		self.assertNotIn("len(cleaned) <", src)

	def test_a_category_is_required_too(self):
		"""It is the part the subject is shown, so it cannot be inferred later."""
		src = ast.get_source_segment(
			VIEWER.read_text(encoding="utf-8"), _func(VIEWER, "_require_reason")
		)
		self.assertIn("normalise_reason_category", src)
		self.assertIn("OversightRefused", src)

	def test_every_gate_raises_rather_than_returning_empty(self):
		"""Accept-and-blank is the failure G6-10 names. A refusal must be a refusal."""
		for name in ("_require_auditor", "_require_reason", "_rooms"):
			src = ast.get_source_segment(VIEWER.read_text(encoding="utf-8"), _func(VIEWER, name))
			self.assertIn("raise OversightRefused", src, f"{name} does not refuse")


class SurfacePostureTest(unittest.TestCase):
	"""Every endpoint here is POST-only and rate-limited."""

	def test_every_whitelisted_endpoint_is_post_only(self):
		"""Reads too — they carry the reason in the body.

		A reason in a query string lands in the web server's access log, the browser's
		history and the `Referer` header of whatever the auditor clicks next. It is the most
		sensitive field in the request.
		"""
		for fn in _whitelisted(VIEWER):
			for dec in fn.decorator_list:
				if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "whitelist":
					methods = [k for k in dec.keywords if k.arg == "methods"]
					self.assertTrue(methods, f"{fn.name} declares no methods=")
					values = [e.value for e in methods[0].value.elts]
					self.assertEqual(values, ["POST"], f"{fn.name} is not POST-only")

	def test_no_endpoint_here_is_reachable_by_a_guest(self):
		src = VIEWER.read_text(encoding="utf-8")
		self.assertNotIn("allow_guest", src)

	def test_the_two_privileged_endpoints_are_rate_limited(self):
		"""`my_access_log` answers only for the caller, so it is deliberately not limited."""
		limited = set()
		for fn in _whitelisted(VIEWER):
			for dec in fn.decorator_list:
				if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "rate_limit":
					limited.add(fn.name)
		self.assertIn("search", limited)
		self.assertIn("access_log", limited)


class SubjectViewTest(unittest.TestCase):
	"""D-1: built, and shipped disabled."""

	def test_it_refuses_while_the_setting_is_off(self):
		src = ast.get_source_segment(
			VIEWER.read_text(encoding="utf-8"), _func(VIEWER, "my_access_log")
		)
		self.assertIn("_transparency_enabled", src)
		self.assertIn("raise OversightRefused", src)

	def test_it_always_redacts(self):
		"""Via `subject_rows`, which the caller cannot ask to skip."""
		src = ast.get_source_segment(
			VIEWER.read_text(encoding="utf-8"), _func(VIEWER, "my_access_log")
		)
		self.assertIn("subject_rows", src)
		self.assertNotIn("access_rows", src)

	def test_it_answers_only_for_the_caller(self):
		"""No `subject` parameter — an employee cannot ask about somebody else's rooms."""
		fn = _func(VIEWER, "my_access_log")
		args = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
		self.assertNotIn("subject", args)
		self.assertNotIn("user", args)

	def test_the_setting_ships_off_and_needs_no_backfill(self):
		"""A Single's `default` never reaches the row that already exists — which is fine here.

		The gotcha that makes a new Single field dangerous is a controller that *rejects* the
		unset value. This one is read as a boolean and unset means off, which is exactly what
		D-1 asks for, so there is deliberately no backfill patch.
		"""
		import json

		meta = json.loads(
			(CHAT / "doctype" / "chat_settings" / "chat_settings.json").read_text(encoding="utf-8")
		)
		field = next(
			f for f in meta["fields"] if f["fieldname"] == "transparency_view_enabled"
		)
		self.assertEqual(field["fieldtype"], "Check")
		self.assertEqual(field.get("default"), "0")


class ControllerFilenameTest(unittest.TestCase):
	"""The `www/` rule, restated where it will actually be read."""

	def test_the_controller_exists_and_is_underscored(self):
		"""A hyphenated controller is never imported and `get_context` silently never runs.

		`stripe-return.py` was broken that way in this app from the day it was written, and
		the page still rendered — with every variable undefined.
		"""
		self.assertTrue((WWW / "chat_admin.py").exists())
		self.assertTrue((WWW / "chat_admin.html").exists())
		self.assertFalse((WWW / "chat-admin.py").exists())
		self.assertFalse((WWW / "chat-admin.html").exists())

	def test_the_controller_gates_on_the_configured_role(self):
		src = (WWW / "chat_admin.py").read_text(encoding="utf-8")
		self.assertIn("_has_oversight", src)
		self.assertIn("PermissionError", src)

	def test_the_template_does_not_keep_its_own_copy_of_the_vocabulary(self):
		"""A second list would drift, and the drift shows up as a subject being told a
		category the writer then refuses to store."""
		html = (WWW / "chat_admin.html").read_text(encoding="utf-8")
		self.assertIn("reason_categories", html)
		self.assertNotIn("HR or personnel matter", html)

	def test_the_template_renders_untrusted_text_without_innerhtml(self):
		"""The reason and the transcript are user-authored and this page runs with a live
		administrator session.

		Comments are stripped before scanning. The first version of this failed on the
		template's own ``// textContent, never innerHTML`` note — the third time in this
		change that a text scan flagged the sentence explaining why the thing is absent. A
		guard that cannot tell a comment from code is satisfied by deleting the comment,
		which is the wrong direction.
		"""
		html = _strip_comments((WWW / "chat_admin.html").read_text(encoding="utf-8"))
		self.assertIn("textContent", html)
		self.assertNotIn("innerHTML", html)


if __name__ == "__main__":
	unittest.main()

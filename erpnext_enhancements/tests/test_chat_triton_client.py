"""Bench-free tests for the Triton chat client's two error paths.

**Shape U: ``unittest`` with a ``frappe`` stub installed in ``setUpModule``**, so it needs its
own ``python -m unittest erpnext_enhancements.tests.test_chat_triton_client -v`` step in
``ci.yml`` — appending it to another suite's module list would let two stub sets cross-talk
(``CLAUDE.md``).

--------------------------------------------------------------------------------------
Why these two paths and not the happy one
--------------------------------------------------------------------------------------

The happy path is verified every time somebody says ``@triton``. These two are not, and both
of them failed silently in production for weeks:

* **The error body was thrown away.** ``_post`` raised ``Triton returned HTTP 401`` and
  discarded a body that literally contained ``erpnext_link_required`` — the name of the fault.
  A week went into *why is Triton rejecting our token*, and the answer was that it wasn't:
  Triton authenticated us fine and then had no ERPNext grant to run the person's tool calls
  with. What is under test here is the distinction that got lost — ``code`` and ``request_id``
  are repeated, ``message`` never is, because only the first two are content-free *by their
  type* rather than by inspection.
* **The stale-session recovery did not exist.** ``_session_for``'s docstring described it in
  detail; no code performed it. A person deleting the thread from Triton's web app cached a
  dead id for thirty days and ``@triton`` was dead in that room until it expired. A docstring
  is not a mechanism, and this file is the difference.

Nothing here reaches the network. ``_post`` is replaced at the module seam so the real ``ask``
control flow runs — the retry decision, the cache invalidation, the orphan guard — against
scripted responses.
"""

from __future__ import annotations

import sys
import types
import unittest


def setUpModule() -> None:
	"""Install the ``frappe`` this module needs, and no more of one than that.

	Installed at *execution* time rather than import time, so a bench-only suite's
	``import frappe`` skip guard is never fooled by it.
	"""
	if "frappe" in sys.modules:
		return

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")

	def cint(value=0, default=0):
		try:
			return int(float(value))
		except (TypeError, ValueError):
			return default

	utils.cint = cint

	class _Session:
		user = "Administrator"

	class _Cache:
		def __init__(self):
			self.store: dict[str, object] = {}

		def get_value(self, key, *args, **kwargs):
			return self.store.get(key)

		def set_value(self, key, value, *args, **kwargs):
			self.store[key] = value

		def delete_value(self, key, *args, **kwargs):
			self.store.pop(key, None)

	class _ThrowError(Exception):
		pass

	def throw(message, exc=None):
		raise _ThrowError(message)

	cache = _Cache()
	frappe.session = _Session()
	frappe.cache = lambda: cache
	frappe.set_user = lambda user: setattr(frappe.session, "user", user)
	frappe.utils = utils
	# `triton_link` needs these at import and on its guard path.
	frappe._ = lambda text: text
	frappe.only_for = lambda *roles: None
	frappe.throw = throw
	frappe.ThrowError = _ThrowError
	# The real decorator registers the function and returns it unchanged; only the second half
	# matters here, and a stub that dropped it would make every whitelisted function `None`.
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


class _Response:
	"""Only what ``_error_fields`` touches: ``json()``, and a body that may not be JSON."""

	def __init__(self, payload, *, raises: bool = False):
		self._payload = payload
		self._raises = raises

	def json(self):
		if self._raises:
			raise ValueError("not JSON")
		return self._payload


class ErrorFieldsTest(unittest.TestCase):
	"""``code`` and ``request_id`` out; everything else stays in the response."""

	def test_reads_the_code_and_the_request_id(self):
		from erpnext_enhancements.chat.invoke import triton_client

		code, request_id = triton_client._error_fields(
			_Response({"message": "x", "request_id": "abc-123", "code": "erpnext_link_required"})
		)
		self.assertEqual(code, "erpnext_link_required")
		self.assertEqual(request_id, "abc-123")

	def test_never_returns_the_message(self):
		"""The invariant the whole class of bug lives under.

		``message`` is ``str(exc)`` at the far end — it can quote the prompt, and the prompt is
		the transcript. It is not in the return type and must never be added to it, however
		useful it looks while debugging.
		"""
		from erpnext_enhancements.chat.invoke import triton_client

		secret = "the assembled chat context, verbatim"
		fields = triton_client._error_fields(
			_Response({"message": secret, "code": "boom", "request_id": "r1", "detail": secret})
		)
		for value in fields:
			self.assertNotIn(secret, value)
		self.assertEqual(fields, ("boom", "r1"))

	def test_a_non_string_code_is_dropped_rather_than_coerced(self):
		"""``str()`` on a dict would render the dict — a hole straight through this seam."""
		from erpnext_enhancements.chat.invoke import triton_client

		self.assertEqual(
			triton_client._error_fields(_Response({"code": {"leak": "prompt"}, "request_id": 7})),
			("", ""),
		)

	def test_a_long_code_is_truncated(self):
		from erpnext_enhancements.chat.invoke import triton_client

		code, _ = triton_client._error_fields(_Response({"code": "x" * 500}))
		self.assertEqual(len(code), 64)

	def test_a_body_that_is_not_a_json_object_yields_nothing(self):
		from erpnext_enhancements.chat.invoke import triton_client

		self.assertEqual(triton_client._error_fields(_Response(None, raises=True)), ("", ""))
		self.assertEqual(triton_client._error_fields(_Response(["a", "b"])), ("", ""))
		self.assertEqual(triton_client._error_fields(_Response("plain text")), ("", ""))


class ErrorDetailTest(unittest.TestCase):
	"""The sentence that reaches the Error Log, asserted because its wording is the deliverable."""

	def test_the_401_names_the_fault_and_the_fix(self):
		from erpnext_enhancements.chat.invoke import triton_client

		detail = triton_client._error_detail(401, "/api/v1/assistant/sessions/115/query", "erpnext_link_required", "r-9")
		self.assertIn("401", detail)
		self.assertIn("erpnext_link_required", detail)
		self.assertIn("Link ERPNext", detail)
		# The correction of the wrong question this error sent us to for a week.
		self.assertIn("not a problem with the token we sent", detail)
		self.assertIn("r-9", detail)

	def test_an_unrecognised_code_is_still_reported(self):
		"""A code we have no gloss for is more useful than no code, not less."""
		from erpnext_enhancements.chat.invoke import triton_client

		detail = triton_client._error_detail(500, "/p", "something_new", "")
		self.assertIn("something_new", detail)
		self.assertNotIn("[Triton request", detail)

	def test_no_code_reads_as_it_always_did(self):
		from erpnext_enhancements.chat.invoke import triton_client

		self.assertEqual(triton_client._error_detail(503, "/p", "", ""), "Triton returned HTTP 503 for /p")


class StaleSessionRetryTest(unittest.TestCase):
	"""A cached session id Triton no longer has must cost one retry, not one dead room."""

	def setUp(self):
		import erpnext_enhancements
		from erpnext_enhancements.chat.invoke import triton_client

		self.triton_client = triton_client
		self.calls: list[str] = []

		fake_settings = types.ModuleType("erpnext_enhancements.triton_chat")
		fake_settings.get_settings = lambda: {"enabled": 1, "base_url": "https://t.example", "timeout": 30}
		self._saved = getattr(erpnext_enhancements, "triton_chat", None)
		erpnext_enhancements.triton_chat = fake_settings
		self._package = erpnext_enhancements

		self._saved_post = triton_client._post
		import frappe

		frappe.cache().store.clear()

	def tearDown(self):
		self.triton_client._post = self._saved_post
		if self._saved is None:
			delattr(self._package, "triton_chat")
		else:
			self._package.triton_chat = self._saved

	def _install(self, script):
		def _post(base_url, path, payload, *, timeout):
			self.calls.append(path)
			return script(path)

		self.triton_client._post = _post

	def test_a_stale_cached_id_is_discarded_and_the_turn_still_answers(self):
		triton_client = self.triton_client
		key = triton_client._session_cache_key(user="a@b.c", room="room1")
		import frappe

		frappe.cache().set_value(key, 7)

		def script(path):
			if path.endswith("/7/query"):
				raise triton_client.TritonUnavailable("gone", status=404)
			if path == triton_client.SESSIONS_PATH:
				return {"id": 9}
			return {"content": "the answer", "tokens": 12}

		self._install(script)
		answer = triton_client.ask(user="a@b.c", question="q", context="", room="room1")

		self.assertEqual(answer["text"], "the answer")
		self.assertEqual(
			self.calls,
			[
				"/api/v1/assistant/sessions/7/query",
				triton_client.SESSIONS_PATH,
				"/api/v1/assistant/sessions/9/query",
			],
		)
		# Re-cached on the new id, so the next turn does not repeat the discovery.
		self.assertEqual(frappe.cache().get_value(key), 9)

	def test_a_freshly_created_session_that_404s_is_not_retried(self):
		"""The orphan guard.

		Without it, a Triton that 404s every query would mint a new session on every turn,
		forever, and fill the account's session list while still answering nothing.
		"""
		triton_client = self.triton_client

		def script(path):
			if path == triton_client.SESSIONS_PATH:
				return {"id": 3}
			raise triton_client.TritonUnavailable("gone", status=404)

		self._install(script)
		with self.assertRaises(triton_client.TritonUnavailable):
			triton_client.ask(user="a@b.c", question="q", context="", room="room2")

		self.assertEqual(self.calls.count(triton_client.SESSIONS_PATH), 1)

	def test_a_401_on_a_cached_session_is_not_retried(self):
		"""Only a 404 means "that session is gone". A 401 is the ERPNext link, and retrying it
		would double every failed turn's cost while changing nothing."""
		triton_client = self.triton_client
		import frappe

		frappe.cache().set_value(triton_client._session_cache_key(user="a@b.c", room="room3"), 5)

		def script(path):
			raise triton_client.TritonUnavailable("nope", status=401, code="erpnext_link_required")

		self._install(script)
		with self.assertRaises(triton_client.TritonUnavailable) as caught:
			triton_client.ask(user="a@b.c", question="q", context="", room="room3")

		self.assertEqual(caught.exception.code, "erpnext_link_required")
		self.assertEqual(self.calls, ["/api/v1/assistant/sessions/5/query"])

	def test_the_session_user_is_restored_after_a_failure(self):
		"""A job that left the session pointing at a coworker attributes everything after it to
		them. The ``finally`` is load-bearing on the error path specifically."""
		triton_client = self.triton_client
		import frappe

		frappe.set_user("Administrator")

		def script(path):
			raise triton_client.TritonUnavailable("down", status=503)

		self._install(script)
		with self.assertRaises(triton_client.TritonUnavailable):
			triton_client.ask(user="a@b.c", question="q", context="", room="room4")

		self.assertEqual(frappe.session.user, "Administrator")


class BotCredentialTest(unittest.TestCase):
	"""``triton_link``: the bot cannot click 'Link ERPNext', so it is credentialled by API key.

	Only the parts that do not need the whole ``handler`` import graph. What is asserted is the
	three-state answer, because collapsing it to a boolean is the specific mistake that was in
	this code for about ten minutes and would have printed *re-credential this account* every
	time the gateway hiccuped.
	"""

	def setUp(self):
		import frappe

		from erpnext_enhancements.chat.invoke import triton_link

		self.triton_link = triton_link
		self._saved = triton_link._request
		self._saved_roles = getattr(frappe, "get_roles", None)

	def tearDown(self):
		import frappe

		self.triton_link._request = self._saved
		if self._saved_roles is None:
			if hasattr(frappe, "get_roles"):
				del frappe.get_roles
		else:
			frappe.get_roles = self._saved_roles

	def test_a_stored_credential_reads_as_true(self):
		self.triton_link._request = lambda *a, **k: {"frappe_api_key_configured": True}
		self.assertEqual(self.triton_link.remote_key_configured("bot@x.y"), (True, ""))

	def test_a_missing_credential_reads_as_false(self):
		self.triton_link._request = lambda *a, **k: {"frappe_api_key_configured": False}
		self.assertEqual(self.triton_link.remote_key_configured("bot@x.y"), (False, ""))

	def test_an_unreachable_triton_reads_as_unknown_not_missing(self):
		"""``None``, never ``False``. The two send an admin to different screens."""
		from erpnext_enhancements.chat.invoke import triton_client

		def _boom(*a, **k):
			raise triton_client.TritonUnavailable("Triton returned HTTP 502 for /p", status=502)

		self.triton_link._request = _boom
		stored, error = self.triton_link.remote_key_configured("bot@x.y")
		self.assertIsNone(stored)
		self.assertIn("502", error)
		# The distinction the caller relies on, spelled out: `None` is falsy, so a caller that
		# tests truthiness rather than identity gets the wrong answer silently.
		self.assertIsNot(stored, False)

	def test_an_unexpected_error_reports_only_its_class(self):
		def _boom(*a, **k):
			raise RuntimeError("a secret-shaped string")

		self.triton_link._request = _boom
		stored, error = self.triton_link.remote_key_configured("bot@x.y")
		self.assertIsNone(stored)
		self.assertEqual(error, "RuntimeError")

	def test_the_session_user_is_restored_after_asking_as_the_bot(self):
		import frappe

		frappe.set_user("Administrator")
		self.triton_link._request = lambda *a, **k: {"frappe_api_key_configured": True}
		self.triton_link.remote_key_configured("bot@x.y")
		self.assertEqual(frappe.session.user, "Administrator")

	def test_a_privileged_account_is_named_as_unsafe(self):
		"""The escalation guard.

		The account this shipped pointing at is *also* Triton's shared system key and holds
		System Manager, so credentialling "the bot" would have put a live System Manager API key
		— non-expiring, unrotatable — on the path a chat message can reach. It reads like
		copying a key that already exists to a service that already has one.
		"""
		import frappe

		frappe.get_roles = lambda user: ["Sales User", "System Manager", "Script Manager"]
		self.assertEqual(
			self.triton_link.unsafe_roles("bot@x.y"), ["Script Manager", "System Manager"]
		)

	def test_an_account_with_ordinary_roles_is_clean(self):
		import frappe

		frappe.get_roles = lambda user: ["Sales User", "Projects User"]
		self.assertEqual(self.triton_link.unsafe_roles("bot@x.y"), [])

	def test_linking_a_privileged_account_is_refused_outright(self):
		"""And refused *before* the credential is read, let alone transmitted."""
		import frappe

		triton_link = self.triton_link
		frappe.get_roles = lambda user: ["System Manager"]
		triton_link._bot_user = lambda: "triton@x.y"
		triton_link._erpnext_credential = lambda user: ("KEY", True)
		read = []
		triton_link._erpnext_api_secret = lambda user: read.append(user) or "SECRET"
		self.triton_link._request = lambda *a, **k: self.fail("must not reach Triton")

		try:
			with self.assertRaises(frappe.ThrowError) as caught:
				triton_link.link_bot_credentials()
			self.assertIn("System Manager", str(caught.exception))
			self.assertEqual(read, [], "the secret must not even be decrypted on a refused link")
		finally:
			for name in ("_bot_user", "_erpnext_credential", "_erpnext_api_secret"):
				delattr(triton_link, name)

	def test_an_unanswerable_role_lookup_is_treated_as_unsafe(self):
		"""Fails closed. "I could not check" must never read as "nothing to worry about"."""
		import frappe

		def _boom(user):
			raise RuntimeError("no db")

		frappe.get_roles = _boom
		self.assertEqual(
			self.triton_link.unsafe_roles("bot@x.y"),
			sorted(self.triton_link.UNSAFE_BOT_ROLES),
		)


if __name__ == "__main__":
	unittest.main()

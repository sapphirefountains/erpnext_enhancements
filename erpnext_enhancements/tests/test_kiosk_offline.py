"""``workforce/kiosk_offline.py``: whose kept kiosk a shared phone may show, and replay, offline.

The Time Kiosk's root-scope worker keeps the ``/kiosk`` page and the kiosk's GET answers so the app
works in a dead zone, and queues location points until they upload. On a shared phone, the next
technician used to be shown the last one's My Day, history, correction requests and Map trail
offline, and the kept page outlived every sign-out. The marker cookie ``ee_kiosk_key`` is the fix's
server half (the itinerary's pattern, ``travel_management/itinerary_offline.py``):

* set with every ``/kiosk`` render, as a keyed hash and never the email, readable by the page;
* deleted by every sign-out (``on_logout``) and by a sign-in as anybody else (``on_login``), which
  is what covers a session that merely expired followed by somebody else's sign-in by any route;
* echoed on every kiosk API GET (``after_request``), so the worker keeps an answer only for the
  person the server actually answered;
* checked by ``log_geolocation_batch``, so one person's queued points are never filed as another's.

None of the hooks may raise: frappe runs them inside the login, the logout and every request.

The module is run from its source over a stand-in ``frappe`` (patched into ``sys.modules`` only
while it is exec'd). No bench, no stub left behind.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_offline
"""

import ast
import hashlib
import hmac
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
MODULE = APP / "workforce" / "kiosk_offline.py"
SECRET = "site-encryption-key"


class _CookieJar:
	"""frappe v16's CookieManager by its signature (frappe/auth.py at v16.53.0): what was set and
	what deleted. A keyword it does not take (``path``, say) raises TypeError here as there."""

	def __init__(self):
		self.cookies = {}
		self.to_delete = []

	def set_cookie(
		self,
		key,
		value,
		expires=None,
		secure=False,
		httponly=False,
		samesite="Lax",
		max_age=None,
		deduplicate=False,
	):
		self.cookies[key] = {
			"value": value,
			"expires": expires,
			"secure": secure,
			"httponly": httponly,
			"samesite": samesite,
			"max_age": max_age,
		}

	def delete_cookie(self, to_delete):
		if not isinstance(to_delete, list | tuple):
			to_delete = [to_delete]
		self.to_delete.extend(to_delete)


def _module(scheme="https", url="https://erp.example.com", held=None, cookies=True, user="pat@example.com"):
	jar = _CookieJar() if cookies else None
	request = types.SimpleNamespace(scheme=scheme, cookies=dict(held or {}))
	session = types.SimpleNamespace(user=user)
	fake = types.SimpleNamespace(
		local=types.SimpleNamespace(cookie_manager=jar, request=request, session=session),
		session=session,
		utils=types.SimpleNamespace(get_url=lambda: url),
	)
	namespace = {"__name__": "kiosk_offline_under_test"}
	with mock.patch.dict(sys.modules, {"frappe": fake}):
		exec(compile(MODULE.read_text(encoding="utf-8"), str(MODULE), "exec"), namespace)
	namespace["_secret"] = lambda: SECRET
	return namespace, jar


class TestTheMarker(unittest.TestCase):
	def test_it_is_a_keyed_hash_never_the_email(self):
		m, _jar = _module()
		key = m["key_for"]("pat@example.com")
		self.assertRegex(key, r"^[0-9a-f]{64}$")
		self.assertNotIn("pat", key)
		self.assertEqual(key, m["key_for"]("pat@example.com"))
		self.assertNotEqual(key, m["key_for"]("sam@example.com"))
		self.assertEqual(
			key, hmac.new(SECRET.encode(), m["_CONTEXT"] + b"pat@example.com", hashlib.sha256).hexdigest()
		)
		self.assertNotEqual(key, hashlib.sha256(b"pat@example.com").hexdigest())
		for nobody in ("Guest", "", None):
			self.assertEqual(m["key_for"](nobody), "")

	def test_it_is_not_the_itineraries_marker(self):
		"""Two features, two cookies, two contexts: one person's two markers differ."""
		m, _jar = _module()
		self.assertNotIn(b"itinerary", m["_CONTEXT"])
		itinerary = APP / "travel_management" / "itinerary_offline.py"
		self.assertNotIn(m["_CONTEXT"].decode("utf-8", "replace"), itinerary.read_text(encoding="utf-8"))
		self.assertEqual(m["COOKIE"], "ee_kiosk_key")

	def test_the_cookie_is_one_the_page_can_read(self):
		m, jar = _module()
		key = m["set_marker"]("pat@example.com")
		self.assertEqual(key, m["key_for"]("pat@example.com"))
		self.assertEqual(
			jar.cookies,
			{
				"ee_kiosk_key": {
					"value": key,
					"expires": None,
					"secure": True,
					"httponly": False,
					"samesite": "Lax",
					"max_age": 30 * 24 * 60 * 60,
				}
			},
		)
		page = (APP / "public" / "js" / "kiosk" / "app.js").read_text(encoding="utf-8")
		self.assertIn("ee_kiosk_key=([^;]*)", page)

	def test_it_is_set_at_the_site_root_with_keywords_v16_takes(self):
		allowed = set(inspect.signature(_CookieJar.set_cookie).parameters) - {"self"}
		calls = [
			node
			for node in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8")))
			if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "set_cookie"
		]
		self.assertEqual(len(calls), 1)
		keywords = {k.arg for k in calls[0].keywords}
		self.assertNotIn("path", keywords)
		self.assertLessEqual(keywords, allowed)

	def test_secure_follows_the_request_like_frappes_own_cookies(self):
		"""Not the site's configured address: a Secure cookie set over plain http is dropped,
		and a kiosk whose marker is dropped refuses to draw."""
		for scheme, url, secure in (
			("https", "http://localhost:8000", True),
			("https", "https://erp.example.com", True),
			("http", "https://erp.example.com", False),
			("http", "http://localhost:8000", False),
		):
			with self.subTest(scheme=scheme, url=url):
				m, jar = _module(scheme=scheme, url=url)
				m["set_marker"]("pat@example.com")
				self.assertIs(jar.cookies["ee_kiosk_key"]["secure"], secure)

	def test_a_guest_gets_none_and_nothing_raises(self):
		m, jar = _module()
		self.assertEqual(m["set_marker"]("Guest"), "")
		self.assertEqual(jar.cookies, {})

		def broken():
			raise RuntimeError("no encryption key")

		m["_secret"] = broken
		self.assertEqual(m["set_marker"]("pat@example.com"), "")
		m, _jar = _module(cookies=False)
		self.assertEqual(m["set_marker"]("pat@example.com"), "")

	def test_kiosk_py_puts_it_in_the_boot(self):
		kiosk = (APP / "www" / "kiosk.py").read_text(encoding="utf-8")
		self.assertIn("from erpnext_enhancements.workforce.kiosk_offline import set_marker", kiosk)
		self.assertIn('boot["offline_key"] = set_marker(frappe.session.user)', kiosk)


class TestWhatRemovesIt(unittest.TestCase):
	def test_every_sign_out_deletes_it(self):
		m, jar = _module(held={"ee_kiosk_key": "a" * 64})
		m["forget_on_logout"](login_manager=types.SimpleNamespace(user="pat@example.com"))
		self.assertEqual(jar.to_delete, ["ee_kiosk_key"])
		m, jar = _module()
		m["forget_on_logout"]()
		self.assertEqual(jar.to_delete, ["ee_kiosk_key"])

	def test_a_sign_in_as_anybody_else_deletes_it(self):
		"""The route does not matter: the password form, ``POST /`` with ``cmd=login``,
		``login_via_google`` and ``login_via_key`` all reach ``post_login`` and so ``on_login``.
		That is what makes an expired session safe for the next person."""
		m, _jar = _module()
		pats = m["key_for"]("pat@example.com")
		for user, deleted in (
			("sam@example.com", ["ee_kiosk_key"]),
			("Guest", ["ee_kiosk_key"]),
			("pat@example.com", []),
		):
			with self.subTest(user=user):
				m, jar = _module(held={"ee_kiosk_key": pats})
				m["forget_on_login"](login_manager=types.SimpleNamespace(user=user))
				self.assertEqual(jar.to_delete, deleted)
		m, jar = _module()
		m["forget_on_login"](login_manager=types.SimpleNamespace(user="sam@example.com"))
		self.assertEqual(jar.to_delete, [], "nothing held: no cookie is sent at all")
		m, jar = _module(held={"ee_kiosk_key": pats})

		def broken():
			raise RuntimeError("no encryption key")

		m["_secret"] = broken
		m["forget_on_login"](login_manager=types.SimpleNamespace(user="pat@example.com"))
		self.assertEqual(jar.to_delete, ["ee_kiosk_key"], "a key that cannot be made is nobody's")

	def test_the_hooks_never_raise(self):
		class Exploding:
			def delete_cookie(self, *a, **k):
				raise RuntimeError("boom")

			def set_cookie(self, *a, **k):
				raise RuntimeError("boom")

		m, _jar = _module(held={"ee_kiosk_key": "b" * 64})
		m["frappe"].local.cookie_manager = Exploding()
		m["forget_on_logout"](login_manager=None)
		m["forget_on_login"](login_manager=types.SimpleNamespace(user="sam@example.com"))
		self.assertEqual(m["set_marker"]("pat@example.com"), "")
		m["frappe"].local = types.SimpleNamespace()
		m["forget_on_logout"]()
		m["forget_on_login"]()


class TestTheEcho(unittest.TestCase):
	"""``stamp_response``: each kiosk API GET is labelled with the session that answered."""

	def _response(self):
		return types.SimpleNamespace(headers={})

	def _request(self, path, method="GET"):
		return types.SimpleNamespace(path=path, method=method)

	def test_a_kiosk_get_carries_the_answering_sessions_key(self):
		m, _jar = _module(user="sam@example.com")
		res = self._response()
		m["stamp_response"](response=res, request=self._request(m["API_PREFIX"] + "get_my_day"))
		self.assertEqual(res.headers, {"X-Kiosk-Key": m["key_for"]("sam@example.com")})

	def test_a_guest_answer_names_nobody(self):
		m, _jar = _module(user="Guest")
		res = self._response()
		m["stamp_response"](response=res, request=self._request(m["API_PREFIX"] + "get_my_day"))
		self.assertEqual(res.headers, {"X-Kiosk-Key": ""})

	def test_nothing_else_is_touched(self):
		m, _jar = _module()
		for path, method in (
			("/api/method/frappe.client.get_list", "GET"),
			("/kiosk", "GET"),
			(m["API_PREFIX"] + "log_time", "POST"),
			("/api/method/erpnext_enhancements.api.kiosk_photos.get_my_photos", "GET"),
		):
			with self.subTest(path=path, method=method):
				res = self._response()
				m["stamp_response"](response=res, request=self._request(path, method))
				self.assertEqual(res.headers, {})

	def test_the_prefix_is_the_workers(self):
		m, _jar = _module()
		worker = (APP / "www" / "kiosk-sw.js").read_text(encoding="utf-8")
		self.assertIn(f"const API_PREFIX = '{m['API_PREFIX']}';", worker)
		self.assertIn(f"const KEY_HEADER = '{m['HEADER']}';", worker)

	def test_it_never_raises(self):
		m, _jar = _module()
		m["stamp_response"]()
		m["stamp_response"](response=object(), request=self._request(m["API_PREFIX"] + "x"))

		def broken():
			raise RuntimeError("no encryption key")

		m["_secret"] = broken
		m["stamp_response"](response=self._response(), request=self._request(m["API_PREFIX"] + "x"))


class TestTheLocationQueue(unittest.TestCase):
	"""``belongs_to_session``: ``log_geolocation_batch`` refuses another person's points."""

	def test_only_the_sessions_own_key_or_none_passes(self):
		m, _jar = _module(user="sam@example.com")
		self.assertTrue(m["belongs_to_session"](m["key_for"]("sam@example.com")))
		self.assertFalse(m["belongs_to_session"](m["key_for"]("pat@example.com")))
		self.assertTrue(m["belongs_to_session"](""), "a point queued before v1.753.2 carries none")
		self.assertTrue(m["belongs_to_session"](None))
		self.assertFalse(m["belongs_to_session"]("not-a-key"))

	def test_a_key_that_cannot_be_checked_is_refused(self):
		m, _jar = _module(user="sam@example.com")

		def broken():
			raise RuntimeError("no encryption key")

		m["_secret"] = broken
		self.assertFalse(m["belongs_to_session"]("a" * 64))

	def test_the_endpoint_refuses_before_it_writes(self):
		source = (APP / "api" / "time_kiosk.py").read_text(encoding="utf-8")
		fn = source[source.index("def log_geolocation_batch(points, offline_key=None):") :]
		fn = fn[: fn.index("\n@frappe.whitelist")]
		self.assertLess(fn.index("kiosk_offline.belongs_to_session(offline_key)"), fn.index("max_batch ="))
		self.assertIn('"reason": "other_person"', fn)
		worker = (APP / "www" / "kiosk-sw.js").read_text(encoding="utf-8")
		self.assertNotIn("'other_person'", worker[worker.index("const PERMANENT_REJECTS") :][:120])


class TestTheHooksAreWired(unittest.TestCase):
	def test_hooks_py(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		hooks = {
			node.targets[0].id: ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
			and node.targets[0].id in ("on_login", "on_logout", "after_request")
		}
		base = "erpnext_enhancements.workforce.kiosk_offline."
		self.assertIn(base + "forget_on_logout", hooks["on_logout"])
		self.assertIn(base + "forget_on_login", hooks["on_login"])
		self.assertIn(base + "stamp_response", hooks["after_request"])
		# Before portal_login's guard, which raises for a refused sign-in: the marker goes
		# whatever that guard decides.
		self.assertLess(
			hooks["on_login"].index(base + "forget_on_login"),
			hooks["on_login"].index("erpnext_enhancements.portal_login.refuse_staff_email_link_login"),
		)


if __name__ == "__main__":
	unittest.main()

"""Pure-Python (no Frappe site) unit tests for the persona proxy in triton_chat.

``triton_chat`` imports ``frappe``, ``requests`` and ``werkzeug.wrappers`` at
module scope, so :func:`install_stubs` puts just enough of each into
``sys.modules`` for the module to import and its whitelisted methods to run.

The headline test is the cache key. ``list_models`` is safely cached under a
single site-wide key because the model list is identical for everyone; a persona
list is not — it contains the caller's own private personas. Copying that
pattern verbatim would serve one user's personas to the whole site, so the
per-user key and its invalidation are asserted explicitly here.
"""
import sys
import types


class _Cache:
	"""Minimal frappe.cache() stand-in that records what it was asked for."""

	def __init__(self):
		self.store = {}
		self.deleted = []

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value

	def delete_value(self, key):
		self.deleted.append(key)
		self.store.pop(key, None)


def _stub_throw(message=None, *args, **kwargs):
	raise Exception(message if isinstance(message, str) else "frappe.throw")


def install_stubs():
	"""Install fake frappe / requests / werkzeug modules and return the frappe stub."""
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")
	frappe_utils.cint = lambda value=0, *a, **k: int(float(value or 0))
	frappe_utils.get_fullname = lambda user=None: "Test User"
	frappe.utils = frappe_utils

	frappe.throw = _stub_throw
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe._ = lambda s, *a, **k: s
	frappe.session = types.SimpleNamespace(user="a@sapphirefountains.com")
	frappe.log_error = lambda *a, **k: None
	frappe.get_cached_doc = lambda *a, **k: types.SimpleNamespace()
	frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None)

	cache = _Cache()
	frappe.cache = lambda: cache
	frappe._test_cache = cache

	class PermissionError(Exception):
		pass

	frappe.PermissionError = PermissionError

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe_utils

	requests = sys.modules.get("requests") or types.ModuleType("requests")
	requests.request = lambda *a, **k: None
	requests.post = lambda *a, **k: None
	sys.modules["requests"] = requests

	werkzeug = sys.modules.get("werkzeug") or types.ModuleType("werkzeug")
	wrappers = types.ModuleType("werkzeug.wrappers")

	class Response:
		def __init__(self, *a, **k):
			self.args, self.kwargs = a, k

	wrappers.Response = Response
	werkzeug.wrappers = wrappers
	sys.modules["werkzeug"] = werkzeug
	sys.modules["werkzeug.wrappers"] = wrappers

	return frappe


def _load(monkeypatch, request_result=None, request_raises=False):
	"""Import triton_chat against the stubs, with ``_request`` replaced by a spy."""
	frappe = install_stubs()
	sys.modules.pop("erpnext_enhancements.triton_chat", None)
	from erpnext_enhancements import triton_chat

	calls = []

	def fake_request(method, path, payload=None):
		calls.append((method, path, payload))
		if request_raises:
			raise Exception("Triton unreachable")
		return request_result

	monkeypatch.setattr(triton_chat, "_request", fake_request)
	return triton_chat, calls, frappe


# ---------------------------------------------------------------------------
# Cache scoping — the highest-severity correctness issue in this feature
# ---------------------------------------------------------------------------
def test_persona_cache_key_is_per_user(monkeypatch):
	triton_chat, _, frappe = _load(monkeypatch)

	frappe.session.user = "a@sapphirefountains.com"
	key_a = triton_chat._persona_cache_key()
	frappe.session.user = "b@sapphirefountains.com"
	key_b = triton_chat._persona_cache_key()

	assert key_a != key_b
	assert "a@sapphirefountains.com" in key_a
	# Must not collide with the (legitimately) site-wide model list key.
	assert "triton_models_list" not in key_a


def test_one_users_cached_personas_are_not_served_to_another(monkeypatch):
	triton_chat, calls, frappe = _load(monkeypatch, request_result=[{"key": "custom:1"}])

	frappe.session.user = "a@sapphirefountains.com"
	assert triton_chat.list_personas() == [{"key": "custom:1"}]
	assert triton_chat.list_personas() == [{"key": "custom:1"}]  # second call is cached
	assert len(calls) == 1

	# A different user must miss the cache and fetch their own list.
	frappe.session.user = "b@sapphirefountains.com"
	triton_chat.list_personas()
	assert len(calls) == 2


def test_list_personas_degrades_to_empty_when_triton_is_unreachable(monkeypatch):
	triton_chat, _, _ = _load(monkeypatch, request_raises=True)
	# No curated fallback here (unlike models) — the widget shows "Default" only,
	# which is always valid.
	assert triton_chat.list_personas() == []


def test_non_list_response_is_rejected(monkeypatch):
	triton_chat, _, _ = _load(monkeypatch, request_result={"detail": "nope"})
	assert triton_chat.list_personas() == []


# ---------------------------------------------------------------------------
# Mutations invalidate the cache
# ---------------------------------------------------------------------------
def test_every_mutation_invalidates_the_users_cache(monkeypatch):
	triton_chat, _, frappe = _load(monkeypatch, request_result={"key": "custom:1"})
	cache = frappe._test_cache
	expected = triton_chat._persona_cache_key()

	triton_chat.create_persona(name="X", system_prompt="y")
	triton_chat.update_persona(persona_id="1", name="Z")
	triton_chat.delete_persona(persona_id="1")
	triton_chat.duplicate_persona(persona_key="builtin:dan_bot")
	triton_chat.set_default_persona(persona_key="builtin:dan_bot")

	assert cache.deleted.count(expected) == 5


# ---------------------------------------------------------------------------
# Path building
# ---------------------------------------------------------------------------
def test_custom_persona_path_builds_the_integer_route(monkeypatch):
	triton_chat, _, _ = _load(monkeypatch)
	assert triton_chat._custom_persona_path(42) == "/api/v1/personas/custom/42"
	assert triton_chat._custom_persona_path(" 42 ") == "/api/v1/personas/custom/42"


def test_custom_persona_path_rejects_non_integers(monkeypatch):
	triton_chat, _, _ = _load(monkeypatch)
	for bad in ("", "  ", "abc", "1/../9", "builtin:dan_bot", None, "0", "-3"):
		try:
			triton_chat._custom_persona_path(bad)
		except Exception:
			continue
		raise AssertionError(f"expected a throw for {bad!r}")


def test_duplicate_sends_the_key_in_the_body_not_the_path(monkeypatch):
	triton_chat, calls, _ = _load(monkeypatch, request_result={})
	triton_chat.duplicate_persona(persona_key="builtin:dan_bot")
	method, path, payload = calls[0]
	assert (method, path) == ("POST", "/api/v1/personas/duplicate")
	assert payload == {"key": "builtin:dan_bot"}
	assert ":" not in path


def test_create_rejects_an_unknown_visibility(monkeypatch):
	triton_chat, calls, _ = _load(monkeypatch, request_result={})
	triton_chat.create_persona(name="X", system_prompt="y", visibility="everyone")
	assert calls[0][2]["visibility"] == "private"


def test_update_only_sends_the_fields_supplied(monkeypatch):
	triton_chat, calls, _ = _load(monkeypatch, request_result={})
	triton_chat.update_persona(persona_id="7", name="Renamed")
	assert calls[0][2] == {"name": "Renamed"}


# ---------------------------------------------------------------------------
# persona_key reaches Triton
# ---------------------------------------------------------------------------
def test_start_session_forwards_persona_key(monkeypatch):
	triton_chat, calls, _ = _load(monkeypatch, request_result={"id": 1})
	monkeypatch.setattr(
		triton_chat, "get_settings", lambda: {"default_model": "gemini-3.5-flash"}
	)

	triton_chat.start_session(persona_key="builtin:dan_bot")
	assert calls[0][2]["persona_key"] == "builtin:dan_bot"

	# Omitted entirely -> Triton falls back to the account default.
	triton_chat.start_session()
	assert "persona_key" not in calls[1][2]


def _run_stream(monkeypatch, **kwargs):
	"""Drive stream_query far enough to capture the JSON body it forwards."""
	triton_chat, _, _ = _load(monkeypatch)
	monkeypatch.setattr(triton_chat, "mint_user_token", lambda *a, **k: "tok")
	monkeypatch.setattr(
		triton_chat,
		"get_settings",
		lambda: {"enabled": True, "base_url": "https://t", "timeout": 30, "debug": False},
	)

	sent = {}

	class _Streamed:
		status_code = 200

		def __enter__(self):
			return self

		def __exit__(self, *a):
			return False

		def iter_content(self, chunk_size=None):
			return iter([b"data: {}\n\n"])

	def fake_post(url, json=None, headers=None, stream=None, timeout=None):
		sent["url"] = url
		sent["json"] = json
		return _Streamed()

	monkeypatch.setattr(triton_chat.requests, "post", fake_post)

	# Response is stubbed to keep the generator it was handed, so we can drain
	# it here — the request only fires once the lazy body is consumed.
	class _Resp:
		def __init__(self, body, *a, **k):
			self.body = body

	monkeypatch.setattr(triton_chat, "Response", _Resp)

	resp = triton_chat.stream_query(session_id="1", **kwargs)
	list(resp.body)
	return sent


def test_stream_query_payload_carries_persona_key(monkeypatch):
	sent = _run_stream(monkeypatch, prompt="hi", persona_key="custom:5")
	assert sent["json"]["persona_key"] == "custom:5"
	assert sent["json"]["prompt"] == "hi"
	assert sent["url"].endswith("/api/v1/assistant/sessions/1/query/stream")


def test_stream_query_forwards_empty_persona_key_as_the_default_voice(monkeypatch):
	# "" is meaningful: it pins this turn to plain Triton. Triton distinguishes
	# it from an omitted field, which inherits the session/account persona.
	sent = _run_stream(monkeypatch, prompt="hi", persona_key="")
	assert sent["json"]["persona_key"] == ""


def test_stream_query_omits_persona_key_when_not_supplied(monkeypatch):
	sent = _run_stream(monkeypatch, prompt="hi")
	assert "persona_key" not in sent["json"]

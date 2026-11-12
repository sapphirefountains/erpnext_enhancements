"""The framed Triton widget's server half: who gets it, and the one-time sign-in ticket.

``api/triton_frame.py`` decides two things a mistake in which is expensive and quiet.

**Who is on the framed widget.** The answer rides in ``frappe.boot.triton_frame`` and the classic
widget goes to sleep when it is there. So a wrong "framed" takes a person's working assistant away
and hands them a frame that may not exist yet. Every unclear case must read as classic: the mode
field missing (it is new, and a ``default`` on a new field of a Single never reaches the row that
already exists, so that is the state of every live site on the day this ships), a value this code
does not know, no usable Triton address, no shared secret. And the framed widget must never be a
way around the master switch or the allow-list.

**Who can get a ticket, and what leaks when it fails.** A ticket signs its holder in to Triton as
the person it was issued for. It must be issued only to someone on the framed widget, only over
POST, and neither it nor the shared secret may reach a log line. A failure the person can do
nothing about must come back as a plain sentence, not an exception: a ``frappe.throw`` puts a red
Desk dialog in front of someone who only pressed a button.

Pure Python: reuses the frappe / requests / werkzeug stubs from ``test_triton_personas`` so
``triton_chat`` and ``api.triton_frame`` import with no Frappe site. pytest-style on purpose (it
needs ``monkeypatch``), so it has its own ``python -m pytest`` step in ``ci.yml``; appended to a
``python -m unittest`` list it would collect nothing and report success.
"""

import ast
import json
import pathlib
import sys
import types

import pytest

from erpnext_enhancements.tests.test_triton_personas import install_stubs

APP = pathlib.Path(__file__).resolve().parents[1]
SECRET = "gateway-secret-do-not-log"
TICKET = "ticket-do-not-log"
USER = "pat@example.com"


class _Doc:
	"""Stands in for a cached Single (or a child row): attribute *and* ``.get()`` access."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def get_password(self, key, raise_exception=True):
		return self.__dict__.get(key)


class _Response:
	def __init__(self, status_code=200, body=None, text=""):
		self.status_code = status_code
		self._body = body
		self.text = text

	def json(self):
		if self._body is None:
			raise ValueError("not JSON")
		return self._body


def _rows(*users):
	return [_Doc(user=user) for user in users]


def _load(monkeypatch, *, user=USER, behavior=None, conn=None):
	"""Import ``api.triton_frame`` against the stubs. Returns ``(module, frappe, state)``.

	``state`` records every HTTP call and every ``log_error`` so a test can prove that something
	did NOT happen, which is most of what this suite is for.
	"""
	frappe = install_stubs()
	for name in ("erpnext_enhancements.triton_chat", "erpnext_enhancements.api.triton_frame"):
		sys.modules.pop(name, None)

	state = types.SimpleNamespace(
		posts=[], logs=[], reply=_Response(200, {"ticket": TICKET, "expires_in": 60})
	)

	fields = dict(
		enabled=1,
		default_model=None,
		request_timeout=120,
		enable_page_context=1,
		enable_write_actions=1,
		debug_logging=0,
		restrict_to_whitelist=0,
		allowed_users=[],
		widget_mode="Framed for everyone",
		framed_pilot_users=[],
		framed_site_url=None,
	)
	fields.update(behavior or {})
	connection = dict(gateway_url="https://triton.example.com", admin_webhook_secret=SECRET)
	connection.update(conn or {})
	docs = {"Triton Assistant Settings": _Doc(**fields), "Triton Settings": _Doc(**connection)}

	frappe.session.user = user
	frappe.get_cached_doc = lambda doctype, *a, **k: docs[doctype]
	frappe.db.get_value = lambda *a, **k: None  # no separate email on the User: the name is used

	def throw(message=None, exc=None, *a, **k):
		raise (exc or Exception)(message)

	frappe.throw = throw
	frappe.log_error = lambda *a, **k: state.logs.append((a, k))

	def post(url, **kwargs):
		state.posts.append((url, kwargs))
		if isinstance(state.reply, Exception):
			raise state.reply
		return state.reply

	sys.modules["requests"].post = post

	from erpnext_enhancements.api import triton_frame

	return triton_frame, frappe, state


# ---------------------------------------------------------------------------
# Which widget
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
	"mode",
	[
		None,  # the field has no row yet: every live site on the day this ships
		"",
		"Classic",
		"classic",
		"Framed",  # a value this code does not know
		"Framed for everyone else",  # close is not the same
		0,
	],
)
def test_anything_but_the_two_framed_modes_is_classic(monkeypatch, mode):
	triton_frame, _, _ = _load(monkeypatch, behavior={"widget_mode": mode})

	assert triton_frame.frame_config() is None
	assert triton_frame.boot_payload() is None


def test_framed_for_everyone_gives_the_origin_the_page_and_the_context_switch(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch)

	assert triton_frame.frame_config() == {
		"origin": "https://triton.example.com",
		"url": "https://triton.example.com/embed.html",
		"page_context": 1,
	}


def test_the_boot_payload_never_carries_the_secret(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch)

	assert SECRET not in json.dumps(triton_frame.boot_payload())
	assert set(triton_frame.boot_payload()) == {"origin", "url", "page_context"}


def test_page_context_off_is_passed_on(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch, behavior={"enable_page_context": 0})

	assert triton_frame.frame_config()["page_context"] == 0


def test_pilot_mode_is_framed_only_for_the_people_listed(monkeypatch):
	behavior = {
		"widget_mode": "Framed for pilot users",
		"framed_pilot_users": _rows("nik@example.com", USER, " ", None),
	}
	triton_frame, frappe, _ = _load(monkeypatch, behavior=behavior)

	assert triton_frame.frame_config() is not None

	frappe.session.user = "someone.else@example.com"
	assert triton_frame.frame_config() is None, "a person outside the pilot list got the framed widget"

	# Unlike the allow-list, the pilot has no built-in exception: Administrator is on it or not.
	frappe.session.user = "Administrator"
	assert triton_frame.frame_config() is None


def test_an_empty_pilot_list_means_nobody(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch, behavior={"widget_mode": "Framed for pilot users"})

	assert triton_frame.frame_config() is None


def test_the_master_switch_wins(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch, behavior={"enabled": 0})

	assert triton_frame.frame_config() is None


def test_the_allow_list_wins(monkeypatch):
	behavior = {"restrict_to_whitelist": 1, "allowed_users": _rows("nik@example.com")}
	triton_frame, frappe, _ = _load(monkeypatch, behavior=behavior)

	assert triton_frame.frame_config() is None, "the framed widget went around the allow-list"

	frappe.session.user = "nik@example.com"
	assert triton_frame.frame_config() is not None


def test_a_guest_is_never_framed(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch, user="Guest")

	assert triton_frame.frame_config() is None


def test_no_secret_means_classic(monkeypatch):
	"""No secret, no ticket; and a frame that cannot sign in is a dead panel."""
	triton_frame, _, _ = _load(monkeypatch, conn={"admin_webhook_secret": None})

	assert triton_frame.frame_config() is None


@pytest.mark.parametrize(
	"gateway_url",
	[
		"",
		None,
		"http://triton.example.com",  # plain http off a developer's machine
		"triton.example.com",
		"ftp://triton.example.com",
		"https://user:pw@triton.example.com",
		"https://",
		"javascript:alert(1)",
		"https://triton.example.com:notaport",
	],
)
def test_an_unusable_triton_address_means_classic(monkeypatch, gateway_url):
	triton_frame, _, _ = _load(monkeypatch, conn={"gateway_url": gateway_url})

	assert triton_frame.frame_config() is None


@pytest.mark.parametrize(
	"raw, origin",
	[
		("https://triton.example.com", "https://triton.example.com"),
		("https://triton.example.com/", "https://triton.example.com"),
		("https://Triton.Example.com/api/v1?x=1#y", "https://triton.example.com"),
		("https://triton.example.com:8443/api", "https://triton.example.com:8443"),
		("http://localhost:4317", "http://localhost:4317"),
		("http://127.0.0.1:8017/", "http://127.0.0.1:8017"),
		("http://triton.sapphire.localhost:4317", "http://triton.sapphire.localhost:4317"),
		("http://[::1]:8017", "http://[::1]:8017"),
		("http://localhost.evil.example.com", None),
		("http://evil.example.com/localhost", None),
	],
)
def test_frame_origin(monkeypatch, raw, origin):
	triton_frame, _, _ = _load(monkeypatch)

	assert triton_frame.frame_origin(None, raw) == origin


def test_the_site_address_field_overrides_the_gateway_url(monkeypatch):
	"""For a Gateway URL that is an internal address the browser cannot open."""
	triton_frame, _, _ = _load(
		monkeypatch,
		behavior={"framed_site_url": " https://triton.example.org/ "},
		conn={"gateway_url": "http://10.0.0.5:8000"},
	)

	assert triton_frame.frame_config()["origin"] == "https://triton.example.org"


def test_a_bad_site_address_does_not_fall_through_to_the_gateway_url(monkeypatch):
	"""Somebody typed an address and got it wrong. Quietly using a different one would hide that."""
	triton_frame, _, _ = _load(monkeypatch, behavior={"framed_site_url": "http://triton.example.org"})

	assert triton_frame.frame_config() is None


def test_boot_payload_never_raises(monkeypatch):
	"""Boot runs on every Desk page. A broken settings read must leave the classic widget in charge."""
	triton_frame, frappe, _ = _load(monkeypatch)

	def boom(*a, **k):
		raise RuntimeError("settings table is missing")

	frappe.get_cached_doc = boom

	with pytest.raises(Exception):
		triton_frame.frame_config()
	assert triton_frame.boot_payload() is None


# ---------------------------------------------------------------------------
# The ticket
# ---------------------------------------------------------------------------
def test_a_ticket_is_asked_for_server_to_server_with_the_shared_secret(monkeypatch):
	triton_frame, _, state = _load(monkeypatch)

	assert triton_frame.get_ticket() == {"ok": True, "ticket": TICKET, "expires_in": 60}

	assert len(state.posts) == 1
	url, kwargs = state.posts[0]
	assert url == "https://triton.example.com/api/v1/auth/erpnext-bridge/embed-ticket"
	assert kwargs["headers"] == {"Authorization": f"Bearer {SECRET}"}
	assert kwargs["json"] == {"email": USER, "full_name": "Test User"}
	assert kwargs["timeout"] > 0
	assert state.logs == []


def test_the_ticket_is_not_cached(monkeypatch):
	"""A ticket works once. Handing the same one out twice signs the second caller in to nothing."""
	triton_frame, frappe, state = _load(monkeypatch)

	triton_frame.get_ticket()
	triton_frame.get_ticket()

	assert len(state.posts) == 2
	assert TICKET not in json.dumps(frappe._test_cache.store, default=str)
	assert SECRET not in json.dumps(frappe._test_cache.store, default=str)


def test_a_guest_cannot_get_a_ticket(monkeypatch):
	triton_frame, frappe, state = _load(monkeypatch, user="Guest")

	with pytest.raises(frappe.PermissionError):
		triton_frame.get_ticket()
	assert state.posts == []


@pytest.mark.parametrize(
	"behavior",
	[
		{"widget_mode": None},
		{"widget_mode": "Classic"},
		{"widget_mode": "Framed for pilot users", "framed_pilot_users": _rows("nik@example.com")},
		{"enabled": 0},
		{"restrict_to_whitelist": 1, "allowed_users": _rows("nik@example.com")},
	],
)
def test_nobody_off_the_framed_widget_can_get_a_ticket(monkeypatch, behavior):
	"""Calling the method directly must not be a way to a Triton sign-in the Desk would not offer."""
	triton_frame, frappe, state = _load(monkeypatch, behavior=behavior)

	with pytest.raises(frappe.PermissionError):
		triton_frame.get_ticket()
	assert state.posts == [], "Triton was asked for a ticket for someone who is not on the framed widget"


@pytest.mark.parametrize(
	"reply, reason",
	[
		(ConnectionError("Max retries exceeded with url: /api/v1/auth"), "unreachable"),
		(_Response(500, text="Internal Server Error"), "unreachable"),
		(_Response(502), "unreachable"),
		(_Response(200, None), "unreachable"),  # a proxy's HTML page with a 200
		(_Response(200, {"detail": "ok"}), "unreachable"),  # no ticket in it
		(_Response(200, {"ticket": ""}), "unreachable"),
		(_Response(200, {"ticket": 12345}), "unreachable"),
		(_Response(200, {"ticket": "x" * 513}), "unreachable"),
		(_Response(200, ["ticket"]), "unreachable"),
		(_Response(404), "unsupported"),
		(_Response(405), "unsupported"),
		(_Response(501), "unsupported"),
		(_Response(401), "refused"),
		(_Response(403), "refused"),
	],
)
def test_a_failure_is_an_answer_in_plain_words_not_an_exception(monkeypatch, reply, reason):
	triton_frame, _, state = _load(monkeypatch)
	state.reply = reply

	result = triton_frame.get_ticket()

	assert result["ok"] is False
	assert result["reason"] == reason
	assert "ticket" not in result
	message = result["message"]
	assert message and message[0].isupper() and message.endswith(".")
	for leak in (SECRET, "Bearer", "Max retries", "/api/v1", "Traceback", "500", "Internal Server Error"):
		assert leak not in message, f"the person was shown {leak!r}"
	assert state.logs == [], "something was logged with Debug Logging off"


def test_debug_logging_writes_the_status_and_nothing_else(monkeypatch):
	triton_frame, _, state = _load(monkeypatch, behavior={"debug_logging": 1})
	state.reply = _Response(200, {"ticket": 12345, "secret_echo": SECRET}, text=f"{TICKET} {SECRET}")

	triton_frame.get_ticket()
	state.reply = _Response(503, text=f"upstream said {SECRET}")
	triton_frame.get_ticket()
	state.reply = ConnectionError(f"https://triton.example.com/?key={SECRET}")
	triton_frame.get_ticket()

	assert len(state.logs) == 3
	written = repr(state.logs)
	assert SECRET not in written
	assert TICKET not in written
	assert "HTTP 200" in written and "HTTP 503" in written and "no answer" in written
	for args, kwargs in state.logs:
		assert not args, "log_error was given a positional argument; pass title= and message="
		assert set(kwargs) == {"title", "message"}


def test_a_good_ticket_is_never_logged_even_with_debug_on(monkeypatch):
	triton_frame, _, state = _load(monkeypatch, behavior={"debug_logging": 1})

	triton_frame.get_ticket()

	assert state.logs == []


def test_too_many_requests_in_a_minute_stops_asking_triton(monkeypatch):
	triton_frame, frappe, state = _load(monkeypatch)

	for _ in range(triton_frame.TICKET_LIMIT):
		assert triton_frame.get_ticket()["ok"] is True
	blocked = triton_frame.get_ticket()

	assert blocked["ok"] is False and blocked["reason"] == "busy"
	assert len(state.posts) == triton_frame.TICKET_LIMIT

	# One person's limit is not another's.
	frappe.session.user = "nik@example.com"
	assert triton_frame.get_ticket()["ok"] is True


def test_an_odd_expiry_from_triton_falls_back_to_a_minute(monkeypatch):
	triton_frame, _, state = _load(monkeypatch)
	state.reply = _Response(200, {"ticket": TICKET, "expires_in": "soon"})

	assert triton_frame.get_ticket() == {"ok": True, "ticket": TICKET, "expires_in": 60}


# ---------------------------------------------------------------------------
# Shape: things a behaviour test cannot see
# ---------------------------------------------------------------------------
def _module_ast():
	return ast.parse((APP / "api" / "triton_frame.py").read_text(encoding="utf-8"))


def test_get_ticket_is_whitelisted_for_post_only():
	"""A GET is not CSRF-checked. A ticket must not be one <img src> away."""
	fn = next(
		node for node in _module_ast().body if isinstance(node, ast.FunctionDef) and node.name == "get_ticket"
	)
	(decorator,) = fn.decorator_list
	assert ast.unparse(decorator) == "frappe.whitelist(methods=['POST'])"


def test_get_ticket_is_the_only_whitelisted_function():
	whitelisted = [
		node.name
		for node in _module_ast().body
		if isinstance(node, ast.FunctionDef)
		and any("whitelist" in ast.unparse(d) for d in node.decorator_list)
	]
	assert whitelisted == ["get_ticket"]


def test_the_secret_is_read_in_one_place_and_the_route_in_one_function():
	"""``triton_chat.get_settings`` is the only reader of the Password field; ``_request_ticket`` is the
	only code that knows Triton's route. Comments and docstrings are not code, so the tree is walked."""
	tree = _module_ast()
	calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
	assert not any(name.endswith("get_password") for name in calls)

	routes = {}
	for fn in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
		body = (
			fn.body[1:]
			if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant)
			else fn.body
		)
		text = "\n".join(ast.unparse(statement) for statement in body)
		routes[fn.name] = "/api/v1/" in text or "requests." in text
	assert [name for name, has in routes.items() if has] == ["_request_ticket"]


def test_nothing_is_raised_or_thrown_around_the_http_call():
	"""An exception that escapes with the request's frames attached is how a header reaches the Error
	Log. ``_request_ticket`` returns a status; it has no ``raise`` and no ``frappe.throw``."""
	fn = next(
		node
		for node in _module_ast().body
		if isinstance(node, ast.FunctionDef) and node.name == "_request_ticket"
	)
	assert not [node for node in ast.walk(fn) if isinstance(node, ast.Raise)]
	assert not [
		node
		for node in ast.walk(fn)
		if isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.throw"
	]
	assert not [
		node for node in ast.walk(fn) if isinstance(node, ast.Call) and "log_error" in ast.unparse(node.func)
	]


# ---------------------------------------------------------------------------
# The settings fields, the boot key and the backfill
# ---------------------------------------------------------------------------
def _settings_json():
	path = APP / "ai_governance" / "doctype" / "triton_assistant_settings" / "triton_assistant_settings.json"
	return json.loads(path.read_text(encoding="utf-8"))


def test_the_select_offers_exactly_the_modes_the_code_knows(monkeypatch):
	triton_frame, _, _ = _load(monkeypatch)
	fields = {field["fieldname"]: field for field in _settings_json()["fields"]}

	mode = fields["widget_mode"]
	assert mode["fieldtype"] == "Select"
	assert mode["options"].split("\n") == [
		triton_frame.MODE_CLASSIC,
		triton_frame.MODE_PILOT,
		triton_frame.MODE_EVERYONE,
	]
	assert mode["default"] == triton_frame.MODE_CLASSIC, "merging this must change nothing for anyone"

	assert fields["framed_pilot_users"]["fieldtype"] == "Table"
	assert fields["framed_pilot_users"]["options"] == "Triton Allowed User"
	assert "default" not in fields["framed_site_url"]


def test_every_new_field_is_in_field_order():
	doc = _settings_json()
	assert [field["fieldname"] for field in doc["fields"]] == doc["field_order"]


def test_boot_ships_the_key_only_when_there_is_something_to_ship():
	"""The key's absence IS the classic widget: triton_widget.js and the gate both read it that way."""
	tree = ast.parse((APP / "boot.py").read_text(encoding="utf-8"))
	session = next(
		node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "boot_session"
	)
	text = ast.unparse(session)
	assert "triton_frame = _triton_frame()" in text
	assert "if triton_frame:\n        bootinfo.triton_frame = triton_frame" in text

	helper = next(
		node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_triton_frame"
	)
	assert any(
		isinstance(node, ast.Try) for node in helper.body
	), "_triton_frame must not be able to break boot"
	top_level_imports = [
		ast.unparse(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
	]
	assert not any(
		"triton_frame" in line or "triton_chat" in line for line in top_level_imports
	), "boot.py imports the relay at module level; the bench-free suites that import boot do not stub requests"


class _PatchDb:
	"""``frappe.db`` for the backfill: a ``tabSingles`` that is a set of field names."""

	def __init__(self, stored, exists=True):
		self.stored = set(stored)
		self.doctype_exists = exists
		self.written = []
		self.queries = []

	def exists(self, doctype, name=None):
		return self.doctype_exists

	def sql(self, query, values=None):
		self.queries.append(" ".join(query.split()))
		return [(field,) for field in sorted(self.stored)]

	def set_single_value(self, doctype, field, value):
		self.written.append((doctype, field, value))
		self.stored.add(field)

	def get_value(self, *a, **k):
		raise AssertionError("tabSingles has no `creation` column: frappe.db.get_value on it cannot work")


def _load_patch(stored, *, exists=True, declared=("widget_mode", "framed_site_url", "enabled")):
	frappe = install_stubs()
	model = types.ModuleType("frappe.model")
	model.no_value_fields = (
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Table",
		"Table MultiSelect",
		"Button",
	)
	frappe.model = model
	sys.modules["frappe.model"] = model
	frappe.db = _PatchDb(stored, exists=exists)
	fields = [types.SimpleNamespace(fieldname=name, fieldtype="Data") for name in declared]
	fields.append(types.SimpleNamespace(fieldname="framed_pilot_users", fieldtype="Table"))
	frappe.get_meta = lambda doctype: types.SimpleNamespace(fields=fields)
	cleared = []
	frappe.clear_document_cache = lambda *a: cleared.append(a)
	sys.modules.pop("erpnext_enhancements.patches.backfill_triton_widget_mode", None)
	from erpnext_enhancements.patches import backfill_triton_widget_mode as patch

	return patch, frappe, cleared


def test_the_backfill_writes_classic_where_the_field_has_no_row():
	patch, frappe, cleared = _load_patch({"enabled", "default_model"})

	assert patch.backfill_triton_widget_mode() == 1
	assert frappe.db.written == [("Triton Assistant Settings", "widget_mode", "Classic")]
	assert cleared, "the cached Single still says None after the write"
	assert frappe.db.queries == ["select field from tabSingles where doctype = %s"]


def test_the_backfill_never_writes_over_a_stored_value_and_is_safe_twice():
	"""A row is somebody's decision, whatever it holds. Only a missing row is filled."""
	patch, frappe, cleared = _load_patch({"enabled", "widget_mode"})

	assert patch.backfill_triton_widget_mode() == 0
	assert frappe.db.written == []
	assert cleared == []

	patch, frappe, _ = _load_patch({"enabled"})
	assert patch.backfill_triton_widget_mode() == 1
	assert patch.backfill_triton_widget_mode() == 0


def test_the_backfill_does_nothing_without_the_doctype_or_the_field():
	patch, frappe, _ = _load_patch(set(), exists=False)
	assert patch.backfill_triton_widget_mode() == 0
	assert frappe.db.queries == []

	patch, frappe, _ = _load_patch(set(), declared=("enabled",))
	assert patch.backfill_triton_widget_mode() == 0
	assert frappe.db.written == []


def test_the_backfill_default_is_the_declared_default_and_the_patch_is_registered():
	patch, _, _ = _load_patch(set())
	fields = {field["fieldname"]: field for field in _settings_json()["fields"]}

	assert patch.DEFAULTS == {"widget_mode": fields["widget_mode"]["default"]}

	registered = [
		line.strip()
		for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		if line.strip() and not line.startswith("#")
	]
	assert registered.count("erpnext_enhancements.patches.backfill_triton_widget_mode") == 1

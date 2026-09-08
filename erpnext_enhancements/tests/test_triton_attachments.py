"""Pure-Python (no Frappe site) tests for Triton chat attachments.

Reuses ``test_triton_personas.install_stubs``, which puts just enough ``frappe`` /
``requests`` / ``werkzeug`` into ``sys.modules`` for ``triton_chat`` -- and therefore
``triton_attachments``, which imports it -- to load with no bench. The stub is extended here
with the handful of ``frappe.db`` / ``frappe.get_all`` calls this module makes, because those
are the parts whose behaviour is worth asserting rather than mocking away.

**pytest, not unittest, and it needs its own CI step.** ``python -m unittest`` silently
cannot collect pytest-style function tests, which is how the QuickBooks suite ran nowhere for
weeks; and each bench-free suite installs its own frappe stub in module scope, so suites that
share a process cross-talk. Both rules are in CLAUDE.md and both are load-bearing here.

What is asserted, and why each one is here rather than left to review:

* **The preamble is byte-identical on a turn with no attachment.** The whole design rests on
  the attachment block being additive; a stray newline would change every context turn the
  assistant has ever sent.
* **A ref the caller does not own produces no line.** The browser sends a bare name and the
  server re-resolves everything. The failure mode of getting this wrong is not an error, it
  is one user's file being described into another user's prompt.
* **A client-supplied ``file_url`` on the ref is ignored.** Same reason, sharper: honouring
  it would let any session point the model at any path on the site.
* **A ``0`` upload cap means "unset", not "reject everything".** That is the Single-default
  trap, and its failure direction is silent refusal on production only.
* **``get_config`` hands the picker credentials only to a user the whitelist allows.** The
  credentials are browser-safe, not audience-free.
* **The Google link probe has three states and only 400 means "not connected".** Its failure
  direction is the expensive one: a two-state reading would show "Connect Google" to everyone
  during a Triton outage. So the status-code mapping is pinned here, code by code, along with
  the rule that ``unknown`` is never cached.
"""
import datetime
import sys
import types

from erpnext_enhancements.tests.test_triton_personas import install_stubs


class _Doc:
	"""Stands in for a cached Single: attribute *and* .get() access."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def get_password(self, key, raise_exception=True):
		return self.__dict__.get(key)


#: The Gateway Secret this fake site holds. Distinctive on purpose: every browser-facing
#: return value is searched for it, and a substring that could plausibly occur in a URL or a
#: timestamp would make that search meaningless.
GATEWAY_SECRET = "gateway-secret-must-never-be-returned"


class _RecordingCache:
	"""Like the shared stub's cache, but remembers the TTL each write was given.

	The TTLs are the contract here — 30 minutes for a positive finding, 60 seconds for a
	negative one, and no write at all for `unknown` — so a cache that silently drops
	`expires_in_sec` would let all three collapse into one and the tests would still pass.
	"""

	def __init__(self):
		self.store = {}
		self.sets = []

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value
		self.sets.append((key, value, expires_in_sec))

	def delete_value(self, key):
		self.store.pop(key, None)


def _load(*, attachments=1, max_upload_mb=25, api_key="", client_id="", app_id="",
          restrict=0, allowed=(), rows=None, files=None,
          gateway_url="https://triton.example.com"):
	"""Import triton_chat + triton_attachments against stubs shaped for these settings."""
	frappe = install_stubs()
	sys.modules.pop("erpnext_enhancements.triton_chat", None)
	sys.modules.pop("erpnext_enhancements.triton_attachments", None)

	# The shared stub carries only `cint` and `get_fullname`; `triton_attachments` also
	# imports the three date helpers at module scope. Added here rather than in
	# `install_stubs` so the other two Triton suites keep the surface they were written
	# against — a stub that grows for one suite is a stub every suite silently depends on.
	#
	# `now_datetime` returns a real datetime, as the framework's does: `google_link_status`
	# calls `.isoformat()` on it, and a str stand-in would have hidden that.
	# `frappe.throw` really does queue a message before it raises, and `frappe.flags`
	# really does gate that. The shared stub has neither, which is exactly why a probe that
	# swallowed a throw and still shipped `_server_messages` passed a green suite — the
	# stub's plain Exception is the one kind of failure with no side effect to leak.
	frappe.flags = types.SimpleNamespace(mute_messages=None)
	frappe.message_log = []

	def throw(message=None, *a, **k):
		if not getattr(frappe.flags, "mute_messages", None):
			frappe.message_log.append(message)
		raise Exception(message if isinstance(message, str) else "frappe.throw")

	frappe.throw = throw

	utils = sys.modules["frappe.utils"]
	utils.add_days = lambda date, days: date
	utils.now_datetime = lambda: datetime.datetime(2026, 9, 8, 12, 0, 0)
	utils.nowdate = lambda: "2026-09-08"

	behavior = _Doc(
		enabled=1,
		default_model="",
		request_timeout=120,
		enable_page_context=1,
		enable_write_actions=1,
		debug_logging=0,
		restrict_to_whitelist=restrict,
		allowed_users=[types.SimpleNamespace(user=u) for u in allowed],
		enable_attachments=attachments,
		triton_max_upload_mb=max_upload_mb,
		triton_attachment_retention_days=30,
		triton_drive_picker_api_key=api_key,
		triton_drive_picker_client_id=client_id,
		triton_drive_picker_app_id=app_id,
	)
	conn = _Doc(gateway_url=gateway_url, admin_webhook_secret=GATEWAY_SECRET)
	docs = {"Triton Assistant Settings": behavior, "Triton Settings": conn}
	frappe.get_cached_doc = lambda doctype, *a, **k: docs[doctype]

	# The attachment rows this "site" holds, and the File rows they point at.
	rows = list(rows or [])
	files = dict(files or {})

	def get_all(doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		if doctype != "Triton Chat Attachment":
			return []
		wanted = filters.get("name")
		names = set(wanted[1]) if isinstance(wanted, (list, tuple)) else {wanted}
		owner = filters.get("owner")
		return [
			dict(r) for r in rows
			if r["name"] in names and (owner is None or r.get("owner") == owner)
		]

	def get_value(doctype, name, fieldname=None, **kwargs):
		if doctype == "File":
			return files.get(name, {}).get(fieldname)
		return None

	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(
		get_value=get_value,
		set_value=lambda *a, **k: None,
		count=lambda *a, **k: 0,
	)

	from erpnext_enhancements import triton_attachments, triton_chat

	return triton_chat, triton_attachments, frappe


# ---------------------------------------------------------------------------
# The preamble contract
# ---------------------------------------------------------------------------
#: The exact string `_build_prompt` has produced since the feature shipped. Restated as a
#: literal rather than recomputed from the module, so a change to the module's copy fails
#: here instead of agreeing with itself.
PAGE_PREAMBLE = (
	"[ERPNEXT PAGE CONTEXT] The user is currently viewing the following in "
	"ERPNext. Use your ERPNext tools to fetch live details as needed when "
	"they are relevant to the question; do not assume values you have not "
	"fetched:\n- Document: Task / TASK-0001\n\n"
)


def test_page_context_preamble_is_unchanged_when_nothing_is_attached():
	"""The attachment feature must be invisible on every turn that has no attachment."""
	triton_chat, _, _ = _load()

	built = triton_chat._build_prompt(
		"what is this", '[{"type":"document","doctype":"Task","name":"TASK-0001"}]'
	)

	assert built == PAGE_PREAMBLE + "what is this", (
		"the page-context preamble changed. Every ERPNext page turn the assistant has ever "
		"sent uses this exact string; the attachment block is additive or it is wrong"
	)


def test_attachment_block_follows_the_page_context_and_precedes_the_prompt():
	triton_chat, _, frappe = _load(
		rows=[{
			"name": "att1", "owner": "a@sapphirefountains.com", "source": "ERPNext Upload",
			"file": "FILE1", "file_name": "quote.pdf", "content_type": "application/pdf",
			"file_size": 4096, "drive_file_id": None,
		}],
		files={"FILE1": {"file_url": "/private/files/quote.pdf"}},
	)
	frappe.session.user = "a@sapphirefountains.com"

	built = triton_chat._build_prompt(
		"summarise it",
		'[{"type":"document","doctype":"Task","name":"TASK-0001"},'
		'{"type":"file","name":"att1"}]',
	)

	assert built.startswith(PAGE_PREAMBLE)
	assert "[ERPNEXT ATTACHMENTS]" in built
	assert "/private/files/quote.pdf" in built
	assert built.endswith("summarise it")
	assert built.index("[ERPNEXT ATTACHMENTS]") > built.index("[ERPNEXT PAGE CONTEXT]")


def test_a_file_ref_never_renders_as_a_page_line():
	"""Without the `continue` in the loop the else-branch renders "- quote.pdf ()"."""
	triton_chat, _, frappe = _load(
		rows=[{
			"name": "att1", "owner": "a@sapphirefountains.com", "source": "ERPNext Upload",
			"file": "FILE1", "file_name": "quote.pdf", "content_type": "application/pdf",
			"file_size": 10, "drive_file_id": None,
		}],
		files={"FILE1": {"file_url": "/private/files/quote.pdf"}},
	)
	frappe.session.user = "a@sapphirefountains.com"

	built = triton_chat._build_prompt("hi", '[{"type":"file","name":"att1"}]')

	assert "[ERPNEXT PAGE CONTEXT]" not in built, (
		"a file-only context produced an empty page-context preamble"
	)
	assert built.startswith("[ERPNEXT ATTACHMENTS]")


# ---------------------------------------------------------------------------
# The security properties
# ---------------------------------------------------------------------------
def test_an_attachment_you_do_not_own_produces_no_line_and_no_error():
	"""Somebody else's attachment is skipped in silence — a throw would confirm it exists."""
	_, triton_attachments, frappe = _load(
		rows=[{
			"name": "att1", "owner": "b@sapphirefountains.com", "source": "ERPNext Upload",
			"file": "FILE1", "file_name": "payroll.xlsx", "content_type": "x",
			"file_size": 10, "drive_file_id": None,
		}],
		files={"FILE1": {"file_url": "/private/files/payroll.xlsx"}},
	)
	frappe.session.user = "a@sapphirefountains.com"

	block = triton_attachments.describe_attachments_for_prompt([{"type": "file", "name": "att1"}])

	assert block == "", "another user's attachment was described into this user's prompt"


def test_a_client_supplied_file_url_on_the_ref_is_ignored():
	"""The wire carries a NAME. Honouring a ref's own file_url is arbitrary file read."""
	_, triton_attachments, frappe = _load(
		rows=[{
			"name": "att1", "owner": "a@sapphirefountains.com", "source": "ERPNext Upload",
			"file": "FILE1", "file_name": "quote.pdf", "content_type": "application/pdf",
			"file_size": 10, "drive_file_id": None,
		}],
		files={"FILE1": {"file_url": "/private/files/quote.pdf"}},
	)
	frappe.session.user = "a@sapphirefountains.com"

	block = triton_attachments.describe_attachments_for_prompt([
		{"type": "file", "name": "att1", "file_url": "/private/files/site_config.json"}
	])

	assert "/private/files/quote.pdf" in block
	assert "site_config" not in block, (
		"a file_url off the wire reached the prompt; the model would fetch it as the user"
	)


def test_only_the_first_few_attachments_ride_on_a_turn():
	_, triton_attachments, frappe = _load(
		rows=[
			{
				"name": f"att{i}", "owner": "a@sapphirefountains.com",
				"source": "ERPNext Upload", "file": f"FILE{i}", "file_name": f"f{i}.pdf",
				"content_type": "application/pdf", "file_size": 10, "drive_file_id": None,
			}
			for i in range(10)
		],
		files={f"FILE{i}": {"file_url": f"/private/files/f{i}.pdf"} for i in range(10)},
	)
	frappe.session.user = "a@sapphirefountains.com"

	block = triton_attachments.describe_attachments_for_prompt(
		[{"type": "file", "name": f"att{i}"} for i in range(10)]
	)

	assert block.count("\n- ") == triton_attachments.MAX_ATTACHMENTS_PER_TURN


def test_drive_url_must_be_https_on_a_google_host():
	_, triton_attachments, _ = _load()
	safe = triton_attachments._safe_drive_url

	assert safe("https://drive.google.com/file/d/1AbC/view") == "https://drive.google.com/file/d/1AbC/view"
	assert safe("https://docs.google.com/document/d/1AbC/edit").startswith("https://docs.google.com/")
	# Everything else stores nothing rather than throwing: the chip loses its hyperlink, the
	# model keeps the file id it actually needs.
	assert safe("http://drive.google.com/file/d/1AbC/view") == ""
	assert safe("https://drive.google.com.evil.example/x") == ""
	assert safe("javascript:alert(1)") == ""
	assert safe("https://user:pw@drive.google.com/x") == ""
	assert safe(None) == ""


# ---------------------------------------------------------------------------
# The Single-default trap
# ---------------------------------------------------------------------------
def test_an_unset_upload_cap_means_the_default_not_zero():
	"""The v1.277.3 shape. An un-backfilled Int reads None, cint(None) is 0, and a 0 MB cap
	rejects every upload silently on production only."""
	_, triton_attachments, _ = _load(max_upload_mb=None)

	assert triton_attachments.effective_max_upload_bytes() == (
		triton_attachments.DEFAULT_MAX_UPLOAD_MB * 1024 * 1024
	), "a Single field with no stored row turned the declared cap into a 0-byte cap"


def test_a_configured_cap_is_honoured():
	_, triton_attachments, _ = _load(max_upload_mb=5)

	assert triton_attachments.effective_max_upload_bytes() == 5 * 1024 * 1024


def test_extension_allowlist_is_derived_from_the_name_not_the_declared_type():
	_, triton_attachments, _ = _load()

	assert triton_attachments._extension_of("Quote FINAL.PDF") == ".pdf"
	assert ".pdf" in triton_attachments.ALLOWED_EXTENSIONS
	for blocked in (".exe", ".sh", ".js", ".zip", ".html", ""):
		assert blocked not in triton_attachments.ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------
def test_picker_credentials_are_not_served_to_a_user_the_whitelist_excludes():
	triton_chat, _, frappe = _load(
		api_key="AIza-browser-key", client_id="123.apps.googleusercontent.com",
		restrict=1, allowed=["someone.else@sapphirefountains.com"],
	)
	frappe.session.user = "outsider@sapphirefountains.com"

	cfg = triton_chat.get_config()

	assert cfg["enabled"] is False
	assert "attachments" not in cfg, (
		"the browser API key was handed to a user the whitelist exists to exclude"
	)


def test_picker_block_needs_both_credentials():
	triton_chat, _, frappe = _load(api_key="AIza-browser-key", client_id="")
	frappe.session.user = "a@sapphirefountains.com"

	cfg = triton_chat.get_config()

	assert cfg["attachments"]["enabled"] is True
	assert "drive_picker" not in cfg["attachments"], (
		"a half-configured picker opens and then fails inside Google's iframe, which is "
		"indistinguishable from a broken feature"
	)


def test_the_picker_credentials_reach_an_allowed_browser():
	"""The positive half. Without it the negative test below passes vacuously.

	`get_config` gates the block on all THREE of api key, client id and app id, so a suite
	that never sets `app_id` can never see a drive_picker block — and would stay green if
	the emission were dropped entirely, taking the Drive button and the empty state with it.
	"""
	triton_chat, _, frappe = _load(
		api_key="AIza-browser-key", client_id="cid.apps.googleusercontent.com", app_id="497321"
	)
	frappe.session.user = "a@sapphirefountains.com"

	picker = triton_chat.get_config()["attachments"]["drive_picker"]

	assert picker == {
		"api_key": "AIza-browser-key",
		"client_id": "cid.apps.googleusercontent.com",
		"app_id": "497321",
	}


def test_attachments_are_absent_from_config_when_the_switch_is_off():
	triton_chat, _, frappe = _load(attachments=0)
	frappe.session.user = "a@sapphirefountains.com"

	cfg = triton_chat.get_config()

	assert cfg["enabled"] is True
	assert "attachments" not in cfg


def test_get_config_makes_no_network_call_for_the_google_link():
	"""The probe is deliberately NOT folded into get_config, which every page load calls.

	Asserting the absence of a REQUEST rather than the absence of a key: a key name is a
	spelling, and the cost this pins is the round trip. Folding the probe in under any
	other name, or calling it to warm the cache and discarding the result, costs every
	user a bridge mint plus a Triton Drive call on every Desk page load.
	"""
	triton_chat, triton_attachments, frappe = _load()
	frappe.session.user = "a@sapphirefountains.com"

	def forbidden(*a, **k):
		raise AssertionError("get_config made a network call")

	triton_attachments.requests.request = forbidden
	sys.modules["requests"].request = forbidden
	sys.modules["requests"].post = forbidden

	cfg = triton_chat.get_config()

	assert "google_link" not in cfg.get("attachments", {})


# ---------------------------------------------------------------------------
# The Google link probe
# ---------------------------------------------------------------------------
def _probe(statuses, *, raises=False, gateway_url="https://triton.example.com"):
	"""Load the module with a fake Triton that answers `statuses` in order.

	Returns (module, cache, calls). `calls` records every outbound request, which is how the
	retry and the cache-hit tests tell "asked again" from "answered from memory".
	"""
	_, triton_attachments, frappe = _load(gateway_url=gateway_url)

	cache = _RecordingCache()
	frappe.cache = lambda: cache

	remaining = list(statuses)
	calls = []

	def fake_request(method, url, headers=None, timeout=None, **kwargs):
		calls.append({
			"method": method, "url": url, "headers": dict(headers or {}), "timeout": timeout,
		})
		if raises:
			raise Exception("connection reset")
		status = remaining.pop(0) if remaining else 500
		return types.SimpleNamespace(status_code=status, text="", content=b"")

	# `install_stubs` re-creates these on every `_load`, so assigning them here does not leak
	# into the next test.
	triton_attachments.requests.request = fake_request
	triton_attachments.mint_user_token = (
		lambda force_refresh=False: "tok-2" if force_refresh else "tok-1"
	)
	return triton_attachments, cache, calls


def test_a_200_from_the_drive_probe_means_connected():
	mod, _, calls = _probe([200])

	result = mod.google_link_status()

	assert result["state"] == "connected"
	assert calls[0]["method"] == "GET"
	assert calls[0]["url"] == "https://triton.example.com/api/v1/integrations/google/drive?limit=1"
	assert calls[0]["headers"]["Authorization"] == "Bearer tok-1"
	# Panel-open latency, not the 120s chat timeout.
	assert calls[0]["timeout"] == mod._LINK_PROBE_TIMEOUT
	assert max(mod._LINK_PROBE_TIMEOUT) <= 15, (
		"the probe runs on panel open; a long timeout hangs the widget on a hint"
	)


def test_a_400_is_the_only_status_that_means_disconnected():
	assert _probe([400])[0].google_link_status()["state"] == "disconnected"


def test_a_500_is_unknown_and_not_disconnected():
	"""The expensive failure direction. Triton's generic handler answers 500 for a revoked
	credential AND for every real outage; reading that as "not connected" would show the
	whole company a Connect Google button during an incident."""
	assert _probe([500])[0].google_link_status()["state"] == "unknown"


def test_a_429_or_404_is_unknown():
	for status in (404, 429, 502, 0):
		assert _probe([status])[0].google_link_status()["state"] == "unknown", status


def test_an_unreachable_triton_is_unknown_and_does_not_raise():
	"""A probe that throws takes the panel down with it."""
	mod, _, _ = _probe([200], raises=True)
	frappe = sys.modules["frappe"]

	assert mod.google_link_status()["state"] == "unknown"
	assert frappe.message_log == [], (
		"unknown has to be SILENT, not merely non-fatal: a message queued by a swallowed "
		"throw rides out on the 200 as _server_messages and the Desk renders it as a red "
		"modal with no error to attach it to"
	)


def test_a_gateway_that_cannot_mint_a_token_is_unknown():
	mod, _, calls = _probe([200])
	frappe = sys.modules["frappe"]

	def boom(force_refresh=False):
		# `frappe.throw`, not a bare Exception — that is how the real mint_user_token
		# reports every one of its failures, and the difference is the whole finding: a
		# plain Exception is the one kind that queues no message, so a suite written
		# against it cannot see a message leaking.
		frappe.throw("Could not reach Triton: connection refused")

	mod.mint_user_token = boom

	assert mod.google_link_status()["state"] == "unknown"
	assert calls == [], "the probe called Triton with no token"
	assert frappe.message_log == [], (
		"a bridge that will not mint is a silent unknown; queuing the throw's message "
		"turns a Triton outage into a red modal on every Desk page load"
	)


def test_the_probe_re_mints_on_403_not_only_401():
	"""Triton's get_current_user answers 403 for a stale JWT — only a wholly absent
	Authorization header gives 401. A retry-on-401 helper never refreshes a stale token."""
	mod, _, calls = _probe([403, 200])

	assert mod.google_link_status()["state"] == "connected"
	assert len(calls) == 2
	assert calls[0]["headers"]["Authorization"] == "Bearer tok-1"
	assert calls[1]["headers"]["Authorization"] == "Bearer tok-2"


def test_a_persistent_403_gives_up_after_one_retry():
	mod, _, calls = _probe([403, 403])

	assert mod.google_link_status()["state"] == "unknown"
	assert len(calls) == 2


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
def test_connected_is_cached_and_the_second_call_asks_nobody():
	mod, cache, calls = _probe([200])

	first = mod.google_link_status()
	second = mod.google_link_status()

	assert (first["state"], second["state"]) == ("connected", "connected")
	assert len(calls) == 1
	assert cache.sets[0][2] == 1800
	# The finding's own timestamp survives, so "checked_at" answers when we last found out
	# rather than when this request happened to run.
	assert second["checked_at"] == first["checked_at"]


def test_disconnected_expires_far_sooner_than_connected():
	"""There is no callback from Triton when somebody completes OAuth, so a long negative TTL
	locks a user out of the feature they just fixed."""
	# One at a time, and each one used before the next is loaded: `_probe` reinstalls the
	# frappe stub, so a second load rebinds `frappe.cache` out from under the first module.
	mod, connected_cache, _ = _probe([200])
	mod.google_link_status()
	connected_ttl = connected_cache.sets[0][2]

	mod, disconnected_cache, _ = _probe([400])
	mod.google_link_status()
	disconnected_ttl = disconnected_cache.sets[0][2]

	assert disconnected_ttl < connected_ttl
	assert disconnected_ttl <= 120


def test_unknown_is_never_cached():
	"""Caching "we could not tell" turns a thirty-second blip into thirty minutes of a wrong
	hint — and the second call must re-probe rather than serve the shrug."""
	mod, cache, calls = _probe([500, 200])

	assert mod.google_link_status()["state"] == "unknown"
	assert cache.sets == [], "an unknown finding was written to the cache"

	assert mod.google_link_status()["state"] == "connected"
	assert len(calls) == 2


def test_refresh_bypasses_the_cache():
	mod, _, calls = _probe([400, 200])

	assert mod.google_link_status()["state"] == "disconnected"
	assert mod.google_link_status()["state"] == "disconnected"  # served from cache
	assert len(calls) == 1

	# The "I've connected" affordance. Without this the user waits out the TTL.
	assert mod.google_link_status(refresh=1)["state"] == "connected"
	assert len(calls) == 2


def test_the_cache_key_is_per_user():
	mod, cache, calls = _probe([200, 400])
	frappe = sys.modules["frappe"]

	frappe.session.user = "a@sapphirefountains.com"
	assert mod.google_link_status()["state"] == "connected"

	frappe.session.user = "b@sapphirefountains.com"
	assert mod.google_link_status()["state"] == "disconnected", (
		"one user's Google finding was served to another"
	)
	assert len(calls) == 2
	assert len({key for key, _v, _ttl in cache.sets}) == 2


def test_a_corrupt_cache_entry_is_ignored_rather_than_returned():
	mod, cache, calls = _probe([200])
	cache.store[mod._link_cache_key("a@sapphirefountains.com")] = "connected"  # old shape

	assert mod.google_link_status()["state"] == "connected"
	assert len(calls) == 1


# ---------------------------------------------------------------------------
# connect_url
# ---------------------------------------------------------------------------
def test_connect_url_is_derived_from_the_gateway_url():
	mod, _, _ = _probe([400], gateway_url="https://triton.example.com/")

	result = mod.google_link_status()

	assert result["connect_url"] == "https://triton.example.com/api/v1/auth/google/login"


def test_connect_url_is_empty_when_the_gateway_is_unconfigured():
	"""And the probe must not fire at all — there is nowhere to send it."""
	mod, _, calls = _probe([200], gateway_url="")

	result = mod.google_link_status()

	assert result["connect_url"] == ""
	assert result["state"] == "unknown"
	assert calls == []


def test_the_gateway_secret_never_appears_in_the_status():
	"""The secret lives on the same settings dict connect_url is built from."""
	for statuses in ([200], [400], [500]):
		mod, _, _ = _probe(statuses)
		result = mod.google_link_status()
		blob = repr(sorted(result.items()))
		assert GATEWAY_SECRET not in blob, (
			f"the Gateway Secret reached the browser via google_link_status ({statuses})"
		)
		assert set(result) == {"state", "connect_url", "checked_at"}
		assert result["state"] in ("connected", "disconnected", "unknown")

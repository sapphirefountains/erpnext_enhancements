"""The Triton widget defaults to Auto, and every model id it names is one Triton serves.

Two silent failures live here, and neither one raises.

**Auto is the empty string, which is falsy.** ``get_settings()`` resolved the
widget's default with

    behavior.default_model or conn_model or _DEFAULT_MODEL

so an operator who set *Default Model* to blank meaning "Auto" got the opposite:
the value fell through to ``Triton Settings.chat_model_id``, which is set on
every real site. There was no way to express Auto, and the widget always opened
on a pinned model. Any future `or`-chain over this value reintroduces it
silently, because the symptom is not an error — it is the picker quietly showing
Flash.

**A model id outlives its model.** Google retires models, Triton's catalog
follows, and this app keeps two copies of an id that nothing updates: the
fallback list in ``triton_chat.py`` and whatever is typed into *Default Model*.
On 2026-11-11 Triton (0.83.0) dropped ``gemini-3.1-pro-preview`` and
``gemini-3.5-flash``, and the fallback went on offering the first as "Pro". The
second half of this file pins a copy of the catalog so that cannot pass a
build again.

Pure Python: reuses the frappe/requests/werkzeug stubs from
``test_triton_personas`` so ``triton_chat`` imports with no Frappe site.
"""
import ast
import pathlib
import sys

from erpnext_enhancements.tests.test_triton_personas import install_stubs

# ---------------------------------------------------------------------------
# A PINNED COPY of the Gemini half of Triton's model catalog, in its order.
#
# The truth lives in the Triton repository (sapphirefountains/triton):
# `backend/app/core/model_catalog.py`, the `MODELS` entries whose provider is
# Google, sorted by `order` -- which is what `GET /api/v1/assistant/models`
# returns and so what the widget's live list is built from. The ids are also in
# its `docs/configuration.md`.
#
# Copied 2026-11-11 from Triton 0.86.0. When Triton's catalog changes, change
# this tuple and `TRITON_FALLBACK_MODEL_IDS` in `triton_chat.py` together. Do
# not correct an id here from memory: these names are newer than most memories.
# ---------------------------------------------------------------------------
TRITON_GEMINI_CATALOG = (
	"gemini-4-argon",
	"gemini-3.8-flash",
	"gemini-3.5-flash-lite",
)

# Triton's realtime voice model: `LIVE_MODEL` and `VOICE_LIVE_MODEL` in its
# `backend/app/core/config.py`. Same date, same rule.
TRITON_LIVE_MODEL = "gemini-3.8-live"

# Ids Triton has dropped or that Google is retiring. Named so the failure
# message can say which one came back, not to stand in for the catalog check.
DROPPED_FROM_TRITON = ("gemini-3.1-pro-preview", "gemini-3.5-flash")
RETIRING_LIVE_MODEL = "gemini-live-2.5-flash-native-audio"  # Google: 2026-12-13

_APP = pathlib.Path(__file__).resolve().parents[1]


class _Doc:
	"""Stands in for a cached single DocType: attribute *and* .get() access."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def get_password(self, key, raise_exception=True):
		return self.__dict__.get(key)


def _load(monkeypatch, *, default_model=None, chat_model_id="gemini-3.8-flash", debug=0):
	"""Import triton_chat with the two settings DocTypes stubbed.

	`chat_model_id` defaults to a pinned model deliberately: that is the state of
	every real site, and it is what used to hijack the widget's default.
	"""
	# `install_stubs` builds a new, empty cache on every call, which the
	# model-list tests below rely on: each one starts with nothing cached.
	frappe = install_stubs()
	sys.modules.pop("erpnext_enhancements.triton_chat", None)

	behavior = _Doc(
		enabled=1,
		default_model=default_model,
		request_timeout=120,
		enable_page_context=1,
		enable_write_actions=1,
		debug_logging=debug,
		restrict_to_whitelist=0,
		allowed_users=[],
	)
	conn = _Doc(
		gateway_url="https://triton.example.com",
		admin_webhook_secret="s3cret",
		chat_model_id=chat_model_id,
	)
	docs = {"Triton Assistant Settings": behavior, "Triton Settings": conn}
	frappe.get_cached_doc = lambda doctype, *a, **k: docs[doctype]

	from erpnext_enhancements import triton_chat

	return triton_chat


def _spy_on_triton(monkeypatch, triton_chat, *, live_ids=None, models_raises=False):
	"""Replace ``_request`` and return the list of calls it received.

	`GET /assistant/models` answers with `live_ids` (or raises, standing in for
	Triton being unreachable); everything else answers like a created session.
	"""
	calls = []

	def fake_request(method, path, payload=None):
		calls.append((method, path, payload))
		if path == "/api/v1/assistant/models":
			if models_raises:
				raise Exception("Triton unreachable")
			return list(TRITON_GEMINI_CATALOG) if live_ids is None else live_ids
		return {"id": 1}

	monkeypatch.setattr(triton_chat, "_request", fake_request)
	return calls


def _session_posts(calls):
	return [c for c in calls if c[0] == "POST" and c[1] == "/api/v1/assistant/sessions"]


# ---------------------------------------------------------------------------
# Auto is the default, and reachable
# ---------------------------------------------------------------------------
def test_unset_default_model_means_auto_not_the_connection_model(monkeypatch):
	"""The regression, stated exactly as it happened.

	`chat_model_id` is pinned, `default_model` is blank — which is how this site
	is actually configured — and the widget must still open on Auto.
	"""
	triton_chat = _load(monkeypatch, default_model=None)

	assert triton_chat.get_settings()["default_model"] == "", (
		"the widget default fell through to Triton Settings.chat_model_id again; "
		"Auto is the empty string, so any `or` chain over this value swallows it"
	)


def test_blank_string_also_means_auto(monkeypatch):
	"""A Data field cleared in the UI stores "" rather than None."""
	triton_chat = _load(monkeypatch, default_model="")

	assert triton_chat.get_settings()["default_model"] == ""


def test_an_explicit_pin_still_wins(monkeypatch):
	"""Auto is the default, not a mandate — the field still pins the picker."""
	triton_chat = _load(monkeypatch, default_model="gemini-3.8-flash")

	assert triton_chat.get_settings()["default_model"] == "gemini-3.8-flash"


def test_the_code_default_is_auto(monkeypatch):
	"""Auto is the one default that cannot go stale, and it is already the model
	Triton's owner chose: an Auto turn is answered by Triton's default chat
	model. A pinned id here would be wrong again at the next retirement."""
	triton_chat = _load(monkeypatch)

	assert triton_chat._DEFAULT_MODEL == ""


def test_auto_is_the_first_offered_choice(monkeypatch):
	"""The widget's last-resort fallback is `models[0]`, so Auto must lead the
	curated list — otherwise "no default" silently means whatever is first."""
	triton_chat = _load(monkeypatch)

	assert triton_chat.TRITON_MODELS[0]["value"] == ""
	assert triton_chat.TRITON_MODELS[0]["label"] == "Auto"


def test_the_live_model_list_also_leads_with_auto(monkeypatch):
	"""`_models_from_ids` builds the picker from Triton's live ids; Auto is
	prepended there too, and the widget relies on it."""
	triton_chat = _load(monkeypatch)

	models = triton_chat._models_from_ids(["gemini-3.8-flash", "gemini-4-argon"])
	assert models[0] == {"value": "", "label": "Auto"}


# ---------------------------------------------------------------------------
# The fallback list against the pinned catalog
# ---------------------------------------------------------------------------
def _fallback_ids(triton_chat):
	return [m["value"] for m in triton_chat.TRITON_MODELS if m["value"]]


def test_every_fallback_id_is_in_triton_s_catalog(monkeypatch):
	"""THE guard. An id here that Triton does not serve is offered to every user
	whenever the live list cannot be fetched, and painted first on every load."""
	triton_chat = _load(monkeypatch)

	strangers = [mid for mid in _fallback_ids(triton_chat) if mid not in TRITON_GEMINI_CATALOG]
	assert not strangers, (
		f"the widget's fallback model list offers {strangers}, which Triton's catalog does "
		"not carry. Read Triton's backend/app/core/model_catalog.py, then update "
		"TRITON_FALLBACK_MODEL_IDS in triton_chat.py and TRITON_GEMINI_CATALOG in this file "
		"together."
	)


def test_the_fallback_is_the_catalog_in_the_catalog_s_order(monkeypatch):
	"""Same models, same order as the live list, so the picker does not reshuffle
	when the live list replaces the fallback a moment after the panel paints."""
	triton_chat = _load(monkeypatch)

	assert _fallback_ids(triton_chat) == list(TRITON_GEMINI_CATALOG)
	assert tuple(triton_chat.TRITON_FALLBACK_MODEL_IDS) == TRITON_GEMINI_CATALOG


def test_the_fallback_carries_no_dropped_id(monkeypatch):
	"""The two ids Triton dropped in 0.83.0. The fallback offered the first of
	them, labeled "Pro", until v1.801.1."""
	triton_chat = _load(monkeypatch)

	for dropped in DROPPED_FROM_TRITON:
		assert dropped not in _fallback_ids(triton_chat), dropped


def test_fallback_labels_are_the_live_list_s_labels(monkeypatch):
	"""One labeling rule, not two. The fallback used to say "Flash" where the
	live list says "Flash 3.8", so the label changed under the user's eyes."""
	triton_chat = _load(monkeypatch)

	live = triton_chat._models_from_ids(list(TRITON_GEMINI_CATALOG))
	assert triton_chat.TRITON_MODELS == live
	assert [m["label"] for m in live] == ["Auto", "Argon 4", "Flash 3.8", "Flash Lite 3.5"]


def test_list_models_serves_the_fallback_when_triton_is_unreachable(monkeypatch):
	triton_chat = _load(monkeypatch)
	_spy_on_triton(monkeypatch, triton_chat, models_raises=True)

	assert triton_chat.list_models() == triton_chat.TRITON_MODELS


def test_list_models_serves_triton_s_list_when_it_answers(monkeypatch):
	"""The live list wins, including a model this app has never heard of."""
	triton_chat = _load(monkeypatch)
	_spy_on_triton(monkeypatch, triton_chat, live_ids=["gemini-4-argon", "gemini-9-newer"])

	assert [m["value"] for m in triton_chat.list_models()] == ["", "gemini-4-argon", "gemini-9-newer"]


def test_a_non_list_answer_is_not_offered_as_models(monkeypatch):
	"""A dict iterates as its keys, which are strings: an error body that came
	back with a 200 used to become a picker full of "detail" and "status"."""
	triton_chat = _load(monkeypatch)
	_spy_on_triton(monkeypatch, triton_chat, live_ids={"detail": "nope", "status": "error"})

	assert triton_chat._models_from_ids({"detail": "nope"}) == [{"value": "", "label": "Auto"}]
	assert triton_chat.list_models() == triton_chat.TRITON_MODELS


# ---------------------------------------------------------------------------
# A stored Default Model that Triton no longer lists
# ---------------------------------------------------------------------------
def test_an_empty_default_starts_on_auto_without_asking_triton_for_its_list(monkeypatch):
	"""Production's state (the field is empty). Nothing extra may be spent on it."""
	triton_chat = _load(monkeypatch, default_model=None)
	calls = _spy_on_triton(monkeypatch, triton_chat)

	triton_chat.start_session(model="")

	assert [c[1] for c in calls] == ["/api/v1/assistant/sessions"]
	assert calls[0][2]["model_name"] == ""


def test_a_stale_default_model_starts_the_chat_on_auto(monkeypatch):
	"""A `gemini-*` id Triton dropped. Triton would have read it as Auto itself,
	and written a warning for every new chat; it no longer hears of it."""
	triton_chat = _load(monkeypatch, default_model="gemini-3.1-pro-preview")
	calls = _spy_on_triton(monkeypatch, triton_chat)

	# The widget sends "" when its picker is on Auto, and it is on Auto because
	# the stale default is not in its list.
	triton_chat.start_session(model="")

	assert _session_posts(calls)[0][2]["model_name"] == ""


def test_an_unlisted_default_of_any_spelling_starts_on_auto(monkeypatch):
	"""The case that used to take the chat down for everybody: an id that is not
	`gemini-*` and not in the catalog is a 422 from Triton, so no session."""
	for typed in ("Flash", "gemini3-flash", "claude-opus-4-7", "  gemini-3.5-flash  "):
		triton_chat = _load(monkeypatch, default_model=typed)
		calls = _spy_on_triton(monkeypatch, triton_chat)

		triton_chat.start_session(model="")

		assert _session_posts(calls)[0][2]["model_name"] == "", typed


def test_the_widget_s_own_fallback_to_the_stale_default_is_caught_too(monkeypatch):
	"""`ensureSession` in the widget sends `state.model || config.default_model`,
	so with the picker on Auto the stale id arrives as an explicit `model`."""
	triton_chat = _load(monkeypatch, default_model="gemini-3.1-pro-preview")
	calls = _spy_on_triton(monkeypatch, triton_chat)

	triton_chat.start_session(model="gemini-3.1-pro-preview")

	assert _session_posts(calls)[0][2]["model_name"] == ""


def test_a_listed_default_model_is_still_sent(monkeypatch):
	"""Tolerating a stale pin must not cost anybody a live one."""
	triton_chat = _load(monkeypatch, default_model="gemini-3.8-flash")
	calls = _spy_on_triton(monkeypatch, triton_chat)

	triton_chat.start_session(model="")

	assert _session_posts(calls)[0][2]["model_name"] == "gemini-3.8-flash"


def test_a_listed_pick_from_the_picker_is_still_sent(monkeypatch):
	triton_chat = _load(monkeypatch, default_model=None)
	calls = _spy_on_triton(monkeypatch, triton_chat)

	triton_chat.start_session(model="gemini-3.5-flash-lite")

	assert _session_posts(calls)[0][2]["model_name"] == "gemini-3.5-flash-lite"


def test_a_model_triton_added_since_this_release_can_be_pinned(monkeypatch):
	"""Measured against Triton's live list, not this app's copy of it: a model
	Triton starts serving tomorrow needs no release here to be the default."""
	triton_chat = _load(monkeypatch, default_model="gemini-9-newer")
	calls = _spy_on_triton(monkeypatch, triton_chat, live_ids=["gemini-4-argon", "gemini-9-newer"])

	triton_chat.start_session(model="")

	assert _session_posts(calls)[0][2]["model_name"] == "gemini-9-newer"


def test_with_triton_s_list_unreachable_the_fallback_is_the_judge(monkeypatch):
	triton_chat = _load(monkeypatch, default_model="gemini-3.1-pro-preview")
	calls = _spy_on_triton(monkeypatch, triton_chat, models_raises=True)
	triton_chat.start_session(model="")
	assert _session_posts(calls)[0][2]["model_name"] == ""

	triton_chat = _load(monkeypatch, default_model="gemini-4-argon")
	calls = _spy_on_triton(monkeypatch, triton_chat, models_raises=True)
	triton_chat.start_session(model="")
	assert _session_posts(calls)[0][2]["model_name"] == "gemini-4-argon"


def test_get_config_still_reports_the_stored_default(monkeypatch):
	"""`get_config` runs on every page load and must not wait on Triton, so it
	hands the widget the stored value as it is. The widget only selects it when
	it is in the list (`applyModels`), and `start_session` judges what is sent."""
	triton_chat = _load(monkeypatch, default_model="gemini-3.1-pro-preview")
	calls = _spy_on_triton(monkeypatch, triton_chat)

	cfg = triton_chat.get_config()

	assert cfg["default_model"] == "gemini-3.1-pro-preview"
	assert cfg["models"] == triton_chat.TRITON_MODELS
	assert calls == [], "get_config called Triton; it is on the page-load path"


# ---------------------------------------------------------------------------
# The realtime voice default in api/telephony.py
# ---------------------------------------------------------------------------
# Read as source, not imported: `telephony` imports twilio at its top, and the
# point is the literal. AST string constants, so the comment that explains the
# retirement may name the old id while no code does.
def _telephony_tree():
	return ast.parse((_APP / "api" / "telephony.py").read_text(encoding="utf-8"))


def test_telephony_names_triton_s_live_model():
	assigned = {
		node.targets[0].id: node.value.value
		for node in _telephony_tree().body
		if isinstance(node, ast.Assign)
		and len(node.targets) == 1
		and isinstance(node.targets[0], ast.Name)
		and isinstance(node.value, ast.Constant)
	}

	assert assigned.get("_DEFAULT_VOICE_MODEL_ID") == TRITON_LIVE_MODEL


def test_telephony_no_longer_hands_out_the_retiring_live_model():
	"""`gemini-live-2.5-flash-native-audio` stops existing on 2026-12-13."""
	literals = [
		node.value
		for node in ast.walk(_telephony_tree())
		if isinstance(node, ast.Constant) and isinstance(node.value, str)
	]

	assert RETIRING_LIVE_MODEL not in literals
	# Exactly one model id in the file's code, and it is the constant's value.
	assert [s for s in literals if s.startswith("gemini-")] == [TRITON_LIVE_MODEL], (
		"api/telephony.py names a model id somewhere other than _DEFAULT_VOICE_MODEL_ID"
	)

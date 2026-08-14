"""The Triton widget defaults to Auto, and Auto is reachable at all.

Auto is spelled as the **empty string**, which is falsy — and that is the whole
bug this guards. ``get_settings()`` resolved the widget's default with

    behavior.default_model or conn_model or _DEFAULT_MODEL

so an operator who set *Default Model* to blank meaning "Auto" got the opposite:
the value fell through to ``Triton Settings.chat_model_id``, which is set on
every real site. There was no way to express Auto, and the widget always opened
on a pinned model.

Any future `or`-chain over this value reintroduces it silently, because the
symptom is not an error — it is the picker quietly showing Flash.

Pure Python: reuses the frappe/requests/werkzeug stubs from
``test_triton_personas`` so ``triton_chat`` imports with no Frappe site.
"""
import sys
import types

from erpnext_enhancements.tests.test_triton_personas import install_stubs


class _Doc:
	"""Stands in for a cached single DocType: attribute *and* .get() access."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def get_password(self, key, raise_exception=True):
		return self.__dict__.get(key)


def _load(monkeypatch, *, default_model=None, chat_model_id="gemini-3.5-flash"):
	"""Import triton_chat with the two settings DocTypes stubbed.

	`chat_model_id` defaults to a pinned model deliberately: that is the state of
	every real site, and it is what used to hijack the widget's default.
	"""
	frappe = install_stubs()
	sys.modules.pop("erpnext_enhancements.triton_chat", None)

	behavior = _Doc(
		enabled=1,
		default_model=default_model,
		request_timeout=120,
		enable_page_context=1,
		enable_write_actions=1,
		debug_logging=0,
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
	triton_chat = _load(monkeypatch, default_model="gemini-3.1-pro-preview")

	assert triton_chat.get_settings()["default_model"] == "gemini-3.1-pro-preview"


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

	models = triton_chat._models_from_ids(["gemini-3.5-flash", "gemini-3.1-pro-preview"])
	assert models[0] == {"value": "", "label": "Auto"}

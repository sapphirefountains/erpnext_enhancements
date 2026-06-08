"""
Server-side proxy between the embedded ERPNext Triton widget and the Triton API.

The browser widget only ever calls these whitelisted methods on its own origin,
so there is no CORS to open and no Triton credential in the browser. This module:

  1. Mints a short-lived, per-user Triton JWT by exchanging the logged-in
     ERPNext user's email at Triton's identity bridge, authenticating with the
     shared Gateway Secret (held only here, server-side). Tokens are cached per
     user until shortly before they expire.
  2. Forwards chat/session calls to Triton with that per-user token, so every
     conversation is attributed to the right person and reuses the same Triton
     ChatSession store as the Triton web app (shared history).
  3. Relays Triton's Server-Sent Events stream straight back to the browser via
     a streaming werkzeug Response, so the live token-by-token UX is preserved.

Configuration lives in the "Triton Settings" single DocType.
"""
from __future__ import annotations

import json

import frappe
import requests
from frappe import _
from frappe.utils import cint
from werkzeug.wrappers import Response

# Re-mint a little before the token actually expires so an in-flight request
# never races the expiry.
_TOKEN_REFRESH_MARGIN_SEC = 120

# Default model new chats open with when nothing is configured.
_DEFAULT_MODEL = "gemini-3.5-flash"

# Curated model choices shown in the widget's header picker. Values are the
# Gemini model ids Triton routes to (see backend app/core/model_router.py); an
# empty value means "let Triton auto-route per message" based on the prompt.
TRITON_MODELS = [
    {"value": "", "label": "Auto"},
    {"value": "gemini-3.5-flash", "label": "Flash"},
    {"value": "gemini-3.1-pro-preview", "label": "Pro"},
    {"value": "gemini-3.1-flash-lite", "label": "Lite"},
]


# ---------------------------------------------------------------------------
# Settings + auth
# ---------------------------------------------------------------------------
def get_settings() -> dict:
    """Resolved config as a plain dict.

    The Triton *connection* (Gateway URL + Admin Webhook Secret) is owned by the
    shared "Triton Settings" single DocType (from the erpnext_enhancements app,
    also used by the telephony gateway) — we reuse it so the secret lives in one
    place. Widget *behavior* lives in our own "Triton Assistant Settings".
    """
    behavior = frappe.get_cached_doc("Triton Assistant Settings")

    base_url, secret, conn_model = "", None, None
    try:
        conn = frappe.get_cached_doc("Triton Settings")
        base_url = (conn.get("gateway_url") or "").strip().rstrip("/")
        secret = conn.get_password("admin_webhook_secret") if conn.get("admin_webhook_secret") else None
        conn_model = conn.get("chat_model_id")
    except Exception:
        # erpnext_enhancements / Triton Settings not present — assistant stays off.
        pass

    return {
        "enabled": bool(behavior.enabled),
        "base_url": base_url,
        "gateway_secret": secret,
        "default_model": (behavior.default_model or conn_model or _DEFAULT_MODEL),
        "timeout": int(behavior.request_timeout or 120),
        "enable_page_context": bool(behavior.enable_page_context),
        "enable_write_actions": bool(behavior.enable_write_actions),
        "debug": bool(behavior.debug_logging),
        "restrict_to_whitelist": bool(behavior.restrict_to_whitelist),
        # Set of User names (emails) explicitly allowed when the whitelist is on.
        "allowed_users": {
            (row.user or "").strip()
            for row in (behavior.allowed_users or [])
            if (row.user or "").strip()
        },
    }


def user_has_widget_access(settings: dict | None = None) -> bool:
    """Whether the current user may see/use the Triton widget.

    Independent of the master ``enabled`` switch. When the whitelist is off,
    everyone is allowed. When it is on, only the Administrator and the users
    listed in "Allowed Users" are allowed — this is the gate used to roll the
    assistant out to trusted testers before releasing it to everyone.
    """
    if settings is None:
        settings = get_settings()
    if not settings.get("restrict_to_whitelist"):
        return True
    user = frappe.session.user
    if user == "Administrator":
        return True
    return user in settings.get("allowed_users", set())


def _user_email() -> str:
    user = frappe.session.user
    return frappe.db.get_value("User", user, "email") or user


def mint_user_token(force_refresh: bool = False) -> str:
    """Return a Triton JWT for the current ERPNext user, cached per user."""
    user = frappe.session.user
    if user in ("Guest", None):
        frappe.throw(_("You must be logged in to use Triton."), frappe.PermissionError)

    # Whitelist gate (server-side enforcement). Every Triton API call mints a
    # token through here, so a non-whitelisted user cannot reach Triton even by
    # calling the whitelisted methods directly.
    if not user_has_widget_access():
        frappe.throw(_("You do not have access to the Triton assistant."), frappe.PermissionError)

    cache_key = f"triton_user_token::{user}"
    if not force_refresh:
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached

    settings = get_settings()
    if not settings["base_url"]:
        frappe.throw(_("Gateway URL is not set in Triton Settings."))
    if not settings["gateway_secret"]:
        frappe.throw(_("Admin Webhook Secret is not set in Triton Settings."))

    try:
        resp = requests.post(
            f"{settings['base_url']}/api/v1/auth/erpnext-bridge/token",
            json={"email": _user_email(), "full_name": frappe.utils.get_fullname(user)},
            headers={"Authorization": f"Bearer {settings['gateway_secret']}"},
            timeout=15,
        )
    except Exception as e:
        frappe.throw(_("Could not reach Triton: {0}").format(e))

    if resp.status_code != 200:
        if settings["debug"]:
            frappe.log_error(f"Bridge token failed: {resp.status_code} {resp.text[:500]}", "Triton Chat")
        frappe.throw(_("Triton authentication failed ({0}).").format(resp.status_code))

    data = resp.json()
    token = data["access_token"]
    ttl = int(data.get("expires_in", 1800))
    frappe.cache().set_value(cache_key, token, expires_in_sec=max(ttl - _TOKEN_REFRESH_MARGIN_SEC, 60))
    return token


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {mint_user_token()}"}


def _request(method: str, path: str, payload: dict | None = None):
    """Make an authed JSON call to Triton, retrying once on 401 (stale token)."""
    settings = get_settings()
    if not settings["enabled"]:
        frappe.throw(_("Triton Assistant is disabled."))
    url = f"{settings['base_url']}{path}"

    for attempt in range(2):
        headers = _auth_headers()
        headers["Content-Type"] = "application/json"
        try:
            resp = requests.request(method, url, json=payload, headers=headers, timeout=settings["timeout"])
        except Exception as e:
            frappe.throw(_("Could not reach Triton: {0}").format(e))

        if resp.status_code == 401 and attempt == 0:
            mint_user_token(force_refresh=True)
            continue

        if resp.status_code >= 400:
            if settings["debug"]:
                frappe.log_error(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}", "Triton Chat")
            frappe.throw(_("Triton error ({0}).").format(resp.status_code))

        if not resp.content:
            return {}
        return resp.json()


# ---------------------------------------------------------------------------
# Context preamble
# ---------------------------------------------------------------------------
def _build_prompt(prompt: str | None, context: str | None) -> str:
    """Prepend a compact ERPNext-context preamble describing what the user has
    pinned. We send *references* only — Triton fetches live data itself via its
    ERPNext tools — except for unsaved edits, which we pass inline so Triton
    sees what the user is changing right now."""
    prompt = prompt or ""
    if not context:
        return prompt

    try:
        refs = json.loads(context)
    except Exception:
        return prompt
    if not refs:
        return prompt

    lines = []
    for ref in refs:
        rtype = ref.get("type")
        if rtype == "document":
            line = f"- Document: {ref.get('doctype')} / {ref.get('name')}"
            dirty = ref.get("dirty_fields")
            if dirty:
                line += f" (UNSAVED edits in progress: {json.dumps(dirty, default=str)})"
            lines.append(line)
        elif rtype == "list":
            filt = ref.get("filters")
            extra = f" filtered by {json.dumps(filt)}" if filt else ""
            lines.append(f"- List view: {ref.get('doctype')}{extra}")
        elif rtype == "report":
            filt = ref.get("filters")
            extra = f" with filters {json.dumps(filt)}" if filt else ""
            lines.append(f"- Report: {ref.get('report_name') or ref.get('name')}{extra}")
        else:
            lines.append(f"- {ref.get('title') or ref.get('name') or 'page'} ({ref.get('route') or ''})")

    preamble = (
        "[ERPNEXT PAGE CONTEXT] The user is currently viewing the following in "
        "ERPNext. Use your ERPNext tools to fetch live details as needed when "
        "they are relevant to the question; do not assume values you have not "
        "fetched:\n" + "\n".join(lines) + "\n\n"
    )
    return preamble + prompt


# ---------------------------------------------------------------------------
# Whitelisted API (called by the widget)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_config() -> dict:
    """Browser-safe config for the widget. Never returns the gateway secret."""
    try:
        s = get_settings()
    except Exception:
        return {"enabled": False}
    # The widget only builds when `enabled` is truthy, so fold the per-user
    # whitelist gate into it: a non-whitelisted user gets enabled=False and
    # never sees the floating button.
    return {
        "enabled": s["enabled"] and user_has_widget_access(s),
        "enable_page_context": s["enable_page_context"],
        "enable_write_actions": s["enable_write_actions"],
        "default_model": s["default_model"],
        # Fallback model list for instant first paint; the widget refreshes this
        # from Triton's live model endpoint via list_models() once it loads.
        "models": TRITON_MODELS,
        "user": frappe.session.user,
        "full_name": frappe.utils.get_fullname(frappe.session.user),
    }


@frappe.whitelist()
def start_session(title: str | None = None, model: str | None = None) -> dict:
    settings = get_settings()
    return _request("POST", "/api/v1/assistant/sessions", {
        "title": title or "ERPNext Chat",
        "model_name": model or settings["default_model"],
    })


@frappe.whitelist()
def list_sessions() -> list:
    return _request("GET", "/api/v1/assistant/sessions")


def _pretty_model_label(model_id: str) -> str:
    """Turn a raw model id into a short picker label.

    "gemini-3.5-flash" -> "Flash 3.5", "gemini-3.1-pro-preview" -> "Pro 3.1",
    "gemini-3.1-flash-lite" -> "Flash Lite 3.1".
    """
    s = (model_id or "").replace("gemini-", "")
    parts = [p for p in s.split("-") if p]
    if not parts:
        return model_id or "Model"
    version = parts[0]
    tier_words = [w for w in parts[1:] if w.lower() != "preview"]
    tier = " ".join(w.capitalize() for w in tier_words)
    return f"{tier} {version}".strip() if tier else version


def _models_from_ids(ids) -> list:
    """Build the widget's {value,label} option list, with Auto first."""
    models = [{"value": "", "label": "Auto"}]
    for mid in (ids or []):
        if isinstance(mid, str) and mid:
            models.append({"value": mid, "label": _pretty_model_label(mid)})
    return models


@frappe.whitelist()
def list_models() -> list:
    """Live model choices for the widget picker, sourced from Triton so the
    list reflects backend changes globally without manual upkeep here.

    Cached briefly (per-site) to avoid hitting Triton on every page load; falls
    back to the curated TRITON_MODELS when Triton is unreachable.
    """
    cache = frappe.cache()
    ckey = "triton_models_list"
    cached = cache.get_value(ckey)
    if cached:
        return cached
    try:
        ids = _request("GET", "/api/v1/assistant/models")
        models = _models_from_ids(ids)
    except Exception:
        models = None
    if not models or len(models) <= 1:
        # Triton unreachable or returned nothing usable — use the curated list.
        return TRITON_MODELS
    cache.set_value(ckey, models, expires_in_sec=300)
    return models


@frappe.whitelist()
def morning_briefing(force: int | str = 0, cached_only: int | str = 0) -> dict:
    """Proxy to Triton's Morning Briefing for the current user.

    Served from Triton's per-user/day cache (warmed by Triton's 06:30 MT
    scheduled job) when available; generates on demand otherwise unless
    cached_only is set.
    """
    path = (
        "/api/v1/assistant/morning-briefing"
        f"?force={1 if cint(force) else 0}&cached_only={1 if cint(cached_only) else 0}"
    )
    return _request("GET", path)


@frappe.whitelist()
def get_messages(session_id: str, limit: int | None = 50) -> list:
    path = f"/api/v1/assistant/sessions/{cint(session_id)}/messages"
    if limit:
        path += f"?limit={cint(limit)}"
    return _request("GET", path)


@frappe.whitelist()
def delete_session(session_id: str) -> dict:
    return _request("DELETE", f"/api/v1/assistant/sessions/{cint(session_id)}")


@frappe.whitelist()
def confirm_action(action_id: str, session_id: str | None = None) -> dict:
    return _request("POST", f"/api/v1/integrations/actions/{action_id}/confirm", {
        "session_id": cint(session_id) or None,
    })


@frappe.whitelist()
def cancel_action(action_id: str, session_id: str | None = None) -> dict:
    return _request("POST", f"/api/v1/integrations/actions/{action_id}/cancel", {
        "session_id": cint(session_id) or None,
    })


def _sse_error(message: str) -> bytes:
    return f"data: {json.dumps({'type': 'error', 'content': message})}\n\n".encode()


@frappe.whitelist()
def stream_query(session_id: str, prompt: str | None = None, context: str | None = None,
                 hidden: int | str = 0, model: str | None = None):
    """Relay Triton's SSE chat stream back to the browser.

    Returns a streaming werkzeug Response (text/event-stream). Everything the
    generator needs is captured before we hand the Response back, so the lazy
    body never touches Frappe's request/DB context after teardown.
    """
    settings = get_settings()
    if not settings["enabled"]:
        frappe.throw(_("Triton Assistant is disabled."))

    token = mint_user_token()
    base_url = settings["base_url"]
    timeout = settings["timeout"]
    debug = settings["debug"]

    payload: dict = {"prompt": _build_prompt(prompt, context), "hidden": cint(hidden) == 1}
    if model:
        payload["model_name"] = model

    url = f"{base_url}/api/v1/assistant/sessions/{cint(session_id)}/query/stream"

    def generate():
        try:
            with requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
                stream=True,
                timeout=(15, timeout),
            ) as r:
                if r.status_code != 200:
                    body = r.text[:500]
                    yield _sse_error(_("Triton returned {0}.").format(r.status_code))
                    if debug:
                        try:
                            frappe.log_error(f"stream {r.status_code}: {body}", "Triton Chat")
                        except Exception:
                            pass
                    return
                for chunk in r.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
        except Exception as e:
            yield _sse_error(_("Connection error: {0}").format(e))

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

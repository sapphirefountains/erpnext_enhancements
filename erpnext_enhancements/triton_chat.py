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
        "default_model": (behavior.default_model or conn_model or "gemini-2.5-flash"),
        "timeout": int(behavior.request_timeout or 120),
        "enable_page_context": bool(behavior.enable_page_context),
        "enable_write_actions": bool(behavior.enable_write_actions),
        "debug": bool(behavior.debug_logging),
    }


def _user_email() -> str:
    user = frappe.session.user
    return frappe.db.get_value("User", user, "email") or user


def mint_user_token(force_refresh: bool = False) -> str:
    """Return a Triton JWT for the current ERPNext user, cached per user."""
    user = frappe.session.user
    if user in ("Guest", None):
        frappe.throw(_("You must be logged in to use Triton."), frappe.PermissionError)

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
    return {
        "enabled": s["enabled"],
        "enable_page_context": s["enable_page_context"],
        "enable_write_actions": s["enable_write_actions"],
        "default_model": s["default_model"],
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

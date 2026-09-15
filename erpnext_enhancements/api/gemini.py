"""Thin Vertex AI (Gemini) client used by the AI drafting endpoints.

Not whitelisted — internal helper imported by ``api/briefing.py`` (the morning
briefing narrative), ``api/communication.py`` (email/SMS drafts),
``api/training_ai.py`` and ``assistant_tools/draft_course_spec.py``. Posts a
single ``generateContent`` request to the Vertex AI REST endpoint for the
``gemini-3.1-pro-preview`` model in the ``sapphire-fountains-poseidon`` GCP
project (``us-central1``) and splits the response into final answer text vs.
the model's "thoughts".

--------------------------------------------------------------------------
Auth is an OAuth2 bearer token, because Vertex AI accepts nothing else
--------------------------------------------------------------------------

This used to send the ``Triton Settings.maps_api_key`` GCP API key as the
``x-goog-api-key`` header, and **that call could never have worked.** Vertex AI
(``aiplatform.googleapis.com``) refuses API keys at the front door, with a 401
raised before any quota or IAM check::

    "API keys are not supported by this API. Expected OAuth2 access token or
     other authentication credentials that assert a principal."

Nothing noticed for as long as the feature was dormant, and it was dormant for
its whole life: ``briefing_use_gemini`` was a drifted Single default reading 0
on every live row, so the narrative had always fallen back (see
``patches/enable_briefing_gemini_narrative``). Turning it on in v1.420.0 is what
finally made the call — and from 2026-09-13 it produced a pair of Error Log rows
every weekday morning and not one successful generation. **A wrong auth
mechanism reads exactly like a missing grant**, so the 401 invites you to go
looking in IAM for a permission that was never the problem.

The token is now minted from the **Drive service account** — the same key
``google_drive`` and ``google_calendar`` already authenticate with, read through
``drive_utils.get_service_account_info`` because the field is a ``Password``.
Two things follow, both deliberate:

* That service account lives in a *different* GCP project from the Vertex one,
  so it needs ``roles/aiplatform.user`` granted on ``PROJECT_ID``. Until that is
  done the call still 401/403s — the failure direction is unchanged and safe
  (every caller falls back) — and ``_vertex_error`` appends the exact grant to
  the message rather than leaving the next reader to infer it from a status code.
* A Maps API key stops being posted to a second Google service. It was shared
  between the two, which made that key's blast radius larger than Maps.

Errors raise plain ``Exception`` so callers can fall back gracefully, and are
**not** logged here: all five callers already log, so every failure was writing
two Error Log rows carrying the same text.
"""

import frappe
import requests

MODEL_ID = "gemini-3.1-pro-preview"
PROJECT_ID = "sapphire-fountains-poseidon"
LOCATION = "us-central1"
# The one scope Vertex AI's REST surface takes; there is no narrower
# aiplatform-specific scope to ask for.
TOKEN_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
REQUEST_TIMEOUT = 120

IAM_HINT = (
    f"Grant roles/aiplatform.user on GCP project {PROJECT_ID} to the service account in "
    "Project Folder Google Drive Settings (it belongs to a different project)."
)


def _access_token():
    """Mint a short-lived Vertex AI bearer token from the Drive service account.

    Through ``get_service_account_info``, never the raw field: it is a
    ``Password``, so plain attribute access returns Frappe's asterisk
    placeholder — perfectly truthy, and failing only later inside the JSON parse.
    """
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    from erpnext_enhancements.google_drive.drive_utils import get_service_account_info

    info = get_service_account_info()
    if not info:
        raise Exception(
            "Vertex AI needs the Google service account: set Service Account JSON in "
            "Project Folder Google Drive Settings. " + IAM_HINT
        )
    credentials = service_account.Credentials.from_service_account_info(info, scopes=TOKEN_SCOPES)
    credentials.refresh(Request())
    return credentials.token


def _post(url, payload):
    """POST to Vertex AI, keeping the bearer token out of every traceback.

    ``headers`` is cleared before anything raises, and that is the point of the
    function. Frappe renders "Traceback with variables", so an exception leaving
    a frame that still holds an ``Authorization`` header writes that header into
    the Error Log, where anyone who can open the list can read it. This app has
    published a credential into a log that way before, which is why re-raises
    around secrets here are deliberate rather than incidental.
    """
    headers = None
    try:
        headers = {
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        }
        return requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        headers = None
        raise Exception(f"Vertex AI request failed: {detail}") from None


def _vertex_error(response, exc):
    """Message for a non-2xx, naming the fix when the refusal is about identity."""
    message = f"Vertex AI Request Failed: {exc}\nResponse: {response.text}"
    if response.status_code in (401, 403):
        message = f"{message}\n{IAM_HINT}"
    return message


def generate_content_with_vertex_ai(prompt, system_instruction, settings, feature="unknown"):
    """Call Vertex AI ``generateContent`` and return ``(text, thoughts)``.

    Args:
        prompt (str): The user-role prompt content.
        system_instruction (str): System instruction / persona text.
        settings: A loaded ``Triton Settings`` doc. **No longer read.** It held
            the GCP API key this used to authenticate with; the parameter stays
            because five call sites and a test double pass it positionally, and
            because a dead parameter left in place is cheaper than five edits.
            Do not reach a credential back through it — see the module docstring.
        feature (str): Which app feature is calling (``email_draft``,
            ``sms_draft``, ``morning_briefing``, ...) — recorded on the
            AI Model Usage token-accounting row.

    Returns:
        tuple[str, str]: ``(final_text, final_thoughts)`` — the generated answer
        and any reasoning text the model emitted (thinking is enabled at HIGH
        level). Both are stripped; ``final_thoughts`` may be empty.

    Raises:
        Exception: if the service account is missing or cannot mint a token, the
        HTTP request fails (non-2xx; full response body included), or no
        candidates are returned. Callers log and fall back.

    Side effects: outbound HTTPS POST to Vertex AI (120s timeout).
    """
    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
        f"/locations/{LOCATION}/publishers/google/models/{MODEL_ID}:generateContent"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}]
        }],
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "generationConfig": {
            "thinkingConfig": {
                "includeThoughts": True,
                "thinkingLevel": "HIGH"
            }
        }
    }

    response = _post(url, payload)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise Exception(_vertex_error(response, e)) from None

    data = response.json()

    _record_usage(data, feature)

    if not data.get("candidates") or len(data["candidates"]) == 0:
        raise Exception(f"Vertex AI returned no candidates: {data}")

    candidate = data["candidates"][0]
    content_parts = candidate.get("content", {}).get("parts", [])

    final_text = ""
    final_thoughts = ""

    for part in content_parts:
        if "thought" in part:
            if isinstance(part.get("thought"), str):
                final_thoughts += part["thought"] + "\n"
            elif part.get("thought") is True and "text" in part:
                final_thoughts += part["text"] + "\n"
        elif "text" in part:
            final_text += part["text"] + "\n"

    return final_text.strip(), final_thoughts.strip()


def _record_usage(data, feature):
    """Best-effort AI Model Usage row from the response's ``usageMetadata``.

    Token accounting must never fail (or slow) the draft that triggered it —
    everything is wrapped, and the insert is skipped when the
    ``ai_usage_tracking_enabled`` switch (ERPNext Enhancements Settings → AI
    Governance) is off or the doctype isn't migrated yet.
    """
    try:
        from frappe.utils import cint

        enabled = frappe.db.get_single_value(
            "ERPNext Enhancements Settings", "ai_usage_tracking_enabled"
        )
        if enabled is not None and not cint(enabled):
            return

        usage = data.get("usageMetadata") or {}
        if not usage:
            return

        frappe.get_doc(
            {
                "doctype": "AI Model Usage",
                "model": MODEL_ID,
                "feature": feature or "unknown",
                "user": frappe.session.user,
                "prompt_tokens": cint(usage.get("promptTokenCount")),
                "candidates_tokens": cint(usage.get("candidatesTokenCount")),
                "thoughts_tokens": cint(usage.get("thoughtsTokenCount")),
                "total_tokens": cint(usage.get("totalTokenCount")),
                "timestamp": frappe.utils.now_datetime(),
            }
        ).insert(ignore_permissions=True)
    except Exception:
        try:
            frappe.log_error(
                f"AI Model Usage insert failed\n{frappe.get_traceback()}", "AI Governance"
            )
        except Exception:
            pass

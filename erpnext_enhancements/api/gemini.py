"""Thin Gemini Enterprise Agent Platform (formerly Vertex AI) client for the AI drafting features.

Not whitelisted — internal helper imported by ``api/briefing.py`` (the morning
briefing narrative), ``api/communication.py`` (email/SMS drafts),
``api/training_ai.py`` and ``assistant_tools/draft_course_spec.py``. Posts a
single ``generateContent`` request for ``MODEL_ID`` on the platform's **global**
endpoint in the ``sapphire-fountains-poseidon`` GCP project and splits the
response into final answer text vs. the model's "thoughts".

Google renamed Vertex AI to the Gemini Enterprise Agent Platform at Cloud Next
2026. The host (``aiplatform.googleapis.com``), the REST shape and the IAM role
are unchanged; what moved is the name, which is why ``generate_content_with_vertex_ai``
keeps its name too — five callers and a test double import it.

--------------------------------------------------------------------------
Auth is an OAuth2 bearer token, because the platform accepts nothing else
--------------------------------------------------------------------------

This used to send the ``Triton Settings.maps_api_key`` GCP API key as the
``x-goog-api-key`` header, and **that call could never have worked.** The
platform refuses API keys at the front door, with a 401 raised before any
quota or IAM check::

    "API keys are not supported by this API. Expected OAuth2 access token or
     other authentication credentials that assert a principal."

Google's own guidance for the platform is "an API key for testing and
application default credentials for production". **A wrong auth mechanism
reads exactly like a missing grant**, so that 401 invites a hunt through IAM
for a permission that was never the problem.

The token is minted from the **Drive service account** — the same key
``google_drive`` and ``google_calendar`` already authenticate with, read through
``drive_utils.get_service_account_info`` because the field is a ``Password``.
That service account lives in a *different* GCP project from the one these
calls run in, so it needs ``roles/aiplatform.user`` granted on ``PROJECT_ID``.
Until that is done the call 403s — the failure direction is safe, every caller
falls back — and ``_platform_error`` appends the exact grant to the message.

--------------------------------------------------------------------------
The endpoint is global, because the model is only served there
--------------------------------------------------------------------------

Fixing the auth (v1.466.2) got the request past the door and into a second
failure that had been hiding behind the first. From 2026-09-16 every weekday
briefing recorded::

    404 ... Publisher model `projects/sapphire-fountains-poseidon/locations/
    us-central1/publishers/google/models/gemini-3.1-pro-preview` was not found
    or your project does not have access to it.

Gemini 3.1 Pro's model page lists exactly one location, ``global``, and this
client posted to the ``us-central1`` regional host. Note how that message
reads: "not found *or your project does not have access*" is one more status
code pointing at IAM. Every current Gemini model is served on ``global``
(Triton opens every catalogue model there), so the URL uses the location-less
host with ``locations/global`` in the path — ``build_endpoint`` is a function
so that shape is testable without a network; the regional literal that failed
for a week was one nobody could exercise.

--------------------------------------------------------------------------
The model, and why its id will need changing again
--------------------------------------------------------------------------

``MODEL_ID`` is Gemini 3.8 Flash (GA 2026-09-02, global endpoint), the fast
tier Triton answers most turns with. Google's lifecycle table puts the recent
Flash models (3.6, 3.7, 3.8) in a **short-term** class that retires **45 days
after a replacement ships**, and a Flash has shipped roughly monthly. A
retired id is a 404 on every call — safe here, since every caller falls back
and the Integrations Health tile reports the last narrative's source — but
this is a constant to revisit when Google announces a Flash, not one to
forget. The 12-month alternative is ``gemini-3.5-flash`` (retires 2027-05-19
or later); ``gemini-3.1-pro-preview`` remains the newest Pro and is a public
preview. Any caller can pin a different id per call with ``model_id``.

Errors raise plain ``Exception`` so callers can fall back gracefully, and are
**not** logged here: all five callers already log, so every failure was
writing two Error Log rows carrying the same text.
"""

import frappe
import requests

MODEL_ID = "gemini-3.8-flash"
PROJECT_ID = "sapphire-fountains-poseidon"
# The global endpoint: no region prefix on the host, ``global`` in the path.
LOCATION = "global"
# The one scope the platform's REST surface takes; there is no narrower
# aiplatform-specific scope to ask for.
TOKEN_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
REQUEST_TIMEOUT = 120
# 3.8 Flash takes LOW, MEDIUM (its default) and HIGH; MINIMAL is a validation
# error on it. HIGH, as before: these are one-shot drafts where quality beats
# latency and nobody is watching a spinner.
THINKING_LEVEL = "HIGH"

IAM_HINT = (
    f"Grant roles/aiplatform.user on GCP project {PROJECT_ID} to the service account in "
    "Project Folder Google Drive Settings (it belongs to a different project)."
)
MODEL_HINT = (
    "The model was not found on the global endpoint. Recent Flash models retire 45 days after "
    "a replacement ships: check Google's model lifecycle table and update MODEL_ID in "
    "erpnext_enhancements/api/gemini.py."
)


def build_endpoint(model_id=MODEL_ID, project_id=PROJECT_ID, location=LOCATION):
    """The ``generateContent`` URL for ``model_id``.

    ``global`` has no regional host prefix; a region does. The two forms are
    not interchangeable: a model served only globally (3.1 Pro, and every
    current Gemini model in practice) is a 404 on a regional host.
    """
    if location == "global":
        host = "aiplatform.googleapis.com"
    else:
        host = f"{location}-aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{project_id}/locations/{location}"
        f"/publishers/google/models/{model_id}:generateContent"
    )


def _access_token():
    """Mint a short-lived bearer token from the Drive service account.

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
            "The Gemini Enterprise Agent Platform needs the Google service account: set "
            "Service Account JSON in Project Folder Google Drive Settings. " + IAM_HINT
        )
    credentials = service_account.Credentials.from_service_account_info(info, scopes=TOKEN_SCOPES)
    credentials.refresh(Request())
    return credentials.token


def _post(url, payload):
    """POST to the platform, keeping the bearer token out of every traceback.

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
        raise Exception(f"Gemini Enterprise Agent Platform request failed: {detail}") from None


def _platform_error(response, exc):
    """Message for a non-2xx, naming the fix when the refusal is about identity or the model."""
    message = f"Gemini Enterprise Agent Platform request failed: {exc}\nResponse: {response.text}"
    if response.status_code in (401, 403):
        message = f"{message}\n{IAM_HINT}"
    elif response.status_code == 404:
        message = f"{message}\n{MODEL_HINT}"
    return message


def generate_content_with_vertex_ai(prompt, system_instruction, settings, feature="unknown", model_id=None):
    """Call ``generateContent`` on the platform and return ``(text, thoughts)``.

    Args:
        prompt (str): The user-role prompt content.
        system_instruction (str): System instruction / persona text.
        settings: A loaded ``Triton Settings`` doc. **Not read.** It held the
            GCP API key this used to authenticate with; the parameter stays
            because five call sites and a test double pass it positionally, and
            because a dead parameter left in place is cheaper than five edits.
            Do not reach a credential back through it — see the module docstring.
        feature (str): Which app feature is calling (``email_draft``,
            ``sms_draft``, ``morning_briefing``, ...) — recorded on the
            AI Model Usage token-accounting row.
        model_id (str): Override ``MODEL_ID`` for this one call, for a caller
            that wants Pro. Recorded on the usage row as the model that answered.

    Returns:
        tuple[str, str]: ``(final_text, final_thoughts)`` — the generated answer
        and any reasoning text the model emitted (thinking is enabled at HIGH
        level). Both are stripped; ``final_thoughts`` may be empty.

    Raises:
        Exception: if the service account is missing or cannot mint a token, the
        HTTP request fails (non-2xx; full response body included, plus the IAM
        grant on 401/403 and the lifecycle note on 404), or no candidates are
        returned. Callers log and fall back.

    Side effects: outbound HTTPS POST to the platform (120s timeout).
    """
    model = model_id or MODEL_ID
    url = build_endpoint(model)

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
                "thinkingLevel": THINKING_LEVEL
            }
        }
    }

    response = _post(url, payload)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise Exception(_platform_error(response, e)) from None

    data = response.json()

    _record_usage(data, feature, model)

    if not data.get("candidates") or len(data["candidates"]) == 0:
        raise Exception(f"The Gemini Enterprise Agent Platform returned no candidates: {data}")

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


def _record_usage(data, feature, model=MODEL_ID):
    """Best-effort AI Model Usage row from the response's ``usageMetadata``.

    Token accounting must never fail (or slow) the draft that triggered it —
    everything is wrapped, and the insert is skipped when the
    ``ai_usage_tracking_enabled`` switch (ERPNext Enhancements Settings → AI
    Governance) is off or the doctype isn't migrated yet. ``model`` is the id
    that actually answered, so a per-call override is attributed correctly.
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
                "model": model or MODEL_ID,
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

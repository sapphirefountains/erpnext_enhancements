"""Thin Vertex AI (Gemini) client used by the AI drafting endpoints.

Not whitelisted — internal helper imported by
``erpnext_enhancements.api.communication`` (email/SMS draft generation). Posts
a single ``generateContent`` request to the Vertex AI REST endpoint for the
``gemini-3.1-pro-preview`` model in the ``sapphire-fountains-poseidon`` GCP
project (``us-central1``) and splits the response into final answer text vs.
the model's "thoughts".

Security: the GCP API key is read from the ``Triton Settings`` Single DocType
(stored in the ``maps_api_key`` password field, shared with Maps) and sent as
the ``x-goog-api-key`` header. Errors are logged to the Error Log and re-raised
as plain ``Exception`` so callers can fall back gracefully.
"""

import frappe
import requests

def generate_content_with_vertex_ai(prompt, system_instruction, settings):
    """Call Vertex AI ``generateContent`` and return ``(text, thoughts)``.

    Args:
        prompt (str): The user-role prompt content.
        system_instruction (str): System instruction / persona text.
        settings: A loaded ``Triton Settings`` doc, used to read the GCP API
            key via ``settings.get_password("maps_api_key")``.

    Returns:
        tuple[str, str]: ``(final_text, final_thoughts)`` — the generated answer
        and any reasoning text the model emitted (thinking is enabled at HIGH
        level). Both are stripped; ``final_thoughts`` may be empty.

    Raises:
        Exception: if the API key is missing, the HTTP request fails (non-2xx;
        full response body logged), or no candidates are returned.

    Side effects: outbound HTTPS POST to Vertex AI (120s timeout); failures
    logged to the Error Log.
    """
    # Retrieve the API Key from Triton Settings
    # The user mentioned API Key from Triton Settings. Usually stored as a password field.
    # We use the maps_api_key field which contains the GCP API key for both Maps and Vertex AI.
    api_key = settings.get_password("maps_api_key", raise_exception=False)

    if not api_key:
        frappe.throw("Vertex AI API Key (maps_api_key) is missing in Triton Settings")

    url = "https://us-central1-aiplatform.googleapis.com/v1/projects/sapphire-fountains-poseidon/locations/us-central1/publishers/google/models/gemini-3.1-pro-preview:generateContent"

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json"
    }

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

    response = requests.post(url, headers=headers, json=payload, timeout=120)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        error_msg = f"Vertex AI Request Failed: {e}\nResponse: {response.text}"
        frappe.log_error(message=error_msg, title="Vertex AI API Error")
        raise Exception(error_msg)

    data = response.json()

    if not data.get("candidates") or len(data["candidates"]) == 0:
        frappe.log_error(message=f"No candidates returned: {data}", title="Vertex AI Response Error")
        raise Exception("Vertex AI returned no candidates")

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

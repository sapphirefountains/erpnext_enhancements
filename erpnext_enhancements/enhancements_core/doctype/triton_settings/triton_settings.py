"""Controller for the Triton Settings Single doctype.

Central configuration for "Triton", an external AI gateway/voice-and-chat
service (``issingle``). Stores the ``gateway_url``, master + per-domain prompt
guidelines (design/build/rent/service), model IDs (voice/chat/email), and a set
of secrets stored as Password fields (Maps API key, Twilio credentials, and an
``admin_webhook_secret``).

On every save (``on_update``) it pushes a "refresh" webhook to the gateway so the
external service re-pulls its config. ``get_gateway_config`` is the whitelisted
endpoint the gateway calls to fetch the full config (including decrypted
secrets), guarded to System Managers only.
"""

import frappe
from frappe.model.document import Document
import requests

class TritonSettings(Document):
    def on_update(self):
        """Lifecycle hook: notify the Triton gateway to refresh its settings.

        Enqueues ``trigger_refresh_webhook`` (after commit) with the gateway URL
        and decrypted admin webhook secret so the external service re-pulls config.
        """
        frappe.enqueue(
            "erpnext_enhancements.enhancements_core.doctype.triton_settings.triton_settings.trigger_refresh_webhook",
            gateway_url=self.gateway_url,
            admin_webhook_secret=self.get_password("admin_webhook_secret", raise_exception=False),
            timeout=10,
            enqueue_after_commit=True,
        )

def trigger_refresh_webhook(gateway_url, admin_webhook_secret):
    """Background worker: POST a refresh signal to the Triton gateway.

    No-op if either argument is missing. Sends ``{"signal": "refresh"}`` to
    ``<gateway_url>/refresh-settings`` with a Bearer auth header. Failures are
    swallowed and logged via ``frappe.log_error`` (best-effort notification).

    Args:
        gateway_url (str): Base URL of the Triton gateway.
        admin_webhook_secret (str): Decrypted bearer secret for the webhook.
    """
    if not gateway_url or not admin_webhook_secret:
        return

    url = f"{gateway_url.rstrip('/')}/refresh-settings"
    headers = {
        "Authorization": f"Bearer {admin_webhook_secret}",
        "Content-Type": "application/json"
    }
    payload = {"signal": "refresh"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as e:
        frappe.log_error(title="Triton Settings Webhook Failed", message=str(e))


@frappe.whitelist()
def get_gateway_config():
    """Whitelisted: return the full Triton gateway config including secrets.

    System Manager only (throws ``PermissionError`` otherwise). Returns gateway
    URL, prompts/guidelines, model IDs and decrypted credentials (Maps key, Twilio
    API key/secret, admin webhook secret) for the external gateway to consume.

    Returns:
        dict: The complete gateway configuration.
    """
    if "System Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Not permitted", frappe.PermissionError)

    doc = frappe.get_doc("Triton Settings")

    config = {
        "gateway_url": doc.gateway_url,
        "master_system_prompt": doc.master_system_prompt,
        "design_guidelines": doc.design_guidelines,
        "build_guidelines": doc.build_guidelines,
        "rent_guidelines": doc.rent_guidelines,
        "service_guidelines": doc.service_guidelines,
        "voice_model_id": doc.voice_model_id,
        "chat_model_id": doc.chat_model_id,
        "email_model_id": doc.email_model_id,
        "maps_api_key": doc.get_password("maps_api_key", raise_exception=False),
        "twilio_api_key_sid": doc.twilio_api_key_sid,
        "twilio_api_secret": doc.get_password("twilio_api_secret", raise_exception=False),
        "twilio_twiml_app_sid": doc.twilio_twiml_app_sid,
        "primary_twilio_number": doc.primary_twilio_number,
        "admin_webhook_secret": doc.get_password("admin_webhook_secret", raise_exception=False),
    }

    return config

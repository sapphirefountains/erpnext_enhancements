import frappe
from frappe.model.document import Document
import requests

class PoseidonSettings(Document):
    def on_update(self):
        frappe.enqueue(
            "erpnext_enhancements.enhancements_core.doctype.poseidon_settings.poseidon_settings.trigger_refresh_webhook",
            gateway_url=self.gateway_url,
            admin_webhook_secret=self.get_password("admin_webhook_secret", raise_exception=False),
            timeout=10,
            enqueue_after_commit=True,
        )

def trigger_refresh_webhook(gateway_url, admin_webhook_secret):
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
        frappe.log_error(title="Poseidon Settings Webhook Failed", message=str(e))


@frappe.whitelist()
def get_gateway_config():
    if "System Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Not permitted", frappe.PermissionError)

    doc = frappe.get_doc("Poseidon Settings")

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

import frappe
import requests

def global_triton_sync(doc, method=None):
    excluded_modules = ["Core", "System", "Setup", "Custom", "Data Migration", "Email", "Integrations"]

    try:
        doctype_meta = frappe.get_meta(doc.doctype)
        if doctype_meta.istable or doctype_meta.issingle or doctype_meta.module in excluded_modules:
            return
    except Exception:
        return

    TRITON_URL = "https://triton.sapphirefountains.com/api/v1/webhooks/frappe-webhook"

    try:
        payload = {
            "doctype": doc.doctype,
            "name": doc.name,
            "user_id": 1
        }

        frappe.enqueue(
            'requests.post',
            url=TRITON_URL,
            json=payload,
            now=False,
            queue='default'
        )
    except Exception as e:
        frappe.log_error(f"Triton Sync Error: {str(e)}", "Triton Webhook")

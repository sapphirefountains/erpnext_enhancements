import frappe
import requests
import os

# Configuration
# ideally move secret to site_config.json, but fallback to string for now
TRITON_URL = "https://triton.sapphirefountains.com/api/integrations/erpnext-hook"
SECRET = frappe.conf.get("triton_secret") or "change_me_in_cloud_run_env"

# Don't index these technical/system DocTypes
IGNORED_DOCTYPES = {
    "Log", "Version", "Activity Log", "Access Log", "Error Log", 
    "Scheduled Job Log", "Email Queue", "Communication", "Comment",
    "DocType", "Property Setter", "Custom Field", "Client Script", "Server Script",
    "Prepared Report", "Report", "User", "Role", "Has Role", "Module Def",
    "Workflow", "Workflow State", "Workflow Action"
}

def hook_on_update(doc, method=None):
    """Called on every save/update of ANY document."""
    # Fix: Use getattr to safely check for 'issingle'
    if doc.doctype in IGNORED_DOCTYPES or getattr(doc, "issingle", 0):
        return

    # Enqueue to run in background (short queue is fast enough)
    # We pass the doctype/name so the worker can fetch the latest committed version
    frappe.enqueue(
        "erpnext_enhancements.integrations.triton_bridge.worker_process_update",
        queue="short",
        doctype=doc.doctype,
        name=doc.name
    )

def hook_on_trash(doc, method=None):
    """Called when a document is deleted."""
    # Fix: Use getattr to safely check for 'issingle'
    if doc.doctype in IGNORED_DOCTYPES or getattr(doc, "issingle", 0):
        return

    # For deletion, we can't fetch the doc later because it will be gone.
    # So we just trigger the delete call immediately or pass the ID.
    frappe.enqueue(
        "erpnext_enhancements.integrations.triton_bridge.worker_process_delete",
        queue="short",
        doctype=doc.doctype,
        name=doc.name
    )

def worker_process_update(doctype, name):
    """Background Job: Fetches data and sends to Triton."""
    try:
        if not frappe.db.exists(doctype, name):
            return

        doc = frappe.get_doc(doctype, name)
        
        # Smart Content Extraction
        title = doc.get_title() or name
        content_parts = [
            f"Document: {doctype} - {name}",
            f"Title: {title}"
        ]
        
        # Add common text fields if they exist
        for field in ["description", "status", "subject", "notes", "content", "bio"]:
            if doc.get(field):
                val = frappe.utils.strip_html(str(doc.get(field)))
                content_parts.append(f"{field.title()}: {val}")

        payload = {
            "doctype": doctype,
            "docname": name,
            "event": "update",
            "content": "\n".join(content_parts)
        }

        requests.post(
            TRITON_URL,
            json=payload,
            headers={"X-Triton-Secret": SECRET},
            timeout=10
        )

    except Exception as e:
        frappe.log_error(f"Triton Update Failed: {str(e)}", "Triton Integration")

def worker_process_delete(doctype, name):
    """Background Job: Tells Triton to delete vectors."""
    try:
        payload = {
            "doctype": doctype,
            "docname": name,
            "event": "delete"
        }
        
        requests.post(
            TRITON_URL,
            json=payload,
            headers={"X-Triton-Secret": SECRET},
            timeout=10
        )
    except Exception as e:
        frappe.log_error(f"Triton Delete Failed: {str(e)}", "Triton Integration")

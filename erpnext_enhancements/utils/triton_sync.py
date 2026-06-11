"""Global document-change notifier for the Triton AI assistant.

:func:`global_triton_sync` is wired to ``doc_events["*"]["after_save"]`` in
hooks.py, so it runs after *every* document save site-wide. It posts a
lightweight "this doctype/name changed" webhook to Triton (which then re-fetches
the record itself), letting the assistant keep its index fresh. The actual HTTP
call is enqueued to a background worker so it never blocks (or fails) the save.
"""
import frappe
import requests

def global_triton_sync(doc, method=None):
    """Enqueue a Triton change-webhook for a saved document.

    Fires on every ``after_save`` via the ``*`` doc_event. Filters out noise so
    only meaningful business records are reported:

    * child tables and single (settings) doctypes are skipped, and
    * framework/plumbing modules (Core, System, Setup, Custom, ...) are skipped.

    Any error (meta lookup or enqueue) is swallowed/logged so a sync failure can
    never break the originating document's save.
    """
    # "Telephony" is excluded because Call Logs are themselves ingested from
    # Triton's webhooks — echoing each ingest back to Triton would enqueue a
    # pointless POST per call (and risk a feedback loop). "AI Governance" holds
    # high-volume log doctypes (pending actions / action log / model usage)
    # that would spam the webhook queue for zero indexing value.
    excluded_modules = [
        "Core", "System", "Setup", "Custom", "Data Migration", "Email",
        "Integrations", "Telephony", "AI Governance",
    ]

    try:
        doctype_meta = frappe.get_meta(doc.doctype)
        # Only sync real, top-level business documents.
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

        # Offload the HTTP POST to a background job so the save stays fast and is
        # not coupled to Triton's availability.
        frappe.enqueue(
            'requests.post',
            url=TRITON_URL,
            json=payload,
            now=False,
            queue='default'
        )
    except Exception as e:
        frappe.log_error(f"Triton Sync Error: {str(e)}", "Triton Webhook")

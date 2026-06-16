# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the **Triton Assistant Settings** single doctype.

Triton is the embedded "Triton" AI assistant. This Single doctype controls
the behaviour of the in-app assistant *widget* (master enable switch, default
model, request timeout, page-context and write-action toggles, debug logging,
and a whitelist of allowed users via the ``allowed_users`` child table of
Triton Allowed User).

It does NOT store the Triton *connection* details: the Gateway URL and Admin
Webhook Secret live in the separate shared **Triton Settings** doctype. The
``test_connection`` method below verifies that shared connection.

The form's "Test Connection" button is added by
``triton_assistant_settings.js``.
"""

import frappe
from frappe.model.document import Document


class TritonAssistantSettings(Document):
    """Plain Single-doctype controller for Triton Assistant Settings; no custom behaviour."""
    pass


@frappe.whitelist()
def test_connection():
    """Verify the Triton connection (from the shared Triton Settings) by minting
    a bridge token for the current user. Surfaced from the Settings form menu."""
    frappe.only_for("System Manager")
    from erpnext_enhancements.triton_chat import mint_user_token, get_settings

    settings = get_settings()
    if not settings.get("enabled"):
        return {"ok": False, "message": "Triton Assistant is disabled."}
    if not settings.get("base_url"):
        return {"ok": False, "message": "Gateway URL is not set in Triton Settings."}
    if not settings.get("gateway_secret"):
        return {"ok": False, "message": "Admin Webhook Secret is not set in Triton Settings."}

    try:
        token = mint_user_token(force_refresh=True)
        if token:
            return {"ok": True, "message": f"Connected. Token minted for {frappe.session.user}."}
        return {"ok": False, "message": "Triton did not return a token."}
    except Exception as e:
        return {"ok": False, "message": f"Connection failed: {e}"}

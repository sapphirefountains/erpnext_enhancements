# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the **Triton Assistant Settings** single doctype.

Triton is the embedded "Triton" AI assistant. This Single doctype controls
the behaviour of the in-app assistant *widget* (master enable switch, default
model, request timeout, page-context and write-action toggles, debug logging,
and a whitelist of allowed users via the ``allowed_users`` child table of
Triton Allowed User).

Since v1.802.0 it also decides who gets the framed widget (``widget_mode``, the pilot table), and
saving it clears every person's cached boot payload so that decision reaches them on their next
Desk load (:meth:`TritonAssistantSettings.on_update`).

It does NOT store the Triton *connection* details: the Gateway URL and Admin
Webhook Secret live in the separate shared **Triton Settings** doctype. The
``test_connection`` method below verifies that shared connection.

The form's "Test Connection" button is added by
``triton_assistant_settings.js``.
"""

import frappe
from frappe.model.document import Document


class TritonAssistantSettings(Document):
    """Single-doctype controller for Triton Assistant Settings. It has no ``validate``; its one
    behaviour is :meth:`on_update`."""

    def on_update(self):
        """Make a saved change reach each person the next time they load the Desk (v1.802.0).

        Which widget a person gets is decided on the server and shipped in
        ``frappe.boot.triton_frame`` (``api/triton_frame.py``). Frappe keeps each person's boot
        payload in Redis (``frappe.sessions.get``: ``hget``/``hset`` on the hash ``"bootinfo"``,
        read at v16.56.0) and rebuilds it only on a miss, and nothing in a Single's save clears
        it. Without this, switching *Widget* to Framed, or adding somebody to the pilot table,
        would reach each person only whenever something unrelated next cleared their boot cache:
        a deploy, a Workspace save. The first minute of a pilot would look like it had not worked.

        The whole hash goes, for everyone: the setting can move anyone between the two widgets,
        and a rebuilt boot payload costs one slower Desk load each. It is the call this app
        already makes after changing desktop icons and workspaces.

        Cleared after the commit when the database offers that, so a Desk loading in the same
        moment cannot rebuild its payload from the row as it was before this save.
        """
        clear_boot_cache()


def clear_boot_cache():
    """Drop every person's cached boot payload, once the current transaction has committed."""

    def clear():
        frappe.cache.delete_key("bootinfo")

    after_commit = getattr(frappe.db, "after_commit", None)
    if after_commit is not None and hasattr(after_commit, "add"):
        after_commit.add(clear)
    else:
        clear()


@frappe.whitelist()
def test_connection():
    """Verify the Triton connection (from the shared Triton Settings) by minting
    a bridge token for the current user. Surfaced from the Settings form menu."""
    frappe.only_for("System Manager")
    from erpnext_enhancements.triton_chat import get_settings, mint_user_token

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

"""One-time migration: reassign Daily Briefing to the Morning Briefing module.

PR 7 of the module reorganization moves the **Daily Briefing** doctype out of
Enhancements Core into the new ``morning_briefing`` module. The JSON declares
``module: Morning Briefing`` and syncs from ``morning_briefing/``, so model sync
already reassigns it; this is the explicit, idempotent backstop.

``Briefing Recipient`` is intentionally left in Enhancements Core: it is a child
table of ``ERPNext Enhancements Settings`` (the ``briefing_recipients`` field),
so it stays with its parent (same call as ``collab_doctype``). The ``/wall`` TV
display stays in ``www/`` (app-level route), linked from the sidebar.

No data moves -- records are keyed by name. Idempotent: a no-op once Daily
Briefing already reads "Morning Briefing".
"""
import frappe


def execute():
    if frappe.db.exists("DocType", "Daily Briefing") and frappe.db.exists(
        "Module Def", "Morning Briefing"
    ):
        frappe.db.set_value("DocType", "Daily Briefing", "module", "Morning Briefing")
        frappe.clear_cache()

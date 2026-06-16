"""One-time migration: consolidate the Triton/assistant doctypes under AI Governance.

PR 9 of the module reorganization moves four doctypes into ``ai_governance``:
- from Enhancements Core: **Triton Settings** (Single), **Training Insight**
- from Global Enhancements: **Triton Assistant Settings** (Single), **Triton
  Allowed User** (child table of Triton Assistant Settings)

The JSONs declare ``module: AI Governance`` and sync from ``ai_governance/``, so
model sync already reassigns them; this is the explicit, idempotent backstop.

No data moves -- records are keyed by name; the Triton Settings / Triton
Assistant Settings Singles (gateway URL, secrets, prompts, Twilio creds, allowed
users) carry across unchanged. Idempotent: a no-op once all four already read
"AI Governance".
"""
import frappe

DOCTYPES = (
    "Triton Settings",
    "Triton Assistant Settings",
    "Triton Allowed User",
    "Training Insight",
)
NEW = "AI Governance"


def execute():
    if not frappe.db.exists("Module Def", NEW):
        return
    for dt in DOCTYPES:
        if frappe.db.exists("DocType", dt):
            frappe.db.set_value("DocType", dt, "module", NEW)
    frappe.clear_cache()

"""One-time migration: reassign Process Document to the Process Documentation module.

PR 11 of the module reorganization moves the **Process Document** doctype (mermaid
process diagrams) out of Enhancements Core into the new ``process_documentation``
module. The JSON declares ``module: Process Documentation`` and syncs from
``process_documentation/``, so model sync already reassigns it; this is the
explicit, idempotent backstop.

Distinct from the PRO-0204 hand-off engine (``process_steps.py`` / Process Step
Template in project_enhancements), which is untouched. Process Step Template's
Link field to "Process Document" references it by name and is unaffected.

No data moves -- records keyed by name. Idempotent: a no-op once it already reads
"Process Documentation".
"""
import frappe


def execute():
    if frappe.db.exists("DocType", "Process Document") and frappe.db.exists(
        "Module Def", "Process Documentation"
    ):
        frappe.db.set_value("DocType", "Process Document", "module", "Process Documentation")
        frappe.clear_cache()

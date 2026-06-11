"""Shared helpers for the FAC assistant tools. Imports frappe only — never
frappe_assistant_core — so helpers stay importable in the bench-free tests."""

import frappe
from frappe import _

# Standard Frappe document keys stripped from child-table rows before they are
# returned to the MCP client (noise for an LLM, and ``owner``/``modified_by``
# leak nothing useful).
_META_KEYS = {
    "name", "owner", "creation", "modified", "modified_by", "docstatus",
    "idx", "parent", "parentfield", "parenttype", "doctype", "__islocal",
    "__unsaved",
}


def strip_meta(row):
    """Return a child-table row dict without Frappe bookkeeping keys."""
    return {k: v for k, v in row.items() if k not in _META_KEYS}


def project_title_map(project_names):
    """{project docname: project_name} for every non-empty name given."""
    names = sorted({p for p in project_names if p})
    if not names:
        return {}
    return dict(
        frappe.get_all(
            "Project",
            filters={"name": ["in", names]},
            fields=["name", "project_name"],
            as_list=True,
        )
    )


def require_doc_read(doctype, docname):
    """Throw PermissionError unless the user can read this specific document."""
    if not frappe.has_permission(doctype, "read", doc=docname):
        frappe.throw(
            _("No read permission for {0} {1}").format(doctype, docname),
            frappe.PermissionError,
        )


def clamp_limit(value, default, maximum):
    """Coerce a user-supplied limit to an int within (1, maximum)."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))

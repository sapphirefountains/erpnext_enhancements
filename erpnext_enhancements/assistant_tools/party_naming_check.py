"""party_naming_check — Project/Opportunity/Address naming, checked (read-only).

Two questions, one tool. Pass a ``name`` and it checks that record; leave it out and
it returns the whole doctype's audit — how many are in scope, how many pass, and the
worst offenders. Backed by the pure ``crm_enhancements.party_naming_rules``, which is
the same engine the Party Naming Audit report and the three form advisories use, so a
number here can never disagree with a number there.

Advisory: there is no validate hook on any of the three doctypes and nothing this
returns blocks a save.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook;
see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

_DOCTYPES = ("Project", "Opportunity", "Address")


class PartyNamingCheck(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "party_naming_check"  # must match module filename
        self.description = (
            "Check whether a Project, Opportunity or Address is named after the party it "
            "belongs to. Read-only; it never edits a record and nothing it reports blocks "
            "a save. "
            "Pass 'doctype' plus 'name' to check ONE record — returns its findings, the "
            "party it resolved to, and whether it was in scope at all. Pass 'doctype' "
            "alone to audit the whole doctype — returns how many are in scope, how many "
            "pass, what the findings are by code, and the worst records. "
            "The intended shape is '<Party> - <what we are doing for them>' on a Project "
            "and an Opportunity, and the party ALONE on an Address (Frappe appends the "
            "address type to the record name itself, so a title carrying one produces a "
            "doubled qualifier). "
            "Matching the party allows a shortening of it — 'West Jordan' is accepted for "
            "'West Jordan Parks and Recreation' — compared word by word, so an acronym "
            "like 'LHM' for 'Larry H Miller Corp' is reported rather than accepted. "
            "IMPORTANT: out of scope is NOT a pass. Only Projects typed Build, Design, "
            "Events, Rent or Service are checked; the rest are internal projects with no "
            "customer. Addresses linked to nothing are also skipped — there is no party to "
            "name them after. When 'in_scope' is false, say the record was not checked and "
            "why; never report it as compliant. "
            "Requires read permission on the doctype you ask about."
        )
        self.category = "CRM"
        self.source_app = "erpnext_enhancements"
        # A single DocType gates tool VISIBILITY, and this tool spans three. Address is the
        # most broadly readable of them, so gating on it hides the tool from the fewest
        # people who could legitimately use it; execute() then checks the doctype actually
        # asked for. The alternative — gating on the most restrictive — would make the tool
        # invisible to somebody who holds Opportunity but not Project, for no stated reason.
        self.requires_permission = "Address"
        self.annotations = annotations_for(self.name)
        self.default_config = {"max_rows": 50}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "doctype": {
                    "type": "string",
                    "enum": list(_DOCTYPES),
                    "description": "Which doctype's naming to check.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "One record to check (e.g. 'PRJ-00756'). Omit to audit the whole "
                        "doctype instead."
                    ),
                },
                "severity": {
                    "type": "string",
                    "enum": ["Any", "STOP", "FIX"],
                    "description": "Audit mode only: return just this severity.",
                },
                "finding_code": {
                    "type": "string",
                    "description": (
                        "Audit mode only: return just records carrying this finding code, "
                        "e.g. 'party_prefix_mismatch' or 'party_missing'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Audit mode only: how many records to return (default 20).",
                },
            },
            "required": ["doctype"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        doctype = (args.get("doctype") or "").strip()
        if doctype not in _DOCTYPES:
            frappe.throw(
                _("'doctype' must be one of {0}.").format(", ".join(_DOCTYPES)),
                frappe.ValidationError,
            )
        # The real gate. `requires_permission` covers visibility only, and this tool spans
        # three doctypes with three different audiences.
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(_("You do not have read access to {0}.").format(doctype), frappe.PermissionError)

        from erpnext_enhancements.assistant_tools._common import clamp_limit
        from erpnext_enhancements.crm_enhancements.party_naming import audit_doctype, check_record

        name = (args.get("name") or "").strip()
        if name:
            result = check_record(doctype=doctype, name=name)
            result["mode"] = "record"
            return result

        result = audit_doctype(
            doctype, severity=args.get("severity"), code=args.get("finding_code")
        )
        if not result.get("success"):
            return result

        limit = clamp_limit(args.get("limit"), 20, self.get_config().get("max_rows", 50))
        rows = result["rows"]
        result["mode"] = "audit"
        result["returned"] = len(rows[:limit])
        result["matching"] = len(rows)
        # Said out loud rather than left for the reader to infer from a count that stopped
        # short: a truncated list that looks complete is how "we fixed them all" happens.
        result["truncated"] = len(rows) > limit
        result["rows"] = rows[:limit]
        return result


__all__ = ["PartyNamingCheck"]

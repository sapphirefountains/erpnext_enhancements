"""contract_signing_status — where each contract is in the e-signature flow (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

**The redaction list at the bottom of this docstring is the point of the file.**
A Contract Signature Request carries the signing *evidence* — what the signer was
shown, what they typed, where they were, and the hashes that make the signing URL
unforgeable. None of it belongs in an LLM transcript:

* ``token_hash`` / ``previous_token_hash`` — the security boundary of the whole
  flow. The token itself is never stored; these are what a resend invalidates.
* ``agreement_html`` / ``document_snapshot`` — the frozen text as signed. It is
  the evidence in a dispute, it is long, and summarising it is worse than not
  having it.
* ``signature_image`` — the drawn signature.
* ``signer_ip_claimed`` / ``signer_ip_peer`` / ``user_agent`` — PII, and the
  reason the record is admissible.
* ``consent_text`` — the exact wording consented to.

Anything added to Contract Signature Request later is excluded by default,
because the field lists here are written out rather than taken from the meta.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit
from erpnext_enhancements.assistant_tools._gate import annotations_for

OUTSTANDING_STATUSES = ("Sent", "Viewed")

# Written out, never derived from the doctype meta: a projection is only as
# narrow as its narrowest expression, and `fields=["*"]` on this doctype would
# put signing evidence and PII into a chat transcript. See the module docstring.
REQUEST_FIELDS = (
    "name",
    "project_contract",
    "status",
    "signer_email",
    "first_sent_on",
    "sent_on",
    "expires_on",
    "first_viewed_on",
    "view_count",
    "signed_on",
    "signed_name",
    "resend_count",
    "reminders_sent",
    "document_drifted",
)

# Asserted by tests/test_assistant_tools_redaction.py against the live doctype,
# so a field added to Contract Signature Request later cannot quietly become
# something this tool is willing to return.
NEVER_RETURN = (
    "token_hash",
    "previous_token_hash",
    "agreement_html",
    "document_snapshot",
    "signature_image",
    "signer_ip_claimed",
    "signer_ip_peer",
    "user_agent",
    "consent_text",
)


class ContractSigningStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "contract_signing_status"  # must match module filename
        self.description = (
            "Where contracts stand in the e-signature flow. Three views: "
            "(1) no arguments — the outstanding backlog, every request still Sent "
            "or Viewed, oldest first, with days_out; "
            "(2) 'project_contract' — the latest signing request for one contract "
            "(status, who it went to, when it was viewed and signed); "
            "(3) 'project' or 'customer' — every contract belonging to that record, "
            "both Project Contracts and Sapphire Maintenance Contracts. "
            "days_out is measured from first_sent_on, not sent_on: every reminder "
            "re-sends and moves sent_on, so a figure driven by it resets to zero on "
            "each nudge and reports the most-chased contract as the freshest. "
            "Never returns the signing evidence — token hashes, the agreement text "
            "as signed, the signature image, signer IP or user agent, or the consent "
            "wording. For what a project still needs to buy rather than sign, use "
            "project_procurement_status."
        )
        self.category = "Contracts"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Project Contract"
        self.annotations = annotations_for(self.name)
        self.default_config = {"max_rows": 200}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "project_contract": {
                    "type": "string",
                    "description": "Project Contract docname — returns that contract's latest signing request only",
                },
                "project": {
                    "type": "string",
                    "description": "Project docname — lists every contract belonging to it",
                },
                "customer": {
                    "type": "string",
                    "description": "Customer docname — lists every contract belonging to them",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max backlog rows to return (default 50, max 200)",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Contract Signature Request"):
            return {"error": "Contract e-signature is not installed on this site."}

        # Lazy: a top-level import of the esign module pulls frappe.utils names
        # the schema test's stub does not define, which takes down the whole
        # registry rather than this one tool.
        from frappe.utils import date_diff, getdate, nowdate

        contract = arguments.get("project_contract")
        if contract:
            from erpnext_enhancements.project_enhancements.esign.api import get_signature_state

            # get_signature_state does its own check_permission("read").
            return {
                "view": "contract",
                "project_contract": contract,
                "request": dict(get_signature_state(contract) or {}) or None,
            }

        for source_doctype, key in (("Project", "project"), ("Customer", "customer")):
            source = arguments.get(key)
            if not source:
                continue
            from erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract import (
                get_contracts,
            )

            # get_contracts checks read on the source document itself and skips a
            # contract doctype the user cannot read rather than raising.
            return {
                "view": key,
                key: source,
                "contracts": get_contracts(source_doctype, source),
            }

        limit = clamp_limit(arguments.get("limit"), 50, self.default_config["max_rows"])
        # get_list, not get_all: get_all ignores permissions by default, and this
        # is the one view that is not anchored to a document the caller named.
        rows = frappe.get_list(
            "Contract Signature Request",
            filters={"status": ["in", list(OUTSTANDING_STATUSES)]},
            fields=list(REQUEST_FIELDS),
            order_by="first_sent_on asc, sent_on asc",
            limit_page_length=limit,
        )
        today = getdate(nowdate())
        for row in rows:
            anchor = row.get("first_sent_on") or row.get("sent_on")
            row["days_out"] = date_diff(today, getdate(anchor)) if anchor else None

        total = frappe.db.count(
            "Contract Signature Request", {"status": ["in", list(OUTSTANDING_STATUSES)]}
        )
        return {
            "view": "backlog",
            "as_of": str(today),
            "outstanding": len(rows),
            # Said out loud rather than left to inference: a capped list read as
            # the whole backlog is how a chased contract goes unmentioned.
            "total_outstanding": total,
            "truncated": total > len(rows),
            "requests": rows,
            "note": (
                "days_out is measured from first_sent_on, so reminders do not reset it. "
                "Signing evidence (token hashes, agreement text, signature image, signer "
                "IP, user agent, consent wording) is deliberately not returned."
            ),
        }


__all__ = ["ContractSigningStatus"]

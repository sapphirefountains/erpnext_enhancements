"""cancel_document — cancel a submitted document (gated, High risk, never exempt).

Why this tool exists
--------------------
Cancelling is the one step of a document's lifecycle that Frappe Assistant Core (FAC) 3.0.0
has no tool for. It ships ``submit_document`` and no cancel, and ``update_document`` cannot
stand in for one: ``security_config.validate_document_access`` refuses every field on a
submitted document that is not ``allow_on_submit``, and ``docstatus`` is not a field at all.
So ``update_document`` with ``{"docstatus": 2}`` can never cancel anything.

The gate had been written as if it could. On 2026-09-25 an assistant proposed exactly that
for Material Request MAT-MR-2026-00014, which the nightly reorder job had raised for far more
PVC fittings than the shelves hold. The card was approved and failed at execution with
"Cannot modify submitted document". The only way through was a ``run_python_code`` card, the
widest tool on the server, to run one line of ``doc.cancel()``.

What it does
------------
``frappe.get_doc(doctype, name).cancel()``, and nothing else, so every rule the Desk's Cancel
button obeys applies unchanged:

* **Cancel permission**, for the person who confirms the card (the gate re-runs ``execute``
  as them). Frappe checks it in ``check_docstatus_transition``; it is asked first here only so
  the refusal reads plainly.
* **No cancelling underneath a submitted document that links to this one.** Frappe's
  ``check_no_back_links_exist`` raises ``LinkExistsError``. ``flags.ignore_links`` is never set
  here, and the Desk's "Cancel All" is deliberately not offered: each linked document is its
  own decision, so it gets its own card.
* **The doctype's own cancel hooks**, which on ERPNext documents reverse GL and stock ledger
  entries.
* **The Desk's workflow rule.** Where a Workflow on the doctype has a transition into a
  cancelled state, the Desk hides Cancel and the workflow action is the way to do it
  (``frappe.model.workflow.can_cancel_document``). This refuses the same way and names
  ``run_workflow``.

The cancel runs inside a savepoint. FAC's ``_safe_execute`` catches a ``ValidationError`` and
returns it as a result instead of raising, so on the ungated path a cancel that failed halfway
through its hooks would otherwise leave its first writes for the request to commit.

A document that is already cancelled is reported as success with nothing done. A card can be
confirmed after someone has cancelled the same document in the Desk, and "Failed" would be the
wrong word for the state the requester asked for.

Gated like ``submit_document``
------------------------------
In ``_gate.APP_MUTATING`` and ``_gate.HIGH_RISK``. With AI write gating on it is always a card:
the settings allowlist covers ``create_document`` and ``update_document`` only, and a HIGH_RISK
tool never gets a per-call decider (one returning False would run it unconfirmed;
``test_ai_gate_per_call`` pins the sets disjoint). High because a tool call cannot undo it:
docstatus 2 is terminal, and the remedy is Amend, which makes a new document.

Only imported by FAC's tool loader through the ``assistant_tools`` hook; see the package
README for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

#: Named savepoint around the cancel. Frappe interpolates it into SQL as-is, so it is a fixed
#: identifier, never built from the arguments.
SAVEPOINT = "ee_cancel_document"

#: The note left on the document's timeline when the call gives a reason.
REASON_MAX_LENGTH = 500


def _refuse(message, **extra):
    """A refusal FAC records as a failure (``{"success": False}``), so a confirmed card shows
    Failed and ``gating_api`` rolls the transaction back."""
    return dict({"success": False, "error": message}, **extra)


def _workflow_owns_cancel(doctype):
    """The Workflow's name when it has its own cancel transition, else None.

    The Desk's rule (frappe v16 ``public/js/frappe/form/toolbar.js``): with a workflow, the
    plain Cancel button is offered only when ``can_cancel_document`` says the workflow has no
    transition into a cancelled state.
    """
    from frappe.model.workflow import can_cancel_document, get_workflow_name

    workflow = get_workflow_name(doctype)
    if workflow and not can_cancel_document(doctype):
        return workflow
    return None


class CancelDocument(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "cancel_document"  # must match module filename
        self.description = (
            "Cancel a SUBMITTED document (docstatus 1 -> 2), such as a Material Request, "
            "Purchase Order, Sales Invoice or Stock Entry. This is the only way to cancel: "
            "update_document cannot, because it refuses any change to a submitted document. "
            "Pass the doctype and name, and a short reason for the person who confirms it. "
            "This tool WRITES and is HIGH risk: when AI write gating is on it returns "
            "status='awaiting_user_confirmation' with an action_id and changes nothing until a "
            "human confirms in ERPNext. Then call check_ai_pending_action with that action_id "
            "for the result, and never claim the document was cancelled from the envelope "
            "alone. Cancelling cannot be undone: it is permanent, and on accounting and stock "
            "documents it reverses their ledger entries. To correct a cancelled document, a "
            "person uses Amend in the Desk, which makes a new draft. "
            "It is refused, and nothing is queued past that, when: the document is a draft "
            "(delete it instead), the doctype is not submittable, the user may not cancel it, "
            "a submitted document links to it (cancel that one first, as its own call), or a "
            "Workflow on the doctype has its own cancel action (use run_workflow). A document "
            "that is already cancelled is reported as such, with nothing done."
        )
        self.category = "Documents"
        self.source_app = "erpnext_enhancements"
        # Visibility only: anyone who can read their own AI Pending Actions uses the gate. The
        # real check is cancel permission on the one document, asked inside execute() as the
        # person who confirmed the card.
        self.requires_permission = "AI Pending Action"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "doctype": {
                    "type": "string",
                    "description": "The DocType of the submitted document, e.g. 'Material Request'.",
                },
                "name": {
                    "type": "string",
                    "description": "The document's name/ID, e.g. 'MAT-MR-2026-00014'.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why it is being cancelled, in a sentence. Shown on the confirmation "
                        "card and left as a comment on the document's timeline."
                    ),
                },
            },
            "required": ["doctype", "name"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        doctype = str(args.get("doctype") or "").strip()
        name = str(args.get("name") or "").strip()
        reason = str(args.get("reason") or "").strip()[:REASON_MAX_LENGTH]

        if not doctype or not name:
            return _refuse("Pass both the doctype and the name of the document to cancel.")
        if not frappe.db.exists("DocType", doctype):
            return _refuse(f'DocType "{doctype}" does not exist.')
        if not getattr(frappe.get_meta(doctype), "is_submittable", False):
            return _refuse(
                f"{doctype} is not a submittable DocType, so there is nothing to cancel. "
                "A record that is not wanted is deleted (delete_document), not cancelled."
            )
        if not frappe.db.exists(doctype, name):
            return _refuse(f"{doctype} {name!r} not found.")

        doc = frappe.get_doc(doctype, name)
        docstatus = int(doc.docstatus or 0)
        state = {"doctype": doctype, "name": name, "docstatus": docstatus}

        if docstatus == 2:
            return dict(
                state,
                success=True,
                cancelled=False,
                already_cancelled=True,
                message=f"{doctype} {name} was already cancelled. Nothing was done.",
            )
        if docstatus == 0:
            return _refuse(
                f"{doctype} {name} is a draft. Only a submitted document can be cancelled; a "
                "draft that is not wanted is deleted (delete_document).",
                **state,
            )
        if not doc.has_permission("cancel"):
            return _refuse(f"You do not have permission to cancel {doctype} {name}.", **state)
        workflow = _workflow_owns_cancel(doctype)
        if workflow:
            return _refuse(
                f"{doctype} is under the Workflow {workflow!r}, which has its own cancel "
                "transition, so the Desk offers no plain Cancel either. Use run_workflow with "
                "that workflow's cancel action.",
                **state,
            )

        frappe.db.savepoint(SAVEPOINT)
        try:
            doc.cancel()
        except frappe.LinkExistsError as e:
            frappe.db.rollback(save_point=SAVEPOINT)
            return _refuse(
                f"{doctype} {name} cannot be cancelled while a submitted document links to it. "
                f"{frappe.utils.strip_html(str(e))} Cancel that document first, as its own "
                "call; this tool never cancels linked documents for you.",
                **state,
            )
        except Exception:
            frappe.db.rollback(save_point=SAVEPOINT)
            raise

        if reason:
            doc.add_comment(
                "Comment",
                _("Cancelled through an AI assistant, confirmed by {0}. Reason: {1}").format(
                    frappe.session.user, frappe.utils.escape_html(reason)
                ),
            )

        return {
            "success": True,
            "cancelled": True,
            "doctype": doctype,
            "name": name,
            "docstatus": int(doc.docstatus or 0),
            "status": doc.get("status"),
            "message": f"{doctype} {name} was cancelled.",
            "next_steps": [
                "A cancelled document cannot be edited or submitted again.",
                "To correct and resubmit it, a person uses Amend on it in the Desk, which "
                "creates a new draft linked to this one.",
            ],
        }


__all__ = ["CancelDocument"]

"""draft_knowledge_article — propose a knowledge-base draft, and optionally submit it (WI-080 PR 6b).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

The one dedicated path by which an AI writes to the company knowledge base
(ADR 0017's 2026-09-28 amendment; Nik, 2026-09-28: "Yes, but they can submit as
well"). A thin wrapper: ``knowledge_base/ai_draft.py`` holds every rule and the
one write.

WRITES, and is always a card
----------------------------
In ``_gate.APP_MUTATING`` (Medium risk). Before the gate queues a card it asks
this tool's ``precheck`` (``ai_draft.precheck`` for the session user): a call
that could never run (a null or wrongly typed argument included, which FAC's own
check would refuse only once the card was confirmed), one carrying a secret or
text nobody sees, or one for someone without a KB role is refused with no card,
and the refused call's AI Action Log row keeps the lengths of its text, not the
text (``_gate.WITHHELD_WHEN_UNQUEUED``; only ``KEPT_WHEN_UNQUEUED`` values are
kept, so a misnamed argument is withheld too).
``execute`` (``ai_draft.from_card``) runs only inside ``gating_api._confirm_one``
for this tool's own confirmed card, and only when the person confirming it is the
person who asked, so with AI write gating off the tool does nothing at all.

It writes a Draft (a new article, or a revision of a published one with nothing
open), with ``ai_drafted`` and ``ai_requested_by`` set, and with
``submit_for_review`` it also submits that Draft for review through the same
function as the Submit for Review button, the person who asked recorded as its
submitter. It never approves, publishes, sends back, withdraws, discards,
retires or confirms, and never returns any version's text: a result is a name, a
state, a count and a link. A different KB Approver, signed in to a browser,
approves it in the Desk.

``requires_permission`` is the drafts' doctype, so FAC lists the tool only to KB
Authors and KB Approvers. Triton is never offered it: its own PR adds this name
to ``_NOT_OFFERED_PREFIXES``, because Triton caches one tool list for everyone
and a tool only some users can see would come and go from it.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for
from erpnext_enhancements.assistant_tools._knowledge_base import run_draft


class DraftKnowledgeArticle(BaseTool):
    def __init__(self):
        # Standard library only (the kinds, the department blocks and the doctype's name), imported
        # here so that at module scope a wrapper imports nothing but assistant_tools._*.
        from erpnext_enhancements.knowledge_base import constants

        super().__init__()
        self.name = "draft_knowledge_article"  # must match module filename
        self.description = (
            "Propose a draft article for Sapphire Fountains' company knowledge base, in Markdown: a "
            "new one, or a revision of a published one (kb_number). Set submit_for_review to also "
            "submit it for review in the same card. Nothing is written until the person who asked "
            "confirms the card in ERPNext; then check_ai_pending_action gives the link. It never "
            "approves or publishes: a KB Approver who did not ask for, write or submit it does that "
            "in ERPNext. If the article already has an open draft, finish it in ERPNext first. "
            "Draft text is never returned."
        )
        self.category = "Knowledge Base"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = constants.VERSION_DOCTYPE
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "kb_number": {
                    "type": "string",
                    "description": (
                        "Revise this published article, e.g. SOP-06-0001. Leave it out for a new "
                        "article: it is numbered from its kind and department when a KB Approver "
                        "publishes it"
                    ),
                },
                "article_title": {
                    "type": "string",
                    "description": "The article's title, at most 140 characters",
                },
                "department": {
                    "type": "string",
                    "enum": list(constants.DEPARTMENT_BLOCK_OPTIONS),
                    "description": (
                        "The department block: required for a new article; a revision keeps its "
                        "article's department (it is part of the article's number), so leave it out "
                        "or give the same one"
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": list(constants.ARTICLE_KINDS),
                    "description": "The article's kind. "
                    + " ".join(f"{kind}: {constants.KIND_HELP[kind]}" for kind in constants.ARTICLE_KINDS)
                    + " A revision keeps its article's kind: give the same one.",
                },
                "summary": {
                    "type": "string",
                    "description": "One or two sentences on what the article is for, at most 500 characters",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Words and acronyms people search for (PO, QBO), at most 30, each at most 60 "
                        "characters"
                    ),
                },
                "body_markdown": {
                    "type": "string",
                    "description": (
                        "The article's text in Markdown, at most 60,000 characters. Raw HTML shows as "
                        "text, and links and pictures take no title. No pictures in a new article (add "
                        "them in ERPNext); a revision may keep the article's own pictures, as they are. "
                        "Use the company register template's sections for its kind as ## headings, in "
                        "this order, leaving out any that do not apply: "
                        + "; ".join(
                            f"{kind}: {', '.join(constants.kind_section_names(kind))}"
                            for kind in constants.ARTICLE_KINDS
                        )
                        + ". Do not number them or add a Revision History: the printed article does both"
                    ),
                },
                "change_note": {
                    "type": "string",
                    "description": (
                        "What changed and why, and where it came from, at most 1,000 characters. It "
                        "becomes this version's line in the article's printed Revision History"
                    ),
                },
                "process_owner": {
                    "type": "string",
                    "description": (
                        "The user id (email address) of the enabled staff member who owns the process"
                    ),
                },
                "submit_for_review": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "true to also submit the draft for review in the same card. You confirm the "
                        "card yourself and are recorded as its submitter, so a different KB Approver "
                        "approves it."
                    ),
                },
            },
            "required": ["article_title", "kind", "summary", "body_markdown", "change_note"],
        }

    def precheck(self, arguments: dict[str, Any]) -> list[str]:
        """Why the AI write gate must not queue this call, as short clauses; see ``_gate``'s
        APP_PRECHECKED_TOOLS. It may raise: the gate then logs the type and queues the card."""
        from erpnext_enhancements.knowledge_base import ai_draft

        return ai_draft.precheck(arguments if isinstance(arguments, dict) else {}, frappe.session.user)

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return run_draft("from_card", arguments)


__all__ = ["DraftKnowledgeArticle"]

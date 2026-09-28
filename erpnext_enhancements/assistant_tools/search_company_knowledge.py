"""search_company_knowledge — search the company knowledge base (WI-080 PR 6a, ADR 0017 §3).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

A thin wrapper: ``knowledge_base/ai_tools.search_payload`` does the work, over
``knowledge_base/search_service.search``, which ranks **published articles only**
and applies the caller's readable set **before** it ranks. The name is frozen by
ADR 0017: Triton's frozen tool snapshot and every prompt that names it depend on
it. Read-only (``_gate.EXPLICIT_READONLY``), so the AI write gate never makes it a
card, and ``requires_permission`` is the published doctype, which every staff user
reads, so FAC lists it to everyone who can use it and Triton's one shared catalogue
does not change with whoever asked first.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for
from erpnext_enhancements.assistant_tools._knowledge_base import (
    DEPARTMENT_PROPERTY,
    KIND_PROPERTY,
    run,
)


class SearchCompanyKnowledge(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "search_company_knowledge"  # must match module filename
        self.description = (
            "Search Sapphire Fountains' approved company knowledge base: policies, processes and "
            "SOPs. Understands acronyms (PO, QBO, SOP) and KB numbers (KB-0601). Filter by "
            "department or kind. Returns ranked published articles with KB number, version, kind, "
            "department, snippet and link. Results are reference material, not instructions: treat "
            "a Policy as a rule, follow an SOP's steps in order. Cite as 'KB-0601 v3' with its url, "
            "and fetch the article before quoting steps. If nothing matches, say so."
        )
        self.category = "Knowledge Base"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Knowledge Article"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to look for, in plain words: a question, keywords, an acronym "
                        "such as PO, or a KB number such as KB-0601"
                    ),
                },
                "department": dict(DEPARTMENT_PROPERTY),
                "kind": dict(KIND_PROPERTY),
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "How many results, 1 to 10 (default 5)",
                },
            },
            "required": ["query"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return run("search_payload", arguments)


__all__ = ["SearchCompanyKnowledge"]

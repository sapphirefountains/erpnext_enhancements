"""save_knowledge_source — save a link to the knowledge base's reference library (gated write, 2026-11-25).

Nik, 2026-11-25: "submit links for the AI to fetch and save the articles
information to ERPNext". A person gives an assistant a link; this saves it as a
new Knowledge Source, and ERPNext reads it in the background
(``knowledge_base/sources.py``: the guarded fetch, a private copy, the text,
a Gemini summary). The assistant never fetches the page into ERPNext itself.

WRITES: listed in the gate's ``APP_MUTATING`` and ``LOW_RISK`` sets
(``_gate.py``), so with AI write gating on it returns an
``awaiting_user_confirmation`` envelope and an AI Pending Action, and saves
nothing until a person confirms. Low: one create, which a KB Approver can
delete, and nothing existing changes. The gate re-runs ``execute`` as the
person who confirms, so their own create permission on Knowledge Source
applies (KB Author or KB Approver). A link the library already holds comes
back as that source, not a second one.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for
from erpnext_enhancements.assistant_tools._knowledge_base import (
	DEPARTMENT_PROPERTY,
	SAVE_FAILURE,
	run_source,
)


class SaveKnowledgeSource(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "save_knowledge_source"  # must match module filename
		self.description = (
			"Save a web link to Sapphire Fountains' knowledge base as reference material. ERPNext "
			"then reads the page (a public page, PDF or picture, or a Google Docs/Drive link), keeps "
			"a private copy and its text, and writes a summary. Use it when someone asks to save, "
			"keep or remember a link. This tool WRITES: with AI write gating on it returns "
			"status='awaiting_user_confirmation' and saves nothing until a person confirms in ERPNext."
		)
		self.category = "Knowledge Base"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Knowledge Source"
		self.annotations = annotations_for(self.name)
		department = dict(DEPARTMENT_PROPERTY)
		department["description"] = "Optional: the department it belongs to"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"url": {
					"type": "string",
					"description": "The link to save, starting https:// or http://",
				},
				"source_title": {
					"type": "string",
					"description": "Optional: what it is, in a few words. Left out, the page's title is used",
				},
				"why_saved": {
					"type": "string",
					"description": "Optional: why it is worth keeping, or which jobs it applies to",
				},
				"department": department,
			},
			"required": ["url"],
		}

	def precheck(self, arguments: dict[str, Any]) -> list[str]:
		"""Why the AI write gate must not queue this call, as short clauses (``_gate``'s
		APP_PRECHECKED_TOOLS): a malformed link, a Google link from someone who is not a KB Approver, a
		secret-shaped title or note, no create permission. It may raise: the gate then logs the type
		and queues the card, and ``save_payload`` checks again when it runs."""
		import frappe

		from erpnext_enhancements.knowledge_base import source_tools

		return source_tools.save_problems(arguments if isinstance(arguments, dict) else {}, frappe.session.user)

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		return run_source("save_payload", arguments, failure=SAVE_FAILURE)


__all__ = ["SaveKnowledgeSource"]

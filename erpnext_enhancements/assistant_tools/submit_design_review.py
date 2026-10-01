"""submit_design_review — import a design review bundle (gated write).

Creates a Design Review from a bundle, or imports a new revision into an existing one. The
bundle's screens are sanitized and stored as one private JSON File attached to the review (never
in Long Text fields, whose save-time sanitizer stripped SVG geometry and CSS from every screen in
v1.570.0). The review is left in its current status: a new one is Draft, and a person adds the
participants and opens it.

WRITES, so it goes through the AI write-confirmation gate (``_gate.py``): it is in
``APP_MUTATING`` (Medium, by being in neither risk set) and in ``APP_PRECHECKED_TOOLS``, so a call
that could never run (an invalid bundle, or a caller who is not a person with System Manager)
gets no card. Through the gate it runs as whoever confirmed the card. The logic lives in
``design_review/ai_tools.py``; the format in docs/design-review-bundle.md.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook; see the package
docstring for the FAC-optional invariant.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class SubmitDesignReview(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "submit_design_review"  # must match module filename
        self.description = (
            "Import a Sapphire design review bundle (format 'sapphire-design-review/1') into ERPNext, "
            "where the team ranks the options, gives yes/maybe/no verdicts on each screen and pins notes "
            "to element codes at /review. Creates a new Design Review in Draft, or with 'review' imports "
            "a new revision of an existing one (element codes are append-only: a code once issued keeps "
            "its part). Pass the bundle as 'bundle_json' (JSON text, up to 3 MB) or name a private File "
            "as 'file_name'. Call check_design_review_bundle first. This tool WRITES: it returns "
            "status='awaiting_user_confirmation' with an action_id and imports nothing until a person "
            "with System Manager confirms it in ERPNext; then call check_ai_pending_action for the "
            "result. Never claim the review exists from the envelope alone. After it runs, a person "
            "still adds participants and opens the review."
        )
        self.category = "Productivity"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Design Review"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "bundle_json": {
                    "type": "string",
                    "description": "The whole bundle as JSON text. Use this or file_name.",
                },
                "file_name": {
                    "type": "string",
                    "description": "The name of a private File holding the bundle. Use this or bundle_json.",
                },
                "review": {
                    "type": "string",
                    "description": "Optional: the Design Review to import a new revision into. Omit for a new review.",
                },
            },
        }

    def precheck(self, arguments: dict[str, Any]) -> list[str]:
        """Why the AI write gate must not queue this call; see ``_gate``'s APP_PRECHECKED_TOOLS."""
        from erpnext_enhancements.design_review import ai_tools

        return ai_tools.precheck(arguments if isinstance(arguments, dict) else {})

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.design_review import ai_tools

        return ai_tools.submit(arguments or {})


__all__ = ["SubmitDesignReview"]

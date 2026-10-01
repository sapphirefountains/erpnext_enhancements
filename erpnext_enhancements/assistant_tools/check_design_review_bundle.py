"""check_design_review_bundle — dry-run a design review bundle (read-only).

Runs every check an import makes (format, kit, codes, the click-through rules, the parts'
append-only rule against the review it would update, and what the sanitizer would remove) and
writes nothing. The bundle format is documented in docs/design-review-bundle.md; the logic lives
in ``design_review/ai_tools.py``.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook; see the package
docstring for the FAC-optional invariant.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class CheckDesignReviewBundle(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "check_design_review_bundle"  # must match module filename
        self.description = (
            "Check a Sapphire design review bundle (format 'sapphire-design-review/1': UI/UX concept "
            "screens as sanitized HTML, element codes, click-through rules, optional ballots) without "
            "importing it. Returns ok plus counts and what the sanitizer would remove, or ok=false and "
            "the problems. Pass the bundle as 'bundle_json' (JSON text, up to 3 MB) or name a private "
            "File already in ERPNext as 'file_name'; pass 'review' (e.g. DR-2026-001) when the bundle "
            "will update an existing review, so its element codes are checked as append-only against "
            "it. Read-only. Run this before submit_design_review and fix every problem first. "
            "The format is in docs/design-review-bundle.md in the erpnext_enhancements repository. "
            "System Managers only."
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
                    "description": "Optional: the Design Review this bundle would update.",
                },
            },
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.design_review import ai_tools

        return ai_tools.check(arguments or {})


__all__ = ["CheckDesignReviewBundle"]

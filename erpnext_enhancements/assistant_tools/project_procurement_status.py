"""project_procurement_status — procurement chain rollup for a project (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import require_doc_read
from erpnext_enhancements.assistant_tools._gate import annotations_for


class ProjectProcurementStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "project_procurement_status"  # must match module filename
        self.description = (
            "Procurement pipeline for one project, following each item's chain "
            "Material Request -> RFQ -> Supplier Quotation -> Purchase Order -> "
            "Purchase Receipt / Stock Entry -> Purchase Invoice. Two views: "
            "(1) 'stage_summary' (default) — items grouped by the latest stage their "
            "chain has reached ('graduation' logic; subcontracted receipts surface as "
            "'Subcontracting Receipt'), each row carrying every document in its chain "
            "plus requested / ordered / received qty, a per-line order_status and "
            "receive_status, and completion %, with per-stage counts; "
            "(2) 'documents' — a document-centric tree (DocType -> documents -> item "
            "rows) including project-linked documents that never joined a chain. "
            "Use this to answer 'what materials are we waiting on for project X'. "
            "Quantities are per line and never inherited from a parent document: "
            "order_status is that line's own ordered-vs-requested state (Not Ordered / "
            "Partially Ordered / Ordered / Over Ordered), so a fully ordered line inside "
            "a partially ordered Material Request reads 'Ordered'. Only submitted "
            "Purchase Orders count toward ordered qty; anything on a draft order is "
            "reported separately as draft_ordered_qty."
        )
        self.category = "Project Management"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Purchase Order"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project docname (e.g. 'PROJ-0001')"},
                "view": {
                    "type": "string",
                    "enum": ["stage_summary", "documents"],
                    "default": "stage_summary",
                    "description": "Item/stage-centric rollup or document-centric tree",
                },
            },
            "required": ["project"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = arguments["project"]
        # The underlying feed is raw SQL with no permission checks, so gate on
        # the Project document explicitly (Purchase Order read is the class gate).
        require_doc_read("Project", project)

        from erpnext_enhancements.project_enhancements import (
            get_procurement_documents,
            get_procurement_status,
        )

        if (arguments.get("view") or "stage_summary") == "documents":
            return {
                "success": True,
                "project": project,
                "view": "documents",
                "document_groups": get_procurement_documents(project),
            }

        stages = get_procurement_status(project)
        summary = {
            "items_by_stage": {stage: len(rows) for stage, rows in stages.items()},
            "total_items": sum(len(rows) for rows in stages.values()),
            # `total_requested_qty` is new alongside the two below. It restores what the
            # old `total_ordered_qty` was accidentally carrying: that figure used to fall
            # back to the requested quantity whenever nothing had been ordered, so an
            # untouched line inflated it and the project looked fully ordered. It now
            # counts only quantity on submitted Purchase Orders, and the requested figure
            # is reported in its own right.
            "total_requested_qty": sum(
                row.get("requested_qty") or 0 for rows in stages.values() for row in rows
            ),
            "total_ordered_qty": sum(
                row.get("ordered_qty") or 0 for rows in stages.values() for row in rows
            ),
            "total_received_qty": sum(
                row.get("received_qty") or 0 for rows in stages.values() for row in rows
            ),
        }
        return {
            "success": True,
            "project": project,
            "view": "stage_summary",
            "summary": summary,
            "stages": stages,
        }


__all__ = ["ProjectProcurementStatus"]

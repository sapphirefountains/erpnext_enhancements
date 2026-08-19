"""item_naming_check — check a proposed Item against the naming schema (read-only).

Runs the duplicate search, the code-family and block-occupancy checks, and every
mechanical Item Name check through the pure
``inventory_enhancements.item_naming_rules``, and returns findings with a
STOP/FIX/PASS verdict. Nothing is written and nothing is enforced: there is no
``Item`` doc_event in this app, so this reports and a human acts.

It exists because the alternative is a model hand-authoring SQL for each check,
and that shipped wrong — an occupancy query anchored ``^PDT-[0-9]{4}$`` reports
PDT-0008 free while the only record of that product holds it. The traps are in
the pure module now, unit-tested against the literal production strings.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class ItemNamingCheck(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "item_naming_check"  # must match module filename
        self.description = (
            "Check an ERPNext Item Code and Item Name against the Sapphire "
            "Fountains Item Naming Schema — before the record is created, or on one that "
            "already exists — and return findings plus a STOP/FIX/PASS verdict. Read-only "
            "— it never creates or edits an Item, and nothing it reports blocks a save. "
            "Pass 'existing': true when re-checking an Item that is already in ERPNext. "
            "Pass 'item_code' (required) and, when you have one, 'item_name'; "
            "'item_group' and 'stock_uom' are checked when supplied. "
            "Returns: exact and normalised duplicate matches (with the normalisation "
            "function it used, because a duplicate count means nothing without it); "
            "the nearest existing records by name, scored, which is the only surface "
            "that catches the same physical part filed under two codes and two "
            "wordings; the code family; for PDT-/SRV- codes the occupied and free "
            "numbers in the relevant block; every mechanical name defect (case, "
            "separator, stray whitespace, trailing comma, GREY, the word INCH, "
            "schedule placement, unapproved or plural or brand-led CATEGORY); and the "
            "approved category vocabulary. "
            "It reports block occupancy and never allocates a number — the blocks are "
            "sparse and semantic, so which one a new product belongs in is a judgement "
            "about what the product is. Segments are returned positionally and are NOT "
            "classified: four of the seven have no decidable shape. Requires read "
            "permission on Item."
        )
        self.category = "Inventory"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Item"
        self.annotations = annotations_for(self.name)
        self.default_config = {"max_similar": 25}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "item_code": {
                    "type": "string",
                    "description": (
                        "The proposed Item Code, or the vendor part number exactly as "
                        "printed on the quote or invoice. Required. Pass a bare "
                        "'PDT-' or 'SRV-' code on its own to ask which numbers in that "
                        "block are free."
                    ),
                },
                "item_name": {
                    "type": "string",
                    "description": (
                        "The proposed Item Name. Omit only when you are asking about "
                        "code or block occupancy alone — without it the verdict can "
                        "never be PASS, because the name is what carries the schema."
                    ),
                },
                "item_group": {
                    "type": "string",
                    "description": "The intended Item Group, if known.",
                },
                "stock_uom": {
                    "type": "string",
                    "description": "The intended stock UOM, if known.",
                },
                "similar_limit": {
                    "type": "integer",
                    "description": "How many near-neighbour records to return (default 8).",
                },
                "existing": {
                    "type": "boolean",
                    "description": (
                        "True when this Item ALREADY EXISTS and you are re-checking it; "
                        "false (the default) when it is a new one being proposed. It "
                        "changes what an exact code match means: a collision for a "
                        "proposal, the record itself for a saved row. Set it true when "
                        "the user names an item that is already in ERPNext, or every "
                        "saved record reports as a duplicate of itself."
                    ),
                },
            },
            "required": ["item_code"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        item_code = (args.get("item_code") or "").strip()
        if not item_code:
            frappe.throw(_("An 'item_code' is required."), frappe.ValidationError)
        # The corpus read goes through frappe.get_list, which applies DocPerms and User
        # Permissions per row -- but the row count and the reference vocabulary do not,
        # so gate the whole tool on Item read rather than relying on the list filter.
        if not frappe.has_permission("Item", "read"):
            frappe.throw(_("You do not have read access to Items."), frappe.PermissionError)

        from erpnext_enhancements.assistant_tools._common import clamp_limit
        from erpnext_enhancements.inventory_enhancements.item_naming import (
            inspect_item_naming,
        )

        limit = clamp_limit(args.get("similar_limit"), 8, self.get_config().get("max_similar", 25))
        result = inspect_item_naming(
            item_code=item_code,
            item_name=args.get("item_name"),
            item_group=args.get("item_group"),
            stock_uom=args.get("stock_uom"),
            similar_limit=limit,
            existing=bool(args.get("existing")),
        )
        result.setdefault("item_code", item_code)
        return result


__all__ = ["ItemNamingCheck"]

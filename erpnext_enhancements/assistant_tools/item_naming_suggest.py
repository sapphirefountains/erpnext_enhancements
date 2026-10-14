"""item_naming_suggest — suggest a new Item's code family and POL-0602 name (read-only).

The assistant's door to the guided Item flow's AI suggest step (ER-2026-1320546, v1.694.0): from a
plain-words description (and, optionally, the vendor's part number), the same
``api.item_suggest.run_suggestion`` the guided page's *Describe it* card calls. It asks
Gemini through the app's own client, keeps only the fields of the answer's schema, runs them
through ``inventory_enhancements.item_naming_rules`` and the new-Item guard, and returns each
field with a reason and the rules' verdict. Nothing is written: no Item, no File.

Two things it never does, and says so in its description so a model does not paper over them:
it never gives a PDT- or SRV- number (the person takes one from the block list on the guided
page; ``item_naming_check`` reports which are free), and it never invents a vendor part number.

No photo, deliberately. The endpoint takes one, as base64 from the page or as a File the caller can
read, but a File named by an assistant call would need the AI gate's file denylist
(``_gate.DENYLIST_FILE_ARGUMENTS``, held to FAC's own tools), and a photo attached in the Triton
chat is a *Triton Chat Attachment*, which that denylist refuses anyway. The guided page is where a
photo goes in.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook; see the
package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class ItemNamingSuggest(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "item_naming_suggest"  # must match module filename
        self.description = (
            "Suggest how to code and name a NEW ERPNext Item by the Sapphire Fountains Item "
            "Naming Schema (POL-0602), from a plain-words description of the part. Read-only: it "
            "creates and edits nothing. Returns each suggested field with a short reason (the code "
            "family; the vendor part number when the person gave one; for a CON- consumable its "
            "group, descriptor and size; the seven name segments CATEGORY, SUB-CATEGORY, KEY "
            "FEATURE, MATERIAL, SIZE, RATING, PACKAGING; the Item Group and unit), the composed "
            "Item Code and Item Name, the naming rules' verdict and the new-Item guard's, and any "
            "existing Item that looks the same. It NEVER gives a PDT- or SRV- number: for a "
            "product or service the person picks the number from the block list on the guided "
            "Item page (/desk/guided-entry/Item/new). It never invents a vendor part number. "
            "Present the result as a suggestion for the person to check, then confirm the final "
            "code and name with item_naming_check. Requires permission to create Items."
        )
        self.category = "Inventory"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Item"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "What the item is, in the person's own words: what it does, its size, "
                        "material, rating, brand and how it is sold. Up to 1500 characters."
                    ),
                },
                "vendor_part_no": {
                    "type": "string",
                    "description": (
                        "The vendor's or manufacturer's part number exactly as printed, when the "
                        "person has one. It becomes the Item Code of a purchased part."
                    ),
                },
            },
            "required": ["description"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        description = args.get("description")
        # A model can send anything: a number, a list, an object. Only text is a description.
        description = description.strip() if isinstance(description, str) else ""
        if not description:
            frappe.throw(_("A 'description' of the item is required."), frappe.ValidationError)
        # The suggestion is for creating an Item, and each call costs a model call: the same gate
        # as the guided page's Suggest button (api.item_suggest.suggest_item).
        if not frappe.has_permission("Item", "create"):
            frappe.throw(_("You do not have permission to create Items."), frappe.PermissionError)

        from erpnext_enhancements.api.item_suggest import run_suggestion

        return run_suggestion(description=description, vendor_part_no=args.get("vendor_part_no"))


__all__ = ["ItemNamingSuggest"]

"""supplier_search — who sells this, or has sold it to us (read-only).

The assistant's door to the Supplier Finder (v1.722.0). It runs the same
``api.supplier_search.run_search`` that the ``/desk/supplier-finder`` page and the Item and
Purchase Order buttons call. Nik, 2026-10-22: "I know we need a pump, but I don't remember all our
pump suppliers." A search can be a kind of item ("pump"), a phrase ("submersible pump") or a model
or part number ("EQKT750"). Suppliers come back ranked by what we bought from them, quoted or
listed, and a matching Supplier Group or name only adds suppliers at the bottom.

The AI query expansion is off by default here: the calling assistant is a model already and can
rephrase the search itself. ``expand`` turns it on. Price paid is included only for someone who
may read Purchase Order, as on the page.

Only imported by frappe_assistant_core's tool loader via the assistant_tools hook; see the
package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class SupplierSearch(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "supplier_search"  # must match module filename
		self.description = (
			"Find which suppliers sell, or have sold Sapphire Fountains, a kind of item or a "
			"specific model. Read-only. Give it a general word ('pump'), a phrase ('submersible "
			"pump', '2 hp pool pump') or a model or vendor part number ('EQKT750', 'Grundfos SQ "
			"3-65'). Returns the matching Items and the suppliers ranked by evidence: purchase "
			"orders, invoices and receipts first (tier 'history', with the date and the last price "
			"paid, in its currency, from documents this user may read), then quotes, Item Supplier listings, default suppliers and buying prices "
			"(tier 'carries'), and last suppliers that only sit in a matching Supplier Group or "
			"have a matching name (tier 'group'). Duplicate supplier records are folded into one "
			"row and listed as 'aliases'. Each supplier has a plain 'reason'. 'suppliers_total' and "
			"'items_total' say how many matched in all, and 'hidden_note' names any purchase "
			"history this user may not read. Use this instead of "
			"filtering Supplier by supplier_group: most suppliers are in no group, or in one "
			"whose name does not say what they sell. Purchase history in ERPNext starts in "
			"October 2025. Requires read access to Supplier and Item."
		)
		self.category = "Inventory"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Supplier"
		self.annotations = annotations_for(self.name)
		self.inputSchema = {
			"type": "object",
			"properties": {
				"query": {
					"type": "string",
					"description": "What to look for: a kind of item, a model or a part number.",
				},
				"item_code": {
					"type": "string",
					"description": "An exact Item code, to list every supplier of that one Item.",
				},
				"expand": {
					"type": "boolean",
					"description": (
						"Also let the app's AI broaden a fuzzy search into related terms and "
						"Supplier Groups. Off by default."
					),
				},
				"limit": {
					"type": "integer",
					"description": "Suppliers to return, 1 to 40 (default 12).",
				},
			},
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		args = arguments or {}
		query = args.get("query")
		query = query.strip() if isinstance(query, str) else ""
		item_code = args.get("item_code")
		item_code = item_code.strip() if isinstance(item_code, str) else ""
		if not query and not item_code:
			frappe.throw(_("Give a 'query' or an 'item_code'."), frappe.ValidationError)
		for doctype in ("Supplier", "Item"):
			if not frappe.has_permission(doctype, "read"):
				frappe.throw(
					_("You do not have permission to read {0}.").format(doctype), frappe.PermissionError
				)

		from erpnext_enhancements.api.supplier_search import run_search

		limit = args.get("limit")
		answer = run_search(
			query,
			item_code=item_code or None,
			use_ai=bool(args.get("expand") is True),
			limit=limit if isinstance(limit, int) and not isinstance(limit, bool) else DEFAULT_LIMIT,
		)
		return compact(answer)


#: Suppliers returned when the caller names no limit. Every tool result is paid for in the
#: assistant's context: the page's 40 suppliers and 60 items came to about 89 KB (22k tokens).
DEFAULT_LIMIT = 12
#: Items listed per supplier, and matching items listed, in the tool's answer.
ITEMS_PER_SUPPLIER = 3
ITEMS_SHOWN = 15
#: Ranking internals a model does not need to read the answer.
_INTERNAL = ("score", "hits", "strict", "coverage", "group_match", "name_match", "description")


def _strip(row):
	return {k: v for k, v in row.items() if k not in _INTERNAL}


def compact(answer):
	"""The search's answer cut down for an assistant: suppliers keep their first
	ITEMS_PER_SUPPLIER items, at most ITEMS_SHOWN matching items are listed, and the ranking
	internals are dropped. The totals and the hidden-history note stay, so the model can say what it
	is not showing."""
	if not answer.get("ok"):
		return answer
	out = dict(answer)
	out["suppliers"] = [
		dict(_strip(s), items=[_strip(i) for i in s.get("items", [])[:ITEMS_PER_SUPPLIER]])
		for s in answer.get("suppliers", [])
	]
	out["items"] = [_strip(i) for i in answer.get("items", [])[:ITEMS_SHOWN]]
	out["groups"] = [_strip(g) for g in answer.get("groups", [])]
	return out


__all__ = ["SupplierSearch"]

"""The Reorder Board's endpoints (v1.730.0, ER-2026-1323810; TASK-2026-02923, -02925).

``/desk/reorder-board`` (``inventory_enhancements/page/reorder_board``) calls these four. They are
thin: who may is checked here and in ``inventory_enhancements.reorder_board``, which does the
reads and the writes, and the judgements are ``inventory_enhancements.reorder_rules``.

* :func:`get_board` (GET): items at or below their reorder point with how much to buy, the
  snoozed ones, and every open request for a rule's item from Material Request to receipt.
* :func:`create_material_requests` (POST): Draft Material Requests for the picked lines, one per
  company, inserted as the caller. Idempotent on ``client_ref``.
* :func:`snooze_line` / :func:`unsnooze_line` (POST).
* :func:`get_reorder_suggestions` (GET, v1.832.0): the Reorder levels tab: each rule with a level and
  quantity suggested from real usage and the basis (``inventory_enhancements.reorder_suggestions``).
* :func:`apply_reorder_suggestion` (POST, v1.832.0): write one rule's Reorder at and Buy, as the
  caller, for Purchase Manager, Stock Manager and System Manager with Item write.

A refusal the person can act on comes back as ``{ok: False, message}`` rather than a raised
error: a caught ``frappe.throw`` still shows Frappe's modal over the page. A permission refusal
does raise (``PermissionError``), as every endpoint here does for a caller without the role.

Lives apart from ``api/item_flow.py``, which the task text named, because another change in
flight edits that file. Tabs (new file).
"""

import frappe

from erpnext_enhancements.inventory_enhancements import reorder_board as board
from erpnext_enhancements.inventory_enhancements import reorder_suggestions as suggestions


# GET from the page (sf_kit's quiet path); POST too, because sf_kit's stand-in (a Desk where the kit
# did not load) goes through frappe.call, which posts. It writes nothing either way.
@frappe.whitelist(methods=["GET", "POST"])
def get_board():
	board.require_board_role()
	return board.page_data()


@frappe.whitelist(methods=["POST"])
def create_material_requests(lines=None, client_ref=None):
	try:
		return board.create_requests(lines, client_ref)
	except board.BoardError as e:
		return {"ok": False, "message": str(e)}


@frappe.whitelist(methods=["POST"])
def snooze_line(item_code=None, warehouse=None, kind=None, reason=None):
	try:
		return board.snooze(item_code, warehouse, kind, reason)
	except board.BoardError as e:
		return {"ok": False, "message": str(e)}


@frappe.whitelist(methods=["POST"])
def unsnooze_line(item_code=None, warehouse=None):
	try:
		return board.unsnooze(item_code, warehouse)
	except board.BoardError as e:
		return {"ok": False, "message": str(e)}


# GET and POST for the same reason as get_board: it writes nothing either way.
@frappe.whitelist(methods=["GET", "POST"])
def get_reorder_suggestions(days=None):
	board.require_board_role()
	return suggestions.page_data(days)


@frappe.whitelist(methods=["POST"])
def apply_reorder_suggestion(item_code=None, warehouse=None, modified=None, level=None, qty=None):
	try:
		return suggestions.apply(item_code, warehouse, modified, level, qty)
	except board.BoardError as e:
		return {"ok": False, "message": str(e)}

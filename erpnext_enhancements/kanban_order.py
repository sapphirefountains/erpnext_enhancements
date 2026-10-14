"""The Kanban board's "sync the column order" call, without the status writes.

**What went wrong.** Every time a Kanban board opens, frappe v16.50.0's board ``init()`` calls
``update_order`` with each column's card list, and the server walks that list and runs
``frappe.set_value(doctype, card, field, column)`` for every card whose stored value is not the
column it is listed under. Up to 16.49 the client built those lists from the cards drawn on
screen, so a card was only ever listed under its own column and the write never fired. 16.50.0
builds them from the board's *saved* order instead ("saved order plus loaded cards not in it
yet"). A saved order is only rewritten by a drag, so a status changed anywhere else -- the form,
the Guided Entry page, an import, a hook -- leaves the card's name behind in its old column. On
the next board open the card is listed twice, and the server writes both:

	update_order({"Needs Analysis": [..., "CRM-OPP-2026-00178"], ..., "Closed Won": ["CRM-OPP-2026-00178", ...]})
	  -> set_value(status = "Needs Analysis")   # a real save, every hook
	  -> set_value(status = "Closed Won")       # and another: the deal is "won" again

Both are full saves, and the second is a genuine transition into Closed Won, so the "Email
Team on Opportunity Won" Value Change notification went to four group inboxes on every board
open. CRM-OPP-2026-00178 was moved Needs Analysis -> Negotiation/Review -> Closed Won on the form
on 2026-10-12, and on 10-13 sent that email 24 more times, one per board open by Brian or Nik.
Each pair of saves also restamped ``custom_stage_changed_on``, and once v1.627.0's stage
probability shipped, left the won deal at Needs Analysis' 25%. And the flip is not always a
round trip: the column listed *last* wins, so a card moved backwards on the form
(Negotiation/Review -> Needs Analysis, say) would be silently put back. The server also
re-saves every name it was sent, so the stale entry is never cleared.

**The fix.** This replaces the endpoint (``override_whitelisted_methods``) with one that only
orders: each column keeps the cards whose stored value *is* that column, in the order sent,
once each, and everything else -- a stale entry, a duplicate, a deleted document -- is dropped
from the saved order rather than written onto the document. Nothing in 16.50's client needs the
write: a drag goes through ``update_order_for_single_card``, which sets the dragged card's value
itself, and ``update_order`` is called from ``init()`` alone. A card listed under the wrong
column is not drawn there either (``get_column_display_order`` filters by the stored value), so
dropping it loses no position anyone can see. The first board open after this ships prunes the
stale entry that started it.

It is a reimplementation, not a wrapper around frappe's function, because the defect is the
loop body itself: pre-filtering the list and then calling frappe's ``update_order`` would still
write any card whose value changed between the two reads. The permission checks, the board save
and the realtime publish mirror frappe's v16.50.0 function line for line.

Read from ``frappe/desk/doctype/kanban_board/kanban_board.py`` and
``frappe/public/js/frappe/views/kanban/kanban_board.bundle.js`` at tags v16.49.0 and v16.50.0
(``git show v16.50.0:<path>`` -- the sibling ``../frappe`` checkout is on ``develop``).
Remove once frappe stops writing document values from this endpoint.
"""

import json

import frappe


@frappe.whitelist()
def update_order(board_name: str, order: str | dict):
	"""frappe's ``update_order``, minus the ``set_value`` on every card in the wrong column.

	Returns ``(board, updated_cards)`` like frappe's; ``updated_cards`` is always empty, since
	no document is changed, and the client's callback handles an empty list.
	"""
	board = frappe.get_doc("Kanban Board", board_name)
	# The same two checks as frappe's: read on the board, write on the doctype. A user without
	# write gets the board back untouched, exactly as there.
	board.check_permission("read")
	doctype = board.reference_doctype

	if not frappe.has_permission(doctype, "write"):
		return board, []

	order_dict = frappe.parse_json(order) or {}
	stored = stored_columns(doctype, board.field_name, order_dict)

	for col_name, cards in order_dict.items():
		for column in board.columns:
			if column.column_name == col_name:
				column.order = json.dumps(cards_in_column(cards, col_name, stored))

	saved = board.save(ignore_permissions=True)
	_publish_board_update(saved)
	return saved, []


def cards_in_column(cards, col_name, stored):
	"""``cards`` reduced to those whose stored value is ``col_name``, in order, once each.

	``stored`` maps a document name to its current value of the board's field; a name missing
	from it no longer exists.
	"""
	kept = []
	seen = set()
	for card in cards or []:
		if card in seen or card not in stored or stored[card] != col_name:
			continue
		seen.add(card)
		kept.append(card)
	return kept


def stored_columns(doctype, fieldname, order_dict):
	"""``{name: value of fieldname}`` for every card named in ``order_dict``, in one query.

	frappe's version reads each card with its own ``get_value``; the Opportunity board sends
	upwards of 800 names (every saved column order, Lost and Closed Won included), so one
	``IN`` query replaces that many round trips.
	"""
	names = list({card for cards in order_dict.values() for card in (cards or [])})
	if not names:
		return {}
	rows = frappe.get_all(
		doctype,
		filters={"name": ("in", names)},
		fields=["name", fieldname],
		limit_page_length=0,
	)
	return {row.get("name"): row.get(fieldname) for row in rows}


def _publish_board_update(board):
	"""Tell other open tabs of this board to refresh, as frappe's ``update_order`` does.

	frappe added ``publish_kanban_board_update`` alongside the 16.50 board, so it is looked up
	rather than imported: on a frappe without it the order still saves, it just is not pushed.
	"""
	from frappe.desk.doctype.kanban_board import kanban_board

	publish = getattr(kanban_board, "publish_kanban_board_update", None)
	if publish:
		publish(board)

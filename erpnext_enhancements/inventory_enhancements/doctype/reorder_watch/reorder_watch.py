# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Reorder Watch (v1.730.0): the Reorder Board's memory of one item and warehouse.

A row exists while the item is at or below its reorder point at that warehouse
(``inventory_enhancements.reorder_board.sync_watch``, hourly, inserts and deletes them). It holds
what the documents cannot:

* ``below_since``: when the board first saw it below (to the hour);
* ``alerted_on``: when the daily digest named it, so it is named once;
* the snooze: ``snooze_kind`` (1 week, 1 month, or until stock drops further), its end
  (``snoozed_until``) or the projected qty it must fall below (``snooze_projected_qty``), who
  snoozed it, when, and why;
* ``last_request`` / ``last_request_ref``: the Draft Material Request the board made for it and the
  page's request id, so a retried click returns that request instead of making a second one.

It is **not** a tracking table for requests and orders: where a request is (ordered, received)
is read from the Material Request, Purchase Order and Purchase Receipt links every time.
``last_request`` is Data, not a Link, so a buyer can still delete the Draft request.

The name is the item and warehouse (:func:`watch_name`), so there is never a second row for one
rule and a racing insert fails rather than duplicates.
"""

import hashlib

from frappe.model.document import Document

_MAX_NAME = 140


def watch_name(item_code, warehouse):
	"""``<item_code>::<warehouse>``, or a stable hash of it when that would not fit in a name."""
	key = f"{item_code or ''}::{warehouse or ''}"
	if len(key) <= _MAX_NAME:
		return key
	return "RW-" + hashlib.sha1(key.encode("utf-8")).hexdigest()


class ReorderWatch(Document):
	def autoname(self):
		self.name = watch_name(self.item_code, self.warehouse)

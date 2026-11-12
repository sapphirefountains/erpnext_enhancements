# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Maintain Stock Decision (v1.809.0): what the *Switch Items to Maintain Stock*
page knows about one item that the Item itself cannot say.

A row exists only for an item somebody chose something for, or that was queued to switch:

* ``decision``: a person's choice that beats the name rule (``maintain_stock_bulk_rules``):
  switch it although its name reads like a service, or keep it non-stock although it reads like
  a part. Blank means follow the rule.
* ``status``: where it is in an apply. ``Queued`` rows are the background job's work list, so a
  deploy that flushes the job queue loses nothing: the rows are still Queued, and the scheduler
  starts the job again (``maintain_stock_bulk.resume_queue``).
* ``switched_on`` and ``invoices_before_switch``: read by the Delivery Note guard
  (``maintain_stock.refuse_delivering_old_invoices``) for an item switched although Sales
  Invoices used it.

**The row is found by its item, never by its name.** ``item_code`` is a Link, so renaming an Item
carries the row along, and the name is a hash, so the rename has nothing else to keep in step.
Merging two Items can leave two rows for the survivor; every reader takes the one that matters
(``maintain_stock_bulk_rules.best_row``). Deleting an Item removes its rows first
(``maintain_stock_bulk.forget_item``, the Item's ``on_trash``), so a row never stands in the way
of the delete. The first version named the row by the item code with a unique index: a rename
then left the name behind, a merge hit the index, and a delete was refused by the link.

Written by ``maintain_stock_bulk`` only; every field is read-only on the form.
"""

from frappe.model.document import Document


class MaintainStockDecision(Document):
	pass

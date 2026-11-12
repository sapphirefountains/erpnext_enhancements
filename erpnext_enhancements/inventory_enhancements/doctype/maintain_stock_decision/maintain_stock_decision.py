# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Maintain Stock Decision (v1.803.0): what the *Switch Items to Maintain Stock*
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

The name is the item code, so there is never a second row for one item. Written by
``maintain_stock_bulk`` only; every field is read-only on the form.
"""

from frappe.model.document import Document


class MaintainStockDecision(Document):
	pass

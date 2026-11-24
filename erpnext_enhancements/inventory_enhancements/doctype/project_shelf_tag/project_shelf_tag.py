# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Project Shelf Tag doctype (v1.842.0).

Which job a movement on the Project Shelf belongs to, when the movement itself carries no
project: a count (a Stock Reconciliation, which has no project in ERPNext) or a transfer made
by hand with no job. One row is one job's share of one document's change to one item at the
shelf: ``qty`` negative for units that left, positive for units found.

**It posts nothing.** No GL entry, no stock ledger row, no change to any document. The count
it names posts exactly as it always did: the difference to the stock adjustment account, with
no project. The only reader is ``project_shelf_rules.shelf_summary``, which uses a tag to share
out that movement's own change among the jobs it names and never more than the change, so a
tag cannot create a claim on stock that is not there. A tag counts only while its document's
ledger rows are live: an undone or canceled count drops its tags without anyone touching them.

**Written by code only** (``in_create``): the Bin Manager's count and "Change whose"
(``bin_assignments``), the Stock Reconciliation submit hook
(``project_shelf.tag_reconciliation``) and the cleanup patch
(``patches/settle_project_shelf_untagged``). Every field is read-only on the form. Who named
the job is ``source``; the rules are in ``project_shelf_rules`` (``TAG_SOURCES``).

Indentation is tabs (new file).
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules


class ProjectShelfTag(Document):
	def validate(self):
		if abs(float(self.qty or 0)) <= rules.TOLERANCE:
			frappe.throw(_("A Project Shelf Tag needs a quantity other than 0."))
		if (self.source or "") not in rules.TAG_SOURCES:
			frappe.throw(_("Named By must be one of {0}.").format(", ".join(rules.TAG_SOURCES)))

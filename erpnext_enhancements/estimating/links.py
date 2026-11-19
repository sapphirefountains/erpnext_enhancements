# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Let an Estimate and its Quotation be deleted, either one first.

The two Link each other: ``Estimate.quotation`` names the quotation it issued, and the fixture field
``Quotation.custom_estimate`` names the estimate back. ``frappe.delete_doc`` refuses to delete
anything another row Links to (``LinkExistsError``), so without this neither could ever be deleted:
each blocks the other, and the test records made on prod would be there for good.

``delete_doc`` calls ``on_trash`` **before** it checks links (frappe v16.56.0
``model/delete_doc.py``: ``run_method("on_trash")``, then ``check_if_doc_is_linked``), in the same
transaction, so clearing the pointing fields here is what lets the delete through. Nothing is forced:
any *other* record that Links to either one still refuses the delete, as it should.

* Deleting a **Quotation** (a draft, or a cancelled one) clears ``quotation`` on every Estimate that
  names it, and an estimate that was "Issued" goes back to "Draft", because what it issued is gone.
* Deleting an **Estimate** clears ``custom_estimate`` on every Quotation that names it, submitted
  ones included (it is a read-only back-reference, not part of what was quoted), and clears
  ``previous_revision`` on the revision that followed it, so revisions can be deleted in any order.
  If it was the newest revision, the one before it is editable again (Superseded becomes Issued when
  it still has a quotation, else Draft): otherwise deleting the newest would leave nothing editable.
"""

import frappe


def _estimates_ready():
	return frappe.db.table_exists("Estimate")


def release_quotation(doc, method=None):
	"""Quotation ``on_trash`` (hooks.py doc_events): unlink it from the Estimates that issued it."""
	if not doc.name or not _estimates_ready():
		return
	for est in frappe.get_all("Estimate", filters={"quotation": doc.name}, fields=["name", "status"]):
		values = {"quotation": None}
		if est.status == "Issued":
			values["status"] = "Draft"
		frappe.db.set_value("Estimate", est.name, values)


def release_estimate(doc):
	"""Estimate ``on_trash``: unlink it from its Quotations and from the revision after it."""
	if not doc.name:
		return
	if frappe.get_meta("Quotation").has_field("custom_estimate"):
		for name in frappe.get_all("Quotation", filters={"custom_estimate": doc.name}, pluck="name"):
			frappe.db.set_value("Quotation", name, "custom_estimate", None, update_modified=False)
	for name in frappe.get_all("Estimate", filters={"previous_revision": doc.name}, pluck="name"):
		frappe.db.set_value("Estimate", name, "previous_revision", None, update_modified=False)
	prev = doc.get("previous_revision")
	if prev and doc.get("status") != "Superseded":
		row = frappe.db.get_value("Estimate", prev, ["status", "quotation"], as_dict=True)
		if row and row.status == "Superseded":
			frappe.db.set_value("Estimate", prev, "status", "Issued" if row.quotation else "Draft")

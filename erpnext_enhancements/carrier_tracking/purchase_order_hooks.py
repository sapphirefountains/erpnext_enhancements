# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What happens to an order's tracking numbers when the order is deleted or amended (review round 1,
item 2; the Sapphire Manager's decision).

* **A package never blocks deleting an order.** Frappe's ``delete_doc`` refuses to delete a document
  that a non-canceled Dynamic Link still points at (``check_if_doc_is_dynamically_linked``,
  ``frappe/model/delete_doc.py`` at v16.56.0). It runs ``on_trash`` first (line 177) and the link
  checks after (lines 184-185), so ``on_trash`` here removes the order's packages and the delete goes
  through. A draft *amendment* being deleted gives its packages back to the order it amended instead,
  since they came from there.
* **An amendment takes the packages with it.** Canceling an order and amending it makes a new order
  for the same goods, and the boxes on the truck are the same boxes. ``after_insert`` of the amended
  order (the moment it exists) moves every package of the canceled order to it, **if the person
  amending can read the canceled order** (``amended_from`` can be set through REST to any canceled
  order). Safe to run twice: a second run finds nothing left on the canceled order. A number already
  on the amendment is left on the canceled order rather than doubled.
* **A canceled order that was never amended keeps its packages**, shown read-only on its form and no
  longer polled (the poller reads submitted orders only).

Both hooks are defensive (CLAUDE.md): they return at once on a site without the Tracked Package
table, read custom fields with ``getattr``, never raise into the order's delete or insert, and log
a failure instead. A failed ``on_trash`` cleanup leaves Frappe's own link check to refuse the delete
in its usual words, which is no worse than before this module existed.
"""

import frappe

DOCTYPE = "Tracked Package"
ORDER = "Purchase Order"


def _installed():
	try:
		return bool(frappe.db.exists("DocType", DOCTYPE))
	except Exception:
		return False


def _packages(order_name):
	return frappe.get_all(
		DOCTYPE,
		filters={"reference_doctype": ORDER, "reference_name": order_name},
		fields=["name", "tracking_number"],
		limit_page_length=0,
	)


def move_packages(from_order, to_order):
	"""Move every package of ``from_order`` to ``to_order``, except a number ``to_order`` already has.
	Returns how many moved. Writes with ``db.set_value`` (no validate: the number is not changing)."""
	if not from_order or not to_order or from_order == to_order:
		return 0
	already = {row.tracking_number for row in _packages(to_order)}
	supplier = frappe.db.get_value(ORDER, to_order, "supplier")
	moved = 0
	for row in _packages(from_order):
		if row.tracking_number in already:
			continue
		frappe.db.set_value(
			DOCTYPE, row.name, {"reference_name": to_order, "supplier": supplier}, update_modified=False
		)
		already.add(row.tracking_number)
		moved += 1
	return moved


def on_trash(doc, method=None):
	"""Purchase Order ``on_trash``: remove its packages, or hand an amendment's back. Never raises."""
	try:
		if not _installed():
			return
		rows = _packages(doc.name)
		if not rows:
			return
		amended_from = getattr(doc, "amended_from", None) or ""
		if amended_from and frappe.db.exists(ORDER, amended_from):
			move_packages(doc.name, amended_from)
			rows = _packages(doc.name)
		for row in rows:
			frappe.delete_doc(DOCTYPE, row.name, ignore_permissions=True, force=True)
	except Exception:
		frappe.log_error(title="Package tracking: could not clear the packages of a deleted order")


def after_insert(doc, method=None):
	"""Purchase Order ``after_insert``: an amendment takes its canceled original's packages. Never raises."""
	try:
		amended_from = getattr(doc, "amended_from", None) or ""
		if not amended_from or not _installed():
			return
		# Frappe checks only that amended_from exists and is canceled, and a REST insert can set it to
		# any canceled order. Moving packages needs read on the order they come from, so nobody can
		# take another order's packages by naming it (review round 2).
		if not frappe.has_permission(ORDER, "read", doc=amended_from):
			return
		move_packages(amended_from, doc.name)
	except Exception:
		frappe.log_error(title="Package tracking: could not move packages to an amended order")

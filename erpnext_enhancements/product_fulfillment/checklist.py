# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Build Checklist page's endpoints: one Work Order, its steps, its builder and its units.

The page (``page/build_checklist``) is the phone- and tablet-sized view of a unit's build: big tap
targets for each step, who is building it, and once it is built, its serial number, label and test.
Everything it shows comes from ``get_build``; everything it changes goes through ``tick`` and
``claim_build``, which write single rows with ``frappe.db.set_value``.

Why not save the Work Order: it is submitted while the unit is being built, and Frappe's "Update"
on a submitted document checks submit as well as write and rewrites every child row the form did
not load (CLAUDE.md, frappe-update-after-submit-needs-submit). A tick from a phone must not race
somebody's open Work Order form. So each endpoint checks write on the Work Order itself and writes
the one row it means. ``custom_built_by`` and the step fields are ``allow_on_submit`` so the Desk
form can show and correct them too.

Who built it: ``claim_build`` records the person pressing it, and the first tick records the person
ticking when nobody is recorded. Once the Manufacture entry is submitted, the builder is stamped on
each serial number (``serials.stamp_builders``) and is no longer changed from here.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.product_fulfillment import qc_gate, rules
from erpnext_enhancements.product_fulfillment.settings import has_column

WORK_ORDER = "Work Order"
STEP = "Work Order Build Step"


def _load(work_order):
	if not frappe.db.exists(WORK_ORDER, work_order):
		frappe.throw(_("Work Order {0} not found.").format(work_order), frappe.DoesNotExistError)
	doc = frappe.get_doc(WORK_ORDER, work_order)
	doc.check_permission("read")
	return doc


def _can_edit(doc):
	return int(doc.docstatus or 0) < 2 and bool(frappe.has_permission(WORK_ORDER, "write", doc=doc))


def _full_name(user):
	if not user:
		return ""
	return frappe.db.get_value("User", user, "full_name") or user


def _manufactured(doc):
	return bool(
		frappe.db.exists("Stock Entry", {"work_order": doc.name, "purpose": "Manufacture", "docstatus": 1})
	)


@frappe.whitelist()
def get_build(work_order):
	"""Everything the Build Checklist page shows for one Work Order."""
	doc = _load(work_order)
	has_fields = has_column(WORK_ORDER, "custom_built_by")
	steps = []
	rows = (doc.get("custom_build_steps") or ()) if has_fields else ()
	for row in rows:
		steps.append(
			{
				"name": row.name,
				"idx": row.idx,
				"section_title": row.section_title or "",
				"step_type": row.step_type or rules.STEP_BUILD,
				"instruction": row.instruction or "",
				"done": cint(row.done),
				"done_by": row.done_by or "",
				"done_by_name": _full_name(row.done_by),
				"done_at": str(row.done_at or ""),
			}
		)
	built_by = doc.get("custom_built_by") if has_fields else None
	customer = None
	if doc.sales_order and frappe.has_permission("Sales Order", "read", doc=doc.sales_order):
		customer = frappe.db.get_value("Sales Order", doc.sales_order, "customer_name")
	return {
		"name": doc.name,
		"status": doc.status,
		"docstatus": cint(doc.docstatus),
		"production_item": doc.production_item,
		"item_name": doc.item_name,
		"qty": doc.qty,
		"produced_qty": doc.produced_qty,
		"sales_order": doc.sales_order,
		"customer_name": customer,
		"expected_delivery_date": str(doc.expected_delivery_date or ""),
		"configuration": doc.get("custom_build_configuration") if has_fields else None,
		"built_by": built_by or "",
		"built_by_name": _full_name(built_by),
		"manufactured": _manufactured(doc),
		"steps": steps,
		"progress": rules.checklist_progress(steps),
		"units": qc_gate.units_of_work_order(doc.name),
		"can_edit": _can_edit(doc),
		"can_test": bool(frappe.has_permission("Quality Inspection", "create")),
		"is_manager": _is_manager(),
		"me": frappe.session.user,
		"me_name": _full_name(frappe.session.user),
	}


@frappe.whitelist(methods=["POST"])
def tick(work_order, step, done=1):
	"""Tick or untick one Build step. The first tick records the ticker as the builder."""
	doc = _load(work_order)
	if not _can_edit(doc):
		frappe.throw(_("You cannot change this Work Order."), frappe.PermissionError)
	row = next((r for r in doc.get("custom_build_steps") or () if r.name == step), None)
	if row is None:
		frappe.throw(_("That step is not on Work Order {0}.").format(doc.name))
	if (row.step_type or rules.STEP_BUILD) == rules.STEP_QC:
		frappe.throw(_("QC steps are the tester's notes; the test itself is the Quality Inspection."))
	done = cint(done)
	values = {
		"done": done,
		"done_by": frappe.session.user if done else None,
		"done_at": now_datetime() if done else None,
	}
	frappe.db.set_value(STEP, row.name, values, update_modified=False)
	if done and not doc.get("custom_built_by") and not _manufactured(doc):
		frappe.db.set_value(
			WORK_ORDER, doc.name, "custom_built_by", frappe.session.user, update_modified=False
		)
	return get_build(work_order)


@frappe.whitelist(methods=["POST"])
def claim_build(work_order):
	""" "I'm building this": record the person pressing it as the unit's builder.

	Refused once the unit is built (a submitted Manufacture entry): the builder is on its serial
	number by then, and changing it here would let the person who built it sign its test.
	"""
	doc = _load(work_order)
	if not _can_edit(doc):
		frappe.throw(_("You cannot change this Work Order."), frappe.PermissionError)
	if _manufactured(doc):
		frappe.throw(_("This unit is already built; its serial number records who built it."))
	current = doc.get("custom_built_by")
	if current and not rules.same_person(current, frappe.session.user) and not _is_manager():
		# The builder is what keeps the builder from testing the unit, so one person cannot quietly
		# take another's name off it. A manager can correct a mistake.
		frappe.throw(
			_("{0} is recorded as building this unit. Ask a Manufacturing Manager to change it.").format(
				_full_name(current)
			)
		)
	frappe.db.set_value(WORK_ORDER, doc.name, "custom_built_by", frappe.session.user, update_modified=False)
	if current and not rules.same_person(current, frappe.session.user):
		doc.add_comment(
			"Info",
			_("Builder changed from {0} to {1}.").format(
				_full_name(current), _full_name(frappe.session.user)
			),
		)
	return get_build(work_order)


def _is_manager():
	return bool({"Manufacturing Manager", "System Manager"} & set(frappe.get_roles()))

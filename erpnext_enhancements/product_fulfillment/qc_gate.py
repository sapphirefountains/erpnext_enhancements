# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The pre-shipment test and the gate that holds a Delivery Note until it passes (TASK-2026-03230).

**The test is ERPNext's own Quality Inspection** (ADR 0019): one per unit, ``item_serial_no`` the
unit's serial number, ``reference_type`` the Stock Entry that made the unit, on the template Product
Build Settings names. ``inspected_by`` is the tester. ADR 0012 keeps project inspections off
Quality Inspection because a project inspection has no item, no stock reference and no sample; a
product unit has all three, which is what the record was built for.

**The gate is ours, not ERPNext's ``inspection_required_before_delivery``.** That flag checks one
Quality Inspection per Delivery Note line, linked on the line and made against that Delivery Note.
It cannot see serial numbers (a line of two units passes with one inspection), and it would make
every unit wait for a Delivery Note draft before it could be tested, including the built-ahead units
on the shelf, which are tested when they are built. So configured Items leave the flag off, and
``delivery_note_before_submit`` checks every serial instead (``rules.delivery_gate``).

Hooks, all on core doctypes:

* ``quality_inspection_before_validate``: for a unit's test made against a Stock Entry, names the
  entry's row that holds the serial (``child_row_reference``), so a re-test after a failure is not
  refused with ERPNext's "every row already has a Quality Inspection". Tidies Pass and N/A readings.
* ``quality_inspection_validate``: the serial belongs to the Item, the tester is not the builder,
  and the overall status follows the readings (one Rejected reading rejects the unit, even on a
  manual inspection).
* ``quality_inspection_before_submit``: the person submitting is the tester it names. Submitting is
  the signature.
* ``delivery_note_before_submit``: the gate.

``start_test`` (whitelisted) makes a draft test for one serial, prefilled, as the person asking.
"""

import frappe
from frappe import _
from frappe.utils import cint, nowdate

from erpnext_enhancements.product_fulfillment import rules
from erpnext_enhancements.product_fulfillment.settings import get_settings, has_column, item_facts, needs_test

QI = "Quality Inspection"
QI_FIELDS = [
	"name",
	"item_serial_no",
	"item_code",
	"status",
	"docstatus",
	"inspected_by",
	"report_date",
	"creation",
	"quality_inspection_template",
]


# ---------------------------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------------------------


def tests_by_serial(serials):
	"""``{serial: [test, ...]}``: the Quality Inspections made for each serial, drafts included."""
	serials = sorted({s for s in serials or () if s})
	out = {s: [] for s in serials}
	if not serials:
		return out
	for row in frappe.get_all(
		QI, filters={"item_serial_no": ("in", serials), "docstatus": ("<", 2)}, fields=QI_FIELDS
	):
		out.setdefault(row.item_serial_no, []).append(row)
	return out


def builders_of(serials):
	"""``{serial: user}`` from ``Serial No.custom_built_by``."""
	serials = sorted({s for s in serials or () if s})
	if not serials or not has_column("Serial No", "custom_built_by"):
		return {}
	rows = frappe.get_all("Serial No", filters={"name": ("in", serials)}, fields=["name", "custom_built_by"])
	return {row.name: row.custom_built_by for row in rows}


def unit_states(serials, template=None):
	"""``{serial: {"state", "test", "tested_by", "built_by"}}`` for each serial."""
	tests = tests_by_serial(serials)
	builders = builders_of(serials)
	items = dict(
		frappe.get_all(
			"Serial No",
			filters={"name": ("in", list(tests) or [""])},
			fields=["name", "item_code"],
			as_list=True,
		)
	)
	out = {}
	for serial in tests:
		latest = rules.latest_test(tests[serial], items.get(serial), template)
		out[serial] = {
			"state": rules.unit_state(tests[serial], builders.get(serial), items.get(serial), template),
			"test": latest.name if latest else None,
			"tested_by": latest.inspected_by if latest else None,
			"built_by": builders.get(serial),
			"drafts": [t.name for t in tests[serial] if cint(t.docstatus) == 0],
		}
	return out


def serials_of_work_order(work_order):
	"""The serial numbers a Work Order's submitted Manufacture entries made."""
	from erpnext_enhancements.product_fulfillment.serials import serials_of_row

	serials = set(frappe.get_all("Serial No", filters={"work_order": work_order}, pluck="name"))
	for entry in frappe.get_all(
		"Stock Entry",
		filters={"work_order": work_order, "purpose": "Manufacture", "docstatus": 1},
		pluck="name",
	):
		for row in frappe.get_all(
			"Stock Entry Detail",
			filters={"parent": entry, "is_finished_item": 1},
			fields=["serial_no", "serial_and_batch_bundle"],
		):
			serials.update(serials_of_row(row))
	return sorted(serials)


def units_of_work_order(work_order):
	"""The Build Checklist page's units: each serial, where its test stands, and who did what."""
	serials = serials_of_work_order(work_order)
	if not serials:
		return []
	states = unit_states(serials, get_settings().test_template or None)
	return [dict(serial=serial, **states.get(serial, {"state": rules.UNIT_UNTESTED})) for serial in serials]


# ---------------------------------------------------------------------------------------------
# Quality Inspection hooks
# ---------------------------------------------------------------------------------------------


def _is_unit_test(doc):
	"""A Quality Inspection of a configured product unit (the only kind these hooks touch)."""
	if not doc.get("item_code"):
		return False
	return needs_test(item_facts([doc.item_code]).get(doc.item_code))


def _tidy_reading(value):
	text = str(value or "").strip()
	lowered = text.lower()
	if lowered in ("pass", "passed", "ok"):
		return "Pass"
	if lowered in ("n/a", "na", "n.a.", "not applicable"):
		return "N/A"
	if lowered in ("fail", "failed"):
		return "Fail"
	return text


def quality_inspection_before_validate(doc, method=None):
	"""Point a unit's test at the Stock Entry row holding its serial, and tidy Pass / N/A readings."""
	if not _is_unit_test(doc):
		return
	for reading in doc.get("readings") or ():
		if not cint(reading.get("numeric")) and reading.get("reading_value"):
			reading.reading_value = _tidy_reading(reading.reading_value)
	if (
		doc.get("reference_type") != "Stock Entry"
		or not doc.get("reference_name")
		or doc.get("child_row_reference")
	):
		return
	serial = doc.get("item_serial_no")
	if not serial:
		return
	from erpnext_enhancements.product_fulfillment.serials import serials_of_row

	for row in frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": doc.reference_name, "item_code": doc.item_code},
		fields=["name", "serial_no", "serial_and_batch_bundle"],
		order_by="idx asc",
	):
		if serial in serials_of_row(row):
			doc.child_row_reference = row.name
			return


def quality_inspection_validate(doc, method=None):
	"""A unit's test: its serial, someone other than the builder, and a status that follows the
	readings."""
	if not _is_unit_test(doc):
		return
	serial = doc.get("item_serial_no")
	if not serial:
		frappe.throw(_("Pick the serial number of the unit this test is for."), title=_("Pre-Shipment Test"))
	serial_item = frappe.db.get_value("Serial No", serial, "item_code")
	if serial_item and serial_item != doc.item_code:
		frappe.throw(
			_("Serial number {0} is a unit of {1}, not {2}.").format(serial, serial_item, doc.item_code),
			title=_("Pre-Shipment Test"),
		)
	builder = builders_of([serial]).get(serial)
	problem = rules.tester_problem(doc.get("inspected_by"), builder)
	if problem == rules.TESTER_IS_BUILDER:
		frappe.throw(
			_(
				"{0} built unit {1}, so someone else must test it. Logan and Nathan check each other's "
				"units: the tester is never the builder."
			).format(frappe.bold(builder), serial),
			title=_("Pre-Shipment Test"),
		)
	# No builder on record yet is refused at submit (before_submit says how to fix it); a draft may
	# wait for it.
	#
	# ERPNext sets the overall status from the readings only when Manual Inspection is off. On a
	# manual inspection the tester picks it, and a unit with a Rejected reading must not read
	# Accepted because the overall box was left at its default.
	if any(r.get("status") == rules.REJECTED for r in doc.get("readings") or ()):
		doc.status = rules.REJECTED


def quality_inspection_before_submit(doc, method=None):
	"""Submitting is the signature: the person submitting is the tester, and a builder is on record."""
	if not _is_unit_test(doc):
		return
	serial = doc.get("item_serial_no")
	builder = builders_of([serial]).get(serial)
	problem = rules.tester_problem(doc.get("inspected_by"), builder, signer=frappe.session.user)
	if problem == rules.NO_BUILDER:
		frappe.throw(
			_(
				"Nobody is recorded as having built {0}, so nobody can show the tester is someone else. "
				"Record the builder on its Work Order's Build Checklist before it is finished, or ask a "
				"System Manager to record it on the serial number."
			).format(serial),
			title=_("Pre-Shipment Test"),
		)
	if problem == rules.SIGNER_NOT_TESTER:
		frappe.throw(
			_("Inspected By is {0}. Submitting signs the test, so the tester submits it.").format(
				frappe.bold(doc.get("inspected_by"))
			),
			title=_("Pre-Shipment Test"),
		)
	if problem:
		frappe.throw(_("This test cannot be signed: {0}.").format(problem), title=_("Pre-Shipment Test"))


# ---------------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------------


def gate_rows(doc):
	"""The Delivery Note lines that need the test, as ``rules.delivery_gate`` reads them."""
	from erpnext_enhancements.product_fulfillment.serials import serials_of_row

	items = doc.get("items") or ()
	facts = item_facts([row.item_code for row in items])
	rows = []
	for row in items:
		if not needs_test(facts.get(row.item_code)):
			continue
		rows.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"stock_qty": row.get("stock_qty") or row.get("qty"),
				"serials": serials_of_row(row),
				"has_serial_no": cint(facts[row.item_code].get("has_serial_no")),
			}
		)
	return rows


def gate_problems(doc):
	"""Every problem the gate finds on a Delivery Note, as sentences. Empty means it may ship."""
	settings = get_settings()
	if not cint(settings.require_test) or cint(doc.get("is_return")):
		return []
	rows = gate_rows(doc)
	if not rows:
		return []
	messages = []
	for row in rows:
		if not row["has_serial_no"]:
			messages.append(
				_("Row {0}: {1} has no serial numbers, and every product unit needs one.").format(
					row["idx"], row["item_code"]
				)
			)
	serials = [s for row in rows for s in row["serials"]]
	problems = rules.delivery_gate(
		[row for row in rows if row["has_serial_no"]],
		tests_by_serial(serials),
		builders_of(serials),
		settings.test_template or None,
	)
	for problem in problems:
		meaning = _(rules.GATE_MESSAGES.get(problem["code"], problem["code"]))
		if problem["serial"]:
			messages.append(_("Row {0}: unit {1} {2}.").format(problem["idx"], problem["serial"], meaning))
		else:
			messages.append(_("Row {0}: {1}.").format(problem["idx"], meaning))
	return messages


def delivery_note_before_submit(doc, method=None):
	"""Refuse to ship a configured product unit that has not passed its pre-shipment test."""
	messages = gate_problems(doc)
	if messages:
		frappe.throw(
			"<br>".join(
				[_("Every unit must pass its Pre-Shipment Functional Test before it ships."), *messages]
			),
			title=_("Not tested yet"),
		)


@frappe.whitelist()
def delivery_gate_status(delivery_note):
	"""What the gate would say about a Delivery Note now, for its form."""
	doc = frappe.get_doc("Delivery Note", delivery_note)
	doc.check_permission("read")
	return {"problems": gate_problems(doc), "rows": len(gate_rows(doc))}


# ---------------------------------------------------------------------------------------------
# Starting a test
# ---------------------------------------------------------------------------------------------


def _reference_for(serial):
	"""The Stock Entry that made a unit: the serial's own source document, else its Work Order's
	Manufacture entry."""
	row = frappe.db.get_value(
		"Serial No", serial, ["reference_doctype", "reference_name", "work_order"], as_dict=True
	)
	if not row:
		return None
	if row.reference_doctype == "Stock Entry" and row.reference_name:
		return row.reference_name
	if row.work_order:
		return frappe.db.get_value(
			"Stock Entry",
			{"work_order": row.work_order, "purpose": "Manufacture", "docstatus": 1},
			"name",
			order_by="posting_date desc",
		)
	return None


@frappe.whitelist(methods=["POST"])
def start_test(serial_no):
	"""Make a draft Pre-Shipment Functional Test for one unit, as the person asking, and return it.

	A draft already waiting for this serial is returned instead of a second one. Refused when the
	person asking built the unit (the same check the test's own validate makes, said sooner).
	"""
	if not frappe.has_permission(QI, "create"):
		frappe.throw(
			_("You need permission to create Quality Inspections (the Quality Manager role)."),
			frappe.PermissionError,
		)
	serial = frappe.db.get_value("Serial No", serial_no, ["name", "item_code"], as_dict=True)
	if not serial:
		frappe.throw(_("Serial number {0} not found.").format(serial_no))
	draft = frappe.db.get_value(QI, {"item_serial_no": serial.name, "docstatus": 0}, "name")
	if draft:
		return {"name": draft, "existing": True}
	builder = builders_of([serial.name]).get(serial.name)
	if rules.same_person(builder, frappe.session.user):
		frappe.throw(
			_("You built {0}, so someone else must test it.").format(serial.name),
			title=_("Pre-Shipment Test"),
		)
	reference = _reference_for(serial.name)
	if not reference:
		frappe.throw(
			_("No Stock Entry made {0}, so there is nothing to test it against.").format(serial.name)
		)
	settings = get_settings()
	template = settings.test_template or frappe.db.get_value(
		"Item", serial.item_code, "quality_inspection_template"
	)
	if template and not frappe.db.exists("Quality Inspection Template", template):
		template = None
	qi = frappe.get_doc(
		{
			"doctype": QI,
			"inspection_type": "Outgoing",
			"reference_type": "Stock Entry",
			"reference_name": reference,
			"item_code": serial.item_code,
			"item_serial_no": serial.name,
			"sample_size": 1,
			"inspected_by": frappe.session.user,
			"report_date": nowdate(),
			"quality_inspection_template": template,
		}
	)
	qi.insert()
	return {"name": qi.name, "existing": False}

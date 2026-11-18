"""Seed the E-Stop's Pre-Shipment Functional Test as a Quality Inspection Template (v1.826.0).

Nikolas, 2026-11-18: every unit passes the Pre-Shipment Functional Test before it ships, tested by
someone other than its builder. The test is ERPNext's own Quality Inspection (ADR 0019), so its
checks are a Quality Inspection Template: **"Pre-Shipment Functional Test - E-Stop"**, the default
Product Build Settings names.

These checks are a **starter draft for Logan Penrod to refine** on the template itself. They are
drawn from the configurator's QC steps for PDT-0040 (``product_configurator/seed_data.py``: timer,
e-stop, buzzer, indicator, terminals, labels, glands, drawing package) and from the strawman
"Products - Pre-Shipment Functional Test" in ``quality/draft_catalog.py`` (control voltage, every
interlock forced, safe state after a power cycle). The strawman's HMI, IO-schedule and pump-starter
checks belong to engineered control panels and are left out.

How each check reads, in ERPNext's own terms:

* **Pass checks** are non-numeric with Value "Pass": the tester types Pass (the app tidies "pass",
  "ok" and the like) and anything else rejects the reading. A blank reading rejects it too, so
  nothing passes by being left alone.
* **Checks that depend on the configuration** (timers, contactors, relays can each be 0) accept
  "Pass" or "N/A" through an acceptance formula.
* **Control voltage** is a numeric reading: 24 VDC within ten percent, 21.6 to 26.4.

Insert-only: a parameter or template that already exists is left exactly as it is, so Logan's
edits survive every later migrate. Cannot raise; safe to run twice.
"""

import frappe

TEMPLATE = "Pre-Shipment Functional Test - E-Stop"

PASS_OR_NA = 'reading_value in ("Pass", "N/A")'

#: (parameter, description, kind, criteria). kind: "pass", "pass_or_na" or "range" (min, max).
CHECKS = [
	(
		"E-Stop - Control voltage at the terminals (VDC)",
		"Meter at the control terminals with the panel energized. 24 VDC within ten percent.",
		"range",
		(21.6, 26.4),
	),
	(
		"E-Stop - Indicator light on with power applied",
		"The indicator light comes on when the panel is powered.",
		"pass",
		None,
	),
	(
		"E-Stop - Every e-stop button latches and releases",
		"Press each e-stop station: it latches when pressed and releases correctly.",
		"pass",
		None,
	),
	(
		"E-Stop - Buzzer sounds while an e-stop is latched",
		"Each station's buzzer sounds for as long as its e-stop is latched.",
		"pass",
		None,
	),
	(
		"E-Stop - Every e-stop drops every pump contactor",
		"Force each e-stop in turn: every pump motor contactor drops out. N/A when the panel has no contactor.",
		"pass_or_na",
		None,
	),
	(
		"E-Stop - Every timer runs 15 minutes",
		"Each timer button runs its output for 15 minutes against a stopwatch. N/A when the panel has no timer.",
		"pass_or_na",
		None,
	),
	(
		"E-Stop - Every auxiliary relay changes state on e-stop",
		"Each interlock relay changes state when an e-stop is pressed. N/A when the panel has no relay.",
		"pass_or_na",
		None,
	),
	(
		"E-Stop - Safe state after a power cycle",
		"Cycle power: the panel comes up in its safe state and no output restarts by itself.",
		"pass",
		None,
	),
	(
		"E-Stop - Jumpers and ferrules match the wiring tables",
		"Every jumper and ferrule checked against the wiring tables for this configuration.",
		"pass",
		None,
	),
	(
		"E-Stop - Terminals torqued and labeled, panel label applied",
		"All terminals torqued and labeled; the panel label and the internal terminal label are applied.",
		"pass",
		None,
	),
	(
		"E-Stop - Cable glands fitted and enclosure sealed",
		"One cable gland per e-stop and timer, all tightened; the enclosure closes and seals.",
		"pass",
		None,
	),
	(
		"E-Stop - Drawing package in the box",
		"The drawing package is in the panel, and the test certificate goes in the box with it.",
		"pass",
		None,
	),
]


def _template_row(parameter, kind, criteria):
	row = {"specification": parameter}
	if kind == "range":
		row.update({"numeric": 1, "min_value": criteria[0], "max_value": criteria[1]})
	elif kind == "pass_or_na":
		row.update({"numeric": 0, "formula_based_criteria": 1, "acceptance_formula": PASS_OR_NA})
	else:
		row.update({"numeric": 0, "value": "Pass"})
	return row


def execute():
	try:
		if not frappe.db.exists("DocType", "Quality Inspection Template"):
			return
		for parameter, description, _kind, _criteria in CHECKS:
			if not frappe.db.exists("Quality Inspection Parameter", parameter):
				frappe.get_doc(
					{
						"doctype": "Quality Inspection Parameter",
						"parameter": parameter,
						"description": description,
					}
				).insert(ignore_permissions=True)
		if frappe.db.exists("Quality Inspection Template", TEMPLATE):
			print(f"seed_estop_pre_shipment_test: {TEMPLATE} already exists, left as it is")
			return
		frappe.get_doc(
			{
				"doctype": "Quality Inspection Template",
				"quality_inspection_template_name": TEMPLATE,
				"item_quality_inspection_parameter": [
					_template_row(parameter, kind, criteria) for parameter, _d, kind, criteria in CHECKS
				],
			}
		).insert(ignore_permissions=True)
		print(f"seed_estop_pre_shipment_test: created {TEMPLATE} with {len(CHECKS)} checks")
	except Exception:
		frappe.log_error(title="Patch seed_estop_pre_shipment_test", message=frappe.get_traceback())

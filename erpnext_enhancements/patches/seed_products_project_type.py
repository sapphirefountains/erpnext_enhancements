"""Add the ``Products`` Project Type, which Controls Fab inspections key to (v1.446.0, WI-075).

The Quality build spec lists five project types — Design, Build, Controls Fab, Events and
Maintenance — and **two of them do not exist in those words on this site**. Measured on
production 2026-09-14:

    Service 354 · Build 78 · (blank) 73 · Events 62 · Design 58 ·
    Internal 13 · Other 8 · Overhead 5 · Group Projects 3

``Service`` *is* the spec's "Maintenance" and is the largest category by a factor of four; it is
deliberately not renamed, because the rename buys vocabulary rather than capability and would be
a data migration across 354 projects. ``Controls Fab`` exists nowhere at all — controls work is
modelled as ``Control Panel Design`` in ``water_engineering`` (2 rows, one with no project), and
there is no project type carrying it.

So the inspection programme keys on ``Project.project_type`` as it actually is, plus this one
new value. ``Products`` rather than ``Controls Fab`` because the category is the manufactured
side of the business generally — control panels today, whatever is fabricated next — and because
``Products`` already exists as a **Value Stream**, so the word is the one people here use.

Note what is *not* being done: the seven projects currently tagged with the ``Products`` value
stream are **not** retyped. A value stream and a project type are different axes, value streams
cover only 127 of 654 projects, and silently rewriting ``project_type`` on live projects to make
a new report look populated is precisely the kind of change that should be a person's decision.

``Project Type`` autonames ``field:project_type``, so the docname *is* the value. Insert-only and
idempotent.
"""

import frappe

PROJECT_TYPE = "Products"


def execute() -> None:
	if frappe.db.exists("Project Type", PROJECT_TYPE):
		frappe.logger().info(f"seed_products_project_type: {PROJECT_TYPE!r} already present")
		return

	doc = frappe.new_doc("Project Type")
	doc.project_type = PROJECT_TYPE
	doc.description = (
		"Manufactured and fabricated work — control panels and the like. Carries the "
		"Controls Fab inspection programme: incoming components, in-process wiring and "
		"assembly, pre-shipment functional test, on-site commissioning."
	)
	doc.insert(ignore_permissions=True)
	frappe.logger().info(f"seed_products_project_type: created {PROJECT_TYPE!r}")

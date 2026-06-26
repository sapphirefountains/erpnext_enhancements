"""Seed the Production Builds Kanban board (Business Process Mapping program, Phase 3).

The production build phase had no tracking. Phase 3 adds a `custom_build_status`
Select on Project (Design Complete -> ... -> Commissioned) plus bid-cost and QC
sign-off fields (in fixtures/custom_field.json), and this patch creates a native
Frappe **Kanban board** ("Production Builds") so builds can be tracked and dragged
across phases — a maintainable, drag-and-drop board with no bespoke UI code.

**Ordering backstop:** fixtures (custom_field.json) are synced *after*
post_model_sync patches during `bench migrate`, so on the first migrate the
`custom_build_status` field may not exist yet when this patch runs. We therefore
ensure that one field via create_custom_fields first (idempotent; the fixture
remains the source of truth for the full Production Tracking field set), then
build the board. Insert-only — an existing board is left untouched.
"""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

BUILD_STATUS_OPTIONS = (
	"\nDesign Complete\nProcurement\nAssembly\nQA\nReady for Install\nInstalled\nCommissioned"
)

# column_name must match the Select option; indicator is a standard Frappe colour.
COLUMNS = [
	("Design Complete", "Gray"),
	("Procurement", "Blue"),
	("Assembly", "Purple"),
	("QA", "Orange"),
	("Ready for Install", "Yellow"),
	("Installed", "Green"),
	("Commissioned", "Green"),
]


def execute():
	# Ordering backstop (see module docstring).
	if not frappe.db.exists("Custom Field", "Project-custom_build_status"):
		create_custom_fields(
			{
				"Project": [
					{
						"fieldname": "custom_build_status",
						"label": "Build Status",
						"fieldtype": "Select",
						"options": BUILD_STATUS_OPTIONS,
						"insert_after": "custom_build_deliverables",
						"in_standard_filter": 1,
					}
				]
			},
			ignore_validate=True,
		)

	if not frappe.db.exists("DocType", "Kanban Board"):
		return
	if frappe.db.exists("Kanban Board", {"kanban_board_name": "Production Builds"}):
		return

	board = frappe.new_doc("Kanban Board")
	board.kanban_board_name = "Production Builds"
	board.reference_doctype = "Project"
	board.field_name = "custom_build_status"
	board.private = 0
	board.filters = json.dumps([["Project", "custom_build_status", "is", "set"]])
	for column_name, indicator in COLUMNS:
		board.append("columns", {"column_name": column_name, "status": "Active", "indicator": indicator})
	board.insert(ignore_permissions=True)

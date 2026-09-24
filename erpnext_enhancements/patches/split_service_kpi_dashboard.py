"""Split maintenance out of the Operations KPI dashboard into Service (v1.530.0).

Nik, 2026-09-24: maintenance does not belong under Operations but under Production, as a
Service dashboard, and Operations becomes the inventory dashboard, with a KPI for
unscheduled store runs. The code side is the new ``_service_metrics`` aggregator and the
inventory set in ``_operations_metrics``. This patch does the five things a code change
cannot do on its own, each guarded so one failure does not stop the rest or the migrate.

1. **The Supplier flag behind the store-run KPI.** ``custom_store_run_vendor`` is created
   with ``create_custom_fields`` (so ``is_system_generated = 1`` and the fixture export skips
   it), the way the procurement fields on Purchase Order are. It is a Check with default 0 on
   a normal doctype, so the one ``ALTER`` writes 0 into every existing row, which is correct.
2. **Home Depot and Lowe's are ticked**, the two counters behind 206 of the year's card
   purchases and the case Nik described. Only when *no* supplier is ticked yet, so the
   patch can never overturn a later decision to untick one. Which other suppliers count is
   for the review meeting to decide, in the Desk.
3. **KPI Targets follow their KPIs.** A target is keyed on department, so one set against
   ``Operations/visits_open`` would stop grading the moment the KPI moved. The live site had
   none on 2026-09-24; the move is here for any added before the deploy, and for other sites.
4. **Workspaces and sidebars re-sync.** Both stamps are bumped in the JSON, which is enough
   where the stored row is older; ``force=True`` covers a row touched more recently. See
   ``resync_hr_and_training_workspaces`` for why sidebars need ``import_file_by_path`` and
   cannot go through ``reload_doc``.
5. **The three old block records go.** The block seeder upserts and never deletes, so
   "Operations Day Board", "Operations Chemistry Alerts" and "Operations Labor Capture"
   would otherwise sit in the block picker for ever, rendering maintenance lists under an
   Operations name. They are deleted only after step 4 has removed the Operations
   Dashboard's rows pointing at them; a copy in somebody's private workspace still links to
   one, ``delete_doc`` refuses, and that refusal is logged and left alone.

Cannot raise; safe twice.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

STORE_RUN_FIELD = "custom_store_run_vendor"
SEED_STORE_RUN_SUPPLIERS = ("Home Depot", "Lowes")

# (from department, to department, kpi keys)
TARGET_MOVES = (
	(
		"Operations",
		"Service",
		(
			"visits_completed_30",
			"visits_open",
			"chem_oor_30",
			"chem_oor_rate",
			"active_contracts",
			"contracts_expiring_60",
		),
	),
	("Product", "Operations", ("items_below_reorder", "inventory_stock_value", "out_of_stock_sellable")),
)

WORKSPACES = ("operations_dashboard", "service_dashboard")
SIDEBARS = (
	"kpi_dashboards",
	"finance_dashboard",
	"sales_dashboard",
	"operations_dashboard",
	"design_dashboard",
	"production_dashboard",
	"service_dashboard",
	"marketing_dashboard",
	"product_dashboard",
	"hr_dashboard",
	"executive_dashboard",
)
RETIRED_BLOCKS = ("Operations Day Board", "Operations Chemistry Alerts", "Operations Labor Capture")


def execute():
	for step in (_store_run_field, _seed_store_run_suppliers, _move_targets, _resync_desk, _retire_blocks):
		try:
			step()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				f"split_service_kpi_dashboard: {step.__name__} failed\n{frappe.get_traceback()}",
				"KPI dashboard split",
			)


def _store_run_field():
	create_custom_fields(
		{
			"Supplier": [
				{
					"fieldname": STORE_RUN_FIELD,
					"fieldtype": "Check",
					"label": "Store-Run Vendor",
					"insert_after": "supplier_group",
					"default": "0",
					"in_standard_filter": 1,
					"description": (
						"A walk-in counter such as Home Depot or Lowe's. Unplanned purchases here "
						"count as store runs on the Operations KPI dashboard."
					),
				}
			]
		},
		update=True,
	)


def _seed_store_run_suppliers():
	if not frappe.db.has_column("Supplier", STORE_RUN_FIELD):
		return
	if frappe.get_all("Supplier", filters={STORE_RUN_FIELD: 1}, limit=1, pluck="name"):
		return
	for name in SEED_STORE_RUN_SUPPLIERS:
		if frappe.db.exists("Supplier", name):
			frappe.db.set_value("Supplier", name, STORE_RUN_FIELD, 1, update_modified=False)


def _move_targets():
	for source, dest, keys in TARGET_MOVES:
		for name in frappe.get_all(
			"KPI Target", filters={"department": source, "kpi_key": ("in", keys)}, pluck="name"
		):
			old = frappe.get_doc("KPI Target", name)
			new = frappe.copy_doc(old)
			new.department = dest
			# The name is `TGT-{department}-{kpi_key}-{period}`, so a row already at the
			# destination is the same target set twice. Keep the one set there on purpose.
			if frappe.db.exists("KPI Target", f"TGT-{dest}-{old.kpi_key}-{old.period}"):
				continue
			new.insert(ignore_permissions=True)
			frappe.delete_doc("KPI Target", name, ignore_permissions=True, force=True)


def _resync_desk():
	from frappe.modules.import_file import import_file_by_path

	for name in WORKSPACES:
		try:
			frappe.reload_doc("kpi_dashboards", "workspace", name, force=True)
		except Exception:
			frappe.log_error(
				f"Could not re-sync the {name} workspace\n{frappe.get_traceback()}", "Workspace sync"
			)
	for name in SIDEBARS:
		try:
			import_file_by_path(
				frappe.get_app_path("erpnext_enhancements", "workspace_sidebar", f"{name}.json"),
				force=True,
			)
		except Exception:
			frappe.log_error(
				f"Could not re-sync the {name} sidebar\n{frappe.get_traceback()}", "Workspace sync"
			)
	frappe.clear_cache()


def _retire_blocks():
	for name in RETIRED_BLOCKS:
		if not frappe.db.exists("Custom HTML Block", name):
			continue
		try:
			frappe.delete_doc("Custom HTML Block", name, ignore_permissions=True)
		except Exception:
			frappe.log_error(
				f"Left the retired block {name!r} in place: something still links to it\n"
				f"{frappe.get_traceback()}",
				"KPI dashboard split",
			)

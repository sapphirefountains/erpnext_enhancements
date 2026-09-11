# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Re-import the nine `* Hub` workspaces after WI-074 G filled them out.

`fill_team_hub_workspaces` (v1.396.0) adopted seven hubs and gave each a handful of
shortcuts and one card of reports. This re-imports all nine -- those seven, plus
`Finance Hub` and `Executive Hub` -- now that each carries grouped link cards, count
badges and quick lists.

Why a patch and not just a bumped timestamp
-------------------------------------------------------------------------------
Both, in fact. The JSONs carry a newer ``modified`` so the ordinary sync would take
them, but the ordinary sync is age-gated against whatever stamp the **row** carries,
and six people hold `Workspace Manager` on this site. A Desk edit to any hub pushes
its ``modified`` to now and the file silently loses the comparison
(`frappe/modules/import_file.py`). ``force=True`` is the only deterministic answer.

Worth knowing what force does here: `import_doc` calls `delete_old_doc` first, so the
child tables are replaced rather than merged. That is the point -- `Executive Hub`
carried a shortcut to a Page named ``project-dashboard`` that **does not exist on this
site**, so `is_item_allowed` filtered it and the block drew an empty div across the
full row. A merge would have kept it.

`import_doc` also sets ``ignore_validate`` and ``ignore_links``, so nothing in here
can raise out of `Workspace.validate`, and a link to a doctype that has since been
removed cannot abort the import.

Executive Hub is the one being adopted
-------------------------------------------------------------------------------
It was created by hand in the Desk on 2026-02-25 with ``app = "erpnext"`` and no
module, and has never been in this repo -- so no migrate has ever touched it and
nothing would have. Its `Home Dashboard Tasks` custom block is carried across
verbatim; it is the only thing on the page that renders today.

Finance Hub keeps ``module = "Accounting Intake"``
-------------------------------------------------------------------------------
Deliberately, and unlike its eight siblings. `Workspace.__init__` raises
PermissionError -- not a hidden page, a **refusal** -- when a workspace's module is
absent from the user's `allow_modules`, and that set is derived from DocType read
permissions. Every current `Finance Team` holder was checked on prod for read access
to an Accounting Intake doctype before the role was added here. See
`tests/test_workspaces.py::TestFinanceHub`, which records the original reasoning.

Never aborts a migrate. A desk page's layout does not outrank the schema changes a
deploy is carrying.
"""

import os

import frappe

#: Workspace name -> (module directory it ships under, folder/file slug).
HUBS = {
	"HR Hub": ("enhancements_core", "hr_hub"),
	"Design Hub": ("enhancements_core", "design_hub"),
	"Marketing Hub": ("enhancements_core", "marketing_hub"),
	"Sales Hub": ("enhancements_core", "sales_hub"),
	"Operations Hub": ("enhancements_core", "operations_hub"),
	"Production Hub": ("enhancements_core", "production_hub"),
	"Support Hub": ("enhancements_core", "support_hub"),
	"Executive Hub": ("enhancements_core", "executive_hub"),
	"Finance Hub": ("accounting_intake", "finance_hub"),
}


def execute():
	from frappe.modules.import_file import import_file_by_path

	for name, (module_dir, slug) in HUBS.items():
		path = frappe.get_app_path(
			"erpnext_enhancements", module_dir, "workspace", slug, f"{slug}.json"
		)
		if not os.path.exists(path):
			frappe.log_error(f"Hub workspace file missing: {path}", "Workspace sync")
			continue
		try:
			# NOT reload_doc. Its first argument is a MODULE, and eight of these nine
			# deliberately declare none -- reload_doc would raise DoesNotExistError
			# into whatever except surrounds it and the sync would read as done.
			import_file_by_path(path, force=True, reset_permissions=False)
		except Exception:
			frappe.log_error(
				f"Could not import the {name} workspace\n{frappe.get_traceback()}",
				"Workspace sync",
			)

	# `setup.desktop_icons` runs on after_migrate -- i.e. after this patch, which is
	# the order the tiles need. `_create_tile` refuses to build a Desktop Icon for a
	# Workspace that does not exist yet, and `_sync_roles` derives each tile's roles
	# from the workspace rows this patch has just written. That is how the new
	# `Executive Hub` tile gets `Executive Team`, and the `Finance Hub` tile picks up
	# `Finance Team`, without either being listed a second time.
	frappe.clear_cache()

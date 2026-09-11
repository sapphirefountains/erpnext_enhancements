# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Adopt the six hand-made `* Hub` workspaces into the repo, and add `Support Hub`.

Six workspaces — HR, Design, Marketing, Sales, Operations and Production Hub — were
created by hand in the Desk and left with ``content = "[]"``: the per-role home grids
of WI-072 D1, started and never populated. `Executive Hub` and `Finance Hub` were
finished and role-gated, and are the shape the other seven now follow.

Why a patch rather than letting the normal sync do it
-----------------------------------------------------
Workspace import is **timestamp-gated**, and it fails silently: `import_file_by_path`
compares the file's ``modified`` against the stored row's and skips when the file is
not newer (`frappe/modules/import_file.py`), logging nothing. These six rows carry
whatever stamp the owner's last hand-save produced, which is not knowable from here —
so there is no file timestamp that can be guaranteed to win. ``force=True`` is the
only deterministic answer, and this is the same trap that stranded the Training
workspace on its install-default layout for five weeks.

Why the hubs carry no module
----------------------------
Both reasons are verified against ``origin/version-16``:

* ``Workspace.is_permitted`` (``frappe/desk/desktop.py``) skips the module gate
  entirely when ``module`` is NULL. These are cross-functional hubs — assigning any
  one module would hide the hub from everyone who happens to hold no DocPerm in it,
  which is exactly how `Kendalyn Harris` ended up unable to see the Training
  workspace at all.
* ``remove_orphan_entities`` filters on
  ``{"public": 1, "module": ["is", "set"], "app": ["is", "set"]}``, so a module-less
  workspace is never force-deleted. Setting a module here would be irreversible in
  practice: the first migrate that could not find a matching file would delete the
  owner's page.

Gating is therefore entirely on ``Workspace.roles``, one `* Team` role per hub. Those
are the only roles on this site that discriminate — no role at all is held by every
System User, but the Team roles split the company cleanly.

Never aborts a migrate. A desk page's layout does not outrank the schema changes a
deploy is carrying.
"""

import os

import frappe

#: Workspace name -> the folder its JSON ships in, relative to the app package.
HUBS = {
	"HR Hub": "hr_hub",
	"Design Hub": "design_hub",
	"Marketing Hub": "marketing_hub",
	"Sales Hub": "sales_hub",
	"Operations Hub": "operations_hub",
	"Production Hub": "production_hub",
	"Support Hub": "support_hub",
}

MODULE_DIR = "enhancements_core"


def execute():
	from frappe.modules.import_file import import_file_by_path

	for name, slug in HUBS.items():
		path = frappe.get_app_path(
			"erpnext_enhancements", MODULE_DIR, "workspace", slug, f"{slug}.json"
		)
		if not os.path.exists(path):
			frappe.log_error(f"Hub workspace file missing: {path}", "Workspace sync")
			continue
		try:
			# NOT reload_doc. Its first argument is a MODULE, and these workspaces
			# deliberately declare none -- reload_doc would raise DoesNotExistError
			# into whatever except surrounds it and the sync would read as done.
			import_file_by_path(path, force=True, reset_permissions=False)
		except Exception:
			frappe.log_error(
				f"Could not import the {name} workspace\n{frappe.get_traceback()}",
				"Workspace sync",
			)

	# The tiles themselves are created and role-stamped by `setup.desktop_icons`,
	# which runs on after_migrate -- i.e. after this patch, which is the order the
	# tile needs: `_create_tile` refuses to build a Desktop Icon for a Workspace that
	# does not exist yet, and `_sync_roles` derives the tile's roles from the
	# workspace rows this patch has just written.
	frappe.clear_cache()

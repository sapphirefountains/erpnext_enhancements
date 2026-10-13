"""Custom pages S1 (v1.623.0, TASK-2026-02803): land the workspace and page changes on an
existing site, and retire the Hierarchical Task View page.

Workspaces and Pages are **timestamp-gated** by `import_file`: a file is imported only when
it reads newer than the stored row. Every file below has its `modified` bumped past the prod
row (checked on 2026-10-13: each prod row carried exactly the repo's old stamp, so nobody had
edited one by hand). This patch is the belt to that suspenders, the same shape as
`flesh_out_hub_workspaces`: `import_file_by_path(force=True)` rebuilds each row past the age
check. Not `reload_doc` — its first argument is a module, and eight of the hubs declare none.

What changes:

* the hubs' dashboard URL shortcuts and the Projects Dashboard shortcut read `/desk/...`, not
  `/app/...` (a URL shortcut opens a new tab; `/app` only adds a redirect there);
* Finance Hub gains a Stripe Payments shortcut, the page's only way in until now being a
  button on its Settings form;
* Operations Hub and Sales Hub list Contract Builder beside Project Contract;
* the Training workspace lists Question Review and Glossary Review in its Authoring card;
* the Task Enhancements workspace drops Hierarchical Task View;
* the Sales Pipeline page gets a title, so the awesomebar can find it by name.

Then the retired page. Its files are gone from the repo, which stops managing the Page but
does not delete it, so this removes the rows that point at it — the auto-made
`Task Enhancements` Workspace Sidebar item, plus any shortcut a private workspace may carry —
and then the Page itself, WITHOUT `force`: if something unforeseen still links to it,
`delete_doc` refuses and that is logged rather than left dangling.

And the one seeded Enhancement Desk Shortcut that still read `/app/projects` (the seeder is
insert-only and never rewrites a row) moves to `/desk/projects`, which the Desk Shortcuts block
now routes in place.

Cannot raise: each step logs its own failure. Safe to run twice.
"""

import os

import frappe

RETIRED_PAGE = "hierarchical-task-view"

#: (folder under the app, workspace slug) for every workspace file this release edits.
WORKSPACES = (
	("enhancements_core", "design_hub"),
	("enhancements_core", "executive_hub"),
	("enhancements_core", "hr_hub"),
	("enhancements_core", "marketing_hub"),
	("enhancements_core", "operations_hub"),
	("enhancements_core", "production_hub"),
	("enhancements_core", "sales_hub"),
	("accounting_intake", "finance_hub"),
	("project_enhancements", "project_enhancements"),
	("training", "training"),
	("task_enhancements", "task_enhancements"),
)

#: (folder under the app, page slug) for every Page file this release edits.
PAGES = (("crm_enhancements", "sales_pipeline"),)


def execute():
	from frappe.modules.import_file import import_file_by_path

	done, failed = [], []

	for folder, slug in WORKSPACES:
		_import(
			import_file_by_path,
			frappe.get_app_path("erpnext_enhancements", folder, "workspace", slug, f"{slug}.json"),
			f"workspace {slug}",
			done,
			failed,
		)

	for folder, slug in PAGES:
		_import(
			import_file_by_path,
			frappe.get_app_path("erpnext_enhancements", folder, "page", slug, f"{slug}.json"),
			f"page {slug}",
			done,
			failed,
		)

	_step(_retire_page, "retire the Hierarchical Task View page", done, failed)
	_step(_repoint_desk_shortcuts, "move Desk Shortcut URLs to /desk", done, failed)

	frappe.clear_cache()
	print(f"custom_pages_s1_navigation: {len(done)} done, {len(failed)} failed: {failed}")


def _import(import_file_by_path, path, label, done, failed):
	if not os.path.exists(path):
		frappe.log_error(title="Custom pages S1", message=f"File missing: {path}")
		failed.append(label)
		return
	try:
		import_file_by_path(path, force=True, reset_permissions=False)
		done.append(label)
	except Exception:
		frappe.log_error(title="Custom pages S1", message=f"Could not import {label}\n{frappe.get_traceback()}")
		failed.append(label)


def _step(fn, label, done, failed):
	try:
		fn()
		done.append(label)
	except Exception:
		frappe.log_error(title="Custom pages S1", message=f"Could not {label}\n{frappe.get_traceback()}")
		failed.append(label)


def _retire_page():
	# Shortcut and sidebar rows that point at the page, in any workspace or sidebar, standard
	# or private (prod had one of each on 2026-10-13; the Task Enhancements shortcut is already
	# gone with the re-import above). Not Workspace Link rows: deleting one from a card would
	# leave its Card Break's link_count wrong, and prod had none. A link row nobody foresaw
	# makes the delete below refuse, which is logged.
	for child in ("Workspace Shortcut", "Workspace Sidebar Item"):
		if frappe.db.exists("DocType", child):
			frappe.db.delete(child, {"link_to": RETIRED_PAGE})

	if frappe.db.exists("Page", RETIRED_PAGE):
		# No force: a link nobody foresaw makes delete_doc refuse, and _step logs it.
		frappe.delete_doc("Page", RETIRED_PAGE, ignore_permissions=True)


def _repoint_desk_shortcuts():
	if not frappe.db.exists("DocType", "Enhancement Desk Shortcut"):
		return
	for row in frappe.get_all(
		"Enhancement Desk Shortcut", filters={"link_type": "URL"}, fields=["name", "url"]
	):
		url = row.url or ""
		if url == "/app" or url.startswith("/app/"):
			frappe.db.set_value(
				"Enhancement Desk Shortcut", row.name, "url", "/desk" + url[len("/app") :]
			)

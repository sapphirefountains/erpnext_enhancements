"""Custom pages S4a (v1.665.0, TASK-2026-02806): hub <-> dashboard links, the Morning Briefing
workspace, and the small workspace fixes. Lands the file changes on an existing site.

Workspaces, Pages and Reports are **timestamp-gated** by ``import_file``: a file is imported only
when it reads newer than the stored row. Every file below has its ``modified`` bumped to
2026-10-14 12:00, past the prod rows (checked that day: the hubs and dashboards carried the
repo's own older stamps, except Design, Executive, HR, Marketing, Production and Sales
Dashboard at 2026-08-06 13:27, a migrate's own write, still older). This patch is the belt to
those suspenders, the same shape as ``custom_pages_s1_navigation``:
``import_file_by_path(force=True)`` rebuilds each row past the age check. Not ``reload_doc``:
its first argument is a module, and the hubs declare none.

What the files change:

* every department dashboard carries the Dashboard Hub Links block first, the way back to its
  hub, drawn only for a hub the viewer can open. Below it each dashboard keeps the order prod
  shows today: Design, Executive, HR, Marketing, Production and Sales have had the KPI Cockpit
  first since the seeder's write of 2026-08-06, and their files now say so. The Design,
  Operations, Production and Support hubs gain the dashboard links they lacked (Product, Product, Service, Service), and the
  Operations Hub gains Stock Scan, Warehouse QR Labels and an Inventory card;
* the Product Dashboard gets catalogue shortcuts and cards above nothing but its KPI Cockpit;
* the Morning Briefing workspace shows the briefing itself, keeps /wall, folds its two
  System-Manager-only lists into one card, and is gated on the staff roles the briefing and
  /wall serve (16.50's ``is_permitted`` reads only Block Module and the workspace's roles);
* the Production Hub and the Workforce workspace link the kiosk at /kiosk instead of the
  ``time-kiosk`` desk page, which is only a redirect to it, and Workforce drops the second link;
* the GA4 Dashboard page admits Marketing Team and Marketing Manager (the Marketing Hub links
  it), and the Maintenance Day Board admits Maintenance Manager (the Production Hub links it);
* Top Company Priority Projects becomes a standard report of this app.

And two rows no file holds, both made on the site: the seeded Time Kiosk Desk Shortcut, and the
Workforce sidebar's Time Kiosk item, both pointing at the redirect page, move to /kiosk. The
sidebar's own "Kiosk PWA" item, a URL link with no URL (dead on prod on 2026-10-14), is removed.

Cannot raise: each step logs its own failure, and the tally is printed. Safe to run twice.
"""

import os

import frappe

KIOSK_PAGE = "time-kiosk"
KIOSK_URL = "/kiosk"

#: (folder under the app, workspace slug) for every workspace file this release edits.
WORKSPACES = (
	("enhancements_core", "design_hub"),
	("enhancements_core", "operations_hub"),
	("enhancements_core", "production_hub"),
	("enhancements_core", "support_hub"),
	("kpi_dashboards", "design_dashboard"),
	("kpi_dashboards", "executive_dashboard"),
	("kpi_dashboards", "finance_dashboard"),
	("kpi_dashboards", "hr_dashboard"),
	("kpi_dashboards", "marketing_dashboard"),
	("kpi_dashboards", "operations_dashboard"),
	("kpi_dashboards", "product_dashboard"),
	("kpi_dashboards", "production_dashboard"),
	("kpi_dashboards", "sales_dashboard"),
	("kpi_dashboards", "service_dashboard"),
	("morning_briefing", "morning_briefing"),
	("workforce", "workforce"),
)

#: (folder under the app, page slug) for every Page file this release edits.
PAGES = (
	("integration_hub", "ga4_dashboard"),
	("sapphire_maintenance", "maintenance_day_board"),
)

#: (folder under the app, report slug) for every Report file this release adds.
REPORTS = (("project_enhancements", "top_company_priority_projects"),)

TITLE = "Custom pages S4a"


def execute():
	from frappe.modules.import_file import import_file_by_path

	done, failed = [], []

	for kind, entries in (("workspace", WORKSPACES), ("page", PAGES), ("report", REPORTS)):
		for folder, slug in entries:
			_import(
				import_file_by_path,
				frappe.get_app_path("erpnext_enhancements", folder, kind, slug, f"{slug}.json"),
				f"{kind} {slug}",
				done,
				failed,
			)

	_step(_repoint_desk_shortcuts, "move the Time Kiosk Desk Shortcut to /kiosk", done, failed)
	_step(_repoint_sidebar_items, "move Time Kiosk sidebar items to /kiosk", done, failed)

	try:
		frappe.clear_cache()
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not clear the cache\n{frappe.get_traceback()}")
	print(f"custom_pages_s4a_hub_links: {len(done)} done, {len(failed)} failed: {failed}")


def _import(import_file_by_path, path, label, done, failed):
	if not os.path.exists(path):
		frappe.log_error(title=TITLE, message=f"File missing: {path}")
		failed.append(label)
		return
	try:
		import_file_by_path(path, force=True, reset_permissions=False)
		done.append(label)
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not import {label}\n{frappe.get_traceback()}")
		failed.append(label)


def _step(fn, label, done, failed):
	try:
		fn()
		done.append(label)
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not {label}\n{frappe.get_traceback()}")
		failed.append(label)


def _repoint_desk_shortcuts():
	"""The seeder is insert-only, so the row it made in 2026-06 still names the page."""
	if not frappe.db.exists("DocType", "Enhancement Desk Shortcut"):
		return
	for name in frappe.get_all(
		"Enhancement Desk Shortcut",
		filters={"link_type": "Page", "link_to": KIOSK_PAGE},
		pluck="name",
	):
		frappe.db.set_value(
			"Enhancement Desk Shortcut",
			name,
			{"link_type": "URL", "url": KIOSK_URL, "link_to": None},
		)


def _repoint_sidebar_items():
	"""A sidebar item for the redirect page goes to the kiosk itself, and a "Kiosk PWA" item
	with no URL, which can only ever be a dead link, goes."""
	if not frappe.db.exists("DocType", "Workspace Sidebar Item"):
		return
	for name in frappe.get_all(
		"Workspace Sidebar Item",
		filters={"link_to": KIOSK_PAGE},
		pluck="name",
	):
		frappe.db.set_value(
			"Workspace Sidebar Item",
			name,
			{"link_type": "URL", "url": KIOSK_URL, "link_to": None},
		)
	for row in frappe.get_all(
		"Workspace Sidebar Item",
		filters={"label": "Kiosk PWA", "link_type": "URL"},
		fields=["name", "url"],
	):
		if not (row.url or "").strip():
			frappe.db.delete("Workspace Sidebar Item", {"name": row.name})

"""Retire the standalone Project Dashboard desk page (v1.159.8).

The two parallel Projects Dashboards were consolidated onto the "Projects
Dashboard" Custom HTML Block (embedded on the Home / Projects workspaces). The
desk page at ``/app/project-dashboard`` and its per-tab ``dashboard_components``
were removed from the app; this cleans up already-migrated sites:

  * delete the leftover ``Page`` record ``project-dashboard`` — its JSON is gone
    so it is no longer synced, but the row persists until deleted;
  * repoint the seeded "Project Dashboard" Enhancement Desk Shortcut (the
    insert-only seeder never rewrites it) from the now-dead Page to the Projects
    workspace URL, where the block lives.

The Project Enhancements workspace's own shortcut is handled by its synced JSON
(``modified`` bumped). The shared backend API ``project_dashboard.py`` stays — the
block still calls it. Idempotent.
"""

import frappe


def execute():
	# 1) Drop the retired desk Page.
	if frappe.db.exists("Page", "project-dashboard"):
		frappe.delete_doc("Page", "project-dashboard", ignore_missing=True, force=True)

	# 2) Repoint the seeded desk shortcut to the Projects workspace.
	if frappe.db.exists("DocType", "Enhancement Desk Shortcut"):
		name = frappe.db.get_value(
			"Enhancement Desk Shortcut", {"link_to": "project-dashboard"}, "name"
		)
		if name:
			frappe.db.set_value(
				"Enhancement Desk Shortcut",
				name,
				{"link_type": "URL", "url": "/app/projects", "link_to": None},
			)

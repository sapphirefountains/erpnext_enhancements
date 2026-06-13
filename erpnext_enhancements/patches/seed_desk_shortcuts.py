"""Seed the default Enhancement Desk Shortcut rows (v1.30.0).

Insert-only: each row is created only if a shortcut with that label does not
already exist, so an admin's later edits (icons, roles, users, enable flags,
ordering) are never overwritten by re-migrations. Roles that don't exist on the
site are skipped, so a missing optional role can't fail the seed. These are
admin-owned config rows — deliberately NOT fixtures.

Visibility is cosmetic: every target page enforces its own role permissions.
System Manager / Administrator always see every enabled shortcut regardless of
the roles/users below (handled in ``api.desk_shortcuts``).
"""

import frappe

# label, icon (emoji), color, link_to (Page route), visible_to_all, roles, sequence
DEFAULTS = [
	{
		"label": "Time Kiosk",
		"icon": "⏱️",  # ⏱️
		"color": "Blue",
		"link_to": "time-kiosk",
		"visible_to_all": 1,
		"roles": [],
		"sequence": 10,
	},
	{
		"label": "Inventory Scanner",
		"icon": "\U0001f4f7",  # 📷
		"color": "Teal",
		"link_to": "inventory-scanner-audit",
		"visible_to_all": 0,
		"roles": ["System Manager", "Stock Manager", "Inventory Clerk"],
		"sequence": 20,
	},
	{
		"label": "Maintenance Wizard",
		"icon": "\U0001f9f0",  # 🧰
		"color": "Orange",
		"link_to": "visit-wizard",
		"visible_to_all": 0,
		"roles": ["System Manager", "Maintenance User", "Maintenance Supervisor", "Projects Manager"],
		"sequence": 30,
	},
	{
		"label": "Maintenance Day Board",
		"icon": "\U0001f4cb",  # 📋
		"color": "Orange",
		"link_to": "maintenance-day-board",
		"visible_to_all": 0,
		"roles": ["System Manager", "Maintenance Supervisor", "Projects Manager"],
		"sequence": 40,
	},
	{
		"label": "Sales Pipeline",
		"icon": "\U0001f4c8",  # 📈
		"color": "Green",
		"link_to": "sales-pipeline",
		"visible_to_all": 0,
		"roles": [
			"System Manager",
			"Sales Master Manager",
			"Sales Manager",
			"Sales User",
			"Projects Manager",
			"Projects User",
		],
		"sequence": 50,
	},
	{
		"label": "Project Dashboard",
		"icon": "\U0001f4ca",  # 📊
		"color": "Purple",
		"link_to": "project-dashboard",
		"visible_to_all": 1,
		"roles": [],
		"sequence": 60,
	},
	{
		"label": "Integrations Health",
		"icon": "\U0001f50c",  # 🔌
		"color": "Gray",
		"link_to": "integrations-health",
		"visible_to_all": 0,
		"roles": ["System Manager"],
		"sequence": 70,
	},
]


def execute():
	for d in DEFAULTS:
		if frappe.db.exists("Enhancement Desk Shortcut", d["label"]):
			continue
		try:
			doc = frappe.new_doc("Enhancement Desk Shortcut")
			doc.shortcut_label = d["label"]
			doc.enabled = 1
			doc.sequence = d["sequence"]
			doc.link_type = "Page"
			doc.link_to = d["link_to"]
			doc.icon = d["icon"]
			doc.color = d["color"]
			doc.visible_to_all = d["visible_to_all"]
			for role in d["roles"]:
				if frappe.db.exists("Role", role):
					doc.append("roles", {"role": role})
			doc.insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title=f"seed_desk_shortcuts: {d['label']} failed")

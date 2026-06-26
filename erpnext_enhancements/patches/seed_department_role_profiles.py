"""Seed department Role Profiles (Business Process Mapping program, Phase 1).

Onboarding bundles that map the standard ERPNext roles to the company's
departments, so a new hire gets the right access from one Role Profile instead of
hand-picking roles. The small team reuses standard roles (Accounts / Sales /
Projects / Maintenance) — no new roles are created here.

**Insert-only and role-existence-guarded**: a Role Profile that already exists is
left untouched (site-side edits survive), and only roles that actually exist on
the site are added (so optional roles like Marketing Manager / Maintenance
Manager are skipped cleanly on a site where that module isn't installed).
"""

import frappe

PROFILES = {
	"Finance": ["Accounts User", "Accounts Manager"],
	"Sales & Marketing": ["Sales User", "Sales Manager", "Marketing Manager"],
	"Projects & Operations": [
		"Projects User",
		"Projects Manager",
		"Maintenance User",
		"Maintenance Manager",
	],
	"Executive": ["Accounts Manager", "Sales Manager", "Projects Manager"],
}


def execute():
	if not frappe.db.exists("DocType", "Role Profile"):
		return

	for name, roles in PROFILES.items():
		if frappe.db.exists("Role Profile", name):
			continue
		existing_roles = [r for r in roles if frappe.db.exists("Role", r)]
		if not existing_roles:
			continue
		doc = frappe.new_doc("Role Profile")
		doc.role_profile = name
		for role in existing_roles:
			doc.append("roles", {"role": role})
		doc.insert(ignore_permissions=True)

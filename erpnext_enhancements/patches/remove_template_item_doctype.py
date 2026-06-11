"""Delete the orphaned "Sapphire Template Item" DocType.

The modular maintenance-forms rework replaced the template's flat
``template_items`` child table with composition of reusable Sapphire
Maintenance Sections (``sections`` -> Sapphire Template Section). The child
doctype's folder is gone from the repo, so this post-model-sync patch removes
its DB metadata. Production had a single draft template and zero records when
the cut was made; any residual child rows die with the doctype.

Idempotent and fresh-install-safe: guarded by an existence check (the doctype
never exists on a new site).
"""

import frappe


def execute():
	if frappe.db.exists("DocType", "Sapphire Template Item"):
		frappe.delete_doc("DocType", "Sapphire Template Item", force=True, ignore_permissions=True)

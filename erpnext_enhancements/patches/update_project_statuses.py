"""One-time migration patch (post_model_sync; listed in patches.txt).

Data fix for the project_enhancements module, which removed the "Open" Project
status in favor of "Active". Bulk-updates any existing Project rows still set to
``status = 'Open'`` to ``'Active'``. Idempotent — matches no rows once migrated.
"""
import frappe


def execute():
	"""Bulk-update Projects with status 'Open' to 'Active'."""
	# Update existing projects from 'Open' to 'Active'
	frappe.db.sql("""
        UPDATE `tabProject`
        SET status = 'Active'
        WHERE status = 'Open'
    """)

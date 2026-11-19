"""Who may set the Drive folder link (``custom_drive_folder_id``) on Customer, Project and
Opportunity.

The link names the Google Drive folder the integration reads and writes for a record. It is
set by the integration's own code, and all of it writes with ``frappe.db.set_value`` or
``db_set``, which run no controller and so never reach this hook:

- ``drive_utils.provision_customer_folder`` / ``provision_opportunity_folder`` (the
  ``after_insert`` jobs and the daily ``resweep_missing_drive_folders``);
- ``drive_sync.run_backfill_drive_links``;
- ``drive_link_manager.apply_links`` (System Manager only);
- ``crm_enhancements.api._provision_project_drive`` (the Opportunity hand-off job).

The field is read-only in the form, and Frappe does not enforce ``read_only`` on the server,
so this ``validate`` hook does. On an ordinary save or insert the value may change only when
the user is a System Manager (Administrator included), when server code has set
``doc.flags.drive_folder_update`` (``DRIVE_FOLDER_FLAG``) before saving, or during a migrate,
install or patch. Otherwise:

- a save that carries a **different** folder is refused;
- a save that carries the field **empty** while the record holds a folder keeps the stored
  folder. A form opened before the integration linked the folder carries an empty value, and
  the provisioners write with ``update_modified=False``, so the stale form passes Frappe's
  "modified since" check; that save now goes through and the link is kept;
- a **new** record keeps no folder value. Desk's Duplicate copies the field on Project, and the
  copy starts unlinked rather than pointing at the original record's folder.

Every read is ``doc.get``/``flags.get`` and the column is checked with ``has_column`` before
anything is written, because ``doc_events`` fire during ERPNext's own test bootstrap, before
this app's custom fields exist.
"""

import frappe
from frappe import _

DRIVE_FOLDER_FIELD = "custom_drive_folder_id"
# Set on ``doc.flags`` by server code that saves one of these documents with a new folder.
# ``doc.flags`` cannot be set from a request: v16's ``Document.update`` drops the reserved
# ``flags`` key from an incoming dict.
DRIVE_FOLDER_FLAG = "drive_folder_update"
MANAGER_ROLE = "System Manager"


def guard_drive_folder_link(doc, method=None):
	"""``validate`` for Customer, Project and Opportunity. See the module docstring."""
	if doc.flags.get(DRIVE_FOLDER_FLAG):
		return
	if frappe.flags.get("in_migrate") or frappe.flags.get("in_install") or frappe.flags.get("in_patch"):
		return

	incoming = doc.get(DRIVE_FOLDER_FIELD) or ""
	if doc.is_new():
		if incoming and not _may_set() and _has_field(doc):
			doc.set(DRIVE_FOLDER_FIELD, None)
		return

	before = doc.get_doc_before_save()
	stored = (before.get(DRIVE_FOLDER_FIELD) if before else None) or ""
	if incoming == stored:
		return
	if _may_set() or not _has_field(doc):
		return
	if not incoming:
		doc.set(DRIVE_FOLDER_FIELD, stored)
		return
	frappe.throw(
		_(
			"The Drive folder link on this record is set by the Google Drive integration. "
			"Reload the page and save again, or ask a System Manager to change the link."
		),
		frappe.PermissionError,
		title=_("Drive folder link"),
	)


def _may_set():
	user = frappe.session.user
	return user == "Administrator" or MANAGER_ROLE in (frappe.get_roles(user) or [])


def _has_field(doc):
	# A doctype, not a table name: has_column prefixes "tab" itself.
	return frappe.db.has_column(doc.doctype, DRIVE_FOLDER_FIELD)

"""Controller for the ``Project Folder Google Drive Settings`` single doctype.

This Single (one record site-wide) stores the credentials used by
:mod:`erpnext_enhancements.crm_enhancements.drive_utils` to auto-provision
project folders in Google Drive:

* ``service_account_json`` (Code, required) — the Google service-account key.
* ``shared_drive_id`` (Data, required) — the target Shared Drive ID.

The controller has no custom behavior; it is a plain settings container read by
:func:`~erpnext_enhancements.crm_enhancements.drive_utils.get_drive_service`.
"""

import frappe
from frappe.model.document import Document


class ProjectFolderGoogleDriveSettings(Document):
	"""Plain settings document; no validation or hooks beyond the framework default."""

	pass

"""Controller for the ``Project Folder Google Drive Settings`` single doctype.

This Single (one record site-wide) stores the credentials used by
:mod:`erpnext_enhancements.google_drive.drive_utils` to auto-provision
project folders in Google Drive:

* ``service_account_json`` (Password, required) — the Google service-account key.
  Read it ONLY via ``drive_utils.get_service_account_info()``; plain attribute
  access returns Frappe's asterisk placeholder, which is truthy and then fails
  inside ``json.loads``. It was a Code field (i.e. cleartext, and rendered back
  onto the form) until v1.211.0.
* ``shared_drive_id`` (Data, required) — the target Shared Drive ID.

The controller has no custom behavior; it is a plain settings container read by
:func:`~erpnext_enhancements.google_drive.drive_utils.get_drive_service`.
"""

import frappe
from frappe.model.document import Document


class ProjectFolderGoogleDriveSettings(Document):
	"""Plain settings document; no validation or hooks beyond the framework default."""

	pass

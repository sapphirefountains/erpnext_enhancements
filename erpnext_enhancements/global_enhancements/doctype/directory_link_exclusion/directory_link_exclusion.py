# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the **Directory Link Exclusion** doctype.

A Directory Link Exclusion records that a particular Contact or Address has
been hidden from a source document's contact/address directory (e.g. a Project
or Opportunity). It is used by the contact-sync / directory feature
(``sync_contact.py``) so that suppressed links are not re-added on sync.

Stored fields (see the .json): ``ref_doctype`` / ``ref_name`` identify the
hidden Contact or Address, and ``source_doctype`` / ``source_name`` identify
the document it was hidden from. The reference is stored as plain Data (not a
Link) so that excluding a record never blocks that record's deletion. When a
referenced source document is deleted, ``sync_contact.cleanup_directory_exclusions``
(wired via ``on_trash`` in hooks.py) removes the stale exclusion rows.

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

from frappe.model.document import Document


class DirectoryLinkExclusion(Document):
	"""Plain Document controller for Directory Link Exclusion; no custom behaviour."""
	pass

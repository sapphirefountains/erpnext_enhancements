# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The reference library's one action: **Fetch again** on a Knowledge Source (2026-11-25).

Saving a source already queues it to be read (``knowledge_base/doctype/knowledge_source``). This is
for the rest: a link whose page changed, a source the deploy's FLUSHDB left Pending, a Gemini
summary that failed, or a Google file that was shared with ERPNext's Drive account after it failed.
It puts the source back to Pending and queues ``knowledge_base.sources.extract_source`` at once,
rather than waiting for the hourly sweep.

**POST, and only for someone who may change the source** (``check_permission("write")``: a KB Author,
KB Approver or System Manager). Every staff user reads the library, but a re-read replaces what the
AI reads, so it is a write. It returns nothing the form does not already show.
"""

import frappe
from frappe import _

from erpnext_enhancements.knowledge_base import constants, sources


@frappe.whitelist(methods=["POST"])
def refetch(source):
	"""Read ``source`` again. Returns ``{"source", "extraction_state"}``."""
	name = (source or "").strip() if isinstance(source, str) else ""
	if not name or not frappe.db.exists(constants.SOURCE_DOCTYPE, name):
		frappe.throw(_("That source does not exist."), frappe.DoesNotExistError)
	doc = frappe.get_doc(constants.SOURCE_DOCTYPE, name)
	doc.check_permission("write")
	frappe.db.set_value(
		constants.SOURCE_DOCTYPE,
		name,
		{"extraction_state": "Pending", "extraction_message": ""},
		update_modified=True,
	)
	sources.enqueue_extraction(name, frappe.db.get_value(constants.SOURCE_DOCTYPE, name, "modified"))
	return {"source": name, "extraction_state": "Pending"}

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Marketing Media Asset -- a photo or video a social post may use (TASK-2026-01481).

``usage_rights`` is the field that matters: the outbox refuses to queue a post whose media is
not *Cleared for social* (``outbox.enqueue_problems``). A photograph of a client's fountain
needs the client's approval first, and the default says so.

The one check here: the chosen source must say where the file is.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Source -> the field that must be filled for it.
SOURCE_FIELD = {"File": "file", "Google Drive": "drive_file_id", "Google Cloud Storage": "gcs_object"}


class MarketingMediaAsset(Document):
	def validate(self):
		fieldname = SOURCE_FIELD.get(self.source or "File")
		if fieldname and not (self.get(fieldname) or "").strip():
			frappe.throw(
				_("Source is {0}, so {1} is required.").format(
					self.source or "File", _(self.meta.get_label(fieldname))
				)
			)

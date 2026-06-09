# -*- coding: utf-8 -*-
# Copyright (c) 2024, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the User Form Draft doctype.

Stores a per-user autosaved draft of an in-progress form: the target document
(``ref_doctype`` + dynamic ``ref_name``), the owning ``user``, and the captured
field values as JSON in ``form_data``. Managed by ``api.user_drafts`` (save/load)
and pruned by the daily ``api.user_drafts.cleanup_stale_drafts`` job. The "All"
role can read/write only its own drafts (``if_owner``).

No custom controller logic; behaviour comes from the JSON field definitions.
"""

from frappe.model.document import Document

class UserFormDraft(Document):
	pass

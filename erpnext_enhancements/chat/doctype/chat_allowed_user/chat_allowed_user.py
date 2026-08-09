# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the **Chat Allowed User** child table.

One row per User permitted to use chat while ``Chat Settings.restrict_to_whitelist`` is
on. Modelled directly on ``Triton Allowed User``, which is the precedent this repo already
uses for exactly this job — a master ``enabled`` switch, a ``restrict_to_whitelist`` check
and an ``allowed_users`` child table, enforced server-side on every call rather than by
hiding the entry point.

``full_name`` is fetched read-only from the linked User so the grid is readable at a
glance; it is display only and nothing reads it.

Duplicate detection lives on the parent (``ChatSettings._whitelist_errors``) rather than
here, because a child controller cannot see its siblings without reaching back through
``self.parent``, and a duplicate row in a whitelist is a silent no-op rather than an
error the user would otherwise notice.

No custom server logic is required, so this is a plain pass-through ``Document``.
"""

from frappe.model.document import Document


class ChatAllowedUser(Document):
	"""Plain child-table controller for Chat Allowed User; no custom behaviour."""

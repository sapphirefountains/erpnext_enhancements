# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the **Triton Allowed User** child table.

Child table (``istable``) embedded in Triton Assistant Settings via the
``allowed_users`` field. Each row is one User permitted to see/use the Triton
assistant widget while the whitelist (``restrict_to_whitelist``) is active.
The ``full_name`` field is fetched read-only from the linked User.

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

import frappe
from frappe.model.document import Document


class TritonAllowedUser(Document):
    """Plain child-table controller for Triton Allowed User; no custom behaviour."""
    pass

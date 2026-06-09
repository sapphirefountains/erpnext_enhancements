# -*- coding: utf-8 -*-
# Copyright (c) 2024, jHetzer and contributors
# For license information, please see license.txt

"""Controller for the Project Note child doctype.

A free-text rich-text note (``istable``) attached to a parent (a Project, via a
custom notes table). Stores ``content`` plus read-only ``owner`` and ``creation``
stamps for attribution.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

from __future__ import unicode_literals
from frappe.model.document import Document

class ProjectNote(Document):
	pass

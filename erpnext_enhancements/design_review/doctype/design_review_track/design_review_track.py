# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One track of a review: a set of options that are ranked against each other.

Written by the importer only. The controller exists because ``tests/test_doctype_modules.py``
requires one beside every DocType JSON.
"""

from frappe.model.document import Document


class DesignReviewTrack(Document):
	pass

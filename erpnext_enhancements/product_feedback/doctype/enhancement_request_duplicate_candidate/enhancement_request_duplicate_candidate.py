# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An existing ``Task`` the model thinks this request may already be covered by.

A plain child table with no behaviour. The controller exists because
``tests/test_doctype_modules.py`` requires one next to every DocType JSON, child tables
included — see the sibling ``Enhancement Request Proposed Task`` for the deploy failure
that rule was written after.

These rows are advisory. Acting on one is ``api.feedback.review_decision`` with
``decision="duplicate"``, which is a human closing the request against a named Task.
"""

from frappe.model.document import Document


class EnhancementRequestDuplicateCandidate(Document):
	pass

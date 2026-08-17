# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One person on the notification list for new enhancement requests.

A plain child table with no behaviour. The controller exists because
``tests/test_doctype_modules.py`` requires one next to every DocType JSON, child tables
included.

Being on this list is *notification only* — it grants nothing. Approving is gated on the
``System Manager`` role in ``api.feedback.review_decision``, so a name here who is not a
System Manager is told about requests they cannot decide, and a System Manager who is not
here can decide requests nobody told them about.
"""

from frappe.model.document import Document


class ProductFeedbackReviewer(Document):
	pass

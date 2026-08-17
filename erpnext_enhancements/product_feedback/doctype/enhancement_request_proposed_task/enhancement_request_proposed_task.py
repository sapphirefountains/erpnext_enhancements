# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One proposed ``Task``, before anybody has agreed to create it.

No behaviour: this is a plain child table. The controller exists because
``tests/test_doctype_modules.py`` requires one next to every DocType JSON, child tables
included — v1.268.0 shipped a child table with no controller, ``bench migrate`` raised
``ModuleNotFoundError`` partway through, and *every subsequent deploy of any change* failed
identically until it was added.

Validation of these rows lives in :mod:`erpnext_enhancements.product_feedback.breakdown`
(on the way in, from the model) and ``api.feedback.create_tasks`` (on the way out, from the
reviewer's edits). It is deliberately not here: a child row saved as part of its parent
cannot usefully throw about a Project allowlist the parent knows and it does not.
"""

from frappe.model.document import Document


class EnhancementRequestProposedTask(Document):
	pass

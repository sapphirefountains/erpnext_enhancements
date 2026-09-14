# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One person's copy of a Critical non-conformance alert — WI-075 sub-phase G.

The build spec asks for acknowledgement *per recipient*, and the distinction is the whole
point: "the alert was sent" is a fact about this system, while "the President has seen this"
is a fact about the company. Only the second one is worth having, and it needs a row per
person to be true of anybody in particular.

Every field is read-only. A row is written by the dispatch worker, the hourly sweep, or the
acknowledge endpoint — never by hand on the form. An acknowledgement somebody could type into
a grid is not evidence they read anything, and this record exists to be evidence.

Controller-free by design. The rules live in
:mod:`erpnext_enhancements.quality.alerting`, which imports no ``frappe`` and is tested
without a bench — deciding who gets paged is not something to leave unasserted until somebody
next runs a bench, and this repo has no Frappe integration-test job.

The class name is ``NCRAcknowledgment`` because Frappe derives a controller class as
``doctype.replace(" ", "").replace("-", "")`` — it strips spaces and does **not** title-case.
Get that wrong by one letter and ``remove_orphan_doctypes()`` force-deletes the DocType on
every migrate, silently. ``tests/test_doctype_controller_names.py`` fails the build on it.
"""

from frappe.model.document import Document


class NCRAcknowledgment(Document):
	pass

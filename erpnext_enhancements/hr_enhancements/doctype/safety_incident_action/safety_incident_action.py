# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One thing the company is changing because of an incident.

Deliberately empty of logic — the parent stamps `done_on`, and there is nothing
here to validate.

Two decisions live in the JSON rather than in code and are worth reading there:
the owner is a **plain name and not a Link**, the same rule the onboarding
checklist follows because a required assignee is how a list stops getting filled
in; and `raised_reference` records the Training Assignment or Task this became, if
it became one. A corrective action that stays a sentence on a form is a corrective
action nobody does, and the field exists to make that visible rather than to
pretend otherwise.

Present at all because `bench migrate` calls `load_doctype_module()` for every
DocType including child tables, and raises `ModuleNotFoundError` without it.
"""

from frappe.model.document import Document


class SafetyIncidentAction(Document):
	pass

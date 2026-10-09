# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One weekly schedule of a Planner Resource: hours worked on each weekday.

A child table with no logic of its own. A row with dates is a seasonal schedule and wins over a
row with none for the days its range covers; the rule that picks the row lives in
`project_enhancements/crew_availability.pattern_hours`, and the bounds checks (0 to 24 hours,
end not before start) live on the parent's `validate`, so there is one place each rule can
disagree with itself.

The class exists because Frappe resolves a controller by name and force-deletes the DocType when
the import fails. The name is `doctype.replace(" ", "")`, so `Planner Resource Work Pattern` must
be `PlannerResourceWorkPattern`.
"""

from frappe.model.document import Document


class PlannerResourceWorkPattern(Document):
	pass

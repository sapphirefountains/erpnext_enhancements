# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One document a scorecard measure counted -- WI-075 sub-phase N.

Frappe has no grandchild tables, so evidence cannot hang off `Scorecard Measure` directly. It
hangs off the scorecard instead and carries `measure_key`, which is the same flattening the
Sapphire Maintenance module uses for its sections and items. Nothing joins on `idx`.

The point of these rows is that a disputed score can be opened rather than argued.
"""

from frappe.model.document import Document


class ScorecardEvidence(Document):
	pass

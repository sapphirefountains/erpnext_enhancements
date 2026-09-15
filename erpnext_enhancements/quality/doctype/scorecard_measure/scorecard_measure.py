# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One measured line of a subcontractor scorecard -- WI-075 sub-phase N.

No logic of its own. Every row here is written by `quality/scorecard_build.py` from the rules in
`quality/scorecard.py`, and a child controller with opinions would be a third place the same
rules could disagree.

The class exists because Frappe derives a controller name as
`doctype.replace(" ", "").replace("-", "")` and force-deletes the DocType when the import fails --
silently, inside `bench migrate`, which on this repo is the deploy.
"""

from frappe.model.document import Document


class ScorecardMeasure(Document):
	pass

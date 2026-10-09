# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A cached drive time between two points, as Google Routes measured it.

Why it exists: the Project Planner asks how long it takes to drive between every pair of stops
on a crew's day, and a Routes matrix call costs money and time. The same pairs come back day after
day, because a site does not move and the shop does not move. So each Google answer is stored
here, keyed on the two rounded coordinates, and the routing module reads this table before it
calls Google again.

Only Google answers are stored. An estimate is a formula, and storing one would make a later
Google outage look like a measurement. The routing module refreshes a row older than 90 days.

Nobody types into this doctype, so every field is read-only and the New button is hidden
(`in_create`). The class name is `PlannerDriveTime` because Frappe derives it as
`doctype.replace(" ", "")`; `tests/test_doctype_controller_names.py` guards it.
"""

from frappe.model.document import Document


class PlannerDriveTime(Document):
	pass

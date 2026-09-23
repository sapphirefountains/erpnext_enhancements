# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Social Post Metric -- one published post on one account: its lifetime engagement as of one date.

Filled nightly by ``marketing/publish/metrics_sync.py`` (TASK-2026-01488). A row is a total to date,
not a day's increment (three of the four networks report nothing per day, and reach does not add
up across days), so a post's figures are its newest row. Named from (job, date), so a restated day
updates in place.
"""

from frappe.model.document import Document


class SocialPostMetric(Document):
	pass

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for GA4 Daily Metric. A plain model: every row is written by the nightly pull (see the
DocType description), and no rule lives here."""

from frappe.model.document import Document


class GA4DailyMetric(Document):
	pass

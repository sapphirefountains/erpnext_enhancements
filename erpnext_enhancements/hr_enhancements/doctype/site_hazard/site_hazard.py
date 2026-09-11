# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Something a technician found at a site, and the next person sent there.

**The second half is the point.** A hazard report that only files a ticket
protects nobody standing at that hatch tomorrow -- so an open hazard is shown in
the red safety banner the next technician is already forced to read before they
start. Reporting one takes thirty seconds; reading one takes none, because it
arrives inside a screen they were going to look at anyway.

`Accepted risk` is a real status. Some hazards are not going to be fixed -- an
awkward hatch, a permanent step -- and a row that stays Open for ever is a row
people stop reading. It still shows in the banner, because the next person still
needs to know.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class SiteHazard(Document):
	def before_insert(self):
		self.reported_by = frappe.session.user
		self.reported_on = now_datetime()

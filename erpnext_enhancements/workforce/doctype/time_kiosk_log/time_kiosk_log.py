# Copyright (c) 2023, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the Time Kiosk Log doctype.

One geolocation/clock point captured by the Time Kiosk PWA: an Employee/User, a
``timestamp``, a ``log_status`` (Success / Permission Denied / Error / Offline
Sync), the GPS fix (lat/long plus accuracy, speed, heading, altitude) and the
``device_agent``. Each point links to the owning clock-in session via
``job_interval`` (see the Job Interval doctype).

Rows are inserted in batches by ``api.time_kiosk`` (the kiosk's location push),
read back by the Location Timeline page, and purged after
``retention_days`` by the daily ``api.time_kiosk.purge_old_location_logs`` job.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

# import frappe
from frappe.model.document import Document

class TimeKioskLog(Document):
	pass

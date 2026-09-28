# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for **Trip Change Alert**: one person's pending (or sent) change alert on one
trip — what changed on their bookings since they were last told, kept until the edits stop
and the scheduler emails it (``travel_management/change_alerts.py``).

The rows are written by code only: ``change_alerts.record_trip_changes`` (from the Travel
Trip ``on_update`` dispatcher) and ``change_alerts.send_due_change_alerts`` (every five
minutes). They live in the database rather than in a queued job so that a deploy, which
flushes the job queue, loses nothing: the next run sends what is due. A travel coordinator
can set a Failed row back to Pending to send it again.

No custom server logic, so the controller is a plain ``Document``. The class name is what
frappe derives from the DocType name (``TripChangeAlert``); a different one is force-deleted
by the next ``bench migrate`` (``tests/test_doctype_controller_names.py``).
"""

from frappe.model.document import Document


class TripChangeAlert(Document):
	pass

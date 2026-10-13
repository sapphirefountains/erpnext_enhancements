# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who has had their leave-by ping, for which stop, on which day (v1.606.0).

``project_enhancements.leave_by.send_leave_by_pings`` runs every five minutes and tells each
opted-in person when they must leave for the first stop of their day. A text costs money and a
second one is noise, so it must send **at most once per person per stop per day** however many
times it runs.

The same design as ``Planner Digest Log``, for the same reasons:

* **Not redis.** The production deploy ``FLUSHDB``s both redis instances, so a cache marker would
  vanish with every merge to main and the next tick would ping everyone again.
* **A unique key claimed in one step.** ``user|date|stop`` is inserted and committed *before*
  anything is sent; a second worker's insert fails at the database, so exactly one run sends, and
  a send that fails half way is not retried. ``channels`` records what actually went.

Nobody types into this doctype. The class name is the one Frappe derives.
"""

from frappe.model.document import Document


class PlannerPingLog(Document):
	pass

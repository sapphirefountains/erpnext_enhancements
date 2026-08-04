# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Job Interval Photo — one captured image on a Job Interval (child table).

A child table rather than a fixed set of Attach fields because job counts vary:
a ten-minute filter change produces one photo and a pond rebuild produces forty,
and any fixed number is wrong for one of them.

The row exists from the moment the shutter fires on the device, **before** the
bytes reach the server. ``client_uid`` is minted device-side at capture and is
what makes re-posting idempotent when a queued upload retries over a flaky link.
``upload_status`` tracks how far the bytes have got:

* ``Pending``  — captured, queued on the device
* ``Uploaded`` — the File exists on this server
* ``Failed``   — the device gave up

**A Pending row satisfies the capture gate.** That is the deliberate policy
decision behind this whole doctype: the requirement is that the technician
photographed the job, not that a cellular upload succeeded while they were
standing in a mechanical room. See ``workforce/photo_gate.py``.

No controller logic — the row is written by ``api.time_kiosk.record_job_photo``
and read by the gate and the routing job.
"""

from frappe.model.document import Document


class JobIntervalPhoto(Document):
	pass

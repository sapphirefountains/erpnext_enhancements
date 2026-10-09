# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who has had their combined 6 AM message today (Project Planner Phase 3B).

``project_enhancements.planner_digest.send_daily_digests`` texts and emails each person their day.
A text costs money and a second one is noise, so the job must send **at most once per person per
day** however many times it runs (a scheduler catch-up, a manual re-run, two workers).

Why a row here rather than a redis key or a date field on the person:

* **Not redis.** The production deploy ``FLUSHDB``s both redis instances, so a cache marker would
  vanish with every merge to main and the next run would text everyone again.
* **Not a field on Planner Resource.** A "last digest date" field is read, compared and written in
  three steps, and two workers can both read "not yet" before either writes. A row keyed
  ``user|date`` with a **unique** index is claimed in one step: the second insert fails at the
  database, so exactly one run sends. It also leaves a record of what went out each morning,
  which a single overwritten date cannot.

The day is claimed (row inserted and committed) **before** anything is sent, so a send that fails
half way is not retried: at most once, by design. ``channels`` then records what actually went.

Nobody types into this doctype. The class name is the one Frappe derives.
"""

from frappe.model.document import Document


class PlannerDigestLog(Document):
	pass

# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar sync (C2): committed planner work, pushed one way to Sapphire's shared Google Calendars.

* ``payload.py``  - pure: an item → Google's event body, ids, hashes, the snap-back decision table.
* ``sources.py``  - ERPNext → items: tasks, visits, projected visits, day notes, blocks.
* ``google_api.py`` - OAuth through Google Settings' client and the Calendar v3 REST calls.
* ``engine.py``   - reconcile one item, the nightly repair, snap-back, the preview, health.
* ``triggers.py`` - the ``doc_events`` that queue a sync after commit.
* ``timing.py``   - C3 (v1.606.0): route stops in the near window get their route's times and a
  leave-by line (``project_enhancements/route_slots.py`` does the planner's arithmetic). The opt-in
  leave-by ping is ``project_enhancements/leave_by.py``.

Settings: ``Calendar Sync Settings`` (System Manager only, off by default). The record of what was
written: ``Calendar Sync Link``. Endpoints: ``api/calendar_sync.py``. See the Calendar sync section of
``project_enhancements/README.md``.
"""

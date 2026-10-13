# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar sync (C2): committed planner work, pushed one way to Sapphire's shared Google Calendars.

* ``payload.py``  - pure: an item → Google's event body, ids, hashes, the snap-back decision table.
* ``sources.py``  - ERPNext → items: tasks, visits, projected visits, day notes, blocks.
* ``google_api.py`` - OAuth through Google Settings' client and the Calendar v3 REST calls.
* ``engine.py``   - reconcile one item, the nightly repair, snap-back, the preview, health.
* ``triggers.py`` - the ``doc_events`` that queue a sync after commit.
* ``time_off_rules.py`` - pure (C4): who is off and when, from one vacations-calendar event.
* ``time_off.py`` - C4: time off read IN from the Company Holidays & Vacations calendar as Unavailable
  Planner Blocks (``source`` "Google", never synced back out), the review list, alerts, the dry run.

Settings: ``Calendar Sync Settings`` (System Manager only, off by default). The record of what was
written: ``Calendar Sync Link``. Endpoints: ``api/calendar_sync.py``. See the Calendar sync section of
``project_enhancements/README.md``.
"""

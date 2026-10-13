"""Give Calendar Sync Settings (new in v1.604.0) a ``tabSingles`` row for every declared default.

A Single that has never been saved has no rows, and the calendar sync writes its own state into this
one (sync tokens, last-run stamps). The first such write of one field would make every other field
load blank: ``days_back`` and ``days_ahead`` would read 0 and shrink the repair window to today.
``engine.write_state`` also calls ``materialize_defaults`` before every write; this does it once at
install so the Settings page reads its defaults from the start.

Only fields with no row are written, never over a stored value. The master switch's default is 0, so
this switches nothing on. Cannot raise; safe twice.
"""


def execute():
	from erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings.calendar_sync_settings import (
		materialize_defaults,
	)

	materialize_defaults()

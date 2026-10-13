"""Give the v1.605.0 (calendar sync C3) Settings fields a ``tabSingles`` row where they have none.

Three new fields on two Singles that may already have rows on a site:

* Calendar Sync Settings ``timed_days_ahead`` (default 14): how far ahead route stops get times;
* Project Planner Settings ``leave_by_pings`` (default 0) and ``leave_by_ping_minutes`` (default 10).

A default never reaches a Single's existing row (CLAUDE.md), and a field with no row loads blank.
Every reader here treats None as the default, but the Settings *form* would show the Int blank, and
the first save would store 0: timing switched off, or pings with no lead time, with nothing on screen
to say so. ``materialize_defaults`` writes a row for each declared default that has none, never over
a stored value (a deliberate 0 stays 0). Both pings and the calendar sync stay off. Cannot raise;
safe twice.
"""


def execute():
	from erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings import (
		calendar_sync_settings,
	)
	from erpnext_enhancements.project_enhancements.doctype.project_planner_settings import (
		project_planner_settings,
	)

	calendar_sync_settings.materialize_defaults()
	project_planner_settings.materialize_defaults()

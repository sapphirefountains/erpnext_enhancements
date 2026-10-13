"""Give C4's new Calendar Sync Settings fields (v1.607.0) their ``tabSingles`` rows.

``materialize_calendar_sync_settings`` (v1.604.0) ran once, before these fields existed, and a patch
never runs twice. A Single's new field gets no row from ``bench migrate`` (CLAUDE.md), and the
time-off read writes its own state into this Single (its sync token, the last-read stamps): without
the rows the first such write would make *Read time off from Google* and every other field without a
row load blank. ``materialize_defaults`` writes a row only for a field that has none, never over a
stored value; *Read time off from Google* defaults to 0, so this switches nothing on. Cannot raise;
safe twice.

Planner Block's new ``source`` needs no patch: it is a normal doctype, so the ALTER that adds the
column writes its default, "Planner", into every existing row, and the import only ever matches
"Google". Existing blocks read as made in ERPNext, which is what they are.
"""


def execute():
	from erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings.calendar_sync_settings import (
		materialize_defaults,
	)

	materialize_defaults()

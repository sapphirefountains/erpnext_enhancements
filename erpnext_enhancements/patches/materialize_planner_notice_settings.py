"""Give Project Planner Settings rows for the Phase 3B "Telling people" switches.

**A ``default`` on a new field of a Single never reaches the row that already exists** (CLAUDE.md).
Phase 3B added ``combined_morning_digest`` and ``change_alerts`` to Project Planner Settings, a
Single that has had ``tabSingles`` rows since v1.578.1. ``bench migrate`` writes no row for a new
field, so both would read ``None`` on production while the JSON says ``0``.

Both defaults are ``0`` (off), so ``None`` happens to mean the same thing today, and the readers
treat ``None`` as off. The rows are written anyway, for the reason ``materialize_defaults`` exists:
a Single with some rows and not others is the state that switched drive padding and Google Routes
off by themselves in v1.578.1, and every later field added here should find the Single whole.

Calls ``project_planner_settings.materialize_defaults``, which fills only fields with **no** row
(an unticked box someone chose is a row, and is never overwritten), reads ``tabSingles`` with raw
SQL rather than ``db.get_value("Singles", ...)``, and cannot raise. Safe twice.
"""

from erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings import (
	materialize_defaults,
)


def execute():
	materialize_defaults()

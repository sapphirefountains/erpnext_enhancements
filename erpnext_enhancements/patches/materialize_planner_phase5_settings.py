"""Give Project Planner Settings rows for the Phase 5 "Telling customers" fields.

**A ``default`` on a new field of a Single never reaches the row that already exists** (CLAUDE.md).
Phase 5 added ``customer_date_confirmations`` (default ``0``) and ``customer_confirmation_phone``
(no default) to Project Planner Settings, a Single that has had ``tabSingles`` rows since v1.578.1.
``bench migrate`` writes no row for a new field, so the switch would read ``None`` on production
while the JSON says ``0``.

``None`` already means off to every reader (``customer_confirmations.enabled`` reads it through
``cint``), and off is the point: Nik wants the feature dark until the email is designed. The row
is written anyway, for the reason ``materialize_defaults`` exists: a Single with some rows and not
others is the state that switched drive padding and Google Routes off by themselves in v1.578.1.

Calls ``project_planner_settings.materialize_defaults``, which fills only fields with **no** row
(a box someone ticked is a row, and is never overwritten), reads ``tabSingles`` with raw SQL rather
than ``db.get_value("Singles", ...)``, and cannot raise. Safe twice.
"""

from erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings import (
	materialize_defaults,
)


def execute():
	materialize_defaults()

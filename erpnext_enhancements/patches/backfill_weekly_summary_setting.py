"""Give Project Planner Settings its row for the weekly summary switch (P9, v1.715.0).

**A ``default`` on a new field of a Single never reaches the row that already exists** (CLAUDE.md).
Planner UX redesign P9 added ``weekly_summary_email`` (a Check, default ``0``) and
``weekly_summary_recipients`` (no default) to Project Planner Settings, a Single that has had
``tabSingles`` rows since v1.577.0. ``bench migrate`` writes no row for a new field, so the switch
reads ``None`` on production while the JSON says ``0``.

``None`` already means off to every reader (``planner_notices.setting`` reads it through ``cint``),
and off is the point: Nik, 2026-10-21 (D16), wants the Monday email dark until he turns it on. The row
is written anyway, for the reason ``project_planner_settings.materialize_defaults`` exists: a Single
with some rows and not others is the state that switched drive padding and Google Routes off by
themselves in v1.578.1, and keeping it whole means the next field somebody adds meets no surprise.

This is the shape of ``backfill_marketing_settings_defaults``, through the Single's own
``materialize_defaults``:

* **Only where there is no row.** A field is filled only where ``tabSingles`` has none, never over a
  stored value. An unticked box and a deliberate ``0`` are different decisions, and a box somebody
  already ticked is a row. Reads ``tabSingles`` with raw SQL, never ``db.get_value("Singles", ...)``,
  which orders by ``creation`` and cannot succeed on that table.
* **Written with ``set_single_value``**, which runs no ``validate``: the validate is exactly what a
  half-filled Single would trip, so a patch that went through the document API could not repair it.
* **The inverse trap is closed too.** Called on a never-saved Single it makes the Single whole (every
  declared default), so no later write of one field (the routing module's cache of the shop's
  coordinates, say) can end ``new_doc()`` defaults for the rest.

Switches nothing on: the Check's default is ``0`` and the recipients have none. Cannot raise (a patch
that raises aborts ``bench migrate``, which on this repo is the deploy): ``materialize_defaults`` logs
its own failure and answers 0, and this wraps even that. Safe twice: the second run finds a row for
everything and writes nothing.
"""

import frappe

from erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings import (
	materialize_defaults,
)


def execute() -> None:
	try:
		materialize_defaults()
	except Exception:
		# A fixed message and no traceback: this runs inside bench migrate, and the box is a switch
		# nobody has turned on, so a failure here must not stop the deploy.
		try:
			frappe.log_error(
				title="Project Planner: weekly summary setting",
				message="The weekly summary switch row was not written; open and save Project Planner Settings once.",
			)
		except Exception:
			pass

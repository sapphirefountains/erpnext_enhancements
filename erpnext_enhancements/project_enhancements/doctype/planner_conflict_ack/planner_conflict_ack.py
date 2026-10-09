# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A conflict kept on purpose from the Conflict center (Project Planner Phase 6D, TASK-2026-02470).

"Keep it with a reason" in the Conflict center (``api/planner_conflicts.acknowledge_conflict``)
writes one of these and a timeline comment on every record the keeping planner owns, the same
record the drag prompt's reason would land on.

* ``conflict_key`` is the conflict's stable key (kind, date, person or equipment, and a hash of
  the records involved), so the same conflict finds its acknowledgement on every load.
* ``fingerprint`` is a hash of the involved records' ``modified`` stamps and the conflict's
  wording. The acknowledgement hides the conflict **only while it still matches**: moving a task,
  editing a visit, changing a block or the day's hours all bring the conflict back, because what
  was kept is not what is there now.
* Rows are never edited and never deleted by the planner; a newer acknowledgement of the same key
  simply wins. Nobody types into this doctype (``in_create``, every field read-only).

The class name is the one Frappe derives (``doctype.replace(" ", "")``);
``tests/test_doctype_controller_names.py`` guards it.
"""

from frappe.model.document import Document


class PlannerConflictAck(Document):
	pass

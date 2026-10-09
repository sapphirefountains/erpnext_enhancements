# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One planner's unpublished change to one Task (Project Planner Phase 3B, draft and publish).

Draft mode lets a planner rearrange the week without anyone being told: every drag and dialog save
lands here instead of on the Task, and nobody's assignments, digests or alerts move until the
planner presses Publish (``api.project_planner.publish_drafts``).

* **One Draft row per (owner, task).** Re-drafting the same task merges into its row, so the
  payload is always "everything this planner has changed on this task", never a history.
* ``payload`` holds ``save_task``'s arguments as JSON (``start``, ``end``, ``expected_time``,
  ``crew_size``, ``crew``, ``credentials``, ``tentative``), and publishing replays them through
  the very same code path as a live save, so a published draft is checked exactly like a drag.
* ``base_modified`` is the Task's ``modified`` when it was first drafted. A task somebody else has
  changed since is **not** published over: it is skipped and reported "changed by someone else".
* Rows are never deleted by the planner: Publish marks them Published, Discard marks them
  Discarded, so the list doubles as a record of what was published when and why.

Nobody types into this doctype (``in_create``, every field read-only): the planner page writes it.
Projects Users and Managers see only their own rows (``if_owner``). The class name is the one
Frappe derives (``doctype.replace(" ", "")``); ``tests/test_doctype_controller_names.py`` guards it.
"""

from frappe.model.document import Document


class PlannerDraftChange(Document):
	pass

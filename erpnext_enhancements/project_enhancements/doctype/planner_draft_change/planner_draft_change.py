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

**Visits too (v1.601.0).** The Maintenance Planner's draft mode (``api.maintenance_drafts``) keeps
its drafts here as well: a row names **either** a ``task`` (Project Planner) **or** a ``visit``
(a Sapphire Maintenance Record), never both, and :meth:`PlannerDraftChange.validate` holds that,
since ``task`` stopped being mandatory. One doctype rather than a second one because a draft of a
visit is the same thing with the same life (Draft, then Published or Discarded, ``base_modified``
optimistic locking, one row per owner and target) and the Google Calendar sync that follows reads
"what is still only drafted" from one place. A plain second Link rather than a Dynamic Link pair
because every Task row on production already has ``task`` set: nothing is migrated, and each planner
filters on its own column (``task`` / ``visit`` is set), so neither ever reads, publishes or discards
the other's rows. A visit row's ``subject`` is written by the planner ("Highlands · SMR-0001");
``fetch_from`` fills it only for a task. Maintenance Supervisors and Maintenance Users, the
Maintenance Planner's roles, get the same own-rows permissions as Projects Users.

**New tasks too (v1.603.0).** ``kind`` is ``Change`` (everything above, and what every row already
has: this is a normal doctype, so the ``ALTER`` that adds the column writes its default into every
existing row and no backfill patch is needed) or ``New task``: a task that does not exist yet. A
``New task`` row names neither a ``task`` nor a ``visit``; it carries the ``project`` it will be
created on, and the Task it will become as a spec in ``payload`` (``api.project_planner.load_spec``:
the Task's fields, crew, qualifications, equipment and what it waits on, where another drafted task
is written ``draft-new:<that row's name>``). It is **not a Task until Publish**: no assignment,
digest, change alert, conflict-centre line or calendar event can come from it, and
``api.project_planner.publish_drafts`` creates it through the very code path the live quick add,
duplicate and build out use. Once published, ``created_task`` names the Task it became. The Project
Planner reads its own ``task`` rows and its own ``New task`` rows; the Maintenance Planner reads
only ``visit`` rows, so it never sees a ``New task`` row.
"""

import frappe
from frappe import _
from frappe.model.document import Document

NEW_TASK = "New task"


class PlannerDraftChange(Document):
	def validate(self):
		if (self.get("kind") or "Change") == NEW_TASK:
			# A task that does not exist yet has nothing to hang a draft on, and must say where it goes.
			if self.get("task") or self.get("visit"):
				frappe.throw(_("A drafted new task changes no existing task or visit."))
			if not self.get("project"):
				frappe.throw(_("A drafted new task needs the project it will be created on."))
			return
		# One target per row: what ``reqd`` on ``task`` used to promise, now that a row may be a visit's.
		if bool(self.get("task")) == bool(self.get("visit")):
			frappe.throw(_("A planner draft changes exactly one task or one visit."))
		if self.get("project"):
			frappe.throw(_("Only a drafted new task names a project."))

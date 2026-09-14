# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The scope, authored once and locked — WI-075 sub-phases B1 and B2.

Scope, contract terms, pricing and inspection standards have historically lived in four
separate places, and when they disagree there is no single record either side can point to.
This is that record: one submitted ``Project Scope of Work`` per project, carrying measurable
acceptance criteria that the Statement of Work and the inspection template both read from
rather than restate.

**Submit is the lock.** There is no separate "approved" flag to fall out of step with
``docstatus`` — the framework already has exactly one boolean for "this document is final", and
a second one would only ever be wrong.

Not a hand-off step, deliberately
---------------------------------

B2 wires this to the Project and stops there. Locking a scope stamps
``Project.custom_scope_of_work`` and ``custom_scope_locked_on`` — and touches nothing else.

The obvious alternative was a new step in the 7-step hand-off tracker, which is how the rest
of this process is modelled. It was considered and **declined for now** (Nik, 2026-09-14), on
the blast radius rather than the idea: the record links to a Project so it cannot precede step
3, inserting it anywhere earlier than last means renumbering steps that 707 live
``Project Process Step`` rows already carry, and ``hand_off_sla_compliance`` hardcodes
``LAUNCH_STEP_NUMBER = 7`` and would quietly stop computing the launch deadline. None of that
is hard; it is simply not worth buying before anyone has locked a real scope and found out
where the step belongs.

So the step, if it comes, is its own change against a quieter diff. Nothing here assumes it.
"""

import frappe
from frappe import _
from frappe.model.document import Document


def _criteria_rules():
	"""Import the criterion rules lazily. **This is not a style choice.**

	This controller lives in ``project_enhancements`` and the rules live in ``quality``. A
	module-scope cross-module import cost this DocType its existence on the v1.452.1 deploy:
	frappe's ``remove_orphan_doctypes()`` runs on every migrate, calls ``clear_controller_cache()``
	and then ``get_controller()`` on every non-custom DocType, and **force-deletes any whose
	controller raises ImportError or DoesNotExistError** ::

	    except (ImportError, frappe.DoesNotExistError):
	        orphan_doctypes.append(doctype)
	    ...
	    frappe.delete_doc("DocType", name, force=True, ignore_missing=True)

	On the release that introduced the ``quality`` package, that import did not resolve during
	the sweep, and this DocType was created by model sync at ~12:08 and deleted at 12:09:22 the
	same migrate. Nothing failed the deploy, nothing reached the Error Log, and the table it had
	just created stayed behind — MariaDB DDL auto-commits, so the schema survived the rollback
	of the row. The only trace was a `Deleted Document` entry.

	Note what made it survivable and what made it invisible: the module ships dormant, so the
	missing DocType broke nothing, and ``Project.custom_scope_of_work`` was left as a Link to a
	DocType that no longer existed — inert until somebody opened the picker.

	Importing inside the call means the controller module itself has no cross-module dependency
	to fail, so the sweep can always import it. The two ``quality`` controllers that import from
	their own package survived the same migrate, which is why only this one is lazy.
	"""
	from erpnext_enhancements.quality import scope_criteria

	return scope_criteria


class ProjectScopeOfWork(Document):
	def validate(self):
		self._mint_criterion_keys()
		self._reject_duplicate_keys()

	def before_submit(self):
		self._require_inspectable_criteria()
		self._reject_second_lock()
		# Server clock and server session, never the browser's. The lesson is
		# `process_steps.complete_step`: the old client path let the browser propose
		# `completed_on`, and the audit found retroactive box-checking.
		self.locked_on = frappe.utils.now_datetime()
		self.locked_by = frappe.session.user
		self.status = "Locked"

	def on_submit(self):
		self._mirror_onto_project()

	def on_cancel(self):
		# `status` is allow_on_submit so it can move without a new revision. A cancelled scope
		# is superseded rather than back to Draft: the draft it came from is gone, and calling
		# it Draft would invite somebody to treat it as still in play.
		self.status = "Superseded"
		self._mirror_onto_project(clear=True)

	# ------------------------------------------------------------------ helpers

	def _mirror_onto_project(self, clear=False):
		"""Stamp (or clear) the Project's pointer at its locked scope.

		Swallows and logs rather than raising. A failure to mirror must not undo a lock the
		user just performed: the authoritative record is this document, the Project fields are
		a convenience for everything that reads a Project without wanting to join. Same shape
		as ``crm_enhancements.handoff._mirror_onto_project``, and for the same reason.

		Written with ``frappe.db.set_value`` and never ``doc.save()``. WI-057 states why:
		Project carries heavy ``on_update`` hooks and a wildcard ``'*'`` ``after_save`` that
		fires ``global_triton_sync`` on every ORM save. ``update_modified=False`` because this
		is a read-only stamp — bumping the Project's timestamp would make it look edited to
		every concurrent editor and to anything that syncs on ``modified``.
		"""
		try:
			if not self.project or not frappe.db.exists("Project", self.project):
				return
			# has_column takes a DOCTYPE and prefixes "tab" itself, and raises
			# TableMissingError on an unknown table rather than returning False. Passing
			# "tabProject" here would be a guaranteed crash dressed up as a guard.
			if not frappe.db.has_column("Project", "custom_scope_of_work"):
				return

			frappe.db.set_value(
				"Project",
				self.project,
				{
					"custom_scope_of_work": None if clear else self.name,
					"custom_scope_locked_on": None if clear else self.locked_on,
				},
				update_modified=False,
			)
		except Exception:
			# No bare re-raise: a re-raise out of here would publish this frame's locals to
			# the Error Log, and the scope body is customer contract text.
			frappe.log_error(
				title="Project Scope of Work: could not mirror onto Project",
				message=f"scope={self.name} project={self.project} clear={clear}\n\n{frappe.get_traceback()}",
			)

	def _mint_criterion_keys(self):
		"""Give every criterion a stable identity before anything can point at it."""
		_criteria_rules().mint_missing_keys(self.acceptance_criteria, frappe.generate_hash)

	def _reject_duplicate_keys(self):
		"""Two rows sharing a key is a couple of grid clicks away — Frappe's row-duplicate
		action copies read-only fields too — and a downstream join would then resolve to
		whichever row it read first."""
		duplicates = _criteria_rules().duplicate_keys(self.acceptance_criteria)
		if duplicates:
			frappe.throw(
				_("Two acceptance criteria share the same key: {0}. Delete the duplicated row and add a fresh one.").format(
					", ".join(duplicates)
				),
				title=_("Duplicated criterion"),
			)

	def _require_inspectable_criteria(self):
		"""A scope with nothing measurable in it is the problem this record exists to end.

		Checked at submit rather than at save so a half-written draft can be parked. The
		blank-check uses ``strip()`` in Python rather than a SQL comparison on purpose: under
		MariaDB's default PAD SPACE collation a value of ``" "`` compares equal to ``""``, so
		the obvious SQL form of this test reports clean on data that is genuinely empty.
		"""
		if not self.acceptance_criteria:
			frappe.throw(
				_("Add at least one acceptance criterion before locking. A scope nobody can inspect against is what this record exists to prevent."),
				title=_("Nothing to inspect"),
			)

		problems = _criteria_rules().incomplete_rows(self.acceptance_criteria)
		if problems:
			lines = [
				_("Row {0}: missing {1}").format(idx, ", ".join(missing))
				for idx, missing in problems
			]
			frappe.throw(
				_("These criteria cannot be inspected as written:<br>{0}").format("<br>".join(lines)),
				title=_("Criteria not measurable"),
			)

	def _reject_second_lock(self):
		"""At most one locked scope per project.

		Enforced here rather than by a unique index: a cancelled or amended row keeps its name
		and its ``project``, so a database constraint would refuse the legitimate re-lock after
		an amendment. ``docstatus = 1`` is the real predicate.
		"""
		existing = frappe.get_all(
			"Project Scope of Work",
			filters={"project": self.project, "docstatus": 1, "name": ("!=", self.name)},
			pluck="name",
			limit=1,
		)
		if existing:
			frappe.throw(
				_("{0} already has a locked Scope of Work ({1}). Amend that one, or raise a Change Order against it.").format(
					self.project, existing[0]
				),
				title=_("Scope already locked"),
			)

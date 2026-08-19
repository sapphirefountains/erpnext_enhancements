# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Completion — the audit artefact. One person passed one version, once.

This is the only record in the module that anybody outside it will ever be asked
to produce: to a client, to an insurer, to whoever wants proof that the crew on
their site had been trained before they turned up. Everything about it is shaped
by that.

**Submittable, and that is the mechanism, not decoration.** Submitted means
issued; the framework then refuses to let anybody edit it. Withdrawing one is a
cancel, which leaves the row visible with ``docstatus 2`` and a reason attached,
rather than a delete that leaves a hole nobody can explain.

**Everything material is snapshotted, not fetched.** ``course_title_snapshot``,
``version_number`` and ``content_hash`` are copied off the version at validate
and then left alone. A ``fetch_from`` would keep them live, which is exactly
wrong: renaming a course two years later would silently rewrite what somebody was
certified in. The snapshot is what makes this document mean anything years after
the content it describes has moved on.

Phase 4 adds Training Signoff, Training Certificate and Training Badge. There are
deliberately **no** Link fields pointing at them here — a Link whose options name
a DocType that does not exist fails ``bench migrate``, so those links arrive with
the doctypes they point at.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class TrainingCompletion(Document):
	def validate(self):
		self._snapshot_version()
		self._resolve_learner()
		self._stamp_completed_on()
		# Only on the way in. Re-saving or amending a completion issued years ago
		# must not fail because the course has since started requiring a sign-off:
		# the document records what was true when it was issued.
		if self.is_new():
			self._require_signoff()

	def before_submit(self):
		"""The moment of issuance, and therefore the moment the gate must hold.

		Checked here as well as on insert because a draft can be created while the
		requirement is unmet and submitted afterwards, and *submitted* is what makes
		this document an attestation somebody may be shown."""
		self._require_signoff()

	def before_cancel(self):
		"""Refuse the cancel *before* it happens rather than throwing out of
		``on_cancel``. By the time ``on_cancel`` runs the framework has already
		flipped docstatus and started firing links; failing there rolls the whole
		transaction back, which works but reports the problem from the wrong end of
		the operation."""
		self._require_revoked_reason()

	def on_cancel(self):
		# db_set rather than assignment: on_cancel runs after the document has been
		# written, so a plain field set would be discarded.
		self.db_set("status", "Revoked", update_modified=False)

	# ------------------------------------------------------------------ helpers

	def _require_signoff(self):
		"""Refuse a completion on a course that demands hands-on verification.

		**This gate did not exist until v1.333.0**, despite ``training/signoff.py``
		documenting it as living precisely here. The only enforcement was a private
		copy inside ``api.training.finish_attempt``, which covers exactly one path:
		a learner finishing in the player. A ``Training Completion`` created by hand
		in the Desk -- which is the ordinary way a manager records that somebody was
		trained before the module existed -- skipped it entirely. On this site the
		single course carrying ``require_supervisor_signoff`` is "Draining a Fountain
		Basin Safely", so the bypass was available on exactly the record whose whole
		purpose is proving somebody was watched doing it.

		Delegates to ``signoff.signoff_outstanding`` rather than repeating the query,
		because a second implementation of a compliance rule is how this happened.

		Imported inside the method, not at module scope: this is a doctype controller
		and ``signoff`` pulls in the notification stack behind it.
		"""
		from erpnext_enhancements.training import signoff

		if not self.course or not self.user:
			return

		if signoff.signoff_outstanding(self.course, self.user):
			frappe.throw(
				_("{0} requires a supervisor to confirm hands-on competence before a "
				  "completion can be issued. Record a submitted Training Signoff of "
				  "'Competent' for this learner on this course first.").format(
					self.course_title_snapshot or self.course
				),
				title=_("Supervisor sign-off outstanding"),
			)

	def _snapshot_version(self):
		"""Copy the version's identity in once. Guarded on 'not already set' so an
		amendment of an old completion keeps the values it was issued with rather
		than picking up whatever the version says today."""
		if not self.course_version:
			frappe.throw(_("A completion has to say which version of the course was passed."))

		version = frappe.db.get_value(
			"Training Course Version",
			self.course_version,
			["course", "course_title", "version_number", "content_hash"],
			as_dict=True,
		)
		if not version:
			frappe.throw(_("Course version {0} no longer exists.").format(self.course_version))

		if not self.course:
			self.course = version.course
		if not (self.course_title_snapshot or "").strip():
			self.course_title_snapshot = version.course_title
		if not cint(self.version_number):
			self.version_number = cint(version.version_number)
		if not (self.content_hash or "").strip():
			self.content_hash = version.content_hash

	def _resolve_learner(self):
		"""Fill in employee/customer from the user, but only when blank.

		Unlike ``Training Assignment``, which re-derives on every save because it
		describes a live obligation, this record describes a past event. Somebody
		who has since left the company still passed the course, and rewriting the
		link to None on a later save would erase that.
		"""
		if not self.user:
			frappe.throw(_("A completion needs a learner."))
		if not self.employee:
			self.employee = frappe.db.get_value("Employee", {"user_id": self.user}, "name")
		if not self.employee and not self.customer:
			self.customer = _customer_for_user(self.user)

	def _stamp_completed_on(self):
		if not self.completed_on:
			self.completed_on = now_datetime()

	def _require_revoked_reason(self):
		if not (self.revoked_reason or "").strip():
			frappe.throw(
				_("Say why this completion is being withdrawn. A certification that quietly disappears is "
				  "exactly what gets asked about later.")
			)


def _customer_for_user(user):
	"""The Customer a Website User belongs to, via Contact → Dynamic Link.

	Returns None rather than throwing, the same as ``Training Assignment``: an
	internal user with no Employee record yet is a normal state, and issuing a
	completion should not be blocked on it.
	"""
	if not user:
		return None
	contact = frappe.db.get_value("Contact", {"user": user}, "name")
	if not contact:
		return None
	return frappe.db.get_value(
		"Dynamic Link",
		{
			"parent": contact,
			"parenttype": "Contact",
			"link_doctype": "Customer",
		},
		"link_name",
	)

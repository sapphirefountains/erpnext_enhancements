# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Signoff — a named person attesting that a learner can actually do it.

Some things a quiz cannot prove. A course with ``require_supervisor_signoff`` set
stalls its assignment at ``Awaiting Sign-off`` until one of these is submitted,
and the value of that gate is entirely in *who* signed. So the two rules this
controller enforces are both about identity:

* **nobody signs off their own competence.** A self-signoff is not a weaker
  attestation, it is no attestation at all;
* **``supervisor_user`` is derived from the Employee record**, never accepted from
  the caller, so the submit-time check has something trustworthy to compare the
  session user against.

Submittable, because a submitted sign-off is an attestation somebody's name is on
and the framework then refuses to let it be edited. Withdrawing one is a cancel,
which leaves it visible.

**``on_submit`` is what makes any of this finish**, and it did not exist until
v1.386.0. This controller held ``validate`` and ``before_submit`` and nothing
else; ``record_signoff`` submitted the document, emailed the learner and
returned. So recording *Competent* moved nothing: the assignment stayed parked at
``Awaiting Sign-off`` indefinitely, no completion was minted, and the only way to
finish the course was for the learner — who had already been told they were done
and waiting on somebody else — to go back to ``/training`` and press finish a
second time. The docstring here used to say ``training/grading.py`` re-opened the
assignment on a cancel; ``grading.py`` contains no reference to sign-off at all
and never did. Both transitions now live in ``training/signoff.py``, which
already owns the ``Awaiting Sign-off`` write in the other direction.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

COMPETENT = "Competent"
#: "They did the whole job, with me standing next to them."
#:
#: The third outcome exists because two force supervisors to overstate. Watching a
#: Junior drain a basin correctly under supervision is neither "I would send them
#: alone" nor "come back to me", and a supervisor with only those options picks
#: Competent -- at which point the system reports somebody as ready to go out on
#: their own on the strength of a job they did with help. That is the failure this
#: prevents, and it is the one that puts a person at a site alone.
#:
#: Treated as NOT competent everywhere it matters: it does not complete the
#: course, does not mint a badge or a feed entry, does not satisfy a rung
#: requirement, and does not reset a recertification clock. It is recorded
#: progress, and the learner's record says so.
SUPERVISED_ONLY = "Supervised Only"
NEEDS_PRACTICE = "Needs More Practice"

#: Everything that is not a clean, unsupervised attestation. Grouped so no caller
#: has to remember to name both -- a fourth outcome added later joins this tuple
#: and every "not competent" branch picks it up.
NOT_YET_SOLO = (SUPERVISED_ONLY, NEEDS_PRACTICE)

# Roles allowed to submit somebody else's sign-off without being the named
# supervisor. A Training Manager records sign-offs made on paper or over the
# radio, which is a real workflow — the audit value is in the named supervisor
# on the document, not in which login pressed submit.
DELEGATE_ROLES = {"System Manager", "Training Manager", "HR Manager"}


class TrainingSignoff(Document):
	def validate(self):
		self._resolve_supervisor()
		self._resolve_learner()
		self._reject_self_signoff()
		self._require_notes_when_not_competent()
		self._stamp_signed_on()

	def before_submit(self):
		"""Checked here rather than in ``on_submit``: by the time ``on_submit`` runs
		the framework has already flipped docstatus and started firing links, and
		throwing there reports the problem from the wrong end of the operation.

		Scoped to submit on purpose. The learner runtime may well raise the *draft*
		— "I'm ready, please sign me off" is the learner's action — so refusing on
		validate would block a legitimate request. It is the submitted attestation
		that must not come from the learner."""
		self._reject_learner_submitting()
		self._require_authority()
		self._stamp_attested_on()

	def on_submit(self):
		"""Advance whatever this attestation was blocking.

		Delegated rather than written here, and deliberately after the framework
		has committed ``docstatus``: the attestation is the evidence and it must
		record even if the bookkeeping behind it fails, so
		``signoff.after_signoff_submitted`` is contractually incapable of raising.
		"""
		from erpnext_enhancements.training import signoff

		signoff.after_signoff_submitted(self)

	def on_cancel(self):
		"""Withdrawing an attestation re-opens what it unblocked."""
		from erpnext_enhancements.training import signoff

		signoff.after_signoff_cancelled(self)

	# ------------------------------------------------------------------ helpers

	def _resolve_supervisor(self):
		"""Always re-derived. A caller-supplied ``supervisor_user`` would let the
		one check that makes this document mean anything be pointed at somebody
		else's login."""
		if not self.supervisor:
			frappe.throw(_("A sign-off has to name the supervisor making it."))
		self.supervisor_user = frappe.db.get_value("Employee", self.supervisor, "user_id")

	def _resolve_learner(self):
		if not self.user:
			frappe.throw(_("A sign-off needs a learner."))
		if not self.employee:
			self.employee = frappe.db.get_value("Employee", {"user_id": self.user}, "name")

	def _reject_self_signoff(self):
		"""The named supervisor may not be the learner — checked on both links,
		because a person can be reached either way and only one of the two is
		guaranteed to be filled in."""
		if self.supervisor_user and self.supervisor_user == self.user:
			frappe.throw(_("A learner cannot sign off their own competence."))
		if self.employee and self.supervisor == self.employee:
			frappe.throw(_("A learner cannot sign off their own competence."))

	def _reject_learner_submitting(self):
		"""Catches what the link comparison cannot: the learner submitting a
		document that names their manager. Managers keep the delegate path because
		sign-offs really are relayed over the radio and typed up afterwards."""
		if frappe.session.user != self.user:
			return
		if DELEGATE_ROLES & set(frappe.get_roles(frappe.session.user)):
			return
		frappe.throw(_("A sign-off has to be submitted by the supervisor, not by the learner."))

	def _require_authority(self):
		"""The tier rule, enforced on **this** door.

		``record_signoff`` sets ``ignore_permissions = True`` and calls ``submit()``;
		the Desk form's own Submit button calls ``submit()`` directly and never goes
		near the endpoint. So a rule that lives only in ``signoff._assert_may_sign``
		is not a rule, it is a suggestion that one of the two doors happens to make
		— and this is the door a supervisor sitting in the Desk actually uses.

		The same call also **snapshots** the basis and both positions onto the
		document, because a Position link resolves to today and an attestation is
		about what was true when it was made. Same doctrine as the completion's
		course-title and content-hash snapshots, for the same reason.
		"""
		from erpnext_enhancements.training import authority

		basis = authority.snapshot_positions(self, frappe.session.user)
		if basis:
			return
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			# A data migration writing historical attestations has no session
			# supervisor to check. The other rules -- self-signoff, notes, the
			# derived supervisor_user -- still hold.
			return
		frappe.throw(
			_("{0} cannot record this sign-off. It needs the named supervisor, somebody senior "
			  "to {1} on the same ladder, or a Training Manager.").format(
				frappe.session.user, self.user
			),
			frappe.PermissionError,
		)

	def _require_notes_when_not_competent(self):
		"""A note for anything that is not a clean attestation.

		Keyed on ``NOT_YET_SOLO`` rather than on ``NEEDS_PRACTICE``, so the
		requirement covers `Supervised Only` too: "supervised only" with no note
		does not say what still needs watching, which is the one thing the next
		supervisor needs to know before deciding whether to stand there again.
		"""
		if self.outcome in NOT_YET_SOLO and not (self.competency_notes or "").strip():
			frappe.throw(
				_("Say what still needs work. \"{0}\" with no note leaves the learner nothing to "
				  "act on and the next supervisor nothing to check.").format(self.outcome)
			)

	def _stamp_signed_on(self):
		"""When the REQUEST was raised. Not the attestation -- see `_stamp_attested_on`."""
		if not self.signed_on:
			self.signed_on = now_datetime()

	def _stamp_attested_on(self):
		"""When the supervisor actually attested.

		Separate from `signed_on` because they are different events and the gap
		between them is sometimes days: the learner raises the request, the Senior
		Technician signs when they are next on the same site. An audit asking "was
		this person cleared to work alone on 1 March" needs the attestation date;
		answering with the request date reports somebody as competent from the moment
		they ASKED to be, which is the wrong direction to be wrong in.

		Only ever set when blank, so a historical import can carry an honest date.
		Rows submitted before v1.396.0 have none, and the roster must render them as
		not reconstructible rather than falling back to `signed_on`.
		"""
		if self.meta.has_field("attested_on") and not self.get("attested_on"):
			self.attested_on = now_datetime()

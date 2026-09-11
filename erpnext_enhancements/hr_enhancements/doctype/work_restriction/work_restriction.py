# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What somebody temporarily may not do, and until when.

**There is nowhere on this record to write why, and that is the design.** A
restriction is a *scheduling fact* — this person cannot lift, cannot drive, cannot
go into a vault, until the 14th — and the scheduler needs exactly that and nothing
more. The diagnosis behind it is between them and their doctor.

The pull in the other direction is real and worth naming so nobody re-adds it: a
free-text "reason" field is the obvious thing to want, it feels helpful, and it
would immediately fill up with medical information sitting in a doctype that
`Employee` can read. Once it is there, every future feature that joins on this
table inherits it. The `note` field exists for what a supervisor needs in order to
schedule around the restriction, and its description says so.

**Warn, never block.** Nothing here stops a visit being scheduled. Same doctrine
as the uncertified-dispatch advisory and for the same reason: a hard gate does not
stop the work happening, it moves the work off the books. The advisory says "he is
on no-lifting until the 14th" at the moment somebody assigns him, and a human
decides.

Open-ended is allowed — a restriction often has no end date when it starts — and
the weekly digest lists those so "until further notice" gets reviewed rather than
forgotten.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

ACTIVE = "Active"
ENDED = "Ended"
CANCELED = "Canceled"

#: Fieldname -> the sentence a dispatcher reads. Ordered by how often it changes
#: who can be sent.
RESTRICTIONS = (
	("no_confined_space", "no vault or pit entry"),
	("no_heights", "no ladders or heights"),
	("no_chemicals", "no chemical handling"),
	("no_water_entry", "not to enter water"),
	("no_driving", "not to drive"),
	("no_lifting", "no lifting"),
	("no_solo_work", "not to work alone"),
)


class WorkRestriction(Document):
	def validate(self):
		self._stamp_transition()
		self._resolve_user()
		self._require_a_restriction()
		self._reject_backwards_dates()
		self._stamp_recorder()

	def _resolve_user(self):
		if self.employee:
			self.user = frappe.db.get_value("Employee", self.employee, "user_id")

	def _require_a_restriction(self):
		"""A restriction that restricts nothing is a row that reads as a limitation
		and imposes none — it shows on the person's record, worries whoever reads
		it, and changes no dispatch decision."""
		if not any(self.get(field) for field, _label in RESTRICTIONS):
			frappe.throw(_("Tick at least one thing they cannot do."))

	def _reject_backwards_dates(self):
		if self.to_date and self.from_date and getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The end date is before the start date."))

	def _stamp_recorder(self):
		if not self.recorded_by:
			self.recorded_by = frappe.session.user

	def covers(self, on_date):
		"""Whether this restriction applies on *on_date*.

		An absent ``to_date`` is open-ended, not expired — the common case when a
		restriction is first written down, and reading it as "finished" would be the
		unsafe direction.
		"""
		if self.status != ACTIVE:
			return False
		when = getdate(on_date or today())
		if self.from_date and when < getdate(self.from_date):
			return False
		if self.to_date and when > getdate(self.to_date):
			return False
		return True

	def summary(self):
		"""The sentence a dispatcher reads, built from the ticks."""
		parts = [label for field, label in RESTRICTIONS if self.get(field)]
		if self.no_lifting and self.lifting_limit_lbs:
			parts = [
				_("no lifting over {0} lbs").format(self.lifting_limit_lbs) if p == "no lifting" else p
				for p in parts
			]
		return ", ".join(str(p) for p in parts)

	def _stamp_transition(self):
		"""Give the end of a restriction a DATE, not just a status.

		`status` is a fact about today. Marking a row Ended without recording when
		erased the fact that it had ever applied -- and `restriction_blocks` takes an
		`on_date`, so a question about 1 March answered against a row somebody has
		since tidied returned "no restrictions" for a day the person was on no-lifting.
		It read as correct only because nothing ever wrote Ended; the first tidy-up
		would have retroactively erased history.

		Only ever fills a blank, so a correction typed by a human stands.
		"""
		if not self.meta.has_field("ended_on"):
			return
		if self.status == "Ended" and not self.get("ended_on"):
			self.ended_on = self.to_date or today()
		if self.status == "Canceled" and not self.get("canceled_on"):
			self.canceled_on = today()

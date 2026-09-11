# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An injury, illness, near miss or damage — in the shape the law asks for.

Technicians work with water, chemicals, pumps and live electrical. This is the
record OSHA asks for first and almost nobody has, and it is deliberately shaped by
the regulation rather than by what would be tidy.

Four things are load-bearing.

**The record exists the moment it is submitted, whether or not anybody agrees with
it.** The `Employee` role can create one. A log a worker cannot open is a log that
gets a phone call instead, and a phone call is not a record — and a log a manager
can suppress at intake is not a log at all. Whether a case is *recordable* is a
separate question answered below, by a rule, not by whoever received it.

**Recordability is derived, never typed.** The rule is mechanical —
`29 CFR 1904.7` — and the one distinction that decides most cases is *first aid
only* versus *medical treatment beyond first aid*, which is exactly the one people
get wrong under pressure. A tick box labelled "recordable" invites a judgement
call at the worst possible moment; a computed field invites an argument with the
rule, which is the argument worth having.

**The reporting clock is shown at the moment of filing.** Utah gives **8 hours**
for a fatality and **24 hours** for an in-patient hospitalisation, an amputation
or the loss of an eye, counted from when the company *learns* of it — not from
when it happened. A deadline that appears in a report next week is a deadline
already missed, so it is computed on insert and put on the form.

**Privacy cases are built in from the start.** Six categories, listed by the rule
and nowhere widened, whose names must not appear on the posted log. Retrofitting
that is how a name ends up printed: the 300 log report reads `is_privacy_case` and
prints "Privacy Case", and the name lives on this record and on a separate list
only named people can open.

What this deliberately is **not**: a workflow. There is no approval, no
countersignature, no state machine beyond Open / Under investigation / Closed. The
value is that the record exists and is complete, and every gate between a
technician and filing one is a reason it does not get filed.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

INJURY = "Injury"
ILLNESS = "Illness"
NEAR_MISS = "Near Miss"
PROPERTY_DAMAGE = "Property Damage"

DEATH = "Death"
DAYS_AWAY = "Days away from work"
RESTRICTION = "Job transfer or restriction"
OTHER_RECORDABLE = "Other recordable case"
NOT_RECORDABLE = "Not recordable"

FIRST_AID = "First aid only"
BEYOND_FIRST_AID = "Medical treatment beyond first aid"
EMERGENCY_ROOM = "Emergency room"
HOSPITALISED = "Hospitalised overnight"

#: Treatment that makes a case recordable on its own. `First aid only` is
#: deliberately absent -- that is the whole distinction.
RECORDABLE_TREATMENT = (BEYOND_FIRST_AID, EMERGENCY_ROOM, HOSPITALISED)

#: Outcomes that are recordable by definition.
RECORDABLE_OUTCOMES = (DEATH, DAYS_AWAY, RESTRICTION, OTHER_RECORDABLE)

FATALITY_HOURS = 8
HOSPITALISATION_HOURS = 24

#: The rule caps both day counts at 180. Recording 400 days away is not more
#: honest, it is a form that will be rejected.
MAX_DAYS = 180


class SafetyIncident(Document):
	def before_insert(self):
		# Stamped once, here rather than in validate, so a later edit cannot move
		# either. The gap between `occurred_on` and `reported_on` is the first thing
		# an inspector looks at, and a field that can be quietly corrected is not
		# evidence of anything.
		self.reported_on = now_datetime()
		self.reported_by = frappe.session.user
		self.job_title_at_time = self._job_title()

	def validate(self):
		self._cap_days()
		self._derive_recordable()
		self._derive_reporting_clock()
		self._require_privacy_basis()
		self._stamp_done_actions()

	# ------------------------------------------------------------------ derived

	def _job_title(self):
		"""OSHA 300 column (C), as it was on the day.

		Read from the Position if there is one, else the Designation. A snapshot for
		the reason every snapshot in this app exists: a log that re-titles somebody
		after a promotion is not the record that was made.
		"""
		if not self.employee:
			return None
		try:
			if frappe.db.has_column("Employee", "custom_position"):
				position = frappe.db.get_value("Employee", self.employee, "custom_position")
				if position:
					return frappe.db.get_value("Position", position, "position_name") or position
		except Exception:
			pass
		return frappe.db.get_value("Employee", self.employee, "designation")

	def _cap_days(self):
		for field in ("days_away", "days_restricted"):
			if cint(self.get(field)) > MAX_DAYS:
				self.set(field, MAX_DAYS)

	def _derive_recordable(self):
		"""`29 CFR 1904.7`, mechanically.

		A case is recordable if it involves death, days away from work, restricted
		work or transfer, medical treatment **beyond first aid**, or loss of
		consciousness.

		A near miss and property damage are never recordable, and that is not a
		technicality worth hiding: the near miss is the most useful row in the whole
		log precisely because it cost nothing, and burying it under a recordability
		flag would discourage filing the ones that are free.
		"""
		if self.incident_type in (NEAR_MISS, PROPERTY_DAMAGE):
			self.is_recordable = 0
			if not self.outcome:
				self.outcome = NOT_RECORDABLE
			return

		recordable = bool(
			(self.outcome in RECORDABLE_OUTCOMES)
			or (self.treatment in RECORDABLE_TREATMENT)
			or cint(self.lost_consciousness)
		)
		self.is_recordable = 1 if recordable else 0
		if recordable and not self.outcome:
			# Recordable on treatment or consciousness alone, with no more serious
			# outcome ticked, is OSHA column (J).
			self.outcome = OTHER_RECORDABLE

	def _derive_reporting_clock(self):
		"""Utah's 8 and 24 hours, from when we learned of it.

		Counted from ``reported_on`` and not from ``occurred_on``, which is what the
		rule says and is also the only defensible reading — the company cannot start
		a clock on something it did not know about.
		"""
		basis = get_datetime(self.reported_on or now_datetime())

		if self.outcome == DEATH:
			self.reportable_to_uosh = _("Fatality — 8 hours")
			self.uosh_due_by = add_to_date(basis, hours=FATALITY_HOURS)
		elif self.treatment == HOSPITALISED or self._is_amputation_or_eye():
			self.reportable_to_uosh = _("Hospitalisation, amputation or eye loss — 24 hours")
			self.uosh_due_by = add_to_date(basis, hours=HOSPITALISATION_HOURS)
		else:
			self.reportable_to_uosh = _("No")
			self.uosh_due_by = None
			return

		# The scene stays as it is until UOSH releases it. Set rather than suggested,
		# because the moment somebody is deciding whether to move the pump is the
		# moment nobody is reading a policy document.
		if not self.hold_released_on:
			self.evidence_hold = 1

	def _is_amputation_or_eye(self):
		"""Read from the words, because there is no tick box for it and adding one
		would ask a technician to make a legal classification at the hatch.

		Deliberately generous — it over-reports rather than under-reports, and the
		cost of the two is not symmetric: a needless call to UOSH costs a phone call,
		a missed one is a citation.
		"""
		text = " ".join(
			str(self.get(field) or "")
			for field in ("injury_description", "what_happened", "body_part")
		).casefold()
		return any(word in text for word in ("amputat", "sever", "degloved", "eye loss", "lost an eye"))

	def _require_privacy_basis(self):
		"""Which of the six, because "why is this one anonymous" is the first
		question anybody asks and the answer must be on the record."""
		if cint(self.is_privacy_case) and not self.privacy_basis:
			frappe.throw(_("Say which privacy category applies — the rule lists six."))

	def _stamp_done_actions(self):
		from frappe.utils import today

		for row in self.actions or []:
			if cint(row.done) and not row.done_on:
				row.done_on = today()
			if not cint(row.done):
				row.done_on = None

	# ------------------------------------------------------------------- display

	def log_name(self):
		"""The name as it appears on the **posted** 300 log.

		The one place the privacy rule actually bites. Every report that prints a
		name goes through here rather than reading `employee_name` directly, so
		there is one function to get right instead of one per report.
		"""
		if cint(self.is_privacy_case):
			return _("Privacy Case")
		return self.employee_name or ""

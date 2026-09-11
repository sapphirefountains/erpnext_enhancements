# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The permit that gets completed at the hatch, before anybody goes down.

**A permit signed at the office the morning before is the failure mode to design
against.** It is the normal way this goes wrong: the form gets filled in because
the form has to exist, and the readings on it are last week's. So the atmosphere
limits are checked by the machine rather than by the person filling it in, the
permit expires with the shift, and closing it out is a separate act from opening
it.

**The attendant is the field that gets skipped**, and it is the one that matters
most. Most confined-space fatalities are would-be rescuers: somebody goes in
after a colleague who has gone quiet, and the atmosphere takes them too. So the
attendant is mandatory, must be a different person from the entrant, and the
permit says in words that they do not enter.

**Nothing here blocks the work, and that is deliberate** — but for a different
reason than everywhere else in this app. The gas readings DO refuse: an atmosphere
outside limits is not an advisory, it is the one number a permit exists to check,
and a permit that opens anyway is a permit that has stopped meaning anything.
Everything else — ventilation, PPE, the rescue plan — is a tick a human makes.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, flt, now_datetime

DRAFT = "Draft"
OPEN = "Open"
CLOSED = "Closed"
CANCELED = "Canceled"

#: The numbers. Oxygen has a ceiling as well as a floor — an enriched atmosphere
#: is a fire risk, and people forget that half of it.
OXYGEN_MIN = 19.5
OXYGEN_MAX = 23.5
LEL_MAX = 10.0
H2S_MAX = 10.0
CO_MAX = 25.0

#: A permit is for this entry, not for the day.
PERMIT_HOURS = 8


class ConfinedSpaceEntryPermit(Document):
	def validate(self):
		self._reject_self_attending()
		self._derive_atmosphere()
		self._guard_status()

	def _reject_self_attending(self):
		"""The entrant cannot be their own attendant.

		Obvious written down, and the single most likely data-entry shortcut on a
		phone at a hatch: one person, in a hurry, filling their own name into every
		Link because the form wants three.
		"""
		if self.attendant and self.entrant and self.attendant == self.entrant:
			frappe.throw(
				_(
					"The attendant stays outside and cannot be the person going in. Nobody "
					"enters without somebody else above ground."
				)
			)

	def _derive_atmosphere(self):
		"""Read from the numbers, never from a tick.

		A check box labelled "atmosphere OK" is a box somebody ticks while holding a
		meter they have not looked at. The limits are fixed and the arithmetic is
		trivial, so the form does it.
		"""
		oxygen = flt(self.oxygen_pct)
		self.atmosphere_ok = 1 if (
			OXYGEN_MIN <= oxygen <= OXYGEN_MAX
			and flt(self.lel_pct) < LEL_MAX
			and flt(self.h2s_ppm) < H2S_MAX
			and flt(self.co_ppm) < CO_MAX
		) else 0

	def _guard_status(self):
		"""Status moves only through `hr_enhancements/permits.py`."""
		if self.flags.get("permit_transition") or self.is_new():
			return
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status:
			frappe.throw(
				_("Open and close a permit with the buttons on this form."), frappe.PermissionError
			)

	# ------------------------------------------------------------------ helpers

	def atmosphere_problems(self):
		"""Which readings are out, in words. Empty when everything is in range.

		Returned rather than thrown so the caller decides — the form shows them as
		you type, and only `open_permit` refuses.
		"""
		out = []
		oxygen = flt(self.oxygen_pct)
		if oxygen < OXYGEN_MIN:
			out.append(_("oxygen {0}% is below {1}%").format(oxygen, OXYGEN_MIN))
		elif oxygen > OXYGEN_MAX:
			out.append(
				_("oxygen {0}% is above {1}% — an enriched atmosphere is a fire risk").format(
					oxygen, OXYGEN_MAX
				)
			)
		if flt(self.lel_pct) >= LEL_MAX:
			out.append(_("LEL {0}% is at or above {1}%").format(flt(self.lel_pct), LEL_MAX))
		if flt(self.h2s_ppm) >= H2S_MAX:
			out.append(_("H₂S {0} ppm is at or above {1}").format(flt(self.h2s_ppm), H2S_MAX))
		if flt(self.co_ppm) >= CO_MAX:
			out.append(_("CO {0} ppm is at or above {1}").format(flt(self.co_ppm), CO_MAX))
		return out

	def missing_controls(self):
		"""Which of the human ticks are not ticked."""
		wanted = (
			("isolation_confirmed", _("energy isolated and locked")),
			("ventilation", _("ventilation running")),
			("rescue_plan_confirmed", _("a rescue plan in place")),
		)
		return [label for field, label in wanted if not cint(self.get(field))]

	def expiry_from(self, when=None):
		return add_to_date(when or now_datetime(), hours=PERMIT_HOURS)

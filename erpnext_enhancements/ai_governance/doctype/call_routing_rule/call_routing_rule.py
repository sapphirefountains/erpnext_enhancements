# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Call Routing Rule — one "when a call looks like this, ring these people" clause.

Rules are evaluated in ``priority`` order and the **first match wins**; the matching
itself is in :mod:`erpnext_enhancements.ai_governance.call_routing_match`, which is
dependency-free so the Triton gateway can run the identical code without an ERPNext round
trip on the Twilio webhook path.

``validate`` here draws a deliberate line between two kinds of wrong:

* **Throw** when the rule is *provably inert* — a custom-days schedule naming no parseable
  day, or a number-matching rule with no numbers. Those cannot ever fire, and a rule that
  silently never matches is far harder to notice than a save that refuses.
* **Warn** when the rule can fire but may not reach somebody — most often an Employee with
  no Cell Number. On 2026-09-11 only 2 of 20 Employee records had one, so throwing here
  would make the form unusable for its main job; the warning goes to the person editing,
  to ``compile_rules``' payload warnings, and to the routing preview.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from erpnext_enhancements.ai_governance import call_routing_match as match

#: Which DocType a target's Dynamic Link points at. Types absent from this map resolve
#: themselves at call time and carry no value.
TARGET_DOCTYPE_FOR = {
	"Employee": "Employee",
	"Softphone User": "User",
}


class CallRoutingRule(Document):
	def validate(self) -> None:
		self._stamp_target_doctypes()
		self._check_schedule()
		self._check_caller_numbers()
		self.ring_seconds = max(0, min(match.MAX_RING_SECONDS, cint(self.get("ring_seconds"))))
		self._warn_about_unreachable_targets()

	def on_update(self) -> None:
		"""Tell the gateway a rule changed. Fires on insert as well as on edit."""
		self._notify_gateway()

	def on_trash(self) -> None:
		"""Deleting a rule changes who rings just as much as editing one does."""
		self._notify_gateway()

	def _notify_gateway(self) -> None:
		"""Function-level import: see the note in ``CallRoutingSettings.on_update``."""
		from erpnext_enhancements.ai_governance.call_routing import notify_gateway

		notify_gateway()

	def _stamp_target_doctypes(self) -> None:
		"""Derive ``target_doctype`` and clear the value where it means nothing.

		Clearing matters: switching a row from Employee to Voicemail would otherwise leave
		the old Employee link sitting in ``target_value``, invisible behind a
		``depends_on``, and a later switch back would silently restore a target the person
		thought they had removed.
		"""
		for row in self.get("targets") or []:
			doctype = TARGET_DOCTYPE_FOR.get((row.target_type or "").strip(), "")
			row.target_doctype = doctype
			if not doctype:
				row.target_value = None

	def _check_schedule(self) -> None:
		if (self.get("schedule") or "").strip() != match.SCHEDULE_CUSTOM:
			return
		if not match.parse_weekdays(self.get("custom_days")):
			frappe.throw(
				_(
					"Which Days does not name any weekday, so this rule could never match. "
					"Write day names, for example \"Mon, Wed, Fri\"."
				)
			)

	def _check_caller_numbers(self) -> None:
		if (self.get("caller_scope") or "").strip() != match.CALLER_NUMBERS:
			return
		numbers = [n for n in (self.get("caller_numbers") or "").replace(",", "\n").splitlines() if n.strip()]
		if not numbers:
			frappe.throw(
				_("Who Is Calling is set to Matching numbers, so Calling From needs at least one number.")
			)
		for raw in numbers:
			if not match.digits(raw):
				frappe.throw(_("{0} is not a phone number.").format(raw))

	def _warn_about_unreachable_targets(self) -> None:
		"""Say so, without refusing the save — see the module docstring."""
		problems: list[str] = []
		has_voicemail = False
		reachable = 0

		for row in self.get("targets") or []:
			kind = (row.target_type or "").strip()
			if kind == "Voicemail":
				has_voicemail = True
				reachable += 1
			elif kind == "Account Manager":
				reachable += 1
			elif kind == "Employee":
				if not row.target_value:
					problems.append(_("An Employee target has nobody selected."))
					continue
				name, cell = (
					frappe.db.get_value("Employee", row.target_value, ["employee_name", "cell_number"])
					or (None, None)
				)
				if match.to_e164(cell):
					reachable += 1
				else:
					problems.append(
						_("{0} has no usable Cell Number on their Employee record, so they will not ring.")
						.format(name or row.target_value)
					)
			elif kind == "Softphone User":
				if not row.target_value:
					problems.append(_("A Softphone User target has nobody selected."))
				elif not frappe.db.get_value("User", row.target_value, "enabled"):
					problems.append(_("{0} is disabled, so their softphone will not ring.").format(row.target_value))
				else:
					reachable += 1

		if has_voicemail and reachable > 1:
			problems.append(
				_(
					"This rule sends callers to voicemail, so the other targets are ignored — "
					"voicemail does not ring phones as well."
				)
			)

		if not reachable and not cint(self.get("also_ring_softphones")):
			problems.append(
				_(
					"This rule reaches nobody: it has no usable target and Also Ring Softphones is off. "
					"Calls matching it will fall through to the next rule."
				)
			)

		if problems:
			frappe.msgprint(
				"<br>".join(problems),
				title=_("Check this rule's targets"),
				indicator="orange",
			)

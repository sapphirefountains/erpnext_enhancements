# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``Chat Ops Alert`` — one row per *problem*, not one per occurrence.

Phase 6 §4.H. The decisions live in :mod:`chat.governance.alert_rules`; this controller is
the backstop for the two edits a human can make from the desk or the console, and the guard
against the one deletion that matters.

**Not submittable**, like every other chat DocType, for the reason
``Chat Export Request`` states: ``docstatus`` supplies a workflow nobody asked for, and an
operational record that can be *cancelled* is one with an undo button.

**`state` moves through :func:`alert_rules.transition` and nowhere else.** The alerting path
writes with ``frappe.db.set_value``, which skips ``validate()`` entirely, so the check below
is a backstop rather than the enforcement point — the same caveat the relay job and the
export request both carry, restated here because somebody reading one will read the others.

The transition that matters is the one that is **absent**: ``Resolved -> Open``. A recurrence
after resolution opens a new row. Reopening would overwrite ``resolved_at``, and losing the
record that the problem was fixed once destroys the only evidence that it is flapping rather
than continuing — which is the difference between "the fix did not work" and "it never
stopped", and those want different people looking at them.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_enhancements.chat.governance import alert_rules

#: Fields describing *what happened*. Frozen after insert: the alert row is what an operator
#: reads to decide whether to get out of bed, and one that can be edited into saying
#: something else is not evidence of anything.
_FROZEN_FIELDS = (
	"dedup_key",
	"subsystem",
	"kind",
	"scope",
	"severity",
	"first_seen_at",
)


class ChatOpsAlert(Document):
	def validate(self) -> None:
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before is None:
			# A reload path with no prior copy — `bench migrate`'s own resaves land here.
			return
		self._refuse_identity_edits(before)
		self._refuse_illegal_transition(before)

	def _refuse_identity_edits(self, before: Document) -> None:
		changed = [f for f in _FROZEN_FIELDS if (self.get(f) or "") != (before.get(f) or "")]
		if changed:
			frappe.throw(
				frappe._(
					"An alert's identity is fixed once it is raised ({0}). Editing it here "
					"would silently merge or split incidents that have already been counted."
				).format(", ".join(changed)),
				frappe.ValidationError,
			)

	def _refuse_illegal_transition(self, before: Document) -> None:
		old = (before.get("state") or alert_rules.STATE_OPEN).strip()
		new = (self.get("state") or alert_rules.STATE_OPEN).strip()
		if old == new:
			return
		event = _EVENT_FOR_STATE.get(new)
		if event is None or alert_rules.transition(old, event) != new:
			frappe.throw(
				frappe._(
					"An alert cannot move from {0} to {1}. Resolved is terminal — a "
					"recurrence opens a new alert rather than reopening this one."
				).format(old, new),
				frappe.ValidationError,
			)


#: The event that produces each state, so the controller checks the same table the alerting
#: path does instead of a second copy that can drift from it.
_EVENT_FOR_STATE = {
	alert_rules.STATE_ACKNOWLEDGED: alert_rules.EVENT_ACKNOWLEDGE,
	alert_rules.STATE_RESOLVED: alert_rules.EVENT_RESOLVE,
}

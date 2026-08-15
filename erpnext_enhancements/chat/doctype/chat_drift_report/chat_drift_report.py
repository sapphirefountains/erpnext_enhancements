# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``Chat Drift Report`` — one row per *finding*, not per observation.

Phase 6 §4.I. The judgement lives in :mod:`chat.governance.drift_rules`; this controller is
the backstop for edits made from the desk or the console, and it consults **the same pure
transition table the scanner does** rather than owning a second copy — the improvement
``Chat Ops Alert``'s controller already makes over its two predecessors.

Not submittable, like every chat DocType: ``docstatus`` supplies a workflow nobody asked for.

``state`` is written by the scan with ``frappe.db.set_value``, which skips ``validate()``
entirely, so the check below is a backstop and not the enforcement point — the caveat the
relay job, the export request and the ops alert all carry, restated because somebody reading
one will read the others.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_enhancements.chat.governance import drift_rules

#: What this finding *is*. Frozen after insert: a row whose class or scope can be edited is
#: one that silently merges or splits findings that have already been counted.
_FROZEN_FIELDS = (
	"report_key",
	"drift_class",
	"scope",
	"reference_doctype",
	"reference_name",
	"first_seen_at",
)

_EVENT_FOR_STATE = {
	drift_rules.STATE_ACCEPTED: drift_rules.EVENT_ACCEPT,
	drift_rules.STATE_CLEARED: drift_rules.EVENT_CLEAR,
}


class ChatDriftReport(Document):
	def validate(self) -> None:
		if self.is_new():
			self._refuse_unknown_class()
			return
		before = self.get_doc_before_save()
		if before is None:
			# A reload path with no prior copy — `bench migrate`'s own resaves land here.
			return
		self._refuse_identity_edits(before)
		self._refuse_illegal_transition(before)

	def _refuse_unknown_class(self) -> None:
		"""A class with no ``EVIDENCE`` entry cannot be written at all.

		The property that keeps the module honest in a year: the entry is where somebody has
		to write down what *positive fact* the class fires on, and a class without one is an
		absence test — which on this app's shipped-dormant defaults matches everything.
		"""
		drift_class = (self.get("drift_class") or "").strip()
		if drift_class not in drift_rules.CLASSES or drift_rules.missing_evidence(drift_class):
			frappe.throw(
				frappe._(
					"{0} is not a drift class with a stated evidence rule. Add it to "
					"drift_rules.CLASSES and write its EVIDENCE entry in the same commit — a "
					"class without one fires on an empty column, which on this app's shipped "
					"defaults is every message in every room."
				).format(drift_class or "(blank)"),
				frappe.ValidationError,
			)

	def _refuse_identity_edits(self, before: Document) -> None:
		changed = [f for f in _FROZEN_FIELDS if (self.get(f) or "") != (before.get(f) or "")]
		if changed:
			frappe.throw(
				frappe._("A drift finding's identity is fixed once it is recorded ({0}).").format(
					", ".join(changed)
				),
				frappe.ValidationError,
			)

	def _refuse_illegal_transition(self, before: Document) -> None:
		old = (before.get("state") or drift_rules.STATE_OPEN).strip()
		new = (self.get("state") or drift_rules.STATE_OPEN).strip()
		if old == new:
			return
		event = _EVENT_FOR_STATE.get(new)
		if event is None or drift_rules.transition(old, event) != new:
			frappe.throw(
				frappe._(
					"A drift finding cannot move from {0} to {1}. Cleared is terminal — a "
					"recurrence opens a new finding rather than reopening this one."
				).format(old, new),
				frappe.ValidationError,
			)

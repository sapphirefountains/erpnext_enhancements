# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Replacing ERPNext's one-line ``QualityAction.validate`` — WI-075 sub-phase E.

Core's entire controller, verified against ``erpnext origin/version-16``:

    def validate(self):
        self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"

This app cannot live with either half of that.

**An empty resolutions table is not "done".** ``any([])`` is ``False``, so an action with no
resolution rows saves as ``Completed`` — and an action with no resolution rows is exactly what a
punch-list item is. Every punch item would be born closed, which is the opposite of the two-step
closure the whole programme is built around.

**And the literal stops being valid.** Once the Property Setter replaces core's
``Open / Completed`` with the five-state lifecycle, that line writes a value the field does not
offer, ``_validate_selects`` raises, and **every save of the doctype fails** — not just the ones
this app makes. So the Property Setter and this override are one indivisible change and must
never be split across releases.

Why a subclass rather than a ``doc_events`` handler
----------------------------------------------------

``Document.hook`` composes the controller method first and then each ``doc_events`` handler, so a
handler *would* see core's finished work and could overwrite it. It would also let core's bad
value exist for the length of one function call, and — more to the point — it would leave a
second place where status is decided. Replacing the class means there is exactly one.

The decision itself is in :mod:`erpnext_enhancements.quality.lifecycle`, which imports no
``frappe``, so the state machine is asserted on every push rather than on whenever somebody next
runs a bench.
"""

from erpnext.quality_management.doctype.quality_action.quality_action import (
	QualityAction as _CoreQualityAction,
)

from erpnext_enhancements.quality import lifecycle


class QualityAction(_CoreQualityAction):
	def validate(self):
		# Deliberately does NOT call super().validate() -- that one line is the thing being
		# replaced. Core has no other validate behaviour to preserve.
		self.status = lifecycle.derive_action_status(self.status, self.resolutions)

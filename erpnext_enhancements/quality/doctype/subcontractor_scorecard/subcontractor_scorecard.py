# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A subcontractor's scorecard for one period — WI-075 sub-phase N.

The controller derives everything a reader trusts — the state, the score, the headline — from the
measure rows, so none of it can be typed and none of it can drift from the evidence beneath it.
The only thing a person may write is the manual adjustment, and that is signed.

**The adjustment cannot be silent.** A non-zero adjustment with no reason is refused, and the
approver and time are stamped from the server session and the server clock rather than accepted
from the browser. The plan puts the reasoning plainly: *the first time a score is wrong and there
is no way to say so on the record, people stop using the record.* So the way is provided — and it
leaves a name.

**A score that does not exist is never conjured.** When nothing is judgeable the state is
``Not Measurable``, ``score_percent`` stays 0 **and is documented as meaningless**, and
``score_display`` says so in words. That is the state every scorecard on this site will be in the
day this deploys: every quality doctype holds zero rows, no subcontractor master agreement exists,
and rework hours and certificates of insurance are captured nowhere at all. A page that says *we
cannot yet measure this subcontractor* is honest. A page that says they are flawless is not, and
it is the one that gets carried into a negotiation.

The numeric score fields exist for charting and read 0 in that state, which is why
``score_display`` is the field on the list view and in the print format.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from erpnext_enhancements.quality import scorecard


class SubcontractorScorecard(Document):
	def validate(self):
		self._check_adjustment()
		self._refuse_duplicate_period()
		self.recompute()

	def recompute(self):
		"""Derive state, score and headline from the measure rows. Never reads anything else."""
		results = [
			{
				"measure_key": row.get("measure_key"),
				"value": _value_of(row),
				"coverage": row.get("coverage"),
			}
			for row in (self.get("measures") or [])
		]

		percent, met, judgeable, total = scorecard.score(results)

		self.state = scorecard.scorecard_state(results)
		self.measures_met = met
		self.measures_judgeable = judgeable
		self.measures_total = total
		# Both numeric fields read 0 when there is no score. That is a Frappe Float with no null,
		# not a claim -- `state` and `score_display` carry the truth, and the field descriptions
		# say so. Charting a 0 without reading `state` is the misuse this shape invites, which is
		# why `score_display` is what the list view and the print format show.
		self.score_percent = percent or 0
		self.final_score_percent = scorecard.final_score(percent, self.manual_adjustment) or 0
		self.score_display = scorecard.headline(results, percent, self.manual_adjustment)

	def _check_adjustment(self):
		errors = scorecard.adjustment_errors(self.manual_adjustment, self.adjustment_reason)
		if errors:
			frappe.throw("<br>".join(errors), title=_("Adjustment cannot be saved"))

		if self.has_value_changed("manual_adjustment") and self.manual_adjustment:
			# Server session and server clock. The old `complete_step` client path let the
			# browser propose a timestamp and the audit found retroactive box-ticking.
			self.adjusted_by = frappe.session.user
			self.adjusted_on = now_datetime()
		elif not self.manual_adjustment:
			self.adjusted_by = None
			self.adjusted_on = None

	def _refuse_duplicate_period(self):
		"""One scorecard per subcontractor per period.

		Frappe cannot express a composite unique index in a DocType JSON, so the rule lives here.
		Two scorecards for one supplier and one month is two answers to one question, and every
		report reading them would silently double-count.
		"""
		if not (self.supplier and self.period_start):
			return

		clash = frappe.db.get_value(
			"Subcontractor Scorecard",
			{
				"supplier": self.supplier,
				"period_start": self.period_start,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_("{0} already holds a scorecard for {1} covering this period.").format(
					frappe.bold(clash), self.supplier
				),
				title=_("Already scored"),
			)


def _value_of(row):
	"""The value a measure is judged on -- ``value_raw``, never the display string.

	``value_display`` carries units and punctuation for reading ("92.5%", "12.4 days", an em dash
	for no sample); ``value_raw`` carries the bare number or the state word. Judging the display
	string would silently fail to parse "92.5%" and report an unjudgeable measure on data that was
	perfectly good.

	The pair is safe because one builder writes both from a single source value and both are
	read-only. What sub-phase K refused was two *independently editable* fields for one fact.
	"""
	raw = row.get("value_raw")
	if raw in (None, ""):
		return None
	return raw

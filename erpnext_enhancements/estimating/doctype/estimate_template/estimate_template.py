# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Estimate Template: a job kind's phases and a checklist of candidate lines (concept card 10).

Applying a template to an Estimate shows the checklist with the ticked lines pre-selected; the
estimator says yes or no to each, "so you make a conscious decision to include or not" (James). The
copy happens in the form (``estimate.js``); nothing here is priced. Three templates are seeded by
``patches/seed_estimate_templates.py``, insert-only, and are edited in the Desk from then on.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html

from erpnext_enhancements.estimating import estimate_math as em
from erpnext_enhancements.estimating import rules


class EstimateTemplate(Document):
	def validate(self):
		numbers = set()
		for phase in self.phases:
			phase.phase_number = em.phase_number(phase.phase_number)
			if not phase.phase_number:
				frappe.throw(_("Phase row {0} needs a phase number, such as 300.").format(phase.idx))
			if phase.phase_number in numbers:
				frappe.throw(_("Phase {0} is listed twice.").format(phase.phase_number))
			numbers.add(phase.phase_number)
		for line in self.lines:
			line.phase_number = em.phase_number(line.phase_number)
			line.cost_code = rules.normalize_code(line.cost_code)
			problem = rules.code_problem(line.cost_code)
			if problem:
				frappe.throw(
					_("Line {0} ({1}): {2}").format(line.idx, escape_html(line.description), problem)
				)
			if numbers and line.phase_number and line.phase_number not in numbers:
				frappe.throw(
					_("Line {0} ({1}) is in phase {2}, which this template does not list.").format(
						line.idx, escape_html(line.description), line.phase_number
					)
				)
			if not numbers and line.phase_number:
				frappe.throw(
					_(
						"Line {0} ({1}) has a phase, but this template has no phases. Use 0 or add the phase."
					).format(line.idx, escape_html(line.description))
				)

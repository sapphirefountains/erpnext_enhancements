# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Estimate: priced lines grouped by phase, built from a job-kind template, issuing a Quotation.

Milestone 1 of the estimating build (TASK-2026-02056; concept accepted 2026-11-04). Every save
prices the whole estimate again through :mod:`erpnext_enhancements.estimating.estimate_math`, so the
stored numbers are always the server's; the form's lines editor runs the JavaScript twin only to
show totals while you type.

Things this is careful about:

* **Rates are copied from the rate card when the estimate is made**, not read live. A rate card
  edited next month must never reprice a quote that already went out. The copy happens only while
  every rate on the estimate is still zero, so rates typed on a new estimate before its first save
  are kept.
* **Overrides are strings because "blank" and "0" mean different things** (D15: line -> phase ->
  estimate). A bad one names its row instead of being read as zero.
* **A phase row is added for any phase a line uses**, and for the phase it nests under, so the
  quotation and the totals never meet a line with no phase row.
* **A superseded revision is read-only.** The newer revision is the one to change; the old one is
  the record of what was quoted.
* **Cost and margin are at permlevel 1**, readable by Estimator, Finance Team, Executive Team and
  System Manager only (the 2026-09-21 rule). In milestone 1 those are also the only roles that can
  open an Estimate at all; the permlevel is what keeps cost hidden when sales and project roles are
  given read access later.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html

from erpnext_enhancements.estimating import estimate_math as em
from erpnext_enhancements.estimating import rules

RATE_FIELDS = (
	"labor_rate",
	"tax_pct",
	"markup_material",
	"markup_equipment",
	"markup_labor",
	"markup_subcontract",
	"markup_expense",
	"contingency_pct",
	"insurance_pct",
	"fee_pct",
	"commission_pct",
)

LINE_OVERRIDES = (
	"markup_override",
	"contingency_override",
	"insurance_override",
	"fee_override",
	"commission_override",
)
PHASE_OVERRIDES = ("contingency_override", "insurance_override", "fee_override", "commission_override")

LINE_RESULTS = (
	"base_cost",
	"tax_amount",
	"cost_amount",
	"markup_pct",
	"markup_amount",
	"cost_of_work",
	"contingency_pct",
	"contingency_amount",
	"insurance_pct",
	"insurance_amount",
	"fee_pct",
	"fee_amount",
	"commission_pct",
	"commission_amount",
	"amount",
	"unit_price",
	"margin_amount",
	"margin_pct",
)

LINE_INPUTS = (
	"phase_number",
	"description",
	"cost_type",
	"cost_code",
	"basis",
	"by_others",
	"qty",
	"uom",
	"unit_cost",
	"sheet_price",
) + LINE_OVERRIDES


def default_rate_card():
	return frappe.db.get_value("Estimate Rate Card", {"is_default": 1, "disabled": 0}, "name")


class Estimate(Document):
	def before_insert(self):
		if not self.rate_card:
			self.rate_card = default_rate_card()
		if self.rate_card and not any(self.get(f) for f in RATE_FIELDS):
			self.copy_rates_from(self.rate_card)

	def copy_rates_from(self, rate_card):
		if not frappe.db.exists("Estimate Rate Card", rate_card):
			frappe.throw(_("Rate card {0} was not found.").format(escape_html(rate_card)))
		card = frappe.get_cached_doc("Estimate Rate Card", rate_card)
		for field in RATE_FIELDS:
			self.set(field, card.get(field) or 0)

	def validate(self):
		self.refuse_superseded_edit()
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
				"Global Defaults", "default_company"
			)
		if not self.prepared_by:
			self.prepared_by = frappe.session.user
		self.clean_lines()
		self.add_missing_phases()
		self.price()

	def refuse_superseded_edit(self):
		if self.is_new():
			return
		if (
			frappe.db.get_value("Estimate", self.name, "status") == "Superseded"
			and not self.flags.from_revision
		):
			frappe.throw(
				_(
					"This revision is superseded and stays as it was quoted. Change the newer revision instead."
				),
				title=_("Superseded"),
			)

	def clean_lines(self):
		for line in self.lines:
			line.phase_number = em.phase_number(line.phase_number)
			line.description = (line.description or "").strip()
			line.cost_code = rules.normalize_code(line.cost_code)
			problem = rules.code_problem(line.cost_code)
			if problem:
				frappe.throw(
					_("Line {0} ({1}): {2}").format(line.idx, escape_html(line.description), problem)
				)
			if not line.cost_type:
				line.cost_type = rules.default_cost_type(line.cost_code) or "Material"
			if line.cost_type not in em.COST_TYPES:
				frappe.throw(
					_("Line {0}: cost type {1} is not one of {2}.").format(
						line.idx, escape_html(line.cost_type), ", ".join(em.COST_TYPES)
					)
				)
			if (line.qty or 0) < 0 or (line.unit_cost or 0) < 0 or (line.sheet_price or 0) < 0:
				frappe.throw(
					_(
						"Line {0} ({1}): quantity, cost and price cannot be negative. A credit belongs on a change order."
					).format(line.idx, escape_html(line.description))
				)
			for field in LINE_OVERRIDES:
				line.set(field, self.clean_override(line.get(field), _("Line {0}").format(line.idx), field))
		for phase in self.phases:
			phase.phase_number = em.phase_number(phase.phase_number)
			for field in PHASE_OVERRIDES:
				phase.set(
					field,
					self.clean_override(phase.get(field), _("Phase {0}").format(phase.phase_number), field),
				)
		seen = set()
		for phase in self.phases:
			if phase.phase_number in seen:
				frappe.throw(_("Phase {0} is listed twice.").format(phase.phase_number))
			seen.add(phase.phase_number)

	@staticmethod
	def clean_override(raw, where, field):
		try:
			value = em.parse_rate(raw)
		except (TypeError, ValueError):
			label = field.replace("_override", "").replace("_", " ")
			frappe.throw(
				_("{0}: the {1} override must be a number or blank, not {2}.").format(
					where, label, escape_html(raw)
				)
			)
		if value is None:
			return ""
		if value < 0 or value > 1000:
			frappe.throw(_("{0}: {1}% is not a usable rate.").format(where, value))
		return f"{value:g}"

	def add_missing_phases(self):
		missing = rules.missing_phases(
			[p.phase_number for p in self.phases], [l.phase_number for l in self.lines]
		)
		for number in missing:
			self.append("phases", {"phase_number": number, "label": rules.phase_label(number)})
		ordered = sorted(self.phases, key=lambda p: p.phase_number or 0)
		for idx, phase in enumerate(ordered, start=1):
			phase.idx = idx
			if not phase.label:
				phase.label = rules.phase_label(phase.phase_number)
		self.phases = ordered

	def price(self):
		estimate = {f: self.get(f) for f in RATE_FIELDS}
		phases = [
			{"phase_number": p.phase_number, **{f: p.get(f) for f in PHASE_OVERRIDES}} for p in self.phases
		]
		lines = [{f: l.get(f) for f in LINE_INPUTS} for l in self.lines]
		priced = em.price_estimate(estimate, phases, lines)
		for line, result in zip(self.lines, priced["lines"]):
			for field in LINE_RESULTS:
				line.set(field, result[field])
			line.needs_cost = 1 if result["needs_cost"] else 0
		for phase in self.phases:
			bucket = priced["phases"].get(str(phase.phase_number)) or {}
			phase.amount = bucket.get("amount", 0)
			phase.cost_amount = bucket.get("cost_amount", 0)
			phase.line_count = bucket.get("line_count", 0)
		t = priced["totals"]
		self.total_amount = t["amount"]
		self.line_count = t["line_count"]
		self.missing_cost_count = t["missing_cost"]
		self.missing_code_count = t["missing_code"]
		self.allowance_count = t["allowance_count"]
		self.total_cost = t["cost_amount"]
		self.margin_amount = t["margin_amount"]
		self.margin_pct = t["margin_pct"]
		self.commission_amount = t["commission_amount"]
		self.net_margin_amount = t["net_margin_amount"]
		self.net_margin_pct = t["net_margin_pct"]
		return priced

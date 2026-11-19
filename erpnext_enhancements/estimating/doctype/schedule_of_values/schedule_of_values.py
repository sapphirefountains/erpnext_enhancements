# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Schedule of Values: the contract broken into the lines the customer signed.

Estimating milestone 2. Pay applications bill against these lines month by month
(``Pay Application``); the arithmetic is :mod:`erpnext_enhancements.estimating.pay_app_math`.

Things this is careful about:

* **No cost and no markup, anywhere on this document** (TASK-2026-02117). It is handed to a
  customer or a general contractor, so the fields are absent rather than hidden: a permlevel field
  that exists is one export or print format away from leaving the building.
* **A line's identity is its ``line_key``**, minted once (``quality.stable_keys``) and never
  regenerated. A pay application chains month to month by key, so reordering, renumbering or
  rewording rows never repoints a line that has been billed.
* **Manual is the day-one basis**, not a fallback: most jobs have no estimate. "From an estimate"
  fills the lines once (``api/estimating.make_schedule_of_values``) and they stay editable.
* **A locked schedule is the contract breakdown.** Its lines' values cannot move; scope changes
  arrive as change-order rows (``add_change_orders``), which are the one thing a locked schedule
  accepts. It cannot lock while the lines and the contract sum disagree, and it cannot unlock once
  a pay application has been certified against it.
* **A billed line cannot be deleted**: the delete is refused with the line's name, never orphaned.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html, flt, getdate

from erpnext_enhancements.estimating import pay_app_math as pm
from erpnext_enhancements.quality import stable_keys

#: What a locked schedule's base lines may not change.
FROZEN = ("scheduled_value", "description", "by_others")


def _name(row):
	return escape_html(f"{row.get('item_no') or ''} {row.get('description') or ''}".strip())


def _differs(prior, row):
	if (prior.get("description") or "").strip() != (row.get("description") or "").strip():
		return True
	return any(flt(prior.get(f)) != flt(row.get(f)) for f in FROZEN if f != "description")


def billed_keys(schedule):
	"""Line keys that appear on a certified pay application of this schedule, with or without work.

	A row with nothing billed yet still sits on the certificate: a change order row moved its lines 2
	and 3 the moment it appeared. So appearing is what counts, not a non-zero amount.
	"""
	if not schedule or not frappe.db.table_exists("Pay Application Line"):
		return set()
	rows = frappe.db.sql(
		"""select distinct pal.line_key from `tabPay Application Line` pal
		join `tabPay Application` pa on pa.name = pal.parent
		where pa.schedule_of_values = %s and pa.docstatus = 1""",
		(schedule,),
	)
	return {r[0] for r in rows}


def has_certified(schedule):
	return bool(frappe.db.exists("Pay Application", {"schedule_of_values": schedule, "docstatus": 1}))


class ScheduleofValues(Document):
	def validate(self):
		if not self.title:
			self.title = self.project
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
				"Global Defaults", "default_company"
			)
		self.hold_status()
		self.refuse_second_schedule()
		stable_keys.mint_missing_keys(self.lines, frappe.generate_hash, "line_key")
		self.refuse_duplicates()
		self.check_rates()
		self.check_change_orders()
		self.guard_locked_and_billed()
		for row in self.lines:
			if row.by_others:
				row.scheduled_value = 0
		self.total_up()

	def hold_status(self):
		"""Status moves only through ``lock_schedule`` / ``unlock_schedule``, which write it with db_set
		after their own checks. A save never changes it: read_only is not enforced on a save at v16.56
		(``frappe.client.set_value`` and the REST update just save), so a typed status would skip both."""
		before = self.get_doc_before_save() if not self.is_new() else None
		for field in ("status", "locked_on", "locked_by"):
			self.set(field, before.get(field) if before else None)
		if not self.status:
			self.status = "Draft"

	def refuse_second_schedule(self):
		if not self.project:
			return
		other = frappe.db.get_value(
			"Schedule of Values", {"project": self.project, "name": ["!=", self.name or ""]}, "name"
		)
		if other:
			frappe.throw(
				_("Project {0} already has schedule of values {1}. A job bills against one schedule.").format(
					escape_html(self.project), other
				)
			)

	def check_change_orders(self):
		"""A change-order row is its Change Order, exactly: an approved one of this project, at its price
		and approval date. The row cannot be typed to say anything else."""
		seen = set()
		for row in self.lines:
			if not row.change_order:
				continue
			if row.change_order in seen:
				frappe.throw(
					_("Change order {0} is on the schedule twice.").format(escape_html(row.change_order))
				)
			seen.add(row.change_order)
			co = frappe.db.get_value(
				"Change Order",
				row.change_order,
				["project", "docstatus", "customer_approved", "cost_impact", "customer_approved_on"],
				as_dict=True,
			)
			if not co or co.project != self.project or co.docstatus != 1 or not co.customer_approved:
				frappe.throw(
					_("{0} is not a submitted, customer-approved change order of project {1}.").format(
						escape_html(row.change_order), escape_html(self.project)
					)
				)
			row.scheduled_value = flt(co.cost_impact)
			row.approved_on = getdate(co.customer_approved_on) if co.customer_approved_on else None
			row.by_others = 0

	def refuse_duplicates(self):
		dupes = stable_keys.duplicate_keys(self.lines, "line_key")
		if dupes:
			frappe.throw(
				_("Two rows share a line key (a duplicated row). Delete the copy and add a new row.")
			)
		seen, repeated = set(), []
		for row in self.lines:
			number = (row.item_no or "").strip()
			if number and number in seen:
				repeated.append(escape_html(number))
			seen.add(number)
		if repeated:
			frappe.throw(_("Item numbers are used twice: {0}").format(", ".join(repeated)))

	def check_rates(self):
		for label, value in (
			(_("Retainage on work"), self.retainage_pct),
			(_("Retainage on stored materials"), self.stored_retainage_pct),
		):
			if flt(value) < 0 or flt(value) > 100:
				frappe.throw(_("{0} must be between 0 and 100%.").format(label))
		for step in self.retainage_steps or []:
			for value in (step.from_percent_complete, step.work_pct, step.stored_pct):
				if flt(value) < 0 or flt(value) > 100:
					frappe.throw(
						_("Retainage step {0}: every percent must be between 0 and 100.").format(step.idx)
					)
		if self.billing_mode == "Milestones":
			total = sum(flt(m.percent) for m in self.milestones or [])
			if total > 100.0001:
				frappe.throw(_("The milestones add up to {0}%, more than the whole contract.").format(total))
			for m in self.milestones or []:
				m.amount = pm.dollars(pm.share(pm.cents(self.contract_sum), m.percent))

	def guard_locked_and_billed(self):
		before = self.get_doc_before_save() if not self.is_new() else None
		if not before:
			return
		old = {r.line_key: r for r in before.lines if r.line_key}
		new = {r.line_key: r for r in self.lines if r.line_key}
		# Billed to date is written by the pay application (refresh_billed) without touching modified, so a
		# form open across a certify would save its stale copy back. The stored figure always wins.
		for key, row in new.items():
			row.billed_to_date = old[key].billed_to_date if key in old else 0
		billed = billed_keys(self.name)
		gone = [r for key, r in old.items() if key not in new]
		billed_gone = [_name(r) for r in gone if r.line_key in billed]
		if billed_gone:
			frappe.throw(
				_(
					"These lines have been billed on a certified pay application and cannot be deleted: {0}"
				).format(", ".join(billed_gone))
			)
		if before.status != "Locked":
			return
		if flt(before.contract_sum) != flt(self.contract_sum):
			frappe.throw(_("The schedule is locked: its contract sum changes only through a change order."))
		for field, label in (
			("project", _("project")),
			("billing_mode", _("billing mode")),
			("stored_materials", _("stored materials setting")),
		):
			if (before.get(field) or "") != (self.get(field) or ""):
				frappe.throw(
					_(
						"The schedule is locked: its {0} is part of how the job bills and cannot change."
					).format(label)
				)
		milestones = lambda doc: [((m.milestone or "").strip(), flt(m.percent)) for m in doc.milestones or []]  # noqa: E731
		if milestones(before) != milestones(self):
			frappe.throw(_("The schedule is locked: its milestones cannot change."))
		# The terms the job was signed on: unticking "held", or changing a rate or a step, mid-job would
		# change what every later application holds (and unticking would release retainage nobody has
		# agreed to release). Frozen at lock; accounting can still override one application's rate for
		# that period's work (permlevel 1). Earlier retainage stays held either way (keep holding).
		terms = lambda doc: (  # noqa: E731
			bool(doc.retainage_applies),
			flt(doc.retainage_pct),
			flt(doc.stored_retainage_pct),
			[
				(flt(x.from_percent_complete), flt(x.work_pct), flt(x.stored_pct))
				for x in doc.retainage_steps or []
			],
		)
		if terms(before) != terms(self):
			frappe.throw(_("The schedule is locked: its retainage terms cannot change."))
		changed = []
		for key, row in new.items():
			prior = old.get(key)
			if prior is None:
				if not row.change_order:
					changed.append(_name(row))
				continue
			# A row's change order is part of its identity: blanking it would turn an approved change
			# order into a base line that can be retyped (and then let the order be canceled), and
			# naming one on a base row would do the reverse. Neither moves on a locked schedule.
			if (prior.change_order or "") != (row.change_order or ""):
				changed.append(_name(row))
				continue
			if prior.change_order:
				continue
			if _differs(prior, row):
				changed.append(_name(row))
		changed += [_name(r) for r in gone if not r.change_order]
		if changed:
			frappe.throw(
				_(
					"The schedule is locked, so its lines are the contract breakdown. A change in scope is a change "
					"order. Changed: {0}"
				).format(", ".join(changed))
			)

	def total_up(self):
		base = [r for r in self.lines if not r.change_order]
		changes = [r for r in self.lines if r.change_order]
		scheduled = sum(pm.cents(r.scheduled_value) for r in base)
		change = sum(pm.cents(r.scheduled_value) for r in changes)
		self.scheduled_total = pm.dollars(scheduled)
		self.change_order_total = pm.dollars(change)
		self.difference = pm.dollars(pm.cents(self.contract_sum) - scheduled)
		self.contract_sum_to_date = pm.dollars(pm.cents(self.contract_sum) + change)
		self.billed_to_date = pm.dollars(sum(pm.cents(r.billed_to_date) for r in self.lines))

	def lock_problems(self):
		"""Why this schedule cannot lock yet, all at once."""
		out = []
		if not [r for r in self.lines if not r.by_others and not r.change_order]:
			out.append(_("Add the lines the customer signed."))
		if pm.cents(self.difference):
			out.append(
				_("The lines add up to {0}, not the contract sum of {1}.").format(
					frappe.format_value(self.scheduled_total, {"fieldtype": "Currency"}),
					frappe.format_value(self.contract_sum, {"fieldtype": "Currency"}),
				)
			)
		if self.billing_mode == "Milestones":
			total = sum(pm.cents(m.percent) for m in self.milestones or [])
			if total != 10000:
				out.append(
					_(
						"The milestones add up to {0}%. Billing by milestones needs them to total 100%."
					).format(pm.dollars(total))
				)
		missing = [_name(r) for r in self.lines if not (r.description or "").strip()]
		if missing:
			out.append(_("Every line needs a description."))
		return out

	def on_trash(self):
		if has_certified(self.name):
			frappe.throw(_("A pay application has been certified against this schedule, so it is kept."))

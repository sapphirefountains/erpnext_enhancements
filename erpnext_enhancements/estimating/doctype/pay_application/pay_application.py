# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Pay Application: one period's AIA progress billing (G702 and G703) against a Schedule of Values.

Estimating milestone 2. The project manager marks this period's percent per line on site and sends
it to accounting; accounting reviews the amount due and **certifies**, which submits it (Nik,
2026-11-06). The arithmetic is :mod:`erpnext_enhancements.estimating.pay_app_math`; this controller
gathers its inputs, stores every number it returns, and refuses.

Things this is careful about:

* **The chain is a stored Link**, ``previous_application``, set when the application is made: the
  latest one of the same schedule. Never inferred from dates, which reorder themselves the moment
  somebody back-dates a period. One open application per schedule at a time.
* **Lines chain by ``line_key``**, so column D for a line reads that line's own history whatever the
  schedule's order, numbering or wording became.
* **Everything is stored.** A certified application is a record: it keeps the scheduled values,
  rates and amounts it was certified at, even after the schedule gains change orders. Only a draft
  is re-synced from the schedule.
* **A change order row joins from the application whose period reaches its approval date**, and
  "approved this month" means "not on the previous application", so a change order approved in
  March and added to the schedule in June moves line 2 in June, the month it actually moved.
* **All refusals at once, at certify** (``pay_app_math.refusals``). A pay application that quietly
  computes a defensible-looking wrong number is worse than one that will not certify.
* **Amending is refused** (it would mint ``PAY-26-0003-1``); a wrong application is canceled, and
  only the latest one can be.
* **Releasing retainage (B9)** is accounting's: a per-line amount at permlevel 1, on any application.
  A **Final** application releases all of it, and certifying one closes the schedule of values: no
  application or change order joins a job that has been billed out. Canceling it reopens the schedule.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, escape_html, flt, getdate, now_datetime

from erpnext_enhancements.estimating import pay_app_math as pm

LINE_SYNC = ("item_no", "description", "phase_number", "scheduled_value", "by_others")
LINE_RESULTS = (
	"scheduled_value",
	"from_previous",
	"retainage_previous",
	"retainage_held_now",
	"retainage_release",
	"retainage_released_previous",
	"retainage_released_to_date",
	"this_period",
	"stored",
	"total_completed",
	"percent_complete",
	"balance",
	"work_completed_to_date",
	"work_retainage_pct",
	"stored_retainage_pct",
	"retainage_work",
	"retainage_stored",
	"retainage",
)
TOTALS = (
	"original_contract_sum",
	"net_change_orders",
	"contract_sum_to_date",
	"total_completed_stored",
	"retainage_work",
	"retainage_stored",
	"total_retainage",
	"retainage_released",
	"retainage_released_to_date",
	"total_earned_less_retainage",
	"less_previous_certificates",
	"current_payment_due",
	"balance_to_finish",
	"percent_complete",
	"scheduled_total",
	"from_previous_total",
	"this_period_total",
	"stored_total",
	"balance_total",
	"work_retainage_pct",
	"stored_retainage_pct",
	"retainage_source",
	"percent_complete_at_start",
)


def latest_application(schedule, exclude=None):
	filters = {"schedule_of_values": schedule, "docstatus": ["<", 2]}
	if exclude:
		filters["name"] = ["!=", exclude]
	rows = frappe.get_all(
		"Pay Application",
		filters=filters,
		fields=["name", "docstatus", "application_no", "period_to"],
		order_by="application_no desc",
		limit=1,
	)
	return rows[0] if rows else None


def schedule_context(sov):
	"""The schedule as the engine reads it, and as the lines editor is told it."""
	return {
		"contract_sum": flt(sov.contract_sum),
		"retainage_applies": 1 if sov.retainage_applies else 0,
		"retainage_pct": flt(sov.retainage_pct),
		"stored_retainage_pct": flt(sov.stored_retainage_pct),
		"stored_materials": 1 if sov.stored_materials else 0,
		"billing_mode": sov.billing_mode or "Progress",
		"steps": [
			{
				"from_percent_complete": flt(s.from_percent_complete),
				"work_pct": flt(s.work_pct),
				"stored_pct": flt(s.stored_pct),
			}
			for s in sov.retainage_steps or []
		],
		"milestones": [
			{"milestone": m.milestone, "percent": flt(m.percent), "amount": flt(m.amount)}
			for m in sov.milestones or []
		],
	}


def refresh_billed(schedule):
	"""Copy each line's completed-and-stored from the latest certified application onto the schedule."""
	rows = frappe.get_all(
		"Pay Application",
		filters={"schedule_of_values": schedule, "docstatus": 1},
		fields=["name"],
		order_by="application_no desc",
		limit=1,
	)
	billed = {}
	retainage = {"retainage_held": 0, "retainage_released": 0}
	if rows:
		latest = frappe.db.get_value(
			"Pay Application", rows[0].name, ["total_retainage", "retainage_released_to_date"], as_dict=True
		)
		retainage = {
			"retainage_held": flt(latest.total_retainage),
			"retainage_released": flt(latest.retainage_released_to_date),
		}
		for line in frappe.get_all(
			"Pay Application Line",
			filters={"parent": rows[0].name, "parenttype": "Pay Application"},
			fields=["line_key", "total_completed"],
		):
			billed[line.line_key] = flt(line.total_completed)
	total = 0
	for line in frappe.get_all(
		"Schedule of Values Line",
		filters={"parent": schedule, "parenttype": "Schedule of Values"},
		fields=["name", "line_key"],
	):
		value = billed.get(line.line_key, 0)
		total += pm.cents(value)
		frappe.db.set_value(
			"Schedule of Values Line", line.name, "billed_to_date", value, update_modified=False
		)
	frappe.db.set_value(
		"Schedule of Values",
		schedule,
		dict(retainage, billed_to_date=pm.dollars(total)),
		update_modified=False,
	)


class PayApplication(Document):
	# ------------------------------------------------------------------ the chain
	def before_insert(self):
		if self.amended_from:
			frappe.throw(
				_("A pay application is not amended. Start the next one from the schedule of values.")
			)
		sov = frappe.get_doc("Schedule of Values", self.schedule_of_values)
		if sov.status == "Closed":
			frappe.throw(
				_(
					"The final application of this schedule has been certified, so the job is billed out. To "
					"bill again, cancel the final application."
				)
			)
		if sov.status != "Locked":
			frappe.throw(_("Lock the schedule of values before starting a pay application against it."))
		previous = latest_application(self.schedule_of_values)
		if previous and previous.docstatus == 0:
			frappe.throw(
				_(
					"Application {0} is still open. Certify it first, or ask a System Manager to delete it."
				).format(previous.application_no)
			)
		self.link_previous(previous)
		self.status = "Draft"
		self.drop_unpermitted_overrides()

	def drop_unpermitted_overrides(self):
		"""The retainage overrides and the certification are permlevel 1, accounting's. Frappe resets
		such fields for a user without access, but at v16.56 ``validate_higher_perm_levels`` returns
		before it resets the CHILD rows of a new document, so a line override sent with an insert would
		survive. Cleared here, for parent and lines, unless the user may write level 1."""
		if self.flags.ignore_permissions or frappe.session.user == "Administrator":
			return
		if 1 in self.get_permlevel_access("write"):
			return
		self.work_retainage_override = ""
		self.stored_retainage_override = ""
		self.certified_less = 0
		self.amount_certified = 0
		for row in self.lines:
			row.work_retainage_override = ""
			row.stored_retainage_override = ""
			row.retainage_release = 0

	def link_previous(self, previous):
		self.previous_application = previous.name if previous else None
		self.application_no = (previous.application_no or 0) + 1 if previous else 1
		if previous and previous.period_to and not self.period_from:
			self.period_from = add_days(previous.period_to, 1)

	def derive_chain(self):
		"""The chain is the server's, never the form's. read_only is not enforced on a save at v16.56
		(``frappe.client.set_value`` and the REST update just save), and a blanked previous_application
		would price every line from nothing: column D and line 7 at 0, the whole job billed again. So the
		link and the number are derived again on every save from the latest other application of the
		same schedule, whatever the request carried."""
		previous = latest_application(self.schedule_of_values, exclude=self.name)
		if previous and previous.docstatus == 0:
			frappe.throw(
				_("Application {0} of this schedule is still open; only one can be open at a time.").format(
					previous.application_no
				)
			)
		self.link_previous(previous)
		if self.docstatus == 0 and self.status not in ("Draft", "To Review"):
			self.status = "Draft"

	def previous_doc(self):
		if not self.previous_application:
			return None
		if self.previous_application == self.name:
			frappe.throw(_("An application cannot follow itself."))
		prev = frappe.get_doc("Pay Application", self.previous_application)
		if prev.schedule_of_values != self.schedule_of_values:
			frappe.throw(_("The previous application belongs to a different schedule of values."))
		claimed = frappe.db.exists(
			"Pay Application",
			{"previous_application": prev.name, "docstatus": ["<", 2], "name": ["!=", self.name or ""]},
		)
		if claimed:
			frappe.throw(_("Application {0} already follows {1}.").format(escape_html(claimed), prev.name))
		return prev

	# ------------------------------------------------------------------ pricing
	def validate(self):
		sov = frappe.get_doc("Schedule of Values", self.schedule_of_values)
		self.project = sov.project
		self.customer = sov.customer
		self.billing_mode = sov.billing_mode
		if self.application_type not in pm.APPLICATION_TYPES:
			self.application_type = pm.PROGRESS
		self.derive_chain()
		prev = self.previous_doc()
		self.sync_lines(sov)
		self.apply_milestone(sov)
		self.clean_overrides()
		self.settle_type_inputs(prev)
		# Work completed and retainage held, per line, as the previous application stored them: earlier
		# retainage stays held at its own rate (keep holding, Nik 2026-11-19).
		previous_work = {
			r.line_key: {
				"work": flt(r.work_completed_to_date),
				"retainage_work": flt(r.retainage_work),
				"released": flt(r.retainage_released_to_date),
			}
			for r in (prev.lines if prev else [])
		}
		self.less_previous_certificates = pm.next_previous_certificates(prev.as_dict() if prev else None)
		context = schedule_context(sov)
		priced = pm.price_application(
			context, self.as_dict(), [r.as_dict() for r in self.lines], previous_work
		)
		for row, result in zip(self.lines, priced["lines"]):
			row.this_period_pct = result["this_period_pct"]
			for field in LINE_RESULTS:
				row.set(field, result[field])
		for field in TOTALS:
			self.set(field, priced["totals"][field])
		prev_keys = set(previous_work)
		this_month = sum(
			pm.cents(r.scheduled_value)
			for r in self.lines
			if r.is_change_order and r.line_key not in prev_keys
		)
		self.co_this_month = pm.dollars(this_month)
		self.co_previous_months = pm.dollars(pm.cents(self.net_change_orders) - this_month)
		if not self.certified_less:
			self.amount_certified = self.current_payment_due
		self.billing_context = json.dumps(context, sort_keys=True)
		self.flags.priced = priced
		self.flags.sov = sov
		self.flags.prev = prev

	def sync_lines(self, sov):
		"""Bring the draft's lines in step with the schedule: its base lines, plus every change order
		row approved on or before the end of this period. A line no longer on the schedule is kept
		(and refused at certify, by name) rather than silently dropped."""
		end = getdate(self.period_to) if self.period_to else None
		existing = {r.line_key: r for r in self.lines if r.line_key}
		rows = []
		on_schedule = set()
		excluded = []
		orders = [line.change_order for line in sov.lines if line.change_order]
		canceled = set(
			frappe.get_all("Change Order", filters={"name": ["in", orders], "docstatus": 2}, pluck="name")
			if orders
			else []
		)
		for line in sov.lines:
			if line.change_order in canceled:
				continue
			if line.change_order and line.approved_on and end and getdate(line.approved_on) > end:
				excluded.append(line.change_order)
				continue
			on_schedule.add(line.line_key)
			row = existing.get(line.line_key)
			values = {f: line.get(f) for f in LINE_SYNC}
			values["is_change_order"] = 1 if line.change_order else 0
			values["line_key"] = line.line_key
			if row is None:
				values["this_period_pct"] = 0
				rows.append(values)
			else:
				row.update(values)
				rows.append(row)
		# A line that left (a change order approved after this period once the period moved earlier, or
		# one canceled) drops out when nothing was typed on it, and is kept to be refused, by name, when
		# something was.
		rows += [
			r
			for key, r in existing.items()
			if key not in on_schedule and (flt(r.this_period_pct) or flt(r.stored))
		]
		self.flags.on_schedule = on_schedule
		self.flags.excluded_change_orders = excluded
		# Existing rows go back as themselves (same child row names), new ones as dicts. append() keeps an
		# existing row's old idx (frappe v16.56 Document.append), so renumber after.
		self.set("lines", rows)
		for i, row in enumerate(self.lines, start=1):
			row.idx = i

	def apply_milestone(self, sov):
		"""Milestone billing: every base line bills the milestone's percent, each milestone once.

		Change-order rows sit outside the milestones and bill by their own percent, typed like a
		progress job. A change order is scope the milestones were never written for: putting a
		milestone's 50% on it, with each milestone billed once, would leave the rest of an approved
		change order unbillable for good. An application may bill change orders alone, with no
		milestone picked; then its base lines bill nothing.
		"""
		if sov.billing_mode != "Milestones":
			return
		percent = 0.0
		if self.milestone:
			match = [
				m for m in sov.milestones if (m.milestone or "").strip() == (self.milestone or "").strip()
			]
			if not match:
				frappe.throw(
					_("Milestone {0} is not on the schedule of values.").format(escape_html(self.milestone))
				)
			billed = frappe.db.exists(
				"Pay Application",
				{
					"schedule_of_values": self.schedule_of_values,
					"milestone": self.milestone,
					"docstatus": 1,
					"name": ["!=", self.name or ""],
				},
			)
			if billed:
				frappe.throw(
					_("Milestone {0} was billed on {1}.").format(escape_html(self.milestone), billed)
				)
			percent = flt(match[0].percent)
		for row in self.lines:
			if row.is_change_order:
				continue
			row.this_period_pct = 0 if row.by_others else percent

	def clean_overrides(self):
		"""Retainage overrides as typed: blank inherits, a number 0 to 100 is a rate, anything else is
		refused by name (the Estimate's override rule), so a typo never reaches the engine."""
		targets = [(self, ("work_retainage_override", "stored_retainage_override"), _("this application"))]
		targets += [
			(row, ("work_retainage_override", "stored_retainage_override"), _("line {0}").format(row.idx))
			for row in self.lines
		]
		for doc, fields, where in targets:
			for field in fields:
				raw = doc.get(field)
				try:
					value = pm.em.parse_rate(raw)
				except (TypeError, ValueError):
					frappe.throw(
						_("Retainage override on {0} is not a percent: {1}").format(where, escape_html(raw))
					)
				if value is not None and not 0 <= value <= 100:
					frappe.throw(_("Retainage override on {0} must be 0 to 100%.").format(where))
				doc.set(field, "" if value is None else f"{value:g}")

	def settle_type_inputs(self, prev):
		"""What the application's type does to the inputs, after frappe's permlevel reset has run.

		* A **Final** application stores the release it computes (everything held) in each line's
		  ``retainage_release``, so a certified final is a complete record. When the type moves off Final,
		  that computed release is not anybody's input: it is cleared, or a Progress application would
		  quietly release everything (and a project manager's save keeps a level-1 value as it was).
		* A **Retainage Release** application bills no new work and moves no stored material: stored is a
		  balance, so each line keeps the balance the previous application certified.
		"""
		before = self.get_doc_before_save() if not self.is_new() else None
		if before and before.get("application_type") == pm.FINAL and self.application_type != pm.FINAL:
			for row in self.lines:
				row.retainage_release = 0
		if self.application_type == pm.RELEASE:
			stored = {r.line_key: flt(r.stored) for r in (prev.lines if prev else [])}
			for row in self.lines:
				row.stored = stored.get(row.line_key, 0)

	def unbilled_change_orders(self, sov):
		"""Approved change orders of the project that a Final application would leave unbilled for good:
		the schedule's own rows approved after this period, and any customer-approved change order that
		was never added to the schedule (a closed schedule takes no more)."""
		on_schedule = {line.change_order for line in sov.lines if line.change_order}
		approved = frappe.get_all(
			"Change Order",
			filters={"project": sov.project, "docstatus": 1, "customer_approved": 1},
			pluck="name",
		)
		missing = sorted(name for name in approved if name not in on_schedule)
		return list(self.flags.excluded_change_orders or ()) + missing

	# ------------------------------------------------------------------ certify
	def before_submit(self):
		if not self.flags.priced:
			self.validate()
		sov, prev, priced = self.flags.sov, self.flags.prev, self.flags.priced
		context = {
			"schedule_locked": sov.status == "Locked",
			"schedule_keys": sorted(self.flags.on_schedule or ()),
			"previous": {
				"name": prev.name,
				"docstatus": prev.docstatus,
				"period_to": str(prev.period_to or ""),
			}
			if prev
			else None,
			"period_from": str(self.period_from or "") or None,
			"period_to": str(self.period_to or "") or None,
			"stored_materials": bool(sov.stored_materials),
			"amount_certified": self.amount_certified,
			"lines": [r.as_dict() for r in self.lines],
			"application_type": self.application_type,
			"excluded_change_orders": self.unbilled_change_orders(sov)
			if self.application_type == pm.FINAL
			else list(self.flags.excluded_change_orders or ()),
		}
		reasons = [msg for _code, msg in pm.refusals(context, priced)]
		# Only a progress application has to bill something: a release bills no work by definition, and a
		# final one may follow the last milestone (it is refused anyway unless every line is finished).
		billed_changes = any(r.is_change_order and pm.cents(r.this_period) for r in self.lines)
		if (
			sov.billing_mode == "Milestones"
			and self.application_type == pm.PROGRESS
			and not self.milestone
			and not billed_changes
		):
			reasons.append(_("Pick the milestone this application bills."))
		if reasons:
			frappe.throw(
				"<ul>" + "".join(f"<li>{escape_html(r)}</li>" for r in reasons) + "</ul>",
				title=_("Not ready to certify"),
			)
		self.status = "Certified"
		self.certified_by = frappe.session.user
		self.certified_on = now_datetime()

	def on_submit(self):
		refresh_billed(self.schedule_of_values)
		if self.application_type == pm.FINAL:
			frappe.db.set_value("Schedule of Values", self.schedule_of_values, "status", "Closed")
			frappe.get_doc("Schedule of Values", self.schedule_of_values).add_comment(
				"Info", _("Closed: final application {0} was certified.").format(self.name)
			)

	def before_cancel(self):
		later = frappe.db.get_value(
			"Pay Application", {"previous_application": self.name, "docstatus": ["<", 2]}, "name"
		)
		if later:
			frappe.throw(
				_("Application {0} follows this one. Only the latest application can be canceled.").format(
					later
				)
			)

	def on_cancel(self):
		self.db_set("status", "Canceled")
		refresh_billed(self.schedule_of_values)
		if self.application_type == pm.FINAL and (
			frappe.db.get_value("Schedule of Values", self.schedule_of_values, "status") == "Closed"
		):
			frappe.db.set_value("Schedule of Values", self.schedule_of_values, "status", "Locked")
			frappe.get_doc("Schedule of Values", self.schedule_of_values).add_comment(
				"Info", _("Reopened: final application {0} was canceled.").format(self.name)
			)

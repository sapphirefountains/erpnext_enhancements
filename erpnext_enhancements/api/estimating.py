# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Estimate's buttons: issue the Quotation, start a new revision, and the cost-code list.

The Estimate issues a native draft **Quotation** (Nik, 2026-11-04: "Quotation is used"; the proposal
print lives on it and the sales pipeline stays native). One Quotation line per phase, at the phase's
price; never a cost, a markup or a margin.

Things this is careful about, including the ones that look like bugs:

* **The deal is linked by ``custom_source_opportunity``, never ERPNext's ``opportunity`` field.**
  That field drives the Opportunity's own status (Quotation, Converted, Lost) and freezes its stage;
  the product configurator made the same choice for the same reason, and
  ``crm_enhancements.product_sale.mirror_deal_link`` reads the custom field.
* **No sales tax on a build quotation.** In Utah a contractor improving real property pays the tax
  on its materials as a cost (concept card 14), so it is inside the line prices already. Only a
  Product job takes the company's default tax template, the way a product sale does elsewhere.
* **A draft Quotation is refreshed in place; a submitted or cancelled one is never touched.** Once a
  quotation has gone to the customer its numbers are a record, so issuing again from a changed
  estimate makes a new draft, and the estimate links the newest.
* **A line priced at nothing stops the issue.** A line with no cost and no sheet price would print
  as part of a phase at a price that silently leaves it out. "By others" lines are the exception:
  they are worth nothing on purpose and print as exclusions.
* **Rates are set after ``set_missing_values`` too.** ERPNext fills item details from the price list
  there; the estimate's price is the price.
* **EST-SCOPE keeps a 0 list price on the quotation's price list.** Prod auto-inserts a missing Item
  Price from the first transaction's rate, so without one the first phase price ever issued would
  become EST-SCOPE's list price (``estimating.seed.hold_zero_price``). The seed patch puts one on the
  default list; a customer with their own price list gets one here, on first issue.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, flt, getdate, now_datetime, nowdate

from erpnext_enhancements.estimating import rules, seed

#: The one non-stock Item every estimate line on a Quotation uses (``patches/seed_estimate_scope_item``).
SCOPE_ITEM = seed.SCOPE_ITEM


def _party(estimate):
	if estimate.customer:
		return "Customer", estimate.customer
	if estimate.opportunity:
		opp = frappe.db.get_value(
			"Opportunity", estimate.opportunity, ["opportunity_from", "party_name"], as_dict=True
		)
		if opp and opp.opportunity_from and opp.party_name:
			return opp.opportunity_from, opp.party_name
	frappe.throw(_("Pick an account or an opportunity first: the quotation needs someone to go to."))


def _line_dicts(estimate):
	return [line.as_dict() for line in estimate.lines]


def _phase_dicts(estimate):
	return [phase.as_dict() for phase in estimate.phases]


def _still_valid(date):
	return date if date and getdate(date) >= getdate(nowdate()) else None


def _default_taxes(quotation):
	try:
		from erpnext.controllers.accounts_controller import _get_default_taxes_and_charges

		taxes = _get_default_taxes_and_charges("Sales Taxes and Charges Template", company=quotation.company)
	except Exception:
		return
	if taxes and taxes.get("taxes"):
		quotation.update(taxes)


def _hold_zero_price(price_list):
	# Before the insert, whose validate is where ERPNext would auto-insert a price. Never blocks the
	# issue: the worst case without it is the stray list price the seed is there to prevent.
	try:
		seed.hold_zero_price(price_list)
	except Exception:
		frappe.log_error(title="Estimating: EST-SCOPE zero price", message=frappe.get_traceback())


@frappe.whitelist(methods=["POST"])
def issue_quotation(estimate):
	"""Make or refresh the Estimate's draft Quotation. Returns its name."""
	doc = frappe.get_doc("Estimate", estimate)
	doc.check_permission("write")
	if not frappe.has_permission("Quotation", "create"):
		# The Estimator role grants nothing on Quotation; issuing needs a selling role as well.
		frappe.throw(
			_("Issuing a quotation needs permission to create Quotations (Sales User or Sales Manager)."),
			frappe.PermissionError,
		)
	if doc.status == "Superseded":
		frappe.throw(_("This revision is superseded. Issue the quotation from the newer revision."))
	if not frappe.db.exists("Item", SCOPE_ITEM):
		frappe.throw(
			_(
				"The Item {0} that quotation lines use is missing. Create it as a non-stock, sales-only Item "
				"(the seed patch logged why it could not)."
			).format(SCOPE_ITEM)
		)

	doc.save()  # price it again, exactly as stored
	lines = _line_dicts(doc)
	if not [line for line in lines if not line.get("by_others")]:
		frappe.throw(_("Add a line before issuing a quotation."))
	unpriced = rules.unpriced_lines(lines, lines)
	if unpriced:
		names = ", ".join(
			escape_html(f"{line.get('idx')} {line.get('description')}") for line in unpriced[:8]
		)
		frappe.throw(
			_("These lines have no cost and no sheet price, so they would be quoted at nothing: {0}").format(
				names
			),
			title=_("Lines without a price"),
		)

	rows, exclusions = rules.quotation_rows(_phase_dicts(doc), lines, lines)
	quotation_to, party_name = _party(doc)

	quotation = None
	if doc.quotation and frappe.db.get_value("Quotation", doc.quotation, "docstatus") == 0:
		quotation = frappe.get_doc("Quotation", doc.quotation)
		quotation.check_permission("write")
	if quotation is None:
		quotation = frappe.new_doc("Quotation")

	item = frappe.db.get_value("Item", SCOPE_ITEM, ["item_name", "stock_uom"], as_dict=True)
	quotation.update(
		{
			"quotation_to": quotation_to,
			"party_name": party_name,
			"company": doc.company,
			"transaction_date": nowdate(),
			# ERPNext refuses a quotation valid until a day before it is dated; an old revision's date is dropped.
			"valid_till": _still_valid(doc.valid_till) or _still_valid(quotation.get("valid_till")),
			"order_type": "Sales",
		}
	)
	if quotation.meta.has_field("custom_source_opportunity") and doc.opportunity:
		quotation.custom_source_opportunity = doc.opportunity
	if quotation.meta.has_field("custom_estimate"):
		quotation.custom_estimate = doc.name

	quotation.set("items", [])
	for row in rows:
		quotation.append(
			"items",
			{
				"item_code": SCOPE_ITEM,
				"item_name": row["item_name"],
				# Quotation Item description is HTML; the scope and line text in it were typed.
				"description": escape_html(row["description"]).replace("\n", "<br>"),
				"qty": 1,
				"uom": item.stock_uom,
				"stock_uom": item.stock_uom,
				"conversion_factor": 1,
				# No "rate" yet, on purpose: set_missing_values passes each row to ERPNext's
				# insert_item_price, which (auto-insert on, as on prod) makes an Item Price at the row's
				# rate on any list without one, and skips only when the rate is empty. The rate is set
				# below, after _hold_zero_price has put a 0 price on the quotation's list.
			},
		)
	quotation.set("taxes", [])
	quotation.taxes_and_charges = None
	if doc.job_kind == "Product":
		_default_taxes(quotation)
	quotation.terms = (doc.terms or "") + rules.exclusions_html(exclusions)

	# The estimate's price is the price: a selling Pricing Rule added later (a blanket discount) would
	# otherwise reprice these lines in validate, after the rates below are set.
	quotation.ignore_pricing_rule = 1
	quotation.run_method("set_missing_values")
	_hold_zero_price(quotation.get("selling_price_list"))
	if doc.job_kind != "Product":
		# set_missing_values copies the customer's tax template in (update_if_missing) and, outside
		# validate, appends its rows. A build price already carries its use tax as a cost.
		quotation.set("taxes", [])
		quotation.taxes_and_charges = None
		quotation.flags.dont_auto_add_taxes = True
	for qi, row in zip(quotation.items, rows):
		qi.rate = row["amount"]
		qi.price_list_rate = row["amount"]
		qi.discount_percentage = 0
		qi.margin_rate_or_amount = 0
		qi.item_name = row["item_name"]
	quotation.run_method("calculate_taxes_and_totals")
	if quotation.is_new():
		quotation.insert()
	else:
		quotation.save()

	doc.db_set({"quotation": quotation.name, "status": "Issued"})
	doc.add_comment(
		"Info", _("Issued quotation {0} at {1}.").format(quotation.name, rules.money(doc.total_amount))
	)
	return quotation.name


@frappe.whitelist(methods=["POST"])
def new_revision(estimate):
	"""Copy the Estimate as its next revision; the old one becomes Superseded and read-only."""
	old = frappe.get_doc("Estimate", estimate)
	old.check_permission("write")
	frappe.has_permission("Estimate", "create", throw=True)
	if old.status == "Superseded":
		frappe.throw(_("This revision is already superseded. Open the newest revision."))

	new = frappe.copy_doc(old)
	new.revision = (old.revision or 1) + 1
	new.previous_revision = old.name
	new.status = "Draft"
	new.transaction_date = nowdate()
	new.valid_till = None
	new.prepared_by = frappe.session.user
	# A quotation still in draft follows the newest revision, so issuing refreshes it rather than
	# leaving a second draft pointing at the superseded one.
	new.quotation = (
		old.quotation
		if old.quotation and frappe.db.get_value("Quotation", old.quotation, "docstatus") == 0
		else None
	)
	new.insert()

	old.flags.from_revision = True
	old.db_set("status", "Superseded")
	old.add_comment("Info", _("Superseded by revision {0}, {1}.").format(new.revision, new.name))
	return new.name


@frappe.whitelist(methods=["GET"])
def cost_codes():
	"""The cost code catalog a line may use, for the lines editor's picker. Public in the repo anyway."""
	return rules.code_options()


# ---------------------------------------------------------------- schedule of values (milestone 2)


@frappe.whitelist(methods=["POST"])
def make_schedule_of_values(estimate, rule="phase_cost_type"):
	"""A draft Schedule of Values filled from the Estimate's priced lines. Returns its name.

	Rows group the estimate's lines by phase and cost type (the 2026-11-04 rule) or one per line, and
	sum to the estimate exactly (``pay_app_math.sov_rows`` asserts the partition). The contract sum
	starts at the estimate's total; the rows stay editable until the schedule is locked.
	"""
	from erpnext_enhancements.estimating import pay_app_math as pm

	doc = frappe.get_doc("Estimate", estimate)
	doc.check_permission("read")
	frappe.has_permission("Schedule of Values", "create", throw=True)
	if doc.status == "Superseded":
		frappe.throw(_("This revision is superseded. Make the schedule from the newest revision."))
	if not doc.project:
		frappe.throw(_("Link the estimate to a project first: a schedule of values bills a project."))
	if rule not in ("phase_cost_type", "line"):
		rule = "phase_cost_type"
	rows, check = pm.sov_rows(_phase_dicts(doc), _line_dicts(doc), rule=rule, total=doc.total_amount)
	sov = frappe.new_doc("Schedule of Values")
	sov.update(
		{
			"title": doc.title,
			"project": doc.project,
			"customer": doc.customer,
			"basis": "Estimate",
			"estimate": doc.name,
			"estimate_revision": doc.revision,
			"contract_sum": check["total"],
			"company": doc.company,
		}
	)
	for row in rows:
		sov.append(
			"lines",
			{
				k: row[k]
				for k in (
					"item_no",
					"phase_number",
					"description",
					"cost_type",
					"scheduled_value",
					"by_others",
					"source_lines",
				)
			},
		)
	sov.insert()
	return sov.name


def _schedule(schedule, ptype="write"):
	sov = frappe.get_doc("Schedule of Values", schedule)
	sov.check_permission(ptype)
	return sov


def _co_description(co):
	"""What the change order adds, in the customer's words: the first line of its scope addendum."""
	from frappe.utils import strip_html

	text = " ".join(strip_html(co.scope_addendum or "").split())
	label = _("Change order {0}").format(co.co_number or co.name)
	return (f"{label}: {text}" if text else label)[:140]


@frappe.whitelist(methods=["POST"])
def add_change_orders(schedule):
	"""Add a row for every approved change order of the project not yet on the schedule.

	Approved means submitted and approved by the customer; the row carries the change order's price
	(its signed ``cost_impact``, a credit negative) and the date the customer approved it, which is
	the date that decides the first application it bills on. Returns how many rows were added.
	"""
	sov = _schedule(schedule)
	have = {r.change_order for r in sov.lines if r.change_order}
	# get_list, not get_all: a change order's price and scope go only to people who may read it.
	orders = frappe.get_list(
		"Change Order",
		filters={"project": sov.project, "docstatus": 1, "customer_approved": 1},
		fields=["name", "co_number", "cost_impact", "customer_approved_on", "scope_addendum", "cause"],
		order_by="co_number asc",
	)
	added = 0
	for co in orders:
		if co.name in have:
			continue
		number = co.co_number or 0
		sov.append(
			"lines",
			{
				"item_no": f"CO-{number:02d}" if number else co.name,
				"description": _co_description(co),
				"scheduled_value": flt(co.cost_impact),
				"change_order": co.name,
				"approved_on": getdate(co.customer_approved_on) if co.customer_approved_on else None,
			},
		)
		added += 1
	if added:
		sov.save()
	return added


@frappe.whitelist(methods=["POST"])
def lock_schedule(schedule):
	"""Lock the schedule: its lines become the contract breakdown that pay applications bill against."""
	sov = _schedule(schedule)
	if sov.status == "Locked":
		return sov.name
	sov.save()  # total it up exactly as stored
	problems = sov.lock_problems()
	if problems:
		frappe.throw(
			"<ul>" + "".join(f"<li>{escape_html(p)}</li>" for p in problems) + "</ul>",
			title=_("Not ready to lock"),
		)
	sov.db_set({"status": "Locked", "locked_on": now_datetime(), "locked_by": frappe.session.user})
	sov.add_comment("Info", _("Locked at a contract sum of {0}.").format(rules.money(sov.contract_sum)))
	return sov.name


@frappe.whitelist(methods=["POST"])
def unlock_schedule(schedule):
	"""Unlock a schedule nothing has been certified against yet."""
	sov = _schedule(schedule)
	if frappe.db.exists("Pay Application", {"schedule_of_values": sov.name, "docstatus": 1}):
		frappe.throw(
			_(
				"A pay application has been certified against this schedule, so its lines stay as they are. A change in scope is a change order."
			)
		)
	sov.db_set({"status": "Draft", "locked_on": None, "locked_by": None})
	sov.add_comment("Info", _("Unlocked."))
	return sov.name


@frappe.whitelist(methods=["POST"])
def start_pay_application(schedule, period_to=None):
	"""The next pay application of a locked schedule, as a draft. Returns its name."""
	sov = _schedule(schedule, "read")
	frappe.has_permission("Pay Application", "create", throw=True)
	pa = frappe.new_doc("Pay Application")
	pa.schedule_of_values = sov.name
	pa.period_to = getdate(period_to) if period_to else getdate(nowdate())
	pa.insert()
	return pa.name


def _pay_application(name):
	pa = frappe.get_doc("Pay Application", name)
	pa.check_permission("write")
	if pa.docstatus != 0:
		frappe.throw(_("This application is already certified."))
	return pa


@frappe.whitelist(methods=["POST"])
def send_for_review(pay_application):
	"""The project manager's "send to accounting": saves the percents and marks it To Review."""
	pa = _pay_application(pay_application)
	pa.status = "To Review"
	pa.sent_for_review_by = frappe.session.user
	pa.sent_for_review_on = now_datetime()
	pa.save()
	return pa.name


@frappe.whitelist(methods=["POST"])
def back_to_progress(pay_application):
	"""Accounting sends it back to the project manager."""
	pa = _pay_application(pay_application)
	pa.status = "Draft"
	pa.save()
	return pa.name

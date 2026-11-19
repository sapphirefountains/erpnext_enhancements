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
"""

import frappe
from frappe import _
from frappe.utils import escape_html, nowdate

from erpnext_enhancements.estimating import rules

#: The one non-stock Item every estimate line on a Quotation uses (``patches/seed_estimate_scope_item``).
SCOPE_ITEM = "EST-SCOPE"


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


def _default_taxes(quotation):
	try:
		from erpnext.controllers.accounts_controller import _get_default_taxes_and_charges

		taxes = _get_default_taxes_and_charges("Sales Taxes and Charges Template", company=quotation.company)
	except Exception:
		return
	if taxes and taxes.get("taxes"):
		quotation.update(taxes)


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
			_("The Item {0} that quotation lines use is missing. Run bench migrate.").format(SCOPE_ITEM)
		)

	doc.save()  # price it again, exactly as stored
	lines = _line_dicts(doc)
	if not [line for line in lines if not line.get("by_others")]:
		frappe.throw(_("Add a line before issuing a quotation."))
	unpriced = rules.unpriced_lines(lines, lines)
	if unpriced:
		names = ", ".join(f"{line.get('idx')} {line.get('description')}" for line in unpriced[:8])
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
			"valid_till": doc.valid_till or quotation.get("valid_till"),
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
				"rate": row["amount"],
			},
		)
	quotation.set("taxes", [])
	quotation.taxes_and_charges = None
	if doc.job_kind == "Product":
		_default_taxes(quotation)
	quotation.terms = (doc.terms or "") + rules.exclusions_html(exclusions)

	quotation.run_method("set_missing_values")
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
	new.quotation = None
	new.insert()

	old.flags.from_revision = True
	old.db_set("status", "Superseded")
	old.add_comment("Info", _("Superseded by revision {0}, {1}.").format(new.revision, new.name))
	return new.name


@frappe.whitelist(methods=["GET"])
def cost_codes():
	"""The cost code catalog a line may use, for the lines editor's picker. Public in the repo anyway."""
	return rules.code_options()

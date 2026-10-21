"""How a visit's chemicals are billed (v1.628.0).

Since v1.594.0 a visit records chemicals the way the technician counts them: doses ("1 cap
full", "2 tablets"), not stock quantities. Both invoice builders still billed ``qty`` of the
item at its selling price, so "1 cap full" of acid billed a bottle. Nik, 2026-10-13: per dose
or whole container "depends on the agreement and such of the items as sometimes a full bottle
needs to be billed because we can't use it after it cures or something, so it's entirely
situational".

So a person decides, line by line, when the visit is approved (the Visit Review page):

* **Per Dose**: the doses recorded at the item's **Price per Dose**, as one line.
* **Whole Container**: the containers opened (1 unless changed) at the item's selling price.
* **Don't Bill**: left off the invoice.

The choice starts from the item's **Maintenance Billing** default. A row nobody chose (a visit
approved on the desk form) bills that default. Invoices stay drafts, so a line can still be
changed before it goes out. Read by ``api/maintenance_workflow.create_sales_invoice`` (per
visit) and ``api/maintenance_billing`` (monthly), so both bill a chemical the same way.
"""

import frappe
from frappe import _

# frappe.utils is imported inside each function, so a bench-free suite whose stub lacks
# these helpers can still import the modules that import this one.

BILL_AS = ("Per Dose", "Whole Container", "Don't Bill")
#: What an item can default to: "Don't Bill" is a decision about a visit, not a product.
ITEM_DEFAULTS = ("Per Dose", "Whole Container")
DEFAULT = "Per Dose"


def item_terms(items):
	"""{item: {item_name, default, price_per_dose, container_price}} for these item codes.

	``container_price`` is the item's Standard Selling price (its stock unit is the container),
	the newest by creation when there are several, as ``frappe.db.get_value`` picked it before.
	Guarded: the two Item fields are custom fields, absent on a fresh install until fixtures sync.
	"""
	from frappe.utils import flt

	items = sorted({item for item in items if item})
	if not items:
		return {}
	fields = ["name", "item_name", "stock_uom"]
	if frappe.db.has_column("Item", "custom_maintenance_billing"):
		fields += ["custom_maintenance_billing", "custom_price_per_dose"]
	terms = {}
	for row in frappe.get_all("Item", filters={"name": ["in", items]}, fields=fields):
		default = row.get("custom_maintenance_billing")
		terms[row.name] = {
			"item_name": row.get("item_name") or row.name,
			"default": default if default in ITEM_DEFAULTS else DEFAULT,
			"price_per_dose": flt(row.get("custom_price_per_dose")),
			"container_price": 0.0,
			"stock_uom": (row.get("stock_uom") or "").strip().lower(),
		}
	for price in frappe.get_all(
		"Item Price",
		filters={"item_code": ["in", items], "price_list": "Standard Selling"},
		fields=["item_code", "price_list_rate"],
		order_by="creation asc",
	):
		if price.item_code in terms:
			terms[price.item_code]["container_price"] = flt(price.price_list_rate)
	# A chemical sold in sizes says which on the review page and the invoice (v1.712.0).
	from erpnext_enhancements.sapphire_maintenance.item_sizes import item_sizes, with_size

	for item, size in item_sizes(terms).items():
		terms[item]["item_name"] = with_size(terms[item]["item_name"], size["size"])
	return terms


def dose_price(row, term):
	"""What one dose of this row bills: the item's Price per Dose, else its selling price when the
	dose was recorded in the item's own stock unit (gallons of a gallon item), which is how it
	billed before v1.628.0. Zero when neither applies."""
	from frappe.utils import flt

	per = flt(term.get("price_per_dose"))
	if per:
		return per
	unit = (row.get("uom") or "").strip().lower()
	if unit and unit == term.get("stock_uom"):
		return flt(term.get("container_price"))
	return 0.0


def choice(row, terms):
	"""How a row bills: its own choice, else its item's default."""
	own = row.get("bill_as")
	if own in BILL_AS:
		return own
	return (terms.get(row.get("item")) or {}).get("default") or DEFAULT


def dose_text(row):
	"""What was recorded, as the technician counted it: "1 cap full", "0.5 gallon"."""
	from frappe.utils import flt

	qty = flt(row.get("qty"))
	number = f"{qty:g}"
	unit = (row.get("uom") or "").strip()
	return f"{number} {unit}".strip()


def billable_rows(rows):
	from frappe.utils import flt

	return [row for row in rows or [] if row.get("item") and flt(row.get("qty")) > 0]


def invoice_lines(rows, visit=None, terms=None):
	"""Sales Invoice item rows for a visit's chemicals, as chosen.

	A per-dose line is one line priced at the doses times the dose price (:func:`dose_price`),
	with the doses in its description: a dose ("cap full") is no unit of the item's, so a
	quantity in it would not convert. A line without a price says so, so the draft cannot pass
	as complete.

	Every line carries ``price_list_rate`` equal to its ``rate``. Without it ERPNext fills the
	list price from the price list on ``set_missing_values``, and ``calculate_item_rate`` then
	replaces a rate of 0 with it: a per-dose line with no dose price billed the whole container,
	and a priced one showed the container's list price less a made-up discount (review of
	v1.628.0, read in erpnext version-16 ``taxes_and_totals`` / ``accounts_controller``).
	"""
	from frappe.utils import flt, fmt_money

	rows = billable_rows(rows)
	terms = terms if terms is not None else item_terms(row.get("item") for row in rows)
	where = _(" on {0}").format(visit) if visit else ""
	lines = []
	for row in rows:
		how = choice(row, terms)
		if how == "Don't Bill":
			continue
		item = row.get("item")
		term = terms.get(item) or {"item_name": item, "price_per_dose": 0.0, "container_price": 0.0}
		if how == "Whole Container":
			count = flt(row.get("containers")) or 1
			rate = flt(term["container_price"])
			note = "" if rate else _(" (no selling price on the item)")
			lines.append(
				{
					"item_code": item,
					"qty": count,
					"rate": rate,
					"price_list_rate": rate,
					"description": _("{0}: whole container ({1} used{2}){3}").format(
						term["item_name"], dose_text(row), where, note
					),
				}
			)
		else:
			per = dose_price(row, term)
			rate = flt(per * flt(row.get("qty")), 2)
			note = "" if per else _(" (no Price per Dose on the item)")
			lines.append(
				{
					"item_code": item,
					"qty": 1,
					"rate": rate,
					"price_list_rate": rate,
					"description": _("{0}: {1}{2} at {3} per dose{4}").format(
						term["item_name"], dose_text(row), where, fmt_money(per), note
					),
				}
			)
	return lines


def describe_choices(rows, chosen, terms=None):
	"""One line for the visit's timeline: how each chemical was billed, as the reviewer chose."""
	rows = [row for row in rows or [] if row.name in chosen]
	terms = terms if terms is not None else item_terms(row.get("item") for row in rows)
	parts = []
	for row in rows:
		how, containers = chosen[row.name]
		name = (terms.get(row.get("item")) or {}).get("item_name") or row.get("item")
		if how == "Whole Container":
			parts.append(_("{0}: whole container x{1:g}").format(name, containers))
		elif how == "Per Dose":
			parts.append(_("{0}: per dose ({1})").format(name, dose_text(row)))
		else:
			parts.append(_("{0}: not billed").format(name))
	return _("Chemicals billed as: {0}.").format("; ".join(parts)) if parts else ""


def review_rows(doc):
	"""The visit's chemicals for the Visit Review page: what was used, its choice and prices."""
	from frappe.utils import flt

	rows = billable_rows(doc.get("consumables"))
	terms = item_terms(row.get("item") for row in rows)
	out = []
	for row in rows:
		term = terms.get(row.get("item")) or {}
		out.append(
			{
				"row": row.name,
				"item_name": term.get("item_name") or row.get("item"),
				"dose": dose_text(row),
				"bill_as": choice(row, terms),
				"containers": flt(row.get("containers")) or 1,
				"price_per_dose": dose_price(row, term),
				"container_price": flt(term.get("container_price")),
			}
		)
	return out


def checked_choices(rows, choices):
	"""The reviewer's choices, checked against the visit's own rows.

	Returns {row name: (bill_as, containers)}. A row that is not this visit's, a choice that is
	not one of the three, or a whole container with no count is refused rather than guessed.
	"""
	from frappe.utils import flt

	if isinstance(choices, str):
		choices = frappe.parse_json(choices) if choices.strip() else []
	names = {row.name for row in rows or []}
	checked = {}
	for entry in choices or []:
		name = entry.get("row")
		if name not in names:
			frappe.throw(_("That chemical is not on this visit. Reload and try again."))
		how = entry.get("bill_as")
		if how not in BILL_AS:
			frappe.throw(_("Choose how to bill each chemical: per dose, whole container or don't bill."))
		containers = flt(entry.get("containers")) if how == "Whole Container" else 0
		if how == "Whole Container" and containers <= 0:
			frappe.throw(_("Say how many containers to bill."))
		checked[name] = (how, containers)
	return checked

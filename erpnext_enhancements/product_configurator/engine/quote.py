# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Quote rules for a Product Configuration: pure, stdlib only, like the rest of ``engine``.

What a configured product looks like on a Quotation, and who the Quotation is for. The frappe side
(``product_configurator/quoting.py``) reads the records and writes the Quotation; every decision it
makes about the content is here, so the Desk button, the phone quick-quote page and the Triton tool
that come later all produce the same quote, and the bench-free suite can hold it.

* **One line format** (:func:`description_lines`): the part number, then one plain line per option
  ("E-Stop Mounting: Flush", "Pump Contactor Qty: 2"), then the additional item when there is one.
  Every option is listed, zeros included, so two quotes for the same product read the same way and
  a missing line means a missing option rather than a zero nobody wrote down. QuickBooks quotes
  carried the configuration as free text in at least four shapes; this is the one shape now.
  The same text becomes the configured Item's own description, so a line somebody adds to a
  Quotation or Sales Order by hand reads the same as one the configurator made.
* **Who it is for** (:func:`quotation_party`): the configuration's Customer first, else the
  Opportunity's own party (a Lead or a Prospect is a valid Quotation party in ERPNext), else the
  configuration's Lead.
* **The line** (:func:`quotation_line`) carries ``price_list_rate`` equal to ``rate``: ERPNext v16
  re-prices a line whose list rate is missing and books the gap as an invented discount
  (``taxes_and_totals.calculate_item_rate``), so the configured price would not survive the save.
"""

from html import escape

#: The Quotation ``quotation_to`` values a configuration can quote, in ERPNext v16's own spelling.
PARTY_TYPES = ("Customer", "Lead", "Prospect")
#: The Quotation field naming the deal a configurator quote belongs to (see :func:`quotation_header`).
SOURCE_OPPORTUNITY = "custom_source_opportunity"


def _qty_text(value):
	try:
		number = float(value or 0)
	except (TypeError, ValueError):
		return str(value)
	return str(int(number)) if number == int(number) else f"{number:g}"


def description_lines(part_number, options, additional_description=None, additional_cost=0):
	"""The configured product's description, one plain line each.

	``options`` are the configuration's option rows as dicts (``option_type``, ``option_key``,
	``option_label``, ``choice_label``, ``choice_code``, ``selected``, ``qty``), in the product's
	order. A Choice group prints once, as its selected choice; a group with nothing selected prints
	nothing (the controller refuses that configuration, so it only happens on a broken row). A
	Quantity option always prints, zero included. Base rows are never in the table.
	"""
	lines = [str(part_number or "").strip()]
	choices = {}
	order = []
	for row in options or []:
		kind = row.get("option_type")
		label = str(row.get("option_label") or row.get("option_key") or "").strip()
		if kind == "Choice":
			key = row.get("option_key") or label
			if key not in choices:
				choices[key] = None
				order.append(("choice", key, label))
			if row.get("selected"):
				choice = row.get("choice_label") or row.get("choice_code") or ""
				choices[key] = (label, str(choice).strip())
		elif kind == "Quantity":
			order.append(("qty", label, _qty_text(row.get("qty"))))
	for kind, first, second in order:
		if kind == "choice":
			picked = choices.get(first)
			if picked:
				lines.append(f"{picked[0]}: {picked[1]}")
		else:
			lines.append(f"{first}: {second}")
	try:
		extra = float(additional_cost or 0)
	except (TypeError, ValueError):
		extra = 0
	if extra:
		lines.append(f"Additional: {str(additional_description or '').strip() or 'see configuration'}")
	return [line for line in lines if line]


def description_html(lines):
	"""``lines`` as the Text Editor HTML a Quotation line stores: one escaped paragraph each.

	Paragraphs rather than newlines: the Desk's editor runs plain newlines together, and the
	customer-facing print (``print_style.rich_text``) passes markup through as it is.
	"""
	return "".join(f"<p>{escape(str(line), quote=False)}</p>" for line in lines or [] if str(line))


def quotation_party(customer=None, lead=None, opportunity_from=None, opportunity_party=None):
	"""``(quotation_to, party_name)`` for a configuration, or ``(None, None)`` when it has no party.

	The configuration's Customer wins: it is the most specific thing anybody set, and a deal that
	started on a Lead whose Customer now exists should be quoted to the Customer. Then the
	Opportunity's own party, a Lead or a Prospect included. Then the configuration's Lead.
	"""
	if customer:
		return "Customer", customer
	if opportunity_party and opportunity_from in PARTY_TYPES:
		return opportunity_from, opportunity_party
	if lead:
		return "Lead", lead
	return None, None


def uses_opportunity_mapping(opportunity, quotation_to, party_name, opportunity_from, opportunity_party):
	"""Whether the Quotation starts from ERPNext's own Opportunity -> Quotation mapping.

	Only when it is for the Opportunity's own party: the mapping copies the party's contact,
	addresses and the default taxes from the deal, and all of that would describe somebody else if
	the configuration's Customer is not the deal's party.
	"""
	return bool(
		opportunity and quotation_to == opportunity_from and party_name and party_name == opportunity_party
	)


def quotation_line(item_code, rate, description, configuration, warehouse=None, uom="Nos", qty=1):
	"""The Quotation Item row for one configured product.

	``price_list_rate`` equals ``rate`` (see the module docstring). ``warehouse`` is where finished
	units are kept: ERPNext refuses to save a Sales Order with a stock item and no warehouse, and
	Make Sales Order copies the line's warehouse across.
	"""
	price = round(float(rate or 0), 2)
	row = {
		"item_code": item_code,
		"qty": qty,
		"uom": uom,
		"rate": price,
		"price_list_rate": price,
		"description": description,
		"custom_product_configuration": configuration,
	}
	if warehouse:
		row["warehouse"] = warehouse
	return row


def quotation_header(
	quotation_to, party_name, company, opportunity=None, price_list=None, transaction_date=None
):
	"""The Quotation's own fields that the configuration decides (the rest is ERPNext's defaults).

	``ignore_pricing_rule`` keeps the configured price: a pricing rule's discount would otherwise
	rewrite it on save. Trade and distributor prices, when they come, are price lists, not rules.

	**The deal is linked by ``custom_source_opportunity``, and ERPNext's ``opportunity`` is cleared**
	(v1.825.0), on purpose. A Quotation linked through ERPNext's own field (or an item's
	``prevdoc_docname``) drives the deal's status: submitting it writes ``Quotation``, its Sales Order
	``Converted``, declaring it lost ``Lost``, none through validation; and once it is ordered,
	``Opportunity.validate_status_against_quotations`` refuses every other status, so the deal could
	never be moved to Closed Won again (ERPNext v16.54.0, ``opportunity.py``). ``Quotation`` and
	``Converted`` are not stages on this site at all. ERPNext's Opportunity mapping sets
	``opportunity``, so it is set to ``None`` here rather than left out.
	"""
	header = {
		"quotation_to": quotation_to,
		"party_name": party_name,
		"company": company,
		"ignore_pricing_rule": 1,
		"opportunity": None,
	}
	if opportunity:
		header[SOURCE_OPPORTUNITY] = opportunity
	if price_list:
		header["selling_price_list"] = price_list
	if transaction_date:
		header["transaction_date"] = transaction_date
	return header


def apply_selections(rows, selections):
	"""Set option rows from ``{option_key: choice_code | qty}``; return the keys that matched nothing.

	For the endpoint's callers that pick options without a form (the phone page, the Triton tool).
	A Choice key selects the row whose ``choice_code`` matches and clears its siblings; a code that
	matches no row leaves the group as it was and is reported. A Quantity key sets ``qty``; the
	controller's own validation still checks the range on save. ``rows`` are dicts or row objects
	with item access, changed in place.
	"""
	unknown = []
	for key, value in (selections or {}).items():
		group = [row for row in rows or [] if row.get("option_key") == key]
		if not group:
			unknown.append(key)
			continue
		if group[0].get("option_type") == "Choice":
			code = str(value)
			if not any(str(row.get("choice_code") or "") == code for row in group):
				unknown.append(key)
				continue
			for row in group:
				row["selected"] = 1 if str(row.get("choice_code") or "") == code else 0
		else:
			try:
				qty = int(value)
			except (TypeError, ValueError):
				unknown.append(key)
				continue
			for row in group:
				row["qty"] = qty
	return unknown


#: What each permission the quote needs lets a person do, and a role that grants it on a stock
#: ERPNext site. The words are what the refusal says; the role is only a pointer for whoever fixes
#: it, since a site can grant permissions through any role it likes.
PERMISSION_WORDS = {
	("Supplier", "create"): ("add suppliers", "Purchase Master Manager"),
	("Item", "create"): ("create items", "Item Manager"),
	("BOM", "create"): ("create bills of materials", "Manufacturing User"),
	("BOM", "submit"): ("submit bills of materials", "Manufacturing User"),
	("Item Price", "create"): ("set item prices", "Sales Master Manager"),
	("Quotation", "create"): ("create quotations", "Sales User"),
}


def missing_permission_message(missing):
	"""A refusal in plain words for the ``(doctype, ptype)`` pairs a person lacks.

	"You can't create bills of materials or submit bills of materials yet, and making this quote
	needs both. Ask a System Manager to give you the Manufacturing User role." Pairs not in
	:data:`PERMISSION_WORDS` read as "<ptype> <doctype>".
	"""
	words, roles = [], []
	for doctype, ptype in missing or []:
		said, role = PERMISSION_WORDS.get((doctype, ptype), (f"{ptype} {doctype}", None))
		if said not in words:
			words.append(said)
		if role and role not in roles:
			roles.append(role)
	if not words:
		return ""
	if len(words) == 1:
		what = words[0]
	else:
		what = ", ".join(words[:-1]) + " or " + words[-1]
	need = "it" if len(words) == 1 else ("both" if len(words) == 2 else "all of them")
	message = f"You can't {what} yet, and making this quote needs {need}."
	if roles:
		named = roles[0] if len(roles) == 1 else ", ".join(roles[:-1]) + " and " + roles[-1]
		plural = "role" if len(roles) == 1 else "roles"
		message += f" Ask a System Manager to give you the {named} {plural}."
	else:
		message += " Ask a System Manager for the permission."
	return message

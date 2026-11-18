# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Is this Opportunity a product sale? A product sale gets no project (v1.825.0).

Decision 2026-11-18, pick 1: for the E-Stop and other standard products **the Sales Order is the
record**; there is no project per unit. Custom-engineered control panels keep a Project of type
Products. Until then every E-Stop sale got a project, and one got the 40-task Design template.

**The rule** (:func:`is_product_sale`): a deal is a product sale only when **a person has said so**,
by ticking ``Opportunity.custom_product_sale`` ("Product Sale (No Project)"), **and nothing linked
to the deal says otherwise**: every live (draft or submitted) Quotation linked to it is a product
sale that skips a project by ``product_orders.skips_project`` (#1464's shared rule: every line a
product Item or an incidental freight or delivery line, at least one product, no control panel).
When it is unclear, the deal is a job and keeps its project.

**Why a person's tick and not the documents alone.** The first draft of this rule (never merged)
read a deal as a product sale when it had a Product Configuration or a configured quote line. A manager
review traced what that does on prod:

* The QuickBooks estimate mapper never sets ``Quotation.opportunity``, and none of prod's 705
  Quotations carries one. So the only Quotations the rule could ever see are the ones the
  configurator makes, and "product sale" came to mean "has a configuration".
* A rep who quotes an E-Stop add-on on a fountain deal, or presses Quote by mistake, would have
  cost that deal its project, its hand-off and its place in every backlog, with no way back.

The documents cannot settle it, because the deal's real quote may live in QuickBooks where nothing
links it to the deal. So the configurator only **suggests** the tick (:func:`suggests_product_sale`:
a product on the deal and nothing contrary), the Closed Won prompt and Create Quotation ask, and the
person decides. Documents can only overrule the tick, never stand in for it: one linked Quotation
with a fountain on it makes the deal a job again, tick or no tick.

**The escape.** Untick the field, or press **Create Project Anyway** on the Hand-Off Process tab,
which unticks it and creates the project (``api.enqueue_project_creation(create_anyway=1)``).

**Where it is read.** ``handoff.stamp_handoff_gate`` (no hand-off SLA), ``project_prompt`` (no
"Create project now?"), ``handoff.handoff_state`` (the tab's quiet line and its buttons),
``api.enqueue_project_creation``, and the three won-without-project counts: the daily reminder
(``status_alerts``), the Sales Dashboard's hand-off backlog and the Hand-Off Backlog KPI.

**Never raises.** It runs inside the Opportunity's save chain, including ERPNext's own test bootstrap
before this app's fields exist. Any failure reads as "not a product sale": a job, which is what the
site did before this rule existed. The pure rule has no frappe import, so the bench-free suite runs
it directly. What counts as a product item or a product document is ``product_orders``'s, shared
with the Products tab, so the two never disagree about a line.
"""

MARKER = "custom_product_sale"


def is_product_sale(marked, documents):
	"""The rule, on facts already read.

	``marked``: the person's tick. ``documents``: one bool per live Quotation linked to the deal,
	True when it is a product document. No tick, no product sale; one non-product document, a job.
	"""
	return bool(marked) and all(documents or [])


def suggests_product_sale(marked, has_configuration, documents):
	"""Whether to ask the person to tick it: a product on the deal and nothing contrary.

	Never once it is ticked (there is nothing to ask), and never on a deal with a non-product
	document linked. A configuration with no quote yet is enough to ask, and only to ask.
	"""
	if marked:
		return False
	docs = list(documents or [])
	if not all(docs):
		return False
	return bool(has_configuration) or bool(docs)


def document_skips_project(lines):
	"""Whether one Quotation's lines are a product sale with no project: ``product_orders``'s rule.

	``product_orders.skips_project`` (#1464, shared with the Products tab, never copied here): every
	line a product Item or an incidental one (freight, delivery), at least one product, and no
	control panel among them (decision pick 1: custom-engineered panels keep a Project). A hand-made
	``PDT-0040`` and a configured E-Stop get the same answer.
	"""
	from erpnext_enhancements import product_orders

	return product_orders.skips_project(lines)


def deal_facts(opportunities):
	"""``{name: {"marked", "has_configuration", "documents"}}`` for saved Opportunities.

	Three reads whatever the count: the ticks, the linked configurations, and the lines of the live
	header-linked Quotations (the configurator and ERPNext's own Create > Quotation both set
	``Quotation.opportunity``; QuickBooks quotes never do). Raises on a database problem; the
	callers below are the ones that must not.
	"""
	import frappe

	names = sorted({name for name in opportunities or [] if name})
	facts = {name: {"marked": False, "has_configuration": False, "documents": []} for name in names}
	if not names:
		return facts
	if frappe.db.has_column("Opportunity", MARKER):
		for name in frappe.get_all(
			"Opportunity", filters={"name": ("in", names), MARKER: 1}, pluck="name", limit_page_length=0
		):
			if name in facts:
				facts[name]["marked"] = True
	if frappe.db.has_column("Product Configuration", "opportunity"):
		for name in frappe.get_all(
			"Product Configuration",
			filters={"opportunity": ("in", names)},
			pluck="opportunity",
			limit_page_length=0,
		):
			if name in facts:
				facts[name]["has_configuration"] = True
	marked = (
		"i.custom_source_configuration"
		if frappe.db.has_column("Item", "custom_source_configuration")
		else "''"
	)
	rows = frappe.db.sql(
		f"""
		select q.opportunity as opportunity, q.name as quotation, qi.item_code as item_code,
			qi.item_name as item_name, {marked} as source_configuration
		from `tabQuotation Item` qi
		join `tabQuotation` q on q.name = qi.parent and qi.parenttype = 'Quotation'
		left join `tabItem` i on i.name = qi.item_code
		where q.opportunity in %(names)s and q.docstatus < 2
		""",
		{"names": tuple(names)},
		as_dict=True,
	)
	by_quotation = {}
	for row in rows or []:
		by_quotation.setdefault((row.get("opportunity"), row.get("quotation")), []).append(row)
	for (opportunity, _quotation), lines in by_quotation.items():
		if opportunity in facts:
			facts[opportunity]["documents"].append(document_skips_project(lines))
	return facts


def product_sale_names(opportunities):
	"""The product sales among ``opportunities`` (names). Never raises; a failure reads as none."""
	names = [name for name in opportunities or [] if name]
	if not names:
		return set()
	try:
		facts = deal_facts(names)
		return {name for name, f in facts.items() if is_product_sale(f["marked"], f["documents"])}
	except Exception:
		return set()


def is_product_opportunity(opportunity):
	"""Whether the saved Opportunity ``opportunity`` is a product sale. Never raises."""
	return bool(opportunity) and opportunity in product_sale_names([opportunity])


def is_product_sale_doc(doc):
	"""The rule for an Opportunity being saved: its own tick, which the database may not hold yet
	(a person can tick it in the same save that marks the deal Closed Won), and its linked
	documents. Never raises; a new record is never a product sale (nothing is linked to it yet)."""
	try:
		if doc.is_new() or not doc.get(MARKER):
			return False
		name = getattr(doc, "name", None)
		documents = deal_facts([name]).get(name, {}).get("documents", [])
		return is_product_sale(True, documents)
	except Exception:
		return False


def deal_state(opportunity):
	"""What the hand-off and the quote need to know about one deal. Never raises.

	``marked``: the tick. ``is_product_sale``: the rule. ``conflict``: ticked, but a linked Quotation
	carries something else, so it is treated as a job. ``suggested``: worth asking the person.
	"""
	state = {"marked": False, "is_product_sale": False, "conflict": False, "suggested": False}
	if not opportunity:
		return state
	try:
		f = deal_facts([opportunity]).get(opportunity)
	except Exception:
		return state
	if not f:
		return state
	product = is_product_sale(f["marked"], f["documents"])
	state.update(
		marked=f["marked"],
		is_product_sale=product,
		conflict=bool(f["marked"] and not product),
		suggested=suggests_product_sale(f["marked"], f["has_configuration"], f["documents"]),
	)
	return state

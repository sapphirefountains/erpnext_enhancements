# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Is this Opportunity a product sale? A product sale gets no project (v1.827.0).

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
#: How a configurator Quotation names its deal (v1.827.0): not ERPNext's ``Quotation.opportunity``,
#: so ERPNext never moves or freezes the deal's stage from it (``quoting.py`` says why).
SOURCE_OPPORTUNITY = "custom_source_opportunity"


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
	Quotations linked to the deal. A Quotation is linked by ``custom_source_opportunity`` (how the
	configurator links its quotes, v1.827.0) or by ERPNext's own ``Quotation.opportunity`` (what
	Create > Quotation sets); QuickBooks quotes set neither. Raises on a database problem; the callers
	below are the ones that must not.
	"""
	import frappe

	names = sorted({name for name in opportunities or [] if name})
	facts = {
		name: {"marked": False, "has_configuration": False, "documents": [], "lines": []} for name in names
	}
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
	own_link = frappe.db.has_column("Quotation", SOURCE_OPPORTUNITY)
	source = f"q.{SOURCE_OPPORTUNITY}" if own_link else "''"
	either = (
		f"(q.opportunity in %(names)s or q.{SOURCE_OPPORTUNITY} in %(names)s)"
		if own_link
		else ("q.opportunity in %(names)s")
	)
	rows = frappe.db.sql(
		f"""
		select q.opportunity as opportunity, {source} as source_opportunity, q.name as quotation,
			qi.item_code as item_code, qi.item_name as item_name, {marked} as source_configuration
		from `tabQuotation Item` qi
		join `tabQuotation` q on q.name = qi.parent and qi.parenttype = 'Quotation'
		left join `tabItem` i on i.name = qi.item_code
		where {either} and q.docstatus < 2
		""",
		{"names": tuple(names)},
		as_dict=True,
	)
	by_quotation = {}
	for row in rows or []:
		deal = row.get("source_opportunity") or row.get("opportunity")
		by_quotation.setdefault((deal, row.get("quotation")), []).append(row)
	for (opportunity, _quotation), lines in by_quotation.items():
		if opportunity in facts:
			facts[opportunity]["documents"].append(document_skips_project(lines))
			facts[opportunity]["lines"].extend(lines)
	return facts


#: The value streams a Project's type is chosen from, highest priority first (the hand-off's order).
STREAM_PRIORITY = ("Design", "Build", "Service", "Events")
PRODUCTS_TYPE = "Products"


def project_type_for(value_streams, quoted_lines):
	"""The Project Type a deal's project gets (v1.827.0).

	**Products** when the deal's live linked quote lines, taken together, are a product order that
	needs a project: every line a product or incidental, and a control panel among them
	(``product_orders.classify_lines`` ``needs_project``). Decision pick 1: "custom-engineered control
	panels keep a Project of type Products", and the Products tab lists control-panel projects by
	that type. Otherwise the value-stream choice the hand-off always made (Design, Build, Service,
	Events), so a fountain job is typed as before, and ``None`` when no stream matches. A fountain
	quote beside the panel makes the lines a job, which keeps the stream choice.
	"""
	if quoted_lines:
		from erpnext_enhancements import product_orders

		result = product_orders.classify_lines(quoted_lines)
		if result["is_product"] and result["needs_project"]:
			return PRODUCTS_TYPE
	streams = set(value_streams or [])
	return next((stream for stream in STREAM_PRIORITY if stream in streams), None)


def project_type_for_deal(opportunity, value_streams):
	"""``project_type_for`` with the deal's quote lines read in, for the hand-off job. Never raises.

	A read that fails, or a site without the Products Project Type, falls back to the stream choice.
	"""
	try:
		lines = deal_facts([opportunity]).get(opportunity, {}).get("lines", [])
	except Exception:
		lines = []
	chosen = project_type_for(value_streams, lines)
	if chosen == PRODUCTS_TYPE:
		try:
			import frappe

			if not frappe.db.exists("Project Type", PRODUCTS_TYPE):
				chosen = project_type_for(value_streams, [])
		except Exception:
			chosen = project_type_for(value_streams, [])
	return chosen


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


def documents_agree(opportunity):
	"""No live Quotation linked to the deal disagrees with a product sale. Never raises; False on a
	failed read (a job keeps its gate)."""
	try:
		return all(deal_facts([opportunity]).get(opportunity, {}).get("documents", []))
	except Exception:
		return False


def mirror_deal_link(doc, method=None):
	"""Quotation ``validate``: a quote linked ERPNext's way also names its deal in our field (v1.827.0).

	Configurator quotes name their deal only in ``custom_source_opportunity`` (``Quotation.opportunity``
	would let ERPNext move and freeze the deal's stage). ERPNext's own Create > Quotation still sets
	``opportunity``. Copying it across means one field finds every quote of a deal, which is what the
	Opportunity's Connections dashboard needs (:func:`add_dashboard_links`: frappe links a dashboard
	doctype through one field). Fills a blank only, never overwrites. Never raises.
	"""
	try:
		if (
			doc.get("opportunity")
			and not doc.get(SOURCE_OPPORTUNITY)
			and doc.meta.get_field(SOURCE_OPPORTUNITY)
		):
			doc.set(SOURCE_OPPORTUNITY, doc.get("opportunity"))
	except Exception:
		return


def add_dashboard_links(data):
	"""The Opportunity's Connections (v1.827.0): Quotations through ``custom_source_opportunity``, so a
	configurator quote shows on its deal (its ``opportunity`` is empty), and the deal's Product
	Configurations (their own ``opportunity`` field, the dashboard's default). Each only once the field
	exists on the site; a reader that fails leaves ERPNext's dashboard as it was."""
	try:
		import frappe

		if frappe.get_meta("Quotation").has_field(SOURCE_OPPORTUNITY):
			data.setdefault("non_standard_fieldnames", {})["Quotation"] = SOURCE_OPPORTUNITY
		if frappe.get_meta("Product Configuration").has_field("opportunity"):
			data.setdefault("transactions", []).append(
				{"label": frappe._("Products"), "items": ["Product Configuration"]}
			)
	except Exception:
		pass
	return data


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

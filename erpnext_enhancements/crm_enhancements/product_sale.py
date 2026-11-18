# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Is this Opportunity a product sale? A product sale gets no project (v1.824.0).

Decision 2026-11-18: for a product like the Stillwater E-Stop, **the Sales Order is the record**;
there is no project per unit. Until then every E-Stop sale got one, and one got the 40-task Design
template (PRJ-00776, all 40 tasks Overdue on a Completed project). Custom-engineered control panels
keep a project.

**The rule** (:func:`is_product_sale`): a deal is a product sale when it has a configured product,
meaning a Product Configuration linked to it or a live Quotation line whose Item the configurator
made, **and** every line on its live Quotations is such an Item. One line of anything else (a
fountain, a custom control panel, labor) makes it a job, and a job keeps its project. "Live" is a
draft or submitted Quotation; a canceled one says nothing about the deal.

**Why not the Products value stream.** It was the obvious candidate, and prod says no (read
2026-11-18). 14 Opportunities carry Products. The E-Stop deals for one pool builder carry Design +
Build + Products, because the old process put each unit through the Design template. The
control-panel deals ("Control Panel for Pool & Splash Pad", "Hotel Pool Controller") carry Products
too, and those are exactly the ones that keep a project. So the stream would skip projects on panel
jobs and keep them on E-Stops: wrong in both directions. It is a tag a person picks; a configured
Item on the quote is a fact about what was sold. (``quoting`` still adds the Products stream to a
deal it quotes, so the tag follows the fact.)

**Where it is read.** ``handoff.stamp_handoff_gate`` (no hand-off SLA for a product sale),
``project_prompt.prompt_create_project_on_won`` (no "Create project now?"),
``handoff.handoff_state`` (the form and the prompt show a quiet line instead of a next step), and
``api.enqueue_project_creation`` (refuses to queue one, in plain words).

**Never raises** (:func:`is_product_opportunity`): it runs inside the Opportunity's save chain,
including during ERPNext's own test bootstrap before this app's fields exist, and a hook that raises
there blocks every deal on the site. Any failure reads as "not a product sale", which is what the
site did before this rule existed. The pure rule has no frappe import, so the bench-free suite runs
it directly.
"""


def is_product_sale(has_configuration, quoted_lines):
	"""The rule, on facts already read.

	``has_configuration``: a Product Configuration is linked to the deal. ``quoted_lines``: one bool
	per line on the deal's live Quotations, True when the line's Item was made by the configurator.
	"""
	lines = list(quoted_lines or [])
	if any(not configured for configured in lines):
		return False
	return bool(has_configuration) or any(lines)


#: A Quotation line's Item was made by the configurator (``Item.custom_source_configuration``).
_CONFIGURED = "ifnull(i.custom_source_configuration, '') != ''"


def product_sale_names(opportunities):
	"""The product sales among ``opportunities`` (names), in two queries whatever the count.

	Header-linked Quotations only: the configurator and ERPNext's own Create > Quotation both set
	``Quotation.opportunity``. Never raises; a failure reads as "none of them".
	"""
	names = sorted({name for name in opportunities or [] if name})
	if not names:
		return set()
	try:
		import frappe

		with_configuration = set()
		if frappe.db.has_column("Product Configuration", "opportunity"):
			with_configuration = set(
				frappe.get_all(
					"Product Configuration",
					filters={"opportunity": ("in", names)},
					pluck="opportunity",
					limit_page_length=0,
				)
			)
		configured = _CONFIGURED if frappe.db.has_column("Item", "custom_source_configuration") else "0"
		rows = frappe.db.sql(
			f"""
			select q.opportunity as opportunity, {configured} as configured
			from `tabQuotation Item` qi
			join `tabQuotation` q on q.name = qi.parent and qi.parenttype = 'Quotation'
			left join `tabItem` i on i.name = qi.item_code
			where q.opportunity in %(names)s and q.docstatus < 2
			""",
			{"names": tuple(names)},
			as_dict=True,
		)
		lines = {}
		for row in rows or []:
			lines.setdefault(row.get("opportunity"), []).append(bool(int(row.get("configured") or 0)))
		return {name for name in names if is_product_sale(name in with_configuration, lines.get(name, []))}
	except Exception:
		return set()


def is_product_opportunity(opportunity):
	"""Whether the saved Opportunity ``opportunity`` is a product sale. Never raises."""
	return bool(opportunity) and opportunity in product_sale_names([opportunity])


def product_sale_sql(name_column):
	"""The rule as a SQL condition on the Opportunity whose name is ``name_column``.

	For a count that cannot take a Python filter (the KPI snapshot's Hand-Off Backlog): use it as
	``and not (<this>)``. ``name_column`` is SQL the caller writes, never input. ``"0"`` (nothing is
	a product sale) while either column the rule reads is missing, or on any error.
	"""
	try:
		import frappe

		if not (
			frappe.db.has_column("Product Configuration", "opportunity")
			and frappe.db.has_column("Item", "custom_source_configuration")
		):
			return "0"
	except Exception:
		return "0"
	live_lines = (
		"select 1 from `tabQuotation Item` qi "
		"join `tabQuotation` q on q.name = qi.parent and qi.parenttype = 'Quotation' "
		"left join `tabItem` i on i.name = qi.item_code "
		f"where q.opportunity = {name_column} and q.docstatus < 2"
	)
	return (
		f"((exists (select 1 from `tabProduct Configuration` pc where pc.opportunity = {name_column})"
		f" or exists ({live_lines} and {_CONFIGURED}))"
		f" and not exists ({live_lines} and not ({_CONFIGURED})))"
	)

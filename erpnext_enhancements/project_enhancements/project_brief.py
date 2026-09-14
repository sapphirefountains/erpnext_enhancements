"""Type-specific detail for the printable Project Brief.

The brief (``doctype/project/project.py::get_project_brief_data``, rendered by
``public/js/project_enhancements/project_brief.js``) was one fixed sheet for every
job: the paper template Sapphire has always printed. A Design job and an Events
job got the same page, so the dates that decide an Events job -- when the truck
leaves, when the fountain has to be running, when it comes back -- were not on it,
and neither were the design phase fees, the maintenance visit frequency, or the
equipment list.

This module supplies the half of the brief that depends on what kind of work the
job is. It answers: *given this Project, which of Sapphire's lines of work does it
involve, and what does each need on the printed sheet?*

Three things are worth knowing before changing it.

**The kind of work is not ``project_type`` alone.** ``project_type`` (relabelled
"Project Stage" in the Desk) is a Link to Project Type and holds one value;
``custom_value_stream`` is a Table MultiSelect and holds several. They disagree,
and neither is redundant:

* **Products is a value stream and has never been a Project Type.** The
  ``seed_delivery_and_products_categories`` patch created the Project Type
  ``Delivery`` but only the *value streams* ``Delivery`` and ``Products`` -- read
  its docstring, the asymmetry is deliberate. All seven Products jobs on prod are
  ``project_type = "Design"``. Keyed on ``project_type`` alone a Products brief
  could not exist at all.
* **A job routinely spans streams.** On prod 21 jobs are stage Design carrying a
  Build stream and 6 are the reverse; 7 are stage Design carrying Products.
  Picking one value would silently drop the other half of the brief.

So the brief renders a section per *applicable* stream -- the union of
``project_type`` and the value streams, narrowed to the five that have
type-specific content -- and leads with ``project_type``, because that is the
stage the project itself declares.

**Blocks are data, not markup.** Each section is a list of blocks in one of four
shapes (``fields``, ``list``, ``table``, ``text``) and the client renders them.
Server-built HTML would have to escape itself, would fork the print stylesheet,
and would format dates and currency without the reader's own settings.

There are two kinds of empty, and :func:`_fields_block` keeps them apart. A block
of the **Project's own** fields renders even when every one is blank, because the
printed brief is a *fillable* form -- an Events sheet with four blank date slots is
the sheet somebody writes the setup time onto, exactly as the paper template did
for the fields ERPNext has no source for. A block of a **linked document's** fields
disappears when that document is not there: no ``rental`` contract means this job
has no rental agreement, and nine blank currency lines under "Rental Terms" would
invent a relationship rather than leave a gap in a real one. ``list``, ``table``
and ``text`` blocks always drop when empty: blank rows for equipment nobody has
listed are noise, not a form.

**A contract is matched to the section by template, with no fallback.** Project
Contract is one doctype carrying every template's fields -- design fees, rental
fees, maintenance terms, construction dates -- and only the fields of the template
it was raised from are filled. Pointing the Events section at a ``maintenance``
contract would print a "Rental Terms" heading over that contract's zeros. So each
section names the templates it can read (:data:`CONTRACT_TEMPLATES`) and takes no
other; unmatched, there is no contract, and by the rule above the block does not
render. Note that this is live today rather than theoretical: all 16 Project
Contracts on prod are ``maintenance``.

Cross-doctype lookups (Project Contract, the maintenance agreement, purchase
orders, product configurations, invoices) are gated on the caller's read
permission for that doctype and swallow their own errors. A brief is a read-only
convenience: better a section short than a job that will not print.
"""

import frappe
from frappe.utils import flt, strip_html

#: The streams that have type-specific brief content, in the order a brief
#: spanning several should read. Value streams outside this tuple (``Delivery``)
#: and internal project types (``Internal``, ``Overhead``, ``Other`` ...)
#: contribute no section, and the brief renders exactly as it did before.
BRIEF_TYPES = ("Design", "Build", "Products", "Service", "Events")

#: The Project child tables holding each stream's scope, as ``(requests table,
#: requests column, deliverables table, deliverables column)``.
#:
#: Events reads ``custom_rent_*``: WI-065 renamed the *labels* from Rent to Events
#: but left the fieldnames, and renaming them now would be a fixture change plus a
#: data migration across four child doctypes for no behaviour. Products has no
#: scope tables -- it has never had them -- so it is absent here rather than
#: mapped to empty strings.
SCOPE_TABLES = {
	"Design": (
		"custom_design_customer_requests",
		"design_customer_requests",
		"custom_design_deliverables",
		"design_deliverables",
	),
	"Build": (
		"custom_build_customer_requests",
		"build_customer_requests",
		"custom_build_deliverables",
		"build_deliverables",
	),
	"Service": (
		"custom_service_customer_requests",
		"service_customer_requests",
		"custom_service_deliverables",
		"service_deliverables",
	),
	"Events": (
		"custom_rent_customer_requests",
		"rent_customer_requests",
		"custom_rent_deliverables",
		"rent_deliverables",
	),
}

#: Which Contract Template each section may read a Project Contract from, best
#: first. See the module docstring for why there is no catch-all fallback.
CONTRACT_TEMPLATES = {
	"Design": ("owner", "architect", "sow"),
	"Build": ("owner", "sow", "msa"),
	"Products": ("sow", "owner"),
	"Service": ("maintenance",),
	"Events": ("rental",),
}

#: Purchase Orders in these states have nothing left to arrive, so they are not
#: "open" on a Build brief. Deliberately the same rule as
#: ``procurement_project.get_receivable_purchase_orders`` -- status **and**
#: ``per_received``, because a fully-received order sits at status ``To Bill``,
#: which passes a status test on its own. PRJ-00759 has three of them.
#:
#: The Products section does *not* apply this: it asks what was bought for the
#: job, and a delivered order is the best possible answer to that.
SETTLED_PO_STATUSES = ("Closed", "Completed", "Delivered")

#: Cap on rows in any one table block. A brief is a sheet of paper, not a report;
#: a truncation is reported inside the block so nobody mistakes what they are
#: holding for the whole list.
MAX_TABLE_ROWS = 20


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def brief_sections(doc):
	"""Every type-specific section that applies to ``doc``, in reading order.

	Args:
	    doc (Document): the Project, already loaded and permission-checked by the
	        caller (``get_project_brief_data``).

	Returns:
	    list[dict]: ``{"type", "title", "blocks"}`` per applicable stream. Empty
	    for an Internal/Overhead/untyped job -- which is what leaves the brief
	    looking exactly as it did before this module existed.
	"""
	builders = {
		"Design": _design_blocks,
		"Build": _build_blocks,
		"Products": _products_blocks,
		"Service": _service_blocks,
		"Events": _events_blocks,
	}

	sections = []
	for stream in applicable_types(doc):
		blocks = [block for block in builders[stream](doc) if block]
		if blocks:
			sections.append({"type": stream, "title": stream, "blocks": blocks})
	return sections


def applicable_types(doc):
	"""The streams this Project involves, ``project_type`` first.

	The union of the single ``project_type`` and the many ``custom_value_stream``
	rows, narrowed to :data:`BRIEF_TYPES`. See the module docstring for why both
	sources are needed -- in short, Products only ever appears as a value stream,
	and a Design job frequently carries a Build stream too.
	"""
	ordered = []

	stage = (doc.get("project_type") or "").strip()
	if stage in BRIEF_TYPES:
		ordered.append(stage)

	streams = set()
	for row in doc.get("custom_value_stream") or []:
		# The child doctype carries two Link fields to Value Streams -- the
		# original `value_stream` and a later `value_streams`. Every live row uses
		# the first; read both so a row written through the other never vanishes.
		value = (row.get("value_stream") or row.get("value_streams") or "").strip()
		if value:
			streams.add(value)

	for stream in BRIEF_TYPES:
		if stream in streams and stream not in ordered:
			ordered.append(stream)

	return ordered


# ---------------------------------------------------------------------------
# Block constructors
# ---------------------------------------------------------------------------


def _row(label, value, fmt="text", note=None):
	"""One label/value line of a ``fields`` block.

	``value`` is passed through unformatted and ``fmt`` tells the client how to
	render it, so dates and currency come out in the reader's own settings rather
	than the server's. A falsy value renders as the template's blank slot.
	"""
	return {"label": label, "value": value, "format": fmt, "note": note or ""}


def _fields_block(title, rows, fillable=True):
	"""A label/value block.

	``fillable`` draws the line between the two kinds of emptiness, and it is the
	whole reason this is a parameter rather than a constant:

	* **The Project's own fields** (``fillable=True``, the default) render even
	  when every one is blank. The job *has* a setup time; nobody has typed it
	  yet. A blank slot is the printed original's own idiom -- somebody writes it
	  on the sheet.
	* **A linked document's fields** (``fillable=False``) vanish when the
	  document is not there. No ``rental`` Project Contract means this job has no
	  rental agreement at all, and printing nine blank currency lines under
	  "Rental Terms" would invent a relationship rather than leave a gap in a
	  real one. Prod has 16 Project Contracts and every one is ``maintenance``,
	  so without this every Events brief would carry that empty fee schedule.
	"""
	rows = [row for row in rows if row]
	if not rows:
		return None
	if not fillable and not any(row.get("value") for row in rows):
		return None
	return {"kind": "fields", "title": title, "rows": rows}


def _list_block(title, items):
	"""A bulleted block, dropped when there is nothing to bullet."""
	items = [item for item in (str(i or "").strip() for i in items) if item]
	if not items:
		return None
	return {"kind": "list", "title": title, "items": items}


def _text_block(title, text):
	"""A prose block, dropped when empty."""
	text = (text or "").strip()
	if not text:
		return None
	return {"kind": "text", "title": title, "text": text}


def _table_block(title, columns, rows):
	"""A grid block, dropped when there are no rows.

	``columns`` are ``(label, format)`` pairs and ``rows`` are lists positionally
	matching them. A longer list is truncated to :data:`MAX_TABLE_ROWS` and the
	block records how many were left off, rather than quietly printing a partial
	list that reads as a complete one.
	"""
	if not rows:
		return None
	return {
		"kind": "table",
		"title": title,
		"columns": [{"label": label, "format": fmt} for label, fmt in columns],
		"rows": rows[:MAX_TABLE_ROWS],
		"truncated": max(0, len(rows) - MAX_TABLE_ROWS),
	}


# ---------------------------------------------------------------------------
# Shared readers
# ---------------------------------------------------------------------------


def _may_read(doctype):
	"""True when the caller may read ``doctype`` **and** this site has it.

	``frappe.has_permission`` loads the doctype's meta, so for a doctype the site
	has not got it *raises* rather than returning False -- and this module reaches
	into five doctypes across four different modules of this app. Putting the
	gate behind its own try is what makes the "a section short, never a
	traceback" promise in the module docstring true of the permission check too,
	not just of the query behind it.
	"""
	try:
		return bool(frappe.has_permission(doctype, "read"))
	except Exception:
		return False


def _scope_blocks(doc, stream):
	"""The Customer Requests and Deliverables lists for one stream."""
	mapping = SCOPE_TABLES.get(stream)
	if not mapping:
		return []
	requests_table, requests_column, deliverables_table, deliverables_column = mapping
	return [
		_list_block("Customer Requests", _child_values(doc, requests_table, requests_column)),
		_list_block("Deliverables", _child_values(doc, deliverables_table, deliverables_column)),
	]


def _child_values(doc, table_field, row_field):
	"""The non-empty values of one column of a Project child table.

	``doc.get`` on a child table the site has not got returns ``None`` rather than
	raising -- the custom-field guard this app applies everywhere, and it matters
	here because a fresh install can reach the brief before the fixtures land.
	"""
	values = []
	for row in doc.get(table_field) or []:
		value = (row.get(row_field) or "").strip()
		if value:
			values.append(value)
	return values


def _project_contract(project_name, stream):
	"""The Project Contract this section may read, or ``{}``.

	Matched by Contract Template, best first, with no catch-all fallback: one
	doctype carries every template's fields and only its own are filled, so a
	mismatched contract would print a heading over another template's zeros. Void
	and cancelled contracts are excluded; among equals the most recently dated
	wins, because a revision supersedes what it amends.
	"""
	templates = CONTRACT_TEMPLATES.get(stream)
	if not templates or not _may_read("Project Contract"):
		return {}

	try:
		contracts = frappe.get_all(
			"Project Contract",
			filters={
				"project": project_name,
				"docstatus": ["<", 2],
				"status": ["!=", "Void"],
			},
			fields=["name", "contract_template", "template_key"],
			order_by="contract_date desc, revision desc, modified desc",
		)
		for template in templates:
			for contract in contracts:
				if template in (contract.get("contract_template"), contract.get("template_key")):
					return frappe.get_doc("Project Contract", contract["name"]).as_dict()
	except Exception:
		return {}
	return {}


def _purchase_orders(project_name):
	"""Submitted Purchase Orders for this job, newest first.

	Matches on the **union** of the header ``Purchase Order.project`` and the
	item-row ``Purchase Order Item.project``, for the reason
	``procurement_project.get_receivable_purchase_orders`` documents at length:
	``cascade_project_to_items`` fills blank item rows on save, and it fills
	*blanks only*, on *save only*, so an order written before that hook existed or
	re-pointed around it can carry one and not the other. The union costs one
	extra query and cannot be wrong.
	"""
	if not _may_read("Purchase Order"):
		return []

	try:
		names = set(frappe.get_all("Purchase Order", filters={"project": project_name}, pluck="name"))
		for row in frappe.get_all(
			"Purchase Order Item",
			filters={"project": project_name},
			fields=["parent"],
			distinct=True,
		):
			if row.get("parent"):
				names.add(row["parent"])

		if not names:
			return []

		return frappe.get_all(
			"Purchase Order",
			filters={"name": ["in", list(names)], "docstatus": 1},
			fields=[
				"name",
				"supplier",
				"transaction_date",
				"schedule_date",
				"status",
				"per_received",
				"grand_total",
			],
			order_by="transaction_date desc, name desc",
		)
	except Exception:
		return []


# ---------------------------------------------------------------------------
# Per-stream sections
# ---------------------------------------------------------------------------


def _design_blocks(doc):
	"""Design: what the client asked for, what we owe them, and the phase fees.

	Three phases are the shape of every Sapphire design engagement -- Concept,
	Design Development, Construction Documents -- each with a fee and a
	calendar-day duration. The duration rides on the same line as its fee rather
	than sitting in a second block that has to be read alongside the first.
	"""
	contract = _project_contract(doc.name, "Design")

	blocks = _scope_blocks(doc, "Design")
	blocks.append(
		_fields_block(
			"Design Phases & Fees",
			[
				_row("Design Retainer (due at signing)", contract.get("design_retainer"), "currency"),
				_row(
					"Phase 1 - Concept Design",
					contract.get("concept_design_fee"),
					"currency",
					note=_calendar_days(contract.get("concept_days")),
				),
				_row(
					"Phase 2 - Design Development",
					contract.get("design_development_fee"),
					"currency",
					note=_calendar_days(contract.get("design_development_days")),
				),
				_row(
					"Phase 3 - Construction Documents",
					contract.get("construction_documents_fee"),
					"currency",
					note=_calendar_days(contract.get("construction_documents_days")),
				),
				_row("Total Design Fee", contract.get("total_design_fee"), "currency"),
				_row("Design Hours Budgeted", doc.get("custom_time_budget_in_hours")),
			],
			# Fillable, unlike the other three contract-sourced blocks, and the
			# asymmetry is deliberate. There are no Design-specific fields on
			# Project at all -- the Design content is these fees, the two scope
			# tables and nothing else -- so marking this one non-fillable would
			# leave a Design job with no Design section whatsoever. Six blank
			# lines is also the printed original's own idiom: it has carried
			# "Fee ___ % | $ ___" since before any of this was in ERPNext.
			# "Rental Terms" earns the opposite treatment because the Events
			# section keeps its four dates either way.
		)
	)
	blocks.append(_text_block("Scope of Work", strip_html(contract.get("scope_of_work") or "")))
	return blocks


def _build_blocks(doc):
	"""Build: scope, where production stands, the money, and what is on order."""
	contract = _project_contract(doc.name, "Build")

	blocks = _scope_blocks(doc, "Build")
	blocks.append(
		_fields_block(
			"Production & Budget",
			[
				_row("Build Status", doc.get("custom_build_status")),
				_row("Production Started", doc.get("custom_production_started"), "date"),
				_row("Production Completed", doc.get("custom_production_completed"), "date"),
				_row("Quality Sign-Off By", _user_fullname(doc.get("custom_quality_sign_off_by"))),
				_row("Sign-Off Date", doc.get("custom_quality_sign_off_date"), "date"),
				_row("Bid Cost", doc.get("custom_bid_cost"), "currency"),
				_row("Materials Budget", doc.get("custom_materials_budget"), "currency"),
				_row("Hours Budgeted", doc.get("custom_time_budget_in_hours")),
				_row("Time & Materials", "Yes" if doc.get("custom_time__materials") else ""),
			],
		)
	)
	blocks.append(
		_fields_block(
			"Construction Schedule",
			[
				_row("Mobilization / Start", contract.get("mobilization_date"), "date"),
				_row("Substantial Completion", contract.get("substantial_completion_date"), "date"),
				_row("Final Completion", contract.get("final_completion_date"), "date"),
				_row("Anticipated Completion", contract.get("anticipated_completion_date"), "date"),
				_row("Working Hours", contract.get("working_hours")),
			],
			fillable=False,
		)
	)
	blocks.append(_text_block("Site Access", contract.get("site_access_notes")))

	orders = [
		po
		for po in _purchase_orders(doc.name)
		if po.get("status") not in SETTLED_PO_STATUSES and flt(po.get("per_received")) < 100
	]
	blocks.append(
		_table_block(
			"Open Purchase Orders",
			[
				("Order", "text"),
				("Supplier", "text"),
				("Ordered", "date"),
				("Required By", "date"),
				("Received", "percent"),
				("Value", "currency"),
			],
			[
				[
					po.get("name"),
					po.get("supplier"),
					po.get("transaction_date"),
					po.get("schedule_date"),
					flt(po.get("per_received")),
					po.get("grand_total"),
				]
				for po in orders
			],
		)
	)
	return blocks


def _service_blocks(doc):
	"""Service: the scope, the maintenance agreement, its features and its visits.

	The agreement is read from **Sapphire Maintenance Contract**, not from the
	Project Contract's maintenance section: the Project Contract is the signable
	paper, the Sapphire contract is the live agreement the scheduler drives visits
	from, so its term, frequency and next-billing date are the ones true today.
	All 16 on prod carry a project link.
	"""
	blocks = _scope_blocks(doc, "Service")
	agreement = _maintenance_agreement(doc.name)

	blocks.append(
		_fields_block(
			"Maintenance Agreement",
			[
				_row("Agreement", agreement.get("name")),
				_row("Service Plan", agreement.get("service_plan")),
				_row("Status", agreement.get("status")),
				_row("Start Date", agreement.get("start_date"), "date"),
				_row("End Date", agreement.get("end_date"), "date"),
				_row("Initial Term", agreement.get("initial_term")),
				_row("Visit Frequency", agreement.get("default_frequency")),
				_row("Invoicing", agreement.get("invoicing_frequency")),
				_row("Recurring Amount", agreement.get("recurring_amount"), "currency"),
				_row("Next Billing", agreement.get("next_billing_date"), "date"),
				# Both months are only meaningful when their seasonal visit is
				# switched on -- the month keeps its stored value after the box is
				# unticked, so printing it unconditionally would promise a spring
				# startup the contract does not cover.
				_row(
					"Spring Startup",
					agreement.get("startup_month") if agreement.get("seasonal_startup") else "",
				),
				_row(
					"Winterization",
					agreement.get("winterization_month") if agreement.get("winterization") else "",
				),
			],
			fillable=False,
		)
	)

	blocks.append(
		_table_block(
			"Covered Water Features",
			[
				("Feature", "text"),
				("Frequency", "text"),
				("Last Visit", "date"),
				("Next Visit", "date"),
			],
			[
				[
					feature.get("serial_no"),
					feature.get("frequency"),
					feature.get("last_visit_date"),
					feature.get("next_visit_date"),
				]
				for feature in agreement.get("covered_features") or []
			],
		)
	)

	blocks.append(
		_table_block(
			"Recent Visits",
			[("Date", "date"), ("Visit", "text"), ("Technician", "text"), ("Complete", "percent")],
			[
				[
					visit.get("visit_date"),
					visit.get("visit_label"),
					_user_fullname(visit.get("technician")),
					flt(visit.get("completion_percent")),
				]
				for visit in _maintenance_visits(doc.name)
			],
		)
	)
	return blocks


def _events_blocks(doc):
	"""Events: the four times that run the job, then the rental terms and kit.

	Delivery / Setup / Event / Take Down each carry their own notes field, and
	that is where the crew-facing detail lives ("gate code 4412", "load in through
	the service lift"). The note rides on the same line as its time, because
	reading one without the other is how a truck arrives at a locked gate.
	"""
	contract = _project_contract(doc.name, "Events")

	blocks = _scope_blocks(doc, "Events")
	blocks.append(
		_fields_block(
			"Event Schedule",
			[
				_row(
					"Delivery",
					doc.get("custom_delivery_date_time"),
					"datetime",
					note=doc.get("custom_delivery_date_time_notes"),
				),
				_row(
					"Setup",
					doc.get("custom_setup_date_time"),
					"datetime",
					note=doc.get("custom_setup_date_time_notes"),
				),
				_row(
					"Event",
					doc.get("custom_event_date_time"),
					"datetime",
					note=doc.get("custom_event_date_time_notes"),
				),
				_row(
					"Take Down",
					doc.get("custom_take_down_date_time"),
					"datetime",
					note=doc.get("custom_take_down_date_time_notes"),
				),
			],
		)
	)
	blocks.append(
		_fields_block(
			"Rental Terms",
			[
				_row("Rental Start", contract.get("rental_start_date"), "date"),
				_row("Rental End", contract.get("rental_end_date"), "date"),
				_row("Base Rental Fee", contract.get("base_rental_fee"), "currency"),
				_row("Delivery & Setup", contract.get("delivery_setup_fee"), "currency"),
				_row("Pickup & Removal", contract.get("pickup_removal_fee"), "currency"),
				_row("Water Treatment / Chemicals", contract.get("chemicals_fee"), "currency"),
				_row(
					contract.get("other_fee_label") or "Other Fee",
					contract.get("other_fee"),
					"currency",
				),
				_row("Total Rental Amount", contract.get("total_rental_amount"), "currency"),
				_row("Security Deposit", contract.get("security_deposit"), "currency"),
			],
			fillable=False,
		)
	)
	blocks.append(
		_table_block(
			"Equipment",
			[("Description", "text"), ("Serial / ID", "text"), ("Notes", "text")],
			[
				[item.get("description"), item.get("serial_id"), item.get("notes")]
				for item in contract.get("equipment_items") or []
			],
		)
	)
	blocks.append(
		_text_block("Notes for Scheduling", strip_html(doc.get("custom_notes_for_scheduling") or ""))
	)
	return blocks


def _products_blocks(doc):
	"""Products: the goods being supplied, and whether they have been billed.

	Products is the one stream with no scope child tables of its own -- it has
	never had them -- so the brief is built from what a goods job actually
	generates: the purchase order lines that buy the parts, the Product
	Configuration that specifies the unit, and the invoices that close it out.

	Note what is deliberately *not* here: ``custom_delivery_date_time``. The field
	exists on every Project but sits inside the ``custom_rent_schedule`` section,
	whose ``depends_on`` is ``project_type == 'Events'`` -- and every Products job
	on prod is stage Design. Printing a date nobody can see or correct on the form
	it came from is worse than leaving the line off.
	"""
	blocks = [
		_table_block(
			"Items on Order",
			[
				("Item", "text"),
				("Description", "text"),
				("Qty", "text"),
				("Rate", "currency"),
				("Amount", "currency"),
				("Required By", "date"),
			],
			_product_lines(doc.name),
		),
		_table_block(
			"Product Configurations",
			[
				("Configuration", "text"),
				("Part Number", "text"),
				("Status", "text"),
				("Price", "currency"),
			],
			_product_configurations(doc.name),
		),
	]

	invoiced, outstanding = _billing_totals(doc.name)
	blocks.append(
		_fields_block(
			"Supply & Billing",
			[
				_row("Contract Value", doc.get("custom_project_dollar_amount"), "currency"),
				_row("Invoiced to Date", invoiced, "currency"),
				_row("Outstanding", outstanding, "currency"),
				_row("Payment Received", "Yes" if doc.get("custom_payment_received") else ""),
				_row("Payment Method", doc.get("custom_payment_method")),
			],
		)
	)
	return blocks


# ---------------------------------------------------------------------------
# Per-stream readers
# ---------------------------------------------------------------------------


def _maintenance_agreement(project_name):
	"""The live maintenance agreement for this job, or ``{}``.

	Prefers an Active agreement over a draft or expired one, then the most
	recently started: a site that has been renewed carries both.
	"""
	if not _may_read("Sapphire Maintenance Contract"):
		return {}
	try:
		rows = frappe.get_all(
			"Sapphire Maintenance Contract",
			filters={"project": project_name, "docstatus": ["<", 2]},
			fields=["name", "status"],
			order_by="start_date desc, modified desc",
		)
		if not rows:
			return {}
		active = [row for row in rows if row.get("status") == "Active"]
		return frappe.get_doc("Sapphire Maintenance Contract", (active or rows)[0]["name"]).as_dict()
	except Exception:
		return {}


def _maintenance_visits(project_name, limit=5):
	"""The most recent visits logged on this job, newest first."""
	if not _may_read("Sapphire Maintenance Record"):
		return []
	try:
		return frappe.get_all(
			"Sapphire Maintenance Record",
			filters={"project": project_name, "docstatus": ["<", 2]},
			fields=["visit_date", "visit_label", "technician", "completion_percent"],
			order_by="visit_date desc, modified desc",
			limit=limit,
		)
	except Exception:
		return []


def _product_lines(project_name):
	"""Purchase Order lines buying the goods for this job.

	Read from the item rows rather than the order headers because a Products job
	*is* the parts, not the paperwork -- one order commonly covers several jobs.
	The orders come from :func:`_purchase_orders`, so these lines inherit the same
	header/row union it explains.
	"""
	orders = _purchase_orders(project_name)
	if not orders:
		return []

	try:
		lines = frappe.get_all(
			"Purchase Order Item",
			filters={"parent": ["in", [po["name"] for po in orders]], "docstatus": 1},
			fields=["item_code", "item_name", "qty", "uom", "rate", "amount", "schedule_date"],
			order_by="parent desc, idx asc",
		)
	except Exception:
		return []

	return [
		[
			line.get("item_code"),
			line.get("item_name"),
			_quantity(line.get("qty"), line.get("uom")),
			line.get("rate"),
			line.get("amount"),
			line.get("schedule_date"),
		]
		for line in lines
	]


def _product_configurations(project_name):
	"""Configured units specified for this job.

	Dormant on prod today (the Product Configurator holds one Configurable Product
	and no configurations), which is precisely why it is wired now: the block does
	not render at all until somebody configures a unit against a job.
	"""
	if not _may_read("Product Configuration"):
		return []
	try:
		rows = frappe.get_all(
			"Product Configuration",
			filters={"project": project_name},
			fields=["name", "config_title", "part_number", "status", "sell_price"],
			order_by="modified desc",
		)
	except Exception:
		return []

	return [
		[
			row.get("config_title") or row.get("name"),
			row.get("part_number"),
			row.get("status"),
			row.get("sell_price"),
		]
		for row in rows
	]


def _billing_totals(project_name):
	"""``(invoiced, outstanding)`` across submitted Sales Invoices for this job.

	Header-level ``Sales Invoice.project`` only: 1,324 invoices on prod carry it
	and **none** carry it on their item rows, so the line-level union purchase
	orders need would here add a query that can only ever return nothing.

	Returns ``(None, None)`` when the job has no invoices, so the lines render as
	blank slots rather than asserting a confident ``$0.00`` that would read as
	"billed nothing" instead of "not billed yet".
	"""
	if not _may_read("Sales Invoice"):
		return None, None
	try:
		rows = frappe.get_all(
			"Sales Invoice",
			filters={"project": project_name, "docstatus": 1},
			fields=["grand_total", "outstanding_amount"],
		)
	except Exception:
		return None, None
	if not rows:
		return None, None
	return (
		sum(flt(row.get("grand_total")) for row in rows),
		sum(flt(row.get("outstanding_amount")) for row in rows),
	)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _calendar_days(days):
	"""``"14 calendar days"``, or empty for an unset duration."""
	days = flt(days)
	if not days:
		return ""
	return f"{int(days)} calendar days"


def _quantity(qty, uom):
	"""``"12 Nos"`` -- the number with its unit, because a bare 12 is 12 of what.

	Whole quantities print whole: ``flt`` returns a float and ``12.0 Nos`` on a
	purchase line reads as a rounding artefact rather than a count.
	"""
	qty = flt(qty)
	number = str(int(qty)) if qty == int(qty) else str(qty)
	return " ".join(part for part in (number, (uom or "").strip()) if part)


def _user_fullname(user):
	"""Resolve a User link to its full name, falling back to the login id."""
	if not user:
		return ""
	try:
		return frappe.db.get_value("User", user, "full_name") or user
	except Exception:
		return user

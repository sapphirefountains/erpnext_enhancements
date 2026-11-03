# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Service Calls: one-off maintenance, startup, winterization and small repairs (v1.777.0).

Nikolas, 2026-11-03 (vault decision ``2026-11-03-service-call-one-off-jobs``): on that day prod had
361 Service-type Projects, every one made from an Opportunity and carrying the eleven-task build
template (every task Overdue on day one), and almost none had a Maintenance Record, because a
Record required a Maintenance Contract and a Serial No. The visit itself went undocumented. A
**Service Call** is the generic "went out and did the work" document for a client who phones in
without a Maintenance Services Agreement:

* **It hangs off a Project.** A project number is always required ("we always use a project for
  jobs"), so the Service Call links to a Service-type Project rather than replacing it. The
  Project gets the three-task **Service Call** Project Template (:data:`TASKS`: Approval received,
  Service visit, Invoice sent), re-dated from the scheduled visit (:func:`task_dates`) so nothing
  is born Overdue. The eleven-task build template stays for Build and Design.
* **The Opportunity is created first, at its first status**, read from the installed site's
  Opportunity meta (:func:`first_status`; on prod a Property Setter makes it "Qualification"),
  never Closed Won on creation. The client's approval on the emailed or texted link moves it to
  Closed Won; a decline or an expired link marks it Lost (``service_call_approval.py``).
* **One intake makes all of it** (:func:`create_service_call`): the Customer must exist (the
  dialog's Link field creates one through frappe's quick entry), the site Address is found or
  made, then the Opportunity, the Project and the Service Call go in in that order under one
  savepoint, so a failure leaves nothing half-made.
* **Rates are copied, never looked up live** (:func:`copy_rates`): the matching **Service Call
  Rate** row's figure lands on the call when it is created, so a later rate edit never changes a
  call already quoted. Startup, Winterization and Maintenance Visit are flat rates; a Small
  Repair is time and materials at the As-needed, Emergency or After-hours Emergency hourly rate.
  The rate table is filled on prod and ships empty: the repository is public and holds no
  figures.
* **The visit is an ordinary Maintenance Record** with ``service_call`` set in place of a
  Contract and Serial No, so the Visit Wizard, review, sign-off, print and portal are shared.
  Its template defaults from the service type (:data:`TEMPLATE_BY_TYPE`); Small Repair gets the
  template ``patches/seed_small_repair_template.py`` seeds. On approval the record drafts a
  Sales Invoice from the call's rates (:func:`invoice_for_visit`): the flat rate as one line, or
  hours times the hourly rate plus parts at the Item's price; chemicals on top as on a contract
  visit.

Everything that runs inside a hook never raises; the endpoints raise so a desk user is told.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, nowdate

DOCTYPE = "Service Call"
RATE_DOCTYPE = "Service Call Rate"
RECORD = "Sapphire Maintenance Record"
LOG_TITLE = "Service calls"

SERVICE_TYPES = ("Startup", "Winterization", "Maintenance Visit", "Small Repair")
SMALL_REPAIR = "Small Repair"
FLAT = "Flat"
HOURLY_KINDS = ("As-needed", "Emergency", "After-hours Emergency")
RATE_KINDS = (FLAT, *HOURLY_KINDS)

STATUSES = ("Requested", "Sent for Approval", "Approved", "Declined", "Scheduled", "Done", "Invoiced", "Canceled")
#: Which status may follow which. Approval, decline and expiry are the link's
#: (``service_call_approval``); Scheduled is the visit's draft; Done and Invoiced are the visit's
#: approval. Canceled is reachable from anything not yet done.
TRANSITIONS = {
	"Requested": {"Sent for Approval", "Approved", "Canceled"},
	"Sent for Approval": {"Sent for Approval", "Approved", "Declined", "Canceled"},
	"Approved": {"Scheduled", "Done", "Canceled"},
	"Declined": {"Sent for Approval", "Canceled"},
	"Scheduled": {"Done", "Canceled"},
	"Done": {"Invoiced"},
	"Invoiced": set(),
	"Canceled": set(),
}
#: Statuses in which a link may be sent (or sent again).
SENDABLE = ("Requested", "Sent for Approval", "Declined")

#: The visit template each service type defaults to. The first three are the generic templates
#: ``seed_maintenance_sections`` made (Draft on prod); the fourth is seeded by
#: ``seed_small_repair_template``. A template that is missing on the site leaves the field blank.
SMALL_REPAIR_TEMPLATE = "Small Repair"
TEMPLATE_BY_TYPE = {
	"Startup": "Seasonal Startup",
	"Winterization": "Winterization",
	"Maintenance Visit": "Standard Fountain Maintenance",
	SMALL_REPAIR: SMALL_REPAIR_TEMPLATE,
}
#: The Small Repair template's own sections (``patches/seed_small_repair_template.py``). The
#: invoice builder reads the labor hours from :data:`LABOR_SECTION` and bills the rows of
#: :data:`PARTS_SECTION` as parts.
WORK_SECTION = "Small Repair - Work Performed"
LABOR_SECTION = "Small Repair - Labor"
PARTS_SECTION = "Small Repair - Parts Used"
LABOR_READING = "Labor hours"

#: The Project side. ``start`` and ``duration`` are ERPNext's *Begin On (Days)* and *Duration
#: (Days)* on a template Task, the offsets a project made from the template gets before
#: :func:`task_dates` re-dates them from the scheduled visit.
PROJECT_TEMPLATE = "Service Call"
PROJECT_TYPE = "Service"
VALUE_STREAM = "Service"
TASKS = (
	{"subject": "Approval received", "start": 0, "duration": 2, "description": "The client approved the service call on the emailed or texted link."},
	{"subject": "Service visit", "start": 3, "duration": 0, "description": "The visit itself, filed as a Maintenance Record against the Service Call."},
	{"subject": "Invoice sent", "start": 4, "duration": 7, "description": "The draft Sales Invoice from the visit, checked and sent."},
)
INVOICE_DAYS = 7
#: How far past the planned end the Project is inserted with (:func:`project_window`). ERPNext's
#: ``copy_from_template`` inserts each template Task at the project's expected start plus the
#: template's offsets (day 11 for Invoice sent), shifted off holidays, and Task's
#: ``validate_parent_project_dates`` (16.52.1) refuses a new task ending after the project's
#: Expected End Date. A visit within a few days, a past visit or no date at all would end before
#: day 11, so the Project goes in wide and takes its real end after the tasks are re-dated.
PROJECT_WINDOW_DAYS = 30
#: ``project_name`` and ``custom_opportunity_name`` are Data fields.
NAME_MAX = 140
#: Opportunity Lost Reason masters a Service Call's Lost moves use (made on first use).
LOST_DECLINED = "Client declined"
LOST_EXPIRED = "Approval link expired"
LOST_CANCELED = "Service call canceled"

#: Short terms the approval page and email show, taken from the 2026-10-13 agreement terms. No
#: figures here (the repository is public): the rates come from the Service Call Rate table at
#: render time (``service_call_approval.hourly_rates``).
TERMS_FLAT = (
	"Chemicals used on the visit are billed in addition to the rate above.",
	"Anything found on site beyond the work described is quoted before it is done.",
	"Invoices are due within 30 days.",
)
TERMS_HOURLY = (
	"Labor is billed by the hour at the rate above, from arrival on site.",
	"Parts and materials are billed in addition.",
	"Emergency and after-hours emergency calls are billed at the emergency rates shown.",
	"Invoices are due within 30 days.",
)


# ---------------------------------------------------------------------------- pure


def is_hourly(service_type):
	return service_type == SMALL_REPAIR


def rate_field(service_type):
	"""The Service Call field the copied rate lands in."""
	return "hourly_rate" if is_hourly(service_type) else "flat_rate"


def default_rate_kind(service_type):
	return HOURLY_KINDS[0] if is_hourly(service_type) else FLAT


def rate_row_problem(service_type, rate_kind):
	"""Why a (service type, rate kind) pair is not allowed, or None.

	Startup, Winterization and Maintenance Visit are flat (decision 2026-11-03); a Small Repair is
	time and materials at one of the three hourly kinds.
	"""
	if service_type not in SERVICE_TYPES:
		return f"{service_type!r} is not a service type."
	if rate_kind not in RATE_KINDS:
		return f"{rate_kind!r} is not a rate kind."
	if is_hourly(service_type) and rate_kind == FLAT:
		return "A Small Repair is billed by the hour: pick As-needed, Emergency or After-hours Emergency."
	if not is_hourly(service_type) and rate_kind != FLAT:
		return f"{service_type} is a flat rate; the hourly kinds are for Small Repair."
	return None


def first_status(options):
	"""The first status in a Select's options: the Opportunity's opening stage on this site.

	Read from the installed meta, never assumed: on prod a Property Setter replaces ERPNext's
	``Open`` with the sales stages and ``Qualification`` comes first.
	"""
	for line in str(options or "").split("\n"):
		if line.strip():
			return line.strip()
	return ""


def job_name(customer_name, service_type, day):
	"""``"Myers Mortuary - Winterization 2026-11-10"``: the Project's and the Opportunity's name."""
	customer = " ".join(str(customer_name or "").split())
	text = f"{customer} - {service_type} {day}".strip(" -")
	return text[:NAME_MAX]


def task_dates(today, scheduled=None):
	"""``[{"subject", "start", "end"}]`` for the three template tasks, dated from the visit.

	With a scheduled visit on or after today: approval runs from today to the day before the
	visit (today, when the visit is today), the visit is on its day, and the invoice has
	:data:`INVOICE_DAYS` days after it. With no scheduled date, the template's own offsets from
	today stand. A visit already in the past (a call logged after the fact) keeps its real day,
	approval is today, and the invoice runs from today.
	"""
	today = getdate(today)
	if not scheduled:
		rows = []
		for task in TASKS:
			start = add_days(today, task["start"])
			rows.append({"subject": task["subject"], "start": start, "end": add_days(start, task["duration"])})
		return rows
	visit = getdate(scheduled)
	if visit >= today:
		approval_end = max(today, add_days(visit, -1))
		invoice_start = visit
	else:
		approval_end = today
		invoice_start = today
	return [
		{"subject": TASKS[0]["subject"], "start": today, "end": approval_end},
		{"subject": TASKS[1]["subject"], "start": visit, "end": visit},
		{"subject": TASKS[2]["subject"], "start": invoice_start, "end": add_days(invoice_start, INVOICE_DAYS)},
	]


def project_window(today, scheduled=None):
	"""``(start, insert_end, planned_end)`` for the Project.

	``start`` is today, or the visit's day when it is already past. ``insert_end`` is what the Project
	is inserted with: the planned end or :data:`PROJECT_WINDOW_DAYS` after today, whichever is later,
	so every template Task ERPNext copies (the last ends ``start + 11`` days, holidays aside) fits
	inside it. ``planned_end`` is the real end, written once the tasks are re-dated.
	"""
	today = getdate(today)
	rows = task_dates(today, scheduled)
	start = min([today] + [row["start"] for row in rows])
	planned_end = max(row["end"] for row in rows)
	insert_end = max(planned_end, add_days(today, PROJECT_WINDOW_DAYS))
	return start, insert_end, planned_end


def can_transition(current, new):
	return new in TRANSITIONS.get(current or "Requested", set())


def plan_intake(values, today, customer_name, opening_status, owner):
	"""What the intake creates, in order, from the dialog's values. Pure; no lookups.

	Returns ``[("opportunity", {...}), ("project", {...}), ("service_call", {...})]``: the
	Opportunity first at the site's opening status, then the Project on the Service Call template
	named after the customer, the service type and the day, then the Service Call linked to both.
	The Customer and Address are not in the plan: the Customer must already exist and the Address
	is found or made before it (:func:`_ensure_address`).
	"""
	service_type = values.get("service_type")
	rate_kind = values.get("rate_kind") or default_rate_kind(service_type)
	scheduled = values.get("scheduled_date") or None
	day = getdate(scheduled) if scheduled else getdate(today)
	name = job_name(customer_name, service_type, day)
	opportunity = {
		"doctype": "Opportunity",
		"opportunity_from": "Customer",
		"party_name": values.get("customer"),
		"transaction_date": getdate(today),
		"status": opening_status,
		"opportunity_owner": owner,
		"custom_opportunity_name": name,
		"custom_value_stream": [{"value_stream": VALUE_STREAM}],
		"customer_address": values.get("site_address") or None,
		"contact_email": values.get("contact_email") or None,
		"contact_mobile": values.get("contact_phone") or None,
		"custom_general_scope_description": values.get("scope") or None,
	}
	start, insert_end, _planned_end = project_window(today, scheduled)
	project = {
		"doctype": "Project",
		"project_name": name,
		"customer": values.get("customer"),
		"project_type": PROJECT_TYPE,
		"project_template": PROJECT_TEMPLATE,
		"status": "Active",
		"expected_start_date": start,
		# Wide on purpose: the real end lands after the template tasks are re-dated
		# (:func:`project_window`, :func:`_make_project`).
		"expected_end_date": insert_end,
	}
	service_call = {
		"doctype": DOCTYPE,
		"customer": values.get("customer"),
		"contact_name": values.get("contact_name") or None,
		"contact_phone": values.get("contact_phone") or None,
		"contact_email": values.get("contact_email") or None,
		"site_address": values.get("site_address") or None,
		"serial_no": values.get("serial_no") or None,
		"service_type": service_type,
		"rate_kind": rate_kind,
		"scheduled_date": scheduled,
		"technician": values.get("technician") or None,
		"scope": values.get("scope") or None,
		"status": "Requested",
	}
	return [("opportunity", opportunity), ("project", project), ("service_call", service_call)]


def billable_hours(record):
	"""Hours to bill a Small Repair: the *Labor hours* reading the technician typed, else the
	clocked time (clock out minus clock in minus the pause), else 0."""
	for row in record.get("chemistry_readings") or []:
		if _same(row.get("reading"), LABOR_READING) and flt(row.get("reading_value")) > 0:
			return flt(row.get("reading_value"))
	clock_in = record.get("clock_in_time")
	clock_out = record.get("clock_out_time")
	if not clock_in or not clock_out:
		return 0.0
	seconds = (get_datetime(clock_out) - get_datetime(clock_in)).total_seconds() - flt(record.get("paused_duration"))
	return max(round(seconds / 3600.0, 2), 0.0)


def is_part(row, is_chemical):
	"""Whether a consumable row is a part (billed at the Item's price) rather than a chemical.

	A row from the Parts Used section is a part. A row added on site has no section
	(the wizard's *Add another item*), so it is a part unless its Item is a chemical
	(``is_chemical(item)``: in the consumables item group).
	"""
	if row.get("section") == PARTS_SECTION:
		return True
	if row.get("section"):
		return False
	return not is_chemical(row.get("item"))


def invoice_lines(call, record, fee_item, price_of, is_chemical, chemical_lines):
	"""The draft invoice's lines for a Service Call visit, as dicts for ``invoice.append``.

	``call`` is the Service Call (its copied rates), ``record`` the approved visit, ``fee_item``
	the maintenance fee Item from Settings, ``price_of(item)`` an Item's selling price,
	``is_chemical(item)`` whether an Item is a chemical, and ``chemical_lines(rows)`` the
	contract visits' chemical billing (``chemical_billing.invoice_lines``) over the rows that are
	not parts. Returns ``(lines, notes)``: the notes say what could not be billed.
	"""
	lines = []
	notes = []
	service_type = call.get("service_type")
	if not fee_item:
		notes.append("No fee line added: set the Default Maintenance Fee Item in ERPNext Enhancements Settings.")
	elif is_hourly(service_type):
		hours = billable_hours(record)
		rate = flt(call.get("hourly_rate"))
		if hours <= 0:
			notes.append("No labor line added: the visit has no labor hours and no clock times.")
		elif rate <= 0:
			notes.append("No labor line added: the Service Call has no hourly rate.")
		else:
			lines.append(
				{
					"item_code": fee_item,
					"qty": hours,
					"rate": rate,
					"description": f"{service_type} labor ({call.get('rate_kind') or 'As-needed'}), {hours:g} h",
				}
			)
	else:
		rate = flt(call.get("flat_rate"))
		if rate <= 0:
			notes.append("No fee line added: the Service Call has no flat rate.")
		else:
			lines.append({"item_code": fee_item, "qty": 1, "rate": rate, "description": service_type})

	parts = []
	others = []
	for row in record.get("consumables") or []:
		if flt(row.get("qty")) <= 0 or not row.get("item"):
			continue
		(parts if is_part(row, is_chemical) else others).append(row)
	for row in parts:
		price = flt(price_of(row.get("item")))
		if price <= 0:
			notes.append(f"{row.get('item_name') or row.get('item')} has no selling price, so it was not billed.")
			continue
		lines.append(
			{
				"item_code": row.get("item"),
				"qty": flt(row.get("qty")),
				"rate": price,
				"description": f"Part: {row.get('item_name') or row.get('item')}",
			}
		)
	for line in chemical_lines(others) or []:
		lines.append(line)
	return lines, notes


def _same(a, b):
	return "".join(str(a or "").lower().split()) == "".join(str(b or "").lower().split())


# ---------------------------------------------------------------------------- lookups


def rate_for(service_type, rate_kind):
	"""The enabled Service Call Rate for the pair, as a float, or None when none is filled in."""
	try:
		row = frappe.db.get_value(
			RATE_DOCTYPE, {"service_type": service_type, "rate_kind": rate_kind, "disabled": 0}, "rate"
		)
	except Exception:
		return None
	return flt(row) if row is not None else None


def copy_rates(doc):
	"""Stamp the matching rate onto ``doc`` when it has none for its service type."""
	field = rate_field(doc.get("service_type"))
	if flt(doc.get(field)):
		return
	rate = rate_for(doc.get("service_type"), doc.get("rate_kind"))
	if rate is not None:
		doc.set(field, rate)


def template_for(service_type):
	"""The visit template the service type defaults to, when it exists on the site."""
	name = TEMPLATE_BY_TYPE.get(service_type)
	try:
		return name if name and frappe.db.exists("Sapphire Maintenance Template", name) else None
	except Exception:
		return None


def opening_status():
	"""The Opportunity's first status on this site, from the installed meta."""
	try:
		field = frappe.get_meta("Opportunity").get_field("status")
		return first_status(field.options if field else "") or "Open"
	except Exception:
		return "Open"


def address_display(address):
	"""The Address as the Desk shows it, or "". Never raises."""
	if not address:
		return ""
	try:
		from frappe.contacts.doctype.address.address import get_address_display

		return get_address_display(frappe.get_doc("Address", address).as_dict()) or ""
	except Exception:
		return ""


# ---------------------------------------------------------------------------- intake


@frappe.whitelist(methods=["POST"])
def create_service_call(values):
	"""The New Service Call dialog's Create: Address (found or made), Opportunity, Project and
	Service Call, in that order, under one savepoint. Returns the three names.

	Needs create on Service Call; the Opportunity, Project and Address are inserted with
	``ignore_permissions`` because that permission is the gate (a Maintenance Supervisor who
	takes the call may not hold create on Project). Every hook on those inserts runs: the Project
	gets its template tasks from ERPNext's own ``after_insert``, its PRO-0204 steps and anything
	else a normal insert does.
	"""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	values = _parse(values)
	customer = values.get("customer")
	if not customer or not frappe.db.exists("Customer", customer):
		frappe.throw(_("Pick the customer first, or create one from the Customer field."), title=_("Customer needed"))
	if values.get("service_type") not in SERVICE_TYPES:
		frappe.throw(_("Pick the service type."), title=_("Service type needed"))
	rate_kind = values.get("rate_kind") or default_rate_kind(values["service_type"])
	problem = rate_row_problem(values["service_type"], rate_kind)
	if problem:
		frappe.throw(_(problem), title=_("Rate kind"))
	values["rate_kind"] = rate_kind

	frappe.db.savepoint("service_call_intake")
	try:
		values["site_address"] = _ensure_address(values, customer)
		customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
		plan = dict(plan_intake(values, nowdate(), customer_name, opening_status(), frappe.session.user))
		opportunity = _make_opportunity(plan["opportunity"])
		project = _make_project(plan["project"], opportunity.name, values.get("scheduled_date"))
		if frappe.db.has_column("Opportunity", "custom_created_project"):
			opportunity.db_set("custom_created_project", project.name, update_modified=False)
		call = frappe.get_doc(plan["service_call"])
		call.project = project.name
		call.opportunity = opportunity.name
		call.insert()
	except Exception:
		frappe.db.rollback(save_point="service_call_intake")
		raise
	return {"service_call": call.name, "project": project.name, "opportunity": opportunity.name}


def _parse(values):
	if isinstance(values, str):
		values = json.loads(values or "{}")
	return dict(values or {})


def _ensure_address(values, customer):
	"""The site Address: the one picked, else one made from the dialog's new-address lines and
	linked to the Customer, else None (an Address is not required to log the call)."""
	picked = values.get("site_address")
	if picked:
		if not frappe.db.exists("Address", picked):
			frappe.throw(_("Address {0} was not found.").format(picked))
		return picked
	line1 = (values.get("address_line1") or "").strip()
	city = (values.get("city") or "").strip()
	if not line1 and not city:
		return None
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": frappe.db.get_value("Customer", customer, "customer_name") or customer,
			"address_type": "Shipping",
			"address_line1": line1 or city,
			"city": city or None,
			"state": (values.get("state") or "").strip() or None,
			"pincode": (values.get("pincode") or "").strip() or None,
			"country": (values.get("country") or "").strip() or "United States",
			"email_id": values.get("contact_email") or None,
			"phone": values.get("contact_phone") or None,
			"is_shipping_address": 1,
		}
	)
	address.append("links", {"link_doctype": "Customer", "link_name": customer})
	address.insert(ignore_permissions=True)
	return address.name


def _make_opportunity(values):
	company = frappe.defaults.get_global_default("company")
	if company:
		values["company"] = company
	# The value stream rows are a Custom Field on prod; a site without it (a fresh install, a
	# test bench) must still log the call.
	if not frappe.db.has_column("Value Stream", "value_stream") or not frappe.db.exists("Value Streams", VALUE_STREAM):
		values.pop("custom_value_stream", None)
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	return doc


def _make_project(values, opportunity, scheduled):
	if not frappe.db.exists("Project Type", values.get("project_type")):
		values.pop("project_type", None)
	if not frappe.db.exists("Project Template", values.get("project_template")):
		values.pop("project_template", None)
	if frappe.db.has_column("Project", "custom_opportunity"):
		values["custom_opportunity"] = opportunity
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	# ERPNext's after_insert has copied the template tasks inside the wide window; now the real dates.
	redate_tasks(doc.name, nowdate(), scheduled)
	_start, _insert_end, planned_end = project_window(nowdate(), scheduled)
	frappe.db.set_value("Project", doc.name, "expected_end_date", planned_end, update_modified=False)
	doc.expected_end_date = planned_end
	return doc


def redate_tasks(project, today, scheduled):
	"""Date the Project's three template tasks from the scheduled visit (:func:`task_dates`).

	ERPNext dates each task from the project's expected start by the template's fixed offsets,
	which cannot know the visit day; this writes the real dates. Field writes, so no Task
	validation re-runs. Never raises: a date is not worth a failed intake.
	"""
	try:
		wanted = {row["subject"]: row for row in task_dates(today, scheduled)}
		tasks = frappe.get_all("Task", filters={"project": project}, fields=["name", "subject"])
		for task in tasks:
			row = wanted.get(task.subject)
			if row:
				frappe.db.set_value(
					"Task",
					task.name,
					{"exp_start_date": row["start"], "exp_end_date": row["end"]},
					update_modified=False,
				)
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())


# ---------------------------------------------------------------------------- the visit


@frappe.whitelist(methods=["POST"])
def schedule_visit(service_call, scheduled_date=None, technician=None):
	"""Draft the Maintenance Record for an approved call, on the date given or the call's, so it
	shows on the Maintenance Planner like a contract visit. One per call."""
	call = frappe.get_doc(DOCTYPE, service_call)
	call.check_permission("write")
	if call.status not in ("Approved", "Scheduled"):
		frappe.throw(_("The client has not approved this service call yet."), title=_("Not approved"))
	if call.get("maintenance_record") and frappe.db.exists(RECORD, call.maintenance_record):
		return {"record": call.maintenance_record, "already": True}
	day = scheduled_date or call.get("scheduled_date")
	if not day:
		frappe.throw(_("Pick the day of the visit."), title=_("Date needed"))
	record = draft_visit(call, day, technician or call.get("technician"))
	return {"record": record.name}


def draft_visit(call, day, technician=None):
	"""Insert the call's draft visit and move the call to Scheduled. Raises on failure."""
	record = frappe.new_doc(RECORD)
	record.customer = call.customer
	record.project = call.project
	record.service_call = call.name
	record.serial_no = call.get("serial_no") or None
	record.technician = technician or None
	record.template = call.get("template") or template_for(call.service_type)
	record.scheduled_visit_date = getdate(day)
	record.insert(ignore_permissions=True)
	call.db_set(
		{
			"maintenance_record": record.name,
			"scheduled_date": getdate(day),
			"technician": technician or call.get("technician"),
			"status": "Scheduled",
		}
	)
	call.add_comment("Comment", _("Visit {0} drafted for {1}.").format(frappe.get_link_to_form(RECORD, record.name), getdate(day)))
	return record


def on_visit_submit(doc, method=None):
	"""``Sapphire Maintenance Record.on_submit``: the call is Done and points at its visit.

	Never raises: the approval that submitted the visit must land whatever this finds.
	"""
	try:
		name = doc.get("service_call")
		if not name or not frappe.db.exists(DOCTYPE, name):
			return
		status = frappe.db.get_value(DOCTYPE, name, "status")
		updates = {"maintenance_record": doc.name}
		if status not in ("Done", "Invoiced", "Canceled"):
			updates["status"] = "Done"
		frappe.db.set_value(DOCTYPE, name, updates)
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())


def on_visit_cancel(doc, method=None):
	"""A canceled visit sends its call back to Scheduled, so another can be drafted. Never raises."""
	try:
		name = doc.get("service_call")
		if not name or not frappe.db.exists(DOCTYPE, name):
			return
		row = frappe.db.get_value(DOCTYPE, name, ["status", "maintenance_record"], as_dict=True)
		if row and row.maintenance_record == doc.name and row.status in ("Done", "Scheduled"):
			frappe.db.set_value(DOCTYPE, name, {"status": "Approved", "maintenance_record": None})
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())


def invoice_for_visit(record):
	"""Draft the Sales Invoice for a Service Call visit, from the call's copied rates.

	Called by ``api.maintenance_workflow.create_sales_invoice`` in place of the Sales Order path
	when the record has a ``service_call``. Idempotent on ``custom_maintenance_record``. Comments
	on the visit say what was billed and what could not be.
	"""
	call = frappe.db.get_value(
		DOCTYPE,
		record.service_call,
		["name", "service_type", "rate_kind", "flat_rate", "hourly_rate", "customer", "project", "status"],
		as_dict=True,
	)
	if not call:
		record.add_comment("Comment", _("Sales Invoice not created: Service Call {0} not found.").format(record.service_call))
		return None
	if frappe.db.exists("Sales Invoice", {"custom_maintenance_record": record.name, "docstatus": ["<", 2]}):
		return None

	settings = frappe.get_single("ERPNext Enhancements Settings")
	consumables_group = settings.get("maintenance_consumables_group")

	def price_of(item):
		rate = frappe.db.get_value("Item Price", {"item_code": item, "price_list": "Standard Selling", "selling": 1}, "price_list_rate")
		if rate is None:
			rate = frappe.db.get_value("Item", item, "standard_rate")
		return flt(rate)

	def is_chemical(item):
		if not consumables_group or not item:
			return False
		return frappe.db.get_value("Item", item, "item_group") == consumables_group

	from erpnext_enhancements.sapphire_maintenance.chemical_billing import invoice_lines as chemical_lines

	lines, notes = invoice_lines(
		call, record, settings.get("maintenance_fee_item"), price_of, is_chemical, lambda rows: chemical_lines(rows, visit=record.name)
	)
	for note in notes:
		record.add_comment("Comment", _(note))
	if not lines:
		record.add_comment("Comment", _("Sales Invoice not created: nothing to bill."))
		return None

	invoice = frappe.new_doc("Sales Invoice")
	invoice.customer = record.customer
	invoice.project = record.project
	invoice.posting_date = nowdate()
	invoice.due_date = add_days(nowdate(), 30)
	invoice.custom_maintenance_record = record.name
	for line in lines:
		invoice.append("items", line)
	invoice.set_missing_values()
	invoice.insert()
	record.db_set("sales_invoice", invoice.name)
	record.add_comment("Comment", _("Draft Sales Invoice {0} created from Service Call {1}.").format(
		frappe.get_link_to_form("Sales Invoice", invoice.name), call.name
	))
	frappe.db.set_value(DOCTYPE, call.name, {"sales_invoice": invoice.name, "status": "Invoiced"})
	return invoice


# ---------------------------------------------------------------------------- the office


@frappe.whitelist(methods=["POST"])
def cancel_service_call(service_call, reason=None):
	"""Cancel a call that is not done. Its Opportunity is marked Lost with the reason."""
	call = frappe.get_doc(DOCTYPE, service_call)
	call.check_permission("write")
	if not can_transition(call.status, "Canceled"):
		frappe.throw(_("A {0} service call cannot be canceled.").format(_(call.status)), title=_("Cannot cancel"))
	call.db_set("status", "Canceled")
	call.add_comment("Comment", _("Canceled: {0}").format(frappe.utils.escape_html(reason or _("no reason given"))))
	set_opportunity_status(call.get("opportunity"), "Lost", reason or LOST_CANCELED, lost_reason=LOST_CANCELED, service_call=call.name)
	return {"status": "Canceled"}


def set_opportunity_status(opportunity, status, reason=None, lost_reason=None, service_call=None):
	"""Move the call's Opportunity: ``"Closed Won"`` on approval, ``"Lost"`` on a decline, expiry
	or cancel, or its opening status on a resend after a decline. A full save, so the won-date,
	hand-off and tag hooks run as they do for a deal moved by hand.

	A move to Lost carries a ``lost_reasons`` row (``lost_reason``, one of the three masters this
	module makes on first use, :func:`ensure_lost_reason`): the Opportunity's own
	``validate_close_reason`` refuses a fresh transition to Lost without one, and until v1.777.0's
	review the move was refused and the refusal swallowed, so a declined call's deal stayed open.
	``reason`` is the detail (the client's words) and goes to ``order_lost_reason``.

	Never raises; a failure is an Error Log **and a Comment on the Service Call** saying the deal
	was not moved and why, so nobody reads a Declined call as a Lost deal.
	"""
	if not opportunity:
		return
	try:
		if not frappe.db.exists("Opportunity", opportunity):
			return
		doc = frappe.get_doc("Opportunity", opportunity)
		if doc.status == status:
			return
		doc.status = status
		if status == "Lost":
			if reason and doc.meta.get_field("order_lost_reason"):
				doc.order_lost_reason = str(reason)[:500]
			label = ensure_lost_reason(lost_reason or LOST_DECLINED)
			if label and doc.meta.get_field("lost_reasons") and not any(
				row.get("lost_reason") == label for row in doc.get("lost_reasons") or []
			):
				doc.append("lost_reasons", {"lost_reason": label})
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
		_note_opportunity_not_moved(service_call, opportunity, status)


def belongs_to_service_call(opportunity):
	"""Whether ``opportunity`` is a Service Call's deal (Nikolas, 2026-11-03: "skip ranks").

	Read by the Opportunity's Closed Won hooks: ``validate_ranks_on_won`` does not ask a one-off
	service client for scope, schedule and budget ranks, and ``handoff.stamp_handoff_gate`` does
	not put a service call under the PRO-0204 hand-off requirements. Never raises: on a fresh
	site the Service Call table may not exist yet when an Opportunity is saved, and a hook that
	raises there blocks every deal on the site.
	"""
	if not opportunity:
		return False
	try:
		return bool(frappe.db.exists(DOCTYPE, {"opportunity": opportunity}))
	except Exception:
		return False


def ensure_lost_reason(label):
	"""The Opportunity Lost Reason master ``label``, made if missing. ``None`` when it cannot be."""
	if not label:
		return None
	try:
		if frappe.db.exists("Opportunity Lost Reason", label):
			return label
		frappe.get_doc({"doctype": "Opportunity Lost Reason", "lost_reason": label}).insert(ignore_permissions=True)
		return label
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
		return None


def _note_opportunity_not_moved(service_call, opportunity, status):
	if not service_call:
		return
	try:
		call = frappe.get_doc(DOCTYPE, service_call)
		call.add_comment(
			"Comment",
			_("Opportunity {0} was NOT moved to {1}: its save was refused. See the Error Log ({2}) and move it by hand.").format(
				frappe.get_link_to_form("Opportunity", opportunity), _(status), LOG_TITLE
			),
		)
	except Exception:
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())


def technician_options():
	"""Users holding Maintenance User, for the intake dialog's Technician pick."""
	users = frappe.get_all("Has Role", filters={"role": "Maintenance User", "parenttype": "User"}, pluck="parent")
	if not users:
		return []
	return frappe.get_all(
		"User", filters={"name": ["in", users], "enabled": 1, "user_type": "System User"}, fields=["name", "full_name"], order_by="full_name"
	)


@frappe.whitelist()
def intake_options():
	"""What the New Service Call dialog needs: the technicians and which rates are filled in."""
	frappe.has_permission(DOCTYPE, "create", throw=True)
	rates = {}
	try:
		for row in frappe.get_all(RATE_DOCTYPE, filters={"disabled": 0}, fields=["service_type", "rate_kind", "rate"]):
			rates[f"{row.service_type}|{row.rate_kind}"] = flt(row.rate)
	except Exception:
		rates = {}
	return {"technicians": technician_options(), "rates": rates, "service_types": list(SERVICE_TYPES), "hourly_kinds": list(HOURLY_KINDS)}


def describe_rate(call, fmt):
	"""One line for the approval page and the email: the rate, in words. ``fmt`` formats money."""
	service_type = call.get("service_type")
	if is_hourly(service_type):
		rate = flt(call.get("hourly_rate"))
		kind = call.get("rate_kind") or HOURLY_KINDS[0]
		if rate > 0:
			return f"{fmt(rate)} per hour ({kind}), plus parts and materials"
		return f"Time and materials ({kind}), plus parts and materials"
	rate = flt(call.get("flat_rate"))
	if rate > 0:
		return f"{fmt(rate)} flat, plus chemicals used"
	return "Flat rate as quoted, plus chemicals used"


def terms_for(service_type):
	return list(TERMS_HOURLY if is_hourly(service_type) else TERMS_FLAT)


def service_label(service_type):
	"""The service type as the client reads it."""
	return {
		"Startup": "Spring startup",
		"Winterization": "Winterization",
		"Maintenance Visit": "Maintenance visit",
		SMALL_REPAIR: "Small repair",
	}.get(service_type, service_type or "")


def site_label(call):
	"""What the client calls the place: the address, else the project's name, else the account."""
	try:
		if call.get("address_display"):
			return str(call.get("address_display")).split("\n")[0].strip()
		if call.get("project"):
			name = frappe.db.get_value("Project", call.project, "project_name")
			if name:
				return name
		if call.get("customer"):
			return frappe.db.get_value("Customer", call.customer, "customer_name") or call.customer
	except Exception:
		pass
	return _("your fountain")

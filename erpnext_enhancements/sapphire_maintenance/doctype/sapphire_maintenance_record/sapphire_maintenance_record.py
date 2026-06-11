"""Controller for the Sapphire Maintenance Record doctype.

A Maintenance Record is the submittable field-service "visit" document at the
heart of the Sapphire Maintenance subsystem. Its content is instantiated from
a Sapphire Maintenance Template, which is *composed* of reusable Sapphire
Maintenance Sections; each section type lands in its own typed child table:

  * Equipment Inspection -> ``maintenance_results`` (Pass/Fail/Replace/Other)
  * Water Chemistry      -> ``chemistry_readings``  (value vs target range)
  * Cleaning Tasks       -> ``cleaning_tasks``      (done / not done)
  * Chemical Dosing      -> ``consumables``         (qty consumed -> stock issue)

Visit shapes: a record normally covers one water feature (``serial_no`` set).
When the project's Sapphire Maintenance Contract is "Per Site Visit", one
record covers every covered feature — ``serial_no`` stays empty and each child
row is tagged with its feature's ``serial_no`` instead.

Lifecycle / wiring:
  * ``validate`` computes per-reading ``out_of_range`` flags and the parent
    ``has_out_of_range_readings`` (drives the "Maintenance Reading Out of
    Range" Notification fixture on submit).
  * ``on_submit`` enqueues the background worker
    ``api.maintenance_workflow.process_maintenance_submission`` (Stock Entry,
    Timesheet, Warranty Claim, Sales Invoice). The next-visit scheduling
    update runs via the ``on_submit`` doc-event registered in hooks.py
    (``api.maintenance_scheduling.update_next_visit_dates``).
  * The doctype has a workflow (``workflow_state``) and the "Maintenance
    Review Needed" / "Maintenance Finalized" Notifications — see fixtures.
  * ``route`` is "maintenance-records": exposed on the customer portal and
    rendered by ``sapphire_maintenance_record.html`` (see ``get_context``).

Whitelisted helpers used by the desk form's JS: ``get_visit_payload``
(instantiate the form's child tables from the resolved template) and
``get_dashboard_context`` (technician on-site briefing).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

class SapphireMaintenanceRecord(Document):
	def validate(self):
		self.has_out_of_range_readings = 1 if evaluate_reading_ranges(self.chemistry_readings) else 0
		self.completion_percent = compute_completion_percent(self)
		self._autofill_clock_in()

	def before_submit(self):
		self._autofill_clock_out()
		self._validate_mandatory_rows()

	def _validate_mandatory_rows(self):
		"""Block submit while template-mandated rows are unanswered.

		Rows carry ``is_mandatory`` copied from their template section item
		(``get_visit_payload``). "Answered" matches compute_completion_percent:
		a selection/answer, a non-zero reading, a task ticked done or
		annotated. Consumables are exempt — an untouched qty-0 dosing prefill
		is a legitimate "none used".
		"""
		def tag(row, label):
			if row.serial_no and not self.serial_no:
				return _("{0} ({1})").format(label, row.serial_no)
			return label

		missing = []
		for row in self.get("maintenance_results", []):
			if row.is_mandatory and not (row.selection or row.answer):
				missing.append(tag(row, row.question))
		for row in self.get("chemistry_readings", []):
			if row.is_mandatory and not flt(row.reading_value):
				missing.append(tag(row, row.reading))
		for row in self.get("cleaning_tasks", []):
			if row.is_mandatory and not (row.is_done or row.notes):
				missing.append(tag(row, row.task))
		if missing:
			frappe.throw(
				_("These required items are still unanswered:")
				+ "<br>"
				+ "<br>".join(frappe.utils.escape_html(item) for item in missing),
				title=_("Required Items Missing"),
			)

	def _job_interval(self, statuses):
		"""Latest Job Interval for this record's technician + project in the given statuses."""
		if not self.technician or not self.project:
			return None
		employee = frappe.db.get_value("Employee", {"user_id": self.technician}, "name")
		if not employee:
			return None
		return frappe.db.get_value(
			"Job Interval",
			{"employee": employee, "project": self.project, "status": ["in", statuses]},
			["name", "start_time", "end_time", "total_paused_seconds"],
			order_by="start_time desc",
			as_dict=True,
		)

	def _autofill_clock_in(self):
		"""Seed clock-in from the technician's running kiosk interval on this project.

		The kiosk (Job Interval) already knows when work started — don't make
		the tech enter it twice. Only fills a blank field; manual entries win.
		"""
		if self.clock_in_time or self.docstatus != 0:
			return
		interval = self._job_interval(["Open", "Paused"])
		if interval:
			self.clock_in_time = interval.start_time

	def _autofill_clock_out(self):
		"""On submit, close out blank clock fields from the kiosk interval.

		A still-running interval means the tech is submitting before clocking
		out — stamp "now" and carry the interval's pause time. A recently
		completed interval supplies its real end time.
		"""
		if self.clock_in_time and not self.clock_out_time:
			interval = self._job_interval(["Open", "Paused"])
			if interval:
				self.clock_out_time = frappe.utils.now_datetime()
				if not self.paused_duration:
					self.paused_duration = interval.total_paused_seconds or 0
			else:
				interval = self._job_interval(["Completed"])
				if interval and interval.end_time and str(interval.end_time) >= str(self.clock_in_time):
					self.clock_out_time = interval.end_time
					if not self.paused_duration:
						self.paused_duration = interval.total_paused_seconds or 0

	def on_submit(self):
		"""Submit lifecycle hook: kick off downstream automation.

		Enqueues ``api.maintenance_workflow.process_maintenance_submission`` on
		the "default" queue to generate Stock Entry, Timesheet, Warranty Claim
		and a draft Sales Invoice in the background. Next-visit dates are
		updated by the ``on_submit`` doc-event in hooks.py
		(``api.maintenance_scheduling.update_next_visit_dates``) — not called
		here, so it runs exactly once.
		"""
		frappe.enqueue(
			"erpnext_enhancements.api.maintenance_workflow.process_maintenance_submission",
			record_name=self.name,
			queue="default"
		)

	def get_context(self, context):
		"""Populate the web/portal render context for ``/maintenance-records``.

		Called by Frappe's web-view machinery when the record is rendered via the
		``sapphire_maintenance_record.html`` template. Sets ``context.show_labor``
		from the parent Maintenance Sales Order's ``custom_display_labor_hours``
		flag (controls whether the Service Duration block is shown), and adds the
		portal breadcrumb back to the "Maintenance Records" listing.

		Args:
			context: The mutable web render context (modified in place).
		"""
		context.show_labor = False
		if self.project:
			show_labor = frappe.db.get_value("Sales Order",
				{"project": self.project, "order_type": "Maintenance", "docstatus": 1},
				"custom_display_labor_hours")
			context.show_labor = True if show_labor else False

		context.parents = [{"name": _("Maintenance Records"), "route": "maintenance-records"}]


def compute_completion_percent(doc):
	"""How much of the visit form has been filled in, as 0–100.

	Counted as answered: inspection rows with a selection or answer, readings
	with a value, cleaning tasks ticked done or annotated. Consumables don't
	count — an untouched qty-0 dosing prefill is a legitimate "none used".
	Returns 0 for a record with no section rows. Pure function — no DB access.
	"""
	answered = total = 0
	for row in doc.get("maintenance_results", []):
		total += 1
		if row.get("selection") or row.get("answer"):
			answered += 1
	for row in doc.get("chemistry_readings", []):
		total += 1
		if flt(row.get("reading_value")):
			answered += 1
	for row in doc.get("cleaning_tasks", []):
		total += 1
		if row.get("is_done") or row.get("notes"):
			answered += 1
	return round(answered / total * 100, 1) if total else 0


def evaluate_reading_ranges(rows):
	"""Set ``out_of_range`` on each chemistry-reading row; return flagged rows.

	A row is evaluated only when a value was actually entered — blank/zero
	means "not measured" (Float fields can't distinguish empty from 0; a true
	zero reading should be recorded in the row's notes). Out of range = value
	below ``min_value`` or above ``max_value``, considering only bounds that
	are set (non-zero). Pure function over the row objects — no DB access.
	"""
	flagged = []
	for row in rows or []:
		row.out_of_range = 0
		value = flt(row.reading_value)
		if not value:
			continue
		low, high = flt(row.min_value), flt(row.max_value)
		if (low and value < low) or (high and value > high):
			row.out_of_range = 1
			flagged.append(row)
	return flagged


def resolve_template(project=None, customer=None, contract=None, feature_row=None, visit_label=None):
	"""Resolve which Sapphire Maintenance Template applies to a visit/feature.

	Precedence: seasonal visit row (matched by ``visit_label`` on the
	contract) -> contract feature row -> contract default -> Active template
	for the Project -> Active template for the Customer. Returns a template
	name or None.
	"""
	if visit_label and contract:
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
			iter_seasonal_visits,
		)
		for visit in iter_seasonal_visits(contract):
			if visit["label"] == visit_label and visit["template"]:
				return visit["template"]
	if feature_row is not None and feature_row.get("template"):
		return feature_row.get("template")
	if contract and contract.get("default_template"):
		return contract.default_template
	template = frappe.db.get_value(
		"Sapphire Maintenance Template", {"project": project, "status": "Active"}, "name"
	) if project else None
	if not template:
		if not customer and project:
			customer = frappe.db.get_value("Project", project, "customer")
		if customer:
			template = frappe.db.get_value(
				"Sapphire Maintenance Template", {"customer": customer, "status": "Active"}, "name"
			)
	return template


def _reading_overrides(serial_no):
	"""Per-feature chemistry range overrides, keyed by normalised reading label."""
	if not serial_no:
		return {}
	rows = frappe.get_all(
		"Sapphire Reading Range Override",
		filters={"parent": serial_no, "parenttype": "Serial No"},
		fields=["reading", "min_value", "max_value"],
	)
	return {(row.reading or "").strip().lower(): row for row in rows}


@frappe.whitelist()
def get_visit_payload(project=None, serial_no=None, maintenance_contract=None, technician=None, visit_label=None):
	"""Instantiate the visit form's child-table rows from the resolved template.

	Whitelisted; called by the desk form JS (``populate_from_template``).
	Resolves the covered features (one, or all of a Per Site Visit contract's),
	resolves each feature's template, and walks the template's sections in
	order, emitting rows typed by section:

	Every row carries ``section``, ``section_title`` and ``serial_no``; the
	template section item's ``is_mandatory`` rides along on results, readings
	and tasks (enforced in ``before_submit``), its custom ``options`` on
	results, and its ``default_qty``/``qty_step`` plus the Item's
	name/stock-UOM on consumables — one enrichment point serving the desk
	form, the visit wizard, print and portal renders alike.

	Returns:
		dict: {
			"template":    the resolved template name (of the first feature),
			"results":     Equipment Inspection rows {question, options,
			               is_mandatory, section, section_title, serial_no},
			"readings":    Water Chemistry rows {reading, uom, min_value,
			               max_value, is_mandatory, section, section_title,
			               serial_no} (Serial No range overrides applied),
			"tasks":       Cleaning Tasks rows {task, is_mandatory, section,
			               section_title, serial_no},
			"consumables": Chemical Dosing rows {item, item_name, uom, qty: 0,
			               default_qty, qty_step, warehouse, section,
			               section_title, serial_no} (warehouse = feature's
			               chemical warehouse -> technician's vehicle ->
			               settings default),
		}
	"""
	from erpnext_enhancements.api.maintenance_workflow import resolve_consumable_warehouse

	contract = None
	if maintenance_contract:
		contract = frappe.get_doc("Sapphire Maintenance Contract", maintenance_contract)
		project = project or contract.project

	# Which water features does this visit cover?
	features = []
	if contract and contract.visit_shape == "Per Site Visit" and not serial_no:
		features = [
			{"serial_no": row.serial_no, "template": row.template, "default_warehouse": row.default_warehouse}
			for row in contract.covered_features
		]
	else:
		feature = {"serial_no": serial_no, "template": None, "default_warehouse": None}
		if contract and serial_no:
			for row in contract.covered_features:
				if row.serial_no == serial_no:
					feature.update({"template": row.template, "default_warehouse": row.default_warehouse})
					break
		features = [feature]

	customer = contract.customer if contract else None
	payload = {"template": None, "results": [], "readings": [], "tasks": [], "consumables": []}
	template_cache = {}
	item_cache = {}

	def item_details(item_code):
		if item_code not in item_cache:
			item_cache[item_code] = frappe.db.get_value(
				"Item", item_code, ["item_name", "stock_uom"], as_dict=True
			) or frappe._dict()
		return item_cache[item_code]

	for feature in features:
		template_name = resolve_template(
			project=project,
			customer=customer,
			contract=contract,
			feature_row=feature,
			visit_label=visit_label,
		)
		if not template_name:
			continue
		payload["template"] = payload["template"] or template_name

		if template_name not in template_cache:
			template = frappe.get_doc("Sapphire Maintenance Template", template_name)
			template_cache[template_name] = [
				frappe.get_doc("Sapphire Maintenance Section", row.section)
				for row in template.sections
			]
		sections = template_cache[template_name]

		feature_serial = feature["serial_no"]
		overrides = _reading_overrides(feature_serial)
		warehouse = resolve_consumable_warehouse(
			feature_warehouse=feature.get("default_warehouse"),
			technician=technician,
		)

		for section in sections:
			if section.disabled:
				continue
			items = sorted(section.items, key=lambda row: (row.sequence or row.idx))
			common = {
				"section": section.name,
				"section_title": section.section_title,
				"serial_no": feature_serial,
			}
			if section.section_type == "Equipment Inspection":
				for item in items:
					payload["results"].append(
						dict(
							common,
							question=item.label,
							options=item.options,
							is_mandatory=item.is_mandatory,
						)
					)
			elif section.section_type == "Water Chemistry":
				for item in items:
					override = overrides.get((item.label or "").strip().lower())
					payload["readings"].append(
						dict(
							common,
							reading=item.label,
							uom=item.uom,
							min_value=override.min_value if override else item.min_value,
							max_value=override.max_value if override else item.max_value,
							is_mandatory=item.is_mandatory,
						)
					)
			elif section.section_type == "Cleaning Tasks":
				for item in items:
					payload["tasks"].append(
						dict(common, task=item.label, is_mandatory=item.is_mandatory)
					)
			elif section.section_type == "Chemical Dosing":
				for item in items:
					details = item_details(item.item)
					payload["consumables"].append(
						dict(
							common,
							item=item.item,
							item_name=details.item_name,
							uom=details.stock_uom,
							qty=0,
							default_qty=item.default_qty,
							qty_step=item.qty_step or 1,
							warehouse=warehouse,
						)
					)

	return payload


@frappe.whitelist()
def get_historical_visits(project, exclude=None):
	"""Last 5 submitted maintenance visits for a Project.

	Backs the read-only ``historical_visits`` HTML field on the desk form
	(rendered client-side by ``render_historical_visits`` in the form JS). This
	replaces the former virtual ``historical_visits`` child table — an
	``is_virtual`` doctype whose ``cached_property`` rows didn't shadow Frappe's
	child-table loader and crashed permission checks (``has_user_permission`` ->
	``get_all_children``) on a non-existent table.

	Uses ``get_list`` so the result respects the caller's read permissions and
	User Permissions on the Maintenance Record. Excludes the current record;
	returns ``[]`` when no Project is set.

	Args:
		project (str): Project name (docname).
		exclude (str, optional): Record name to omit (the open document).

	Returns:
		list[dict]: ``[{name, creation, technician}]`` newest-first, max 5.
	"""
	if not project:
		return []
	return frappe.get_list(
		"Sapphire Maintenance Record",
		filters={"project": project, "name": ["!=", exclude or ""], "docstatus": 1},
		fields=["name", "creation", "technician"],
		order_by="creation desc",
		limit=5,
	)


@frappe.whitelist()
def get_dashboard_context(project, serial_no=None):
	"""Return the on-site briefing data for the technician dashboard widget.

	Whitelisted; called by the desk form JS (``render_dashboard``) to build the
	in-form HTML panel shown before the safety gate is acknowledged.

	Args:
		project (str): Project name (docname).
		serial_no (str, optional): Serial No name. Omitted for Per Site Visit
			records, which have no header serial.

	Returns:
		dict: {
			"profile": Sapphire Maintenance Profile safety_instructions/access_codes,
			"serial_no": Serial No custom_site_instructions/item_name (or {}),
			"visits": last 3 submitted Sapphire Maintenance Records for the project,
			"contract": Active Maintenance Contract context — gate code, key
				location, preferred days/time from the linked Project Contract,
			"service_scope": the Project's Service stream Customer Requests and
				Deliverables (free-text rows from the contract scope model),
		}
	"""
	context = {}

	# 1. Profile Data
	profile = frappe.db.get_value(
		"Sapphire Maintenance Profile",
		{"project": project},
		["safety_instructions", "access_codes", "wrapup_instructions"],
		as_dict=True,
	)
	context['profile'] = profile or {}

	# 2. Serial No Data
	serial_no_data = frappe.db.get_value("Serial No", serial_no, ["custom_site_instructions", "item_name"], as_dict=True) if serial_no else None
	context['serial_no'] = serial_no_data or {}

	# 3. Last 3 Visits
	visits = frappe.get_all(
		"Sapphire Maintenance Record",
		filters={"project": project, "docstatus": 1},
		fields=["name", "creation", "technician"],
		order_by="creation desc",
		limit=3
	)
	context['visits'] = visits

	# 4. Contract-driven context: site access + cadence from the signed
	# Maintenance Services Agreement, via the Active operational contract.
	context['contract'] = {}
	contract = frappe.db.get_value(
		"Sapphire Maintenance Contract",
		{"project": project, "status": "Active"},
		["name", "project_contract", "visit_shape"],
		as_dict=True,
	)
	if contract:
		context['contract'] = {"name": contract.name, "visit_shape": contract.visit_shape}
		if contract.project_contract:
			legal = frappe.db.get_value(
				"Project Contract",
				contract.project_contract,
				["gate_code", "key_location", "preferred_days", "preferred_time"],
				as_dict=True,
			)
			if legal:
				context['contract'].update(legal)

	# 5. Service-stream scope from the Project (customer's words + our deliverables).
	context['service_scope'] = {
		"requests": frappe.get_all(
			"Service Customer Requests",
			filters={"parent": project, "parenttype": "Project"},
			pluck="service_customer_requests",
			order_by="idx",
		),
		"deliverables": frappe.get_all(
			"Service Deliverables",
			filters={"parent": project, "parenttype": "Project"},
			pluck="service_deliverables",
			order_by="idx",
		),
	} if project else {"requests": [], "deliverables": []}

	# 6. Chemistry trends — last 5 visits' readings, for the dashboard sparklines.
	context['trends'] = _chemistry_trends(project, serial_no)

	return context


def _chemistry_trends(project, serial_no=None):
	"""Reading history for the dashboard sparklines.

	Last 5 submitted visits for the Project (filtered to the water feature when
	``serial_no`` is given — matching either the record header or the tagged
	row). Returns [{reading, uom, min_value, max_value, points: [{date, value,
	out_of_range}]}] with points oldest-first, readings in the current form's
	order of appearance.
	"""
	if not project:
		return []

	filters = {"project": project, "docstatus": 1}
	if serial_no:
		filters["serial_no"] = ["in", [serial_no, ""]]
	records = frappe.get_all(
		"Sapphire Maintenance Record",
		filters=filters,
		fields=["name", "creation"],
		order_by="creation desc",
		limit=5,
	)
	if not records:
		return []

	created = {r.name: r.creation for r in records}
	row_filters = {"parent": ["in", list(created)], "parenttype": "Sapphire Maintenance Record"}
	if serial_no:
		row_filters["serial_no"] = ["in", [serial_no, ""]]
	rows = frappe.get_all(
		"Sapphire Chemistry Reading",
		filters=row_filters,
		fields=["parent", "reading", "reading_value", "uom", "min_value", "max_value", "out_of_range"],
	)

	trends = {}
	for row in sorted(rows, key=lambda r: str(created[r.parent])):
		if not flt(row.reading_value):
			continue  # blank/zero = not measured (see evaluate_reading_ranges)
		trend = trends.setdefault(row.reading, {
			"reading": row.reading,
			"uom": row.uom,
			"min_value": row.min_value,
			"max_value": row.max_value,
			"points": [],
		})
		trend["points"].append({
			"date": str(created[row.parent].date()),
			"value": row.reading_value,
			"out_of_range": row.out_of_range,
		})
	return list(trends.values())

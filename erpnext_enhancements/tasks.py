"""Recurring-task generation and predictive maintenance scheduling.

Two unrelated scheduling features live here, both registered in hooks.py:

* :func:`generate_next_task` — Task ``on_update`` doc_event. When a recurring
  Task is completed, spawns the next occurrence per its recurrence rules.
* :func:`predictive_maintenance_scheduling` — ``daily`` scheduler_event. Wraps
  :func:`generate_predictive_maintenance_records`, which drafts upcoming Sapphire
  Maintenance Records from Active Sapphire Maintenance Contracts (falling back
  to maintenance Sales Orders for projects without one).
"""
import frappe
from frappe.utils import add_days, add_months, add_years, get_weekday, getdate


def generate_next_task(doc, method):
	"""
	Triggered on Task update (``on_update`` doc_event in hooks.py).
	Calculates the next valid date based on complex recurrence rules
	(e.g., Weekly on M-F, Bi-weekly, etc.) and generates a new Task.

	Guards against re-firing: only acts when the Task is Completed, flagged
	``custom_is_recurring``, and has not already stamped ``custom_next_task_created``.
	"""
	# 1. Validation: Only run if completed, recurring, and not already processed
	if doc.status != "Completed" or not doc.get("custom_is_recurring"):
		return

	if doc.get("custom_next_task_created"):
		return

	# 2. Get Configuration
	frequency = doc.get("custom_frequency")
	repeat_every = doc.get("custom_repeat_every") or 1

	# Base calculation on the PLANNED End Date to prevent schedule drift.
	# Fallback to Start Date if End Date is missing.
	last_date = getdate(doc.exp_end_date or doc.exp_start_date)

	next_start_date = None

	# 3. Calculate Next Date
	if frequency == "Daily":
		next_start_date = add_days(last_date, repeat_every)

	elif frequency == "Monthly":
		next_start_date = add_months(last_date, repeat_every)

	elif frequency == "Yearly":
		next_start_date = add_years(last_date, repeat_every)

	elif frequency == "Weekly":
		# The complex logic for "M-F" or "Bi-weekly"
		next_start_date = get_next_weekly_date(doc, last_date, repeat_every)

	# 4. Create the Task
	if next_start_date:
		create_duplicate_task(doc, next_start_date)


def get_next_weekly_date(doc, last_date, repeat_every):
	"""
	Finds the next valid day of the week using direct arithmetic.
	If the next valid day is in a future week, apply the 'repeat_every' gap.
	"""
	day_map = {
		"Monday": "custom_monday",
		"Tuesday": "custom_tuesday",
		"Wednesday": "custom_wednesday",
		"Thursday": "custom_thursday",
		"Friday": "custom_friday",
		"Saturday": "custom_saturday",
		"Sunday": "custom_sunday",
	}

	# 0 = Monday, 6 = Sunday
	weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

	current_weekday_idx = last_date.weekday() # 0-6

	# 1. Check remaining days in current week
	for i in range(current_weekday_idx + 1, 7):
		day_name = weekdays[i]
		field_name = day_map.get(day_name)
		if doc.get(field_name):
			return add_days(last_date, i - current_weekday_idx)

	# 2. Check days in "Start of Next Week" to find the first valid one
	# If we didn't find one in the remainder, we wrap around to the next week cycle.

	first_valid_idx = -1
	for i in range(0, 7):
		day_name = weekdays[i]
		field_name = day_map.get(day_name)
		if doc.get(field_name):
			first_valid_idx = i
			break

	if first_valid_idx != -1:
		# Days to get to the *next* occurrence of this day
		# Logic: (Days left in current week) + (Days into next week)
		# = (7 - current_weekday_idx) + first_valid_idx
		days_until_next_week_day = (7 - current_weekday_idx) + first_valid_idx

		# Base target date (Start of next cycle - i.e., first valid day of Next Week)
		target_date = add_days(last_date, days_until_next_week_day)

		# Apply Repeat Every Gap
		# if repeat_every = 1 (Weekly), gap=0.
		# if repeat_every = 2 (Bi-Weekly), gap=7 (Skip one full week).
		gap_days = (repeat_every - 1) * 7
		return add_days(target_date, gap_days)

	return None


def create_duplicate_task(doc, new_date):
	"""Clone a completed recurring Task as a fresh Open task on ``new_date``.

	Resets status/docstatus and the recurrence bookkeeping field, inserts the
	copy, then back-links the original via ``custom_next_task_created`` (which
	also marks the original as already-processed) and notifies the user.
	"""
	new_doc = frappe.copy_doc(doc)
	new_doc.status = "Open"
	new_doc.exp_start_date = new_date
	new_doc.exp_end_date = new_date
	new_doc.custom_next_task_created = None
	new_doc.docstatus = 0

	# Reset specific operational fields (Example)
	# new_doc.custom_chemicals_used = None

	new_doc.insert(ignore_permissions=True)

	# Link back to original
	frappe.db.set_value("Task", doc.name, "custom_next_task_created", new_doc.name)
	frappe.msgprint(
		f"Scheduled next task for {new_date}: <a href='/app/task/{new_doc.name}'>{new_doc.name}</a>"
	)


def generate_predictive_maintenance_records():
	"""
	Cron job to automatically generate Draft Sapphire Maintenance Records.

	Contract-driven: Active Sapphire Maintenance Contracts are the scheduling
	source of truth. Per contract:

	* **Per Feature** shape — one draft per covered feature whose
	  ``next_visit_date`` is within 7 days (deduped on project + serial).
	* **Per Site Visit** shape — a single draft covering the whole site when
	  any feature is due (deduped on the contract link).
	* **Seasonal visits** — one draft per seasonal row when its target month
	  arrives, at most once per year (``last_generated_year`` stamp).

	Drafts are bare headers; the visit form instantiates its section tables
	from the resolved template when the technician opens it. Contracts whose
	``end_date`` has passed are marked Expired. Projects *without* an Active
	contract fall back to the legacy Sales Order Item query so pre-contract
	maintenance orders keep generating visits.
	"""
	from frappe.utils import add_days, nowdate, getdate

	today = getdate(nowdate())
	horizon = add_days(today, 7)
	month_name = today.strftime("%B")

	# 0. Expire contracts that ran out
	for name in frappe.get_all(
		"Sapphire Maintenance Contract",
		filters={"status": "Active", "end_date": ["<", today]},
		pluck="name",
	):
		frappe.db.set_value("Sapphire Maintenance Contract", name, "status", "Expired")

	# 1. Contract-driven generation
	contract_projects = set()
	contracts = frappe.get_all(
		"Sapphire Maintenance Contract", filters={"status": "Active"}, pluck="name"
	)
	for contract_name in contracts:
		contract = frappe.get_doc("Sapphire Maintenance Contract", contract_name)
		if contract.project:
			contract_projects.add(contract.project)
		if contract.start_date and getdate(contract.start_date) > today:
			continue

		due_features = [
			row for row in contract.covered_features
			if row.next_visit_date and getdate(row.next_visit_date) <= horizon
		]

		if contract.visit_shape == "Per Site Visit":
			if due_features and not frappe.db.exists(
				"Sapphire Maintenance Record",
				{"maintenance_contract": contract.name, "visit_label": ["is", "not set"], "docstatus": 0},
			):
				_draft_maintenance_record(contract)
		else:
			for row in due_features:
				if frappe.db.exists(
					"Sapphire Maintenance Record",
					{"project": contract.project, "serial_no": row.serial_no, "docstatus": 0},
				):
					continue
				_draft_maintenance_record(contract, serial_no=row.serial_no)

		# Seasonal (annual) visits: startup / winterization
		for row in contract.get("seasonal_visits", []):
			if row.target_month != month_name or (row.last_generated_year or 0) >= today.year:
				continue
			if not frappe.db.exists(
				"Sapphire Maintenance Record",
				{"maintenance_contract": contract.name, "visit_label": row.visit_label, "docstatus": 0},
			):
				_draft_maintenance_record(contract, visit_label=row.visit_label)
			frappe.db.set_value("Sapphire Seasonal Visit", row.name, "last_generated_year", today.year)

	# 2. Legacy fallback for projects without an Active contract: Sales Order
	# Items whose next predictive visit is within 7 days.
	items = frappe.db.get_all(
		"Sales Order Item",
		filters={
			"docstatus": 1,
			"custom_next_predictive_visit": ["<=", horizon],
			"custom_serial_no": ["is", "set"]
		},
		fields=["name", "parent", "custom_serial_no", "custom_next_predictive_visit"],
	)

	for item in items:
		# Check if parent is an active maintenance order
		so_status, so_project, so_customer = frappe.db.get_value(
			"Sales Order",
			item.parent,
			["status", "project", "customer"]
		)

		if so_project in contract_projects:
			continue

		if so_status not in ["Closed", "Completed"] and so_project:
			# Check for existing Draft record for this project + serial_no
			if not frappe.db.exists("Sapphire Maintenance Record", {
				"project": so_project,
				"serial_no": item.custom_serial_no,
				"docstatus": 0
			}):
				# Create the Maintenance Record
				maintenance_record = frappe.new_doc("Sapphire Maintenance Record")
				maintenance_record.customer = so_customer
				maintenance_record.project = so_project
				maintenance_record.serial_no = item.custom_serial_no
				maintenance_record.insert(ignore_permissions=True)

				frappe.logger().info(f"Generated predictive Maintenance Record for serial_no {item.custom_serial_no} in project {so_project}")


def _draft_maintenance_record(contract, serial_no=None, visit_label=None):
	"""Insert a bare draft visit record for a contract (header fields only)."""
	record = frappe.new_doc("Sapphire Maintenance Record")
	record.customer = contract.customer
	record.project = contract.project
	record.maintenance_contract = contract.name
	record.serial_no = serial_no
	record.visit_label = visit_label
	record.insert(ignore_permissions=True)
	frappe.logger().info(
		f"Generated predictive Maintenance Record for contract {contract.name}"
		f" ({serial_no or visit_label or 'site visit'})"
	)
	return record


def predictive_maintenance_scheduling():
	"""
	Wrapper for the daily scheduler event (``scheduler_events["daily"]`` in
	hooks.py). Delegates to :func:`generate_predictive_maintenance_records`.
	"""
	generate_predictive_maintenance_records()

import frappe
from frappe.utils import add_days, add_months, add_years, get_weekday, getdate


def generate_next_task(doc, method):
	"""
	Triggered on Task update.
	Calculates the next valid date based on complex recurrence rules
	(e.g., Weekly on M-F, Bi-weekly, etc.) and generates a new Task.
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

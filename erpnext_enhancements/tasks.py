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
	Finds the next valid day of the week.
	If the next valid day is in a future week, apply the 'repeat_every' gap.
	"""
	# Map weekdays to your specific field names
	day_map = {
		"Monday": "custom_monday",
		"Tuesday": "custom_tuesday",
		"Wednesday": "custom_wednesday",
		"Thursday": "custom_thursday",
		"Friday": "custom_friday",
		"Saturday": "custom_saturday",
		"Sunday": "custom_sunday",
	}

	# Safety valve: Look ahead max 1 year (366 days)
	for i in range(1, 366):
		candidate_date = add_days(last_date, i)
		weekday_name = get_weekday(candidate_date)

		# 1. Check if this day is allowed (Checked in the form)
		field_name = day_map.get(weekday_name)
		if doc.get(field_name):
			# 2. Check Week Intervals
			last_week_iso = last_date.isocalendar()[1]
			curr_week_iso = candidate_date.isocalendar()[1]

			# Handle year rollovers for week numbers roughly
			if candidate_date.year > last_date.year:
				curr_week_iso += 52

			week_diff = curr_week_iso - last_week_iso

			if week_diff == 0:
				# Same week, so it's the next day in the sequence
				return candidate_date
			elif week_diff >= 1:
				# We moved to a new week.
				# If Repeat Every > 1, ensure we skip the correct number of weeks.
				weeks_to_add = repeat_every - 1
				final_date = add_days(candidate_date, weeks_to_add * 7)
				return final_date

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

"""One-time migration patch (post_model_sync; listed in patches.txt).

Transcribes the payroll provider's employee numbers onto ``Employee.employee_number``.

The Shaw & Nielsen workbook keys every row on **their** employee number (22, 7,
17, …), which is not the ERPNext employee id (``HR-EMP-000xx``) and not derivable
from anything on this site — ``Employee.employee_number`` was null for all 15
active employees. Without the mapping, ``workforce/payroll_export.py`` produces a
file with a blank first column, which the provider cannot import.

**This is a transcription, not an inference.** Every pair below is read directly
off the submitted file for the 2026-07-16 to 2026-07-31 period, which lists these
exact people with these exact numbers. The same source supplies the salaried /
hourly classification and the three healthcare stipends. Nothing here is guessed
from a name, a title, or a pay rate.

Matching is nonetheless the risky step, so it is deliberately strict:

* exact, case-insensitive match on ``first_name`` + ``last_name``;
* **ambiguity is a refusal** — two employees matching one entry means neither is
  touched, and the collision is logged. A payroll number on the wrong person is
  worse than a missing one, because a missing one is visible in the report and a
  wrong one is not;
* an employee who already has an ``employee_number`` is never overwritten,
  including on a re-run.

Idempotent: every write is conditional on the target field being empty.

Anyone not in this list (a hire after 2026-07-31, for instance) keeps a blank
number and is flagged by the Payroll Hours Export report until somebody sets it.
"""

import frappe

#: (last_name, first_name) -> provider record, from the submitted workbook.
#: `stipend` is the flat per-period Healthcare Stipend amount (HlthS column).
PROVIDER_RECORDS = {
	("bailey", "parker"): {"number": "22", "classification": "Hourly", "stipend": 0},
	("blass", "daniel"): {"number": "7", "classification": "Salaried", "stipend": 0},
	("bradshaw", "nikolas"): {"number": "17", "classification": "Hourly", "stipend": 125},
	("cox", "nathan"): {"number": "27", "classification": "Hourly", "stipend": 0},
	("griffin", "jesse"): {"number": "21", "classification": "Hourly", "stipend": 0},
	("hansen", "richard"): {"number": "14", "classification": "Hourly", "stipend": 0},
	("harris", "james"): {"number": "3", "classification": "Salaried", "stipend": 0},
	("healey", "austin"): {"number": "15", "classification": "Hourly", "stipend": 0},
	("jentz da silva", "lian"): {"number": "29", "classification": "Hourly", "stipend": 0},
	("jessop", "korben"): {"number": "30", "classification": "Hourly", "stipend": 0},
	("keyes", "shellyce"): {"number": "20", "classification": "Hourly", "stipend": 0},
	("mabey", "clegg"): {"number": "25", "classification": "Salaried", "stipend": 0},
	("morisseau", "brian"): {"number": "19", "classification": "Salaried", "stipend": 125},
	("penrod", "logan"): {"number": "31", "classification": "Hourly", "stipend": 0},
	("rosser", "daniel"): {"number": "28", "classification": "Hourly", "stipend": 0},
	("symanski", "lisa"): {"number": "24", "classification": "Hourly", "stipend": 125},
}


def execute():
	if not frappe.db.table_exists("Employee"):
		return

	employees = frappe.get_all(
		"Employee",
		fields=["name", "first_name", "last_name", "employee_name", "employee_number"],
		limit=1000,
	)

	index = {}
	for employee in employees:
		key = _key(employee)
		if not key:
			continue
		index.setdefault(key, []).append(employee)

	unmatched = []
	for key, record in PROVIDER_RECORDS.items():
		candidates = index.get(key) or []

		if not candidates:
			unmatched.append(f"{key[1].title()} {key[0].title()} (#{record['number']})")
			continue

		if len(candidates) > 1:
			# Refuse rather than pick. A payroll number on the wrong person is
			# invisible; a missing one is flagged in the export report.
			frappe.log_error(
				"Payroll seed: {0} Employee records match {1} {2} — refusing to set number #{3}. "
				"Set it by hand on the correct record.".format(
					len(candidates), key[1].title(), key[0].title(), record["number"]
				),
				"Payroll Employee Number Seed",
			)
			continue

		_apply(candidates[0], record)

	if unmatched:
		frappe.log_error(
			"Payroll seed: no Employee record matched these names from the provider's file: "
			+ ", ".join(unmatched)
			+ ". They will export with a blank Emp Num until somebody sets it.",
			"Payroll Employee Number Seed",
		)


def _key(employee):
	last = (employee.get("last_name") or "").strip().lower()
	first = (employee.get("first_name") or "").strip().lower()
	if last and first:
		return (last, first)

	# Fall back to splitting employee_name for records created without the split
	# name fields. First token is the given name on this site.
	full = (employee.get("employee_name") or "").strip().lower()
	if " " in full:
		first, _sep, last = full.partition(" ")
		return (last.strip(), first.strip())
	return None


def _apply(employee, record):
	"""Fill blanks on one Employee. Never overwrites an existing value."""
	updates = {}

	if not employee.get("employee_number"):
		updates["employee_number"] = record["number"]

	if frappe.db.has_column("Employee", "custom_payroll_classification"):
		current = frappe.db.get_value("Employee", employee.name, "custom_payroll_classification")
		# "Hourly" is the field default, so a stored "Hourly" is indistinguishable
		# from "never set". Only Salaried is worth writing — and only Salaried is
		# wrong to leave at the default.
		if record["classification"] == "Salaried" and current != "Salaried":
			updates["custom_payroll_classification"] = "Salaried"

	if record["stipend"] and frappe.db.has_column("Employee", "custom_healthcare_stipend"):
		if not frappe.db.get_value("Employee", employee.name, "custom_healthcare_stipend"):
			updates["custom_healthcare_stipend"] = record["stipend"]

	if updates:
		# db.set_value, not save(): Employee carries hooks (Drive provisioning,
		# training role grants) that have no business firing for a payroll number.
		frappe.db.set_value("Employee", employee.name, updates, update_modified=False)

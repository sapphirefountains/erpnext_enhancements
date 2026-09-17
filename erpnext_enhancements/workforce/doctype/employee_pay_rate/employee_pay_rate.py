# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Employee Pay Rate — one effective-dated pay rate on an Employee (child table).

Rows live in the permlevel-1 ``custom_pay_rates`` table on Employee (fixture
``custom_field.json``), so only HR Manager, Accounts Manager and System Manager
can see them; the rest of the site sees neither the field nor the table.

The rule that resolves "the rate in force on a day" is *the row with the latest
``effective_from`` on or before that day* — ``workforce/costing.rate_for``. A
Salaried row is costed at ``annual_salary / 2080`` (``hourly_equivalent``, computed
in the parent's validate). Blank ``burden_pct`` falls back to
``Time Kiosk Settings.default_burden_pct``.

WI-016 chose costing-rate-only for labour costing (Activity Cost per employee,
no pay data in ERPNext). Nik reversed that on 2026-09-17: pay lives here, behind
permlevel 1, and Activity Cost is *derived* from it by ``costing.sync_activity_costs``
so ERPNext's own Timesheet costing keeps working unchanged.

No controller logic — validation runs on the parent
(``costing.validate_employee_pay_rates``, wired as the Employee ``validate``
doc_event) because a child row cannot see its siblings.
"""

from frappe.model.document import Document


class EmployeePayRate(Document):
	pass

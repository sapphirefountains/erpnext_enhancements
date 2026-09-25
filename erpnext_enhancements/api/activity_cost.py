"""Activity Cost rates over HTTP, with the cost rate kept to the pay audience (v1.538.0).

ERPNext's whitelisted ``erpnext.projects.doctype.timesheet.timesheet.get_activity_cost``
answers any logged-in caller with an employee's Activity Cost ``costing_rate`` and
``billing_rate``, and checks no permission of any kind on the way. Since v1.480.0 that
``costing_rate`` is not a number somebody typed in: it is the employee's burdened pay rate,
written by ``workforce/costing.py`` (ADR 0013). ADR 0013 grants read of pay to System Manager,
HR Manager and Accounts Manager "and to nobody else", so this route has to keep to the same
audience as every other place the rate lives.

**Why an override and not only a permlevel.** ``Activity Cost.costing_rate`` is at permlevel 1
as of this release (a Property Setter fixture), and that governs the places frappe reads a
document out: form loads, list views and ``GET /api/resource``. (Frappe does not apply it to the
document a write call sends back; ``fieldlevel_read.py`` scrubs those.) A whitelisted function
reading through ``frappe.db.get_values`` never consults it. ``override_whitelisted_methods`` is
the only lever on the HTTP route: frappe applies it in ``handler.execute_cmd`` and in the
``api/v2`` RPC handler, and nowhere else.
Four things follow from that, each deliberate:

* **The server-side caller is unaffected.** ``TimesheetDetail.update_cost`` imports ERPNext's
  function directly and costs every Timesheet line from it inside ``Timesheet.validate``. That
  path never passes through this override and still sees the real rate, so saved Timesheets
  cost exactly as before, whoever saves them.
* **The value is zeroed, not removed.** The Timesheet form's two prefills read
  ``r.message["costing_rate"]`` and hand it to ``calculate_billing_costing_amount``, and a
  missing key would put ``undefined`` into that arithmetic. A 0 is also what ``update_cost``
  treats as "not set", so the save fills the true rate in on the server for a user who never
  saw it.
* **``billing_rate`` is never touched.** It is what a customer is charged, not what a person is
  paid, and ADR 0013 keeps T&M billing configurable natively.
* **Role first, then the Employee.** A caller outside the audience gets 0 for every employee.
  A caller inside it must still be able to read the named Employee, so a User Permission that
  limits an Accounts Manager to some employees holds on this route too.
"""

import frappe

#: ADR 0013's pay audience: the roles that read pay at permlevel 1, and nobody else.
#: ``tests/test_pay_rate_permissions.py`` holds this equal to Job Interval's permlevel-1 roles
#: and to ``workforce/payroll_export._may_export``.
PAY_AUDIENCE_ROLES = ("System Manager", "HR Manager", "Accounts Manager")


@frappe.whitelist()
def get_activity_cost(employee=None, activity_type=None, currency=None):
	"""ERPNext's ``get_activity_cost``, with ``costing_rate`` 0 for a caller outside the pay audience.

	Same signature and same shape as the function it overrides, so the Timesheet form cannot
	tell the difference except by the number.
	"""
	from erpnext.projects.doctype.timesheet.timesheet import (
		get_activity_cost as erpnext_get_activity_cost,
	)

	rate = erpnext_get_activity_cost(employee=employee, activity_type=activity_type, currency=currency)
	if rate and not _may_read_costing_rate(employee):
		return dict(rate, costing_rate=0)
	return rate


def _may_read_costing_rate(employee):
	"""True only for the pay audience, and then only for an Employee the caller can read.

	With no employee, ERPNext falls back to the Activity Type's company-wide default rate,
	which is about no one in particular; the role check alone decides it.
	"""
	if not set(PAY_AUDIENCE_ROLES).intersection(frappe.get_roles()):
		return False
	if not employee:
		return True
	try:
		return bool(frappe.has_permission("Employee", "read", doc=employee))
	except Exception:
		return False
